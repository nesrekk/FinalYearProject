"""
espn_summary.py
================
ESPN's game summary (site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>) turned into the rows
fetch_pbp_espn.py stores in pbp_games / pbp_events, mapped the way the hosted sportsdataverse release was (round 9
step 2, docs/qa/ROUND9_ISSUES.md R9-004: that release was last updated 2026-09-09 and has no 2026-27 file, so the
live season's play-by-play comes from this endpoint, one call per final, ~1.5 s and 440 kB). Import-only; the
script that stores what this returns is scripts/daily_update.py.

The mapping, checked on 2,069 stored events of four 2025-26 games (BOS-ORL, IND-DET and DAL-CHI of 2026-04-12,
the two-overtime OKC-HOU of 2025-10-21): every column of every event equals the release's row.
  action_number       the play's position in `plays`, 1-based (hoopR's game_play_number)
  period              period.number
  seconds_remaining   the NEXT play's start, in game seconds (2160/1440/720 added for Q1-Q3; Q4 and overtime use the
                      period's own clock), except the first play (2880) and the first play of a period (2160, 1440,
                      720, or 300 in overtime); the last play has none and is not stored (495 plays -> 494 rows).
                      The release's clock columns were Float32 and were stored to four decimals, so a clock of 55.1 s
                      left in Q1 is 2215.1001, not 2215.1: start_seconds() reproduces that (R9-004).
  score_home/away     homeScore / awayScore
  team_id             team.id (ESPN's id); team_tricode the NBA code of that side (fetch_pbp_espn.TEAM_CROSSWALK)
  person_id, player_name   the first participant's athlete, named from the box score (displayName), matched to an
                      NBA id by fetch_pbp_espn.PlayerMatcher for the season (exact name that season, exact name in
                      any season, then fuzzy); unmatched keeps ESPN's name with no id, as the release's rows do
  action_type         type.text;  sub_type NULL;  description = text, else shortDescription
pbp_games: game_id 'espn_<id>', season (end year), game_date = the tip's US Eastern date, home/away NBA codes,
home_win from the final score, source 'espn'.

Also here: the officials' names (gameInfo.officials; names only, so daily_update.py takes ids from
BoxScoreSummaryV3 instead), and summary_complete() (a final with a full play-by-play; ESPN publishes the plays
with the final score, but a summary read while the box is being closed can be short).
"""

import gzip
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import requests

from fetch_pbp_espn import REAL_NBA_TRICODES, _resolve_team

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
EASTERN = ZoneInfo("America/New_York")
MIN_PLAYS = 100       # a final NBA game has 400-600 plays; fewer means ESPN hasn't published the play-by-play yet
PERIOD_BASE = {1: 2160, 2: 1440, 3: 720}   # game seconds left at the end of Q1-Q3; Q4 and overtime count their own clock


def fetch_summary(espn_id, timeout=30, tries=4):
    """The summary JSON of one ESPN event (up to `tries` attempts with growing waits)."""
    for attempt in range(tries):
        try:
            r = requests.get(SUMMARY_URL, params={"event": str(espn_id)}, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # network hiccup: back off and retry
            if attempt == tries - 1:
                raise RuntimeError(f"ESPN summary {espn_id}: {exc}") from exc
            time.sleep(2 * (attempt + 1))


def save_summary(summary, path):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(summary, f, separators=(",", ":"))


def load_summary(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def competition(summary):
    return summary["header"]["competitions"][0]


def sides(summary):
    """{'home': {...}, 'away': {...}}: ESPN team id, NBA code, final score, winner flag."""
    out = {}
    for c in competition(summary)["competitors"]:
        team = c.get("team") or {}
        out[c["homeAway"]] = {"espn_team_id": str(team.get("id")), "code": _resolve_team(team.get("abbreviation")),
                              "score": int(c["score"]) if c.get("score") not in (None, "") else None,
                              "winner": bool(c.get("winner"))}
    return out


def season_of(summary):
    """(season end year, ESPN season type: 1 preseason, 2 regular season, 3 playoffs, 5 play-in)."""
    s = summary["header"]["season"]
    return int(s["year"]), int(s["type"])


def game_date_et(summary):
    """The tip's US Eastern date (the date every table in the database uses, round 8 R8-067)."""
    return datetime.fromisoformat(competition(summary)["date"].replace("Z", "+00:00")).astimezone(EASTERN).date()


def is_final(summary):
    st = (competition(summary).get("status") or {}).get("type") or {}
    return bool(st.get("completed")) or st.get("name") == "STATUS_FINAL"


def summary_complete(summary):
    """A final whose play-by-play is published (at least MIN_PLAYS plays and both scores)."""
    s = sides(summary)
    return (is_final(summary) and len(summary.get("plays") or []) >= MIN_PLAYS and "home" in s and "away" in s
            and s["home"]["score"] is not None and s["away"]["score"] is not None)


def athlete_names(summary):
    """ESPN athlete id -> display name, from the box score (every player on either roster that night)."""
    names = {}
    for team in (summary.get("boxscore") or {}).get("players") or []:
        for block in team.get("statistics") or []:
            for a in block.get("athletes") or []:
                ath = a.get("athlete") or {}
                if ath.get("id") and ath.get("displayName"):
                    names[str(ath["id"])] = ath["displayName"]
    return names


def officials(summary):
    """The officials' names in ESPN's order (no ids: game_officials takes ids from BoxScoreSummaryV3)."""
    return [o.get("fullName") or o.get("displayName") for o in (summary.get("gameInfo") or {}).get("officials") or []]


def clock_parts(display):
    """'1:34' -> (1.0, 34.0); '45.3' -> (0.0, 45.3) (hoopR prefixes '0:' when there is no colon)."""
    if ":" not in display:
        display = "0:" + display
    mm, ss = display.split(":", 1)
    return float(mm), float(ss)


def start_seconds(period, mm, ss):
    """hoopR's start.game_seconds_remaining as the release stored it: Float32 arithmetic (the release's clock
    columns were Float32), 2160/1440/720 added in Q1-Q3, the period's own clock in Q4 and overtime, rounded to four
    decimals (the release's text form: 2215.1001 for 55.1 s left in Q1, 777.3 for 57.3 s left in Q3)."""
    q = np.float32(60) * np.float32(mm) + np.float32(ss)
    base = PERIOD_BASE.get(int(period), 0)
    v = np.float32(base) + q if base else q
    return round(float(v), 4)


def end_seconds(plays):
    """end_game_seconds_remaining per play (None for the last one), hoopR's rule: the next play's start, except
    2880 for the first play and 2160 / 1440 / 720 / 300 for the first play of Q2 / Q3 / Q4 / an overtime."""
    starts = [start_seconds(p["period"]["number"], *clock_parts(p["clock"]["displayValue"])) for p in plays]
    out = []
    for i, p in enumerate(plays):
        per = int(p["period"]["number"])
        lag = int(plays[i - 1]["period"]["number"]) if i else None
        if i == 0:
            v = 2880.0
        elif lag == 1 and per == 2:
            v = 2160.0
        elif lag == 2 and per == 3:
            v = 1440.0
        elif lag == 3 and per == 4:
            v = 720.0
        elif per >= 5 and lag == per - 1:
            v = 300.0
        else:
            v = starts[i + 1] if i + 1 < len(plays) else None
        out.append(v)
    return out


def event_rows(summary, season, matcher):
    """(game_row, event_rows, raw_names) for fetch_pbp_espn's INSERT statements: game_row = (game_id, season,
    game_date, home_team, away_team, home_win, 'espn'); each event row = (game_id, action_number, period,
    seconds_remaining, score_home, score_away, team_id, team_tricode, person_id, player_name, action_type, sub_type,
    description); raw_names = {ESPN athlete id: display name} of every participant seen. `matcher` is a
    fetch_pbp_espn.PlayerMatcher (its season map must hold the season's player_season_stats rows)."""
    s = sides(summary)
    espn_id = str(summary["header"]["id"])
    db_game_id = f"espn_{espn_id}"
    home, away = s["home"], s["away"]
    if home["code"] not in REAL_NBA_TRICODES or away["code"] not in REAL_NBA_TRICODES:
        raise ValueError(f"{db_game_id}: teams {home['code']} / {away['code']} are not NBA teams (an All-Star game?)")
    names = athlete_names(summary)
    plays = summary["plays"]
    ends = end_seconds(plays)
    rows, raw_names = [], {}
    for i, p in enumerate(plays):
        if ends[i] is None:
            continue
        team_id = (p.get("team") or {}).get("id")
        tricode = home["code"] if team_id == home["espn_team_id"] else away["code"] if team_id == away["espn_team_id"] else None
        parts = p.get("participants") or []
        athlete = str(parts[0]["athlete"]["id"]) if parts and (parts[0].get("athlete") or {}).get("id") else None
        raw = names.get(athlete) if athlete else None
        person_id, player_name = (None, None)
        if raw:
            raw_names[athlete] = raw
            person_id, player_name = matcher.match(raw, season)
        description = p.get("text") or p.get("shortDescription")
        rows.append((db_game_id, i + 1, int(p["period"]["number"]), ends[i], int(p["homeScore"]), int(p["awayScore"]),
                     int(team_id) if team_id else None, tricode, person_id, player_name,
                     (p.get("type") or {}).get("text"), None, description))
    game_row = (db_game_id, int(season), game_date_et(summary), home["code"], away["code"],
                home["score"] > away["score"], "espn")
    return game_row, rows, raw_names
