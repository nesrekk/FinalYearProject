"""
Aging curves: how a typical player's production changes from one age to the
next, by the delta method (scripts/build_aging_curves.py writes the tables).

    GET /aging/curves?stat=bpm&era=all        — the curve for every era, the
                                                 chosen one with its sample and
                                                 survivors by age, plus every
                                                 stat's peak age
    GET /aging/player?player_id=2544&stat=bpm&era=all
                                               — one player's seasons on the
                                                 same scale, and the curve's
                                                 path shifted to his level

Everything is measured against that season's league average (minutes- or
attempts-weighted, over the same qualified pool the curves use), so a
1960s season and a 2020s season sit on one scale and league-wide trends
don't count as aging.
"""

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

SOURCE = make_source(["aging_curves", "aging_curve_summary", "aging_league_average", "player_season_stats",
                      "player_bio"],
                     "nba_api (stats.nba.com) + Basketball-Reference (seasons, birth dates)")
ERA_ORDER = ["all", "three_point", "modern"]
SUMMARY_COLS = ["stat", "era", "label", "stat_group", "kind", "higher_is_better", "weight", "era_label",
                "season_from", "season_to", "age_from", "age_to", "peak_age", "peak_lo", "peak_hi", "peak_vs_ref",
                "ref_age", "ref_level", "pairs", "players", "latest_season", "latest_league", "min_minutes",
                "min_attempts", "built_on"]
CURVE_COLS = ["stat", "era", "age", "level", "change_vs_ref", "ci_lo", "ci_hi", "delta_next", "pairs",
              "pair_weight", "thin", "seasons_at_age", "seasons_with_next", "returned_share", "leaver_gap"]
# Season-total attempts behind each shooting stat's weight (the table is per game).
ATTEMPTS_SQL = {"fga": "p.fga * p.gp", "fg3a": "p.fg3a * p.gp", "fta": "p.fta * p.gp",
                "tsa": "(p.fga + 0.44 * p.fta) * p.gp"}
THIN_PAIRS = 100  # build_aging_curves.THIN_PAIRS


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


@lru_cache(maxsize=1)
def _tables():
    """Summary and curve rows, cached per process (restart after a rebuild)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(SUMMARY_COLS)} FROM aging_curve_summary;")
        summary = {(r[0], r[1]): dict(zip(SUMMARY_COLS, r)) for r in cur.fetchall()}
        cur.execute(f"SELECT {', '.join(CURVE_COLS)} FROM aging_curves ORDER BY stat, era, age;")
        curves = {}
        for r in cur.fetchall():
            row = dict(zip(CURVE_COLS, r))
            curves.setdefault((row["stat"], row["era"]), []).append(row)
    for s in summary.values():
        s["built_on"] = s["built_on"].isoformat()
    return summary, curves


def _stats(summary):
    seen = {}
    for (stat, era), s in summary.items():
        e = seen.setdefault(stat, {"key": stat, "label": s["label"], "group": s["stat_group"], "kind": s["kind"],
                                   "higher_is_better": s["higher_is_better"], "peaks": {}})
        e["peaks"][era] = {"age": s["peak_age"], "lo": s["peak_lo"], "hi": s["peak_hi"],
                           "vs_ref": s["peak_vs_ref"], "pairs": s["pairs"], "season_from": s["season_from"]}
    order = ["pts", "reb", "ast", "stl", "blk", "tov", "oreb", "fg3a", "fg3m", "fta", "min", "fg_pct", "fg3_pct",
             "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct", "tov_pct",
             "bpm", "obpm", "dbpm"]
    return sorted(seen.values(), key=lambda e: order.index(e["key"]) if e["key"] in order else 99)


def _check(stat, era, summary):
    if not any(k[0] == stat for k in summary):
        raise HTTPException(status_code=400, detail=f"Unknown stat '{stat}'.")
    if era not in ERA_ORDER:
        raise HTTPException(status_code=400, detail=f"era must be one of {', '.join(ERA_ORDER)}.")
    if (stat, era) not in summary:
        raise HTTPException(status_code=404, detail="No curve for that stat in that era.")


@router.get("/aging/curves")
def aging_curves(stat: str = "bpm", era: str = "all"):
    summary, curves = _tables()
    _check(stat, era, summary)
    s = summary[(stat, era)]
    points = [{k: row[k] for k in CURVE_COLS if k not in ("stat", "era")} for row in curves[(stat, era)]]
    others = []
    for e in ERA_ORDER:
        if (stat, e) in summary:
            others.append({"era": e, "era_label": summary[(stat, e)]["era_label"],
                           "season_from": summary[(stat, e)]["season_from"],
                           "peak_age": summary[(stat, e)]["peak_age"],
                           "points": [{"age": r["age"], "level": r["level"], "thin": r["thin"]}
                                      for r in curves[(stat, e)]]})
    return {
        "stat": stat,
        "era": era,
        "summary": s,
        "points": points,
        "eras": others,
        "stats": _stats(summary),
        "thin_pairs": THIN_PAIRS,
        "method": (
            "Delta method: every player with two consecutive seasons of 250+ minutes (shooting % also need 100 "
            "FGA, 50 3PA or 50 FTA in both) gives one change from age a to a+1. Each season's value is measured "
            "against that season's league average first, so league-wide trends don't count as aging. Changes are "
            "weighted by the harmonic mean of the two seasons' minutes (attempts for shooting %), averaged per age "
            "and chained into a curve, anchored at the average 27-year-old. Ages with under 30 pairs are left off. "
            "95% ranges: 300 bootstrap resamples of whole careers. Age is the player's age on February 1 of the "
            "season (Basketball-Reference's convention), from his birth date, in every season."
        ),
        "caveats": [
            "Survivor bias: players who decline sharply often leave the league, so their drop never appears as a "
            "pair. The curve's decline at older ages is, if anything, too gentle. The table shows how many "
            "players at each age didn't get another qualified season, and how they compared with those who did.",
            "Selection works the other way too: a player kept on after a lucky season tends to fall back, which "
            "makes young and middle ages look slightly worse than pure aging.",
            "One age convention for every season: stored ages switch from Basketball-Reference's to NBA.com's "
            "(often a year older) in 2009-10, so birth dates are used instead.",
            "Rebound %, offensive rebound %, turnover %, usage % and assist % are defined differently before and "
            "after 2009-10 (Basketball-Reference, then NBA.com), so changes across that line are left out.",
            "Per 36 minutes describes the minutes a player earns; minutes a game itself shows how playing time "
            "changes, which is also a coaching decision.",
        ],
        "_source": SOURCE,
    }


@router.get("/aging/player")
def aging_player(player_id: int, stat: str = "bpm", era: str = "all"):
    summary, curves = _tables()
    _check(stat, era, summary)
    s = summary[(stat, era)]
    kind, weight = s["kind"], s["weight"]
    # stat comes from the stored catalogue (checked above), never raw input.
    value_sql = f"p.{stat} / NULLIF(p.min, 0) * 36" if kind == "per36" else f"p.{stat}"
    weight_sql = "p.gp * p.min" if weight == "minutes" else "p.gp" if weight == "games" else ATTEMPTS_SQL[weight]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT p.season, p.player_name, p.team_abbreviation, p.gp, p.min, p.gp * p.min,
                       {value_sql},
                       {weight_sql}, b.birth_date, l.league_average
                FROM player_season_stats p
                LEFT JOIN player_bio b USING (player_id)
                LEFT JOIN aging_league_average l ON l.stat = %s AND l.season = p.season
                WHERE p.player_id = %s ORDER BY p.season;""", (stat, player_id))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No seasons on file for that player.")
    name = rows[-1][1]
    birth = rows[0][8]
    by_age = {r["age"]: r for r in curves[(stat, era)]}
    seasons, resid_w, resid = [], 0.0, 0.0
    for season, _n, team, gp, mpg, minutes, value, w, _b, league in rows:
        age = None
        if birth is not None:
            age = season - birth.year - (1 if (birth.month, birth.day) > (2, 1) else 0)
        value = None if value is None else float(value)
        minutes = None if minutes is None else float(minutes)
        w = None if w is None else float(w)
        reasons = []
        if season < s["season_from"]:
            reasons.append(f"before {_label(s['season_from'])}")
        if value is None:
            reasons.append("not recorded")
        if minutes is None or minutes < s["min_minutes"]:
            reasons.append(f"under {s['min_minutes']} minutes" if minutes is not None else "minutes not recorded")
        if s["min_attempts"] and (w is None or w < s["min_attempts"]):
            reasons.append(f"under {s['min_attempts']} attempts")
        rel = None if value is None or league is None else value - float(league)
        qualified = not reasons and rel is not None
        seasons.append({"season": season, "team": team, "gp": gp, "age": age,
                        "min": None if mpg is None else round(float(mpg), 1),
                        "minutes": None if minutes is None else round(minutes),
                        "weight": None if w is None else round(w, 1),
                        "value": value, "league_average": None if league is None else float(league),
                        "vs_league": rel, "qualified": qualified,
                        "note": ", ".join(reasons) if reasons else None})
        if qualified and age in by_age:
            resid += w * (rel - by_age[age]["level"])
            resid_w += w
    offset = resid / resid_w if resid_w else None
    path = None
    if offset is not None:
        path = [{"age": r["age"], "level": r["level"] + offset} for r in curves[(stat, era)]]
    return {
        "player": {"player_id": player_id, "player_name": name,
                   "birth_date": None if birth is None else birth.isoformat()},
        "stat": stat,
        "era": era,
        "seasons": seasons,
        "qualified_seasons": sum(1 for x in seasons if x["qualified"]),
        "offset": offset,
        "path": path,
        "notes": [] if birth is not None else ["No birth date on file, so his ages can't be placed on the curve."],
        "method": (
            "Each season is measured against that season's league average (the same qualified pool as the curve). "
            "The dashed path is the typical curve moved up or down to his level: the weighted average gap between "
            "his qualified seasons and the curve at the same ages. It shows how a typical player at his level ages, "
            "not a forecast for him."
        ),
        "_source": SOURCE,
    }
