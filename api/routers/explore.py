"""
Regression Explorer: how two player-season stats move together.

    GET /explore/regression?x=usg_pct&y=ts_pct&season_from=2016&season_to=2026

Fits y = a + b*x over player-seasons (player_season_stats), with the
Leaderboard Builder's stat catalogue, first-season rules and attempt floors.

Two things keep the answer honest:
  * within_season (default on): x and y are both measured against their own
    season's average before fitting (season fixed effects), so a league-wide
    trend (threes rising while scoring efficiency rose) can't masquerade as a
    relationship between players.
  * Standard errors are clustered by player: one player's seasons aren't
    independent observations, so plain OLS errors would be too small
    (scripts/stats_lib.wls_cluster, CR1 correction, t intervals).

Association only: the page says so.
"""

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db  # also puts scripts/ on sys.path
from routers.leaderboard import ATTEMPT_DEFAULTS, STATS
from source_badge import make_source
from stats_lib import wls_cluster

router = APIRouter()

MAX_POINTS = 2500


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


@router.get("/explore/regression")
def explore_regression(
    x: str,
    y: str,
    season_from: int | None = None,
    season_to: int | None = None,
    min_gp: int = Query(30, ge=0),
    min_mpg: float = Query(20.0, ge=0),
    within_season: bool = True,
):
    for key in (x, y):
        if key not in STATS:
            raise HTTPException(status_code=400, detail=f"Unknown stat '{key}'. See /leaderboard/options.")
    if x == y:
        raise HTTPException(status_code=400, detail="Pick two different stats.")

    first_needed = max(STATS[x][3], STATS[y][3])
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
        lo, hi = cur.fetchone()
        season_to = hi if season_to is None else season_to
        season_from = season_to if season_from is None else season_from
        if season_from > season_to:
            season_from, season_to = season_to, season_from
        clipped_from = max(season_from, first_needed, lo)
        if season_to < clipped_from:
            raise HTTPException(status_code=404, detail=(
                f"Both stats are only recorded together from {_label(first_needed)} on."))

        where = [f"{x} IS NOT NULL", f"{y} IS NOT NULL", "season BETWEEN %s AND %s", "gp >= %s", "min >= %s"]
        params = [clipped_from, season_to, min_gp, min_mpg]
        floors = []
        for key in (x, y):
            att = STATS[key][5]
            if att:
                where.append(f"{att} >= %s")
                params.append(ATTEMPT_DEFAULTS[att])
                floors.append(f"{STATS[key][0]} needs {ATTEMPT_DEFAULTS[att]:g}+ {att.upper()} a game")
        cols = ["player_id", "player_name", "season", "team_abbreviation", x, y]
        cur.execute(f"SELECT {', '.join(dict.fromkeys(cols))} FROM player_season_stats "
                    f"WHERE {' AND '.join(where)};", params)
        rows = cur.fetchall()

    if len(rows) < 30:
        raise HTTPException(status_code=404, detail=f"Only {len(rows)} player-seasons pass these filters; "
                                                    f"widen the range or lower the floors.")

    pid = np.array([r[0] for r in rows])
    season = np.array([r[2] for r in rows])
    xv = np.array([float(r[4]) for r in rows])
    yv = np.array([float(r[5]) for r in rows])

    n_seasons = len(np.unique(season))
    demean = within_season and n_seasons > 1
    if demean:
        xf, yf = xv.copy(), yv.copy()
        for s in np.unique(season):
            m = season == s
            xf[m] -= xv[m].mean()
            yf[m] -= yv[m].mean()
    else:
        xf, yf = xv, yv
    if np.std(xf) == 0:
        raise HTTPException(status_code=404, detail=f"{STATS[x][0]} doesn't vary in this selection.")

    fit = wls_cluster(yf, np.column_stack([np.ones(len(xf)), xf]), np.ones(len(xf)), pid)
    slope, se = float(fit["beta"][1]), float(fit["se"][1])
    r = float(np.corrcoef(xf, yf)[0, 1])
    sd_x, sd_y = float(np.std(xf)), float(np.std(yf))

    # Line drawn on the raw axes: through the overall means with the fitted
    # slope (for within-season fits that is the average within-season line).
    x_mean, y_mean = float(xv.mean()), float(yv.mean())
    intercept_raw = y_mean - slope * x_mean if demean else float(fit["beta"][0])

    # Residuals on the fitted scale, for the "furthest above/below the line" lists.
    resid = yf - (fit["beta"][0] + slope * xf)

    # Deterministic sample for drawing (the fit uses every row).
    order = np.argsort((pid * 2654435761 + season * 97) % 1000003, kind="stable")
    keep = np.sort(order[:MAX_POINTS])

    def pt(i):
        return {"player_id": int(pid[i]), "player_name": rows[i][1], "season": int(season[i]),
                "team": rows[i][3], "x": round(float(xv[i]), 4), "y": round(float(yv[i]), 4),
                "residual": round(float(resid[i]), 4)}

    above = [pt(i) for i in np.argsort(-resid)[:8]]
    below = [pt(i) for i in np.argsort(resid)[:8]]

    def stat_meta(k):
        return {"key": k, "label": STATS[k][0], "format": STATS[k][2]}

    return {
        "x": stat_meta(x),
        "y": stat_meta(y),
        "filters": {"season_from": clipped_from, "season_to": season_to, "min_gp": min_gp, "min_mpg": min_mpg,
                    "within_season": demean},
        "notes": ([f"Starts in {_label(clipped_from)}, the first season both stats are recorded."]
                  if clipped_from > season_from else []) + floors,
        "fit": {
            "slope": round(slope, 6), "se": round(se, 6),
            "ci_low": round(float(fit["ci_low"][1]), 6), "ci_high": round(float(fit["ci_high"][1]), 6),
            "p": float(fit["p"][1]), "r": round(r, 4), "r2": round(float(fit["r2"]), 4),
            "per_sd_x": round(slope * sd_x, 6), "sd_x": round(sd_x, 6), "sd_y": round(sd_y, 6),
            "intercept_raw": round(intercept_raw, 6),
            "n": int(fit["n"]), "n_players": int(fit["n_clusters"]), "n_seasons": int(n_seasons),
        },
        "points": [pt(i) for i in keep],
        "points_sampled": len(rows) > MAX_POINTS,
        "above_line": above,
        "below_line": below,
        "method": (
            "Ordinary least squares of y on x over player-seasons. With several seasons, both stats are first "
            "measured against their season's average (season fixed effects), so league-wide trends don't count "
            "as a relationship. Standard errors are clustered by player (a player's seasons aren't independent), "
            "with a t-based 95% interval. The line on the chart runs through the overall averages with the fitted "
            "slope. This shows association, not cause, and the filters choose who is in the pool: older players "
            "in it are the ones good enough to stay in the league, so age can look like it helps."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }
