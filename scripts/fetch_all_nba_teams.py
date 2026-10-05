"""
fetch_all_nba_teams.py
========================
Build the All-NBA team label dataset (First/Second/Third Team, 5 players
each, 17 seasons) from a static historical roster list — same pattern as
fetch_dpoy_roy_stats.py's DPOY_WINNERS/ROY_WINNERS, just 15 labels per
season instead of 1.

Source: official All-NBA team announcements, cross-checked against
landofbasketball.com/awards/all_nba_teams_year.htm (a third-party historical
reference, not the project's own memory) rather than typed from recall —
same "verify, don't guess" discipline used everywhere else in this project.

Outputs:
    nba_data/all_nba_seasons.csv
    PostgreSQL table: all_nba_seasons (player_id, player_name, season,
    team_tier [1/2/3])

Usage:
    python fetch_all_nba_teams.py
"""

import os
import pandas as pd
import psycopg2

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")

from db_config import DB_CONFIG

# ─── All-NBA Teams, 2009-10 through 2025-26 ─────────────────────────────────
# (season_int, team_tier, player) — team_tier 1/2/3 = First/Second/Third Team.
ALL_NBA_TEAMS = [
    # 2009-10
    (2010, 1, "Kevin Durant"), (2010, 1, "LeBron James"), (2010, 1, "Kobe Bryant"),
    (2010, 1, "Dwyane Wade"), (2010, 1, "Dwight Howard"),
    (2010, 2, "Carmelo Anthony"), (2010, 2, "Dirk Nowitzki"), (2010, 2, "Steve Nash"),
    (2010, 2, "Deron Williams"), (2010, 2, "Amar'e Stoudemire"),
    (2010, 3, "Tim Duncan"), (2010, 3, "Pau Gasol"), (2010, 3, "Joe Johnson"),
    (2010, 3, "Brandon Roy"), (2010, 3, "Andrew Bogut"),
    # 2010-11
    (2011, 1, "Kevin Durant"), (2011, 1, "LeBron James"), (2011, 1, "Kobe Bryant"),
    (2011, 1, "Derrick Rose"), (2011, 1, "Dwight Howard"),
    (2011, 2, "Pau Gasol"), (2011, 2, "Dirk Nowitzki"), (2011, 2, "Dwyane Wade"),
    (2011, 2, "Russell Westbrook"), (2011, 2, "Amar'e Stoudemire"),
    (2011, 3, "LaMarcus Aldridge"), (2011, 3, "Zach Randolph"), (2011, 3, "Manu Ginobili"),
    (2011, 3, "Chris Paul"), (2011, 3, "Al Horford"),
    # 2011-12
    (2012, 1, "LeBron James"), (2012, 1, "Kevin Durant"), (2012, 1, "Dwight Howard"),
    (2012, 1, "Kobe Bryant"), (2012, 1, "Chris Paul"),
    (2012, 2, "Kevin Love"), (2012, 2, "Blake Griffin"), (2012, 2, "Andrew Bynum"),
    (2012, 2, "Tony Parker"), (2012, 2, "Russell Westbrook"),
    (2012, 3, "Carmelo Anthony"), (2012, 3, "Dirk Nowitzki"), (2012, 3, "Tyson Chandler"),
    (2012, 3, "Dwyane Wade"), (2012, 3, "Rajon Rondo"),
    # 2012-13
    (2013, 1, "LeBron James"), (2013, 1, "Kevin Durant"), (2013, 1, "Kobe Bryant"),
    (2013, 1, "Chris Paul"), (2013, 1, "Tim Duncan"),
    (2013, 2, "Carmelo Anthony"), (2013, 2, "Blake Griffin"), (2013, 2, "Tony Parker"),
    (2013, 2, "Russell Westbrook"), (2013, 2, "Marc Gasol"),
    (2013, 3, "David Lee"), (2013, 3, "Paul George"), (2013, 3, "Dwyane Wade"),
    (2013, 3, "James Harden"), (2013, 3, "Dwight Howard"),
    # 2013-14
    (2014, 1, "Kevin Durant"), (2014, 1, "LeBron James"), (2014, 1, "Joakim Noah"),
    (2014, 1, "James Harden"), (2014, 1, "Chris Paul"),
    (2014, 2, "Blake Griffin"), (2014, 2, "Kevin Love"), (2014, 2, "Dwight Howard"),
    (2014, 2, "Stephen Curry"), (2014, 2, "Tony Parker"),
    (2014, 3, "Paul George"), (2014, 3, "LaMarcus Aldridge"), (2014, 3, "Al Jefferson"),
    (2014, 3, "Goran Dragic"), (2014, 3, "Damian Lillard"),
    # 2014-15
    (2015, 1, "LeBron James"), (2015, 1, "Anthony Davis"), (2015, 1, "Marc Gasol"),
    (2015, 1, "Stephen Curry"), (2015, 1, "James Harden"),
    (2015, 2, "LaMarcus Aldridge"), (2015, 2, "DeMarcus Cousins"), (2015, 2, "Pau Gasol"),
    (2015, 2, "Russell Westbrook"), (2015, 2, "Chris Paul"),
    (2015, 3, "Blake Griffin"), (2015, 3, "Tim Duncan"), (2015, 3, "DeAndre Jordan"),
    (2015, 3, "Klay Thompson"), (2015, 3, "Kyrie Irving"),
    # 2015-16
    (2016, 1, "LeBron James"), (2016, 1, "Kawhi Leonard"), (2016, 1, "DeAndre Jordan"),
    (2016, 1, "Stephen Curry"), (2016, 1, "Russell Westbrook"),
    (2016, 2, "Kevin Durant"), (2016, 2, "Draymond Green"), (2016, 2, "DeMarcus Cousins"),
    (2016, 2, "Chris Paul"), (2016, 2, "Damian Lillard"),
    (2016, 3, "Paul George"), (2016, 3, "LaMarcus Aldridge"), (2016, 3, "Andre Drummond"),
    (2016, 3, "Klay Thompson"), (2016, 3, "Kyle Lowry"),
    # 2016-17
    (2017, 1, "LeBron James"), (2017, 1, "Kawhi Leonard"), (2017, 1, "Anthony Davis"),
    (2017, 1, "James Harden"), (2017, 1, "Russell Westbrook"),
    (2017, 2, "Giannis Antetokounmpo"), (2017, 2, "Kevin Durant"), (2017, 2, "Rudy Gobert"),
    (2017, 2, "Stephen Curry"), (2017, 2, "Isaiah Thomas"),
    (2017, 3, "Draymond Green"), (2017, 3, "Jimmy Butler"), (2017, 3, "DeAndre Jordan"),
    (2017, 3, "John Wall"), (2017, 3, "DeMar DeRozan"),
    # 2017-18
    (2018, 1, "Anthony Davis"), (2018, 1, "Kevin Durant"), (2018, 1, "James Harden"),
    (2018, 1, "LeBron James"), (2018, 1, "Damian Lillard"),
    (2018, 2, "LaMarcus Aldridge"), (2018, 2, "Giannis Antetokounmpo"), (2018, 2, "DeMar DeRozan"),
    (2018, 2, "Joel Embiid"), (2018, 2, "Russell Westbrook"),
    (2018, 3, "Jimmy Butler"), (2018, 3, "Stephen Curry"), (2018, 3, "Paul George"),
    (2018, 3, "Victor Oladipo"), (2018, 3, "Karl-Anthony Towns"),
    # 2018-19
    (2019, 1, "Nikola Jokić"), (2019, 1, "Giannis Antetokounmpo"), (2019, 1, "James Harden"),
    (2019, 1, "Stephen Curry"), (2019, 1, "Paul George"),
    (2019, 2, "Joel Embiid"), (2019, 2, "Kevin Durant"), (2019, 2, "Damian Lillard"),
    (2019, 2, "Kawhi Leonard"), (2019, 2, "Kyrie Irving"),
    (2019, 3, "Russell Westbrook"), (2019, 3, "Blake Griffin"), (2019, 3, "LeBron James"),
    (2019, 3, "Rudy Gobert"), (2019, 3, "Kemba Walker"),
    # 2019-20
    (2020, 1, "Giannis Antetokounmpo"), (2020, 1, "LeBron James"), (2020, 1, "James Harden"),
    (2020, 1, "Anthony Davis"), (2020, 1, "Luka Dončić"),
    (2020, 2, "Kawhi Leonard"), (2020, 2, "Nikola Jokić"), (2020, 2, "Damian Lillard"),
    (2020, 2, "Chris Paul"), (2020, 2, "Pascal Siakam"),
    (2020, 3, "Jayson Tatum"), (2020, 3, "Jimmy Butler"), (2020, 3, "Rudy Gobert"),
    (2020, 3, "Ben Simmons"), (2020, 3, "Russell Westbrook"),
    # 2020-21
    (2021, 1, "Giannis Antetokounmpo"), (2021, 1, "Stephen Curry"), (2021, 1, "Luka Dončić"),
    (2021, 1, "Nikola Jokić"), (2021, 1, "Kawhi Leonard"),
    (2021, 2, "Damian Lillard"), (2021, 2, "Joel Embiid"), (2021, 2, "Chris Paul"),
    (2021, 2, "Julius Randle"), (2021, 2, "LeBron James"),
    (2021, 3, "Bradley Beal"), (2021, 3, "Jimmy Butler"), (2021, 3, "Paul George"),
    (2021, 3, "Rudy Gobert"), (2021, 3, "Kyrie Irving"),
    # 2021-22
    (2022, 1, "Devin Booker"), (2022, 1, "Luka Dončić"), (2022, 1, "Jayson Tatum"),
    (2022, 1, "Giannis Antetokounmpo"), (2022, 1, "Nikola Jokić"),
    (2022, 2, "Stephen Curry"), (2022, 2, "DeMar DeRozan"), (2022, 2, "Kevin Durant"),
    (2022, 2, "Joel Embiid"), (2022, 2, "Ja Morant"),
    (2022, 3, "LeBron James"), (2022, 3, "Chris Paul"), (2022, 3, "Pascal Siakam"),
    (2022, 3, "Karl-Anthony Towns"), (2022, 3, "Trae Young"),
    # 2022-23
    (2023, 1, "Giannis Antetokounmpo"), (2023, 1, "Luka Dončić"), (2023, 1, "Joel Embiid"),
    (2023, 1, "Shai Gilgeous-Alexander"), (2023, 1, "Jayson Tatum"),
    (2023, 2, "Jaylen Brown"), (2023, 2, "Jimmy Butler III"), (2023, 2, "Stephen Curry"),
    (2023, 2, "Nikola Jokić"), (2023, 2, "Donovan Mitchell"),
    (2023, 3, "De'Aaron Fox"), (2023, 3, "LeBron James"), (2023, 3, "Damian Lillard"),
    (2023, 3, "Julius Randle"), (2023, 3, "Domantas Sabonis"),
    # 2023-24
    (2024, 1, "Giannis Antetokounmpo"), (2024, 1, "Luka Dončić"), (2024, 1, "Shai Gilgeous-Alexander"),
    (2024, 1, "Nikola Jokić"), (2024, 1, "Jayson Tatum"),
    (2024, 2, "Jalen Brunson"), (2024, 2, "Anthony Davis"), (2024, 2, "Kevin Durant"),
    (2024, 2, "Anthony Edwards"), (2024, 2, "Kawhi Leonard"),
    (2024, 3, "Stephen Curry"), (2024, 3, "Tyrese Haliburton"), (2024, 3, "LeBron James"),
    (2024, 3, "Domantas Sabonis"), (2024, 3, "Devin Booker"),
    # 2024-25
    (2025, 1, "Giannis Antetokounmpo"), (2025, 1, "Shai Gilgeous-Alexander"), (2025, 1, "Nikola Jokić"),
    (2025, 1, "Jayson Tatum"), (2025, 1, "Donovan Mitchell"),
    (2025, 2, "Anthony Edwards"), (2025, 2, "LeBron James"), (2025, 2, "Stephen Curry"),
    (2025, 2, "Evan Mobley"), (2025, 2, "Jalen Brunson"),
    (2025, 3, "Cade Cunningham"), (2025, 3, "Karl-Anthony Towns"), (2025, 3, "Tyrese Haliburton"),
    (2025, 3, "Jalen Williams"), (2025, 3, "James Harden"),
    # 2025-26 (round 8 step 7): read 2026-10-06 from https://en.wikipedia.org/wiki/2025%E2%80%9326_NBA_season (Awards).
    (2026, 1, "Cade Cunningham"), (2026, 1, "Luka Dončić"), (2026, 1, "Shai Gilgeous-Alexander"),
    (2026, 1, "Nikola Jokić"), (2026, 1, "Victor Wembanyama"),
    (2026, 2, "Jaylen Brown"), (2026, 2, "Jalen Brunson"), (2026, 2, "Kevin Durant"),
    (2026, 2, "Kawhi Leonard"), (2026, 2, "Donovan Mitchell"),
    (2026, 3, "Tyrese Maxey"), (2026, 3, "Jamal Murray"), (2026, 3, "Jalen Johnson"),
    (2026, 3, "Chet Holmgren"), (2026, 3, "Jalen Duren"),
]


def load_base_stats():
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql_query(
        "SELECT player_id, player_name, season FROM player_season_stats;", conn
    )
    conn.close()
    return df


def match_player(name, season_int, base_df):
    mask = (base_df["player_name"] == name) & (base_df["season"] == season_int)
    match = base_df[mask]
    if not match.empty:
        return match.iloc[0]

    last = name.split()[-1].rstrip(".")
    mask = (
        base_df["player_name"].str.contains(last, case=False, na=False, regex=False)
        & (base_df["season"] == season_int)
    )
    match = base_df[mask]
    if len(match) == 1:
        return match.iloc[0]
    return None


def build_dataset(base_df):
    rows = []
    misses = []
    for season_int, tier, name in ALL_NBA_TEAMS:
        match = match_player(name, season_int, base_df)
        if match is None:
            misses.append((season_int, tier, name))
            continue
        rows.append({
            "player_id": int(match["player_id"]),
            "player_name": match["player_name"],
            "season": season_int,
            "team_tier": tier,
        })

    print(f"Matched {len(rows)}/{len(ALL_NBA_TEAMS)} All-NBA selections.")
    if misses:
        print(f"\n  ✗ {len(misses)} NOT FOUND (fix spelling or check DB coverage):")
        for season_int, tier, name in misses:
            print(f"    {season_int} Team {tier}: {name}")

    return pd.DataFrame(rows), misses


def save(df):
    os.makedirs(DATA_DIR, exist_ok=True)
    csv_path = os.path.join(DATA_DIR, "all_nba_seasons.csv")
    df.to_csv(csv_path, index=False)
    print(f"\nSaved {csv_path}")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS all_nba_seasons (
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            season INTEGER NOT NULL,
            team_tier SMALLINT NOT NULL,
            PRIMARY KEY (player_id, season)
        );
    """)
    cur.execute("DELETE FROM all_nba_seasons;")
    from psycopg2.extras import execute_values
    execute_values(
        cur,
        "INSERT INTO all_nba_seasons (player_id, player_name, season, team_tier) VALUES %s;",
        [(int(r.player_id), r.player_name, int(r.season), int(r.team_tier)) for r in df.itertuples()],
    )
    conn.commit()
    conn.close()
    print(f"Saved {len(df)} rows to all_nba_seasons table.")


if __name__ == "__main__":
    print(f"Building All-NBA label dataset ({len(ALL_NBA_TEAMS)} selections, 17 seasons)...")
    base_df = load_base_stats()
    df, misses = build_dataset(base_df)
    save(df)
    if misses:
        print(f"\n⚠️  {len(misses)} selections unmatched — all_nba_seasons is INCOMPLETE until fixed.")
