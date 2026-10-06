"""
build_event_clock.py
====================
One corrected game clock for every ESPN play-by-play event (pbp_events,
source 'espn', 2020-21 on), stored once so every table and endpoint that
places events in time reads the same times: pbp_event_clock.

Why: ESPN's clock is late by event type (round 6 step 3, measured against
NBA.com's own play-by-play of the 418 nba_api twin games of 2024-25): made
shots and made last free throws a median 14 s, rebounds 6 s, turnovers 5 s
(steals) or 10 s (dead ball), misses 2 s; fouls called with the clock
stopped and the first free throw of a trip are on time.
pbp_possessions.corrected_clock() rebuilds the clock from the reliable
parts (rules in its docstring): field goals matched to the NBA shot chart
take the chart's clock (~99%), free throws their trip's time, a rebound
2 s after its miss, turnovers and unmatched shots ESPN's time less the
median lag, anything else ESPN's own; derived times stay between the
anchors around them and the clock never runs backwards. Its shot-chart
match takes ~45 s, too slow for an API request, hence the table.

Columns of pbp_event_clock (one row per ESPN event, key pbp_events.id):
  period_t           seconds into the period (exactly corrected_clock()'s
                     value: what the scripts pass to Game(..., clock=));
  seconds_remaining  the same time in pbp_events' convention (regulation
                     counts down from 2880, each overtime from 300): what
                     SQL readers (Game Replay) use in place of ESPN's;
  source             chart / ft_trip / rebound / lag / espn (how it was set;
                     pbp_possessions.CLOCK_SOURCES);
  anchored           believed to the second (chart matches, trips of two or
                     three, rebounds of an anchored miss);
  bounded            moved by the between-anchors / no-going-back rule.

pbp_event_clock_meta (key / JSON value) stores the constants, the per
season breakdown by source, the check against NBA.com's play-by-play (ESPN's
lag and the corrected clock's error by event class, on all twin games and
on odd / even halves; build_possessions.py copies it into possession_meta
unchanged) and the win-probability check: Game Replay's model (wpa_lib;
fitted on ESPN's times, these games included) scored against every ESPN
game's result with ESPN's clock and with the corrected one. If the
corrected clock scores worse, the model wants a refit on it: that is left
to the owner, not done here.

Read by: pbp_lineups.load_espn(conn, clock=True) + game_clock() (scripts:
build_possessions.py, build_rotations.py, build_play_finder.py) and the
Game Replay endpoint (api/routers/wp_replay.py). player_game_lines,
lineup_stints and everything on them keep ESPN's clock on purpose (minutes
come from substitutions, at dead balls, where ESPN is on time).

Usage:
    cd scripts && python3 build_event_clock.py      (~2.5 min)
    cd scripts && python3 build_event_clock.py --season 2027
        # round 9 step 3: only that season's events are deleted and rebuilt
        # (the same per-game code; the chart matched within the season,
        # which gives the same matches); pbp_event_clock_meta is left as it
        # is (its checks cover every game: R9-008). Needs the full build's table.
Rerun after new ESPN play-by-play or a player_shots reload, then
build_possessions.py, build_rotations.py, build_play_finder.py and
build_best_games.py (they read it).
"""

import io
import json
import time
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, chart_matches, load_espn, load_season_names, period_bounds
import pbp_possessions as PP
import season_mode as SM

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

TWIN_SEASON = 2025
CLUTCH_SECONDS = 300        # wpa_lib's clutch window, for the win-probability check by game phase


def chart_clock(conn, matched, through=None, season=None):
    """{game_id: {action_number: seconds into the period}} by the NBA shot chart's clock (`through`: seasons up to that
    end year only; the paper's audit passes paper_freeze.MAX_PAPER_SEASON, round 9 step 1; `season`: that season's
    chart only, the --season build)."""
    cap = f" AND season <= '{int(through) - 1}-{str(int(through))[-2:]}'" if through else ""
    if season is not None:
        cap += f" AND season = '{SM.season_label(season)}'"
    clocks = pd.read_sql_query(
        f"""SELECT id AS nba_shot_id, period AS chart_period, minutes_remaining * 60 + seconds_remaining AS clock
           FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21'{cap}""", conn)
    m = matched[matched.nba_shot_id.notna()][["game_id", "action_number", "period", "nba_shot_id"]].copy()
    m["nba_shot_id"] = m.nba_shot_id.astype("int64")
    m = m.merge(clocks, on="nba_shot_id")
    m = m[m.period == m.chart_period]
    m["t"] = [period_bounds(int(p))[1] - c for p, c in zip(m.period, m.clock)]
    out = defaultdict(dict)
    for gid, n, t in m[["game_id", "action_number", "t"]].itertuples(index=False):
        out[gid][int(n)] = float(t)
    return out


def clock_check(conn, season_names, all_names, chart_t, grouped, through=None):
    """ESPN's lag and the corrected clock's error against NBA.com's play-by-play of the same games (the nba_api
    twins), by event class, on all twin games and on the odd / even halves separately. `through`: twins of seasons
    up to that end year only (the paper's audit passes paper_freeze.MAX_PAPER_SEASON, round 9 step 1)."""
    cap = f" AND n.season <= {int(through)}" if through else ""
    cap_g = f" AND g.season <= {int(through)}" if through else ""
    link = pd.read_sql_query(
        f"""SELECT DISTINCT n.game_id AS nba, 'espn_' || s.espn_id AS espn, g.home_team
           FROM pbp_games n JOIN game_scores s ON s.game_id = n.game_id JOIN pbp_games g ON g.game_id = 'espn_' || s.espn_id
           WHERE n.source = 'nba_api' AND s.espn_id IS NOT NULL{cap} ORDER BY 1""", conn)
    nba = pd.read_sql_query(
        f"""SELECT e.game_id, e.period, e.seconds_remaining, e.person_id, e.action_type
           FROM pbp_events e JOIN pbp_games g USING (game_id) WHERE g.source = 'nba_api' AND e.person_id IS NOT NULL{cap_g}
           AND e.action_type IN ('Made Shot', 'Missed Shot', 'Free Throw', 'Rebound', 'Turnover')
           ORDER BY e.game_id, e.action_number, e.id""", conn)
    keymap = {"Made Shot": "fg", "Missed Shot": "fg", "Free Throw": "ft", "Rebound": "reb", "Turnover": "tov"}
    nba["key"] = nba.action_type.map(keymap)
    nba["t"] = [period_bounds(int(p))[0] - s for p, s in zip(nba.period, nba.seconds_remaining)]
    nba["pid"] = nba.person_id.astype("int64")
    nba["half"] = nba.game_id.map({r.nba: i % 2 for i, r in enumerate(link.itertuples())})
    rows = []
    for i, l in enumerate(link.itertuples()):
        ev = grouped.get(l.espn)
        if ev is None:
            continue
        game = Game(l.espn, TWIN_SEASON, None, ev, season_names[TWIN_SEASON], all_names)
        game.home = l.home_team
        events = game.parse()
        corr, _ = PP.corrected_clock(events, chart_t.get(l.espn, {}))
        for e in events:
            k = e["kind"]
            if k not in PP.COUNT_KINDS or not e["pid"]:
                continue
            if k == "ft":
                kk, n, tech, _ = PP.ft_trip(e["action"])
                if tech:
                    continue
                cls = ("ft_first" if kk == 1 and n > 1 else "ft_later" if kk > 1 else "ft_single") + \
                      ("_made" if e["made"] else "_miss")
            elif k == "fg":
                cls = "fg_made" if e["made"] else "fg_miss"
            elif k == "tov":
                cls = "tov_steal" if e.get("steal") else "tov_dead"
            else:
                cls = "reb"
            key = {"fg": "fg", "ft": "ft", "oreb": "reb", "dreb": "reb", "tov": "tov"}[k]
            rows.append((l.nba, i % 2, e["period"], int(e["pid"]), key, cls, PP.espn_t(e), corr[e["action_number"]]))
    d = pd.DataFrame(rows, columns=["game_id", "half", "period", "pid", "key", "cls", "t_espn", "t_corr"])
    d["k"] = d.groupby(["game_id", "period", "pid", "key"]).cumcount()
    nba["k"] = nba.groupby(["game_id", "period", "pid", "key"]).cumcount()
    m = d.merge(nba[["game_id", "period", "pid", "key", "k", "t"]], on=["game_id", "period", "pid", "key", "k"])
    m["lag"] = m.t_espn - m.t
    m["err"] = (m.t_corr - m.t).abs()
    m["err_espn"] = m.lag.abs()
    out = {"twin_games": int(link.shape[0]), "matched_events": int(len(m)), "classes": {}}
    for cls, grp in m.groupby("cls"):
        out["classes"][cls] = {
            "n": int(len(grp)),
            "espn_lag_median": float(grp.lag.median()),
            "espn_lag_median_odd": float(grp[grp.half == 1].lag.median()),
            "espn_lag_median_even": float(grp[grp.half == 0].lag.median()),
            "espn_abs_err_median": float(grp.err_espn.median()),
            "corrected_abs_err_median": float(grp.err.median()),
            "corrected_abs_err_median_even": float(grp[grp.half == 0].err.median()),
            "espn_within_2s": round(float((grp.err_espn <= 2).mean()), 4),
            "corrected_within_2s": round(float((grp.err <= 2).mean()), 4),
            "corrected_within_2s_even": round(float((grp[grp.half == 0].err <= 2).mean()), 4),
            "espn_within_5s": round(float((grp.err_espn <= 5).mean()), 4),
            "corrected_within_5s": round(float((grp.err <= 5).mean()), 4),
        }
    out["all"] = {"espn_within_2s": round(float((m.err_espn <= 2).mean()), 4),
                  "corrected_within_2s": round(float((m.err <= 2).mean()), 4),
                  "espn_within_5s": round(float((m.err_espn <= 5).mean()), 4),
                  "corrected_within_5s": round(float((m.err <= 5).mean()), 4),
                  "espn_abs_err_median": float(m.err_espn.median()), "corrected_abs_err_median": float(m.err.median())}
    return out


def game_rows(g, ev, season_names, all_names, chart_t):
    """(event_id, period, kind, made, period_t, seconds_remaining, espn seconds_remaining, source, anchored, bounded)
    for every event of one game."""
    game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
    game.home = g.home_team                 # parse() needs the home side for score steps (walk() sets it the same way)
    events = game.parse()
    how = {}
    clock, anchored = PP.corrected_clock(events, chart_t.get(g.game_id, {}), how)
    ids = dict(zip(ev.action_number.astype(int).tolist(), ev.id.astype(int).tolist()))
    out = []
    for e in events:
        n = e["action_number"]
        t = clock[n]
        src, bounded = how[n]
        out.append((ids[n], e["period"], e["kind"], e.get("made"), t, period_bounds(e["period"])[0] - t, e["secs"],
                    src, n in anchored, bounded))
    return out


def wp_check(conn, df):
    """Game Replay's win-probability model scored on every ESPN event of a game with a known result, with ESPN's
    seconds_remaining and with the corrected one (same events, same score fields)."""
    from compute_wpa import compute_win_probs
    from wpa_lib import load_model
    sc = pd.read_sql_query(
        """SELECT e.id AS event_id, e.score_home - e.score_away AS margin, g.home_win
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' AND g.home_win IS NOT NULL AND e.score_home IS NOT NULL AND e.score_away IS NOT NULL""",
        conn)
    d = df[["event_id", "period", "secs_espn", "secs_corr"]].merge(sc, on="event_id")
    model, scaler = load_model()
    y = d.home_win.astype(int).to_numpy()
    m = d.margin.astype(float).to_numpy()
    p_espn = compute_win_probs(model, scaler, d.secs_espn.to_numpy(), m)
    p_corr = compute_win_probs(model, scaler, d.secs_corr.to_numpy(), m)
    eps = 1e-15

    def scores(mask):
        out = {"events": int(mask.sum())}
        for name, p in (("espn", p_espn), ("corrected", p_corr)):
            pp = np.clip(p[mask], eps, 1 - eps)
            yy = y[mask]
            out[f"brier_{name}"] = round(float(np.mean((pp - yy) ** 2)), 6)
            out[f"log_loss_{name}"] = round(float(-np.mean(yy * np.log(pp) + (1 - yy) * np.log(1 - pp))), 6)
            bins = np.minimum((pp * 10).astype(int), 9)
            out[f"reliability_{name}"] = [
                {"bin": b / 10, "n": int((bins == b).sum()), "mean_p": round(float(pp[bins == b].mean()), 4),
                 "observed": round(float(yy[bins == b].mean()), 4)} for b in range(10) if (bins == b).any()]
            ece = sum(abs(pp[bins == b].mean() - yy[bins == b].mean()) * (bins == b).sum() for b in range(10)
                      if (bins == b).any()) / len(pp)
            out[f"ece_{name}"] = round(float(ece), 6)
        out["mean_abs_wp_change"] = round(float(np.mean(np.abs(p_corr[mask] - p_espn[mask]))), 6)
        out["share_wp_change_over_1pt"] = round(float(np.mean(np.abs(p_corr[mask] - p_espn[mask]) > 0.01)), 4)
        return out

    late = (d.period.to_numpy() >= 4) & (d.secs_corr.to_numpy() <= CLUTCH_SECONDS)
    last_min = (d.period.to_numpy() >= 4) & (d.secs_corr.to_numpy() <= 60)
    close_late = late & (np.abs(m) <= 5)
    return {"note": "Game Replay's model (wpa_lib, fitted on ESPN's times incl. these games) on every ESPN event with "
                    "both score fields of a game with a result; phases are by the corrected clock, the same events "
                    "scored both ways",
            "all": scores(np.ones(len(d), dtype=bool)), "last_5_min": scores(late),
            "last_5_min_within_5": scores(close_late), "last_minute": scores(last_min)}


# Column order keeps rows free of alignment padding (~20% smaller than key first).
DDL = """CREATE TABLE pbp_event_clock (
    period_t DOUBLE PRECISION NOT NULL, seconds_remaining DOUBLE PRECISION NOT NULL, event_id INTEGER NOT NULL,
    anchored BOOLEAN NOT NULL, bounded BOOLEAN NOT NULL, source TEXT NOT NULL)"""
COLS = ["period_t", "seconds_remaining", "event_id", "anchored", "bounded", "source"]


def copy_rows(cur, rows):
    buf = io.StringIO()
    for r in rows:
        buf.write(f"{r[4]!r},{r[5]!r},{r[0]},{'t' if r[8] else 'f'},{'t' if r[9] else 'f'},{r[7]}\n")
    buf.seek(0)
    cur.copy_expert(f"COPY pbp_event_clock ({', '.join(COLS)}) FROM STDIN WITH (FORMAT csv)", buf)


SEASON_EVENTS_WHERE = ("event_id IN (SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id "
                       "WHERE g.source = 'espn' AND g.season = %s)")


def main():
    season = SM.parse_season()
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn, season=season)
    matched = chart_matches(conn, games, grouped, season_names, all_names, season=season)
    chart_t = chart_clock(conn, matched, season=season)
    del matched
    print(f"{len(games)} games; shot chart clock for {sum(len(v) for v in chart_t.values()):,} field goals "
          f"({time.time() - t0:.0f}s)")

    if season is None:
        cur.execute("DROP TABLE IF EXISTS pbp_event_clock; DROP TABLE IF EXISTS pbp_event_clock_meta;")
        cur.execute(DDL)
    else:
        SM.require_tables(cur, ["pbp_event_clock"], season)
        print(f"--season {season}: {SM.delete_season(cur, 'pbp_event_clock', season, SEASON_EVENTS_WHERE):,} "
              f"stored events of the season deleted")
    summary = []        # (season, period, kind, made, source, anchored, bounded, secs_espn, secs_corr, event_id)
    batch, n = [], 0
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        rows = game_rows(g, ev, season_names, all_names, chart_t)
        batch += rows
        for r in rows:
            summary.append((int(g.season), r[1], r[2] or "other", r[3], r[7], r[8], r[9], r[6], r[5], r[0]))
        if len(batch) >= 200000:
            copy_rows(cur, batch)
            n += len(batch)
            batch = []
        if i % 1500 == 0:
            print(f"  {i} games ({time.time() - t0:.0f}s)")
    copy_rows(cur, batch)
    n += len(batch)
    if season is None:
        cur.execute("ALTER TABLE pbp_event_clock ADD PRIMARY KEY (event_id);")
    cur.execute("ANALYZE pbp_event_clock;")
    print(f"wrote {n:,} events ({time.time() - t0:.0f}s)")

    df = pd.DataFrame(summary, columns=["season", "period", "kind", "made", "source", "anchored", "bounded",
                                        "secs_espn", "secs_corr", "event_id"])
    del summary
    df["shift"] = df.secs_corr - df.secs_espn       # seconds_remaining: positive = earlier in the game than ESPN said
    cur.execute("""SELECT COUNT(*) FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
                   WHERE g.source = 'espn'""" + ("" if season is None else f" AND g.season = {int(season)}"))
    espn_events = cur.fetchone()[0]
    by_season = {}
    for season, grp in df.groupby("season"):
        by_season[str(season)] = {
            "events": int(len(grp)), "sources": {k: int(v) for k, v in grp.source.value_counts().items()},
            "anchored_share": round(float(grp.anchored.mean()), 4), "bounded_share": round(float(grp.bounded.mean()), 4),
            "moved_share": round(float((grp["shift"].abs() > 1e-9).mean()), 4),
            "moved_over_2s_share": round(float((grp["shift"].abs() > 2).mean()), 4)}
    kinds = {}
    for (kind, made), grp in df.groupby([df.kind, df.made.fillna(False)]):
        key = f"{kind}_{'made' if made else 'miss'}" if kind in ("fg", "ft") else kind
        kinds[key] = {"events": int(len(grp)), "median_shift": float(grp["shift"].median()),
                      "share_moved_over_2s": round(float((grp["shift"].abs() > 2).mean()), 4)}
    print(pd.DataFrame(by_season).T.to_string())
    print(pd.DataFrame(kinds).T.to_string())
    print(f"events covered: {len(df):,} of {espn_events:,} ESPN events")
    if season is not None:
        conn.commit()
        cur.execute("SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('pbp_event_clock')) FROM pbp_event_clock")
        c, size = cur.fetchone()
        print(f"  pbp_event_clock: {c:,} rows, {size}; pbp_event_clock_meta left as it is (its checks cover every game)")
        print(f"done in {time.time() - t0:.0f}s")
        conn.close()
        return

    check = clock_check(conn, season_names, all_names, chart_t, grouped)
    print("\nAgainst NBA.com's play-by-play (twin games):", check["all"])
    wp = wp_check(conn, df)
    for k in ("all", "last_5_min", "last_5_min_within_5", "last_minute"):
        w = wp[k]
        print(f"  win probability, {k}: {w['events']:,} events; Brier ESPN {w['brier_espn']:.5f} vs corrected "
              f"{w['brier_corrected']:.5f}; log loss {w['log_loss_espn']:.5f} vs {w['log_loss_corrected']:.5f}; "
              f"ECE {w['ece_espn']:.4f} vs {w['ece_corrected']:.4f}; mean |dWP| {w['mean_abs_wp_change']:.4f}")
    meta = {
        "constants": {"lag_fg_made": PP.LAG_FG_MADE, "lag_fg_miss": PP.LAG_FG_MISS, "lag_tov_steal": PP.LAG_TOV_STEAL,
                      "lag_tov_dead": PP.LAG_TOV_DEAD, "lag_reb": PP.LAG_REB, "reb_gap": PP.REB_GAP},
        "sources": list(PP.CLOCK_SOURCES), "espn_events": espn_events, "events": int(len(df)),
        "by_season": by_season, "by_kind": kinds, "clock_check": check, "wp_check": wp,
    }
    cur.execute("CREATE TABLE pbp_event_clock_meta (key TEXT PRIMARY KEY, value JSONB NOT NULL)")
    psycopg2.extras.execute_values(cur, "INSERT INTO pbp_event_clock_meta (key, value) VALUES %s",
                                   [(k, json.dumps(v)) for k, v in meta.items()])
    conn.commit()
    for t in ("pbp_event_clock", "pbp_event_clock_meta"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        c, size = cur.fetchone()
        print(f"  {t}: {c:,} rows, {size}")
    print(f"done in {time.time() - t0:.0f}s")
    conn.close()


if __name__ == "__main__":
    main()
