from datetime import date, datetime, timedelta
from typing import Optional
from psycopg2 import pool

from fastapi import APIRouter, HTTPException

from impact_core import (
    GUESS_GAME_MAX_GUESSES,
    _direction,
    _guess_game_default_season,
    _guess_game_fields_match,
    _guess_game_mystery,
    _guess_game_pool,
    _guess_game_public,
    _parse_puzzle_date,
    _position_label,
    check_season_exists,
    get_db,
)

router = APIRouter()


@router.get("/games/guess-the-player/daily")
def get_guess_the_player_daily(season: Optional[int] = None):
    """Today's puzzle setup: which season's qualified pool is in play and
    the full guessable list (name/team only — never bio/stat fields, so
    the answer can't be read off this response)."""
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    return {
        "date": date.today().isoformat(),
        "season": resolved_season,
        "max_guesses": GUESS_GAME_MAX_GUESSES,
        "pool_size": len(pool_rows),
        "pool": [
            {"player_id": r["player_id"], "player_name": r["player_name"], "team_abbreviation": r["team_abbreviation"]}
            for r in pool_rows
        ],
    }

@router.get("/games/guess-the-player/guess")
def guess_the_player(guess_player_name: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    """Compares one guess against today's mystery player and returns
    per-field feedback, Wordle-style."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    mystery = _guess_game_mystery(pool_rows, resolved_season, resolved_date)

    pool_by_name = {r["player_name"].lower(): r for r in pool_rows}
    guess = pool_by_name.get(guess_player_name.strip().lower())
    if guess is None:
        raise HTTPException(status_code=404, detail=f"{guess_player_name} isn't in today's guessable pool.")

    correct = guess["player_id"] == mystery["player_id"]
    result = {
        "correct": correct,
        "guess": _guess_game_public(guess),
        "feedback": {
            "team": "match" if guess["team_abbreviation"] == mystery["team_abbreviation"] else "no_match",
            "position": "match" if _guess_game_fields_match(_position_label(guess["bpm_position"]), _position_label(mystery["bpm_position"])) else "no_match",
            "archetype": "match" if _guess_game_fields_match(guess["archetype"], mystery["archetype"]) else "no_match",
            "age": _direction(mystery["age"], guess["age"]),
            "pts": _direction(mystery["pts"], guess["pts"]),
            "reb": _direction(mystery["reb"], guess["reb"]),
            "ast": _direction(mystery["ast"], guess["ast"]),
        },
    }
    if correct:
        result["mystery_player"] = result["guess"]
    return result

@router.get("/games/guess-the-player/reveal")
def reveal_guess_the_player(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    """Reveals the mystery player once a player is out of guesses."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    mystery = _guess_game_mystery(pool_rows, resolved_season, resolved_date)
    return _guess_game_public(mystery)
