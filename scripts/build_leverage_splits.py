"""
build_leverage_splits.py
=========================
Garbage-Time Deflator: re-weights every real player's real production by
how much the game was actually on the line when it happened, using the
project's own trained win-probability model (wpa_lib.py) over real
play-by-play.

Data: pbp_events / pbp_games, ESPN source only (sportsdataverse, real full
seasons). The small nba_api sample is deliberately excluded here: all 420
of its games are the SAME real games as ESPN rows (418 by date + teams; the
other 2 are neutral-site games with a NULL nba_api home_team, same date and
final score — see wpa_lib.PBP_DEDUP_WHERE), so combining the two would
double-count those games' production. The 3 real NBA Cup finals are excluded too (see
NBA_CUP_FINALS). No new data is fetched.

Method (all thresholds documented in the API response too):
  * Game state for each real play = (seconds remaining at the play, score
    margin BEFORE the play). Win probability = the deployed WPA model.
    Seconds remaining come from the corrected clock (pbp_event_clock,
    build_event_clock.py; rerun this after it) since 2026-10-06 (round 8
    step 7), like Clutch WPA: ESPN stamps made shots a median 14 s late.
    Measured before switching (--clock espn --dry-run reproduces the old
    tables exactly; --dry-run compares): filtered PPG moved in 501 of 2,111
    qualified player-seasons at one decimal (max 0.31), garbage share by up
    to 4.9 points, 4 padding badges; clutch plays already decided 17% -> 16%.
  * Leverage Index (LI), Tango-style: the EXPECTED absolute
    win-probability swing of the next event at a game state,
        E|dWP| = sum_k P(k) * |WP(t, margin_before + k) - WP(t, margin_before)|
    over the real scoring outcomes k (home +1/+2/+3, away +1/+2/+3, or no
    score), with P(k) the real league-wide frequency of each outcome per
    event across every real event in the table. Divided by its real
    event-weighted league average, so the average real event has LI = 1.0
    exactly. Computed directly per state (no sparse bins to smooth), so a
    rare state like a 10-point lead late in overtime still gets the
    near-zero leverage the model says it has. leverage_index_grid stores
    the same formula on a regular (minute of regulation, margin) lattice
    for display (time_bin = minutes remaining in regulation, 0-47).
  * Buckets, evaluated in this order (first match wins):
        garbage : pre-play WP > 99% for either team, OR 2nd half/OT with
                  |margin| >= 18
        high    : LI >= 1.5, OR NBA clutch time (last 5 min of the 4th/OT,
                  |margin| <= 5)
        low     : LI < 0.3
        medium  : everything else
    Garbage is checked before clutch on purpose: a 5-point lead with a few
    seconds left meets the NBA's clutch definition but is a >99% decided
    game. How many real clutch events that affects is stored and shown.
  * Real stat attribution per event: points (made FG = the real score
    change when it is 2 or 3, which fixes ESPN's step-back/pull-up threes
    that omit "three point" from the text; description fallback
    otherwise; made FT = 1), FGA/FGM, 3PA (made: real score change;
    missed: "three point" in text or listed distance >= 23 ft — accuracy
    of that rule measured on real made shots and stored), FTA/FTM,
    rebounds (not team rebounds), turnovers, assists (parsed from
    "(Name assists)" and matched to a real player id by exact name within
    the same real game). Only events with a real matched player id count;
    the real attribution rates are stored per season.

Validation (stored in leverage_validation): real rebuilt PPG vs. the real
official per-game PPG in player_season_stats (Pearson r, mean absolute
error) for players with >= 40 real games.

Usage:
    cd scripts && python3 build_leverage_splits.py [--clock espn] [--dry-run]
"""

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from wpa_lib import CLUTCH_MARGIN, CLUTCH_SECONDS, load_model

GARBAGE_WP = 0.99
GARBAGE_MARGIN_2H = 18
HIGH_LI = 1.5
LOW_LI = 0.3
MIN_GAMES_QUALIFIED = 40
PADDING_PCTILE = 90
MARGIN_CLIP = 25
THREE_DISTANCE_FT = 23
BUCKETS = ["garbage", "low", "medium", "high"]

EXCLUDED_ACTION_TYPES = {"Challenge", "No Shot (Default Shot)"}

# Real NBA Cup championship games. ESPN labels them regular season, but the
# NBA does not count the Cup final in official regular-season stats (checked:
# every player with an extra real game vs. player_season_stats in 2024/2025
# played in exactly one of these). Excluded so per-game numbers line up with
# official regular-season lines.
NBA_CUP_FINALS = {
    "espn_401607495",  # 2023-12-09 LAL vs IND
    "espn_401734908",  # 2024-12-17 OKC vs MIL
    "espn_401809839",  # 2025-12-16 NYK vs SAS
}


def load_events(conn, clock="corrected"):
    """clock='corrected' (default since 2026-10-06): seconds remaining from pbp_event_clock
    (build_event_clock.py), like Clutch WPA; 'espn' = ESPN's own stamps (made shots a median
    14 s late), kept for the comparison in --dry-run. Every ESPN event has a pbp_event_clock row;
    one without (none today) keeps its own time."""
    secs = "COALESCE(k.seconds_remaining, e.seconds_remaining)" if clock == "corrected" else "e.seconds_remaining"
    query = f"""
        SELECT e.game_id, e.action_number, e.id, e.period, {secs} AS seconds_remaining,
               e.score_home, e.score_away, e.team_tricode, e.person_id, e.player_name,
               e.action_type, e.description, g.home_team, g.season, g.game_date
        FROM pbp_events e
        JOIN pbp_games g ON g.game_id = e.game_id
        LEFT JOIN pbp_event_clock k ON k.event_id = e.id
        WHERE g.source = 'espn' AND g.game_id <> ALL(%s)
        ORDER BY g.game_date, e.game_id, e.action_number, e.id;
    """
    with conn.cursor() as cur:
        cur.execute(query, (list(NBA_CUP_FINALS),))
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def win_probs(model, scaler, secs, margin):
    """Vectorized wpa_lib.win_prob() — identical math to compute_wpa.py."""
    secs = np.clip(secs.astype(float), 0, None)
    margin = margin.astype(float)
    X = scaler.transform(np.column_stack([secs, margin, margin / np.sqrt(secs + 1)]))
    return model.predict_proba(X)[:, 1]


def classify(df):
    at = df["action_type"].fillna("")
    desc = df["description"].fillna("")
    low = desc.str.lower()
    valid = ~at.isin(EXCLUDED_ACTION_TYPES)

    is_ft = valid & at.str.startswith("Free Throw")
    makes = low.str.contains(r"\bmakes\b")
    misses = low.str.contains(r"\bmisses\b")
    blocked = low.str.contains(r"\bblocks\b")
    is_fga = valid & ~is_ft & (makes | misses | blocked)
    is_fgm = is_fga & makes & ~blocked

    is_home = df["team_tricode"] == df["home_team"]
    d_home = df.groupby("game_id")["score_home"].diff().fillna(df["score_home"])
    d_away = df.groupby("game_id")["score_away"].diff().fillna(df["score_away"])
    own_delta = np.where(is_home, d_home, d_away)

    distance = desc.str.extract(r"(\d+)-foot")[0].astype(float)
    text_three = low.str.contains("three point") | (distance >= THREE_DISTANCE_FT)
    real_delta_ok = np.isin(own_delta, [2, 3])

    fg_pts = np.where(real_delta_ok, own_delta, np.where(text_three, 3, 2))
    df["pts"] = np.where(is_fgm, fg_pts, 0) + np.where(is_ft & makes, 1, 0)
    df["fga"] = is_fga.astype(int)
    df["fgm"] = is_fgm.astype(int)
    df["fg3m"] = (is_fgm & (df["pts"] == 3)).astype(int)
    df["fg3a"] = (df["fg3m"].astype(bool) | (is_fga & ~is_fgm & text_three)).astype(int)
    df["fta"] = is_ft.astype(int)
    df["ftm"] = (is_ft & makes).astype(int)
    df["reb"] = (valid & at.str.contains("Rebound") & ~low.str.contains("team rebound")).astype(int)
    df["tov"] = (valid & at.str.contains("Turnover")).astype(int)
    df["assister_name"] = desc.str.extract(r"\(([^()]+?) assists\)")[0]

    # Real accuracy of the text/distance 3PA rule, measured on real made
    # shots where the real score change gives the true answer.
    truth = is_fgm & real_delta_ok
    three_rule_accuracy = float(((text_three == (own_delta == 3))[truth]).mean())
    return df, three_rule_accuracy


def expected_swing(model, scaler, secs, margin_before, outcome_probs):
    """E|dWP| at each state over the real per-event scoring-outcome mix."""
    base = win_probs(model, scaler, secs, margin_before)
    total = np.zeros_like(base)
    for k, p in outcome_probs.items():
        if k == 0 or p == 0:
            continue
        total += p * np.abs(win_probs(model, scaler, secs, margin_before + k) - base)
    return base, total


def compute(conn, clock="corrected"):
    """Everything the build writes, computed without writing: (df, grid, splits, summary, validation, accuracy)."""
    model, scaler = load_model()
    cur = conn.cursor()

    print(f"Loading real ESPN play-by-play (one bulk query; clock = {clock})...")
    df = load_events(conn, clock)
    print(f"  {len(df):,} real events, {df['game_id'].nunique():,} real games, "
          f"seasons {df['season'].min()}-{df['season'].max()}")

    df, three_rule_accuracy = classify(df)
    print(f"  3PA text/distance rule accuracy on real made shots: {three_rule_accuracy:.4f}")

    df["margin"] = (df["score_home"] - df["score_away"]).astype(float)
    df["margin_before"] = df.groupby("game_id")["margin"].shift(1).fillna(0.0)
    change = (df["margin"] - df["margin_before"]).clip(-3, 3).astype(int)
    outcome_probs = change.value_counts(normalize=True).to_dict()
    print("  Real per-event scoring-outcome mix (home-perspective margin change): "
          + ", ".join(f"{k:+d}: {v:.4f}" for k, v in sorted(outcome_probs.items())))

    secs = df["seconds_remaining"].astype(float).values
    wp_before, swing = expected_swing(model, scaler, secs, df["margin_before"].values, outcome_probs)
    df["wp_before"] = wp_before
    league_mean_swing = float(swing.mean())
    df["li"] = swing / league_mean_swing
    print(f"  Real event-weighted mean LI: {df['li'].mean():.4f}")

    # Display lattice: one row per real minute of regulation (seconds
    # remaining 30, 90, ... 2850) x margin -25..25. Overtime needs no rows of
    # its own: the WPA model's only inputs are seconds remaining and margin,
    # and OT's clock restarts, so an OT state with t seconds left is the
    # same model input as regulation with t seconds left.
    lattice = [(tb, m, tb * 60 + 30) for tb in range(48) for m in range(-MARGIN_CLIP, MARGIN_CLIP + 1)]
    lat = np.array(lattice, dtype=float)
    _, lat_swing = expected_swing(model, scaler, lat[:, 2], lat[:, 1], outcome_probs)
    grid = pd.DataFrame({"tbin": lat[:, 0].astype(int), "mbin": lat[:, 1].astype(int),
                         "mean_swing": lat_swing, "li": lat_swing / league_mean_swing})

    abs_mb = df["margin_before"].abs()
    garbage = (df["wp_before"] > GARBAGE_WP) | (df["wp_before"] < 1 - GARBAGE_WP) | (
        (df["period"] >= 3) & (abs_mb >= GARBAGE_MARGIN_2H)
    )
    clutch = (df["period"] >= 4) & (df["seconds_remaining"] <= CLUTCH_SECONDS) & (abs_mb <= CLUTCH_MARGIN)
    high = (df["li"] >= HIGH_LI) | clutch
    lowb = df["li"] < LOW_LI
    df["bucket"] = np.select([garbage, high, lowb], ["garbage", "high", "low"], default="medium")
    df["clutch_overridden"] = clutch & garbage

    # Assists: real assister name -> real person_id, exact match within the same real game.
    names = df.dropna(subset=["person_id", "player_name"])[["game_id", "player_name", "person_id"]]
    names = names.drop_duplicates(["game_id", "player_name"])
    ast = df[df["assister_name"].notna()][["game_id", "season", "assister_name", "bucket", "li"]]
    ast = ast.merge(
        names.rename(columns={"player_name": "assister_name"}), on=["game_id", "assister_name"], how="left"
    )

    attributed = df[df["person_id"].notna()].copy()
    attributed["person_id"] = attributed["person_id"].astype(np.int64)
    attributed["lw_pts"] = attributed["pts"] * attributed["li"]

    # ---- per-season validation / attribution rates ----
    validation = {}
    for season, sdf in df.groupby("season"):
        final = sdf.groupby("game_id")[["score_home", "score_away"]].last()
        sast = ast[ast["season"] == season]
        validation[season] = {
            "n_games": int(sdf["game_id"].nunique()),
            "n_events": int(len(sdf)),
            "pts_total": int(final.sum().sum()),
            "pts_attributed": int(sdf.loc[sdf["person_id"].notna(), "pts"].sum()),
            "assists_parsed": int(len(sast)),
            "assists_matched": int(sast["person_id"].notna().sum()),
            "clutch_events": int(clutch[sdf.index].sum()),
            "clutch_overridden": int(sdf["clutch_overridden"].sum()),
            "bucket_event_share": sdf["bucket"].value_counts(normalize=True).to_dict(),
        }

    # ---- per player-season-bucket splits ----
    stat_cols = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "reb", "tov", "lw_pts"]
    splits = attributed.groupby(["season", "person_id", "bucket"])[stat_cols].sum()
    ast_m = ast[ast["person_id"].notna()].copy()
    ast_m["person_id"] = ast_m["person_id"].astype(np.int64)
    ast_counts = ast_m.groupby(["season", "person_id", "bucket"]).size().rename("ast")
    splits = splits.join(ast_counts, how="outer").fillna(0).reset_index()

    games = pd.concat([
        attributed[["season", "person_id", "game_id"]],
        ast_m[["season", "person_id", "game_id"]],
    ]).drop_duplicates().groupby(["season", "person_id"]).size().rename("games")
    last_team = attributed.groupby(["season", "person_id"])["team_tricode"].last().rename("team")
    last_name = attributed.groupby(["season", "person_id"])["player_name"].last().rename("pbp_name")

    wide = splits.pivot_table(index=["season", "person_id"], columns="bucket", values="pts", fill_value=0)
    for b in BUCKETS:
        if b not in wide.columns:
            wide[b] = 0
    summary = wide[BUCKETS].rename(columns={b: f"{b}_pts" for b in BUCKETS})
    summary["pts"] = summary[[f"{b}_pts" for b in BUCKETS]].sum(axis=1)
    summary["lw_pts"] = splits.groupby(["season", "person_id"])["lw_pts"].sum()
    summary = summary.join(games).join(last_team).join(last_name).reset_index()
    summary = summary[summary["games"] > 0]

    cur.execute("SELECT player_id, season, player_name, pts, gp FROM player_season_stats;")
    pss = pd.DataFrame(cur.fetchall(), columns=["person_id", "season", "full_name", "pss_ppg", "pss_gp"])
    summary = summary.merge(pss, on=["person_id", "season"], how="left")
    summary["player_name"] = summary["full_name"].fillna(summary["pbp_name"])

    g = summary["games"]
    summary["ppg_raw"] = summary["pts"] / g
    summary["ppg_ex_garbage"] = (summary["pts"] - summary["garbage_pts"]) / g
    summary["ppg_filtered"] = (summary["pts"] - summary["garbage_pts"] - summary["low_pts"]) / g
    summary["lw_ppg"] = summary["lw_pts"] / g
    has_pts = summary["pts"] > 0
    summary["true_production_ratio"] = np.where(has_pts, summary["ppg_filtered"] / summary["ppg_raw"].where(has_pts, 1), np.nan)
    summary["garbage_share"] = np.where(has_pts, summary["garbage_pts"] / summary["pts"].where(has_pts, 1), np.nan)
    summary["high_share"] = np.where(has_pts, summary["high_pts"] / summary["pts"].where(has_pts, 1), np.nan)
    summary["qualified"] = summary["games"] >= MIN_GAMES_QUALIFIED
    summary["garbage_share_pctile"] = np.nan
    for season, idx in summary[summary["qualified"]].groupby("season").groups.items():
        summary.loc[idx, "garbage_share_pctile"] = summary.loc[idx, "garbage_share"].rank(pct=True, method="max") * 100
    summary["padding_risk"] = summary["qualified"] & (summary["garbage_share_pctile"] >= PADDING_PCTILE)

    for season in validation:
        q = summary[(summary["season"] == season) & summary["qualified"] & summary["pss_ppg"].notna()]
        validation[season]["n_players_compared"] = int(len(q))
        validation[season]["n_qualified"] = int((summary["qualified"] & (summary["season"] == season)).sum())
        validation[season]["ppg_r"] = float(np.corrcoef(q["ppg_raw"], q["pss_ppg"])[0, 1]) if len(q) > 2 else None
        validation[season]["ppg_mae"] = float((q["ppg_raw"] - q["pss_ppg"]).abs().mean()) if len(q) else None

    return df, grid, splits, summary, validation, three_rule_accuracy


def compare(conn, new):
    """--dry-run: how the per-player numbers move against the stored tables (built on whichever clock they were)."""
    _df, _grid, splits, summary, validation, _acc = new
    old = pd.read_sql("SELECT * FROM player_leverage_summary", conn)
    m = old.merge(summary.rename(columns={"person_id": "player_id"}), on=["season", "player_id"], suffixes=("_old", ""))
    q = m[m["qualified"]]
    print(f"\nPlayer-seasons: stored {len(old):,}, new {len(summary):,}, both {len(m):,} ({len(q):,} qualified)")
    for col, scale, unit in (("ppg_filtered", 1, "ppg"), ("ppg_ex_garbage", 1, "ppg"), ("lw_ppg", 1, "ppg"),
                             ("garbage_share", 100, "pts of share"), ("high_share", 100, "pts of share"),
                             ("true_production_ratio", 100, "pts of ratio")):
        d = (q[col] - q[f"{col}_old"]) * scale
        print(f"  {col:<22} qualified: mean |change| {d.abs().mean():.3f} {unit}, max {d.abs().max():.3f}, "
              f"changed at shown precision (1 dp) {int((d.abs() >= 0.05).sum()):,} of {len(q):,}")
    flips = int((q["padding_risk"] != q["padding_risk_old"]).sum())
    print(f"  padding badge changes: {flips}")
    oldv = pd.read_sql("SELECT * FROM leverage_validation ORDER BY season", conn).set_index("season")
    for s, v in sorted(validation.items()):
        o = oldv.loc[s]
        sh = v["bucket_event_share"]
        print(f"  {s}: clutch events {o.clutch_events} -> {v['clutch_events']}, overridden {o.clutch_overridden} -> "
              f"{v['clutch_overridden']}, garbage share {o.share_garbage:.4f} -> {sh.get('garbage', 0):.4f}, "
              f"high {o.share_high:.4f} -> {sh.get('high', 0):.4f}")
    big = q.assign(d=(q["ppg_filtered"] - q["ppg_filtered_old"]).abs()).nlargest(10, "d")
    print("\nBiggest filtered-PPG moves (qualified):")
    print(big[["season", "player_name", "ppg_raw", "ppg_filtered_old", "ppg_filtered", "garbage_share_old",
               "garbage_share"]].to_string(index=False))
    return m


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Garbage-Time Deflator")
    ap.add_argument("--clock", choices=("corrected", "espn"), default="corrected")
    ap.add_argument("--dry-run", action="store_true", help="compute and compare with the stored tables; write nothing")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    res = compute(conn, args.clock)
    if args.dry_run:
        compare(conn, res)
        conn.close()
        return
    df, grid, splits, summary, validation, three_rule_accuracy = res
    cur = conn.cursor()

    # ---- write tables ----
    cur.execute("""
        DROP TABLE IF EXISTS leverage_index_grid;
        CREATE TABLE leverage_index_grid (
            time_bin INTEGER NOT NULL,
            margin_before INTEGER NOT NULL,
            expected_swing DOUBLE PRECISION NOT NULL,
            li DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (time_bin, margin_before)
        );
        DROP TABLE IF EXISTS player_leverage_splits;
        CREATE TABLE player_leverage_splits (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            bucket TEXT NOT NULL,
            pts INTEGER, fgm INTEGER, fga INTEGER, fg3m INTEGER, fg3a INTEGER,
            ftm INTEGER, fta INTEGER, reb INTEGER, ast INTEGER, tov INTEGER,
            lw_pts DOUBLE PRECISION,
            PRIMARY KEY (season, player_id, bucket)
        );
        DROP TABLE IF EXISTS player_leverage_summary;
        CREATE TABLE player_leverage_summary (
            season INTEGER NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            games INTEGER NOT NULL,
            pts INTEGER NOT NULL,
            garbage_pts INTEGER, low_pts INTEGER, medium_pts INTEGER, high_pts INTEGER,
            ppg_raw DOUBLE PRECISION,
            ppg_ex_garbage DOUBLE PRECISION,
            ppg_filtered DOUBLE PRECISION,
            lw_ppg DOUBLE PRECISION,
            true_production_ratio DOUBLE PRECISION,
            garbage_share DOUBLE PRECISION,
            high_share DOUBLE PRECISION,
            qualified BOOLEAN NOT NULL,
            garbage_share_pctile DOUBLE PRECISION,
            padding_risk BOOLEAN NOT NULL,
            official_ppg DOUBLE PRECISION,
            official_gp INTEGER,
            PRIMARY KEY (season, player_id)
        );
        DROP TABLE IF EXISTS leverage_validation;
        CREATE TABLE leverage_validation (
            season INTEGER PRIMARY KEY,
            n_games INTEGER, n_events INTEGER,
            pts_total INTEGER, pts_attributed INTEGER,
            assists_parsed INTEGER, assists_matched INTEGER,
            clutch_events INTEGER, clutch_overridden INTEGER,
            share_garbage DOUBLE PRECISION, share_low DOUBLE PRECISION,
            share_medium DOUBLE PRECISION, share_high DOUBLE PRECISION,
            n_qualified INTEGER, n_players_compared INTEGER,
            ppg_r DOUBLE PRECISION, ppg_mae DOUBLE PRECISION,
            three_rule_accuracy DOUBLE PRECISION
        );
    """)

    psycopg2.extras.execute_values(cur, "INSERT INTO leverage_index_grid VALUES %s", [
        (int(r.tbin), int(r.mbin), float(r.mean_swing), float(r.li)) for _, r in grid.iterrows()
    ])
    psycopg2.extras.execute_values(cur, "INSERT INTO player_leverage_splits VALUES %s", [
        (int(r.season), int(r.person_id), r.bucket, int(r.pts), int(r.fgm), int(r.fga), int(r.fg3m),
         int(r.fg3a), int(r.ftm), int(r.fta), int(r.reb), int(r.ast), int(r.tov), float(r.lw_pts))
        for _, r in splits.iterrows()
    ])

    def f(x):
        return None if pd.isna(x) else float(x)

    psycopg2.extras.execute_values(cur, "INSERT INTO player_leverage_summary VALUES %s", [
        (int(r.season), int(r.person_id), r.player_name, r.team, int(r.games), int(r.pts),
         int(r.garbage_pts), int(r.low_pts), int(r.medium_pts), int(r.high_pts),
         f(r.ppg_raw), f(r.ppg_ex_garbage), f(r.ppg_filtered), f(r.lw_ppg), f(r.true_production_ratio),
         f(r.garbage_share), f(r.high_share), bool(r.qualified), f(r.garbage_share_pctile),
         bool(r.padding_risk), f(r.pss_ppg), None if pd.isna(r.pss_gp) else int(r.pss_gp))
        for _, r in summary.iterrows()
    ])
    psycopg2.extras.execute_values(cur, "INSERT INTO leverage_validation VALUES %s", [
        (int(s), v["n_games"], v["n_events"], v["pts_total"], v["pts_attributed"], v["assists_parsed"],
         v["assists_matched"], v["clutch_events"], v["clutch_overridden"],
         *[float(v["bucket_event_share"].get(b, 0.0)) for b in BUCKETS],
         v["n_qualified"], v["n_players_compared"], v["ppg_r"], v["ppg_mae"], three_rule_accuracy)
        for s, v in validation.items()
    ])
    conn.commit()

    print("\nPer-season real validation:")
    for s, v in sorted(validation.items()):
        print(f"  {s}: games={v['n_games']} pts attributed={v['pts_attributed'] / v['pts_total']:.3f} "
              f"assists matched={v['assists_matched'] / max(v['assists_parsed'], 1):.3f} "
              f"PPG r={v['ppg_r']:.4f} MAE={v['ppg_mae']:.3f} (n={v['n_players_compared']}) "
              f"clutch overridden by garbage={v['clutch_overridden']}/{v['clutch_events']} "
              f"buckets={ {k: round(x, 3) for k, x in v['bucket_event_share'].items()} }")

    latest = int(summary["season"].max())
    q = summary[(summary["season"] == latest) & summary["qualified"]]
    print(f"\n{latest} top garbage-time share (qualified):")
    print(q.nlargest(10, "garbage_share")[["player_name", "games", "ppg_raw", "ppg_filtered", "garbage_share", "garbage_share_pctile"]].to_string())
    print(f"\n{latest} top scorers raw -> filtered:")
    print(q.nlargest(15, "ppg_raw")[["player_name", "ppg_raw", "ppg_filtered", "true_production_ratio", "lw_ppg", "high_share"]].to_string())
    conn.close()


if __name__ == "__main__":
    main()
