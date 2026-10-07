"""
weekly_report.py
================
The weekly guide report (round 9 step 6): one page per week of the live season, "what the models said vs what
happened", written to docs/weekly/<week end>.md. The numbers come from api/weekly_report_lib.py, which the app's
printable page (Teams > Forecast Ledger > Weekly report, GET /ledger/weekly) reads too; see its docstring for every
section and rule. Reads only: run it after the Monday daily update (daily_update.py, which runs the ledger first),
so the ledger has scored Sunday's games.

    cd scripts && python3 weekly_report.py                    # the week that ended last Sunday (US Eastern)
    python3 weekly_report.py --end 2026-11-01                 # the week ending that date (Monday to Sunday)
    python3 weekly_report.py --all                            # every week with finals so far
    python3 weekly_report.py --stdout                         # print, write nothing
    options: --season N (default 2027)  --out-dir DIR (default docs/weekly)
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg2

from db_config import DB_CONFIG

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))
import weekly_report_lib as W  # noqa: E402

SEASON = 2027
EASTERN = ZoneInfo("America/New_York")
SHORT = {"roster": "Roster, nightly", "as_is": "As is, nightly", "record": "Record only",
         "roster_pre": "Roster, locked", "as_is_pre": "As is, locked"}


def f4(x):
    return "n/a" if x is None else f"{x:.4f}"


def sgn(x, d=4):
    if x is None:
        return "n/a"
    s = f"{x:+.{d}f}"
    return s.replace("+-", "-") if float(s) != 0 else f"{0:.{d}f}"


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def ci(lo, hi, d=4):
    return "" if lo is None else f" [{lo:.{d}f}, {hi:.{d}f}]"


def pv(p):
    return "n/a" if p is None else ("< 0.001" if p < 0.001 else f"{p:.3f}")


def score_table(rows):
    out = ["| Version | Games | Brier [95% CI] | Log loss [95% CI] | Favourite won |", "|---|---:|---|---|---:|"]
    for r in rows:
        out.append(f"| {r['label']} | {r['n']} | {f4(r['brier'])}{ci(r['brier_lo'], r['brier_hi'])} | "
                   f"{f4(r['log_loss'])}{ci(r['log_loss_lo'], r['log_loss_hi'])} | {pct(r['favourite_won'])} |")
    return out


def render(rep):
    L = [f"# Weekly report: {rep['label']}, week of {rep['start']} to {rep['end']}", ""]
    if not rep["live_tables"] or rep["games_season"] == 0:
        L += ["No game of the season had been played by the end of this week, so there is nothing to score yet.",
              "The Forecast Ledger's locked odds are on Teams > Forecast Ledger > Preseason lock.", ""]
        return "\n".join(L)
    lg, st, nt = rep["ledger"], rep["standings"], rep["notable"]
    run = rep["last_run"]
    L += [f"**{rep['games_week']} games this week, {rep['games_season']} so far** (finals through {rep['last_game']}, "
          f"US Eastern dates). Forecast Ledger: the {rep['label']} forecasts were locked on {rep['lock']['locked_at']} (SHA-256 "
          f"{rep['lock']['sha256'][:16]}...); "
          "each game's odds are logged on the morning of its date, before tip-off, by the code at the lock's git tag.", ""]
    if run:
        L += [f"Last ledger run used: {run['today_et']} (run {run['run_id']})"
              + (f"; it was waiting: {run['waiting']}" if run["waiting"] else "") + ".", ""]
    if lg["recomputed_week"]:
        L += [f"{lg['recomputed_week']} of this week's games have odds logged after tip-off (a missed run, recomputed by the "
              "same rule): they are scored, and the stored tests also run without them.", ""]

    L += ["## 1. How the forecasts are doing", "",
          "Lower is better for both scores. A coin flip scores Brier 0.25 and log loss 0.693. Games scored under all five "
          f"versions only (season {lg['common_season']}, this week {lg['common_week']}); intervals resample games "
          f"({rep['resamples']:,} times).", "", "**Season so far**", ""]
    L += score_table(lg["season"]) + ["", "**This week**", ""]
    L += score_table(lg["week"]) if lg["week"] else ["No game this week."]
    L += ["", "**Paired tests** (A minus B: negative = A had the lower error)", ""]
    if lg["tests"]:
        L += [f"Stored by the ledger's run of {lg['tests_as_of']}. All games:", "",
              "| A - B | Metric | Games | Difference [95% CI] | p (bootstrap) |", "|---|---|---:|---|---:|"]
        for t in lg["tests"]:
            if t["variant"] != "all":
                continue
            L.append(f"| {SHORT[t['model_a']]} - {SHORT[t['model_b']]}{' (headline)' if t['headline'] else ''} | "
                     f"{t['metric'].replace('_', ' ')} | {t['n']} | {sgn(t['diff'], 5)}{ci(t['ci_lo'], t['ci_hi'], 5)} | {pv(t['p_boot'])} |")
        nb = [t for t in lg["tests"] if t["variant"] == "logged_before_tip"]
        if nb:
            L += ["", f"Only games whose odds were all logged before tip-off: {nb[0]['n']} games (same tests stored)."]
    else:
        L += [f"None yet: the ledger stores paired tests from {rep['min_test_games']} games scored under every version "
              f"({lg['common_season']} so far)."]
    L += ["", "**Biggest misses this week** (the nightly roster-aware odds, by log loss)", ""]
    if lg["misses"]:
        L += ["| Date | Game | Final | Winner's chance (roster, nightly) | As is | Record only | Roster, locked |",
              "|---|---|---|---:|---:|---:|---:|"]
        for m in lg["misses"]:
            def wc(v):
                p = m.get(v)
                return "n/a" if p is None else pct(p if m["winner"] == m["home"] else 1 - p)
            L.append(f"| {m['date']} | {m['away']} at {m['home']} | {m['pts_away']}-{m['pts_home']} | "
                     f"{pct(m['winner_chance_roster'])} | {wc('as_is')} | {wc('record')} | {wc('roster_pre')} |")
    else:
        L += ["No game this week."]

    L += ["", "## 2. Standings against the locked forecast", "",
          f"Expected final wins on the morning of {st['as_of']} (record so far plus each remaining game's chance), against "
          "the locked opening-day mean and its 80% range; the change is against the morning the week began"
          + (f" ({st['was_as_of']})." if st["was_as_of"] else ".") + " * = outside the locked 80% range.", ""]
    for conf in ("East", "West"):
        L += [f"**{conf}**", "", "| Team | W-L | Roster: now (locked, 80%) | Week | As is: now (locked) |",
              "|---|---|---|---:|---|"]
        for t in [t for t in st["teams"] if t["conference"] == conf]:
            r, a = t["roster"], t["as_is"]
            star = "*" if r["outside_range"] else ""
            now_r = "n/a" if r["exp_final_wins"] is None else f"{r['exp_final_wins']:.1f}{star}"
            now_a = "n/a" if a["exp_final_wins"] is None else f"{a['exp_final_wins']:.1f}{'*' if a['outside_range'] else ''}"
            L.append(f"| {t['team']} | {t['wins']}-{t['losses']} | {now_r} ({r['locked_mean']:.1f}, "
                     f"{r['locked_p10']:.0f}-{r['locked_p90']:.0f}) | {sgn(r['week_change'], 1)} | {now_a} ({a['locked_mean']:.1f}) |")
        L.append("")

    L += ["## 3. Notable this week", ""]
    if nt["movers"]:
        L.append("Biggest moves in expected final wins (roster-aware): "
                 + "; ".join(f"{m['team']} {sgn(m['change'], 1)} to {m['exp_final_wins']:.1f} ({m['wins']}-{m['losses']})"
                             for m in nt["movers"]) + ".")
    bg, up = nt["best_game"], nt["biggest_upset"]
    if bg:
        L.append(f"Best game by excitement (Best Games & Upsets): {bg['away']} at {bg['home']}, {bg['pts_away']}-{bg['pts_home']}"
                 f"{' (OT)' if (bg['periods'] or 4) > 4 else ''} on {bg['date']}, excitement {bg['excitement']:.2f}, "
                 f"{bg['lead_changes']} lead changes.")
    if up:
        L.append(f"Biggest upset by the app's held-out pre-game odds: {up['winner']} beat {up['loser']} "
                 f"({up['pts_away']}-{up['pts_home']}, {up['away']} at {up['home']}) on {up['date']} with a "
                 f"{pct(up['winner_chance'])} chance.")
    if not bg and not up:
        L.append("The app's own game tables (Best Games, held-out pre-game odds) have no game of this week yet: they come "
                 "from the daily update's season rebuild and models.")
    if nt["tracker"]:
        L += ["", f"Rating Tracker, top {len(nt['tracker'])} so far (points per 100 possessions, 95% interval; "
              f"{W.TRACKER_MIN_GAMES}+ games; early-season ratings lean on last season's):", "",
              "| Player | Team | Games | Rating [95% CI] |", "|---|---|---:|---|"]
        for p in nt["tracker"]:
            L.append(f"| {p['player']} | {p['teams'] or ''} | {p['games']} | {sgn(p['rating'], 1)}{ci(p['ci_lo'], p['ci_hi'], 1)} |")
    L += ["", "---", "", "Made by scripts/weekly_report.py from the stored tables (the same numbers as the app's Weekly report "
          f"tab: ?page=ledger&tab=weekly&week={rep['end']}). Reads only; the forecasts themselves are the ledger's logged rows.", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--end", type=date.fromisoformat, help="the week's last date (default: last Sunday, US Eastern)")
    ap.add_argument("--season", type=int, default=SEASON)
    ap.add_argument("--all", action="store_true", help="every week with finals so far")
    ap.add_argument("--out-dir", default=str(ROOT / "docs" / "weekly"))
    ap.add_argument("--stdout", action="store_true", help="print the report(s); write nothing")
    a = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    ends = W.weeks(conn, a.season) if a.all else [a.end or W.last_sunday(datetime.now(EASTERN).date())]
    if not ends:
        print("no week with a final game yet: nothing to report", flush=True)
        return 0
    out = Path(a.out_dir)
    for end in ends:
        text = render(W.build(conn, a.season, end))
        if a.stdout:
            print(text)
            continue
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{end.isoformat()}.md"
        path.write_text(text)
        print(f"wrote {path}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
