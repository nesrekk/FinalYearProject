from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter

from impact_core import (
    fetch_current_news,
)

router = APIRouter()


@router.get("/news/current")
def get_current_news(date: Optional[str] = None, limit: int = 20, team: Optional[str] = None):
    """
    Current-day NBA news from public RSS feeds (real headlines, no invented
    summaries). `team` is a real full team name (e.g. "Los Angeles Lakers")
    that adds one extra real team-scoped Google News feed to the results.
    """
    safe_limit = max(1, min(int(limit), 50))
    items = fetch_current_news(date, safe_limit, team=team)
    return {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "items": items,
    }
