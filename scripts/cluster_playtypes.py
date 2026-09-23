"""
cluster_playtypes.py
======================
"Offensive Style" archetypes — the same real, unsupervised K-Means
approach as cluster_players.py (era-normalized within season, K
fixed for a stable presentable count, archetype names matched to
discovered centroids via cosine similarity + Hungarian assignment,
never imposed on the clustering itself), applied to a different real
feature space: each real player-season's real offensive play-type
frequency mix (player_playtypes, scripts/fetch_playtypes.py) instead
of traditional box-score rate stats. This answers a different real
question than the existing stat archetypes: not "what kind of
production does this player put up" but "how does this player's real
offense actually get generated" (real isolations vs. real spot-ups vs.
real pick-and-roll reps vs. ...).

A play type missing for a player-season (no row in player_playtypes)
is treated as real 0 frequency — Synergy only tracks a play type once
a player has real possessions in it, so a real absence there is real
information (they essentially don't run that play), not a data gap.

Usage:
    cd scripts && python3 cluster_playtypes.py

Writes to Postgres: playtype_clusters (one row per player-season) and
playtype_cluster_archetypes (one row per style: centroid, size,
representative players, silhouette score).
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

PLAY_TYPES = [
    "Cut", "Handoff", "Isolation", "Misc", "OffScreen", "Postup",
    "PRBallHandler", "PRRollman", "OffRebound", "Spotup", "Transition",
]

MIN_TOTAL_POSS = 40  # A real season-wide floor across all play types combined.
N_CLUSTERS = 6

# Real, human-readable labels for the play types most likely to define a
# distinct, well-known real offensive style, matched against the ACTUAL
# discovered centroids by cosine similarity (same Hungarian-assignment
# approach as cluster_players.py) — not every real play type needs to be
# a chosen archetype's defining label, same as how not every raw stat
# feature there gets its own named cluster.
STYLE_PROFILES = {
    "Isolation Scorer": {"Isolation": 2.0, "Postup": 0.3},
    "Pick-and-Roll Ball Handler": {"PRBallHandler": 2.0, "Isolation": 0.3},
    "Roll Man / Screener": {"PRRollman": 2.0, "OffRebound": 0.5, "Cut": 0.3},
    "Spot-Up Shooter": {"Spotup": 2.0, "OffScreen": 0.5},
    "Transition Scorer": {"Transition": 2.0, "Cut": 0.3},
    "Post-Up Threat": {"Postup": 2.0, "OffRebound": 0.3},
}


def load_data():
    conn = psycopg2.connect(**DB_CONFIG)
    query = """
        SELECT season, player_id, player_name, team_abbreviation, play_type, freq, poss
        FROM player_playtypes WHERE side = 'offensive';
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    wide = df.pivot_table(
        index=["season", "player_id", "player_name"],
        columns="play_type", values="freq", fill_value=0.0, aggfunc="first",
    ).reset_index()
    for pt in PLAY_TYPES:
        if pt not in wide.columns:
            wide[pt] = 0.0

    # A traded player can have a different max-poss team per play type, which
    # would otherwise split them into two rows if team stayed in the index above.
    total_poss = df.groupby(["season", "player_id"])["poss"].sum().reset_index(name="total_poss")
    team = (
        df.sort_values("poss", ascending=False)
        .drop_duplicates(subset=["season", "player_id"])[["season", "player_id", "team_abbreviation"]]
    )
    wide = wide.merge(total_poss, on=["season", "player_id"], how="left")
    wide = wide.merge(team, on=["season", "player_id"], how="left")
    wide = wide[wide["total_poss"] >= MIN_TOTAL_POSS].reset_index(drop=True)
    return wide


def era_normalize(df, features):
    season_mean = df.groupby("season")[features].transform("mean")
    season_std = df.groupby("season")[features].transform("std").replace(0, 1)
    return ((df[features] - season_mean) / season_std).values


def match_style_names(centroids, features):
    names = list(STYLE_PROFILES.keys())
    profile_matrix = np.array([
        [STYLE_PROFILES[name].get(f, 0) for f in features]
        for name in names
    ], dtype=float)
    profile_norms = profile_matrix / (np.linalg.norm(profile_matrix, axis=1, keepdims=True) + 1e-9)
    centroid_norms = centroids / (np.linalg.norm(centroids, axis=1, keepdims=True) + 1e-9)
    similarity = centroid_norms @ profile_norms.T
    row_idx, col_idx = linear_sum_assignment(-similarity)
    return {r: names[c] for r, c in zip(row_idx, col_idx)}


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS playtype_clusters;")
    cur.execute("DROP TABLE IF EXISTS playtype_cluster_archetypes;")
    cur.execute(f"""
        CREATE TABLE playtype_clusters (
            id SERIAL PRIMARY KEY,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            season INTEGER NOT NULL,
            cluster_id INTEGER NOT NULL,
            style TEXT NOT NULL,
            distance_to_centroid DOUBLE PRECISION,
            pca_x DOUBLE PRECISION,
            pca_y DOUBLE PRECISION,
            {', '.join(f'"{pt}" DOUBLE PRECISION' for pt in PLAY_TYPES)}
        );
    """)
    cur.execute("CREATE INDEX idx_playtype_clusters_season ON playtype_clusters(season);")
    cur.execute("CREATE INDEX idx_playtype_clusters_player ON playtype_clusters(player_id);")
    cur.execute("""
        CREATE TABLE playtype_cluster_archetypes (
            cluster_id INTEGER PRIMARY KEY,
            style TEXT NOT NULL,
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
            "style", "distance_to_centroid", "pca_x", "pca_y"] + PLAY_TYPES
    rows = [tuple(row[c] for c in cols) for _, row in df.iterrows()]
    rows = [tuple(v.item() if hasattr(v, "item") else v for v in r) for r in rows]
    quoted_cols = ", ".join(f'"{c}"' if c in PLAY_TYPES else c for c in cols)
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO playtype_clusters ({quoted_cols}) VALUES %s;", rows,
    )
    conn.commit()


def save_archetypes(conn, df, km, cluster_to_name, overall_silhouette):
    cur = conn.cursor()
    for cid in range(N_CLUSTERS):
        sub = df[df["cluster_id"] == cid]
        centroid = {f: float(v) for f, v in zip(PLAY_TYPES, km.cluster_centers_[cid])}
        top5 = sub.nsmallest(5, "distance_to_centroid")
        representative = [
            {"player_name": r["player_name"], "season": int(r["season"])}
            for _, r in top5.iterrows()
        ]
        cur.execute(
            """INSERT INTO playtype_cluster_archetypes
               (cluster_id, style, n_player_seasons, silhouette_score, centroid, representative_players)
               VALUES (%s, %s, %s, %s, %s, %s);""",
            (cid, cluster_to_name[cid], len(sub), overall_silhouette,
             psycopg2.extras.Json(centroid), psycopg2.extras.Json(representative)),
        )
    conn.commit()


def main():
    print("=" * 70)
    print("Offensive Style Clustering (K-Means on real play-type frequency)")
    print("=" * 70)

    df = load_data()
    print(f"\nCandidate pool: {len(df):,} real player-seasons (>= {MIN_TOTAL_POSS} real total tracked possessions)")

    X = era_normalize(df, PLAY_TYPES)

    print(f"\nFitting K-Means with K={N_CLUSTERS}...")
    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    cluster_ids = km.fit_predict(X)
    df["cluster_id"] = cluster_ids
    overall_silhouette = silhouette_score(X, cluster_ids)
    print(f"Real silhouette score: {overall_silhouette:.3f}")

    cluster_to_name = match_style_names(km.cluster_centers_, PLAY_TYPES)
    df["style"] = df["cluster_id"].map(cluster_to_name)

    print("\nOffensive styles discovered:")
    for cid in range(N_CLUSTERS):
        name = cluster_to_name[cid]
        n = (df["cluster_id"] == cid).sum()
        print(f"  Cluster {cid} -> \"{name}\" ({n:,} real player-seasons)")

    dists = np.linalg.norm(X - km.cluster_centers_[cluster_ids], axis=1)
    df["distance_to_centroid"] = dists

    pca = PCA(n_components=2, random_state=42)
    coords = pca.fit_transform(X)
    df["pca_x"] = coords[:, 0]
    df["pca_y"] = coords[:, 1]

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)
    save_players(conn, df)
    save_archetypes(conn, df, km, cluster_to_name, overall_silhouette)
    conn.close()

    print("\n✅ Done. Results saved to playtype_clusters / playtype_cluster_archetypes.")


if __name__ == "__main__":
    main()
