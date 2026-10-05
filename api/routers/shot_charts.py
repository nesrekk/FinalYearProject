from functools import lru_cache
from typing import Optional
import shots_lib

from fastapi import APIRouter, HTTPException

from source_badge import make_source

from impact_core import (
    find_player,
    get_db,
    resolve_player,
)

router = APIRouter()


def _shots_source(how: str):
    # Round 8 (R8-014): the Shot Charts badge. `how` is ensure_*_cached()'s "cache" or "live" (fetched just now).
    return make_source(["player_shots"], "stats.nba.com shotchartdetail (bulk files, and per player on first view)",
                       live=how == "live")


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
        "_source": _shots_source(result["source"]),
    }

@router.get("/shots/player/{player_name}")
def get_player_shots(player_name: str, season: Optional[str] = None, player_id: Optional[int] = None):
    """
    Shots for one season (defaults to the player's most recent cached
    season). Triggers the same cache-or-fetch flow as /seasons, so this can
    be called directly without hitting /seasons first. `player_id` (the
    Workbench) picks the player by id instead of by name.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = resolve_player(cursor, player_name, player_id)

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
        "_source": _shots_source(result["source"]),
    }

@router.get("/shots/player/{player_name}/zones")
def get_player_shot_zones(player_name: str, season: int):
    """A player's own regular-season FG% by the 5 real NBA shot zones (Restricted
    Area, Paint, Mid-Range, Corner 3, Above the Break 3), for the comparison
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
        how = shots_lib.ensure_season_shots_cached(int(player_id), resolved_name, season_label)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    # Regular season only (game_id '002…'), like the profile's zones, the league zones this is
    # compared with and every other shot view; before, playoff and play-in shots were counted too
    # (round 8 R8-065).
    shots = [s for s in shots_lib.get_shots_for_season(int(player_id), season_label)
             if str(s["game_id"]).startswith("002")]
    if not shots:
        raise HTTPException(status_code=404, detail=f"No regular-season shot data for {resolved_name} in {season_label}.")
    zones = shots_lib.compute_zone_stats(shots)
    return {"player_id": player_id, "player_name": resolved_name, "season": season_label, "games": "regular season",
            "zones": zones, "_source": _shots_source(how)}

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
        "_source": make_source(["player_shots"], "stats.nba.com shotchartdetail, bulk-loaded into Postgres"),
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
    return {"season": season_label, "zones": zones,
            "_source": make_source(["league_shot_zones"], "stats.nba.com shotchartdetail (league totals per season)")}


# A season with fewer tracked attempts than this is shown greyed out: one
# zone's share can swing by 5+ points on a couple of dozen shots.
ZONE_HISTORY_MIN_FGA = 200


@router.get("/shots/player/{player_name}/zone-history")
def get_player_zone_history(player_name: str, player_id: Optional[int] = None):
    """A player's shot mix season by season: attempts, makes and share of
    their shots in each of the five zones, regular season only (game_id
    '002…'), from the stored player_shots rows (no live fetch). Each season
    carries the league's own share per zone (league_zone_mix) for context.
    A season with several teams is one row (player_shots is per player).
    `player_id` (the Workbench) picks the player by id instead of by name."""
    with get_db() as conn:
        cur = conn.cursor()
        player_id, resolved_name = resolve_player(cur, player_name, player_id)
        cur.execute(
            """SELECT season, loc_x, loc_y, shot_distance, shot_type, shot_zone_basic, shot_made_flag
               FROM player_shots WHERE player_id = %s AND game_id LIKE '002%%'""",
            (int(player_id),),
        )
        cols = ["season", "loc_x", "loc_y", "shot_distance", "shot_type", "shot_zone_basic", "shot_made_flag"]
        by_season = {}
        for row in cur.fetchall():
            s = dict(zip(cols, row))
            by_season.setdefault(s["season"], []).append(s)
        cur.execute("SELECT season, zone, fgm, fga FROM league_zone_mix")
        league = {}
        for season, zone, fgm, fga in cur.fetchall():
            league.setdefault(season, {})[zone] = (fgm, fga)
        cur.execute("SELECT min(season), max(season) FROM league_zone_mix")
        coverage = cur.fetchone()
        cur.execute(
            "SELECT count(DISTINCT season) FROM player_season_stats WHERE player_id = %s AND season < %s",
            (int(player_id), int(coverage[0][:4]) + 1),
        )
        seasons_before = cur.fetchone()[0]

    if not by_season:
        raise HTTPException(
            status_code=404,
            detail=f"No regular-season shot locations stored for {resolved_name}. "
                   f"Shot data covers {coverage[0]} to {coverage[1]}.",
        )

    seasons = []
    for season in sorted(by_season):
        zones = shots_lib.compute_zone_stats(by_season[season])
        fga = sum(z["fga"] for z in zones)
        fgm = sum(z["fgm"] for z in zones)
        lg = league.get(season, {})
        lg_fga = sum(v[1] for v in lg.values())
        for z in zones:
            z["share"] = round(z["fga"] / fga, 4) if fga else None
            lz = lg.get(z["zone"])
            z["league_share"] = round(lz[1] / lg_fga, 4) if lz and lg_fga else None
            z["league_fg_pct"] = round(lz[0] / lz[1], 3) if lz and lz[1] else None
        seasons.append({
            "season": season,
            "fga": fga,
            "fgm": fgm,
            "fg_pct": round(fgm / fga, 3) if fga else None,
            "small_sample": fga < ZONE_HISTORY_MIN_FGA,
            "zones": zones,
        })

    return {
        "player_id": int(player_id),
        "player_name": resolved_name,
        "zones": shots_lib.ZONES,
        "min_fga": ZONE_HISTORY_MIN_FGA,
        "coverage": {"first": coverage[0], "last": coverage[1]},
        # Career seasons before shot locations were recorded (none are shown).
        "seasons_before_coverage": int(seasons_before),
        "seasons": seasons,
        "_source": make_source(
            ["player_shots", "league_zone_mix"],
            "stats.nba.com shot locations, regular season",
        ),
    }
