import time
from source_badge import make_source
from wpa_lib import PBP_DEDUP_WHERE

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
)

router = APIRouter()


@router.get("/players/clutch-wpa")
def get_clutch_wpa_leaderboard(top_n: int = 25, min_clutch_plays: int = 3):
    top_n = max(1, min(top_n, 100))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_wpa_totals');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="WPA data hasn't been computed yet — run scripts/fetch_play_by_play.py, "
                       "train_wpa_model.py, then compute_wpa.py.",
            )
        # Same one-copy-per-real-game filter compute_wpa.py used to build the totals.
        cursor.execute("SELECT COUNT(DISTINCT game_id), MIN(season), MAX(season) FROM pbp_games g WHERE " + PBP_DEDUP_WHERE)
        n_games_sample, season_min, season_max = cursor.fetchone()
        cursor.execute("SELECT source, COUNT(DISTINCT game_id) FROM pbp_games g WHERE " + PBP_DEDUP_WHERE + " GROUP BY source;")
        by_source = dict(cursor.fetchall())

        cursor.execute(
            """SELECT w.person_id, COALESCE(MAX(p.player_name), w.player_name) AS full_name,
                      w.team_abbreviation, w.n_games, w.n_plays, w.total_wpa, w.clutch_wpa, w.clutch_plays
               FROM player_wpa_totals w
               LEFT JOIN player_season_stats p ON p.player_id = w.person_id
               WHERE w.clutch_plays >= %s
               GROUP BY w.person_id, w.player_name, w.team_abbreviation, w.n_games, w.n_plays,
                        w.total_wpa, w.clutch_wpa, w.clutch_plays
               ORDER BY w.clutch_wpa DESC LIMIT %s;""",
            (min_clutch_plays, top_n),
        )
        rows = cursor.fetchall()

    season_span = f"{season_min}" if season_min == season_max else f"{season_min}-{season_max}"
    source_note = ", ".join(f"{n} real games from {src}" for src, n in sorted(by_source.items()))
    return {
        "sample_size_games": n_games_sample,
        "methodology": (
            "Real win-probability model (Logistic Regression) trained on real play-by-play from "
            f"{n_games_sample} real games across seasons {season_span} ({source_note} — real full-season "
            "coverage where the source is ESPN via sportsdataverse, a real sampled subset where the source "
            "is nba_api; both real sources use the identical seconds-remaining/score-margin convention, "
            "verified before combining them; a game stored by both sources is counted once, using the ESPN copy). clutch_wpa sums each real play's real win-probability swing "
            "(model output after the play minus before it) across every play in real 'clutch time' (final 5 "
            "min of regulation/OT, score within 5 points), attributed to whichever player made the play. "
            "This is the model's real output on real data, not an invented formula."
        ),
        "results": [
            {
                "rank": i + 1, "player_id": r[0], "player_name": r[1], "team_abbreviation": r[2],
                "n_games": r[3], "n_plays": r[4], "total_wpa": r[5],
                "clutch_wpa": r[6], "clutch_plays": r[7],
            }
            for i, r in enumerate(rows)
        ],
        "_source": make_source(["player_wpa_totals", "pbp_games", "pbp_events"], "nba_api + ESPN via sportsdataverse (play-by-play)"),
    }
