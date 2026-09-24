from datetime import date, datetime, timedelta
from typing import Optional
from urllib.request import Request, urlopen
from psycopg2 import pool

from fastapi import APIRouter, HTTPException, Response

from impact_core import (
    BLURRED_PLAYER_MAX_GUESSES,
    _SSL_CONTEXT,
    _blurred_player_resolve,
    _guess_game_public,
)

router = APIRouter()


@router.get("/games/blurred-player/daily")
def get_blurred_player_daily(season: Optional[int] = None):
    resolved_season, _, pool_rows, _ = _blurred_player_resolve(season, None)
    return {
        "date": date.today().isoformat(),
        "season": resolved_season,
        "max_guesses": BLURRED_PLAYER_MAX_GUESSES,
        "pool_size": len(pool_rows),
        "pool": [
            {"player_id": r["player_id"], "player_name": r["player_name"], "team_abbreviation": r["team_abbreviation"]}
            for r in pool_rows
        ],
    }

@router.get("/games/blurred-player/image")
def get_blurred_player_image(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, _, mystery = _blurred_player_resolve(season, puzzle_date)
    image_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{mystery['player_id']}.png"
    try:
        req = Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10, context=_SSL_CONTEXT) as resp:
            data = resp.read()
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load today's player image.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

@router.get("/games/blurred-player/guess")
def guess_blurred_player(guess_player_name: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, pool_rows, mystery = _blurred_player_resolve(season, puzzle_date)

    pool_by_name = {r["player_name"].lower(): r for r in pool_rows}
    guess = pool_by_name.get(guess_player_name.strip().lower())
    if guess is None:
        raise HTTPException(status_code=404, detail=f"{guess_player_name} isn't in today's guessable pool.")

    correct = guess["player_id"] == mystery["player_id"]
    result = {"correct": correct, "guess_player_name": guess["player_name"]}
    if correct:
        result["mystery_player"] = _guess_game_public(mystery)
    return result

@router.get("/games/blurred-player/reveal")
def reveal_blurred_player(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, _, mystery = _blurred_player_resolve(season, puzzle_date)
    return _guess_game_public(mystery)
