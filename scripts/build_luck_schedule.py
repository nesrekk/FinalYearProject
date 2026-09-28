"""
build_luck_schedule.py
=======================
Luck and schedule strength for every team-season 2009-10 to 2025-26, from
the real final score of every regular-season game (`game_scores`, fetched by
fetch_game_scores.py; season point totals equal Basketball-Reference's for
all 510 team-seasons).

What it computes:
  expected wins  the season win% a team's points for and against usually
                 produce. Three curves are fitted on all team-seasons
                 (weighted by games): linear in margin per game, a normal
                 curve in margin, and Pythagorean in points (exponent
                 fitted). The one with the lowest leave-one-season-out error
                 is used; all three are stored with their errors.
  luck           actual wins minus expected wins.
  close games    record in games decided by 3 or fewer and 5 or fewer points,
                 and in overtime games.
  SRS            least-squares team ratings per season: each game's margin =
                 home court + rating(team) - rating(opponent), ratings summing
                 to zero (neutral-site games, including the 2019-20 Orlando
                 restart, get no home court). SOS = the mean rating of the
                 opponents a team actually played. Unlike Basketball-
                 Reference's SRS it has a home-court term; the two are
                 compared below.

Validation (stored in luck_schedule_validation):
  * does luck carry over? correlation of a franchise's luck (per 82 games)
    with its luck the next season, of close-game win% with next season's,
    and for contrast of margin per game with next season's; and a
    regression of next season's win% on this season's expected win% and
    luck (franchise-clustered SE);
  * mid-season: at each season's halfway date, predicting every team's
    rest-of-season win% from (a) its record so far, (b) its expected win%
    from points so far, (c) ratings so far shrunk toward average plus the
    schedule it has left; RMSE of each;
  * SRS against Basketball-Reference's published SRS (Kaggle export).

Tables written (dropped and rebuilt):
  team_luck_schedule        one row per team-season
  luck_schedule_seasons     one row per season: games, home court, SD of a
                            game's margin around the ratings, league close-game
                            share, whether the season is complete, and the
                            halfway date (the date of the season's middle game)
  luck_model_fit            the three expected-win curves and their errors
  luck_schedule_validation  the checks above (metric, value, n, interval, note)

Usage:
    cd scripts && python3 build_luck_schedule.py
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from scipy.optimize import minimize_scalar
from scipy.stats import norm

from db_config import DB_CONFIG
from stats_lib import wls_cluster

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from luck_lib import (CLOSE_MARGINS, FRANCHISE, GAMES_SQL, as_of, expected_win_pct,  # noqa: E402
                      prepare, schedule_strength, srs_fit, team_table)

KAGGLE = Path(__file__).resolve().parent.parent / "nba_data" / "kaggle_1947_present" / "Team Summaries.csv"
BREF_ABBR = {"PHO": "PHX", "BRK": "BKN", "CHO": "CHA"}
FULL_SEASON = 82


def _fit(method, df):
    """Fit one expected-win curve by games-weighted least squares on win%."""
    y, w = df.win_pct.to_numpy(), df.games.to_numpy(float)

    def loss(p):
        pred = expected_win_pct(method, p, df.pts_for, df.pts_against, df.games)
        return float(np.sum(w * (y - pred) ** 2))

    bounds = {"linear": (0.005, 0.1), "normal": (3, 30), "pythagorean": (5, 30)}[method]
    return minimize_scalar(loss, bounds=bounds, method="bounded").x


def fit_curves(ts):
    out = []
    for method in ("linear", "normal", "pythagorean"):
        param = _fit(method, ts)
        err = []
        for s in sorted(ts.season.unique()):
            p = _fit(method, ts[ts.season != s])
            h = ts[ts.season == s]
            pred = expected_win_pct(method, p, h.pts_for, h.pts_against, h.games)
            err.extend(((h.win_pct - pred) * FULL_SEASON).tolist())
        err = np.array(err)
        pred_all = expected_win_pct(method, param, ts.pts_for, ts.pts_against, ts.games)
        resid = ts.win_pct - pred_all
        r2 = 1 - np.sum(resid ** 2) / np.sum((ts.win_pct - ts.win_pct.mean()) ** 2)
        out.append({"method": method, "param": float(param), "loso_rmse_wins": float(np.sqrt(np.mean(err ** 2))),
                    "loso_mae_wins": float(np.mean(np.abs(err))), "r2": float(r2), "n": len(ts)})
    fits = pd.DataFrame(out)
    fits["chosen"] = fits.loso_rmse_wins == fits.loso_rmse_wins.min()
    return fits


def corr_ci(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    r = float(np.corrcoef(x, y)[0, 1])
    z, se = np.arctanh(r), 1 / np.sqrt(n - 3)
    return r, float(np.tanh(z - 1.96 * se)), float(np.tanh(z + 1.96 * se)), n


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    games = prepare(pd.read_sql(GAMES_SQL.format(where=""), conn))
    print(f"{len(games):,} team-games, {games.game_id.nunique():,} games, seasons "
          f"{games.season.min()}-{games.season.max()}")

    # --- per season: ratings, home court, records ---------------------------
    parts, season_rows = [], []
    fatigue = pd.read_sql("SELECT season, COUNT(DISTINCT game_id) AS n FROM team_game_fatigue GROUP BY 1", conn)
    for season, sg in games.groupby("season"):
        ratings, hca, sigma, _ = srs_fit(sg)
        tbl = team_table(sg)
        tbl["srs"] = pd.Series(ratings)
        tbl = tbl.join(schedule_strength(sg, ratings)[["sos"]])
        tbl["season"] = season
        parts.append(tbl.reset_index())
        n_games = sg.game_id.nunique()
        close = {m: float((sg.drop_duplicates("game_id").margin.abs() <= m).mean()) for m in CLOSE_MARGINS}
        season_rows.append({"season": season, "games": n_games, "hca": hca, "sigma": sigma,
                            "close3_share": close[3], "close5_share": close[5],
                            "ot_share": float((sg.drop_duplicates("game_id").periods > 4).mean()),
                            "first_date": sg.game_date.min(), "last_date": sg.game_date.max()})
    ts = pd.concat(parts, ignore_index=True)
    ts["win_pct"] = ts.wins / ts.games

    # --- expected wins ------------------------------------------------------
    fits = fit_curves(ts)
    print(fits.to_string(index=False))
    best = fits[fits.chosen].iloc[0]
    ts["exp_win_pct"] = expected_win_pct(best.method, best.param, ts.pts_for, ts.pts_against, ts.games)
    ts["exp_wins"] = ts.exp_win_pct * ts.games
    ts["luck"] = ts.wins - ts.exp_wins
    ts["luck_per82"] = ts.luck / ts.games * FULL_SEASON
    ts["srs_rank"] = ts.groupby("season").srs.rank(ascending=False, method="min").astype(int)
    ts["luck_rank"] = ts.groupby("season").luck.rank(ascending=False, method="min").astype(int)
    ts["franchise"] = ts.team_abbreviation.map(lambda t: FRANCHISE.get(t, t))

    seasons = pd.DataFrame(season_rows)
    seasons = seasons.merge(fatigue.rename(columns={"n": "scheduled"}), on="season")
    seasons["complete"] = seasons.games == seasons.scheduled
    print(seasons[["season", "games", "hca", "sigma", "close3_share", "close5_share"]].round(3).to_string(index=False))

    # --- validation ---------------------------------------------------------
    val = []

    def add(metric, value, n=None, lo=None, hi=None, note=""):
        val.append({"metric": metric, "value": value, "n": n, "lo": lo, "hi": hi, "note": note})

    nxt = ts[["franchise", "season", "luck_per82", "win_pct", "exp_win_pct", "mov", "close3_w", "close3_l",
              "close5_w", "close5_l"]].copy()
    nxt["season"] -= 1
    pairs = ts.merge(nxt, on=["franchise", "season"], suffixes=("", "_next"))
    r, lo, hi, n = corr_ci(pairs.luck_per82, pairs.luck_per82_next)
    add("luck_next_luck_r", r, n, lo, hi, "Correlation of a franchise's luck (wins above expected, per 82 games) with its luck the next season")
    r, lo, hi, n = corr_ci(pairs.mov, pairs.mov_next)
    add("mov_next_mov_r", r, n, lo, hi, "Correlation of margin per game with next season's margin per game (for contrast)")
    for m in CLOSE_MARGINS:
        cg = pairs[(pairs[f"close{m}_w"] + pairs[f"close{m}_l"] >= 8)
                   & (pairs[f"close{m}_w_next"] + pairs[f"close{m}_l_next"] >= 8)]
        a = cg[f"close{m}_w"] / (cg[f"close{m}_w"] + cg[f"close{m}_l"])
        b = cg[f"close{m}_w_next"] / (cg[f"close{m}_w_next"] + cg[f"close{m}_l_next"])
        r, lo, hi, n = corr_ci(a, b)
        add(f"close{m}_next_close{m}_r", r, n, lo, hi,
            f"Correlation of win% in games decided by {m} or fewer points with next season's (teams with 8+ such games both seasons)")
    luck_rate = pairs.win_pct - pairs.exp_win_pct
    X = np.column_stack([np.ones(len(pairs)), pairs.exp_win_pct, luck_rate])
    reg = wls_cluster(pairs.win_pct_next, X, np.ones(len(pairs)), pairs.franchise)
    beta, se = reg["beta"], reg["se"]
    add("next_winpct_coef_exp", float(beta[1]), len(pairs), float(beta[1] - 1.96 * se[1]), float(beta[1] + 1.96 * se[1]),
        "Next season's win% on this season's expected win%: coefficient (franchise-clustered 95% interval)")
    add("next_winpct_coef_luck", float(beta[2]), len(pairs), float(beta[2] - 1.96 * se[2]), float(beta[2] + 1.96 * se[2]),
        "Next season's win% on this season's luck (win% minus expected), holding expected win% fixed")
    for name, col in (("record", "win_pct"), ("expected", "exp_win_pct")):
        r, lo, hi, n = corr_ci(pairs[col], pairs.win_pct_next)
        add(f"next_winpct_r_{name}", r, n, lo, hi,
            f"Correlation of this season's {'actual' if name == 'record' else 'expected'} win% with next season's win%")

    # Mid-season: at the date half the season's games are done.
    mid_rows = []
    for season, sg in games.groupby("season"):
        per_game = sg.drop_duplicates("game_id").sort_values("game_date")
        cutoff = per_game.game_date.iloc[len(per_game) // 2]
        tbl, info = as_of(sg, cutoff, best.method, best.param)
        tbl = tbl[tbl.rem_games > 0]
        rest_pct = tbl.rem_actual_wins / tbl.rem_games
        preds = {"record": tbl.wins / tbl.games, "expected": tbl.exp_win_pct,
                 "srs_schedule": tbl.rem_exp_wins / tbl.rem_games}
        for name, p in preds.items():
            mid_rows.append({"season": season, "method": name, "n": len(tbl),
                             "sq_err_wins": float(np.sum(((rest_pct - p) * tbl.rem_games) ** 2)),
                             "shrink": info["shrink"], "cutoff": cutoff})
    mid = pd.DataFrame(mid_rows)
    seasons = seasons.merge(mid.drop_duplicates("season")[["season", "cutoff"]].rename(columns={"cutoff": "halfway_date"}),
                            on="season")
    for name, g in mid.groupby("method"):
        add(f"midseason_rmse_{name}", float(np.sqrt(g.sq_err_wins.sum() / g.n.sum())), int(g.n.sum()), note={
            "record": "Rest-of-season wins predicted from the record at the halfway date: RMSE in wins",
            "expected": "Rest-of-season wins predicted from expected win% (points) at the halfway date: RMSE in wins",
            "srs_schedule": "Rest-of-season wins predicted from ratings at the halfway date (shrunk toward average) "
                            "and the actual remaining schedule: RMSE in wins",
        }[name])
    add("midseason_shrink_mean", float(mid.drop_duplicates("season").shrink.mean()), int(mid.season.nunique()),
        note="Average share of the halfway ratings' spread kept after shrinking toward average")

    # Basketball-Reference comparison.
    if KAGGLE.exists():
        br = pd.read_csv(KAGGLE)
        br = br[(br.lg == "NBA") & br.abbreviation.notna() & br.season.isin(ts.season.unique())].copy()
        br["team_abbreviation"] = br.abbreviation.map(lambda a: BREF_ABBR.get(a, a))
        cmp_ = ts.merge(br[["season", "team_abbreviation", "w", "mov", "srs", "sos", "pw"]],
                        on=["season", "team_abbreviation"], suffixes=("", "_br"))
        add("bref_matched", float(len(cmp_)), len(ts), note="Team-seasons matched to Basketball-Reference's team summaries")
        add("bref_wins_mismatch", float((cmp_.wins != cmp_.w).sum()), len(cmp_), note="Team-seasons whose W differs from Basketball-Reference")
        add("bref_mov_max_abs_diff", float((cmp_.mov - cmp_.mov_br).abs().max()), len(cmp_),
            note="Largest gap between our margin per game and Basketball-Reference's (theirs is rounded to 0.01)")
        r, lo, hi, n = corr_ci(cmp_.srs, cmp_.srs_br)
        add("bref_srs_r", r, n, lo, hi, "Correlation of our SRS with Basketball-Reference's published SRS")
        add("bref_srs_mean_abs_diff", float((cmp_.srs - cmp_.srs_br).abs().mean()), n,
            note="Mean absolute gap to Basketball-Reference's SRS (theirs has no home-court term)")
        add("bref_srs_max_abs_diff", float((cmp_.srs - cmp_.srs_br).abs().max()), n)
        add("bref_pythag_wins_mae", float((cmp_.exp_wins - cmp_.pw).abs().mean()), n,
            note="Mean absolute gap between our expected wins and Basketball-Reference's Pythagorean wins (exponent 14)")
        worst = cmp_.assign(d=(cmp_.srs - cmp_.srs_br).abs()).nlargest(3, "d")
        print("largest SRS gaps vs Basketball-Reference:")
        print(worst[["season", "team_abbreviation", "srs", "srs_br", "sos", "sos_br"]].round(2).to_string(index=False))
    else:
        print(f"(skipping the Basketball-Reference comparison: {KAGGLE} not found)")

    vdf = pd.DataFrame(val)
    print(vdf[["metric", "value", "n", "lo", "hi"]].round(3).to_string(index=False))

    # --- sniff tests printed ------------------------------------------------
    for season in (2016, 2017, 2024, 2025, 2026):
        top = ts[ts.season == season].nsmallest(3, "srs_rank")
        print(season, ", ".join(f"{r.team_abbreviation} {r.wins}-{r.losses} SRS {r.srs:+.2f}" for r in top.itertuples()))
    print("luckiest:", ", ".join(f"{r.season} {r.team_abbreviation} {r.luck:+.1f}"
                                 for r in ts.nlargest(5, "luck").itertuples()))
    print("unluckiest:", ", ".join(f"{r.season} {r.team_abbreviation} {r.luck:+.1f}"
                                   for r in ts.nsmallest(5, "luck").itertuples()))

    # --- write --------------------------------------------------------------
    cur = conn.cursor()
    cols = ["season", "team_abbreviation", "franchise", "games", "wins", "losses", "win_pct", "pts_for", "pts_against",
            "mov", "exp_win_pct", "exp_wins", "luck", "luck_per82", "luck_rank", "close3_w", "close3_l", "close5_w",
            "close5_l", "ot_w", "ot_l", "srs", "sos", "srs_rank"]
    cur.execute("DROP TABLE IF EXISTS team_luck_schedule")
    cur.execute("""CREATE TABLE team_luck_schedule (
        season INTEGER, team_abbreviation TEXT, franchise TEXT, games INTEGER, wins INTEGER, losses INTEGER,
        win_pct DOUBLE PRECISION, pts_for INTEGER, pts_against INTEGER, mov DOUBLE PRECISION,
        exp_win_pct DOUBLE PRECISION, exp_wins DOUBLE PRECISION, luck DOUBLE PRECISION, luck_per82 DOUBLE PRECISION,
        luck_rank INTEGER, close3_w INTEGER, close3_l INTEGER, close5_w INTEGER, close5_l INTEGER,
        ot_w INTEGER, ot_l INTEGER, srs DOUBLE PRECISION, sos DOUBLE PRECISION, srs_rank INTEGER,
        PRIMARY KEY (season, team_abbreviation))""")
    execute_values(cur, f"INSERT INTO team_luck_schedule ({', '.join(cols)}) VALUES %s",
                   [tuple(v.item() if hasattr(v, "item") else v for v in row)
                    for row in ts[cols].itertuples(index=False, name=None)])

    cur.execute("DROP TABLE IF EXISTS luck_schedule_seasons")
    cur.execute("""CREATE TABLE luck_schedule_seasons (
        season INTEGER PRIMARY KEY, games INTEGER, scheduled INTEGER, complete BOOLEAN, hca DOUBLE PRECISION,
        sigma DOUBLE PRECISION, close3_share DOUBLE PRECISION, close5_share DOUBLE PRECISION,
        ot_share DOUBLE PRECISION, first_date DATE, last_date DATE, halfway_date DATE)""")
    scols = ["season", "games", "scheduled", "complete", "hca", "sigma", "close3_share", "close5_share", "ot_share",
             "first_date", "last_date", "halfway_date"]
    execute_values(cur, f"INSERT INTO luck_schedule_seasons ({', '.join(scols)}) VALUES %s",
                   [tuple(v.item() if hasattr(v, "item") else v for v in row)
                    for row in seasons[scols].itertuples(index=False, name=None)])

    cur.execute("DROP TABLE IF EXISTS luck_model_fit")
    cur.execute("""CREATE TABLE luck_model_fit (method TEXT PRIMARY KEY, param DOUBLE PRECISION,
        loso_rmse_wins DOUBLE PRECISION, loso_mae_wins DOUBLE PRECISION, r2 DOUBLE PRECISION, n INTEGER,
        chosen BOOLEAN)""")
    execute_values(cur, "INSERT INTO luck_model_fit VALUES %s",
                   [(r.method, r.param, r.loso_rmse_wins, r.loso_mae_wins, r.r2, int(r.n), bool(r.chosen))
                    for r in fits.itertuples()])

    cur.execute("DROP TABLE IF EXISTS luck_schedule_validation")
    cur.execute("""CREATE TABLE luck_schedule_validation (metric TEXT PRIMARY KEY, value DOUBLE PRECISION,
        n INTEGER, lo DOUBLE PRECISION, hi DOUBLE PRECISION, note TEXT)""")
    execute_values(cur, "INSERT INTO luck_schedule_validation VALUES %s",
                   [(r.metric, float(r.value), None if pd.isna(r.n) else int(r.n),
                     None if pd.isna(r.lo) else float(r.lo), None if pd.isna(r.hi) else float(r.hi), r.note)
                    for r in vdf.itertuples()])
    conn.commit()
    print(f"wrote {len(ts)} team-seasons, {len(seasons)} seasons, {len(vdf)} checks ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
