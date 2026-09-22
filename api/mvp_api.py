"""
mvp_api.py
===========
FastAPI backend for NBA MVP Prediction.

Endpoints:
    GET /mvp/predict/{season}    — Top 15 MVP candidates with probabilities
    GET /dpoy/predict/{season}   — Top 15 DPOY candidates with probabilities
    GET /roy/predict/{season}    — Top 15 ROY candidates with probabilities
    GET /allnba/predict/{season} — Predicted 15-player All-NBA pool w/ tiers

Usage:
    uvicorn mvp_api:app --reload
"""

import os
import pickle
from contextlib import contextmanager

import numpy as np
import pandas as pd
import psycopg2
from psycopg2 import pool
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ─── App Setup ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="NBA MVP Prediction API",
    description="Predict MVP probabilities for any season using Logistic Regression.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Load Model & Scaler (once at startup) ──────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "mvp_model.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "mvp_model.pkl"),
    os.path.join(SCRIPT_DIR, "..", "scripts", "mvp_model.pkl"),
]
SCALER_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "mvp_scaler.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "mvp_scaler.pkl"),
    os.path.join(SCRIPT_DIR, "..", "scripts", "mvp_scaler.pkl"),
]

def load_first_existing(candidates):
    for path in candidates:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            with open(resolved, "rb") as f:
                return pickle.load(f)
    raise FileNotFoundError(f"Could not find artifact in: {candidates}")

model = load_first_existing(MODEL_CANDIDATES)
scaler = load_first_existing(SCALER_CANDIDATES)

FEATURES = [
    "pts", "ts_pct", "usg_pct", "off_rating", "def_rating",
    "net_rating", "w_pct", "min", "age",
]

# DPOY/ROY models — same logistic-regression + StandardScaler pipeline as
# MVP, trained in build_dpoy_roy_models.py. Feature lists and candidate-pool
# rules (DPOY: min/gp floor; ROY: rookie season only) must match that script
# exactly, or predictions here would silently diverge from what was trained.
DPOY_MODEL_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "dpoy_model.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "dpoy_model.pkl"),
]
DPOY_SCALER_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "dpoy_scaler.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "dpoy_scaler.pkl"),
]
ROY_MODEL_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "roy_model.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "roy_model.pkl"),
]
ROY_SCALER_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "roy_scaler.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "roy_scaler.pkl"),
]

dpoy_model = load_first_existing(DPOY_MODEL_CANDIDATES)
dpoy_scaler = load_first_existing(DPOY_SCALER_CANDIDATES)
roy_model = load_first_existing(ROY_MODEL_CANDIDATES)
roy_scaler = load_first_existing(ROY_SCALER_CANDIDATES)

DPOY_FEATURES = ["def_rating", "net_rating", "stl", "blk", "reb_pct", "min", "w_pct"]
ROY_FEATURES = ["pts", "ts_pct", "usg_pct", "net_rating", "min", "age"]
DPOY_MIN_MINUTES = 24
DPOY_MIN_GAMES = 40

# All-NBA model — trained in build_all_nba_model.py against 240 real
# historical selections (fetch_all_nba_teams.py). Binary "made any All-NBA
# team" classifier; the live endpoint reconstructs First/Second/Third Team
# by simple rank cutoff (top 5/10/15), not by modeling per-tier voting.
ALLNBA_MODEL_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "all_nba_model.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "all_nba_model.pkl"),
]
ALLNBA_SCALER_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "all_nba_scaler.pkl"),
    os.path.join(SCRIPT_DIR, "..", "models", "all_nba_scaler.pkl"),
]
allnba_model = load_first_existing(ALLNBA_MODEL_CANDIDATES)
allnba_scaler = load_first_existing(ALLNBA_SCALER_CANDIDATES)

ALLNBA_FEATURES = [
    "pts", "reb", "ast", "stl", "blk",
    "ts_pct", "usg_pct", "net_rating", "w_pct", "min", "age",
]
ALLNBA_MIN_MINUTES = 24
ALLNBA_MIN_GAMES = 40
ALLNBA_SELECTIONS = 15

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
    """Get a connection from the pool, auto-return on exit."""
    conn = DB_POOL.getconn()
    try:
        yield conn
    finally:
        DB_POOL.putconn(conn)


# ─── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "NBA MVP Prediction API",
        "version": "1.0.0",
        "endpoints": [
            "/mvp/predict/{season}", "/backtest", "/backtest/{award}", "/backtest/{award}/compare",
            "/explain/{award}", "/explain/{award}/{player_name}",
        ],
    }


# ─── Model Validation / Backtest ────────────────────────────────────────────
# Reads results written by scripts/backtest_models.py (leave-one-season-out
# validation for MVP/DPOY/ROY, across multiple model types) — see that
# script for methodology.

VALID_AWARDS = {"mvp", "dpoy", "roy"}
DEFAULT_MODEL_TYPE = "logreg"

# All-NBA's backtest lives in its own tables (all_nba_backtest_summary /
# all_nba_backtest_seasons) with a genuinely different shape — see
# build_all_nba_model.py's docstring: it's a 15-winner-per-season award, so
# "precision@15" replaces the rank-of-the-single-winner metrics the other
# three awards use. Handled as a branch below rather than forced into
# SUMMARY_COLS / model_backtest_seasons' single-winner schema.
ALLNBA_SUMMARY_COLS = [
    "award", "n_seasons_evaluated", "mean_precision_at_15", "roc_auc",
    "roc_curve", "feature_importance", "holdout_season", "updated_at",
]

SUMMARY_COLS = [
    "award", "model_type", "model_label", "n_seasons_evaluated", "top1_accuracy", "top3_accuracy",
    "top5_accuracy", "mean_reciprocal_rank", "roc_auc", "roc_curve", "precision_at_0_5", "recall_at_0_5",
    "f1_at_0_5", "confusion_matrix", "feature_importance", "updated_at",
]


def _season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"


def _row_to_summary(row):
    data = dict(zip(SUMMARY_COLS, row))
    data["updated_at"] = data["updated_at"].isoformat() if data["updated_at"] else None
    return data


def _fetch_summary(cursor, award: str, model_type: str):
    cursor.execute(
        f"SELECT {', '.join(SUMMARY_COLS)} FROM model_backtest_summary WHERE award = %s AND model_type = %s;",
        (award.upper(), model_type),
    )
    row = cursor.fetchone()
    return _row_to_summary(row) if row else None


@app.get("/backtest")
def get_backtest_overview():
    """Summary metrics for every (award, model_type) combination that's been backtested."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {', '.join(SUMMARY_COLS)} FROM model_backtest_summary ORDER BY award, model_type;")
        summaries = [_row_to_summary(row) for row in cursor.fetchall()]

    if not summaries:
        raise HTTPException(
            status_code=404,
            detail="No backtest results found. Run scripts/backtest_models.py first.",
        )
    return {"awards": summaries}


@app.get("/backtest/allnba")
def get_allnba_backtest():
    """
    All-NBA's LOSO backtest: mean precision@15 across 16 seasons (of the 15
    players predicted each held-out season, how many were actually
    selected), plus the pooled ROC curve, final-model feature coefficients,
    and the real 2024-25 holdout sanity check (never seen in training).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT {', '.join(ALLNBA_SUMMARY_COLS)} FROM all_nba_backtest_summary WHERE award = 'ALL_NBA';")
        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=404,
                detail="No All-NBA backtest results found. Run scripts/build_all_nba_model.py first.",
            )
        summary = dict(zip(ALLNBA_SUMMARY_COLS, row))
        summary["updated_at"] = summary["updated_at"].isoformat() if summary["updated_at"] else None

        cursor.execute(
            "SELECT season, hits, precision_at_15, top15 FROM all_nba_backtest_seasons "
            "WHERE award = 'ALL_NBA' ORDER BY season ASC;"
        )
        seasons = [
            {
                "season": r[0],
                "season_label": _season_label(r[0]),
                "hits": r[1],
                "precision_at_15": r[2],
                "top15": r[3],
            }
            for r in cursor.fetchall()
        ]

    return {"award": "ALL_NBA", "summary": summary, "seasons": seasons}


@app.get("/backtest/{award}")
def get_backtest_detail(award: str, model: str = DEFAULT_MODEL_TYPE):
    """Full backtest results for one award + model: summary metrics + every held-out season."""
    award = award.lower()
    if award not in VALID_AWARDS:
        raise HTTPException(status_code=400, detail=f"award must be one of {sorted(VALID_AWARDS)}.")

    with get_db() as conn:
        cursor = conn.cursor()
        summary = _fetch_summary(cursor, award, model)
        if not summary:
            raise HTTPException(
                status_code=404,
                detail=f"No backtest results for '{award}' / model '{model}'. Run scripts/backtest_models.py first.",
            )

        cursor.execute(
            """
            SELECT season, actual_winner, predicted_rank, num_candidates, top5
            FROM model_backtest_seasons
            WHERE award = %s AND model_type = %s
            ORDER BY season ASC;
            """,
            (award.upper(), model),
        )
        seasons = [
            {
                "season": row[0],
                "season_label": _season_label(row[0]),
                "actual_winner": row[1],
                "predicted_rank": row[2],
                "num_candidates": row[3],
                "top5": row[4],
            }
            for row in cursor.fetchall()
        ]

    return {"award": award.upper(), "model_type": model, "summary": summary, "seasons": seasons}


@app.get("/backtest/{award}/compare")
def get_backtest_comparison(award: str):
    """Every model's summary metrics for one award, side by side — no per-season detail."""
    award = award.lower()
    if award not in VALID_AWARDS:
        raise HTTPException(status_code=400, detail=f"award must be one of {sorted(VALID_AWARDS)}.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {', '.join(SUMMARY_COLS)} FROM model_backtest_summary WHERE award = %s ORDER BY model_type;",
            (award.upper(),),
        )
        summaries = [_row_to_summary(row) for row in cursor.fetchall()]

    if not summaries:
        raise HTTPException(
            status_code=404,
            detail=f"No backtest results for '{award}'. Run scripts/backtest_models.py first.",
        )
    return {"award": award.upper(), "models": summaries}


# ─── SHAP Explainability (Random Forest only) ───────────────────────────────
# Reads results written by scripts/build_shap_explanations.py. Logistic
# Regression doesn't need this — its coefficients (see /backtest feature
# importance) already explain every prediction directly. Random Forest's
# predictions come from voting across 200 trees with no simple formula, so
# SHAP decomposes one player's predicted probability into each feature's
# contribution, starting from the model's base rate.

@app.get("/explain/{award}")
def get_shap_candidates(award: str):
    """Top candidates for the current prediction season with their SHAP-explained probability."""
    award = award.lower()
    if award not in VALID_AWARDS:
        raise HTTPException(status_code=400, detail=f"award must be one of {sorted(VALID_AWARDS)}.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT player_id, player_name, predicted_probability, season, base_value
            FROM shap_explanations
            WHERE award = %s
            ORDER BY predicted_probability DESC;
            """,
            (award.upper(),),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No SHAP results for '{award}'. Run scripts/build_shap_explanations.py first.",
        )
    return {
        "award": award.upper(),
        "season": rows[0][3],
        "base_value": rows[0][4],
        "candidates": [
            {"player_id": r[0], "player_name": r[1], "predicted_probability": r[2]}
            for r in rows
        ],
    }


@app.get("/explain/{award}/{player_name}")
def get_shap_breakdown(award: str, player_name: str):
    """One player's full SHAP feature breakdown, sorted by |contribution| descending."""
    award = award.lower()
    if award not in VALID_AWARDS:
        raise HTTPException(status_code=400, detail=f"award must be one of {sorted(VALID_AWARDS)}.")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT player_id, player_name, predicted_probability, base_value,
                   feature, feature_value, shap_value, season
            FROM shap_explanations
            WHERE award = %s AND LOWER(player_name) = LOWER(%s)
            ORDER BY ABS(shap_value) DESC;
            """,
            (award.upper(), player_name),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No SHAP explanation for '{player_name}' in {award.upper()}. "
                   f"Only the top candidates for the current prediction season are explained.",
        )

    first = rows[0]
    return {
        "award": award.upper(),
        "season": first[7],
        "player_id": first[0],
        "player_name": first[1],
        "predicted_probability": first[2],
        "base_value": first[3],
        "features": [
            {"feature": r[4], "feature_value": r[5], "shap_value": r[6]}
            for r in rows
        ],
    }


@app.get("/mvp/predict/{season}")
def predict_mvp(season: int, top_n: int = 15):
    """
    Predict MVP probabilities for all players in a given season.
    Returns top N candidates sorted by probability descending.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Check season exists
        cursor.execute(
            "SELECT MIN(season), MAX(season) FROM player_season_stats;"
        )
        season_min, season_max = cursor.fetchone()

        cursor.execute(
            "SELECT COUNT(*) FROM player_season_stats WHERE season = %s;",
            (season,),
        )
        count = cursor.fetchone()[0]
        if count == 0:
            available_range = (
                f"{int(season_min)}–{int(season_max)}"
                if season_min is not None and season_max is not None
                else "unknown"
            )
            raise HTTPException(
                status_code=404,
                detail=f"No data for season {season}. "
                       f"Available range: {available_range}.",
            )

        # Pull season data
        cols = ", ".join(["player_id", "player_name", "team_abbreviation"] + FEATURES)
        cursor.execute(
            f"SELECT {cols} FROM player_season_stats WHERE season = %s;",
            (season,),
        )
        rows = cursor.fetchall()
        col_names = ["player_id", "player_name", "team_abbreviation"] + FEATURES
        df = pd.DataFrame(rows, columns=col_names)

    # Drop rows with NULLs in features
    df = df.dropna(subset=FEATURES).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No valid player data for season {season} after removing NULLs.",
        )

    # Scale features and predict
    X = df[FEATURES].values
    X_scaled = scaler.transform(X)
    probabilities = model.predict_proba(X_scaled)[:, 1]

    # Build results
    df["mvp_probability"] = probabilities
    df = df.sort_values("mvp_probability", ascending=False).head(top_n)

    return {
        "season": season,
        "total_players": count,
        "results": [
            {
                "rank": i + 1,
                "player_id": int(row["player_id"]),
                "player_name": row["player_name"],
                "team_abbreviation": row["team_abbreviation"],
                "mvp_probability": round(float(row["mvp_probability"]), 4),
                "pts": round(float(row["pts"]), 1),
                "ts_pct": round(float(row["ts_pct"]), 3),
                "w_pct": round(float(row["w_pct"]), 3),
                "net_rating": round(float(row["net_rating"]), 1),
            }
            for i, (_, row) in enumerate(df.iterrows())
        ],
    }


@app.get("/dpoy/predict/{season}")
def predict_dpoy(season: int, top_n: int = 15):
    """
    Predict DPOY probabilities for a given season, restricted to the same
    candidate pool the model was trained on (min>=24, gp>=40) — without
    this floor the model is asked to rank low-minute bench players it has
    never seen a positive label for, which just adds noise near the top.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
        season_min, season_max = cursor.fetchone()

        cols = ", ".join(["player_id", "player_name", "team_abbreviation"] + DPOY_FEATURES + ["gp"])
        cursor.execute(
            f"""
            SELECT {cols} FROM player_season_stats
            WHERE season = %s AND min >= %s AND gp >= %s;
            """,
            (season, DPOY_MIN_MINUTES, DPOY_MIN_GAMES),
        )
        rows = cursor.fetchall()

    if not rows:
        available_range = (
            f"{int(season_min)}–{int(season_max)}"
            if season_min is not None and season_max is not None
            else "unknown"
        )
        raise HTTPException(
            status_code=404,
            detail=f"No DPOY-eligible players (min>={DPOY_MIN_MINUTES}, "
                   f"gp>={DPOY_MIN_GAMES}) for season {season}. "
                   f"Available range: {available_range}.",
        )

    col_names = ["player_id", "player_name", "team_abbreviation"] + DPOY_FEATURES + ["gp"]
    df = pd.DataFrame(rows, columns=col_names)
    df = df.dropna(subset=DPOY_FEATURES).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No valid DPOY candidate data for season {season} after removing NULLs.",
        )

    X_scaled = dpoy_scaler.transform(df[DPOY_FEATURES].values)
    df["dpoy_probability"] = dpoy_model.predict_proba(X_scaled)[:, 1]
    df = df.sort_values("dpoy_probability", ascending=False).head(top_n)

    return {
        "season": season,
        "candidate_pool_size": len(rows),
        "candidate_pool_rule": f"min>={DPOY_MIN_MINUTES}, gp>={DPOY_MIN_GAMES}",
        "results": [
            {
                "rank": i + 1,
                "player_id": int(row["player_id"]),
                "player_name": row["player_name"],
                "team_abbreviation": row["team_abbreviation"],
                "dpoy_probability": round(float(row["dpoy_probability"]), 4),
                "def_rating": round(float(row["def_rating"]), 1),
                "net_rating": round(float(row["net_rating"]), 1),
                "stl": round(float(row["stl"]), 1),
                "blk": round(float(row["blk"]), 1),
                "reb_pct": round(float(row["reb_pct"]), 3),
            }
            for i, (_, row) in enumerate(df.iterrows())
        ],
    }


@app.get("/roy/predict/{season}")
def predict_roy(season: int, top_n: int = 15):
    """
    Predict ROY probabilities for a given season, restricted to players in
    their rookie season (their first season anywhere in player_season_stats)
    — matching the candidate pool build_dpoy_roy_models.py trained on.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
        season_min, season_max = cursor.fetchone()

        if season == season_min:
            raise HTTPException(
                status_code=400,
                detail=f"Season {season} is this dataset's first season on record — "
                       f"rookies that year can't be distinguished from players who were "
                       f"already veterans before the data starts, so ROY isn't computed for it.",
            )

        cols = ", ".join(["player_id", "player_name", "team_abbreviation"] + ROY_FEATURES)
        cursor.execute(
            f"""
            SELECT {cols} FROM player_season_stats p
            WHERE p.season = %s
              AND p.season = (
                  SELECT MIN(season) FROM player_season_stats
                  WHERE player_id = p.player_id
              );
            """,
            (season,),
        )
        rows = cursor.fetchall()

    if not rows:
        available_range = (
            f"{int(season_min)}–{int(season_max)}"
            if season_min is not None and season_max is not None
            else "unknown"
        )
        raise HTTPException(
            status_code=404,
            detail=f"No rookie-season players found for season {season}. "
                   f"Available range: {available_range}.",
        )

    col_names = ["player_id", "player_name", "team_abbreviation"] + ROY_FEATURES
    df = pd.DataFrame(rows, columns=col_names)
    df = df.dropna(subset=ROY_FEATURES).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No valid ROY candidate data for season {season} after removing NULLs.",
        )

    X_scaled = roy_scaler.transform(df[ROY_FEATURES].values)
    df["roy_probability"] = roy_model.predict_proba(X_scaled)[:, 1]
    df = df.sort_values("roy_probability", ascending=False).head(top_n)

    return {
        "season": season,
        "candidate_pool_size": len(rows),
        "candidate_pool_rule": "rookie season only (player's first season on record)",
        "results": [
            {
                "rank": i + 1,
                "player_id": int(row["player_id"]),
                "player_name": row["player_name"],
                "team_abbreviation": row["team_abbreviation"],
                "roy_probability": round(float(row["roy_probability"]), 4),
                "pts": round(float(row["pts"]), 1),
                "ts_pct": round(float(row["ts_pct"]), 3),
                "usg_pct": round(float(row["usg_pct"]), 3),
                "net_rating": round(float(row["net_rating"]), 1),
                "min": round(float(row["min"]), 1),
            }
            for i, (_, row) in enumerate(df.iterrows())
        ],
    }


@app.get("/allnba/predict/{season}")
def predict_all_nba(season: int):
    """
    Predict the 15-player All-NBA pool for a season (probability of making
    any All-NBA team), restricted to the same candidate pool the model was
    trained on (min>=24, gp>=40). Team tier (First/Second/Third) is
    reconstructed by rank cutoff over the top 15 — an honest approximation
    of actual voting tiers, not a model of them, and labeled as such.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
        season_min, season_max = cursor.fetchone()

        cols = ", ".join(["player_id", "player_name", "team_abbreviation"] + ALLNBA_FEATURES)
        cursor.execute(
            f"""
            SELECT {cols} FROM player_season_stats
            WHERE season = %s AND min >= %s AND gp >= %s;
            """,
            (season, ALLNBA_MIN_MINUTES, ALLNBA_MIN_GAMES),
        )
        rows = cursor.fetchall()

    if not rows:
        available_range = (
            f"{int(season_min)}–{int(season_max)}"
            if season_min is not None and season_max is not None
            else "unknown"
        )
        raise HTTPException(
            status_code=404,
            detail=f"No All-NBA-eligible players (min>={ALLNBA_MIN_MINUTES}, "
                   f"gp>={ALLNBA_MIN_GAMES}) for season {season}. "
                   f"Available range: {available_range}.",
        )

    col_names = ["player_id", "player_name", "team_abbreviation"] + ALLNBA_FEATURES
    df = pd.DataFrame(rows, columns=col_names)
    df = df.dropna(subset=ALLNBA_FEATURES).reset_index(drop=True)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No valid All-NBA candidate data for season {season} after removing NULLs.",
        )

    X_scaled = allnba_scaler.transform(df[ALLNBA_FEATURES].values)
    df["all_nba_probability"] = allnba_model.predict_proba(X_scaled)[:, 1]
    df = df.sort_values("all_nba_probability", ascending=False).head(ALLNBA_SELECTIONS)

    def tier_for_rank(rank):
        if rank <= 5:
            return "First Team"
        if rank <= 10:
            return "Second Team"
        return "Third Team"

    return {
        "season": season,
        "candidate_pool_size": len(rows),
        "candidate_pool_rule": f"min>={ALLNBA_MIN_MINUTES}, gp>={ALLNBA_MIN_GAMES}",
        "tier_method": "rank cutoff (1-5 First, 6-10 Second, 11-15 Third) — approximate, not modeled per tier",
        "results": [
            {
                "rank": i + 1,
                "predicted_team": tier_for_rank(i + 1),
                "player_id": int(row["player_id"]),
                "player_name": row["player_name"],
                "team_abbreviation": row["team_abbreviation"],
                "all_nba_probability": round(float(row["all_nba_probability"]), 4),
                "pts": round(float(row["pts"]), 1),
                "reb": round(float(row["reb"]), 1),
                "ast": round(float(row["ast"]), 1),
                "net_rating": round(float(row["net_rating"]), 1),
            }
            for i, (_, row) in enumerate(df.iterrows())
        ],
    }


# ─── Main Guard ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("mvp_api:app", host="0.0.0.0", port=8001, reload=True)
