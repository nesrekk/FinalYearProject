"""
build_team_seasons.py
======================
One row per team-season, 1946-47 (BAA) to 2025-26, from the Basketball-
Reference team files in the local Kaggle export (nba_data/kaggle_1947_present,
gitignored; the same export player_season_stats' pre-2010 rows and
league_season_averages come from): record, Pythagorean wins, margin, SRS and
SOS, offensive/defensive/net rating, pace, the four factors on both ends,
average age, arena and attendance, whether the team made the playoffs, plus
minutes and points per game from the per-game file.

Each row carries two codes: Basketball-Reference's own (PHO, BRK, CHO in every
season) and the one the rest of this database uses in that season
(api/teams_lib.app_abbr: Basketball-Reference's before 2009-10, the NBA's
after), and the franchise it belongs to (teams_lib.FRANCHISES). The league
average row of each season is stored with is_league_avg = true.

Checks printed: every (season, code) used by player_season_stats and, from
2009-10 on, by team_game_fatigue has a row here and vice versa; W-L here
equals team_game_fatigue's for every team-season it covers.

Read by api/routers/team_profile.py (the team page's summary block, franchise
history and season list). Rerun after refreshing the Kaggle export.

Table written (dropped and rebuilt): team_seasons

Usage:
    cd scripts && python3 build_team_seasons.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from teams_lib import app_abbr, franchise_of  # noqa: E402

KAGGLE = Path(__file__).resolve().parent.parent / "nba_data" / "kaggle_1947_present"

SUMMARY_COLS = ["age", "w", "l", "pw", "pl", "mov", "sos", "srs", "o_rtg", "d_rtg", "n_rtg", "pace", "f_tr",
                "x3p_ar", "ts_percent", "e_fg_percent", "tov_percent", "orb_percent", "ft_fga", "opp_e_fg_percent",
                "opp_tov_percent", "drb_percent", "opp_ft_fga", "arena", "attend", "attend_g"]


def main():
    summ = pd.read_csv(KAGGLE / "Team Summaries.csv")
    per_game = pd.read_csv(KAGGLE / "Team Stats Per Game.csv")
    opp = pd.read_csv(KAGGLE / "Opponent Stats Per Game.csv")
    summ = summ[summ.lg.isin(["NBA", "BAA"])].copy()
    per_game = per_game[per_game.lg.isin(["NBA", "BAA"])]
    opp = opp[opp.lg.isin(["NBA", "BAA"])]
    keys = ["season", "team"]
    df = summ.merge(per_game[keys + ["g", "mp_per_game", "pts_per_game"]], on=keys, how="left")
    df = df.merge(opp[keys + ["opp_pts_per_game"]], on=keys, how="left")

    df["is_league_avg"] = df.team == "League Average"
    df["bref_abbreviation"] = np.where(df.is_league_avg, None, df.abbreviation)
    df["abbreviation"] = [None if lg else app_abbr(a, s) for a, s, lg in zip(df.abbreviation, df.season, df.is_league_avg)]
    df["franchise"] = [None if lg else franchise_of(a) for a, lg in zip(df.bref_abbreviation, df.is_league_avg)]
    df["playoffs"] = df.playoffs.astype(str).str.upper().eq("TRUE")
    dup = df[~df.is_league_avg].duplicated(["season", "abbreviation"], keep=False)
    if dup.any():
        raise SystemExit(f"duplicate (season, abbreviation):\n{df[~df.is_league_avg][dup]}")

    conn = psycopg2.connect(**DB_CONFIG)
    pss = pd.read_sql("""SELECT DISTINCT season, team_abbreviation AS abbreviation FROM player_season_stats
                         WHERE team_abbreviation !~ '^([0-9]TM|TOT)$'""", conn)
    stints = pd.read_sql("SELECT DISTINCT season, team AS abbreviation FROM player_team_stints", conn)
    fat = pd.read_sql("""SELECT season, team_abbreviation AS abbreviation, COUNT(*) FILTER (WHERE win) AS w,
                                COUNT(*) FILTER (WHERE NOT win) AS l
                         FROM team_game_fatigue GROUP BY 1, 2""", conn)
    teams = df[~df.is_league_avg][["season", "abbreviation", "w", "l", "team"]]
    used = pd.concat([pss, stints]).drop_duplicates()
    m = used.merge(teams, on=["season", "abbreviation"], how="left")
    missing = m[m.team.isna()]
    print(f"codes used by player tables without a team_seasons row: {len(missing)}")
    if len(missing):
        print(missing.to_string(index=False))
    in_range = teams[teams.season >= used.season.min()]
    unused = in_range.merge(used, on=["season", "abbreviation"], how="left", indicator=True)
    print(f"team_seasons rows no player table uses (season >= {used.season.min()}): "
          f"{(unused._merge == 'left_only').sum()}")
    fm = fat.merge(teams, on=["season", "abbreviation"], how="outer", suffixes=("_fat", ""), indicator=True)
    fm = fm[fm.season >= fat.season.min()]
    print(f"team_game_fatigue team-seasons: {len(fat)}, unmatched either way: {(fm._merge != 'both').sum()}, "
          f"W-L differs: {((fm._merge == 'both') & ((fm.w_fat != fm.w) | (fm.l_fat != fm.l))).sum()}")

    cols = ["season", "lg", "abbreviation", "bref_abbreviation", "franchise", "team", "is_league_avg", "playoffs",
            "g", "mp_per_game", "pts_per_game", "opp_pts_per_game"] + SUMMARY_COLS
    out = df[cols].rename(columns={"team": "team_name"})
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS team_seasons")
    num = [c for c in cols if c not in ("season", "lg", "abbreviation", "bref_abbreviation", "franchise", "team",
                                          "is_league_avg", "playoffs", "arena")]
    cur.execute(f"""CREATE TABLE team_seasons (
        season INTEGER NOT NULL, lg TEXT, abbreviation TEXT, bref_abbreviation TEXT, franchise TEXT,
        team_name TEXT, is_league_avg BOOLEAN, playoffs BOOLEAN, arena TEXT,
        {', '.join(f'{c} DOUBLE PRECISION' for c in num)})""")
    names = ["season", "lg", "abbreviation", "bref_abbreviation", "franchise", "team_name", "is_league_avg",
             "playoffs", "arena"] + num
    rows = []
    for r in out.rename(columns={}).to_dict("records"):
        r["team_name"] = r.pop("team_name")
        rows.append(tuple(None if (isinstance(r[c], float) and np.isnan(r[c])) else
                          (r[c].item() if hasattr(r[c], "item") else r[c]) for c in names))
    execute_values(cur, f"INSERT INTO team_seasons ({', '.join(names)}) VALUES %s", rows)
    cur.execute("CREATE INDEX ON team_seasons (season, abbreviation)")
    cur.execute("CREATE INDEX ON team_seasons (franchise)")
    conn.commit()
    t = out[~out.is_league_avg]
    print(f"team_seasons: {len(out):,} rows ({len(t):,} team-seasons, {t.season.min()}-{t.season.max()}, "
          f"{t.franchise.nunique()} franchises incl. defunct)")
    for s, a in ((2016, "GSW"), (1996, "CHI"), (1973, "PHI"), (2008, "SEA")):
        r = t[(t.season == s) & (t.abbreviation == a)].iloc[0]
        print(f"  {s} {a} {r.team_name}: {int(r.w)}-{int(r.l)}, SRS {r.srs:+.2f}, ORtg {r.o_rtg}, "
              f"DRtg {r.d_rtg}, pace {r.pace}, franchise {r.franchise}")


if __name__ == "__main__":
    main()
