"""
fetch_pbp_espn.py
===================
Bulk-fetches real FULL-SEASON play-by-play via `sportsdataverse` (the
Python port of the hoopR/sportsdataverse-data project the roadmap named:
a pre-scraped, hosted ESPN play-by-play release, not a live scrape).
This is real additional coverage for the same pbp_games/pbp_events
tables fetch_play_by_play.py (nba_api-sourced) already populates — every
row gets a real `source` column ('espn' here, 'nba_api' there) so the
two real sources never silently mix despite sharing a schema.

Why add a second source instead of just fetching more nba_api games:
nba_api's PlayByPlayV3 is one real HTTP call PER GAME (fetch_play_by_play.py
disclaims it only pulls a sampled subset for that reason). This endpoint
returns an ENTIRE real season — ~1,232 real regular-season games, ~600k
real events — in one real ~7-second call. Real trade-off: it's ESPN's
own play-by-play, not the NBA's, so real team abbreviations and real
player identities need mapping before they line up with this project's
existing nba_api-keyed tables.

Real facts verified live against the endpoint before writing this script:
- `end_game_seconds_remaining` uses the IDENTICAL real convention
  fetch_play_by_play.py's own seconds_remaining_in_game() already uses:
  counts down from 2880 across regulation, then RESETS to each real OT
  period's own clock rather than continuing a running countdown. No
  conversion needed — confirmed by inspecting a real OT game's rows.
- ESPN's real team abbreviations differ from nba_api's tricodes for 6
  real franchises: GS/NO/NY/SA/UTAH/WSH vs. GSW/NOP/NYK/SAS/UTA/WAS.
  TEAM_CROSSWALK below maps them; any game where either team doesn't
  resolve to one of the 30 real NBA tricodes is dropped.
- `season_type == 2` ("regular season" per the endpoint's own labeling)
  real-world STILL includes the 2024 All-Star Game (home/away team
  abbreviations 'EAST'/'WEST', game_id 401623259, 2024-02-18) — a real
  ESPN/sportsdataverse mislabel, not a code bug. The 30-real-team-only
  filter above catches this incidentally, but it's called out explicitly
  since it's the kind of silent contamination this project's "never
  fabricate/never silently mix" discipline exists to catch.

Player attribution (person_id) is matched to this project's existing
nba_api-keyed player_id by real name, per season, against
player_season_stats — accent-normalized exact match first, falling back
to rapidfuzz fuzzy matching (already a transitive dependency of
sportsdataverse) above a disclosed similarity floor. Unmatched real
plays keep their real ESPN player name but get NULL person_id rather
than a guessed one — compute_wpa.py already skips WPA attribution for
any event with a null person_id, so an unmatched player (retired,
G-League call-up with no real season row, a real name ESPN spells
differently than nba_api) is a real, disclosed gap in player-level
attribution only, not a break in the win-probability model itself
(training never uses person_id at all).

Usage:
    cd scripts && python3 fetch_pbp_espn.py [season_start] [season_end]
    python3 fetch_pbp_espn.py 2021 2026
"""

import sys
import time
import unicodedata

import pandas as pd
import psycopg2
import psycopg2.extras
from rapidfuzz import fuzz, process

from db_config import DB_CONFIG

TEAM_CROSSWALK = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS",
}
REAL_NBA_TRICODES = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND",
    "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
    "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}
NAME_MATCH_FLOOR = 90


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    stripped = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return stripped.lower().strip()


def _resolve_team(abbrev):
    return TEAM_CROSSWALK.get(abbrev, abbrev)


def _pyval(x):
    """sportsdataverse returns pyarrow-backed pandas dtypes, whose nulls are
    pd.NA — not NaN — so the classic `x == x` NaN check silently raises
    (bool(pd.NA) is ambiguous) instead of returning False. pd.isna() is the
    one check that's correct across both NaN and pd.NA."""
    return None if pd.isna(x) else x


def ensure_schema(cursor):
    cursor.execute("ALTER TABLE pbp_games ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'nba_api';")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbp_games_source ON pbp_games(source);")


class PlayerMatcher:
    """Per-season real name -> nba_api player_id lookup, accent-normalized
    exact match first, rapidfuzz fallback second."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._cache = {}  # season -> {normalized_name: (player_id, player_name)}
        self._miss_count = 0
        self._hit_count = 0

    def _season_map(self, season: int):
        if season not in self._cache:
            self._cursor.execute(
                "SELECT DISTINCT player_id, player_name FROM player_season_stats WHERE season = %s;",
                (season,),
            )
            self._cache[season] = {_normalize_name(name): (pid, name) for pid, name in self._cursor.fetchall()}
        return self._cache[season]

    def match(self, espn_name: str, season: int):
        if not espn_name:
            return None, espn_name
        name_map = self._season_map(season)
        norm = _normalize_name(espn_name)
        if norm in name_map:
            self._hit_count += 1
            pid, real_name = name_map[norm]
            return pid, real_name
        if name_map:
            best = process.extractOne(norm, name_map.keys(), scorer=fuzz.WRatio)
            if best and best[1] >= NAME_MATCH_FLOOR:
                self._hit_count += 1
                pid, real_name = name_map[best[0]]
                return pid, real_name
        self._miss_count += 1
        return None, espn_name

    def stats(self):
        return self._hit_count, self._miss_count


def fetch_season(season: int):
    import sportsdataverse.nba as nba

    df = nba.load_nba_pbp(seasons=season, return_as_pandas=True)
    if df.empty:
        return df
    df = df[df["season_type"] == 2].copy()
    df["home_team_abbrev"] = df["home_team_abbrev"].map(_resolve_team)
    df["away_team_abbrev"] = df["away_team_abbrev"].map(_resolve_team)
    valid_games = (
        df.groupby("game_id")
        .agg(home=("home_team_abbrev", "first"), away=("away_team_abbrev", "first"))
        .reset_index()
    )
    valid_games = valid_games[
        valid_games["home"].isin(REAL_NBA_TRICODES) & valid_games["away"].isin(REAL_NBA_TRICODES)
    ]
    return df[df["game_id"].isin(valid_games["game_id"])].copy()


def _process_season(matcher, season: int):
    """Returns (game_rows, event_rows) ready for insert, or (None, None) if
    this real season has no usable data. Raises on a genuine real error
    (e.g. an unexpected schema gap) so the caller can skip just this season
    without losing already-committed earlier seasons."""
    df = fetch_season(season)
    if df.empty:
        return None, None

    df = df.sort_values(["game_id", "game_play_number"])

    game_rows, event_rows = [], []
    for game_id, g in df.groupby("game_id"):
        db_game_id = f"espn_{int(game_id)}"
        last = g.iloc[-1]
        home_abbrev, away_abbrev = str(last["home_team_abbrev"]), str(last["away_team_abbrev"])
        home_win = bool(last["home_score"] > last["away_score"])
        game_date = last["game_date"]
        game_rows.append((db_game_id, season, game_date, home_abbrev, away_abbrev, home_win, "espn"))

        for _, r in g.iterrows():
            secs = _pyval(r["end_game_seconds_remaining"])
            if secs is None:
                continue
            team_id = _pyval(r["team_id"])
            team_abbrev = None
            if team_id is not None:
                if team_id == _pyval(r["home_team_id"]):
                    team_abbrev = home_abbrev
                elif team_id == _pyval(r["away_team_id"]):
                    team_abbrev = away_abbrev
            raw_name = _pyval(r["athlete_name_1"])
            person_id, player_name = (None, None)
            if raw_name:
                person_id, player_name = matcher.match(str(raw_name), season)
            # short_description isn't present in every real season's schema
            # (real, verified column drift across sportsdataverse releases) —
            # .get() rather than [] so an older season doesn't crash on it.
            description = _pyval(r["text"]) or _pyval(r.get("short_description"))
            type_text = _pyval(r["type_text"])
            event_rows.append((
                db_game_id,
                int(_pyval(r["game_play_number"])) if _pyval(r["game_play_number"]) is not None else None,
                int(r["period_number"]),
                float(secs),
                int(r["home_score"]),
                int(r["away_score"]),
                int(team_id) if team_id is not None else None,
                team_abbrev,
                person_id,
                player_name,
                str(type_text) if type_text is not None else None,
                None,
                str(description) if description is not None else None,
            ))

    return game_rows, event_rows


def main():
    season_start = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    season_end = int(sys.argv[2]) if len(sys.argv) > 2 else 2026

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_schema(cursor)
    conn.commit()

    matcher = PlayerMatcher(cursor)
    total_games, total_events = 0, 0

    for season in range(season_start, season_end + 1):
        t0 = time.time()
        try:
            game_rows, event_rows = _process_season(matcher, season)
        except Exception as exc:
            print(f"  {season}: FAILED — {exc}")
            conn.rollback()
            continue

        if game_rows is None:
            print(f"  {season}: no real ESPN regular-season data returned, skipping.")
            continue

        try:
            cursor.execute("DELETE FROM pbp_games WHERE season = %s AND source = 'espn';", (season,))
            psycopg2.extras.execute_values(
                cursor,
                """INSERT INTO pbp_games (game_id, season, game_date, home_team, away_team, home_win, source)
                   VALUES %s;""",
                game_rows,
            )
            psycopg2.extras.execute_values(
                cursor,
                """INSERT INTO pbp_events
                   (game_id, action_number, period, seconds_remaining, score_home, score_away,
                    team_id, team_tricode, person_id, player_name, action_type, sub_type, description)
                   VALUES %s;""",
                event_rows,
                page_size=2000,
            )
            conn.commit()
        except Exception as exc:
            print(f"  {season}: FAILED on insert — {exc}")
            conn.rollback()
            continue

        n_games, n_events = len(game_rows), len(event_rows)
        total_games += n_games
        total_events += n_events
        print(f"  {season}: {n_games} real games, {n_events:,} real events ({time.time() - t0:.1f}s)")

    hits, misses = matcher.stats()
    conn.close()
    print(f"\n✅ Done. {total_games} real ESPN-sourced games, {total_events:,} real events, "
          f"seasons {season_start}-{season_end}.")
    print(f"   Real player-name matches: {hits:,} hit, {misses:,} unmatched "
          f"(unmatched events keep their real ESPN name but no person_id, per the docstring above).")


if __name__ == "__main__":
    main()
