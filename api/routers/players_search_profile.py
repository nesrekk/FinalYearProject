from typing import Optional

from fastapi import APIRouter, HTTPException

from season_team import shown_team

from impact_core import (
    _normalize_search_text,
    resolve_player,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/players/search")
def search_players_live(q: str, limit: int = 20):
    """
    Player-name autocomplete from the stored seasons (player_season_stats), most recent players
    first. Until round 8 step 4 it asked stats.nba.com for the current season's player list first
    (up to 6 s on every keystroke, and names the rest of the app couldn't resolve anyway: every
    tool that takes a name looks it up in the same table).
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"query": query, "results": []}

    safe_limit = max(1, min(int(limit), 50))
    needle = _normalize_search_text(query)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT player_name, MAX(season) AS last_season
            FROM player_season_stats
            WHERE LOWER(player_name) LIKE LOWER(%s)
            GROUP BY player_name
            ORDER BY last_season DESC, player_name ASC
            LIMIT %s;
            """,
            (f"%{query}%", safe_limit * 3),
        )
        rows = cursor.fetchall()
        if len(rows) < safe_limit:
            # Accent-insensitive pass (the DB is on the C locale, so LOWER() only folds ASCII and
            # "jokic" doesn't match "Jokić" above): scan the distinct names once.
            cursor.execute(
                "SELECT player_name, MAX(season) FROM player_season_stats GROUP BY player_name;"
            )
            seen = {r[0] for r in rows}
            extra = [r for r in cursor.fetchall() if r[0] not in seen and needle in _normalize_search_text(r[0])]
            rows = rows + sorted(extra, key=lambda r: (-r[1], r[0]))
    return {"query": query, "results": [r[0] for r in rows[:safe_limit]]}

@router.get("/players/profile/{player_name}")
def get_player_profile(player_name: str, season: Optional[int] = None, player_id: Optional[int] = None):
    """
    Player profile stats from the local DB (no page calls this; round 8 R8-017). If season is
    omitted, the player's latest stored season. It used to ask stats.nba.com first with a 45 s
    timeout (round 8 step 4).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = resolve_player(cursor, player_name, player_id)
        if season is None:
            season = get_latest_season(cursor)

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
        team = shown_team(cursor, player_id, row[2], row[1])

    def pct(v):
        if v is None:
            return None
        v = float(v)
        return round(v * 100, 1) if v <= 1 else round(v, 1)

    return {
        "player_id": int(player_id),
        "player_name": row[0],
        "team_abbr": team,
        "season": int(row[2]),
        "age": round(float(row[3]), 1) if row[3] is not None else None,
        "min": round(float(row[4]), 1) if row[4] is not None else None,
        "stats": {
            "ppg": round(float(row[5]), 1) if row[5] is not None else None,
            "rpg": round(float(row[6]), 1) if row[6] is not None else None,
            "apg": round(float(row[7]), 1) if row[7] is not None else None,
            "spg": round(float(row[8]), 1) if row[8] is not None else None,
            "bpg": round(float(row[9]), 1) if row[9] is not None else None,
            "fgPct": pct(row[10]),
            "threePct": pct(row[11]),
            "ftPct": pct(row[12]),
        },
    }
