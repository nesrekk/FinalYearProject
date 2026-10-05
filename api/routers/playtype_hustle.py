from typing import Optional

from fastapi import APIRouter, HTTPException

from source_badge import make_source

from impact_core import (
    HUSTLE_STAT_MAP,
    find_player,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/hustle/leaders")
def get_hustle_leaders(stat: str = "deflections", season: Optional[int] = None, top_n: int = 15):
    stat = stat.lower()
    if stat not in HUSTLE_STAT_MAP:
        raise HTTPException(status_code=400, detail=f"stat must be one of {sorted(HUSTLE_STAT_MAP)}.")
    top_n = max(1, min(top_n, 50))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_hustle');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No hustle data yet — run scripts/fetch_hustle_stats.py first.")
        resolved_season = season or get_latest_season(cursor)
        cursor.execute(
            f"""SELECT player_id, player_name, team_abbreviation, gp, {stat}
                FROM player_hustle WHERE season = %s AND {stat} IS NOT NULL
                ORDER BY {stat} DESC LIMIT %s;""",
            (resolved_season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": resolved_season,
        "stat": stat,
        "stat_label": HUSTLE_STAT_MAP[stat],
        "results": [
            {"rank": i + 1, "player_id": r[0], "player_name": r[1], "team_abbreviation": r[2], "gp": r[3], "value": round(r[4], 2)}
            for i, r in enumerate(rows)
        ],
        "methodology": (
            f"Real per-game {HUSTLE_STAT_MAP[stat]} for season {resolved_season}, from the NBA's own real "
            "hustle-stat tracking (LeagueHustleStatsPlayer) — real effort/activity stats the traditional "
            "box score doesn't capture."
        ),
        "_source": make_source(["player_hustle"], "nba_api (stats.nba.com, LeagueHustleStatsPlayer), stored"),
    }

@router.get("/players/playtype-profile/{player_name}")
def get_playtype_profile(player_name: str, season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_playtypes');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No play-type data yet — run scripts/fetch_playtypes.py first.")

        player_id, resolved_name = find_player(cursor, player_name)
        resolved_season = season or get_latest_season(cursor)

        cursor.execute(
            """SELECT play_type, gp, poss, freq, ppp, percentile
               FROM player_playtypes
               WHERE player_id = %s AND season = %s AND side = 'offensive'
               ORDER BY freq DESC;""",
            (player_id, resolved_season),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No real play-type data for {resolved_name} in season {resolved_season} "
                   "(too few possessions in any play type to qualify, or before real play-type tracking began in 2012-13).",
        )

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": resolved_season,
        "play_types": [
            {"play_type": r[0], "gp": r[1], "poss": r[2], "freq": round(r[3], 4), "ppp": round(r[4], 3), "percentile": round(r[5], 3)}
            for r in rows
        ],
        "methodology": (
            "Real offensive play-type breakdown (NBA's own real Synergy tracking): freq is this real player's "
            "real share of their own offensive possessions run through that real play type; ppp is their real "
            "points per possession in it; percentile is their real league percentile rank for efficiency in "
            "that play type (higher is better), among real players with enough real possessions to qualify."
        ),
    }
