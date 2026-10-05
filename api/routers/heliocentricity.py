import time
from typing import Optional
from psycopg2 import pool
from source_badge import make_source
from season_team import season_team_sql

from fastapi import APIRouter, HTTPException

from impact_core import (
    RADAR_MIN_GAMES,
    RADAR_MIN_MINUTES,
    _fetch_pt_possession_stats,
    _percentile_rank,
    check_season_exists,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/players/heliocentricity")
def get_heliocentricity_leaderboard(season: Optional[int] = None, top_n: int = 25):
    """Real touches/time-of-possession-share/usage%/assist% for this
    season's qualified pool, ranked by a disclosed equal-weighted average
    of each stat's real percentile rank."""
    top_n = max(1, min(top_n, 100))

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        check_season_exists(cursor, resolved_season)
        cursor.execute(
            f"""
            SELECT player_id, player_name, {season_team_sql(cursor)}, usg_pct, ast_pct
            FROM player_season_stats
            WHERE season = %s AND min >= %s AND gp >= %s
              AND usg_pct IS NOT NULL AND ast_pct IS NOT NULL;
            """,
            (resolved_season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        db_rows = cursor.fetchall()

    pt_stats = _fetch_pt_possession_stats(resolved_season)
    if pt_stats is None:
        raise HTTPException(
            status_code=503,
            detail="stats.nba.com didn't answer within 3 s (touch and time-of-possession tracking isn't stored). Try again in a moment.",
        )
    if not pt_stats:
        raise HTTPException(status_code=404, detail=f"stats.nba.com has no tracking rows for season {resolved_season}.")

    combined = []
    for player_id, player_name, team_abbr, usg_pct, ast_pct in db_rows:
        pt = pt_stats.get(player_name.lower())
        if not pt:
            continue
        combined.append({
            "player_id": player_id, "player_name": player_name, "team_abbreviation": team_abbr,
            "usg_pct": usg_pct, "ast_pct": ast_pct,
            "touches": pt["touches"], "time_of_poss_share": pt["time_of_poss_share"],
            "avg_sec_per_touch": pt["avg_sec_per_touch"], "pts_per_touch": pt["pts_per_touch"],
        })

    if len(combined) < 10:
        raise HTTPException(status_code=404, detail=f"Not enough matched players to build a leaderboard for season {resolved_season}.")

    pools = {k: [r[k] for r in combined] for k in ("time_of_poss_share", "touches", "usg_pct", "ast_pct")}
    for r in combined:
        pcts = [_percentile_rank(r[k], pools[k]) for k in ("time_of_poss_share", "touches", "usg_pct", "ast_pct")]
        r["percentiles"] = {
            "time_of_poss_share": pcts[0], "touches": pcts[1], "usg_pct": pcts[2], "ast_pct": pcts[3],
        }
        r["heliocentricity_index"] = round(sum(pcts) / len(pcts), 1)

    combined.sort(key=lambda r: r["heliocentricity_index"], reverse=True)

    return {
        "season": resolved_season,
        "pool_size": len(combined),
        "methodology": (
            "heliocentricity_index is the simple average of four real percentile ranks within this season's "
            "qualified pool (min>=15 mpg, gp>=20): real time-of-possession share of the player's own team "
            "(live NBA tracking data), real touches per game, real usage%, and real assist%. Equal weights, "
            "fully disclosed — not a trained or fitted model, the same kind of transparent composite this "
            "project's own Impact Score already uses."
        ),
        "results": [
            {**r, "rank": i + 1} for i, r in enumerate(combined[:top_n])
        ],
        "_source": make_source(
            ["player_season_stats"], "nba_api (stats.nba.com, live touch/possession tracking)", live=True,
        ),
    }
