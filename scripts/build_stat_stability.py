"""
build_stat_stability.py
========================
"When does a stat mean something?" For every stat in the Leaderboard
Builder's catalogue: how big a sample it takes before a player's number is
more his own than luck. Writes stat_stability, stat_stability_curve and
stat_year_to_year, read by GET /leaderboard/stability and by the
Leaderboard/Breakout reliability warnings.

Split-half reliability (the main measure), where per-game data exists:
  - data: player_game_lines (play-by-play, every regular-season game
    2020-21 to 2025-26; build_player_game_lines.py) and, for FG%, 3P% and
    eFG%, player_shots (every regular-season shot 1996-97 to 2025-26);
  - each player-season's games are numbered in date order and split into
    odd and even games. For a target sample n, each half is added up game
    by game from its start until it reaches n (n attempts, games,
    possessions...), and the stat is worked out on each half. Player-
    seasons where both halves reach n form the pool; each half is centred
    on its season's pool mean (so league-wide shifts don't count as
    talent) and the two halves are correlated across players: r(n);
  - Spearman-Brown: a sample of size n has reliability r(n) = n / (n + M),
    where M is the sample at which reliability reaches 0.5 (half signal,
    half noise). M is fitted to every target with a pool of 100+ player-
    seasons (weighted by pool size); the 95% interval is a bootstrap over
    player-seasons (300 resamples). M from each target alone is stored too:
    it drifts upward for some stats (FG% most), because the pool at big n
    is only high-volume players, who are more alike;
  - per-game stats (points, rebounds... a game) settle within a game or
    two because minutes and role barely change. So those stats also get a
    per-minute version (count per minute, sample in minutes), which is the
    honest measure of the skill.

Year-to-year correlation (for every stat, and the only measure for BPM,
VORP and impact score, which exist only as season totals): the correlation
between a player's value in consecutive seasons (both with 30+ games and
15+ minutes a game, shooting percentages with the Leaderboard's attempts
floors), centred by season. It's a different thing: it mixes noise with
real change (age, role, team), so it's lower than a split-half number.

Rate stats are rebuilt from play-by-play the way NBA.com defines them;
build_player_game_lines.py's check: season totals within 1.5% of NBA.com's;
usage, assist, rebound and turnover % correlate 0.99+ with NBA.com's,
on-court offensive/defensive/net rating 0.96-0.99 (rebound % runs about 13%
higher: NBA counts team rebounds in the chances). stat_stability.nba_unit_scale is the
ratio of this script's sample units to the season-table units in
api/stat_samples.py, so the page can turn a season total into a
reliability.

Usage:
    cd scripts && python3 build_stat_stability.py
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from scipy.optimize import minimize_scalar

from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from stat_samples import SEASON_SAMPLE_SQL  # noqa: E402

MIN_POOL = 100      # player-seasons a target needs to count in the fit
SHOW_POOL = 50      # ... to be stored on the curve at all
BOOT = 300
TYPICAL_GAMES = 50  # "a typical rotation player's season"
Y2Y_GP, Y2Y_MPG = 30, 15.0
ATTEMPT_FLOORS = {"fg_pct": ("fga", 5.0), "efg_pct": ("fga", 5.0), "ts_pct": ("fga", 5.0),
                  "fg3_pct": ("fg3a", 2.0), "ft_pct": ("fta", 2.0)}

# The Leaderboard catalogue (api/routers/leaderboard.py STATS, minus age;
# the smoke test checks the two match).
CATALOGUE = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min",
             "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct",
             "usg_pct", "ast_pct", "reb_pct", "oreb_pct", "tov_pct",
             "off_rating", "def_rating", "net_rating", "plus_minus",
             "bpm", "obpm", "dbpm", "vorp", "impact_score_raw"]

UNITS = {
    "games": "games", "minutes": "minutes", "fga": "field-goal attempts", "fg3a": "3-point attempts",
    "fta": "free-throw attempts", "tsa": "shooting attempts (FGA + 0.44 × FTA)",
    "reb_ch": "rebound chances", "oreb_ch": "offensive rebound chances",
    "tm_fgm": "teammate field goals", "tm_plays": "team plays", "plays": "own plays", "poss": "possessions",
}
GRIDS = {
    "games": [2, 3, 5, 8, 10, 15, 20, 25, 30, 35],
    "minutes": [50, 100, 200, 300, 500, 700, 1000, 1500],
    "fga": [25, 50, 100, 200, 300, 400, 500, 600],
    "fg3a": [25, 50, 75, 100, 150, 200, 250, 300],
    "fta": [10, 20, 30, 50, 75, 100, 150, 200],
    "tsa": [25, 50, 100, 200, 300, 400, 500],
    "reb_ch": [50, 100, 200, 300, 400, 600, 800],
    "oreb_ch": [25, 50, 100, 150, 200, 300, 400],
    "tm_fgm": [25, 50, 100, 150, 200, 300, 400],
    "tm_plays": [50, 100, 250, 500, 750, 1000, 1500],
    "plays": [25, 50, 100, 200, 300, 400, 500],
    "poss": [250, 500, 750, 1000, 1500, 2000, 2500],
}
COUNTS = {"pts": "pts", "reb": None, "ast": "ast", "stl": "stl", "blk": "blk", "tov": "tov",
          "fg3m": "fg3m", "fg3a": "fg3a", "fta": "fta", "oreb": "oreb"}


def poss(L):
    return ((L.tm_fga + 0.44 * L.tm_fta - L.tm_oreb + L.tm_tov) +
            (L.op_fga + 0.44 * L.op_fta - L.op_oreb + L.op_tov)) / 2


def definitions(L, S):
    """(stat, variant) -> (frame, numerator, denominator, unit, source)."""
    one = pd.Series(1.0, index=L.index)
    mins = L.seconds / 60
    reb = L.oreb + L.dreb
    lines = "player_game_lines"
    shots = "player_shots"
    d = {}
    for k, col in COUNTS.items():
        num = reb if k == "reb" else L[col]
        d[(k, "catalogue")] = (L, num, one, "games", lines)
        d[(k, "per_minute")] = (L, num, mins, "minutes", lines)
    d[("min", "catalogue")] = (L, mins, one, "games", lines)
    d[("plus_minus", "catalogue")] = (L, L.tm_pts - L.op_pts, one, "games", lines)
    d[("fg_pct", "catalogue")] = (S, S.fgm, S.fga, "fga", shots)
    d[("fg3_pct", "catalogue")] = (S, S.fg3m, S.fg3a, "fg3a", shots)
    d[("efg_pct", "catalogue")] = (S, S.fgm + 0.5 * S.fg3m, S.fga, "fga", shots)
    d[("ft_pct", "catalogue")] = (L, L.ftm, L.fta, "fta", lines)
    d[("ts_pct", "catalogue")] = (L, L.pts / 2, L.fga + 0.44 * L.fta, "tsa", lines)
    d[("usg_pct", "catalogue")] = (L, L.fga + 0.44 * L.fta + L.tov, L.tm_fga + 0.44 * L.tm_fta + L.tm_tov,
                                   "tm_plays", lines)
    d[("ast_pct", "catalogue")] = (L, L.ast, L.tm_fgm - L.fgm, "tm_fgm", lines)
    d[("reb_pct", "catalogue")] = (L, reb, L.tm_oreb + L.tm_dreb + L.op_oreb + L.op_dreb, "reb_ch", lines)
    d[("oreb_pct", "catalogue")] = (L, L.oreb, L.tm_oreb + L.op_dreb, "oreb_ch", lines)
    d[("tov_pct", "catalogue")] = (L, L.tov, L.fga + 0.44 * L.fta + L.ast + L.tov, "plays", lines)
    p = poss(L)
    d[("off_rating", "catalogue")] = (L, 100 * L.tm_pts, p, "poss", lines)
    d[("def_rating", "catalogue")] = (L, 100 * L.op_pts, p, "poss", lines)
    d[("net_rating", "catalogue")] = (L, 100 * (L.tm_pts - L.op_pts), p, "poss", lines)
    return d


def halves(df, num, den):
    """Per game: player-season, odd/even half, running totals within the half."""
    h = pd.DataFrame({"p": df.player_id.values, "s": df.season.values,
                      "num": num.values.astype(float), "den": den.values.astype(float)})
    h["par"] = h.groupby(["p", "s"]).cumcount() % 2
    g = h.groupby(["p", "s", "par"])
    h["cn"] = g.num.cumsum()
    h["cd"] = g.den.cumsum()
    return h


def point(h, n):
    """Both halves cut at the first game reaching n: centred values and sizes."""
    x = h[h.cd >= n].groupby(["p", "s", "par"]).first()[["cn", "cd"]].unstack("par").dropna()
    if len(x) < SHOW_POOL:
        return None
    seasons = x.index.get_level_values("s")
    v = []
    for par in (0, 1):
        val = x[("cn", par)] / x[("cd", par)]
        v.append((val - val.groupby(seasons).transform("mean")).to_numpy())
    n_half = float((x[("cd", 0)].mean() + x[("cd", 1)].mean()) / 2)
    return v[0], v[1], n_half


def fit_m(n_half, r, w):
    n_half, r, w = map(np.asarray, (n_half, r, w))

    def loss(logm):
        m = np.exp(logm)
        return float(np.sum(w * (r - n_half / (n_half + m)) ** 2))
    res = minimize_scalar(loss, bounds=(np.log(0.01), np.log(1e6)), method="bounded")
    return float(np.exp(res.x))


def analyse(h, unit, rng):
    pts = []
    for n in GRIDS[unit]:
        p = point(h, n)
        if p is None:
            break
        v0, v1, n_half = p
        r = float(np.corrcoef(v0, v1)[0, 1])
        pts.append({"n": n, "pool": len(v0), "n_half": n_half, "r": r, "v0": v0, "v1": v1,
                    "m": n_half * (1 - r) / r if r > 0 else None})
    used = [p for p in pts if p["pool"] >= MIN_POOL]
    if not used:
        return pts, None
    m = fit_m([p["n_half"] for p in used], [p["r"] for p in used], [p["pool"] for p in used])
    boots = []
    for _ in range(BOOT):
        rs = []
        for p in used:
            idx = rng.integers(0, p["pool"], p["pool"])
            rs.append(np.corrcoef(p["v0"][idx], p["v1"][idx])[0, 1])
        boots.append(fit_m([p["n_half"] for p in used], rs, [p["pool"] for p in used]))
    ms = [p["m"] for p in used if p["m"] is not None]
    return pts, {"m": m, "lo": float(np.percentile(boots, 2.5)), "hi": float(np.percentile(boots, 97.5)),
                 "m_min": min(ms) if ms else None, "m_max": max(ms) if ms else None,
                 "fit_points": len(used)}


def season_scale(conn, stat, h):
    """This script's season sample / the season table's (api/stat_samples.py)."""
    expr = SEASON_SAMPLE_SQL.get(stat)
    if not expr:
        return None
    mine = h.groupby(["p", "s"]).den.sum().rename("mine").reset_index()
    nba = pd.read_sql_query(
        f"SELECT player_id AS p, season AS s, ({expr})::float AS nba FROM player_season_stats "
        f"WHERE gp >= 20 AND season >= %s;", conn, params=(int(mine.s.min()),))
    m = mine.merge(nba, on=["p", "s"]).dropna()
    m = m[m.nba > 0]
    return float(m.mine.sum() / m.nba.sum()) if len(m) else None


def year_to_year(conn):
    cols = sorted(set(CATALOGUE) | {"fga", "fg3a", "fta"})
    df = pd.read_sql_query(
        f"SELECT player_id, season, gp, {', '.join(c for c in cols if c != 'min')}, min FROM player_season_stats "
        "WHERE gp >= %s AND min >= %s;", conn, params=(Y2Y_GP, Y2Y_MPG))
    nxt = df.copy()
    nxt["season"] -= 1
    pairs = df.merge(nxt, on=["player_id", "season"], suffixes=("", "_next"))
    out = []
    for k in CATALOGUE:
        p = pairs[pairs[k].notna() & pairs[f"{k}_next"].notna()]
        floor = ATTEMPT_FLOORS.get(k)
        if floor:
            p = p[(p[floor[0]] >= floor[1]) & (p[f"{floor[0]}_next"] >= floor[1])]
        a = p[k] - p.groupby("season")[k].transform("mean")
        b = p[f"{k}_next"] - p.groupby("season")[f"{k}_next"].transform("mean")
        out.append((k, round(float(np.corrcoef(a, b)[0, 1]), 4), len(p), int(p.season.min()), int(p.season.max()) + 1,
                    f"{Y2Y_GP}+ games and {Y2Y_MPG:g}+ minutes a game in both seasons" +
                    (f", {floor[1]:g}+ {floor[0].upper()} a game" if floor else "")))
    return out


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    L = pd.read_sql_query("SELECT * FROM player_game_lines WHERE seconds > 0 "
                          "ORDER BY player_id, season, game_date, game_id;", conn)
    S = pd.read_sql_query(
        """SELECT player_id, season, game_id, COUNT(*) AS fga, SUM(shot_made_flag) AS fgm,
                  COUNT(*) FILTER (WHERE shot_type LIKE '3PT%%') AS fg3a,
                  COALESCE(SUM(shot_made_flag) FILTER (WHERE shot_type LIKE '3PT%%'), 0) AS fg3m
           FROM player_shots WHERE game_id LIKE '002%%'
           GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;""", conn)
    S["season"] = S.season.str[:4].astype(int) + 1  # '1996-97' -> 1997. Game ids run in schedule order, close enough to date order for an odd/even split
    print(f"{len(L)} player-game lines, {len(S)} player-game shot lines")

    rng = np.random.default_rng(2026)
    rows, curve = [], []
    for (stat, variant), (df, num, den, unit, source) in definitions(L, S).items():
        h = halves(df, num, den)
        pts, fit = analyse(h, unit, rng)
        season_totals = h.groupby(["p", "s"]).agg(games=("den", "size"), den=("den", "sum"))
        typical = float(season_totals[season_totals.games >= TYPICAL_GAMES].den.median())
        scale = season_scale(conn, stat, h) if variant == "catalogue" else None
        m = fit["m"] if fit else None
        rows.append((stat, variant, "split_half", unit, UNITS[unit], source, int(df.season.min()), int(df.season.max()),
                     m, fit and fit["lo"], fit and fit["hi"], fit and fit["m_min"], fit and fit["m_max"],
                     fit and fit["fit_points"], pts[0]["pool"] if pts else 0, typical,
                     typical / (typical + m) if m else None, scale, date.today()))
        for p in pts:
            curve.append((stat, variant, p["n"], p["pool"], p["n_half"], p["r"], p["m"]))
        print(f"  {stat:>16} {variant:<10} M = {m:9.1f} {UNITS[unit]} "
              f"[{fit['lo']:.1f}, {fit['hi']:.1f}]  per-target {fit['m_min']:.1f}-{fit['m_max']:.1f}  "
              f"typical season {typical:.0f} -> {typical / (typical + m):.2f}  scale {scale}")

    y2y = year_to_year(conn)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS stat_stability; DROP TABLE IF EXISTS stat_stability_curve; "
                "DROP TABLE IF EXISTS stat_year_to_year;")
    cur.execute("""
        CREATE TABLE stat_stability (
            stat TEXT, variant TEXT, method TEXT, unit TEXT, unit_label TEXT, source TEXT,
            season_from INTEGER, season_to INTEGER,
            stable_n DOUBLE PRECISION, ci_lo DOUBLE PRECISION, ci_hi DOUBLE PRECISION,
            m_min DOUBLE PRECISION, m_max DOUBLE PRECISION, fit_points INTEGER, pool INTEGER,
            typical_n DOUBLE PRECISION, typical_reliability DOUBLE PRECISION, nba_unit_scale DOUBLE PRECISION,
            built_on DATE, PRIMARY KEY (stat, variant));
        CREATE TABLE stat_stability_curve (
            stat TEXT, variant TEXT, n_target DOUBLE PRECISION, pool INTEGER, n_half DOUBLE PRECISION,
            r_half DOUBLE PRECISION, m_point DOUBLE PRECISION, PRIMARY KEY (stat, variant, n_target));
        CREATE TABLE stat_year_to_year (
            stat TEXT PRIMARY KEY, r DOUBLE PRECISION, pairs INTEGER, season_from INTEGER, season_to INTEGER,
            floors TEXT);""")
    psycopg2.extras.execute_values(cur, "INSERT INTO stat_stability VALUES %s", rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO stat_stability_curve VALUES %s", curve)
    psycopg2.extras.execute_values(cur, "INSERT INTO stat_year_to_year VALUES %s", y2y)
    conn.commit()
    print("year-to-year:")
    for k, r, n, a, b, _f in y2y:
        print(f"  {k:>16} r = {r:.3f}  ({n} pairs, {a}-{b})")
    conn.close()


if __name__ == "__main__":
    main()
