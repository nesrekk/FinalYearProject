"""
build_gravity_index.py
========================
Gravity Index & Spacing Lab. A DISCLOSED COMPOSITE PROXY for "shooting
gravity" built from real tracking splits — not player-tracking gravity
itself (that would need raw defender coordinates this project doesn't
have).

Per real player-season (2013-14+), three real components, each z-scored
within the season against the pool of players with 500+ real minutes:
  1. 3PA per 100 real possessions (player_season_stats: fg3a x gp / poss).
  2. Catch-and-shoot 3P% (player_shot_tracking), shrunk with a
     250-attempt prior: (3PM + 250 x prior%) / (3PA + 250).
  3. Share of the player's real 3PA taken with a defender within 6 ft
     (NBA closest-defender bands 0-2, 2-4 and 4-6 ft, vs. 6+ ft "wide
     open") — defenders staying attached to a shooter is the gravity
     signal. Shrunk with the same 250-attempt prior (not in the owner's
     plan; added so a player with a handful of 3PA can't post an extreme
     share).
  Shrinkage target — a deliberate, disclosed departure from the plan's
  "shrink to the league mean": the prior is the real pooled rate of
  players in the same 3PA-volume tier that season (quintiles of 3PA per
  100 possessions among 500+ minute players), not the league-wide rate.
  First real run with the league-wide prior rated non-shooters as
  AVERAGE on components 2 and 3 (no attempts -> league mean), which put
  Rudy Gobert (0 3PA in 2024-25) above a fifth of the league's actual
  shooters. The real data say low-volume shooters are neither average
  catch-and-shoot shooters nor tightly guarded (2024-25 tier priors,
  lowest vs. highest volume quintile: 31.8% vs. 39.2% C&S 3P%, 29.0% vs.
  58.7% of 3PA with a defender within 6 ft), so the volume-tier prior is
  the honest empirical-Bayes target. After the change Gobert sits at the
  7.5th percentile and Capela, Allen and Adams in the bottom 4%.
  Gravity = sum of the three z-scores.

Lineup spacing = sum of the five real players' Gravity for that season.

Validation (gravity_validation): does spacing relate to real offense?
Real 5-man lineups with 100+ real possessions (lineup_stats), pooled over
seasons: weighted least squares (weights = possessions) of real lineup
offensive rating on lineup spacing + the sum of the five players' OBPM
(this project's own BPM reproduction; players under 500 minutes use
replacement level -2.0, same as the DAD Index) + season fixed effects
(league offense changes by era). Standard errors are clustered by
team-season, because lineups from the same team share players and aren't
independent observations (stats_lib.wls_cluster, checked by simulation:
reported SE 0.0618 vs. an empirical 0.0615, 96% CI coverage). The
coefficient, its 95% CI, p, R², and R² without the spacing term are
stored. The Spacing Lab only shows a predicted ORtg change when this
coefficient is significant (p < 0.05).

Data-coverage check also stored: real tracked 3PA (sum of the four
defender bands) vs. real box-score 3PA, per season.

Usage:
    cd scripts && python3 build_gravity_index.py
"""

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from stats_lib import wls_cluster

POOL_MIN_MINUTES = 500
PRIOR_ATTEMPTS = 250
MIN_LINEUP_POSS = 100
REPLACEMENT_OBPM = -2.0


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""SELECT season, player_id, player_name, team_abbreviation, gp, min, poss, fg3a, obpm
                   FROM player_season_stats WHERE season >= 2014;""")
    pss = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "player_name", "team", "gp", "mpg",
                                                "poss", "fg3a_pg", "obpm"])
    for c in ["gp", "mpg", "poss", "fg3a_pg", "obpm"]:
        pss[c] = pss[c].astype(float)
    pss["minutes"] = pss["gp"] * pss["mpg"]
    pss["fg3a_total"] = pss["fg3a_pg"] * pss["gp"]

    cur.execute("SELECT * FROM player_shot_tracking;")
    cols = [c[0] for c in cur.description]
    trk = pd.DataFrame(cur.fetchall(), columns=cols).drop(columns=["player_name", "team_abbreviation"])
    df = pss.merge(trk, on=["season", "player_id"], how="left")
    track_cols = ["cs_fg3m", "cs_fg3a", "fg3a_def_0_2", "fg3a_def_2_4", "fg3a_def_4_6", "fg3a_def_6_plus"]
    df["tracked"] = df["cs_fg3a"].notna()
    df[track_cols] = df[track_cols].fillna(0).astype(float)
    df["def_fg3a"] = df[["fg3a_def_0_2", "fg3a_def_2_4", "fg3a_def_4_6", "fg3a_def_6_plus"]].sum(axis=1)
    df["contested_fg3a"] = df[["fg3a_def_0_2", "fg3a_def_2_4", "fg3a_def_4_6"]].sum(axis=1)

    coverage = []
    for season, g in df.groupby("season"):
        coverage.append((int(season), float(g["def_fg3a"].sum()), float(g["fg3a_total"].sum())))

    k = PRIOR_ATTEMPTS
    df["three_rate"] = np.where(df["poss"] > 0, df["fg3a_total"] / df["poss"] * 100, np.nan)
    df["in_pool"] = df["minutes"] >= POOL_MIN_MINUTES
    df["tier"] = np.nan
    for season, idx in df.groupby("season").groups.items():
        g = df.loc[idx]
        pool_rates = g.loc[g["in_pool"] & g["three_rate"].notna(), "three_rate"]
        edges = np.quantile(pool_rates, [0.2, 0.4, 0.6, 0.8])
        df.loc[idx, "tier"] = np.where(g["three_rate"].notna(), np.searchsorted(edges, g["three_rate"], side="right"), np.nan)
    tier_rates = (
        df[df["in_pool"] & df["tier"].notna()]
        .groupby(["season", "tier"])[["cs_fg3m", "cs_fg3a", "contested_fg3a", "def_fg3a"]].sum()
    )
    tier_rates["prior_cs_pct"] = tier_rates["cs_fg3m"] / tier_rates["cs_fg3a"]
    tier_rates["prior_contested_share"] = tier_rates["contested_fg3a"] / tier_rates["def_fg3a"]
    df = df.join(tier_rates[["prior_cs_pct", "prior_contested_share"]], on=["season", "tier"])

    df["cs_pct_shrunk"] = (df["cs_fg3m"] + k * df["prior_cs_pct"]) / (df["cs_fg3a"] + k)
    df["contested_share_shrunk"] = (df["contested_fg3a"] + k * df["prior_contested_share"]) / (df["def_fg3a"] + k)
    df["cs_pct_raw"] = np.where(df["cs_fg3a"] > 0, df["cs_fg3m"] / df["cs_fg3a"].where(df["cs_fg3a"] > 0, 1), np.nan)

    print("Volume-tier priors (latest full season):")
    print(tier_rates.loc[int(df["season"].max()) - 1][["prior_cs_pct", "prior_contested_share"]].round(3).to_string())

    comps = {"three_rate": "z_three_rate", "cs_pct_shrunk": "z_cs_pct", "contested_share_shrunk": "z_contested"}
    for src, dst in comps.items():
        df[dst] = np.nan
    for season, idx in df.groupby("season").groups.items():
        g = df.loc[idx]
        pool = g[(g["minutes"] >= POOL_MIN_MINUTES) & g["three_rate"].notna()]
        for src, dst in comps.items():
            mu, sd = pool[src].mean(), pool[src].std(ddof=0)
            df.loc[idx, dst] = (g[src] - mu) / sd
    df["gravity"] = df[list(comps.values())].sum(axis=1, min_count=3)
    df["obpm_used"] = np.where(df["minutes"] >= POOL_MIN_MINUTES, df["obpm"], REPLACEMENT_OBPM)

    # ---- lineups ----
    cur.execute("SELECT season, group_id, team_abbreviation, player_ids, poss, off_rating FROM lineup_stats;")
    lu = pd.DataFrame(cur.fetchall(), columns=["season", "group_id", "team", "player_ids", "poss", "off_rating"])
    grav = df.set_index(["season", "player_id"])[["gravity", "obpm_used"]]
    grav_d = grav.to_dict("index")

    def lineup_sums(r):
        vals = [grav_d.get((r.season, int(p))) for p in r.player_ids]
        if any(v is None or pd.isna(v["gravity"]) or pd.isna(v["obpm_used"]) for v in vals):
            return pd.Series({"spacing": np.nan, "sum_obpm": np.nan})
        return pd.Series({"spacing": sum(v["gravity"] for v in vals), "sum_obpm": sum(v["obpm_used"] for v in vals)})

    lu = pd.concat([lu, lu.apply(lineup_sums, axis=1)], axis=1)
    lu_all_100 = lu[lu["poss"] >= MIN_LINEUP_POSS]
    fit = lu_all_100.dropna(subset=["spacing", "sum_obpm"]).copy()
    n_dropped = len(lu_all_100) - len(fit)

    seasons = sorted(fit["season"].unique())
    dummies = np.column_stack([(fit["season"] == s).astype(float) for s in seasons[1:]])
    X_full = np.column_stack([np.ones(len(fit)), fit["spacing"], fit["sum_obpm"], dummies])
    X_base = np.column_stack([np.ones(len(fit)), fit["sum_obpm"], dummies])
    clusters = fit["team"].astype(str) + "-" + fit["season"].astype(str)
    full = wls_cluster(fit["off_rating"], X_full, fit["poss"], clusters)
    base = wls_cluster(fit["off_rating"], X_base, fit["poss"], clusters)

    # Real distribution of lineup spacing among real 100+-possession lineups, for the court tint.
    spacing_pct = np.percentile(fit["spacing"], [5, 25, 50, 75, 95])

    cur.execute("""
        DROP TABLE IF EXISTS player_gravity;
        CREATE TABLE player_gravity (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            minutes DOUBLE PRECISION,
            in_pool BOOLEAN NOT NULL,
            tracked BOOLEAN NOT NULL,
            fg3a_total DOUBLE PRECISION,
            three_rate DOUBLE PRECISION,
            cs_fg3m INTEGER, cs_fg3a INTEGER,
            cs_pct_raw DOUBLE PRECISION, cs_pct_shrunk DOUBLE PRECISION,
            def_fg3a INTEGER, contested_fg3a INTEGER,
            contested_share_shrunk DOUBLE PRECISION,
            z_three_rate DOUBLE PRECISION, z_cs_pct DOUBLE PRECISION, z_contested DOUBLE PRECISION,
            gravity DOUBLE PRECISION,
            obpm_used DOUBLE PRECISION,
            PRIMARY KEY (season, player_id)
        );
        DROP TABLE IF EXISTS gravity_validation;
        CREATE TABLE gravity_validation (
            id INTEGER PRIMARY KEY,
            n_lineups INTEGER, n_clusters INTEGER, n_lineups_dropped INTEGER,
            season_min INTEGER, season_max INTEGER,
            coef_spacing DOUBLE PRECISION, se_spacing DOUBLE PRECISION,
            ci_low DOUBLE PRECISION, ci_high DOUBLE PRECISION, p_spacing DOUBLE PRECISION,
            coef_obpm DOUBLE PRECISION, p_obpm DOUBLE PRECISION,
            r2 DOUBLE PRECISION, r2_without_spacing DOUBLE PRECISION,
            spacing_p5 DOUBLE PRECISION, spacing_p25 DOUBLE PRECISION, spacing_p50 DOUBLE PRECISION,
            spacing_p75 DOUBLE PRECISION, spacing_p95 DOUBLE PRECISION
        );
        DROP TABLE IF EXISTS gravity_tracking_coverage;
        CREATE TABLE gravity_tracking_coverage (
            season INTEGER PRIMARY KEY,
            tracked_fg3a DOUBLE PRECISION,
            boxscore_fg3a DOUBLE PRECISION
        );
    """)

    def f(x):
        return None if pd.isna(x) else float(x)

    psycopg2.extras.execute_values(cur, "INSERT INTO player_gravity VALUES %s", [
        (int(r.season), int(r.player_id), r.player_name, r.team, f(r.minutes), bool(r.in_pool), bool(r.tracked),
         f(r.fg3a_total), f(r.three_rate), int(r.cs_fg3m), int(r.cs_fg3a), f(r.cs_pct_raw), f(r.cs_pct_shrunk),
         int(r.def_fg3a), int(r.contested_fg3a), f(r.contested_share_shrunk),
         f(r.z_three_rate), f(r.z_cs_pct), f(r.z_contested), f(r.gravity), f(r.obpm_used))
        for r in df.itertuples()
    ])
    cur.execute("INSERT INTO gravity_validation VALUES %s;" % "(%s)" % ",".join(["%s"] * 20), (
        1, full["n"], full["n_clusters"], int(n_dropped), int(seasons[0]), int(seasons[-1]),
        float(full["beta"][1]), float(full["se"][1]), float(full["ci_low"][1]), float(full["ci_high"][1]),
        float(full["p"][1]), float(full["beta"][2]), float(full["p"][2]),
        full["r2"], base["r2"], *[float(x) for x in spacing_pct],
    ))
    psycopg2.extras.execute_values(cur, "INSERT INTO gravity_tracking_coverage VALUES %s", coverage)
    conn.commit()

    print("Tracked 3PA (defender bands) vs box-score 3PA:")
    for s, t, b in coverage:
        print(f"  {s}: {t:,.0f} / {b:,.0f} = {t / b:.3f}")
    print(f"\nLineup validation: n = {full['n']} real lineups (100+ poss), {full['n_clusters']} team-season clusters, "
          f"{n_dropped} dropped (a player with no gravity/OBPM)")
    print(f"  spacing coef = {full['beta'][1]:.3f} ORtg per gravity point, 95% CI [{full['ci_low'][1]:.3f}, "
          f"{full['ci_high'][1]:.3f}], p = {full['p'][1]:.4g}")
    print(f"  sum-OBPM coef = {full['beta'][2]:.3f}, p = {full['p'][2]:.4g}")
    print(f"  R² = {full['r2']:.4f} (without spacing: {base['r2']:.4f})")
    print(f"  lineup spacing percentiles 5/25/50/75/95: {np.round(spacing_pct, 2)}")
    latest = int(df["season"].max()) - 1
    q = df[(df["season"] == latest) & df["in_pool"]]
    print(f"\n{latest} top gravity (pool):")
    print(q.nlargest(12, "gravity")[["player_name", "three_rate", "cs_fg3a", "cs_pct_raw", "contested_share_shrunk",
                                      "gravity"]].round(3).to_string())
    print(f"\n{latest} lowest gravity (pool):")
    print(q.nsmallest(5, "gravity")[["player_name", "three_rate", "cs_fg3a", "gravity"]].round(3).to_string())
    conn.close()


if __name__ == "__main__":
    main()
