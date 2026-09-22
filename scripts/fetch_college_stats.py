"""
fetch_college_stats.py
=======================
One-time (re-runnable) bulk fetch of real D1 college basketball player-
season stats from CollegeBasketballData.com (CBBD), stored in Postgres as
college_player_season_stats. One API call per season (not per player/team),
well within the free tier's ~1000 call/period quota.

This is the real data foundation for the Draft Prospect Comp Finder: for
each NBA rookie already in player_season_stats, this lets us look up their
real final college season, era-normalize it the same way the rest of this
project's similarity/trajectory features do, and find real historical
college comps whose real NBA rookie outcomes are already known.

Usage:
    cd scripts && python3 fetch_college_stats.py
"""

import os
import sys
import time

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "api", ".env"))

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

CBBD_API_KEY = os.getenv("CBBD_API_KEY")
CBBD_BASE = "https://api.collegebasketballdata.com"

# Covers college seasons whose players plausibly overlap with this
# project's NBA data window (2010-2026) as rookies.
SEASONS = list(range(2014, 2026))

FIELDS = [
    "season", "athleteId", "name", "team", "conference", "position",
    "games", "minutes", "points", "assists", "turnovers", "steals", "blocks",
    "usage", "offensiveRating", "defensiveRating", "netRating",
    "trueShootingPct", "effectiveFieldGoalPct", "PORPAG",
]


def fetch_season(season: int):
    if not CBBD_API_KEY:
        print("  ❌ CBBD_API_KEY not set in api/.env")
        sys.exit(1)
    resp = requests.get(
        f"{CBBD_BASE}/stats/player/season",
        params={"season": season},
        headers={"Authorization": f"Bearer {CBBD_API_KEY}"},
        timeout=30,
    )
    resp.raise_for_status()
    remaining = resp.headers.get("X-CallLimit-Remaining")
    return resp.json(), remaining


def flatten(row):
    rebounds = row.get("rebounds") or {}
    return {
        "season": row.get("season"),
        "athlete_id": row.get("athleteId"),
        "name": row.get("name"),
        "team": row.get("team"),
        "conference": row.get("conference"),
        "position": row.get("position"),
        "games": row.get("games"),
        "minutes": row.get("minutes"),
        "points": row.get("points"),
        "assists": row.get("assists"),
        "turnovers": row.get("turnovers"),
        "steals": row.get("steals"),
        "blocks": row.get("blocks"),
        "rebounds_total": rebounds.get("total"),
        "usage": row.get("usage"),
        "off_rating": row.get("offensiveRating"),
        "def_rating": row.get("defensiveRating"),
        "net_rating": row.get("netRating"),
        "ts_pct": row.get("trueShootingPct"),
        "efg_pct": row.get("effectiveFieldGoalPct"),
        "porpag": row.get("PORPAG"),
    }


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS college_player_season_stats (
            season INTEGER NOT NULL,
            athlete_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            team TEXT,
            conference TEXT,
            position TEXT,
            games DOUBLE PRECISION,
            minutes DOUBLE PRECISION,
            points DOUBLE PRECISION,
            assists DOUBLE PRECISION,
            turnovers DOUBLE PRECISION,
            steals DOUBLE PRECISION,
            blocks DOUBLE PRECISION,
            rebounds_total DOUBLE PRECISION,
            usage DOUBLE PRECISION,
            off_rating DOUBLE PRECISION,
            def_rating DOUBLE PRECISION,
            net_rating DOUBLE PRECISION,
            ts_pct DOUBLE PRECISION,
            efg_pct DOUBLE PRECISION,
            porpag DOUBLE PRECISION,
            PRIMARY KEY (season, athlete_id)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_college_stats_name ON college_player_season_stats (LOWER(name));")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_college_stats_season ON college_player_season_stats (season);")
    conn.commit()

    cols = [
        "season", "athlete_id", "name", "team", "conference", "position",
        "games", "minutes", "points", "assists", "turnovers", "steals", "blocks",
        "rebounds_total", "usage", "off_rating", "def_rating", "net_rating",
        "ts_pct", "efg_pct", "porpag",
    ]
    upsert = f"""
        INSERT INTO college_player_season_stats ({', '.join(cols)})
        VALUES %s
        ON CONFLICT (season, athlete_id) DO UPDATE SET
            {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c not in ('season', 'athlete_id'))};
    """

    total_rows = 0
    for season in SEASONS:
        print(f"Season {season}...", end=" ", flush=True)
        try:
            data, remaining = fetch_season(season)
        except Exception as e:
            print(f"❌ {e}")
            continue
        rows = [flatten(r) for r in data if r.get("athleteId") is not None]
        values = [tuple(r[c] for c in cols) for r in rows]
        if values:
            psycopg2.extras.execute_values(cursor, upsert, values)
            conn.commit()
        total_rows += len(values)
        print(f"✅ {len(values)} players (quota remaining: {remaining})")
        time.sleep(0.5)

    cursor.execute("SELECT COUNT(*), COUNT(DISTINCT season) FROM college_player_season_stats;")
    count, n_seasons = cursor.fetchone()
    print(f"\n✅ Done. {count} total rows across {n_seasons} seasons ({total_rows} upserted this run).")
    conn.close()


if __name__ == "__main__":
    main()
