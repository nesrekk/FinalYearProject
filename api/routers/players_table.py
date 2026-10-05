
from fastapi import APIRouter

from source_badge import make_source

from impact_core import (
    _position_label,
    check_season_exists,
    get_db,
)

router = APIRouter()


@router.get("/players/table/{season}")
def get_players_table(season: int, min_minutes: float = 0.0):
    """
    Every player for a season with traditional, advanced, and plus-minus
    stats in one row — powers a full sortable/filterable player table
    (like Basketball-Reference/CraftedNBA's stat tables), not just a
    single-player lookup.

    "position" here is NOT an official roster position — this project has
    no position data anywhere in its pipeline (confirmed: not in the raw
    nba_api CSVs, not fetchable without a new data source). It's derived
    from BPM's own position-estimation regression (scripts/build_bpm_vorp.py),
    rounded to the nearest of 5 buckets — a real, disclosed estimate, not a
    guess dressed up as fact. Treat it as "plays like a ~PG", not a roster fact.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_id, player_name, team_abbreviation, age, gp, min,
                   pts, reb, ast, stl, blk, tov, fg_pct, fg3_pct, ft_pct,
                   fgm, fga, fg3m, fg3a, ftm, fta, w_pct, plus_minus,
                   ts_pct, usg_pct, off_rating, def_rating, net_rating,
                   ast_pct, reb_pct, efg_pct, oreb_pct, tov_pct,
                   bpm, obpm, dbpm, vorp, bpm_position
            FROM player_season_stats
            WHERE season = %s AND min >= %s
            ORDER BY min DESC;
            """,
            (season, min_minutes),
        )
        rows = cursor.fetchall()

    def r3(v):
        return round(float(v), 3) if v is not None else None

    def r1(v):
        return round(float(v), 1) if v is not None else None

    results = []
    for row in rows:
        (player_id, player_name, team_abbreviation, age, gp, minutes,
         pts, reb, ast, stl, blk, tov, fg_pct, fg3_pct, ft_pct,
         fgm, fga, fg3m, fg3a, ftm, fta, w_pct, plus_minus,
         ts_pct, usg_pct, off_rating, def_rating, net_rating,
         ast_pct, reb_pct, efg_pct, oreb_pct, tov_pct,
         bpm, obpm, dbpm, vorp, bpm_position) = row
        results.append({
            "player_id": int(player_id), "player_name": player_name,
            "team_abbreviation": team_abbreviation, "age": age, "gp": gp,
            "min": r1(minutes), "position": _position_label(bpm_position),
            "traditional": {
                "pts": r1(pts), "reb": r1(reb), "ast": r1(ast), "stl": r1(stl),
                "blk": r1(blk), "tov": r1(tov), "fgm": r1(fgm), "fga": r1(fga),
                "fg_pct": r3(fg_pct), "fg3m": r1(fg3m), "fg3a": r1(fg3a),
                "fg3_pct": r3(fg3_pct), "ftm": r1(ftm), "fta": r1(fta),
                "ft_pct": r3(ft_pct), "w_pct": r3(w_pct), "plus_minus": r1(plus_minus),
            },
            "advanced": {
                "ts_pct": r3(ts_pct), "efg_pct": r3(efg_pct), "usg_pct": r3(usg_pct),
                "off_rating": r1(off_rating), "def_rating": r1(def_rating),
                "net_rating": r1(net_rating), "ast_pct": r3(ast_pct),
                "reb_pct": r3(reb_pct), "oreb_pct": r3(oreb_pct), "tov_pct": r3(tov_pct),
            },
            "plus_minus": {
                "bpm": r3(bpm), "obpm": r3(obpm), "dbpm": r3(dbpm), "vorp": r3(vorp),
            },
        })

    return {"season": season, "min_minutes": min_minutes, "count": len(results), "results": results,
            "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference")}
