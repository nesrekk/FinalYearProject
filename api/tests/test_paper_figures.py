"""
test_paper_figures.py
======================
Guards scripts/paper_figures.py, which draws the conference paper's figures
(paper/figures/*.pdf) from the database:

  * the macro reader and the TeX-to-figure-text conversion work without a
    database;
  * a full run succeeds, i.e. every plotted number the paper also prints
    rounds to its macro and every sentence the captions state holds (the
    script's checks), and two runs give byte-identical PDFs;
  * every PDF is vector with its fonts embedded as TrueType (no Type 3, which
    IEEE's PDF checks reject) at the IEEE column or text width;
  * the paper includes every figure and references every figure label, and
    the check notices when it doesn't (skipped where paper/, untracked on
    purpose, isn't present).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_figures.py
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

import paper_figures as F  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

PAPER = os.path.join(_ROOT, "paper", "nba_hub_paper.tex")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


def test_macro_reader_and_plain_text():
    text = ("\\newcommand{\\pnA}{7{,}220}% a\n\\newcommand{\\pnB}{\\ensuremath{-}0.30}% b\n"
            "\\newcommand{\\pnC}{2020--21}% c\n")
    m = F.parse_macros(text)
    assert m == {"pnA": "7{,}220", "pnB": "\\ensuremath{-}0.30", "pnC": "2020--21"}
    assert F.plain(m["pnA"]) == "7,220" and F.plain(m["pnB"]) == "−0.30" and F.plain(m["pnC"]) == "2020–21"
    with pytest.raises(ValueError):
        F.plain("\\emph{x}")
    C = F.Checks({"pnX": "0.43"})
    C.expect("X", 0.425, 2)              # half-up on the stored value, as paper_numbers prints it
    C.expect("X", 0.44, 2)
    C.expect("Y", 1.0, 2, optional=True)
    assert C.n == 2 and len(C.bad) == 1 and "pnX" in C.bad[0]


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory):
    out = []
    for i in range(2):
        d = tmp_path_factory.mktemp(f"figs{i}")
        conn = psycopg2.connect(**DB_CONFIG)
        conn.set_session(readonly=True, autocommit=True)
        try:
            paths, n_checks, _ = F.build(conn, str(d))
        finally:
            conn.close()
        out.append((str(d), paths, n_checks))
    return out


@needs_db
def test_checks_pass_and_runs_are_identical(two_runs):
    (d1, p1, n1), (d2, p2, n2) = two_runs
    assert n1 == n2 and n1 >= 150, "the figures check their plotted numbers against the paper's macros"
    assert sorted(os.path.basename(p) for p in p1) == sorted(f"fig_{k}.pdf" for k in F.FIGURES)
    for p in p1:
        with open(p, "rb") as a, open(os.path.join(d2, os.path.basename(p)), "rb") as b:
            assert a.read() == b.read(), f"{os.path.basename(p)} differs between two runs"


@needs_db
def test_pdfs_are_vector_with_embedded_truetype(two_runs):
    widths = {round(72 * F.COL_W, 2), round(72 * F.FULL_W, 2)}
    for p in two_runs[0][1]:
        with open(p, "rb") as f:
            b = f.read()
        assert b"/Type3" not in b and b"/FontFile2" in b, f"{os.path.basename(p)}: fonts not embedded as TrueType"
        assert b"/CreationDate" not in b, f"{os.path.basename(p)}: carries a creation date (breaks reproducibility)"
        box = [float(x) for x in re.search(rb"/MediaBox \[([^\]]*)\]", b).group(1).split()]
        assert round(box[2], 2) in widths, f"{os.path.basename(p)}: width {box[2]} pt is not the IEEE column or text width"
        assert b"/Subtype /Image" not in b, f"{os.path.basename(p)}: holds a raster image"


@pytest.mark.skipif(not os.path.exists(PAPER), reason="paper/ is untracked and not present")
def test_paper_includes_and_references_every_figure(tmp_path):
    out = F.DEFAULT_OUT
    if not all(os.path.exists(os.path.join(out, f"fig_{k}.pdf")) for k in F.FIGURES):
        pytest.skip("paper/figures not generated here")
    assert F.check_paper(PAPER, out) == []
    with open(PAPER) as f:
        tex = f.read()
    broken = tmp_path / "paper.tex"
    broken.write_text(tex.replace("Fig.~\\ref{fig:streaks}", "Fig.", 1).replace("figures/fig_forest.pdf", "figures/fig_nope.pdf"))
    problems = F.check_paper(str(broken), out)
    assert any("fig:streaks" in p for p in problems) and any("fig_nope" in p for p in problems) \
        and any("fig_forest: written but not included" in p for p in problems)
