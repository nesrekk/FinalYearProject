from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException

from impact_core import (
    DRAFT_MATURITY_CUTOFF,
    DRAFT_PICK_BUCKETS,
    _pick_bucket_label,
    get_db,
)

router = APIRouter()


@router.get("/draft/value-curve")
def get_draft_value_curve():
    """
    Average career impact_score_raw by pick-range bucket, across draft
    classes mature enough to judge fairly (draft_year <= DRAFT_MATURITY_
    CUTOFF, so every included class has had several seasons to accumulate
    value) — a 'draft value chart' grounded in actual career outcomes
    rather than a scout's opinion of pick worth.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.overall_pick,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_type = 'Draft' AND d.overall_pick IS NOT NULL
              AND d.draft_year <= %s
            GROUP BY d.player_id, d.overall_pick;
            """,
            (DRAFT_MATURITY_CUTOFF,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No draft data loaded yet. Run scripts/fetch_draft_history.py first.",
        )

    buckets = {label: [] for _, _, label in DRAFT_PICK_BUCKETS}
    for overall_pick, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        if label in buckets:
            buckets[label].append(float(career_impact))

    return {
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "note": "Only includes draft classes through the cutoff year so every "
                "player has had a fair number of seasons to accumulate career value.",
        "buckets": [
            {
                "range": label,
                "n_players": len(values),
                "avg_career_impact_raw": round(sum(values) / len(values), 3) if values else None,
            }
            for _, _, label in DRAFT_PICK_BUCKETS
            for values in [buckets[label]]
        ],
    }

@router.get("/draft/best-value")
def get_draft_best_value(limit: int = 15, worst: bool = False):
    """
    Picks whose career impact_score_raw deviates most from their pick
    bucket's average — biggest positive deviation = best value (steals),
    biggest negative = underperformed their slot (not necessarily 'busts'
    in the pejorative sense — injuries, unlucky context, etc. aren't
    separated out here, just the statistical gap from expectation).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.player_id, d.player_name, d.draft_year, d.overall_pick, d.team_abbreviation,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_type = 'Draft' AND d.overall_pick IS NOT NULL
              AND d.draft_year <= %s
            GROUP BY d.player_id, d.player_name, d.draft_year, d.overall_pick, d.team_abbreviation;
            """,
            (DRAFT_MATURITY_CUTOFF,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No draft data loaded yet. Run scripts/fetch_draft_history.py first.",
        )

    bucket_values = {}
    for _, _, _, overall_pick, _, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        bucket_values.setdefault(label, []).append(float(career_impact))
    bucket_avg = {label: sum(v) / len(v) for label, v in bucket_values.items()}

    scored = []
    for player_id, player_name, draft_year, overall_pick, team, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        expected = bucket_avg.get(label, 0.0)
        scored.append({
            "player_id": int(player_id),
            "player_name": player_name,
            "draft_year": draft_year,
            "overall_pick": overall_pick,
            "team_abbreviation": team,
            "career_impact_raw": round(float(career_impact), 3),
            "expected_impact_raw": round(expected, 3),
            "value_over_expectation": round(float(career_impact) - expected, 3),
        })

    scored.sort(key=lambda r: r["value_over_expectation"], reverse=not worst)
    return {
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "mode": "worst" if worst else "best",
        "results": scored[:limit],
    }

@router.get("/draft/{draft_year}")
def get_draft_class(draft_year: int):
    """
    One draft class's picks with career value to date, using impact_score_raw
    (the same 'total roster impact' currency Trade Analyzer already sums) —
    not a new formula invented for this feature.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.player_id, d.player_name, d.overall_pick, d.round_number, d.round_pick,
                   d.team_abbreviation, d.organization, d.organization_type, d.rookie_season_int,
                   COUNT(p.season) AS seasons_played,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw,
                   COALESCE(AVG(p.impact_score_raw), 0) AS avg_impact_raw,
                   COUNT(*) FILTER (WHERE p.impact_score_star IS NOT NULL) AS star_seasons
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_year = %s AND d.draft_type = 'Draft'
            GROUP BY d.player_id, d.player_name, d.overall_pick, d.round_number, d.round_pick,
                     d.team_abbreviation, d.organization, d.organization_type, d.rookie_season_int
            ORDER BY d.overall_pick ASC NULLS LAST;
            """,
            (draft_year,),
        )
        rows = cursor.fetchall()

        if not rows:
            cursor.execute("SELECT MIN(draft_year), MAX(draft_year) FROM draft_history;")
            bounds = cursor.fetchone()
            available = f"{bounds[0]}–{bounds[1]}" if bounds and bounds[0] is not None else "none loaded yet — run scripts/fetch_draft_history.py"
            raise HTTPException(status_code=404, detail=f"No draft data for {draft_year}. Available: {available}.")

    return {
        "draft_year": draft_year,
        "rookie_season_int": draft_year + 1,
        "results": [
            {
                "overall_pick": r[2],
                "round_number": r[3],
                "round_pick": r[4],
                "player_id": int(r[0]),
                "player_name": r[1],
                "team_abbreviation": r[5],
                "organization": r[6],
                "organization_type": r[7],
                "seasons_played": r[9],
                "career_impact_raw": round(float(r[10]), 3),
                "avg_impact_raw": round(float(r[11]), 3),
                "star_seasons": r[12],
            }
            for r in rows
        ],
    }
