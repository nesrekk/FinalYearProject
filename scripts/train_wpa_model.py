"""
train_wpa_model.py
===================
Trains a real win-probability model from the real play-by-play data
fetch_play_by_play.py collected: for every real event in every real game
(score, time remaining, and the real final winner), fits a Logistic
Regression predicting P(home team wins). Same library and same
interpretable-coefficients approach this project already uses for MVP/
DPOY/ROY — not a black box.

Features (standard, published win-probability-model technique, not
invented): seconds_remaining, score_margin_home, and
score_margin_home / sqrt(seconds_remaining + 1) — the interaction term is
what makes the model learn that the same 5-point margin means much more
with 30 seconds left than with 40 minutes left; the actual weight it gets
is fit from real data, not assumed.

Validates calibration (not just accuracy) before trusting the model: a
well-built win-probability model should have "quoted 70% win probability"
situations actually resolve to a win about 70% of the real time. This
validation — held-out ROC-AUC, Brier score, log loss, and a 10-bucket
reliability curve, computed both across all events and restricted to
real clutch-time events only — is stored in wpa_model_validation so the
Model Validation tab can show it rather than it only ever being printed
to a terminal and forgotten.

Also trains and evaluates a Gradient Boosting alternative on the exact
same held-out-by-game split (real comparison, not a separate uncontrolled
experiment) — HistGradientBoostingClassifier specifically, not plain
GradientBoostingClassifier: this table has 3.6M+ real events, and
sklearn's non-histogram GB is impractically slow at that size, where the
histogram-binned variant is built for exactly this scale. Evaluated and
stored (wpa_model_validation.model_type) purely for comparison — it does
NOT replace wpa_model.pkl, which stays the deployed Logistic Regression.
Win probability is looked up one event at a time, scalar, in tight loops
(wpa_lib.py's win_prob(), called per real play-by-play event when
replaying a full game), where a fast closed-form sigmoid clearly beats a
few hundred scalar calls into a boosted-tree ensemble — so this is a real,
evaluated "should we switch" comparison, not a redeployment.

Usage:
    cd scripts && python3 train_wpa_model.py
"""

import math
import pickle
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from db_config import DB_CONFIG
from wpa_lib import PBP_DEDUP_WHERE

# Same real NBA clutch-time definition used everywhere else in this
# project (compute_wpa.py, the /players/clutch-wpa endpoint): final 5
# minutes of regulation/OT, score within 5 points.
CLUTCH_SECONDS = 300
CLUTCH_MARGIN = 5


def load_data():
    # PBP_DEDUP_WHERE drops the nba_api copy of every game ESPN also has —
    # without it, the same real game could land in train AND test under its
    # two game_ids, leaking into the held-out-by-game evaluation.
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT e.game_id, e.period, e.seconds_remaining, e.score_home, e.score_away, g.home_win
        FROM pbp_events e
        JOIN pbp_games g ON g.game_id = e.game_id
        WHERE e.action_type != 'period' AND """ + PBP_DEDUP_WHERE + ";"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def build_features(df):
    df = df.copy()
    df["score_margin_home"] = df["score_home"] - df["score_away"]
    df["margin_per_sqrt_time"] = df["score_margin_home"] / np.sqrt(df["seconds_remaining"] + 1)
    df["home_win"] = df["home_win"].astype(int)
    return df


FEATURES = ["seconds_remaining", "score_margin_home", "margin_per_sqrt_time"]


def calibration_check(model, scaler, X_test, y_test):
    probs = model.predict_proba(scaler.transform(X_test))[:, 1]
    bins = np.linspace(0, 1, 11)
    print("\n  Calibration (predicted bucket -> real observed win rate):")
    for i in range(10):
        lo, hi = bins[i], bins[i + 1]
        mask = (probs >= lo) & (probs < hi)
        n = mask.sum()
        if n == 0:
            continue
        observed = y_test[mask].mean()
        print(f"    [{lo:.1f}-{hi:.1f}) n={n:6d}  predicted~{(lo + hi) / 2:.2f}  real observed={observed:.3f}")
    return probs


def reliability_bins(probs, y_true):
    """10-bucket reliability curve as data (not just printed) — predicted
    probability bucket vs. real observed win rate, plus n per bucket so
    a UI can grey out or size buckets by real sample size."""
    bins = np.linspace(0, 1, 11)
    out = []
    for i in range(10):
        lo, hi = float(bins[i]), float(bins[i + 1])
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        out.append({
            "bucket_lo": lo,
            "bucket_hi": hi,
            "predicted_mid": (lo + hi) / 2,
            "n": n,
            "observed_rate": float(y_true[mask].mean()),
        })
    return out


def validation_row(model_type, scope, probs, y_true, roc_auc):
    return {
        "model_type": model_type,
        "scope": scope,
        "n_events": int(len(y_true)),
        "roc_auc": float(roc_auc) if roc_auc is not None else None,
        "brier_score": float(brier_score_loss(y_true, probs)),
        "log_loss": float(log_loss(y_true, probs, labels=[0, 1])),
        "reliability_bins": reliability_bins(probs, y_true),
    }


def save_validation(rows, n_games_train, n_games_test):
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wpa_model_validation (
            id SERIAL PRIMARY KEY,
            computed_at TIMESTAMPTZ NOT NULL,
            model_type TEXT NOT NULL DEFAULT 'logreg_calibrated',
            scope TEXT NOT NULL,
            n_games_train INT,
            n_games_test INT,
            n_events INT,
            roc_auc DOUBLE PRECISION,
            brier_score DOUBLE PRECISION,
            log_loss DOUBLE PRECISION,
            reliability_bins JSONB
        );
    """)
    # Additive — rows created before model_type existed all get the deployed
    # model's real label, same "never silently guess" pattern used elsewhere
    # (e.g. player_shots.shot_zone_basic backfill in api/shots_lib.py).
    cursor.execute("ALTER TABLE wpa_model_validation ADD COLUMN IF NOT EXISTS model_type TEXT NOT NULL DEFAULT 'logreg_calibrated';")
    cursor.execute("TRUNCATE TABLE wpa_model_validation;")
    computed_at = datetime.now(timezone.utc)
    values = [
        (
            computed_at, r["model_type"], r["scope"], n_games_train, n_games_test, r["n_events"],
            r["roc_auc"], r["brier_score"], r["log_loss"],
            psycopg2.extras.Json(r["reliability_bins"]),
        )
        for r in rows
    ]
    psycopg2.extras.execute_values(
        cursor,
        """INSERT INTO wpa_model_validation
           (computed_at, model_type, scope, n_games_train, n_games_test, n_events, roc_auc, brier_score, log_loss, reliability_bins)
           VALUES %s;""",
        values,
    )
    conn.commit()
    conn.close()


def sanity_checks(model, scaler):
    print("\n  Sanity checks on known real scenarios:")
    cases = [
        ("Tied game, 10 sec left", 10, 0),
        ("Home up 20, 1 min left", 60, 20),
        ("Home down 20, 1 min left", 60, -20),
        ("Home up 3, tip-off (48 min left)", 2880, 3),
        ("Home up 1, 5 sec left", 5, 1),
    ]
    for label, secs, margin in cases:
        margin_per_sqrt = margin / math.sqrt(secs + 1)
        X = scaler.transform([[secs, margin, margin_per_sqrt]])
        p = model.predict_proba(X)[0, 1]
        print(f"    {label:35s} -> P(home wins) = {p:.3f}")


def main():
    print("Loading real play-by-play data...")
    df = load_data()
    print(f"  {len(df):,} real events across {df['game_id'].nunique()} real games")

    df = build_features(df)
    df = df.dropna(subset=FEATURES + ["home_win"])

    X = df[FEATURES].values
    y = df["home_win"].values
    groups = df["game_id"].values

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
    train_idx, test_idx = next(splitter.split(X, y, groups))
    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]
    print(f"  Train: {len(X_train):,} events ({df['game_id'].iloc[train_idx].nunique()} games)")
    print(f"  Test:  {len(X_test):,} events ({df['game_id'].iloc[test_idx].nunique()} games) [held out by GAME, not by row]")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    train_groups = df["game_id"].iloc[train_idx].values

    base_model = LogisticRegression(max_iter=1000)
    base_model.fit(X_train_scaled, y_train)
    raw_auc = roc_auc_score(y_test, base_model.predict_proba(scaler.transform(X_test))[:, 1])
    print(f"\n  Raw model real held-out ROC-AUC: {raw_auc:.4f}")
    print(f"  Coefficients: {dict(zip(FEATURES, base_model.coef_[0]))}")
    print("\n  --- Raw model calibration (before fixing) ---")
    calibration_check(base_model, scaler, X_test, y_test)

    # The raw logistic regression's calibration is measurably off in the
    # 10-40% bucket range (checked above) — exactly the close-game range
    # clutch WPA cares about most. Isotonic calibration (Platt/isotonic
    # recalibration is standard, published practice for exactly this
    # problem, not a workaround) is fit via its OWN internal group-aware
    # cross-validation on the training games only, so the held-out test
    # games below are never touched until final evaluation.
    n_groups = len(set(train_groups))
    cv_splitter = GroupKFold(n_splits=min(5, n_groups))
    precomputed_splits = list(cv_splitter.split(X_train_scaled, y_train, groups=train_groups))
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=precomputed_splits)
    model.fit(X_train_scaled, y_train)
    # CalibratedClassifierCV retains whatever `cv` was as a plain attribute
    # (`self.cv`) even after fitting, purely for introspection — predict_proba()
    # only ever reads self.calibrated_classifiers_/self.classes_, verified
    # against this sklearn version's own source before relying on it. Passing
    # a precomputed list of (train_idx, test_idx) arrays (needed: this
    # sklearn's metadata-routing API failed to forward a plain `groups=`
    # kwarg to GroupKFold without extra global config) meant `self.cv` held
    # a full materialized index array per fold — measured to alone pickle to
    # ~115MB over real ~2.9M-event training folds, blowing past GitHub's
    # 100MB limit once C8 grew training data from ~170k to ~2.9M events.
    # Dropping it here shrinks the saved model without touching behavior.
    model.cv = None

    auc = roc_auc_score(y_test, model.predict_proba(scaler.transform(X_test))[:, 1])
    print(f"\n  Calibrated model real held-out ROC-AUC: {auc:.4f}")
    print("\n  --- Calibrated model calibration (after fixing) ---")
    calibration_check(model, scaler, X_test, y_test)
    sanity_checks(model, scaler)

    with open("wpa_model.pkl", "wb") as f:
        pickle.dump(model, f)
    with open("wpa_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    print("\n✅ Saved wpa_model.pkl, wpa_scaler.pkl")

    # ─── Store calibration validation for the Model Validation tab ─────────
    # Two scopes, both on the calibrated (deployed) model's real held-out
    # predictions: every test event, and the subset that's real clutch time
    # — the range clutch WPA actually uses. Whole-game calibration can look
    # weaker than clutch-specific calibration (a real, disclosed nuance, not
    # a bug), so both are stored rather than only the flattering one.
    test_df = df.iloc[test_idx]
    calibrated_probs = model.predict_proba(scaler.transform(X_test))[:, 1]

    clutch_mask = (
        (test_df["period"].values >= 4)
        & (test_df["seconds_remaining"].values <= CLUTCH_SECONDS)
        & (np.abs(test_df["score_margin_home"].values) <= CLUTCH_MARGIN)
    )
    n_clutch = int(clutch_mask.sum())

    def scored_rows(model_type, probs, label):
        out = [validation_row(model_type, "all_events", probs, y_test, roc_auc_score(y_test, probs))]
        if n_clutch > 0:
            clutch_y = y_test[clutch_mask]
            clutch_probs = probs[clutch_mask]
            clutch_auc = roc_auc_score(clutch_y, clutch_probs) if len(set(clutch_y)) > 1 else None
            out.append(validation_row(model_type, "clutch_only", clutch_probs, clutch_y, clutch_auc))
            print(f"  [{label}] clutch-time-only test events: {n_clutch:,}" + (f" (AUC {clutch_auc:.4f})" if clutch_auc else ""))
        return out

    rows = scored_rows("logreg_calibrated", calibrated_probs, "Logistic Regression (deployed)")

    # ─── Gradient Boosting alternative — evaluated, NOT deployed ───────────
    # Same train/test rows and features as the deployed model above, for a
    # real apples-to-apples comparison. HistGradientBoostingClassifier (not
    # plain GradientBoostingClassifier) because this table has 3.6M+ real
    # events, where sklearn's histogram-binned implementation is the one
    # actually built to train at that scale in reasonable time.
    print("\n  --- Gradient Boosting alternative (evaluated, not deployed) ---")
    gb_model = HistGradientBoostingClassifier(max_iter=200, max_depth=6, random_state=42)
    gb_model.fit(X_train_scaled, y_train)
    gb_probs = gb_model.predict_proba(scaler.transform(X_test))[:, 1]
    gb_auc = roc_auc_score(y_test, gb_probs)
    print(f"  Gradient Boosting real held-out ROC-AUC: {gb_auc:.4f} (deployed Logistic Regression: {auc:.4f})")
    rows += scored_rows("gradient_boosting", gb_probs, "Gradient Boosting")

    n_games_train = int(df["game_id"].iloc[train_idx].nunique())
    n_games_test = int(df["game_id"].iloc[test_idx].nunique())
    save_validation(rows, n_games_train, n_games_test)
    print(f"✅ Saved calibration validation ({len(rows)} row(s), 2 models) to wpa_model_validation")


if __name__ == "__main__":
    main()
