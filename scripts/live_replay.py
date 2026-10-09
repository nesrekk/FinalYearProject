"""
live_replay.py
===============
The replay-as-live harness (round 10 step 1, docs/LIVE_GAME.md): a finished game's ESPN summary served as if the
game were on, cut play by play, at any speed and at any hour. Every later live step (the engine, the page, the
tests) runs against this instead of waiting for a real game. Nothing here touches the database.

    cd scripts && python3 live_replay.py --event 401809243 --speed 20              # serve on 127.0.0.1:8765
    cd scripts && python3 live_replay.py --event 401809243 --start-play 400 --paused --port 8765
    cd scripts && python3 live_replay.py --event 401809243 --cut 120 > cut.json    # one cut, to stdout

The server answers ESPN's own paths, so a client only has to point its base URL at it:
    GET /apis/site/v2/sports/basketball/nba/summary?event=<id>         the cut summary (ESPN's shape)
    GET /apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD  the scoreboard with the replayed games
    GET /__replay/status                                                 {event: {n, total, status, speed, ...}}
    GET /__replay/control?event=<id>&set=<n> | &advance=<k> | &speed=<x> | &pause=1 | &resume=1
    GET /__replay/fail?mode=none|timeout|500[&seconds=6]                 make every ESPN path fail that way
In Python, `Replay(summary, speed=...)` gives the same cuts without a server (`at(n)`, `current()`, `scoreboard()`),
and `serve([replay, ...], port=0)` starts the server on a free port (`server.base_url`, `server.shutdown()`).

What a cut is (checked against a recorded live game, docs/LIVE_GAME.md § 2): the first n plays and ESPN's own
win-probability entries for them; the header's status, period, clock and scores from the n-th play (scheduled before
the first play, final after the last, halftime after the 2nd period's "End Period", end of period after the others);
the line scores from the plays; `meta` with the last play's wallclock as the feed's last update. The box score cannot
be cut: before the final every athlete keeps his id and name (scripts/espn_summary.py names the plays' participants
from it) with no statistics, and the final cut is the summary exactly as stored. Pre-game keys that ESPN publishes
before tip (`pickcenter`, `gameInfo`, `format`, `injuries`, `news`, `standings`, `seasonseries`, `videos`) stay as
they are in the stored final; each team's `leaders` (ESPN fills them live from its box score) is emptied before
the final, and `againstTheSpread` stays the empty per-team shell ESPN serves in every state.

Timing: `Replay` moves on the plays' own wallclock timestamps (every ESPN play carries one since at least 2025-26),
scaled by `speed`: at speed 1 the replay takes as long as the game did, halftime included; at speed 20 a game runs
in about seven minutes. A summary without wallclocks advances one play every `pace` seconds. `paused=True` (or
`set(n)`) puts the replay under manual control, which is what a test wants.

Where a game's summary comes from (`summary_source(espn_id)`): the daily update's cache (live_data/<season>/
espn_summary/<id>.json.gz) if it has the game, else live_data/replay/<id>.json.gz, else ESPN's summary endpoint
(saved to live_data/replay/, gitignored). ESPN's JSON is never committed.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import json
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = ROOT / "live_data"
REPLAY_DIR = LIVE_DIR / "replay"
EASTERN = ZoneInfo("America/New_York")
SITE_PATH = "/apis/site/v2/sports/basketball/nba"
DEFAULT_PACE = 17.0      # seconds a play when a summary has no wallclocks (a 2025-26 game: ~150 min / ~530 plays)
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


# ─── Reading a stored game ───────────────────────────────────────────────────

def load_json(path):
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summary_source(espn_id, fetch=True):
    """A finished game's summary: the daily update's cache, then live_data/replay/, then ESPN (saved there)."""
    espn_id = str(espn_id)
    for path in sorted(LIVE_DIR.glob(f"*/espn_summary/{espn_id}.json.gz")):
        return load_json(path)
    cached = REPLAY_DIR / f"{espn_id}.json.gz"
    if cached.exists():
        return load_json(cached)
    if not fetch:
        return None
    sys.path.insert(0, str(ROOT / "scripts"))
    import espn_summary as ES
    s = ES.fetch_summary(espn_id, timeout=10, tries=2)
    if not ES.is_final(s):
        raise ValueError(f"ESPN event {espn_id} is not final; the harness replays finished games")
    REPLAY_DIR.mkdir(parents=True, exist_ok=True)
    ES.save_summary(s, cached)
    return s


# ─── Cutting a summary ───────────────────────────────────────────────────────

def _parse_wallclock(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None


def _clock_seconds(display):
    """'7:32' -> 452.0; '45.3' -> 45.3; None when unreadable."""
    try:
        if ":" in display:
            mm, ss = display.split(":", 1)
            return int(mm) * 60 + float(ss)
        return float(display)
    except (TypeError, ValueError, AttributeError):
        return None


def _period_label(number, n_regulation=4):
    if number <= n_regulation:
        return f"{ORDINAL.get(number, str(number) + 'th')} Quarter", ORDINAL.get(number, str(number) + "th")
    ot = number - n_regulation
    return ("OT" if ot == 1 else f"{ot}OT"), ("OT" if ot == 1 else f"{ot}OT")


def status_after(play, is_last, scheduled_detail=None):
    """ESPN's `status` object (type + period + clock) as it reads after `play` (None before the first play)."""
    if play is None:
        return {"type": {"id": "1", "name": "STATUS_SCHEDULED", "state": "pre", "completed": False,
                         "description": "Scheduled", "detail": scheduled_detail or "Scheduled",
                         "shortDetail": scheduled_detail or "Scheduled"}}
    period = int((play.get("period") or {}).get("number") or 0)
    display = (play.get("clock") or {}).get("displayValue") or "0.0"
    secs = _clock_seconds(display) or 0.0
    kind = (play.get("type") or {}).get("text") or ""
    long_label, short_label = _period_label(period)
    primary = {}
    if is_last:
        kind_id, name, state, completed = "3", "STATUS_FINAL", "post", True
        detail = short = "Final" if period <= 4 else ("Final/OT" if period == 5 else f"Final/{period - 4}OT")
        description = "Final"
    elif kind == "End Period" and period == 2:
        kind_id, name, state, completed = "23", "STATUS_HALFTIME", "in", False
        description = detail = short = "Halftime"
        primary = {"statusPrimary": "Halftime"}
    elif kind == "End Period":
        kind_id, name, state, completed = "22", "STATUS_END_PERIOD", "in", False
        description = "End of Period"
        detail = short = f"End of {short_label}"
    else:
        kind_id, name, state, completed = "2", "STATUS_IN_PROGRESS", "in", False
        description = "In Progress"
        detail, short = f"{display} - {long_label}", f"{display} - {short_label}"
        primary = {"statusPrimary": display, "statusSecondary": short_label}
    # The live summary's header (HOU-DAL 2026-10-09) adds `statusPrimary` / `statusSecondary` and `displayPeriod`;
    # the scoreboard's status carries `clock` as a number. Both are served here.
    return {"clock": secs, "displayClock": display, "period": period, "displayPeriod": short_label,
            "type": {"id": kind_id, "name": name, "state": state, "completed": completed,
                     "description": description, "detail": detail, "shortDetail": short, **primary}}


def line_scores(plays):
    """Points per period for the home and away sides, from the plays' running scores."""
    home, away = [], []
    last_h = last_a = 0
    last_period = 0
    for p in plays:
        period = int((p.get("period") or {}).get("number") or 0)
        while last_period < period:
            home.append(0)
            away.append(0)
            last_period += 1
        h, a = int(p.get("homeScore") or 0), int(p.get("awayScore") or 0)
        if period >= 1:
            home[period - 1] += h - last_h
            away[period - 1] += a - last_a
        last_h, last_a = h, a
    return home, away


def cut(summary, n, scheduled_detail=None):
    """The summary as ESPN would have served it after the first `n` plays (0 = before tip, len(plays) = final)."""
    plays_all = summary.get("plays") or []
    n = max(0, min(int(n), len(plays_all)))
    is_final = n == len(plays_all)
    if is_final:
        return copy.deepcopy(summary)
    out = copy.deepcopy(summary)
    plays = plays_all[:n]
    out["plays"] = plays
    ids = {p.get("id") for p in plays}
    out["winprobability"] = [w for w in summary.get("winprobability") or [] if w.get("playId") in ids]
    last = plays[-1] if plays else None
    comp = out["header"]["competitions"][0]
    comp["status"] = status_after(last, False, scheduled_detail)
    home_lines, away_lines = line_scores(plays)
    for c in comp.get("competitors") or []:
        c.pop("winner", None)
        if last is None:
            c["score"] = None
            c.pop("linescores", None)
        else:
            c["score"] = str(last.get("homeScore") if c.get("homeAway") == "home" else last.get("awayScore"))
            c["linescores"] = [{"displayValue": str(v)} for v in (home_lines if c.get("homeAway") == "home" else away_lines)]
    comp["liveAvailable"] = last is not None
    comp["playByPlaySource"] = "full" if last is not None else "none"
    comp["boxscoreSource"] = "none"       # ESPN serves "full" in-game; the harness can't cut a box score (R10-001)
    comp["boxscoreAvailable"] = False
    if last is None:                      # before tip ESPN serves no `plays` key and no `boxscore.players`
        out.pop("plays", None)
        if isinstance(out.get("boxscore"), dict):
            out["boxscore"].pop("players", None)
    for team in (out.get("boxscore") or {}).get("players") or []:
        for block in team.get("statistics") or []:
            block["totals"] = []
            for a in block.get("athletes") or []:
                a["stats"] = []
                a["active"] = False
    for team in out.get("leaders") or []:  # per-team shells with `leaders: []`, as ESPN serves them before tip
        if isinstance(team, dict):
            team["leaders"] = []
    meta = dict(out.get("meta") or {})
    meta["gameState"] = "pre" if last is None else "in"
    for key in ("lastUpdatedAt", "firstPlayWallClock", "lastPlayWallClock"):
        meta.pop(key, None)
    if plays:
        if plays[0].get("wallclock"):
            meta["firstPlayWallClock"] = plays[0]["wallclock"]
        if last.get("wallclock"):
            meta["lastPlayWallClock"] = meta["lastUpdatedAt"] = last["wallclock"]
    out["meta"] = meta
    return out


# ─── A replay: which cut applies now ─────────────────────────────────────────

class Replay:
    def __init__(self, summary, speed=1.0, start_play=0, paused=False, pace=DEFAULT_PACE, clock=time.monotonic):
        self.summary = summary
        self.plays = summary.get("plays") or []
        self.total = len(self.plays)
        self.espn_id = str(summary["header"]["id"])
        self.speed = float(speed)
        self.pace = float(pace)
        self.clock = clock
        self.fail_mode, self.fail_seconds = "none", 6.0
        self._lock = threading.Lock()
        self._walls = [_parse_wallclock(p.get("wallclock")) for p in self.plays]
        self._has_walls = bool(self._walls) and all(w is not None for w in self._walls)
        self._manual = bool(paused)
        self._n = max(0, min(int(start_play), self.total))
        self._t0 = self.clock()
        self._base = self._n

    # The replay runs on the plays' wallclocks (scaled) from the play it was started or resumed at.
    def n_now(self):
        with self._lock:
            if self._manual:
                return self._n
            elapsed = (self.clock() - self._t0) * self.speed
            if self._has_walls:
                origin = self._walls[self._base] if self._base < self.total else self._walls[-1]
                n = self._base
                while n < self.total and (self._walls[n] - origin).total_seconds() <= elapsed:
                    n += 1
                # the first play of the replay is on the floor at once
                n = max(n, min(self._base + 1, self.total)) if self.total else 0
            else:
                n = min(self.total, self._base + 1 + int(elapsed // self.pace))
            self._n = n
            return n

    def set(self, n):
        with self._lock:
            self._manual = True
            self._n = max(0, min(int(n), self.total))

    def advance(self, k=1):
        self.set(self.n_now() + int(k))

    def pause(self):
        self.set(self.n_now())

    def resume(self, speed=None):
        n = self.n_now()
        with self._lock:
            if speed is not None:
                self.speed = float(speed)
            self._manual = False
            self._base, self._n, self._t0 = n, n, self.clock()

    def at(self, n):
        return cut(self.summary, n, scheduled_detail=self.scheduled_detail())

    def current(self):
        return self.at(self.n_now())

    def scheduled_detail(self):
        tip = (self.summary["header"]["competitions"][0].get("date") or "")
        try:
            local = datetime.fromisoformat(tip.replace("Z", "+00:00")).astimezone(EASTERN)
            return local.strftime("%-m/%-d - %-I:%M %p ") + ("EDT" if local.dst() else "EST")
        except ValueError:
            return "Scheduled"

    def us_date(self):
        tip = self.summary["header"]["competitions"][0].get("date") or ""
        try:
            return datetime.fromisoformat(tip.replace("Z", "+00:00")).astimezone(EASTERN).date()
        except ValueError:
            return None

    def status(self):
        n = self.n_now()
        st = status_after(self.plays[n - 1] if n else None, n == self.total and self.total > 0, self.scheduled_detail())
        return {"event": self.espn_id, "n": n, "total": self.total, "speed": self.speed, "manual": self._manual,
                "status": st["type"]["name"], "detail": st["type"].get("shortDetail"), "period": st.get("period"),
                "clock": st.get("displayClock"), "wallclock": self._has_walls, "fail": self.fail_mode}

    def scoreboard_event(self):
        """This game as an entry of ESPN's scoreboard `events` list, as it reads now."""
        c = self.current()
        header = c["header"]
        comp = header["competitions"][0]
        n = len(c.get("plays") or [])
        competitors = []
        for side in comp.get("competitors") or []:
            team = side.get("team") or {}
            competitors.append({
                "id": side.get("id"), "homeAway": side.get("homeAway"), "order": side.get("order"),
                "team": {"id": team.get("id"), "abbreviation": team.get("abbreviation"), "location": team.get("location"),
                         "name": team.get("name"), "displayName": team.get("displayName")},
                "score": side.get("score") if side.get("score") is not None else "0",
                "records": side.get("record") or [],
                "winner": side.get("winner"),
                "linescores": [{"value": float(x.get("displayValue") or 0)} for x in side.get("linescores") or []],
            })
        venue = ((c.get("gameInfo") or {}).get("venue") or {})
        return {
            "id": header.get("id"), "uid": header.get("uid"), "date": comp.get("date"),
            "name": " at ".join((s["team"].get("displayName") or "") for s in sorted(competitors, key=lambda s: s["homeAway"] != "away")),
            "season": dict(header.get("season") or {}),
            "competitions": [{
                "id": comp.get("id"), "date": comp.get("date"), "timeValid": header.get("timeValid", True),
                "neutralSite": bool(comp.get("neutralSite")), "status": comp.get("status"),
                "competitors": competitors, "notes": [], "playByPlayAvailable": n > 0,
                "venue": {"id": venue.get("id"), "fullName": venue.get("fullName"), "address": venue.get("address")},
            }],
            "status": comp.get("status"),
        }


def scoreboard(replays, date_str=None):
    """ESPN's scoreboard JSON for the replays (all of them, or those tipping on the US date YYYYMMDD)."""
    events = []
    for r in replays:
        d = r.us_date()
        if date_str and d and d.strftime("%Y%m%d") != str(date_str):
            continue
        events.append(r.scoreboard_event())
    return {"leagues": [{"id": "46", "abbreviation": "NBA", "name": "National Basketball Association"}],
            "day": {"date": date_str}, "events": events}


# ─── The server ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    server_version = "live_replay/1"

    def log_message(self, *args):   # quiet
        if getattr(self.server, "verbose", False):
            super().log_message(*args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        replays = self.server.replays
        if url.path == "/__replay/status":
            return self._json({rid: r.status() for rid, r in replays.items()})
        if url.path == "/__replay/control":
            r = replays.get(q.get("event")) or next(iter(replays.values()), None)
            if r is None:
                return self._json({"error": "no replay"}, 404)
            if "set" in q:
                r.set(int(q["set"]))
            if "advance" in q:
                r.advance(int(q["advance"]))
            if "pause" in q:
                r.pause()
            if "speed" in q and "resume" not in q:
                r.resume(float(q["speed"]))
            if "resume" in q:
                r.resume(float(q["speed"]) if "speed" in q else None)
            return self._json(r.status())
        if url.path == "/__replay/fail":
            mode = q.get("mode", "none")
            if mode not in ("none", "timeout", "500"):
                return self._json({"error": "mode is none, timeout or 500"}, 400)
            for r in replays.values():
                r.fail_mode, r.fail_seconds = mode, float(q.get("seconds", 6))
            return self._json({"fail": mode})
        fail = next((r for r in replays.values() if r.fail_mode != "none"), None)
        if fail is not None and url.path.startswith(SITE_PATH):
            if fail.fail_mode == "timeout":
                time.sleep(fail.fail_seconds)
                return self._json({"error": "slow"}, 200)
            return self._json({"error": "server error"}, 500)
        if url.path == f"{SITE_PATH}/summary":
            r = replays.get(q.get("event", ""))
            if r is None:
                return self._json({"code": 404, "message": "no event"}, 404)
            return self._json(r.current())
        if url.path == f"{SITE_PATH}/scoreboard":
            return self._json(scoreboard(replays.values(), q.get("dates")))
        return self._json({"code": 404, "message": "not found"}, 404)


class ReplayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):
        # HTTPServer.server_bind() resolves the host's fully qualified name, which takes ~35 s on a Mac with no
        # reverse DNS; the name is only used in error pages, so bind without it.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[0], self.server_address[1]

    def __init__(self, replays, port=0, verbose=False):
        super().__init__(("127.0.0.1", int(port)), _Handler)
        self.replays = {r.espn_id: r for r in replays}
        self.verbose = verbose
        self._thread = None

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def site_url(self):
        return self.base_url + SITE_PATH

    @property
    def summary_url(self):
        return self.site_url + "/summary"

    @property
    def scoreboard_url(self):
        return self.site_url + "/scoreboard"

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def serve(replays, port=0, verbose=False):
    """Start the server on a thread; returns it (base_url, summary_url, scoreboard_url, stop())."""
    return ReplayServer(list(replays), port=port, verbose=verbose).start()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", required=True, action="append", help="ESPN event id of a finished game (repeatable)")
    ap.add_argument("--file", action="append", default=[], help="a saved summary (.json or .json.gz) instead of --event's lookup")
    ap.add_argument("--speed", type=float, default=1.0, help="wallclock multiplier (20 = a game in ~7 minutes)")
    ap.add_argument("--start-play", type=int, default=0)
    ap.add_argument("--paused", action="store_true", help="manual control through /__replay/control")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cut", type=int, help="print the summary after N plays and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    summaries = [load_json(f) for f in args.file] or [summary_source(e) for e in args.event]
    if args.cut is not None:
        json.dump(cut(summaries[0], args.cut), sys.stdout)
        return
    replays = [Replay(s, speed=args.speed, start_play=args.start_play, paused=args.paused) for s in summaries]
    server = serve(replays, port=args.port, verbose=args.verbose)
    print(f"replay-as-live on {server.base_url}  (summary: {server.summary_url}?event=<id>; scoreboard: "
          f"{server.scoreboard_url}?dates=YYYYMMDD; status: {server.base_url}/__replay/status)")
    for r in replays:
        st = r.status()
        print(f"  event {r.espn_id}: {r.total} plays, US date {r.us_date()}, speed x{r.speed}"
              f"{' (paused)' if st['manual'] else ''}, wallclocks {'yes' if st['wallclock'] else 'no'}")
    try:
        while True:
            time.sleep(5)
            if args.verbose:
                print({rid: (s["n"], s["status"], s["detail"]) for rid, s in ((r.espn_id, r.status()) for r in replays)})
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
