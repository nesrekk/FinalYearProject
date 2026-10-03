"""
How well the Workbench's "type it in English" box fills the Player Finder's
boxes (round 7 step 9), measured on the sentences in
api/workbench_parse_sentences.json (written before the prompt existed).

    python3 workbench_parse_eval.py --split dev            # tune on these (prints, writes nothing)
    python3 workbench_parse_eval.py --split test --runs 3 --write
                                                           # the stated number: api/workbench_parse_eval.json

Each sentence goes through exactly what POST /workbench/parse does
(workbench_parse_lib.parse: the prompt, Gemini, then to_spec's checks and the
finder's defaults); the expected answer goes through the same defaults
(workbench_parse_lib.finalize), and the two are compared box by box:

    data set      season stats or game logs
    scope         one result per player-season, or over the seasons combined
    seasons       first and last season (null = the data's whole range)
    games floor   min_games (after the 40-game default)
    filters       home/away, wins/losses, opponent, minutes floor (all of them)
    conditions    every condition, as a set (stat, comparison, number, per,
                  attempts floor, count, tests): order doesn't matter
    flag          something was reported (not understood, or dropped by the
                  server) exactly when the sentence asks for something the
                  boxes can't hold

"All right" = every box and the flag right. Sentences whose expected
conditions are null ("or") are scored on the flag and the other boxes only.
A sentence the model or the server couldn't turn into boxes counts as wrong
on every box. Network: Google's Gemini API (key in api/.env; free tier, so
calls are spaced out and retried after a 429).
"""

import argparse
import datetime
import hashlib
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

import workbench_parse_lib as P                  # noqa: E402
from routers.workbench import _dataset_meta      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SENTENCES = os.path.join(HERE, "..", "api", "workbench_parse_sentences.json")
OUT = os.path.join(HERE, "..", "api", "workbench_parse_eval.json")
BOXES = ("dataset", "scope", "seasons", "min_games", "filters", "conditions", "flag")


def _norm(spec):
    """A spec reduced to what the boxes show, for comparing."""
    meta = _dataset_meta()[spec["dataset"]]
    lo = spec.get("season_from") if spec.get("season_from") is not None else meta["from"]
    hi = spec.get("season_to") if spec.get("season_to") is not None else meta["to"]

    def test_key(t):
        return json.dumps([t["stat"], t["op"], t["value"]])

    conds = []
    for c in spec["conditions"]:
        if c["type"] == "value":
            conds.append(json.dumps(["value", c["stat"], c.get("per", "game"), c["op"], c["value"], c.get("min_n")]))
        else:
            tests = sorted(test_key(t) for t in c["tests"])
            conds.append(json.dumps([c["type"], c.get("count_op", "gte") if c["type"] == "count" else "gte",
                                     c["count"], tests]))
    return {
        "dataset": spec["dataset"], "scope": spec["scope"], "seasons": [max(lo, meta["from"]), min(hi, meta["to"])],
        "min_games": spec.get("min_games"),
        "filters": sorted(json.dumps([f["key"], f["op"], f["value"]]) for f in spec.get("filters", [])),
        "conditions": sorted(conds),
    }


def score(item, got, failure=None):
    """{box: True/False/None} for one sentence; `got` is parse()'s answer, or
    None with `failure` = the ParseError's {status, not_understood, dropped}."""
    exp = item["expect"]
    flag_expected = item["unsupported"]
    if got is None:
        if failure and failure["status"] == 422 and exp["conditions"] is None:
            # Nothing could be filled: right only if the sentence was all "can't do" and it said so.
            reported = bool(failure["not_understood"] or failure["dropped"])
            return {b: (reported == flag_expected if b == "flag" else None) for b in BOXES}
        return {b: (None if b in ("conditions", "min_games") and exp["conditions"] is None else False)
                for b in BOXES}
    reported = bool(got["not_understood"] or got["dropped"])
    g = _norm(got["spec"])
    out = {"flag": reported == flag_expected}
    if exp["conditions"] is None:
        # "or": only the flag and the boxes around the conditions are scored.
        e = _norm({**exp, "conditions": []})
        out["conditions"] = out["min_games"] = None
    else:
        e = _norm(P.finalize(exp)[0])
        out["conditions"] = g["conditions"] == e["conditions"]
        out["min_games"] = g["min_games"] == e["min_games"]
    for b in ("dataset", "scope", "seasons", "filters"):
        out[b] = g[b] == e[b]
    return out


def run_once(items, model, today, sleep):
    results = []
    for k, item in enumerate(items):
        got, err, failure = None, None, None
        for attempt in range(4):
            try:
                got = P.parse(item["text"], model=model, today=today)
                break
            except P.ParseError as e:
                if e.status in (429, 502, 504) and attempt < 3:
                    wait = 65 if e.status == 429 else 10
                    print(f"  {item['id']}: {e.status} {e}; waiting {wait} s", flush=True)
                    time.sleep(wait)
                    continue
                err = f"{e.status}: {e}"
                failure = {"status": e.status, "not_understood": e.not_understood, "dropped": e.dropped}
                got = None
                break
        boxes = score(item, got, failure)
        ok = all(v is not False for v in boxes.values())
        results.append({
            "id": item["id"], "text": item["text"], "unsupported": item["unsupported"], "all_right": ok,
            "boxes": boxes, "error": err,
            "not_understood": got["not_understood"] if got else (failure or {}).get("not_understood"),
            "dropped": got["dropped"] if got else (failure or {}).get("dropped"),
            "spec": got["spec"] if got else None, "sentence": got["sentence"] if got else None,
            "seconds": got["seconds"] if got else None,
        })
        wrong = [b for b, v in boxes.items() if v is False]
        print(f"  {item['id']} {'ok ' if ok else 'BAD'} {', '.join(wrong) or ''}{(' ' + err) if err else ''}", flush=True)
        if k < len(items) - 1:
            time.sleep(sleep)
    return results


def summarise(results):
    n = len(results)
    per_box = {}
    for b in BOXES:
        scored = [r["boxes"][b] for r in results if r["boxes"][b] is not None]
        per_box[b] = {"right": sum(scored), "of": len(scored)}
    secs = [r["seconds"] for r in results if r["seconds"] is not None]
    unsup = [r for r in results if r["unsupported"]]
    return {
        "n": n, "all_right": sum(r["all_right"] for r in results),
        "boxes_right": sum(all(v is not False for b, v in r["boxes"].items() if b != "flag") for r in results),
        "failed": sum(r["error"] is not None for r in results),
        "per_box": per_box,
        "unsupported_reported": {"right": sum(r["boxes"]["flag"] for r in unsup), "of": len(unsup)},
        "false_reports": sum(1 for r in results if not r["unsupported"] and r["boxes"]["flag"] is False),
        "median_seconds": round(statistics.median(secs), 2) if secs else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--ids", help="comma-separated sentence ids (development only)")
    ap.add_argument("--model", default=P.MODEL)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--sleep", type=float, default=4.5, help="seconds between calls (free-tier per-minute limit)")
    ap.add_argument("--write", action="store_true", help=f"write {os.path.relpath(OUT)} (test split, chosen model)")
    ap.add_argument("--dump", help="also write the full results to this file")
    a = ap.parse_args()
    if a.write and (a.split != "test" or a.ids or a.model != P.MODEL):
        sys.exit("--write is for the test split, all of it, with the model the app uses.")
    if not P.api_key():
        sys.exit("No GEMINI_API_KEY in api/.env.")
    data = json.load(open(SENTENCES))
    items = [s for s in data["sentences"] if a.split == "all" or s["split"] == a.split]
    if a.ids:
        wanted = set(a.ids.split(","))
        items = [s for s in items if s["id"] in wanted]
    today = datetime.date.today()
    lib_sha = hashlib.sha256(open(P.__file__, "rb").read()).hexdigest()[:12]    # the code that runs
    runs = []
    for i in range(a.runs):
        print(f"run {i + 1} of {a.runs}: {len(items)} sentences, {a.model}, prompt {P.prompt_version(today)}", flush=True)
        res = run_once(items, a.model, today, a.sleep)
        s = summarise(res)
        print(f"  all right {s['all_right']}/{s['n']}, boxes right {s['boxes_right']}/{s['n']}, failed {s['failed']}; "
              + ", ".join(f"{b} {v['right']}/{v['of']}" for b, v in s["per_box"].items()), flush=True)
        runs.append({"summary": s, "results": res})
    out = {
        "about": "Accuracy of the Workbench's English box on the test sentences of api/workbench_parse_sentences.json "
                 "(scripts/workbench_parse_eval.py; the boxes it compares are listed in its docstring).",
        "evaluated": today.isoformat(), "split": a.split, "model": a.model, "prompt_version": P.prompt_version(today),
        "lib_sha256": lib_sha,
        "sentences_written": data["written"], "runs": len(runs),
        "all_right_by_run": [r["summary"]["all_right"] for r in runs],
        "n": len(items),
        "runs_detail": runs,
    }
    if a.dump:
        json.dump(out, open(a.dump, "w"), indent=1, ensure_ascii=False)
    if a.write:
        json.dump(out, open(OUT, "w"), indent=1, ensure_ascii=False)
        print(f"wrote {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
