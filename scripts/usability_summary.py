"""
usability_summary.py
====================
Round 7 step 10: turns the exported usability-study sessions into counts.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/usability_summary.py docs/usability/sessions
    ... --out docs/usability/RESULTS.md      write the markdown instead of printing it
    ... --include-pilot                       count pilot sessions too (never for the report)

Input: the JSON files the study panel exports (components/common/StudyPanel.jsx,
utils/studyLog.js), one per participant. The tasks, their answers and their
checks are frontend/src/utils/studyTasks.json; the moderator's guide is
docs/USABILITY_STUDY.md.

What is counted, per task (nothing here is an opinion):

  completed     the moderator marked success (with or without a hint) AND the
                board check passed (where the task has one) AND the answer
                matches (where the task asks a question). Disagreements between
                the moderator and the checks are listed, for the owner to look
                at, never resolved silently.
  board check   the boards the panel saved when the task ended, read against
                the task's `check` (check_task() below): e.g. a table bound to
                a set holding both players with points, rebounds and assists.
                Any of the participant's boards may satisfy it.
  answer        what the moderator typed, normalised (case, spaces, dashes)
                against the task's accepted answers.
  time          seconds from Start task to Participant is done / Gave up.
  hints, stuck  the moderator's Hint given presses and stuck tags.
  app signals   events the app logged during the task: block errors, board
                errors, undos, blocks removed, and the longest gap between two
                logged actions (a long gap is where someone stopped to think).
  ease          the Single Ease Question (1 very difficult .. 7 very easy).
  SUS           the System Usability Scale per participant (Brooke 1996):
                ((odd items − 1) + (5 − even items)) × 2.5, 0-100.

The truths the checks use are recomputed from the database through the
Workbench's own query and finder code (the finder's player set for task 2,
the 40-point games for task 3), and the script stops if a stored answer no
longer matches the data: then the task set is stale and must not be graded.
"""

import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(ROOT, "api")
if API not in sys.path:
    sys.path.insert(0, API)

TASKS_PATH = os.path.join(ROOT, "frontend", "src", "utils", "studyTasks.json")
STUDY_FORMAT = "nba-hub-usability"
OUTCOMES = ("success", "help", "fail", "skipped")
# Event types counted as trouble signals (utils/studyLog.js callers).
SIGNALS = (("block_error", "Block errors"), ("error", "Board errors"), ("js_error", "Page errors"),
           ("undo", "Undos"), ("remove_block", "Blocks removed"))


def load_tasks(path=TASKS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_sessions(paths):
    out = []
    for p in paths:
        files = sorted(glob.glob(os.path.join(p, "*.json"))) if os.path.isdir(p) else [p]
        for fp in files:
            with open(fp, encoding="utf-8") as f:
                s = json.load(f)
            if s.get("format") != STUDY_FORMAT:
                raise SystemExit(f"{fp}: not a study session export")
            s["_file"] = os.path.basename(fp)
            out.append(s)
    return out


# ── Truths from the database ────────────────────────────────────────────

def truths(study):
    """The values the checks compare against, recomputed live."""
    from routers.workbench import QuerySpec, workbench_query
    from routers.workbench_finder import FinderSpec, workbench_finder

    out = {}
    for t in study["tasks"]:
        c = t["check"]
        if c["kind"] == "finder_chart":
            r = workbench_finder(FinderSpec(**c["finder"]))
            players = {row["player_id"] for row in r["rows"]}
            if len(players) != c["players"]:
                raise SystemExit(f"{t['key']}: the finder now finds {len(players)} players, the task says "
                                 f"{c['players']}: the task set is stale")
            out[t["key"]] = players
        elif c["kind"] == "answer":
            rows = workbench_query(QuerySpec(
                dataset="player_game", entities=[c["player"]], columns=[c["stat"]],
                season_from=c["season"], season_to=c["season"],
                filters=[{"key": c["stat"], "op": "gte", "value": c["min"]}], limit=500))["rows"]
            if str(len(rows)) != t["answer"]:
                raise SystemExit(f"{t['key']}: the data now gives {len(rows)}, the task's answer is "
                                 f"{t['answer']}: the task set is stale")
            out[t["key"]] = len(rows)
        elif c["kind"] == "table" and t.get("answer"):
            out[t["key"]] = None
    return out


# ── Checks on the saved boards ──────────────────────────────────────────

def _sets_of(board, kind="player"):
    return [s for s in board.get("sets", []) if s.get("kind") == kind]


def _ids(s):
    return [m["id"] for m in s.get("members", [])]


def check_table(c, boards):
    want = set(c["players"])
    for b in boards:
        sets = {s["id"]: s for s in _sets_of(b) if want <= set(_ids(s))}
        for blk in b.get("blocks", []):
            st = blk.get("settings", {})
            if blk.get("type") != "table" or st.get("setId") not in sets:
                continue
            if st.get("dataset") != c["dataset"] or not set(c["columns"]) <= set(st.get("columns", [])):
                continue
            if st.get("groupBy", "none") != "none" or st.get("per", "game") != "game":
                continue
            # As tableSpec.buildSpec reads them: no last season = the latest,
            # no first season = the last five seasons (for a set).
            hi = st.get("seasonTo") or c["latest"]
            lo = st.get("seasonFrom") or max(hi - 4, 0)
            if lo <= c["seasonFrom"] and hi >= c["seasonTo"]:
                return True, "table found"
        if sets:
            reason = "a set holds both players, but no table bound to it shows the stats per game by season over the seasons"
            break
    else:
        reason = "no player set holds both players"
    return False, reason


def check_finder_chart(c, boards, truth):
    need = c["share"] * len(truth)
    good_sets = {}
    for b in boards:
        for s in _sets_of(b):
            ids = set(_ids(s))
            hit = len(ids & truth)
            if ids and hit >= need and hit >= c["share"] * len(ids):
                good_sets[s["id"]] = s
        for blk in b.get("blocks", []):
            st = blk.get("settings", {})
            if (blk.get("type") == "chart" and st.get("setId") in good_sets and st.get("chart") == c["chart"]
                    and {st.get("x"), st.get("y")} == set(c["axes"])):
                return True, "scatter of the found players"
    if not good_sets:
        return False, f"no set holds at least {c['share']:.0%} of the {len(truth)} players (and little else)"
    return False, "the set is there, but no scatter bound to it has usage and true shooting on its axes"


def check_tool(c, boards):
    for b in boards:
        sets = {s["id"]: s for s in _sets_of(b)}
        for blk in b.get("blocks", []):
            st = blk.get("settings", {})
            if blk.get("type") != "tool" or st.get("tool") != c["tool"]:
                continue
            s = sets.get(st.get("setId"))
            if not s or not s.get("members"):
                continue
            shown = st.get("member") if st.get("member") is not None else s["members"][0]["id"]
            if shown == c["player"] and st.get("season") == c["season"]:
                return True, "shot chart found"
    return False, "no shot chart block shows that player and season"


def _event_matches(ev, want):
    return all(ev.get(k) == v for k, v in want.items())


def check_events(c, events):
    for ev in events:
        if any(_event_matches(ev, w) for w in c["any"]):
            return True, ev["type"]
    return False, "no share link copied and no file exported"


def check_parse_finder(c, boards, events):
    if not any(e.get("type") == "parse" and e.get("ok") is True for e in events):
        return False, "the type-in box never filled the boxes"
    want = [tuple(x) for x in c["conditions"]]
    for b in boards:
        for blk in b.get("blocks", []):
            if blk.get("type") != "finder":
                continue
            have = {(x.get("stat"), x.get("op"), x.get("value")) for x in blk.get("settings", {}).get("conditions", [])
                    if x.get("type") == "value" and x.get("per", "game") == "game"}
            if all(w in have for w in want):
                return True, "finder run with both conditions"
    return False, "no finder block holds both conditions"


def check_task(task, record, events, truth):
    """(passed, reason) for the board/event check; (None, reason) when the task has none."""
    c = task["check"]
    boards = record.get("boards")
    if c["kind"] == "answer":
        return None, "answer only"
    if c["kind"] == "event":
        return check_events(c, events)
    if boards is None:
        return False, "the boards weren't saved at the end of the task"
    if c["kind"] == "table":
        return check_table(c, boards)
    if c["kind"] == "finder_chart":
        return check_finder_chart(c, boards, truth[task["key"]])
    if c["kind"] == "tool":
        return check_tool(c, boards)
    if c["kind"] == "parse_finder":
        return check_parse_finder(c, boards, events)
    raise ValueError(f"unknown check {c['kind']}")


def _norm(text):
    return " ".join(str(text or "").lower().replace("–", "-").replace("—", "-").replace("/", "-").split())


def check_answer(task, record):
    if not task.get("question"):
        return None
    return _norm(record.get("answer")) in {_norm(a) for a in task.get("answerAccept", [task["answer"]])}


def sus_score(items):
    if not items or len(items) != 10 or any(not isinstance(v, int) or not 1 <= v <= 5 for v in items):
        return None
    odd = sum(items[i] - 1 for i in range(0, 10, 2))
    even = sum(5 - items[i] for i in range(1, 10, 2))
    return (odd + even) * 2.5


def longest_gap(record, events):
    """Seconds of the longest stretch with nothing logged, from Start to the end of the task."""
    if not record.get("started") or not record.get("ended"):
        return None
    times = sorted([record["started"], record["ended"]] + [e["t"] for e in events
                                                           if record["started"] <= e["t"] <= record["ended"]])
    return max((b - a) / 1000 for a, b in zip(times, times[1:])) if len(times) > 1 else None


# ── Grading and the report ──────────────────────────────────────────────

def grade(study, sessions, truth):
    tasks = {t["key"]: t for t in study["tasks"]}
    rows = []
    for s in sessions:
        by_task = defaultdict(list)
        for e in s.get("events", []):
            by_task[e.get("task")].append(e)
        for rec in s.get("tasks", []):
            task = tasks.get(rec["key"])
            if task is None:
                raise SystemExit(f"{s['_file']}: task {rec['key']} isn't in {study['taskSet']}")
            evs = by_task.get(rec["key"], [])
            outcome = rec.get("outcome")
            if outcome == "skipped":
                rows.append({"p": s["participant"], "task": rec["key"], "outcome": "skipped", "completed": None,
                             "check": None, "check_reason": "", "answer_ok": None, "seconds": None, "hints": 0,
                             "stuck": [], "seq": None, "events": [], "gap": None})
                continue
            passed, reason = check_task(task, rec, evs, truth)
            answer_ok = check_answer(task, rec)
            completed = (outcome in ("success", "help") and passed is not False and answer_ok is not False)
            rows.append({
                "p": s["participant"], "task": rec["key"], "outcome": outcome, "completed": completed,
                "check": passed, "check_reason": reason, "answer_ok": answer_ok, "answer": rec.get("answer", ""),
                "seconds": rec.get("seconds"), "hints": rec.get("hints", 0), "stuck": rec.get("stuck", []),
                "seq": rec.get("seq"), "events": evs, "gap": longest_gap(rec, evs),
            })
    return rows


def _med(values):
    v = [x for x in values if x is not None]
    return statistics.median(v) if v else None


def _mmss(sec):
    if sec is None:
        return "–"
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def _range(values, fmt=_mmss):
    v = [x for x in values if x is not None]
    if not v:
        return "–"
    return f"{fmt(_med(v))} ({fmt(min(v))}–{fmt(max(v))})"


def report(study, sessions, rows, pilots_left_out):
    tasks = study["tasks"]
    tags = dict(study["stuckTags"])
    people = sorted({s["participant"] for s in sessions})
    dates = sorted(str(s["started"])[:10] for s in sessions)
    lines = [
        "## Usability check: results",
        "",
        f"Task set `{study['taskSet']}`; {len(people)} participant{'s' if len(people) != 1 else ''} "
        f"({', '.join(people) or 'none'})" + (f", sessions {dates[0]} to {dates[-1]}" if dates else "")
        + f"; pilot sessions left out: {pilots_left_out}. Generated by `scripts/usability_summary.py` from the exported "
          "sessions; every number below is a count or a time from them.",
        "",
        "| Task | Completed | Success / after a hint / failed / skipped (moderator) | Board check passed | Answer right "
        "| Time, median (range) | Hints | Ease 1-7, median |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in tasks:
        r = [x for x in rows if x["task"] == t["key"]]
        if not r:
            continue
        done = [x for x in r if x["outcome"] != "skipped"]
        oc = Counter(x["outcome"] for x in r)
        checked = [x for x in done if x["check"] is not None]
        answered = [x for x in done if x["answer_ok"] is not None]
        lines.append(
            f"| {t['key']} {t['title']} | {sum(1 for x in done if x['completed'])} of {len(done)} "
            f"| {oc['success']} / {oc['help']} / {oc['fail']} / {oc['skipped']} "
            f"| {f'{sum(1 for x in checked if x['check'])} of {len(checked)}' if checked else '–'} "
            f"| {f'{sum(1 for x in answered if x['answer_ok'])} of {len(answered)}' if answered else '–'} "
            f"| {_range([x['seconds'] for x in done])} | {sum(x['hints'] for x in done)} "
            f"| {_med([x['seq'] for x in done]) if done else '–'} |")
    all_done = [x for x in rows if x["outcome"] != "skipped"]
    lines += ["", f"Completed overall: {sum(1 for x in all_done if x['completed'])} of {len(all_done)} attempts.", ""]

    stuck = defaultdict(lambda: {"n": 0, "people": set(), "tasks": set()})
    for x in rows:
        for st in x["stuck"]:
            d = stuck[st["tag"]]
            d["n"] += 1
            d["people"].add(x["p"])
            d["tasks"].add(x["task"])
    lines += ["### Where people got stuck (moderator's tags)", ""]
    if stuck:
        lines += ["| Stuck on | Times | Participants | Tasks |", "|---|---|---|---|"]
        for tag, d in sorted(stuck.items(), key=lambda kv: (-len(kv[1]["people"]), -kv[1]["n"], kv[0])):
            lines.append(f"| {tags.get(tag, tag)} | {d['n']} | {len(d['people'])} | {', '.join(sorted(d['tasks']))} |")
    else:
        lines.append("No stuck points were logged.")
    notes = [(x["p"], x["task"], st["tag"], st["note"]) for x in rows for st in x["stuck"] if st.get("note")]
    if notes:
        lines += ["", "Moderator's notes, as typed:", ""]
        lines += [f"- {p} {task} ({tags.get(tag, tag)}): {note}" for p, task, tag, note in notes]

    lines += ["", "### What the app logged during the tasks", "",
              "| Task | " + " | ".join(label for _, label in SIGNALS) + " | Longest pause, median (range) |",
              "|---|" + "---|" * len(SIGNALS) + "---|"]
    for t in tasks:
        r = [x for x in rows if x["task"] == t["key"] and x["outcome"] != "skipped"]
        if not r:
            continue
        counts = [sum(1 for x in r for e in x["events"] if e.get("type") == k) for k, _ in SIGNALS]
        lines.append(f"| {t['key']} | " + " | ".join(str(c) for c in counts) + f" | {_range([x['gap'] for x in r])} |")
    errors = Counter((e.get("message") or "")[:120] for x in rows for e in x["events"]
                     if e.get("type") in ("block_error", "error", "js_error"))
    if errors:
        lines += ["", "Error messages shown, by count:", ""]
        lines += [f"- {n} × {msg or '(no message)'}" for msg, n in errors.most_common()]

    scores = [(s["participant"], sus_score(s.get("sus"))) for s in sessions]
    valid = [v for _, v in scores if v is not None]
    lines += ["", "### System Usability Scale", ""]
    if valid:
        lines.append(f"SUS {statistics.mean(valid):.1f} on average (range {min(valid):.1f}–{max(valid):.1f}, "
                     f"n = {len(valid)}).")
    else:
        lines.append("No complete SUS questionnaire.")

    devices = Counter(f"{s['device']['width']} px, {s['device']['theme']}" for s in sessions if s.get("device"))
    lines += ["", "Screens: " + (", ".join(f"{k} ({n})" for k, n in sorted(devices.items())) or "–") + "."]

    disagree = [x for x in rows if x["outcome"] in ("success", "help") and not x["completed"]]
    disagree += [x for x in rows if x["outcome"] == "fail" and x["check"] is True and x["answer_ok"] is not False]
    lines += ["", "### Where the moderator and the checks disagree (look at these)", ""]
    if disagree:
        for x in disagree:
            why = [] if x["check"] is not False else [f"board check: {x['check_reason']}"]
            if x["answer_ok"] is False:
                why.append(f"answer “{x['answer']}” isn't an accepted one")
            if x["outcome"] == "fail":
                why.append("the checks passed but the moderator marked it failed")
            lines.append(f"- {x['p']} {x['task']}: moderator {x['outcome']}; " + "; ".join(why))
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="session JSON files or folders of them")
    ap.add_argument("--out", help="write the markdown here")
    ap.add_argument("--include-pilot", action="store_true")
    args = ap.parse_args(argv)
    study = load_tasks()
    sessions = load_sessions(args.paths)
    wrong_set = [s["_file"] for s in sessions if s.get("taskSet") != study["taskSet"]]
    if wrong_set:
        raise SystemExit(f"sessions from another task set: {', '.join(wrong_set)}")
    pilots = [s for s in sessions if s.get("pilot")]
    if not args.include_pilot:
        sessions = [s for s in sessions if not s.get("pilot")]
    seen = Counter(s["participant"] for s in sessions)
    twice = [p for p, n in seen.items() if n > 1]
    if twice:
        raise SystemExit(f"more than one session for {', '.join(twice)}: keep one file per participant")
    rows = grade(study, sessions, truths(study))
    text = report(study, sessions, rows, 0 if args.include_pilot else len(pilots))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
