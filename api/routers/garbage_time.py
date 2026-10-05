from typing import Optional

from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
)

router = APIRouter()

BUCKETS = ["garbage", "low", "medium", "high"]

THRESHOLDS = {
    "garbage": "Pre-play win probability above 99% for either team, OR 2nd half/OT with the score margin at 18+",
    "high": "Leverage Index >= 1.5, OR NBA clutch time (last 5 min of the 4th quarter/OT, margin <= 5)",
    "low": "Leverage Index < 0.3",
    "medium": "Everything else (Leverage Index 0.3-1.5)",
    "order": "Checked in the order garbage -> high -> low -> medium; the first match wins. Garbage is checked "
             "before clutch on purpose: e.g. a 5-point lead with seconds left meets the NBA's clutch definition "
             "but the game is already >99% decided.",
    "qualified": "At least 40 real games with a recorded play",
    "padding_badge": "Garbage-time share of points at or above the 90th percentile among qualified players that season",
}

METHODOLOGY = (
    "Every real play in ESPN's full-season play-by-play (via sportsdataverse) is placed in a game state: seconds "
    "remaining (on the corrected clock, pbp_event_clock: ESPN stamps made shots a median 14 s late) and the score "
    "margin before the play. The project's own win-probability model (the same one behind "
    "Clutch WPA and Game Replay) turns that state into a win probability. The Leverage Index (LI) of a state is the "
    "expected absolute win-probability swing of the next play there, averaged over the real league-wide mix of scoring "
    "outcomes (no score, +1/+2/+3 either way), divided by the real league average so the average real play has LI = 1.0. "
    "Each real play is then bucketed (garbage / low / medium / high) and the real points, shots, rebounds, assists and "
    "turnovers it produced are credited to the real player who made it. Filtered PPG drops garbage-time and "
    "low-leverage points; True Production Ratio = filtered PPG / raw PPG; leverage-weighted PPG = sum of points x LI "
    "per game. These are descriptive splits of real production, not a judgement of intent: a bench player's minutes "
    "come mostly in blowouts because that is when coaches play them. Official NBA Cup finals are excluded (not counted in "
    "official regular-season stats), and so is the small nba_api play-by-play sample, which is almost entirely "
    "duplicate games already covered by ESPN."
)


def _require_table(cursor):
    cursor.execute("SELECT to_regclass('public.player_leverage_summary');")
    if cursor.fetchone()[0] is None:
        raise HTTPException(
            status_code=503,
            detail="Garbage-Time Deflator data hasn't been built yet — run scripts/build_leverage_splits.py.",
        )


def _resolve_season(cursor, season: Optional[int]):
    cursor.execute("SELECT DISTINCT season FROM player_leverage_summary ORDER BY season;")
    seasons = [r[0] for r in cursor.fetchall()]
    if not seasons:
        raise HTTPException(status_code=503, detail="player_leverage_summary is empty.")
    if season is None:
        season = seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No real play-by-play coverage for season {season}.")
    return season, seasons


def _validation(cursor, season: int):
    cursor.execute(
        """SELECT n_games, n_events, pts_total, pts_attributed, assists_parsed, assists_matched,
                  clutch_events, clutch_overridden, share_garbage, share_low, share_medium, share_high,
                  n_qualified, n_players_compared, ppg_r, ppg_mae, three_rule_accuracy
           FROM leverage_validation WHERE season = %s;""",
        (season,),
    )
    r = cursor.fetchone()
    if r is None:
        return None
    return {
        "n_games": r[0],
        "n_events": r[1],
        "points_attribution_rate": round(r[3] / r[2], 4) if r[2] else None,
        "assist_match_rate": round(r[5] / r[4], 4) if r[4] else None,
        "clutch_events": r[6],
        "clutch_events_reclassified_garbage": r[7],
        "event_share_by_bucket": {"garbage": r[8], "low": r[9], "medium": r[10], "high": r[11]},
        "n_qualified": r[12],
        "ppg_vs_official_n": r[13],
        "ppg_vs_official_r": r[14],
        "ppg_vs_official_mae": r[15],
        "three_pa_rule_accuracy": r[16],
        "note": None,
    }


SUMMARY_COLS = (
    "player_id, player_name, team_abbreviation, games, pts, garbage_pts, low_pts, medium_pts, high_pts, "
    "ppg_raw, ppg_ex_garbage, ppg_filtered, lw_ppg, true_production_ratio, garbage_share, high_share, "
    "qualified, garbage_share_pctile, padding_risk, official_ppg, official_gp"
)
SUMMARY_KEYS = [c.strip() for c in SUMMARY_COLS.split(",")]


def _rows(cursor):
    return [dict(zip(SUMMARY_KEYS, r)) for r in cursor.fetchall()]


@router.get("/players/garbage-time")
def get_garbage_time(season: Optional[int] = None, min_ppg: float = 0.0, top_n: int = 10):
    top_n = max(1, min(top_n, 50))
    min_ppg = max(0.0, min_ppg)
    with get_db() as conn:
        cursor = conn.cursor()
        _require_table(cursor)
        season, seasons = _resolve_season(cursor, season)

        cursor.execute(
            f"""SELECT {SUMMARY_COLS} FROM player_leverage_summary
                WHERE season = %s AND qualified ORDER BY ppg_raw DESC LIMIT 30;""",
            (season,),
        )
        top_scorers = _rows(cursor)

        cursor.execute(
            f"""SELECT {SUMMARY_COLS} FROM player_leverage_summary
                WHERE season = %s AND qualified AND ppg_raw >= %s
                ORDER BY garbage_share DESC NULLS LAST LIMIT %s;""",
            (season, min_ppg, top_n),
        )
        empty_calories = _rows(cursor)

        cursor.execute(
            f"""SELECT {SUMMARY_COLS} FROM player_leverage_summary
                WHERE season = %s AND qualified AND ppg_raw >= %s
                ORDER BY high_share DESC NULLS LAST LIMIT %s;""",
            (season, min_ppg, top_n),
        )
        clutch_heavy = _rows(cursor)

        cursor.execute(
            """SELECT player_id, player_name, team_abbreviation, games, qualified
               FROM player_leverage_summary WHERE season = %s AND pts > 0 ORDER BY player_name;""",
            (season,),
        )
        players = [
            {"player_id": r[0], "player_name": r[1], "team_abbreviation": r[2], "games": r[3], "qualified": r[4]}
            for r in cursor.fetchall()
        ]
        validation = _validation(cursor, season)

    return {
        "season": season,
        "seasons_available": seasons,
        "min_ppg": min_ppg,
        "methodology": METHODOLOGY,
        "thresholds": THRESHOLDS,
        "validation": validation,
        "top_scorers": top_scorers,
        "empty_calories": empty_calories,
        "clutch_heavy": clutch_heavy,
        "players": players,
        "_source": make_source(
            ["player_leverage_summary", "leverage_validation", "pbp_events", "pbp_games", "pbp_event_clock"],
            "ESPN play-by-play via sportsdataverse (corrected clock) + this project's WPA model",
        ),
    }


@router.get("/players/garbage-time/player/{player_id}")
def get_garbage_time_player(player_id: int, season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        _require_table(cursor)
        season, _ = _resolve_season(cursor, season)
        cursor.execute(
            f"SELECT {SUMMARY_COLS} FROM player_leverage_summary WHERE season = %s AND player_id = %s;",
            (season, player_id),
        )
        rows = _rows(cursor)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No real play-by-play production for player {player_id} in {season}.")
        summary = rows[0]
        cursor.execute(
            "SELECT COUNT(*) FROM player_leverage_summary WHERE season = %s AND qualified;", (season,)
        )
        n_qualified = cursor.fetchone()[0]
        cursor.execute(
            """SELECT bucket, pts, fgm, fga, fg3m, fg3a, ftm, fta, reb, ast, tov, lw_pts
               FROM player_leverage_splits WHERE season = %s AND player_id = %s;""",
            (season, player_id),
        )
        by_bucket = {
            r[0]: {
                "pts": r[1], "fgm": r[2], "fga": r[3], "fg3m": r[4], "fg3a": r[5], "ftm": r[6], "fta": r[7],
                "reb": r[8], "ast": r[9], "tov": r[10], "lw_pts": r[11],
                "fg_pct": round(r[2] / r[3], 4) if r[3] else None,
            }
            for r in cursor.fetchall()
        }

    empty = {k: 0 for k in ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "ast", "tov", "lw_pts"]}
    splits = [{"bucket": b, **by_bucket.get(b, {**empty, "fg_pct": None})} for b in BUCKETS]
    return {
        "season": season,
        "player": summary,
        "n_qualified_in_season": n_qualified,
        "small_sample_warning": not summary["qualified"],
        "splits": splits,
        "thresholds": THRESHOLDS,
        "_source": make_source(
            ["player_leverage_summary", "player_leverage_splits"],
            "ESPN play-by-play via sportsdataverse (corrected clock) + this project's WPA model",
        ),
    }
