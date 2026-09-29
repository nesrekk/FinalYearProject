"""
build_season_sim.py
====================
Pre-game win probabilities for every regular-season game 2010-11 to
2025-26, and a backtest of the season simulator (Teams > Season Simulator)
that runs on them. The math lives in api/season_sim_lib.py, shared with the
endpoint that simulates any date live, and builds on api/luck_lib.py.

What it does, in order:

1. Loads every game (`game_scores`, real final scores) with each side's rest
   (`team_game_fatigue.rest_days`; 0 = back-to-back). Fits the prior's three
   constants on all 480 franchise pairs of consecutive seasons: carry (a
   season's SRS regressed on the previous season's, about 0.6), tau2 (the
   spread left around that) and hca_n0 (how many games' worth of prior last
   season's home court is worth).

2. For every game, the features as of its morning, using only games before
   it: this season's SRS (shrunk toward zero, as on the Luck & Schedule
   page) and the same SRS blended with last season's as a prior, the blended
   home court, both sides' back-to-back flags, and luck_lib's own projection
   as a baseline. Four forms are fitted leave-one-season-out (each season
   predicted by coefficients fitted on the other 15) and scored by log loss,
   Brier and calibration; the lowest log loss is the form the simulator
   uses. Every game's held-out probability under every form goes to
   `game_pregame_odds`.

3. Who actually made the playoffs, from `postseason_games` (every play-in
   and playoff game from ESPN's scoreboard, scripts/fetch_postseason_games.py):
   the teams in playoff games, and the teams in play-in games. Cross-checked
   against the playoff (`004...`) and play-in (`005...`) games in
   `player_shots` (the two teams with the most shooters in each game) for
   every season that has them, and against Basketball-Reference's playoff
   flag in `team_seasons` where that flag lists 16 teams; the script stops
   on any disagreement.

4. Backtest: at opening day (nothing played, prior only), the halfway date
   and about 60 games in, every season is simulated 10,000 times with
   coefficients fitted without that season, and compared with what
   happened: Brier score, log loss and calibration of the playoff
   probability (and of a top-6 finish from 2020-21), the mean win total's
   error and how often the final win total fell inside the 80% range.
   Two baselines at the same dates: the standings as they stood (a team in a
   playoff spot gets 100%) and the record carried forward (each remaining
   game by log5 of the two teams' win%, no home court, same tiebreaks).

Tables written (dropped and rebuilt):
  game_pregame_odds            one row per game (home side): held-out P(home wins)
                               under every form, the features, the result
  pregame_model_fit            the four forms: all-season coefficients, held-out
                               log loss / Brier / favourite win rate, chosen flag
  pregame_calibration          predicted vs actual home win rate by decile, per form
  pregame_model_seasons        held-out log loss per form per season
  season_postseason            per team-season: conference, play-in, playoffs, final
                               record and finish by this simulator's ranking rule
  season_sim_seasons           per season: dates, checkpoints, prior home court / SD
  season_sim_params            the prior constants, the chosen form, run count
  season_sim_backtest          per season x checkpoint x method x team
  season_sim_backtest_summary  Brier / log loss / win error / coverage per checkpoint x method
  season_sim_calibration       playoff (and top-6) calibration bins per checkpoint x method

Usage:
    cd scripts && python3 build_season_sim.py            (~2 min)
Rerun after game_scores or team_game_fatigue change (a new season loaded) and
after fetch_postseason_games.py (it needs that season's postseason on file).
"""

import sys
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import season_sim_lib as L  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

RUNS = L.DEFAULT_RUNS
DECILES = np.linspace(0, 1, 11)
BINS = np.array([0, 0.05, 0.15, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0])
CHECKPOINTS = ("opening", "halfway", "sixty")

POSTSEASON_SQL = """SELECT s.season, s.game_id, p.team_abbreviation, COUNT(*) AS n
                    FROM player_shots s
                    JOIN player_season_stats p ON p.player_id = s.player_id
                         AND p.season = CAST(LEFT(s.season, 4) AS INT) + 1
                    WHERE s.game_id LIKE '004%%' OR s.game_id LIKE '005%%'
                    GROUP BY 1, 2, 3"""


def py(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, (np.datetime64, pd.Timestamp)):
        return pd.Timestamp(v).date()
    return v


def write(cur, table, ddl, cols, rows):
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TABLE {table} ({ddl})")
    execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s",
                   [tuple(py(v) for v in row) for row in rows], page_size=2000)


def build_features(season_games, params):
    """Home rows of every season 2010-11 on with their as-of features."""
    parts = []
    for season in sorted(season_games):
        if season - 1 not in season_games:
            continue
        sg = season_games[season]
        teams = sorted(sg.team_abbreviation.unique())
        prior = L.season_prior(season_games[season - 1])
        hr = L.home_rows(sg).sort_values(["game_date", "game_id"])
        for d, day in hr.groupby("game_date", sort=True):
            rat = L.ratings_as_of(sg[sg.game_date < d], teams, prior, params)
            f = L.game_features(day, rat)
            f["hca"] = rat["hca"]
            parts.append(f)
    return pd.concat(parts, ignore_index=True)


def fit_forms(R):
    """Held-out predictions per form (column p_<form>), all-season coefficients, per-season and
    pooled scores, calibration deciles."""
    y = R.home_won.to_numpy(float)
    seasons = sorted(R.season.unique())
    fits, per_season, cal, loso_betas = [], [], [], {}
    for form in L.FORMS:
        if form == "baseline":
            preds = R.p_baseline.to_numpy(float)
            beta = {}
        else:
            cols = L.FEATURES[form]
            X = R[cols].to_numpy(float)
            preds = np.zeros(len(R))
            loso_betas[form] = {}
            for s in seasons:
                tr = (R.season != s).to_numpy()
                b = L.logit_fit(X[tr], y[tr])
                loso_betas[form][s] = dict(zip(cols, b.tolist()))
                preds[~tr] = L.sigmoid(X[~tr] @ b)
            beta = dict(zip(cols, L.logit_fit(X, y).tolist()))
        R[f"p_{form}"] = preds
        fav = float(np.where(preds >= 0.5, y, 1 - y).mean())
        fits.append({"form": form, "label": L.FORM_LABELS[form], "features": ",".join(L.FEATURES.get(form, [])),
                     "beta": beta, "loso_log_loss": L.log_loss(preds, y), "loso_brier": L.brier(preds, y),
                     "favourite_win_rate": fav, "n": len(R)})
        for s in seasons:
            m = (R.season == s).to_numpy()
            per_season.append({"form": form, "season": s, "n": int(m.sum()), "log_loss": L.log_loss(preds[m], y[m]),
                               "brier": L.brier(preds[m], y[m]),
                               "favourite_win_rate": float(np.where(preds[m] >= 0.5, y[m], 1 - y[m]).mean())})
        for i, b in enumerate(L.calibration_bins(preds, y, DECILES)):
            cal.append({"form": form, "decile": i + 1, **b})
    fits = pd.DataFrame(fits)
    fits["chosen"] = fits.loso_log_loss == fits.loso_log_loss.min()
    return fits, pd.DataFrame(per_season), pd.DataFrame(cal), loso_betas


def postseason_facts(conn, season_games):
    """Playoff and play-in teams per season from postseason_games, cross-checked against
    player_shots' postseason games and Basketball-Reference's playoff flag."""
    cur = conn.cursor()
    cur.execute("SELECT season, stage, home, away FROM postseason_games")
    playoffs, playin = defaultdict(set), defaultdict(set)
    for season, stage, home, away in cur.fetchall():
        (playoffs if stage == "playoffs" else playin)[season].update((home, away))
    cur.execute(POSTSEASON_SQL)
    votes = defaultdict(Counter)
    for season, gid, team, n in cur.fetchall():
        votes[(int(season[:4]) + 1, gid)][team] += n
    shots_po, shots_pi = defaultdict(set), defaultdict(set)
    for (season, gid), cnt in votes.items():
        top = [t for t, _ in cnt.most_common(2)]
        (shots_po if gid.startswith("004") else shots_pi)[season].update(top)
    cur.execute("""SELECT season, abbreviation FROM team_seasons
                   WHERE playoffs AND lg = 'NBA' AND NOT is_league_avg""")
    bref = defaultdict(set)
    for s, t in cur.fetchall():
        bref[s].add(t)
    rows = []
    for season in sorted(season_games):
        sg = season_games[season]
        teams = sorted(sg.team_abbreviation.unique())
        po, pi = playoffs.get(season, set()), playin.get(season, set())
        if len(po) != 16:
            raise SystemExit(f"{season}: {len(po)} playoff teams in postseason_games, expected 16 (rerun fetch_postseason_games.py)")
        if season in shots_po and shots_po[season] != po:
            raise SystemExit(f"{season}: playoff teams differ from player_shots' playoff games: {po ^ shots_po[season]}")
        if season in shots_pi and shots_pi[season] != pi:
            raise SystemExit(f"{season}: play-in teams differ from player_shots' play-in games: {pi ^ shots_pi[season]}")
        if len(bref.get(season, ())) == 16 and bref[season] != po:
            raise SystemExit(f"{season}: playoff teams differ from Basketball-Reference: {po ^ bref[season]}")
        if season >= L.PLAY_IN_FROM and len(pi) != 8:
            raise SystemExit(f"{season}: {len(pi)} play-in teams, expected 8")
        st = L.Standings(teams, sg)
        pos = L.current_positions(st, season, np.random.default_rng(L.sim_seed(season, "final")))
        for t in teams:
            i = st.idx[t]
            rows.append({"season": season, "team_abbreviation": t, "conference": L.CONFERENCE[t],
                         "play_in": t in pi, "playoffs": t in po,
                         "top6": (t in po and t not in pi) if season >= L.PLAY_IN_FROM else None,
                         "wins": int(st.wins[i]), "games": int(st.games[i]), "position": pos[t]})
    df = pd.DataFrame(rows)
    checked = sorted(s for s in season_games if s in shots_po)
    print(f"postseason facts: 16 playoff teams every season; cross-checked against player_shots for "
          f"{checked[0]}-{checked[-1]} and Basketball-Reference for {min(s for s in bref if len(bref[s]) == 16)}-"
          f"{max(s for s in bref if len(bref[s]) == 16 and s in season_games)}; play-in teams: "
          f"{ {s: len(playin[s]) for s in sorted(playin)} }")
    return df


def backtest(season_games, params, loso_betas, all_beta, chosen, facts):
    rows, summary, cal = [], [], []
    fact = facts.set_index(["season", "team_abbreviation"])
    seasons = [s for s in sorted(season_games) if s - 1 in season_games]
    max_beta_gap = max(abs(loso_betas[chosen][s][k] - all_beta[k]) for s in seasons for k in all_beta)
    print(f"largest gap between a held-out coefficient and the all-season one: {max_beta_gap:.4f}")
    for season in seasons:
        sg = season_games[season]
        teams = sorted(sg.team_abbreviation.unique())
        prior = L.season_prior(season_games[season - 1])
        cps = L.checkpoint_dates(sg)
        beta = loso_betas[chosen][season]
        for cp in CHECKPOINTS:
            cutoff = cps[cp]
            played = sg[sg.game_date < cutoff]
            left = L.home_rows(sg[sg.game_date >= cutoff])
            rat = L.ratings_as_of(played, teams, prior, params)
            st = L.Standings(teams, played)
            pos_now = L.current_positions(st, season, np.random.default_rng(L.sim_seed(season, cutoff, "now")))
            methods = ["model"] if cp == "opening" else ["model", "record", "standings"]
            for method in methods:
                rng = np.random.default_rng(L.sim_seed(season, cutoff, method))
                if method == "standings":
                    summ = None
                else:
                    if method == "model":
                        r = L.draw_ratings(rat["r_post"], rat["var_post"], teams, RUNS, rng)
                        pm = L.model_p_matrix(left, st, beta, rat["hca"])
                    else:
                        r = None
                        pm = L.record_p_matrix(left, st)
                    sim = L.simulate(st, left, season, pm, r, beta, rat["hca"], RUNS, rng)
                    summ = L.summarize(sim, st, season)
                for t in teams:
                    f = fact.loc[(season, t)]
                    i = st.idx[t]
                    row = {"season": season, "checkpoint": cp, "checkpoint_date": cutoff, "method": method,
                           "team_abbreviation": t, "conference": L.CONFERENCE[t],
                           "wins_now": int(st.wins[i]), "games_now": int(st.games[i]), "position_now": pos_now[t],
                           "final_wins": int(f.wins), "final_games": int(f.games), "final_position": int(f.position),
                           "made_playoffs": bool(f.playoffs), "made_top6": None if pd.isna(f.top6) else bool(f.top6),
                           "played_playin": bool(f.play_in)}
                    if summ is None:
                        row.update({"mean_wins": None, "wins_p10": None, "wins_p90": None,
                                    "p_playoffs": float(pos_now[t] <= L.PLAYOFF_SPOTS),
                                    "p_top6": float(pos_now[t] <= L.DIRECT_SPOTS) if season >= L.PLAY_IN_FROM else None,
                                    "p_playin": None, "p_first": float(pos_now[t] == 1)})
                    else:
                        s = summ[t]
                        row.update({"mean_wins": s["mean_wins"], "wins_p10": s["wins_p10"], "wins_p90": s["wins_p90"],
                                    "p_playoffs": s["p_playoffs"], "p_top6": s["p_top6"], "p_playin": s["p_playin"],
                                    "p_first": s["p_first"]})
                    rows.append(row)
        print(f"  backtest {season} done")
    bt = pd.DataFrame(rows)
    for (cp, method), g in bt.groupby(["checkpoint", "method"]):
        y = g.made_playoffs.to_numpy(float)
        p = g.p_playoffs.to_numpy(float)
        add = lambda metric, value, n, note: summary.append(  # noqa: E731
            {"checkpoint": cp, "method": method, "metric": metric, "value": value, "n": n, "note": note})
        add("playoffs_brier", L.brier(p, y), len(g), "Brier score of the playoff probability (0 = perfect, 0.25 = coin flips)")
        add("playoffs_log_loss", L.log_loss(p, y), len(g), "Log loss of the playoff probability")
        add("playoffs_base_rate", float(y.mean()), len(g), "Share of teams that made the playoffs")
        pi = g[g.season >= L.PLAY_IN_FROM]
        if len(pi) and pi.p_top6.notna().all():
            add("top6_brier", L.brier(pi.p_top6.to_numpy(float), pi.made_top6.to_numpy(float)), len(pi),
                "Brier score of a top-6 finish (straight into the playoffs), 2020-21 on")
            for i, b in enumerate(L.calibration_bins(pi.p_top6.to_numpy(float), pi.made_top6.to_numpy(float), BINS)):
                cal.append({"checkpoint": cp, "method": method, "target": "top6", "bin": i + 1, **b})
        for i, b in enumerate(L.calibration_bins(p, y, BINS)):
            cal.append({"checkpoint": cp, "method": method, "target": "playoffs", "bin": i + 1, **b})
        if method != "standings":
            err = (g.mean_wins - g.final_wins).to_numpy(float)
            add("wins_mae", float(np.abs(err).mean()), len(g), "Mean absolute error of the mean projected win total")
            add("wins_rmse", float(np.sqrt((err ** 2).mean())), len(g), "Root-mean-square error of the mean projected win total")
            inside = ((g.final_wins >= g.wins_p10) & (g.final_wins <= g.wins_p90)).mean()
            add("wins_cover80", float(inside), len(g), "Share of final win totals inside the 80% range (10th-90th percentile)")
            add("first_brier", L.brier(g.p_first.to_numpy(float), (g.final_position == 1).to_numpy(float)), len(g),
                "Brier score of finishing first in the conference")
    return bt, pd.DataFrame(summary), pd.DataFrame(cal)


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    df = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where=""), conn))
    season_games = dict(tuple(df.groupby("season")))
    print(f"{len(df):,} team-games, seasons {min(season_games)}-{max(season_games)} ({time.time() - t0:.0f}s)")
    params = L.fit_params(season_games)
    print("prior constants:", {k: round(v, 3) if isinstance(v, float) else v for k, v in params.items()})

    # --- pre-game odds ------------------------------------------------------
    R = build_features(season_games, params)
    print(f"{len(R):,} games with features ({time.time() - t0:.0f}s); home win rate {R.home_won.mean():.3f}")
    fits, per_season, cal, loso_betas = fit_forms(R)
    print(fits[["form", "loso_log_loss", "loso_brier", "favourite_win_rate", "chosen"]].round(4).to_string(index=False))
    chosen = fits[fits.chosen].iloc[0].form
    all_beta = fits[fits.chosen].iloc[0].beta
    print("chosen:", chosen, {k: round(v, 4) for k, v in all_beta.items()})
    R["p_home"] = R[f"p_{chosen}"]
    gp = np.minimum(R.home_games, R.away_games)
    for lo, hi in ((0, 5), (6, 15), (16, 30), (31, 50), (51, 100)):
        m = (gp >= lo) & (gp <= hi)
        print(f"  games played {lo:>2}-{hi:<3} n={int(m.sum()):5d} " + " ".join(
            f"{f} {L.log_loss(R.loc[m, f'p_{f}'], R.loc[m, 'home_won']):.4f}" for f in L.FORMS))
    b2b = {}
    for name, m in (("home_b2b_vs_rested", (R.home_b2b == 1) & (R.away_b2b == 0)),
                    ("rested_vs_away_b2b", (R.home_b2b == 0) & (R.away_b2b == 1)),
                    ("neither", (R.home_b2b == 0) & (R.away_b2b == 0)), ("both", (R.home_b2b == 1) & (R.away_b2b == 1))):
        b2b[name] = (int(m.sum()), float(R.loc[m, "home_won"].mean()))
    print("home win rate by rest:", {k: (n, round(r, 3)) for k, (n, r) in b2b.items()})
    era = {"2010-11 to 2019-20": R.season <= 2020, "2020-21 on": R.season >= 2021}
    for k, m in era.items():
        print(f"  {k}: home win rate {R.loc[m, 'home_won'].mean():.3f}, favourite wins "
              f"{np.where(R.loc[m, 'p_home'] >= 0.5, R.loc[m, 'home_won'], ~R.loc[m, 'home_won']).mean():.3f}")

    # --- who made it --------------------------------------------------------
    facts = postseason_facts(conn, season_games)

    # --- simulator backtest -------------------------------------------------
    bt, bt_summary, bt_cal = backtest(season_games, params, loso_betas, all_beta, chosen, facts)
    print(bt_summary.pivot(index=["checkpoint", "metric"], columns="method", values="value").round(4).to_string())
    for season, cp, team in ((2016, "halfway", "GSW"), (2024, "halfway", "BOS"), (2021, "halfway", "MIL"),
                             (2026, "sixty", "DET")):
        r = bt[(bt.season == season) & (bt.checkpoint == cp) & (bt.method == "model") & (bt.team_abbreviation == team)].iloc[0]
        print(f"  {season} {cp} {team}: {r.wins_now}-{r.games_now - r.wins_now} then, playoffs {r.p_playoffs:.3f}, "
              f"mean {r.mean_wins:.1f} [{r.wins_p10:.0f}-{r.wins_p90:.0f}], final {r.final_wins}-{r.final_games - r.final_wins}")

    # --- seasons ------------------------------------------------------------
    srows = []
    for season in sorted(season_games):
        if season - 1 not in season_games:
            continue
        sg = season_games[season]
        prior = L.season_prior(season_games[season - 1])
        cps = L.checkpoint_dates(sg)
        srows.append({"season": season, "games": int(sg.game_id.nunique()), "teams": int(sg.team_abbreviation.nunique()),
                      "first_date": sg.game_date.min(), "last_date": sg.game_date.max(),
                      "halfway_date": cps["halfway"], "sixty_date": cps["sixty"], "play_in": season >= L.PLAY_IN_FROM,
                      "hca_prev": prior["hca"], "sigma_prev": prior["sigma"],
                      "note": {2013: "1,229 games: one Boston-Indiana game was cancelled.",
                               2020: "Shortened season: 64-75 games a team; the schedule after a date is the one actually played, "
                                     "including the Orlando restart (no home court), and the 22-team format and the 8th-seed play-in "
                                     "are not modelled (top 8 by win%).",
                               2021: "72-game season; first season with the play-in."}.get(season, "")})

    # --- write --------------------------------------------------------------
    cur = conn.cursor()
    odds_cols = ["game_id", "season", "game_date", "home", "away", "venue", "home_b2b", "away_b2b", "home_games",
                 "away_games", "r_home", "r_away", "hca", "exp_margin", "p_home", "p_baseline", "p_current", "p_prior",
                 "p_prior_rest", "home_won", "margin", "pts_home", "pts_away"]
    R["pts_home"], R["pts_away"] = R.pts_for, R.pts_against
    R["home_b2b"], R["away_b2b"] = R.home_b2b.astype(bool), R.away_b2b.astype(bool)
    write(cur, "game_pregame_odds", """game_id TEXT PRIMARY KEY, season INTEGER, game_date DATE, home TEXT, away TEXT,
        venue SMALLINT, home_b2b BOOLEAN, away_b2b BOOLEAN, home_games SMALLINT, away_games SMALLINT,
        r_home DOUBLE PRECISION, r_away DOUBLE PRECISION, hca DOUBLE PRECISION, exp_margin DOUBLE PRECISION,
        p_home DOUBLE PRECISION, p_baseline DOUBLE PRECISION, p_current DOUBLE PRECISION, p_prior DOUBLE PRECISION,
        p_prior_rest DOUBLE PRECISION, home_won BOOLEAN, margin SMALLINT, pts_home SMALLINT, pts_away SMALLINT""",
          odds_cols, R[odds_cols].itertuples(index=False, name=None))
    cur.execute("CREATE INDEX ON game_pregame_odds (season, game_date)")

    write(cur, "pregame_model_fit", """form TEXT PRIMARY KEY, label TEXT, features TEXT, beta JSONB,
        loso_log_loss DOUBLE PRECISION, loso_brier DOUBLE PRECISION, favourite_win_rate DOUBLE PRECISION, n INTEGER,
        chosen BOOLEAN""", ["form", "label", "features", "beta", "loso_log_loss", "loso_brier", "favourite_win_rate", "n", "chosen"],
          [(r.form, r.label, r.features, psycopg2.extras.Json(r.beta), r.loso_log_loss, r.loso_brier, r.favourite_win_rate,
            int(r.n), bool(r.chosen)) for r in fits.itertuples()])
    write(cur, "pregame_calibration", """form TEXT, decile SMALLINT, lo DOUBLE PRECISION, hi DOUBLE PRECISION, n INTEGER,
        predicted DOUBLE PRECISION, actual DOUBLE PRECISION, PRIMARY KEY (form, decile)""",
          ["form", "decile", "lo", "hi", "n", "predicted", "actual"], cal[["form", "decile", "lo", "hi", "n", "predicted", "actual"]].itertuples(index=False, name=None))
    write(cur, "pregame_model_seasons", """form TEXT, season INTEGER, n INTEGER, log_loss DOUBLE PRECISION,
        brier DOUBLE PRECISION, favourite_win_rate DOUBLE PRECISION, PRIMARY KEY (form, season)""",
          ["form", "season", "n", "log_loss", "brier", "favourite_win_rate"],
          per_season[["form", "season", "n", "log_loss", "brier", "favourite_win_rate"]].itertuples(index=False, name=None))
    write(cur, "season_postseason", """season INTEGER, team_abbreviation TEXT, conference TEXT, play_in BOOLEAN,
        playoffs BOOLEAN, top6 BOOLEAN, wins INTEGER, games INTEGER, position SMALLINT,
        PRIMARY KEY (season, team_abbreviation)""",
          ["season", "team_abbreviation", "conference", "play_in", "playoffs", "top6", "wins", "games", "position"],
          facts[["season", "team_abbreviation", "conference", "play_in", "playoffs", "top6", "wins", "games", "position"]].itertuples(index=False, name=None))
    write(cur, "season_sim_seasons", """season INTEGER PRIMARY KEY, games INTEGER, teams INTEGER, first_date DATE,
        last_date DATE, halfway_date DATE, sixty_date DATE, play_in BOOLEAN, hca_prev DOUBLE PRECISION,
        sigma_prev DOUBLE PRECISION, note TEXT""",
          ["season", "games", "teams", "first_date", "last_date", "halfway_date", "sixty_date", "play_in", "hca_prev",
           "sigma_prev", "note"], [tuple(r[c] for c in ["season", "games", "teams", "first_date", "last_date", "halfway_date",
                                                        "sixty_date", "play_in", "hca_prev", "sigma_prev", "note"]) for r in srows])
    prow = [("carry", params["carry"], "Slope of a season's SRS on the previous season's (through the origin), all franchise pairs"),
            ("tau2", params["tau2"], "Residual variance of that regression: the prior's variance (points per game, squared)"),
            ("hca_n0", params["hca_n0"], "Games' worth of prior for last season's home court (game variance / variance of year-to-year changes)"),
            ("pairs", float(params["pairs"]), "Franchise season pairs the constants were fitted on"),
            ("next_srs_r", params["next_srs_r"], "Correlation of a season's SRS with the previous season's"),
            ("runs", float(RUNS), "Simulated seasons per view"),
            ("min_games_for_srs", float(L.MIN_GAMES_FOR_SRS), "Games before SRS replaces raw venue-adjusted margins")]
    write(cur, "season_sim_params", "name TEXT PRIMARY KEY, value DOUBLE PRECISION, note TEXT", ["name", "value", "note"], prow)
    cur.execute("INSERT INTO season_sim_params VALUES ('chosen_form', NULL, %s)", (chosen,))
    bt_cols = ["season", "checkpoint", "checkpoint_date", "method", "team_abbreviation", "conference", "wins_now", "games_now",
               "position_now", "mean_wins", "wins_p10", "wins_p90", "p_playoffs", "p_top6", "p_playin", "p_first",
               "final_wins", "final_games", "final_position", "made_playoffs", "made_top6", "played_playin"]
    write(cur, "season_sim_backtest", """season INTEGER, checkpoint TEXT, checkpoint_date DATE, method TEXT,
        team_abbreviation TEXT, conference TEXT, wins_now INTEGER, games_now INTEGER, position_now SMALLINT,
        mean_wins DOUBLE PRECISION, wins_p10 DOUBLE PRECISION, wins_p90 DOUBLE PRECISION, p_playoffs DOUBLE PRECISION,
        p_top6 DOUBLE PRECISION, p_playin DOUBLE PRECISION, p_first DOUBLE PRECISION, final_wins INTEGER,
        final_games INTEGER, final_position SMALLINT, made_playoffs BOOLEAN, made_top6 BOOLEAN, played_playin BOOLEAN,
        PRIMARY KEY (season, checkpoint, method, team_abbreviation)""", bt_cols, bt[bt_cols].itertuples(index=False, name=None))
    write(cur, "season_sim_backtest_summary", """checkpoint TEXT, method TEXT, metric TEXT, value DOUBLE PRECISION,
        n INTEGER, note TEXT, PRIMARY KEY (checkpoint, method, metric)""", ["checkpoint", "method", "metric", "value", "n", "note"],
          bt_summary[["checkpoint", "method", "metric", "value", "n", "note"]].itertuples(index=False, name=None))
    write(cur, "season_sim_calibration", """checkpoint TEXT, method TEXT, target TEXT, bin SMALLINT, lo DOUBLE PRECISION,
        hi DOUBLE PRECISION, n INTEGER, predicted DOUBLE PRECISION, actual DOUBLE PRECISION,
        PRIMARY KEY (checkpoint, method, target, bin)""", ["checkpoint", "method", "target", "bin", "lo", "hi", "n", "predicted", "actual"],
          bt_cal[["checkpoint", "method", "target", "bin", "lo", "hi", "n", "predicted", "actual"]].itertuples(index=False, name=None))
    conn.commit()
    cur.execute("""SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables
                   WHERE relname IN ('game_pregame_odds','pregame_model_fit','pregame_calibration','pregame_model_seasons',
                   'season_postseason','season_sim_seasons','season_sim_params','season_sim_backtest',
                   'season_sim_backtest_summary','season_sim_calibration') ORDER BY 1""")
    print("sizes:", cur.fetchall())
    print(f"wrote {len(R):,} game odds, {len(bt):,} backtest rows ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
