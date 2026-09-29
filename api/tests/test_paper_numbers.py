"""
test_paper_numbers.py
======================
Guards scripts/paper_numbers.py, which writes every number the conference
paper states (paper/numbers.tex) from the database:

  * the formatting rules (half-up on the stored value, LaTeX-safe commas and
    minus signs, seasons as 2025--26) hold without a database;
  * two runs against the same database give byte-identical output, and none
    of the paper's data-dependent sentences (the script's claims) is broken;
  * every \\pn macro the paper uses is defined (skipped where paper/, which is
    untracked on purpose, isn't present).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_numbers.py
"""

import os
import re
import sys

import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import paper_numbers as P  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


def test_formatting_rules():
    assert P.dec(0.425, 2) == "0.43"                      # half-up on the stored value, not the binary float's 0.42
    assert P.dec(-0.0353, 2) == "\\ensuremath{-}0.04"
    assert P.dec(-0.001, 2) == "0.00"                     # no negative zero
    assert P.dec(3000.0, 0) == "3{,}000"
    assert P.dec(0.99996, 4, P.ROUND_FLOOR) == "0.9999"
    assert P.dec(0.0054, 3, P.ROUND_CEILING) == "0.006"
    assert P.integer(7232) == "7{,}232"
    assert P.pct(0.6583, 1) == "65.8"
    assert P.ceil_pct(0.000488, 2) == "0.05"
    assert P.millions(3_401_830, 2) == "3.40"
    assert P.season(2026) == "2025--26" and P.season("2025-26") == "2025--26" and P.season(2010) == "2009--10"
    assert P.word(5) == "five" and P.word(14) == "14"


def test_macro_names_are_checked():
    n = P.Numbers()
    n.add("GoodName", "1", "x")
    with pytest.raises(ValueError):
        n.add("GoodName", "2", "x")      # defined twice
    with pytest.raises(ValueError):
        n.add("Top1", "3", "x")          # digits can't be in a TeX command name


def test_comments_are_stripped_but_escaped_percent_is_text():
    tex = "a \\pnOne\\% then \\pnTwo{} % \\pnThree\n% \\pnFour\n"
    assert P.used_macros(tex) == {"pnOne", "pnTwo"}


@pytest.fixture(scope="module")
def generated():
    conn = P.connect()
    try:
        first, second = P.build(conn), P.build(conn)
    finally:
        conn.close()
    return first, second


@needs_db
def test_generator_is_deterministic(generated):
    first, second = generated
    assert first == second
    assert "\\newcommand" in first


@needs_db
def test_every_macro_line_is_well_formed(generated):
    text = generated[0]
    lines = [line for line in text.splitlines() if line.startswith("\\newcommand")]
    assert len(lines) == len(P.defined_macros(text)) > 100
    for line in lines:
        assert re.match(r"^\\newcommand\{\\pn[A-Z][A-Za-z]*\}\{[^%]*\}% \S", line), line


@needs_db
def test_every_macro_the_paper_uses_is_defined(generated):
    if not os.path.exists(P.DEFAULT_PAPER):
        pytest.skip("paper/ is untracked and not present in this checkout")
    undefined, _unused = P.check_paper(generated[0], P.DEFAULT_PAPER)
    assert undefined == []
