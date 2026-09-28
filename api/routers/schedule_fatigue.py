from typing import Optional
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/schedule/rest-study")
def get_rest_study(season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.team_game_fatigue');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No schedule data yet — run scripts/build_schedule_fatigue.py first.")

        params = [season] if season else []
        season_clause = "AND f.season = %s" if season else ""
        # Margins are real final scores (game_scores), not team_game_fatigue.plus_minus,
        # which is summed player +/- / 5 and wrong in 160 games.
        cursor.execute(
            f"""SELECT f.rest_days, COUNT(*) AS n,
                       AVG(CASE WHEN f.win THEN 1.0 ELSE 0 END) AS win_pct,
                       AVG(g.pts_for - g.pts_against) AS avg_point_diff
                FROM team_game_fatigue f
                LEFT JOIN game_scores g ON g.game_id = f.game_id AND g.team_abbreviation = f.team_abbreviation
                WHERE f.rest_days IS NOT NULL {season_clause}
                GROUP BY f.rest_days
                ORDER BY f.rest_days;""",
            params,
        )
        rows = cursor.fetchall()

    buckets = []
    for rest_days, n, win_pct, avg_diff in rows:
        bucket_label = "B2B (0 days rest)" if rest_days == 0 else f"{rest_days} day{'s' if rest_days != 1 else ''} rest"
        buckets.append({
            "rest_days": rest_days,
            "bucket_label": bucket_label if rest_days < 4 else "4+ days rest",
            "n": n,
            "win_pct": round(float(win_pct), 4),
            "avg_point_diff": round(float(avg_diff), 3) if avg_diff is not None else None,
        })

    # Fold 4+ day buckets together — real n gets thin past 3 days and a
    # dozen separate one-off buckets is noise, not signal.
    merged = {}
    for b in buckets:
        key = b["rest_days"] if b["rest_days"] < 4 else 4
        if key not in merged:
            merged[key] = {"rest_days": key, "bucket_label": "4+ days rest" if key == 4 else b["bucket_label"], "n": 0, "_win_sum": 0.0, "_diff_sum": 0.0}
        merged[key]["n"] += b["n"]
        merged[key]["_win_sum"] += b["win_pct"] * b["n"]
        merged[key]["_diff_sum"] += (b["avg_point_diff"] or 0) * b["n"]

    final_buckets = []
    for key in sorted(merged.keys()):
        m = merged[key]
        final_buckets.append({
            "rest_days": m["rest_days"],
            "bucket_label": m["bucket_label"],
            "n": m["n"],
            "win_pct": round(m["_win_sum"] / m["n"], 4),
            "avg_point_diff": round(m["_diff_sum"] / m["n"], 3),
        })

    return {
        "season": season,
        "buckets": final_buckets,
        "methodology": (
            "Real win% and real average point differential (that game's real final score, game_scores) by real rest-days "
            "bucket, across every real team-game with a known previous real game (team_game_fatigue). "
            "0 days rest = a real back-to-back. Real n is shown per bucket — samples get thin past 3+ days "
            "rest, folded into one '4+ days rest' bucket rather than presented as many noisy one-off buckets."
        ),
        "_source": make_source(["team_game_fatigue", "game_scores"],
                               "nba_api (stats.nba.com) schedule, ESPN scoreboard final scores"),
    }

@router.get("/schedule/difficulty")
def get_schedule_difficulty(season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        cursor.execute("SELECT to_regclass('public.team_game_fatigue');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No schedule data yet — run scripts/build_schedule_fatigue.py first.")

        cursor.execute(
            """SELECT team_abbreviation,
                      COUNT(*) AS n_games,
                      SUM(travel_miles_since_last) AS total_miles,
                      SUM(CASE WHEN is_b2b THEN 1 ELSE 0 END) AS n_b2b,
                      SUM(CASE WHEN games_last_7_days >= 4 THEN 1 ELSE 0 END) AS n_heavy_weeks
               FROM team_game_fatigue
               WHERE season = %s
               GROUP BY team_abbreviation
               ORDER BY total_miles DESC NULLS LAST;""",
            (resolved_season,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No real schedule data for season {resolved_season}.")

    results = [
        {
            "rank": i + 1,
            "team_abbreviation": r[0],
            "n_games": r[1],
            "total_travel_miles": round(r[2], 0) if r[2] is not None else None,
            "b2b_count": r[3],
            "games_with_4plus_in_7days": r[4],
        }
        for i, r in enumerate(rows)
    ]

    return {
        "season": resolved_season,
        "results": results,
        "methodology": (
            "Real total travel miles (haversine between each real consecutive game's real arena location, "
            "scripts/arenas.py), real back-to-back count, and real count of stretches with 4+ real games in "
            "a trailing 7-day window, per real team for the season — ranked by real total travel, the most "
            "direct real proxy for a grueling real schedule. Not causal — a team's real record isn't adjusted "
            "for this, it's shown as real schedule context only."
        ),
        "_source": make_source(["team_game_fatigue"], "nba_api (stats.nba.com)"),
    }
