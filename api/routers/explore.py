"""
Regression Explorer: how two player-season stats move together.

    GET /explore/regression?x=usg_pct&y=ts_pct&season_from=2016&season_to=2026
    GET /explore/breakouts?season=2026&direction=up   (see the Breakout section)

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

from functools import lru_cache

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


# ─── Breakout Detector ──────────────────────────────────────────────────────
# A breakout is a jump in a player's standing within the league from one
# season to the next: each stat is z-scored within its own season among
# qualified players, and the score is the average change in z across the
# chosen stats (lower-is-better stats flipped). Comparing standings, not raw
# numbers, keeps league-wide shifts (pace, the three-point boom) out.

BREAKOUT_DEFAULT = ["pts", "ts_pct", "usg_pct", "ast_pct", "reb_pct", "bpm"]
BREAKOUT_TOP = 20  # size of the historical breakout lists used for the persistence check


@lru_cache(maxsize=32)
def _season_z(stats: tuple, min_gp: int, min_mpg: float):
    """{season: {player_id: (z array, raw array, name, team, age, gp, min)}} for every
    season where all stats are recorded; z within that season's qualified pool.
    A shooting percentage below its attempts floor gets z = 0 (average)."""
    first = max(STATS[k][3] for k in stats)
    att_cols = sorted({STATS[k][5] for k in stats if STATS[k][5]})
    cols = ["player_id", "player_name", "season", "team_abbreviation", "age", "gp", "min"] + \
        [c for c in list(stats) + att_cols if c not in ("gp", "min", "age")]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(cols)} FROM player_season_stats "
                    f"WHERE season >= %s AND gp >= %s AND min >= %s;", (first, min_gp, min_mpg))
        rows = cur.fetchall()
    idx = {c: i for i, c in enumerate(cols)}
    by_season = {}
    for r in rows:
        by_season.setdefault(int(r[idx["season"]]), []).append(r)

    out = {}
    for season, rs in by_season.items():
        n = len(rs)
        raw = np.full((n, len(stats)), np.nan)
        z = np.zeros((n, len(stats)))
        for j, k in enumerate(stats):
            vals = np.array([np.nan if r[idx[k]] is None else float(r[idx[k]]) for r in rs])
            ok = ~np.isnan(vals)
            att = STATS[k][5]
            if att:
                ok &= np.array([(r[idx[att]] or 0) >= ATTEMPT_DEFAULTS[att] for r in rs])
            raw[:, j] = vals
            if ok.sum() >= 2 and vals[ok].std() > 0:
                z[ok, j] = (vals[ok] - vals[ok].mean()) / vals[ok].std()
            if not STATS[k][4]:
                z[:, j] = -z[:, j]
        out[season] = {
            int(r[idx["player_id"]]): (z[i], raw[i], r[idx["player_name"]], r[idx["team_abbreviation"]],
                                       r[idx["age"]], r[idx["gp"]], r[idx["min"]])
            for i, r in enumerate(rs)
        }
    return out


def _jumps(zs, season):
    """(player_id, score, per-stat delta z) for players qualified in season and season-1."""
    prev, cur = zs.get(season - 1, {}), zs.get(season, {})
    res = []
    for pid, now in cur.items():
        if pid in prev:
            d = now[0] - prev[pid][0]
            res.append((pid, float(d.mean()), d))
    return res


@lru_cache(maxsize=32)
def _persistence(stats: tuple, min_gp: int, min_mpg: float):
    """How much of a top-20 breakout survives the next season, historically."""
    zs = _season_z(stats, min_gp, min_mpg)
    seasons = sorted(zs)
    kept, n_players, used = [], 0, []
    for s in seasons[1:-1]:
        top = sorted(_jumps(zs, s), key=lambda t: -t[1])[:BREAKOUT_TOP]
        nxt = zs.get(s + 1, {})
        before = zs[s - 1]
        fr = []
        for pid, score, _d in top:
            if pid in nxt and score > 0:
                later = float((nxt[pid][0] - before[pid][0]).mean())
                fr.append(later / score)
        if fr:
            kept.append(float(np.median(fr)))
            n_players += len(fr)
            used.append(s)
    if not kept:
        return None
    return {"median_share_kept": round(float(np.median(kept)), 3), "seasons": len(used),
            "from": used[0], "to": used[-1], "players": n_players}


@router.get("/explore/breakouts")
def breakouts(
    season: int | None = None,
    stats: str | None = None,
    direction: str = "up",
    min_gp: int = Query(30, ge=0),
    min_mpg: float = Query(15.0, ge=0),
    top_n: int = Query(25, ge=1, le=100),
):
    """
    Biggest season-over-season jumps (direction=up) or drops (down) in a
    player's standing within the league. stats = comma-separated keys
    (default pts,ts_pct,usg_pct,ast_pct,reb_pct,bpm). Both seasons must pass
    min_gp/min_mpg.
    """
    keys = [k.strip() for k in (stats or ",".join(BREAKOUT_DEFAULT)).split(",") if k.strip()]
    keys = list(dict.fromkeys(keys))
    bad = [k for k in keys if k not in STATS or k == "age"]
    if bad or not keys:
        raise HTTPException(status_code=400, detail=f"Unknown or unusable stats: {', '.join(bad) or '(none)'}.")
    if len(keys) > 8:
        raise HTTPException(status_code=400, detail="At most 8 stats.")
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'.")
    stats_t = tuple(keys)
    zs = _season_z(stats_t, min_gp, float(min_mpg))
    seasons = sorted(zs)
    if len(seasons) < 2:
        raise HTTPException(status_code=404, detail="Not enough seasons with these stats.")
    season = seasons[-1] if season is None else season
    if season not in zs or season - 1 not in zs:
        first = STATS[max(keys, key=lambda k: STATS[k][3])][3]
        raise HTTPException(status_code=404, detail=(
            f"Pick a season from {_label(first + 1)} on: these stats start in {_label(first)} "
            f"and a breakout needs the season before."))

    jumps = _jumps(zs, season)
    jumps.sort(key=lambda t: -t[1] if direction == "up" else t[1])
    prev, cur = zs[season - 1], zs[season]

    def num(v, d=4):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)

    results = []
    for rank, (pid, score, d) in enumerate(jumps[:top_n], start=1):
        now, before = cur[pid], prev[pid]
        results.append({
            "rank": rank, "player_id": pid, "player_name": now[2], "team": now[3],
            "age": num(now[4], 0), "gp": int(now[5]), "min": num(now[6], 1), "min_before": num(before[6], 1),
            "score": round(score, 3),
            "stats": {k: {"before": num(before[1][j]), "now": num(now[1][j]), "delta_z": round(float(d[j]), 2)}
                      for j, k in enumerate(keys)},
        })

    return {
        "season": season,
        "seasons_available": [s for s in seasons if s - 1 in zs],
        "direction": direction,
        "stats": [{"key": k, "label": STATS[k][0], "format": STATS[k][2], "higher_is_better": STATS[k][4]}
                  for k in keys],
        "filters": {"min_gp": min_gp, "min_mpg": min_mpg, "top_n": top_n},
        "pool": len(jumps),
        "persistence": _persistence(stats_t, min_gp, float(min_mpg)),
        "results": results,
        "method": (
            "Each stat is z-scored within its season among players with the games and minutes shown, so a "
            "player's number is his standing in that season's league. The score is the average change in "
            "standing across the chosen stats from the season before (lower-is-better stats flipped; a shooting "
            "percentage on too few attempts counts as average). Both seasons must qualify. Big one-year jumps "
            "partly regress: the historical line shows how much of a top-20 breakout the same players kept the "
            "following season (median across seasons)."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }
