"""
build_aging_curves.py
======================
Aging curves: how a typical player's per-minute production, shooting and
rates change from one age to the next. Writes aging_curves (one row per
stat, era and age), aging_curve_summary (one row per stat and era) and
aging_league_average (each stat's league average per season, the zero line
a player's career is placed against), read by GET /aging/curves (api/routers/aging.py) and the Aging Curves page.

Delta method (the standard one, e.g. Tango / Lichtman for baseball):
  - every player with two consecutive seasons (s, s+1), both with 250+
    minutes (NBA.com's rows from 2009-10 to 2024-25 only exist from 200
    minutes, so 250 keeps the pool the same in every era); shooting % also
    need 100 FGA / 50 3PA / 50 FTA in both seasons;
  - each season's value is centred on that season's league average (the
    minutes- or attempts-weighted mean of the same qualified pool), so a
    league-wide trend (three-point volume rising every year) doesn't count
    as players getting better with age;
  - change from age a to a+1, weighted by the harmonic mean of the two
    seasons' minutes (attempts for shooting %), averaged over every pair
    starting at age a; the deltas are chained into a curve;
  - the curve's level is anchored at age 27: the weighted average of
    27-year-olds' values against their league in that era;
  - ages with fewer than 30 pairs are left off (the chain stops there);
    under 100 pairs is flagged thin;
  - 95% ranges and the peak-age range: 300 bootstrap resamples of players
    (a player's whole career goes in or out together).

Age: one convention for every season, the player's age on February 1 of
the season (Basketball-Reference's), from player_bio.birth_date.
player_season_stats.age mixes two conventions (Basketball-Reference before
2009-10, NBA.com's later-in-the-season age after, a year older for ~45% of
players), so it isn't used; the 7 player-seasons with no birth date on file
are skipped.

Three eras: every season the stat is recorded in (per-minute stats need
minutes, recorded from 1951-52), the three-point era (pairs starting
1979-80 on) and 2009-10 on (one data source: NBA.com).

Rebound %, offensive rebound % and turnover % are defined differently by
Basketball-Reference (to 2008-09) and NBA.com (2009-10 on): median rebound
% 0.091 in 2008-09, 0.079 in 2009-10. Pairs crossing that line (2008-09 ->
2009-10) are left out for the five rate stats.

Survivor bias, measured rather than corrected: for each age, the share of
qualified players with a qualified season the next year, and how the ones
who didn't come back compared with the ones who did at that age. A player
who falls off a cliff and leaves the league has no pair, so the curve's
decline at old ages is if anything too gentle.

Usage:
    cd scripts && python3 build_aging_curves.py
"""

from datetime import date

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

MIN_MINUTES = 250
ATT_FLOORS = {"fga": 100, "fg3a": 50, "fta": 50, "tsa": 100}
MIN_PAIRS = 30      # an age needs this many pairs to be on the curve
THIN_PAIRS = 100    # ... and is flagged thin below this
REF_AGE = 27
BOOT = 300
MINUTES_FROM = 1952  # minutes a game recorded from 1951-52
SOURCE_BREAK = 2010  # NBA.com rows from 2009-10 on
MODERN_FROM = 2010
THREE_POINT_FROM = 1980

# key -> (label, group, kind, first_season, higher_is_better, weight)
#   kind: per36 (count per 36 minutes) | per_game | pct | rate | bpm
#   weight: 'minutes', 'games' or an attempts column
STATS = {
    "pts": ("Points per 36", "Per 36 minutes", "per36", 1952, True, "minutes"),
    "reb": ("Rebounds per 36", "Per 36 minutes", "per36", 1952, True, "minutes"),
    "ast": ("Assists per 36", "Per 36 minutes", "per36", 1952, True, "minutes"),
    "stl": ("Steals per 36", "Per 36 minutes", "per36", 1974, True, "minutes"),
    "blk": ("Blocks per 36", "Per 36 minutes", "per36", 1974, True, "minutes"),
    "tov": ("Turnovers per 36", "Per 36 minutes", "per36", 1978, False, "minutes"),
    "oreb": ("Offensive rebounds per 36", "Per 36 minutes", "per36", 1974, True, "minutes"),
    "fg3a": ("3-point attempts per 36", "Per 36 minutes", "per36", 1980, True, "minutes"),
    "fg3m": ("3-pointers made per 36", "Per 36 minutes", "per36", 1980, True, "minutes"),
    "fta": ("Free-throw attempts per 36", "Per 36 minutes", "per36", 1952, True, "minutes"),
    "min": ("Minutes a game", "Playing time", "per_game", 1952, True, "games"),
    "fg_pct": ("Field-goal %", "Shooting", "pct", 1950, True, "fga"),
    "fg3_pct": ("3-point %", "Shooting", "pct", 1980, True, "fg3a"),
    "ft_pct": ("Free-throw %", "Shooting", "pct", 1950, True, "fta"),
    "ts_pct": ("True shooting %", "Shooting", "pct", 1950, True, "tsa"),
    "efg_pct": ("Effective FG %", "Shooting", "pct", 1980, True, "fga"),
    "usg_pct": ("Usage %", "Rates", "rate", 1978, True, "minutes"),
    "ast_pct": ("Assist %", "Rates", "rate", 1965, True, "minutes"),
    "reb_pct": ("Rebound %", "Rates", "rate", 1971, True, "minutes"),
    "oreb_pct": ("Offensive rebound %", "Rates", "rate", 1974, True, "minutes"),
    "tov_pct": ("Turnover %", "Rates", "rate", 1978, False, "minutes"),
    "bpm": ("BPM", "Impact", "bpm", 1974, True, "minutes"),
    "obpm": ("Offensive BPM", "Impact", "bpm", 1974, True, "minutes"),
    "dbpm": ("Defensive BPM", "Impact", "bpm", 1974, True, "minutes"),
}
# Defined differently before and after the source switch (see docstring).
NO_CROSS_SOURCE = {"reb_pct", "oreb_pct", "tov_pct", "usg_pct", "ast_pct"}
ERAS = {
    "all": ("Every season recorded", None),
    "three_point": ("Three-point era (1979-80 on)", THREE_POINT_FROM),
    "modern": ("2009-10 on", MODERN_FROM),
}


def load(conn):
    cols = ", ".join(f"s.{k}" for k in STATS if k != "min")
    d = pd.read_sql_query(
        f"""SELECT s.player_id, s.player_name, s.season, s.gp, s.min,
                   s.fga * s.gp AS fga_n, s.fg3a * s.gp AS fg3a_n, s.fta * s.gp AS fta_n, {cols},
                   b.birth_date
            FROM player_season_stats s LEFT JOIN player_bio b USING (player_id)
            ORDER BY s.player_id, s.season;""", conn)
    skipped = int(d.birth_date.isna().sum())
    d = d[d.birth_date.notna()].copy()
    bd = pd.to_datetime(d.birth_date)
    # Age on February 1 of the season's end year.
    d["age_feb1"] = d.season - bd.dt.year - ((bd.dt.month > 2) | ((bd.dt.month == 2) & (bd.dt.day > 1))).astype(int)
    d["minutes"] = d.gp * d["min"]
    d["games"] = d.gp.astype(float)
    d["tsa_n"] = d.fga_n + 0.44 * d.fta_n  # season totals (the table is per game)
    return d, skipped


def values(d, key):
    """(value, weight, qualified) for one stat on every player-season."""
    _l, _g, kind, first, _hib, wcol = STATS[key]
    if kind == "per36":
        v = d[key] / d["min"] * 36
    else:
        v = d[key].astype(float)
    wname = f"{wcol}_n" if wcol in ATT_FLOORS else wcol
    w = d[wname].astype(float)
    ok = (d.season >= first) & v.notna() & np.isfinite(v) & (d.minutes >= MIN_MINUTES)
    if wcol in ATT_FLOORS:
        ok &= d[wname] >= ATT_FLOORS[wcol]
    return v.astype(float), w, ok


def centre(d, v, w, ok):
    """Value minus its season's weighted league average (qualified pool)."""
    q = pd.DataFrame({"s": d.season[ok], "vw": (v * w)[ok], "w": w[ok]})
    g = q.groupby("s").sum()
    league = g.vw / g.w
    return v - d.season.map(league), league


def curve(pairs_age, delta, wt, players, rng, ages):
    """Weighted mean delta per age, chained; bootstrap over players."""
    idx = pairs_age - ages[0]
    k = len(ages)
    sw = np.bincount(idx, weights=wt, minlength=k)
    sd = np.bincount(idx, weights=wt * delta, minlength=k)
    d = sd / sw
    # Bootstrap: resample players, count how often each appears.
    uniq, pinv = np.unique(players, return_inverse=True)
    boot = np.empty((BOOT, k))
    for b in range(BOOT):
        cnt = np.bincount(rng.integers(0, len(uniq), len(uniq)), minlength=len(uniq))
        wb = wt * cnt[pinv]
        swb = np.bincount(idx, weights=wb, minlength=k)
        sdb = np.bincount(idx, weights=wb * delta, minlength=k)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot[b] = np.where(swb > 0, sdb / swb, d)
    return d, boot


def chain(deltas):
    """Level at each age (first age = 0) from deltas a -> a+1; one longer."""
    return np.concatenate([np.zeros(deltas.shape[:-1] + (1,)), np.cumsum(deltas, axis=-1)], axis=-1)


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    d, skipped = load(conn)
    print(f"{len(d)} player-seasons with a birth date ({skipped} without, skipped)")
    rng = np.random.default_rng(2026)
    rows, summary, league_rows = [], [], []

    for key, (label, group, kind, first, hib, wcol) in STATS.items():
        v, w, ok = values(d, key)
        rel, league = centre(d, v, w, ok)
        pool = ok.groupby(d.season).sum()
        league_rows += [(key, int(s_), float(a), int(pool[s_])) for s_, a in league.items()]
        f = pd.DataFrame({"pid": d.player_id, "name": d.player_name, "season": d.season, "age": d.age_feb1,
                          "v": v, "rel": rel, "w": w, "ok": ok})
        nxt = f.groupby("pid").shift(-1)
        consecutive = (nxt.season == f.season + 1)
        # Pairs: both seasons qualified.
        P = f[ok & consecutive & nxt.ok.fillna(False).astype(bool)].copy()
        P["rel_next"] = nxt.rel[P.index]
        P["w_next"] = nxt.w[P.index]
        if key in NO_CROSS_SOURCE:
            P = P[P.season != SOURCE_BREAK - 1]
        P["delta"] = P.rel_next - P.rel
        P["hw"] = 2 * P.w * P.w_next / (P.w + P.w_next)
        # Survivors: of qualified player-seasons, who had a qualified next season.
        Q = f[ok].copy()
        Q["returned"] = (consecutive & nxt.ok.fillna(False).astype(bool))[Q.index]

        for era, (era_label, era_from) in ERAS.items():
            start = max(first, era_from or first)
            E = P[P.season >= start]
            ages_count = E.groupby("age").size()
            good = ages_count[ages_count >= MIN_PAIRS].index.to_numpy()
            if len(good) < 5:
                continue
            # Longest run of consecutive ages with enough pairs, containing REF_AGE.
            runs = np.split(good, np.where(np.diff(good) != 1)[0] + 1)
            run = next((r for r in runs if r[0] <= REF_AGE <= r[-1]), None)
            if run is None:
                continue
            ages = np.arange(run[0], run[-1] + 1)
            E = E[E.age.isin(ages)]
            delta, boot = curve(E.age.to_numpy() - 0, E.delta.to_numpy(), E.hw.to_numpy(), E.pid.to_numpy(),
                                rng, ages)
            lvl_ages = np.arange(ages[0], ages[-1] + 2)          # curve covers one more age
            ref_i = REF_AGE - lvl_ages[0]
            # Anchor: weighted mean of 27-year-olds against their league, this era's seasons.
            QE = Q[(Q.season >= start)]
            a27 = QE[QE.age == REF_AGE]
            anchor = float((a27.rel * a27.w).sum() / a27.w.sum())
            level = chain(delta)
            level = level - level[ref_i] + anchor
            blev = chain(boot)
            brel = blev - blev[:, [ref_i]]                    # change vs. age 27, per resample
            lo, hi = np.percentile(brel, [2.5, 97.5], axis=0)
            best = np.argmax if hib else np.argmin
            peak_i = int(best(level))
            bpeaks = lvl_ages[best(blev, axis=1)]
            plo, phi = np.percentile(bpeaks, [2.5, 97.5])
            # Survivors by age (qualified seasons in this era).
            surv = {}
            for a, g in QE[QE.age.isin(lvl_ages)].groupby("age"):
                stay, leave = g[g.returned], g[~g.returned]
                gap = None
                if len(leave) >= 10 and len(stay) >= 10:
                    gap = float((leave.rel * leave.w).sum() / leave.w.sum() - (stay.rel * stay.w).sum() / stay.w.sum())
                # The last season on file has no next season to return to.
                n_eligible = int((g.season < d.season.max()).sum())
                ret = g[g.season < d.season.max()].returned
                surv[int(a)] = (len(g), n_eligible, float(ret.mean()) if n_eligible else None, gap)
            npairs = E.groupby("age").size().reindex(ages, fill_value=0)
            wsum = E.groupby("age").hw.sum().reindex(ages, fill_value=0.0)
            for i, a in enumerate(lvl_ages):
                on_delta = i < len(ages)
                s = surv.get(int(a), (0, 0, None, None))
                rows.append((key, era, int(a), float(level[i]), float(level[i] - level[ref_i]),
                             float(lo[i]), float(hi[i]),
                             float(delta[i]) if on_delta else None,
                             int(npairs.iloc[i]) if on_delta else None,
                             float(wsum.iloc[i]) if on_delta else None,
                             bool(on_delta and npairs.iloc[i] < THIN_PAIRS),
                             s[0], s[1], s[2], s[3]))
            latest = int(league.index.max())
            summary.append((key, era, label, group, kind, hib, wcol, era_label, int(start), int(E.season.max()) + 1,
                            int(lvl_ages[0]), int(lvl_ages[-1]), int(lvl_ages[peak_i]), float(plo), float(phi),
                            float(level[peak_i] - level[ref_i]), REF_AGE, anchor, int(len(E)), int(E.pid.nunique()),
                            latest, float(league[latest]), MIN_MINUTES, ATT_FLOORS.get(wcol), date.today()))
            ch = lambda a: level[a - lvl_ages[0]] - level[ref_i] if lvl_ages[0] <= a <= lvl_ages[-1] else float("nan")  # noqa: E731
            print(f"  {key:>9} {era:<11} ages {lvl_ages[0]}-{lvl_ages[-1]}  peak {lvl_ages[peak_i]} "
                  f"[{plo:.0f}-{phi:.0f}]  vs27: 22 {ch(22):+.3f}  31 {ch(31):+.3f}  34 {ch(34):+.3f}  "
                  f"pairs {len(E)}")

    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS aging_curves; DROP TABLE IF EXISTS aging_curve_summary; "
                "DROP TABLE IF EXISTS aging_league_average;")
    cur.execute("""
        CREATE TABLE aging_curves (
            stat TEXT, era TEXT, age INTEGER,
            level DOUBLE PRECISION, change_vs_ref DOUBLE PRECISION, ci_lo DOUBLE PRECISION, ci_hi DOUBLE PRECISION,
            delta_next DOUBLE PRECISION, pairs INTEGER, pair_weight DOUBLE PRECISION, thin BOOLEAN,
            seasons_at_age INTEGER, seasons_with_next INTEGER, returned_share DOUBLE PRECISION,
            leaver_gap DOUBLE PRECISION,
            PRIMARY KEY (stat, era, age));
        CREATE TABLE aging_curve_summary (
            stat TEXT, era TEXT, label TEXT, stat_group TEXT, kind TEXT, higher_is_better BOOLEAN,
            weight TEXT, era_label TEXT, season_from INTEGER, season_to INTEGER,
            age_from INTEGER, age_to INTEGER, peak_age INTEGER, peak_lo DOUBLE PRECISION, peak_hi DOUBLE PRECISION,
            peak_vs_ref DOUBLE PRECISION, ref_age INTEGER, ref_level DOUBLE PRECISION,
            pairs INTEGER, players INTEGER, latest_season INTEGER, latest_league DOUBLE PRECISION,
            min_minutes INTEGER, min_attempts INTEGER, built_on DATE,
            PRIMARY KEY (stat, era));
        CREATE TABLE aging_league_average (
            stat TEXT, season INTEGER, league_average DOUBLE PRECISION, pool INTEGER,
            PRIMARY KEY (stat, season));""")
    psycopg2.extras.execute_values(cur, "INSERT INTO aging_curves VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO aging_curve_summary VALUES %s", summary)
    psycopg2.extras.execute_values(cur, "INSERT INTO aging_league_average VALUES %s", league_rows)
    conn.commit()
    print(f"wrote {len(rows)} aging_curves rows, {len(summary)} summaries")
    conn.close()


if __name__ == "__main__":
    main()
