"""
similarity_api.py
==================
FastAPI backend for NBA Player Similarity Engine.

Endpoints:
    GET /similarity/season/{player_name}/{season}  — Top 10 similar seasons
    GET /similarity/season-profile/{player_name}/{season} — Same method, computed
        live, with filters and each season's stat profile
    GET /similarity/career/{player_name}           — Top 10 similar careers

Usage:
    uvicorn similarity_api:app --reload
"""

from contextlib import contextmanager
from functools import lru_cache
import unicodedata

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg2 import pool

from source_badge import make_source

# ─── App Setup ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="NBA Similarity API",
    description="Find similar NBA player seasons and careers using cosine similarity.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database Connection Pool ───────────────────────────────────────────────

from db_config import DB_CONFIG

DB_POOL = pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)


@contextmanager
def get_db():
    """Get a database connection from the pool (auto-returns on exit)."""
    conn = DB_POOL.getconn()
    try:
        yield conn
    finally:
        DB_POOL.putconn(conn)


# ─── Helpers ────────────────────────────────────────────────────────────────

def find_player_id(cursor, player_name: str) -> int:
    """
    Look up a player_id by name (case-insensitive partial match).
    Raises 404 if not found.
    """
    # Try exact match first
    cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_season_stats "
        "WHERE LOWER(player_name) = LOWER(%s) LIMIT 1;",
        (player_name,),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Try partial match
    cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_season_stats "
        "WHERE LOWER(player_name) LIKE LOWER(%s) LIMIT 1;",
        (f"%{player_name}%",),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Accent-insensitive fallback (handles cases like jokic -> Jokić)
    normalized_query = normalize_text(player_name)
    cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_season_stats;"
    )
    candidates = cursor.fetchall()
    for pid, pname in candidates:
        if normalized_query == normalize_text(pname):
            return pid, pname
    for pid, pname in candidates:
        if normalized_query in normalize_text(pname):
            return pid, pname

    raise HTTPException(
        status_code=404,
        detail=f"Player '{player_name}' not found in database.",
    )


def normalize_text(text: str) -> str:
    """
    Lowercase and strip accents/diacritics for loose matching.
    """
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.lower().strip()


# ─── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    """Health check and API info."""
    return {
        "service": "NBA Similarity API",
        "version": "1.0.0",
        "endpoints": [
            "/similarity/season/{player_name}/{season}",
            "/similarity/career/{player_name}",
            "/clusters/archetypes",
            "/clusters/season/{season}",
            "/clusters/player/{player_name}",
        ],
    }


# ─── Player Archetypes (roles) ──────────────────────────────────────────────
# Reads scripts/build_player_roles.py's 10 roles (K-Means on rate stats and
# shot locations; see that script). The six broader archetypes from
# scripts/cluster_players.py stay in player_clusters for Pair Synergy and
# Trivia, and come back here as each player's "family".

@app.get("/clusters/archetypes")
def get_archetypes():
    """All discovered player roles: size, centroid (per-season z-scores),
    description, most typical players and top scorers."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT role_id, role, n_player_seasons, silhouette_score, centroid,
                   representative_players, description, stability_ari, top_scorers
            FROM role_archetypes
            ORDER BY n_player_seasons DESC;
            """
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No player roles found. Run scripts/build_player_roles.py first.",
        )

    return {
        "archetypes": [
            {
                "cluster_id": r[0],
                "archetype": r[1],
                "n_player_seasons": r[2],
                "silhouette_score": r[3],
                "centroid": r[4],
                "representative_players": r[5],
                "description": r[6],
                "stability_ari": r[7],
                "top_scorers": r[8],
            }
            for r in rows
        ],
        "_source": make_source(["role_archetypes", "player_roles"], "nba_api (stats.nba.com): season stats and shot locations"),
    }


@app.get("/clusters/season/{season}")
def get_season_clusters(season: int):
    """
    Every clustered player-season for one season: archetype + 2D PCA
    coordinates (for a scatter plot) + the underlying style stats.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT player_id, player_name, team_abbreviation, role,
                   pca_x, pca_y, pts, reb, ast, stl, blk, tov,
                   fg3_pct, ts_pct, usg_pct, ast_pct, reb_pct, family
            FROM player_roles
            WHERE season = %s
            ORDER BY player_name ASC;
            """,
            (season,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No cluster results for season {season}. "
                   f"Run scripts/build_player_roles.py first, or check the season has data.",
        )

    cols = ["player_id", "player_name", "team_abbreviation", "archetype", "pca_x", "pca_y",
            "pts", "reb", "ast", "stl", "blk", "tov", "fg3_pct", "ts_pct", "usg_pct", "ast_pct", "reb_pct",
            "family"]
    return {
        "season": season, "players": [dict(zip(cols, row)) for row in rows],
        "_source": make_source(["player_roles"], "nba_api (stats.nba.com): season stats and shot locations"),
    }


@app.get("/clusters/player/{player_name}")
def get_player_cluster_history(player_name: str):
    """A player's archetype across every season they were clustered in — shows role evolution."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player_id(cursor, player_name)

        cursor.execute(
            """
            SELECT season, role, pts, reb, ast, usg_pct, family
            FROM player_roles
            WHERE player_id = %s
            ORDER BY season ASC;
            """,
            (player_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No role for this player (may not meet the 15+ minutes / 20+ games filter "
                   f"in any season since 2009-10). Run scripts/build_player_roles.py first.",
        )

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "seasons": [
            {"season": r[0], "archetype": r[1], "pts": r[2], "reb": r[3], "ast": r[4], "usg_pct": r[5],
             "family": r[6]}
            for r in rows
        ],
    }


# ─── Offensive Style Clusters ────────────────────────────────────────────────
#
# Reads results written by scripts/cluster_playtypes.py — the same real
# K-Means approach as the stat archetypes above, applied to real play-type
# frequency mix instead of box-score rate stats. Answers "how does this
# player's offense actually get generated" rather than "what does their
# production look like."

@app.get("/clusters/playtype-archetypes")
def get_playtype_archetypes():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT cluster_id, style, n_player_seasons, silhouette_score, centroid, representative_players
               FROM playtype_cluster_archetypes ORDER BY n_player_seasons DESC;"""
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No offensive-style cluster results found. Run scripts/cluster_playtypes.py first.")

    return {
        "styles": [
            {"cluster_id": r[0], "style": r[1], "n_player_seasons": r[2], "silhouette_score": r[3],
             "centroid": r[4], "representative_players": r[5]}
            for r in rows
        ],
        "_source": make_source(["playtype_cluster_archetypes"], "nba_api (Synergy play-type tracking)"),
    }


@app.get("/clusters/playtype-season/{season}")
def get_playtype_season_clusters(season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT player_id, player_name, team_abbreviation, style, pca_x, pca_y
               FROM playtype_clusters WHERE season = %s ORDER BY player_name ASC;""",
            (season,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No offensive-style cluster results for season {season}. Run scripts/cluster_playtypes.py first.")

    cols = ["player_id", "player_name", "team_abbreviation", "style", "pca_x", "pca_y"]
    return {
        "season": season, "players": [dict(zip(cols, row)) for row in rows],
        "_source": make_source(["playtype_clusters"], "nba_api (Synergy play-type tracking)"),
    }


# ─── League Evolution ────────────────────────────────────────────────────────
#
# How the real league has changed: each real statistical archetype's real
# share of the qualified-player pool per season (player_roles, already
# populated for every season 2009-10–present), plus three real league-average
# trends per season computed straight from player_season_stats, each a
# minutes-weighted average across every real player that season (so a
# 10-minute bench role doesn't count as much as a 35-minute starter) —
# nothing here is modeled or projected, just real historical aggregation.

@app.get("/clusters/evolution")
def get_league_evolution():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT season, role, COUNT(*) AS n
               FROM player_roles
               GROUP BY season, role
               ORDER BY season, role;"""
        )
        archetype_rows = cursor.fetchall()

        if not archetype_rows:
            raise HTTPException(
                status_code=404,
                detail="No player roles found. Run scripts/build_player_roles.py first.",
            )

        # Scoped to exactly the seasons player_roles actually covers, so
        # these league-average trends never silently outrun the archetype
        # data displayed right alongside them (player_season_stats now goes
        # back to 1950 via the Kaggle historical import, well before
        # clustering was ever run).
        seasons = sorted({r[0] for r in archetype_rows})
        cursor.execute(
            """SELECT season,
                      SUM((fg3a::float / NULLIF(fga, 0)) * min * gp) / NULLIF(SUM(min * gp), 0) AS three_pt_rate,
                      SUM(ts_pct * min * gp) / NULLIF(SUM(min * gp), 0) AS ts_pct,
                      SUM(poss) / NULLIF(SUM(min * gp), 0) * 48 AS pace_proxy
               FROM player_season_stats
               WHERE gp > 0 AND min > 0 AND season = ANY(%s)
               GROUP BY season
               ORDER BY season;""",
            (seasons,),
        )
        trend_rows = cursor.fetchall()

    archetypes = sorted({r[1] for r in archetype_rows})
    season_totals = {}
    for season, archetype, n in archetype_rows:
        season_totals[season] = season_totals.get(season, 0) + n

    archetype_shares = {a: [] for a in archetypes}
    counts_by_season = {s: {} for s in seasons}
    for season, archetype, n in archetype_rows:
        counts_by_season[season][archetype] = n
    for season in seasons:
        total = season_totals[season]
        for archetype in archetypes:
            n = counts_by_season[season].get(archetype, 0)
            archetype_shares[archetype].append({
                "season": season,
                "n": n,
                "share": round(n / total, 4) if total else None,
            })

    trends = [
        {
            "season": r[0],
            "three_pt_rate": round(r[1], 4) if r[1] is not None else None,
            "ts_pct": round(r[2], 4) if r[2] is not None else None,
            "pace_proxy": round(r[3], 2) if r[3] is not None else None,
        }
        for r in trend_rows
    ]

    return {
        "seasons": seasons,
        "archetypes": archetypes,
        "archetype_shares": archetype_shares,
        "trends": trends,
        "methodology": (
            "Role share = count of qualified player-seasons in that role (K-Means clustering, "
            "scripts/build_player_roles.py) divided by the real total qualified pool that season. three_pt_rate is "
            "a minutes-weighted league average of each real player's own 3PA/FGA that season (the share of shot "
            "attempts taken from three, not raw makes or attempts per game, which pace changes would confound). "
            "ts_pct is a minutes-weighted league average of real True Shooting %. pace_proxy is SUM(real season "
            "possessions) / SUM(real season minutes) * 48 — a real, minutes-weighted approximation of league pace "
            "built from player-level possession/minutes data (not the official team-level NBA pace stat, which "
            "this project doesn't have a historical source for), disclosed as an approximation rather than "
            "presented as the official number."
        ),
        "_source": make_source(["player_roles", "player_season_stats"], "nba_api (stats.nba.com)"),
    }


@app.get("/similarity/season/{player_name}/{season}")
def get_season_similarity(player_name: str, season: int, top_n: int = 10):
    """
    Find the top N most similar player-seasons for a given player + season.
    Uses precomputed league-adjusted cosine similarity from season_similarity table.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Resolve player
        player_id, resolved_name = find_player_id(cursor, player_name)

        # Check the season exists for this player
        cursor.execute(
            "SELECT 1 FROM player_season_stats WHERE player_id = %s AND season = %s;",
            (player_id, season),
        )
        if not cursor.fetchone():
            raise HTTPException(
                status_code=404,
                detail=f"No data for {resolved_name} in season {season}.",
            )

        # Query precomputed similarities
        cursor.execute(
            """
            SELECT
                s.similar_player_id,
                p.player_name,
                s.similar_season,
                s.similarity_score
            FROM season_similarity s
            JOIN player_season_stats p
                ON s.similar_player_id = p.player_id
                AND s.similar_season = p.season
            WHERE s.source_player_id = %s
              AND s.source_season = %s
            ORDER BY s.similarity_score DESC
            LIMIT %s;
            """,
            (player_id, season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "query": {
            "player_name": resolved_name,
            "player_id": player_id,
            "season": season,
        },
        "results": [
            {
                "player_id": row[0],
                "player_name": row[1],
                "season": row[2],
                "similarity_score": round(row[3], 4),
            }
            for row in rows
        ],
        "_source": make_source(["season_similarity", "player_season_stats"], "nba_api (stats.nba.com)"),
    }


# ─── Season similarity, computed live (same method as season_similarity) ─────
# precompute_league_similarity.py stores only each season's top 10, so a
# player's own other seasons crowd out everyone else and a 3-game cameo can
# be a match. Recomputing is cheap (about 7,300 seasons x 8 numbers) and was
# checked to reproduce every stored score and top-10 set exactly.

SIM_ADJUST = ["pts", "ts_pct", "usg_pct", "net_rating", "ast_pct", "reb_pct"]
SIM_FEATURES = SIM_ADJUST + ["age", "min"]
SIM_LABELS = {
    "pts": "Points", "ts_pct": "True shooting", "usg_pct": "Usage", "net_rating": "Net rating",
    "ast_pct": "Assist %", "reb_pct": "Rebound %", "age": "Age", "min": "Minutes",
}


@lru_cache(maxsize=1)
def _season_matrix():
    """Every player-season with all eight inputs, z-scored the way the
    precompute script does it: each stat within its own season, then every
    feature standardised over the whole pool, then unit-normalised."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT player_id, player_name, season, team_abbreviation, gp, {", ".join(SIM_FEATURES)}
            FROM player_season_stats
            WHERE {" AND ".join(f"{c} IS NOT NULL" for c in SIM_FEATURES)}
            ORDER BY player_id, season;
        """)
        rows = cur.fetchall()
    meta = [r[:5] for r in rows]
    raw = np.array([r[5:] for r in rows], dtype=float)
    seasons = np.array([m[2] for m in meta])
    z = raw[:, :len(SIM_ADJUST)].copy()
    for s in np.unique(seasons):
        mask = seasons == s
        block = z[mask]
        std = block.std(axis=0, ddof=1)  # pandas' default, as in the precompute script
        std[std == 0] = 1.0
        z[mask] = (block - block.mean(axis=0)) / std
    feats = np.column_stack([z, raw[:, len(SIM_ADJUST):]])
    scaled = (feats - feats.mean(axis=0)) / feats.std(axis=0)
    unit = scaled / np.linalg.norm(scaled, axis=1, keepdims=True)
    index = {(m[0], m[2]): i for i, m in enumerate(meta)}
    return meta, raw, z, scaled, unit, index


def _season_row(i, meta, raw, z):
    pid, name, season, team, gp = meta[i]
    return {
        "player_id": pid,
        "player_name": name,
        "season": season,
        "team": team,
        "gp": gp,
        "stats": {f: round(float(raw[i, k]), 4) for k, f in enumerate(SIM_FEATURES)},
        "season_z": {f: round(float(z[i, k]), 2) for k, f in enumerate(SIM_ADJUST)},
    }


@app.get("/similarity/season-profile/{player_name}/{season}")
def get_season_similarity_profile(player_name: str, season: int, top_n: int = 10,
                                  exclude_self: bool = False, min_gp: int = 0,
                                  one_per_player: bool = False):
    """
    Most similar player-seasons, with filters: exclude_self drops the player's
    own other seasons, min_gp drops matches with fewer games, one_per_player
    keeps only each player's closest season. Each match lists the inputs it's
    closest on and the one it differs most on.
    """
    top_n = max(1, min(top_n, 25))
    min_gp = max(0, min_gp)
    with get_db() as conn:
        player_id, resolved_name = find_player_id(conn.cursor(), player_name)

    meta, raw, z, scaled, unit, index = _season_matrix()
    i = index.get((player_id, season))
    if i is None:
        seasons = sorted(m[2] for m in meta if m[0] == player_id)
        label = lambda y: f"{y - 1}-{str(y)[-2:]}"  # noqa: E731
        detail = (f"No comparable season for {resolved_name} in {label(season)}. "
                  + (f"Seasons available: {label(seasons[0])} to {label(seasons[-1])}." if seasons
                     else "Similarity needs usage, net rating, assist % and rebound %, "
                          "which this database has from 2009-10 on."))
        raise HTTPException(status_code=404, detail=detail)

    sims = unit @ unit[i]
    keep = np.ones(len(meta), dtype=bool)
    keep[i] = False
    if exclude_self:
        keep &= np.array([m[0] != player_id for m in meta])
    if min_gp:
        keep &= np.array([(m[4] or 0) >= min_gp for m in meta])
    candidates = np.flatnonzero(keep)
    order = candidates[np.argsort(-sims[candidates], kind="stable")]
    if one_per_player:
        seen = set()
        order = [j for j in order if not (meta[j][0] in seen or seen.add(meta[j][0]))]
    order = order[:top_n]

    results = []
    for rank, j in enumerate(order, start=1):
        gap = np.abs(scaled[j] - scaled[i])
        # "Closest on" names playing-style stats only; age and minutes still
        # count towards the score and can be what differs most.
        closest = [SIM_ADJUST[k] for k in np.argsort(gap[:len(SIM_ADJUST)], kind="stable")[:2]]
        k = int(np.argmax(gap))
        results.append({
            "rank": rank,
            **_season_row(j, meta, raw, z),
            "similarity_score": round(float(sims[j]), 4),
            "closest_on": closest,
            "differs_most": {
                "feature": SIM_FEATURES[k],
                "direction": "higher" if raw[j, k] > raw[i, k] else "lower",
            },
        })

    seasons_all = [m[2] for m in meta]
    return {
        "query": _season_row(i, meta, raw, z),
        "filters": {"top_n": top_n, "exclude_self": exclude_self, "min_gp": min_gp,
                    "one_per_player": one_per_player},
        "pool": {"seasons": len(meta), "from": min(seasons_all), "to": max(seasons_all)},
        "results": results,
        "features": [{"key": f, "label": SIM_LABELS[f]} for f in SIM_FEATURES],
        "methodology": (
            "Each season is described by eight numbers: points, true shooting, usage, net rating, assist % and "
            "rebound % (each z-scored within its own season, so eras compare fairly), plus age and minutes per "
            "game. Every number is then standardised across all seasons and two seasons are compared by the "
            "cosine of their vectors (1 = identical shape). Same method and numbers as the stored "
            "season_similarity table; this view recomputes it so filters work. Covers 2009-10 on, where "
            "usage and net rating exist."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }


@app.get("/players/trajectory/{player_name}")
def get_player_trajectory(player_name: str, season: int, top_n_comps: int = 5, project_years: int = 3):
    """
    CARMELO-style career trajectory: finds this player's closest real
    statistical comps at this same age (the same era-normalized
    season_similarity table /similarity/season uses, filtered to exclude
    the player's own other seasons), then shows what those REAL comps
    actually did at age+1, age+2, age+3 in their own real careers.
    Nothing here is invented — every projected point is a real
    similarity-weighted average of real historical outcomes, and the
    comps themselves are always returned so the projection can be
    audited rather than trusted blindly. If the target player already
    has real data for a projected age (an older/established player), that
    real outcome is returned alongside the projection as a sanity check.
    """
    project_years = max(1, min(project_years, 5))
    top_n_comps = max(1, min(top_n_comps, 15))

    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player_id(cursor, player_name)

        cursor.execute(
            "SELECT age FROM player_season_stats WHERE player_id = %s AND season = %s;",
            (player_id, season),
        )
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise HTTPException(status_code=404, detail=f"No data for {resolved_name} in season {season}.")
        current_age = row[0]

        cursor.execute(
            "SELECT season, age, pts FROM player_season_stats WHERE player_id = %s ORDER BY season;",
            (player_id,),
        )
        career = [{"season": r[0], "age": r[1], "pts": r[2]} for r in cursor.fetchall()]

        cursor.execute(
            """
            SELECT s.similar_player_id, p.player_name, s.similar_season, s.similarity_score, p.age
            FROM season_similarity s
            JOIN player_season_stats p
                ON s.similar_player_id = p.player_id AND s.similar_season = p.season
            WHERE s.source_player_id = %s AND s.source_season = %s
              AND s.similar_player_id != %s
            ORDER BY s.similarity_score DESC
            LIMIT %s;
            """,
            (player_id, season, player_id, top_n_comps),
        )
        comps = [
            {"player_id": r[0], "player_name": r[1], "season": r[2], "similarity": round(r[3], 4), "age": r[4]}
            for r in cursor.fetchall()
        ]

        if not comps:
            raise HTTPException(
                status_code=404,
                detail=f"No comparable player-seasons found for {resolved_name} in {season}.",
            )

        comp_ids = list({c["player_id"] for c in comps})
        cursor.execute(
            "SELECT player_id, age, season, pts FROM player_season_stats WHERE player_id = ANY(%s);",
            (comp_ids,),
        )
        comp_stats_by_id = {}
        for pid, age, comp_season, pts in cursor.fetchall():
            if age is None or pts is None:
                continue
            comp_stats_by_id.setdefault(pid, {})[age] = {"season": comp_season, "pts": pts}

    own_by_age = {c["age"]: c for c in career if c["age"] is not None}

    projection = []
    for offset in range(1, project_years + 1):
        target_age = current_age + offset
        contributing = []
        for c in comps:
            future = comp_stats_by_id.get(c["player_id"], {}).get(c["age"] + offset)
            if future:
                contributing.append({
                    "player_name": c["player_name"],
                    "similarity": c["similarity"],
                    "pts": future["pts"],
                })

        if contributing:
            total_weight = sum(x["similarity"] for x in contributing)
            weighted_pts = sum(x["pts"] * x["similarity"] for x in contributing) / total_weight
            ceiling_pts = max(x["pts"] for x in contributing)
            floor_pts = min(x["pts"] for x in contributing)
        else:
            weighted_pts = ceiling_pts = floor_pts = None

        actual = own_by_age.get(target_age)
        projection.append({
            "age": target_age,
            "projected_pts": round(weighted_pts, 1) if weighted_pts is not None else None,
            "ceiling_pts": round(ceiling_pts, 1) if ceiling_pts is not None else None,
            "floor_pts": round(floor_pts, 1) if floor_pts is not None else None,
            "n_comps_with_data": len(contributing),
            "contributing_comps": contributing,
            "actual_pts": round(actual["pts"], 1) if actual and actual["pts"] is not None else None,
            "actual_season": actual["season"] if actual else None,
        })

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "current_age": current_age,
        "career": career,
        "comps": comps,
        "projection": projection,
        "_source": make_source(["season_similarity", "player_season_stats"], "nba_api (stats.nba.com)"),
    }


@app.get("/similarity/career/{player_name}")
def get_career_similarity(player_name: str, top_n: int = 10):
    """
    Find the top N most similar careers for a given player.
    Uses precomputed cosine similarity from career_similarity table.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Resolve player
        player_id, resolved_name = find_player_id(cursor, player_name)

        # Query precomputed career similarities
        cursor.execute(
            """
            SELECT
                c.similar_player_id,
                p.player_name,
                c.similarity_score
            FROM career_similarity c
            JOIN (
                SELECT DISTINCT player_id, player_name
                FROM player_season_stats
            ) p ON c.similar_player_id = p.player_id
            WHERE c.source_player_id = %s
            ORDER BY c.similarity_score DESC
            LIMIT %s;
            """,
            (player_id, top_n),
        )
        rows = cursor.fetchall()

    return {
        "query": {
            "player_name": resolved_name,
            "player_id": player_id,
        },
        "results": [
            {
                "player_id": row[0],
                "player_name": row[1],
                "similarity_score": round(row[2], 4),
            }
            for row in rows
        ],
    }


@app.get("/players/search")
def search_players(q: str, limit: int = 8):
    """
    Lightweight player-name autocomplete endpoint.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"query": query, "results": []}

    safe_limit = max(1, min(limit, 25))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT player_name
            FROM player_season_stats
            WHERE LOWER(player_name) LIKE LOWER(%s)
            ORDER BY player_name ASC
            LIMIT %s;
            """,
            (f"%{query}%", safe_limit),
        )
        rows = cursor.fetchall()
        if not rows:
            cursor.execute("SELECT DISTINCT player_name FROM player_season_stats;")
            all_rows = cursor.fetchall()
            normalized_query = normalize_text(query)
            filtered = [
                row[0] for row in all_rows
                if normalized_query in normalize_text(row[0])
            ]
            return {
                "query": query,
                "results": filtered[:safe_limit],
            }

    return {
        "query": query,
        "results": [row[0] for row in rows],
    }


# ─── Main Guard ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("similarity_api:app", host="0.0.0.0", port=8001, reload=True)
