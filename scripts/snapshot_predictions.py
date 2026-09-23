"""
snapshot_predictions.py
=========================
Logs today's real live award predictions (MVP, DPOY, ROY, All-NBA) into
prediction_ledger, timestamped, so they can be graded against real
outcomes once the season resolves (resolve_predictions.py) and so a
"probability over time" trajectory can be plotted for the current
season's favorites.

This is the live, ongoing counterpart to the honest leave-one-season-out
backtest tables (model_backtest_seasons, all_nba_backtest_seasons) —
those are historical, this is real-time. They're never mixed: the
ledger only ever holds live predictions actually made at a real point
in time, not backtest output.

Requires mvp_api.py running locally on port 8000 — this script snapshots
the exact same real predictions a user would see in the UI at this
moment (via the live /mvp/predict, /dpoy/predict, /roy/predict,
/allnba/predict endpoints), rather than re-implementing model inference
here. Start it first:
    cd api && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn mvp_api:app --port 8000

Championship-probability snapshots (from the Vegas Scanner) are
intentionally NOT included yet — resolving them would need a real
NBA-champion-history table this project doesn't have, and a ledger
entry that can never resolve isn't worth logging.

Usage:
    cd scripts && python3 snapshot_predictions.py [--season SEASON]

Run this daily or weekly to build up real history. To automate (macOS,
not installed by this script — add manually if wanted):
    # crontab -e, run every day at 09:00
    0 9 * * * cd /path/to/scripts && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 snapshot_predictions.py >> snapshot.log 2>&1
"""

import argparse
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
import requests

from db_config import DB_CONFIG

MVP_API_BASE = "http://localhost:8000"

MODELS = [
    ("mvp", "/mvp/predict/{season}", "mvp_probability"),
    ("dpoy", "/dpoy/predict/{season}", "dpoy_probability"),
    ("roy", "/roy/predict/{season}", "roy_probability"),
    ("all_nba", "/allnba/predict/{season}", "all_nba_probability"),
]


def get_current_nba_season() -> int:
    """Same formula as impact_api.py's get_current_nba_season() —
    kept as a small standalone copy since it's two lines and this
    script has no dependency on the api/ package."""
    now = datetime.now(timezone.utc)
    return now.year + 1 if now.month >= 10 else now.year


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prediction_ledger (
            id SERIAL PRIMARY KEY,
            model TEXT NOT NULL,
            season INT NOT NULL,
            subject TEXT NOT NULL,
            subject_id BIGINT NOT NULL,
            predicted JSONB NOT NULL,
            predicted_at TIMESTAMPTZ NOT NULL,
            resolved_at TIMESTAMPTZ,
            actual JSONB,
            score DOUBLE PRECISION,
            score_type TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_ledger_model_season ON prediction_ledger (model, season);")


def snapshot(season: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    now = datetime.now(timezone.utc)
    total_rows = 0

    for model, path_template, prob_field in MODELS:
        url = MVP_API_BASE + path_template.format(season=season)
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  {model}: FAILED to fetch {url} — {exc}")
            print("  Is mvp_api.py running on port 8000? See this script's docstring.")
            continue

        data = resp.json()
        results = data.get("results", [])
        if not results:
            print(f"  {model}: no real candidates returned for season {season}, skipping.")
            continue

        rows = [
            (
                model, season, r["player_name"], r["player_id"],
                psycopg2.extras.Json({"probability": r[prob_field], "rank": r["rank"]}),
                now,
            )
            for r in results
        ]
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO prediction_ledger (model, season, subject, subject_id, predicted, predicted_at)
               VALUES %s;""",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {model}: logged {len(rows)} real candidates (top pick: {results[0]['player_name']} @ {results[0][prob_field]:.3f})")

    conn.close()
    print(f"\n✅ Snapshot complete for season {season}: {total_rows} rows logged at {now.isoformat()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=None, help="Season end-year to snapshot (defaults to the current NBA season).")
    args = parser.parse_args()

    season = args.season or get_current_nba_season()
    print(f"Snapshotting real live predictions for season {season}...")
    snapshot(season)


if __name__ == "__main__":
    main()
