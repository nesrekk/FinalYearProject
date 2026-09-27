from fastapi import APIRouter, HTTPException

from impact_core import (
    DRAFT_FIRST_CLASS,
    DRAFT_MATURITY_CUTOFF,
    DRAFT_PICK_BUCKETS,
    _pick_bucket_label,
    get_db,
)
from source_badge import make_source

router = APIRouter()

NOT_LOADED = ("No draft data loaded yet. Run scripts/load_draft_history_bref.py "
              "(builds draft_history and draft_pick_outcomes from Basketball-Reference).")

SOURCE = make_source(
    ["draft_history", "draft_pick_outcomes"],
    "Basketball-Reference draft history, Win Shares and All-Star selections (Kaggle export), "
    "matched to NBA player ids",
)


def _mature_picks(cursor):
    cursor.execute(
        """
        SELECT d.player_id, d.player_name, d.draft_year, d.overall_pick, d.team_abbreviation,
               d.nba_id_status, o.ws_first5
        FROM draft_history d
        JOIN draft_pick_outcomes o USING (player_id, draft_year)
        WHERE d.draft_type = 'Draft' AND d.overall_pick IS NOT NULL
          AND d.draft_year BETWEEN %s AND %s;
        """,
        (DRAFT_FIRST_CLASS, DRAFT_MATURITY_CUTOFF),
    )
    rows = cursor.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail=NOT_LOADED)
    return rows


@router.get("/draft/value-curve")
def get_draft_value_curve():
    """
    Average Win Shares in a pick's first five NBA seasons, by pick-range
    bucket, across draft classes DRAFT_FIRST_CLASS-DRAFT_MATURITY_CUTOFF
    (every class has had five seasons). Picks who never played count as zero.
    """
    with get_db() as conn:
        rows = _mature_picks(conn.cursor())

    buckets = {label: [] for _, _, label in DRAFT_PICK_BUCKETS}
    for *_, overall_pick, _team, _status, ws5 in rows:
        label = _pick_bucket_label(overall_pick)
        if label in buckets:
            buckets[label].append(float(ws5))

    return {
        "first_draft_year": DRAFT_FIRST_CLASS,
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "metric": "Win Shares in the first five NBA seasons after the draft",
        "note": f"Draft classes {DRAFT_FIRST_CLASS}-{DRAFT_MATURITY_CUTOFF}, so every pick has had five "
                "seasons. Picks who never played in the NBA count as zero.",
        "buckets": [
            {
                "range": label,
                "n_players": len(values),
                "avg_ws_first5": round(sum(values) / len(values), 2) if values else None,
                "share_zero": round(sum(v <= 0 for v in values) / len(values), 3) if values else None,
            }
            for _, _, label in DRAFT_PICK_BUCKETS
            for values in [buckets[label]]
        ],
        "_source": SOURCE,
    }


@router.get("/draft/best-value")
def get_draft_best_value(limit: int = 15, worst: bool = False):
    """
    Picks whose first-five-season Win Shares beat (or fell furthest short of)
    their pick bucket's average. "Worst" isn't a verdict on the player:
    injuries and team context aren't separated out, only the gap from
    what that slot usually produced.
    """
    with get_db() as conn:
        rows = _mature_picks(conn.cursor())

    bucket_values = {}
    for *_, overall_pick, _team, _status, ws5 in rows:
        bucket_values.setdefault(_pick_bucket_label(overall_pick), []).append(float(ws5))
    bucket_avg = {label: sum(v) / len(v) for label, v in bucket_values.items()}

    scored = []
    for player_id, player_name, draft_year, overall_pick, team, _status, ws5 in rows:
        expected = bucket_avg.get(_pick_bucket_label(overall_pick), 0.0)
        scored.append({
            "player_id": int(player_id) if player_id > 0 else None,
            "player_name": player_name,
            "draft_year": draft_year,
            "overall_pick": overall_pick,
            "team_abbreviation": team,
            "ws_first5": round(float(ws5), 1),
            "expected_ws_first5": round(expected, 1),
            "value_over_expectation": round(float(ws5) - expected, 1),
        })

    scored.sort(key=lambda r: r["value_over_expectation"], reverse=not worst)
    return {
        "first_draft_year": DRAFT_FIRST_CLASS,
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "mode": "worst" if worst else "best",
        "results": scored[:limit],
        "_source": SOURCE,
    }


@router.get("/draft/{draft_year}")
def get_draft_class(draft_year: int):
    """One draft class with each pick's NBA outcome (Basketball-Reference)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.player_id, d.player_name, d.overall_pick, d.round_number, d.round_pick,
                   d.team_abbreviation, d.organization, d.nba_id_status,
                   o.ws_first5, o.ws_career, o.nba_seasons, o.nba_games, o.all_star_selections
            FROM draft_history d
            LEFT JOIN draft_pick_outcomes o USING (player_id, draft_year)
            WHERE d.draft_year = %s AND d.draft_type = 'Draft'
            ORDER BY d.overall_pick ASC NULLS LAST, d.round_number, d.player_name;
            """,
            (draft_year,),
        )
        rows = cursor.fetchall()

        if not rows:
            cursor.execute("SELECT MIN(draft_year), MAX(draft_year) FROM draft_history;")
            bounds = cursor.fetchone()
            available = f"{bounds[0]}–{bounds[1]}" if bounds and bounds[0] is not None else "none loaded yet"
            raise HTTPException(status_code=404, detail=f"No draft data for {draft_year}. Available: {available}.")

    return {
        "draft_year": draft_year,
        "rookie_season_int": draft_year + 1,
        "five_seasons_played": draft_year <= DRAFT_MATURITY_CUTOFF,
        "results": [
            {
                "overall_pick": r[2],
                "round_number": r[3],
                "round_pick": r[4],
                "player_id": int(r[0]) if r[0] > 0 else None,
                "player_name": r[1],
                "team_abbreviation": r[5],
                "organization": r[6],
                "nba_id_status": r[7],
                "ws_first5": None if r[8] is None else round(float(r[8]), 1),
                "ws_career": None if r[9] is None else round(float(r[9]), 1),
                "nba_seasons": r[10],
                "nba_games": r[11],
                "all_star_selections": r[12],
            }
            for r in rows
        ],
        "_source": SOURCE,
    }
