"""
paper_xrapm.py
===============
Expected-points RAPM (round 5, step 4): regularised adjusted plus-minus whose
target is not the points a lineup scored but what its shots were worth in
expectation, so that whether the shots went in (shooting luck, as far as a
location model can tell) is taken out of the target. Everything else about
the regression is scripts/build_rapm.py's: the same stints, sides,
possessions, weights, folds, design matrix and solver.

Per stint and side (the five-man stints of lineup_stints):

  expected points = sum over field-goal attempts of P(make) x shot value
                  + sum over free-throw attempts of the shooter's expected FT%
                  + the points the stint was credited that no recorded attempt
                    accounts for (the residual, below)

  P(make)   the platform's cross-fitted expected-FG model (shot_xfg, written by
            scripts/build_shot_making.py: gradient boosting on court location,
            distance, angle, zone, two/three, period, clock and season, five-fold
            cross-fitted by player over 1996-97 to 2025-26, so no shot is scored
            by a model that saw its shooter). ESPN's attempts are matched to the
            NBA shot chart with pbp_lineups.match_coordinates() (order within
            game, shooter and period, identical make/miss sequences; about 99% of
            attempts). A matched attempt takes the chart's value (2 or 3) and its
            P(make). An unmatched attempt takes the parser's value and, as its
            P(make), the shooter's mean P(make) over his matched attempts of that
            value in that season, or the league's mean where he has none. Both
            kinds are counted per season (paper_xrapm_meta).
  FT%       leave-one-out and shrunk: for an attempt by a shooter with fta
            attempts and ftm makes that season, (ftm - made + M x league FT%) /
            (fta - 1 + M), where M is the number of attempts at which the Stat
            Stability page's split-half reliability of FT% reaches one half
            (stat_stability, about 27 attempts). An attempt with no shooter id
            takes the league rate. The attempt's own outcome never enters its
            own expectation.
  residual  lineup_stints credits a stint's points from its made shots and free
            throws or, in the ~2% of games where those don't add up to the real
            final score, from the running maximum of ESPN's score fields. Where a
            stint's stored points differ from the points its recorded attempts
            account for (with the chart's shot value), the difference is added as
            it is: points with no attempt in the play-by-play can't be priced.
            Counted per season.

Fits, the platform's way (build_rapm.Design, cross_validate and solve; nothing is
re-implemented): version 'single' shrinks toward zero with lambda at the minimum
of the 5-fold game-grouped cross-validation on the expected-points target;
version 'prior' shrinks toward k x Basketball-Reference OBPM/DBPM with the single
version's lambda by rule and k from the same cross-validation. No bootstrap: the
intervals the paper reports come from scripts/paper_tests.py on the protocol's
stored per-game predictions. The protocol fits themselves (lambda chosen by
next-season actual-margin error on the tune pairs, then next-season, held-out
and year-to-year tests against actual-points RAPM, BPM and on/off) are in
scripts/paper_eval.py, which reads paper_xrapm_stints.

Judgment calls (also stored in paper_xrapm_meta):
  * P(make) comes from the cross-fit over all six seasons, so a season's expected
    points use other players' shots from every season, later ones included. The
    model has no player identity and the regression has a season intercept; the
    model was not refitted season by season for this purpose.
  * Free throws and field goals are priced independently of each other and of
    what happened next (a missed second free throw can be rebounded; the rebound
    is a possession component and stays as it was).
  * Regular-season games with ESPN play-by-play only (2020-21 on); the three NBA
    Cup finals have no game_scores row and no tracked stint.

Tables written (dropped and rebuilt):
  paper_xrapm_stints     one row per stint of lineup_stints: per side the attempts,
                         matched attempts, fallbacks, expected FG and FT points,
                         event points, stored points, residual and expected points;
  paper_xrapm_players    one row per version, season and player: ORAPM/DRAPM/RAPM on
                         expected points, possessions, qualified, and player_rapm's
                         actual-points ratings for the same version and season;
  paper_xrapm_fits       per version and season: lambda, prior scale, cross-validated
                         error against the zero model, home edge, sizes, and the
                         correlation and spreads against player_rapm;
  paper_xrapm_lambda_cv  the cross-validation curve;
  paper_xrapm_meta       counts and constants (matched share, fallbacks, FT shrinkage,
                         residual points, target variance), per season (0 = all).

Runtime about 4 minutes (the play-by-play parse is most of it); deterministic.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_xrapm.py
Then: paper_eval.py --only impact, paper_tests.py --only impact, paper_numbers.py.
Rerun after build_lineup_stints.py, build_shot_making.py (shot_xfg) or build_rapm.py.
"""

import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

import build_rapm as R
from db_config import DB_CONFIG
from pbp_lineups import Game, load_espn, load_season_names, match_coordinates

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

VERSIONS = ("single", "prior")
FT_SHRINK_FALLBACK = 27.0      # pseudo-attempts if stat_stability has no ft_pct row (it has: read live)
T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


# ── 1. Every attempt the lineup parser counts ────────────────────────────────

def collect_events(conn, cur):
    """Field-goal and free-throw attempts with the side they belong to: game,
    season, action_number, kind, side, shooter, period, made, the parser's value
    (1 for a free throw)."""
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn)
    rows = []
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        game.home = g.home_team          # parse() needs the home side for score steps (walk() sets it the same way)
        sides = {g.home_team: "home", g.away_team: "away"}
        for e in game.parse():
            # the same two filters Game.stints() applies: a counted kind, and a team that is one of the two
            if e["kind"] in ("fg", "ft") and e["team"] in sides:
                rows.append((g.game_id, int(g.season), e["action_number"], e["kind"], sides[e["team"]], e["pid"],
                             e["period"], bool(e["made"]), int(e["val"]) if e["kind"] == "fg" else 1))
        if i % 1500 == 0:
            log(f"  {i} of {len(games)} games parsed, {len(rows):,} attempts")
    ev = pd.DataFrame(rows, columns=["game_id", "season", "action_number", "kind", "side", "pid", "period", "made", "val"])
    ev["pid"] = ev.pid.astype("Int64")
    return ev


def load_stints(conn):
    return pd.read_sql_query(
        """SELECT stint_id, game_id, season, home_team, away_team, action_from, action_to, tracked_ok,
                  home_pts, away_pts, home_fga, away_fga, home_fta, away_fta, home_poss, away_poss
           FROM lineup_stints ORDER BY stint_id""", conn)


def map_to_stints(ev, st):
    """The stint of each attempt: action_number inside the stint's inclusive
    [action_from, action_to] within its game (action_number is unique per game)."""
    right = st[st.action_from.notna()][["game_id", "action_from", "action_to", "stint_id"]].copy()
    right["action_from"] = right.action_from.astype(int)
    right["action_to"] = right.action_to.astype(int)
    left = ev.sort_values("action_number").copy()
    left["action_number"] = left.action_number.astype(int)
    right = right.sort_values("action_from")
    m = pd.merge_asof(left, right, left_on="action_number", right_on="action_from", by="game_id", direction="backward")
    inside = m.stint_id.notna() & (m.action_number <= m.action_to)
    unmapped = int((~inside).sum())
    m = m[inside].copy()
    m["stint_id"] = m.stint_id.astype(int)
    return m.drop(columns=["action_from", "action_to"]), unmapped


# ── 2. Pricing the attempts ──────────────────────────────────────────────────

def price_field_goals(conn, fg):
    """P(make) and value per field-goal attempt: the chart's where matched, the
    stated fallback otherwise. Adds p_make, value, source ('chart', 'shooter',
    'league')."""
    fg = match_coordinates(conn, fg)
    xfg = pd.read_sql_query("SELECT shot_id, p_make FROM shot_xfg", conn)
    fg = fg.merge(xfg, left_on="nba_shot_id", right_on="shot_id", how="left").drop(columns=["shot_id"])
    chart = fg.p_make.notna()
    # the chart's two-or-three where matched (the text misses ~8,700 missed threes), the parser's call otherwise
    fg["value"] = np.where(fg.shot_type.notna(), np.where(fg.shot_type.fillna("").str.startswith("3"), 3, 2), fg.val).astype(int)
    key = ["season", "pid", "value"]
    shooter_mean = fg[chart].groupby(key, dropna=True).p_make.mean().rename("p_shooter")
    league_mean = fg[chart].groupby(["season", "value"]).p_make.mean().rename("p_league")
    fg = fg.merge(shooter_mean, left_on=key, right_index=True, how="left")
    fg = fg.merge(league_mean, left_on=["season", "value"], right_index=True, how="left")
    src = np.select([chart, fg.p_shooter.notna()], ["chart", "shooter"], "league")
    fg["source"] = src
    fg["p_use"] = np.where(chart, fg.p_make, np.where(fg.p_shooter.notna(), fg.p_shooter, fg.p_league))
    assert fg.p_use.notna().all()
    matched_no_p = int((fg.nba_shot_id.notna() & ~chart).sum())
    return fg, matched_no_p


def ft_shrinkage(cur):
    cur.execute("SELECT stable_n FROM stat_stability WHERE stat = 'ft_pct' AND variant = 'catalogue'")
    r = cur.fetchone()
    return (float(r[0]), "stat_stability ft_pct catalogue stable_n (split-half reliability 0.5)") if r else \
        (FT_SHRINK_FALLBACK, "fallback constant (no stat_stability ft_pct row)")


def price_free_throws(ft, M):
    """Leave-one-out shrunk FT% per attempt; the league rate for attempts with no shooter."""
    league = ft.groupby("season").made.mean().rename("ft_league")
    ft = ft.merge(league, left_on="season", right_index=True, how="left")
    tot = ft[ft.pid.notna()].groupby(["season", "pid"]).made.agg(ftm="sum", fta="size")
    ft = ft.merge(tot, left_on=["season", "pid"], right_index=True, how="left")
    made = ft.made.astype(float)
    own = (ft.ftm.astype(float) - made + M * ft.ft_league) / (ft.fta.astype(float) - 1 + M)
    ft["p_use"] = np.where(ft.pid.notna(), own, ft.ft_league)
    ft["value"] = 1
    ft["source"] = np.where(ft.pid.notna(), "shooter_loo", "league")
    assert ft.p_use.notna().all() and (ft.p_use.between(0, 1)).all()
    return ft


# ── 3. Per stint and side ────────────────────────────────────────────────────

def stint_table(fg, ft, st):
    ev = pd.concat([fg[["stint_id", "side", "kind", "p_use", "value", "made", "source"]],
                    ft[["stint_id", "side", "kind", "p_use", "value", "made", "source"]]], ignore_index=True)
    ev["x"] = ev.p_use * ev.value
    ev["actual"] = ev.made.astype(int) * ev.value
    is_fg = ev.kind == "fg"
    g = ev.assign(fga=is_fg.astype(int), fga_chart=(is_fg & (ev.source == "chart")).astype(int),
                  fga_fallback=(is_fg & (ev.source != "chart")).astype(int), fta=(~is_fg).astype(int),
                  xfg_pts=np.where(is_fg, ev.x, 0.0), xft_pts=np.where(is_fg, 0.0, ev.x)) \
        .groupby(["stint_id", "side"])[["fga", "fga_chart", "fga_fallback", "fta", "xfg_pts", "xft_pts", "actual"]].sum()
    out = st[["stint_id", "game_id", "season", "tracked_ok", "home_pts", "away_pts", "home_fga", "away_fga", "home_fta", "away_fta",
              "home_poss", "away_poss"]].copy()
    for side in ("home", "away"):
        w = g.xs(side, level="side").reindex(out.stint_id).fillna(0.0)
        out[f"{side}_ev_fga"] = w.fga.to_numpy().astype(int)
        out[f"{side}_fga_chart"] = w.fga_chart.to_numpy().astype(int)
        out[f"{side}_fga_fallback"] = w.fga_fallback.to_numpy().astype(int)
        out[f"{side}_ev_fta"] = w.fta.to_numpy().astype(int)
        out[f"{side}_xfg_pts"] = w.xfg_pts.to_numpy()
        out[f"{side}_xft_pts"] = w.xft_pts.to_numpy()
        out[f"{side}_pts_events"] = w.actual.to_numpy().astype(int)
        out[f"{side}_residual"] = out[f"{side}_pts"].astype(int) - out[f"{side}_pts_events"]
        out[f"{side}_xpts"] = out[f"{side}_xfg_pts"] + out[f"{side}_xft_pts"] + out[f"{side}_residual"]
    return out


def checks(out, fg, unmapped, matched_no_p, M, M_note):
    """Reconciliation and the counts the paper quotes; returns meta rows (key, season, value, note)."""
    meta = []
    tracked = out[out.tracked_ok]
    bad_fga = int(((tracked.home_ev_fga != tracked.home_fga) | (tracked.away_ev_fga != tracked.away_fga)).sum())
    bad_fta = int(((tracked.home_ev_fta != tracked.home_fta) | (tracked.away_ev_fta != tracked.away_fta)).sum())
    log(f"event -> stint: {unmapped} attempts outside every stint's range; tracked stints whose attempts differ from the "
        f"stored FGA: {bad_fga}, FTA: {bad_fta}; matched to the chart but no shot_xfg row: {matched_no_p}")
    meta += [("attempts_unmapped", 0, unmapped, "attempts (all games) whose action_number falls in no stint's range"),
             ("tracked_stints_fga_mismatch", 0, bad_fga, "tracked stints whose parsed FGA differ from lineup_stints"),
             ("tracked_stints_fta_mismatch", 0, bad_fta, "tracked stints whose parsed FTA differ from lineup_stints"),
             ("fga_matched_without_xfg", 0, matched_no_p, "attempts matched to a chart row that shot_xfg does not hold (impossible-location rows dropped by the model)"),
             ("ft_shrink_attempts", 0, M, "M: pseudo-attempts toward the league FT% in the leave-one-out expected FT%; " + M_note)]
    rows = []
    for season, x in tracked.groupby("season"):
        fga = int(x.home_ev_fga.sum() + x.away_ev_fga.sum())
        chart = int(x.home_fga_chart.sum() + x.away_fga_chart.sum())
        fb = int(x.home_fga_fallback.sum() + x.away_fga_fallback.sum())
        pts = int(x.home_pts.sum() + x.away_pts.sum())
        xpts = float(x.home_xpts.sum() + x.away_xpts.sum())
        xfg = float(x.home_xfg_pts.sum() + x.away_xfg_pts.sum())
        xft = float(x.home_xft_pts.sum() + x.away_xft_pts.sum())
        res = int(x.home_residual.sum() + x.away_residual.sum())
        res_abs = int(x.home_residual.abs().sum() + x.away_residual.abs().sum())
        res_stints = int(((x.home_residual != 0) | (x.away_residual != 0)).sum())
        w = np.concatenate([x.home_poss.to_numpy(float), x.away_poss.to_numpy(float)])
        ya = np.concatenate([x.home_pts.to_numpy(float), x.away_pts.to_numpy(float)])
        yx = np.concatenate([x.home_xpts.to_numpy(float), x.away_xpts.to_numpy(float)])
        keep = w > 0
        w, ya, yx = w[keep], 100 * ya[keep] / w[keep], 100 * yx[keep] / w[keep]

        def wvar(v):
            mu = np.average(v, weights=w)
            return float(np.average((v - mu) ** 2, weights=w))

        var_a, var_x, var_d = wvar(ya), wvar(yx), wvar(ya - yx)
        rows.append({"season": int(season), "stints": int(len(x)), "fga": fga, "fga_chart": chart, "fga_fallback": fb,
                     "matched_share": chart / fga, "pts": pts, "xpts": xpts, "xfg_pts": xfg, "xft_pts": xft,
                     "residual": res, "residual_abs": res_abs, "residual_stints": res_stints,
                     "sd_pts100": np.sqrt(var_a), "sd_xpts100": np.sqrt(var_x), "sd_diff100": np.sqrt(var_d)})
        for k, v, note in (("fga", fga, "tracked field-goal attempts"), ("fga_chart", chart, "priced by the chart's P(make)"),
                           ("fga_fallback", fb, "unmatched: the shooter's (or league's) mean P(make) for that value"),
                           ("matched_share", chart / fga, "share of tracked attempts priced by the chart"),
                           ("pts", pts, "stored points of tracked stints"), ("xpts", xpts, "expected points of tracked stints"),
                           ("xpts_over_pts", xpts / pts, "expected over stored points"),
                           ("residual_pts", res, "stored minus attempt-accounted points, summed"),
                           ("residual_abs_pts", res_abs, "the same, absolute values summed"),
                           ("residual_stints", res_stints, "tracked stints with a nonzero residual on either side"),
                           ("sd_pts100", np.sqrt(var_a), "possession-weighted SD of a side's points per 100 across tracked stints"),
                           ("sd_xpts100", np.sqrt(var_x), "the same for expected points per 100"),
                           ("sd_diff100", np.sqrt(var_d), "the same for actual minus expected per 100 (shooting variance)")):
            meta.append((k, int(season), float(v), note))
    per = pd.DataFrame(rows)
    tot_fga, tot_chart = int(per.fga.sum()), int(per.fga_chart.sum())
    meta += [("fga", 0, tot_fga, "tracked field-goal attempts, all seasons"),
             ("fga_chart", 0, tot_chart, "priced by the chart's P(make), all seasons"),
             ("fga_fallback", 0, int(per.fga_fallback.sum()), "unmatched attempts, all seasons"),
             ("matched_share", 0, tot_chart / tot_fga, "share of tracked attempts priced by the chart, all seasons"),
             ("pts", 0, int(per.pts.sum()), "stored points, all seasons"), ("xpts", 0, float(per.xpts.sum()), "expected points, all seasons"),
             ("xpts_over_pts", 0, float(per.xpts.sum() / per.pts.sum()), "expected over stored points, all seasons"),
             ("residual_pts", 0, int(per.residual.sum()), "residual points, all seasons"),
             ("residual_abs_pts", 0, int(per.residual_abs.sum()), "absolute residual points, all seasons"),
             ("residual_stints", 0, int(per.residual_stints.sum()), "tracked stints with a residual, all seasons"),
             ("fallback_shooter", 0, int((fg.source == "shooter").sum()), "unmatched attempts priced at the shooter's mean (all games)"),
             ("fallback_league", 0, int((fg.source == "league").sum()), "unmatched attempts priced at the league mean (all games)")]
    print(per.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    return meta, per


# ── 4. Fits, the platform's way ──────────────────────────────────────────────

def fit_all(conn, out):
    rows, n_stints, dropped = R.load_rows(conn)
    x = out.set_index("stint_id")
    xpts = np.where(rows.home.to_numpy() == 1, x.home_xpts.reindex(rows.stint_id).to_numpy(),
                    x.away_xpts.reindex(rows.stint_id).to_numpy())
    assert not np.isnan(xpts).any()
    rows["pts_actual"] = rows.pts
    rows["pts"] = xpts
    bpm = R.load_bpm(conn)
    app = pd.read_sql_query("SELECT version, season, player_id, orapm, drapm, rapm, qualified FROM player_rapm WHERE version IN ('single', 'prior')", conn)
    app_idx = app.set_index(["version", "season", "player_id"])
    seasons = sorted(int(s) for s in rows.season.unique())
    log(f"fits: {n_stints} tracked stints -> {len(rows)} side-rows ({dropped} sides with no possession dropped), seasons {seasons[0]}-{seasons[-1]}")
    player_rows, fit_rows, curve_rows = [], [], []
    for season in seasons:
        design = R.Design(rows[rows.season == season])
        lam_single = None
        for version in VERSIONS:
            prior, prior_od = None, {}
            if version == "prior":
                prior_od = {p: (bpm[(p, season)][0], bpm[(p, season)][1]) for p in design.players if (p, season) in bpm}
                prior = design.prior_vector(prior_od)
            t = time.time()
            curve, best, (G, b) = R.cross_validate(design, prior, fixed_lambda=None if version == "single" else lam_single)
            lam, scale = best["lambda"], best["prior_scale"]
            if version == "single":
                lam_single = lam
            beta = design.solve(G, b, lam, None if prior is None else prior * scale)
            sizes = R.player_sizes(design)
            P = design.P
            recs = []
            for p, i in design.pidx.items():
                sz = sizes[p]
                poss = (sz["poss_off"] + sz["poss_def"]) / 2
                a = app_idx.loc[(version, season, p)] if (version, season, p) in app_idx.index else None
                recs.append({"version": version, "season": season, "player_id": int(p), "games": sz["games"], "minutes": sz["minutes"],
                             "poss": round(poss, 1), "xorapm": float(beta[i]), "xdrapm": float(beta[P + i]), "xrapm": float(beta[i] + beta[P + i]),
                             "orapm": None if a is None else float(a.orapm), "drapm": None if a is None else float(a.drapm),
                             "rapm": None if a is None else float(a.rapm), "qualified": poss >= R.QUALIFIED_POSS})
            df = pd.DataFrame(recs)
            q = df[df.qualified & df.rapm.notna()]
            r = float(np.corrcoef(q.xrapm, q.rapm)[0, 1])
            home = float(beta[2 * P + design.S])
            fit_rows.append({"version": version, "season": season, "games": int(len(design.games)), "rows": design.n, "players": design.P,
                             "poss": round(float(design.w.sum()), 1), "lambda": lam, "prior_scale": scale, "lambda_rule": best["lambda_rule"],
                             "cv_folds": R.FOLDS, "cv_rmse": best["cv_rmse"], "cv_rmse_zero": best["cv_rmse_zero"],
                             "cv_best_lambda": best["cv_best_lambda"], "cv_best_scale": best["cv_best_scale"],
                             "intercept": float(beta[2 * P + design.sidx[season]]), "home_coef": home, "home_edge_per_100": 2 * home,
                             "qualified": int(df.qualified.sum()), "players_with_prior": len(prior_od) if version == "prior" else None,
                             "r_with_rapm": r, "sd_xrapm": float(q.xrapm.std(ddof=1)), "sd_rapm": float(q.rapm.std(ddof=1)),
                             "mean_abs_diff": float((q.xrapm - q.rapm).abs().mean())})
            for c in curve:
                curve_rows.append({"version": version, "season": season, **c})
            player_rows.extend(recs)
            log(f"  {version} {label(season)}: lambda {lam}{'' if scale is None else f', prior scale {scale}'}, cv {best['cv_rmse']:.4f} vs zero "
                f"{best['cv_rmse_zero']:.4f}, home edge {2 * home:+.2f}/100, r with actual-points RAPM {r:.3f} "
                f"(sd {q.xrapm.std(ddof=1):.2f} vs {q.rapm.std(ddof=1):.2f}, {len(q)} qualified) ({time.time() - t:.0f}s)")
    return pd.DataFrame(player_rows), pd.DataFrame(fit_rows), pd.DataFrame(curve_rows)


# ── 5. Write ─────────────────────────────────────────────────────────────────

def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def write(conn, out, players, fits, curve, meta):
    cur = conn.cursor()
    for t in ("paper_xrapm_meta", "paper_xrapm_lambda_cv", "paper_xrapm_fits", "paper_xrapm_players", "paper_xrapm_stints"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    side_cols = []
    for side in ("home", "away"):
        side_cols += [f"{side}_pts SMALLINT NOT NULL", f"{side}_ev_fga SMALLINT NOT NULL", f"{side}_fga_chart SMALLINT NOT NULL",
                      f"{side}_fga_fallback SMALLINT NOT NULL", f"{side}_ev_fta SMALLINT NOT NULL", f"{side}_xfg_pts REAL NOT NULL",
                      f"{side}_xft_pts REAL NOT NULL", f"{side}_pts_events SMALLINT NOT NULL", f"{side}_residual SMALLINT NOT NULL",
                      f"{side}_xpts REAL NOT NULL"]
    cur.execute(f"""CREATE TABLE paper_xrapm_stints (
        stint_id INTEGER PRIMARY KEY, game_id TEXT NOT NULL, season INTEGER NOT NULL, tracked_ok BOOLEAN NOT NULL,
        {', '.join(side_cols)})""")
    cols = ["stint_id", "game_id", "season", "tracked_ok"] + [f"{s}_{c}" for s in ("home", "away") for c in
                                                             ("pts", "ev_fga", "fga_chart", "fga_fallback", "ev_fta", "xfg_pts", "xft_pts", "pts_events", "residual", "xpts")]
    psycopg2.extras.execute_values(cur, f"INSERT INTO paper_xrapm_stints ({', '.join(cols)}) VALUES %s",
                                   [tuple(clean(v) for v in r) for r in out[cols].itertuples(index=False)], page_size=5000)
    cur.execute("CREATE INDEX ON paper_xrapm_stints (season)")
    cur.execute("""CREATE TABLE paper_xrapm_players (
        version TEXT NOT NULL, season INTEGER NOT NULL, player_id BIGINT NOT NULL, games INTEGER, minutes REAL, poss REAL,
        xorapm REAL NOT NULL, xdrapm REAL NOT NULL, xrapm REAL NOT NULL, orapm REAL, drapm REAL, rapm REAL, qualified BOOLEAN NOT NULL,
        PRIMARY KEY (version, season, player_id))""")
    pc = ["version", "season", "player_id", "games", "minutes", "poss", "xorapm", "xdrapm", "xrapm", "orapm", "drapm", "rapm", "qualified"]
    psycopg2.extras.execute_values(cur, f"INSERT INTO paper_xrapm_players ({', '.join(pc)}) VALUES %s",
                                   [tuple(clean(v) for v in r) for r in players[pc].itertuples(index=False)], page_size=2000)
    cur.execute("""CREATE TABLE paper_xrapm_fits (
        version TEXT NOT NULL, season INTEGER NOT NULL, games INTEGER, rows INTEGER, players INTEGER, poss REAL,
        lambda REAL NOT NULL, prior_scale REAL, lambda_rule TEXT, cv_folds INTEGER, cv_rmse REAL, cv_rmse_zero REAL,
        cv_best_lambda REAL, cv_best_scale REAL, intercept REAL, home_coef REAL, home_edge_per_100 REAL, qualified INTEGER,
        players_with_prior INTEGER, r_with_rapm REAL, sd_xrapm REAL, sd_rapm REAL, mean_abs_diff REAL,
        PRIMARY KEY (version, season))""")
    fc = ["version", "season", "games", "rows", "players", "poss", "lambda", "prior_scale", "lambda_rule", "cv_folds", "cv_rmse", "cv_rmse_zero",
          "cv_best_lambda", "cv_best_scale", "intercept", "home_coef", "home_edge_per_100", "qualified", "players_with_prior", "r_with_rapm",
          "sd_xrapm", "sd_rapm", "mean_abs_diff"]
    psycopg2.extras.execute_values(cur, f"INSERT INTO paper_xrapm_fits ({', '.join(fc)}) VALUES %s",
                                   [tuple(clean(v) for v in r) for r in fits[fc].itertuples(index=False)])
    cur.execute("CREATE TABLE paper_xrapm_lambda_cv (version TEXT NOT NULL, season INTEGER NOT NULL, lambda REAL NOT NULL, prior_scale REAL, cv_rmse REAL NOT NULL)")
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_xrapm_lambda_cv (version, season, lambda, prior_scale, cv_rmse) VALUES %s",
                                   [tuple(clean(v) for v in r) for r in curve[["version", "season", "lambda", "prior_scale", "cv_rmse"]].itertuples(index=False)])
    cur.execute("CREATE TABLE paper_xrapm_meta (key TEXT NOT NULL, season INTEGER NOT NULL, value DOUBLE PRECISION, note TEXT, PRIMARY KEY (key, season))")
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_xrapm_meta (key, season, value, note) VALUES %s",
                                   [(k, int(s), clean(float(v)) if v is not None else None, n) for k, s, v, n in meta])
    conn.commit()
    for t in ("paper_xrapm_stints", "paper_xrapm_players", "paper_xrapm_fits", "paper_xrapm_lambda_cv", "paper_xrapm_meta"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        log(f"  {t}: {n:,} rows, {size}")


def print_checks(conn, players, fits):
    names = dict(pd.read_sql_query("SELECT player_id, player_name FROM player_bio", conn).itertuples(index=False))
    print("\nFits (paper_xrapm_fits):")
    print(fits[["version", "season", "rows", "players", "lambda", "prior_scale", "cv_rmse", "cv_rmse_zero", "cv_best_lambda",
                "home_edge_per_100", "r_with_rapm", "sd_xrapm", "sd_rapm", "mean_abs_diff"]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    for version in VERSIONS:
        print(f"\n{version}: top 8 by expected-points RAPM per season (qualified), with the actual-points RAPM in brackets")
        q = players[(players.version == version) & players.qualified].copy()
        q["name"] = q.player_id.map(names)
        for season, g in q.groupby("season"):
            top = g.sort_values("xrapm", ascending=False).head(8)
            print(f"  {label(season)}: " + "; ".join(f"{r.name} {r.xrapm:+.1f} [{r.rapm:+.1f}]" for r in top.itertuples()))
            g = g.dropna(subset=["rapm"]).assign(d=lambda d: d.xrapm - d.rapm)
            up, down = g.nlargest(3, "d"), g.nsmallest(3, "d")
            print("     biggest movers up: " + "; ".join(f"{r.name} {r.d:+.1f}" for r in up.itertuples())
                  + " | down: " + "; ".join(f"{r.name} {r.d:+.1f}" for r in down.itertuples()))


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('shot_xfg'), to_regclass('lineup_stints'), to_regclass('player_rapm')")
    assert all(cur.fetchone()), "needs shot_xfg (build_shot_making.py), lineup_stints (build_lineup_stints.py) and player_rapm (build_rapm.py)"

    ev = collect_events(conn, cur)
    log(f"{len(ev):,} attempts parsed ({int((ev.kind == 'fg').sum()):,} field goals, {int((ev.kind == 'ft').sum()):,} free throws)")
    st = load_stints(conn)
    ev, unmapped = map_to_stints(ev, st)
    fg, matched_no_p = price_field_goals(conn, ev[ev.kind == "fg"].copy())
    log(f"field goals priced: {(fg.source == 'chart').mean():.4%} from the chart, "
        f"{int((fg.source == 'shooter').sum()):,} at the shooter's mean, {int((fg.source == 'league').sum()):,} at the league mean")
    M, M_note = ft_shrinkage(cur)
    ft = price_free_throws(ev[ev.kind == "ft"].copy(), M)
    log(f"free throws priced with M = {M:.1f} pseudo-attempts ({M_note}); league FT% "
        + ", ".join(f"{label(s)} {v:.3f}" for s, v in ft.groupby('season').made.mean().items()))
    out = stint_table(fg, ft, st)
    meta, per = checks(out, fg, unmapped, matched_no_p, M, M_note)
    meta += [("pmake_source", 0, None, "shot_xfg: build_shot_making.py's five-fold by-player cross-fit over 1996-97 to 2025-26 (all seasons, later ones included)"),
             ("fallback_rule", 0, None, "unmatched attempt: parser's value; P(make) = shooter's mean chart P(make) for that value and season, else the league's"),
             ("ft_rule", 0, None, "leave-one-out shrunk FT%: (ftm - made + M x league) / (fta - 1 + M); league rate when no shooter id"),
             ("residual_rule", 0, None, "stored stint points minus points its attempts account for (chart value) are added unpriced")]

    players, fits, curve = fit_all(conn, out)
    write(conn, out, players, fits, curve, meta)
    print_checks(conn, players, fits)
    conn.close()
    log("done")


if __name__ == "__main__":
    main()
