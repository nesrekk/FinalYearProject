"""
fetch_game_scores.py
=====================
Final scores for every regular-season game in `team_game_fatigue`
(2009-10 to 2025-26), from ESPN's public scoreboard endpoint, one request
per game date.

Why: `team_game_fatigue.plus_minus` comes from stats.nba.com's
LeagueGameFinder PLUS_MINUS, which is the team's summed player plus-minus
divided by five, not the final margin. In 109 games the two teams' margins
don't add to zero (e.g. TOR +33.6 / CHI -32), in 13 games the margin's sign
disagrees with the stored win flag, and 3 team-games carry a margin of 0;
in all, 160 games differ from the real final score (134 by a point or more).
Stored W-L records are right (they match Basketball-Reference for every
team-season), but anything that needs the real margin of each game (Luck &
Schedule: margin-based expected wins, close-game records, SRS) reads this
table instead. stats.nba.com is unreachable from this machine, so the scores
come from ESPN, the same source as the 2020-21+ play-by-play.

Matching: each ESPN event is matched to an NBA game id by date + the pair of
teams (ESPN abbreviations crosswalked; NJN/BKN and NOH/NOP treated as the
same franchise; a day either side for the few games ESPN dates differently). Every stored game is checked against `team_game_fatigue`'s
win flag, and for 2020-21 on against the play-by-play final score in
`team_game_totals`; the script prints both.

Table written (dropped and rebuilt): game_scores, one row per team-game
  (same rows and abbreviations as team_game_fatigue): game_id (NBA id),
  team_abbreviation, opponent, season, game_date, is_home, neutral_site,
  pts_for, pts_against, periods (4 = no overtime), espn_id.

Usage:
    cd scripts && python3 fetch_game_scores.py
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_TO_NBA = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "NJ": "BKN"}
FRANCHISE = {"NJN": "BKN", "NOH": "NOP"}   # historical NBA abbreviations -> matching key


def _key(abbr):
    abbr = ESPN_TO_NBA.get(abbr, abbr)
    return FRANCHISE.get(abbr, abbr)


def fetch_date(day):
    for attempt in range(4):
        try:
            r = requests.get(URL, params={"dates": day.strftime("%Y%m%d"), "limit": 100}, timeout=30)
            r.raise_for_status()
            out = []
            for e in r.json().get("events", []):
                comp = e["competitions"][0]
                if not comp.get("status", {}).get("type", {}).get("completed"):
                    continue
                sides = {c["homeAway"]: c for c in comp["competitors"]}
                if set(sides) != {"home", "away"}:
                    continue
                out.append({
                    "espn_id": e["id"], "game_date": day,
                    "home_key": _key(sides["home"]["team"]["abbreviation"]),
                    "away_key": _key(sides["away"]["team"]["abbreviation"]),
                    "home_pts": int(sides["home"]["score"]), "away_pts": int(sides["away"]["score"]),
                    "periods": int(comp["status"].get("period") or 0),
                })
            return out
        except Exception as exc:  # network hiccup: back off and retry
            if attempt == 3:
                raise RuntimeError(f"{day}: {exc}") from exc
            time.sleep(2 * (attempt + 1))


def _pair(a, b):
    return tuple(sorted((_key(a), _key(b))))


def _match(games, espn):
    """Match each NBA game to one ESPN event by date and the pair of teams (not
    home/away: neutral-site games in Mexico City, Paris, Berlin, London and the
    NBA Cup semifinals in Las Vegas are stored with both teams away). A few games
    ESPN files under the next or previous date are matched within a day."""
    espn = espn.assign(pair=[_pair(h, a) for h, a in zip(espn.home_key, espn.away_key)])
    by_key = {}
    for e in espn.itertuples():
        by_key.setdefault((e.game_date, e.pair), []).append(e)
    used, out = set(), {}
    for shift in (0, 1, -1):
        for g in games.itertuples():
            if g.game_id in out:
                continue
            day = g.game_date + pd.Timedelta(days=shift)
            cand = [e for e in by_key.get((day, g.pair), []) if e.espn_id not in used]
            if len(cand) == 1:
                used.add(cand[0].espn_id)
                out[g.game_id] = cand[0]
    return out


def _cached_events(dates):
    """ESPN events for the dates; FETCH_CACHE=<path> reuses a pickle between runs."""
    import os
    cache = os.environ.get("FETCH_CACHE")
    if cache and os.path.exists(cache):
        return pd.read_pickle(cache)
    with ThreadPoolExecutor(max_workers=6) as pool:
        events = [ev for day in pool.map(fetch_date, dates) for ev in day]
    espn = pd.DataFrame(events).drop_duplicates("espn_id")
    if cache:
        espn.to_pickle(cache)
    return espn


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    rows = pd.read_sql("""SELECT game_id, season, game_date, team_abbreviation, opponent, is_home, win
                          FROM team_game_fatigue""", conn)
    # The opponent comes from the game's other row: in 5 neutral-site games
    # team_game_fatigue.opponent names the team itself (its MATCHUP parse).
    both = rows.groupby("game_id").team_abbreviation.agg(["min", "max"])
    rows["opponent"] = [both.at[g, "max"] if t == both.at[g, "min"] else both.at[g, "min"]
                        for g, t in zip(rows.game_id, rows.team_abbreviation)]
    games = rows.drop_duplicates("game_id")[["game_id", "season", "game_date", "team_abbreviation", "opponent"]]
    games = games.assign(pair=[_pair(a, b) for a, b in zip(games.team_abbreviation, games.opponent)])
    dates = sorted(games.game_date.unique())
    print(f"{len(games):,} games on {len(dates):,} dates; fetching ESPN scoreboards...")
    espn = _cached_events(dates)
    print(f"{len(espn):,} completed ESPN events ({time.time() - t0:.0f}s)")

    matched = _match(games, espn)
    missing = games[~games.game_id.isin(matched)]
    print(f"matched {len(matched):,} of {len(games):,}; unmatched:")
    print(missing[["game_id", "game_date", "team_abbreviation", "opponent"]].to_string() if len(missing) else "  none")
    shifted = sum(1 for g in games.itertuples() if g.game_id in matched and matched[g.game_id].game_date != g.game_date)
    print(f"matched on an adjacent ESPN date: {shifted}")

    neutral = set(rows.groupby("game_id").is_home.sum().loc[lambda s: s == 0].index)
    out = []
    for r in rows.itertuples():
        e = matched.get(r.game_id)
        if e is None:
            continue
        mine_home = _key(r.team_abbreviation) == e.home_key
        pf, pa = (e.home_pts, e.away_pts) if mine_home else (e.away_pts, e.home_pts)
        out.append((r.game_id, r.team_abbreviation, r.opponent, int(r.season), r.game_date, bool(r.is_home),
                    r.game_id in neutral, int(pf), int(pa), int(e.periods), e.espn_id, bool(r.win)))
    df = pd.DataFrame(out, columns=["game_id", "team_abbreviation", "opponent", "season", "game_date", "is_home",
                                    "neutral_site", "pts_for", "pts_against", "periods", "espn_id", "win"])

    wrong = df[(df.pts_for > df.pts_against) != df.win]
    ties = df[df.pts_for == df.pts_against]
    print(f"team-games: {len(df):,}; winner disagrees with team_game_fatigue.win: {len(wrong)}; ties: {len(ties)}; "
          f"neutral-site games: {len(neutral)}")

    ttot = pd.read_sql("""SELECT game_date, team_abbreviation, pts_for AS pbp_for, pts_against AS pbp_against
                          FROM team_game_totals""", conn)
    chk = df.merge(ttot, on=["game_date", "team_abbreviation"], how="inner")
    bad = chk[(chk.pts_for != chk.pbp_for) | (chk.pts_against != chk.pbp_against)]
    print(f"vs play-by-play final scores (2020-21+): {len(chk) // 2:,} games compared, {len(bad) // 2} differ")

    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS game_scores")
    cur.execute("""CREATE TABLE game_scores (
                       game_id TEXT, team_abbreviation TEXT, opponent TEXT, season INTEGER, game_date DATE,
                       is_home BOOLEAN, neutral_site BOOLEAN, pts_for INTEGER, pts_against INTEGER,
                       periods INTEGER, espn_id TEXT,
                       PRIMARY KEY (game_id, team_abbreviation))""")
    execute_values(cur, "INSERT INTO game_scores VALUES %s",
                   [t[:-1] for t in df.itertuples(index=False, name=None)])
    cur.execute("CREATE INDEX ON game_scores (season, team_abbreviation)")
    conn.commit()
    print(f"game_scores: {len(df):,} rows ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
