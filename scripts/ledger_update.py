"""
ledger_update.py
=================
The Forecast Ledger's nightly update (round 6 step 2; Teams > Forecast Ledger,
Live scoring tab). Scores the 2026-27 season against the forecasts locked by
scripts/ledger_lock.py, with the code frozen at the lock's git tag.

Each run, in order:

1. Frozen code: `git archive <tag> api/ledger_lib.py api/season_sim_lib.py
   api/luck_lib.py` into a temporary folder that goes first on sys.path, so
   every forecast number comes from the tagged files, never the working tree.
   Their blob ids must equal the ones the lock stored (ledger_meta.frozen_files)
   and the tag must point at the lock's code_commit; the stored lock must still
   reproduce its SHA-256 (canonical CSV from the frozen ledger_lib). Any
   mismatch stops the run before anything is written.

2. ESPN: every regular-season event of the season (one scoreboard request per
   date of ESPN's calendar, as the lock read the schedule), with status and
   final score, upserted into ledger_results. An event counts in the standings
   when both teams are known and it isn't the NBA Cup Championship ("The
   championship game does not count as a regular season game", Wikipedia "NBA
   Cup", read 2026-09-30); the Cup's quarterfinals and semifinals and the games
   the NBA adds after the group stage count, as game_scores has them for
   2025-26. An ESPN id listed on two dates (a postponement) keeps its final
   row, else its latest date. `fetch_game_scores.py` can't be used: it matches
   ESPN to team_game_fatigue, which comes from stats.nba.com (unreachable here).

3. Odds, by the rule locked in ledger_meta.rule_in_season: for every date D up
   to today (US Eastern) with games that count, and only when every counting
   game dated before D is final, postponed or cancelled:
     played  = the final scores of counting games dated before D
     ratings = ledger_lib.ratings_on(played, teams, prior_for(locked prior
               means, prior_var_<forecast>, hca_prev, sigma_prev, hca_n0))
     odds    = ledger_lib.game_odds(D's games, ratings, locked pregame_beta),
               back-to-backs from the dates games were actually played
               (ledger_lib.add_b2b over every event with both teams known,
               postponed and cancelled ones left out)
     record  = season_sim_lib.record_p_matrix (log5 of win % before D)
   One row per game and version is appended to ledger_game_log with the run
   time, whether that is before ESPN's tip time, how many results it used, the
   latest result date (always before D) and a digest of those results. A row
   is appended only if no row for that game, version, date and digest exists,
   so rerunning a night adds nothing. Dates the update didn't run are
   computed on the next run by the same rule: before_tip = false says so.

4. Standings and projections as of this morning, per forecast and team
   (ledger_team_log): record, rating and SD, and expected final wins = wins +
   the sum of P(win) over every remaining game (today's ratings, the frozen
   game_odds) + the games still owed to reach 82 against a league-average
   opponent (ledger_lib.placeholder_games; played neutral-site games are
   passed in as venue-0 rows so they count toward the 82).

5. Paired tests (ledger_tests), once at least MIN_TEST_GAMES (100) games are
   scored under every version: paper_tests.paired_rows for each pair in
   ledger_live.PAIRS on Brier and log loss, 95% interval from 10,000 bootstrap
   resamples of games, sign-flip p and Diebold-Mariano p (task 'pregame', as
   in paper_eval_tests); twice: on all scored games, and on the games whose
   in-season odds were all logged before tip. Replaced per (season, as_of).

6. A row in ledger_runs (what was fetched, logged, waited for).

Judgment calls (also on the Methodology card): "today" is the US Eastern date,
the one ESPN's scoreboard files games under; a date waits while any earlier
counting game is still scheduled or in progress; the official number for a
game is the earliest row logged for the date it was played; no interval before
100 common games.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 ledger_update.py              # fetch, log, score (~1 min)
    cd scripts && python3 ledger_update.py --dry-run    # everything, writes nothing
    cd scripts && python3 ledger_update.py --offline    # no ESPN read: log/score from stored results
    cd scripts && python3 ledger_update.py --dry-run --today 2026-10-21   # pretend date (dry run only)
"""

import argparse
import atexit
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FROZEN_MODULES = ["api/ledger_lib.py", "api/season_sim_lib.py", "api/luck_lib.py"]
DEFAULT_TAG = "ledger-2026-27"
SEASON = 2027
EASTERN = ZoneInfo("America/New_York")
RESAMPLES = 10_000
CUP_FINAL = "NBA Cup Championship"
NOT_PLAYED = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED", "STATUS_FORFEIT")

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")


def log(msg):
    print(msg, flush=True)


def git(*args, text=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip() if text else r.stdout


def load_frozen(tag):
    """Import ledger_lib, season_sim_lib and luck_lib from the tag. Returns (LL, L, luck, blobs, commit)."""
    for m in ("ledger_lib", "season_sim_lib", "luck_lib"):
        if m in sys.modules:
            raise SystemExit(f"{m} was imported before the frozen copy: it would come from the working tree")
    tmp = tempfile.mkdtemp(prefix="ledger_frozen_")
    atexit.register(shutil.rmtree, tmp, True)
    data = git("archive", tag, *FROZEN_MODULES, text=False)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(tmp, filter="data")
    sys.path.insert(0, os.path.join(tmp, "api"))
    import ledger_lib as LL
    import luck_lib as luck
    import season_sim_lib as L
    for mod in (LL, L, luck):
        if not os.path.realpath(mod.__file__).startswith(os.path.realpath(tmp)):
            raise SystemExit(f"{mod.__name__} imported from {mod.__file__}, not the tag")
    blobs = {f: git("rev-parse", f"{tag}:{f}") for f in FROZEN_MODULES}
    return LL, L, luck, blobs, git("rev-parse", f"{tag}^{{commit}}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="compute everything, write nothing")
    ap.add_argument("--offline", action="store_true", help="don't read ESPN; use the stored ledger_results")
    ap.add_argument("--today", help="with --dry-run: pretend today (US Eastern) is this date, YYYY-MM-DD")
    ap.add_argument("--resamples", type=int, default=RESAMPLES)
    ap.add_argument("--tag", default=DEFAULT_TAG)
    args = ap.parse_args()
    if args.today and not args.dry_run:
        raise SystemExit("--today only with --dry-run: logged rows always carry the real time")

    LL, L, luck, blobs, tag_commit = load_frozen(args.tag)
    # Import after the frozen modules are in sys.modules; nothing below imports the working-tree copies.
    import psycopg2
    from psycopg2.extras import execute_values

    import ledger_espn as E
    import paper_tests as PT
    from db_config import DB_CONFIG
    sys.path.append(str(ROOT / "api"))
    import ledger_live as LV

    t0 = time.time()
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc)
    today = date.fromisoformat(args.today) if args.today else started.astimezone(EASTERN).date()
    season = LL.SEASON
    if season != SEASON:
        raise SystemExit(f"the tag's ledger_lib locks season {season}, this script scores {SEASON}")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # 1. the lock and the frozen code
    lock = read_lock(cur, LL, season)
    stored_blobs = json.loads(lock["meta"]["frozen_files"])
    bad = [f for f in FROZEN_MODULES if stored_blobs.get(f) != blobs[f]]
    if bad or tag_commit != lock["meta"]["code_commit"]:
        raise SystemExit(f"tag {args.tag} ({tag_commit[:12]}) doesn't hold the locked code: {bad or 'commit differs'}")
    log(f"frozen code: {args.tag} = {tag_commit[:12]}, {len(blobs)} files match the lock's blob ids; "
        f"lock sha256 {lock['sha256'][:16]}... reproduced")

    # 2. ESPN
    if args.offline:
        res = pd.read_sql("SELECT * FROM ledger_results WHERE season = %s", conn, params=(season,)) \
            if LV.live_tables_exist(cur) else pd.DataFrame()
        if not len(res):
            raise SystemExit("--offline: no stored ledger_results yet")
        log(f"offline: {len(res):,} stored events")
    else:
        res = fetch_events(E, season, set(lock["schedule_ids"]))
        log(f"ESPN: {len(res):,} regular-season events, {int(res.counts.sum()):,} count, "
            f"{int((res.counts & res.completed).sum()):,} final, {int(res.status.isin(NOT_PLAYED).sum())} postponed/cancelled "
            f"({time.time() - t0:.0f}s)")
    res["game_date"] = pd.to_datetime(res.game_date).dt.date

    # 3. odds for every date that is due
    prior = priors(LL, lock)
    beta = lock["beta"]
    teams = lock["teams"]
    sched = schedule_frame(LL, res)
    existing = existing_keys(cur, LV, season)
    rows, waiting = [], None
    due = sorted(d for d in sched[sched.counts].game_date.unique() if d <= today)
    for d in due:
        before = res[res.counts & (res.game_date < d)]
        open_ = before[~before.completed & ~before.status.isin(NOT_PLAYED)]
        if len(open_):
            waiting = (f"{d}: waiting for {len(open_)} earlier game(s) to go final "
                       f"({', '.join(f'{r.away}@{r.home} {r.game_date}' for r in open_.head(3).itertuples())})")
            break
        day = sched[sched.counts & (sched.game_date == d) & ~sched.status.isin(NOT_PLAYED)]
        new = odds_for_date(LL, L, luck, d, day, before[before.completed], teams, prior, beta, season)
        for r in new:
            if (r["espn_id"], r["forecast"], r["game_date"], r["results_digest"]) not in existing:
                rows.append(r)
    now = datetime.now(timezone.utc)
    for r in rows:
        r.update(computed_at=now, run_id=run_id, code_tag=args.tag,
                 before_tip=bool(now < pd.Timestamp(r["tip_utc"]).to_pydatetime()))
    late = sum(1 for r in rows if not r["before_tip"])
    log(f"odds: {len(due)} dates due through {today}, {len(rows)} new rows ({late} after tip: recomputed)"
        + (f"; {waiting}" if waiting else ""))

    # 4. standings and projections as of this morning
    # (while a date waits for earlier finals, the standings keep their last complete morning)
    morning = today
    team_rows = [] if waiting else team_log(LL, L, luck, morning, sched, res, teams, prior, beta, season)

    if args.dry_run:
        for r in rows[:6]:
            log(f"  {r['game_date']} {r['away']}@{r['home']} {r['forecast']:>7}: p_home {r['p_home']:.4f} "
                f"(results used {r['games_used']}, last {r['last_result_date']}, before tip {r['before_tip']})")
        show = pd.DataFrame(team_rows)
        if len(show):
            log(show[show.forecast == "roster"].sort_values("exp_final_wins", ascending=False)
                .head(5)[["team", "wins", "losses", "rating", "exp_final_wins"]].to_string(index=False))
        log(f"dry run: nothing written ({time.time() - t0:.0f}s)")
        return 0

    ensure_tables(cur)
    if not args.offline:
        upsert_results(cur, execute_values, res, season, started)
    if rows:
        cols = GAME_LOG_COLS
        execute_values(cur, f"INSERT INTO ledger_game_log ({', '.join(cols)}) VALUES %s",
                       [tuple(py(r.get(c, season if c == "season" else None)) for c in cols) for r in rows], page_size=2000)
    cur.execute("DELETE FROM ledger_team_log WHERE season = %s AND as_of = %s", (season, morning))
    if team_rows:
        execute_values(cur, f"INSERT INTO ledger_team_log ({', '.join(TEAM_LOG_COLS)}) VALUES %s",
                       [tuple(py({**r, "season": season, "computed_at": now}.get(c)) for c in TEAM_LOG_COLS) for r in team_rows])
    conn.commit()

    # 5. paired tests on everything scored so far
    n_common, tests = paired_tests(conn, LV, PT, season, today, args.resamples)
    cur.execute("DELETE FROM ledger_tests WHERE season = %s AND as_of = %s", (season, today))
    if tests:
        cols = ["season", "as_of", "computed_at", *PT.Rows.COLS]
        execute_values(cur, f"INSERT INTO ledger_tests ({', '.join(cols)}) VALUES %s",
                       [(season, today, now, *t) for t in tests])
    log(f"scored: {n_common} games under every version; "
        + (f"{len(tests)} paired tests stored" if tests else f"no interval yet (fewer than {LV.MIN_TEST_GAMES})"))

    # 6. the run
    finished = datetime.now(timezone.utc)
    cur.execute("""INSERT INTO ledger_runs (season, run_id, started_at, finished_at, today_et, mode, espn_events,
                   events_final, new_rows, late_rows, scored_common, waiting, code_tag, code_commit)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (season, run_id, started, finished, today, "offline" if args.offline else "espn", int(len(res)),
                 int((res.counts & res.completed).sum()), len(rows), late, n_common, waiting, args.tag, tag_commit))
    conn.commit()
    log(f"run {run_id} done in {time.time() - t0:.0f}s")
    return 0


# ── the lock ────────────────────────────────────────────────────────────────

def read_lock(cur, LL, season):
    cur.execute("SELECT lock_sha256 FROM ledger_lock WHERE season = %s", (season,))
    got = cur.fetchone()
    if not got:
        raise SystemExit(f"no lock for {season}: scripts/ledger_lock.py --lock runs first")
    digest = got[0]
    if LL.sha256(LL.canonical_csv(cur, season)) != digest:
        raise SystemExit("the stored lock no longer reproduces its SHA-256: stop and investigate before scoring anything")
    cur.execute("SELECT key, value FROM ledger_meta WHERE season = %s", (season,))
    meta = dict(cur.fetchall())
    cur.execute("SELECT forecast, key, prior_mean FROM ledger_forecasts WHERE season = %s AND kind = 'team'", (season,))
    means = {}
    for f, t, m in cur.fetchall():
        means.setdefault(f, {})[t] = float(m)
    cur.execute("SELECT espn_id FROM ledger_schedule WHERE season = %s AND counted", (season,))
    ids = [r[0] for r in cur.fetchall()]
    beta = {k: float(v) for k, v in json.loads(meta["pregame_beta"]).items()}
    return {"sha256": digest, "meta": meta, "means": means, "beta": beta, "schedule_ids": ids,
            "teams": sorted(means["as_is"])}


def priors(LL, lock):
    m = lock["meta"]
    return {f: LL.prior_for(lock["means"][f], float(m[f"prior_var_{f}"]), m["hca_prev"], m["sigma_prev"], m["hca_n0"])
            for f in LL.FORECASTS}


# ── ESPN ────────────────────────────────────────────────────────────────────

def fetch_events(E, season, lock_ids):
    """Every regular-season event with status and score (one row per ESPN id)."""
    dates = E.calendar_dates(season)

    def one(day):
        return day, E._get(E.SCOREBOARD, {"dates": day, "limit": 200}).get("events", [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(one, dates))
    rows = []
    for day, events in got:
        for e in events:
            s = e.get("season", {})
            if int(s.get("year", 0)) != season or int(s.get("type", 0)) != E.REGULAR_SEASON:
                continue
            r = E._event_row(day, e)
            comp = e["competitions"][0]
            st = comp.get("status", {})
            sides = {c["homeAway"]: c for c in comp["competitors"]}
            r["completed"] = bool(st.get("type", {}).get("completed")) and r["status"] == "STATUS_FINAL"
            r["periods"] = st.get("period")

            def pts(side):
                v = sides.get(side, {}).get("score")
                return int(float(v)) if r["completed"] and v not in (None, "") else None
            r["home_pts"], r["away_pts"] = pts("home"), pts("away")
            rows.append(r)
    df = pd.DataFrame(rows)
    # One row per id: a final row wins, else the latest date (a postponed game moved to a new date).
    df = df.sort_values(["espn_id", "completed", "game_date", "tip_utc"]).drop_duplicates("espn_id", keep="last")
    df["counts"] = df.home.notna() & df.away.notna() & ~df.note.fillna("").str.contains(CUP_FINAL)
    df["in_lock"] = df.espn_id.isin(lock_ids)
    missing = set(lock_ids) - set(df.espn_id)
    if missing:
        log(f"  note: {len(missing)} locked game(s) no longer on ESPN's schedule: {sorted(missing)[:5]}")
    return df.sort_values(["game_date", "tip_utc", "espn_id"]).reset_index(drop=True)


# ── odds ────────────────────────────────────────────────────────────────────

def schedule_frame(LL, res):
    """Events with both teams known, back-to-backs from the dates actually played or scheduled
    (postponed / cancelled events left out), venue 1 or 0 (neutral) for the counting ones."""
    s = res[res.home.notna() & res.away.notna() & ~res.status.isin(NOT_PLAYED)].copy()
    s = LL.add_b2b(s.assign(counted=True))
    s["game_date"] = pd.to_datetime(s.game_date).dt.date
    s["venue"] = np.where(s.neutral_site, 0, 1)
    s["home_b2b"] = s.home_b2b.astype(bool)
    s["away_b2b"] = s.away_b2b.astype(bool)
    keep = ["espn_id", "game_date", "tip_utc", "home", "away", "venue", "home_b2b", "away_b2b", "counts", "status",
            "completed", "neutral_site"]
    return pd.concat([s[keep], res[res.status.isin(NOT_PLAYED) & res.counts].assign(
        venue=lambda x: np.where(x.neutral_site, 0, 1), home_b2b=False, away_b2b=False)[keep]], ignore_index=True)


def played_rows(luck, finals, season):
    """Two team-game rows per final (luck_lib.prepare format)."""
    if not len(finals):
        return None
    base = {"game_id": finals.espn_id.to_numpy(), "season": season, "game_date": finals.game_date.to_numpy(),
            "neutral_site": finals.neutral_site.astype(bool).to_numpy()}
    h = pd.DataFrame({**base, "team_abbreviation": finals.home.to_numpy(), "opponent": finals.away.to_numpy(),
                      "is_home": True, "pts_for": finals.home_pts.to_numpy(float), "pts_against": finals.away_pts.to_numpy(float)})
    a = pd.DataFrame({**base, "team_abbreviation": finals.away.to_numpy(), "opponent": finals.home.to_numpy(),
                      "is_home": False, "pts_for": finals.away_pts.to_numpy(float), "pts_against": finals.home_pts.to_numpy(float)})
    return luck.prepare(pd.concat([h, a], ignore_index=True).sort_values(["game_date", "game_id", "team_abbreviation"]))


def results_digest(finals):
    s = "\n".join(sorted(f"{i},{int(h)},{int(a)}" for i, h, a in zip(finals.espn_id, finals.home_pts, finals.away_pts)))
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def odds_for_date(LL, L, luck, d, day, finals, teams, prior, beta, season):
    """Log rows (not yet stamped) for the counting games on date d, from the finals before it."""
    if not len(day):
        return []
    played = played_rows(luck, finals, season)
    played = LL.EMPTY_PLAYED if played is None else played
    digest = results_digest(finals)
    last = max(finals.game_date) if len(finals) else None
    base = [{"espn_id": r.espn_id, "game_date": d, "tip_utc": r.tip_utc, "home": r.home, "away": r.away,
             "venue": int(r.venue), "home_b2b": bool(r.home_b2b), "away_b2b": bool(r.away_b2b),
             "games_used": int(len(finals)), "last_result_date": last, "results_digest": digest}
            for r in day.itertuples()]
    out = []
    for f in LL.FORECASTS:
        pr, prm = prior[f]
        rat = LL.ratings_on(played, teams, pr, prm)
        em, p = LL.game_odds(day, rat, beta)
        for b, r, e, q in zip(base, day.itertuples(), em, p):
            out.append({**b, "forecast": f, "r_home": rat["r_post"][r.home], "r_away": rat["r_post"][r.away],
                        "hca": rat["hca"], "exp_margin": float(e), "p_home": float(q)})
    st = L.Standings(teams, played)
    p_rec = L.record_p_matrix(day, st)(None)[0]
    wp = {t: (st.wins[i] / st.games[i] if st.games[i] else None) for t, i in st.idx.items()}
    for b, r, q in zip(base, day.itertuples(), p_rec):
        out.append({**b, "forecast": "record", "r_home": wp[r.home], "r_away": wp[r.away], "hca": None,
                    "exp_margin": None, "p_home": float(q)})
    return out


def team_log(LL, L, luck, morning, sched, res, teams, prior, beta, season):
    """Per forecast and team, as of the morning of `morning`: record, rating, expected final wins."""
    finals = res[res.counts & res.completed & (res.game_date < morning)]
    played = played_rows(luck, finals, season)
    played = LL.EMPTY_PLAYED if played is None else played
    remaining = sched[sched.counts & ~sched.espn_id.isin(finals.espn_id)]
    remaining = remaining[~remaining.completed | (remaining.game_date >= morning)]
    st = L.Standings(teams, played)
    home = finals[~finals.neutral_site.astype(bool)]
    ph, pa = home.home.value_counts().to_dict(), home.away.value_counts().to_dict()
    neutral = finals[finals.neutral_site.astype(bool)].assign(venue=0)[["home", "away", "venue"]]
    extras = LL.placeholder_games(teams, pd.concat([remaining[["home", "away", "venue"]], neutral], ignore_index=True), ph, pa)
    out = []
    for f in LL.FORECASTS:
        pr, prm = prior[f]
        rat = LL.ratings_on(played, teams, pr, prm)
        _, p = LL.game_odds(remaining, rat, beta)
        exp = pd.concat([pd.Series(p, index=remaining.home.to_numpy()).groupby(level=0).sum(),
                         pd.Series(1 - p, index=remaining.away.to_numpy()).groupby(level=0).sum()], axis=1).fillna(0).sum(axis=1)
        for t, v in extras:
            exp[t] = exp.get(t, 0.0) + float(L.win_prob(beta, rat["r_post"][t] + v * rat["hca"]))
        for t in teams:
            i = st.idx[t]
            out.append({"as_of": morning, "forecast": f, "team": t, "games": int(st.games[i]), "wins": int(st.wins[i]),
                        "losses": int(st.games[i] - st.wins[i]), "rating": float(rat["r_post"][t]),
                        "rating_sd": float(np.sqrt(rat["var_post"][t])), "hca": float(rat["hca"]),
                        "games_left": int((remaining.home == t).sum() + (remaining.away == t).sum()
                                          + sum(1 for x, _ in extras if x == t)),
                        "exp_final_wins": float(st.wins[i] + exp.get(t, 0.0)), "results_used": int(len(finals))})
    return out


# ── tests ───────────────────────────────────────────────────────────────────

def paired_tests(conn, LV, PT, season, today, resamples):
    """(games scored under every version, paper_tests rows) for LV.PAIRS on Brier and log loss."""
    if not LV.live_tables_exist(conn.cursor()):
        return 0, []
    df = LV.common(LV.scored(conn, season))
    n = int(df.espn_id.nunique())
    if n < LV.MIN_TEST_GAMES:
        return n, []
    out = PT.Rows(resamples)
    early = df[df.version.isin(LV.IN_SEASON)].groupby("espn_id").before_tip.all()
    for variant, ids in (("all", df.espn_id.unique()), ("logged_before_tip", early.index[early].to_numpy())):
        if len(ids) < LV.MIN_TEST_GAMES:
            continue
        sub = df[df.espn_id.isin(ids)]

        def series(v):
            d = sub[sub.version == v]
            return PT.Series(pd.DataFrame({"season": season, "unit_id": d.espn_id.astype("int64").to_numpy(),
                                           "pred": d.p.to_numpy(), "actual": d.y.to_numpy(), "lo": np.nan, "hi": np.nan,
                                           "unit_date": d.game_date.to_numpy(), "cluster": d.espn_id.astype("int64").to_numpy()}),
                             variant)
        for a, b in LV.PAIRS:
            for metric in LV.METRICS:
                PT.paired_rows(out, "pregame", f"ledger {today}", a, b, series(a), series(b), metric, variant,
                               note=f"Forecast Ledger {season - 1}-{str(season)[-2:]}, games scored through {today}")
    return n, out.rows


# ── storage ─────────────────────────────────────────────────────────────────

GAME_LOG_COLS = ["season", "espn_id", "forecast", "computed_at", "run_id", "game_date", "tip_utc", "before_tip", "home",
                 "away", "venue", "home_b2b", "away_b2b", "r_home", "r_away", "hca", "exp_margin", "p_home", "games_used",
                 "last_result_date", "results_digest", "code_tag"]
TEAM_LOG_COLS = ["season", "as_of", "forecast", "team", "games", "wins", "losses", "rating", "rating_sd", "hca",
                 "games_left", "exp_final_wins", "results_used", "computed_at"]
RESULT_COLS = ["season", "espn_id", "game_date", "tip_utc", "time_valid", "home", "away", "neutral_site", "venue_name",
               "city", "note", "status", "completed", "home_pts", "away_pts", "periods", "counts", "in_lock",
               "first_seen_at", "final_seen_at", "fetched_at"]


def ensure_tables(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS ledger_results (season INTEGER, espn_id TEXT, game_date DATE, tip_utc TEXT,
        time_valid BOOLEAN, home TEXT, away TEXT, neutral_site BOOLEAN, venue_name TEXT, city TEXT, note TEXT, status TEXT,
        completed BOOLEAN, home_pts INTEGER, away_pts INTEGER, periods INTEGER, counts BOOLEAN, in_lock BOOLEAN,
        first_seen_at TIMESTAMPTZ, final_seen_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ, PRIMARY KEY (season, espn_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ledger_game_log (season INTEGER, espn_id TEXT, forecast TEXT,
        computed_at TIMESTAMPTZ, run_id TEXT, game_date DATE, tip_utc TEXT, before_tip BOOLEAN, home TEXT, away TEXT,
        venue SMALLINT, home_b2b BOOLEAN, away_b2b BOOLEAN, r_home DOUBLE PRECISION, r_away DOUBLE PRECISION,
        hca DOUBLE PRECISION, exp_margin DOUBLE PRECISION, p_home DOUBLE PRECISION, games_used INTEGER,
        last_result_date DATE, results_digest TEXT, code_tag TEXT, PRIMARY KEY (season, espn_id, forecast, computed_at))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ledger_team_log (season INTEGER, as_of DATE, forecast TEXT, team TEXT,
        games INTEGER, wins INTEGER, losses INTEGER, rating DOUBLE PRECISION, rating_sd DOUBLE PRECISION,
        hca DOUBLE PRECISION, games_left INTEGER, exp_final_wins DOUBLE PRECISION, results_used INTEGER,
        computed_at TIMESTAMPTZ, PRIMARY KEY (season, as_of, forecast, team))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ledger_runs (season INTEGER, run_id TEXT PRIMARY KEY, started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ, today_et DATE, mode TEXT, espn_events INTEGER, events_final INTEGER, new_rows INTEGER,
        late_rows INTEGER, scored_common INTEGER, waiting TEXT, code_tag TEXT, code_commit TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS ledger_tests (season INTEGER, as_of DATE, computed_at TIMESTAMPTZ, task TEXT,
        phase TEXT, metric TEXT, model_a TEXT, model_b TEXT, variant TEXT, seasons TEXT, unit_type TEXT, cluster_by TEXT,
        n INTEGER, n_clusters INTEGER, value_a DOUBLE PRECISION, value_b DOUBLE PRECISION, diff DOUBLE PRECISION,
        ci_lo DOUBLE PRECISION, ci_hi DOUBLE PRECISION, p_boot DOUBLE PRECISION, p_perm DOUBLE PRECISION,
        dm_stat DOUBLE PRECISION, dm_p DOUBLE PRECISION, dm_lags INTEGER, resamples INTEGER, seed BIGINT, note TEXT,
        PRIMARY KEY (season, as_of, metric, model_a, model_b, variant))""")


def existing_keys(cur, LV, season):
    if not LV.live_tables_exist(cur):
        return set()
    cur.execute("SELECT espn_id, forecast, game_date, results_digest FROM ledger_game_log WHERE season = %s", (season,))
    return set(cur.fetchall())


def upsert_results(cur, execute_values, res, season, fetched_at):
    cur.execute("SELECT espn_id, home_pts, away_pts FROM ledger_results WHERE season = %s AND completed", (season,))
    before = {i: (h, a) for i, h, a in cur.fetchall()}
    changed = [r.espn_id for r in res[res.completed].itertuples()
               if r.espn_id in before and before[r.espn_id] != (int(r.home_pts), int(r.away_pts))]
    if changed:
        log(f"  WARNING: ESPN changed the final score of {len(changed)} game(s): {changed[:5]} (stored scores updated; "
            "odds already logged keep the digest of the results they used)")
    r = res.rename(columns={"venue": "venue_name"}).assign(season=season, fetched_at=fetched_at, first_seen_at=fetched_at,
                                                          final_seen_at=[fetched_at if c else None for c in res.completed])
    vals = [tuple(py(v) for v in rec) for rec in r[RESULT_COLS].itertuples(index=False, name=None)]
    execute_values(cur, f"""INSERT INTO ledger_results ({', '.join(RESULT_COLS)}) VALUES %s
        ON CONFLICT (season, espn_id) DO UPDATE SET game_date = EXCLUDED.game_date, tip_utc = EXCLUDED.tip_utc,
            time_valid = EXCLUDED.time_valid, home = EXCLUDED.home, away = EXCLUDED.away, neutral_site = EXCLUDED.neutral_site,
            venue_name = EXCLUDED.venue_name, city = EXCLUDED.city, note = EXCLUDED.note, status = EXCLUDED.status,
            completed = EXCLUDED.completed, home_pts = EXCLUDED.home_pts, away_pts = EXCLUDED.away_pts,
            periods = EXCLUDED.periods, counts = EXCLUDED.counts, in_lock = EXCLUDED.in_lock,
            final_seen_at = COALESCE(ledger_results.final_seen_at, EXCLUDED.final_seen_at), fetched_at = EXCLUDED.fetched_at""",
                   vals, page_size=2000)


def py(v):
    """A value psycopg2 can store: numpy scalars unwrapped, NaN / NaT / None -> NULL."""
    if v is None or v is pd.NaT:
        return None
    if hasattr(v, "item") and not isinstance(v, (pd.Timestamp, datetime, date)):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


if __name__ == "__main__":
    sys.exit(main())
