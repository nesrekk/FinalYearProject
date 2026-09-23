"""
build_similarity_engine.py
==========================
NBA Player Season Similarity Engine — Cosine Similarity

Connects to PostgreSQL (nba_analytics), normalizes player stats,
and finds the most similar player-seasons using cosine similarity.

Usage:
    python build_similarity_engine.py

Output:
    - similarity_scaler.pkl  (fitted StandardScaler)
"""

import os
import sys
import warnings
import pickle
import numpy as np
import pandas as pd
import psycopg2
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity

warnings.filterwarnings("ignore")

# ─── Configuration ──────────────────────────────────────────────────────────

from db_config import DB_CONFIG

FEATURES = [
    "pts", "ts_pct", "usg_pct", "net_rating",
    "ast_pct", "reb_pct", "age", "min",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCALER_PATH = os.path.join(SCRIPT_DIR, "similarity_scaler.pkl")


# ─── Data Loading ───────────────────────────────────────────────────────────

def load_data():
    """
    Connect to PostgreSQL and pull player season stats into a DataFrame.
    Drops rows with NULL values in any selected feature.
    """
    print("=" * 60)
    print("Loading data from PostgreSQL")
    print("=" * 60)

    query = """
        SELECT
            player_id,
            player_name,
            season,
            pts,
            ts_pct,
            usg_pct,
            net_rating,
            ast_pct,
            reb_pct,
            age,
            min
        FROM player_season_stats;
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(query, conn)
        print(f"  Raw rows:     {len(df):,}")

        # Drop rows with NULLs in any feature column
        before = len(df)
        df = df.dropna(subset=FEATURES).reset_index(drop=True)
        dropped = before - len(df)
        if dropped > 0:
            print(f"  Dropped:      {dropped} rows with NULL values")
        print(f"  Clean rows:   {len(df):,}")
        print(f"  Seasons:      {df['season'].min()}–{df['season'].max()}")
        print(f"  Players:      {df['player_name'].nunique():,}")

        return df

    except Exception as e:
        print(f"  ❌ Database error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Feature Scaling ────────────────────────────────────────────────────────

def scale_features(df):
    """
    Normalize features using StandardScaler.
    Returns the scaler and the scaled feature matrix.
    """
    print("\n" + "=" * 60)
    print("Normalizing features (StandardScaler)")
    print("=" * 60)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df[FEATURES].values)

    print(f"  ✅ Scaled {X_scaled.shape[0]} rows × {X_scaled.shape[1]} features")

    # Save scaler
    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
    print(f"  ✅ Scaler saved: {SCALER_PATH}")

    return scaler, X_scaled


# ─── Similarity Function ────────────────────────────────────────────────────

def get_similar_seasons(player_name, season, df, X_scaled, top_n=10):
    """
    Find the most similar player-seasons to a given player + season.

    Args:
        player_name: Full player name (e.g., "Stephen Curry")
        season:      Season ending year (e.g., 2016 for 2015-16)
        df:          Full DataFrame with player data
        X_scaled:    Normalized feature matrix (same row order as df)
        top_n:       Number of similar seasons to return

    Returns:
        DataFrame with top_n most similar player-seasons
    """
    print("\n" + "=" * 60)
    print(f"Finding similar seasons to: {player_name} ({season})")
    print("=" * 60)

    # Find the target row
    mask = (df["player_name"] == player_name) & (df["season"] == season)
    matches = df[mask]

    if matches.empty:
        print(f"  ❌ No matching row for {player_name} in season {season}")
        print(f"  Available seasons for this player:")
        player_rows = df[df["player_name"] == player_name]
        if player_rows.empty:
            print(f"     Player not found. Check spelling.")
        else:
            for _, r in player_rows.iterrows():
                print(f"     {int(r['season'])}")
        return None

    target_idx = matches.index[0]
    target_vector = X_scaled[target_idx].reshape(1, -1)

    # Print target player stats
    target = df.loc[target_idx]
    print(f"\n  Target Stats:")
    print(f"    PTS: {target['pts']:.1f}  TS%: {target['ts_pct']:.3f}  "
          f"USG%: {target['usg_pct']:.3f}  NET: {target['net_rating']:.1f}")
    print(f"    AST%: {target['ast_pct']:.3f}  REB%: {target['reb_pct']:.3f}  "
          f"AGE: {int(target['age'])}  MIN: {target['min']:.1f}")

    # Compute cosine similarity against all rows
    similarities = cosine_similarity(target_vector, X_scaled)[0]

    # Build results (exclude the exact target row)
    results = df.copy()
    results["similarity_score"] = similarities
    results = results.drop(index=target_idx)
    results = results.sort_values("similarity_score", ascending=False).head(top_n)

    # Print results
    print(f"\n  Top {top_n} Most Similar Seasons:")
    print(f"  {'Rank':<6} {'Player':<28} {'Season':>7} {'Similarity':>11}")
    print(f"  {'─' * 6} {'─' * 28} {'─' * 7} {'─' * 11}")
    for rank, (_, row) in enumerate(results.iterrows(), 1):
        print(f"  {rank:<6} {row['player_name']:<28} {int(row['season']):>7} "
              f"{row['similarity_score']:>10.4f}")

    return results[["player_name", "season", "similarity_score"]].reset_index(drop=True)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 NBA Player Season Similarity Engine")
    print("=" * 60)
    print(f"  Features: {FEATURES}")
    print(f"  Metric:   Cosine Similarity")
    print()

    # Load and scale
    df = load_data()
    scaler, X_scaled = scale_features(df)

    # Example: Find seasons similar to Steph Curry's unanimous MVP year
    get_similar_seasons("Stephen Curry", 2016, df, X_scaled, top_n=10)


if __name__ == "__main__":
    main()
