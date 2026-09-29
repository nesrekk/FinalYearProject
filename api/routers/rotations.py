"""Rotation charts from the play-by-play stints.

    GET /rotations/options                      seasons, teams per season, definitions
    GET /rotations/games?team=&season=          a team-season's games (for the game picker)
    GET /rotations/game/{game_id}               one game: every player's stretches on the floor,
                                                both teams, and the score margin
    GET /rotations/team?team=&season=           a team-season: each player's share of every game
                                                minute (heatmap), starts, the most common starting
                                                five, and the closing lineups in close games

Reads `lineup_stints` / `lineup_stint_games` (scripts/build_lineup_stints.py:
every five-on-five stint of every regular-season game 2020-21 on, rebuilt
from ESPN play-by-play and reconciled game by game) and, for the closing
lineups, `rotation_closing_games` / `rotation_closing_stints`
(scripts/build_rotations.py: the same stints cut at 5:00 left in the
fourth, so the score there and the closing stretch are exact). Player
seconds come from the same parser as player_game_lines, so a player's
minutes here equal his Game Log minutes. Options and team views are cached
per process: restart impact_api after rerunning either script.
"""

from collections import Counter, defaultdict
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from lineups_lib import season_label
from source_badge import make_source
from teams_lib import lookup_codes

router = APIRouter()

TABLES = ["lineup_stints", "lineup_stint_games", "rotation_closing_games", "rotation_closing_stints",
          "player_season_stats"]
UPSTREAM = "ESPN play-by-play, rebuilt into stints (build_lineup_stints.py, build_rotations.py)"

REGULATION = 2880
CUT = 2580                  # 5:00 left in the fourth (wpa_lib.CLUTCH_SECONDS)
CLOSE_MARGIN = 5            # wpa_lib.CLUTCH_MARGIN
CLOSING_SHOW = 8
CLOSING_MIN_MINUTES = 5.0   # a closing five needs this many minutes to be ranked

GAME_METHOD = (
    "Every stint of the game rebuilt from ESPN play-by-play (scripts/build_lineup_stints.py): who is on the floor "
    "comes from the substitutions and, at the start of each period, from who shows up in the play-by-play before "
    "being subbed in. A player's minutes are the same parser's as his Game Log, so they match it. The margin is the "
    "score at every substitution and period break, from the stints' own points (made shots and free throws, checked "
    "against the real final score); between two substitutions it is drawn flat. \"Unidentified\" marks minutes when "
    "a side had fewer than five identified players, almost always a player ESPN gives no id to."
)

TEAM_METHOD = (
    "Heatmap: for each minute of regulation, the share of the team's games in which each player was on the floor, "
    "in seconds (a player on for 30 of the 60 seconds of minute 1 in every game shows 50%). Every column adds up to "
    "five players, including the unidentified row. Overtime isn't in the heatmap. Only games whose play-by-play "
    "reconciled with the real final score, the game length and the team totals are counted (lineup_stint_games."
    "game_ok); the rest are listed. Starts: in the game's first stint. Closing lineups: in close games, the last "
    "five minutes of the fourth quarter and all of overtime (the NBA's clutch window), where a game is close if the "
    "margin at 5:00 left in the fourth was within five points either way. The NBA's own clutch stats re-check the "
    "margin at every moment; this picks the games once, at 5:00, so the same stretch is compared game to game. A "
    "closing stretch counts for a five only while all five of the team's players were identified. Net rating is "
    "points per 100 possessions (FGA + 0.44 FTA - OREB + TOV averaged over both sides, about 3 points under "
    "NBA.com's scale); in a few minutes a game it is mostly noise, so the table leads with games and minutes."
)


def _team(cur, team, season):
    codes = sorted(lookup_codes(team))
    cur.execute("""SELECT DISTINCT home_team FROM lineup_stint_games WHERE season = %s AND home_team = ANY(%s)
                   UNION SELECT DISTINCT away_team FROM lineup_stint_games WHERE season = %s AND away_team = ANY(%s)""",
                (season, codes, season, codes))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"No tracked games for {team.upper()} in {season_label(season)}.")
    return row[0]


def _names(cur, ids):
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                   WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (ids,))
    out = dict(cur.fetchall())
    missing = [i for i in ids if i not in out]
    if missing:
        cur.execute("SELECT to_regclass('player_bio')")
        if cur.fetchone()[0]:
            cur.execute("SELECT player_id, player_name FROM player_bio WHERE player_id = ANY(%s)", (missing,))
            out.update(dict(cur.fetchall()))
    return out


@lru_cache(maxsize=1)
def _options():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('lineup_stint_games'), to_regclass('rotation_closing_games')")
        stints, closing = cur.fetchone()
        if not stints:
            raise HTTPException(status_code=503, detail="No stints yet: run scripts/build_lineup_stints.py.")
        cur.execute("""SELECT season, array_agg(DISTINCT team ORDER BY team) FROM (
                           SELECT season, home_team AS team FROM lineup_stint_games
                           UNION SELECT season, away_team FROM lineup_stint_games) t GROUP BY season ORDER BY season""")
        teams = {s: list(t) for s, t in cur.fetchall()}
    seasons = sorted(teams)
    return {
        "seasons": seasons, "default_season": seasons[-1] if seasons else None,
        "teams": {str(s): t for s, t in teams.items()},
        "closing_available": closing is not None,
        "definitions": {"cut_seconds": CUT, "close_margin": CLOSE_MARGIN, "closing_min_minutes": CLOSING_MIN_MINUTES},
    }


@router.get("/rotations/options")
def rotations_options():
    return {**_options(), "_source": make_source(TABLES, UPSTREAM)}


def _game_rows(cur, season, team):
    cur.execute("""SELECT g.game_id, g.nba_game_id, g.game_date, g.home_team, g.away_team, g.final_home, g.final_away,
                          g.home_pts, g.away_pts, g.periods, g.game_ok, g.tracked_ok, g.reason,
                          (SELECT bool_and(CASE WHEN g.home_team = %s THEN s.n_home ELSE s.n_away END = 5)
                           FROM lineup_stints s WHERE s.game_id = g.game_id) AS side_complete
                   FROM lineup_stint_games g
                   WHERE g.season = %s AND (g.home_team = %s OR g.away_team = %s)
                   ORDER BY g.game_date, g.game_id""", (team, season, team, team))
    out = []
    for (gid, nba_id, date, home, away, fh, fa, hp, ap, periods, game_ok, tracked_ok, reason, side_ok) in cur.fetchall():
        is_home = home == team
        pf, pa = (fh, fa) if fh is not None else (hp, ap)
        if not is_home:
            pf, pa = pa, pf
        out.append({
            "game_id": gid, "nba_game_id": nba_id, "date": date.isoformat() if date else None,
            "opponent": away if is_home else home, "home": is_home, "pts_for": pf, "pts_against": pa,
            "result": None if pf is None or pa is None else ("W" if pf > pa else "L"),
            "overtimes": max((periods or 4) - 4, 0), "game_ok": game_ok, "tracked_ok": tracked_ok,
            "side_complete": bool(side_ok), "reason": reason,
        })
    return out


@router.get("/rotations/games")
def rotations_games(team: str, season: Optional[int] = None):
    opts = _options()
    season = season or opts["default_season"]
    with get_db() as conn:
        cur = conn.cursor()
        code = _team(cur, team, season)
        games = _game_rows(cur, season, code)
    return {"team": code, "season": season, "season_label": season_label(season), "games": games,
            "_source": make_source(["lineup_stint_games", "lineup_stints"], UPSTREAM)}


def _intervals(pieces):
    """Merge [start, end] pieces that touch."""
    out = []
    for a, b in sorted(pieces):
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 1), round(b, 1)] for a, b in out]


@router.get("/rotations/game/{game_id}")
def rotations_game(game_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT game_id, nba_game_id, season, game_date, home_team, away_team, periods, game_length,
                              stint_seconds, final_home, final_away, home_pts, away_pts, points_ok, game_ok, tracked_ok,
                              reason, bad_lineup_seconds
                       FROM lineup_stint_games WHERE game_id = %s OR nba_game_id = %s""", (game_id, game_id))
        g = cur.fetchone()
        if not g:
            raise HTTPException(status_code=404, detail=f"No stints for game {game_id} (stints cover 2020-21 on).")
        keys = ["game_id", "nba_game_id", "season", "game_date", "home_team", "away_team", "periods", "game_length",
                "stint_seconds", "final_home", "final_away", "home_pts", "away_pts", "points_ok", "game_ok",
                "tracked_ok", "reason", "bad_lineup_seconds"]
        game = dict(zip(keys, g))
        cur.execute("""SELECT stint_no, period, start_elapsed, end_elapsed, seconds, home_ids, away_ids, n_home, n_away,
                              home_score, away_score, home_pts, away_pts
                       FROM lineup_stints WHERE game_id = %s ORDER BY stint_no""", (game["game_id"],))
        stints = cur.fetchall()
        names = _names(cur, {p for s in stints for p in list(s[5]) + list(s[6])})

    length = float(game["game_length"] or game["stint_seconds"] or REGULATION)
    teams = {}
    for side, team, ids_i, n_i, pts_i, opp_i in (("home", game["home_team"], 5, 7, 11, 12),
                                                  ("away", game["away_team"], 6, 8, 12, 11)):
        pieces = defaultdict(list)
        secs = Counter()
        pm = Counter()
        unknown = []
        starters = list(stints[0][ids_i]) if stints else []
        first_in = {}
        for s in stints:
            start, end, seconds = float(s[2]), float(s[3]), float(s[4])
            for p in s[ids_i]:
                pieces[p].append((start, end))
                secs[p] += seconds
                pm[p] += s[pts_i] - s[opp_i]
                first_in.setdefault(p, start)
            if s[n_i] < 5 and end > start:
                unknown.append({"from": round(start, 1), "to": round(end, 1), "missing": 5 - s[n_i]})
        players = []
        for p in pieces:
            if secs[p] < 0.05:   # in only for a stint with no clock time (the Game Log lists him with none either)
                continue
            players.append({
                "player_id": p, "player_name": names.get(p), "starter": p in starters,
                "minutes": round(secs[p] / 60, 1), "seconds": round(secs[p], 1),
                "plus_minus": int(pm[p]) if game["game_ok"] else None,
                "first_in": round(first_in[p], 1), "stretches": _intervals(pieces[p]),
            })
        players.sort(key=lambda r: (not r["starter"], r["first_in"] if not r["starter"] else -r["seconds"]))
        teams[side] = {"team": team, "players": players, "unidentified": unknown,
                       "unidentified_minutes": round(sum(u["to"] - u["from"] for u in unknown) / 60, 1)}

    margin = []
    for s in stints:
        margin.append({"t": round(float(s[2]), 1), "home": int(s[9]), "away": int(s[10]), "period": int(s[1])})
    if stints:
        last = stints[-1]
        margin.append({"t": round(float(last[3]), 1), "home": int(last[9] + last[11]), "away": int(last[10] + last[12]),
                       "period": int(last[1])})
    stint_list = [{"from": round(float(s[2]), 1), "to": round(float(s[3]), 1), "period": int(s[1]),
                   "home_ids": list(s[5]), "away_ids": list(s[6]), "home_score": int(s[9]), "away_score": int(s[10]),
                   "home_pts": int(s[11]), "away_pts": int(s[12])} for s in stints]
    periods = int(game["periods"] or (stints[-1][1] if stints else 4))
    return {
        "game_id": game["game_id"], "nba_game_id": game["nba_game_id"], "season": game["season"],
        "season_label": season_label(game["season"]),
        "date": game["game_date"].isoformat() if game["game_date"] else None,
        "home_team": game["home_team"], "away_team": game["away_team"],
        "final": {"home": game["final_home"], "away": game["final_away"]},
        "periods": periods, "length": length,
        "period_starts": [0.0] + [min(i, 4) * 720.0 + max(i - 4, 0) * 300.0 for i in range(1, periods)],
        "game_ok": game["game_ok"], "tracked_ok": game["tracked_ok"], "points_ok": game["points_ok"],
        "reason": game["reason"],
        "home": teams["home"], "away": teams["away"], "margin": margin, "stints": stint_list,
        "names": {str(k): v for k, v in names.items()},
        "method": GAME_METHOD,
        "_source": make_source(["lineup_stints", "lineup_stint_games", "player_season_stats"], UPSTREAM),
    }


def _heatmap(stints):
    """Per player: seconds in each regulation minute, summed over games."""
    by_min = defaultdict(lambda: [0.0] * 48)
    unknown = [0.0] * 48
    games_of = defaultdict(set)
    total = Counter()
    for gid, start, end, ids, n in stints:
        for p in ids:
            games_of[p].add(gid)
            total[p] += end - start
        if start >= REGULATION:
            continue
        m0 = int(start // 60)
        m1 = min(int((min(end, REGULATION) - 1e-9) // 60), 47)
        for m in range(m0, m1 + 1):
            overlap = min(end, (m + 1) * 60.0, REGULATION) - max(start, m * 60.0)
            if overlap <= 0:
                continue
            for p in ids:
                by_min[p][m] += overlap
            if n < 5:
                unknown[m] += overlap * (5 - n)
    return by_min, unknown, games_of, total


@lru_cache(maxsize=96)
def _team_view(code, season):
    with get_db() as conn:
        cur = conn.cursor()
        games = _game_rows(cur, season, code)
        cur.execute("""SELECT s.game_id, s.stint_no, s.start_elapsed, s.end_elapsed,
                              CASE WHEN s.home_team = %s THEN s.home_ids ELSE s.away_ids END,
                              CASE WHEN s.home_team = %s THEN s.n_home ELSE s.n_away END
                       FROM lineup_stints s JOIN lineup_stint_games g USING (game_id)
                       WHERE s.season = %s AND (s.home_team = %s OR s.away_team = %s) AND g.game_ok
                       ORDER BY s.game_id, s.stint_no""", (code, code, season, code, code))
        rows = cur.fetchall()
        cur.execute("SELECT to_regclass('rotation_closing_stints')")
        has_closing = cur.fetchone()[0] is not None
        closing_rows, closing_games = [], []
        if has_closing:
            cur.execute("""SELECT game_id, home_team = %s, margin_at_cut, home_final, away_final, overtimes, close_game,
                                  game_ok, game_date
                           FROM rotation_closing_games
                           WHERE season = %s AND (home_team = %s OR away_team = %s)""", (code, season, code, code))
            closing_games = cur.fetchall()
            cur.execute("""SELECT c.game_id, c.start_elapsed, c.end_elapsed, c.seconds,
                                  CASE WHEN c.home_team = %s THEN c.home_ids ELSE c.away_ids END,
                                  CASE WHEN c.home_team = %s THEN c.n_home ELSE c.n_away END,
                                  CASE WHEN c.home_team = %s THEN c.home_pts ELSE c.away_pts END,
                                  CASE WHEN c.home_team = %s THEN c.away_pts ELSE c.home_pts END,
                                  c.home_poss + c.away_poss
                           FROM rotation_closing_stints c JOIN rotation_closing_games g USING (game_id)
                           WHERE c.season = %s AND (c.home_team = %s OR c.away_team = %s) AND g.close_game AND g.game_ok
                           ORDER BY c.game_id, c.start_elapsed""", (code, code, code, code, season, code, code))
            closing_rows = cur.fetchall()
        ids = {p for r in rows for p in r[4]}
        names = _names(cur, ids)

    counted = {g["game_id"] for g in games if g["game_ok"]}
    n = len(counted)
    stints = [(r[0], float(r[2]), float(r[3]), list(r[4]), r[5]) for r in rows]
    by_min, unknown, games_of, total = _heatmap(stints)

    # Starts: the side's five in each game's first stint.
    starts = Counter()
    start_fives = Counter()
    first_seen = set()
    for r in rows:
        if r[0] in first_seen:
            continue
        first_seen.add(r[0])
        for p in r[4]:
            starts[p] += 1
        if r[5] == 5:
            start_fives[tuple(sorted(r[4]))] += 1

    # Closing: close games, the stretch from 5:00 left in the fourth.
    close_games = {g[0]: g for g in closing_games if g[6] and g[7]}
    closing_secs = Counter()
    closing_games_of = defaultdict(set)
    fives = defaultdict(lambda: {"games": set(), "seconds": 0.0, "pf": 0, "pa": 0, "poss": 0.0, "finished": []})
    stretch_total = 0.0
    unknown_closing = 0.0
    last_piece = {}
    for gid, a, b, secs, five, nside, pf, pa, poss2 in closing_rows:
        secs = float(secs)
        stretch_total += secs
        for p in five:
            closing_secs[p] += secs
            closing_games_of[p].add(gid)
        last_piece[gid] = (tuple(sorted(five)), nside)
        if nside != 5:
            unknown_closing += secs
            continue
        f = fives[tuple(sorted(five))]
        f["games"].add(gid)
        f["seconds"] += secs
        f["pf"] += pf
        f["pa"] += pa
        f["poss"] += float(poss2) / 2
    close_record = Counter()
    for gid, g in close_games.items():
        is_home, hf, af = g[1], g[3], g[4]
        won = (hf > af) if is_home else (af > hf)
        close_record["W" if won else "L"] += 1
        five, nside = last_piece.get(gid, (None, 0))
        if five and nside == 5:
            fives[five]["finished"].append(won)

    all_ids = set(ids) | {p for f in fives for p in f}
    missing_names = [p for p in all_ids if p not in names]
    if missing_names:
        with get_db() as conn:
            names.update(_names(conn.cursor(), missing_names))

    def five_out(key, f):
        poss = f["poss"]
        fin = f["finished"]
        return {
            "players": [{"player_id": p, "player_name": names.get(p)} for p in key],
            "games": len(f["games"]), "minutes": round(f["seconds"] / 60, 1),
            "pts_for": f["pf"], "pts_against": f["pa"], "plus_minus": f["pf"] - f["pa"],
            "poss": round(poss, 1),
            "net_rating": round(100 * (f["pf"] - f["pa"]) / poss, 1) if poss else None,
            "finished": len(fin), "finished_wins": sum(fin), "finished_losses": len(fin) - sum(fin),
            "starting_five": start_fives.get(key, 0) > 0,
        }

    closing = sorted((five_out(k, f) for k, f in fives.items()), key=lambda r: (-r["games"], -r["minutes"]))
    ranked = [c for c in closing if c["minutes"] >= CLOSING_MIN_MINUTES]

    players = []
    for p, secs in total.items():
        mins = by_min[p]
        g_n = len(games_of[p])
        players.append({
            "player_id": p, "player_name": names.get(p), "games": g_n, "starts": starts.get(p, 0),
            "minutes": round(secs / 60, 1), "mpg": round(secs / 60 / g_n, 1) if g_n else None,
            "share": [round(s / (60 * n), 3) if n else None for s in mins],
            "share_own": [round(s / (60 * g_n), 3) if g_n else None for s in mins],
            "closing_games": len(closing_games_of.get(p, ())),
            "closing_minutes": round(closing_secs.get(p, 0) / 60, 1),
            "closing_share": round(closing_secs[p] / stretch_total, 3) if stretch_total else None,
        })
    players.sort(key=lambda r: -r["minutes"])
    top_start = start_fives.most_common(1)
    start_five = None
    if top_start:
        key, cnt = top_start[0]
        start_five = {"players": [{"player_id": p, "player_name": names.get(p)} for p in key], "games": cnt}
    top_close = max(closing, key=lambda r: (r["minutes"], r["games"])) if closing else None

    excluded = [g for g in games if not g["game_ok"]]
    return {
        "team": code, "season": season, "season_label": season_label(season),
        "games": len(games), "games_counted": n, "excluded": excluded,
        "partial_games": sum(1 for g in games if g["game_ok"] and not g["side_complete"]),
        "players": players,
        "unidentified": [round(s / (60 * n), 3) if n else None for s in unknown],
        "unidentified_minutes": round(sum(unknown) / 60, 1),
        "starting_five": start_five,
        "closing": {
            "available": has_closing, "close_games": len(close_games),
            "record": {"wins": close_record["W"], "losses": close_record["L"]},
            "stretch_minutes": round(stretch_total / 60, 1),
            "unidentified_minutes": round(unknown_closing / 60, 1),
            "lineups": ranked[:CLOSING_SHOW], "lineups_total": len(closing),
            "most_minutes": top_close, "min_minutes": CLOSING_MIN_MINUTES,
        },
        "method": TEAM_METHOD,
    }


@router.get("/rotations/team")
def rotations_team(team: str, season: Optional[int] = None):
    opts = _options()
    season = season or opts["default_season"]
    if season not in opts["seasons"]:
        raise HTTPException(status_code=404, detail=(
            f"Rotations are built from play-by-play stints, on file for {season_label(opts['seasons'][0])} to "
            f"{season_label(opts['seasons'][-1])}; not {season_label(season)}."))
    with get_db() as conn:
        code = _team(conn.cursor(), team, season)
    return {**_team_view(code, season), "_source": make_source(TABLES, UPSTREAM)}
