from typing import Optional

from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
)

router = APIRouter()

MIN_LINEUP_POSS = 100
ALPHA = 0.05

METHODOLOGY = (
    "Gravity is a disclosed composite proxy for shooting gravity, built from real NBA tracking splits — not "
    "player-tracking gravity itself (that needs raw defender coordinates this project doesn't have). Three real "
    "components, each z-scored within the season against players with 500+ minutes, summed: 3PA per 100 possessions; "
    "catch-and-shoot 3P%; and the share of 3PA taken with a defender within 6 ft (defenders staying attached to a "
    "shooter). The last two are shrunk with a 250-attempt prior toward the real rate of players with similar 3-point "
    "volume that season (not the league-wide rate, which would rate non-shooters as average). Lineup spacing = the sum "
    "of the five players' Gravity. Validation: across real 5-man lineups with 100+ possessions, real offensive rating "
    "is regressed on lineup spacing plus the five players' summed OBPM with season fixed effects, weighted by "
    "possessions, with standard errors clustered by team-season (lineups on one team share players)."
)


def _require(cursor):
    cursor.execute("SELECT to_regclass('public.player_gravity');")
    if cursor.fetchone()[0] is None:
        raise HTTPException(status_code=503, detail="Gravity data hasn't been built yet — run scripts/fetch_spacing_data.py "
                                                    "then scripts/build_gravity_index.py.")


def _season(cursor, season):
    cursor.execute("SELECT DISTINCT season FROM player_gravity WHERE gravity IS NOT NULL ORDER BY season;")
    seasons = [r[0] for r in cursor.fetchall()]
    if season is None:
        season = seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No real tracking data for season {season} (starts 2013-14).")
    return season, seasons


def _validation(cursor):
    cursor.execute(
        """SELECT n_lineups, n_clusters, n_lineups_dropped, season_min, season_max, coef_spacing, se_spacing,
                  ci_low, ci_high, p_spacing, coef_obpm, p_obpm, r2, r2_without_spacing
           FROM gravity_validation WHERE id = 1;""")
    v = cursor.fetchone()
    return {
        "n_lineups": v[0], "n_clusters": v[1], "n_lineups_dropped": v[2], "season_min": v[3], "season_max": v[4],
        "coef_spacing": v[5], "se_spacing": v[6], "ci_low": v[7], "ci_high": v[8], "p_spacing": v[9],
        "coef_obpm": v[10], "p_obpm": v[11], "r2": v[12], "r2_without_spacing": v[13],
        "significant": v[9] < ALPHA,
        "min_lineup_poss": MIN_LINEUP_POSS,
    }


def _season_lineup_spacing(cursor, season):
    """Real spacing of every real 100+-possession lineup this season."""
    cursor.execute(
        """SELECT l.group_id, SUM(g.gravity), COUNT(g.gravity)
           FROM lineup_stats l
           CROSS JOIN LATERAL unnest(l.player_ids) AS pid
           LEFT JOIN player_gravity g ON g.season = l.season AND g.player_id = pid
           WHERE l.season = %s AND l.poss >= %s
           GROUP BY l.group_id;""",
        (season, MIN_LINEUP_POSS),
    )
    return sorted(r[1] for r in cursor.fetchall() if r[2] == 5)


@router.get("/spacing/gravity")
def get_gravity(season: Optional[int] = None, top_n: int = 25):
    top_n = max(1, min(top_n, 100))
    with get_db() as conn:
        cursor = conn.cursor()
        _require(cursor)
        season, seasons = _season(cursor, season)
        cursor.execute(
            """SELECT player_id, player_name, team_abbreviation, minutes, fg3a_total, three_rate, cs_fg3m, cs_fg3a,
                      cs_pct_raw, cs_pct_shrunk, def_fg3a, contested_fg3a, contested_share_shrunk,
                      z_three_rate, z_cs_pct, z_contested, gravity, obpm_used
               FROM player_gravity WHERE season = %s AND in_pool AND gravity IS NOT NULL
               ORDER BY gravity DESC;""",
            (season,),
        )
        keys = ["player_id", "player_name", "team_abbreviation", "minutes", "fg3a_total", "three_rate", "cs_fg3m",
                "cs_fg3a", "cs_pct_raw", "cs_pct_shrunk", "def_fg3a", "contested_fg3a", "contested_share_shrunk",
                "z_three_rate", "z_cs_pct", "z_contested", "gravity", "obpm_used"]
        pool = [dict(zip(keys, r)) for r in cursor.fetchall()]
        n_pool = len(pool)
        for i, p in enumerate(pool):
            p["rank"] = i + 1
            p["percentile"] = round(100 * (n_pool - i) / n_pool, 1)
        cursor.execute("SELECT tracked_fg3a, boxscore_fg3a FROM gravity_tracking_coverage WHERE season = %s;", (season,))
        cov = cursor.fetchone()
        spacing = _season_lineup_spacing(cursor, season)
        validation = _validation(cursor)

    return {
        "season": season,
        "seasons_available": seasons,
        "methodology": METHODOLOGY,
        "validation": validation,
        "tracking_coverage": {"tracked_fg3a": cov[0], "boxscore_fg3a": cov[1],
                              "share": cov[0] / cov[1] if cov and cov[1] else None},
        "n_pool": n_pool,
        "leaderboard": pool[:top_n],
        "players": [{"player_id": p["player_id"], "player_name": p["player_name"],
                     "team_abbreviation": p["team_abbreviation"], "gravity": p["gravity"], "percentile": p["percentile"]}
                    for p in sorted(pool, key=lambda p: p["player_name"])],
        "season_lineup_spacing": {"n": len(spacing), "min": spacing[0] if spacing else None,
                                  "median": spacing[len(spacing) // 2] if spacing else None,
                                  "max": spacing[-1] if spacing else None},
        "_source": make_source(["player_gravity", "gravity_validation", "player_shot_tracking", "lineup_stats",
                                "player_season_stats"], "nba_api (LeagueDashPlayerPtShot, LeagueDashLineups)"),
    }


def lineup_report(cursor, season, ids):
    """Spacing of one five-man lineup vs. every real 100+-possession lineup
    that season. Shared by /spacing/lineup and /trade/impact. Raises
    HTTPException (404) when a player has no real Gravity that season."""
    cursor.execute(
        """SELECT player_id, player_name, team_abbreviation, gravity, in_pool
           FROM player_gravity WHERE season = %s AND player_id = ANY(%s);""",
        (season, ids),
    )
    found = {r[0]: r for r in cursor.fetchall()}
    missing = [i for i in ids if i not in found or found[i][3] is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"No real Gravity on file for player id(s) {missing} in that season.")
    spacing = float(sum(found[i][3] for i in ids))
    dist = _season_lineup_spacing(cursor, season)
    validation = _validation(cursor)
    cursor.execute(
        """SELECT poss, off_rating, group_name FROM lineup_stats
           WHERE season = %s AND player_ids @> %s::bigint[] AND cardinality(player_ids) = 5;""",
        (season, ids),
    )
    real = cursor.fetchone()

    below = sum(1 for s in dist if s < spacing)
    percentile = round(100 * below / len(dist), 1) if dist else None
    median = dist[len(dist) // 2] if dist else None

    in_range = bool(dist) and dist[0] <= spacing <= dist[-1]
    predicted = None
    no_effect_message = None
    if not validation["significant"]:
        no_effect_message = "Not enough evidence to estimate an effect."
    elif not in_range:
        no_effect_message = (
            f"This lineup's spacing ({spacing:+.2f}) is outside the range of every real lineup with {MIN_LINEUP_POSS}+ "
            f"possessions this season ({dist[0]:+.2f} to {dist[-1]:+.2f}), so no prediction is shown — "
            "extending the real relationship past real data would be a guess."
        ) if dist else "No real lineups this season to compare against."
    elif median is not None:
        delta = spacing - median
        lo, hi = sorted([delta * validation["ci_low"], delta * validation["ci_high"]])
        predicted = {
            "vs_median_lineup": delta * validation["coef_spacing"],
            "ci_low": lo, "ci_high": hi,
            "spacing_delta": delta,
            "note": ("Change in offensive rating (points per 100 possessions) vs. a median-spacing real lineup this "
                     "season, holding the five players' summed OBPM fixed — the real regression coefficient times the "
                     "spacing difference, with its 95% interval. An association across real lineups, not a guarantee "
                     "for any one lineup."),
        }

    return {
        "season": season,
        "players": [{"player_id": i, "player_name": found[i][1], "team_abbreviation": found[i][2],
                     "gravity": found[i][3], "in_pool": found[i][4]} for i in ids],
        "spacing": spacing,
        "percentile_vs_real_lineups": percentile,
        "n_real_lineups": len(dist),
        "median_real_spacing": median,
        "predicted_ortg_change": predicted,
        "within_real_range": in_range,
        "real_range": [dist[0], dist[-1]] if dist else None,
        "no_effect_message": no_effect_message,
        "real_lineup": {"poss": real[0], "off_rating": real[1], "group_name": real[2]} if real else None,
        "validation": validation,
    }


@router.get("/spacing/lineup")
def get_lineup_spacing(player_ids: str, season: Optional[int] = None):
    try:
        ids = [int(x) for x in player_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="player_ids must be 5 comma-separated player ids.")
    if len(ids) != 5 or len(set(ids)) != 5:
        raise HTTPException(status_code=400, detail="Pick exactly 5 different players.")

    with get_db() as conn:
        cursor = conn.cursor()
        _require(cursor)
        season, _ = _season(cursor, season)
        report = lineup_report(cursor, season, ids)

    report["_source"] = make_source(["player_gravity", "lineup_stats", "gravity_validation"],
                                    "nba_api (LeagueDashPlayerPtShot, LeagueDashLineups)")
    return report
