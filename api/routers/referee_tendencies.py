from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    REFEREE_MIN_GAMES_DEFAULT,
    REFEREE_SMALL_N_THRESHOLD,
    get_db,
)

router = APIRouter()


@router.get("/referees/tendencies")
def get_referee_tendencies(min_games: int = REFEREE_MIN_GAMES_DEFAULT, sort: str = "n_games"):
    min_games = max(1, min_games)
    sort_columns = {
        "n_games": "n_games DESC",
        "fouls_diff_pct": "ABS(fouls_diff_pct) DESC",
        "fta_diff_pct": "ABS(fta_diff_pct) DESC",
        "pace_diff_pct": "ABS(pace_diff_pct) DESC",
        "name": "official_name ASC",
    }
    order_clause = sort_columns.get(sort, sort_columns["n_games"])

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.referee_tendencies');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="No referee data yet — run scripts/fetch_referee_officials.py then scripts/build_referee_tendencies.py.",
            )

        cursor.execute(
            f"""SELECT official_id, official_name, n_games, season_min, season_max,
                       avg_total_fouls, league_avg_fouls, fouls_diff, fouls_diff_pct, fouls_ci_low, fouls_ci_high,
                       avg_total_fta, league_avg_fta, fta_diff, fta_diff_pct, fta_ci_low, fta_ci_high,
                       avg_pace, league_avg_pace, pace_diff_pct, small_n_warning
                FROM referee_tendencies
                WHERE n_games >= %s
                ORDER BY {order_clause};""",
            (min_games,),
        )
        rows = cursor.fetchall()

        cursor.execute("SELECT COUNT(*), MIN(season_min), MAX(season_max), SUM(n_games) FROM referee_tendencies;")
        total_officials, span_min, span_max, total_official_game_slots = cursor.fetchone()

    officials = [
        {
            "official_id": r[0], "official_name": r[1], "n_games": r[2],
            "season_min": r[3], "season_max": r[4],
            "avg_total_fouls": r[5], "league_avg_fouls": r[6], "fouls_diff": r[7], "fouls_diff_pct": r[8],
            "fouls_ci_low": r[9], "fouls_ci_high": r[10],
            "avg_total_fta": r[11], "league_avg_fta": r[12], "fta_diff": r[13], "fta_diff_pct": r[14],
            "fta_ci_low": r[15], "fta_ci_high": r[16],
            "avg_pace": r[17], "league_avg_pace": r[18], "pace_diff_pct": r[19],
            "small_n_warning": r[20],
        }
        for r in rows
    ]

    return {
        "min_games": min_games,
        "sort": sort,
        "officials": officials,
        "season_span": {"min": span_min, "max": span_max},
        "total_officials_tracked": total_officials,
        "methodology": (
            "For each real NBA official, real total fouls called and real free throws attempted in games they "
            "worked (BoxScoreSummaryV2 officials, LeagueGameFinder box stats), averaged and compared against the "
            "real league average for those same real seasons (season-adjusted per game, not one blended baseline). "
            "The percentage shown is that real difference as a share of the real league average. A 95% confidence "
            "interval is shown on the difference; officials below "
            f"{REFEREE_SMALL_N_THRESHOLD} real games worked are flagged as a small "
            "sample, where normal game-to-game variance alone can produce a large-looking difference. This is a "
            "descriptive comparison of real totals only — it does not and cannot account for real confounders like "
            "which teams' games an official was assigned to, crew composition, or era, and is not a claim about "
            "intent or bias."
        ),
        "_source": make_source(
            ["referee_tendencies", "game_officials", "game_team_box"],
            "nba_api (stats.nba.com, BoxScoreSummaryV2 + LeagueGameFinder)",
        ),
    }
