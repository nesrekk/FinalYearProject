"""
calibrate_award_chances.py
===========================
Turns the award models' scores into real probabilities. MVP / DPOY / ROY
get a chance of winning that adds up to 100% across the field; All-NBA gets
a chance of making one of the three teams that adds up to about 15.

Why: the served models are logistic regressions trained with
class_weight="balanced" (about one winner per few hundred players), which
pushes raw probabilities toward 1. Across the held-out backtest seasons the
raw probabilities summed to about 6.5 (MVP), 17 (DPOY) and 5 (ROY) per
season, although exactly one player wins; the dashboard showed SGA at
100.0% and Wembanyama at 99.99% in the same season. All-NBA (same
weighting) gave its 15th pick 92%.

Method, single-winner awards (a conditional-logit calibration):
chance_i = exp(a * z_i) / sum_j exp(a * z_j), where z is the model's
log-odds score and the sum runs over that season's candidate pool (the
same pools the models train on); one number a per award, fitted by maximum
likelihood of the real winners. All-NBA (15 selections a season): Platt
scaling, chance_i = 1 / (1 + exp(-(a * z_i + b))), fitted the same way.

Evidence, all out of sample:
  * The calibration is fitted on leave-one-season-out scores (each season
    scored by a model that never saw it), with the served models' own
    settings (same as backtest_models.py's logistic model and
    build_all_nba_model.py).
  * The evaluation is nested: for each season, the calibration is refitted
    without that season, then the season is scored. Reported: held-out log
    loss against a baseline (plain normalised shares for single-winner
    awards, the raw probabilities for All-NBA), how often the favourite won
    against its average chance, the average sum of chances per season, and
    a reliability table.

Writes award_chance_calibration (one row per award). The API reads it once
per process; rerun this and restart mvp_api after retraining an award model.

Usage:
    cd scripts && python3 calibrate_award_chances.py
"""

import json
import warnings

import numpy as np
import psycopg2
from scipy.optimize import minimize_scalar
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import backtest_models as bm
import build_all_nba_model as an
import build_dpoy_roy_models as dr
from db_config import DB_CONFIG

warnings.filterwarnings("ignore")

LOGREG = next(factory for key, _label, factory, _w in bm.MODEL_CONFIGS if key == "logreg")
FIELD_BUCKETS = [(0.0, 0.05), (0.05, 0.25), (0.25, 0.5), (0.5, 1.0001)]
ALLNBA_BUCKETS = [(0.0, 0.1), (0.1, 0.5), (0.5, 0.9), (0.9, 1.0001)]


def buckets_of(pairs, edges):
    pairs = np.array(pairs)
    out = []
    for lo, hi in edges:
        m = (pairs[:, 0] >= lo) & (pairs[:, 0] < hi)
        if m.sum():
            out.append({"from": lo, "to": min(hi, 1.0), "n": int(m.sum()),
                        "mean_chance": round(float(pairs[m, 0].mean()), 4), "won": int(pairs[m, 1].sum())})
    return out


def report(r, fold_as):
    print(f"\n{r['award']}: a = {r['a']}" + (f", b = {r['b']}" if r["b"] is not None else "")
          + f" (nested folds a {min(fold_as):.2f}-{max(fold_as):.2f}), {r['n_seasons']} seasons "
          f"{r['season_from']}-{r['season_to']}")
    print(f"  per season, raw probabilities summed to {r['raw_sum_mean']} on average; calibrated {r['chance_sum_mean']}")
    print(f"  held-out log loss: calibrated {r['logloss_calibrated']}, {r['before_label']} {r['logloss_before']}"
          + (f", uniform {r['logloss_uniform']}" if r["logloss_uniform"] is not None else ""))
    if r["favourite_won"] is not None:
        print(f"  favourite won {r['favourite_won']}/{r['n_seasons']}; its average chance {r['favourite_mean_chance']:.1%}")
    for b in r["buckets"]:
        print(f"   chance {b['from']:.2f}-{b['to']:.2f}: n={b['n']:5d}, mean {b['mean_chance']:.1%}, won {b['won']}")


# ─── MVP / DPOY / ROY: one winner, chances across the field ─────────────────

def field_scores(df, feats, train_seasons, test_season):
    """Log-odds scores for one season from a model trained on train_seasons."""
    tr = df[df.season.isin(train_seasons)]
    te = df[df.season == test_season]
    scaler = StandardScaler()
    model = LOGREG()
    model.fit(scaler.fit_transform(tr[feats].values), tr.label.values)
    return model.decision_function(scaler.transform(te[feats].values)), te.label.values


def softmax(z, a):
    x = a * z
    e = np.exp(x - x.max())
    return e / e.sum()


def fit_field(groups):
    nll = lambda a: -sum(np.log(softmax(z, a)[y == 1][0]) for z, y in groups)  # noqa: E731
    return float(minimize_scalar(nll, bounds=(0.05, 5.0), method="bounded").x)


def calibrate_field(award, df, feats, seasons):
    missing = [s for s in seasons if df[df.season == s].label.sum() != 1]
    if missing:
        print(f"{award}: seasons without exactly one labelled winner, left out: {missing}")
    seasons = [s for s in seasons if s not in missing]
    held_out = [field_scores(df, feats, [u for u in seasons if u != t], t) for t in seasons]
    a_served = fit_field(held_out)

    rows, pairs = [], []
    for s in seasons:
        rest = [t for t in seasons if t != s]
        a = fit_field([field_scores(df, feats, [u for u in rest if u != t], t) for t in rest])
        z, y = field_scores(df, feats, rest, s)
        chance = softmax(z, a)
        raw = 1 / (1 + np.exp(-z))
        share = raw / raw.sum()
        rows.append({"a": a, "raw_sum": float(raw.sum()),
                     "ll_chance": float(-np.log(chance[y == 1][0])), "ll_share": float(-np.log(share[y == 1][0])),
                     "ll_uniform": float(np.log(len(z))),
                     "fav_chance": float(chance.max()), "fav_won": bool(y[chance.argmax()] == 1)})
        pairs += list(zip(chance.tolist(), y.tolist()))

    result = {
        "award": award, "kind": "field", "a": round(a_served, 4), "b": None,
        "n_seasons": len(rows), "season_from": min(seasons), "season_to": max(seasons),
        "raw_sum_mean": round(float(np.mean([r["raw_sum"] for r in rows])), 2), "chance_sum_mean": 1.0,
        "logloss_calibrated": round(float(np.mean([r["ll_chance"] for r in rows])), 3),
        "logloss_before": round(float(np.mean([r["ll_share"] for r in rows])), 3),
        "before_label": "plain normalised share",
        "logloss_uniform": round(float(np.mean([r["ll_uniform"] for r in rows])), 3),
        "favourite_won": int(sum(r["fav_won"] for r in rows)),
        "favourite_mean_chance": round(float(np.mean([r["fav_chance"] for r in rows])), 4),
        "buckets": buckets_of(pairs, FIELD_BUCKETS),
    }
    report(result, [r["a"] for r in rows])
    return result


# ─── All-NBA: 15 selections a season, Platt scaling ─────────────────────────

def allnba_scores(df, train_seasons, test_season):
    tr = df[df.season.isin(train_seasons)]
    te = df[df.season == test_season]
    scaler = StandardScaler()
    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42, solver="lbfgs")
    model.fit(scaler.fit_transform(tr[an.FEATURES].values), tr.all_nba_label.values)
    return model.decision_function(scaler.transform(te[an.FEATURES].values)), te.all_nba_label.values


def fit_platt(groups):
    z = np.concatenate([g[0] for g in groups])
    y = np.concatenate([g[1] for g in groups])
    m = LogisticRegression(C=1e6, max_iter=1000).fit(z.reshape(-1, 1), y)  # effectively unpenalised
    return float(m.coef_[0][0]), float(m.intercept_[0])


def logloss(p, y):
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibrate_allnba():
    df = an.load_data()
    counts = df.groupby("season").all_nba_label.sum()
    seasons = sorted(int(s) for s, n in counts.items() if n == an.SELECTIONS_PER_SEASON)
    skipped = sorted(int(s) for s, n in counts.items() if n != an.SELECTIONS_PER_SEASON)
    if skipped:
        # e.g. the current season, whose teams aren't announced/loaded yet
        print(f"ALL_NBA: seasons without all 15 selections in the pool, left out: "
              f"{ {s: int(counts[s]) for s in skipped} }")
    a_served, b_served = fit_platt([allnba_scores(df, [u for u in seasons if u != t], t) for t in seasons])

    rows, pairs = [], []
    for s in seasons:
        rest = [t for t in seasons if t != s]
        a, b = fit_platt([allnba_scores(df, [u for u in rest if u != t], t) for t in rest])
        z, y = allnba_scores(df, rest, s)
        chance = 1 / (1 + np.exp(-(a * z + b)))
        raw = 1 / (1 + np.exp(-z))
        rows.append({"a": a, "y": y, "chance": chance, "raw": raw})
        pairs += list(zip(chance.tolist(), y.tolist()))
    y_all = np.concatenate([r["y"] for r in rows])

    result = {
        "award": "ALL_NBA", "kind": "platt", "a": round(a_served, 4), "b": round(b_served, 4),
        "n_seasons": len(rows), "season_from": seasons[0], "season_to": seasons[-1],
        "raw_sum_mean": round(float(np.mean([r["raw"].sum() for r in rows])), 2),
        "chance_sum_mean": round(float(np.mean([r["chance"].sum() for r in rows])), 2),
        "logloss_calibrated": round(logloss(np.concatenate([r["chance"] for r in rows]), y_all), 4),
        "logloss_before": round(logloss(np.concatenate([r["raw"] for r in rows]), y_all), 4),
        "before_label": "raw model probability",
        "logloss_uniform": None, "favourite_won": None, "favourite_mean_chance": None,
        "buckets": buckets_of(pairs, ALLNBA_BUCKETS),
    }
    report(result, [r["a"] for r in rows])
    return result


COLS = ["award", "kind", "a", "b", "n_seasons", "season_from", "season_to", "raw_sum_mean", "chance_sum_mean",
        "logloss_calibrated", "logloss_before", "before_label", "logloss_uniform", "favourite_won",
        "favourite_mean_chance", "buckets"]


def main():
    results = [
        calibrate_field("MVP", *bm.load_mvp_data(), bm.TRAIN_SEASONS),
        calibrate_field("DPOY", *bm.load_award_data(dr.DPOY_FEATURES, dr.DPOY_WINNERS,
                                                    pool_column="is_dpoy_candidate"), bm.TRAIN_SEASONS),
        calibrate_field("ROY", *bm.load_award_data(dr.ROY_FEATURES, dr.ROY_WINNERS,
                                                   pool_column="is_rookie_candidate"), dr.ROY_TRAIN_SEASONS),
        calibrate_allnba(),
    ]

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS award_chance_calibration;")
    cur.execute("""
        CREATE TABLE award_chance_calibration (
            award TEXT PRIMARY KEY,
            kind TEXT NOT NULL,              -- 'field' (softmax over the pool) or 'platt'
            a DOUBLE PRECISION NOT NULL,
            b DOUBLE PRECISION,
            n_seasons INT, season_from INT, season_to INT,
            raw_sum_mean DOUBLE PRECISION, chance_sum_mean DOUBLE PRECISION,
            logloss_calibrated DOUBLE PRECISION, logloss_before DOUBLE PRECISION, before_label TEXT,
            logloss_uniform DOUBLE PRECISION,
            favourite_won INT, favourite_mean_chance DOUBLE PRECISION,
            buckets JSONB,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );""")
    for r in results:
        cur.execute(f"INSERT INTO award_chance_calibration ({', '.join(COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(COLS))});",
                    [json.dumps(r[c]) if c == "buckets" else r[c] for c in COLS])
    conn.commit()
    conn.close()
    print("\nWrote award_chance_calibration.")


if __name__ == "__main__":
    main()
