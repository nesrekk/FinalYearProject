from scipy.stats import pearsonr
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    LENGTH_STUDY_MIN_TOTAL_MINUTES,
    get_db,
)

router = APIRouter()


@router.get("/draft/length-study")
def get_length_study():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            -- A real player can attend the real combine more than once (e.g.
            -- an underclassman testing again in a later year) — dedupe to
            -- their single most recent real measurement before joining, so
            -- each player contributes exactly one real point to the study.
            WITH latest_combine AS (
                SELECT DISTINCT ON (player_id) player_id, player_name, wingspan, height_wo_shoes
                FROM draft_combine
                WHERE wingspan IS NOT NULL AND height_wo_shoes IS NOT NULL
                ORDER BY player_id, draft_year DESC
            )
            SELECT dc.player_id, dc.player_name, dc.wingspan, dc.height_wo_shoes,
                   SUM(p.dbpm * p.min * p.gp) / NULLIF(SUM(p.min * p.gp), 0) AS avg_dbpm,
                   (SUM(p.blk * p.gp) + SUM(p.stl * p.gp)) / NULLIF(SUM(p.min * p.gp), 0) * 36 AS stocks_per36,
                   SUM(p.min * p.gp) AS total_minutes
            FROM latest_combine dc
            JOIN player_season_stats p ON p.player_id = dc.player_id
            WHERE p.dbpm IS NOT NULL AND p.min IS NOT NULL AND p.gp IS NOT NULL
            GROUP BY dc.player_id, dc.player_name, dc.wingspan, dc.height_wo_shoes
            HAVING SUM(p.min * p.gp) >= %s;
            """,
            (LENGTH_STUDY_MIN_TOTAL_MINUTES,),
        )
        rows = cursor.fetchall()

    if len(rows) < 10:
        raise HTTPException(status_code=404, detail="Not enough real players with both combine and career defensive data yet.")

    points = [
        {
            "player_id": r[0], "player_name": r[1],
            "wingspan_minus_height": round(r[2] - r[3], 2),
            "avg_dbpm": round(r[4], 3),
            "stocks_per36": round(r[5], 2),
        }
        for r in rows
    ]

    wmh = [p["wingspan_minus_height"] for p in points]
    dbpm = [p["avg_dbpm"] for p in points]
    stocks = [p["stocks_per36"] for p in points]

    r_dbpm, p_dbpm = pearsonr(wmh, dbpm)
    r_stocks, p_stocks = pearsonr(wmh, stocks)

    return {
        "n": len(points),
        "min_total_minutes": LENGTH_STUDY_MIN_TOTAL_MINUTES,
        "points": points,
        "correlations": {
            "wingspan_minus_height_vs_dbpm": {"r": round(float(r_dbpm), 3), "p_value": round(float(p_dbpm), 4)},
            "wingspan_minus_height_vs_stocks_per36": {"r": round(float(r_stocks), 3), "p_value": round(float(p_stocks), 4)},
        },
        "methodology": (
            f"Real wingspan-minus-height (NBA Draft Combine) vs. real career-average Defensive Box Plus-Minus "
            f"and real career-average steals+blocks per 36 minutes (minutes-weighted across each real player's "
            f"whole real career, player_season_stats), for {len(points)} real players with at least "
            f"{LENGTH_STUDY_MIN_TOTAL_MINUTES} real career minutes and real combine measurements. A real Pearson "
            "correlation coefficient, not a causal claim — length is one real input among many real factors "
            "(effort, positioning, IQ) that drive real defensive production."
        ),
        "_source": make_source(["draft_combine", "player_season_stats"], "nba_api (stats.nba.com)"),
    }
