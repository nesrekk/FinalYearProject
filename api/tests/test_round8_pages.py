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


# ─── Step 2b: Teams, Games, Live Scores (and the Today pages) ────────────────

def _mix(a, b, t):
    ha, hb = a.lstrip("#"), b.lstrip("#")
    ca = [int(ha[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(hb[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02x}" for x, y in zip(ca, cb))


def test_simulator_seed_cells_read_in_both_themes():
    """R8-044: the Simulator's finish-odds cells put #0d0d0d digits on a brand tint up to 100% of the
    cell, which in Ink is a darkened orange (1.1-3.4:1); light digits fail at 100% (2.7:1). Now the digits
    are --text and Ink caps the tint at 65%. Checked at every tint from 0 to the cap, on both surfaces."""
    css = _read("styles/simulator.css")
    assert ".ss-seed--hot { color: var(--text);" in css
    assert ":root[data-theme='dark'] .ss-seed { --seed-max: 65%; }" in css
    assert "--seed-max: 65%" in css.split("prefers-color-scheme: dark")[1]
    assert "color-mix(in srgb, var(--brand) ${" not in _read("components/pages/SeasonSimulator.jsx")
    caps = {"paper": 1.0, "ink": 0.65}
    for theme, t in _theme_tokens().items():
        for bg in ("surface", "bg"):
            for step in range(0, 101, 5):
                tint = step / 100 * caps[theme]
                cell = _mix(t["brand"], t[bg], tint)
                # Below 50% the digits are --text-2, from 50% on --text (.ss-seed--hot).
                fg = t["text"] if step >= 50 else t["text-2"]
                assert _contrast(fg, cell) >= 4.5, (theme, bg, step)


def test_live_scores_names_and_logos_come_from_the_app():
    """R8-045: the scoreboard's nickname field is empty and its logos are thesportsdb's or null, so each
    card showed a bare logo (or a white-on-orange abbreviation at 2.6:1) and no team name."""
    src = _read("components/pages/LiveScores.jsx")
    assert "TEAM_FULL_NAME" in src and "<TeamLogo abbreviation={team.abbr}" in src
    assert "<TeamLink abbr={team.abbr}>" in src
    assert "team.logo" not in src and "game.away.logo" not in src and "TEAM_COLORS" not in src
    assert 'role="button"' in src and "onKeyDown" in src  # the card opens the box score from the keyboard too
    assert "'001': 'Preseason'" in src


def test_standings_say_when_no_game_has_been_played():
    """R8-004 (Standings and Dashboard part): before a season's first game every team is 0-0, but the page
    crowned Atlanta "#1 Seed" with a "W 0" streak and no season; the Dashboard tile took the West's first
    row only."""
    src = _read("components/pages/StandingsSection.jsx")
    assert "no games played yet" in src and "const leader = played ?" in src
    dash = _read("components/pages/DashboardHome.jsx")
    assert "west.length > 0 ? west[0]" not in dash
    assert "...(meta?.standings?.eastern || []), ...(meta?.standings?.western || [])" in dash


def test_trade_pages_use_season_pickers():
    """R8-043 (2b part): Trade Analyzer opened on 2023-24 (`?? 2024`) with a raw-year number box; Trade
    Impact had the number box too (its 2024-25 default is deliberate: the last season with all three
    blocks)."""
    ta = _read("components/pages/TradeAnalyzer.jsx")
    assert "?? 2026);" in ta and "?? 2024)" not in ta
    for rel in ("components/pages/TradeAnalyzer.jsx", "components/pages/TradeImpact.jsx"):
        src = _read(rel)
        assert 'type="number"' not in src, rel
        assert "TRADE_SEASONS.map" in src and "slice(-2)" in src, rel


def test_team_pages_link_player_names():
    """R8-046: Trade Analyzer, Trade Impact and Team Comparison drew headshot + name by hand (no profile
    link, no watchlist star)."""
    for rel in ("components/pages/TradeAnalyzer.jsx", "components/pages/TradeImpact.jsx",
                "components/pages/TeamComparison.jsx"):
        src = _read(rel)
        assert "<PlayerName playerId=" in src and "PlayerHeadshot" not in src, rel


def test_team_page_lineup_link_keeps_the_season():
    """R8-047: the team page's "Open Lineup Chemistry" passed no season (a 2004-05 page opened 2025-26), and
    Coaching Decisions had no way to the team page of the team it shows."""
    assert "onNavigate('analytics', 'lineups', { season })" in _read("components/pages/TeamProfile.jsx")
    assert "Open the {d.team} team page" in _read("components/pages/CoachingDecisions.jsx")


def test_puzzles_name_their_season():
    """R8-016: Trivia asked who leads the league "this season" from last season's numbers, and the puzzles'
    headers showed a raw end year ("Season 2026")."""
    for rel in ("components/pages/Trivia.jsx", "components/pages/GuessThePlayer.jsx",
                "components/pages/BlurredPlayer.jsx"):
        src = _read(rel)
        assert "`Season ${daily.season}" not in src and "this season's" not in src, rel


@needs_db
def test_trivia_questions_name_the_season():
    """R8-016: every question names the pool's season instead of "this season"."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/games/trivia/daily").json()
    label = f"{d['season'] - 1}-{str(d['season'])[-2:]}"
    assert len(d["questions"]) == 5
    for q in d["questions"]:
        assert "this season" not in q["question"] and label in q["question"], q["question"]


@needs_db
def test_trade_analyzer_carries_a_source():
    """R8-014 (Teams pages): /trade/teams, /trade/roster and /trade/simulate had no _source."""
    from impact_api import app
    client = TestClient(app)
    assert client.get("/trade/teams/2026").json()["_source"]["tables"] == ["player_season_stats"]
    assert "player_season_stats" in client.get("/trade/roster/LAL/2026").json()["_source"]["tables"]
    d = client.get("/trade/simulate", params={"season": 2026, "team_a": "LAL", "player_a_id": 1629029,
                                              "team_b": "DAL", "player_b_id": 1642843}).json()
    assert "season_similarity" in d["_source"]["tables"]
    assert "<SourceBadge source={result._source} />" in _read("components/pages/TradeAnalyzer.jsx")


def test_games_hub_keeps_the_open_game_in_the_link():
    """R8-048: the open game lived only in component state, so a copied or saved link always opened the
    hub, never Trivia or Guess the Game."""
    src = _read("components/pages/GamesHub.jsx")
    assert "parseParam.oneOf(params, 'g', tabs.map((t) => t.id))" in src and "useUrlSync({ g: activeTab })" in src
