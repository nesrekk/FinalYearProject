"""
train_mvp_model.py
==================
NBA MVP Prediction Model — Logistic Regression

Connects to PostgreSQL (nba_analytics), trains on 2009–2024 seasons,
and predicts MVP probabilities for the 2025 season.

Usage:
    python train_mvp_model.py

Outputs:
    - mvp_model.pkl   (trained Logistic Regression model)
    - mvp_scaler.pkl  (fitted StandardScaler)
"""

import os
import sys
import warnings
import pickle
import numpy as np
import pandas as pd
import psycopg2
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, confusion_matrix

warnings.filterwarnings("ignore")

# ─── Configuration ──────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

FEATURES = [
    "pts", "ts_pct", "usg_pct", "off_rating", "def_rating",
    "net_rating", "w_pct", "min", "age",
]

TRAIN_SEASONS = list(range(2010, 2025))  # 2009-10 through 2023-24
TEST_SEASON = 2025                        # 2024-25

# Save artifacts next to the script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, "mvp_model.pkl")
SCALER_PATH = os.path.join(SCRIPT_DIR, "mvp_scaler.pkl")


# ─── Step 1: Connect to PostgreSQL and pull data ────────────────────────────

def load_data():
    """
    Pull full dataset from PostgreSQL using a LEFT JOIN between
    player_season_stats and mvp_winners. Returns a pandas DataFrame
    with an mvp_label column (1 = MVP, 0 = not).
    """
    print("=" * 60)
    print("STEP 1: Loading data from PostgreSQL")
    print("=" * 60)

    query = """
        SELECT 
            p.player_id,
            p.player_name,
            p.season,
            p.pts,
            p.ts_pct,
            p.usg_pct,
            p.off_rating,
            p.def_rating,
            p.net_rating,
            p.w_pct,
            p.min,
            p.age,
            CASE 
                WHEN m.player_id IS NOT NULL THEN 1
                ELSE 0
            END AS mvp_label
        FROM player_season_stats p
        LEFT JOIN mvp_winners m
            ON p.player_id = m.player_id
            AND p.season = m.season;
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(query, conn)
        print(f"  ✅ Loaded {len(df):,} rows × {len(df.columns)} columns")
        print(f"  Seasons: {sorted(df['season'].unique())}")
        print(f"  MVP labels: {df['mvp_label'].sum()} positive / {len(df)} total")
        return df
    except Exception as e:
        print(f"  ❌ Database error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2: Prepare train/test splits ──────────────────────────────────────

def prepare_data(df):
    """
    Split data into training (2010–2024) and test (2025) sets.
    Drop rows with missing feature values.
    """
    print("\n" + "=" * 60)
    print("STEP 2: Preparing train/test split")
    print("=" * 60)

    # Drop rows with missing features
    before = len(df)
    df = df.dropna(subset=FEATURES)
    dropped = before - len(df)
    if dropped > 0:
        print(f"  Dropped {dropped} rows with missing feature values")

    # Split
    train_df = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test_df = df[df["season"] == TEST_SEASON].copy()

    print(f"  Training set: {len(train_df):,} rows (seasons {min(TRAIN_SEASONS)}–{max(TRAIN_SEASONS)})")
    print(f"    MVPs in training: {train_df['mvp_label'].sum()}")
    print(f"  Test set:     {len(test_df):,} rows (season {TEST_SEASON})")

    if len(train_df) == 0:
        print("  ❌ No training data found!")
        sys.exit(1)

    if len(test_df) == 0:
        print("  ⚠️  No 2025 season data — predictions will be skipped")

    return train_df, test_df


# ─── Step 3: Normalize features ─────────────────────────────────────────────

def scale_features(train_df, test_df):
    """
    Fit StandardScaler on training features, transform both sets.
    """
    print("\n" + "=" * 60)
    print("STEP 3: Normalizing features (StandardScaler)")
    print("=" * 60)

    scaler = StandardScaler()

    X_train = train_df[FEATURES].values
    y_train = train_df["mvp_label"].values

    X_train_scaled = scaler.fit_transform(X_train)
    print(f"  ✅ Scaler fitted on {X_train_scaled.shape[0]} training samples")
    print(f"  Feature means: {dict(zip(FEATURES, [f'{m:.2f}' for m in scaler.mean_]))}")

    X_test_scaled = None
    if len(test_df) > 0:
        X_test = test_df[FEATURES].values
        X_test_scaled = scaler.transform(X_test)
        print(f"  ✅ Test set transformed: {X_test_scaled.shape[0]} samples")

    return X_train_scaled, y_train, X_test_scaled, scaler


# ─── Step 4: Train Logistic Regression ──────────────────────────────────────

def train_model(X_train_scaled, y_train):
    """
    Train Logistic Regression with balanced class weights.
    """
    print("\n" + "=" * 60)
    print("STEP 4: Training Logistic Regression")
    print("=" * 60)

    model = LogisticRegression(
        class_weight="balanced",
        max_iter=1000,
        random_state=42,
        solver="lbfgs",
    )

    model.fit(X_train_scaled, y_train)
    print("  ✅ Model trained successfully")

    # ── Coefficients ──
    print("\n  Model Coefficients:")
    print(f"  {'Feature':<15} {'Coefficient':>12}")
    print(f"  {'─' * 15} {'─' * 12}")
    coefs = list(zip(FEATURES, model.coef_[0]))
    coefs_sorted = sorted(coefs, key=lambda x: abs(x[1]), reverse=True)
    for feat, coef in coefs_sorted:
        sign = "+" if coef >= 0 else ""
        print(f"  {feat:<15} {sign}{coef:>11.4f}")
    print(f"  {'Intercept':<15} {model.intercept_[0]:>+11.4f}")

    # ── Training accuracy ──
    y_pred_train = model.predict(X_train_scaled)
    acc = accuracy_score(y_train, y_pred_train)
    print(f"\n  Training Accuracy: {acc:.4f} ({acc*100:.2f}%)")

    # ── Confusion matrix ──
    cm = confusion_matrix(y_train, y_pred_train)
    print(f"\n  Confusion Matrix (Train):")
    print(f"                  Predicted")
    print(f"                  Non-MVP   MVP")
    print(f"  Actual Non-MVP  {cm[0][0]:>7}  {cm[0][1]:>5}")
    print(f"  Actual MVP      {cm[1][0]:>7}  {cm[1][1]:>5}")

    tn, fp, fn, tp = cm.ravel()
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    print(f"\n  Precision: {precision:.4f}")
    print(f"  Recall:    {recall:.4f}")

    return model


# ─── Step 5: Predict 2025 MVP ───────────────────────────────────────────────

def predict_2025(model, X_test_scaled, test_df):
    """
    Predict MVP probabilities for the 2025 season.
    Print top 10 candidates sorted by probability.
    """
    print("\n" + "=" * 60)
    print("STEP 5: 2025 MVP Predictions")
    print("=" * 60)

    if X_test_scaled is None or len(test_df) == 0:
        print("  ⚠️  No 2025 season data available — skipping predictions")
        return

    # Get probabilities (second column = probability of being MVP)
    probs = model.predict_proba(X_test_scaled)[:, 1]

    # Build results DataFrame
    results = test_df[["player_name", "player_id"]].copy()
    results["mvp_probability"] = probs
    results = results.sort_values("mvp_probability", ascending=False).reset_index(drop=True)

    # Print top 10
    print("\n  Top 10 MVP Candidates (2024-25 Season):")
    print(f"  {'Rank':<6} {'Player':<30} {'MVP Probability':>15}")
    print(f"  {'─' * 6} {'─' * 30} {'─' * 15}")
    for i, row in results.head(10).iterrows():
        print(f"  {i + 1:<6} {row['player_name']:<30} {row['mvp_probability']:>14.4f}")


# ─── Step 6: Save model and scaler ──────────────────────────────────────────

def save_artifacts(model, scaler):
    """
    Save the trained model and scaler as pickle files.
    """
    print("\n" + "=" * 60)
    print("STEP 6: Saving model artifacts")
    print("=" * 60)

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"  ✅ Model saved: {MODEL_PATH}")

    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
    print(f"  ✅ Scaler saved: {SCALER_PATH}")

    model_size = os.path.getsize(MODEL_PATH) / 1024
    scaler_size = os.path.getsize(SCALER_PATH) / 1024
    print(f"  Model size:  {model_size:.1f} KB")
    print(f"  Scaler size: {scaler_size:.1f} KB")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 NBA MVP Prediction Model")
    print("=" * 60)
    print(f"  Features:       {len(FEATURES)}")
    print(f"  Train seasons:  {min(TRAIN_SEASONS)}–{max(TRAIN_SEASONS)}")
    print(f"  Test season:    {TEST_SEASON}")
    print()

    # Pipeline
    df = load_data()
    train_df, test_df = prepare_data(df)
    X_train_scaled, y_train, X_test_scaled, scaler = scale_features(train_df, test_df)
    model = train_model(X_train_scaled, y_train)
    predict_2025(model, X_test_scaled, test_df)
    save_artifacts(model, scaler)

    print("\n" + "=" * 60)
    print("🎉 Done! Model trained and saved.")
    print(f"   Model:  {MODEL_PATH}")
    print(f"   Scaler: {SCALER_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
