"""
Next-season projections: a Marcel-style baseline for every current player
(scripts/build_projections.py writes the tables).

    GET /projections?stat=pts                — every current player's projection
                                               for one stat, with the backtest
                                               for that stat and the catalogue
    GET /projections/backtest                — the backtest for every stat
    GET /projections/player/{player_id}      — one player, every stat

A projection is a weighted average of the last three seasons, regressed
toward the league average by an amount set from Stat Stability, plus an
age adjustment from the aging curves. The 80% range is the spread of the
same method's past errors for players in the same reliability and level
bin. It is a baseline, not a scouting opinion.

Projected vs actual (round 9 step 4): once the projected season has rows in
player_season_stats (the live season's, refreshed daily), every row carries
what the player has done so far (`actual`, in the stat's own units from the
season-to-date line, with its sample), the gap (actual minus projection) and
whether it sits inside the 80% range, plus a summary over the players with
an actual. The projections themselves are never refit in-season (the stored
2026-27 rows are the ones made before it); BPM has no in-season actual
(Basketball-Reference's values arrive at the season's end).
"""

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source
from stat_samples import SEASON_SAMPLE_SQL

router = APIRouter()

SOURCE = make_source(["player_projections", "projection_backtest", "projection_ranges", "projection_stats",
                      "player_season_stats", "stat_stability", "aging_curves", "player_bio"],
                     "nba_api (stats.nba.com) + Basketball-Reference (seasons, BPM, birth dates)")
CAT_COLS = ["stat", "label", "stat_group", "kind", "source_col", "sample", "aging_key", "first_season",
            "higher_is_better", "format", "shrink_m", "shrink_source", "y2y_r", "level_cut1", "level_cut2",
            "backtest_from", "backtest_to"]
BT_COLS = ["stat", "season", "n", "mae", "mae_last", "mae_league", "mae_average", "mae_no_age", "bias", "slope", "r",
           "coverage80"]
ROW_COLS = ["player_id", "player_name", "team", "season", "stat", "projection", "lo", "hi", "own_weight", "sample",
            "seasons_used", "last_season", "last_value", "last_sample", "league_mean", "age_adjustment", "age_known",
            "age_next", "k_bin", "level_bin"]
SAMPLE_LABELS = {"minutes": "minutes", "games": "games", "fga": "field-goal attempts", "fg3a": "3-point attempts",
                 "fta": "free-throw attempts", "tsa": "shooting attempts (FGA + 0.44 × FTA)",
                 "usg_pct": "team plays while on the floor", "ast_pct": "teammate field goals while on the floor",
                 "reb_pct": "rebound chances", "oreb_pct": "offensive rebound chances",
                 "tov_pct": "own plays (FGA + 0.44 × FTA + AST + TOV)"}
K_BIN_LABELS = ["under 0.5", "0.5 to 0.8", "0.8 and up"]
LOW_WEIGHT = 0.5   # below this the projection is mostly the league average
MIN_PROJ_MINUTES = 250  # build_projections.MIN_PROJ_MINUTES: fewer over three seasons, no projection
NOT_PROJECTED = [
    ("Offensive, defensive and net rating, plus-minus", "on-court team results, mostly teammates and opponents"),
    ("VORP", "needs games and possessions, which aren't projected"),
    ("Impact score", "an in-house composite whose year-to-year correlation is 0.49"),
    ("Games played", "injuries and rest aren't predictable from a stat line"),
]
PROFILE_STATS = ["min", "pts", "reb", "ast", "stl", "blk", "fg3m", "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "usg_pct",
                 "bpm"]
METHOD = (
    "Weighted average of the player's last three seasons (weights 5/4/3, most recent first, each season also "
    "weighted by its sample), regressed toward the league average of those seasons: the weight on his own numbers "
    "is N / (N + M), N his weighted sample and M the sample at which the stat is half signal (Stat Stability's "
    "split-half number; for minutes a game and BPM, where real year-to-year change matters more than in-season "
    "noise, M comes from the year-to-year correlation instead). Then an age adjustment: the aging curve's level at "
    "his age next season minus its level at each past season's age; minutes a game instead get the average miss of "
    "the regressed minutes projection for players of that age and minutes level (bench, rotation, starter), since "
    "the population curve for minutes is mostly players gaining and losing a role. Per-game counts are the projected "
    "per-36 rate times projected minutes. Ages are taken from birth dates (age on February 1) in every season. The 80% range "
    "is the 10th to 90th percentile of the same method's past errors (2000-01 on) for players in the same "
    "reliability and level bin, checked leave-one-season-out."
)
CAVEATS = [
    "A baseline, not a scouting opinion: it knows nothing about role changes, trades, injuries, a new coach or a "
    "player's summer. A one-season player's projection leans on the league average and carries a wide range; a "
    "player with under 250 minutes over the last three seasons, or no NBA season on file, gets none.",
    "The exceptions it can't see: the few players still scoring 22+ a game at 34 or older (31 in the backtest) beat "
    "their projection by 3 points a game on average, because the aging curve describes the typical player and they "
    "aren't one.",
    "The backtest scores only players who then played 500+ minutes (shooting % also 100 FGA / 50 3PA / 50 FTA), so "
    "it says how the method does for players who kept playing, not for those who didn't.",
    "Regression is toward the league average of all players, weighted by playing time, so the bias per stat in the "
    "backtest (actual minus projection) is real: e.g. BPM projections run a little high, because bench players "
    "sit below the minutes-weighted average they're pulled toward. The ranges include that bias.",
    "Three-point volume keeps rising league-wide, and a projection regressed toward past league averages lags it: "
    "'same as last season' is slightly better for 3-point attempts and makes.",
    "Two inputs were estimated on all seasons and not re-fit per backtest season: the M values (measured on "
    "2020-21 to 2025-26 play-by-play) and the aging curves. Both are a handful of numbers, not fit to any one season.",
    "Rebound %, offensive rebound %, turnover %, usage % and assist % change definition in 2009-10, so a projection "
    "never mixes seasons from both sides for them.",
]


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _actual_sql(stat, c):
    """(value SQL, sample SQL) of a stat's season-to-date value in player_season_stats' per-game line, in the units the
    projection uses (scripts/build_projections.stat_values); None for BPM, which has no in-season value."""
    kind, col = c["kind"], c["source_col"]
    if kind == "bpm":
        return None, None
    if kind in ("per36",):
        return f"{col} * 36.0 / NULLIF(min, 0)", "min * gp"
    if kind == "per_game":
        return col, "gp"
    if kind == "mpg":
        return "min", "gp"
    return col, SEASON_SAMPLE_SQL.get(stat, "gp")      # pct and rate stats: the value as published, the Leaderboard's sample


@lru_cache(maxsize=8)
def _actuals(season, stat):
    """{player_id: (actual, sample)} for the projected season's rows on file, and the season's last game date;
    ({}, None) when the season has no rows yet."""
    cat, _, _ = _tables()
    c = cat.get(stat)
    if c is None:
        return {}, None
    val, samp = _actual_sql(stat, c)
    if val is None:
        return {}, None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT player_id, ({val})::float, ({samp})::float FROM player_season_stats WHERE season = %s AND gp > 0", (season,))
        rows = {int(p): (v, n) for p, v, n in cur.fetchall() if v is not None}
        cur.execute("SELECT to_regclass('game_scores')")
        through = None
        if cur.fetchone()[0] is not None:
            cur.execute("SELECT max(game_date) FROM game_scores WHERE season = %s", (season,))
            through = cur.fetchone()[0]
    return rows, through.isoformat() if through else None


def _with_actuals(rows, season, stat):
    """Adds actual / actual_sample / gap / in_range to projection rows; returns the summary (None without rows on file)."""
    act, through = _actuals(season, stat)
    if not act:
        for x in rows:
            x.update(actual=None, actual_sample=None, gap=None, in_range=None)
        return None
    gaps, inside, n = [], 0, 0
    for x in rows:
        a = act.get(x["player_id"])
        if a is None:
            x.update(actual=None, actual_sample=None, gap=None, in_range=None)
            continue
        v, smp = a
        x["actual"], x["actual_sample"] = v, smp
        x["gap"] = v - x["projection"] if x["projection"] is not None else None
        x["in_range"] = (x["lo"] <= v <= x["hi"]) if x["lo"] is not None and x["hi"] is not None else None
        n += 1
        if x["gap"] is not None:
            gaps.append(x["gap"])
        inside += bool(x["in_range"])
    gaps.sort()
    med = gaps[len(gaps) // 2] if gaps else None
    return {"season": season, "through": through, "players": n, "mean_gap": sum(gaps) / len(gaps) if gaps else None,
            "median_abs_gap": sorted(abs(g) for g in gaps)[len(gaps) // 2] if gaps else None, "median_gap": med,
            "share_in_range": inside / n if n else None,
            "note": (f"Season to date, through {through}: each player's {_label(season)} line so far against the projection "
                     "made before the season (never refit). Early in a season the lines are noisy and the share inside the "
                     "80% range says little; it should settle toward 80% as the season fills in.") if through else None}


@lru_cache(maxsize=1)
def _tables():
    """Catalogue and backtest rows, cached per process (restart after a rebuild)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('player_projections');")
        if cur.fetchone()[0] is None:
            return None, None, None
        cur.execute(f"SELECT {', '.join(CAT_COLS)} FROM projection_stats;")
        cat = {r[0]: dict(zip(CAT_COLS, r)) for r in cur.fetchall()}
        cur.execute(f"SELECT {', '.join(BT_COLS)} FROM projection_backtest ORDER BY stat, season;")
        bt = {}
        for r in cur.fetchall():
            row = dict(zip(BT_COLS, r))
            bt.setdefault(row["stat"], []).append(row)
        cur.execute("SELECT MAX(season), COUNT(DISTINCT player_id) FROM player_projections;")
        season, players = cur.fetchone()
    order = ["pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min",
             "pts36", "reb36", "ast36", "stl36", "blk36", "tov36", "fg3m36", "fg3a36", "fta36", "oreb36",
             "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct",
             "tov_pct", "bpm", "obpm", "dbpm"]
    cat = dict(sorted(cat.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
    return cat, bt, {"season": season, "players": players}


def _catalogue(cat, bt):
    out = []
    for k, c in cat.items():
        pooled = next((r for r in bt.get(k, []) if r["season"] == 0), None)
        out.append({
            "key": k, "label": c["label"], "group": c["stat_group"], "kind": c["kind"], "format": c["format"],
            "higher_is_better": c["higher_is_better"], "sample_label": SAMPLE_LABELS.get(c["sample"], c["sample"]),
            "shrink_m": c["shrink_m"], "shrink_source": c["shrink_source"], "y2y_r": c["y2y_r"],
            "first_season": c["first_season"], "backtest": pooled,
        })
    return out


def _need():
    cat, bt, meta = _tables()
    if cat is None:
        raise HTTPException(status_code=503, detail="Projections haven't been built (scripts/build_projections.py).")
    return cat, bt, meta


@router.get("/projections")
def projections(stat: str = "pts"):
    cat, bt, meta = _need()
    if stat not in cat:
        raise HTTPException(status_code=400, detail=f"Unknown stat '{stat}'. See the catalogue in this response.")
    c = cat[stat]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM player_projections WHERE stat = %s "
                    f"ORDER BY projection {'DESC' if c['higher_is_better'] else 'ASC'}, player_name;", (stat,))
        rows = []
        for r in cur.fetchall():
            x = dict(zip(ROW_COLS, r))
            x["change"] = None if x["last_value"] is None else x["projection"] - x["last_value"]
            x["low_weight"] = x["own_weight"] < LOW_WEIGHT
            x["k_bin_label"] = K_BIN_LABELS[x["k_bin"]]
            rows.append(x)
    actual_summary = _with_actuals(rows, meta["season"], stat)
    return {
        "stat": stat,
        "season": meta["season"],
        "latest_season": meta["season"] - 1,
        "players": meta["players"],
        "actual_summary": actual_summary,
        "stat_info": next(s for s in _catalogue(cat, bt) if s["key"] == stat),
        "rows": rows,
        "backtest": {
            "all": next((r for r in bt.get(stat, []) if r["season"] == 0), None),
            "by_season": [r for r in bt.get(stat, []) if r["season"] != 0],
        },
        "catalogue": _catalogue(cat, bt),
        "low_weight": LOW_WEIGHT,
        "not_projected": [{"what": w, "why": y} for w, y in NOT_PROJECTED],
        "method": METHOD,
        "caveats": CAVEATS,
        "_source": SOURCE,
    }


@router.get("/projections/backtest")
def projections_backtest():
    cat, bt, meta = _need()
    return {
        "season": meta["season"],
        "stats": [{**s, "by_season": [r for r in bt.get(s["key"], []) if r["season"] != 0]}
                  for s in _catalogue(cat, bt)],
        "eval_rule": "players with 500+ minutes in the target season (shooting %: 100 FGA / 50 3PA / 50 FTA)",
        "method": METHOD,
        "caveats": CAVEATS,
        "_source": SOURCE,
    }


@router.get("/projections/player/{player_id}")
def projections_player(player_id: int):
    cat, bt, meta = _need()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM player_projections WHERE player_id = %s;", (player_id,))
        by_stat = {r[4]: dict(zip(ROW_COLS, r)) for r in cur.fetchall()}
        if not by_stat:
            latest = meta["season"] - 1
            cur.execute("""SELECT MAX(player_name), MAX(season), COALESCE(SUM(gp * min) FILTER (WHERE season >= %s), 0)
                           FROM player_season_stats WHERE player_id = %s;""", (latest - 2, player_id))
            p = cur.fetchone()
            if p[0] is None:
                raise HTTPException(status_code=404, detail="No seasons on file for that player.")
            if p[1] < latest:
                reason = f"Projections are made for players with a {_label(latest)} season; his last is {_label(p[1])}."
            else:
                reason = (f"No projection: {int(round(float(p[2]))):,} minutes over the last three seasons, under the "
                          f"{MIN_PROJ_MINUTES}-minute floor (regressing so few minutes toward the league average "
                          f"would invent a role).")
            return {"player": {"player_id": player_id, "player_name": p[0], "last_season": p[1]},
                    "season": meta["season"], "rows": [], "reason": reason, "_source": SOURCE}
    any_row = next(iter(by_stat.values()))
    rows = []
    through = None
    for k, c in cat.items():
        x = by_stat.get(k)
        if x is None:
            continue
        row = {"stat": k, "label": c["label"], "group": c["stat_group"], "format": c["format"],
               "higher_is_better": c["higher_is_better"], "projection": x["projection"], "lo": x["lo"],
               "hi": x["hi"], "own_weight": x["own_weight"], "low_weight": x["own_weight"] < LOW_WEIGHT,
               "sample": x["sample"], "sample_label": SAMPLE_LABELS.get(c["sample"], c["sample"]),
               "seasons_used": x["seasons_used"], "last_season": x["last_season"],
               "last_value": x["last_value"], "league_mean": x["league_mean"],
               "age_adjustment": x["age_adjustment"], "profile": k in PROFILE_STATS, "player_id": player_id}
        _with_actuals([row], meta["season"], k)
        act, thr = _actuals(meta["season"], k)
        through = through or thr
        rows.append(row)
    return {
        "player": {"player_id": player_id, "player_name": any_row["player_name"], "team": any_row["team"],
                   "age_next": any_row["age_next"], "age_known": any_row["age_known"],
                   "seasons_used": any_row["seasons_used"]},
        "season": meta["season"],
        "rows": rows,
        "actual_through": through,
        "low_weight": LOW_WEIGHT,
        "method": METHOD,
        "_source": SOURCE,
    }
