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

from db_config import DB_CONFIG

DB_POOL = pool.SimpleConnectionPool(minconn=1, maxconn=3, **DB_CONFIG)

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
                seconds_remaining INTEGER,
                shot_zone_basic TEXT
            );
            """
        )
        # Additive — players cached before shot_zone_basic existed just have
        # NULL here; classify_zone() below falls back to a coordinate-based
        # approximation for those rows instead of requiring a re-fetch.
        cur.execute(
            "ALTER TABLE player_shots ADD COLUMN IF NOT EXISTS shot_zone_basic TEXT;"
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
        # League-wide FG% by zone, one row per (season, zone) — a single
        # cheap league-aggregate API call per season (NOT one call per
        # player), cached forever like everything else here.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS league_shot_zones (
                season TEXT NOT NULL,
                zone TEXT NOT NULL,
                fgm INTEGER NOT NULL,
                fga INTEGER NOT NULL,
                fg_pct FLOAT NOT NULL,
                PRIMARY KEY (season, zone)
            );
            """
        )
        conn.commit()
    finally:
        put_db(conn)


# ─── Shot zone classification ──────────────────────────────────────────────
# Five zones, matching both stats.nba.com's own SHOT_ZONE_BASIC values (used
# directly for any shot fetched after this was added) and
# LeagueDashTeamShotLocations' "By Zone" columns (used for the league
# average) — so a player's zone FG% and the league's zone FG% are always
# comparable apples-to-apples, not two different bucketing schemes.
ZONES = ["Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"]


def classify_zone(loc_x, loc_y, shot_distance, shot_type, shot_zone_basic=None):
    """Prefer the real NBA-assigned zone (stored since this column existed);
    fall back to a coordinate-based approximation only for legacy rows
    fetched before shot_zone_basic was captured."""
    if shot_zone_basic:
        if shot_zone_basic == "Left Corner 3" or shot_zone_basic == "Right Corner 3":
            return "Corner 3"
        if shot_zone_basic in ZONES:
            return shot_zone_basic
        if shot_zone_basic == "Backcourt":
            return None
        return None

    is_three = (shot_type or "").startswith("3PT")
    x, y = (loc_x or 0), (loc_y or 0)
    if is_three:
        # NBA's corner-3 definition: within the corner strip along the
        # baseline (roughly 14ft from the sideline, below the arc's bend).
        if abs(x) > 220 and y < 92:
            return "Corner 3"
        return "Above the Break 3"
    dist = shot_distance or 0
    if dist <= 4:
        return "Restricted Area"
    if abs(x) <= 80 and y <= 190:
        return "In The Paint (Non-RA)"
    return "Mid-Range"


def compute_zone_stats(shots: list[dict]) -> list[dict]:
    """[{zone, fgm, fga, fg_pct}] for one player's shots, real zones only
    (unclassifiable shots — e.g. backcourt heaves — are dropped, same as
    they'd be excluded from any real shot-zone chart)."""
    totals = {z: {"fgm": 0, "fga": 0} for z in ZONES}
    for s in shots:
        zone = classify_zone(
            s.get("loc_x"), s.get("loc_y"), s.get("shot_distance"),
            s.get("shot_type"), s.get("shot_zone_basic"),
        )
        if zone is None or zone not in totals:
            continue
        totals[zone]["fga"] += 1
        if s.get("shot_made_flag"):
            totals[zone]["fgm"] += 1
    return [
        {
            "zone": z,
            "fgm": t["fgm"],
            "fga": t["fga"],
            "fg_pct": round(t["fgm"] / t["fga"], 3) if t["fga"] else None,
        }
        for z, t in totals.items()
    ]


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


def _extract_shots(df, player_id: int):
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
                "shot_zone_basic": str(row.get("SHOT_ZONE_BASIC") or "") or None,
            }
        )
    return out_rows


def _fetch_career_shots_live(player_id: int, log=print) -> list[dict]:
    """Blocking, rate-limited fetch of every shot in a player's career.
    Caller MUST hold _FETCH_LOCK before calling this.
    """
    from nba_api.stats.endpoints import shotchartdetail, playercareerstats

    proxy = (os.getenv("NBA_API_PROXY") or "").strip() or None
    extract_shots = lambda df: _extract_shots(df, player_id)  # noqa: E731

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
                   shot_distance, period, minutes_remaining, seconds_remaining, season,
                   shot_zone_basic
            FROM player_shots
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        cols = ["game_id", "loc_x", "loc_y", "shot_made_flag", "shot_type",
                "shot_distance", "period", "minutes_remaining", "seconds_remaining", "season",
                "shot_zone_basic"]
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
                s.get("shot_zone_basic"),
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
                 minutes_remaining, seconds_remaining, shot_zone_basic)
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


# ─── Fast path: one season only ────────────────────────────────────────────
# ensure_player_shots_cached above fetches an ENTIRE career (playercareerstats
# to enumerate seasons, then one shotchartdetail call per season) — worth it
# for the single-player Shot Charts page, where switching seasons afterward
# should be instant. The comparison page only ever wants one season at a
# time, so paying for the whole career up front is pure waste — and, found
# live: it's also strictly less reliable, since the season-enumeration call
# is itself one more request that can time out before any shot data comes
# back at all (this is exactly what failed for a full-career fetch in
# testing). A single shotchartdetail call for just the requested season has
# far fewer places to go wrong.

def season_has_rows(player_id: int, season_label: str) -> bool:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM player_shots WHERE player_id = %s AND season = %s LIMIT 1;",
            (player_id, season_label),
        )
        return cur.fetchone() is not None
    finally:
        put_db(conn)


def store_season_shots(player_id: int, player_name: str, season_label: str, shots: list[dict]):
    conn = get_db()
    try:
        cur = conn.cursor()
        # Season-scoped delete only — must never wipe out other seasons
        # already cached (by this path or the full-career path above).
        cur.execute(
            "DELETE FROM player_shots WHERE player_id = %s AND season = %s;",
            (player_id, season_label),
        )
        rows = [
            (
                player_id, player_name, str(s.get("season") or season_label),
                str(s.get("game_id") or ""), _to_int(s.get("loc_x")), _to_int(s.get("loc_y")),
                _to_int(s.get("shot_made_flag")), str(s.get("shot_type") or ""),
                _to_int(s.get("shot_distance")), _to_int(s.get("period")),
                _to_int(s.get("minutes_remaining")), _to_int(s.get("seconds_remaining")),
                s.get("shot_zone_basic"),
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
                 minutes_remaining, seconds_remaining, shot_zone_basic)
                VALUES %s;
                """,
                rows,
            )
        conn.commit()
    finally:
        put_db(conn)


def _fetch_one_season_live(player_id: int, season_label: str, log=print) -> list[dict]:
    """One shotchartdetail call for exactly one season. Caller MUST hold
    _FETCH_LOCK."""
    from nba_api.stats.endpoints import shotchartdetail

    proxy = (os.getenv("NBA_API_PROXY") or "").strip() or None
    log(f"[shots] fetching single season {season_label} for player_id={player_id}...")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            sc = shotchartdetail.ShotChartDetail(
                team_id=0,
                player_id=player_id,
                season_nullable=season_label,
                season_type_all_star="Regular Season",
                context_measure_simple="FGA",
                headers=_build_headers(),
                proxy=proxy,
                timeout=(30, 60),
            )
            frames = sc.get_data_frames()
            return _extract_shots(frames[0], player_id) if frames else []
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
    return []


def ensure_season_shots_cached(player_id: int, player_name: str, season_label: str, log=print) -> str:
    """Fast path: cache (or fetch) just ONE season instead of the whole
    career — one live request instead of ~N+1. Returns "cache" or "live".
    Raises ShotsUnavailable / ShotsFetchFailed."""
    if season_has_rows(player_id, season_label):
        return "cache"

    if not LIVE_FETCH_ENABLED:
        raise ShotsUnavailable(
            f"'{player_name}' isn't cached for {season_label} and live fetching is disabled "
            "(ENABLE_LIVE_SHOT_FETCH=false)."
        )

    with _FETCH_LOCK:
        if season_has_rows(player_id, season_label):  # another request may have just finished it
            return "cache"
        try:
            shots = _fetch_one_season_live(player_id, season_label, log=log)
            if not shots:
                raise ShotsFetchFailed(f"No shot data returned for '{player_name}' in {season_label}.")
            store_season_shots(player_id, player_name, season_label, shots)
        except ShotsFetchFailed:
            raise
        except Exception as e:
            raise ShotsFetchFailed(str(e)) from e

    return "live"


# ─── League-wide zone averages (for "player % vs league %") ───────────────
# One aggregate API call per season — NOT one call per player — so this
# never competes with the per-player rate limit above in any meaningful way.
# Reuses the same _FETCH_LOCK anyway, to keep every live stats.nba.com call
# in this process strictly serialized.

def _fetch_league_zone_totals_live(season_label: str) -> dict:
    from nba_api.stats.endpoints import leaguedashteamshotlocations

    proxy = (os.getenv("NBA_API_PROXY") or "").strip() or None
    # Deliberately NOT using _build_headers()'s spoofed browser headers here
    # (those are tuned for the per-shot detail endpoint) — verified live
    # that this specific league-aggregate endpoint responds in under a
    # second with nba_api's own default headers, but stalled to a 60s
    # read-timeout with the custom header set.
    ep = leaguedashteamshotlocations.LeagueDashTeamShotLocations(
        season=season_label,
        season_type_all_star="Regular Season",
        distance_range="By Zone",
        per_mode_detailed="Totals",
        proxy=proxy,
        timeout=45,
    )
    d = ep.get_dict()
    rs = d["resultSets"]
    zone_header = rs["headers"][0]
    zone_names = zone_header["columnNames"]
    span = zone_header.get("columnSpan", 3)
    skip = zone_header.get("columnsToSkip", 2)

    totals: dict[str, dict] = {}
    for row in rs["rowSet"]:
        for i, zname in enumerate(zone_names):
            # "Corner 3" here is already Left+Right combined — use it
            # directly rather than also adding the individual Left/Right
            # columns, which would double-count.
            if zname in ("Left Corner 3", "Right Corner 3", "Backcourt"):
                continue
            if zname not in ZONES:
                continue
            start = skip + i * span
            fgm, fga = row[start], row[start + 1]
            t = totals.setdefault(zname, {"fgm": 0, "fga": 0})
            t["fgm"] += fgm or 0
            t["fga"] += fga or 0
    return totals


def get_league_zone_stats(season_label: str, log=print) -> list[dict]:
    """[{zone, fgm, fga, fg_pct}] league-wide for one season ('2024-25'
    format). Cached forever in Postgres after the first fetch."""
    def _read_cache():
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT zone, fgm, fga, fg_pct FROM league_shot_zones WHERE season = %s;",
                (season_label,),
            )
            return cur.fetchall()
        finally:
            put_db(conn)

    rows = _read_cache()
    if rows:
        return [{"zone": r[0], "fgm": r[1], "fga": r[2], "fg_pct": r[3]} for r in rows]

    with _FETCH_LOCK:
        rows = _read_cache()  # another request may have just finished this
        if rows:
            return [{"zone": r[0], "fgm": r[1], "fga": r[2], "fg_pct": r[3]} for r in rows]

        log(f"[shots] fetching league-wide zone totals for {season_label}...")
        totals = _fetch_league_zone_totals_live(season_label)

        result = []
        conn = get_db()
        try:
            cur = conn.cursor()
            for zone in ZONES:
                t = totals.get(zone, {"fgm": 0, "fga": 0})
                fg_pct = round(t["fgm"] / t["fga"], 3) if t["fga"] else 0.0
                cur.execute(
                    """
                    INSERT INTO league_shot_zones (season, zone, fgm, fga, fg_pct)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (season, zone) DO UPDATE
                    SET fgm = EXCLUDED.fgm, fga = EXCLUDED.fga, fg_pct = EXCLUDED.fg_pct;
                    """,
                    (season_label, zone, t["fgm"], t["fga"], fg_pct),
                )
                result.append({"zone": zone, "fgm": t["fgm"], "fga": t["fga"], "fg_pct": fg_pct})
            conn.commit()
        finally:
            put_db(conn)
        time.sleep(REQUEST_SLEEP_SECONDS)
        return result
