"""
pbp_lineups.py
===============
The one place that turns ESPN play-by-play (pbp_events, source 'espn') into
who was on the floor when. Shared by build_player_game_lines.py (per
player-game lines) and build_lineup_stints.py (every five-on-five stint),
so there is exactly one lineup parser; anything that needs on-floor
tracking should build on `Game`, never re-parse substitutions itself.

How the play-by-play is read (ESPN text):
  - shots are events whose text says "makes"/"misses", or "X blocks Y's
    ..." (a blocked shot is a missed attempt for the shooter, a block for
    X). Assists and steals are the names in "(X assists)" / "(X steals)".
    Made shots are worth what the shooter's team score went up by; a miss
    is a three when the text says "three point", or, when it gives no
    shot type (common in 2020-21 and 2021-22), when the distance is 23
    feet or more;
  - turnovers are events typed "... Turnover" or "Traveling" (not "No
    Turnover"); rebounds and turnovers with no player are team ones: they
    count for possessions but aren't anybody's rebound chance;
  - who is on the floor: in each period, a player starts it if the first
    thing he does in it is anything other than checking in (being checked
    out counts). Technical fouls and ejections don't count, bench players
    get those. A player who played a whole period without appearing in the
    play-by-play is found from the previous period's closing lineup. Then
    every "A enters the game for B" swaps them.

Names in descriptions are matched to ids within the game first, then
against player_season_stats for that season and team, then against names
only one player has had since 2010.

Two or three on a miss: the text's call ("three point", or 23+ ft when it
gives no shot type) unless the caller passes `miss_threes`, the NBA shot
chart's call for the same shots (`miss_three_calls()`: shots matched by
`match_coordinates()`, order within game, shooter and period). The text
misses ~8,700 of the chart's missed threes over 2020-21 to 2025-26 (median
26 ft; ESPN often doesn't write "three point"), so the player lines and the
Play Finder pass it; makes always take the score step (the chart agrees on
99.97% of matched makes). Without it the output is what it always was.

The clock: by default every time is ESPN's `seconds_remaining`, which is
late by event type (made shots a median 14 s; pbp_possessions.py). A
caller can pass `clock` ({action_number: seconds into the period}:
`game_clock()` of `load_espn(conn, clock=True)`, i.e. the stored
pbp_event_clock, built by build_event_clock.py from
pbp_possessions.corrected_clock()); `walk()` and `secs()` then use
it for every event it covers. Without it the output is what it always was
(player_game_lines and lineup_stints don't pass it: minutes come from
substitutions, at dead balls, where ESPN is on time).

`Game.walk(home, on_time, on_event, ...)` replays a game once, calling
back for every stretch of clock with an unchanged lineup and for every
event with the lineup on the floor when it happened. `Game.run(home)`
(the player lines) and `Game.stints(home)` (five-man stints) are both
built on it, so the two can't drift apart.

Two ways of crediting points are carried through `stints()`, because
ESPN's score fields are stale in a few hundred games of 2020-21 to
2022-23 (the score drops, or jumps by two baskets at once): points from
the made shots and free throws themselves (`pts_shots`), and the running
maximum of the score fields (`pts_score`). build_lineup_stints.py picks
per game whichever adds up to the real final score. The player lines'
own on-court points (`tm_pts`/`op_pts`) still come from positive score
steps, as they always have (their output is checked not to change).
"""

import re
import unicodedata
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

ASSIST_RE = re.compile(r"\((.+?) assists\)")
STEAL_RE = re.compile(r"\((.+?) steals\)")
BLOCK_RE = re.compile(r"^(.+?) blocks ")
SUB_RE = re.compile(r"^(.*?) enters the game for (.*)$")
DIST_RE = re.compile(r"(\d+)-foot")
NOT_ON_FLOOR = {"Technical Foul", "Double Technical Foul", "Ejection", "Delay Technical",
                "Hanging Technical Foul", "Excess Timeout Technical", "Too Many Players Technical",
                "Defensive 3-Seconds Technical"}
THREE_FOOT_FLOOR = 23
# ESPN spelling -> NBA.com spelling, for players whose name differs.
ALIASES = {"kenyon martin": "kj martin", "enes kanter": "enes freedom", "carlton carrington": "bub carrington",
           "alexandre sarr": "alex sarr", "nicolas claxton": "nic claxton"}

REGULATION_SECONDS = 2880
PERIOD_SECONDS = 720
OT_SECONDS = 300
STINT_STATS = ["fga", "fgm", "fg3a", "fg3m", "fta", "ftm", "oreb", "dreb", "tov"]


def norm(name):
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[.'`]", "", s.lower())
    s = re.sub(r"\s+(jr|sr|ii|iii|iv)$", "", s.strip())
    s = re.sub(r"\s+", " ", s)
    return ALIASES.get(s, s)


def period_bounds(period):
    """(seconds_remaining at the period's start, period length)."""
    if period <= 4:
        return REGULATION_SECONDS - PERIOD_SECONDS * (period - 1), PERIOD_SECONDS
    return OT_SECONDS, OT_SECONDS


def elapsed(period, t):
    """Seconds since tip-off for `t` seconds into `period` (overtime continues past 2880)."""
    if period <= 4:
        return PERIOD_SECONDS * (period - 1) + t
    return REGULATION_SECONDS + OT_SECONDS * (period - 5) + t


def game_seconds(periods):
    """Length of a game with this many periods."""
    return REGULATION_SECONDS + OT_SECONDS * max(periods - 4, 0)


def is_turnover(action):
    return action != "No Turnover" and ("Turnover" in action or action == "Traveling")


def shot_value(desc, made, delta):
    if made and delta in (2, 3):
        return delta
    if "three point" in desc:
        return 3
    if "two point" in desc or "free throw" in desc:
        return 2
    m = DIST_RE.search(desc)
    return 3 if m and int(m.group(1)) >= THREE_FOOT_FLOOR else 2


class Game:
    def __init__(self, gid, season, game_date, events, season_names, all_names, miss_threes=None, clock=None):
        self.gid, self.season, self.game_date = gid, season, game_date
        # {action_number: True if the NBA shot chart calls this missed shot a three}; None = the text's call.
        self.miss_threes = miss_threes
        # {action_number: seconds into the period} replacing ESPN's times (game_clock()); None = ESPN's.
        self.clock = clock
        self.ev = events
        self.teams = [t for t in pd.unique(events["team_tricode"].dropna())]
        self.team_of = {}
        self.names = {}
        cnt = defaultdict(Counter)
        for pid, name, team in events[["person_id", "player_name", "team_tricode"]].dropna().itertuples(index=False):
            pid = int(pid)
            self.names[norm(name)] = pid
            cnt[pid][team] += 1
        for pid, c in cnt.items():
            self.team_of[pid] = c.most_common(1)[0][0]
        self.season_names = season_names
        self.all_names = all_names
        self.unmatched = Counter()
        self.lineup_fixes = 0

    def pid(self, name, team_hint=None):
        key = norm(name)
        if key in self.names:
            return self.names[key]
        cand = self.season_names.get(key)
        if cand:
            pid = cand.get(team_hint) if team_hint in cand else (next(iter(cand.values())) if len(cand) == 1 else None)
            if pid:
                self.names[key] = pid
                if team_hint:
                    self.team_of.setdefault(pid, team_hint)
                return pid
        pid = self.all_names.get(key)
        if pid:
            self.names[key] = pid
            if team_hint:
                self.team_of.setdefault(pid, team_hint)
            return pid
        self.unmatched[name] += 1
        return None

    def other(self, team):
        return next((t for t in self.teams if t != team), None)

    def secs(self, e):
        """seconds_remaining of a parsed event (pbp_events' convention): the clock's if one was given and covers
        it, else ESPN's."""
        if self.clock is not None:
            t = self.clock.get(e["action_number"])
            if t is not None:
                return period_bounds(e["period"])[0] - t
        return e["secs"]

    def event_t(self, e, start, length):
        """Seconds into the period, as walk() places the event: the clock's time if given, else ESPN's, kept
        inside the period."""
        if self.clock is not None:
            t = self.clock.get(e["action_number"])
            if t is not None:
                return min(max(t, 0.0), float(length))
        return min(max(start - e["secs"], 0.0), float(length))

    def parse(self):
        """Event list with actors resolved: (period, secs, kind, fields)."""
        out = []
        prev_home = prev_away = 0
        home = self.home
        for r in self.ev.itertuples(index=False):
            desc = r.description or ""
            action = r.action_type or ""
            team = r.team_tricode
            pid = int(r.person_id) if r.person_id == r.person_id and r.person_id is not None else None
            sh, sa = r.score_home, r.score_away
            known = sh == sh and sa == sa and sh is not None and sa is not None
            dh = (sh - prev_home) if known else 0
            da = (sa - prev_away) if known else 0
            if known:
                prev_home, prev_away = sh, sa
            if r.period != r.period or r.seconds_remaining != r.seconds_remaining:
                continue
            e = {"period": int(r.period), "secs": float(r.seconds_remaining), "team": team, "pid": pid,
                 "dh": dh, "da": da, "action": action, "actors": set(), "floor_ok": action not in NOT_ON_FLOOR,
                 "action_number": int(r.action_number)}
            if pid is None and isinstance(r.player_name, str) and r.player_name and action != "Substitution":
                pid = self.pid(r.player_name, team)
                e["pid"] = pid
            if pid:
                e["actors"].add(pid)
            if action == "Substitution":
                m = SUB_RE.match(desc)
                e["kind"] = "sub"
                e["in"] = pid if pid else (self.pid(m.group(1), team) if m and m.group(1) else None)
                e["out"] = self.pid(m.group(2), team) if m and m.group(2).strip() else None
                e["actors"] = set()
            elif action.startswith("Free Throw"):
                e["kind"] = "ft"
                e["made"] = " makes " in desc
            elif " blocks " in desc or re.search(r" (makes|misses) ", desc):
                e["kind"] = "fg"
                bm = BLOCK_RE.match(desc)
                e["made"] = " makes " in desc and not bm
                own_delta = dh if team == home else da
                e["val"] = shot_value(desc, e["made"], own_delta)
                if not e["made"] and self.miss_threes and e["action_number"] in self.miss_threes:
                    e["val"] = 3 if self.miss_threes[e["action_number"]] else 2
                e["blocker"] = self.pid(bm.group(1), self.other(team)) if bm else None
                am = ASSIST_RE.search(desc)
                e["assist"] = self.pid(am.group(1), team) if am else None
                for x in (e["blocker"], e["assist"]):
                    if x:
                        e["actors"].add(x)
            elif "Rebound" in action:
                e["kind"] = "oreb" if action.startswith("Offensive") else "dreb" if action.startswith("Defensive") else None
            elif is_turnover(action):
                e["kind"] = "tov"
                sm = STEAL_RE.search(desc)
                e["steal"] = self.pid(sm.group(1), self.other(team)) if sm else None
                if e["steal"]:
                    e["actors"].add(e["steal"])
            else:
                e["kind"] = None
            out.append(e)
        return out

    def starters(self, events, period, prev_end):
        first = {}
        for e in events:
            if e["kind"] == "sub":
                if e["in"] and e["in"] not in first:
                    first[e["in"]] = "in"
                if e["out"] and e["out"] not in first:
                    first[e["out"]] = "on"
            elif e["floor_ok"]:
                for a in e["actors"]:
                    first.setdefault(a, "on")
        lineups = {}
        for team in self.teams:
            on = [p for p, how in first.items() if how == "on" and self.team_of.get(p) == team]
            if len(on) > 5:
                on = on[:5]
                self.lineup_fixes += 1
            if len(on) < 5:
                self.lineup_fixes += 1
                for p in prev_end.get(team, []):
                    if len(on) >= 5:
                        break
                    if p not in on and p not in first:
                        on.append(p)
            lineups[team] = set(on)
        return lineups

    def walk(self, home, on_time, on_event, on_period_start=None, on_period_end=None):
        """Replay the game once. `on_time(period, t0, t1, lineups)` is called for
        each stretch of clock (seconds into the period) during which nobody
        changed; `on_event(e, lineups, period, t)` for every event with the
        lineups on the floor when it happened (it must apply substitutions to
        `lineups` itself, see `apply_sub`). The optional period callbacks get
        (period, lineups) at each period's start and end."""
        self.home = home
        events = self.parse()
        by_period = defaultdict(list)
        for e in events:
            by_period[e["period"]].append(e)
        prev_end = {}
        for period in sorted(by_period):
            evs = by_period[period]
            start, length = period_bounds(period)
            lineups = self.starters(evs, period, prev_end)
            if on_period_start:
                on_period_start(period, lineups)
            last = 0.0
            for e in evs:
                t = self.event_t(e, start, length)
                if t > last:
                    on_time(period, last, t, lineups)
                    last = t
                on_event(e, lineups, period, max(t, last))
            if length > last:
                on_time(period, last, length, lineups)
            if on_period_end:
                on_period_end(period, lineups)
            prev_end = {t: list(on) for t, on in lineups.items()}

    def run(self, home):
        """Per-player rows for build_player_game_lines.py: seconds on the floor,
        own counting stats and the team/opponent totals while he played."""
        rows = defaultdict(lambda: defaultdict(float))
        played = set()

        def on_time(period, t0, t1, lineups):
            for team, on in lineups.items():
                for p in on:
                    rows[p]["seconds"] += t1 - t0

        def on_event(e, lineups, period, t):
            self.apply(e, lineups, rows, played)

        self.walk(home, on_time, on_event)
        return rows, played

    def credit(self, lineups, team, rows, field_tm, field_op, amount=1.0):
        for t, on in lineups.items():
            f = field_tm if t == team else field_op
            for p in on:
                rows[p][f] += amount

    def apply_sub(self, e, lineups, played=None):
        team = e["team"]
        on = lineups.setdefault(team, set())
        if e["out"]:
            on.discard(e["out"])
        if e["in"]:
            on.add(e["in"])
            if played is not None:
                played.add(e["in"])
            self.team_of.setdefault(e["in"], team)

    def apply(self, e, lineups, rows, played):
        team, kind = e["team"], e["kind"]
        for a in e["actors"]:
            played.add(a)
        # Points while on the floor come from the score, whoever scored.
        home, away = self.home, self.other(self.home)
        if e["dh"] > 0:
            self.credit(lineups, home, rows, "tm_pts", "op_pts", e["dh"])
        if e["da"] > 0:
            self.credit(lineups, away, rows, "tm_pts", "op_pts", e["da"])
        if kind == "sub":
            self.apply_sub(e, lineups, played)
            return
        pid = e["pid"]
        if kind == "fg":
            self.credit(lineups, team, rows, "tm_fga", "op_fga")
            if pid:
                rows[pid]["fga"] += 1
                if e["val"] == 3:
                    rows[pid]["fg3a"] += 1
            if e["made"]:
                self.credit(lineups, team, rows, "tm_fgm", "op_fgm")
                if pid:
                    rows[pid]["fgm"] += 1
                    rows[pid]["pts"] += e["val"]
                    if e["val"] == 3:
                        rows[pid]["fg3m"] += 1
            if e.get("assist"):
                rows[e["assist"]]["ast"] += 1
            if e.get("blocker"):
                rows[e["blocker"]]["blk"] += 1
        elif kind == "ft":
            self.credit(lineups, team, rows, "tm_fta", "op_fta")
            if pid:
                rows[pid]["fta"] += 1
                if e["made"]:
                    rows[pid]["ftm"] += 1
                    rows[pid]["pts"] += 1
        elif kind in ("oreb", "dreb"):
            if pid:  # team rebounds aren't a chance for anyone
                self.credit(lineups, team, rows, f"tm_{kind}", f"op_{kind}")
                rows[pid][kind] += 1
        elif kind == "tov":
            self.credit(lineups, team, rows, "tm_tov", "op_tov")
            if pid:
                rows[pid]["tov"] += 1
            if e.get("steal"):
                rows[e["steal"]]["stl"] += 1

    def stints(self, home, split_at=None):
        """Every stretch of a game with the same ten players on the floor.

        Returns (stints, diag). Each stint is a dict: period, t0/t1 (seconds
        into the period), home/away (sorted player ids; fewer or more than
        five when the play-by-play left the lineup uncertain), action_from/
        action_to (inclusive `action_number` range of the events it holds,
        None if none), and per side (`h_`/`a_` prefix): pts_shots (made
        shots and free throws), pts_score (running maximum of the score
        fields) and the STINT_STATS counts with the same credit rules as the
        player lines (team rebounds and turnovers with no player: turnovers
        count, rebounds don't). Stints with no time and no counted event
        (two substitutions at the same clock) are dropped. `diag` counts
        events whose actor wasn't on his team's tracked floor and events
        with no team (skipped).

        `split_at` (optional, seconds since tip-off) also cuts a stint where
        the clock passes each of those moments, with the same ten players on
        both sides of the cut; events at exactly that clock stay before it.
        build_rotations.py cuts at 5:00 left in the fourth quarter to get
        the score there and the closing stretch exactly. Without it the
        output is unchanged (build_lineup_stints.py doesn't pass it)."""
        splits = defaultdict(list)
        for x in split_at or ():
            period = 1 + int(x // PERIOD_SECONDS) if x < REGULATION_SECONDS else 5 + int((x - REGULATION_SECONDS) // OT_SECONDS)
            t = x - elapsed(period, 0.0)
            if t > 0:
                splits[period].append(t)
        away = None
        out = []
        cur = None
        high = {"h": 0, "a": 0}      # running maximum of the score fields
        raw = {"h": 0, "a": 0}       # the score fields themselves, rebuilt from the steps
        diag = Counter()

        def side(team):
            return "h" if team == home else "a" if team == away else None

        def fresh(period, t, lineups):
            s = {"period": period, "t0": t, "t1": t,
                 "home": sorted(lineups.get(home, ())), "away": sorted(lineups.get(away, ())),
                 "action_from": None, "action_to": None}
            for p in ("h_", "a_"):
                s[p + "pts_shots"] = 0
                s[p + "pts_score"] = 0
                for k in STINT_STATS:
                    s[p + k] = 0
            return s

        def close():
            nonlocal cur
            if cur is None:
                return
            counted = any(cur[p + k] for p in ("h_", "a_") for k in ["pts_shots", "pts_score"] + STINT_STATS)
            if cur["t1"] > cur["t0"] or counted:
                out.append(cur)
            cur = None

        def key(lineups):
            return (frozenset(lineups.get(home, ())), frozenset(lineups.get(away, ())))

        def on_period_start(period, lineups):
            nonlocal away, cur
            if away is None:
                away = self.other(home)
            cur = fresh(period, 0.0, lineups)
            cur["_key"] = key(lineups)

        def on_period_end(period, lineups):
            close()

        def on_time(period, t0, t1, lineups):
            nonlocal cur
            for x in splits.get(period, ()):
                if cur["t0"] < x < t1:
                    cur["t1"] = x
                    k = cur["_key"]
                    close()
                    cur = fresh(period, x, lineups)
                    cur["_key"] = k
            cur["t1"] = t1

        def on_event(e, lineups, period, t):
            nonlocal cur
            # Score fields: running maximum, credited to the current stint.
            raw["h"] += e["dh"]
            raw["a"] += e["da"]
            for s in ("h", "a"):
                if raw[s] > high[s]:
                    cur[s + "_pts_score"] += raw[s] - high[s]
                    high[s] = raw[s]
            if e["kind"] == "sub":
                self.apply_sub(e, lineups)
                k = key(lineups)
                if k != cur["_key"]:
                    cur["t1"] = max(cur["t1"], t)
                    close()
                    cur = fresh(period, t, lineups)
                    cur["_key"] = k
                return
            s = side(e["team"])
            if e["kind"] in ("fg", "ft", "oreb", "dreb", "tov"):
                if s is None:
                    diag["no_team"] += 1
                    return
                cur["action_from"] = e["action_number"] if cur["action_from"] is None else cur["action_from"]
                cur["action_to"] = e["action_number"]
            if e["floor_ok"]:
                for a in e["actors"]:
                    team = self.team_of.get(a)
                    if team in lineups and a not in lineups[team]:
                        diag["actor_off_floor"] += 1
            p = s + "_" if s else None
            if e["kind"] == "fg":
                cur[p + "fga"] += 1
                if e["val"] == 3:
                    cur[p + "fg3a"] += 1
                if e["made"]:
                    cur[p + "fgm"] += 1
                    cur[p + "pts_shots"] += e["val"]
                    if e["val"] == 3:
                        cur[p + "fg3m"] += 1
            elif e["kind"] == "ft":
                cur[p + "fta"] += 1
                if e["made"]:
                    cur[p + "ftm"] += 1
                    cur[p + "pts_shots"] += 1
            elif e["kind"] in ("oreb", "dreb"):
                if e["pid"]:  # team rebounds aren't a chance for anyone
                    cur[p + e["kind"]] += 1
            elif e["kind"] == "tov":
                cur[p + "tov"] += 1

        self.walk(home, on_time, on_event, on_period_start, on_period_end)
        for s in out:
            s.pop("_key", None)
        return out, diag


def load_season_names(cur):
    """(season -> name -> team -> id, name -> id for names only one player had since 2010)."""
    cur.execute("SELECT season, player_id, player_name, team_abbreviation FROM player_season_stats WHERE season >= 2010;")
    names = defaultdict(lambda: defaultdict(dict))
    ids = defaultdict(set)
    for season, pid, name, team in cur.fetchall():
        names[season][norm(name)][team] = int(pid)
        ids[norm(name)].add(int(pid))
    return names, {n: next(iter(p)) for n, p in ids.items() if len(p) == 1}


def load_espn(conn, clock=False, game_ids=None):
    """Every ESPN game (regular season 2020-21 on) and its events, grouped by
    game in the order the parser expects (action_number, then id). With
    `clock`, each event also carries pbp_event_clock's corrected time
    (`period_t`, seconds into the period), `clock_anchored` and
    `clock_source`; game_clock()
    turns one game's into the `Game(..., clock=)` argument. `game_ids`
    (optional list) loads only those games (build_coaching_decisions.py
    parses a few hundred); omitted = every game, as before."""
    only, params = "", None
    if game_ids is not None:
        only, params = " AND g.game_id = ANY(%(ids)s)", {"ids": list(game_ids)}
    games = pd.read_sql_query(
        "SELECT game_id, season, game_date, home_team, away_team FROM pbp_games g WHERE source = 'espn'" + only +
        " ORDER BY game_date, game_id;", conn, params=params)
    extra, join = "", ""
    if clock:
        extra = ", c.period_t, c.anchored AS clock_anchored, c.source AS clock_source"
        join = " LEFT JOIN pbp_event_clock c ON c.event_id = e.id"
    events = pd.read_sql_query(
        f"""SELECT e.game_id, e.action_number, e.id, e.period, e.seconds_remaining, e.score_home, e.score_away,
                  e.team_tricode, e.person_id, e.player_name, e.action_type, e.description{extra}
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id{join}
           WHERE g.source = 'espn'{only} ORDER BY e.game_id, e.action_number, e.id;""", conn, params=params)
    events[["description", "action_type"]] = events[["description", "action_type"]].fillna("")
    events["person_id"] = events["person_id"].astype("Int64").astype(object).where(events["person_id"].notna(), None)
    grouped = dict(tuple(events.groupby("game_id", sort=False)))
    return games, grouped


def match_coordinates(conn, shots):
    """Attach the NBA shot chart's coordinates (coord_ft, shot_type) and row id
    (nba_shot_id) to each ESPN attempt matched by order within (game, shooter,
    period) with identical make/miss sequences; NaN where unmatched."""
    link = pd.read_sql_query(
        "SELECT DISTINCT 'espn_' || espn_id AS game_id, game_id AS nba_id FROM game_scores WHERE espn_id IS NOT NULL", conn)
    shots = shots.merge(link, on="game_id", how="left")
    nba = pd.read_sql_query(
        """SELECT id, game_id AS nba_id, player_id AS pid, period, minutes_remaining * 60 + seconds_remaining AS clock,
                  shot_made_flag = 1 AS made, loc_x, loc_y, shot_type
           FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21'""", conn)
    keys = ["nba_id", "pid", "period"]
    s = shots[shots.nba_id.notna() & shots.pid.notna()].copy()
    s["pid"] = s["pid"].astype(int)
    s = s.sort_values(keys + ["action_number"])
    nba["pid"] = nba["pid"].astype("int64")
    nba = nba.sort_values(keys + ["clock", "id"], ascending=[True, True, True, False, True])
    s["k"] = s.groupby(keys).cumcount()
    nba["k"] = nba.groupby(keys).cumcount()

    def seq(df):
        return df.groupby(keys)["made"].agg(lambda x: "".join("1" if v else "0" for v in x))

    same = pd.concat([seq(s).rename("a"), seq(nba).rename("b")], axis=1, join="inner")
    same = same[same.a == same.b].index
    ok = pd.MultiIndex.from_frame(s[keys]).isin(same)
    m = s[ok].merge(nba[keys + ["k", "id", "loc_x", "loc_y", "shot_type"]], on=keys + ["k"], how="inner")
    m["coord_ft"] = np.hypot(m.loc_x, m.loc_y) / 10.0
    # nba_shot_id (player_shots.id of the matched shot; added 2026-09-29 for paper_xrapm.py) rides along as an
    # extra column: every caller names the columns it reads.
    shots = shots.merge(m[["game_id", "action_number", "coord_ft", "shot_type", "id"]].rename(columns={"id": "nba_shot_id"}),
                        on=["game_id", "action_number"], how="left")
    return shots


def chart_matches(conn, games, grouped, season_names, all_names):
    """Every ESPN field-goal attempt (game_id, season, action_number, pid, period, made, text_three) with the
    NBA shot chart row it matches (match_coordinates(): coord_ft, shot_type, nba_shot_id; NaN where
    unmatched). One parse of every game (the shooter ids the lines use)."""
    rows = []
    for g in games.itertuples(index=False):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        game.home = g.home_team  # parse() needs the home side for score steps (walk() sets it the same way)
        for e in game.parse():
            if e["kind"] == "fg":
                rows.append((g.game_id, int(g.season), e["action_number"], e["pid"], e["period"], bool(e["made"]),
                             e["val"] == 3))
    shots = pd.DataFrame(rows, columns=["game_id", "season", "action_number", "pid", "period", "made", "text_three"])
    return match_coordinates(conn, shots)


def miss_three_calls(conn, games, grouped, season_names, all_names, matched=None):
    """The NBA shot chart's two-or-three call for every missed field goal it
    can be matched to: ({game_id: {action_number: is_three}}, a DataFrame of
    those misses with the text's call `text_three` and the chart's `nba_three`).
    `matched` is chart_matches()'s output when the caller already has it
    (build_possessions.py also reads the chart's clock from it)."""
    m = chart_matches(conn, games, grouped, season_names, all_names) if matched is None else matched
    misses = m[~m.made & m.shot_type.notna()].copy()
    misses["nba_three"] = misses.shot_type.str.startswith("3")
    calls = {}
    for gid, n, three in misses[["game_id", "action_number", "nba_three"]].itertuples(index=False):
        calls.setdefault(gid, {})[int(n)] = bool(three)
    return calls, misses[["game_id", "season", "action_number", "text_three", "nba_three"]]


def game_clock(ev):
    """({action_number: seconds into the period}, {action_numbers whose time is anchored}) for one game's events
    as load_espn(conn, clock=True) returns them; ({}, set()) where pbp_event_clock has no rows."""
    if "period_t" not in ev.columns:
        return {}, set()
    has = ev[ev.period_t.notna()]
    clock = dict(zip(has.action_number.astype(int).tolist(), has.period_t.astype(float).tolist()))
    anchored = set(has.action_number[has.clock_anchored.astype(bool)].astype(int).tolist())
    return clock, anchored
