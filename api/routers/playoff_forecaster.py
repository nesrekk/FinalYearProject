from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    PLAYOFF_COMPARISON_STATS,
    _fetch_playoff_stats_season,
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/players/playoff-comparison/{player_name}")
def get_playoff_comparison(player_name: str, season: int):
    """Real regular-season vs. real playoff advanced stats for one player-
    season, side by side. 404s honestly if the player's team didn't make
    the playoffs that season, or the player didn't appear — that's real
    information too, not something to paper over."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT team_abbreviation, gp, min, pts, ts_pct, usg_pct, net_rating, ast_pct, reb_pct
            FROM player_season_stats
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"No regular-season data for {resolved_name} in season {season}.")

    regular = {
        "team_abbreviation": row[0], "gp": row[1], "min": round(row[2], 1) if row[2] is not None else None,
        "pts": round(row[3], 1) if row[3] is not None else None,
        "ts_pct": row[4], "usg_pct": row[5], "net_rating": row[6], "ast_pct": row[7], "reb_pct": row[8],
    }

    playoff_by_name = _fetch_playoff_stats_season(season)
    playoff = playoff_by_name.get(resolved_name.lower())

    if not playoff:
        return {
            "player_id": player_id,
            "player_name": resolved_name,
            "season": season,
            "regular_season": regular,
            "playoffs": None,
            "note": f"{resolved_name}'s team did not make the playoffs in season {season}, "
                    f"or they did not appear in a playoff game — no real playoff data exists for this comparison.",
            "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)", live=True),
        }

    deltas = {}
    for key, _ in PLAYOFF_COMPARISON_STATS:
        r_val, p_val = regular.get(key), playoff.get(key)
        deltas[key] = round(p_val - r_val, 4) if r_val is not None and p_val is not None else None

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "regular_season": regular,
        "playoffs": playoff,
        "deltas": deltas,
        "small_sample_warning": playoff["gp"] < 10,
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)", live=True),
    }
