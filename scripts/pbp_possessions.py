"""
pbp_possessions.py
==================
Cuts a game into possessions on top of the shared lineup parser
(pbp_lineups.Game.walk(): same parse, same event order), so
there is still one play-by-play parser. build_possessions.py writes the
tables; anything that needs possession state (Step 5's coaching decisions)
should build on `possessions()` here, not re-derive it.

Rules (judgment calls, all in one place):
  - A possession belongs to the team with the ball. It ends on
      made_fg   a made field goal, unless the same team's free throws follow
                before the other team does anything (an and-one: the
                possession then ends on the last free throw);
      made_ft   the last free throw of a trip, made, when the ball isn't
                kept (flagrant and clear-path trips, and the one shot after
                a transition take foul or an away-from-play foul, keep it);
      dreb      a player's defensive rebound of a missed field goal;
      dreb_ft   a player's defensive rebound of a missed last free throw;
      team_dreb a team defensive rebound (the ball went out of bounds off
                the shooting side, or the shot clock/period ran out), except
                one between two free throws of a trip (a dead-ball rebound);
      steal     a turnover with a steal (live ball);
      dead_tov  any other turnover (out of bounds, violation, offensive
                foul, shot clock);
      jump_ball a held ball won by the other team;
      period_end the end of the period;
      other     the other team acts (shot, free throw, turnover) with no
                ending event logged before it (a foul in the bonus by the
                team with the ball, or a gap in ESPN's log).
    The next possession's start_type is the previous one's end_type
    ("period_start" for a period's first).
  - Every counted event (shot, free throw, rebound, turnover) belongs to
    exactly one possession: the possession's events run from the one after
    the previous ending event to its own ending event, so a defensive
    rebound belongs to the possession it ends (that is how the box score
    credits it). Points, attempts and offensive rebounds are the offence's.
  - Technical free throws don't change possession; their points go to
    `off_tech_pts` / `def_tech_pts` of the possession in progress (or the
    one just ended, between possessions), never to `pts`.
  - A possession is opened when its team first does something. A period
    that ends with time left after a possession ended and before the other
    team acted (a made basket with 3 s left, then nothing) gets an empty
    possession when at least EMPTY_MIN_SECONDS were left; `empty` marks it
    (no shot, free throw or turnover), so it can be left out of any count
    that has to match the box-score estimate.
  - Second-chance points: points scored after an offensive rebound of a
    missed field goal or missed last free throw (team rebounds included,
    dead-ball rebounds between free throws not).
  - first_attempt_sec: seconds from the possession's start to its first
    field-goal attempt or non-technical free throw (NULL if none). Whether
    that is "transition" is decided by build_possessions.py's window.

Clock: possessions() takes the times it is given. build_possessions.py
passes corrected_clock() (below), because ESPN's own clock is late by event
type (made shots a median 14 s); without a clock it uses Game.walk()'s,
i.e. ESPN's. The rebuilt clock is within 2 s of NBA.com's for ~94% of
events; turnovers stay the weak spot (see corrected_clock()).
"""

import re
from collections import Counter

from pbp_lineups import PERIOD_SECONDS, OT_SECONDS, period_bounds

EMPTY_MIN_SECONDS = 1.0
FT_OF_RE = re.compile(r"(\d) of (\d)")
RETAIN_FOULS = {"Transition Take Foul", "Away from Play Foul"}
JUMP_BALLS = {"Jumpball", "Jump Ball"}
END_TYPES = ["made_fg", "made_ft", "dreb", "dreb_ft", "team_dreb", "steal", "dead_tov", "jump_ball",
             "period_end", "other"]
START_TYPES = ["period_start"] + [t for t in END_TYPES if t != "period_end"]
COUNT_KINDS = ("fg", "ft", "oreb", "dreb", "tov")


def ft_trip(action):
    """(shot k, of n, technical, keeps the ball) for a free-throw action type."""
    if "Technical" in action:
        return None, None, True, False
    m = FT_OF_RE.search(action)
    k, n = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
    return k, n, False, ("Flagrant" in action or "Clear Path" in action)


# ESPN's clock is late by event type (measured against NBA.com's own play-by-play of the same 418 games of
# 2024-25 kept in pbp_events as nba_api twins; build_possessions.py re-measures and stores it): made shots and
# made last free throws a median 14 s, rebounds 6 s, turnovers 5 s (steals) or 10 s (dead ball, bimodal: on time
# or 11-15 s late), misses 2 s; fouls called with the clock stopped and the first free throw of a trip are on time
# (offensive fouls are as late as the turnover they cause). corrected_clock() rebuilds a clock for the counted events from the reliable parts.
LAG_FG_MADE = 14.0      # ESPN minus NBA, median, for a field goal the shot chart can't match
LAG_FG_MISS = 2.0
LAG_TOV_STEAL = 5.0
LAG_TOV_DEAD = 10.0
LAG_REB = 6.0           # a rebound with no miss before it in the period
REB_GAP = 2.0           # NBA.com: a rebound comes a median 2 s after the miss


def espn_t(e):
    """Seconds into the period by ESPN's clock, as Game.walk() reads it (before its no-going-back rule)."""
    start, length = period_bounds(e["period"])
    return min(max(start - e["secs"], 0.0), float(length))


CLOCK_SOURCES = ("chart", "ft_trip", "rebound", "lag", "espn")


def corrected_clock(events, chart_t, how=None):
    """({action_number: seconds into the period}, {action_numbers whose time is anchored}) for every event of a
    parsed game (Game.parse(), home set).

    Anchors (believed to the second): field goals matched to the NBA shot chart take the chart's clock
    (`chart_t`: {action_number: seconds into the period}); a free-throw trip of two or three takes the
    first free throw's ESPN time (the clock is stopped and ESPN logs it on time), a trip of one the made
    shot it completes (an and-one) or else the foul before it. Derived: a rebound comes REB_GAP after the
    miss it follows (never later than ESPN says); a turnover ESPN's time minus its median lag (LAG_TOV_STEAL
    or LAG_TOV_DEAD); an unmatched field goal ESPN's minus its lag; anything else ESPN's own.
    Derived times are kept between the anchors around them, and the clock never runs backwards.
    Anchored: the anchors themselves and rebounds of an anchored miss. Turnovers never are: ESPN stamps a
    turnover at about the time of the next thing that happens (NBA.com: steal to next attempt a median 6 s;
    ESPN's lag on steals: a median 5 s), so the time a turnover happened can't be recovered from ESPN.

    `how` (optional dict) is filled with {action_number: (source, bounded)}: source is one of CLOCK_SOURCES
    (chart = the shot chart's clock; ft_trip = the free-throw trip's time; rebound = REB_GAP after its miss;
    lag = ESPN's time less a median lag; espn = ESPN's as is), bounded is True when the rule keeping derived
    times between anchors and the clock from running backwards moved it. The times returned don't depend on
    it (build_event_clock.py stores both in pbp_event_clock)."""
    out = {}
    anchored = set()
    by_period = {}
    for e in events:
        by_period.setdefault(e["period"], []).append(e)
    for period, evs in by_period.items():
        raw = [espn_t(e) for e in evs]
        t = [None] * len(evs)
        hard = [False] * len(evs)
        src = ["espn"] * len(evs)
        last_miss = None          # (corrected time, anchored) of the latest miss
        last_foul = None          # time of the latest foul by ESPN's clock
        last_made_fg = None       # (team, corrected time) while the and-one window is open
        hard_made_fg = False
        trip_t, trip_hard = None, False
        for i, e in enumerate(evs):
            kind, action = e["kind"], e["action"]
            n = e["action_number"]
            if kind == "fg":
                if n in chart_t:
                    t[i], hard[i] = chart_t[n], True
                    src[i] = "chart"
                else:
                    t[i] = raw[i] - (LAG_FG_MADE if e["made"] else LAG_FG_MISS)
                    src[i] = "lag"
                last_made_fg = (e["team"], t[i]) if e["made"] else None
                last_miss = None if e["made"] else (t[i], hard[i])
            elif kind == "ft":
                k, m, tech, _ = ft_trip(action)
                if tech:
                    t[i] = raw[i]
                    continue
                if k == 1:
                    if m >= 2:
                        trip_t, trip_hard = raw[i], True
                    elif last_made_fg is not None and last_made_fg[0] == e["team"]:
                        trip_t, trip_hard = last_made_fg[1], hard_made_fg
                    elif last_foul is not None:
                        trip_t, trip_hard = last_foul, False
                    else:
                        trip_t, trip_hard = raw[i], False
                t[i] = trip_t if trip_t is not None else raw[i]
                hard[i] = trip_t is not None and trip_hard
                src[i] = "ft_trip" if trip_t is not None else "espn"
                last_miss = None if e["made"] else (t[i], hard[i])
            elif kind in ("oreb", "dreb"):
                if last_miss is not None:
                    t[i] = min(last_miss[0] + REB_GAP, raw[i])
                    src[i] = "rebound"
                    if last_miss[1]:
                        anchored.add(n)
                else:
                    t[i] = raw[i] - LAG_REB
                    src[i] = "lag"
                last_miss = last_made_fg = None
            elif kind == "tov":
                t[i] = raw[i] - (LAG_TOV_STEAL if e.get("steal") else LAG_TOV_DEAD)
                src[i] = "lag"
                last_miss = last_made_fg = None
            else:
                t[i] = raw[i]
                if "Foul" in action and e["team"] is not None:
                    last_foul = raw[i]
            if kind == "fg":
                hard_made_fg = hard[i]
        # Derived times stay between the hard anchors around them; then the clock never goes backwards.
        nxt_hard = [None] * len(evs)
        upcoming = None
        for i in range(len(evs) - 1, -1, -1):
            nxt_hard[i] = upcoming
            if hard[i]:
                upcoming = t[i]
        clock = 0.0
        for i, e in enumerate(evs):
            x = t[i]
            if not hard[i] and nxt_hard[i] is not None:
                x = min(x, nxt_hard[i])
            clock = max(clock, x, 0.0)
            out[e["action_number"]] = clock
            if how is not None:
                how[e["action_number"]] = (src[i], clock != t[i])
            if hard[i]:
                anchored.add(e["action_number"])
    return out, anchored


def possessions(game, home, clock=None):
    """Every possession of `game` (a pbp_lineups.Game) with `home` the home side.

    Returns (rows, diag). Each row: period, t0/t1 (seconds into the period),
    offense, start_type, end_type, action_from/action_to (inclusive range of
    its counted events, None if it has none), first_attempt_sec, the
    offence's fga/fgm/fg3a/fg3m/fta/ftm/oreb (player rebounds)/tov, team_oreb (team offensive rebounds
    not between two free throws),
    pts_shots (offence points from made shots and non-technical free throws),
    off_tech_pts/def_tech_pts (and their attempts, *_tech_fta), second_chance_pts, and_one, and per side
    (h_/a_) the running-maximum score steps credited while it was in
    progress (score_h/score_a, the fallback when a game's shots don't add up).

    `clock` ({action_number: seconds into the period}, from corrected_clock()) replaces ESPN's own times;
    without it the times are Game.walk()'s, i.e. ESPN's. start_action / end_action / first_attempt_action are
    the event numbers behind t0 / t1 / first_attempt_sec (None: a period's start or end, or an unlogged change),
    so a caller can tell which of those times are anchored."""
    away = None
    out = []
    cur = None          # open possession
    last = None         # last closed possession of this period (techs between possessions land here)
    nxt = None          # (team, start_type, t, action) for the next possession, set by an ending event
    pending_make = None  # possession closed by a made shot, still open to an and-one
    trip = {"team": None, "left": 0, "keep": False}
    last_foul = None
    high = {"h": 0, "a": 0}
    raw = {"h": 0, "a": 0}
    diag = Counter()
    period_len = {"len": PERIOD_SECONDS}
    early = {"score_h": 0, "score_a": 0, "techs": []}

    def side(team):
        return "h" if team == home else "a" if team == away else None

    def new(team, start_type, t0, period):
        return {"period": period, "t0": t0, "t1": t0, "offense": team, "start_type": start_type, "end_type": None,
                "action_from": None, "action_to": None, "first_attempt_sec": None,
                "fga": 0, "fgm": 0, "fg3a": 0, "fg3m": 0, "fta": 0, "ftm": 0, "oreb": 0, "tov": 0,
                "pts_shots": 0, "off_tech_pts": 0, "def_tech_pts": 0, "off_tech_fta": 0, "def_tech_fta": 0,
                "second_chance_pts": 0, "team_oreb": 0, "and_one": False,
                "start_action": None, "end_action": None, "first_attempt_action": None,
                "score_h": 0, "score_a": 0, "_second": False, "_last_miss": None}

    def close(p, end_type, t, n=None):
        nonlocal cur, last
        p["end_type"] = end_type
        p["end_action"] = n
        p["t1"] = max(t, p["t0"])
        out.append(p)
        last = p
        if cur is p:
            cur = None

    def settle_make():
        """The and-one window closes: the made-shot possession stays ended."""
        nonlocal pending_make
        pending_make = None

    def open_for(team, period, t):
        """The possession `team` is in, opening one if needed."""
        nonlocal cur, nxt
        if cur is not None and cur["offense"] == team:
            return cur
        if cur is not None:
            diag["change_without_end"] += 1
            close(cur, "other", t)
            cur = new(team, "other", t, period)
        elif nxt is not None and nxt[0] == team:
            cur = new(team, nxt[1], nxt[2], period)
            cur["start_action"] = nxt[3]
            if early.get("take") is not None:
                cur["take_foul_sec"] = early["take"]
        elif nxt is not None:
            diag["same_team_again"] += 1          # the other side's possession left no trace in the log
            cur = new(team, "other", t, period)
        else:
            cur = new(team, "period_start", 0.0, period)
        # Score steps and technicals logged before the period's first possession belong to it.
        for k in ("score_h", "score_a"):
            cur[k] += early[k]
        for tm, pts, n in early["techs"]:
            who = "off" if tm == team else "def"
            cur[who + "_tech_pts"] += pts
            cur[who + "_tech_fta"] += 1
            cur["action_from"] = n if cur["action_from"] is None else min(cur["action_from"], n)
            cur["action_to"] = n if cur["action_to"] is None else max(cur["action_to"], n)
        early.update(score_h=0, score_a=0, techs=[])
        nxt = None
        early["take"] = None
        return cur

    def touch(p, e):
        n = e["action_number"]
        p["action_from"] = n if p["action_from"] is None else p["action_from"]
        p["action_to"] = n

    def on_period_start(period, lineups):
        nonlocal away, cur, last, nxt, pending_make, last_foul
        if away is None:
            away = game.other(home)
        cur = last = nxt = pending_make = last_foul = None
        if early["score_h"] or early["score_a"] or early["techs"]:
            diag["period_without_possession"] += 1
        early.update(score_h=0, score_a=0, techs=[])
        trip.update(team=None, left=0, keep=False)
        period_len["len"] = PERIOD_SECONDS if period <= 4 else OT_SECONDS

    def on_period_end(period, lineups):
        nonlocal nxt
        length = period_len["len"]
        if cur is not None:
            close(cur, "period_end", length)
        elif nxt is not None and length - nxt[2] >= EMPTY_MIN_SECONDS:
            p = new(nxt[0], nxt[1], nxt[2], period)
            p["start_action"] = nxt[3]
            close(p, "period_end", length)
            p["_empty"] = True
        nxt = None

    def on_time(period, t0, t1, lineups):
        pass

    def on_event(e, lineups, period, t):
        if clock is not None:
            t = clock.get(e["action_number"], t)
        if e["kind"] == "sub":
            game.apply_sub(e, lineups)
        handle(e, period, t)
        # Score fields: running maximum, credited after the event to the possession it belongs to (the one in
        # progress, or the one it just ended); before a period's first possession they wait for it.
        raw["h"] += e["dh"]
        raw["a"] += e["da"]
        holder = cur if cur is not None else last
        for s in ("h", "a"):
            if raw[s] > high[s]:
                if holder is not None:
                    holder["score_" + s] += raw[s] - high[s]
                else:
                    early["score_" + s] += raw[s] - high[s]
                high[s] = raw[s]

    def handle(e, period, t):
        nonlocal cur, nxt, pending_make, last_foul
        kind, team, action = e["kind"], e["team"], e["action"]
        if kind is None:
            if "Foul" in action and team is not None:
                last_foul = action
                if action == "Transition Take Foul":     # kept for the transition-window check
                    if cur is not None and cur["offense"] != team:
                        cur.setdefault("take_foul_sec", t - cur["t0"])
                    elif cur is None and nxt is not None and nxt[0] != team:
                        early["take"] = t - nxt[2]
            if action in JUMP_BALLS and team is not None:
                if cur is not None and cur["offense"] != team and cur["action_from"] is not None:
                    close(cur, "jump_ball", t, e["action_number"])
                    nxt = (team, "jump_ball", t, e["action_number"])
            return
        if kind not in COUNT_KINDS:
            return
        if side(team) is None:
            diag["no_team"] += 1
            return

        if kind == "ft":
            k, n, tech, keep = ft_trip(action)
            if tech:
                holder = cur if cur is not None else last
                pts = 1 if e["made"] else 0
                if holder is None:      # before the period's first possession: it waits for that one
                    early["techs"].append((team, pts, e["action_number"]))
                    return
                touch(holder, e)
                who = "off" if team == holder["offense"] else "def"
                holder[who + "_tech_pts"] += pts
                holder[who + "_tech_fta"] += 1
                return
            if pending_make is not None and pending_make["offense"] == team and cur is None:
                # and-one: the made-shot possession continues to the free throw
                cur = pending_make
                cur["and_one"] = True
                out.remove(cur)
                nxt = None
            settle_make()
            p = open_for(team, period, t)
            touch(p, e)
            if p["first_attempt_sec"] is None:
                p["first_attempt_sec"] = max(t - p["t0"], 0.0)
                p["first_attempt_action"] = e["action_number"]
            p["fta"] += 1
            if e["made"]:
                p["ftm"] += 1
                p["pts_shots"] += 1
                if p["_second"]:
                    p["second_chance_pts"] += 1
            if k is not None and n is not None:
                keep = keep or (n == 1 and last_foul in RETAIN_FOULS)
                trip.update(team=team, left=n - k, keep=keep)
                if k == n:
                    if e["made"] and not keep:
                        close(p, "made_ft", t, e["action_number"])
                        nxt = (game.other(team), "made_ft", t, e["action_number"])
                    elif not e["made"]:
                        p["_last_miss"] = "ft"
            return

        settle_make()
        if kind == "fg":
            p = open_for(team, period, t)
            touch(p, e)
            if p["first_attempt_sec"] is None:
                p["first_attempt_sec"] = max(t - p["t0"], 0.0)
                p["first_attempt_action"] = e["action_number"]
            p["fga"] += 1
            three = e["val"] == 3
            p["fg3a"] += three
            trip.update(team=None, left=0, keep=False)
            if e["made"]:
                p["fgm"] += 1
                p["fg3m"] += three
                p["pts_shots"] += e["val"]
                if p["_second"]:
                    p["second_chance_pts"] += e["val"]
                close(p, "made_fg", t, e["action_number"])
                pending_make = p
                nxt = (game.other(team), "made_fg", t, e["action_number"])
            else:
                p["_last_miss"] = "fg"
            return

        if kind == "oreb":
            between_fts = trip["team"] == team and trip["left"] > 0
            if cur is None or cur["offense"] != team:
                diag["oreb_without_possession"] += 1
            p = open_for(team, period, t)
            touch(p, e)
            if e["pid"]:
                p["oreb"] += 1
            elif not between_fts:
                p["team_oreb"] += 1     # out of bounds off the defence: the box-score estimate doesn't subtract these
            if not between_fts and p["_last_miss"] is not None:
                p["_second"] = True
            return

        if kind == "dreb":
            between_fts = trip["left"] > 0 and trip["team"] is not None and trip["team"] != team
            if cur is not None and cur["offense"] != team:
                if between_fts and not e["pid"]:
                    touch(cur, e)               # dead-ball team rebound between two free throws
                    diag["team_dreb_between_fts"] += 1
                    return
                touch(cur, e)
                end = "team_dreb" if not e["pid"] else ("dreb_ft" if cur["_last_miss"] == "ft" else "dreb")
                close(cur, end, t, e["action_number"])
                nxt = (team, end, t, e["action_number"])
                trip.update(team=None, left=0, keep=False)
            elif cur is not None:
                touch(cur, e)                   # the team with the ball "defensive" rebounds: a mislabel
                diag["dreb_by_offense"] += 1
            else:
                holder = last
                if holder is not None:
                    touch(holder, e)
                diag["dreb_without_possession"] += 1
                if nxt is None or nxt[0] != team:
                    nxt = (team, "other", t, e["action_number"])
            return

        if kind == "tov":
            p = open_for(team, period, t)
            touch(p, e)
            p["tov"] += 1
            end = "steal" if e.get("steal") else "dead_tov"
            close(p, end, t, e["action_number"])
            nxt = (game.other(team), end, t, e["action_number"])
            trip.update(team=None, left=0, keep=False)

    game.walk(home, on_time, on_event, on_period_start, on_period_end)
    for p in out:
        p["empty"] = bool(p.pop("_empty", False)) or (p["fga"] == 0 and p["fta"] == 0 and p["tov"] == 0)
        p.pop("_second", None)
        p.pop("_last_miss", None)
    return out, diag        # already in game order: possessions are appended as they close
