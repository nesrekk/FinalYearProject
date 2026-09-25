import re
from source_badge import make_source
from wpa_lib import PBP_DEDUP_WHERE
from wpa_lib import seconds_elapsed as wpa_seconds_elapsed
from wpa_lib import win_prob as wpa_win_prob

from fastapi import APIRouter, HTTPException

from impact_core import (
    WPA_MODEL,
    WPA_SCALER,
    _fetch_game_events,
    _is_missed_field_goal,
    _wpa_model_required,
    get_db,
)

router = APIRouter()


@router.get("/games/wp-replay/list")
def get_wp_replay_list(season: int = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.pbp_games');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="No play-by-play data yet — run scripts/fetch_play_by_play.py first.",
            )
        if season is None:
            cursor.execute("SELECT MAX(season) FROM pbp_games;")
            season = cursor.fetchone()[0]
        resolved_season = season
        cursor.execute(
            """
            WITH last_events AS (
                SELECT DISTINCT ON (game_id) game_id, score_home, score_away
                FROM pbp_events
                ORDER BY game_id, action_number DESC
            )
            SELECT g.game_id, g.game_date, g.home_team, g.away_team, g.home_win,
                   le.score_home, le.score_away
            FROM pbp_games g
            JOIN last_events le ON le.game_id = g.game_id
            WHERE g.season = %s AND """ + PBP_DEDUP_WHERE + """
            ORDER BY g.game_date DESC;
            """,
            (resolved_season,),
        )
        rows = cursor.fetchall()

    return {
        "season": resolved_season,
        "games": [
            {
                "game_id": r[0],
                "game_date": r[1].isoformat() if r[1] else None,
                "home_team": r[2],
                "away_team": r[3],
                "home_win": r[4],
                "final_score": {"home": r[5], "away": r[6]},
            }
            for r in rows
        ],
    }

@router.get("/games/wp-replay/{game_id}")
def get_wp_replay(game_id: str):
    _wpa_model_required()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT game_date, home_team, away_team, home_win FROM pbp_games WHERE game_id = %s;",
            (game_id,),
        )
        game_row = cursor.fetchone()
        if not game_row:
            raise HTTPException(status_code=404, detail=f"No play-by-play found for game {game_id}.")
        game_date, home_team, away_team, home_win = game_row

        events = _fetch_game_events(cursor, game_id)

    if not events:
        raise HTTPException(status_code=404, detail=f"No play-by-play events found for game {game_id}.")

    points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for event_id, action_number, period, secs, score_home, score_away, team_tricode, person_id, player_name, action_type, sub_type, description in events:
        margin = score_home - score_away
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        points.append({
            "event_id": event_id,
            "action_number": action_number,
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "period": period,
            "margin": margin,
            "home_wp": round(wp_home, 4),
            "wpa": round(wp_home - prev_wp_home, 4),
            "action_type": action_type,
            "sub_type": sub_type,
            "description": description,
            "player_name": player_name,
            "team_tricode": team_tricode,
            "is_missed_shot": _is_missed_field_goal(action_type, description),
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    top_plays = sorted(points, key=lambda p: abs(p["wpa"]), reverse=True)[:5]
    last_home_score = events[-1][4]
    last_away_score = events[-1][5]

    return {
        "game_id": game_id,
        "game_date": game_date.isoformat() if game_date else None,
        "home_team": home_team,
        "away_team": away_team,
        "home_win": home_win,
        "final_score": {"home": last_home_score, "away": last_away_score},
        "methodology": (
            "Every real play-by-play event from this real game, run through the same real trained "
            "win-probability model used by the Clutch WPA leaderboard. home_wp is the model's real "
            "output (probability the home team wins) after that play; wpa is the real swing from the "
            "previous play, from the home team's perspective."
        ),
        "points": points,
        "top_plays": top_plays,
        "_source": make_source(
            ["pbp_games", "pbp_events"],
            "ESPN via sportsdataverse" if game_id.startswith("espn_") else "nba_api (stats.nba.com)",
        ),
    }

@router.get("/games/wp-replay/{game_id}/whatif")
def get_wp_replay_whatif(game_id: str, event_id: int):
    _wpa_model_required()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT home_team, away_team FROM pbp_games WHERE game_id = %s;",
            (game_id,),
        )
        game_row = cursor.fetchone()
        if not game_row:
            raise HTTPException(status_code=404, detail=f"No play-by-play found for game {game_id}.")
        home_team, away_team = game_row

        events = _fetch_game_events(cursor, game_id)

    if not events:
        raise HTTPException(status_code=404, detail=f"No play-by-play events found for game {game_id}.")

    target_idx = next((i for i, e in enumerate(events) if e[0] == event_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail=f"No event {event_id} in game {game_id}.")

    target = events[target_idx]
    _, target_action_number, _, _, _, _, target_team, _, target_player, target_action_type, target_sub_type, target_description = target
    if not _is_missed_field_goal(target_action_type, target_description):
        raise HTTPException(
            status_code=400,
            detail="What-if is only supported for a real missed field goal.",
        )

    # "3PT" is nba_api's real description convention; ESPN's (scripts/fetch_pbp_espn.py)
    # says "three point" instead (verified live: 0 real ESPN rows contain "3PT") — both
    # checked so a real 3-point miss isn't silently scored as a 2 for ESPN-sourced games.
    _desc_lower = (target_description or "").lower()
    points_awarded = 3 if ("3pt" in _desc_lower or "three point" in _desc_lower) else 2
    shift_home = points_awarded if target_team == home_team else 0
    shift_away = points_awarded if target_team == away_team else 0

    cf_points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for i, (event_id_i, action_number_i, period, secs, score_home, score_away, *_rest) in enumerate(events):
        shifted = i >= target_idx
        margin = (score_home + (shift_home if shifted else 0)) - (score_away + (shift_away if shifted else 0))
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        cf_points.append({
            "event_id": event_id_i,
            "action_number": action_number_i,
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "home_wp": round(wp_home, 4),
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    return {
        "game_id": game_id,
        "event_id": event_id,
        "action_number": target_action_number,
        "shooter": target_player,
        "team_tricode": target_team,
        "points_awarded": points_awarded,
        "original_description": target_description,
        "counterfactual_label": f"What if this shot had gone in? (+{points_awarded} for {target_team})",
        "disclaimer": (
            "This is a counterfactual, not a re-simulation: it assumes every later play in the real game "
            "happens exactly as it really did, just with the score shifted from this shot onward. It doesn't "
            "account for how players or coaches might have actually played differently with a different score."
        ),
        "points": cf_points,
    }
