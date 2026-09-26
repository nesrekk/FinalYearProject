"""
build_scouting_reports.py
===========================
Scouting Report ("Exploit Guide") v1 — automatic, statistically filtered
strengths and weaknesses for every qualified player-season, built only
from real data already in Postgres (no new fetch).

Qualified: >= 1,500 real regular-season minutes (player_season_stats
min x gp).

Splits tested per qualified player-season (each one a real comparison):
  * Shot zones (player_shots, regular-season games only — game_id '002…';
    zones from api/shots_lib.classify_zone(), the same classifier the
    shot charts use): the player's real FG% in each of the 5 zones vs.
    the rest of the league's real FG% in that zone that season.
    Two-proportion z-test. Min 50 real FGA in the zone.
  * Play types (player_playtypes, NBA Synergy): the player's real points
    per possession vs. the rest of the league's real PPP for that play
    type that season. Synergy gives possessions and PPP but no per-
    possession distribution, so the per-possession SD is estimated per
    (season, play type) by method of moments from the real spread of
    player PPPs: E[(PPP_i - mu)^2] = tau^2 + sigma^2 / poss_i (weighted
    least squares); a season/type whose fit gives no positive sigma^2 falls
    back to that play type's mean sigma across seasons. Real estimates
    land at ~0.8-1.6 points per possession, as expected for possessions
    scoring 0-3 points. z-test with SE = sigma / sqrt(poss). Min 50 real
    possessions. Synergy's "Misc" catch-all bucket (league PPP ~0.49) is
    not tested — it isn't a real play type, and in a first run it came up
    as a "strength" for nearly every star.
  * Leverage (player_leverage_splits, Garbage-Time Deflator, 2020-21+):
    real FG% in high-leverage moments vs. the player's own real FG% in all
    other moments. Two-proportion z-test. Min 50 real high-leverage FGA.

A split is a finding when p < 0.05 (two-sided). Every tested split is
stored — not just findings — so the API can say how many were tested and
how many would be expected to clear p < 0.05 by chance alone.

Leverage findings are stored but the API does NOT list them among
strengths/weaknesses: this script's own persistence check found them no
better than chance (first real run: 54% same direction next season,
5.7% significant again — i.e. the false-positive rate), the well-known
"clutch isn't a stable skill" result. They're shown separately, with that
caveat, instead.

Validation (scouting_validation): persistence — of the real findings in
season s for a player who is qualified again in s+1 with the same split
tested, the real share that point the same direction in s+1, and the share
that are significant again. Plus zone_classifier_check: the classifier's
real league zone totals vs. the NBA's own (league_shot_zones).

Usage:
    cd scripts && python3 build_scouting_reports.py
"""

import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy.stats import norm

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
from shots_lib import ZONES, classify_zone  # noqa: E402

MIN_MINUTES = 1500
MIN_ZONE_FGA = 50
MIN_PLAYTYPE_POSS = 50
MIN_LEVERAGE_FGA = 50
ALPHA = 0.05


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"


def two_prop(m1, n1, m2, n2):
    """Two-sided two-proportion z-test (pooled SE)."""
    p1, p2 = m1 / n1, m2 / n2
    p = (m1 + m2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    z = np.where(se > 0, (p1 - p2) / np.where(se > 0, se, 1), 0.0)
    return z, 2 * norm.sf(np.abs(z))


def vector_zones(x, y, shot_type):
    """Vectorized classify_zone() for rows with no NBA-assigned zone —
    identical rules (checked row-for-row against classify_zone below)."""
    x = np.nan_to_num(x.astype(float))
    y = np.nan_to_num(y.astype(float))
    three = shot_type.fillna("").str.startswith("3PT").values
    return np.where(
        three,
        np.where((np.abs(x) > 220) & (y < 92), "Corner 3", "Above the Break 3"),
        np.where(np.hypot(x, y) <= 40, "Restricted Area",
                 np.where((np.abs(x) <= 80) & (y <= 137.5), "In The Paint (Non-RA)", "Mid-Range")),
    )


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT MIN(season), MAX(season) FROM player_playtypes;")
    s_min, s_max = cur.fetchone()
    seasons = list(range(s_min, s_max + 1))
    cur.execute(
        """SELECT season, player_id, player_name, min * gp FROM player_season_stats
           WHERE season BETWEEN %s AND %s AND min * gp >= %s;""",
        (s_min, s_max, MIN_MINUTES),
    )
    qual = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "player_name", "minutes"])
    qual_keys = set(zip(qual["season"], qual["player_id"]))
    print(f"{len(qual):,} real qualified player-seasons (>= {MIN_MINUTES} min), seasons {s_min}-{s_max}")

    tests = []

    # ---- shot zones ----
    labels = [season_label(s) for s in seasons]
    cur.execute(
        """SELECT season, player_id, loc_x, loc_y, shot_type, shot_zone_basic, shot_made_flag
           FROM player_shots WHERE season = ANY(%s) AND game_id LIKE '002%%';""",
        (labels,),
    )
    shots = pd.DataFrame(cur.fetchall(), columns=["label", "player_id", "x", "y", "shot_type", "zbasic", "made"])
    shots["zone"] = vector_zones(shots["x"], shots["y"], shots["shot_type"])
    has_basic = shots["zbasic"].notna()
    if has_basic.any():
        shots.loc[has_basic, "zone"] = [
            classify_zone(None, None, None, None, zb) for zb in shots.loc[has_basic, "zbasic"]
        ]
    sample = shots[~has_basic].sample(min(50000, int((~has_basic).sum())), random_state=0)
    ref = [classify_zone(x, y, None, t) for x, y, t in zip(sample["x"], sample["y"], sample["shot_type"])]
    mismatch = int((np.array(ref, dtype=object) != sample["zone"].values).sum())
    assert mismatch == 0, f"vectorized zones disagree with classify_zone on {mismatch} sampled shots"
    print(f"  {len(shots):,} real regular-season shots; vectorized zones match classify_zone on a 50k sample")
    shots = shots[shots["zone"].isin(ZONES)]
    shots["season"] = shots["label"].str[:4].astype(int) + 1
    shots["made"] = shots["made"].astype(int)

    pz = shots.groupby(["season", "player_id", "zone"])["made"].agg(fgm="sum", fga="count").reset_index()
    lz = pz.groupby(["season", "zone"])[["fgm", "fga"]].sum().rename(columns={"fgm": "lg_fgm", "fga": "lg_fga"})
    pov = pz.groupby(["season", "player_id"])[["fgm", "fga"]].sum().rename(columns={"fgm": "ov_fgm", "fga": "ov_fga"})
    pz = pz.join(lz, on=["season", "zone"]).join(pov, on=["season", "player_id"])
    pz = pz[np.array([k in qual_keys for k in zip(pz["season"], pz["player_id"])]) & (pz["fga"] >= MIN_ZONE_FGA)]
    rest_m, rest_n = pz["lg_fgm"] - pz["fgm"], pz["lg_fga"] - pz["fga"]
    z, p = two_prop(pz["fgm"].values, pz["fga"].values, rest_m.values, rest_n.values)
    for r, zz, pp, rm, rn in zip(pz.itertuples(), z, p, rest_m, rest_n):
        tests.append((r.season, r.player_id, "zone", r.zone, r.fgm / r.fga, rm / rn, r.ov_fgm / r.ov_fga,
                      int(r.fga), "FGA", float(zz), float(pp)))

    # zone classifier check vs the NBA's own league zone totals
    cur.execute("SELECT season, zone, fga, fg_pct FROM league_shot_zones;")
    nba = pd.DataFrame(cur.fetchall(), columns=["label", "zone", "nba_fga", "nba_pct"])
    ours = shots.groupby(["label", "zone"])["made"].agg(our_fgm="sum", our_fga="count").reset_index()
    check = ours.merge(nba, on=["label", "zone"])
    check["our_pct"] = check["our_fgm"] / check["our_fga"]

    # ---- play types ----
    cur.execute("""SELECT season, player_id, play_type, poss, ppp FROM player_playtypes
                   WHERE side = 'offensive' AND poss > 0 AND play_type <> 'Misc';""")
    pt = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "play_type", "poss", "ppp"])
    pt[["poss", "ppp"]] = pt[["poss", "ppp"]].astype(float)
    pt["pts"] = pt["poss"] * pt["ppp"]
    sig = {}
    for (s, t), g in pt.groupby(["season", "play_type"]):
        mu = np.average(g["ppp"], weights=g["poss"])
        w = np.sqrt(g["poss"].values)
        X = np.column_stack([np.ones(len(g)), 1 / g["poss"].values]) * w[:, None]
        coef = np.linalg.lstsq(X, ((g["ppp"] - mu) ** 2).values * w, rcond=None)[0]
        sig[(s, t)] = float(np.sqrt(coef[1])) if coef[1] > 0 else None
    by_type = pd.Series({k: v for k, v in sig.items() if v}).groupby(level=1).mean()
    n_fallback = sum(1 for v in sig.values() if v is None)
    sig = {k: (v if v else float(by_type[k[1]])) for k, v in sig.items()}
    print(f"  play-type per-possession SD: {len(sig)} (season, type) fits, {n_fallback} fell back to the type's mean")

    lg = pt.groupby(["season", "play_type"])[["pts", "poss"]].sum().rename(columns={"pts": "lg_pts", "poss": "lg_poss"})
    own = pt.groupby(["season", "player_id"])[["pts", "poss"]].sum()
    own_ppp = (own["pts"] / own["poss"]).rename("own_ppp")
    pt = pt.join(lg, on=["season", "play_type"]).join(own_ppp, on=["season", "player_id"])
    pt = pt[np.array([k in qual_keys for k in zip(pt["season"], pt["player_id"])]) & (pt["poss"] >= MIN_PLAYTYPE_POSS)]
    for r in pt.itertuples():
        rest = (r.lg_pts - r.pts) / (r.lg_poss - r.poss)
        se = sig[(r.season, r.play_type)] / np.sqrt(r.poss)
        zz = (r.ppp - rest) / se
        tests.append((r.season, r.player_id, "playtype", r.play_type, r.ppp, rest, r.own_ppp,
                      int(r.poss), "possessions", float(zz), float(2 * norm.sf(abs(zz)))))

    # ---- leverage (Garbage-Time Deflator splits) ----
    cur.execute("SELECT to_regclass('public.player_leverage_splits');")
    if cur.fetchone()[0] is not None:
        cur.execute("SELECT season, player_id, bucket, fgm, fga FROM player_leverage_splits;")
        lv = pd.DataFrame(cur.fetchall(), columns=["season", "player_id", "bucket", "fgm", "fga"])
        hi = lv[lv["bucket"] == "high"].set_index(["season", "player_id"])[["fgm", "fga"]]
        other = lv[lv["bucket"] != "high"].groupby(["season", "player_id"])[["fgm", "fga"]].sum()
        both = hi.join(other, lsuffix="_hi", rsuffix="_ot", how="inner").reset_index()
        both = both[np.array([k in qual_keys for k in zip(both["season"], both["player_id"])])
                    & (both["fga_hi"] >= MIN_LEVERAGE_FGA) & (both["fga_ot"] > 0)]
        z, p = two_prop(both["fgm_hi"].values, both["fga_hi"].values, both["fgm_ot"].values, both["fga_ot"].values)
        for r, zz, pp in zip(both.itertuples(), z, p):
            other_pct = r.fgm_ot / r.fga_ot
            tests.append((r.season, r.player_id, "leverage", "High leverage", r.fgm_hi / r.fga_hi, other_pct,
                          other_pct, int(r.fga_hi), "FGA", float(zz), float(pp)))

    t = pd.DataFrame(tests, columns=["season", "player_id", "category", "split", "value", "baseline", "own_avg",
                                     "n", "n_unit", "z", "p"])
    t["significant"] = t["p"] < ALPHA
    t["direction"] = np.where(t["z"] > 0, "strength", "weakness")
    print(f"  {len(t):,} real splits tested, {int(t['significant'].sum()):,} significant at p < {ALPHA}")

    # ---- validation: persistence into the next season ----
    nxt = t[["season", "player_id", "category", "split", "z", "significant"]].copy()
    nxt["season"] -= 1
    f = t[t["significant"]].merge(nxt, on=["season", "player_id", "category", "split"], suffixes=("", "_next"))
    val = []
    for cat, g in list(f.groupby("category")) + [("all", f)]:
        n_findings = int(t[t["significant"] & ((t["category"] == cat) if cat != "all" else True)].shape[0])
        val.append((cat, n_findings, int(len(g)),
                    float((np.sign(g["z"]) == np.sign(g["z_next"])).mean()) if len(g) else None,
                    float(g["significant_next"].mean()) if len(g) else None))

    cur.execute("""
        DROP TABLE IF EXISTS scouting_splits;
        CREATE TABLE scouting_splits (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            category TEXT NOT NULL,
            split TEXT NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            baseline DOUBLE PRECISION NOT NULL,
            own_avg DOUBLE PRECISION,
            n INTEGER NOT NULL,
            n_unit TEXT NOT NULL,
            z DOUBLE PRECISION NOT NULL,
            p DOUBLE PRECISION NOT NULL,
            significant BOOLEAN NOT NULL,
            direction TEXT NOT NULL,
            PRIMARY KEY (season, player_id, category, split)
        );
        DROP TABLE IF EXISTS scouting_validation;
        CREATE TABLE scouting_validation (
            category TEXT PRIMARY KEY,
            n_findings INTEGER,
            n_followed INTEGER,
            same_direction_rate DOUBLE PRECISION,
            significant_again_rate DOUBLE PRECISION
        );
        DROP TABLE IF EXISTS zone_classifier_check;
        CREATE TABLE zone_classifier_check (
            season TEXT NOT NULL,
            zone TEXT NOT NULL,
            our_fga INTEGER, nba_fga INTEGER,
            our_fg_pct DOUBLE PRECISION, nba_fg_pct DOUBLE PRECISION,
            PRIMARY KEY (season, zone)
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO scouting_splits VALUES %s", [
        (int(r.season), int(r.player_id), r.category, r.split, float(r.value), float(r.baseline),
         None if pd.isna(r.own_avg) else float(r.own_avg), int(r.n), r.n_unit, float(r.z), float(r.p),
         bool(r.significant), r.direction)
        for r in t.itertuples()
    ])
    psycopg2.extras.execute_values(cur, "INSERT INTO scouting_validation VALUES %s", val)
    psycopg2.extras.execute_values(cur, "INSERT INTO zone_classifier_check VALUES %s", [
        (r.label, r.zone, int(r.our_fga), int(r.nba_fga), float(r.our_pct), float(r.nba_pct))
        for r in check.itertuples()
    ])
    conn.commit()

    print("\nZone classifier vs NBA's own league zone totals:")
    check["fga_err_pct"] = (check["our_fga"] - check["nba_fga"]) / check["nba_fga"] * 100
    check["pct_err_pts"] = (check["our_pct"] - check["nba_pct"]) * 100
    print(check[["label", "zone", "our_fga", "nba_fga", "fga_err_pct", "pct_err_pts"]].round(2).to_string())
    print("\nPersistence of real findings into the next season:")
    for v in val:
        print(f"  {v[0]}: {v[1]} findings, {v[2]} followed; same direction {v[3]:.3f}, significant again {v[4]:.3f}")
    names = dict(zip(zip(qual["season"], qual["player_id"]), qual["player_name"]))
    for name in ["Jalen Brunson", "Stephen Curry", "Giannis Antetokounmpo"]:
        pid = qual[(qual["player_name"] == name) & (qual["season"] == 2025)]["player_id"]
        if pid.empty:
            continue
        rows = t[(t["season"] == 2025) & (t["player_id"] == int(pid.iloc[0])) & t["significant"]]
        print(f"\n{names[(2025, int(pid.iloc[0]))]} 2024-25 findings ({len(rows)} of "
              f"{int(((t['season'] == 2025) & (t['player_id'] == int(pid.iloc[0]))).sum())} tested):")
        print(rows[["category", "split", "value", "baseline", "n", "p", "direction"]].round(3).to_string())
    conn.close()


if __name__ == "__main__":
    main()
