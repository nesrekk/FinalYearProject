"""
load_nba75_team.py
===================================
One-time load of the real NBA 75th Anniversary Team (76 players, announced
by the NBA on 2021-10-21) into a new nba75_team table, for badging on the
Hall of Fame page and anywhere else a real, official accolade is useful.

This is real, publicly documented data (sourced from Wikipedia's "NBA 75th
Anniversary Team" article, cross-checked name-for-name against nba_api's
own static player list — all 76 names resolved to a real nba_api player_id
with an exact match, no fuzzy guessing needed). One name is shared with
another player (Patrick Ewing and his son); fixed 2026-09-27 to take the one
with the long career instead of whichever id the name lookup kept last,
which had put the son on the team. It is NOT the same as real
Naismith Basketball Hall of Fame induction, which this project has no
dataset for from any source it uses — kept as a clearly separate, smaller,
real, sourced list rather than conflated with "Hall of Fame."

Usage:
    python load_nba75_team.py
"""

import psycopg2
from psycopg2.extras import execute_values
from nba_api.stats.static import players as nba_static_players

from db_config import DB_CONFIG

# Source: https://en.wikipedia.org/wiki/NBA_75th_Anniversary_Team
NBA75_NAMES = [
    "Kareem Abdul-Jabbar", "Ray Allen", "Giannis Antetokounmpo", "Carmelo Anthony",
    "Nate Archibald", "Paul Arizin", "Charles Barkley", "Rick Barry", "Elgin Baylor",
    "Dave Bing", "Larry Bird", "Kobe Bryant", "Wilt Chamberlain", "Bob Cousy",
    "Dave Cowens", "Billy Cunningham", "Stephen Curry", "Anthony Davis",
    "Dave DeBusschere", "Clyde Drexler", "Tim Duncan", "Kevin Durant",
    "Julius Erving", "Patrick Ewing", "Walt Frazier", "Kevin Garnett",
    "George Gervin", "Hal Greer", "James Harden", "John Havlicek", "Elvin Hayes",
    "Allen Iverson", "LeBron James", "Magic Johnson", "Sam Jones", "Michael Jordan",
    "Jason Kidd", "Kawhi Leonard", "Damian Lillard", "Jerry Lucas", "Karl Malone",
    "Moses Malone", "Pete Maravich", "Bob McAdoo", "Kevin McHale", "George Mikan",
    "Reggie Miller", "Earl Monroe", "Steve Nash", "Dirk Nowitzki", "Hakeem Olajuwon",
    "Shaquille O'Neal", "Robert Parish", "Chris Paul", "Gary Payton", "Bob Pettit",
    "Paul Pierce", "Scottie Pippen", "Willis Reed", "Oscar Robertson",
    "David Robinson", "Dennis Rodman", "Bill Russell", "Dolph Schayes",
    "Bill Sharman", "John Stockton", "Isiah Thomas", "Nate Thurmond", "Wes Unseld",
    "Dwyane Wade", "Bill Walton", "Jerry West", "Russell Westbrook",
    "Lenny Wilkens", "Dominique Wilkins", "James Worthy",
]


def resolve_player_ids():
    static = {}
    for p in nba_static_players.get_players():
        static.setdefault(p["full_name"], []).append(p["id"])
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT player_id, SUM(gp) FROM player_season_stats GROUP BY 1;")
    career_gp = dict(cur.fetchall())
    conn.close()
    resolved, unmatched = [], []
    for name in NBA75_NAMES:
        ids = static.get(name, [])
        if len(ids) > 1:
            # A shared name (Patrick Ewing and his son): the 75th-team member
            # is the one with the long career. Fails loudly if that's unclear.
            ranked = sorted(ids, key=lambda i: career_gp.get(i) or 0, reverse=True)
            top, second = (career_gp.get(i) or 0 for i in ranked[:2])
            if top < 500 or second > top / 4:
                raise RuntimeError(f"Can't tell which {name} ({ids}) is on the team: career games {top} vs {second}")
            ids = ranked[:1]
        if not ids:
            unmatched.append(name)
        else:
            resolved.append((ids[0], name))
    if unmatched:
        raise RuntimeError(f"Could not resolve {len(unmatched)} real NBA75 names to a player_id: {unmatched}")
    return resolved


def save(rows):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nba75_team (
            player_id INTEGER PRIMARY KEY,
            player_name TEXT NOT NULL
        );
        """
    )
    cur.execute("DELETE FROM nba75_team;")
    execute_values(cur, "INSERT INTO nba75_team (player_id, player_name) VALUES %s;", rows)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    rows = resolve_player_ids()
    print(f"Resolved all {len(rows)} real NBA 75th Anniversary Team players to nba_api player_ids.")
    save(rows)
    print(f"Saved {len(rows)} rows to nba75_team.")
