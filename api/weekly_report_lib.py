"""The weekly guide report (round 9 step 6): what the models said against what happened, one week of the live season
at a time. Shared by scripts/weekly_report.py (docs/weekly/<week end>.md) and GET /ledger/weekly (the printable
Weekly report tab of Teams > Forecast Ledger), so the file and the page can't disagree. Reads only; computes no
forecast (the forecasts are the ledger's logged rows: api/ledger_live.py picks and scores them).

A week = the seven US Eastern dates ending on `end` (a Sunday by default: weeks run Monday to Sunday, the first one
is opening week, Tuesday 2026-10-20 to Sunday 2026-10-25). Everything is "as of the end of the week": games dated
up to `end`, so a report for a past week, built again later, gives the same numbers (unless ESPN corrects a final or
a missed day's odds are recomputed later; both are labelled on the Live scoring tab too).

Sections:
  ledger.season / ledger.week   per version, on the games final and scored under all five versions (ledger_live.common,
                                the like-for-like set the paired tests use): games, Brier and log loss with a 95%
                                interval (games resampled, 2,000 times, seeded by season, week and version), the share
                                of games the favourite won
  ledger.tests                  the paired tests ledger_update.py stored on the morning after the week (ledger_tests,
                                as_of = the latest run date <= end + 1 day; none before 100 games: MIN_TEST_GAMES)
  ledger.misses                 the week's five games the in-season roster-aware odds got most wrong (log loss), with
                                every version's P(home wins)
  standings                     per team: record through the week, the expected final wins of both forecasts on the
                                morning after the week (ledger_team_log) against the locked mean and 80% range, and the
                                change over the week (against the morning the week began)
  notable                       the week's biggest moves in expected wins; the week's best game and biggest upset by the
                                app's own held-out pre-game odds (week_lib, the Dashboard's "This week"); the Rating
                                Tracker's top ten for the season so far (player_rating_tracker, filtered)
"""

import hashlib
from datetime import timedelta, timezone

import numpy as np
import pandas as pd

import ledger_live as LV
import week_lib

RESAMPLES = 2_000
WEEK_DAYS = 7
MISSES = 5
MOVERS = 5
TRACKER_TOP = 10
TRACKER_MIN_GAMES = 5      # a rating needs a few games before it is listed (the tracker's own interval says the rest)
HEADLINE_PAIRS = (("as_is", "record"), ("roster", "as_is"), ("roster", "record"))


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _seed(*parts):
    return int(hashlib.md5(":".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _exists(cur, t):
    cur.execute("SELECT to_regclass(%s)", (t,))
    return cur.fetchone()[0] is not None


def last_sunday(today):
    """The latest Sunday strictly before `today`: a Monday's report covers the week that ended the day before."""
    return today - timedelta(days=today.weekday() + 1)


def weeks(conn, season):
    """Every week (Monday-Sunday, by its Sunday) that has at least one final counting game, oldest first."""
    cur = conn.cursor()
    if not LV.live_tables_exist(cur):
        return []
    cur.execute("SELECT DISTINCT game_date FROM ledger_results WHERE season = %s AND counts AND completed AND home_pts IS NOT NULL",
                (season,))
    ends = sorted({d + timedelta(days=6 - d.weekday()) for (d,) in cur.fetchall()})
    conn.rollback()
    return ends


def _bootstrap(b, ll, seed):
    """95% percentile interval of the mean Brier and log loss, games resampled."""
    n = len(b)
    if n < 2:
        return (None, None), (None, None)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(RESAMPLES, n))
    mb, ml = b[idx].mean(axis=1), ll[idx].mean(axis=1)
    q = lambda x: (float(np.quantile(x, 0.025)), float(np.quantile(x, 0.975)))   # noqa: E731
    return q(mb), q(ml)


def scores(df, season, end, scope):
    """Per version (ledger_live.VERSIONS order): n, Brier and log loss with intervals, favourite won share."""
    out = []
    for v in LV.VERSIONS:
        d = df[df.version == v].sort_values(["game_date", "espn_id"])
        if not len(d):
            continue
        b, ll = LV.losses(d.p, d.y)
        (blo, bhi), (llo, lhi) = _bootstrap(b, ll, _seed(season, end, scope, v))
        fav = np.where(d.p >= 0.5, d.y, 1 - d.y)
        out.append({"version": v, "label": LV.VERSION_LABELS[v], "n": int(len(d)),
                    "n_before_tip": int(d.before_tip.sum()), "brier": float(b.mean()), "brier_lo": blo, "brier_hi": bhi,
                    "log_loss": float(ll.mean()), "log_loss_lo": llo, "log_loss_hi": lhi, "favourite_won": float(fav.mean())})
    return out


def _tests(cur, season, end):
    cur.execute("SELECT MAX(as_of) FROM ledger_tests WHERE season = %s AND as_of <= %s", (season, end + timedelta(days=1)))
    as_of = cur.fetchone()[0]
    if as_of is None:
        return None, []
    cur.execute("""SELECT metric, model_a, model_b, variant, n, value_a, value_b, diff, ci_lo, ci_hi, p_boot, p_perm, dm_p
                   FROM ledger_tests WHERE season = %s AND as_of = %s ORDER BY variant, model_a, model_b, metric""",
                (season, as_of))
    cols = [c.name for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    order = {p: i for i, p in enumerate(HEADLINE_PAIRS + tuple(p for p in LV.PAIRS if p not in HEADLINE_PAIRS))}
    for r in rows:
        r["headline"] = (r["model_a"], r["model_b"]) in HEADLINE_PAIRS
    rows.sort(key=lambda r: (r["variant"], order.get((r["model_a"], r["model_b"]), 99), r["metric"]))
    return as_of, rows


def _misses(week_df):
    if not len(week_df):
        return []
    wide = week_df.pivot_table(index="espn_id", columns="version", values="p", aggfunc="first")
    base = week_df.drop_duplicates("espn_id").set_index("espn_id")[["game_date", "home", "away", "home_pts", "away_pts", "y"]]
    early = week_df[week_df.version.isin(LV.IN_SEASON)].groupby("espn_id").before_tip.all()
    t = base.join(wide).join(early)
    if "roster" not in t:
        return []
    _, ll = LV.losses(t.roster, t.y)
    t["roster_log_loss"] = ll
    t = t.reset_index().sort_values(["roster_log_loss", "espn_id"], ascending=[False, True]).head(MISSES)
    out = []
    for r in t.to_dict("records"):
        home_won = bool(r["y"])
        p_win = r["roster"] if home_won else 1 - r["roster"]
        out.append({"espn_id": r["espn_id"], "date": str(r["game_date"]), "home": r["home"], "away": r["away"],
                    "pts_home": int(r["home_pts"]), "pts_away": int(r["away_pts"]), "winner": r["home"] if home_won else r["away"],
                    "winner_chance_roster": float(p_win), "roster_log_loss": float(r["roster_log_loss"]),
                    "before_tip": bool(r["before_tip"]),
                    **{v: (None if pd.isna(r.get(v)) else float(r[v])) for v in LV.VERSIONS}})
    return out


def _team_morning(conn, season, morning):
    """ledger_team_log of the latest morning <= `morning` (the date it is, or None)."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(as_of) FROM ledger_team_log WHERE season = %s AND as_of <= %s", (season, morning))
    as_of = cur.fetchone()[0]
    if as_of is None:
        return None, pd.DataFrame()
    return as_of, pd.read_sql("SELECT * FROM ledger_team_log WHERE season = %s AND as_of = %s", conn, params=(season, as_of))


def _standings(conn, season, start, end):
    lk = pd.read_sql("""SELECT forecast, key AS team, conference, mean_wins, wins_p10, wins_p90, p_playoffs FROM ledger_forecasts
                        WHERE season = %s AND kind = 'team' ORDER BY key""", conn, params=(season,))
    now_as_of, now = _team_morning(conn, season, end + timedelta(days=1))
    was_as_of, was = _team_morning(conn, season, start)
    res = pd.read_sql("""SELECT home, away, home_pts, away_pts FROM ledger_results WHERE season = %s AND counts AND completed
                         AND home_pts IS NOT NULL AND game_date <= %s""", conn, params=(season, end))
    wins = res.home.where(res.home_pts > res.away_pts, res.away).value_counts()
    games = pd.concat([res.home, res.away]).value_counts()
    rows = []
    for team, g in lk.groupby("team"):
        row = {"team": team, "conference": g.conference.iloc[0], "games": int(games.get(team, 0)),
               "wins": int(wins.get(team, 0))}
        row["losses"] = row["games"] - row["wins"]
        for r in g.itertuples():
            cur_ = now[(now.team == team) & (now.forecast == r.forecast)] if len(now) else now
            old = was[(was.team == team) & (was.forecast == r.forecast)] if len(was) else was
            exp = float(cur_.exp_final_wins.iloc[0]) if len(cur_) else None
            prev = float(old.exp_final_wins.iloc[0]) if len(old) else None
            row[r.forecast] = {"locked_mean": float(r.mean_wins), "locked_p10": float(r.wins_p10), "locked_p90": float(r.wins_p90),
                               "locked_p_playoffs": float(r.p_playoffs), "exp_final_wins": exp,
                               "vs_lock": None if exp is None else exp - float(r.mean_wins),
                               "week_change": None if exp is None or prev is None else exp - prev,
                               "outside_range": None if exp is None else bool(exp < r.wins_p10 or exp > r.wins_p90)}
        rows.append(row)
    key = lambda r: (r["conference"], -(r["roster"]["exp_final_wins"] if r["roster"]["exp_final_wins"] is not None  # noqa: E731
                                        else r["roster"]["locked_mean"]), r["team"])
    rows.sort(key=key)
    return {"as_of": None if now_as_of is None else now_as_of.isoformat(),
            "was_as_of": None if was_as_of is None else was_as_of.isoformat(), "teams": rows}


def _tracker(cur, season):
    if not _exists(cur, "player_rating_tracker"):
        return []
    cur.execute("""SELECT t.player_id, COALESCE(n.player_name, t.player_id::text), t.teams, t.games, t.rapm, t.rapm_ci_low,
                          t.rapm_ci_high
                   FROM player_rating_tracker t
                   LEFT JOIN (SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                              WHERE season = %s ORDER BY player_id, player_name) n ON n.player_id = t.player_id
                   WHERE t.kind = 'filtered' AND t.season = %s AND t.games >= %s
                   ORDER BY t.rapm DESC, t.player_id LIMIT %s""", (season, season, TRACKER_MIN_GAMES, TRACKER_TOP))
    return [{"player_id": int(pid), "player": name, "teams": teams, "games": int(g), "rating": float(r),
             "ci_lo": None if lo is None else float(lo), "ci_hi": None if hi is None else float(hi)}
            for pid, name, teams, g, r, lo, hi in cur.fetchall()]


def build(conn, season, end):
    """The report for the week ending `end` (a date). Reads only."""
    start = end - timedelta(days=WEEK_DAYS - 1)
    cur = conn.cursor()
    out = {"season": season, "label": season_label(season), "start": start.isoformat(), "end": end.isoformat(),
           "resamples": RESAMPLES, "min_test_games": LV.MIN_TEST_GAMES, "version_labels": LV.VERSION_LABELS,
           "live_tables": LV.live_tables_exist(cur)}
    cur.execute("SELECT locked_at, lock_sha256, code_tag FROM ledger_lock WHERE season = %s", (season,))
    lk = cur.fetchone()
    out["lock"] = None if lk is None else {"locked_at": lk[0].astimezone(timezone.utc).strftime("%Y-%m-%d"),
                                            "sha256": lk[1], "code_tag": lk[2]}
    if not out["live_tables"]:
        conn.rollback()
        return {**out, "games_week": 0, "games_season": 0, "ledger": None, "standings": None, "notable": None}
    df = LV.scored(conn, season)
    df["game_date"] = pd.to_datetime(df.game_date).dt.date
    df = df[df.game_date <= end]
    both = LV.common(df)
    wk = both[both.game_date >= start]
    cur.execute("""SELECT COUNT(*) FILTER (WHERE game_date BETWEEN %s AND %s), COUNT(*),
                          MAX(game_date)
                   FROM ledger_results WHERE season = %s AND counts AND completed AND home_pts IS NOT NULL AND game_date <= %s""",
                (start, end, season, end))
    n_week, n_season, last = cur.fetchone()
    cur.execute("""SELECT run_id, started_at, today_et, new_rows, late_rows, scored_common, waiting FROM ledger_runs
                   WHERE season = %s AND today_et <= %s ORDER BY started_at DESC LIMIT 1""", (season, end + timedelta(days=1)))
    r = cur.fetchone()
    last_run = None if r is None else {"run_id": r[0], "started_at": r[1].isoformat(), "today_et": r[2].isoformat(),
                                      "new_rows": r[3], "late_rows": r[4], "scored_common": r[5], "waiting": r[6]}
    tests_as_of, tests = _tests(cur, season, end)
    standings = _standings(conn, season, start, end)
    movers = []
    for t in standings["teams"]:
        ch = t["roster"]["week_change"]
        if ch is not None:
            movers.append({"team": t["team"], "conference": t["conference"], "change": ch,
                           "exp_final_wins": t["roster"]["exp_final_wins"], "wins": t["wins"], "losses": t["losses"]})
    movers.sort(key=lambda m: (-abs(m["change"]), m["team"]))
    wk_games = week_lib.week_games(cur, season, start, end)
    conn.rollback()
    in_week = df[df.game_date >= start]
    return {**out, "games_week": int(n_week), "games_season": int(n_season), "last_game": None if last is None else last.isoformat(),
            "last_run": last_run,
            "ledger": {"common_season": int(both.espn_id.nunique()), "common_week": int(wk.espn_id.nunique()),
                       "season": scores(both, season, end, "season"), "week": scores(wk, season, end, "week"),
                       "tests_as_of": None if tests_as_of is None else tests_as_of.isoformat(), "tests": tests,
                       "misses": _misses(in_week[in_week.espn_id.isin(in_week[in_week.version == "roster"].espn_id)]),
                       "recomputed_week": int((~in_week[in_week.version.isin(LV.IN_SEASON)].groupby("espn_id").before_tip.all()).sum())},
            "standings": standings,
            "notable": {"movers": movers[:MOVERS], "best_game": wk_games["best_game"], "biggest_upset": wk_games["biggest_upset"],
                        "app_games_week": wk_games["games"], "tracker": _tracker(conn.cursor(), season)}}
