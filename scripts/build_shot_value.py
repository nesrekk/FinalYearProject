"""
build_shot_value.py
===================
Shot Value Added (round 6, step 8): every regular-season attempt from 2020-21
on is priced twice before its game - as an average shooter would make it, and
as *this* shooter would, given everything he had shown up to the day before -
so a season's shooting points split into what the shots were worth, what the
shooter's record said his shooting adds (skill) and what he made beyond that
(the season's run, luck included). The model is scripts/shot_value_lib.py,
shared with scripts/paper_xrapm.py, which builds the shooter-aware
expected-points RAPM on these prices.

Why (round 5 step 4): expected-points RAPM priced every field goal with a
shooter-blind location model, so a lineup with a great shooter was expected to
score like a lineup of average shooters; its ratings lost shooting skill along
with shooting luck and predicted next season's margins worse than plain RAPM.
The paper's limitation (viii) adds that the blind model was cross-fitted over
all six seasons, so a season's prices used later seasons' shots. Both are fixed
here: the location model that prices season S never saw a shot from S or later
(nor from the shooter's own fold of players), and the shooter's skill comes only
from his attempts before the game.

Pipeline
  1. Shots: every regular-season chart shot, read the way build_shot_making.py
     reads them (load_shots + add_features: same filter, same features, id order).
  2. Look-ahead-free location models: for each priced season S (2020-21 to
     2025-26) and each of the five player folds (build_shot_making's by-player
     folds, reproduced exactly: same seed and shuffle; checked against shot_xfg),
     a gradient-boosting model with build_shot_making's parameters is fitted on
     every shot of 1996-97 to S-1 by the other folds' players and prices its own
     fold's shots of 2010-11 to S. 30 fits.
  3. Skill classes: rim (a two in the restricted area), mid (any other two),
     three, ft. Free-throw history: player_season_stats before 2020-21 (games x
     per-game FTA, makes = attempts x FT%, rounded; the per-game averages carry a
     rounding error of a few attempts a season), player_game_lines per game from
     2020-21 (the three NBA Cup finals dropped: no game_scores row). A free throw's
     blind price is the league FT% (league_season_averages) of the season before
     the priced one, of the season itself for history. Debut = player_first_season,
     or the first season the player is seen if that is earlier or missing.
  4. Hyperparameters, empirical Bayes (shot_value_lib.fit_class): per class,
     (mu0, v0, phi, q) maximise the Laplace marginal likelihood of 2010-11 to
     2019-20 (field goals priced by the 2020-21 fold models, so no priced season
     is used), from three starts (all stored). delta's shrinkage per class from
     the same seasons' league make rates.
  5. Each priced season: the filter over 2010-11 to S-1 (with S's models) gives
     every player's skill carried into S; then date by date through S. Prices:
     p_blind (the fold model), p_lf (+ the league level so far: look-ahead free,
     shooter-blind), p_sa (+ the shooter's skill before the date). A
     preseason-only variant (skill frozen at the season start) is scored in the
     validation table only.

Definitions in shot_value_added (field goals; free throws the same with ft_):
  x_blind   sum of p_lf x shot value: an average shooter on these shots, at the
            league's level so far;
  x_aware   sum of p_sa x value: this shooter on these shots, as known before each game;
  skill_pts = x_aware - x_blind   what his record said his shooting adds;
  above_pts = pts - x_aware        what he made beyond that this season;
  total_pts = pts - x_blind        points above an average shooter (= skill + above).
  sva = skill_pts + ft_skill_pts   Shot Value Added: the repeatable part.
The league as a whole beats or misses its own forecast in a season when its
level drifts during the season (the running delta lags a trend); the validation
table stores that league gap per class so the page can show it beside a player's.

Tables (dropped and rebuilt)
  shot_value_shots       one row per regular-season chart shot 2020-21 on (player_shots.id): season, date,
                         player, class, made, the game's RAPM fold (build_rapm.game_fold of 'espn_' + its
                         ESPN id), p_blind, p_lf, p_sa (~1.28M rows);
  shot_value_states      per priced season x player (every player with a chart shot or a game line that
                         season) x class: skill carried in and after the season (log-odds mean and sd), the
                         season's attempts and makes;
  shot_value_added       per player-season: attempts by class, points, x_blind, x_aware, skill, above and
                         total points, free throws the same, skills in percentage points at the league's rate,
                         ranks among 200+ FGA;
  shot_value_fit         per class: hyperparameters, -2 log likelihood, the three starts, delta's variance and
                         the data they rest on; plus a 'models' row per priced season (the fits' iterations);
  shot_value_validation  per season (and pooled) x class x price: attempts, log loss, Brier, mean price and
                         make rate, and shooter-aware minus blind log loss with a 95% interval (bootstrap over
                         games); plus year-to-year correlations of the parts (class 'yty').

Runtime ~20 minutes on the M5 Air with OMP_NUM_THREADS=4 (the 30 fits ~14, the
hyperparameters ~6). Deterministic: the fits use build_shot_making's random_state,
the optimiser starts are fixed and the bootstrap is seeded (SEED).

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && OMP_NUM_THREADS=4 python3 build_shot_value.py
Then paper_xrapm.py, paper_eval.py --only impact, paper_tests.py --only impact,
rebuild_all.sh paper-inputs; restart impact_api. Rerun after a player_shots
reload, build_player_game_lines.py, build_player_profile_data.py /
build_first_nba_season.py or a player_season_stats load.
"""

import io
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

import build_rapm as R
import build_shot_making as S
import shot_value_lib as V
from db_config import DB_CONFIG

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

SEED = 20261002
BOOT = 2000
MIN_FGA = 200          # ranks and year-to-year pairs (build_shot_making's floor)
MIN_FTA = 50
T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


label = V.label


# ── 1-2. Shots and the look-ahead-free location models ───────────────────────

def player_folds(df):
    """build_shot_making.crossfit()'s fold per shot, reproduced: same unique players, same seed, same shuffle."""
    players = np.unique(df.player_id.to_numpy())
    rng = np.random.default_rng(S.SEED)
    rng.shuffle(players)
    fold_of_player = pd.Series(np.arange(len(players)) % S.N_FOLDS, index=players)
    return fold_of_player.reindex(df.player_id.to_numpy()).to_numpy()


def location_models(df, fold):
    """{S: P(make) per shot (NaN outside 2010-11..S)} from five fold models fitted on 1996-97..S-1."""
    X = S.hgb_matrix(df)
    y = df.made.to_numpy()
    season = df.season.to_numpy()
    out, iters = {}, {}
    for s in V.PRICED:
        p = np.full(len(df), np.nan, np.float32)
        its = []
        for k in range(S.N_FOLDS):
            t = time.time()
            tr = (season <= s - 1) & (fold != k)
            model = S.fit_hgb(X[tr], y[tr])
            te = (fold == k) & (season >= V.WINDOW_FROM) & (season <= s)
            p[te] = model.predict_proba(X[te])[:, 1]
            its.append(int(model.n_iter_))
            log(f"  {label(s)} fold {k + 1}/{S.N_FOLDS}: trained on {int(tr.sum()):,} shots ({label(int(season.min()))} to "
                f"{label(s - 1)}), {model.n_iter_} iterations, priced {int(te.sum()):,} ({time.time() - t:.0f}s)")
        out[s] = p
        iters[s] = its
    return out, iters


def check_folds(conn, df, fold):
    xf = pd.read_sql_query("SELECT shot_id, fold FROM shot_xfg", conn).set_index("shot_id").fold
    m = df.season.to_numpy() >= 2021
    got = pd.Series(fold[m], index=df.id.to_numpy()[m])
    agree = float((xf.reindex(got.index).to_numpy() == got.to_numpy()).mean())
    assert agree == 1.0, f"the reproduced folds agree with shot_xfg's on {agree:.4%} of shots"
    log(f"player folds reproduce shot_xfg's on all {int(m.sum()):,} shots from 2020-21")


# ── 3. Players, debuts, free throws ──────────────────────────────────────────

def load_dates(conn):
    d = pd.read_sql_query("""SELECT s.id, g.game_date, g.espn_id FROM player_shots s
                             JOIN (SELECT DISTINCT game_id, game_date, espn_id FROM game_scores) g USING (game_id)
                             WHERE s.game_id LIKE '002%%' AND s.season >= '2020-21'""", conn)
    d["game_date"] = pd.to_datetime(d.game_date)
    return d.set_index("id")


def load_ft(conn):
    """(history, games): history per player-season before 2020-21 from player_season_stats; games per player-game
    2020-21 on from player_game_lines (Cup finals dropped), with the game's RAPM fold."""
    pss = pd.read_sql_query("""SELECT player_id, season, gp, fta, ft_pct FROM player_season_stats
                               WHERE season >= %s AND season < %s AND gp > 0 AND fta > 0""", conn, params=(V.WINDOW_FROM, V.PRICED[0]))
    pss["fta_n"] = np.round(pss.gp * pss.fta)
    pss["ftm_n"] = np.round(pss.fta_n * pss.ft_pct.fillna(0.0))
    hist = pss[pss.fta_n > 0][["player_id", "season", "fta_n", "ftm_n"]].rename(columns={"fta_n": "fta", "ftm_n": "ftm"})
    games = pd.read_sql_query("""SELECT l.player_id, l.season, l.game_id, l.game_date, l.fta, l.ftm FROM player_game_lines l
                                 WHERE EXISTS (SELECT 1 FROM game_scores g WHERE 'espn_' || g.espn_id = l.game_id)
                                 ORDER BY l.game_date, l.game_id, l.player_id""", conn)
    games["game_date"] = pd.to_datetime(games.game_date)
    games["fold"] = [R.game_fold(g) for g in games.game_id]
    return hist, games


def league_ft(conn):
    return {int(s): float(v) for s, v in pd.read_sql_query("SELECT season, ft_pct FROM league_season_averages", conn).itertuples(index=False)}


def debuts(conn, players, seen):
    """NBA debut per player: player_first_season, or the first season seen (chart, season stats, game lines) if
    earlier or missing."""
    fs = pd.read_sql_query("SELECT player_id, first_season FROM player_first_season", conn).set_index("player_id").first_season
    out = np.array([min(int(fs.get(p, 9999)), int(seen.get(p, 9999))) for p in players], np.int64)
    assert (out < 9999).all()
    return out


# ── 4-5. Hyperparameters and the priced seasons ──────────────────────────────

def fg_attempts(df, pidx, p, cls, c, seasons):
    m = (cls == c) & np.isin(df.season.to_numpy(), list(seasons)) & ~np.isnan(p)
    return V.Attempts(pidx[m], df.season.to_numpy()[m], V.logit(p[m]), df.made.to_numpy()[m], np.ones(int(m.sum())))


def ft_attempts(rows, pmap, lg, seasons):
    """FT rows (player_id, season, fta, ftm) -> Attempts (made and missed pseudo-rows, against the season's league FT%)."""
    r = rows[rows.season.isin(list(seasons))]
    pi = r.player_id.map(pmap).to_numpy()
    a = V.logit(np.array([lg[int(s)] for s in r.season]))
    return V.Attempts(np.r_[pi, pi], np.r_[r.season, r.season], np.r_[a, a], np.r_[np.ones(len(r)), np.zeros(len(r))],
                      np.r_[r.ftm.to_numpy(float), (r.fta - r.ftm).to_numpy(float)])


def fit_hyperparameters(df, pidx, p21, cls, ft_hist_rows, pmap, lg, debut):
    out = {}
    starts = {c: {"mu0": 0.0, "v0": 0.05, "phi": 0.8, "q": 0.01} for c in V.FG_CLASSES}
    starts["ft"] = {"mu0": 0.0, "v0": 0.3, "phi": 0.9, "q": 0.03}
    for ci, c in enumerate(V.CLASSES):
        t = time.time()
        att = fg_attempts(df, pidx, p21, cls, ci, V.FIT_SEASONS) if c != "ft" else ft_attempts(ft_hist_rows, pmap, lg, V.FIT_SEASONS)
        par, n2ll, rep = V.fit_class(debut, att, V.FIT_SEASONS, start=starts[c])
        rep.update({"attempts": float(att.w.sum()), "players": int(len(np.unique(att.player))), "seconds": round(time.time() - t)})
        out[c] = (par, n2ll, rep)
        stat = np.sqrt(par["q"] / (1 - par["phi"] ** 2))
        log(f"  {c:<5}: mu0 {par['mu0']:+.4f}, v0 {par['v0']:.4f} (sd {np.sqrt(par['v0']):.3f}), phi {par['phi']:.4f}, q {par['q']:.5f} "
            f"(stationary sd {stat:.3f}); -2LL {n2ll:,.1f}; starts {[round(s['n2ll'], 1) for s in rep['starts']]} ({rep['seconds']}s)")
    return out


def delta_vars(df, cls, lg):
    season = df.season.to_numpy()
    made = df.made.to_numpy()
    out = {}
    for ci, c in enumerate(V.FG_CLASSES):
        rates = {s: float(made[(season == s) & (cls == ci)].mean()) for s in V.FIT_SEASONS}
        out[c] = V.league_delta_var(rates)
    out["ft"] = V.league_delta_var({s: lg[s] for s in V.FIT_SEASONS})
    return out


def price_season(s, df, pidx, cls, p_s, dates, ft_hist_rows, ft_games, pmap, lg, debut, pars, dvar):
    """Everything for one priced season: per-shot prices, per-game FT prices, states and the preseason variant."""
    season = df.season.to_numpy()
    cur = season == s
    shots = pd.DataFrame({"row": np.flatnonzero(cur)})
    shots["id"] = df.id.to_numpy()[cur]
    shots["player_id"] = df.player_id.to_numpy()[cur]
    shots["p"] = pidx[cur]
    shots["cls"] = cls[cur]
    shots["made"] = df.made.to_numpy()[cur].astype(int)
    shots["is3"] = df.is3.to_numpy()[cur].astype(int)
    shots["p_blind"] = p_s[cur].astype(float)
    dd = dates.reindex(shots.id)
    assert dd.game_date.notna().all(), f"{label(s)}: chart shots without a game date"
    shots["date"] = dd.game_date.to_numpy()
    shots["fold"] = [R.game_fold(f"espn_{e}") for e in dd.espn_id]
    shots["p_lf"] = np.nan
    shots["p_sa"] = np.nan
    shots["p_pre"] = np.nan
    ft = ft_games[ft_games.season == s].copy()
    ft["p"] = ft.player_id.map(pmap).to_numpy()
    states, ft_price = [], {}
    for ci, c in enumerate(V.CLASSES):
        par = pars[c][0]
        if c != "ft":
            hist = fg_attempts(df, pidx, p_s, cls, ci, range(V.WINDOW_FROM, s))
            m0, v0 = V.season_start(par, debut, hist, s)
            k = shots.cls.to_numpy() == ci
            a = V.logit(shots.p_blind.to_numpy()[k])
            args = (shots.p.to_numpy()[k], shots.date.to_numpy()[k], a, shots.made.to_numpy()[k], np.ones(int(k.sum())), dvar[c])
            main = V.in_season(par, m0, v0, *args)
            pre = V.in_season(par, m0, v0, *args, update=False)
            shots.loc[k, "p_lf"] = V.expit(a + main["delta"])
            shots.loc[k, "p_sa"] = V.expit(a + main["delta"] + main["theta"])
            shots.loc[k, "p_pre"] = V.expit(a + pre["delta"] + pre["theta"])
            att = np.bincount(shots.p.to_numpy()[k], minlength=len(debut))
            mk = np.bincount(shots.p.to_numpy()[k], weights=shots.made.to_numpy()[k], minlength=len(debut))
        else:
            rows = pd.concat([ft_hist_rows, ft_games[ft_games.season < s].groupby(["player_id", "season"], as_index=False)[["fta", "ftm"]].sum()])
            hist = ft_attempts(rows, pmap, lg, range(V.WINDOW_FROM, s))
            m0, v0 = V.season_start(par, debut, hist, s)
            a0 = float(V.logit(lg[s - 1]))
            pi = ft.p.to_numpy()
            args = (np.r_[pi, pi], np.r_[ft.game_date.to_numpy(), ft.game_date.to_numpy()], np.full(2 * len(ft), a0),
                    np.r_[np.ones(len(ft)), np.zeros(len(ft))], np.r_[ft.ftm.to_numpy(float), (ft.fta - ft.ftm).to_numpy(float)], dvar[c])
            main = V.in_season(par, m0, v0, *args)
            pre = V.in_season(par, m0, v0, *args, update=False)
            n = len(ft)
            ft_price = {"p_lf": V.expit(a0 + main["delta"][:n]), "p_sa": V.expit(a0 + main["delta"][:n] + main["theta"][:n]),
                        "p_pre": V.expit(a0 + pre["delta"][:n] + pre["theta"][:n]), "a0": a0}
            att = np.bincount(pi, weights=ft.fta.to_numpy(float), minlength=len(debut))
            mk = np.bincount(pi, weights=ft.ftm.to_numpy(float), minlength=len(debut))
        states.append(pd.DataFrame({"p": np.arange(len(debut)), "cls": c, "pre_mean": m0, "pre_sd": np.sqrt(v0),
                                    "post_mean": main["end_m"], "post_sd": np.sqrt(main["end_v"]), "att": att, "made": mk,
                                    "league_after": float(main["league"].delta_after.iloc[-1])}))
    for k_ in ("p_lf", "p_sa", "p_pre"):
        ft[k_] = ft_price[k_]
    assert shots[["p_lf", "p_sa", "p_pre"]].notna().all().all()
    return shots, ft, pd.concat(states, ignore_index=True)


# ── Aggregates, validation ───────────────────────────────────────────────────

def ll_vec(p, y):
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return -(y * np.log(p) + (1 - y) * np.log1p(-p))


def boot_diff(game, d, rng):
    """95% interval and two-sided p of mean(d) over attempts, resampling games."""
    codes, g = np.unique(game, return_inverse=True)
    sums = np.bincount(g, weights=d)
    cnt = np.bincount(g).astype(float)
    G = len(codes)
    idx = rng.integers(0, G, size=(BOOT, G))
    draws = sums[idx].sum(axis=1) / cnt[idx].sum(axis=1)
    est = float(sums.sum() / cnt.sum())
    lo, hi = np.percentile(draws, [2.5, 97.5])
    p = float(min(1.0, 2 * min((draws <= 0).mean(), (draws >= 0).mean())))
    return est, float(lo), float(hi), p, G


def validation_rows(all_shots, all_ft, xfg, rng):
    rows = []
    groups = [("tune", (2021, 2022, 2023, 2024)), ("validate", (2025,)), ("test", (2026,)), ("all", V.PRICED)]
    groups = [(label(s), (s,)) for s in V.PRICED] + groups
    for name, seasons in groups:
        for c in V.CLASSES + ("fg",):
            if c == "ft":
                f = all_ft[all_ft.season.isin(seasons)]
                y = np.r_[np.ones(len(f)), np.zeros(len(f))]
                w = np.r_[f.ftm.to_numpy(float), (f.fta - f.ftm).to_numpy(float)]
                keep = w > 0
                y, w = y[keep], w[keep].astype(int)
                rep = lambda col: np.repeat(np.r_[f[col].to_numpy(), f[col].to_numpy()][keep], w)  # noqa: E731
                y = np.repeat(y, w)
                game = rep("game_id")
                prices = {k: rep(k) for k in ("p_lf", "p_pre", "p_sa")}
            else:
                sh = all_shots[all_shots.season.isin(seasons)]
                if c != "fg":
                    sh = sh[sh.cls == V.CLASSES.index(c)]
                y = sh.made.to_numpy(float)
                game = sh.game_key.to_numpy()
                prices = {k: sh[k].to_numpy() for k in ("p_blind", "p_lf", "p_pre", "p_sa")}
                prices["p_xfg"] = sh.id.map(xfg).to_numpy()
            n = len(y)
            base = ll_vec(prices["p_lf"], y)
            for k, p in prices.items():
                if np.isnan(p).any():
                    continue
                llv = ll_vec(p, y)
                r = {"scope": name, "seasons": V.label(seasons[0]) if len(seasons) == 1 else f"{label(seasons[0])} to {label(seasons[-1])}",
                     "cls": c, "price": k[2:], "n": n, "log_loss": float(llv.mean()), "brier": float(np.mean((p - y) ** 2)),
                     "mean_price": float(p.mean()), "make_rate": float(y.mean())}
                if k != "p_lf":
                    est, lo, hi, pv, G = boot_diff(game, llv - base, rng)
                    r.update({"d_log_loss_vs_lf": est, "ci_lo": lo, "ci_hi": hi, "p_boot": pv, "games": G})
                rows.append(r)
    return pd.DataFrame(rows)


def player_table(conn, all_shots, all_ft, states, players, lg):
    sh = all_shots.assign(value=np.where(all_shots.is3 == 1, 3, 2))
    sh["pts"] = sh.made * sh.value
    sh["xb"] = sh.p_lf * sh.value
    sh["xa"] = sh.p_sa * sh.value
    for ci, c in enumerate(V.FG_CLASSES):
        sh[f"att_{c}"] = (sh.cls == ci).astype(int)
        sh[f"made_{c}"] = ((sh.cls == ci) & (sh.made == 1)).astype(int)
    g = sh.groupby(["player_id", "season"]).agg(fga=("made", "size"), fgm=("made", "sum"), fg3a=("is3", "sum"), pts=("pts", "sum"),
                                                x_blind=("xb", "sum"), x_aware=("xa", "sum"),
                                                **{f"att_{c}": (f"att_{c}", "sum") for c in V.FG_CLASSES},
                                                **{f"made_{c}": (f"made_{c}", "sum") for c in V.FG_CLASSES})
    g["fg3m"] = sh[sh.is3 == 1].groupby(["player_id", "season"]).made.sum().reindex(g.index).fillna(0).astype(int)
    f = all_ft.assign(xb=all_ft.p_lf * all_ft.fta, xa=all_ft.p_sa * all_ft.fta)
    fg = f.groupby(["player_id", "season"]).agg(fta=("fta", "sum"), ftm=("ftm", "sum"), ft_x_blind=("xb", "sum"), ft_x_aware=("xa", "sum"))
    t = g.join(fg, how="outer").reset_index()
    for c in ("fga", "fgm", "fg3a", "fg3m", "pts", "fta", "ftm") + tuple(f"att_{c}" for c in V.FG_CLASSES) + tuple(f"made_{c}" for c in V.FG_CLASSES):
        t[c] = t[c].fillna(0).astype(int)
    for c in ("x_blind", "x_aware", "ft_x_blind", "ft_x_aware"):
        t[c] = t[c].fillna(0.0)
    t["skill_pts"] = t.x_aware - t.x_blind
    t["above_pts"] = t.pts - t.x_aware
    t["total_pts"] = t.pts - t.x_blind
    t["ft_skill_pts"] = t.ft_x_aware - t.ft_x_blind
    t["ft_above_pts"] = t.ftm - t.ft_x_aware
    t["ft_total_pts"] = t.ftm - t.ft_x_blind
    t["sva"] = t.skill_pts + t.ft_skill_pts
    t["beyond"] = t.above_pts + t.ft_above_pts
    # skills as log-odds (the API turns them into percentage points at the season's league rate)
    st = states
    for c in V.CLASSES:
        x = st[st.cls == c].set_index(["player_id", "season"])
        for when in ("pre", "post"):
            t[f"{when}_{c}"] = [x[f"{when}_mean"].get((p, s), np.nan) for p, s in zip(t.player_id, t.season)]
            t[f"{when}_{c}_sd"] = [x[f"{when}_sd"].get((p, s), np.nan) for p, s in zip(t.player_id, t.season)]
    t["qualified"] = t.fga >= MIN_FGA
    q = t[t.qualified]
    for col, name in (("sva", "rank_sva"), ("total_pts", "rank_total"), ("beyond", "rank_beyond")):
        t[name] = q.groupby("season")[col].rank(ascending=False, method="min").reindex(t.index)
    t["pool"] = q.groupby("season").fga.transform("size").reindex(t.index)
    names = pd.read_sql_query("""SELECT DISTINCT ON (player_id, season) player_id, season, player_name, team_abbreviation
                                 FROM player_season_stats WHERE season >= %s ORDER BY player_id, season""", conn, params=(V.PRICED[0],))
    t = t.merge(names, on=["player_id", "season"], how="left")
    shot_names = pd.read_sql_query("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_shots
                                      WHERE season >= '2020-21' ORDER BY player_id, id DESC""", conn).set_index("player_id").player_name
    t["player_name"] = t.player_name.fillna(t.player_id.map(shot_names))
    bio = pd.read_sql_query("SELECT player_id, player_name FROM player_bio", conn).set_index("player_id").player_name
    t["player_name"] = t.player_name.fillna(t.player_id.map(bio))
    return t


def yty_rows(t):
    out = []
    for lo, hi in zip(V.PRICED[:-1], V.PRICED[1:]):
        a = t[(t.season == lo) & (t.fga >= MIN_FGA)].set_index("player_id")
        b = t[(t.season == hi) & (t.fga >= MIN_FGA)].set_index("player_id")
        both = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
        for part in ("skill_pts", "above_pts", "total_pts"):
            x, y = both[f"{part}_a"] / both.fga_a, both[f"{part}_b"] / both.fga_b
            out.append({"scope": f"{label(lo)} -> {label(hi)}", "seasons": label(hi), "cls": "yty", "price": part, "n": len(both),
                        "corr": float(np.corrcoef(x, y)[0, 1])})
        a = t[(t.season == lo) & (t.fta >= MIN_FTA)].set_index("player_id")
        b = t[(t.season == hi) & (t.fta >= MIN_FTA)].set_index("player_id")
        both = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
        for part in ("ft_skill_pts", "ft_above_pts", "ft_total_pts"):
            x, y = both[f"{part}_a"] / both.fta_a, both[f"{part}_b"] / both.fta_b
            out.append({"scope": f"{label(lo)} -> {label(hi)}", "seasons": label(hi), "cls": "yty", "price": part, "n": len(both),
                        "corr": float(np.corrcoef(x, y)[0, 1])})
    return pd.DataFrame(out)


# ── Write ────────────────────────────────────────────────────────────────────

def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def write(conn, all_shots, states, table, fit_rows, val):
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS shot_value_shots, shot_value_states, shot_value_added, shot_value_fit, shot_value_validation")
    cur.execute("""CREATE TABLE shot_value_shots (
        shot_id bigint PRIMARY KEY,        -- player_shots.id (regular season, 2020-21 on)
        season smallint NOT NULL, game_date date NOT NULL, player_id integer NOT NULL,
        cls smallint NOT NULL,             -- 0 rim, 1 mid, 2 three (shot_value_lib.CLASSES)
        made boolean NOT NULL,
        fold smallint NOT NULL,            -- build_rapm.game_fold of the game's ESPN id (the protocol's held-out folds)
        p_blind real NOT NULL,             -- the look-ahead-free fold location model
        p_lf real NOT NULL,                -- + the league level before the date
        p_sa real NOT NULL)                -- + the shooter's skill before the date""")
    buf = io.StringIO()
    s = all_shots.sort_values("id")
    for r in zip(s.id.tolist(), s.season.tolist(), s.date.dt.strftime("%Y-%m-%d").tolist(), s.player_id.tolist(), s.cls.tolist(),
                 s.made.tolist(), s.fold.tolist(), s.p_blind.tolist(), s.p_lf.tolist(), s.p_sa.tolist()):
        buf.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{'t' if r[5] else 'f'},{r[6]},{r[7]!r},{r[8]!r},{r[9]!r}\n")
    buf.seek(0)
    cur.copy_expert("COPY shot_value_shots FROM STDIN WITH (FORMAT CSV)", buf)

    cur.execute("""CREATE TABLE shot_value_states (
        season smallint NOT NULL, player_id integer NOT NULL, cls text NOT NULL,
        pre_mean double precision NOT NULL, pre_sd double precision NOT NULL,     -- skill carried into the season (log-odds)
        post_mean double precision NOT NULL, post_sd double precision NOT NULL,   -- after the season
        att integer NOT NULL, made integer NOT NULL,
        PRIMARY KEY (season, player_id, cls))""")
    st = states.sort_values(["season", "player_id", "cls"])
    psycopg2.extras.execute_values(cur, "INSERT INTO shot_value_states VALUES %s",
                                   [(int(r.season), int(r.player_id), r.cls, float(r.pre_mean), float(r.pre_sd), float(r.post_mean),
                                     float(r.post_sd), int(r.att), int(r.made)) for r in st.itertuples(index=False)], page_size=5000)

    cols = ["player_id", "season", "player_name", "team_abbreviation", "fga", "fgm", "fg3a", "fg3m", "pts", "x_blind", "x_aware",
            "skill_pts", "above_pts", "total_pts", "fta", "ftm", "ft_x_blind", "ft_x_aware", "ft_skill_pts", "ft_above_pts", "ft_total_pts",
            "sva", "beyond"] + [f"{k}_{c}" for c in V.FG_CLASSES for k in ("att", "made")] + \
           [f"{w}_{c}{sd}" for c in V.CLASSES for w in ("pre", "post") for sd in ("", "_sd")] + \
           ["qualified", "rank_sva", "rank_total", "rank_beyond", "pool"]
    types = {"player_id": "integer NOT NULL", "season": "smallint NOT NULL", "player_name": "text", "team_abbreviation": "text",
             "qualified": "boolean NOT NULL"}
    ints = {"fga", "fgm", "fg3a", "fg3m", "pts", "fta", "ftm", "rank_sva", "rank_total", "rank_beyond", "pool"} | \
           {f"{k}_{c}" for c in V.FG_CLASSES for k in ("att", "made")}
    ddl = ", ".join(f"{c} {types.get(c, 'integer' if c in ints else 'real')}" for c in cols)
    cur.execute(f"CREATE TABLE shot_value_added ({ddl}, PRIMARY KEY (player_id, season))")
    tt = table.sort_values(["season", "player_id"])
    psycopg2.extras.execute_values(cur, f"INSERT INTO shot_value_added ({', '.join(cols)}) VALUES %s",
                                   [tuple(clean(r[c]) if c not in ints else (None if pd.isna(r[c]) else int(r[c])) for c in cols)
                                    for _, r in tt.iterrows()], page_size=2000)
    cur.execute("CREATE INDEX shot_value_added_season_idx ON shot_value_added (season, qualified)")

    cur.execute("""CREATE TABLE shot_value_fit (
        cls text PRIMARY KEY, mu0 double precision, v0 double precision, phi double precision, q double precision,
        n2ll double precision, delta_var double precision, estimated_on text, attempts double precision, players integer,
        detail jsonb)""")
    psycopg2.extras.execute_values(cur, "INSERT INTO shot_value_fit VALUES %s", fit_rows)

    vcols = ["scope", "seasons", "cls", "price", "n", "log_loss", "brier", "mean_price", "make_rate", "d_log_loss_vs_lf", "ci_lo", "ci_hi",
             "p_boot", "games", "corr"]
    cur.execute("""CREATE TABLE shot_value_validation (
        scope text NOT NULL, seasons text NOT NULL, cls text NOT NULL, price text NOT NULL, n integer NOT NULL,
        log_loss double precision, brier double precision, mean_price double precision, make_rate double precision,
        d_log_loss_vs_lf double precision, ci_lo double precision, ci_hi double precision, p_boot double precision, games integer,
        corr double precision,              -- cls 'yty': year-to-year correlation (per attempt) of the part named in price
        PRIMARY KEY (scope, cls, price))""")
    vv = val.reindex(columns=vcols)
    psycopg2.extras.execute_values(cur, f"INSERT INTO shot_value_validation ({', '.join(vcols)}) VALUES %s",
                                   [tuple(clean(v) for v in r) for r in vv.itertuples(index=False)])
    conn.commit()
    for t in ("shot_value_shots", "shot_value_states", "shot_value_added", "shot_value_fit", "shot_value_validation"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        log(f"  {t}: {n:,} rows, {size}")


def sniff(t):
    print("\nSniff tests (qualified player-seasons; skills in percentage points at the league's rate):")
    for name, s in (("Stephen Curry", 2026), ("Stephen Curry", 2021), ("Rudy Gobert", 2025), ("Nikola Jokić", 2025),
                    ("Kevin Durant", 2024), ("Shai Gilgeous-Alexander", 2025), ("Giannis Antetokounmpo", 2025), ("Ben Simmons", 2021)):
        r = t[(t.player_name == name) & (t.season == s)]
        if r.empty:
            print(f"  {name} {label(s)}: not found")
            continue
        r = r.iloc[0]
        print(f"  {name} {label(s)}: {r.fga} FGA, pts {r.pts}, blind {r.x_blind:.0f}, aware {r.x_aware:.0f} -> skill {r.skill_pts:+.0f}, "
              f"beyond {r.above_pts:+.0f}; FT {r.ftm}/{r.fta} skill {r.ft_skill_pts:+.0f}; SVA {r.sva:+.0f}; carried-in log-odds "
              f"rim {r.pre_rim:+.2f} mid {r.pre_mid:+.2f} three {r.pre_three:+.2f} ft {r.pre_ft:+.2f}")
    for s in (V.PRICED[0], V.PRICED[-1]):
        q = t[(t.season == s) & t.qualified]
        top = q.nlargest(8, "sva")
        bot = q.nsmallest(5, "sva")
        print(f"  {label(s)} top SVA: " + "; ".join(f"{r.player_name} {r.sva:+.0f}" for r in top.itertuples()))
        print(f"  {label(s)} bottom SVA: " + "; ".join(f"{r.player_name} {r.sva:+.0f}" for r in bot.itertuples()))


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", help="development only: keep the 30 fits' prices in this directory and reuse them")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('shot_xfg'), to_regclass('player_game_lines'), to_regclass('player_first_season'), "
                "to_regclass('league_season_averages'), to_regclass('game_scores')")
    assert all(cur.fetchone()), "needs shot_xfg, player_game_lines, player_first_season, league_season_averages and game_scores"

    log("loading shots")
    df = S.add_features(S.load_shots(conn))
    fold = player_folds(df)
    check_folds(conn, df, fold)
    cls = V.shot_class(df.zone.to_numpy(), df.is3.to_numpy())
    dates = load_dates(conn)
    ft_hist_rows, ft_games = load_ft(conn)
    lg = league_ft(conn)
    for s in range(V.WINDOW_FROM, V.PRICED[-1] + 1):
        assert s in lg, f"league_season_averages has no FT% for {label(s)}"

    seen = pd.concat([df[df.season >= V.WINDOW_FROM].groupby("player_id").season.min(), ft_hist_rows.groupby("player_id").season.min(),
                      ft_games.groupby("player_id").season.min()]).groupby(level=0).min()
    players = sorted(set(df.player_id[df.season >= V.WINDOW_FROM]) | set(ft_hist_rows.player_id) | set(ft_games.player_id))
    pmap = {p: i for i, p in enumerate(players)}
    debut = debuts(conn, players, seen)
    pidx = np.full(len(df), -1, np.int64)
    w = df.season.to_numpy() >= V.WINDOW_FROM
    pidx[w] = df.player_id[w].map(pmap).to_numpy()
    log(f"{len(players):,} players with an attempt from {label(V.WINDOW_FROM)}; {len(ft_games):,} player-games of free throws from {label(V.PRICED[0])}")

    cache = Path(args.cache) / "shot_value_models.npz" if args.cache else None
    if cache is not None and cache.exists():
        z = np.load(cache, allow_pickle=True)
        assert (z["ids"] == df.id.to_numpy()).all(), "the cached fits are for a different set of shots"
        p_by_season = {s: z[f"p{s}"] for s in V.PRICED}
        iters = z["iters"].item()
        log(f"location models: read from the development cache {cache} (a final build runs without --cache)")
    else:
        log("location models, look-ahead free (30 fits)")
        p_by_season, iters = location_models(df, fold)
        if cache is not None:
            np.savez(cache, ids=df.id.to_numpy(), iters=np.array(iters, dtype=object), **{f"p{s}": p for s, p in p_by_season.items()})

    log(f"hyperparameters, empirical Bayes on {label(V.FIT_SEASONS[0])} to {label(V.FIT_SEASONS[-1])} (the {label(V.PRICED[0])} models)")
    pars = fit_hyperparameters(df, pidx, p_by_season[V.PRICED[0]], cls, ft_hist_rows, pmap, lg, debut)
    dvar = delta_vars(df, cls, lg)
    log("league-level shrinkage (sd of the year-to-year change in log-odds): " + ", ".join(f"{c} {np.sqrt(v):.3f}" for c, v in dvar.items()))

    all_shots, all_ft, all_states = [], [], []
    for s in V.PRICED:
        t = time.time()
        shots, ft, states = price_season(s, df, pidx, cls, p_by_season[s], dates, ft_hist_rows, ft_games, pmap, lg, debut, pars, dvar)
        shots["season"] = s
        states["season"] = s
        all_shots.append(shots)
        all_ft.append(ft)
        active = set(shots.p) | set(ft.p)
        all_states.append(states[states.p.isin(active)])
        y = shots.made.to_numpy(float)
        msg = ", ".join(f"{k[2:]} {ll_vec(shots[k].to_numpy(), y).mean():.5f}" for k in ("p_blind", "p_lf", "p_pre", "p_sa"))
        log(f"  {label(s)}: {len(shots):,} shots, log loss {msg}; FT {int(ft.fta.sum()):,} attempts ({time.time() - t:.0f}s)")
    all_shots = pd.concat(all_shots, ignore_index=True)
    all_ft = pd.concat(all_ft, ignore_index=True)
    states = pd.concat(all_states, ignore_index=True)
    states["player_id"] = np.asarray(players)[states.p.to_numpy()]

    log("validation")
    xfg = pd.read_sql_query("SELECT shot_id, p_make FROM shot_xfg", conn).set_index("shot_id").p_make
    # bootstrap clusters: the ESPN game of each shot (a date and fold pin it down only approximately), so key by NBA game
    gid = pd.read_sql_query("SELECT id, game_id FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21'", conn).set_index("id").game_id
    all_shots["game_key"] = all_shots.id.map(gid).to_numpy()
    rng = np.random.default_rng(SEED)
    val = validation_rows(all_shots, all_ft, xfg, rng)
    table = player_table(conn, all_shots, all_ft, states, players, lg)
    val = pd.concat([val, yty_rows(table)], ignore_index=True)
    print(val[val.cls != "yty"].pivot_table(index=["scope", "cls"], columns="price", values="log_loss").round(5).to_string())
    print(val[val.cls == "yty"][["scope", "price", "n", "corr"]].to_string(index=False))

    fit_rows = []
    for c in V.CLASSES:
        par, n2ll, rep = pars[c]
        fit_rows.append((c, par["mu0"], par["v0"], par["phi"], par["q"], n2ll, dvar[c],
                         f"{label(V.FIT_SEASONS[0])} to {label(V.FIT_SEASONS[-1])}", rep["attempts"], rep["players"],
                         psycopg2.extras.Json({k: v for k, v in rep.items() if k not in ("attempts", "players")})))
    fit_rows.append(("models", None, None, None, None, None, None, f"{label(V.PRICED[0])} to {label(V.PRICED[-1])}", None, None,
                     psycopg2.extras.Json({"iterations": {label(s): iters[s] for s in V.PRICED}, "params": S.HGB_PARAMS,
                                           "features": S.HGB_FEATURES, "trained_from": label(int(df.season.min())),
                                           "window_from": label(V.WINDOW_FROM), "folds": S.N_FOLDS, "seed": S.SEED})))
    write(conn, all_shots, states, table, fit_rows, val)
    sniff(table)
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
