from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import (
    column_exists,
    fetch_nba_api_player_leaders,
    get_current_nba_season,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/leaders/{stat_key}")
def get_stat_leaders(stat_key: str, season: Optional[int] = None, top_n: int = 10):
    """
    Top-N leaders for a selected stat in a season (latest season by default).
    """
    stat_map = {
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

    key = (stat_key or "").strip().lower()
    if key not in stat_map:
        raise HTTPException(status_code=400, detail=f"Unsupported stat '{stat_key}'.")

    safe_top_n = max(1, min(int(top_n), 50))

    with get_db() as conn:
        cursor = conn.cursor()
        if season is None:
            season = max(get_latest_season(cursor), get_current_nba_season())

    live_leaders = fetch_nba_api_player_leaders(key, season, safe_top_n)
    if live_leaders and live_leaders.get("results"):
        return live_leaders

    with get_db() as conn:
        cursor = conn.cursor()

        selected_col = None
        for candidate_col in stat_map[key]["columns"]:
            if column_exists(cursor, "player_season_stats", candidate_col):
                selected_col = candidate_col
                break

        if selected_col is None:
            raise HTTPException(
                status_code=400,
                detail=f"Stat '{key}' is not available in this database.",
            )

        cursor.execute(
            f"""
            SELECT player_id, player_name, team_abbreviation, {selected_col}
            FROM player_season_stats
            WHERE season = %s
              AND {selected_col} IS NOT NULL
              AND team_abbreviation IS NOT NULL
              AND team_abbreviation <> 'TOT'
            ORDER BY {selected_col} DESC
            LIMIT %s;
            """,
            (season, safe_top_n),
        )
        rows = cursor.fetchall()

    def to_display_value(raw_value):
        if raw_value is None:
            return None
        value = float(raw_value)
        if stat_map[key]["is_pct"] and value <= 1:
            value *= 100
        return round(value, 2)

    return {
        "season": int(season),
        "stat_key": key,
        "stat_label": stat_map[key]["label"],
        "results": [
            {
                "rank": i + 1,
                "player_id": row[0],
                "player_name": row[1],
                "team_abbr": row[2],
                "value": to_display_value(row[3]),
            }
            for i, row in enumerate(rows)
        ],
    }
