"""
live_spike.py
==============
Round 10 step 1 (docs/LIVE_GAME.md): record what ESPN's public site API does while a game is on, and measure it.
Nothing here touches the database.

    cd scripts && python3 live_spike.py record --event 401898395 --minutes 170 [--interval 15] [--out DIR]
    cd scripts && python3 live_spike.py analyze live_data/live_spike/401898395

`record` polls the game's summary (the same endpoint scripts/espn_summary.py maps into pbp_events) and the
scoreboard of its US date every `--interval` seconds for `--minutes`, saves every answer gzipped under
live_data/live_spike/<event>/ (gitignored) and appends one row per poll to log.jsonl: the wall time, each call's
latency and size, the number of plays, the last play's id / period / clock / wallclock, the header's status, the
scores, the length of ESPN's own win-probability array, and what the scoreboard says for the same game. On a Mac
it runs `caffeinate -i` for its own lifetime (idle sleep stopped the 2026-10-09 recording for most of the game;
a closed lid still sleeps).

`analyze` turns log.jsonl into the numbers docs/LIVE_GAME.md quotes: how often the plays and the score change,
the delay between a play's wallclock and the poll that first carried it, payload sizes, failures, and every
status transition seen on the summary and on the scoreboard (in progress, end of period, halftime, final).
"""

import argparse
import gzip
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
TIMEOUT = 5.0
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "live_data" / "live_spike"


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _get(url, params):
    """(json or None, latency seconds, bytes, error or None)."""
    t0 = time.perf_counter()
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        dt = time.perf_counter() - t0
        if r.status_code != 200:
            return None, dt, len(r.content), f"http {r.status_code}"
        return r.json(), dt, len(r.content), None
    except Exception as exc:
        return None, time.perf_counter() - t0, 0, type(exc).__name__


def _status(comp):
    st = (comp or {}).get("status") or {}
    kind = st.get("type") or {}
    return {"name": kind.get("name"), "state": kind.get("state"), "completed": kind.get("completed"),
            "detail": kind.get("shortDetail") or kind.get("detail"), "period": st.get("period"),
            "clock": st.get("displayClock")}


def summary_row(s):
    """What one summary answer says, for the log."""
    comp = s["header"]["competitions"][0]
    plays = s.get("plays") or []
    wp = s.get("winprobability") or []
    last = plays[-1] if plays else {}
    scores = {c["homeAway"]: c.get("score") for c in comp.get("competitors", [])}
    box = (s.get("boxscore") or {}).get("players") or []
    n_box = sum(len(b.get("athletes") or []) for t in box for b in (t.get("statistics") or []))
    return {
        "status": _status(comp),
        "n_plays": len(plays),
        "last_play": {"id": last.get("id"), "seq": last.get("sequenceNumber"), "period": (last.get("period") or {}).get("number"),
                      "clock": (last.get("clock") or {}).get("displayValue"), "wallclock": last.get("wallclock"),
                      "type": (last.get("type") or {}).get("text"), "home": last.get("homeScore"), "away": last.get("awayScore")},
        "score": scores,
        "n_wp": len(wp),
        "last_wp": (wp[-1] if wp else None),
        "n_box_players": n_box,
        "flags": {k: comp.get(k) for k in ("liveAvailable", "playByPlaySource", "boxscoreSource", "wallclockAvailable")},
        "top_keys": sorted(s.keys()),
    }


def scoreboard_row(sb, event):
    for e in sb.get("events") or []:
        if str(e.get("id")) == str(event):
            comp = e["competitions"][0]
            return {"status": _status(comp), "score": {c["homeAway"]: c.get("score") for c in comp.get("competitors", [])},
                    "playByPlayAvailable": comp.get("playByPlayAvailable"), "n_events": len(sb.get("events") or [])}
    return {"status": None, "n_events": len(sb.get("events") or [])}


def record(args):
    out = Path(args.out) / str(args.event)
    (out / "summary").mkdir(parents=True, exist_ok=True)
    (out / "scoreboard").mkdir(parents=True, exist_ok=True)
    log = open(out / "log.jsonl", "a", encoding="utf-8")
    end = time.time() + args.minutes * 60
    n = 0
    keeper = None
    if shutil.which("caffeinate"):   # the Mac's idle sleep ate most of HOU-DAL on 2026-10-09: hold it awake while recording
        keeper = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    print(f"recording event {args.event} every {args.interval} s for {args.minutes} min -> {out}"
          f"{' (caffeinate -i holds off idle sleep; the lid must stay open)' if keeper else ''}", flush=True)
    while time.time() < end:
        t = utc_now()
        stamp = t.strftime("%H%M%S")
        row = {"t": iso(t), "i": n}
        s, dt, nbytes, err = _get(f"{SITE}/summary", {"event": str(args.event)})
        row["summary"] = {"latency": round(dt, 3), "bytes": nbytes, "error": err}
        if s is not None:
            try:
                row["summary"].update(summary_row(s))
            except Exception as exc:   # an unexpected shape is a finding, not a crash
                row["summary"]["error"] = f"shape: {type(exc).__name__}: {exc}"
            with gzip.open(out / "summary" / f"{stamp}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(s, f, separators=(",", ":"))
        sb, dt, nbytes, err = _get(f"{SITE}/scoreboard", {"dates": args.date, "limit": 200})
        row["scoreboard"] = {"latency": round(dt, 3), "bytes": nbytes, "error": err}
        if sb is not None:
            try:
                row["scoreboard"].update(scoreboard_row(sb, args.event))
            except Exception as exc:
                row["scoreboard"]["error"] = f"shape: {type(exc).__name__}: {exc}"
            with gzip.open(out / "scoreboard" / f"{stamp}.json.gz", "wt", encoding="utf-8") as f:
                json.dump(sb, f, separators=(",", ":"))
        log.write(json.dumps(row) + "\n")
        log.flush()
        sm, sc = row["summary"], row["scoreboard"]
        print(f"{row['t']} #{n:<4} summary {sm.get('error') or 'ok':<12} {sm.get('latency')}s {sm.get('bytes')}B "
              f"plays={sm.get('n_plays')} wp={sm.get('n_wp')} status={(sm.get('status') or {}).get('name')} "
              f"{(sm.get('status') or {}).get('period')} {(sm.get('status') or {}).get('clock')} score={sm.get('score')} "
              f"| scoreboard {sc.get('error') or 'ok'} {(sc.get('status') or {}).get('name')} "
              f"{(sc.get('status') or {}).get('period')} {(sc.get('status') or {}).get('clock')} {sc.get('score')}", flush=True)
        n += 1
        time.sleep(max(0.0, args.interval - (utc_now() - t).total_seconds()))
    log.close()


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _fmt(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def analyze(args):
    folder = Path(args.folder)
    rows = [json.loads(line) for line in open(folder / "log.jsonl", encoding="utf-8") if line.strip()]
    if not rows:
        print("empty log")
        return
    ok = [r for r in rows if r["summary"].get("error") is None]
    print(f"polls: {len(rows)} from {rows[0]['t']} to {rows[-1]['t']}; summary ok {len(ok)}, "
          f"errors {len(rows) - len(ok)} {sorted({r['summary'].get('error') for r in rows if r['summary'].get('error')})}")
    sb_ok = [r for r in rows if r["scoreboard"].get("error") is None]
    print(f"scoreboard ok {len(sb_ok)}, errors {len(rows) - len(sb_ok)}")
    for key in ("summary", "scoreboard"):
        lat = [r[key]["latency"] for r in rows if r[key].get("error") is None]
        size = [r[key]["bytes"] for r in rows if r[key].get("error") is None]
        if lat:
            print(f"{key}: latency median {_fmt(statistics.median(lat), 2)} s, p90 {_fmt(_pct(lat, .9), 2)}, max {_fmt(max(lat), 2)}; "
                  f"bytes median {int(statistics.median(size)):,}, max {max(size):,}")

    # Plays: how often the feed changes, and how far behind the play's own wallclock the first poll carrying it was.
    live = [r for r in ok if (r["summary"].get("status") or {}).get("state") == "in"]
    # A poll is "dense" when the one before it came within twice the recording's usual interval: a laptop that
    # slept (2026-10-09: the Mac slept through most of HOU-DAL) leaves polls that say nothing about the feed's cadence.
    times = [parse_iso(r["t"]) for r in rows]
    usual = statistics.median((b - a).total_seconds() for a, b in zip(times, times[1:])) if len(times) > 1 else 15.0
    prev_t = {id(r): (parse_iso(r["t"]) - parse_iso(rows[i - 1]["t"])).total_seconds() if i else None for i, r in enumerate(rows)}
    def _dense(i):   # this poll and the one before it both came on time (a wake-up's first poll carries a stale clock)
        return i >= 2 and prev_t[id(rows[i])] <= 2 * usual and prev_t[id(rows[i - 1])] <= 2 * usual
    dense_ids = {id(rows[i]) for i in range(len(rows)) if _dense(i)}
    dense = [r for r in live if id(r) in dense_ids]
    long_gaps = [(rows[i - 1]["t"], r["t"], int(prev_t[id(r)])) for i, r in enumerate(rows) if i and prev_t[id(r)] > 2 * usual]
    print(f"polls while in progress: {len(live)}, of which {len(dense)} within {2 * usual:.0f} s of the previous poll "
          f"(the change and delay numbers below use only those); gaps over {2 * usual:.0f} s: {len(long_gaps)}"
          + (f", longest {max(g for _, _, g in long_gaps)} s" if long_gaps else ""))
    changes, gaps, delays, last_n, last_t, last_id = 0, [], [], None, None, None
    adds = []
    for r in live:
        n = r["summary"]["n_plays"]
        t = parse_iso(r["t"])
        lp = r["summary"].get("last_play") or {}
        if last_n is not None and n != last_n and id(r) in dense_ids:
            changes += 1
            adds.append(n - last_n)
            gaps.append((t - last_t).total_seconds())
            if lp.get("wallclock") and lp.get("id") != last_id:   # a new last play (not a correction inserted earlier in the array)
                delays.append((t - parse_iso(lp["wallclock"])).total_seconds())
        if last_n is None or n != last_n:
            last_t = t
        last_n, last_id = n, lp.get("id")
    print(f"plays changed on {changes} of {max(len(dense), 0)} dense in-progress polls; plays added per change: "
          f"median {_fmt(statistics.median(adds), 0) if adds else '-'}, max {max(adds) if adds else '-'}; "
          f"seconds between changes: median {_fmt(statistics.median(gaps)) if gaps else '-'}, p90 {_fmt(_pct(gaps, .9))}, max {_fmt(max(gaps)) if gaps else '-'}")
    print(f"delay from the last play's wallclock to the first poll carrying it (s): n {len(delays)}, "
          f"median {_fmt(statistics.median(delays)) if delays else '-'}, p10 {_fmt(_pct(delays, .1))}, p90 {_fmt(_pct(delays, .9))}, "
          f"min {_fmt(min(delays)) if delays else '-'}, max {_fmt(max(delays)) if delays else '-'}")
    # Score changes seen by the scoreboard vs the summary.
    sc_changes = sum(1 for a, b in zip(ok, ok[1:]) if a["summary"].get("score") != b["summary"].get("score"))
    sb_changes = sum(1 for a, b in zip(sb_ok, sb_ok[1:]) if a["scoreboard"].get("score") != b["scoreboard"].get("score"))
    print(f"score changes: summary {sc_changes}, scoreboard {sb_changes}")
    # Does the scoreboard run ahead of the summary (same poll)?
    ahead = behind = same = 0
    for r in rows:
        a, b = (r["summary"].get("score") or {}), (r["scoreboard"].get("score") or {})
        if not a or not b or r["summary"].get("error") or r["scoreboard"].get("error"):
            continue
        ta, tb = (int(a.get("home") or 0) + int(a.get("away") or 0)), (int(b.get("home") or 0) + int(b.get("away") or 0))
        ahead += tb > ta
        behind += tb < ta
        same += tb == ta
    print(f"scoreboard total points vs summary's, same poll: ahead {ahead}, behind {behind}, same {same}")
    # Win probability: present? grows with the plays?
    wp = [(r["summary"]["n_plays"], r["summary"]["n_wp"]) for r in ok]
    print(f"ESPN win probability: present in {sum(1 for _, w in wp if w)} of {len(wp)} answers; "
          f"n_wp == n_plays in {sum(1 for p, w in wp if p == w)}; n_wp < n_plays in {sum(1 for p, w in wp if w < p)}; "
          f"n_wp > n_plays in {sum(1 for p, w in wp if w > p)}; last value {ok[-1]['summary'].get('last_wp')}")
    # Score: the header's score vs the last play's score (same poll).
    leads = []
    for r in live:
        sc, lp = r["summary"].get("score") or {}, r["summary"].get("last_play") or {}
        if sc.get("home") is not None and lp.get("home") is not None:
            leads.append((int(sc["home"]) + int(sc["away"])) - (int(lp["home"]) + int(lp["away"])))
    if leads:
        print(f"header's total points minus the last play's (same poll; >0 = the header runs ahead of the plays): "
              f"ahead on {sum(1 for d in leads if d > 0)} of {len(leads)} live polls, behind on {sum(1 for d in leads if d < 0)}, "
              f"median {_fmt(statistics.median(leads))}, max {max(leads)}")
    # Clock: the header's clock vs the last play's clock.
    diffs = []
    for r in live:
        st, lp = r["summary"].get("status") or {}, r["summary"].get("last_play") or {}
        if st.get("clock") and lp.get("clock") and st.get("period") == lp.get("period"):
            try:
                diffs.append(_secs(lp["clock"]) - _secs(st["clock"]))
            except ValueError:
                pass
    if diffs:
        print(f"last play's clock minus the header's clock (s, same period; >0 = the header runs ahead of the plays): "
              f"median {_fmt(statistics.median(diffs))}, p90 {_fmt(_pct(diffs, .9))}, max {_fmt(max(diffs))}")
    # Status transitions.
    for key in ("summary", "scoreboard"):
        print(f"{key} status transitions:")
        prev = None
        for r in rows:
            st = r[key].get("status")
            if st is None and r[key].get("error"):
                st = {"name": f"ERROR {r[key]['error']}"}
            sig = (st or {}).get("name"), (st or {}).get("state"), (st or {}).get("detail"), (st or {}).get("period")
            if sig != prev:
                print(f"   {r['t']} #{r['i']} {sig} clock={(st or {}).get('clock')} score={r[key].get('score')}"
                      + (f" plays={r['summary'].get('n_plays')}" if key == "summary" else ""))
                prev = sig
    flags = {json.dumps(r["summary"].get("flags"), sort_keys=True) for r in ok}
    print("summary flags seen:", flags)
    keys = {tuple(r["summary"].get("top_keys") or ()) for r in ok}
    print("summary top-level keys seen:", [list(k) for k in keys])


def _secs(display):
    if ":" in display:
        mm, ss = display.split(":", 1)
        return int(mm) * 60 + float(ss)
    return float(display)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--event", required=True, help="ESPN event id")
    r.add_argument("--date", required=True, help="the game's US date for the scoreboard, YYYYMMDD")
    r.add_argument("--interval", type=float, default=15.0)
    r.add_argument("--minutes", type=float, default=20.0)
    r.add_argument("--out", default=str(DEFAULT_OUT))
    a = sub.add_parser("analyze")
    a.add_argument("folder")
    args = ap.parse_args()
    if args.cmd == "record":
        record(args)
    else:
        analyze(args)


if __name__ == "__main__":
    main()
