"""
build_league_zone_mix.py
========================
League-wide shot mix per season: attempts and makes in each of the five
shot zones, regular season only (game_id '002…'), from every row in
player_shots. The zones come from api/shots_lib.classify_zone(), the same
classifier every other shot feature uses, so a player's zone shares and
the league's are directly comparable.

Used by GET /shots/player/{name}/zone-history (Shot Charts › "Shot mix
over a career") as the league line behind each player's own mix.

    league_zone_mix(season text, zone text, fgm int, fga int)

Rerun after reloading player_shots. Takes a minute or two (~6.3M rows
streamed through a server-side cursor).

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/build_league_zone_mix.py
"""

import os
import sys
from collections import defaultdict

import psycopg2

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
from shots_lib import ZONES, classify_zone  # noqa: E402


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    agg = defaultdict(lambda: [0, 0])  # (season, zone) -> [fgm, fga]
    dropped = 0
    with conn.cursor(name="zone_mix_stream") as cur:
        cur.itersize = 200_000
        cur.execute(
            """SELECT season, loc_x, loc_y, shot_distance, shot_type, shot_zone_basic, shot_made_flag
               FROM player_shots WHERE game_id LIKE '002%%'"""
        )
        for season, x, y, dist, stype, zbasic, made in cur:
            zone = classify_zone(x, y, dist, stype, zbasic)
            if zone is None:
                dropped += 1
                continue
            cell = agg[(season, zone)]
            cell[0] += made or 0
            cell[1] += 1

    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS league_zone_mix;
            CREATE TABLE league_zone_mix (
                season text NOT NULL,
                zone text NOT NULL,
                fgm integer NOT NULL,
                fga integer NOT NULL,
                PRIMARY KEY (season, zone)
            );
        """)
        rows = [(s, z, fgm, fga) for (s, z), (fgm, fga) in sorted(agg.items())]
        cur.executemany("INSERT INTO league_zone_mix VALUES (%s, %s, %s, %s)", rows)
    conn.commit()

    seasons = sorted({s for s, _ in agg})
    print(f"league_zone_mix: {len(rows)} rows, {len(seasons)} seasons ({seasons[0]} to {seasons[-1]}), "
          f"{dropped} unclassifiable shots dropped")
    for s in seasons:
        total = sum(agg[(s, z)][1] for z in ZONES)
        threes = agg[(s, "Corner 3")][1] + agg[(s, "Above the Break 3")][1]
        print(f"  {s}: {total:>7} FGA, 3PA share {threes / total:.3f}")
    conn.close()


if __name__ == "__main__":
    main()
