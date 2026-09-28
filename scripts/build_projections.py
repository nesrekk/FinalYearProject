"""
build_projections.py
=====================
Next-season projections: a transparent Marcel-style baseline (Tom Tango's
"Marcel the Monkey" method, the simplest projection that's hard to beat)
for every Leaderboard stat where it makes sense, plus a backtest of the
same method on every season from 2000-01 on. Writes:

  player_projections   one row per current player and stat: the projection
                       for next season with its 80% range, the weight the
                       player's own numbers got, the sample behind it and
                       what he did last season
  projection_backtest  per stat and target season (season 0 = all pooled):
                       mean absolute error of the projection against the
                       actual, next to "same as last season", "league
                       average", the weighted average alone and the
                       projection without the age step; bias, calibration
                       slope and the 80% range's real coverage
  projection_ranges    the 10th/90th percentile of past errors per stat,
                       reliability bin and level bin (where the ranges
                       come from), with fallbacks (-1 = every bin) for
                       bins under 50 errors
  projection_backtest_rows
                       every backtest projection next to what happened
  projection_stats     the catalogue: how each stat is projected

Method, per stat and player:
  1. Weighted average of his last three seasons, weights 5/4/3 (most
     recent first), each season weighted by its sample too (minutes for
     per-36 rates, BPM and minutes a game; attempts for shooting %; the
     stat's own sample for rate stats, api/stat_samples.py).
  2. Regressed toward the league average of those same seasons (weighted
     the same way): the weight on his own numbers is N / (N + M), where N
     is the weighted sample (in units of one most-recent season) and M is
     the sample at which the stat is half signal, from stat_stability
     (split-half reliability, build_stat_stability.py). For minutes a game
     and BPM the split-half number describes only in-season noise, and
     what moves them year to year is real change (role, health), so M
     comes from their year-to-year correlation r instead:
     M = typical season sample x (1 - r) / r.
  3. Age adjustment along the aging curve (aging_curves, era 'all',
     build_aging_curves.py): the curve's level at his age next season
     minus its level at the age of each past season, weighted like the
     seasons. Beyond the curve's ages the last measured yearly change is
     carried on. Ages come from birth dates (age on February 1 of the
     season), never from the stored age column with its two conventions.
     Minutes a game are the exception (see minutes_steps): the population
     curve is mostly role changes, so their age step is the average miss
     of the regressed minutes projection for players of that age and
     minutes level (bench, rotation, starter), measured on the other
     backtest seasons and applied once.
  4. Per-game counts = projected per-36 rate x projected minutes a game / 36.

80% range: the 10th and 90th percentiles of the backtest's errors for
players like him: same stat, same reliability bin (weight on own numbers
under 0.5, 0.5-0.8, 0.8+) and same level bin (terciles of the projection).
Coverage is checked leave-one-season-out: each season's ranges come from
the other seasons' errors.

No projection for a player with under 250 minutes over the three seasons
(regressing his minutes toward a starter's league average would invent a
role); he gets a reason instead.

Backtest: every target season 2000-01 to the latest, players with 500+
minutes that season (shooting % also 100 FGA / 50 3PA / 50 FTA), using only
seasons before the target. Two things are not re-estimated per season and
would be in a strict backtest: the M values (measured on 2020-21 to
2025-26 data) and the aging curves (all seasons); both are a handful of
numbers, not fit to any one season.

Rebound %, offensive rebound %, turnover %, usage % and assist % change
definition in 2009-10 (Basketball-Reference before, NBA.com after), so a
projection never mixes seasons from both sides for them.

Not projected: on-court ratings and plus-minus (team results), VORP
(needs games and possessions), impact score (an in-house composite),
games played (injuries).

Usage:
    cd scripts && python3 build_projections.py
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from stat_samples import SEASON_SAMPLE_SQL  # noqa: E402

WEIGHTS = {1: 5.0, 2: 4.0, 3: 3.0}         # seasons back -> weight
BACKTEST_FROM = 2001
EVAL_MIN_MINUTES = 500
ATT_FLOORS = {"fga": 100, "fg3a": 50, "fta": 50, "tsa": 100}
SOURCE_BREAK = 2010
K_BINS = [0.0, 0.5, 0.8, 1.0001]           # weight on own numbers
K_BIN_LABELS = ["under 0.5", "0.5 to 0.8", "0.8 and up"]
RANGE_LO, RANGE_HI = 10, 90
MIN_BIN = 50                                # errors a range bin needs; below it, fall back
ALL_BIN = -1                                # k_bin / level_bin value meaning "every bin"

# key -> (label, group, kind, source column, sample, aging key, first season, higher is better, format)
#   kind: per36 | per_game (count = per36 x minutes) | mpg | pct | rate | bpm
#   sample: minutes | games | an attempts column | a stat_samples expression key
COUNTS = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb"]
COUNT_LABELS = {"pts": "Points", "reb": "Rebounds", "ast": "Assists", "stl": "Steals", "blk": "Blocks",
                "tov": "Turnovers", "fg3m": "3-pointers made", "fg3a": "3-point attempts",
                "fta": "Free-throw attempts", "oreb": "Offensive rebounds"}
COUNT_FIRST = {"pts": 1952, "reb": 1952, "ast": 1952, "stl": 1974, "blk": 1974, "tov": 1978, "fg3m": 1980,
               "fg3a": 1980, "fta": 1952, "oreb": 1974}
STATS = {}
for _k in COUNTS:
    STATS[_k] = (COUNT_LABELS[_k], "Per game", "per_game", _k, "minutes", _k, COUNT_FIRST[_k], _k != "tov", "num1")
    STATS[f"{_k}36"] = (f"{COUNT_LABELS[_k]} per 36", "Per 36 minutes", "per36", _k, "minutes", _k,
                        COUNT_FIRST[_k], _k != "tov", "num1")
STATS.update({
    "min": ("Minutes a game", "Per game", "mpg", "min", "games", "min", 1952, True, "num1"),
    "fg_pct": ("Field-goal %", "Shooting", "pct", "fg_pct", "fga", "fg_pct", 1950, True, "pct"),
    "fg3_pct": ("3-point %", "Shooting", "pct", "fg3_pct", "fg3a", "fg3_pct", 1980, True, "pct"),
    "ft_pct": ("Free-throw %", "Shooting", "pct", "ft_pct", "fta", "ft_pct", 1950, True, "pct"),
    "ts_pct": ("True shooting %", "Shooting", "pct", "ts_pct", "tsa", "ts_pct", 1950, True, "pct"),
    "efg_pct": ("Effective FG %", "Shooting", "pct", "efg_pct", "fga", "efg_pct", 1980, True, "pct"),
    "usg_pct": ("Usage %", "Rates", "rate", "usg_pct", "usg_pct", "usg_pct", 1978, True, "pct"),
    "ast_pct": ("Assist %", "Rates", "rate", "ast_pct", "ast_pct", "ast_pct", 1965, True, "pct"),
    "reb_pct": ("Rebound %", "Rates", "rate", "reb_pct", "reb_pct", "reb_pct", 1971, True, "pct"),
    "oreb_pct": ("Offensive rebound %", "Rates", "rate", "oreb_pct", "oreb_pct", "oreb_pct", 1974, True, "pct"),
    "tov_pct": ("Turnover %", "Rates", "rate", "tov_pct", "tov_pct", "tov_pct", 1978, False, "pct"),
    "bpm": ("BPM", "Impact", "bpm", "bpm", "minutes", "bpm", 1974, True, "signed1"),
    "obpm": ("Offensive BPM", "Impact", "bpm", "obpm", "minutes", "obpm", 1974, True, "signed1"),
    "dbpm": ("Defensive BPM", "Impact", "bpm", "dbpm", "minutes", "dbpm", 1974, True, "signed1"),
})
NO_CROSS_SOURCE = {"reb_pct", "oreb_pct", "tov_pct", "usg_pct", "ast_pct"}
Y2Y_SHRINK = {"min", "bpm", "obpm", "dbpm"}   # M from year-to-year r, not split-half
MPG_BINS = [15.0, 28.0]                      # bench / rotation / starter, by minutes a game
MPG_MIN_PAIRS = 30
MIN_PROJ_MINUTES = 250                     # fewer minutes over the three seasons: no projection
NOT_PROJECTED = {
    "off_rating": "on-court team result, mostly teammates",
    "def_rating": "on-court team result, mostly teammates",
    "net_rating": "on-court team result, mostly teammates",
    "plus_minus": "on-court team result, mostly teammates",
    "vorp": "needs games and possessions, which aren't projected",
    "impact_score_raw": "in-house composite (year-to-year r 0.49)",
}


def load(conn):
    d = pd.read_sql_query(
        """SELECT s.player_id, s.player_name, s.team_abbreviation AS team, s.season, s.gp, s.min,
                  s.pts, s.reb, s.ast, s.stl, s.blk, s.tov, s.fg3m, s.fg3a, s.fta, s.oreb, s.fga,
                  s.fg_pct, s.fg3_pct, s.ft_pct, s.ts_pct, s.efg_pct,
                  s.usg_pct, s.ast_pct, s.reb_pct, s.oreb_pct, s.tov_pct, s.bpm, s.obpm, s.dbpm,
                  b.birth_date
           FROM player_season_stats s LEFT JOIN player_bio b USING (player_id)
           ORDER BY s.player_id, s.season;""", conn)
    for c in d.columns:
        if c not in ("player_name", "team", "birth_date"):
            d[c] = d[c].astype(float)
    bd = pd.to_datetime(d.birth_date)
    after_feb1 = (bd.dt.month > 2) | ((bd.dt.month == 2) & (bd.dt.day > 1))
    d["age"] = np.where(bd.notna(), d.season - bd.dt.year - after_feb1.astype(int), np.nan)
    d["minutes"] = d.gp * d["min"]
    d["games"] = d.gp
    d["fga_n"] = d.fga * d.gp
    d["fg3a_n"] = d.fg3a * d.gp
    d["fta_n"] = d.fta * d.gp
    d["tsa_n"] = d.fga_n + 0.44 * d.fta_n
    # Rate-stat samples as the Leaderboard defines them (stat_samples.py), per game x gp.
    d["usg_pct_n"] = (d.fga + 0.44 * d.fta + d.tov) * d.gp / d.usg_pct.replace(0, np.nan)
    d["ast_pct_n"] = d.ast * d.gp / d.ast_pct.replace(0, np.nan)
    d["reb_pct_n"] = d.reb * d.gp / d.reb_pct.replace(0, np.nan)
    d["oreb_pct_n"] = d.oreb * d.gp / d.oreb_pct.replace(0, np.nan)
    d["tov_pct_n"] = (d.fga + 0.44 * d.fta + d.ast + d.tov) * d.gp
    return d


def stat_values(d, key):
    """(value, sample) series for one catalogue stat."""
    _l, _g, kind, col, sample, _a, first, _h, _f = STATS[key]
    if kind in ("per36", "per_game"):
        v = d[col] / d["min"].replace(0, np.nan) * 36
    else:
        v = d[col].copy()
    n = d[sample] if sample in ("minutes", "games") else d[f"{sample}_n"]
    ok = (d.season >= first) & v.notna() & np.isfinite(v) & n.notna() & (n > 0)
    if sample in ATT_FLOORS:
        ok &= n >= 1
    return v.where(ok), n.where(ok)


def shrink_m(conn):
    """M per stat in the sample's own units."""
    st = pd.read_sql_query("SELECT stat, variant, stable_n, nba_unit_scale, typical_n FROM stat_stability;", conn)
    y2y = pd.read_sql_query("SELECT stat, r FROM stat_year_to_year;", conn).set_index("stat").r
    per_min = st[st.variant == "per_minute"].set_index("stat")
    cat = st[st.variant == "catalogue"].set_index("stat")
    typical_minutes = float(per_min.loc["pts", "typical_n"])
    typical_games = float(cat.loc["min", "typical_n"])
    out = {}
    for key, (_l, _g, kind, col, sample, _a, _f, _h, _fmt) in STATS.items():
        if key in Y2Y_SHRINK:
            r = float(y2y[col])
            typical = typical_games if sample == "games" else typical_minutes
            out[key] = (typical * (1 - r) / r, "year_to_year", r)
        elif kind in ("per36", "per_game"):
            out[key] = (float(per_min.loc[col, "stable_n"]), "split_half", None)
        else:
            row = cat.loc[col]
            out[key] = (float(row.stable_n) / float(row.nba_unit_scale), "split_half", None)
    return out


def aging_levels(conn):
    """{aging key: (ages array, levels array)} from era 'all', extended at both ends."""
    c = pd.read_sql_query("SELECT stat, age, level, delta_next FROM aging_curves WHERE era = 'all' ORDER BY stat, age;",
                          conn)
    out = {}
    for stat, g in c.groupby("stat"):
        ages = g.age.to_numpy(int)
        lvl = g.level.to_numpy(float)
        first_delta = float(g.delta_next.iloc[0])
        last_delta = float(g.delta_next.dropna().iloc[-1])
        full = np.arange(10, 61)
        ext = np.interp(full, ages, lvl)
        ext[full < ages[0]] = lvl[0] - (ages[0] - full[full < ages[0]]) * first_delta
        ext[full > ages[-1]] = lvl[-1] + (full[full > ages[-1]] - ages[-1]) * last_delta
        out[stat] = (full, ext)
    return out


def minutes_steps(rows, exclude_T=None):
    """The age step for minutes a game, by minutes level: the average miss
    (actual minus the regressed, un-aged projection) for players of that
    age and level in the backtest seasons other than exclude_T. The
    population curve (aging_curves 'min') is mostly players gaining and
    losing a role, and in the backtest it under-projected 31+ year-olds
    playing 28+ minutes by 2.6-3.5 minutes while over-projecting young
    starters; a player already at 34 minutes can't gain. Bench (<15),
    rotation (15-28) and starter (28+) players get their own step, applied
    once (Marcel's order: regress, then age). An age with under
    MPG_MIN_PAIRS players in a bin takes the nearest such age's step.
    Returns ({(bin, age): step}, {bin: pooled step})."""
    r = rows if exclude_T is None else rows[rows["T"] != exclude_T]
    r = r[r.age_T.notna()]
    by_cell = {(int(b), int(a)): float(g.resid.mean())
               for (b, a), g in r.groupby(["bin", "age_T"]) if len(g) >= MPG_MIN_PAIRS}
    pooled = {int(b): float(g.resid.mean()) for b, g in r.groupby("bin")}
    return by_cell, pooled


def minutes_step_at(steps, bin_id, age):
    """The bin's step at this age; outside the ages with enough players the
    nearest such age's step is carried on (like the aging curves)."""
    by_cell, pooled = steps
    ages = sorted(a for b, a in by_cell if b == bin_id)
    if not ages:
        return pooled.get(bin_id, 0.0)
    nearest = min(ages, key=lambda a: abs(a - age))
    return by_cell[(bin_id, nearest)]


def apply_minutes_step(frame, steps):
    b = np.digitize(frame.reg.to_numpy(float), MPG_BINS)
    age = frame.age_T.to_numpy(float)
    step = np.array([0.0 if np.isnan(a) else minutes_step_at(steps, int(bi), int(a)) for bi, a in zip(b, age)])
    out = frame.copy()
    out["age_adj"] = step
    out["proj"] = out.reg + step
    return out


def level_at(curve, age):
    full, ext = curve
    a = np.clip(np.nan_to_num(age, nan=10), 10, 60).astype(int)
    return np.where(np.isnan(age), np.nan, ext[a - 10])


def project(d, T, key, M, curve, league):
    """One stat's projection for target season T from seasons T-3..T-1.
    Returns a frame indexed by player_id. curve=None: no age step (minutes
    a game get theirs later, see minutes_steps)."""
    _l, _g, kind, col, sample, aging_key, first, _h, _f = STATS[key]
    lo_season = T - 3
    if key in NO_CROSS_SOURCE and T >= SOURCE_BREAK:
        lo_season = max(lo_season, SOURCE_BREAK)
    p = d[(d.season >= lo_season) & (d.season <= T - 1)].copy()
    v, n = stat_values(p, key)
    p["v"], p["n"] = v, n
    p = p[p.v.notna()]
    if p.empty:
        return None
    p["w"] = (T - p.season).map(WEIGHTS)
    p["wn"] = p.w * p.n
    p["L"] = p.season.map(league)
    p["age_T"] = p.age + (T - p.season)
    if curve is None:
        p["adj"] = 0.0
    else:
        p["adj"] = level_at(curve, p.age_T.to_numpy(float)) - level_at(curve, p.age.to_numpy(float))
    g = p.groupby("player_id")
    wn = g.wn.sum()
    out = pd.DataFrame({
        "wn": wn,
        "xbar": g.apply(lambda x: (x.wn * x.v).sum(), include_groups=False) / wn,
        "lbar": g.apply(lambda x: (x.wn * x.L).sum(), include_groups=False) / wn,
        "n_seasons": g.size(),
        "age_known": g.age.apply(lambda a: a.notna().all()),
        "age_T": g.age_T.max(),
        "minutes_window": g.minutes.sum(),
    })
    adj = g.apply(lambda x: (x.wn * x.adj).sum(), include_groups=False) / wn
    out["age_adj"] = adj.where(out.age_known, 0.0)
    out["N"] = out.wn / WEIGHTS[1]
    out["k"] = out.N / (out.N + M)
    out["reg"] = out.k * out.xbar + (1 - out.k) * out.lbar
    out["proj"] = out.reg + out.age_adj
    last = p.sort_values("season").groupby("player_id").last()
    out["last_value"] = last.v
    out["last_season"] = last.season.astype(int)
    out["last_n"] = last.n
    return out


def league_means(d, key):
    v, n = stat_values(d, key)
    q = pd.DataFrame({"s": d.season, "vn": v * n, "n": n}).dropna()
    g = q.groupby("s").sum()
    return (g.vn / g.n).to_dict()


def bins_for(k, level, cuts):
    kb = np.digitize(k, K_BINS[1:-1])
    lb = np.digitize(level, cuts)
    return kb, lb


def range_table(err_frame):
    """10th/90th percentile of the error per (k bin, level bin), plus the
    fallbacks: per k bin over every level (level_bin = ALL_BIN) and the whole
    stat (both ALL_BIN). Returns {(kb, lb): (lo, hi, n)}."""
    out = {}
    groups = [((int(kb), int(lb)), g) for (kb, lb), g in err_frame.groupby(["kb", "lb"])]
    groups += [((int(kb), ALL_BIN), g) for kb, g in err_frame.groupby("kb")]
    groups += [((ALL_BIN, ALL_BIN), err_frame)]
    for key, g in groups:
        lo, hi = np.percentile(g.err, [RANGE_LO, RANGE_HI])
        out[key] = (float(lo), float(hi), int(len(g)))
    return out


def lookup_range(table, kb, lb):
    for key in ((kb, lb), (kb, ALL_BIN), (ALL_BIN, ALL_BIN)):
        r = table.get(key)
        if r is not None and r[2] >= MIN_BIN:
            return r[0], r[1], key
    r = table[(ALL_BIN, ALL_BIN)]
    return r[0], r[1], (ALL_BIN, ALL_BIN)


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    d = load(conn)
    latest = int(d.season.max())
    target = latest + 1
    Ms = shrink_m(conn)
    curves = aging_levels(conn)
    print(f"{len(d)} player-seasons to {latest - 1}-{str(latest)[-2:]}; projecting {target - 1}-{str(target)[-2:]}; "
          f"{int(d[d.season == latest].birth_date.isna().sum())} current players without a birth date")

    leagues = {key: league_means(d, key) for key in STATS}
    # Every stat's projection for every target season (minutes without an age step yet).
    all_proj = {}
    for T in range(BACKTEST_FROM, target + 1):
        all_proj[T] = {key: project(d, T, key, Ms[key][0], None if key == "min" else curves[STATS[key][5]],
                                    leagues[key])
                       for key in STATS if STATS[key][2] != "per_game"}
    # Minutes: the age x level step, from the regressed projection's misses.
    min_rows = []
    for T in range(BACKTEST_FROM, latest + 1):
        pr = all_proj[T]["min"]
        if pr is None:
            continue
        act = d[(d.season == T) & (d.minutes >= EVAL_MIN_MINUTES)].set_index("player_id")["min"].rename("actual")
        j = pr.join(act, how="inner")
        j["T"] = T
        j["resid"] = j.actual - j.reg
        j["bin"] = np.digitize(j.reg.to_numpy(float), MPG_BINS)
        min_rows.append(j[["T", "reg", "age_T", "resid", "bin"]])
    min_rows = pd.concat(min_rows, ignore_index=True)
    cells, pooled = minutes_steps(min_rows)
    for b, lab in enumerate(("bench (<15)", "rotation (15-28)", "starter (28+)")):
        row = {a: cells.get((b, a)) for a in (22, 25, 27, 30, 33, 36)}
        print(f"  minutes step, {lab}: " + "  ".join(f"{a}: {v:+.2f}" if v is not None else f"{a}: —"
                                                     for a, v in row.items()) + f"  (all ages {pooled[b]:+.2f})")

    per_stat_rows = {key: [] for key in STATS}          # backtest evaluation rows (T, player, actual, proj, ...)
    current = {}
    for T in range(BACKTEST_FROM, target + 1):
        projections = dict(all_proj[T])
        # Under MIN_PROJ_MINUTES over the three seasons: no projection at all (the
        # regression toward a starter's league-average minutes would invent a role).
        enough = projections["pts36"].index[projections["pts36"].minutes_window >= MIN_PROJ_MINUTES]
        projections = {k: (None if v is None else v[v.index.isin(enough)]) for k, v in projections.items()}
        if projections["min"] is not None:
            projections["min"] = apply_minutes_step(projections["min"],
                                                    minutes_steps(min_rows, exclude_T=T if T <= latest else None))
        # Per-game counts from per-36 x minutes.
        mpg = projections["min"]
        for c in COUNTS:
            p36 = projections[f"{c}36"]
            if p36 is None or mpg is None:
                projections[c] = None
                continue
            j = p36.join(mpg[["proj", "reg", "xbar"]], rsuffix="_min", how="inner")
            out = j[["wn", "n_seasons", "age_known", "N", "k", "last_season"]].copy()
            out["proj"] = j.proj * j.proj_min / 36
            out["reg"] = j.reg * j.reg_min / 36          # without the age step on either part
            out["xbar"] = j.xbar * j.xbar_min / 36       # weighted average alone
            out["lbar"] = np.nan
            out["age_adj"] = out.proj - out.reg
            # Last season's per-game value.
            lastpg = d[(d.season == T - 1) | (d.season == T - 2) | (d.season == T - 3)]
            lastpg = lastpg.sort_values("season").groupby("player_id").last()
            out["last_value"] = lastpg[c].reindex(out.index)
            out["last_n"] = lastpg.games.reindex(out.index)
            projections[c] = out
        if T <= latest:
            actual_all = d[d.season == T].set_index("player_id")
            for key, pr in projections.items():
                if pr is None:
                    continue
                _l, _g, kind, col, sample, _a, first, _h, _f = STATS[key]
                av, an = stat_values(actual_all, key)
                if kind == "per_game":
                    av = actual_all[col]
                ok = av.notna() & (actual_all.minutes >= EVAL_MIN_MINUTES)
                if sample in ATT_FLOORS:
                    ok &= an >= ATT_FLOORS[sample]
                a = av[ok]
                j = pr.join(a.rename("actual"), how="inner")
                if j.empty:
                    continue
                league_last = leagues[key].get(T - 1, np.nan)
                if kind == "per_game":
                    # League per-game count of the prior season, minutes-weighted like the rest.
                    prev = d[d.season == T - 1]
                    league_last = float((prev[col] * prev.games).sum() / prev.games.sum())
                j["league"] = league_last
                j["T"] = T
                per_stat_rows[key].append(j.reset_index())
        else:
            current = projections

    # ── Ranges from the backtest errors, coverage leave-one-season-out ──
    backtest, ranges, catalogue, row_frames = [], [], [], []
    for key, frames in per_stat_rows.items():
        label, group, kind, col, sample, aging_key, first, hib, fmt = STATS[key]
        M, msrc, r = Ms[key]
        if not frames:
            continue
        e = pd.concat(frames, ignore_index=True)
        e["err"] = e.actual - e.proj
        cuts = np.quantile(e.proj, [1 / 3, 2 / 3])
        e["kb"], e["lb"] = bins_for(e.k.to_numpy(), e.proj.to_numpy(), cuts)
        # Ranges from every season (used for the live projections).
        for (kb, lb), (lo, hi, n) in range_table(e).items():
            ranges.append((key, kb, lb, lo, hi, n))
        # Coverage: each season judged by the other seasons' ranges (same fallback rule).
        e["lo"] = np.nan
        e["hi"] = np.nan
        for T, g in e.groupby("T"):
            table = range_table(e[e["T"] != T])
            bounds = np.array([lookup_range(table, int(kb), int(lb))[:2] for kb, lb in zip(g.kb, g.lb)])
            e.loc[g.index, "lo"] = g.proj.to_numpy() + bounds[:, 0]
            e.loc[g.index, "hi"] = g.proj.to_numpy() + bounds[:, 1]
        e["covered"] = (e.actual >= e.lo) & (e.actual <= e.hi)
        e["stat"] = key
        row_frames.append(e[["stat", "T", "player_id", "proj", "actual", "last_value", "k", "kb", "lb", "lo", "hi",
                             "n_seasons"]])

        def metrics(g):
            slope = np.nan
            if len(g) > 10 and g.proj.std() > 0:
                slope = float(np.polyfit(g.proj, g.actual, 1)[0])
            return (int(len(g)),
                    float((g.actual - g.proj).abs().mean()),
                    float((g.actual - g.last_value).abs().mean()),
                    float((g.actual - g.league).abs().mean()),
                    float((g.actual - g.xbar).abs().mean()),
                    float((g.actual - g.reg).abs().mean()),
                    float((g.actual - g.proj).mean()),
                    slope,
                    float(g[["actual", "proj"]].corr().iloc[0, 1]),
                    float(g.covered.mean()))
        for T, g in e.groupby("T"):
            backtest.append((key, int(T), *metrics(g)))
        backtest.append((key, 0, *metrics(e)))
        n_all, mae, mae_last, mae_lg, mae_x, mae_reg, bias, slope, corr, cov = metrics(e)
        catalogue.append((key, label, group, kind, col, sample, aging_key, first, hib, fmt,
                          float(M), msrc, None if r is None else float(r), float(cuts[0]), float(cuts[1]),
                          int(e["T"].min()), int(e["T"].max())))
        print(f"  {key:>9} n={n_all:6d}  MAE proj {mae:7.3f}  last {mae_last:7.3f}  league {mae_lg:7.3f}  "
              f"avg-only {mae_x:7.3f}  no-age {mae_reg:7.3f}  bias {bias:+.3f}  slope {slope:.2f}  cover80 {cov:.2f}")

    # ── Current projections with ranges ──
    rng = {}
    for k, kb, lb, lo, hi, n in ranges:
        rng.setdefault(k, {})[(kb, lb)] = (lo, hi, n)
    cuts_by = {c[0]: (c[13], c[14]) for c in catalogue}
    now = d[d.season == latest].set_index("player_id")
    rows = []
    for key, pr in current.items():
        if pr is None or key not in cuts_by:
            continue
        pr = pr[pr.index.isin(now.index)]
        kb, lb = bins_for(pr.k.to_numpy(), pr.proj.to_numpy(), cuts_by[key])
        for i, (pid, x) in enumerate(pr.iterrows()):
            lo, hi, _used = lookup_range(rng[key], int(kb[i]), int(lb[i]))
            p = now.loc[pid]
            age_next = None if pd.isna(p.age) else int(p.age) + (target - latest)
            rows.append((int(pid), p.player_name, p.team, target, key,
                         float(x.proj), float(x.proj + lo), float(x.proj + hi), float(x.k), float(x.N),
                         int(x.n_seasons), int(x.last_season), _num(x.last_value), _num(x.last_n),
                         _num(x.lbar), float(x.age_adj), bool(x.age_known), age_next, int(kb[i]), int(lb[i])))

    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS player_projections; DROP TABLE IF EXISTS projection_backtest; "
                "DROP TABLE IF EXISTS projection_ranges; DROP TABLE IF EXISTS projection_stats; "
                "DROP TABLE IF EXISTS projection_backtest_rows;")
    cur.execute("""
        CREATE TABLE player_projections (
            player_id INTEGER, player_name TEXT, team TEXT, season INTEGER, stat TEXT,
            projection DOUBLE PRECISION, lo DOUBLE PRECISION, hi DOUBLE PRECISION,
            own_weight DOUBLE PRECISION, sample DOUBLE PRECISION, seasons_used INTEGER,
            last_season INTEGER, last_value DOUBLE PRECISION, last_sample DOUBLE PRECISION,
            league_mean DOUBLE PRECISION, age_adjustment DOUBLE PRECISION, age_known BOOLEAN, age_next INTEGER,
            k_bin INTEGER, level_bin INTEGER,
            PRIMARY KEY (player_id, season, stat));
        CREATE TABLE projection_backtest (
            stat TEXT, season INTEGER, n INTEGER,
            mae DOUBLE PRECISION, mae_last DOUBLE PRECISION, mae_league DOUBLE PRECISION,
            mae_average DOUBLE PRECISION, mae_no_age DOUBLE PRECISION,
            bias DOUBLE PRECISION, slope DOUBLE PRECISION, r DOUBLE PRECISION, coverage80 DOUBLE PRECISION,
            PRIMARY KEY (stat, season));
        CREATE TABLE projection_ranges (
            stat TEXT, k_bin INTEGER, level_bin INTEGER, lo DOUBLE PRECISION, hi DOUBLE PRECISION, n INTEGER,
            PRIMARY KEY (stat, k_bin, level_bin));
        CREATE TABLE projection_stats (
            stat TEXT PRIMARY KEY, label TEXT, stat_group TEXT, kind TEXT, source_col TEXT, sample TEXT,
            aging_key TEXT, first_season INTEGER, higher_is_better BOOLEAN, format TEXT,
            shrink_m DOUBLE PRECISION, shrink_source TEXT, y2y_r DOUBLE PRECISION,
            level_cut1 DOUBLE PRECISION, level_cut2 DOUBLE PRECISION,
            backtest_from INTEGER, backtest_to INTEGER);
        CREATE TABLE projection_backtest_rows (
            stat TEXT, season INTEGER, player_id INTEGER, projection DOUBLE PRECISION, actual DOUBLE PRECISION,
            last_value DOUBLE PRECISION, own_weight DOUBLE PRECISION, k_bin INTEGER, level_bin INTEGER,
            lo DOUBLE PRECISION, hi DOUBLE PRECISION, seasons_used INTEGER,
            PRIMARY KEY (stat, season, player_id));""")
    psycopg2.extras.execute_values(cur, "INSERT INTO player_projections VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO projection_backtest VALUES %s", backtest)
    psycopg2.extras.execute_values(cur, "INSERT INTO projection_ranges VALUES %s", ranges)
    psycopg2.extras.execute_values(cur, "INSERT INTO projection_stats VALUES %s", catalogue)
    bt_rows = pd.concat(row_frames, ignore_index=True)
    bt_rows = bt_rows.astype({"T": int, "player_id": int, "kb": int, "lb": int, "n_seasons": int})
    psycopg2.extras.execute_values(cur, "INSERT INTO projection_backtest_rows VALUES %s",
                                   [tuple(None if (isinstance(v, float) and np.isnan(v)) else v for v in r)
                                    for r in bt_rows.itertuples(index=False, name=None)], page_size=5000)
    cur.execute("COMMENT ON TABLE player_projections IS %s",
                (f"Marcel-style next-season projections, built {date.today().isoformat()} by build_projections.py",))
    conn.commit()
    print(f"wrote {len(rows)} projections for {len({r[0] for r in rows})} players, {len(backtest)} backtest rows, "
          f"{len(ranges)} range bins")
    conn.close()


def _num(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


if __name__ == "__main__":
    main()
