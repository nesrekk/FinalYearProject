"""
build_award_winners_table.py
=============================
Consolidates this project's real historical award-winner data — the
mvp_winners table (used by train_mvp_model.py) and the DPOY_WINNERS /
ROY_WINNERS dicts (used by build_dpoy_roy_models.py) — into one canonical,
queryable award_winners table, so the live API can answer "has this player
won this award recently" without duplicating the winner lists a second time.

Usage:
    cd scripts && python3 build_award_winners_table.py
"""

import sys
import psycopg2

import build_dpoy_roy_models as dpoy_roy_mod

from db_config import DB_CONFIG


# MVPs after mvp_winners' last season. 2025-26: read 2026-10-06 from
# https://en.wikipedia.org/wiki/NBA_Most_Valuable_Player_Award and the 2025-26 NBA season page.
MVP_AFTER_LEGACY = {
    2026: "Shai Gilgeous-Alexander",
}


def resolve_player_id(cursor, player_name: str, season: int):
    cursor.execute(
        "SELECT player_id FROM player_season_stats WHERE player_name = %s AND season = %s;",
        (player_name, season),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS award_winners (
            season INTEGER NOT NULL,
            award TEXT NOT NULL,
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            PRIMARY KEY (season, award)
        );
    """)
    cursor.execute("TRUNCATE TABLE award_winners;")
    conn.commit()

    rows = []

    # MVP: mvp_winners already has real player_id values. It is a legacy table with no loader in the repo
    # (stops at 2024-25), so later winners are listed here and resolved like DPOY/ROY.
    cursor.execute("SELECT season, player_id, player_name FROM mvp_winners ORDER BY season;")
    for season, player_id, player_name in cursor.fetchall():
        rows.append((season, "MVP", player_id, player_name))
    legacy_seasons = {r[0] for r in rows}
    for season, player_name in MVP_AFTER_LEGACY.items():
        if season in legacy_seasons:
            continue
        player_id = resolve_player_id(cursor, player_name, season)
        if player_id is None:
            print(f"  ⚠️  Could not resolve MVP {season}: {player_name} — skipped")
            continue
        rows.append((season, "MVP", player_id, player_name))

    # DPOY / ROY: winners are only recorded as names in
    # build_dpoy_roy_models.py — resolve each to a real player_id.
    for award, winners in [("DPOY", dpoy_roy_mod.DPOY_WINNERS), ("ROY", dpoy_roy_mod.ROY_WINNERS)]:
        for season, player_name in winners.items():
            player_id = resolve_player_id(cursor, player_name, season)
            if player_id is None:
                print(f"  ⚠️  Could not resolve {award} {season}: {player_name} — skipped")
                continue
            rows.append((season, award, player_id, player_name))

    cursor.executemany(
        "INSERT INTO award_winners (season, award, player_id, player_name) VALUES (%s, %s, %s, %s);",
        rows,
    )
    conn.commit()

    cursor.execute("SELECT award, COUNT(*) FROM award_winners GROUP BY award ORDER BY award;")
    for award, count in cursor.fetchall():
        print(f"  {award}: {count} seasons")

    conn.close()
    print(f"\n✅ award_winners table built: {len(rows)} total rows.")


if __name__ == "__main__":
    sys.exit(main())
