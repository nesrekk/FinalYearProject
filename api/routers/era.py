"""
Era Translator: restate a player-season in another season's environment.

    GET /era/players?q=wilt                         — player search (local DB, accent-blind)
    GET /era/translate?player_id=76375&season=1962&target=2026

Two restatements, both simple ratios from Basketball-Reference's league
averages (league_season_averages, scripts/build_league_averages.py):

  * Pace only: every per-game count x (target pace / source pace). The
    player keeps the same production per possession; percentages don't move.
  * Pace + league: every per-game count x (target league average per team
    game / source league average) for that stat, i.e. the player keeps the
    same share of what an average team produced (for points that folds in
    both pace and scoring efficiency). Percentages move by the change in the
    league average (player - source league + target league). Threes stay
    pace-only: league volume grew more than tenfold after 1979-80, so a
    share-of-league restatement would describe the league, not the player.

Plus the era-free comparison the builders use: the player's z-score,
percentile and rank within the player's own season among qualified players (30+
games and 20+ minutes a game, minutes only where recorded; shooting % also
needs the builders' attempts floor).

League pace is used, not the player's team's, so a player on a fast team is
slightly over-shrunk (and on a slow team, under-shrunk).
"""

import unicodedata
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from routers.leaderboard import ATTEMPT_DEFAULTS, STATS
from source_badge import make_source
from season_team import season_team_sql

router = APIRouter()

MIN_GP, MIN_MPG = 30, 20.0

# key -> (label, format, kind). kind: 'count' (per game) | 'threes' | 'pct'
ROWS = {
    "pts": ("Points", "num1", "count"),
    "reb": ("Rebounds", "num1", "count"),
    "ast": ("Assists", "num1", "count"),
    "stl": ("Steals", "num1", "count"),
    "blk": ("Blocks", "num1", "count"),
    "tov": ("Turnovers", "num1", "count"),
    "fta": ("Free-throw attempts", "num1", "count"),
    "fg3m": ("3-pointers made", "num1", "threes"),
    "fg_pct": ("Field-goal %", "pct", "pct"),
    "fg3_pct": ("3-point %", "pct", "pct"),
    "ft_pct": ("Free-throw %", "pct", "pct"),
    "ts_pct": ("True shooting %", "pct", "pct"),
}
THREES = ("fg3m", "fg3_pct")  # the line arrived in 1979-80
LEAGUE_COLS = ["season", "pace", "pace_source", "ortg", *ROWS]
PACE_NOTES = {
    "bref": "Basketball-Reference",
    "bref_estimate": "Basketball-Reference's estimate (offensive rebounds and turnovers not recorded yet)",
    "estimated_here": "estimated by this app from shots and free throws (no published pace)",
}
SOURCE = make_source(["player_season_stats", "league_season_averages"],
                     "nba_api (stats.nba.com) + Basketball-Reference league averages")


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


@lru_cache(maxsize=1)
def _players():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT player_id, player_name, MIN(season), MAX(season), COUNT(*)
                       FROM player_season_stats GROUP BY player_id, player_name;""")
        rows = cur.fetchall()
    # One entry per id (a renamed player keeps the name of the latest season).
    by_id = {}
    for pid, name, first, last, n in rows:
        cur_ = by_id.get(pid)
        if cur_ is None or last > cur_["to"]:
            by_id[pid] = {"player_id": int(pid), "player_name": name,
                          "from": min(first, cur_["from"]) if cur_ else first, "to": last,
                          "seasons": n + (cur_["seasons"] if cur_ else 0)}
        else:
            cur_["from"] = min(cur_["from"], first)
            cur_["seasons"] += n
    return [(_fold(p["player_name"]), p) for p in by_id.values()]


@lru_cache(maxsize=1)
def _league():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(LEAGUE_COLS)} FROM league_season_averages ORDER BY season;")
        return {r[0]: dict(zip(LEAGUE_COLS, r)) for r in cur.fetchall()}


@router.get("/era/players")
def era_players(q: str, limit: int = Query(12, ge=1, le=30)):
    query = _fold(q).strip()
    if len(query) < 2:
        return {"query": q, "results": []}
    hits = [p for folded, p in _players() if query in folded]
    # Names that start with the query first, then the longest careers.
    hits.sort(key=lambda p: (not _fold(p["player_name"]).startswith(query), -p["seasons"], p["player_name"]))
    return {"query": q, "results": hits[:limit]}


def _standing(cur, key, season, player_id, value):
    """z, percentile and rank of `value` among the season's qualified players."""
    _l, _g, _f, _first, higher_is_better, att = STATS[key]
    where = [f"{key} IS NOT NULL", "season = %s", "gp >= %s", "(min IS NULL OR min >= %s)", "player_id <> %s"]
    params = [season, MIN_GP, MIN_MPG, player_id]
    if att:
        where.append(f"{att} >= %s")
        params.append(ATTEMPT_DEFAULTS[att])
    cur.execute(f"SELECT {key} FROM player_season_stats WHERE {' AND '.join(where)};", params)
    others = np.array([float(r[0]) for r in cur.fetchall()])
    if len(others) < 10:
        return None
    pool = np.append(others, value)
    sd = pool.std(ddof=0)
    z = 0.0 if sd == 0 else (value - pool.mean()) / sd
    sign = 1 if higher_is_better else -1
    better = int((sign * others > sign * value).sum())
    worse = int((sign * others < sign * value).sum())
    ties = len(others) - better - worse
    return {
        "z": round(float(sign * z), 2),
        "percentile": round(100 * (worse + 0.5 * ties) / len(others), 1),
        "rank": better + 1,
        "pool": len(others) + 1,
    }


@router.get("/era/translate")
def era_translate(player_id: int, season: int | None = None, target: int | None = None):
    league = _league()
    with get_db() as conn:
        cur = conn.cursor()
        team_sql = season_team_sql(cur)  # not a team he never played for that season (season_team.py)
        cur.execute(f"SELECT season, {team_sql}, gp FROM player_season_stats "
                    "WHERE player_id = %s ORDER BY season;", (player_id,))
        seasons = [{"season": s, "team": t, "gp": g} for s, t, g in cur.fetchall()]
        if not seasons:
            raise HTTPException(status_code=404, detail="No seasons on file for that player.")
        if season is None:
            season = seasons[-1]["season"]
        if season not in {s["season"] for s in seasons}:
            raise HTTPException(status_code=404, detail=f"No {_label(season)} season on file for that player.")
        cur.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
        first_on_file, last_on_file = cur.fetchone()
        # a target needs its league averages (league_season_averages, built at a season's end): the season being
        # played isn't one until then (round 9 step 5)
        last_on_file = max(s for s in league if s <= last_on_file)
        target = last_on_file if target is None else target
        if target not in league or not first_on_file <= target <= last_on_file:
            raise HTTPException(status_code=400, detail=(
                f"Target season must be between {_label(first_on_file)} and {_label(last_on_file)}."))

        cols = ["player_name", "team_abbreviation", "age", "gp", "min", "fga", "fg3a", *ROWS]
        cur.execute(f"SELECT {', '.join(team_sql if c == 'team_abbreviation' else c for c in cols)} "
                    "FROM player_season_stats WHERE player_id = %s AND season = %s;",
                    (player_id, season))
        p = dict(zip(cols, cur.fetchone()))

        src, tgt = league[season], league[target]
        pace_factor = tgt["pace"] / src["pace"]
        rows = []
        for key, (label, fmt, kind) in ROWS.items():
            v = p[key]
            v = None if v is None else float(v)
            row = {"key": key, "label": label, "format": fmt, "kind": kind, "original": v,
                   "pace_adjusted": None, "league_adjusted": None, "league_factor": None,
                   "league_source": src[key], "league_target": tgt[key], "standing": None, "standing_note": None,
                   "note": None}
            if v is None:
                row["note"] = (f"No three-point line in {_label(season)}." if key in THREES and season < 1980
                               else f"Not recorded in {_label(season)}.")
            elif tgt[key] is None:
                row["note"] = (f"No three-point line in {_label(target)}." if key in THREES
                               else f"Not recorded in {_label(target)}.")
            else:
                if kind == "pct":
                    row["pace_adjusted"] = v
                    if src[key] is not None:
                        row["league_adjusted"] = min(1.0, max(0.0, v - src[key] + tgt[key]))
                else:
                    row["pace_adjusted"] = v * pace_factor
                    if kind == "count" and src[key]:
                        row["league_factor"] = tgt[key] / src[key]
                        row["league_adjusted"] = v * row["league_factor"]
                    elif kind == "threes":
                        row["note"] = "Pace only: league three-point volume grew more than tenfold after 1979-80."
            if v is not None:
                att = STATS[key][5]
                if att and (p[att] or 0) < ATTEMPT_DEFAULTS[att]:
                    row["standing_note"] = (f"Under {ATTEMPT_DEFAULTS[att]:g} {att.upper()} a game, "
                                            f"so not ranked.")
                else:
                    row["standing"] = _standing(cur, key, season, player_id, v)
            for k in ("pace_adjusted", "league_adjusted", "league_factor"):
                if row[k] is not None:
                    row[k] = round(row[k], 4)
            rows.append(row)

    gp, mpg = p["gp"], p["min"]
    notes = []
    small = gp < MIN_GP or (mpg is not None and mpg < MIN_MPG)
    if small:
        notes.append(f"Small sample: {gp} games" + (f", {mpg:.1f} minutes a game" if mpg is not None else "") +
                     f". Standing is measured against players with {MIN_GP}+ games and {MIN_MPG:g}+ minutes.")
    if mpg is None:
        notes.append(f"Minutes weren't recorded in {_label(season)}, so the qualified pool uses games only.")
    for s, lg in ((season, src), (target, tgt)):
        if lg["pace_source"] != "bref":
            notes.append(f"{_label(s)} pace ({lg['pace']:.1f}) is {PACE_NOTES[lg['pace_source']]}.")
    if p["team_abbreviation"] and p["team_abbreviation"].endswith("TM"):
        notes.append("Played for more than one team that season; the line is the combined one.")

    return {
        "player": {"player_id": player_id, "player_name": p["player_name"], "team": p["team_abbreviation"],
                   "age": p["age"], "gp": gp, "min": None if mpg is None else round(float(mpg), 1),
                   "small_sample": small},
        "season": season,
        "target": target,
        "seasons": seasons,
        "targets": {"from": max(first_on_file, min(league)), "to": last_on_file},
        "environment": {
            "source": {"season": season, "pace": src["pace"], "pace_source": src["pace_source"],
                       "pts": src["pts"], "ortg": src["ortg"], "ts_pct": src["ts_pct"]},
            "target": {"season": target, "pace": tgt["pace"], "pace_source": tgt["pace_source"],
                       "pts": tgt["pts"], "ortg": tgt["ortg"], "ts_pct": tgt["ts_pct"]},
            "pace_factor": round(pace_factor, 4),
        },
        "rows": rows,
        "league_series": [{"season": s, "pace": lg["pace"], "pace_source": lg["pace_source"], "pts": lg["pts"]}
                          for s, lg in league.items() if first_on_file <= s <= last_on_file],
        "notes": notes,
        "qualified": {"min_gp": MIN_GP, "min_mpg": MIN_MPG},
        "method": (
            "Pace only: each per-game count x (target pace / source pace), so production per possession stays "
            "the same; percentages don't change. Pace + league: each per-game count x (target league average "
            "per team game / source league average) for that stat, so the player keeps the same share of an "
            "average team's output; for points that covers pace and scoring efficiency together. Percentages "
            "move by the change in the league average. Threes are pace-only. Pace is the league's, not the "
            "team's, and minutes are not changed. Standing: z-score and percentile within the player's own "
            f"season among players with {MIN_GP}+ games and {MIN_MPG:g}+ minutes a game (games only where "
            "minutes weren't recorded); shooting % also needs the Leaderboard Builder's attempts floor."
        ),
        "_source": SOURCE,
    }
