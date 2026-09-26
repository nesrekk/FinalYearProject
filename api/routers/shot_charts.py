from functools import lru_cache
from typing import Optional
import shots_lib

from fastapi import APIRouter, HTTPException

from impact_core import (
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/shots/player/{player_name}/seasons")
def get_shot_seasons(player_name: str):
    """
    Resolve a player and make sure their career shots are cached (fetching
    live once, on a cache miss, if ENABLE_LIVE_SHOT_FETCH != "false").
    A cold cache miss for a long career can take a couple of minutes — the
    fetch is deliberately rate-limited to avoid getting flagged by
    stats.nba.com.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    try:
        result = shots_lib.ensure_player_shots_cached(int(player_id), resolved_name)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    return {
        "player_id": int(player_id),
        "player_name": resolved_name,
        "seasons": result["seasons"],
        "source": result["source"],
    }

@router.get("/shots/player/{player_name}")
def get_player_shots(player_name: str, season: Optional[str] = None):
    """
    Shots for one season (defaults to the player's most recent cached
    season). Triggers the same cache-or-fetch flow as /seasons, so this can
    be called directly without hitting /seasons first.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    try:
        result = shots_lib.ensure_player_shots_cached(int(player_id), resolved_name)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    seasons = result["seasons"]
    if not seasons:
        raise HTTPException(status_code=404, detail=f"No seasons with shot data for '{resolved_name}'.")

    target_season = season if season in seasons else seasons[-1]
    shots = shots_lib.get_shots_for_season(int(player_id), target_season)

    return {
        "player_id": int(player_id),
        "player_name": resolved_name,
        "season": target_season,
        "seasons": seasons,
        "source": result["source"],
        "shots": shots,
    }

@router.get("/shots/player/{player_name}/zones")
def get_player_shot_zones(player_name: str, season: int):
    """A player's own FG% by the 5 real NBA shot zones (Restricted Area,
    Paint, Mid-Range, Corner 3, Above the Break 3), for the comparison
    page's shot-chart section. Fetches (and caches forever) just the ONE
    requested season — one live request instead of the ~N+1 a full-career
    fetch needs, so this is both much faster and has far fewer places to
    hit a transient network failure. If a full-career fetch already ran
    for this player (e.g. from the single-player Shot Charts page), this
    season is already cached and returns instantly either way."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    season_label = f"{season - 1}-{str(season)[-2:]}"
    try:
        shots_lib.ensure_season_shots_cached(int(player_id), resolved_name, season_label)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    shots = shots_lib.get_shots_for_season(int(player_id), season_label)
    if not shots:
        raise HTTPException(status_code=404, detail=f"No shot data for {resolved_name} in {season_label}.")
    zones = shots_lib.compute_zone_stats(shots)
    return {"player_id": player_id, "player_name": resolved_name, "season": season_label, "zones": zones}

@lru_cache(maxsize=8)
def _league_sample(n: int):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT max(season) FROM player_shots")
        season = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*), avg(shot_made_flag) FROM player_shots WHERE season = %s",
            (season,),
        )
        n_total, fg = cur.fetchone()
        # md5(id) ordering gives a fixed, reproducible random sample.
        cur.execute(
            """SELECT loc_x, loc_y, shot_made_flag FROM player_shots
               WHERE season = %s AND loc_x BETWEEN -250 AND 250 AND loc_y BETWEEN -50 AND 420
               ORDER BY md5(id::text) LIMIT %s""",
            (season, n),
        )
        points = [[int(x), int(y), int(m)] for x, y, m in cur.fetchall()]
    made = sum(p[2] for p in points)
    return {
        "season": season,
        "n_season_shots": int(n_total),
        "season_fg_pct": round(float(fg), 4),
        "n_sample": len(points),
        "sample_fg_pct": round(made / len(points), 4) if points else None,
        "points": points,
        "_source": {
            "tables": ["player_shots"],
            "upstream_api": "stats.nba.com shotchartdetail, bulk-loaded into Postgres",
            "live": False,
            "as_of": None,
        },
    }


@router.get("/shots/league-sample")
def get_league_shot_sample(n: int = 6000):
    """A fixed random sample of real shots (x, y, made) from the latest
    season in player_shots, for the landing page's shot court. Half-court
    locations only; the season totals cover every shot that season."""
    return _league_sample(max(100, min(n, 20000)))


@router.get("/shots/league-zones/{season}")
def get_league_shot_zones(season: int):
    """League-wide FG% by the same 5 zones, one cheap aggregate call per
    season (not per player), cached forever after the first fetch."""
    season_label = f"{season - 1}-{str(season)[-2:]}"
    try:
        zones = shots_lib.get_league_zone_stats(season_label)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Live league shot fetch failed: {e}")
    return {"season": season_label, "zones": zones}
