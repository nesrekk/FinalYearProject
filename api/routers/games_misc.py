from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter

from impact_core import (
    _attach_rest_tags,
    fetch_boxscore,
    fetch_nba_games_by_date,
)

router = APIRouter()


@router.get("/games/by-date")
def get_games_by_date(date: Optional[str] = None):
    """
    Games for a given date (YYYY-MM-DD). Defaults to today. Each team
    object gets a real "rest" field (rest_days, is_b2b, and a
    rest_disadvantage flag when the two teams' real rest days differ)
    computed from the real schedule in team_game_fatigue when available.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    games = fetch_nba_games_by_date(date)
    games = _attach_rest_tags(games, date)
    return {
        "date": date,
        "games": games,
    }

@router.get("/games/boxscore/{game_id}")
def get_game_boxscore(game_id: str):
    """
    Traditional box score for a game.
    """
    return {
        "game_id": game_id,
        "boxscore": fetch_boxscore(game_id),
    }
