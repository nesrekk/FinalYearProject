from functools import lru_cache

import numpy as np
import psycopg2
from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TIERS = [(1, 10, "Top 10"), (11, 25, "11–25"), (26, 50, "26–50"), (51, 100, "51–100"), (101, 10_000, "101+")]
RUNS = [
    ("Final Four+", {"Champions", "2ND", "F4"}),
    ("Sweet 16 / Elite 8", {"S16", "E8"}),
    ("First weekend", {"R68", "R64", "R32"}),
    ("No tournament", {None}),
]
MIN_SCHOOL_PICKS = 5


def _mean_ci(values, rng, n_boot=4000):
    values = np.asarray(values, dtype=float)
    boot = rng.choice(values, (n_boot, len(values))).mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return round(float(values.mean()), 2), round(float(lo), 2), round(float(hi), 2)


def _group(label, rows, rng):
    mean, lo, hi = _mean_ci([r["ws4_vs_expected"] for r in rows], rng)
    return {
        "label": label,
        "n": len(rows),
        "mean_pick": round(float(np.mean([r["overall_pick"] for r in rows])), 1),
        "mean_ws4": round(float(np.mean([r["ws4"] for r in rows])), 1),
        "ws4_vs_expected": mean,
        "ci_low": lo,
        "ci_high": hi,
    }


@lru_cache(maxsize=1)
def _pipeline():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM college_pipeline_meta;")
            meta = dict(cur.fetchall())
            cur.execute(
                """SELECT p.player_name, p.player_id, p.draft_year, p.overall_pick, p.nba_team,
                          p.college_team, p.college_season, p.season_check, p.ws4, p.seasons4,
                          p.ws_career, p.expected_ws4, p.ws4_vs_expected, p.mature,
                          t.conf, t.adj_margin, t.barthag, t.barthag_rank, t.n_teams, t.postseason, t.seed
                   FROM college_draft_pipeline p
                   JOIN college_team_seasons t ON t.season = p.college_season AND t.team = p.college_team
                   ORDER BY p.draft_year DESC, p.overall_pick"""
            )
            cols = [d[0] for d in cur.description]
            players = [dict(zip(cols, r)) for r in cur.fetchall()]
    except psycopg2.errors.UndefinedTable:
        raise HTTPException(
            status_code=503,
            detail="College pipeline not built yet: run scripts/load_college_teams.py then scripts/build_college_pipeline.py.",
        )

    for p in players:
        for k in ("ws4", "ws_career", "expected_ws4", "ws4_vs_expected", "adj_margin", "barthag"):
            p[k] = round(float(p[k]), 3 if k == "barthag" else 2) if p[k] is not None else None

    for p in players:
        if not p["mature"]:
            p["ws4_vs_expected"] = None  # fewer than four NBA seasons played so far

    mature = [p for p in players if p["mature"]]
    rng = np.random.default_rng(0)

    tiers = [
        _group(label, rows, rng)
        for lo, hi, label in TIERS
        for rows in [[p for p in mature if lo <= p["barthag_rank"] <= hi]]
        if rows
    ]
    runs = [
        _group(label, rows, rng)
        for label, codes in RUNS
        for rows in [[p for p in mature if p["postseason"] in codes]]
        if rows
    ]

    # Does team strength predict NBA value beyond the pick? OLS of ws4 on
    # ln(pick) + adj_margin, bootstrap CI on the adj_margin coefficient.
    ws4 = np.array([p["ws4"] for p in mature])
    margin = np.array([p["adj_margin"] for p in mature])
    resid = np.array([p["ws4_vs_expected"] for p in mature])
    X = np.column_stack([np.ones(len(mature)), np.log([p["overall_pick"] for p in mature]), margin])
    coef = np.linalg.lstsq(X, ws4, rcond=None)[0][2]
    boot = []
    for _ in range(2000):
        i = rng.integers(0, len(mature), len(mature))
        boot.append(np.linalg.lstsq(X[i], ws4[i], rcond=None)[0][2])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    r = float(np.corrcoef(margin, resid)[0, 1])

    by_school = {}
    for p in mature:
        by_school.setdefault(p["college_team"], []).append(p)
    schools = []
    for school, rows in by_school.items():
        if len(rows) < MIN_SCHOOL_PICKS:
            continue
        g = _group(school, rows, rng)
        g["total_ws4"] = round(sum(p["ws4"] for p in rows), 1)
        g["best"] = max(rows, key=lambda p: p["ws4"])["player_name"]
        schools.append(g)
    schools.sort(key=lambda s: s["ws4_vs_expected"], reverse=True)

    return {
        "mature_class": int(meta["mature_class"]),
        "counts": {
            "picks": int(meta["n_picks"]),
            "with_college": int(meta["n_with_college"]),
            "linked": int(meta["n_linked"]),
            "analysed": len(mature),
        },
        "expected_curve": {
            "a": round(meta["fit_a"], 3),
            "b": round(meta["fit_b"], 3),
            "n": int(meta["fit_n"]),
            "formula": "expected WS4 = a + b × ln(pick)",
        },
        "effect": {
            "pearson_r": round(r, 3),
            "ws4_per_10_margin": round(float(coef) * 10, 2),
            "ci_low": round(float(lo) * 10, 2),
            "ci_high": round(float(hi) * 10, 2),
        },
        "tiers": tiers,
        "runs": runs,
        "no_college": {
            "n": int(meta["other_n"]),
            "ws4_vs_expected": round(meta["other_mean"], 2),
            "ci_low": round(meta["other_lo"], 2),
            "ci_high": round(meta["other_hi"], 2),
            "no_nba_games_share": round(meta["other_zero_share"], 3),
            "college_no_nba_games_share": round(meta["college_zero_share"], 3),
            "played_ws4_vs_expected": round(meta["other_played_mean"], 2),
            "played_n": int(meta["other_played_n"]),
            "college_played_ws4_vs_expected": round(meta["college_played_mean"], 2),
            "college_played_n": int(meta["college_played_n"]),
        },
        "schools": schools,
        "min_school_picks": MIN_SCHOOL_PICKS,
        "players": players,
        "_source": make_source(
            ["college_draft_pipeline", "college_team_seasons", "college_player_season_stats"],
            "Basketball-Reference (draft history, Win Shares) via Kaggle; Bart Torvik T-Rank team ratings via Kaggle; CollegeBasketballData.com (last college season check)",
        ),
    }


@router.get("/college/pipeline")
def get_college_pipeline():
    """College-to-NBA pipeline: NBA draft picks 2013-2025 linked to their
    college team's strength that season (Torvik), and their first four NBA
    seasons of Win Shares against what their draft slot predicts."""
    return _pipeline()
