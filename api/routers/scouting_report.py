import math
from typing import Optional

from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    find_player,
    get_db,
)

router = APIRouter()

MIN_MINUTES = 1500
ALPHA = 0.05
MAX_PER_SIDE = 3
MIN_DEF_POSS = 20
MIN_DEF_FGA = 10

PLAYTYPE_LABELS = {
    "Cut": "Cuts",
    "Handoff": "Handoffs",
    "Isolation": "Isolation",
    "OffRebound": "Putbacks",
    "OffScreen": "Off screens",
    "PRBallHandler": "Pick-and-roll ball handler",
    "PRRollman": "Pick-and-roll roll man",
    "Postup": "Post-ups",
    "Spotup": "Spot-ups",
    "Transition": "Transition",
}

CANT_TELL = [
    "Pick-and-roll coverage type (drop, blitz, switch, hedge) — no real coverage-level data is available to this project.",
    "Drive direction (left vs. right) — not in any real dataset this project uses.",
    "How closely he was guarded on each shot (closest-defender distance, touch time, dribbles) — planned for v2.",
    "Help defense, off-ball movement, or scheme — every split here is a box-score or tracking total, not film.",
]

METHODOLOGY = (
    "Every qualified player-season (1,500+ real regular-season minutes) gets each real split tested against a real "
    "baseline: FG% in each of 5 shot zones vs. the rest of the league's FG% in that zone (two-proportion z-test, 50+ "
    "FGA), and points per possession in each NBA Synergy play type vs. the rest of the league (z-test, 50+ "
    "possessions; the per-possession SD is estimated from the real spread of player PPPs, since Synergy doesn't "
    "publish per-possession outcomes). A split is listed only when p < 0.05; the strongest 3 each way are shown. "
    "At p < 0.05, about 1 in 20 tested splits would clear the bar by chance alone, so the expected number of chance "
    "findings for this player is shown too. Real check: across every player, these findings point the same way the "
    "following season 83-87% of the time (50% would be chance). High-leverage FG% (from the Garbage-Time Deflator) is "
    "tested too, but shown separately: the same check found it no better than chance. Shot zones come from the same "
    "coordinate classifier as the shot charts, verified against the NBA's own league zone totals."
)


def _fmt(category, value):
    return f"{value:.2f} PPP" if category == "playtype" else f"{value * 100:.1f}% FG"


def _label(category, split):
    return PLAYTYPE_LABELS.get(split, split) if category == "playtype" else split


def _finding(row):
    season, category, split, value, baseline, own_avg, n, n_unit, z, p, significant, direction = row
    label = _label(category, split)
    base_name = "his other moments" if category == "leverage" else "league"
    p_txt = "p < 0.001" if p < 0.001 else f"p = {p:.3f}"
    return {
        "category": category,
        "split": split,
        "label": label,
        "value": value,
        "baseline": baseline,
        "own_avg": own_avg,
        "n": n,
        "n_unit": n_unit,
        "z": z,
        "p": p,
        "significant": significant,
        "direction": direction,
        "text": f"{label}: {_fmt(category, value)} vs. {_fmt(category, baseline)} {base_name} (n = {n} {n_unit}, {p_txt}).",
    }


@router.get("/players/scouting-report/{player_name}")
def get_scouting_report(player_name: str, season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.scouting_splits');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="Scouting reports haven't been built yet — run scripts/build_scouting_reports.py.")

        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute("SELECT DISTINCT season FROM scouting_splits WHERE player_id = %s ORDER BY season;", (player_id,))
        seasons = [r[0] for r in cursor.fetchall()]
        if season is None:
            if not seasons:
                raise HTTPException(
                    status_code=404,
                    detail=f"No qualified season on file for {resolved_name} (needs {MIN_MINUTES:,}+ real regular-season "
                           "minutes in a season with real play-type data, 2012-13 onward).",
                )
            season = seasons[-1]
        elif season not in seasons:
            raise HTTPException(
                status_code=404,
                detail=f"No scouting report for {resolved_name} in {season - 1}-{str(season)[-2:]} (needs {MIN_MINUTES:,}+ "
                       "real regular-season minutes and real play-type data, 2012-13 onward).",
            )

        cursor.execute("SELECT min * gp FROM player_season_stats WHERE player_id = %s AND season = %s;", (player_id, season))
        m = cursor.fetchone()
        minutes = float(m[0]) if m and m[0] is not None else None
        qualified = minutes is not None and minutes >= MIN_MINUTES

        cursor.execute(
            """SELECT season, category, split, value, baseline, own_avg, n, n_unit, z, p, significant, direction
               FROM scouting_splits WHERE player_id = %s AND season = %s ORDER BY ABS(z) DESC;""",
            (player_id, season),
        )
        splits = [_finding(r) for r in cursor.fetchall()]

        cursor.execute(
            """SELECT category, n_findings, n_followed, same_direction_rate, significant_again_rate
               FROM scouting_validation;""")
        validation = {
            r[0]: {"n_findings": r[1], "n_followed_next_season": r[2], "same_direction_rate": r[3],
                   "significant_again_rate": r[4]}
            for r in cursor.fetchall()
        }
        cursor.execute(
            """SELECT MAX(ABS(our_fga - nba_fga)::float / nba_fga), MAX(ABS(our_fg_pct - nba_fg_pct))
               FROM zone_classifier_check;""")
        zc = cursor.fetchone()

        # Best defenders against him — real tracked matchups, descriptive.
        cursor.execute(
            """SELECT SUM(matchup_fgm), SUM(matchup_fga) FROM player_matchups
               WHERE off_player_id = %s AND season = %s;""",
            (player_id, season),
        )
        tot_fgm, tot_fga = cursor.fetchone()
        cursor.execute(
            """SELECT def_player_id, def_player_name, partial_poss, matchup_fgm, matchup_fga
               FROM player_matchups
               WHERE off_player_id = %s AND season = %s AND partial_poss >= %s AND matchup_fga >= %s
               ORDER BY matchup_fgm::float / matchup_fga ASC, partial_poss DESC LIMIT 3;""",
            (player_id, season, MIN_DEF_POSS, MIN_DEF_FGA),
        )
        defenders = []
        for d in cursor.fetchall():
            fgm, fga = float(d[3]), float(d[4])
            rest_m, rest_n = float(tot_fgm) - fgm, float(tot_fga) - fga
            p_val = None
            if rest_n > 0:
                pooled = (fgm + rest_m) / (fga + rest_n)
                se = math.sqrt(pooled * (1 - pooled) * (1 / fga + 1 / rest_n)) if 0 < pooled < 1 else 0
                if se > 0:
                    zz = (fgm / fga - rest_m / rest_n) / se
                    p_val = math.erfc(abs(zz) / math.sqrt(2))
            defenders.append({
                "player_id": d[0], "player_name": d[1], "partial_poss": round(float(d[2]), 1),
                "fgm": int(fgm), "fga": int(fga), "fg_pct": fgm / fga,
                "vs_everyone_else_fg_pct": rest_m / rest_n if rest_n > 0 else None,
                "p": p_val,
            })

    tested = [s for s in splits if s["category"] != "leverage"]
    found = [s for s in tested if s["significant"]]
    strengths = [s for s in found if s["direction"] == "strength"][:MAX_PER_SIDE]
    weaknesses = [s for s in found if s["direction"] == "weakness"][:MAX_PER_SIDE]
    leverage = next((s for s in splits if s["category"] == "leverage"), None)

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "seasons_available": seasons,
        "minutes": minutes,
        "qualified": qualified,
        "min_minutes": MIN_MINUTES,
        "methodology": METHODOLOGY,
        "n_tested": len(tested),
        "n_significant": len(found),
        "expected_by_chance": round(len(tested) * ALPHA, 1),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "all_tested": tested,
        "leverage": leverage,
        "leverage_caveat": (
            "Shown for reference only: across every player, high-leverage FG% splits point the same way the next "
            "season only about as often as a coin flip, so they aren't treated as a scouting key."
        ),
        "best_defenders": defenders,
        "best_defenders_note": (
            f"Real tracked matchups this season with {MIN_DEF_POSS}+ partial possessions and {MIN_DEF_FGA}+ of his shots, "
            "lowest FG% first. Descriptive — small samples; the p-value compares his FG% against this defender vs. "
            "against everyone else."
        ),
        "cant_tell": CANT_TELL,
        "validation": {
            "persistence": validation,
            "zone_classifier_max_fga_error": zc[0],
            "zone_classifier_max_fg_pct_error": zc[1],
        },
        "_source": make_source(
            ["scouting_splits", "scouting_validation", "player_shots", "player_playtypes", "player_matchups",
             "player_leverage_splits"],
            "nba_api (shot charts, Synergy play types, LeagueSeasonMatchups) + ESPN play-by-play",
        ),
    }
