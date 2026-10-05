"""
espn_live.py
============
Live reads from ESPN's public site API for the pages that have to work during the season without
stats.nba.com (round 8 step 4, docs/qa/ROUND8_ISSUES.md R8-002/R8-003): the scoreboard of a date,
one game's box score, and the standings. The project already reads the same host for the Forecast
Ledger's schedule and results (scripts/ledger_espn.py, scripts/ledger_update.py), the final scores
(scripts/fetch_game_scores.py) and the postseason (scripts/fetch_postseason_games.py).

Rules:
  - Every call has a short timeout (TIMEOUT_SECONDS; ESPN answers in 0.5-1.5 s from this machine), so
    a page shows a clear "couldn't reach ESPN" state within about 3 s instead of hanging.
  - Answers are cached in memory for a short time: a scoreboard for today or a future date 60 s,
    a past date's scoreboard and a final box score 6 h (they don't change), standings 5 min.
  - A function returns None when ESPN can't be reached or answers something unexpected. Nothing is
    ever invented; the caller falls back to stored data or says the source is unreachable.
  - Dates are US Eastern calendar dates (ESPN's `dates=` parameter is the US date; the whole database
    uses that date too, round 8 R8-067). eastern_today() gives today's.
  - Team codes come back as the NBA codes the database uses (GS -> GSW, NO -> NOP, NY -> NYK,
    SA -> SAS, UTAH -> UTA, WSH -> WAS), the crosswalk of fetch_game_scores.py / ledger_espn.py.

Nothing here touches the database or the locked forecast.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SCOREBOARD_URL = f"{SITE}/scoreboard"
SUMMARY_URL = f"{SITE}/summary"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"

TIMEOUT_SECONDS = 3.0
EASTERN = ZoneInfo("America/New_York")
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NJ": "BKN"}
# ESPN's season types on the scoreboard: 1 preseason, 2 regular season, 3 postseason, 5 play-in
# (fetch_postseason_games.py reads the same codes).
SEASON_TYPE_KIND = {1: "Preseason", 2: "Regular season", 3: "Playoffs", 5: "Play-in"}

_TTL_LIVE = 60
_TTL_PAST = 6 * 60 * 60
_TTL_STANDINGS = 5 * 60
_cache: dict = {}
_cache_lock = threading.Lock()


def nba_code(abbr):
    abbr = (abbr or "").upper()
    return ESPN_TO_NBA.get(abbr, abbr)


def eastern_today() -> date:
    return datetime.now(EASTERN).date()


def eastern_date_of(iso_utc: str):
    """ESPN's event time ("2026-10-20T23:00Z") -> the US Eastern date, or None."""
    try:
        return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(EASTERN).date()
    except (TypeError, ValueError):
        return None


def _cached(key, ttl, make):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = make()
    if value is not None:
        with _cache_lock:
            _cache[key] = (now, value)
    return value


def clear_cache():
    with _cache_lock:
        _cache.clear()


def _get(url, params, timeout=TIMEOUT_SECONDS):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ─── Scoreboard ─────────────────────────────────────────────────────────────

def _status(comp):
    st = comp.get("status") or {}
    kind = st.get("type") or {}
    name = str(kind.get("name") or "")
    state = str(kind.get("state") or "")
    period = int(st.get("period") or 0)
    clock = str(st.get("displayClock") or "")
    if name == "STATUS_FINAL" or (state == "post" and kind.get("completed")):
        return "FINAL", str(kind.get("shortDetail") or "Final"), period, clock
    if state == "in":
        label = "Halftime" if name == "STATUS_HALFTIME" else (
            f"Q{period} {clock}" if period <= 4 else (f"OT {clock}" if period == 5 else f"{period - 4}OT {clock}"))
        return "LIVE", label.strip(), period, clock
    if name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"):
        return name.replace("STATUS_", ""), str(kind.get("description") or name.replace("STATUS_", "").title()), period, clock
    return "SCHEDULED", None, period, clock


def _tip_label(iso_utc, time_valid):
    if not time_valid:
        return "Time TBD"
    try:
        local = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(EASTERN)
    except (TypeError, ValueError):
        return "Scheduled"
    return local.strftime("%-I:%M %p ET")


def _team(competitor):
    team = competitor.get("team") or {}
    records = competitor.get("records") or []
    summary = next((r.get("summary") for r in records if r.get("type") == "total"), None) or (
        records[0].get("summary") if records else None)
    wins = losses = 0
    if summary and "-" in summary:
        try:
            wins, losses = (int(x) for x in summary.split("-")[:2])
        except ValueError:
            wins = losses = 0
    score = competitor.get("score")
    try:
        score = int(score) if score not in (None, "") else None
    except (TypeError, ValueError):
        score = None
    return {
        "abbr": nba_code(team.get("abbreviation")),
        "city": str(team.get("location") or ""),
        "name": str(team.get("name") or ""),
        "score": score,
        "wins": wins,
        "losses": losses,
        "winner": competitor.get("winner"),
    }


def parse_scoreboard(payload, date_str):
    """ESPN scoreboard JSON -> the app's game list (see scoreboard())."""
    games = []
    for e in payload.get("events", []) or []:
        comps = e.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", []) or []}
        if "home" not in sides or "away" not in sides:
            continue
        status, status_text, period, clock = _status(comp)
        if status == "SCHEDULED":
            status_text = _tip_label(e.get("date"), comp.get("timeValid", True))
        season = e.get("season") or {}
        home, away = _team(sides["home"]), _team(sides["away"])
        if status != "FINAL":
            home["winner"] = away["winner"] = None
        games.append({
            "id": str(e.get("id")),
            "espn_id": str(e.get("id")),
            "nba_game_id": None,
            "status": status,
            "status_text": status_text,
            "period": period,
            "clock": clock,
            "tip_utc": e.get("date"),
            "date": date_str,
            "season": int(season.get("year") or 0) or None,
            "kind": SEASON_TYPE_KIND.get(int(season.get("type") or 0), "Game"),
            "neutral_site": bool(comp.get("neutralSite")),
            "venue": ((comp.get("venue") or {}).get("fullName")),
            "note": "; ".join(n.get("headline", "") for n in comp.get("notes", []) or [] if n.get("headline")) or None,
            "away": away,
            "home": home,
        })
    games.sort(key=lambda g: (g["tip_utc"] or "", g["id"]))
    return games


def scoreboard(date_str: str):
    """Every NBA game ESPN lists for a US date (YYYY-MM-DD): preseason, regular season, play-in and
    playoffs, with status (SCHEDULED / LIVE / FINAL / POSTPONED ...), scores, records and the game's
    kind. [] when ESPN lists nothing that day; None when ESPN couldn't be reached."""
    try:
        day = date.fromisoformat(date_str)
    except ValueError:
        return None
    ttl = _TTL_PAST if day < eastern_today() else _TTL_LIVE

    def make():
        try:
            return parse_scoreboard(_get(SCOREBOARD_URL, {"dates": day.strftime("%Y%m%d"), "limit": 200}), date_str)
        except Exception:
            return None
    return _cached(("scoreboard", date_str), ttl, make)


# ─── Box score ──────────────────────────────────────────────────────────────

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _plus_minus(v):
    if v in (None, "", "--"):
        return None
    try:
        return int(str(v).replace("+", ""))
    except ValueError:
        return None


def parse_boxscore(payload):
    """ESPN summary JSON -> {"away": [row...], "home": [row...], "status": ..., "teams": {...}}.
    A row: name, espn_athlete_id, starter, min (None for a DNP), pts, reb, ast, stl, blk, tov, fg,
    three, ft, pm (int or None), dnp_reason."""
    header = (payload.get("header") or {}).get("competitions") or [{}]
    comps = header[0].get("competitors", []) or []
    side_by_team = {str((c.get("team") or {}).get("id")): c.get("homeAway") for c in comps}
    status, status_text, period, clock = _status(header[0]) if header and header[0] else ("UNKNOWN", None, 0, "")
    out = {"away": [], "home": [], "status": status, "status_text": status_text, "teams": {}}
    box = payload.get("boxscore") or {}
    for tm in box.get("players", []) or []:
        team = tm.get("team") or {}
        side = side_by_team.get(str(team.get("id")))
        if side not in ("home", "away"):
            continue
        out["teams"][side] = {"abbr": nba_code(team.get("abbreviation")), "name": team.get("displayName")}
        stats = (tm.get("statistics") or [{}])[0]
        names = stats.get("names") or []
        rows = []
        for a in stats.get("athletes", []) or []:
            vals = dict(zip(names, a.get("stats") or []))
            athlete = a.get("athlete") or {}
            dnp = bool(a.get("didNotPlay")) or not vals
            rows.append({
                "name": athlete.get("displayName") or athlete.get("shortName") or "Unknown",
                "espn_athlete_id": str(athlete.get("id") or "") or None,
                "starter": bool(a.get("starter")),
                "min": None if dnp else str(vals.get("MIN", "0")),
                "pts": 0 if dnp else (_int(vals.get("PTS")) or 0),
                "reb": 0 if dnp else (_int(vals.get("REB")) or 0),
                "ast": 0 if dnp else (_int(vals.get("AST")) or 0),
                "stl": 0 if dnp else (_int(vals.get("STL")) or 0),
                "blk": 0 if dnp else (_int(vals.get("BLK")) or 0),
                "tov": 0 if dnp else (_int(vals.get("TO")) or 0),
                "fg": "0-0" if dnp else str(vals.get("FG", "0-0")),
                "three": "0-0" if dnp else str(vals.get("3PT", "0-0")),
                "ft": "0-0" if dnp else str(vals.get("FT", "0-0")),
                "pm": None if dnp else _plus_minus(vals.get("+/-")),
                "dnp_reason": (a.get("reason") or None) if dnp else None,
            })
        out[side] = rows
    return out


def boxscore(espn_id: str):
    """One game's box score from ESPN's summary, or None when ESPN couldn't be reached. For a game
    that hasn't started the player lists are empty and status is SCHEDULED."""
    espn_id = str(espn_id or "").strip()
    if not espn_id.isdigit():
        return None

    def make():
        try:
            parsed = parse_boxscore(_get(SUMMARY_URL, {"event": espn_id}, timeout=5.0))
        except Exception:
            return None
        return parsed
    value = _cached(("boxscore", espn_id), _TTL_PAST, make)
    # Only a final game may stay cached for hours; a live or scheduled one is re-read each minute.
    if value is not None and value.get("status") != "FINAL":
        with _cache_lock:
            hit = _cache.get(("boxscore", espn_id))
            if hit and time.time() - hit[0] >= _TTL_LIVE:
                _cache.pop(("boxscore", espn_id), None)
    return value


# ─── Standings ──────────────────────────────────────────────────────────────

def parse_standings(payload):
    """ESPN standings JSON (regular season) -> {"eastern": [...], "western": [...]}; each row has
    rank (playoff seed), abbr, team (ESPN's name; the caller may replace it), w, l, pct ('.732'),
    gb, last10, streak. Rows are in seed order; before the first game every seed is 0 on ESPN and
    the rows are numbered in name order."""
    out = {"eastern": [], "western": []}
    for conf in payload.get("children", []) or []:
        name = str(conf.get("name") or "").lower()
        key = "eastern" if name.startswith("east") else ("western" if name.startswith("west") else None)
        if key is None:
            continue
        rows = []
        for entry in (conf.get("standings") or {}).get("entries", []) or []:
            team = entry.get("team") or {}
            st = {s.get("name"): s for s in entry.get("stats", []) or []}

            def disp(k, default="-"):
                v = (st.get(k) or {}).get("displayValue")
                return v if v not in (None, "") else default

            def val(k, default=0):
                v = (st.get(k) or {}).get("value")
                return default if v is None else v
            wins, losses = int(val("wins")), int(val("losses"))
            pct = float(val("winPercent"))
            rows.append({
                "rank": int(val("playoffSeed")),
                "abbr": nba_code(team.get("abbreviation")),
                "team": team.get("displayName") or nba_code(team.get("abbreviation")),
                "w": wins,
                "l": losses,
                "pct": f".{int(round(pct * 1000)):03d}",
                "gb": disp("gamesBehind"),
                "last10": disp("Last Ten Games"),
                "streak": disp("streak"),
            })
        if rows and all(r["rank"] == 0 for r in rows):
            # Before the first game ESPN gives every team seed 0: number them in name order so the
            # table doesn't read "0" fifteen times (the record column already says 0-0).
            rows.sort(key=lambda r: r["team"])
            for i, r in enumerate(rows, start=1):
                r["rank"] = i
        else:
            rows.sort(key=lambda r: (r["rank"] or 99, r["team"]))
        out[key] = rows
    if not out["eastern"] and not out["western"]:
        return None
    return out


def standings(season: int):
    """Regular-season standings of a season (end year: 2027 = 2026-27), from ESPN; None when it
    couldn't be reached. ESPN's default counts preseason games before opening night, so the
    regular-season type is asked for explicitly."""
    def make():
        try:
            return parse_standings(_get(STANDINGS_URL, {"season": int(season), "seasontype": 2}))
        except Exception:
            return None
    return _cached(("standings", int(season)), _TTL_STANDINGS, make)


def played(standings_obj) -> bool:
    """True once any team has a decision in these standings."""
    if not standings_obj:
        return False
    return any((r.get("w") or 0) + (r.get("l") or 0) > 0 for conf in ("eastern", "western") for r in standings_obj.get(conf, []))


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
