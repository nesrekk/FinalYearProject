"""
impact_core.py
================
Shared setup for the impact_api service, split out of what used to be
one 5,500+ line impact_api.py (A6): the Postgres connection pool, loaded
model artifacts, every fetch_*/find_player/get_db-style helper, and every
module-level constant that more than one router needs. Nothing in this
file defines a route — see routers/ for those, grouped one file per
feature, each importing exactly the names it needs from here.
"""

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
from source_badge import make_source
load_dotenv()
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
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
    "news": {},  # key: (date_str, limit, team) -> {"ts": ..., "data": [item_dict, ...]}
}
_CACHE_TTL_SECONDS = 6 * 60 * 60
_NEWS_CACHE_TTL_SECONDS = 5 * 60  # news moves fast — much shorter TTL than the general cache
TEAM_NAME_TO_ABBR = {info["name"]: abbr for abbr, info in TEAM_META.items()}
@contextmanager
def get_db():
    conn = DB_POOL.getconn()
    try:
        yield conn
    finally:
        DB_POOL.putconn(conn)
shots_lib.ensure_schema()
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
_SCRIPTS_DIR = os.path.join(_SCRIPT_DIR, "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from wpa_lib import CLUTCH_MARGIN as WPA_CLUTCH_MARGIN
from wpa_lib import CLUTCH_SECONDS as WPA_CLUTCH_SECONDS
from wpa_lib import PBP_DEDUP_WHERE
from wpa_lib import load_model as _load_wpa_model
from wpa_lib import seconds_elapsed as wpa_seconds_elapsed
from wpa_lib import win_prob as wpa_win_prob
try:
    WPA_MODEL, WPA_SCALER = _load_wpa_model()
except Exception:
    WPA_MODEL, WPA_SCALER = None, None
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
LENGTH_STUDY_MIN_TOTAL_MINUTES = 500
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
def _wpa_model_required():
    if WPA_MODEL is None or WPA_SCALER is None:
        raise HTTPException(
            status_code=503,
            detail="WPA model isn't available — run scripts/train_wpa_model.py first.",
        )
def _is_missed_field_goal(action_type, description) -> bool:
    """True for a real missed FIELD GOAL attempt, from either real pbp
    source. nba_api's own action_type vocabulary marks these plainly as
    "Missed Shot"; ESPN's (scripts/fetch_pbp_espn.py) real per-shot-type
    vocabulary (e.g. "Driving Layup Shot", "Step Back Jump Shot") doesn't
    have an equivalent single category, so real ESPN rows are matched by
    the real word "misses" in their real play description instead — verified
    live to appear in 114,058 of 114,059 real ESPN missed-shot rows for a
    real season, with zero false positives among real made shots. Missed
    FREE THROWS are excluded either way (nba_api: a separate "Free Throw"
    action_type; ESPN: description always says "Free Throw" too) since the
    original nba_api-only feature was already scoped to field goals only —
    a missed FT's counterfactual is a fixed, uninteresting +1."""
    if action_type == "Missed Shot":
        return True
    if description and "misses" in description.lower() and "free throw" not in description.lower():
        return True
    return False
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
        WHERE """ + PBP_DEDUP_WHERE + """
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
_NEWS_CATEGORY_KEYWORDS = [
    # Order matters — first match wins, most specific real-language signals first.
    ("Injuries", ("injury", "injured", "out for", "questionable", "doubtful", "surgery",
                   "tears", "torn", "sprain", "fracture", "ruled out", "day-to-day")),
    ("Trade Rumors", ("trade", "traded", "trading", "deal", "acquire", "acquired", "waived",
                        "waive", "buyout", "sign-and-trade")),
    ("Game Recap", (" beat ", " beats ", " win over", " wins over", " rout ", " routs ",
                      "final score", "walk-off", "buzzer-beater", "overtime thriller")),
    ("Player Watch", ("mvp", "all-star", "career-high", "triple-double", "milestone", "record")),
]


def _classify_news_category(headline: str, summary: str) -> str:
    """Real keyword match against the real headline/summary text — a disclosed
    heuristic, not an editorial category from the source (RSS feeds carry no
    category field at all)."""
    text = f" {headline.lower()} {summary.lower()} "
    for category, keywords in _NEWS_CATEGORY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return category
    return "News"


def fetch_current_news(date_str: Optional[str] = None, limit: int = 20, team: Optional[str] = None):
    """
    Pull current NBA headlines from RapidAPI (if configured),
    then fallback to public RSS feeds. `team` (a real full team name, e.g.
    "Los Angeles Lakers") adds one extra real Google News RSS feed scoped to
    that team via its own query parameter, alongside the general feeds.
    Cached for _NEWS_CACHE_TTL_SECONDS since news moves fast but this
    endpoint can be hit often.
    """
    cache_key = (date_str, limit, team)
    cached = _CACHE["news"].get(cache_key)
    if cached and time.time() - cached["ts"] < _NEWS_CACHE_TTL_SECONDS:
        return cached["data"]

    result = _fetch_current_news_uncached(date_str, limit, team)
    _CACHE["news"][cache_key] = {"ts": time.time(), "data": result}
    return result


def _fetch_current_news_uncached(date_str: Optional[str], limit: int, team: Optional[str]):
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
                        "category": _classify_news_category(title, summary),
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
        ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
        ("Sports Illustrated", "https://www.si.com/rss/si_topic/nba"),
    ]
    if team:
        # A real, additional Google News RSS feed scoped to this one team via
        # its own query — same feed mechanism as the general "NBA" one above,
        # just a more specific real query string, not a different data source.
        feeds.append((
            "Google News",
            f"https://news.google.com/rss/search?q={quote(team)}+NBA&hl=en-US&gl=US&ceid=US:en",
        ))

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
                        "category": _classify_news_category(title, description),
                    }
                )
        except Exception:
            continue

    # If strict date filter produced nothing, return most recent feed items instead.
    if not items and date_str:
        return _fetch_current_news_uncached(date_str=None, limit=limit, team=team)

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
HUSTLE_STAT_MAP = {
    "deflections": "Deflections",
    "contested_shots": "Contested Shots",
    "screen_assists": "Screen Assists",
    "loose_balls_recovered": "Loose Balls Recovered",
    "charges_drawn": "Charges Drawn",
    "box_outs": "Box Outs",
}
MATCHUP_RELIABLE_POSS = 20.0
MATCHUP_MIN_POSS = 5.0  # already enforced at fetch time; re-applied here as a floor
REFEREE_MIN_GAMES_DEFAULT = 10
REFEREE_SMALL_N_THRESHOLD = 25  # must match build_referee_tendencies.py's MIN_GAMES_SMALL_N
RADAR_STATS = ["pts", "reb", "ast", "stl", "blk", "ts_pct", "usg_pct"]
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
HIGHER_LOWER_MIN_CAREER_GAMES = 150
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
TREND_PLAYER_STATS = ["pts", "reb", "ast", "stl", "blk", "ts_pct", "usg_pct", "net_rating", "min"]
TREND_TEAM_STATS = [
    "net_rating", "off_rating", "def_rating", "ts_pct", "win_pct",
    "efg_pct", "oreb_pct", "tov_pct", "ftr",  # Four Factors (Dean Oliver)
]
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
ESTIMATED_POSITION_LABELS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}
def _position_label(bpm_position):
    if bpm_position is None:
        return None
    return ESTIMATED_POSITION_LABELS[max(1, min(5, round(bpm_position)))]
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
