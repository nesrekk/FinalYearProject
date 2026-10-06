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
instantly. Times are the corrected clock (pbp_event_clock, from
build_event_clock.py; rerun this after it), not ESPN's own.

Vectorized end to end (one bulk SQL query, one batched model.predict_proba
call across every real event) rather than one DB round-trip per game and
one model call per event — the original per-event version was measured
to still be running after an hour on the real, enlarged (C8) dataset with
zero progress output, a genuine performance bug rather than something
worth just waiting out. This version computes the identical real
per-play WPA definition (see win_prob() in wpa_lib.py and the
prev-win-probability delta logic below) on ~3.6M real events in under a
minute.

Clutch vs. non-clutch (added 2026-09-28): a clutch play swings win
probability about 3.7x as much as an average play, so raw per-play WPA
can't be compared across the two. Each play's WPA is therefore divided by
its leverage (the win-probability value of one point at that moment,
from the same model), summed as a ratio of sums: sum(WPA) / sum(leverage)
= WPA per play in points at equal leverage. Rates are per "scoring
chance" (a shot, free throw or turnover — the plays that end a
possession in that player's hands), since rebounds, fouls and subs earn
~0 WPA and would otherwise mix usage into the rate. clutch_lift is the
player's clutch rate minus their non-clutch rate, minus the league's own
clutch-minus-non-clutch shift, with a game-clustered standard error
(delta method for a ratio of sums).

Usage:
    cd scripts && python3 compute_wpa.py
"""

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from wpa_lib import CLUTCH_MARGIN, CLUTCH_SECONDS, PBP_DEDUP_WHERE, load_model, win_prob


def events_sql(through=None):
    """The events query; `through` (an end year) keeps the games of seasons up to it (the paper's scripts pass
    paper_freeze.MAX_PAPER_SEASON, round 9 step 1; this build reads every season)."""
    # PBP_DEDUP_WHERE: one copy per real game (the ESPN one), so no play is
    # counted twice in a player's totals. Times come from pbp_event_clock (the
    # corrected clock, build_event_clock.py; since round 6 step 12, 2026-10-02):
    # ESPN logs made shots a median 14 s late, which put some of the last five
    # minutes' plays outside the clutch window. Every deduplicated event has a
    # row there; an event without one (an nba_api-only game, none today) keeps
    # its own time.
    cap = f" AND g.season <= {int(through)}" if through else ""
    return """
        SELECT e.game_id, e.action_number, e.id, e.period,
               COALESCE(k.seconds_remaining, e.seconds_remaining) AS seconds_remaining,
               e.score_home, e.score_away, e.team_tricode, e.person_id, e.player_name,
               e.action_type, g.home_team
        FROM pbp_events e
        JOIN pbp_games g ON g.game_id = e.game_id
        LEFT JOIN pbp_event_clock k ON k.event_id = e.id
        WHERE """ + PBP_DEDUP_WHERE + cap + """
        ORDER BY e.game_id, e.action_number, e.id;
    """


def load_events(conn, through=None):
    return pd.read_sql_query(events_sql(through), conn)


# A "scoring chance": a shot, free throw or turnover (ESPN action_type
# names, checked against every distinct value on 2026-09-28). The model
# doesn't know possession, so a miss or turnover earns ~0 WPA, not a loss.
SCORING_CHANCE_RE = r"Shot|Layup|Dunk|Hook|^Free Throw|Turnover|^Traveling$"


def is_scoring_chance(action_type):
    at = action_type.fillna("")
    return at.str.contains(SCORING_CHANCE_RE, regex=True) & (at != "No Turnover")


def clutch_split(attributed):
    """Per player: leverage-neutral points per scoring chance in clutch and
    non-clutch time, the lift over the league's own clutch shift, and its
    game-clustered standard error. Returns (per-player DataFrame, league dict)."""
    ch = attributed[is_scoring_chance(attributed["action_type"])]
    league = {
        c: g["delta"].sum() / g["lev"].sum() for c, g in ch.groupby("is_clutch")
    }
    shift = league[True] - league[False]

    per_game = ch.groupby(["person_id", "game_id", "is_clutch"]).agg(
        d=("delta", "sum"), lev=("lev", "sum"), n=("delta", "size"),
    ).reset_index()
    tot = per_game.groupby(["person_id", "is_clutch"]).agg(
        D=("d", "sum"), LEV=("lev", "sum"), N=("n", "sum"),
    ).reset_index()
    per_game = per_game.merge(tot, on=["person_id", "is_clutch"])
    # Influence of each game on the ratio D/LEV (delta method); the lift's
    # influence is the clutch one minus the non-clutch one from the same
    # game, so clutch and non-clutch plays in one game stay correlated.
    per_game["infl"] = (per_game["d"] - per_game["D"] / per_game["LEV"] * per_game["lev"]) / per_game["LEV"]
    signed = np.where(per_game["is_clutch"], per_game["infl"], -per_game["infl"])
    by_game = per_game.assign(s=signed).groupby(["person_id", "game_id"])["s"].sum()
    n_g = by_game.groupby("person_id").size()
    var = (by_game ** 2).groupby("person_id").sum() * n_g / (n_g - 1)

    wide = tot.pivot(index="person_id", columns="is_clutch", values=["D", "LEV", "N"])
    out = pd.DataFrame(index=wide.index)
    out["clutch_chances"] = wide["N"].get(True)
    out["nonclutch_chances"] = wide["N"].get(False)
    out["clutch_pts_rate"] = wide["D"].get(True) / wide["LEV"].get(True)
    out["nonclutch_pts_rate"] = wide["D"].get(False) / wide["LEV"].get(False)
    out["clutch_lift"] = out["clutch_pts_rate"] - out["nonclutch_pts_rate"] - shift
    out["clutch_lift_se"] = np.sqrt(var)
    # Both sides are needed for a lift; a one-game player has no SE.
    both = out["clutch_chances"].notna() & out["nonclutch_chances"].notna()
    out.loc[~both, ["clutch_pts_rate", "nonclutch_pts_rate", "clutch_lift", "clutch_lift_se"]] = np.nan
    out.loc[n_g.reindex(out.index) < 2, "clutch_lift_se"] = np.nan
    league_row = {
        "clutch_pts_rate": float(league[True]), "nonclutch_pts_rate": float(league[False]),
        "clutch_chances": int(ch["is_clutch"].sum()), "nonclutch_chances": int((~ch["is_clutch"]).sum()),
        "clutch_leverage_ratio": float(
            attributed.loc[attributed["is_clutch"], "lev"].mean() / attributed.loc[~attributed["is_clutch"], "lev"].mean()
        ),
    }
    return out, league_row


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
    cursor.execute("""
        ALTER TABLE player_wpa_totals
            ADD COLUMN IF NOT EXISTS nonclutch_wpa DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS nonclutch_plays INTEGER,
            ADD COLUMN IF NOT EXISTS clutch_chances INTEGER,
            ADD COLUMN IF NOT EXISTS nonclutch_chances INTEGER,
            ADD COLUMN IF NOT EXISTS clutch_pts_rate DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS nonclutch_pts_rate DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS clutch_lift DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS clutch_lift_se DOUBLE PRECISION;
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wpa_clutch_league (
            id INTEGER PRIMARY KEY,
            clutch_pts_rate DOUBLE PRECISION,
            nonclutch_pts_rate DOUBLE PRECISION,
            clutch_chances INTEGER,
            nonclutch_chances INTEGER,
            clutch_leverage_ratio DOUBLE PRECISION
        );
    """)
    cursor.execute("TRUNCATE TABLE player_wpa_totals;")
    cursor.execute("TRUNCATE TABLE wpa_clutch_league;")
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
    # Leverage: win probability gained per point scored at the state before
    # the play (same model, margin +/-2 points, symmetric for either team).
    secs, pm = df["seconds_remaining"].values, df["prev_margin"].values
    df["lev"] = (compute_win_probs(model, scaler, secs, pm + 2) - compute_win_probs(model, scaler, secs, pm - 2)) / 4

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
    nonclutch = attributed[~attributed["is_clutch"]].groupby("person_id").agg(
        nonclutch_wpa=("delta", "sum"), nonclutch_plays=("delta", "count"),
    )
    split, league = clutch_split(attributed)
    result = result.join(clutch, how="left").join(nonclutch, how="left").join(split, how="left")
    for col in ("clutch_wpa", "nonclutch_wpa"):
        result[col] = result[col].fillna(0.0)
    for col in ("clutch_plays", "nonclutch_plays", "clutch_chances", "nonclutch_chances"):
        result[col] = result[col].fillna(0).astype(int)

    def num(v, digits):
        return None if pd.isna(v) else round(float(v), digits)

    rows = [
        (
            int(pid), r["player_name"], r["team_abbreviation"], int(r["n_games"]), int(r["n_plays"]),
            round(float(r["total_wpa"]), 4), round(float(r["clutch_wpa"]), 4), int(r["clutch_plays"]),
            round(float(r["nonclutch_wpa"]), 4), int(r["nonclutch_plays"]),
            int(r["clutch_chances"]), int(r["nonclutch_chances"]),
            num(r["clutch_pts_rate"], 4), num(r["nonclutch_pts_rate"], 4),
            num(r["clutch_lift"], 4), num(r["clutch_lift_se"], 4),
        )
        for pid, r in result.iterrows()
    ]
    psycopg2.extras.execute_values(
        cursor,
        """INSERT INTO player_wpa_totals
               (person_id, player_name, team_abbreviation, n_games, n_plays, total_wpa, clutch_wpa, clutch_plays,
                nonclutch_wpa, nonclutch_plays, clutch_chances, nonclutch_chances,
                clutch_pts_rate, nonclutch_pts_rate, clutch_lift, clutch_lift_se)
           VALUES %s;""",
        rows,
    )
    cursor.execute(
        """INSERT INTO wpa_clutch_league
               (id, clutch_pts_rate, nonclutch_pts_rate, clutch_chances, nonclutch_chances, clutch_leverage_ratio)
           VALUES (1, %s, %s, %s, %s, %s);""",
        (league["clutch_pts_rate"], league["nonclutch_pts_rate"], league["clutch_chances"],
         league["nonclutch_chances"], league["clutch_leverage_ratio"]),
    )
    conn.commit()
    print(f"\nLeague points per scoring chance at equal leverage: clutch {league['clutch_pts_rate']:.3f} "
          f"({league['clutch_chances']:,}), non-clutch {league['nonclutch_pts_rate']:.3f} "
          f"({league['nonclutch_chances']:,}); clutch plays carry {league['clutch_leverage_ratio']:.1f}x the leverage.")
    for floor in (50, 100, 200):
        q = result[(result["clutch_chances"] >= floor) & result["clutch_lift_se"].notna()]
        z = q["clutch_lift"] / q["clutch_lift_se"]
        print(f"  >= {floor} clutch chances: {len(q)} players, {(z.abs() > 1.96).sum()} outside zero at 95% "
              f"(~{0.05 * len(q):.0f} expected by chance), SD of z = {z.std():.2f}")
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
