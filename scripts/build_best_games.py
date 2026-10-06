"""
build_best_games.py
====================
One row per regular-season game 2020-21 to 2025-26 with how much happened in
it, for Teams > Best Games & Upsets (GET /best-games).

For every game the win probability of the home team is run through the same
trained model Game Replay and the Clutch WPA leaderboard use (wpa_lib), at
every play of the game, on the reconciled score: the score before each play
from play_finder_events (built by build_play_finder.py from the shared
play-by-play parser: shots and free throws, or ESPN's running-maximum score
fields when those reconcile, checked against the real final in game_scores),
plus the start of the game (tied, 48:00 left) and the real final score at
the buzzer. Times are Play Finder's, i.e. the corrected clock of
pbp_event_clock (build_event_clock.py; since round 6 step 3b), so a late
basket is read at the moment it was scored, not up to ~15 s later as
ESPN stamps it. From that series, per game:

  swing           sum of |change in win probability| from play to play
  lead_changes    times the lead passed from one team to the other
  ties            times the score was tied after having been un-tied
  largest_lead    the winner's biggest lead
  comeback        the winner's biggest deficit (0 if he never trailed)
  win_min_wp      the winner's lowest win probability at any point
  final_margin, periods, events (plays the win probability was read at)
  peak_*          the single play that moved win probability most: its event
                  id (pbp_events.id), seconds since tip-off, and the size of
                  the swing it caused (a link into Game Replay)
  excitement      api/best_games.py's formula (weights are a stated judgment,
                  not fitted); games whose score doesn't reconcile
                  (score_ok false) keep their numbers but the API leaves them
                  out of every ranking.

The ESPN score fields glitch in some games (Game Replay's raw chart reads
them as they are); this table uses the reconciled score, so a game's swing
here can differ a little from the sum you would read off Game Replay's chart
in those games. best_games_meta stores the formula's weights and how much
they matter (rank correlation and top-50 overlap with the plain swing).

Rerun after build_play_finder.py (new play-by-play, a player_game_lines
rebuild, a player_shots reload) or a change to the win-probability model.

Usage:
    cd scripts && python3 build_best_games.py
    cd scripts && python3 build_best_games.py --season 2027
        # round 9 step 3: only that season's rows are deleted and rebuilt (the
        # same per-game replay on that season's Play Finder rows);
        # best_games_meta (weights, rank agreement over every game) is left as
        # it is (R9-008). Needs the full build's table.
"""

import io
import os
import sys
import time

import numpy as np
import pandas as pd
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

from best_games import LEAD_CHANGE_W, MARGIN_W, OVERTIME_W, excitement  # noqa: E402
from compute_wpa import compute_win_probs  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402
import season_mode as SM  # noqa: E402
from wpa_lib import load_model  # noqa: E402

END = 10 ** 9   # event id of the closing point, after every real event


def copy_rows(cur, table, df, cols):
    buf = io.StringIO()
    df[cols].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def lead_changes(signs):
    """Changes of leader in one game's sequence of score signs (ties skipped)."""
    v = signs[signs != 0]
    return int((v[1:] != v[:-1]).sum()) if len(v) > 1 else 0


def load(conn, season=None):
    where = "" if season is None else f" WHERE season = {int(season)}"
    games = pd.read_sql_query(f"SELECT * FROM play_finder_games{where} ORDER BY game_no", conn).set_index("game_no")
    ev_where = "" if season is None else f" WHERE game_no IN (SELECT game_no FROM play_finder_games{where})"
    ev = pd.read_sql_query(
        f"""SELECT game_no, event_id, MIN(period) AS period, MIN(clock) AS clock,
                  MAX(CASE WHEN is_home THEN score_for ELSE score_against END) AS sh,
                  MAX(CASE WHEN is_home THEN score_against ELSE score_for END) AS sa
           FROM play_finder_events{ev_where} GROUP BY game_no, event_id""", conn)
    return games, ev


def series(games, ev):
    """Every game's states in order: (game_no, event_id, seconds left, margin, elapsed)."""
    ev = ev.sort_values(["game_no", "event_id"]).reset_index(drop=True)
    left = ev.clock.to_numpy() / 10
    period = ev.period.to_numpy()
    ev["secs"] = np.where(period <= 4, left + (4 - period) * 720, left)      # what wpa_lib.win_prob is given
    ev["elapsed"] = np.where(period <= 4, (period - 1) * 720 + 720 - left, 2880 + (period - 5) * 300 + 300 - left)
    ev["m"] = (ev.sh - ev.sa).astype(float)
    n = len(games)
    start = pd.DataFrame({"game_no": games.index, "event_id": -1, "secs": 2880.0, "elapsed": 0.0, "m": 0.0})
    length = np.where(games.periods.to_numpy() <= 4, 2880.0, 2880.0 + (games.periods.to_numpy() - 4) * 300.0)
    end = pd.DataFrame({"game_no": games.index, "event_id": END, "secs": 0.0, "elapsed": length,
                        "m": (games.final_home - games.final_away).astype(float).to_numpy()})
    cols = ["game_no", "event_id", "secs", "elapsed", "m"]
    out = pd.concat([start, ev[cols], end[cols]]).sort_values(["game_no", "event_id"]).reset_index(drop=True)
    assert len(out) == len(ev) + 2 * n
    return out


def per_game(games, pts):
    model, scaler = load_model()
    pts["wp"] = compute_win_probs(model, scaler, pts.secs.to_numpy(), pts.m.to_numpy())
    g = pts.groupby("game_no")
    pts["dwp"] = g.wp.diff().abs()
    pts["sign"] = np.sign(pts.m)
    # the play that caused a step is the one before it (the score is "before the play")
    pts["cause_event"] = g.event_id.shift()
    pts["cause_t"] = g.elapsed.shift()

    res = g.agg(swing=("dwp", "sum"), events=("wp", "size"), wp_min=("wp", "min"), wp_max=("wp", "max"),
                m_min=("m", "min"), m_max=("m", "max"))
    res["events"] -= 2
    res["lead_changes"] = g.sign.apply(lambda s: lead_changes(s.to_numpy()))
    # ties: entering a tie from a lead (the opening 0-0 doesn't count)
    tie_in = (pts.m == 0) & (g.m.shift() != 0) & g.m.shift().notna()
    res["ties"] = tie_in.groupby(pts.game_no).sum()
    peak = pts.loc[pts.groupby("game_no").dwp.idxmax()].set_index("game_no")
    res["peak_dwp"] = peak.dwp
    res["peak_event_id"] = peak.cause_event.where(peak.cause_event >= 0)
    res["peak_t"] = peak.cause_t

    res = res.join(games.drop(columns=["plays"]))
    home_won = res.final_home > res.final_away
    res["final_margin"] = (res.final_home - res.final_away).abs()
    res["largest_lead"] = np.where(home_won, res.m_max, -res.m_min).clip(min=0)
    res["comeback"] = np.where(home_won, -res.m_min, res.m_max).clip(min=0)
    res["win_min_wp"] = np.where(home_won, res.wp_min, 1 - res.wp_max)
    res["excitement"] = excitement(res.swing, res.lead_changes, res.periods - 4, res.final_margin)
    res = res.rename(columns={"final_home": "pts_home", "final_away": "pts_away"})
    return res


def main():
    season = SM.parse_season()
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('play_finder_events')")
    if cur.fetchone()[0] is None:
        sys.exit("play_finder_events is missing: run build_play_finder.py first.")
    games, ev = load(conn, season)
    print(f"{len(games):,} games, {len(ev):,} distinct plays ({time.time() - t0:.0f}s)")
    res = per_game(games, series(games, ev))

    # Reconciliation: the last state before the closing point is the game's own score.
    ok = res[res.score_ok]
    print(f"score_ok: {len(ok):,} of {len(res):,} games; {int((~res.score_ok).sum())} left out of rankings")
    print(res[["swing", "lead_changes", "ties", "comeback", "final_margin", "excitement"]].describe().round(2).to_string())
    by_ot = res.groupby(res.periods - 4).swing.agg(["size", "mean"])
    print("mean swing by overtime periods:\n" + by_ot.round(2).to_string())

    # How much the weights matter: agreement with the plain swing.
    rho = float(ok.excitement.rank().corr(ok.swing.rank()))
    top = lambda col: set(ok.sort_values(col, ascending=False).head(50).index)  # noqa: E731
    overlap = len(top("excitement") & top("swing"))
    print(f"excitement vs plain swing: Spearman {rho:.4f}, top-50 overlap {overlap}")

    cols = ["season", "game_date", "home_team", "away_team", "pts_home", "pts_away", "periods", "events", "score_ok",
            "swing", "lead_changes", "ties", "largest_lead", "comeback", "win_min_wp", "final_margin", "excitement",
            "peak_dwp", "peak_event_id", "peak_t"]
    out = res.reset_index(drop=True)[["game_id", "nba_game_id"] + cols].copy()
    for c in ("lead_changes", "ties", "largest_lead", "comeback", "final_margin", "peak_event_id"):
        out[c] = out[c].astype("Int64")
    out["swing"], out["excitement"] = out.swing.round(4), out.excitement.round(4)
    out["win_min_wp"], out["peak_dwp"], out["peak_t"] = out.win_min_wp.round(4), out.peak_dwp.round(4), out.peak_t.round(1)
    out = out.sort_values(["game_date", "game_id"])

    meta = pd.DataFrame([
        ("lead_change_w", LEAD_CHANGE_W, "excitement points per lead change"),
        ("overtime_w", OVERTIME_W, "excitement points per overtime period"),
        ("margin_w", MARGIN_W, "excitement points taken off per point of final margin"),
        ("rank_corr_swing", rho, "Spearman correlation of excitement with the plain win-probability swing, score_ok games"),
        ("top50_overlap_swing", overlap, "of the 50 highest excitement scores, how many are also among the 50 highest swings"),
        ("games", len(out), "games in best_games"),
        ("games_ranked", len(ok), "games with score_ok (the only ones the API ranks)"),
    ], columns=["name", "value", "note"])

    if season is not None:
        SM.require_tables(cur, ["best_games"], season)
        print(f"--season {season}: {SM.delete_season(cur, 'best_games', season):,} stored rows of the season deleted; "
              "best_games_meta left as it is (its rank agreement covers every game)")
        copy_rows(cur, "best_games", out, ["game_id", "nba_game_id"] + cols)
        conn.commit()
        cur.execute("ANALYZE best_games;")
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM best_games WHERE season = %s", (season,))
        print(f"best_games: {cur.fetchone()[0]:,} rows of the season; done in {time.time() - t0:.0f}s")
        conn.close()
        return
    cur.execute("DROP TABLE IF EXISTS best_games, best_games_meta;")
    cur.execute("""CREATE TABLE best_games (
        game_id TEXT PRIMARY KEY, nba_game_id TEXT NOT NULL UNIQUE, season SMALLINT NOT NULL, game_date DATE NOT NULL,
        home_team TEXT NOT NULL, away_team TEXT NOT NULL, pts_home SMALLINT NOT NULL, pts_away SMALLINT NOT NULL,
        periods SMALLINT NOT NULL, events INTEGER NOT NULL, score_ok BOOLEAN NOT NULL, swing REAL NOT NULL,
        lead_changes SMALLINT NOT NULL, ties SMALLINT NOT NULL, largest_lead SMALLINT NOT NULL, comeback SMALLINT NOT NULL,
        win_min_wp REAL NOT NULL, final_margin SMALLINT NOT NULL, excitement REAL NOT NULL, peak_dwp REAL NOT NULL,
        peak_event_id INTEGER, peak_t REAL)""")
    copy_rows(cur, "best_games", out, ["game_id", "nba_game_id"] + cols)
    cur.execute("CREATE INDEX best_games_season ON best_games (season, excitement DESC);")
    cur.execute("CREATE INDEX best_games_date ON best_games (game_date);")
    cur.execute("CREATE TABLE best_games_meta (name TEXT PRIMARY KEY, value DOUBLE PRECISION NOT NULL, note TEXT NOT NULL)")
    copy_rows(cur, "best_games_meta", meta, ["name", "value", "note"])
    conn.commit()
    cur.execute("ANALYZE best_games;")
    conn.commit()
    for t in ("best_games", "best_games_meta"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t};")
        n, size = cur.fetchone()
        print(f"{t}: {n:,} rows, {size}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
