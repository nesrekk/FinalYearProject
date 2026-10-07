"""
build_rating_tracker.py
========================
The Rating Tracker (round 6, step 7): RAPM whose ratings carry across
seasons. The model and the estimator are in scripts/rating_tracker_lib.py
(shared with scripts/paper_eval.py, which scores the same model under the
paper's protocol); this script estimates the hyperparameters, runs the
filter and the smoother over every season on file, validates the result the
way build_rapm.py validates its versions, and writes the app's tables.

What it does, in order
  1. Loads the tracked five-man stints exactly as build_rapm.py does
     (build_rapm.load_rows, one Design per season) and Basketball-Reference's
     OBPM/DBPM (build_rapm.load_bpm).
  2. Checks the algebra: with no carry-over (phi = 0) and one season's
     measurements the tracker's posterior mean must equal build_rapm's ridge
     toward a scaled BPM (rating_tracker_lib.ridge_equivalent; the largest
     difference is stored and must be under 1e-6).
  3. Chooses the five hyperparameters on the protocol's tune seasons only
     (2020-21 to 2023-24; rating_tracker_lib.TUNE_SEASONS): lambda_0 (how far
     from average a newcomer may start), lambda_q (how much a rating may
     drift between seasons), lambda_b (how much a season's BPM is worth), k
     (the BPM scale) and phi (how much of last season's rating carries over).
     Criterion: the pooled next-season game-margin RMSE over the three tune
     pairs from the filtered ratings after each season, the number every
     other impact model's hyperparameters were chosen by in paper_eval.py.
     Two starting points, Powell with bounds, the better kept; every
     evaluation is counted and the result stored. The validate and test
     seasons (2024-25, 2025-26) never touch the hyperparameters, so the
     paper's scores of them are out of sample. Stored beside it, not used:
     (a) the maximum-marginal-likelihood estimate on the same seasons (the
     state-space model's own estimator) and its tune RMSE, which overweights
     BPM because a season's BPM carries that season's point differential;
     (b) a leave-one-pair-out check: the choice made on two tune pairs and
     scored on the third, against the chosen point scored on the same pair,
     so the reader can see how much the choice moves between seasons.
     sigma^2 (the stint noise variance behind every standard deviation) is
     the profiled likelihood estimate at the chosen point.
  4. Profiles both criteria along a grid of lambda_q (the drift) with the
     other hyperparameters at their chosen values: rating_tracker_curve.
  5. Runs the filter over 2020-21 to 2025-26 at the estimates and the
     smoother back over it. Two kinds of rating per player-season:
       filtered  the posterior after that season's games, using nothing later
                 (what the paper scores and what a forecast could have used);
       smoothed  the same with hindsight: every season's games weigh in
                 (the career line on the profile page).
     Standard deviations are sqrt(sigma^2 x posterior variance); the 95%
     interval is +-1.96 sd. `carried_o/d` is what the tracker expected before
     the season started (phi x last season's posterior; 0 for a newcomer), so
     rating - carried is what the season's games and BPM moved him.
  6. Validation (rating_tracker_validation, build_rapm's columns and tests):
       next_season     filtered ratings after season S predict season S+1's
                       stints, scale 1, only the intercept and home term
                       refitted on S+1 (build_rapm's convention), for the
                       tracker and, for comparison on the same rows, the stored
                       player_rapm versions, BPM and everyone-average. A player
                       the tracker has seen but who missed season S keeps his
                       carried rating; the one-season versions have none for
                       him, and `coverage` shows the difference. The smoothed
                       kind is not scored here: it has seen the later season.
       held_out_games  within a season, build_rapm's five game folds: each
                       fold's stints predicted from the other four with the
                       prior the filter brought into the season (the earlier
                       seasons' information is the same for every fold). The
                       page shows these next to rapm_validation's rows for the
                       same folds.
       year_to_year    correlation of consecutive seasons among players
                       qualified in both, for both kinds and the comparison
                       models. The tracker's is higher by construction (last
                       season is part of this season's estimate); it is a
                       smoothness, not evidence.
  7. Prints the checks the README quotes.

Judgment calls (all stored or printed)
  * Hyperparameters by the protocol's next-season criterion, not by the
    marginal likelihood: the likelihood scores a season's stints against
    that season's BPM, which already contains the season's point
    differential, so it trusts BPM more than any out-of-sample test does
    (on a single season with BPM switched off it lands where build_rapm's
    cross-validation does, lambda about 3,000). Both are stored.
  * One drift variance for offence and defence, one BPM weight for OBPM and
    DBPM (build_rapm uses one lambda for both sides too).
  * A player with a BPM row but no tracked stint that season gets no BPM
    measurement that season (he has no stint rows either).
  * Rows are written only for player-seasons with a tracked stint; the state
    of a player who sat out a season is carried but not shown.

Tables written (all dropped and rebuilt)
  player_rating_tracker    one row per kind (filtered / smoothed), season and
                           player: ratings, sds, interval, carried values, the
                           BPM measurement, sizes, qualified (1,000+ possessions,
                           build_rapm.QUALIFIED_POSS);
  rating_tracker_fit       one row: the hyperparameters, where, how and by what
                           criterion they were chosen, the likelihood estimate
                           and the leave-one-pair-out check beside them, sigma^2,
                           the implied sds, the equivalence check, per-season
                           intercepts and home edges, sizes;
  rating_tracker_curve     the drift profile (lambda_q grid: pooled tune
                           next-season RMSE and -2 log likelihood), the choice
                           marked;
  rating_tracker_validation  the tests above.

Deterministic: no random numbers anywhere; two runs give identical tables.
Runtime about 8 minutes (the three estimations are most of it, ~340 + 100 +
580 evaluations at half a second each; --quick cuts them to one start and a
short optimisation while developing, and says so in the fit row, which
paper_eval.py then refuses).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 build_rating_tracker.py [--quick]
    cd scripts && python3 build_rating_tracker.py --season 2027      # the live season only (round 9 step 4)
Rerun after build_lineup_stints.py, load_bref_bpm_vorp.py or build_rapm.py
(the validation compares against player_rapm); then paper_eval.py --only
impact, paper_tests.py --only impact and rebuild_all.sh paper-inputs.

--season N (round 9 step 4, 2026-10-07; scripts/season_mode.py): the filtered
rating through today for season N, with nothing re-tuned. The five
hyperparameters and sigma^2 are read from the stored rating_tracker_fit row
(a --quick row is refused; the row itself is never written: it is the
paper's, api/paper_freeze.py), the filter runs over every season up to N
exactly as the full build runs it (about half a second a season), and only
season N's rows are replaced: player_rating_tracker (both kinds; for the
last season on file the smoother's estimate equals the filter's, so the
'with hindsight' rows of N are its 'as of then' rows, and the earlier
seasons' with-hindsight rows, which a full build would revise with N's
games, stay as the paper's full build left them), rating_tracker_validation
(held-out folds of N, N predicted from N-1's ratings, year to year) and the
season's summary (players, rows, games, stints, possessions, intercept, home
term, BPM measurements, qualified), which the full build keeps in the fit
row's JSON and the --season mode writes as the row version 'tracker',
season N of rapm_fits (the one per-season model table; lambda there is
lambda_0, prior_scale is k, lambda_rule 'tracker:frozen'). rating_tracker_fit
and rating_tracker_curve are never touched. The test proves `--season 2026`
reproduces the stored 2025-26 rows byte for byte.
"""

import argparse
import json
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

import build_rapm as R
import rating_tracker_lib as T
import season_mode as SM
from db_config import DB_CONFIG

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

VERSION = "tracker"
KINDS = ("filtered", "smoothed")
RIDGE_CHECK = (2024, 3000.0, 0.5)      # season, lambda, prior scale of the equivalence check (paper_eval's rapm_prior choice)
Z95 = 1.959963984540054

T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def span(seasons):
    seasons = sorted(set(int(s) for s in seasons))
    return label(seasons[0]) if len(seasons) == 1 else f"{label(seasons[0])} to {label(seasons[-1])}"


# ── Loading ──────────────────────────────────────────────────────────────────

def load(conn, through=None):
    """Every season's Design (up to `through`: the --season mode), BPM, names, seasons."""
    rows, n_stints, dropped = R.load_rows(conn)
    bpm = R.load_bpm(conn)
    names = R.load_names(conn)
    seasons = sorted(int(s) for s in rows.season.unique() if through is None or int(s) <= through)
    designs = {s: R.Design(rows[rows.season == s]) for s in seasons}
    log(f"{n_stints} tracked stints -> {len(rows)} side-rows ({dropped} sides with no possession dropped); seasons {seasons[0]}-{seasons[-1]}")
    return designs, bpm, names, seasons


def load_player_rapm(conn):
    """{(version, season): ({player: orapm}, {player: drapm})} from the app's RAPM table."""
    df = pd.read_sql_query("SELECT version, season, player_id, orapm, drapm FROM player_rapm", conn)
    out = {}
    for (v, s), g in df.groupby(["version", "season"]):
        out[(v, int(s))] = (dict(zip(g.player_id.astype(int), g.orapm.astype(float))),
                            dict(zip(g.player_id.astype(int), g.drapm.astype(float))))
    return out


# ── Scoring helpers (build_rapm's conventions) ───────────────────────────────

def next_season_row(design, o, d, model, season, fit_seasons):
    """Ratings (o, d) used as published on season `season`'s rows; intercept and home refitted."""
    all_rows = np.ones(design.n, bool)
    vec = design.rating_vector(o, d)
    beta, _ = R.fit_nuisance(design, all_rows, vec, False)
    pred = design.X @ beta
    rated = set(o) | set(d)
    if rated:
        _, k = R.fit_nuisance(design, all_rows, vec, True)
        n_rated = np.array([sum(p in rated for p in ids) + sum(p in rated for p in ids2)
                            for ids, ids2 in zip(design.rows.off, design.rows.de)])
    else:
        k = None
        n_rated = np.zeros(design.n)
    cov_slots = float((design.w * n_rated).sum() / (10 * design.w.sum()))
    cov_all = float(design.w[n_rated == 10].sum() / design.w.sum())
    return {"test": "next_season", "season": season, "model": model, "fit_seasons": fit_seasons,
            "coverage": None if not rated else round(cov_slots, 4), "coverage_all10": None if not rated else round(cov_all, 4),
            "scale_fit": None if k is None else round(float(k), 3), **R.score(design, all_rows, pred)}


# ── Write ────────────────────────────────────────────────────────────────────

PLAYER_COLS = ["kind", "season", "player_id", "teams", "games", "stints", "minutes", "poss_off", "poss_def", "poss",
               "orapm", "drapm", "rapm", "orapm_sd", "drapm_sd", "rapm_sd", "rapm_ci_low", "rapm_ci_high",
               "carried_o", "carried_d", "carried", "prior_o", "prior_d", "obpm", "dbpm", "bpm",
               "first_season", "seasons_seen", "qualified"]
FIT_COLS = ["version", "seasons_from", "seasons_to", "estimated_on", "criterion", "lambda0", "lambda_q", "lambda_b", "prior_scale", "phi",
            "tune_rmse", "tune_games", "sigma2", "neg2ll", "nuisance_var", "newcomer_sd", "drift_sd", "bpm_sd", "evaluations",
            "converged", "quick", "starts", "ml_estimate", "loo_pairs", "ridge_check", "seasons", "players", "qualified_poss", "runtime_s"]
CURVE_COLS = ["lambda_q", "next_rmse", "next_games", "neg2ll", "sigma2", "chosen"]
VAL_COLS = R.VAL_COLS


def write(conn, player_rows, fit_row, curve_rows, val_rows):
    cur = conn.cursor()
    for t in ("rating_tracker_validation", "rating_tracker_curve", "rating_tracker_fit", "player_rating_tracker"):
        cur.execute(f"DROP TABLE IF EXISTS {t};")
    cur.execute("""CREATE TABLE player_rating_tracker (
        kind TEXT NOT NULL, season INTEGER NOT NULL, player_id BIGINT NOT NULL, teams TEXT,
        games INTEGER, stints INTEGER, minutes REAL, poss_off REAL, poss_def REAL, poss REAL,
        orapm REAL NOT NULL, drapm REAL NOT NULL, rapm REAL NOT NULL,
        orapm_sd REAL NOT NULL, drapm_sd REAL NOT NULL, rapm_sd REAL NOT NULL, rapm_ci_low REAL, rapm_ci_high REAL,
        carried_o REAL, carried_d REAL, carried REAL, prior_o REAL, prior_d REAL, obpm REAL, dbpm REAL, bpm REAL,
        first_season INTEGER NOT NULL, seasons_seen INTEGER NOT NULL, qualified BOOLEAN NOT NULL,
        PRIMARY KEY (kind, season, player_id));""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO player_rating_tracker ({', '.join(PLAYER_COLS)}) VALUES %s",
        [tuple(R.clean(r.get(c)) for c in PLAYER_COLS) for r in player_rows], page_size=2000)
    cur.execute("CREATE INDEX ON player_rating_tracker (player_id);")
    cur.execute("CREATE INDEX ON player_rating_tracker (kind, season, qualified, rapm DESC);")
    cur.execute("""CREATE TABLE rating_tracker_fit (
        version TEXT PRIMARY KEY, seasons_from INTEGER, seasons_to INTEGER, estimated_on TEXT NOT NULL, criterion TEXT NOT NULL,
        lambda0 DOUBLE PRECISION NOT NULL, lambda_q DOUBLE PRECISION NOT NULL, lambda_b DOUBLE PRECISION NOT NULL,
        prior_scale DOUBLE PRECISION NOT NULL, phi DOUBLE PRECISION NOT NULL, tune_rmse DOUBLE PRECISION NOT NULL, tune_games INTEGER,
        sigma2 DOUBLE PRECISION NOT NULL, neg2ll DOUBLE PRECISION NOT NULL, nuisance_var DOUBLE PRECISION,
        newcomer_sd REAL, drift_sd REAL, bpm_sd REAL, evaluations INTEGER, converged BOOLEAN, quick BOOLEAN,
        starts JSONB, ml_estimate JSONB, loo_pairs JSONB, ridge_check JSONB,
        seasons JSONB, players INTEGER, qualified_poss INTEGER, runtime_s REAL);""")
    cur.execute(f"INSERT INTO rating_tracker_fit ({', '.join(FIT_COLS)}) VALUES ({', '.join(['%s'] * len(FIT_COLS))})",
                tuple(R.clean(fit_row.get(c)) for c in FIT_COLS))
    cur.execute("""CREATE TABLE rating_tracker_curve (
        lambda_q DOUBLE PRECISION NOT NULL, next_rmse DOUBLE PRECISION NOT NULL, next_games INTEGER,
        neg2ll DOUBLE PRECISION NOT NULL, sigma2 DOUBLE PRECISION NOT NULL, chosen BOOLEAN NOT NULL);""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rating_tracker_curve ({', '.join(CURVE_COLS)}) VALUES %s",
        [tuple(R.clean(r.get(c)) for c in CURVE_COLS) for r in curve_rows])
    cur.execute("""CREATE TABLE rating_tracker_validation (
        test TEXT NOT NULL, season INTEGER NOT NULL, model TEXT NOT NULL, fit_seasons TEXT,
        rows INTEGER, games INTEGER, players INTEGER, poss REAL, coverage REAL, coverage_all10 REAL, scale_fit REAL,
        stint_rmse REAL, game_rmse REAL, game_corr REAL, corr REAL,
        PRIMARY KEY (test, season, model));""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rating_tracker_validation ({', '.join(VAL_COLS)}) VALUES %s",
        [tuple(R.clean(r.get(c)) for c in VAL_COLS) for r in val_rows])
    conn.commit()


# ── Checks ───────────────────────────────────────────────────────────────────

def print_checks(conn, names, season=None):
    """The checks the README quotes; with `season` only that season's rows (the --season mode)."""
    cur = conn.cursor()
    only = "" if season is None else f" AND t.season = {int(season)}"
    print("\nFit (rating_tracker_fit):")
    print(pd.read_sql_query("""SELECT estimated_on, lambda0, lambda_q, lambda_b, prior_scale, phi, tune_rmse, sigma2, newcomer_sd, drift_sd,
                                      bpm_sd, evaluations, converged, quick, players FROM rating_tracker_fit""", conn).to_string(index=False))
    f = pd.read_sql_query("SELECT ml_estimate, loo_pairs FROM rating_tracker_fit", conn).iloc[0]
    print("  likelihood estimate (not used):", json.dumps(f.ml_estimate))
    print("  leave-one-pair-out:", json.dumps(f.loo_pairs))
    print("\nDrift profile (rating_tracker_curve):")
    print(pd.read_sql_query("SELECT lambda_q, next_rmse, neg2ll, chosen FROM rating_tracker_curve ORDER BY lambda_q", conn).to_string(index=False))
    for kind in KINDS:
        df = pd.read_sql_query(
            f"""SELECT t.season, t.player_id, t.teams, t.poss, t.orapm, t.drapm, t.rapm, t.rapm_sd, t.carried, t.bpm,
                      p.rapm AS single, p.rapm_se AS single_se, q.rapm AS prior
               FROM player_rating_tracker t
               LEFT JOIN player_rapm p ON p.version = 'single' AND p.season = t.season AND p.player_id = t.player_id
               LEFT JOIN player_rapm q ON q.version = 'prior' AND q.season = t.season AND q.player_id = t.player_id
               WHERE t.kind = %s AND t.qualified{only} ORDER BY t.season, t.rapm DESC""", conn, params=(kind,))
        df["name"] = df.player_id.map(names)
        print(f"\n{kind}: top 8 per season (qualified, {R.QUALIFIED_POSS}+ possessions)")
        if df.empty:
            print(f"  (no qualified player: under {R.QUALIFIED_POSS} possessions each so far)")
        for season, g in df.groupby("season"):
            top = g.head(8)
            jok = g.reset_index(drop=True)
            jrank = jok.index[jok.player_id == 203999]
            jr = f"Jokić #{jrank[0] + 1} of {len(jok)}" if len(jrank) else "Jokić not qualified"
            corr = lambda a, b: g[[a, b]].dropna().corr().iloc[0, 1] if g[[a, b]].dropna().shape[0] > 2 else float("nan")  # noqa: E731
            r_b, r_s, r_p = corr("rapm", "bpm"), corr("rapm", "single"), corr("rapm", "prior")
            print(f"  {label(season)} ({jr}; r with BPM {r_b:.2f}, one-season {r_s:.2f}, BPM-prior {r_p:.2f}; sd {g.rapm.std():.2f}; "
                  f"mean sd {g.rapm_sd.mean():.2f} vs one-season se {g.single_se.mean():.2f}): "
                  + "; ".join(f"{x.name} {x.rapm:+.1f}±{x.rapm_sd:.1f}" for x in top.itertuples()))
    print("\nValidation (rating_tracker_validation):")
    v = pd.read_sql_query(f"""SELECT test, season, model, fit_seasons, games, coverage, coverage_all10, scale_fit, stint_rmse,
                                    game_rmse, game_corr, corr, players
                             FROM rating_tracker_validation t WHERE TRUE{only} ORDER BY test, season, game_rmse NULLS LAST, corr DESC NULLS LAST, model""", conn)
    print(v.to_string(index=False))
    for t in ("player_rating_tracker", "rating_tracker_fit", "rating_tracker_curve", "rating_tracker_validation"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true", help="one start and a short optimisation (developing only; recorded in the fit row)")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    designs, bpm, names, seasons = load(conn)
    stored_rapm = load_player_rapm(conn)
    data = T.TrackerData(designs, bpm, folds=True)
    log(f"state: {data.P} players x 2 + intercept + home = {data.D}; BPM measurements per season "
        + ", ".join(f"{s}: {len(data.meas_idx[s]) // 2}" for s in seasons))

    # 2. the algebra check
    cs, cl, ck = RIDGE_CHECK
    tb, rb = T.ridge_equivalent(designs[cs], bpm, cs, cl, ck)
    ridge_diff = float(np.max(np.abs(tb - rb)))
    assert ridge_diff < 1e-6, f"tracker with phi = 0 differs from build_rapm's ridge by {ridge_diff}"
    log(f"ridge equivalence ({label(cs)}, lambda {cl:g}, scale {ck}): max |difference| {ridge_diff:.2e}")

    # 3. hyperparameters on the tune seasons
    tune = [s for s in seasons if s in T.TUNE_SEASONS]
    assert tune == list(T.TUNE_SEASONS), tune
    pairs = T.tune_pairs(tune)
    quick = args.quick
    maxfev = 60 if quick else 600
    starts = T.STARTS[:1] if quick else T.STARTS
    criterion = f"pooled next-season game-margin RMSE over the tune pairs {', '.join(f'{label(a)} -> {label(b)}' for a, b in pairs)}"
    log(f"choosing hyperparameters by {criterion} ({'quick' if quick else 'full'})")
    par, info = T.estimate(data, lambda p_: T.next_rmse(data, p_, pairs)[0], starts=starts, log=log, maxfev=maxfev, name="next-rmse")
    tune_rmse, tune_games = T.next_rmse(data, par, pairs)
    neg2ll, sigma2 = T.objective(data, par, tune)
    log(f"chosen: {T.fmt(par)}; tune RMSE {tune_rmse:.4f} over {tune_games} games; sigma2 {sigma2:,.1f}; {info['evaluations']} evaluations")
    # (a) the likelihood estimate, for comparison
    par_ml, info_ml = T.estimate(data, lambda p_: T.objective(data, p_, tune)[0], starts=(par,), log=log, maxfev=maxfev, name="likelihood")
    ml_rmse, _ = T.next_rmse(data, par_ml, pairs)
    ml_estimate = {"par": par_ml, "neg2ll": info_ml["value"], "sigma2": T.objective(data, par_ml, tune)[1], "tune_rmse": ml_rmse,
                   "evaluations": info_ml["evaluations"], "converged": info_ml["starts"][0]["converged"],
                   "note": "maximum marginal likelihood of the tune seasons' stint rows given the BPM priors; not used (see the docstring)"}
    log(f"likelihood estimate (not used): {T.fmt(par_ml)}; its tune RMSE {ml_rmse:.4f}")
    # (b) leave one tune pair out
    loo = []
    for held in pairs:
        others = [p_ for p_ in pairs if p_ != held]
        par_o, info_o = T.estimate(data, lambda p_: T.next_rmse(data, p_, others)[0], starts=(par,), log=log,
                                   maxfev=40 if quick else 200, name=f"without {label(held[0])}->{label(held[1])}")
        loo.append({"held_out": [held[0], held[1]], "par": par_o, "others_rmse": info_o["value"],
                    "held_out_rmse": T.next_rmse(data, par_o, [held])[0], "held_out_rmse_chosen": T.next_rmse(data, par, [held])[0],
                    "evaluations": info_o["evaluations"]})
    log("leave-one-pair-out: " + "; ".join(f"{label(r['held_out'][1])}: {r['held_out_rmse']:.3f} vs chosen {r['held_out_rmse_chosen']:.3f} "
                                          f"(lambda_q {r['par']['lambda_q']:,.0f}, phi {r['par']['phi']:.2f})" for r in loo))

    # 4. the drift profile under both criteria
    curve_rows = T.drift_profile(data, par, pairs, seasons=tune)
    for r in curve_rows:
        r["next_rmse"] = round(r["next_rmse"], 4)
    log("drift profile: " + "; ".join(f"{r['lambda_q']:.0f}: {r['next_rmse']:.3f} / {r['neg2ll'] - neg2ll:+.0f}" for r in curve_rows))

    # 5. the filter over every season, and the smoother
    f = T.Filter(data, par, keep=True)
    ms, Ps = f.smooth()
    player_rows, season_info = season_rows(f, ms, Ps, designs, data, bpm, par, sigma2, seasons)

    # 6. validation
    val_rows = []
    for s in seasons:
        val_rows += validation_rows(s, f, designs, data, bpm, stored_rapm, player_rows, seasons)
    nx = {(r["season"], r["model"]): r["game_rmse"] for r in val_rows if r["test"] == "next_season"}
    for s in seasons[1:]:
        log(f"  next-season {label(s)}: " + ", ".join(f"{m} {nx[(s, m)]:.2f}" for m in ("rapm_tracker", "rapm_single", "rapm_prior", "rapm_multi", "bpm", "zero") if (s, m) in nx))

    fit_row = {
        "version": VERSION, "seasons_from": seasons[0], "seasons_to": seasons[-1], "estimated_on": span(tune), "criterion": criterion,
        **{k: float(par[k]) for k in T.PARAMS}, "tune_rmse": float(tune_rmse), "tune_games": int(tune_games),
        "sigma2": float(sigma2), "neg2ll": float(neg2ll), "nuisance_var": T.NUISANCE_VAR,
        "newcomer_sd": round(float(np.sqrt(sigma2 / par["lambda0"])), 3), "drift_sd": round(float(np.sqrt(sigma2 / par["lambda_q"])), 3),
        "bpm_sd": round(float(np.sqrt(sigma2 / par["lambda_b"])), 3),
        "evaluations": int(info["evaluations"]), "converged": bool(info["starts"][info["best_start"]]["converged"]), "quick": bool(quick),
        "starts": json.dumps(info["starts"]), "ml_estimate": json.dumps(ml_estimate), "loo_pairs": json.dumps(loo),
        "ridge_check": json.dumps({"season": cs, "lambda": cl, "prior_scale": ck, "max_abs_diff": ridge_diff}),
        "seasons": json.dumps(season_info), "players": data.P, "qualified_poss": R.QUALIFIED_POSS, "runtime_s": round(time.time() - T0, 1),
    }
    write(conn, player_rows, fit_row, curve_rows, val_rows)
    log(f"wrote {len(player_rows)} player rows, {len(curve_rows)} curve points, {len(val_rows)} validation rows")
    print_checks(conn, names)
    conn.close()


def season_rows(f, ms, Ps, designs, data, bpm, par, sigma2, seasons):
    """The player rows (both kinds) and the per-season summary of every season from a filter run and its smoother."""
    sizes = {s: R.player_sizes(designs[s]) for s in seasons}
    P = data.P
    player_rows = []
    season_info = {}
    seen = {}
    for s in seasons:
        d = designs[s]
        ic, hm = f.nuisance(s)
        sdo, sdd, sdt = f.sds(s, sigma2)
        io, idd = np.arange(P), P + np.arange(P)
        vo, vd, cov = Ps[s][io, io], Ps[s][idd, idd], Ps[s][io, idd]
        ssdo, ssdd = np.sqrt(sigma2 * vo), np.sqrt(sigma2 * vd)
        ssdt = np.sqrt(sigma2 * np.maximum(vo + vd + 2 * cov, 0.0))
        n_q = 0
        for p in d.players:
            i = data.gidx[p]
            seen[p] = seen.get(p, 0) + 1
            sz = sizes[s][p]
            poss = (sz["poss_off"] + sz["poss_def"]) / 2
            q = poss >= R.QUALIFIED_POSS
            n_q += q
            bp = bpm.get((p, s))
            meas = (bp[0] * par["prior_scale"], bp[1] * par["prior_scale"]) if bp else (None, None)
            base = {"season": s, "player_id": p, "teams": sz["teams"], "games": sz["games"], "stints": sz["stints"],
                    "minutes": sz["minutes"], "poss_off": sz["poss_off"], "poss_def": sz["poss_def"], "poss": round(poss, 1),
                    "carried_o": round(float(f.m_prior[s][i]), 3), "carried_d": round(float(f.m_prior[s][P + i]), 3),
                    "carried": round(float(f.m_prior[s][i] + f.m_prior[s][P + i]), 3),
                    "prior_o": None if bp is None else round(meas[0], 3), "prior_d": None if bp is None else round(meas[1], 3),
                    "obpm": None if bp is None else round(bp[0], 2), "dbpm": None if bp is None else round(bp[1], 2),
                    "bpm": None if bp is None else round(bp[2], 2),
                    "first_season": data.first[p], "seasons_seen": seen[p], "qualified": bool(q)}
            for kind, m, so, sd_, st in (("filtered", f.m[s], sdo, sdd, sdt), ("smoothed", ms[s], ssdo, ssdd, ssdt)):
                o, de = float(m[i]), float(m[P + i])
                player_rows.append({**base, "kind": kind, "orapm": round(o, 3), "drapm": round(de, 3), "rapm": round(o + de, 3),
                                    "orapm_sd": round(float(so[i]), 3), "drapm_sd": round(float(sd_[i]), 3), "rapm_sd": round(float(st[i]), 3),
                                    "rapm_ci_low": round(o + de - Z95 * float(st[i]), 3), "rapm_ci_high": round(o + de + Z95 * float(st[i]), 3)})
        season_info[str(s)] = {"players": d.P, "rows": int(d.n), "games": int(len(d.games)), "stints": int(d.rows.stint_id.nunique()),
                               "poss": round(float(d.w.sum()), 1), "intercept": round(ic, 3), "home_coef": round(hm, 3),
                               "home_edge_per_100": round(2 * hm, 3), "n_bpm": int(len(data.meas_idx[s]) // 2), "qualified": int(n_q),
                               "newcomers": int(sum(1 for p in d.players if data.first[p] == s))}
        log(f"  {label(s)}: {d.P} players, {n_q} qualified, {season_info[str(s)]['newcomers']} newcomers, home edge {2 * hm:+.2f}/100")
    return player_rows, season_info


def validation_rows(s, f, designs, data, bpm, stored_rapm, player_rows, seasons):
    """Season s's validation rows (build_rapm's tests) from the filter and the stored RAPM versions."""
    val_rows = []
    d = designs[s]
    # held-out folds, the prior into the season fixed
    pred = np.zeros(d.n)
    for k in sorted(set(d.fold)):
        test = d.fold == k
        pred[test] = d.X[test] @ f.heldout_beta(s, k)
    val_rows.append({"test": "held_out_games", "season": s, "model": "rapm_tracker", "fit_seasons": f"{label(s)} (other folds), earlier seasons",
                     **R.score(d, np.ones(d.n, bool), pred)})
    if s > seasons[0]:
        prev = s - 1
        o, de = f.ratings(prev)
        models = {"rapm_tracker": (o, de, f"through {label(prev)}")}
        for v in ("single", "prior", "multi"):
            if (v, prev) in stored_rapm:
                fs = label(prev) if v != "multi" else f"{label(prev - R.WINDOW + 1)} to {label(prev)}"
                models[f"rapm_{v}"] = (*stored_rapm[(v, prev)], fs)
        models["bpm"] = ({p: v[0] for (p, ss), v in bpm.items() if ss == prev}, {p: v[1] for (p, ss), v in bpm.items() if ss == prev}, label(prev))
        models["zero"] = ({}, {}, label(prev))
        for name, (mo, md, fs) in models.items():
            val_rows.append(next_season_row(d, mo, md, name, s, fs))
        # year-to-year among players qualified in both seasons
        qa = {r["player_id"]: r for r in player_rows if r["kind"] == "filtered" and r["season"] == prev and r["qualified"]}
        qb = {r["player_id"]: r for r in player_rows if r["kind"] == "filtered" and r["season"] == s and r["qualified"]}
        both = sorted(set(qa) & set(qb))
        sm = {(r["season"], r["player_id"]): r["rapm"] for r in player_rows if r["kind"] == "smoothed"}
        series = {"rapm_tracker": [(qa[p]["rapm"], qb[p]["rapm"]) for p in both],
                  "rapm_tracker_smoothed": [(sm[(prev, p)], sm[(s, p)]) for p in both],
                  "bpm": [(bpm[(p, prev)][2], bpm[(p, s)][2]) for p in both if (p, prev) in bpm and (p, s) in bpm]}
        for v in ("single", "prior"):
            if (v, prev) in stored_rapm and (v, s) in stored_rapm:
                a_, b_ = stored_rapm[(v, prev)], stored_rapm[(v, s)]
                series[f"rapm_{v}"] = [(a_[0][p] + a_[1][p], b_[0][p] + b_[1][p]) for p in both if p in a_[0] and p in b_[0]]
        for model, xy in series.items():
            arr = np.array(xy, float)
            if len(arr) < 20:
                continue
            val_rows.append({"test": "year_to_year", "season": s, "model": model, "fit_seasons": f"{label(prev)} vs {label(s)}",
                             "players": int(len(arr)), "corr": round(float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]), 4)})
    return val_rows


# ── The --season mode ────────────────────────────────────────────────────────

def load_fit(conn):
    """The stored hyperparameters and sigma^2 (rating_tracker_fit); a --quick row or a missing one stops the run."""
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('rating_tracker_fit')")
    if cur.fetchone()[0] is None:
        raise SystemExit("--season: rating_tracker_fit does not exist; run the full build first")
    cur.execute(f"SELECT {', '.join(T.PARAMS)}, sigma2, quick, seasons_from, seasons_to, estimated_on FROM rating_tracker_fit WHERE version = %s", (VERSION,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit("--season: no stored fit row; run the full build first")
    par = {k: float(v) for k, v in zip(T.PARAMS, row[:len(T.PARAMS)])}
    sigma2, quick, s_from, s_to, on = row[len(T.PARAMS):]
    if quick:
        raise SystemExit("--season: the stored fit row is a --quick one; run the full build first")
    return par, float(sigma2), int(s_from), int(s_to), on


def fit_summary_row(season, seasons, data, par, info):
    """Season N's summary as a rapm_fits row (version 'tracker'): what the full build keeps in the fit row's JSON."""
    return {"version": VERSION, "season": season, "seasons_from": seasons[0], "seasons_to": season,
            "games": info["games"], "stints": info["stints"], "rows": info["rows"], "players": info["players"], "poss": info["poss"],
            "lambda": par["lambda0"], "prior_scale": par["prior_scale"], "lambda_rule": "tracker:frozen", "cv_folds": R.FOLDS,
            "cv_rmse": None, "cv_rmse_zero": None, "cv_best_lambda": None, "cv_best_scale": None, "cv_best_rmse": None,
            "intercepts": json.dumps({str(season): info["intercept"]}), "home_coef": info["home_coef"],
            "home_edge_per_100": info["home_edge_per_100"], "bootstraps": None, "seed": None, "qualified_poss": R.QUALIFIED_POSS,
            "qualified": info["qualified"], "players_with_prior": info["n_bpm"]}


def write_season(conn, season, player_rows, val_rows, fit_summary):
    cur = conn.cursor()
    SM.require_tables(cur, ["player_rating_tracker", "rating_tracker_validation", "rapm_fits"], season)
    n = SM.delete_season(cur, "player_rating_tracker", season) + SM.delete_season(cur, "rating_tracker_validation", season)
    n += SM.delete_season(cur, "rapm_fits", season, "season = %s AND version = 'tracker'")
    psycopg2.extras.execute_values(cur, f"INSERT INTO player_rating_tracker ({', '.join(PLAYER_COLS)}) VALUES %s",
                                   [tuple(R.clean(r.get(c)) for c in PLAYER_COLS) for r in player_rows], page_size=2000)
    psycopg2.extras.execute_values(cur, f"INSERT INTO rating_tracker_validation ({', '.join(VAL_COLS)}) VALUES %s",
                                   [tuple(R.clean(r.get(c)) for c in VAL_COLS) for r in val_rows])
    psycopg2.extras.execute_values(cur, f"INSERT INTO rapm_fits ({', '.join(R.FIT_COLS)}) VALUES %s",
                                   [tuple(R.clean(fit_summary.get(c)) for c in R.FIT_COLS)])
    conn.commit()
    return n


def main_season(season):
    conn = psycopg2.connect(**DB_CONFIG)
    par, sigma2, s_from, s_to, on = load_fit(conn)
    log(f"--season {season}: hyperparameters from the stored fit ({T.fmt(par)}; chosen on {on}, held fixed), sigma2 {sigma2:,.1f}")
    designs, bpm, names, seasons = load(conn, through=season)
    if season not in seasons:
        raise SystemExit(f"--season {season}: no tracked stint of that season in lineup_stints")
    if seasons[0] != s_from:
        raise SystemExit(f"--season {season}: the stints start in {seasons[0]} but the stored fit ran from {s_from}; run the full build")
    stored_rapm = load_player_rapm(conn)
    data = T.TrackerData(designs, bpm, folds=True)
    f = T.Filter(data, par, keep=True)
    ms, Ps = f.smooth()
    player_rows, season_info = season_rows(f, ms, Ps, designs, data, bpm, par, sigma2, seasons)
    val_rows = validation_rows(season, f, designs, data, bpm, stored_rapm, player_rows, seasons)
    nx = {r["model"]: r["game_rmse"] for r in val_rows if r["test"] == "next_season"}
    if nx:
        log(f"  next-season {label(season)}: " + ", ".join(f"{m} {nx[m]:.2f}" for m in ("rapm_tracker", "rapm_single", "rapm_prior", "rapm_multi", "bpm", "zero") if m in nx))
    mine = [r for r in player_rows if r["season"] == season]
    summary = fit_summary_row(season, seasons, data, par, season_info[str(season)])
    n_del = write_season(conn, season, mine, val_rows, summary)
    log(f"--season {season}: replaced {n_del} rows with {len(mine)} player rows ({len(mine) // 2} players, both kinds), "
        f"{len(val_rows)} validation rows and the season's summary (rapm_fits, version 'tracker'); the fit row, the curve and every "
        f"other season untouched")
    print_checks(conn, names, season)
    conn.close()


if __name__ == "__main__":
    _season = SM.parse_season()
    if _season is not None:
        main_season(_season)
    else:
        main()
