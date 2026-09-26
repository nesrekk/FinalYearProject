"""
fetch_cbb_games.py
===================
Every men's D1 college game (scores, venue type, CollegeBasketballData.com
Elo at tip-off, NCAA seeds) for seasons 2013-2026, stored as cbb_games.
Feeds the March Madness model (build_ncaa_model.py).

One /games call per calendar month per season (Nov 1 to April 15), 84
calls in total, well inside the free tier's quota. A call returning 3,000
rows (the API's page cap) is split in half and re-fetched rather than
silently truncated. 2020 is fetched too (no tournament that year, but the
regular season is real), so the table is a complete record.

Needs CBBD_API_KEY in api/.env (same key as fetch_college_stats.py).

Usage:
    cd scripts && python3 fetch_cbb_games.py            # resumes: skips seasons already stored
    cd scripts && python3 fetch_cbb_games.py --refetch
"""

import os
import sys
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "api", ".env"))

from db_config import DB_CONFIG

CBBD_BASE = "https://api.collegebasketballdata.com"
SEASONS = list(range(2013, 2027))
PAGE_CAP = 3000


def get_games(session, season, start, end):
    for attempt in range(4):
        try:
            resp = session.get(
                f"{CBBD_BASE}/games",
                params={"season": season, "startDateRange": start.isoformat(), "endDateRange": end.isoformat()},
                timeout=(10, 120),
            )
            resp.raise_for_status()
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == 3:
                raise
            print(f"  timeout on {start}..{end}, retrying in 20s", flush=True)
            time.sleep(20)
    games = resp.json()
    if len(games) >= PAGE_CAP and (end - start).days > 1:
        mid = start + timedelta(days=(end - start).days // 2)
        return get_games(session, season, start, mid) + get_games(session, season, mid, end)
    return games


def month_windows(season):
    """[start, end) windows: the API's endDateRange is exclusive (a window
    ending 2013-03-31 returns nothing played on March 31, checked live)."""
    start = date(season - 1, 11, 1)
    last = date(season, 4, 16)
    while start < last:
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        yield start, min(nxt, last)
        start = nxt


def main():
    key = os.getenv("CBBD_API_KEY")
    if not key:
        raise SystemExit("CBBD_API_KEY missing from api/.env")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {key}"

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cbb_games (
            game_id INTEGER PRIMARY KEY,
            season INTEGER NOT NULL,
            start_date TIMESTAMPTZ NOT NULL,
            season_type TEXT,
            tournament TEXT,
            status TEXT,
            neutral_site BOOLEAN,
            game_notes TEXT,
            home_team TEXT, home_conference TEXT, home_seed INTEGER, home_points INTEGER,
            home_elo_start INTEGER, home_winner BOOLEAN,
            away_team TEXT, away_conference TEXT, away_seed INTEGER, away_points INTEGER,
            away_elo_start INTEGER, away_winner BOOLEAN
        );
    """)
    conn.commit()

    cur.execute("SELECT DISTINCT season FROM cbb_games;")
    done = {r[0] for r in cur.fetchall()}
    for season in SEASONS:
        if season in done and "--refetch" not in sys.argv:
            print(f"{season}: already stored, skipping (pass --refetch to redo)")
            continue
        games = {}
        for start, end in month_windows(season):
            batch = get_games(session, season, start, end)
            for g in batch:
                games[g["id"]] = g
            print(f"  {season} {start}..{end}: {len(batch)}", flush=True)
            time.sleep(0.3)
        rows = [(
            g["id"], g["season"], g["startDate"], g.get("seasonType"), g.get("tournament"), g.get("status"),
            g.get("neutralSite"), g.get("gameNotes"),
            g.get("homeTeam"), g.get("homeConference"), g.get("homeSeed"), g.get("homePoints"),
            g.get("homeTeamEloStart"), g.get("homeWinner"),
            g.get("awayTeam"), g.get("awayConference"), g.get("awaySeed"), g.get("awayPoints"),
            g.get("awayTeamEloStart"), g.get("awayWinner"),
        ) for g in games.values()]
        cur.execute("DELETE FROM cbb_games WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(cur, "INSERT INTO cbb_games VALUES %s;", rows)
        conn.commit()
        n_ncaa = sum(1 for g in games.values() if g.get("tournament") == "NCAA")
        print(f"{season}: {len(rows)} games, {n_ncaa} NCAA tournament", flush=True)

    conn.close()


if __name__ == "__main__":
    main()
