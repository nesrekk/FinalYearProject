"""Two-player chemistry grid: how a team did with each pair of its players
on the floor.

Two sources, chosen by season (api/lineups_lib.py): from 2020-21 on, every
five-on-five stint rebuilt from ESPN play-by-play (`lineup_seasons` and
`pair_seasons`, scripts/build_lineup_stints.py), covering every minute of
every game whose play-by-play reconciled; before that, the stored 5-man
lineups in `lineup_stats` (the 2,000 with the most minutes each season, the
cap of stats.nba.com's LeagueDashLineups), which cover only 31-89% of a
team's minutes. The response says which source it used and, for the stints,
which games were excluded and why; coverage is reported per team and per
player rather than hidden.
"""

from itertools import combinations
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from lineups_lib import (STINT_TABLES, STINT_UPSTREAM, STINTS_METHOD, STORED_METHOD, STORED_UPSTREAM, SOURCE_LABEL,
                         lineup_sources, season_label, stint_seasons, team_stint_coverage)
from source_badge import make_source

router = APIRouter()

MAX_PLAYERS_CHOICES = (8, 10, 12, 15)


def _rate(points, poss):
    return round(100.0 * points / poss, 1) if poss else None


def _acc(store, key, minutes, poss, pts_for, pts_against, n=1):
    a = store.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0])
    a[0] += minutes
    a[1] += poss
    a[2] += pts_for
    a[3] += pts_against
    a[4] += n


def _load_stored(cur, season, team):
    """lineup_stats: (lineups, pairs, teams) with points recovered from the ratings."""
    cur.execute("SELECT DISTINCT team_abbreviation FROM lineup_stats WHERE season = %s ORDER BY 1;", (season,))
    teams = [r[0] for r in cur.fetchall()]
    if team not in teams:
        return None, None, teams
    cur.execute("""SELECT player_ids, minutes, poss, off_rating, def_rating
                   FROM lineup_stats WHERE season = %s AND team_abbreviation = %s;""", (season, team))
    lineups = [(ids, float(minutes), float(poss), off * poss / 100.0, dfn * poss / 100.0)
               for ids, minutes, poss, off, dfn in cur.fetchall()]
    pairs = {}
    for ids, minutes, poss, pf, pa in lineups:
        for pa_, pb_ in combinations(sorted(ids), 2):
            _acc(pairs, (pa_, pb_), minutes, poss, pf, pa)
    return lineups, pairs, teams


def _load_stints(cur, season, team):
    """lineup_seasons / pair_seasons (tracked games only)."""
    cur.execute("SELECT DISTINCT team_abbreviation FROM lineup_seasons WHERE season = %s ORDER BY 1;", (season,))
    teams = [r[0] for r in cur.fetchall()]
    if team not in teams:
        return None, None, teams
    cur.execute("""SELECT player_ids, minutes, poss, pts_for, pts_against
                   FROM lineup_seasons WHERE season = %s AND team_abbreviation = %s;""", (season, team))
    lineups = [(list(ids), float(minutes), float(poss), float(pf), float(pa)) for ids, minutes, poss, pf, pa in cur.fetchall()]
    cur.execute("""SELECT player_a, player_b, minutes, poss, pts_for, pts_against, lineups
                   FROM pair_seasons WHERE season = %s AND team_abbreviation = %s;""", (season, team))
    pairs = {(a, b): [float(m), float(p), float(pf), float(pa), int(n)] for a, b, m, p, pf, pa, n in cur.fetchall()}
    return lineups, pairs, teams


@router.get("/lineups/pair-grid")
def pair_grid(season: Optional[int] = None, team: Optional[str] = None,
              min_minutes: float = 100, max_players: int = 12):
    if max_players not in MAX_PLAYERS_CHOICES:
        raise HTTPException(status_code=400, detail=f"max_players must be one of {list(MAX_PLAYERS_CHOICES)}.")
    min_minutes = max(0.0, min(float(min_minutes), 2000.0))

    sources = lineup_sources()
    seasons = list(sources)
    if not seasons:
        raise HTTPException(status_code=503, detail="No stored lineups — run scripts/build_lineup_stints.py "
                                                    "or scripts/fetch_spacing_data.py.")
    season = season or seasons[-1]
    if season not in sources:
        raise HTTPException(status_code=404, detail=f"No stored lineups for {season}; "
                                                    f"stored seasons are {seasons[0]}-{seasons[-1]}.")
    source = sources[season]

    with get_db() as conn:
        cur = conn.cursor()
        load = _load_stints if source == "stints" else _load_stored
        if team:
            team = team.upper()
        lineups, pairs, teams = load(cur, season, team or "")
        if not team:
            team = teams[0]
            lineups, pairs, teams = load(cur, season, team)
        if lineups is None:
            raise HTTPException(status_code=404, detail=f"No stored lineups for {team} in {season}.")

        cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE win) FROM team_game_fatigue
                       WHERE season = %s AND team_abbreviation = %s;""", (season, team))
        games, wins = cur.fetchone()
        coverage = team_stint_coverage(cur, season, team) if source == "stints" else None

        # Per player: [minutes, poss, points for, points against, lineups]
        solo = {}
        team_tot = [0.0, 0.0, 0.0, 0.0, 0]
        for ids, minutes, poss, pf, pa in lineups:
            for i, v in enumerate((minutes, poss, pf, pa, 1)):
                team_tot[i] += v
            for pid in ids:
                _acc(solo, pid, minutes, poss, pf, pa)

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
            "poss": int(round(acc[1])),
            "lineups": acc[4],
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
    team_block = block(team_tot)
    if source == "stints":
        cov_share = coverage["share"]
        players_covered = len(solo)
        parts = [f"Every minute of {coverage['tracked_games']} of its {coverage['games']} games is tracked"]
        if coverage["excluded"]:
            parts.append(f"{len(coverage['excluded'])} game(s) excluded because the play-by-play didn't reconcile")
        if coverage["partial"]:
            parts.append(f"{coverage['partial_minutes']:.0f} minutes in {len(coverage['partial'])} game(s) left out "
                         "because a player on the floor had no id in the play-by-play")
        note = "; ".join(parts) + "."
    else:
        # Stored lineup minutes over 48 x games; overtime makes the true
        # denominator slightly larger, so this slightly overstates coverage.
        cov_share = round(team_tot[0] / regulation_minutes, 3) if regulation_minutes else None
        players_covered = len(solo)
        note = ("Only the league's 2,000 most-used lineups a season are stored, so bench units are under-counted "
                "and the team's figure over them runs above its real net rating.")
    return {
        "season": season,
        "team": team,
        "seasons_available": seasons,
        "sources": {str(s): src for s, src in sources.items()},
        "source": source,
        "source_label": SOURCE_LABEL[source],
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
            "coverage": cov_share,
            "players_in_stored_lineups": players_covered,
            "note": note,
        },
        "coverage": coverage,
        "season_check": stint_seasons().get(season) if source == "stints" else None,
        "players": players,
        "pairs": cells,
        "method": STINTS_METHOD if source == "stints" else STORED_METHOD,
        "_source": make_source(
            (["lineup_seasons", "pair_seasons", "lineup_stint_games"] if source == "stints" else ["lineup_stats"])
            + ["player_season_stats", "team_game_fatigue"],
            STINT_UPSTREAM if source == "stints" else STORED_UPSTREAM),
    }
