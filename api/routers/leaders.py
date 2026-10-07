import math
from typing import Optional

import current_season
from fastapi import APIRouter, HTTPException

from routers.leaderboard import ATTEMPT_DEFAULTS, DEFAULT_MIN_GP, DEFAULT_MIN_MPG, STATS as LB_STATS
from season_team import season_team_sql
from source_badge import make_source

from impact_core import (
    column_exists,
    fetch_nba_api_player_leaders,
    get_current_nba_season,
    get_db,
    get_latest_season,
)

router = APIRouter()

STAT_MAP = {
    "pts": {"columns": ["pts"], "label": "PTS", "is_pct": False},
    "reb": {"columns": ["reb"], "label": "REB", "is_pct": False},
    "ast": {"columns": ["ast"], "label": "AST", "is_pct": False},
    "dreb": {"columns": ["dreb"], "label": "DREB", "is_pct": False},
    "oreb": {"columns": ["oreb"], "label": "OREB", "is_pct": False},
    "plus_minus": {"columns": ["plus_minus"], "label": "+/-", "is_pct": False},
    "stl": {"columns": ["stl"], "label": "STL", "is_pct": False},
    "blk": {"columns": ["blk"], "label": "BLK", "is_pct": False},
    "tov": {"columns": ["tov"], "label": "TOV", "is_pct": False},
    "fg_pct": {"columns": ["fg_pct"], "label": "FG%", "is_pct": True},
    "fg3_pct": {"columns": ["fg3_pct", "three_pct"], "label": "3P%", "is_pct": True},
    "ft_pct": {"columns": ["ft_pct"], "label": "FT%", "is_pct": True},
    "fg3m": {"columns": ["fg3m", "fg3"], "label": "3PM", "is_pct": False},
}


# In a season in progress nobody has 30 games for the first two months: the games floor is then this share of
# the most games anyone has played so far (capped at the full floor).
LIVE_GAMES_SHARE = 0.7


def qualifying(key: str, max_gp: Optional[float] = None) -> dict:
    """The floors a leader must clear (round 8 R8-068: shooting percentages were ranked with no attempts floor,
    3P% 2025-26 opened on 1-for-1 shooters, and per-game leaders on 1-3 game players). They are the Leaderboard
    Builder's defaults, so Stat Leaders' top 10 is that page's: 30+ games, 20+ minutes a game, and for a shooting
    percentage the catalogue's attempts a game (5 FGA, 2 3PA, 2 FTA). max_gp = the most games anyone has played,
    for a season in progress."""
    attempts = LB_STATS[key][5] if key in LB_STATS else None
    min_gp = DEFAULT_MIN_GP if max_gp is None else min(DEFAULT_MIN_GP, math.ceil(LIVE_GAMES_SHARE * max_gp))
    f = {"min_gp": int(min_gp), "min_mpg": DEFAULT_MIN_MPG, "attempts": attempts,
         "min_attempts": ATTEMPT_DEFAULTS.get(attempts) if attempts else None}
    words = {"fga": "FGA", "fg3a": "3PA", "fta": "FTA"}
    f["text"] = (f"Qualified: {f['min_gp']}+ games, {f['min_mpg']:g}+ minutes a game"
                 + (f", {f['min_attempts']:g}+ {words[attempts]} a game" if attempts else "")
                 + " (the Leaderboard Builder's default floor).")
    return f


def live_leaders(key: str, season: int, top_n: int):
    """The season in progress from stats.nba.com, with the qualifying floors applied; None if it doesn't answer
    or nobody qualifies yet."""
    full = fetch_nba_api_player_leaders(key, season, None)
    if not full or not full.get("results"):
        return None
    rows = full["results"]
    max_gp = max((r.get("gp") or 0) for r in rows)
    f = qualifying(key, max_gp)

    def ok(r):
        att = r.get(f["attempts"]) if f["attempts"] else None
        return ((r.get("gp") or 0) >= f["min_gp"] and (r.get("min") or 0) >= f["min_mpg"]
                and (not f["attempts"] or (att or 0) >= f["min_attempts"]))
    kept = [r for r in rows if ok(r)][:top_n]
    if not kept:
        return None
    keys = ("player_id", "player_name", "team_abbr", "value")
    return {**full, "results": [{"rank": i + 1, **{k: r[k] for k in keys}} for i, r in enumerate(kept)],
            "qualifying": f}


def stored_floor(cursor, key: str, season: int) -> dict:
    """qualifying() for a stored season: the season being played (current_season's live season) scales the games
    floor to the most games anyone has played so far, as the live path does (round 9 step 5)."""
    live = current_season.status(cursor)["live"]
    if live and live["season"] == int(season):
        cursor.execute("SELECT MAX(gp) FROM player_season_stats WHERE season = %s", (season,))
        return qualifying(key, cursor.fetchone()[0] or 0)
    return qualifying(key)


def _stored_leaders(cursor, key: str, season: int, top_n: int):
    selected_col = None
    for candidate_col in STAT_MAP[key]["columns"]:
        if column_exists(cursor, "player_season_stats", candidate_col):
            selected_col = candidate_col
            break
    if selected_col is None:
        raise HTTPException(status_code=400, detail=f"Stat '{key}' is not available in this database.")
    f = stored_floor(cursor, key, season)
    att_sql = f"AND {f['attempts']} >= %(att)s" if f["attempts"] else ""
    cursor.execute(
        f"""
        SELECT player_id, player_name, {season_team_sql(cursor)} AS team_abbreviation, {selected_col}
        FROM player_season_stats
        WHERE season = %(season)s
          AND {selected_col} IS NOT NULL
          AND team_abbreviation IS NOT NULL
          AND team_abbreviation <> 'TOT'
          AND gp >= %(gp)s AND min >= %(mpg)s {att_sql}
        ORDER BY {selected_col} DESC, gp DESC, player_name
        LIMIT %(n)s;
        """,
        {"season": season, "gp": f["min_gp"], "mpg": f["min_mpg"], "att": f["min_attempts"], "n": top_n},
    )
    rows = cursor.fetchall()

    def to_display_value(raw_value):
        if raw_value is None:
            return None
        value = float(raw_value)
        if STAT_MAP[key]["is_pct"] and value <= 1:
            value *= 100
        return round(value, 2)

    return [
        {"rank": i + 1, "player_id": row[0], "player_name": row[1], "team_abbr": row[2], "value": to_display_value(row[3])}
        for i, row in enumerate(rows)
    ]


@router.get("/leaders/{stat_key}")
def get_stat_leaders(stat_key: str, season: Optional[int] = None, top_n: int = 10):
    """
    Top-N leaders for a stat. A stored season (player_season_stats) is read from the table; only a
    season newer than the stored ones (the one in progress) is fetched live from stats.nba.com, with a
    3 s timeout. Without `season`: the season in progress when it has games, else the latest stored
    season, and the answer says so (`requested_season`, `fallback`, `note`). Round 8 R8-001: the page
    used to show an empty "Top 10 · 2027" from the first day of the new league year until the first
    game, and again whenever stats.nba.com failed.
    """
    key = (stat_key or "").strip().lower()
    if key not in STAT_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported stat '{stat_key}'.")
    safe_top_n = max(1, min(int(top_n), 50))

    with get_db() as conn:
        cursor = conn.cursor()
        latest_stored = get_latest_season(cursor)
        requested = season if season is not None else max(latest_stored, get_current_nba_season())

        live = None
        if requested > latest_stored:
            live = live_leaders(key, requested, safe_top_n)
        if live and live.get("results"):
            return {
                **live, "requested_season": requested, "fallback": False, "note": None,
                "_source": make_source([], "nba_api (stats.nba.com, live)", live=True),
            }

        if requested > latest_stored:
            if season is not None:
                raise HTTPException(
                    status_code=404,
                    detail=(f"No stored leaders for {requested - 1}-{str(requested)[-2:]} and stats.nba.com has none "
                            f"yet (or didn't answer within 3 s); the latest stored season is "
                            f"{latest_stored - 1}-{str(latest_stored)[-2:]}."),
                )
            shown = latest_stored
            note = (f"{requested - 1}-{str(requested)[-2:]} has no leaders yet (the season hasn't started, or "
                    f"stats.nba.com didn't answer within 3 s); showing {shown - 1}-{str(shown)[-2:]}.")
        else:
            shown = requested
            note = None
        results = _stored_leaders(cursor, key, shown, safe_top_n)
        floor = stored_floor(cursor, key, shown)

    return {
        "season": int(shown),
        "requested_season": int(requested),
        "fallback": shown != requested,
        "note": note,
        "stat_key": key,
        "stat_label": STAT_MAP[key]["label"],
        "qualifying": floor,
        "results": results,
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) season tables, stored"),
    }
