"""
build_dpoy_roy_models.py
=========================
Build DPOY and ROY prediction models using existing data from PostgreSQL.

Data source: player_season_stats table (already loaded from NBA API).
Winners: Hardcoded (reliable, no API calls needed for awards).

Usage:
    python build_dpoy_roy_models.py

Outputs:
    nba_data/dpoy_training_data.csv
    nba_data/roy_training_data.csv
    models/dpoy_model.pkl, dpoy_scaler.pkl
    models/roy_model.pkl, roy_scaler.pkl
"""

import os
import sys
import pickle
import warnings
import numpy as np
import pandas as pd
import psycopg2
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, confusion_matrix

warnings.filterwarnings("ignore")

# ─── Configuration ──────────────────────────────────────────────────────────

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")
MODEL_DIR = os.path.join(BASE_DIR, "models")

from db_config import DB_CONFIG

TRAIN_SEASONS = list(range(2010, 2025))  # 2009-10 through 2023-24
TEST_SEASON = 2025                        # 2024-25

# ROY excludes the very first season in the dataset (2009-10): a player's
# "rookie season" is derived as the first season they appear in
# player_season_stats, which is indistinguishable from "already a veteran
# when our data starts" for that one boundary season. See
# add_candidate_pool_flags().
ROY_TRAIN_SEASONS = [s for s in TRAIN_SEASONS if s != min(TRAIN_SEASONS)]

# DPOY features (stl, blk used as proxies for stl_pct, blk_pct)
DPOY_FEATURES = ["def_rating", "net_rating", "stl", "blk", "reb_pct", "min", "w_pct"]

# ROY features
ROY_FEATURES = ["pts", "ts_pct", "usg_pct", "net_rating", "min", "age"]

# Every historical DPOY winner played >=28.4 min and >=56 games in their
# winning season (checked against the DB) — these thresholds keep real
# winners in with margin while cutting bench/low-minute players out of the
# candidate pool.
DPOY_MIN_MINUTES = 24
DPOY_MIN_GAMES = 40

# ─── Award Winners (2009-10 to 2023-24) ─────────────────────────────────────

DPOY_WINNERS = {
    2010: "Dwight Howard",
    2011: "Dwight Howard",
    2012: "Tyson Chandler",
    2013: "Marc Gasol",
    2014: "Joakim Noah",
    2015: "Kawhi Leonard",
    2016: "Kawhi Leonard",
    2017: "Draymond Green",
    2018: "Rudy Gobert",
    2019: "Rudy Gobert",
    2020: "Giannis Antetokounmpo",
    2021: "Rudy Gobert",
    2022: "Marcus Smart",
    2023: "Jaren Jackson Jr.",
    2024: "Rudy Gobert",
    2025: "Evan Mobley",
}

ROY_WINNERS = {
    2010: "Tyreke Evans",
    2011: "Blake Griffin",
    2012: "Kyrie Irving",
    2013: "Damian Lillard",
    2014: "Michael Carter-Williams",
    2015: "Andrew Wiggins",
    2016: "Karl-Anthony Towns",
    2017: "Malcolm Brogdon",
    2018: "Ben Simmons",
    2019: "Luka Dončić",
    2020: "Ja Morant",
    2021: "LaMelo Ball",
    2022: "Scottie Barnes",
    2023: "Paolo Banchero",
    2024: "Victor Wembanyama",  # unanimous winner (99/99 first-place votes); was
                                # incorrectly Chet Holmgren (a finalist, not the winner)
    2025: "Stephon Castle",
}


# ─── Step 1: Load data from PostgreSQL ──────────────────────────────────────

def load_data():
    """Pull all player-season stats from the database."""
    print("=" * 60)
    print("Step 1: Loading data from PostgreSQL")
    print("=" * 60)

    query = """
        SELECT player_id, player_name, season, age, gp, min,
               pts, reb, ast, stl, blk, tov,
               fg_pct, fg3_pct, ft_pct, w_pct, plus_minus,
               ts_pct, usg_pct, off_rating, def_rating,
               net_rating, ast_pct, reb_pct
        FROM player_season_stats;
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(query, conn)
        print(f"  Loaded {len(df):,} rows")
        print(f"  Seasons: {sorted(df['season'].unique())}")
        return df
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2: Create labels ──────────────────────────────────────────────────

def add_labels(df):
    """Add dpoy_label and roy_label columns based on award winner lists."""
    print("\n" + "=" * 60)
    print("Step 2: Creating award labels")
    print("=" * 60)

    # DPOY labels — match by name + season
    df["dpoy_label"] = 0
    for season, winner in DPOY_WINNERS.items():
        mask = (df["season"] == season) & (df["player_name"] == winner)
        matches = mask.sum()
        df.loc[mask, "dpoy_label"] = 1
        if matches == 0:
            # Try partial match
            partial = (df["season"] == season) & (
                df["player_name"].str.contains(winner.split()[-1], case=False, na=False)
            )
            df.loc[partial, "dpoy_label"] = 1
            matches = partial.sum()
        status = "✓" if matches > 0 else "✗ NOT FOUND"
        print(f"  DPOY {season}: {winner} — {status}")

    # ROY labels
    df["roy_label"] = 0
    for season, winner in ROY_WINNERS.items():
        mask = (df["season"] == season) & (df["player_name"] == winner)
        matches = mask.sum()
        df.loc[mask, "roy_label"] = 1
        if matches == 0:
            # Try partial match
            partial = (df["season"] == season) & (
                df["player_name"].str.contains(winner.split()[-1], case=False, na=False)
            )
            df.loc[partial, "roy_label"] = 1
            matches = partial.sum()
        status = "✓" if matches > 0 else "✗ NOT FOUND"
        print(f"  ROY  {season}: {winner} — {status}")

    dpoy_total = df["dpoy_label"].sum()
    roy_total = df["roy_label"].sum()
    print(f"\n  DPOY labels: {dpoy_total} positive / {len(df)} total")
    print(f"  ROY  labels: {roy_total} positive / {len(df)} total")

    return df


# ─── Step 3: Restrict each award to a relevant candidate pool ──────────────

def first_nba_seasons():
    """player_id -> first NBA season, from player_first_season."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("SELECT player_id, first_season FROM player_first_season;")
        return {int(pid): int(first) for pid, first in cur.fetchall()}
    finally:
        conn.close()


def add_candidate_pool_flags(df):
    """
    Without this, DPOY/ROY are trained to pick the winner out of EVERY
    player in the league that season (~400+ people) — bench scrubs and
    30ppg stars alike. A 21ppg rookie season doesn't look special next to
    prime LeBron/Curry, so the model buries real winners hundreds of spots
    down (confirmed via backtest_models.py: ~0% top-5 accuracy for both
    awards). Restricting to the actual reference group each award is judged
    against fixes this.

      - ROY: only each player's rookie season (his first NBA season), so the
        model compares Luka to other rookies, not to the entire league. The
        first season comes from player_first_season (build_first_nba_season.py:
        the earlier of Basketball-Reference's first NBA season and the first
        season in this table). Using only this table's first season, as before
        2026-09-27, counted 240 players whose earlier short stints are missing
        from it as rookies.
      - DPOY: only rows above a minutes/games floor comfortably below every
        historical winner's actual minutes, cutting out low-minute players
        who were never realistic candidates.
    """
    rookie_season = df["player_id"].map(first_nba_seasons())
    # Players the first-season table doesn't know (it's rebuilt from this same
    # table, so only brand-new rows) fall back to their first season here.
    rookie_season = rookie_season.fillna(df.groupby("player_id")["season"].transform("min"))
    df["is_rookie_candidate"] = df["season"] == rookie_season
    df["is_dpoy_candidate"] = (df["min"] >= DPOY_MIN_MINUTES) & (df["gp"] >= DPOY_MIN_GAMES)
    return df


# ─── Step 4: Save training datasets ─────────────────────────────────────────

def save_training_data(df):
    """Save labeled, candidate-pool-restricted datasets as CSVs."""
    print("\n" + "=" * 60)
    print("Step 4: Restricting candidate pools + saving training data CSVs")
    print("=" * 60)

    os.makedirs(DATA_DIR, exist_ok=True)

    # DPOY dataset — only plausible-minutes candidates
    dpoy_cols = ["player_id", "player_name", "season"] + DPOY_FEATURES + ["dpoy_label"]
    dpoy_df = df[df["is_dpoy_candidate"]][dpoy_cols].dropna(subset=DPOY_FEATURES)
    dpoy_path = os.path.join(DATA_DIR, "dpoy_training_data.csv")
    dpoy_df.to_csv(dpoy_path, index=False)
    print(f"  ✅ DPOY candidate pool: {len(dpoy_df):,} rows (min>={DPOY_MIN_MINUTES}, gp>={DPOY_MIN_GAMES}) → {dpoy_path}")

    # ROY dataset — only each player's own rookie season, and never the
    # 2009-10 boundary season (see ROY_TRAIN_SEASONS).
    roy_cols = ["player_id", "player_name", "season"] + ROY_FEATURES + ["roy_label"]
    roy_df = df[df["is_rookie_candidate"] & (df["season"] != min(TRAIN_SEASONS))][roy_cols].dropna(subset=ROY_FEATURES)
    roy_path = os.path.join(DATA_DIR, "roy_training_data.csv")
    roy_df.to_csv(roy_path, index=False)
    print(f"  ✅ ROY candidate pool:  {len(roy_df):,} rows (rookie seasons only) → {roy_path}")

    return dpoy_df, roy_df


# ─── Step 4: Train model helper ─────────────────────────────────────────────

def train_model(df, features, label_col, award_name, train_seasons=None):
    """
    Train a Logistic Regression model for a given award.
    Returns (model, scaler, test_df).
    """
    train_seasons = train_seasons if train_seasons is not None else TRAIN_SEASONS
    print(f"\n  Training {award_name} model...")
    print(f"  Features: {features}")

    # Split
    train = df[df["season"].isin(train_seasons)].copy()
    test = df[df["season"] == TEST_SEASON].copy()

    print(f"  Train: {len(train):,} rows ({train[label_col].sum()} positive)")
    print(f"  Test:  {len(test):,} rows (season {TEST_SEASON})")

    if len(train) == 0 or train[label_col].sum() == 0:
        print(f"  ❌ Insufficient training data for {award_name}")
        return None, None, test

    # Scale
    scaler = StandardScaler()
    X_train = scaler.fit_transform(train[features].values)
    y_train = train[label_col].values

    # Train
    model = LogisticRegression(
        class_weight="balanced",
        max_iter=1000,
        random_state=42,
        solver="lbfgs",
    )
    model.fit(X_train, y_train)

    # Training metrics
    y_pred = model.predict(X_train)
    acc = accuracy_score(y_train, y_pred)
    cm = confusion_matrix(y_train, y_pred)
    print(f"  Training accuracy: {acc:.4f} ({acc*100:.2f}%)")
    print(f"  Confusion matrix:")
    print(f"    TN={cm[0][0]:,}  FP={cm[0][1]:,}")
    print(f"    FN={cm[1][0]:,}  TP={cm[1][1]:,}")

    # Coefficients
    print(f"  Coefficients:")
    coefs = sorted(zip(features, model.coef_[0]), key=lambda x: abs(x[1]), reverse=True)
    for feat, coef in coefs:
        print(f"    {feat:<15} {coef:>+8.4f}")

    return model, scaler, test


def predict_top(model, scaler, test_df, features, award_name, top_n=5):
    """Predict and print top candidates for 2025."""
    if model is None or len(test_df) == 0:
        print(f"  ⚠️  Cannot predict {award_name} for {TEST_SEASON}")
        return

    X_test = scaler.transform(test_df[features].values)
    probs = model.predict_proba(X_test)[:, 1]

    results = test_df[["player_name", "player_id"]].copy()
    results["probability"] = probs
    results = results.sort_values("probability", ascending=False).head(top_n)

    print(f"\n  🏆 Top {top_n} Predicted {award_name} ({TEST_SEASON}):")
    print(f"  {'Rank':<6} {'Player':<30} {'Probability':>12}")
    print(f"  {'─' * 6} {'─' * 30} {'─' * 12}")
    for rank, (_, row) in enumerate(results.iterrows(), 1):
        print(f"  {rank:<6} {row['player_name']:<30} {row['probability']:>11.4f}")


# ─── Step 5: Train and save models ──────────────────────────────────────────

def train_and_save_models(dpoy_df, roy_df):
    """Train DPOY and ROY models, save to disk."""
    print("\n" + "=" * 60)
    print("Step 4: Training models")
    print("=" * 60)

    os.makedirs(MODEL_DIR, exist_ok=True)

    # ── DPOY ──
    dpoy_model, dpoy_scaler, dpoy_test = train_model(
        dpoy_df, DPOY_FEATURES, "dpoy_label", "DPOY", train_seasons=TRAIN_SEASONS
    )
    predict_top(dpoy_model, dpoy_scaler, dpoy_test, DPOY_FEATURES, "DPOY")

    # ── ROY (restricted to each player's own rookie season; see
    #     ROY_TRAIN_SEASONS for why 2009-10 is excluded) ──
    roy_model, roy_scaler, roy_test = train_model(
        roy_df, ROY_FEATURES, "roy_label", "ROY", train_seasons=ROY_TRAIN_SEASONS
    )
    predict_top(roy_model, roy_scaler, roy_test, ROY_FEATURES, "ROY")

    # ── Save ──
    print("\n" + "=" * 60)
    print("Step 5: Saving models")
    print("=" * 60)

    artifacts = {
        "dpoy_model.pkl": dpoy_model,
        "dpoy_scaler.pkl": dpoy_scaler,
        "roy_model.pkl": roy_model,
        "roy_scaler.pkl": roy_scaler,
    }

    for name, obj in artifacts.items():
        if obj is not None:
            path = os.path.join(MODEL_DIR, name)
            with open(path, "wb") as f:
                pickle.dump(obj, f)
            size_kb = os.path.getsize(path) / 1024
            print(f"  ✅ {name} ({size_kb:.1f} KB)")
        else:
            print(f"  ⚠️  Skipped {name} (no model trained)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Build DPOY & ROY Prediction Models")
    print("=" * 60)
    print(f"  Data source: PostgreSQL (nba_analytics)")
    print(f"  Train:       seasons {min(TRAIN_SEASONS)}–{max(TRAIN_SEASONS)}")
    print(f"  Predict:     season {TEST_SEASON}")
    print(f"  DPOY feat:   {DPOY_FEATURES}")
    print(f"  ROY feat:    {ROY_FEATURES}")
    print()

    df = load_data()
    df = add_labels(df)
    df = add_candidate_pool_flags(df)
    dpoy_df, roy_df = save_training_data(df)
    train_and_save_models(dpoy_df, roy_df)

    print("\n" + "=" * 60)
    print("🎉 Done! Models trained and saved.")
    print(f"   Data:   {DATA_DIR}")
    print(f"   Models: {MODEL_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
