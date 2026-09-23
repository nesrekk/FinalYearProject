"""
build_all_nba_model.py
========================
Train a logistic regression model to predict All-NBA Team selection
(binary: made any of First/Second/Third Team) using the label dataset
built by fetch_all_nba_teams.py (240 real historical selections, 2009-10
through 2024-25, sourced and verified — see that script's docstring).

Why this is a different shape of problem than MVP/DPOY/ROY: those are
single-winner-per-season awards, so backtest_models.py's run_loso() (which
scores "what rank did the actual single winner land at") applies directly.
All-NBA has 15 winners per season across 3 tiers, so "rank of the winner"
doesn't make sense — the honest metric here is set overlap: of the 15
players we'd have predicted, how many were actually selected that season
(precision@15). That's computed separately below rather than forcing this
into the existing single-winner backtest machinery.

Tiering (First/Second/Third) in the live predict endpoint is reconstructed
by simple rank cutoff (top 5 probability = First, next 5 = Second, next 5
= Third) — a transparent approximation, not a model of actual voting
patterns per tier, and it's documented as such in the API response.

Outputs:
    models/all_nba_model.pkl, models/all_nba_scaler.pkl
    PostgreSQL table: all_nba_backtest_summary

Usage:
    python build_all_nba_model.py
"""

import os
import pickle
import json
import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, roc_curve

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_DIR = os.path.join(BASE_DIR, "models")

from db_config import DB_CONFIG

FEATURES = [
    "pts", "reb", "ast", "stl", "blk",
    "ts_pct", "usg_pct", "net_rating", "w_pct", "min", "age",
]
MIN_MINUTES = 24
MIN_GAMES = 40
TRAIN_SEASONS = list(range(2010, 2025))  # 2009-10 through 2023-24
TEST_SEASON = 2025                        # 2024-25 (real holdout, not fabricated)
SELECTIONS_PER_SEASON = 15


def load_data():
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql_query(
        f"""
        SELECT p.player_id, p.player_name, p.season, {', '.join(FEATURES)}
        FROM player_season_stats p
        WHERE p.min >= {MIN_MINUTES} AND p.gp >= {MIN_GAMES};
        """,
        conn,
    )
    labels = pd.read_sql_query("SELECT player_id, season FROM all_nba_seasons;", conn)
    conn.close()

    labels["all_nba_label"] = 1
    df = df.merge(labels, on=["player_id", "season"], how="left")
    df["all_nba_label"] = df["all_nba_label"].fillna(0).astype(int)
    df = df.dropna(subset=FEATURES).reset_index(drop=True)
    return df


def loso_backtest(df):
    """
    Leave-one-season-out: for each season, train on every other season,
    predict probabilities for that season's candidate pool, take the top
    15 by probability, and measure precision@15 against who was actually
    selected. Averaged across all 16 seasons — an honest, out-of-sample
    accuracy number, not a training-set fit. Also records the top-15
    predicted names for each held-out season (for a real example table,
    same spirit as MVP/DPOY/ROY's per-season detail) and pools every
    out-of-fold probability to compute a real ROC curve, not just the AUC
    number.
    """
    seasons = sorted(df["season"].unique())
    per_season = []
    all_probs, all_labels = [], []

    for season in seasons:
        train = df[df["season"] != season]
        test = df[df["season"] == season]
        if train["all_nba_label"].sum() == 0 or test.empty:
            continue

        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[FEATURES].values)
        y_train = train["all_nba_label"].values

        model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42, solver="lbfgs")
        model.fit(X_train, y_train)

        X_test = scaler.transform(test[FEATURES].values)
        probs = model.predict_proba(X_test)[:, 1]

        ranked = test.copy()
        ranked["prob"] = probs
        ranked = ranked.sort_values("prob", ascending=False)
        top15 = ranked.head(SELECTIONS_PER_SEASON)
        hits = int(top15["all_nba_label"].sum())
        precision = hits / SELECTIONS_PER_SEASON

        per_season.append({
            "season": int(season),
            "hits": hits,
            "precision_at_15": precision,
            "top15": [
                {"player_name": r.player_name, "probability": float(r.prob), "actual_selection": bool(r.all_nba_label)}
                for r in top15.itertuples()
            ],
        })

        all_probs.extend(probs)
        all_labels.extend(test["all_nba_label"].values)

    mean_precision = float(np.mean([s["precision_at_15"] for s in per_season]))
    auc = float(roc_auc_score(all_labels, all_probs))

    fpr, tpr, _ = roc_curve(all_labels, all_probs)
    n = len(fpr)
    max_points = 60
    idx = range(n) if n <= max_points else sorted(set(np.linspace(0, n - 1, max_points).astype(int).tolist()))
    roc_curve_points = [{"fpr": float(fpr[i]), "tpr": float(tpr[i])} for i in idx]

    print(f"\nLOSO backtest — {len(per_season)} seasons")
    print(f"  Mean precision@15: {mean_precision:.3f} ({mean_precision*15:.1f}/15 correct on average)")
    print(f"  ROC-AUC: {auc:.4f}")
    return mean_precision, auc, per_season, roc_curve_points


def train_final_model(df):
    train = df[df["season"].isin(TRAIN_SEASONS)]
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train[FEATURES].values)
    y_train = train["all_nba_label"].values

    model = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42, solver="lbfgs")
    model.fit(X_train, y_train)

    coefs = sorted(zip(FEATURES, model.coef_[0]), key=lambda x: abs(x[1]), reverse=True)
    print("\nFinal model (trained on 2010-2024, held out 2025) coefficients:")
    for feat, coef in coefs:
        print(f"  {feat:<12} {coef:>+8.4f}")
    feature_importance = [
        {"feature": feat, "value": float(coef), "signed": True} for feat, coef in coefs
    ]

    # Sanity-check against the real, held-out 2024-25 All-NBA teams
    holdout_season = None
    test = df[df["season"] == TEST_SEASON]
    if not test.empty:
        X_test = scaler.transform(test[FEATURES].values)
        probs = model.predict_proba(X_test)[:, 1]
        ranked = test.copy()
        ranked["prob"] = probs
        ranked = ranked.sort_values("prob", ascending=False).head(SELECTIONS_PER_SEASON)
        hits = int(ranked["all_nba_label"].sum())
        print(f"\n2024-25 holdout sanity check: {hits}/15 of our top-15 picks were real All-NBA selections.")
        print("  Top 15 predicted:")
        for i, row in enumerate(ranked.itertuples(), 1):
            mark = "✓" if row.all_nba_label == 1 else " "
            print(f"    {i:>2}. {mark} {row.player_name:<28} {row.prob:.3f}")
        holdout_season = {
            "season": TEST_SEASON,
            "hits": hits,
            "precision_at_15": hits / SELECTIONS_PER_SEASON,
            "top15": [
                {"player_name": r.player_name, "probability": float(r.prob), "actual_selection": bool(r.all_nba_label)}
                for r in ranked.itertuples()
            ],
        }

    return model, scaler, feature_importance, holdout_season


def save_model(model, scaler):
    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(os.path.join(MODEL_DIR, "all_nba_model.pkl"), "wb") as f:
        pickle.dump(model, f)
    with open(os.path.join(MODEL_DIR, "all_nba_scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)
    print(f"\nSaved model + scaler to {MODEL_DIR}")


def save_backtest_results(mean_precision, auc, per_season, roc_curve_points, feature_importance, holdout_season):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS all_nba_backtest_summary (
            award TEXT PRIMARY KEY,
            n_seasons_evaluated INTEGER NOT NULL,
            mean_precision_at_15 DOUBLE PRECISION NOT NULL,
            roc_auc DOUBLE PRECISION NOT NULL,
            roc_curve JSONB,
            feature_importance JSONB,
            holdout_season JSONB,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    # Additive — in case an earlier run created the table before these
    # columns existed (same "don't break what's already there" convention
    # used for player_season_stats' migration earlier this session).
    cur.execute("ALTER TABLE all_nba_backtest_summary ADD COLUMN IF NOT EXISTS roc_curve JSONB;")
    cur.execute("ALTER TABLE all_nba_backtest_summary ADD COLUMN IF NOT EXISTS feature_importance JSONB;")
    cur.execute("ALTER TABLE all_nba_backtest_summary ADD COLUMN IF NOT EXISTS holdout_season JSONB;")

    cur.execute("""
        INSERT INTO all_nba_backtest_summary
            (award, n_seasons_evaluated, mean_precision_at_15, roc_auc, roc_curve, feature_importance, holdout_season)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (award) DO UPDATE SET
            n_seasons_evaluated = EXCLUDED.n_seasons_evaluated,
            mean_precision_at_15 = EXCLUDED.mean_precision_at_15,
            roc_auc = EXCLUDED.roc_auc,
            roc_curve = EXCLUDED.roc_curve,
            feature_importance = EXCLUDED.feature_importance,
            holdout_season = EXCLUDED.holdout_season,
            updated_at = NOW();
        """,
        (
            "ALL_NBA", len(per_season), mean_precision, auc,
            json.dumps(roc_curve_points), json.dumps(feature_importance), json.dumps(holdout_season),
        ),
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS all_nba_backtest_seasons (
            award TEXT NOT NULL,
            season INTEGER NOT NULL,
            hits INTEGER NOT NULL,
            precision_at_15 DOUBLE PRECISION NOT NULL,
            top15 JSONB NOT NULL,
            PRIMARY KEY (award, season)
        );
    """)
    cur.execute("DELETE FROM all_nba_backtest_seasons WHERE award = %s;", ("ALL_NBA",))
    execute_values(
        cur,
        "INSERT INTO all_nba_backtest_seasons (award, season, hits, precision_at_15, top15) VALUES %s;",
        [("ALL_NBA", s["season"], s["hits"], s["precision_at_15"], json.dumps(s["top15"])) for s in per_season],
    )

    conn.commit()
    conn.close()
    print("Saved backtest results to all_nba_backtest_summary + all_nba_backtest_seasons.")


if __name__ == "__main__":
    print("Building All-NBA Team prediction model...")
    df = load_data()
    print(f"Candidate pool: {len(df):,} player-seasons (min>={MIN_MINUTES}, gp>={MIN_GAMES}), "
          f"{int(df['all_nba_label'].sum())} positive labels")

    mean_precision, auc, per_season, roc_curve_points = loso_backtest(df)
    model, scaler, feature_importance, holdout_season = train_final_model(df)
    save_model(model, scaler)
    save_backtest_results(mean_precision, auc, per_season, roc_curve_points, feature_importance, holdout_season)
