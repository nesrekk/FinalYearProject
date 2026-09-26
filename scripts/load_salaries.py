"""
load_salaries.py
==================
Loads real per-player salaries for the Contract Value feature
(build_contract_value.py) from two third-party CSVs the owner downloaded
(kept in nba_data/salaries/, gitignored — not redistributed):

  * nba_salaries_2000_2020.csv — "nba-salaries.csv", seasons 2000-2020.
  * nba_player_stats_salaries_2010_2025.csv — Kaggle "NBA Player Stats and
    Salaries 2010-2025" (ratin21), whose uploader credits HoopsHype and
    Basketball-Reference. No license is stated on either.

Season scope, decided by checking the files against real contracts before
writing this (see README): both files use the season's END year. The
2000-2020 file agrees with the Kaggle file on 99.9% of 4,828 overlapping
2010-2020 salaries and matches real contracts spot-checked. BUT:
  * 2000-2005 in that file are incomplete payrolls (120-416 players a
    season) — unusable for a league cost-per-win, so excluded.
  * The Kaggle file's 2020-21 through 2023-24 salaries are INFLATION-
    ADJUSTED (every player in a season scaled by the same factor: x1.219,
    x1.156, x1.060, x1.030 — e.g. Stephen Curry 2021-22 listed $52,938,707
    vs. his real $45,780,966), so they're excluded rather than "un-adjusted".
    Its 2024-25 values are nominal and match real contracts (Jokic,
    Giannis, LeBron, SGA, Brunson), so 2024-25 is used from it.
So: 2005-06 through 2019-20 from the 2000-2020 file, 2024-25 from Kaggle.
Individual wrong rows exist upstream (e.g. both files list Stephen Curry
2017-18 at his 2018-19 salary) and can't all be detected; disclosed.

Player matching (salary name -> player_season_stats.player_id, same
season): accent/punctuation-normalized exact match first; then a strict
fuzzy fallback (rapidfuzz WRatio >= 94 AND at least 5 points clear of the
next-best name, so twins like Marcus/Markieff Morris can't cross-match).
A name listed twice in one season, or two salary rows landing on one
player id, is kept in league payroll but not valued per player. Match
rates and unmatched salary totals are stored per season.

Also loads each season's real league minimum salary (0 years of service)
into league_minimum_salary with its source, for the cost-per-win formula.

Usage:
    cd scripts && python3 load_salaries.py
"""

import os
import re
import unicodedata

import pandas as pd
import psycopg2
import psycopg2.extras
from rapidfuzz import fuzz, process

from db_config import DB_CONFIG

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nba_data", "salaries")
FILE_2000_2020 = os.path.join(DATA_DIR, "nba_salaries_2000_2020.csv")
FILE_2010_2025 = os.path.join(DATA_DIR, "nba_player_stats_salaries_2010_2025.csv")

SEASONS_FROM_2000_2020 = range(2006, 2021)
SEASONS_FROM_2010_2025 = [2025]

# Real, documented name changes where the salary file uses the old name and
# player_season_stats the new one (checked: no other unmatched salary rows
# in scope correspond to a renamed player in the stats table).
NAME_ALIASES = {
    "enes kanter": "enes freedom",   # legal name change, 2021
    "nene hilario": "nene",          # listed by first name only in the stats table
}

FUZZY_FLOOR = 94
FUZZY_MARGIN = 5

CBA_FAQ_2005 = "Larry Coon's NBA Salary Cap FAQ (2005 CBA), web.archive.org snapshot of members.cox.net/lmcoon/salarycap.htm, 2009-08-29"
CBA_FAQ_2011 = "Larry Coon's NBA Salary Cap FAQ (2011 CBA), cbafaq.com/salarycap11.htm"
CBA_FAQ_2017 = "Larry Coon's Minimum Salary Scales under the 2017 CBA, cbafaq.com/minimums.htm"
LEAGUE_MINIMUM_0YR = {
    2006: (398_762, CBA_FAQ_2005), 2007: (412_718, CBA_FAQ_2005), 2008: (427_163, CBA_FAQ_2005),
    2009: (442_114, CBA_FAQ_2005), 2010: (457_588, CBA_FAQ_2005), 2011: (473_604, CBA_FAQ_2005),
    2012: (473_604, CBA_FAQ_2011), 2013: (473_604, CBA_FAQ_2011), 2014: (490_180, CBA_FAQ_2011),
    2015: (507_336, CBA_FAQ_2011), 2016: (525_093, CBA_FAQ_2011), 2017: (543_471, CBA_FAQ_2011),
    2018: (815_615, CBA_FAQ_2017),
    2019: (838_464, "Hoops Rumors, 'NBA Minimum Salaries For 2018/19' (2018-06)"),
    2020: (898_310, "Hoops Rumors, 'NBA Minimum Salaries For 2019/20' (2019-06)"),
    2025: (1_157_153, "Hoops Rumors, 'NBA Minimum Salaries For 2024/25' (2024-06)"),
}


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[.'`’-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_files():
    a = pd.read_csv(FILE_2000_2020)
    a.columns = [c.strip() for c in a.columns]
    a = a.rename(columns={"name": "player_name", "season": "season", "salary": "salary", "team": "team"})
    a["player_name"] = a["player_name"].str.strip()
    a = a[a["season"].isin(SEASONS_FROM_2000_2020)][["season", "player_name", "team", "salary"]]
    a["source_file"] = "nba_salaries_2000_2020.csv"

    b = pd.read_csv(FILE_2010_2025, encoding="utf-8-sig")
    b = b.rename(columns={"Player": "player_name", "Year": "season", "Salary": "salary", "Team": "team"})
    b = b[b["season"].isin(SEASONS_FROM_2010_2025)][["season", "player_name", "team", "salary"]]
    b["source_file"] = "nba_player_stats_salaries_2010_2025.csv"
    df = pd.concat([a, b], ignore_index=True)
    df["salary"] = df["salary"].astype(float)
    return df


def main():
    df = load_files()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT season, player_id, player_name FROM player_season_stats WHERE season = ANY(%s);",
                ([int(s) for s in df["season"].unique()],))
    pss = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "pss_name"])
    pss["norm"] = pss["pss_name"].map(normalize)

    df["norm"] = df["player_name"].map(normalize).replace(NAME_ALIASES)
    dup_names = df.duplicated(["season", "norm"], keep=False)
    df["player_id"] = pd.NA
    df["match_method"] = "unmatched"
    df.loc[dup_names, "match_method"] = "ambiguous_name"

    for season, idx in df[~dup_names].groupby("season").groups.items():
        pool = pss[pss["season"] == season]
        exact = dict(zip(pool["norm"], pool["player_id"]))
        choices = list(exact.keys())
        for i in idx:
            n = df.at[i, "norm"]
            if n in exact:
                df.at[i, "player_id"] = int(exact[n])
                df.at[i, "match_method"] = "exact"
                continue
            best = process.extract(n, choices, scorer=fuzz.WRatio, limit=2)
            if best and best[0][1] >= FUZZY_FLOOR and (len(best) < 2 or best[0][1] - best[1][1] >= FUZZY_MARGIN):
                df.at[i, "player_id"] = int(exact[best[0][0]])
                df.at[i, "match_method"] = "fuzzy"

    # Two salary rows on one player id in a season -> ambiguous, not valued.
    matched = df["player_id"].notna()
    clash = matched & df.duplicated(["season", "player_id"], keep=False)
    df.loc[clash, "match_method"] = "ambiguous_id"
    df.loc[clash, "player_id"] = pd.NA

    cur.execute("""
        DROP TABLE IF EXISTS player_salaries;
        CREATE TABLE player_salaries (
            id SERIAL PRIMARY KEY,
            season INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            team TEXT,
            salary BIGINT NOT NULL,
            player_id BIGINT,
            match_method TEXT NOT NULL,
            source_file TEXT NOT NULL
        );
        CREATE INDEX ON player_salaries (season, player_id);
        DROP TABLE IF EXISTS league_minimum_salary;
        CREATE TABLE league_minimum_salary (
            season INTEGER PRIMARY KEY,
            min_salary_0yr BIGINT NOT NULL,
            source TEXT NOT NULL
        );
    """)
    psycopg2.extras.execute_values(cur, """INSERT INTO player_salaries
        (season, player_name, team, salary, player_id, match_method, source_file) VALUES %s""", [
        (int(r.season), r.player_name, None if pd.isna(r.team) else str(r.team), int(r.salary),
         None if pd.isna(r.player_id) else int(r.player_id), r.match_method, r.source_file)
        for r in df.itertuples()
    ])
    psycopg2.extras.execute_values(cur, "INSERT INTO league_minimum_salary VALUES %s",
                                   [(s, v, src) for s, (v, src) in LEAGUE_MINIMUM_0YR.items()])
    conn.commit()

    print("Per-season real match rates:")
    for season, g in df.groupby("season"):
        counts = g["match_method"].value_counts().to_dict()
        pay = g["salary"].sum()
        unmatched_pay = g.loc[g["player_id"].isna(), "salary"].sum()
        print(f"  {season}: {len(g)} rows, ${pay / 1e9:.3f}B payroll; {counts}; "
              f"unvalued salary ${unmatched_pay / 1e6:.1f}M ({unmatched_pay / pay:.1%})")
    fz = df[df["match_method"] == "fuzzy"]
    names = dict(zip(zip(pss["season"], pss["player_id"]), pss["pss_name"]))
    print(f"\n{len(fz)} fuzzy matches (sample, salary name -> stats name):")
    for r in fz.head(25).itertuples():
        print(f"  {r.season}: {r.player_name!r} -> {names[(r.season, r.player_id)]!r}")
    conn.close()


if __name__ == "__main__":
    main()
