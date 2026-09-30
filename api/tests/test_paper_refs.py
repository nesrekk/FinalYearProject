"""
test_paper_refs.py
===================
Guards scripts/paper_refs_check.py, which checks the conference paper's
bibliography (paper/refs.bib, round 5 step 10). No database, no network:

  * the .bib reader handles braces, quotes, bare values and TeX accents, and
    refuses an '@' on a comment line (BibTeX would read an entry there);
  * cite keys are read after TeX comments are stripped, where \\% is a percent
    sign and not a comment;
  * the offline check catches a cited key with no entry, an entry nobody
    cites, a missing "% verified" line and a leftover thebibliography;
  * the real paper and refs.bib pass (skipped where paper/, untracked on
    purpose, isn't present). The Crossref comparison (--online) is run by
    hand after editing an entry, not here.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_refs.py
"""

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import paper_refs_check as R  # noqa: E402

BIB = """% verified: https://api.crossref.org/works/10.1/x 2026-09-30 (Crossref)
@article{a2020,
  author  = {Jos{\\'e} P{\\'e}rez and {Daum{\\'e} III}, Hal},
  title   = {A {NBA} study: {x} = {y}},
  journal = "J. Tests",
  volume  = 3,
  pages   = {1--9},
  year    = {2020},
  doi     = {10.1/x}
}

% verified: https://example.org/page 2026-09-30
@misc{b2019,
  title        = {Page},
  howpublished = {example.org},
  year         = {2019},
  url          = {https://example.org/page?a=1&b=2}
}
"""
TEX = ("\\documentclass{IEEEtran}\\begin{document}Text~\\cite{a2020} and 5\\% more~\\cite{b2019}. % \\cite{zz}\n"
       "\\bibliographystyle{IEEEtran}\n\\bibliography{refs}\n\\end{document}\n")


def test_reader():
    e = R.parse_bib(BIB)
    assert [x["key"] for x in e] == ["a2020", "b2019"]
    assert e[0]["fields"]["journal"] == "J. Tests" and e[0]["fields"]["volume"] == "3"
    assert e[0]["fields"]["title"] == "A {NBA} study: {x} = {y}"
    assert e[1]["fields"]["url"].endswith("a=1&b=2")
    assert e[0]["verified"].startswith("% verified:")
    assert R.bib_families(e[0]["fields"]["author"]) == ["perez", "daume"]
    with pytest.raises(ValueError):
        R.parse_bib("% verified: see me@example.org 2026-09-30\n")
    with pytest.raises(ValueError):
        R.parse_bib("@article{c, title = {open}\n")


def test_cite_keys_skip_comments_not_percent_signs():
    assert R.cited_keys(TEX) == ["a2020", "b2019"]
    assert R.cited_keys("x~\\cite{a, b}\\cite[p.~3]{c}") == ["a", "b", "c"]


def test_offline_check_catches_problems():
    e = R.parse_bib(BIB)
    assert R.offline_problems(TEX, e) == []
    bad = R.offline_problems(TEX.replace("\\cite{b2019}", "\\cite{c2000}"), e)
    assert any("c2000: cited" in p for p in bad) and any("b2019: in refs.bib but never cited" in p for p in bad)
    unverified = R.parse_bib(BIB.replace("% verified: https://example.org/page 2026-09-30", "% seen"))
    assert any("b2019: no '% verified" in p for p in R.offline_problems(TEX, unverified))
    old = TEX.replace("\\bibliography{refs}", "\\begin{thebibliography}{9}\\bibitem{a2020} x\\end{thebibliography}")
    assert any("thebibliography" in p for p in R.offline_problems(old, e))


@pytest.mark.skipif(not (os.path.isfile(R.TEX) and os.path.isfile(R.BIB)), reason="paper/ (untracked) not present")
def test_real_paper_passes():
    with open(R.TEX, encoding="utf-8") as fh:
        tex = fh.read()
    with open(R.BIB, encoding="utf-8") as fh:
        entries = R.parse_bib(fh.read())
    assert R.offline_problems(tex, entries) == []
    assert len(entries) >= 25
