"""Model Report Card: every model scored season by season, each season predicted with only the seasons before it.

    GET /report-card/options                      the tasks (models, metrics, checkpoints, seasons), the design and
                                                  the build's checks
    GET /report-card/task?task=&metric=&variant=  one task: every model's score per season with its 95% interval and
                                                  rank, how often it ranked first, its pooled difference from the
                                                  task's reference model, every pair's pooled difference and how often
                                                  the order flipped, and what was chosen before each season
    GET /report-card/pair?task=&metric=&variant=&a=&b=
                                                  two models: their difference season by season and pooled

All from scripts/build_report_card.py (report_card_tests, report_card_pooled, report_card_choices,
report_card_meta). Pooling across seasons is api/report_card_lib.random_effects (DerSimonian-Laird with the
Hartung-Knapp interval and a prediction interval for a new season). Every read is cached per process: restart
impact_api after rerunning the script.
"""

import math
import warnings
from functools import lru_cache

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from report_card_lib import TASK_ORDER, TASKS, VARIANT_LABELS, badness, metric_info, model_label
from source_badge import make_source

router = APIRouter()
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

UPSTREAM = ("Rolling-origin re-runs of the platform's models (scripts/build_report_card.py) on ESPN play-by-play stints and "
            "possessions, ESPN final scores, NBA.com shot charts and Basketball-Reference BPM")
SOURCE = ["report_card_tests", "report_card_pooled", "report_card_choices", "report_card_meta"]

# What each task leaves out (shown under "Not on file").
NOT_ON_FILE = {
    "pregame": "Who played: injuries and rest are not known at forecast time (the availability-aware odds use who played at tip-off, "
               "an upper bound, so they are not on the report card).",
    "sim_playoffs": "Opening-day forecasts have no record-based baseline (no games played yet), so only the simulator's own score is shown "
                    "there. Teams in one season are not independent (16 of 30 make the playoffs); the bootstrap treats them as exchangeable.",
    "sim_top6": "The play-in began in 2020-21, so there are only six seasons; opening day has no record-based baseline.",
    "sim_wins": "Opening day has no record-based baseline. Coverage is better the closer it is to 80%, not the higher.",
    "impact_next": "The within-season held-out task and year-to-year reliabilities are not forecasts of a later season and are not scored "
                   "here (they are under the protocol, on the RAPM page and in the paper). The three-season window needs a full window to "
                   "tune on, so it starts in 2024-25.",
    "impact_poss": "A possession's predicted points come from the ten on the floor when it began; substitutions inside a possession, "
                   "and possessions in games whose stints or possessions don't reconcile, are left out.",
    "xfg": "Defender distance and shot type aren't on file for any season. 2020-21 is priced but has no season before it in the table "
           "for the baselines, so it is left out.",
}
CHOICE_LABELS = {
    ("pregame", "chosen", "form"): "Form the simulator uses",
    ("pregame", "constants", "carry"): "Carry-over of last season's rating",
    ("pregame", "constants", "tau2"): "Prior variance (tau²)",
    ("pregame", "constants", "hca_n0"): "Home court prior (games)",
    ("impact", "rapm_single", "lambda"): "RAPM λ",
    ("impact", "rapm_prior", "prior_scale"): "BPM prior scale",
    ("impact", "rapm_multi", "lambda"): "Three-season λ",
    ("impact", "bpm_scaled", "scale"): "BPM scale",
    ("impact", "onoff_scaled", "scale"): "On/off scale",
    ("impact", "rapm_tracker", "lambda_q"): "Tracker drift λq",
    ("impact", "rapm_tracker", "phi"): "Tracker carry-over φ",
    ("impact", "rapm_tracker", "prior_scale"): "Tracker BPM scale k",
    ("impact", "xrapm_sa_single", "lambda"): "Shooter-aware xRAPM λ",
    ("impact", "possessions", "level_factor"): "Points per counted / estimated possession",
    ("xfg", "constant", "fg_pct"): "League FG% the season before",
}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _f(v, d=6):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, d)


@lru_cache(maxsize=1)
def _tables():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.report_card_tests'), to_regclass('public.report_card_pooled'), "
                    "to_regclass('public.report_card_choices'), to_regclass('public.report_card_meta')")
        if None in cur.fetchone():
            return None
        tests = pd.read_sql("SELECT * FROM report_card_tests", conn)
        pooled = pd.read_sql("SELECT * FROM report_card_pooled", conn)
        choices = pd.read_sql("SELECT task, model, parameter, season, value, chosen_on, criterion, note FROM report_card_choices", conn)
        meta = dict(pd.read_sql("SELECT key, value FROM report_card_meta", conn).itertuples(index=False))
    return tests, pooled, choices, meta


def _data():
    t = _tables()
    if t is None:
        raise HTTPException(status_code=503, detail="No report-card data: run scripts/build_report_card.py.")
    return t


def _check(task, metric, variant):
    if task not in TASKS:
        raise HTTPException(status_code=400, detail=f"Unknown task '{task}'.")
    info = TASKS[task]
    metric = metric or info["metrics"][0][0]
    if metric not in [m for m, _, _ in info["metrics"]]:
        raise HTTPException(status_code=400, detail=f"Unknown metric '{metric}' for {task}.")
    variant = info["variants"][0] if variant is None else variant
    if variant not in info["variants"]:
        raise HTTPException(status_code=400, detail=f"Unknown checkpoint '{variant}' for {task}.")
    return info, metric, variant


def _pooled_dict(r, flip):
    """A stored pooled row, oriented as (a - b): flip = the request is the stored pair reversed."""
    s = -1 if flip else 1

    def neg(v):
        return None if v is None or pd.isna(v) else s * float(v)

    lo, hi = (neg(r.ci_hi), neg(r.ci_lo)) if flip else (neg(r.ci_lo), neg(r.ci_hi))
    plo, phi = (neg(r.pi_hi), neg(r.pi_lo)) if flip else (neg(r.pi_lo), neg(r.pi_hi))
    return {
        "k": int(r.k), "seasons": r.seasons, "mu": _f(neg(r.mu)), "ci_lo": _f(lo), "ci_hi": _f(hi), "p": _f(r.p),
        "pi_lo": _f(plo), "pi_hi": _f(phi), "tau": _f(r.tau), "i2": _f(r.i2), "q_p": _f(r.q_p),
        "fixed": _f(neg(r.fixed)), "se_fixed": _f(r.se_fixed),
        "a_better": int(r.b_better if flip else r.a_better), "b_better": int(r.a_better if flip else r.b_better),
        "a_clear": int(r.b_clear if flip else r.a_clear), "b_clear": int(r.a_clear if flip else r.b_clear),
        "flips": int(r.flips), "clear_flips": int(r.clear_flips),
    }


def _find_pooled(pooled, task, metric, variant, a, b):
    m = pooled[(pooled.task == task) & (pooled.metric == metric) & (pooled.variant == variant)]
    r = m[(m.model_a == a) & (m.model_b == b)]
    if len(r):
        return r.iloc[0], False
    r = m[(m.model_a == b) & (m.model_b == a)]
    if len(r):
        return r.iloc[0], True
    return None, False


@router.get("/report-card/options")
def report_card_options():
    tests, _, _, meta = _data()
    tasks = []
    for key in TASK_ORDER:
        info = TASKS[key]
        t = tests[tests.task == key]
        if t.empty:
            continue
        seasons = sorted(int(s) for s in t.season.unique())
        tasks.append({
            "key": key, "label": info["label"], "short": info["short"], "what": info["what"], "unit": info["unit"],
            "units_label": info["units_label"], "reference": info["reference"],
            "metrics": [{"key": m, "label": lab, "lower_is_better": lower} for m, lab, lower in info["metrics"]],
            "variants": [{"key": v, "label": VARIANT_LABELS[v]} for v in info["variants"]],
            "models": [{"key": m, "label": model_label(key, m)} for m in info["models"] if m in set(t.model_a)],
            "seasons": [{"season": s, "label": label(s)} for s in seasons],
        })
    return {"tasks": tasks, "design": meta.get("design"),
            "checks": {k: v for k, v in meta.items() if k.startswith("check") or k in ("impact_checks", "tracker_reused", "pregame_forms")},
            "runs": {k: v for k, v in meta.items() if k.startswith("run:")},
            "_source": make_source(SOURCE, UPSTREAM)}


@router.get("/report-card/task")
def report_card_task(task: str = Query("pregame"), metric: str | None = Query(None), variant: str | None = Query(None)):
    info, metric, variant = _check(task, metric, variant)
    tests, pooled, choices, _ = _data()
    mlabel, lower = metric_info(task, metric)
    t = tests[(tests.task == task) & (tests.metric == metric) & (tests.variant == variant)]
    if t.empty:
        raise HTTPException(status_code=404, detail="Nothing stored for this task, metric and checkpoint.")
    singles = t[t.model_b == ""]
    seasons = sorted(int(s) for s in singles.season.unique())
    models = [m for m in info["models"] if m in set(singles.model_a)]
    # per season: values, ranks (1 = best; coverage ranks by distance from 80%)
    grid = {}
    season_rows = []
    for s in seasons:
        g = singles[singles.season == s]
        vals = {r.model_a: r for r in g.itertuples()}
        order = sorted(vals, key=lambda m: badness(metric, float(vals[m].value_a)))
        ranks = {}
        for i, m in enumerate(order):
            prev = order[i - 1] if i else None
            ranks[m] = ranks[prev] if prev is not None and abs(float(vals[m].value_a) - float(vals[prev].value_a)) < 1e-12 else i + 1
        for m, r in vals.items():
            grid[(m, s)] = {"value": _f(r.value_a), "ci_lo": _f(r.ci_lo), "ci_hi": _f(r.ci_hi), "rank": ranks[m]}
        first = g.iloc[0]
        season_rows.append({"season": s, "label": label(s), "n": int(first.n), "n_clusters": int(first.n_clusters),
                            "best": order[0], "models": len(vals)})
    ref = info["reference"]
    out_models = []
    for m in models:
        per = [{"season": s, **grid[(m, s)]} if (m, s) in grid else {"season": s, "value": None, "rank": None} for s in seasons]
        ranks = [p["rank"] for p in per if p.get("rank") is not None]
        row, flip = _find_pooled(pooled, task, metric, variant, m, ref) if m != ref else (None, False)
        out_models.append({
            "key": m, "label": model_label(task, m), "seasons": per,
            "scored": len(ranks), "first": sum(1 for r in ranks if r == 1),
            "mean_rank": _f(sum(ranks) / len(ranks), 2) if ranks else None,
            "vs_reference": _pooled_dict(row, flip) if row is not None else None,
        })
    # every pair: pooled difference and flips, the most contested first
    pairs = []
    p = pooled[(pooled.task == task) & (pooled.metric == metric) & (pooled.variant == variant)]
    for r in p.itertuples():
        d = _pooled_dict(r, False)
        pairs.append({"a": r.model_a, "b": r.model_b, "a_label": model_label(task, r.model_a), "b_label": model_label(task, r.model_b), **d})
    pairs.sort(key=lambda x: (-x["flips"], x["a"], x["b"]))
    fam = "pregame" if task == "pregame" or task.startswith("sim") else "impact" if task.startswith("impact") else "xfg"
    ch = choices[choices.task == fam]
    chosen = []
    for (model, parameter), g in ch.groupby(["model", "parameter"], sort=False):
        lab = CHOICE_LABELS.get((fam, model, parameter))
        if not lab:
            continue
        g = g.sort_values("season")
        chosen.append({"model": model, "parameter": parameter, "label": lab, "criterion": g.criterion.iloc[0],
                       "values": [{"season": int(r.season), "label": label(int(r.season)), "value": r.value, "chosen_on": r.chosen_on}
                                  for r in g.itertuples()]})
    chosen.sort(key=lambda c: list(CHOICE_LABELS).index((fam, c["model"], c["parameter"])))
    return {
        "task": task, "label": info["label"], "what": info["what"], "metric": metric, "metric_label": mlabel,
        "lower_is_better": lower, "variant": variant, "variant_label": VARIANT_LABELS[variant], "reference": ref,
        "reference_label": model_label(task, ref), "unit": info["unit"], "units_label": info["units_label"],
        "seasons": season_rows, "models": out_models, "pairs": pairs, "choices": chosen,
        "not_on_file": NOT_ON_FILE.get(task),
        "_source": make_source(SOURCE, UPSTREAM),
    }


@router.get("/report-card/pair")
def report_card_pair(task: str = Query(...), a: str = Query(...), b: str = Query(...), metric: str | None = Query(None),
                     variant: str | None = Query(None)):
    info, metric, variant = _check(task, metric, variant)
    if a == b or a not in info["models"] or b not in info["models"]:
        raise HTTPException(status_code=400, detail="Pick two different models of the task.")
    tests, pooled, _, _ = _data()
    t = tests[(tests.task == task) & (tests.metric == metric) & (tests.variant == variant)]
    flip = info["models"].index(a) > info["models"].index(b)
    x, y = (b, a) if flip else (a, b)
    rows = t[(t.model_a == x) & (t.model_b == y)].sort_values("season")
    if rows.empty:
        raise HTTPException(status_code=404, detail="These two models were never scored on the same season.")
    s = -1 if flip else 1
    per = []
    for r in rows.itertuples():
        lo, hi = (-r.ci_hi, -r.ci_lo) if flip else (r.ci_lo, r.ci_hi)
        va, vb = (r.value_b, r.value_a) if flip else (r.value_a, r.value_b)
        per.append({"season": int(r.season), "label": label(int(r.season)), "diff": _f(s * r.diff), "ci_lo": _f(lo), "ci_hi": _f(hi),
                    "se": _f(r.se), "p_boot": _f(r.p_boot), "p_perm": _f(r.p_perm), "value_a": _f(va), "value_b": _f(vb),
                    "n": int(r.n), "n_clusters": int(r.n_clusters),
                    "better": "a" if badness(metric, float(va)) < badness(metric, float(vb)) else
                              "b" if badness(metric, float(vb)) < badness(metric, float(va)) else None})
    row, fl = _find_pooled(pooled, task, metric, variant, a, b)
    mlabel, lower = metric_info(task, metric)
    return {"task": task, "metric": metric, "metric_label": mlabel, "lower_is_better": lower, "variant": variant,
            "a": a, "b": b, "a_label": model_label(task, a), "b_label": model_label(task, b),
            "seasons": per, "pooled": _pooled_dict(row, fl) if row is not None else None,
            "resamples": int(rows.resamples.iloc[0]), "cluster_by": rows.cluster_by.iloc[0],
            "_source": make_source(SOURCE, UPSTREAM)}

