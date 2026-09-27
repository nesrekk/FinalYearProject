"""
load_kaggle_historical_seasons.py
===================================
Extends player_season_stats backward from its current floor (season=2010)
using the Kaggle "NBA Database, 1947-present" CSVs at
nba_data/kaggle_1947_present/ (Player Per Game.csv + Advanced.csv,
Basketball-Reference-sourced). Purely additive: only ever touches rows with
season < 2010 — every row this project already has (2010-2026, loaded from
nba_api) is left untouched.

Two real problems this script has to solve that the existing nba_api-only
loaders (load_2025_26_into_db.py etc.) never had to:

1. Player identity. Every table in this DB keys players by nba_api's numeric
   player_id (LeBron James = 2544). The Kaggle CSVs key players by
   Basketball-Reference's slug id instead (e.g. "jamesle01"). There is no
   existing crosswalk between the two systems anywhere in this project, so
   this script builds one — a new player_id_map table — by matching each
   Kaggle player's real full name against nba_api's static all-time player
   list (nba_api.stats.static.players, 5,103 players, bundled with the
   package — no live API call). Matching lives in bref_nba_ids.py (shared
   with load_draft_history_bref.py): exact names with Jr./II kept, first-
   name forms, a few hand-checked nicknames, season overlap and NBA id-era
   checks, one person per id. Rebuilt 2026-09-27: the first version matched
   exact names only, so 233 players (Ewing, Payton, Hardaway...) were
   skipped and 4 ids held two different players' seasons. A player it
   can't match safely is recorded with the reason and that player-season is
   skipped rather than guessed at. The map is a real table, not a one-off dict, so any
   later script (or a future Kaggle re-import) can reuse it instead of
   re-resolving names from scratch.

2. Traded players. Basketball-Reference gives a player traded mid-season one
   row per team PLUS one combined-total row (team code "2TM", "3TM", etc.).
   This table's primary key is (player_id, season), one row per player-
   season, so the combined-total row is used when present; a player who
   wasn't traded that season just has their single team row.

Column mapping (verified against a known real season — LeBron James,
2005-06, Advanced.csv row: ts_percent .578 / usg_percent 28.4 / bpm 8.9 —
matches published Basketball-Reference figures for that season):
  Player Per Game.csv -> player_id(bbref), player, team, season, age, g,
    mp_per_game, pts/trb/ast/stl/blk/tov_per_game, fg/x3p/ft_percent,
    fg/fga/x3p/x3pa/ft/fta/orb/drb_per_game, e_fg_percent, pf_per_game
  Advanced.csv -> ts_percent, usg_percent, ast_percent, orb_percent,
    trb_percent, tov_percent, obpm, dbpm, bpm, vorp
  Percent-scale advanced columns (usg/ast/orb/trb/tov/stl/blk_percent) are
  on Basketball-Reference's 0-100 scale; this table's *_pct columns are
  fractions (0-1), same conversion load_2025_26_into_db.py already does for
  TM_TOV_PCT. ts_percent/e_fg_percent/fg_percent etc. are already fractions
  in the Kaggle CSV, no conversion needed.
  off_rating/def_rating/net_rating/w_pct/plus_minus/poss have no equivalent
  in these two Kaggle files and are left NULL for historical rows (a real
  gap — Basketball-Reference's advanced table doesn't publish individual
  ORtg/DRtg for most of this era) rather than estimated.
  impact_score/impact_score_raw/impact_score_star/bpm_position are NOT
  computed here, same as load_2025_26_into_db.py — run
  compute_impact_score.py / upgrade_impact_scores.py / build_bpm_vorp.py's
  ranking step afterward if you want those backfilled for these seasons too
  (bpm/obpm/dbpm/vorp themselves ARE filled here, straight from
  Basketball-Reference, since this project's own build_bpm_vorp.py computes
  the same metric from scratch for 2010+ — reusing BBRef's published values
  for pre-2010 seasons is more reliable than re-deriving them here).

Scope: NBA seasons only (lg == 'NBA'), season < 2010. ABA (1968-1976) and
BAA (1946-1949) rows exist in the Kaggle data too but use different rules
and aren't loaded here — a real, disclosed scope limit, not an oversight.

Usage:
    python load_kaggle_historical_seasons.py
"""

import os

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import bref_nba_ids

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data", "kaggle_1947_present")
BACKUP_DIR = os.path.join(BASE_DIR, "scratchpad_backups")

PER_GAME_CSV = os.path.join(DATA_DIR, "Player Per Game.csv")
ADVANCED_CSV = os.path.join(DATA_DIR, "Advanced.csv")

SEASON_CUTOFF = 2010  # only load season < this; 2010+ is already owned by the nba_api pipeline

from db_config import DB_CONFIG

INSERT_COLUMNS = [
    "player_id", "player_name", "team_abbreviation", "season", "age", "gp", "min",
    "pts", "reb", "ast", "stl", "blk", "tov", "fg_pct", "fg3_pct", "ft_pct",
    "ts_pct", "usg_pct", "ast_pct", "reb_pct", "fgm", "fga", "fg3m", "fg3a",
    "ftm", "fta", "oreb", "dreb", "efg_pct", "oreb_pct", "tov_pct", "pf",
    "obpm", "dbpm", "bpm", "vorp",
]


def build_player_id_map(bbref_names):
    """Returns dict bbref_id -> nba_player_id and rewrites player_id_map for
    every BAA/NBA player in the export, using bref_nba_ids.resolve (names,
    first-name forms, checked nicknames, 2010+ season overlap, NBA id eras,
    one person per id). Players it can't match safely get
    nba_player_id=NULL with the reason in match_method, not a guess.
    bbref_names is kept for the call signature; resolution runs over all
    players so a pre-2010 player can't claim an id that belongs to a later
    one."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""SELECT player_id, player_name, min(season), max(season)
                   FROM player_season_stats WHERE season >= %s GROUP BY 1, 2;""", (SEASON_CUTOFF,))
    evidence = {"_names": {}}
    for pid, name, lo, hi in cur.fetchall():
        evidence[pid] = (lo, hi)
        evidence["_names"][pid] = name

    bref = bref_nba_ids.load_bref_players()
    result = bref_nba_ids.resolve(bref, evidence)
    names = dict(zip(bref.player_id, bref.player))

    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_id_map (
            bbref_id TEXT PRIMARY KEY,
            player_name TEXT NOT NULL,
            nba_player_id INTEGER,
            match_method TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
    """)
    cur.execute("DELETE FROM player_id_map;")
    rows = [(bid, names[bid], pid, how) for bid, (pid, how) in result.items()]
    execute_values(cur, "INSERT INTO player_id_map (bbref_id, player_name, nba_player_id, match_method) VALUES %s;", rows)
    conn.commit()
    conn.close()

    resolved = {bid: pid for bid, (pid, _) in result.items() if pid is not None}
    wanted = {bid for bid, _ in bbref_names}
    print(f"player_id_map: {len(resolved)} of {len(result)} Basketball-Reference players matched; "
          f"{len(wanted - set(resolved))} of the {len(wanted)} with pre-{SEASON_CUTOFF} seasons left unmatched")
    return resolved


def load_and_merge():
    per_game = pd.read_csv(PER_GAME_CSV)
    adv = pd.read_csv(ADVANCED_CSV)

    per_game = per_game[(per_game["season"] < SEASON_CUTOFF) & (per_game["lg"] == "NBA")].copy()
    adv = adv[(adv["season"] < SEASON_CUTOFF) & (adv["lg"] == "NBA")].copy()

    # Prefer the combined "NTM" row for a player traded mid-season, so each
    # (player_id, season) ends up with exactly one row, matching this
    # table's primary key.
    def pick_one_row_per_player_season(df):
        df = df.copy()
        df["_is_combined"] = df["team"].astype(str).str.match(r"^\d+TM$")
        df = df.sort_values("_is_combined", ascending=False)
        return df.drop_duplicates(subset=["player_id", "season"], keep="first").drop(columns=["_is_combined"])

    per_game = pick_one_row_per_player_season(per_game)
    adv = pick_one_row_per_player_season(adv)

    df = per_game.merge(
        adv[["player_id", "season", "ts_percent", "usg_percent", "ast_percent",
             "orb_percent", "trb_percent", "tov_percent", "obpm", "dbpm", "bpm", "vorp"]],
        on=["player_id", "season"], how="left",
    )

    bbref_names = set(zip(df["player_id"], df["player"]))
    id_map = build_player_id_map(bbref_names)
    df["nba_player_id"] = df["player_id"].map(id_map)
    unmatched_n = df["nba_player_id"].isna().sum()
    df = df.dropna(subset=["nba_player_id"])
    print(f"{unmatched_n} player-seasons dropped (no confident nba_api player_id match).")

    df = df.drop(columns=["player_id"])  # bbref slug id — superseded by nba_player_id below
    df["nba_player_id"] = df["nba_player_id"].astype("Int64")
    for pct_col in ["usg_percent", "ast_percent", "orb_percent", "trb_percent", "tov_percent"]:
        df[pct_col] = df[pct_col] / 100.0

    rename = {
        "nba_player_id": "player_id", "player": "player_name", "team": "team_abbreviation",
        "age": "age", "g": "gp", "mp_per_game": "min", "pts_per_game": "pts",
        "trb_per_game": "reb", "ast_per_game": "ast", "stl_per_game": "stl",
        "blk_per_game": "blk", "tov_per_game": "tov", "fg_percent": "fg_pct",
        "x3p_percent": "fg3_pct", "ft_percent": "ft_pct", "ts_percent": "ts_pct",
        "usg_percent": "usg_pct", "ast_percent": "ast_pct", "trb_percent": "reb_pct",
        "fg_per_game": "fgm", "fga_per_game": "fga", "x3p_per_game": "fg3m",
        "x3pa_per_game": "fg3a", "ft_per_game": "ftm", "fta_per_game": "fta",
        "orb_per_game": "oreb", "drb_per_game": "dreb", "e_fg_percent": "efg_pct",
        "orb_percent": "oreb_pct", "tov_percent": "tov_pct", "pf_per_game": "pf",
        "obpm": "obpm", "dbpm": "dbpm", "bpm": "bpm", "vorp": "vorp",
    }
    df = df.rename(columns=rename)
    df["age"] = df["age"].astype("Int64")
    df["gp"] = df["gp"].astype("Int64")
    df = df.dropna(subset=["player_id", "player_name", "team_abbreviation"])
    return df[INSERT_COLUMNS]


def backup_existing():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql_query(
        "SELECT * FROM player_season_stats WHERE season < %s;", conn, params=(SEASON_CUTOFF,)
    )
    conn.close()
    if not df.empty:
        path = os.path.join(BACKUP_DIR, f"player_season_stats_pre_{SEASON_CUTOFF}_before_reload.csv")
        df.to_csv(path, index=False)
        print(f"Backed up {len(df)} existing season<{SEASON_CUTOFF} rows to {path} before replacing them.")
    return len(df)


def save(df):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM player_season_stats WHERE season < %s;", (SEASON_CUTOFF,))

    values = [tuple(row[c] if pd.notna(row[c]) else None for c in INSERT_COLUMNS) for _, row in df.iterrows()]
    execute_values(
        cur,
        f"INSERT INTO player_season_stats ({', '.join(INSERT_COLUMNS)}) VALUES %s;",
        values,
    )
    conn.commit()
    conn.close()
    return len(values)


if __name__ == "__main__":
    print(f"Loading NBA seasons before {SEASON_CUTOFF} from Kaggle's 1947-present dataset into player_season_stats...")
    existing = backup_existing()

    df = load_and_merge()
    print(f"Merged {len(df)} player-seasons after ID resolution and trade-row dedup.")

    n = save(df)
    print(f"Saved {n} rows for seasons < {SEASON_CUTOFF}.")
    print(f"\nplayer_season_stats now covers back to season {SEASON_CUTOFF - 1} and earlier (previously started at {SEASON_CUTOFF}).")
    print("Columns left NULL for these rows (no Kaggle equivalent): w_pct, plus_minus, off_rating, "
          "def_rating, net_rating, poss, impact_score(s), bpm_position.")
    print("\nNext (optional): run compute_impact_score.py / upgrade_impact_scores.py to backfill "
          "impact scores for these seasons too — they recompute across the whole table.")
