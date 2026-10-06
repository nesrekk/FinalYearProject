"""
test_round85_fixes.py
======================
Round 8.5 step B (docs/qa/ROUND8_ISSUES.md): the quick fixes left open by round 8, pinned.

  - R8-059 / R8-071: chart marks in brand orange use --chart-brand (>= 3:1 on every Paper and Ink
    background); the profile's shot-zone map and shot-mix bars use theme tokens.
  - R8-012: Referee Tendencies' By Crew view pages through the crews (100 at a time, opening on 2+ games
    together); the route's numbers are unchanged, paging only splits them.
  - R8-032: Pair Synergy is retired (the owner's call, 2026-10-06): no route, no model loading, and Player
    Comparison points to Pair Chemistry.

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_round85_fixes.py
"""

import glob
import os
import re
import sys

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_API_DIR)
_FRONTEND = os.path.join(_REPO, "frontend", "src")
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


def _lum(hex_):
    h = hex_.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _blocks():
    """Paper, Ink and the 'System' dark block of tokens.css as {name: raw value}."""
    css = _read("styles/tokens.css")
    pick = lambda block: dict(re.findall(r"--([\w-]+):\s*([^;]+);", block))  # noqa: E731
    paper = pick(css[css.index(":root {"):css.index("}", css.index(":root {"))])
    ink_start = css.index(":root[data-theme='dark'] {")
    ink = {**paper, **pick(css[ink_start:css.index("}", ink_start)])}
    sys_start = css.index("@media (prefers-color-scheme: dark)")
    system = {**paper, **pick(css[sys_start:css.index("\n}\n", sys_start)])}
    return {"paper": paper, "ink": ink, "system": system}


def _resolve(tokens, value):
    m = re.fullmatch(r"var\(--([\w-]+)\)", value.strip())
    return _resolve(tokens, tokens[m.group(1)]) if m else value.strip()


# ─── R8-059 / R8-071: chart marks ─────────────────────────────────────────────

def test_chart_brand_reads_on_every_background():
    """--brand (#ff5b14) is 2.4-2.9:1 on Paper; --chart-brand must be >= 3:1 in every theme block."""
    for theme, t in _blocks().items():
        colour = _resolve(t, t["chart-brand"])
        for bg in ("bg", "surface", "surface-2"):
            assert _contrast(colour, _resolve(t, t[bg])) >= 3, (theme, bg, colour)
    assert _contrast("#ff5b14", "#e8e1d3") < 3  # why the token exists


def test_no_chart_mark_draws_in_plain_brand():
    """Every SVG fill/stroke in brand orange goes through --chart-brand (kit.css's decorative tool-preview
    icon excepted), and so do the legend keys that stand for those marks."""
    left = []
    for path in glob.glob(os.path.join(_FRONTEND, "styles", "*.css")):
        if path.endswith("kit.css"):
            continue
        for i, line in enumerate(open(path, encoding="utf-8"), 1):
            if re.search(r"(fill|stroke)\s*:\s*var\(--brand\)", line) or ("legend" in line and "var(--brand)" in line):
                left.append(f"{os.path.basename(path)}:{i}")
    for path in glob.glob(os.path.join(_FRONTEND, "components", "**", "*.jsx"), recursive=True):
        text = open(path, encoding="utf-8").read()
        if re.search(r"(fill|stroke)=\"var\(--brand\)\"", text):
            left.append(os.path.relpath(path, _FRONTEND))
    assert left == []


def test_shot_zone_map_and_shot_mix_use_tokens():
    """R8-071: the profile's zone map washed zones in fixed rgba at 55% (1.3-1.5:1 against the court) and the
    shot-mix bars used three palette steps at 2.0-2.7:1 on Paper."""
    zone = _read("components/common/ZoneCourtMap.jsx")
    assert "rgba(" not in zone and "--series-8" in zone and "--series-1" in zone
    css = _read("styles/theme.css")
    root = css[css.index(".sm-root {"):css.index("}", css.index(".sm-root {"))]
    zones = dict(re.findall(r"--(zone-\d):\s*([^;]+);", root))
    paper = _blocks()["paper"]
    for k, v in zones.items():
        colour = _resolve(paper, v)
        for bg in ("bg", "surface", "surface-2"):
            assert _contrast(colour, paper[bg]) >= 3, (k, bg)
    # full-strength zone tints against the court (--surface-2), both themes
    for theme, t in _blocks().items():
        for tok in ("series-8", "series-1"):
            assert _contrast(t[tok], t["surface-2"]) >= 3, (theme, tok)


# ─── R8-012: referee crews, paged ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from impact_api import app
    return TestClient(app)


@needs_db
def test_crew_pages_split_the_full_answer(client):
    for min_games in (1, 2):
        for sort in ("n_games", "fouls_diff_pct", "name"):
            full = client.get("/referees/crew-tendencies", params={"min_games": min_games, "sort": sort}).json()
            assert full["limit"] is None and full["total_matching"] == len(full["crews"])
            paged, offset = [], 0
            while offset < full["total_matching"]:
                page = client.get("/referees/crew-tendencies",
                                  params={"min_games": min_games, "sort": sort, "limit": 100, "offset": offset}).json()
                assert page["total_matching"] == full["total_matching"] and len(page["crews"]) <= 100
                paged += page["crews"]
                offset += 100
            assert paged == full["crews"], (min_games, sort)
            assert len({c["crew_key"] for c in paged}) == len(paged)


@needs_db
def test_crew_page_is_small(client):
    """R8-012: the whole list was 2.8 MB; one page of 100 must stay well under 300 kB."""
    r = client.get("/referees/crew-tendencies", params={"min_games": 1, "limit": 100})
    assert len(r.content) < 300_000
    assert r.json()["total_matching"] > 1000  # 5,373 on 2026-10-06; most crews worked one game together


def test_crew_view_opens_on_repeat_crews_and_pages():
    src = _read("components/RefereeTendenciesSection.jsx")
    assert "useState(2)" in src and "const CREW_PAGE = 100" in src
    assert "fetchRefereeCrewTendencies(crewMinGames, sort, CREW_PAGE, crewOffset)" in src


# ─── R8-032: Pair Synergy retired ─────────────────────────────────────────────

@needs_db
def test_pair_synergy_route_is_gone(client):
    assert client.get("/players/pair-synergy", params={"player_a": "Nikola Jokic", "player_b": "Jamal Murray"}).status_code == 404
    import impact_core
    assert not any(name.startswith("PAIR_SYNERGY") for name in dir(impact_core))
    assert not hasattr(impact_core, "_player_synergy_features")
    assert not os.path.exists(os.path.join(_API_DIR, "routers", "pair_synergy.py"))


def test_comparison_points_to_pair_chemistry():
    src = _read("components/pages/PlayerComparison.jsx")
    assert "fetchPairSynergy" not in src and "pair-synergy" not in _read("services/api.js")
    assert "pushPage('analytics', 'pairs'" in src
    meth = _read("components/pages/methodologyContent.js")
    issues = meth[meth.index("export const OPEN_ISSUES"):]
    assert "Pair Synergy" not in issues
    assert "retired on 2026-10-06" in meth


def test_retired_model_keeps_its_manifest_table():
    """pair_synergy_validation stays (dropping it would change the paper manifest's digest), so its producer
    stays in the manifest and the rebuild plan."""
    sys.path.insert(0, os.path.join(_REPO, "scripts"))
    import paper_manifest as PM
    assert PM.PRODUCERS["train_pair_synergy.py"][1] == ["pair_synergy_validation"]
    manifest = os.path.join(_REPO, "paper", "manifest.tsv")  # paper/ is untracked: absent in a fresh clone
    if os.path.exists(manifest):
        assert "pair_synergy_validation" in open(manifest, encoding="utf-8").read()
