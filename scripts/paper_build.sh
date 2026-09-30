#!/bin/sh
# Build every version of the conference paper in both \ifanon states and report pages, overfull boxes and undefined
# references (round 5 step 11). Needs tectonic (brew install tectonic; the TeX bundle downloads on the first run).
#   scripts/paper_build.sh            # nba_hub_paper.tex (long), nba_hub_paper_8p.tex, nba_hub_paper_6p.tex
#   scripts/paper_build.sh 8p         # one version
# For each X.tex it writes X.pdf (named) and X_anon.tex + X_anon.pdf (\anontrue), keeps X.log, and prints one line per build.
set -e
cd "$(dirname "$0")/../paper"
versions=${1:-"long 8p 6p"}
for v in $versions; do
  case $v in long) f=nba_hub_paper;; *) f=nba_hub_paper_$v;; esac
  [ -f "$f.tex" ] || { echo "$f.tex: missing"; exit 1; }
  sed 's/^\\anonfalse$/\\anontrue/' "$f.tex" > "${f}_anon.tex"
  for t in "$f" "${f}_anon"; do
    if tectonic --keep-logs "$t.tex" > /dev/null 2>&1; then status=ok; else status=FAILED; fi
    pages=$(grep -o 'Output written on [^ ]* ([0-9]* pages' "$t.log" | grep -o '[0-9]* pages')
    over=$(grep -c '^Overfull' "$t.log" || true); undef=$(grep -c 'Warning: \(Reference\|Citation\)' "$t.log" || true)
    echo "$t: $status, $pages, $over overfull, $undef undefined refs/cites"
    rm -f "$t.blg"
  done
done
