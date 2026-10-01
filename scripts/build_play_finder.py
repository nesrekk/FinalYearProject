"""
build_play_finder.py
=====================
Every play of every regular-season game 2020-21 to 2025-26, as one
searchable table for the Play Finder (GET /plays/finder).

The ESPN play-by-play (pbp_events) says who shot, but the rest a finder
needs is only in the text or has to be worked out game by game: the
passer, blocker and stealer are names inside the description ("(Jamal
Murray assists)", "Luke Kennard blocks ...", "(Jarrett Allen steals)"),
half of ESPN's made field goals don't say whether they were twos or
threes, and the score fields are stale in a few hundred games. So each
game goes through the shared play-by-play parser once
(pbp_lineups.Game.parse: the same shots, values and name -> id matching as
player_game_lines; nothing here re-parses shots or matches names) and every
player's part in every play becomes a row:

  shot          made 2 / made 3 / missed 2 / missed 3 (a blocked shot is a
                miss); the assist on a make (ast2/ast3) and the block on a
                miss are rows of their own for the passer and blocker.
                Two or three: for makes, as the parser calls it (the
                shooter's team score step; the NBA shot chart agrees on
                99.97% of matched makes); for misses, the NBA shot chart's
                shot type where the shot is matched (below), else the
                parser's call ("three point" in the text, or 23+ ft): the
                parser's `miss_threes` input, exactly as player_game_lines
                (the text alone calls ~8,700 of the NBA's missed threes
                twos, median 26 ft);
  free throw    made / missed;
  rebound       offensive / defensive, players only (team rebounds aren't
                anybody's play: the team_game_totals convention);
  turnover      including team turnovers (player NULL), and the steal on it;
  foul          every foul and technical the parser doesn't read as
                something else (api/play_finder.is_foul).
A name in the text that matches no id (ESPN gives ~100-155 players a
season no id) keeps its row with player NULL; the API shows the name from
the text.

Score at the time: the score just before the play, from the row player's
team's side. Taken, per game, from the made shots and free throws
themselves when they add up to the real final score (game_scores), else
from the running maximum of ESPN's score fields when that does, else from
the shots and the game is flagged (score_ok false): the same rule as
lineup_stints (their per-game choice is checked to agree).

Clock (since round 6 step 3b): the corrected clock of pbp_event_clock
(build_event_clock.py; Game(..., clock=)), not ESPN's own, which stamps
made shots a median 14 s and rebounds 6 s late, so end-of-period and
clutch searches find the plays that really happened then. The score
before each play doesn't depend on the clock (it follows the event order).

Distance: the NBA shot chart's coordinates for the same shot where the two
feeds match (build_rim_deterrence.match_coordinates: order within game,
shooter and period with identical make/miss sequences, ~99% of attempts;
sqrt(x^2 + y^2), never player_shots.shot_distance), else ESPN's "N-foot",
else none (ESPN gives no distance for most layups and dunks it can't be
matched on). Assist and block rows carry their shot's distance.

Games: every ESPN game linked to a real regular-season result in
game_scores, i.e. all but the three NBA Cup finals (they don't count in
regular-season stats; Game Log and the assist network drop them too). ESPN
is the one kept copy of each game (wpa_lib.PBP_DEDUP_WHERE drops only
nba_api twins).

Tables written (dropped and rebuilt):
  play_finder_games    game_no (1..n by date), ESPN and NBA ids, season,
                       date, teams, real final, score source and score_ok;
  play_finder_events   one row per player per play: pbp_events.id,
                       player_id, game_no, cat (api/play_finder.CATS),
                       period, clock (tenths of a second left in the
                       period), score_for / score_against before the play,
                       dist (feet), is_home;
  play_finder_seasons  per season: games, rows, how distances were found,
                       unidentified rows, and the check below.

Checks printed (and stored per season): every player-game's shots, free
throws, rebounds, assists, steals, blocks and turnovers here against his
player_game_lines line (same parser and the same shot-chart calls, so
they must match, three-point attempts included), and
Bam Adebayo's 83 on 2026-03-10 (20 field goals, 7 threes, 36 free throws).

Usage:
    cd scripts && python3 build_play_finder.py     (~2 min)
Rerun after new play-by-play is loaded, after build_player_game_lines.py
(they must agree), after build_event_clock.py or after player_shots is
reloaded (then build_event_clock.py first). Restart impact_api
afterwards (the router caches the games and seasons tables).
"""

import io
import re
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

from db_config import DB_CONFIG
from pbp_lineups import (ASSIST_RE, BLOCK_RE, DIST_RE, PERIOD_SECONDS, STEAL_RE, Game, game_clock, load_espn,
                         load_season_names, match_coordinates, miss_three_calls)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from play_finder import CATS, CODE, SHOT_CODES, is_foul  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

COLS = ["event_id", "player_id", "game_no", "cat", "period", "clock", "score_for", "score_against", "dist", "is_home"]
LINE_STATS = {"fgm": (1, 2), "fga": (1, 2, 3, 4), "fg3m": (2,), "fg3a": (2, 4), "ftm": (11,), "fta": (11, 12),
              "oreb": (13,), "dreb": (14,), "ast": (5, 6), "stl": (8,), "blk": (7,), "tov": (9,)}


def period_clock(period, secs):
    """Tenths of a second left in the period (seconds_remaining counts down all of regulation)."""
    left = secs - (4 - period) * PERIOD_SECONDS if period <= 4 else secs
    return int(round(min(max(left, 0.0), PERIOD_SECONDS) * 10))


def game_rows(game, g, game_no, ev, final):
    """Rows for one game, the game's score source, and the fg events (for distances)."""
    home, away = g.home_team, g.away_team
    game.home = home  # parse() needs the home side for score steps (walk() sets it the same way)
    idx = ev.set_index(ev["action_number"].astype(int))
    event_id = idx["id"].astype(int).to_dict()
    desc = idx["description"].to_dict()
    named = idx["player_name"].map(lambda x: isinstance(x, str) and bool(x)).to_dict()
    parsed = game.parse()

    # The score before each event, both ways.
    shots, high, raw = {home: 0, away: 0}, {home: 0, away: 0}, {home: 0, away: 0}
    before_shots, before_high = [], []
    for e in parsed:
        before_shots.append((shots[home], shots[away]))
        before_high.append((high[home], high[away]))
        if e["team"] in shots and e["kind"] in ("fg", "ft") and e["made"]:
            shots[e["team"]] += e["val"] if e["kind"] == "fg" else 1
        raw[home] += e["dh"]
        raw[away] += e["da"]
        for t in (home, away):
            high[t] = max(high[t], raw[t])
    fh, fa = final
    if (shots[home], shots[away]) == (fh, fa):
        method, ok, before = "shots", True, before_shots
    elif (high[home], high[away]) == (fh, fa):
        method, ok, before = "score", True, before_high
    else:
        method, ok, before = "shots", False, before_shots

    rows, fgs, diag = [], [], Counter()
    for e, (bh, ba) in zip(parsed, before):
        team, n = e["team"], e["action_number"]
        if team not in (home, away):
            if e["kind"] in ("fg", "ft", "oreb", "dreb", "tov"):
                diag["no_team"] += 1
            continue
        own_home = team == home
        clock = period_clock(e["period"], game.secs(e))
        eid = event_id[n]
        text = desc.get(n, "")

        def add(pid, code, defense=False):
            h = own_home != defense
            rows.append((eid, pid, game_no, code, e["period"], clock, bh if h else ba, ba if h else bh, None, h, n))

        kind = e["kind"]
        if kind == "fg":
            three = e["val"] == 3
            add(e["pid"], CODE["made3" if three else "made2"] if e["made"] else CODE["miss3" if three else "miss2"])
            fgs.append((g.game_id, n, eid, e["pid"], e["period"], bool(e["made"])))
            if e["made"] and ASSIST_RE.search(text):
                add(e.get("assist"), CODE["ast3" if three else "ast2"])
            if not e["made"] and BLOCK_RE.match(text):
                add(e.get("blocker"), CODE["blk"], defense=True)
        elif kind == "ft":
            add(e["pid"], CODE["ftm" if e["made"] else "ftx"])
        elif kind in ("oreb", "dreb"):
            if e["pid"] or named.get(n):
                add(e["pid"], CODE[kind])
            else:
                diag["team_rebounds"] += 1
        elif kind == "tov":
            add(e["pid"], CODE["tov"])
            if STEAL_RE.search(text):
                add(e.get("steal"), CODE["stl"], defense=True)
        elif kind is None and is_foul(e["action"]):
            add(e["pid"], CODE["foul"])
    return rows, fgs, method, ok, diag


def collect(conn, cur):
    cur.execute("SELECT to_regclass('public.pbp_event_clock')")
    if cur.fetchone()[0] is None:
        sys.exit("pbp_event_clock is missing: run build_event_clock.py first.")
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn, clock=True)
    ref = pd.read_sql_query(
        """SELECT 'espn_' || espn_id AS game_id, game_id AS nba_game_id, team_abbreviation AS team, pts_for, periods
           FROM game_scores WHERE espn_id IS NOT NULL""", conn)
    final = {(r.game_id, r.team): int(r.pts_for) for r in ref.itertuples()}
    nba_id = dict(zip(ref.game_id, ref.nba_game_id))
    periods = dict(zip(ref.game_id, ref.periods))
    calls, misses = miss_three_calls(conn, games, grouped, season_names, all_names)
    flipped = misses[(misses.text_three != misses.nba_three) & misses.game_id.isin(nba_id)]
    retyped = flipped.groupby("season").size()
    print(f"misses where the NBA shot chart's two-or-three call differs from the text's: {len(flipped)} "
          f"({retyped.to_dict()})")
    left_out = sorted(set(games.game_id) - set(nba_id))
    print(f"left out: {len(left_out)} ESPN games with no regular-season result in game_scores (the NBA Cup finals): "
          f"{', '.join(left_out)}")
    rows, fgs, game_list, diag = [], [], [], Counter()
    game_no = 0
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None or g.game_id not in nba_id:
            continue
        game_no += 1
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names,
                    miss_threes=calls.get(g.game_id), clock=game_clock(ev)[0])
        fh, fa = final[(g.game_id, g.home_team)], final[(g.game_id, g.away_team)]
        r, f, method, ok, d = game_rows(game, g, game_no, ev, (fh, fa))
        rows.extend(r)
        fgs.extend(f)
        diag.update(d)
        game_list.append((game_no, g.game_id, nba_id[g.game_id], int(g.season), g.game_date, g.home_team, g.away_team,
                          fh, fa, int(periods[g.game_id]), method, ok, len(r)))
        if i % 1500 == 0:
            print(f"  {i} of {len(games)} games, {len(rows):,} rows")
    ev_df = pd.DataFrame(rows, columns=COLS + ["action_number"])
    games_df = pd.DataFrame(game_list, columns=["game_no", "game_id", "nba_game_id", "season", "game_date", "home_team",
                                                "away_team", "final_home", "final_away", "periods", "score_source",
                                                "score_ok", "plays"])
    fg_df = pd.DataFrame(fgs, columns=["game_id", "action_number", "event_id", "pid", "period", "made"])
    ev_df["player_id"] = pd.to_numeric(ev_df["player_id"]).astype("Int64")
    return ev_df, games_df, fg_df, diag, retyped


def distances(conn, ev_df, games_df, fg_df):
    """Feet for every shot row (and its assist/block rows): coordinates, else text, else none."""
    m = match_coordinates(conn, fg_df)
    text = pd.read_sql_query(
        """SELECT e.id AS event_id, e.description FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' AND (e.description ~ ' (makes|misses) ' OR e.description ~ ' blocks ')""", conn)
    text["text_ft"] = text.description.str.extract(DIST_RE.pattern)[0].astype(float)
    m = m.merge(text[["event_id", "text_ft"]], on="event_id", how="left")
    m["feet"] = np.where(m.coord_ft.notna(), np.round(m.coord_ft), m.text_ft)
    m["src"] = np.select([m.coord_ft.notna(), m.text_ft.notna()], ["coords", "text"], "none")
    feet = m.set_index("event_id")["feet"]
    shot_rows = ev_df.cat.isin(SHOT_CODES)
    ev_df["dist"] = ev_df["event_id"].map(feet).where(shot_rows).round().astype("Int64")
    season_of = m.merge(games_df[["game_id", "season"]], on="game_id")
    src = season_of.groupby(["season", "src"]).size().unstack(fill_value=0)
    return ev_df, src


def check_lines(conn, ev_df, games_df):
    """Every player-game's counts here against player_game_lines (same parser: must match)."""
    df = ev_df[ev_df.player_id.notna()].merge(games_df[["game_no", "game_id", "season"]], on="game_no")
    for stat, codes in LINE_STATS.items():
        df[stat] = df.cat.isin(codes).astype(int)
    mine = df.groupby(["game_id", "player_id"])[list(LINE_STATS)].sum()
    lines = pd.read_sql_query("SELECT game_id, player_id, season, " + ", ".join(LINE_STATS) + " FROM player_game_lines",
                              conn)
    lines = lines[lines.game_id.isin(games_df.game_id)].set_index(["game_id", "player_id"])
    both = lines[list(LINE_STATS)].join(mine, how="outer", rsuffix="_pf").fillna(0)
    both["season"] = both.index.get_level_values(0).map(dict(zip(games_df.game_id, games_df.season)))
    differ = np.zeros(len(both), dtype=bool)
    for stat in LINE_STATS:
        d = both[stat] != both[stat + "_pf"]
        if d.any():
            print(f"  {stat}: {int(d.sum())} player-games differ")
        differ |= d.values
    both["differ"] = differ
    both["fg3a_differ"] = both["fg3a"] != both["fg3a_pf"]
    print(f"player-games checked: {len(both):,}, differing: {int(differ.sum())}")
    if differ.any():
        print(both[both.differ].head(10).to_string())
    return both.groupby("season").agg(lines_checked=("differ", "size"), lines_differ=("differ", "sum"),
                                      lines_fg3a_differ=("fg3a_differ", "sum"))


def copy_rows(cur, table, df, cols):
    buf = io.StringIO()
    df[cols].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    ev_df, games_df, fg_df, diag, retyped = collect(conn, cur)
    print(f"{len(games_df):,} games, {len(ev_df):,} rows ({time.time() - t0:.0f}s); {dict(diag)}")
    ev_df, src = distances(conn, ev_df, games_df, fg_df)
    print("shot distances by source:\n" + src.to_string())

    # Score source: must agree with lineup_stint_games' own per-game choice.
    stint = pd.read_sql_query("SELECT game_id, points_method, points_ok FROM lineup_stint_games", conn)
    cmp = games_df.merge(stint, on="game_id", how="left")
    agree = int(((cmp.points_method == cmp.score_source) & (cmp.points_ok == cmp.score_ok)).sum())
    print(f"score source: {games_df.score_source.value_counts().to_dict()}, not reconciled: "
          f"{int((~games_df.score_ok).sum())}; agrees with lineup_stint_games in {agree} of {len(cmp)} games")

    checks = check_lines(conn, ev_df, games_df)

    bam = ev_df.merge(games_df[["game_no", "game_date"]], on="game_no")
    bam = bam[(bam.player_id == 1628389) & (bam.game_date.astype(str) == "2026-03-10")]
    fgm, fg3, ftm = int(bam.cat.isin((1, 2)).sum()), int((bam.cat == 2).sum()), int((bam.cat == 11).sum())
    fga, fg3a = int(bam.cat.isin((1, 2, 3, 4)).sum()), int(bam.cat.isin((2, 4)).sum())
    pts = int(bam.cat.map(lambda c: CATS[c][2] if c in (1, 2, 11) else 0).sum())
    print(f"Bam Adebayo 2026-03-10: {fgm}-{fga} FG, {fg3}-{fg3a} threes, {ftm} FT = {pts} points "
          "(expect 20 FG, 7 threes, 36 FT, 83; NBA shot chart 20-43, 7-22)")

    ev_df = ev_df.sort_values(["game_no", "action_number", "cat"]).reset_index(drop=True)
    est = len(ev_df) * 56 / 1e6
    print(f"writing {len(ev_df):,} rows (~{est:.0f} MB of heap before indexes)")

    seasons = games_df.groupby("season").agg(games=("game_no", "size"), games_score_ok=("score_ok", "sum"),
                                             rows=("plays", "sum"))
    unid = ev_df[ev_df.player_id.isna() & ~ev_df.cat.isin((CODE["tov"], CODE["foul"]))]
    seasons["unidentified_rows"] = unid.merge(games_df[["game_no", "season"]], on="game_no").groupby("season").size()
    seasons = seasons.join(src.rename(columns={"coords": "shots_coords", "text": "shots_text", "none": "shots_no_dist"}))
    seasons["misses_retyped"] = retyped
    seasons = seasons.join(checks).fillna(0).astype(int).reset_index()

    cur.execute("DROP TABLE IF EXISTS play_finder_events, play_finder_games, play_finder_seasons;")
    cur.execute("""CREATE TABLE play_finder_games (
        game_no SMALLINT PRIMARY KEY, game_id TEXT NOT NULL UNIQUE, nba_game_id TEXT NOT NULL, season SMALLINT NOT NULL,
        game_date DATE NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL, final_home SMALLINT NOT NULL,
        final_away SMALLINT NOT NULL, periods SMALLINT NOT NULL, score_source TEXT NOT NULL, score_ok BOOLEAN NOT NULL,
        plays INTEGER NOT NULL)""")
    copy_rows(cur, "play_finder_games", games_df, list(games_df.columns))
    cur.execute("""CREATE TABLE play_finder_events (
        event_id INTEGER NOT NULL, player_id INTEGER, game_no SMALLINT NOT NULL, cat SMALLINT NOT NULL,
        period SMALLINT NOT NULL, clock SMALLINT NOT NULL, score_for SMALLINT NOT NULL, score_against SMALLINT NOT NULL,
        dist SMALLINT, is_home BOOLEAN NOT NULL)""")
    copy_rows(cur, "play_finder_events", ev_df, COLS)
    cur.execute("CREATE INDEX play_finder_events_player ON play_finder_events (player_id, game_no) "
                "WHERE player_id IS NOT NULL;")
    cur.execute("CREATE INDEX play_finder_events_game ON play_finder_events USING brin (game_no);")
    cur.execute("""CREATE TABLE play_finder_seasons (
        season SMALLINT PRIMARY KEY, games INTEGER, games_score_ok INTEGER, rows INTEGER, unidentified_rows INTEGER,
        shots_coords INTEGER, shots_text INTEGER, shots_no_dist INTEGER, misses_retyped INTEGER, lines_checked INTEGER,
        lines_differ INTEGER, lines_fg3a_differ INTEGER)""")
    copy_rows(cur, "play_finder_seasons", seasons, ["season", "games", "games_score_ok", "rows", "unidentified_rows",
                                                    "shots_coords", "shots_text", "shots_no_dist", "misses_retyped",
                                                    "lines_checked", "lines_differ", "lines_fg3a_differ"])
    conn.commit()
    cur.execute("ANALYZE play_finder_events; ANALYZE play_finder_games;")
    conn.commit()
    for t in ("play_finder_events", "play_finder_games", "play_finder_seasons"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t};")
        n, size = cur.fetchone()
        print(f"{t}: {n:,} rows, {size}")
    print(seasons.to_string(index=False))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
