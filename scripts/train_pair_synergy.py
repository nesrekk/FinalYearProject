"""
train_pair_synergy.py
=======================
RETIRED FROM THE APP (2026-10-06, round 8.5 step B, R8-032; the owner's call): no route
or page reads this model any more (Player Comparison points to Pair Chemistry, which shows
what pairs actually did from the play-by-play stints). It was fitted on the old in-house
defensive BPM (dbpm_repro) and its season-grouped CV R² was 0.015. The script stays only
because it produces pair_synergy_validation, which is in the paper manifest (dropping the
table would change the manifest digest); its .pkl outputs are no longer committed.

Trains a real ridge regression model predicting a real 2-man lineup
pair's "synergy": their real observed net rating (nba_api's
LeagueDashLineups, group_quantity=2) minus the minutes-weighted average
of each player's own real individual on-court net rating for that
season (player_season_stats.net_rating — the real net rating across
all of that player's own real minutes, not a fabricated baseline). A
positive synergy score means the real pair outperformed what their two
individual real net ratings alone would predict; negative means they
underperformed. This is the honest, real-data replacement for guessing
at "chemistry" — every input is a real number that actually happened.

Real features per player: their real statistical archetype
(player_clusters, one-hot) and z-scored usage%, 3PA rate (3PA/FGA),
AST%, REB%, and DBPM — z-scored within their own real season's pool,
the same era-normalization approach used everywhere else in this
project. The two players in a pair are ordered by player_id (smaller
first) so the model sees a consistent, symmetric representation of
"this pair" regardless of query order.

Validated with GroupKFold cross-validation grouped by SEASON (not by
row) — no real season's pairs leak into a fold meant to test on a
season the model has never seen. The real cross-validated R² is
reported and stored even if it's low, matching this project's standing
rule to disclose validation honestly rather than hide a weak result.

Real pair/season data is live-fetched from nba_api (LeagueDashLineups,
one real call per real season, ~17 calls) rather than bulk pre-stored,
since it's only needed once per training run.

Usage:
    cd scripts && python3 train_pair_synergy.py
"""

import pickle
import time
from datetime import datetime, timezone

import numpy as np
import psycopg2
import psycopg2.extras
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold, cross_val_score
from sklearn.preprocessing import StandardScaler

from db_config import DB_CONFIG

SEASON_START = 2010
SEASON_END = 2026
MIN_PAIR_MINUTES = 200
ARCHETYPES = [
    "3-and-D Wing", "Bench Role Player", "Elite Two-Way Big",
    "Playmaker", "Primary Scorer", "Rim Protector",
]
# "dbpm" is this project's BPM reproduction (dbpm_repro), which the saved model
# was trained on; switching to the published dbpm means retraining (needs
# stats.nba.com for the pair data) and changing impact_core in the same step.
NUMERIC_FEATURES = ["usg_pct", "tpar", "ast_pct", "reb_pct", "dbpm"]
RIDGE_ALPHA = 5.0


def fetch_pairs_for_season(season: int):
    from nba_api.stats.endpoints import leaguedashlineups

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = leaguedashlineups.LeagueDashLineups(
        group_quantity=2,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="Totals",
        season=season_label,
        season_type_all_star="Regular Season",
        timeout=45,
    )
    df = endpoint.get_data_frames()[0]
    pairs = []
    for _, row in df.iterrows():
        player_ids = sorted(int(pid) for pid in str(row["GROUP_ID"]).split("-") if pid)
        if len(player_ids) != 2:
            continue
        pairs.append({
            "player_a": player_ids[0], "player_b": player_ids[1],
            "min": float(row["MIN"]), "net_rating": float(row["NET_RATING"]),
        })
    return pairs


def load_player_features(cursor, season: int):
    """Real per-player feature vector for one real season: z-scored
    within that season's real pool, plus a one-hot real archetype."""
    cursor.execute(
        """SELECT p.player_id, p.net_rating, p.min, p.gp, p.usg_pct, p.fg3a, p.fga,
                  p.ast_pct, p.reb_pct, p.dbpm_repro AS dbpm, c.archetype
           FROM player_season_stats p
           LEFT JOIN player_clusters c ON c.player_id = p.player_id AND c.season = p.season
           WHERE p.season = %s;""",
        (season,),
    )
    raw = {}
    for pid, net, mn, gp, usg, fg3a, fga, ast, reb, dbpm, archetype in cursor.fetchall():
        raw[pid] = {
            "net_rating": net, "min": (mn * gp) if (mn is not None and gp) else None,
            "usg_pct": usg, "tpar": (fg3a / fga) if fga else None,
            "ast_pct": ast, "reb_pct": reb, "dbpm": dbpm, "archetype": archetype,
        }

    means, stds = {}, {}
    for k in NUMERIC_FEATURES:
        vals = [v[k] for v in raw.values() if v[k] is not None]
        if not vals:
            continue
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        means[k], stds[k] = m, sd

    features = {}
    for pid, v in raw.items():
        if v["archetype"] is None or v["net_rating"] is None or v["min"] is None:
            continue
        if any(v[k] is None for k in NUMERIC_FEATURES):
            continue
        z = [(v[k] - means[k]) / stds[k] for k in NUMERIC_FEATURES]
        onehot = [1.0 if v["archetype"] == a else 0.0 for a in ARCHETYPES]
        features[pid] = {"vec": z + onehot, "net_rating": v["net_rating"], "min": v["min"]}
    return features


def build_dataset():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    X, y, groups = [], [], []
    for season in range(SEASON_START, SEASON_END + 1):
        try:
            pairs = fetch_pairs_for_season(season)
        except Exception as exc:
            print(f"  {season}: FAILED to fetch — {exc}")
            time.sleep(1.0)
            continue

        player_features = load_player_features(cursor, season)

        season_rows = 0
        for p in pairs:
            if p["min"] < MIN_PAIR_MINUTES:
                continue
            fa = player_features.get(p["player_a"])
            fb = player_features.get(p["player_b"])
            if not fa or not fb:
                continue
            expected = (fa["net_rating"] * fa["min"] + fb["net_rating"] * fb["min"]) / (fa["min"] + fb["min"])
            synergy = p["net_rating"] - expected
            X.append(fa["vec"] + fb["vec"])
            y.append(synergy)
            groups.append(season)
            season_rows += 1

        print(f"  {season}: {season_rows} real qualifying pairs (>= {MIN_PAIR_MINUTES} real shared minutes)")
        time.sleep(0.6)

    conn.close()
    return np.array(X), np.array(y), np.array(groups)


def save_validation(n_pairs, n_seasons, cv_r2, cv_scores):
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pair_synergy_validation (
            id SERIAL PRIMARY KEY,
            computed_at TIMESTAMPTZ NOT NULL,
            n_pairs INT,
            n_seasons INT,
            min_pair_minutes INT,
            ridge_alpha DOUBLE PRECISION,
            cv_r2_mean DOUBLE PRECISION,
            cv_r2_scores JSONB
        );
    """)
    cursor.execute("TRUNCATE TABLE pair_synergy_validation;")
    cursor.execute(
        """INSERT INTO pair_synergy_validation
           (computed_at, n_pairs, n_seasons, min_pair_minutes, ridge_alpha, cv_r2_mean, cv_r2_scores)
           VALUES (%s, %s, %s, %s, %s, %s, %s);""",
        (
            datetime.now(timezone.utc), n_pairs, n_seasons, MIN_PAIR_MINUTES, RIDGE_ALPHA,
            float(cv_r2), psycopg2.extras.Json([float(s) for s in cv_scores]),
        ),
    )
    conn.commit()
    conn.close()


def main():
    print("Building real pair-synergy training set (live nba_api fetch, one call per real season)...")
    X, y, groups = build_dataset()
    n_pairs = len(y)
    n_seasons = len(set(groups))
    print(f"\nTotal real qualifying pairs: {n_pairs} across {n_seasons} real seasons")

    if n_pairs < 100:
        print("Not enough real pairs to train a meaningful model. Aborting.")
        return

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    n_splits = min(5, n_seasons)
    cv = GroupKFold(n_splits=n_splits)
    model = Ridge(alpha=RIDGE_ALPHA)
    cv_scores = cross_val_score(model, X_scaled, y, groups=groups, cv=cv, scoring="r2")
    cv_r2 = cv_scores.mean()
    print(f"Real {n_splits}-fold season-grouped cross-validated R²: {cv_r2:.4f} (per-fold: {[round(s, 3) for s in cv_scores]})")
    print("Reported honestly — a low or even negative R² here is a real result, not a bug, if pair synergy")
    print("genuinely isn't well predicted by these real features alone.")

    # Fit the final model on all real data for deployment.
    model.fit(X_scaled, y)

    with open("pair_synergy_model.pkl", "wb") as f:
        pickle.dump(model, f)
    with open("pair_synergy_scaler.pkl", "wb") as f:
        pickle.dump(scaler, f)
    print("\n✅ Saved pair_synergy_model.pkl, pair_synergy_scaler.pkl")

    save_validation(n_pairs, n_seasons, cv_r2, cv_scores)
    print("✅ Saved real validation results to pair_synergy_validation")


if __name__ == "__main__":
    main()
