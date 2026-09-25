import json
import math
from typing import Optional

from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
)

router = APIRouter()

MIN_DFGA_RELIABLE = 300

METHODOLOGY = (
    "DAD Index (OBPM-weighted) = the sum, over every real offensive player a defender was matched up with, of that "
    "player's share of the defender's real partial possessions (NBA tracking, LeagueSeasonMatchups) times that "
    "player's OBPM. OBPM is this project's own reproduction of Box Plus/Minus 2.0 (build_bpm_vorp.py), not "
    "Basketball-Reference's numbers; EPM is proprietary and not used. Offensive players with under 500 real minutes "
    "that season (or no stored season line) count as replacement level, OBPM -2.0. Only matchup pairs of 5+ real "
    "partial possessions are stored, so shares are over those. Qualified defenders have 1,000+ real partial "
    "possessions; DAD is z-scored within each season's qualified pool, and also within the defender's position group "
    "(G/F/C) — qualified centers average about +0.95 on the plain z-score, partly because they guard other starting "
    "bigs and partly because this BPM reproduction runs hot for productive bigs, so the position view is the fairer "
    "wing-vs-big comparison. The DFG% differential is the NBA's own real defended FG% minus the same shooters' real "
    "normal FG% (LeagueDashPtDefend); negative = shooters made fewer shots than usual. It has a real sampling margin "
    "(95% interval shown); defenders under 300 real defended shots are greyed out. This describes who a defender "
    "guarded and how those shots went — it is not a complete defensive rating (no help defense, no rebounding, no "
    "scheme)."
)

QUADRANTS = {
    "lockdown": "Lockdown vs. stars — hard assignments, shooters below their normal FG%",
    "hidden": "Hidden — easy assignments, shooters below their normal FG%",
    "targeted": "Targeted — easy assignments, yet shooters above their normal FG%",
    "struggling": "Struggling vs. stars — hard assignments, shooters above their normal FG%",
}


def quadrant(z, dfg_diff):
    if z is None or dfg_diff is None:
        return None
    if z >= 0:
        return "lockdown" if dfg_diff <= 0 else "struggling"
    return "hidden" if dfg_diff <= 0 else "targeted"


def _require_table(cursor):
    cursor.execute("SELECT to_regclass('public.defender_dad');")
    if cursor.fetchone()[0] is None:
        raise HTTPException(
            status_code=503,
            detail="DAD Index hasn't been built yet — run scripts/fetch_defend_dashboard.py then scripts/build_dad_index.py.",
        )


@router.get("/defense/dad")
def get_dad_index(season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        _require_table(cursor)
        cursor.execute("SELECT DISTINCT season FROM defender_dad ORDER BY season;")
        seasons = [r[0] for r in cursor.fetchall()]
        if not seasons:
            raise HTTPException(status_code=503, detail="defender_dad is empty.")
        if season is None:
            season = seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"No real matchup data for season {season}.")

        cursor.execute(
            """SELECT player_id, player_name, team_abbreviation, position, pos_group, total_poss, n_assignments,
                      dad, dad_z, dad_pos_z, replacement_share, top3, d_fga, d_fg_pct, normal_fg_pct, dfg_diff
               FROM defender_dad WHERE season = %s AND qualified ORDER BY dad DESC;""",
            (season,),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """SELECT n_qualified, n_defenders, yoy_r, yoy_n, dad_dfg_r, dad_dfg_n, replacement_poss_share,
                      dad_mean, dad_sd
               FROM dad_validation WHERE season = %s;""",
            (season,),
        )
        v = cursor.fetchone()

    defenders = []
    for r in rows:
        d_fga, d_pct = r[12], r[13]
        margin = 1.96 * math.sqrt(d_pct * (1 - d_pct) / d_fga) if d_fga and d_pct is not None else None
        top3 = r[11] if isinstance(r[11], list) else json.loads(r[11] or "[]")
        defenders.append({
            "player_id": r[0], "player_name": r[1], "team_abbreviation": r[2], "position": r[3],
            "pos_group": r[4], "total_poss": round(r[5], 1), "n_assignments": r[6],
            "dad": r[7], "dad_z": r[8], "dad_pos_z": r[9], "replacement_share": r[10],
            "top_assignments": top3,
            "d_fga": d_fga, "d_fg_pct": d_pct, "normal_fg_pct": r[14], "dfg_diff": r[15],
            "dfg_diff_margin95": margin,
            "small_dfg_sample": d_fga is None or d_fga < MIN_DFGA_RELIABLE,
            "quadrant": quadrant(r[8], r[15]),
            "quadrant_pos": quadrant(r[9], r[15]),
        })

    validation = None
    if v:
        validation = {
            "n_qualified": v[0], "n_defenders": v[1],
            "year_over_year_r": v[2], "year_over_year_n": v[3],
            "dad_vs_dfg_diff_r": v[4], "dad_vs_dfg_diff_n": v[5],
            "replacement_possession_share": v[6], "dad_mean": v[7], "dad_sd": v[8],
            "note": (
                "Year-over-year r compares the same real defenders' DAD in consecutive seasons — how persistent "
                "a defender's assignment difficulty is. DAD vs. DFG% differential r is descriptive only."
            ) + (
                " 2025-26 OBPM comes from an earlier snapshot of the 2025-26 season line (see README known gaps)."
                if season == 2026 else ""
            ),
        }

    return {
        "season": season,
        "seasons_available": seasons,
        "name": "DAD Index (OBPM-weighted)",
        "methodology": METHODOLOGY,
        "thresholds": {
            "qualified_min_partial_poss": 1000,
            "replacement_min_minutes": 500,
            "replacement_obpm": -2.0,
            "reliable_min_defended_fga": MIN_DFGA_RELIABLE,
        },
        "quadrants": QUADRANTS,
        "validation": validation,
        "defenders": defenders,
        "_source": make_source(
            ["defender_dad", "dad_validation", "player_matchups", "defender_dfg", "player_season_stats"],
            "nba_api (LeagueSeasonMatchups, LeagueDashPtDefend) + this project's BPM reproduction",
        ),
    }
