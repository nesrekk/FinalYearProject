"""
build_player_on_off.py
=======================
On/off-court ratings for every player, every regular-season game 2020-21 to
2025-26, from the play-by-play lines in `player_game_lines` (every minute of
every game, unlike `lineup_stats`, which stores only each season's 2,000
most-used five-man lineups).

What it computes, per player, season and team:
  on-court   points for and against, and possessions, while he was on the
             floor (the `tm_*` / `op_*` columns of his game lines);
  off-court  his team's game totals minus his on-court totals, game by game,
             over the games he played (games he missed entirely are not
             "off-court" minutes: a different roster played them, which is
             what the With/Without a Star tool measures);
  ratings    points per 100 possessions on and off (ORtg, DRtg, net) and the
             on-minus-off differences;
  noise      a game-clustered bootstrap of on minus off net rating (his
             games are resampled with replacement 2,000 times), giving a
             standard error and a 95% interval;
  usage      his share of his team's shooting possessions while on the floor
             (FGA + 0.44 FTA + TOV), to name each team's top-usage player.

Possessions are the Basketball-Reference convention: FGA + 0.44 FTA - OREB
+ TOV, averaged over the two sides (a stint's two sides differ by a
possession or so because stints start and end mid-possession). Team totals
come from the same ESPN play-by-play (`pbp_events`, source 'espn') with the
same event rules as build_player_game_lines.py, so on + off always equals
the team's game total: field-goal and free-throw attempts and turnovers
(including team turnovers) are counted per team from the events, offensive
rebounds are player rebounds only (team rebounds aren't credited to anyone
in the lines), points are the final score. Two free throws in six seasons
carry no team in the ESPN feed and are left out.

Tables written (all dropped and rebuilt):
  team_game_totals        one row per team-game: points for/against, the
                          possession components for both sides, possessions,
                          game seconds (overtime included) and the share of
                          player-seconds the rebuilt lineups tracked;
  player_on_off           one row per player-season-team (see columns below);
  player_on_off_seasons   per season: rows, tracked share, league checks.

Checks printed at the end (the README quotes them):
  * on + off possessions equal the team's game total by construction; the
    lineups' tracked share of player-seconds is reported per season;
  * the possession-weighted league mean of on-court net rating is ~0 (each
    possession's margin is credited to the five players on each side);
  * how many qualified players' 95% intervals exclude zero, against the 5%
    expected by chance;
  * known cases: Jokić's on-court net each season, and each season's top
    on-minus-off among 1,000+ minute players.

Usage:
    cd scripts && python3 build_player_on_off.py
"""

import time

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

BOOTSTRAPS = 2000
SEED = 20260928
QUALIFIED_MINUTES = 500     # the API's default floor; stored rows carry minutes, the floor is applied live
STAR_MINUTES = 1000         # each team's top-usage player is chosen among players with this many minutes
FT_POSS = 0.44

# Same event rules as build_player_game_lines.py (order matters there:
# substitution, then free throw, then field goal, then rebound, then turnover).
FG_SQL = ("action_type NOT LIKE 'Free Throw%%' AND action_type <> 'Substitution' "
          "AND (description LIKE '%% blocks %%' OR description ~ ' (makes|misses) ')")
FT_SQL = "action_type LIKE 'Free Throw%%'"
TOV_SQL = (f"NOT ({FG_SQL}) AND NOT ({FT_SQL}) AND action_type NOT LIKE '%%Rebound%%' "
           "AND action_type <> 'No Turnover' AND (action_type LIKE '%%Turnover%%' OR action_type = 'Traveling')")

TEAM_TOTALS_SQL = f"""
WITH ev AS (
    SELECT e.game_id, g.season, g.game_date, g.home_team, g.away_team, e.team_tricode AS team,
           e.action_type, COALESCE(e.description, '') AS description, e.period, e.score_home, e.score_away
    FROM pbp_events e JOIN pbp_games g USING (game_id)
    WHERE g.source = 'espn'
),
per_team AS (
    SELECT game_id, season, game_date, home_team, away_team, team,
           COUNT(*) FILTER (WHERE {FG_SQL}) AS fga,
           COUNT(*) FILTER (WHERE {FT_SQL}) AS fta,
           COUNT(*) FILTER (WHERE {TOV_SQL}) AS tov
    FROM ev WHERE team IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6
),
per_game AS (
    SELECT game_id, MAX(score_home) AS score_home, MAX(score_away) AS score_away,
           2880 + 300 * GREATEST(MAX(period) - 4, 0) AS game_seconds
    FROM ev GROUP BY 1
)
SELECT t.game_id, t.season, t.game_date, t.team,
       CASE WHEN t.team = t.home_team THEN t.away_team ELSE t.home_team END AS opponent,
       t.team = t.home_team AS is_home,
       CASE WHEN t.team = t.home_team THEN g.score_home ELSE g.score_away END AS pts_for,
       CASE WHEN t.team = t.home_team THEN g.score_away ELSE g.score_home END AS pts_against,
       t.fga, t.fta, t.tov, g.game_seconds
FROM per_team t JOIN per_game g USING (game_id)
WHERE t.team IN (t.home_team, t.away_team)
"""

LINE_COLS = ["player_id", "game_id", "season", "game_date", "team_abbreviation", "seconds",
             "fga", "fta", "oreb", "tov",
             "tm_fga", "tm_fta", "tm_oreb", "tm_tov", "tm_pts", "op_fga", "op_fta", "op_oreb", "op_tov", "op_pts"]


def poss(fga, fta, oreb, tov):
    return fga + FT_POSS * fta - oreb + tov


def rating(points, possessions):
    return 100.0 * points / possessions if possessions > 0 else None


def team_totals(conn):
    """Team-game totals from the play-by-play, joined with the lines' own
    offensive-rebound and tracked-seconds sums."""
    t = pd.read_sql_query(TEAM_TOTALS_SQL, conn)
    lines_by_team = pd.read_sql_query(
        """SELECT game_id, team_abbreviation AS team, SUM(oreb) AS oreb, SUM(seconds) AS tracked_seconds
           FROM player_game_lines GROUP BY 1, 2""", conn)
    t = t.merge(lines_by_team, on=["game_id", "team"], how="left")
    t["oreb"] = t["oreb"].fillna(0).astype(int)
    t["tracked_seconds"] = t["tracked_seconds"].fillna(0.0)
    # Opponent side of the same game.
    opp = t[["game_id", "team", "fga", "fta", "oreb", "tov"]].rename(
        columns={"team": "opponent", "fga": "opp_fga", "fta": "opp_fta", "oreb": "opp_oreb", "tov": "opp_tov"})
    t = t.merge(opp, on=["game_id", "opponent"], how="inner")
    t["poss"] = (poss(t.fga, t.fta, t.oreb, t.tov) + poss(t.opp_fga, t.opp_fta, t.opp_oreb, t.opp_tov)) / 2
    t["tracked_share"] = t["tracked_seconds"] / (5 * t["game_seconds"])
    t["win"] = t["pts_for"] > t["pts_against"]
    return t


def bootstrap(rng, per_game):
    """per_game: G x 6 array (pf_on, pa_on, poss_on, pf_off, pa_off, poss_off).
    Resamples games with replacement; returns (se, lo, hi) of on - off net,
    and the SE of on-court net."""
    g = per_game.shape[0]
    idx = rng.integers(0, g, size=(BOOTSTRAPS, g))
    sums = per_game[idx].sum(axis=1)  # B x 6
    with np.errstate(divide="ignore", invalid="ignore"):
        net_on = 100 * (sums[:, 0] - sums[:, 1]) / sums[:, 2]
        net_off = 100 * (sums[:, 3] - sums[:, 4]) / sums[:, 5]
    diff = net_on - net_off
    ok = np.isfinite(diff)
    if ok.sum() < BOOTSTRAPS * 0.9:
        return None, None, None, None
    diff, net_on = diff[ok], net_on[ok]
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return float(diff.std(ddof=1)), float(lo), float(hi), float(net_on.std(ddof=1))


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    teams = team_totals(conn)
    print(f"{len(teams)} team-games, {teams.game_id.nunique()} games ({time.time() - t0:.0f}s)")

    lines = pd.read_sql_query(f"SELECT {', '.join(LINE_COLS)} FROM player_game_lines WHERE seconds > 0", conn)
    lines = lines.merge(
        teams[["game_id", "team", "pts_for", "pts_against", "fga", "fta", "oreb", "tov",
               "opp_fga", "opp_fta", "opp_oreb", "opp_tov", "poss", "game_seconds"]]
        .rename(columns={"team": "team_abbreviation", "fga": "t_fga", "fta": "t_fta", "oreb": "t_oreb", "tov": "t_tov",
                         "poss": "t_poss"}),
        on=["game_id", "team_abbreviation"], how="inner")
    print(f"{len(lines)} player-games with minutes")

    # On-court possessions: average of the two sides of his stints.
    lines["poss_on"] = (poss(lines.tm_fga, lines.tm_fta, lines.tm_oreb, lines.tm_tov)
                        + poss(lines.op_fga, lines.op_fta, lines.op_oreb, lines.op_tov)) / 2
    lines["poss_off"] = lines["t_poss"] - lines["poss_on"]
    lines["pf_off"] = lines["pts_for"] - lines["tm_pts"]
    lines["pa_off"] = lines["pts_against"] - lines["op_pts"]
    lines["seconds_off"] = lines["game_seconds"] - lines["seconds"]
    negative = int(((lines.poss_off < 0) | (lines.pf_off < 0) | (lines.pa_off < 0) | (lines.seconds_off < 0)).sum())
    for c in ("poss_off", "pf_off", "pa_off", "seconds_off"):
        lines[c] = lines[c].clip(lower=0)
    lines["shooting_poss"] = lines.fga + FT_POSS * lines.fta + lines.tov
    lines["tm_shooting_poss"] = lines.tm_fga + FT_POSS * lines.tm_fta + lines.tm_tov

    rng = np.random.default_rng(SEED)
    rows = []
    groups = lines.groupby(["player_id", "season", "team_abbreviation"], sort=True)
    team_games = teams.groupby(["season", "team"]).size().to_dict()
    for i, ((pid, season, team), g) in enumerate(groups):
        s = g[["tm_pts", "op_pts", "poss_on", "pf_off", "pa_off", "poss_off", "seconds", "seconds_off",
               "shooting_poss", "tm_shooting_poss"]].sum()
        net_on = rating(s.tm_pts - s.op_pts, s.poss_on)
        net_off = rating(s.pf_off - s.pa_off, s.poss_off)
        ortg_on, drtg_on = rating(s.tm_pts, s.poss_on), rating(s.op_pts, s.poss_on)
        ortg_off, drtg_off = rating(s.pf_off, s.poss_off), rating(s.pa_off, s.poss_off)
        per_game = g[["tm_pts", "op_pts", "poss_on", "pf_off", "pa_off", "poss_off"]].to_numpy(float)
        se = lo = hi = se_on = None
        if len(g) >= 2 and net_on is not None and net_off is not None:
            se, lo, hi, se_on = bootstrap(rng, per_game)
        rows.append({
            "player_id": int(pid), "season": int(season), "team_abbreviation": team,
            "games": int(len(g)), "team_games": int(team_games.get((season, team), 0)),
            "minutes_on": round(s.seconds / 60, 1), "minutes_off": round(s.seconds_off / 60, 1),
            "poss_on": round(float(s.poss_on), 1), "poss_off": round(float(s.poss_off), 1),
            "pts_for_on": int(s.tm_pts), "pts_against_on": int(s.op_pts),
            "pts_for_off": int(s.pf_off), "pts_against_off": int(s.pa_off),
            "ortg_on": ortg_on, "drtg_on": drtg_on, "net_on": net_on,
            "ortg_off": ortg_off, "drtg_off": drtg_off, "net_off": net_off,
            "on_off_ortg": None if ortg_on is None or ortg_off is None else ortg_on - ortg_off,
            "on_off_drtg": None if drtg_on is None or drtg_off is None else drtg_on - drtg_off,
            "on_off_net": None if net_on is None or net_off is None else net_on - net_off,
            "on_off_se": se, "on_off_ci_low": lo, "on_off_ci_high": hi, "net_on_se": se_on,
            "usg_pct": float(s.shooting_poss / s.tm_shooting_poss) if s.tm_shooting_poss > 0 else None,
        })
        if i % 500 == 0:
            print(f"  {i} of {groups.ngroups} player-season-teams ({time.time() - t0:.0f}s)")
    out = pd.DataFrame(rows)
    for c in ("ortg_on", "drtg_on", "net_on", "ortg_off", "drtg_off", "net_off", "on_off_ortg", "on_off_drtg",
              "on_off_net", "on_off_se", "on_off_ci_low", "on_off_ci_high", "net_on_se"):
        out[c] = out[c].astype(float).round(2)
    out["usg_pct"] = out["usg_pct"].astype(float).round(4)

    # ── Season checks ──
    season_rows = []
    for season, tg in teams.groupby("season"):
        o = out[out.season == season]
        q = o[o.minutes_on >= QUALIFIED_MINUTES].dropna(subset=["on_off_ci_low"])
        excl = int(((q.on_off_ci_low > 0) | (q.on_off_ci_high < 0)).sum())
        rated = o.dropna(subset=["net_on"])  # a row with no on-court possession has no rating
        season_rows.append({
            "season": int(season), "games": int(tg.game_id.nunique()), "player_rows": int(len(o)),
            "players": int(o.player_id.nunique()),
            "tracked_share": round(float(tg.tracked_seconds.sum() / (5 * tg.game_seconds.sum())), 4),
            "league_net_on_weighted": round(float(np.average(rated.net_on.to_numpy(), weights=rated.poss_on.to_numpy())), 3),
            "league_ortg": round(float(100 * tg.pts_for.sum() / tg.poss.sum()), 2),
            "qualified": int(len(q)), "qualified_ci_excludes_zero": excl,
            "qualified_minutes": QUALIFIED_MINUTES, "bootstraps": BOOTSTRAPS,
        })
    seasons = pd.DataFrame(season_rows)

    # ── Write ──
    cur.execute("DROP TABLE IF EXISTS team_game_totals;")
    cur.execute("""CREATE TABLE team_game_totals (
        game_id TEXT NOT NULL, season INTEGER NOT NULL, game_date DATE, team_abbreviation TEXT NOT NULL,
        opponent TEXT, is_home BOOLEAN, win BOOLEAN, pts_for INTEGER, pts_against INTEGER,
        fga INTEGER, fta INTEGER, oreb INTEGER, tov INTEGER,
        opp_fga INTEGER, opp_fta INTEGER, opp_oreb INTEGER, opp_tov INTEGER,
        poss DOUBLE PRECISION, game_seconds INTEGER, tracked_seconds DOUBLE PRECISION, tracked_share DOUBLE PRECISION,
        PRIMARY KEY (game_id, team_abbreviation));""")
    tcols = ["game_id", "season", "game_date", "team", "opponent", "is_home", "win", "pts_for", "pts_against",
             "fga", "fta", "oreb", "tov", "opp_fga", "opp_fta", "opp_oreb", "opp_tov", "poss", "game_seconds",
             "tracked_seconds", "tracked_share"]
    trecs = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)
                   for v in r) for r in teams[tcols].itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO team_game_totals ({', '.join(c if c != 'team' else 'team_abbreviation' for c in tcols)}) VALUES %s",
        trecs, page_size=2000)
    cur.execute("CREATE INDEX ON team_game_totals (season, team_abbreviation);")

    cur.execute("DROP TABLE IF EXISTS player_on_off;")
    cur.execute("""CREATE TABLE player_on_off (
        player_id BIGINT NOT NULL, season INTEGER NOT NULL, team_abbreviation TEXT NOT NULL,
        games INTEGER, team_games INTEGER, minutes_on DOUBLE PRECISION, minutes_off DOUBLE PRECISION,
        poss_on DOUBLE PRECISION, poss_off DOUBLE PRECISION,
        pts_for_on INTEGER, pts_against_on INTEGER, pts_for_off INTEGER, pts_against_off INTEGER,
        ortg_on DOUBLE PRECISION, drtg_on DOUBLE PRECISION, net_on DOUBLE PRECISION,
        ortg_off DOUBLE PRECISION, drtg_off DOUBLE PRECISION, net_off DOUBLE PRECISION,
        on_off_ortg DOUBLE PRECISION, on_off_drtg DOUBLE PRECISION, on_off_net DOUBLE PRECISION,
        on_off_se DOUBLE PRECISION, on_off_ci_low DOUBLE PRECISION, on_off_ci_high DOUBLE PRECISION,
        net_on_se DOUBLE PRECISION, usg_pct DOUBLE PRECISION,
        PRIMARY KEY (player_id, season, team_abbreviation));""")
    ocols = list(out.columns)
    orecs = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)
                   for v in r) for r in out[ocols].itertuples(index=False)]
    psycopg2.extras.execute_values(cur, f"INSERT INTO player_on_off ({', '.join(ocols)}) VALUES %s", orecs, page_size=2000)
    cur.execute("CREATE INDEX ON player_on_off (season, team_abbreviation);")
    cur.execute("CREATE INDEX ON player_on_off (player_id);")

    cur.execute("DROP TABLE IF EXISTS player_on_off_seasons;")
    cur.execute("""CREATE TABLE player_on_off_seasons (
        season INTEGER PRIMARY KEY, games INTEGER, player_rows INTEGER, players INTEGER,
        tracked_share DOUBLE PRECISION, league_net_on_weighted DOUBLE PRECISION, league_ortg DOUBLE PRECISION,
        qualified INTEGER, qualified_ci_excludes_zero INTEGER, qualified_minutes INTEGER, bootstraps INTEGER);""")
    scols = list(seasons.columns)
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO player_on_off_seasons ({', '.join(scols)}) VALUES %s",
        [tuple(v.item() if hasattr(v, "item") else v for v in r) for r in seasons[scols].itertuples(index=False)])
    conn.commit()
    print(f"wrote {len(teams)} team-games, {len(out)} player-season-team rows ({time.time() - t0:.0f}s)")
    print(f"player-games where on-court exceeded the team total (clipped to 0 off-court): {negative}")

    # ── Checks ──
    print("\nPer season:")
    print(seasons.to_string(index=False))
    names = pd.read_sql_query(
        "SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats ORDER BY player_id, season DESC", conn)
    out = out.merge(names, on="player_id", how="left")
    jokic = out[out.player_id == 203999][["season", "team_abbreviation", "games", "minutes_on", "net_on", "net_off",
                                           "on_off_net", "on_off_ci_low", "on_off_ci_high", "usg_pct"]]
    print("\nNikola Jokić:")
    print(jokic.to_string(index=False))
    print(f"\nTop on-minus-off net, {STAR_MINUTES}+ minutes:")
    for season, o in out[out.minutes_on >= STAR_MINUTES].groupby("season"):
        top = o.nlargest(5, "on_off_net")
        print(f"  {season - 1}-{str(season)[-2:]}: " + "; ".join(
            f"{r.player_name} {r.team_abbreviation} {r.on_off_net:+.1f} ({r.on_off_ci_low:+.1f} to {r.on_off_ci_high:+.1f})"
            for r in top.itertuples()))
    print("\nEach team's top-usage player, latest season:")
    last = out[(out.season == out.season.max()) & (out.minutes_on >= STAR_MINUTES)]
    stars = last.sort_values("usg_pct", ascending=False).drop_duplicates("team_abbreviation")
    print(stars[["team_abbreviation", "player_name", "usg_pct", "net_on", "net_off", "on_off_net"]]
          .sort_values("on_off_net", ascending=False).to_string(index=False))
    conn.close()


if __name__ == "__main__":
    main()
