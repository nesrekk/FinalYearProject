"""
current_season.py
=================
The app's one "current season" rule (round 9 step 5, 2026-10-07; R9-011). Every season picker opens on
`status()["current"]`, and every page that shows a season still being played says how far it is.

    latest_complete  the newest season whose regular season is over (luck_schedule_seasons.complete;
                     without that table, the paper's test season, paper_freeze.MAX_PAPER_SEASON)
    live             the newest season after latest_complete with at least DEFAULT_AFTER_GAMES regular-season
                     finals stored in game_scores (scripts/daily_update.py writes them from opening night),
                     else None
    current          live's season when there is one, else latest_complete

So the app opens on 2025-26 until the first 2026-27 final is stored (opening night, 2026-10-20) and on 2026-27
from then on, with the "through <date>" line and the early-season warnings below on every page that shows it.
The playoffs don't change it: a season stays live until its regular season is complete (the season-to-date
builds mark it), and then it is latest_complete.

`early` (live seasons only) is the reliability of a typical rotation player's numbers so far, from Stat
Stability's half-signal samples (stat_stability; reliability = n / (n + M), the Leaderboard's `_sample`):
the n is the median over players averaging 15+ minutes, in each stat's own unit (games, attempts,
possessions). Nothing here writes; nothing here reads a paper table's live rows into a paper number.
"""

import time

from paper_freeze import MAX_PAPER_SEASON

DEFAULT_AFTER_GAMES = 1      # the live season becomes the default once this many regular-season finals are stored
EARLY_STATS = [               # (stat key in stat_stability / stat_samples, label as the app writes it)
    ("pts", "points a game"), ("fg_pct", "FG%"), ("fg3_pct", "3P%"), ("ft_pct", "FT%"), ("ts_pct", "TS%"),
    ("stl", "steals a game"), ("plus_minus", "plus-minus"), ("net_rating", "net rating"),
]
ROTATION_MPG = 15
_TTL_SECONDS = 300
_cache = {"at": 0.0, "value": None}


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}" if season else None


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


def latest_complete_season(cur):
    if _exists(cur, "luck_schedule_seasons"):
        cur.execute("SELECT MAX(season) FROM luck_schedule_seasons WHERE complete")
        s = cur.fetchone()[0]
        if s is not None:
            return int(s)
    return MAX_PAPER_SEASON


def _live_block(cur, after):
    """The newest season past `after` with enough finals stored, as {season, label, through, games, ...}."""
    if not _exists(cur, "game_scores"):
        return None
    cur.execute("""SELECT season, COUNT(DISTINCT game_id), MIN(game_date), MAX(game_date)
                   FROM game_scores WHERE season > %s GROUP BY season ORDER BY season DESC LIMIT 1""", (after,))
    r = cur.fetchone()
    if r is None or r[1] < DEFAULT_AFTER_GAMES:
        return None
    season, games, first, through = int(r[0]), int(r[1]), r[2], r[3]
    cur.execute("""SELECT MIN(n), MAX(n), COUNT(*) FROM (SELECT team_abbreviation, COUNT(*) n FROM game_scores
                   WHERE season = %s GROUP BY 1) t""", (season,))
    tmin, tmax, teams = cur.fetchone()
    scheduled = None
    if _exists(cur, "luck_schedule_seasons"):
        cur.execute("SELECT scheduled FROM luck_schedule_seasons WHERE season = %s", (season,))
        row = cur.fetchone()
        scheduled = int(row[0]) if row and row[0] else None
    if scheduled is None and _exists(cur, "ledger_schedule"):
        cur.execute("SELECT COUNT(*) FROM ledger_schedule WHERE season = %s AND counted", (season,))
        scheduled = int(cur.fetchone()[0]) or None
    return {"season": season, "label": season_label(season), "first_date": first.isoformat(),
            "through": through.isoformat(), "games": games, "scheduled": scheduled,
            "team_games_min": int(tmin or 0), "team_games_max": int(tmax or 0), "teams": int(teams or 0)}


def _upcoming(cur, after):
    """The next season's first tip when it isn't live yet (the Forecast Ledger's locked schedule)."""
    if not _exists(cur, "ledger_schedule"):
        return None
    cur.execute("SELECT season, MIN(game_date), COUNT(*) FILTER (WHERE counted) FROM ledger_schedule "
                "WHERE season > %s GROUP BY season ORDER BY season LIMIT 1", (after,))
    r = cur.fetchone()
    if r is None:
        return None
    return {"season": int(r[0]), "label": season_label(int(r[0])), "first_date": r[1].isoformat(), "scheduled": int(r[2])}


def early_reliability(cur, season):
    """[{stat, label, unit_label, stable_n, median_n, reliability, players}] for a season: how much of a typical
    rotation player's number so far is signal (Stat Stability's M; the Leaderboard's reliability formula)."""
    from routers.leaderboard import stable_samples
    from stat_samples import SEASON_SAMPLE_SQL
    stable = stable_samples(cur)
    keys = [(k, lab) for k, lab in EARLY_STATS if k in stable and k in SEASON_SAMPLE_SQL]
    if not keys:
        return []
    exprs = ", ".join(f"percentile_cont(0.5) WITHIN GROUP (ORDER BY ({SEASON_SAMPLE_SQL[k]})::float)" for k, _ in keys)
    cur.execute(f"""SELECT COUNT(*), {exprs} FROM player_season_stats
                    WHERE season = %s AND team_abbreviation <> 'TOT' AND gp > 0 AND min >= %s""", (season, ROTATION_MPG))
    row = cur.fetchone()
    players = int(row[0] or 0)
    out = []
    for (k, lab), n in zip(keys, row[1:]):
        if n is None:
            continue
        m = stable[k]["stable_n"]
        out.append({"stat": k, "label": lab, "unit_label": stable[k]["unit_label"], "stable_n": m,
                    "median_n": round(float(n), 1), "reliability": round(float(n) / (float(n) + m), 2) if n > 0 else 0.0,
                    "players": players})
    return out


def compute(cur):
    complete = latest_complete_season(cur)
    live = _live_block(cur, complete)
    current = live["season"] if live else complete
    early = early_reliability(cur, live["season"]) if live else []
    return {
        "current": current, "current_label": season_label(current),
        "latest_complete": complete, "latest_complete_label": season_label(complete),
        "paper_season": MAX_PAPER_SEASON,
        "live": live,
        "upcoming": None if live else _upcoming(cur, complete),
        "early": early,
        "rule": (f"The app opens on the newest season with at least {DEFAULT_AFTER_GAMES} regular-season final"
                 f"{'' if DEFAULT_AFTER_GAMES == 1 else 's'} stored (game_scores); until then on the newest complete season "
                 "(luck_schedule_seasons.complete)."),
        "early_rule": (f"Reliability of a typical rotation player's number so far (median over players averaging "
                       f"{ROTATION_MPG}+ minutes): n / (n + M), M = the sample at which the stat is half signal, half "
                       "noise (Stat Stability)."),
    }


def status(cur=None):
    """compute() cached for five minutes (the daily update adds a day's games once a day)."""
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _TTL_SECONDS:
        return _cache["value"]
    if cur is None:
        from impact_core import get_db
        with get_db() as conn:
            value = compute(conn.cursor())
    else:
        value = compute(cur)
    _cache.update(at=now, value=value)
    return value


def clear_cache():
    _cache.update(at=0.0, value=None)


def current_season(cur=None):
    return status(cur)["current"]


def default_season_for(min_games, cur=None):
    """For a tool with a games floor: the current season once its teams have played `min_games` games, else the
    newest complete one (the frontend's utils/season.js defaultSeasonFor)."""
    st = status(cur)
    live = st["live"]
    if live and live["team_games_max"] < min_games:
        return st["latest_complete"]
    return st["current"]


def latest_season_in(cur, table, where="TRUE", args=()):
    """The newest season a table has rows for (optionally filtered): the default of a route whose data doesn't
    follow the daily update (tracking fetches, season-end builds), so it never opens on a season it doesn't have."""
    cur.execute(f"SELECT MAX(season) FROM {table} WHERE {where}", args)
    s = cur.fetchone()[0]
    return None if s is None else int(s)
