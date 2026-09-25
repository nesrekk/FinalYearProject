"""
load_pbp_shots.py
===================================
Bulk-loads player_shots from the play-by-play CSVs at
shots_data/pbp_shots_1997_2026/ (one file per season, pbp<end_year>.csv,
covering seasons 1997-2026 i.e. "1996-97" through "2025-26").

Why this exists: player_shots is normally populated lazily, one player at a
time, by a live scrape of stats.nba.com (see api/shots_lib.py) — slow,
fragile, and the source of the backend freezing up on shot-chart requests
(stats.nba.com blocking the single uvicorn worker on timeout/retry). This
script pre-populates the table in bulk from a static dataset instead, so
most players never need a live fetch at all.

Column mapping (pbp CSV -> player_shots):
  playerid          -> player_id          (already an nba_api numeric ID —
                                             no name/ID crosswalk needed)
  player full name  -> player_name        (looked up from nba_api's static
                                             player list; the CSV's own
                                             `player` column is abbreviated,
                                             e.g. "N. Vučević")
  gameid             -> game_id            (kept as zero-padded string)
  x, y                -> loc_x, loc_y
  result == 'Made'    -> shot_made_flag
  '3PT' in desc        -> shot_type ("3PT Field Goal" / "2PT Field Goal")
  dist                -> shot_distance
  period              -> period
  clock (PT12M00.00S) -> minutes_remaining, seconds_remaining
  season (end year)   -> season, converted to "YYYY-YY" to match the
                          existing table's convention (e.g. 2024 -> "2023-24")
  shot_zone_basic left NULL — api/shots_lib.classify_zone() already has a
  documented coordinate-based fallback for rows without it (same path used
  for shots fetched before that column existed), so we don't need to
  reimplement NBA's own zone assignment here.

Only actual shot events are loaded (type in Made Shot / Missed Shot) — the
CSVs are full play-by-play and are ~65% non-shot rows (rebounds, fouls,
subs, etc.) that don't belong in player_shots.

Scope/safety: for each season file, existing player_shots rows for that
exact season string are deleted before inserting (delete-then-insert,
scoped to the season being (re)loaded — matches this project's existing
ingestion convention). A player already live-cached for a season this
script also covers gets replaced by the (more complete, more consistent)
bulk data.

After loading, every player_id seen in the dataset is marked 'done' in
player_shots_cache_status, so api/shots_lib.py's live-fetch path is skipped
for them. Caveat: this dataset ends at season 2025-26 (the most recently
completed season as of writing). It does NOT include 2026-27 games once
that season starts — a previously-"done" active player won't get live
shots for games played after this data's cutoff until re-run or fetched
some other way. Fine for now (off-season); worth revisiting once the new
season is underway.

Usage:
    python load_pbp_shots.py
"""

import os
import re

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from nba_api.stats.static import players as nba_static_players

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "shots_data", "pbp_shots_1997_2026")

from db_config import DB_CONFIG

CLOCK_RE = re.compile(r"PT(\d+)M([\d.]+)S")

INSERT_COLUMNS = [
    "player_id", "player_name", "season", "game_id", "loc_x", "loc_y",
    "shot_made_flag", "shot_type", "shot_distance", "period",
    "minutes_remaining", "seconds_remaining", "shot_zone_basic",
]


def season_end_year_to_label(season_end_year: int) -> str:
    """2024 -> '2023-24' (matches the table's existing season format)."""
    return f"{season_end_year - 1}-{str(season_end_year)[-2:]}"


def parse_clock(clock: str):
    """'PT11M36.00S' -> (11, 36)."""
    if not isinstance(clock, str):
        return 0, 0
    m = CLOCK_RE.match(clock)
    if not m:
        return 0, 0
    return int(m.group(1)), int(float(m.group(2)))


def build_player_name_map():
    """nba_player_id -> full_name, from nba_api's local static player list
    (no network call) — used instead of the CSV's abbreviated `player`
    column (e.g. "N. Vučević")."""
    return {p["id"]: p["full_name"] for p in nba_static_players.get_players()}


def load_season_file(path, season_end_year, name_map):
    df = pd.read_csv(
        path,
        dtype={"gameid": str, "playerid": "Int64"},
        usecols=["gameid", "period", "clock", "playerid", "type", "result", "x", "y", "dist", "desc"],
    )
    df = df[df["type"].isin(["Made Shot", "Missed Shot"])].dropna(subset=["playerid", "x", "y"])

    season_label = season_end_year_to_label(season_end_year)
    minutes, seconds = zip(*df["clock"].map(parse_clock)) if len(df) else ([], [])

    rows = []
    for i, (_, r) in enumerate(df.iterrows()):
        player_id = int(r["playerid"])
        rows.append((
            player_id,
            name_map.get(player_id, ""),
            season_label,
            str(r["gameid"]).zfill(10),
            int(round(r["x"])),
            int(round(r["y"])),
            1 if r["result"] == "Made" else 0,
            "3PT Field Goal" if "3PT" in str(r["desc"]) else "2PT Field Goal",
            int(round(r["dist"])) if pd.notna(r["dist"]) else 0,
            int(r["period"]) if pd.notna(r["period"]) else 0,
            minutes[i],
            seconds[i],
            None,
        ))
    return rows, season_label


def save_season(cur, season_label, rows):
    cur.execute("DELETE FROM player_shots WHERE season = %s;", (season_label,))
    if rows:
        execute_values(
            cur,
            f"INSERT INTO player_shots ({', '.join(INSERT_COLUMNS)}) VALUES %s;",
            rows,
        )


def mark_players_done(cur, player_ids_with_names):
    rows = [(pid, name or f"player_{pid}", "done") for pid, name in player_ids_with_names.items()]
    execute_values(
        cur,
        """
        INSERT INTO player_shots_cache_status (player_id, player_name, status, updated_at)
        VALUES %s
        ON CONFLICT (player_id) DO UPDATE SET
            status = EXCLUDED.status,
            updated_at = NOW();
        """,
        rows,
        template="(%s, %s, %s, NOW())",
    )


if __name__ == "__main__":
    name_map = build_player_name_map()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    all_player_ids = {}
    total_rows = 0

    for season_end_year in range(1997, 2027):
        path = os.path.join(DATA_DIR, f"pbp{season_end_year}.csv")
        if not os.path.exists(path):
            print(f"season {season_end_year}: file not found, skipping")
            continue

        rows, season_label = load_season_file(path, season_end_year, name_map)
        save_season(cur, season_label, rows)
        conn.commit()

        for r in rows:
            all_player_ids[r[0]] = r[1]

        total_rows += len(rows)
        print(f"season {season_label}: loaded {len(rows)} shots")

    mark_players_done(cur, all_player_ids)
    conn.commit()
    conn.close()

    print(f"\nDone. {total_rows} total shots loaded across {len(all_player_ids)} players.")
    print("Those players are now marked 'done' in player_shots_cache_status, "
          "so the live stats.nba.com fetch path is skipped for them.")
