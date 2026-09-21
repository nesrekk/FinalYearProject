"""
precompute_league_similarity.py
================================
Precompute top-10 league-adjusted season similarities for ALL player-seasons
and store in PostgreSQL (season_similarity table).

Usage:
    python precompute_league_similarity.py
"""

import os
import sys
import time
import warnings
import numpy as np
import pandas as pd
import psycopg2
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity

warnings.filterwarnings("ignore")

# ─── Configuration ──────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

ADJUST_COLS = ["pts", "ts_pct", "usg_pct", "net_rating", "ast_pct", "reb_pct"]

FEATURES = [
    "adj_pts", "adj_ts_pct", "adj_usg_pct", "adj_net_rating",
    "adj_ast_pct", "adj_reb_pct", "age", "min",
]

TOP_N = 10
PROGRESS_INTERVAL = 500


# ─── Step 1: Load data from PostgreSQL ──────────────────────────────────────

def load_data():
    """Pull player season stats and drop NULLs."""
    print("=" * 60)
    print("Step 1: Loading data from PostgreSQL")
    print("=" * 60)

    query = """
        SELECT
            player_id,
            player_name,
            season,
            pts, ts_pct, usg_pct, net_rating,
            ast_pct, reb_pct, age, min
        FROM player_season_stats;
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(query, conn)
        before = len(df)
        df = df.dropna(subset=ADJUST_COLS + ["age", "min"]).reset_index(drop=True)
        dropped = before - len(df)
        print(f"  Loaded: {len(df):,} rows ({dropped} dropped for NULLs)")
        return df
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2: League adjustment ──────────────────────────────────────────────

def apply_league_adjustment(df):
    """Subtract per-season league averages from selected stats."""
    print("\n" + "=" * 60)
    print("Step 2: Computing league-adjusted stats")
    print("=" * 60)

    season_means = df.groupby("season")[ADJUST_COLS].mean()

    for col in ADJUST_COLS:
        df[f"adj_{col}"] = df.apply(
            lambda row: row[col] - season_means.loc[row["season"], col],
            axis=1,
        )

    print(f"  ✅ Created {len(ADJUST_COLS)} adjusted columns")
    return df


# ─── Step 3: Normalize features ─────────────────────────────────────────────

def scale_features(df):
    """Fit StandardScaler on all features."""
    print("\n" + "=" * 60)
    print("Step 3: Normalizing features")
    print("=" * 60)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df[FEATURES].values)
    print(f"  ✅ Scaled {X_scaled.shape[0]} rows × {X_scaled.shape[1]} features")
    return X_scaled


# ─── Step 4: Setup target table ─────────────────────────────────────────────

def setup_target_table():
    """Create season_similarity table and index if they don't exist, then truncate."""
    print("\n" + "=" * 60)
    print("Step 4: Setting up season_similarity table")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_similarity (
                source_player_id BIGINT,
                source_season INT,
                similar_player_id BIGINT,
                similar_season INT,
                similarity_score FLOAT
            );
        """)
        print("  ✅ Table created/verified")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_similarity_lookup
            ON season_similarity (source_player_id, source_season);
        """)
        print("  ✅ Index created/verified")

        # Truncate before inserting fresh data
        cursor.execute("TRUNCATE TABLE season_similarity;")
        print("  ✅ Table truncated (ready for fresh insert)")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 5: Compute and insert similarities ────────────────────────────────

def compute_and_insert(df, X_scaled):
    """
    For each player-season, compute cosine similarity against ALL rows (row-by-row
    for memory efficiency), get top 10, and bulk insert into PostgreSQL.
    """
    print("\n" + "=" * 60)
    print("Step 5: Computing similarities and inserting into DB")
    print("=" * 60)

    total_rows = len(df)
    print(f"  Processing {total_rows:,} player-seasons × top {TOP_N} each")
    print(f"  Expected inserts: {total_rows * TOP_N:,} rows\n")

    insert_query = """
        INSERT INTO season_similarity
            (source_player_id, source_season, similar_player_id, similar_season, similarity_score)
        VALUES (%s, %s, %s, %s, %s)
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        batch = []
        batch_size = 5000  # Flush every 5000 rows
        start_time = time.time()

        for i in range(total_rows):
            # Compute similarity for this row against all rows
            row_vector = X_scaled[i].reshape(1, -1)
            sims = cosine_similarity(row_vector, X_scaled)[0]

            # Exclude self (set to -1 so it won't appear in top N)
            sims[i] = -1.0

            # Get top N indices
            top_indices = np.argpartition(sims, -TOP_N)[-TOP_N:]
            top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

            source_pid = int(df.iloc[i]["player_id"])
            source_season = int(df.iloc[i]["season"])

            for idx in top_indices:
                batch.append((
                    source_pid,
                    source_season,
                    int(df.iloc[idx]["player_id"]),
                    int(df.iloc[idx]["season"]),
                    float(sims[idx]),
                ))

            # Flush batch periodically
            if len(batch) >= batch_size:
                cursor.executemany(insert_query, batch)
                conn.commit()
                batch = []

            # Progress update
            if (i + 1) % PROGRESS_INTERVAL == 0 or (i + 1) == total_rows:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (total_rows - i - 1) / rate if rate > 0 else 0
                print(f"  [{i + 1:>6,} / {total_rows:,}]  "
                      f"{(i + 1) / total_rows * 100:5.1f}%  "
                      f"{rate:.0f} rows/s  "
                      f"ETA: {eta:.0f}s")

        # Flush remaining
        if batch:
            cursor.executemany(insert_query, batch)
            conn.commit()

        total_time = time.time() - start_time
        total_inserts = total_rows * TOP_N
        print(f"\n  ✅ Inserted {total_inserts:,} rows in {total_time:.1f}s")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 6: Verify ─────────────────────────────────────────────────────────

def verify():
    """Print row count and a sample query to confirm data."""
    print("\n" + "=" * 60)
    print("Step 6: Verification")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM season_similarity;")
        count = cursor.fetchone()[0]
        print(f"  Total rows in season_similarity: {count:,}")

        # Sample: top 5 similar to Stephen Curry 2016 (player_id=201939)
        cursor.execute("""
            SELECT
                s.similar_player_id,
                p.player_name,
                s.similar_season,
                s.similarity_score
            FROM season_similarity s
            JOIN player_season_stats p
                ON s.similar_player_id = p.player_id
                AND s.similar_season = p.season
            WHERE s.source_player_id = 201939
              AND s.source_season = 2016
            ORDER BY s.similarity_score DESC
            LIMIT 5;
        """)
        rows = cursor.fetchall()
        if rows:
            print(f"\n  Sample — Similar to Stephen Curry (2016):")
            print(f"  {'Player':<28} {'Season':>7} {'Score':>8}")
            print(f"  {'─' * 28} {'─' * 7} {'─' * 8}")
            for r in rows:
                print(f"  {r[1]:<28} {r[2]:>7} {r[3]:>8.4f}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
    finally:
        if conn:
            conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Precompute League-Adjusted Season Similarities")
    print("=" * 60)
    print(f"  Top N:     {TOP_N}")
    print(f"  Features:  {FEATURES}")
    print()

    df = load_data()
    df = apply_league_adjustment(df)
    X_scaled = scale_features(df)
    setup_target_table()
    compute_and_insert(df, X_scaled)
    verify()

    print("\n" + "=" * 60)
    print("🎉 Done! season_similarity table is ready.")
    print("   Query it from TablePlus or your app.")
    print("=" * 60)


if __name__ == "__main__":
    main()
