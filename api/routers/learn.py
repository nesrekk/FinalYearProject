from functools import lru_cache

from fastapi import APIRouter

import shots_lib
from source_badge import make_source
from impact_core import get_db

router = APIRouter()

ZONE_POINTS = {
    "Restricted Area": 2,
    "In The Paint (Non-RA)": 2,
    "Mid-Range": 2,
    "Corner 3": 3,
    "Above the Break 3": 3,
}


@lru_cache(maxsize=2)
def _basics():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT max(season) FROM player_shots")
        season_label = cur.fetchone()[0]
        season = int(season_label[:4]) + 1

        cur.execute(
            """SELECT loc_x, loc_y, shot_distance, shot_type, shot_zone_basic, shot_made_flag
               FROM player_shots WHERE season = %s AND game_id LIKE '002%%'""",
            (season_label,),
        )
        agg = {z: [0, 0] for z in shots_lib.ZONES}
        for x, y, dist, stype, zbasic, made in cur.fetchall():
            zone = shots_lib.classify_zone(x, y, dist, stype, zbasic)
            if zone in agg:
                agg[zone][0] += made
                agg[zone][1] += 1

        cur.execute(
            """SELECT max(abs(our_fga - nba_fga)::float / nba_fga),
                      max(abs(our_fg_pct - nba_fg_pct)), string_agg(DISTINCT season, ', ')
               FROM zone_classifier_check"""
        )
        fga_err, pct_err, check_seasons = cur.fetchone()

        cur.execute(
            "SELECT avg(poss_est), count(DISTINCT game_id) FROM game_team_box WHERE season = %s",
            (season,),
        )
        pace, n_games = cur.fetchone()

        cur.execute(
            "SELECT sum(pts * gp), count(*) FROM player_season_stats WHERE season = %s",
            (season,),
        )
        total_pts, n_players = cur.fetchone()

        cur.execute(
            """SELECT player_name, team_abbreviation, gp, pts, reb, ast, ts_pct, fg3_pct
               FROM player_season_stats WHERE season = %s AND gp >= 50
               ORDER BY pts DESC LIMIT 1""",
            (season,),
        )
        top = cur.fetchone()

    n_shots = sum(v[1] for v in agg.values())
    zones = []
    for zone in shots_lib.ZONES:
        fgm, fga = agg[zone]
        fg_pct = fgm / fga if fga else None
        zones.append({
            "zone": zone,
            "fga": fga,
            "fgm": fgm,
            "fg_pct": round(fg_pct, 4) if fg_pct is not None else None,
            "share_of_shots": round(fga / n_shots, 4) if n_shots else None,
            "points": ZONE_POINTS[zone],
            "points_per_shot": round(fg_pct * ZONE_POINTS[zone], 3) if fg_pct is not None else None,
        })
    threes = sum(z["fga"] for z in zones if z["points"] == 3)
    team_games = (n_games or 0) * 2

    return {
        "season": season,
        "season_label": season_label,
        "n_shots": n_shots,
        "zones": zones,
        "three_share": round(threes / n_shots, 4) if n_shots else None,
        "zone_classifier_check": {
            "seasons": check_seasons,
            "max_fga_error": round(float(fga_err), 4) if fga_err is not None else None,
            "max_fg_pct_error": round(float(pct_err), 4) if pct_err is not None else None,
        },
        "pace": {
            "possessions_per_team_game": round(float(pace), 1) if pace is not None else None,
            "n_games": n_games,
        },
        "scoring": {
            # Per-game averages are stored rounded to 0.1, so this total is approximate.
            "points_per_team_game": round(float(total_pts) / team_games, 1) if total_pts and team_games else None,
            "n_players": n_players,
        },
        "example_player": None if top is None else {
            "player_name": top[0],
            "team": top[1],
            "gp": top[2],
            "pts": float(top[3]),
            "reb": float(top[4]),
            "ast": float(top[5]),
            "ts_pct": float(top[6]) if top[6] is not None else None,
            "fg3_pct": float(top[7]) if top[7] is not None else None,
        },
        "_source": make_source(
            ["player_shots", "zone_classifier_check", "game_team_box", "player_season_stats"],
            "stats.nba.com (shotchartdetail, box scores, league player stats), loaded into Postgres",
        ),
    }


@router.get("/learn/basics")
def get_learn_basics():
    """Real league numbers for the Learn the Game page: latest-season shot
    zones (via shots_lib.classify_zone, checked against the NBA's own zone
    totals in zone_classifier_check), pace, scoring and a real stat line."""
    return _basics()
