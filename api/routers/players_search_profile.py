from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import (
    fetch_nba_api_player_profile,
    fetch_nba_api_player_search,
    find_player,
    get_current_nba_season,
    get_db,
)

router = APIRouter()


@router.get("/players/search")
def search_players_live(q: str, limit: int = 20):
    """
    Live-first player autocomplete (nba_api), DB fallback.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"query": query, "results": []}

    safe_limit = max(1, min(int(limit), 50))
    live = fetch_nba_api_player_search(query, safe_limit)
    if live:
        return {"query": query, "results": live}

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT player_name
            FROM player_season_stats
            WHERE LOWER(player_name) LIKE LOWER(%s)
            ORDER BY player_name ASC
            LIMIT %s;
            """,
            (f"%{query}%", safe_limit),
        )
        rows = cursor.fetchall()

    return {"query": query, "results": [r[0] for r in rows]}

@router.get("/players/profile/{player_name}")
def get_player_profile(player_name: str, season: Optional[int] = None):
    """
    Player profile stats from local DB.
    If season is omitted, returns latest available season for the player.
    """
    if season is None:
        season = get_current_nba_season()

    live_profile = fetch_nba_api_player_profile(player_name, season)
    if live_profile:
        return live_profile

    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT
                player_name, team_abbreviation, season, age, min,
                pts, reb, ast, stl, blk,
                fg_pct, fg3_pct, ft_pct
            FROM player_season_stats
            WHERE player_id = %s AND season = %s
            LIMIT 1;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                """
                SELECT MAX(season)
                FROM player_season_stats
                WHERE player_id = %s;
                """,
                (player_id,),
            )
            fallback_season = cursor.fetchone()[0]
            if fallback_season is not None and int(fallback_season) != int(season):
                cursor.execute(
                    """
                    SELECT
                        player_name, team_abbreviation, season, age, min,
                        pts, reb, ast, stl, blk,
                        fg_pct, fg3_pct, ft_pct
                    FROM player_season_stats
                    WHERE player_id = %s AND season = %s
                    LIMIT 1;
                    """,
                    (player_id, fallback_season),
                )
                row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No profile data for {resolved_name}.",
            )

    return {
        "player_id": int(player_id),
        "player_name": row[0],
        "team_abbr": row[1],
        "season": int(row[2]),
        "age": round(float(row[3]), 1) if row[3] is not None else None,
        "min": round(float(row[4]), 1) if row[4] is not None else None,
        "stats": {
            "ppg": round(float(row[5]), 1) if row[5] is not None else None,
            "rpg": round(float(row[6]), 1) if row[6] is not None else None,
            "apg": round(float(row[7]), 1) if row[7] is not None else None,
            "spg": round(float(row[8]), 1) if row[8] is not None else None,
            "bpg": round(float(row[9]), 1) if row[9] is not None else None,
            "fgPct": round(float(row[10]) * 100, 1) if row[10] is not None and float(row[10]) <= 1 else (round(float(row[10]), 1) if row[10] is not None else None),
            "threePct": round(float(row[11]) * 100, 1) if row[11] is not None and float(row[11]) <= 1 else (round(float(row[11]), 1) if row[11] is not None else None),
            "ftPct": round(float(row[12]) * 100, 1) if row[12] is not None and float(row[12]) <= 1 else (round(float(row[12]), 1) if row[12] is not None else None),
        },
    }
