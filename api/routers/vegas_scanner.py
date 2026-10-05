from source_badge import make_source

from fastapi import APIRouter

from impact_core import (
    TEAM_NAME_TO_ABBR,
    standings_win_pct,
    get_championship_odds_cached,
    get_db,
    get_current_nba_season,
    get_latest_season,
)

router = APIRouter()


@router.get("/odds/championship")
def get_championship_odds_scanner():
    """
    Real live championship-winner odds (Shin's-method devigged) vs. a
    naive real-win-percentage proxy, sorted by the size of the gap between
    them. See module docstring above for what this is and isn't.
    """
    odds_data = get_championship_odds_cached()

    with get_db() as conn:
        cursor = conn.cursor()
        season = max(get_latest_season(cursor), get_current_nba_season())

    # ESPN's standings for the season in progress (stored team_seasons when ESPN doesn't answer);
    # before opening night every team is 0-0, so the latest season with a decision is used and
    # named (round 8 step 4; this used to come from stats.nba.com).
    win_pct_by_abbr, win_pct_season, win_pct_source = standings_win_pct(season)

    total_win_pct = sum(win_pct_by_abbr.values()) or 1.0
    proxy_prob_by_abbr = {abbr: wp / total_win_pct for abbr, wp in win_pct_by_abbr.items()}

    rows = []
    for team_name, market_prob in odds_data["team_market_probability"].items():
        abbr = TEAM_NAME_TO_ABBR.get(team_name)
        proxy_prob = proxy_prob_by_abbr.get(abbr) if abbr else None
        spread = odds_data["team_probability_spread"].get(team_name)
        rows.append({
            "team_name": team_name,
            "team_abbreviation": abbr,
            "market_probability": market_prob,
            "probability_spread": spread,
            "best_odds": odds_data["team_best_odds"].get(team_name),
            "worst_odds": odds_data["team_worst_odds"].get(team_name),
            "books": odds_data["team_books"].get(team_name, []),
            "proxy_probability": round(proxy_prob, 4) if proxy_prob is not None else None,
            "value": round(market_prob - proxy_prob, 4) if proxy_prob is not None else None,
            "win_pct": round(win_pct_by_abbr.get(abbr), 3) if abbr in win_pct_by_abbr else None,
        })

    rows.sort(key=lambda r: abs(r["value"]) if r["value"] is not None else -1, reverse=True)

    return {
        "season": season,
        "win_pct_season": win_pct_season,
        "win_pct_source": win_pct_source,
        "last_update": odds_data["last_update"],
        "books_used": odds_data["books_used"],
        "avg_z": odds_data["avg_z"],
        "methodology": (
            "market_probability is Shin's-method devigged, averaged across all real bookmakers "
            "in the live response. proxy_probability is each team's real win percentage this "
            "season, normalized to sum to 1 across the teams with live odds — a naive proxy for "
            "'who is actually good right now', NOT a trained championship-probability model. "
            "value = market_probability - proxy_probability; a large positive value means the "
            "market is pricing this team higher than its real season win rate alone would "
            "suggest, a large negative value the opposite. This is a starting point for a "
            "real-data comparison, not a betting recommendation."
        ),
        "teams": rows,
        "_source": make_source(
            ["team_seasons"] if win_pct_source == "stored" else [],
            "The Odds API (live) + ESPN standings" + (" (live)" if win_pct_source == "espn" else " (stored)"),
            as_of=odds_data["last_update"], live=True,
        ),
    }
