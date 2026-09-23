"""
resolve_predictions.py
========================
Grades real logged predictions (prediction_ledger, written by
snapshot_predictions.py) against real outcomes once they exist: MVP/
DPOY/ROY against award_winners, All-NBA against all_nba_seasons. Only
resolves a (model, season) once real outcome data actually exists for
it — an in-progress season's predictions are correctly left unresolved
rather than guessed at.

Score = real Brier score per row: (predicted_probability - actual)^2,
where actual is 1 if that real candidate really won/was really
selected, 0 otherwise. Lower is better; 0 is a perfect prediction.

Usage:
    cd scripts && python3 resolve_predictions.py
"""

from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SINGLE_WINNER_MODELS = {"mvp": "MVP", "dpoy": "DPOY", "roy": "ROY"}


def resolve_single_winner_model(cursor, model: str, award: str) -> int:
    cursor.execute(
        "SELECT DISTINCT season FROM prediction_ledger WHERE model = %s AND resolved_at IS NULL;",
        (model,),
    )
    unresolved_seasons = [r[0] for r in cursor.fetchall()]
    resolved_count = 0

    for season in unresolved_seasons:
        cursor.execute(
            "SELECT player_id FROM award_winners WHERE award = %s AND season = %s;",
            (award, season),
        )
        winner_row = cursor.fetchone()
        if winner_row is None:
            continue  # Real winner not recorded yet — season still in progress or not yet updated.
        winner_id = winner_row[0]

        cursor.execute(
            "SELECT id, subject_id, predicted FROM prediction_ledger WHERE model = %s AND season = %s AND resolved_at IS NULL;",
            (model, season),
        )
        rows = cursor.fetchall()
        now = datetime.now(timezone.utc)
        for row_id, subject_id, predicted in rows:
            won = subject_id == winner_id
            probability = predicted.get("probability", 0.0)
            score = (probability - (1.0 if won else 0.0)) ** 2
            cursor.execute(
                """UPDATE prediction_ledger
                   SET resolved_at = %s, actual = %s, score = %s, score_type = 'brier'
                   WHERE id = %s;""",
                (now, psycopg2.extras.Json({"won": won}), score, row_id),
            )
            resolved_count += 1
        print(f"  {model} {season}: resolved {len(rows)} rows against real winner (player_id {winner_id}).")

    return resolved_count


def resolve_all_nba(cursor) -> int:
    cursor.execute(
        "SELECT DISTINCT season FROM prediction_ledger WHERE model = 'all_nba' AND resolved_at IS NULL;"
    )
    unresolved_seasons = [r[0] for r in cursor.fetchall()]
    resolved_count = 0

    for season in unresolved_seasons:
        cursor.execute("SELECT player_id FROM all_nba_seasons WHERE season = %s;", (season,))
        real_selections = {r[0] for r in cursor.fetchall()}
        if not real_selections:
            continue  # Real All-NBA teams not recorded yet for this season.

        cursor.execute(
            "SELECT id, subject_id, predicted FROM prediction_ledger WHERE model = 'all_nba' AND season = %s AND resolved_at IS NULL;",
            (season,),
        )
        rows = cursor.fetchall()
        now = datetime.now(timezone.utc)
        for row_id, subject_id, predicted in rows:
            selected = subject_id in real_selections
            probability = predicted.get("probability", 0.0)
            score = (probability - (1.0 if selected else 0.0)) ** 2
            cursor.execute(
                """UPDATE prediction_ledger
                   SET resolved_at = %s, actual = %s, score = %s, score_type = 'brier'
                   WHERE id = %s;""",
                (now, psycopg2.extras.Json({"selected": selected}), score, row_id),
            )
            resolved_count += 1
        print(f"  all_nba {season}: resolved {len(rows)} rows against {len(real_selections)} real selections.")

    return resolved_count


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SELECT to_regclass('public.prediction_ledger');")
    if cursor.fetchone()[0] is None:
        print("No prediction_ledger table yet — run snapshot_predictions.py first.")
        conn.close()
        return

    print("Resolving real logged predictions against real outcomes...")
    total = 0
    for model, award in SINGLE_WINNER_MODELS.items():
        total += resolve_single_winner_model(cursor, model, award)
    total += resolve_all_nba(cursor)
    conn.commit()
    conn.close()

    if total == 0:
        print("Nothing to resolve — no real outcomes recorded yet for any unresolved logged season.")
    else:
        print(f"\n✅ Resolved {total} real logged predictions.")


if __name__ == "__main__":
    main()
