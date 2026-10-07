"""
season_sim_live.py
==================
The live season's schedule for the Season Simulator (round 9 step 4, 2026-10-07): the games still to play.
For a finished season the simulator plays "the schedule as actually played" (the games in game_scores on or
after the date). A live season's remaining games are not in game_scores yet, so they come from the Forecast
Ledger's nightly read of ESPN's season (`ledger_results`, every event of the season with its status; written
only by scripts/ledger_update.py and read here), with the locked schedule (`ledger_schedule`) as the fallback
before the first nightly run. Back-to-backs come from the schedule's dates (the ledger checked that rule
against every stored rest day of 2010-11 on). Postponed and cancelled events are left out, as the ledger
leaves them out; the NBA Cup final (which does not count in the standings) too.

Shared by api/routers/season_sim.py (the live view, which decides what is live: a season with no postseason
facts) and scripts/build_season_sim.py --season N (the season row's dates and checkpoints). Nothing here writes,
and nothing here reads a table a build produces (scripts/build_luck_schedule.py imports it too).
"""

from datetime import date

import numpy as np
import pandas as pd

NOT_PLAYED = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED", "STATUS_FORFEIT")
CUP_FINAL = "NBA Cup Championship"
COLS = ["espn_id", "game_date", "home", "away", "neutral_site", "completed", "status", "note"]


def schedule(cur, season):
    """Every counting event of the season as ESPN lists it (ledger_results, else the locked schedule), one row
    per game: espn_id, game_date, home, away, neutral_site, completed, status, home_b2b, away_b2b, venue, source.
    Empty frame when neither table has the season."""
    cur.execute("SELECT to_regclass('ledger_results'), to_regclass('ledger_schedule')")
    has_res, has_sched = cur.fetchone()
    rows, source = [], None
    if has_res is not None:
        cur.execute(f"""SELECT {', '.join(COLS)} FROM ledger_results WHERE season = %s AND counts
                        AND home IS NOT NULL AND away IS NOT NULL ORDER BY game_date, espn_id""", (int(season),))
        rows, source = cur.fetchall(), "ledger_results"
    if not rows and has_sched is not None:
        cur.execute("""SELECT espn_id, game_date, home, away, neutral_site, FALSE, 'STATUS_SCHEDULED', note
                       FROM ledger_schedule WHERE season = %s AND counted ORDER BY game_date, espn_id""", (int(season),))
        rows, source = cur.fetchall(), "ledger_schedule"
    df = pd.DataFrame(rows, columns=COLS)
    if df.empty:
        return df.assign(home_b2b=[], away_b2b=[], venue=[], source=source)
    df = df[~df.status.isin(NOT_PLAYED) & ~df.note.fillna("").str.contains(CUP_FINAL)].copy()
    df["game_date"] = pd.to_datetime(df.game_date).dt.date
    df["neutral_site"] = df.neutral_site.fillna(False).astype(bool)
    df["completed"] = df.completed.fillna(False).astype(bool)
    df["venue"] = np.where(df.neutral_site, 0, 1)
    # back-to-backs from the dates: a side played the day before (its previous game on this schedule)
    long = pd.concat([df[["espn_id", "game_date", "home"]].rename(columns={"home": "team"}),
                      df[["espn_id", "game_date", "away"]].rename(columns={"away": "team"})])
    long = long.sort_values(["team", "game_date", "espn_id"])
    prev = long.groupby("team").game_date.shift()
    long["b2b"] = [(p is not None and not pd.isna(p) and (g - p).days == 1) for g, p in zip(long.game_date, prev)]
    b2b = long.set_index(["espn_id", "team"]).b2b
    df["home_b2b"] = [bool(b2b.get((e, t), False)) for e, t in zip(df.espn_id, df.home)]
    df["away_b2b"] = [bool(b2b.get((e, t), False)) for e, t in zip(df.espn_id, df.away)]
    df["source"] = source
    return df.sort_values(["game_date", "espn_id"]).reset_index(drop=True)


def remaining_home_rows(sched, as_of):
    """The games on or after the morning of `as_of`, in the shape season_sim_lib.simulate wants (home, away, venue,
    home_b2b, away_b2b, game_date): played ones included, as for a finished season."""
    left = sched[sched.game_date >= as_of]
    return left[["home", "away", "venue", "home_b2b", "away_b2b", "game_date"]].reset_index(drop=True)


def checkpoint_dates(sched):
    """season_sim_lib.checkpoint_dates' rule on the schedule as it stands (every scheduled game, played or not)."""
    dates = sched.sort_values(["game_date", "espn_id"]).game_date.to_numpy()
    if len(dates) == 0:
        return {"opening": None, "halfway": None, "sixty": None, "last": None}
    return {"opening": dates[0], "halfway": dates[len(dates) // 2], "sixty": dates[int(len(dates) * 60 / 82)], "last": dates[-1]}


def today_eastern():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def clamp(d, lo, hi):
    return max(lo, min(d, hi)) if isinstance(d, date) else d
