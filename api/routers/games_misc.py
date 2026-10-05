from datetime import datetime
from typing import Optional

from fastapi import APIRouter

from source_badge import make_source

import espn_live
from impact_core import (
    _attach_rest_tags,
    game_boxscore,
    games_by_date,
)

router = APIRouter()


@router.get("/games/by-date")
def get_games_by_date(date: Optional[str] = None):
    """
    Games for a US Eastern date (YYYY-MM-DD; defaults to today's). Stored results first (the real
    final scores: regular season from game_scores, play-in and playoffs from postseason_games, 2009-10
    on), everything else from ESPN's scoreboard (today, the future, the preseason), which lists every
    game with its status, scores, records and kind. `status` is "unreachable" with a message when ESPN
    didn't answer within 3 s and nothing is stored for the date (round 8 step 4: before, that looked
    like a day without games). Each team object gets a real "rest" field (rest_days, is_b2b, and a
    rest_disadvantage flag when the two teams' real rest days differ) computed from the real schedule
    in team_game_fatigue when available.
    """
    if date is None:
        date = espn_live.eastern_today().isoformat()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"date": date, "games": [], "source": "none", "status": "bad_date",
                "message": "date must be YYYY-MM-DD.", "_source": make_source([], "none")}
    result = games_by_date(date)
    games = _attach_rest_tags(result["games"], date)
    if result["source"] == "stored":
        source = make_source(["game_scores", "postseason_games", "team_game_fatigue"], "ESPN final scores (stored)")
    else:
        source = make_source(["team_game_fatigue"], "ESPN scoreboard (live)", live=True, as_of=espn_live.utc_now_iso())
    return {
        "date": date,
        "games": games,
        "source": result["source"],
        "status": result["status"],
        "message": result["message"],
        "_source": source,
    }


@router.get("/games/boxscore/{game_id}")
def get_game_boxscore(game_id: str):
    """
    Traditional box score for a game, from ESPN's summary: an ESPN event id (the ids /games/by-date
    returns) or an NBA game id (regular season 2009-10 on, mapped through game_scores). Plus-minus is
    an integer, null for a player who didn't play (round 8 R8-005: it used to read "nan").
    """
    result = game_boxscore(game_id)
    result["_source"] = make_source(
        ["game_scores"] if str(game_id).startswith("00") else [], "ESPN box score (live)",
        live=True, as_of=espn_live.utc_now_iso(),
    )
    return result
