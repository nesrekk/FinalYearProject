"""
fetch_draft_history.py
========================
Draft Value Analysis, step 1: pulls the NBA's full draft history (every
pick, every year back to 1947) via nba_api's DraftHistory endpoint — one
single request, not a per-season loop like shots/defense-tracking needed,
since this endpoint returns the whole history at once when no season
filter is passed.

Column names verified directly from the installed nba_api library's source
(nba_api/stats/endpoints/drafthistory.py's `expected_data`):
    PERSON_ID        -- this IS player_id, matches player_season_stats
    PLAYER_NAME, SEASON (the draft year, e.g. 2015 for the 2015 draft),
    ROUND_NUMBER, ROUND_PICK, OVERALL_PICK, DRAFT_TYPE ('Draft' vs
    'Undrafted' — undrafted entries have no pick number and are kept in
    the table but excluded from pick-based analysis by the API layer),
    TEAM_ABBREVIATION, ORGANIZATION (college/team), ORGANIZATION_TYPE.

Season-convention note: the draft happens in the summer BEFORE a player's
rookie season. This project's season_int convention is "the year the
season ends" (e.g. "2009-10" -> 2010), so the 2015 draft's rookie class
played their first season in 2015-16 -> rookie_season_int = draft_year + 1.
Stored directly as a column so joins against player_season_stats don't
need this off-by-one recomputed everywhere.

IMPORTANT: like every other live nba_api pull this session, this could NOT
be tested end-to-end from the dev sandbox this script was written in
(stats.nba.com is unreachable from there — confirmed repeatedly). The
column names and table schema were verified and the API/frontend
integration was tested against temporary synthetic rows (inserted and then
removed) to confirm the plumbing works — the actual live HTTP round-trip
needs to be confirmed by running this for real, on a machine with normal
internet access.

Usage:
    python fetch_draft_history.py
"""

from __future__ import annotations

import random
import time

import psycopg2
import psycopg2.extras
import requests
from nba_api.stats.endpoints import drafthistory

from db_config import DB_CONFIG

MAX_RETRIES = 3
BACKOFF_SLEEP_SECONDS = 30


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


def fetch_all_picks() -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = drafthistory.DraftHistory(headers=_build_headers(), timeout=45)
            frames = resp.get_data_frames()
            if not frames or frames[0].empty:
                return []
            return frames[0].to_dict("records")
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status in (403, 429) and attempt < MAX_RETRIES:
                print(f"  HTTP {status}; cooling down {BACKOFF_SLEEP_SECONDS}s...", flush=True)
                time.sleep(BACKOFF_SLEEP_SECONDS)
                continue
            raise
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES:
                print(f"  {type(e).__name__}; cooling down {BACKOFF_SLEEP_SECONDS}s...", flush=True)
                time.sleep(BACKOFF_SLEEP_SECONDS)
                continue
            raise
    return []


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS draft_history (
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            rookie_season_int INTEGER NOT NULL,
            round_number INTEGER,
            round_pick INTEGER,
            overall_pick INTEGER,
            draft_type TEXT,
            team_abbreviation TEXT,
            organization TEXT,
            organization_type TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (player_id, draft_year)
        );
    """)
    conn.commit()


def save(conn, picks: list[dict]):
    cur = conn.cursor()
    values = []
    for p in picks:
        try:
            draft_year = int(p["SEASON"])
        except (TypeError, ValueError):
            continue
        values.append((
            int(p["PERSON_ID"]), p["PLAYER_NAME"], draft_year, draft_year + 1,
            int(p["ROUND_NUMBER"]) if p.get("ROUND_NUMBER") not in (None, "") else None,
            int(p["ROUND_PICK"]) if p.get("ROUND_PICK") not in (None, "") else None,
            int(p["OVERALL_PICK"]) if p.get("OVERALL_PICK") not in (None, "") else None,
            p.get("DRAFT_TYPE"), p.get("TEAM_ABBREVIATION"),
            p.get("ORGANIZATION"), p.get("ORGANIZATION_TYPE"),
        ))

    if not values:
        print("No picks parsed — nothing to save.")
        return 0

    cur.execute("DELETE FROM draft_history;")
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO draft_history
            (player_id, player_name, draft_year, rookie_season_int, round_number,
             round_pick, overall_pick, draft_type, team_abbreviation, organization, organization_type)
        VALUES %s;
        """,
        values,
    )
    conn.commit()
    return len(values)


def main():
    print("Draft History — one-shot full pull via nba_api's DraftHistory endpoint")
    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)

    print("Fetching (this covers every draft year back to 1947 in one request)...")
    picks = fetch_all_picks()
    if not picks:
        print("No data returned.")
        conn.close()
        return

    n = save(conn, picks)
    print(f"Saved {n} draft picks (including undrafted entries) to draft_history.")
    conn.close()
    print("\nDone. Draft Value Analysis endpoints (in impact_api.py) read this "
          "table live — no separate training step needed.")


if __name__ == "__main__":
    main()
