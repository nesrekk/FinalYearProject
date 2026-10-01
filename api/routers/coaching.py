"""Coaching Decisions: four things coaches act on, tested the way every popular belief is.

    GET /coaching/options                 seasons, teams, the four decisions (definitions, counts), the families'
                                          "k of n survive" summary and the build's parameters and exclusion counts
    GET /coaching/decision/{name}?team=&season=
                                          one decision (timeout | challenge | foul_up3 | twoforone): the stored test
                                          for the scope (league, a season, or a team over every season), the
                                          secondary outcomes and sensitivity runs, breakdowns re-read from the
                                          stored decision points for the team / season chosen, the season trend and
                                          the per-team table
    GET /coaching/tests                   every family's summary and every league-level test

All from scripts/build_coaching_decisions.py (coaching_decisions, coaching_decision_tests,
coaching_decision_summary, coaching_decision_meta): regular-season games 2020-21 on whose possessions reconcile
(possession_games.game_ok), every time on the corrected clock (pbp_event_clock). p-values come only from the stored
tests (permutation of the decision within matched moments, Benjamini-Hochberg within a family); a breakdown
re-read here for a team-season gives the matched effect with no p-value, and says so. Every read is cached per
process: restart impact_api after rerunning the script.
"""

import math
import warnings
from functools import lru_cache

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from coaching_lib import DECISIONS, LABELS, UNIT_LABELS, att, wilson
from impact_core import get_db
from source_badge import make_source

router = APIRouter()
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

UPSTREAM = ("ESPN play-by-play via sportsdataverse, cut into possessions on the corrected clock; tested by "
            "scripts/build_coaching_decisions.py")
SOURCE = ["coaching_decisions", "coaching_decision_tests", "coaching_decision_summary", "coaching_decision_meta",
          "possessions", "pbp_event_clock"]

# What each stored column means per decision (the page labels its charts with these).
DEFINITIONS = {
    "timeout": {
        "title": "Does a timeout stop a run?",
        "unit": "moment a run reached 8-0",
        "decider": "the team on the wrong end of the run",
        "treated": "called a timeout before its next play",
        "control": "played on (no timeout before its next play)",
        "outcome": "Net points, next 6 possessions",
        "outcome_b": "Net points, next 2 possessions",
        "outcome_c": "Net points, next 12 possessions",
        "outcome_d": "Scored on its next possession",
        "units": "pts",
        "summary": ("The moment one team's unanswered run reaches 8 points, right after its score, with the other "
                    "team about to inbound: did a timeout before that team's next play change what happened over "
                    "the next six possessions? Compared with matched moments where no timeout was called (same "
                    "season, quarter group, time left, margin and run size), so the regression to the mean that "
                    "follows every run is in both groups."),
    },
    "challenge": {
        "title": "Coach's challenges",
        "unit": "challenge",
        "decider": "the challenging team",
        "treated": "won (call overturned)",
        "control": "lost (call stands or replay supports it)",
        "outcome": "Change in the challenger's win probability over two possessions",
        "outcome_b": "Net points over the same two possessions",
        "units": "wp",
        "summary": ("Who wins challenges, and what is a won one worth? Win probability (Game Replay's model) from "
                    "the start of the possession in progress at the challenge to the end of the next one, after a "
                    "won challenge vs after a lost one, matched by season and quarter group."),
    },
    "foul_up3": {
        "title": "Fouling up 3 late",
        "unit": "first possession of a game where the trailing team has the ball, down 3, with 24 s or less left "
                "in the 4th quarter or overtime",
        "decider": "the leading team",
        "treated": "fouled before the trailing team's first shot",
        "control": "defended (no foul, or a foul on a shot)",
        "outcome": "Leader won the game",
        "outcome_b": "Period ended tied",
        "outcome_c": "Trailing team made a three on the possession",
        "units": "share",
        "summary": ("Leading by 3 with the clock almost out: foul to give up two free throws, or defend the three? "
                    "The leader's chance of winning when it fouled vs when it defended, matched by time left at "
                    "the start of the possession and regulation vs overtime."),
    },
    "twoforone": {
        "title": "The 2-for-1",
        "unit": "possession starting with 28-40 s left in quarters 1-3",
        "decider": "the team with the ball",
        "treated": "shot early (first shot, shooting foul or turnover with more than 24 s left)",
        "control": "did not",
        "outcome": "Net points to the end of the quarter",
        "outcome_b": "Own possessions minus the opponent's to the end of the quarter",
        "outcome_c": "Points on the possession itself",
        "outcome_d": "Net points after the possession itself",
        "units": "pts",
        "summary": ("With about half a minute left in a quarter, shooting early guarantees the ball back for the "
                    "last shot. Net points from that possession to the end of the quarter, early vs not, matched "
                    "by season, quarter, start time (2 s bins) and how the possession began."),
    },
}
NOT_ON_FILE = {
    "challenge": ("What was challenged (foul, out of bounds, goaltending) is not on file: ESPN rewrites the play-by-play "
                  "after an overturn, so the overturned call disappears and only lost challenges still show the call "
                  "that was challenged. The cost of a lost challenge, the timeout it uses, isn't priced either: the "
                  "win-probability model has no timeouts-left term."),
    "timeout": ("ESPN's log has one kind of timeout, always charged to a team, so a coach's choice and a mandatory "
                "television break charged to the team can't be told apart. Timeouts are placed by the order of the "
                "log, not its clock: ESPN stamps about one timeout in five late (checked against NBA.com's own log)."),
    "foul_up3": ("Timeouts left, team fouls and who was inbounding aren't used; the fouling teams were a little "
                 "stronger before the game (pre-game expected margin, matched difference in the build's balance "
                 "check)."),
    "twoforone": ("Shooting early is partly an opportunity (a quick open look) as well as a choice; how much of the "
                  "gain is the early shot itself and how much the extra possession is shown separately."),
}
TO_RUN = [(8, "8-0"), (9, "9-0"), (10, "10-0 or more")]
PERIOD_GROUPS = [("P12", "1st-2nd quarter"), ("P3", "3rd quarter"), ("P4+", "4th quarter / OT")]


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _f(v, d=4):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, d)


def _require(cur):
    cur.execute("SELECT to_regclass('public.coaching_decisions'), to_regclass('public.coaching_decision_tests')")
    if None in cur.fetchone():
        raise HTTPException(status_code=503, detail="No coaching-decision data: run scripts/build_coaching_decisions.py.")


@lru_cache(maxsize=1)
def _base():
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        rows = pd.read_sql_query("SELECT * FROM coaching_decisions", conn)
        tests = pd.read_sql_query("SELECT * FROM coaching_decision_tests", conn)
        summary = pd.read_sql_query("SELECT * FROM coaching_decision_summary ORDER BY family", conn)
        cur.execute("SELECT key, value, note FROM coaching_decision_meta ORDER BY key")
        meta = {k: {"value": v, "note": n} for k, v, n in cur.fetchall()}
    rows["pg"] = [("P12" if p <= 2 else "P3" if p == 3 else "P4+") for p in rows.period]
    return {"rows": rows, "tests": tests, "summary": summary, "meta": meta,
            "seasons": sorted(int(s) for s in rows.season.unique()), "teams": sorted(rows.team.unique())}


def _test_dict(r):
    if r is None:
        return None
    out = {k: (_f(r[k], 6) if isinstance(r[k], (float, np.floating)) else r[k]) for k in
           ("level", "family", "key", "unit_id", "unit_name", "n_treated", "n_control", "n_dropped", "n_strata", "stat",
            "ci_lo", "ci_hi", "treated_mean", "control_mean", "null_mean", "null_sd", "null_lo", "null_hi", "p",
            "n_perm", "q", "survives", "note")}
    for k in ("n_treated", "n_control", "n_dropped", "n_strata", "n_perm"):
        out[k] = None if r[k] is None or (isinstance(r[k], float) and math.isnan(r[k])) else int(r[k])
    out["season"] = int(r["season"])
    out["survives"] = None if r["survives"] is None or (isinstance(r["survives"], float) and math.isnan(r["survives"])) \
        else bool(r["survives"])
    return out


def _find(tests, **kw):
    m = tests
    for k, v in kw.items():
        m = m[m[k] == v]
    return None if m.empty else m.iloc[0]


def _live(d, col="outcome", strata="stratum"):
    """Matched effect re-read from stored decision points (no p-value: only the stored tests carry one). A team's
    own moments are matched within the coarser `team_stratum` (the team tests' strata)."""
    d = d[d.treated.notna() & d[col].notna()]
    if d.empty:
        return None
    s = pd.factorize(d[strata])[0]
    t = d.treated.astype(bool).to_numpy()
    a, m1, m0, ns = att(d[col].to_numpy(float), t, s)
    used = pd.Series(t).groupby(s).transform(lambda x: x.any() and not x.all()).to_numpy()
    return {"stat": _f(a, 6), "treated_mean": _f(m1, 6), "control_mean": _f(m0, 6), "n_strata": ns,
            "n_treated": int(t[used].sum()), "n_control": int((~t[used]).sum()), "n": int(len(d))}


def _group_rows(d, key, groups, col="outcome", strata="stratum"):
    out = []
    for g, lab in groups:
        sub = d[d[key] == g] if not callable(g) else d[g(d)]
        if sub.empty:
            continue
        lv = _live(sub, col, strata) or {}
        out.append({"key": str(lab), "label": lab, "n": int(len(sub)),
                    "treated": int((sub.treated == True).sum()),  # noqa: E712
                    "treated_share": _f((sub.treated == True).mean(), 4),  # noqa: E712
                    "treated_raw": _f(sub[sub.treated == True][col].mean(), 4),  # noqa: E712
                    "control_raw": _f(sub[sub.treated == False][col].mean(), 4),  # noqa: E712
                    **{k: lv.get(k) for k in ("stat", "treated_mean", "control_mean", "n_strata")}})
    return out


@router.get("/coaching/options")
def coaching_options():
    b = _base()
    rows = b["rows"]
    decisions = []
    for d in DECISIONS:
        sub = rows[rows.decision == d]
        decisions.append({"key": d, "label": LABELS[d], **DEFINITIONS[d], "n": int(len(sub)),
                          "treated": int((sub.treated == True).sum()),  # noqa: E712
                          "not_on_file": NOT_ON_FILE.get(d)})
    meta = b["meta"]
    return {
        "seasons": [{"season": s, "label": label(s), "n": int((rows.season == s).sum())} for s in b["seasons"]],
        "teams": b["teams"],
        "decisions": decisions,
        "league": {r["key"]: _test_dict(r) for _, r in b["tests"][b["tests"].family == "coaching:league"].iterrows()},
        "families": [{**{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()},
                      "label": UNIT_LABELS.get(r["family"], r["label"])} for r in b["summary"].to_dict("records")],
        "params": {k.split(":", 1)[1]: v["value"] for k, v in meta.items() if k.startswith("param:")},
        "counts": {k.split(":", 1)[1]: v["value"] for k, v in meta.items() if k.startswith("count:")},
        "balance": {k.split(":", 1)[1]: {"value": _f(v["value"], 4), "note": v["note"]}
                    for k, v in meta.items() if k.startswith("balance:")},
        "_source": make_source(SOURCE, UPSTREAM),
    }


@router.get("/coaching/tests")
def coaching_tests():
    b = _base()
    t = b["tests"]
    league = [_test_dict(r) for _, r in t[t.level == "league"].sort_values(["family", "key"]).iterrows()]
    return {"summary": b["summary"].to_dict("records"), "league": league, "_source": make_source(SOURCE, UPSTREAM)}


@router.get("/coaching/decision/{name}")
def coaching_decision(name: str, team: str = Query(None), season: int = Query(None)):
    if name not in DECISIONS:
        raise HTTPException(status_code=404, detail=f"Unknown decision {name!r}; one of {', '.join(DECISIONS)}.")
    b = _base()
    if team and team not in b["teams"]:
        raise HTTPException(status_code=404, detail=f"No decisions for team {team!r}.")
    if season and season not in b["seasons"]:
        raise HTTPException(status_code=404, detail=f"No decisions for season {season}.")
    rows, tests = b["rows"], b["tests"]
    all_d = rows[rows.decision == name]
    d = all_d
    if team:
        d = d[d.team == team]
    if season:
        d = d[d.season == season]

    league = _test_dict(_find(tests, level="league", family="coaching:league", key=name))
    scope_test, scope_note = league, "League, every season: stored permutation test."
    if season and not team:
        scope_test = _test_dict(_find(tests, level="season", key=name, season=season))
        scope_note = f"League, {label(season)}: stored permutation test (not in any family)."
    elif team and not season:
        fam = f"{name}:team"
        scope_test = _test_dict(_find(tests, level="team", family=fam, unit_id=team))
        scope_note = (f"{team}, every season: stored permutation test, Benjamini-Hochberg over the 30 teams."
                      if scope_test else f"{team}: no stored test for this decision (league level only).")
    elif team and season:
        scope_test = None
        scope_note = f"{team}, {label(season)}: matched effect re-read from the stored decision points, no p-value."
    strata = "team_stratum" if team else "stratum"
    live = _live(d, strata=strata)
    scope_stat = DEFINITIONS[name]["outcome"]
    if name == "challenge" and team and not season and scope_test:
        scope_stat = "Success rate minus the league's (same season and quarter group)"

    secondary = [_test_dict(r) for _, r in tests[(tests.family == f"secondary:{name}")].iterrows()]
    sensitivity = [_test_dict(r) for _, r in tests[(tests.family == f"sensitivity:{name}")].iterrows()]
    trend = []
    for s in b["seasons"]:
        r = _find(tests, level="season", key=name, season=s)
        sub = all_d[all_d.season == s] if not team else all_d[(all_d.season == s) & (all_d.team == team)]
        lv = _live(sub, strata="team_stratum") if team else None
        entry = {"season": s, "label": label(s), "n": int(len(sub)),
                 "treated": int((sub.treated == True).sum())}  # noqa: E712
        if r is not None and not team:
            entry.update({k: _test_dict(r)[k] for k in ("stat", "ci_lo", "ci_hi", "p", "treated_mean", "control_mean")})
        elif lv:
            entry.update({k: lv[k] for k in ("stat", "treated_mean", "control_mean")})
        trend.append(entry)

    team_rows = []
    fam = f"{name}:team"
    for t in b["teams"]:
        r = _find(tests, level="team", family=fam, unit_id=t)
        sub = all_d[all_d.team == t]
        entry = {"team": t, "n": int(len(sub)), "treated": int((sub.treated == True).sum()),  # noqa: E712
                 "treated_share": _f((sub.treated == True).sum() / max(int(sub.treated.notna().sum()), 1), 4)}  # noqa: E712
        if r is not None:
            entry.update({k: _test_dict(r)[k] for k in ("stat", "treated_mean", "control_mean", "p", "q", "survives",
                                                        "n_perm", "null_lo", "null_hi")})
        team_rows.append(entry)

    out = {"decision": name, "label": LABELS[name], "definition": DEFINITIONS[name], "not_on_file": NOT_ON_FILE.get(name),
           "team": team, "season": season, "n": int(len(d)), "treated": int((d.treated == True).sum()),  # noqa: E712
           "league": league, "scope_test": scope_test, "scope_note": scope_note, "scope_stat": scope_stat,
           "live": live, "strata": strata,
           "secondary": secondary, "sensitivity": sensitivity, "trend": trend, "teams": team_rows,
           "breakdowns": _breakdowns(name, d, strata)}
    return {**out, "_source": make_source(SOURCE, UPSTREAM)}


def _breakdowns(name, d, strata="stratum"):
    if d.empty:
        return {}

    def live(dd, col="outcome"):
        return _live(dd, col, strata)

    def groups(dd, key, gs, col="outcome"):
        return _group_rows(dd, key, gs, col, strata)

    if name == "timeout":
        windows = []
        for col, k in (("outcome_b", 2), ("outcome", 6), ("outcome_c", 12)):
            lv = live(d, col) or {}
            windows.append({"k": k, "col": col, **lv})
        return {
            "windows": windows,
            "by_run": groups(d, "size", [(lambda x, lo=lo, hi=hi: (x["size"] >= lo) & (x["size"] < hi), lab)
                                              for (lo, lab), hi in zip(TO_RUN, [9, 10, 99])]),
            "by_period": groups(d, "pg", PERIOD_GROUPS),
            "scored_next": live(d, "outcome_d"),
            "later_share": _f(d[d.treated == False].later.mean(), 4),  # noqa: E712
            "run_mean": _f(d["size"].mean(), 3),
        }
    if name == "challenge":
        dec = d[d.treated.notna()]
        by_season = []
        for s in sorted(d.season.unique()):
            sub = d[d.season == s]
            k = int((sub.treated == True).sum())  # noqa: E712
            n = int(sub.treated.notna().sum())
            lo, hi = wilson(k, n)
            by_season.append({"season": int(s), "label": label(int(s)), "won": k, "lost": n - k,
                              "unknown": int(sub.treated.isna().sum()), "rate": _f(k / n if n else None, 4),
                              "lo": _f(lo, 4), "hi": _f(hi, 4)})
        by_period = []
        for g, lab in PERIOD_GROUPS + [("CLOSE", "Last 2 min of the 4th / OT")]:
            sub = dec[(dec.period >= 4) & (dec.sec_left <= 120)] if g == "CLOSE" else dec[dec.pg == g]
            k, n = int((sub.treated == True).sum()), len(sub)  # noqa: E712
            lo, hi = wilson(k, n)
            by_period.append({"key": g, "label": lab, "won": k, "n": n, "rate": _f(k / n if n else None, 4),
                              "lo": _f(lo, 4), "hi": _f(hi, 4),
                              "lev": _f(sub.lev.median(), 4)})
        kinds = d.detail.value_counts().to_dict()
        return {
            "by_season": by_season, "by_period": by_period,
            "kinds": {k: int(v) for k, v in kinds.items()},
            "value_pts": live(d, "outcome_b"),
            "median_lev": _f(d.lev.median(), 4),
            "wp_won": _f(dec[dec.treated == True].outcome.mean(), 4),  # noqa: E712
            "wp_lost": _f(dec[dec.treated == False].outcome.mean(), 4),  # noqa: E712
        }
    if name == "foul_up3":
        buckets = [(lambda x, lo=lo: (x.sec_left > lo) & (x.sec_left <= lo + 6), f"{lo}-{lo + 6} s") for lo in (0, 6, 12, 18)]
        return {
            "by_time": groups(d, "sec_left", buckets),
            "tied": live(d, "outcome_b"),
            "three": live(d, "outcome_c"),
            "win_fouled": _f(d[d.treated == True].outcome.mean(), 4),  # noqa: E712
            "win_defended": _f(d[d.treated == False].outcome.mean(), 4),  # noqa: E712
            "reg": int((d.period == 4).sum()), "ot": int((d.period > 4).sum()),
        }
    if name == "twoforone":
        buckets = [(lambda x, lo=lo: (x.sec_left >= lo) & (x.sec_left < lo + 2), f"{lo}-{lo + 2} s") for lo in range(28, 40, 2)]
        return {
            "by_start": groups(d, "sec_left", buckets),
            "by_start_type": groups(d, "detail", [("make", "After a make"), ("miss", "After a miss"),
                                                       ("turnover", "After a turnover"), ("other", "Other")]),
            "split": {"itself": live(d, "outcome_c"), "after": live(d, "outcome_d"),
                      "possessions": live(d, "outcome_b")},
        }
    return {}
