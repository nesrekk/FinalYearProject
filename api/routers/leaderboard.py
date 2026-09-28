"""
Custom leaderboards: rank player-seasons by any stat in player_season_stats,
with the filters an analyst needs to keep small samples out.

    GET /leaderboard/options    — the stat catalogue, season range, team codes
    GET /leaderboard/custom     — the ranked player-seasons
    GET /leaderboard/stability  — how big a sample each stat needs (stat_stability,
                                  built by scripts/build_stat_stability.py)

Every stat is per game unless it's a rate. Each stat has the first season it
is recorded for at least 90% of player-seasons (checked in the data: steals
and blocks start in 1973-74, threes in 1979-80, net rating/plus-minus/impact
in 2009-10); ranges are clipped to it rather than ranking a half-empty pool.
Shooting percentages need a minimum number of attempts per game, so a
1-for-1 season can't top 3P%.
"""

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from source_badge import make_source
from stat_samples import SEASON_SAMPLE_SQL

router = APIRouter()

# key -> (label, group, format, first_season, higher_is_better, attempts_column)
STATS = {
    "pts": ("Points", "Per game", "num1", 1950, True, None),
    "reb": ("Rebounds", "Per game", "num1", 1951, True, None),
    "ast": ("Assists", "Per game", "num1", 1950, True, None),
    "stl": ("Steals", "Per game", "num1", 1974, True, None),
    "blk": ("Blocks", "Per game", "num1", 1974, True, None),
    "tov": ("Turnovers", "Per game", "num1", 1978, False, None),
    "fg3m": ("3-pointers made", "Per game", "num1", 1980, True, None),
    "fg3a": ("3-point attempts", "Per game", "num1", 1980, True, None),
    "fta": ("Free-throw attempts", "Per game", "num1", 1950, True, None),
    "oreb": ("Offensive rebounds", "Per game", "num1", 1974, True, None),
    "min": ("Minutes", "Per game", "num1", 1952, True, None),
    "fg_pct": ("Field-goal %", "Shooting", "pct", 1950, True, "fga"),
    "fg3_pct": ("3-point %", "Shooting", "pct", 1980, True, "fg3a"),
    "ft_pct": ("Free-throw %", "Shooting", "pct", 1950, True, "fta"),
    "ts_pct": ("True shooting %", "Shooting", "pct", 1950, True, "fga"),
    "efg_pct": ("Effective FG %", "Shooting", "pct", 1980, True, "fga"),
    "usg_pct": ("Usage %", "Rates", "pct", 1978, True, None),
    "ast_pct": ("Assist %", "Rates", "pct", 1965, True, None),
    "reb_pct": ("Rebound %", "Rates", "pct", 1971, True, None),
    "oreb_pct": ("Offensive rebound %", "Rates", "pct", 1974, True, None),
    "tov_pct": ("Turnover %", "Rates", "pct", 1978, False, None),
    "off_rating": ("Offensive rating", "Impact", "num1", 2010, True, None),
    "def_rating": ("Defensive rating", "Impact", "num1", 2010, False, None),
    "net_rating": ("Net rating", "Impact", "signed1", 2010, True, None),
    "plus_minus": ("Plus-minus", "Impact", "signed1", 2010, True, None),
    "bpm": ("BPM", "Impact", "signed1", 1974, True, None),
    "obpm": ("Offensive BPM", "Impact", "signed1", 1974, True, None),
    "dbpm": ("Defensive BPM", "Impact", "signed1", 1974, True, None),
    "vorp": ("VORP", "Impact", "num1", 1974, True, None),
    "impact_score_raw": ("Impact score (raw)", "Impact", "num2", 2010, True, None),
    "age": ("Age", "Other", "int", 1950, True, None),
}

# Default minimum attempts per game for shooting percentages (overridable,
# including to 0): without one, 2025-26's top 3P% was 100% on 0.0 3PA a game.
ATTEMPT_DEFAULTS = {"fga": 5.0, "fg3a": 2.0, "fta": 2.0}

# Shown on every row for context, alongside the ranked stat.
CONTEXT = ["gp", "min", "pts", "reb", "ast", "ts_pct"]


def _bounds(cursor):
    cursor.execute("SELECT MIN(season), MAX(season) FROM player_season_stats;")
    return cursor.fetchone()


@router.get("/leaderboard/options")
def leaderboard_options():
    with get_db() as conn:
        cur = conn.cursor()
        first, last = _bounds(cur)
        cur.execute("""SELECT team_abbreviation, MIN(season), MAX(season), COUNT(*)
                       FROM player_season_stats WHERE team_abbreviation IS NOT NULL
                       GROUP BY 1 ORDER BY 1;""")
        teams = [{"team": t, "from": a, "to": b, "rows": n} for t, a, b, n in cur.fetchall()]
    return {
        "seasons": {"from": first, "to": last},
        "stats": [
            {"key": k, "label": v[0], "group": v[1], "format": v[2], "first_season": v[3],
             "higher_is_better": v[4], "attempts": v[5],
             "default_min_attempts": ATTEMPT_DEFAULTS.get(v[5])}
            for k, v in STATS.items()
        ],
        "teams": teams,
        "notes": [
            "Traded players are listed under their last team from 2009-10 on, and as 2TM/3TM before that.",
            "Team codes follow the franchise's name at the time (e.g. SEA, NJN, PHO).",
        ],
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }


@router.get("/leaderboard/custom")
def custom_leaderboard(
    stat: str = "pts",
    season_from: int | None = None,
    season_to: int | None = None,
    min_gp: int = Query(30, ge=0),
    min_mpg: float = Query(20.0, ge=0),
    min_attempts: float | None = Query(None, ge=0),
    team: str | None = None,
    order: str | None = None,
    top_n: int = Query(25, ge=1, le=100),
):
    """
    Player-seasons ranked by one stat. order = high | low; by default the
    stat's better end comes first (low for turnovers, defensive rating and
    turnover %; high for everything else, and for age). min_attempts applies
    per game to the stat's attempts column (FGA, 3PA or FTA) and only to
    shooting percentages.
    """
    if stat not in STATS:
        raise HTTPException(status_code=400, detail=f"Unknown stat '{stat}'. See /leaderboard/options.")
    label, _group, _fmt, first_season, higher_is_better, attempts = STATS[stat]
    if order is None:
        order = "high" if higher_is_better else "low"
    if order not in ("high", "low"):
        raise HTTPException(status_code=400, detail="order must be 'high' or 'low'.")
    if attempts and min_attempts is None:
        min_attempts = ATTEMPT_DEFAULTS[attempts]

    with get_db() as conn:
        cur = conn.cursor()
        lo, hi = _bounds(cur)
        season_to = hi if season_to is None else season_to
        season_from = season_to if season_from is None else season_from
        if season_from > season_to:
            season_from, season_to = season_to, season_from
        clipped_from = max(season_from, first_season, lo)
        notes = []
        if clipped_from > season_from:
            notes.append(f"{label} is recorded from {first_season - 1}-{str(first_season)[-2:]} on; "
                         f"earlier seasons were left out.")
        if season_to < clipped_from:
            raise HTTPException(status_code=404,
                                detail=f"{label} isn't recorded before {first_season - 1}-{str(first_season)[-2:]}.")

        where = [f"{stat} IS NOT NULL", "season BETWEEN %s AND %s", "gp >= %s", "min >= %s"]
        params = [clipped_from, season_to, min_gp, min_mpg]
        if attempts and min_attempts > 0:
            where.append(f"{attempts} >= %s")
            params.append(min_attempts)
        if team:
            where.append("team_abbreviation = %s")
            params.append(team.upper())
        direction = "DESC" if order == "high" else "ASC"
        cols = ["player_id", "player_name", "team_abbreviation", "season", stat] + \
            [c for c in CONTEXT + ([attempts] if attempts else []) if c != stat]
        stability = stable_samples(cur).get(stat)
        sample_sql = f", ({SEASON_SAMPLE_SQL[stat]})::float AS sample_n" if stability else ""

        cur.execute(f"SELECT COUNT(*) FROM player_season_stats WHERE {' AND '.join(where)};", params)
        qualified = cur.fetchone()[0]
        cur.execute(
            f"""SELECT {', '.join(cols)}{sample_sql} FROM player_season_stats
                WHERE {' AND '.join(where)}
                ORDER BY {stat} {direction}, gp DESC, player_name
                LIMIT %s;""",
            params + [top_n],
        )
        rows = cur.fetchall()

    def val(v):
        return None if v is None else round(float(v), 4)

    results = []
    for rank, r in enumerate(rows, start=1):
        row = dict(zip(cols, r))
        item = {
            "rank": rank,
            "player_id": int(row.pop("player_id")),
            "player_name": row.pop("player_name"),
            "team": row.pop("team_abbreviation"),
            "season": row.pop("season"),
            "value": val(row.pop(stat)),
            "context": {k: val(v) for k, v in row.items()},
        }
        if stability:
            item["sample"] = _sample(r[len(cols)], stability["stable_n"])
        results.append(item)

    return {
        "stat": {"key": stat, "label": label, "format": _fmt, "higher_is_better": higher_is_better,
                 "attempts": attempts},
        "stability": stability,
        "filters": {"season_from": clipped_from, "season_to": season_to, "min_gp": min_gp, "min_mpg": min_mpg,
                    "min_attempts": min_attempts if attempts else None, "team": team.upper() if team else None,
                    "order": order, "top_n": top_n},
        "qualified": qualified,
        "notes": notes,
        "results": results,
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }


# ─── Stat stability ─────────────────────────────────────────────────────────
# A sample of size n has reliability n / (n + M), M being the sample where
# half of a player's number is signal (scripts/build_stat_stability.py). Rows
# below RELIABLE are flagged "mostly noise" on the Leaderboard and Breakout
# pages.
RELIABLE = 0.5


def stable_samples(cur):
    """{stat: {stable_n, unit, unit_label}} with stable_n in the season-table
    units of stat_samples.py; {} if the stability tables were never built."""
    cur.execute("SELECT to_regclass('stat_stability');")
    if cur.fetchone()[0] is None:
        return {}
    cur.execute("""SELECT stat, stable_n / nba_unit_scale, unit, unit_label FROM stat_stability
                   WHERE variant = 'catalogue' AND stable_n IS NOT NULL AND nba_unit_scale > 0;""")
    return {k: {"stable_n": round(float(m), 1), "unit": u, "unit_label": ul}
            for k, m, u, ul in cur.fetchall() if k in SEASON_SAMPLE_SQL}


def _sample(n, stable_n):
    if n is None:
        return None
    rel = n / (n + stable_n) if n > 0 else 0.0
    return {"n": round(float(n), 1), "reliability": round(rel, 3), "noisy": rel < RELIABLE}


def sample_reliability(cur, keys, player_seasons):
    """{(player_id, season): {stat: {n, reliability, noisy}}} for the given stats
    (those with a split-half estimate); used by the Breakout Detector."""
    stable = stable_samples(cur)
    keys = [k for k in keys if k in stable]
    if not keys or not player_seasons:
        return {}
    exprs = ", ".join(f"({SEASON_SAMPLE_SQL[k]})::float" for k in keys)
    ids = sorted({int(p) for p, _ in player_seasons})
    seasons = sorted({int(s) for _, s in player_seasons})
    cur.execute(f"SELECT player_id, season, {exprs} FROM player_season_stats "
                f"WHERE player_id = ANY(%s) AND season = ANY(%s);", (ids, seasons))
    wanted = {(int(p), int(s)) for p, s in player_seasons}
    out = {}
    for r in cur.fetchall():
        key = (int(r[0]), int(r[1]))
        if key in wanted:
            out[key] = {k: _sample(r[2 + j], stable[k]["stable_n"]) for j, k in enumerate(keys)}
    return out


@router.get("/leaderboard/stability")
def stat_stability():
    """
    For every catalogue stat: the sample at which a player's number is half
    signal, half noise (split-half reliability with the Spearman-Brown
    formula), the reliability curve behind it, what a typical season gives,
    and the year-to-year correlation (the only measure for stats that exist
    only as season totals).
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('stat_stability');")
        if cur.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="Stat stability hasn't been built: run "
                                "scripts/build_stat_stability.py.")
        cur.execute("""SELECT stat, variant, unit, unit_label, source, season_from, season_to, stable_n, ci_lo,
                              ci_hi, m_min, m_max, fit_points, pool, typical_n, typical_reliability, built_on
                       FROM stat_stability;""")
        split = {}
        for r in cur.fetchall():
            split.setdefault(r[0], {})[r[1]] = {
                "unit": r[2], "unit_label": r[3], "source": r[4], "season_from": r[5], "season_to": r[6],
                "stable_n": r[7], "ci": [r[8], r[9]], "per_target_range": [r[10], r[11]], "fit_points": r[12],
                "pool": r[13], "typical_n": r[14], "typical_reliability": r[15], "curve": [],
            }
            built_on = r[16]
        cur.execute("""SELECT stat, variant, n_target, pool, n_half, r_half, m_point FROM stat_stability_curve
                       ORDER BY stat, variant, n_target;""")
        for stat, variant, n, pool, n_half, r, m in cur.fetchall():
            split[stat][variant]["curve"].append({"n": n, "pool": pool, "n_half": round(n_half, 1),
                                                  "r": round(r, 4), "m": None if m is None else round(m, 1)})
        cur.execute("SELECT stat, r, pairs, season_from, season_to, floors FROM stat_year_to_year;")
        y2y = {r[0]: {"r": r[1], "pairs": r[2], "season_from": r[3], "season_to": r[4], "floors": r[5]}
               for r in cur.fetchall()}

    def rnd(v, d=1):
        return None if v is None else round(float(v), d)

    stats = []
    for key, (label, group, fmt, *_rest) in STATS.items():
        if key == "age":
            continue
        entry = {"key": key, "label": label, "group": group, "format": fmt,
                 "split_half": None, "per_minute": None, "year_to_year": y2y.get(key)}
        for variant, name in (("catalogue", "split_half"), ("per_minute", "per_minute")):
            v = split.get(key, {}).get(variant)
            if v:
                v.update(stable_n=rnd(v["stable_n"]), ci=[rnd(x) for x in v["ci"]],
                         per_target_range=[rnd(x) for x in v["per_target_range"]], typical_n=rnd(v["typical_n"], 0),
                         typical_reliability=rnd(v["typical_reliability"], 3))
                entry[name] = v
        stats.append(entry)
    return {
        "stats": stats,
        "reliable_at": RELIABLE,
        "built_on": str(built_on),
        "method": (
            "Split-half reliability: each player-season's games are split into odd and even games, the stat is "
            "worked out on each half once both halves reach the same sample (n attempts, games or possessions), "
            "each half is centred on its season's average, and the halves are correlated across players. The "
            "Spearman-Brown formula turns that into reliability = n / (n + M) for a sample of n; M, shown here, "
            "is the sample where a player's number is half his own level and half luck. Year-to-year correlation "
            "(value one season vs. the next) also mixes in real change (age, role, team), so it runs lower."
        ),
        "_source": make_source(["stat_stability", "stat_stability_curve", "stat_year_to_year",
                                "player_game_lines", "player_shots", "player_season_stats"],
                               "ESPN play-by-play + nba_api (stats.nba.com) shot charts + Basketball-Reference"),
    }


# ─── Composite metric ("build your own") ────────────────────────────────────
# Stats that can go into a composite: everything except age, which has no
# better or worse end.
COMPOSITE_STATS = [k for k in STATS if k != "age"]
MAX_COMPOSITE_STATS = 8


def _parse_weights(weights: str):
    parsed = {}
    for part in (weights or "").split(","):
        if not part.strip():
            continue
        key, _, w = part.partition(":")
        key = key.strip()
        if key not in COMPOSITE_STATS:
            raise HTTPException(status_code=400, detail=f"Unknown or unusable stat '{key}'.")
        try:
            w = float(w)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Weight for '{key}' must be a number.")
        if not -5 <= w <= 5:
            raise HTTPException(status_code=400, detail="Weights must be between -5 and 5.")
        if w != 0:
            parsed[key] = w
    if not parsed:
        raise HTTPException(status_code=400, detail="Give at least one stat with a non-zero weight, e.g. pts:1,ts_pct:1.")
    if len(parsed) > MAX_COMPOSITE_STATS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_COMPOSITE_STATS} stats.")
    return parsed


@router.get("/leaderboard/composite")
def composite_leaderboard(
    weights: str,
    season_from: int | None = None,
    season_to: int | None = None,
    min_gp: int = Query(30, ge=0),
    min_mpg: float = Query(20.0, ge=0),
    team: str | None = None,
    top_n: int = Query(25, ge=1, le=100),
):
    """
    Rank player-seasons by a weighted sum of z-scores: weights = "pts:1,ts_pct:2".

    Each stat is z-scored within its own season's qualified pool (the players
    passing min_gp/min_mpg that season, before the team filter), so the score
    means "standard deviations above that season's qualified players" and a
    range of seasons compares eras fairly. Stats where lower is better
    (turnovers, defensive rating, turnover %) are flipped, so a positive
    weight always rewards the good end. A shooting percentage on fewer
    attempts a game than ATTEMPT_DEFAULTS counts as average (z = 0), so a
    1-for-1 season can't dominate.
    """
    import numpy as np  # local: only this endpoint needs it

    parsed = _parse_weights(weights)
    stats = list(parsed)
    first_needed = max(STATS[k][3] for k in stats)

    with get_db() as conn:
        cur = conn.cursor()
        lo, hi = _bounds(cur)
        season_to = hi if season_to is None else season_to
        season_from = season_to if season_from is None else season_from
        if season_from > season_to:
            season_from, season_to = season_to, season_from
        clipped_from = max(season_from, first_needed, lo)
        if season_to < clipped_from:
            raise HTTPException(status_code=404, detail=(
                f"These stats are all recorded only from {first_needed - 1}-{str(first_needed)[-2:]} on."))
        attempt_cols = sorted({STATS[k][5] for k in stats if STATS[k][5]})
        cols = ["player_id", "player_name", "team_abbreviation", "season", "gp", "min"] + \
            [c for c in stats + attempt_cols if c not in ("gp", "min")]
        cur.execute(
            f"""SELECT {', '.join(cols)} FROM player_season_stats
                WHERE season BETWEEN %s AND %s AND gp >= %s AND min >= %s;""",
            (clipped_from, season_to, min_gp, min_mpg),
        )
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No player-seasons pass these filters.")
    data = {c: np.array([r[i] for r in rows], dtype=object) for i, c in enumerate(cols)}
    seasons = data["season"].astype(int)
    n = len(rows)
    score = np.zeros(n)
    parts = {}
    missing = np.zeros(n, dtype=bool)
    for k in stats:
        _label, _group, _fmt, _first, higher_is_better, attempts = STATS[k]
        raw = np.array([np.nan if v is None else float(v) for v in data[k]])
        usable = ~np.isnan(raw)
        if attempts:
            att = np.array([0.0 if v is None else float(v) for v in data[attempts]])
            enough = att >= ATTEMPT_DEFAULTS[attempts]
            usable &= enough
            # A percentage with too few (or no) attempts is average, not missing.
            missing |= np.isnan(raw) & enough
        else:
            missing |= np.isnan(raw)
        z = np.zeros(n)
        for s in np.unique(seasons):
            m = (seasons == s) & usable
            if m.sum() >= 2:
                sd = raw[m].std(ddof=0)
                z[m] = 0.0 if sd == 0 else (raw[m] - raw[m].mean()) / sd
        if not higher_is_better:
            z = -z
        parts[k] = (raw, z)
        score += parsed[k] * z

    keep = ~missing
    if team:
        keep &= np.array([t == team.upper() for t in data["team_abbreviation"]])
    idx = np.flatnonzero(keep)
    order = idx[np.argsort(-score[idx], kind="stable")][:top_n]

    def rnd(v, d=4):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)

    results = []
    for rank, i in enumerate(order, start=1):
        results.append({
            "rank": rank,
            "player_id": int(data["player_id"][i]),
            "player_name": data["player_name"][i],
            "team": data["team_abbreviation"][i],
            "season": int(seasons[i]),
            "gp": int(data["gp"][i]),
            "min": rnd(data["min"][i], 1),
            "score": round(float(score[i]), 3),
            "parts": {k: {"value": rnd(parts[k][0][i]), "z": round(float(parts[k][1][i]), 2),
                          "contribution": round(float(parsed[k] * parts[k][1][i]), 3)} for k in stats},
        })

    notes = []
    if clipped_from > season_from:
        notes.append(f"Starts in {clipped_from - 1}-{str(clipped_from)[-2:]}, the first season every chosen stat "
                     f"is recorded.")
    if missing.any():
        k = int(missing.sum())
        notes.append(f"{k} qualified player-season{'s lack' if k != 1 else ' lacks'} one of the stats "
                     f"and {'were' if k != 1 else 'was'} left out.")
    return {
        "weights": [{"key": k, "label": STATS[k][0], "format": STATS[k][2], "weight": parsed[k],
                     "higher_is_better": STATS[k][4]} for k in stats],
        "filters": {"season_from": clipped_from, "season_to": season_to, "min_gp": min_gp, "min_mpg": min_mpg,
                    "team": team.upper() if team else None, "top_n": top_n},
        "pool": int(keep.sum()),
        "notes": notes,
        "results": results,
        "method": (
            "Score = the sum of weight x z-score. Each stat is z-scored within its own season among players "
            "passing the games and minutes filters, so 1.0 means one standard deviation better than that "
            "season's qualified players. Lower-is-better stats are flipped. Shooting percentages on too few "
            "attempts a game count as average. Correlated stats (points and field-goal attempts, say) count "
            "the same skill twice. When few players did something in a season (threes in the early 1980s), "
            "the few who did can sit 5-8 standard deviations above the rest, so one stat can dominate a "
            "multi-era ranking; a stat needs at least two qualifying players in a season to be scored."
        ),
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com) + Basketball-Reference"),
    }
