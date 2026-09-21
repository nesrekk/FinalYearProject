"""
prewarm_shots.py
=================
Offline batch pre-warm for the Shot Charts feature. Run this manually,
BEFORE a demo, to populate Postgres (player_shots / player_shots_cache_status)
for a curated list of players — so they load instantly instead of triggering
a live on-demand fetch in front of anyone.

This does not touch the frontend or run as part of the API; it's a one-off
CLI script, same category as train_mvp_model.py etc.

Usage:
    cd api && python3 ../scripts/prewarm_shots.py

Edit PLAYERS below to change the roster. Each entry is (display_name,
nba_player_id) — look IDs up at https://www.nba.com/players or via
nba_api.stats.static.players.find_players_by_full_name().

Safety: this reuses shots_lib's single rate-limited fetch path (5s sleep
between every request, 30s backoff + retry on 403/429), the same as the
live API endpoint — nothing here hits stats.nba.com any harder than a
single user manually searching each of these players one at a time.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# shots_lib.py lives in api/, this script lives in scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

import shots_lib  # noqa: E402

# Edit this list freely. player_id is the NBA.com stats player id.
PLAYERS = [
    ("Stephen Curry", 201939),   # already cached locally, will be skipped
    ("LeBron James", 2544),
    ("Kevin Durant", 201142),
    ("Giannis Antetokounmpo", 203507),
    ("Nikola Jokic", 203999),
    ("Luka Doncic", 1629029),
    ("Jayson Tatum", 1628369),
    ("Joel Embiid", 203954),
    ("Shai Gilgeous-Alexander", 1628983),
    ("James Harden", 201935),
    ("Kyrie Irving", 202681),
    ("Damian Lillard", 203081),
    ("Anthony Davis", 203076),
    ("Kawhi Leonard", 202695),
    ("Devin Booker", 1626164),
]


def main():
    shots_lib.ensure_schema()
    print(f"Pre-warming {len(PLAYERS)} players (already-cached ones are skipped instantly)...\n")

    for i, (name, pid) in enumerate(PLAYERS, start=1):
        status = shots_lib.get_cache_status(pid)
        if status == "done":
            print(f"[{i}/{len(PLAYERS)}] {name} — already cached, skipping.")
            continue

        print(f"[{i}/{len(PLAYERS)}] {name} (player_id={pid}) — fetching live...")
        try:
            result = shots_lib.ensure_player_shots_cached(pid, name)
            print(f"    -> done. {len(result['seasons'])} seasons cached.")
        except shots_lib.ShotsFetchFailed as e:
            print(f"    -> FAILED: {e}")
        except shots_lib.ShotsUnavailable as e:
            print(f"    -> skipped: {e}")

        if i < len(PLAYERS):
            time.sleep(5)

    print("\nDone.")


if __name__ == "__main__":
    main()
