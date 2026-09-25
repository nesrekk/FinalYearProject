"""
fetch_defend_dashboard.py
===========================
Bulk-fetches real defended-shot data from nba_api's LeagueDashPtDefend
endpoint (defense_category='Overall', real regular-season totals) — one
real call per season. For every real defender: real shots taken with them
as the closest defender (D_FGA/D_FGM), the real FG% on those shots
(D_FG_PCT), and the real FG% those same shooters normally make
(NORMAL_FG_PCT, the NBA's own baseline). PCT_PLUSMINUS = D_FG_PCT -
NORMAL_FG_PCT, so negative means the shooters made fewer shots than usual
against this defender.

Used by build_dad_index.py (DAD Index). Seasons match player_matchups'
real coverage (2017-18 through 2025-26); verified live before writing
this script that 2017-18 and 2025-26 both return full real league data.

Usage:
    cd scripts && python3 fetch_defend_dashboard.py
"""

import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2018
SEASON_END = 2026


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS defender_dfg (
            season INT NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            position TEXT,
            gp INT,
            d_fgm INT,
            d_fga INT,
            d_fg_pct DOUBLE PRECISION,
            normal_fg_pct DOUBLE PRECISION,
            pct_plusminus DOUBLE PRECISION,
            PRIMARY KEY (season, player_id)
        );
    """)


def fetch_season(season: int):
    from nba_api.stats.endpoints import leaguedashptdefend

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = leaguedashptdefend.LeagueDashPtDefend(
        season=season_label,
        defense_category="Overall",
        per_mode_simple="Totals",
        season_type_all_star="Regular Season",
        timeout=60,
    )
    return endpoint.get_data_frames()[0]


def combine_stints(season, df):
    """A real player traded mid-season can come back as one row per stint
    (checked live: Corey Brewer 2017-18, 52 + 18 real games). Stints are
    summed; D_FG% is recomputed from the real summed makes/attempts, and
    NORMAL_FG% is weighted by each stint's real D_FGA (i.e. real expected
    makes / real attempts), so the combined line is exact, not averaged."""
    df = df.copy()
    df["normal_makes"] = df["NORMAL_FG_PCT"] * df["D_FGA"]
    rows = []
    for pid, g in df.groupby("CLOSE_DEF_PERSON_ID", sort=False):
        last = g.iloc[-1]
        fgm, fga = int(g["D_FGM"].sum()), int(g["D_FGA"].sum())
        if len(g) == 1:
            d_pct, normal = float(last["D_FG_PCT"]), float(last["NORMAL_FG_PCT"])
            plusminus = float(last["PCT_PLUSMINUS"])
        else:
            d_pct = fgm / fga if fga else None
            normal = float(g["normal_makes"].sum()) / fga if fga else None
            plusminus = d_pct - normal if fga else None
        rows.append((
            season, int(pid), last["PLAYER_NAME"], last["PLAYER_LAST_TEAM_ABBREVIATION"],
            last["PLAYER_POSITION"], int(g["GP"].sum()), fgm, fga, d_pct, normal, plusminus,
        ))
    return rows


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    total_rows = 0
    for season in range(SEASON_START, SEASON_END + 1):
        df = None
        for attempt in range(3):
            try:
                df = fetch_season(season)
                break
            except Exception as exc:
                print(f"  {season}: attempt {attempt + 1} failed — {exc}")
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            print(f"  {season}: no real defend data returned, skipping.")
            continue

        rows = combine_stints(season, df)
        cursor.execute("DELETE FROM defender_dfg WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO defender_dfg
               (season, player_id, player_name, team_abbreviation, position, gp,
                d_fgm, d_fga, d_fg_pct, normal_fg_pct, pct_plusminus)
               VALUES %s;""",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {season}: {len(rows)} real defenders")
        time.sleep(1.0)

    conn.close()
    print(f"\n✅ Done. {total_rows} real defender rows, seasons {SEASON_START}-{SEASON_END}.")


if __name__ == "__main__":
    main()
