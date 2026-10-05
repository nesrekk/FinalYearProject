"""
build_player_game_onfloor.py
============================
Per player-game on-court plus-minus (and the points for and against behind
it), regular season 2020-21 to 2025-26, from the same lineups as
`lineup_stints` (round 7 step 2: the Workbench offers "any stat", so the
per-game +/- that `player_game_lines` gets wrong had to be rebuilt first).

Why not `player_game_lines.tm_pts - op_pts`: those credit positive changes
of ESPN's score fields, which are stale in hundreds of games, so a team's
summed on-court margin is 5x the final margin in only ~75% of team-games
(README Known real gaps). Here points come from the made shots and free
throws, exactly as `lineup_stints` credits them (or, in the ~2% of games
where those don't add up to the final score, the running maximum of the
score fields, the game's `lineup_stint_games.points_method`).

**A free throw is credited to the players on the floor at the foul, not
at the shot** (since round 8 step 6a `lineup_stints` does the same; until
then this was the one rule where the two differed).
That is the official box score's convention: substitutions are made
between free throws (after the first of two, or after a foul and before
the trip), and the stints, which follow the log, give those points to the
incoming players. The "lineups at the foul" are the lineups at the last
earlier event that is a foul or names a player, other than a substitution
or another free throw (the foul itself, or the made shot of an and-one;
team rebounds and timeouts logged inside a trip are skipped, because ESPN
often logs them after the substitutions). Measured against ESPN's box
scores (their +/- column) on 300 random reconciled games, 6,390 player-games
matched by name, read 2026-10-03 (`--espn-check 300`):

    this table (free throws at the foul)       98.2% exact, 99.7% within 2
    stints' credit (free throws at the shot)   42.5% exact, 95.1% within 2
    player_game_lines' tm_pts - op_pts         36.6% exact

Read again 2026-10-05 after round 8 step 6a (same 300 games and seed; 6,453
player-games matched now that ESPN's no-id players have ids): the stints'
sums and the lines' tm_pts - op_pts now use this rule, and all three are
98.2% exact, 99.7% within 2.

(Placing made shots by the corrected clock, pbp_event_clock, instead of the
log order moved 76 of 35,487 scoring events and matched slightly worse, so
ESPN's late-logged made shots are not the cause; the anchor rule without
skipping team rebounds and timeouts was 94.9% exact.)

This table's replay is its own (only the anchor rule, `is_foul_anchor()`,
comes from pbp_lineups), so it is an independent check of the stints'
crediting: the build stops unless the two agree (below). The old
at-the-shot numbers are still computed, for `player_games_moved_by_ft_rule`.

Columns (one row per player-game he was on the floor in, or credited with a
free throw while on the floor for a foul only, i.e. in and out at the same
clock):
  seconds          his seconds on the floor (the stints' clock);
  tracked_seconds  of those, seconds in stints with five identified players
                   a side in a game_ok game (`lineup_stints.tracked_ok`);
  pts_for / pts_against / plus_minus
                   team and opponent points while he was on the floor, and
                   their difference;
  game_ok          the game reconciles (`lineup_stint_games.game_ok`: points
                   to the real final score, seconds to the game length,
                   possession components to team_game_totals). The
                   Workbench shows the three numbers only where it's true
                   (7,220 of 7,232 games).

Checks (the build stops on the first two; all are stored in
`player_game_onfloor_meta` and printed):
  * every player-game's points for and against equal the sums over his
    `lineup_stints` rows, and his seconds equal theirs (to their 0.1 s
    rounding): the same lineups, the same points (until round 8 step 6a
    this compared the at-the-shot numbers, the stints' rule then);
  * every team-game of a game_ok game whose side had exactly five players
    on the floor all game: its players' plus-minus adds up to 5 x the real
    final margin;
  * the row set against `player_game_lines`' (who played);
  * the other team-games (a stretch with four or six listed): how many still
    add up.

Usage:
    cd scripts && python3 build_player_game_onfloor.py        # ~35 s
    cd scripts && python3 build_player_game_onfloor.py --dry-run   # build and check, write nothing
    cd scripts && python3 build_player_game_onfloor.py --espn-check 300 [--cache DIR]
        # after the build: compares N random games (fixed seed) with ESPN's
        # box score +/- (site.api.espn.com summary; network); prints only,
        # writes nothing. Box scores are cached in DIR ($TMPDIR by default).
Rerun after build_lineup_stints.py.
"""

import json
import os
import random
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, is_foul_anchor, load_espn, load_season_names, norm

ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"
ESPN_SEED = 7

DDL = """
CREATE TABLE player_game_onfloor (
    player_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL, team TEXT NOT NULL,
    seconds REAL NOT NULL, tracked_seconds REAL NOT NULL,
    pts_for SMALLINT NOT NULL, pts_against SMALLINT NOT NULL, plus_minus SMALLINT NOT NULL,
    game_ok BOOLEAN NOT NULL,
    PRIMARY KEY (player_id, game_id));
CREATE INDEX ON player_game_onfloor (season, player_id);
CREATE TABLE player_game_onfloor_meta (key TEXT PRIMARY KEY, value JSONB NOT NULL);
"""


def snapshot(lineups):
    return {t: frozenset(on) for t, on in lineups.items()}


def replay(g, ev, season_names, all_names, method, game_ok):
    """Per player: [team, seconds, tracked seconds, pf, pa, pf at the shot, pa at the shot]; and per team
    whether that side had exactly five on the floor all game."""
    game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
    home = g.home_team
    rows = {}
    five = {}
    high = {"h": 0, "a": 0}
    raw = {"h": 0, "a": 0}
    anchor = [None]
    away = [None]

    def row(pid, team):
        r = rows.get(pid)
        if r is None:
            r = rows[pid] = [team, 0.0, 0.0, 0, 0, 0, 0]
        return r

    def credit(lineups, scoring_team, pts, idx):
        for team in (home, away[0]):
            for pid in lineups.get(team, ()):
                r = row(pid, team)
                r[idx + (0 if team == scoring_team else 1)] += pts

    def check_five(lineups):
        for team in (home, away[0]):
            if len(lineups.get(team, ())) != 5:
                five[team] = False

    def on_period_start(period, lineups):
        if away[0] is None:
            away[0] = game.other(home)
            five.setdefault(home, True)
            five.setdefault(away[0], True)
        anchor[0] = snapshot(lineups)

    def on_time(period, t0, t1, lineups):
        both = all(len(lineups.get(t, ())) == 5 for t in (home, away[0]))
        # Only the two teams: a substitution ESPN tagged to no team opens a phantom lineup under None
        # (that's how player_game_lines counts 9 player-games' seconds twice); the stints ignore it too.
        for team in (home, away[0]):
            for pid in lineups.get(team, ()):
                r = row(pid, team)
                r[1] += t1 - t0
                if game_ok and both:
                    r[2] += t1 - t0
        if t1 > t0:
            check_five(lineups)

    def on_event(e, lineups, period, t):
        at_foul = anchor[0] if e["kind"] == "ft" else None
        # Score fields (running maximum), credited like lineup_stints: before a substitution is applied.
        raw["h"] += e["dh"]
        raw["a"] += e["da"]
        for s, team in (("h", home), ("a", away[0])):
            if raw[s] > high[s]:
                step = raw[s] - high[s]
                high[s] = raw[s]
                if method == "score":
                    credit(at_foul or lineups, team, step, 3)
                    credit(lineups, team, step, 5)
                    check_five(at_foul or lineups)
        if e["kind"] == "sub":
            game.apply_sub(e, lineups)
            return
        if is_foul_anchor(e):
            anchor[0] = snapshot(lineups)
        team = e["team"]
        if team not in (home, away[0]) or method != "shots":
            return
        pts = e["val"] if e["kind"] == "fg" and e["made"] else 1 if e["kind"] == "ft" and e["made"] else 0
        if pts:
            credit(at_foul or lineups, team, pts, 3)
            credit(lineups, team, pts, 5)
            check_five(at_foul or lineups)

    game.walk(home, on_time, on_event, on_period_start)
    return rows, five


def load_games(cur):
    cur.execute("""SELECT game_id, points_method, game_ok, home_team, away_team, final_home, final_away
                   FROM lineup_stint_games""")
    return {r[0]: r[1:] for r in cur.fetchall()}


def stint_sums(cur):
    """Per (game, player): seconds, stints, points for and against from lineup_stints."""
    cur.execute("""
        WITH sides AS (
            SELECT s.game_id, u.pid, s.seconds, s.home_pts AS pf, s.away_pts AS pa
            FROM lineup_stints s, unnest(s.home_ids) u(pid)
            UNION ALL
            SELECT s.game_id, u.pid, s.seconds, s.away_pts, s.home_pts
            FROM lineup_stints s, unnest(s.away_ids) u(pid))
        SELECT game_id, pid, sum(seconds::numeric)::float8, count(*), sum(pf), sum(pa) FROM sides GROUP BY 1, 2""")
    return {(r[0], int(r[1])): r[2:] for r in cur.fetchall()}


def build(conn):
    t0 = time.time()
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    meta_games = load_games(cur)
    games, grouped = load_espn(conn)
    print(f"{len(games)} games loaded ({time.time() - t0:.0f}s)")

    out, five_by_game = [], {}
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None or g.game_id not in meta_games:
            continue
        method, game_ok = meta_games[g.game_id][0], bool(meta_games[g.game_id][1])
        rows, five = replay(g, ev, season_names, all_names, method, game_ok)
        five_by_game[g.game_id] = five
        for pid, (team, sec, tsec, pf, pa, pf_shot, pa_shot) in rows.items():
            if sec <= 0 and not (pf or pa):   # no seconds and nothing credited under the foul rule: not on the floor
                continue
            out.append((int(pid), g.game_id, int(g.season), team, sec, tsec, pf, pa, pf - pa, game_ok, pf_shot, pa_shot))
        if i % 1000 == 0:
            print(f"  {i} games, {len(out)} player-games ({time.time() - t0:.0f}s)")
    print(f"{len(out)} player-games ({time.time() - t0:.0f}s)")
    return out, five_by_game, meta_games


def checks(cur, out, five_by_game, meta_games):
    res = {}
    # 1. Same lineups and points as lineup_stints (both credit free throws at the foul since round 8 step 6a).
    stints = stint_sums(cur)
    ours = {(r[1], r[0]): r for r in out}
    missing = {k for k in set(stints) - set(ours) if stints[k][0] > 0 or stints[k][2] or stints[k][3]}
    # A player on the floor only for a foul (in and out at the same clock, e.g. a take foul) is credited the free
    # throws; the stints keep that zero-second stint, so he is in both.
    only_at_foul = {k for k in ours if ours[k][4] == 0}
    extra = set(ours) - set(stints)
    bad_pts = [k for k in set(ours) & set(stints) if (ours[k][6], ours[k][7]) != (stints[k][2], stints[k][3])]
    bad_sec = [k for k in set(ours) & set(stints) if abs(ours[k][4] - stints[k][0]) > 0.05 * stints[k][1] + 0.01]
    res["stints_reproduced"] = {"player_games": len(ours), "missing_from_ours": len(missing), "extra_in_ours": len(extra),
                                "points_differ": len(bad_pts), "seconds_differ": len(bad_sec),
                                "on_the_floor_only_for_a_foul": len(only_at_foul)}
    if missing or extra or bad_pts or bad_sec:
        raise SystemExit(f"stints not reproduced: {res['stints_reproduced']}; e.g. {(list(missing) + list(extra) + bad_pts + bad_sec)[:5]}")

    # 2. A side with five on the floor all game adds up to 5 x the real final margin.
    team_pm = defaultdict(int)
    team_sec = defaultdict(float)
    for r in out:
        team_pm[(r[1], r[3])] += r[8]
        team_sec[(r[1], r[3])] += r[4]
    full = Counter()
    other = Counter()
    for gid, (method, game_ok, home, away, fh, fa) in meta_games.items():
        if gid not in five_by_game:
            continue
        for team, margin in ((home, (fh or 0) - (fa or 0)), (away, (fa or 0) - (fh or 0))):
            ok = team_pm[(gid, team)] == 5 * margin
            if game_ok and five_by_game[gid].get(team, False):
                full["team_games"] += 1
                full["add_up"] += ok
            elif game_ok:
                other["team_games"] += 1
                other["add_up"] += ok
            else:
                other["not_game_ok"] += 1
    res["five_all_game"] = dict(full)
    res["other_team_games"] = dict(other)
    if full["add_up"] != full["team_games"]:
        raise SystemExit(f"a side with five all game doesn't add up to 5 x the margin: {dict(full)}")

    # 3. Same rows as player_game_lines.
    cur.execute("SELECT player_id, game_id FROM player_game_lines")
    lines = {(int(p), g) for p, g in cur.fetchall()}
    mine = {(r[0], r[1]) for r in out}
    res["rows_vs_player_game_lines"] = {"lines": len(lines), "ours": len(mine), "only_in_lines": len(lines - mine),
                                        "only_in_ours": len(mine - lines)}

    # Coverage.
    res["rows"] = len(out)
    res["game_ok_rows"] = sum(r[9] for r in out)
    res["games"] = len({r[1] for r in out})
    res["tracked_share_of_seconds"] = round(sum(r[5] for r in out) / sum(r[4] for r in out), 4)
    moved = sum(1 for r in out if (r[6], r[7]) != (r[10], r[11]))
    res["player_games_moved_by_ft_rule"] = moved
    return res


def write(cur, out, res):
    cur.execute("DROP TABLE IF EXISTS player_game_onfloor; DROP TABLE IF EXISTS player_game_onfloor_meta;")
    cur.execute(DDL)
    psycopg2.extras.execute_values(
        cur, """INSERT INTO player_game_onfloor (player_id, game_id, season, team, seconds, tracked_seconds,
                pts_for, pts_against, plus_minus, game_ok) VALUES %s""",
        [(r[0], r[1], r[2], r[3], round(r[4], 1), round(r[5], 1), r[6], r[7], r[8], r[9]) for r in out],
        page_size=5000)
    rules = {
        "free_throws": ("credited to the lineups at the last earlier event that is a foul or names a player, "
                        "other than a substitution or another free throw (the official box score's convention)"),
        "points": "made shots and free throws, or the running maximum of the score fields where the game's "
                  "lineup_stint_games.points_method is 'score'",
        "espn_check_2026_10_03": {"games": 300, "player_games": 6390, "seed": ESPN_SEED,
                                  "exact": 0.982, "within_2": 0.997,
                                  "stints_ft_at_shot_exact": 0.425, "stints_ft_at_shot_within_2": 0.951,
                                  "lines_exact": 0.366},
        "espn_check_2026_10_05": {"games": 300, "player_games": 6453, "seed": ESPN_SEED,
                                  "exact": 0.982, "within_2": 0.997, "stints_exact": 0.982, "stints_within_2": 0.997,
                                  "lines_exact": 0.982,
                                  "note": "after round 8 step 6a: the stints and the lines credit free throws at the foul"},
    }
    for k, v in list(res.items()) + [("rules", rules)]:
        cur.execute("INSERT INTO player_game_onfloor_meta (key, value) VALUES (%s, %s)", (k, json.dumps(v)))


def espn_check(conn, n, cache):
    """Our plus-minus vs ESPN's box score on n random games (fixed seed). Network; prints only."""
    os.makedirs(cache, exist_ok=True)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT game_id FROM player_game_onfloor WHERE game_ok ORDER BY game_id")
    ids = [r[0] for r in cur.fetchall()]
    random.Random(ESPN_SEED).shuffle(ids)
    ids = ids[:n]
    cur.execute("""WITH sides AS (
                       SELECT s.game_id, u.pid, s.home_pts - s.away_pts AS pm FROM lineup_stints s, unnest(s.home_ids) u(pid)
                       WHERE s.game_id = ANY(%(ids)s)
                       UNION ALL
                       SELECT s.game_id, u.pid, s.away_pts - s.home_pts FROM lineup_stints s, unnest(s.away_ids) u(pid)
                       WHERE s.game_id = ANY(%(ids)s)),
                   st AS (SELECT game_id, pid AS player_id, sum(pm) AS pm FROM sides GROUP BY 1, 2)
                   SELECT o.game_id, o.player_id, o.plus_minus, l.tm_pts - l.op_pts,
                          coalesce(ps.player_name, b.player_name), coalesce(st.pm, 0)
                   FROM player_game_onfloor o
                   JOIN player_game_lines l USING (player_id, game_id)
                   LEFT JOIN st USING (game_id, player_id)
                   LEFT JOIN player_season_stats ps ON ps.player_id = o.player_id AND ps.season = o.season
                   LEFT JOIN player_bio b ON b.player_id = o.player_id
                   WHERE o.game_id = ANY(%(ids)s)""", {"ids": ids})
    ours = defaultdict(dict)
    for gid, pid, pm, lines_pm, name, stint_pm in cur.fetchall():
        ours[gid][pid] = (pm, lines_pm, norm(name or ""), stint_pm)
    tally = Counter()
    for k, gid in enumerate(ids):
        path = os.path.join(cache, f"{gid}.json")
        if not os.path.exists(path):
            req = urllib.request.Request(ESPN_SUMMARY.format(gid.replace("espn_", "")), headers={"User-Agent": "Mozilla/5.0"})
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        data = r.read()
                    json.loads(data)
                    break
                except Exception as ex:  # flaky chunked reads: retry
                    print(f"  retry {gid}: {ex}")
                    time.sleep(2 + 3 * attempt)
            else:
                raise SystemExit(f"could not fetch {gid}")
            with open(path, "wb") as f:
                f.write(data)
            time.sleep(0.3)
        with open(path) as f:
            box = json.load(f)
        espn = {}
        for t in box["boxscore"]["players"]:
            st = t["statistics"][0]
            i = st["labels"].index("+/-")
            for a in st["athletes"]:
                name = a.get("athlete", {}).get("displayName")
                if name and a.get("stats") and a["stats"][i] not in ("", "--"):
                    espn[norm(name)] = int(a["stats"][i].replace("+", ""))
        for pid, (pm, lines_pm, name, stint_pm) in ours[gid].items():
            e = espn.get(name)
            if e is None:
                tally["unmatched"] += 1
                continue
            tally["n"] += 1
            tally["exact"] += pm == e
            tally["within2"] += abs(pm - e) <= 2
            tally["lines_exact"] += lines_pm == e
            tally["stints_exact"] += stint_pm == e
            tally["stints_within2"] += abs(stint_pm - e) <= 2
        if k % 50 == 0:
            print(f"  {k} games")
    n_ = tally["n"]
    print(f"ESPN box-score check, {len(ids)} games, {n_} player-games matched by name ({tally['unmatched']} not):")
    print(f"  this table exact {tally['exact'] / n_:.3f}, within 2 {tally['within2'] / n_:.3f}; "
          f"lineup_stints' sums exact {tally['stints_exact'] / n_:.3f}, within 2 "
          f"{tally['stints_within2'] / n_:.3f}; player_game_lines' tm_pts - op_pts exact {tally['lines_exact'] / n_:.3f}")


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    if "--espn-check" in sys.argv:
        n = int(sys.argv[sys.argv.index("--espn-check") + 1])
        cache = sys.argv[sys.argv.index("--cache") + 1] if "--cache" in sys.argv else \
            os.path.join(tempfile.gettempdir(), "espn_box")
        espn_check(conn, n, cache)
        return
    t0 = time.time()
    out, five_by_game, meta_games = build(conn)
    cur = conn.cursor()
    res = checks(cur, out, five_by_game, meta_games)
    print(json.dumps(res, indent=1))
    if "--dry-run" in sys.argv:
        print(f"dry run: nothing written ({time.time() - t0:.0f}s)")
        return
    write(cur, out, res)
    conn.commit()
    cur.execute("SELECT pg_total_relation_size('player_game_onfloor')")
    print(f"player_game_onfloor {cur.fetchone()[0] / 1e6:.1f} MB; done in {time.time() - t0:.0f}s")
    conn.close()


if __name__ == "__main__":
    main()
