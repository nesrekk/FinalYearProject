"""
Player profile page: one aggregating call instead of ten.

    GET /player-profile/resolve?name=Jokic        name -> player_id
    GET /player-profile/{player_id}               every block below
    GET /player-profile/{player_id}/shot-zones?season=2026

Each block says whether it has data for this player and, when it doesn't,
why (most often: the source only covers certain seasons, e.g. tracking data
starts in 2013-14). Every number carries its season and sample size. Blocks
read the same stored tables as the feature pages they come from, so a
profile never disagrees with, say, the DAD Index page.

Similar seasons come from the similarity service (port 8001,
/similarity/season-profile/...), which the page calls itself; this endpoint
lists the seasons that service can compare.
"""

import json
import math
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

import shots_lib
from impact_core import find_player, get_db
from routers.dad_index import MIN_DFGA_RELIABLE
from routers.explore import BREAKOUT_DEFAULT, BREAKOUT_DEFAULT_GP, BREAKOUT_DEFAULT_MPG, BREAKOUT_FLAG_TOP, \
    breakout_flags, breakout_persistence
from routers.leaderboard import STATS
from source_badge import make_source

router = APIRouter()

SMALL_GP = 10              # season rows below this are greyed out
ZONE_MIN_FGA = 20          # a zone with fewer attempts is greyed out
SHOT_SEASON_MIN_FGA = 200  # a season with fewer located shots is flagged small
CLUTCH_SMALL_PLAYS = 100   # clutch WPA on fewer clutch plays is flagged small
ROLE_RULE = "15+ minutes a game in 20+ games, 2009-10 on"
# Inputs the similarity service needs (similarity_api.SIM_FEATURES).
SIM_FEATURES = ["pts", "ts_pct", "usg_pct", "net_rating", "ast_pct", "reb_pct", "age", "min"]

SEASON_COLS = ["season", "team_abbreviation", "age", "gp", "min", "pts", "reb", "ast", "stl", "blk", "tov",
               "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "usg_pct", "fga", "fg3a", "fta", "bpm", "vorp"]


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _num(v, d=4):
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else round(v, d)


def _span(cur, sql):
    cur.execute(sql)
    lo, hi = cur.fetchone()
    return {"from": lo, "to": hi}


@lru_cache(maxsize=1)
def _coverage():
    """Which seasons each source covers, read from the tables themselves."""
    with get_db() as conn:
        cur = conn.cursor()
        cov = {
            "seasons": _span(cur, "SELECT min(season), max(season) FROM player_season_stats"),
            "roles": _span(cur, "SELECT min(season), max(season) FROM player_roles"),
            "scouting": _span(cur, "SELECT min(season), max(season) FROM scouting_splits"),
            "defense": _span(cur, "SELECT min(season), max(season) FROM defender_dad"),
            "gravity": _span(cur, "SELECT min(season), max(season) FROM player_gravity WHERE gravity IS NOT NULL"),
            "clutch": _span(cur, "SELECT min(season), max(season) FROM pbp_games"),
            "awards": _span(cur, "SELECT min(season), max(season) FROM player_awards WHERE award <> 'All-Star'"),
            "all_star": _span(cur, "SELECT min(season), max(season) FROM player_awards WHERE award = 'All-Star'"),
        }
        cur.execute("SELECT min(season), max(season) FROM player_shots")
        lo, hi = cur.fetchone()
        cov["shots"] = {"from": int(lo[:4]) + 1, "to": int(hi[:4]) + 1}  # '1996-97' -> 1997
        cur.execute("SELECT season, included FROM contract_value_seasons ORDER BY season")
        rows = cur.fetchall()
        cov["contracts"] = {"from": rows[0][0], "to": rows[-1][0],
                            "included": [s for s, inc in rows if inc], "excluded": [s for s, inc in rows if not inc]}
    return cov


def _league_zones(cur, season_label):
    """League FG% per zone for one regular season, from league_zone_mix
    (every stored shot, same classifier; build_league_zone_mix.py)."""
    cur.execute("SELECT zone, fgm, fga FROM league_zone_mix WHERE season = %s", (season_label,))
    return [{"zone": z, "fgm": m, "fga": a, "fg_pct": round(m / a, 3) if a else None} for z, m, a in cur.fetchall()]


def _zones(cur, player_id, season):
    season_label = label(season)
    cur.execute(
        """SELECT loc_x, loc_y, shot_distance, shot_type, shot_zone_basic, shot_made_flag
           FROM player_shots WHERE player_id = %s AND season = %s AND game_id LIKE '002%%'""",
        (player_id, season_label),
    )
    cols = ["loc_x", "loc_y", "shot_distance", "shot_type", "shot_zone_basic", "shot_made_flag"]
    zones = shots_lib.compute_zone_stats([dict(zip(cols, r)) for r in cur.fetchall()])
    fga = sum(z["fga"] for z in zones)
    league = {z["zone"]: z for z in _league_zones(cur, season_label)}
    for z in zones:
        z["share"] = round(z["fga"] / fga, 4) if fga else None
        z["league_fg_pct"] = league[z["zone"]]["fg_pct"] if z["zone"] in league else None
        z["small_sample"] = z["fga"] < ZONE_MIN_FGA
    return {
        "season": season, "fga": fga, "fgm": sum(z["fgm"] for z in zones),
        "small_sample": fga < SHOT_SEASON_MIN_FGA,
        "zones": zones, "league_zones": list(league.values()),
    }


def _shot_seasons(cur, player_id):
    cur.execute(
        """SELECT season, count(*) FROM player_shots WHERE player_id = %s AND game_id LIKE '002%%'
           GROUP BY season ORDER BY season""",
        (player_id,),
    )
    return [{"season": int(s[:4]) + 1, "fga": n} for s, n in cur.fetchall()]


def _player(cur, player_id):
    cur.execute("SELECT player_name FROM player_season_stats WHERE player_id = %s ORDER BY season DESC LIMIT 1",
                (player_id,))
    r = cur.fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail=(
            f"No NBA seasons on file for player id {player_id}. Draft picks who never played, and a few "
            "pre-2010 players whose Basketball-Reference and NBA records couldn't be matched, have no profile."))
    return r[0]


@router.get("/player-profile/resolve")
def resolve_player(name: str = Query(..., min_length=2)):
    """Name (accent-insensitive, partial) -> the player's id, for links that
    only know a name (the search palette)."""
    with get_db() as conn:
        player_id, resolved = find_player(conn.cursor(), name.strip())
    return {"player_id": int(player_id), "player_name": resolved}


@router.get("/player-profile/{player_id}/shot-zones")
def get_profile_shot_zones(player_id: int, season: int):
    with get_db() as conn:
        cur = conn.cursor()
        name = _player(cur, player_id)
        seasons = [s["season"] for s in _shot_seasons(cur, player_id)]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"No regular-season shot locations for {name} in {label(season)}.")
        return {"player_id": player_id, **_zones(cur, player_id, season)}


@router.get("/player-profile/{player_id}")
def get_player_profile(player_id: int):
    cov = _coverage()
    with get_db() as conn:
        cur = conn.cursor()
        name = _player(cur, player_id)

        # ── Season by season, with team stints for traded seasons ──
        cur.execute(f"SELECT {', '.join(SEASON_COLS)} FROM player_season_stats WHERE player_id = %s ORDER BY season",
                    (player_id,))
        seasons = [dict(zip(SEASON_COLS, r)) for r in cur.fetchall()]
        cur.execute("SELECT season, team, gp FROM player_team_stints WHERE player_id = %s ORDER BY season, stint",
                    (player_id,))
        stints = {}
        for s, team, gp in cur.fetchall():
            stints.setdefault(s, []).append({"team": team, "gp": gp})
        cur.execute("SELECT season, role FROM player_roles WHERE player_id = %s", (player_id,))
        roles = dict(cur.fetchall())
        for s in seasons:
            for k in SEASON_COLS[2:]:
                s[k] = _num(s[k])
            s["gp"] = int(s["gp"]) if s["gp"] is not None else None
            s["stints"] = stints.get(s["season"], [])
            s["role"] = roles.get(s["season"])
            s["small_sample"] = s["gp"] is None or s["gp"] < SMALL_GP
        last = seasons[-1]
        last_team = last["stints"][-1]["team"] if last["stints"] else last["team_abbreviation"]

        # ── Header: bio, draft, role, Greats ──
        cur.execute("""SELECT position, height_in, weight_lb, birth_date, colleges, hall_of_fame
                       FROM player_bio WHERE player_id = %s""", (player_id,))
        b = cur.fetchone()
        bio = None if b is None else {
            "position": b[0], "height_in": b[1], "weight_lb": b[2],
            "birth_date": b[3].isoformat() if b[3] else None, "colleges": b[4], "hall_of_fame": b[5],
        }
        cur.execute("""SELECT draft_year, round_number, overall_pick, team_abbreviation, organization
                       FROM draft_history WHERE player_id = %s ORDER BY draft_year DESC LIMIT 1""", (player_id,))
        d = cur.fetchone()
        draft = None if d is None else {"year": d[0], "round": d[1], "pick": d[2], "team": d[3], "organization": d[4]}
        cur.execute("SELECT first_season FROM player_first_season WHERE player_id = %s", (player_id,))
        fs = cur.fetchone()
        first_season = fs[0] if fs else seasons[0]["season"]
        cur.execute("""SELECT r.season, r.role, r.family, a.description FROM player_roles r
                       LEFT JOIN role_archetypes a USING (role_id)
                       WHERE r.player_id = %s ORDER BY r.season DESC LIMIT 1""", (player_id,))
        r = cur.fetchone()
        role = None if r is None else {"season": r[0], "role": r[1], "family": r[2], "description": r[3]}
        cur.execute("SELECT grp, facts FROM greats WHERE player_id = %s", (player_id,))
        g = cur.fetchone()
        greats = None if g is None else {
            "group": "75th Anniversary Team" if g[0] == "75" else "Today's star",
            "facts": g[1] if isinstance(g[1], list) else json.loads(g[1] or "[]"),
        }

        # ── Awards ──
        cur.execute("""SELECT season, award, detail, winner, vote_share, finish FROM player_awards
                       WHERE player_id = %s ORDER BY season, award""", (player_id,))
        awards = [{"season": a[0], "award": a[1], "detail": a[2], "winner": a[3], "vote_share": _num(a[4], 3),
                   "finish": a[5]} for a in cur.fetchall()]

        # ── Scouting report seasons (the card itself loads its own data) ──
        cur.execute("SELECT DISTINCT season FROM scouting_splits WHERE player_id = %s ORDER BY season", (player_id,))
        scouting_seasons = [s for (s,) in cur.fetchall()]

        # ── Defense: DAD Index ──
        cur.execute("""SELECT season, team_abbreviation, pos_group, qualified, total_poss, n_assignments, dad, dad_z,
                              dad_pos_z, d_fga, d_fg_pct, dfg_diff, top3
                       FROM defender_dad WHERE player_id = %s ORDER BY season""", (player_id,))
        defense = []
        for x in cur.fetchall():
            d_fga, d_pct = x[9], x[10]
            top3 = x[12] if isinstance(x[12], list) else json.loads(x[12] or "[]")
            defense.append({
                "season": x[0], "team": x[1], "pos_group": x[2], "qualified": x[3], "total_poss": _num(x[4], 1),
                "n_assignments": x[5], "dad": _num(x[6], 3), "dad_z": _num(x[7], 2), "dad_pos_z": _num(x[8], 2),
                "d_fga": d_fga, "dfg_diff": _num(x[11]),
                "dfg_diff_margin95": _num(1.96 * math.sqrt(d_pct * (1 - d_pct) / d_fga)) if d_fga and d_pct else None,
                "small_dfg_sample": d_fga is None or d_fga < MIN_DFGA_RELIABLE,
                "top_assignments": top3,
            })

        # ── Gravity, with rank in that season's pool ──
        cur.execute("""SELECT season, in_pool, minutes, gravity, rank, pool, three_rate, fg3a_total, cs_pct_raw,
                              cs_fg3a, contested_share_shrunk, z_three_rate, z_cs_pct, z_contested
                       FROM (SELECT *, rank() OVER (PARTITION BY season ORDER BY gravity DESC) AS rank,
                                    count(*) OVER (PARTITION BY season) AS pool
                             FROM player_gravity WHERE in_pool AND gravity IS NOT NULL) ranked
                       WHERE player_id = %s ORDER BY season""", (player_id,))
        gkeys = ["season", "in_pool", "minutes", "gravity", "rank", "pool", "three_rate", "fg3a_total", "cs_pct",
                 "cs_fg3a", "contested_share", "z_three_rate", "z_cs_pct", "z_contested"]
        gravity = [{k: (_num(v) if isinstance(v, float) else v) for k, v in zip(gkeys, x)} for x in cur.fetchall()]

        # ── Contract value ──
        cur.execute("""SELECT season, team_abbreviation, minutes, salary, war, fair_value, surplus
                       FROM contract_value WHERE player_id = %s ORDER BY season""", (player_id,))
        contracts = [{"season": x[0], "team": x[1], "minutes": _num(x[2], 0), "salary": x[3], "war": _num(x[4], 2),
                      "fair_value": _num(x[5], 0), "surplus": _num(x[6], 0)} for x in cur.fetchall()]

        # ── Clutch: WPA over every stored play-by-play season combined ──
        cur.execute("""SELECT n_games, n_plays, total_wpa, clutch_wpa, clutch_plays, rank, pool FROM (
                           SELECT *, rank() OVER (ORDER BY clutch_wpa DESC) AS rank, count(*) OVER () AS pool
                           FROM player_wpa_totals WHERE clutch_plays >= 3) w
                       WHERE person_id = %s""", (player_id,))
        c = cur.fetchone()
        clutch = None if c is None else {
            "n_games": c[0], "n_plays": c[1], "total_wpa": _num(c[2], 2), "clutch_wpa": _num(c[3], 2),
            "clutch_plays": c[4], "rank": c[5], "pool": c[6], "small_sample": c[4] < CLUTCH_SMALL_PLAYS,
        }

        shot_seasons = _shot_seasons(cur, player_id)
        zones = _zones(cur, player_id, shot_seasons[-1]["season"]) if shot_seasons else None

        # Seasons the similarity service can compare (all eight inputs present).
        cur.execute(f"""SELECT season FROM player_season_stats WHERE player_id = %s
                        AND {" AND ".join(f"{k} IS NOT NULL" for k in SIM_FEATURES)} ORDER BY season""",
                    (player_id,))
        sim_seasons = [s for (s,) in cur.fetchall()]

    flags = breakout_flags().get(player_id, [])
    first_recorded = {k: STATS[k][3] for k in ("stl", "blk", "tov", "fg3a", "usg_pct", "bpm")}

    return {
        "player": {
            "player_id": player_id, "player_name": name,
            "first_season": seasons[0]["season"], "last_season": last["season"], "n_seasons": len(seasons),
            "rookie_season": first_season, "last_team": last_team,
            "active": last["season"] == cov["seasons"]["to"],
            "bio": bio, "draft": draft, "role": role, "role_rule": ROLE_RULE, "greats": greats,
        },
        "coverage": cov,
        "seasons": {"rows": seasons, "small_gp": SMALL_GP, "first_recorded": first_recorded},
        "awards": {"rows": awards},
        "shots": {"seasons": shot_seasons, "zones": zones, "zone_min_fga": ZONE_MIN_FGA,
                  "season_min_fga": SHOT_SEASON_MIN_FGA},
        "scouting": {"seasons": scouting_seasons},
        "defense": {"rows": defense, "reliable_min_dfga": MIN_DFGA_RELIABLE},
        "gravity": {"rows": gravity},
        "contracts": {"rows": contracts},
        "clutch": clutch,
        "similarity": {"seasons": sim_seasons},
        "breakouts": {
            "flags": flags, "top": BREAKOUT_FLAG_TOP, "stats": [STATS[k][0] for k in BREAKOUT_DEFAULT],
            "keys": BREAKOUT_DEFAULT, "min_gp": BREAKOUT_DEFAULT_GP, "min_mpg": BREAKOUT_DEFAULT_MPG,
            "persistence": breakout_persistence(),
        },
        "_source": make_source(
            ["player_season_stats", "player_team_stints", "player_bio", "player_awards", "draft_history",
             "player_roles", "greats", "player_shots", "scouting_splits", "defender_dad", "player_gravity",
             "contract_value", "player_wpa_totals", "league_zone_mix"],
            "nba_api (stats.nba.com), Basketball-Reference via Kaggle, ESPN play-by-play, Kaggle salary datasets",
        ),
    }
