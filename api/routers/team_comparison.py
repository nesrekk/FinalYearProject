from typing import Optional
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import get_db, get_latest_season

router = APIRouter()

# Real per-player team-comparison additions: advanced team-average stats
# (minutes/games-weighted, same methodology as /meta/current's basic stat
# block), a real roster (top players by real points that season), a real
# head-to-head record + recent-meetings list from team_game_fatigue, and
# each team's real last-10-games form — all straight aggregation, nothing
# modeled or projected.


def _advanced_team_stats(cursor, team_abbr: str, season: int) -> Optional[dict]:
    cursor.execute(
        """SELECT
               SUM(tov * gp) / NULLIF(MAX(gp), 0) AS tov,
               SUM(off_rating * gp) / NULLIF(SUM(gp), 0) AS off_rating,
               SUM(def_rating * gp) / NULLIF(SUM(gp), 0) AS def_rating,
               SUM(net_rating * gp) / NULLIF(SUM(gp), 0) AS net_rating
           FROM player_season_stats
           WHERE season = %s AND team_abbreviation = %s AND gp IS NOT NULL AND gp > 0;""",
        (season, team_abbr),
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    tov, off_rating, def_rating, net_rating = row
    return {
        "tov": round(tov, 1) if tov is not None else None,
        "offRating": round(off_rating, 1) if off_rating is not None else None,
        "defRating": round(def_rating, 1) if def_rating is not None else None,
        "netRating": round(net_rating, 1) if net_rating is not None else None,
    }


def _roster(cursor, team_abbr: str, season: int, limit: int = 8) -> list[dict]:
    cursor.execute(
        """SELECT player_id, player_name, gp, min, pts, reb, ast
           FROM player_season_stats
           WHERE season = %s AND team_abbreviation = %s AND gp IS NOT NULL AND gp > 0
           ORDER BY pts DESC
           LIMIT %s;""",
        (season, team_abbr, limit),
    )
    cols = ["player_id", "player_name", "gp", "min", "pts", "reb", "ast"]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _recent_form(cursor, team_abbr: str, n: int = 10) -> Optional[dict]:
    cursor.execute(
        """SELECT game_date, opponent, win, plus_minus
           FROM team_game_fatigue
           WHERE team_abbreviation = %s
           ORDER BY game_date DESC
           LIMIT %s;""",
        (team_abbr, n),
    )
    rows = cursor.fetchall()
    if not rows:
        return None
    wins = sum(1 for r in rows if r[2])
    return {
        "games_considered": len(rows),
        "wins": wins,
        "losses": len(rows) - wins,
        "results": [
            {"date": str(date), "opponent": opp, "win": bool(win), "point_diff": pm}
            for date, opp, win, pm in rows
        ],
    }


def _head_to_head(cursor, team_a: str, team_b: str, limit_recent: int = 5) -> dict:
    cursor.execute(
        """SELECT game_date, win, plus_minus
           FROM team_game_fatigue
           WHERE team_abbreviation = %s AND opponent = %s
           ORDER BY game_date DESC;""",
        (team_a, team_b),
    )
    rows = cursor.fetchall()
    a_wins = sum(1 for r in rows if r[1])
    return {
        "games_played": len(rows),
        "team_a_wins": a_wins,
        "team_b_wins": len(rows) - a_wins,
        "recent_meetings": [
            {"date": str(date), "team_a_won": bool(win), "team_a_point_diff": pm}
            for date, win, pm in rows[:limit_recent]
        ],
    }


@router.get("/teams/compare/{team_a}/{team_b}")
def get_team_comparison(team_a: str, team_b: str, season: Optional[int] = None):
    team_a, team_b = team_a.upper(), team_b.upper()
    with get_db() as conn:
        cursor = conn.cursor()
        if season is None:
            season = get_latest_season(cursor)

        adv_a = _advanced_team_stats(cursor, team_a, season)
        adv_b = _advanced_team_stats(cursor, team_b, season)
        if adv_a is None or adv_b is None:
            raise HTTPException(
                status_code=404,
                detail=f"No real season-{season} data for '{team_a if adv_a is None else team_b}'.",
            )

        roster_a = _roster(cursor, team_a, season)
        roster_b = _roster(cursor, team_b, season)
        form_a = _recent_form(cursor, team_a)
        form_b = _recent_form(cursor, team_b)
        head_to_head = _head_to_head(cursor, team_a, team_b)

    return {
        "season": season,
        "team_a": {"abbreviation": team_a, "advanced_stats": adv_a, "roster": roster_a, "recent_form": form_a},
        "team_b": {"abbreviation": team_b, "advanced_stats": adv_b, "roster": roster_b, "recent_form": form_b},
        "head_to_head": head_to_head,
        "_source": make_source(
            ["player_season_stats", "team_game_fatigue"],
            "nba_api (stats.nba.com)",
        ),
    }
