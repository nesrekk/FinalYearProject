"""
similarity_api.py
==================
FastAPI backend for NBA Player Similarity Engine.

Endpoints:
    GET /similarity/season/{player_name}/{season}  — Top 10 similar seasons
    GET /similarity/career/{player_name}           — Top 10 similar careers

Usage:
    uvicorn similarity_api:app --reload
"""

from contextlib import contextmanager
import unicodedata
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg2 import pool

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


# ─── Player Archetype Clusters ──────────────────────────────────────────────
# Reads results written by scripts/cluster_players.py (K-Means on style
# stats — see that script for methodology and why the labels are trustworthy
# despite being hand-named).

@app.get("/clusters/archetypes")
def get_archetypes():
    """All 6 discovered archetypes: size, centroid stats, representative players."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT cluster_id, archetype, n_player_seasons, silhouette_score,
                   centroid, representative_players
            FROM cluster_archetypes
            ORDER BY n_player_seasons DESC;
            """
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No cluster results found. Run scripts/cluster_players.py first.",
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
            }
            for r in rows
        ]
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
            SELECT player_id, player_name, team_abbreviation, archetype,
                   pca_x, pca_y, pts, reb, ast, stl, blk, tov,
                   fg3_pct, ts_pct, usg_pct, ast_pct, reb_pct
            FROM player_clusters
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
                   f"Run scripts/cluster_players.py first, or check the season has data.",
        )

    cols = ["player_id", "player_name", "team_abbreviation", "archetype", "pca_x", "pca_y",
            "pts", "reb", "ast", "stl", "blk", "tov", "fg3_pct", "ts_pct", "usg_pct", "ast_pct", "reb_pct"]
    return {"season": season, "players": [dict(zip(cols, row)) for row in rows]}


@app.get("/clusters/player/{player_name}")
def get_player_cluster_history(player_name: str):
    """A player's archetype across every season they were clustered in — shows role evolution."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player_id(cursor, player_name)

        cursor.execute(
            """
            SELECT season, archetype, pts, reb, ast, usg_pct
            FROM player_clusters
            WHERE player_id = %s
            ORDER BY season ASC;
            """,
            (player_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No cluster results for this player (may not meet the min>=15mpg / gp>=20 filter "
                   f"in any season). Run scripts/cluster_players.py first.",
        )

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "seasons": [
            {"season": r[0], "archetype": r[1], "pts": r[2], "reb": r[3], "ast": r[4], "usg_pct": r[5]}
            for r in rows
        ],
    }


# ─── League Evolution ────────────────────────────────────────────────────────
#
# How the real league has changed: each real statistical archetype's real
# share of the qualified-player pool per season (player_clusters, already
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
            """SELECT season, archetype, COUNT(*) AS n
               FROM player_clusters
               GROUP BY season, archetype
               ORDER BY season, archetype;"""
        )
        archetype_rows = cursor.fetchall()

        cursor.execute(
            """SELECT season,
                      SUM((fg3a::float / NULLIF(fga, 0)) * min * gp) / NULLIF(SUM(min * gp), 0) AS three_pt_rate,
                      SUM(ts_pct * min * gp) / NULLIF(SUM(min * gp), 0) AS ts_pct,
                      SUM(poss) / NULLIF(SUM(min * gp), 0) * 48 AS pace_proxy
               FROM player_season_stats
               WHERE gp > 0 AND min > 0
               GROUP BY season
               ORDER BY season;"""
        )
        trend_rows = cursor.fetchall()

    if not archetype_rows:
        raise HTTPException(
            status_code=404,
            detail="No cluster results found. Run scripts/cluster_players.py first.",
        )

    seasons = sorted({r[0] for r in archetype_rows})
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
            "Archetype share = real count of qualified player-seasons in that archetype (K-Means clustering, "
            "scripts/cluster_players.py) divided by the real total qualified pool that season. three_pt_rate is "
            "a minutes-weighted league average of each real player's own 3PA/FGA that season (the share of shot "
            "attempts taken from three, not raw makes or attempts per game, which pace changes would confound). "
            "ts_pct is a minutes-weighted league average of real True Shooting %. pace_proxy is SUM(real season "
            "possessions) / SUM(real season minutes) * 48 — a real, minutes-weighted approximation of league pace "
            "built from player-level possession/minutes data (not the official team-level NBA pace stat, which "
            "this project doesn't have a historical source for), disclosed as an approximation rather than "
            "presented as the official number."
        ),
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
