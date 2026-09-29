"""
build_rotations.py
===================
The closing stretch of every regular-season game 2020-21 to 2025-26, for
the Rotations page's closing lineups (api/routers/rotations.py). Everything
else on that page (who was on the floor when, the team heatmaps) is read
live from `lineup_stints`; this script exists because a closing lineup
needs the score at exactly 5:00 left in the fourth quarter, and stints only
end at substitutions, so the stint in progress at 5:00 has to be cut there.

It replays every game once more with the shared lineup parser
(pbp_lineups.Game.stints(..., split_at=[2580])): the same stints as
build_lineup_stints.py, except the one running through 5:00 left in the
fourth is split in two, same ten players on both sides of the cut. Points
are credited the way build_lineup_stints.py chose for that game
(`lineup_stint_games.points_method`: the made shots and free throws, or
the running maximum of ESPN's score fields), so the score at 5:00 left
plus the closing stretch adds up to the real final in every reconciled
game.

Definitions (the page states them):
  closing stretch  the last five minutes of the fourth quarter and every
                   overtime (the NBA's clutch window, wpa_lib.CLUTCH_SECONDS);
  close game       the margin at 5:00 left in the fourth was within five
                   points (wpa_lib.CLUTCH_MARGIN) either way. The NBA's own
                   clutch stats re-check the margin at every moment; this
                   picks the games once, at the start of the stretch, so
                   the same stretch is compared game to game.

Tables written (dropped and rebuilt):
  rotation_closing_games   one row per game: score and margin at 5:00 left
                           in the fourth, close_game, the final, overtimes,
                           and the game's reconciliation flag from
                           lineup_stint_games (game_ok);
  rotation_closing_stints  one row per stint piece inside the closing
                           stretch: both fives, points and possessions each
                           side (FGA + 0.44 FTA - OREB + TOV, the stints'
                           credit rules), score at the piece's start.

Check printed at the end: gluing the cut stints back together must give
`lineup_stints` exactly (same fives, same seconds, same counts), game by
game; the score at 5:00 plus the closing stretch must equal the final.

Usage:
    cd scripts && python3 build_rotations.py        (~80 s)
Rerun after build_lineup_stints.py.
"""

import time
import warnings

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, STINT_STATS, elapsed, load_espn, load_season_names
from wpa_lib import CLUTCH_MARGIN, CLUTCH_SECONDS

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

FT_POSS = 0.44
CUT = 4 * 720 - CLUTCH_SECONDS          # 2580 s since tip-off = 5:00 left in the fourth


def poss(s, p):
    return s[p + "fga"] + FT_POSS * s[p + "fta"] - s[p + "oreb"] + s[p + "tov"]


def load_games(conn):
    return pd.read_sql_query(
        """SELECT game_id, nba_game_id, season, game_date, home_team, away_team, periods, points_method,
                  final_home, final_away, game_ok
           FROM lineup_stint_games ORDER BY game_date, game_id""", conn)


def load_stints(conn):
    """lineup_stints as the check needs it: per game, the stints in order."""
    cols = ["game_id", "stint_no", "period", "start_elapsed", "end_elapsed", "home_ids", "away_ids", "home_pts",
            "away_pts"] + [f"{side}_{k}" for side in ("home", "away") for k in STINT_STATS]
    df = pd.read_sql_query(f"SELECT {', '.join(cols)} FROM lineup_stints ORDER BY game_id, stint_no", conn)
    return {gid: g.to_dict("records") for gid, g in df.groupby("game_id", sort=False)}


def glue(pieces):
    """Undo the cut at 5:00: merge a piece starting at the cut into the one
    before it when it has the same ten players (the cut's only change)."""
    out = []
    for s in pieces:
        start = elapsed(s["period"], s["t0"])
        prev = out[-1] if out else None
        if (prev is not None and abs(start - CUT) < 1e-6 and prev["period"] == s["period"]
                and abs(elapsed(prev["period"], prev["t1"]) - CUT) < 1e-6
                and prev["home"] == s["home"] and prev["away"] == s["away"]):
            merged = dict(prev)
            merged["t1"] = s["t1"]
            for p in ("h_", "a_"):
                for k in ["pts_shots", "pts_score"] + STINT_STATS:
                    merged[p + k] = prev[p + k] + s[p + k]
            out[-1] = merged
        else:
            out.append(dict(s))
    return out


def same_as_stored(glued, stored, pts_key):
    """True when the glued pieces are exactly the stored stints."""
    if stored is None or len(glued) != len(stored):
        return False
    for g, s in zip(glued, stored):
        if g["period"] != s["period"] or list(g["home"]) != list(s["home_ids"]) or list(g["away"]) != list(s["away_ids"]):
            return False
        if abs(round(elapsed(g["period"], g["t0"]), 1) - s["start_elapsed"]) > 0.051:
            return False
        if abs(round(elapsed(g["period"], g["t1"]), 1) - s["end_elapsed"]) > 0.051:
            return False
        if g["h_" + pts_key] != s["home_pts"] or g["a_" + pts_key] != s["away_pts"]:
            return False
        for side, p in (("home", "h_"), ("away", "a_")):
            if any(g[p + k] != s[f"{side}_{k}"] for k in STINT_STATS):
                return False
    return True


def build_game(g, ev, season_names, all_names, stored):
    game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
    pieces, _ = game.stints(g.home_team, split_at=[CUT])
    pts_key = "pts_shots" if g.points_method == "shots" else "pts_score"
    matches = same_as_stored(glue(pieces), stored, pts_key)
    n_periods = max((s["period"] for s in pieces), default=0)
    score = [0, 0]
    at_cut = None
    closing = []
    for s in pieces:
        start, end = elapsed(s["period"], s["t0"]), elapsed(s["period"], s["t1"])
        if at_cut is None and start >= CUT - 1e-6 and n_periods >= 4:
            at_cut = tuple(score)
        if start >= CUT - 1e-6 and n_periods >= 4:
            closing.append({
                "game_id": g.game_id, "season": int(g.season), "home_team": g.home_team, "away_team": g.away_team,
                "period": s["period"], "start_elapsed": round(start, 1), "end_elapsed": round(end, 1),
                "seconds": round(end - start, 1), "home_ids": s["home"], "away_ids": s["away"],
                "n_home": len(s["home"]), "n_away": len(s["away"]),
                "home_score": score[0], "away_score": score[1],
                "home_pts": s["h_" + pts_key], "away_pts": s["a_" + pts_key],
                "home_poss": round(poss(s, "h_"), 2), "away_poss": round(poss(s, "a_"), 2),
                "game_ok": bool(g.game_ok),
            })
        score[0] += s["h_" + pts_key]
        score[1] += s["a_" + pts_key]
    if at_cut is None and n_periods >= 4:
        at_cut = tuple(score)   # nothing happened after 5:00 left (never seen, kept for safety)
    margin = None if at_cut is None else at_cut[0] - at_cut[1]
    final_ok = pd.notna(g.final_home) and (score[0], score[1]) == (int(g.final_home), int(g.final_away))
    row = {
        "game_id": g.game_id, "nba_game_id": g.nba_game_id if pd.notna(g.nba_game_id) else None, "season": int(g.season), "game_date": g.game_date,
        "home_team": g.home_team, "away_team": g.away_team, "periods": n_periods,
        "overtimes": max(n_periods - 4, 0),
        "home_at_cut": None if at_cut is None else at_cut[0], "away_at_cut": None if at_cut is None else at_cut[1],
        "margin_at_cut": margin, "close_game": margin is not None and abs(margin) <= CLUTCH_MARGIN,
        "home_final": score[0], "away_final": score[1], "final_matches": final_ok,
        "closing_seconds": round(sum(c["seconds"] for c in closing), 1), "closing_pieces": len(closing),
        "game_ok": bool(g.game_ok), "matches_stints": matches,
    }
    return closing, row


GAMES_DDL = """CREATE TABLE rotation_closing_games (
    game_id TEXT PRIMARY KEY, nba_game_id TEXT, season INTEGER NOT NULL, game_date DATE, home_team TEXT NOT NULL,
    away_team TEXT NOT NULL, periods SMALLINT, overtimes SMALLINT, home_at_cut SMALLINT, away_at_cut SMALLINT,
    margin_at_cut SMALLINT, close_game BOOLEAN NOT NULL, home_final SMALLINT, away_final SMALLINT,
    final_matches BOOLEAN NOT NULL, closing_seconds REAL, closing_pieces SMALLINT, game_ok BOOLEAN NOT NULL,
    matches_stints BOOLEAN NOT NULL)"""

STINTS_DDL = """CREATE TABLE rotation_closing_stints (
    game_id TEXT NOT NULL, season INTEGER NOT NULL, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
    period SMALLINT NOT NULL, start_elapsed REAL NOT NULL, end_elapsed REAL NOT NULL, seconds REAL NOT NULL,
    home_ids INTEGER[] NOT NULL, away_ids INTEGER[] NOT NULL, n_home SMALLINT NOT NULL, n_away SMALLINT NOT NULL,
    home_score SMALLINT NOT NULL, away_score SMALLINT NOT NULL, home_pts SMALLINT NOT NULL, away_pts SMALLINT NOT NULL,
    home_poss REAL NOT NULL, away_poss REAL NOT NULL, game_ok BOOLEAN NOT NULL)"""


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    games = load_games(conn)
    stored = load_stints(conn)
    _, grouped = load_espn(conn)
    print(f"{len(games)} games ({time.time() - t0:.0f}s)")

    closing, rows = [], []
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        c, r = build_game(g, ev, season_names, all_names, stored.get(g.game_id))
        closing.extend(c)
        rows.append(r)
        if i % 1500 == 0:
            print(f"  {i} games ({time.time() - t0:.0f}s)")

    cur.execute("DROP TABLE IF EXISTS rotation_closing_stints; DROP TABLE IF EXISTS rotation_closing_games;")
    cur.execute(GAMES_DDL)
    gcols = list(rows[0].keys())
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rotation_closing_games ({', '.join(gcols)}) VALUES %s",
        [tuple(r[c] for c in gcols) for r in rows], page_size=2000)
    cur.execute("CREATE INDEX ON rotation_closing_games (season, home_team);")
    cur.execute("CREATE INDEX ON rotation_closing_games (season, away_team);")
    cur.execute(STINTS_DDL)
    scols = list(closing[0].keys())
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rotation_closing_stints ({', '.join(scols)}) VALUES %s",
        [tuple(r[c] for c in scols) for r in closing], page_size=5000)
    cur.execute("CREATE INDEX ON rotation_closing_stints (game_id);")
    cur.execute("CREATE INDEX ON rotation_closing_stints (season, home_team);")
    cur.execute("CREATE INDEX ON rotation_closing_stints (season, away_team);")
    conn.commit()
    print(f"wrote {len(rows)} games, {len(closing)} closing pieces ({time.time() - t0:.0f}s)")

    # Checks.
    df = pd.DataFrame(rows)
    print(f"\nGlued back together, the cut stints equal lineup_stints in {df.matches_stints.sum()} of {len(df)} games")
    bad = df[~df.matches_stints]
    if len(bad):
        print(bad[["game_id", "season", "game_ok"]].head(10).to_string(index=False))
    ok = df[df.game_ok]
    print(f"Reconciled games (game_ok): {len(ok)}; score at 5:00 + closing stretch = real final in "
          f"{ok.final_matches.sum()} of them")
    print(f"Games with fewer than four periods (no 5:00 mark): {(df.periods < 4).sum()}")
    per = df.groupby("season").agg(games=("game_id", "size"), ok=("game_ok", "sum"), close=("close_game", "sum"),
                                   ot=("overtimes", lambda x: (x > 0).sum()))
    per["close_share"] = (per.close / per.games).round(3)
    print("\nClose games (within 5 at 5:00 left in the fourth) per season:")
    print(per.to_string())
    for t in ("rotation_closing_games", "rotation_closing_stints"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")
    conn.close()


if __name__ == "__main__":
    main()
