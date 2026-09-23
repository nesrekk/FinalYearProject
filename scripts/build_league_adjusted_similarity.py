"""
build_league_adjusted_similarity.py
====================================
NBA League-Adjusted Player Season Similarity Engine

Adjusts each player's stats relative to their season's league average
before computing cosine similarity, enabling fair cross-era comparisons.

Usage:
    python build_league_adjusted_similarity.py

Output:
    - league_similarity_scaler.pkl  (fitted StandardScaler)
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

# Stats to league-adjust (subtract season mean)
ADJUST_COLS = ["pts", "ts_pct", "usg_pct", "net_rating", "ast_pct", "reb_pct"]

# Final features for similarity (adjusted stats + raw age/min)
FEATURES = [
    "adj_pts", "adj_ts_pct", "adj_usg_pct", "adj_net_rating",
    "adj_ast_pct", "adj_reb_pct", "age", "min",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCALER_PATH = os.path.join(SCRIPT_DIR, "league_similarity_scaler.pkl")


# ─── Data Loading ───────────────────────────────────────────────────────────

def load_data():
    """
    Connect to PostgreSQL and pull player season stats.
    Drops rows with NULL values in any selected column.
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

        # Drop NULLs
        all_cols = ADJUST_COLS + ["age", "min"]
        before = len(df)
        df = df.dropna(subset=all_cols).reset_index(drop=True)
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


# ─── League Adjustment ──────────────────────────────────────────────────────

def apply_league_adjustment(df):
    """
    For each season, subtract the league average from each stat.
    Creates adj_* columns. Does NOT adjust age or min.
    """
    print("\n" + "=" * 60)
    print("Applying league adjustment (season-relative stats)")
    print("=" * 60)

    # Compute per-season league averages
    season_means = df.groupby("season")[ADJUST_COLS].mean()
    print("\n  League Averages by Season:")
    print(f"  {'Season':>7}  {'PTS':>6}  {'TS%':>6}  {'USG%':>6}  {'NET':>6}  {'AST%':>6}  {'REB%':>6}")
    print(f"  {'─' * 7}  {'─' * 6}  {'─' * 6}  {'─' * 6}  {'─' * 6}  {'─' * 6}  {'─' * 6}")
    for season, row in season_means.iterrows():
        print(f"  {int(season):>7}  {row['pts']:>6.1f}  {row['ts_pct']:>6.3f}  "
              f"{row['usg_pct']:>6.3f}  {row['net_rating']:>6.1f}  "
              f"{row['ast_pct']:>6.3f}  {row['reb_pct']:>6.3f}")

    # Subtract season averages to create adjusted columns
    for col in ADJUST_COLS:
        adj_col = f"adj_{col}"
        df[adj_col] = df.apply(
            lambda row: row[col] - season_means.loc[row["season"], col],
            axis=1,
        )

    # Show adjustment effect for a sample player
    print(f"\n  ✅ Created {len(ADJUST_COLS)} adjusted columns: {[f'adj_{c}' for c in ADJUST_COLS]}")
    print(f"  Note: age and min are NOT adjusted")

    return df


# ─── Feature Scaling ────────────────────────────────────────────────────────

def scale_features(df):
    """
    Normalize the adjusted features using StandardScaler.
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
    Find the most similar player-seasons using league-adjusted cosine similarity.

    Args:
        player_name: Full player name (e.g., "Stephen Curry")
        season:      Season ending year (e.g., 2016 for 2015-16)
        df:          DataFrame with player data and adjusted columns
        X_scaled:    Normalized feature matrix (same row order as df)
        top_n:       Number of similar seasons to return

    Returns:
        DataFrame with top_n most similar player-seasons
    """
    print("\n" + "=" * 60)
    print(f"Finding similar seasons to: {player_name} ({season})")
    print("  [League-Adjusted Cosine Similarity]")
    print("=" * 60)

    # Find the target row
    mask = (df["player_name"] == player_name) & (df["season"] == season)
    matches = df[mask]

    if matches.empty:
        print(f"  ❌ No matching row for {player_name} in season {season}")
        player_rows = df[df["player_name"] == player_name]
        if player_rows.empty:
            print(f"     Player not found. Check spelling.")
        else:
            print(f"  Available seasons:")
            for _, r in player_rows.iterrows():
                print(f"     {int(r['season'])}")
        return None

    target_idx = matches.index[0]
    target_vector = X_scaled[target_idx].reshape(1, -1)

    # Print target player stats (raw and adjusted)
    t = df.loc[target_idx]
    print(f"\n  Target Stats (Raw):")
    print(f"    PTS: {t['pts']:.1f}  TS%: {t['ts_pct']:.3f}  "
          f"USG%: {t['usg_pct']:.3f}  NET: {t['net_rating']:.1f}")
    print(f"    AST%: {t['ast_pct']:.3f}  REB%: {t['reb_pct']:.3f}  "
          f"AGE: {int(t['age'])}  MIN: {t['min']:.1f}")
    print(f"\n  Target Stats (League-Adjusted):")
    print(f"    adj_PTS: {t['adj_pts']:+.1f}  adj_TS%: {t['adj_ts_pct']:+.3f}  "
          f"adj_USG%: {t['adj_usg_pct']:+.3f}  adj_NET: {t['adj_net_rating']:+.1f}")
    print(f"    adj_AST%: {t['adj_ast_pct']:+.3f}  adj_REB%: {t['adj_reb_pct']:+.3f}")

    # Compute cosine similarity against all rows
    similarities = cosine_similarity(target_vector, X_scaled)[0]

    # Build results (exclude exact target row)
    results = df.copy()
    results["similarity_score"] = similarities
    results = results.drop(index=target_idx)
    results = results.sort_values("similarity_score", ascending=False).head(top_n)

    # Print results
    print(f"\n  Top {top_n} Most Similar Seasons (League-Adjusted):")
    print(f"  {'Rank':<6} {'Player':<28} {'Season':>7} {'Similarity':>11}")
    print(f"  {'─' * 6} {'─' * 28} {'─' * 7} {'─' * 11}")
    for rank, (_, row) in enumerate(results.iterrows(), 1):
        print(f"  {rank:<6} {row['player_name']:<28} {int(row['season']):>7} "
              f"{row['similarity_score']:>10.4f}")

    return results[["player_name", "season", "similarity_score"]].reset_index(drop=True)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 NBA League-Adjusted Similarity Engine")
    print("=" * 60)
    print(f"  Adjusted stats: {ADJUST_COLS}")
    print(f"  Features:       {FEATURES}")
    print(f"  Metric:         Cosine Similarity")
    print()

    # Pipeline
    df = load_data()
    df = apply_league_adjustment(df)
    scaler, X_scaled = scale_features(df)

    # Example: Steph Curry's unanimous MVP year
    get_similar_seasons("Stephen Curry", 2016, df, X_scaled, top_n=10)


if __name__ == "__main__":
    main()
