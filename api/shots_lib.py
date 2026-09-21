"""
shots_lib.py
============
Shared shot-chart caching layer used by impact_api.py (live endpoint) and
scripts/prewarm_shots.py (offline batch pre-warm).

Design goals (see progress.txt / PROJECT_DETAILS.md conventions):
  - stats.nba.com is an unofficial, scrape-prone endpoint. We NEVER fire more
    than one live request at a time across the whole process (a single
    process-wide lock), we sleep between every request, and we back off hard
    on 403/429. This is the same pattern already used in the old
    fetch_career_shots_to_json.py / fetch_jokic_shots.py scripts, just
    consolidated so both the API and the batch script share one code path.
  - Once a player's career shots are fetched, they are cached in Postgres
    forever (historical shot locations never change), so a given player is
    only ever fetched live once, by whoever searches them first.
  - ENABLE_LIVE_SHOT_FETCH=false fully disables live fetching (e.g. for a
    demo where you want zero network dependency) — only pre-warmed / already
    cached players will work.
"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Optional

import requests
from psycopg2 import pool
from psycopg2.extras import execute_values

# ─── Config ─────────────────────────────────────────────────────────────────

LIVE_FETCH_ENABLED = os.getenv("ENABLE_LIVE_SHOT_FETCH", "true").strip().lower() != "false"
REQUEST_SLEEP_SECONDS = 5
BACKOFF_SLEEP_SECONDS = 30
MAX_RETRIES = 3

DB_POOL = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=3,
    host="localhost",
    port="5432",
    user="postgres",
    password="meinkampf:)",
    dbname="nba_analytics",
)

# Only one live nba_api fetch job may run at a time, project-wide. This is
# the main anti-ban safeguard: no matter how many users search at once,
# stats.nba.com only ever sees one request in flight.
_FETCH_LOCK = threading.Lock()


class ShotsUnavailable(Exception):
    """Raised when a player isn't cached and live fetching is disabled."""


class ShotsFetchFailed(Exception):
    """Raised when a live fetch was attempted but failed."""


def get_db():
    return DB_POOL.getconn()


def put_db(conn):
    DB_POOL.putconn(conn)


def ensure_schema():
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS player_shots (
                id SERIAL PRIMARY KEY,
                player_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                season TEXT NOT NULL,
                game_id TEXT,
                loc_x INTEGER,
                loc_y INTEGER,
                shot_made_flag SMALLINT,
                shot_type TEXT,
                shot_distance INTEGER,
                period INTEGER,
                minutes_remaining INTEGER,
                seconds_remaining INTEGER
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_player_shots_player_season "
            "ON player_shots(player_id, season);"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS player_shots_cache_status (
                player_id INTEGER PRIMARY KEY,
                player_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            );
            """
        )
        conn.commit()
    finally:
        put_db(conn)


# ─── Live fetch (rate-limited, backed off) ─────────────────────────────────

def _build_headers() -> dict:
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
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


def _to_int(v, default=0):
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except Exception:
        return default


def _fetch_career_shots_live(player_id: int, log=print) -> list[dict]:
    """Blocking, rate-limited fetch of every shot in a player's career.
    Caller MUST hold _FETCH_LOCK before calling this.
    """
    from nba_api.stats.endpoints import shotchartdetail, playercareerstats

    proxy = (os.getenv("NBA_API_PROXY") or "").strip() or None

    def extract_shots(df):
        if df is None or df.empty:
            return []
        season_col = None
        for c in ("SEASON_1", "SEASON", "SEASON_YEAR"):
            if c in df.columns:
                season_col = c
                break
        out_rows = []
        for _, row in df.iterrows():
            season_val = row.get(season_col) if season_col else None
            season = str(season_val) if season_val is not None else ""
            out_rows.append(
                {
                    "player_id": _to_int(row.get("PLAYER_ID"), default=player_id),
                    "game_id": str(row.get("GAME_ID") or ""),
                    "loc_x": _to_int(row.get("LOC_X")),
                    "loc_y": _to_int(row.get("LOC_Y")),
                    "shot_made_flag": _to_int(row.get("SHOT_MADE_FLAG")),
                    "shot_type": str(row.get("SHOT_TYPE") or ""),
                    "shot_distance": _to_int(row.get("SHOT_DISTANCE")),
                    "period": _to_int(row.get("PERIOD")),
                    "minutes_remaining": _to_int(row.get("MINUTES_REMAINING")),
                    "seconds_remaining": _to_int(row.get("SECONDS_REMAINING")),
                    "season": season,
                }
            )
        return out_rows

    log(f"[shots] fetching season list for player_id={player_id}...")
    season_rows = []
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            cs = playercareerstats.PlayerCareerStats(
                player_id=player_id,
                headers=_build_headers(),
                proxy=proxy,
                timeout=(30, 300),
            )
            frames = cs.get_data_frames()
            if frames:
                season_rows = frames[0].to_dict("records")
            break
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES:
                log(f"[shots] season list timeout ({type(e).__name__}); cooling down {BACKOFF_SLEEP_SECONDS}s")
                time.sleep(BACKOFF_SLEEP_SECONDS)
                continue
            raise

    seasons = []
    for r in season_rows or []:
        s = r.get("SEASON") or r.get("SEASON_YEAR") or r.get("SEASON_ID")
        if s:
            seasons.append(str(s))
    seasons = sorted(set(s for s in seasons if "-" in s))
    if not seasons:
        seasons = ["Career"]

    time.sleep(REQUEST_SLEEP_SECONDS)

    all_shots = []
    for i, season in enumerate(seasons, start=1):
        log(f"[shots] player_id={player_id} season={season} ({i}/{len(seasons)})")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                sc = shotchartdetail.ShotChartDetail(
                    team_id=0,
                    player_id=player_id,
                    season_nullable=season,
                    season_type_all_star="Regular Season",
                    context_measure_simple="FGA",
                    headers=_build_headers(),
                    proxy=proxy,
                    timeout=(30, 300),
                )
                frames = sc.get_data_frames()
                if frames:
                    all_shots.extend(extract_shots(frames[0]))
                break
            except requests.HTTPError as e:
                status = getattr(e.response, "status_code", None)
                if status in (403, 429) and attempt < MAX_RETRIES:
                    log(f"[shots] HTTP {status}; cooling down {BACKOFF_SLEEP_SECONDS}s")
                    time.sleep(BACKOFF_SLEEP_SECONDS)
                    continue
                raise
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
                if attempt < MAX_RETRIES:
                    log(f"[shots] timeout ({type(e).__name__}); cooling down {BACKOFF_SLEEP_SECONDS}s")
                    time.sleep(BACKOFF_SLEEP_SECONDS)
                    continue
                raise

        if i < len(seasons):
            time.sleep(REQUEST_SLEEP_SECONDS)

    return all_shots


# ─── Cache read/write ───────────────────────────────────────────────────────

def get_cache_status(player_id: int) -> Optional[str]:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM player_shots_cache_status WHERE player_id = %s;",
            (player_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        put_db(conn)


def get_cached_seasons(player_id: int) -> list[str]:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT season FROM player_shots WHERE player_id = %s ORDER BY season;",
            (player_id,),
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        put_db(conn)


def get_shots_for_season(player_id: int, season: str) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT game_id, loc_x, loc_y, shot_made_flag, shot_type,
                   shot_distance, period, minutes_remaining, seconds_remaining, season
            FROM player_shots
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        cols = ["game_id", "loc_x", "loc_y", "shot_made_flag", "shot_type",
                "shot_distance", "period", "minutes_remaining", "seconds_remaining", "season"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        put_db(conn)


def _set_status(player_id: int, player_name: str, status: str):
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO player_shots_cache_status (player_id, player_name, status, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (player_id)
            DO UPDATE SET status = EXCLUDED.status, updated_at = NOW();
            """,
            (player_id, player_name, status),
        )
        conn.commit()
    finally:
        put_db(conn)


def store_shots(player_id: int, player_name: str, shots: list[dict]):
    conn = get_db()
    try:
        cur = conn.cursor()
        # Clear any partial data from a previous failed attempt before reinserting.
        cur.execute("DELETE FROM player_shots WHERE player_id = %s;", (player_id,))
        rows = [
            (
                player_id,
                player_name,
                str(s.get("season") or ""),
                str(s.get("game_id") or ""),
                _to_int(s.get("loc_x")),
                _to_int(s.get("loc_y")),
                _to_int(s.get("shot_made_flag")),
                str(s.get("shot_type") or ""),
                _to_int(s.get("shot_distance")),
                _to_int(s.get("period")),
                _to_int(s.get("minutes_remaining")),
                _to_int(s.get("seconds_remaining")),
            )
            for s in shots
        ]
        if rows:
            execute_values(
                cur,
                """
                INSERT INTO player_shots
                (player_id, player_name, season, game_id, loc_x, loc_y,
                 shot_made_flag, shot_type, shot_distance, period,
                 minutes_remaining, seconds_remaining)
                VALUES %s;
                """,
                rows,
            )
        conn.commit()
    finally:
        put_db(conn)


def ensure_player_shots_cached(player_id: int, player_name: str, log=print) -> dict:
    """Returns {"seasons": [...], "source": "cache"|"live"}.
    Raises ShotsUnavailable / ShotsFetchFailed on failure.
    """
    status = get_cache_status(player_id)
    if status == "done":
        return {"seasons": get_cached_seasons(player_id), "source": "cache"}

    if not LIVE_FETCH_ENABLED:
        raise ShotsUnavailable(
            f"'{player_name}' isn't cached yet and live fetching is disabled "
            "(ENABLE_LIVE_SHOT_FETCH=false)."
        )

    with _FETCH_LOCK:
        # Re-check now that we hold the lock — another request may have just
        # finished fetching this exact player while we were waiting.
        status = get_cache_status(player_id)
        if status == "done":
            return {"seasons": get_cached_seasons(player_id), "source": "cache"}

        _set_status(player_id, player_name, "pending")
        try:
            shots = _fetch_career_shots_live(player_id, log=log)
            if not shots:
                _set_status(player_id, player_name, "failed")
                raise ShotsFetchFailed(f"No shot data returned for '{player_name}'.")
            store_shots(player_id, player_name, shots)
            _set_status(player_id, player_name, "done")
        except ShotsFetchFailed:
            raise
        except Exception as e:
            _set_status(player_id, player_name, "failed")
            raise ShotsFetchFailed(str(e)) from e

    return {"seasons": get_cached_seasons(player_id), "source": "live"}
