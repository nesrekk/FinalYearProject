from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import (
    _parse_puzzle_date,
    _trivia_build_all,
    _trivia_resolve_pool,
)

router = APIRouter()


@router.get("/games/trivia/daily")
def get_trivia_daily(season: Optional[int] = None):
    resolved_season, pool_rows = _trivia_resolve_pool(season)
    resolved_date = date.today()
    results = _trivia_build_all(pool_rows, resolved_season, resolved_date)
    if not results:
        raise HTTPException(status_code=404, detail="Could not build today's trivia questions.")
    return {
        "date": resolved_date.isoformat(),
        "season": resolved_season,
        "questions": [q for q, _ in results],
    }

@router.get("/games/trivia/guess")
def guess_trivia(question_id: str, option_id: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    resolved_date = _parse_puzzle_date(puzzle_date)
    resolved_season, pool_rows = _trivia_resolve_pool(season)
    results = _trivia_build_all(pool_rows, resolved_season, resolved_date)
    match = next((correct_id for q, correct_id in results if q["id"] == question_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown question {question_id}.")
    return {"correct": option_id == match, "correct_option_id": match}
