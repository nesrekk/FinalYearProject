from typing import Optional
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import get_db, get_latest_season

router = APIRouter()

# Team-comparison additions: the team's ratings and turnovers (team_seasons
# and NBA.com's team box score, the team page's numbers), a real roster (top
# players by real points that season), a real head-to-head record +
# recent-meetings list from team_game_fatigue, and each team's real
# last-10-games form — all straight aggregation, nothing modeled or
# projected. Point differentials are real final scores from game_scores, not
# team_game_fatigue.plus_minus (summed player +/- / 5, wrong in 160 games).


def _advanced_team_stats(cursor, team_abbr: str, season: int) -> Optional[dict]:
    """The team's ratings and turnovers for the season, the numbers the team page shows (round 8
    R8-063). Ratings: Basketball-Reference's team offensive, defensive and net rating per 100
    possessions (team_seasons). Before, this was the games-weighted mean of the players' own
    on-court ratings, 0.9 points from the team's net rating on average and up to 4.0 (and it
    counted a traded player's whole season under his last team). Turnovers per game: NBA.com's
    team box score (game_team_box, from 2020-21, team turnovers included); earlier seasons sum
    the players' season rows over the team's games."""
    cursor.execute(
        """SELECT o_rtg, d_rtg, n_rtg, g FROM team_seasons
           WHERE season = %s AND abbreviation = %s AND NOT is_league_avg;""",
        (season, team_abbr),
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    off_rating, def_rating, net_rating, games = row
    cursor.execute(
        "SELECT SUM(tov)::float / COUNT(*) FROM game_team_box WHERE season = %s AND team_abbreviation = %s;",
        (season, team_abbr),
    )
    tov = cursor.fetchone()[0]
    tov_source = "game_team_box"
    if tov is None:
        cursor.execute(
            """SELECT SUM(tov * gp) FROM player_season_stats
               WHERE season = %s AND team_abbreviation = %s AND gp IS NOT NULL AND gp > 0;""",
            (season, team_abbr),
        )
        total = cursor.fetchone()[0]
        tov = float(total) / games if total is not None and games else None
        tov_source = "player_season_stats"
    return {
        "tov": round(float(tov), 1) if tov is not None else None,
        "tovSource": tov_source,
        "offRating": round(float(off_rating), 1),
        "defRating": round(float(def_rating), 1) if def_rating is not None else None,
        "netRating": round(float(net_rating), 1) if net_rating is not None else None,
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
        """SELECT f.game_date, f.opponent, f.win, g.pts_for - g.pts_against
           FROM team_game_fatigue f
           LEFT JOIN game_scores g ON g.game_id = f.game_id AND g.team_abbreviation = f.team_abbreviation
           WHERE f.team_abbreviation = %s
           ORDER BY f.game_date DESC
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
        """SELECT f.game_date, f.win, g.pts_for - g.pts_against
           FROM team_game_fatigue f
           LEFT JOIN game_scores g ON g.game_id = f.game_id AND g.team_abbreviation = f.team_abbreviation
           WHERE f.team_abbreviation = %s AND f.opponent = %s
           ORDER BY f.game_date DESC;""",
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
            ["team_seasons", "game_team_box", "player_season_stats", "team_game_fatigue", "game_scores"],
            "Basketball-Reference team ratings, nba_api (stats.nba.com) box scores, ESPN scoreboard final scores",
        ),
    }
