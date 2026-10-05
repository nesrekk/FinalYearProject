
from fastapi import APIRouter, HTTPException

from impact_core import (
    _best_fit_teammate,
    _fetch_roster,
    _swap_roster,
    _team_summary,
    get_db,
)
from source_badge import make_source

router = APIRouter()


@router.get("/trade/teams/{season}")
def get_trade_teams(season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT team_abbreviation FROM player_season_stats "
            "WHERE season = %s AND team_abbreviation IS NOT NULL ORDER BY 1;",
            (season,),
        )
        teams = [r[0] for r in cursor.fetchall()]
    if not teams:
        raise HTTPException(status_code=404, detail=f"No team data for season {season}.")
    return {"season": season, "teams": teams,
            "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference")}

@router.get("/trade/roster/{team_abbr}/{season}")
def get_trade_roster(team_abbr: str, season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        roster = _fetch_roster(cursor, team_abbr, season)
    if not roster:
        raise HTTPException(
            status_code=404,
            detail=f"No roster found for {team_abbr.upper()} in season {season}.",
        )
    return {"team": team_abbr.upper(), "season": season, "roster": roster,
            "_source": make_source(["player_season_stats", "player_clusters"], "nba_api (stats.nba.com) + Basketball-Reference")}

@router.get("/trade/simulate")
def simulate_trade(season: int, team_a: str, player_a_id: int, team_b: str, player_b_id: int):
    if player_a_id == player_b_id:
        raise HTTPException(status_code=400, detail="Can't trade a player for themselves.")

    with get_db() as conn:
        cursor = conn.cursor()
        roster_a = _fetch_roster(cursor, team_a, season)
        roster_b = _fetch_roster(cursor, team_b, season)

        player_a = next((r for r in roster_a if r["player_id"] == player_a_id), None)
        player_b = next((r for r in roster_b if r["player_id"] == player_b_id), None)
        if not player_a:
            raise HTTPException(status_code=404, detail=f"Player {player_a_id} not found on {team_a.upper()} in {season}.")
        if not player_b:
            raise HTTPException(status_code=404, detail=f"Player {player_b_id} not found on {team_b.upper()} in {season}.")

        fit_a_on_b = _best_fit_teammate(cursor, player_a_id, season, [r for r in roster_b if r["player_id"] != player_b_id])
        fit_b_on_a = _best_fit_teammate(cursor, player_b_id, season, [r for r in roster_a if r["player_id"] != player_a_id])

    roster_a_after = _swap_roster(roster_a, player_a_id, player_b)
    roster_b_after = _swap_roster(roster_b, player_b_id, player_a)

    return {
        "season": season,
        "trade": {
            "team_a": {"team": team_a.upper(), "sends": player_a, "receives": player_b, "fit_note": fit_b_on_a},
            "team_b": {"team": team_b.upper(), "sends": player_b, "receives": player_a, "fit_note": fit_a_on_b},
        },
        "team_a_summary": {"before": _team_summary(roster_a), "after": _team_summary(roster_a_after)},
        "team_b_summary": {"before": _team_summary(roster_b), "after": _team_summary(roster_b_after)},
        "caveat": (
            "This treats each player's own historical stats as their contribution to the new team — "
            "a real trade changes role, usage, and minutes, which this can't simulate. Read it as a "
            "roster production balance estimate, not an on-court projection."
        ),
        "predicted_win_pct_note": (
            "predicted_win_pct comes from a Linear Regression model (net rating + true-shooting %, "
            "leave-one-season-out validated: R2=0.92, average error ~2.5 wins over an 82-game season) — "
            "an estimate, not a guarantee."
        ),
        "_source": make_source(["player_season_stats", "player_clusters", "season_similarity"],
                               "nba_api (stats.nba.com) + Basketball-Reference; win model scripts/train_win_model.py"),
    }
