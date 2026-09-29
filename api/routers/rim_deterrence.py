"""Rim deterrence: what opponents shot at the rim with each defender on the floor and off it.

    GET /defense/rim-deterrence?season=&team=&min_minutes=&position=   one season, league-wide
                                                                        or one team's defenders

Reads `rim_deterrence` and `rim_deterrence_seasons` (scripts/build_rim_deterrence.py):
every field-goal attempt of every regular-season game 2020-21 to 2025-26 mapped
to its five-man stint (lineup_stints), its distance from the NBA shot chart's
coordinates. On = the opponents' attempts with him in the defending five; off =
his team's other tracked stints in the games he played. Each on-minus-off
headline number carries a game-clustered bootstrap 95% interval; rows under the
minutes floor are flagged, not hidden. The profile block reads the same table
through /player-profile/{id}. Tables are cached per process: restart impact_api
after rerunning the script.
"""

from functools import lru_cache
from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

DEFAULT_MIN_MINUTES = 1000  # rim numbers are noisy: 500-minute stints fill the extremes
MAX_MIN_MINUTES = 3000
FEW_OFF_MINUTES = 300
STABILITY_MINUTES = 1000
POSITIONS = {"all": "All players", "bigs": "Centers (listed C, F-C or C-F)"}

BANDS = [
    {"id": "rim", "label": "0-3 ft", "long": "At the rim (under 4 ft)"},
    {"id": "short", "label": "4-9 ft", "long": "Floaters and short hooks"},
    {"id": "mid", "label": "10-15 ft", "long": "Mid-range"},
    {"id": "long2", "label": "16 ft-arc", "long": "Long twos"},
    {"id": "three", "label": "3PT", "long": "Threes"},
    {"id": "unk", "label": "2PT, unknown", "long": "Twos with no distance on file"},
]
BAND_IDS = [b["id"] for b in BANDS]
HEADLINES = ["rim_fga100", "rim_fg", "rim_pts100"]

TABLES = ["rim_deterrence", "rim_deterrence_seasons", "lineup_stints", "player_shots", "player_game_lines", "player_bio"]
UPSTREAM = ("ESPN play-by-play (pbp_events) rebuilt into five-man stints by scripts/build_lineup_stints.py; "
            "shot distances from the NBA's shot chart (nba_api, player_shots)")

METHOD = (
    "Every field-goal attempt of every regular-season game 2020-21 to 2025-26, placed in the five-man stint it happened "
    "in (the same play-by-play lineups as On/Off and RAPM; games whose play-by-play doesn't reconcile, and stints with a "
    "player ESPN gives no id to, are left out). Distance comes from the NBA's own shot chart: each ESPN attempt is "
    "matched to the same shot in the NBA's feed (same game, shooter and period, in order, with an identical make/miss "
    "sequence), about 99% of attempts. For the rest, ESPN's 'N-foot' text; failing that, a layup, dunk, tip or putback "
    "counts as 0-3 ft; any other two is left in 'distance unknown'. The rim is under 4 feet from the hoop (about the "
    "restricted area). On: the opponents' attempts while he was one of the five defenders. Off: his team's other "
    "minutes in the games he played (games he missed aren't counted). Rates are per 100 possessions (FGA + 0.44 FTA - "
    "OREB + TOV averaged over both sides, the On/Off convention, about 3% more than NBA.com counts). Rim points per 100 "
    "= 2 x rim makes per 100: fewer attempts and worse finishing together, fouls and and-ones not included. The 95% "
    "intervals come from resampling his games 2,000 times. This is on/off, not an adjusted number: teammates (a second "
    "big, switchable wings), his backup and the opponents' units he faced all move it."
)


def _label(s):
    return f"{s - 1}-{str(s)[-2:]}"


def _r(v, d=4):
    return None if v is None else round(float(v), d)


@lru_cache(maxsize=1)
def _seasons():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rim_deterrence_seasons')")
        if cur.fetchone()[0] is None:
            return {}
        cur.execute("SELECT * FROM rim_deterrence_seasons ORDER BY season")
        cols = [c[0] for c in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


@lru_cache(maxsize=1)
def _rows():
    """Every stored row, with the player's name and listed position."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT r.*, b.player_name AS bio_name, b.position FROM rim_deterrence r
                       LEFT JOIN player_bio b USING (player_id)""")
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        missing = sorted({r["player_id"] for r in rows if not r["bio_name"]})
        names = {}
        if missing:
            cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                           WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (missing,))
            names = dict(cur.fetchall())
    for r in rows:
        r["player_name"] = r.pop("bio_name") or names.get(r["player_id"])
    return tuple(rows)


def is_big(position):
    return bool(position) and "C" in position


def shape(r, floor=DEFAULT_MIN_MINUTES):
    """API shape of one stored row: headline numbers with intervals and the band breakdown."""
    out = {k: r[k] for k in ("player_id", "player_name", "season", "team_abbreviation", "position", "games", "blk",
                             "minutes_on", "minutes_off", "poss_on", "poss_off", "rim_fga_on", "rim_fga_off",
                             "rim_fgm_on", "rim_fgm_off", "fga_on", "fga_off")}
    out["big"] = is_big(r["position"])
    out["blk36"] = _r(36 * r["blk"] / r["minutes_on"], 2) if r["minutes_on"] else None
    for k in HEADLINES + ["rim_share", "opp_fg"]:
        for side in ("on", "off", "diff"):
            out[f"{k}_{side}"] = _r(r[f"{k}_{side}"])
    for k in HEADLINES:
        lo, hi = r[f"{k}_lo"], r[f"{k}_hi"]
        out[f"{k}_se"], out[f"{k}_lo"], out[f"{k}_hi"] = _r(r[f"{k}_se"]), _r(lo), _r(hi)
        out[f"{k}_clear"] = None if lo is None else bool(lo > 0 or hi < 0)
    bands = {}
    for b in BAND_IDS:
        entry = {}
        for side in ("on", "off"):
            fga, fgm = r[f"{b}_fga_{side}"], r[f"{b}_fgm_{side}"]
            poss, tot = r[f"poss_{side}"], r[f"fga_{side}"]
            entry[side] = {"fga": fga, "fgm": fgm, "per100": _r(100 * fga / poss) if poss else None,
                           "share": _r(fga / tot) if tot else None, "fg": _r(fgm / fga) if fga else None}
        bands[b] = entry
    out["bands"] = bands
    out["qualified"] = (r["minutes_on"] or 0) >= floor
    out["few_off_minutes"] = (r["minutes_off"] or 0) < FEW_OFF_MINUTES
    return out


@lru_cache(maxsize=1)
def stability():
    """Year-to-year correlation of each headline difference, same player in back-to-back seasons
    (all his teams pooled), both seasons with STABILITY_MINUTES+ minutes on."""
    by = {}
    for r in _rows():
        a = by.setdefault((r["player_id"], r["season"]), {"m": 0.0, "poss_on": 0.0, "poss_off": 0.0,
                                                          "fga_on": 0, "fga_off": 0, "fgm_on": 0, "fgm_off": 0})
        a["m"] += r["minutes_on"] or 0
        for side in ("on", "off"):
            a[f"poss_{side}"] += r[f"poss_{side}"] or 0
            a[f"fga_{side}"] += r[f"rim_fga_{side}"]
            a[f"fgm_{side}"] += r[f"rim_fgm_{side}"]

    def diffs(a):
        if min(a["poss_on"], a["poss_off"]) <= 0 or min(a["fga_on"], a["fga_off"]) <= 0:
            return None
        return (100 * (a["fga_on"] / a["poss_on"] - a["fga_off"] / a["poss_off"]),
                a["fgm_on"] / a["fga_on"] - a["fgm_off"] / a["fga_off"],
                200 * (a["fgm_on"] / a["poss_on"] - a["fgm_off"] / a["poss_off"]))

    pairs = []
    for (pid, season), a in by.items():
        b = by.get((pid, season + 1))
        if b and a["m"] >= STABILITY_MINUTES and b["m"] >= STABILITY_MINUTES:
            x, y = diffs(a), diffs(b)
            if x and y:
                pairs.append(x + y)
    if len(pairs) < 20:
        return None
    p = np.array(pairs, float)
    return {"pairs": len(pairs), "min_minutes": STABILITY_MINUTES,
            **{k: round(float(np.corrcoef(p[:, i], p[:, i + 3])[0, 1]), 3) for i, k in enumerate(HEADLINES)}}


def season_summary(s):
    out = {"season": s["season"], "games": s["games"], "attempts": s["attempts"],
           "tracked_attempts": s["tracked_attempts"], "poss": s["poss"], "qualified": s["qualified"],
           "qualified_minutes": s["qualified_minutes"], "bootstraps": s["bootstraps"],
           "three_agree": s["three_agree"], "stints_fga_mismatch": s["stints_fga_mismatch"]}
    tot = sum(s[f"{b}_fga"] for b in BAND_IDS)
    out["bands"] = {b: {"fga": s[f"{b}_fga"], "fgm": s[f"{b}_fgm"], "per100": _r(s[f"{b}_fga100"]),
                        "fg": _r(s[f"{b}_fg"]), "share": _r(s[f"{b}_fga"] / tot) if tot else None} for b in BAND_IDS}
    out["sources"] = {k: s[f"src_{k}"] for k in ("coords", "text", "rule", "unknown", "three")}
    out["rule"] = {"checked": s["rule_probe"], "right": s["rule_right"],
                   "share_right": _r(s["rule_right"] / s["rule_probe"]) if s["rule_probe"] else None}
    out["ci_excludes_zero"] = {k: s[f"{k}_ci_excl"] for k in HEADLINES}
    out["expected_by_chance"] = round(0.05 * s["qualified"], 1)
    return out


@lru_cache(maxsize=64)
def league_ranks(season, floor):
    """Rank (1 = biggest drop) of every qualified row league-wide for each headline difference."""
    q = [r for r in _rows() if r["season"] == season and (r["minutes_on"] or 0) >= floor]
    ranks = {}
    for k in HEADLINES:
        order = sorted((r for r in q if r[f"{k}_diff"] is not None), key=lambda r: r[f"{k}_diff"])
        for i, r in enumerate(order):
            ranks.setdefault((r["player_id"], r["team_abbreviation"]), {})[k] = i + 1
    return ranks, len(q)


@router.get("/defense/rim-deterrence")
def rim_deterrence(season: Optional[int] = None, team: Optional[str] = None, min_minutes: float = DEFAULT_MIN_MINUTES,
                   position: str = "all"):
    seasons = _seasons()
    if not seasons:
        raise HTTPException(status_code=503, detail="No rim deterrence data: run scripts/build_rim_deterrence.py.")
    available = sorted(seasons)
    season = season or available[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=(
            f"No rim deterrence data for {_label(season)}; the play-by-play covers {_label(available[0])} to "
            f"{_label(available[-1])}."))
    if position not in POSITIONS:
        raise HTTPException(status_code=400, detail=f"position must be one of {', '.join(POSITIONS)}.")
    floor = max(0.0, min(float(min_minutes), MAX_MIN_MINUTES))
    rows = [r for r in _rows() if r["season"] == season]
    teams = sorted({r["team_abbreviation"] for r in rows})
    team = team.upper() if team else None
    if team and team not in teams:
        raise HTTPException(status_code=404, detail=f"No {team} defenders in the {_label(season)} stints.")
    ranks, n_q = league_ranks(season, floor)
    out = []
    for r in rows:
        if team and r["team_abbreviation"] != team:
            continue
        if position == "bigs" and not is_big(r["position"]):
            continue
        if not team and (r["minutes_on"] or 0) < floor:
            continue
        d = shape(r, floor)
        rk = ranks.get((r["player_id"], r["team_abbreviation"]), {})
        for k in HEADLINES:
            d[f"{k}_rank"] = rk.get(k)
        out.append(d)
    out.sort(key=lambda d: (d["rim_pts100_diff"] is None, d["rim_pts100_diff"] or 0))
    shown_q = [d for d in out if d["qualified"]]
    noise = {k: {"qualified": sum(1 for d in shown_q if d[f"{k}_clear"] is not None),
                 "ci_excludes_zero": sum(1 for d in shown_q if d[f"{k}_clear"])} for k in HEADLINES}
    for v in noise.values():
        v["expected_by_chance"] = round(0.05 * v["qualified"], 1)
    return {
        "season": season, "seasons_available": available, "teams": teams, "team": team,
        "position": position, "positions": POSITIONS, "min_minutes": floor, "default_min_minutes": DEFAULT_MIN_MINUTES,
        "few_off_minutes": FEW_OFF_MINUTES, "n_ranked": n_q,
        "bands": BANDS, "league": season_summary(seasons[season]), "noise": noise, "stability": stability(),
        "players": out,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


def profile_block(player_id):
    """Every season of one player for /player-profile/{id}: headline numbers, intervals, league ranks
    among qualified players, and the league's rim numbers that season."""
    seasons = _seasons()
    if not seasons:
        return {"rows": [], "qualified_minutes": DEFAULT_MIN_MINUTES}
    rows = []
    for r in sorted((r for r in _rows() if r["player_id"] == player_id), key=lambda r: (r["season"], -r["minutes_on"])):
        d = shape(r)
        rk = league_ranks(r["season"], DEFAULT_MIN_MINUTES)
        mine = rk[0].get((player_id, r["team_abbreviation"]), {})
        for k in HEADLINES:
            d[f"{k}_rank"] = mine.get(k)
        d["n_ranked"] = rk[1]
        s = seasons.get(r["season"])
        d["league_rim_fga100"] = _r(s["rim_fga100"], 2) if s else None
        d["league_rim_fg"] = _r(s["rim_fg"]) if s else None
        rows.append(d)
    return {"rows": rows, "qualified_minutes": DEFAULT_MIN_MINUTES, "few_off_minutes": FEW_OFF_MINUTES,
            "bands": BANDS}
