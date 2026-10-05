from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional
from wpa_lib import seconds_elapsed as wpa_seconds_elapsed
from wpa_lib import win_prob as wpa_win_prob

from fastapi import APIRouter, HTTPException

from impact_core import (
    GUESS_THE_GAME_MAX_GUESSES,
    WPA_MODEL,
    WPA_SCALER,
    _downsample_points,
    _fetch_game_events,
    _guess_the_game_mystery,
    _guess_the_game_pool,
    _parse_puzzle_date,
    _wpa_model_required,
    get_db,
)

router = APIRouter()


@router.get("/games/guess-the-game/daily")
def get_guess_the_game_daily(puzzle_date: Optional[str] = None):
    """Today's puzzle: a real completed game's downsampled real win-
    probability curve, with no team names or date — just the shape of how
    the game actually unfolded."""
    _wpa_model_required()
    return _daily(_parse_puzzle_date(puzzle_date))


@lru_cache(maxsize=16)
def _daily(resolved_date):
    """One date's puzzle, kept per process (round 8 step 8: ~470 win-probability calls a game, ~0.4 s, for an
    answer that depends only on the date and the stored play-by-play; restart impact_api after a rebuild)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.pbp_games');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="No play-by-play data yet — run scripts/fetch_play_by_play.py first.",
            )
        pool_rows = _guess_the_game_pool(cursor)
        if not pool_rows:
            raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

        mystery = _guess_the_game_mystery(pool_rows, resolved_date)
        events = _fetch_game_events(cursor, mystery["game_id"])

    points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for _event_id, _action_number, period, secs, score_home, score_away, *_rest in events:
        margin = score_home - score_away
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        points.append({
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "home_wp": round(wp_home, 4),
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    return {
        "puzzle_date": resolved_date.isoformat(),
        "max_guesses": GUESS_THE_GAME_MAX_GUESSES,
        "points": _downsample_points(points, 100),
        "methodology": (
            "The real win-probability curve (home team's perspective, from the real trained WPA model) "
            "for one real completed game, downsampled to about 100 points. Team names and the date are "
            "withheld until you guess or run out of guesses."
        ),
    }

@router.get("/games/guess-the-game/guess")
def guess_the_game(team: str, attempt_number: int, puzzle_date: Optional[str] = None):
    """One guess = one real team abbreviation. Each wrong guess reveals the
    next clue in a fixed order (season, then final margin, then one of the
    two real teams) — a correct guess ends the puzzle immediately."""
    attempt_number = max(1, min(attempt_number, GUESS_THE_GAME_MAX_GUESSES))
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        pool_rows = _guess_the_game_pool(cursor)
    if not pool_rows:
        raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

    mystery = _guess_the_game_mystery(pool_rows, resolved_date)
    guess_abbr = team.strip().upper()
    correct = guess_abbr in (mystery["home_team"], mystery["away_team"])
    guesses_remaining = GUESS_THE_GAME_MAX_GUESSES - attempt_number

    result = {
        "attempt_number": attempt_number,
        "correct": correct,
        "guesses_remaining": max(0, guesses_remaining),
    }

    if correct:
        result["mystery_game"] = {
            "game_id": mystery["game_id"],
            "season": mystery["season"],
            "game_date": mystery["game_date"].isoformat() if mystery["game_date"] else None,
            "home_team": mystery["home_team"],
            "away_team": mystery["away_team"],
            "final_score": {"home": mystery["score_home"], "away": mystery["score_away"]},
        }
        return result

    if attempt_number == 1:
        result["clue"] = {"type": "season", "value": mystery["season"]}
    elif attempt_number == 2:
        result["clue"] = {"type": "final_margin", "value": abs(mystery["score_home"] - mystery["score_away"])}
    else:
        result["clue"] = {"type": "one_team", "value": mystery["home_team"]}

    return result

@router.get("/games/guess-the-game/reveal")
def reveal_guess_the_game(puzzle_date: Optional[str] = None):
    """Full reveal once a player is out of guesses."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        pool_rows = _guess_the_game_pool(cursor)
    if not pool_rows:
        raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

    mystery = _guess_the_game_mystery(pool_rows, resolved_date)
    return {
        "game_id": mystery["game_id"],
        "season": mystery["season"],
        "game_date": mystery["game_date"].isoformat() if mystery["game_date"] else None,
        "home_team": mystery["home_team"],
        "away_team": mystery["away_team"],
        "final_score": {"home": mystery["score_home"], "away": mystery["score_away"]},
    }
