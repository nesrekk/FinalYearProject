"""
paper_beliefs.py
=================
Popular beliefs tested properly (round 5, step 6 of the conference paper):
one framework for the five "is it real or is it noise?" analyses the
platform already shows -- clutch scoring, hot streaks, situational splits,
team luck and referee tendencies -- so that every one of them is reported
the same way: "k of n units survive at FDR 5%, m expected by chance".

The framework
-------------
For every unit (a player, a player-season, a franchise, an official, a
crew) the statistic is the platform's own number for that unit, recomputed
here from the same tables and checked against the stored value. Its null
distribution comes from shuffling THAT UNIT'S OWN DATA in the way that
would hold if the belief were false, so no difference between units in
level, role, minutes or era can produce a false positive:

  clutch    unit = player; statistic = his clutch lift (points per scoring
            chance at equal leverage in clutch time minus the rest, net of
            the league's own clutch shift; compute_wpa.py). Null: the clutch
            labels of his scoring chances are shuffled WITHIN EACH GAME AND
            KIND OF PLAY (free throw, turnover, two-point or three-point
            attempt; the number of clutch chances of each kind in every game
            is kept), after the league shift has been removed from his
            clutch plays so that "no clutch effect beyond the league's" is
            exchangeability. The kind matters: clutch chances are free-throw
            heavy (end-of-game fouling), and a free throw's outcome varies
            far less than a field-goal attempt's, so a shuffle across kinds
            would make the null too wide (checked: 3 of 186 players at
            p < 0.05 against 9 expected, before the strata were added).
            The league shift is taken out PROPORTIONALLY (his clutch plays'
            value divided by the league's clutch-to-other ratio, 0.92), so
            the tested statistic is his clutch rate over that ratio minus
            his other rate; the platform's additive lift is stored beside
            it (platform_stat) and checked against player_wpa_totals.
  streak    unit = player (all his seasons); statistic, per stat and window
            N, = the slope of "next N games minus baseline" on "last N games
            minus baseline" over his windows, exactly as
            build_hot_streak_persistence.py fits it for the league (the
            stored prior-season weight, windows with a prior season).
            Null: the order of his games is shuffled within each season.
            The same shuffles give the null of the LEAGUE slope, which is
            the Miller-Sanjurjo check (below).
  split     unit = player-season; statistic, per split and stat, = his gap
            (side A minus side B) after the season's average-player gap is
            taken out of his side A PROPORTIONALLY (side-A numerators divided
            by 1 + league gap / league side-B rate). Null: his games are
            shuffled between the two sides (side sizes kept). Why
            proportional: the platform's vs_league takes the league gap out
            additively, and for a rate a player barely has (a centre's
            three-point attempts, a guard's blocks) an additive league gap
            (+0.02 attempts per 36 at home) turns his side-A games into
            negative counts that no shuffle of his games can reproduce, so
            every such player hit the p-value floor (68 "survivors", all
            in 3PA and blocks, 25 of the 29 home-3PA ones centres with 0-3
            attempts a season; checked 2026-09-29). A proportional gap
            leaves a zero at zero. The platform's additive vs_league is
            stored beside the statistic (platform_stat) and the raw gap is
            checked against player_situational_splits.diff.
  luck      unit = franchise; statistic = its mean luck per 82 games (wins
            above Pythagorean expectation, team_luck_schedule) over its
            seasons. Null: luck is shuffled across the 30 teams WITHIN EACH
            SEASON (a season's luck values are exchangeable if no franchise
            is systematically lucky). The persistence correlation of luck
            with next season's gets the same null as one global test.
  referee   unit = official (and, separately, a 3-official crew with 2+
            games); statistic = mean over the games worked of (fouls, FTA
            or pace minus that season's league mean) -- referee_tendencies'
            own season-adjusted difference. Null: the games' values are
            shuffled within each season, i.e. the official's games are a
            random draw of the season's games. A rejection says the games
            an official worked are not a random sample of the season; it
            does not say why (assignment policy is one reason).

p-values: two-sided around the null's own mean, p = (1 + #{|T* - m| >=
|T - m|}) / (B + 1) with m the mean of the B draws (Phipson & Smyth 2010:
the +1 keeps a permutation p from being zero). A shuffle that leaves a
streak unit with no windows to fit (a player near the 40-window floor whose
shuffled baseline games fail the 15-minute cut) yields no draw: it counts
in neither B nor the exceedances, and a unit with under 90% of its draws
defined is dropped from the family (counted in paper_beliefs_meta). An
earlier version counted such draws as non-exceedances, which handed about
150 players the floor p-value and 7 of 8 "survivors" for free-throw
percentage over ten games; checked 2026-09-29. Every unit gets B1 draws;
units with p <= STAGE2_P get a fresh B2 draws whose p replaces the first
(a screen-then-confirm design, so that Benjamini-Hochberg at 5% over
hundreds of units can resolve a p below 0.05 / n). Benjamini-Hochberg is
applied within each family (a belief, a stat, a window or a split) over
the units it holds; the "expected by chance" count is 0.05 n, the number
of nominal 5% rejections the global null would give.

The Miller-Sanjurjo check (hot streaks)
----------------------------------------
Miller and Sanjurjo (2018) show that the usual measure of streak
dependence -- the share of successes after a run of successes, computed
within finite sequences -- is biased below the base rate under
independence, because a run of successes uses up successes the rest of the
sequence no longer has. The platform's persistence share is a different
estimator (a regression of the next window's gap on the last window's,
both against a baseline from the earlier games of the same season), and
it has its own finite-sequence bias, in the other direction: the baseline
is estimated from the same finite season, and both gaps subtract that same
noisy baseline, so under the null of no streaks the slope is not zero but
Var(baseline error) / Var(last-window gap), less the negative
without-replacement covariance between the windows. The check measures
it: the mean slope under the within-season shuffle is the null centre,
and the null-centred share (observed minus null mean) is the share of a
run's gap that carries on beyond what regression toward a noisy baseline
produces. Both are stored per stat and window; the paper reports them.

Tables written (dropped and rebuilt; nothing else is touched)
-------------------------------------------------------------
  paper_beliefs           one row per unit and family: the statistic, the
                          null's mean, SD and 2.5/97.5 percentiles, the
                          p-value, the draws it used, the Benjamini-Hochberg
                          q-value and flag, the unit's sample size
  paper_beliefs_summary   one row per family: units, p < 0.05 count, 0.05 n,
                          FDR survivors, the aggregate statistic where there
                          is one (the league slope and its null centre; the
                          persistence correlation), the macro stem the paper
                          prints it with, whether it is a row of Table beliefs
  paper_beliefs_meta      key / value: seeds, draws, the checks against the
                          stored platform tables, runtime
and paper/tables/beliefs.tex (the paper's Table beliefs, every number a \\pn
macro that scripts/paper_numbers.py defines from paper_beliefs_summary).

Checks against the platform (each stops the run if it fails): the league
clutch rates equal wpa_clutch_league; every player's clutch lift equals
player_wpa_totals.clutch_lift; every league slope equals
hot_streak_persistence.slope (and slope_season_only); every split statistic
equals player_situational_splits.vs_league; every official's fouls / FTA
difference equals referee_tendencies'; the luck persistence r equals
luck_schedule_validation's.

Deterministic: every family's generator is seeded from an md5 of its key
and SEED, so a --only rerun reproduces its rows bit for bit. Runtime about
20 minutes (the streak family's shuffles and stage-2 confirmations are most
of it); --perms 200 --perms2 2000 --streak-perms 50 for a quick look.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 paper_beliefs.py
    cd scripts && python3 paper_beliefs.py --only clutch,luck --perms 200 --perms2 2000
Then rerun paper_numbers.py.
"""

import argparse
import hashlib
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))
import hot_streaks as HSDEF  # noqa: E402
import situational_splits as SPDEF  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_TEX = os.path.join(ROOT, "paper", "tables", "beliefs.tex")

SEED = 20260929
PERMS = 2_000            # B1: every unit
PERMS2 = 20_000          # B2: units screened at STAGE2_P
STREAK_PERMS = 2_000     # B1 for the streak family (every draw re-parses every window of every player-season)
STAGE2_P = 0.02
FDR_Q = 0.05
CHUNK = 250              # draws per block for the matrix shuffles
CLUTCH_FLOORS = (50, 100)
CLUTCH_STORE_FLOOR = 50  # players stored
REF_FLOOR = 25           # referee_tendencies' own small-sample line
CREW_FLOOR = 2           # a crew with one game has nothing to test
STREAK_MIN_WINDOWS = 40  # about one season of windows for a per-player streak test
MIN_FINITE_SHARE = 0.9   # a per-player streak test needs this share of its shuffles to leave windows to fit
FAMILIES = ("clutch", "streak", "split", "luck", "referee")

# Rows of the paper's Table beliefs: (family, key, macro stem, row label, unit label).
TABLE_ROWS = [
    ("clutch", "clutch:100", "Clutch", "Clutch scoring (100+ clutch chances)", "player"),
    ("streak", "streak:pts:10", "StreakPtsTen", "Hot streak, points, last 10 games", "player"),
    ("streak", "streak:fg3_pct:10", "StreakThreeTen", "Hot streak, 3P\\%, last 10 games", "player"),
    ("streak", "streak:ts_pct:10", "StreakTsTen", "Hot streak, TS\\%, last 10 games", "player"),
    ("split", "split:home:pts", "SplitHomePts", "Home vs.\\ away, points per 36", "player-season"),
    ("split", "split:opp:ts_pct", "SplitOppTs", "Top-10 vs.\\ bottom-10 opponents, TS\\%", "player-season"),
    ("split", "split:rest:min", "SplitRestMin", "Back-to-back vs.\\ rested, minutes", "player-season"),
    ("split", "split:travel:pts", "SplitTravelPts", "Long trip vs.\\ short, points per 36", "player-season"),
    ("luck", "luck:luck_per82", "Luck", "Team luck (wins above expected)", "franchise"),
    ("referee", "referee:official:fouls", "RefFouls", "Referee: fouls per game (25+ games)", "official"),
    ("referee", "referee:official:fta", "RefFta", "Referee: free throws per game (25+ games)", "official"),
    ("referee", "referee:crew:fouls", "CrewFouls", "Referee crew: fouls per game (2+ games)", "crew"),
]

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


# ------------------------------------------------------------------ helpers

def rng_for(*key):
    h = hashlib.md5(("|".join(str(k) for k in key) + f"|{SEED}").encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def perm_p(obs, draws):
    """Two-sided permutation p around the null mean, +1 corrected (Phipson & Smyth). A draw that is not a
    number (a shuffle that left a unit with no windows to fit) is not a draw: it counts in neither B nor r."""
    draws = np.asarray(draws, float)
    finite = np.isfinite(draws)
    m = float(draws[finite].mean())
    r = int(np.sum(np.abs(draws[finite] - m) >= np.abs(obs - m) - 1e-12))
    return (1 + r) / (int(finite.sum()) + 1), m


def perm_p_matrix(obs, draws):
    """perm_p for many units at once: obs (U,), draws (B, U) -> (p (U,), mean (U,), finite draws (U,))."""
    finite = np.isfinite(draws)
    nf = finite.sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        m = np.nanmean(draws, axis=0)
        r = np.nansum(np.abs(draws - m) >= np.abs(obs - m) - 1e-12, axis=0)   # a NaN comparison is False
    return (1 + r) / (nf + 1), m, nf


def null_summary(draws, axis=0):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return (np.nanmean(draws, axis=axis), np.nanstd(draws, axis=axis, ddof=1),
                np.nanpercentile(draws, 2.5, axis=axis), np.nanpercentile(draws, 97.5, axis=axis))


def bh(p, q=FDR_Q):
    """Benjamini-Hochberg: (reject flags, adjusted p) for one family."""
    p = np.asarray(p, float)
    n = len(p)
    if n == 0:
        return np.zeros(0, bool), np.zeros(0)
    order = np.argsort(p)
    ranked = p[order]
    ranks = np.arange(1, n + 1)
    adj = np.minimum.accumulate((ranked * n / ranks)[::-1])[::-1]
    adj = np.minimum(adj, 1.0)
    below = np.nonzero(ranked <= q * ranks / n)[0]
    reject = np.zeros(n, bool)
    if len(below):
        reject[order[:below[-1] + 1]] = True
    qv = np.empty(n)
    qv[order] = adj
    return reject, qv


def row_perms(rng, lengths, L, B):
    """B within-row permutations of a (P, L) padded matrix: entry (b, i, :) is a
    permutation of 0..L-1 whose first lengths[i] positions are a random order of
    the row's real entries (the padding stays at the end)."""
    keys = rng.random((B, len(lengths), L))
    keys[:, np.arange(L)[None, :] >= np.asarray(lengths)[:, None]] = 2.0
    return np.argsort(keys, axis=2)


class Out:
    """Collects per-unit rows, per-family summaries and meta values."""

    def __init__(self):
        self.units, self.summary, self.meta = [], [], []

    def unit(self, family, key, unit_id, unit_name, season, n_obs, stat, null, p, n_perm, platform=None):
        m, sd, lo, hi = null
        self.units.append([family, key, str(unit_id), unit_name, int(season), int(n_obs), float(stat), float(m),
                           float(sd), float(lo), float(hi), float(p), int(n_perm), None, None,
                           float(stat if platform is None else platform)])

    def family(self, family, key, label, unit_kind, floor, note, agg=None, perms=(PERMS, PERMS2)):
        rows = [r for r in self.units if r[0] == family and r[1] == key]
        p = np.array([r[11] for r in rows])
        reject, qv = bh(p)
        for r, rj, q in zip(rows, reject, qv):
            r[13], r[14] = bool(rj), float(q)
        agg = agg or {}
        macro = next((m for f, k, m, _l, _u in TABLE_ROWS if f == family and k == key), None)
        self.summary.append((family, key, label, unit_kind, floor, len(rows), int((p < 0.05).sum()), 0.05 * len(rows),
                             int(reject.sum()), perms[0], perms[1], agg.get("obs"), agg.get("null_mean"),
                             agg.get("null_lo"), agg.get("null_hi"), agg.get("p"), agg.get("adjusted"),
                             agg.get("n"), macro, macro is not None, note))
        log(f"  {key}: n={len(rows)} p<0.05: {int((p < 0.05).sum())} (exp {0.05 * len(rows):.1f}) FDR: {int(reject.sum())}"
            + (f" | agg {agg['obs']:.4f} null {agg['null_mean']:.4f} [{agg['null_lo']:.4f}, {agg['null_hi']:.4f}] p {agg['p']:.3g}"
               if agg else ""))

    def add_meta(self, key, value, note=""):
        self.meta.append((key, None if value is None else float(value), note))


def check(ok, what):
    if not ok:
        raise SystemExit(f"CHECK FAILED: {what}")


# ------------------------------------------------------------------ clutch

def clutch(conn, out, perms, perms2):
    import compute_wpa as W
    log("clutch: loading play-by-play and scoring the win-probability model")
    model, scaler = W.load_model()
    df = W.load_events(conn)
    df["margin"] = df["score_home"] - df["score_away"]
    df["wp_home"] = W.compute_win_probs(model, scaler, df["seconds_remaining"].values, df["margin"].values)
    tipoff_wp = float(W.win_prob(model, scaler, 2880.0, 0))
    df["prev_wp_home"] = df.groupby("game_id")["wp_home"].shift(1).fillna(tipoff_wp)
    df["prev_margin"] = df.groupby("game_id")["margin"].shift(1).fillna(0)
    is_home = df["team_tricode"] == df["home_team"]
    df["delta"] = np.where(is_home, df["wp_home"] - df["prev_wp_home"], df["prev_wp_home"] - df["wp_home"])
    df["is_clutch"] = ((df["period"] >= 4) & (df["seconds_remaining"] <= W.CLUTCH_SECONDS)
                       & (df["prev_margin"].abs() <= W.CLUTCH_MARGIN))
    secs, pm = df["seconds_remaining"].values, df["prev_margin"].values
    df["lev"] = (W.compute_win_probs(model, scaler, secs, pm + 2) - W.compute_win_probs(model, scaler, secs, pm - 2)) / 4
    att = df[df["person_id"].notna() & df["player_name"].notna() & df["team_tricode"].notna()]
    ch = att[W.is_scoring_chance(att["action_type"])][["id", "person_id", "player_name", "game_id", "is_clutch", "delta", "lev",
                                                       "action_type"]].copy()
    del df, att
    # Play kind, the stratum of the shuffle: free throw / turnover / two-point / three-point attempt (the text's call,
    # as pbp_lineups reads it: "three point", or 23+ feet when no type is written).
    at = ch["action_type"].fillna("")
    kind = np.where(at.str.startswith("Free Throw"), 0, np.where(at.str.contains("Turnover") | (at == "Traveling"), 1, 2))
    three = pd.read_sql("""SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
                           WHERE """ + W.PBP_DEDUP_WHERE + """ AND e.person_id IS NOT NULL
                             AND e.action_type ~ 'Shot|Layup|Dunk|Hook'
                             AND (e.description ILIKE '%%three point%%'
                                  OR COALESCE(substring(e.description FROM '(\\d+)-foot')::int, 0) >= 23)""", conn)
    kind = np.where((kind == 2) & ch["id"].isin(three["id"]).to_numpy(), 3, kind)
    ch["kind"] = kind
    del three
    n_games = ch["game_id"].nunique()
    league = {c: g["delta"].sum() / g["lev"].sum() for c, g in ch.groupby("is_clutch")}
    shift = float(league[True] - league[False])
    cur = conn.cursor()
    cur.execute("SELECT clutch_pts_rate, nonclutch_pts_rate, clutch_chances, nonclutch_chances FROM wpa_clutch_league")
    lc, ln, nc, nn = cur.fetchone()
    check(abs(lc - league[True]) < 1e-9 and abs(ln - league[False]) < 1e-9 and nc == int(ch["is_clutch"].sum())
          and nn == int((~ch["is_clutch"]).sum()), "league clutch rates equal wpa_clutch_league (rerun compute_wpa.py?)")
    out.add_meta("clutch_league_shift", shift, "league clutch minus non-clutch points per chance at equal leverage")
    out.add_meta("clutch_scoring_chances", len(ch), "scoring chances with a player attributed")
    out.add_meta("clutch_games", n_games)
    ratio = float(league[True] / league[False])                 # the average player's clutch rate as a share of his other rate
    out.add_meta("clutch_league_ratio", ratio, "league clutch rate / non-clutch rate (the proportional shift taken out)")
    ch["delta_adj"] = np.where(ch["is_clutch"], ch["delta"] / ratio, ch["delta"])
    cur.execute("SELECT person_id, clutch_lift, clutch_chances FROM player_wpa_totals WHERE clutch_chances >= %s", (CLUTCH_STORE_FLOOR,))
    stored = {pid: (lift, n) for pid, lift, n in cur.fetchall()}
    counts = ch.groupby("person_id")["is_clutch"].sum()
    players = sorted(int(p) for p, n in counts.items() if n >= CLUTCH_STORE_FLOOR)
    check(set(players) == set(stored), "the players with 50+ clutch chances are player_wpa_totals'")
    log(f"clutch: {len(ch):,} scoring chances in {n_games:,} games; {len(players)} players with {CLUTCH_STORE_FLOOR}+ clutch chances")
    ch = ch.sort_values(["person_id", "game_id", "kind"], kind="stable")   # positions ordered by stratum (game, kind)
    groups = {int(p): g for p, g in ch.groupby("person_id") if int(p) in stored}
    out.add_meta("clutch_three_point_chances", int((ch["kind"] == 3).sum()), "scoring chances read as three-point attempts")
    max_dev = 0.0
    rows = []
    for pid in players:
        g = groups[pid]
        gcode = pd.factorize(g["game_id"])[0] * 4 + g["kind"].to_numpy()   # the stratum: this game, this kind of play
        c = g["is_clutch"].to_numpy(float)
        d, lev = g["delta_adj"].to_numpy(float), g["lev"].to_numpy(float)
        td, tl = d.sum(), lev.sum()
        raw = g["delta"].to_numpy(float)
        platform = float((c @ raw) / (c @ lev) - ((1 - c) @ raw) / ((1 - c) @ lev) - shift)   # the platform's additive lift
        max_dev = max(max_dev, abs(platform - stored[pid][0]))

        def stat(cm):
            dc, lc_ = cm @ d, cm @ lev
            return dc / lc_ - (td - dc) / (tl - lc_)
        obs = float(stat(c[None, :])[0])

        def draws(rng, B):
            res = []
            for start in range(0, B, CHUNK):
                b = min(CHUNK, B - start)
                keys = gcode[None, :] + rng.random((b, len(c)))
                perm = np.argsort(keys, axis=1)
                res.append(stat(c[perm]))
            return np.concatenate(res)
        rng = rng_for("clutch", pid)
        dr = draws(rng, perms)
        p, _ = perm_p(obs, dr)
        n_perm = perms
        if p <= STAGE2_P:
            dr = draws(rng_for("clutch", pid, "stage2"), perms2)
            p, _ = perm_p(obs, dr)
            n_perm = perms2
        rows.append((pid, g["player_name"].iloc[-1], int(c.sum()), len(c), obs, null_summary(dr), p, n_perm, platform))
    check(max_dev <= 5.1e-5, f"every clutch lift equals player_wpa_totals.clutch_lift to its 4 decimals (max dev {max_dev:.2e})")
    out.add_meta("clutch_max_dev_vs_stored", max_dev, "max |recomputed - stored| clutch lift (stored has 4 decimals)")
    for floor in CLUTCH_FLOORS:
        for pid, name, ncl, n, obs, null, p, n_perm, platform in rows:
            if ncl >= floor:
                out.unit("clutch", f"clutch:{floor}", pid, name, 0, ncl, obs, null, p, n_perm, platform)
        out.family("clutch", f"clutch:{floor}", f"Clutch scoring lift, {floor}+ clutch chances", "player", floor,
                   "null: clutch labels shuffled within each of his games and kind of play, the league's proportional clutch shift "
                   "taken out of his clutch plays first; platform_stat = player_wpa_totals.clutch_lift (additive)", perms=(perms, perms2))


# ------------------------------------------------------------------ hot streaks

def streak(conn, out, perms, perms2):
    import build_hot_streak_persistence as HS
    log("streak: loading player game lines and previous seasons")
    lines, prior = HS.load(conn)
    lines = HSDEF.add_columns(lines)
    key = lines["player_id"].astype(str) + "_" + lines["season"].astype(str)
    codes, uniq = pd.factorize(key)              # rows in LINES_SQL order (player, season, date, game)
    pos = lines.groupby(codes).cumcount().to_numpy()
    P, L = len(uniq), int(pos.max()) + 1
    lengths = np.bincount(codes, minlength=P)
    row_pid = lines.groupby(codes)["player_id"].first().to_numpy().astype(int)
    row_season = lines.groupby(codes)["season"].first().to_numpy().astype(int)
    cols = sorted({c for _l, a, b, _f, _lo in HSDEF.STATS.values() for c in (a, b)} | {"min"})
    M = {}
    for c in cols:
        m = np.zeros((P, L))
        m[codes, pos] = lines[c].to_numpy(float)
        M[c] = m
    # Prior season per row and stat: (has_prior, numerator, denominator, gp).
    prior_n = {s: np.full(P, np.nan) for s in HSDEF.STATS}
    prior_d = {s: np.full(P, np.nan) for s in HSDEF.STATS}
    gp = np.zeros(P)
    for i in range(P):
        p = prior.get((int(row_pid[i]), int(row_season[i])))
        if p is None:
            continue
        gp[i] = p.gp
        for s in HSDEF.STATS:
            pn, pdn = getattr(p, f"{s}_n"), getattr(p, f"{s}_d")
            if pn is not None and pdn is not None and not np.isnan(pdn) and pdn > 0:
                prior_n[s][i], prior_d[s][i] = pn, pdn
    cur = conn.cursor()
    cur.execute("SELECT stat, window_games, prior_games, slope, slope_season_only FROM hot_streak_persistence")
    stored = {(s, n): (w, sl, sl0) for s, n, w, sl, sl0 in cur.fetchall()}
    check(set(stored) == {(s, n) for s in HSDEF.STATS for n in HSDEF.WINDOWS}, "hot_streak_persistence covers the catalogue")
    pcodes, puniq = pd.factorize(row_pid)        # row -> player
    NP = len(puniq)
    ident = np.tile(np.arange(L), (P, 1))
    has_prior = {s: ~np.isnan(prior_d[s]) for s in HSDEF.STATS}
    prior_n = {s: np.where(has_prior[s], prior_n[s], 0.0) for s in HSDEF.STATS}
    prior_d = {s: np.where(has_prior[s], prior_d[s], 0.0) for s in HSDEF.STATS}
    share = {}
    for stat, N in [(s, n) for s in HSDEF.STATS for n in HSDEF.WINDOWS]:
        w = stored[(stat, N)][0]
        share[(stat, N)] = np.where(has_prior[stat] & (gp >= HSDEF.MIN_PRIOR_GAMES),
                                    np.minimum(1.0, w / np.where(gp > 0, gp, 1)), 0.0)

    def cums(perm, rows, which=None):
        """Cumulative sums (with a leading zero) of the columns (all, or `which`) for the given rows in the order perm gives."""
        idx = rows[:, None]
        return {c: np.concatenate([np.zeros((len(rows), 1)), np.cumsum(M[c][idx, perm], axis=1)], axis=1)
                for c in (cols if which is None else which)}

    def windows(C, rows, stat, N):
        """Per (row, t) pair: D and F against the prior-weighted baseline (and the season-only one), with masks."""
        _label, a, b, _fmt, lo = HSDEF.STATS[stat]
        t = np.arange(HSDEF.MIN_BASE_GAMES + N, L - N + 1)
        s0 = t - N
        cn, cd, cm = C[a], C[b], C["min"]
        bn, bd = cn[:, s0], cd[:, s0]
        wn, wd = cn[:, t] - cn[:, s0], cd[:, t] - cd[:, s0]
        fn, fd = cn[:, t + N] - cn[:, t], cd[:, t + N] - cd[:, t]
        ok = (t + N <= lengths[rows][:, None]) & (cm[:, s0] / s0 >= HSDEF.MIN_BASE_MPG) & (bd > 0) & (wd > 0) & (fd > 0)
        if lo is not None:
            ok &= (bd >= lo * s0) & (wd >= lo * N)
        sh = share[(stat, N)][rows][:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            base = (bn + sh * prior_n[stat][rows][:, None]) / (bd + sh * prior_d[stat][rows][:, None])
            base0 = bn / bd
            D, F = wn / wd - base, fn / fd - base
            D0, F0 = wn / wd - base0, fn / fd - base0
        return ok & has_prior[stat][rows][:, None], D, F, ok, D0, F0

    def slope(D, F, ok):
        d, f = D[ok], F[ok]
        dm = d - d.mean()
        return float((dm * (f - f.mean())).sum() / (dm ** 2).sum())

    def player_sums(D, F, ok, rows):
        """Per player: n, sum D, sum F, sum D^2, sum DF over his windows (rows = the row subset D/F cover)."""
        r = np.nonzero(ok)[0]
        d, f = D[ok], F[ok]
        pc = pcodes[rows[r]]
        return np.stack([np.bincount(pc, minlength=NP), np.bincount(pc, weights=d, minlength=NP),
                         np.bincount(pc, weights=f, minlength=NP), np.bincount(pc, weights=d * d, minlength=NP),
                         np.bincount(pc, weights=d * f, minlength=NP)])

    def player_slopes(S):
        n, sd, sf, sdd, sdf = S
        with np.errstate(divide="ignore", invalid="ignore"):
            return (sdf - sd * sf / n) / (sdd - sd * sd / n)

    all_rows = np.arange(P)
    combos = [(s, n) for s in HSDEF.STATS for n in HSDEF.WINDOWS]
    obs, elig = {}, {}
    max_dev = 0.0
    C = cums(ident, all_rows)
    for stat, N in combos:
        okp, D, F, ok, D0, F0 = windows(C, all_rows, stat, N)
        sl, sl0 = slope(D, F, okp), slope(D0, F0, ok)
        w, st_sl, st_sl0 = stored[(stat, N)]
        max_dev = max(max_dev, abs(sl - st_sl), abs(sl0 - st_sl0))
        S = player_sums(D, F, okp, all_rows)
        ps = player_slopes(S)
        obs[(stat, N)] = (sl, sl0, S, ps, int(okp.sum()), int(ok.sum()))
        # Per player: windows with a prior season, at least STREAK_MIN_WINDOWS of them.
        elig[(stat, N)] = np.nonzero((S[0] >= STREAK_MIN_WINDOWS) & np.isfinite(ps))[0]
    check(max_dev < 1e-6, f"every league slope equals hot_streak_persistence's (max dev {max_dev:.2e})")
    out.add_meta("streak_max_dev_vs_stored", max_dev, "max |recomputed - stored| league slope")
    out.add_meta("streak_player_seasons", P)
    log(f"streak: {P:,} player-seasons, {NP:,} players, slopes match the stored table; {perms} shuffles of game order")

    rng = rng_for("streak")
    null_sl = {c: np.empty(perms) for c in combos}
    null_sl0 = {c: np.empty(perms) for c in combos}
    null_ps = {c: np.empty((perms, len(elig[c]))) for c in combos}
    for start in range(0, perms, 25):
        b = min(25, perms - start)
        perms_blk = row_perms(rng, lengths, L, b)
        for i in range(b):
            Cp = cums(perms_blk[i], all_rows)
            for stat, N in combos:
                okp, D, F, ok, D0, F0 = windows(Cp, all_rows, stat, N)
                null_sl[(stat, N)][start + i] = slope(D, F, okp)
                null_sl0[(stat, N)][start + i] = slope(D0, F0, ok)
                null_ps[(stat, N)][start + i] = player_slopes(player_sums(D, F, okp, all_rows))[elig[(stat, N)]]
        if (start // 25) % 8 == 0:
            log(f"  streak shuffles {start + b}/{perms}")

    names = dict(pd.read_sql("SELECT player_id, player_name FROM player_bio", conn).itertuples(index=False))
    dropped = {}
    for stat, N in combos:
        sl, sl0, S, pslopes, n_win, n_win0 = obs[(stat, N)]
        dr, dr0 = null_sl[(stat, N)], null_sl0[(stat, N)]
        p, m = perm_p(sl, dr)
        p0, m0 = perm_p(sl0, dr0)
        e = elig[(stat, N)]
        PS = null_ps[(stat, N)]                        # (B, len(e))
        n_p = S[0]
        pp, _, nf = perm_p_matrix(pslopes[e], PS)
        nulls = list(null_summary(PS))
        n_perm = nf.copy()
        flagged = np.nonzero(pp <= STAGE2_P)[0]
        if len(flagged):
            sub_players = e[flagged]
            sub_rows = np.nonzero(np.isin(pcodes, sub_players))[0]
            rng2 = rng_for("streak", stat, N, "stage2")
            need = {HSDEF.STATS[stat][1], HSDEF.STATS[stat][2], "min"}       # only this stat's columns
            dr2 = np.empty((perms2, len(sub_players)))
            for start in range(0, perms2, 50):
                b = min(50, perms2 - start)
                blk = row_perms(rng2, lengths[sub_rows], L, b)
                for i in range(b):
                    okp, D, F, _ok, _D0, _F0 = windows(cums(blk[i], sub_rows, need), sub_rows, stat, N)
                    dr2[start + i] = player_slopes(player_sums(D, F, okp, sub_rows))[sub_players]
            p2, _, nf2 = perm_p_matrix(pslopes[sub_players], dr2)
            pp[flagged] = p2
            n_perm[flagged] = nf2
            ns2 = null_summary(dr2)
            for j, f in enumerate(flagged):
                for k in range(4):
                    nulls[k][f] = ns2[k][j]
        # A unit whose shuffles left fewer than MIN_FINITE_SHARE of the draws defined has no null to test against.
        untestable = n_perm < MIN_FINITE_SHARE * np.where(np.isin(np.arange(len(e)), flagged), perms2, perms)
        dropped[(stat, N)] = int(untestable.sum())
        for j, pi in enumerate(e):
            if untestable[j]:
                continue
            pid = int(puniq[pi])
            out.unit("streak", f"streak:{stat}:{N}", pid, names.get(pid, str(pid)), 0, int(n_p[pi]), pslopes[pi],
                     tuple(x[j] for x in nulls), pp[j], int(n_perm[j]))
        agg = {"obs": sl, "null_mean": m, "null_lo": float(np.percentile(dr, 2.5)), "null_hi": float(np.percentile(dr, 97.5)),
               "p": p, "adjusted": sl - m, "n": n_win}
        out.family("streak", f"streak:{stat}:{N}", f"Hot streak persistence, {HSDEF.STATS[stat][0]}, last {N} games",
                   "player", STREAK_MIN_WINDOWS,
                   f"null: game order shuffled within each season; prior-season weight {stored[(stat, N)][0]} games "
                   f"(hot_streak_persistence); aggregate = league slope (windows with a prior season); "
                   f"{dropped[(stat, N)]} eligible players not tested (shuffles left under {MIN_FINITE_SHARE:.0%} of draws defined)",
                   agg=agg, perms=(perms, perms2))
        agg0 = {"obs": sl0, "null_mean": m0, "null_lo": float(np.percentile(dr0, 2.5)), "null_hi": float(np.percentile(dr0, 97.5)),
                "p": p0, "adjusted": sl0 - m0, "n": n_win0}
        out.family("streak", f"streak0:{stat}:{N}", f"Hot streak persistence, season-only baseline, {HSDEF.STATS[stat][0]}, last {N} games",
                   "league", 0, "aggregate only: the league slope with the season-only baseline (all windows)",
                   agg=agg0, perms=(perms, 0))
    out.add_meta("streak_players_untestable", sum(dropped.values()),
                 "eligible (player, stat, window) tests dropped because shuffles left under MIN_FINITE_SHARE of the draws defined")


# ------------------------------------------------------------------ situational splits

def split(conn, out, perms, perms2):
    log("split: loading player game lines with the schedule")
    lines = SPDEF.add_columns(pd.read_sql(SPDEF.LINES_SQL, conn))
    stored = pd.read_sql("""SELECT player_id, season, split, stat, diff, vs_league, league_diff, games_a, games_b
                            FROM player_situational_splits WHERE qualified""", conn)
    league = pd.read_sql("SELECT season, split, stat, league_diff, league_value_b FROM situational_split_league WHERE season > 0", conn)
    # The average player's gap as a share of his side-B rate: the proportional effect taken out of every player's side A.
    league = {(r.split, r.stat, int(r.season)): (r.league_diff / r.league_value_b if r.league_value_b else 0.0)
              for r in league.itertuples()}
    names = dict(pd.read_sql("SELECT player_id, player_name FROM player_bio", conn).itertuples(index=False))
    max_dev = 0.0
    for sp, (_label, _a, _b, cond_a, cond_b) in SPDEF.SPLITS.items():
        mask_a, mask_b = lines.eval(cond_a), lines.eval(cond_b)
        sub = lines[mask_a | mask_b].copy()
        sub["side_a"] = mask_a[mask_a | mask_b].to_numpy()
        # Rows = player-seasons with 10+ games a side; A games first, then B.
        cnt = sub.groupby(["player_id", "season"])["side_a"].agg(["sum", "size"])
        keep = cnt[(cnt["sum"] >= SPDEF.MIN_GAMES) & (cnt["size"] - cnt["sum"] >= SPDEF.MIN_GAMES)].index
        sub = sub.set_index(["player_id", "season"]).loc[keep].reset_index()
        sub = sub.sort_values(["player_id", "season", "side_a"], ascending=[True, True, False], kind="stable")
        codes, uniq = pd.factorize(sub["player_id"].astype(int).astype(str) + "_" + sub["season"].astype(int).astype(str))
        pos = sub.groupby(codes).cumcount().to_numpy()
        P, L = len(uniq), int(pos.max()) + 1
        n_all = np.bincount(codes, minlength=P)
        n_a = np.bincount(codes, weights=sub["side_a"].to_numpy(float), minlength=P).astype(int)
        row_pid = sub.groupby(codes)["player_id"].first().to_numpy().astype(int)
        row_season = sub.groupby(codes)["season"].first().to_numpy().astype(int)
        cols = sorted({c for _l, num, den, _s, _f, _lo in SPDEF.STATS.values() for c in (num, den)})
        M = {}
        for c in cols:
            m = np.zeros((P, L))
            m[codes, pos] = sub[c].to_numpy(float)
            M[c] = m
        maskA = np.arange(L)[None, :] < n_a[:, None]
        rng = rng_for("split", sp)
        st_sp = stored[stored["split"] == sp]
        log(f"split {sp}: {P:,} player-seasons with {SPDEF.MIN_GAMES}+ games a side")
        # One shuffle of the games serves every stat.
        stats = list(SPDEF.STATS)
        prepared, elig = {}, {}
        for stat in stats:
            _label, num, den, scale, _fmt, _lo = SPDEF.STATS[stat]
            g = np.array([league.get((sp, stat, int(s)), np.nan) for s in row_season])
            num_adj = np.where(maskA, M[num] / (1.0 + g)[:, None], M[num])   # the league's proportional gap out of the side-A games
            q = st_sp[st_sp["stat"] == stat].set_index(["player_id", "season"])
            elig[stat] = np.nonzero([(int(p), int(s)) in q.index for p, s in zip(row_pid, row_season)])[0]
            prepared[stat] = (num_adj, M[den], scale, q)

        def stat_matrix(perm, rows, stat, raw=False):
            """The statistic for the given rows, with each row's games in the order perm gives (raw: no league adjustment)."""
            num_adj, den, scale, _q = prepared[stat]
            num = M[SPDEF.STATS[stat][1]] if raw else num_adj
            idx = rows[:, None]
            na = (num[idx, perm] * maskA[rows]).sum(axis=1)
            da = (den[idx, perm] * maskA[rows]).sum(axis=1)
            tn, td = num[rows].sum(axis=1), den[rows].sum(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                return scale * (na / da - (tn - na) / (td - da))
        ident = np.tile(np.arange(L), (P, 1))
        all_rows = np.arange(P)
        obs = {stat: stat_matrix(ident, all_rows, stat) for stat in stats}
        platform = {}
        for stat in stats:
            e, q = elig[stat], prepared[stat][3]
            idx = pd.MultiIndex.from_arrays([row_pid[e], row_season[e]])
            mine = pd.Series(stat_matrix(ident, all_rows, stat, raw=True)[e], index=idx)
            dev = (mine - q["diff"].reindex(idx)).abs().max()
            check(len(mine) == len(q) and np.isfinite(dev) and dev < 1e-9,
                  f"split {sp}/{stat}: recomputed gaps equal player_situational_splits.diff ({len(mine)} vs {len(q)}, dev {dev})")
            max_dev = max(max_dev, float(dev))
            platform[stat] = q["vs_league"].reindex(idx).to_numpy()
        draws = {stat: np.empty((perms, len(elig[stat]))) for stat in stats}
        for start in range(0, perms, CHUNK):
            b = min(CHUNK, perms - start)
            blk = row_perms(rng, n_all, L, b)
            for i in range(b):
                for stat in stats:
                    draws[stat][start + i] = stat_matrix(blk[i], all_rows, stat)[elig[stat]]
        for stat in stats:
            e = elig[stat]
            D = draws[stat]
            pp, _, nf = perm_p_matrix(obs[stat][e], D)
            check(bool((nf == perms).all()), f"split {sp}/{stat}: every draw is defined")
            nulls = list(null_summary(D))
            n_perm = np.full(len(e), perms)
            flagged = np.nonzero(pp <= STAGE2_P)[0]
            if len(flagged):
                sub_rows = e[flagged]
                rng2 = rng_for("split", sp, stat, "stage2")
                dr2 = np.empty((perms2, len(sub_rows)))
                for start in range(0, perms2, CHUNK):
                    b = min(CHUNK, perms2 - start)
                    blk = row_perms(rng2, n_all[sub_rows], L, b)
                    for i in range(b):
                        dr2[start + i] = stat_matrix(blk[i], sub_rows, stat)
                p2, _, _nf2 = perm_p_matrix(obs[stat][sub_rows], dr2)
                pp[flagged] = p2
                n_perm[flagged] = perms2
                ns2 = null_summary(dr2)
                for j, f in enumerate(flagged):
                    for k in range(4):
                        nulls[k][f] = ns2[k][j]
            for j, r in enumerate(e):
                pid = int(row_pid[r])
                out.unit("split", f"split:{sp}:{stat}", f"{pid}_{row_season[r]}", names.get(pid, str(pid)), int(row_season[r]),
                         int(n_all[r]), obs[stat][r], tuple(x[j] for x in nulls), pp[j], int(n_perm[j]), platform[stat][j])
            out.family("split", f"split:{sp}:{stat}", f"{SPDEF.SPLITS[sp][0]}, {SPDEF.STATS[stat][0]}", "player-season",
                       SPDEF.MIN_GAMES, "null: his games shuffled between the two sides within the season, the season's proportional "
                       "league gap taken out of his side-A games first; platform_stat = player_situational_splits.vs_league (additive)",
                       perms=(perms, perms2))
    out.add_meta("split_max_dev_vs_stored", max_dev, "max |recomputed - stored| raw gap (player_situational_splits.diff)")


# ------------------------------------------------------------------ team luck

def luck(conn, out, perms2):
    ts = pd.read_sql("SELECT season, franchise, luck_per82 FROM team_luck_schedule ORDER BY season, franchise", conn)
    W = ts.pivot(index="season", columns="franchise", values="luck_per82")
    check(W.notna().all().all() and W.shape[1] == 30, "every franchise has a luck value in every season")
    X = W.to_numpy()                                     # (seasons, 30)
    S, K = X.shape
    obs = X.mean(axis=0)
    pairs = np.stack([X[:-1].ravel(), X[1:].ravel()], axis=1)  # franchise-season pairs (consecutive seasons)
    r_obs = float(np.corrcoef(pairs[:, 0], pairs[:, 1])[0, 1])
    cur = conn.cursor()
    cur.execute("SELECT value, n FROM luck_schedule_validation WHERE metric = 'luck_next_luck_r'")
    r_st, n_st = cur.fetchone()
    check(abs(r_obs - r_st) < 1e-9 and n_st == len(pairs), f"luck persistence r equals luck_schedule_validation's ({r_obs} vs {r_st})")
    between_obs = float(obs.var(ddof=1))
    rng = rng_for("luck")
    B = perms2
    means = np.empty((B, K))
    rs = np.empty(B)
    between = np.empty(B)
    for b in range(B):
        Xp = np.take_along_axis(X, np.argsort(rng.random((S, K)), axis=1), axis=1)
        means[b] = Xp.mean(axis=0)
        rs[b] = np.corrcoef(Xp[:-1].ravel(), Xp[1:].ravel())[0, 1]
        between[b] = means[b].var(ddof=1)
    pp, _, _nf = perm_p_matrix(obs, means)
    nulls = null_summary(means)
    for j, fr in enumerate(W.columns):
        out.unit("luck", "luck:luck_per82", fr, fr, 0, S, obs[j], tuple(x[j] for x in nulls), pp[j], B)
    p_r, m_r = perm_p(r_obs, rs)
    p_b, m_b = perm_p(between_obs, between)
    out.add_meta("luck_between_var_obs", between_obs, "variance of franchise mean luck per 82")
    out.add_meta("luck_between_var_p", p_b, "permutation p of that variance (luck shuffled across teams within each season)")
    out.add_meta("luck_between_var_null_mean", m_b)
    out.family("luck", "luck:luck_per82", "Team luck (wins above Pythagorean expectation per 82 games)", "franchise", 0,
               "null: luck shuffled across the 30 teams within each season; aggregate = correlation of a franchise's luck "
               "with its luck the next season (luck_schedule_validation.luck_next_luck_r) under the same null",
               agg={"obs": r_obs, "null_mean": m_r, "null_lo": float(np.percentile(rs, 2.5)),
                    "null_hi": float(np.percentile(rs, 97.5)), "p": p_r, "adjusted": r_obs - m_r, "n": len(pairs)},
               perms=(B, 0))


# ------------------------------------------------------------------ referees

def referee(conn, out, perms2):
    games = pd.read_sql("""SELECT game_id, season, SUM(pf) AS fouls, SUM(fta) AS fta, AVG(poss_est) AS pace
                           FROM game_team_box GROUP BY game_id, season HAVING COUNT(*) = 2
                           ORDER BY season, game_id""", conn)          # rows grouped by season (the shuffle is within season)
    for c in ("fouls", "fta", "pace"):
        games[c] = games[c].astype(float)
        games[c + "_adj"] = games[c] - games.groupby("season")[c].transform("mean")
    offs = pd.read_sql("SELECT game_id, official_id, official_name FROM game_officials ORDER BY game_id, official_id", conn)
    offs = offs[offs["game_id"].isin(games["game_id"])]
    gidx = pd.Series(np.arange(len(games)), index=games["game_id"])
    per_game = offs.groupby("game_id").size()
    crew_games = per_game[per_game == 3].index                          # the exact 3-official crew, as the platform defines it
    crews = offs[offs["game_id"].isin(crew_games)].groupby("game_id").agg(
        key=("official_id", lambda s: ",".join(str(i) for i in s)),      # offs is ordered by official_id within a game
        names=("official_name", ", ".join))
    season_codes = pd.factorize(games["season"], sort=True)[0]
    check(bool((np.diff(season_codes) >= 0).all()), "games are ordered by season")
    S = season_codes.max() + 1
    G = len(games)
    cur = conn.cursor()
    cur.execute("SELECT official_id, n_games, fouls_diff, fta_diff FROM referee_tendencies")
    st_off = {i: (n, f, t) for i, n, f, t in cur.fetchall()}
    cur.execute("SELECT crew_key, n_games, fouls_diff, fta_diff FROM referee_crew_tendencies")
    st_crew = {k: (n, f, t) for k, n, f, t in cur.fetchall()}

    def assignment(frame, col, name_col):
        """Dense (units, G) 0/1 matrix, unit ids, names, counts."""
        units = frame.groupby(col).agg(n=("game_id", "size"), name=(name_col, "first")).reset_index()
        A = np.zeros((len(units), G))
        uidx = pd.Series(np.arange(len(units)), index=units[col])
        A[uidx[frame[col]].to_numpy(), gidx[frame["game_id"]].to_numpy()] = 1.0
        return A, units

    A_off, units_off = assignment(offs, "official_id", "official_name")
    crew_frame = crews.reset_index().rename(columns={"key": "crew_key"})
    crew_frame = crew_frame[crew_frame.groupby("crew_key")["game_id"].transform("size") >= CREW_FLOOR]
    A_crew, units_crew = assignment(crew_frame, "crew_key", "names")
    check(all(int(units_off.loc[i, "n"]) == st_off[o][0] for i, o in enumerate(units_off["official_id"])),
          "each official's game count equals referee_tendencies'")
    check(all(int(units_crew.loc[i, "n"]) == st_crew[k][0] for i, k in enumerate(units_crew["crew_key"])),
          "each crew's game count equals referee_crew_tendencies'")
    log(f"referee: {G:,} games with box totals, {len(units_off)} officials, {len(units_crew)} crews with {CREW_FLOOR}+ games")
    max_dev = 0.0
    for kind, A, units, key_col, st in (("official", A_off, units_off, "official_id", st_off),
                                        ("crew", A_crew, units_crew, "crew_key", st_crew)):
        n = A.sum(axis=1)
        for c in ("fouls", "fta", "pace"):
            v = games[c + "_adj"].to_numpy()
            obs = A @ v / n
            if c != "pace":
                dev = max(abs(obs[i] - st[k][1 if c == "fouls" else 2]) for i, k in enumerate(units[key_col]))
                max_dev = max(max_dev, dev)
                check(dev <= 5.1e-4, f"referee {kind} {c}: recomputed differences equal the stored ones to 3 decimals (dev {dev:.2e})")
            rng = rng_for("referee", kind, c)
            draws = []
            for start in range(0, perms2, CHUNK):
                b = min(CHUNK, perms2 - start)
                keys = season_codes[None, :] + rng.random((b, G))
                perm = np.argsort(keys, axis=1)                     # a shuffle of the games within each season
                draws.append((v[perm] @ A.T) / n)
            D = np.concatenate(draws)
            floor = REF_FLOOR if kind == "official" else CREW_FLOOR
            e = np.nonzero(n >= floor)[0]
            pp, _, _nf = perm_p_matrix(obs[e], D[:, e])
            nulls = null_summary(D[:, e])
            for j, i in enumerate(e):
                out.unit("referee", f"referee:{kind}:{c}", units.loc[i, key_col], units.loc[i, "name"], 0, int(n[i]), obs[i],
                         tuple(x[j] for x in nulls), pp[j], perms2)
            out.family("referee", f"referee:{kind}:{c}", f"Referee {kind}: {c} per game vs.\\ the season's league mean",
                       kind, floor, "null: game values shuffled within each season (the games worked are a random draw of the "
                       "season's games); statistic = referee_tendencies' season-adjusted difference", perms=(perms2, 0))
    out.add_meta("referee_max_dev_vs_stored", max_dev, "max |recomputed - stored| fouls/FTA difference (stored has 3 decimals)")
    out.add_meta("referee_games", G)


# ------------------------------------------------------------------ write

UNIT_DDL = """family TEXT NOT NULL, key TEXT NOT NULL, unit_id TEXT NOT NULL, unit_name TEXT, season INTEGER NOT NULL,
    n_obs INTEGER, stat DOUBLE PRECISION, null_mean DOUBLE PRECISION, null_sd DOUBLE PRECISION,
    null_lo DOUBLE PRECISION, null_hi DOUBLE PRECISION, p_value DOUBLE PRECISION, n_perm INTEGER,
    bh_reject BOOLEAN, bh_q DOUBLE PRECISION, platform_stat DOUBLE PRECISION, PRIMARY KEY (family, key, unit_id, season)"""
SUMMARY_DDL = """family TEXT NOT NULL, key TEXT PRIMARY KEY, label TEXT, unit_kind TEXT, floor INTEGER, n_units INTEGER,
    k05 INTEGER, expected05 DOUBLE PRECISION, k_fdr INTEGER, perms1 INTEGER, perms2 INTEGER,
    agg_obs DOUBLE PRECISION, agg_null_mean DOUBLE PRECISION, agg_null_lo DOUBLE PRECISION, agg_null_hi DOUBLE PRECISION,
    agg_p DOUBLE PRECISION, agg_adjusted DOUBLE PRECISION, agg_n INTEGER, macro TEXT, in_table BOOLEAN, note TEXT"""
META_DDL = "key TEXT PRIMARY KEY, value DOUBLE PRECISION, note TEXT"


def _columns(ddl):
    """Column names of a DDL body, in order (stops at the PRIMARY KEY clause)."""
    cols = []
    for piece in ddl.split(","):
        words = piece.split()
        if not words or words[0] == "PRIMARY":
            break
        cols.append(words[0])
    return cols


def write(conn, out, families, everything):
    cur = conn.cursor()
    for t, ddl in (("paper_beliefs", UNIT_DDL), ("paper_beliefs_summary", SUMMARY_DDL), ("paper_beliefs_meta", META_DDL)):
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (t,))
        have = [r[0] for r in cur.fetchall()]
        if everything or (have and have != _columns(ddl)):      # a schema change rebuilds the table even on --only
            cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute(f"CREATE TABLE IF NOT EXISTS paper_beliefs ({UNIT_DDL})")
    cur.execute(f"CREATE TABLE IF NOT EXISTS paper_beliefs_summary ({SUMMARY_DDL})")
    cur.execute(f"CREATE TABLE IF NOT EXISTS paper_beliefs_meta ({META_DDL})")
    if not everything:
        cur.execute("DELETE FROM paper_beliefs WHERE family IN %s", (tuple(families),))
        cur.execute("DELETE FROM paper_beliefs_summary WHERE family IN %s", (tuple(families),))
        cur.execute("DELETE FROM paper_beliefs_meta WHERE key IN %s", (tuple(k for k, _v, _n in out.meta),))
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_beliefs VALUES %s",
                                   sorted(out.units, key=lambda r: (r[0], r[1], r[2], r[4])), page_size=2000)
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_beliefs_summary VALUES %s", sorted(out.summary))
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_beliefs_meta VALUES %s", sorted(out.meta))
    conn.commit()
    for t in ("paper_beliefs", "paper_beliefs_summary", "paper_beliefs_meta"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        log(f"{t}: {n} rows, {size}")


def write_table(conn):
    """paper/tables/beliefs.tex: the paper's Table beliefs, numbers as \\pn macros."""
    cur = conn.cursor()
    cur.execute("SELECT key, macro FROM paper_beliefs_summary WHERE in_table")
    have = dict(cur.fetchall())
    missing = [k for _f, k, _m, _l, _u in TABLE_ROWS if k not in have]
    if missing:
        log(f"table not written: families not yet in paper_beliefs_summary: {missing}")
        return
    lines = [
        "% paper/tables/beliefs.tex -- GENERATED by scripts/paper_beliefs.py; every number is a \\pn macro from numbers.tex.",
        "\\begin{table*}[!t]",
        "\\caption{Popular beliefs under one test. For every unit the statistic is the platform's own number and the null "
        "comes from shuffling that unit's own data; $p<0.05$ counts nominal rejections, ``exp.'' is the $0.05\\,n$ the global null "
        "would give, and FDR is the number surviving Benjamini--Hochberg at 5\\% within the row}\\label{tab:beliefs}",
        "\\centering",
        "\\footnotesize",
        "\\begin{tabular}{llrrrr}",
        "\\toprule",
        "Belief & Unit & $n$ & $p<0.05$ & exp. & FDR\\\\",
        "\\midrule",
    ]
    for _f, _k, m, label, unit in TABLE_ROWS:
        lines.append(f"{label} & {unit} & \\pnBl{m}N & \\pnBl{m}Kfive & \\pnBl{m}Exp & \\pnBl{m}Kfdr\\\\")
    lines += ["\\bottomrule", "\\end{tabular}", "\\end{table*}", ""]
    os.makedirs(os.path.dirname(TABLE_TEX), exist_ok=True)
    with open(TABLE_TEX, "w") as f:
        f.write("\n".join(lines))
    log(f"wrote {TABLE_TEX}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated families: " + ",".join(FAMILIES))
    ap.add_argument("--perms", type=int, default=PERMS, help=f"draws per unit (default {PERMS})")
    ap.add_argument("--perms2", type=int, default=PERMS2, help=f"draws for screened units and small families (default {PERMS2})")
    ap.add_argument("--streak-perms", type=int, default=STREAK_PERMS, help=f"draws for the streak family (default {STREAK_PERMS})")
    args = ap.parse_args()
    families = [f for f in args.only.split(",") if f] or list(FAMILIES)
    bad = set(families) - set(FAMILIES)
    if bad:
        raise SystemExit(f"unknown families: {sorted(bad)}")
    conn = psycopg2.connect(**DB_CONFIG)
    out = Out()
    out.add_meta("seed", SEED)
    out.add_meta("perms1", args.perms)
    out.add_meta("perms2", args.perms2)
    out.add_meta("streak_perms", args.streak_perms)
    out.add_meta("stage2_p", STAGE2_P)
    out.add_meta("fdr_q", FDR_Q)
    if "clutch" in families:
        clutch(conn, out, args.perms, args.perms2)
    if "streak" in families:
        streak(conn, out, args.streak_perms, args.perms2)
    if "split" in families:
        split(conn, out, args.perms, args.perms2)
    if "luck" in families:
        luck(conn, out, args.perms2)
    if "referee" in families:
        referee(conn, out, args.perms2)
    out.add_meta("runtime_seconds", time.time() - T0)
    write(conn, out, families, set(families) == set(FAMILIES))
    write_table(conn)
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
