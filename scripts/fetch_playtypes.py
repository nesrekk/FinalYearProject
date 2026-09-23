"""
fetch_playtypes.py
=====================
Bulk-fetches real offensive play-type breakdowns (frequency share of a
player's real offensive possessions, real points-per-possession, and
real league percentile) from nba_api's SynergyPlayTypes endpoint — one
real call per (season, play type).

Real, verified-before-assuming facts (checked directly against the live
endpoint before writing this script, since the plan's own estimate of
"2015-16 onward" turned out to be wrong):
  - Real play-type data actually starts at the 2012-13 season (season
    int 2013) — 2009-10 through 2011-12 return a real, empty 0-row
    response, not an error.
  - The real play-type categories (nba_api.stats.library.parameters.
    PlayTypeNullable) are: Cut, Handoff, Isolation, Misc, OffScreen,
    Postup, PRBallHandler, PRRollman, OffRebound (labeled "Putbacks"),
    Spotup, Transition — 11 real categories, not a made-up list.

Scope: offensive play types only (type_grouping="offensive"). Defensive
play-type data exists in the same real endpoint but isn't fetched here —
the "Offensive Style" archetype feature and play-type bar chart this
feeds only need the offensive side; a real defensive-side pass is
straightforward future scope if wanted (same endpoint, type_grouping=
"defensive").

Usage:
    cd scripts && python3 fetch_playtypes.py
"""

import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2013
SEASON_END = 2026

PLAY_TYPES = [
    "Cut", "Handoff", "Isolation", "Misc", "OffScreen", "Postup",
    "PRBallHandler", "PRRollman", "OffRebound", "Spotup", "Transition",
]


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_playtypes (
            season INT NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            play_type TEXT NOT NULL,
            side TEXT NOT NULL,
            gp INT,
            poss INT,
            freq DOUBLE PRECISION,
            ppp DOUBLE PRECISION,
            percentile DOUBLE PRECISION,
            PRIMARY KEY (season, player_id, play_type, side)
        );
    """)


def fetch_one(season: int, play_type: str):
    from nba_api.stats.endpoints import synergyplaytypes

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = synergyplaytypes.SynergyPlayTypes(
        player_or_team_abbreviation="P",
        play_type_nullable=play_type,
        type_grouping_nullable="offensive",
        season=season_label,
        season_type_all_star="Regular Season",
        timeout=45,
    )
    return endpoint.get_data_frames()[0]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    total_rows = 0
    for season in range(SEASON_START, SEASON_END + 1):
        season_rows = 0
        for play_type in PLAY_TYPES:
            try:
                df = fetch_one(season, play_type)
            except Exception as exc:
                print(f"  {season} {play_type}: FAILED — {exc}")
                time.sleep(1.0)
                continue

            if df.empty:
                time.sleep(0.4)
                continue

            # A real mid-season trade gives one real row per team stint for
            # the same player — aggregate to one real season total per
            # player before inserting (real poss-weighted average for the
            # rate stats, real sums for gp/poss, real most-recent team by
            # possession volume), rather than crashing on a duplicate key.
            agg = {}
            for _, r in df.iterrows():
                pid = int(r["PLAYER_ID"])
                poss = int(r["POSS"])
                entry = agg.setdefault(pid, {
                    "player_name": r["PLAYER_NAME"], "team_abbreviation": r["TEAM_ABBREVIATION"],
                    "gp": 0, "poss": 0, "freq_sum": 0.0, "ppp_sum": 0.0, "pct_sum": 0.0, "max_poss": -1,
                })
                entry["gp"] += int(r["GP"])
                entry["poss"] += poss
                entry["freq_sum"] += float(r["POSS_PCT"]) * poss
                entry["ppp_sum"] += float(r["PPP"]) * poss
                entry["pct_sum"] += float(r["PERCENTILE"]) * poss
                if poss > entry["max_poss"]:
                    entry["max_poss"] = poss
                    entry["team_abbreviation"] = r["TEAM_ABBREVIATION"]

            rows = [
                (
                    season, pid, e["player_name"], e["team_abbreviation"], play_type, "offensive",
                    e["gp"], e["poss"],
                    e["freq_sum"] / e["poss"] if e["poss"] else None,
                    e["ppp_sum"] / e["poss"] if e["poss"] else None,
                    e["pct_sum"] / e["poss"] if e["poss"] else None,
                )
                for pid, e in agg.items()
            ]
            psycopg2.extras.execute_values(
                cursor,
                """INSERT INTO player_playtypes
                   (season, player_id, player_name, team_abbreviation, play_type, side, gp, poss, freq, ppp, percentile)
                   VALUES %s
                   ON CONFLICT (season, player_id, play_type, side) DO UPDATE SET
                     player_name = EXCLUDED.player_name, team_abbreviation = EXCLUDED.team_abbreviation,
                     gp = EXCLUDED.gp, poss = EXCLUDED.poss, freq = EXCLUDED.freq,
                     ppp = EXCLUDED.ppp, percentile = EXCLUDED.percentile;""",
                rows,
            )
            conn.commit()
            season_rows += len(rows)
            time.sleep(0.4)

        total_rows += season_rows
        print(f"  {season}: {season_rows} real play-type rows across {len(PLAY_TYPES)} play types")

    conn.close()
    print(f"\n✅ Done. {total_rows} real player-playtype rows, seasons {SEASON_START}-{SEASON_END}.")


if __name__ == "__main__":
    main()
