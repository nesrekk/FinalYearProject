from functools import lru_cache

import psycopg2
from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

COLUMNS = ["player_id", "player_name", "group", "first_season", "last_season", "seasons", "position",
           "height_in", "hall_of_fame", "games", "pts", "reb", "ast", "ppg", "rpg", "apg", "win_shares",
           "peak_season", "peak_ws", "mvps", "all_nba", "all_nba_first", "all_defense", "all_star",
           "has_photo", "facts", "trivia"]


@lru_cache(maxsize=1)
def _greats():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT player_id, player_name, grp, first_season, last_season, seasons, position, height_in,
                          hall_of_fame, games, pts, reb, ast, ppg, rpg, apg, win_shares, peak_season, peak_ws,
                          mvps, all_nba, all_nba_first, all_defense, all_star, has_photo, facts, trivia
                   FROM greats ORDER BY mvps DESC, all_nba DESC, win_shares DESC"""
            )
            rows = [dict(zip(COLUMNS, r)) for r in cur.fetchall()]
            cur.execute("SELECT value FROM greats_meta WHERE key = 'stars_rule'")
            rule = cur.fetchone()[0]
    except psycopg2.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail="Greats not built yet: run scripts/build_greats.py.")
    for r in rows:
        r["group"] = "75th Anniversary Team" if r["group"] == "75" else "Today's star"
    return {
        "counts": {
            "total": len(rows),
            "team75": sum(r["group"] == "75th Anniversary Team" for r in rows),
            "stars": sum(r["group"] == "Today's star" for r in rows),
            "mvps": sum(r["mvps"] for r in rows),
            "all_star": sum(r["all_star"] for r in rows),
        },
        "stars_rule": rule,
        "greats": rows,
        "_source": make_source(
            ["greats", "nba75_team", "player_id_map"],
            "Basketball-Reference via Kaggle; NBA 75th Anniversary Team; Wikipedia (sourced trivia); NBA photos",
        ),
    }


@router.get("/greats")
def get_greats():
    """Greats of the Game: the NBA 75th Anniversary Team plus today's stars
    (rule in stars_rule), with NBA/BAA career numbers, data-derived facts and
    confirmed trivia (basis 'data' or 'source' with a link)."""
    return _greats()
