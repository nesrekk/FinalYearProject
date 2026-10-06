"""
build_pregame_availability.py
==============================
Availability-aware pre-game odds (round 6, step 6): how much better is the
Season Simulator's pre-game model when it knows who played? The math is in
api/availability_lib.py (shared with the what-if tool's endpoint); the base
model is api/season_sim_lib.py's, unchanged.

The feature (availability_lib's docstring has the formulas): each team's
lineup strength in a game is 5 x the minutes-weighted mean rating of its
rotation players who played, minutes being each player's *expected* minutes
from his earlier games, ratings fixed before the season. Its deviation from
what the team rating already knows (last season's lineup and this season's
earlier lineups, blended with the prior's own weight), home minus away, is
added to the base model's log-odds with one fitted coefficient:

    logit p = logit p_base + b * avail.

Who played is known at tip-off (injury reports, the inactive list), not at
the time a forecast is usually made, and late scratches, a player hurt in
the first minute and coach's decisions are all folded into "played". So
every gain here is an **upper bound** on what injury information is worth
to this model; the page and the README say so.

Judgment calls (stated on the Methodology card too)
---------------------------------------------------
* Two rating sources, both fixed before the season so nothing from the
  season itself leaks in:
    bpm   the Projections page's Marcel-style BPM projection for the season,
          made from earlier seasons only (projection_backtest_rows; the
          projection's own constants - regression weights, aging steps -
          were fitted on all seasons, the player values only see earlier ones);
    rapm  last season's RAPM with the box-score prior (player_rapm, version
          prior). No 2019-20 RAPM, so this source starts in 2021-22.
  Ratings are not updated during the season.
* Players with no rating (rookies; under 250 minutes over three seasons for
  the projection) get a replacement value: the minutes-weighted mean of the
  same-season rating of such players, measured on the tune seasons only
  (BPM as published, RAPM with prior), with its weighted SD as their
  uncertainty in the what-if tool.
* Expected minutes (availability_lib.expected_minutes_table): his mean
  minutes in earlier games this season for this team, else for any team this
  season, else last season's minutes per game (2019-20 from
  player_season_stats), else the median minutes of such no-history
  appearances on the tune seasons. Regular-season games only (the join to
  game_scores drops the three NBA Cup finals). Never the minutes he played in the game
  (they carry the game's own result: blowouts, foul trouble, injuries).
* Rotation players only: players expected to play at least ROTATION_MIN = 10
  minutes. Deep-bench players enter blowouts, so "appeared" says something
  about the score (the count of appearances by players expected under 8
  minutes correlates 0.36 with the absolute margin). The version with every
  appearance ("_all") is scored beside it as a check, not chosen; 10 is a
  stated threshold, not tuned.
* Last season's lineup strength uses last season's minutes per team game on
  that team by players who averaged ROTATION_MIN or more there (2019-20 from
  player_team_stints / player_season_stats), weighted with this season's
  ratings, so it changes only with personnel.
* A game has 240 minutes: when the rotation players who played are expected
  to cover fewer, the rest go to a replacement-level player (the rating
  unrated players get); when more, every share is scaled down.
* The base model is an offset: its coefficients are never refitted, so the
  comparison isolates the lineup term. One coefficient b, by maximum
  likelihood (Newton).

Two versions are built
----------------------
platform  base = game_pregame_odds.p_home (the app's held-out odds, each
          season by coefficients fitted without it); b leave-one-season-out
          over 2020-21 to 2025-26 (2021-22 on for rapm). The app shows these,
          and the what-if tool uses the game's own season's b.
protocol  round 5's split (scripts/paper_eval.py): tune 2020-21 to 2023-24,
          validate 2024-25, test 2025-26; base = paper_eval's stored
          prior_rest predictions of each phase; the lineup feature with that
          phase's prior constants (tau2); b leave-one-season-out within tune,
          fitted on tune for validate, on tune + validate for test. The
          rating source is chosen on validate log loss; the test season is
          scored once. Paired intervals and p-values with paper_tests'
          functions (game-clustered bootstrap, sign-flip test, Diebold-Mariano),
          10,000 resamples.

Checks (the script stops on any failure)
  * every regular-season game 2020-21 on is in the odds table; lineups are
    missing for exactly the games player_game_lines lacks (one: CHI-LAC
    2026-01-20, not in the ESPN play-by-play), which keep the base odds;
  * each stored platform probability recomputes from p_base, avail and its
    season's b; the protocol base equals paper_eval_predictions;
  * the stored roster rows reproduce every team-game's lineup strength.

Tables written (dropped and rebuilt)
  pregame_availability_odds     one row per game 2020-21 on: base odds, the
                                lineup feature's parts per source, platform
                                and protocol probabilities, missing rotation
                                minutes per side
  pregame_availability_players  per game and side: the rotation players who
                                played and the rotation players who sat (on
                                the roster: played for the team earlier and
                                not for another team since), with expected
                                minutes and both ratings (what-if tool)
  pregame_availability_fit      constants, coefficients per season and phase,
                                metrics per phase, by games played and by the
                                size of the lineup change, the check on games
                                with complete lineups, the biggest upsets
  pregame_availability_tests    paper_eval_tests' columns: intervals and
                                tests per phase

Deterministic: every read is ordered (a float mean over rows in a different order differs in the
last bit) and the tests' seeds are paper_tests' md5 of the row key; two runs give identical tables.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 build_pregame_availability.py                  (~1 min)
    cd scripts && python3 build_pregame_availability.py --resamples 500  (a quick look)
Rerun after build_player_game_lines.py, build_season_sim.py, build_projections.py,
build_rapm.py or paper_eval.py; then restart impact_api and rerun
rebuild_all.sh paper-inputs.
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

import paper_eval as PE
import paper_tests as PT
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import availability_lib as A  # noqa: E402
import season_sim_lib as L  # noqa: E402
from paper_freeze import F  # noqa: E402  (the paper's rows: seasons to 2025-26, round 9 step 1)

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROTATION_MIN = 10.0                 # expected minutes for a player to count as a rotation player
SETS = {"": ROTATION_MIN, "_all": 0.0}   # main version, and every appearance as a check
SOURCES = tuple(A.RATING_SOURCES)
TUNE, VALIDATE, TEST = PE.TUNE, PE.VALIDATE, PE.TEST
FIRST = min(TUNE)
Z80 = 1.2815515655446004            # 80% normal interval half-width in SDs
AVAIL_BUCKETS = ((0, 1, "under 1"), (1, 3, "1-3"), (3, 6, "3-6"), (6, 1e9, "6 or more"))   # |avail|, points per 100
SHORT_MIN = 1.0                     # a side's identified minutes this far under the game's length = unidentified players
GAMES_BUCKETS = ((0, 9, "0-9"), (10, 29, "10-29"), (30, 99, "30+"))

T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


# ── loading ──────────────────────────────────────────────────────────────────

def load(conn):
    lines = pd.read_sql(f"""SELECT l.player_id, l.season, l.game_date, g.game_id, l.team_abbreviation AS team,
                                  l.seconds / 60.0 AS minutes
                           FROM {F('player_game_lines', 'l')}
                           JOIN {F('game_scores', 'g')} ON 'espn_' || g.espn_id = l.game_id AND g.team_abbreviation = l.team_abbreviation
                           WHERE l.seconds > 0 ORDER BY l.player_id, l.game_date, g.game_id""", conn)
    # minutes per game the season before: player_game_lines from 2020-21, player_season_stats for 2019-20
    per = lines.groupby(["season", "player_id"]).minutes.mean()
    prev_mpg = {(s + 1, p): float(v) for (s, p), v in per.items()}
    for p, m in pd.read_sql(f"SELECT player_id, min FROM {F('player_season_stats')} WHERE season = {FIRST - 1} ORDER BY player_id", conn).itertuples(index=False):
        prev_mpg[(FIRST, p)] = float(m)
    # last season's minutes per team
    st = pd.read_sql(f"SELECT player_id, team, gp, minutes FROM {F('player_team_stints')} WHERE season = {FIRST - 1} ORDER BY player_id, stint", conn)
    ps = pd.read_sql(f"""SELECT player_id, team_abbreviation AS team, gp, gp * min AS minutes FROM {F('player_season_stats')}
                         WHERE season = {FIRST - 1} ORDER BY player_id, team_abbreviation""", conn)
    first_prev = pd.concat([st, ps[~ps.player_id.isin(st.player_id)]])
    first_prev = first_prev.assign(season=FIRST)[["season", "team", "player_id", "gp", "minutes"]]
    pm = lines.groupby(["season", "team", "player_id"]).minutes.agg(["size", "sum"]).reset_index()
    pm = pm.rename(columns={"size": "gp", "sum": "minutes"}).assign(season=lambda d: d.season + 1)
    prev_team = pd.concat([first_prev, pm], ignore_index=True)
    tgp = pd.read_sql(f"""SELECT season + 1 AS season, team_abbreviation AS team, COUNT(*) AS n FROM {F('game_scores')}
                            WHERE season >= {FIRST - 1} GROUP BY 1, 2 ORDER BY 1, 2""", conn)
    team_games_prev = {(s, t): int(n) for s, t, n in tgp.itertuples(index=False)}
    ratings = {
        "bpm": pd.read_sql(f"""SELECT season, player_id, projection AS r, (hi - lo) / (2 * %(z)s) AS sd
                              FROM {F('projection_backtest_rows')} WHERE stat = 'bpm' ORDER BY season, player_id""", conn, params={"z": Z80}),
        "rapm": pd.read_sql(f"""SELECT season + 1 AS season, player_id, rapm AS r, rapm_se AS sd
                               FROM {F('player_rapm')} WHERE version = 'prior' ORDER BY season, player_id""", conn),
    }
    same_season = {
        "bpm": pd.read_sql(f"SELECT season, player_id, bpm AS x FROM {F('player_season_stats')} WHERE season >= {FIRST} ORDER BY 1, 2", conn),
        "rapm": pd.read_sql(f"SELECT season, player_id, rapm AS x FROM {F('player_rapm')} WHERE version = 'prior' ORDER BY 1, 2", conn),
    }
    odds = pd.read_sql(f"""SELECT game_id, season, game_date, home, away, p_home, home_won, home_games, away_games
                           FROM {F('game_pregame_odds')} WHERE season >= {FIRST} ORDER BY game_date, game_id""", conn)
    sigma = pd.read_sql(f"SELECT season, sigma_prev FROM {F('season_sim_seasons')}", conn).set_index("season").sigma_prev.to_dict()
    tau2 = {"platform": float(pd.read_sql("SELECT value FROM season_sim_params WHERE name = 'tau2'", conn).value[0])}
    ch = pd.read_sql("""SELECT model, value FROM paper_eval_choices WHERE task = 'pregame' AND parameter = 'tau2'
                        AND model IN ('constants', 'constants_test')""", conn).set_index("model").value
    tau2["tune"] = tau2["validate"] = float(ch["constants"])
    tau2["test"] = float(ch["constants_test"])
    # minutes ESPN's unidentified players took (game length minus the identified players' minutes)
    ident = pd.read_sql(f"""SELECT g.game_id, g.team_abbreviation AS team, 240 + 25 * (g.periods - 4) - SUM(l.seconds::numeric)::float8 / 60.0 AS unid
                            FROM {F('player_game_lines', 'l')} JOIN {F('game_scores', 'g')} ON 'espn_' || g.espn_id = l.game_id
                                 AND g.team_abbreviation = l.team_abbreviation
                            WHERE g.season >= {FIRST} GROUP BY 1, 2, g.periods ORDER BY 1, 2""", conn).set_index(["game_id", "team"]).unid
    names = pd.read_sql(f"""SELECT DISTINCT ON (player_id) player_id, player_name FROM (
                               SELECT player_id, player_name, 1 AS pri, 0 AS season FROM player_bio
                               UNION ALL SELECT player_id, player_name, 2, season FROM {F('player_season_stats')}) x
                           ORDER BY player_id, pri, season DESC""", conn).set_index("player_id").player_name
    base_eval = pd.read_sql(f"""SELECT phase, season, unit_id AS game_id, pred AS p_eval_base, actual FROM {F('paper_eval_predictions')}
                               WHERE task = 'pregame' AND model = 'prior_rest' AND season >= %s ORDER BY unit_id""", conn, params=(FIRST,))
    return lines, prev_mpg, prev_team, team_games_prev, ratings, same_season, odds, sigma, tau2, names, base_eval, ident


# ── constants from the tune seasons ─────────────────────────────────────────

def tune_constants(lines, prev_mpg, ratings, same_season):
    probe = A.expected_minutes_table(lines, prev_mpg, np.nan)
    nohist = probe[(probe.exp_from == "default") & probe.season.isin(TUNE)]
    default_minutes = float(nohist.minutes.median())
    em = A.expected_minutes_table(lines, prev_mpg, default_minutes)
    out = {"default_minutes": default_minutes, "default_minutes_n": int(len(nohist))}
    for src in SOURCES:
        rated = ratings[src].set_index(["season", "player_id"])
        d = em[em.season >= A.MIN_SEASON[src]]
        has = pd.MultiIndex.from_arrays([d.season, d.player_id]).isin(rated.index)
        un = d[~has & d.season.isin(TUNE)].merge(same_season[src], on=["season", "player_id"], how="inner")
        w = un.minutes.to_numpy()
        x = un.x.to_numpy(float)
        mean = float((w * x).sum() / w.sum())
        out[f"replacement_{src}"] = mean
        out[f"replacement_sd_{src}"] = float(np.sqrt((w * (x - mean) ** 2).sum() / w.sum()))
        out[f"replacement_n_{src}"] = int(un.player_id.nunique())
        out[f"unrated_minute_share_{src}"] = float(d.minutes[~has].sum() / d.minutes.sum())
    return em, out


# ── the feature ─────────────────────────────────────────────────────────────

def rate(df, src, ratings, consts):
    rated = ratings[src].set_index(["season", "player_id"])
    idx = pd.MultiIndex.from_arrays([df.season, df.player_id])
    r = rated.r.reindex(idx).to_numpy(float)
    sd = rated.sd.reindex(idx).to_numpy(float)
    known = ~np.isnan(r)
    return (np.where(known, r, consts[f"replacement_{src}"]), np.where(known, sd, consts[f"replacement_sd_{src}"]), known)


def team_games(em, src, ratings, consts, prev_team, team_games_prev, sigma, tau2, min_exp):
    """Per team-game: S (lineup strength), the reference and the deviation."""
    fill = consts[f"replacement_{src}"]
    d = em[(em.season >= A.MIN_SEASON[src]) & (em.exp_min >= min_exp)].copy()
    d["r"], _, _ = rate(d, src, ratings, consts)
    d["rm"] = d.r * d.exp_min
    tg = d.groupby(["game_id", "team"]).agg(rm=("rm", "sum"), m=("exp_min", "sum"), players=("player_id", "size"),
                                            season=("season", "first"), game_date=("game_date", "first")).reset_index()
    tg["S"] = A.strength_from_sums(tg.rm, tg.m, fill)
    # last season: rotation players' minutes per team game on that team, this season's ratings
    pt = prev_team[(prev_team.season >= A.MIN_SEASON[src]) & (prev_team.minutes >= min_exp * prev_team.gp)].copy()
    pt["r"], _, _ = rate(pt, src, ratings, consts)
    pt["mpg_team"] = pt.minutes / [team_games_prev[(s, t)] for s, t in zip(pt.season, pt.team)]
    sp = pt.assign(rm=pt.r * pt.mpg_team).groupby(["season", "team"]).agg(rm=("rm", "sum"), m=("mpg_team", "sum"))
    s_prev = pd.Series(A.strength_from_sums(sp.rm, sp.m, fill), index=sp.index).to_dict()
    tg = tg.sort_values(["season", "team", "game_date", "game_id"]).reset_index(drop=True)
    g = tg.groupby(["season", "team"]).S
    tg["n_before"] = g.cumcount()
    tg["mean_before"] = (g.cumsum() - tg.S) / tg.n_before.where(tg.n_before > 0)
    tg["S_prev"] = [s_prev.get((s, t), np.nan) for s, t in zip(tg.season, tg.team)]
    if tg.S_prev.isna().any():
        raise SystemExit(f"{src}: no last-season lineup for {tg[tg.S_prev.isna()][['season', 'team']].drop_duplicates().values.tolist()}")
    tg["ref"] = A.reference(tg.S_prev, tg.mean_before, tg.n_before, tg.season.map(sigma).to_numpy(float), tau2)
    tg["dev"] = tg.S - tg.ref
    return tg


def attach(odds, tg, suffix):
    keep = tg[["game_id", "team", "S", "ref", "dev", "players"]]
    o = odds.merge(keep.rename(columns={"team": "home", "S": f"s_home{suffix}", "ref": f"ref_home{suffix}", "dev": "_dh",
                                        "players": f"players_home{suffix}"}), on=["game_id", "home"], how="left")
    o = o.merge(keep.rename(columns={"team": "away", "S": f"s_away{suffix}", "ref": f"ref_away{suffix}", "dev": "_da",
                                     "players": f"players_away{suffix}"}), on=["game_id", "away"], how="left")
    o[f"avail{suffix}"] = (o._dh - o._da).fillna(0.0)
    return o.drop(columns=["_dh", "_da"])


# ── fits ────────────────────────────────────────────────────────────────────

def loso(o, feat, base, seasons):
    """Leave-one-season-out over `seasons`: predictions and {season: (b, se)}."""
    y = o.home_won.to_numpy(float)
    bl = A.logit(o[base])
    pred = np.full(len(o), np.nan)
    coefs = {}
    for s in seasons:
        tr = (o.season.isin(seasons) & (o.season != s)).to_numpy()
        te = (o.season == s).to_numpy()
        b, se = A.fit_offset_logit(o[feat].to_numpy()[tr], bl[tr], y[tr])
        pred[te] = A.predict(o[base].to_numpy()[te], o[feat].to_numpy()[te], b)
        coefs[s] = (b, se)
    return pred, coefs


def fit_on(o, feat, base, seasons):
    m = o.season.isin(seasons).to_numpy()
    return A.fit_offset_logit(o[feat].to_numpy()[m], A.logit(o[base])[m], o.home_won.to_numpy(float)[m])


# ── roster table (what-if) ──────────────────────────────────────────────────

def roster_rows(em, odds, ratings, consts, names):
    """Rotation players who played, and rotation players who sat but were still on the team."""
    played = em[em.exp_min >= ROTATION_MIN][["game_id", "team", "season", "game_date", "player_id", "exp_min", "exp_from", "minutes"]]
    played = played.assign(played=True)
    # team games (both sides) in date order
    tgames = pd.concat([odds[["game_id", "season", "game_date", "home"]].rename(columns={"home": "team"}),
                        odds[["game_id", "season", "game_date", "away"]].rename(columns={"away": "team"})])
    # every appearance, for "still on the team": the player's next appearance must not be for another team
    app = em[["season", "player_id", "team", "game_date", "game_id", "minutes"]].sort_values(["season", "player_id", "game_date", "game_id"])
    app["cum_m"] = app.groupby(["season", "player_id", "team"]).minutes.cumsum()
    app["cum_n"] = app.groupby(["season", "player_id", "team"]).cumcount() + 1
    sat = []
    for (season, team), tg in tgames.groupby(["season", "team"]):
        mine = app[(app.season == season) & (app.team == team)]
        if mine.empty:
            continue
        everyone = app[(app.season == season) & app.player_id.isin(mine.player_id.unique())]
        tg = tg.sort_values(["game_date", "game_id"])
        for pid, pm in mine.groupby("player_id"):
            pm = pm.sort_values(["game_date", "game_id"])
            other = everyone[(everyone.player_id == pid) & (everyone.team != team)]
            cand = tg[(tg.game_date > pm.game_date.iloc[0]) & ~tg.game_id.isin(pm.game_id)]
            if cand.empty:
                continue
            # mean minutes with this team before each candidate game
            k = np.searchsorted(pm.game_date.to_numpy(), cand.game_date.to_numpy(), side="left")
            mean_before = pm.cum_m.to_numpy()[k - 1] / pm.cum_n.to_numpy()[k - 1]
            # left for another team: an appearance for another team after his last game here and before this one
            last_here = pm.game_date.to_numpy()[k - 1]
            od = np.sort(other.game_date.to_numpy())
            gone = np.array([((od > lh) & (od < gd)).any() for lh, gd in zip(last_here, cand.game_date.to_numpy())]) if len(od) else \
                np.zeros(len(cand), bool)
            keep = (mean_before >= ROTATION_MIN) & ~gone
            if keep.any():
                c = cand[keep]
                sat.append(pd.DataFrame({"game_id": c.game_id.to_numpy(), "team": team, "season": season,
                                         "game_date": c.game_date.to_numpy(), "player_id": pid,
                                         "exp_min": mean_before[keep], "exp_from": "team", "minutes": np.nan, "played": False}))
    rows = pd.concat([played] + sat, ignore_index=True)
    for src in SOURCES:
        r, sd, known = rate(rows, src, ratings, consts)
        ok = (rows.season >= A.MIN_SEASON[src]).to_numpy()
        rows[f"r_{src}"] = np.where(ok, r, np.nan)
        rows[f"sd_{src}"] = np.where(ok, sd, np.nan)
        rows[f"rated_{src}"] = np.where(ok, known, False)
    rows["player_name"] = names.reindex(rows.player_id).fillna("").to_numpy()
    return rows


# ── main ────────────────────────────────────────────────────────────────────

def py(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def write(cur, table, ddl, cols, rows):
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TABLE {table} ({ddl})")
    execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s",
                   [tuple(py(v) for v in row) for row in rows], page_size=5000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resamples", type=int, default=PT.RESAMPLES)
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    lines, prev_mpg, prev_team, team_games_prev, ratings, same_season, odds, sigma, tau2, names, base_eval, ident = load(conn)
    log(f"{len(lines):,} player-games, {len(odds):,} games {FIRST}-{odds.season.max()}")
    em, consts = tune_constants(lines, prev_mpg, ratings, same_season)
    consts["rotation_min"] = ROTATION_MIN
    log("constants (tune seasons): " + json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in consts.items()}))
    print("expected minutes from:", em.exp_from.value_counts().to_dict())

    # ---- features, platform and protocol ----
    feats, short = {}, {}
    for ctx in ("platform", "tune", "test"):
        o = odds.copy()
        for src in SOURCES:
            for sfx, thr in SETS.items():
                tg = team_games(em, src, ratings, consts, prev_team, team_games_prev, sigma, tau2[ctx], thr)
                if sfx == "" and ctx == "platform":
                    short[src] = (int((tg.players < 5).sum()), int((tg.m < A.TEAM_MINUTES).sum()), len(tg))
                o = attach(o, tg, f"_{src}{sfx}")
                o.loc[o.season < A.MIN_SEASON[src], [c for c in o.columns if c.endswith(f"_{src}{sfx}")]] = np.nan
        feats[ctx] = o
    P = feats["platform"]
    print("rotation sets (team-games under five players, under 240 expected minutes, all):", short)
    missing = P[P.players_home_bpm.isna() | P.players_away_bpm.isna()]
    print("games without lineups:", missing[["game_id", "game_date", "home", "away"]].values.tolist())
    if len(missing) != 1 or missing.game_id.iloc[0] != "0022500614":
        raise SystemExit("expected exactly one game without lineups (CHI-LAC 2026-01-20)")
    P["lineups_ok"] = P.players_home_bpm.notna() & P.players_away_bpm.notna()
    P["unidentified_min_home"] = [max(0.0, float(ident.get((g, t), np.nan))) for g, t in zip(P.game_id, P.home)]
    P["unidentified_min_away"] = [max(0.0, float(ident.get((g, t), np.nan))) for g, t in zip(P.game_id, P.away)]

    fit_rows = []

    def fr(kind, name, value, source="", phase="", season=None, n=None, se=None, detail=None):
        fit_rows.append((kind, source, phase, season, name, value, se, n, json.dumps(detail) if detail is not None else None))

    for k, v in consts.items():
        src = next((s for s in SOURCES if k.endswith(f"_{s}")), "")
        fr("constant", k[: -len(src) - 1] if src else k, float(v), source=src, phase="tune",
           n=None, detail={"seasons": PE.span(TUNE)})
    for src, (n5, n240, ntg) in short.items():
        fr("check", "team_games_under_five_rotation_players", n5, source=src, phase="platform", n=ntg)
        fr("check", "team_games_under_240_expected_minutes", n240, source=src, phase="platform", n=ntg)
    for ctx, t in tau2.items():
        fr("constant", "tau2", t, phase=ctx)

    # platform: leave-one-season-out over every season with lineups
    for src in SOURCES:
        for sfx in SETS:
            f = f"avail_{src}{sfx}"
            seasons = sorted(P.season[P.season >= A.MIN_SEASON[src]].unique())
            sub = P[P.season.isin(seasons)]
            pred, coefs = loso(sub, f, "p_home", seasons)
            P.loc[sub.index, f"p_{src}{sfx}"] = pred
            for s, (b, se) in coefs.items():
                fr("coef", "b", b, source=src + sfx, phase="platform", season=int(s), se=se, n=int((sub.season != s).sum()),
                   detail={"fitted_on": [int(x) for x in seasons if x != s]})
            b, se = fit_on(sub, f, "p_home", seasons)
            fr("coef", "b_all", b, source=src + sfx, phase="platform", se=se, n=len(sub))
            y = sub.home_won.to_numpy(float)
            fr("metric", "log_loss", L.log_loss(pred, y), source=src + sfx, phase="platform", n=len(sub),
               detail={"base": L.log_loss(sub.p_home, y), "seasons": PE.span(seasons)})
            fr("metric", "brier", L.brier(pred, y), source=src + sfx, phase="platform", n=len(sub), detail={"base": L.brier(sub.p_home, y)})
            fr("feature", "avail_sd", float(sub[f].std()), source=src + sfx, phase="platform", n=len(sub))
            log(f"platform {src}{sfx}: log loss {L.log_loss(sub.p_home, y):.4f} -> {L.log_loss(pred, y):.4f}, "
                f"b {b:.4f} ± {se:.4f}")
    # stored probabilities recompute from their season's b
    coef = {(r[1], r[3]): r[5] for r in fit_rows if r[0] == "coef" and r[4] == "b" and r[2] == "platform"}
    for src in SOURCES:
        sub = P[P.season >= A.MIN_SEASON[src]]
        again = A.predict(sub.p_home, sub[f"avail_{src}"], sub.season.map(lambda s: coef[(src, s)]).to_numpy())
        assert np.abs(again - sub[f"p_{src}"]).max() < 1e-12

    # protocol
    be = base_eval.set_index("game_id")
    for ctx in ("tune", "test"):
        o = feats[ctx]
        o["p_eval_base"] = o.game_id.map(be.p_eval_base)
        o["phase"] = o.season.map(PE.PHASE_OF)
    E = feats["tune"].copy()
    T = feats["test"]
    if E.p_eval_base.isna().any():
        raise SystemExit("paper_eval_predictions lacks prior_rest for some games 2020-21 on: rerun paper_eval.py")
    assert (E.game_id.map(be.actual) == E.home_won.astype(float)).all()
    proto = {}
    for src in SOURCES:
        for sfx in SETS:
            f, key = f"avail_{src}{sfx}", f"{src}{sfx}"
            tune_s = [s for s in TUNE if s >= A.MIN_SEASON[src]]
            pred = pd.Series(np.nan, index=E.index)
            sub = E[E.season.isin(tune_s)]
            p, coefs = loso(sub, f, "p_eval_base", tune_s)
            pred[sub.index] = p
            for s, (b, se) in coefs.items():
                fr("coef", "b", b, source=key, phase="tune", season=int(s), se=se, n=int(sub.season.isin(tune_s).sum() - (sub.season == s).sum()),
                   detail={"fitted_on": [int(x) for x in tune_s if x != s]})
            b, se = fit_on(E, f, "p_eval_base", tune_s)
            m = (E.season == VALIDATE).to_numpy()
            pred[m] = A.predict(E.p_eval_base[m], E[f][m], b)
            fr("coef", "b", b, source=key, phase="validate", season=VALIDATE, se=se, n=int(E.season.isin(tune_s).sum()),
               detail={"fitted_on": tune_s})
            fit_s = tune_s + [VALIDATE]
            b, se = fit_on(T, f, "p_eval_base", fit_s)
            m = (T.season == TEST).to_numpy()
            pred[m] = A.predict(T.p_eval_base[m], T[f][m], b)
            fr("coef", "b", b, source=key, phase="test", season=TEST, se=se, n=int(T.season.isin(fit_s).sum()),
               detail={"fitted_on": fit_s})
            proto[key] = pred
            E[f"p_eval_{key}"] = pred
    for ph, seasons in (("tune", list(TUNE)), ("validate", [VALIDATE]), ("test", [TEST])):
        m = E.season.isin(seasons)
        y = E.home_won[m].to_numpy(float)
        fr("metric", "log_loss", L.log_loss(E.p_eval_base[m], y), source="prior_rest", phase=ph, n=int(m.sum()))
        fr("metric", "brier", L.brier(E.p_eval_base[m], y), source="prior_rest", phase=ph, n=int(m.sum()))
        for key in proto:
            mm = m & E[f"p_eval_{key}"].notna()
            y2 = E.home_won[mm].to_numpy(float)
            fr("metric", "log_loss", L.log_loss(E[f"p_eval_{key}"][mm], y2), source=key, phase=ph, n=int(mm.sum()),
               detail={"base": L.log_loss(E.p_eval_base[mm], y2)})
            fr("metric", "brier", L.brier(E[f"p_eval_{key}"][mm], y2), source=key, phase=ph, n=int(mm.sum()),
               detail={"base": L.brier(E.p_eval_base[mm], y2)})
        log(f"protocol {ph}: base {L.log_loss(E.p_eval_base[m], y):.4f}; " + ", ".join(
            f"{k} {L.log_loss(E[f'p_eval_{k}'][m & E[f'p_eval_{k}'].notna()], E.home_won[m & E[f'p_eval_{k}'].notna()].astype(float)):.4f}"
            for k in proto))
    # the source, chosen on validate
    mv = E.season == VALIDATE
    ll_val = {src: L.log_loss(E[f"p_eval_{src}"][mv], E.home_won[mv].astype(float)) for src in SOURCES}
    chosen = min(ll_val, key=ll_val.get)
    fr("choice", "source", None, source=chosen, phase="validate", season=VALIDATE,
       detail={"criterion": f"log loss on {PE.label(VALIDATE)}", "candidates": {k: round(v, 6) for k, v in ll_val.items()},
               "note": "rotation set (players expected to play 10+ minutes); the all-appearances version is a check, never a candidate"})
    log(f"validate picks {chosen}: {ll_val}")

    # ---- tests (paper_tests' functions, on the protocol predictions) ----
    out = PT.Rows(args.resamples)

    def series(col, m):
        d = E[m & E[col].notna()]
        return PT.Series(pd.DataFrame({"season": d.season.to_numpy(), "unit_id": d.game_id.to_numpy(), "pred": d[col].to_numpy(float),
                                       "actual": d.home_won.to_numpy(float), "lo": np.nan, "hi": np.nan,
                                       "unit_date": d.game_date.to_numpy(), "cluster": d.game_id.to_numpy()}), "")
    pairs = [(f"avail_{s}", "prior_rest") for s in SOURCES] + [("avail_bpm", "avail_rapm")] + \
            [(f"avail_{s}_all", f"avail_{s}") for s in SOURCES]
    colof = {"prior_rest": "p_eval_base", **{f"avail_{k}": f"p_eval_{k}" for k in proto}}
    for ph, seasons in (("tune", list(TUNE)), ("validate", [VALIDATE]), ("test", [TEST])):
        m = E.season.isin(seasons)
        for metric in ("log_loss", "brier"):
            for a, b in pairs:
                sa, sb = series(colof[a], m), series(colof[b], m)
                PT.paired_rows(out, "pregame", ph, a, b, sa, sb, metric, "",
                               note="paired on the games both forecast; base = paper_eval's prior_rest of the phase")
            for model in ["prior_rest"] + [f"avail_{s}" for s in SOURCES]:
                PT.single_rows(out, "pregame", ph, model, series(colof[model], m), metric)
        log(f"tests {ph} done")
    tests = pd.DataFrame(out.rows, columns=PT.Rows.COLS)
    show = tests[(tests.model_b != "") & (tests.metric == "log_loss")]
    print(show[["phase", "model_a", "model_b", "n", "diff", "ci_lo", "ci_hi", "p_boot", "dm_p"]].round(5).to_string(index=False))

    # ---- roster rows, missing minutes, buckets, upsets (platform odds) ----
    roster = roster_rows(em, odds, ratings, consts, names)
    sat = roster[~roster.played].groupby(["game_id", "team"]).exp_min.sum()
    P["absent_min_home"] = [float(sat.get((g, t), 0.0)) for g, t in zip(P.game_id, P.home)]
    P["absent_min_away"] = [float(sat.get((g, t), 0.0)) for g, t in zip(P.game_id, P.away)]
    # the stored players reproduce S exactly
    pl = roster[roster.played & (roster.season >= A.MIN_SEASON["bpm"])]
    sums = pl.assign(rm=pl.r_bpm * pl.exp_min).groupby(["game_id", "team"]).agg(rm=("rm", "sum"), m=("exp_min", "sum"))
    s_again = pd.Series(A.strength_from_sums(sums.rm, sums.m, consts["replacement_bpm"]), index=sums.index)
    chk = P[P.lineups_ok].set_index(["game_id", "home"]).s_home_bpm
    assert np.abs(s_again.reindex(chk.index).to_numpy() - chk.to_numpy()).max() < 1e-9
    log(f"roster rows: {len(roster):,} ({int(roster.played.sum()):,} played, {int((~roster.played).sum()):,} sat)")

    y = P.home_won.to_numpy(float)
    pc = P[f"p_{chosen}"].to_numpy(float)
    ok = ~np.isnan(pc)
    gp = np.minimum(P.home_games, P.away_games).to_numpy()
    miss = (P.absent_min_home + P.absent_min_away).to_numpy()
    for lo, hi, lab in GAMES_BUCKETS:
        m = ok & (gp >= lo) & (gp <= hi)
        fr("bucket", "games_played", L.log_loss(pc[m], y[m]), source=chosen, phase="platform", n=int(m.sum()),
           detail={"label": lab, "base": L.log_loss(P.p_home[m], y[m])})
    av = np.abs(P[f"avail_{chosen}"].to_numpy(float))
    for lo, hi, lab in AVAIL_BUCKETS:
        m = ok & (av >= lo) & (av < hi)
        fr("bucket", "abs_avail", L.log_loss(pc[m], y[m]), source=chosen, phase="platform", n=int(m.sum()),
           detail={"label": lab, "base": L.log_loss(P.p_home[m], y[m]), "mean_move": float(np.abs(pc[m] - P.p_home[m]).mean())})
    # games where either side had minutes by ESPN players without an id (they look absent)
    short = ((P.unidentified_min_home > SHORT_MIN) | (P.unidentified_min_away > SHORT_MIN)).to_numpy()
    for lab, m in (("complete", ok & ~short), ("unidentified", ok & short)):
        fr("check", f"log_loss_{lab}_lineups", L.log_loss(pc[m], y[m]), source=chosen, phase="platform", n=int(m.sum()),
           detail={"base": L.log_loss(P.p_home[m], y[m]), "short_min": SHORT_MIN})
    log(f"lineups with unidentified minutes: {int(short.sum())} games; complete-lineup games: log loss "
        f"{L.log_loss(P.p_home[ok & ~short], y[ok & ~short]):.4f} -> {L.log_loss(pc[ok & ~short], y[ok & ~short]):.4f}")
    pw = np.where(P.home_won, P.p_home, 1 - P.p_home)
    pa = np.where(P.home_won, pc, 1 - pc)
    order = np.argsort(pw)[:10]
    sat_names = roster[~roster.played].sort_values("exp_min", ascending=False).groupby(["game_id", "team"]).player_name.apply(list)
    for rank, i in enumerate(order, 1):
        r = P.iloc[i]
        winner, loser = (r.home, r.away) if r.home_won else (r.away, r.home)
        fr("upset", "winner_p", float(pa[i]), source=chosen, phase="platform", season=int(r.season),
           detail={"rank": rank, "game_id": r.game_id, "game_date": str(r.game_date), "home": r.home, "away": r.away,
                   "winner": winner, "base": float(pw[i]),
                   "sat_winner": sat_names.get((r.game_id, winner), []), "sat_loser": sat_names.get((r.game_id, loser), [])})
    up = [x for x in fit_rows if x[0] == "upset"]
    print("biggest upsets, winner's chance before -> with who played:")
    for x in up:
        d = json.loads(x[8])
        print(f"  {d['game_date']} {d['away']}@{d['home']} winner {d['winner']}: {d['base']:.3f} -> {x[5]:.3f}  "
              f"sat: {d['winner']} {d['sat_winner'][:3]}, other {d['sat_loser'][:3]}")

    # ---- write ----
    E_cols = {c: E.set_index("game_id")[c] for c in E.columns if c.startswith("p_eval_")}
    for c, s in E_cols.items():
        P[c] = P.game_id.map(s)
    P["phase"] = P.season.map(PE.PHASE_OF)
    odds_cols = (["game_id", "season", "game_date", "home", "away", "phase", "home_won", "lineups_ok", "p_base"]
                 + [f"{a}_{s}" for s in SOURCES for a in ("avail", "p")]
                 + [f"{a}_{side}_{s}" for s in SOURCES for side in ("home", "away") for a in ("s", "ref", "players")]
                 + [f"{a}_{s}_all" for s in SOURCES for a in ("avail", "p")]
                 + ["absent_min_home", "absent_min_away", "unidentified_min_home", "unidentified_min_away", "p_eval_base"] + [f"p_eval_{k}" for k in proto])
    P["p_base"] = P.p_home
    num = lambda c: "SMALLINT" if c.startswith("players_") else "DOUBLE PRECISION"  # noqa: E731
    ddl = ("game_id TEXT PRIMARY KEY, season INTEGER, game_date DATE, home TEXT, away TEXT, phase TEXT, home_won BOOLEAN, "
           "lineups_ok BOOLEAN, " + ", ".join(f"{c} {num(c)}" for c in odds_cols[8:]))
    cur = conn.cursor()
    write(cur, "pregame_availability_odds", ddl, odds_cols, P[odds_cols].itertuples(index=False, name=None))
    cur.execute("CREATE INDEX ON pregame_availability_odds (season, game_date)")
    pcols = ["game_id", "team", "player_id", "player_name", "played", "exp_min", "exp_from", "minutes"] + \
        [f"{a}_{s}" for s in SOURCES for a in ("r", "sd", "rated")]
    write(cur, "pregame_availability_players", """game_id TEXT, team TEXT, player_id BIGINT, player_name TEXT, played BOOLEAN,
        exp_min DOUBLE PRECISION, exp_from TEXT, minutes DOUBLE PRECISION, r_bpm DOUBLE PRECISION, sd_bpm DOUBLE PRECISION,
        rated_bpm BOOLEAN, r_rapm DOUBLE PRECISION, sd_rapm DOUBLE PRECISION, rated_rapm BOOLEAN,
        PRIMARY KEY (game_id, team, player_id)""", pcols, roster[pcols].itertuples(index=False, name=None))
    write(cur, "pregame_availability_fit", """kind TEXT, source TEXT, phase TEXT, season INTEGER, name TEXT,
        value DOUBLE PRECISION, se DOUBLE PRECISION, n INTEGER, detail JSONB""",
          ["kind", "source", "phase", "season", "name", "value", "se", "n", "detail"], fit_rows)
    cur.execute("DROP TABLE IF EXISTS pregame_availability_tests")
    cur.execute(f"CREATE TABLE pregame_availability_tests ({PT.DDL})")
    execute_values(cur, f"INSERT INTO pregame_availability_tests ({', '.join(PT.Rows.COLS)}) VALUES %s", out.rows)
    conn.commit()
    cur.execute("""SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) FROM pg_catalog.pg_statio_user_tables
                   WHERE relname LIKE 'pregame_availability%' ORDER BY 1""")
    log(f"wrote {len(P):,} games, {len(roster):,} roster rows, {len(fit_rows)} fit rows, {len(out.rows)} test rows; sizes {cur.fetchall()}")


if __name__ == "__main__":
    main()
