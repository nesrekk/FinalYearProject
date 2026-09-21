"""
build_shap_explanations.py
===========================
SHAP explainability for the Random Forest award models.

Why Random Forest specifically, not Logistic Regression: a logistic
regression prediction is already fully explainable — probability comes from
sum(coefficient * standardized_feature), so the coefficients ARE the
explanation, globally and per-prediction alike (see Model Validation's
feature importance table). A Random Forest's prediction comes from voting
across 200 trees with arbitrary split thresholds — there's no simple formula
to point to. That's exactly the gap SHAP fills: shap.TreeExplainer decomposes
one specific player's predicted probability into "how many percentage points
did each feature push this player's chance up or down, starting from the
model's average prediction" — a real explanation for a model that otherwise
has none.

This computes SHAP values for the current prediction target (TEST_SEASON,
2025) using the exact same Random Forest configuration already validated in
backtest_models.py's LOSO comparison — this isn't a new/different model,
it's an explanation of the one already shown to work reasonably (MVP: 86.7%
top-5 in backtesting) or reasonably-not (DPOY/ROY, where Logistic Regression
won — SHAP still computed here for completeness/comparison, with that
context preserved rather than hidden).

Usage:
    cd scripts && python3 build_shap_explanations.py

Writes to Postgres: shap_explanations (one row per player-season-feature).
"""

import os
import sys
import warnings

import numpy as np
import psycopg2
import psycopg2.extras
import shap
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import train_mvp_model as mvp_mod              # noqa: E402
import build_dpoy_roy_models as dpoy_roy_mod    # noqa: E402
import backtest_models as bt                    # noqa: E402

DB_CONFIG = mvp_mod.DB_CONFIG
TEST_SEASON = mvp_mod.TEST_SEASON  # 2025
TOP_N = 30  # only explain the plausible candidates, not all ~400 players


def compute_for_award(award_name, df, features, eval_seasons):
    train = df[df["season"].isin(eval_seasons)]
    test = df[df["season"] == TEST_SEASON]
    if len(test) == 0:
        print(f"  {award_name}: no {TEST_SEASON} data, skipping.")
        return []

    scaler = StandardScaler()
    X_train = scaler.fit_transform(train[features].values)
    y_train = train["label"].values
    model = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)

    X_test = scaler.transform(test[features].values)
    probs = model.predict_proba(X_test)[:, 1]

    test = test.copy()
    test["predicted_probability"] = probs
    top = test.sort_values("predicted_probability", ascending=False).head(TOP_N)
    top_idx_positions = [test.index.get_loc(i) for i in top.index]

    explainer = shap.TreeExplainer(model)
    sv = explainer(X_test[top_idx_positions])
    # sv.values shape: (n_top, n_features, 2 classes) — take class 1 (winner)
    class1_values = sv.values[:, :, 1]
    base_value = float(np.array(sv.base_values)[0, 1])  # same for every row (model's overall base rate)

    rows = []
    for i, (_, player_row) in enumerate(top.iterrows()):
        for f_idx, feature in enumerate(features):
            rows.append({
                "player_id": int(player_row["player_id"]),
                "player_name": player_row["player_name"],
                "predicted_probability": float(player_row["predicted_probability"]),
                "feature": feature,
                "feature_value": float(player_row[feature]),
                "shap_value": float(class1_values[i, f_idx]),
            })
    print(f"  {award_name}: explained top {len(top)} candidates x {len(features)} features.")
    return rows, base_value


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS shap_explanations;")
    cur.execute("""
        CREATE TABLE shap_explanations (
            id SERIAL PRIMARY KEY,
            award TEXT NOT NULL,
            season INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            predicted_probability DOUBLE PRECISION NOT NULL,
            base_value DOUBLE PRECISION NOT NULL,
            feature TEXT NOT NULL,
            feature_value DOUBLE PRECISION,
            shap_value DOUBLE PRECISION NOT NULL
        );
    """)
    cur.execute("CREATE INDEX idx_shap_award_season ON shap_explanations(award, season);")
    conn.commit()


def save_rows(conn, award_name, base_value, rows):
    cur = conn.cursor()
    values = [
        (award_name, TEST_SEASON, r["player_id"], r["player_name"], r["predicted_probability"],
         base_value, r["feature"], r["feature_value"], r["shap_value"])
        for r in rows
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO shap_explanations
            (award, season, player_id, player_name, predicted_probability, base_value,
             feature, feature_value, shap_value)
        VALUES %s;
        """,
        values,
    )
    conn.commit()


def main():
    print(f"SHAP explanations for Random Forest — {TEST_SEASON} season, top {TOP_N} candidates per award")

    jobs = [
        ("MVP", *bt.load_mvp_data(), mvp_mod.TRAIN_SEASONS),
        ("DPOY", *bt.load_award_data(dpoy_roy_mod.DPOY_FEATURES, dpoy_roy_mod.DPOY_WINNERS, pool_column="is_dpoy_candidate"), mvp_mod.TRAIN_SEASONS),
        ("ROY", *bt.load_award_data(dpoy_roy_mod.ROY_FEATURES, dpoy_roy_mod.ROY_WINNERS, pool_column="is_rookie_candidate"), dpoy_roy_mod.ROY_TRAIN_SEASONS),
    ]

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)

    for award_name, df, features, eval_seasons in jobs:
        result = compute_for_award(award_name, df, features, eval_seasons)
        if not result:
            continue
        rows, base_value = result
        save_rows(conn, award_name, base_value, rows)

    conn.close()
    print("Done. Results saved to shap_explanations.")


if __name__ == "__main__":
    main()
