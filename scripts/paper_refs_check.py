"""
paper_refs_check.py
====================
Checks the conference paper's bibliography, paper/refs.bib (round 5 step 10).
No database. Offline by default:

  * the paper cites through BibTeX (\\bibliography{refs}, no hand-written
    thebibliography block left behind);
  * every \\cite key in the paper has an entry in refs.bib and every entry is
    cited (IEEEtran would silently drop an uncited one, so it would only be
    unchecked weight);
  * every entry is directly preceded by a "% verified: <url> <YYYY-MM-DD> ..."
    line saying where it was checked and what the paper relies on it for
    (the rule: nothing is cited from memory);
  * each entry type has its required fields; no duplicate keys; braces
    balance; no '@' on a comment line (BibTeX would read it as an entry).

--online also compares every entry that has a DOI with its Crossref record:
title, year, volume, issue, pages (where Crossref has them) and the authors'
family names in order. A mismatch is printed and the exit code is 1. Run it
after adding or editing an entry.

    cd scripts && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 paper_refs_check.py
    cd scripts && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 paper_refs_check.py --online
"""

import argparse
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_DIR = os.path.join(ROOT, "paper")
TEX = os.path.join(PAPER_DIR, "nba_hub_paper.tex")
BIB = os.path.join(PAPER_DIR, "refs.bib")

REQUIRED = {
    "article": ("author", "title", "journal", "year"),
    "inproceedings": ("author", "title", "booktitle", "year"),
    "book": ("author", "title", "publisher", "year"),
    "misc": ("title", "howpublished", "year", "url"),
}
VERIFIED = re.compile(r"^%\s*verified(?:\s*\(read (\d{4}-\d{2}-\d{2})\))?:\s*(\S+).*?(\d{4}-\d{2}-\d{2})?")


def parse_bib(text: str) -> list[dict]:
    """Entries in file order: {type, key, fields, verified, line}. Raises ValueError on a malformed entry."""
    entries, i, n = [], 0, len(text)
    while True:
        at = text.find("@", i)
        if at < 0:
            return entries
        line_no = text.count("\n", 0, at) + 1
        line_start = text.rfind("\n", 0, at) + 1
        if text[line_start:at].lstrip().startswith("%"):
            raise ValueError(f"line {line_no}: '@' on a comment line (BibTeX would read an entry there)")
        m = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,").match(text, at)
        if not m:
            raise ValueError(f"line {line_no}: cannot read the entry header")
        depth, j = 1, m.end()
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        if depth:
            raise ValueError(f"line {line_no}: unbalanced braces in {m.group(2)}")
        body = text[m.end():j - 1]
        fields, k = {}, 0
        while True:
            fm = re.compile(r"\s*(\w+)\s*=\s*").match(body, k)
            if not fm:
                break
            name, k = fm.group(1).lower(), fm.end()
            if body[k] == "{":
                d, s = 1, k + 1
                k += 1
                while d:
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                value = body[s:k - 1]
            elif body[k] == '"':
                e = body.index('"', k + 1)
                value, k = body[k + 1:e], e + 1
            else:
                vm = re.compile(r"[^,\s]+").match(body, k)
                value, k = vm.group(0), vm.end()
            if name in fields:
                raise ValueError(f"{m.group(2)}: field {name} twice")
            fields[name] = " ".join(value.split())
            cm = re.compile(r"\s*,?").match(body, k)
            k = cm.end()
        if body[k:].strip():
            raise ValueError(f"{m.group(2)}: cannot read fields near {body[k:k + 40]!r}")
        prev = text[:line_start].rstrip("\n").rsplit("\n", 1)[-1]
        entries.append({"type": m.group(1).lower(), "key": m.group(2), "fields": fields,
                        "verified": prev if prev.lstrip().startswith("%") else "", "line": line_no})
        i = j


def strip_comments(tex: str) -> str:
    """TeX without its comments (an unescaped % to the end of the line; \\% is a percent sign)."""
    return "\n".join(re.sub(r"(?<!\\)%.*", "", l) for l in tex.splitlines())


def cited_keys(tex: str) -> list[str]:
    body = strip_comments(tex)
    keys = []
    for group in re.findall(r"\\cite\*?(?:\[[^\]]*\])?\{([^}]*)\}", body):
        keys += [k.strip() for k in group.split(",") if k.strip()]
    return keys


def offline_problems(tex: str, entries: list[dict]) -> list[str]:
    out = []
    body = strip_comments(tex)
    if not re.search(r"\\bibliography\{refs\}", body):
        out.append("the paper has no \\bibliography{refs}")
    if not re.search(r"\\bibliographystyle\{IEEEtran\}", body):
        out.append("the paper has no \\bibliographystyle{IEEEtran}")
    if "thebibliography" in body or "\\bibitem" in body:
        out.append("the paper still has a hand-written thebibliography / \\bibitem")
    keys = [e["key"] for e in entries]
    for k in sorted({k for k in keys if keys.count(k) > 1}):
        out.append(f"{k}: duplicate key")
    cited = cited_keys(tex)
    for k in sorted(set(cited) - set(keys)):
        out.append(f"{k}: cited in the paper, no entry in refs.bib")
    for k in sorted(set(keys) - set(cited)):
        out.append(f"{k}: in refs.bib but never cited")
    for e in entries:
        v = VERIFIED.match(e["verified"].strip())
        if not v or not re.search(r"https?://", e["verified"]) or not re.search(r"\d{4}-\d{2}-\d{2}", e["verified"]):
            out.append(f"{e['key']}: no '% verified: <url> <date>' line directly above it")
        for f in REQUIRED.get(e["type"], ("title", "year")):
            if not e["fields"].get(f):
                out.append(f"{e['key']}: {e['type']} without {f}")
        if e["type"] not in REQUIRED:
            out.append(f"{e['key']}: entry type @{e['type']} not expected (use article/inproceedings/book/misc)")
        p = e["fields"].get("pages", "")
        if p and not re.fullmatch(r"\d+--\d+|\d+", p):
            out.append(f"{e['key']}: pages {p!r} should be first--last")
    return out


# ---------------------------------------------------------------- online comparison with Crossref
def norm(s: str) -> str:
    s = re.sub(r"\\[\"'`^~=.]\{?(\w)\}?", r"\1", s)          # TeX accents -> letter
    s = re.sub(r"<[^>]+>", "", html.unescape(s))              # JATS tags in Crossref titles
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.replace("``", "").replace("''", "")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def family(name: str) -> str:
    """A family name compared without accents, case or a Jr./II/III suffix."""
    n = norm(name)
    return "" if n in ("iii", "ii", "jr") else re.sub(r"(iii|ii|jr)$", "", n)


def bib_families(author: str) -> list[str]:
    """Family names in order: the part before a top-level comma, else the last top-level word or {group}."""
    fams = []
    for a in re.split(r"\s+and\s+", author.strip()):
        tokens, depth, cur = [], 0, ""
        for ch in a.strip():
            depth += {"{": 1, "}": -1}.get(ch, 0)
            if depth == 0 and ch in " ,":
                if cur:
                    tokens.append(cur)
                if ch == ",":
                    tokens.append(",")
                cur = ""
            else:
                cur += ch
        if cur:
            tokens.append(cur)
        fams.append(family("".join(tokens[:tokens.index(",")]) if "," in tokens else tokens[-1]))
    return fams


def crossref(doi: str) -> dict:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/()<>;:.-_")
    req = urllib.request.Request(url, headers={"User-Agent": "nba-hub-paper-refs-check/1.0"})
    return json.load(urllib.request.urlopen(req, timeout=30))["message"]


def online_problems(entries: list[dict]) -> list[str]:
    out = []
    for e in entries:
        f, doi = e["fields"], e["fields"].get("doi")
        if not doi:
            print(f"  {e['key']}: no DOI, checked by its '% verified' page only")
            continue
        try:
            m = crossref(doi)
        except Exception as exc:                               # network trouble is a failure, not a pass
            out.append(f"{e['key']}: Crossref lookup failed ({exc})")
            continue
        issued = (m.get("issued", {}).get("date-parts") or [[None]])[0][0]
        checks = [("title", norm(f.get("title", "")), norm((m.get("title") or [""])[0])),
                  ("year", f.get("year"), str(issued)),
                  ("volume", f.get("volume"), m.get("volume")),
                  ("number", f.get("number"), m.get("issue"))]
        if m.get("page") and f.get("pages") and not ("-" not in m["page"] and f["pages"].split("--")[0] == m["page"]):
            checks.append(("pages", f["pages"].replace("--", "-"), m["page"]))  # a first page alone agrees with first--last
        elif m.get("page") and not f.get("pages"):
            out.append(f"{e['key']}: Crossref has pages {m['page']}, the entry has none")
        for name, mine, theirs in checks:
            if mine is None or theirs in (None, "None", ""):
                continue
            if name == "title" and norm((m.get("title") or [""])[0]) and mine != theirs:
                out.append(f"{e['key']}: title differs from Crossref: {(m.get('title') or [''])[0]!r}")
            elif name != "title" and str(mine) != str(theirs):
                out.append(f"{e['key']}: {name} {mine} vs Crossref {theirs}")
        cr_fam = [family(a.get("family", a.get("name", ""))) or family((a.get("given") or "").split()[-1:][0] if a.get("given") else "")
                  for a in m.get("author", [])]  # Crossref files some suffixes as the family ("III", given "Hal Daume")
        mine_fam = bib_families(f.get("author", ""))
        same = len(mine_fam) == len(cr_fam) and all(a == b or (b and a.endswith(b)) for a, b in zip(mine_fam, cr_fam))
        if f.get("author") and cr_fam and not same:  # "endswith": Crossref may keep half of a double surname
            out.append(f"{e['key']}: authors {mine_fam} vs Crossref {cr_fam}")
        print(f"  {e['key']}: checked against Crossref {doi}")
        time.sleep(0.3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--online", action="store_true", help="also compare every DOI entry with Crossref")
    args = ap.parse_args()
    with open(TEX, encoding="utf-8") as fh:
        tex = fh.read()
    with open(BIB, encoding="utf-8") as fh:
        entries = parse_bib(fh.read())
    problems = offline_problems(tex, entries)
    if args.online:
        problems += online_problems(entries)
    n_doi = sum(1 for e in entries if e["fields"].get("doi"))
    print(f"refs.bib: {len(entries)} entries ({n_doi} with a DOI), {len(set(cited_keys(tex)))} keys cited")
    for p in problems:
        print("  PROBLEM:", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
