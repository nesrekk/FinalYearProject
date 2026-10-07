"""
vault_status.py
================
Writes `Status.md` in the owner's Obsidian vault: one generated page that says where the project is, so no chat has
to retype progress into the vault by hand. Read-only on the repo and the database; the only file it writes is
`<vault>/Status.md` (never edit that file by hand: the next run replaces it).

What it collects (each part is skipped with a note if its source is missing):
  - the step tables of the round plans in `paper/ROUND*_PLAN.md` (untracked, local), with each step's status;
  - the issue lists `docs/qa/ROUND*_ISSUES.md`: entries and how many are open;
  - the last commits and whether the working tree has uncommitted changes;
  - chats / jobs running right now (Claude CLI sessions, Python scripts, pytest);
  - the live season: the last `daily_update_runs` row and the newest stored game date (local DB only);
  - the test count and the Layerbase line as CLAUDE.md records them.

Usage:
  python3 scripts/vault_status.py            # write <vault>/Status.md
  python3 scripts/vault_status.py --print    # print it instead
The vault folder defaults to ~/Desktop/Second Brain/FInalYearProject (override with NBAHUB_VAULT).
"""

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = Path.home() / "Desktop" / "Second Brain" / "FInalYearProject"
VAULT = Path(os.environ.get("NBAHUB_VAULT", DEFAULT_VAULT))


def sh(*args):
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def plan_number(path):
    m = re.search(r"ROUND(\d+)_PLAN", path.name)
    return int(m.group(1)) if m else -1


def step_table(text):
    """Rows of the plan's 'Step | What | ...' table as dicts (only tables that have a Status column)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| Step |"):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip().strip("|").split("|")]
                if len(cells) == len(head):
                    rows.append(dict(zip(head, cells)))
            return head, rows
    return None, []


def short(s, n):
    s = re.sub(r"\*\*|`", "", s or "")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def plans_section():
    plans = sorted((ROOT / "paper").glob("ROUND*_PLAN.md"), key=plan_number)
    if not plans:
        return ["_No plan files found in `paper/`._"]
    out, nxt = [], []
    for p in plans:
        head, rows = step_table(p.read_text())
        if not rows or "Status" not in head:
            out.append(f"- **{p.stem}**: no status column (older plan format).")
            continue
        done = sum(r["Status"].startswith("✅") for r in rows)
        out.append(f"- **{p.stem}** (`paper/{p.name}`): {done} of {len(rows)} steps done.")
        if done < len(rows):
            out.append("")
            out.append("  | Step | What | Run in | Status |")
            out.append("  |---|---|---|---|")
            for r in rows:
                out.append(f"  | {r['Step']} | {short(r.get('What', ''), 80)} | {short(r.get('Run in', r.get('Where', '')), 14)} "
                           f"| {short(r['Status'], 40) or '—'} |")
                if not r["Status"].startswith(("✅", "⏸", "⏳")) and not nxt:
                    nxt.append(f"{p.stem} step {r['Step']}: {short(r.get('What', ''), 90)}")
            out.append("")
    return out, nxt


def issues_section():
    out = []
    for p in sorted((ROOT / "docs" / "qa").glob("ROUND*_ISSUES.md")):
        text = p.read_text()
        blocks = re.split(r"\n(?=### R\d+(?:\.\d+)?-\d+)", text)
        entries = [b for b in blocks if re.match(r"### R", b)]
        open_ = []
        for b in entries:
            st = re.search(r"\*\*Status:\*\*\s*([^\n]*)", b)
            status = st.group(1).strip().lower() if st else ""
            if status.startswith("open"):
                title = re.match(r"### (\S+) · ([^\n]*)", b)
                if title:
                    open_.append(f"{title.group(1)} {short(title.group(2), 70)}")
        out.append(f"- `docs/qa/{p.name}`: {len(entries)} entries, {len(open_)} open.")
        out += [f"  - {o}" for o in open_]
    return out or ["_No issue lists found._"]


def git_section():
    log = sh("git", "log", "-10", "--format=%h %ad %s", "--date=short").strip().splitlines()
    dirty = [l for l in sh("git", "status", "--porcelain").splitlines() if not l.endswith("paper/")]
    branch = sh("git", "status", "-sb").splitlines()[:1]
    out = [f"- Branch: `{branch[0][3:] if branch else '?'}`; uncommitted changes in {len(dirty)} path(s)"
           + (" (another chat may be mid-step)." if dirty else ".")]
    for l in log:
        h, d, subj = (l.split(" ", 2) + ["", ""])[:3]
        out.append(f"  - `{h}` {d} {short(subj, 120)}")
    return out


def running_section():
    ps = sh("ps", "-Ao", "etime,command")
    rows, seen = [], set()
    for line in ps.splitlines():
        if line.lstrip().split(" ", 1)[-1].startswith(("/bin/zsh", "/bin/bash", "zsh ", "bash ")):
            continue
        if re.search(r"claude --model|scripts/[a-z_]+\.py|\bpytest\b|build_[a-z_]+\.py|paper_[a-z_]+\.py", line) \
                and "vault_status" not in line and "grep" not in line:
            et, _, cmd = line.strip().partition(" ")
            m = re.search(r"(claude --model \S+ --effort \S+|[a-z_]+\.py[^\n]{0,40}|pytest[^\n]{0,40})", cmd)
            what = (m.group(1) if m else short(cmd, 60)).split(" >")[0].strip()
            if what not in seen:
                seen.add(what)
                rows.append(f"- {what} (running {et})")
    return rows or ["- Nothing running."]


def live_section():
    try:
        sys.path.insert(0, str(ROOT / "api"))
        os.environ["DB_TARGET"] = "local"
        import psycopg2
        from db_config import DB_CONFIG
        conn = psycopg2.connect(**DB_CONFIG, connect_timeout=5)
        cur = conn.cursor()
        out = []
        cur.execute("SELECT to_regclass('daily_update_runs') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'daily_update_runs'")
            names = {r[0] for r in cur.fetchall()}
            order = "started_at DESC NULLS LAST" if "started_at" in names else "1 DESC"
            cur.execute(f"SELECT * FROM daily_update_runs ORDER BY {order} LIMIT 1")
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                r = dict(zip(cols, row))
                keys = [k for k in ("started_at", "run_at", "status", "through_date", "summary") if k in r]
                out.append("- Last daily update: " + "; ".join(f"{k} {short(str(r[k]), 90)}" for k in keys))
            else:
                out.append("- No daily update has run yet.")
        cur.execute("SELECT MAX(game_date) FROM game_scores")
        out.append(f"- Newest stored game date: {cur.fetchone()[0]}")
        conn.close()
        return out
    except Exception as e:  # DB down is normal after a reboot
        return [f"- Database not reachable ({type(e).__name__}); start it with `brew services run postgresql@18`."]


def claude_md_section():
    path = ROOT / "CLAUDE.md"
    if not path.exists():
        return []
    text = path.read_text()
    out = []
    m = re.search(r"Tests: \*\*(\d[\d,]*)\*\*[^;)]*", text)
    if m:
        out.append(f"- Tests (as CLAUDE.md records them): {short(m.group(0), 110)}")
    m = re.search(r"\*\*Layerbase mirror in sync\*\*[^\n]{0,200}", text)
    if m:
        out.append(f"- Layerbase: {short(m.group(0), 200)}")
    return out


def build():
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    plans, nxt = plans_section()
    lines = [
        "---",
        "generated: true",
        f"updated: {now}",
        "tags: [status, generated]",
        "---",
        "",
        "# Status (generated)",
        "",
        f"> Written by `scripts/vault_status.py` on **{now} IST**. Don't edit this page: the next run replaces it.",
        "> Rerun it at the end of any chat that commits: `python3 scripts/vault_status.py`.",
        "",
        "## Next",
        "",
        f"- {nxt[0]}" if nxt else "- Every step in the plans is done or on hold.",
        "- Owner's own items: [[Owner-ToDo]].",
        "",
        "## Rounds",
        "",
        *plans,
        "## Issue lists",
        "",
        *issues_section(),
        "",
        "## Live season",
        "",
        *live_section(),
        "",
        "## Repo",
        "",
        *git_section(),
        *claude_md_section(),
        "",
        "## Running now",
        "",
        *running_section(),
        "",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--print", action="store_true", help="print instead of writing the vault file")
    args = ap.parse_args()
    text = build()
    if args.print:
        print(text)
        return
    if not VAULT.exists():
        sys.exit(f"vault folder not found: {VAULT} (set NBAHUB_VAULT)")
    (VAULT / "Status.md").write_text(text)
    print(f"wrote {VAULT / 'Status.md'} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
