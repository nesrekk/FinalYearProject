"""
build_defense_tracking_stats.py
================================
Defensive tracking metrics: DFG% (Defended FG%), FGDiff% (vs. league
average at that shot type), and RAD/g (Rim Attempts Defended per game).

Why this is a different category from everything else fetched in this
project: it comes from nba_api's leaguedashptdefend endpoint, which is
SportVU/Second Spectrum player-tracking data — camera-tracked contests, not
box score counting stats. Two consequences:
  1. It only exists from the 2013-14 season onward (tracking cameras were
     installed across arenas starting then) — there is no way to backfill
     DFG%/RAD for 2009-13, unlike everything else in this DB.
  2. Column names verified directly from the installed nba_api library's
     source (nba_api/stats/endpoints/leaguedashptdefend.py's
     `expected_data`), not guessed:
       CLOSE_DEF_PERSON_ID  -- this IS player_id, despite the odd name
       D_FG_PCT             -- defended FG% (what opponents shot against them)
       NORMAL_FG_PCT        -- league-average FG% for that same shot category
       PCT_PLUSMINUS         -- D_FG_PCT - NORMAL_FG_PCT (negative = holds
                                opponents below average = good defense)
       D_FGA, G              -- defended attempts, games (for RAD/g = D_FGA/G)

Unlike shot charts (one request per player), this is a LEAGUE-WIDE pull —
one request returns every player in the league for that season/category at
once. Two categories needed per season ("Overall" for DFG%/FGDiff%, "Less
Than 6Ft" for RAD/g) means ~2 requests per season, not one per player — a
much smaller footprint than shot charts, but still rate-limited the same
careful way (5s between requests, 30s backoff + retry on 403/429) since
it's still the same unofficial stats.nba.com endpoint family.

IMPORTANT: this could not be tested end-to-end from the dev sandbox this
script was written in (stats.nba.com is unreachable from there — confirmed
separately). Column names are verified against the installed library's
source, and the DB/API/frontend integration was verified with the schema
and graceful-empty-state handling, but the actual live HTTP round-trip
needs to be confirmed by running this for real, on a machine with normal
internet access.

Usage:
    cd scripts && python3 build_defense_tracking_stats.py
"""

from __future__ import annotations

import os
import random
import time

import psycopg2
import psycopg2.extras
import requests
from nba_api.stats.endpoints import leaguedashptdefend

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

# Tracking data starts 2013-14 (season code 2014). Matches this project's
# season-code convention throughout (season 2014 == "2013-14").
FIRST_TRACKING_SEASON = 2014
LAST_SEASON = 2025  # stop at 2024-25; 2025-26 likely has too few games logged

REQUEST_SLEEP_SECONDS = 5
BACKOFF_SLEEP_SECONDS = 30
MAX_RETRIES = 3


def season_code_to_label(season_code: int) -> str:
    return f"{season_code - 1}-{str(season_code)[-2:]}"


def _build_headers() -> dict:
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ]
    return {
        "User-Agent": random.choice(user_agents),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://stats.nba.com/",
        "Origin": "https://stats.nba.com",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
        "Connection": "keep-alive",
    }


def fetch_category(season_label: str, category: str) -> list[dict]:
    """One league-wide request for one season + defense category.
    Retries with backoff on 403/429/timeout, same pattern as shots_lib.py."""
    proxy = (os.getenv("NBA_API_PROXY") or "").strip() or None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = leaguedashptdefend.LeagueDashPtDefend(
                defense_category=category,
                season=season_label,
                season_type_all_star="Regular Season",
                per_mode_simple="Totals",
                headers=_build_headers(),
                proxy=proxy,
                timeout=45,
            )
            frames = resp.get_data_frames()
            if not frames or frames[0].empty:
                return []
            return frames[0].to_dict("records")
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 429) and attempt < MAX_RETRIES:
                print(f"    HTTP {status}; cooling down {BACKOFF_SLEEP_SECONDS}s...", flush=True)
                time.sleep(BACKOFF_SLEEP_SECONDS)
                continue
            raise
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES:
                print(f"    {type(e).__name__}; cooling down {BACKOFF_SLEEP_SECONDS}s...", flush=True)
                time.sleep(BACKOFF_SLEEP_SECONDS)
                continue
            raise
    return []


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS defense_tracking_stats (
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            season INTEGER NOT NULL,
            dfg_pct DOUBLE PRECISION,
            league_avg_fg_pct DOUBLE PRECISION,
            fg_diff_pct DOUBLE PRECISION,
            overall_freq DOUBLE PRECISION,
            rim_fga_total DOUBLE PRECISION,
            rim_games INTEGER,
            rad_per_game DOUBLE PRECISION,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (player_id, season)
        );
    """)
    conn.commit()


def save_season(conn, season_code, overall_rows, rim_rows):
    cur = conn.cursor()
    rim_by_id = {int(r["CLOSE_DEF_PERSON_ID"]): r for r in rim_rows}

    values = []
    for r in overall_rows:
        pid = int(r["CLOSE_DEF_PERSON_ID"])
        rim = rim_by_id.get(pid)
        rim_fga = float(rim["D_FGA"]) if rim else None
        rim_games = int(rim["G"]) if rim else None
        rad_per_game = (rim_fga / rim_games) if (rim_fga is not None and rim_games) else None
        values.append((
            pid, r["PLAYER_NAME"], season_code,
            float(r["D_FG_PCT"]) if r.get("D_FG_PCT") is not None else None,
            float(r["NORMAL_FG_PCT"]) if r.get("NORMAL_FG_PCT") is not None else None,
            float(r["PCT_PLUSMINUS"]) if r.get("PCT_PLUSMINUS") is not None else None,
            float(r["FREQ"]) if r.get("FREQ") is not None else None,
            rim_fga, rim_games, rad_per_game,
        ))

    if not values:
        return 0

    cur.execute("DELETE FROM defense_tracking_stats WHERE season = %s;", (season_code,))
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO defense_tracking_stats
            (player_id, player_name, season, dfg_pct, league_avg_fg_pct, fg_diff_pct,
             overall_freq, rim_fga_total, rim_games, rad_per_game)
        VALUES %s;
        """,
        values,
    )
    conn.commit()
    return len(values)


def main():
    print("Defense Tracking Stats (DFG%, FGDiff%, RAD/g) — league-wide pull per season")
    print(f"Seasons: {season_code_to_label(FIRST_TRACKING_SEASON)} through {season_code_to_label(LAST_SEASON)}")
    print("(Tracking data doesn't exist before 2013-14 — this is the earliest possible season.)\n")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)

    for i, season_code in enumerate(range(FIRST_TRACKING_SEASON, LAST_SEASON + 1)):
        season_label = season_code_to_label(season_code)
        print(f"[{season_label}] fetching 'Overall'...", flush=True)
        overall_rows = fetch_category(season_label, "Overall")
        time.sleep(REQUEST_SLEEP_SECONDS)

        print(f"[{season_label}] fetching 'Less Than 6Ft' (for RAD/g)...", flush=True)
        rim_rows = fetch_category(season_label, "Less Than 6Ft")

        if not overall_rows:
            print(f"[{season_label}] no data returned — skipping (season may not exist yet).")
        else:
            n = save_season(conn, season_code, overall_rows, rim_rows)
            print(f"[{season_label}] saved {n} players.")

        if season_code < LAST_SEASON:
            time.sleep(REQUEST_SLEEP_SECONDS)

    conn.close()
    print("\nDone. Results saved to defense_tracking_stats.")


if __name__ == "__main__":
    main()
