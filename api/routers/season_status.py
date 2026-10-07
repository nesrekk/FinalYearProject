"""
The current season and its week (round 9 step 5, 2026-10-07).

GET /meta/season       current_season.status(): which season the app opens on, how far a live season is
                       ("through <date>", games played of the schedule), the early-season reliability warnings.
                       The frontend reads it once at start-up (utils/season.js) and every season picker defaults
                       to `current`.
GET /dashboard/week    the Dashboard's "This week": the seven days of finals ending on the current season's last
                       stored game date (before a season starts, the latest complete season's last week, labelled
                       as such): the results, the biggest upset by the held-out pre-game odds (game_pregame_odds,
                       the Best Games & Upsets page's rule) and the best game by excitement (best_games). Read live,
                       cached five minutes with the status.
"""

from datetime import timedelta
from functools import lru_cache

from fastapi import APIRouter

import current_season
import week_lib
from impact_core import get_db
from source_badge import make_source

router = APIRouter()
WEEK_DAYS = 7


@router.get("/meta/season")
def meta_season():
    return {**current_season.status(),
            "_source": make_source(["game_scores", "luck_schedule_seasons", "ledger_schedule", "stat_stability",
                                    "player_season_stats"], "ESPN scoreboard (daily update) + stored tables")}


@lru_cache(maxsize=4)
def _week(season, through_iso):
    from datetime import date
    through = date.fromisoformat(through_iso)
    start = through - timedelta(days=WEEK_DAYS - 1)
    with get_db() as conn:
        return week_lib.week_games(conn.cursor(), season, start, through)


@router.get("/dashboard/week")
def dashboard_week():
    st = current_season.status()
    live = st["live"]
    season = live["season"] if live else st["latest_complete"]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MAX(game_date) FROM game_scores WHERE season = %s", (season,))
        through = cur.fetchone()[0]
    if through is None:
        body = {"from": None, "through": None, "games": 0, "results": [], "biggest_upset": None, "best_game": None}
    else:
        body = _week(season, through.isoformat())
    note = (f"{st['current_label']} so far." if live else
            f"The last week of {st['latest_complete_label']}'s regular season"
            + (f" ({st['upcoming']['label']} starts {st['upcoming']['first_date']})." if st.get("upcoming") else "."))
    return {"season": season, "label": current_season.season_label(season), "live": bool(live), "note": note,
            "upcoming": st.get("upcoming"), **body,
            "_source": make_source(["game_scores", "game_pregame_odds", "best_games"],
                                   "ESPN finals + held-out pre-game odds + Best Games")}
