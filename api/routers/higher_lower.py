from psycopg2 import pool

from fastapi import APIRouter, HTTPException

from impact_core import (
    HIGHER_LOWER_MIN_CAREER_GAMES,
    get_db,
)

router = APIRouter()


@router.get("/games/higher-lower/pool")
def get_higher_lower_pool():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.player_id,
                   MAX(p.player_name) AS player_name,
                   (array_agg(p.team_abbreviation ORDER BY p.season DESC))[1] AS team_abbreviation,
                   SUM(p.pts * p.gp) AS career_pts,
                   SUM(p.reb * p.gp) AS career_reb,
                   SUM(p.ast * p.gp) AS career_ast,
                   SUM(p.gp) AS career_gp
            FROM player_season_stats p
            GROUP BY p.player_id
            HAVING SUM(p.gp) >= %s;
            """,
            (HIGHER_LOWER_MIN_CAREER_GAMES,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No qualified player pool.")

    players = [
        {
            "player_id": player_id,
            "player_name": player_name,
            "team_abbreviation": team_abbreviation,
            "career_pts": round(career_pts) if career_pts is not None else None,
            "career_reb": round(career_reb) if career_reb is not None else None,
            "career_ast": round(career_ast) if career_ast is not None else None,
            "career_gp": int(career_gp) if career_gp is not None else None,
        }
        for player_id, player_name, team_abbreviation, career_pts, career_reb, career_ast, career_gp in rows
    ]

    return {
        "min_career_games": HIGHER_LOWER_MIN_CAREER_GAMES,
        "stat_options": [
            {"key": "career_pts", "label": "Career Points"},
            {"key": "career_reb", "label": "Career Rebounds"},
            {"key": "career_ast", "label": "Career Assists"},
            {"key": "career_gp", "label": "Career Games Played"},
        ],
        "pool_size": len(players),
        "players": players,
    }
