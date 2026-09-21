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

DB_POOL = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    host="localhost",
    port="5432",
    user="postgres",
    password="meinkampf:)",
    dbname="nba_analytics",
)


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
    uvicorn.run("similarity_api:app", host="0.0.0.0", port=8000, reload=True)
