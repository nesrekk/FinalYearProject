"""On/off-court ratings for every player, from the play-by-play lines.

    GET /lineups/on-off?season=&team=&min_minutes=   one team's players (team given)
                                                      or the league's qualified players
    GET /lineups/on-off/stars?season=                 each team's top-usage player: the
                                                      team with him on vs. off the floor

Reads `player_on_off` (scripts/build_player_on_off.py): every minute of every
regular-season game 2020-21 to 2025-26 rebuilt from ESPN play-by-play, unlike
Pair Chemistry's stored top-2,000 lineups. Off-court = the team's game total
minus his on-court total, over the games he played. Each on-minus-off number
carries a game-clustered bootstrap 95% interval; rows under the minutes floor
are flagged, not hidden.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

DEFAULT_MIN_MINUTES = 500   # on-court minutes for "qualified"
STAR_MINUTES = 1000         # a team's top-usage player is chosen among players with this many on-court minutes
FEW_OFF_MINUTES = 300       # below this the off-court side is flagged as a small sample
MAX_MIN_MINUTES = 3000

TABLES = ["player_on_off", "team_game_totals", "player_on_off_seasons", "player_season_stats"]
UPSTREAM = "ESPN play-by-play (pbp_events), rebuilt into player lines by scripts/build_player_game_lines.py"

METHOD = (
    "Every regular-season game 2020-21 to 2025-26, rebuilt from ESPN play-by-play into who was on the floor for each "
    "event. On-court: his team's and the opponent's points and possessions while he was in the game. Off-court: the "
    "team's game totals minus his on-court totals, game by game, over the games he played (games he sat out entirely "
    "aren't counted as off-court minutes: a different roster played them). Possessions = FGA + 0.44 FTA - OREB + TOV, "
    "averaged over the two sides, the Basketball-Reference convention, so ratings run about 3 points under NBA.com's "
    "(which counts about 3% fewer possessions); on-minus-off differences don't depend on that scale. The 95% interval "
    "comes from resampling his games with replacement 2,000 times. This is descriptive on/off, not RAPM or any "
    "adjusted plus-minus: it credits him with everything his lineups did, including who he played with and against, "
    "and off-court numbers depend on the backups and on when coaches rest starters."
)

ROW_COLS = ["player_id", "season", "team_abbreviation", "games", "team_games", "minutes_on", "minutes_off",
            "poss_on", "poss_off", "pts_for_on", "pts_against_on", "pts_for_off", "pts_against_off",
            "ortg_on", "drtg_on", "net_on", "ortg_off", "drtg_off", "net_off", "on_off_ortg", "on_off_drtg",
            "on_off_net", "on_off_se", "on_off_ci_low", "on_off_ci_high", "net_on_se", "usg_pct"]


def _seasons(cur):
    cur.execute("""SELECT season, games, player_rows, players, tracked_share, league_net_on_weighted, league_ortg,
                          qualified, qualified_ci_excludes_zero, qualified_minutes, bootstraps
                   FROM player_on_off_seasons ORDER BY season""")
    keys = ["season", "games", "player_rows", "players", "tracked_share", "league_net_on_weighted", "league_ortg",
            "qualified", "qualified_ci_excludes_zero", "qualified_minutes", "bootstraps"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def _pick_season(cur, season):
    seasons = _seasons(cur)
    if not seasons:
        raise HTTPException(status_code=503, detail="No on/off data — run scripts/build_player_on_off.py.")
    available = [s["season"] for s in seasons]
    season = season or available[-1]
    if season not in available:
        raise HTTPException(status_code=404, detail=(
            f"No on/off data for {season}; the play-by-play lines cover {available[0]}-{available[-1]}."))
    return season, seasons


def _teams(cur, season):
    cur.execute("SELECT DISTINCT team_abbreviation FROM team_game_totals WHERE season = %s ORDER BY 1", (season,))
    return [r[0] for r in cur.fetchall()]


def _team_summary(cur, season, team):
    cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE win), SUM(pts_for), SUM(pts_against), SUM(poss),
                          SUM(tracked_seconds) / (5 * SUM(game_seconds))
                   FROM team_game_totals WHERE season = %s AND team_abbreviation = %s""", (season, team))
    games, wins, pf, pa, poss, tracked = cur.fetchone()
    if not games:
        return None
    poss = float(poss)
    return {
        "team": team, "games": games, "wins": wins, "losses": games - wins,
        "poss": round(poss, 1),
        "ortg": round(100 * pf / poss, 1), "drtg": round(100 * pa / poss, 1),
        "net_rating": round(100 * (pf - pa) / poss, 1),
        "tracked_share": round(float(tracked), 4),
    }


def _names(cur, player_ids):
    if not player_ids:
        return {}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                   WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (list(player_ids),))
    return dict(cur.fetchall())


def _row(r, names, min_minutes):
    d = dict(zip(ROW_COLS, r))
    for k, v in d.items():
        if isinstance(v, float):
            d[k] = round(v, 4 if k == "usg_pct" else 2)
    on, off = d["minutes_on"] or 0, d["minutes_off"] or 0
    d["player_name"] = names.get(d["player_id"])
    d["qualified"] = on >= min_minutes
    d["few_off_minutes"] = off < FEW_OFF_MINUTES
    d["minutes_share"] = round(on / (on + off), 3) if on + off else None
    lo, hi = d["on_off_ci_low"], d["on_off_ci_high"]
    d["ci_excludes_zero"] = None if lo is None else bool(lo > 0 or hi < 0)
    return d


def _rows(cur, season, min_minutes, team=None, floor_sql=None):
    """Rows for a season; `min_minutes` sets the qualified flag, `floor_sql`
    (default: the same) drops rows under it. A team view keeps every player
    and flags the short ones instead."""
    floor_sql = min_minutes if floor_sql is None else floor_sql
    sql = f"SELECT {', '.join(ROW_COLS)} FROM player_on_off WHERE season = %s AND minutes_on >= %s"
    params = [season, floor_sql]
    if team:
        sql += " AND team_abbreviation = %s"
        params.append(team)
    cur.execute(sql + " ORDER BY on_off_net DESC NULLS LAST, minutes_on DESC", params)
    raw = cur.fetchall()
    names = _names(cur, {r[0] for r in raw})
    return [_row(r, names, min_minutes) for r in raw]


def _stars(cur, season):
    """Each team's top-usage player among those with STAR_MINUTES on-court minutes."""
    cur.execute(f"""SELECT {', '.join(ROW_COLS)} FROM (
                        SELECT *, row_number() OVER (PARTITION BY team_abbreviation ORDER BY usg_pct DESC) AS rn
                        FROM player_on_off WHERE season = %s AND minutes_on >= %s AND usg_pct IS NOT NULL) s
                    WHERE rn = 1 ORDER BY on_off_net DESC NULLS LAST""", (season, STAR_MINUTES))
    raw = cur.fetchall()
    names = _names(cur, {r[0] for r in raw})
    return [_row(r, names, STAR_MINUTES) for r in raw]


def _season_note(info, min_minutes):
    """How many qualified intervals clear zero, against chance, at the stored floor."""
    if info is None or min_minutes != info["qualified_minutes"]:
        return None
    return {
        "qualified": info["qualified"],
        "ci_excludes_zero": info["qualified_ci_excludes_zero"],
        "expected_by_chance": round(0.05 * info["qualified"], 1),
    }


@router.get("/lineups/on-off")
def on_off(season: Optional[int] = None, team: Optional[str] = None, min_minutes: float = DEFAULT_MIN_MINUTES):
    min_minutes = max(0.0, min(float(min_minutes), MAX_MIN_MINUTES))
    with get_db() as conn:
        cur = conn.cursor()
        season, seasons = _pick_season(cur, season)
        info = next(s for s in seasons if s["season"] == season)
        teams = _teams(cur, season)
        team = team.upper() if team else None
        if team and team not in teams:
            raise HTTPException(status_code=404, detail=f"No games for {team} in {season}.")
        if team:
            rows = _rows(cur, season, min_minutes, team, floor_sql=0.0)
            summary = _team_summary(cur, season, team)
            stars = [r for r in rows if r["minutes_on"] >= STAR_MINUTES and r["usg_pct"] is not None]
            star = max(stars, key=lambda r: r["usg_pct"]) if stars else None
        else:
            rows = _rows(cur, season, min_minutes)
            summary = None
            star = None
    if team:
        qualified = sum(1 for r in rows if r["qualified"])
        excludes = sum(1 for r in rows if r["qualified"] and r["ci_excludes_zero"])
        noise = {"qualified": qualified, "ci_excludes_zero": excludes, "expected_by_chance": round(0.05 * qualified, 1)}
    else:
        noise = {
            "qualified": len(rows),
            "ci_excludes_zero": sum(1 for r in rows if r["ci_excludes_zero"]),
            "expected_by_chance": round(0.05 * len(rows), 1),
        }
    return {
        "season": season,
        "team": team,
        "seasons_available": [s["season"] for s in seasons],
        "teams": teams,
        "min_minutes": min_minutes,
        "star_minutes": STAR_MINUTES,
        "few_off_minutes": FEW_OFF_MINUTES,
        "season_summary": {
            "games": info["games"], "players": info["players"], "tracked_share": info["tracked_share"],
            "league_net_on_weighted": info["league_net_on_weighted"], "league_ortg": info["league_ortg"],
            "bootstraps": info["bootstraps"],
        },
        "team_summary": summary,
        "star": star,
        "noise": noise,
        "players": rows,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


@router.get("/lineups/on-off/stars")
def on_off_stars(season: Optional[int] = None):
    with get_db() as conn:
        cur = conn.cursor()
        season, seasons = _pick_season(cur, season)
        info = next(s for s in seasons if s["season"] == season)
        teams = _teams(cur, season)
        rows = _stars(cur, season)
        summaries = {t: _team_summary(cur, season, t) for t in teams}
    for r in rows:
        r["team"] = summaries[r["team_abbreviation"]]
    missing = sorted(set(teams) - {r["team_abbreviation"] for r in rows})
    return {
        "season": season,
        "seasons_available": [s["season"] for s in seasons],
        "star_minutes": STAR_MINUTES,
        "few_off_minutes": FEW_OFF_MINUTES,
        "season_summary": {"games": info["games"], "tracked_share": info["tracked_share"], "bootstraps": info["bootstraps"]},
        "teams_without_star": missing,
        "stars": rows,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }
