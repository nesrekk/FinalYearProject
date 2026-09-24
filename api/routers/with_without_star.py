from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    TEAM_ABBR_TO_ID,
    _fetch_player_game_ids,
    _fetch_team_game_log,
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/teams/with-without/{team_abbr}/{season}")
def get_with_without_star(team_abbr: str, season: int, player_name: str):
    team_abbr = team_abbr.upper()
    team_id = TEAM_ABBR_TO_ID.get(team_abbr)
    if team_id is None:
        raise HTTPException(status_code=400, detail=f"Unknown team abbreviation '{team_abbr}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    team_games = _fetch_team_game_log(team_id, season)
    if not team_games:
        raise HTTPException(status_code=404, detail=f"No real games found for {team_abbr} in season {season}.")

    played_game_ids = _fetch_player_game_ids(player_id, season, team_id)

    with_games = [g for g in team_games if g["game_id"] in played_game_ids]
    without_games = [g for g in team_games if g["game_id"] not in played_game_ids]

    def summarize(games):
        n = len(games)
        if n == 0:
            return {"n": 0, "wins": 0, "losses": 0, "win_pct": None, "avg_point_diff": None}
        wins = sum(1 for g in games if g["wl"] == "W")
        losses = n - wins
        diffs = [g["plus_minus"] for g in games if g["plus_minus"] is not None]
        avg_diff = sum(diffs) / len(diffs) if diffs else None
        return {
            "n": n,
            "wins": wins,
            "losses": losses,
            "win_pct": round(wins / n, 3),
            "avg_point_diff": round(avg_diff, 2) if avg_diff is not None else None,
        }

    return {
        "team_abbreviation": team_abbr,
        "season": season,
        "player_id": player_id,
        "player_name": resolved_name,
        "with_player": summarize(with_games),
        "without_player": summarize(without_games),
        "methodology": (
            f"Real {team_abbr} team game log and real {resolved_name} game log for season {season}, both "
            "live-fetched from the NBA's own real per-game data, not a model. This is an association, not a "
            "causal claim: other players being in or out of the lineup for the same real games also affects "
            "the real result, and this comparison doesn't control for that. Real sample sizes for both splits "
            "are always shown — draw conclusions cautiously from a small 'without' sample, which is common for "
            "a player who rarely sits."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com, live game logs)", live=True),
    }
