"""
fetch_season_shots.py
=====================
Re-fetches one season of the NBA shot chart (every field-goal attempt with its court coordinates) from
stats.nba.com's ShotChartDetail through nba_api, one call per team and season type (30 teams x regular
season, playoffs, play-in = 90 calls, 1-2 s each), stages it, checks it, and with --apply merges it into
player_shots (round 8.5 step C, R8-028).

Why it exists: player_shots comes from bulk play-by-play CSVs (load_pbp_shots.py). The 2025-26 file was
downloaded on 2026-04-17, before the playoffs and play-in, and four games of 2025-11-19/20 (0022500259-61,
0022500265) have no shot rows in it at all.

    python3 fetch_season_shots.py --season 2026            # fetch (cached) -> staging table -> checks; player_shots untouched
    python3 fetch_season_shots.py --season 2026 --apply    # same, then merge into player_shots, drop the staging table
    python3 fetch_season_shots.py --season 2026 --fetch-only   # fill the cache only (rebuild_all.sh's fetch stage)
    options: --offline (cache only; stops if a file is missing)  --refresh (re-fetch cached files)
             --types regular,playoffs,playin  --cache DIR  --sleep S (between calls, default 1.0)

Cache: one CSV per team and season type, the raw ShotChartDetail rows, in shots_data/shotchart_detail/<season>/
(gitignored, like the bulk CSVs). A cached file is never fetched again unless --refresh, so a rerun is offline
and repeatable, and an interrupted fetch resumes where it stopped. Calls are polite: one at a time, a pause
between them, up to 4 tries with growing waits.

Staging table: zz_stage.shotchart_<season> (schema zz_stage, outside `public`, so paper_manifest.py never sees
it). Without --apply it stays for inspection (`DROP SCHEMA zz_stage CASCADE` when done); --apply drops it after
merging (and the schema, once empty).

In scripts/rebuild_all.sh: the fetch stage fills the cache (--fetch-only, nbaapi) and the load stage merges from
it right after load_pbp_shots.py (--offline --apply, input tag shotchart), which reloads the bulk file's 2025-26
rows; a rerun on an already-merged table pairs every shot and writes nothing.

Merge rule (--apply; every stored shot keeps its id). A staged shot and a stored shot of the same season are
the same shot when game, shooter, period, clock (minutes, seconds), coordinates and make/miss are equal;
within equal keys they pair one-to-one in order. Paired stored rows are left untouched (id and every column;
the check below also requires their 2/3 call to agree). Stored rows of the season with no staged partner are
deleted; staged shots with no stored partner are inserted in (game, GAME_EVENT_ID) order with the stored
rows' conventions: shot_zone_basic NULL (zones come from shots_lib.classify_zone() on the coordinates, as for
every bulk row), shot_distance = the coordinate distance in feet rounded half-up (the rule every stored
non-zero distance of 2025-26 follows exactly; NBA's SHOT_DISTANCE is the same distance rounded down, and
the bulk file's zero distances on threes are its own error, not copied), player_name from nba_api's static
list (as load_pbp_shots.py), else the chart's name. New shooters get a 'done' row in
player_shots_cache_status (existing rows untouched). Only rows of --season are ever touched.

Checks printed before any write (and the merge stops unless the hard ones pass):
  * no duplicate (GAME_ID, GAME_EVENT_ID); every row an attempt; one season, the requested one
  * regular-season games = game_scores' games of the season; playoff + play-in games = postseason_games'
  * per game, the chart's FGA vs player_game_lines' FGA (both teams, the ESPN play-by-play lines)
  * per player-season (20+ games), regular-season chart FGA vs player_season_stats FGA x GP within the
    per-game rounding (0.05 x GP + 2), as api/tests/test_consistency.py
  * the merge preview: paired / stored-only / staged-only, per season type
"""

import argparse
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TYPES = {"regular": ("Regular Season", "002"), "playoffs": ("Playoffs", "004"), "playin": ("PlayIn", "005")}
STAGE_SCHEMA = "zz_stage"
KEY = ["game_id", "player_id", "period", "minutes_remaining", "seconds_remaining", "loc_x", "loc_y", "shot_made_flag"]
INSERT_COLUMNS = ["player_id", "player_name", "season", "game_id", "loc_x", "loc_y", "shot_made_flag", "shot_type",
                  "shot_distance", "period", "minutes_remaining", "seconds_remaining", "shot_zone_basic"]


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def teams():
    from nba_api.stats.static import teams as static_teams
    return sorted((t["id"], t["abbreviation"]) for t in static_teams.get_teams())


def fetch_one(team_id, season, kind, tries=4):
    from nba_api.stats.endpoints import shotchartdetail
    wait = 5.0
    for attempt in range(1, tries + 1):
        try:
            df = shotchartdetail.ShotChartDetail(
                team_id=team_id, player_id=0, season_nullable=season_label(season),
                season_type_all_star=TYPES[kind][0], context_measure_simple="FGA", timeout=30,
            ).get_data_frames()[0]
            return df
        except Exception as e:  # network or a throttled answer: wait and try again
            if attempt == tries:
                raise
            print(f"    {team_id} {kind}: {type(e).__name__}, retry {attempt} in {wait:.0f} s", flush=True)
            time.sleep(wait)
            wait *= 2


def fetch_all(season, kinds, cache, offline, refresh, pause):
    os.makedirs(cache, exist_ok=True)
    frames, calls = [], 0
    for team_id, abbr in teams():
        for kind in kinds:
            path = os.path.join(cache, f"{abbr}_{kind}.csv")
            if os.path.exists(path) and not refresh:
                df = pd.read_csv(path, dtype={"GAME_ID": str, "GAME_DATE": str})
            elif offline:
                raise SystemExit(f"--offline: {path} is not cached")
            else:
                if calls:
                    time.sleep(pause)
                t0 = time.time()
                df = fetch_one(team_id, season, kind)
                calls += 1
                df.to_csv(path, index=False)
                df = pd.read_csv(path, dtype={"GAME_ID": str, "GAME_DATE": str})  # same types as a cached read
                print(f"  {abbr:3s} {kind:8s} {len(df):5d} shots in {time.time() - t0:.1f} s", flush=True)
            if len(df):
                df["GAME_ID"] = df["GAME_ID"].str.zfill(10)
                df["kind"] = kind
                frames.append(df)
    print(f"{calls} calls made, {len(teams()) * len(kinds) - calls} files read from {cache}")
    return pd.concat(frames, ignore_index=True)


def to_stage(df, season):
    from nba_api.stats.static import players as static_players
    names = {p["id"]: p["full_name"] for p in static_players.get_players()}
    out = pd.DataFrame({
        "game_id": df.GAME_ID, "game_event_id": df.GAME_EVENT_ID.astype(int), "kind": df.kind,
        "player_id": df.PLAYER_ID.astype(int), "chart_name": df.PLAYER_NAME, "team_id": df.TEAM_ID.astype(int),
        "period": df.PERIOD.astype(int), "minutes_remaining": df.MINUTES_REMAINING.astype(int),
        "seconds_remaining": df.SECONDS_REMAINING.astype(int), "action_type": df.ACTION_TYPE,
        "shot_type": df.SHOT_TYPE, "nba_zone_basic": df.SHOT_ZONE_BASIC, "nba_distance": df.SHOT_DISTANCE.astype(int),
        "loc_x": df.LOC_X.astype(int), "loc_y": df.LOC_Y.astype(int), "attempted": df.SHOT_ATTEMPTED_FLAG.astype(int),
        "shot_made_flag": df.SHOT_MADE_FLAG.astype(int), "game_date": df.GAME_DATE.astype(str),
    })
    out["season"] = season_label(season)
    out["player_name"] = [names.get(p) or n for p, n in zip(out.player_id, out.chart_name)]
    # stored convention: distance from the coordinates (tenths of a foot) rounded half-up
    out["shot_distance"] = np.floor(np.hypot(out.loc_x, out.loc_y) / 10.0 + 0.5).astype(int)
    return out.sort_values(["game_id", "game_event_id"]).reset_index(drop=True)


def write_stage(conn, st, season):
    table = f"{STAGE_SCHEMA}.shotchart_{season}"
    cols = list(st.columns)
    types = {c: "integer" for c in cols}
    types.update({c: "text" for c in ["game_id", "kind", "chart_name", "action_type", "shot_type", "nba_zone_basic",
                                       "game_date", "season", "player_name"]})
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {STAGE_SCHEMA}")
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE TABLE {table} ({', '.join(f'{c} {types[c]}' for c in cols)})")
        execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s",
                       [tuple(None if pd.isna(v) else (v.item() if hasattr(v, 'item') else v) for v in r)
                        for r in st.itertuples(index=False)], page_size=5000)
    conn.commit()
    return table


def stored(conn, season):
    sd = pd.read_sql_query(f"SELECT id, {', '.join(KEY)}, shot_type FROM player_shots WHERE season = %s ORDER BY id",
                           conn, params=(season_label(season),))
    if sd[KEY[1:]].isna().any().any():
        raise SystemExit("stored rows of the season with a NULL key column: the merge rule can't pair them")
    return sd.astype({c: int for c in KEY[1:]})


def pair(st, sd):
    """Pair staged and stored shots one-to-one on KEY (in order within equal keys). Returns the merged pairs, the
    stored rows with no partner and the staged rows with no partner."""
    a = st.assign(k=st.groupby(KEY).cumcount())
    b = sd.assign(k=sd.groupby(KEY).cumcount())
    m = a.merge(b, on=KEY + ["k"], how="outer", suffixes=("", "_stored"), indicator=True)
    return m[m._merge == "both"], m[m._merge == "right_only"], m[m._merge == "left_only"]


def checks(conn, st, season):
    """Print every check; return the list of hard failures (the merge refuses to run while any is left)."""
    hard = []
    lab = season_label(season)
    dup = st.duplicated(["game_id", "game_event_id"]).sum()
    print(f"\nstaged {len(st):,} shots in {st.game_id.nunique():,} games, {st.player_id.nunique()} shooters; "
          f"duplicate (game, event) {dup}; not attempts {(st.attempted != 1).sum()}")
    if dup or (st.attempted != 1).any():
        hard.append("duplicate events or non-attempts in the chart")
    for kind, (_, prefix) in TYPES.items():
        bad = st[(st.kind == kind) & ~st.game_id.str.startswith(prefix)]
        if len(bad):
            hard.append(f"{len(bad)} {kind} rows with a game id outside {prefix}")
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT game_id FROM game_scores WHERE season = %s", (season,))
        sched = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT count(*) FROM postseason_games WHERE season = %s", (season,))
        n_post = cur.fetchone()[0]
    reg = set(st.loc[st.kind == "regular", "game_id"])
    post = st.loc[st.kind != "regular", "game_id"].nunique()
    print(f"regular-season games: chart {len(reg):,}, game_scores {len(sched):,}, missing {sorted(sched - reg)[:8]}, "
          f"extra {sorted(reg - sched)[:8]}; playoff + play-in games: chart {post}, postseason_games {n_post}")
    if reg != sched:
        hard.append("regular-season games differ from game_scores")
    if n_post and post != n_post:
        hard.append("playoff + play-in game count differs from postseason_games")

    # per game: chart FGA vs the play-by-play lines' FGA (regular season; the lines cover it from 2020-21)
    lines = pd.read_sql_query(
        """SELECT g.game_id, SUM(l.fga)::int AS lines_fga FROM player_game_lines l
           JOIN (SELECT DISTINCT game_id, espn_id FROM game_scores WHERE season = %s) g ON 'espn_' || g.espn_id = l.game_id
           GROUP BY 1""", conn, params=(season,))
    old = pd.read_sql_query("SELECT game_id, count(*)::int AS stored_fga FROM player_shots WHERE season = %s GROUP BY 1",
                            conn, params=(lab,))
    per = (st[st.kind == "regular"].groupby("game_id").size().rename("chart_fga").reset_index()
           .merge(lines, on="game_id", how="outer").merge(old, on="game_id", how="left").fillna(0))
    if len(lines):
        d = per.chart_fga - per.lines_fga
        print(f"per game vs the lines' FGA: equal {int((d == 0).sum())}, chart short {int((d < 0).sum())} "
              f"({int(-d[d < 0].sum())} FGA), chart over {int((d > 0).sum())} ({int(d[d > 0].sum())} FGA)")
        d0 = per.stored_fga - per.lines_fga
        print(f"  (the stored chart before: equal {int((d0 == 0).sum())}, short {int((d0 < 0).sum())} "
              f"({int(-d0[d0 < 0].sum())} FGA), over {int((d0 > 0).sum())} ({int(d0[d0 > 0].sum())} FGA))")

    # per player-season vs the season table
    ps = pd.read_sql_query("SELECT player_id, fga, gp FROM player_season_stats WHERE season = %s AND gp >= 20",
                           conn, params=(season,))
    fga = st[st.kind == "regular"].groupby("player_id").size().rename("chart").reset_index()
    ps = ps.merge(fga, on="player_id", how="left").fillna({"chart": 0})
    gap = (ps.chart - ps.fga * ps.gp).abs()
    off = ps[gap > 0.05 * ps.gp + 2]
    print(f"player-seasons (20+ games) outside FGA x GP's rounding: {len(off)} of {len(ps)} (worst {gap.max():.1f})")
    if len(off):
        print(off.assign(gap=gap[off.index]).head(10).to_string(index=False))

    sd = stored(conn, season)
    both, old_only, new_only = pair(st, sd)
    print(f"\nmerge preview: paired {len(both):,} (stored ids kept), stored-only {len(old_only):,} (deleted), "
          f"staged-only {len(new_only):,} (inserted)")
    for kind, (_, prefix) in TYPES.items():
        print(f"  {kind:8s} paired {int(both.game_id.str.startswith(prefix).sum()):7,d}  "
              f"stored-only {int(old_only.game_id.str.startswith(prefix).sum()):5,d}  "
              f"staged-only {int(new_only.game_id.str.startswith(prefix).sum()):6,d}")
    wrong_type = (both.shot_type != both.shot_type_stored).sum()
    print(f"paired shots whose 2/3 call differs: {wrong_type}")
    if wrong_type:
        hard.append("paired shots with a different 2/3 call")
    return hard, both, old_only, new_only


def apply_merge(conn, st, season, old_only, new_only):
    lab = season_label(season)
    keys = new_only[KEY + ["game_event_id"]].astype({c: int for c in KEY[1:] + ["game_event_id"]})
    ins = st.merge(keys, on=KEY + ["game_event_id"], how="inner") \
            .sort_values(["game_id", "game_event_id"])
    assert len(ins) == len(new_only), (len(ins), len(new_only))
    with conn.cursor() as cur:
        ids = [int(i) for i in old_only.id]
        if ids:
            cur.execute("DELETE FROM player_shots WHERE season = %s AND id = ANY(%s)", (lab, ids))
            assert cur.rowcount == len(ids)
        rows = [(int(r.player_id), r.player_name, lab, r.game_id, int(r.loc_x), int(r.loc_y), int(r.shot_made_flag),
                 r.shot_type, int(r.shot_distance), int(r.period), int(r.minutes_remaining), int(r.seconds_remaining), None)
                for r in ins.itertuples(index=False)]
        if rows:
            execute_values(cur, f"INSERT INTO player_shots ({', '.join(INSERT_COLUMNS)}) VALUES %s", rows, page_size=5000)
        new_players = sorted(set(zip(ins.player_id.astype(int), ins.player_name)))
        if new_players:
            execute_values(cur, """INSERT INTO player_shots_cache_status (player_id, player_name, status, updated_at)
                                   VALUES %s ON CONFLICT (player_id) DO NOTHING""",
                           [(p, n, "done") for p, n in new_players], template="(%s, %s, %s, NOW())")
            added = cur.rowcount
        else:
            added = 0
    conn.commit()
    print(f"applied: {len(ids):,} stored rows deleted, {len(rows):,} inserted, {added} new shooters marked done")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, required=True, help="end year (2026 = 2025-26)")
    ap.add_argument("--types", default="regular,playoffs,playin")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fetch-only", action="store_true", help="fill the cache and stop (no database)")
    a = ap.parse_args()
    kinds = [k.strip() for k in a.types.split(",") if k.strip()]
    if any(k not in TYPES for k in kinds):
        raise SystemExit(f"--types: choose from {', '.join(TYPES)}")
    cache = a.cache or os.path.join(ROOT, "shots_data", "shotchart_detail", str(a.season))
    raw = fetch_all(a.season, kinds, cache, a.offline, a.refresh, a.sleep)
    if a.fetch_only:
        print(f"{len(raw):,} shots cached in {cache}; nothing written to the database")
        return
    st = to_stage(raw, a.season)
    conn = psycopg2.connect(**DB_CONFIG)
    table = write_stage(conn, st, a.season)
    print(f"staged into {table}")
    hard, _, old_only, new_only = checks(conn, st, a.season)
    if hard:
        print("\nNOT merged: " + "; ".join(hard))
        sys.exit(1 if a.apply else 0)
    if a.apply:
        apply_merge(conn, st, a.season, old_only, new_only)
        with conn.cursor() as cur:  # the staged copy has done its job: leave no zz_ schema behind a rebuild
            cur.execute(f"DROP TABLE {table}")
            cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s",
                        (STAGE_SCHEMA,))
            if cur.fetchone()[0] == 0:
                cur.execute(f"DROP SCHEMA {STAGE_SCHEMA}")
        conn.commit()
        print(f"dropped {table}")
    else:
        print("\nchecks pass; nothing written to player_shots (run with --apply to merge)")
    conn.close()


if __name__ == "__main__":
    main()
