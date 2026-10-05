"""
paper_ablations.py
===================
Ablations for the conference paper (round 5, step 7): take one part out of
a model at a time, re-estimate it under the protocol of scripts/paper_eval.py
(tune 2020-21 to 2023-24, validate 2024-25, test 2025-26), and put a paired
interval on the change against the full model, the way scripts/paper_tests.py
does for the paper's comparisons.

Nothing here chooses anything for the paper's models. The full models are the
ones paper_eval.py chose and stored; every ablation is scored on the same
units, and the script checks that its own full model reproduces paper_eval's
stored predictions exactly before it scores anything against it.

What is ablated
---------------
impact  (RAPM; build_rapm.py's Design and solver, paper_eval.py's next-season
         task: ratings from season S predict the game margins of S+1)
  bases     rapm_single (lambda 3,000) and rapm_prior (lambda 3,000 by rule,
            prior scale 0.5), as chosen by paper_eval.py on the tune pairs.
  lambda=L  the penalty moved over build_rapm.LAMBDAS, all else fixed (both
            bases; for rapm_prior this is how hard the stints are pulled to
            the prior, lambda -> infinity being scale x BPM itself).
  scale=K   rapm_prior's prior scale over 0, 0.25, build_rapm.PRIOR_SCALES
            and 2 at the chosen lambda; scale 0 is one-season RAPM.
  no_poss_weight  every side-row weighted equally instead of by its
            possessions (each row gets the season's mean possessions, so the
            total weight and lambda's meaning are unchanged); lambda (and the
            prior scale at it) re-chosen on the tune pairs by paper_eval's
            rules, because the best penalty moves with the weights.
  no_home   no home column in the regression and no home edge in the
            prediction (lambda, scale re-chosen the same way).
  no_home_rated  no home column when the ratings are fitted; the season
            intercept and home term are then refitted on the season's rows
            with the ratings held fixed (paper_eval's BPM treatment), so the
            prediction has a home edge again. This isolates what the home
            term does to the player ratings themselves.
  Scored: next-season game-margin RMSE and correlation (tune pooled over the
  three tune pairs, validate, test) and the rating's year-to-year correlation
  among players with 1,000+ possessions in both seasons (impact_reliability).
  The held-out-games task is not repeated.

xfg     (the expected-FG model; build_shot_making.py's features, paper_eval's
         chosen boosting configuration and training window)
  One feature group removed at a time, the model refitted on every regular-
  season shot from 1996-97 to T-1 and scored on T, for T = 2024-25
  (validate) and 2025-26 (test):
    no_coords        x, y, |x|
    no_dist_angle    distance and angle
    no_zone_value    zone and the three-point flag
    no_location      all seven geometric features at once
    no_clock_period  period and game clock
    no_season        the season
  The full model's per-shot predictions are paper_eval's stored ones (a check
  fit on the test season reproduces them exactly). Per-shot predictions of the
  ablations are not stored (1.3M rows); each game's shot count and summed
  losses are (paper_ablation_shot_games), which is all the game-clustered
  bootstrap and the sign-flip test use, so every interval can be re-derived.

sim     (the pre-game model and the season simulator; api/season_sim_lib.py)
  no_carry   last season's rating carries nothing into the prior: prior mean
             0 instead of carry x last season's SRS, and prior variance the
             ratings' mean square around zero over the same franchise pairs
             (what the carry-over regression's residual variance becomes
             without its slope); the home-court prior is kept (a league
             constant, not a team's carry-over).
  no_shrink  no prior at all: prior variance 10^12, so once a team has played
             its rating is its raw rating from the games so far (before its
             first game nothing else is known and the carried rating stands).
  no_b2b     the pre-game form without the two back-to-back flags ('prior').
  no_draw    the simulator uses each team's posterior mean in every run
             instead of drawing a rating per run (only the simulator; the
             pre-game model is the full one).
  Each variant is re-estimated exactly as paper_eval's pregame stage: the
  prior constants on the seasons before the phase, the logistic coefficients
  leave-one-season-out within tune, on the tune seasons for validate, on
  2010-11 to 2024-25 for test. Scored: pre-game log loss and Brier per game
  (task pregame), and the simulator at the halfway date, 10,000 runs, the
  full model's seeds (common random numbers): playoff Brier and log loss
  (sim_playoffs), win-total MAE, RMSE and 80% coverage (sim_wins), for the
  14 tune seasons (2010-11 to 2023-24, the same seasons paper_eval's tune
  phase simulates), 2024-25 and 2025-26. Opening day and 60 games in are not
  repeated (no_shrink has no rating at opening day).

Tests (paper_ablation_tests, the columns of paper_eval_tests): for every
ablation, diff = metric(ablation) - metric(full) on the same units, a 95%
paired cluster-bootstrap interval, the bootstrap p, a sign-flip permutation p
for mean-type metrics and a Diebold-Mariano p for game series, clustered by
game (impact_next, pregame, the shots of a game), player (reliability) or
team-season (simulator); 10,000 resamples, seeds an md5 of the row key
(paper_tests.row_seed). model_a is '<base>:<ablation>', model_b '<base>:full'.

Tables written (a stage's rows are replaced when it runs; the whole set is
dropped and rebuilt when every stage runs)
  paper_ablation_predictions  one row per scored unit (games, players, team-
                              seasons): task, base, ablation, phase, variant,
                              season, unit_id, pred, actual, lo, hi, unit_date;
  paper_ablation_shot_games   per (ablation, phase, season, game): shots and
                              summed log loss, Brier, P(make) and makes;
  paper_ablation_metrics      summary metric per task, base, ablation, phase;
  paper_ablation_tests        the paired tests above;
  paper_ablation_meta         what each ablation removes, every re-chosen
                              hyperparameter with its candidates, and checks.
paper_ablation_predictions has no primary key (an index on its text key would
be larger than the table); api/tests/test_paper_ablations.py checks that
(task, base, ablation, phase, variant, season, unit_id) is unique.
The script also writes paper/tables/ablations.tex (untracked; the paper's
Table ablations, every number a \\pn macro that paper_numbers.py defines).

Runtime about 11 minutes, most of it the 13 boosting fits on ~5M shots
(set OMP_NUM_THREADS=4). Deterministic: two full runs give content-identical
tables (checked 2026-09-30).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 paper_ablations.py
    cd scripts && python3 paper_ablations.py --only impact,sim --resamples 500   # a quick look
Then rerun paper_numbers.py. Rerun this after paper_eval.py.
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import build_rapm as R
import build_season_sim as BS
import build_shot_making as S
import paper_eval as E
import paper_tests as T
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import season_sim_lib as L  # noqa: E402
from luck_lib import srs_fit  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

TUNE, VALIDATE, TEST, PHASE_OF = E.TUNE, E.VALIDATE, E.TEST, E.PHASE_OF
PHASES = ("tune", "validate", "test")
STAGES = ("impact", "xfg", "sim")
TASKS_OF = {"impact": ("impact_next", "impact_reliability"), "xfg": ("xfg",), "sim": ("pregame", "sim_playoffs", "sim_wins")}
TABLE_PATH = Path(__file__).resolve().parent.parent / "paper" / "tables" / "ablations.tex"

PRIOR_GRID = [0.0, 0.25] + list(R.PRIOR_SCALES) + [2.0]
XFG_GROUPS = {
    "no_coords": ["x", "y", "ax"],
    "no_dist_angle": ["dist", "angle"],
    "no_zone_value": ["zone", "is3"],
    "no_location": ["x", "y", "ax", "dist", "angle", "zone", "is3"],
    "no_clock_period": ["period", "clock"],
    "no_season": ["season"],
}
SIM_ABLATIONS = ("no_carry", "no_shrink", "no_b2b", "no_draw")
NO_SHRINK_TAU2 = 1e12
CHECKPOINT = "halfway"
CLIP = 1e-6

T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def label(season):
    return E.label(season)


def span(seasons):
    return E.span(seasons)


# ── Collection ───────────────────────────────────────────────────────────────

class Out:
    def __init__(self):
        self.preds, self.shot_games, self.metrics, self.meta = [], [], [], []

    def pred(self, task, base, ablation, phase, season, unit_type, unit_id, pred, actual, variant="", lo=None, hi=None, date=None):
        self.preds.append((task, base, ablation, phase, variant, int(season), unit_type, str(unit_id), E._f(pred), E._f(actual),
                           E._f(lo), E._f(hi), date))

    def metric(self, task, base, ablation, phase, seasons, metric, value, n, variant="", note=None):
        self.metrics.append((task, base, ablation, phase, variant, span(seasons), metric, E._f(value), int(n), note))

    def put(self, key, value, note=None):
        self.meta.append((key, json.dumps(value), note))


def rmse(p, a):
    return E.rmse(p, a)


def corr(p, a):
    return E.corr(p, a)


# ── impact: RAPM ─────────────────────────────────────────────────────────────

class Variant:
    """One RAPM estimator: base, name, weights ('poss' or 'flat'), home ('fit', 'refit', 'none'), lambda, prior scale."""

    def __init__(self, base, name, lam, scale, weights="poss", home="fit"):
        self.base, self.name, self.lam, self.scale, self.weights, self.home = base, name, lam, scale, weights, home


def solve(d, gram, lam, prior=None, home=True):
    """build_rapm's ridge solve; with home=False the home column (the last) is dropped from the system."""
    G, b = gram[0], gram[1]
    if home:
        return d.solve(G, b, lam, prior)
    k = d.ncol - 1
    pen = np.zeros(k)
    pen[:2 * d.P] = lam
    Gr, br = G[:k, :k], b[:k]
    if prior is None:
        beta_r = np.linalg.solve(Gr + np.diag(pen), br)
    else:
        p0 = prior[:k]
        beta_r = p0 + np.linalg.solve(Gr + np.diag(pen), br - Gr @ p0)
    beta = np.zeros(d.ncol)
    beta[:k] = beta_r
    return beta


def impact_stage(conn, out, stored):
    log("impact: loading stints")
    rows, n_stints, dropped = R.load_rows(conn)
    bpm = R.load_bpm(conn)
    dates = dict(pd.read_sql_query("SELECT game_id, game_date FROM lineup_stint_games", conn).itertuples(index=False))
    seasons = sorted(int(s) for s in rows.season.unique())
    assert seasons == list(range(TUNE[0], TEST + 1)), seasons
    designs = {s: R.Design(rows[rows.season == s]) for s in seasons}
    grams = {s: designs[s].gram() for s in seasons}
    # Flat weights: every side-row the season's mean possessions (same total weight, so lambda keeps its meaning).
    flat_grams = {s: designs[s].gram(weights=np.full(designs[s].n, designs[s].w.mean())) for s in seasons}
    all_of = {s: np.ones(designs[s].n, bool) for s in seasons}
    pv = {s: designs[s].prior_vector({p: (bpm[(p, s)][0], bpm[(p, s)][1]) for p in designs[s].players if (p, s) in bpm})
          for s in seasons}
    log(f"impact: {n_stints:,} tracked stints, {len(rows):,} side-rows, seasons {seasons[0]}-{seasons[-1]}")

    chosen = {r[0]: r[1] for r in stored["choices"]}
    lam_single, lam_prior, scale_prior = int(chosen["rapm_single:lambda"]), int(chosen["rapm_prior:lambda"]), float(chosen["rapm_prior:prior_scale"])
    assert lam_prior == lam_single

    def fit(s, v):
        d = designs[s]
        g = grams[s] if v.weights == "poss" else flat_grams[s]
        prior = pv[s] * v.scale if v.scale else None
        beta = solve(d, g, v.lam, prior, home=v.home == "fit")
        if v.home == "refit":
            o = {p: float(beta[i]) for p, i in d.pidx.items()}
            dd = {p: float(beta[d.P + i]) for p, i in d.pidx.items()}
            nb, _ = R.fit_nuisance(d, all_of[s], d.rating_vector(o, dd), False)
            return E.Fit(o, dd, *E.nuisance_of(d, nb))
        return E.Fit.from_beta(d, beta)

    tune_pairs = [(s, s + 1) for s in seasons if s + 1 in TUNE]

    def pooled_tune(v):
        p_all, a_all = [], []
        for s, nxt in tune_pairs:
            _, p, a = E.by_game(designs[nxt], all_of[nxt], E.predict_next(designs[nxt], fit(s, v)))
            p_all.append(p)
            a_all.append(a)
        return rmse(np.concatenate(p_all), np.concatenate(a_all))

    # ---- the variants ---------------------------------------------------------
    variants = [Variant("rapm_single", "full", lam_single, 0.0), Variant("rapm_prior", "full", lam_prior, scale_prior)]
    for lam in R.LAMBDAS:
        if lam != lam_single:
            variants.append(Variant("rapm_single", f"lambda={lam}", lam, 0.0))
        if lam != lam_prior:
            variants.append(Variant("rapm_prior", f"lambda={lam}", lam, scale_prior))
    for sc in PRIOR_GRID:
        if sc != scale_prior:
            variants.append(Variant("rapm_prior", f"scale={sc:g}", lam_prior, sc))
    # Re-chosen on the tune pairs by paper_eval's rules: lambda for the one-season form, then the prior scale at that lambda.
    for name, weights, home in (("no_poss_weight", "flat", "fit"), ("no_home", "poss", "none"), ("no_home_rated", "poss", "refit")):
        grid = [{"lambda": lam, "game_rmse": round(pooled_tune(Variant("rapm_single", name, lam, 0.0, weights, home)), 4)} for lam in R.LAMBDAS]
        lam_v = min(grid, key=lambda g: g["game_rmse"])["lambda"]
        at = [{"lambda": lam_v, "prior_scale": sc,
               "game_rmse": round(pooled_tune(Variant("rapm_prior", name, lam_v, sc, weights, home)), 4)} for sc in R.PRIOR_SCALES]
        sc_v = min(at, key=lambda g: g["game_rmse"])["prior_scale"]
        out.put(f"impact:{name}:lambda", lam_v, f"re-chosen on the tune pairs ({span(TUNE)}) by pooled next-season game RMSE; candidates "
                + json.dumps(grid))
        out.put(f"impact:{name}:prior_scale", sc_v, f"re-chosen on the tune pairs at lambda {lam_v} (paper_eval's rule); candidates " + json.dumps(at))
        edge = lam_v in (R.LAMBDAS[0], R.LAMBDAS[-1])
        log(f"impact: {name}: lambda {lam_v}{' (grid edge)' if edge else ''}, prior scale {sc_v}")
        variants.append(Variant("rapm_single", name, lam_v, 0.0, weights, home))
        variants.append(Variant("rapm_prior", name, lam_v, sc_v, weights, home))

    # ---- next-season predictions and reliability, every variant ---------------
    sizes = {s: R.player_sizes(designs[s]) for s in seasons}
    qualified = {s: {p for p, z in sizes[s].items() if (z["poss_off"] + z["poss_def"]) / 2 >= R.QUALIFIED_POSS} for s in seasons}
    frames = {"impact_next": [], "impact_reliability": []}
    for v in variants:
        fits = {s: fit(s, v) for s in seasons}
        pooled, rel_pooled = ([], []), ([], [])
        for s in seasons[:-1]:
            nxt = s + 1
            phase = PHASE_OF[nxt]
            gids, p, a = E.by_game(designs[nxt], all_of[nxt], E.predict_next(designs[nxt], fits[s]))
            for g, pi, ai in zip(gids, p, a):
                out.pred("impact_next", v.base, v.name, phase, nxt, "game", g, pi, ai, date=dates[g])
            frames["impact_next"].append(pd.DataFrame({"base": v.base, "ablation": v.name, "phase": phase, "season": nxt, "unit_id": gids,
                                                       "pred": p, "actual": a, "unit_date": [dates[g] for g in gids]}))
            out.metric("impact_next", v.base, v.name, phase, [nxt], "game_rmse", rmse(p, a), len(a))
            out.metric("impact_next", v.base, v.name, phase, [nxt], "game_corr", corr(p, a), len(a))
            if phase == "tune":
                pooled[0].append(p)
                pooled[1].append(a)
            a_t, b_t = fits[s].total(), fits[nxt].total()
            both = [p_ for p_ in sorted(qualified[s] & qualified[nxt]) if p_ in a_t and p_ in b_t]
            x, y = np.array([a_t[p_] for p_ in both]), np.array([b_t[p_] for p_ in both])
            for p_, xv, yv in zip(both, x, y):
                out.pred("impact_reliability", v.base, v.name, phase, nxt, "player", p_, xv, yv)
            frames["impact_reliability"].append(pd.DataFrame({"base": v.base, "ablation": v.name, "phase": phase, "season": nxt,
                                                              "unit_id": [str(p_) for p_ in both], "pred": x, "actual": y,
                                                              "unit_date": None}))
            out.metric("impact_reliability", v.base, v.name, phase, [nxt], "corr", corr(x, y), len(x))
            if phase == "tune":
                rel_pooled[0].append(x)
                rel_pooled[1].append(y)
        p, a = np.concatenate(pooled[0]), np.concatenate(pooled[1])
        out.metric("impact_next", v.base, v.name, "tune", [n for _, n in tune_pairs], "game_rmse", rmse(p, a), len(a), note="pooled over the tune pairs")
        out.metric("impact_next", v.base, v.name, "tune", [n for _, n in tune_pairs], "game_corr", corr(p, a), len(a), note="pooled over the tune pairs")
        x, y = np.concatenate(rel_pooled[0]), np.concatenate(rel_pooled[1])
        out.metric("impact_reliability", v.base, v.name, "tune", [n for _, n in tune_pairs], "corr", corr(x, y), len(x),
                   note="pooled pairs over the tune seasons")
        out.put(f"impact:{v.base}:{v.name}:spec", {"lambda": v.lam, "prior_scale": v.scale, "weights": v.weights, "home": v.home})
    log(f"impact: {len(variants)} variants scored")
    frames = {k: pd.concat(f, ignore_index=True) for k, f in frames.items()}

    # ---- the full models reproduce paper_eval's stored rows -------------------
    for base in ("rapm_single", "rapm_prior"):
        mine = frames["impact_next"].query("base == @base and ablation == 'full'").set_index(["season", "unit_id"])
        ref = stored["impact_next"][base].set_index(["season", "unit_id"])
        assert len(mine) == len(ref) and mine.index.sort_values().equals(ref.index.sort_values()), base
        dev = float(np.max(np.abs(mine.pred - ref.pred.reindex(mine.index))))
        assert dev < 1e-9 and np.allclose(mine.actual, ref.actual.reindex(mine.index), atol=1e-9), (base, dev)
        mine_r = frames["impact_reliability"].query("base == @base and ablation == 'full'").set_index(["season", "unit_id"])
        ref_r = stored["impact_reliability"][base].set_index(["season", "unit_id"])
        dev_r = float(np.max(np.abs(mine_r.pred - ref_r.pred.reindex(mine_r.index))))
        assert len(mine_r) == len(ref_r) and dev_r < 1e-9, (base, dev_r)
        out.put(f"check:impact:{base}:max_dev_vs_paper_eval", max(dev, dev_r),
                "largest |difference| between this script's full model and paper_eval_predictions (impact_next pred, impact_reliability pred)")
        log(f"impact: {base} full model reproduces paper_eval (max |dev| {max(dev, dev_r):.1e})")
    single_at_zero = frames["impact_next"].query("base == 'rapm_prior' and ablation == 'scale=0'").pred.to_numpy()
    assert np.allclose(single_at_zero, frames["impact_next"].query("base == 'rapm_single' and ablation == 'full'").pred.to_numpy(), atol=1e-9)
    return frames


# ── xfg: feature groups ──────────────────────────────────────────────────────

def xfg_stage(conn, out, stored, check=True):
    config = stored["xfg_config"]
    params = E.XFG_CONFIGS[config]
    df = S.add_features(E.load_shots_with_ids(conn))
    y_all = df.made.to_numpy()
    season = df.season.to_numpy()
    X_all = S.hgb_matrix(df)
    ids_all = df.id.to_numpy()
    games = pd.read_sql("SELECT id, game_id FROM player_shots WHERE game_id LIKE '002%%' AND season IN %s", conn,
                        params=(tuple(f"{t - 1}-{str(t)[-2:]}" for t in (VALIDATE, TEST)),))
    game_of = pd.Series(games.game_id.to_numpy(), index=games.id.to_numpy())
    del df, games
    frames = []

    def record(ablation, phase, t, ids, p, y):
        pc = np.clip(p, CLIP, 1 - CLIP)
        ll = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
        br = (pc - y) ** 2
        g = pd.DataFrame({"game_id": game_of.reindex(ids).to_numpy(), "shots": 1, "sum_log_loss": ll, "sum_brier": br,
                          "sum_p": p, "sum_made": y}).groupby("game_id", sort=True).sum().reset_index()
        assert g.game_id.notna().all() and int(g.shots.sum()) == len(ids)
        for r in g.itertuples(index=False):
            out.shot_games.append((ablation, phase, int(t), r.game_id, int(r.shots), float(r.sum_log_loss), float(r.sum_brier),
                                   float(r.sum_p), float(r.sum_made)))
        frames.append(g.assign(ablation=ablation, phase=phase, season=int(t)))
        out.metric("xfg", "hgb", ablation, phase, [t], "log_loss", ll.mean(), len(y))
        out.metric("xfg", "hgb", ablation, phase, [t], "brier", br.mean(), len(y))
        out.metric("xfg", "hgb", ablation, phase, [t], "roc_auc", roc_auc_score(y, pc), len(y))
        log(f"xfg: {phase:<8} {ablation:<16} n={len(y):,} log loss {ll.mean():.5f} Brier {br.mean():.5f} AUC {roc_auc_score(y, pc):.4f}")
        return p

    for phase, t in (("validate", VALIDATE), ("test", TEST)):
        te = season == t
        tr = season <= t - 1
        ids, y = ids_all[te], y_all[te].astype(float)
        ref = stored["xfg"][phase].set_index("unit_id").pred
        p_full = ref.reindex(ids.astype(str)).to_numpy()
        assert not np.isnan(p_full).any(), "every scored shot has paper_eval's full-model prediction"
        record("full", phase, t, ids, p_full, y)
        if check and phase == "test":
            m = HistGradientBoostingClassifier(categorical_features=S.HGB_CATEGORICAL, **params).fit(X_all[tr], y_all[tr])
            dev = float(np.max(np.abs(m.predict_proba(X_all[te])[:, 1] - p_full)))
            assert dev < 1e-12, f"check fit differs from paper_eval's stored predictions by {dev}"
            out.put("check:xfg:full_refit_max_dev", dev, f"refit of the full model on the {label(t)} window vs paper_eval_predictions")
            log(f"xfg: check fit reproduces paper_eval's stored predictions (max |dev| {dev:.1e})")
        for ablation, drop in XFG_GROUPS.items():
            keep = [c for c in S.HGB_FEATURES if c not in drop]
            cols = [S.HGB_FEATURES.index(c) for c in keep]
            cat = [keep.index(c) for c in ("zone", "period") if c in keep]
            Xk = X_all[:, cols]
            t1 = time.time()
            m = HistGradientBoostingClassifier(categorical_features=cat or None, **params).fit(Xk[tr], y_all[tr])
            log(f"xfg: {ablation} fit in {time.time() - t1:.0f}s ({m.n_iter_} iterations)")
            record(ablation, phase, t, ids, m.predict_proba(Xk[te])[:, 1], y)
            del Xk, m
    for ablation, drop in XFG_GROUPS.items():
        out.put(f"xfg:{ablation}:drops", drop)
    out.put("xfg:config", config, "paper_eval_choices xfg/hgb/config (chosen on the tune season); training window 1996-97 to the season before")
    return pd.concat(frames, ignore_index=True)


# ── sim: pre-game model and simulator ────────────────────────────────────────

def carry_pairs(season_games):
    """The franchise pairs fit_params uses: (last season's SRS, this season's SRS)."""
    fits = {s: {L.franchise(t): r for t, r in srs_fit(sg)[0].items()} for s, sg in season_games.items()}
    xs, ys = [], []
    for s in sorted(fits):
        if s - 1 in fits:
            for f, r in fits[s].items():
                if f in fits[s - 1]:
                    xs.append(fits[s - 1][f])
                    ys.append(r)
    return np.array(xs), np.array(ys)


def params_for(variant, season_games):
    p = dict(L.fit_params(season_games))
    if variant == "no_carry":
        xs, ys = carry_pairs(season_games)
        assert abs(float((xs * ys).sum() / (xs * xs).sum()) - p["carry"]) < 1e-12 and len(xs) == p["pairs"]
        p["carry"], p["tau2"] = 0.0, float(np.mean(ys ** 2))
    elif variant == "no_shrink":
        p["tau2"] = NO_SHRINK_TAU2
    return p


def sim_stage(conn, out, stored):
    season_games = E.load_games(conn)
    facts = pd.read_sql("SELECT season, team_abbreviation, playoffs, wins FROM season_postseason", conn).set_index(["season", "team_abbreviation"])
    form_full = stored["pregame_form"]
    assert form_full == "prior_rest", form_full
    feats = {}
    for variant in ("full", "no_carry", "no_shrink"):
        tune_games = {s: g for s, g in season_games.items() if s <= TUNE[-1]}
        fit_games = {s: g for s, g in season_games.items() if s <= VALIDATE}
        p_tune, p_test = params_for(variant, tune_games), params_for(variant, fit_games)
        # build_features is per season (a season's rows depend only on it and the season before), so one call covers tune and validate.
        R_tv = BS.build_features({s: g for s, g in season_games.items() if s <= VALIDATE}, p_tune)
        R_all = BS.build_features({s: g for s, g in season_games.items() if s <= TEST}, p_test)
        feats[variant] = (p_tune, p_test, R_tv, R_all)
        for k in ("carry", "tau2", "hca_n0"):
            out.put(f"sim:{variant}:{k}", {"tune": p_tune[k], "test": p_test[k]},
                    f"prior constant fitted on {span(tune_games)} (tune, validate) and {span(fit_games)} (test)")
        log(f"sim: features for {variant} (carry {p_tune['carry']:.3f}, tau2 {p_tune['tau2']:.3g})")

    pre_frames, sim_frames = [], []
    variants = ("full",) + SIM_ABLATIONS
    for variant in variants:
        fv = variant if variant in feats else "full"
        form = "prior" if variant == "no_b2b" else form_full
        cols = L.FEATURES[form]
        p_tune, p_test, R_tv, R_all = feats[fv]
        R_tune = R_tv[R_tv.season <= TUNE[-1]].reset_index(drop=True)
        tune_seasons = sorted(R_tune.season.unique())
        # tune: leave-one-season-out coefficients within tune
        X, y = R_tune[cols].to_numpy(float), R_tune.home_won.to_numpy(float)
        p = np.zeros(len(R_tune))
        betas = {}
        for s in tune_seasons:
            trm = (R_tune.season != s).to_numpy()
            b = L.logit_fit(X[trm], y[trm])
            betas[s] = dict(zip(cols, b.tolist()))
            p[~trm] = L.sigmoid(X[~trm] @ b)
        R_tune = R_tune.assign(p=p)
        b_val = L.logit_fit(X, y)
        R_val = R_tv[R_tv.season == VALIDATE].copy()
        R_val["p"] = L.sigmoid(R_val[cols].to_numpy(float) @ b_val)
        fit_rows = R_all[R_all.season <= VALIDATE]
        b_test = L.logit_fit(fit_rows[cols].to_numpy(float), fit_rows.home_won.to_numpy(float))
        R_test = R_all[R_all.season == TEST].copy()
        R_test["p"] = L.sigmoid(R_test[cols].to_numpy(float) @ b_test)
        phases = {"tune": (p_tune, betas, R_tune), "validate": (p_tune, {VALIDATE: dict(zip(cols, b_val))}, R_val),
                  "test": (p_test, {TEST: dict(zip(cols, b_test))}, R_test)}
        out.put(f"sim:{variant}:form", form)
        out.put(f"sim:{variant}:beta_test", {k: float(v) for k, v in zip(cols, b_test)}, f"logistic coefficients fitted on 2010-11 to {label(VALIDATE)}")
        if variant != "no_draw":        # the pre-game model of no_draw is the full one
            for phase, (_, _, Rp) in phases.items():
                for r in Rp.itertuples():
                    out.pred("pregame", "pregame", variant, phase, int(r.season), "game", r.game_id, r.p, float(r.home_won), date=r.game_date)
                pre_frames.append(pd.DataFrame({"ablation": variant, "phase": phase, "season": Rp.season.to_numpy(), "unit_id": Rp.game_id.to_numpy(),
                                                "pred": Rp.p.to_numpy(float), "actual": Rp.home_won.to_numpy(float), "unit_date": Rp.game_date.to_numpy()}))
                seasons_ = sorted(Rp.season.unique())
                yy, pp = Rp.home_won.to_numpy(float), Rp.p.to_numpy(float)
                out.metric("pregame", "pregame", variant, phase, seasons_, "log_loss", L.log_loss(pp, yy), len(yy))
                out.metric("pregame", "pregame", variant, phase, seasons_, "brier", L.brier(pp, yy), len(yy))
        # the simulator at the halfway date, the full model's seeds
        for phase, (params, bts, _) in phases.items():
            for s, beta in sorted(bts.items()):
                sg = season_games[s]
                teams = sorted(sg.team_abbreviation.unique())
                cutoff = L.checkpoint_dates(sg)[CHECKPOINT]
                played = sg[sg.game_date < cutoff]
                left = L.home_rows(sg[sg.game_date >= cutoff])
                rat = L.ratings_as_of(played, teams, L.season_prior(season_games[s - 1]), params)
                st = L.Standings(teams, played)
                rng = np.random.default_rng(L.sim_seed(s, cutoff, "model"))
                var = {t: 0.0 for t in teams} if variant == "no_draw" else rat["var_post"]
                r = L.draw_ratings(rat["r_post"], var, teams, E.RUNS, rng)
                summ = L.summarize(L.simulate(st, left, s, L.model_p_matrix(left, st, beta, rat["hca"]), r, beta, rat["hca"], E.RUNS, rng), st, s)
                for t in teams:
                    f = facts.loc[(s, t)]
                    u = summ[t]
                    uid = f"{s}-{t}"
                    out.pred("sim_playoffs", "sim", variant, phase, s, "team_season", uid, u["p_playoffs"], float(f.playoffs), variant=CHECKPOINT)
                    out.pred("sim_wins", "sim", variant, phase, s, "team_season", uid, u["mean_wins"], float(f.wins), variant=CHECKPOINT,
                             lo=u["wins_p10"], hi=u["wins_p90"])
                    sim_frames.append((variant, phase, s, uid, u["p_playoffs"], float(f.playoffs), u["mean_wins"], float(f.wins),
                                       u["wins_p10"], u["wins_p90"]))
        log(f"sim: {variant} done")
    pre = pd.concat(pre_frames, ignore_index=True)
    sim = pd.DataFrame(sim_frames, columns=["ablation", "phase", "season", "unit_id", "p_playoffs", "made_playoffs", "mean_wins",
                                            "final_wins", "p10", "p90"])
    for (variant, phase), g in sim.groupby(["ablation", "phase"]):
        seasons_ = sorted(g.season.unique())
        yv, pv = g.made_playoffs.to_numpy(float), g.p_playoffs.to_numpy(float)
        out.metric("sim_playoffs", "sim", variant, phase, seasons_, "brier", L.brier(pv, yv), len(g), variant=CHECKPOINT)
        out.metric("sim_playoffs", "sim", variant, phase, seasons_, "log_loss", L.log_loss(pv, yv), len(g), variant=CHECKPOINT)
        err = (g.mean_wins - g.final_wins).to_numpy(float)
        out.metric("sim_wins", "sim", variant, phase, seasons_, "mae", float(np.abs(err).mean()), len(g), variant=CHECKPOINT)
        out.metric("sim_wins", "sim", variant, phase, seasons_, "rmse", float(np.sqrt((err ** 2).mean())), len(g), variant=CHECKPOINT)
        out.metric("sim_wins", "sim", variant, phase, seasons_, "cover80",
                   float(((g.final_wins >= g.p10) & (g.final_wins <= g.p90)).mean()), len(g), variant=CHECKPOINT)

    # ---- the full model (and no_b2b's pre-game form) reproduce paper_eval -----
    devs = {}
    for variant, form in (("full", form_full), ("no_b2b", "prior")):
        mine = pre[pre.ablation == variant].set_index(["phase", "season", "unit_id"]).pred
        ref = stored["pregame"][form].set_index(["phase", "season", "unit_id"]).pred
        assert len(mine) == len(ref), (variant, len(mine), len(ref))
        devs[f"pregame:{variant}"] = float(np.max(np.abs(mine - ref.reindex(mine.index))))
    mine = sim[sim.ablation == "full"].set_index(["phase", "season", "unit_id"])
    ref = stored["sim"].set_index(["phase", "season", "unit_id"])
    assert len(mine) == len(ref), (len(mine), len(ref))
    for c_m, c_r in (("p_playoffs", "p_playoffs"), ("mean_wins", "mean_wins"), ("p10", "lo"), ("p90", "hi")):
        devs[f"sim:{c_m}"] = float(np.max(np.abs(mine[c_m] - ref[c_r].reindex(mine.index))))
    assert max(devs.values()) < 1e-9, devs
    out.put("check:sim:max_dev_vs_paper_eval", devs, "this script's full model (and no_b2b's pre-game form) vs paper_eval_predictions")
    log(f"sim: full model reproduces paper_eval (max |dev| {max(devs.values()):.1e})")
    for a in SIM_ABLATIONS:
        out.put(f"sim:{a}:definition", {
            "no_carry": "prior mean 0 (no carry-over of last season's rating), prior variance = mean square of ratings over the franchise pairs",
            "no_shrink": f"prior variance {NO_SHRINK_TAU2:g}: a team's rating is its raw rating from the games so far",
            "no_b2b": "pre-game form without the back-to-back flags ('prior')",
            "no_draw": "simulator uses each team's posterior mean in every run (no per-run rating draw)"}[a])
    return pre, sim


# ── Tests ────────────────────────────────────────────────────────────────────

def series(df):
    d = df.copy()
    d["lo"] = d["lo"] if "lo" in d else np.nan
    d["hi"] = d["hi"] if "hi" in d else np.nan
    if "unit_date" not in d or d.unit_date.isna().all():
        d["unit_date"] = pd.Timestamp("2000-01-01")
    d["cluster"] = d.unit_id
    return T.Series(d, "")


def paired_series_tests(rows, task, frame, metrics, variant=""):
    """Every ablation against its base's full model, per phase, from per-unit frames (base, ablation, phase, ...)."""
    for (base, phase), g in frame.groupby(["base", "phase"], sort=True):
        full = series(g[g.ablation == "full"])
        for ablation, ga in g[g.ablation != "full"].groupby("ablation", sort=True):
            sa = series(ga)
            for metric in metrics:
                T.paired_rows(rows, task, phase, f"{base}:{ablation}", f"{base}:full", sa, full, metric, variant)


def shot_tests(rows, frame):
    """Game-clustered paired tests on per-game loss sums (the shot model)."""
    for phase, g in frame.groupby("phase", sort=True):
        full = g[g.ablation == "full"].set_index("game_id").sort_index()
        for ablation, ga in g[g.ablation != "full"].groupby("ablation", sort=True):
            a = ga.set_index("game_id").sort_index()
            assert a.index.equals(full.index) and (a.shots == full.shots).all()
            C = len(a)
            idx = np.arange(C)
            for metric, col in (("log_loss", "sum_log_loss"), ("brier", "sum_brier")):
                seasons = span(a.season.unique())
                seed = T.row_seed("xfg", phase, metric, f"hgb:{ablation}", "hgb:full", "", seasons)
                rng = np.random.default_rng(seed)
                la, lb, n = a[col].to_numpy(float), full[col].to_numpy(float), a.shots.to_numpy(float)
                va, vb = la.sum() / n.sum(), lb.sum() / n.sum()
                sums = T.boot_sums(rng, idx, C, [la, lb, n], rows.resamples)
                d = sums[:, 0] / sums[:, 2] - sums[:, 1] / sums[:, 2]
                p_perm = T.sign_flip_p(rng, la - lb, rows.resamples)
                lo, hi = T.percentile_ci(d)
                rows.add(task="xfg", phase=phase, metric=metric, model_a=f"hgb:{ablation}", model_b="hgb:full", seasons=seasons,
                         unit_type="shot", cluster_by="game", n=int(n.sum()), n_clusters=C, value_a=va, value_b=vb, diff=va - vb,
                         ci_lo=lo, ci_hi=hi, p_boot=T.boot_p(d), p_perm=p_perm, seed=seed)


def tests_for(stage, frames, resamples):
    rows = T.Rows(resamples)
    if stage == "impact":
        paired_series_tests(rows, "impact_next", frames["impact_next"], ("game_rmse",))
        paired_series_tests(rows, "impact_reliability", frames["impact_reliability"], ("corr",))
    elif stage == "xfg":
        shot_tests(rows, frames)
    else:
        pre, sim = frames
        paired_series_tests(rows, "pregame", pre.assign(base="pregame"), ("log_loss", "brier"))
        s = sim.assign(base="sim")
        paired_series_tests(rows, "sim_playoffs", s.rename(columns={"p_playoffs": "pred", "made_playoffs": "actual"}), ("brier", "log_loss"),
                            variant=CHECKPOINT)
        paired_series_tests(rows, "sim_wins", s.rename(columns={"mean_wins": "pred", "final_wins": "actual", "p10": "lo", "p90": "hi"}),
                            ("mae", "rmse", "cover80"), variant=CHECKPOINT)
    log(f"tests: {stage}: {len(rows.rows)} rows")
    return rows.rows


# ── Stored inputs from paper_eval ────────────────────────────────────────────

def load_stored(conn, stages):
    st = {}
    cur = conn.cursor()
    cur.execute("SELECT model || ':' || parameter, value FROM paper_eval_choices WHERE task IN ('impact', 'xfg', 'pregame')")
    st["choices"] = cur.fetchall()
    ch = dict(st["choices"])
    st["xfg_config"] = ch["hgb:config"]
    st["pregame_form"] = ch["form:form"]

    def q(sql, *params):
        return pd.read_sql(sql, conn, params=params)

    if "impact" in stages:
        st["impact_next"] = {m: q("SELECT season, unit_id, pred, actual FROM paper_eval_predictions WHERE task = 'impact_next' AND model = %s", m)
                             for m in ("rapm_single", "rapm_prior")}
        st["impact_reliability"] = {m: q("SELECT season, unit_id, pred FROM paper_eval_predictions WHERE task = 'impact_reliability' AND model = %s", m)
                                    for m in ("rapm_single", "rapm_prior")}
    if "xfg" in stages:
        st["xfg"] = {ph: q("SELECT unit_id, pred FROM paper_eval_predictions WHERE task = 'xfg' AND model = 'hgb' AND phase = %s AND variant = %s",
                           ph, st["xfg_config"]) for ph in ("validate", "test")}
    if "sim" in stages:
        st["pregame"] = {f: q("SELECT phase, season, unit_id, pred FROM paper_eval_predictions WHERE task = 'pregame' AND model = %s", f)
                         for f in ("prior_rest", "prior")}
        a = q("""SELECT phase, season, unit_id, pred AS p_playoffs FROM paper_eval_predictions
                 WHERE task = 'sim_playoffs' AND model = 'model' AND variant = %s""", CHECKPOINT)
        b = q("""SELECT phase, season, unit_id, pred AS mean_wins, lo, hi FROM paper_eval_predictions
                 WHERE task = 'sim_wins' AND model = 'model' AND variant = %s""", CHECKPOINT)
        st["sim"] = a.merge(b, on=["phase", "season", "unit_id"])
    return st


# ── Write ────────────────────────────────────────────────────────────────────

DDL = {
    "paper_ablation_predictions": """
        task TEXT NOT NULL, base TEXT NOT NULL, ablation TEXT NOT NULL, phase TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '',
        season INTEGER NOT NULL, unit_type TEXT NOT NULL, unit_id TEXT NOT NULL,
        pred DOUBLE PRECISION, actual DOUBLE PRECISION, lo DOUBLE PRECISION, hi DOUBLE PRECISION, unit_date DATE""",
    "paper_ablation_shot_games": """
        ablation TEXT NOT NULL, phase TEXT NOT NULL, season INTEGER NOT NULL, game_id TEXT NOT NULL, shots INTEGER NOT NULL,
        sum_log_loss DOUBLE PRECISION NOT NULL, sum_brier DOUBLE PRECISION NOT NULL, sum_p DOUBLE PRECISION NOT NULL,
        sum_made DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (ablation, phase, season, game_id)""",
    "paper_ablation_metrics": """
        task TEXT NOT NULL, base TEXT NOT NULL, ablation TEXT NOT NULL, phase TEXT NOT NULL, variant TEXT NOT NULL DEFAULT '',
        seasons TEXT NOT NULL, metric TEXT NOT NULL, value DOUBLE PRECISION, n INTEGER NOT NULL, note TEXT,
        PRIMARY KEY (task, base, ablation, phase, variant, seasons, metric)""",
    "paper_ablation_tests": T.DDL,
    "paper_ablation_meta": "key TEXT PRIMARY KEY, value JSONB NOT NULL, note TEXT",
}


def prepare_tables(conn, stages, everything):
    cur = conn.cursor()
    for t, ddl in DDL.items():
        if everything:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(f"CREATE TABLE IF NOT EXISTS {t} ({ddl})")
    if not everything:
        tasks = tuple(t for s in stages for t in TASKS_OF[s])
        for t in ("paper_ablation_predictions", "paper_ablation_metrics", "paper_ablation_tests"):
            cur.execute(f"DELETE FROM {t} WHERE task IN %s", (tasks,))
        if "xfg" in stages:
            cur.execute("DELETE FROM paper_ablation_shot_games")
        prefixes = tuple(f"{s}:%" for s in stages) + tuple(f"check:{s}:%" for s in stages) + ("run:%", "table:%")
        cur.execute("DELETE FROM paper_ablation_meta WHERE " + " OR ".join(["key LIKE %s"] * len(prefixes)), prefixes)
    conn.commit()


def flush(conn, out, test_rows):
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_ablation_predictions VALUES %s", out.preds, page_size=5000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_ablation_shot_games VALUES %s", out.shot_games, page_size=5000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_ablation_metrics VALUES %s", out.metrics, page_size=2000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_ablation_meta VALUES %s", out.meta)
    rows = sorted(test_rows, key=lambda r: tuple(str(x) for x in r[:7]))
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_ablation_tests VALUES %s", rows, page_size=1000)
    conn.commit()
    log(f"wrote {len(out.preds):,} predictions, {len(out.shot_games):,} shot-game rows, {len(out.metrics):,} metrics, "
        f"{len(out.meta)} meta, {len(rows):,} tests")
    out.preds, out.shot_games, out.metrics, out.meta = [], [], [], []


# ── The paper's table ────────────────────────────────────────────────────────

# The rows of the paper's Table ablations. Each row is one paired test of paper_ablation_tests (task, model_a =
# base:ablation, metric; variant = the checkpoint for the simulator); paper_numbers.py prints its tune, validate and test
# differences and the test interval as \pnAb<stem><Tune|Val|Test|Lo|Hi>, multiplied by `scale` and rounded to
# `decimals`. The spec is stored in paper_ablation_meta ('table:rows', 'table:blocks') so paper_numbers.py reads it from
# the database; the table file below only names the macros.
TABLE_BLOCKS = {
    "rapm": "\\emph{RAPM + prior: next-season game-margin RMSE (points)}",
    "xfg": "\\emph{Expected FG\\%: log loss $\\times 10^3$ (validation and test seasons)}",
    "pregame": "\\emph{Pre-game model: log loss $\\times 10^3$}",
    "sim": "\\emph{Simulator at the halfway point}",
}
TABLE_ROWS = [
    # block, task, base, ablation, metric, variant, label, stem, scale, decimals
    ("rapm", "impact_next", "rapm_prior", "scale=0", "game_rmse", "", "No prior (one-season RAPM)", "PriorNone", 1, 2),
    ("rapm", "impact_next", "rapm_prior", "scale=1", "game_rmse", "", "Prior at scale \\pnAbPriorFullScale{} (not \\pnEvPriorScale)", "PriorFull", 1, 2),
    # The protocol's free minimum (paper_eval_choices impact rapm_prior free_minimum), which its rule sets aside: lambda 12,000
    # through round 8 step 6a, 8,000 since the play-by-play rebuild of step 6b (paper_numbers.py checks the two agree).
    ("rapm", "impact_next", "rapm_prior", "lambda=8000", "game_rmse", "", "$\\lambda=\\pnAbLamHighLambda$ (not \\pnEvLambdaSingle)", "LamHigh", 1, 2),
    ("rapm", "impact_next", "rapm_prior", "no_poss_weight", "game_rmse", "", "No possession weights", "NoWeight", 1, 2),
    ("rapm", "impact_next", "rapm_prior", "no_home_rated", "game_rmse", "", "No home term in the fit", "NoHomeRated", 1, 2),
    ("rapm", "impact_next", "rapm_prior", "no_home", "game_rmse", "", "No home term at all", "NoHome", 1, 2),
    ("xfg", "xfg", "hgb", "no_coords", "log_loss", "", "$-$ coordinates", "XfgCoords", 1000, 2),
    ("xfg", "xfg", "hgb", "no_dist_angle", "log_loss", "", "$-$ distance, angle", "XfgDist", 1000, 2),
    ("xfg", "xfg", "hgb", "no_zone_value", "log_loss", "", "$-$ zone, three-point flag", "XfgZone", 1000, 2),
    ("xfg", "xfg", "hgb", "no_location", "log_loss", "", "$-$ all location features", "XfgLocation", 1000, 2),
    ("xfg", "xfg", "hgb", "no_clock_period", "log_loss", "", "$-$ clock, period", "XfgClock", 1000, 2),
    ("xfg", "xfg", "hgb", "no_season", "log_loss", "", "$-$ season", "XfgSeason", 1000, 2),
    ("pregame", "pregame", "pregame", "no_carry", "log_loss", "", "No carry-over prior", "PreCarry", 1000, 1),
    ("pregame", "pregame", "pregame", "no_shrink", "log_loss", "", "No shrinkage", "PreShrink", 1000, 1),
    ("pregame", "pregame", "pregame", "no_b2b", "log_loss", "", "No back-to-back flags", "PreBtb", 1000, 1),
    ("sim", "sim_playoffs", "sim", "no_carry", "log_loss", CHECKPOINT, "No carry-over, playoff log loss $\\times 10^3$", "SimCarry", 1000, 1),
    ("sim", "sim_playoffs", "sim", "no_shrink", "log_loss", CHECKPOINT, "No shrinkage, playoff log loss $\\times 10^3$", "SimShrink", 1000, 1),
    ("sim", "sim_wins", "sim", "no_shrink", "rmse", CHECKPOINT, "No shrinkage, win-total RMSE", "SimShrinkRmse", 1, 2),
    ("sim", "sim_wins", "sim", "no_draw", "cover80", CHECKPOINT, "No rating draw, 80\\% coverage (points)", "SimDraw", 100, 1),
]
TABLE_KEYS = ("block", "task", "base", "ablation", "metric", "variant", "label", "stem", "scale", "decimals")


def table_meta(out):
    out.put("table:rows", [dict(zip(TABLE_KEYS, r)) for r in TABLE_ROWS], "the rows of the paper's Table ablations (paper/tables/ablations.tex)")
    out.put("table:blocks", TABLE_BLOCKS)


def write_table():
    lines = [
        "% paper/tables/ablations.tex -- GENERATED by scripts/paper_ablations.py; every number is a \\pn macro from numbers.tex.",
        "\\begin{table}[!t]",
        "\\caption{Ablations: one part removed at a time and the model re-estimated under the protocol. $\\Delta$ is the ablated "
        "model's value minus the full model's on the same units (for an error, positive: the part was helping); Tune pools the "
        "tuning phase (\\pnEvPregameHistoryFirst{} to \\pnEvTuneLast{}, leave-one-season-out, for the pre-game model and the "
        "simulator); the interval is the paired cluster bootstrap on the test season}\\label{tab:ablations}",
        "\\centering",
        "\\footnotesize",
        "\\setlength{\\tabcolsep}{3pt}",
        "\\begin{tabular}{p{3.4cm}rrrc}",
        "\\toprule",
        " & \\multicolumn{3}{c}{$\\Delta$} & \\\\",
        "Removed & Tune & Val. & Test & 95\\% CI (test)\\\\",
        "\\midrule",
    ]
    block = None
    for blk, task, _base, _abl, _metric, _variant, text, stem, _scale, _dec in TABLE_ROWS:
        if blk != block:
            lines.append(f"\\multicolumn{{5}}{{l}}{{{TABLE_BLOCKS[blk]}}}\\\\")
            block = blk
        tune = "--" if task == "xfg" else f"\\pnAb{stem}Tune"
        lines.append(f"{text} & {tune} & \\pnAb{stem}Val & \\pnAb{stem}Test & \\pnAb{stem}Lo, \\pnAb{stem}Hi\\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text("\n".join(lines))
    log(f"wrote {TABLE_PATH}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(STAGES), help="comma-separated stages: impact, xfg, sim")
    ap.add_argument("--resamples", type=int, default=T.RESAMPLES, help=f"bootstrap resamples and permutations (default {T.RESAMPLES:,})")
    ap.add_argument("--no-check-fit", action="store_true", help="skip the xfg check fit that reproduces paper_eval's full model")
    args = ap.parse_args()
    stages = [s for s in STAGES if s in args.only.split(",")]
    conn = psycopg2.connect(**DB_CONFIG)
    stored = load_stored(conn, stages)
    prepare_tables(conn, stages, everything=stages == list(STAGES))
    out = Out()
    if "impact" in stages:
        frames = impact_stage(conn, out, stored)
        flush(conn, out, tests_for("impact", frames, args.resamples))
    if "xfg" in stages:
        frames = xfg_stage(conn, out, stored, check=not args.no_check_fit)
        flush(conn, out, tests_for("xfg", frames, args.resamples))
    if "sim" in stages:
        frames = sim_stage(conn, out, stored)
        flush(conn, out, tests_for("sim", frames, args.resamples))
    out.put("run:resamples", args.resamples, "bootstrap resamples and sign-flip permutations per test row")
    table_meta(out)
    flush(conn, out, [])
    write_table()
    cur = conn.cursor()
    for t in DDL:
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
