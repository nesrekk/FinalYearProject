"""
build_report_card.py
====================
Model Report Card (round 6, step 10): every model scored season by season,
each season predicted with only what was known before it, and the seasons
pooled with a random-effects estimate, so the paper can say how much a
model's lead moves from one season to the next (limitation ix: the protocol
of round 5 scores one validation and one test season, so a ranking that
flips between seasons looks settled).

Rolling origin
--------------
For every target season T, each model is rebuilt from scratch with its own
selection rule run on the seasons before T only, then T is scored once:

  pre-game (api/season_sim_lib.py, the four forms of build_season_sim.py)
      T = 2012-13 to 2025-26. The prior constants (carry, tau2, hca_n0) are
      fitted on the franchise pairs of the seasons before T (game_scores
      starts in 2009-10; 2011-12 is the first T with two pairs of seasons,
      so 2012-13 is the first with a defined home-court constant), each
      form's coefficients on every game of 2010-11 to T-1, and the form
      the simulator uses ("chosen") by build_season_sim.py's own rule,
      leave-one-season-out log loss within 2010-11 to T-1.
  simulator (season_sim_lib, run through paper_eval.sim_stage unchanged)
      T = 2012-13 to 2025-26, 10,000 runs at opening day, the halfway date
      and about 60 games in, with T's constants and the chosen form's
      coefficients; the record-only (log5) and current-standings baselines.
  player impact (build_rapm.py's rows and fits, paper_eval.py's models)
      T = 2022-23 to 2025-26 (stints start in 2020-21, so 2021-22 -> 2022-23
      is the first pair with a pair before it to tune on). Ratings of T-1
      predict T's game margins at scale one with T-1's intercept and home
      term (paper_eval's impact_next). Every hyperparameter is chosen by
      paper_eval's criterion, pooled next-season game RMSE, over the pairs
      (S, S+1) with S+1 < T: one pair for 2022-23, two for 2023-24, three
      for 2024-25 (= the protocol's tune pairs, so 2024-25 must reproduce
      paper_eval's validate rows; checked), four for 2025-26. Rules carried
      over: RAPM with a prior keeps the one-season lambda; the three-season
      window needs a full window to tune on, so it starts in 2024-25; BPM
      and on/off "x scale" are least squares on those pairs; the Rating
      Tracker's five hyperparameters are re-estimated per origin with
      build_rating_tracker.py's estimator (two starts, Powell, 600
      evaluations) on those pairs.
      Also scored per possession (round 6 step 3's possessions table,
      tracked games): the offence's predicted points per possession from the
      ten on the floor when the possession began (possessions.stint_no), the
      stint row's prediction / 100 times k(T-1) = season T-1's points per
      counted possession / points per estimated possession (the ratings are
      fitted per FGA + 0.44 FTA - OREB + TOV possession, which runs ~2.3 a
      team-game over the counted ones; k is known before T and the same for
      every model). Possessions whose side has no stint row (no estimated
      possession) are dropped and counted.
  shot model (build_shot_value.py's prices, shot_value_shots)
      T = 2021-22 to 2025-26: the shooter-blind location price (p_lf: a
      fit on the seasons before T) and the shooter-aware price (p_sa) are
      look-ahead-free by construction; baselines last season's league FG%
      and last season's FG% by class (rim / mid / three). Shot Value's
      settings were chosen on 2010-11 to 2019-20, so every T scored is
      after them. 2020-21 is priced too but has no season before it in the
      table for the baselines, so it is left out. The gradient-boosting
      settings themselves are build_shot_making's, not re-tuned per T.

What is not here (judgment calls): the within-season held-out task and the
year-to-year reliabilities (not forecasts of a later season); the
availability-aware odds (they need who played, an upper bound) and the
Lineup Predictor (one test season under the protocol); the Forecast
Ledger's 2026-27 season (scored live, round 6 step 2).

Scores and tests
----------------
Per task, checkpoint (simulator) and season: every model's metric with a
95% cluster-bootstrap interval, and every pair of models' difference
(metric(A) - metric(B), negative = A lower) with its interval, bootstrap
standard error, two-sided bootstrap p and a sign-flip permutation p, all
with paper_tests' functions (cluster_weights, percentile_ci, boot_p,
sign_flip_p, row_seed, its losses and clips). Clusters: games (pre-game,
impact, possessions, shots), team-seasons (simulator). One set of resamples
per (task, checkpoint, season), shared by every model and pair in it.
No Diebold-Mariano (a season is one series; the bootstrap is the test).

Pooled across seasons (api/report_card_lib.random_effects): DerSimonian-
Laird random effects on the per-season differences and their bootstrap
standard errors, Hartung-Knapp interval (t, k-1 df, scale floored at 1),
prediction interval for a new season (t, k-2 df), tau = the between-season
standard deviation of the true difference, I^2, Cochran's Q p. Counted per
pair: seasons each model wins, seasons whose interval excludes zero on
either side, and flips = seasons whose difference has the opposite sign to
the pooled one.

Tables written (all dropped and rebuilt; --only replaces its own tasks)
  report_card_units      per unit (game / team-season): task, model, variant,
                         season, pred, actual, lo/hi (80% win range), date
  report_card_game_sums  per game for the per-possession and per-shot tasks:
                         units, sum of squared errors, sum of log loss
  report_card_tests      per task, metric, variant, season: singles
                         (model_b = '') and every pair
  report_card_pooled     per task, metric, variant, pair: the random-effects
                         estimate and the counts above
  report_card_choices    every choice per target season: what, on which
                         seasons, by which criterion, the candidates
  report_card_meta       the design, the checks, counts

Checks (stop the run if they fail): the 2024-25 and 2025-26 pre-game rows
equal paper_eval's validate and test rows (same constants, coefficients and
features; to 1e-12: the coefficients differ by summation order only); 2024-25's impact rows equal paper_eval's validate rows for every
model (same tune pairs); the 2024-25 Rating Tracker estimate reproduces
rating_tracker_fit's tune RMSE; simulator rows equal paper_eval's where the
chosen form is the protocol's (to one run in 10,000: a 1e-15 difference in a
coefficient can flip one simulated game); the shot prices' per-season log loss equals
shot_value_validation's.

Deterministic (fixed seeds: paper_tests.row_seed of each row's key;
season_sim_lib.sim_seed for the simulator). Runtime about 25 minutes, most
of it the four Rating Tracker estimations (--reuse-tracker takes them from
the last run's report_card_choices while developing, and says so in
report_card_meta) and the impact grids.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 build_report_card.py
    cd scripts && OMP_NUM_THREADS=4 python3 build_report_card.py --only pregame --resamples 500
Rerun after paper_eval.py (it reads paper_eval_predictions for the checks),
build_rating_tracker.py, build_shot_value.py / paper_xrapm.py,
build_possessions.py or a game_scores / postseason refresh; restart
impact_api (the router is lru-cached).
"""

import argparse
import copy
import json
import math
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

import build_rapm as R
import build_season_sim as BS
import paper_eval as PE
import paper_tests as PT
import rating_tracker_lib as T
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import report_card_lib as RC  # noqa: E402

L = PE.L
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

PREGAME_TARGETS = tuple(range(2013, 2027))
IMPACT_TARGETS = (2023, 2024, 2025, 2026)
XFG_TARGETS = (2022, 2023, 2024, 2025, 2026)
STAGES = ("pregame", "impact", "xfg")          # pregame includes the simulator
TASKS_OF = {"pregame": ("pregame", "sim_playoffs", "sim_top6", "sim_wins"), "impact": ("impact_next", "impact_poss"), "xfg": ("xfg",)}
AWARE = {"x": "expected-points (round 5)", "lf": "look-ahead-free expected-points", "sa": "shooter-aware expected-points"}
XNAME = {"x": "xrapm", "lf": "xrapm_lf", "sa": "xrapm_sa"}
TRACKER_MAXFEV = 600                          # build_rating_tracker.py's full run
label, span = PE.label, PE.span

T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def f_(v):
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else v


class Store:
    def __init__(self):
        self.units, self.sums, self.choices, self.meta = [], [], [], {}

    def unit(self, task, model, variant, season, unit_type, unit_id, pred, actual, lo=None, hi=None, date=None):
        self.units.append((task, model, variant, int(season), unit_type, str(unit_id), f_(pred), f_(actual), f_(lo), f_(hi), date))

    def gsum(self, task, model, season, game_id, n, sq, ll=None):
        self.sums.append((task, model, int(season), str(game_id), int(n), float(sq), f_(ll)))

    def choice(self, task, model, parameter, season, value, chosen_on, criterion, candidates=None, note=None):
        self.choices.append((task, model, parameter, int(season), str(value), chosen_on, criterion,
                             psycopg2.extras.Json(candidates) if candidates is not None else None, note))


# ── Pre-game and the simulator ───────────────────────────────────────────────

def pregame_stage(conn, st):
    season_games = PE.load_games(conn)
    all_seasons = sorted(season_games)
    phases, forms = {}, {}
    for t in PREGAME_TARGETS:
        fit_seasons = [s for s in all_seasons if s < t]
        params = L.fit_params({s: season_games[s] for s in fit_seasons})
        R_all = BS.build_features({s: season_games[s] for s in all_seasons if s <= t}, params)
        R_fit = R_all[R_all.season < t].copy()
        R_t = R_all[R_all.season == t].copy()
        y = R_fit.home_won.to_numpy(float)
        betas = {f: dict(zip(L.FEATURES[f], L.logit_fit(R_fit[L.FEATURES[f]].to_numpy(float), y).tolist()))
                 for f in L.FORMS if f != "baseline"}
        for f in L.FORMS:
            R_t[f"p_{f}"] = R_t.p_baseline if f == "baseline" else L.sigmoid(
                R_t[L.FEATURES[f]].to_numpy(float) @ np.array([betas[f][c] for c in L.FEATURES[f]]))
        fits, _, _, _ = BS.fit_forms(R_fit.copy())
        form = fits[fits.chosen].iloc[0].form
        assert form != "baseline", f"{label(t)}: leave-one-season-out picks the baseline, which the simulator can't run"
        forms[t] = form
        fit_span = span([s for s in fit_seasons if s - 1 in season_games])
        st.choice("pregame", "chosen", "form", t, form, fit_span,
                  "build_season_sim.py's rule: lowest leave-one-season-out log loss within the seasons before T",
                  [{"form": r.form, "loso_log_loss": round(float(r.loso_log_loss), 6)} for r in fits.itertuples()])
        for k in ("carry", "tau2", "hca_n0", "pairs"):
            st.choice("pregame", "constants", k, t, round(params[k], 6) if isinstance(params[k], float) else params[k],
                      span(fit_seasons), "season_sim_lib.fit_params on the franchise pairs of the seasons before T")
        for f, b in betas.items():
            st.choice("pregame", f, "beta", t, json.dumps({k: round(v, 6) for k, v in b.items()}), fit_span,
                      "logistic regression on every game of the seasons before T")
        for r in R_t.sort_values(["game_date", "game_id"]).itertuples():
            for f in L.FORMS:
                st.unit("pregame", f, "", t, "game", r.game_id, getattr(r, f"p_{f}"), float(r.home_won), date=r.game_date)
            st.unit("pregame", "chosen", "", t, "game", r.game_id, getattr(r, f"p_{form}"), float(r.home_won), date=r.game_date)
        phases[f"rolling-{t}"] = (params, {t: betas[form]}, form)
        log(f"pregame: {label(t)} carry {params['carry']:.3f} tau2 {params['tau2']:.2f} hca_n0 {params['hca_n0']:.0f}; chosen {form}; "
            + ", ".join(f"{f} {L.log_loss(R_t[f'p_{f}'], R_t.home_won):.4f}" for f in L.FORMS))

    # the simulator, through paper_eval's own stage (one 'phase' per target season)
    out = PE.Out()
    PE.sim_stage(conn, out, season_games, phases)
    for (task, phase, method, cp, season, ut, uid, pred, actual, lo, hi, date) in out.preds:
        st.unit(task, method, cp, season, ut, uid, pred, actual, lo, hi, date)
    st.meta["pregame_forms"] = {label(t): f for t, f in forms.items()}
    return forms


def check_pregame(conn, st, forms):
    """2024-25 / 2025-26 rows = paper_eval's validate / test rows (same constants, coefficients, features); the
    simulator's where the chosen form is the protocol's."""
    pe = pd.read_sql("""SELECT phase, task, model, variant, season, unit_id, pred FROM paper_eval_predictions
                        WHERE task IN ('pregame', 'sim_playoffs', 'sim_top6', 'sim_wins') AND phase IN ('validate', 'test')""", conn)
    proto_form = pd.read_sql("SELECT value FROM paper_eval_choices WHERE task = 'pregame' AND parameter = 'form'", conn).value.iloc[0]
    ours = pd.DataFrame(st.units, columns=["task", "model", "variant", "season", "unit_type", "unit_id", "pred", "actual", "lo", "hi", "date"])
    out = {}
    for task in ("pregame", "sim_playoffs", "sim_top6", "sim_wins"):
        a = ours[(ours.task == task) & ours.season.isin([PE.VALIDATE, PE.TEST]) & (ours.model != "chosen")]
        if task != "pregame":
            a = a[[forms[s] == proto_form for s in a.season]]
            if a.empty:
                out[task] = "no season where the chosen form is the protocol's"
                continue
        m = a.merge(pe[pe.task == task], on=["model", "variant", "season", "unit_id"], suffixes=("", "_pe"))
        assert len(m) == len(a), (task, len(m), len(a))
        dev = float(np.max(np.abs(m.pred - m.pred_pe)))
        # the coefficients agree to ~1e-15 (summation order); in the simulator that can flip one coin toss in one of
        # 10,000 runs, i.e. a win total moves by 1/10,000
        tol = 1e-12 if task == "pregame" else 1.5 / L.DEFAULT_RUNS
        assert dev < tol, f"{task}: rolling rows differ from paper_eval's by {dev}"
        out[task] = {"rows": len(m), "max_abs_dev": dev, "rows_differing": int((np.abs(m.pred - m.pred_pe) > 1e-12).sum())}
    log(f"pregame check vs paper_eval: {out}")
    st.meta["check_pregame_vs_paper_eval"] = {"protocol_form": proto_form, **out}


# ── Player impact ────────────────────────────────────────────────────────────

def impact_stage(conn, st, reuse_tracker):
    log("impact: loading stints")
    rows, n_stints, dropped = R.load_rows(conn)
    bpm = R.load_bpm(conn)
    dates = dict(pd.read_sql_query("SELECT game_id, game_date FROM lineup_stint_games", conn).itertuples(index=False))
    seasons = sorted(int(s) for s in rows.season.unique())
    assert seasons == list(range(PE.TUNE[0], PE.TEST + 1)), seasons
    designs = {s: R.Design(rows[rows.season == s]) for s in seasons}
    grams = {s: designs[s].gram() for s in seasons}
    all_of = {s: np.ones(designs[s].n, bool) for s in seasons}
    log(f"impact: {n_stints} tracked stints, {len(rows)} side-rows ({dropped} dropped)")

    xp = pd.read_sql_query("""SELECT stint_id, home_xpts, away_xpts, home_xpts_lf, away_xpts_lf, home_xpts_sa, away_xpts_sa
                              FROM paper_xrapm_stints""", conn).set_index("stint_id")
    home_rows = rows.home.to_numpy() == 1
    tdesigns, tgrams = {}, {}
    for v in AWARE:
        suf = "" if v == "x" else f"_{v}"
        xv = np.where(home_rows, xp[f"home_xpts{suf}"].reindex(rows.stint_id).to_numpy(), xp[f"away_xpts{suf}"].reindex(rows.stint_id).to_numpy())
        assert not np.isnan(xv).any(), f"every tracked stint needs the {v} target"
        tdesigns[v] = {}
        for s in seasons:
            d = copy.copy(designs[s])
            m = (rows.season == s).to_numpy()
            d.y = 100.0 * xv[m] / rows.poss.to_numpy(float)[m]
            tdesigns[v][s] = d
        tgrams[v] = {s: tdesigns[v][s].gram() for s in seasons}
    log("impact: expected-points targets loaded")

    pv = {}
    for s in seasons:
        d = designs[s]
        pv[s] = d.prior_vector({p: (bpm[(p, s)][0], bpm[(p, s)][1]) for p in d.players if (p, s) in bpm})

    def zero_fit(s):
        beta, _ = R.fit_nuisance(designs[s], all_of[s], np.zeros(designs[s].ncol), False)
        return PE.Fit({}, {}, *PE.nuisance_of(designs[s], beta))

    zero = {s: zero_fit(s) for s in seasons}
    bpmf = {s: PE.Fit({p: v[0] for (p, ss), v in bpm.items() if ss == s}, {p: v[1] for (p, ss), v in bpm.items() if ss == s},
                      zero[s].intercept, zero[s].home) for s in seasons}
    onoff_full = {s: R.on_off_from_rows(designs[s], all_of[s]) for s in seasons}
    onof = {s: PE.Fit({p: v / 2 for p, v in onoff_full[s].items()}, {p: v / 2 for p, v in onoff_full[s].items()},
                      zero[s].intercept, zero[s].home) for s in seasons}
    multi_designs = {s: R.Design(rows[rows.season.between(s - R.WINDOW + 1, s)]) for s in seasons if s - R.WINDOW + 1 >= seasons[0]}
    multi_grams = {s: d.gram() for s, d in multi_designs.items()}

    def xfit(s, beta):
        d = designs[s]
        o = {p: float(beta[i]) for p, i in d.pidx.items()}
        dd = {p: float(beta[d.P + i]) for p, i in d.pidx.items()}
        nb, _ = R.fit_nuisance(d, all_of[s], d.rating_vector(o, dd), False)
        return PE.Fit(o, dd, *PE.nuisance_of(d, nb))

    cache = {}

    def fit(kind, s, lam=None, scale=None):
        """kind: 'rapm' | 'multi' | 'x' | 'lf' | 'sa'; scale None = no prior."""
        key = (kind, s, lam, scale)
        if key not in cache:
            if kind == "rapm":
                d, (G, b, _) = designs[s], grams[s]
                cache[key] = PE.Fit.from_beta(d, d.solve(G, b, lam, None if scale is None else pv[s] * scale))
            elif kind == "multi":
                d, (G, b, _) = multi_designs[s], multi_grams[s]
                cache[key] = PE.Fit.from_beta(d, d.solve(G, b, lam))
            else:
                d, (G, b, _) = tdesigns[kind][s], tgrams[kind][s]
                cache[key] = xfit(s, d.solve(G, b, lam, None if scale is None else pv[s] * scale))
        return cache[key]

    def pooled(fit_of, pairs):
        p_all, a_all = [], []
        for s, nxt in pairs:
            _, p, a = PE.by_game(designs[nxt], all_of[nxt], PE.predict_next(designs[nxt], fit_of(s)))
            p_all.append(p)
            a_all.append(a)
        return PE.rmse(np.concatenate(p_all), np.concatenate(a_all)), sum(len(x) for x in a_all)

    # the Rating Tracker's data (one global player index); per-origin estimates below
    tdata = T.TrackerData(designs, bpm, folds=False)
    stored_tracker = {}
    if reuse_tracker:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('report_card_choices')")
            if cur.fetchone()[0]:
                cur.execute("SELECT season, parameter, value FROM report_card_choices WHERE task = 'impact' AND model = 'rapm_tracker'")
                for s, k, v in cur.fetchall():
                    if k in T.PARAMS:
                        stored_tracker.setdefault(int(s), {})[k] = float(v)
    tf = pd.read_sql("SELECT estimated_on, tune_rmse, tune_games, lambda0, lambda_q, lambda_b, prior_scale, phi FROM rating_tracker_fit "
                     "WHERE version = 'tracker'", conn).iloc[0]

    poss = pd.read_sql("""SELECT p.season, p.game_id, ls.stint_id, CASE WHEN p.off_home THEN 1 ELSE -1 END AS home, p.pts
                          FROM possessions p JOIN lineup_stints ls ON ls.game_id = p.game_id AND ls.stint_no = p.stint_no
                          WHERE p.tracked_ok AND ls.tracked_ok AND p.season >= %s
                          ORDER BY p.game_id, p.poss_no""", conn, params=(IMPACT_TARGETS[0] - 1,))
    counted_rate = poss.groupby("season").pts.sum() / poss.groupby("season").size()
    est_rate = {s: float(designs[s].rows.pts.sum() / designs[s].rows.poss.sum()) for s in seasons}
    log(f"impact: {len(poss):,} tracked possessions loaded")

    checks = {}
    for t in IMPACT_TARGETS:
        s_ = t - 1
        pairs = [(s, s + 1) for s in seasons[:-1] if s + 1 < t]
        on = span([x for p_ in pairs for x in p_])       # every season the pairs touch
        crit = f"pooled next-season game-margin RMSE over the pairs {', '.join(f'{label(a)} -> {label(b)}' for a, b in pairs)}"
        fits_t = {"zero": zero[s_], "bpm": bpmf[s_], "onoff": onof[s_]}
        # one-season RAPM, then the prior at that lambda (build_rapm's rule)
        lams = {}
        for kind, name in (("rapm", "rapm_single"),) + tuple((v, f"{XNAME[v]}_single") for v in AWARE):
            grid = [{"lambda": lam, "game_rmse": round(pooled(lambda s, lam=lam: fit(kind, s, lam), pairs)[0], 6)} for lam in R.LAMBDAS]
            lam = min(grid, key=lambda g: g["game_rmse"])["lambda"]
            lams[kind] = lam
            st.choice("impact", name, "lambda", t, lam, on, crit, grid)
            fits_t[name] = fit(kind, s_, lam)
            pname = "rapm_prior" if kind == "rapm" else f"{XNAME[kind]}_prior"
            grid = [{"prior_scale": sc, "game_rmse": round(pooled(lambda s, sc=sc: fit(kind, s, lam, sc), pairs)[0], 6)} for sc in R.PRIOR_SCALES]
            sc = min(grid, key=lambda g: g["game_rmse"])["prior_scale"]
            st.choice("impact", pname, "prior_scale", t, sc, on, f"{crit}, at lambda {lam} (the one-season lambda, by rule)", grid)
            st.choice("impact", pname, "lambda", t, lam, on, "rule: the one-season lambda (build_rapm.py's rule)")
            fits_t[pname] = fit(kind, s_, lam, sc)
        # three-season window: tune pairs whose window is full
        mpairs = [(s, n) for s, n in pairs if s in multi_designs]
        if mpairs and s_ in multi_designs:
            grid = [{"lambda": lam, "game_rmse": round(pooled(lambda s, lam=lam: fit("multi", s, lam), mpairs)[0], 6)} for lam in R.LAMBDAS]
            lam = min(grid, key=lambda g: g["game_rmse"])["lambda"]
            st.choice("impact", "rapm_multi", "lambda", t, lam, span([x for p_ in mpairs for x in range(p_[0] - R.WINDOW + 1, p_[1] + 1)]),
                      f"pooled next-season game-margin RMSE over the pairs with a full three-season window "
                      f"({', '.join(f'{label(a)} -> {label(b)}' for a, b in mpairs)})", grid)
            fits_t["rapm_multi"] = fit("multi", s_, lam)
        else:
            st.choice("impact", "rapm_multi", "lambda", t, "not scored", "n/a",
                      "no earlier pair with a full three-season window to tune on")
        # one scale for the published BPM and on/off
        for name, fs in (("bpm", bpmf), ("onoff", onof)):
            num = den = 0.0
            for s, nxt in pairs:
                d = designs[nxt]
                _, pn, a = PE.by_game(d, all_of[nxt], PE.predict_next(d, zero[s]))
                _, pp, _ = PE.by_game(d, all_of[nxt], PE.player_part(d, fs[s].o, fs[s].d))
                num += float(np.sum((a - pn) * pp))
                den += float(np.sum(pp * pp))
            k = num / den
            st.choice("impact", f"{name}_scaled", "scale", t, round(k, 6), on,
                      "least squares of the pairs' actual home margins (net of the zero model) on the published rating's predicted margin")
            fits_t[f"{name}_scaled"] = fs[s_].scaled(k)
        # the Rating Tracker: build_rating_tracker.py's estimator on these pairs
        if t in stored_tracker and set(stored_tracker[t]) == set(T.PARAMS):
            par, how = stored_tracker[t], "reused from the previous run's report_card_choices (--reuse-tracker)"
        else:
            log(f"impact: estimating the Rating Tracker for {label(t)} on {len(pairs)} pair(s)")
            par, info = T.estimate(tdata, lambda p_: T.next_rmse(tdata, p_, pairs)[0], starts=T.STARTS, log=log,
                                   maxfev=TRACKER_MAXFEV, name=f"tracker {label(t)}")
            how = f"build_rating_tracker.py's estimator: {info['method']}, {info['evaluations']} evaluations, best of {len(T.STARTS)} starts"
        t_rmse, t_games = T.next_rmse(tdata, par, pairs)
        for k in T.PARAMS:
            st.choice("impact", "rapm_tracker", k, t, round(par[k], 9), on, crit, note=how)
        st.choice("impact", "rapm_tracker", "tune_rmse", t, round(t_rmse, 6), on, crit, note=f"{t_games} games")
        if t == PE.VALIDATE:      # the protocol's tune pairs: must reproduce rating_tracker_fit
            assert tf.estimated_on == on, (tf.estimated_on, on)
            checks["tracker_tune_rmse_vs_rating_tracker_fit"] = abs(t_rmse - float(tf.tune_rmse))
            assert abs(t_rmse - float(tf.tune_rmse)) < 1e-6, (t_rmse, float(tf.tune_rmse))
        f = T.Filter(tdata, par, seasons=[s for s in seasons if s <= s_], keep=True)
        fits_t["rapm_tracker"] = PE.Fit(*f.ratings(s_), *f.nuisance(s_))
        log(f"impact: {label(t)} lambdas {lams}; tracker {T.fmt(par)} (tune RMSE {t_rmse:.4f})")

        # score T: game margins, then possessions
        d = designs[t]
        rowkey = pd.DataFrame({"stint_id": d.rows.stint_id.to_numpy(), "home": d.rows.home.to_numpy(), "row": np.arange(d.n)})
        pt = poss[poss.season == t].merge(rowkey, on=["stint_id", "home"], how="left")
        dropped_p = int(pt.row.isna().sum())
        pt = pt[pt.row.notna()]
        prow = pt.row.to_numpy(int)
        k_lvl = float(counted_rate[s_]) / est_rate[s_]
        gcode, gids = pd.factorize(pt.game_id)
        nper = np.bincount(gcode)
        for model in RC.TASKS["impact_next"]["models"]:
            if model not in fits_t:
                continue
            pred = PE.predict_next(d, fits_t[model])
            g, p, a = PE.by_game(d, all_of[t], pred)
            for gi, pi, ai in zip(g, p, a):
                st.unit("impact_next", model, "", t, "game", gi, pi, ai, date=dates[gi])
            pp = k_lvl * pred[prow] / 100.0
            sq = np.bincount(gcode, weights=(pt.pts.to_numpy(float) - pp) ** 2)
            for gi, n, s2 in zip(gids, nper, sq):
                st.gsum("impact_poss", model, t, gi, n, s2)
        st.choice("impact", "possessions", "level_factor", t, round(k_lvl, 6), label(s_),
                  "points per counted possession / points per estimated possession, season T-1 (same for every model)",
                  note=f"{len(pt):,} possessions scored, {dropped_p} dropped (their side has no stint row)")
        log(f"impact: {label(t)} scored ({len(pt):,} possessions, {dropped_p} dropped, level factor {k_lvl:.4f})")
    st.meta["impact_checks"] = checks
    st.meta["tracker_reused"] = sorted(label(t) for t in stored_tracker if set(stored_tracker[t]) == set(T.PARAMS))


def check_impact(conn, st):
    pe = pd.read_sql("""SELECT model, season, unit_id, pred FROM paper_eval_predictions
                        WHERE task = 'impact_next' AND phase IN ('validate', 'test')""", conn)
    ours = pd.DataFrame([u for u in st.units if u[0] == "impact_next"],
                        columns=["task", "model", "variant", "season", "unit_type", "unit_id", "pred", "actual", "lo", "hi", "date"])
    m = ours.merge(pe, on=["model", "season", "unit_id"], suffixes=("", "_pe"))
    out = {}
    for (model, season), g in m.groupby(["model", "season"]):
        out[f"{model}:{label(season)}"] = float(np.max(np.abs(g.pred - g.pred_pe)))
    v = {k: x for k, x in out.items() if k.endswith(label(PE.VALIDATE))}
    assert len(v) == len(RC.TASKS["impact_next"]["models"]) and max(v.values()) < 1e-6, v
    log(f"impact check: 2024-25 equals paper_eval's validate rows for {len(v)} models (max dev {max(v.values()):.1e}); "
        f"2025-26 identical for {sum(1 for k, x in out.items() if k.endswith(label(PE.TEST)) and x < 1e-6)} models")
    st.meta["check_impact_vs_paper_eval"] = {k: (x if x >= 1e-12 else 0.0) for k, x in sorted(out.items())}


# ── Shot model ───────────────────────────────────────────────────────────────

def xfg_stage(conn, st):
    t0 = time.time()
    df = pd.read_sql("""SELECT s.shot_id, s.season, s.made::int AS made, s.cls, s.p_lf, s.p_sa, ps.game_id
                        FROM shot_value_shots s JOIN player_shots ps ON ps.id = s.shot_id ORDER BY s.shot_id""", conn)
    log(f"xfg: {len(df):,} priced shots in {time.time() - t0:.0f}s")
    val = pd.read_sql("SELECT seasons, price, n, log_loss, brier FROM shot_value_validation WHERE cls = 'fg' AND scope = seasons", conn)
    checks = {}
    for t in XFG_TARGETS:
        prev, cur = df[df.season == t - 1], df[df.season == t]
        const = float(prev.made.mean())
        by_cls = prev.groupby("cls").made.mean()
        st.choice("xfg", "constant", "fg_pct", t, round(const, 6), label(t - 1), "last season's league FG% (shot_value_shots)")
        st.choice("xfg", "class", "fg_pct", t, json.dumps({["rim", "mid", "three"][int(c)]: round(float(v), 6) for c, v in by_cls.items()}),
                  label(t - 1), "last season's FG% by class (rim = restricted-area twos, mid = other twos, three)")
        y = cur.made.to_numpy(float)
        preds = {"sa": cur.p_sa.to_numpy(float), "lf": cur.p_lf.to_numpy(float),
                 "class": by_cls.reindex(cur.cls.to_numpy()).to_numpy(float), "constant": np.full(len(cur), const)}
        gcode, gids = pd.factorize(cur.game_id)
        n = np.bincount(gcode)
        for model, p in preds.items():
            ll = PT.loss("log_loss", "xfg", p, y)
            br = PT.loss("brier", "xfg", p, y)
            for gi, ni, s2, l2 in zip(gids, n, np.bincount(gcode, weights=br), np.bincount(gcode, weights=ll)):
                st.gsum("xfg", model, t, gi, ni, s2, l2)
            if model in ("lf", "sa"):
                ref = val[(val.seasons == label(t)) & (val.price == model)]
                assert len(ref) == 1 and int(ref.n.iloc[0]) == len(cur), (t, model)
                checks[f"{model}:{label(t)}"] = abs(float(ll.mean()) - float(ref.log_loss.iloc[0]))
        log(f"xfg: {label(t)} " + ", ".join(f"{m} {PT.loss('log_loss', 'xfg', p, y).mean():.5f}" for m, p in preds.items()))
    worst = max(checks.values())
    assert worst < 1e-5, checks
    st.meta["check_xfg_vs_shot_value_validation"] = {"max_abs_log_loss_dev": worst, "rows": len(checks)}


# ── Tests and pooling ────────────────────────────────────────────────────────

def cells(st, tasks):
    """(task, variant, season) -> (models, cluster ids, {(model, metric): per-cluster loss sums}, per-cluster counts)."""
    out = {}
    units = pd.DataFrame(st.units, columns=["task", "model", "variant", "season", "unit_type", "unit_id", "pred", "actual", "lo", "hi", "date"])
    sums = pd.DataFrame(st.sums, columns=["task", "model", "season", "game_id", "n", "sq", "ll"])
    for task in tasks:
        info = RC.TASKS[task]
        if task in ("impact_poss", "xfg"):
            src = sums[sums.task == task]
            for season, g in src.groupby("season", sort=True):
                piv = {m: x.sort_values("game_id") for m, x in g.groupby("model")}
                models = [m for m in info["models"] if m in piv]
                ids = piv[models[0]].game_id.to_numpy()
                assert all((piv[m].game_id.to_numpy() == ids).all() for m in models), (task, season)
                cnt = piv[models[0]].n.to_numpy(float)
                L_ = {}
                for m in models:
                    for metric, _, _ in info["metrics"]:
                        L_[(m, metric)] = piv[m].ll.to_numpy(float) if metric == "log_loss" else piv[m].sq.to_numpy(float)
                out[(task, "", int(season))] = (models, ids, L_, cnt)
            continue
        src = units[units.task == task]
        for (variant, season), g in src.groupby(["variant", "season"], sort=True):
            piv = {m: x.sort_values("unit_id") for m, x in g.groupby("model")}
            models = [m for m in info["models"] if m in piv]
            ids = piv[models[0]].unit_id.to_numpy()
            assert all((piv[m].unit_id.to_numpy() == ids).all() for m in models), (task, variant, season)
            L_ = {}
            for m in models:
                x = piv[m]
                for metric, _, _ in info["metrics"]:
                    if task == "sim_playoffs" and m == "standings" and metric == "log_loss":
                        continue          # 0/1: its log loss is the clip, not a forecast (paper_tests' rule)
                    if task == "sim_wins" and x.pred.isna().any():
                        continue
                    L_[(m, metric)] = PT.loss(metric, task, x.pred.to_numpy(float), x.actual.to_numpy(float),
                                              x.lo.to_numpy(float), x.hi.to_numpy(float))
            out[(task, variant, int(season))] = (models, ids, L_, np.ones(len(ids)))
    return out


def transform(metric, m):
    return np.sqrt(m) if metric in ("game_rmse", "rmse", "poss_rmse") else m


def test_rows(cellmap, resamples):
    rows = []
    for (task, variant, season), (models, ids, L_, cnt) in sorted(cellmap.items()):
        info = RC.TASKS[task]
        C = len(ids)
        keys = list(L_)
        S = np.column_stack([L_[k] for k in keys] + [cnt])
        seed = PT.row_seed("report_card", task, variant, season)
        rng = np.random.default_rng(seed)
        boot = np.empty((resamples, S.shape[1]))
        for start in range(0, resamples, PT.CHUNK):
            b = min(PT.CHUNK, resamples - start)
            boot[start:start + b] = PT.cluster_weights(rng, C, b) @ S
        means = boot[:, :-1] / boot[:, -1:]
        tot = S.sum(0)
        point = {k: float(transform(k[1], tot[i] / tot[-1])) for i, k in enumerate(keys)}
        draws = {k: transform(k[1], means[:, i]) for i, k in enumerate(keys)}
        ut = info["unit"]
        cl = "team_season" if task.startswith("sim") else "game"
        n_units = int(cnt.sum())
        for k in keys:
            lo, hi = PT.percentile_ci(draws[k])
            rows.append((task, k[1], variant, season, k[0], "", ut, cl, n_units, C, point[k], None, point[k],
                         float(np.std(draws[k], ddof=1)), lo, hi, None, None, resamples, seed))
        for i, a in enumerate(models):
            for b_ in models[i + 1:]:
                for metric, _, _ in info["metrics"]:
                    if (a, metric) not in L_ or (b_, metric) not in L_:
                        continue
                    d = draws[(a, metric)] - draws[(b_, metric)]
                    lo, hi = PT.percentile_ci(d)
                    pseed = PT.row_seed("report_card", task, metric, a, b_, variant, season)
                    p_perm = PT.sign_flip_p(np.random.default_rng(pseed), L_[(a, metric)] - L_[(b_, metric)], resamples)
                    rows.append((task, metric, variant, season, a, b_, ut, cl, n_units, C, point[(a, metric)], point[(b_, metric)],
                                 point[(a, metric)] - point[(b_, metric)], float(np.std(d, ddof=1)), lo, hi, PT.boot_p(d), p_perm,
                                 resamples, pseed))
        log(f"tests: {task} {variant or '-'} {label(season)}: {len(models)} models, {C} clusters")
    return rows


TEST_COLS = ("task", "metric", "variant", "season", "model_a", "model_b", "unit_type", "cluster_by", "n", "n_clusters",
             "value_a", "value_b", "diff", "se", "ci_lo", "ci_hi", "p_boot", "p_perm", "resamples", "seed")


def pooled_rows(tests):
    df = pd.DataFrame(tests, columns=TEST_COLS)
    df = df[df.model_b != ""]
    out = []
    for (task, metric, variant, a, b), g in df.groupby(["task", "metric", "variant", "model_a", "model_b"], sort=True):
        g = g.sort_values("season")
        re = RC.random_effects(g["diff"].to_numpy(), g.se.to_numpy())
        if re is None:
            continue
        bad_a = np.array([RC.badness(metric, v) for v in g.value_a])
        bad_b = np.array([RC.badness(metric, v) for v in g.value_b])
        sign = np.sign(re["mu"])
        flips = int(np.sum(np.sign(g["diff"].to_numpy()) == -sign)) if sign != 0 else 0
        clear_flips = int(np.sum(((g.ci_hi < 0) & (sign > 0)) | ((g.ci_lo > 0) & (sign < 0))))
        out.append((task, metric, variant, a, b, int(re["k"]), span(g.season), re["fixed"], re["se_fixed"], re["tau2"],
                    None if re["tau2"] is None else math.sqrt(re["tau2"]), re["q"], re["q_p"], re["i2"], re["mu"], re["se_hk"],
                    re["ci_lo"], re["ci_hi"], re["p"], re["pi_lo"], re["pi_hi"], int(np.sum(bad_a < bad_b)), int(np.sum(bad_b < bad_a)),
                    int(np.sum(g.ci_hi < 0)), int(np.sum(g.ci_lo > 0)), flips, clear_flips))
    return out


POOL_COLS = ("task", "metric", "variant", "model_a", "model_b", "k", "seasons", "fixed", "se_fixed", "tau2", "tau", "q", "q_p", "i2",
             "mu", "se_hk", "ci_lo", "ci_hi", "p", "pi_lo", "pi_hi", "a_better", "b_better", "a_clear", "b_clear", "flips", "clear_flips")


# ── Write ────────────────────────────────────────────────────────────────────

DDL = {
    "report_card_units": """task TEXT NOT NULL, model TEXT NOT NULL, variant TEXT NOT NULL, season INTEGER NOT NULL,
        unit_type TEXT NOT NULL, unit_id TEXT NOT NULL, pred DOUBLE PRECISION, actual DOUBLE PRECISION,
        lo DOUBLE PRECISION, hi DOUBLE PRECISION, unit_date DATE,
        PRIMARY KEY (task, model, variant, season, unit_id)""",
    "report_card_game_sums": """task TEXT NOT NULL, model TEXT NOT NULL, season INTEGER NOT NULL, game_id TEXT NOT NULL,
        n INTEGER NOT NULL, sq DOUBLE PRECISION NOT NULL, ll DOUBLE PRECISION,
        PRIMARY KEY (task, model, season, game_id)""",
    "report_card_tests": """task TEXT NOT NULL, metric TEXT NOT NULL, variant TEXT NOT NULL, season INTEGER NOT NULL,
        model_a TEXT NOT NULL, model_b TEXT NOT NULL, unit_type TEXT NOT NULL, cluster_by TEXT NOT NULL,
        n INTEGER NOT NULL, n_clusters INTEGER NOT NULL, value_a DOUBLE PRECISION NOT NULL, value_b DOUBLE PRECISION,
        diff DOUBLE PRECISION NOT NULL, se DOUBLE PRECISION NOT NULL, ci_lo DOUBLE PRECISION NOT NULL, ci_hi DOUBLE PRECISION NOT NULL,
        p_boot DOUBLE PRECISION, p_perm DOUBLE PRECISION, resamples INTEGER NOT NULL, seed BIGINT NOT NULL,
        PRIMARY KEY (task, metric, variant, season, model_a, model_b)""",
    "report_card_pooled": """task TEXT NOT NULL, metric TEXT NOT NULL, variant TEXT NOT NULL, model_a TEXT NOT NULL, model_b TEXT NOT NULL,
        k INTEGER NOT NULL, seasons TEXT NOT NULL, fixed DOUBLE PRECISION, se_fixed DOUBLE PRECISION, tau2 DOUBLE PRECISION,
        tau DOUBLE PRECISION, q DOUBLE PRECISION, q_p DOUBLE PRECISION, i2 DOUBLE PRECISION, mu DOUBLE PRECISION NOT NULL,
        se_hk DOUBLE PRECISION, ci_lo DOUBLE PRECISION, ci_hi DOUBLE PRECISION, p DOUBLE PRECISION,
        pi_lo DOUBLE PRECISION, pi_hi DOUBLE PRECISION, a_better INTEGER NOT NULL, b_better INTEGER NOT NULL,
        a_clear INTEGER NOT NULL, b_clear INTEGER NOT NULL, flips INTEGER NOT NULL, clear_flips INTEGER NOT NULL,
        PRIMARY KEY (task, metric, variant, model_a, model_b)""",
    "report_card_choices": """task TEXT NOT NULL, model TEXT NOT NULL, parameter TEXT NOT NULL, season INTEGER NOT NULL,
        value TEXT NOT NULL, chosen_on TEXT NOT NULL, criterion TEXT NOT NULL, candidates JSONB, note TEXT,
        PRIMARY KEY (task, model, parameter, season)""",
    "report_card_meta": "key TEXT PRIMARY KEY, value JSONB NOT NULL",
}


def write(conn, st, tests, pooled, stages, everything):
    tasks = tuple(t for s in stages for t in TASKS_OF[s])
    choice_tasks = tuple({"pregame": "pregame", "impact": "impact", "xfg": "xfg"}[s] for s in stages)
    cur = conn.cursor()
    for t, ddl in DDL.items():
        if everything:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(f"CREATE TABLE IF NOT EXISTS {t} ({ddl})")
    if not everything:
        for t in ("report_card_units", "report_card_game_sums", "report_card_tests", "report_card_pooled"):
            cur.execute(f"DELETE FROM {t} WHERE task IN %s", (tasks,))
        cur.execute("DELETE FROM report_card_choices WHERE task IN %s", (choice_tasks,))
    ins = psycopg2.extras.execute_values
    ins(cur, "INSERT INTO report_card_units VALUES %s", sorted(st.units, key=lambda r: r[:6]), page_size=5000)
    ins(cur, "INSERT INTO report_card_game_sums VALUES %s", sorted(st.sums, key=lambda r: r[:4]), page_size=5000)
    ins(cur, "INSERT INTO report_card_tests VALUES %s", [tuple(f_(v) if isinstance(v, float) else v for v in r) for r in tests], page_size=2000)
    ins(cur, "INSERT INTO report_card_pooled VALUES %s", [tuple(f_(v) if isinstance(v, float) else v for v in r) for r in pooled])
    ins(cur, "INSERT INTO report_card_choices VALUES %s", st.choices)
    for k, v in st.meta.items():
        cur.execute("INSERT INTO report_card_meta VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (k, psycopg2.extras.Json(v)))
    conn.commit()
    for t in DDL:
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        log(f"  {t}: {n:,} rows, {size}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(STAGES), help="comma-separated stages: pregame (with the simulator), impact, xfg")
    ap.add_argument("--resamples", type=int, default=PT.RESAMPLES)
    ap.add_argument("--reuse-tracker", action="store_true",
                    help="take the Rating Tracker's per-origin hyperparameters from the last run (developing only; recorded)")
    args = ap.parse_args()
    stages = [s for s in STAGES if s in args.only.split(",")]
    conn = psycopg2.connect(**DB_CONFIG)
    st = Store()
    if "pregame" in stages:
        forms = pregame_stage(conn, st)
        check_pregame(conn, st, forms)
    if "impact" in stages:
        impact_stage(conn, st, args.reuse_tracker)
        check_impact(conn, st)
    if "xfg" in stages:
        xfg_stage(conn, st)
    tasks = [t for s in stages for t in TASKS_OF[s]]
    tests = test_rows(cells(st, tasks), args.resamples)
    pooled = pooled_rows(tests)
    st.meta[f"run:{','.join(stages)}"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "resamples": args.resamples,
                                          "reuse_tracker": bool(args.reuse_tracker), "seconds": round(time.time() - T0)}
    st.meta["design"] = {
        "pregame_targets": span(PREGAME_TARGETS), "impact_targets": span(IMPACT_TARGETS), "xfg_targets": span(XFG_TARGETS),
        "pooling": "DerSimonian-Laird random effects on per-season differences with bootstrap standard errors; Hartung-Knapp interval "
                   "(t, k-1 df, scale floored at 1); prediction interval for a new season (t, k-2 df)",
        "bootstrap": "clusters: games (pre-game, impact, possessions, shots), team-seasons (simulator); one set of resamples per task, "
                     "checkpoint and season; paper_tests' functions and seeds",
        "simulator_runs": L.DEFAULT_RUNS,
    }
    write(conn, st, tests, pooled, stages, everything=stages == list(STAGES))
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
