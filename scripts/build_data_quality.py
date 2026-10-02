"""
build_data_quality.py
======================
The Data Quality page (round 6, step 11): the data-quality audit's error
classes (scripts/paper_data_audit.py) turned into a flag on every game, and a
"does it matter?" check that re-scores three headline results with the
flagged games dropped.

What it does
------------
1. Re-measures the audit. Every check of paper_data_audit.py is run again here
   (its own functions, so one implementation) and the result must equal the
   stored paper_data_audit row for row; otherwise the run stops ("rerun
   paper_data_audit.py"). Each live check of api/data_quality_lib.py (the
   page's independent SQL re-measure of 14 of the 18 classes) must agree with
   the same rows at the precision the paper prints them.
2. Flags every game of the play-by-play era (lineup_stint_games: every ESPN
   regular-season game 2020-21 on, the three NBA Cup finals included) with
   the size of each per-game class in it (counts and booleans; the audit's
   per-event frames give the Python-only ones: repaired wrong-player events,
   tag/text disagreements, missed threes worded as twos, chart matches and
   clock offsets). Which classes touch a game is data_quality_lib.PER_GAME's
   SQL rule on those columns, and the game's level is the worst handling
   among them (data_quality_lib.LEVELS): excluded (no real final or does not
   reconcile) > flagged (an error left in the data: tag/text, team-less
   substitution, unidentified player, chart gap) > worked around (only errors
   the pipeline repairs or works around) > clean. wrong_team and
   age_convention are season-table classes, not per game.
3. Re-scores three results with games dropped (data_quality_lib.RESULTS):
   impact        the protocol's RAPM + BPM prior vs BPM (and one-season RAPM,
                 the Rating Tracker) on next-season game margins
                 (paper_eval impact_next). Two scopes: the dropped games
                 removed from the fitting season and the scored season
                 ("everywhere": every model refitted on the kept stints), or
                 from the scored season only ("scoring": the full-data fits).
                 Hyperparameters are held at the protocol's choices
                 (paper_eval_choices: lambda, prior scale; rating_tracker_fit:
                 the tracker's five), not re-chosen: the question is whether
                 the data errors move the result, not the tuning.
   possessions   Possession Explorer's pooled points per possession after a
                 steal vs after a made shot, and transition vs settled
                 possessions (2020-21 to 2025-26, reconciled games).
   availability  the availability-aware odds (BPM of who played) vs the
                 protocol's pre-game model by log loss, scored on the kept
                 games only (pregame_availability_odds' stored protocol
                 probabilities; the one coefficient is not refitted).
   Drop sets (data_quality_lib.drop_sets): every game; the flagged and
   excluded games; then each per-game class on its own, when it touches
   between one game and half of the result's games (a class on nearly every
   game, such as zero-distance threes, would leave nothing to compare).
   Every difference gets paper_tests' paired cluster bootstrap by game (and
   its sign-flip and Diebold-Mariano p where paper_tests computes them).
   The control: dropping games moves a result even when the games are fine
   (fewer stints to fit, fewer games to score), so every drop set of 50+
   games is also compared with 30 random drops of the same number of games
   in each season (seeded); the stored 2.5th-97.5th percentiles of their
   differences and p = (1 + random drops that move the difference at least
   as far from every game's) / 31 say whether the flagged games move it more
   than any games would. The bootstrap interval treats the fits as fixed
   (as paper_tests does); the random drops are the check on the fitting.
   With the full count of resamples the "every game" rows must equal
   paper_eval_tests / pregame_availability_tests bit for bit (same seeds:
   the variant is '' for them) and the predictions paper_eval_predictions to
   1e-6; the possession numbers must equal possession_seasons' league rows.

Judgment calls (also on the Methodology card)
  * Per-game thresholds: any event of a class touches the game, except the
    chart gap (no chart rows, or over 5% of the game's identified attempts
    unmatched) and the clock: only an event stamped outside its own period
    flags a game. ESPN's lag behind the chart (a median 4 s, fixed by
    pbp_event_clock since step 3b) is on every game, so the game's median
    offset and its shots over 5 s are stored as columns, not as a flag. A
    shot at (0, 0) is a real shot at the rim in this era (about five a game),
    so the unlocated class is not a per-game one here.
  * Dropping a game drops all of it, both teams, every possession and stint.
  * The availability odds keep their expected minutes from earlier games, some
    of them flagged: only the scored games are dropped, and the page says so.
  * Games of the results that are not in the flag table (none in practice) are
    never dropped; the meta row counts them.

Tables written (dropped and rebuilt):
  data_quality_game_flags    one row per game (7,232): ids, teams, the per-game
                             class columns, classes (text[]), n_classes, level;
  data_quality_sensitivity   paper_eval_tests' columns plus result, drop_set,
                             scope, games_dropped, games_in_scope: every model's
                             score and every pair's difference per phase, and
                             the random-drop control (rand_draws, rand_lo,
                             rand_hi, rand_p) on the differences;
  data_quality_meta          key -> JSON: the audit check, every live check's
                             values, level and flag rules, the drop sets of
                             each result with their sizes, the hyperparameters,
                             the reproduction checks, the runtime.

Runtime about 8 minutes (the audit's replay ~75 s, the impact refits with
their random drops ~3 min, the bootstraps). Deterministic (paper_tests' row
seeds; the random drops are seeded per set).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 build_data_quality.py
    cd scripts && python3 build_data_quality.py --resamples 500     # while developing (skips the bit-for-bit test check)
Rerun after paper_data_audit.py (which it must equal), paper_eval.py /
paper_tests.py, build_pregame_availability.py or build_possessions.py; then
restart impact_api (the router caches the stored tables).
"""

import argparse
import gc
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

import build_rapm as R
import paper_data_audit as DA
import paper_eval as PE
import paper_tests as PT
import rating_tracker_lib as T
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import data_quality_lib as Q  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")
T0 = time.time()
IMPACT_MODELS = ("zero", "bpm", "rapm_single", "rapm_prior", "rapm_tracker")


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def jsonable(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (frozenset, set)):
        return sorted(v)
    raise TypeError(type(v))


# ── 1. The audit, re-measured ────────────────────────────────────────────────

def remeasure(conn):
    """A fresh run of paper_data_audit's checks: (Audit, wrong-player events, tag/text events, attempts, matched attempts)."""
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    A = DA.Audit()
    DA.feed_checks(cur, A)
    DA.score_checks(cur, A)
    DA.table_checks(cur, A)
    DA.chart_checks(cur, A)
    log("audit: SQL checks")
    wrong, single = DA.identity_checks(conn, cur, A)
    log("audit: identity checks")
    attempts, matched = DA.shot_checks(conn, cur, A)
    log("audit: shot checks")
    DA.clock_lag_checks(conn, cur, A)
    log("audit: clock lag checks")
    DA.class_counts(A)
    conn.rollback()
    return A, wrong, single, attempts, matched


def check_audit(conn, A, meta):
    cur = conn.cursor()
    cur.execute("SELECT key, season, value, fmt FROM paper_data_audit")
    stored = {(k, s): (v, f) for k, s, v, f in cur.fetchall()}
    fresh = {k: v[0] for k, v in A.rows.items()}
    diff = sorted(set(stored) ^ set(fresh))
    diff += sorted(k for k in set(stored) & set(fresh)
                   if not ((stored[k][0] is None and fresh[k] is None)
                           or (stored[k][0] is not None and fresh[k] is not None and abs(stored[k][0] - fresh[k]) <= 1e-12 * max(1, abs(fresh[k])))))
    if diff:
        raise SystemExit(f"paper_data_audit is stale ({len(diff)} rows differ from a fresh run, e.g. {diff[:5]}): "
                         "rerun scripts/paper_data_audit.py first")
    meta["audit_check"] = {"rows": len(stored), "identical": True}
    log(f"audit: all {len(stored)} stored rows reproduce")
    # the page's live checks against the same rows
    live = {}
    for key, fn in Q.LIVE.items():
        t = time.time()
        res = Q.compare(fn(cur), stored)
        bad = [r for r in res if not r["ok"]]
        if bad:
            raise SystemExit(f"live check {key} disagrees with the audit: {bad[:3]}")
        live[key] = {"values": len(res), "seconds": round(time.time() - t, 2)}
    conn.rollback()
    meta["live_checks"] = live
    log(f"live checks: all {len(live)} agree with the audit ({sum(v['values'] for v in live.values())} values)")
    return stored


# ── 2. Per-game flags ────────────────────────────────────────────────────────

FLAG_SQL = """
WITH g AS (SELECT s.game_id, s.nba_game_id, s.season, s.game_date, s.home_team, s.away_team, s.game_ok, s.reason,
                  coalesce(s.bad_lineup_seconds, 0) untracked_seconds, s.stint_seconds, p.home_team pb_home, p.away_team pb_away
           FROM lineup_stint_games s JOIN pbp_games p ON p.game_id = s.game_id),
ev AS (SELECT e.game_id,
              count(*) FILTER (WHERE e.person_id IS NULL AND coalesce(e.player_name, '') <> '') unid_events,
              count(*) FILTER (WHERE e.action_type = 'Substitution' AND e.team_tricode IS NULL) teamless_subs,
              count(*) FILTER (WHERE e.period <= 4 AND (e.seconds_remaining < %(ps)s * (4 - e.period)
                                                         OR e.seconds_remaining > %(ps)s * (5 - e.period))) clock_outside_events
       FROM pbp_events e WHERE e.game_id IN (SELECT game_id FROM g) GROUP BY 1),
st AS (SELECT e.game_id, e.score_home - lag(e.score_home) OVER w dh, e.score_away - lag(e.score_away) OVER w da
       FROM pbp_events e WHERE e.game_id IN (SELECT game_id FROM g)
       WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number)),
sb AS (SELECT e.game_id, e.score_home - lag(e.score_home) OVER w dh, e.score_away - lag(e.score_away) OVER w da
       FROM pbp_events e WHERE e.game_id IN (SELECT game_id FROM g) AND e.score_home IS NOT NULL AND e.score_away IS NOT NULL
       WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number, e.id)),
steps AS (SELECT game_id, sum(greatest(dh, 0)) ph, sum(greatest(da, 0)) pa FROM st GROUP BY 1),
back AS (SELECT game_id, bool_or(dh < 0 OR da < 0) backward FROM sb GROUP BY 1),
lx AS (SELECT s.game_id, count(*) FILTER (WHERE l.seconds::numeric - s.secs > 0.2) excess
       FROM (SELECT game_id, pid, SUM(seconds::numeric) secs FROM (
                 SELECT game_id, unnest(home_ids) pid, seconds FROM lineup_stints
                 UNION ALL SELECT game_id, unnest(away_ids), seconds FROM lineup_stints) x GROUP BY 1, 2) s
       JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid GROUP BY 1),
ls AS (SELECT t.game_id, bool_or(t.pts_for <> c.pts_for OR t.pts_against <> c.pts_against) off
       FROM team_game_totals t JOIN game_scores c ON c.espn_id IS NOT NULL AND t.game_id = 'espn_' || c.espn_id
                                                  AND c.team_abbreviation = t.team_abbreviation GROUP BY 1),
sh AS (SELECT game_id, count(*) shots, count(*) FILTER (WHERE shot_type LIKE '3%%' AND shot_distance = 0) zero_dist_threes
       FROM player_shots WHERE game_id IN (SELECT nba_game_id FROM g) GROUP BY 1),
pm AS (SELECT f.game_id, bool_or(abs(f.plus_minus - (c.pts_for - c.pts_against)) > 1e-6) off
       FROM team_game_fatigue f JOIN game_scores c ON c.game_id = f.game_id AND c.team_abbreviation = f.team_abbreviation
       WHERE f.game_id IN (SELECT nba_game_id FROM g) GROUP BY 1)
SELECT g.game_id, g.nba_game_id, g.season, g.game_date, g.home_team, g.away_team,
       EXISTS (SELECT 1 FROM pbp_games n WHERE n.source = 'nba_api' AND n.game_date = g.game_date
               AND ((n.home_team = g.pb_home AND n.away_team = g.pb_away) OR (n.home_team = g.pb_away AND n.away_team = g.pb_home)
                    OR (n.home_team IS NULL AND n.away_team IN (g.pb_home, g.pb_away)))
               AND EXISTS (SELECT 1 FROM pbp_events x WHERE x.game_id = n.game_id)) twin,
       NOT EXISTS (SELECT 1 FROM game_scores c WHERE c.espn_id IS NOT NULL AND 'espn_' || c.espn_id = g.game_id) cup_final,
       coalesce(ev.unid_events, 0) unid_events, g.untracked_seconds, g.untracked_seconds / nullif(g.stint_seconds, 0) untracked_share,
       coalesce(ev.teamless_subs, 0) teamless_subs, coalesce(lx.excess, 0) lines_excess_players,
       (steps.ph = gs.final_home AND steps.pa = gs.final_away) IS NOT TRUE score_stale, coalesce(back.backward, false) score_backward,
       coalesce(ls.off, false) last_score_off, NOT g.game_ok unreconciled, CASE WHEN NOT g.game_ok THEN g.reason END reason,
       coalesce(ev.clock_outside_events, 0) clock_outside_events,
       g.nba_game_id IS NOT NULL AND sh.game_id IS NULL chart_missing,
       coalesce(sh.zero_dist_threes, 0) zero_dist_threes,
       coalesce(pm.off, false) pm_off
FROM g JOIN lineup_stint_games gs USING (game_id)
LEFT JOIN ev USING (game_id) LEFT JOIN steps USING (game_id) LEFT JOIN back USING (game_id) LEFT JOIN lx USING (game_id)
LEFT JOIN ls USING (game_id) LEFT JOIN sh ON sh.game_id = g.nba_game_id LEFT JOIN pm ON pm.game_id = g.nba_game_id
ORDER BY g.game_id
"""

FLAG_DDL = """CREATE TABLE data_quality_game_flags (
    game_id TEXT PRIMARY KEY, nba_game_id TEXT, season SMALLINT NOT NULL, game_date DATE, home_team TEXT, away_team TEXT,
    twin BOOLEAN NOT NULL, cup_final BOOLEAN NOT NULL, wrong_player_events SMALLINT NOT NULL, tag_text_events SMALLINT NOT NULL,
    unid_events SMALLINT NOT NULL, untracked_seconds REAL NOT NULL, untracked_share REAL, teamless_subs SMALLINT NOT NULL,
    lines_excess_players SMALLINT NOT NULL, score_stale BOOLEAN NOT NULL, score_backward BOOLEAN NOT NULL,
    last_score_off BOOLEAN NOT NULL, missed_three_calls SMALLINT NOT NULL, fg_attempts SMALLINT NOT NULL,
    chart_matched SMALLINT NOT NULL, chart_unmatched SMALLINT NOT NULL, chart_missing BOOLEAN NOT NULL,
    clock_outside_events SMALLINT NOT NULL, clock_big_shots SMALLINT NOT NULL, clock_median_off REAL,
    unreconciled BOOLEAN NOT NULL, reason TEXT, zero_dist_threes SMALLINT NOT NULL,
    pm_off BOOLEAN NOT NULL, game_ok BOOLEAN NOT NULL,
    classes TEXT[] NOT NULL DEFAULT '{}', n_classes SMALLINT NOT NULL DEFAULT 0, level TEXT NOT NULL DEFAULT 'clean')"""


def game_flags(conn, wrong, single, attempts, matched):
    f = pd.read_sql_query(FLAG_SQL, conn, params={"ps": DA.PERIOD_SECONDS})
    f["wrong_player_events"] = f.game_id.map(wrong.game_id.value_counts()).fillna(0).astype(int)
    f["tag_text_events"] = f.game_id.map(single.game_id.value_counts()).fillna(0).astype(int)
    known = attempts[attempts.pid.notna()]
    f["fg_attempts"] = f.game_id.map(known.groupby("game_id").size()).fillna(0).astype(int)
    f["chart_matched"] = f.game_id.map(known.groupby("game_id").matched.sum()).fillna(0).astype(int)
    f["chart_unmatched"] = f.fg_attempts - f.chart_matched
    miss = matched[~matched.made]
    f["missed_three_calls"] = f.game_id.map((miss.text_three != miss.nba_three).groupby(miss.game_id).sum()).fillna(0).astype(int)
    f["clock_big_shots"] = f.game_id.map((matched.clock_off > Q.CLOCK_BIG).groupby(matched.game_id).sum()).fillna(0).astype(int)
    f["clock_median_off"] = f.game_id.map(matched.groupby("game_id").clock_off.median())
    f["game_ok"] = ~f.unreconciled
    assert f.game_id.is_unique and set(wrong.game_id) <= set(f.game_id) and set(single.game_id) <= set(f.game_id)
    return f


def write_flags(conn, f):
    cols = ["game_id", "nba_game_id", "season", "game_date", "home_team", "away_team", "twin", "cup_final", "wrong_player_events",
            "tag_text_events", "unid_events", "untracked_seconds", "untracked_share", "teamless_subs", "lines_excess_players",
            "score_stale", "score_backward", "last_score_off", "missed_three_calls", "fg_attempts", "chart_matched", "chart_unmatched",
            "chart_missing", "clock_outside_events", "clock_big_shots", "clock_median_off", "unreconciled", "reason",
            "zero_dist_threes", "pm_off", "game_ok"]
    rows = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in f[cols].itertuples(index=False)]
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS data_quality_game_flags")
    cur.execute(FLAG_DDL)
    psycopg2.extras.execute_values(cur, f"INSERT INTO data_quality_game_flags ({', '.join(cols)}) VALUES %s", rows, page_size=2000)
    # which classes touch the game: data_quality_lib.PER_GAME's rules, applied here and nowhere else
    hits = ", ".join(f"CASE WHEN {rule} THEN '{key}' END" for key, (rule, _) in Q.PER_GAME.items())
    cur.execute(f"UPDATE data_quality_game_flags SET classes = array_remove(ARRAY[{hits}]::text[], NULL)")
    rank = {lv: i for i, (lv, _, _) in enumerate(Q.LEVELS)}
    worst = " ".join(f"WHEN classes && ARRAY[{', '.join(repr(k) for k, v in Q.LEVEL_OF.items() if v == lv)}]::text[] THEN '{lv}'"
                     for lv, _, _ in Q.LEVELS if lv != "clean")
    cur.execute(f"UPDATE data_quality_game_flags SET n_classes = cardinality(classes), level = CASE {worst} ELSE 'clean' END")
    assert set(rank) >= set(Q.LEVEL_OF.values()) and set(Q.LEVEL_OF) == set(Q.PER_GAME)
    cur.execute("CREATE INDEX ON data_quality_game_flags (season)")
    cur.execute("CREATE INDEX ON data_quality_game_flags (nba_game_id)")
    conn.commit()
    cur.execute("SELECT level, count(*) FROM data_quality_game_flags GROUP BY 1")
    levels = dict(cur.fetchall())
    cur.execute("SELECT c, count(*) FROM data_quality_game_flags, unnest(classes) c GROUP BY 1")
    per_class = dict(cur.fetchall())
    log(f"flags: {len(rows)} games; levels {levels}; classes {per_class}")
    return levels, per_class


def flag_rows(conn):
    df = pd.read_sql_query("SELECT game_id, nba_game_id, season, classes, level FROM data_quality_game_flags", conn)
    by_espn = {r.game_id: {"classes": set(r.classes), "quality": r.level} for r in df.itertuples(index=False)}
    nba_to_espn = {r.nba_game_id: r.game_id for r in df.itertuples(index=False) if r.nba_game_id}
    season_of = {r.game_id: int(r.season) for r in df.itertuples(index=False)}
    return by_espn, nba_to_espn, season_of


# ── 3. Does it matter? ───────────────────────────────────────────────────────

RANDOM_DRAWS = 30          # random drops of the same size per season, per drop set (the "fewer games" control)
RANDOM_MIN_GAMES = 50      # drop sets of fewer games get no random control (their moves are a few thousandths)


class Store:
    """data_quality_sensitivity rows: paper_tests' columns behind (result, drop_set, scope, games_dropped, games_in_scope),
    then the random-drop control of each difference (draws, 2.5th and 97.5th percentiles, p)."""

    def __init__(self, resamples):
        self.rows, self.resamples = [], resamples

    def block(self):
        return PT.Rows(self.resamples)

    def take(self, rows, result, drop_set, scope, dropped, in_scope, rand):
        for r in rows.rows:
            c = dict(zip(PT.Rows.COLS, r))
            ctl = rand.get((c["phase"], c["metric"], c["model_a"], c["model_b"]), (None, None, None, None))
            self.rows.append((result, drop_set, scope, int(dropped), int(in_scope)) + tuple(r) + ctl)


def variant_of(drop_key, scope, first_scope):
    """'' for every game under the result's first scope (paper_eval_tests' seeds), else a key of its own."""
    if drop_key == "none":
        return ""
    return f"drop:{drop_key}" + ("" if scope == first_scope else f":{scope}")


def series_from(df):
    return PT.Series(df.assign(lo=np.nan, hi=np.nan, cluster=df.unit_id), "")


def random_sets(result, key, scope, ids, scope_games, season_of):
    """RANDOM_DRAWS sets of games drawn from the scope with the drop set's count in every season (seeded per set)."""
    rng = np.random.default_rng(PT.row_seed("random-drop", result, key, scope))
    by_season = {}
    for g in scope_games:
        by_season.setdefault(season_of[g], []).append(g)
    need = pd.Series([season_of[g] for g in ids]).value_counts().to_dict()
    out = []
    for _ in range(RANDOM_DRAWS):
        pick = []
        for s in sorted(need):
            pool = by_season[s]
            pick += [pool[i] for i in rng.choice(len(pool), size=need[s], replace=False)]
        out.append(frozenset(pick))
    return out


def run_result(result, store, flags, season_of, scope_games, evaluate, meta):
    """Every drop set of one result: the bootstrap rows, then the random-drop control."""
    res = Q.RESULTS[result]
    first = res["scopes"][0]
    base = evaluate(frozenset(), first)
    meta[f"{result}_drop_sets"] = []
    for key, _label, rule, ids in Q.drop_sets(flags, scope_games):
        t = time.time()
        for scope in res["scopes"]:
            if key == "none" and scope != first:
                continue
            out = store.block()
            point = evaluate(ids, scope, out, variant_of(key, scope, first))
            rand = {}
            if key != "none" and len(ids) >= RANDOM_MIN_GAMES:
                draws = [evaluate(r, scope) for r in random_sets(result, key, scope, ids, scope_games, season_of)]
                for k, v in point.items():
                    d = np.array([x[k] for x in draws])
                    lo, hi = np.percentile(d, [2.5, 97.5])
                    far = int(np.sum(np.abs(d - base[k]) >= abs(v - base[k]) - 1e-12))
                    rand[k] = (len(d), float(lo), float(hi), (1 + far) / (1 + len(d)))
            store.take(out, result, key, scope, len(ids), len(scope_games), rand)
        meta[f"{result}_drop_sets"].append({"key": key, "rule": rule, "games": len(ids),
                                             "random_control": key != "none" and len(ids) >= RANDOM_MIN_GAMES})
        log(f"{result}: drop set {key} ({len(ids)} games) in {time.time() - t:.0f}s")


def impact_stage(conn, store, flags, season_of, meta):
    res = Q.RESULTS["impact"]
    rows_all, _, _ = R.load_rows(conn)
    bpm = R.load_bpm(conn)
    dates = dict(pd.read_sql_query("SELECT game_id, game_date FROM lineup_stint_games", conn).itertuples(index=False))
    ch = pd.read_sql("""SELECT model, parameter, value FROM paper_eval_choices
                        WHERE task = 'impact' AND model IN ('rapm_single', 'rapm_prior') AND parameter IN ('lambda', 'prior_scale')""", conn)
    cv = {(r.model, r.parameter): float(r.value) for r in ch.itertuples(index=False)}
    lam, lam_p, scale = cv[("rapm_single", "lambda")], cv[("rapm_prior", "lambda")], cv[("rapm_prior", "prior_scale")]
    tf = pd.read_sql("SELECT * FROM rating_tracker_fit WHERE version = 'tracker'", conn).iloc[0]
    assert not bool(tf.quick) and tf.estimated_on == PE.span(PE.TUNE)
    tpar = {k: float(tf[k]) for k in T.PARAMS}
    meta["impact_hyperparameters"] = {"rapm_single_lambda": lam, "rapm_prior_lambda": lam_p, "rapm_prior_scale": scale,
                                      "rapm_tracker": tpar, "source": "paper_eval_choices; rating_tracker_fit (held fixed)"}
    seasons = sorted(int(s) for s in rows_all.season.unique())

    def fits(rows):
        designs = {s: R.Design(rows[rows.season == s]) for s in seasons}
        out = {}
        tfilter = T.Filter(T.TrackerData(designs, bpm, folds=False), tpar, keep=True)
        for s in seasons[:-1]:
            d = designs[s]
            G, b, _ = d.gram()
            pv = d.prior_vector({p: (bpm[(p, s)][0], bpm[(p, s)][1]) for p in d.players if (p, s) in bpm})
            zb, _ = R.fit_nuisance(d, np.ones(d.n, bool), np.zeros(d.ncol), False)
            zero = PE.Fit({}, {}, *PE.nuisance_of(d, zb))
            out[s] = {"zero": zero,
                      "bpm": PE.Fit({p: v[0] for (p, ss), v in bpm.items() if ss == s}, {p: v[1] for (p, ss), v in bpm.items() if ss == s},
                                    zero.intercept, zero.home),
                      "rapm_single": PE.Fit.from_beta(d, d.solve(G, b, lam)),
                      "rapm_prior": PE.Fit.from_beta(d, d.solve(G, b, lam_p, pv * scale)),
                      "rapm_tracker": PE.Fit(*tfilter.ratings(s), *tfilter.nuisance(s))}
        return designs, out

    def predict(designs, fitted):
        """{model: DataFrame(season, unit_id, pred, actual, unit_date)} over every target season."""
        out = {m: [] for m in IMPACT_MODELS}
        for s in seasons[:-1]:
            d = designs[s + 1]
            for m in IMPACT_MODELS:
                g, p, a = PE.by_game(d, np.ones(d.n, bool), PE.predict_next(d, fitted[s][m]))
                out[m].append(pd.DataFrame({"season": s + 1, "unit_id": g, "pred": p, "actual": a, "unit_date": [dates[x] for x in g]}))
        return {m: pd.concat(v, ignore_index=True) for m, v in out.items()}

    t = time.time()
    full = predict(*fits(rows_all))
    log(f"impact: full-data fits in {time.time() - t:.1f}s")
    pe = pd.read_sql("""SELECT model, season, unit_id, pred, actual FROM paper_eval_predictions
                        WHERE task = 'impact_next' AND model IN %s""", conn, params=(IMPACT_MODELS,))
    dev = {}
    for m in IMPACT_MODELS:
        j = full[m].merge(pe[pe.model == m], on=["season", "unit_id"], suffixes=("", "_pe"), how="outer", indicator=True)
        assert (j._merge == "both").all(), (m, j._merge.value_counts().to_dict())
        dev[m] = float(max(np.abs(j.pred - j.pred_pe).max(), np.abs(j.actual - j.actual_pe).max()))
    assert max(dev.values()) < 1e-6, dev
    meta["impact_check_vs_paper_eval"] = {m: (v if v >= 1e-15 else 0.0) for m, v in dev.items()}
    log(f"impact: full-data predictions = paper_eval_predictions (max dev {max(dev.values()):.1e})")
    phases = (("tune", list(PE.TUNE[1:])), ("validate", [PE.VALIDATE]), ("test", [PE.TEST]))

    def evaluate(ids, scope, out=None, variant=None):
        pred = full if (not ids or scope == "scoring") else predict(*fits(rows_all[~rows_all.game_id.isin(ids)].reset_index(drop=True)))
        point = {}
        for phase, ss in phases:
            sub = {m: pred[m][pred[m].season.isin(ss) & ~pred[m].unit_id.isin(ids)] for m in IMPACT_MODELS}
            err = {m: PE.rmse(sub[m].pred, sub[m].actual) for m in IMPACT_MODELS}
            for a, b in res["pairs"]:
                assert (sub[a].unit_id.to_numpy() == sub[b].unit_id.to_numpy()).all()
                point[(phase, res["metric"], a, b)] = err[a] - err[b]
            if out is not None:
                ser = {m: series_from(sub[m]) for m in IMPACT_MODELS}
                for m in IMPACT_MODELS:
                    if m != "zero":
                        PT.single_rows(out, res["task"], phase, m, ser[m], res["metric"])
                for a, b in res["pairs"]:
                    PT.paired_rows(out, res["task"], phase, a, b, ser[a], ser[b], res["metric"], variant)
        if out is not None:   # single rows carry the series' own variant (''): give them the drop set's
            out.rows = [r if r[4] else r[:5] + (variant,) + r[6:] for r in out.rows]
        return point

    scope_games = sorted(set(rows_all.game_id))
    run_result("impact", store, flags, season_of, scope_games, evaluate, meta)
    return scope_games


def possessions_stage(conn, store, flags, season_of, meta):
    res = Q.RESULTS["possessions"]
    p = pd.read_sql_query("""SELECT p.game_id, p.start_type, p.pts, p.transition FROM possessions p
                             JOIN possession_games g ON g.game_id = p.game_id WHERE g.game_ok""", conn)
    s0, s1 = pd.read_sql_query("SELECT min(season), max(season) FROM possession_games WHERE game_ok", conn).iloc[0].tolist()
    span = PE.span(range(int(s0), int(s1) + 1))
    # the league rows of possession_seasons are these sums
    ps = pd.read_sql_query("SELECT start_type, poss, pts, timed_poss, trans_poss, trans_pts FROM possession_seasons WHERE team = 'ALL'", conn)
    tot = ps.groupby("start_type")[["poss", "pts", "timed_poss", "trans_poss", "trans_pts"]].sum()
    mine = p.groupby("start_type").agg(poss=("pts", "size"), pts=("pts", "sum"))
    for st in ("steal", "made_fg"):
        assert (int(mine.poss[st]), int(mine.pts[st])) == (int(tot.poss[st]), int(tot.pts[st])), st
    timed = p.transition.notna().to_numpy()
    tr = (p.transition == True).to_numpy()  # noqa: E712 (None-safe)
    assert (int(timed.sum()), int(tr.sum()), int(p.pts[tr].sum())) == \
        (int(tot.timed_poss["all"]), int(tot.trans_poss["all"]), int(tot.trans_pts["all"])), "transition counts"
    meta["possessions_check_vs_possession_seasons"] = {
        "steal_ppp": float(tot.pts["steal"] / tot.poss["steal"]), "made_fg_ppp": float(tot.pts["made_fg"] / tot.poss["made_fg"]),
        "transition_share": float(tot.trans_poss["all"] / tot.timed_poss["all"])}
    log(f"possessions: {len(p):,} possessions reproduce possession_seasons' league rows")
    groups = {"steal": (p.start_type == "steal").to_numpy(), "made_fg": (p.start_type == "made_fg").to_numpy(),
              "transition": timed & tr, "settled": timed & ~tr}
    pts = p.pts.to_numpy(float)
    games = pd.Series(p.game_id.to_numpy())
    del p

    def evaluate(ids, scope, out=None, variant=None):
        keep = ~games.isin(ids).to_numpy() if ids else np.ones(len(games), bool)
        ppp = {g: float(pts[keep & m].sum() / (keep & m).sum()) for g, m in groups.items()}
        point = {("all", res["metric"], a, b): ppp[a] - ppp[b] for a, b in res["pairs"]}
        if out is None:
            return point
        idx, C = PT.cluster_codes(games.to_numpy()[keep])

        def ratio_rows(a, b=None):
            """PPP of group a (and of b, and a - b) with a game-clustered bootstrap of the ratio of sums."""
            ga = groups[a][keep]
            cols = [pts[keep] * ga, ga.astype(float)]
            if b is not None:
                gb = groups[b][keep]
                cols += [pts[keep] * gb, gb.astype(float)]
            seed = PT.row_seed(res["task"], "all", res["metric"], a, b or "", variant, span)
            S = PT.boot_sums(np.random.default_rng(seed), idx, C, cols, store.resamples)
            va = float(cols[0].sum() / cols[1].sum())
            ba = S[:, 0] / S[:, 1]
            if b is None:
                lo, hi = PT.percentile_ci(ba)
                out.add(task=res["task"], phase="all", metric=res["metric"], model_a=a, variant=variant, seasons=span,
                        unit_type="possession", cluster_by="game", n=int(cols[1].sum()), n_clusters=C, value_a=va, diff=va,
                        ci_lo=lo, ci_hi=hi, seed=seed)
                return
            vb = float(cols[2].sum() / cols[3].sum())
            d = ba - S[:, 2] / S[:, 3]
            lo, hi = PT.percentile_ci(d)
            out.add(task=res["task"], phase="all", metric=res["metric"], model_a=a, model_b=b, variant=variant, seasons=span,
                    unit_type="possession", cluster_by="game", n=int(cols[1].sum() + cols[3].sum()), n_clusters=C, value_a=va,
                    value_b=vb, diff=va - vb, ci_lo=lo, ci_hi=hi, p_boot=PT.boot_p(d), seed=seed,
                    note="ratio of summed points to possessions, games resampled")

        for g in groups:
            ratio_rows(g)
        for a, b in res["pairs"]:
            ratio_rows(a, b)
        cols = [groups["transition"][keep].astype(float), timed[keep].astype(float)]
        seed = PT.row_seed(res["task"], "all", "share", "transition", "", variant, span)
        S = PT.boot_sums(np.random.default_rng(seed), idx, C, cols, store.resamples)
        lo, hi = PT.percentile_ci(S[:, 0] / S[:, 1])
        v = float(cols[0].sum() / cols[1].sum())
        out.add(task=res["task"], phase="all", metric="share", model_a="transition", variant=variant, seasons=span, unit_type="possession",
                cluster_by="game", n=int(cols[1].sum()), n_clusters=C, value_a=v, diff=v, ci_lo=lo, ci_hi=hi, seed=seed,
                note="transition possessions / timed possessions")
        return point

    scope_games = sorted(set(games))
    run_result("possessions", store, flags, season_of, scope_games, evaluate, meta)
    return scope_games


def availability_stage(conn, store, flags, season_of, nba_to_espn, meta):
    res = Q.RESULTS["availability"]
    E = pd.read_sql_query("""SELECT game_id, season, game_date, home_won, p_eval_base, p_eval_bpm, p_eval_rapm
                             FROM pregame_availability_odds WHERE p_eval_base IS NOT NULL""", conn)
    E["espn"] = E.game_id.map(nba_to_espn)
    meta["availability_unmapped_games"] = int(E.espn.isna().sum())
    colof = {"prior_rest": "p_eval_base", "avail_bpm": "p_eval_bpm", "avail_rapm": "p_eval_rapm"}
    phases = (("tune", list(PE.TUNE)), ("validate", [PE.VALIDATE]), ("test", [PE.TEST]))
    y = E.home_won.to_numpy(float)

    def evaluate(ids, scope, out=None, variant=None):
        keep = ~E.espn.isin(ids).to_numpy()
        point = {}
        for phase, ss in phases:
            m = E.season.isin(ss).to_numpy() & keep
            for metric in ("log_loss", "brier"):
                for a, b in res["pairs"]:
                    pa, pb = E[colof[a]].to_numpy(float), E[colof[b]].to_numpy(float)
                    both = m & ~np.isnan(pa) & ~np.isnan(pb)
                    point[(phase, metric, a, b)] = float(PT.loss(metric, res["task"], pa[both], y[both]).mean()
                                                         - PT.loss(metric, res["task"], pb[both], y[both]).mean())
        if out is None:
            return point

        def series(col, m):
            d = E[m & keep & E[col].notna().to_numpy()]
            return PT.Series(pd.DataFrame({"season": d.season.to_numpy(), "unit_id": d.game_id.to_numpy(), "pred": d[col].to_numpy(float),
                                           "actual": d.home_won.to_numpy(float), "lo": np.nan, "hi": np.nan,
                                           "unit_date": d.game_date.to_numpy(), "cluster": d.game_id.to_numpy()}), "")
        for phase, ss in phases:
            m = E.season.isin(ss).to_numpy()
            for metric in ("log_loss", "brier"):
                for a, b in res["pairs"]:
                    PT.paired_rows(out, res["task"], phase, a, b, series(colof[a], m), series(colof[b], m), metric, variant,
                                   note="paired on the games both forecast; base = paper_eval's prior_rest of the phase")
                for model in colof:
                    PT.single_rows(out, res["task"], phase, model, series(colof[model], m), metric)
        out.rows = [r if r[4] else r[:5] + (variant,) + r[6:] for r in out.rows]
        return point

    scope_games = sorted(set(E.espn.dropna()))
    run_result("availability", store, flags, season_of, scope_games, evaluate, meta)
    return scope_games


def check_reproduction(conn, store, meta):
    """With the full resample count, the every-game rows equal the stored test tables bit for bit."""
    cols = ["task", "phase", "metric", "model_a", "model_b", "variant", "seasons", "n", "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot"]
    mine = pd.DataFrame([r[5:5 + len(PT.Rows.COLS)] for r in store.rows if r[1] == "none"], columns=PT.Rows.COLS)
    out = {}
    for result, table in (("impact", "paper_eval_tests"), ("availability", "pregame_availability_tests")):
        task = Q.RESULTS[result]["task"]
        ref = pd.read_sql(f"SELECT {', '.join(cols)} FROM {table} WHERE task = %s AND resamples = %s", conn,
                          params=(task, store.resamples))
        m = mine[mine.task == task].merge(ref, on=["task", "phase", "metric", "model_a", "model_b", "variant", "seasons"],
                                          suffixes=("", "_ref"), how="left", indicator=True)
        if store.resamples != PT.RESAMPLES:
            out[result] = f"skipped ({store.resamples} resamples; the stored tables use {PT.RESAMPLES})"
            continue
        assert (m._merge == "both").all(), m[m._merge != "both"][["phase", "metric", "model_a", "model_b"]].head()
        worst = 0.0
        for c in ("n", "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot"):
            a, b = m[c].astype(float), m[f"{c}_ref"].astype(float)
            both = a.notna() & b.notna()
            assert (a.isna() == b.isna()).all(), c
            worst = max(worst, float(np.abs(a[both] - b[both]).max()) if both.any() else 0.0)
        assert worst < 1e-12, (result, worst)
        out[result] = {"rows": int(len(m)), "max_abs_diff": worst}
    meta["tests_reproduce"] = out
    log(f"reproduction: {out}")


SENS_DDL = """CREATE TABLE data_quality_sensitivity (
    result TEXT NOT NULL, drop_set TEXT NOT NULL, scope TEXT NOT NULL, games_dropped INTEGER NOT NULL, games_in_scope INTEGER NOT NULL,
    {cols}, rand_draws INTEGER, rand_lo DOUBLE PRECISION, rand_hi DOUBLE PRECISION, rand_p DOUBLE PRECISION,
    PRIMARY KEY (result, drop_set, scope, phase, metric, model_a, model_b))"""


def write_rest(conn, store, meta):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS data_quality_sensitivity")
    body = PT.DDL.strip().rsplit("PRIMARY KEY", 1)[0].rstrip().rstrip(",")
    cur.execute(SENS_DDL.format(cols=body))
    rows = sorted(store.rows, key=lambda r: tuple(str(x) for x in r[:3] + r[6:12]))
    psycopg2.extras.execute_values(cur, "INSERT INTO data_quality_sensitivity VALUES %s", rows, page_size=1000)
    cur.execute("DROP TABLE IF EXISTS data_quality_meta")
    cur.execute("CREATE TABLE data_quality_meta (key TEXT PRIMARY KEY, value JSONB NOT NULL)")
    psycopg2.extras.execute_values(cur, "INSERT INTO data_quality_meta VALUES %s",
                                   [(k, json.dumps(v, default=jsonable)) for k, v in sorted(meta.items())])
    conn.commit()
    for t in ("data_quality_game_flags", "data_quality_sensitivity", "data_quality_meta"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        log(f"{t}: {cur.fetchone()}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--resamples", type=int, default=PT.RESAMPLES, help=f"bootstrap resamples (default {PT.RESAMPLES:,})")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    meta = {"run": {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "resamples": args.resamples},
            "levels": [{"key": k, "label": lb, "what": w} for k, lb, w in Q.LEVELS],
            "rules": {k: {"rule": r, "what": w, "level": Q.LEVEL_OF[k]} for k, (r, w) in Q.PER_GAME.items()},
            "not_per_game": Q.NOT_PER_GAME, "max_drop_share": Q.MAX_DROP_SHARE}

    A, wrong, single, attempts, matched = remeasure(conn)
    check_audit(conn, A, meta)
    f = game_flags(conn, wrong, single, attempts, matched)
    del wrong, single, attempts, matched
    gc.collect()
    levels, per_class = write_flags(conn, f)
    meta["flags"] = {"games": len(f), "levels": levels, "per_class": per_class,
                     "seasons": [int(f.season.min()), int(f.season.max())]}
    del f
    gc.collect()

    flags, nba_to_espn, season_of = flag_rows(conn)
    store = Store(args.resamples)
    meta["random_control"] = {"draws": RANDOM_DRAWS, "min_games": RANDOM_MIN_GAMES,
                              "rule": "the drop set's count of games in every season, drawn at random from the result's games of that "
                                      "season; p = (1 + draws moving the difference at least as far from every game's) / (1 + draws)"}
    scopes = {"impact": impact_stage(conn, store, flags, season_of, meta)}
    gc.collect()
    scopes["possessions"] = possessions_stage(conn, store, flags, season_of, meta)
    gc.collect()
    scopes["availability"] = availability_stage(conn, store, flags, season_of, nba_to_espn, meta)
    meta["scope_games"] = {k: len(v) for k, v in scopes.items()}
    meta["scope_not_flagged"] = {k: sum(1 for g in v if g not in flags) for k, v in scopes.items()}
    check_reproduction(conn, store, meta)
    meta["run"]["seconds"] = round(time.time() - T0)
    write_rest(conn, store, meta)
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
