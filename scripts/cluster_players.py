"""
cluster_players.py
===================
Unsupervised player archetype discovery via K-Means clustering.

What this is: unlike the MVP/DPOY/ROY models (supervised — trained to
predict a known label) or cosine similarity (compares one specific player to
others), this asks a genuinely different question with no "correct answer"
given in advance: "if we ignore who anyone is and just look at the shape of
their stat line, which players naturally group together?" K-Means finds
those groups by minimizing the distance between each player-season and the
center ("centroid") of the group it's assigned to, iterating until the
grouping stabilizes.

Why this matters for the project: it's the one feature that isn't asking
"who wins the award" or "who does X resemble" — it's asking "what are the
natural player types in the league," discovered from the data itself rather
than from box-score categories a human predefined (there's no "3&D wing"
column in the database; the cluster of players who all shoot a lot of 3s,
play good defense, and don't have the ball much emerges from the stats).

Method:
  1. Filter to a meaningful-sample candidate pool (min>=15 mpg, gp>=20) so
     small-sample noise doesn't distort a player's rate stats.
  2. Era-normalize 11 style-describing features (NOT raw scoring volume
     alone — a mix of scoring, playmaking, rebounding, defense, and
     efficiency rates): z-score each one within its OWN season, not pooled
     globally, so no single stat dominates just because of its raw scale
     AND league-wide shifts over 2010-2026 (3PA volume, pace, etc.) don't
     get mistaken for player differences (see era_normalize()).
  3. K-Means with K=6, chosen to match a conventional number of basketball
     archetypes and checked against silhouette scores for K=4..8.
  4. Each of the 6 discovered clusters gets a human-readable name by
     matching its centroid (via cosine similarity + Hungarian assignment,
     so the matching is optimal and one-to-one) against 6 hand-authored
     archetype profiles — e.g. a cluster whose centroid is high on
     reb/reb_pct/blk and low on fg3_pct gets matched to "Rim Protector".
     The underlying grouping is 100% data-driven; only the NAME given to
     each already-discovered group uses basketball knowledge.
  5. A 2D PCA projection of the same standardized features is stored
     alongside each player so the frontend can render an actual scatter
     plot of "player stat-space" colored by archetype, not just a table.

Usage:
    cd scripts && python3 cluster_players.py

Writes to Postgres: player_clusters (one row per player-season) and
cluster_archetypes (one row per archetype: centroid, size, representative
players, silhouette score).
"""

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score

from db_config import DB_CONFIG

FEATURES = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3_pct", "ts_pct", "usg_pct", "ast_pct", "reb_pct"]

MIN_MINUTES = 15
MIN_GAMES = 20
# nba_api-era seasons only: pre-2010 rows (load_kaggle_historical_seasons.py) are
# Basketball-Reference-sourced and were never part of the archetype pool.
MIN_SEASON = 2010

N_CLUSTERS = 6

# Hand-authored expected direction per feature for each archetype (not every
# feature needs an opinion — omitted ones default to neutral/0). These are
# matched against the ACTUAL discovered centroids by cosine similarity, not
# imposed on the clustering itself — K-Means never sees these.
ARCHETYPE_PROFILES = {
    "Primary Scorer": {"pts": 2, "usg_pct": 2, "ts_pct": 1, "fg3_pct": 0.5, "reb": -1, "blk": -1},
    "Playmaker": {"ast": 2, "ast_pct": 2, "tov": 1, "usg_pct": 0.5, "reb": -1, "blk": -1},
    "3-and-D Wing": {"fg3_pct": 2, "stl": 1, "ts_pct": 1, "usg_pct": -0.5, "ast": -0.5, "reb": -0.5},
    "Rim Protector": {"reb": 1, "reb_pct": 1, "blk": 1, "fg3_pct": -2, "ast": -1, "pts": -1, "usg_pct": -0.7},
    "Elite Two-Way Big": {"reb": 2, "reb_pct": 1.5, "blk": 2, "pts": 1, "ts_pct": 0.5, "fg3_pct": -0.5},
    "Bench Role Player": {"pts": -1.5, "usg_pct": -1.5, "ast": -1, "reb": -1, "blk": -1, "stl": -1, "ts_pct": -0.5},
}


def load_data():
    query = f"""
        SELECT player_id, player_name, team_abbreviation, season, min, gp,
               {', '.join(FEATURES)}
        FROM player_season_stats
        WHERE min >= {MIN_MINUTES} AND gp >= {MIN_GAMES} AND season >= {MIN_SEASON};
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    df = df.dropna(subset=FEATURES).reset_index(drop=True)
    return df


def era_normalize(df, features):
    """
    Z-score each feature within its OWN season (mean 0, std 1 per season)
    instead of pooling raw stats across 2010-2026 and standardizing once
    globally. The league has changed a lot over that window (3PA volume,
    pace, etc.) — without this, a player gets grouped by how their raw
    numbers compare to every other season combined, so era-wide shifts get
    misread as player differences. A 2011 stretch-4 and a 2025 stretch-4
    with equivalent ROLES can end up with very different raw fg3_pct/usg
    just because the league as a whole shot fewer 3s in 2011; z-scoring
    within each season fixes that by asking "how unusual is this player
    relative to their own year" instead of relative to the whole dataset.
    """
    season_mean = df.groupby("season")[features].transform("mean")
    season_std = df.groupby("season")[features].transform("std").replace(0, 1)
    return ((df[features] - season_mean) / season_std).values


def choose_k(X, k_range=range(4, 9)):
    """Prints silhouette scores for a range of K purely as justification —
    we still fix K=N_CLUSTERS for a stable, presentable archetype count."""
    print("  Silhouette scores by K (higher = better-separated clusters):")
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X)
        score = silhouette_score(X, labels)
        marker = "  <- chosen" if k == N_CLUSTERS else ""
        print(f"    K={k}: {score:.3f}{marker}")


def match_archetype_names(centroids, features):
    """
    Optimal one-to-one matching between discovered centroids and hand-
    authored archetype profiles via cosine similarity + Hungarian algorithm
    (scipy.optimize.linear_sum_assignment finds the assignment maximizing
    total similarity, so two centroids can't accidentally get the same name
    and every name gets used exactly once).
    """
    names = list(ARCHETYPE_PROFILES.keys())
    profile_matrix = np.array([
        [ARCHETYPE_PROFILES[name].get(f, 0) for f in features]
        for name in names
    ], dtype=float)
    profile_norms = profile_matrix / (np.linalg.norm(profile_matrix, axis=1, keepdims=True) + 1e-9)

    centroid_norms = centroids / (np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-9)

    similarity = centroid_norms @ profile_norms.T  # (n_clusters, n_profiles)
    cost = -similarity  # Hungarian minimizes cost; we want to maximize similarity
    row_idx, col_idx = linear_sum_assignment(cost)

    cluster_to_name = {}
    for r, c in zip(row_idx, col_idx):
        cluster_to_name[r] = names[c]
    return cluster_to_name


def main():
    print("=" * 70)
    print("Player Archetype Clustering (K-Means)")
    print("=" * 70)

    df = load_data()
    print(f"\nCandidate pool: {len(df):,} player-seasons (min>={MIN_MINUTES}, gp>={MIN_GAMES})")

    X = era_normalize(df, FEATURES)

    print()
    choose_k(X)

    print(f"\nFitting K-Means with K={N_CLUSTERS}...")
    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    cluster_ids = km.fit_predict(X)
    df["cluster_id"] = cluster_ids
    overall_silhouette = silhouette_score(X, cluster_ids)
    print(f"Final silhouette score: {overall_silhouette:.3f}")

    cluster_to_name = match_archetype_names(km.cluster_centers_, FEATURES)
    df["archetype"] = df["cluster_id"].map(cluster_to_name)

    print("\nArchetypes discovered:")
    for cid in range(N_CLUSTERS):
        name = cluster_to_name[cid]
        n = (df["cluster_id"] == cid).sum()
        print(f"  Cluster {cid} -> \"{name}\" ({n:,} player-seasons)")

    # Distance to own centroid (for "most representative examples" per archetype)
    dists = np.linalg.norm(X - km.cluster_centers_[cluster_ids], axis=1)
    df["distance_to_centroid"] = dists

    # 2D projection for the frontend scatter plot (separate from the 11-D
    # clustering itself — PCA here is purely for visualization).
    pca = PCA(n_components=2, random_state=42)
    coords = pca.fit_transform(X)
    df["pca_x"] = coords[:, 0]
    df["pca_y"] = coords[:, 1]
    explained = pca.explained_variance_ratio_
    print(f"\n2D PCA projection explains {explained[0]*100:.1f}% + {explained[1]*100:.1f}% "
          f"= {sum(explained)*100:.1f}% of feature variance (for visualization only).")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)
    save_players(conn, df)
    save_archetypes(conn, df, km, cluster_to_name, overall_silhouette)
    conn.close()

    print("\n" + "=" * 70)
    print("Done. Results saved to player_clusters / cluster_archetypes.")
    print("=" * 70)


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS player_clusters;")
    cur.execute("DROP TABLE IF EXISTS cluster_archetypes;")
    cur.execute(f"""
        CREATE TABLE player_clusters (
            id SERIAL PRIMARY KEY,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            season INTEGER NOT NULL,
            cluster_id INTEGER NOT NULL,
            archetype TEXT NOT NULL,
            distance_to_centroid DOUBLE PRECISION,
            pca_x DOUBLE PRECISION,
            pca_y DOUBLE PRECISION,
            {', '.join(f'{f} DOUBLE PRECISION' for f in FEATURES)}
        );
    """)
    cur.execute("CREATE INDEX idx_player_clusters_season ON player_clusters(season);")
    cur.execute("CREATE INDEX idx_player_clusters_player ON player_clusters(player_id);")
    cur.execute("""
        CREATE TABLE cluster_archetypes (
            cluster_id INTEGER PRIMARY KEY,
            archetype TEXT NOT NULL,
            n_player_seasons INTEGER,
            silhouette_score DOUBLE PRECISION,
            centroid JSONB,
            representative_players JSONB
        );
    """)
    conn.commit()


def save_players(conn, df):
    cur = conn.cursor()
    cols = ["player_id", "player_name", "team_abbreviation", "season", "cluster_id",
            "archetype", "distance_to_centroid", "pca_x", "pca_y"] + FEATURES
    rows = [tuple(row[c] for c in cols) for _, row in df.iterrows()]
    # numpy int64/float64 -> plain python for psycopg2
    rows = [tuple(v.item() if hasattr(v, "item") else v for v in r) for r in rows]
    psycopg2.extras.execute_values(
        cur,
        f"INSERT INTO player_clusters ({', '.join(cols)}) VALUES %s;",
        rows,
    )
    conn.commit()


def save_archetypes(conn, df, km, cluster_to_name, overall_silhouette):
    cur = conn.cursor()
    for cid in range(N_CLUSTERS):
        sub = df[df["cluster_id"] == cid]
        centroid = {f: float(v) for f, v in zip(FEATURES, km.cluster_centers_[cid])}
        top5 = sub.nsmallest(5, "distance_to_centroid")
        representative = [
            {"player_name": r["player_name"], "season": int(r["season"]), "pts": round(float(r["pts"]), 1)}
            for _, r in top5.iterrows()
        ]
        cur.execute(
            """
            INSERT INTO cluster_archetypes
                (cluster_id, archetype, n_player_seasons, silhouette_score, centroid, representative_players)
            VALUES (%s, %s, %s, %s, %s, %s);
            """,
            (
                cid, cluster_to_name[cid], len(sub), overall_silhouette,
                psycopg2.extras.Json(centroid), psycopg2.extras.Json(representative),
            ),
        )
    conn.commit()


if __name__ == "__main__":
    main()
