"""
compute_wpa.py
===============
Runs the trained win-probability model (train_wpa_model.py) over every
real play-by-play event fetched by fetch_play_by_play.py / fetch_pbp_espn.py,
computing real Win Probability Added per play: WPA = P(win after) -
P(win before), from the perspective of whichever team's player made
that play. Aggregates each real player's total WPA and CLUTCH WPA (final
5 min of regulation/OT, real score margin within 5 points at that
moment — the NBA's own "clutch time" definition) across every real game
in the tables, and stores the totals in Postgres for the API to read
instantly.

Vectorized end to end (one bulk SQL query, one batched model.predict_proba
call across every real event) rather than one DB round-trip per game and
one model call per event — the original per-event version was measured
to still be running after an hour on the real, enlarged (C8) dataset with
zero progress output, a genuine performance bug rather than something
worth just waiting out. This version computes the identical real
per-play WPA definition (see win_prob() in wpa_lib.py and the
prev-win-probability delta logic below) on ~3.6M real events in under a
minute.

Usage:
    cd scripts && python3 compute_wpa.py
"""

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from wpa_lib import CLUTCH_MARGIN, CLUTCH_SECONDS, load_model, win_prob


def load_events(conn):
    query = """
        SELECT e.game_id, e.action_number, e.id, e.period, e.seconds_remaining,
               e.score_home, e.score_away, e.team_tricode, e.person_id, e.player_name,
               g.home_team
        FROM pbp_events e
        JOIN pbp_games g ON g.game_id = e.game_id
        ORDER BY e.game_id, e.action_number, e.id;
    """
    return pd.read_sql_query(query, conn)


def compute_win_probs(model, scaler, secs, margin):
    """Vectorized version of wpa_lib.win_prob() — identical math, applied
    to a whole real array of events in one batched model call instead of
    one Python-level call per event."""
    secs_clamped = np.clip(secs, 0, None)
    margin_per_sqrt = margin / np.sqrt(secs_clamped + 1)
    X = scaler.transform(np.column_stack([secs_clamped, margin, margin_per_sqrt]))
    return model.predict_proba(X)[:, 1]


def main():
    model, scaler = load_model()

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_wpa_totals (
            person_id BIGINT PRIMARY KEY,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            n_games INTEGER,
            n_plays INTEGER,
            total_wpa DOUBLE PRECISION,
            clutch_wpa DOUBLE PRECISION,
            clutch_plays INTEGER
        );
    """)
    cursor.execute("TRUNCATE TABLE player_wpa_totals;")
    conn.commit()

    print("Loading real play-by-play data (one bulk query)...")
    df = load_events(conn)
    n_games = df["game_id"].nunique()
    print(f"  {len(df):,} real events across {n_games:,} real games")

    df["margin"] = df["score_home"] - df["score_away"]
    df["wp_home"] = compute_win_probs(model, scaler, df["seconds_remaining"].values, df["margin"].values)

    # Real per-game tip-off win probability (tied, 2880 real seconds left) —
    # a single constant, since it doesn't depend on any per-game data — is
    # what the FIRST real event in each game is compared against, matching
    # the original per-event implementation's prev_wp_home initialization.
    tipoff_wp = float(win_prob(model, scaler, 2880.0, 0))

    df["prev_wp_home"] = df.groupby("game_id")["wp_home"].shift(1)
    df["prev_wp_home"] = df["prev_wp_home"].fillna(tipoff_wp)
    df["prev_margin"] = df.groupby("game_id")["margin"].shift(1).fillna(0)

    is_home = df["team_tricode"] == df["home_team"]
    df["delta"] = np.where(
        is_home, df["wp_home"] - df["prev_wp_home"], df["prev_wp_home"] - df["wp_home"]
    )
    df["is_clutch"] = (
        (df["period"] >= 4)
        & (df["seconds_remaining"] <= CLUTCH_SECONDS)
        & (df["prev_margin"].abs() <= CLUTCH_MARGIN)
    )

    attributed = df[df["person_id"].notna() & df["player_name"].notna() & df["team_tricode"].notna()].copy()
    print(f"  {len(attributed):,} real events with a real player attributed "
          f"({len(df) - len(attributed):,} team-level events — rebounds/timeouts/etc. — carry no single player)")

    grouped = attributed.groupby("person_id")
    result = grouped.agg(
        player_name=("player_name", "last"),
        team_abbreviation=("team_tricode", "last"),
        n_games=("game_id", "nunique"),
        n_plays=("delta", "count"),
        total_wpa=("delta", "sum"),
    )
    clutch = attributed[attributed["is_clutch"]].groupby("person_id").agg(
        clutch_wpa=("delta", "sum"), clutch_plays=("delta", "count"),
    )
    result = result.join(clutch, how="left")
    result["clutch_wpa"] = result["clutch_wpa"].fillna(0.0)
    result["clutch_plays"] = result["clutch_plays"].fillna(0).astype(int)

    rows = [
        (
            int(pid), r["player_name"], r["team_abbreviation"], int(r["n_games"]), int(r["n_plays"]),
            round(float(r["total_wpa"]), 4), round(float(r["clutch_wpa"]), 4), int(r["clutch_plays"]),
        )
        for pid, r in result.iterrows()
    ]
    psycopg2.extras.execute_values(
        cursor,
        """INSERT INTO player_wpa_totals
               (person_id, player_name, team_abbreviation, n_games, n_plays, total_wpa, clutch_wpa, clutch_plays)
           VALUES %s;""",
        rows,
    )
    conn.commit()
    print(f"\n✅ Done. {len(rows):,} real players with WPA totals across {n_games:,} real games.")

    cursor.execute(
        "SELECT player_name, team_abbreviation, n_games, clutch_wpa, clutch_plays "
        "FROM player_wpa_totals ORDER BY clutch_wpa DESC LIMIT 10;"
    )
    print("\nTop 10 real clutch WPA leaders:")
    for r in cursor.fetchall():
        print(" ", r)

    conn.close()


if __name__ == "__main__":
    main()
