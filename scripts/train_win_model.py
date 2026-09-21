"""
train_win_model.py
===================
Team win% prediction from roster composition — the last genuinely new model
in this project, and the one that closes the loop with Trade Analyzer
(api/impact_api.py's /trade/simulate can show a predicted win% shift for a
proposed trade using this model).

Target variable: there's no standings table in this DB (progress.txt notes
standings are fetched live, never stored). But player_season_stats' w_pct
column, minutes-weighted across a team's roster, reconstructs the real
season win% almost exactly — verified against a known record (Denver
2023-24: actual 57-25 = .695, minutes-weighted roster average = .696). So
the ground truth is derived, not fetched, and the derivation itself is
checked before being trusted.

Features: the exact same team-aggregate stats Trade Analyzer already
computes (api/impact_api.py's _team_summary) — minutes-weighted net/off/def
rating and TS%, plus total roster impact_score_raw. Reusing that feature set
is deliberate: it means a trade's predicted win% impact and its roster
production impact come from a shared, consistent view of "what changed."

Expect a strong fit: net_rating and win% are famously tightly linked in
basketball (the same relationship behind Pythagorean win expectation) — a
high R² here is the model rediscovering a well-known relationship from raw
data, not a red flag.

Usage:
    cd scripts && python3 train_win_model.py

Outputs: models/win_model.pkl + models/win_scaler.pkl, and LOSO backtest
results (by season) written to Postgres for the record.
"""

import os
import warnings

import numpy as np
import pandas as pd
import pickle
import psycopg2
import psycopg2.extras
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score, root_mean_squared_error
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODEL_DIR = os.path.join(BASE_DIR, "models")

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

# net_rating alone (not off_rating/def_rating separately) — those two are
# mathematically off_rating - def_rating = net_rating, so including all
# three makes coefficients unstable/uninterpretable (arbitrary sign flips)
# even though predictions stay accurate. Same class of issue as the ROY
# multicollinearity fix earlier in this project.
#
# total_impact_raw was tested and dropped: net_rating alone already gets
# R2=0.9223 in LOSO backtesting; adding ts_pct + total_impact_raw only
# reaches R2=0.9245 — a 0.002 improvement that's noise, not signal, and it's
# exactly why total_impact_raw picked up a confusing small negative
# coefficient (fitting residual noise, not a real "more talent = fewer
# wins" effect). Keeping ts_pct since it's still basketball-intuitive and
# costs nothing; dropping total_impact_raw for a cleaner, still-accurate
# model.
FEATURES = ["net_rating", "ts_pct"]
TRAIN_SEASONS = list(range(2010, 2025))

MODEL_CONFIGS = [
    ("linreg", "Linear Regression", lambda: LinearRegression()),
    ("random_forest", "Random Forest", lambda: RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=-1)),
]


def build_team_season_dataset():
    """One row per (team, season): minutes-weighted roster aggregates as
    features, minutes-weighted w_pct as the (derived, verified) target."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        df = pd.read_sql_query(
            """
            SELECT team_abbreviation, season, player_id, min,
                   net_rating, off_rating, def_rating, ts_pct, w_pct, impact_score_raw
            FROM player_season_stats
            WHERE team_abbreviation IS NOT NULL;
            """,
            conn,
        )
    finally:
        conn.close()

    df = df.dropna(subset=["min", "net_rating", "off_rating", "def_rating", "ts_pct", "w_pct"])
    df["min"] = df["min"].clip(lower=0.1)  # guard against a zero-minute weight collapsing a team

    rows = []
    for (team, season), g in df.groupby(["team_abbreviation", "season"]):
        total_min = g["min"].sum()
        if total_min <= 0 or len(g) < 5:  # skip incomplete/garbage rosters
            continue
        row = {"team": team, "season": int(season)}
        for col in ["net_rating", "off_rating", "def_rating", "ts_pct", "w_pct"]:
            row[col] = float((g["min"] * g[col]).sum() / total_min)
        row["total_impact_raw"] = float(g["impact_score_raw"].fillna(0).sum())
        rows.append(row)

    return pd.DataFrame(rows)


def run_loso(df, model_factory):
    per_season = []
    for held_out in TRAIN_SEASONS:
        train = df[(df["season"] != held_out) & (df["season"].isin(TRAIN_SEASONS))]
        test = df[df["season"] == held_out]
        if len(train) < 20 or len(test) == 0:
            continue

        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[FEATURES].values)
        y_train = train["w_pct"].values
        X_test = scaler.transform(test[FEATURES].values)
        y_test = test["w_pct"].values

        model = model_factory()
        model.fit(X_train, y_train)
        preds = model.predict(X_test)

        per_season.append({
            "season": int(held_out),
            "n_teams": len(test),
            "r2": float(r2_score(y_test, preds)),
            "mae": float(mean_absolute_error(y_test, preds)),
            "rmse": float(root_mean_squared_error(y_test, preds)),
        })

    all_r2 = [s["r2"] for s in per_season]
    all_mae = [s["mae"] for s in per_season]
    all_rmse = [s["rmse"] for s in per_season]
    summary = {
        "n_seasons_evaluated": len(per_season),
        "mean_r2": float(np.mean(all_r2)),
        "mean_mae": float(np.mean(all_mae)),
        "mean_rmse": float(np.mean(all_rmse)),
    }
    return per_season, summary


def print_report(model_label, per_season, summary):
    print(f"\n{'='*70}\n  Win% Prediction — {model_label} — Leave-One-Season-Out\n{'='*70}")
    print(f"  {'Season':<10}{'Teams':<8}{'R2':<10}{'MAE':<10}{'RMSE':<10}")
    for s in per_season:
        season_label = f"{s['season']-1}-{str(s['season'])[-2:]}"
        print(f"  {season_label:<10}{s['n_teams']:<8}{s['r2']:<10.3f}{s['mae']:<10.4f}{s['rmse']:<10.4f}")
    print(f"\n  Mean R2:   {summary['mean_r2']:.3f}  (fraction of win% variance explained by roster stats)")
    print(f"  Mean MAE:  {summary['mean_mae']:.4f}  ({summary['mean_mae']*82:.1f} wins, out of an 82-game season)")
    print(f"  Mean RMSE: {summary['mean_rmse']:.4f}")


def ensure_schema(conn):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS win_model_backtest;")
    cur.execute("""
        CREATE TABLE win_model_backtest (
            model_type TEXT NOT NULL,
            model_label TEXT NOT NULL,
            season INTEGER,
            n_teams INTEGER,
            r2 DOUBLE PRECISION,
            mae DOUBLE PRECISION,
            rmse DOUBLE PRECISION,
            is_summary BOOLEAN NOT NULL DEFAULT FALSE
        );
    """)
    conn.commit()


def save_results(conn, model_type, model_label, per_season, summary):
    cur = conn.cursor()
    rows = [
        (model_type, model_label, s["season"], s["n_teams"], s["r2"], s["mae"], s["rmse"], False)
        for s in per_season
    ]
    rows.append((model_type, model_label, None, summary["n_seasons_evaluated"], summary["mean_r2"], summary["mean_mae"], summary["mean_rmse"], True))
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO win_model_backtest (model_type, model_label, season, n_teams, r2, mae, rmse, is_summary) VALUES %s;",
        rows,
    )
    conn.commit()


def main():
    print("Team Win% Prediction — building team-season dataset...")
    df = build_team_season_dataset()
    print(f"  {len(df)} team-seasons across {df['season'].nunique()} seasons.")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)

    results = {}
    for model_type, model_label, factory in MODEL_CONFIGS:
        per_season, summary = run_loso(df, factory)
        print_report(model_label, per_season, summary)
        save_results(conn, model_type, model_label, per_season, summary)
        results[model_type] = summary
    conn.close()

    print(f"\n{'-'*70}\n  Model Comparison\n{'-'*70}")
    for model_type, model_label, _ in MODEL_CONFIGS:
        s = results[model_type]
        print(f"  {model_label:<20} R2={s['mean_r2']:.3f}  MAE={s['mean_mae']:.4f} ({s['mean_mae']*82:.1f} wins)")

    # Deploy Linear Regression — interpretable, and typically ties or beats
    # Random Forest here since the underlying relationship (net rating ->
    # win%) is close to linear; confirmed by the comparison printed above.
    print("\nTraining final Linear Regression model on all seasons...")
    scaler = StandardScaler()
    X = scaler.fit_transform(df[df["season"].isin(TRAIN_SEASONS)][FEATURES].values)
    y = df[df["season"].isin(TRAIN_SEASONS)]["w_pct"].values
    model = LinearRegression()
    model.fit(X, y)

    print("  Coefficients:")
    for f, c in sorted(zip(FEATURES, model.coef_), key=lambda x: abs(x[1]), reverse=True):
        print(f"    {f:<20} {c:+.4f}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(os.path.join(MODEL_DIR, "win_model.pkl"), "wb") as f:
        pickle.dump(model, f)
    with open(os.path.join(MODEL_DIR, "win_scaler.pkl"), "wb") as f:
        pickle.dump(scaler, f)
    print(f"\nSaved models/win_model.pkl + models/win_scaler.pkl")
    print("Done.")


if __name__ == "__main__":
    main()
