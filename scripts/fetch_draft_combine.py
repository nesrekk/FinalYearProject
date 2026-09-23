"""
fetch_draft_combine.py
========================
Bulk-fetches real NBA Draft Combine measurements (height, wingspan,
standing reach, weight, body fat, vertical leaps, lane agility, sprint)
for every draft class from nba_api's DraftCombineStats endpoint — one
real call per draft year, same pattern as fetch_college_stats.py.

Real, disclosed gap: this only covers players actually invited to and
measured at the real NBA Draft Combine — not every drafted player
attended, and undrafted/international players who skipped it have no
row here. NULL is stored (never guessed) for any individual test a
player skipped at the combine.

Column note: nba_api's own parameter is misleadingly named
`season_all_time`, but it actually filters to ONE real draft class per
call (verified directly against the live endpoint before writing this
script) — e.g. season_all_time='2023-24' returns only the real 2023
draft combine, not a cumulative "all seasons up to X" set. draft_year
follows this project's existing draft_history table convention: the
year of the real draft itself (a player drafted in the real 2023 draft
has their real rookie season as season int 2024 elsewhere in this DB).

Usage:
    cd scripts && python3 fetch_draft_combine.py
"""

import math
import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

DRAFT_YEAR_START = 2010
DRAFT_YEAR_END = 2026

COLUMNS = [
    ("player_id", "PLAYER_ID"),
    ("player_name", "PLAYER_NAME"),
    ("position", "POSITION"),
    ("height_wo_shoes", "HEIGHT_WO_SHOES"),
    ("height_w_shoes", "HEIGHT_W_SHOES"),
    ("weight", "WEIGHT"),
    ("wingspan", "WINGSPAN"),
    ("standing_reach", "STANDING_REACH"),
    ("body_fat_pct", "BODY_FAT_PCT"),
    ("hand_length", "HAND_LENGTH"),
    ("hand_width", "HAND_WIDTH"),
    ("standing_vertical_leap", "STANDING_VERTICAL_LEAP"),
    ("max_vertical_leap", "MAX_VERTICAL_LEAP"),
    ("lane_agility_time", "LANE_AGILITY_TIME"),
    ("three_quarter_sprint", "THREE_QUARTER_SPRINT"),
    ("bench_press", "BENCH_PRESS"),
]


def clean(value):
    """NaN (pandas' stand-in for a real missing test) and empty strings
    both become a real NULL — never a guessed 0 or default."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS draft_combine (
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            draft_year INT NOT NULL,
            position TEXT,
            height_wo_shoes DOUBLE PRECISION,
            height_w_shoes DOUBLE PRECISION,
            weight DOUBLE PRECISION,
            wingspan DOUBLE PRECISION,
            standing_reach DOUBLE PRECISION,
            body_fat_pct DOUBLE PRECISION,
            hand_length DOUBLE PRECISION,
            hand_width DOUBLE PRECISION,
            standing_vertical_leap DOUBLE PRECISION,
            max_vertical_leap DOUBLE PRECISION,
            lane_agility_time DOUBLE PRECISION,
            three_quarter_sprint DOUBLE PRECISION,
            bench_press DOUBLE PRECISION,
            PRIMARY KEY (player_id, draft_year)
        );
    """)


def fetch_draft_year(draft_year: int):
    from nba_api.stats.endpoints import draftcombinestats

    season_label = f"{draft_year}-{str(draft_year + 1)[-2:]}"
    endpoint = draftcombinestats.DraftCombineStats(season_all_time=season_label, timeout=30)
    return endpoint.get_data_frames()[0]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    total_rows = 0
    for draft_year in range(DRAFT_YEAR_START, DRAFT_YEAR_END + 1):
        try:
            df = fetch_draft_year(draft_year)
        except Exception as exc:
            print(f"  {draft_year}: FAILED — {exc}")
            time.sleep(1.0)
            continue

        if df.empty:
            print(f"  {draft_year}: no real combine data returned, skipping.")
            time.sleep(0.5)
            continue

        rows = []
        for _, r in df.iterrows():
            row = [clean(r[src]) for _, src in COLUMNS]
            rows.append((*row[:2], draft_year, *row[2:]))

        cursor.execute("DELETE FROM draft_combine WHERE draft_year = %s;", (draft_year,))
        col_names = ", ".join(["player_id", "player_name", "draft_year"] + [c for c, _ in COLUMNS[2:]])
        psycopg2.extras.execute_values(
            cursor,
            f"INSERT INTO draft_combine ({col_names}) VALUES %s;",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {draft_year}: {len(rows)} real combine measurements")
        time.sleep(0.6)

    conn.close()
    print(f"\n✅ Done. {total_rows} real draft combine rows across {DRAFT_YEAR_START}-{DRAFT_YEAR_END}.")


if __name__ == "__main__":
    main()
