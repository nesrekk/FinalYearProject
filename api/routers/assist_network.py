"""Assist network: who assists whom, and how much of each player's scoring is assisted.

    GET /assists/options                      seasons, teams per season, the league's assisted shares
    GET /assists/team?team=&season=           a team-season: every passer -> scorer pair (the network),
                                              every player's assists and assisted shares
    GET /assists/pairs?season=&sort=&limit=   the league's top passer -> scorer duos in a season
    GET /assists/player/{id}?season=          one player: top targets, top feeders, assisted shares
                                              against the league, every season on file

Reads `assist_pairs`, `player_assisted_share` and `assist_seasons`
(scripts/build_assist_network.py): every made field goal of every
regular-season game 2020-21 on from the ESPN play-by-play, through the same
parser as player_game_lines (a player's assists here equal his Game Log's),
with the passer named in the text ("(X assists)") matched to an NBA id the
same way. Everything is cached per process: restart impact_api after
rerunning the script.
"""

from collections import defaultdict
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source
from teams_lib import lookup_codes

router = APIRouter()

TABLES = ["assist_pairs", "player_assisted_share", "assist_seasons", "player_game_lines"]
UPSTREAM = "ESPN play-by-play (pbp_events), parsed by scripts/pbp_lineups.py (build_assist_network.py)"

KINDS = [
    {"id": "rim", "label": "Layups & dunks", "long": "Layups, dunks, alley-oops, finger rolls, tips"},
    {"id": "floater", "label": "Floaters & hooks", "long": "Floaters and hook shots"},
    {"id": "jumper", "label": "2-pt jumpers", "long": "Every other two: jump shots, pull-ups, fadeaways"},
    {"id": "three", "label": "Threes", "long": "Made threes"},
]
SORTS = {"ast": "Assists", "pts": "Points on the assisted shots", "three": "Assisted threes", "rim": "Layups & dunks"}
MIN_FGM2 = 50        # assisted share of 2s greyed under this many made 2s
MIN_FGM3 = 25        # ... of 3s under this many made 3s
RANK_FGM2 = 100      # league ranks of assisted shares among players with this many made 2s
RANK_FGM3 = 50       # ... made 3s
TOP = 8

METHOD = (
    "Every made field goal of every regular-season game since 2020-21, from ESPN's play-by-play. ESPN names the passer "
    "in the made shot's text (\"Nikola Jokic makes 2-foot dunk (Jamal Murray assists)\"); the passer's name is matched "
    "to an NBA id by the same parser that builds the Game Log, so every player's assist total here equals his Game Log's "
    "(checked: 0 player-seasons differ), and the parser's season assists are within 0.3% of NBA.com's. A basket is two "
    "or three as the parser calls it; its kind comes from ESPN's shot type: layups and dunks (with alley-oops, finger "
    "rolls and tips), floaters and hooks, other two-point jumpers, threes. Left out of the pairs: assists whose passer "
    "ESPN gives no id to (about 0.3%; the basket still counts as assisted for the scorer) and 4 plays that are plain "
    "data errors (3 players credited with assisting themselves). A basket by a player with no id is kept as "
    "\"Unidentified\". Assisted share = assisted makes / all makes of that kind. An assist is the scorekeeper's call: "
    "home scorekeepers are known to differ in how generously they give them."
)


def _label(s):
    return f"{s - 1}-{str(s)[-2:]}"


def _ratio(a, b, d=4):
    return round(a / b, d) if b else None


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


@lru_cache(maxsize=1)
def _data():
    """(seasons, share rows, pairs by (season, team), names) — the tables are small (~6 MB)."""
    with get_db() as conn:
        cur = conn.cursor()
        if not all(_exists(cur, t) for t in ("assist_seasons", "assist_pairs", "player_assisted_share")):
            return None
        cur.execute("SELECT * FROM assist_seasons ORDER BY season")
        cols = [c[0] for c in cur.description]
        seasons = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
        cur.execute("SELECT * FROM player_assisted_share")
        cols = [c[0] for c in cur.description]
        shares = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT * FROM assist_pairs")
        cols = [c[0] for c in cur.description]
        pairs = defaultdict(list)
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            pairs[(d["season"], d["team_abbreviation"])].append(d)
        ids = sorted({r["player_id"] for r in shares})
        cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                       WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (ids,))
        names = dict(cur.fetchall())
        missing = [i for i in ids if i not in names]
        if missing and _exists(cur, "player_bio"):
            cur.execute("SELECT player_id, player_name FROM player_bio WHERE player_id = ANY(%s)", (missing,))
            names.update(dict(cur.fetchall()))
    return seasons, tuple(shares), dict(pairs), names


def _need():
    d = _data()
    if d is None:
        raise HTTPException(status_code=503, detail="No assist data: run scripts/build_assist_network.py.")
    return d


def _name(names, pid):
    return "Unidentified" if pid == 0 else names.get(pid, f"#{pid}")


def _league(s):
    """A season's league-wide assisted shares."""
    out = {"season": s["season"], "season_label": _label(s["season"]), "games": s["games"], "fgm": s["fgm"],
           "assisted": s["assisted"], "assisted_share": _ratio(s["assisted"], s["fgm"]),
           "share2": _ratio(s["ast_fgm2"], s["fgm2"]), "share3": _ratio(s["ast_fgm3"], s["fgm3"]),
           "unknown_passer": s["unknown_passer"], "unknown_scorer": s["unknown_scorer"],
           "data_errors": s["data_errors"], "lines_mismatch": s["lines_mismatch"], "ast_vs_nba": s["ast_vs_nba"]}
    for k in ("rim", "floater", "jumper"):
        out[f"share_{k}"] = _ratio(s[f"ast_{k}"], s[f"fgm_{k}"])
    out["share_three"] = out["share3"]
    return out


def _player_row(r, names):
    out = {k: r[k] for k in ("player_id", "team_abbreviation", "season", "games", "fgm", "fgm2", "ast_fgm2", "fgm3",
                             "ast_fgm3", "ast", "ast_pts", "ast3_given", "ast_unknown_passer")}
    out["player_name"] = _name(names, r["player_id"])
    out["minutes"] = round(r["minutes"] or 0.0, 1)
    out["share2"] = _ratio(r["ast_fgm2"], r["fgm2"])
    out["share3"] = _ratio(r["ast_fgm3"], r["fgm3"])
    out["share"] = _ratio(r["ast_fgm2"] + r["ast_fgm3"], r["fgm"])
    out["kinds"] = {k: {"fgm": r[f"fgm_{k}"], "assisted": r[f"ast_{k}"], "share": _ratio(r[f"ast_{k}"], r[f"fgm_{k}"])}
                    for k in ("rim", "floater", "jumper")}
    out["kinds"]["three"] = {"fgm": r["fgm3"], "assisted": r["ast_fgm3"], "share": out["share3"]}
    out["small2"] = r["fgm2"] < MIN_FGM2
    out["small3"] = r["fgm3"] < MIN_FGM3
    return out


def _edge(p, names):
    return {"passer_id": p["passer_id"], "passer_name": _name(names, p["passer_id"]),
            "scorer_id": p["scorer_id"], "scorer_name": _name(names, p["scorer_id"]),
            "team_abbreviation": p["team_abbreviation"], "season": p["season"],
            "ast": p["ast"], "games": p["games"], "ast2": p["ast2"], "ast3": p["ast3"], "pts": p["pts"],
            "kinds": {"rim": p["rim"], "floater": p["floater"], "jumper": p["jumper"], "three": p["ast3"]}}


@lru_cache(maxsize=16)
def _share_ranks(season):
    """(player_id -> {share2: (rank, n), share3: (rank, n)}), rank 1 = most assisted, over whole seasons
    (a traded player's teams summed), among players with enough makes."""
    _, shares, _, _ = _need()
    tot = defaultdict(lambda: defaultdict(int))
    for r in shares:
        if r["season"] == season:
            for k in ("fgm2", "ast_fgm2", "fgm3", "ast_fgm3"):
                tot[r["player_id"]][k] += r[k]
    out = defaultdict(dict)
    for key, made, floor in (("share2", "fgm2", RANK_FGM2), ("share3", "fgm3", RANK_FGM3)):
        pool = sorted(((t[f"ast_{made}"] / t[made], pid) for pid, t in tot.items() if t[made] >= floor), reverse=True)
        for i, (_, pid) in enumerate(pool):
            out[pid][key] = (i + 1, len(pool))
    return dict(out)


def _team_code(pairs, season, team):
    codes = set(lookup_codes(team)) | {team.upper()}
    for c in sorted(codes):
        if (season, c) in pairs:
            return c
    return None


@lru_cache(maxsize=1)
def _options():
    seasons, shares, pairs, _ = _need()
    teams = defaultdict(set)
    for s, t in pairs:
        teams[s].add(t)
    team_share = defaultdict(dict)
    for r in shares:
        d = team_share[r["season"]].setdefault(r["team_abbreviation"], [0, 0])
        d[0] += r["ast_fgm2"] + r["ast_fgm3"]
        d[1] += r["fgm"]
    return {
        "seasons": sorted(seasons), "default_season": max(seasons),
        "teams": {str(s): sorted(v) for s, v in teams.items()},
        "league": {str(s): _league(v) for s, v in seasons.items()},
        "team_shares": {str(s): {t: _ratio(a, b) for t, (a, b) in v.items()} for s, v in team_share.items()},
        "kinds": KINDS, "sorts": SORTS,
    }


@router.get("/assists/options")
def assist_options():
    return {**_options(), "method": METHOD, "_source": make_source(TABLES, UPSTREAM)}


@router.get("/assists/team")
def assist_team(team: str, season: Optional[int] = None):
    seasons, shares, pairs, names = _need()
    season = season or max(seasons)
    if season not in seasons:
        raise HTTPException(status_code=404, detail=(
            f"No assist data for {_label(season)}; the play-by-play covers {_label(min(seasons))} to "
            f"{_label(max(seasons))}."))
    code = _team_code(pairs, season, team)
    if code is None:
        raise HTTPException(status_code=404, detail=f"No {team.upper()} games in the {_label(season)} play-by-play.")
    players = [_player_row(r, names) for r in shares if r["season"] == season and r["team_abbreviation"] == code]
    players = [p for p in players if p["minutes"] > 0 or p["fgm"] > 0 or p["ast"] > 0]
    players.sort(key=lambda p: -p["minutes"])
    edges = sorted((_edge(p, names) for p in pairs[(season, code)]), key=lambda e: -e["ast"])
    received = defaultdict(int)
    for e in edges:
        received[e["scorer_id"]] += e["ast"]
    for p in players:
        mine_out = [e for e in edges if e["passer_id"] == p["player_id"]]
        mine_in = [e for e in edges if e["scorer_id"] == p["player_id"]]
        p["top_target"] = {"player_id": mine_out[0]["scorer_id"], "player_name": mine_out[0]["scorer_name"],
                           "ast": mine_out[0]["ast"]} if mine_out else None
        p["top_feeder"] = {"player_id": mine_in[0]["passer_id"], "player_name": mine_in[0]["passer_name"],
                           "ast": mine_in[0]["ast"]} if mine_in else None
        p["received"] = received.get(p["player_id"], 0)
    fgm = sum(p["fgm"] for p in players)
    assisted = sum(p["ast_fgm2"] + p["ast_fgm3"] for p in players)
    unknown_scorer = sum(e["ast"] for e in edges if e["scorer_id"] == 0)
    team_shares = _options()["team_shares"][str(season)]
    order = sorted((v for v in team_shares.values() if v is not None), reverse=True)
    share = _ratio(assisted, fgm)
    return {
        "team": code, "season": season, "season_label": _label(season),
        "totals": {"fgm": fgm, "assisted": assisted, "share": share,
                   "rank": order.index(team_shares[code]) + 1 if code in team_shares else None, "n_teams": len(order),
                   "assists_identified": sum(e["ast"] for e in edges),
                   "unknown_passer": sum(p["ast_unknown_passer"] for p in players),
                   "unknown_scorer": unknown_scorer,
                   "games": max((p["games"] for p in players), default=0)},
        "league": _league(seasons[season]),
        "players": players, "edges": edges,
        "kinds": KINDS, "min_fgm2": MIN_FGM2, "min_fgm3": MIN_FGM3,
        "method": METHOD, "_source": make_source(TABLES, UPSTREAM),
    }


@router.get("/assists/pairs")
def assist_pairs(season: Optional[int] = None, sort: str = "ast", limit: int = 50):
    seasons, _, pairs, names = _need()
    season = season or max(seasons)
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No assist data for {_label(season)}.")
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {', '.join(SORTS)}.")
    limit = max(1, min(int(limit), 200))
    key = {"ast": "ast", "pts": "pts", "three": "ast3", "rim": "rim"}[sort]
    rows = [p for (s, _), ps in pairs.items() if s == season for p in ps if p["scorer_id"] != 0]
    rows.sort(key=lambda p: (-p[key], -p["ast"]))
    return {"season": season, "season_label": _label(season), "sort": sort, "sorts": SORTS,
            "n_pairs": len(rows), "pairs": [_edge(p, names) for p in rows[:limit]],
            "league": _league(seasons[season]), "method": METHOD, "_source": make_source(TABLES, UPSTREAM)}


def player_seasons(player_id):
    """Seasons with made shots or assists on file, for /player-profile/{id}."""
    d = _data()
    if d is None:
        return []
    return sorted({r["season"] for r in d[1] if r["player_id"] == player_id and (r["fgm"] or r["ast"])})


@router.get("/assists/player/{player_id}")
def assist_player(player_id: int, season: Optional[int] = None):
    seasons, shares, pairs, names = _need()
    mine = [r for r in shares if r["player_id"] == player_id and (r["fgm"] or r["ast"])]
    if not mine:
        raise HTTPException(status_code=404, detail="No made shots or assists in the play-by-play for this player.")
    available = sorted({r["season"] for r in mine})
    season = season if season in available else available[-1]
    rows = [r for r in mine if r["season"] == season]
    teams = [r["team_abbreviation"] for r in sorted(rows, key=lambda r: -(r["minutes"] or 0))]
    total = {k: sum(r[k] for r in rows) for k in rows[0] if k not in ("season", "team_abbreviation", "player_id")}
    total.update(season=season, team_abbreviation="/".join(teams), player_id=player_id)
    summary = _player_row(total, names)
    ranks = _share_ranks(season).get(player_id, {})
    for k in ("share2", "share3"):
        summary[f"{k}_rank"], summary[f"{k}_ranked"] = ranks.get(k, (None, None))
    targets, feeders = [], []
    for t in teams:
        for p in pairs.get((season, t), []):
            if p["passer_id"] == player_id:
                targets.append(_edge(p, names))
            if p["scorer_id"] == player_id:
                feeders.append(_edge(p, names))
    targets.sort(key=lambda e: -e["ast"])
    feeders.sort(key=lambda e: -e["ast"])
    history = []
    for s in available:
        rs = [r for r in mine if r["season"] == s]
        t = {k: sum(r[k] for r in rs) for k in ("fgm", "fgm2", "ast_fgm2", "fgm3", "ast_fgm3", "ast", "ast_pts")}
        best = None
        for r in rs:
            for p in pairs.get((s, r["team_abbreviation"]), []):
                if p["passer_id"] == player_id and (best is None or p["ast"] > best["ast"]):
                    best = p
        history.append({"season": s, "season_label": _label(s),
                         "teams": [r["team_abbreviation"] for r in sorted(rs, key=lambda r: -(r["minutes"] or 0))],
                         "minutes": round(sum(r["minutes"] or 0 for r in rs), 1), **t,
                         "share2": _ratio(t["ast_fgm2"], t["fgm2"]), "share3": _ratio(t["ast_fgm3"], t["fgm3"]),
                         "small2": t["fgm2"] < MIN_FGM2, "small3": t["fgm3"] < MIN_FGM3,
                         "top_target": None if best is None else {"player_id": best["scorer_id"],
                                                                  "player_name": _name(names, best["scorer_id"]),
                                                                  "ast": best["ast"]}})
    return {
        "player_id": player_id, "player_name": _name(names, player_id), "season": season,
        "season_label": _label(season), "seasons": available, "teams": teams,
        "summary": summary, "league": _league(seasons[season]),
        "targets": targets[:TOP], "feeders": feeders[:TOP], "n_targets": len(targets), "n_feeders": len(feeders),
        "history": history, "kinds": KINDS, "min_fgm2": MIN_FGM2, "min_fgm3": MIN_FGM3,
        "rank_floor": {"share2": RANK_FGM2, "share3": RANK_FGM3},
        "method": METHOD, "_source": make_source(TABLES, UPSTREAM),
    }
