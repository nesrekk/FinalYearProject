"""
test_round8_pages.py
=====================
Fixes from round 8's page sweeps (docs/qa/ROUND8_ISSUES.md), pinned so they stay fixed. Step 2a (Players
pages) wrote the first ones; 2b and 2c add theirs here. API checks go through FastAPI's TestClient against
the real local database (skipped if it isn't reachable); frontend checks read the source files (the
browser sweep itself is frontend/qa/page_scan.js, run through the browser tool).

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_round8_pages.py
"""

import os
import re
import sys

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(os.path.dirname(_API_DIR), "frontend", "src")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


def _read(rel):
    with open(os.path.join(_FRONTEND, rel), encoding="utf-8") as f:
        return f.read()


# ─── Colour contrast of theme tokens (WCAG 2.1) ──────────────────────────────

def _lum(hex_):
    h = hex_.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _theme_tokens():
    """{'paper': {...}, 'ink': {...}} from styles/tokens.css (hex values only)."""
    css = _read("styles/tokens.css")
    paper_block = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    ink_start = css.index(":root[data-theme='dark'] {")
    ink_block = css[ink_start:css.index("}", ink_start)]
    pick = lambda block: dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))  # noqa: E731
    paper = pick(paper_block)
    ink = {**paper, **pick(ink_block)}
    return {"paper": paper, "ink": ink}


def test_muted_text_reads_on_the_rails():
    """R8-033: --text-3 on --surface-2 (the page rails: Player Stats, Stat Leaders, Hall of Fame) was 4.497:1."""
    for theme, t in _theme_tokens().items():
        for bg in ("surface", "surface-2", "bg"):
            assert _contrast(t["text-3"], t[bg]) >= 4.5, (theme, bg)


def test_comparison_colours_read_as_text():
    """R8-035: Player Comparison's two player colours were #f87171 / #38bdf8 in both themes (1.6-2.6:1 on Paper)."""
    for theme, t in _theme_tokens().items():
        for key in ("compare-a", "compare-b"):
            for bg in ("surface", "surface-2"):
                assert _contrast(t[key], t[bg]) >= 4.5, (theme, key, bg)
    src = _read("components/pages/PlayerComparison.jsx")
    assert "const COLOR_A = 'var(--compare-a)'" in src and "const COLOR_B = 'var(--compare-b)'" in src


# ─── Frontend source checks ─────────────────────────────────────────────────

def test_profile_blocks_have_distinct_keys():
    """R8-034: six profile blocks shared key={d.player.player_id} → React's duplicate-key error on every
    profile with play-by-play data."""
    src = _read("components/pages/PlayerProfile.jsx")
    assert "key={d.player.player_id}" not in src
    keys = re.findall(r"<\w+Block key=\{`(\w+)-\$\{d\.player\.player_id\}`\}", src)
    assert len(keys) >= 6 and len(keys) == len(set(keys))


def test_phone_search_button_shows_its_icon():
    """R8-036: below 480 px the label-hiding rule also hid the icon (an empty box in the top bar)."""
    css = _read("styles/shell.css")
    assert ".nav-search-pill span:not(.icon)" in css
    assert not re.search(r"\.nav-search-pill span\s*\{", css)
    assert 'className="nav-search-pill" onClick={() => setPaletteOpen(true)} aria-label="Search"' in _read("components/layout/TopNav.jsx")


def test_season_pickers_default_to_the_latest_season():
    """R8-037: Player Stats and Player Comparison opened on 2024-25 with a raw-year number box (Rookie
    Class Tracker had the number box too)."""
    for rel in ("components/pages/PlayerStats.jsx", "components/pages/PlayerComparison.jsx",
                "components/pages/RookieClassTracker.jsx"):
        src = _read(rel)
        assert "const LATEST_SEASON = 2026;" in src, rel
        assert "?? 2025" not in src and "useState(2025)" not in src, rel
        assert "seasonLabel" in src or "slice(-2)" in src, rel  # options read 2025-26, not 2026


def test_stat_leaders_season_label():
    """R8-015 (Stat Leaders part): the subtitle showed the raw end year ("Top 10 · 2027")."""
    assert "`· ${season - 1}-${String(season).slice(-2)}`" in _read("components/pages/StatLeaders.jsx")


# ─── API ────────────────────────────────────────────────────────────────────

@needs_db
def test_player_stats_and_comparison_carry_a_source():
    """R8-014 (Players pages): /players/table and /players/compare-profile had no _source."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/players/table/2026", params={"min_minutes": 30}).json()
    assert d["_source"]["tables"] == ["player_season_stats"] and d["count"] > 0
    d = client.get("/players/compare-profile/Nikola Jokić", params={"season": 2026}).json()
    assert "player_season_stats" in d["_source"]["tables"] and d["season"] == 2026
    # Not in the qualified pool: the message names the season as the app writes it.
    r = client.get("/players/compare-profile/Cooper Flagg", params={"season": 2025})
    assert r.status_code == 404 and "2024-25" in r.json()["detail"]


@needs_db
def test_empty_results_say_why():
    """R8-038: floors nobody meets gave "Not enough seasons with these stats." (Breakout Detector) and
    "Only 0 player-seasons pass" (Regression Explorer)."""
    from impact_api import app
    client = TestClient(app)
    r = client.get("/explore/breakouts", params={"season": 2026, "min_gp": 82, "min_mpg": 44})
    assert r.status_code == 404
    assert "lower the floors" in r.json()["detail"] and "82+ games" in r.json()["detail"]
    # A season before the stats start still gets the stat message.
    r = client.get("/explore/breakouts", params={"season": 1950})
    assert r.status_code == 404 and "Pick a season" in r.json()["detail"]
    r = client.get("/explore/regression", params={"x": "usg_pct", "y": "ts_pct", "season_from": 2026,
                                                   "season_to": 2026, "min_gp": 82, "min_mpg": 45})
    assert r.status_code == 404 and r.json()["detail"].startswith("No player-seasons pass")
