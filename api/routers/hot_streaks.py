"""
Hot Streak Checker: is a player's last N games real?

    GET /games/hot-streak/options                         stats, windows, seasons, persistence table
    GET /games/hot-streak/{player_id}?season=&stat=pts&window=10&as_of=2026-01-15
    GET /games/hot-streaks?season=&stat=pts&window=10&as_of=&direction=hot

Two separate questions, answered separately:

  1. How unusual is the window for him? 4,000 random sets of N games from
     his own season so far (including the window): the share at least as
     hot (or cold) is the p-value, and the window's percentile among his
     actual rolling N-game stretches this season is shown beside it. It
     says whether the run is more than the clumping his own games produce
     by chance, not whether it will last.
  2. How much of it is likely to carry on? From hot_streak_persistence
     (scripts/build_hot_streak_persistence.py): across every player-season
     2020-21 to 2025-26, the share of a window's gap from baseline that
     showed up again in the next N games. Expected next N = baseline +
     intercept + slope x gap. That share is not zero when nothing carries
     on: with each season's games shuffled (no streaks) it is still 7-86%,
     because both gaps are measured against the same noisy baseline (N games
     also say something about his level). So the answer also gives the
     shuffled-null centre (`null_share`) and the share beyond it
     (`net_share` = share - null_share): how much of the run itself lasts
     (since round 8 step 7, R8-029; before, only the share was shown and
     read as "how much carries on").

Definitions (baseline, floors, stats) come from api/hot_streaks.py, the
same module the build script uses. Games come from player_game_lines joined
to team_game_fatigue (the Game Log's games; NBA Cup finals left out).

League list: every player whose window qualifies and who played within 14
days of the as-of date, ranked by how many standard deviations of his own
random N-game sets the window sits from his season rate. It also counts how
many would pass p < 0.05 by chance alone. When the as-of date is before the
season's end, it shows what the listed players actually did next.

Results are lru-cached per process: restart impact_api after rebuilding
player_game_lines or hot_streak_persistence.
"""

import math
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from current_season import default_season_for
from fastapi import APIRouter, HTTPException, Query

from hot_streaks import (LINES_SQL, MIN_BASE_GAMES, MIN_BASE_MPG, MIN_PRIOR_GAMES, PRIOR_SQL, STATS, WINDOWS,
                         add_columns, prior_share)
from impact_core import get_db
from source_badge import make_source

router = APIRouter()

DRAWS = 4000
RECENT_DAYS = 14   # league list: must have played within this many days of the as-of date
ALPHA = 0.05       # p below this = unusual for him, on the card and the league list alike
MINUS = "\u2212"   # the minus sign the pages print (utils/format.js)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _r(v, d=4):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)


@lru_cache(maxsize=1)
def _persistence():
    with get_db() as conn:
        df = pd.read_sql("SELECT * FROM hot_streak_persistence", conn)
    return {(r.stat, int(r.window_games)): r._asdict() for r in df.itertuples(index=False)}


@lru_cache(maxsize=1)
def _seasons():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT l.season, min(l.game_date), max(l.game_date) FROM player_game_lines l
                       JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                       GROUP BY 1 ORDER BY 1""")
        return {s: (a, b) for s, a, b in cur.fetchall()}


@lru_cache(maxsize=1)
def _names():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                       ORDER BY player_id, season DESC""")
        return dict(cur.fetchall())


@lru_cache(maxsize=8)
def _season_lines(season):
    with get_db() as conn:
        return add_columns(pd.read_sql(LINES_SQL.format(where="AND l.season = %(s)s"), conn, params={"s": season}))


@lru_cache(maxsize=8)
def _prior(season):
    """Previous-season totals per player and stat (player_season_stats, 20+ games)."""
    cols = ", ".join(f"{n} AS {k}_n, {d} AS {k}_d" for k, (n, d) in PRIOR_SQL.items())
    with get_db() as conn:
        df = pd.read_sql(f"SELECT player_id, gp, {cols} FROM player_season_stats WHERE season = %(s)s AND gp >= %(g)s",
                         conn, params={"s": season - 1, "g": MIN_PRIOR_GAMES})
    return {int(r["player_id"]): r for r in df.to_dict("records")}


def _check_inputs(season, stat, window):
    seasons = _seasons()
    if season is None:
        season = max(seasons)
    if season not in seasons:
        raise HTTPException(status_code=404, detail=(
            f"No game lines for {label(season)}: they cover {label(min(seasons))} to {label(max(seasons))}."))
    if stat not in STATS:
        raise HTTPException(status_code=400, detail=f"Unknown stat '{stat}'. Use one of {', '.join(STATS)}.")
    if window not in WINDOWS:
        raise HTTPException(status_code=400, detail=f"Window must be one of {', '.join(map(str, WINDOWS))} games.")
    return season


def _assess(g, prior_row, stat, window, rng):
    """One player's games this season up to the as-of date (oldest first) -> the check, or a reason it can't run."""
    _label, a, b, fmt, floor = STATS[stat]
    n = len(g)
    base_games = n - window
    if base_games < MIN_BASE_GAMES:
        return {"qualified": False, "reason": (
            f"Needs {MIN_BASE_GAMES} games before the {window}-game window to have a baseline; "
            f"he has {max(base_games, 0)} so far this season.")}
    num, den, mins = g[a].to_numpy(float), g[b].to_numpy(float), g["min"].to_numpy(float)
    bn, bd = num[:base_games].sum(), den[:base_games].sum()
    wn, wd = num[base_games:].sum(), den[base_games:].sum()
    if mins[:base_games].mean() < MIN_BASE_MPG:
        return {"qualified": False, "reason": (
            f"Averaged {mins[:base_games].mean():.1f} minutes before the window; the history this is measured on "
            f"only counts players averaging {MIN_BASE_MPG:.0f}+.")}
    if bd <= 0 or wd <= 0 or (floor is not None and (bd < floor * base_games or wd < floor * window)):
        return {"qualified": False, "reason": (
            f"Too few attempts: {STATS[stat][0]} needs {floor:g}+ a game both before and during the window."
            if floor is not None else "No attempts to measure.")}

    per = _persistence()[(stat, window)]
    pn = prior_row.get(f"{stat}_n") if prior_row else None
    pdn = prior_row.get(f"{stat}_d") if prior_row else None
    has_prior = pdn is not None and not math.isnan(pdn) and pdn > 0
    share = prior_share(per["prior_games"], prior_row["gp"]) if has_prior else 0.0
    baseline = (bn + share * pn) / (bd + share * pdn) if share else bn / bd
    rate = wn / wd
    gap = rate - baseline
    sfx = "" if has_prior else "_season_only"
    slope, lo, hi, icpt = per[f"slope{sfx}"], per[f"slope{sfx}_lo"], per[f"slope{sfx}_hi"], per[f"intercept{sfx}"]
    null, net, net_lo, net_hi = (per[f"null_slope{sfx}"], per[f"net_share{sfx}"], per[f"net_share{sfx}_lo"],
                                 per[f"net_share{sfx}_hi"])
    season_rate = num.sum() / den.sum()

    # How unusual: random sets of `window` games from his season so far.
    idx = rng.random((DRAWS, n)).argpartition(window - 1, axis=1)[:, :window]
    rand = num[idx].sum(axis=1) / np.where(den[idx].sum(axis=1) > 0, den[idx].sum(axis=1), np.nan)
    rand = rand[~np.isnan(rand)]
    hot = gap >= 0
    p = float(np.mean(rand >= rate - 1e-12) if hot else np.mean(rand <= rate + 1e-12))
    sd = float(rand.std())
    z = (rate - float(rand.mean())) / sd if sd > 0 else 0.0
    # Percentile among his actual rolling windows this season.
    cn, cd = np.concatenate([[0], np.cumsum(num)]), np.concatenate([[0], np.cumsum(den)])
    ends = np.arange(window, n + 1)
    wd_all = cd[ends] - cd[ends - window]
    rolling = np.where(wd_all > 0, (cn[ends] - cn[ends - window]) / np.where(wd_all > 0, wd_all, 1), np.nan)
    rolling = rolling[~np.isnan(rolling)]
    pct_rank = float(np.mean(rolling <= rate + 1e-12)) if len(rolling) else None

    return {
        "qualified": True, "direction": "hot" if hot else "cold",
        "window": {"games": window, "from": g["game_date"].iloc[base_games].isoformat(),
                   "to": g["game_date"].iloc[-1].isoformat(), "value": _r(rate), "sample": _r(wd, 1)},
        "baseline": {"value": _r(baseline), "games_this_season": base_games, "has_prior": bool(has_prior),
                     "prior_games_weight": per["prior_games"] if has_prior else 0,
                     "prior_season_games": int(prior_row["gp"]) if has_prior else None},
        "season_so_far": _r(season_rate),
        # A points or usage run often comes with a minutes change (a new role): shown beside it.
        "minutes": {"before": _r(mins[:base_games].mean(), 1), "window": _r(mins[base_games:].mean(), 1)},
        "gap": _r(gap),
        "unusual": {"p": _r(p, 4), "z": _r(z, 2), "draws": int(len(rand)), "random_mean": _r(float(rand.mean())),
                    "percentile_own_windows": _r(pct_rank, 3), "own_windows": int(len(rolling))},
        "persistence": {"share": _r(slope, 3), "share_lo": _r(lo, 3), "share_hi": _r(hi, 3), "intercept": _r(icpt, 4),
                        "null_share": _r(null, 3), "net_share": _r(net, 3), "net_share_lo": _r(net_lo, 3),
                        "net_share_hi": _r(net_hi, 3), "null_shuffles": int(per["null_shuffles"]),
                        "carry_on": _r(slope * gap), "carry_on_lo": _r(min(lo * gap, hi * gap)),
                        "carry_on_hi": _r(max(lo * gap, hi * gap)), "expected_next": _r(baseline + icpt + slope * gap),
                        "fitted_on": "every player-season window 2020-21 to 2025-26 "
                                     + ("with a previous season" if has_prior else "(season-only baseline)")},
    }


def _verdict(res, stat, window):
    if not res["qualified"]:
        return res["reason"]
    fmt = STATS[stat][3]
    show = (lambda v: f"{v * 100:.1f}%") if fmt == "pct" else (lambda v: f"{v:.1f}")
    u, per, word = res["unusual"], res["persistence"], res["direction"]
    head = (f"Within normal noise: {u['p'] * 100:.0f}% of random {window}-game sets from his season are this {word}."
            if u["p"] >= ALPHA else
            f"Unusual for him: only {u['p'] * 100:.1f}% of random {window}-game sets from his season are this {word}.")
    tail = (f" Expect about {show(per['expected_next'])} over his next {window} games (baseline "
            f"{show(res['baseline']['value'])}): about {per['share'] * 100:.0f}% of a gap like this shows up again, "
            f"but {per['null_share'] * 100:.0f}% would with his games shuffled (no streaks; {window} games also say "
            f"something about his level), so the run itself carries on {_pct(per['net_share'])}.")
    return head + tail


def _pct(v):
    """A share as a whole percent, with no sign on one that reads 0%."""
    p = round(v * 100)
    return f"{MINUS if p < 0 else ''}{abs(p)}%"


def _next_games(g_after, stat, window):
    _label, a, b, _fmt, _floor = STATS[stat]
    nxt = g_after.head(window)
    d = nxt[b].sum()
    if not len(nxt) or d <= 0:
        return None
    return {"games": int(len(nxt)), "value": _r(nxt[a].sum() / d)}


def _source():
    return make_source(["player_game_lines", "team_game_fatigue", "player_season_stats", "hot_streak_persistence"],
                       "ESPN play-by-play (lines rebuilt from it), nba_api (stats.nba.com)")


@router.get("/games/hot-streak/options")
def hot_streak_options():
    per = _persistence()
    seasons = _seasons()
    return {
        "stats": [{"key": k, "label": v[0], "format": v[3], "min_per_game": v[4]} for k, v in STATS.items()],
        "windows": list(WINDOWS),
        "seasons": [{"season": s, "first_date": a.isoformat(), "last_date": b.isoformat()} for s, (a, b) in seasons.items()],
        "rules": {"min_base_games": MIN_BASE_GAMES, "min_base_mpg": MIN_BASE_MPG, "min_prior_games": MIN_PRIOR_GAMES,
                  "draws": DRAWS, "recent_days": RECENT_DAYS, "alpha": ALPHA},
        "persistence": [{k: (_r(v, 4) if isinstance(v, float) else (v.isoformat() if isinstance(v, date) else v))
                         for k, v in row.items()} for row in per.values()],
        "_source": _source(),
    }


@router.get("/games/hot-streak/{player_id}")
def hot_streak(player_id: int, season: int | None = None, stat: str = "pts", window: int = 10,
               as_of: date | None = None):
    if season is None:  # this player's newest season with game lines (round 9 step 5), not the league's newest
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(season) FROM player_game_lines WHERE player_id = %s", (player_id,))
            season = cur.fetchone()[0]
    season = _check_inputs(season, stat, window)
    names = _names()
    if player_id not in names:
        raise HTTPException(status_code=404, detail=f"No NBA seasons on file for player id {player_id}.")
    lines = _season_lines(season)
    g = lines[lines.player_id == player_id]
    if g.empty:
        raise HTTPException(status_code=404, detail=f"No game lines for {names[player_id]} in {label(season)}.")
    cutoff = as_of or g.game_date.max()
    before, after = g[g.game_date <= cutoff], g[g.game_date > cutoff]
    rng = np.random.default_rng(player_id * 1000 + season)  # same answer on every reload
    res = _assess(before, _prior(season).get(player_id), stat, window, rng)
    if res["qualified"]:
        res["what_happened_next"] = _next_games(after, stat, window)
    return {
        "player_id": player_id, "player_name": names[player_id], "season": season, "stat": stat,
        "stat_label": STATS[stat][0], "format": STATS[stat][3], "window": window,
        "as_of": (before.game_date.max().isoformat() if len(before) else cutoff.isoformat()),
        "games_so_far": int(len(before)), "games_after": int(len(after)),
        **res, "verdict": _verdict(res, stat, window), "_source": _source(),
    }


@lru_cache(maxsize=64)
def _league(season, stat, window, as_of):
    lines = _season_lines(season)
    prior = _prior(season)
    rng = np.random.default_rng(season * 100 + window)
    out, skipped = [], 0
    upto = lines[lines.game_date <= as_of]
    # Each player's games after the as-of date, split once (round 8 step 8: a mask over the whole season per
    # player before; the same rows in the same order).
    later = lines[lines.game_date > as_of]
    later_by = dict(tuple(later.groupby("player_id", sort=False)))
    for pid, g in upto.groupby("player_id", sort=False):
        if g.game_date.iloc[-1] < as_of - timedelta(days=RECENT_DAYS):
            continue
        res = _assess(g, prior.get(int(pid)), stat, window, rng)
        if not res["qualified"]:
            skipped += 1
            continue
        after = later_by.get(pid, later.iloc[0:0])
        res["what_happened_next"] = _next_games(after, stat, window)
        res["player_id"] = int(pid)
        out.append(res)
    return out, skipped


@router.get("/games/hot-streaks")
def hot_streaks(season: int | None = None, stat: str = "pts", window: int = 10, as_of: date | None = None,
                direction: str = Query("hot", pattern="^(hot|cold)$"), limit: int = Query(25, ge=5, le=100)):
    if season is None:
        # nobody can be tested before window + MIN_BASE_GAMES games: the newest complete season until the season
        # being played has them (round 9 step 5)
        season = default_season_for(window + MIN_BASE_GAMES)
    season = _check_inputs(season, stat, window)
    first, last = _seasons()[season]
    as_of = min(max(as_of or last, first), last)
    rows, skipped = _league(season, stat, window, as_of)
    names = _names()
    tested = len(rows)
    sign = 1 if direction == "hot" else -1
    same_way = [r for r in rows if (r["gap"] or 0) * sign > 0]
    ranked = sorted(same_way, key=lambda r: -sign * (r["unusual"]["z"] or 0))[:limit]
    significant = sum(1 for r in same_way if r["unusual"]["p"] < ALPHA)
    per = _persistence()[(stat, window)]
    # What the listed players did next (only when the as-of date leaves games to look at).
    followed = [r for r in ranked if r.get("what_happened_next") and r["what_happened_next"]["games"] == window]
    next_summary = None
    if followed:
        gaps = np.array([r["gap"] for r in followed])
        after = np.array([r["what_happened_next"]["value"] - r["baseline"]["value"] for r in followed])
        expected = np.array([r["persistence"]["expected_next"] - r["baseline"]["value"] for r in followed])
        next_summary = {"players": len(followed), "mean_gap": _r(float(gaps.mean())),
                        "mean_next_vs_baseline": _r(float(after.mean())),
                        "mean_expected_vs_baseline": _r(float(expected.mean())),
                        "share_carried": _r(float(after.mean() / gaps.mean()), 3) if gaps.mean() else None}
    for r in ranked:
        r["player_name"] = names.get(r["player_id"])
    return {
        "season": season, "stat": stat, "stat_label": STATS[stat][0], "format": STATS[stat][3], "window": window,
        "as_of": as_of.isoformat(), "season_dates": {"first": first.isoformat(), "last": last.isoformat()},
        "direction": direction,
        "summary": {
            "tested": tested, "skipped": skipped, "same_direction": len(same_way),
            "significant": significant, "expected_by_chance": _r(ALPHA * tested, 1),
            "alpha": ALPHA, "share_carries_on": per["slope"], "share_lo": per["slope_lo"], "share_hi": per["slope_hi"],
            # With a previous season (most of the list); the shuffled-null centre and the share beyond it.
            "null_share": per["null_slope"], "net_share": per["net_share"], "net_share_lo": per["net_share_lo"],
            "net_share_hi": per["net_share_hi"],
        },
        "next_games": next_summary,
        "results": ranked, "_source": _source(),
    }
