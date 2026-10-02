"""Data Quality: the audit's error classes of the public feeds, checked live, flagged per game, and whether they matter.

    GET /data-quality/overview          every error class (feed, how detected, size from the audit, handling, its per-game
                                        rule and the games it touches by season, whether it has a live check), the games'
                                        quality levels by season, the build's checks, and each result's headline verdict
    GET /data-quality/check/{key}       one class's live check: its numbers re-measured from the tables as they are now,
                                        each beside the audit's stored value and whether they agree at the precision the
                                        paper prints (cached per process: the time of the check is returned)
    GET /data-quality/games?season=&level=&cls=&team=&sort=&dir=&limit=&offset=
                                        the per-game flags, filtered and paged, with what touched each game
    GET /data-quality/sensitivity?result=
                                        one result re-scored with each drop set: every model's score and every pair's
                                        difference per phase and scope, with the verdict against every game

Stored tables: paper_data_audit / paper_data_audit_classes (scripts/paper_data_audit.py) and data_quality_game_flags /
data_quality_sensitivity / data_quality_meta (scripts/build_data_quality.py). Definitions in api/data_quality_lib.py.
The overview and the sensitivity tables are cached per process: restart impact_api after rerunning either script.
"""

import time
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

import data_quality_lib as Q
from impact_core import get_db
from source_badge import make_source

router = APIRouter()

UPSTREAM = ("ESPN play-by-play and scoreboard, the NBA.com shot chart, stats.nba.com team game logs and the season tables, "
            "re-measured by scripts/paper_data_audit.py and scripts/build_data_quality.py")
TABLES = ["paper_data_audit", "paper_data_audit_classes", "data_quality_game_flags", "data_quality_sensitivity", "data_quality_meta"]
SORTS = {"date": "game_date", "classes": "n_classes", "untracked": "untracked_share", "events": "tag_text_events + teamless_subs + unid_events"}
LEVEL_ORDER = [lv for lv, _, _ in Q.LEVELS]
CLASS_SHORT = {
    "twin_copies": "Duplicate copy", "cup_finals": "Not counted", "wrong_player": "Wrong player (repaired)", "tag_text": "Tag ≠ text",
    "unidentified": "No player id", "teamless_sub": "Sub with no team", "score_fields": "Score fields", "last_score": "Last score ≠ final",
    "missed_threes": "3 worded as 2", "clock_offset": "Clock", "clock_lag": "Clock lag", "unreconciled": "Doesn't reconcile", "chart_gaps": "Chart gap",
    "zero_distance": "Zero distance", "unlocated": "No location", "plus_minus": "Plus-minus field",
}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _f(v, d=6):
    if v is None:
        return None
    v = float(v)
    return None if v != v else round(v, d)


def _rows(cur, sql, args=None):
    cur.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _require(cur):
    for t in ("paper_data_audit", "paper_data_audit_classes", "data_quality_game_flags", "data_quality_sensitivity"):
        cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
        if cur.fetchone()[0] is None:
            raise HTTPException(503, f"{t} is not built: run scripts/paper_data_audit.py, then scripts/build_data_quality.py")


@lru_cache(maxsize=1)
def _audit():
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        cur.execute("SELECT key, season, value, fmt FROM paper_data_audit")
        rows = {(k, s): (v, f) for k, s, v, f in cur.fetchall()}
        classes = _rows(cur, "SELECT * FROM paper_data_audit_classes ORDER BY ord")
        cur.execute("SELECT key, value FROM data_quality_meta")
        meta = dict(cur.fetchall())
        conn.rollback()
    return rows, classes, meta


def _class_names():
    _, classes, _ = _audit()
    return {c["key"]: _tex_plain(c["error_class"]) for c in classes}


@lru_cache(maxsize=1)
def _overview():
    audit, classes, meta = _audit()

    def A(key, season=0):
        return audit[(key, season)][0]

    def S(key):
        return {s: v for (k, s), (v, _) in audit.items() if k == key and s}

    with get_db() as conn:
        cur = conn.cursor()
        per = _rows(cur, """SELECT c AS key, season, count(*) AS games FROM data_quality_game_flags, unnest(classes) c
                            GROUP BY 1, 2 ORDER BY 1, 2""")
        lv = _rows(cur, "SELECT season, level, count(*) AS games FROM data_quality_game_flags GROUP BY 1, 2 ORDER BY 1, 2")
        seasons = [r["season"] for r in _rows(cur, "SELECT DISTINCT season FROM data_quality_game_flags ORDER BY 1")]
        cur.execute("SELECT count(*) FROM data_quality_game_flags")
        total = cur.fetchone()[0]
        conn.rollback()
    hits = {}
    for r in per:
        hits.setdefault(r["key"], {})[r["season"]] = r["games"]
    out = []
    for c in classes:
        k = c["key"]
        rule = Q.PER_GAME.get(k)
        by = hits.get(k, {})
        out.append({
            "key": k, "feed": c["feed"], "name": _tex_plain(c["error_class"]), "short": CLASS_SHORT.get(k, _tex_plain(c["error_class"])),
            "detection": _tex_plain(c["detection"]), "size": Q.size_text(k, A, S),
            "handling_kind": c["handling_kind"], "handling": c["handling"],
            "per_game": {"rule": rule[0], "what": rule[1], "level": Q.LEVEL_OF[k],
                         "games": sum(by.values()), "by_season": [{"season": s, "label": label(s), "games": by.get(s, 0)} for s in seasons]}
                        if rule else None,
            "not_per_game": Q.NOT_PER_GAME.get(k),
            "live": {"mode": "build", "why": Q.BUILD_ONLY[k]} if k in Q.BUILD_ONLY else
                    {"mode": "live", "note": Q.LIVE_NOTES.get(k)} if k in Q.LIVE else {"mode": "none"},
        })
    levels = []
    for key, name, what in Q.LEVELS:
        by = {r["season"]: r["games"] for r in lv if r["level"] == key}
        levels.append({"key": key, "label": name, "what": what, "games": sum(by.values()),
                       "by_season": [{"season": s, "label": label(s), "games": by.get(s, 0)} for s in seasons]})
    verdicts = {}
    for result in Q.RESULTS:
        d = _sensitivity(result)
        verdicts[result] = {"label": d["label"], "short": d["short"], "headline": d["headline_summary"]}
    return {
        "games": total, "seasons": [{"season": s, "label": label(s)} for s in seasons],
        "classes": out, "levels": levels, "results": verdicts,
        "feeds": list(dict.fromkeys(c["feed"] for c in classes)),
        "kinds": ["repaired", "worked around", "excluded", "disclosed"],
        "build": {"run": meta.get("run"), "audit_check": meta.get("audit_check"), "live_checks": meta.get("live_checks"),
                  "max_drop_share": meta.get("max_drop_share")},
    }


def _tex_plain(s):
    """The audit's detection text is LaTeX for the paper's table; the page shows it plain."""
    for a, b in (("\\_", "_"), ("vs.\\ ", "vs. "), ("$\\neq$", "≠"), ("$\\div 5$", "÷ 5"), ("$=0$", "= 0"), ("$(0,0)$", "(0, 0)"),
                 ("\\texttt{", ""), ("}", "")):
        s = s.replace(a, b)
    return s


@router.get("/data-quality/overview")
def data_quality_overview():
    d = _overview()
    return {**d, "_source": make_source(TABLES, UPSTREAM)}


@lru_cache(maxsize=32)
def _live(key):
    audit, _, _ = _audit()
    t = time.time()
    with get_db() as conn:
        cur = conn.cursor()
        try:
            res = Q.compare(Q.LIVE[key](cur), audit)
        finally:
            conn.rollback()
    return {"values": res, "seconds": round(time.time() - t, 2),
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


NOTES = {   # what each audit key is, for the live table (paper_data_audit.note is the long form)
    "twin_rows": "pbp_events rows of nba_api games", "twin_games": "nba_api games",
    "cup_games": "ESPN regular-season games with no NBA scoreboard game",
    "unid_events": "named events with no player id", "unid_events_share": "their share of named events",
    "unid_minutes_share": "minutes with fewer than five identified players a side",
    "teamless_subs": "substitutions with no team", "teamless_games": "games holding them", "nan_team_rows": "game lines with no team",
    "score_steps_games": "games with a real final", "score_steps_miss": "summed positive score steps miss the final",
    "score_backwards_games": "games whose score steps backwards", "espn_games": "ESPN games",
    "last_score_games": "games in both tables", "last_score_bad": "last play-by-play score ≠ scoreboard final",
    "clock_outside_events": "regulation events outside their own period", "clock_outside_games": "games holding them",
    "clock_pairs": "shots timed by both feeds", "clock_median": "median |ESPN − chart| (s)", "clock_p99": "99th percentile (s)",
    "clock_big_share": "share over 5 s", "unrec_games": "games that do not reconcile",
    "chart_missing_games": "reconciled games with no chart rows",
    "zero_dist_threes": "threes with shot_distance = 0", "origin_shots": "shots at (0, 0)",
    "pm_games": "games in both tables", "pm_bad": "plus_minus ≠ final margin", "pm_point": "by a point or more",
    "pm_sign": "wrong sign against the win flag", "wrong_team_rows": "season rows with a team the player never played for",
    "age_older": "share of player-seasons a year older than the 1 February age",
}


@router.get("/data-quality/check/{key}")
def data_quality_check(key: str):
    if key not in Q.LIVE:
        if key in Q.BUILD_ONLY:
            raise HTTPException(400, f"{key} is checked by the build only: {Q.BUILD_ONLY[key]}")
        raise HTTPException(404, f"no error class {key}")
    d = _live(key)
    values = [{**v, "label": NOTES.get(v["key"], v["key"]), "season_label": label(v["season"]) if v["season"] else "all"}
              for v in d["values"]]
    return {"key": key, "values": values, "agree": sum(v["ok"] for v in values), "total": len(values),
            "seconds": d["seconds"], "checked_at": d["checked_at"], "note": Q.LIVE_NOTES.get(key),
            "_source": make_source(["paper_data_audit"] + _live_tables(key), UPSTREAM, as_of=d["checked_at"], live=True)}


def _live_tables(key):
    return {"twin_copies": ["pbp_events", "pbp_games"], "cup_finals": ["pbp_games", "game_scores"],
            "unidentified": ["pbp_events", "lineup_stint_seasons"], "teamless_sub": ["pbp_events", "player_game_lines"],
            "score_fields": ["pbp_events", "lineup_stint_games"], "last_score": ["team_game_totals", "game_scores"],
            "clock_offset": ["pbp_events", "pbp_event_clock"], "unreconciled": ["lineup_stint_games"],
            "chart_gaps": ["lineup_stint_games", "player_shots"], "zero_distance": ["player_shots"], "unlocated": ["player_shots"],
            "plus_minus": ["team_game_fatigue", "game_scores"], "wrong_team": ["player_season_stats", "player_game_lines"],
            "age_convention": ["player_season_stats", "player_bio"]}.get(key, [])


def _details(r):
    """What touched one game, in words."""
    out = []
    if r["twin"]:
        out.append(("twin_copies", "an nba_api copy exists (left out)"))
    if r["cup_final"]:
        out.append(("cup_finals", "NBA Cup final: not counted by the NBA"))
    if r["wrong_player_events"]:
        out.append(("wrong_player", f"{r['wrong_player_events']} events re-tagged to the right player"))
    if r["tag_text_events"]:
        out.append(("tag_text", f"{r['tag_text_events']} event{'s' if r['tag_text_events'] > 1 else ''} whose text names someone else"))
    if r["untracked_seconds"]:
        out.append(("unidentified", f"{r['untracked_share'] * 100:.1f}% of minutes without five identified players a side "
                                    f"({r['unid_events']} events with no player id)"))
    if r["teamless_subs"]:
        out.append(("teamless_sub", f"{r['teamless_subs']} substitution{'s' if r['teamless_subs'] > 1 else ''} with no team; "
                                    f"{r['lines_excess_players']} player-game{'' if r['lines_excess_players'] == 1 else 's'} over-credited in the game lines"))
    if r["score_stale"] or r["score_backward"]:
        out.append(("score_fields", "score fields " + " and ".join(x for x, y in (("stale", r["score_stale"]), ("step backwards", r["score_backward"])) if y)))
    if r["last_score_off"]:
        out.append(("last_score", "last play-by-play score ≠ the scoreboard final"))
    if r["missed_three_calls"]:
        out.append(("missed_threes", f"{r['missed_three_calls']} missed shot{'s' if r['missed_three_calls'] > 1 else ''} the text and chart call differently"))
    if r["clock_outside_events"] or r["clock_big_shots"]:
        bits = []
        if r["clock_big_shots"]:
            bits.append(f"{r['clock_big_shots']} of {r['chart_matched']} shots timed 5+ s from the chart (median {r['clock_median_off']:.0f} s)")
        if r["clock_outside_events"]:
            bits.append(f"{r['clock_outside_events']} event{'s' if r['clock_outside_events'] > 1 else ''} outside its period")
        out.append(("clock_offset", "; ".join(bits)))
    if not r["game_ok"]:
        out.append(("unreconciled", f"does not reconcile ({r['reason']})"))
    if r["chart_missing"]:
        out.append(("chart_gaps", "no shot-chart rows"))
    elif r["fg_attempts"] and r["chart_unmatched"] > Q.CHART_UNMATCHED_SHARE * r["fg_attempts"]:
        out.append(("chart_gaps", f"{r['chart_unmatched']} of {r['fg_attempts']} attempts not on the chart"))
    if r["zero_dist_threes"]:
        out.append(("zero_distance", f"{r['zero_dist_threes']} three{'s' if r['zero_dist_threes'] > 1 else ''} at distance 0"))
    if r["pm_off"]:
        out.append(("plus_minus", "plus_minus field ≠ final margin"))
    return [{"key": k, "short": CLASS_SHORT[k], "level": Q.LEVEL_OF[k], "text": t} for k, t in out]


@router.get("/data-quality/games")
def data_quality_games(season: int = Query(None, ge=2000, le=2100), level: str = Query(None), cls: str = Query(None),
                       team: str = Query(None, max_length=4), sort: str = Query("classes"), dir: str = Query("desc"),
                       limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    if level is not None and level not in LEVEL_ORDER:
        raise HTTPException(400, f"level must be one of {LEVEL_ORDER}")
    if cls is not None and cls not in Q.PER_GAME:
        raise HTTPException(400, f"cls must be a per-game class: {list(Q.PER_GAME)}")
    if sort not in SORTS or dir not in ("asc", "desc"):
        raise HTTPException(400, f"sort must be one of {list(SORTS)}, dir asc or desc")
    where, args = ["TRUE"], []
    if season is not None:
        where.append("season = %s")
        args.append(season)
    if level:
        where.append("level = %s")
        args.append(level)
    if cls:
        where.append("%s = ANY(classes)")
        args.append(cls)
    if team:
        where.append("(home_team = %s OR away_team = %s)")
        args += [team.upper(), team.upper()]
    w = " AND ".join(where)
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        cur.execute(f"SELECT count(*) FROM data_quality_game_flags WHERE {w}", args)
        n = cur.fetchone()[0]
        rows = _rows(cur, f"""SELECT * FROM data_quality_game_flags WHERE {w}
                              ORDER BY {SORTS[sort]} {dir} NULLS LAST, game_date DESC, game_id LIMIT %s OFFSET %s""", args + [limit, offset])
        teams = [r[0] for r in _team_list(cur)]
        conn.rollback()
    games = [{"game_id": r["game_id"], "nba_game_id": r["nba_game_id"], "season": r["season"], "season_label": label(r["season"]),
              "date": r["game_date"].isoformat() if r["game_date"] else None, "home": r["home_team"], "away": r["away_team"],
              "level": r["level"], "classes": list(r["classes"]), "details": _details(r)} for r in rows]
    return {"total": n, "limit": limit, "offset": offset, "games": games, "teams": teams,
            "_source": make_source(["data_quality_game_flags"], UPSTREAM)}


def _team_list(cur):
    cur.execute("SELECT DISTINCT t FROM (SELECT home_team t FROM data_quality_game_flags UNION SELECT away_team FROM data_quality_game_flags) x "
                "WHERE t IS NOT NULL ORDER BY 1")
    return cur.fetchall()


def _call(diff, lo, hi):
    """'a' / 'b' (which side is lower, the interval excluding zero) or 'none'."""
    if lo is None or hi is None:
        return None
    return "a" if hi < 0 else "b" if lo > 0 else "none"


def _verdict(full, now):
    """How a drop set's difference compares with every game's."""
    if full is None or now is None:
        return None
    if full == now:
        return "same"
    if full == "none":
        return "becomes clear"
    if now == "none":
        return "no longer clear"
    return "flips"


@lru_cache(maxsize=8)
def _sensitivity(result):
    res = Q.RESULTS[result]
    names = _class_names()
    _, _, meta = _audit()
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        rows = _rows(cur, "SELECT * FROM data_quality_sensitivity WHERE result = %s", (result,))
        conn.rollback()
    if not rows:
        raise HTTPException(503, f"no sensitivity rows for {result}: rerun scripts/build_data_quality.py")
    rules = {r["key"]: r["rule"] for r in meta.get(f"{result}_drop_sets", [])}
    first = res["scopes"][0]
    full = {(r["phase"], r["metric"], r["model_a"], r["model_b"]): r for r in rows if r["drop_set"] == "none"}
    order = ["none", "flagged"] + list(Q.PER_GAME)
    sets = {}
    for r in rows:
        sets.setdefault(r["drop_set"], {"dropped": r["games_dropped"], "in_scope": r["games_in_scope"]})
    phases = [p for p in ("tune", "validate", "test", "all") if any(r["phase"] == p for r in rows)]
    out_sets = []
    for key in sorted(sets, key=order.index):
        label_ = "Every game" if key == "none" else "Flagged or excluded games" if key == "flagged" else names.get(key, key)
        cells = []
        for scope in res["scopes"]:
            src = [r for r in rows if r["drop_set"] == key and (r["scope"] == scope or (key == "none" and r["scope"] == first))]
            if not src:
                continue
            for r in src:
                f = full.get((r["phase"], r["metric"], r["model_a"], r["model_b"]))
                cell = {"scope": scope, "phase": r["phase"], "metric": r["metric"], "a": r["model_a"], "b": r["model_b"] or None,
                        "n": r["n"], "n_clusters": r["n_clusters"], "value_a": _f(r["value_a"]), "value_b": _f(r["value_b"]),
                        "diff": _f(r["diff"]), "ci_lo": _f(r["ci_lo"]), "ci_hi": _f(r["ci_hi"]), "p_boot": _f(r["p_boot"], 4),
                        "p_perm": _f(r["p_perm"], 4), "dm_p": _f(r["dm_p"], 4),
                        "full_diff": _f(f["diff"]) if f else None,
                        "rand_draws": r["rand_draws"], "rand_lo": _f(r["rand_lo"]), "rand_hi": _f(r["rand_hi"]), "rand_p": _f(r["rand_p"], 4)}
                if r["model_b"]:
                    cell["call"] = _call(r["diff"], r["ci_lo"], r["ci_hi"])
                    cell["verdict"] = _verdict(_call(f["diff"], f["ci_lo"], f["ci_hi"]) if f else None, cell["call"])
                    cell["beyond_random"] = None if r["rand_p"] is None else bool(r["rand_p"] <= 0.05)
                cells.append(cell)
        out_sets.append({"key": key, "label": label_, "rule": rules.get(key), "level": Q.LEVEL_OF.get(key),
                         "games_dropped": sets[key]["dropped"], "games_in_scope": sets[key]["in_scope"], "cells": cells})
    a, b = res["headline"]
    head_phase = "test" if "test" in phases else phases[-1]
    by_set = {s["key"]: s for s in out_sets}
    changed = [by_set[k]["label"] for k in by_set for c in by_set[k]["cells"]
               if c["a"] == a and c["b"] == b and c["metric"] == res["metric"] and c.get("verdict") not in (None, "same")]
    hcells = [c for s in out_sets if s["key"] != "none" for c in s["cells"] if c["a"] == a and c["b"] == b and c["metric"] == res["metric"]]
    summary = {"phase": head_phase, "a": a, "b": b, "a_label": Q.MODEL_LABELS[a], "b_label": Q.MODEL_LABELS[b],
               "sets": len(out_sets) - 1, "changed": sorted(set(changed)), "cells": len(hcells),
               "same": sum(c.get("verdict") == "same" for c in hcells), "flips": sum(c.get("verdict") == "flips" for c in hcells),
               "controlled": sum(c["rand_p"] is not None for c in hcells),
               "beyond_random": sum(bool(c.get("beyond_random")) for c in hcells)}
    full_head = next((c for c in by_set["none"]["cells"] if c["a"] == a and c["b"] == b and c["phase"] == head_phase
                      and c["metric"] == res["metric"]), None)
    flagged_head = next((c for c in by_set.get("flagged", {"cells": []})["cells"] if c["a"] == a and c["b"] == b
                         and c["phase"] == head_phase and c["metric"] == res["metric"] and c["scope"] == first), None)
    summary["full"] = full_head
    summary["flagged"] = flagged_head
    summary["flagged_dropped"] = by_set.get("flagged", {}).get("games_dropped")
    metrics = sorted({r["metric"] for r in rows})
    return {"result": result, "label": res["label"], "short": res["short"], "what": res["what"], "unit": res["unit"],
            "task": res["task"], "metric": res["metric"], "metrics": metrics, "phases": phases,
            "scopes": [{"key": s, "label": Q.SCOPE_LABELS[s]} for s in res["scopes"]],
            "pairs": [{"a": x, "b": y, "a_label": Q.MODEL_LABELS[x], "b_label": Q.MODEL_LABELS[y]} for x, y in res["pairs"]],
            "models": sorted({r["model_a"] for r in rows if not r["model_b"]}), "model_labels": Q.MODEL_LABELS,
            "phase_seasons": {r["phase"]: r["seasons"] for r in rows},
            "headline": {"a": a, "b": b}, "headline_summary": summary, "sets": out_sets,
            "resamples": rows[0]["resamples"], "seasons": sorted({r["seasons"] for r in rows})}


@router.get("/data-quality/sensitivity")
def data_quality_sensitivity(result: str = Query("impact")):
    if result not in Q.RESULTS:
        raise HTTPException(400, f"result must be one of {list(Q.RESULTS)}")
    return {**_sensitivity(result), "_source": make_source(["data_quality_sensitivity", "data_quality_game_flags", "data_quality_meta"], UPSTREAM)}
