"""Season simulator: playoff, play-in and seed odds from any date of any season
2010-11 to 2025-26, and the pre-game win-probability model behind it.

    GET /season-sim/options                 seasons on file with their dates and checkpoints,
                                            the chosen pre-game form and its coefficients
    GET /season-sim?season=&as_of=          10,000 simulated seasons from the morning of a date:
                                            per team the win range, playoff / play-in / top-6 /
                                            seed odds, next to what actually happened; plus that
                                            date's games with their pre-game odds
    GET /season-sim/model                   the four pre-game forms and their held-out scores,
                                            calibration by decile, log loss by season, rest
                                            effects, and the simulator's own backtest

Math in api/season_sim_lib.py (shared with scripts/build_season_sim.py, which
fits the model and backtests the simulator). Live views use the all-season
coefficients from `pregame_model_fit`; the backtest used coefficients fitted
without the season being tested (they differ in the third decimal). Every
read is cached per process: restart impact_api after rerunning the script.

The live season (round 9 step 4): a season with no postseason facts yet is
"live": its played games come from game_scores as for any season, its
remaining games from ESPN's schedule as the Forecast Ledger last read it
(api/season_sim_live.py), the default morning is today (US Eastern), and
the response says so (`info.live`, `info.live_note`). This is the app's
current model recomputed from today's results, separate from the Forecast
Ledger's locked odds (the forward test, frozen code); the page names both.
"""

from datetime import date
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

import season_sim_lib as L
import season_sim_live as SL
from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TABLES = ["game_scores", "team_game_fatigue", "game_pregame_odds", "pregame_model_fit", "season_postseason",
          "season_sim_seasons", "season_sim_params", "season_sim_backtest_summary", "season_sim_calibration"]
UPSTREAM = "ESPN scoreboard final scores (scripts/fetch_game_scores.py, scripts/fetch_postseason_games.py)"

METHOD = (
    "Pre-game odds: P(home wins) from a logistic regression on the expected margin (rating difference plus home "
    "court) and both sides' back-to-back flags, everything as of that morning. A team's rating is this season's SRS "
    "from the games so far blended with last season's final SRS as a prior (prior mean 0.6 x last season, worth "
    "about 11 games), the home court this season's blended with last season's. Four forms were scored "
    "leave-one-season-out on 19,118 games; this one had the lowest log loss and beats the Luck & Schedule page's "
    "own projection most in the first weeks of a season. Simulation: each of 10,000 runs draws every team's true "
    "rating from its posterior, plays every remaining game with those odds (the schedule as actually played), ranks "
    "the conference by win% with head-to-head, then conference record, then a coin flip as tiebreaks, and from "
    "2020-21 plays the play-in (7 v 8, 9 v 10, loser v winner; higher seed at home). Backtested at opening day, the "
    "halfway date and 60 games in for every season against what happened."
)

CHECKPOINT_LABELS = {"opening": "Opening day", "halfway": "Halfway", "sixty": "60 games in"}
METHOD_LABELS = {"model": "This simulator", "record": "Record carried forward (log5, no home court)",
                 "standings": "Standings today (in a playoff spot = 100%)"}


def _round(d, digits=3):
    out = {}
    for k, v in d.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float):
            v = round(v, digits)
        elif isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@lru_cache(maxsize=1)
def _stored():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.season_sim_seasons')")
        if cur.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No simulator data: run scripts/build_season_sim.py.")
        seasons = _rows(cur, "SELECT * FROM season_sim_seasons ORDER BY season")
        params = {r["name"]: r for r in _rows(cur, "SELECT name, value, note FROM season_sim_params")}
        fits = _rows(cur, "SELECT * FROM pregame_model_fit ORDER BY loso_log_loss")
        facts = _rows(cur, "SELECT * FROM season_postseason")
    chosen = next(f for f in fits if f["chosen"])
    with_facts = {f["season"] for f in facts}        # a season is live while it has no postseason facts
    for sr in seasons:
        sr["live"] = sr["season"] not in with_facts
        if sr["live"]:
            today = SL.clamp(SL.today_eastern(), sr["first_date"], sr["last_date"])
            sr["today_date"] = today
    return {"seasons": seasons, "params": params, "fits": fits, "chosen": chosen,
            "facts": {(f["season"], f["team_abbreviation"]): f for f in facts}}


@lru_cache(maxsize=4)
def _schedule(season):
    """The live season's schedule (every counting game, played or not) from the ledger's nightly ESPN read."""
    with get_db() as conn:
        return SL.schedule(conn.cursor(), season)


LIVE_NOTE = ("Live season: the app's current model, recomputed from the results so far (ratings = last season's final SRS "
             "as the prior, updated by this season's games); the games still to play are ESPN's schedule as the Forecast "
             "Ledger last read it. This is not the Forecast Ledger: the ledger's odds were locked before opening night "
             "and are scored with frozen code (the forward test); these can change as the code does.")


@lru_cache(maxsize=8)
def _games(season):
    """Prepared team-game rows (with rest) for a season and its predecessor."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(L.GAMES_REST_SQL.format(where="WHERE g.season IN (%s, %s)"), (season - 1, season))
        df = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
    df = L.prepare_rest(df)
    return df[df.season == season], df[df.season == season - 1]


def _season_info(season):
    st = _stored()
    available = [s["season"] for s in st["seasons"]]
    season = season or available[-1]
    if season not in available:
        raise HTTPException(status_code=404, detail=f"No simulator data for {season}; seasons on file: {available[0]}-{available[-1]}.")
    return season, next(s for s in st["seasons"] if s["season"] == season)


def _params():
    p = _stored()["params"]
    return {"carry": p["carry"]["value"], "tau2": p["tau2"]["value"], "hca_n0": p["hca_n0"]["value"]}


@router.get("/season-sim/options")
def season_sim_options():
    st = _stored()
    chosen = st["chosen"]
    p = st["params"]
    return {
        "seasons": [_round(s) for s in st["seasons"]],
        "default_season": st["seasons"][-1]["season"],
        "checkpoints": CHECKPOINT_LABELS,
        "form": chosen["form"], "form_label": chosen["label"], "beta": chosen["beta"],
        "params": {k: round(p[k]["value"], 4) for k in ("carry", "tau2", "hca_n0", "runs", "next_srs_r")},
        "runs": int(p["runs"]["value"]),
        "play_in_from": L.PLAY_IN_FROM,
        "tie_rule": L.TIE_RULE,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


@lru_cache(maxsize=128)
def _simulate(season, as_of):
    st = _stored()
    info = next(s for s in st["seasons"] if s["season"] == season)
    sg, prev = _games(season)
    live = bool(info.get("live"))
    sched = _schedule(season) if live else None
    teams = sorted(set(sg.team_abbreviation.unique()) | (set(sched.home) | set(sched.away) if live and len(sched) else set()))
    prior = L.season_prior(prev)
    params = _params()
    played = sg[sg.game_date < as_of]
    if live:
        if sched.empty:
            raise HTTPException(status_code=503, detail=f"No schedule on file for {season} (ledger_results / ledger_schedule): "
                                                        "run scripts/ledger_update.py.")
        left = SL.remaining_home_rows(sched, as_of)
    else:
        left = L.home_rows(sg[sg.game_date >= as_of])
    rat = L.ratings_as_of(played, teams, prior, params)
    standings = L.Standings(teams, played)
    pos_now = L.current_positions(standings, season, np.random.default_rng(L.sim_seed(season, as_of, "now")))
    beta = st["chosen"]["beta"]
    runs = int(st["params"]["runs"]["value"])
    seed = L.sim_seed(season, as_of, "model")
    rng = np.random.default_rng(seed)
    draws = L.draw_ratings(rat["r_post"], rat["var_post"], teams, runs, rng)
    sim = L.simulate(standings, left, season, L.model_p_matrix(left, standings, beta, rat["hca"]), draws, beta,
                     rat["hca"], runs, rng)
    summ = L.summarize(sim, standings, season)
    left_team = pd.concat([left[["home", "away"]].rename(columns={"home": "team", "away": "opp"}).assign(home=True),
                           left[["home", "away"]].rename(columns={"away": "team", "home": "opp"}).assign(home=False)])
    left_team["opp_r"] = left_team.opp.map(rat["r_post"])
    rem = left_team.groupby("team").agg(games_left=("opp", "size"), home_left=("home", "sum"), rem_sos=("opp_r", "mean"))
    out = {"East": [], "West": []}
    for t in teams:
        i = standings.idx[t]
        s = summ[t]
        f = st["facts"].get((season, t), {})
        r = rem.loc[t] if t in rem.index else None
        row = {
            "team": t, "wins": int(standings.wins[i]), "losses": int(standings.games[i] - standings.wins[i]),
            "position_now": pos_now[t],
            "rating": rat["r_post"][t], "rating_sd": float(np.sqrt(rat["var_post"][t])),
            "rating_now": rat["r_cur"][t], "prior_rating": params["carry"] * prior["ratings"].get(L.franchise(t), 0.0),
            "games_left": int(r.games_left) if r is not None else 0,
            "home_left": int(r.home_left) if r is not None else 0,
            "rem_sos": float(r.rem_sos) if r is not None else None,
            "mean_wins": s["mean_wins"], "sd_wins": s["sd_wins"], "wins_p10": s["wins_p10"], "wins_p50": s["wins_p50"],
            "wins_p90": s["wins_p90"], "wins_hist": s["wins_hist"], "games_final": s["games_final"],
            "p_playoffs": s["p_playoffs"], "p_top6": s["p_top6"], "p_playin": s["p_playin"], "p_first": s["p_first"],
            "p_seed": [round(x, 4) for x in s["p_seed"]],
            "final": {"wins": f.get("wins"), "losses": (f["games"] - f["wins"]) if f else None,
                      "position": f.get("position"), "playoffs": f.get("playoffs"), "play_in": f.get("play_in"),
                      "top6": f.get("top6")},
        }
        out[L.CONFERENCE[t]].append(_round(row))
    for conf in out:
        out[conf].sort(key=lambda r: (-r["p_playoffs"], -r["mean_wins"]))
    checkpoint = next((k for k in ("opening", "halfway", "sixty") if info[f"{k}_date" if k != "opening" else "first_date"] == as_of), None)
    return {
        "season": season, "season_label": f"{season - 1}-{str(season)[-2:]}", "as_of": as_of.isoformat(),
        "checkpoint": checkpoint, "play_in": season >= L.PLAY_IN_FROM,
        "info": _round({"played_games": rat["n_games"], "left_games": int(len(left)), "hca": rat["hca"],
                        "hca_now": rat["hca_cur"], "sigma": rat["sigma"], "shrink": rat["shrink"], "runs": runs,
                        "seed": seed, "prior_games_worth": rat["sigma"] ** 2 / params["tau2"],
                        "ratings_from_srs": rat["n_games"] >= L.MIN_GAMES_FOR_SRS,
                        "first_date": info["first_date"], "last_date": info["last_date"],
                        "halfway_date": info["halfway_date"], "sixty_date": info["sixty_date"], "note": info["note"],
                        "live": live, "today_date": info.get("today_date"),
                        "played_through": sg.game_date.max() if live and len(sg) else None,
                        "schedule_source": sched.source.iloc[0] if live and len(sched) else None,
                        "scheduled_games": int(len(sched)) if live else None,
                        "live_note": LIVE_NOTE if live else None}),
        "conferences": out,
    }


@router.get("/season-sim")
def season_sim(season: Optional[int] = None, as_of: Optional[date] = None):
    season, info = _season_info(season)
    as_of = as_of or (info["today_date"] if info.get("live") else info["halfway_date"])
    if as_of < info["first_date"] or as_of > info["last_date"]:
        raise HTTPException(status_code=400, detail=(
            f"Pick a date from {info['first_date'].isoformat()} (opening day: nothing played yet) to "
            f"{info['last_date'].isoformat()} (the last day of the regular season) for {season}."))
    data = dict(_simulate(season, as_of))
    with get_db() as conn:
        cur = conn.cursor()
        games = _rows(cur, """SELECT game_id, home, away, p_home, pts_home, pts_away, home_won, home_b2b, away_b2b, venue,
                                     exp_margin FROM game_pregame_odds WHERE season = %s AND game_date = %s
                              ORDER BY game_id""", (season, as_of))
    st = _stored()
    data["games_on_date"] = [_round(g) for g in games]
    data["form"] = st["chosen"]["form"]
    data["form_label"] = st["chosen"]["label"]
    data["beta"] = st["chosen"]["beta"]
    data["tie_rule"] = L.TIE_RULE
    data["method"] = METHOD
    data["_source"] = make_source(TABLES, UPSTREAM)
    return data


@lru_cache(maxsize=1)
def _model():
    with get_db() as conn:
        cur = conn.cursor()
        cal = _rows(cur, "SELECT form, decile, lo, hi, n, predicted, actual FROM pregame_calibration ORDER BY form, decile")
        per_season = _rows(cur, "SELECT form, season, n, log_loss, brier, favourite_win_rate FROM pregame_model_seasons ORDER BY season, form")
        summary = _rows(cur, "SELECT checkpoint, method, metric, value, n, note FROM season_sim_backtest_summary ORDER BY checkpoint, method, metric")
        bt_cal = _rows(cur, "SELECT checkpoint, method, target, bin, lo, hi, n, predicted, actual FROM season_sim_calibration ORDER BY checkpoint, method, target, bin")
        rest = _rows(cur, """SELECT home_b2b, away_b2b, COUNT(*) AS n, AVG(home_won::int) AS home_win_rate,
                                    AVG(p_prior) AS p_prior_mean, AVG(p_home) AS p_home_mean
                             FROM game_pregame_odds GROUP BY 1, 2 ORDER BY 1, 2""")
        era = _rows(cur, """SELECT CASE WHEN season <= 2020 THEN 'to_2020' ELSE 'from_2021' END AS era, COUNT(*) AS n,
                                   AVG(home_won::int) AS home_win_rate,
                                   AVG(CASE WHEN (p_home >= 0.5) = home_won THEN 1 ELSE 0 END) AS favourite_win_rate
                            FROM game_pregame_odds GROUP BY 1 ORDER BY 1""")
        bucket = _rows(cur, """SELECT CASE WHEN LEAST(home_games, away_games) <= 5 THEN '0-5'
                                           WHEN LEAST(home_games, away_games) <= 15 THEN '6-15'
                                           WHEN LEAST(home_games, away_games) <= 30 THEN '16-30'
                                           WHEN LEAST(home_games, away_games) <= 50 THEN '31-50' ELSE '51+' END AS games_played,
                                      MIN(LEAST(home_games, away_games)) AS lo, COUNT(*) AS n,
                                      -AVG(home_won::int * LN(GREATEST(p_baseline, 1e-6)) + (1 - home_won::int) * LN(GREATEST(1 - p_baseline, 1e-6))) AS baseline,
                                      -AVG(home_won::int * LN(GREATEST(p_current, 1e-6)) + (1 - home_won::int) * LN(GREATEST(1 - p_current, 1e-6))) AS current,
                                      -AVG(home_won::int * LN(GREATEST(p_prior, 1e-6)) + (1 - home_won::int) * LN(GREATEST(1 - p_prior, 1e-6))) AS prior,
                                      -AVG(home_won::int * LN(GREATEST(p_prior_rest, 1e-6)) + (1 - home_won::int) * LN(GREATEST(1 - p_prior_rest, 1e-6))) AS prior_rest
                               FROM game_pregame_odds GROUP BY 1 ORDER BY lo""")
    st = _stored()
    by_form = {}
    for c in cal:
        by_form.setdefault(c["form"], []).append(_round(c, 4))
    return {
        "forms": [_round({**f, "beta": f["beta"]}, 4) for f in st["fits"]],
        "chosen": st["chosen"]["form"],
        "calibration": by_form,
        "by_season": [_round(r, 4) for r in per_season],
        "by_games_played": [_round(r, 4) for r in bucket],
        "rest": [_round(r, 4) for r in rest],
        "era": [_round(r, 4) for r in era],
        "params": {k: _round(v, 4) for k, v in st["params"].items()},
        "backtest": {"summary": [_round(r, 4) for r in summary], "calibration": [_round(r, 4) for r in bt_cal],
                     "checkpoints": CHECKPOINT_LABELS, "methods": METHOD_LABELS},
        "form_labels": L.FORM_LABELS,
    }


@router.get("/season-sim/model")
def season_sim_model():
    return {**_model(), "method": METHOD, "tie_rule": L.TIE_RULE, "_source": make_source(TABLES, UPSTREAM)}
