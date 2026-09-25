"""
build_dad_index.py
====================
DAD Index (OBPM-weighted) — Defensive Assignment Difficulty. Measures how
hard each real defender's real assignments were, so a wing who spends his
nights on stars isn't judged by the same raw defended-FG% as a teammate
hidden on a non-shooter.

    DAD = sum over real offensive players i of
          (defender's real partial possessions vs. i / defender's total) x OBPM_i

Data (all real, nothing invented):
  * player_matchups — real NBA tracking partial possessions per
    defender x offensive player (fetch_matchups.py). Pairs under 5 real
    partial possessions were already dropped at fetch time, so shares are
    over >= 5-possession pairs only (~all of a defender's real volume).
  * OBPM_i — the obpm column on player_season_stats (build_bpm_vorp.py),
    this project's own disclosed reproduction of Box Plus/Minus 2.0, not
    Basketball-Reference's published numbers. EPM is proprietary and is
    not used or imitated.
  * Replacement level: an offensive player with under 500 real minutes
    that season — or no stored season line at all — counts as OBPM -2.0
    (the conventional BPM replacement level), since a tiny-minutes BPM is
    noise. The real share of possessions this touches is stored.
  * defender_dfg — real defended FG% vs. the shooters' real normal FG%
    (fetch_defend_dashboard.py), joined for the DFG% differential.

Qualified defenders: >= 1,000 real partial possessions that season. DAD is
z-scored within each season's qualified pool (so seasons are comparable).
A second, position-relative z-score (dad_pos_z) is z-scored within the
same season AND the defender's listed position group (G/F/C, first letter
of the NBA's own PLAYER_POSITION): qualified centers average ~+0.95 on the
plain z-score, partly because they guard other starting bigs and partly
because this project's BPM reproduction runs hot for productive bigs
(e.g. 2024-25 OBPM: Jarrett Allen 8.4, Evan Mobley 8.5), so the
position-relative view is the fairer comparison between a wing and a big.

Validation stored in dad_validation, per season:
  * year-over-year stability — Pearson r of DAD for the same real
    defenders qualified in consecutive seasons (does it measure something
    persistent about a defender's role, or noise?)
  * r between DAD z and the real DFG% differential (descriptive)
  * real share of partial possessions assigned replacement level

Usage:
    cd scripts && python3 build_dad_index.py
"""

import json

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

MIN_OFF_MINUTES = 500
REPLACEMENT_OBPM = -2.0
MIN_DEF_POSS = 1000


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""SELECT season, def_player_id, def_player_name, off_player_id, off_player_name, partial_poss
                   FROM player_matchups;""")
    m = pd.DataFrame(cur.fetchall(), columns=["season", "def_id", "def_name", "off_id", "off_name", "poss"])
    m["poss"] = m["poss"].astype(float)

    cur.execute("""SELECT season, player_id, player_name, team_abbreviation, obpm, min * gp AS total_min
                   FROM player_season_stats WHERE season >= 2018;""")
    pss = pd.DataFrame(cur.fetchall(), columns=["season", "off_id", "pss_name", "pss_team", "obpm", "total_min"])
    pss["obpm"] = pss["obpm"].astype(float)
    pss["total_min"] = pss["total_min"].astype(float)

    m = m.merge(pss[["season", "off_id", "obpm", "total_min"]], on=["season", "off_id"], how="left")
    m["replacement"] = m["obpm"].isna() | (m["total_min"] < MIN_OFF_MINUTES)
    m["off_obpm"] = np.where(m["replacement"], REPLACEMENT_OBPM, m["obpm"])

    tot = m.groupby(["season", "def_id"])["poss"].transform("sum")
    m["share"] = m["poss"] / tot
    m["contrib"] = m["share"] * m["off_obpm"]

    g = m.groupby(["season", "def_id"])
    dad = g.agg(
        def_name=("def_name", "last"),
        total_poss=("poss", "sum"),
        n_assignments=("off_id", "nunique"),
        dad=("contrib", "sum"),
        replacement_share=("share", lambda s: float(s[m.loc[s.index, "replacement"]].sum())),
    ).reset_index()
    dad["qualified"] = dad["total_poss"] >= MIN_DEF_POSS

    dad["dad_z"] = np.nan
    for season, idx in dad[dad["qualified"]].groupby("season").groups.items():
        vals = dad.loc[idx, "dad"]
        dad.loc[idx, "dad_z"] = (vals - vals.mean()) / vals.std(ddof=0)

    top3 = (
        m.sort_values("share", ascending=False)
        .groupby(["season", "def_id"])
        .head(3)
        .groupby(["season", "def_id"])
        .apply(lambda x: json.dumps([
            {"player_id": int(r.off_id), "player_name": r.off_name, "share": round(float(r.share), 4),
             "partial_poss": round(float(r.poss), 1), "obpm": round(float(r.off_obpm), 2),
             "replacement_level": bool(r.replacement)}
            for r in x.itertuples()
        ]), include_groups=False)
        .rename("top3")
    )
    dad = dad.join(top3, on=["season", "def_id"])

    cur.execute("""SELECT season, player_id, team_abbreviation, position, d_fga, d_fg_pct, normal_fg_pct, pct_plusminus
                   FROM defender_dfg;""")
    dfg = pd.DataFrame(cur.fetchall(), columns=["season", "def_id", "team", "position", "d_fga", "d_fg_pct",
                                                "normal_fg_pct", "dfg_diff"])
    dad = dad.merge(dfg, on=["season", "def_id"], how="left")

    dad["pos_group"] = dad["position"].str[0].where(dad["position"].notna() & (dad["position"] != ""))
    dad["dad_pos_z"] = np.nan
    pos_pool = dad[dad["qualified"] & dad["pos_group"].notna()]
    for _, idx in pos_pool.groupby(["season", "pos_group"]).groups.items():
        vals = dad.loc[idx, "dad"]
        if len(vals) >= 10:
            dad.loc[idx, "dad_pos_z"] = (vals - vals.mean()) / vals.std(ddof=0)

    names = pss.rename(columns={"off_id": "def_id"})[["season", "def_id", "pss_name", "pss_team"]]
    dad = dad.merge(names, on=["season", "def_id"], how="left")
    dad["player_name"] = dad["pss_name"].fillna(dad["def_name"])
    dad["team"] = dad["team"].fillna(dad["pss_team"])

    # ---- validation ----
    validation = []
    q = dad[dad["qualified"]]
    for season in sorted(dad["season"].unique()):
        cur_s = q[q["season"] == season]
        prev = q[q["season"] == season - 1][["def_id", "dad"]].rename(columns={"dad": "dad_prev"})
        pair = cur_s.merge(prev, on="def_id")
        yoy_r = float(np.corrcoef(pair["dad"], pair["dad_prev"])[0, 1]) if len(pair) > 2 else None
        both = cur_s.dropna(subset=["dfg_diff"])
        dfg_r = float(np.corrcoef(both["dad_z"], both["dfg_diff"])[0, 1]) if len(both) > 2 else None
        ms = m[m["season"] == season]
        validation.append((
            int(season), int(len(cur_s)), int((dad["season"] == season).sum()),
            yoy_r, int(len(pair)) if yoy_r is not None else 0,
            dfg_r, int(len(both)),
            float(ms.loc[ms["replacement"], "poss"].sum() / ms["poss"].sum()),
            float(cur_s["dad"].mean()), float(cur_s["dad"].std(ddof=0)),
        ))

    cur.execute("""
        DROP TABLE IF EXISTS defender_dad;
        CREATE TABLE defender_dad (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            position TEXT,
            total_poss DOUBLE PRECISION NOT NULL,
            n_assignments INTEGER NOT NULL,
            dad DOUBLE PRECISION NOT NULL,
            dad_z DOUBLE PRECISION,
            pos_group TEXT,
            dad_pos_z DOUBLE PRECISION,
            qualified BOOLEAN NOT NULL,
            replacement_share DOUBLE PRECISION,
            top3 JSONB,
            d_fga INTEGER,
            d_fg_pct DOUBLE PRECISION,
            normal_fg_pct DOUBLE PRECISION,
            dfg_diff DOUBLE PRECISION,
            PRIMARY KEY (season, player_id)
        );
        DROP TABLE IF EXISTS dad_validation;
        CREATE TABLE dad_validation (
            season INTEGER PRIMARY KEY,
            n_qualified INTEGER,
            n_defenders INTEGER,
            yoy_r DOUBLE PRECISION,
            yoy_n INTEGER,
            dad_dfg_r DOUBLE PRECISION,
            dad_dfg_n INTEGER,
            replacement_poss_share DOUBLE PRECISION,
            dad_mean DOUBLE PRECISION,
            dad_sd DOUBLE PRECISION
        );
    """)

    def f(x):
        return None if pd.isna(x) else float(x)

    psycopg2.extras.execute_values(cur, "INSERT INTO defender_dad VALUES %s", [
        (int(r.season), int(r.def_id), r.player_name, None if pd.isna(r.team) else r.team,
         None if pd.isna(r.position) else r.position, float(r.total_poss), int(r.n_assignments),
         float(r.dad), f(r.dad_z), None if pd.isna(r.pos_group) else r.pos_group, f(r.dad_pos_z),
         bool(r.qualified), float(r.replacement_share), r.top3,
         None if pd.isna(r.d_fga) else int(r.d_fga), f(r.d_fg_pct), f(r.normal_fg_pct), f(r.dfg_diff))
        for r in dad.itertuples()
    ])
    psycopg2.extras.execute_values(cur, "INSERT INTO dad_validation VALUES %s", validation)
    conn.commit()

    print("Per-season real validation:")
    for v in validation:
        yoy = f"{v[3]:.3f} (n={v[4]})" if v[3] is not None else "—"
        print(f"  {v[0]}: qualified={v[1]}/{v[2]} YoY r={yoy} DAD-vs-DFG r={v[5]:.3f} (n={v[6]}) "
              f"replacement poss share={v[7]:.3f} mean DAD={v[8]:.2f} sd={v[9]:.2f}")

    latest = int(dad["season"].max()) - 1
    ql = dad[(dad["season"] == latest) & dad["qualified"]]
    cols = ["player_name", "team", "position", "total_poss", "dad", "dad_z", "dad_pos_z", "dfg_diff"]
    print(f"\n{latest} hardest assignments (qualified):")
    print(ql.nlargest(15, "dad")[cols].to_string())
    print(f"\n{latest} easiest assignments (qualified):")
    print(ql.nsmallest(10, "dad")[cols].to_string())
    conn.close()


if __name__ == "__main__":
    main()
