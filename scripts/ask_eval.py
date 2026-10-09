"""
How well "ask in English everywhere" turns a sentence into the right action
(round 10 step 7), measured on the sentences of api/ask_sentences.json
(written before the prompt existed, step 10-6; never edited to suit an answer).

    python3 ask_eval.py --split dev                 # tune on these (prints, writes nothing)
    python3 ask_eval.py --split test --runs 3 --write
                                                    # the stated number: api/ask_eval.json
    python3 ask_eval.py --resume                    # ask only the sentence-runs a dead daily
                                                    # quota left unanswered (prompt, code, model
                                                    # and seasons must be unchanged; see below)

Each sentence goes through exactly what POST /ask does (ask_lib.ask: the
prompt, Gemini, then to_action's checks; a Finder sentence also goes through
the Finder box's own parse), and the result is scored by the rules the
sentences file states (its "actions" / "normalisation" sections):

    open_page       page and hash equal; params equal as a whole after normalisation
                    (numbers as numbers, strings case- and accent-insensitive, list keys as
                    sets); the free keys (compare a/b, shotcharts player, pg) ignored
    build_board     the sets equal (kinds; ids / codes as sets); every expected block
                    matched by one produced block of its type whose every named key is
                    equal (expected columns ⊆ produced columns); extra blocks free
    run_finder      as the Finder box is scored (scripts/workbench_parse_eval.py): data
                    set, scope, seasons, games floor, filters, conditions as a set, flag
    open_live_game  team equal (null = today's list)
    ask             about and the set of options equal
    refuse          the reason equal

Season tokens in the expectations ('current', 'last', 'current-N') and the date
token 'yesterday' are resolved on the day the evaluation runs through
api/current_season.py and the NBA's date (R10-009), so the same file scores
right on both sides of opening night.

Per action type the report counts right, wrong, refused (the engine refused or
asked when the expectation was something else) and failed (no answer: the
model's error, a 4xx from the server). Network: Google's Gemini API (key in
api/.env; free tier, so calls are spaced out and retried after a per-minute
429). When Google says the day's free quota is used up, the run stops asking
and marks every remaining sentence failed (the stored file says so in
`failed_by_run`); `--resume` then asks only those sentence-runs and never an
answered one, and only while the prompt (as it was on the evaluation day), the
engine's code, the model and the seasons are the stored ones, so a resume can
only complete the same evaluation, never retune it. Each resume is recorded in
the file's `resumed` list.
"""

import argparse
import datetime
import hashlib
import json
import os
import statistics
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "api"))
sys.path.insert(0, HERE)

import ask_lib as A                                  # noqa: E402
import workbench_parse_lib as P                      # noqa: E402
from workbench_parse_eval import _norm as finder_norm   # noqa: E402

SENTENCES = os.path.join(HERE, "..", "api", "ask_sentences.json")
OUT = os.path.join(HERE, "..", "api", "ask_eval.json")
ACTIONS = ("open_page", "build_board", "run_finder", "open_live_game", "ask", "refuse")
FREE_KEYS = {"compare": {"a", "b"}, "shotcharts": {"player"}}
LIST_KEYS = {("gamefinder", "f"), ("statline", "line"), ("breakouts", "stats"), ("plays", "per"), ("builder", "w")}
TOKENS = ("current", "last")


# ─── the expectation on the day it is scored ────────────────────────────────

def resolve_season(v, seasons):
    current, last = seasons
    if v == "current":
        return current
    if v == "last":
        return last
    if isinstance(v, str) and v.startswith("current-"):
        return current - int(v.split("-")[1])
    return v


def resolve_expect(exp, seasons, today):
    """Tokens → values for one expected action (a copy)."""
    exp = json.loads(json.dumps(exp))
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    if exp["action"] == "open_page":
        for k, v in list(exp["params"].items()):
            if v in TOKENS or (isinstance(v, str) and v.startswith("current-")):
                s = resolve_season(v, seasons)
                exp["params"][k] = f"{s - 1}-{str(s)[-2:]}" if exp["page"] == "shotcharts" and k == "season" else s
            elif v == "yesterday":
                exp["params"][k] = yesterday
    elif exp["action"] == "build_board":
        for b in exp["blocks"]:
            for k in ("seasonFrom", "seasonTo"):
                if k in b:
                    b[k] = resolve_season(b[k], seasons)
    elif exp["action"] == "run_finder":
        for k in ("season_from", "season_to"):
            exp["spec"][k] = resolve_season(exp["spec"][k], seasons)
    return exp


# ─── normalisation and comparison ───────────────────────────────────────────

def fold(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def norm_params(page, params):
    out = {}
    for k, v in (params or {}).items():
        if k == "pg" or k in FREE_KEYS.get(page, ()):
            continue
        if (page, k) in LIST_KEYS:
            out[k] = frozenset(fold(x) for x in str(v).split(",") if x.strip())
        else:
            out[k] = fold(v)
    return out


def score_page(exp, got):
    if got.get("action") != "open_page":
        return False
    return (exp["page"] == got.get("page") and (exp.get("hash") or None) == (got.get("hash") or None)
            and norm_params(exp["page"], exp.get("params")) == norm_params(exp["page"], got.get("params")))


def norm_sets(sets):
    return sorted((s["kind"], tuple(sorted(fold(x) for x in (s.get("ids") or s.get("codes") or []))))
                  for s in sets or [])


def block_matches(exp, got, seasons):
    if exp["type"] != got.get("type"):
        return False
    for k, v in exp.items():
        if k == "type":
            continue
        if k == "columns":
            if not set(v) <= set(got.get("columns") or []):
                return False
        elif k == "spec":
            if got.get("spec") is None or not finder_equal(v, got["spec"], seasons):
                return False
        else:
            gv = got.get(k)
            if gv is None:
                if v is not None:
                    return False
            elif v is None or fold(v) != fold(gv):
                return False
    return True


def score_board(exp, got, seasons):
    if got.get("action") != "build_board":
        return False
    if norm_sets(exp["sets"]) != norm_sets(got.get("sets")):
        return False
    produced = list(got.get("blocks") or [])
    for b in exp["blocks"]:
        match = next((i for i, g in enumerate(produced) if block_matches(b, g, seasons)), None)
        if match is None:
            return False
        produced.pop(match)
    return True


def finder_equal(exp_spec, got_spec, seasons):
    """The Finder box's comparison (its eval's boxes, both sides after finalize)."""
    e = finder_norm(P.finalize(json.loads(json.dumps(exp_spec)))[0])
    g = finder_norm(got_spec)
    return e == g


def finder_boxes(exp, got, seasons):
    """{box: ok} for a run_finder expectation."""
    boxes = {"dataset": False, "scope": False, "seasons": False, "min_games": False, "filters": False,
             "conditions": False, "flag": False}
    if got.get("action") != "run_finder" or not got.get("spec"):
        return boxes
    e = finder_norm(P.finalize(json.loads(json.dumps(exp["spec"])))[0])
    g = finder_norm(got["spec"])
    for b in ("dataset", "scope", "seasons", "min_games", "filters", "conditions"):
        boxes[b] = e[b] == g[b]
    boxes["flag"] = bool(exp["flag"]) == bool(got.get("flag"))
    return boxes


def score(item, got, seasons, today):
    """→ (right: bool, detail)."""
    exp = resolve_expect(item["expect"], seasons, today)
    a = exp["action"]
    if got is None:
        return False, {"failed": True}
    if a == "open_page":
        return score_page(exp, got), {"got": got.get("action")}
    if a == "build_board":
        return score_board(exp, got, seasons), {"got": got.get("action")}
    if a == "run_finder":
        boxes = finder_boxes(exp, got, seasons)
        return all(boxes.values()), {"got": got.get("action"), "boxes": boxes}
    if a == "open_live_game":
        return got.get("action") == "open_live_game" and (exp["team"] or None) == (got.get("team") or None), {"got": got.get("action")}
    if a == "ask":
        ids = sorted(o["id"] if isinstance(o, dict) else o for o in (got.get("options") or []))
        return got.get("action") == "ask" and got.get("about") == exp["about"] and ids == sorted(exp["options"]), {"got": got.get("action")}
    if a == "refuse":
        return got.get("action") == "refuse" and got.get("reason") == exp["reason"], {"got": got.get("action"), "reason": got.get("reason")}
    raise ValueError(a)


# ─── the run ────────────────────────────────────────────────────────────────

def lib_sha():
    """A hash of the engine's code (ask_lib.py + ask_pages.py): a changed engine is a new evaluation."""
    api = os.path.dirname(A.__file__)
    return hashlib.sha256(open(A.__file__, "rb").read() + open(os.path.join(api, "ask_pages.py"), "rb").read()).hexdigest()[:12]


def day_quota_gone(e):
    """Google's 429 for the day's free quota (workbench_parse_lib._quota_message), not the per-minute one."""
    return e.status == 429 and "for today" in str(e)


def resume_blockers(ev, model, sha, seasons):
    """Why a stored evaluation may not be resumed (empty = it may). A resume only fills in sentence-runs that got no
    answer; it must be the same test under the same prompt, code, model and seasons."""
    why = []
    if ev.get("split") != "test":
        why.append("the stored file is not the test split")
    if ev.get("model") != model:
        why.append(f"the model changed ({ev.get('model')} stored, {model} now)")
    if ev.get("lib_sha256") != sha:
        why.append("ask_lib.py or ask_pages.py changed since: a new evaluation, not a resume")
    try:
        day = datetime.date.fromisoformat(ev["evaluated"])
        stored_seasons = tuple(ev["seasons"])
    except (KeyError, TypeError, ValueError):
        return why + ["the stored file has no evaluation day or seasons"]
    if A.prompt_version(day, stored_seasons) != ev.get("prompt_version"):
        why.append("the prompt changed since: a new evaluation, not a resume")
    if stored_seasons != tuple(seasons):
        why.append(f"the seasons moved ({list(stored_seasons)} stored, {list(seasons)} now): a new evaluation, not a resume")
    if not any(r["outcome"] == "failed" for run in ev.get("runs_detail", []) for r in run["results"]):
        why.append("nothing to resume: every sentence-run was answered")
    return why


def _slim(action):
    """The action without the importable board (kept small in the stored file)."""
    if not isinstance(action, dict):
        return action
    out = {k: v for k, v in action.items() if k not in ("board", "href", "message")}
    if "then" in out:
        out["then"] = _slim(out["then"])
    if "options" in out:
        out["options"] = [o["id"] if isinstance(o, dict) else o for o in out["options"]]
    return out


def run_once(items, model, today, seasons, sleep):
    results = []
    quota_gone = None
    for k, item in enumerate(items):
        got, err, failure = None, None, None
        if quota_gone:
            err, failure = quota_gone
        for attempt in range(4 if not quota_gone else 0):
            try:
                got = A.ask(item["text"], model=model, today=today, seasons=seasons)
                break
            except P.ParseError as e:
                if day_quota_gone(e):
                    print(f"  {item['id']}: {e.status} {e}; the rest of this run is unanswered (--resume tomorrow)", flush=True)
                    err = f"{e.status}: {e}"
                    failure = {"status": e.status, "not_understood": [], "dropped": []}
                    quota_gone = (err, failure)
                    got = None
                    break
                if e.status in (429, 502, 504) and attempt < 3:
                    wait = 65 if e.status == 429 else 10
                    print(f"  {item['id']}: {e.status} {e}; waiting {wait} s", flush=True)
                    time.sleep(wait)
                    continue
                err = f"{e.status}: {e}"
                failure = {"status": e.status, "not_understood": e.not_understood, "dropped": e.dropped}
                got = None
                break
        action = got["action"] if got else None
        right, detail = score(item, action, seasons, today)
        expected = item["expect"]["action"]
        produced = action["action"] if action else None
        outcome = ("right" if right else "failed" if action is None else
                   "refused" if produced in ("refuse", "ask") and expected not in ("refuse", "ask") else "wrong")
        results.append({
            "id": item["id"], "text": item["text"], "expected": expected, "produced": produced, "outcome": outcome,
            "right": right, "detail": detail, "error": err,
            "action": _slim(action), "preview": got["preview"] if got else None,
            "notes": got["notes"] if got else (failure or {}).get("dropped"),
            "guessed": got["guessed"] if got else None,
            "not_understood": got["not_understood"] if got else (failure or {}).get("not_understood"),
            "seconds": got["seconds"] if got else None,
        })
        mark = "ok " if right else "BAD"
        extra = "" if right else f" expected {expected} {json.dumps(resolve_expect(item['expect'], seasons, today), ensure_ascii=False)[:160]} | got {json.dumps(_slim(action), ensure_ascii=False)[:220] if action else err}"
        print(f"  {item['id']} {mark} {outcome}{extra}", flush=True)
        if k < len(items) - 1 and not quota_gone:
            time.sleep(sleep)
    return results


def print_summary(s):
    print(f"  right {s['right']}/{s['n']}; false refusals {s['false_refusals']}, missed refusals {s['missed_refusals']}, "
          f"failed {s['failed']}; median {s['median_seconds']} s", flush=True)
    for act, v in s["by_action"].items():
        print(f"    {act:15s} right {v['right']}/{v['n']}  wrong {v['wrong']}  refused {v['refused']}  failed {v['failed']}")


def write_out(out, a):
    out["right_by_run"] = [r["summary"]["right"] for r in out["runs_detail"]]
    out["failed_by_run"] = [r["summary"]["failed"] for r in out["runs_detail"]]
    out["runs"] = len(out["runs_detail"])
    if a.dump:
        json.dump(out, open(a.dump, "w"), indent=1, ensure_ascii=False)
    if a.write or a.resume:
        json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
        print(f"wrote {os.path.relpath(OUT)}")
    if sum(out["failed_by_run"]):
        print(f"  {sum(out['failed_by_run'])} sentence-runs unanswered ({out['failed_by_run']} by run): "
              f"`ask_eval.py --resume` asks only those, once Google's daily quota is back (midnight Pacific).")


def resume(a, data, items, today, seasons, sha):
    """Ask only the failed sentence-runs of the stored file; everything answered stays as it was."""
    ev = json.load(open(OUT))
    why = resume_blockers(ev, a.model, sha, seasons)
    if why:
        sys.exit("cannot resume: " + "; ".join(why))
    by_id = {s["id"]: s for s in items}
    assert {r["id"] for run in ev["runs_detail"] for r in run["results"]} == set(by_id), "the sentences changed"
    asked = 0
    for i, run in enumerate(ev["runs_detail"]):
        todo = [by_id[r["id"]] for r in run["results"] if r["outcome"] == "failed"]
        if not todo:
            continue
        print(f"run {i + 1} of {ev['runs']}: resuming {len(todo)} unanswered sentences, {a.model}, "
              f"prompt {ev['prompt_version']}, today {today}, seasons {seasons}", flush=True)
        fresh = {r["id"]: r for r in run_once(todo, a.model, today, seasons, a.sleep)}
        run["results"] = [fresh.get(r["id"], r) for r in run["results"]]
        run["summary"] = summarise(run["results"])
        print_summary(run["summary"])
        asked += len(todo)
        if any(r["outcome"] == "failed" and "for today" in (r["error"] or "") for r in fresh.values()):
            print("  the day's quota ran out again; the remaining runs are left for another --resume", flush=True)
            break
    ev.setdefault("resumed", []).append({"day": today.isoformat(), "asked": asked,
                                        "unanswered_after": sum(r["summary"]["failed"] for r in ev["runs_detail"])})
    write_out(ev, a)


def summarise(results):
    by_action = {}
    for a in ACTIONS:
        rs = [r for r in results if r["expected"] == a]
        if not rs:
            continue
        by_action[a] = {"n": len(rs), "right": sum(r["right"] for r in rs),
                        "wrong": sum(r["outcome"] == "wrong" for r in rs),
                        "refused": sum(r["outcome"] == "refused" for r in rs),
                        "failed": sum(r["outcome"] == "failed" for r in rs)}
    secs = [r["seconds"] for r in results if r["seconds"] is not None]
    refusals = [r for r in results if r["expected"] == "refuse"]
    return {
        "n": len(results), "right": sum(r["right"] for r in results),
        "by_action": by_action,
        "false_refusals": sum(r["outcome"] == "refused" for r in results),
        "missed_refusals": sum(1 for r in refusals if r["produced"] not in ("refuse", None)),
        "failed": sum(r["outcome"] == "failed" for r in results),
        "median_seconds": round(statistics.median(secs), 2) if secs else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--ids", help="comma-separated sentence ids (development only)")
    ap.add_argument("--model", default=A.MODEL)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=4.5, help="seconds between sentences (free-tier per-minute limit)")
    ap.add_argument("--write", action="store_true", help=f"write {os.path.relpath(OUT)} (test split, all of it, the app's model)")
    ap.add_argument("--dump", help="also write the full results to this file")
    ap.add_argument("--resume", action="store_true",
                    help=f"ask only the sentence-runs of {os.path.relpath(OUT)} that got no answer (a dead daily quota); "
                         "refuses when the prompt, the engine's code, the model or the seasons changed")
    a = ap.parse_args()
    if a.resume:
        a.split, a.write = "test", False
        if a.ids or a.runs != 1:
            sys.exit("--resume takes no --ids or --runs: it completes the stored runs.")
    if (a.write or a.resume) and (a.split != "test" or a.ids or a.model != A.MODEL):
        sys.exit("--write is for the test split, all of it, with the model the app uses.")
    if not A.api_key():
        sys.exit("No GEMINI_API_KEY in api/.env.")
    data = json.load(open(SENTENCES))
    items = [s for s in data["sentences"] if a.split == "all" or s["split"] == a.split]
    if a.ids:
        wanted = set(a.ids.split(","))
        items = [s for s in items if s["id"] in wanted]
    today = A.today_nba()
    seasons = A.seasons_today()
    sha = lib_sha()
    if a.resume:
        resume(a, data, items, today, seasons, sha)
        return
    runs = []
    for i in range(a.runs):
        print(f"run {i + 1} of {a.runs}: {len(items)} sentences, {a.model}, prompt {A.prompt_version(today, seasons)}, "
              f"today {today}, seasons {seasons}", flush=True)
        res = run_once(items, a.model, today, seasons, a.sleep)
        s = summarise(res)
        print_summary(s)
        runs.append({"summary": s, "results": res})
        if any(r["outcome"] == "failed" and "for today" in (r["error"] or "") for r in res) and i < a.runs - 1:
            print("  the day's quota is used up: the remaining runs are recorded as unanswered (--resume tomorrow)", flush=True)
            unanswered = [{**r, "right": False, "outcome": "failed", "produced": None, "action": None, "preview": None,
                           "notes": None, "guessed": None, "not_understood": None, "seconds": None,
                           "detail": {"failed": True}, "error": "not asked: the day's free quota was used up"} for r in res]
            for _ in range(i + 1, a.runs):
                runs.append({"summary": summarise(unanswered), "results": unanswered})
            break
    out = {
        "about": "Accuracy of 'ask in English everywhere' on the test sentences of api/ask_sentences.json "
                 "(scripts/ask_eval.py; the rules it scores by are in that file's 'actions' section). A sentence-run "
                 "marked failed got no answer (Google's daily free quota); --resume asks only those, under the same prompt.",
        "evaluated": today.isoformat(), "split": a.split, "model": a.model,
        "prompt_version": A.prompt_version(today, seasons), "seasons": list(seasons), "lib_sha256": sha,
        "sentences_written": data["written"], "runs": len(runs),
        "right_by_run": [r["summary"]["right"] for r in runs], "failed_by_run": [r["summary"]["failed"] for r in runs],
        "n": len(items), "resumed": [],
        "runs_detail": runs,
    }
    write_out(out, a)


if __name__ == "__main__":
    main()
