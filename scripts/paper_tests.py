"""
paper_tests.py
===============
Significance tests and intervals on every comparison the conference paper
states (round 5, step 3), computed from the per-unit predictions that
scripts/paper_eval.py stored in paper_eval_predictions, never from the
summary metrics alone. Writes paper_eval_tests; touches nothing else.

What is computed for a difference between two models A and B
--------------------------------------------------------------
Both models are scored on exactly the same units (the inner join of their
unit ids), and the difference is metric(A) - metric(B): negative means A
has the lower error.

  paired cluster bootstrap   the clusters are resampled with replacement
      B times, both metrics are recomputed on the same resample, and the
      resampled differences give the 95% interval (2.5th and 97.5th
      percentiles) and the two-sided bootstrap p-value
      2 * min(P*(diff* <= 0), P*(diff* >= 0)), capped at 1. The clusters
      are games (next-season and held-out game margins, pre-game odds),
      the game a shot belongs to (the shot model: shots in one game share
      a defence and a night), players (year-to-year reliabilities; a
      player's pairs in several tune seasons stay together) and
      team-seasons (the simulator).
  sign-flip permutation      for metrics that are a mean of per-unit losses
      (squared error, log loss, Brier, absolute error, coverage): each
      cluster's summed loss differential has its sign flipped at random
      B times, p = (1 + #{|T*| >= |T|}) / (B + 1). Equal mean squared
      error is equal RMSE, so the test on squared errors is the test of
      the RMSE difference. Not defined for correlations.
  Diebold-Mariano            where the units are a time-ordered series of
      games (impact_next, impact_heldout, pregame): the per-game loss
      differential ordered by date, DM = mean(d) / sqrt(LRV / n) with a
      Newey-West long-run variance (Bartlett kernel, bandwidth
      floor(4 (n/100)^(2/9))), two-sided normal p-value. Games on the same
      date have no natural order (ordered by game id); the HAC variance
      is there for whatever dependence a season's schedule leaves.

For a single number (an RMSE, a log loss, a correlation, a coverage, the
shot model's calibration error) the same cluster bootstrap gives a 95%
percentile interval; model_b is '' on those rows, diff = value_a and the
p-value columns are NULL.

Which comparisons: PAIRS below, for every phase of the protocol (the tune
phase pooled over its seasons, then validate, then test) and every metric
in METRICS. Nothing here chooses anything; the test season is scored
exactly as step 2 stored it.

Calibration of the shot model (metric ece, calib_max_gap; model hgb): the
ten fixed-width probability bins of build_shot_making.reliability_bins();
ECE is the count-weighted mean of |observed - predicted| over the bins,
calib_max_gap the largest gap over the bins holding at least 1% of the
season's shots (CALIB_MIN_SHARE: the 0.9-1.0 bin holds 7 shots in
2025-26, whose gap means nothing). These are not in paper_eval_metrics
(step 2 stored no calibration), so value_a is computed here.

Judgment calls, all stored in the note column or the docstring:
  * team-seasons in one season are not independent (16 of 30 make the
    playoffs); the bootstrap treats them as exchangeable, so the simulator
    intervals are, if anything, a little narrow;
  * the Brier score follows the convention of the stored metric: clipped
    probabilities for the shot model (paper_eval.score_probs), raw for
    the pre-game model and the simulator (season_sim_lib.brier); log loss
    clips at 1e-6 everywhere. The stored value_a / value_b equal
    paper_eval_metrics to 1e-9 (api/tests/test_paper_tests.py checks);
  * the interval is the percentile interval, no bias correction: the
    statistics are means or smooth functions of means over 260-16,658
    units, where the plain percentile interval is adequate;
  * 10,000 resamples (RESAMPLES) for the bootstrap and the permutation
    test; the smallest p-value the permutation can report is 1/10,001.
Deterministic: each row's seed is an md5 of its key (stored), so a rerun
or a --only rerun reproduces every row bit for bit.

Runtime about 3 minutes (loading the 1.75M stored shot rows is most of it).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_tests.py                     # everything
    cd scripts && python3 paper_tests.py --only impact,sim   # some tasks
    cd scripts && python3 paper_tests.py --resamples 500     # a quick look
Then rerun paper_numbers.py. Rerun this after paper_eval.py.
"""

import argparse
import hashlib
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy import stats

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))
from paper_freeze import F  # noqa: E402  (the paper's rows: seasons to 2025-26, round 9 step 1)

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

RESAMPLES = 10_000
SEED = 20260929
CHUNK = 500                    # resamples per block (keeps the weight matrix under ~70 MB at 16,658 clusters)
CLIP = 1e-6
CALIB_MIN_SHARE = 0.01         # a bin counts toward the largest calibration gap only with >= 1% of the season's shots
PHASES = ("tune", "validate", "test")
TIME_ORDERED = ("impact_next", "impact_heldout", "pregame")

# Metrics that are a (transformed) mean of a per-unit loss, and the transform.
MEAN_METRICS = {"game_rmse": "sqrt", "rmse": "sqrt", "log_loss": None, "brier": None, "mae": None,
                "cover80": None, "favourite_win_rate": None}
CORR_METRICS = ("corr", "game_corr")
CALIB_METRICS = ("ece", "calib_max_gap")

# The comparisons the paper states (model_a, model_b), by task; every phase, every metric of the task.
PAIRS = {
    "impact_next": [("rapm_prior", "bpm"), ("rapm_multi", "bpm"), ("rapm_single", "bpm"), ("rapm_single", "rapm_prior"),
                    ("rapm_multi", "rapm_prior"), ("bpm_scaled", "bpm"), ("onoff", "zero"), ("onoff_scaled", "zero"),
                    ("rapm_prior", "zero"), ("rapm_single", "zero"), ("bpm", "zero"),
                    # expected-points RAPM (step 4) against its actual-points twin, BPM and zero
                    ("xrapm_single", "rapm_single"), ("xrapm_prior", "rapm_prior"), ("xrapm_single", "bpm"), ("xrapm_prior", "bpm"),
                    ("xrapm_single", "zero"), ("xrapm_prior", "zero"), ("xrapm_single", "xrapm_prior"),
                    # the Rating Tracker (round 6 step 7) against the versions it replaces, BPM and zero
                    ("rapm_tracker", "bpm"), ("rapm_tracker", "rapm_prior"), ("rapm_tracker", "rapm_multi"),
                    ("rapm_tracker", "rapm_single"), ("rapm_tracker", "zero"),
                    # shooter-aware expected-points RAPM (round 6 step 8): against actual points, round 5's xRAPM, its
                    # shooter-blind look-ahead-free twin (the shooter term alone), BPM, the tracker and zero; and the
                    # look-ahead fix alone (lf against round 5's xRAPM)
                    ("xrapm_sa_single", "rapm_single"), ("xrapm_sa_prior", "rapm_prior"), ("xrapm_sa_single", "xrapm_single"),
                    ("xrapm_sa_prior", "xrapm_prior"), ("xrapm_sa_single", "xrapm_lf_single"), ("xrapm_sa_prior", "xrapm_lf_prior"),
                    ("xrapm_lf_single", "xrapm_single"), ("xrapm_lf_prior", "xrapm_prior"), ("xrapm_sa_single", "bpm"),
                    ("xrapm_sa_prior", "bpm"), ("xrapm_sa_prior", "rapm_tracker"), ("xrapm_sa_single", "zero"), ("xrapm_sa_prior", "zero")],
    "impact_heldout": [("rapm_prior", "bpm"), ("rapm_multi", "bpm"), ("rapm_single", "bpm"), ("rapm_single", "rapm_prior"),
                       ("rapm_multi", "rapm_prior"), ("bpm_scaled", "bpm"), ("onoff", "zero"), ("onoff_scaled", "zero"),
                       ("rapm_prior", "zero"), ("rapm_single", "zero"), ("bpm", "zero"),
                       ("xrapm_single", "rapm_single"), ("xrapm_prior", "rapm_prior"), ("xrapm_single", "bpm"), ("xrapm_prior", "bpm"),
                       ("xrapm_single", "zero"), ("xrapm_prior", "zero"),
                       ("rapm_tracker", "bpm"), ("rapm_tracker", "rapm_prior"), ("rapm_tracker", "rapm_multi"), ("rapm_tracker", "rapm_single"),
                       ("xrapm_sa_single", "rapm_single"), ("xrapm_sa_prior", "rapm_prior"), ("xrapm_sa_single", "xrapm_single"),
                       ("xrapm_sa_prior", "xrapm_prior"), ("xrapm_sa_single", "xrapm_lf_single"), ("xrapm_sa_prior", "xrapm_lf_prior"),
                       ("xrapm_sa_prior", "bpm")],
    "impact_reliability": [("bpm", "rapm_prior"), ("rapm_prior", "rapm_single"), ("rapm_single", "onoff"), ("bpm", "rapm_single"),
                           ("xrapm_single", "rapm_single"), ("xrapm_prior", "rapm_prior"), ("bpm", "xrapm_prior"), ("bpm", "xrapm_single"),
                           ("rapm_tracker", "rapm_prior"), ("rapm_tracker", "rapm_single"), ("bpm", "rapm_tracker"),
                           ("xrapm_sa_single", "rapm_single"), ("xrapm_sa_prior", "rapm_prior"), ("xrapm_sa_single", "xrapm_single"),
                           ("xrapm_sa_prior", "xrapm_prior"), ("xrapm_sa_single", "xrapm_lf_single"), ("xrapm_sa_prior", "xrapm_lf_prior"),
                           ("bpm", "xrapm_sa_prior")],
    "xfg": [("hgb", "logreg"), ("hgb", "zone"), ("hgb", "constant"), ("logreg", "zone"), ("zone", "constant")],
    "xfg_reliability": [("quality", "shot_making"), ("efg", "shot_making"), ("quality", "efg")],
    "pregame": [("prior_rest", "current"), ("prior_rest", "baseline"), ("current", "baseline"), ("prior_rest", "prior"),
                ("prior", "current")],
    "sim_playoffs": [("model", "record"), ("model", "standings")],
    "sim_top6": [("model", "record")],
    "sim_wins": [("model", "record")],
}
METRICS = {
    "impact_next": ("game_rmse", "game_corr"), "impact_heldout": ("game_rmse", "game_corr"), "impact_reliability": ("corr",),
    "xfg": ("log_loss", "brier"), "xfg_reliability": ("corr",), "pregame": ("log_loss", "brier", "favourite_win_rate"),
    "sim_playoffs": ("brier", "log_loss"), "sim_top6": ("brier",), "sim_wins": ("mae", "rmse", "cover80"),
}
STAGES = {"impact": ("impact_next", "impact_heldout", "impact_reliability"), "xfg": ("xfg", "xfg_reliability"),
          "pregame": ("pregame",), "sim": ("sim_playoffs", "sim_top6", "sim_wins")}
CLUSTER_BY = {"xfg": "game", "impact_reliability": "player", "xfg_reliability": "player",
              "sim_playoffs": "team_season", "sim_top6": "team_season", "sim_wins": "team_season"}   # default: game

T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def span(seasons):
    seasons = sorted(set(int(s) for s in seasons))
    return label(seasons[0]) if len(seasons) == 1 else f"{label(seasons[0])} to {label(seasons[-1])}"


def row_seed(*key):
    """A deterministic 63-bit seed from the row's key (stored with the row)."""
    h = hashlib.md5(("|".join(str(k) for k in key) + f"|{SEED}").encode()).digest()
    return int.from_bytes(h[:8], "little") & (2 ** 63 - 1)


# ── Losses and statistics ────────────────────────────────────────────────────

def clip(p):
    return np.clip(p, CLIP, 1 - CLIP)


def loss(metric, task, pred, actual, lo=None, hi=None):
    """Per-unit loss whose (transformed) mean is the stored metric."""
    if metric in ("game_rmse", "rmse"):
        return (pred - actual) ** 2
    if metric == "mae":
        return np.abs(pred - actual)
    if metric == "log_loss":
        p = clip(pred)
        return -(actual * np.log(p) + (1 - actual) * np.log(1 - p))
    if metric == "brier":
        p = clip(pred) if task == "xfg" else pred
        return (p - actual) ** 2
    if metric == "cover80":
        return ((actual >= lo) & (actual <= hi)).astype(float)
    if metric == "favourite_win_rate":
        return np.where(pred >= 0.5, actual, 1 - actual)
    raise ValueError(metric)


def transform(metric, m):
    return np.sqrt(m) if MEAN_METRICS[metric] == "sqrt" else m


def cluster_codes(keys):
    _, idx = np.unique(np.asarray(keys), return_inverse=True)
    return idx, int(idx.max()) + 1


def cluster_weights(rng, n_clusters, b):
    """(b, n_clusters) multiplicities of a bootstrap resample of the clusters."""
    draws = rng.integers(0, n_clusters, size=(b, n_clusters))
    W = np.empty((b, n_clusters))
    for i in range(b):
        W[i] = np.bincount(draws[i], minlength=n_clusters)
    return W


def boot_sums(rng, idx, n_clusters, cols, resamples):
    """Resampled totals of each column (per-cluster sums resampled with the clusters): (resamples, k)."""
    S = np.stack([np.bincount(idx, weights=np.asarray(c, float), minlength=n_clusters) for c in cols], axis=1)
    out = np.empty((resamples, S.shape[1]))
    for start in range(0, resamples, CHUNK):
        b = min(CHUNK, resamples - start)
        out[start:start + b] = cluster_weights(rng, n_clusters, b) @ S
    return out


def boot_means(rng, idx, n_clusters, cols, resamples):
    sums = boot_sums(rng, idx, n_clusters, list(cols) + [np.ones(len(idx))], resamples)
    return sums[:, :-1] / sums[:, -1:]


def corr_from_sums(M):
    """Correlation from totals (Sx, Sy, Sxx, Syy, Sxy, N), row-wise."""
    Sx, Sy, Sxx, Syy, Sxy, N = (M[:, i] for i in range(6))
    cov = Sxy - Sx * Sy / N
    vx, vy = Sxx - Sx * Sx / N, Syy - Sy * Sy / N
    return cov / np.sqrt(vx * vy)


def boot_corr(rng, idx, n_clusters, xy_pairs, resamples):
    """Resampled correlations for each (x, y) pair on the same resamples: (resamples, len(pairs))."""
    cols = []
    for x, y in xy_pairs:
        cols += [x, y, x * x, y * y, x * y]
    sums = boot_sums(rng, idx, n_clusters, cols + [np.ones(len(idx))], resamples)
    N = sums[:, -1:]
    return np.stack([corr_from_sums(np.hstack([sums[:, 5 * i:5 * i + 5], N])) for i in range(len(xy_pairs))], axis=1)


def percentile_ci(d):
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(lo), float(hi)


def boot_p(d):
    return float(min(1.0, 2 * min(np.mean(d <= 0), np.mean(d >= 0))))


def sign_flip_p(rng, cluster_diff, resamples):
    """Two-sided p of the mean differential being zero: random sign flips of each cluster's summed differential."""
    T = abs(float(cluster_diff.sum()))
    hits = 0
    for start in range(0, resamples, CHUNK):
        b = min(CHUNK, resamples - start)
        signs = rng.choice(np.array([-1.0, 1.0]), size=(b, len(cluster_diff)))
        hits += int(np.sum(np.abs(signs @ cluster_diff) >= T - 1e-12))
    return (1 + hits) / (resamples + 1)


def diebold_mariano(d):
    """(statistic, two-sided normal p, bandwidth) for a time-ordered loss differential."""
    d = np.asarray(d, float)
    n = len(d)
    e = d - d.mean()
    L = int(np.floor(4 * (n / 100) ** (2 / 9)))
    lrv = float(e @ e) / n
    for k in range(1, L + 1):
        lrv += 2 * (1 - k / (L + 1)) * float(e[k:] @ e[:-k]) / n
    if lrv <= 0:
        lrv = float(e @ e) / n
    stat = d.mean() / np.sqrt(lrv / n)
    return float(stat), float(2 * stats.norm.sf(abs(stat))), L


# ── Loading ──────────────────────────────────────────────────────────────────

class Series:
    """One model's stored rows for a (task, phase), sorted by unit id."""

    def __init__(self, df, variant):
        df = df.sort_values(["season", "unit_id"]).reset_index(drop=True)
        self.ids = df.unit_id.to_numpy()
        self.key = (df.season.astype(str) + "|" + df.unit_id.astype(str)).to_numpy()   # unique by the table's primary key
        self.pred, self.actual = df.pred.to_numpy(float), df.actual.to_numpy(float)
        self.lo, self.hi = df.lo.to_numpy(float), df.hi.to_numpy(float)
        self.date = pd.to_datetime(df.unit_date).to_numpy("datetime64[D]")
        self.season = df.season.to_numpy()
        self.cluster = df.cluster.to_numpy()
        self.variant = str(variant)

    def take(self, mask):
        s = Series.__new__(Series)
        for k, v in self.__dict__.items():
            setattr(s, k, v[mask] if isinstance(v, np.ndarray) else v)
        return s


def load_phase(conn, task, phase, shot_games):
    """{(model, variant): Series} for one task and phase."""
    df = pd.read_sql(f"""SELECT model, variant, season, unit_id, pred, actual, lo, hi, unit_date
                        FROM {F('paper_eval_predictions')} WHERE task = %s AND phase = %s""", conn, params=(task, phase))
    if df.empty:
        return {}
    if task == "xfg":
        df["cluster"] = df.unit_id.astype("int64").map(shot_games)
        assert df.cluster.notna().all(), "every stored shot has a game in player_shots"
    else:
        df["cluster"] = df.unit_id
    return {(m, v): Series(g, v) for (m, v), g in df.groupby(["model", "variant"], sort=True)}


def load_shot_games(conn):
    t = time.time()
    df = pd.read_sql(f"SELECT id, game_id FROM {F('player_shots')} WHERE game_id LIKE '002%%'", conn)
    m = pd.Series(df.game_id.to_numpy(), index=df.id.to_numpy())
    log(f"xfg: {len(m):,} regular-season shot -> game ids in {time.time() - t:.0f}s")
    return m


def align(a, b):
    """The two series restricted to their common units, in the same order."""
    _, ia, ib = np.intersect1d(a.key, b.key, assume_unique=True, return_indices=True)
    return a.take(ia), b.take(ib)


# ── The rows ─────────────────────────────────────────────────────────────────

class Rows:
    COLS = ("task", "phase", "metric", "model_a", "model_b", "variant", "seasons", "unit_type", "cluster_by", "n", "n_clusters",
            "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_stat", "dm_p", "dm_lags", "resamples", "seed", "note")

    def __init__(self, resamples):
        self.rows, self.resamples = [], resamples

    def add(self, **kw):
        kw.setdefault("model_b", "")
        kw.setdefault("variant", "")
        kw.setdefault("resamples", self.resamples)
        for c in ("value_b", "p_boot", "p_perm", "dm_stat", "dm_p", "dm_lags", "note"):
            kw.setdefault(c, None)
        self.rows.append(tuple(_f(kw[c]) if c in ("value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_stat", "dm_p")
                               else kw[c] for c in self.COLS))


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def unit_type_of(task):
    return {"impact_reliability": "player", "xfg_reliability": "player", "xfg": "shot"}.get(
        task, "team_season" if task.startswith("sim") else "game")


def order_in_time(s):
    """By date, ties (games on one date) in season and unit-id order (the series is already sorted so)."""
    return np.argsort(s.date, kind="stable")


def single_rows(out, task, phase, model, s, metric, note=None):
    """A 95% interval on one stored number."""
    seed = row_seed(task, phase, metric, model, "", s.variant, span(s.season))
    rng = np.random.default_rng(seed)
    idx, C = cluster_codes(s.cluster)
    if metric in MEAN_METRICS:
        lo_ = loss(metric, task, s.pred, s.actual, s.lo, s.hi)
        value = float(transform(metric, lo_.mean()))
        boot = transform(metric, boot_means(rng, idx, C, [lo_], out.resamples)[:, 0])
    elif metric in CORR_METRICS:
        value = float(np.corrcoef(s.pred, s.actual)[0, 1])
        boot = boot_corr(rng, idx, C, [(s.pred, s.actual)], out.resamples)[:, 0]
    else:
        raise ValueError(metric)
    lo, hi = percentile_ci(boot)
    out.add(task=task, phase=phase, metric=metric, model_a=model, variant=s.variant, seasons=span(s.season), unit_type=unit_type_of(task),
            cluster_by=CLUSTER_BY.get(task, "game"), n=len(s.ids), n_clusters=C, value_a=value, diff=value, ci_lo=lo, ci_hi=hi,
            seed=seed, note=note)


def paired_rows(out, task, phase, ma, mb, a, b, metric, variant, note=None):
    """diff = metric(a) - metric(b) on the common units, with interval and p-values."""
    a, b = align(a, b)
    assert len(a.ids) > 0, (task, phase, ma, mb)
    assert metric in CORR_METRICS or (a.actual == b.actual).all(), (task, phase, ma, mb)   # one outcome per unit for a loss
    seasons = span(a.season)
    seed = row_seed(task, phase, metric, ma, mb, variant, seasons)
    rng = np.random.default_rng(seed)
    idx, C = cluster_codes(a.cluster)
    dm = (None, None, None)
    p_perm = None
    if metric in MEAN_METRICS:
        la, lb = loss(metric, task, a.pred, a.actual, a.lo, a.hi), loss(metric, task, b.pred, b.actual, b.lo, b.hi)
        va, vb = float(transform(metric, la.mean())), float(transform(metric, lb.mean()))
        means = boot_means(rng, idx, C, [la, lb], out.resamples)
        d = transform(metric, means[:, 0]) - transform(metric, means[:, 1])
        p_perm = sign_flip_p(rng, np.bincount(idx, weights=la - lb, minlength=C), out.resamples)
        if task in TIME_ORDERED:
            o = order_in_time(a)
            dm = diebold_mariano((la - lb)[o])
    elif metric in CORR_METRICS:
        va, vb = float(np.corrcoef(a.pred, a.actual)[0, 1]), float(np.corrcoef(b.pred, b.actual)[0, 1])
        r = boot_corr(rng, idx, C, [(a.pred, a.actual), (b.pred, b.actual)], out.resamples)
        d = r[:, 0] - r[:, 1]
    else:
        raise ValueError(metric)
    lo, hi = percentile_ci(d)
    out.add(task=task, phase=phase, metric=metric, model_a=ma, model_b=mb, variant=variant, seasons=seasons, unit_type=unit_type_of(task),
            cluster_by=CLUSTER_BY.get(task, "game"), n=len(a.ids), n_clusters=C, value_a=va, value_b=vb, diff=va - vb, ci_lo=lo, ci_hi=hi,
            p_boot=boot_p(d), p_perm=p_perm, dm_stat=dm[0], dm_p=dm[1], dm_lags=dm[2], seed=seed, note=note)


def calibration_rows(out, task, phase, model, s):
    """ECE and the largest bin gap of the shot model over ten fixed-width probability bins, with intervals."""
    p, y = clip(s.pred), s.actual
    bins = np.minimum((p * 10).astype(int), 9)
    idx, C = cluster_codes(s.cluster)
    key = idx * 10 + bins
    Sy = np.bincount(key, weights=y, minlength=C * 10).reshape(C, 10)
    Sp = np.bincount(key, weights=p, minlength=C * 10).reshape(C, 10)
    Sn = np.bincount(key, minlength=C * 10).reshape(C, 10).astype(float)

    def gaps(SY, SP, SN):
        with np.errstate(invalid="ignore", divide="ignore"):
            g = np.abs(SY / SN - SP / SN)
        g = np.where(SN > 0, g, 0.0)
        total = SN.sum(axis=-1, keepdims=True)
        big = SN >= CALIB_MIN_SHARE * total          # the largest gap is taken over bins holding >= 1% of the shots
        return (g * SN).sum(axis=-1) / total[..., 0], np.where(big, g, 0.0).max(axis=-1)

    ece, mx = gaps(Sy.sum(0), Sp.sum(0), Sn.sum(0))
    counts = Sn.sum(0).astype(int)
    for metric, value in (("ece", ece), ("calib_max_gap", mx)):
        seed = row_seed(task, phase, metric, model, "", s.variant, span(s.season))
        rng = np.random.default_rng(seed)
        boot = np.empty(out.resamples)
        for start in range(0, out.resamples, CHUNK):
            b = min(CHUNK, out.resamples - start)
            W = cluster_weights(rng, C, b)
            e_, m_ = gaps(W @ Sy, W @ Sp, W @ Sn)
            boot[start:start + b] = e_ if metric == "ece" else m_
        lo, hi = percentile_ci(boot)
        out.add(task=task, phase=phase, metric=metric, model_a=model, variant=s.variant, seasons=span(s.season), unit_type="shot",
                cluster_by="game", n=len(s.ids), n_clusters=C, value_a=float(value), diff=float(value), ci_lo=lo, ci_hi=hi,
                seed=seed, note="ten fixed-width probability bins (build_shot_making.reliability_bins); bin counts "
                                + ",".join(str(c) for c in counts) + ("; ECE = count-weighted mean |observed - predicted|"
                                                                      if metric == "ece" else
                                                                      f"; largest gap over bins holding >= {CALIB_MIN_SHARE:.0%} of the shots"))
    log(f"{task}: {phase} {model} ECE {ece:.4f}, max gap {mx:.4f}")


# ── Driver ───────────────────────────────────────────────────────────────────

def variants_of(series):
    """{model: [variant, ...]} present in a phase."""
    v = {}
    for m, va in series:
        v.setdefault(m, []).append(va)
    return v


def run_task(conn, out, task, shot_games):
    for phase in PHASES:
        series = load_phase(conn, task, phase, shot_games)
        if not series:
            continue
        by_model = variants_of(series)
        shared = sorted({va for (_, va) in series if va != ""} | {""})   # checkpoints / floors, plus the plain variant
        # intervals on every stored number
        for (m, va), s in series.items():
            for metric in METRICS[task]:
                if task == "sim_playoffs" and m == "standings" and metric == "log_loss":
                    continue          # the standings baseline is 0/1: its log loss is the clip, not a forecast
                single_rows(out, task, phase, m, s, metric)
            if task == "xfg" and m == "hgb":
                calibration_rows(out, task, phase, m, s)
        # paired differences
        for ma, mb in PAIRS[task]:
            if ma not in by_model or mb not in by_model:
                continue
            for va in shared:
                # a model's rows carry either the shared variant (checkpoint, floor) or its own (the shot model's config)
                sa = series.get((ma, va)) or (series.get((ma, by_model[ma][0])) if len(by_model[ma]) == 1 and va == "" else None)
                sb = series.get((mb, va)) or (series.get((mb, by_model[mb][0])) if len(by_model[mb]) == 1 and va == "" else None)
                if sa is None or sb is None:
                    continue
                note = None
                if sa.variant != va or sb.variant != va:
                    note = f"{ma} rows are variant '{sa.variant}', {mb} rows variant '{sb.variant}'"
                for metric in METRICS[task]:
                    if task == "sim_playoffs" and mb == "standings" and metric == "log_loss":
                        continue          # the standings baseline is 0/1: its log loss is the clip, not a forecast
                    paired_rows(out, task, phase, ma, mb, sa, sb, metric, va, note=note)
        log(f"{task}: {phase} done ({len(series)} model series, {len(out.rows)} rows so far)")


DDL = """
    task TEXT NOT NULL, phase TEXT NOT NULL, metric TEXT NOT NULL, model_a TEXT NOT NULL, model_b TEXT NOT NULL DEFAULT '',
    variant TEXT NOT NULL DEFAULT '', seasons TEXT NOT NULL, unit_type TEXT NOT NULL, cluster_by TEXT NOT NULL,
    n INTEGER NOT NULL, n_clusters INTEGER NOT NULL,
    value_a DOUBLE PRECISION NOT NULL, value_b DOUBLE PRECISION, diff DOUBLE PRECISION NOT NULL,
    ci_lo DOUBLE PRECISION NOT NULL, ci_hi DOUBLE PRECISION NOT NULL, p_boot DOUBLE PRECISION, p_perm DOUBLE PRECISION,
    dm_stat DOUBLE PRECISION, dm_p DOUBLE PRECISION, dm_lags INTEGER, resamples INTEGER NOT NULL, seed BIGINT NOT NULL, note TEXT,
    PRIMARY KEY (task, phase, metric, model_a, model_b, variant, seasons)"""


def write(conn, out, tasks, everything):
    cur = conn.cursor()
    if everything:
        cur.execute("DROP TABLE IF EXISTS paper_eval_tests")
    cur.execute(f"CREATE TABLE IF NOT EXISTS paper_eval_tests ({DDL})")
    if not everything:
        cur.execute("DELETE FROM paper_eval_tests WHERE task IN %s", (tuple(tasks),))
    rows = sorted(out.rows, key=lambda r: tuple(str(x) for x in r[:7]))
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_eval_tests VALUES %s", rows, page_size=1000)
    conn.commit()
    cur.execute("SELECT count(*), pg_size_pretty(pg_total_relation_size('paper_eval_tests')) FROM paper_eval_tests")
    n, size = cur.fetchone()
    log(f"wrote {len(rows)} rows; paper_eval_tests now {n} rows, {size}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default=",".join(STAGES), help="comma-separated stages: impact, xfg, pregame, sim")
    ap.add_argument("--resamples", type=int, default=RESAMPLES, help=f"bootstrap resamples and permutations (default {RESAMPLES:,})")
    args = ap.parse_args()
    stages = [s for s in STAGES if s in args.only.split(",")]
    tasks = [t for s in stages for t in STAGES[s]]
    conn = psycopg2.connect(**DB_CONFIG)
    out = Rows(args.resamples)
    shot_games = load_shot_games(conn) if "xfg" in stages else None
    for task in tasks:
        run_task(conn, out, task, shot_games)
    write(conn, out, tasks, everything=stages == list(STAGES))
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
