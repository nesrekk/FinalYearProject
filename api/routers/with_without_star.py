from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from source_badge import make_source

from impact_core import (
    TEAM_ABBR_TO_ID,
    _fetch_player_game_ids,
    _fetch_team_game_log,
    get_db,
    resolve_player,
)

router = APIRouter()


@lru_cache(maxsize=1)
def stored_line_seasons():
    """Seasons with per-game player lines on file (player_game_lines, 2020-21 on). Cached per process:
    restart impact_api after build_player_game_lines.py."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.player_game_lines'), to_regclass('public.game_scores');")
        if None in cur.fetchone():
            return frozenset()
        cur.execute("SELECT DISTINCT season FROM player_game_lines;")
        return frozenset(int(r[0]) for r in cur.fetchall())


def _stored_split(cursor, team_abbr: str, season: int, player_id: int):
    """Every regular-season game of the team that season from game_scores (the real final scores),
    split by whether the player has a line with minutes for that team on that date in the
    play-by-play game lines. The join is on team + date, the Game Log's rule, so the three NBA Cup
    finals (not regular-season games) stay out."""
    cursor.execute(
        """
        SELECT g.game_date, g.pts_for > g.pts_against AS win, g.pts_for - g.pts_against AS margin,
               EXISTS (SELECT 1 FROM player_game_lines l
                       WHERE l.season = g.season AND l.player_id = %s AND l.team_abbreviation = g.team_abbreviation
                         AND l.game_date = g.game_date AND l.seconds > 0) AS played
        FROM game_scores g
        WHERE g.season = %s AND g.team_abbreviation = %s
        ORDER BY g.game_date;
        """,
        (player_id, season, team_abbr),
    )
    return [{"wl": "W" if win else "L", "plus_minus": float(margin), "played": played}
            for _, win, margin, played in cursor.fetchall()]


def _summarize(games):
    n = len(games)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "win_pct": None, "avg_point_diff": None}
    wins = sum(1 for g in games if g["wl"] == "W")
    diffs = [g["plus_minus"] for g in games if g["plus_minus"] is not None]
    avg_diff = sum(diffs) / len(diffs) if diffs else None
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "win_pct": round(wins / n, 3),
        "avg_point_diff": round(avg_diff, 2) if avg_diff is not None else None,
    }


@router.get("/teams/with-without/{team_abbr}/{season}")
def get_with_without_star(team_abbr: str, season: int, player_name: str, player_id: Optional[int] = None):
    """A team's record and average margin with and without one player. Seasons with stored game
    lines (2020-21 on) are read from the stored tables: wins and margins from game_scores (the real
    final scores), who played from player_game_lines. Earlier seasons still need stats.nba.com's live
    game logs (3 s timeout; its per-game point differential is the summed player plus-minus / 5, not
    the final margin, README Known real gaps). Round 8 step 4 (R8-008, R8-021)."""
    team_abbr = team_abbr.upper()
    team_id = TEAM_ABBR_TO_ID.get(team_abbr)
    if team_id is None:
        raise HTTPException(status_code=400, detail=f"Unknown team abbreviation '{team_abbr}'.")

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_id, resolved_name = resolve_player(cursor, player_name, player_id)
        stored = season in stored_line_seasons()
        if stored:
            games = _stored_split(cursor, team_abbr, season, resolved_id)
            if not games:
                raise HTTPException(status_code=404, detail=f"No stored games for {team_abbr} in season {season}.")
            with_games = [g for g in games if g["played"]]
            without_games = [g for g in games if not g["played"]]

    if not stored:
        team_games = _fetch_team_game_log(team_id, season)
        if not team_games:
            raise HTTPException(status_code=404, detail=f"No real games found for {team_abbr} in season {season}.")
        played_game_ids = _fetch_player_game_ids(resolved_id, season, team_id)
        with_games = [g for g in team_games if g["game_id"] in played_game_ids]
        without_games = [g for g in team_games if g["game_id"] not in played_game_ids]

    label = f"{season - 1}-{str(season)[-2:]}"
    if stored:
        methodology = (
            f"Every {team_abbr} regular-season game of {label} from the stored final scores (game_scores), split by "
            f"whether {resolved_name} has minutes for {team_abbr} that day in the play-by-play game lines "
            "(player_game_lines). Average point differential is the real final margin. This is an association, "
            "not a causal claim: other players being in or out of the lineup for the same games also affects "
            "the result, and this comparison doesn't control for that. Both sample sizes are always shown; a "
            "player who rarely sits has a small, noisy 'without' sample."
        )
        source = make_source(["game_scores", "player_game_lines"], "ESPN final scores + ESPN play-by-play (stored)")
    else:
        methodology = (
            f"Real {team_abbr} team game log and real {resolved_name} game log for {label}, fetched live from "
            "stats.nba.com (stored game lines start in 2020-21). Its per-game point differential is the summed "
            "player plus-minus divided by 5, which differs from the final margin in a few games a season. This "
            "is an association, not a causal claim: other players being in or out of the lineup for the same "
            "games also affects the result, and this comparison doesn't control for that. Both sample sizes are "
            "always shown; a player who rarely sits has a small, noisy 'without' sample."
        )
        source = make_source([], "nba_api (stats.nba.com, live game logs)", live=True)

    return {
        "team_abbreviation": team_abbr,
        "season": season,
        "player_id": resolved_id,
        "player_name": resolved_name,
        "source": "stored" if stored else "live",
        "games": len(with_games) + len(without_games),
        "with_player": _summarize(with_games),
        "without_player": _summarize(without_games),
        "methodology": methodology,
        "_source": source,
    }
