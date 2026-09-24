
from fastapi import APIRouter, HTTPException

from impact_core import (
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/impact/player/{player_name}/{season}")
def get_player_impact(player_name: str, season: int):
    """Get both raw and star impact scores for a specific player-season."""
    with get_db() as conn:
        cursor = conn.cursor()

        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT player_name, pts, ts_pct, usg_pct, net_rating,
                   w_pct, min, impact_score_raw, impact_score_star
            FROM player_season_stats
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No data for {resolved_name} in season {season}.",
            )

    return {
        "player_name": row[0],
        "player_id": player_id,
        "season": season,
        "stats": {
            "pts": round(float(row[1]), 1),
            "ts_pct": round(float(row[2]), 3),
            "usg_pct": round(float(row[3]), 3),
            "net_rating": round(float(row[4]), 1),
            "w_pct": round(float(row[5]), 3),
            "min": round(float(row[6]), 1),
        },
        "impact_score_raw": round(float(row[7]), 4) if row[7] is not None else None,
        "impact_score_star": round(float(row[8]), 4) if row[8] is not None else None,
    }
