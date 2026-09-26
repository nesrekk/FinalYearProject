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

CONTEXT_LABELS = {
    "close_def": {"0-2 ft": "Defender 0-2 ft (very tight)", "2-4 ft": "Defender 2-4 ft (tight)",
                  "4-6 ft": "Defender 4-6 ft (open)", "6+ ft": "Defender 6+ ft (wide open)"},
    "touch": {"< 2 s": "Touch under 2 s", "2-6 s": "Touch 2-6 s", "6+ s": "Touch 6+ s"},
    "dribbles": {"0": "0 dribbles", "1": "1 dribble", "2": "2 dribbles", "3-6": "3-6 dribbles", "7+": "7+ dribbles"},
}

CANT_TELL = [
    "Pick-and-roll coverage type (drop, blitz, switch, hedge) — no real coverage-level data is available to this project.",
    "Drive direction (left vs. right) — not in any real dataset this project uses.",
    "How a player's two-pointers change with defender distance, touch time or dribbles — 2PT bands mix layups with "
    "mid-range jumpers, so only 3PT is tested in those contexts.",
    "Help defense, off-ball movement, or scheme — every split here is a box-score or tracking total, not film.",
]

METHODOLOGY = (
    "Every qualified player-season (1,500+ real regular-season minutes) gets each real split tested against a real "
    "baseline: FG% in each of 5 shot zones vs. the rest of the league's FG% in that zone (two-proportion z-test, 50+ "
    "FGA), and points per possession in each NBA Synergy play type vs. the rest of the league (z-test, 50+ "
    "possessions; the per-possession SD is estimated from the real spread of player PPPs, since Synergy doesn't "
    "publish per-possession outcomes), and — from 2013-14 — 3PT FG% by closest-defender distance, touch time and "
    "dribbles vs. the rest of the league in the same band (50+ attempts; 3PT only, because 2PT bands mix layups with "
    "mid-range jumpers and mostly restate where a player shoots). A split is listed only when p < 0.05; up to 3 each "
    "way are shown, "
    "taking the strongest from each category first so one skill isn't repeated three ways. "
    "At p < 0.05, about 1 in 20 tested splits would clear the bar by chance alone, so the expected number of chance "
    "findings for this player is shown too. Real check: across every player, each category's findings point the same "
    "way the following season 83-87% of the time (50% would be chance); a category only counts as a scouting key when "
    "that rate is reliably above 50%. High-leverage FG% (from the Garbage-Time Deflator) fails that check, so it's shown "
    "separately for reference. Shot zones come from the same coordinate classifier as the shot charts, verified "
    "against the NBA's own league zone totals."
)


def _fmt(category, value):
    return f"{value:.2f} PPP" if category == "playtype" else f"{value * 100:.1f}% FG"


def _label(category, split):
    if category == "playtype":
        return PLAYTYPE_LABELS.get(split, split)
    if category in CONTEXT_LABELS:
        bucket, shot = split.split("|")
        return f"{CONTEXT_LABELS[category].get(bucket, bucket)} · {shot}"
    return split


def _pick(findings, k):
    """Up to k findings: the strongest (by |z|) from each category first,
    then the next strongest overall — so one skill isn't listed three ways."""
    picked, seen = [], set()
    for f in findings:
        if f["category"] not in seen:
            picked.append(f)
            seen.add(f["category"])
        if len(picked) == k:
            return picked
    for f in findings:
        if f not in picked:
            picked.append(f)
        if len(picked) == k:
            break
    return picked


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
            """SELECT category, n_findings, n_followed, same_direction_rate, significant_again_rate,
                      same_direction_lower95, reliable
               FROM scouting_validation;""")
        validation = {
            r[0]: {"n_findings": r[1], "n_followed_next_season": r[2], "same_direction_rate": r[3],
                   "significant_again_rate": r[4], "same_direction_lower95": r[5], "reliable": r[6]}
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

    reliable = {c for c, v in validation.items() if c != "all" and v["reliable"]}
    tested = [s for s in splits if s["category"] in reliable]
    found = [s for s in tested if s["significant"]]
    strengths = _pick([s for s in found if s["direction"] == "strength"], MAX_PER_SIDE)
    weaknesses = _pick([s for s in found if s["direction"] == "weakness"], MAX_PER_SIDE)
    reference = [s for s in splits if s["category"] not in reliable]
    leverage = next((s for s in reference if s["category"] == "leverage"), None)

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
        "reliable_categories": sorted(reliable),
        "reference_only": reference,
        "leverage": leverage,
        "leverage_caveat": (
            "Shown for reference only: across every player, high-leverage FG% splits point the same way the next "
            "season only about as often as a coin flip, so they aren't treated as a scouting key."
        ),
        "context_note": (
            "Defender-distance, touch-time and dribble splits (2013-14 onward) cover 3-point shooting only."
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
            ["scouting_splits", "scouting_validation", "player_shots", "player_playtypes", "player_shot_context",
             "player_matchups", "player_leverage_splits"],
            "nba_api + ESPN play-by-play",
        ),
    }
