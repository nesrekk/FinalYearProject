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
import html
import json
import math
import os
import pickle
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote
from urllib.request import Request, urlopen
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg2 import pool

import shots_lib

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

DB_POOL = pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    host="localhost",
    port="5432",
    user="postgres",
    password="meinkampf:)",
    dbname="nba_analytics",
)

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

_CACHE = {
    "team_badges": {"ts": 0, "data": {}},
    "standings_bdl": {},  # key: season -> {"ts": ..., "data": ...}
    "player_images": {},  # key: normalized_name -> {"ts": ..., "url": ...}
}
_CACHE_TTL_SECONDS = 6 * 60 * 60


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
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, headers=None, timeout: int = 20):
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as resp:
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
            abbr = str(val(row, "TeamAbbreviation", "")).upper()
            team_name = f"{val(row, 'TeamCity', '')} {val(row, 'TeamName', '')}".strip()
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


@app.get("/games/by-date")
def get_games_by_date(date: Optional[str] = None):
    """
    Games for a given date (YYYY-MM-DD). Defaults to today.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    games = fetch_nba_games_by_date(date)
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


# ─── Main Guard ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("impact_api:app", host="0.0.0.0", port=8002, reload=True)
