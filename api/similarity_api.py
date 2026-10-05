"""
similarity_api.py
==================
FastAPI backend for NBA Player Similarity Engine.

Endpoints:
    GET /similarity/season/{player_name}/{season}  — Top 10 similar seasons
    GET /similarity/season-profile/{player_name}/{season} — Same method, computed
        live, with filters and each season's stat profile
    GET /similarity/stat-line?line=pts:25,ts_pct:0.6,ast:8 — Closest real
        player-seasons to a typed stat line
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
from season_team import season_team_sql  # the team shown: not one he never played for that season

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

# Latest career first: 19 names belong to two players (impact_core.find_player, same order).
_PLAYER_ORDER = "ORDER BY MAX(season) DESC, SUM(COALESCE(min, 0) * COALESCE(gp, 0)) DESC, player_id"


def find_player_id(cursor, player_name: str, player_id: int | None = None):
    """
    (player_id, name) for a typed name (case-insensitive, then partial, then
    accents ignored; the latest player of a shared name), or for an NBA id
    when the page knows it. Raises 404 if not found.
    """
    if player_id is not None:
        cursor.execute(
            "SELECT player_name FROM player_season_stats WHERE player_id = %s ORDER BY season DESC LIMIT 1;",
            (player_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"No player with id {player_id}.")
        return player_id, row[0]

    # Try exact match first
    cursor.execute(
        "SELECT player_id, (array_agg(player_name ORDER BY season DESC))[1] FROM player_season_stats "
        f"WHERE LOWER(player_name) = LOWER(%s) GROUP BY player_id {_PLAYER_ORDER} LIMIT 1;",
        (player_name,),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Try partial match
    cursor.execute(
        "SELECT player_id, (array_agg(player_name ORDER BY season DESC))[1] FROM player_season_stats "
        f"WHERE LOWER(player_name) LIKE LOWER(%s) GROUP BY player_id {_PLAYER_ORDER} LIMIT 1;",
        (f"%{player_name}%",),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Accent-insensitive fallback (handles cases like jokic -> Jokić)
    normalized_query = normalize_text(player_name)
    cursor.execute(
        f"SELECT player_id, player_name FROM player_season_stats GROUP BY player_id, player_name {_PLAYER_ORDER};"
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
            f"""
            SELECT player_id, player_name, {season_team_sql(cursor)}, role,
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
def get_player_cluster_history(player_name: str, player_id: int | None = None):
    """A player's archetype across every season they were clustered in — shows role evolution."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player_id(cursor, player_name, player_id)

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
def get_season_similarity(player_name: str, season: int, top_n: int = 10, player_id: int | None = None):
    """
    Find the top N most similar player-seasons for a given player + season.
    Uses precomputed league-adjusted cosine similarity from season_similarity table.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Resolve player
        player_id, resolved_name = find_player_id(cursor, player_name, player_id)

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
            SELECT player_id, player_name, season, {season_team_sql(cur)}, gp, {", ".join(SIM_FEATURES)}
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
                                  one_per_player: bool = False, player_id: int | None = None):
    """
    Most similar player-seasons, with filters: exclude_self drops the player's
    own other seasons, min_gp drops matches with fewer games, one_per_player
    keeps only each player's closest season. Each match lists the inputs it's
    closest on and the one it differs most on. player_id, when given, picks
    the player exactly (two players can share a name) and player_name is
    only used in messages.
    """
    top_n = max(1, min(top_n, 25))
    min_gp = max(0, min_gp)
    if player_id is not None:
        resolved_name = player_name
    else:
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


# ─── Similar seasons from a typed stat line ─────────────────────────────────
# "25 pts, 60% TS, 8 ast" -> the closest real player-seasons. Same idea as the
# season matrix above (each stat z-scored within its own season, over every
# player-season that has it, ddof=1), but with the box-score stats people
# actually type. The typed line is z-scored against one chosen season, so
# "25 points in 2025-26" matches seasons with the same standing in their own
# year, not the same raw number.

# key -> (label, format, first_season, attempts_column). First seasons and
# attempt floors match routers/leaderboard.py STATS / ATTEMPT_DEFAULTS
# (a smoke test checks they agree).
LINE_STATS = {
    "pts": ("Points", "num1", 1950, None),
    "reb": ("Rebounds", "num1", 1951, None),
    "ast": ("Assists", "num1", 1950, None),
    "stl": ("Steals", "num1", 1974, None),
    "blk": ("Blocks", "num1", 1974, None),
    "tov": ("Turnovers", "num1", 1978, None),
    "fg3a": ("3-point attempts", "num1", 1980, None),
    "fg3_pct": ("3-point %", "pct", 1980, "fg3a"),
    "ft_pct": ("Free-throw %", "pct", 1950, "fta"),
    "ts_pct": ("True shooting %", "pct", 1950, "fga"),
    "usg_pct": ("Usage %", "pct", 1978, None),
    "ast_pct": ("Assist %", "pct", 1965, None),
    "reb_pct": ("Rebound %", "pct", 1971, None),
    "net_rating": ("Net rating", "signed1", 2010, None),
    "min": ("Minutes", "num1", 1952, None),
    "age": ("Age", "int", 1950, None),
}
LINE_ATTEMPTS = {"fga": 5.0, "fg3a": 2.0, "fta": 2.0}  # per game, as in the Leaderboard Builder
LINE_MAX_STATS = 10


@lru_cache(maxsize=1)
def _line_matrix():
    """Every player-season with each LINE_STATS stat as raw value and
    within-season z (NaN where it isn't recorded yet, or a shooting % is
    below its attempts floor), plus each season's mean and SD per stat."""
    keys = list(LINE_STATS)
    att = sorted(LINE_ATTEMPTS)
    cols = keys + [a for a in att if a not in keys]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT player_id, player_name, season, {season_team_sql(cur)}, gp, {", ".join(cols)}
            FROM player_season_stats ORDER BY player_id, season;
        """)
        rows = cur.fetchall()
    meta = [r[:5] for r in rows]
    vals = np.array([[np.nan if v is None else float(v) for v in r[5:]] for r in rows])
    ci = {c: i for i, c in enumerate(cols)}
    seasons = np.array([m[2] for m in meta])
    raw = vals[:, :len(keys)].copy()
    for k, key in enumerate(keys):
        _label, _fmt, first, attempts = LINE_STATS[key]
        raw[seasons < first, k] = np.nan
        if attempts:
            raw[~(np.nan_to_num(vals[:, ci[attempts]]) >= LINE_ATTEMPTS[attempts]), k] = np.nan
    z = np.full_like(raw, np.nan)
    norms = {}  # season -> (mean, SD, n) per stat
    for s in np.unique(seasons):
        mask = seasons == s
        block = raw[mask]
        mean = np.full(len(keys), np.nan)
        std = np.full(len(keys), np.nan)
        n = (~np.isnan(block)).sum(axis=0)
        for k in range(len(keys)):
            v = block[:, k][~np.isnan(block[:, k])]
            if len(v) >= 2 and v.std(ddof=1) > 0:
                mean[k], std[k] = v.mean(), v.std(ddof=1)
        z[mask] = (block - mean) / std
        norms[int(s)] = (mean, std, n)
    return meta, raw, z, norms


def _parse_line(line: str):
    parsed = {}
    for part in (line or "").split(","):
        if not part.strip():
            continue
        key, _, value = part.partition(":")
        key = key.strip()
        if key not in LINE_STATS:
            raise HTTPException(status_code=400, detail=f"Unknown stat '{key}'. Use: {', '.join(LINE_STATS)}.")
        if key in parsed:
            raise HTTPException(status_code=400, detail=f"'{key}' is given twice.")
        try:
            v = float(value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"'{key}' needs a number, e.g. {key}:25.")
        if not np.isfinite(v):
            raise HTTPException(status_code=400, detail=f"'{key}' needs a finite number.")
        if LINE_STATS[key][1] == "pct" and not 0 <= v <= 1:
            raise HTTPException(status_code=400, detail=f"'{key}' is a share between 0 and 1 (0.6 = 60%).")
        parsed[key] = v
    if not parsed:
        raise HTTPException(status_code=400, detail="Type at least one stat, e.g. line=pts:25,ts_pct:0.6,ast:8.")
    if len(parsed) > LINE_MAX_STATS:
        raise HTTPException(status_code=400, detail=f"At most {LINE_MAX_STATS} stats.")
    return parsed


def _line_catalogue():
    return [{"key": k, "label": v[0], "format": v[1], "first_season": v[2],
             "attempts": v[3], "min_attempts": LINE_ATTEMPTS.get(v[3])}
            for k, v in LINE_STATS.items()]


@app.get("/similarity/stat-line/options")
def stat_line_options():
    """The stats a line can use and the seasons it can be read in."""
    _meta, _raw, _z, norms = _line_matrix()
    return {"stats": _line_catalogue(), "seasons": {"from": min(norms), "to": max(norms)},
            "max_stats": LINE_MAX_STATS,
            "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference")}


@app.get("/similarity/stat-line")
def similar_to_stat_line(line: str, season: int | None = None, season_from: int | None = None,
                         season_to: int | None = None, min_gp: int = 20, one_per_player: bool = True,
                         top_n: int = 25):
    """
    Closest real player-seasons to a typed stat line.

    line = comma-separated key:value pairs (percentages as shares:
    ts_pct:0.6). season = the season the line is read in (default: latest).
    Distance = root-mean-square gap in within-season z-scores over the
    typed stats: 0 = the same standing on every stat, 1 = one standard
    deviation apart on a typical stat.
    """
    parsed = _parse_line(line)
    keys = list(LINE_STATS)
    cols = [keys.index(k) for k in parsed]
    top_n = max(1, min(top_n, 100))
    min_gp = max(0, min_gp)

    meta, raw, z, norms = _line_matrix()
    first = max(LINE_STATS[k][2] for k in parsed)
    last = max(norms)
    label = lambda y: f"{y - 1}-{str(y)[-2:]}"  # noqa: E731
    limiting = max(parsed, key=lambda k: LINE_STATS[k][2])
    if season is None:
        season = last
    if not first <= season <= last:
        raise HTTPException(status_code=400, detail=(
            f"{LINE_STATS[limiting][0]} is recorded from {label(first)} on; pick a season from "
            f"{label(first)} to {label(last)}."))
    lo = max(first, season_from or first)
    hi = min(last, season_to or last)
    if lo > hi:
        raise HTTPException(status_code=400, detail="The season range is empty.")

    mean, std, n_season = norms[season]
    x = np.array([parsed[k] for k in parsed])
    xz = (x - mean[cols]) / std[cols]

    seasons = np.array([m[2] for m in meta])
    gp = np.array([m[4] or 0 for m in meta])
    zc = z[:, cols]
    keep = (~np.isnan(zc).any(axis=1)) & (seasons >= lo) & (seasons <= hi) & (gp >= min_gp)
    cand = np.flatnonzero(keep)
    gaps = zc[cand] - xz
    dist = np.sqrt((gaps ** 2).mean(axis=1))
    order = np.argsort(dist, kind="stable")
    if one_per_player:
        seen = set()
        order = [o for o in order if not (meta[cand[o]][0] in seen or seen.add(meta[cand[o]][0]))]
    order = order[:top_n]

    results = []
    for rank, o in enumerate(order, start=1):
        j = cand[o]
        pid, name, s, team, g = meta[j]
        g_row = gaps[o]
        results.append({
            "rank": rank,
            "player_id": pid,
            "player_name": name,
            "season": s,
            "team": team,
            "gp": g,
            "distance": round(float(dist[o]), 3),
            "stats": {k: {"value": round(float(raw[j, c]), 4), "z": round(float(z[j, c]), 2),
                          "gap_z": round(float(g_row[n]), 2)}
                      for n, (k, c) in enumerate(zip(parsed, cols))},
            # Shares of the squared distance: which stats the match is (not) close on.
            "share_of_distance": {k: (round(float(g_row[n] ** 2 / (g_row ** 2).sum()), 3)
                                      if (g_row ** 2).sum() > 0 else 0.0)
                                  for n, k in enumerate(parsed)},
        })

    return {
        "line": [
            {"key": k, "label": LINE_STATS[k][0], "format": LINE_STATS[k][1], "value": parsed[k],
             "z": round(float(xz[n]), 2), "season_mean": round(float(mean[c]), 4),
             "season_sd": round(float(std[c]), 4), "season_n": int(n_season[c])}
            for n, (k, c) in enumerate(zip(parsed, cols))
        ],
        "season": season,
        "range": {"from": lo, "to": hi},
        "filters": {"min_gp": min_gp, "one_per_player": one_per_player, "top_n": top_n},
        "pool": int(len(cand)),
        "results": results,
        "stats": _line_catalogue(),
        "seasons": {"from": min(norms), "to": last},
        "method": (
            "Each stat is z-scored within its own season (how many standard deviations from that season's "
            "average, over every player-season that has it; shooting percentages only for players above an "
            "attempts floor). Your line is z-scored against the season you pick, and every real player-season "
            "is compared on the stats you typed only. Distance = root-mean-square gap in those z-scores: 0 = "
            "the same standing on every stat, 1 = a typical stat one standard deviation away. Season Similarity "
            "uses the cosine, which rewards a similar overall profile; this matches levels, stat by stat."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }


@app.get("/players/trajectory/{player_name}")
def get_player_trajectory(player_name: str, season: int, top_n_comps: int = 5, project_years: int = 3,
                          player_id: int | None = None):
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
        player_id, resolved_name = find_player_id(cursor, player_name, player_id)

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
def get_career_similarity(player_name: str, top_n: int = 10, player_id: int | None = None):
    """
    Find the top N most similar careers for a given player.
    Uses precomputed cosine similarity from career_similarity table.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Resolve player
        player_id, resolved_name = find_player_id(cursor, player_name, player_id)

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
