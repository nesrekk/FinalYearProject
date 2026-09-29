"""
fetch_postseason_games.py
==========================
Every play-in and playoff game 2009-10 to 2025-26 from ESPN's public
scoreboard (the same endpoint as fetch_game_scores.py), so the season
simulator's backtest knows who really made the playoffs, including seasons
whose postseason isn't in `player_shots` yet (2025-26 at the time of
writing).

For each season the dates from the day after the last regular-season game
in `game_scores` to 75 days later are fetched (the 2019-20 restart's
postseason ran to October 11). ESPN marks each game's season type
(5 = play-in, 3 = playoffs) and a headline note ("East 1st Round - Game 1",
"NBA Finals - Game 4", "NBA Play-In - West - 7th Place vs 8th Place").
Team codes are the NBA's, with the codes the rest of the database uses in
that season (NJN before 2012-13, NOH before 2013-14).

Table written (dropped and rebuilt): postseason_games
  espn_id, season (end year), game_date, stage ('play-in' | 'playoffs'),
  round ('Play-In', '1st Round', 'Conf Semifinals', 'Conf Finals', 'NBA Finals'),
  conference (East/West/NULL for the Finals), home, away, pts_home, pts_away,
  winner, note (ESPN's headline).

Usage:
    cd scripts && python3 fetch_postseason_games.py        (~3 min)
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg2
import requests
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NJ": "BKN"}
WINDOW_DAYS = 75


def season_code(abbr, season):
    abbr = ESPN_TO_NBA.get(abbr, abbr)
    if abbr == "BKN" and season <= 2012:
        return "NJN"
    if abbr == "NOP" and season <= 2013:
        return "NOH"
    return abbr


def fetch_date(day):
    for attempt in range(4):
        try:
            r = requests.get(URL, params={"dates": day.strftime("%Y%m%d"), "limit": 100}, timeout=30)
            r.raise_for_status()
            return day, r.json().get("events", [])
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"ESPN scoreboard failed for {day}")


def parse(events, season):
    rows = []
    for e in events:
        stype = e.get("season", {}).get("type")
        c = e["competitions"][0]
        notes = " ".join(n.get("headline", "") for n in c.get("notes", []))
        up = notes.upper()      # older seasons' notes are all caps ("EASTERN CONFERENCE SEMIFINALS - GAME 1")
        if stype == 5 or "PLAY-IN" in up:
            stage = "play-in"
        elif stype == 3:
            stage = "playoffs"
        else:
            continue
        if not c["status"]["type"].get("completed"):
            continue
        sides = {t["homeAway"]: t for t in c["competitors"]}
        home, away = sides["home"], sides["away"]
        m = re.search(r"\b(EAST|WEST)", up)     # "East Finals - Game 1" since 2017-18, "EASTERN CONFERENCE FINALS" before
        rnd = ("Play-In" if stage == "play-in"
               else "Conf Semifinals" if "SEMI" in up
               else ("Conf Finals" if m else "NBA Finals") if "FINAL" in up
               else "1st Round" if ("1ST" in up or "FIRST" in up) else None)
        rows.append((e["id"], season, e["date"][:10], stage, rnd, m.group(1).title() if m else None,
                     season_code(home["team"]["abbreviation"], season), season_code(away["team"]["abbreviation"], season),
                     int(home["score"]), int(away["score"]),
                     season_code((home if home.get("winner") else away)["team"]["abbreviation"], season), notes))
    return rows


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT season, MAX(game_date) FROM game_scores GROUP BY season ORDER BY season")
    seasons = cur.fetchall()
    rows = []
    for season, last in seasons:
        days = [last + timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)]
        with ThreadPoolExecutor(max_workers=8) as ex:
            for day, events in ex.map(fetch_date, days):
                rows.extend(parse(events, season))
        n = [r for r in rows if r[1] == season]
        print(f"{season}: {len(n)} postseason games ({sum(r[3] == 'play-in' for r in n)} play-in), "
              f"{min(r[2] for r in n) if n else '-'} to {max(r[2] for r in n) if n else '-'}")
    cur.execute("DROP TABLE IF EXISTS postseason_games")
    cur.execute("""CREATE TABLE postseason_games (espn_id TEXT PRIMARY KEY, season INTEGER, game_date DATE, stage TEXT,
                   round TEXT, conference TEXT, home TEXT, away TEXT, pts_home INTEGER, pts_away INTEGER, winner TEXT,
                   note TEXT)""")
    execute_values(cur, "INSERT INTO postseason_games VALUES %s", rows)
    conn.commit()
    print(f"wrote {len(rows)} games ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
