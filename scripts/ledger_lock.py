"""
ledger_lock.py
===============
Locks the Forecast Ledger's preseason forecasts for 2026-27 (Teams > Forecast
Ledger) before the first tip, so the season can be used as a forward-looking
test: nothing in the lock can have seen a 2026-27 result. The rules are in
api/ledger_lib.py (frozen at the git tag ledger_lib.LOCK_TAG); this script
applies them once and stores what they give.

What it does, in order:

1. Checks the code: the files the lock runs (FROZEN) must be committed and
   unchanged, and HEAD must be the commit the tag points to (--lock only).
   Their git blob ids, the commit and the tag go into ledger_meta.

2. Reads ESPN (scripts/ledger_espn.py): the 2026-27 regular-season schedule
   (1,206 events on 2026-09-30: 1,200 with both teams, 80 per team, plus six
   NBA Cup knockout games whose teams aren't known) and every team's roster
   (training-camp rosters, 18-21 players). The first tip is the earliest
   start time among the games with both teams; the lock refuses to run after it.

3. Projections: build_projections.py's own functions (load, shrink_m,
   aging_levels, league_means, project, minutes_steps, apply_minutes_step)
   give every player's Marcel projection of BPM and minutes a game for every
   target season from 2010-11 to 2026-27. For 2026-27 they must equal the
   stored player_projections to 1e-9 for every player there (the run stops
   otherwise); players who sat out all of 2025-26 keep the projection the
   method gives them (the stored table lists only players who played in
   2025-26).

4. Hindcast (2010-11 to 2025-26, 480 team-seasons): each team's roster = the
   players with 200+ minutes that season (the only players every season on
   file has) on the team they started the season with (player_game_lines'
   first game from 2020-21, Basketball-Reference's first stint for traded
   players before, else the season row's team); the minutes rule and the
   projections made before that season give its team BPM, centred on the
   league's mean. Final SRS (luck_lib.srs_fit on game_scores) is regressed
   through the origin on (centred team BPM, last season's final SRS):
   leave-one-season-out predictions give the roster forecast's honest error,
   whose mean square is its prior variance; the fit on all 16 seasons gives
   the locked a and c. The same seasons are also scored game by game from
   preseason ratings only (never updated), as-is against roster-aware, with
   a 95% interval from paper_tests' cluster bootstrap by season. The
   hindcast knows who played for each team (a player who missed the season
   is not on its roster; nobody traded in mid-season is), so it flatters the
   roster forecast: the 2026-27 season is the real test.

5. The two forecasts (ledger_lib.FORECASTS): prior means and variance per
   team, P(home wins) for every scheduled game from preseason ratings, and
   10,000 simulated seasons + playoffs each (win totals with 80% ranges,
   playoff / top-6 / play-in / first-seed odds, each playoff round, title).

6. --lock: writes ledger_meta, ledger_schedule, ledger_rosters,
   ledger_hindcast and ledger_forecasts for the season (every row stamped
   with the lock time), re-reads them as the canonical CSV
   (ledger_lib.canonical_csv), and stores its SHA-256 in ledger_lock. The
   CSV is also written to $TMPDIR (or --csv PATH). One lock per season: a
   second --lock stops unless --relock is given, and --relock is refused
   after the first tip (a relock makes any hash already sent stale).

Tables (one set of rows per season; created if missing):
  ledger_meta       key / value / note: code, fetch times, every locked parameter,
                    rule texts, hindcast results, checks, sources
  ledger_schedule   ESPN's schedule at lock time with back-to-back flags
  ledger_rosters    ESPN's rosters at lock time, matched NBA ids, projections and
                    the minutes each player was given
  ledger_hindcast   the hindcast per team-season
  ledger_forecasts  one row per forecast x team, and per forecast x game
  ledger_lock       the hash of the canonical CSV of the five tables above, its size
                    and the lock time (not part of the CSV)

Usage:
    cd scripts && python3 ledger_lock.py --dry-run     # everything, writes nothing (~1 min)
    cd scripts && python3 ledger_lock.py --lock        # once, before the first tip
    cd scripts && python3 ledger_lock.py --verify      # re-export and compare with the stored hash
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
import build_projections as BP  # noqa: E402
import ledger_espn as E  # noqa: E402
import ledger_lib as LL  # noqa: E402
import luck_lib  # noqa: E402
import paper_tests as PT  # noqa: E402
import season_sim_lib as L  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

FROZEN = ["api/ledger_lib.py", "api/season_sim_lib.py", "api/luck_lib.py", "api/stat_samples.py",
          "scripts/ledger_lock.py", "scripts/ledger_espn.py", "scripts/build_projections.py", "scripts/paper_tests.py"]
RUNS = L.DEFAULT_RUNS
RESAMPLES = 10000
MIN_HINDCAST_MINUTES = 200        # every season on file has its players with 200+ minutes (paper_manifest.LEGACY_SEASONS)
RECENT_FROM = LL.SEASON - 3       # "recent" for name matching: played in 2023-24 or later
SOURCES = {
    "espn_schedule": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard (one request per date of ESPN's 2026-27 calendar)",
    "espn_rosters": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}/roster",
    "nba_cup_rule": "https://en.wikipedia.org/wiki/NBA_Cup (read 2026-09-30): 80 games announced per team, two added after the group stage",
    "playoff_format": "https://en.wikipedia.org/wiki/2025_NBA_playoffs and https://en.wikipedia.org/wiki/NBA_playoffs (read 2026-09-30): "
                      "fixed bracket 1-8/4-5/3-6/2-7, best of seven 2-2-1-1-1, Finals home court to the better record",
    "replacement_level": "scripts/build_bpm_vorp.py: VORP = (BPM + 2.0) x share of team minutes",
}


def log(msg):
    print(msg, flush=True)


# ── code ────────────────────────────────────────────────────────────────────

def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def code_state():
    dirty = git("status", "--porcelain", "--", *FROZEN)
    head = git("rev-parse", "HEAD")
    tagged = git("rev-parse", "--verify", "--quiet", f"{LL.LOCK_TAG}^{{commit}}")
    blobs = {f: git("hash-object", f) for f in FROZEN}
    committed = {f: git("rev-parse", f"HEAD:{f}") for f in FROZEN}
    return {"head": head, "tag_commit": tagged, "dirty": dirty, "blobs": blobs,
            "clean": not dirty and blobs == committed, "tag_at_head": tagged == head}


# ── projections ─────────────────────────────────────────────────────────────

def projections(conn):
    """{T: DataFrame(player_id -> proj_bpm, proj_min)} for T = HINDCAST_FROM..SEASON with build_projections'
    functions, exactly as its main() makes them, and the check against the stored player_projections."""
    d = BP.load(conn)
    latest = int(d.season.max())
    if latest + 1 != LL.SEASON:
        raise SystemExit(f"player_season_stats ends in {latest}; the ledger locks {LL.SEASON}")
    Ms = BP.shrink_m(conn)
    curves = BP.aging_levels(conn)
    leagues = {k: BP.league_means(d, k) for k in ("min", "pts36", "bpm")}
    raw_min = {T: BP.project(d, T, "min", Ms["min"][0], None, leagues["min"])
               for T in range(BP.BACKTEST_FROM, LL.SEASON + 1)}
    min_rows = []
    for T in range(BP.BACKTEST_FROM, latest + 1):
        pr = raw_min[T]
        act = d[(d.season == T) & (d.minutes >= BP.EVAL_MIN_MINUTES)].set_index("player_id")["min"].rename("actual")
        j = pr.join(act, how="inner")
        j["T"] = T
        j["resid"] = j.actual - j.reg
        j["bin"] = np.digitize(j.reg.to_numpy(float), BP.MPG_BINS)
        min_rows.append(j[["T", "reg", "age_T", "resid", "bin"]])
    min_rows = pd.concat(min_rows, ignore_index=True)
    out = {}
    for T in range(LL.HINDCAST_FROM, LL.SEASON + 1):
        p36 = BP.project(d, T, "pts36", Ms["pts36"][0], curves[BP.STATS["pts36"][5]], leagues["pts36"])
        enough = p36.index[p36.minutes_window >= BP.MIN_PROJ_MINUTES]
        bpm = BP.project(d, T, "bpm", Ms["bpm"][0], curves[BP.STATS["bpm"][5]], leagues["bpm"])
        mpg = raw_min[T][raw_min[T].index.isin(enough)]
        mpg = BP.apply_minutes_step(mpg, BP.minutes_steps(min_rows, exclude_T=T if T <= latest else None))
        bpm = bpm[bpm.index.isin(enough)]
        out[T] = pd.DataFrame({"proj_min": mpg.proj, "proj_bpm": bpm.proj.reindex(mpg.index)})
    stored = pd.read_sql("SELECT player_id, stat, projection FROM player_projections WHERE season = %s AND stat IN ('min', 'bpm')",
                         conn, params=(LL.SEASON,))
    st = stored.pivot(index="player_id", columns="stat", values="projection")
    mine = out[LL.SEASON]
    dev_min = (mine.proj_min.reindex(st.index) - st["min"]).abs()
    dev_bpm = (mine.proj_bpm.reindex(st.index) - st["bpm"]).abs()
    if dev_min.isna().any() or (dev_bpm[st["bpm"].notna()].isna()).any() or dev_min.max() > 1e-9 or dev_bpm.max() > 1e-9:
        raise SystemExit(f"projections differ from the stored player_projections: min {dev_min.max()}, bpm {dev_bpm.max()}")
    extra = mine.index.difference(st.index)
    names = d.sort_values("season").groupby("player_id").player_name.last()
    check = {"stored_players": int(len(st)), "max_dev_min": float(dev_min.max()), "max_dev_bpm": float(dev_bpm.max()),
             "added_players": int(len(extra)),
             "added_names": sorted(names.reindex(extra).fillna("?").tolist())}
    return out, check, d


# ── hindcast ────────────────────────────────────────────────────────────────

HINDCAST_ROSTER_SQL = """
    SELECT s.player_id, s.season, s.team_abbreviation AS season_team, s.gp * s.min AS minutes,
           st.team AS stint_team, pgl.team_abbreviation AS first_game_team
    FROM player_season_stats s
    LEFT JOIN player_team_stints st ON st.player_id = s.player_id AND st.season = s.season AND st.stint = 1
    LEFT JOIN (SELECT DISTINCT ON (player_id, season) player_id, season, team_abbreviation
               FROM player_game_lines WHERE team_abbreviation <> 'NaN'
               ORDER BY player_id, season, game_date, game_id) pgl
           ON pgl.player_id = s.player_id AND pgl.season = s.season
    WHERE s.season BETWEEN %s AND %s AND s.gp * s.min >= %s
    ORDER BY s.season, s.player_id"""


def team_table(players, allocate=LL.allocate_minutes):
    """Per team: team BPM, minutes to replacement, players counted. players: team, player id, proj_min, proj_bpm."""
    rows, alloc = [], []
    for team, g in players.groupby("team", sort=True):
        p, team_bpm, gap = allocate(g)
        rows.append({"team": team, "team_bpm": team_bpm, "gap_minutes": gap, "players": int(p.counted.sum())})
        alloc.append(p)
    t = pd.DataFrame(rows).set_index("team")
    t["team_bpm_centred"] = t.team_bpm - t.team_bpm.mean()
    return t, pd.concat(alloc)


def fit_through_origin(X, y):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def expected_wins(games, prior_means, hca, beta):
    """Per team: sum of its preseason win chances over the season's games (games: prepare_rest rows)."""
    g = L.home_rows(games)
    em = g.home.map(prior_means).to_numpy(float) - g.away.map(prior_means).to_numpy(float) + g.venue.to_numpy(float) * hca
    p = L.win_prob(beta, em, g.home_b2b.to_numpy(float), g.away_b2b.to_numpy(float))
    g = g.assign(p=p)
    w = pd.concat([g.groupby("home").p.sum(), (1 - g.p).groupby(g.away).sum()], axis=1).fillna(0).sum(axis=1)
    return w, g


def hindcast(conn, proj, params, beta):
    games = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where="WHERE g.season >= %s"), conn,
                                       params=(LL.HINDCAST_FROM - 1,)))
    by_season = dict(tuple(games.groupby("season")))
    srs, hca, sigma = {}, {}, {}
    for s, g in by_season.items():
        r, h, sd, _ = luck_lib.srs_fit(g)
        srs[s], hca[s], sigma[s] = r, h, sd
    ros = pd.read_sql(HINDCAST_ROSTER_SQL, conn, params=(LL.HINDCAST_FROM, LL.SEASON - 1, MIN_HINDCAST_MINUTES))
    ros["team"] = ros.first_game_team.fillna(ros.stint_team).fillna(ros.season_team)
    rows = []
    for T in range(LL.HINDCAST_FROM, LL.SEASON):
        r = ros[ros.season == T].copy()
        teams = set(srs[T])
        if set(r.team) != teams:
            raise SystemExit(f"hindcast {T}: roster teams {sorted(set(r.team) ^ teams)} don't match game_scores")
        pr = proj[T]
        r["proj_min"] = r.player_id.map(pr.proj_min)
        r["proj_bpm"] = r.player_id.map(pr.proj_bpm)
        r["order_key"] = r.player_id.astype(str)
        t, _ = team_table(r)
        alt, _ = team_table(r, LL.allocate_minutes_scaled)
        prev = {luck_lib.FRANCHISE.get(k, k): v for k, v in srs[T - 1].items()}
        for team in sorted(teams):
            rows.append({"target_season": T, "team": team, "players": int(t.at[team, "players"]),
                         "team_bpm": t.at[team, "team_bpm"], "team_bpm_centred": t.at[team, "team_bpm_centred"],
                         "gap_minutes": t.at[team, "gap_minutes"], "srs_prev": prev[luck_lib.FRANCHISE.get(team, team)],
                         "srs_final": srs[T][team], "alt_centred": alt.at[team, "team_bpm_centred"]})
    h = pd.DataFrame(rows)
    # The dropped rule's leave-one-season-out error, for the record (ledger_meta.rule_minutes_alternative).
    Xa = h[["alt_centred", "srs_prev"]].to_numpy(float)
    alt_pred = np.zeros(len(h))
    for T in sorted(h.target_season.unique()):
        m = (h.target_season == T).to_numpy()
        alt_pred[m] = Xa[m] @ fit_through_origin(Xa[~m], h.srs_final.to_numpy(float)[~m])
    alt_rmse = float(np.sqrt(np.mean((h.srs_final.to_numpy(float) - alt_pred) ** 2)))
    h = h.drop(columns="alt_centred")
    X = h[["team_bpm_centred", "srs_prev"]].to_numpy(float)
    y = h.srs_final.to_numpy(float)
    h["pred_as_is"] = params["carry"] * h.srs_prev
    h["pred_roster_loso"] = np.nan
    loso_coef = {}
    for T in sorted(h.target_season.unique()):
        m = (h.target_season == T).to_numpy()
        b = fit_through_origin(X[~m], y[~m])
        loso_coef[int(T)] = b.tolist()
        h.loc[m, "pred_roster_loso"] = X[m] @ b
    a, c = fit_through_origin(X, y)
    err_r = h.srs_final - h.pred_roster_loso
    err_a = h.srs_final - h.pred_as_is
    tau2_roster = float(np.mean(err_r ** 2))
    # Game by game from preseason ratings only, and win totals.
    game_parts, wins_rows = [], []
    for T in sorted(h.target_season.unique()):
        ht = h[h.target_season == T].set_index("team")
        g = by_season[T]
        wa, ga = expected_wins(g, ht.pred_as_is.to_dict(), hca[T - 1], beta)
        wr, gr = expected_wins(g, ht.pred_roster_loso.to_dict(), hca[T - 1], beta)
        tt = luck_lib.team_table(g)
        for team in ht.index:
            wins_rows.append({"target_season": T, "team": team, "wins": int(tt.at[team, "wins"]),
                              "games": int(tt.at[team, "games"]), "exp_wins_as_is": float(wa[team]),
                              "exp_wins_roster": float(wr[team])})
        game_parts.append(pd.DataFrame({"season": T, "y": ga.home_won.to_numpy(float), "p_as_is": ga.p.to_numpy(float),
                                        "p_roster": gr.p.to_numpy(float)}))
    h = h.merge(pd.DataFrame(wins_rows), on=["target_season", "team"])
    G = pd.concat(game_parts, ignore_index=True)
    ll = {k: PT.loss("log_loss", "pregame", G[f"p_{k}"].to_numpy(), G.y.to_numpy()) for k in LL.FORECASTS}
    br = {k: PT.loss("brier", "pregame", G[f"p_{k}"].to_numpy(), G.y.to_numpy()) for k in LL.FORECASTS}
    idx, C = PT.cluster_codes(G.season.to_numpy())

    def paired(la, lb, key):
        rng = np.random.default_rng(PT.row_seed("ledger_hindcast", key))
        means = PT.boot_means(rng, idx, C, [la, lb], RESAMPLES)
        dd = means[:, 0] - means[:, 1]
        lo, hi = PT.percentile_ci(dd)
        return {"a": float(la.mean()), "b": float(lb.mean()), "diff": float(la.mean() - lb.mean()),
                "ci_lo": lo, "ci_hi": hi, "p_boot": PT.boot_p(dd)}
    idx_t, C_t = PT.cluster_codes(h.target_season.to_numpy())

    def paired_team(la, lb, key):
        rng = np.random.default_rng(PT.row_seed("ledger_hindcast", key))
        means = PT.boot_means(rng, idx_t, C_t, [la, lb], RESAMPLES)
        dd = np.sqrt(means[:, 0]) - np.sqrt(means[:, 1]) if key.endswith("rmse") else means[:, 0] - means[:, 1]
        lo, hi = PT.percentile_ci(dd)
        va = float(np.sqrt(la.mean())) if key.endswith("rmse") else float(la.mean())
        vb = float(np.sqrt(lb.mean())) if key.endswith("rmse") else float(lb.mean())
        return {"a": va, "b": vb, "diff": va - vb, "ci_lo": lo, "ci_hi": hi, "p_boot": PT.boot_p(dd)}
    wa_err = (h.exp_wins_as_is / h.games * 82 - h.wins / h.games * 82).abs().to_numpy()
    wr_err = (h.exp_wins_roster / h.games * 82 - h.wins / h.games * 82).abs().to_numpy()
    summary = {
        "team_seasons": int(len(h)), "seasons": f"{LL.HINDCAST_FROM}-{LL.SEASON - 1}", "games": int(len(G)),
        "roster_a": float(a), "roster_c": float(c), "loso_coefficients": loso_coef,
        "srs_rmse": paired_team(err_r.to_numpy() ** 2, err_a.to_numpy() ** 2, "srs_rmse"),
        "wins82_mae": paired_team(wr_err, wa_err, "wins82_mae"),
        "game_log_loss": paired(ll["roster"], ll["as_is"], "game_log_loss"),
        "game_brier": paired(br["roster"], br["as_is"], "game_brier"),
        "by_season": {int(T): {"log_loss_as_is": float(ll["as_is"][(G.season == T).to_numpy()].mean()),
                               "log_loss_roster": float(ll["roster"][(G.season == T).to_numpy()].mean()),
                               "srs_rmse_as_is": float(np.sqrt((err_a[h.target_season == T] ** 2).mean())),
                               "srs_rmse_roster": float(np.sqrt((err_r[h.target_season == T] ** 2).mean()))}
                      for T in sorted(h.target_season.unique())},
        "corr_team_bpm_srs": float(np.corrcoef(h.team_bpm_centred, h.srs_final)[0, 1]),
        "corr_srs_prev_srs": float(np.corrcoef(h.srs_prev, h.srs_final)[0, 1]),
        "tau2_roster": tau2_roster, "mse_as_is": float(np.mean(err_a ** 2)),
        "alternative_scaled_rule_srs_rmse": alt_rmse,
    }
    return h, summary, {"hca_prev": hca[LL.SEASON - 1], "sigma_prev": sigma[LL.SEASON - 1], "srs_prev": srs[LL.SEASON - 1],
                        "games_prev": by_season[LL.SEASON - 1]}


# ── live ────────────────────────────────────────────────────────────────────

def live_rosters(conn, proj):
    rows, fetched_at = E.fetch_rosters()
    r = pd.DataFrame(rows)
    bio = pd.read_sql("SELECT player_id, player_name, birth_date, last_season FROM player_bio", conn)
    recent = pd.read_sql("SELECT DISTINCT player_id, player_name FROM player_season_stats WHERE season >= %s",
                         conn, params=(RECENT_FROM,))
    r["player_id"], r["match_method"] = LL.match_players(r, bio, recent, RECENT_FROM)
    pr = proj[LL.SEASON]
    r["proj_min"] = r.player_id.map(lambda p: pr.proj_min.get(p) if p is not None else np.nan).astype(float)
    r["proj_bpm"] = r.player_id.map(lambda p: pr.proj_bpm.get(p) if p is not None else np.nan).astype(float)
    r["order_key"] = r.espn_athlete_id.str.zfill(12)
    dup = r[r.player_id.notna()].player_id.duplicated(keep=False)
    if dup.any():
        raise SystemExit(f"one NBA id on two roster rows: {r[r.player_id.notna()][dup][['team', 'player_name']].values.tolist()}")
    return r, fetched_at


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="compute everything, write nothing to the database")
    mode.add_argument("--lock", action="store_true", help="write the lock (once, before the first tip)")
    mode.add_argument("--verify", action="store_true", help="re-export the stored lock and compare with its hash")
    ap.add_argument("--relock", action="store_true", help="with --lock: replace this season's lock (before the first tip only)")
    ap.add_argument("--csv", help="where to write the canonical CSV (default: $TMPDIR)")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    if args.verify:
        return verify(conn, args.csv)

    t0 = time.time()
    code = code_state()
    log(f"code: HEAD {code['head'][:12]}, tag {LL.LOCK_TAG} -> {(code['tag_commit'] or 'none')[:12]}, "
        f"frozen files {'clean' if code['clean'] else 'MODIFIED'}")
    if args.lock and not (code["clean"] and code["tag_at_head"]):
        raise SystemExit(f"--lock needs the frozen files committed and unchanged and {LL.LOCK_TAG} at HEAD "
                         f"(git tag -f {LL.LOCK_TAG} HEAD after committing):\n{code['dirty']}")

    # ESPN schedule first: the lock must happen before the first tip.
    sched_rows = E.fetch_schedule(LL.SEASON)
    sched_fetched = datetime.now(timezone.utc)
    sched = pd.DataFrame(sched_rows)
    sched["counted"] = sched.home.notna() & sched.away.notna()
    first_tip = min(pd.Timestamp(t) for t in sched[sched.counted].tip_utc)
    now = datetime.now(timezone.utc)
    log(f"schedule: {len(sched):,} events, {int(sched.counted.sum()):,} with both teams, first tip {first_tip.isoformat()} "
        f"({(first_tip - pd.Timestamp(now)).total_seconds() / 86400:.1f} days from now)")
    if pd.Timestamp(now) >= first_tip and not args.dry_run:
        raise SystemExit("the season has started: a lock now would not be a forward-looking forecast")
    sched = LL.add_b2b(sched)
    teams = sorted(set(sched[sched.counted].home) | set(sched[sched.counted].away))
    per_team = pd.concat([sched[sched.counted].home, sched[sched.counted].away]).value_counts()
    log(f"  games per team: {per_team.min()}-{per_team.max()}; neutral site: "
        f"{sched[sched.counted & sched.neutral_site][['game_date', 'home', 'away', 'city']].values.tolist()}")

    # Locked model inputs.
    cur = conn.cursor()
    cur.execute("SELECT name, value, note FROM season_sim_params")
    sp = {n: (v, note) for n, v, note in cur.fetchall()}
    params = {k: float(sp[k][0]) for k in ("carry", "tau2", "hca_n0")}
    cur.execute("SELECT form, beta FROM pregame_model_fit WHERE chosen")
    form, beta = cur.fetchone()
    if form != sp["chosen_form"][1]:
        raise SystemExit(f"pregame_model_fit's chosen form {form} != season_sim_params' {sp['chosen_form'][1]}")
    log(f"as-is model: form {form}, beta {beta}, carry {params['carry']:.4f}, tau2 {params['tau2']:.3f}, hca_n0 {params['hca_n0']:.1f}")

    proj, proj_check, _ = projections(conn)
    log(f"projections: {proj_check['stored_players']} stored players reproduced (max dev min {proj_check['max_dev_min']:.1e}, "
        f"bpm {proj_check['max_dev_bpm']:.1e}); {proj_check['added_players']} more who sat out 2025-26 "
        f"({time.time() - t0:.0f}s)")

    hc, hsum, prev = hindcast(conn, proj, params, beta)
    log(f"hindcast {hsum['seasons']} ({hsum['team_seasons']} team-seasons): roster a {hsum['roster_a']:.4f}, "
        f"c {hsum['roster_c']:.4f}; prior variance {hsum['tau2_roster']:.3f} (as-is {params['tau2']:.3f}, "
        f"as-is MSE here {hsum['mse_as_is']:.3f})")
    for k in ("srs_rmse", "wins82_mae", "game_log_loss", "game_brier"):
        s = hsum[k]
        log(f"  {k:>13}: roster {s['a']:.4f} vs as-is {s['b']:.4f}, diff {s['diff']:+.4f} "
            f"[{s['ci_lo']:+.4f}, {s['ci_hi']:+.4f}] p {s['p_boot']:.3f}")
    if set(prev["srs_prev"]) != set(teams):
        raise SystemExit(f"2025-26 teams {sorted(prev['srs_prev'])} != the schedule's {teams}")

    # Rosters and the roster-aware ratings.
    ros, ros_fetched = live_rosters(conn, proj)
    if sorted(ros.team.unique()) != teams:
        raise SystemExit(f"ESPN roster teams {sorted(ros.team.unique())} != schedule teams")
    tt, alloc = team_table(ros)
    ros = ros.merge(alloc[["espn_athlete_id", "counted", "minutes", "contribution"]], on="espn_athlete_id", how="left")
    log(f"rosters: {len(ros)} players, {ros.player_id.notna().sum()} matched "
        f"({ros.match_method.value_counts().to_dict()}), {ros.proj_min.notna().sum()} with a projection; "
        f"injury status {ros.injury_status.value_counts().to_dict()}")

    srs_prev = prev["srs_prev"]
    means = {
        "as_is": {t: LL.rnd(params["carry"] * srs_prev[t]) for t in teams},
        "roster": {t: LL.rnd(hsum["roster_a"] * tt.at[t, "team_bpm_centred"] + hsum["roster_c"] * srs_prev[t]) for t in teams},
    }
    var = {"as_is": params["tau2"], "roster": hsum["tau2_roster"]}
    # The as-is prior through the ledger's route equals the Season Simulator's own opening day.
    ref = L.ratings_as_of(LL.EMPTY_PLAYED, teams, L.season_prior(prev["games_prev"]), params)
    rows = LL.home_rows(sched)
    extras = LL.placeholder_games(teams, rows)
    log(f"placeholder games: {len(extras)} ({pd.Series([t for t, _ in extras]).value_counts().unique().tolist()} per team, "
        f"venues {pd.Series([v for _, v in extras]).value_counts().to_dict()})")
    locked_at = datetime.now(timezone.utc)
    fc_rows, sim_checks, rats = [], {}, {}
    for f in LL.FORECASTS:
        prior, prm = LL.prior_for(means[f], var[f], prev["hca_prev"], prev["sigma_prev"], params["hca_n0"])
        rat = LL.ratings_on(LL.EMPTY_PLAYED, teams, prior, prm)
        rats[f] = rat
        if f == "as_is":
            dev = max(abs(rat["r_post"][t] - ref["r_post"][t]) for t in teams)
            dev_v = max(abs(rat["var_post"][t] - ref["var_post"][t]) for t in teams)
            if dev > 1e-5 or dev_v > 1e-9 or abs(rat["hca"] - ref["hca"]) > 1e-12:
                raise SystemExit(f"as-is prior differs from the Season Simulator's opening day: {dev}, {dev_v}")
            sim_checks["as_is_vs_simulator_max_dev"] = dev
        em, p = LL.game_odds(rows, rat, beta)
        summ, chk = LL.simulate_preseason(teams, rows, rat, beta, extras, RUNS, L.sim_seed(LL.SEASON, "preseason", f))
        sim_checks[f] = chk
        n_sched = pd.concat([rows.home, rows.away]).value_counts()
        n_extra = pd.Series([t for t, _ in extras]).value_counts()
        for t in teams:
            s = summ[t]
            fc_rows.append({"forecast": f, "kind": "team", "key": t, "conference": L.CONFERENCE[t],
                            "team_bpm": LL.rnd(tt.at[t, "team_bpm_centred"]) if f == "roster" else None,
                            "srs_prev": LL.rnd(srs_prev[t]), "prior_mean": means[f][t], "prior_sd": LL.rnd(np.sqrt(var[f])),
                            "games_scheduled": int(n_sched.get(t, 0)), "games_placeholder": int(n_extra.get(t, 0)),
                            **{k: LL.rnd(s[k]) for k in ("mean_wins", "sd_wins", "wins_p10", "wins_p50", "wins_p90",
                                                          "p_playoffs", "p_top6", "p_playin", "p_first", "p_round2",
                                                          "p_conf_finals", "p_finals", "p_title")},
                            "locked_at": locked_at})
        for r_, e_, p_ in zip(rows.itertuples(), em, p):
            fc_rows.append({"forecast": f, "kind": "game", "key": r_.espn_id, "game_date": r_.game_date,
                            "home": r_.home, "away": r_.away, "venue": int(r_.venue), "home_b2b": bool(r_.home_b2b),
                            "away_b2b": bool(r_.away_b2b), "exp_margin": LL.rnd(e_), "p_home": LL.rnd(p_),
                            "locked_at": locked_at})
        log(f"{f}: title {max(summ.values(), key=lambda s: s['p_title'])['team']} "
            f"{max(s['p_title'] for s in summ.values()):.3f}; checks {chk}")
    fc = pd.DataFrame(fc_rows)
    show = fc[fc.kind == "team"].pivot(index="key", columns="forecast", values=["prior_mean", "mean_wins", "p_playoffs", "p_title"])
    log(show.sort_values(("mean_wins", "roster"), ascending=False).round(3).to_string())

    meta = build_meta(code, sched, sched_fetched, ros, ros_fetched, first_tip, locked_at, params, beta, form, prev,
                      hsum, proj_check, var, sim_checks, extras, sp)
    if args.dry_run:
        log(f"dry run: nothing written ({time.time() - t0:.0f}s)")
        return
    write_lock(conn, meta, sched, ros, hc, fc, first_tip, locked_at, code, args)
    log(f"done ({time.time() - t0:.0f}s)")


def build_meta(code, sched, sched_fetched, ros, ros_fetched, first_tip, locked_at, params, beta, form, prev, hsum,
               proj_check, var, sim_checks, extras, sp):
    j = lambda x: json.dumps(x, sort_keys=True, default=float)  # noqa: E731
    m = [
        ("season_label", f"{LL.SEASON - 1}-{str(LL.SEASON)[-2:]}", "the season this lock forecasts"),
        ("lock_time_utc", locked_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "when the rows were written; every ledger_forecasts row carries it"),
        ("first_tip_utc", first_tip.strftime("%Y-%m-%dT%H:%M:%SZ"), "earliest start time among the scheduled games with both teams (ESPN)"),
        ("code_commit", code["head"], "HEAD when the lock ran"),
        ("code_tag", LL.LOCK_TAG, "git tag of the frozen code (round 6 step 2 imports ledger_lib, season_sim_lib and luck_lib from it)"),
        ("frozen_files", j(code["blobs"]), "git blob id of each file the lock ran; equal to HEAD's"),
        ("schedule_fetched_utc", sched_fetched.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), SOURCES["espn_schedule"]),
        ("schedule_events", str(len(sched)), "regular-season events ESPN listed"),
        ("schedule_counted", str(int(sched.counted.sum())), "events with both teams known (forecast)"),
        ("schedule_tbd", str(int((~sched.counted).sum())), "NBA Cup knockout events with teams to be decided (not forecast)"),
        ("roster_fetched_utc", ros_fetched.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), SOURCES["espn_rosters"]),
        ("roster_players", str(len(ros)), "athletes on ESPN's 30 rosters"),
        ("roster_matched", j(ros.match_method.value_counts().sort_index().to_dict()), "players per matching tier (ledger_lib.match_players)"),
        ("roster_projected", str(int(ros.proj_min.notna().sum())), "rostered players with a projection"),
        ("roster_injury_status", j(ros.injury_status.fillna("none").value_counts().sort_index().to_dict()),
         "ESPN's injury status at lock time (stored, not used)"),
        ("pregame_form", form, "pregame_model_fit's chosen form (both forecasts use its coefficients)"),
        ("pregame_beta", j({k: repr(float(v)) for k, v in beta.items()}), "logistic coefficients, all seasons 2010-11 to 2025-26"),
        ("carry", repr(params["carry"]), sp["carry"][1]),
        ("tau2", repr(params["tau2"]), sp["tau2"][1] + " (the as-is prior variance)"),
        ("hca_n0", repr(params["hca_n0"]), sp["hca_n0"][1]),
        ("hca_prev", repr(float(prev["hca_prev"])), "2025-26 home court (SRS fit): the preseason home court"),
        ("sigma_prev", repr(float(prev["sigma_prev"])), "2025-26 game SD (SRS residual)"),
        ("prior_var_as_is", repr(float(var["as_is"])), "as-is prior variance (= tau2)"),
        ("prior_var_roster", repr(float(var["roster"])), "roster prior variance: the hindcast's leave-one-season-out mean squared error"),
        ("roster_a", repr(hsum["roster_a"]), "roster prior mean = a x centred team BPM + c x last season's SRS (fit on all hindcast seasons)"),
        ("roster_c", repr(hsum["roster_c"]), "see roster_a"),
        ("runs", str(RUNS), "simulated seasons per forecast"),
        ("seeds", j({f: L.sim_seed(LL.SEASON, "preseason", f) for f in LL.FORECASTS}), "season_sim_lib.sim_seed(2027, 'preseason', forecast)"),
        ("placeholder_games", str(len(extras)), "games simulated against a league-average opponent (two per team)"),
        ("hindcast", j({k: v for k, v in hsum.items() if k not in ("by_season", "loso_coefficients")}),
         "2010-11 to 2025-26: roster (a) vs as-is (b); diff = a - b with a 95% cluster-bootstrap interval by season (paper_tests)"),
        ("hindcast_by_season", j(hsum["by_season"]), "per target season: preseason-only log loss and SRS RMSE"),
        ("hindcast_loso_coefficients", j(hsum["loso_coefficients"]), "(a, c) fitted without each target season"),
        ("projection_check", j(proj_check), "build_projections' functions vs the stored player_projections for 2026-27"),
        ("simulation_checks", j(sim_checks), "title and Finals odds sum to 1 and 2 per run, 16 playoff teams, 82 games each"),
        ("sources", j(SOURCES), "where the facts the rules rely on were read"),
        ("rule_minutes", "Players: ESPN roster at lock time; projection = build_projections.py's Marcel method for 2026-27 (the stored "
                         "player_projections, plus players who sat out 2025-26). No projection (rookie, or under 250 minutes over three "
                         "seasons): no minutes. Projected minutes but no projected BPM: replacement level. Depth chart: in order of "
                         "projected minutes, each player gets his projected minutes until the team's 240 a game are used; if the roster's "
                         "projected minutes add to less, the rest go to a replacement-level player (BPM -2.0). Team BPM = "
                         "sum(minutes x BPM) / 48, centred on the league mean.", "ledger_lib.allocate_minutes"),
        ("rule_minutes_alternative", repr(hsum["alternative_scaled_rule_srs_rmse"]),
         "Hindcast leave-one-season-out SRS RMSE of the rule tried first and dropped before the lock (the 15 players with the most "
         "projected minutes, all scaled down to 240: ledger_lib.allocate_minutes_scaled), next to the chosen rule's in 'hindcast'. "
         "Dropped because with 18-21-player training-camp rosters it depends on how many veterans a team brought to camp."),
        ("rule_prior", "as_is: prior mean = carry x 2025-26 SRS, variance tau2 (the Season Simulator's). roster: prior mean = a x centred "
                       "team BPM + c x 2025-26 SRS, variance = the hindcast's leave-one-season-out mean squared error. Home court and "
                       "game SD from 2025-26.", "ledger_lib.prior_for"),
        ("rule_schedule", "ESPN's schedule at lock time; back-to-back = also played the previous calendar day; neutral site = no home "
                          "court. Games a team is still owed to reach 82 are played against a league-average opponent (rating 0), venues "
                          "bringing it closest to 41-41, home first on a tie.", "ledger_lib.add_b2b, placeholder_games"),
        ("rule_simulation", "season_sim_lib.simulate from opening day (each run draws every team's rating from its prior), then "
                            "simulate_playoffs: fixed bracket 1-8/4-5/3-6/2-7, best of seven, home court to the better seed (Finals: "
                            "better record in the run, coin flip on ties), games from the same model without rest terms.",
         "ledger_lib.simulate_preseason"),
        ("rule_game_odds", "P(home wins) = the chosen form's logistic on expected margin (prior means + home court) and both "
                           "back-to-back flags, from preseason ratings for every scheduled game.", "ledger_lib.game_odds"),
        ("rule_in_season", "Round 6 step 2 computes each game's odds on the morning of its date with the code at the tag: "
                           "season_sim_lib.ratings_as_of on the final scores of games before that date, starting from each forecast's "
                           "locked prior means (ledger_forecasts) and variance (prior_var_*), hca_prev, sigma_prev and hca_n0; the "
                           "locked coefficients; back-to-backs from the dates games were actually played. It logs when each set of "
                           "odds was computed, so odds logged before tip-off can be told from odds recomputed later.",
         "ledger_lib.prior_for, ratings_on, game_odds"),
        ("rule_injuries", "Injury status is stored (ledger_rosters.injury_status) and not used: the lock has no injury model. Round 6 "
                          "step 6 measures what knowing who plays is worth.", ""),
        ("rule_hindcast", "Hindcast rosters are the players with 200+ minutes that season on the team they started it with; they "
                          "know who played (a player who missed the season isn't on them), so the hindcast flatters the roster "
                          "forecast. Hindcast game odds use the all-season pre-game coefficients for both forecasts.", ""),
    ]
    return m


def write_lock(conn, meta, sched, ros, hc, fc, first_tip, locked_at, code, args):
    S = LL.SEASON
    cur = conn.cursor()
    ddl = {
        "ledger_meta": "season INTEGER, key TEXT, value TEXT, note TEXT, PRIMARY KEY (season, key)",
        "ledger_schedule": """season INTEGER, espn_id TEXT, game_date DATE, tip_utc TEXT, time_valid BOOLEAN, home TEXT, away TEXT,
            neutral_site BOOLEAN, venue TEXT, city TEXT, note TEXT, counted BOOLEAN, home_b2b BOOLEAN, away_b2b BOOLEAN,
            PRIMARY KEY (season, espn_id)""",
        "ledger_rosters": """season INTEGER, team TEXT, espn_athlete_id TEXT, player_name TEXT, birth_date DATE, position TEXT,
            experience INTEGER, injury_status TEXT, has_contract BOOLEAN, player_id INTEGER, match_method TEXT,
            proj_min DOUBLE PRECISION, proj_bpm DOUBLE PRECISION, counted BOOLEAN, minutes DOUBLE PRECISION,
            contribution DOUBLE PRECISION, PRIMARY KEY (season, team, espn_athlete_id)""",
        "ledger_hindcast": """season INTEGER, target_season INTEGER, team TEXT, players INTEGER, team_bpm DOUBLE PRECISION,
            team_bpm_centred DOUBLE PRECISION, gap_minutes DOUBLE PRECISION, srs_prev DOUBLE PRECISION,
            srs_final DOUBLE PRECISION, pred_as_is DOUBLE PRECISION, pred_roster_loso DOUBLE PRECISION, wins INTEGER,
            games INTEGER, exp_wins_as_is DOUBLE PRECISION, exp_wins_roster DOUBLE PRECISION,
            PRIMARY KEY (season, target_season, team)""",
        "ledger_forecasts": """season INTEGER, forecast TEXT, kind TEXT, key TEXT, conference TEXT, game_date DATE, home TEXT,
            away TEXT, venue SMALLINT, home_b2b BOOLEAN, away_b2b BOOLEAN, team_bpm DOUBLE PRECISION,
            srs_prev DOUBLE PRECISION, prior_mean DOUBLE PRECISION, prior_sd DOUBLE PRECISION, games_scheduled SMALLINT,
            games_placeholder SMALLINT, exp_margin DOUBLE PRECISION, p_home DOUBLE PRECISION, mean_wins DOUBLE PRECISION,
            sd_wins DOUBLE PRECISION, wins_p10 DOUBLE PRECISION, wins_p50 DOUBLE PRECISION, wins_p90 DOUBLE PRECISION,
            p_playoffs DOUBLE PRECISION, p_top6 DOUBLE PRECISION, p_playin DOUBLE PRECISION, p_first DOUBLE PRECISION,
            p_round2 DOUBLE PRECISION, p_conf_finals DOUBLE PRECISION, p_finals DOUBLE PRECISION, p_title DOUBLE PRECISION,
            locked_at TIMESTAMPTZ, PRIMARY KEY (season, forecast, kind, key)""",
        "ledger_lock": """season INTEGER PRIMARY KEY, lock_sha256 TEXT, csv_bytes INTEGER, csv_lines INTEGER,
            locked_at TIMESTAMPTZ, first_tip_utc TIMESTAMPTZ, code_commit TEXT, code_tag TEXT""",
    }
    for t, d in ddl.items():
        cur.execute(f"CREATE TABLE IF NOT EXISTS {t} ({d})")
    cur.execute("SELECT lock_sha256, locked_at FROM ledger_lock WHERE season = %s", (S,))
    old = cur.fetchone()
    if old and not args.relock:
        raise SystemExit(f"{S} is already locked ({old[1]}, {old[0][:16]}...): --relock replaces it, before the first tip only")
    if old:
        log(f"RELOCK: replacing the lock of {old[1]} ({old[0]}); any hash already sent is now stale")
    for t in ddl:
        cur.execute(f"DELETE FROM {t} WHERE season = %s", (S,))

    def put(table, frame):
        cols = LL.TABLES[table][0]
        f = frame.assign(season=S)
        vals = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)
                      for v in rec) for rec in f[cols].itertuples(index=False, name=None)]
        execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s", vals, page_size=2000)
    put("ledger_meta", pd.DataFrame(meta, columns=["key", "value", "note"]))
    sc = sched.copy()
    sc["home_b2b"] = sc.home_b2b.astype(object).where(sc.counted, None)
    sc["away_b2b"] = sc.away_b2b.astype(object).where(sc.counted, None)
    put("ledger_schedule", sc)
    rr = ros.copy()
    rr["player_id"] = rr.player_id.astype(object)
    rr["experience"] = [int(x) if pd.notna(x) else None for x in rr.experience]
    for c in ("proj_min", "proj_bpm", "minutes", "contribution"):
        rr[c] = rr[c].map(LL.rnd)
    rr["counted"] = rr.counted.fillna(False).astype(bool)
    put("ledger_rosters", rr)
    h = hc.copy()
    for c in ("team_bpm", "team_bpm_centred", "gap_minutes", "srs_prev", "srs_final", "pred_as_is", "pred_roster_loso",
              "exp_wins_as_is", "exp_wins_roster"):
        h[c] = h[c].map(LL.rnd)
    put("ledger_hindcast", h.assign(season=S))
    f = fc.copy()
    for c in LL.TABLES["ledger_forecasts"][0]:
        if c not in f:
            f[c] = None
    f = f.astype(object).where(f.notna(), None)
    put("ledger_forecasts", f)
    conn.commit()
    data = LL.canonical_csv(cur, S)
    digest = LL.sha256(data)
    cur.execute("INSERT INTO ledger_lock VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (S, digest, len(data), data.count(b"\n"), locked_at, first_tip.to_pydatetime(), code["head"], LL.LOCK_TAG))
    conn.commit()
    path = args.csv or os.path.join(os.environ.get("TMPDIR", tempfile.gettempdir()), f"ledger_{S - 1}-{str(S)[-2:]}_lock.csv")
    with open(path, "wb") as fh:
        fh.write(data)
    log(f"LOCKED {S - 1}-{str(S)[-2:]} at {locked_at.isoformat()}: sha256 {digest} ({len(data):,} bytes, "
        f"{data.count(b'\n'):,} lines) -> {path}")


def verify(conn, csv_path=None):
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.ledger_lock')")
    if cur.fetchone()[0] is None:
        log(f"no ledger tables yet: run ledger_lock.py --lock once before {LL.SEASON - 1}-{str(LL.SEASON)[-2:]}'s first tip")
        return 0
    cur.execute("SELECT season, lock_sha256, locked_at FROM ledger_lock ORDER BY season")
    bad = 0
    for season, digest, locked_at in cur.fetchall():
        data = LL.canonical_csv(cur, season)
        ok = LL.sha256(data) == digest
        bad += not ok
        log(f"{season}: locked {locked_at}, sha256 {digest} {'reproduced' if ok else 'DOES NOT MATCH ' + LL.sha256(data)}")
        if csv_path:
            with open(csv_path, "wb") as fh:
                fh.write(data)
    if bad:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    main()
