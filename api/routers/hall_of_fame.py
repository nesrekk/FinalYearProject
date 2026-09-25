from typing import Optional
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import get_db

router = APIRouter()

# "Hall of Fame" here is a real all-time-greats leaderboard, not a claim
# about actual Naismith Hall of Fame induction — this project has no real
# induction dataset anywhere. Everything below is real aggregation over
# player_season_stats' full 1950-2026 coverage (the Kaggle historical
# import), nothing modeled or curated.

CAREER_STATS = {
    "pts": "points", "reb": "rebounds", "ast": "assists",
    "stl": "steals", "blk": "blocks",
}

SEASON_STATS = {
    "pts": "pts", "reb": "reb", "ast": "ast", "stl": "stl", "blk": "blk",
    "fg3_pct": "fg3_pct", "ts_pct": "ts_pct",
}


def _load_nba75_ids(cursor) -> set:
    cursor.execute("SELECT to_regclass('public.nba75_team');")
    if cursor.fetchone()[0] is None:
        return set()
    cursor.execute("SELECT player_id FROM nba75_team;")
    return {r[0] for r in cursor.fetchall()}


@router.get("/hof/career-leaders")
def get_career_leaders(stat: str = "pts", limit: int = 50):
    if stat not in CAREER_STATS:
        raise HTTPException(status_code=400, detail=f"stat must be one of {list(CAREER_STATS)}")
    safe_limit = max(1, min(int(limit), 100))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT player_id, MAX(player_name) AS player_name,
                   SUM({stat} * gp) AS career_total,
                   SUM(gp) AS career_gp,
                   COUNT(DISTINCT season) AS seasons_played,
                   MIN(season) AS first_season,
                   MAX(season) AS last_season
            FROM player_season_stats
            WHERE gp IS NOT NULL AND gp > 0 AND {stat} IS NOT NULL
            GROUP BY player_id
            ORDER BY career_total DESC
            LIMIT %s;
            """,
            (safe_limit,),
        )
        rows = cursor.fetchall()
        nba75_ids = _load_nba75_ids(cursor)

    leaders = [
        {
            "rank": i + 1,
            "player_id": r[0],
            "player_name": r[1],
            "career_total": round(r[2]),
            "career_gp": r[3],
            "seasons_played": r[4],
            "first_season": r[5],
            "last_season": r[6],
            "per_game": round(r[2] / r[3], 1) if r[3] else None,
            "is_nba75": r[0] in nba75_ids,
        }
        for i, r in enumerate(rows)
    ]

    return {
        "stat": stat,
        "stat_label": CAREER_STATS[stat],
        "leaders": leaders,
        "_source": make_source(["player_season_stats", "nba75_team"], "nba_api (stats.nba.com) + Kaggle historical (Basketball-Reference)"),
    }


@router.get("/hof/greatest-seasons")
def get_greatest_seasons(stat: str = "pts", limit: int = 50, min_gp: int = 50):
    if stat not in SEASON_STATS:
        raise HTTPException(status_code=400, detail=f"stat must be one of {list(SEASON_STATS)}")
    safe_limit = max(1, min(int(limit), 100))
    safe_min_gp = max(1, min(int(min_gp), 82))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT player_id, player_name, season, age, gp, {stat} AS value
            FROM player_season_stats
            WHERE gp >= %s AND {stat} IS NOT NULL
            ORDER BY value DESC
            LIMIT %s;
            """,
            (safe_min_gp, safe_limit),
        )
        rows = cursor.fetchall()
        nba75_ids = _load_nba75_ids(cursor)

    seasons = [
        {
            "rank": i + 1,
            "player_id": r[0],
            "player_name": r[1],
            "season": r[2],
            "season_label": f"{r[2] - 1}-{str(r[2])[-2:]}",
            "age": r[3],
            "gp": r[4],
            "value": r[5],
            "is_nba75": r[0] in nba75_ids,
        }
        for i, r in enumerate(rows)
    ]

    return {
        "stat": stat,
        "min_gp": safe_min_gp,
        "seasons": seasons,
        "_source": make_source(["player_season_stats", "nba75_team"], "nba_api (stats.nba.com) + Kaggle historical (Basketball-Reference)"),
    }


@router.get("/hof/longevity")
def get_longevity_leaders(limit: int = 50):
    safe_limit = max(1, min(int(limit), 100))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT player_id, MAX(player_name) AS player_name,
                   COUNT(DISTINCT season) AS seasons_played,
                   SUM(gp) AS career_gp,
                   MIN(season) AS first_season,
                   MAX(season) AS last_season
            FROM player_season_stats
            WHERE gp IS NOT NULL AND gp > 0
            GROUP BY player_id
            ORDER BY seasons_played DESC, career_gp DESC
            LIMIT %s;
            """,
            (safe_limit,),
        )
        rows = cursor.fetchall()
        nba75_ids = _load_nba75_ids(cursor)

    leaders = [
        {
            "rank": i + 1,
            "player_id": r[0],
            "player_name": r[1],
            "seasons_played": r[2],
            "career_gp": r[3],
            "first_season": r[4],
            "last_season": r[5],
            "is_nba75": r[0] in nba75_ids,
        }
        for i, r in enumerate(rows)
    ]

    return {
        "leaders": leaders,
        "_source": make_source(["player_season_stats", "nba75_team"], "nba_api (stats.nba.com) + Kaggle historical (Basketball-Reference)"),
    }
