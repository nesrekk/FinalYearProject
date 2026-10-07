"""One week of a season's finals (round 9 step 5's Dashboard "This week", shared since round 9 step 6 with the weekly
guide report, api/weekly_report_lib.py): the results, the biggest upset by the held-out pre-game odds
(game_pregame_odds, the Best Games & Upsets page's rule) and the best game by excitement (best_games), for the days
start..through (inclusive). Takes a cursor; reads only."""


def _exists(cur, t):
    cur.execute("SELECT to_regclass(%s)", (t,))
    return cur.fetchone()[0] is not None


def week_games(cur, season, start, through):
    # one row a game: the home side's (at a neutral site both rows are is_home = false: the first by code)
    cur.execute("""SELECT * FROM (
                       SELECT DISTINCT ON (g.game_id) g.game_id, g.game_date, g.team_abbreviation, g.opponent, g.pts_for,
                              g.pts_against, g.periods, g.neutral_site, b.game_id AS replay_id
                       FROM game_scores g LEFT JOIN best_games b ON b.nba_game_id = g.game_id
                       WHERE g.season = %s AND g.game_date BETWEEN %s AND %s
                       ORDER BY g.game_id, g.is_home DESC, g.team_abbreviation) t
                   ORDER BY game_date DESC, game_id""", (season, start, through))
    results = [{"game_id": gid, "date": d.isoformat(), "home": h, "away": a, "pts_home": ph, "pts_away": pa,
                "periods": per, "neutral_site": bool(ns), "replay_id": rid}
               for gid, d, h, a, ph, pa, per, ns, rid in cur.fetchall()]
    upset = best = None
    if _exists(cur, "game_pregame_odds"):
        cur.execute("""SELECT o.game_id, o.game_date, o.home, o.away, o.pts_home, o.pts_away, o.p_home, o.home_won,
                              CASE WHEN o.home_won THEN o.p_home ELSE 1 - o.p_home END AS winner_p, b.game_id
                       FROM game_pregame_odds o LEFT JOIN best_games b ON b.nba_game_id = o.game_id
                       WHERE o.season = %s AND o.game_date BETWEEN %s AND %s AND o.home_won IS NOT NULL
                         AND ((o.home_won AND o.p_home < 0.5) OR (NOT o.home_won AND o.p_home > 0.5))
                       ORDER BY winner_p, o.game_id LIMIT 1""", (season, start, through))
        r = cur.fetchone()
        if r:
            gid, d, h, a, ph, pa, p_home, home_won, wp, rid = r
            upset = {"game_id": gid, "date": d.isoformat(), "home": h, "away": a, "pts_home": ph, "pts_away": pa,
                     "winner": h if home_won else a, "loser": a if home_won else h,
                     "winner_chance": round(float(wp), 3), "replay_id": rid}
    if _exists(cur, "best_games"):
        cur.execute("""SELECT game_id, nba_game_id, game_date, home_team, away_team, pts_home, pts_away, periods,
                              excitement, swing, lead_changes, comeback, peak_t, peak_event_id
                       FROM best_games WHERE season = %s AND game_date BETWEEN %s AND %s AND score_ok
                       ORDER BY excitement DESC, game_id LIMIT 1""", (season, start, through))
        r = cur.fetchone()
        if r:
            rid, gid, d, h, a, ph, pa, per, exc, swing, lc, cb, pt, pev = r
            best = {"replay_id": rid, "game_id": gid, "date": d.isoformat(), "home": h, "away": a,
                    "pts_home": ph, "pts_away": pa, "periods": per, "excitement": round(float(exc), 2),
                    "swing": round(float(swing), 2), "lead_changes": lc, "comeback": cb,
                    "peak_t": None if pt is None else float(pt), "peak_event_id": pev}
    return {"from": start.isoformat(), "through": through.isoformat(), "games": len(results), "results": results,
            "biggest_upset": upset, "best_game": best}
