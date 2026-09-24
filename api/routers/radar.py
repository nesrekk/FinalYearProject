from psycopg2 import pool
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    RADAR_HELPER_COLS,
    RADAR_MIN_GAMES,
    RADAR_MIN_MINUTES,
    RADAR_STATS,
    _shooting_proficiency,
    _spacing,
    find_player,
    get_db,
)

router = APIRouter()


@router.get("/radar/{player_name}")
def get_radar_profile(player_name: str, season: int):
    """
    Percentile rank (0-100) for each radar axis, among that season's
    qualified pool (min>=15mpg, gp>=20 — same convention as clustering, to
    keep rate stats meaningful). Percentile, not raw value or a fixed
    hardcoded scale, so every axis is directly comparable on a 0-100 chart
    regardless of the stat's natural range (points go to ~35, TS% to ~0.65).
    Includes CraftedNBA-style derived metrics alongside the raw box-score
    stats — see _shooting_proficiency/_spacing for the disclosed formulas
    being reproduced. defensive_impact/rad_per_game come from
    defense_tracking_stats (player-tracking data, only exists from 2013-14
    onward and only for players scripts/build_defense_tracking_stats.py has
    been run for) — null/omitted gracefully when unavailable, same as any
    other missing stat here.
    """
    all_cols = RADAR_STATS + RADAR_HELPER_COLS
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            f"""
            SELECT p.player_id, {', '.join(f'p.{c}' for c in all_cols)},
                   d.fg_diff_pct, d.rad_per_game
            FROM player_season_stats p
            LEFT JOIN defense_tracking_stats d
                ON d.player_id = p.player_id AND d.season = p.season
            WHERE p.season = %s AND p.min >= %s AND p.gp >= %s;
            """,
            (season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        pool = cursor.fetchall()

    if not pool:
        raise HTTPException(status_code=404, detail=f"No qualified player data for season {season}.")

    all_row_cols = all_cols + ["fg_diff_pct", "rad_per_game"]
    pool_by_id = {row[0]: dict(zip(all_row_cols, row[1:])) for row in pool}
    if player_id not in pool_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"{resolved_name} doesn't meet the qualified-pool minimum "
                   f"(min>={RADAR_MIN_MINUTES}mpg, gp>={RADAR_MIN_GAMES}) for season {season}.",
        )

    # Compute the derived metrics for every pool member (needed to rank this
    # player's percentile against them), not just the selected player.
    for values in pool_by_id.values():
        values["shooting_proficiency"] = _shooting_proficiency(
            values["fg3a"], values["fg3_pct"], values["gp"], values["poss"])
        values["spacing"] = _spacing(values["fg3a"], values["fg3_pct"], values["efg_pct"])
        # fg_diff_pct is negative for GOOD defense (holds opponents below
        # league average) — flip the sign so "further out on the radar" is
        # consistently "better" across every axis, like the rest of the chart.
        fgd = values["fg_diff_pct"]
        values["defensive_impact"] = (-fgd) if fgd is not None else None

    radar_axes = RADAR_STATS + ["shooting_proficiency", "spacing", "defensive_impact", "rad_per_game"]
    player_values = pool_by_id[player_id]
    n = len(pool)
    percentiles = []
    for stat in radar_axes:
        this_value = player_values[stat]
        if this_value is None:
            percentiles.append({"stat": stat, "value": None, "percentile": None})
            continue
        below_or_equal = sum(
            1 for v in pool_by_id.values() if v[stat] is not None and v[stat] <= this_value
        )
        percentile = round(100 * below_or_equal / n, 1)
        percentiles.append({"stat": stat, "value": round(float(this_value), 3), "percentile": percentile})

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "pool_size": n,
        "stats": percentiles,
        "_source": make_source(["player_season_stats", "defense_tracking_stats"], "nba_api (stats.nba.com)"),
    }
