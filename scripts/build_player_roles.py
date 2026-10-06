"""
build_player_roles.py
======================
Player Roles: a finer set of player archetypes than cluster_players.py's
six (which Trivia depends on, as Pair Synergy did until 2026-10-06, so they stay
as they are and are shown here as each role's "family").

Why finer groups needed different inputs: the six archetypes were clustered
on per-game box stats, which mostly measure how many minutes a player gets
(1,750 player-seasons landed in one "Bench Role Player" blob). Roles use
what a player does with his time on the floor:

  usage, assist, turnover and rebounding rates (usg_pct, ast_pct, tov_pct,
  oreb_pct, reb_pct), steals and blocks per 36 minutes, free-throw rate
  (FTA/FGA), true shooting, and where his shots come from: share of field-
  goal attempts at the rim, in the rest of the paint, from mid-range, from
  the corners and from above the break (player_shots, regular season only,
  zones via shots_lib.classify_zone, the same classifier the shot charts use).

Same pool and era handling as cluster_players.py: 15+ minutes a game, 20+
games, seasons 2010-2026, every feature z-scored within its own season.
K-Means; the number of roles is chosen by the stability check below, not
fixed in advance. Names come from matching each discovered centroid to
hand-written role profiles (cosine similarity + Hungarian assignment, one
name per role); the grouping itself never sees the names.

Usage:
    cd scripts && python3 build_player_roles.py            # fit and save
    cd scripts && python3 build_player_roles.py --explore  # choose K only
"""

import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy.optimize import linear_sum_assignment
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import shots_lib  # noqa: E402

from db_config import DB_CONFIG  # noqa: E402

MIN_MINUTES, MIN_GAMES, MIN_SEASON = 15, 20, 2010
MIN_FGA_FOR_SHOT_PROFILE = 100
# Chosen by the stability check (--explore): the most roles whose grouping
# survives refitting on 80% subsamples (mean adjusted Rand >= 0.8). 11+
# roles fall to 0.52-0.64, i.e. the boundaries stop being reproducible.
N_ROLES = 10
STABILITY_FLOOR = 0.8

# Expected direction per feature for each role name (0 = no opinion). Used
# only to NAME the groups K-Means found, by cosine similarity + Hungarian
# assignment; the clustering never sees these.
ROLE_PROFILES = {
    "Lead Creator": {"usg_pct": 2, "ast_pct": 2, "ft_rate": 1},
    "Combo Guard": {"ast_pct": 1.5, "tov_pct": 1, "ts_pct": -1, "reb_pct": -0.5},
    "Mid-Range Scorer": {"mid_share": 2, "usg_pct": 1, "rim_share": -0.5},
    "Above-the-Break Shooter": {"ab3_share": 2, "rim_share": -1, "reb_pct": -1, "ft_rate": -0.5},
    "Corner Spot-Up Shooter": {"corner3_share": 2, "ab3_share": 1, "usg_pct": -1, "paint_share": -1},
    "Defensive Wing": {"stl36": 2, "usg_pct": -1, "corner3_share": 0.5},
    "Balanced Forward": {"ast_pct": -0.5, "stl36": -0.5, "mid_share": -0.5, "corner3_share": 0.3},
    "Rim-Running Big": {"rim_share": 2.5, "oreb_pct": 2, "ft_rate": 2, "blk36": 2, "ts_pct": 1.5},
    "Interior Finisher": {"oreb_pct": 1.5, "reb_pct": 1.5, "rim_share": 1.5, "paint_share": 1.5, "ab3_share": -1.5},
    "Skilled Big": {"reb_pct": 1, "blk36": 1, "mid_share": 1.5, "ab3_share": -1, "corner3_share": -1},
}
FEATURE_LABELS = {
    "usg_pct": "usage", "ast_pct": "assist rate", "tov_pct": "turnover rate", "ts_pct": "true shooting",
    "oreb_pct": "offensive boards", "reb_pct": "rebounding", "stl36": "steals", "blk36": "blocks",
    "ft_rate": "free-throw rate", "rim_share": "shots at the rim", "paint_share": "paint shots",
    "mid_share": "mid-range shots", "corner3_share": "corner threes", "ab3_share": "above-the-break threes",
}
FEATURES = ["usg_pct", "ast_pct", "tov_pct", "ts_pct", "oreb_pct", "reb_pct", "stl36", "blk36",
            "ft_rate", "rim_share", "paint_share", "mid_share", "corner3_share", "ab3_share"]
DISPLAY = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3_pct", "ts_pct", "usg_pct", "ast_pct", "reb_pct"]
ZONE_COL = {"Restricted Area": "rim", "In The Paint (Non-RA)": "paint", "Mid-Range": "mid",
            "Corner 3": "corner3", "Above the Break 3": "ab3"}


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def load(conn):
    df = pd.read_sql_query(
        f"""SELECT player_id, player_name, team_abbreviation, season, gp, min, poss,
                   pts, reb, ast, stl, blk, tov, fg3_pct, ts_pct, usg_pct, ast_pct, reb_pct,
                   oreb_pct, tov_pct, fga, fta
            FROM player_season_stats
            WHERE min >= {MIN_MINUTES} AND gp >= {MIN_GAMES} AND season >= {MIN_SEASON}""",
        conn,
    )
    df["stl36"] = df["stl"] / df["min"] * 36
    df["blk36"] = df["blk"] / df["min"] * 36
    df["ft_rate"] = df["fta"] / df["fga"].replace(0, np.nan)

    cur = conn.cursor()
    shots = {}
    for season in sorted(df.season.unique()):
        cur.execute(
            """SELECT player_id, loc_x, loc_y, shot_distance, shot_type, shot_zone_basic
               FROM player_shots WHERE season = %s AND game_id LIKE '002%%'""",
            (season_label(int(season)),),
        )
        for pid, x, y, dist, stype, zbasic in cur.fetchall():
            zone = ZONE_COL.get(shots_lib.classify_zone(x, y, dist, stype, zbasic))
            if zone:
                key = (pid, int(season))
                counts = shots.setdefault(key, {z: 0 for z in ZONE_COL.values()})
                counts[zone] += 1
        print(f"  shots loaded for {season_label(int(season))}", flush=True)
    zone_rows = []
    for (pid, season), counts in shots.items():
        total = sum(counts.values())
        zone_rows.append({"player_id": pid, "season": season, "shot_fga": total,
                          **{f"{z}_share": counts[z] / total if total else np.nan for z in counts}})
    zones = pd.DataFrame(zone_rows)
    df = df.merge(zones, on=["player_id", "season"], how="left")
    before = len(df)
    df = df[df.shot_fga >= MIN_FGA_FOR_SHOT_PROFILE].dropna(subset=FEATURES).reset_index(drop=True)
    print(f"Pool: {len(df):,} player-seasons ({before - len(df)} dropped for <{MIN_FGA_FOR_SHOT_PROFILE} "
          f"located shots or a missing stat)")
    return df


def era_normalize(df):
    mean = df.groupby("season")[FEATURES].transform("mean")
    std = df.groupby("season")[FEATURES].transform("std").replace(0, 1)
    return ((df[FEATURES] - mean) / std).values


def explore(X, k_range=range(6, 17), n_boot=8):
    """Silhouette (separation) and stability: refit on 80% subsamples and
    compare labels on the shared points with the full fit (adjusted Rand)."""
    rng = np.random.default_rng(0)
    print(" K  silhouette  stability(ARI)")
    out = {}
    for k in k_range:
        full = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X)
        sil = silhouette_score(X, full.labels_, sample_size=4000, random_state=0)
        aris = []
        for _ in range(n_boot):
            idx = rng.choice(len(X), int(0.8 * len(X)), replace=False)
            sub = KMeans(n_clusters=k, random_state=int(rng.integers(1e6)), n_init=10).fit(X[idx])
            aris.append(adjusted_rand_score(full.labels_[idx], sub.labels_))
        out[k] = (sil, float(np.mean(aris)))
        print(f"{k:2d}  {sil:.3f}       {np.mean(aris):.3f}")
    return out


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    df = load(conn)
    X = era_normalize(df)
    if "--explore" in sys.argv:
        explore(X)
        return
    if "--describe" not in sys.argv:
        fit_and_save(conn, df, X)
        return
    if "--describe" in sys.argv:
        k = int(sys.argv[sys.argv.index("--describe") + 1])
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X)
        df["cid"] = km.labels_
        df["dist"] = np.linalg.norm(X - km.cluster_centers_[km.labels_], axis=1)
        for c in range(k):
            cen = km.cluster_centers_[c]
            order = np.argsort(-np.abs(cen))[:6]
            desc = ", ".join(f"{FEATURES[i]} {cen[i]:+.1f}" for i in order)
            sub = df[df.cid == c]
            stars = sub.sort_values("pts", ascending=False).head(6)
            reps = sub.nsmallest(4, "dist")
            print(f"\n[{c}] n={len(sub)}  {desc}")
            print("   top scorers:", "; ".join(f"{r.player_name} {r.season}" for r in stars.itertuples()))
            print("   most typical:", "; ".join(f"{r.player_name} {r.season}" for r in reps.itertuples()))
        return


def name_roles(centroids):
    names = list(ROLE_PROFILES)
    prof = np.array([[ROLE_PROFILES[n].get(f, 0) for f in FEATURES] for n in names], dtype=float)
    prof /= np.linalg.norm(prof, axis=1, keepdims=True)
    cen = centroids / np.linalg.norm(centroids, axis=1, keepdims=True)
    sim = cen @ prof.T
    rows, cols = linear_sum_assignment(-sim)
    return {r: names[c] for r, c in zip(rows, cols)}, {r: float(sim[r, c]) for r, c in zip(rows, cols)}


def describe(centroid):
    """Plain-English summary from the centroid's three biggest deviations."""
    order = np.argsort(-np.abs(centroid))[:3]
    return ", ".join(f"{'more' if centroid[i] > 0 else 'fewer' if FEATURES[i].endswith('share') else 'lower'} "
                     f"{FEATURE_LABELS[FEATURES[i]]}" if centroid[i] < 0 or FEATURES[i].endswith("share")
                     else f"high {FEATURE_LABELS[FEATURES[i]]}" for i in order)


def fit_and_save(conn, df, X):
    stability = explore(X, k_range=[N_ROLES])[N_ROLES][1]
    assert stability >= STABILITY_FLOOR, f"stability {stability:.2f} below {STABILITY_FLOOR}"
    km = KMeans(n_clusters=N_ROLES, random_state=42, n_init=10).fit(X)
    df["role_id"] = km.labels_
    sil = float(silhouette_score(X, km.labels_))
    names, fit = name_roles(km.cluster_centers_)
    df["role"] = df.role_id.map(names)
    df["distance_to_centroid"] = np.linalg.norm(X - km.cluster_centers_[km.labels_], axis=1)
    pca = PCA(n_components=2, random_state=42)
    coords = pca.fit_transform(X)
    df["pca_x"], df["pca_y"] = coords[:, 0], coords[:, 1]
    print(f"K={N_ROLES}: silhouette {sil:.3f}, stability {stability:.3f}, "
          f"PCA shows {pca.explained_variance_ratio_.sum():.0%} of variance")

    cur = conn.cursor()
    cur.execute("SELECT player_id, season, archetype FROM player_clusters;")
    family = {(p, s): a for p, s, a in cur.fetchall()}
    df["family"] = [family.get((p, s)) for p, s in zip(df.player_id, df.season)]

    cur.execute("DROP TABLE IF EXISTS player_roles, role_archetypes;")
    cur.execute(f"""
        CREATE TABLE player_roles (
            player_id INTEGER NOT NULL, player_name TEXT NOT NULL, team_abbreviation TEXT,
            season INTEGER NOT NULL, role_id INTEGER NOT NULL, role TEXT NOT NULL, family TEXT,
            distance_to_centroid REAL, pca_x REAL, pca_y REAL,
            {", ".join(f"{c} REAL" for c in DISPLAY)},
            {", ".join(f"f_{c} REAL" for c in FEATURES)},
            PRIMARY KEY (player_id, season)
        );
        CREATE TABLE role_archetypes (
            role_id INTEGER PRIMARY KEY, role TEXT NOT NULL, description TEXT,
            n_player_seasons INTEGER, silhouette_score REAL, stability_ari REAL, name_fit REAL,
            centroid JSONB, representative_players JSONB, top_scorers JSONB
        );
    """)
    rows = []
    for r in df.itertuples(index=False):
        rows.append((int(r.player_id), r.player_name, r.team_abbreviation, int(r.season), int(r.role_id), r.role,
                     r.family, float(r.distance_to_centroid), float(r.pca_x), float(r.pca_y),
                     *[None if pd.isna(getattr(r, c)) else float(getattr(r, c)) for c in DISPLAY],
                     *[float(getattr(r, c)) for c in FEATURES]))
    psycopg2.extras.execute_values(cur, "INSERT INTO player_roles VALUES %s;", rows)
    for rid in range(N_ROLES):
        sub = df[df.role_id == rid]
        rep = sub.nsmallest(5, "distance_to_centroid")
        # Five different players: each one's highest-scoring season in this role.
        top = sub.sort_values("pts", ascending=False).drop_duplicates("player_id").head(5)
        pack = lambda t: [{"player_id": int(x.player_id), "player_name": x.player_name, "season": int(x.season),
                           "pts": round(float(x.pts), 1)} for x in t.itertuples()]
        cur.execute(
            "INSERT INTO role_archetypes VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);",
            (rid, names[rid], describe(km.cluster_centers_[rid]), len(sub), sil, stability, fit[rid],
             psycopg2.extras.Json({f: round(float(v), 3) for f, v in zip(FEATURES, km.cluster_centers_[rid])}),
             psycopg2.extras.Json(pack(rep)), psycopg2.extras.Json(pack(top))),
        )
        print(f"  {names[rid]:24s} n={len(sub):4d} fit={fit[rid]:.2f}  {describe(km.cluster_centers_[rid])}"
              f" | {', '.join(f'{x.player_name} {x.season}' for x in top.head(3).itertuples())}")
    conn.commit()


if __name__ == "__main__":
    main()
