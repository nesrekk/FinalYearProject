"""
qa_route_crawl.py
=================
Round 8 QA: call every route of the three running backends once, with real
arguments, and write down what came back.

It starts nothing itself. The three services must already be running
(`./start.sh`, or uvicorn on 8000 / 8001 / 8002, see README "Running it
locally"). It reads each service's OpenAPI schema, so a new route is crawled
without editing this file; only a route whose *required* argument can't be
guessed from its name needs an entry in OVERRIDES / POST_BODIES below.

Arguments come from the database (read-only, autocommit, nothing imported
from the API): a known player (LeBron James), his team and season (LAL
2024-25), a LAL game of that season in each id format the tables use, a draft
class. Optional parameters are left out, so each route runs on its own
defaults, except where OVERRIDES gives what the page itself passes.

Per route: status, seconds (first call after the service started = cold,
unless something earlier warmed a shared cache), response bytes, rows (the
longest list in the answer; blank when it has none), `empty` (200 with lists
and every one empty), `_source` (top / nested / no / n/a) with its `live` flag
and upstream text, and a note (error detail, or why it wasn't called). One
call at a time, 20 s cap (`timeout`).

    python3 scripts/qa_route_crawl.py                       # all three services
    python3 scripts/qa_route_crawl.py --only /games         # paths starting with it
    python3 scripts/qa_route_crawl.py --out docs/qa/crawl_2026-10-05.tsv --md $TMPDIR/crawl.md

Calls two quota-limited outside services once each (the Odds API behind
/odds/championship, cached 6 h by the server; Gemini behind /workbench/parse
and /ask, cached for the day): `--skip-quota` leaves them out.
"""

import argparse
import datetime as dt
import itertools
import json
import os
import re
import sys
import time
from urllib.parse import quote, urlencode

import psycopg2
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "api"))
from db_config import DB_CONFIG  # noqa: E402  (reads api/.env only)

SERVICES = {"mvp": "http://127.0.0.1:8000", "similarity": "http://127.0.0.1:8001", "impact": "http://127.0.0.1:8002"}
TIMEOUT = 20.0
QUOTA_ROUTES = {("impact", "/odds/championship"), ("impact", "/workbench/parse"), ("impact", "/ask")}
COLUMNS = ["service", "method", "path", "url", "status", "seconds", "bytes", "rows", "empty",
           "source", "source_live", "upstream", "odd_text", "note"]


# ─── real arguments from the database ──────────────────────────────────────

def samples():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()

    def one(sql, *args):
        cur.execute(sql, args)
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"no sample for: {sql}")
        return row

    s = {"player_name": "LeBron James", "team": "LAL", "team_b": "BOS", "season": 2025}
    s["player_id"], = one("SELECT player_id FROM player_season_stats WHERE player_name = %s AND season = %s",
                          s["player_name"], s["season"])
    s["player_id_b"], = one("SELECT player_id FROM player_season_stats WHERE player_name = 'Anthony Davis' "
                            "AND season = %s", s["season"])
    s["espn_game"], s["nba_game"], s["game_date"] = one(
        "SELECT game_id, nba_game_id, game_date FROM lineup_stint_games WHERE season = %s AND home_team = %s "
        "AND game_ok ORDER BY game_date LIMIT 1", s["season"], s["team"])
    s["game_date"] = s["game_date"].isoformat()
    s["draft_year"] = 2003
    conn.close()
    return s


# Path parameter or required query parameter → value, by name.
def by_name(name, s):
    return {
        "player_name": s["player_name"], "name_or_player": s["player_name"], "player": s["player_name"],
        "player_id": s["player_id"], "pid": s["player_id"], "player_a": s["player_name"],
        "player_b": "Anthony Davis", "player1": s["player_name"], "player2": "Anthony Davis",
        "team_abbr": s["team"], "abbr": s["team"], "team": s["team"], "team_a": s["team"], "team_b": s["team_b"],
        "season": s["season"], "draft_year": s["draft_year"], "award": "mvp", "stat_key": "pts",
        "date": s["game_date"], "q": "LeBron", "query": "LeBron", "name": s["player_name"],
    }.get(name)


# Per-route values where the name alone isn't enough (or the page always
# passes something the route's default leaves out). A callable value is
# read from another route of the same service first: get(path) -> JSON.
def overrides(s):
    def trivia(get):
        q = get("/games/trivia/daily")["questions"][0]
        return {"question_id": q["id"], "option_id": q["options"][0]["id"]}

    def whatif(get):
        points = get(f"/games/wp-replay/{s['espn_game']}")["points"]
        return {"event_id": next(p["event_id"] for p in points if p.get("is_missed_shot"))}

    def lineup(get):
        top = get(f"/lineup-predictor/team?team={s['team']}&season={s['season']}")["lineups"][0]
        return {"ids": ",".join(str(i) for i in top["player_ids"])}

    return {
        "/games/wp-replay/{game_id}": {"game_id": s["espn_game"]},
        "/games/wp-replay/{game_id}/whatif": {"game_id": s["espn_game"], "_dynamic": whatif},
        "/rotations/game/{game_id}": {"game_id": s["espn_game"]},
        "/games/boxscore/{game_id}": {"game_id": s["nba_game"]},
        "/pregame/availability/game/{game_id}": {"game_id": s["nba_game"]},
        "/coaching/decision/{name}": {"name": "timeout"},
        "/data-quality/check/{key}": {"key": "twin_copies"},
        "/prospects/comp/{player_name}": {"player_name": "Cooper Flagg"},
        "/teams/with-without/{team_abbr}/{season}": {"player_name": s["player_name"]},
        "/workbench/entities": {"kind": "player", "q": "LeBron"},
        "/explain/{award}/{player_name}": {"award": "mvp", "player_name": "Nikola Jokić"},
        "/similarity/stat-line": {"line": "pts:30.1,ast:6.7,ts_pct:0.669,fg3a:11.2", "season": 2016},
        "/explore/regression": {"x": "ast", "y": "tov", "season_from": 2016, "season_to": 2026},
        "/games/blurred-player/guess": {"guess_player_name": s["player_name"]},
        "/games/guess-the-player/guess": {"guess_player_name": s["player_name"]},
        "/games/guess-the-game/guess": {"team": s["team"], "attempt_number": 1},
        "/games/trivia/guess": {"_dynamic": trivia},
        "/leaderboard/composite": {"weights": "pts:1,ts_pct:1", "season_from": 2026},
        "/lineup-predictor/predict": {"team": s["team"], "season": s["season"], "_dynamic": lineup},
        "/report-card/pair": {"task": "impact_next", "a": "bpm", "b": "rapm_tracker"},
        # Real 2024-25 Knicks starting five (the smoke test's).
        "/spacing/lineup": {"season": 2025, "player_ids": "1626157,1628384,1628404,1628969,1628973"},
        # Real 2024-25 case: Josh Hart (NYK) for Duncan Robinson (MIA) (the smoke test's).
        "/trade/impact": {"season": 2025, "team_a": "NYK", "player_a_id": 1628404, "team_b": "MIA",
                          "player_b_id": 1629130},
        "/trade/simulate": {"season": 2025, "team_a": "NYK", "player_a_id": 1628404, "team_b": "MIA",
                            "player_b_id": 1629130},
    }


def post_bodies(s):
    spec = {"dataset": "player_season", "entities": "all", "columns": ["pts", "fg3a", "fg3_pct"],
            "season_from": 2025, "season_to": 2026, "min_games": 20, "limit": 5000}
    return {
        "/workbench/query": {"dataset": "player_season", "entities": [s["player_id"]], "columns": ["pts", "ts_pct"],
                             "season_from": 2021, "season_to": 2026},
        "/workbench/context": {"spec": spec, "column": "pts", "by": "season", "bins": 20},
        "/workbench/trend": {"spec": spec, "x": "fg3a", "y": "fg3_pct"},
        "/workbench/aging": {"stat": "pts", "player_ids": [s["player_id"]], "era": "all"},
        "/workbench/finder": {"dataset": "player_season", "season_from": 2023, "season_to": 2023, "min_games": 58,
                              "conditions": [{"type": "value", "stat": "pts", "op": "gte", "value": 30}]},
        "/workbench/parse": {"text": "players who averaged 30 points in 2022-23"},
        "/ask": {"text": "Curry's shot chart in 2015-16"},
    }


# ─── one call ──────────────────────────────────────────────────────────────

def rows_and_empty(obj):
    """Longest list at the top level or one level down; empty = lists exist and all are empty."""
    if isinstance(obj, list):
        return len(obj), len(obj) == 0
    if not isinstance(obj, dict):
        return "", False
    lengths = []
    for k, v in obj.items():
        if k == "_source":
            continue
        if isinstance(v, list):
            lengths.append(len(v))
        elif isinstance(v, dict):
            lengths += [len(x) for x in v.values() if isinstance(x, list)]
    if not lengths:
        return "", False
    return max(lengths), max(lengths) == 0


# Case matters: lowercase "none" is a real setting in several answers (group_by, kind, call).
ODD_STRINGS = {"nan", "NaN", "None", "undefined", "null", "inf", "-inf", "Infinity", "-Infinity"}


def odd_strings(obj, path="", found=None):
    """JSON string values that read like a leaked Python/JS placeholder ("NaN", "None", "undefined")."""
    found = [] if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            odd_strings(v, f"{path}.{k}", found)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:500]):
            odd_strings(v, f"{path}[{i}]" if i < 1 else f"{path}[]", found)
    elif isinstance(obj, str) and obj.strip() in ODD_STRINGS:
        found.append(f"{path}={obj}")
    return found


def source_of(obj):
    if not isinstance(obj, dict):
        return "n/a", "", ""
    src = obj.get("_source")
    where = "top"
    if src is None:
        where = "no"
        for v in obj.values():
            if isinstance(v, dict) and isinstance(v.get("_source"), dict):
                src, where = v["_source"], "nested"
                break
    if not isinstance(src, dict):
        return where, "", ""
    upstream = str(src.get("upstream_api") or "")
    return where, str(bool(src.get("live"))).lower(), upstream[:80]


def call(session, method, url, body=None):
    t0 = time.perf_counter()
    try:
        if method == "GET":
            r = session.get(url, timeout=TIMEOUT)
        else:
            r = session.post(url, json=body, timeout=TIMEOUT)
    except requests.Timeout:
        return {"status": "timeout", "seconds": f"{time.perf_counter() - t0:.2f}", "note": f"no answer in {TIMEOUT:.0f} s"}
    except requests.RequestException as e:
        return {"status": "error", "seconds": f"{time.perf_counter() - t0:.2f}", "note": type(e).__name__}
    out = {"status": r.status_code, "seconds": f"{time.perf_counter() - t0:.2f}", "bytes": len(r.content)}
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype:
        out.update(source="n/a", note=ctype.split(";")[0])
        return out
    try:
        obj = r.json()
    except ValueError:
        out["note"] = "invalid JSON"
        return out
    if r.status_code >= 400:
        detail = obj.get("detail") if isinstance(obj, dict) else obj
        out["note"] = re.sub(r"\s+", " ", json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail)[:200]
        return out
    rows, empty = rows_and_empty(obj)
    out["rows"], out["empty"] = rows, "yes" if empty else ""
    out["source"], out["source_live"], out["upstream"] = source_of(obj)
    odd = sorted(set(odd_strings(obj)))
    if odd:
        out["odd_text"] = f"{len(odd)}: " + ", ".join(odd[:3])
    return out


# ─── the crawl ─────────────────────────────────────────────────────────────

def plan(service, base, s, only, session=None):
    schema = requests.get(f"{base}/openapi.json", timeout=TIMEOUT).json()
    ov, bodies = overrides(s), post_bodies(s)

    def get(path):
        r = session.get(f"{base}{path}", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    for path, ops in sorted(schema["paths"].items()):
        if only and not path.startswith(only):
            continue
        for method, op in ops.items():
            method = method.upper()
            item = {"service": service, "method": method, "path": path}
            if method not in ("GET", "POST"):
                yield {**item, "note": f"not crawled: {method}"}
                continue
            if method == "POST":
                if path not in bodies:
                    yield {**item, "note": "not crawled: no request body in POST_BODIES"}
                    continue
                yield {**item, "url": f"{base}{path}", "body": bodies[path]}
                continue
            values, missing = dict(ov.get(path, {})), []
            dynamic = values.pop("_dynamic", None)
            if dynamic:
                if session is None:
                    yield {**item, "note": "arguments read from another route at crawl time"}
                    continue
                try:
                    values.update(dynamic(get))
                except Exception as e:  # noqa: BLE001 - recorded, the crawl goes on
                    yield {**item, "note": f"not crawled: argument lookup failed ({type(e).__name__})"}
                    continue
            for p in op.get("parameters", []):
                name, where = p["name"], p["in"]
                if name in values:
                    continue
                if where == "path" or p.get("required"):
                    v = by_name(name, s)
                    enum = p.get("schema", {}).get("enum")
                    if v is None and enum:
                        v = enum[0]
                    if v is None:
                        missing.append(name)
                    else:
                        values[name] = v
            if missing:
                yield {**item, "note": f"not crawled: no value for {', '.join(missing)}"}
                continue
            url_path = path
            query = {}
            path_names = set(re.findall(r"\{(\w+)\}", path))
            for k, v in values.items():
                if k in path_names:
                    url_path = url_path.replace("{" + k + "}", quote(str(v), safe=""))
                else:
                    query[k] = v
            url = f"{base}{url_path}" + (f"?{urlencode(query)}" if query else "")
            yield {**item, "url": url}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(HERE, "..", "docs", "qa", f"crawl_{dt.date.today().isoformat()}.tsv"))
    ap.add_argument("--md", help="also write a markdown summary (problems only) to this file")
    ap.add_argument("--only", help="crawl only paths starting with this")
    ap.add_argument("--skip-quota", action="store_true", help="leave out the Odds API and Gemini routes")
    ap.add_argument("--list", action="store_true", help="print the URLs it would call and stop")
    args = ap.parse_args()

    s = samples()
    if args.list:
        for service, base in SERVICES.items():
            for item in plan(service, base, s, args.only):
                print(f"{service:>10} {item['method']:4} {item.get('url', '').replace(base, '') or item['path']}"
                      f"  {item.get('note', '')}")
        return
    session = requests.Session()
    results = []
    for service, base in SERVICES.items():
        try:
            items = plan(service, base, s, args.only, session)
            first = next(items, None)  # reads the schema now, so a stopped service is caught here
        except requests.RequestException as e:
            print(f"{service}: can't read {base}/openapi.json ({type(e).__name__}); is it running?", file=sys.stderr)
            results.append({"service": service, "method": "", "path": "", "status": "down",
                            "note": f"{base} not answering"})
            continue
        for item in itertools.chain([first] if first else [], items):
            if "url" in item and args.skip_quota and (service, item["path"]) in QUOTA_ROUTES:
                item = {**item, "note": "not crawled: --skip-quota"}
                item.pop("url")
            if "url" in item:
                item.update(call(session, item["method"], item["url"], item.get("body")))
                item["url"] = item["url"].replace(base, "")
            print(f"{item.get('status', '-')!s:>7} {item.get('seconds', ''):>6} {service:>10} {item['method']:4} "
                  f"{item['path']}  {item.get('note', '')}", flush=True)
            item.pop("body", None)
            results.append(item)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in results:
            f.write("\t".join(str(r.get(c, "")).replace("\t", " ").replace("\n", " ") for c in COLUMNS) + "\n")

    called = [r for r in results if r.get("status") not in (None, "", "down")]
    bad = [r for r in called if not str(r["status"]).startswith("2")]
    empty = [r for r in called if r.get("empty")]
    slow = sorted((r for r in called if str(r["status"]).startswith("2") and float(r["seconds"]) > 3),
                  key=lambda r: -float(r["seconds"]))
    nosrc = [r for r in called if str(r["status"]).startswith("2") and r.get("source") == "no"]
    odd = [r for r in called if r.get("odd_text")]
    skipped = [r for r in results if not r.get("status")]
    print(f"\n{len(results)} routes: {len(called)} called, {len(skipped)} not called; "
          f"{len(bad)} not 2xx, {len(empty)} empty, {len(slow)} over 3 s, {len(nosrc)} JSON without _source, "
          f"{len(odd)} with placeholder text")
    if args.md:
        def table(rows, cols):
            out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            out += ["| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |" for r in rows]
            return "\n".join(out)
        with open(args.md, "w") as f:
            f.write(f"Crawl {dt.datetime.now().isoformat(timespec='minutes')}: {len(results)} routes, "
                    f"{len(called)} called, {len(skipped)} not called, {len(bad)} not 2xx, {len(empty)} empty, "
                    f"{len(slow)} over 3 s, {len(nosrc)} JSON answers without `_source`, {len(odd)} with placeholder text.\n\n")
            for title, rows, cols in (
                ("Not 2xx", bad, ["service", "url", "status", "seconds", "note"]),
                ("Empty answers", empty, ["service", "url", "seconds", "rows"]),
                ("Over 3 s", slow, ["service", "url", "status", "seconds", "bytes"]),
                ("Not called", skipped, ["service", "method", "path", "note"]),
                ("Placeholder text in the answer", odd, ["service", "url", "odd_text"]),
                ("JSON without _source", nosrc, ["service", "url"]),
            ):
                f.write(f"### {title} ({len(rows)})\n\n{table(rows, cols) if rows else 'None.'}\n\n")


if __name__ == "__main__":
    main()
