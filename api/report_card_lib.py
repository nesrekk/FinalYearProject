"""
report_card_lib.py
==================
Shared by scripts/build_report_card.py (which scores every model season by season and stores the tests) and
api/routers/report_card.py (the Model Report Card page): the tasks, their models, metrics and reference model,
the labels, and the random-effects pooling, so the page and the stored rows can't disagree on what a model is
called or how seasons are pooled.

Pooling across seasons: a season's difference between two models, d_i, with its bootstrap standard error s_i
(within-season variance v_i = s_i^2), is pooled by DerSimonian-Laird (tau^2 = the between-season variance of the
true difference, method of moments), with the Hartung-Knapp-Sidik-Jonkman interval (t with k - 1 degrees of
freedom; with 4-14 seasons the plain normal interval is too narrow; its scale factor is floored at 1, so the
interval is never narrower than DerSimonian-Laird's own standard error times t) and the prediction interval for a new season
(t with k - 2 degrees of freedom, k >= 3): where next season's difference should fall if seasons keep varying as
they have. I^2 = the share of the spread across seasons beyond what within-season noise explains.
"""

import math

import numpy as np
from scipy import stats

# task -> what it scores. `metrics`: (name, label, lower is better); `reference`: every model is pooled against it.
TASKS = {
    "pregame": {
        "label": "Pre-game win probability",
        "short": "Pre-game odds",
        "unit": "game", "units_label": "games",
        "what": "P(home team wins) before each regular-season game, from what was known that morning.",
        "models": ("baseline", "current", "prior", "prior_rest", "chosen"),
        "reference": "baseline",
        "metrics": (("log_loss", "Log loss", True), ("brier", "Brier score", True)),
        "variants": ("",),
        "from": 2013,
    },
    "sim_playoffs": {
        "label": "Season simulator: who makes the playoffs",
        "short": "Playoff odds",
        "unit": "team_season", "units_label": "team-seasons",
        "what": "Each team's chance of reaching the playoffs (top 8 after the play-in), at a checkpoint of the season.",
        "models": ("model", "record", "standings"),
        "reference": "record",
        "metrics": (("brier", "Brier score", True), ("log_loss", "Log loss", True)),
        "variants": ("halfway", "sixty", "opening"),
        "from": 2013,
    },
    "sim_top6": {
        "label": "Season simulator: who finishes top 6",
        "short": "Top-6 odds",
        "unit": "team_season", "units_label": "team-seasons",
        "what": "Each team's chance of a top-6 seed (no play-in), from 2020-21, when the play-in began.",
        "models": ("model", "record", "standings"),
        "reference": "record",
        "metrics": (("brier", "Brier score", True),),
        "variants": ("halfway", "sixty", "opening"),
        "from": 2021,
    },
    "sim_wins": {
        "label": "Season simulator: final win totals",
        "short": "Win totals",
        "unit": "team_season", "units_label": "team-seasons",
        "what": "Each team's expected final wins and its 80% range, at a checkpoint of the season.",
        "models": ("model", "record"),
        "reference": "record",
        "metrics": (("mae", "Mean absolute error (wins)", True), ("rmse", "RMSE (wins)", True),
                    ("cover80", "Share inside the 80% range", None)),
        "variants": ("halfway", "sixty", "opening"),
        "from": 2013,
    },
    "impact_next": {
        "label": "Player impact: next season's game margins",
        "short": "Impact → next season",
        "unit": "game", "units_label": "games",
        "what": "Ratings from season S, at scale one, predict every tracked game margin of season S+1 from who was on "
                "the floor (S's intercept and home term).",
        "models": ("zero", "bpm", "bpm_scaled", "onoff", "onoff_scaled", "rapm_single", "rapm_prior", "rapm_multi",
                   "rapm_tracker", "xrapm_single", "xrapm_prior", "xrapm_lf_single", "xrapm_lf_prior",
                   "xrapm_sa_single", "xrapm_sa_prior"),
        "reference": "zero",
        "metrics": (("game_rmse", "Game-margin RMSE (points)", True),),
        "variants": ("",),
        "from": 2023,
    },
    "impact_poss": {
        "label": "Player impact: next season, per possession",
        "short": "Impact → per possession",
        "unit": "possession", "units_label": "possessions",
        "what": "The same ratings scored on every counted possession of season S+1 (round 6's possessions table): the "
                "offence's expected points from the ten on the floor when the possession began.",
        "models": ("zero", "bpm", "bpm_scaled", "onoff", "onoff_scaled", "rapm_single", "rapm_prior", "rapm_multi",
                   "rapm_tracker", "xrapm_single", "xrapm_prior", "xrapm_lf_single", "xrapm_lf_prior",
                   "xrapm_sa_single", "xrapm_sa_prior"),
        "reference": "zero",
        "metrics": (("poss_rmse", "Points-per-possession RMSE", True),),
        "variants": ("",),
        "from": 2023,
    },
    "xfg": {
        "label": "Shot model: will the shot go in?",
        "short": "Shot model",
        "unit": "shot", "units_label": "shots",
        "what": "P(make) for every regular-season field goal, priced before its game (Shot Value Added's prices).",
        "models": ("sa", "lf", "class", "constant"),
        "reference": "constant",
        "metrics": (("log_loss", "Log loss", True), ("brier", "Brier score", True)),
        "variants": ("",),
        "from": 2022,
    },
}
TASK_ORDER = ("pregame", "sim_playoffs", "sim_top6", "sim_wins", "impact_next", "impact_poss", "xfg")
VARIANT_LABELS = {"": "", "opening": "Opening day", "halfway": "Halfway", "sixty": "About 60 games in"}

MODEL_LABELS = {
    "pregame": {
        "baseline": "Luck & Schedule's projection",
        "current": "This season's ratings only",
        "prior": "Ratings blended with last season's",
        "prior_rest": "Blended ratings + back-to-backs",
        "chosen": "The form chosen before the season",
    },
    "sim": {
        "model": "Simulator (the chosen pre-game form)",
        "record": "Record carried forward (log5)",
        "standings": "Current standings",
    },
    "impact": {
        "zero": "Everyone average (home edge only)",
        "bpm": "BPM as published",
        "bpm_scaled": "BPM x one fitted scale",
        "onoff": "On/off as published",
        "onoff_scaled": "On/off x one fitted scale",
        "rapm_single": "RAPM, one season",
        "rapm_prior": "RAPM with a BPM prior",
        "rapm_multi": "RAPM, three seasons",
        "rapm_tracker": "Rating Tracker",
        "xrapm_single": "Expected-points RAPM (round 5)",
        "xrapm_prior": "Expected-points RAPM + prior (round 5)",
        "xrapm_lf_single": "Look-ahead-free xRAPM",
        "xrapm_lf_prior": "Look-ahead-free xRAPM + prior",
        "xrapm_sa_single": "Shooter-aware xRAPM",
        "xrapm_sa_prior": "Shooter-aware xRAPM + prior",
    },
    "xfg": {
        "sa": "Shooter-aware price",
        "lf": "Location model (shooter-blind)",
        "class": "Last season's FG% by shot class",
        "constant": "Last season's league FG%",
    },
}


def family(task):
    return "sim" if task.startswith("sim") else "impact" if task.startswith("impact") else task


def model_label(task, model):
    return MODEL_LABELS[family(task)].get(model, model)


def metric_info(task, metric):
    for name, label, lower in TASKS[task]["metrics"]:
        if name == metric:
            return label, lower
    raise KeyError(metric)


def badness(metric, value):
    """A number to rank by, lower = better: the metric itself, or for 80% coverage its distance from 0.80."""
    if value is None:
        return math.inf
    return abs(value - 0.8) if metric == "cover80" else value


def pair_key(task, a, b):
    """The stored order of a pair: the task's model order."""
    order = TASKS[task]["models"]
    return (a, b) if order.index(a) < order.index(b) else (b, a)


def random_effects(d, se):
    """DerSimonian-Laird pooling of per-season differences `d` with standard errors `se`. Returns a dict:
    k, fixed (inverse-variance mean) and its se, tau2, q, q_p, i2, mu, se_hk, ci_lo/ci_hi (Hartung-Knapp, t_{k-1}),
    p (t_{k-1}), pi_lo/pi_hi (prediction interval, t_{k-2}; None when k < 3). Seasons with se <= 0 are dropped."""
    d, se = np.asarray(d, float), np.asarray(se, float)
    ok = np.isfinite(d) & np.isfinite(se) & (se > 0)
    d, v = d[ok], se[ok] ** 2
    k = len(d)
    if k == 0:
        return None
    w = 1 / v
    fixed = float(np.sum(w * d) / np.sum(w))
    se_fixed = float(math.sqrt(1 / np.sum(w)))
    if k == 1:
        return {"k": 1, "fixed": fixed, "se_fixed": se_fixed, "tau2": None, "q": None, "q_p": None, "i2": None,
                "mu": fixed, "se_hk": se_fixed, "ci_lo": fixed - 1.959964 * se_fixed, "ci_hi": fixed + 1.959964 * se_fixed,
                "p": float(2 * stats.norm.sf(abs(fixed / se_fixed))), "pi_lo": None, "pi_hi": None}
    q = float(np.sum(w * (d - fixed) ** 2))
    c = float(np.sum(w) - np.sum(w * w) / np.sum(w))
    tau2 = max(0.0, (q - (k - 1)) / c)
    ws = 1 / (v + tau2)
    mu = float(np.sum(ws * d) / np.sum(ws))
    se_re = math.sqrt(1 / np.sum(ws))
    q_hk = float(np.sum(ws * (d - mu) ** 2) / (k - 1))
    se_hk = math.sqrt(max(1.0, q_hk) / np.sum(ws))     # never narrower than the DerSimonian-Laird standard error
    t = stats.t.ppf(0.975, k - 1)
    p = float(2 * stats.t.sf(abs(mu / se_hk), k - 1)) if se_hk > 0 else 0.0
    if k >= 3:
        half = stats.t.ppf(0.975, k - 2) * math.sqrt(tau2 + se_re ** 2)
        pi = (mu - half, mu + half)
    else:
        pi = (None, None)
    return {"k": k, "fixed": fixed, "se_fixed": se_fixed, "tau2": tau2, "q": q, "q_p": float(stats.chi2.sf(q, k - 1)),
            "i2": max(0.0, (q - (k - 1)) / q) if q > 0 else 0.0, "mu": mu, "se_hk": se_hk,
            "ci_lo": mu - t * se_hk, "ci_hi": mu + t * se_hk, "p": p, "pi_lo": pi[0], "pi_hi": pi[1]}
