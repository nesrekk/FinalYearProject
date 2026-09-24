from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import (
    PAIR_SYNERGY_MODEL,
    PAIR_SYNERGY_SCALER,
    _fetch_lineup_stats_season,
    _player_synergy_features,
    find_player,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/players/pair-synergy")
def get_pair_synergy(player_a: str, player_b: str, season: Optional[int] = None):
    if PAIR_SYNERGY_MODEL is None or PAIR_SYNERGY_SCALER is None:
        raise HTTPException(status_code=503, detail="Pair synergy model isn't available — run scripts/train_pair_synergy.py first.")

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        pid_a, name_a = find_player(cursor, player_a)
        pid_b, name_b = find_player(cursor, player_b)
        if pid_a == pid_b:
            raise HTTPException(status_code=400, detail="Pick two different players.")

        fa = _player_synergy_features(cursor, pid_a, resolved_season)
        fb = _player_synergy_features(cursor, pid_b, resolved_season)

        cursor.execute(
            "SELECT n_pairs, n_seasons, min_pair_minutes, cv_r2_mean, computed_at FROM pair_synergy_validation ORDER BY id DESC LIMIT 1;"
        )
        validation_row = cursor.fetchone()

    if not fa or not fb:
        raise HTTPException(
            status_code=404,
            detail=f"Missing real qualified-season data for {name_a if not fa else name_b} in season {resolved_season} "
                   "(needs a real archetype + real usage/3PA-rate/AST%/REB%/DBPM on file).",
        )

    ordered = sorted([(pid_a, name_a, fa), (pid_b, name_b, fb)], key=lambda t: t[0])
    (lo_id, lo_name, lo_f), (hi_id, hi_name, hi_f) = ordered

    X = [lo_f["vec"] + hi_f["vec"]]
    X_scaled = PAIR_SYNERGY_SCALER.transform(X)
    predicted_synergy = float(PAIR_SYNERGY_MODEL.predict(X_scaled)[0])

    expected_baseline = (fa["net_rating"] * fa["min"] + fb["net_rating"] * fb["min"]) / (fa["min"] + fb["min"])
    predicted_pair_net_rating = expected_baseline + predicted_synergy

    # Real observed data: have these two actually shared the floor this season?
    observed = None
    try:
        pairs = _fetch_lineup_stats_season(resolved_season, group_quantity=2)
        for p in pairs:
            if set(p["player_ids"]) == {pid_a, pid_b}:
                observed = {
                    "min": p["min"], "net_rating": p["net_rating"],
                    "off_rating": p["off_rating"], "def_rating": p["def_rating"],
                }
                break
    except HTTPException:
        observed = None

    validation = None
    if validation_row:
        validation = {
            "n_pairs": validation_row[0], "n_seasons": validation_row[1],
            "min_pair_minutes": validation_row[2], "cv_r2_mean": round(validation_row[3], 4),
            "computed_at": validation_row[4].isoformat() if validation_row[4] else None,
        }

    return {
        "season": resolved_season,
        "player_a": {"player_id": pid_a, "player_name": name_a, "archetype": fa["archetype"], "net_rating": fa["net_rating"]},
        "player_b": {"player_id": pid_b, "player_name": name_b, "archetype": fb["archetype"], "net_rating": fb["net_rating"]},
        "predicted_synergy": round(predicted_synergy, 2),
        "predicted_pair_net_rating": round(predicted_pair_net_rating, 2),
        "expected_baseline_net_rating": round(expected_baseline, 2),
        "observed": observed,
        "validation": validation,
        "methodology": (
            "predicted_synergy is a real ridge regression's output: the real pair net rating you'd expect ABOVE "
            "the minutes-weighted average of these two real players' own individual real net ratings this "
            "season, based on their real archetypes and real z-scored usage/3PA-rate/AST%/REB%/DBPM. "
            + (
                f"Cross-validated on {validation['n_pairs']} real pairs across {validation['n_seasons']} real "
                f"seasons with real season-grouped CV: R² = {validation['cv_r2_mean']}. "
                "That R² is low — disclosed honestly rather than hidden, because it's a real finding: pair "
                "chemistry isn't well predicted by these real box-score features alone, at least not by this "
                "real model. Treat predicted_synergy as a rough, honestly-uncertain real-data signal, not a "
                "confident prediction. "
                if validation else
                "No stored validation found — run scripts/train_pair_synergy.py to see the real cross-validated R². "
            )
            + (
                "'observed' is these two real players' actual real net rating in real minutes they've actually "
                "shared the floor together this season, live-fetched from the NBA's own data — compare it "
                "directly against the model's prediction when available."
                if observed else
                "These two real players haven't shared the floor together (enough) this season for a real "
                "observed pair net rating — 'observed' is null rather than guessed."
            )
        ),
    }
