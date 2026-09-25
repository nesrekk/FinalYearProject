"""
precompute_career_similarity.py
================================
Precompute top-10 career-level cosine similarities for ALL players
and store in PostgreSQL (career_similarity table).

Aggregates season-level stats into career averages, then computes
pairwise similarity across all players.

Usage:
    python precompute_career_similarity.py
"""

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

from db_config import DB_CONFIG

FEATURES = [
    "career_pts", "career_ts_pct", "career_usg_pct", "career_net_rating",
    "career_ast_pct", "career_reb_pct", "career_min",
    "seasons_played", "peak_pts",
]

TOP_N = 10
PROGRESS_INTERVAL = 200
# nba_api-era seasons only: pre-2010 rows (load_kaggle_historical_seasons.py) are
# Basketball-Reference-sourced and were never part of the career-similarity pool.
MIN_SEASON = 2010


# ─── Step 1: Load and aggregate data ────────────────────────────────────────

def load_data():
    """
    Pull season-level data from PostgreSQL and aggregate per player
    into career-level stats.
    """
    print("=" * 60)
    print("Step 1: Loading and aggregating career stats")
    print("=" * 60)

    query = f"""
        SELECT
            player_id,
            player_name,
            AVG(pts)        AS career_pts,
            AVG(ts_pct)     AS career_ts_pct,
            AVG(usg_pct)    AS career_usg_pct,
            AVG(net_rating) AS career_net_rating,
            AVG(ast_pct)    AS career_ast_pct,
            AVG(reb_pct)    AS career_reb_pct,
            AVG(min)        AS career_min,
            COUNT(season)   AS seasons_played,
            MAX(pts)        AS peak_pts
        FROM player_season_stats
        WHERE season >= {MIN_SEASON}
          AND pts IS NOT NULL
          AND ts_pct IS NOT NULL
          AND usg_pct IS NOT NULL
          AND net_rating IS NOT NULL
          AND ast_pct IS NOT NULL
          AND reb_pct IS NOT NULL
          AND min IS NOT NULL
        GROUP BY player_id, player_name;
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(query, conn)
        print(f"  Players: {len(df):,}")
        print(f"  Columns: {list(df.columns)}")

        # Quick stats
        print(f"\n  Seasons played range: {int(df['seasons_played'].min())}–{int(df['seasons_played'].max())}")
        print(f"  Career PPG range:     {df['career_pts'].min():.1f}–{df['career_pts'].max():.1f}")
        print(f"  Peak PPG range:       {df['peak_pts'].min():.1f}–{df['peak_pts'].max():.1f}")

        return df

    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2: Normalize features ─────────────────────────────────────────────

def scale_features(df):
    """Fit StandardScaler on career features."""
    print("\n" + "=" * 60)
    print("Step 2: Normalizing features")
    print("=" * 60)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df[FEATURES].values)
    print(f"  ✅ Scaled {X_scaled.shape[0]} players × {X_scaled.shape[1]} features")
    return X_scaled


# ─── Step 3: Setup target table ─────────────────────────────────────────────

def setup_target_table():
    """Create career_similarity table and index, then truncate."""
    print("\n" + "=" * 60)
    print("Step 3: Setting up career_similarity table")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS career_similarity (
                source_player_id BIGINT,
                similar_player_id BIGINT,
                similarity_score FLOAT
            );
        """)
        print("  ✅ Table created/verified")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_career_similarity_lookup
            ON career_similarity (source_player_id);
        """)
        print("  ✅ Index created/verified")

        cursor.execute("TRUNCATE TABLE career_similarity;")
        print("  ✅ Table truncated (ready for fresh insert)")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 4: Compute and insert similarities ────────────────────────────────

def compute_and_insert(df, X_scaled):
    """
    For each player, compute cosine similarity against all others (row-by-row),
    get top 10, and bulk insert into PostgreSQL.
    """
    print("\n" + "=" * 60)
    print("Step 4: Computing career similarities and inserting")
    print("=" * 60)

    total = len(df)
    print(f"  Processing {total:,} players × top {TOP_N} each")
    print(f"  Expected inserts: {total * TOP_N:,} rows\n")

    insert_query = """
        INSERT INTO career_similarity
            (source_player_id, similar_player_id, similarity_score)
        VALUES (%s, %s, %s)
    """

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        batch = []
        batch_size = 5000
        start_time = time.time()

        for i in range(total):
            # Compute similarity for this player against all
            row_vector = X_scaled[i].reshape(1, -1)
            sims = cosine_similarity(row_vector, X_scaled)[0]

            # Exclude self
            sims[i] = -1.0

            # Get top N indices
            top_indices = np.argpartition(sims, -TOP_N)[-TOP_N:]
            top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]

            source_pid = int(df.iloc[i]["player_id"])

            for idx in top_indices:
                batch.append((
                    source_pid,
                    int(df.iloc[idx]["player_id"]),
                    float(sims[idx]),
                ))

            # Flush batch
            if len(batch) >= batch_size:
                cursor.executemany(insert_query, batch)
                conn.commit()
                batch = []

            # Progress
            if (i + 1) % PROGRESS_INTERVAL == 0 or (i + 1) == total:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                eta = (total - i - 1) / rate if rate > 0 else 0
                print(f"  [{i + 1:>5,} / {total:,}]  "
                      f"{(i + 1) / total * 100:5.1f}%  "
                      f"{rate:.0f} players/s  "
                      f"ETA: {eta:.0f}s")

        # Flush remaining
        if batch:
            cursor.executemany(insert_query, batch)
            conn.commit()

        total_time = time.time() - start_time
        print(f"\n  ✅ Inserted {total * TOP_N:,} rows in {total_time:.1f}s")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 5: Verify ─────────────────────────────────────────────────────────

def verify(df):
    """Print row count and sample query."""
    print("\n" + "=" * 60)
    print("Step 5: Verification")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM career_similarity;")
        count = cursor.fetchone()[0]
        print(f"  Total rows in career_similarity: {count:,}")

        # Sample: careers most similar to LeBron James (player_id=2544)
        cursor.execute("""
            SELECT
                c.similar_player_id,
                p.player_name,
                c.similarity_score
            FROM career_similarity c
            JOIN (
                SELECT DISTINCT player_id, player_name
                FROM player_season_stats
            ) p ON c.similar_player_id = p.player_id
            WHERE c.source_player_id = 2544
            ORDER BY c.similarity_score DESC
            LIMIT 5;
        """)
        rows = cursor.fetchall()
        if rows:
            print(f"\n  Sample — Careers similar to LeBron James:")
            print(f"  {'Player':<28} {'Score':>8}")
            print(f"  {'─' * 28} {'─' * 8}")
            for r in rows:
                print(f"  {r[1]:<28} {r[2]:>8.4f}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
    finally:
        if conn:
            conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Precompute Career-Level Similarities")
    print("=" * 60)
    print(f"  Top N:     {TOP_N}")
    print(f"  Features:  {FEATURES}")
    print()

    df = load_data()
    X_scaled = scale_features(df)
    setup_target_table()
    compute_and_insert(df, X_scaled)
    verify(df)

    print("\n" + "=" * 60)
    print("🎉 Done! career_similarity table is ready.")
    print("   Query it from TablePlus or your app.")
    print("=" * 60)


if __name__ == "__main__":
    main()
