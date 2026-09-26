"""
build_contract_value.py
=========================
Contract Surplus Value ("Moneyball Engine"): real on-court value in
dollars vs. real salary. Seasons: 2005-06 through 2019-20 and 2024-25 —
see load_salaries.py for why the others are excluded.

  WAR (raw)   = VORP x 2.7 (Basketball-Reference's documented VORP->wins
               conversion). VORP is this project's own BPM 2.0
               reproduction (build_bpm_vorp.py), not Basketball-Reference's.
  WAR (calibrated) = raw WAR x k, with k set per season so the league's
               real sum of positive WAR equals the real league wins above
               replacement. A disclosed departure from the plan: the first
               real run found this VORP reproduction runs hot — the sum of
               positive raw WAR was 1.3-2.0x the real wins above
               replacement — which priced 2024-25 Shai Gilgeous-Alexander
               at a $279M "fair value". Cost/win is defined on real wins, so
               WAR has to be on the same real-win scale for fair value to
               mean anything. Raw VORP/WAR and k are stored and shown.
  Cost/win   = (on-court payroll - n x league minimum)
               / (real league wins - real replacement wins)
               where on-court payroll = the real salaries of the n players
               who actually played that season (salary row matched to that
               season's player_season_stats), league minimum = that
               season's real 0-years minimum (league_minimum_salary, with
               source), real wins/games from Team Summaries, and
               replacement wins = 0.200 x real games per team (a .200
               team). Computed per season from real data; never assumed.
  Fair value = max(calibrated WAR, 0) x cost/win + league minimum
  Surplus    = fair value - real salary

Why "on-court" payroll rather than every salary row: the 2000-2020 salary
file carries stale rows forward after contracts end (e.g. Cuttino Mobley
listed at $9.5M a season through 2012-13, years after he'd retired), which
inflates whole-league payroll by an unknowable amount. Restricting to
players who really played drops those rows and also leaves out real money
paid to players who didn't play (injury, buyouts) — so cost/win here is
the real price of production above replacement, not total spending.
Disclosed as such.

Validation stored per season (contract_value_seasons):
  * minutes coverage — share of the season's real minutes played by
    players with a matched salary. Seasons below 90% are flagged and not
    shown (too much production can't be priced).
  * WAR calibration — real sum of positive raw WAR vs. real league wins
    above replacement (the inverse of k). Stored and shown so the dollar
    scale's dependence on the VORP reproduction is visible, not hidden.

Contract-type hint, only where real data shows it: "rookie-scale years" =
a real first-round pick (Team Summaries' companion Draft Pick History)
in seasons 1-4 after his draft. No minimum-contract flag: identifying a
minimum deal needs the full years-of-service scale for every season,
which isn't all sourced here, so none is inferred.

Usage:
    cd scripts && python3 build_contract_value.py
"""

import os
import re
import unicodedata

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

WAR_PER_VORP = 2.7
REPLACEMENT_WIN_PCT = 0.200
MIN_MINUTES_COVERAGE = 0.90
KAGGLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nba_data", "kaggle_1947_present")


def normalize(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[.'`’-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT season, min_salary_0yr, source FROM league_minimum_salary;")
    mins = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    seasons = sorted(mins)

    cur.execute("""SELECT season, player_id, player_name, team_abbreviation, gp, min, vorp
                   FROM player_season_stats WHERE season = ANY(%s);""", (seasons,))
    pss = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "player_name", "team", "gp", "mpg", "vorp"])
    pss["minutes"] = pss["gp"].astype(float) * pss["mpg"].astype(float)
    pss["vorp"] = pss["vorp"].astype(float)

    cur.execute("""SELECT season, player_id, salary FROM player_salaries
                   WHERE player_id IS NOT NULL AND season = ANY(%s);""", (seasons,))
    sal = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "salary"])
    sal["salary"] = sal["salary"].astype(float)
    df = pss.merge(sal, on=["season", "player_id"], how="left")

    teams = pd.read_csv(os.path.join(KAGGLE_DIR, "Team Summaries.csv"))
    teams = teams[(teams["lg"] == "NBA") & teams["abbreviation"].notna() & teams["season"].isin(seasons)]
    wins = teams.groupby("season").apply(
        lambda g: pd.Series({"wins": g["w"].sum(), "games": (g["w"] + g["l"]).sum(), "n_teams": len(g)}),
        include_groups=False)
    wins["replacement_wins"] = REPLACEMENT_WIN_PCT * wins["games"]
    wins["wins_above_replacement"] = wins["wins"] - wins["replacement_wins"]

    draft = pd.read_csv(os.path.join(KAGGLE_DIR, "Draft Pick History.csv"))
    draft = draft[(draft["lg"] == "NBA") & (draft["round"] == 1)]
    first_round = {}
    for r in draft.itertuples():
        first_round.setdefault(normalize(r.player), []).append((int(r.season), int(r.overall_pick)))

    df["war"] = df["vorp"] * WAR_PER_VORP
    rows, season_rows = [], []
    for season in seasons:
        g = df[df["season"] == season]
        if g.empty or season not in wins.index:
            continue
        paid = g[g["salary"].notna()]
        min_sal = mins[season][0]
        payroll = paid["salary"].sum()
        repl_cost = len(paid) * min_sal
        w = wins.loc[season]
        cost_per_win = (payroll - repl_cost) / w["wins_above_replacement"]
        coverage = paid["minutes"].sum() / g["minutes"].sum()
        war_pos_all = g["war"].clip(lower=0).sum()
        k = float(w["wins_above_replacement"] / war_pos_all)
        included = coverage >= MIN_MINUTES_COVERAGE
        season_rows.append((
            int(season), float(payroll), int(len(paid)), int(len(g)), int(min_sal), mins[season][1],
            float(repl_cost), float(w["wins"]), float(w["games"]), float(w["replacement_wins"]),
            float(w["wins_above_replacement"]), float(cost_per_win), float(coverage),
            float(war_pos_all), float(war_pos_all / w["wins_above_replacement"]), k, bool(included),
        ))
        for r in paid.itertuples():
            war_cal = r.war * k if pd.notna(r.war) else None
            fair = float(max(war_cal, 0.0) * cost_per_win + min_sal) if war_cal is not None else None
            picks = [p for p in first_round.get(normalize(r.player_name), []) if 1 <= season - p[0] <= 4]
            rows.append((
                int(season), int(r.player_id), r.player_name, r.team, float(r.minutes), int(r.salary),
                None if pd.isna(r.vorp) else float(r.vorp), None if pd.isna(r.war) else float(r.war),
                None if war_cal is None else float(war_cal),
                fair, None if fair is None else fair - r.salary,
                bool(picks), picks[0][1] if picks else None, picks[0][0] if picks else None,
            ))

    cur.execute("""
        DROP TABLE IF EXISTS contract_value;
        CREATE TABLE contract_value (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            minutes DOUBLE PRECISION,
            salary BIGINT NOT NULL,
            vorp DOUBLE PRECISION,
            war_raw DOUBLE PRECISION,
            war DOUBLE PRECISION,
            fair_value DOUBLE PRECISION,
            surplus DOUBLE PRECISION,
            rookie_scale_years BOOLEAN NOT NULL,
            draft_pick INTEGER,
            draft_year INTEGER,
            PRIMARY KEY (season, player_id)
        );
        DROP TABLE IF EXISTS contract_value_seasons;
        CREATE TABLE contract_value_seasons (
            season INTEGER PRIMARY KEY,
            oncourt_payroll DOUBLE PRECISION,
            n_paid_players INTEGER,
            n_players INTEGER,
            min_salary_0yr BIGINT,
            min_salary_source TEXT,
            replacement_cost DOUBLE PRECISION,
            wins DOUBLE PRECISION,
            games DOUBLE PRECISION,
            replacement_wins DOUBLE PRECISION,
            wins_above_replacement DOUBLE PRECISION,
            cost_per_win DOUBLE PRECISION,
            minutes_coverage DOUBLE PRECISION,
            war_positive_sum DOUBLE PRECISION,
            war_to_real_wins_ratio DOUBLE PRECISION,
            war_scale_k DOUBLE PRECISION,
            included BOOLEAN NOT NULL
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO contract_value VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO contract_value_seasons VALUES %s", season_rows)
    conn.commit()

    print("Per-season real cost per win:")
    for s in season_rows:
        print(f"  {s[0]}: payroll ${s[1] / 1e9:.3f}B over {s[2]}/{s[3]} players, min ${s[4]:,}, wins {s[7]:.0f}/{s[8]:.0f} "
              f"(WAR-above-repl {s[10]:.0f}), cost/win ${s[11] / 1e6:.2f}M, minutes coverage {s[12]:.3f}, "
              f"sum+rawWAR/real {s[14]:.2f} (k {s[15]:.3f}), included={s[16]}")
    cv = pd.DataFrame(rows, columns=["season", "player_id", "name", "team", "minutes", "salary", "vorp", "war_raw",
                                     "war", "fair", "surplus", "rookie", "pick", "draft_year"])
    for season in [2016, 2020, 2025]:
        q = cv[(cv["season"] == season) & (cv["minutes"] >= 500)]
        print(f"\n{season} top bargains:")
        print(q.nlargest(6, "surplus")[["name", "salary", "war", "fair", "surplus", "rookie"]].round(1).to_string())
        print(f"{season} top liabilities (salary >= $25M):")
        print(q[q["salary"] >= 25e6].nsmallest(5, "surplus")[["name", "salary", "war", "fair", "surplus"]].round(1).to_string())
    conn.close()


if __name__ == "__main__":
    main()
