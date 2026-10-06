"""
fetch_2025_26_season_data.py
============================
Safely fetch and cache 2025-26 NBA player-season datasets into nba_data/.

Why this script:
- Avoid repeated calls to stats.nba.com (reduces ban/rate-limit risk)
- Save once locally, then reuse CSVs for the project

Outputs:
- nba_data/nba_2025_26_season.csv
- nba_data/nba_2025_26_advanced.csv

Usage:
    python fetch_2025_26_season_data.py
"""

import os
import random
import time

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")

SEASON_LABEL = "2025-26"
FILE_TAG = "2025_26"

SEASON_CSV = os.path.join(DATA_DIR, f"nba_{FILE_TAG}_season.csv")
ADVANCED_CSV = os.path.join(DATA_DIR, f"nba_{FILE_TAG}_advanced.csv")

# Conservative request behavior to avoid hammering API.
MAX_RETRIES = 5
BASE_SLEEP_SECONDS = 3.0
REQUEST_TIMEOUT = 60


def polite_sleep(multiplier: float = 1.0):
    """Sleep with small jitter so requests are less bursty."""
    jitter = random.uniform(0.2, 1.0)
    time.sleep((BASE_SLEEP_SECONDS * multiplier) + jitter)


def fetch_with_retry(measure_type: str, season_label: str = SEASON_LABEL,
                     season_type: str = "Regular Season", allow_empty: bool = False) -> pd.DataFrame:
    """
    Fetch a player stats table with retries and backoff.
    measure_type should be 'Base' or 'Advanced'. season_label / season_type default to this
    script's 2025-26 regular season; daily_update.py passes the live season's (round 9 step 2)
    with allow_empty=True: before the first game the endpoint answers an empty table, which is
    not an error there (here an empty answer for 2025-26 is, and is retried).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
                season=season_label,
                season_type_all_star=season_type,
                measure_type_detailed_defense=measure_type,
                per_mode_detailed="PerGame",
                timeout=REQUEST_TIMEOUT,
            )
            df = endpoint.get_data_frames()[0]
            if df.empty and not allow_empty:
                raise RuntimeError(f"Empty dataframe returned for {measure_type}")
            return df
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"Failed to fetch {measure_type} after {MAX_RETRIES} attempts: {exc}"
                ) from exc
            backoff = attempt * 1.5
            print(
                f"Attempt {attempt}/{MAX_RETRIES} failed for {measure_type}: {exc}. "
                f"Retrying after {backoff:.1f}s..."
            )
            polite_sleep(multiplier=backoff / BASE_SLEEP_SECONDS)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    print(f"Target season: {SEASON_LABEL}")
    print(f"Output folder: {DATA_DIR}")

    if os.path.exists(SEASON_CSV) and os.path.exists(ADVANCED_CSV):
        print("Both CSV files already exist. Skipping fetch to avoid extra API calls.")
        print(f"- {SEASON_CSV}")
        print(f"- {ADVANCED_CSV}")
        return

    if not os.path.exists(SEASON_CSV):
        print("\nFetching base season stats...")
        base_df = fetch_with_retry("Base")
        base_df.to_csv(SEASON_CSV, index=False)
        print(f"Saved: {SEASON_CSV} ({len(base_df)} rows)")
    else:
        print(f"\nBase file already exists, skipping: {SEASON_CSV}")

    # Sleep between calls to reduce chance of throttling.
    polite_sleep()

    if not os.path.exists(ADVANCED_CSV):
        print("\nFetching advanced season stats...")
        adv_df = fetch_with_retry("Advanced")
        adv_df.to_csv(ADVANCED_CSV, index=False)
        print(f"Saved: {ADVANCED_CSV} ({len(adv_df)} rows)")
    else:
        print(f"\nAdvanced file already exists, skipping: {ADVANCED_CSV}")

    print("\nDone. 2025-26 season data is cached locally.")


if __name__ == "__main__":
    main()

