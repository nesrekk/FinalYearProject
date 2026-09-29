"""
paper_numbers.py
=================
Every number the conference paper (paper/nba_hub_paper.tex) states, read
from the database and written to paper/numbers.tex as LaTeX macros, one per
number, with the table or query it came from in a comment beside it. The
paper types no number by hand: it says \\pnRapmNextPriorRmse{} and this
script decides that is 14.77. Rebuild a pipeline, rerun this, recompile.

Formatting is decided here, once:
  * decimals are rounded half-up on the stored value as written (Decimal of
    repr(float)), so a stored 0.425 prints 0.43, not the binary float's 0.42;
  * thousands get a LaTeX-safe comma ({,}), minus signs \\ensuremath{-}, so a
    macro works in text and in math mode alike;
  * seasons are stored as end years (2026 = 2025-26) and print 2025--26;
  * small counts that the prose spells out (five-fold, nine games) print as
    words.
Macro names are \\pn + letters only (TeX names can't hold digits), which is
also how --check finds every macro the paper uses.

Claims: some sentences in the paper depend on an ordering or a threshold
("lands at 3,000 in every season", "the record-only baseline wins on Brier").
Each such dependency is checked here; if a rebuild breaks one, the script
stops and names the sentence to rewrite instead of printing a number that
contradicts the text around it.

Data quality: the error classes of the public feeds and their sizes are
measured by scripts/paper_data_audit.py (round 5 step 5) into paper_data_audit,
which names the macro and format of each number it wants printed; the section
data_audit() below prints them and checks the audit against the tables the
other sections read (a stale audit stops the run). The audit also writes
paper/tables/data_audit.tex, whose numbers are all macros from this file;
--check covers every table the paper inputs.

Read-only: one read-only autocommit session, no table is created or changed.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_numbers.py               # writes ../paper/numbers.tex
    cd scripts && python3 paper_numbers.py --check       # also checks the paper's macros
    cd scripts && python3 paper_numbers.py --out FILE    # write elsewhere
"""

import argparse
import json
import math
import os
import re
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

import psycopg2

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))
import season_sim_lib  # noqa: E402  (pure numpy/pandas, no database or DDL at import)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_DIR = os.path.join(ROOT, "paper")
DEFAULT_OUT = os.path.join(PAPER_DIR, "numbers.tex")
DEFAULT_PAPER = os.path.join(PAPER_DIR, "nba_hub_paper.tex")

NAME_RE = re.compile(r"^pn[A-Z][A-Za-z]*$")
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
         10: "ten"}

# Thresholds the paper's text states; chosen once here, printed through macros.
THREE_PA_MIN_GAMES = 20        # players with 20+ games in the 3PA-vs-NBA.com check (the smoke test's floor)
CALIB_BIG_BIN = 500_000        # "the bins that each hold over 500,000 shots"
STALE_SCORE_SHARE = 0.10       # a season "has stale score fields" when 10%+ of its games don't reconcile

MODEL_LABELS = {"logreg": "logistic regression", "random_forest": "random forest",
                "gradient_boosting": "gradient boosting"}

# ---------------------------------------------------------------- formatting

def _dec(x):
    return Decimal(repr(float(x))) if not isinstance(x, Decimal) else x


def _sign(d, s):
    return ("\\ensuremath{-}" + s.lstrip("-")) if d < 0 and s.strip("-0.") != "" else s.lstrip("-")


def dec(x, n, rounding=ROUND_HALF_UP):
    """x rounded to n decimals (half-up on the stored value), LaTeX-safe."""
    d = _dec(x)
    q = d.quantize(Decimal(1).scaleb(-n), rounding=rounding)
    s = f"{q:,.{n}f}" if abs(q) >= 1000 else f"{q:.{n}f}"
    return _sign(q, s.replace(",", "{,}"))


def pct(x, n):
    """A share (0-1) as a percentage number, no % sign (the paper writes \\%)."""
    return dec(_dec(x) * 100, n)


def ceil_pct(x, n):
    return dec(_dec(x) * 100, n, ROUND_CEILING)


def integer(n):
    return f"{int(n):,}".replace(",", "{,}")


def word(n):
    return WORDS.get(int(n), integer(n))


def millions(n, d):
    return dec(Decimal(int(n)) / Decimal(1_000_000), d)


def pval(p):
    """A p-value as a math fragment: p<0.001 below that, else p=0.0xx (three decimals under 0.01, two above)."""
    p = float(p)
    if p < 0.001:
        return "\\ensuremath{p<0.001}"
    return "\\ensuremath{p=" + dec(p, 3 if p < 0.01 else 2) + "}"


def pcell(p):
    """The same p-value for a table cell: <0.001 or the number alone."""
    p = float(p)
    return "\\ensuremath{<0.001}" if p < 0.001 else dec(p, 3 if p < 0.01 else 2)


def season(end):
    """End-year int (2026) or '2025-26' text -> 2025--26."""
    if isinstance(end, str):
        end = int(end[:4]) + 1
    return f"{end - 1}--{str(end)[-2:]}"


# ---------------------------------------------------------------- collection

class Numbers:
    def __init__(self):
        self.items = []        # (section, name, value, source)
        self.names = set()
        self.section = ""
        self.failed = []
        self.raw = {}          # values one section hands to a later one's checks

    def start(self, section):
        self.section = section

    def add(self, name, value, source):
        full = "pn" + name
        if not NAME_RE.match(full):
            raise ValueError(f"bad macro name {full!r}: letters only after \\pn")
        if full in self.names:
            raise ValueError(f"macro {full} defined twice")
        self.names.add(full)
        self.items.append((self.section, full, value, " ".join(source.split())))

    def claim(self, ok, sentence):
        """A sentence of the paper that depends on the data; a failure stops the run."""
        if not ok:
            self.failed.append(sentence)

    def render(self):
        out = ["% paper/numbers.tex -- GENERATED by scripts/paper_numbers.py from the database. Do not edit;",
               "% rerun the script. Each macro's source table or query is in the comment beside it.",
               ""]
        last = None
        for section, name, value, source in self.items:
            if section != last:
                out += ["", f"% ---- {section}"]
                last = section
            out.append(f"\\newcommand{{\\{name}}}{{{value}}}% {source}")
        return "\n".join(out).lstrip("\n") + "\n"


def _corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy)


def one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def rows(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def as_json(v):
    return v if isinstance(v, (dict, list)) else json.loads(v)


# ---------------------------------------------------------------- sections

def data_and_pipeline(cur, N):
    N.start("Data and pipeline (Section: Data and Pipeline, abstract)")
    espn, espn_games = one(cur, "SELECT count(*), count(DISTINCT game_id) FROM pbp_events WHERE game_id LIKE 'espn\\_%'")
    N.add("EspnEventsMillion", millions(espn, 2), f"pbp_events rows with an ESPN game id ({espn:,}; nba_api twin rows excluded)")
    N.add("EspnEventsMillionShort", millions(espn, 1), "same count, one decimal (abstract)")
    shots = one(cur, "SELECT count(*) FROM player_shots")[0]
    N.add("ShotsMillion", millions(shots, 2), f"count(*) player_shots ({shots:,}; all seasons and game types)")
    N.add("ShotsMillionShort", millions(shots, 1), "same count, one decimal (abstract)")
    gs = one(cur, "SELECT count(*) FROM game_scores")[0]
    N.add("GameScoreRows", integer(gs), "count(*) game_scores (team-game rows)")

    n, s0, s1 = one(cur, "SELECT count(*), min(season), max(season) FROM lineup_stints")
    N.add("Stints", integer(n), "count(*) lineup_stints")
    N.add("StintFirstSeason", season(s0), "min(season) lineup_stints")
    N.add("StintLastSeason", season(s1), "max(season) lineup_stints")
    games, ok = one(cur, "SELECT count(*), sum(game_ok::int) FROM lineup_stint_games")
    N.raw["games"] = (games, ok)
    N.claim(games == espn_games, f"every ESPN game is parsed ({games} stint games vs {espn_games} ESPN games)")
    N.add("GamesParsed", integer(games), "count(*) lineup_stint_games")
    N.add("GamesReconciled", integer(ok), "sum(game_ok) lineup_stint_games (final score, length, team totals)")

    # Crediting every positive step of ESPN's score fields: share of games whose sum != the real final.
    stale = rows(cur, """
        WITH s AS (SELECT e.game_id,
                          e.score_home - lag(e.score_home) OVER w dh, e.score_away - lag(e.score_away) OVER w da
                   FROM pbp_events e JOIN lineup_stint_games g ON g.game_id = e.game_id
                   WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number))
        SELECT g.season, count(*), sum(((t.ph = g.final_home) AND (t.pa = g.final_away))::int)
        FROM (SELECT game_id, sum(greatest(dh, 0)) ph, sum(greatest(da, 0)) pa FROM s GROUP BY 1) t
        JOIN lineup_stint_games g USING (game_id) GROUP BY 1 ORDER BY 1""")
    N.raw["stale"] = {s: (g, g - k) for s, g, k in stale}
    bad = [(s, g, g - k) for s, g, k in stale if (g - k) / g >= STALE_SCORE_SHARE]
    seasons_bad = [s for s, _, _ in bad]
    N.claim(bool(bad) and seasons_bad == list(range(seasons_bad[0], seasons_bad[-1] + 1)),
            "the stale-score seasons form one run (Data: 'the 2020-21 to 2022-23 games')")
    N.add("StaleFirstSeason", season(seasons_bad[0]),
          f"first season where {STALE_SCORE_SHARE:.0%}+ of games' summed positive ESPN score steps != the final")
    N.add("StaleLastSeason", season(seasons_bad[-1]), "last such season")
    share = sum(b for _, _, b in bad) / sum(g for _, g, _ in bad)
    N.add("StaleGamesPct", pct(share, 0),
          "games in those seasons where crediting every positive score step misses the real final (pbp_events vs lineup_stint_games.final_*)")

    # Minutes with an unidentified player on court, per season before the last.
    un = rows(cur, "SELECT season, bad_lineup_minutes / minutes FROM lineup_stint_seasons WHERE season < %s ORDER BY 1", (s1,))
    N.add("UnidMinutesPctMin", pct(min(v for _, v in un), 0),
          "min over seasons before the last of lineup_stint_seasons.bad_lineup_minutes / minutes")
    N.add("UnidMinutesPctMax", pct(max(v for _, v in un), 0), "max of the same")
    names = rows(cur, """SELECT g.season, count(DISTINCT e.player_name) FROM pbp_events e
                         JOIN lineup_stint_games g ON g.game_id = e.game_id
                         WHERE e.person_id IS NULL AND coalesce(e.player_name, '') <> '' AND g.season < %s
                         GROUP BY 1""", (s1,))
    N.add("UnidNamesMin", integer(min(v for _, v in names)),
          "min over seasons before the last of distinct pbp_events.player_name with no person_id")
    N.add("UnidNamesMax", integer(max(v for _, v in names)), "max of the same")

    # Parsed season totals against NBA.com's (player_season_stats per-game x games).
    ratios = rows(cur, """SELECT s.season, SUM(l.pts)::float / SUM(s.pts * s.gp), SUM(l.oreb + l.dreb)::float / SUM(s.reb * s.gp),
                  SUM(l.ast)::float / SUM(s.ast * s.gp), SUM(l.stl)::float / SUM(s.stl * s.gp),
                  SUM(l.fg3a)::float / SUM(s.fg3a * s.gp)
           FROM (SELECT player_id, season, SUM(pts) pts, SUM(oreb) oreb, SUM(dreb) dreb, SUM(ast) ast, SUM(stl) stl,
                        SUM(fg3a) fg3a FROM player_game_lines GROUP BY 1, 2) l
           JOIN player_season_stats s USING (player_id, season) GROUP BY 1""")
    N.add("LinesMaxDevPct", ceil_pct(max(abs(r - 1) for row in ratios for r in row[1:]), 2),
          "max |ratio - 1| over seasons of player_game_lines season totals (pts, reb, ast, stl, 3PA) / "
          "player_season_stats per-game x gp, rounded up (test_known_facts)")
    three = rows(cur, """WITH l AS (SELECT player_id, season, SUM(fg3a) f FROM player_game_lines GROUP BY 1, 2)
                         SELECT s.season, SUM(l.f)::float / SUM(s.fg3a * s.gp) FROM l
                         JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= %s GROUP BY 1""",
                 (THREE_PA_MIN_GAMES,))
    N.add("ThreePaMaxDevPct", ceil_pct(max(abs(r - 1) for _, r in three), 2),
          f"max |ratio - 1| over seasons of 3PA, players with {THREE_PA_MIN_GAMES}+ games, rounded up (test_smoke)")
    N.add("ThreePaMinGames", integer(THREE_PA_MIN_GAMES), "the floor of that check (paper_numbers.THREE_PA_MIN_GAMES)")
    ast = rows(cur, """SELECT COALESCE(a.n, 0), COALESCE(l.n, 0)
           FROM (SELECT season, team_abbreviation, passer_id pid, SUM(ast) n FROM assist_pairs GROUP BY 1, 2, 3) a
           FULL JOIN (SELECT season, team_abbreviation, player_id pid, SUM(ast) n FROM player_game_lines
                      WHERE game_id IN (SELECT 'espn_' || espn_id FROM game_scores) GROUP BY 1, 2, 3) l
                USING (season, team_abbreviation, pid)
           WHERE a.n IS DISTINCT FROM l.n AND COALESCE(l.n, 0) > 0""")
    N.claim(all(l - a == 1 for a, l in ast), "every assist-network mismatch is exactly one assist (Data section)")
    N.add("AstOffByOne", word(len(ast)),
          "player-team-seasons where assist_pairs' assists differ from player_game_lines' (NBA Cup finals excluded), each by one")

    N.start("Platform")
    t = one(cur, """SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'
                    AND table_type = 'BASE TABLE' AND table_name NOT LIKE 'paper\\_%%'""")[0]
    N.add("TableCount", integer(t), "base tables in schema public, excluding the paper's own paper_* tables")


def rapm(cur, N):
    N.start("RAPM (Methods, Table: rapm, abstract)")
    single = rows(cur, "SELECT DISTINCT lambda FROM rapm_fits WHERE version = 'single'")
    N.claim(len(single) == 1, "Methods: 'it lands at 3,000 in every season' (one lambda for every single-season fit)")
    N.add("RapmLambda", integer(single[0][0]), "rapm_fits.lambda, version single (the same in every season)")
    folds = rows(cur, "SELECT DISTINCT cv_folds FROM rapm_fits")
    N.claim(len(folds) == 1, "one cv_folds value for every RAPM fit")
    N.add("RapmFolds", word(folds[0][0]), "rapm_fits.cv_folds")
    boots = rows(cur, "SELECT DISTINCT bootstraps FROM rapm_fits")
    N.claim(len(boots) == 1, "one bootstrap count for every RAPM fit")
    N.add("RapmBootstraps", integer(boots[0][0]), "rapm_fits.bootstraps")
    window = rows(cur, "SELECT DISTINCT seasons_to - seasons_from + 1 FROM rapm_fits WHERE version = 'multi'")
    N.claim(len(window) == 1, "one window length for the multi-season RAPM")
    N.add("RapmMultiSeasons", word(window[0][0]), "rapm_fits.seasons_to - seasons_from + 1, version multi")

    # The platform's own validation numbers (rapm_validation: within-season cross-validation, next season with
    # the intercept and home term refitted, year to year) are no longer in the paper: the protocol section below
    # scores the same models under one split (paper_eval_metrics).


def shot_quality(cur, N):
    N.start("Expected FG% and shot-making, the platform's cross-fit (Methods, Results, abstract)")
    # The platform's holdout table (shot_making_validation scope 'holdout', 2025-26 chosen after the fact) is no
    # longer in the paper: the protocol section scores the four families on the validation season and then once on
    # the test season. The cross-fit numbers below are the platform's deployed values (all seasons).
    cf_notes, bins = one(cur, "SELECT notes, reliability_bins FROM shot_making_validation WHERE scope = 'crossfit' AND model_type = 'hgb'")
    cf_notes, bins = as_json(cf_notes), as_json(bins)
    N.add("XfgFolds", word(cf_notes["folds"]), "shot_making_validation.notes.folds, scope crossfit")
    gaps = [abs(b["observed_rate"] - b["predicted_mean"]) for b in bins]
    big = [abs(b["observed_rate"] - b["predicted_mean"]) for b in bins if b["n"] > CALIB_BIG_BIN]
    N.add("XfgCalibBins", word(len(bins)), "len(reliability_bins), scope crossfit")
    N.add("XfgCalibMaxGap", dec(max(gaps), 3, ROUND_CEILING),
          "max |observed - predicted| over the cross-fit reliability bins, rounded up")
    N.add("XfgCalibBigBins", word(len(big)), f"cross-fit bins holding over {CALIB_BIG_BIN:,} shots")
    N.add("XfgCalibBigThreshold", integer(CALIB_BIG_BIN), "paper_numbers.CALIB_BIG_BIN")
    N.add("XfgCalibBigMaxGap", dec(max(big), 3, ROUND_CEILING), "max gap over those bins, rounded up")

    yty = cf_notes["year_to_year"]
    lo, hi = sorted(yty, key=int)[:2]
    N.add("XfgYtyMinFga", integer(lo), "shot_making_validation.notes.year_to_year: lower attempts floor")
    N.add("XfgYtyPairs", integer(yty[lo]["n_pairs"]), f"notes.year_to_year['{lo}'].n_pairs")
    N.add("XfgYtyQuality", dec(yty[lo]["quality"], 2), f"notes.year_to_year['{lo}'].quality (expected FG%)")
    N.add("XfgYtyMaking", dec(yty[lo]["shot_making"], 2), f"notes.year_to_year['{lo}'].shot_making")
    N.add("XfgYtyHighMinFga", integer(hi), "notes.year_to_year: higher attempts floor")
    N.add("XfgYtyHighMaking", dec(yty[hi]["shot_making"], 2), f"notes.year_to_year['{hi}'].shot_making")
    N.claim(yty[lo]["quality"] > yty[lo]["shot_making"], "abstract: shot-making less persistent than shot quality")

    z, n = one(cur, """SELECT sum((shot_distance = 0)::int), count(*) FROM player_shots
                       WHERE game_id LIKE '002%%' AND shot_type LIKE '3%%'""")
    N.add("ZeroDistThreesPct", pct(z / n, 0),
          f"regular-season three-point attempts in player_shots with shot_distance = 0 ({z:,} of {n:,})")


def pregame_and_sim(cur, N):
    N.start("Pre-game odds and season simulation, platform facts (Methods)")
    N.add("SimPlayInFirstSeason", season(season_sim_lib.PLAY_IN_FROM),
          "api/season_sim_lib.PLAY_IN_FROM (the simulator plays the play-in from this season; a rule, not a table)")
    fits = {f: (b, ch) for f, b, ch in rows(cur, "SELECT form, beta, chosen FROM pregame_model_fit")}
    chosen = [f for f, v in fits.items() if v[1]]
    N.claim(len(chosen) == 1, "one chosen pre-game form in the platform's table")
    N.add("PregameCoefs", word(len(as_json(fits[chosen[0]][0]))), f"number of coefficients of the platform's chosen form ({chosen[0]}), pregame_model_fit.beta")
    # The platform's leave-one-season-out fit (pregame_model_fit, all seasons) and its simulator backtest
    # (season_sim_backtest_summary, 480 team-seasons) are no longer in the paper; the protocol section scores
    # both under the tune / validate / test split (paper_eval_metrics). season_sim_params.carry is fitted on all
    # seasons, test included, so the paper quotes the protocol's test-phase carry (EvCarry) instead.


def luck(cur, N):
    N.start("Luck and persistence (Methods, Results)")
    exp, n_fit = one(cur, "SELECT param, n FROM luck_model_fit WHERE chosen")
    N.add("PythagExponent", dec(exp, 1), "luck_model_fit.param, chosen (pythagorean)")
    N.add("LuckFitTeamSeasons", integer(n_fit), "luck_model_fit.n, chosen")
    v = {m: (val, n, lo, hi) for m, val, n, lo, hi in rows(cur, "SELECT metric, value, n, lo, hi FROM luck_schedule_validation")}
    N.add("LuckPairs", integer(v["luck_next_luck_r"][1]), "luck_schedule_validation.n, luck_next_luck_r")
    for metric, m in (("luck_next_luck_r", "LuckNextR"), ("mov_next_mov_r", "MovNextR"),
                      ("next_winpct_coef_luck", "LuckCoef"), ("next_winpct_coef_exp", "ExpCoef")):
        val, _, lo, hi = v[metric]
        N.add(m, dec(val, 2), f"luck_schedule_validation.value, {metric}")
        N.add(m + "Lo", dec(lo, 2), f"luck_schedule_validation.lo, {metric} (franchise-clustered 95%)")
        N.add(m + "Hi", dec(hi, 2), f"luck_schedule_validation.hi, {metric}")
    N.add("CloseThreeNextR", dec(v["close3_next_close3_r"][0], 2), "luck_schedule_validation.value, close3_next_close3_r")
    N.claim(v["next_winpct_coef_luck"][2] < 0 < v["next_winpct_coef_luck"][3], "Results: luck coefficient's interval includes zero")
    matched = v["bref_matched"][0]
    N.claim(v["bref_wins_mismatch"][0] == 0, "Results: totals match Basketball-Reference for every matched team-season")
    N.add("BrefMatched", integer(matched), "luck_schedule_validation.value, bref_matched (0 win mismatches)")
    srs = v["bref_srs_r"][0]
    N.claim(srs > 0.9999, "Results: 'SRS correlation above 0.9999'")
    N.add("BrefSrsRFloor", dec(srs, 4, ROUND_FLOOR), "luck_schedule_validation.value, bref_srs_r, rounded down")


def awards(cur, N):
    N.start("Award models (Results)")
    data = rows(cur, """SELECT award, model_type, n_seasons_evaluated, top1_accuracy, roc_auc
                        FROM model_backtest_summary ORDER BY award, top1_accuracy DESC, model_type""")
    by = {}
    for award, model, n, top1, auc in data:
        by.setdefault(award, []).append((model, int(n), round(top1 * n), auc))
    for award, a in (("MVP", "Mvp"), ("ROY", "Roy")):
        best = by[award][0]
        N.claim(best[2] > by[award][1][2], f"{award}: a single best model on top-1 accuracy")
        N.add(f"{a}BestModel", MODEL_LABELS[best[0]], f"model_backtest_summary, {award}, highest top1_accuracy")
        N.add(f"{a}BestHits", integer(best[2]), f"top1_accuracy x n_seasons_evaluated, {award}, {best[0]}")
        N.add(f"{a}Seasons", integer(best[1]), f"n_seasons_evaluated, {award}")
    mvp = {m: (n, hits, auc) for m, n, hits, auc in by["MVP"]}
    N.claim(by["MVP"][0][0] != "logreg", "Results: 'logistic regression 7 of 15' is the MVP runner-up, not the best")
    N.add("MvpLogregHits", integer(mvp["logreg"][1]), "model_backtest_summary, MVP, logreg: winners picked")
    N.claim(by["ROY"][0][0] == "logreg", "Results: 'logistic regression picks the ROY'")
    N.add("MvpAucMax", dec(max(v[2] for v in mvp.values()), 3), "max roc_auc over MVP models, model_backtest_summary")


def protocol(cur, N):
    """Round 5 step 2: one evaluation protocol for every model (scripts/paper_eval.py's tables)."""
    N.start("Evaluation protocol (Methods: protocol; Results: Tables rapm, shot, sim; abstract)")
    ch = {(t, m, p): (v, on, as_json(c) if c is not None else None, note)
          for t, m, p, v, on, c, note in rows(cur, "SELECT task, model, parameter, value, chosen_on, candidates, note FROM paper_eval_choices")}
    M = {}
    for task, phase, model, variant, seasons, metric, value, n in rows(
            cur, "SELECT task, phase, model, variant, seasons, metric, value, n FROM paper_eval_metrics"):
        M[(task, phase, model, variant, seasons, metric)] = (value, n)

    def pick(task, phase, model, metric, variant=""):
        """(value, n, seasons) of the phase's summary row: the pooled row for tune, the one season otherwise."""
        c = [(se, v, n) for (t, ph, m, va, se, me), (v, n) in M.items() if (t, ph, m, va, me) == (task, phase, model, variant, metric)]
        if phase == "tune" and len(c) > 1:
            c = [x for x in c if " to " in x[0]]
        assert len(c) == 1, (task, phase, model, metric, variant, c)
        se, v, n = c[0]
        return v, n, se

    tune_first, tune_last = ch[("protocol", "all", "tune_seasons")][0].split(" to ")
    val, test = ch[("protocol", "all", "validate_season")][0], ch[("protocol", "all", "test_season")][0]
    N.add("EvTuneFirst", season(tune_first), "paper_eval_choices protocol.tune_seasons (first)")
    N.add("EvTuneLast", season(tune_last), "paper_eval_choices protocol.tune_seasons (last)")
    N.add("EvValidate", season(val), "paper_eval_choices protocol.validate_season")
    N.add("EvTest", season(test), "paper_eval_choices protocol.test_season")
    src_pairs = pick("impact_next", "tune", "bpm", "game_rmse")[2]
    N.add("EvNextTunePairs", word(len(range(int(src_pairs[:4]), int(src_pairs[-7:-3]) + 1))), "number of tune next-season pairs (scored seasons in the pooled row)")
    pre_span = pick("pregame", "tune", "baseline", "log_loss")[2]
    N.add("EvPregameHistoryFirst", season(pre_span.split(" to ")[0]), "paper_eval_metrics pregame tune pooled seasons (first)")
    xfg_first = one(cur, "SELECT min(season) FROM player_shots WHERE game_id LIKE '002%%'")[0]
    N.add("EvXfgHistoryFirst", season(xfg_first), "min(season) of regular-season player_shots (the shot model's first training season)")

    # -- impact: hyperparameters and the three tests -------------------------------
    N.add("EvLambdaSingle", integer(float(ch[("impact", "rapm_single", "lambda")][0])), "paper_eval_choices impact.rapm_single.lambda (tune)")
    N.add("EvLambdaMulti", integer(float(ch[("impact", "rapm_multi", "lambda")][0])), "paper_eval_choices impact.rapm_multi.lambda (one tune pair)")
    N.add("EvPriorScale", dec(ch[("impact", "rapm_prior", "prior_scale")][0], 2), "paper_eval_choices impact.rapm_prior.prior_scale (tune)")
    free = as_json(ch[("impact", "rapm_prior", "free_minimum")][0])
    N.add("EvPriorFreeLambda", integer(free["lambda"]), "paper_eval_choices impact.rapm_prior.free_minimum: lambda of the whole-grid minimum")
    N.add("EvPriorFreeScale", dec(free["prior_scale"], 2), "same: prior scale of the whole-grid minimum")
    N.add("EvBpmScale", dec(ch[("impact", "bpm_scaled", "scale")][0], 2), "paper_eval_choices impact.bpm_scaled.scale (tune)")
    N.add("EvOnoffScale", dec(ch[("impact", "onoff_scaled", "scale")][0], 2), "paper_eval_choices impact.onoff_scaled.scale (tune)")
    lam_app = one(cur, "SELECT DISTINCT lambda FROM rapm_fits WHERE version = 'single'")[0]
    N.claim(float(ch[("impact", "rapm_single", "lambda")][0]) == lam_app,
            "Methods: the protocol's lambda for one-season RAPM equals the platform's cross-validated one")

    models = {"bpm": "Bpm", "bpm_scaled": "BpmScaled", "rapm_prior": "Prior", "rapm_multi": "Multi", "rapm_single": "Single",
              "zero": "Zero", "onoff": "Onoff", "onoff_scaled": "OnoffScaled"}
    nx = {}
    for model, m in models.items():
        for phase, ph in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
            v, n, se = pick("impact_next", phase, model, "game_rmse")
            nx[(phase, model)] = v
            N.add(f"EvNext{ph}{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_next {phase} {model} game_rmse ({se}, n {n})")
        v, n, se = pick("impact_next", "test", model, "game_corr")
        N.add(f"EvNextTest{m}R", dec(v, 2), f"paper_eval_metrics impact_next test {model} game_corr ({se})")
        v, n, se = pick("impact_heldout", "test", model, "game_rmse")
        N.add(f"EvHeldTest{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_heldout test {model} game_rmse ({se}, n {n})")
    N.add("EvNextTuneGames", integer(pick("impact_next", "tune", "bpm", "game_rmse")[1]), "n of the pooled tune next-season rows (games)")
    N.add("EvNextTestGames", integer(pick("impact_next", "test", "bpm", "game_rmse")[1]), "n of the test next-season rows (games)")
    N.add("EvHeldTestGames", integer(pick("impact_heldout", "test", "bpm", "game_rmse")[1]), "n of the test held-out rows (games)")
    N.claim(all(nx[(p, "onoff")] > nx[(p, "zero")] for p in ("tune", "validate", "test")),
            "Results: on/off as published is worse than predicting zero in every phase")
    N.claim(all(nx[(p, "rapm_single")] > nx[(p, "rapm_prior")] for p in ("tune", "validate", "test")),
            "Results: one-season RAPM is worse than RAPM + prior throughout")
    N.claim(nx[("test", "bpm")] == min(v for (p, m), v in nx.items() if p == "test"),
            "Results: on the test season BPM is the best estimator of all")
    N.claim(nx[("tune", "rapm_prior")] < nx[("tune", "bpm")] and nx[("validate", "rapm_prior")] < nx[("validate", "bpm")]
            and nx[("test", "rapm_prior")] > nx[("test", "bpm")],
            "Results/abstract: RAPM + prior edges BPM on the tune and validation seasons but not on the test season")
    N.claim(nx[("test", "onoff_scaled")] > nx[("test", "zero")], "Results: even rescaled, on/off does not beat zero on the test season")
    N.claim(nx[("test", "bpm_scaled")] > nx[("test", "bpm")] and nx[("validate", "bpm_scaled")] < nx[("validate", "bpm")],
            "Results: the tuned BPM scale helps on the validation season and not on the test season")
    yty = {}
    for model, m in (("bpm", "Bpm"), ("rapm_prior", "Prior"), ("rapm_single", "Single"), ("onoff", "Onoff")):
        for phase in ("tune", "test"):
            v, n, se = pick("impact_reliability", phase, model, "corr")
            yty[(phase, model)] = v
            if phase == "test":
                N.add(f"EvYtyTest{m}", dec(v, 2), f"paper_eval_metrics impact_reliability test {model} corr ({se}, n {n})")
    N.add("EvYtyTestPlayers", integer(pick("impact_reliability", "test", "bpm", "corr")[1]), "players qualified in both test-pair seasons")
    N.claim(all(yty[(p, "bpm")] > yty[(p, "rapm_prior")] > yty[(p, "rapm_single")] > yty[(p, "onoff")] for p in ("tune", "test")),
            "Results: year-to-year order BPM > RAPM+prior > one-season RAPM > on/off holds in tune and test")

    # -- expected FG% -----------------------------------------------------------------
    cfg = ch[("xfg", "hgb", "config")]
    chosen = next(c for c in cfg[2] if c["config"] == cfg[0])
    N.add("EvXfgConfigs", word(len(cfg[2])), "number of boosting configurations compared on the tune season")
    N.add("EvXfgConfigLeaves", integer(chosen["params"]["max_leaf_nodes"]), "paper_eval_choices xfg.hgb.config: max_leaf_nodes of the chosen configuration")
    N.add("EvXfgConfigMinLeaf", integer(chosen["params"]["min_samples_leaf"]), "same: min_samples_leaf")
    N.add("EvXfgConfigLr", dec(chosen["params"]["learning_rate"], 2), "same: learning_rate")
    lls = sorted(c["log_loss"] for c in cfg[2])
    N.add("EvXfgConfigSpread", dec(lls[-1] - lls[0], 4, ROUND_CEILING), "largest minus smallest tune log loss over the configurations, rounded up")
    N.add("EvXfgTuneSeason", season(cfg[1]), "paper_eval_choices xfg.hgb.config chosen_on")
    N.add("EvXfgTuneTrainLast", season(int(cfg[1][:4])), "the season before it (last training season of the tune fits)")
    fam = ch[("xfg", "family", "model")]
    N.claim(fam[0] == "hgb", "Results: the validation season picks gradient boosting")
    xm = {"constant": "Const", "zone": "Zone", "logreg": "Logreg", "hgb": "Hgb"}
    xv = {}
    for model, m in xm.items():
        for phase, ph in (("validate", "Val"), ("test", "Test")):
            var = next(va for (t, p_, mo, va, se, me) in M if (t, p_, mo, me) == ("xfg", phase, model, "log_loss"))
            for metric, mm, d in (("log_loss", "LogLoss", 4), ("brier", "Brier", 4), ("roc_auc", "Auc", 3)):
                v, n, se = pick("xfg", phase, model, metric, var)
                xv[(phase, model, metric)] = v
                if phase == "test" or metric == "log_loss":     # Table shot: validation log loss, test all three
                    N.add(f"EvXfg{ph}{m}{mm}", dec(v, d), f"paper_eval_metrics xfg {phase} {model} {metric} ({se}, n {n}, variant '{var}')")
    N.add("EvXfgValShots", integer(pick("xfg", "validate", "constant", "log_loss")[1]), "shots scored in the validation season")
    N.add("EvXfgTestShots", integer(pick("xfg", "test", "constant", "log_loss")[1]), "shots scored in the test season")
    for phase in ("validate", "test"):
        N.claim(all(xv[(phase, "hgb", "log_loss")] < xv[(phase, o, "log_loss")] and xv[(phase, "hgb", "brier")] < xv[(phase, o, "brier")]
                    and xv[(phase, "hgb", "roc_auc")] > xv[(phase, o, "roc_auc")] for o in ("constant", "zone", "logreg")),
                f"Table shot: gradient boosting best on all three metrics ({phase})")
    ry = {}
    for phase, ph in (("validate", "Val"), ("test", "Test")):
        for model, m in (("quality", "Quality"), ("shot_making", "Making")):
            v, n, se = pick("xfg_reliability", phase, model, "corr", "fga>=200")
            ry[(phase, model)] = v
            N.add(f"EvXfgYty{ph}{m}", dec(v, 2), f"paper_eval_metrics xfg_reliability {phase} {model} corr, fga>=200 ({se}, n {n})")
        N.add(f"EvXfgYty{ph}Pairs", integer(pick("xfg_reliability", phase, "quality", "corr", "fga>=200")[1]), f"players with 200+ attempts in both seasons ({phase})")
    v, n, se = pick("xfg_reliability", "test", "shot_making", "corr", "fga>=500")
    N.add("EvXfgYtyTestMakingHigh", dec(v, 2), f"paper_eval_metrics xfg_reliability test shot_making corr, fga>=500 ({se}, n {n})")
    N.add("EvXfgYtyTestPairsHigh", integer(n), "players with 500+ attempts in both test-pair seasons")
    N.claim(all(ry[(p, "quality")] > ry[(p, "shot_making")] for p in ("validate", "test")),
            "Results: shot quality more persistent than shot-making, out of sample (validate and test)")

    # -- pre-game odds ----------------------------------------------------------------
    form = ch[("pregame", "form", "form")]
    FORM_TEXT = {"baseline": "the shrunk-ratings probit baseline", "current": "this season's ratings alone",
                 "prior": "ratings blended with last season's", "prior_rest": "the blend plus both back-to-back flags"}
    N.add("EvPregameForm", FORM_TEXT[form[0]], "paper_eval_choices pregame.form.form (chosen on the validation season)")
    N.claim("(the same)" in (form[3] or ""), "Methods: leave-one-season-out within the tune seasons picks the same form as the validation season")
    N.claim(form[0] == "prior_rest", "Results: the chosen form is the blend plus back-to-back flags (the \\pnEvPregame...PriorRest macros name it)")
    fm = {"baseline": "Baseline", "current": "Current", "prior": "Prior", "prior_rest": "PriorRest"}
    pg = {}
    for f, m in fm.items():
        for phase, ph in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
            v, n, se = pick("pregame", phase, f, "log_loss")
            pg[(phase, f)] = v
            if f != "prior":     # the blend without rest flags is not named in the text
                N.add(f"EvPregame{ph}{m}LogLoss", dec(v, 4), f"paper_eval_metrics pregame {phase} {f} log_loss ({se}, n {n})")
    for phase, ph in (("tune", "Tune"), ("test", "Test")):
        N.add(f"EvPregame{ph}Games", integer(pick("pregame", phase, form[0], "log_loss")[1]), f"games scored in the {phase} phase")
    N.add("EvPregameTestBrier", dec(pick("pregame", "test", form[0], "brier")[0], 4), "paper_eval_metrics pregame test, chosen form, brier")
    N.add("EvPregameTestFavWinPct", pct(pick("pregame", "test", form[0], "favourite_win_rate")[0], 1), "same, favourite_win_rate")
    N.claim(pg[("tune", form[0])] < pg[("tune", "current")] < pg[("tune", "baseline")] and pg[("validate", form[0])] < pg[("validate", "current")],
            "Results: chosen form < current-only < baseline on the tune seasons and the validation season")
    N.claim(pg[("test", "current")] < pg[("test", form[0])], "Results: on the test season this season's ratings alone edge the chosen form")
    N.add("EvCarry", dec(ch[("pregame", "constants_test", "carry")][0], 2), "paper_eval_choices pregame.constants_test.carry (fitted before the test season)")

    # -- season simulator -------------------------------------------------------------
    sm = {("sim_playoffs", "brier"): ("Brier", 4), ("sim_playoffs", "log_loss"): ("LogLoss", 3), ("sim_wins", "mae"): ("Mae", 2),
          ("sim_wins", "rmse"): ("Rmse", 2), ("sim_wins", "cover80"): ("Cover", 3)}
    sv = {}
    for (task, metric), (mm, d) in sm.items():
        for method, me in (("model", "Model"), ("record", "Record")):
            for phase, ph in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
                v, n, se = pick(task, phase, method, metric, "halfway")
                sv[(phase, method, metric)] = v
                if phase != "validate" or metric in ("brier", "mae"):     # the text quotes the validation season's Brier and MAE
                    N.add(f"EvSim{ph}{me}{mm}", dec(v, d), f"paper_eval_metrics {task} {phase} {method} {metric}, halfway ({se}, n {n})")
    for phase, ph in (("tune", "Tune"), ("test", "Test")):
        N.add(f"EvSim{ph}Teams", integer(pick("sim_playoffs", phase, "model", "brier", "halfway")[1]), f"team-seasons at the halfway checkpoint ({phase})")
    for method, me in (("model", "Model"), ("record", "Record")):
        N.add(f"EvSimTest{me}CoverPct", pct(sv[("test", method, "cover80")], 1), f"sim_wins test {method} cover80 as a percentage")
    N.claim(all(sv[("tune", "model", k)] < sv[("tune", "record", k)] for k in ("brier", "log_loss", "mae", "rmse")),
            "Results: on the tune seasons the simulator beats the record-only baseline on Brier, log loss, MAE and RMSE")
    N.claim(sv[("tune", "record", "brier")] - sv[("tune", "model", "brier")] < 0.002, "Results: 'narrowly on the playoff Brier score' (tune)")
    N.claim(all(sv[p, "record", k] < sv[p, "model", k] for p in ("validate", "test") for k in ("brier", "log_loss", "mae", "rmse")),
            "Results: on the validation and test seasons the record-only baseline beats the simulator on Brier, log loss, MAE and RMSE")
    N.claim(all(abs(sv[p, "model", "cover80"] - 0.8) < abs(sv[p, "record", "cover80"] - 0.8) for p in ("tune", "validate", "test")),
            "Results: the simulator's 80% range is closer to nominal in every phase")


def tests(cur, N):
    """Round 5 step 3: intervals and paired tests on the protocol's comparisons (scripts/paper_tests.py's table)."""
    N.start("Significance tests and intervals (Methods: protocol; Results; Table: tests; abstract) -- paper_eval_tests")
    cols = ("task", "phase", "metric", "model_a", "model_b", "variant", "seasons", "n", "n_clusters", "value_a", "value_b",
            "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_stat", "dm_p", "resamples")
    T = {}
    for r in rows(cur, f"SELECT {', '.join(cols)} FROM paper_eval_tests"):
        r = dict(zip(cols, r))
        T[(r["task"], r["phase"], r["metric"], r["model_a"], r["model_b"], r["variant"])] = r
    res = {r["resamples"] for r in T.values()}
    N.claim(len(res) == 1, "Methods: one resample count for every test")
    N.add("EvResamples", integer(res.pop()), "paper_eval_tests.resamples (bootstrap resamples and sign flips)")
    src = "paper_eval_tests"

    used = []

    def diff(name, task, phase, metric, a, b, d, variant="", dm=False, scale=1, lo_hi=True, p=True, cell=False):
        r = T[(task, phase, metric, a, b, variant)]
        used.append(r)
        where = f"{src} {task} {phase} {metric} {a} - {b}" + (f", {variant}" if variant else "") + f" ({r['seasons']}, n {r['n']}, {r['n_clusters']} clusters)"
        N.add(name, dec(r["diff"] * scale, d), where + ": diff")
        if lo_hi:
            N.add(name + "Lo", dec(r["ci_lo"] * scale, d), where + ": ci_lo (paired cluster bootstrap, 2.5th percentile)")
            N.add(name + "Hi", dec(r["ci_hi"] * scale, d), where + ": ci_hi (97.5th percentile)")
        if p:
            N.add(name + "P", pval(r["p_boot"]), where + ": p_boot (two-sided bootstrap p)")
        if dm:
            N.add(name + "DmP", pval(r["dm_p"]), where + ": dm_p (Diebold-Mariano, Newey-West variance, normal)")
        if cell:      # Table tests prints the value alone
            N.add(name + "Pv", pcell(r["p_boot"]), where + ": p_boot as a table cell")
            if dm:
                N.add(name + "DmPv", pcell(r["dm_p"]), where + ": dm_p as a table cell")
        return r

    def interval(name, task, phase, metric, model, d, variant="", scale=1):
        r = T[(task, phase, metric, model, "", variant)]
        where = f"{src} {task} {phase} {metric} {model}" + (f", {variant}" if variant else "") + f" ({r['seasons']}, n {r['n']}, {r['n_clusters']} clusters)"
        N.add(name + "Lo", dec(r["ci_lo"] * scale, d), where + ": ci_lo (cluster bootstrap, 2.5th percentile)")
        N.add(name + "Hi", dec(r["ci_hi"] * scale, d), where + ": ci_hi (97.5th percentile)")
        return r

    def excludes_zero(r):
        return r["ci_lo"] > 0 or r["ci_hi"] < 0

    # -- impact: next-season game RMSE ------------------------------------------------
    pb = {ph: diff(f"EvDNext{P}PriorBpmRmse", "impact_next", ph, "game_rmse", "rapm_prior", "bpm", 2, dm=True, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    N.claim(pb["validate"]["diff"] < 0 and excludes_zero(pb["validate"]),
            "Results/abstract: RAPM + prior is ahead of BPM on the validation season by more than its interval")
    N.claim(pb["test"]["diff"] > 0 and excludes_zero(pb["test"]) and pb["test"]["dm_p"] < 0.05,
            "Results/abstract: BPM is ahead of RAPM + prior on the test season by more than its interval (bootstrap and Diebold-Mariano)")
    N.claim(pb["tune"]["diff"] < 0 and excludes_zero(pb["tune"]),
            "Results: RAPM + prior is ahead of BPM on the tune pairs by more than its interval")
    sp = {ph: diff(f"EvDNext{P}SinglePriorRmse", "impact_next", ph, "game_rmse", "rapm_single", "rapm_prior", 2, lo_hi=(ph == "test"), p=(ph == "test"), dm=(ph == "test"), cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    N.claim(all(r["diff"] > 0 and excludes_zero(r) for r in sp.values()),
            "Results: one-season RAPM is worse than the prior version in every phase by more than its interval")
    diff("EvDNextTestSingleBpmRmse", "impact_next", "test", "game_rmse", "rapm_single", "bpm", 2, dm=True)
    mb = diff("EvDNextTestMultiBpmRmse", "impact_next", "test", "game_rmse", "rapm_multi", "bpm", 2, dm=True, cell=True)
    N.claim(not excludes_zero(mb), "Results: the three-season RAPM is indistinguishable from BPM on the test season")
    mp = diff("EvDNextTestMultiPriorRmse", "impact_next", "test", "game_rmse", "rapm_multi", "rapm_prior", 2)
    N.claim(mp["diff"] < 0 and excludes_zero(mp), "Results: the three-season window is ahead of the one-season prior version on the test season")
    bs = {ph: diff(f"EvDNext{P}BpmScaledBpmRmse", "impact_next", ph, "game_rmse", "bpm_scaled", "bpm", 2)
          for ph, P in (("validate", "Val"), ("test", "Test"))}
    N.claim(not excludes_zero(bs["validate"]) and not excludes_zero(bs["test"]),
            "Results: the tuned BPM multiplier's gain on the validation season and its loss on the test season are both inside their intervals")
    oz = {ph: diff(f"EvDNext{P}OnoffZeroRmse", "impact_next", ph, "game_rmse", "onoff", "zero", 2, lo_hi=(ph == "test"), p=(ph == "test"), dm=(ph == "test"), cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    N.claim(all(r["diff"] > 0 and excludes_zero(r) for r in oz.values()),
            "Results: on/off as published is worse than zero in every phase by more than its interval")
    os_ = diff("EvDNextTestOnoffScaledZeroRmse", "impact_next", "test", "game_rmse", "onoff_scaled", "zero", 2, dm=True, cell=True)
    N.claim(not excludes_zero(os_), "Results: rescaled on/off is indistinguishable from zero on the test season")
    pz = diff("EvDNextTestPriorZeroRmse", "impact_next", "test", "game_rmse", "rapm_prior", "zero", 2)
    N.claim(pz["diff"] < 0 and excludes_zero(pz), "Results: every RAPM version beats zero on the test season (prior version shown)")
    hb = diff("EvDHeldTestPriorBpmRmse", "impact_heldout", "test", "game_rmse", "rapm_prior", "bpm", 2)
    N.claim(hb["diff"] > 0 and excludes_zero(hb), "Results: BPM is ahead on the test season's held-out games too")
    for model, m in (("bpm", "Bpm"), ("rapm_prior", "Prior")):
        interval(f"EvNextTest{m}Rmse", "impact_next", "test", "game_rmse", model, 2)

    # -- impact: year-to-year reliability ---------------------------------------------
    for model, m in (("bpm", "Bpm"), ("rapm_prior", "Prior"), ("rapm_single", "Single"), ("onoff", "Onoff")):
        interval(f"EvYtyTest{m}", "impact_reliability", "test", "corr", model, 2)
    yd = {k: diff(f"EvDYtyTest{n_}", "impact_reliability", "test", "corr", a, b, 2, cell=(k == "bp"))
          for k, n_, a, b in (("bp", "BpmPrior", "bpm", "rapm_prior"), ("ps", "PriorSingle", "rapm_prior", "rapm_single"),
                              ("so", "SingleOnoff", "rapm_single", "onoff"))}
    N.claim(all(r["diff"] > 0 and excludes_zero(r) for r in yd.values()),
            "Results: each step of the year-to-year order (BPM > RAPM + prior > one-season RAPM > on/off) is outside its interval on the test pair")

    # -- expected FG% -----------------------------------------------------------------
    hl = {ph: diff(f"EvDXfg{P}HgbLogregLogLoss", "xfg", ph, "log_loss", "hgb", "logreg", 4, cell=(ph == "test")) for ph, P in (("validate", "Val"), ("test", "Test"))}
    hz = diff("EvDXfgTestHgbZoneLogLoss", "xfg", "test", "log_loss", "hgb", "zone", 4)
    N.add("EvXfgTestGames", integer(hz["n_clusters"]), "paper_eval_tests xfg test: games the scored shots belong to (the bootstrap clusters)")
    N.claim(all(r["diff"] < 0 and excludes_zero(r) for r in (*hl.values(), hz)),
            "Results: gradient boosting beats the logistic regression and the zone baseline by more than its interval")
    lz = diff("EvDXfgTestLogregZoneLogLoss", "xfg", "test", "log_loss", "logreg", "zone", 4, cell=True)
    N.claim(not excludes_zero(lz), "Results: on the test season the logistic regression and the zone baseline are indistinguishable")
    hb_ = diff("EvDXfgTestHgbLogregBrier", "xfg", "test", "brier", "hgb", "logreg", 4)
    N.claim(hb_["diff"] < 0 and excludes_zero(hb_), "Results: boosting's Brier gain over the logistic regression is outside its interval")
    for ph, P in (("validate", "Val"), ("test", "Test")):
        r = T[("xfg", ph, "ece", "hgb", "", T[("xfg", ph, "log_loss", "hgb", "", next(v for (t, p_, me, mo, mb, v) in T if (t, p_, me, mo, mb) == ("xfg", ph, "log_loss", "hgb", "")))]["variant"])]
        N.add(f"EvXfg{P}Ece", dec(r["value_a"], 3), f"paper_eval_tests xfg {ph} ece hgb ({r['seasons']}, n {r['n']}, {r['n_clusters']} games): value_a")
        N.add(f"EvXfg{P}EceLo", dec(r["ci_lo"], 3), "same: ci_lo")
        N.add(f"EvXfg{P}EceHi", dec(r["ci_hi"], 3), "same: ci_hi")
        g = T[("xfg", ph, "calib_max_gap", "hgb", "", r["variant"])]
        N.add(f"EvXfg{P}MaxGap", dec(g["value_a"], 3), f"paper_eval_tests xfg {ph} calib_max_gap hgb: largest gap over bins holding >= 1% of the shots")
        N.add(f"EvXfg{P}MaxGapHi", dec(g["ci_hi"], 3), "same: ci_hi")
    for ph, P in (("validate", "Val"), ("test", "Test")):
        for model, m in (("quality", "Quality"), ("shot_making", "Making")):
            interval(f"EvXfgYty{P}{m}", "xfg_reliability", ph, "corr", model, 2, variant="fga>=200")
        r = diff(f"EvDXfgYty{P}QualityMaking", "xfg_reliability", ph, "corr", "quality", "shot_making", 2, variant="fga>=200")
        N.claim(r["diff"] > 0 and excludes_zero(r), f"Results: shot quality is more persistent than shot-making by more than its interval ({ph})")

    # -- pre-game odds ----------------------------------------------------------------
    pc = {ph: diff(f"EvDPregame{P}ChosenCurrentLogLoss", "pregame", ph, "log_loss", "prior_rest", "current", 4, dm=True, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    N.claim(pc["tune"]["diff"] < 0 and excludes_zero(pc["tune"]) and pc["tune"]["dm_p"] < 0.05,
            "Results: on the tune seasons the chosen form beats this season's ratings alone by more than its interval")
    N.claim(not excludes_zero(pc["validate"]), "Results: on the validation season the chosen form's gain over current-only is inside its interval")
    N.claim(not excludes_zero(pc["test"]) and pc["test"]["dm_p"] > 0.05, "Results: on the test season the chosen form and current-only are indistinguishable")
    pbase = {ph: diff(f"EvDPregame{P}ChosenBaselineLogLoss", "pregame", ph, "log_loss", "prior_rest", "baseline", 4, dm=(ph == "test"), cell=(ph == "test"))
             for ph, P in (("validate", "Val"), ("test", "Test"))}
    N.claim(not excludes_zero(pbase["test"]), "Results: on the test season the chosen form and the baseline are indistinguishable")
    cb = diff("EvDPregameTestCurrentBaselineLogLoss", "pregame", "test", "log_loss", "current", "baseline", 4, dm=True)
    N.claim(cb["diff"] < 0 and excludes_zero(cb), "Results: this season's ratings alone beat the baseline on the test season by more than its interval")
    pr = {ph: diff(f"EvDPregame{P}ChosenPriorLogLoss", "pregame", ph, "log_loss", "prior_rest", "prior", 4, lo_hi=(ph != "tune"), p=True)
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    N.claim(pr["tune"]["diff"] < 0 and excludes_zero(pr["tune"]) and pr["validate"]["diff"] < 0 and excludes_zero(pr["validate"]),
            "Results: the back-to-back flags help on the tune seasons and the validation season by more than their intervals")
    N.claim(not excludes_zero(pr["test"]), "Results: the back-to-back flags' contribution on the test season is inside its interval")
    interval("EvPregameTestFavWinPct", "pregame", "test", "favourite_win_rate", "prior_rest", 1, scale=100)

    # -- season simulator (halfway) -------------------------------------------------------
    sm = {"brier": ("Brier", 4, 1), "log_loss": ("LogLoss", 3, 1), "mae": ("Mae", 2, 1), "rmse": ("Rmse", 2, 1), "cover80": ("Cover", 1, 100)}
    sd = {}
    for metric, (mm, d, scale) in sm.items():
        task = "sim_playoffs" if metric in ("brier", "log_loss") else "sim_wins"
        for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
            sd[(ph, metric)] = diff(f"EvDSim{P}{mm}", task, ph, metric, "model", "record", d, variant="halfway", scale=scale,
                                    cell=(ph == "test" and metric != "rmse"))
    N.claim(all(sd[("tune", k)]["diff"] < 0 and excludes_zero(sd[("tune", k)]) for k in ("mae", "rmse")),
            "Results: on the tune seasons the simulator's win-total gain is outside its interval")
    N.claim(sd[("tune", "cover80")]["diff"] > 0 and excludes_zero(sd[("tune", "cover80")]), "Results: the simulator's coverage gain on the tune seasons is outside its interval")
    N.claim(not excludes_zero(sd[("tune", "brier")]) and not excludes_zero(sd[("tune", "log_loss")]),
            "Results: on the tune seasons the playoff Brier and log-loss differences are inside their intervals")
    N.claim(all(not excludes_zero(sd[(ph, k)]) for ph in ("validate", "test") for k in ("brier", "mae", "rmse")),
            "Results: on the validation and test seasons the baseline's Brier, MAE and RMSE gains are inside their intervals")
    N.claim(sd[("test", "log_loss")]["diff"] > 0 and excludes_zero(sd[("test", "log_loss")]),
            "Results: on the test season the baseline's playoff log-loss gain is outside its interval")
    N.claim(not excludes_zero(sd[("test", "cover80")]), "Results: the simulator's coverage gain on the test season is inside its interval")
    for method, me in (("model", "Model"), ("record", "Record")):
        interval(f"EvSimTest{me}CoverPct", "sim_wins", "test", "cover80", method, 1, variant="halfway", scale=100)
    # Results: "the gap comes mostly from teams the simulator favoured at the midpoint that then missed the playoffs"
    units = rows(cur, """SELECT m.pred, r.pred, m.actual FROM paper_eval_predictions m JOIN paper_eval_predictions r
                         ON r.task = m.task AND r.phase = m.phase AND r.variant = m.variant AND r.unit_id = m.unit_id AND r.model = 'record'
                         WHERE m.task = 'sim_playoffs' AND m.phase = 'test' AND m.model = 'model' AND m.variant = 'halfway'""")

    def ll(p, y):
        p = min(max(p, 1e-6), 1 - 1e-6)
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))

    gap = [(ll(pm, y) - ll(pr, y), pm, pr, y) for pm, pr, y in units]
    favoured_missed = sum(g for g, pm, pr, y in gap if y == 0 and pm > pr)
    N.claim(favoured_missed > 0.5 * sum(g for g, *_ in gap) > 0,
            "Results: the test-season playoff log-loss gap comes mostly from teams the simulator favoured at the midpoint that missed the playoffs")
    # Methods: the sign-flip test agrees with the bootstrap interval at the 5% level on every comparison the paper reports
    N.claim(all((r["p_perm"] < 0.05) == excludes_zero(r) for r in used if r["p_perm"] is not None),
            "Methods: the sign-flip permutation test agrees with the bootstrap interval, at the 5% level, on every comparison reported")


def xrapm(cur, N):
    """Round 5 step 4: expected-points RAPM (scripts/paper_xrapm.py's tables, the xrapm_* models of paper_eval / paper_tests)."""
    N.start("Expected-points RAPM (Methods: xrapm; Results: xrapm; Tables rapm, tests) -- paper_xrapm_*, paper_eval_*, paper_eval_tests")
    meta = {(k, se): v for k, se, v in rows(cur, "SELECT key, season, value FROM paper_xrapm_meta WHERE value IS NOT NULL")}
    seasons = sorted({se for _, se in meta if se > 0})
    N.claim(len(seasons) == 6, "Methods: expected points for every stint of the six seasons")
    N.add("XrFgaMatchedPct", pct(meta[("matched_share", 0)], 1), "paper_xrapm_meta matched_share (all seasons): tracked attempts priced by the shot chart")
    lo_s = min(seasons, key=lambda se: meta[("matched_share", se)])
    N.add("XrFgaMatchedMinPct", pct(meta[("matched_share", lo_s)], 1), f"paper_xrapm_meta matched_share, lowest season ({lo_s})")
    N.add("XrFgaMatchedMinSeason", season(lo_s), "that season")
    N.add("XrFgaMatchedOtherMinPct", pct(min(meta[("matched_share", se)] for se in seasons if se != lo_s), 1), "lowest matched share among the other seasons")
    N.add("XrFgaFallback", integer(meta[("fga_fallback", 0)]), "paper_xrapm_meta fga_fallback (all seasons): unmatched attempts priced by the fallback")
    N.add("XrFgaTrackedMillion", millions(meta[("fga", 0)], 2), "paper_xrapm_meta fga (all seasons): tracked field-goal attempts, millions")
    N.add("XrFtShrink", dec(meta[("ft_shrink_attempts", 0)], 0), "paper_xrapm_meta ft_shrink_attempts: stat_stability ft_pct stable_n (split-half reliability 0.5)")
    N.add("XrResidualAbsPts", integer(meta[("residual_abs_pts", 0)]), "paper_xrapm_meta residual_abs_pts: unpriced points (absolute), all seasons")
    N.add("XrResidualStints", integer(meta[("residual_stints", 0)]), "paper_xrapm_meta residual_stints")
    N.add("XrPtsMillion", millions(meta[("pts", 0)], 2), "paper_xrapm_meta pts: stored points of tracked stints, millions")
    dev = max(abs(meta[("xpts_over_pts", se)] - 1) for se in seasons)
    N.add("XrXptsDevMaxPct", ceil_pct(dev, 1), "largest |expected / stored points - 1| over the seasons, rounded up")
    cut = [1 - meta[("sd_xpts100", se)] / meta[("sd_pts100", se)] for se in seasons]
    N.add("XrSpreadCutMinPct", pct(min(cut), 0), "1 - sd_xpts100 / sd_pts100, smallest season (possession-weighted SDs of a side's points per 100 across tracked stints)")
    N.add("XrSpreadCutMaxPct", pct(max(cut), 0), "the same, largest season")
    N.add("XrSdPtsMin", dec(min(meta[("sd_pts100", se)] for se in seasons), 0), "paper_xrapm_meta sd_pts100, smallest season")
    N.add("XrSdPtsMax", dec(max(meta[("sd_pts100", se)] for se in seasons), 0), "the same, largest season")
    N.add("XrSdXptsMin", dec(min(meta[("sd_xpts100", se)] for se in seasons), 0), "paper_xrapm_meta sd_xpts100, smallest season")
    N.add("XrSdXptsMax", dec(max(meta[("sd_xpts100", se)] for se in seasons), 0), "the same, largest season")
    N.claim(all(meta[("sd_xpts100", se)] < meta[("sd_pts100", se)] for se in seasons), "Results: the expected-points target has less spread in every season")
    fits = {(v, se): (lam, r, sdx, sdr) for v, se, lam, r, sdx, sdr in rows(cur, "SELECT version, season, lambda, r_with_rapm, sd_xrapm, sd_rapm FROM paper_xrapm_fits")}
    lams = sorted({int(fits[("single", se)][0]) for se in seasons})
    N.add("XrCvLambdaMin", integer(lams[0]), "paper_xrapm_fits single.lambda (5-fold game-grouped CV on the expected-points target), smallest")
    N.add("XrCvLambdaMax", integer(lams[-1]), "the same, largest")
    for v, V in (("single", "Single"), ("prior", "Prior")):
        rs = [fits[(v, se)][1] for se in seasons]
        ratio = [fits[(v, se)][2] / fits[(v, se)][3] for se in seasons]
        N.add(f"XrCorr{V}Min", dec(min(rs), 2), f"paper_xrapm_fits {v}.r_with_rapm (qualified players), smallest season")
        N.add(f"XrCorr{V}Max", dec(max(rs), 2), "the same, largest season")
        N.add(f"XrSdRatio{V}Min", dec(min(ratio), 2), f"paper_xrapm_fits {v}: sd_xrapm / sd_rapm over qualified players, smallest season")
        N.add(f"XrSdRatio{V}Max", dec(max(ratio), 2), "the same, largest season")
    N.claim(all(fits[(v, se)][2] < fits[(v, se)][3] for v in ("single", "prior") for se in seasons),
            "Results: expected-points ratings are less spread out than actual-points ones in every season and version")
    missing = rows(cur, """WITH charted AS (SELECT DISTINCT game_id FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21')
                          SELECT season, count(*) FROM lineup_stint_games
                          WHERE game_ok AND nba_game_id IS NOT NULL AND nba_game_id NOT IN (SELECT game_id FROM charted) GROUP BY 1""")
    N.claim(len(missing) == 1 and missing[0][0] == lo_s, "Methods: the games missing from the shot chart are all in the season with the lowest matched share")
    N.raw["chart_missing"] = missing[0][1]
    N.add("XrChartMissingGames", word(missing[0][1]), "reconciled games (lineup_stint_games.game_ok) whose NBA game id has no player_shots row at all")
    # the rating change tracks shot-making: the expected-points target removes shooting skill, not only luck
    mk = {}
    for v in ("single", "prior"):
        b = rows(cur, """SELECT m.shot_making, p.rapm, p.xrapm FROM paper_xrapm_players p
                         JOIN player_shot_making m ON m.player_id = p.player_id AND m.season = p.season
                         WHERE p.version = %s AND p.qualified AND p.rapm IS NOT NULL AND m.fga >= 200""", (v,))
        sm, ra, xa = (list(c) for c in zip(*b))
        d = [x - r for x, r in zip(xa, ra)]
        mk[v] = (_corr(sm, d), _corr(sm, ra), _corr(sm, xa), len(b))
    N.add("XrMakingPairs", integer(mk["single"][3]), "qualified player-seasons (1,000+ possessions) with 200+ attempts in player_shot_making, both versions")
    N.add("XrMakingDeltaCorrSingle", dec(mk["single"][0], 2), "corr(shot-making, expected-points RAPM - RAPM), one-season version, over those player-seasons")
    N.add("XrMakingDeltaCorrPrior", dec(mk["prior"][0], 2), "the same, prior version")
    N.add("XrMakingCorrRapm", dec(mk["single"][1], 2), "corr(shot-making, one-season RAPM) over the same player-seasons")
    N.add("XrMakingCorrXrapm", dec(mk["single"][2], 2), "corr(shot-making, one-season expected-points RAPM) over the same player-seasons")
    N.claim(mk["single"][0] < -0.3 and mk["prior"][0] < -0.3, "Results: the drop in a player's rating tracks his shot-making (r below -0.3 in both versions)")
    N.claim(abs(mk["single"][2]) < abs(mk["single"][1]) and mk["single"][1] > 0.2,
            "Results: RAPM rewards shot-making and the expected-points version barely does")

    # -- the protocol's numbers (paper_eval) ------------------------------------------
    ch = {(t, m, p): (v, on, as_json(c) if c is not None else None, note)
          for t, m, p, v, on, c, note in rows(cur, "SELECT task, model, parameter, value, chosen_on, candidates, note FROM paper_eval_choices WHERE model LIKE 'xrapm%%'")}
    N.add("EvLambdaXsingle", integer(float(ch[("impact", "xrapm_single", "lambda")][0])), "paper_eval_choices impact.xrapm_single.lambda (tune, actual next-season margins)")
    N.add("EvPriorScaleX", dec(ch[("impact", "xrapm_prior", "prior_scale")][0], 2), "paper_eval_choices impact.xrapm_prior.prior_scale (tune)")
    note = ch[("impact", "xrapm_prior", "lambda")][3]
    m_ = re.search(r"lambda (\d+), scale ([\d.]+)", note)
    N.add("EvPriorFreeLambdaX", integer(int(m_.group(1))), "paper_eval_choices impact.xrapm_prior.lambda note: lambda of the whole-grid minimum")
    N.add("EvPriorFreeScaleX", dec(float(m_.group(2)), 2), "same: prior scale of the whole-grid minimum")
    M = {}
    for task, phase, model, variant, seasons_, metric, value, n in rows(
            cur, "SELECT task, phase, model, variant, seasons, metric, value, n FROM paper_eval_metrics WHERE task LIKE 'impact%%' AND variant = ''"):
        M[(task, phase, model, seasons_, metric)] = (value, n)

    def pick(task, phase, model, metric):
        c = [(se, v, n) for (t, ph, m, se, me), (v, n) in M.items() if (t, ph, m, me) == (task, phase, model, metric)]
        if phase == "tune" and len(c) > 1:
            c = [x for x in c if " to " in x[0]]
        assert len(c) == 1, (task, phase, model, metric, c)
        return c[0][1], c[0][2], c[0][0]

    nx, yty = {}, {}
    for model, m in (("xrapm_single", "Xsingle"), ("xrapm_prior", "Xprior"), ("rapm_single", "Single"), ("rapm_prior", "Prior"), ("bpm", "Bpm")):
        for phase, ph in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
            v, n, se = pick("impact_next", phase, model, "game_rmse")
            nx[(phase, model)] = v
            if model.startswith("x"):
                N.add(f"EvNext{ph}{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_next {phase} {model} game_rmse ({se}, n {n})")
            v, n, se = pick("impact_reliability", phase, model, "corr")
            yty[(phase, model)] = v
            if model.startswith("x"):
                N.add(f"EvYty{ph}{m}", dec(v, 2), f"paper_eval_metrics impact_reliability {phase} {model} corr ({se}, n {n})")
        if model.startswith("x"):
            v, n, se = pick("impact_next", "test", model, "game_corr")
            N.add(f"EvNextTest{m}R", dec(v, 2), f"paper_eval_metrics impact_next test {model} game_corr ({se})")
            v, n, se = pick("impact_heldout", "test", model, "game_rmse")
            N.add(f"EvHeldTest{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_heldout test {model} game_rmse ({se}, n {n})")
    N.claim(all(nx[(p, "xrapm_single")] > nx[(p, "rapm_single")] for p in ("tune", "validate", "test")),
            "Results: expected-points RAPM (one season) predicts next season's margins worse than actual-points RAPM in every phase")
    N.claim(nx[("tune", "xrapm_prior")] > nx[("tune", "rapm_prior")] and nx[("validate", "xrapm_prior")] > nx[("validate", "rapm_prior")]
            and nx[("test", "xrapm_prior")] < nx[("test", "rapm_prior")],
            "Results: with a prior, the expected-points version is behind on the tune and validation seasons and marginally ahead on the test season")
    N.claim(all(nx[(p, "xrapm_prior")] > nx[(p, "bpm")] for p in ("tune", "validate", "test")),
            "Results: expected-points RAPM with a prior never beats BPM on next-season margins")
    N.claim(all(yty[(p, "xrapm_prior")] > yty[(p, "rapm_prior")] for p in ("tune", "validate", "test")),
            "Results: with a prior, the expected-points ratings are more reliable year to year in every phase")
    N.claim(all(yty[(p, "xrapm_prior")] < yty[(p, "bpm")] for p in ("tune", "validate", "test")), "Results: BPM stays the most reliable")

    # -- the tests (paper_eval_tests) --------------------------------------------------
    cols = ("task", "phase", "metric", "model_a", "model_b", "seasons", "n", "n_clusters", "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_p")
    T = {}
    for r in rows(cur, f"SELECT {', '.join(cols)} FROM paper_eval_tests WHERE variant = '' AND (model_a LIKE 'xrapm%%' OR model_b LIKE 'xrapm%%')"):
        r = dict(zip(cols, r))
        T[(r["task"], r["phase"], r["metric"], r["model_a"], r["model_b"])] = r

    def excludes_zero(r):
        return r["ci_lo"] > 0 or r["ci_hi"] < 0

    def diff(name, task, phase, metric, a, b, d, dm=False, cell=False, lo_hi=True, p=True):
        r = T[(task, phase, metric, a, b)]
        where = f"paper_eval_tests {task} {phase} {metric} {a} - {b} ({r['seasons']}, n {r['n']}, {r['n_clusters']} clusters)"
        N.add(name, dec(r["diff"], d), where + ": diff")
        if lo_hi:
            N.add(name + "Lo", dec(r["ci_lo"], d), where + ": ci_lo")
            N.add(name + "Hi", dec(r["ci_hi"], d), where + ": ci_hi")
        if p:
            N.add(name + "P", pval(r["p_boot"]), where + ": p_boot")
        if dm:
            N.add(name + "DmP", pval(r["dm_p"]), where + ": dm_p")
        if cell:
            N.add(name + "Pv", pcell(r["p_boot"]), where + ": p_boot as a table cell")
            if dm:
                N.add(name + "DmPv", pcell(r["dm_p"]), where + ": dm_p as a table cell")
        return r

    def interval(name, task, phase, metric, model, d):
        r = T[(task, phase, metric, model, "")]
        N.add(name + "Lo", dec(r["ci_lo"], d), f"paper_eval_tests {task} {phase} {metric} {model} ({r['seasons']}): ci_lo")
        N.add(name + "Hi", dec(r["ci_hi"], d), "same: ci_hi")

    xs = {ph: diff(f"EvDNext{P}XsingleSingleRmse", "impact_next", ph, "game_rmse", "xrapm_single", "rapm_single", 2, dm=True, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    xp = {ph: diff(f"EvDNext{P}XpriorPriorRmse", "impact_next", ph, "game_rmse", "xrapm_prior", "rapm_prior", 2, dm=True, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    diff("EvDNextTestXpriorBpmRmse", "impact_next", "test", "game_rmse", "xrapm_prior", "bpm", 2, dm=True, cell=True)
    diff("EvDHeldTestXsingleSingleRmse", "impact_heldout", "test", "game_rmse", "xrapm_single", "rapm_single", 2)
    diff("EvDHeldTestXpriorPriorRmse", "impact_heldout", "test", "game_rmse", "xrapm_prior", "rapm_prior", 2)
    ys = {ph: diff(f"EvDYty{P}XsingleSingle", "impact_reliability", ph, "corr", "xrapm_single", "rapm_single", 2, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    yp = {ph: diff(f"EvDYty{P}XpriorPrior", "impact_reliability", ph, "corr", "xrapm_prior", "rapm_prior", 2, cell=(ph == "test"))
          for ph, P in (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))}
    for model, m in (("xrapm_single", "Xsingle"), ("xrapm_prior", "Xprior")):
        interval(f"EvYtyTest{m}", "impact_reliability", "test", "corr", model, 2)
        interval(f"EvNextTest{m}Rmse", "impact_next", "test", "game_rmse", model, 2)
    xb = {ph: T[("impact_next", ph, "game_rmse", "xrapm_prior", "bpm")] for ph in ("tune", "validate", "test")}
    bp = {ph: T[("impact_reliability", ph, "corr", "bpm", "xrapm_prior")] for ph in ("tune", "validate", "test")}
    hs = T[("impact_heldout", "test", "game_rmse", "xrapm_single", "rapm_single")]
    hp = T[("impact_heldout", "test", "game_rmse", "xrapm_prior", "rapm_prior")]
    for d_, what in ((xs, "one-season"), (xp, "prior")):
        N.claim(d_["tune"]["diff"] > 0 and excludes_zero(d_["tune"]) and d_["validate"]["diff"] > 0 and excludes_zero(d_["validate"]),
                f"Results: expected-points RAPM ({what}) is behind its actual-points twin on the tune pairs and the validation season by more than its interval")
        N.claim(not excludes_zero(d_["test"]), f"Results: expected-points RAPM ({what}) is indistinguishable from its actual-points twin on the test season")
    N.claim(all(r["diff"] > 0 for r in xb.values()) and not excludes_zero(xb["test"]),
            "Results: expected-points RAPM with a prior is never ahead of BPM, and level with it on the test season")
    N.claim(hs["diff"] > 0 and excludes_zero(hs) and hp["diff"] > 0 and excludes_zero(hp),
            "Results: on the test season's held-out games both expected-points versions are behind by more than their intervals")
    N.claim(all(not excludes_zero(r) for r in ys.values()), "Results: without the prior, the reliability gain is inside its interval in every phase")
    N.claim(all(r["diff"] > 0 and excludes_zero(r) for r in yp.values()), "Results: with the prior, the reliability gain is outside its interval in every phase")
    N.claim(all(r["diff"] > 0 and excludes_zero(r) for r in bp.values()), "Results: BPM is more reliable than expected-points RAPM + prior by more than its interval in every phase")
    reported = [*xs.values(), *xp.values(), xb["test"], hs, hp]
    N.claim(all((r["p_perm"] < 0.05) == excludes_zero(r) for r in reported if r["p_perm"] is not None),
            "Methods: the sign-flip permutation test agrees with the bootstrap interval at 5% on every expected-points comparison reported")


AUDIT_FORMATS = {"integer": integer, "word": word, "pct0": lambda v: pct(v, 0), "pct1": lambda v: pct(v, 1),
                 "season": lambda v: season(int(v)), "int_round": lambda v: dec(v, 0)}


def data_audit(cur, N):
    """The data-quality audit (paper_data_audit, written by scripts/paper_data_audit.py): Data quality subsection,
    Table audit (paper/tables/data_audit.tex), and the wrong-player / missed-three / tag-text counts of the Data section."""
    N.start("Data-quality audit (Section: Data quality, Table: audit; scripts/paper_data_audit.py)")
    A = {(k, s): (v, note) for k, s, v, note in rows(cur, "SELECT key, season, value, note FROM paper_data_audit")}
    for key, macro, fmt, v, note in rows(cur, """SELECT key, macro, fmt, value, note FROM paper_data_audit
                                              WHERE macro IS NOT NULL ORDER BY macro"""):
        if fmt not in AUDIT_FORMATS:
            raise ValueError(f"paper_data_audit {key}: unknown format {fmt!r}")
        N.add(macro, AUDIT_FORMATS[fmt](v), f"paper_data_audit {key}: {note}")

    def a(key, season=0):
        return A[(key, season)][0]

    # The audit must describe the tables the other sections read (a stale audit stops the run).
    games, ok = N.raw["games"]
    N.claim(a("unrec_games") == games - ok, "the audit's unreconciled games equal lineup_stint_games' (rerun paper_data_audit.py)")
    N.claim(a("unrec_cup") + a("unrec_score") + a("unrec_totals") == a("unrec_games") and a("unrec_cup") == a("cup_games"),
            "Table audit: the failed games split into score, rebound count and the Cup finals with no final")
    N.claim(all(a("score_steps_games", se) == g and a("score_steps_miss", se) == m for se, (g, m) in N.raw["stale"].items()),
            "the audit's stale-score counts equal the Data section's (rerun paper_data_audit.py)")
    xr = dict(rows(cur, "SELECT season, value FROM paper_xrapm_meta WHERE key = 'matched_share' AND season > 0"))
    N.claim(min(xr, key=xr.get) == a("chart_match_min_season"),
            "the audit's lowest chart-match season is the xRAPM section's (Methods, Expected-points RAPM)")
    N.claim(a("chart_missing_games") == N.raw["chart_missing"], "the audit's missing chart games equal the xRAPM section's")
    # Sentences of the Data quality subsection and the table.
    N.claim(a("wrong_player_left") == 0, "Table audit: the wrong-player tags are repaired (repair_espn_player_ids.find() finds none)")
    N.claim(a("twin_same_teams") + a("twin_neutral") == a("twin_games"), "Table audit: every nba_api game is an ESPN game again")
    N.claim(a("teamless_player_games") == a("teamless_lines_higher") == a("teamless_pg_in_sub_games") == a("teamless_pg_games")
            == a("teamless_games") and a("nan_team_in_sub_games") == a("nan_team_rows"),
            "Data quality: one player-game per team-less-substitution game gets extra minutes, all in those games; the 'NaN' line is one of them")
    N.claim(a("clock_signed_median") == -a("clock_median") < 0 and a("clock_espn_more_share") < 0.05,
            "Data quality: ESPN's clock runs behind the chart's (it shows less time left in all but a few per cent of shots)")
    N.claim(a("miss_threes_as_twos") > 3 * a("miss_twos_as_threes"), "Data quality: the text mostly turns missed threes into twos, not the reverse")
    N.claim(a("tag_text_teammate") / a("tag_text_other") > 0.9, "Data quality / Table audit: the other player named is nearly always a teammate")
    N.claim(a("origin_after_max") < a("origin_min") / 5, "Table audit: almost no shots sit at (0, 0) after 2009-10")
    N.claim(a("pm_point") <= a("pm_bad") and a("pm_sign") <= a("pm_bad"), "Table audit: plus-minus sub-counts are parts of the whole")
    kinds = rows(cur, "SELECT handling_kind, count(*) FROM paper_data_audit_classes GROUP BY 1")
    N.claim(sum(n for _, n in kinds) == a("classes"), "Data quality: the class count is the table's row count")
    N.claim(a("made_disagree") / a("made_matched") < 0.001, "Data quality: made shots' values agree with the chart in all but a few hundredths of a per cent")
    N.add("DqMadeDisagreePct", pct(a("made_disagree") / a("made_matched"), 2),
          "paper_data_audit made_disagree / made_matched: matched made shots whose score-step value differs from the chart's call (%)")


SECTIONS = (data_and_pipeline, rapm, shot_quality, pregame_and_sim, luck, awards, protocol, tests, xrapm, data_audit)


def build(conn):
    """All macros as the text of numbers.tex; raises if a claim the paper makes no longer holds."""
    cur = conn.cursor()
    N = Numbers()
    for section in SECTIONS:
        section(cur, N)
    cur.close()
    if N.failed:
        raise SystemExit("A sentence in the paper no longer matches the data; rewrite it before regenerating:\n  - "
                         + "\n  - ".join(N.failed))
    return N.render()


def defined_macros(text):
    return set(re.findall(r"\\newcommand\{\\(pn[A-Za-z]+)\}", text))


COMMENT_RE = re.compile(r"(?<!\\)%.*")  # a TeX comment: an unescaped % to the end of the line (\% is text)


def strip_comments(tex):
    return "\n".join(COMMENT_RE.sub("", line) for line in tex.splitlines())


def used_macros(paper_text):
    return set(re.findall(r"\\(pn[A-Z][A-Za-z]*)", strip_comments(paper_text)))


INPUT_RE = re.compile(r"\\input\{(tables/[^}]+)\}")


def check_paper(numbers_text, paper_path):
    """(undefined macros the paper or a table it inputs uses, macros defined but unused)."""
    with open(paper_path) as f:
        paper = f.read()
    texts = [paper]
    for rel in INPUT_RE.findall(strip_comments(paper)):
        path = os.path.join(os.path.dirname(os.path.abspath(paper_path)), rel if rel.endswith(".tex") else rel + ".tex")
        with open(path) as f:
            texts.append(f.read())
    defined, used = defined_macros(numbers_text), set().union(*(used_macros(t) for t in texts))
    return sorted(used - defined), sorted(defined - used)


def connect():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    return conn


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check", action="store_true", help="also check that every \\pn macro the paper uses is defined")
    ap.add_argument("--paper", default=DEFAULT_PAPER)
    args = ap.parse_args()
    conn = connect()
    try:
        text = build(conn)
    finally:
        conn.close()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text)
    print(f"wrote {len(defined_macros(text))} macros to {args.out}")
    if args.check:
        undefined, unused = check_paper(text, args.paper)
        if unused:
            print("defined but not used in the paper: " + ", ".join(unused))
        if undefined:
            print("USED BUT NOT DEFINED: " + ", ".join(undefined))
            sys.exit(1)
        print("every macro the paper uses is defined")


if __name__ == "__main__":
    main()
