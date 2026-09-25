"""
backtest_models.py
===================
Leave-one-season-out (LOSO) backtesting for the MVP, DPOY, and ROY models —
now across multiple model types (Logistic Regression, Random Forest) so they
can be compared head-to-head, not just validated in isolation.

Why LOSO and not just re-scoring the existing models: train_mvp_model.py and
build_dpoy_roy_models.py train on ALL of 2010-2024 and predict the unseen
2025 season. That tells you nothing about historical accuracy, because
re-running the trained model against a season it was trained on is circular
(the winner's own row helped shape the model that "predicts" them, especially
with class_weight="balanced" giving that one row real numerical weight).

LOSO fixes this: for each season S, train a fresh model on the other 14
seasons only, then predict S as if it had never been seen. That's exactly
the situation the real 2025 prediction is in, so it's an honest measure of
"how often would this approach have called the winner right".

Reuses the exact feature lists / winner labels / DB config from
train_mvp_model.py and build_dpoy_roy_models.py (imported, not copied) so
this can never drift out of sync with what's actually deployed.

Usage:
    cd scripts && python3 backtest_models.py

Writes results to Postgres (model_backtest_seasons, model_backtest_summary),
one row per (award, model_type), for the API/frontend to read, and prints a
full report to the console.
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score, roc_auc_score, roc_curve
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight

warnings.filterwarnings("ignore")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import train_mvp_model as mvp_mod          # noqa: E402
import build_dpoy_roy_models as dpoy_roy_mod  # noqa: E402

DB_CONFIG = mvp_mod.DB_CONFIG
TRAIN_SEASONS = mvp_mod.TRAIN_SEASONS  # 2010..2024

# Each entry: (model_type key, display label, factory returning a fresh
# unfitted estimator, needs_sample_weight). class_weight="balanced" on
# LogReg/RF, same random_state, so the comparison isolates the algorithm
# rather than imbalance handling. GradientBoostingClassifier has no
# class_weight parameter at all, so it gets the same real balanced
# weighting applied manually via sample_weight in run_loso() instead —
# same effective imbalance handling, just a different sklearn API for it.
MODEL_CONFIGS = [
    (
        "logreg", "Logistic Regression",
        lambda: LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42, solver="lbfgs"),
        False,
    ),
    (
        "random_forest", "Random Forest",
        lambda: RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=42, n_jobs=-1),
        False,
    ),
    (
        "gradient_boosting", "Gradient Boosting",
        lambda: GradientBoostingClassifier(n_estimators=200, max_depth=3, random_state=42),
        True,
    ),
]


# ─── Data loading (mirrors the two training scripts exactly) ───────────────

def load_mvp_data():
    query = """
        SELECT
            p.player_id, p.player_name, p.season,
            p.pts, p.ts_pct, p.usg_pct, p.off_rating, p.def_rating,
            p.net_rating, p.w_pct, p.min, p.age,
            CASE WHEN m.player_id IS NOT NULL THEN 1 ELSE 0 END AS label
        FROM player_season_stats p
        LEFT JOIN mvp_winners m
            ON p.player_id = m.player_id AND p.season = m.season;
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    df = df.dropna(subset=mvp_mod.FEATURES)
    return df, mvp_mod.FEATURES


def load_award_data(features, winners, pool_column=None):
    """
    pool_column: "is_dpoy_candidate" or "is_rookie_candidate" — restricts to
    the same relevant candidate pool build_dpoy_roy_models.py trains on
    (see add_candidate_pool_flags there for why this matters: without it,
    DPOY/ROY get compared against the entire league instead of their actual
    peer group, and the backtest below will show it — that's the whole
    point of running this before and after the fix).
    """
    query = """
        SELECT player_id, player_name, season, age, gp, min,
               pts, reb, ast, stl, blk, tov,
               fg_pct, fg3_pct, ft_pct, w_pct, plus_minus,
               ts_pct, usg_pct, off_rating, def_rating,
               net_rating, ast_pct, reb_pct
        FROM player_season_stats;
    """
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()

    df["label"] = 0
    for season, winner in winners.items():
        mask = (df["season"] == season) & (df["player_name"] == winner)
        if mask.sum() == 0:
            mask = (df["season"] == season) & (
                df["player_name"].str.contains(winner.split()[-1], case=False, na=False)
            )
        df.loc[mask, "label"] = 1

    if pool_column:
        df = dpoy_roy_mod.add_candidate_pool_flags(df)
        df = df[df[pool_column]]

    df = df.dropna(subset=features)
    return df, features


# ─── Feature importance (handles both linear coefficients and tree
#     importances under one shape the API/frontend can render generically) ──

def extract_feature_importance(model, features):
    if hasattr(model, "coef_"):
        values = model.coef_[0]
        signed = True
    else:
        values = model.feature_importances_
        signed = False
    ranked = sorted(
        [{"feature": f, "value": float(v), "signed": signed} for f, v in zip(features, values)],
        key=lambda x: abs(x["value"]), reverse=True,
    )
    return ranked


def downsample_curve(fpr, tpr, max_points=60):
    """
    sklearn's roc_curve returns one point per distinct threshold, which can
    be hundreds of points for thousands of predictions — way more than an
    SVG line needs. Evenly samples down to max_points while always keeping
    the first (0,0) and last (1,1) points so the curve's endpoints are exact.
    """
    n = len(fpr)
    if n <= max_points:
        idx = range(n)
    else:
        idx = sorted(set(np.linspace(0, n - 1, max_points).astype(int).tolist()))
    return [{"fpr": float(fpr[i]), "tpr": float(tpr[i])} for i in idx]


# ─── LOSO backtest ───────────────────────────────────────────────────────────

def run_loso(df, features, award_name, model_factory, eval_seasons=None, needs_sample_weight=False):
    eval_seasons = eval_seasons if eval_seasons is not None else TRAIN_SEASONS
    per_season = []
    oof_probs = []
    oof_labels = []

    for held_out in eval_seasons:
        train = df[(df["season"] != held_out) & (df["season"].isin(eval_seasons))]
        test = df[df["season"] == held_out]

        if train["label"].sum() == 0 or len(test) == 0:
            continue

        scaler = StandardScaler()
        X_train = scaler.fit_transform(train[features].values)
        y_train = train["label"].values
        X_test = scaler.transform(test[features].values)

        model = model_factory()
        if needs_sample_weight:
            model.fit(X_train, y_train, sample_weight=compute_sample_weight("balanced", y_train))
        else:
            model.fit(X_train, y_train)
        probs = model.predict_proba(X_test)[:, 1]

        oof_probs.extend(probs.tolist())
        oof_labels.extend(test["label"].values.tolist())

        ranked = test[["player_name"]].copy()
        ranked["probability"] = probs
        # label travels along with its row through the sort below — pulling
        # it from `test` again afterwards would desync since sort_values()
        # reorders rows (a plain numpy boolean mask applies positionally,
        # not by the row's original identity).
        ranked["label"] = test["label"].values
        ranked = ranked.sort_values("probability", ascending=False).reset_index(drop=True)
        ranked["rank"] = ranked.index + 1

        winner_rows = ranked[ranked["label"] == 1]
        actual_winner_row = test[test["label"] == 1]
        actual_winner = actual_winner_row["player_name"].iloc[0] if len(actual_winner_row) else None
        predicted_rank = int(winner_rows["rank"].iloc[0]) if len(winner_rows) else None

        per_season.append({
            "season": int(held_out),
            "actual_winner": actual_winner,
            "predicted_rank": predicted_rank,
            "num_candidates": len(test),
            "top5": ranked.head(5)[["rank", "player_name", "probability"]].to_dict("records"),
        })

    # Pooled out-of-fold classification metrics (principled way to evaluate
    # under this much class imbalance — see report footer for caveats).
    oof_probs = np.array(oof_probs)
    oof_labels = np.array(oof_labels)
    oof_preds = (oof_probs >= 0.5).astype(int)

    cm = confusion_matrix(oof_labels, oof_preds)
    # NumPy 2.0 changed np.float64's __repr__ to "np.float64(0.99...)" instead of
    # just the number, which breaks psycopg2's adaptation fallback for anything
    # left as a numpy scalar — cast to plain float before it ever reaches SQL.
    roc_auc = float(roc_auc_score(oof_labels, oof_probs)) if len(set(oof_labels)) > 1 else None
    roc_curve_points = None
    if len(set(oof_labels)) > 1:
        fpr, tpr, _ = roc_curve(oof_labels, oof_probs)
        roc_curve_points = downsample_curve(fpr, tpr, max_points=60)
    precision = precision_score(oof_labels, oof_preds, zero_division=0)
    recall = recall_score(oof_labels, oof_preds, zero_division=0)
    f1 = f1_score(oof_labels, oof_preds, zero_division=0)

    ranks = [s["predicted_rank"] for s in per_season if s["predicted_rank"]]
    n = len(ranks)
    summary = {
        "award": award_name,
        "n_seasons_evaluated": len(per_season),
        "top1_accuracy": sum(1 for r in ranks if r == 1) / n if n else None,
        "top3_accuracy": sum(1 for r in ranks if r <= 3) / n if n else None,
        "top5_accuracy": sum(1 for r in ranks if r <= 5) / n if n else None,
        "mean_reciprocal_rank": sum(1 / r for r in ranks) / n if n else None,
        "roc_auc": roc_auc,
        "roc_curve": roc_curve_points,
        "precision_at_0.5": float(precision),
        "recall_at_0.5": float(recall),
        "f1_at_0.5": float(f1),
        "confusion_matrix": {"tn": int(cm[0][0]), "fp": int(cm[0][1]), "fn": int(cm[1][0]), "tp": int(cm[1][1])},
    }

    # Final model trained on ALL seasons (= what's actually deployed) purely
    # to report feature importance — not used for scoring above.
    scaler_full = StandardScaler()
    X_full = scaler_full.fit_transform(df[df["season"].isin(eval_seasons)][features].values)
    y_full = df[df["season"].isin(eval_seasons)]["label"].values
    full_model = model_factory()
    if needs_sample_weight:
        full_model.fit(X_full, y_full, sample_weight=compute_sample_weight("balanced", y_full))
    else:
        full_model.fit(X_full, y_full)
    summary["feature_importance"] = extract_feature_importance(full_model, features)

    return per_season, summary


# ─── Reporting ───────────────────────────────────────────────────────────────

def print_report(award_name, model_label, per_season, summary):
    print("\n" + "=" * 70)
    print(f"  {award_name} — {model_label} — Leave-One-Season-Out Backtest")
    print("=" * 70)
    print(f"  {'Season':<10}{'Actual Winner':<28}{'Predicted Rank':<18}{'Candidates'}")
    print(f"  {'-'*10}{'-'*28}{'-'*18}{'-'*10}")
    for s in per_season:
        rank_str = f"#{s['predicted_rank']}" if s["predicted_rank"] else "NOT FOUND"
        season_label = f"{s['season']-1}-{str(s['season'])[-2:]}"
        print(f"  {season_label:<10}{(s['actual_winner'] or '—'):<28}{rank_str:<18}{s['num_candidates']}")

    print(f"\n  Top-1 accuracy (correctly ranked #1): {fmt_pct(summary['top1_accuracy'])}")
    print(f"  Top-3 accuracy (winner in top 3):     {fmt_pct(summary['top3_accuracy'])}")
    print(f"  Top-5 accuracy (winner in top 5):     {fmt_pct(summary['top5_accuracy'])}")
    print(f"  Mean Reciprocal Rank:                 {summary['mean_reciprocal_rank']:.3f}" if summary['mean_reciprocal_rank'] else "  MRR: n/a")
    print(f"  ROC-AUC (pooled out-of-fold):          {summary['roc_auc']:.4f}" if summary["roc_auc"] else "  ROC-AUC: n/a")
    cm = summary["confusion_matrix"]
    print(f"\n  Confusion matrix @ 0.5 threshold (pooled out-of-fold, {sum(cm.values())} player-seasons):")
    print(f"                    Predicted Non-{award_name}   Predicted {award_name}")
    print(f"    Actual Non-{award_name}   {cm['tn']:>10}              {cm['fp']:>6}")
    print(f"    Actual {award_name}       {cm['fn']:>10}              {cm['tp']:>6}")
    print(f"  Precision: {summary['precision_at_0.5']:.3f}   Recall: {summary['recall_at_0.5']:.3f}   F1: {summary['f1_at_0.5']:.3f}")
    print(f"  (Caveat: with ~1 winner per ~500 candidates a season, threshold metrics")
    print(f"   are noisy on this little positive data — ROC-AUC and rank accuracy above")
    print(f"   are the more reliable signal. This is exactly why we report both.)")

    kind = "coefficients" if summary["feature_importance"] and summary["feature_importance"][0]["signed"] else "importances"
    print(f"\n  Feature {kind} (from the full-data model, |value| desc):")
    for f in summary["feature_importance"]:
        sign = "+" if (f["signed"] and f["value"] >= 0) else ("" if f["signed"] else "")
        print(f"    {f['feature']:<15} {sign}{f['value']:.4f}")


def fmt_pct(v):
    return f"{v*100:.1f}%" if v is not None else "n/a"


def print_comparison(award_name, results_by_model):
    print("\n" + "-" * 70)
    print(f"  {award_name} — Model Comparison")
    print("-" * 70)
    print(f"  {'Model':<20}{'Top-1':>8}{'Top-3':>8}{'Top-5':>8}{'MRR':>8}{'ROC-AUC':>10}")
    for model_type, label, summary in results_by_model:
        print(
            f"  {label:<20}{fmt_pct(summary['top1_accuracy']):>8}{fmt_pct(summary['top3_accuracy']):>8}"
            f"{fmt_pct(summary['top5_accuracy']):>8}{(summary['mean_reciprocal_rank'] or 0):>8.3f}"
            f"{(summary['roc_auc'] or 0):>10.4f}"
        )


# ─── Persistence ─────────────────────────────────────────────────────────────

def ensure_schema(conn):
    cur = conn.cursor()
    # Derived/reproducible data (regenerated in full every run) — simplest
    # to recreate fresh with the model_type column rather than migrate.
    cur.execute("DROP TABLE IF EXISTS model_backtest_seasons;")
    cur.execute("DROP TABLE IF EXISTS model_backtest_summary;")
    cur.execute("""
        CREATE TABLE model_backtest_seasons (
            id SERIAL PRIMARY KEY,
            award TEXT NOT NULL,
            model_type TEXT NOT NULL,
            season INTEGER NOT NULL,
            actual_winner TEXT,
            predicted_rank INTEGER,
            num_candidates INTEGER,
            top5 JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("""
        CREATE TABLE model_backtest_summary (
            award TEXT NOT NULL,
            model_type TEXT NOT NULL,
            model_label TEXT NOT NULL,
            n_seasons_evaluated INTEGER,
            top1_accuracy DOUBLE PRECISION,
            top3_accuracy DOUBLE PRECISION,
            top5_accuracy DOUBLE PRECISION,
            mean_reciprocal_rank DOUBLE PRECISION,
            roc_auc DOUBLE PRECISION,
            roc_curve JSONB,
            precision_at_0_5 DOUBLE PRECISION,
            recall_at_0_5 DOUBLE PRECISION,
            f1_at_0_5 DOUBLE PRECISION,
            confusion_matrix JSONB,
            feature_importance JSONB,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (award, model_type)
        );
    """)
    conn.commit()


def save_results(conn, award_name, model_type, model_label, per_season, summary):
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM model_backtest_seasons WHERE award = %s AND model_type = %s;",
        (award_name, model_type),
    )
    for s in per_season:
        cur.execute(
            """
            INSERT INTO model_backtest_seasons
                (award, model_type, season, actual_winner, predicted_rank, num_candidates, top5)
            VALUES (%s, %s, %s, %s, %s, %s, %s);
            """,
            (
                award_name, model_type, s["season"], s["actual_winner"], s["predicted_rank"],
                s["num_candidates"], psycopg2.extras.Json(s["top5"]),
            ),
        )
    cur.execute(
        """
        INSERT INTO model_backtest_summary
            (award, model_type, model_label, n_seasons_evaluated, top1_accuracy, top3_accuracy, top5_accuracy,
             mean_reciprocal_rank, roc_auc, roc_curve, precision_at_0_5, recall_at_0_5, f1_at_0_5,
             confusion_matrix, feature_importance, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (award, model_type) DO UPDATE SET
            model_label = EXCLUDED.model_label,
            n_seasons_evaluated = EXCLUDED.n_seasons_evaluated,
            top1_accuracy = EXCLUDED.top1_accuracy,
            top3_accuracy = EXCLUDED.top3_accuracy,
            top5_accuracy = EXCLUDED.top5_accuracy,
            mean_reciprocal_rank = EXCLUDED.mean_reciprocal_rank,
            roc_auc = EXCLUDED.roc_auc,
            roc_curve = EXCLUDED.roc_curve,
            precision_at_0_5 = EXCLUDED.precision_at_0_5,
            recall_at_0_5 = EXCLUDED.recall_at_0_5,
            f1_at_0_5 = EXCLUDED.f1_at_0_5,
            confusion_matrix = EXCLUDED.confusion_matrix,
            feature_importance = EXCLUDED.feature_importance,
            updated_at = NOW();
        """,
        (
            award_name, model_type, model_label, summary["n_seasons_evaluated"], summary["top1_accuracy"],
            summary["top3_accuracy"], summary["top5_accuracy"], summary["mean_reciprocal_rank"],
            summary["roc_auc"], psycopg2.extras.Json(summary["roc_curve"]),
            summary["precision_at_0.5"], summary["recall_at_0.5"],
            summary["f1_at_0.5"], psycopg2.extras.Json(summary["confusion_matrix"]),
            psycopg2.extras.Json(summary["feature_importance"]),
        ),
    )
    conn.commit()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("Model Backtesting — Leave-One-Season-Out validation")
    print(f"Seasons evaluated: {min(TRAIN_SEASONS)}-{max(TRAIN_SEASONS)} ({len(TRAIN_SEASONS)} seasons)")
    print(f"Models compared: {', '.join(label for _, label, _, _ in MODEL_CONFIGS)}")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_schema(conn)

    jobs = [
        ("MVP", *load_mvp_data(), TRAIN_SEASONS),
        ("DPOY", *load_award_data(dpoy_roy_mod.DPOY_FEATURES, dpoy_roy_mod.DPOY_WINNERS, pool_column="is_dpoy_candidate"), TRAIN_SEASONS),
        ("ROY", *load_award_data(dpoy_roy_mod.ROY_FEATURES, dpoy_roy_mod.ROY_WINNERS, pool_column="is_rookie_candidate"), dpoy_roy_mod.ROY_TRAIN_SEASONS),
    ]

    for award_name, df, features, eval_seasons in jobs:
        results_by_model = []
        for model_type, model_label, model_factory, needs_sample_weight in MODEL_CONFIGS:
            per_season, summary = run_loso(
                df, features, award_name, model_factory,
                eval_seasons=eval_seasons, needs_sample_weight=needs_sample_weight,
            )
            print_report(award_name, model_label, per_season, summary)
            save_results(conn, award_name, model_type, model_label, per_season, summary)
            results_by_model.append((model_type, model_label, summary))
        print_comparison(award_name, results_by_model)

    conn.close()
    print("\n" + "=" * 70)
    print("Done. Results saved to model_backtest_seasons / model_backtest_summary.")
    print("=" * 70)


if __name__ == "__main__":
    main()
