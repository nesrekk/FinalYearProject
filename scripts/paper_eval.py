"""
paper_eval.py
==============
One evaluation protocol for every model the conference paper compares
(round 5, step 2). The platform's own validation tables were built model by
model, each with its own split (within-season cross-validation for RAPM,
one held-out season chosen after the fact for the shot model, leave-one-
season-out for the pre-game model). This script puts them all under one
split and stores what it scored, unit by unit, so the paper's comparisons
can be resampled (step 3):

    tune      2020-21 to 2023-24   every hyperparameter and every model
                                   choice is made here;
    validate  2024-25              one look, to choose between model families
                                   (the expected-FG family, the pre-game
                                   form) and as a second out-of-sample season;
    test      2025-26              scored once, with everything frozen.

The seasons are end years (2026 = 2025-26). Where a model needs history
from before 2020-21 (the pre-game model and the simulator start in 2010-11,
the shot model trains on every season from 1996-97) the extra seasons are
training data only; the seasons scored are always the ones above.

What each model does under the protocol, and the judgment calls
-----------------------------------------------------------------
impact (RAPM, BPM, on/off; scripts/build_rapm.py's design matrix and fits)
  impact_next      ratings from season S predict the game margins of S+1,
                   with S+1 in the phase's seasons. Nothing from S+1 is used
                   for anything but scoring: the intercept and home term are
                   season S's too (the platform's validation refits those two
                   on the target season; for a game margin the intercept
                   cancels, so only the home term differs). Players without a
                   rating in S count as league average for every model.
  impact_heldout   within a season, 5 folds grouped by game (build_rapm's
                   deterministic md5 folds), each fold's games predicted by a
                   fit on the other four; hyperparameters frozen from tune,
                   so, unlike the platform's version, the fold that is scored
                   never influenced lambda. BPM here is the season's own
                   published value (same-season, so only RAPM and on/off are
                   strictly out of sample; the paper says so).
  impact_reliability  a rating's correlation between consecutive seasons among
                   players with 1,000+ possessions in both.
  Hyperparameters, all chosen on the tune pairs (2020-21->2021-22,
  2021-22->2022-23, 2022-23->2023-24) by pooled next-season game RMSE:
    rapm_single   lambda on build_rapm.LAMBDAS;
    rapm_prior    lambda = the single-season lambda by rule (the platform's
                  rule: with lambda free the choice slides to the top of the
                  grid, i.e. to BPM itself; the full grid is stored under the
                  tune phase so the reader can see it), prior scale on
                  build_rapm.PRIOR_SCALES;
    rapm_multi    lambda for the three-season window; the only tune pair
                  with a full window is 2022-23 (2020-21 to 2022-23) ->
                  2023-24, so it is chosen on one pair, and the script says so;
    bpm_scaled, onoff_scaled  one multiplier on the published rating, least
                  squares on the tune pairs' game margins; bpm and onoff are
                  the published ratings at scale 1 (what a reader of the
                  number would use); zero predicts the home edge only.
  On/off in the held-out task is recomputed from the training folds (the
  published full-season number would see the scored games).

xfg (the expected-FG model; scripts/build_shot_making.py's features and fits)
  A model that scores season T is trained on every regular-season shot
  from 1996-97 to T-1. Tune: three HistGradientBoosting configurations
  (the deployed one, a smaller and a larger tree) trained to 2022-23 and
  scored on 2023-24; the constant, zone and logistic baselines scored the
  same way. Validate: the four families trained to 2023-24 and scored on
  2024-25, which picks the family. Test: trained to 2024-25, scored once on
  2025-26. The platform picked its family on 2025-26 itself; the protocol
  moves that choice to 2024-25. The logistic regression keeps its fixed
  C = 1 and 3,000,000-shot training subsample.
  xfg_reliability  per-player expected eFG% (shot quality), actual minus
                   expected (shot-making) and raw eFG% in consecutive
                   seasons, each season scored by a model that never saw it
                   (the platform's numbers come from a cross-fit by player over
                   all seasons); floors of 200 and 500 attempts in both seasons.

pregame (api/season_sim_lib.py; the four forms of scripts/build_season_sim.py)
  The prior constants (carry, tau2, hca_n0) are fitted on the franchise pairs
  of the fit seasons only (the platform fits them on all seasons, test
  included). Tune: leave-one-season-out over 2010-11 to 2023-24 with tune
  constants. Validate: coefficients on 2010-11 to 2023-24 predict 2024-25,
  which picks the form. Test: coefficients on 2010-11 to 2024-25 and constants
  from the same seasons predict 2025-26 once.

sim (the season simulator, api/season_sim_lib.py)
  Every season is simulated 10,000 times at opening day, the halfway date
  and about 60 games in, with the coefficients and constants of its phase
  (tune seasons: the leave-one-season-out coefficients within tune) and the
  form the pre-game validation chose; the record-only and standings
  baselines as in the platform's backtest. Playoff outcomes come from
  season_postseason (built and cross-checked by build_season_sim.py).

Tables written (dropped and rebuilt; a --only run replaces its own tasks)
  paper_eval_predictions  one row per scored unit: task, phase, model,
                          variant (checkpoint, floor), season, unit_type
                          (game / shot / player / team_season), unit_id,
                          pred, actual, lo/hi (the simulator's 80% range),
                          unit_date (for time-ordered tests);
  paper_eval_metrics      summary metrics per task, phase, model, variant and
                          season (plus a pooled row for phases with several
                          seasons), with n;
  paper_eval_choices      every hyperparameter and model choice: what was
                          chosen, on which seasons, by which criterion, and
                          the candidates with their scores; plus the protocol
                          itself under task 'protocol'.
The platform's own tables are not touched.

Runtime about 25-30 minutes, most of it the six boosting fits on ~5M shots.
Deterministic: fixed seeds everywhere (build_rapm.SEED is not needed - no
bootstrap here; the shot model's random_state and subsample seed are
build_shot_making's; simulator seeds are season_sim_lib.sim_seed's). Two
runs give identical tables except paper_eval_choices' 'run' timestamp row
(fit times are printed, not stored).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_eval.py                    # everything
    cd scripts && python3 paper_eval.py --only impact,sim  # some stages
Then rerun paper_numbers.py. Rerun this after build_lineup_stints.py,
load_bref_bpm_vorp.py, a player_shots reload, or a game_scores / postseason
refresh.
"""

import argparse
import io
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

import build_rapm as R
import build_season_sim as BS
import build_shot_making as S
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import season_sim_lib as L  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

# ── The protocol ─────────────────────────────────────────────────────────────
TUNE = (2021, 2022, 2023, 2024)     # 2020-21 to 2023-24
VALIDATE = 2025                     # 2024-25
TEST = 2026                         # 2025-26
PHASE_OF = {**{s: "tune" for s in TUNE}, VALIDATE: "validate", TEST: "test"}

RUNS = L.DEFAULT_RUNS
CHECKPOINTS = ("opening", "halfway", "sixty")
RELIABILITY_FLOORS = (200, 500)     # attempts in both seasons, shot reliability
XFG_CONFIGS = {
    "deployed": dict(S.HGB_PARAMS),
    "small": {**S.HGB_PARAMS, "max_leaf_nodes": 31, "min_samples_leaf": 200, "learning_rate": 0.1},
    "large": {**S.HGB_PARAMS, "max_leaf_nodes": 127, "min_samples_leaf": 1000, "learning_rate": 0.05, "max_iter": 800},
}
STAGES = ("impact", "xfg", "pregame", "sim")


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def span(seasons):
    seasons = sorted(set(seasons))
    return label(seasons[0]) if len(seasons) == 1 else f"{label(seasons[0])} to {label(seasons[-1])}"


T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


# ── Collection ───────────────────────────────────────────────────────────────

class Out:
    """Rows for the three tables, written stage by stage."""

    def __init__(self):
        self.preds, self.metrics, self.choices = [], [], []

    def pred(self, task, phase, model, season, unit_type, unit_id, pred, actual, variant="", lo=None, hi=None, date=None):
        self.preds.append((task, phase, model, variant, int(season), unit_type, str(unit_id), _f(pred), _f(actual),
                           _f(lo), _f(hi), date))

    def metric(self, task, phase, model, seasons, metric, value, n, variant="", note=None):
        self.metrics.append((task, phase, model, variant, span(seasons) if not isinstance(seasons, str) else seasons,
                             metric, _f(value), int(n), note))

    def choice(self, task, model, parameter, value, chosen_on, criterion, candidates=None, note=None):
        self.choices.append((task, model, parameter, str(value), chosen_on, criterion,
                             psycopg2.extras.Json(candidates) if candidates is not None else None, note))

    def take(self):
        p, m, c = self.preds, self.metrics, self.choices
        self.preds, self.metrics, self.choices = [], [], []
        return p, m, c


def _f(v):
    if v is None:
        return None
    v = float(v)
    return None if np.isnan(v) else v


def rmse(p, a):
    return float(np.sqrt(np.mean((np.asarray(p) - np.asarray(a)) ** 2)))


def corr(p, a):
    return float(np.corrcoef(np.asarray(p, float), np.asarray(a, float))[0, 1]) if len(p) > 2 else None


# ── Impact: RAPM, BPM, on/off ────────────────────────────────────────────────

def by_game(design, mask, pred):
    """(game ids, predicted home margin, actual home margin) over the masked rows."""
    w, y, sign, gi = design.w[mask], design.y[mask], design.sign[mask], design.game_idx[mask]
    p = np.bincount(gi, weights=sign * pred * w / 100, minlength=len(design.games))
    a = np.bincount(gi, weights=sign * y * w / 100, minlength=len(design.games))
    present = np.bincount(gi, minlength=len(design.games)) > 0
    return np.asarray(design.games)[present], p[present], a[present]


def nuisance_of(design, beta):
    """(intercept of the design's last season, home coefficient) from a full coefficient vector."""
    return float(beta[2 * design.P + design.sidx[design.seasons[-1]]]), float(beta[2 * design.P + design.S])


class Fit:
    """A fitted rating set for one season: per-player (offence, defence) dicts and the nuisance to
    carry into the next season."""

    def __init__(self, o, d, intercept, home):
        self.o, self.d, self.intercept, self.home = o, d, intercept, home

    @classmethod
    def from_beta(cls, design, beta):
        P = design.P
        o = {p: float(beta[i]) for p, i in design.pidx.items()}
        d = {p: float(beta[P + i]) for p, i in design.pidx.items()}
        return cls(o, d, *nuisance_of(design, beta))

    def scaled(self, k):
        return Fit({p: k * v for p, v in self.o.items()}, {p: k * v for p, v in self.d.items()}, self.intercept, self.home)

    def total(self):
        return {p: self.o[p] + self.d[p] for p in self.o}


def predict_next(design_next, fit):
    """Season S ratings applied to season S+1's rows: player part at scale 1, S's intercept and home term."""
    return design_next.X @ design_next.rating_vector(fit.o, fit.d) + fit.intercept + fit.home * design_next.sign


def player_part(design_next, o, d):
    return design_next.X @ design_next.rating_vector(o, d)


def impact_stage(conn, out):
    log("impact: loading stints")
    rows, n_stints, dropped = R.load_rows(conn)
    bpm = R.load_bpm(conn)
    dates = dict(pd.read_sql_query("SELECT game_id, game_date FROM lineup_stint_games", conn).itertuples(index=False))
    seasons = sorted(int(s) for s in rows.season.unique())
    assert seasons == list(range(TUNE[0], TEST + 1)), seasons
    designs = {s: R.Design(rows[rows.season == s]) for s in seasons}
    grams = {s: designs[s].gram() for s in seasons}
    all_of = {s: np.ones(designs[s].n, bool) for s in seasons}
    log(f"impact: {n_stints} tracked stints, {len(rows)} side-rows ({dropped} dropped), seasons {seasons[0]}-{seasons[-1]}")

    def prior_vec(s):
        d = designs[s]
        od = {p: (bpm[(p, s)][0], bpm[(p, s)][1]) for p in d.players if (p, s) in bpm}
        return d.prior_vector(od), len(od)

    def zero_fit(s):
        beta, _ = R.fit_nuisance(designs[s], all_of[s], np.zeros(designs[s].ncol), False)
        return Fit({}, {}, *nuisance_of(designs[s], beta))

    def bpm_fit(s):
        z = zero_fit(s)
        return Fit({p: v[0] for (p, ss), v in bpm.items() if ss == s}, {p: v[1] for (p, ss), v in bpm.items() if ss == s},
                   z.intercept, z.home)

    onoff_full = {s: R.on_off_from_rows(designs[s], all_of[s]) for s in seasons}

    def onoff_fit(s):
        z = zero_fit(s)
        return Fit({p: v / 2 for p, v in onoff_full[s].items()}, {p: v / 2 for p, v in onoff_full[s].items()}, z.intercept, z.home)

    tune_pairs = [(s, s + 1) for s in seasons if s + 1 in TUNE]      # (2021,2022) (2022,2023) (2023,2024)

    def pooled_next(fits_by_season):
        """Pooled next-season RMSE over the tune pairs for {S: Fit}."""
        p_all, a_all = [], []
        for s, nxt in tune_pairs:
            if s not in fits_by_season:
                continue
            _, p, a = by_game(designs[nxt], all_of[nxt], predict_next(designs[nxt], fits_by_season[s]))
            p_all.append(p)
            a_all.append(a)
        return rmse(np.concatenate(p_all), np.concatenate(a_all)), sum(len(x) for x in a_all)

    # ---- tune: lambda for the single-season version --------------------------
    log("impact: tuning lambda (single)")
    grid = []
    for lam in R.LAMBDAS:
        fits = {s: Fit.from_beta(designs[s], designs[s].solve(grams[s][0], grams[s][1], lam)) for s, _ in tune_pairs}
        v, n = pooled_next(fits)
        grid.append({"lambda": lam, "game_rmse": round(v, 4)})
        out.metric("impact_next", "tune", "rapm_single", [n_ for _, n_ in tune_pairs], "game_rmse", v, n, variant=f"lambda={lam}",
                   note="tune grid: pooled next-season game RMSE at this lambda")
    lam_single = min(grid, key=lambda g: g["game_rmse"])["lambda"]
    out.choice("impact", "rapm_single", "lambda", lam_single, span(TUNE),
               "pooled next-season game-margin RMSE over the tune pairs (2020-21->2021-22, 2021-22->2022-23, 2022-23->2023-24)", grid)
    log(f"impact: lambda single = {lam_single}")

    # ---- tune: prior scale (lambda by rule = single's), full grid stored --------
    log("impact: tuning the prior scale")
    grid = []
    pv = {s: prior_vec(s)[0] for s in seasons}
    for lam in R.LAMBDAS:
        for sc in R.PRIOR_SCALES:
            fits = {s: Fit.from_beta(designs[s], designs[s].solve(grams[s][0], grams[s][1], lam, pv[s] * sc)) for s, _ in tune_pairs}
            v, n = pooled_next(fits)
            grid.append({"lambda": lam, "prior_scale": sc, "game_rmse": round(v, 4)})
            out.metric("impact_next", "tune", "rapm_prior", [n_ for _, n_ in tune_pairs], "game_rmse", v, n,
                       variant=f"lambda={lam},scale={sc}", note="tune grid: pooled next-season game RMSE at this lambda and prior scale")
    at_rule = [g for g in grid if g["lambda"] == lam_single]
    scale_prior = min(at_rule, key=lambda g: g["game_rmse"])["prior_scale"]
    free = min(grid, key=lambda g: g["game_rmse"])
    out.choice("impact", "rapm_prior", "prior_scale", scale_prior, span(TUNE),
               f"pooled next-season game-margin RMSE over the tune pairs at lambda = {lam_single} (the single-season lambda, by rule)",
               at_rule, note=f"with lambda free the grid minimum is lambda {free['lambda']}, scale {free['prior_scale']} "
                             f"(RMSE {free['game_rmse']}); the top of the grid ({max(R.LAMBDAS)}) would be BPM itself")
    out.choice("impact", "rapm_prior", "lambda", lam_single, span(TUNE), "rule: the single-season lambda (build_rapm.py's rule)",
               note=f"free grid minimum: lambda {free['lambda']}, scale {free['prior_scale']}")
    out.choice("impact", "rapm_prior", "free_minimum", json.dumps(free), span(TUNE),
               "the (lambda, prior scale) pair with the lowest pooled next-season game RMSE over the whole grid; not used, recorded",
               note=f"the top of the lambda grid ({max(R.LAMBDAS)}) would be BPM itself")
    log(f"impact: prior scale = {scale_prior} at lambda {lam_single}; free minimum lambda {free['lambda']} scale {free['prior_scale']}")

    # ---- tune: lambda for the three-season window (one pair) -------------------
    log("impact: tuning lambda (multi)")
    multi_designs = {s: R.Design(rows[rows.season.between(s - R.WINDOW + 1, s)]) for s in seasons if s - R.WINDOW + 1 >= seasons[0]}
    multi_grams = {s: d.gram() for s, d in multi_designs.items()}
    multi_pairs = [(s, n) for s, n in tune_pairs if s in multi_designs]
    grid = []
    for lam in R.LAMBDAS:
        fits = {s: Fit.from_beta(multi_designs[s], multi_designs[s].solve(multi_grams[s][0], multi_grams[s][1], lam)) for s, _ in multi_pairs}
        p_all, a_all = [], []
        for s, nxt in multi_pairs:
            _, p, a = by_game(designs[nxt], all_of[nxt], predict_next(designs[nxt], fits[s]))
            p_all.append(p)
            a_all.append(a)
        v = rmse(np.concatenate(p_all), np.concatenate(a_all))
        grid.append({"lambda": lam, "game_rmse": round(v, 4)})
        out.metric("impact_next", "tune", "rapm_multi", [n_ for _, n_ in multi_pairs], "game_rmse", v, sum(len(x) for x in a_all),
                   variant=f"lambda={lam}", note="tune grid (one pair: the only full three-season window inside tune)")
    lam_multi = min(grid, key=lambda g: g["game_rmse"])["lambda"]
    out.choice("impact", "rapm_multi", "lambda", lam_multi, span([s for s, _ in multi_pairs] + [n for _, n in multi_pairs]),
               f"next-season game-margin RMSE on the one tune pair with a full window ({label(multi_pairs[0][0])} window -> {label(multi_pairs[0][1])})",
               grid)
    log(f"impact: lambda multi = {lam_multi}")

    # ---- fits at the chosen hyperparameters, every season ----------------------
    single = {s: Fit.from_beta(designs[s], designs[s].solve(grams[s][0], grams[s][1], lam_single)) for s in seasons}
    prior = {s: Fit.from_beta(designs[s], designs[s].solve(grams[s][0], grams[s][1], lam_single, pv[s] * scale_prior)) for s in seasons}
    multi = {s: Fit.from_beta(d, d.solve(multi_grams[s][0], multi_grams[s][1], lam_multi)) for s, d in multi_designs.items()}
    zero = {s: zero_fit(s) for s in seasons}
    bpmf = {s: bpm_fit(s) for s in seasons}
    onof = {s: onoff_fit(s) for s in seasons}

    # ---- tune: one scale for the published BPM and on/off ----------------------
    scales = {}
    for name, fits in (("bpm", bpmf), ("onoff", onof)):
        num = den = 0.0
        for s, nxt in tune_pairs:
            d = designs[nxt]
            _, pn, a = by_game(d, all_of[nxt], predict_next(d, zero[s]))
            _, pp, _ = by_game(d, all_of[nxt], player_part(d, fits[s].o, fits[s].d))
            num += float(np.sum((a - pn) * pp))
            den += float(np.sum(pp * pp))
        scales[name] = num / den
        out.choice("impact", f"{name}_scaled", "scale", round(scales[name], 4), span(TUNE),
                   "least squares of the tune pairs' actual home margins (net of the zero model) on the published rating's predicted margin")
    log(f"impact: scales bpm {scales['bpm']:.3f}, on/off {scales['onoff']:.3f}")

    def models_for(s):
        m = {"rapm_single": single[s], "rapm_prior": prior[s], "zero": zero[s], "bpm": bpmf[s], "onoff": onof[s],
             "bpm_scaled": bpmf[s].scaled(scales["bpm"]), "onoff_scaled": onof[s].scaled(scales["onoff"])}
        if s in multi:
            m["rapm_multi"] = multi[s]
        return m

    # ---- impact_next: every pair, per game ---------------------------------------
    log("impact: next-season predictions")
    pooled = {}
    for s in seasons[:-1]:
        nxt = s + 1
        phase = PHASE_OF[nxt]
        d = designs[nxt]
        for model, fit in models_for(s).items():
            gids, p, a = by_game(d, all_of[nxt], predict_next(d, fit))
            for g, pi, ai in zip(gids, p, a):
                out.pred("impact_next", phase, model, nxt, "game", g, pi, ai, date=dates[g])
            out.metric("impact_next", phase, model, [nxt], "game_rmse", rmse(p, a), len(a))
            out.metric("impact_next", phase, model, [nxt], "game_corr", corr(p, a), len(a))
            if phase == "tune":
                pooled.setdefault(model, ([], []))
                pooled[model][0].append(p)
                pooled[model][1].append(a)
    for model, (ps, as_) in pooled.items():
        if len(ps) < 2:      # rapm_multi has one tune pair: its per-season row is the pooled row
            continue
        p, a = np.concatenate(ps), np.concatenate(as_)
        out.metric("impact_next", "tune", model, [n for _, n in tune_pairs], "game_rmse", rmse(p, a), len(a), note="pooled over the tune pairs")
        out.metric("impact_next", "tune", model, [n for _, n in tune_pairs], "game_corr", corr(p, a), len(a), note="pooled over the tune pairs")

    # ---- impact_heldout: within-season folds, hyperparameters frozen ------------
    log("impact: held-out games")
    pooled = {}
    for s in seasons:
        d = designs[s]
        phase = PHASE_OF[s]
        folds = sorted(set(d.fold))
        fold_grams = {k: d.gram(mask=d.fold == k) for k in folds}
        G_all, b_all = grams[s][0], grams[s][1]
        preds = {m: np.zeros(d.n) for m in ("rapm_single", "rapm_prior", "zero", "bpm", "bpm_scaled", "onoff", "onoff_scaled")}
        if s in multi_designs:
            preds["rapm_multi"] = np.zeros(d.n)
            md = multi_designs[s]
            m_this = md.rows.season.to_numpy() == s
            assert (md.rows.stint_id.to_numpy()[m_this] == d.rows.stint_id.to_numpy()).all()
            m_folds = {k: md.gram(mask=md.fold == k) for k in folds}
            mG, mb = multi_grams[s][0], multi_grams[s][1]
        for k in folds:
            test = d.fold == k
            train = ~test
            G, b = G_all - fold_grams[k][0], b_all - fold_grams[k][1]
            preds["rapm_single"][test] = d.X[test] @ d.solve(G, b, lam_single)
            preds["rapm_prior"][test] = d.X[test] @ d.solve(G, b, lam_single, pv[s] * scale_prior)
            if "rapm_multi" in preds:
                beta = md.solve(mG - m_folds[k][0], mb - m_folds[k][1], lam_multi)
                mt = m_this & (md.fold == k)
                preds["rapm_multi"][test] = md.X[mt] @ beta
            beta, _ = R.fit_nuisance(d, train, np.zeros(d.ncol), False)
            preds["zero"][test] = d.X[test] @ beta
            bvec = d.rating_vector(bpmf[s].o, bpmf[s].d)
            for name, vec in (("bpm", bvec), ("bpm_scaled", bvec * scales["bpm"])):
                beta, _ = R.fit_nuisance(d, train, vec, False)
                preds[name][test] = d.X[test] @ beta
            oo = R.on_off_from_rows(d, train)
            ovec = d.rating_vector({p: v / 2 for p, v in oo.items()}, {p: v / 2 for p, v in oo.items()})
            for name, vec in (("onoff", ovec), ("onoff_scaled", ovec * scales["onoff"])):
                beta, _ = R.fit_nuisance(d, train, vec, False)
                preds[name][test] = d.X[test] @ beta
        for model, pred in preds.items():
            gids, p, a = by_game(d, all_of[s], pred)
            for g, pi, ai in zip(gids, p, a):
                out.pred("impact_heldout", phase, model, s, "game", g, pi, ai, date=dates[g])
            out.metric("impact_heldout", phase, model, [s], "game_rmse", rmse(p, a), len(a))
            out.metric("impact_heldout", phase, model, [s], "game_corr", corr(p, a), len(a))
            if phase == "tune":
                pooled.setdefault(model, ([], [], []))
                pooled[model][0].append(p)
                pooled[model][1].append(a)
                pooled[model][2].append(s)
    for model, (ps, as_, ss) in pooled.items():
        if len(ps) < 2:
            continue
        p, a = np.concatenate(ps), np.concatenate(as_)
        out.metric("impact_heldout", "tune", model, ss, "game_rmse", rmse(p, a), len(a), note="pooled over the tune seasons")
        out.metric("impact_heldout", "tune", model, ss, "game_corr", corr(p, a), len(a), note="pooled over the tune seasons")

    # ---- impact_reliability: consecutive seasons, qualified both -----------------
    log("impact: year-to-year reliability")
    sizes = {s: R.player_sizes(designs[s]) for s in seasons}
    qualified = {s: {p for p, z in sizes[s].items() if (z["poss_off"] + z["poss_def"]) / 2 >= R.QUALIFIED_POSS} for s in seasons}
    pooled = {}
    for s in seasons[:-1]:
        nxt = s + 1
        phase = PHASE_OF[nxt]
        both = sorted(qualified[s] & qualified[nxt])
        series = {
            "rapm_single": (single[s].total(), single[nxt].total()),
            "orapm": (single[s].o, single[nxt].o), "drapm": (single[s].d, single[nxt].d),
            "rapm_prior": (prior[s].total(), prior[nxt].total()),
            "bpm": ({p: v[2] for (p, ss), v in bpm.items() if ss == s}, {p: v[2] for (p, ss), v in bpm.items() if ss == nxt}),
            "onoff": (onoff_full[s], onoff_full[nxt]),
        }
        for model, (a, b) in series.items():
            pairs = [(p, a[p], b[p]) for p in both if p in a and p in b]
            x, y = np.array([v for _, v, _ in pairs]), np.array([v for _, _, v in pairs])
            for p, xv, yv in pairs:
                out.pred("impact_reliability", phase, model, nxt, "player", p, xv, yv)
            out.metric("impact_reliability", phase, model, [nxt], "corr", corr(x, y), len(pairs))
            if phase == "tune":
                pooled.setdefault(model, ([], [], []))
                pooled[model][0].append(x)
                pooled[model][1].append(y)
                pooled[model][2].append(nxt)
    for model, (xs, ys, ss) in pooled.items():
        if len(xs) < 2:
            continue
        x, y = np.concatenate(xs), np.concatenate(ys)
        out.metric("impact_reliability", "tune", model, ss, "corr", corr(x, y), len(x), note="pooled pairs over the tune seasons")
    log("impact: done")


# ── xfg: the expected-FG model ───────────────────────────────────────────────

def load_shots_with_ids(conn):
    """build_shot_making.load_shots() plus the row id (the unit stored per shot); same order, same filter."""
    t = time.time()
    buf = io.StringIO()
    with conn.cursor() as cur:
        cur.copy_expert(
            """COPY (SELECT id, player_id, player_name, substr(season, 1, 4)::int + 1 AS season,
                            loc_x, loc_y, shot_made_flag AS made,
                            (shot_type = '3PT Field Goal')::int AS is3, period,
                            minutes_remaining * 60 + seconds_remaining AS clock
                     FROM player_shots WHERE game_id LIKE '002%' ORDER BY id)
               TO STDOUT WITH (FORMAT CSV, HEADER)""", buf)
    buf.seek(0)
    df = pd.read_csv(buf, dtype={"id": "int64", "player_id": "int32", "player_name": "string", "season": "int16",
                                 "loc_x": "int32", "loc_y": "int32", "made": "int8", "is3": "int8", "period": "int8",
                                 "clock": "int16"})
    del buf
    n0 = len(df)
    bad = (df.loc_x.abs() > 300) | (df.loc_y < -60) | (df.loc_y > 950)
    df = df[~bad].reset_index(drop=True)
    log(f"xfg: loaded {n0:,} regular-season shots in {time.time() - t:.0f}s; dropped {int(bad.sum())} with impossible locations")
    return df


def fit_hgb(X, y, params):
    m = HistGradientBoostingClassifier(categorical_features=S.HGB_CATEGORICAL, **params)
    m.fit(X, y)
    return m


def score_probs(out, phase, model, season, p, y, variant="", store=None, note=None):
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    ll, br, auc = float(log_loss(y, pc, labels=[0, 1])), float(brier_score_loss(y, pc)), float(roc_auc_score(y, pc))
    out.metric("xfg", phase, model, [season], "log_loss", ll, len(y), variant=variant, note=note)
    out.metric("xfg", phase, model, [season], "brier", br, len(y), variant=variant, note=note)
    out.metric("xfg", phase, model, [season], "roc_auc", auc, len(y), variant=variant, note=note)
    if store is not None:
        for sid, pi, yi in zip(store, p, y):
            out.pred("xfg", phase, model, season, "shot", int(sid), float(pi), float(yi), variant=variant)
    log(f"xfg: {phase:<8} {model:<12} {variant:<9} {label(season)} n={len(y):,} log loss {ll:.4f} Brier {br:.4f} AUC {auc:.3f}")
    return ll


def baselines(df, train_last, test):
    last = df[df.season == train_last]
    const = np.full(len(test), last.made.mean())
    zone_fg = last.groupby("zone").made.mean()
    return const, zone_fg.reindex(test.zone.to_numpy()).to_numpy()


def logreg_fit_predict(train, test, seasons):
    rng = np.random.default_rng(S.SEED)
    sub = rng.choice(len(train), size=min(S.LOGREG_SAMPLE, len(train)), replace=False)
    fit = S.fit_logreg(S.logreg_matrix(train.iloc[sub], seasons), train.made.to_numpy()[sub])
    return S.predict_logreg(fit, S.logreg_matrix(test, seasons))


def player_season_values(test, p):
    """Per player: attempts, eFG%, expected eFG% (quality) and the difference (shot-making)."""
    w = np.where(test.is3.to_numpy() == 1, 1.5, 1.0)
    made = test.made.to_numpy().astype(float)
    g = pd.DataFrame({"player_id": test.player_id.to_numpy(), "fga": 1, "efg": made * w, "xefg": p * w}).groupby("player_id").sum()
    g["efg"] /= g.fga
    g["xefg"] /= g.fga
    g["shot_making"] = g.efg - g.xefg
    return g


def xfg_stage(conn, out):
    df = S.add_features(load_shots_with_ids(conn))
    y_all = df.made.to_numpy()
    X_all = S.hgb_matrix(df)
    season = df.season.to_numpy()

    def train_mask(last):
        return season <= last

    def scored(t):
        return season == t

    values = {}   # season -> per-player frame from the model that scores it

    # ---- tune: three configurations trained to 2022-23, scored on 2023-24 --------
    t_score = TUNE[-1]
    tr, te = train_mask(t_score - 1), scored(t_score)
    test = df[te]
    const, zb = baselines(df, t_score - 1, test)
    score_probs(out, "tune", "constant", t_score, const, y_all[te], note=f"{label(t_score - 1)} league FG%")
    score_probs(out, "tune", "zone", t_score, zb, y_all[te], note=f"{label(t_score - 1)} FG% in the shot's zone")
    train_seasons = sorted(int(s) for s in np.unique(season[tr]))
    score_probs(out, "tune", "logreg", t_score, logreg_fit_predict(df[tr], test, train_seasons), y_all[te],
                note="C = 1, 3,000,000-shot subsample (build_shot_making's)")
    cands = []
    tune_preds = {}
    for name, params in XFG_CONFIGS.items():
        t = time.time()
        m = fit_hgb(X_all[tr], y_all[tr], params)
        p = m.predict_proba(X_all[te])[:, 1]
        tune_preds[name] = p
        log(f"xfg: hgb {name} fit in {time.time() - t:.0f}s")
        ll = score_probs(out, "tune", "hgb", t_score, p, y_all[te], variant=name, note=f"iterations {m.n_iter_}")
        cands.append({"config": name, "log_loss": round(ll, 5), "iterations": int(m.n_iter_),
                      "params": {k: v for k, v in params.items() if k in ("max_leaf_nodes", "min_samples_leaf", "learning_rate", "max_iter")}})
    config = min(cands, key=lambda c: c["log_loss"])["config"]
    out.choice("xfg", "hgb", "config", config, label(t_score),
               f"log loss on {label(t_score)} of a fit on 1996-97 to {label(t_score - 1)}", cands,
               note="candidates: build_shot_making.HGB_PARAMS (deployed), a smaller and a larger tree")
    log(f"xfg: config = {config}")
    values[t_score] = player_season_values(test, tune_preds[config])

    # ---- tune reliability pair needs 2022-23 scored by a fit to 2021-22 ------------
    t_prev = t_score - 1
    tr, te = train_mask(t_prev - 1), scored(t_prev)
    m = fit_hgb(X_all[tr], y_all[tr], XFG_CONFIGS[config])
    p = m.predict_proba(X_all[te])[:, 1]
    score_probs(out, "tune", "hgb", t_prev, p, y_all[te], variant=config, note="for the tune reliability pair")
    values[t_prev] = player_season_values(df[te], p)

    # ---- validate and test -------------------------------------------------------
    family_scores = {}
    for phase, t_score in (("validate", VALIDATE), ("test", TEST)):
        tr, te = train_mask(t_score - 1), scored(t_score)
        test = df[te]
        ids = test.id.to_numpy()
        const, zb = baselines(df, t_score - 1, test)
        ll = {}
        ll["constant"] = score_probs(out, phase, "constant", t_score, const, y_all[te], store=ids, note=f"{label(t_score - 1)} league FG%")
        ll["zone"] = score_probs(out, phase, "zone", t_score, zb, y_all[te], store=ids, note=f"{label(t_score - 1)} FG% in the shot's zone")
        train_seasons = sorted(int(s) for s in np.unique(season[tr]))
        ll["logreg"] = score_probs(out, phase, "logreg", t_score, logreg_fit_predict(df[tr], test, train_seasons), y_all[te], store=ids,
                                   note="C = 1, 3,000,000-shot subsample")
        t = time.time()
        m = fit_hgb(X_all[tr], y_all[tr], XFG_CONFIGS[config])
        p = m.predict_proba(X_all[te])[:, 1]
        log(f"xfg: hgb {config} fit in {time.time() - t:.0f}s")
        ll["hgb"] = score_probs(out, phase, "hgb", t_score, p, y_all[te], variant=config, store=ids,
                                note=f"iterations {m.n_iter_}, trained {label(int(season.min()))} to {label(t_score - 1)}")
        values[t_score] = player_season_values(test, p)
        family_scores[phase] = ll
    family = min(family_scores["validate"], key=family_scores["validate"].get)
    out.choice("xfg", "family", "model", family, label(VALIDATE), f"log loss on {label(VALIDATE)} (validate)",
               [{"model": k, "log_loss": round(v, 5)} for k, v in family_scores["validate"].items()],
               note="the platform chose its family on 2025-26 itself (build_shot_making.select_model)")
    log(f"xfg: family = {family}")

    # ---- reliability: consecutive seasons, each scored out of sample ---------------
    for s in sorted(values):
        nxt = s + 1
        if nxt not in values:
            continue
        phase = PHASE_OF[nxt]
        a, b = values[s], values[nxt]
        for floor in RELIABILITY_FLOORS:
            both = a[a.fga >= floor].join(b[b.fga >= floor], lsuffix="_a", rsuffix="_b", how="inner")
            for model, col in (("shot_making", "shot_making"), ("quality", "xefg"), ("efg", "efg")):
                x, yv = both[f"{col}_a"].to_numpy(), both[f"{col}_b"].to_numpy()
                for pid, xv, yy in zip(both.index, x, yv):
                    out.pred("xfg_reliability", phase, model, nxt, "player", int(pid), xv, yy, variant=f"fga>={floor}")
                out.metric("xfg_reliability", phase, model, [nxt], "corr", corr(x, yv), len(x), variant=f"fga>={floor}")
            log(f"xfg: reliability {label(s)}->{label(nxt)} fga>={floor}: n {len(both)}, "
                f"quality r {corr(both.xefg_a, both.xefg_b):.3f}, shot-making r {corr(both.shot_making_a, both.shot_making_b):.3f}")
    log("xfg: done")


# ── pregame and simulator ────────────────────────────────────────────────────

def load_games(conn):
    df = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where=""), conn))
    return dict(tuple(df.groupby("season")))


def score_pregame(out, phase, R_, form, seasons, note=None, store=False):
    y = R_.home_won.to_numpy(float)
    p = R_[f"p_{form}"].to_numpy(float)
    out.metric("pregame", phase, form, seasons, "log_loss", L.log_loss(p, y), len(y), note=note)
    out.metric("pregame", phase, form, seasons, "brier", L.brier(p, y), len(y), note=note)
    out.metric("pregame", phase, form, seasons, "favourite_win_rate", float(np.where(p >= 0.5, y, 1 - y).mean()), len(y), note=note)
    if store:
        for r in R_.itertuples():
            out.pred("pregame", phase, form, int(r.season), "game", r.game_id, getattr(r, f"p_{form}"), float(r.home_won), date=r.game_date)


def pregame_stage(conn, out, season_games):
    """Returns what the simulator needs per phase: {phase: (params, {season: beta}, form)}."""
    tune_seasons = [s for s in sorted(season_games) if s <= TUNE[-1] and s - 1 in season_games]     # 2011..2024
    # ---- tune: constants and leave-one-season-out within 2010-11 to 2023-24 ----
    params_tune = L.fit_params({s: g for s, g in season_games.items() if s <= TUNE[-1]})
    log(f"pregame: tune constants {json.dumps({k: round(v, 4) if isinstance(v, float) else v for k, v in params_tune.items()})}")
    R_tune = BS.build_features({s: g for s, g in season_games.items() if s <= TUNE[-1]}, params_tune)
    fits, per_season, _, loso = BS.fit_forms(R_tune)
    for form in L.FORMS:
        for s in tune_seasons:
            score_pregame(out, "tune", R_tune[R_tune.season == s], form, [s], note="leave-one-season-out within tune", store=True)
        score_pregame(out, "tune", R_tune, form, tune_seasons, note="pooled leave-one-season-out within tune")
    tune_choice = fits[fits.chosen].iloc[0].form
    for k, v in params_tune.items():
        out.choice("pregame", "constants", k, round(v, 6) if isinstance(v, float) else v, span([s for s in season_games if s <= TUNE[-1]]),
                   "season_sim_lib.fit_params on the franchise pairs of the tune seasons (used for tune and validate)")

    # ---- validate: coefficients on the tune seasons predict 2024-25 -------------
    beta_val = {form: dict(zip(L.FEATURES[form], L.logit_fit(R_tune[L.FEATURES[form]].to_numpy(float), R_tune.home_won.to_numpy(float))))
                for form in L.FORMS if form != "baseline"}
    R_val = BS.build_features({s: season_games[s] for s in (VALIDATE - 1, VALIDATE)}, params_tune)
    for form in L.FORMS:
        R_val[f"p_{form}"] = R_val.p_baseline if form == "baseline" else L.sigmoid(
            R_val[L.FEATURES[form]].to_numpy(float) @ np.array([beta_val[form][c] for c in L.FEATURES[form]]))
        score_pregame(out, "validate", R_val, form, [VALIDATE], note=f"coefficients fitted on {span(tune_seasons)}", store=True)
    ll_val = {form: L.log_loss(R_val[f"p_{form}"], R_val.home_won) for form in L.FORMS}
    form = min(ll_val, key=ll_val.get)
    out.choice("pregame", "form", "form", form, label(VALIDATE), f"log loss on {label(VALIDATE)} with coefficients fitted on {span(tune_seasons)}",
               [{"form": f, "log_loss": round(v, 5)} for f, v in ll_val.items()],
               note=f"leave-one-season-out within tune picks {tune_choice}" + (" (the same)" if tune_choice == form else " (different)"))
    log(f"pregame: validate picks {form} (tune LOSO: {tune_choice}); validate log loss "
        + ", ".join(f"{f} {v:.4f}" for f, v in ll_val.items()))

    # ---- test: constants and coefficients from 2010-11 to 2024-25, scored once on 2025-26 ----
    fit_seasons = [s for s in sorted(season_games) if s <= VALIDATE]
    params_test = L.fit_params({s: season_games[s] for s in fit_seasons})
    R_all = BS.build_features({s: g for s, g in season_games.items() if s <= TEST}, params_test)
    R_fit = R_all[R_all.season <= VALIDATE]
    R_test = R_all[R_all.season == TEST].copy()
    beta_test = {f: dict(zip(L.FEATURES[f], L.logit_fit(R_fit[L.FEATURES[f]].to_numpy(float), R_fit.home_won.to_numpy(float))))
                 for f in L.FORMS if f != "baseline"}
    for f in L.FORMS:
        R_test[f"p_{f}"] = R_test.p_baseline if f == "baseline" else L.sigmoid(
            R_test[L.FEATURES[f]].to_numpy(float) @ np.array([beta_test[f][c] for c in L.FEATURES[f]]))
        score_pregame(out, "test", R_test, f, [TEST], note=f"coefficients and constants fitted on {span(fit_seasons)}", store=True)
    for k, v in params_test.items():
        out.choice("pregame", "constants_test", k, round(v, 6) if isinstance(v, float) else v, span(fit_seasons),
                   "season_sim_lib.fit_params on the franchise pairs of the seasons before the test season (used for the test)")
    for f, b in beta_test.items():
        out.choice("pregame", f, "beta", json.dumps({k: round(v, 6) for k, v in b.items()}), span([s for s in fit_seasons if s >= 2011]),
                   "logistic regression on the games of those seasons (the test-phase coefficients)")
    log("pregame: test log loss " + ", ".join(f"{f} {L.log_loss(R_test[f'p_{f}'], R_test.home_won):.4f}" for f in L.FORMS))
    return {"tune": (params_tune, {s: loso[form][s] for s in tune_seasons}, form),
            "validate": (params_tune, {VALIDATE: beta_val[form]}, form),
            "test": (params_test, {TEST: beta_test[form]}, form)}


def sim_stage(conn, out, season_games, phases):
    facts = pd.read_sql("SELECT season, team_abbreviation, play_in, playoffs, top6, wins, games, position FROM season_postseason", conn)
    fact = facts.set_index(["season", "team_abbreviation"])
    rows = []
    for phase, (params, betas, form) in phases.items():
        for season, beta in sorted(betas.items()):
            sg = season_games[season]
            teams = sorted(sg.team_abbreviation.unique())
            prior = L.season_prior(season_games[season - 1])
            cps = L.checkpoint_dates(sg)
            for cp in CHECKPOINTS:
                cutoff = cps[cp]
                played = sg[sg.game_date < cutoff]
                left = L.home_rows(sg[sg.game_date >= cutoff])
                rat = L.ratings_as_of(played, teams, prior, params)
                st = L.Standings(teams, played)
                pos_now = L.current_positions(st, season, np.random.default_rng(L.sim_seed(season, cutoff, "now")))
                for method in (["model"] if cp == "opening" else ["model", "record", "standings"]):
                    rng = np.random.default_rng(L.sim_seed(season, cutoff, method))
                    summ = None
                    if method != "standings":
                        if method == "model":
                            r = L.draw_ratings(rat["r_post"], rat["var_post"], teams, RUNS, rng)
                            pm = L.model_p_matrix(left, st, beta, rat["hca"])
                        else:
                            r, pm = None, L.record_p_matrix(left, st)
                        summ = L.summarize(L.simulate(st, left, season, pm, r, beta, rat["hca"], RUNS, rng), st, season)
                    for t in teams:
                        f = fact.loc[(season, t)]
                        row = {"phase": phase, "season": season, "checkpoint": cp, "method": method, "team": t,
                               "made_playoffs": bool(f.playoffs), "made_top6": None if pd.isna(f.top6) else bool(f.top6),
                               "final_wins": int(f.wins)}
                        if summ is None:
                            row.update({"p_playoffs": float(pos_now[t] <= L.PLAYOFF_SPOTS),
                                        "p_top6": float(pos_now[t] <= L.DIRECT_SPOTS) if season >= L.PLAY_IN_FROM else None,
                                        "mean_wins": None, "p10": None, "p90": None})
                        else:
                            s_ = summ[t]
                            row.update({"p_playoffs": s_["p_playoffs"], "p_top6": s_["p_top6"], "mean_wins": s_["mean_wins"],
                                        "p10": s_["wins_p10"], "p90": s_["wins_p90"]})
                        rows.append(row)
            log(f"sim: {phase} {label(season)} done ({form}, {RUNS:,} runs)")
    bt = pd.DataFrame(rows)
    for r in bt.itertuples():
        uid = f"{r.season}-{r.team}"
        out.pred("sim_playoffs", r.phase, r.method, r.season, "team_season", uid, r.p_playoffs, float(r.made_playoffs), variant=r.checkpoint)
        if r.p_top6 is not None and not pd.isna(r.p_top6):
            out.pred("sim_top6", r.phase, r.method, r.season, "team_season", uid, r.p_top6, float(r.made_top6), variant=r.checkpoint)
        if r.mean_wins is not None and not pd.isna(r.mean_wins):
            out.pred("sim_wins", r.phase, r.method, r.season, "team_season", uid, r.mean_wins, float(r.final_wins), variant=r.checkpoint,
                     lo=r.p10, hi=r.p90)

    def metrics(g, phase, method, cp, seasons, note=None):
        y, p = g.made_playoffs.to_numpy(float), g.p_playoffs.to_numpy(float)
        out.metric("sim_playoffs", phase, method, seasons, "brier", L.brier(p, y), len(g), variant=cp, note=note)
        out.metric("sim_playoffs", phase, method, seasons, "log_loss", L.log_loss(p, y), len(g), variant=cp, note=note)
        t6 = g[g.p_top6.notna()]
        if len(t6):      # the play-in exists from 2020-21, so a pooled row spans only the seasons it has
            out.metric("sim_top6", phase, method, sorted(t6.season.unique()), "brier",
                       L.brier(t6.p_top6.to_numpy(float), t6.made_top6.to_numpy(float)), len(t6), variant=cp, note=note)
        if method != "standings":
            err = (g.mean_wins - g.final_wins).to_numpy(float)
            out.metric("sim_wins", phase, method, seasons, "mae", float(np.abs(err).mean()), len(g), variant=cp, note=note)
            out.metric("sim_wins", phase, method, seasons, "rmse", float(np.sqrt((err ** 2).mean())), len(g), variant=cp, note=note)
            inside = ((g.final_wins >= g.p10) & (g.final_wins <= g.p90)).mean()
            out.metric("sim_wins", phase, method, seasons, "cover80", float(inside), len(g), variant=cp, note=note)

    for (phase, method, cp, season), g in bt.groupby(["phase", "method", "checkpoint", "season"]):
        metrics(g, phase, method, cp, [season])
    for (phase, method, cp), g in bt.groupby(["phase", "method", "checkpoint"]):
        if g.season.nunique() > 1:
            metrics(g, phase, method, cp, sorted(g.season.unique()), note="pooled over the tune seasons")
    log("sim: done")


# ── Write ────────────────────────────────────────────────────────────────────

DDL = {
    "paper_eval_predictions": """
        task TEXT NOT NULL, phase TEXT NOT NULL, model TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '',
        season INTEGER NOT NULL, unit_type TEXT NOT NULL, unit_id TEXT NOT NULL,
        pred DOUBLE PRECISION, actual DOUBLE PRECISION, lo DOUBLE PRECISION, hi DOUBLE PRECISION, unit_date DATE,
        PRIMARY KEY (task, phase, model, variant, season, unit_id)""",
    "paper_eval_metrics": """
        task TEXT NOT NULL, phase TEXT NOT NULL, model TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '',
        seasons TEXT NOT NULL, metric TEXT NOT NULL, value DOUBLE PRECISION, n INTEGER NOT NULL, note TEXT,
        PRIMARY KEY (task, phase, model, variant, seasons, metric)""",
    "paper_eval_choices": """
        task TEXT NOT NULL, model TEXT NOT NULL, parameter TEXT NOT NULL, value TEXT NOT NULL,
        chosen_on TEXT NOT NULL, criterion TEXT NOT NULL, candidates JSONB, note TEXT,
        PRIMARY KEY (task, model, parameter)""",
}
TASKS_OF = {"impact": ("impact_next", "impact_heldout", "impact_reliability", "impact"),
            "xfg": ("xfg", "xfg_reliability"), "pregame": ("pregame",), "sim": ("sim_playoffs", "sim_top6", "sim_wins", "sim")}


def prepare_tables(conn, stages, everything):
    cur = conn.cursor()
    if everything:
        for t, ddl in DDL.items():
            cur.execute(f"DROP TABLE IF EXISTS {t}")
            cur.execute(f"CREATE TABLE {t} ({ddl})")
    else:
        for t, ddl in DDL.items():
            cur.execute(f"CREATE TABLE IF NOT EXISTS {t} ({ddl})")
        tasks = tuple(t for s in stages for t in TASKS_OF[s]) + ("protocol",)
        for t in DDL:
            cur.execute(f"DELETE FROM {t} WHERE task IN %s", (tasks,))
    conn.commit()


def flush(conn, out):
    p, m, c = out.take()
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_eval_predictions VALUES %s", p, page_size=5000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_eval_metrics VALUES %s", m, page_size=2000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_eval_choices VALUES %s", c)
    conn.commit()
    log(f"wrote {len(p):,} predictions, {len(m):,} metrics, {len(c)} choices")


def protocol_rows(out, stages):
    out.choice("protocol", "all", "tune_seasons", span(TUNE), span(TUNE), "the plan (paper/ROUND5_PLAN.md step 2)",
               note="every hyperparameter and model choice is made on these seasons")
    out.choice("protocol", "all", "validate_season", label(VALIDATE), label(VALIDATE), "the plan",
               note="one look: picks the expected-FG family and the pre-game form")
    out.choice("protocol", "all", "test_season", label(TEST), label(TEST), "the plan", note="scored once with everything frozen")
    out.choice("protocol", "all", "history", "pregame/sim from 2010-11 (game_scores), xfg from 1996-97 (player_shots)", "n/a",
               "the models that need history before 2020-21 use it as training data only")
    out.choice("protocol", "all", "run", datetime.now(timezone.utc).isoformat(timespec="seconds"), "n/a", "when this script last wrote its tables",
               note=f"stages: {', '.join(stages)}; simulator runs {RUNS:,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(STAGES), help="comma-separated stages: impact, xfg, pregame, sim (sim needs pregame)")
    args = ap.parse_args()
    stages = [s for s in STAGES if s in args.only.split(",")]
    if "sim" in stages and "pregame" not in stages:
        stages.insert(stages.index("sim"), "pregame")
    conn = psycopg2.connect(**DB_CONFIG)
    prepare_tables(conn, stages, everything=stages == list(STAGES))
    out = Out()
    protocol_rows(out, stages)
    flush(conn, out)
    if "impact" in stages:
        impact_stage(conn, out)
        flush(conn, out)
    if "xfg" in stages:
        xfg_stage(conn, out)
        flush(conn, out)
    if "pregame" in stages:
        season_games = load_games(conn)
        phases = pregame_stage(conn, out, season_games)
        flush(conn, out)
        if "sim" in stages:
            sim_stage(conn, out, season_games, phases)
            flush(conn, out)
    cur = conn.cursor()
    for t in DDL:
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
