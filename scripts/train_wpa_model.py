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
situations actually resolve to a win about 70% of the real time.

Usage:
    cd scripts && python3 train_wpa_model.py
"""

import math
import pickle

import numpy as np
import pandas as pd
import psycopg2
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from db_config import DB_CONFIG


def load_data():
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT e.game_id, e.seconds_remaining, e.score_home, e.score_away, g.home_win
        FROM pbp_events e
        JOIN pbp_games g ON g.game_id = e.game_id
        WHERE e.action_type != 'period';
    """
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


if __name__ == "__main__":
    main()
