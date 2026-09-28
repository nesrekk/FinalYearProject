"""
Role Player Finder: "I need a 3-and-D wing" -> ranked player-seasons.

    GET /roles/finder/options — presets (with their weight tables), the
                                component catalogue, seasons, positions
    GET /roles/finder         — the ranked list for a preset or custom weights

Pool: every player-season with 500+ real minutes (min x gp) from 2017-18
on, the first season every input exists: defender_dad (2017-18+),
player_gravity (2013-14+), player_roles (2009-10+), scouting_splits
(2012-13+), player_season_stats. Positions come from defender_dad
(the NBA's listed position), which covers the whole pool.

Score = the sum of weight x z-score over the preset's components, the
same idea as /leaderboard/composite:
  * DAD and Gravity components use the z-scores those tables store, as
    is: dad_z / dad_pos_z are z-scored within the season's qualified
    defenders (1,000+ partial possessions; players under that are
    missing here, not average), and z_three_rate / z_cs_pct / z_contested
    within the season's 500+ minute pool, i.e. this pool.
  * Box-score components (blocks and steals per 36, rebound rates,
    assist/usage/turnover rates, true shooting, BPM parts, the DFG%
    differential and the summed Gravity) are z-scored here, within the
    season among the pool.
A player-season missing a weighted component is left out and counted.

Scouting splits are not scored: they only exist for the ~180 players a
season with 1,500+ minutes and 50+ attempts per split, so weighting them
would silently shrink the pool. Each preset names the splits that matter
for the role and the row shows which of them are significant strengths
or weaknesses ("fit flags"); a player with no tested split shows none.

The presets' weights are a judgment call, written down and shown on the
page, not fitted to anything. Sniff test (2024-25) before shipping:
3-and-D wing -> Anunoby / Bridges / Camara / Nembhard near the top; rim
protector -> Wembanyama / Kessler / Gobert; point-of-attack -> Dyson
Daniels / Dort / Caruso; stretch big -> Lopez / Turner / Porzingis;
floor spacer -> Beasley / Hauser / Hield.
"""

import numpy as np
from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

FIRST_SEASON = 2018
POOL_MIN_MINUTES = 500
POSITIONS = ["G", "G-F", "F-G", "F", "F-C", "C-F", "C"]
GUARDS = ["G", "G-F", "F-G"]
WINGS = ["G", "G-F", "F-G", "F"]
BIGS = ["F-C", "C-F", "C"]
FRONTCOURT = ["F", "F-C", "C-F", "C"]

# key -> (label, group, source, higher_is_better, description)
# source 'stored_z' = the table's own z-score; 'pool_z' = z-scored here.
COMPONENTS = {
    "dad": ("Assignment difficulty (DAD)", "Defense", "stored_z", True,
            "How good the players they guarded were (defender_dad.dad_z). Qualified defenders only."),
    "dad_pos": ("Assignment difficulty vs. position", "Defense", "stored_z", True,
                "DAD z-scored within the season and position group (defender_dad.dad_pos_z)."),
    "dfg": ("Defended FG% vs. normal", "Defense", "pool_z", False,
            "Opponents' FG% when this player was the closest defender minus their usual FG% (lower is better)."),
    "stl36": ("Steals per 36", "Defense", "pool_z", True, "Steals per 36 minutes."),
    "blk36": ("Blocks per 36", "Defense", "pool_z", True, "Blocks per 36 minutes."),
    "dbpm": ("Defensive BPM", "Defense", "pool_z", True, "Basketball-Reference's defensive box plus/minus."),
    "reb_pct": ("Rebound %", "Rebounding", "pool_z", True, "Share of available rebounds grabbed."),
    "oreb_pct": ("Offensive rebound %", "Rebounding", "pool_z", True, "Share of available offensive rebounds grabbed."),
    "three_rate": ("3PA per 100 possessions", "Shooting", "stored_z", True,
                   "Three-point volume (player_gravity.z_three_rate)."),
    "cs_pct": ("Catch-and-shoot 3P%", "Shooting", "stored_z", True,
               "Shrunk towards the shooter's volume tier (player_gravity.z_cs_pct)."),
    "contested": ("Share of 3s taken guarded", "Shooting", "stored_z", True,
                  "Share of threes with a defender within 6 ft: defenders stay attached (player_gravity.z_contested)."),
    "gravity": ("Gravity Index", "Shooting", "pool_z", True,
                "The three shooting components summed (player_gravity.gravity), z-scored again here."),
    "fg3_pct": ("3P%", "Shooting", "pool_z", True, "Three-point %; missing under 2 attempts a game."),
    "ts_pct": ("True shooting %", "Scoring", "pool_z", True, "Points per shooting possession, on one scale."),
    "usg_pct": ("Usage %", "Scoring", "pool_z", True,
                "Share of the team's possessions used. A negative weight asks for a lower-usage role player."),
    "ft_rate": ("Free-throw rate", "Scoring", "pool_z", True, "FTA per FGA: getting to the line."),
    "obpm": ("Offensive BPM", "Scoring", "pool_z", True, "Basketball-Reference's offensive box plus/minus."),
    "ast_pct": ("Assist %", "Creation", "pool_z", True, "Share of teammates' baskets assisted while on the floor."),
    "tov_pct": ("Turnover %", "Creation", "pool_z", False, "Turnovers per 100 plays (lower is better)."),
}

# Each preset: weights (shown on screen), a default position set, an
# optional usage cap, and the scouting splits that matter for the role
# (category, split, short label).
PRESETS = {
    "three_and_d": {
        "label": "3-and-D wing",
        "blurb": "Guards the other team's best perimeter players and hits open threes without needing the ball.",
        "weights": {"dad_pos": 1.25, "dfg": 0.5, "stl36": 0.25, "three_rate": 0.75, "cs_pct": 1.0, "usg_pct": -0.5},
        "positions": WINGS,
        "relative": "league",
        "max_usg": None,
        "flags": [("playtype", "Spotup", "Spot-up"), ("zone", "Corner 3", "Corner 3"),
                  ("zone", "Above the Break 3", "Above-break 3"), ("close_def", "4-6 ft|3PT", "Open 3s"),
                  ("playtype", "Transition", "Transition")],
    },
    "rim_protector": {
        "label": "Rim protector",
        "blurb": "Blocks shots, makes shooters miss at the rim, ends possessions with the rebound.",
        "weights": {"blk36": 1.5, "dfg": 1.0, "reb_pct": 0.5, "dbpm": 0.5, "dad_pos": 0.25},
        "positions": FRONTCOURT,
        "relative": "league",
        "max_usg": None,
        "flags": [("zone", "Restricted Area", "At the rim"), ("playtype", "PRRollman", "Roll man"),
                  ("playtype", "Cut", "Cutter"), ("playtype", "OffRebound", "Put-backs")],
    },
    "stretch_big": {
        "label": "Stretch big",
        "blurb": "A center or forward-center who takes and makes threes while still rebounding and protecting the rim.",
        "weights": {"three_rate": 1.0, "cs_pct": 1.0, "reb_pct": 0.5, "blk36": 0.5, "ts_pct": 0.25},
        "positions": BIGS,
        "relative": "positions",
        "max_usg": None,
        "flags": [("playtype", "Spotup", "Spot-up"), ("zone", "Above the Break 3", "Above-break 3"),
                  ("playtype", "PRRollman", "Roll / pop"), ("zone", "Restricted Area", "At the rim")],
    },
    "secondary_creator": {
        "label": "Secondary creator",
        "blurb": "Sets up teammates efficiently without being the primary option: usage capped at 24%.",
        "weights": {"ast_pct": 1.5, "ts_pct": 0.75, "tov_pct": 0.5, "obpm": 0.5},
        "positions": POSITIONS,
        "relative": "league",
        "max_usg": 24.0,
        "flags": [("playtype", "PRBallHandler", "Pick-and-roll"), ("playtype", "Isolation", "Isolation"),
                  ("playtype", "Handoff", "Handoff"), ("playtype", "Spotup", "Spot-up")],
    },
    "point_of_attack": {
        "label": "Point-of-attack defender",
        "blurb": "A guard who takes the toughest backcourt assignment every night and creates turnovers.",
        "weights": {"dad": 1.0, "stl36": 0.5, "dfg": 0.5, "dbpm": 0.25},
        "positions": GUARDS,
        "relative": "league",
        "max_usg": None,
        "flags": [("playtype", "Spotup", "Spot-up"), ("playtype", "Transition", "Transition"),
                  ("zone", "Corner 3", "Corner 3"), ("playtype", "Cut", "Cutter")],
    },
    "glass_cleaner": {
        "label": "Glass cleaner",
        "blurb": "Lives on the offensive glass, finishes what it gets, and still blocks a few.",
        "weights": {"oreb_pct": 1.5, "reb_pct": 1.0, "ts_pct": 0.5, "blk36": 0.25},
        "positions": FRONTCOURT,
        "relative": "league",
        "max_usg": None,
        "flags": [("playtype", "OffRebound", "Put-backs"), ("zone", "Restricted Area", "At the rim"),
                  ("playtype", "PRRollman", "Roll man"), ("playtype", "Cut", "Cutter")],
    },
    "floor_spacer": {
        "label": "Floor spacer",
        "blurb": "Shoots a lot of threes, makes them off the catch, and gets guarded for it; usage doesn't matter.",
        "weights": {"three_rate": 1.0, "cs_pct": 1.0, "contested": 0.5, "fg3_pct": 0.5, "usg_pct": -0.25},
        "positions": POSITIONS,
        "relative": "league",
        "max_usg": None,
        "flags": [("playtype", "Spotup", "Spot-up"), ("playtype", "OffScreen", "Off screens"),
                  ("playtype", "Handoff", "Handoff"), ("zone", "Corner 3", "Corner 3"),
                  ("zone", "Above the Break 3", "Above-break 3"), ("close_def", "2-4 ft|3PT", "Tight 3s")],
    },
}
MAX_CUSTOM = 8
CUSTOM_FLAGS = [("playtype", "Spotup", "Spot-up"), ("playtype", "PRBallHandler", "Pick-and-roll"),
                ("zone", "Restricted Area", "At the rim"), ("zone", "Corner 3", "Corner 3")]

_SOURCE_TABLES = ["player_season_stats", "defender_dad", "player_gravity", "player_roles", "scouting_splits",
                  "contract_value"]
_SOURCE = "nba_api (stats.nba.com) + Basketball-Reference"


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _parse_weights(weights):
    parsed = {}
    for part in (weights or "").split(","):
        if not part.strip():
            continue
        key, _, w = part.partition(":")
        key = key.strip()
        if key not in COMPONENTS:
            raise HTTPException(status_code=400, detail=f"Unknown component '{key}'. See /roles/finder/options.")
        try:
            w = float(w)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Weight for '{key}' must be a number.")
        if not -5 <= w <= 5:
            raise HTTPException(status_code=400, detail="Weights must be between -5 and 5.")
        if w != 0:
            parsed[key] = w
    if not parsed:
        raise HTTPException(status_code=400, detail="Give at least one component a non-zero weight, e.g. dad_pos:1,cs_pct:1.")
    if len(parsed) > MAX_CUSTOM:
        raise HTTPException(status_code=400, detail=f"At most {MAX_CUSTOM} components.")
    return parsed


def _bounds(cur):
    cur.execute("SELECT MAX(season) FROM player_season_stats;")
    last = cur.fetchone()[0]
    cur.execute("SELECT MIN(season), MAX(season) FROM defender_dad;")
    dad_lo, dad_hi = cur.fetchone()
    return max(FIRST_SEASON, dad_lo), min(last, dad_hi)


def _salary_seasons(cur, lo, hi):
    cur.execute("SELECT DISTINCT season FROM contract_value WHERE season BETWEEN %s AND %s ORDER BY 1;", (lo, hi))
    return [r[0] for r in cur.fetchall()]


@router.get("/roles/finder/options")
def role_finder_options():
    with get_db() as conn:
        cur = conn.cursor()
        lo, hi = _bounds(cur)
        salary_seasons = _salary_seasons(cur, lo, hi)
    return {
        "seasons": {"from": lo, "to": hi},
        "salary_seasons": salary_seasons,
        "positions": POSITIONS,
        "pool_rule": f"{POOL_MIN_MINUTES}+ minutes in the season",
        "presets": [
            {"key": k, "label": p["label"], "blurb": p["blurb"],
             "weights": [{"key": c, "label": COMPONENTS[c][0], "weight": w} for c, w in p["weights"].items()],
             "positions": p["positions"], "relative": p["relative"], "max_usg": p["max_usg"],
             "flags": [{"category": c, "split": s, "label": l} for c, s, l in p["flags"]]}
            for k, p in PRESETS.items()
        ],
        "components": [
            {"key": k, "label": v[0], "group": v[1], "source": v[2], "higher_is_better": v[3], "description": v[4]}
            for k, v in COMPONENTS.items()
        ],
        "max_custom": MAX_CUSTOM,
        "_source": make_source(_SOURCE_TABLES, _SOURCE),
    }


def _load_pool(cur, season):
    cur.execute(
        """SELECT p.player_id, p.player_name, p.team_abbreviation, p.gp, p.min, p.pts, p.usg_pct, p.ts_pct,
                  p.ast_pct, p.tov_pct, p.reb_pct, p.oreb_pct, p.obpm, p.dbpm, p.stl, p.blk, p.fta, p.fga,
                  p.fg3_pct, p.fg3a,
                  d.position, d.qualified, d.dad_z, d.dad_pos_z, d.dfg_diff, d.n_assignments, d.total_poss, d.d_fga,
                  g.z_three_rate, g.z_cs_pct, g.z_contested, g.gravity, g.fg3a_total, g.cs_fg3a, g.def_fg3a,
                  r.role,
                  c.salary, c.surplus
           FROM player_season_stats p
           LEFT JOIN defender_dad d ON d.season = p.season AND d.player_id = p.player_id
           LEFT JOIN player_gravity g ON g.season = p.season AND g.player_id = p.player_id
           LEFT JOIN player_roles r ON r.season = p.season AND r.player_id = p.player_id
           LEFT JOIN contract_value c ON c.season = p.season AND c.player_id = p.player_id
           WHERE p.season = %s AND p.min * p.gp >= %s;""",
        (season, POOL_MIN_MINUTES),
    )
    cols = ["player_id", "player_name", "team", "gp", "min", "pts", "usg_pct", "ts_pct", "ast_pct", "tov_pct",
            "reb_pct", "oreb_pct", "obpm", "dbpm", "stl", "blk", "fta", "fga", "fg3_pct", "fg3a",
            "position", "dad_qualified", "dad_z", "dad_pos_z", "dfg_diff", "n_assignments", "total_poss", "d_fga",
            "z_three_rate", "z_cs_pct", "z_contested", "gravity", "fg3a_total", "cs_fg3a", "def_fg3a",
            "role", "salary", "surplus"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _f(v):
    return np.nan if v is None else float(v)


# Raw value per component for one pool row (nan = missing); stored z's
# come back as the raw value too, since they are the value.
def _raw(row, key):
    if key == "dad":
        return _f(row["dad_z"]) if row["dad_qualified"] else np.nan
    if key == "dad_pos":
        return _f(row["dad_pos_z"]) if row["dad_qualified"] else np.nan
    if key == "dfg":
        return _f(row["dfg_diff"]) if row["dad_qualified"] else np.nan
    if key == "stl36":
        return _f(row["stl"]) * 36 / _f(row["min"]) if row["min"] else np.nan
    if key == "blk36":
        return _f(row["blk"]) * 36 / _f(row["min"]) if row["min"] else np.nan
    if key == "three_rate":
        return _f(row["z_three_rate"])
    if key == "cs_pct":
        return _f(row["z_cs_pct"])
    if key == "contested":
        return _f(row["z_contested"])
    if key == "ft_rate":
        return _f(row["fta"]) / _f(row["fga"]) if row["fga"] else np.nan
    if key == "fg3_pct":
        return _f(row["fg3_pct"]) if (row["fg3a"] or 0) >= 2 else np.nan
    return _f(row[key])


def _flags(cur, season, player_ids, flags):
    """Significant scouting splits, per player, for the preset's splits."""
    if not flags or not player_ids:
        return {}, 0
    cur.execute(
        """SELECT player_id, category, split, direction, z, n, n_unit
           FROM scouting_splits
           WHERE season = %s AND player_id = ANY(%s) AND (category, split) IN %s;""",
        (season, list(player_ids), tuple((c, s) for c, s, _ in flags)),
    )
    labels = {(c, s): l for c, s, l in flags}
    out = {}
    tested = set()
    for pid, cat, split, direction, z, n, unit in cur.fetchall():
        tested.add(pid)
        if direction in ("strength", "weakness"):
            out.setdefault(pid, []).append({
                "label": labels[(cat, split)], "direction": direction, "z": round(float(z), 1),
                "n": int(n), "n_unit": unit,
            })
    return out, len(tested)


@router.get("/roles/finder")
def role_finder(
    preset: str = "three_and_d",
    season: int | None = None,
    weights: str | None = None,
    positions: str | None = None,
    max_usg: float | None = Query(None, ge=0, le=60),
    max_salary: float | None = Query(None, ge=0),
    relative: str | None = None,
    top_n: int = Query(25, ge=1, le=100),
):
    """
    Ranked player-seasons for a role. preset = one of PRESETS or 'custom'
    (then weights = "dad_pos:1,cs_pct:1"). positions = comma list of the
    NBA's listed positions (G, G-F, F-G, F, F-C, C-F, C); the preset's
    default when omitted. max_usg caps usage % (the secondary-creator
    preset's default is 24). max_salary (in dollars) filters on that
    season's real salary; only seasons with contract data support it.
    relative = league (z-scores as stored / within the whole pool) or
    positions (every component re-z-scored within the chosen positions,
    so a center's three-point volume is judged against centers).
    """
    if preset == "custom":
        parsed = _parse_weights(weights)
        p = {"label": "Custom", "blurb": None, "positions": POSITIONS, "relative": "league", "max_usg": None,
             "flags": CUSTOM_FLAGS}
    elif preset in PRESETS:
        p = PRESETS[preset]
        parsed = _parse_weights(weights) if weights else dict(p["weights"])
    else:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'. See /roles/finder/options.")
    if positions:
        pos = [s.strip().upper() for s in positions.split(",") if s.strip()]
        bad = [s for s in pos if s not in POSITIONS]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown position(s) {', '.join(bad)}. Use {', '.join(POSITIONS)}.")
    else:
        pos = list(p["positions"])
    if max_usg is None:
        max_usg = p["max_usg"]
    relative = relative or p["relative"]
    if relative not in ("league", "positions"):
        raise HTTPException(status_code=400, detail="relative must be 'league' or 'positions'.")

    with get_db() as conn:
        cur = conn.cursor()
        lo, hi = _bounds(cur)
        season = hi if season is None else season
        if not lo <= season <= hi:
            raise HTTPException(status_code=404, detail=f"The Role Finder covers {_label(lo)} to {_label(hi)}.")
        salary_seasons = _salary_seasons(cur, lo, hi)
        rows = _load_pool(cur, season)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No player-seasons with {POOL_MIN_MINUTES}+ minutes in {_label(season)}.")

        keys = list(parsed)
        n = len(rows)
        at_pos = np.array([r["position"] in pos for r in rows])
        raw = {k: np.array([_raw(r, k) for r in rows]) for k in keys}
        z = {}
        for k in keys:
            _lab, _grp, source, higher, _d = COMPONENTS[k]
            v = raw[k]
            if source == "stored_z" and relative == "league":
                zk = v.copy()
            else:
                # Within the whole pool, or within the chosen positions only
                # (a stored z re-z-scored within a group is that group's z).
                ok = ~np.isnan(v) & (at_pos if relative == "positions" else True)
                zk = np.full(n, np.nan)
                if ok.sum() >= 2:
                    sd = v[ok].std(ddof=0)
                    zk[ok] = 0.0 if sd == 0 else (v[ok] - v[ok].mean()) / sd
            if not higher:
                zk = -zk
            z[k] = zk
        score = np.zeros(n)
        missing = np.zeros(n, dtype=bool)
        for k in keys:
            missing |= np.isnan(z[k])
            score += parsed[k] * np.nan_to_num(z[k])

        keep = ~missing & at_pos
        if max_usg is not None:
            keep &= np.array([r["usg_pct"] is not None and float(r["usg_pct"]) * 100 <= max_usg for r in rows])
        salary_applied = False
        notes = []
        if max_salary is not None:
            if season in salary_seasons:
                keep &= np.array([r["salary"] is not None and float(r["salary"]) <= max_salary for r in rows])
                salary_applied = True
            else:
                notes.append(f"No salary data for {_label(season)} (contract data covers "
                             f"{', '.join(_label(s) for s in salary_seasons)}), so the salary filter was not applied.")
        idx = np.flatnonzero(keep)
        order = idx[np.argsort(-score[idx], kind="stable")][:top_n]

        flag_map, n_flag_tested = _flags(cur, season, [int(rows[i]["player_id"]) for i in order], p["flags"])

    def rnd(v, d=2):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), d)

    results = []
    for rank, i in enumerate(order, start=1):
        r = rows[i]
        results.append({
            "rank": rank,
            "player_id": int(r["player_id"]),
            "player_name": r["player_name"],
            "team": r["team"],
            "position": r["position"],
            "role": r["role"],
            "score": round(float(score[i]), 3),
            "parts": {k: {"value": rnd(raw[k][i], 3), "z": rnd(z[k][i]), "contribution": rnd(parsed[k] * z[k][i], 3)}
                      for k in keys},
            "context": {
                "gp": int(r["gp"]), "min": rnd(r["min"], 1), "pts": rnd(r["pts"], 1), "usg_pct": rnd(None if r["usg_pct"] is None else float(r["usg_pct"]) * 100, 1),
                "ts_pct": rnd(r["ts_pct"], 3), "gravity": rnd(r["gravity"]), "dad_z": rnd(r["dad_z"]),
                "n_assignments": r["n_assignments"], "fg3a_total": rnd(r["fg3a_total"], 0), "d_fga": r["d_fga"],
            },
            "salary": None if r["salary"] is None else int(r["salary"]),
            "surplus": rnd(r["surplus"], 0),
            "flags": flag_map.get(int(r["player_id"]), []),
        })

    n_missing = int((missing & at_pos).sum())
    if n_missing:
        by = {COMPONENTS[k][0]: int((np.isnan(z[k]) & at_pos).sum()) for k in keys}
        which = ", ".join(f"{lab} ({c})" for lab, c in by.items() if c)
        notes.append(f"{n_missing} player-season{'s' if n_missing != 1 else ''} at these positions "
                     f"{'lack' if n_missing != 1 else 'lacks'} a component and {'were' if n_missing != 1 else 'was'} "
                     f"left out: {which}.")
    return {
        "preset": {"key": preset, "label": p["label"], "blurb": p["blurb"]},
        "weights": [{"key": k, "label": COMPONENTS[k][0], "group": COMPONENTS[k][1], "source": COMPONENTS[k][2],
                     "weight": parsed[k], "higher_is_better": COMPONENTS[k][3]} for k in keys],
        "filters": {"season": season, "positions": pos, "relative": relative, "max_usg": max_usg,
                    "max_salary": max_salary if salary_applied else None, "salary_available": season in salary_seasons,
                    "top_n": top_n},
        "pool": int(keep.sum()),
        "pool_total": n,
        "flags": [{"category": c, "split": s, "label": l} for c, s, l in p["flags"]],
        "flags_tested": n_flag_tested,
        "notes": notes,
        "results": results,
        "method": (
            "Score = the sum of weight x z-score. DAD components use the DAD table's own z-scores (within the "
            "season's qualified defenders, 1,000+ partial possessions); shooting components use the Gravity "
            "table's (within the season's 500+ minute pool). Everything else is z-scored here within the season "
            f"among players with {POOL_MIN_MINUTES}+ minutes. Lower-is-better components are flipped. A player "
            "missing a weighted component is left out, not treated as average. The weights are a judgment "
            "call, shown in full above; change them and the ranking changes. Fit flags are the Scouting "
            "Report's significant splits (p < 0.05) for the role, only for players with 1,500+ minutes and "
            "enough attempts, so a blank means untested, not neutral."
        ),
        "_source": make_source(_SOURCE_TABLES, _SOURCE),
    }
