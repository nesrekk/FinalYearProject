"""
compute_wpa.py
===============
Runs the trained win-probability model (train_wpa_model.py) over every
real play-by-play event fetched by fetch_play_by_play.py, computing real
Win Probability Added per play: WPA = P(win after) - P(win before), from
the perspective of whichever team's player made that play. Aggregates
each real player's total WPA and CLUTCH WPA (final 5 min of regulation/OT,
real score margin within 5 points at that moment — the NBA's own
"clutch time" definition) across the real games in the sample, and
stores the totals in Postgres for the API to read instantly.

Usage:
    cd scripts && python3 compute_wpa.py
"""

import math
import pickle

import numpy as np
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

CLUTCH_SECONDS = 300
CLUTCH_MARGIN = 5


def load_model():
    with open("wpa_model.pkl", "rb") as f:
        model = pickle.load(f)
    with open("wpa_scaler.pkl", "rb") as f:
        scaler = pickle.load(f)
    return model, scaler


def win_prob(model, scaler, seconds_remaining, margin):
    margin_per_sqrt = margin / math.sqrt(seconds_remaining + 1)
    X = scaler.transform([[seconds_remaining, margin, margin_per_sqrt]])
    return float(model.predict_proba(X)[0, 1])


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

    cursor.execute("SELECT game_id, home_team, away_team FROM pbp_games;")
    games = cursor.fetchall()
    print(f"Computing real WPA across {len(games)} real games...")

    totals = {}  # person_id -> {name, team, games:set, plays, total_wpa, clutch_wpa, clutch_plays}

    for gi, (game_id, home_team, away_team) in enumerate(games):
        cursor.execute(
            """SELECT period, seconds_remaining, score_home, score_away, team_tricode,
                      person_id, player_name, action_type
               FROM pbp_events WHERE game_id = %s ORDER BY action_number;""",
            (game_id,),
        )
        events = cursor.fetchall()
        if not events:
            continue

        prev_secs, prev_margin = 2880.0, 0
        prev_wp_home = win_prob(model, scaler, prev_secs, prev_margin)

        for period, secs, score_home, score_away, team_tricode, person_id, player_name, action_type in events:
            margin = score_home - score_away
            wp_home = win_prob(model, scaler, max(secs, 0), margin)

            if person_id and player_name and team_tricode:
                is_home = (team_tricode == home_team)
                delta = (wp_home - prev_wp_home) if is_home else (prev_wp_home - wp_home)

                entry = totals.setdefault(person_id, {
                    "name": player_name, "team": team_tricode, "games": set(),
                    "plays": 0, "total_wpa": 0.0, "clutch_wpa": 0.0, "clutch_plays": 0,
                })
                entry["team"] = team_tricode  # keep most-recently-seen team
                entry["games"].add(game_id)
                entry["plays"] += 1
                entry["total_wpa"] += delta

                is_clutch = (period >= 4) and (secs <= CLUTCH_SECONDS) and (abs(prev_margin) <= CLUTCH_MARGIN)
                if is_clutch:
                    entry["clutch_wpa"] += delta
                    entry["clutch_plays"] += 1

            prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

        if (gi + 1) % 50 == 0:
            print(f"  [{gi + 1}/{len(games)}] games processed, {len(totals)} players tracked so far")

    rows = [
        (pid, e["name"], e["team"], len(e["games"]), e["plays"],
         round(e["total_wpa"], 4), round(e["clutch_wpa"], 4), e["clutch_plays"])
        for pid, e in totals.items()
    ]
    psycopg2.extras.execute_values(
        cursor,
        """INSERT INTO player_wpa_totals
               (person_id, player_name, team_abbreviation, n_games, n_plays, total_wpa, clutch_wpa, clutch_plays)
           VALUES %s;""",
        rows,
    )
    conn.commit()
    print(f"\n✅ Done. {len(rows)} real players with WPA totals across {len(games)} real games.")

    cursor.execute("SELECT player_name, team_abbreviation, n_games, clutch_wpa, clutch_plays FROM player_wpa_totals ORDER BY clutch_wpa DESC LIMIT 10;")
    print("\nTop 10 real clutch WPA leaders in this sample:")
    for r in cursor.fetchall():
        print(" ", r)

    conn.close()


if __name__ == "__main__":
    main()
