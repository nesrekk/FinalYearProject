from psycopg2 import pool

from fastapi import APIRouter, HTTPException

from impact_core import (
    COMPARE_DETAIL_STATS,
    COMPOSITE_SKILL_AXES,
    RADAR_MIN_GAMES,
    RADAR_MIN_MINUTES,
    _percentile_rank,
    _position_label,
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/players/compare-profile/{player_name}")
def get_compare_profile(player_name: str, season: int):
    """
    Everything the Player Comparison page needs for one player: bio, "Tale
    of the Tape" raw stats (mapping the reference's Offensive/Defensive/
    Overall Impact rows onto this project's own OBPM/DBPM/BPM), 6 composite
    Skill Profile percentiles, and a granular percentile stat table.
    "Archetype" (statistical clustering) is used in place of a scouted
    offensive/defensive role — this project has no real scouted-role data.
    Real physical measurements (height, wingspan, standing reach, weight)
    ARE included when the player has real NBA Draft Combine data on file
    (scripts/fetch_draft_combine.py) — null when they don't (undrafted or
    skipped the combine), never guessed.
    """
    cols = [
        "pts", "reb", "ast", "ts_pct", "efg_pct", "usg_pct",
        "ast_pct", "reb_pct", "tov_pct", "oreb_pct", "net_rating",
        "fta", "fga", "fg3a", "bpm", "obpm", "dbpm", "vorp", "bpm_position",
        "age", "gp", "min", "team_abbreviation",
    ]
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            f"""
            SELECT p.player_id, {', '.join(f'p.{c}' for c in cols)}, c.archetype
            FROM player_season_stats p
            LEFT JOIN player_clusters c
                ON c.player_id = p.player_id AND c.season = p.season
            WHERE p.season = %s AND p.min >= %s AND p.gp >= %s;
            """,
            (season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        pool = cursor.fetchall()

        cursor.execute(
            """SELECT wingspan, height_wo_shoes, height_w_shoes, standing_reach, weight, max_vertical_leap
               FROM draft_combine WHERE player_id = %s ORDER BY draft_year DESC LIMIT 1;""",
            (player_id,),
        )
        combine_row = cursor.fetchone()

    if not pool:
        raise HTTPException(status_code=404, detail=f"No qualified player data for season {season}.")

    row_cols = ["player_id"] + cols + ["archetype"]
    pool_by_id = {row[0]: dict(zip(row_cols, row)) for row in pool}
    if player_id not in pool_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"{resolved_name} doesn't meet the qualified-pool minimum "
                   f"(min>={RADAR_MIN_MINUTES}mpg, gp>={RADAR_MIN_GAMES}) for season {season}.",
        )

    # FTr / 3PAr aren't stored columns — derive for the whole pool (needed
    # to rank this player's percentile against them), same pattern as the
    # derived radar metrics above.
    for v in pool_by_id.values():
        v["ftr"] = (v["fta"] / v["fga"]) if v.get("fga") else None
        v["tpar"] = (v["fg3a"] / v["fga"]) if v.get("fga") else None

    player = pool_by_id[player_id]
    n = len(pool_by_id)

    combine_measurements = None
    if combine_row:
        wingspan, height_wo, height_w, reach, weight, vertical = combine_row
        combine_measurements = {
            "wingspan": wingspan, "height_wo_shoes": height_wo, "height_w_shoes": height_w,
            "standing_reach": reach, "weight": weight, "max_vertical_leap": vertical,
        }

    def pct_entry(stat, label):
        this_value = player.get(stat)
        pool_values = [v[stat] for v in pool_by_id.values() if v.get(stat) is not None]
        return {
            "key": stat,
            "label": label,
            "value": round(float(this_value), 3) if this_value is not None else None,
            "percentile": _percentile_rank(this_value, pool_values),
        }

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "pool_size": n,
        "bio": {
            "team_abbreviation": player.get("team_abbreviation"),
            "age": player.get("age"),
            "gp": player.get("gp"),
            "min": round(player["min"], 1) if player.get("min") is not None else None,
            "position": _position_label(player["bpm_position"]) if player.get("bpm_position") is not None else None,
            "archetype": player.get("archetype"),
            "combine_measurements": combine_measurements,
        },
        "tale_of_the_tape": {
            "pts": player.get("pts"), "reb": player.get("reb"), "ast": player.get("ast"),
            "ts_pct": player.get("ts_pct"), "usg_pct": player.get("usg_pct"),
            "obpm": player.get("obpm"), "dbpm": player.get("dbpm"), "bpm": player.get("bpm"),
        },
        "skill_profile": [pct_entry(stat, label) for _, label, stat in COMPOSITE_SKILL_AXES],
        "detail_stats": [pct_entry(stat, label) for stat, label in COMPARE_DETAIL_STATS],
    }
