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


REACH_COLS = ["p_r64", "p_r32", "p_s16", "p_e8", "p_f4", "p_final", "p_champ"]


@lru_cache(maxsize=1)
def _madness_overview():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM ncaa_model_seasons ORDER BY season")
            cols = [d[0] for d in cur.description]
            seasons = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute(
                """SELECT feature_set, bool_or(chosen), sum(n_games),
                          sum(n_games * log_loss) / sum(n_games), sum(n_games * brier) / sum(n_games),
                          sum(n_games * accuracy) / sum(n_games)
                   FROM ncaa_model_backtest GROUP BY feature_set ORDER BY 4"""
            )
            variants = [
                {"feature_set": r[0], "chosen": r[1], "n_games": int(r[2]), "log_loss": round(float(r[3]), 4),
                 "brier": round(float(r[4]), 4), "accuracy": round(float(r[5]), 4)}
                for r in cur.fetchall()
            ]
    except psycopg2.errors.UndefinedTable:
        raise HTTPException(
            status_code=503,
            detail="March Madness model not built yet: run scripts/fetch_cbb_games.py then scripts/build_ncaa_model.py.",
        )
    for s in seasons:
        for k, v in list(s.items()):
            if isinstance(v, float):
                s[k] = round(v, 4)
    backtest = [s for s in seasons if not s["is_test"]]
    return {
        "chosen_feature_set": variants and next(v["feature_set"] for v in variants if v["chosen"]),
        "variants": variants,
        "seasons": seasons,
        "backtest_summary": {
            "n_seasons": len(backtest),
            "champion_top1": sum(s["champion_rank"] == 1 for s in backtest),
            "champion_top4": sum(s["champion_rank"] <= 4 for s in backtest),
            # Seeds can't single out one team (four 1 seeds tie), so the seed
            # comparison is "champion inside the top-4 seed group", ties counted whole.
            "seed_champion_top4": sum(s["seed_champion_rank"] + s["seed_champion_tied"] - 1 <= 4 for s in backtest),
            "reach_log_loss": round(sum(s["reach_log_loss"] for s in backtest) / len(backtest), 4) if backtest else None,
            "seed_reach_log_loss": round(sum(s["seed_reach_log_loss"] for s in backtest) / len(backtest), 4) if backtest else None,
        },
    }


@router.get("/college/madness")
def get_march_madness(season: int = None):
    """March Madness model: pre-tournament bracket odds for one season (the
    test season by default), plus the model's backtest record."""
    overview = _madness_overview()
    seasons = [s["season"] for s in overview["seasons"]]
    test = next((s["season"] for s in overview["seasons"] if s["is_test"]), seasons[-1])
    season = season or test
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No tournament for {season}. Available: {', '.join(map(str, seasons))}.")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM ncaa_bracket_odds WHERE season = %s ORDER BY p_champ DESC, seed", (season,))
        cols = [d[0] for d in cur.description]
        teams = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute(
            """SELECT round_name, round, start_date, team_a, seed_a, points_a, team_b, seed_b, points_b, winner, p_a
               FROM ncaa_tourney_games WHERE season = %s ORDER BY round, start_date""",
            (season,),
        )
        cols = [d[0] for d in cur.description]
        games = [dict(zip(cols, r)) for r in cur.fetchall()]
    for g in games:
        g["start_date"] = g["start_date"].isoformat()
        fav_p = g["p_a"] if g["p_a"] >= 0.5 else 1 - g["p_a"]
        fav = g["team_a"] if g["p_a"] >= 0.5 else g["team_b"]
        g["favourite"], g["favourite_p"] = fav, round(float(fav_p), 4)
        g["upset"] = g["winner"] is not None and g["winner"] != fav
        g["seed_upset"] = (
            g["winner"] is not None and g["seed_a"] != g["seed_b"]
            and g["winner"] == (g["team_a"] if g["seed_a"] > g["seed_b"] else g["team_b"])
        )
    return {
        **overview,
        "season": season,
        "season_summary": next(s for s in overview["seasons"] if s["season"] == season),
        "teams": teams,
        "games": games,
        "_source": make_source(
            ["cbb_games", "ncaa_bracket_odds", "ncaa_model_seasons", "ncaa_model_backtest", "ncaa_tourney_games"],
            "CollegeBasketballData.com (every D1 game 2013-2026: scores, Elo at tip-off, NCAA seeds)",
        ),
    }
