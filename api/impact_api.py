"""
impact_api.py
==============
FastAPI backend for NBA Impact Score queries.

Endpoints:
    GET /impact/raw/{season}                — Top 20 by raw impact
    GET /impact/star/{season}               — Top 20 by star impact
    GET /impact/player/{player_name}/{season} — Both scores for a player

Usage:
    uvicorn impact_api:app --port 8002 --reload
"""

from contextlib import contextmanager
import hashlib
import html
import json
import math
import os
import pickle
import random
import re
import ssl
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote
from urllib.request import Request, urlopen
import certifi
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg2 import pool
from scipy.optimize import brentq
from scipy.stats import pearsonr

import shots_lib

load_dotenv()
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# This Python.framework install doesn't ship a populated default CA trust
# store, so plain urlopen() against some HTTPS hosts (e.g. cdn.nba.com) fails
# with CERTIFICATE_VERIFY_FAILED even though curl/requests on the same
# machine work fine — pass certifi's bundle explicitly everywhere we open a
# raw urllib HTTPS connection.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# ─── App Setup ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="NBA Impact Score API",
    description="Query raw and star impact scores for NBA player-seasons.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Database Connection Pool ───────────────────────────────────────────────

from db_config import DB_CONFIG

DB_POOL = pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)

TEAM_META = {
    "ATL": {"name": "Atlanta Hawks", "conference": "eastern"},
    "BOS": {"name": "Boston Celtics", "conference": "eastern"},
    "BKN": {"name": "Brooklyn Nets", "conference": "eastern"},
    "CHA": {"name": "Charlotte Hornets", "conference": "eastern"},
    "CHI": {"name": "Chicago Bulls", "conference": "eastern"},
    "CLE": {"name": "Cleveland Cavaliers", "conference": "eastern"},
    "DAL": {"name": "Dallas Mavericks", "conference": "western"},
    "DEN": {"name": "Denver Nuggets", "conference": "western"},
    "DET": {"name": "Detroit Pistons", "conference": "eastern"},
    "GSW": {"name": "Golden State Warriors", "conference": "western"},
    "HOU": {"name": "Houston Rockets", "conference": "western"},
    "IND": {"name": "Indiana Pacers", "conference": "eastern"},
    "LAC": {"name": "Los Angeles Clippers", "conference": "western"},
    "LAL": {"name": "Los Angeles Lakers", "conference": "western"},
    "MEM": {"name": "Memphis Grizzlies", "conference": "western"},
    "MIA": {"name": "Miami Heat", "conference": "eastern"},
    "MIL": {"name": "Milwaukee Bucks", "conference": "eastern"},
    "MIN": {"name": "Minnesota Timberwolves", "conference": "western"},
    "NOP": {"name": "New Orleans Pelicans", "conference": "western"},
    "NYK": {"name": "New York Knicks", "conference": "eastern"},
    "OKC": {"name": "Oklahoma City Thunder", "conference": "western"},
    "ORL": {"name": "Orlando Magic", "conference": "eastern"},
    "PHI": {"name": "Philadelphia 76ers", "conference": "eastern"},
    "PHX": {"name": "Phoenix Suns", "conference": "western"},
    "POR": {"name": "Portland Trail Blazers", "conference": "western"},
    "SAC": {"name": "Sacramento Kings", "conference": "western"},
    "SAS": {"name": "San Antonio Spurs", "conference": "western"},
    "TOR": {"name": "Toronto Raptors", "conference": "eastern"},
    "UTA": {"name": "Utah Jazz", "conference": "western"},
    "WAS": {"name": "Washington Wizards", "conference": "eastern"},
}

# Real team IDs from nba_api's bundled static teams list (not a live fetch —
# same source the frontend's teamAssets.js already uses for logos).
try:
    from nba_api.stats.static import teams as _nba_static_teams
    TEAM_ABBR_TO_ID = {t["abbreviation"]: t["id"] for t in _nba_static_teams.get_teams()}
except Exception:
    TEAM_ABBR_TO_ID = {}

_CACHE = {
    "team_badges": {"ts": 0, "data": {}},
    "standings_bdl": {},  # key: season -> {"ts": ..., "data": ...}
    "player_images": {},  # key: normalized_name -> {"ts": ..., "url": ...}
    "championship_odds": {"ts": 0, "data": None},
    "playoff_stats": {},  # key: season -> {"ts": ..., "data": {name_lower: row_dict}}
    "helio_pt_stats": {},  # key: season -> {"ts": ..., "data": {name_lower: row_dict}}
    "lineup_chemistry": {},  # key: season -> {"ts": ..., "data": [lineup_dict, ...]}
    "team_game_log": {},  # key: (team_id, season) -> {"ts": ..., "data": [game_dict, ...]}
    "player_game_log": {},  # key: (player_id, season) -> {"ts": ..., "data": set(game_id)}
}
_CACHE_TTL_SECONDS = 6 * 60 * 60

TEAM_NAME_TO_ABBR = {info["name"]: abbr for abbr, info in TEAM_META.items()}


@contextmanager
def get_db():
    conn = DB_POOL.getconn()
    try:
        yield conn
    finally:
        DB_POOL.putconn(conn)


shots_lib.ensure_schema()


# ─── Win% Model (loaded once at startup, used by /trade/simulate) ──────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_WIN_MODEL_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "win_model.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "models", "win_model.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "scripts", "win_model.pkl"),
]
_WIN_SCALER_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "win_scaler.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "models", "win_scaler.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "scripts", "win_scaler.pkl"),
]


def _load_first_existing(candidates):
    for path in candidates:
        resolved = os.path.abspath(path)
        if os.path.exists(resolved):
            with open(resolved, "rb") as f:
                return pickle.load(f)
    return None


WIN_MODEL = _load_first_existing(_WIN_MODEL_CANDIDATES)
WIN_SCALER = _load_first_existing(_WIN_SCALER_CANDIDATES)
WIN_MODEL_FEATURES = ["net_rating", "ts_pct"]


def predict_win_pct(net_rating, ts_pct):
    """None if the model artifacts aren't present (e.g. before anyone has
    run scripts/train_win_model.py) rather than raising — trade simulation
    should still work without win% predictions."""
    if WIN_MODEL is None or WIN_SCALER is None or net_rating is None or ts_pct is None:
        return None
    X = WIN_SCALER.transform([[net_rating, ts_pct]])
    return float(WIN_MODEL.predict(X)[0])


# ─── Pair Synergy Model (loaded once at startup, used by Fit Analysis) ──────

_PAIR_SYNERGY_MODEL_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "pair_synergy_model.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "scripts", "pair_synergy_model.pkl"),
]
_PAIR_SYNERGY_SCALER_CANDIDATES = [
    os.path.join(_SCRIPT_DIR, "pair_synergy_scaler.pkl"),
    os.path.join(_SCRIPT_DIR, "..", "scripts", "pair_synergy_scaler.pkl"),
]
PAIR_SYNERGY_MODEL = _load_first_existing(_PAIR_SYNERGY_MODEL_CANDIDATES)
PAIR_SYNERGY_SCALER = _load_first_existing(_PAIR_SYNERGY_SCALER_CANDIDATES)
PAIR_SYNERGY_ARCHETYPES = [
    "3-and-D Wing", "Bench Role Player", "Elite Two-Way Big",
    "Playmaker", "Primary Scorer", "Rim Protector",
]
PAIR_SYNERGY_NUMERIC_FEATURES = ["usg_pct", "tpar", "ast_pct", "reb_pct", "dbpm"]


# ─── Clutch WPA model (shared with scripts/compute_wpa.py via wpa_lib) ──────

_SCRIPTS_DIR = os.path.join(_SCRIPT_DIR, "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from wpa_lib import CLUTCH_MARGIN as WPA_CLUTCH_MARGIN
from wpa_lib import CLUTCH_SECONDS as WPA_CLUTCH_SECONDS
from wpa_lib import load_model as _load_wpa_model
from wpa_lib import seconds_elapsed as wpa_seconds_elapsed
from wpa_lib import win_prob as wpa_win_prob

try:
    WPA_MODEL, WPA_SCALER = _load_wpa_model()
except Exception:
    WPA_MODEL, WPA_SCALER = None, None


# ─── Helpers ────────────────────────────────────────────────────────────────

def check_season_exists(cursor, season: int):
    cursor.execute(
        "SELECT COUNT(*) FROM player_season_stats WHERE season = %s;",
        (season,),
    )
    if cursor.fetchone()[0] == 0:
        raise HTTPException(status_code=404, detail=f"No data for season {season}.")


def get_latest_season(cursor) -> int:
    cursor.execute("SELECT MAX(season) FROM player_season_stats;")
    season = cursor.fetchone()[0]
    if season is None:
        raise HTTPException(status_code=404, detail="No season data available in database.")
    return int(season)


def get_current_nba_season() -> int:
    """
    Returns season end year for the current NBA season.
    Example:
      - Nov 2025 -> 2026
      - Apr 2026 -> 2026
    """
    now = datetime.utcnow()
    return now.year + 1 if now.month >= 10 else now.year


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        LIMIT 1;
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def fetch_json(url: str, headers=None, timeout: int = 20):
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, headers=None, timeout: int = 20):
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def clean_html_text(value: str) -> str:
    """
    Strip HTML tags/entities from feed fields to keep UI text clean.
    """
    text = html.unescape((value or "").strip())
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_team_badges():
    now = time.time()
    cached = _CACHE["team_badges"]
    if cached["data"] and (now - cached["ts"] < _CACHE_TTL_SECONDS):
        return cached["data"]

    badges = {}
    try:
        data = fetch_json(
            "https://www.thesportsdb.com/api/v1/json/123/search_all_teams.php?l=NBA"
        )
        for team in data.get("teams", []) or []:
            badge = team.get("strBadge")
            short = (team.get("strTeamShort") or "").upper().strip()
            full_name = (team.get("strTeam") or "").strip()
            if badge:
                if short in TEAM_META:
                    badges[short] = badge
                for abbr, meta in TEAM_META.items():
                    if meta["name"].lower() == full_name.lower():
                        badges[abbr] = badge
    except Exception:
        # No hard fail; UI can continue without badges.
        pass

    _CACHE["team_badges"] = {"ts": now, "data": badges}
    return badges


def fetch_balldontlie_standings(season: int):
    cache_key = str(season)
    now = time.time()
    cache_item = _CACHE["standings_bdl"].get(cache_key)
    if cache_item and (now - cache_item["ts"] < _CACHE_TTL_SECONDS):
        return cache_item["data"]

    api_key = os.getenv("BALLDONTLIE_API_KEY")
    if not api_key:
        return None

    try:
        # Endpoint availability depends on BallDontLie plan/version.
        payload = fetch_json(
            f"https://api.balldontlie.io/v1/standings?season={season}",
            headers={"Authorization": api_key},
        )
        rows = payload.get("data", [])
        east = []
        west = []
        for row in rows:
            team = row.get("team", {})
            abbr = (team.get("abbreviation") or "").upper()
            conference = (row.get("conference") or "").lower()
            entry = {
                "rank": int(row.get("conference_rank") or 0),
                "abbr": abbr,
                "team": TEAM_META.get(abbr, {}).get("name") or team.get("full_name") or abbr,
                "w": int(row.get("wins") or 0),
                "l": int(row.get("losses") or 0),
                "pct": f".{int(round(float(row.get('win_pct') or 0) * 1000)):03d}",
                "gb": str(row.get("games_behind") or "-"),
                "last10": row.get("last_ten") or "-",
                "streak": row.get("streak") or "-",
            }
            if conference.startswith("east"):
                east.append(entry)
            elif conference.startswith("west"):
                west.append(entry)

        east = sorted(east, key=lambda x: x["rank"] or 99)
        west = sorted(west, key=lambda x: x["rank"] or 99)
        parsed = {"eastern": east, "western": west}
        _CACHE["standings_bdl"][cache_key] = {"ts": now, "data": parsed}
        return parsed
    except Exception:
        return None


def fetch_nba_api_standings(season: int):
    """
    Fetch accurate conference standings using nba_api endpoint.
    Returns {'eastern': [...], 'western': [...]} or None on failure.
    """
    try:
        from nba_api.stats.endpoints import leaguestandingsv3

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguestandingsv3.LeagueStandingsV3(
            league_id="00",
            season=season_label,
            season_type="Regular Season",
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []
        if not result_sets:
            return None

        rows = result_sets[0].get("rowSet", []) or []
        headers = result_sets[0].get("headers", []) or []
        idx = {name: i for i, name in enumerate(headers)}

        def val(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        east = []
        west = []
        for row in rows:
            conf_raw = str(val(row, "Conference", "")).lower()
            team_name = f"{val(row, 'TeamCity', '')} {val(row, 'TeamName', '')}".strip()
            # LeagueStandingsV3's real response has no abbreviation/tricode
            # column at all (only TeamID/TeamCity/TeamName/TeamSlug) — derive
            # it from the full team name instead of a column that doesn't exist.
            abbr = TEAM_NAME_TO_ABBR.get(team_name, "")
            wins = int(val(row, "WINS", 0) or 0)
            losses = int(val(row, "LOSSES", 0) or 0)
            pct_val = float(val(row, "WinPCT", 0) or 0)
            rank = int(val(row, "PlayoffRank", 0) or 0)
            gb_val = val(row, "ConferenceGamesBack", "-")
            last10 = val(row, "L10", "-") or "-"
            streak = val(row, "strCurrentStreak", "-") or "-"

            item = {
                "rank": rank,
                "abbr": abbr,
                "team": TEAM_META.get(abbr, {}).get("name") or team_name or abbr,
                "w": wins,
                "l": losses,
                "pct": f".{int(round(pct_val * 1000)):03d}",
                "gb": str(gb_val),
                "last10": str(last10),
                "streak": str(streak),
            }

            if conf_raw.startswith("east"):
                east.append(item)
            elif conf_raw.startswith("west"):
                west.append(item)

        east = sorted(east, key=lambda x: x["rank"] or 99)
        west = sorted(west, key=lambda x: x["rank"] or 99)
        if not east and not west:
            return None
        return {"eastern": east, "western": west}
    except Exception:
        return None


def fetch_nba_cdn_standings():
    """
    Fallback live standings source from NBA CDN.
    """
    try:
        data = fetch_json(
            "https://cdn.nba.com/static/json/liveData/standings/leagueStandings.json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        rows = (((data or {}).get("leagueStandings") or {}).get("teams") or [])
        if not rows:
            return None

        east = []
        west = []
        for row in rows:
            abbr = str(row.get("teamTricode") or "").upper()
            conf = str(row.get("conferenceName") or "").lower()
            wins = int(row.get("wins", 0) or 0)
            losses = int(row.get("losses", 0) or 0)
            pct_val = float(row.get("winPct", 0) or 0)
            rank = int(row.get("confRank", 0) or 0)
            gb_val = row.get("gamesBehind", "-")
            streak_w = int(row.get("streak", 0) or 0)
            streak_type = str(row.get("streakCode") or "").upper()
            streak = f"{streak_type}{streak_w}" if streak_type in ("W", "L") else "-"

            item = {
                "rank": rank,
                "abbr": abbr,
                "team": TEAM_META.get(abbr, {}).get("name") or abbr,
                "w": wins,
                "l": losses,
                "pct": f".{int(round(pct_val * 1000)):03d}",
                "gb": str(gb_val),
                "last10": "-",
                "streak": streak,
            }
            if conf.startswith("east"):
                east.append(item)
            elif conf.startswith("west"):
                west.append(item)

        east = sorted(east, key=lambda x: x["rank"] or 99)
        west = sorted(west, key=lambda x: x["rank"] or 99)
        if not east and not west:
            return None
        return {"eastern": east, "western": west}
    except Exception:
        return None


def fetch_nba_api_player_leaders(stat_key: str, season: int, top_n: int = 10):
    """
    Live leaders from nba_api leaguedashplayerstats (per-game regular season).
    """
    stat_map = {
        "pts": ("PTS", "PTS", False),
        "reb": ("REB", "REB", False),
        "ast": ("AST", "AST", False),
        "dreb": ("DREB", "DREB", False),
        "oreb": ("OREB", "OREB", False),
        "plus_minus": ("PLUS_MINUS", "+/-", False),
        "stl": ("STL", "STL", False),
        "blk": ("BLK", "BLK", False),
        "tov": ("TOV", "TOV", False),
        "fg_pct": ("FG_PCT", "FG%", True),
        "fg3_pct": ("FG3_PCT", "3P%", True),
        "ft_pct": ("FT_PCT", "FT%", True),
        "fg3m": ("FG3M", "3PM", False),
    }
    if stat_key not in stat_map:
        return None

    try:
        from nba_api.stats.endpoints import leaguedashplayerstats

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season_label,
            season_type_all_star="Regular Season",
            per_mode_detailed="PerGame",
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []
        if not result_sets:
            return None
        rs = result_sets[0]
        headers = rs.get("headers", []) or []
        rows = rs.get("rowSet", []) or []
        idx = {name: i for i, name in enumerate(headers)}

        stat_col, stat_label, is_pct = stat_map[stat_key]
        if stat_col not in idx:
            return None

        def get_val(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        cleaned = []
        for row in rows:
            player_id = get_val(row, "PLAYER_ID", None)
            player_name = str(get_val(row, "PLAYER_NAME", "") or "")
            team_abbr = str(get_val(row, "TEAM_ABBREVIATION", "") or "")
            stat_val = get_val(row, stat_col, None)
            if not player_name or not team_abbr or stat_val is None:
                continue
            value = float(stat_val)
            if is_pct and value <= 1:
                value *= 100
            cleaned.append((player_id, player_name, team_abbr, round(value, 2)))

        cleaned.sort(key=lambda x: x[3], reverse=True)
        top_rows = cleaned[:max(1, min(int(top_n), 50))]
        return {
            "season": int(season),
            "stat_key": stat_key,
            "stat_label": stat_label,
            "results": [
                {
                    "rank": i + 1,
                    "player_id": r[0],
                    "player_name": r[1],
                    "team_abbr": r[2],
                    "value": r[3],
                }
                for i, r in enumerate(top_rows)
            ],
        }
    except Exception:
        return None


def fetch_nba_api_team_stats(season: int):
    """
    Live team per-game stats for team comparison.
    """
    try:
        from nba_api.stats.endpoints import leaguedashteamstats

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashteamstats.LeagueDashTeamStats(
            season=season_label,
            season_type_all_star="Regular Season",
            per_mode_detailed="PerGame",
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []
        if not result_sets:
            return None
        rs = result_sets[0]
        headers = rs.get("headers", []) or []
        rows = rs.get("rowSet", []) or []
        idx = {name: i for i, name in enumerate(headers)}

        def get_val(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        out = {}
        for row in rows:
            abbr = str(get_val(row, "TEAM_ABBREVIATION", "") or "").upper()
            if not abbr or abbr not in TEAM_META:
                continue
            fg = float(get_val(row, "FG_PCT", 0) or 0)
            fg3 = float(get_val(row, "FG3_PCT", 0) or 0)
            ft = float(get_val(row, "FT_PCT", 0) or 0)
            if fg <= 1:
                fg *= 100
            if fg3 <= 1:
                fg3 *= 100
            if ft <= 1:
                ft *= 100

            out[abbr] = {
                "name": TEAM_META[abbr]["name"],
                "abbr": abbr,
                "ppg": round(float(get_val(row, "PTS", 0) or 0), 1),
                "rpg": round(float(get_val(row, "REB", 0) or 0), 1),
                "apg": round(float(get_val(row, "AST", 0) or 0), 1),
                "spg": round(float(get_val(row, "STL", 0) or 0), 1),
                "bpg": round(float(get_val(row, "BLK", 0) or 0), 1),
                "fgPct": round(fg, 1),
                "threePct": round(fg3, 1),
                "ftPct": round(ft, 1),
            }

        return out or None
    except Exception:
        return None


def _normalize_search_text(text: str) -> str:
    """Lowercase + strip accents so 'jokic' matches 'Jokić'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return ascii_only.lower().strip()


def fetch_nba_api_player_search(query: str, limit: int = 25):
    """
    Live player-name autocomplete from current season player stats endpoint.
    """
    q = _normalize_search_text(query)
    if len(q) < 2:
        return []
    try:
        season = get_current_nba_season()
        leaders_payload = fetch_nba_api_player_leaders("pts", season, top_n=500)
        if not leaders_payload:
            return []
        names = []
        seen = set()
        for row in leaders_payload.get("results", []):
            name = (row.get("player_name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        filtered = [n for n in names if q in _normalize_search_text(n)]
        return filtered[: max(1, min(int(limit), 50))]
    except Exception:
        return []


def fetch_nba_api_player_profile(player_name: str, season: int):
    """
    Live player profile from nba_api.
    """
    try:
        from nba_api.stats.endpoints import leaguedashplayerstats

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season_label,
            season_type_all_star="Regular Season",
            per_mode_detailed="PerGame",
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []
        if not result_sets:
            return None
        rs = result_sets[0]
        headers = rs.get("headers", []) or []
        rows = rs.get("rowSet", []) or []
        idx = {name: i for i, name in enumerate(headers)}

        target = (player_name or "").strip().lower()
        if not target:
            return None

        def get_val(row, key, default=None):
            i = idx.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        matched = None
        for row in rows:
            name = str(get_val(row, "PLAYER_NAME", "") or "").strip()
            if name.lower() == target:
                matched = row
                break
        if matched is None:
            for row in rows:
                name = str(get_val(row, "PLAYER_NAME", "") or "").strip().lower()
                if target in name:
                    matched = row
                    break
        if matched is None:
            return None

        fg = float(get_val(matched, "FG_PCT", 0) or 0)
        fg3 = float(get_val(matched, "FG3_PCT", 0) or 0)
        ft = float(get_val(matched, "FT_PCT", 0) or 0)
        if fg <= 1:
            fg *= 100
        if fg3 <= 1:
            fg3 *= 100
        if ft <= 1:
            ft *= 100

        return {
            "player_id": int(get_val(matched, "PLAYER_ID", 0) or 0),
            "player_name": str(get_val(matched, "PLAYER_NAME", "") or ""),
            "team_abbr": str(get_val(matched, "TEAM_ABBREVIATION", "") or ""),
            "season": int(season),
            "age": float(get_val(matched, "AGE", 0) or 0),
            "min": float(get_val(matched, "MIN", 0) or 0),
            "stats": {
                "ppg": round(float(get_val(matched, "PTS", 0) or 0), 1),
                "rpg": round(float(get_val(matched, "REB", 0) or 0), 1),
                "apg": round(float(get_val(matched, "AST", 0) or 0), 1),
                "spg": round(float(get_val(matched, "STL", 0) or 0), 1),
                "bpg": round(float(get_val(matched, "BLK", 0) or 0), 1),
                "fgPct": round(fg, 1),
                "threePct": round(fg3, 1),
                "ftPct": round(ft, 1),
            },
        }
    except Exception:
        return None


# ─── Playoff Drop-off Forecaster ─────────────────────────────────────────────
#
# Compares a player's real regular-season advanced stats (already in
# player_season_stats) against their real playoff advanced stats for the
# same season (live-fetched from nba_api, season_type_all_star="Playoffs").
# One real, well-known effect: playoff defenses scheme specifically for a
# team's few best options, minutes get more concentrated onto fewer
# players, and possessions slow down — some players' efficiency holds up
# under that and some collapses. This surfaces the real before/after
# numbers rather than predicting anything: no regression, no invented
# "playoff tax" formula, just what actually happened, with the real
# playoff sample size (often well under 20 games) shown prominently since
# small samples are genuinely noisy.

PLAYOFF_COMPARISON_STATS = [
    ("ts_pct", "TS%"), ("usg_pct", "USG%"), ("net_rating", "Net Rtg"),
    ("ast_pct", "AST%"), ("reb_pct", "REB%"),
]


def _fetch_playoff_stats_season(season: int):
    """Live-fetch ALL players' real playoff advanced stats for one season in
    a single request (fast, ~0.5s for the whole league), cached by season."""
    cached = _CACHE["playoff_stats"].get(season)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguedashplayerstats

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season_label,
            season_type_all_star="Playoffs",
            per_mode_detailed="PerGame",
            measure_type_detailed_defense="Advanced",
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []
        rows = result_sets[0].get("rowSet", []) if result_sets else []
        headers = result_sets[0].get("headers", []) if result_sets else []
        idx = {name: i for i, name in enumerate(headers)}

        def val(row, key, default=None):
            i = idx.get(key)
            return row[i] if i is not None and i < len(row) else default

        by_name = {}
        for row in rows:
            name = str(val(row, "PLAYER_NAME", "") or "").strip()
            if not name:
                continue
            by_name[name.lower()] = {
                "player_id": int(val(row, "PLAYER_ID", 0) or 0),
                "player_name": name,
                "team_abbreviation": str(val(row, "TEAM_ABBREVIATION", "") or ""),
                "gp": int(val(row, "GP", 0) or 0),
                "min": float(val(row, "MIN", 0) or 0),
                "ts_pct": float(val(row, "TS_PCT", 0) or 0),
                "usg_pct": float(val(row, "USG_PCT", 0) or 0),
                "net_rating": float(val(row, "NET_RATING", 0) or 0),
                "ast_pct": float(val(row, "AST_PCT", 0) or 0),
                "reb_pct": float(val(row, "REB_PCT", 0) or 0),
            }
    except Exception:
        by_name = {}

    _CACHE["playoff_stats"][season] = {"ts": time.time(), "data": by_name}
    return by_name


@app.get("/players/playoff-comparison/{player_name}")
def get_playoff_comparison(player_name: str, season: int):
    """Real regular-season vs. real playoff advanced stats for one player-
    season, side by side. 404s honestly if the player's team didn't make
    the playoffs that season, or the player didn't appear — that's real
    information too, not something to paper over."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT team_abbreviation, gp, min, pts, ts_pct, usg_pct, net_rating, ast_pct, reb_pct
            FROM player_season_stats
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"No regular-season data for {resolved_name} in season {season}.")

    regular = {
        "team_abbreviation": row[0], "gp": row[1], "min": round(row[2], 1) if row[2] is not None else None,
        "pts": round(row[3], 1) if row[3] is not None else None,
        "ts_pct": row[4], "usg_pct": row[5], "net_rating": row[6], "ast_pct": row[7], "reb_pct": row[8],
    }

    playoff_by_name = _fetch_playoff_stats_season(season)
    playoff = playoff_by_name.get(resolved_name.lower())

    if not playoff:
        return {
            "player_id": player_id,
            "player_name": resolved_name,
            "season": season,
            "regular_season": regular,
            "playoffs": None,
            "note": f"{resolved_name}'s team did not make the playoffs in season {season}, "
                    f"or they did not appear in a playoff game — no real playoff data exists for this comparison.",
        }

    deltas = {}
    for key, _ in PLAYOFF_COMPARISON_STATS:
        r_val, p_val = regular.get(key), playoff.get(key)
        deltas[key] = round(p_val - r_val, 4) if r_val is not None and p_val is not None else None

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "regular_season": regular,
        "playoffs": playoff,
        "deltas": deltas,
        "small_sample_warning": playoff["gp"] < 10,
    }


# ─── Draft Prospect Comp Finder ──────────────────────────────────────────────
#
# Real D1 college stats (CollegeBasketballData.com, ~105k player-seasons,
# 2014-2025, fetched once into college_player_season_stats — see
# scripts/fetch_college_stats.py) matched by real name to this project's own
# NBA rookie seasons wherever a match exists (~55% of NBA rookies 2015-2026 —
# international players and G-League/draft-and-stash players never appear in
# US college data, which is a real gap, not a bug). For a given prospect,
# finds their closest real college-season comps (z-scored within that
# season's own real pool, same era-normalization approach used everywhere
# else in this project) among players whose own real NBA rookie outcome is
# known, and shows what those comps actually did — a similarity-weighted
# average of real outcomes, never a trained or invented projection.
#
# College-side metrics (CBBD's own usage/netRating/etc.) are a DIFFERENT
# methodology than this project's own NBA-side metrics of the same name —
# they're used only to compare college seasons to other college seasons,
# never mixed into the same z-score space as an NBA stat.

COLLEGE_COMP_FEATURES = ["ppg", "apg", "rpg", "usage", "ts_pct", "net_rating", "porpag"]
COLLEGE_MIN_GAMES = 10
BRIDGE_MIN_GAMES = 15


def _college_features(games, points, assists, rebounds_total, usage, ts_pct, net_rating, porpag):
    if not games:
        return None
    return {
        "ppg": points / games if points is not None else None,
        "apg": assists / games if assists is not None else None,
        "rpg": rebounds_total / games if rebounds_total is not None else None,
        "usage": usage, "ts_pct": ts_pct, "net_rating": net_rating, "porpag": porpag,
    }


def _combine_measurement_features(row):
    """row = (wingspan, height_wo_shoes, standing_reach), all real inches
    from the real NBA Draft Combine. Returns None if any is missing —
    never guessed."""
    if row is None:
        return None
    wingspan, height, reach = row
    if wingspan is None or height is None or reach is None:
        return None
    return {"wingspan_minus_height": wingspan - height, "standing_reach": reach}


@app.get("/prospects/comp/{player_name}")
def get_draft_prospect_comp(
    player_name: str,
    season: Optional[int] = None,
    top_n_comps: int = 5,
    include_measurements: bool = False,
):
    """Real college-season comps + their real NBA rookie outcomes for one prospect.

    include_measurements=true adds real wingspan-minus-height and real
    standing reach (from the NBA Draft Combine, z-scored within the
    prospect's own real draft-class combine pool — the same "normalize
    against the query's own real pool" approach already used for the
    7 core college stats above) to the comparison vector. Only the query
    prospect and comps that were actually measured at a real combine
    participate when this is on; everyone else is excluded rather than
    silently compared on partial data.
    """
    top_n_comps = max(1, min(top_n_comps, 10))

    with get_db() as conn:
        cursor = conn.cursor()

        if season is not None:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) = LOWER(%s) AND season = %s LIMIT 1;""",
                (player_name, season),
            )
        else:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) = LOWER(%s) ORDER BY season DESC LIMIT 1;""",
                (player_name,),
            )
        prospect_row = cursor.fetchone()
        if not prospect_row:
            cursor.execute(
                """SELECT season, athlete_id, name, team, games, points, assists, rebounds_total,
                          usage, ts_pct, net_rating, porpag
                   FROM college_player_season_stats
                   WHERE LOWER(name) LIKE LOWER(%s) ORDER BY season DESC LIMIT 1;""",
                (f"%{player_name}%",),
            )
            prospect_row = cursor.fetchone()

        if not prospect_row:
            raise HTTPException(status_code=404, detail=f"No real college season found for '{player_name}'.")

        (p_season, p_athlete_id, p_name, p_team, p_games, p_pts, p_ast,
         p_reb, p_usage, p_ts, p_net, p_porpag) = prospect_row

        if not p_games or p_games < 5:
            raise HTTPException(status_code=404, detail=f"{p_name}'s {p_season} college sample is too small (<5 games) for a real comparison.")

        cursor.execute(
            """SELECT athlete_id, name, team, games, points, assists, rebounds_total,
                      usage, ts_pct, net_rating, porpag
               FROM college_player_season_stats
               WHERE season = %s AND games >= %s;""",
            (p_season, COLLEGE_MIN_GAMES),
        )
        pool_rows = cursor.fetchall()

        cursor.execute(
            """
            WITH rookies AS (
                SELECT player_id, player_name, MIN(season) AS rookie_season
                FROM player_season_stats GROUP BY player_id, player_name
            )
            SELECT c.athlete_id, c.name, c.season, c.team, c.games, c.points, c.assists,
                   c.rebounds_total, c.usage, c.ts_pct, c.net_rating, c.porpag,
                   r.player_id, r.rookie_season
            FROM rookies r
            JOIN college_player_season_stats c
                ON LOWER(c.name) = LOWER(r.player_name) AND c.season = r.rookie_season - 1
            WHERE c.games >= %s;
            """,
            (BRIDGE_MIN_GAMES,),
        )
        bridge_rows = cursor.fetchall()

        bridge_player_ids = list({b[12] for b in bridge_rows}) or [-1]
        cursor.execute(
            """SELECT player_id, season, pts, ts_pct, ast_pct, reb_pct, net_rating
               FROM player_season_stats WHERE player_id = ANY(%s);""",
            (bridge_player_ids,),
        )
        nba_by_id_season = {
            (pid, szn): {"pts": pts, "ts_pct": ts, "ast_pct": ast, "reb_pct": reb, "net_rating": net}
            for pid, szn, pts, ts, ast, reb, net in cursor.fetchall()
        }

        # If this prospect has since been drafted and appears in the NBA
        # data too, grab their real player_id for a real headshot — purely
        # cosmetic, doesn't affect the comparison math at all.
        cursor.execute(
            "SELECT DISTINCT player_id FROM player_season_stats WHERE LOWER(player_name) = LOWER(%s) LIMIT 1;",
            (p_name,),
        )
        prospect_nba_row = cursor.fetchone()
        prospect_nba_player_id = prospect_nba_row[0] if prospect_nba_row else None

        # Real combine measurements — draft_year lines up with college_season
        # (a player's last college season and their real draft year are the
        # same integer in this project's convention; verified against the
        # existing bridge-pool join above, which already relies on this).
        # Always fetched (not just when include_measurements=True) so the
        # prospect's own real measurements can be shown informationally
        # even when the toggle comparing on them is off.
        cursor.execute(
            """SELECT wingspan, height_wo_shoes, standing_reach, weight, max_vertical_leap
               FROM draft_combine WHERE draft_year = %s AND LOWER(player_name) = LOWER(%s) LIMIT 1;""",
            (p_season, p_name),
        )
        prospect_combine_row = cursor.fetchone()

        combine_pool_rows = []
        combine_by_name_year = {}
        if include_measurements:
            cursor.execute(
                """SELECT wingspan, height_wo_shoes, standing_reach
                   FROM draft_combine
                   WHERE draft_year = %s AND wingspan IS NOT NULL
                         AND height_wo_shoes IS NOT NULL AND standing_reach IS NOT NULL;""",
                (p_season,),
            )
            combine_pool_rows = cursor.fetchall()

            cursor.execute(
                """SELECT player_name, draft_year, wingspan, height_wo_shoes, standing_reach
                   FROM draft_combine
                   WHERE wingspan IS NOT NULL AND height_wo_shoes IS NOT NULL AND standing_reach IS NOT NULL;"""
            )
            combine_by_name_year = {
                (name.lower(), yr): (wingspan, height, reach)
                for name, yr, wingspan, height, reach in cursor.fetchall()
            }

    pool_features = []
    for athlete_id, name, team, games, points, assists, reb, usage, ts, net, porpag in pool_rows:
        f = _college_features(games, points, assists, reb, usage, ts, net, porpag)
        if f and all(f.get(k) is not None for k in COLLEGE_COMP_FEATURES):
            pool_features.append(f)

    if len(pool_features) < 10:
        raise HTTPException(status_code=404, detail=f"Not enough real college data for season {p_season} to build a comparison pool.")

    means, stds = {}, {}
    for k in COLLEGE_COMP_FEATURES:
        vals = [f[k] for f in pool_features]
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        means[k], stds[k] = m, sd

    def zvec(f):
        return [(f[k] - means[k]) / stds[k] for k in COLLEGE_COMP_FEATURES]

    MEASUREMENT_FEATURES = ["wingspan_minus_height", "standing_reach"]
    measure_means, measure_stds = {}, {}
    prospect_measure_vec = []
    if include_measurements:
        measure_pool = [
            _combine_measurement_features((w, h, r)) for w, h, r in combine_pool_rows
        ]
        measure_pool = [m for m in measure_pool if m is not None]
        if len(measure_pool) < 10:
            raise HTTPException(
                status_code=404,
                detail=f"Not enough real combine measurements for draft class {p_season} to build a comparison pool.",
            )
        for k in MEASUREMENT_FEATURES:
            vals = [m[k] for m in measure_pool]
            mm = sum(vals) / len(vals)
            sd = (sum((v - mm) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
            measure_means[k], measure_stds[k] = mm, sd

        prospect_measure = _combine_measurement_features(
            prospect_combine_row[:3] if prospect_combine_row else None
        )
        if prospect_measure is None:
            raise HTTPException(
                status_code=404,
                detail=f"No real combine measurements for {p_name} — try without include_measurements.",
            )
        prospect_measure_vec = [(prospect_measure[k] - measure_means[k]) / measure_stds[k] for k in MEASUREMENT_FEATURES]

    def measure_zvec(name, c_season):
        combine_row = combine_by_name_year.get((name.lower(), c_season))
        m = _combine_measurement_features(combine_row)
        if m is None:
            return None
        return [(m[k] - measure_means[k]) / measure_stds[k] for k in MEASUREMENT_FEATURES]

    prospect_features = _college_features(p_games, p_pts, p_ast, p_reb, p_usage, p_ts, p_net, p_porpag)
    if not prospect_features or any(prospect_features.get(k) is None for k in COLLEGE_COMP_FEATURES):
        raise HTTPException(status_code=404, detail=f"{p_name}'s {p_season} season is missing real stats needed for comparison.")
    prospect_vec = zvec(prospect_features) + prospect_measure_vec

    scored = []
    for (athlete_id, name, c_season, team, games, points, assists, reb,
         usage, ts, net, porpag, nba_pid, rookie_season) in bridge_rows:
        if athlete_id == p_athlete_id and c_season == p_season:
            continue
        f = _college_features(games, points, assists, reb, usage, ts, net, porpag)
        if not f or any(f.get(k) is None for k in COLLEGE_COMP_FEATURES):
            continue
        nba_outcome = nba_by_id_season.get((nba_pid, rookie_season))
        if not nba_outcome:
            continue
        comp_vec = zvec(f)
        if include_measurements:
            m_vec = measure_zvec(name, c_season)
            if m_vec is None:
                continue  # No real combine data for this comp — excluded, not guessed.
            comp_vec = comp_vec + m_vec
        dist = sum((a - b) ** 2 for a, b in zip(prospect_vec, comp_vec)) ** 0.5
        scored.append({
            "name": name, "college_season": c_season, "team": team, "nba_player_id": nba_pid,
            "distance": dist, "nba_rookie_season": rookie_season, "nba_outcome": nba_outcome,
        })

    scored.sort(key=lambda x: x["distance"])
    bridge_pool_size = len(scored)
    top_comps = scored[:top_n_comps]

    if not top_comps:
        raise HTTPException(status_code=404, detail="No real comps with known NBA rookie outcomes were found for this prospect.")

    weights = [1 / (1 + c["distance"]) for c in top_comps]
    projected = {}
    for stat in ["pts", "ts_pct", "ast_pct", "reb_pct", "net_rating"]:
        pairs = [(c["nba_outcome"].get(stat), w) for c, w in zip(top_comps, weights) if c["nba_outcome"].get(stat) is not None]
        projected[stat] = round(sum(v * w for v, w in pairs) / sum(w for _, w in pairs), 3) if pairs else None

    combine_measurements = None
    if prospect_combine_row:
        c_wingspan, c_height, c_reach, c_weight, c_vertical = prospect_combine_row
        combine_measurements = {
            "wingspan": c_wingspan, "height_wo_shoes": c_height, "standing_reach": c_reach,
            "weight": c_weight, "max_vertical_leap": c_vertical,
        }

    return {
        "prospect": {
            "name": p_name, "college_season": p_season, "team": p_team, "games": p_games,
            "nba_player_id": prospect_nba_player_id,
            "ppg": round(prospect_features["ppg"], 1), "apg": round(prospect_features["apg"], 1),
            "rpg": round(prospect_features["rpg"], 1), "usage": prospect_features["usage"],
            "ts_pct": prospect_features["ts_pct"], "net_rating": prospect_features["net_rating"],
            "combine_measurements": combine_measurements,
        },
        "comps": [
            {
                "name": c["name"], "college_season": c["college_season"], "team": c["team"],
                "nba_player_id": c["nba_player_id"],
                "similarity": round(1 / (1 + c["distance"]), 4),
                "nba_rookie_season": c["nba_rookie_season"],
                "nba_rookie_outcome": {k: (round(v, 3) if v is not None else None) for k, v in c["nba_outcome"].items()},
            }
            for c in top_comps
        ],
        "projected_nba_rookie_outcome": projected,
        "bridge_pool_size": bridge_pool_size,
        "measurements_included": include_measurements,
        "measurements_note": (
            "Comps are also matched on real wingspan-minus-height and real standing reach from the NBA "
            "Draft Combine, z-scored within this prospect's own real draft-class combine pool. Only "
            "the query prospect and comps who were actually measured at a real combine participate — "
            "not every drafted player attends, so this narrows the comp pool to real combine attendees."
            if include_measurements else
            "Comparison is on real college stats only. Add include_measurements=true to also match on "
            "real wingspan/standing reach from the NBA Draft Combine (narrows to real combine attendees only)."
        ),
    }


# ─── Does Length Matter? (real correlation study) ───────────────────────────
#
# Real wingspan-minus-height (NBA Draft Combine) vs. real career defensive
# production (player_season_stats, minutes-weighted career average) for
# every real player who has both — joined directly on the real NBA
# player_id (draft_combine and player_season_stats share the same ID
# space, verified directly before writing this). A real Pearson
# correlation, not a claim of causation, with n always disclosed.

LENGTH_STUDY_MIN_TOTAL_MINUTES = 500


@app.get("/draft/length-study")
def get_length_study():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            -- A real player can attend the real combine more than once (e.g.
            -- an underclassman testing again in a later year) — dedupe to
            -- their single most recent real measurement before joining, so
            -- each player contributes exactly one real point to the study.
            WITH latest_combine AS (
                SELECT DISTINCT ON (player_id) player_id, player_name, wingspan, height_wo_shoes
                FROM draft_combine
                WHERE wingspan IS NOT NULL AND height_wo_shoes IS NOT NULL
                ORDER BY player_id, draft_year DESC
            )
            SELECT dc.player_id, dc.player_name, dc.wingspan, dc.height_wo_shoes,
                   SUM(p.dbpm * p.min * p.gp) / NULLIF(SUM(p.min * p.gp), 0) AS avg_dbpm,
                   (SUM(p.blk * p.gp) + SUM(p.stl * p.gp)) / NULLIF(SUM(p.min * p.gp), 0) * 36 AS stocks_per36,
                   SUM(p.min * p.gp) AS total_minutes
            FROM latest_combine dc
            JOIN player_season_stats p ON p.player_id = dc.player_id
            WHERE p.dbpm IS NOT NULL AND p.min IS NOT NULL AND p.gp IS NOT NULL
            GROUP BY dc.player_id, dc.player_name, dc.wingspan, dc.height_wo_shoes
            HAVING SUM(p.min * p.gp) >= %s;
            """,
            (LENGTH_STUDY_MIN_TOTAL_MINUTES,),
        )
        rows = cursor.fetchall()

    if len(rows) < 10:
        raise HTTPException(status_code=404, detail="Not enough real players with both combine and career defensive data yet.")

    points = [
        {
            "player_id": r[0], "player_name": r[1],
            "wingspan_minus_height": round(r[2] - r[3], 2),
            "avg_dbpm": round(r[4], 3),
            "stocks_per36": round(r[5], 2),
        }
        for r in rows
    ]

    wmh = [p["wingspan_minus_height"] for p in points]
    dbpm = [p["avg_dbpm"] for p in points]
    stocks = [p["stocks_per36"] for p in points]

    r_dbpm, p_dbpm = pearsonr(wmh, dbpm)
    r_stocks, p_stocks = pearsonr(wmh, stocks)

    return {
        "n": len(points),
        "min_total_minutes": LENGTH_STUDY_MIN_TOTAL_MINUTES,
        "points": points,
        "correlations": {
            "wingspan_minus_height_vs_dbpm": {"r": round(float(r_dbpm), 3), "p_value": round(float(p_dbpm), 4)},
            "wingspan_minus_height_vs_stocks_per36": {"r": round(float(r_stocks), 3), "p_value": round(float(p_stocks), 4)},
        },
        "methodology": (
            f"Real wingspan-minus-height (NBA Draft Combine) vs. real career-average Defensive Box Plus-Minus "
            f"and real career-average steals+blocks per 36 minutes (minutes-weighted across each real player's "
            f"whole real career, player_season_stats), for {len(points)} real players with at least "
            f"{LENGTH_STUDY_MIN_TOTAL_MINUTES} real career minutes and real combine measurements. A real Pearson "
            "correlation coefficient, not a causal claim — length is one real input among many real factors "
            "(effort, positioning, IQ) that drive real defensive production."
        ),
    }


# ─── Heliocentricity Index ───────────────────────────────────────────────────
#
# How much of a team's real offense runs through one player. Every input is
# real, live NBA tracking data (touches, real time of possession, real usage
# and assist rate already in this project's DB) — no fabricated "what if
# they sat out" simulation, which would require inventing an effect size
# with nothing real to fit it against (the same reasoning that ruled out a
# few other proposed features this session). The index itself is a simple,
# fully disclosed average of real percentile ranks — the same kind of
# transparent weighted composite this project's own Impact Score already
# uses, not a trained or validated model.

def _fetch_pt_possession_stats(season: int):
    """Real per-player touch/possession tracking data for a whole season, one
    request for the whole league (~1.5s), cached like the other league-wide
    live fetches in this file."""
    cached = _CACHE["helio_pt_stats"].get(season)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguedashptstats

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashptstats.LeagueDashPtStats(
            season=season_label,
            season_type_all_star="Regular Season",
            per_mode_simple="PerGame",
            player_or_team="Player",
            pt_measure_type="Possessions",
            timeout=30,
        )
        data = endpoint.get_dict()
        rs = data.get("resultSets", [{}])[0]
        headers, rows = rs.get("headers", []), rs.get("rowSet", [])
        idx = {h: i for i, h in enumerate(headers)}

        def val(row, key, default=None):
            i = idx.get(key)
            return row[i] if i is not None and i < len(row) else default

        by_name = {}
        team_totals = {}
        for row in rows:
            name = str(val(row, "PLAYER_NAME", "") or "").strip()
            team = str(val(row, "TEAM_ABBREVIATION", "") or "")
            top = float(val(row, "TIME_OF_POSS", 0) or 0)
            if not name:
                continue
            by_name[name.lower()] = {
                "player_id": int(val(row, "PLAYER_ID", 0) or 0),
                "player_name": name,
                "team_abbreviation": team,
                "touches": float(val(row, "TOUCHES", 0) or 0),
                "time_of_poss": top,
                "avg_sec_per_touch": float(val(row, "AVG_SEC_PER_TOUCH", 0) or 0),
                "pts_per_touch": float(val(row, "PTS_PER_TOUCH", 0) or 0),
            }
            team_totals[team] = team_totals.get(team, 0.0) + top

        for row in by_name.values():
            team_top = team_totals.get(row["team_abbreviation"]) or 1.0
            row["time_of_poss_share"] = round(100 * row["time_of_poss"] / team_top, 1)
    except Exception:
        by_name = {}

    _CACHE["helio_pt_stats"][season] = {"ts": time.time(), "data": by_name}
    return by_name


@app.get("/players/heliocentricity")
def get_heliocentricity_leaderboard(season: Optional[int] = None, top_n: int = 25):
    """Real touches/time-of-possession-share/usage%/assist% for this
    season's qualified pool, ranked by a disclosed equal-weighted average
    of each stat's real percentile rank."""
    top_n = max(1, min(top_n, 100))

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        check_season_exists(cursor, resolved_season)
        cursor.execute(
            """
            SELECT player_id, player_name, team_abbreviation, usg_pct, ast_pct
            FROM player_season_stats
            WHERE season = %s AND min >= %s AND gp >= %s
              AND usg_pct IS NOT NULL AND ast_pct IS NOT NULL;
            """,
            (resolved_season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        db_rows = cursor.fetchall()

    pt_stats = _fetch_pt_possession_stats(resolved_season)
    if not pt_stats:
        raise HTTPException(status_code=502, detail="Live touch/possession tracking data is unavailable right now.")

    combined = []
    for player_id, player_name, team_abbr, usg_pct, ast_pct in db_rows:
        pt = pt_stats.get(player_name.lower())
        if not pt:
            continue
        combined.append({
            "player_id": player_id, "player_name": player_name, "team_abbreviation": team_abbr,
            "usg_pct": usg_pct, "ast_pct": ast_pct,
            "touches": pt["touches"], "time_of_poss_share": pt["time_of_poss_share"],
            "avg_sec_per_touch": pt["avg_sec_per_touch"], "pts_per_touch": pt["pts_per_touch"],
        })

    if len(combined) < 10:
        raise HTTPException(status_code=404, detail=f"Not enough matched players to build a leaderboard for season {resolved_season}.")

    pools = {k: [r[k] for r in combined] for k in ("time_of_poss_share", "touches", "usg_pct", "ast_pct")}
    for r in combined:
        pcts = [_percentile_rank(r[k], pools[k]) for k in ("time_of_poss_share", "touches", "usg_pct", "ast_pct")]
        r["percentiles"] = {
            "time_of_poss_share": pcts[0], "touches": pcts[1], "usg_pct": pcts[2], "ast_pct": pcts[3],
        }
        r["heliocentricity_index"] = round(sum(pcts) / len(pcts), 1)

    combined.sort(key=lambda r: r["heliocentricity_index"], reverse=True)

    return {
        "season": resolved_season,
        "pool_size": len(combined),
        "methodology": (
            "heliocentricity_index is the simple average of four real percentile ranks within this season's "
            "qualified pool (min>=15 mpg, gp>=20): real time-of-possession share of the player's own team "
            "(live NBA tracking data), real touches per game, real usage%, and real assist%. Equal weights, "
            "fully disclosed — not a trained or fitted model, the same kind of transparent composite this "
            "project's own Impact Score already uses."
        ),
        "results": [
            {**r, "rank": i + 1} for i, r in enumerate(combined[:top_n])
        ],
    }


# ─── Clutch-Time Win Probability Added (WPA) Tracker ────────────────────────
#
# A real win-probability model (Logistic Regression, same library and same
# interpretable-coefficients approach as this project's MVP/DPOY/ROY models)
# trained on real play-by-play — real running score, real game clock, real
# final winner — for a real sample of games (scripts/fetch_play_by_play.py,
# scripts/train_wpa_model.py). WPA per play = P(home wins) after the play
# minus before it, from the perspective of whichever team's player made
# that play (scripts/compute_wpa.py). Clutch time uses the NBA's own real
# definition: final 5 minutes of regulation/OT with the score within 5
# points. This is real data engineering end to end — nothing here is an
# invented coefficient, the whole point of building the model was to fit
# real weights against real outcomes instead of guessing them.
#
# The sample size (games actually fetched, not a full season) is always
# returned alongside the leaderboard so results are never presented as more
# comprehensive than they are.

@app.get("/players/clutch-wpa")
def get_clutch_wpa_leaderboard(top_n: int = 25, min_clutch_plays: int = 3):
    top_n = max(1, min(top_n, 100))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_wpa_totals');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="WPA data hasn't been computed yet — run scripts/fetch_play_by_play.py, "
                       "train_wpa_model.py, then compute_wpa.py.",
            )
        cursor.execute("SELECT COUNT(DISTINCT game_id) FROM pbp_games;")
        n_games_sample = cursor.fetchone()[0]

        cursor.execute(
            """SELECT w.person_id, COALESCE(MAX(p.player_name), w.player_name) AS full_name,
                      w.team_abbreviation, w.n_games, w.n_plays, w.total_wpa, w.clutch_wpa, w.clutch_plays
               FROM player_wpa_totals w
               LEFT JOIN player_season_stats p ON p.player_id = w.person_id
               WHERE w.clutch_plays >= %s
               GROUP BY w.person_id, w.player_name, w.team_abbreviation, w.n_games, w.n_plays,
                        w.total_wpa, w.clutch_wpa, w.clutch_plays
               ORDER BY w.clutch_wpa DESC LIMIT %s;""",
            (min_clutch_plays, top_n),
        )
        rows = cursor.fetchall()

    return {
        "sample_size_games": n_games_sample,
        "methodology": (
            "Real win-probability model (Logistic Regression) trained on real play-by-play from a real sample "
            f"of {n_games_sample} games this season — not the full season, disclosed here rather than implied. "
            "clutch_wpa sums each real play's real win-probability swing (model output after the play minus "
            "before it) across every play in real 'clutch time' (final 5 min of regulation/OT, score within 5 "
            "points), attributed to whichever player made the play. This is the model's real output on real "
            "data, not an invented formula."
        ),
        "results": [
            {
                "rank": i + 1, "player_id": r[0], "player_name": r[1], "team_abbreviation": r[2],
                "n_games": r[3], "n_plays": r[4], "total_wpa": r[5],
                "clutch_wpa": r[6], "clutch_plays": r[7],
            }
            for i, r in enumerate(rows)
        ],
    }


# ─── Game Win-Probability Replay ─────────────────────────────────────────────
#
# Replays a real game's real play-by-play through the same real WPA model
# used by the Clutch WPA leaderboard (scripts/wpa_lib.py — the exact same
# code, not a re-implementation), producing a real win-probability curve
# for that one game. The "what if" endpoint recomputes the curve assuming
# one real missed shot had gone in instead — a clearly-labeled counter-
# factual, not a claim about what actually would have happened.

def _wpa_model_required():
    if WPA_MODEL is None or WPA_SCALER is None:
        raise HTTPException(
            status_code=503,
            detail="WPA model isn't available — run scripts/train_wpa_model.py first.",
        )


@app.get("/games/wp-replay/list")
def get_wp_replay_list(season: int = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.pbp_games');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="No play-by-play data yet — run scripts/fetch_play_by_play.py first.",
            )
        if season is None:
            cursor.execute("SELECT MAX(season) FROM pbp_games;")
            season = cursor.fetchone()[0]
        resolved_season = season
        cursor.execute(
            """
            WITH last_events AS (
                SELECT DISTINCT ON (game_id) game_id, score_home, score_away
                FROM pbp_events
                ORDER BY game_id, action_number DESC
            )
            SELECT g.game_id, g.game_date, g.home_team, g.away_team, g.home_win,
                   le.score_home, le.score_away
            FROM pbp_games g
            JOIN last_events le ON le.game_id = g.game_id
            WHERE g.season = %s
            ORDER BY g.game_date DESC;
            """,
            (resolved_season,),
        )
        rows = cursor.fetchall()

    return {
        "season": resolved_season,
        "games": [
            {
                "game_id": r[0],
                "game_date": r[1].isoformat() if r[1] else None,
                "home_team": r[2],
                "away_team": r[3],
                "home_win": r[4],
                "final_score": {"home": r[5], "away": r[6]},
            }
            for r in rows
        ],
    }


def _fetch_game_events(cursor, game_id: str):
    # action_number alone isn't a safe unique key: nba_api's real feed
    # sometimes assigns the same action_number to two simultaneous events
    # (e.g. a blocked shot's "Missed Shot" and the "Block" row it paired
    # with) — id (the table's own primary key) is what's actually unique,
    # so callers use that to reference one specific event unambiguously.
    cursor.execute(
        """SELECT id, action_number, period, seconds_remaining, score_home, score_away,
                  team_tricode, person_id, player_name, action_type, sub_type, description
           FROM pbp_events WHERE game_id = %s ORDER BY action_number, id;""",
        (game_id,),
    )
    return cursor.fetchall()


@app.get("/games/wp-replay/{game_id}")
def get_wp_replay(game_id: str):
    _wpa_model_required()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT game_date, home_team, away_team, home_win FROM pbp_games WHERE game_id = %s;",
            (game_id,),
        )
        game_row = cursor.fetchone()
        if not game_row:
            raise HTTPException(status_code=404, detail=f"No play-by-play found for game {game_id}.")
        game_date, home_team, away_team, home_win = game_row

        events = _fetch_game_events(cursor, game_id)

    if not events:
        raise HTTPException(status_code=404, detail=f"No play-by-play events found for game {game_id}.")

    points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for event_id, action_number, period, secs, score_home, score_away, team_tricode, person_id, player_name, action_type, sub_type, description in events:
        margin = score_home - score_away
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        points.append({
            "event_id": event_id,
            "action_number": action_number,
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "period": period,
            "margin": margin,
            "home_wp": round(wp_home, 4),
            "wpa": round(wp_home - prev_wp_home, 4),
            "action_type": action_type,
            "sub_type": sub_type,
            "description": description,
            "player_name": player_name,
            "team_tricode": team_tricode,
            "is_missed_shot": action_type == "Missed Shot",
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    top_plays = sorted(points, key=lambda p: abs(p["wpa"]), reverse=True)[:5]
    last_home_score = events[-1][4]
    last_away_score = events[-1][5]

    return {
        "game_id": game_id,
        "game_date": game_date.isoformat() if game_date else None,
        "home_team": home_team,
        "away_team": away_team,
        "home_win": home_win,
        "final_score": {"home": last_home_score, "away": last_away_score},
        "methodology": (
            "Every real play-by-play event from this real game, run through the same real trained "
            "win-probability model used by the Clutch WPA leaderboard. home_wp is the model's real "
            "output (probability the home team wins) after that play; wpa is the real swing from the "
            "previous play, from the home team's perspective."
        ),
        "points": points,
        "top_plays": top_plays,
    }


@app.get("/games/wp-replay/{game_id}/whatif")
def get_wp_replay_whatif(game_id: str, event_id: int):
    _wpa_model_required()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT home_team, away_team FROM pbp_games WHERE game_id = %s;",
            (game_id,),
        )
        game_row = cursor.fetchone()
        if not game_row:
            raise HTTPException(status_code=404, detail=f"No play-by-play found for game {game_id}.")
        home_team, away_team = game_row

        events = _fetch_game_events(cursor, game_id)

    if not events:
        raise HTTPException(status_code=404, detail=f"No play-by-play events found for game {game_id}.")

    target_idx = next((i for i, e in enumerate(events) if e[0] == event_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail=f"No event {event_id} in game {game_id}.")

    target = events[target_idx]
    _, target_action_number, _, _, _, _, target_team, _, target_player, target_action_type, target_sub_type, target_description = target
    if target_action_type != "Missed Shot":
        raise HTTPException(
            status_code=400,
            detail="What-if is only supported for a real missed field goal (action_type == 'Missed Shot').",
        )

    points_awarded = 3 if "3PT" in (target_description or "") else 2
    shift_home = points_awarded if target_team == home_team else 0
    shift_away = points_awarded if target_team == away_team else 0

    cf_points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for i, (event_id_i, action_number_i, period, secs, score_home, score_away, *_rest) in enumerate(events):
        shifted = i >= target_idx
        margin = (score_home + (shift_home if shifted else 0)) - (score_away + (shift_away if shifted else 0))
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        cf_points.append({
            "event_id": event_id_i,
            "action_number": action_number_i,
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "home_wp": round(wp_home, 4),
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    return {
        "game_id": game_id,
        "event_id": event_id,
        "action_number": target_action_number,
        "shooter": target_player,
        "team_tricode": target_team,
        "points_awarded": points_awarded,
        "original_description": target_description,
        "counterfactual_label": f"What if this shot had gone in? (+{points_awarded} for {target_team})",
        "disclaimer": (
            "This is a counterfactual, not a re-simulation: it assumes every later play in the real game "
            "happens exactly as it really did, just with the score shifted from this shot onward. It doesn't "
            "account for how players or coaches might have actually played differently with a different score."
        ),
        "points": cf_points,
    }


# ─── Games: Guess the Game ───────────────────────────────────────────────────
#
# Same deterministic-daily-seed pattern as Guess the Player: no server-side
# session, the "mystery" real game is re-derived from a hash of the date on
# every request, so any request (daily/guess/reveal) is stateless and always
# agrees on the same real answer for that date. Reuses the real WPA replay
# infrastructure above (_fetch_game_events, the real trained model) rather
# than re-implementing win-probability scoring.

GUESS_THE_GAME_MAX_GUESSES = 3


def _guess_the_game_pool(cursor):
    cursor.execute(
        """
        WITH last_events AS (
            SELECT DISTINCT ON (game_id) game_id, score_home, score_away
            FROM pbp_events
            ORDER BY game_id, action_number DESC
        )
        SELECT g.game_id, g.season, g.game_date, g.home_team, g.away_team,
               le.score_home, le.score_away
        FROM pbp_games g
        JOIN last_events le ON le.game_id = g.game_id
        ORDER BY g.game_id ASC;
        """
    )
    cols = ["game_id", "season", "game_date", "home_team", "away_team", "score_home", "score_away"]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _guess_the_game_mystery(pool_rows, puzzle_date: date):
    seed = f"guess-the-game-{puzzle_date.isoformat()}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    index = int(digest, 16) % len(pool_rows)
    return pool_rows[index]


def _downsample_points(points, target: int = 100):
    if len(points) <= target:
        return points
    step = len(points) / target
    indices = sorted({min(len(points) - 1, int(i * step)) for i in range(target)})
    if indices[-1] != len(points) - 1:
        indices.append(len(points) - 1)
    return [points[i] for i in indices]


@app.get("/games/guess-the-game/daily")
def get_guess_the_game_daily(puzzle_date: Optional[str] = None):
    """Today's puzzle: a real completed game's downsampled real win-
    probability curve, with no team names or date — just the shape of how
    the game actually unfolded."""
    _wpa_model_required()
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.pbp_games');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(
                status_code=503,
                detail="No play-by-play data yet — run scripts/fetch_play_by_play.py first.",
            )
        pool_rows = _guess_the_game_pool(cursor)
        if not pool_rows:
            raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

        mystery = _guess_the_game_mystery(pool_rows, resolved_date)
        events = _fetch_game_events(cursor, mystery["game_id"])

    points = []
    prev_secs, prev_margin = 2880.0, 0
    prev_wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, prev_secs, prev_margin)
    for _event_id, _action_number, period, secs, score_home, score_away, *_rest in events:
        margin = score_home - score_away
        wp_home = wpa_win_prob(WPA_MODEL, WPA_SCALER, max(secs, 0), margin)
        points.append({
            "seconds_elapsed": wpa_seconds_elapsed(period, secs),
            "home_wp": round(wp_home, 4),
        })
        prev_secs, prev_margin, prev_wp_home = secs, margin, wp_home

    return {
        "puzzle_date": resolved_date.isoformat(),
        "max_guesses": GUESS_THE_GAME_MAX_GUESSES,
        "points": _downsample_points(points, 100),
        "methodology": (
            "The real win-probability curve (home team's perspective, from the real trained WPA model) "
            "for one real completed game, downsampled to about 100 points. Team names and the date are "
            "withheld until you guess or run out of guesses."
        ),
    }


@app.get("/games/guess-the-game/guess")
def guess_the_game(team: str, attempt_number: int, puzzle_date: Optional[str] = None):
    """One guess = one real team abbreviation. Each wrong guess reveals the
    next clue in a fixed order (season, then final margin, then one of the
    two real teams) — a correct guess ends the puzzle immediately."""
    attempt_number = max(1, min(attempt_number, GUESS_THE_GAME_MAX_GUESSES))
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        pool_rows = _guess_the_game_pool(cursor)
    if not pool_rows:
        raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

    mystery = _guess_the_game_mystery(pool_rows, resolved_date)
    guess_abbr = team.strip().upper()
    correct = guess_abbr in (mystery["home_team"], mystery["away_team"])
    guesses_remaining = GUESS_THE_GAME_MAX_GUESSES - attempt_number

    result = {
        "attempt_number": attempt_number,
        "correct": correct,
        "guesses_remaining": max(0, guesses_remaining),
    }

    if correct:
        result["mystery_game"] = {
            "game_id": mystery["game_id"],
            "season": mystery["season"],
            "game_date": mystery["game_date"].isoformat() if mystery["game_date"] else None,
            "home_team": mystery["home_team"],
            "away_team": mystery["away_team"],
            "final_score": {"home": mystery["score_home"], "away": mystery["score_away"]},
        }
        return result

    if attempt_number == 1:
        result["clue"] = {"type": "season", "value": mystery["season"]}
    elif attempt_number == 2:
        result["clue"] = {"type": "final_margin", "value": abs(mystery["score_home"] - mystery["score_away"])}
    else:
        result["clue"] = {"type": "one_team", "value": mystery["home_team"]}

    return result


@app.get("/games/guess-the-game/reveal")
def reveal_guess_the_game(puzzle_date: Optional[str] = None):
    """Full reveal once a player is out of guesses."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        pool_rows = _guess_the_game_pool(cursor)
    if not pool_rows:
        raise HTTPException(status_code=503, detail="No completed games with play-by-play available.")

    mystery = _guess_the_game_mystery(pool_rows, resolved_date)
    return {
        "game_id": mystery["game_id"],
        "season": mystery["season"],
        "game_date": mystery["game_date"].isoformat() if mystery["game_date"] else None,
        "home_team": mystery["home_team"],
        "away_team": mystery["away_team"],
        "final_score": {"home": mystery["score_home"], "away": mystery["score_away"]},
    }


# ─── Lineup Chemistry (real 5-man unit on-court performance) ────────────────
#
# Real 5-man lineup combinations and their real on-court Offensive/Defensive/
# Net Rating, fetched live from nba_api's LeagueDashLineups (the NBA's own
# real lineup data, not a simulation). This is the honest substitute for a
# "Trade Chemistry Simulator": rather than inventing a usage-redistribution
# formula for lineups that have never actually played together, it shows
# how real lineups that HAVE actually shared the floor have actually
# performed — real minutes, real possessions, real outcomes.
#
# Lineups with very little shared floor time are extremely noisy (a 3-minute
# sample can produce a wild net rating that means nothing), so a real
# min_minutes cutoff is applied and always disclosed rather than hidden.

def _fetch_lineup_stats_season(season: int, group_quantity: int = 5):
    """Live-fetch every real lineup combination of the given size (5 for
    full lineups, 2 for pairs) for a season in one request (~1.5-4s for
    the whole league), cached like the other live fetches in this file."""
    cache_key = (group_quantity, season)
    cached = _CACHE["lineup_chemistry"].get(cache_key)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguedashlineups

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguedashlineups.LeagueDashLineups(
            group_quantity=group_quantity,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="Totals",
            season=season_label,
            season_type_all_star="Regular Season",
            timeout=45,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live lineup data fetch failed: {exc}")

    lineups = []
    for _, row in df.iterrows():
        player_ids = [int(pid) for pid in str(row["GROUP_ID"]).split("-") if pid]
        abbr_names = [n.strip() for n in str(row["GROUP_NAME"]).split(" - ") if n.strip()]
        lineups.append({
            "player_ids": player_ids,
            "abbr_names": abbr_names,
            "team_abbreviation": row["TEAM_ABBREVIATION"],
            "gp": int(row["GP"]),
            "min": float(row["MIN"]),
            "off_rating": float(row["OFF_RATING"]),
            "def_rating": float(row["DEF_RATING"]),
            "net_rating": float(row["NET_RATING"]),
            "ast_pct": float(row["AST_PCT"]),
            "ts_pct": float(row["TS_PCT"]),
            "pace": float(row["PACE"]),
        })

    _CACHE["lineup_chemistry"][cache_key] = {"ts": time.time(), "data": lineups}
    return lineups


@app.get("/lineups/chemistry")
def get_lineup_chemistry(season: int = None, min_minutes: float = 40, top_n: int = 15, order: str = "best"):
    top_n = max(1, min(top_n, 50))
    order = order if order in ("best", "worst") else "best"

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        check_season_exists(cursor, resolved_season)

        lineups = _fetch_lineup_stats_season(resolved_season)
        qualified = [l for l in lineups if l["min"] >= min_minutes]

        all_ids = {pid for l in qualified for pid in l["player_ids"]}
        name_map = {}
        if all_ids:
            cursor.execute(
                """SELECT DISTINCT ON (player_id) player_id, player_name
                   FROM player_season_stats
                   WHERE player_id = ANY(%s)
                   ORDER BY player_id, season DESC;""",
                (list(all_ids),),
            )
            name_map = {r[0]: r[1] for r in cursor.fetchall()}

    qualified.sort(key=lambda l: l["net_rating"], reverse=(order == "best"))
    top = qualified[:top_n]

    results = []
    for i, l in enumerate(top):
        players = [
            {"player_id": pid, "player_name": name_map.get(pid, abbr)}
            for pid, abbr in zip(l["player_ids"], l["abbr_names"])
        ]
        results.append({
            "rank": i + 1,
            "players": players,
            "team_abbreviation": l["team_abbreviation"],
            "gp": l["gp"],
            "min": l["min"],
            "off_rating": l["off_rating"],
            "def_rating": l["def_rating"],
            "net_rating": l["net_rating"],
            "ast_pct": l["ast_pct"],
            "ts_pct": l["ts_pct"],
            "pace": l["pace"],
        })

    return {
        "season": resolved_season,
        "min_minutes": min_minutes,
        "order": order,
        "lineups_qualified": len(qualified),
        "lineups_total": len(lineups),
        "methodology": (
            f"Real 5-man lineup combinations that have actually shared the floor this season, fetched live from "
            f"the NBA's own real lineup data (not a simulation of hypothetical lineups). Only lineups with at "
            f"least {min_minutes:.0f} real shared minutes are shown ({len(qualified)} of {len(lineups)} total "
            "combinations qualify) — lineups with only a few shared minutes produce real but extremely noisy "
            "net ratings, so that noise is filtered out and disclosed here rather than hidden."
        ),
        "results": results,
    }


# ─── Pair Synergy (real-data upgrade to Fit Analysis) ───────────────────────
#
# A real ridge regression (scripts/train_pair_synergy.py) predicting a real
# 2-man pair's "synergy" — their real observed net rating minus the
# minutes-weighted average of each player's own real individual net rating
# — from each player's real z-scored usage/3PA-rate/AST%/REB%/DBPM and real
# statistical archetype. Cross-validated with real season-grouped CV; the
# real R² is disclosed here exactly as the training script reported it,
# honestly, even though it's low — real pair chemistry isn't well predicted
# by these real box-score features alone, and that's a real finding, not a
# bug to paper over. Also checks nba_api live for whether these two real
# players have actually shared the floor this season, and shows their real
# observed pair net rating if so.

def _player_synergy_features(cursor, player_id: int, season: int):
    cursor.execute(
        """SELECT p.net_rating, p.min, p.gp, p.usg_pct, p.fg3a, p.fga,
                  p.ast_pct, p.reb_pct, p.dbpm, c.archetype
           FROM player_season_stats p
           LEFT JOIN player_clusters c ON c.player_id = p.player_id AND c.season = p.season
           WHERE p.player_id = %s AND p.season = %s;""",
        (player_id, season),
    )
    row = cursor.fetchone()
    if not row:
        return None
    net, mn, gp, usg, fg3a, fga, ast, reb, dbpm, archetype = row
    if archetype is None or net is None or mn is None or not gp:
        return None
    tpar = (fg3a / fga) if fga else None
    raw = {"usg_pct": usg, "tpar": tpar, "ast_pct": ast, "reb_pct": reb, "dbpm": dbpm}
    if any(v is None for v in raw.values()):
        return None

    cursor.execute(
        """SELECT p.usg_pct, (p.fg3a::float / NULLIF(p.fga, 0)) AS tpar, p.ast_pct, p.reb_pct, p.dbpm
           FROM player_season_stats p WHERE p.season = %s;""",
        (season,),
    )
    pool = cursor.fetchall()
    z = []
    for i, key in enumerate(PAIR_SYNERGY_NUMERIC_FEATURES):
        vals = [r[i] for r in pool if r[i] is not None]
        if not vals:
            return None
        m = sum(vals) / len(vals)
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        z.append((raw[key] - m) / sd)

    onehot = [1.0 if archetype == a else 0.0 for a in PAIR_SYNERGY_ARCHETYPES]
    return {
        "vec": z + onehot, "net_rating": net, "min": mn * gp, "archetype": archetype,
    }


@app.get("/players/pair-synergy")
def get_pair_synergy(player_a: str, player_b: str, season: Optional[int] = None):
    if PAIR_SYNERGY_MODEL is None or PAIR_SYNERGY_SCALER is None:
        raise HTTPException(status_code=503, detail="Pair synergy model isn't available — run scripts/train_pair_synergy.py first.")

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        pid_a, name_a = find_player(cursor, player_a)
        pid_b, name_b = find_player(cursor, player_b)
        if pid_a == pid_b:
            raise HTTPException(status_code=400, detail="Pick two different players.")

        fa = _player_synergy_features(cursor, pid_a, resolved_season)
        fb = _player_synergy_features(cursor, pid_b, resolved_season)

        cursor.execute(
            "SELECT n_pairs, n_seasons, min_pair_minutes, cv_r2_mean, computed_at FROM pair_synergy_validation ORDER BY id DESC LIMIT 1;"
        )
        validation_row = cursor.fetchone()

    if not fa or not fb:
        raise HTTPException(
            status_code=404,
            detail=f"Missing real qualified-season data for {name_a if not fa else name_b} in season {resolved_season} "
                   "(needs a real archetype + real usage/3PA-rate/AST%/REB%/DBPM on file).",
        )

    ordered = sorted([(pid_a, name_a, fa), (pid_b, name_b, fb)], key=lambda t: t[0])
    (lo_id, lo_name, lo_f), (hi_id, hi_name, hi_f) = ordered

    X = [lo_f["vec"] + hi_f["vec"]]
    X_scaled = PAIR_SYNERGY_SCALER.transform(X)
    predicted_synergy = float(PAIR_SYNERGY_MODEL.predict(X_scaled)[0])

    expected_baseline = (fa["net_rating"] * fa["min"] + fb["net_rating"] * fb["min"]) / (fa["min"] + fb["min"])
    predicted_pair_net_rating = expected_baseline + predicted_synergy

    # Real observed data: have these two actually shared the floor this season?
    observed = None
    try:
        pairs = _fetch_lineup_stats_season(resolved_season, group_quantity=2)
        for p in pairs:
            if set(p["player_ids"]) == {pid_a, pid_b}:
                observed = {
                    "min": p["min"], "net_rating": p["net_rating"],
                    "off_rating": p["off_rating"], "def_rating": p["def_rating"],
                }
                break
    except HTTPException:
        observed = None

    validation = None
    if validation_row:
        validation = {
            "n_pairs": validation_row[0], "n_seasons": validation_row[1],
            "min_pair_minutes": validation_row[2], "cv_r2_mean": round(validation_row[3], 4),
            "computed_at": validation_row[4].isoformat() if validation_row[4] else None,
        }

    return {
        "season": resolved_season,
        "player_a": {"player_id": pid_a, "player_name": name_a, "archetype": fa["archetype"], "net_rating": fa["net_rating"]},
        "player_b": {"player_id": pid_b, "player_name": name_b, "archetype": fb["archetype"], "net_rating": fb["net_rating"]},
        "predicted_synergy": round(predicted_synergy, 2),
        "predicted_pair_net_rating": round(predicted_pair_net_rating, 2),
        "expected_baseline_net_rating": round(expected_baseline, 2),
        "observed": observed,
        "validation": validation,
        "methodology": (
            "predicted_synergy is a real ridge regression's output: the real pair net rating you'd expect ABOVE "
            "the minutes-weighted average of these two real players' own individual real net ratings this "
            "season, based on their real archetypes and real z-scored usage/3PA-rate/AST%/REB%/DBPM. "
            + (
                f"Cross-validated on {validation['n_pairs']} real pairs across {validation['n_seasons']} real "
                f"seasons with real season-grouped CV: R² = {validation['cv_r2_mean']}. "
                "That R² is low — disclosed honestly rather than hidden, because it's a real finding: pair "
                "chemistry isn't well predicted by these real box-score features alone, at least not by this "
                "real model. Treat predicted_synergy as a rough, honestly-uncertain real-data signal, not a "
                "confident prediction. "
                if validation else
                "No stored validation found — run scripts/train_pair_synergy.py to see the real cross-validated R². "
            )
            + (
                "'observed' is these two real players' actual real net rating in real minutes they've actually "
                "shared the floor together this season, live-fetched from the NBA's own data — compare it "
                "directly against the model's prediction when available."
                if observed else
                "These two real players haven't shared the floor together (enough) this season for a real "
                "observed pair net rating — 'observed' is null rather than guessed."
            )
        ),
    }


def fetch_nba_games_by_date(date_str: str):
    """
    Fetch NBA games for a specific date (YYYY-MM-DD) using nba_api scoreboard.
    """
    try:
        from nba_api.stats.endpoints import scoreboardv2

        badges = get_team_badges()
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        game_date = dt.strftime("%m/%d/%Y")
        endpoint = scoreboardv2.ScoreboardV2(
            game_date=game_date,
            league_id="00",
            day_offset=0,
            timeout=45,
        )
        data = endpoint.get_dict()
        result_sets = data.get("resultSets", []) or []

        game_header = None
        line_score = None
        for rs in result_sets:
            name = rs.get("name")
            if name == "GameHeader":
                game_header = rs
            elif name == "LineScore":
                line_score = rs

        if not game_header or not line_score:
            return []

        gh_headers = game_header.get("headers", [])
        gh_rows = game_header.get("rowSet", [])
        ls_headers = line_score.get("headers", [])
        ls_rows = line_score.get("rowSet", [])

        gh_idx = {k: i for i, k in enumerate(gh_headers)}
        ls_idx = {k: i for i, k in enumerate(ls_headers)}

        def safe_value(row, index_map, key, default=None):
            i = index_map.get(key)
            if i is None or i >= len(row):
                return default
            return row[i]

        lines_by_game = {}
        for row in ls_rows:
            game_id = str(row[ls_idx["GAME_ID"]])
            lines_by_game.setdefault(game_id, []).append(row)

        games = []
        for row in gh_rows:
            game_id = str(safe_value(row, gh_idx, "GAME_ID", ""))
            status_text = str(row[gh_idx.get("GAME_STATUS_TEXT", 0)])
            status_num = int(row[gh_idx.get("GAME_STATUS_ID", 0)] or 0)
            game_code = str(row[gh_idx.get("GAMECODE", 0)] or "")

            line_rows = lines_by_game.get(game_id, [])
            if len(line_rows) < 2:
                continue

            # line score rows contain one row per team.
            away_row = None
            home_row = None
            for lr in line_rows:
                if int(safe_value(lr, ls_idx, "TEAM_ID", -1)) == int(safe_value(row, gh_idx, "VISITOR_TEAM_ID", -2)):
                    away_row = lr
                if int(safe_value(lr, ls_idx, "TEAM_ID", -1)) == int(safe_value(row, gh_idx, "HOME_TEAM_ID", -3)):
                    home_row = lr
            if away_row is None or home_row is None:
                continue

            def team_obj(lr):
                abbr = str(safe_value(lr, ls_idx, "TEAM_ABBREVIATION", "") or "")
                city = str(safe_value(lr, ls_idx, "TEAM_CITY_NAME", "") or "")
                nickname = str(safe_value(lr, ls_idx, "TEAM_NICKNAME", "") or "")
                wl = str(safe_value(lr, ls_idx, "TEAM_WINS_LOSSES", "") or "")
                wins = int(wl.split("-")[0]) if "-" in wl else 0
                losses = int(wl.split("-")[1]) if "-" in wl else 0
                pts = safe_value(lr, ls_idx, "PTS", None)
                return {
                    "abbr": abbr,
                    "city": city,
                    "name": nickname,
                    "score": int(pts) if pts is not None else None,
                    "wins": wins,
                    "losses": losses,
                    "logo": badges.get(abbr),
                }

            if status_num == 3:
                status = "FINAL"
            elif status_num == 2:
                status = "LIVE"
            else:
                status = "SCHEDULED"

            games.append(
                {
                    "id": game_id,
                    "game_code": game_code,
                    "status": status,
                    "status_text": status_text,
                    "date": date_str,
                    "away": team_obj(away_row),
                    "home": team_obj(home_row),
                }
            )

        # Fill missing scores from NBA CDN scoreboard fallback.
        cdn_games = fetch_nba_cdn_games_by_date(date_str)
        if cdn_games:
            by_matchup = {
                (
                    (g.get("away", {}) or {}).get("abbr"),
                    (g.get("home", {}) or {}).get("abbr"),
                ): g
                for g in cdn_games
            }
            for g in games:
                if (g.get("away", {}) or {}).get("score") is not None and (g.get("home", {}) or {}).get("score") is not None:
                    continue
                key = (
                    (g.get("away", {}) or {}).get("abbr"),
                    (g.get("home", {}) or {}).get("abbr"),
                )
                cg = by_matchup.get(key)
                if not cg:
                    continue
                if g["away"]["score"] is None:
                    g["away"]["score"] = (cg.get("away", {}) or {}).get("score")
                if g["home"]["score"] is None:
                    g["home"]["score"] = (cg.get("home", {}) or {}).get("score")

        return games
    except Exception:
        # If nba_api is unavailable, fallback fully to CDN.
        return fetch_nba_cdn_games_by_date(date_str)


def fetch_nba_cdn_games_by_date(date_str: str):
    """
    Fallback game schedule/scores from NBA CDN for a given date.
    """
    try:
        badges = get_team_badges()
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        ymd = dt.strftime("%Y%m%d")
        data = fetch_json(
            f"https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_{ymd}.json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        games = (((data or {}).get("scoreboard") or {}).get("games") or [])
        out = []

        def to_int(v):
            try:
                if v is None or v == "":
                    return None
                return int(float(v))
            except Exception:
                return None

        for g in games:
            away = g.get("awayTeam", {}) or {}
            home = g.get("homeTeam", {}) or {}
            away_abbr = (away.get("teamTricode") or "").upper()
            home_abbr = (home.get("teamTricode") or "").upper()
            game_status = int(g.get("gameStatus", 1) or 1)
            if game_status == 3:
                status = "FINAL"
            elif game_status == 2:
                status = "LIVE"
            else:
                status = "SCHEDULED"

            away_score = to_int(away.get("score"))
            home_score = to_int(home.get("score"))

            out.append(
                {
                    "id": str(g.get("gameId") or ""),
                    "game_code": str(g.get("gameCode") or ""),
                    "status": status,
                    "status_text": str(g.get("gameStatusText") or ""),
                    "date": date_str,
                    "away": {
                        "abbr": away_abbr,
                        "city": str(away.get("teamCity") or ""),
                        "name": str(away.get("teamName") or away_abbr),
                        "score": away_score,
                        "wins": to_int(away.get("wins")) or 0,
                        "losses": to_int(away.get("losses")) or 0,
                        "logo": badges.get(away_abbr),
                    },
                    "home": {
                        "abbr": home_abbr,
                        "city": str(home.get("teamCity") or ""),
                        "name": str(home.get("teamName") or home_abbr),
                        "score": home_score,
                        "wins": to_int(home.get("wins")) or 0,
                        "losses": to_int(home.get("losses")) or 0,
                        "logo": badges.get(home_abbr),
                    },
                }
            )
        return out
    except Exception:
        return []


def fetch_boxscore(game_id: str):
    """
    Fetch traditional boxscore for a game_id.
    """
    try:
        from nba_api.stats.endpoints import boxscoretraditionalv2

        endpoint = boxscoretraditionalv2.BoxScoreTraditionalV2(
            game_id=game_id,
            start_period=0,
            end_period=10,
            start_range=0,
            end_range=0,
            range_type=0,
            timeout=45,
        )
        frames = endpoint.get_data_frames()
        if not frames:
            return fetch_boxscore_from_cdn(game_id)

        # Frame 0 is PlayerStats in this endpoint.
        df = frames[0].copy()
        if df.empty:
            return fetch_boxscore_from_cdn(game_id)

        for col in ("TEAM_ID", "PTS", "REB", "AST", "STL", "BLK", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA"):
            if col in df.columns:
                df[col] = df[col].fillna(0)

        df["MIN"] = df["MIN"].fillna("0")
        df["PLAYER_NAME"] = df["PLAYER_NAME"].fillna("")

        team_ids = [tid for tid in df["TEAM_ID"].drop_duplicates().tolist() if tid is not None]
        if len(team_ids) < 2:
            return fetch_boxscore_from_cdn(game_id)

        away_team_id = team_ids[0]
        home_team_id = team_ids[1]

        def to_player_dict(row):
            return {
                "name": str(row.get("PLAYER_NAME", "")),
                "min": str(row.get("MIN", "0")),
                "pts": int(row.get("PTS", 0) or 0),
                "reb": int(row.get("REB", 0) or 0),
                "ast": int(row.get("AST", 0) or 0),
                "stl": int(row.get("STL", 0) or 0),
                "blk": int(row.get("BLK", 0) or 0),
                "fg": f"{int(row.get('FGM', 0) or 0)}-{int(row.get('FGA', 0) or 0)}",
                "three": f"{int(row.get('FG3M', 0) or 0)}-{int(row.get('FG3A', 0) or 0)}",
                "ft": f"{int(row.get('FTM', 0) or 0)}-{int(row.get('FTA', 0) or 0)}",
                "pm": str(row.get("PLUS_MINUS", 0) or 0),
            }

        away_df = df[df["TEAM_ID"] == away_team_id]
        home_df = df[df["TEAM_ID"] == home_team_id]

        away = [to_player_dict(row) for _, row in away_df.iterrows()]
        home = [to_player_dict(row) for _, row in home_df.iterrows()]
        if not away and not home:
            return fetch_boxscore_from_cdn(game_id)
        return {"away": away, "home": home}
    except Exception:
        return fetch_boxscore_from_cdn(game_id)


def fetch_boxscore_from_cdn(game_id: str):
    """
    Fallback boxscore parser from NBA live CDN JSON.
    """
    try:
        data = fetch_json(
            f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        game = data.get("game", {}) or {}
        away_team = game.get("awayTeam", {}) or {}
        home_team = game.get("homeTeam", {}) or {}

        def parse_players(team_obj):
            out = []
            for p in team_obj.get("players", []) or []:
                stats = p.get("statistics", {}) or {}
                out.append(
                    {
                        "name": p.get("name") or p.get("familyName") or "Unknown",
                        "min": str(stats.get("minutes", "0")),
                        "pts": int(stats.get("points", 0) or 0),
                        "reb": int((stats.get("reboundsTotal", 0) or 0)),
                        "ast": int((stats.get("assists", 0) or 0)),
                        "stl": int((stats.get("steals", 0) or 0)),
                        "blk": int((stats.get("blocks", 0) or 0)),
                        "fg": f"{int(stats.get('fieldGoalsMade', 0) or 0)}-{int(stats.get('fieldGoalsAttempted', 0) or 0)}",
                        "three": f"{int(stats.get('threePointersMade', 0) or 0)}-{int(stats.get('threePointersAttempted', 0) or 0)}",
                        "ft": f"{int(stats.get('freeThrowsMade', 0) or 0)}-{int(stats.get('freeThrowsAttempted', 0) or 0)}",
                        "pm": str(stats.get("plusMinusPoints", 0) or 0),
                    }
                )
            return out

        return {
            "away": parse_players(away_team),
            "home": parse_players(home_team),
        }
    except Exception:
        return {"away": [], "home": []}


def _pub_time_matches_target(
    date_obj: Optional[datetime], target_date: date, should_filter: bool
) -> bool:
    """
    If filtering by calendar day: keep undated items; otherwise require same day
    or ±1 day (RSS/pub timestamps are often UTC while the UI sends local YYYY-MM-DD).
    """
    if not should_filter:
        return True
    if date_obj is None:
        return True
    d = date_obj.date()
    if d == target_date:
        return True
    return abs((d - target_date).days) <= 1


def fetch_current_news(date_str: Optional[str] = None, limit: int = 20):
    """
    Pull current NBA headlines from RapidAPI (if configured),
    then fallback to public RSS feeds.
    """
    target_date = None
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            target_date = None
    if target_date is None:
        target_date = datetime.now().date()
    should_filter_by_date = date_str is not None

    # ── Live primary source: RapidAPI NBA news (optional) ──────────────────
    rapid_key = (os.getenv("RAPIDAPI_KEY") or "").strip()
    rapid_url = (os.getenv("RAPIDAPI_NEWS_URL") or "").strip()
    rapid_host = (os.getenv("RAPIDAPI_HOST") or "").strip()
    if rapid_key and rapid_url:
        try:
            headers = {
                "x-rapidapi-key": rapid_key,
                "User-Agent": "Mozilla/5.0",
            }
            if rapid_host:
                headers["x-rapidapi-host"] = rapid_host

            payload = fetch_json(rapid_url, headers=headers, timeout=25)

            # Handle common RapidAPI response shapes.
            candidates = []
            if isinstance(payload, dict):
                for k in ("data", "articles", "results", "news", "response"):
                    if isinstance(payload.get(k), list):
                        candidates = payload.get(k) or []
                        break
            elif isinstance(payload, list):
                candidates = payload

            rapid_items = []
            for row in candidates:
                if not isinstance(row, dict):
                    continue

                title = (
                    row.get("headline")
                    or row.get("title")
                    or row.get("name")
                    or ""
                ).strip()
                link = (
                    row.get("url")
                    or row.get("link")
                    or row.get("source_url")
                    or ""
                ).strip()
                summary = (
                    row.get("summary")
                    or row.get("description")
                    or row.get("excerpt")
                    or ""
                ).strip()
                source = (
                    row.get("source")
                    or row.get("provider")
                    or row.get("publisher")
                    or "RapidAPI"
                )
                pub_raw = (
                    row.get("published_at")
                    or row.get("publishedAt")
                    or row.get("pubDate")
                    or row.get("date")
                    or ""
                ).strip()

                if not title or not link:
                    continue

                date_obj = None
                if pub_raw:
                    try:
                        # Try RFC2822 then ISO-like formats.
                        date_obj = parsedate_to_datetime(pub_raw)
                    except Exception:
                        try:
                            date_obj = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                        except Exception:
                            date_obj = None

                if should_filter_by_date and not _pub_time_matches_target(
                    date_obj, target_date, should_filter_by_date
                ):
                    continue

                rapid_items.append(
                    {
                        "headline": title,
                        "summary": summary[:280] if summary else "",
                        "source": str(source),
                        "url": link,
                        "published_at": pub_raw,
                    }
                )

            if rapid_items:
                dedup = {}
                for item in rapid_items:
                    dedup[item["headline"]] = item
                return list(dedup.values())[:limit]
        except Exception:
            # Continue to RSS fallback below.
            pass

    # ── Fallback source: RSS feeds ──────────────────────────────────────────
    feeds = [
        ("ESPN", "https://www.espn.com/espn/rss/nba/news"),
        ("NBA.com", "https://www.nba.com/rss/nba_rss.xml"),
        ("Google News", "https://news.google.com/rss/search?q=NBA&hl=en-US&gl=US&ceid=US:en"),
        ("Yahoo Sports", "https://sports.yahoo.com/nba/rss/"),
    ]

    items = []
    for source, feed_url in feeds:
        try:
            xml_text = fetch_text(feed_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
            root = ET.fromstring(xml_text)
            for item in root.findall(".//item"):
                title = clean_html_text(item.findtext("title") or "")
                link = (item.findtext("link") or "").strip()
                description = clean_html_text(item.findtext("description") or "")
                pub_date_raw = (item.findtext("pubDate") or "").strip()
                if not title or not link:
                    continue

                date_obj = None
                if pub_date_raw:
                    try:
                        date_obj = parsedate_to_datetime(pub_date_raw)
                    except Exception:
                        date_obj = None
                if should_filter_by_date and not _pub_time_matches_target(
                    date_obj, target_date, should_filter_by_date
                ):
                    continue

                items.append(
                    {
                        "headline": title,
                        "summary": description[:280] if description else "",
                        "source": source,
                        "url": link,
                        "published_at": pub_date_raw,
                    }
                )
        except Exception:
            continue

    # If strict date filter produced nothing, return most recent feed items instead.
    if not items and date_str:
        return fetch_current_news(date_str=None, limit=limit)

    # Deduplicate by headline
    dedup = {}
    for item in items:
        dedup[item["headline"]] = item
    unique_items = list(dedup.values())[:limit]
    return unique_items


def find_player(cursor, player_name: str):
    # Exact match
    cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_season_stats "
        "WHERE LOWER(player_name) = LOWER(%s) LIMIT 1;",
        (player_name,),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Partial match
    cursor.execute(
        "SELECT DISTINCT player_id, player_name FROM player_season_stats "
        "WHERE LOWER(player_name) LIKE LOWER(%s) LIMIT 1;",
        (f"%{player_name}%",),
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]

    # Accent-insensitive fallback (e.g. "Jokic" -> "Nikola Jokić") — SQL
    # LOWER() above doesn't strip accents, so an un-accented query against
    # an accented name falls through to here.
    normalized_query = _normalize_search_text(player_name)
    cursor.execute("SELECT DISTINCT player_id, player_name FROM player_season_stats;")
    candidates = cursor.fetchall()
    for pid, pname in candidates:
        if normalized_query == _normalize_search_text(pname):
            return pid, pname
    for pid, pname in candidates:
        if normalized_query in _normalize_search_text(pname):
            return pid, pname

    raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found.")


# ─── With vs. Without a Star ─────────────────────────────────────────────────
#
# Real team record and real point differential in games a real player did
# vs. didn't play, for one team + season + player. Live-fetched from
# nba_api's LeagueGameFinder (real full-season team game log + real
# full-season player game log — the same endpoint/pattern the WPA pipeline
# already uses), cached like the other live fetches in this file. This is
# a real, disclosed ASSOCIATION, not a causal claim: who else was in or
# out of the lineup for those same real games also matters, and this
# endpoint doesn't control for that.

def _fetch_team_game_log(team_id: int, season: int):
    cached = _CACHE["team_game_log"].get((team_id, season))
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguegamefinder

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=str(team_id),
            season_nullable=season_label,
            season_type_nullable="Regular Season",
            timeout=30,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live team game log fetch failed: {exc}")

    games = [
        {
            "game_id": row["GAME_ID"],
            "wl": row["WL"],
            "plus_minus": float(row["PLUS_MINUS"]) if row["PLUS_MINUS"] is not None else None,
        }
        for _, row in df.iterrows()
    ]
    _CACHE["team_game_log"][(team_id, season)] = {"ts": time.time(), "data": games}
    return games


def _fetch_player_game_ids(player_id: int, season: int, team_id: int):
    cache_key = (player_id, season, team_id)
    cached = _CACHE["player_game_log"].get(cache_key)
    if cached and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        from nba_api.stats.endpoints import leaguegamefinder

        season_label = f"{season - 1}-{str(season)[-2:]}"
        endpoint = leaguegamefinder.LeagueGameFinder(
            player_id_nullable=str(player_id),
            season_nullable=season_label,
            season_type_nullable="Regular Season",
            timeout=30,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live player game log fetch failed: {exc}")

    game_ids = set(df[df["TEAM_ID"] == team_id]["GAME_ID"].tolist())
    _CACHE["player_game_log"][cache_key] = {"ts": time.time(), "data": game_ids}
    return game_ids


@app.get("/teams/with-without/{team_abbr}/{season}")
def get_with_without_star(team_abbr: str, season: int, player_name: str):
    team_abbr = team_abbr.upper()
    team_id = TEAM_ABBR_TO_ID.get(team_abbr)
    if team_id is None:
        raise HTTPException(status_code=400, detail=f"Unknown team abbreviation '{team_abbr}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    team_games = _fetch_team_game_log(team_id, season)
    if not team_games:
        raise HTTPException(status_code=404, detail=f"No real games found for {team_abbr} in season {season}.")

    played_game_ids = _fetch_player_game_ids(player_id, season, team_id)

    with_games = [g for g in team_games if g["game_id"] in played_game_ids]
    without_games = [g for g in team_games if g["game_id"] not in played_game_ids]

    def summarize(games):
        n = len(games)
        if n == 0:
            return {"n": 0, "wins": 0, "losses": 0, "win_pct": None, "avg_point_diff": None}
        wins = sum(1 for g in games if g["wl"] == "W")
        losses = n - wins
        diffs = [g["plus_minus"] for g in games if g["plus_minus"] is not None]
        avg_diff = sum(diffs) / len(diffs) if diffs else None
        return {
            "n": n,
            "wins": wins,
            "losses": losses,
            "win_pct": round(wins / n, 3),
            "avg_point_diff": round(avg_diff, 2) if avg_diff is not None else None,
        }

    return {
        "team_abbreviation": team_abbr,
        "season": season,
        "player_id": player_id,
        "player_name": resolved_name,
        "with_player": summarize(with_games),
        "without_player": summarize(without_games),
        "methodology": (
            f"Real {team_abbr} team game log and real {resolved_name} game log for season {season}, both "
            "live-fetched from the NBA's own real per-game data, not a model. This is an association, not a "
            "causal claim: other players being in or out of the lineup for the same real games also affects "
            "the real result, and this comparison doesn't control for that. Real sample sizes for both splits "
            "are always shown — draw conclusions cautiously from a small 'without' sample, which is common for "
            "a player who rarely sits."
        ),
    }


# ─── Schedule Fatigue ────────────────────────────────────────────────────────
#
# Real rest days, back-to-backs, real travel miles (haversine, scripts/
# arenas.py's real arena locations), and real time zones crossed for every
# real team game, precomputed by scripts/build_schedule_fatigue.py from
# real nba_api game logs (team_game_fatigue table). Read-only here — this
# section only queries what that script already computed.

@app.get("/schedule/rest-study")
def get_rest_study(season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.team_game_fatigue');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No schedule data yet — run scripts/build_schedule_fatigue.py first.")

        params = [season] if season else []
        season_clause = "AND season = %s" if season else ""
        cursor.execute(
            f"""SELECT rest_days, COUNT(*) AS n,
                       AVG(CASE WHEN win THEN 1.0 ELSE 0 END) AS win_pct,
                       AVG(plus_minus) AS avg_point_diff
                FROM team_game_fatigue
                WHERE rest_days IS NOT NULL {season_clause}
                GROUP BY rest_days
                ORDER BY rest_days;""",
            params,
        )
        rows = cursor.fetchall()

    buckets = []
    for rest_days, n, win_pct, avg_diff in rows:
        bucket_label = "B2B (0 days rest)" if rest_days == 0 else f"{rest_days} day{'s' if rest_days != 1 else ''} rest"
        buckets.append({
            "rest_days": rest_days,
            "bucket_label": bucket_label if rest_days < 4 else "4+ days rest",
            "n": n,
            "win_pct": round(float(win_pct), 4),
            "avg_point_diff": round(float(avg_diff), 3) if avg_diff is not None else None,
        })

    # Fold 4+ day buckets together — real n gets thin past 3 days and a
    # dozen separate one-off buckets is noise, not signal.
    merged = {}
    for b in buckets:
        key = b["rest_days"] if b["rest_days"] < 4 else 4
        if key not in merged:
            merged[key] = {"rest_days": key, "bucket_label": "4+ days rest" if key == 4 else b["bucket_label"], "n": 0, "_win_sum": 0.0, "_diff_sum": 0.0}
        merged[key]["n"] += b["n"]
        merged[key]["_win_sum"] += b["win_pct"] * b["n"]
        merged[key]["_diff_sum"] += (b["avg_point_diff"] or 0) * b["n"]

    final_buckets = []
    for key in sorted(merged.keys()):
        m = merged[key]
        final_buckets.append({
            "rest_days": m["rest_days"],
            "bucket_label": m["bucket_label"],
            "n": m["n"],
            "win_pct": round(m["_win_sum"] / m["n"], 4),
            "avg_point_diff": round(m["_diff_sum"] / m["n"], 3),
        })

    return {
        "season": season,
        "buckets": final_buckets,
        "methodology": (
            "Real win% and real average point differential (that game's real plus/minus) by real rest-days "
            "bucket, across every real team-game with a known previous real game (team_game_fatigue). "
            "0 days rest = a real back-to-back. Real n is shown per bucket — samples get thin past 3+ days "
            "rest, folded into one '4+ days rest' bucket rather than presented as many noisy one-off buckets."
        ),
    }


@app.get("/schedule/difficulty")
def get_schedule_difficulty(season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        cursor.execute("SELECT to_regclass('public.team_game_fatigue');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No schedule data yet — run scripts/build_schedule_fatigue.py first.")

        cursor.execute(
            """SELECT team_abbreviation,
                      COUNT(*) AS n_games,
                      SUM(travel_miles_since_last) AS total_miles,
                      SUM(CASE WHEN is_b2b THEN 1 ELSE 0 END) AS n_b2b,
                      SUM(CASE WHEN games_last_7_days >= 4 THEN 1 ELSE 0 END) AS n_heavy_weeks
               FROM team_game_fatigue
               WHERE season = %s
               GROUP BY team_abbreviation
               ORDER BY total_miles DESC NULLS LAST;""",
            (resolved_season,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No real schedule data for season {resolved_season}.")

    results = [
        {
            "rank": i + 1,
            "team_abbreviation": r[0],
            "n_games": r[1],
            "total_travel_miles": round(r[2], 0) if r[2] is not None else None,
            "b2b_count": r[3],
            "games_with_4plus_in_7days": r[4],
        }
        for i, r in enumerate(rows)
    ]

    return {
        "season": resolved_season,
        "results": results,
        "methodology": (
            "Real total travel miles (haversine between each real consecutive game's real arena location, "
            "scripts/arenas.py), real back-to-back count, and real count of stretches with 4+ real games in "
            "a trailing 7-day window, per real team for the season — ranked by real total travel, the most "
            "direct real proxy for a grueling real schedule. Not causal — a team's real record isn't adjusted "
            "for this, it's shown as real schedule context only."
        ),
    }


# ─── Play-Type Profiles & Hustle Stats ───────────────────────────────────────
#
# Real offensive play-type frequency/efficiency (nba_api's SynergyPlayTypes,
# scripts/fetch_playtypes.py — real data confirmed live to start at the
# 2012-13 season, not "2015-16" as originally assumed) and real hustle
# stats (LeagueHustleStatsPlayer, scripts/fetch_hustle_stats.py — real data
# confirmed to start at 2015-16). Both precomputed, read-only here.

HUSTLE_STAT_MAP = {
    "deflections": "Deflections",
    "contested_shots": "Contested Shots",
    "screen_assists": "Screen Assists",
    "loose_balls_recovered": "Loose Balls Recovered",
    "charges_drawn": "Charges Drawn",
    "box_outs": "Box Outs",
}


@app.get("/hustle/leaders")
def get_hustle_leaders(stat: str = "deflections", season: Optional[int] = None, top_n: int = 15):
    stat = stat.lower()
    if stat not in HUSTLE_STAT_MAP:
        raise HTTPException(status_code=400, detail=f"stat must be one of {sorted(HUSTLE_STAT_MAP)}.")
    top_n = max(1, min(top_n, 50))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_hustle');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No hustle data yet — run scripts/fetch_hustle_stats.py first.")
        resolved_season = season or get_latest_season(cursor)
        cursor.execute(
            f"""SELECT player_id, player_name, team_abbreviation, gp, {stat}
                FROM player_hustle WHERE season = %s AND {stat} IS NOT NULL
                ORDER BY {stat} DESC LIMIT %s;""",
            (resolved_season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": resolved_season,
        "stat": stat,
        "stat_label": HUSTLE_STAT_MAP[stat],
        "results": [
            {"rank": i + 1, "player_id": r[0], "player_name": r[1], "team_abbreviation": r[2], "gp": r[3], "value": round(r[4], 2)}
            for i, r in enumerate(rows)
        ],
        "methodology": (
            f"Real per-game {HUSTLE_STAT_MAP[stat]} for season {resolved_season}, from the NBA's own real "
            "hustle-stat tracking (LeagueHustleStatsPlayer) — real effort/activity stats the traditional "
            "box score doesn't capture."
        ),
    }


@app.get("/players/playtype-profile/{player_name}")
def get_playtype_profile(player_name: str, season: Optional[int] = None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_playtypes');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No play-type data yet — run scripts/fetch_playtypes.py first.")

        player_id, resolved_name = find_player(cursor, player_name)
        resolved_season = season or get_latest_season(cursor)

        cursor.execute(
            """SELECT play_type, gp, poss, freq, ppp, percentile
               FROM player_playtypes
               WHERE player_id = %s AND season = %s AND side = 'offensive'
               ORDER BY freq DESC;""",
            (player_id, resolved_season),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No real play-type data for {resolved_name} in season {resolved_season} "
                   "(too few possessions in any play type to qualify, or before real play-type tracking began in 2012-13).",
        )

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": resolved_season,
        "play_types": [
            {"play_type": r[0], "gp": r[1], "poss": r[2], "freq": round(r[3], 4), "ppp": round(r[4], 3), "percentile": round(r[5], 3)}
            for r in rows
        ],
        "methodology": (
            "Real offensive play-type breakdown (NBA's own real Synergy tracking): freq is this real player's "
            "real share of their own offensive possessions run through that real play type; ppp is their real "
            "points per possession in it; percentile is their real league percentile rank for efficiency in "
            "that play type (higher is better), among real players with enough real possessions to qualify."
        ),
    }


# ─── Shot Chart Endpoints ───────────────────────────────────────────────────

@app.get("/shots/player/{player_name}/seasons")
def get_shot_seasons(player_name: str):
    """
    Resolve a player and make sure their career shots are cached (fetching
    live once, on a cache miss, if ENABLE_LIVE_SHOT_FETCH != "false").
    A cold cache miss for a long career can take a couple of minutes — the
    fetch is deliberately rate-limited to avoid getting flagged by
    stats.nba.com.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    try:
        result = shots_lib.ensure_player_shots_cached(int(player_id), resolved_name)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    return {
        "player_id": int(player_id),
        "player_name": resolved_name,
        "seasons": result["seasons"],
        "source": result["source"],
    }


@app.get("/shots/player/{player_name}")
def get_player_shots(player_name: str, season: Optional[str] = None):
    """
    Shots for one season (defaults to the player's most recent cached
    season). Triggers the same cache-or-fetch flow as /seasons, so this can
    be called directly without hitting /seasons first.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    try:
        result = shots_lib.ensure_player_shots_cached(int(player_id), resolved_name)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    seasons = result["seasons"]
    if not seasons:
        raise HTTPException(status_code=404, detail=f"No seasons with shot data for '{resolved_name}'.")

    target_season = season if season in seasons else seasons[-1]
    shots = shots_lib.get_shots_for_season(int(player_id), target_season)

    return {
        "player_id": int(player_id),
        "player_name": resolved_name,
        "season": target_season,
        "seasons": seasons,
        "source": result["source"],
        "shots": shots,
    }


# ─── Endpoints ──────────────────────────────────────────────────────────────

RADAR_STATS = ["pts", "reb", "ast", "stl", "blk", "ts_pct", "usg_pct"]
# Raw columns fetched only to compute the two derived metrics below — not
# radar axes themselves.
RADAR_HELPER_COLS = ["gp", "fg3a", "fg3_pct", "efg_pct", "poss"]
RADAR_MIN_MINUTES = 15
RADAR_MIN_GAMES = 20


def _shooting_proficiency(fg3a, fg3_pct, gp, poss):
    """(2 / (1 + e^-3PAper100) - 1) * 3FG% — rewards volume AND accuracy;
    a raw 3P% alone can't tell a 1-attempt hot streak from a real shooter."""
    if None in (fg3a, fg3_pct, gp, poss) or poss == 0:
        return None
    fg3a_per100 = (fg3a * gp) / poss * 100
    return (2 / (1 + math.exp(-fg3a_per100)) - 1) * fg3_pct


def _spacing(fg3a, fg3_pct, efg_pct):
    """(3PA * (3P% * 1.5)) - EFG% — an index (not a percentage) of how much
    gravity a player's outside shooting pulls defenders away from the paint."""
    if None in (fg3a, fg3_pct, efg_pct):
        return None
    return (fg3a * (fg3_pct * 1.5)) - efg_pct


@app.get("/radar/{player_name}")
def get_radar_profile(player_name: str, season: int):
    """
    Percentile rank (0-100) for each radar axis, among that season's
    qualified pool (min>=15mpg, gp>=20 — same convention as clustering, to
    keep rate stats meaningful). Percentile, not raw value or a fixed
    hardcoded scale, so every axis is directly comparable on a 0-100 chart
    regardless of the stat's natural range (points go to ~35, TS% to ~0.65).
    Includes CraftedNBA-style derived metrics alongside the raw box-score
    stats — see _shooting_proficiency/_spacing for the disclosed formulas
    being reproduced. defensive_impact/rad_per_game come from
    defense_tracking_stats (player-tracking data, only exists from 2013-14
    onward and only for players scripts/build_defense_tracking_stats.py has
    been run for) — null/omitted gracefully when unavailable, same as any
    other missing stat here.
    """
    all_cols = RADAR_STATS + RADAR_HELPER_COLS
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            f"""
            SELECT p.player_id, {', '.join(f'p.{c}' for c in all_cols)},
                   d.fg_diff_pct, d.rad_per_game
            FROM player_season_stats p
            LEFT JOIN defense_tracking_stats d
                ON d.player_id = p.player_id AND d.season = p.season
            WHERE p.season = %s AND p.min >= %s AND p.gp >= %s;
            """,
            (season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        pool = cursor.fetchall()

    if not pool:
        raise HTTPException(status_code=404, detail=f"No qualified player data for season {season}.")

    all_row_cols = all_cols + ["fg_diff_pct", "rad_per_game"]
    pool_by_id = {row[0]: dict(zip(all_row_cols, row[1:])) for row in pool}
    if player_id not in pool_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"{resolved_name} doesn't meet the qualified-pool minimum "
                   f"(min>={RADAR_MIN_MINUTES}mpg, gp>={RADAR_MIN_GAMES}) for season {season}.",
        )

    # Compute the derived metrics for every pool member (needed to rank this
    # player's percentile against them), not just the selected player.
    for values in pool_by_id.values():
        values["shooting_proficiency"] = _shooting_proficiency(
            values["fg3a"], values["fg3_pct"], values["gp"], values["poss"])
        values["spacing"] = _spacing(values["fg3a"], values["fg3_pct"], values["efg_pct"])
        # fg_diff_pct is negative for GOOD defense (holds opponents below
        # league average) — flip the sign so "further out on the radar" is
        # consistently "better" across every axis, like the rest of the chart.
        fgd = values["fg_diff_pct"]
        values["defensive_impact"] = (-fgd) if fgd is not None else None

    radar_axes = RADAR_STATS + ["shooting_proficiency", "spacing", "defensive_impact", "rad_per_game"]
    player_values = pool_by_id[player_id]
    n = len(pool)
    percentiles = []
    for stat in radar_axes:
        this_value = player_values[stat]
        if this_value is None:
            percentiles.append({"stat": stat, "value": None, "percentile": None})
            continue
        below_or_equal = sum(
            1 for v in pool_by_id.values() if v[stat] is not None and v[stat] <= this_value
        )
        percentile = round(100 * below_or_equal / n, 1)
        percentiles.append({"stat": stat, "value": round(float(this_value), 3), "percentile": percentile})

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "pool_size": n,
        "stats": percentiles,
    }


# ─── Player Comparison page ─────────────────────────────────────────────────
# Same qualified-pool convention as /radar (min>=15mpg, gp>=20) so a
# percentile means the same thing whichever feature computed it.

COMPOSITE_SKILL_AXES = [
    ("scoring", "Scoring", "pts"),
    ("efficiency", "Efficiency", "ts_pct"),
    ("playmaking", "Playmaking", "ast"),
    ("rebounding", "Rebounding", "reb"),
    ("defense", "Defense", "dbpm"),
    ("impact", "Impact", "bpm"),
]

COMPARE_DETAIL_STATS = [
    ("ts_pct", "TS%"), ("efg_pct", "eFG%"), ("usg_pct", "USG%"),
    ("ast_pct", "AST%"), ("reb_pct", "REB%"), ("tov_pct", "TOV%"),
    ("oreb_pct", "OREB%"), ("net_rating", "Net Rtg"),
    ("ftr", "FTr"), ("tpar", "3PAr"),
]


def _percentile_rank(value, pool_values):
    if value is None or not pool_values:
        return None
    n = len(pool_values)
    below_or_equal = sum(1 for v in pool_values if v <= value)
    return round(100 * below_or_equal / n, 1)


@app.get("/players/compare-profile/{player_name}")
def get_compare_profile(player_name: str, season: int):
    """
    Everything the Player Comparison page needs for one player: bio, "Tale
    of the Tape" raw stats (mapping the reference's Offensive/Defensive/
    Overall Impact rows onto this project's own OBPM/DBPM/BPM), 6 composite
    Skill Profile percentiles, and a granular percentile stat table.
    "Archetype" (statistical clustering) is used in place of a scouted
    offensive/defensive role — this project has no real scouted-role data.
    Real physical measurements (height, wingspan, standing reach, weight)
    ARE included when the player has real NBA Draft Combine data on file
    (scripts/fetch_draft_combine.py) — null when they don't (undrafted or
    skipped the combine), never guessed.
    """
    cols = [
        "pts", "reb", "ast", "ts_pct", "efg_pct", "usg_pct",
        "ast_pct", "reb_pct", "tov_pct", "oreb_pct", "net_rating",
        "fta", "fga", "fg3a", "bpm", "obpm", "dbpm", "vorp", "bpm_position",
        "age", "gp", "min", "team_abbreviation",
    ]
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            f"""
            SELECT p.player_id, {', '.join(f'p.{c}' for c in cols)}, c.archetype
            FROM player_season_stats p
            LEFT JOIN player_clusters c
                ON c.player_id = p.player_id AND c.season = p.season
            WHERE p.season = %s AND p.min >= %s AND p.gp >= %s;
            """,
            (season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
        )
        pool = cursor.fetchall()

        cursor.execute(
            """SELECT wingspan, height_wo_shoes, height_w_shoes, standing_reach, weight, max_vertical_leap
               FROM draft_combine WHERE player_id = %s ORDER BY draft_year DESC LIMIT 1;""",
            (player_id,),
        )
        combine_row = cursor.fetchone()

    if not pool:
        raise HTTPException(status_code=404, detail=f"No qualified player data for season {season}.")

    row_cols = ["player_id"] + cols + ["archetype"]
    pool_by_id = {row[0]: dict(zip(row_cols, row)) for row in pool}
    if player_id not in pool_by_id:
        raise HTTPException(
            status_code=404,
            detail=f"{resolved_name} doesn't meet the qualified-pool minimum "
                   f"(min>={RADAR_MIN_MINUTES}mpg, gp>={RADAR_MIN_GAMES}) for season {season}.",
        )

    # FTr / 3PAr aren't stored columns — derive for the whole pool (needed
    # to rank this player's percentile against them), same pattern as the
    # derived radar metrics above.
    for v in pool_by_id.values():
        v["ftr"] = (v["fta"] / v["fga"]) if v.get("fga") else None
        v["tpar"] = (v["fg3a"] / v["fga"]) if v.get("fga") else None

    player = pool_by_id[player_id]
    n = len(pool_by_id)

    combine_measurements = None
    if combine_row:
        wingspan, height_wo, height_w, reach, weight, vertical = combine_row
        combine_measurements = {
            "wingspan": wingspan, "height_wo_shoes": height_wo, "height_w_shoes": height_w,
            "standing_reach": reach, "weight": weight, "max_vertical_leap": vertical,
        }

    def pct_entry(stat, label):
        this_value = player.get(stat)
        pool_values = [v[stat] for v in pool_by_id.values() if v.get(stat) is not None]
        return {
            "key": stat,
            "label": label,
            "value": round(float(this_value), 3) if this_value is not None else None,
            "percentile": _percentile_rank(this_value, pool_values),
        }

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": season,
        "pool_size": n,
        "bio": {
            "team_abbreviation": player.get("team_abbreviation"),
            "age": player.get("age"),
            "gp": player.get("gp"),
            "min": round(player["min"], 1) if player.get("min") is not None else None,
            "position": _position_label(player["bpm_position"]) if player.get("bpm_position") is not None else None,
            "archetype": player.get("archetype"),
            "combine_measurements": combine_measurements,
        },
        "tale_of_the_tape": {
            "pts": player.get("pts"), "reb": player.get("reb"), "ast": player.get("ast"),
            "ts_pct": player.get("ts_pct"), "usg_pct": player.get("usg_pct"),
            "obpm": player.get("obpm"), "dbpm": player.get("dbpm"), "bpm": player.get("bpm"),
        },
        "skill_profile": [pct_entry(stat, label) for _, label, stat in COMPOSITE_SKILL_AXES],
        "detail_stats": [pct_entry(stat, label) for stat, label in COMPARE_DETAIL_STATS],
    }


@app.get("/shots/player/{player_name}/zones")
def get_player_shot_zones(player_name: str, season: int):
    """A player's own FG% by the 5 real NBA shot zones (Restricted Area,
    Paint, Mid-Range, Corner 3, Above the Break 3), for the comparison
    page's shot-chart section. Fetches (and caches forever) just the ONE
    requested season — one live request instead of the ~N+1 a full-career
    fetch needs, so this is both much faster and has far fewer places to
    hit a transient network failure. If a full-career fetch already ran
    for this player (e.g. from the single-player Shot Charts page), this
    season is already cached and returns instantly either way."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

    season_label = f"{season - 1}-{str(season)[-2:]}"
    try:
        shots_lib.ensure_season_shots_cached(int(player_id), resolved_name, season_label)
    except shots_lib.ShotsUnavailable as e:
        raise HTTPException(status_code=404, detail=str(e))
    except shots_lib.ShotsFetchFailed as e:
        raise HTTPException(status_code=502, detail=f"Live shot fetch failed: {e}")

    shots = shots_lib.get_shots_for_season(int(player_id), season_label)
    if not shots:
        raise HTTPException(status_code=404, detail=f"No shot data for {resolved_name} in {season_label}.")
    zones = shots_lib.compute_zone_stats(shots)
    return {"player_id": player_id, "player_name": resolved_name, "season": season_label, "zones": zones}


@app.get("/shots/league-zones/{season}")
def get_league_shot_zones(season: int):
    """League-wide FG% by the same 5 zones, one cheap aggregate call per
    season (not per player), cached forever after the first fetch."""
    season_label = f"{season - 1}-{str(season)[-2:]}"
    try:
        zones = shots_lib.get_league_zone_stats(season_label)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Live league shot fetch failed: {e}")
    return {"season": season_label, "zones": zones}


# ─── Games: Guess the Player ────────────────────────────────────────────────
#
# A Wordle-style daily deduction game: one mystery player from the season's
# qualified pool (same min≥15mpg, gp≥20 pool used by Radar/Compare), guesses
# get per-field feedback (team/position/archetype match, age/pts/reb/ast
# higher-or-lower). Entirely built from real player_season_stats /
# player_clusters data already in the DB — nothing invented.
#
# Deliberately stateless: the mystery player is derived by hashing
# puzzle_date+season into an index in the pool, so nothing is stored
# server-side and a restart never loses "today's" puzzle. The frontend
# passes its own local calendar date as puzzle_date so the puzzle matches
# what the player actually sees as "today" (see frontend/src/utils/date.js).

GUESS_GAME_MAX_GUESSES = 8


def _guess_game_pool(cursor, season: int):
    cursor.execute(
        """
        SELECT p.player_id, p.player_name, p.team_abbreviation, p.age,
               p.pts, p.reb, p.ast, p.bpm_position, c.archetype
        FROM player_season_stats p
        LEFT JOIN player_clusters c
            ON c.player_id = p.player_id AND c.season = p.season
        WHERE p.season = %s AND p.min >= %s AND p.gp >= %s
        ORDER BY p.player_id ASC;
        """,
        (season, RADAR_MIN_MINUTES, RADAR_MIN_GAMES),
    )
    rows = cursor.fetchall()
    cols = ["player_id", "player_name", "team_abbreviation", "age",
            "pts", "reb", "ast", "bpm_position", "archetype"]
    return [dict(zip(cols, row)) for row in rows]


def _guess_game_default_season(cursor) -> int:
    """Prefer the latest season that has real archetype clustering data —
    the newest player_season_stats season is often not clustered yet (the
    clustering job is a separate offline batch), which would make every
    "archetype" clue trivially null-vs-null. Falls back to the latest
    stats season if clustering hasn't run for anything (shouldn't happen)."""
    cursor.execute("SELECT MAX(season) FROM player_clusters;")
    clustered = cursor.fetchone()[0]
    return int(clustered) if clustered is not None else get_latest_season(cursor)


def _guess_game_mystery(pool_rows, season: int, puzzle_date: date):
    seed = f"{puzzle_date.isoformat()}-{season}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    index = int(digest, 16) % len(pool_rows)
    return pool_rows[index]


def _parse_puzzle_date(puzzle_date: Optional[str]) -> date:
    if not puzzle_date:
        return date.today()
    try:
        return date.fromisoformat(puzzle_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="puzzle_date must be YYYY-MM-DD.")


def _guess_game_fields_match(guess_value, mystery_value):
    """Equality that never counts two missing values as a "match" — a
    null-vs-null archetype/position shouldn't read as a meaningful clue."""
    if guess_value is None or mystery_value is None:
        return False
    return guess_value == mystery_value


def _direction(mystery_value, guess_value):
    if mystery_value is None or guess_value is None:
        return None
    if mystery_value == guess_value:
        return "same"
    return "higher" if mystery_value > guess_value else "lower"


def _guess_game_public(row):
    return {
        "player_id": row["player_id"],
        "player_name": row["player_name"],
        "team_abbreviation": row["team_abbreviation"],
        "position": _position_label(row["bpm_position"]),
        "archetype": row["archetype"],
        "age": row["age"],
        "pts": row["pts"],
        "reb": row["reb"],
        "ast": row["ast"],
    }


@app.get("/games/guess-the-player/daily")
def get_guess_the_player_daily(season: Optional[int] = None):
    """Today's puzzle setup: which season's qualified pool is in play and
    the full guessable list (name/team only — never bio/stat fields, so
    the answer can't be read off this response)."""
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    return {
        "date": date.today().isoformat(),
        "season": resolved_season,
        "max_guesses": GUESS_GAME_MAX_GUESSES,
        "pool_size": len(pool_rows),
        "pool": [
            {"player_id": r["player_id"], "player_name": r["player_name"], "team_abbreviation": r["team_abbreviation"]}
            for r in pool_rows
        ],
    }


@app.get("/games/guess-the-player/guess")
def guess_the_player(guess_player_name: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    """Compares one guess against today's mystery player and returns
    per-field feedback, Wordle-style."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    mystery = _guess_game_mystery(pool_rows, resolved_season, resolved_date)

    pool_by_name = {r["player_name"].lower(): r for r in pool_rows}
    guess = pool_by_name.get(guess_player_name.strip().lower())
    if guess is None:
        raise HTTPException(status_code=404, detail=f"{guess_player_name} isn't in today's guessable pool.")

    correct = guess["player_id"] == mystery["player_id"]
    result = {
        "correct": correct,
        "guess": _guess_game_public(guess),
        "feedback": {
            "team": "match" if guess["team_abbreviation"] == mystery["team_abbreviation"] else "no_match",
            "position": "match" if _guess_game_fields_match(_position_label(guess["bpm_position"]), _position_label(mystery["bpm_position"])) else "no_match",
            "archetype": "match" if _guess_game_fields_match(guess["archetype"], mystery["archetype"]) else "no_match",
            "age": _direction(mystery["age"], guess["age"]),
            "pts": _direction(mystery["pts"], guess["pts"]),
            "reb": _direction(mystery["reb"], guess["reb"]),
            "ast": _direction(mystery["ast"], guess["ast"]),
        },
    }
    if correct:
        result["mystery_player"] = result["guess"]
    return result


@app.get("/games/guess-the-player/reveal")
def reveal_guess_the_player(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    """Reveals the mystery player once a player is out of guesses."""
    resolved_date = _parse_puzzle_date(puzzle_date)

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)

    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")

    mystery = _guess_game_mystery(pool_rows, resolved_season, resolved_date)
    return _guess_game_public(mystery)


# ─── Games: Higher or Lower ─────────────────────────────────────────────────
#
# An arcade-style streak game: pick a career stat, chain guesses of whether
# the next player's total is higher or lower than the current one's. Unlike
# Guess the Player, this has no daily puzzle or hidden state to protect, so
# the whole pool is sent once and the round-by-round logic runs client-side.
#
# "Career totals" here are estimated as SUM(season_ppg * season_gp) across
# every season this project's DB actually has (2010-present) — not a real
# full-career total the way basketball-reference would report one for an
# older player. That's disclosed on the frontend rather than presented as
# an official number.

HIGHER_LOWER_MIN_CAREER_GAMES = 150


@app.get("/games/higher-lower/pool")
def get_higher_lower_pool():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT p.player_id,
                   MAX(p.player_name) AS player_name,
                   (array_agg(p.team_abbreviation ORDER BY p.season DESC))[1] AS team_abbreviation,
                   SUM(p.pts * p.gp) AS career_pts,
                   SUM(p.reb * p.gp) AS career_reb,
                   SUM(p.ast * p.gp) AS career_ast,
                   SUM(p.gp) AS career_gp
            FROM player_season_stats p
            GROUP BY p.player_id
            HAVING SUM(p.gp) >= %s;
            """,
            (HIGHER_LOWER_MIN_CAREER_GAMES,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No qualified player pool.")

    players = [
        {
            "player_id": player_id,
            "player_name": player_name,
            "team_abbreviation": team_abbreviation,
            "career_pts": round(career_pts) if career_pts is not None else None,
            "career_reb": round(career_reb) if career_reb is not None else None,
            "career_ast": round(career_ast) if career_ast is not None else None,
            "career_gp": int(career_gp) if career_gp is not None else None,
        }
        for player_id, player_name, team_abbreviation, career_pts, career_reb, career_ast, career_gp in rows
    ]

    return {
        "min_career_games": HIGHER_LOWER_MIN_CAREER_GAMES,
        "stat_options": [
            {"key": "career_pts", "label": "Career Points"},
            {"key": "career_reb", "label": "Career Rebounds"},
            {"key": "career_ast", "label": "Career Assists"},
            {"key": "career_gp", "label": "Career Games Played"},
        ],
        "pool_size": len(players),
        "players": players,
    }


# ─── Games: Blurred Player ──────────────────────────────────────────────────
#
# Same daily-puzzle mechanics as Guess the Player (deterministic pool-index
# hash, nothing stored server-side) but with a real headshot photo,
# progressively un-blurred client-side as guesses run out, instead of
# stat-based clues. Uses a different hash salt than Guess the Player so the
# two games don't share the same daily answer.
#
# The image is proxied through this backend rather than handing the
# frontend a raw cdn.nba.com/.../{player_id}.png URL — that URL *is* the
# answer (the filename is the player_id), so shipping it directly would let
# anyone check devtools and skip the game.

BLURRED_PLAYER_MAX_GUESSES = 8


def _blurred_player_mystery(pool_rows, season: int, puzzle_date: date):
    seed = f"blurred-player-{puzzle_date.isoformat()}-{season}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    index = int(digest, 16) % len(pool_rows)
    return pool_rows[index]


def _blurred_player_resolve(season: Optional[int], puzzle_date: Optional[str]):
    resolved_date = _parse_puzzle_date(puzzle_date)
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)
    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")
    mystery = _blurred_player_mystery(pool_rows, resolved_season, resolved_date)
    return resolved_season, resolved_date, pool_rows, mystery


@app.get("/games/blurred-player/daily")
def get_blurred_player_daily(season: Optional[int] = None):
    resolved_season, _, pool_rows, _ = _blurred_player_resolve(season, None)
    return {
        "date": date.today().isoformat(),
        "season": resolved_season,
        "max_guesses": BLURRED_PLAYER_MAX_GUESSES,
        "pool_size": len(pool_rows),
        "pool": [
            {"player_id": r["player_id"], "player_name": r["player_name"], "team_abbreviation": r["team_abbreviation"]}
            for r in pool_rows
        ],
    }


@app.get("/games/blurred-player/image")
def get_blurred_player_image(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, _, mystery = _blurred_player_resolve(season, puzzle_date)
    image_url = f"https://cdn.nba.com/headshots/nba/latest/1040x760/{mystery['player_id']}.png"
    try:
        req = Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10, context=_SSL_CONTEXT) as resp:
            data = resp.read()
    except Exception:
        raise HTTPException(status_code=502, detail="Could not load today's player image.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/games/blurred-player/guess")
def guess_blurred_player(guess_player_name: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, pool_rows, mystery = _blurred_player_resolve(season, puzzle_date)

    pool_by_name = {r["player_name"].lower(): r for r in pool_rows}
    guess = pool_by_name.get(guess_player_name.strip().lower())
    if guess is None:
        raise HTTPException(status_code=404, detail=f"{guess_player_name} isn't in today's guessable pool.")

    correct = guess["player_id"] == mystery["player_id"]
    result = {"correct": correct, "guess_player_name": guess["player_name"]}
    if correct:
        result["mystery_player"] = _guess_game_public(mystery)
    return result


@app.get("/games/blurred-player/reveal")
def reveal_blurred_player(season: Optional[int] = None, puzzle_date: Optional[str] = None):
    _, _, _, mystery = _blurred_player_resolve(season, puzzle_date)
    return _guess_game_public(mystery)


# ─── Games: Trivia ───────────────────────────────────────────────────────────
#
# Five real-data multiple-choice questions a day, built from the same
# qualified season pool the other games use. The correct answer is always
# today's actual real value (this season's real top scorer, etc.) — only
# which real players show up as decoys, and the shuffle order, rotate daily
# via the same date-hash technique as the other games. Nothing is invented:
# a decoy is always some other real player from the same pool, never a
# fabricated stat.

TRIVIA_ARCHETYPES = [
    "Bench Role Player", "Elite Two-Way Big", "Primary Scorer",
    "3-and-D Wing", "Rim Protector", "Playmaker",
]


def _trivia_rng(season: int, puzzle_date: date, question_id: str) -> random.Random:
    seed = f"trivia-{question_id}-{puzzle_date.isoformat()}-{season}"
    return random.Random(seed)


def _trivia_player_question(question_id, prompt, pool_rows, stat_key, season, puzzle_date):
    ranked = sorted(
        (r for r in pool_rows if r.get(stat_key) is not None),
        key=lambda r: r[stat_key], reverse=True,
    )
    if not ranked:
        return None
    correct = ranked[0]
    band = ranked[1:9]  # plausible near-leaders, not random scrubs
    rng = _trivia_rng(season, puzzle_date, question_id)
    decoys = rng.sample(band, min(3, len(band)))
    options = [correct] + decoys
    rng.shuffle(options)
    question = {
        "id": question_id,
        "question": prompt,
        "options": [{"id": str(o["player_id"]), "label": o["player_name"]} for o in options],
    }
    return question, str(correct["player_id"])


def _trivia_youngest_question(pool_rows, season, puzzle_date):
    ranked_by_pts = sorted(
        (r for r in pool_rows if r.get("pts") is not None),
        key=lambda r: r["pts"], reverse=True,
    )
    top10 = [r for r in ranked_by_pts[:10] if r.get("age") is not None]
    if len(top10) < 4:
        return None
    correct = min(top10, key=lambda r: r["age"])
    band = [r for r in top10 if r["player_id"] != correct["player_id"]]
    rng = _trivia_rng(season, puzzle_date, "youngest_top10")
    decoys = rng.sample(band, min(3, len(band)))
    options = [correct] + decoys
    rng.shuffle(options)
    question = {
        "id": "youngest_top10",
        "question": "Who is the youngest player among this season's top 10 scorers?",
        "options": [{"id": str(o["player_id"]), "label": o["player_name"]} for o in options],
    }
    return question, str(correct["player_id"])


def _trivia_archetype_question(pool_rows, season, puzzle_date):
    counts = {}
    for r in pool_rows:
        a = r.get("archetype")
        if a:
            counts[a] = counts.get(a, 0) + 1
    if not counts:
        return None
    correct_label = max(counts, key=counts.get)
    wrong_labels = [a for a in TRIVIA_ARCHETYPES if a != correct_label]
    rng = _trivia_rng(season, puzzle_date, "top_archetype")
    decoys = rng.sample(wrong_labels, min(3, len(wrong_labels)))
    options = [correct_label] + decoys
    rng.shuffle(options)
    question = {
        "id": "top_archetype",
        "question": "Which statistical archetype has the most players this season?",
        "options": [{"id": a, "label": a} for a in options],
    }
    return question, correct_label


def _trivia_build_all(pool_rows, season: int, puzzle_date: date):
    specs = [
        ("top_scorer", "Who leads the league in points per game this season?", "pts"),
        ("top_rebounder", "Who leads the league in rebounds per game this season?", "reb"),
        ("top_assister", "Who leads the league in assists per game this season?", "ast"),
    ]
    results = []
    for question_id, prompt, stat_key in specs:
        result = _trivia_player_question(question_id, prompt, pool_rows, stat_key, season, puzzle_date)
        if result:
            results.append(result)
    youngest = _trivia_youngest_question(pool_rows, season, puzzle_date)
    if youngest:
        results.append(youngest)
    archetype = _trivia_archetype_question(pool_rows, season, puzzle_date)
    if archetype:
        results.append(archetype)
    return results


def _trivia_resolve_pool(season: Optional[int]):
    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or _guess_game_default_season(cursor)
        check_season_exists(cursor, resolved_season)
        pool_rows = _guess_game_pool(cursor, resolved_season)
    if not pool_rows:
        raise HTTPException(status_code=404, detail=f"No qualified player pool for season {resolved_season}.")
    return resolved_season, pool_rows


@app.get("/games/trivia/daily")
def get_trivia_daily(season: Optional[int] = None):
    resolved_season, pool_rows = _trivia_resolve_pool(season)
    resolved_date = date.today()
    results = _trivia_build_all(pool_rows, resolved_season, resolved_date)
    if not results:
        raise HTTPException(status_code=404, detail="Could not build today's trivia questions.")
    return {
        "date": resolved_date.isoformat(),
        "season": resolved_season,
        "questions": [q for q, _ in results],
    }


@app.get("/games/trivia/guess")
def guess_trivia(question_id: str, option_id: str, season: Optional[int] = None, puzzle_date: Optional[str] = None):
    resolved_date = _parse_puzzle_date(puzzle_date)
    resolved_season, pool_rows = _trivia_resolve_pool(season)
    results = _trivia_build_all(pool_rows, resolved_season, resolved_date)
    match = next((correct_id for q, correct_id in results if q["id"] == question_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Unknown question {question_id}.")
    return {"correct": option_id == match, "correct_option_id": match}


# ─── Trend Analysis (player career trajectory / team trajectory) ───────────

TREND_PLAYER_STATS = ["pts", "reb", "ast", "stl", "blk", "ts_pct", "usg_pct", "net_rating", "min"]
TREND_TEAM_STATS = [
    "net_rating", "off_rating", "def_rating", "ts_pct", "win_pct",
    "efg_pct", "oreb_pct", "tov_pct", "ftr",  # Four Factors (Dean Oliver)
]


@app.get("/players/history/{player_name}")
def get_player_history(player_name: str):
    """Every season a player appears in player_season_stats, unfiltered (no
    qualified-pool minimum) — trend analysis should show the real trajectory,
    injury-shortened or rookie seasons included, not just the "clean" ones."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)
        cursor.execute(
            f"""
            SELECT season, age, gp, {', '.join(TREND_PLAYER_STATS)}
            FROM player_season_stats
            WHERE player_id = %s
            ORDER BY season ASC;
            """,
            (player_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No season data for {resolved_name}.")

    cols = ["season", "age", "gp"] + TREND_PLAYER_STATS
    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "seasons": [dict(zip(cols, row)) for row in rows],
    }


@app.get("/teams/history/{team_abbr}")
def get_team_history(team_abbr: str):
    """
    Team-level trend: minutes-weighted roster aggregates per season — the
    same computation Trade Analyzer and the win% model use (SUM(min*stat) /
    SUM(min)), done directly in SQL here since it's one aggregate row per
    season rather than a roster to assemble in Python.
    Also includes the Four Factors (Dean Oliver's eFG%/TOV%/OREB%/FTr) —
    same minutes-weighted-roster-average methodology as net_rating/win_pct
    above (this DB has no team-level game log, so "team eFG%" here means
    "this roster's players' own eFG%, weighted by minutes played" — a
    roster-composition proxy, not an official team box score stat. FTr is
    computed as weighted-team-FTA / weighted-team-FGA rather than averaging
    individual FTr ratios directly, to avoid distorting the ratio.
    Note: franchise relocations/renames (e.g. NOH -> NOP, NJN -> BKN) show
    up as separate abbreviations, not stitched into one continuous history.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT season,
                   SUM(min * net_rating) / NULLIF(SUM(min), 0) AS net_rating,
                   SUM(min * off_rating) / NULLIF(SUM(min), 0) AS off_rating,
                   SUM(min * def_rating) / NULLIF(SUM(min), 0) AS def_rating,
                   SUM(min * ts_pct) / NULLIF(SUM(min), 0) AS ts_pct,
                   SUM(min * w_pct) / NULLIF(SUM(min), 0) AS win_pct,
                   SUM(min * efg_pct) / NULLIF(SUM(min), 0) AS efg_pct,
                   SUM(min * oreb_pct) / NULLIF(SUM(min), 0) AS oreb_pct,
                   SUM(min * tov_pct) / NULLIF(SUM(min), 0) AS tov_pct,
                   SUM(min * fta) / NULLIF(SUM(min), 0) AS weighted_fta,
                   SUM(min * fga) / NULLIF(SUM(min), 0) AS weighted_fga,
                   COUNT(*) AS n_players
            FROM player_season_stats
            WHERE team_abbreviation = %s
            GROUP BY season
            ORDER BY season ASC;
            """,
            (team_abbr.upper(),),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for team '{team_abbr.upper()}'.")

    raw_cols = ["season", "net_rating", "off_rating", "def_rating", "ts_pct", "win_pct",
                "efg_pct", "oreb_pct", "tov_pct", "weighted_fta", "weighted_fga", "n_players"]
    seasons = []
    for row in rows:
        entry = dict(zip(raw_cols, row))
        wfta, wfga = entry.pop("weighted_fta"), entry.pop("weighted_fga")
        entry["ftr"] = (wfta / wfga) if wfga else None
        seasons.append(entry)

    return {"team": team_abbr.upper(), "seasons": seasons}


@app.get("/")
def root():
    return {
        "service": "NBA Impact Score API",
        "version": "1.0.0",
        "endpoints": [
            "/impact/raw/{season}",
            "/impact/star/{season}",
            "/impact/player/{player_name}/{season}",
            "/radar/{player_name}?season=",
            "/players/history/{player_name}",
            "/teams/history/{team_abbr}",
            "/trade/teams/{season}",
            "/trade/roster/{team_abbr}/{season}",
            "/trade/simulate",
        ],
    }


# ─── Trade Analyzer ─────────────────────────────────────────────────────────
# Simulates a straight 1-for-1 trade using stats/impact scores already in the
# DB. IMPORTANT CAVEAT (surfaced to the frontend, not hidden): this treats an
# incoming player's own historical stat line as their contribution to the new
# team — a real trade changes role, usage, and minutes, which this can't
# simulate. It's a "what does the roster's raw production balance look like"
# estimate, not a projection of actual on-court chemistry.

TRADE_ROSTER_COLS = [
    "p.player_id", "p.player_name", "p.min", "p.pts", "p.reb", "p.ast",
    "p.net_rating", "p.off_rating", "p.def_rating", "p.ts_pct", "p.usg_pct",
    "p.impact_score_raw", "c.archetype",
]


def _fetch_roster(cursor, team_abbr: str, season: int):
    cursor.execute(
        f"""
        SELECT {', '.join(TRADE_ROSTER_COLS)}
        FROM player_season_stats p
        LEFT JOIN player_clusters c
            ON c.player_id = p.player_id AND c.season = p.season
        WHERE p.team_abbreviation = %s AND p.season = %s
        ORDER BY p.min DESC;
        """,
        (team_abbr.upper(), season),
    )
    cols = ["player_id", "player_name", "min", "pts", "reb", "ast", "net_rating",
            "off_rating", "def_rating", "ts_pct", "usg_pct", "impact_score_raw", "archetype"]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _weighted_avg(roster, key):
    total_min = sum(r["min"] or 0 for r in roster)
    if not total_min:
        return None
    return sum((r["min"] or 0) * (r[key] or 0) for r in roster) / total_min


def _team_summary(roster):
    net_rating = _weighted_avg(roster, "net_rating")
    ts_pct = _weighted_avg(roster, "ts_pct")
    return {
        "n_players": len(roster),
        "net_rating": net_rating,
        "off_rating": _weighted_avg(roster, "off_rating"),
        "def_rating": _weighted_avg(roster, "def_rating"),
        "ts_pct": ts_pct,
        "total_impact_raw": sum(r["impact_score_raw"] or 0 for r in roster),
        "archetype_counts": _archetype_counts(roster),
        "predicted_win_pct": predict_win_pct(net_rating, ts_pct),
    }


def _archetype_counts(roster):
    counts = {}
    for r in roster:
        a = r["archetype"] or "Unclustered"
        counts[a] = counts.get(a, 0) + 1
    return counts


def _swap_roster(roster, outgoing_id, incoming_row):
    new_roster = [r for r in roster if r["player_id"] != outgoing_id]
    new_roster.append(incoming_row)
    return new_roster


def _best_fit_teammate(cursor, moving_player_id: int, season: int, destination_roster: list):
    """Of the moving player's precomputed similar seasons, which (if any) is
    already on the destination roster — a rough "stylistic fit" signal."""
    roster_ids = {r["player_id"] for r in destination_roster}
    if not roster_ids:
        return None
    cursor.execute(
        """
        SELECT similar_player_id, similarity_score
        FROM season_similarity
        WHERE source_player_id = %s AND source_season = %s AND similar_season = %s
        ORDER BY similarity_score DESC;
        """,
        (moving_player_id, season, season),
    )
    by_id = {r["player_id"]: r["player_name"] for r in destination_roster}
    for similar_id, score in cursor.fetchall():
        if similar_id in roster_ids:
            return {"player_name": by_id[similar_id], "similarity_score": float(score)}
    return None


@app.get("/trade/teams/{season}")
def get_trade_teams(season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT team_abbreviation FROM player_season_stats "
            "WHERE season = %s AND team_abbreviation IS NOT NULL ORDER BY 1;",
            (season,),
        )
        teams = [r[0] for r in cursor.fetchall()]
    if not teams:
        raise HTTPException(status_code=404, detail=f"No team data for season {season}.")
    return {"season": season, "teams": teams}


@app.get("/trade/roster/{team_abbr}/{season}")
def get_trade_roster(team_abbr: str, season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        roster = _fetch_roster(cursor, team_abbr, season)
    if not roster:
        raise HTTPException(
            status_code=404,
            detail=f"No roster found for {team_abbr.upper()} in season {season}.",
        )
    return {"team": team_abbr.upper(), "season": season, "roster": roster}


@app.get("/trade/simulate")
def simulate_trade(season: int, team_a: str, player_a_id: int, team_b: str, player_b_id: int):
    if player_a_id == player_b_id:
        raise HTTPException(status_code=400, detail="Can't trade a player for themselves.")

    with get_db() as conn:
        cursor = conn.cursor()
        roster_a = _fetch_roster(cursor, team_a, season)
        roster_b = _fetch_roster(cursor, team_b, season)

        player_a = next((r for r in roster_a if r["player_id"] == player_a_id), None)
        player_b = next((r for r in roster_b if r["player_id"] == player_b_id), None)
        if not player_a:
            raise HTTPException(status_code=404, detail=f"Player {player_a_id} not found on {team_a.upper()} in {season}.")
        if not player_b:
            raise HTTPException(status_code=404, detail=f"Player {player_b_id} not found on {team_b.upper()} in {season}.")

        fit_a_on_b = _best_fit_teammate(cursor, player_a_id, season, [r for r in roster_b if r["player_id"] != player_b_id])
        fit_b_on_a = _best_fit_teammate(cursor, player_b_id, season, [r for r in roster_a if r["player_id"] != player_a_id])

    roster_a_after = _swap_roster(roster_a, player_a_id, player_b)
    roster_b_after = _swap_roster(roster_b, player_b_id, player_a)

    return {
        "season": season,
        "trade": {
            "team_a": {"team": team_a.upper(), "sends": player_a, "receives": player_b, "fit_note": fit_b_on_a},
            "team_b": {"team": team_b.upper(), "sends": player_b, "receives": player_a, "fit_note": fit_a_on_b},
        },
        "team_a_summary": {"before": _team_summary(roster_a), "after": _team_summary(roster_a_after)},
        "team_b_summary": {"before": _team_summary(roster_b), "after": _team_summary(roster_b_after)},
        "caveat": (
            "This treats each player's own historical stats as their contribution to the new team — "
            "a real trade changes role, usage, and minutes, which this can't simulate. Read it as a "
            "roster production balance estimate, not an on-court projection."
        ),
        "predicted_win_pct_note": (
            "predicted_win_pct comes from a Linear Regression model (net rating + true-shooting %, "
            "leave-one-season-out validated: R2=0.92, average error ~2.5 wins over an 82-game season) — "
            "an estimate, not a guarantee."
        ),
    }


ESTIMATED_POSITION_LABELS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}


def _position_label(bpm_position):
    if bpm_position is None:
        return None
    return ESTIMATED_POSITION_LABELS[max(1, min(5, round(bpm_position)))]


@app.get("/players/table/{season}")
def get_players_table(season: int, min_minutes: float = 0.0):
    """
    Every player for a season with traditional, advanced, and plus-minus
    stats in one row — powers a full sortable/filterable player table
    (like Basketball-Reference/CraftedNBA's stat tables), not just a
    single-player lookup.

    "position" here is NOT an official roster position — this project has
    no position data anywhere in its pipeline (confirmed: not in the raw
    nba_api CSVs, not fetchable without a new data source). It's derived
    from BPM's own position-estimation regression (scripts/build_bpm_vorp.py),
    rounded to the nearest of 5 buckets — a real, disclosed estimate, not a
    guess dressed up as fact. Treat it as "plays like a ~PG", not a roster fact.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_id, player_name, team_abbreviation, age, gp, min,
                   pts, reb, ast, stl, blk, tov, fg_pct, fg3_pct, ft_pct,
                   fgm, fga, fg3m, fg3a, ftm, fta, w_pct, plus_minus,
                   ts_pct, usg_pct, off_rating, def_rating, net_rating,
                   ast_pct, reb_pct, efg_pct, oreb_pct, tov_pct,
                   bpm, obpm, dbpm, vorp, bpm_position
            FROM player_season_stats
            WHERE season = %s AND min >= %s
            ORDER BY min DESC;
            """,
            (season, min_minutes),
        )
        rows = cursor.fetchall()

    def r3(v):
        return round(float(v), 3) if v is not None else None

    def r1(v):
        return round(float(v), 1) if v is not None else None

    results = []
    for row in rows:
        (player_id, player_name, team_abbreviation, age, gp, minutes,
         pts, reb, ast, stl, blk, tov, fg_pct, fg3_pct, ft_pct,
         fgm, fga, fg3m, fg3a, ftm, fta, w_pct, plus_minus,
         ts_pct, usg_pct, off_rating, def_rating, net_rating,
         ast_pct, reb_pct, efg_pct, oreb_pct, tov_pct,
         bpm, obpm, dbpm, vorp, bpm_position) = row
        results.append({
            "player_id": int(player_id), "player_name": player_name,
            "team_abbreviation": team_abbreviation, "age": age, "gp": gp,
            "min": r1(minutes), "position": _position_label(bpm_position),
            "traditional": {
                "pts": r1(pts), "reb": r1(reb), "ast": r1(ast), "stl": r1(stl),
                "blk": r1(blk), "tov": r1(tov), "fgm": r1(fgm), "fga": r1(fga),
                "fg_pct": r3(fg_pct), "fg3m": r1(fg3m), "fg3a": r1(fg3a),
                "fg3_pct": r3(fg3_pct), "ftm": r1(ftm), "fta": r1(fta),
                "ft_pct": r3(ft_pct), "w_pct": r3(w_pct), "plus_minus": r1(plus_minus),
            },
            "advanced": {
                "ts_pct": r3(ts_pct), "efg_pct": r3(efg_pct), "usg_pct": r3(usg_pct),
                "off_rating": r1(off_rating), "def_rating": r1(def_rating),
                "net_rating": r1(net_rating), "ast_pct": r3(ast_pct),
                "reb_pct": r3(reb_pct), "oreb_pct": r3(oreb_pct), "tov_pct": r3(tov_pct),
            },
            "plus_minus": {
                "bpm": r3(bpm), "obpm": r3(obpm), "dbpm": r3(dbpm), "vorp": r3(vorp),
            },
        })

    return {"season": season, "min_minutes": min_minutes, "count": len(results), "results": results}


@app.get("/impact/bpm/{season}")
def get_bpm_leaderboard(season: int, top_n: int = 20, min_minutes: float = 20.0, min_games: int = 30):
    """
    Top players by BPM (Box Plus/Minus) for a season — see
    scripts/build_bpm_vorp.py's module docstring for the full methodology
    and honesty caveats (this is an independent reproduction of the
    published BPM 2.0 formula, not Basketball-Reference's own numbers;
    relative ranking is verified sound, absolute scale runs somewhat
    hotter than the real thing at the top of the leaderboard). A modest
    minutes/games floor is applied by default — like every other rate-stat
    leaderboard in this project, unfiltered per-100-possession numbers are
    dominated by small-sample noise from low-minute players.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, team_abbreviation, pts, min, bpm, obpm, dbpm, vorp
            FROM player_season_stats
            WHERE season = %s AND bpm IS NOT NULL AND min >= %s AND gp >= %s
            ORDER BY bpm DESC
            LIMIT %s;
            """,
            (season, min_minutes, min_games, top_n),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No BPM data for season {season} (min>={min_minutes}, gp>={min_games}). "
                   f"Run scripts/build_bpm_vorp.py if this table hasn't been populated for this season yet.",
        )

    return {
        "season": season,
        "min_minutes": min_minutes,
        "min_games": min_games,
        "results": [
            {
                "rank": i + 1,
                "player_name": r[0],
                "team_abbreviation": r[1],
                "pts": round(float(r[2]), 1),
                "min": round(float(r[3]), 1),
                "bpm": round(float(r[4]), 2),
                "obpm": round(float(r[5]), 2),
                "dbpm": round(float(r[6]), 2),
                "vorp": round(float(r[7]), 2),
            }
            for i, r in enumerate(rows)
        ],
    }


@app.get("/impact/raw/{season}")
def get_raw_impact(season: int, top_n: int = 20):
    """Top players by raw impact score for a given season."""
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, pts, w_pct, impact_score_raw
            FROM player_season_stats
            WHERE season = %s AND impact_score_raw IS NOT NULL
            ORDER BY impact_score_raw DESC
            LIMIT %s;
            """,
            (season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": season,
        "type": "raw",
        "results": [
            {
                "rank": i + 1,
                "player_name": r[0],
                "pts": round(float(r[1]), 1),
                "w_pct": round(float(r[2]), 3),
                "impact_score_raw": round(float(r[3]), 4),
            }
            for i, r in enumerate(rows)
        ],
    }


@app.get("/impact/star/{season}")
def get_star_impact(season: int, top_n: int = 20):
    """Top star-qualified players by star impact score for a given season."""
    with get_db() as conn:
        cursor = conn.cursor()
        check_season_exists(cursor, season)

        cursor.execute(
            """
            SELECT player_name, pts, w_pct, impact_score_star
            FROM player_season_stats
            WHERE season = %s AND impact_score_star IS NOT NULL
            ORDER BY impact_score_star DESC
            LIMIT %s;
            """,
            (season, top_n),
        )
        rows = cursor.fetchall()

    return {
        "season": season,
        "type": "star",
        "results": [
            {
                "rank": i + 1,
                "player_name": r[0],
                "pts": round(float(r[1]), 1),
                "w_pct": round(float(r[2]), 3),
                "impact_score_star": round(float(r[3]), 4),
            }
            for i, r in enumerate(rows)
        ],
    }


DRAFT_MATURITY_CUTOFF = 2020  # rookie_season_int 2021 -> up to 5 seasons possible by our 2025 max
DRAFT_PICK_BUCKETS = [
    (1, 5, "1-5"), (6, 14, "6-14"), (15, 30, "15-30"), (31, 45, "31-45"), (46, 60, "46-60"),
]


def _pick_bucket_label(overall_pick):
    if overall_pick is None:
        return None
    for lo, hi, label in DRAFT_PICK_BUCKETS:
        if lo <= overall_pick <= hi:
            return label
    return "61+"


@app.get("/draft/value-curve")
def get_draft_value_curve():
    """
    Average career impact_score_raw by pick-range bucket, across draft
    classes mature enough to judge fairly (draft_year <= DRAFT_MATURITY_
    CUTOFF, so every included class has had several seasons to accumulate
    value) — a 'draft value chart' grounded in actual career outcomes
    rather than a scout's opinion of pick worth.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.overall_pick,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_type = 'Draft' AND d.overall_pick IS NOT NULL
              AND d.draft_year <= %s
            GROUP BY d.player_id, d.overall_pick;
            """,
            (DRAFT_MATURITY_CUTOFF,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No draft data loaded yet. Run scripts/fetch_draft_history.py first.",
        )

    buckets = {label: [] for _, _, label in DRAFT_PICK_BUCKETS}
    for overall_pick, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        if label in buckets:
            buckets[label].append(float(career_impact))

    return {
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "note": "Only includes draft classes through the cutoff year so every "
                "player has had a fair number of seasons to accumulate career value.",
        "buckets": [
            {
                "range": label,
                "n_players": len(values),
                "avg_career_impact_raw": round(sum(values) / len(values), 3) if values else None,
            }
            for _, _, label in DRAFT_PICK_BUCKETS
            for values in [buckets[label]]
        ],
    }


@app.get("/draft/best-value")
def get_draft_best_value(limit: int = 15, worst: bool = False):
    """
    Picks whose career impact_score_raw deviates most from their pick
    bucket's average — biggest positive deviation = best value (steals),
    biggest negative = underperformed their slot (not necessarily 'busts'
    in the pejorative sense — injuries, unlucky context, etc. aren't
    separated out here, just the statistical gap from expectation).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.player_id, d.player_name, d.draft_year, d.overall_pick, d.team_abbreviation,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_type = 'Draft' AND d.overall_pick IS NOT NULL
              AND d.draft_year <= %s
            GROUP BY d.player_id, d.player_name, d.draft_year, d.overall_pick, d.team_abbreviation;
            """,
            (DRAFT_MATURITY_CUTOFF,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No draft data loaded yet. Run scripts/fetch_draft_history.py first.",
        )

    bucket_values = {}
    for _, _, _, overall_pick, _, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        bucket_values.setdefault(label, []).append(float(career_impact))
    bucket_avg = {label: sum(v) / len(v) for label, v in bucket_values.items()}

    scored = []
    for player_id, player_name, draft_year, overall_pick, team, career_impact in rows:
        label = _pick_bucket_label(overall_pick)
        expected = bucket_avg.get(label, 0.0)
        scored.append({
            "player_id": int(player_id),
            "player_name": player_name,
            "draft_year": draft_year,
            "overall_pick": overall_pick,
            "team_abbreviation": team,
            "career_impact_raw": round(float(career_impact), 3),
            "expected_impact_raw": round(expected, 3),
            "value_over_expectation": round(float(career_impact) - expected, 3),
        })

    scored.sort(key=lambda r: r["value_over_expectation"], reverse=not worst)
    return {
        "maturity_cutoff_draft_year": DRAFT_MATURITY_CUTOFF,
        "mode": "worst" if worst else "best",
        "results": scored[:limit],
    }


# Registered AFTER /draft/value-curve and /draft/best-value — a path param
# route matches any string positionally, so if this were declared first it
# would swallow those two literal paths before they ever got a chance to
# match (hit exactly this bug once already this session with /backtest/*).
@app.get("/draft/{draft_year}")
def get_draft_class(draft_year: int):
    """
    One draft class's picks with career value to date, using impact_score_raw
    (the same 'total roster impact' currency Trade Analyzer already sums) —
    not a new formula invented for this feature.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT d.player_id, d.player_name, d.overall_pick, d.round_number, d.round_pick,
                   d.team_abbreviation, d.organization, d.organization_type, d.rookie_season_int,
                   COUNT(p.season) AS seasons_played,
                   COALESCE(SUM(p.impact_score_raw), 0) AS career_impact_raw,
                   COALESCE(AVG(p.impact_score_raw), 0) AS avg_impact_raw,
                   COUNT(*) FILTER (WHERE p.impact_score_star IS NOT NULL) AS star_seasons
            FROM draft_history d
            LEFT JOIN player_season_stats p ON p.player_id = d.player_id
            WHERE d.draft_year = %s AND d.draft_type = 'Draft'
            GROUP BY d.player_id, d.player_name, d.overall_pick, d.round_number, d.round_pick,
                     d.team_abbreviation, d.organization, d.organization_type, d.rookie_season_int
            ORDER BY d.overall_pick ASC NULLS LAST;
            """,
            (draft_year,),
        )
        rows = cursor.fetchall()

        if not rows:
            cursor.execute("SELECT MIN(draft_year), MAX(draft_year) FROM draft_history;")
            bounds = cursor.fetchone()
            available = f"{bounds[0]}–{bounds[1]}" if bounds and bounds[0] is not None else "none loaded yet — run scripts/fetch_draft_history.py"
            raise HTTPException(status_code=404, detail=f"No draft data for {draft_year}. Available: {available}.")

    return {
        "draft_year": draft_year,
        "rookie_season_int": draft_year + 1,
        "results": [
            {
                "overall_pick": r[2],
                "round_number": r[3],
                "round_pick": r[4],
                "player_id": int(r[0]),
                "player_name": r[1],
                "team_abbreviation": r[5],
                "organization": r[6],
                "organization_type": r[7],
                "seasons_played": r[9],
                "career_impact_raw": round(float(r[10]), 3),
                "avg_impact_raw": round(float(r[11]), 3),
                "star_seasons": r[12],
            }
            for r in rows
        ],
    }


@app.get("/impact/player/{player_name}/{season}")
def get_player_impact(player_name: str, season: int):
    """Get both raw and star impact scores for a specific player-season."""
    with get_db() as conn:
        cursor = conn.cursor()

        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT player_name, pts, ts_pct, usg_pct, net_rating,
                   w_pct, min, impact_score_raw, impact_score_star
            FROM player_season_stats
            WHERE player_id = %s AND season = %s;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No data for {resolved_name} in season {season}.",
            )

    return {
        "player_name": row[0],
        "player_id": player_id,
        "season": season,
        "stats": {
            "pts": round(float(row[1]), 1),
            "ts_pct": round(float(row[2]), 3),
            "usg_pct": round(float(row[3]), 3),
            "net_rating": round(float(row[4]), 1),
            "w_pct": round(float(row[5]), 3),
            "min": round(float(row[6]), 1),
        },
        "impact_score_raw": round(float(row[7]), 4) if row[7] is not None else None,
        "impact_score_star": round(float(row[8]), 4) if row[8] is not None else None,
    }


@app.get("/meta/current")
def get_current_meta():
    """
    Current-season standings + team comparison stats from local DB.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        db_latest_season = get_latest_season(cursor)
        current_live_season = get_current_nba_season()
        season = max(db_latest_season, current_live_season)
        badges = get_team_badges()
        nba_api_standings = fetch_nba_api_standings(season)
        cdn_standings = fetch_nba_cdn_standings()
        external_standings = fetch_balldontlie_standings(season)
        live_team_stats = fetch_nba_api_team_stats(season)

        cursor.execute(
            """
            SELECT player_name, pts
            FROM player_season_stats
            WHERE season = %s AND pts IS NOT NULL
            ORDER BY pts DESC
            LIMIT 1;
            """,
            (db_latest_season,),
        )
        top_scorer_row = cursor.fetchone()

        cursor.execute(
            """
            SELECT team_abbreviation, MAX(w_pct) AS w_pct
            FROM player_season_stats
            WHERE season = %s AND team_abbreviation IS NOT NULL AND w_pct IS NOT NULL
            GROUP BY team_abbreviation;
            """,
            (db_latest_season,),
        )
        standing_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                team_abbreviation,
                SUM(pts * gp) / NULLIF(MAX(gp), 0) AS ppg,
                SUM(reb * gp) / NULLIF(MAX(gp), 0) AS rpg,
                SUM(ast * gp) / NULLIF(MAX(gp), 0) AS apg,
                SUM(stl * gp) / NULLIF(MAX(gp), 0) AS spg,
                SUM(blk * gp) / NULLIF(MAX(gp), 0) AS bpg,
                SUM(fg_pct * gp) / NULLIF(SUM(gp), 0) AS fg_pct,
                SUM(fg3_pct * gp) / NULLIF(SUM(gp), 0) AS fg3_pct,
                SUM(ft_pct * gp) / NULLIF(SUM(gp), 0) AS ft_pct
            FROM player_season_stats
            WHERE season = %s
              AND team_abbreviation IS NOT NULL
              AND team_abbreviation <> 'TOT'
              AND gp IS NOT NULL
              AND gp > 0
            GROUP BY team_abbreviation;
            """,
            (db_latest_season,),
        )
        team_rows = cursor.fetchall()

    standings = []
    for abbr, w_pct in standing_rows:
        if abbr not in TEAM_META or w_pct is None:
            continue
        wins = int(round(float(w_pct) * 82))
        losses = max(0, 82 - wins)
        standings.append(
            {
                "abbr": abbr,
                "team": TEAM_META[abbr]["name"],
                "conference": TEAM_META[abbr]["conference"],
                "w": wins,
                "l": losses,
                "w_pct": float(w_pct),
            }
        )

    east = sorted([s for s in standings if s["conference"] == "eastern"], key=lambda x: x["w_pct"], reverse=True)
    west = sorted([s for s in standings if s["conference"] == "western"], key=lambda x: x["w_pct"], reverse=True)

    def decorate_with_rank_and_gb(rows):
        if not rows:
            return []
        leader_w, leader_l = rows[0]["w"], rows[0]["l"]
        out = []
        for i, row in enumerate(rows, start=1):
            gb = ((leader_w - row["w"]) + (row["l"] - leader_l)) / 2
            out.append(
                {
                    "rank": i,
                    "abbr": row["abbr"],
                    "team": row["team"],
                    "w": row["w"],
                    "l": row["l"],
                    "pct": f".{int(round(row['w_pct'] * 1000)):03d}",
                    "gb": "-" if i == 1 else f"{gb:.1f}".rstrip("0").rstrip("."),
                    "last10": "-",
                    "streak": "-",
                    "logo": badges.get(row["abbr"]),
                }
            )
        return out

    db_team_stats = {}
    for row in team_rows:
        abbr = row[0]
        if abbr not in TEAM_META:
            continue

        fg_pct = float(row[6]) if row[6] is not None else 0.0
        fg3_pct = float(row[7]) if row[7] is not None else 0.0
        ft_pct = float(row[8]) if row[8] is not None else 0.0

        # Normalize to percent style expected by frontend (e.g. 48.1).
        if fg_pct <= 1:
            fg_pct *= 100
        if fg3_pct <= 1:
            fg3_pct *= 100
        if ft_pct <= 1:
            ft_pct *= 100

        db_team_stats[abbr] = {
            "name": TEAM_META[abbr]["name"],
            "abbr": abbr,
            "ppg": round(float(row[1] or 0), 1),
            "rpg": round(float(row[2] or 0), 1),
            "apg": round(float(row[3] or 0), 1),
            "spg": round(float(row[4] or 0), 1),
            "bpg": round(float(row[5] or 0), 1),
            "fgPct": round(fg_pct, 1),
            "threePct": round(fg3_pct, 1),
            "ftPct": round(ft_pct, 1),
            "logo": badges.get(abbr),
        }

    if live_team_stats:
        for abbr, item in live_team_stats.items():
            item["logo"] = badges.get(abbr)

    if nba_api_standings:
        for conf in ("eastern", "western"):
            for item in nba_api_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])
    elif cdn_standings:
        for conf in ("eastern", "western"):
            for item in cdn_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])
    elif external_standings:
        for conf in ("eastern", "western"):
            for item in external_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])

    live_pts_leaders = fetch_nba_api_player_leaders("pts", season, 1)
    live_top_scorer = None
    if live_pts_leaders and live_pts_leaders.get("results"):
        p0 = live_pts_leaders["results"][0]
        live_top_scorer = {
            "player_name": p0["player_name"],
            "ppg": p0["value"],
        }

    return {
        "season": season,
        "standings_source": (
            "nba_api" if nba_api_standings
            else ("nba_cdn" if cdn_standings else ("balldontlie" if external_standings else "local_db"))
        ),
        "standings": nba_api_standings or cdn_standings or external_standings or {
            "eastern": decorate_with_rank_and_gb(east),
            "western": decorate_with_rank_and_gb(west),
        },
        "team_stats": live_team_stats or db_team_stats,
        "top_scorer": live_top_scorer or (
            {
                "player_name": top_scorer_row[0],
                "ppg": round(float(top_scorer_row[1]), 1),
            }
            if top_scorer_row
            else None
        ),
    }


@app.get("/media/player-image/{player_name}")
def get_player_image(player_name: str):
    """
    Player image from TheSportsDB by player name.
    """
    name = (player_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="player_name is required.")

    key = name.lower()
    now = time.time()
    cached = _CACHE["player_images"].get(key)
    if cached and (now - cached["ts"] < _CACHE_TTL_SECONDS):
        return {"player_name": name, "image_url": cached["url"]}

    try:
        data = fetch_json(
            f"https://www.thesportsdb.com/api/v1/json/123/searchplayers.php?p={quote(name)}"
        )
        players = data.get("player", []) or []
        image_url = None
        if players:
            best = players[0]
            image_url = best.get("strThumb") or best.get("strCutout") or best.get("strRender")
        _CACHE["player_images"][key] = {"ts": now, "url": image_url}
        return {"player_name": name, "image_url": image_url}
    except Exception:
        return {"player_name": name, "image_url": None}


@app.get("/players/search")
def search_players_live(q: str, limit: int = 20):
    """
    Live-first player autocomplete (nba_api), DB fallback.
    """
    query = (q or "").strip()
    if len(query) < 2:
        return {"query": query, "results": []}

    safe_limit = max(1, min(int(limit), 50))
    live = fetch_nba_api_player_search(query, safe_limit)
    if live:
        return {"query": query, "results": live}

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT player_name
            FROM player_season_stats
            WHERE LOWER(player_name) LIKE LOWER(%s)
            ORDER BY player_name ASC
            LIMIT %s;
            """,
            (f"%{query}%", safe_limit),
        )
        rows = cursor.fetchall()

    return {"query": query, "results": [r[0] for r in rows]}


@app.get("/players/profile/{player_name}")
def get_player_profile(player_name: str, season: Optional[int] = None):
    """
    Player profile stats from local DB.
    If season is omitted, returns latest available season for the player.
    """
    if season is None:
        season = get_current_nba_season()

    live_profile = fetch_nba_api_player_profile(player_name, season)
    if live_profile:
        return live_profile

    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = find_player(cursor, player_name)

        cursor.execute(
            """
            SELECT
                player_name, team_abbreviation, season, age, min,
                pts, reb, ast, stl, blk,
                fg_pct, fg3_pct, ft_pct
            FROM player_season_stats
            WHERE player_id = %s AND season = %s
            LIMIT 1;
            """,
            (player_id, season),
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                """
                SELECT MAX(season)
                FROM player_season_stats
                WHERE player_id = %s;
                """,
                (player_id,),
            )
            fallback_season = cursor.fetchone()[0]
            if fallback_season is not None and int(fallback_season) != int(season):
                cursor.execute(
                    """
                    SELECT
                        player_name, team_abbreviation, season, age, min,
                        pts, reb, ast, stl, blk,
                        fg_pct, fg3_pct, ft_pct
                    FROM player_season_stats
                    WHERE player_id = %s AND season = %s
                    LIMIT 1;
                    """,
                    (player_id, fallback_season),
                )
                row = cursor.fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No profile data for {resolved_name}.",
            )

    return {
        "player_id": int(player_id),
        "player_name": row[0],
        "team_abbr": row[1],
        "season": int(row[2]),
        "age": round(float(row[3]), 1) if row[3] is not None else None,
        "min": round(float(row[4]), 1) if row[4] is not None else None,
        "stats": {
            "ppg": round(float(row[5]), 1) if row[5] is not None else None,
            "rpg": round(float(row[6]), 1) if row[6] is not None else None,
            "apg": round(float(row[7]), 1) if row[7] is not None else None,
            "spg": round(float(row[8]), 1) if row[8] is not None else None,
            "bpg": round(float(row[9]), 1) if row[9] is not None else None,
            "fgPct": round(float(row[10]) * 100, 1) if row[10] is not None and float(row[10]) <= 1 else (round(float(row[10]), 1) if row[10] is not None else None),
            "threePct": round(float(row[11]) * 100, 1) if row[11] is not None and float(row[11]) <= 1 else (round(float(row[11]), 1) if row[11] is not None else None),
            "ftPct": round(float(row[12]) * 100, 1) if row[12] is not None and float(row[12]) <= 1 else (round(float(row[12]), 1) if row[12] is not None else None),
        },
    }


@app.get("/leaders/{stat_key}")
def get_stat_leaders(stat_key: str, season: Optional[int] = None, top_n: int = 10):
    """
    Top-N leaders for a selected stat in a season (latest season by default).
    """
    stat_map = {
        "pts": {"columns": ["pts"], "label": "PTS", "is_pct": False},
        "reb": {"columns": ["reb"], "label": "REB", "is_pct": False},
        "ast": {"columns": ["ast"], "label": "AST", "is_pct": False},
        "dreb": {"columns": ["dreb"], "label": "DREB", "is_pct": False},
        "oreb": {"columns": ["oreb"], "label": "OREB", "is_pct": False},
        "plus_minus": {"columns": ["plus_minus"], "label": "+/-", "is_pct": False},
        "stl": {"columns": ["stl"], "label": "STL", "is_pct": False},
        "blk": {"columns": ["blk"], "label": "BLK", "is_pct": False},
        "tov": {"columns": ["tov"], "label": "TOV", "is_pct": False},
        "fg_pct": {"columns": ["fg_pct"], "label": "FG%", "is_pct": True},
        "fg3_pct": {"columns": ["fg3_pct", "three_pct"], "label": "3P%", "is_pct": True},
        "ft_pct": {"columns": ["ft_pct"], "label": "FT%", "is_pct": True},
        "fg3m": {"columns": ["fg3m", "fg3"], "label": "3PM", "is_pct": False},
    }

    key = (stat_key or "").strip().lower()
    if key not in stat_map:
        raise HTTPException(status_code=400, detail=f"Unsupported stat '{stat_key}'.")

    safe_top_n = max(1, min(int(top_n), 50))

    with get_db() as conn:
        cursor = conn.cursor()
        if season is None:
            season = max(get_latest_season(cursor), get_current_nba_season())

    live_leaders = fetch_nba_api_player_leaders(key, season, safe_top_n)
    if live_leaders and live_leaders.get("results"):
        return live_leaders

    with get_db() as conn:
        cursor = conn.cursor()

        selected_col = None
        for candidate_col in stat_map[key]["columns"]:
            if column_exists(cursor, "player_season_stats", candidate_col):
                selected_col = candidate_col
                break

        if selected_col is None:
            raise HTTPException(
                status_code=400,
                detail=f"Stat '{key}' is not available in this database.",
            )

        cursor.execute(
            f"""
            SELECT player_id, player_name, team_abbreviation, {selected_col}
            FROM player_season_stats
            WHERE season = %s
              AND {selected_col} IS NOT NULL
              AND team_abbreviation IS NOT NULL
              AND team_abbreviation <> 'TOT'
            ORDER BY {selected_col} DESC
            LIMIT %s;
            """,
            (season, safe_top_n),
        )
        rows = cursor.fetchall()

    def to_display_value(raw_value):
        if raw_value is None:
            return None
        value = float(raw_value)
        if stat_map[key]["is_pct"] and value <= 1:
            value *= 100
        return round(value, 2)

    return {
        "season": int(season),
        "stat_key": key,
        "stat_label": stat_map[key]["label"],
        "results": [
            {
                "rank": i + 1,
                "player_id": row[0],
                "player_name": row[1],
                "team_abbr": row[2],
                "value": to_display_value(row[3]),
            }
            for i, row in enumerate(rows)
        ],
    }


def _attach_rest_tags(games: list, date_str: str):
    """Real rest-days context for each team in each game, from the real
    schedule already on file (team_game_fatigue). Purely informational —
    if the fatigue table hasn't been refreshed recently (it's a script a
    human has to re-run, like the Prediction Ledger's snapshot script),
    a team's rest data just won't be there yet, which shows up here as
    null rather than a stale guess."""
    abbrs = {g["away"]["abbr"] for g in games} | {g["home"]["abbr"] for g in games}
    if not abbrs:
        return games
    try:
        game_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return games

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.team_game_fatigue');")
        if cursor.fetchone()[0] is None:
            return games
        cursor.execute(
            """SELECT DISTINCT ON (team_abbreviation) team_abbreviation, game_date
               FROM team_game_fatigue
               WHERE team_abbreviation = ANY(%s) AND game_date < %s
               ORDER BY team_abbreviation, game_date DESC;""",
            (list(abbrs), game_date),
        )
        last_game_by_team = {r[0]: r[1] for r in cursor.fetchall()}

    for g in games:
        rest = {}
        for side in ("away", "home"):
            abbr = g[side]["abbr"]
            last_date = last_game_by_team.get(abbr)
            if last_date is None:
                rest[side] = None
                continue
            rest_days = (game_date - last_date).days - 1
            rest[side] = {"rest_days": rest_days, "is_b2b": rest_days == 0}

        g["away"]["rest"] = rest["away"]
        g["home"]["rest"] = rest["home"]
        if rest["away"] and rest["home"] and rest["away"]["rest_days"] != rest["home"]["rest_days"]:
            g["away"]["rest"]["rest_disadvantage"] = rest["away"]["rest_days"] < rest["home"]["rest_days"]
            g["home"]["rest"]["rest_disadvantage"] = rest["home"]["rest_days"] < rest["away"]["rest_days"]
    return games


@app.get("/games/by-date")
def get_games_by_date(date: Optional[str] = None):
    """
    Games for a given date (YYYY-MM-DD). Defaults to today. Each team
    object gets a real "rest" field (rest_days, is_b2b, and a
    rest_disadvantage flag when the two teams' real rest days differ)
    computed from the real schedule in team_game_fatigue when available.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    games = fetch_nba_games_by_date(date)
    games = _attach_rest_tags(games, date)
    return {
        "date": date,
        "games": games,
    }


@app.get("/games/boxscore/{game_id}")
def get_game_boxscore(game_id: str):
    """
    Traditional box score for a game.
    """
    return {
        "game_id": game_id,
        "boxscore": fetch_boxscore(game_id),
    }


@app.get("/news/current")
def get_current_news(date: Optional[str] = None, limit: int = 20):
    """
    Current-day NBA news from public RSS feeds.
    """
    safe_limit = max(1, min(int(limit), 50))
    items = fetch_current_news(date, safe_limit)
    return {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "items": items,
    }


# ─── Vegas vs. Machine: Championship Odds Scanner ───────────────────────────
#
# Cross-references real live NBA championship-winner odds (The Odds API)
# against a simple, honestly-labeled team-strength proxy built from real
# data this project already has (each team's real win percentage this
# season, from player_season_stats). This is NOT a trained championship-
# probability model — no such model exists in this project — so it's
# disclosed as a naive proxy throughout, not represented as validated.
#
# The odds API has no NBA MVP/DPOY/ROY futures market (checked directly
# against the live API before building this), only game lines and
# championship-winner outrights, which is why this compares team odds
# rather than the MVP-vs-market idea originally proposed.
#
# Vig removal uses Shin's (1992) method, implemented directly here rather
# than via the `shin` PyPI package — that package requires a Rust
# toolchain that fails to build against this Python version. The formula
# itself is the same one that package implements, verified here against a
# real 30-team live market before use (sane z ~1%, probabilities sum to 1,
# rank order preserved, favorites get a slightly larger de-vig haircut
# than longshots — the expected, documented behavior of Shin's method).

def shin_probabilities(decimal_odds):
    """
    Shin's method for removing bookmaker overround under the assumption
    that a fraction z of stake comes from better-informed bettors. Solves
    for z such that the resulting probabilities sum to exactly 1, then
    returns (probabilities, z).
    """
    pi = [1.0 / o for o in decimal_odds]
    total_pi = sum(pi)

    def implied(z):
        return [
            (math.sqrt(z * z + 4 * (1 - z) * (p_i ** 2) / total_pi) - z) / (2 * (1 - z))
            for p_i in pi
        ]

    def sum_minus_one(z):
        if z <= 0:
            return math.sqrt(total_pi) - 1.0
        return sum(implied(z)) - 1.0

    if sum_minus_one(0.0) <= 0:
        z = 0.0
    else:
        lo, hi = 0.0, 0.499999
        while sum_minus_one(hi) > 0 and hi < 0.999999:
            hi = 1 - (1 - hi) / 2
        z = brentq(sum_minus_one, lo, hi, xtol=1e-12)

    probabilities = [p_i / math.sqrt(total_pi) for p_i in pi] if z <= 0 else implied(z)
    return probabilities, z


def _fetch_championship_odds_live():
    if not ODDS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ODDS_API_KEY isn't configured — set it in api/.env to enable the championship odds scanner.",
        )
    try:
        resp = requests.get(
            "https://api.the-odds-api.com/v4/sports/basketball_nba_championship_winner/odds/",
            params={"apiKey": ODDS_API_KEY, "regions": "us", "markets": "outrights"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Live odds fetch failed: {e}")

    if not events or not events[0].get("bookmakers"):
        raise HTTPException(status_code=404, detail="No live championship odds available right now.")

    # Devig each bookmaker's own full outcome set independently (each book
    # has its own overround), then average the devigged probability per
    # team across books for a more robust market consensus. Every
    # individual (book, odds, devigged probability) triple is kept too —
    # not just the average — so real book-to-book disagreement is visible
    # rather than smoothed away.
    per_team_probs = {}
    per_team_raw_odds = {}
    per_team_books = {}
    books_used = []
    z_values = []

    for book in events[0]["bookmakers"]:
        markets = book.get("markets", [])
        if not markets:
            continue
        outcomes = markets[0].get("outcomes", [])
        if len(outcomes) < 2:
            continue
        book_title = book.get("title", book.get("key"))
        names = [o["name"] for o in outcomes]
        odds = [o["price"] for o in outcomes]
        probs, z = shin_probabilities(odds)
        books_used.append(book_title)
        z_values.append(z)
        for name, odd, prob in zip(names, odds, probs):
            per_team_probs.setdefault(name, []).append(prob)
            per_team_raw_odds.setdefault(name, []).append(odd)
            per_team_books.setdefault(name, []).append({
                "book": book_title, "odds": odd, "probability": round(prob, 4),
            })

    if not per_team_probs:
        raise HTTPException(status_code=404, detail="Live odds response had no usable outcomes.")

    def _stdev(values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))

    return {
        "last_update": events[0]["bookmakers"][0].get("last_update"),
        "books_used": books_used,
        "avg_z": round(sum(z_values) / len(z_values), 4) if z_values else None,
        "team_market_probability": {
            name: round(sum(probs) / len(probs), 4) for name, probs in per_team_probs.items()
        },
        "team_probability_spread": {
            name: round(_stdev(probs), 4) for name, probs in per_team_probs.items()
        },
        "team_best_odds": {
            name: round(max(odds), 2) for name, odds in per_team_raw_odds.items()
        },
        "team_worst_odds": {
            name: round(min(odds), 2) for name, odds in per_team_raw_odds.items()
        },
        "team_books": per_team_books,
    }


def get_championship_odds_cached():
    cached = _CACHE["championship_odds"]
    if cached["data"] and time.time() - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["data"]
    data = _fetch_championship_odds_live()
    _CACHE["championship_odds"] = {"ts": time.time(), "data": data}
    return data


def _real_standings_win_pct(season: int):
    """
    Same source-priority chain /meta/current uses (live nba_api -> NBA CDN
    -> balldontlie -> local DB last resort) — reused here rather than
    re-derived, because a naive MAX(w_pct) GROUP BY team over
    player_season_stats turned out to give nonsense (1.000 for several
    teams) for the current in-progress season: that column is each
    player's own win rate over the games THEY personally played, so a
    player who only appeared in a short hot streak for a team distorts the
    team-level MAX. The real standings feeds report the team's actual
    win-loss record directly.
    """
    standings = (
        fetch_nba_api_standings(season)
        or fetch_nba_cdn_standings()
        or fetch_balldontlie_standings(season)
    )
    win_pct_by_abbr = {}
    if standings:
        for conf in ("eastern", "western"):
            for item in standings.get(conf, []):
                try:
                    win_pct_by_abbr[item["abbr"]] = float(item["pct"])
                except (KeyError, ValueError, TypeError):
                    continue
    return win_pct_by_abbr


@app.get("/odds/championship")
def get_championship_odds_scanner():
    """
    Real live championship-winner odds (Shin's-method devigged) vs. a
    naive real-win-percentage proxy, sorted by the size of the gap between
    them. See module docstring above for what this is and isn't.
    """
    odds_data = get_championship_odds_cached()

    with get_db() as conn:
        cursor = conn.cursor()
        season = get_latest_season(cursor)

    win_pct_by_abbr = _real_standings_win_pct(season)

    total_win_pct = sum(win_pct_by_abbr.values()) or 1.0
    proxy_prob_by_abbr = {abbr: wp / total_win_pct for abbr, wp in win_pct_by_abbr.items()}

    rows = []
    for team_name, market_prob in odds_data["team_market_probability"].items():
        abbr = TEAM_NAME_TO_ABBR.get(team_name)
        proxy_prob = proxy_prob_by_abbr.get(abbr) if abbr else None
        spread = odds_data["team_probability_spread"].get(team_name)
        rows.append({
            "team_name": team_name,
            "team_abbreviation": abbr,
            "market_probability": market_prob,
            "probability_spread": spread,
            "best_odds": odds_data["team_best_odds"].get(team_name),
            "worst_odds": odds_data["team_worst_odds"].get(team_name),
            "books": odds_data["team_books"].get(team_name, []),
            "proxy_probability": round(proxy_prob, 4) if proxy_prob is not None else None,
            "value": round(market_prob - proxy_prob, 4) if proxy_prob is not None else None,
            "win_pct": round(win_pct_by_abbr.get(abbr), 3) if abbr in win_pct_by_abbr else None,
        })

    rows.sort(key=lambda r: abs(r["value"]) if r["value"] is not None else -1, reverse=True)

    return {
        "season": season,
        "last_update": odds_data["last_update"],
        "books_used": odds_data["books_used"],
        "avg_z": odds_data["avg_z"],
        "methodology": (
            "market_probability is Shin's-method devigged, averaged across all real bookmakers "
            "in the live response. proxy_probability is each team's real win percentage this "
            "season, normalized to sum to 1 across the teams with live odds — a naive proxy for "
            "'who is actually good right now', NOT a trained championship-probability model. "
            "value = market_probability - proxy_probability; a large positive value means the "
            "market is pricing this team higher than its real season win rate alone would "
            "suggest, a large negative value the opposite. This is a starting point for a "
            "real-data comparison, not a betting recommendation."
        ),
        "teams": rows,
    }


# ─── Main Guard ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("impact_api:app", host="0.0.0.0", port=8002, reload=True)
