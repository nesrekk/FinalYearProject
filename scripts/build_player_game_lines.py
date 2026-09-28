"""
build_player_game_lines.py
===========================
One row per player per game, rebuilt from ESPN play-by-play (pbp_events,
source = 'espn': every regular-season game 2020-21 to 2025-26). The project
has no box-score game logs, so this is the only per-game player data it has
besides shot charts. Used by build_stat_stability.py (split-half
reliability); written as its own table so later features can reuse it.

What each row holds:
  - the player's own counting stats: points, FGM/FGA, 3PM/3PA, FTM/FTA,
    offensive/defensive rebounds, assists, steals, blocks, turnovers;
  - seconds played, reconstructed from substitutions;
  - what happened while he was on the floor, for rate stats: his team's and
    the opponent's FGM/FGA/FTA/offensive and defensive rebounds/turnovers
    and points.

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

Checked against player_season_stats (NBA.com season totals) after the
build; the script prints the comparison. Names in descriptions are
matched to ids within the game first, then against player_season_stats
for that season and team.

Usage:
    cd scripts && python3 build_player_game_lines.py
"""

import re
import unicodedata
from collections import Counter, defaultdict

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

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

OWN = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov"]
ON = ["tm_fgm", "tm_fga", "tm_fta", "tm_oreb", "tm_dreb", "tm_tov", "tm_pts",
      "op_fgm", "op_fga", "op_fta", "op_oreb", "op_dreb", "op_tov", "op_pts"]


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
        return 2880 - 720 * (period - 1), 720
    return 300, 300


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
    def __init__(self, gid, season, game_date, events, season_names, all_names):
        self.gid, self.season, self.game_date = gid, season, game_date
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
                 "dh": dh, "da": da, "action": action, "actors": set(), "floor_ok": action not in NOT_ON_FLOOR}
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

    def run(self, home):
        self.home = home
        events = self.parse()
        rows = defaultdict(lambda: defaultdict(float))
        played = set()
        by_period = defaultdict(list)
        for e in events:
            by_period[e["period"]].append(e)
        prev_end = {}
        for period in sorted(by_period):
            evs = by_period[period]
            start, length = period_bounds(period)
            lineups = self.starters(evs, period, prev_end)
            last = 0.0
            for e in evs:
                t = min(max(start - e["secs"], 0.0), float(length))
                if t > last:
                    for team, on in lineups.items():
                        for p in on:
                            rows[p]["seconds"] += t - last
                    last = t
                self.apply(e, lineups, rows, played)
            if length > last:
                for team, on in lineups.items():
                    for p in on:
                        rows[p]["seconds"] += length - last
            prev_end = {t: list(on) for t, on in lineups.items()}
        return rows, played

    def credit(self, lineups, team, rows, field_tm, field_op, amount=1.0):
        for t, on in lineups.items():
            f = field_tm if t == team else field_op
            for p in on:
                rows[p][f] += amount

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
            on = lineups.setdefault(team, set())
            if e["out"]:
                on.discard(e["out"])
            if e["in"]:
                on.add(e["in"])
                played.add(e["in"])
                self.team_of.setdefault(e["in"], team)
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


def load_season_names(cur):
    """(season -> name -> team -> id, name -> id for names only one player had since 2010)."""
    cur.execute("SELECT season, player_id, player_name, team_abbreviation FROM player_season_stats WHERE season >= 2010;")
    names = defaultdict(lambda: defaultdict(dict))
    ids = defaultdict(set)
    for season, pid, name, team in cur.fetchall():
        names[season][norm(name)][team] = int(pid)
        ids[norm(name)].add(int(pid))
    return names, {n: next(iter(p)) for n, p in ids.items() if len(p) == 1}


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    games = pd.read_sql_query(
        "SELECT game_id, season, game_date, home_team FROM pbp_games WHERE source = 'espn' ORDER BY game_date, game_id;", conn)
    events = pd.read_sql_query(
        """SELECT e.game_id, e.action_number, e.id, e.period, e.seconds_remaining, e.score_home, e.score_away,
                  e.team_tricode, e.person_id, e.player_name, e.action_type, e.description
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' ORDER BY e.game_id, e.action_number, e.id;""", conn)
    events[["description", "action_type"]] = events[["description", "action_type"]].fillna("")
    events["person_id"] = events["person_id"].astype("Int64").astype(object).where(events["person_id"].notna(), None)
    grouped = dict(tuple(events.groupby("game_id", sort=False)))
    print(f"{len(games)} games, {len(events)} events")

    out, unmatched, fixes, periods = [], Counter(), 0, 0
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        rows, played = game.run(g.home_team)
        unmatched.update(game.unmatched)
        fixes += game.lineup_fixes
        periods += ev["period"].nunique()
        for pid, r in rows.items():
            if pid not in played and r.get("seconds", 0) < 1:
                continue
            team = game.team_of.get(pid)
            out.append([pid, g.game_id, int(g.season), g.game_date, team,
                        round(r.get("seconds", 0.0), 1)] + [int(r.get(k, 0)) for k in OWN] +
                       [int(r.get(k, 0)) for k in ON])
        if i % 1000 == 0:
            print(f"  {i} games")

    cols = ["player_id", "game_id", "season", "game_date", "team_abbreviation", "seconds"] + OWN + ON
    cur.execute("DROP TABLE IF EXISTS player_game_lines;")
    cur.execute(f"""CREATE TABLE player_game_lines (
        player_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL, game_date DATE,
        team_abbreviation TEXT, seconds DOUBLE PRECISION,
        {', '.join(f'{c} INTEGER' for c in OWN + ON)},
        PRIMARY KEY (player_id, game_id));""")
    psycopg2.extras.execute_values(cur, f"INSERT INTO player_game_lines ({', '.join(cols)}) VALUES %s", out, page_size=5000)
    cur.execute("CREATE INDEX ON player_game_lines (season, player_id);")
    conn.commit()
    print(f"{len(out)} player-game rows; lineup periods needing a fill/trim: {fixes} of ~{periods * 2} team-periods")
    print("most common unmatched names:", unmatched.most_common(10))

    # Check against NBA.com season totals (players with 20+ games).
    cur.execute("""
        WITH l AS (SELECT player_id, season, COUNT(*) FILTER (WHERE seconds > 0) gp, SUM(seconds)/60 mins,
                          SUM(pts) pts, SUM(fga) fga, SUM(fg3a) fg3a, SUM(fta) fta, SUM(oreb) oreb, SUM(dreb) dreb,
                          SUM(ast) ast, SUM(stl) stl, SUM(blk) blk, SUM(tov) tov
                   FROM player_game_lines GROUP BY 1, 2)
        SELECT COUNT(*),
               SUM(l.gp)/SUM(s.gp), SUM(l.mins)/SUM(s.min*s.gp), SUM(l.pts)/SUM(s.pts*s.gp),
               SUM(l.fga)/SUM(s.fga*s.gp), SUM(l.fg3a)/SUM(s.fg3a*s.gp), SUM(l.fta)/SUM(s.fta*s.gp),
               SUM(l.oreb)/SUM(s.oreb*s.gp), SUM(l.dreb)/SUM(s.dreb*s.gp), SUM(l.ast)/SUM(s.ast*s.gp),
               SUM(l.stl)/SUM(s.stl*s.gp), SUM(l.blk)/SUM(s.blk*s.gp), SUM(l.tov)/SUM(s.tov*s.gp),
               AVG(ABS(l.mins - s.min*s.gp) / NULLIF(s.min*s.gp, 0))
        FROM l JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= 20;""")
    labels = ["player-seasons", "gp", "min", "pts", "fga", "fg3a", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov",
              "mean |min error|"]
    for k, v in zip(labels, cur.fetchone()):
        print(f"  {k:>16}: {float(v):.4f}" if k != "player-seasons" else f"  {k:>16}: {v}")
    conn.close()


if __name__ == "__main__":
    main()
