import math
from typing import Optional
from psycopg2 import pool
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    BRIDGE_MIN_GAMES,
    COLLEGE_COMP_FEATURES,
    COLLEGE_MIN_GAMES,
    _college_features,
    _combine_measurement_features,
    get_db,
)

router = APIRouter()


@router.get("/prospects/comp/{player_name}")
def get_draft_prospect_comp(
    player_name: str,
    season: Optional[int] = None,
    top_n_comps: int = 5,
    include_measurements: bool = False,
):
    """Real college-season comps + their real NBA rookie outcomes for one prospect.

    include_measurements=true adds real wingspan-minus-height and real
    standing reach (from the NBA Draft Combine, z-scored within the
    prospect's own real draft-class combine pool — the same "normalize
    against the query's own real pool" approach already used for the
    7 core college stats above) to the comparison vector. Only the query
    prospect and comps that were actually measured at a real combine
    participate when this is on; everyone else is excluded rather than
    silently compared on partial data.
    """
    top_n_comps = max(1, min(top_n_comps, 10))

    with get_db() as conn:
        cursor = conn.cursor()

        if season is not None:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) = LOWER(%s) AND season = %s LIMIT 1;""",
                (player_name, season),
            )
        else:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) = LOWER(%s) ORDER BY season DESC LIMIT 1;""",
                (player_name,),
            )
        prospect_row = cursor.fetchone()
        if not prospect_row:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) LIKE LOWER(%s) ORDER BY season DESC LIMIT 1;""",
                (f"%{player_name}%",),
            )
            prospect_row = cursor.fetchone()

        if not prospect_row:
            raise HTTPException(status_code=404, detail=f"No real college season found for '{player_name}'.")

        (p_season, p_athlete_id, p_name, p_team, p_games, p_pts, p_ast,
         p_reb, p_usage, p_ts, p_net, p_porpag) = prospect_row

        if not p_games or p_games < 5:
            raise HTTPException(status_code=404, detail=f"{p_name}'s {p_season} college sample is too small (<5 games) for a real comparison.")

        cursor.execute(
            """SELECT athlete_id, name, team, games, points, assists, rebounds_total,
                      usage, ts_pct, net_rating, porpag
               FROM college_player_season_stats
               WHERE season = %s AND games >= %s;""",
            (p_season, COLLEGE_MIN_GAMES),
        )
        pool_rows = cursor.fetchall()

        cursor.execute(
            """
            WITH rookies AS (
                SELECT player_id, player_name, MIN(season) AS rookie_season
                FROM player_season_stats GROUP BY player_id, player_name
            )
            SELECT c.athlete_id, c.name, c.season, c.team, c.games, c.points, c.assists,
                   c.rebounds_total, c.usage, c.ts_pct, c.net_rating, c.porpag,
                   r.player_id, r.rookie_season
            FROM rookies r
            JOIN college_player_season_stats c
                ON LOWER(c.name) = LOWER(r.player_name) AND c.season = r.rookie_season - 1
            WHERE c.games >= %s;
            """,
            (BRIDGE_MIN_GAMES,),
        )
        bridge_rows = cursor.fetchall()

        bridge_player_ids = list({b[12] for b in bridge_rows}) or [-1]
        cursor.execute(
            """SELECT player_id, season, pts, ts_pct, ast_pct, reb_pct, net_rating
               FROM player_season_stats WHERE player_id = ANY(%s);""",
            (bridge_player_ids,),
        )
        nba_by_id_season = {
            (pid, szn): {"pts": pts, "ts_pct": ts, "ast_pct": ast, "reb_pct": reb, "net_rating": net}
            for pid, szn, pts, ts, ast, reb, net in cursor.fetchall()
        }

        # If this prospect has since been drafted and appears in the NBA
        # data too, grab their real player_id for a real headshot — purely
        # cosmetic, doesn't affect the comparison math at all.
        cursor.execute(
            "SELECT DISTINCT player_id FROM player_season_stats WHERE LOWER(player_name) = LOWER(%s) LIMIT 1;",
            (p_name,),
        )
        prospect_nba_row = cursor.fetchone()
        prospect_nba_player_id = prospect_nba_row[0] if prospect_nba_row else None

        # Real combine measurements — draft_year lines up with college_season
        # (a player's last college season and their real draft year are the
        # same integer in this project's convention; verified against the
        # existing bridge-pool join above, which already relies on this).
        # Always fetched (not just when include_measurements=True) so the
        # prospect's own real measurements can be shown informationally
        # even when the toggle comparing on them is off.
        cursor.execute(
            """SELECT wingspan, height_wo_shoes, standing_reach, weight, max_vertical_leap
               FROM draft_combine WHERE draft_year = %s AND LOWER(player_name) = LOWER(%s) LIMIT 1;""",
            (p_season, p_name),
        )
        prospect_combine_row = cursor.fetchone()

        combine_pool_rows = []
        combine_by_name_year = {}
        if include_measurements:
            cursor.execute(
                """SELECT wingspan, height_wo_shoes, standing_reach
                   FROM draft_combine
                   WHERE draft_year = %s AND wingspan IS NOT NULL
                         AND height_wo_shoes IS NOT NULL AND standing_reach IS NOT NULL;""",
                (p_season,),
            )
            combine_pool_rows = cursor.fetchall()

            cursor.execute(
                """SELECT player_name, draft_year, wingspan, height_wo_shoes, standing_reach
                   FROM draft_combine
                   WHERE wingspan IS NOT NULL AND height_wo_shoes IS NOT NULL AND standing_reach IS NOT NULL;"""
            )
            combine_by_name_year = {
                (name.lower(), yr): (wingspan, height, reach)
                for name, yr, wingspan, height, reach in cursor.fetchall()
            }

    pool_features = []
    for athlete_id, name, team, games, points, assists, reb, usage, ts, net, porpag in pool_rows:
        f = _college_features(games, points, assists, reb, usage, ts, net, porpag)
        if f and all(f.get(k) is not None for k in COLLEGE_COMP_FEATURES):
            pool_features.append(f)

    if len(pool_features) < 10:
        raise HTTPException(status_code=404, detail=f"Not enough real college data for season {p_season} to build a comparison pool.")

    means, stds = {}, {}
    for k in COLLEGE_COMP_FEATURES:
        vals = [f[k] for f in pool_features]
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        means[k], stds[k] = m, sd

    def zvec(f):
        return [(f[k] - means[k]) / stds[k] for k in COLLEGE_COMP_FEATURES]

    MEASUREMENT_FEATURES = ["wingspan_minus_height", "standing_reach"]
    measure_means, measure_stds = {}, {}
    prospect_measure_vec = []
    if include_measurements:
        measure_pool = [
            _combine_measurement_features((w, h, r)) for w, h, r in combine_pool_rows
        ]
        measure_pool = [m for m in measure_pool if m is not None]
        if len(measure_pool) < 10:
            raise HTTPException(
                status_code=404,
                detail=f"Not enough real combine measurements for draft class {p_season} to build a comparison pool.",
            )
        for k in MEASUREMENT_FEATURES:
            vals = [m[k] for m in measure_pool]
            mm = sum(vals) / len(vals)
            sd = (sum((v - mm) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
            measure_means[k], measure_stds[k] = mm, sd

        prospect_measure = _combine_measurement_features(
            prospect_combine_row[:3] if prospect_combine_row else None
        )
        if prospect_measure is None:
            raise HTTPException(
                status_code=404,
                detail=f"No real combine measurements for {p_name} — try without include_measurements.",
            )
        prospect_measure_vec = [(prospect_measure[k] - measure_means[k]) / measure_stds[k] for k in MEASUREMENT_FEATURES]

    def measure_zvec(name, c_season):
        combine_row = combine_by_name_year.get((name.lower(), c_season))
        m = _combine_measurement_features(combine_row)
        if m is None:
            return None
        return [(m[k] - measure_means[k]) / measure_stds[k] for k in MEASUREMENT_FEATURES]

    prospect_features = _college_features(p_games, p_pts, p_ast, p_reb, p_usage, p_ts, p_net, p_porpag)
    if not prospect_features or any(prospect_features.get(k) is None for k in COLLEGE_COMP_FEATURES):
        raise HTTPException(status_code=404, detail=f"{p_name}'s {p_season} season is missing real stats needed for comparison.")
    prospect_vec = zvec(prospect_features) + prospect_measure_vec

    scored = []
    for (athlete_id, name, c_season, team, games, points, assists, reb,
         usage, ts, net, porpag, nba_pid, rookie_season) in bridge_rows:
        if athlete_id == p_athlete_id and c_season == p_season:
            continue
        f = _college_features(games, points, assists, reb, usage, ts, net, porpag)
        if not f or any(f.get(k) is None for k in COLLEGE_COMP_FEATURES):
            continue
        nba_outcome = nba_by_id_season.get((nba_pid, rookie_season))
        if not nba_outcome:
            continue
        comp_vec = zvec(f)
        if include_measurements:
            m_vec = measure_zvec(name, c_season)
            if m_vec is None:
                continue  # No real combine data for this comp — excluded, not guessed.
            comp_vec = comp_vec + m_vec
        dist = sum((a - b) ** 2 for a, b in zip(prospect_vec, comp_vec)) ** 0.5
        scored.append({
            "name": name, "college_season": c_season, "team": team, "nba_player_id": nba_pid,
            "distance": dist, "nba_rookie_season": rookie_season, "nba_outcome": nba_outcome,
        })

    scored.sort(key=lambda x: x["distance"])
    bridge_pool_size = len(scored)
    top_comps = scored[:top_n_comps]

    if not top_comps:
        raise HTTPException(status_code=404, detail="No real comps with known NBA rookie outcomes were found for this prospect.")

    weights = [1 / (1 + c["distance"]) for c in top_comps]
    projected = {}
    for stat in ["pts", "ts_pct", "ast_pct", "reb_pct", "net_rating"]:
        pairs = [(c["nba_outcome"].get(stat), w) for c, w in zip(top_comps, weights) if c["nba_outcome"].get(stat) is not None]
        projected[stat] = round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 3) if pairs else None

    combine_measurements = None
    if prospect_combine_row:
        c_wingspan, c_height, c_reach, c_weight, c_vertical = prospect_combine_row
        combine_measurements = {
            "wingspan": c_wingspan, "height_wo_shoes": c_height, "standing_reach": c_reach,
            "weight": c_weight, "max_vertical_leap": c_vertical,
        }

    return {
        "prospect": {
            "name": p_name, "college_season": p_season, "team": p_team, "games": p_games,
            "nba_player_id": prospect_nba_player_id,
            "ppg": round(prospect_features["ppg"], 1), "apg": round(prospect_features["apg"], 1),
            "rpg": round(prospect_features["rpg"], 1), "usage": prospect_features["usage"],
            "ts_pct": prospect_features["ts_pct"], "net_rating": prospect_features["net_rating"],
            "combine_measurements": combine_measurements,
        },
        "comps": [
            {
                "name": c["name"], "college_season": c["college_season"], "team": c["team"],
                "nba_player_id": c["nba_player_id"],
                "similarity": round(1 / (1 + c["distance"]), 4),
                "nba_rookie_season": c["nba_rookie_season"],
                "nba_rookie_outcome": {k: (round(v, 3) if v is not None else None) for k, v in c["nba_outcome"].items()},
            }
            for c in top_comps
        ],
        "projected_nba_rookie_outcome": projected,
        "bridge_pool_size": bridge_pool_size,
        "measurements_included": include_measurements,
        "measurements_note": (
            "Comps are also matched on real wingspan-minus-height and real standing reach from the NBA "
            "Draft Combine, z-scored within this prospect's own real draft-class combine pool. Only "
            "the query prospect and comps who were actually measured at a real combine participate — "
            "not every drafted player attends, so this narrows the comp pool to real combine attendees."
            if include_measurements else
            "Comparison is on real college stats only. Add include_measurements=true to also match on "
            "real wingspan/standing reach from the NBA Draft Combine (narrows to real combine attendees only)."
        ),
        "_source": make_source(
            ["college_player_season_stats", "draft_combine", "player_season_stats"],
            "CollegeBasketballData.com + nba_api (stats.nba.com)",
        ),
    }
