"""Two-player chemistry grid: how a team did with each pair of its players
on the floor, from the stored 5-man lineups in `lineup_stats`.

`lineup_stats` holds the 2,000 lineups with the most minutes each season
(the cap of stats.nba.com's LeagueDashLineups), so a pair's numbers cover
only the minutes it played inside those lineups. Coverage is reported per
team and per player rather than hidden. Stored data is used on purpose:
Pair Synergy's live two-man fetch needs stats.nba.com, unreachable from
this machine since 2026-09-26.
"""

from itertools import combinations
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

MAX_PLAYERS_CHOICES = (8, 10, 12, 15)

METHOD = (
    "Built from the stored 5-man lineups (the 2,000 with the most minutes each regular season, the most the "
    "NBA's lineup endpoint returns). A pair's minutes and possessions are summed over every stored lineup that "
    "contains both players; its offensive and defensive ratings are those lineups' ratings weighted by "
    "possessions (points per 100 possessions, so this equals the pair's total points over total possessions in "
    "those lineups). Net = offense minus defence. Lineups outside the top 2,000, mostly short bench and "
    "garbage-time units, are missing, so starters are covered better than reserves, and the team's figure over "
    "the same lineups is usually better than its full-season net rating. Colours compare each pair to that "
    "same-lineup team figure. This describes what happened; it isn't adjusted for opponents or for the other "
    "three players on the floor."
)


def _rate(points, poss):
    return round(points / poss, 1) if poss else None


@router.get("/lineups/pair-grid")
def pair_grid(season: Optional[int] = None, team: Optional[str] = None,
              min_minutes: float = 100, max_players: int = 12):
    if max_players not in MAX_PLAYERS_CHOICES:
        raise HTTPException(status_code=400, detail=f"max_players must be one of {list(MAX_PLAYERS_CHOICES)}.")
    min_minutes = max(0.0, min(float(min_minutes), 2000.0))

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT season FROM lineup_stats ORDER BY season;")
        seasons = [r[0] for r in cur.fetchall()]
        if not seasons:
            raise HTTPException(status_code=503, detail="No stored lineups — run scripts/fetch_spacing_data.py.")
        season = season or seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"No stored lineups for {season}; "
                                                        f"stored seasons are {seasons[0]}-{seasons[-1]}.")

        cur.execute("SELECT DISTINCT team_abbreviation FROM lineup_stats WHERE season = %s ORDER BY 1;", (season,))
        teams = [r[0] for r in cur.fetchall()]
        team = (team or teams[0]).upper()
        if team not in teams:
            raise HTTPException(status_code=404, detail=f"No stored lineups for {team} in {season}.")

        cur.execute(
            """SELECT player_ids, minutes, poss, off_rating, def_rating
               FROM lineup_stats WHERE season = %s AND team_abbreviation = %s;""",
            (season, team),
        )
        lineups = cur.fetchall()

        cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE win) FROM team_game_fatigue
                       WHERE season = %s AND team_abbreviation = %s;""", (season, team))
        games, wins = cur.fetchone()

        # Per player: [minutes, poss, off points x100, def points x100, lineups]
        solo, pairs = {}, {}
        team_tot = [0.0, 0, 0.0, 0.0]
        for ids, minutes, poss, off, dfn in lineups:
            team_tot[0] += minutes; team_tot[1] += poss; team_tot[2] += off * poss; team_tot[3] += dfn * poss
            for pid in ids:
                a = solo.setdefault(pid, [0.0, 0, 0.0, 0.0, 0])
                a[0] += minutes; a[1] += poss; a[2] += off * poss; a[3] += dfn * poss; a[4] += 1
            for pa, pb in combinations(sorted(ids), 2):
                a = pairs.setdefault((pa, pb), [0.0, 0, 0.0, 0.0, 0])
                a[0] += minutes; a[1] += poss; a[2] += off * poss; a[3] += dfn * poss; a[4] += 1

        shown = sorted(solo, key=lambda pid: -solo[pid][0])[:max_players]
        cur.execute(
            """SELECT player_id, player_name, team_abbreviation, gp, min
               FROM player_season_stats WHERE season = %s AND player_id = ANY(%s);""",
            (season, [int(p) for p in solo]),
        )
        info = {r[0]: r[1:] for r in cur.fetchall()}

    def block(acc):
        off, dfn = _rate(acc[2], acc[1]), _rate(acc[3], acc[1])
        return {
            "minutes": round(acc[0], 1),
            "poss": int(acc[1]),
            "lineups": acc[4] if len(acc) > 4 else None,
            "off_rating": off,
            "def_rating": dfn,
            "net_rating": round(off - dfn, 1) if off is not None else None,
        }

    players = []
    for pid in shown:
        name, last_team, gp, mpg = info.get(pid, (None, None, None, None))
        season_minutes = round(gp * mpg) if gp and mpg else None
        players.append({
            "player_id": pid,
            "player_name": name,
            # player_season_stats keeps one row per player-season (all teams
            # combined, listed under the last team), so a traded player's
            # season minutes include time with other teams.
            "season_minutes_all_teams": season_minutes,
            "listed_team": last_team,
            **block(solo[pid]),
        })
    index = {pid: i for i, pid in enumerate(shown)}

    cells = []
    for (pa, pb), acc in pairs.items():
        if pa in index and pb in index:
            if index[pa] > index[pb]:
                pa, pb = pb, pa
            cells.append({
                "a": pa, "b": pb,
                "qualified": acc[0] >= min_minutes,
                **block(acc),
            })
    cells.sort(key=lambda c: (index[c["a"]], index[c["b"]]))

    regulation_minutes = games * 48
    team_block = block(team_tot + [len(lineups)])
    return {
        "season": season,
        "team": team,
        "seasons_available": seasons,
        "teams": teams,
        "min_minutes": min_minutes,
        "max_players": max_players,
        "max_players_choices": list(MAX_PLAYERS_CHOICES),
        "team_summary": {
            **team_block,
            "games": games,
            "wins": wins,
            "losses": games - wins,
            "regulation_minutes": regulation_minutes,
            # Stored lineup minutes over 48 x games; overtime makes the true
            # denominator slightly larger, so this slightly overstates coverage.
            "coverage": round(team_tot[0] / regulation_minutes, 3) if regulation_minutes else None,
            "players_in_stored_lineups": len(solo),
        },
        "players": players,
        "pairs": cells,
        "method": METHOD,
        "_source": make_source(["lineup_stats", "player_season_stats", "team_game_fatigue"],
                               "nba_api (stats.nba.com LeagueDashLineups, stored by scripts/fetch_spacing_data.py)"),
    }
