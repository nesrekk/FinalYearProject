"""
ledger_espn.py
===============
ESPN reads for the Forecast Ledger (Teams > Forecast Ledger): a season's
regular-season schedule and every team's current roster. Import-only; the
scripts that store what these return are scripts/ledger_lock.py (the
preseason lock) and, from round 6 step 2, the nightly update.

  fetch_schedule(season)   every regular-season event ESPN lists for a season
                           (season = end year, 2027 = 2026-27), one scoreboard
                           request per date of ESPN's own calendar. Events whose
                           teams are not known yet (NBA Cup knockout games) come
                           back with home/away None. game_date is the US date the
                           scoreboard was asked for (as in fetch_game_scores.py);
                           tip_utc is ESPN's start time.
  fetch_rosters()          every team's roster as ESPN lists it right now:
                           athlete id, name, birth date, position, experience,
                           ESPN's injury status, and whether a contract for the
                           season is attached.

ESPN abbreviations are mapped to the NBA codes the rest of the database uses
(GS -> GSW, NO -> NOP, NY -> NYK, SA -> SAS, UTAH -> UTA, WSH -> WAS), the
same crosswalk as fetch_game_scores.py.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
TEAMS = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{}/roster"
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NJ": "BKN"}
REGULAR_SEASON = 2


def nba_code(abbr):
    return ESPN_TO_NBA.get(abbr, abbr)


def _get(url, params=None, tries=4):
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # network hiccup: back off and retry
            if attempt == tries - 1:
                raise RuntimeError(f"{url} {params}: {exc}") from exc
            time.sleep(2 * (attempt + 1))


def calendar_dates(season):
    """The US dates ESPN's calendar lists for a season (preseason through the Finals), as YYYYMMDD."""
    j = _get(SCOREBOARD, {"dates": f"{season - 1}1101", "limit": 1})
    league = j["leagues"][0]
    if int(league["season"]["year"]) != season:
        raise SystemExit(f"ESPN's calendar is for season {league['season']['year']}, not {season}")
    return sorted({c[:10].replace("-", "") for c in league["calendar"]})


def _event_row(day, e):
    comp = e["competitions"][0]
    sides = {c["homeAway"]: c for c in comp["competitors"]}
    notes = "; ".join(n.get("headline", "") for n in comp.get("notes", []) if n.get("headline"))

    def team(side):
        t = sides.get(side, {}).get("team", {})
        abbr = t.get("abbreviation")
        return None if abbr in (None, "TBD") else nba_code(abbr)
    return {
        "espn_id": str(e["id"]), "game_date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "tip_utc": e["date"],
        "time_valid": bool(comp.get("timeValid")), "home": team("home"), "away": team("away"),
        "neutral_site": bool(comp.get("neutralSite")), "venue": comp.get("venue", {}).get("fullName"),
        "city": comp.get("venue", {}).get("address", {}).get("city"), "note": notes,
        "status": comp.get("status", {}).get("type", {}).get("name"),
    }


def fetch_schedule(season, dates=None):
    """Every regular-season event of a season, one row per ESPN event id (sorted by date, tip, id)."""
    dates = dates or calendar_dates(season)

    def one(day):
        return day, _get(SCOREBOARD, {"dates": day, "limit": 200}).get("events", [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(one, dates))
    rows, seen = [], set()
    for day, events in got:
        for e in events:
            s = e.get("season", {})
            if int(s.get("year", 0)) != season or int(s.get("type", 0)) != REGULAR_SEASON or e["id"] in seen:
                continue
            seen.add(e["id"])
            rows.append(_event_row(day, e))
    return sorted(rows, key=lambda r: (r["game_date"], r["tip_utc"], r["espn_id"]))


def fetch_rosters():
    """(rows, fetched_at): one row per rostered athlete of every team ESPN lists, and the UTC time of the read."""
    fetched_at = datetime.now(timezone.utc)
    teams = _get(TEAMS)["sports"][0]["leagues"][0]["teams"]
    ids = [(t["team"]["id"], nba_code(t["team"]["abbreviation"])) for t in teams]

    def one(pair):
        tid, code = pair
        j = _get(ROSTER.format(tid))
        out = []
        for a in j.get("athletes", []):
            inj = a.get("injuries") or []
            out.append({
                "team": code, "espn_team_id": str(tid), "espn_athlete_id": str(a["id"]),
                "player_name": a.get("fullName") or a.get("displayName"),
                "birth_date": (a.get("dateOfBirth") or "")[:10] or None,
                "position": (a.get("position") or {}).get("abbreviation"),
                "experience": (a.get("experience") or {}).get("years"),
                "espn_status": (a.get("status") or {}).get("name"),
                "injury_status": inj[0].get("status") if inj else None,
                "has_contract": bool(a.get("contract")),
                "roster_season": (j.get("season") or {}).get("year"),
            })
        return out
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = [r for team in pool.map(one, ids) for r in team]
    return rows, fetched_at
