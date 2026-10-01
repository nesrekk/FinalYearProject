"""
build_coaching_decisions.py
===========================
Coaching Decisions (round 6, step 5): four things coaches act on, each
tested with round 5's machinery (scripts/paper_beliefs.py: a permutation
null from shuffling the decision WITHIN MATCHED MOMENTS, a two-sided p
with Phipson-Smyth's +1, a screen-then-confirm second stage, and
Benjamini-Hochberg at 5% within a family), so the answer is reported the
way every popular belief is: "k of n survive".

Every time used here is the corrected clock (pbp_event_clock, through the
`possessions` table and its event ranges), never ESPN's raw clock: the
last half-minute of a quarter is where these decisions happen, and ESPN
stamps made shots there ~14 s late. Only games whose possessions reconcile
(possession_games.game_ok: 7,220 of 7,232, 2020-21 to 2025-26) are used.

The four decisions (judgment calls, all fixed before looking at outcomes)
---------------------------------------------------------------------------
timeout   Does a timeout stop a run?
          Run = unanswered points by one team in the same period (counted by
          possession: offence points and technical free throws; a period
          start ends a run, because the break is a stoppage too). Decision
          point = the first possession boundary at which a run reaches
          RUN_MIN (8) points, right after the running team scored (the
          possession ended on a made field goal or last free throw), the
          other team (the "victim") has the next possession in the same
          period, at least MIN_LEFT (120) s are left in the period, and the
          victim has not already called a timeout since its last score.
          Treated = the victim calls a full timeout logged after the
          runner's score and before its own next possession's first counted
          event (shot, free throw, turnover). By ORDER, not clock: in the 418
          games of 2024-25 that NBA.com also logged, ESPN's timeout times
          are within 1 s of NBA.com's for 77% of timeouts (median 0) but
          about one in five is stamped late (95th percentile 18 s), while
          the made shot before it sits on the corrected (shot-chart) clock,
          so a clock gap can't tell a timeout at the dead ball from one
          called later in the possession (NBA.com's own log: 83% of
          timeouts right after the other team's make come within 3 s of
          it). The order-based group therefore holds some timeouts called
          a few seconds into the possession (a team in trouble calling one
          would make timeouts look worse); `flag` marks timeouts whose
          corrected time is within DEAD_BALL_GAP (2) s of the score, surely
          at the dead ball, and a sensitivity run treats only those (the
          rest of the order-based group become controls, so late-stamped
          dead-ball timeouts dilute that run instead). A victim timeout
          after its first counted event leaves the moment a control, with
          `later` set. Excluded (counted in the meta): moments
          where the runner called the timeout, where a coach's challenge was
          made in that gap, or where the victim had already called one
          during the run. A "Full Timeout" within CHALLENGE_GAP (3) events
          of a coach's challenge is the challenge's own timeout and is not a
          timeout here. Outcome = the victim's net points (its points minus
          the runner's, technicals included) over the next K (6)
          possessions of the same period (3 each, the victim first);
          outcome_b = over the next 2, outcome_c = over the next 12,
          outcome_d = 1 if the victim scores on its next possession.
          Matched within: season x period group (1-2 / 3 / 4+) x time left
          in the period (120-300 / 300-480 / 480+ s) x the victim's margin
          after the run (<=-10 / -9..-4 / -3..3 / 4..9 / >=10) x run size
          (8 / 9 / 10+). Regression to the mean is the null: both groups'
          next possessions are compared, never the timeout group to zero.
          A control may still call a timeout later in the window (the
          comparison is "timeout now" vs "no timeout now"; `later` flags it).
          ESPN's log has one timeout type ("Full Timeout", always with a
          team): a coach's choice and a mandatory television break charged
          to the team can't be told apart. Sensitivity: RUN_MIN 6 and 10;
          dead-ball timeouts only (`flag`).
challenge Coach's challenges: who wins them, and what is a won one worth?
          ESPN logs a challenge as an outcome event: "Coach's Challenge
          (Overturned)" ("... retain their timeout": won), "(Stands)" and
          "(Supported)" (call stands for lack of evidence / replay supports
          the call; both "... charged with a timeout": lost) and
          "(replaycenter)" (no outcome given: not in any rate). A plain
          "Challenge" event is a second record of the same challenge when an
          outcome event of the same team lies within PLAIN_GAP (6) events
          (1,485 of 1,510 in the reconciled games); the rest (25) are challenges with no outcome. Two
          outcome events of the same team and kind within 3 events at the
          same clock time are one challenge logged twice. The team is the
          event's, else the nickname in brackets in its text. What was
          challenged (foul, out of bounds, goaltending) is NOT on file:
          ESPN rewrites the log after an overturn (the overturned call
          disappears and the corrected call takes its place; a shooting
          foul by the challenger precedes 1,444 challenges, 5% of them won,
          because won ones no longer show their foul), so a call type read
          from the log depends on the outcome; success by call type is not
          reported. Value: the challenger's win probability (Game Replay's
          model, wpa_lib) at the start of the possession in progress at the
          challenge vs after the possession that follows it (outcome =
          that change; outcome_b = net points over the same two
          possessions); treated = won; matched within season x period
          group. That is "after a won challenge vs after a lost one", not
          the value of reversing a given call (the mix of calls differs).
          The cost side, the timeout a lost challenge uses, is not on file:
          the win-probability model has no timeouts-left term.
          Team family: a team's success rate minus the league's in the same
          season x period group, null = outcomes shuffled among all decided
          challenges within those strata.
foul_up3  Fouling up 3 late. Decision point = the first possession of a game
          in which the offence trails by exactly 3 at its start, in the 4th
          quarter or overtime, with 0-24 s left in the period at its start.
          Treated = the defence (the leader) commits a non-shooting foul
          (Personal Foul, Personal Take Foul, Loose Ball Foul, Transition
          Take Foul) before the offence's first field-goal attempt of that
          possession (event kinds from the shared parser, Game.parse()).
          A foul on a shot is "defended". Outcome = the leader wins the
          game; outcome_b = the period ended tied; outcome_c = the offence
          made a three on the possession. Matched within: time left at the
          start (0-6 / 6-12 / 12-18 / 18-24 s) x regulation / overtime. Too
          few per team for a team family: league only.
twoforone 2-for-1 at the end of quarters 1-3. Decision point = a possession
          starting with 28-40 s left in the period (not empty). Treated
          ("early") = its first counted event (field-goal attempt, free
          throw on a shooting foul, or turnover) comes with more than 24 s
          left, so the other team can't hold the ball for the period's last
          shot; the 24 s line sits in the valley of the bimodal timing of
          first attempts (2024-25: possessions starting with 32-34 s left
          shoot either at 26-30 s or at 4-12 s). Possessions whose first
          counted event is a free throw after a non-shooting foul or a
          technical are left out (counted): the defence chose that timing.
          Outcome = the team's net points from that possession's
          start to the end of the period (technicals included); outcome_b =
          its possessions minus the opponent's in that span; outcome_c =
          points on the possession itself; outcome_d = net points after it
          (outcome minus outcome_c: what the extra possession is worth). Matched within: season x period x
          start time (2 s bins) x how the possession began (after a make /
          a miss / a turnover / other). Shooting early is partly an
          opportunity (a quick open look), not only a choice; possessions
          after a turnover start at a derived time (turnovers can't be timed
          from ESPN).

The test (paper_beliefs' machinery)
-----------------------------------
Statistic: the stratified effect on the treated, ATT = sum over strata of
n_treated x (treated mean - control mean) / sum of n_treated, over strata
holding both. Null: the decision labels are shuffled within strata (counts
kept), B1 = 2,000 draws, then 20,000 fresh ones for p <= 0.02;
p = (1 + #{|T* - m| >= |T - m|}) / (B + 1) around the draws' mean m.
Interval: 95% game-clustered bootstrap (2,000 resamples of games).
Families, each with Benjamini-Hochberg at 5%:
  coaching:league   the four primary league-level tests (k of 4);
  timeout:team      per franchise, its own decision points, shuffled within
                    its own strata (season x period group x margin 3);
  twoforone:team    per franchise, the same (season x start 4 s bin x how it
                    began);
  challenge:team    per franchise, success above the league's.
Season-level rows (league, one season at a time) and the timeout
sensitivity runs are reported but are not in any family.

Tables written (dropped and rebuilt; nothing else is touched)
-------------------------------------------------------------
  coaching_decisions         one row per decision point (~50k): decision,
                             game, team (the decider), opponent, time left in
                             the period, margin, win probability and the
                             value of one point there, pre-game expected
                             margin (balance check), strata, treated,
                             outcomes (meaning per decision above), detail;
  coaching_decision_tests    one row per test (level league / season / team):
                             ATT, bootstrap interval, treated and matched
                             control means, the null's mean, SD, 2.5/97.5
                             percentiles, p, draws, BH q and flag;
  coaching_decision_summary  one row per family: units, p < 0.05, 0.05 n,
                             survivors;
  coaching_decision_meta     key / value / note: definitions, counts of
                             every exclusion, balance checks, runtime.

Deterministic: every generator is seeded from an md5 of its key
(paper_beliefs.rng_for). ~4 min with the full draw counts.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 build_coaching_decisions.py
    cd scripts && python3 build_coaching_decisions.py --perms 200 --perms2 1000 --boot 200   # quick look
Rerun after build_possessions.py (or anything it depends on) and
build_season_sim.py; restart impact_api (the router caches the tables).
"""

import argparse
import time
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, load_espn, load_season_names
import paper_beliefs as PB          # also puts api/ on sys.path
import compute_wpa as W
from coaching_lib import DECISIONS, LABELS, UNIT_LABELS, att as matched_att  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

SEASONS = range(2021, 2027)
PERMS, PERMS2, BOOT = 2_000, 20_000, 2_000
STAGE2_P = PB.STAGE2_P
FDR_Q = PB.FDR_Q

RUN_MIN = 8
RUN_SENSITIVITY = (6, 10)
MIN_LEFT = 120.0
K = 6
CHALLENGE_GAP = 3
DEAD_BALL_GAP = 2.0
PLAIN_GAP = 6
UP3_MAX_LEFT = 24.0
UP3_FOULS = {"Personal Foul", "Personal Take Foul", "Loose Ball Foul", "Transition Take Foul"}
TFO_LO, TFO_HI, TFO_LINE = 28.0, 40.0, 24.0
SCORE_ENDS = ("made_fg", "made_ft")
START_GROUP = {"made_fg": "make", "made_ft": "make", "dreb": "miss", "dreb_ft": "miss", "team_dreb": "miss",
               "steal": "turnover", "dead_tov": "turnover"}
OUTCOME_KINDS = {"Coach's Challenge (Overturned)": "won", "Coach's Challenge (Stands)": "lost_stands",
                 "Coach's Challenge (Supported)": "lost_supported", "Coach's Challenge (replaycenter)": "unknown"}
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


def period_end(period):
    """Seconds since tip-off at the end of `period`."""
    return 720.0 * period if period <= 4 else 2880.0 + 300.0 * (period - 4)


def wp_secs(period, elapsed):
    """Win-probability model's clock (pbp_events' convention: regulation counts down from 2880, overtime from 300)."""
    return max(period_end(period) - elapsed, 0.0) + (720.0 * (4 - period) if period <= 4 else 0.0)


def bucket(x, edges):
    """Index of the bin of `x` among `edges` (left-closed)."""
    return int(np.searchsorted(edges, x, side="right"))


# ------------------------------------------------------------------ loading

def load_games(conn):
    g = pd.read_sql_query(
        """SELECT pg.game_id, pg.season, pg.home_team, pg.away_team, pg.final_home, pg.final_away, o.exp_margin
           FROM possession_games pg LEFT JOIN game_pregame_odds o ON o.game_id = pg.nba_game_id
           WHERE pg.game_ok""", conn)
    return g.set_index("game_id")


def load_possessions(conn, season):
    p = pd.read_sql_query(
        """SELECT p.game_id, p.poss_no, p.period, p.offense, p.defense, p.off_home, p.start_type, p.end_type,
                  p.start_elapsed, p.end_elapsed, p.action_from, p.action_to, p.off_score, p.def_score,
                  p.pts, p.off_tech_pts, p.def_tech_pts, p.fg3m, p.empty
           FROM possessions p JOIN possession_games g USING (game_id)
           WHERE g.game_ok AND p.season = %(s)s ORDER BY p.game_id, p.poss_no""", conn, params={"s": season})
    p["action_from"] = p.action_from.fillna(-1).astype(int)
    p["action_to"] = p.action_to.fillna(-1).astype(int)
    return p


def load_stoppages(conn, season):
    """Timeouts and challenge events of one season (game_ok games), with the corrected clock."""
    return pd.read_sql_query(
        """SELECT e.game_id, e.action_number, e.period, e.team_tricode, e.action_type, e.description, c.period_t
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           JOIN possession_games pg ON pg.game_id = e.game_id AND pg.game_ok
           LEFT JOIN pbp_event_clock c ON c.event_id = e.id
           WHERE g.source = 'espn' AND g.season = %(s)s
             AND (e.action_type = 'Full Timeout' OR e.action_type LIKE 'Coach''s Challenge%%' OR e.action_type = 'Challenge')
           ORDER BY e.game_id, e.action_number""", conn, params={"s": season})


def load_nicknames(conn):
    """Team nickname (as in "[Cavaliers] COACH'S CHALLENGE ...") -> tricode, from ESPN's own timeout lines."""
    rows = pd.read_sql_query(
        """SELECT regexp_replace(e.description, ' Full timeout$', '') AS nick, e.team_tricode AS team, COUNT(*) AS n
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' AND e.action_type = 'Full Timeout' AND e.team_tricode IS NOT NULL
           GROUP BY 1, 2""", conn)
    best = rows.sort_values("n").groupby("nick").tail(1)
    return dict(zip(best.nick, best.team))


def first_counted_left(conn, season):
    """{(game_id, poss_no): (seconds left in the period at the possession's first counted event, whether that
    event is a free throw the offence didn't shoot for)} for the 2-for-1 window (quarters 1-3, starting 28-40 s
    from the end), on the corrected clock. A trip after a non-shooting foul (the last foul logged before it isn't
    a shooting foul) or a technical is timed by the defence, not by the offence's choice."""
    q = pd.read_sql_query(
        """SELECT p.game_id, p.poss_no, 720 - c.period_t AS first_left, e.action_type,
                  (SELECT f.action_type FROM pbp_events f
                    WHERE f.game_id = e.game_id AND f.action_number < e.action_number AND f.period = e.period
                      AND f.action_type LIKE '%%Foul%%'
                    ORDER BY f.action_number DESC LIMIT 1) AS last_foul
           FROM possessions p JOIN possession_games g USING (game_id)
           JOIN pbp_events e ON e.game_id = p.game_id AND e.action_number = p.action_from
           JOIN pbp_event_clock c ON c.event_id = e.id
           WHERE g.game_ok AND p.season = %(s)s AND p.period <= 3
             AND 720 * p.period - p.start_elapsed >= %(lo)s AND 720 * p.period - p.start_elapsed < %(hi)s""",
        conn, params={"s": season, "lo": TFO_LO, "hi": TFO_HI})
    out = {}
    for r in q.itertuples():
        ft = r.action_type.startswith("Free Throw")
        defence_timed = ft and ("Technical" in r.action_type or r.last_foul != "Shooting Foul")
        out[(r.game_id, int(r.poss_no))] = (float(r.first_left), defence_timed)
    return out


# ------------------------------------------------------------------ per-game state

class GameArrays:
    """One game's possessions as arrays, with the score before and after each possession."""

    def __init__(self, d):
        self.n = len(d)
        for c in ("period", "poss_no", "action_from", "action_to", "pts", "off_tech_pts", "def_tech_pts", "off_score",
                  "def_score", "fg3m"):
            setattr(self, c, d[c].to_numpy())
        for c in ("offense", "defense", "start_type", "end_type"):
            setattr(self, c, d[c].to_numpy(dtype=object))
        self.off_home = d.off_home.to_numpy(bool)
        self.empty = d["empty"].to_numpy(bool)
        self.start = d.start_elapsed.to_numpy(float)
        self.end = d.end_elapsed.to_numpy(float)
        self.off_pts = self.pts + self.off_tech_pts
        self.def_pts = self.def_tech_pts
        h0 = np.where(self.off_home, self.off_score, self.def_score)
        a0 = np.where(self.off_home, self.def_score, self.off_score)
        self.h0, self.a0 = h0, a0
        self.h1 = h0 + np.where(self.off_home, self.off_pts, self.def_pts)
        self.a1 = a0 + np.where(self.off_home, self.def_pts, self.off_pts)

    def signed(self, i, team):
        """Net points for `team` in possession i."""
        return (self.off_pts[i] - self.def_pts[i]) * (1 if self.offense[i] == team else -1)


# ------------------------------------------------------------------ decision builders

def timeouts_of_game(st):
    """(timeouts [(action_number, team, seconds since tip-off)] that aren't a challenge's own, challenge event
    action numbers)."""
    chal = st.action_number[st.action_type != "Full Timeout"].to_numpy()
    tos = []
    full = st[st.action_type == "Full Timeout"]
    for a, tm, per, t in zip(full.action_number, full.team_tricode, full.period, full.period_t):
        if len(chal) and np.min(np.abs(chal - a)) <= CHALLENGE_GAP:
            continue
        start = period_end(int(per)) - (720.0 if per <= 4 else 300.0)
        tos.append((int(a), tm, start + float(t) if t == t else np.nan))
    return tos, chal


def timeout_points(gid, A, tos, chal, run_min, cnt):
    """Decision points of one game for a run threshold `run_min`."""
    rows = []
    runner, run, since = None, 0, -1
    last_score_end = defaultdict(lambda: -1)
    for i in range(A.n):
        if i == 0 or A.period[i] != A.period[i - 1]:
            runner, run = None, 0
            last_score_end = defaultdict(lambda: -1)
        off, dfn = A.offense[i], A.defense[i]
        op, dp = A.off_pts[i], A.def_pts[i]
        prev = run if runner == off else 0
        if op > 0 and dp > 0:
            runner, run = off, op
            last_score_end[off] = last_score_end[dfn] = A.action_to[i]
            continue
        if dp > 0:
            if runner == dfn:
                run += dp
            else:
                runner, run = dfn, dp
            last_score_end[dfn] = A.action_to[i]
            continue
        if op <= 0:
            continue
        if runner == off:
            run += op
        else:
            runner, run = off, op
        since = last_score_end[dfn]
        last_score_end[off] = A.action_to[i]
        if not (prev < run_min <= run):
            continue
        cnt["crossings"] += 1
        if A.end_type[i] not in SCORE_ENDS or i + 1 >= A.n or A.period[i + 1] != A.period[i] \
                or A.offense[i + 1] != dfn or A.action_from[i + 1] < 0 or A.action_to[i] < 0:
            cnt["not_dead_ball_to_victim"] += 1
            continue
        left = period_end(A.period[i]) - A.end[i]
        if left < MIN_LEFT:
            cnt["under_min_left"] += 1
            continue
        lo, hi = A.action_to[i], A.action_from[i + 1]
        gap = [(tm, t) for a, tm, t in tos if lo < a < hi]
        if dfn in [tm for a, tm, _t in tos if since < a <= lo]:
            cnt["victim_timeout_during_run"] += 1
            continue
        if off in [tm for tm, _t in gap]:
            cnt["runner_timeout"] += 1
            continue
        if len(chal) and np.any((chal > lo) & (chal < hi)):
            cnt["challenge_in_gap"] += 1
            continue
        # Treated: a victim timeout logged between the score and its next counted event (order, not clock: ESPN
        # stamps about one timeout in five late). `flag`: that timeout's corrected time is within DEAD_BALL_GAP of
        # the scoring play's, i.e. surely at the dead ball (the sensitivity run treats only those).
        treated = dfn in [tm for tm, _t in gap]
        quick = any(tm == dfn and t - A.end[i] <= DEAD_BALL_GAP for tm, t in gap)
        win = [j for j in range(i + 1, min(i + 1 + 12, A.n)) if A.period[j] == A.period[i]]
        net = np.cumsum([A.signed(j, dfn) for j in win]) if win else np.array([0])
        k_at = lambda k: float(net[min(k, len(win)) - 1]) if win else 0.0  # noqa: E731
        end_k = win[min(K, len(win)) - 1] if win else i
        later = any(tm == dfn for a, tm, _t in tos if hi <= a <= A.action_to[end_k])
        margin = int((A.def_score[i] + dp) - (A.off_score[i] + op))
        home = bool(A.off_home[i + 1])                  # the victim has the next possession
        rows.append(dict(decision="timeout", game_id=gid, period=int(A.period[i]), team=dfn, opponent=off, home=home,
                         sec_left=round(float(left), 1), elapsed=float(A.end[i]), margin=margin,
                         treated=bool(treated), outcome=k_at(K), outcome_b=k_at(2), outcome_c=k_at(12),
                         outcome_d=float(A.off_pts[i + 1] > 0), detail=f"{run}-0 run", size=int(run),
                         action_number=int(lo), later=bool(later and not treated), flag=bool(quick)))
    return rows


def challenge_points(gid, A, st, home_team, away_team, nick, cnt):
    """One row per coach's challenge of one game."""
    ev = st[st.action_type != "Full Timeout"]
    if ev.empty:
        return []
    teams = {home_team, away_team}

    def team_of(r):
        if isinstance(r.team_tricode, str) and r.team_tricode in teams:
            return r.team_tricode
        desc = r.description if isinstance(r.description, str) else ""
        if "[" in desc and "]" in desc:
            t = nick.get(desc[desc.index("[") + 1:desc.index("]")])
            if t in teams:
                return t
        return None

    out_ev = [r for r in ev.itertuples() if r.action_type in OUTCOME_KINDS]
    plain = [r for r in ev.itertuples() if r.action_type == "Challenge"]
    items = []
    for r in out_ev:
        t = team_of(r)
        dup = any(x[1] == t and x[2] == r.action_type and abs(x[0].action_number - r.action_number) <= 3
                  and x[0].period_t == r.period_t for x in items)
        if dup:
            cnt["challenge_duplicate_outcome"] += 1
            continue
        items.append((r, t, r.action_type))
    for r in plain:
        t = team_of(r)
        if any(x[1] == t and abs(x[0].action_number - r.action_number) <= PLAIN_GAP for x in items
               if x[2] in OUTCOME_KINDS):
            cnt["challenge_plain_companion"] += 1
            continue
        cnt["challenge_plain_alone"] += 1
        items.append((r, t, "Challenge"))
    rows = []
    for r, t, kind in items:
        if t is None:
            cnt["challenge_no_team"] += 1
            continue
        opp = away_team if t == home_team else home_team
        a = int(r.action_number)
        ks = np.nonzero((A.action_from >= 0) & (A.action_from <= a))[0]
        if len(ks) == 0:
            cnt["challenge_before_first_possession"] += 1
            continue
        k = int(ks[-1])
        k2 = k + 1 if k + 1 < A.n and A.period[k + 1] == A.period[k] else k
        result = OUTCOME_KINDS.get(kind, "unknown")
        net = sum(A.signed(j, t) for j in range(k, k2 + 1))
        hk = t == home_team
        m0 = (A.h0[k] - A.a0[k]) * (1 if hk else -1)
        m1 = (A.h1[k2] - A.a1[k2]) * (1 if hk else -1)
        per = int(r.period)
        left = (720.0 if per <= 4 else 300.0) - float(r.period_t) if r.period_t == r.period_t else np.nan
        rows.append(dict(decision="challenge", game_id=gid, period=per, team=t, opponent=opp, home=bool(hk),
                         sec_left=None if left != left else round(left, 1), elapsed=float(A.start[k]), margin=int(m0),
                         treated=None if result == "unknown" else result == "won",
                         end_elapsed=float(A.end[k2]), end_margin=int(m1), outcome_b=float(net),
                         detail=result, size=None, action_number=a, later=None))
    return rows


def foul_candidate(gid, A):
    """(possession index, previous possession's last event) of a game's first up-3-late possession, or None."""
    for i in range(A.n):
        p = A.period[i]
        left = period_end(p) - A.start[i]
        if p >= 4 and A.def_score[i] - A.off_score[i] == 3 and 0 < left <= UP3_MAX_LEFT and A.action_to[i] >= 0:
            return i, (int(A.action_to[i - 1]) if i > 0 else -1), float(left)
    return None


def twoforone_points(gid, A, first_left, cnt):
    rows = []
    for i in range(A.n):
        p = A.period[i]
        if p > 3 or A.empty[i]:
            continue
        left = period_end(p) - A.start[i]
        if not (TFO_LO <= left < TFO_HI):
            continue
        got = first_left.get((gid, int(A.poss_no[i])))
        if got is None:
            cnt["twoforone_no_first_event_time"] += 1
            continue
        fl, defence_timed = got
        if defence_timed:
            cnt["twoforone_first_event_defence_foul"] += 1
            continue
        team = A.offense[i]
        rest = [j for j in range(i, A.n) if A.period[j] == p]
        net = sum(A.signed(j, team) for j in rest)
        own = sum(1 for j in rest if A.offense[j] == team)
        rows.append(dict(decision="twoforone", game_id=gid, period=int(p), team=team, opponent=A.defense[i],
                         home=bool(A.off_home[i]), sec_left=round(float(left), 1), elapsed=float(A.start[i]),
                         margin=int(A.off_score[i] - A.def_score[i]), treated=bool(fl > TFO_LINE),
                         outcome=float(net), outcome_b=float(own - (len(rest) - own)),
                         outcome_c=float(A.off_pts[i]), outcome_d=float(net - A.off_pts[i]),
                         detail=START_GROUP.get(A.start_type[i], "other"),
                         size=None, action_number=int(A.action_from[i]), later=None, first_left=round(fl, 1)))
    return rows


def foul_up3_rows(conn, cands, games, cnt):
    """Parse the candidate games with the shared parser: did the leader foul before the offence's first shot?"""
    if not cands:
        return []
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    gdf, grouped = load_espn(conn, clock=True, game_ids=list(cands))
    rows = []
    for g in gdf.itertuples(index=False):
        c = cands[g.game_id]
        ev = grouped.get(g.game_id)
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        game.home = g.home_team
        pt = dict(zip(ev.action_number.astype(int), ev.period_t))
        foul_at = fga_at = None
        foul_left = None
        for e in game.parse():
            n = e["action_number"]
            if n <= c["prev_to"] or n > c["action_to"]:
                continue
            if fga_at is None and e["kind"] == "fg" and e["team"] == c["offense"]:
                fga_at = n
            if foul_at is None and e["kind"] is None and e["action"] in UP3_FOULS and e["team"] == c["defense"]:
                foul_at = n
                t = pt.get(n)
                foul_left = None if t is None or t != t else (720.0 if e["period"] <= 4 else 300.0) - float(t)
        treated = foul_at is not None and (fga_at is None or foul_at < fga_at)
        cnt["foul_up3_fouled"] += treated
        G = games.loc[g.game_id]
        lead_home = c["defense"] == G.home_team
        won = (G.final_home > G.final_away) == lead_home
        rows.append(dict(decision="foul_up3", game_id=g.game_id, season=int(g.season), period=c["period"], team=c["defense"],
                         opponent=c["offense"], home=bool(lead_home), sec_left=round(c["left"], 1),
                         elapsed=c["elapsed"], margin=3, treated=bool(treated), outcome=float(won),
                         outcome_b=float(c["tied_after"]), outcome_c=float(c["fg3m"] > 0),
                         detail=None if foul_left is None else f"foul at {foul_left:.1f} s",
                         size=None, action_number=int(c["action_to"]) if foul_at is None else int(foul_at), later=None))
    return rows


# ------------------------------------------------------------------ strata

TIMEOUT_LEFT = [300.0, 480.0]
TIMEOUT_MARGIN = [-9, -3, 4, 10]
UP3_LEFT = [6.0, 12.0, 18.0]


def pgroup(p):
    return "P12" if p <= 2 else "P3" if p == 3 else "P4+"


def add_strata(df):
    s, ts = [], []
    for r in df.itertuples():
        if r.decision == "timeout":
            mb = bucket(r.margin, TIMEOUT_MARGIN)
            s.append(f"{r.season}|{pgroup(r.period)}|L{bucket(r.sec_left, TIMEOUT_LEFT)}|M{mb}|R{min(r.size, 10)}")
            ts.append(f"{r.season}|{pgroup(r.period)}|M{bucket(r.margin, [-3, 4])}")
        elif r.decision == "challenge":
            s.append(f"{r.season}|{pgroup(r.period)}")
            ts.append(s[-1])
        elif r.decision == "foul_up3":
            s.append(f"L{bucket(r.sec_left, UP3_LEFT)}|{'REG' if r.period == 4 else 'OT'}")
            ts.append(s[-1])
        else:
            sb = int((r.sec_left - TFO_LO) // 2)
            s.append(f"{r.season}|P{r.period}|S{sb}|{r.detail}")
            ts.append(f"{r.season}|S{sb // 2}|{r.detail}")
    df["stratum"], df["team_stratum"] = s, ts
    return df


# ------------------------------------------------------------------ the test

class Matched:
    """Stratified effect on the treated, its permutation null and a game-clustered bootstrap."""

    def __init__(self, y, t, strata, clusters):
        self.y = np.asarray(y, float)
        self.t = np.asarray(t, bool)
        self.s = pd.factorize(pd.Series(strata))[0]
        self.c = pd.factorize(pd.Series(clusters))[0]
        self.S = self.s.max() + 1 if len(self.s) else 0
        self.idx0 = np.argsort(self.s, kind="stable")  # with idx1 below: a within-stratum relabelling
        n1 = np.bincount(self.s, self.t, self.S)
        n0 = np.bincount(self.s, ~self.t, self.S)
        self.valid = (n1 > 0) & (n0 > 0)
        self.n1, self.n0 = n1, n0

    def att(self, t=None, w=None):
        r = matched_att(self.y, self.t if t is None else t, self.s, self.S, w)
        return r[0], r[1], r[2]

    def null(self, rng, B):
        draws = np.empty(B)
        for b in range(B):
            idx1 = np.argsort(self.s + rng.random(len(self.y)))      # stratum blocks in order, random within each
            tp = np.empty_like(self.t)
            tp[self.idx0] = self.t[idx1]
            draws[b] = self.att(tp)[0]
        return draws

    def boot(self, rng, B):
        G = self.c.max() + 1
        out = np.empty(B)
        for b in range(B):
            w = np.bincount(rng.integers(0, G, G), minlength=G)[self.c].astype(float)
            out[b] = self.att(w=w)[0]
        return np.nanpercentile(out, [2.5, 97.5])


class Results:
    def __init__(self, perms, perms2, boot):
        self.perms, self.perms2, self.boot = perms, perms2, boot
        self.tests, self.summary, self.meta = [], [], []

    def add_meta(self, key, value, note=""):
        self.meta.append((key, None if value is None else float(value), note))

    def matched(self, level, family, key, unit_id, unit_name, season, y, t, strata, clusters, note=""):
        M = Matched(y, t, strata, clusters)
        used = M.valid[M.s]
        if not M.valid.any() or M.t[used].sum() < 5 or (~M.t[used]).sum() < 5:
            return None
        att, m1, m0 = M.att()
        rng = PB.rng_for("coaching", family, key, unit_id, season)
        draws = M.null(rng, self.perms)
        p, _ = PB.perm_p(att, draws)
        nperm = self.perms
        if p <= STAGE2_P and self.perms2 > self.perms:
            draws = M.null(PB.rng_for("coaching2", family, key, unit_id, season), self.perms2)
            p, _ = PB.perm_p(att, draws)
            nperm = self.perms2
        lo, hi = M.boot(PB.rng_for("coachingboot", family, key, unit_id, season), self.boot)
        nm, nsd, nlo, nhi = PB.null_summary(draws)
        row = dict(level=level, family=family, key=key, unit_id=str(unit_id), unit_name=unit_name, season=int(season),
                   n_treated=int(M.t[used].sum()), n_control=int((~M.t[used]).sum()), n_dropped=int((~used).sum()),
                   n_strata=int(M.valid.sum()), stat=att, ci_lo=float(lo), ci_hi=float(hi), treated_mean=m1,
                   control_mean=m0, null_mean=float(nm), null_sd=float(nsd), null_lo=float(nlo), null_hi=float(nhi),
                   p=float(p), n_perm=int(nperm), q=None, survives=None, note=note)
        self.tests.append(row)
        return row

    def family(self, family, key, label, unit_kind):
        rows = [r for r in self.tests if r["family"] == family]
        p = np.array([r["p"] for r in rows])
        reject, qv = PB.bh(p, FDR_Q)
        for r, rj, q in zip(rows, reject, qv):
            r["q"], r["survives"] = float(q), bool(rj)
        self.summary.append(dict(family=family, key=key, label=label, unit_kind=unit_kind, units=len(rows),
                                 p05=int((p < 0.05).sum()), expected=0.05 * len(rows), survivors=int(reject.sum())))
        log(f"  {family}: n={len(rows)} p<0.05: {int((p < 0.05).sum())} (exp {0.05 * len(rows):.1f}) "
            f"FDR survivors: {int(reject.sum())}")


def challenge_team_family(res, ch):
    """Per team: success rate minus the league's in the same strata; null = outcomes shuffled within strata."""
    d = ch[ch.treated.notna()].reset_index(drop=True)
    y = d.treated.astype(bool).to_numpy()
    s = pd.factorize(d.stratum)[0]
    team, teams = pd.factorize(d.team)
    exp = pd.Series(y.astype(float)).groupby(s).transform("mean").to_numpy()
    n_t = np.bincount(team)

    def stat(yy):
        return np.bincount(team, yy - exp, len(teams)) / n_t

    obs = stat(y)
    idx0 = np.argsort(s, kind="stable")

    def draws(rng, B):
        out = np.empty((B, len(teams)))
        for b in range(B):
            idx1 = np.argsort(s + rng.random(len(y)))
            yp = np.empty_like(y, dtype=float)
            yp[idx0] = y[idx1]
            out[b] = stat(yp)
        return out

    D = draws(PB.rng_for("coaching", "challenge:team"), res.perms)
    p, m, nf = PB.perm_p_matrix(obs, D)
    nperm = np.full(len(teams), res.perms)
    screen = np.nonzero(p <= STAGE2_P)[0]
    if len(screen) and res.perms2 > res.perms:
        D2 = draws(PB.rng_for("coaching2", "challenge:team"), res.perms2)
        p2, _m2, _ = PB.perm_p_matrix(obs, D2)
        p[screen] = p2[screen]
        nperm[screen] = res.perms2
    summ = np.array(PB.null_summary(D))
    if len(screen) and res.perms2 > res.perms:
        summ[:, screen] = np.array(PB.null_summary(D2))[:, screen]
    nm, nsd, nlo, nhi = summ
    for j, t in enumerate(teams):
        mine = d.team == t
        res.tests.append(dict(level="team", family="challenge:team", key="challenge:team", unit_id=t, unit_name=t,
                              season=0, n_treated=int(y[mine].sum()), n_control=int((~y[mine]).sum()), n_dropped=0,
                              n_strata=int(len(set(s[mine]))), stat=float(obs[j]), ci_lo=None, ci_hi=None,
                              treated_mean=float(y[mine].mean()), control_mean=float(exp[mine].mean()),
                              null_mean=float(nm[j]), null_sd=float(nsd[j]), null_lo=float(nlo[j]), null_hi=float(nhi[j]),
                              p=float(p[j]), n_perm=int(nperm[j]), q=None, survives=None,
                              note="stat = success rate minus the league's in the same season x period group"))


# ------------------------------------------------------------------ main

ROW_COLS = ["decision", "game_id", "season", "period", "team", "opponent", "home", "sec_left", "margin", "wp", "lev",
            "exp_margin", "stratum", "team_stratum", "treated", "outcome", "outcome_b", "outcome_c", "outcome_d",
            "detail", "size", "action_number", "later", "flag"]
ROW_DDL = """decision TEXT NOT NULL, game_id TEXT NOT NULL, season SMALLINT NOT NULL, period SMALLINT NOT NULL,
    team TEXT NOT NULL, opponent TEXT NOT NULL, home BOOLEAN NOT NULL, sec_left REAL, margin SMALLINT NOT NULL,
    wp REAL, lev REAL, exp_margin REAL, stratum TEXT NOT NULL, team_stratum TEXT NOT NULL, treated BOOLEAN,
    outcome REAL, outcome_b REAL, outcome_c REAL, outcome_d REAL, detail TEXT, size SMALLINT, action_number INTEGER,
    later BOOLEAN, flag BOOLEAN"""
TEST_COLS = ["level", "family", "key", "unit_id", "unit_name", "season", "n_treated", "n_control", "n_dropped",
             "n_strata", "stat", "ci_lo", "ci_hi", "treated_mean", "control_mean", "null_mean", "null_sd", "null_lo",
             "null_hi", "p", "n_perm", "q", "survives", "note"]
TEST_DDL = """level TEXT NOT NULL, family TEXT NOT NULL, key TEXT NOT NULL, unit_id TEXT NOT NULL, unit_name TEXT,
    season SMALLINT NOT NULL, n_treated INTEGER, n_control INTEGER, n_dropped INTEGER, n_strata INTEGER,
    stat DOUBLE PRECISION, ci_lo DOUBLE PRECISION, ci_hi DOUBLE PRECISION, treated_mean DOUBLE PRECISION,
    control_mean DOUBLE PRECISION, null_mean DOUBLE PRECISION, null_sd DOUBLE PRECISION, null_lo DOUBLE PRECISION,
    null_hi DOUBLE PRECISION, p DOUBLE PRECISION, n_perm INTEGER, q DOUBLE PRECISION, survives BOOLEAN, note TEXT,
    PRIMARY KEY (family, key, unit_id, season)"""
SUMMARY_COLS = ["family", "key", "label", "unit_kind", "units", "p05", "expected", "survivors"]
SUMMARY_DDL = """family TEXT NOT NULL, key TEXT PRIMARY KEY, label TEXT, unit_kind TEXT, units INTEGER, p05 INTEGER,
    expected DOUBLE PRECISION, survivors INTEGER"""
META_DDL = "key TEXT PRIMARY KEY, value DOUBLE PRECISION, note TEXT"


def write(conn, rows, res):
    cur = conn.cursor()
    for t, ddl in (("coaching_decisions", ROW_DDL), ("coaching_decision_tests", TEST_DDL),
                   ("coaching_decision_summary", SUMMARY_DDL), ("coaching_decision_meta", META_DDL)):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute(f"CREATE TABLE {t} ({ddl})")
    vals = [tuple(None if (isinstance(v, float) and v != v) else v for v in r)
            for r in rows[ROW_COLS].astype(object).itertuples(index=False)]
    psycopg2.extras.execute_values(cur, "INSERT INTO coaching_decisions VALUES %s", vals, page_size=5000)
    cur.execute("CREATE INDEX ON coaching_decisions (decision, team)")
    psycopg2.extras.execute_values(cur, "INSERT INTO coaching_decision_tests VALUES %s",
                                   [tuple(r[c] for c in TEST_COLS) for r in res.tests])
    psycopg2.extras.execute_values(cur, "INSERT INTO coaching_decision_summary VALUES %s",
                                   [tuple(r[c] for c in SUMMARY_COLS) for r in res.summary])
    psycopg2.extras.execute_values(cur, "INSERT INTO coaching_decision_meta VALUES %s", sorted(res.meta))
    conn.commit()
    for t in ("coaching_decisions", "coaching_decision_tests", "coaching_decision_summary", "coaching_decision_meta"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        log(f"{t}: {n} rows, {size}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--perms", type=int, default=PERMS)
    ap.add_argument("--perms2", type=int, default=PERMS2)
    ap.add_argument("--boot", type=int, default=BOOT)
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    games = load_games(conn)
    nick = load_nicknames(conn)
    cnt = defaultdict(int)
    cnt_sens = {r: defaultdict(int) for r in RUN_SENSITIVITY}
    rows, sens_rows, up3 = [], {r: [] for r in RUN_SENSITIVITY}, {}
    for season in SEASONS:
        P = load_possessions(conn, season)
        ST = load_stoppages(conn, season)
        FL = first_counted_left(conn, season)
        st_by = dict(tuple(ST.groupby("game_id", sort=False)))
        empty = ST.iloc[0:0]
        n0 = len(rows)
        for gid, d in P.groupby("game_id", sort=False):
            A = GameArrays(d)
            st = st_by.get(gid, empty)
            tos, chal = timeouts_of_game(st)
            cnt["timeouts_logged"] += int((st.action_type == "Full Timeout").sum())
            cnt["timeouts_challenge_own"] += int((st.action_type == "Full Timeout").sum()) - len(tos)
            for r in timeout_points(gid, A, tos, chal, RUN_MIN, cnt):
                rows.append({**r, "season": season})
            for rm in RUN_SENSITIVITY:
                for r in timeout_points(gid, A, tos, chal, rm, cnt_sens[rm]):
                    sens_rows[rm].append({**r, "season": season})
            G = games.loc[gid]
            for r in challenge_points(gid, A, st, G.home_team, G.away_team, nick, cnt):
                rows.append({**r, "season": season})
            for r in twoforone_points(gid, A, FL, cnt):
                rows.append({**r, "season": season})
            c = foul_candidate(gid, A)
            if c is not None:
                i, prev_to, left = c
                p_end = [j for j in range(A.n) if A.period[j] == A.period[i]][-1]
                up3[gid] = dict(period=int(A.period[i]), offense=A.offense[i], defense=A.defense[i], prev_to=prev_to,
                                action_to=int(A.action_to[i]), left=left, elapsed=float(A.start[i]),
                                fg3m=int(A.fg3m[i]), tied_after=bool(A.h1[p_end] == A.a1[p_end]))
        log(f"{season}: {len(P):,} possessions, {len(rows) - n0:,} decision points")
        del P, ST
    rows += foul_up3_rows(conn, up3, games, cnt)
    df = pd.DataFrame(rows)

    # Win probability (Game Replay's model) for the decider at the moment, and the value of one point there.
    model, scaler = W.load_model()
    secs = np.array([wp_secs(p, e) for p, e in zip(df.period, df.elapsed)])
    sign = np.where(df.home, 1, -1)
    mh = df.margin.to_numpy() * sign
    wp_home = W.compute_win_probs(model, scaler, secs, mh)
    df["wp"] = np.where(df.home, wp_home, 1 - wp_home)
    df["lev"] = (W.compute_win_probs(model, scaler, secs, mh + 1) - W.compute_win_probs(model, scaler, secs, mh - 1)) / 2
    ch = df.decision == "challenge"
    secs_end = np.array([wp_secs(p, e) for p, e in zip(df.period[ch], df.end_elapsed[ch])])
    wp_end_home = W.compute_win_probs(model, scaler, secs_end, df.end_margin[ch].to_numpy() * sign[ch])
    df.loc[ch, "outcome"] = np.where(df.home[ch], wp_end_home, 1 - wp_end_home) - df.wp[ch]
    em = games.exp_margin.reindex(df.game_id).to_numpy()
    df["exp_margin"] = np.where(df.home, em, -em)
    df = add_strata(df)
    df = df.sort_values(["decision", "game_id", "action_number"]).reset_index(drop=True)

    res = Results(args.perms, args.perms2, args.boot)
    for k, v in sorted(cnt.items()):
        res.add_meta(f"count:{k}", v)
    for rm in RUN_SENSITIVITY:
        for k, v in sorted(cnt_sens[rm].items()):
            res.add_meta(f"count:run{rm}:{k}", v)
    for dname in DECISIONS:
        d = df[df.decision == dname]
        res.add_meta(f"rows:{dname}", len(d))
        res.add_meta(f"treated:{dname}", int((d.treated == True).sum()))  # noqa: E712
    for k, v in (("run_min", RUN_MIN), ("min_left", MIN_LEFT), ("k_possessions", K), ("challenge_gap", CHALLENGE_GAP),
                 ("plain_gap", PLAIN_GAP), ("up3_max_left", UP3_MAX_LEFT), ("twoforone_lo", TFO_LO),
                 ("twoforone_hi", TFO_HI), ("twoforone_line", TFO_LINE), ("perms1", args.perms),
                 ("perms2", args.perms2), ("boot", args.boot), ("stage2_p", STAGE2_P), ("fdr_q", FDR_Q),
                 ("seed", PB.SEED)):
        res.add_meta(f"param:{k}", v)
    res.add_meta("param:dead_ball_gap", DEAD_BALL_GAP)

    log("league tests")
    league = {}
    for dname in DECISIONS:
        d = df[(df.decision == dname) & df.treated.notna()]
        league[dname] = res.matched("league", "coaching:league", dname, "ALL", "League", 0, d.outcome,
                                    d.treated.astype(bool), d.stratum, d.game_id, note=LABELS[dname])
        r = league[dname]
        log(f"  {dname}: ATT {r['stat']:+.4f} [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] treated {r['n_treated']} "
            f"control {r['n_control']} p {r['p']:.4g}")
        # Balance: pre-game expected margin and margin at the moment, treated vs matched control.
        for cov in ("exp_margin", "margin", "sec_left"):
            dd = d[d[cov].notna()]
            M = Matched(dd[cov], dd.treated.astype(bool), dd.stratum, dd.game_id)
            res.add_meta(f"balance:{dname}:{cov}", M.att()[0], "treated minus matched control (same strata)")
    res.family("coaching:league", "coaching:league", "Four coaching beliefs, league level", "decision")

    log("secondary outcomes and sensitivity (league, no family)")
    for dname, col, what in (("timeout", "outcome_b", "next 2 possessions"), ("timeout", "outcome_c", "next 12 possessions"),
                             ("timeout", "outcome_d", "victim scores on its next possession"),
                             ("challenge", "outcome_b", "net points over the two possessions"),
                             ("foul_up3", "outcome_b", "period ends tied"), ("foul_up3", "outcome_c", "offence makes a three"),
                             ("twoforone", "outcome_b", "own minus opponent possessions to the period's end"),
                             ("twoforone", "outcome_c", "points on the possession itself"),
                             ("twoforone", "outcome_d", "net points after the possession itself")):
        d = df[(df.decision == dname) & df.treated.notna()]
        r = res.matched("league", f"secondary:{dname}", col, "ALL", "League", 0, d[col], d.treated.astype(bool),
                        d.stratum, d.game_id, note=what)
        log(f"  {dname} {col}: {r['stat']:+.4f} [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] p {r['p']:.4g}")
    for rm in RUN_SENSITIVITY:
        d = add_strata(pd.DataFrame([{**r, "decision": "timeout"} for r in sens_rows[rm]]))
        res.add_meta(f"rows:timeout_run{rm}", len(d))
        r = res.matched("league", "sensitivity:timeout", f"run{rm}", "ALL", "League", 0, d.outcome, d.treated, d.stratum,
                        d.game_id, note=f"timeout after a {rm}-0 run, next {K} possessions")
        log(f"  timeout run {rm}: {r['stat']:+.4f} [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] p {r['p']:.4g} "
            f"({r['n_treated']} timeouts)")
    d = df[df.decision == "timeout"]
    r = res.matched("league", "sensitivity:timeout", "dead_ball", "ALL", "League", 0, d.outcome, d.flag.astype(bool),
                    d.stratum, d.game_id, note=f"only timeouts within {DEAD_BALL_GAP:g} s of the score on the corrected "
                                               "clock are treated; the other order-based timeouts become controls")
    log(f"  timeout dead ball only: {r['stat']:+.4f} [{r['ci_lo']:+.4f}, {r['ci_hi']:+.4f}] p {r['p']:.4g} "
        f"({r['n_treated']} timeouts)")

    log("season by season (league, no family)")
    for dname in DECISIONS:
        if dname == "foul_up3":
            continue
        for season in SEASONS:
            d = df[(df.decision == dname) & df.treated.notna() & (df.season == season)]
            res.matched("season", f"season:{dname}", dname, "ALL", "League", season, d.outcome,
                        d.treated.astype(bool), d.stratum, d.game_id)

    log("team families")
    for dname in ("timeout", "twoforone"):
        d = df[df.decision == dname]
        for team in sorted(d.team.unique()):
            dt = d[d.team == team]
            res.matched("team", f"{dname}:team", f"{dname}:team", team, team, 0, dt.outcome, dt.treated.astype(bool),
                        dt.team_stratum, dt.game_id)
        res.family(f"{dname}:team", f"{dname}:team", UNIT_LABELS[f"{dname}:team"], "franchise")
    challenge_team_family(res, df[df.decision == "challenge"])
    res.family("challenge:team", "challenge:team", UNIT_LABELS["challenge:team"], "franchise")

    # Challenge success, the plain counts (the page shows them by season and team from coaching_decisions).
    chd = df[(df.decision == "challenge")]
    for k, v in chd.detail.value_counts().items():
        res.add_meta(f"challenge:{k}", v)
    dec = chd[chd.treated.notna()]
    res.add_meta("challenge:success_rate", dec.treated.astype(float).mean())
    res.add_meta("challenge:median_lev", chd.lev.median(), "win probability per point at the challenges' moments")
    res.add_meta("timeout:later_share_controls", df[(df.decision == "timeout") & (df.treated == False)].later.mean())  # noqa: E712
    res.add_meta("runtime_seconds", time.time() - T0)
    write(conn, df, res)
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
