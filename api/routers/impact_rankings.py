from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    check_season_exists,
    get_db,
)

router = APIRouter()


@router.get("/impact/bpm/{season}")
def get_bpm_leaderboard(season: int, top_n: int = 20, min_minutes: float = 20.0, min_games: int = 30):
    """
    Top players by BPM (Box Plus/Minus) for a season — see
    Basketball-Reference's published BPM/OBPM/DBPM/VORP
    (scripts/load_bref_bpm_vorp.py; this project's own reproduction ran
    hot and is kept only in *_repro columns). A modest
    minutes/games floor is applied by default — like every other rate-stat
    leaderboard in this project, unfiltered per-100-possession numbers are
    dominated by small-sample noise from low-minute players.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, team_abbreviation, pts, min, bpm, obpm, dbpm, vorp, player_id
            FROM player_season_stats
            WHERE season = %s AND bpm IS NOT NULL AND min >= %s AND gp >= %s
            ORDER BY bpm DESC
            LIMIT %s;
            """,
            (season, min_minutes, min_games, top_n),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No BPM data for season {season} (min>={min_minutes}, gp>={min_games}). "
                   f"Run scripts/build_bpm_vorp.py if this table hasn't been populated for this season yet.",
        )

    return {
        "season": season,
        "min_minutes": min_minutes,
        "min_games": min_games,
        "results": [
            {
                "rank": i + 1,
                "player_id": int(r[-1]),
                "player_name": r[0],
                "team_abbreviation": r[1],
                "pts": round(float(r[2]), 1),
                "min": round(float(r[3]), 1),
                "bpm": round(float(r[4]), 2),
                "obpm": round(float(r[5]), 2),
                "dbpm": round(float(r[6]), 2),
                "vorp": round(float(r[7]), 2),
            }
            for i, r in enumerate(rows)
        ],
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }

@router.get("/impact/raw/{season}")
def get_raw_impact(season: int, top_n: int = 20, min_minutes: float = 20.0, min_games: int = 30):
    """
    Top players by raw impact score for a given season, with the same default
    minutes/games floor as the BPM leaderboard. Without it the list was topped
    by players with a handful of games (2025-26: Colby Jones, 1 game, 2.0 PPG).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, pts, w_pct, impact_score_raw, player_id
            FROM player_season_stats
            WHERE season = %s AND impact_score_raw IS NOT NULL AND min >= %s AND gp >= %s
            ORDER BY impact_score_raw DESC
            LIMIT %s;
            """,
            (season, min_minutes, min_games, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": season,
        "type": "raw",
        "min_minutes": min_minutes,
        "min_games": min_games,
        "results": [
            {
                "rank": i + 1,
                "player_id": int(r[-1]),
                "player_name": r[0],
                "pts": round(float(r[1]), 1),
                "w_pct": round(float(r[2]), 3),
                "impact_score_raw": round(float(r[3]), 4),
            }
            for i, r in enumerate(rows)
        ],
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }

@router.get("/impact/star/{season}")
def get_star_impact(season: int, top_n: int = 20):
    """Top star-qualified players by star impact score for a given season."""
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, pts, w_pct, impact_score_star, player_id
            FROM player_season_stats
            WHERE season = %s AND impact_score_star IS NOT NULL
            ORDER BY impact_score_star DESC
            LIMIT %s;
            """,
            (season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": season,
        "type": "star",
        "results": [
            {
                "rank": i + 1,
                "player_id": int(r[-1]),
                "player_name": r[0],
                "pts": round(float(r[1]), 1),
                "w_pct": round(float(r[2]), 3),
                "impact_score_star": round(float(r[3]), 4),
            }
            for i, r in enumerate(rows)
        ],
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }
