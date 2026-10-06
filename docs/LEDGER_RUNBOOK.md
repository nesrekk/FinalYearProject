# Forecast Ledger: game-day runbook (2026-27)

The 2026-27 forecasts were **locked on 2026-09-30** (SHA-256 `c1a48402c1363db2f1e247f6931bad5c788a928d36717806aa5d8f89e8483d17`,
git tag `ledger-2026-27` = `c9071a8`). During the season one command scores them: `scripts/ledger_update.py`. It
logs each game's odds on the morning of its date, stores ESPN's final scores, updates the standings projection and,
from 100 scored games, the paired tests. The Live scoring tab of Teams › Forecast Ledger reads what it writes.

Checked 2026-10-06 (round 8.5 step A): the lock reproduces its hash; ESPN's live schedule equals the locked one (1,206
events, every id, date, tip time, team, venue and note the same); dry runs for 2026-10-20 and 2026-10-21 behave as below;
a simulated opening week passes (`api/tests/test_ledger_gameday.py`).

## Before the first tip (once, before 2026-10-20 19:00 UTC = 15:00 ET = 00:30 IST on the 21st)

1. **The tag is on GitHub:** `git ls-remote --tags origin ledger-2026-27` must print
   `c9071a8b5a85d16880376145feb9bcc7cd7bd07d  refs/tags/ledger-2026-27`. It does (checked 2026-10-06).
2. **The hash is with the guide:** email the SHA-256 above (and the tag name) to the guide before the first tip, so
   the forecasts can be shown to predate the games. (Only the owner can do this; it is not recorded in the repo.)
3. **The lock still reproduces:** `cd scripts && python3 ledger_lock.py --verify` prints `sha256 c1a48402… reproduced`.

## The one command (every day with games)

```bash
brew services run postgresql@18          # only if Postgres isn't running (after a reboot)
cd ~/Desktop/"main nba project build"/scripts
DB_TARGET=local /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 ledger_update.py
```

`DB_TARGET=local` makes sure it writes to the laptop's database even if `api/.env` points somewhere else. It takes
about a minute (reading ESPN's whole season is 45-70 s). It exits 0 when it worked and prints five lines:

```
frozen code: ledger-2026-27 = c9071a8b5a85, 3 files match the lock's blob ids; lock sha256 c1a48402c1363db2... reproduced
ESPN: 1,206 regular-season events, 1,200 count, 3 final, 0 postponed/cancelled (52s)
odds: 2 dates due through 2026-10-21, 33 new rows (0 after tip: recomputed)
scored: 3 games under every version; no interval yet (fewer than 100)
run 1a2b3c4d5e6f done in 60s
```

(The numbers are what opening week would show; "3 final" the morning after opening night.)

## When to run it

"Today" is the **US Eastern date** (ESPN files games under it). Run it on each game day **after the previous night's
games are final and before that day's first tip**:

| | US Eastern | India (IST) |
|---|---|---|
| Previous night's last game final (latest tip 23:00 ET + ~2.5 h) | ~01:30 | ~11:00; ~12:00 from 1 Nov to 13 Mar (US winter time) |
| Earliest first tip of the season (2027-01-17, Manchester) | 10:30 | 21:00 |
| Usual first tip | 19:00 | 04:30 / 05:30 the next day |

**Run it at about 14:00 IST.** That is inside the window every day of the season. Opening day (2026-10-20): any time
that day in India; the first tip (BOS at DET, 15:00 ET) is 00:30 IST on the 21st.

Days without games: running does nothing harmful (nothing is due), and isn't needed. The day after the last
regular-season game (2027-04-11) run it once more to store the final scores.

## What the output means

- **`N new rows (M after tip: recomputed)`** — rows are odds for one game under one version (as is, roster, record
  only: 3 per game). A row logged after ESPN's tip time is **recomputed**: same rule, same results, same numbers it
  would have had, but it can't prove it was made in advance, so it's stored with `before_tip = false`, the page labels
  it, and `ledger_tests` keeps a second set of tests on only the games logged before tip. Recomputed rows happen when a
  day is missed or the run comes after the first tip. They are not errors; just avoid them.
- **`waiting for N earlier game(s) to go final (...)`** — a date's odds need every earlier game's final score. If one
  isn't final yet (game still on, or ESPN slow to update), today waits: nothing is logged for today and the standings
  keep their last complete morning. **Run again later, before the first tip.**
- **`WARNING: ESPN changed the final score of N game(s)`** — the stored score is updated; odds already logged keep the
  digest of the results they used. Note it in `docs/qa/ROUND9_ISSUES.md`.
- **`note: N locked game(s) no longer on ESPN's schedule`** — ESPN dropped a game id from the season. See "special cases".
- **`0 new rows`** on a second run the same day — correct: a rerun adds nothing unless the results changed.

## Checking it worked

- The page: start the app (`./start.sh`), open `?page=ledger&tab=live`: last update, games scored, odds for games not
  yet final. The live routes aren't cached, so no restart is needed.
- The run log (each run is one row):

  ```bash
  /opt/homebrew/opt/postgresql@18/bin/psql -U postgres -d nba_analytics -c \
    "SELECT started_at, today_et, mode, events_final, new_rows, late_rows, scored_common, waiting
     FROM ledger_runs ORDER BY started_at DESC LIMIT 5"
  ```

  `late_rows` > 0 = recomputed rows; `waiting` not empty = run again later.

## When something goes wrong

- **ESPN doesn't answer:** the script retries each request 4 times, then stops with an error before writing anything.
  Run it again later. If ESPN is down past the first tip, run it once it's back: the day's odds are computed by the
  same rule and labelled recomputed. (`--offline` only re-scores what is already stored; it can't catch up a day.)
- **"connection refused":** Postgres isn't running: `brew services run postgresql@18`.
- **`the stored lock no longer reproduces its SHA-256` or `tag ... doesn't hold the locked code`:** stop. Something
  changed a locked table or the tag. Don't relock, don't move the tag; open a chat and investigate.
- **Any other error:** an error while reading or computing writes nothing (results, odds and standings are written
  together in one transaction after the computing; the tests and the run row in a second one). Rerunning is always
  safe: it adds only what is missing. Copy the output into `docs/qa/ROUND9_ISSUES.md` and open a chat.

## Special cases (all handled by the locked rule; nothing to do by hand)

- **Postponed game:** left out of that date's odds; when ESPN lists its new date, it gets odds on that morning
  (back-to-backs from the dates actually played). An ESPN id listed on two dates keeps its final row, else the latest
  date. The official number for a game is the earliest row logged for the date it was actually played.
- **Suspended / cancelled / forfeited:** treated as not played (they don't block later dates). A cancelled game that
  is never made up stays in the projection's remaining games; it doesn't touch scoring.
- **NBA Cup:** group-play games are ordinary games. The quarterfinals and semifinals (teams known in early December)
  count in the standings; the Championship (2026-12-11) doesn't (Wikipedia "NBA Cup"). The games the NBA adds for the
  other teams after group play appear on ESPN when announced. Games not in the lock have no locked preseason odds, so
  they are scored under the three daily versions only and left out of the like-for-like comparison.
- **Neutral-site games** (Mexico City 2026-11-07, Paris 2027-01-14, Manchester 2027-01-17): no home court.
- **Tip time moved:** ESPN's current tip time is stored and `before_tip` is judged against it.

## Don't

- Edit `api/ledger_lib.py`, `api/season_sim_lib.py` or `api/luck_lib.py` in a way the update would pick up (it imports
  them from the tag; the run stops if the tag's files differ from the lock's), move the tag, relock after the first
  tip, or write the `ledger_*` tables by hand. Only `ledger_update.py` writes them.
- Set up a scheduled job without the owner's OK in that chat (one was set up and removed on 2026-09-30; scheduling is a
  yes/no in round 9 step 7).
- Sync the `ledger_*` tables to Layerbase without the owner's OK.

## Things that change because of a run (expected)

- `scripts/rebuild_all.sh paper-inputs` stops at the claim `LgScored == 0` once the first game is scored. That is on
  purpose: round 9 step 6 rewrites the paper's forward-test sentence from `ledger_tests`.
- Every run changes the paper's printed `\pnLgAsOf` (date of the last run) and `\pnManDigest` (the manifest covers
  the five live ledger tables), so `paper-inputs` is not byte-identical after a run (R8-086, for round 9 step 1).

## Testing without touching the real ledger

- `python3 ledger_update.py --dry-run` (ESPN, computes everything, writes nothing); `--dry-run --today 2026-10-21`
  pretends the date.
- `python3 -m pytest api/tests/test_ledger_gameday.py` (~10 s): runs the real script six times on copies of the locked
  tables in a `zz_ledger_gameday` schema, with a fake ESPN and clock (opening night before tip, the morning after with a
  postponed game, a missed day labelled recomputed, a waiting date, the postponed game on its new date, reruns adding
  nothing), then drops the schema and checks the real tables didn't change. Local database only.
