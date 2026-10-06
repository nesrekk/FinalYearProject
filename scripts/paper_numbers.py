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

Popular beliefs: the permutation + false-discovery framework of round 5 step 6
(scripts/paper_beliefs.py -> paper_beliefs, paper_beliefs_summary,
paper_beliefs_meta, paper/tables/beliefs.tex) is printed by the section
beliefs() below (\\pnBl... macros): the table's counts per family, the hot
streak null centres (the Miller-Sanjurjo check), the luck persistence test
and the referee counts, with claims on the sentences that depend on them.

Ablations: round 5 step 7 (scripts/paper_ablations.py -> paper_ablation_tests,
paper_ablation_metrics, paper_ablation_meta, paper/tables/ablations.tex) is
printed by ablations() (\\pnAb... macros): every cell of Table ablations, from
the row spec the script stores in paper_ablation_meta ('table:rows'), and the
numbers the Ablations subsection quotes, with a claim on every sentence that
depends on a sign or an interval.

Manifest: round 5 step 9's artifact manifest (scripts/paper_manifest.py ->
paper/manifest.json, row count and content hash of every table) is printed by
manifest() (\\pnMan... macros: table and row counts, the database digest).
The section checks the manifest against the live database (table set, schemas,
row counts) and stops if it is stale; if the file is missing it writes no
\\pnMan macros and says so (--check then names them). Run it through
scripts/rebuild_all.sh paper-inputs, which writes the manifest first.

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
from datetime import timezone
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
    # Two decimals since round 8 step 6a: the parser finds most of ESPN's no-id players by exact name, season and team
    # (pbp_lineups.load_season_names, player_bio), so what is left is a few hundredths of a per cent (it was 2-6%).
    N.add("UnidMinutesPctMin", pct(min(v for _, v in un), 2),
          "min over seasons before the last of lineup_stint_seasons.bad_lineup_minutes / minutes")
    N.add("UnidMinutesPctMax", pct(max(v for _, v in un), 2), "max of the same")
    N.claim(max(v for _, v in un) < 0.005,
            "Data / Table audit: after the exact-name match only a fraction of a per cent of minutes a season lack five identified players a side")
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
    # (No sentence uses this difference since round 6: the claim that the three-season window was ahead of the prior version on
    # the test season, true until round 8 step 6b's rebuild (-0.15, p 0.06 since), guarded nothing; the macros stay defined.)
    del mp
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
    # Round 8.5 step C re-fetched the 2025-26 chart from stats.nba.com: the four games of 2025-11-19/20 the bulk file
    # lacked are in, so no reconciled game is missing. The lowest season's gap is ESPN's end-of-quarter heaves, which
    # the NBA counts as team attempts since 2025-26 and leaves off the chart (docs/qa/ROUND8_ISSUES.md R8-088).
    N.claim(not missing, "Methods (appendix): every reconciled game has shot-chart rows")
    N.raw["chart_missing"] = 0
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
    N.claim(nx[("tune", "xrapm_prior")] > nx[("tune", "rapm_prior")] and nx[("validate", "xrapm_prior")] > nx[("validate", "rapm_prior")],
            "Results: with a prior, the expected-points version is behind on the tune and validation seasons")
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
    N.claim(all(r["diff"] > 0 for r in xb.values()), "Results: expected-points RAPM with a prior is never ahead of BPM")
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
    N.claim(a("chart_missing_games") == 0, "Table audit: every game charted (the bulk file's gaps re-fetched, round 8.5 step C)")
    N.claim(a("chart_heaves") == a("chart_heaves_all") > 0 and a("chart_heaves_matched") <= 0.01 * a("chart_heaves_all"),
            "Table audit: the play-by-play's heaves are all in the lowest-matching season and almost none is on the chart")
    N.claim(a("chart_heave_period_unmatched") > 0.8 * a("chart_unmatched_min_season"),
            "Methods (appendix): most of the lowest season's unmatched attempts are in a shooter-period with a heave")
    # Sentences of the Data quality subsection and the table.
    N.claim(a("wrong_player_left") == 0, "Table audit: the wrong-player tags are repaired (repair_espn_player_ids.find() finds none)")
    N.claim(a("twin_same_teams") + a("twin_neutral") == a("twin_games"), "Table audit: every nba_api game is an ESPN game again")
    # Round 8 step 6a: the parser ignores a team-less substitution naming nobody leaving and gives the one naming both players
    # their team; before it, one player-game per such game gained minutes and one line had team 'NaN' (class 'disclosed').
    N.claim(a("teamless_player_games") == 0 and a("teamless_lines_higher") == 0 and a("nan_team_rows") == 0,
            "Table audit: no team-less substitution leaves a game line with extra minutes or without a team (handling: repaired)")
    off_unrec = one(cur, """
        WITH l AS (SELECT game_id, team_abbreviation t, sum(seconds::numeric) secs, sum((tm_pts - op_pts)::numeric) onc
                   FROM player_game_lines GROUP BY 1, 2),
             g AS (SELECT 'espn_' || espn_id game_id, team_abbreviation t, pts_for - pts_against m, periods gp
                   FROM game_scores WHERE espn_id IS NOT NULL)
        SELECT count(*) FROM l JOIN g USING (game_id, t) JOIN lineup_stint_games s ON s.game_id = l.game_id
        WHERE abs(secs - 5 * (2880 + 300 * greatest(gp - 4, 0))) < 1 AND onc <> 5 * m AND NOT s.game_ok""")[0]
    N.claim(a("oncourt_off") == off_unrec,
            "Table audit: the game lines' on-court margin misses five times the final only in games whose play-by-play does not reconcile")
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


# Franchise code -> name, for the prose (team_luck_schedule.franchise; NJN/NOH are joined into BKN/NOP there).
FRANCHISE_NAMES = {"ATL": "Atlanta", "BOS": "Boston", "BKN": "Brooklyn", "CHA": "Charlotte", "CHI": "Chicago", "CLE": "Cleveland",
                   "DAL": "Dallas", "DEN": "Denver", "DET": "Detroit", "GSW": "Golden State", "HOU": "Houston", "IND": "Indiana",
                   "LAC": "the Los Angeles Clippers", "LAL": "the Los Angeles Lakers", "MEM": "Memphis", "MIA": "Miami",
                   "MIL": "Milwaukee", "MIN": "Minnesota", "NOP": "New Orleans", "NYK": "New York", "OKC": "Oklahoma City",
                   "ORL": "Orlando", "PHI": "Philadelphia", "PHX": "Phoenix", "POR": "Portland", "SAC": "Sacramento",
                   "SAS": "San Antonio", "TOR": "Toronto", "UTA": "Utah", "WAS": "Washington"}
STREAK_PROSE = {"pts": "StreakPts", "fg3_pct": "StreakThree", "ts_pct": "StreakTs", "min": "StreakMin", "usg_pct": "StreakUsg"}
STREAK_SHOOTING = ("fg_pct", "fg3_pct", "ft_pct", "ts_pct")
STREAK_ROLE = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fta", "min", "usg_pct")


def beliefs(cur, N):
    """Popular beliefs under one permutation + FDR framework (paper_beliefs_summary, paper_beliefs, paper_beliefs_meta;
    scripts/paper_beliefs.py): the Beliefs subsection and Table beliefs (paper/tables/beliefs.tex)."""
    N.start("Popular beliefs (Section: Popular beliefs tested, Table: beliefs; scripts/paper_beliefs.py)")
    meta = dict(rows(cur, "SELECT key, value FROM paper_beliefs_meta"))
    S = {key: r for key, *r in rows(cur, """SELECT key, n_units, k05, expected05, k_fdr, agg_obs, agg_null_mean, agg_null_lo,
                                                   agg_null_hi, agg_p, agg_adjusted, agg_n, macro, in_table, floor
                                            FROM paper_beliefs_summary""")}
    N.add("BlPermsOne", integer(meta["perms1"]), "paper_beliefs_meta perms1: first-stage draws per unit")
    N.add("BlPermsTwo", integer(meta["perms2"]), "paper_beliefs_meta perms2: draws for units screened at stage2_p")
    N.add("BlStageTwoP", dec(meta["stage2_p"], 2), "paper_beliefs_meta stage2_p")
    N.add("BlFdrPct", dec(meta["fdr_q"] * 100, 0), "paper_beliefs_meta fdr_q (%)")
    N.claim(meta["streak_perms"] == meta["perms1"], "Methods: every family gets the same number of first-stage draws")
    N.claim(meta["clutch_max_dev_vs_stored"] <= 5.1e-5 and meta["streak_max_dev_vs_stored"] < 1e-6
            and meta["split_max_dev_vs_stored"] < 1e-9 and meta["referee_max_dev_vs_stored"] <= 5.1e-4,
            "Methods: every unit's platform number equals the stored value (within the stored rounding: 4 decimals for clutch lifts, 3 for referees)")

    # Table beliefs: one row per in_table family.
    total_n = total_fdr = 0
    for key, (n, k05, exp, kfdr, *_rest, macro, in_table, _floor) in S.items():
        if not in_table:
            continue
        N.add(f"Bl{macro}N", integer(n), f"paper_beliefs_summary n_units, {key}")
        N.add(f"Bl{macro}Kfive", integer(k05), f"paper_beliefs_summary k05, {key}")
        N.add(f"Bl{macro}Exp", dec(exp, 1), f"paper_beliefs_summary expected05, {key}")
        N.add(f"Bl{macro}Kfdr", integer(kfdr), f"paper_beliefs_summary k_fdr, {key}")
        total_n += n
        total_fdr += kfdr
    N.add("BlTableTests", integer(total_n), "paper_beliefs_summary: sum of n_units over the table's rows")
    N.add("BlTableKfdr", integer(total_fdr), "paper_beliefs_summary: sum of k_fdr over the table's rows")

    # Clutch.
    N.add("BlClutchShift", dec(meta["clutch_league_shift"], 3), "paper_beliefs_meta clutch_league_shift: league clutch minus non-clutch points per chance")
    N.add("BlClutchGames", integer(meta["clutch_games"]), "paper_beliefs_meta clutch_games")
    N.add("BlClutchChancesMillion", millions(meta["clutch_scoring_chances"], 2), "paper_beliefs_meta clutch_scoring_chances")
    n50, k50, e50, f50 = S["clutch:50"][:4]
    N.add("BlClutchFiftyN", integer(n50), "paper_beliefs_summary n_units, clutch:50")
    N.add("BlClutchFiftyKfive", integer(k50), "paper_beliefs_summary k05, clutch:50")
    N.add("BlClutchFiftyExp", dec(e50, 1), "paper_beliefs_summary expected05, clutch:50")
    N.add("BlClutchFiftyKfdr", integer(f50), "paper_beliefs_summary k_fdr, clutch:50")
    N.claim(S["clutch:100"][3] == 0 and f50 == 0, "Beliefs: no player's clutch lift survives FDR at either floor")

    # Hot streaks: the league slope, its null centre and the null-centred share, per stat and window (windows of 10 in the prose).
    combos = [(k.split(":")[1], int(k.split(":")[2])) for k in S if k.startswith("streak:")]
    N.add("BlStreakCombos", integer(len(combos)), "paper_beliefs_summary: streak families (stat x window)")
    null_c = {c: S[f"streak:{c[0]}:{c[1]}"][5] for c in combos}
    adj = {c: S[f"streak:{c[0]}:{c[1]}"][9] for c in combos}
    obs_sl = {c: S[f"streak:{c[0]}:{c[1]}"][4] for c in combos}
    N.claim(all(v > 0 for v in null_c.values()), "Beliefs: the persistence slope's null centre is positive for every stat and window")
    N.add("BlStreakNullMinPct", pct(min(null_c.values()), 0), "paper_beliefs_summary agg_null_mean, min over streak families (%)")
    N.add("BlStreakNullMaxPct", pct(max(null_c.values()), 0), "paper_beliefs_summary agg_null_mean, max over streak families (%)")
    for stat, macro in STREAK_PROSE.items():
        r = S[f"streak:{stat}:10"]
        if stat in ("pts", "min", "usg_pct"):
            N.add(f"Bl{macro}TenAdjPct", pct(r[9], 0), f"paper_beliefs_summary agg_adjusted, streak:{stat}:10 (%; observed minus null centre)")
        if stat in ("min", "usg_pct"):
            continue
        N.add(f"Bl{macro}TenObsPct", pct(r[4], 0), f"paper_beliefs_summary agg_obs, streak:{stat}:10 (%; = hot_streak_persistence.slope)")
        N.add(f"Bl{macro}TenNullPct", pct(r[5], 0), f"paper_beliefs_summary agg_null_mean, streak:{stat}:10 (%)")
        N.add(f"Bl{macro}TenP", pval(r[8]), f"paper_beliefs_summary agg_p, streak:{stat}:10")
        if stat == "pts":
            N.add(f"Bl{macro}TenNullLoPct", pct(r[6], 0), f"paper_beliefs_summary agg_null_lo, streak:{stat}:10 (%)")
            N.add(f"Bl{macro}TenNullHiPct", pct(r[7], 0), f"paper_beliefs_summary agg_null_hi, streak:{stat}:10 (%)")
    shoot = [adj[c] for c in combos if c[0] in STREAK_SHOOTING]
    role = [adj[c] for c in combos if c[0] in STREAK_ROLE]
    N.add("BlStreakShootingAdjMinPct", pct(min(shoot), 0), "paper_beliefs_summary agg_adjusted, min over the shooting-% streak families (%)")
    N.add("BlStreakShootingAdjMaxPct", pct(max(shoot), 0), "paper_beliefs_summary agg_adjusted, max over the shooting-% streak families (%)")
    N.add("BlStreakShootingFamilies", integer(len(shoot)), "paper_beliefs_summary: shooting-% streak families")
    N.add("BlStreakShootingInsideNull", integer(sum(1 for c in combos if c[0] in STREAK_SHOOTING and S[f"streak:{c[0]}:{c[1]}"][8] >= 0.05)),
          "paper_beliefs_summary: shooting-% streak families whose league slope has permutation p >= 0.05")
    N.add("BlStreakRoleAdjMinPct", pct(min(role), 0), "paper_beliefs_summary agg_adjusted, min over the counting/role streak families (%)")
    N.add("BlStreakRoleAdjMaxPct", pct(max(role), 0), "paper_beliefs_summary agg_adjusted, max over the counting/role streak families (%)")
    N.add("BlStreakMinTwentyObsPct", pct(obs_sl[("min", 20)], 0), "paper_beliefs_summary agg_obs, streak:min:20 (%)")
    N.add("BlStreakMinTwentyNullPct", pct(null_c[("min", 20)], 0), "paper_beliefs_summary agg_null_mean, streak:min:20 (%)")
    N.add("BlStreakMinTwentyP", pval(S["streak:min:20"][8]), "paper_beliefs_summary agg_p, streak:min:20")
    N.claim(S["streak0:min:20"][4] > S["streak0:min:20"][5],
            "Beliefs: with the season-only baseline the minutes slope is above its null centre (the prior-season blend is what puts it below)")
    N.claim(obs_sl[("min", 20)] < null_c[("min", 20)] and S["streak:min:20"][8] < 0.05,
            "Beliefs: minutes over 20 games sit below their null centre, by more than the draws allow")
    for stat, N_, macro in (("min", 5, "StreakMinFive"), ("pts", 5, "StreakPtsFive"), ("ft_pct", 10, "StreakFtTen")):
        r = S[f"streak:{stat}:{N_}"]
        N.add(f"Bl{macro}N", integer(r[0]), f"paper_beliefs_summary n_units, streak:{stat}:{N_}")
        N.add(f"Bl{macro}Kfdr", integer(r[3]), f"paper_beliefs_summary k_fdr, streak:{stat}:{N_}")
        if stat == "ft_pct":
            N.add(f"Bl{macro}Exp", dec(r[2], 1), f"paper_beliefs_summary expected05, streak:{stat}:{N_}")
            N.add(f"Bl{macro}Kfive", integer(r[1]), f"paper_beliefs_summary k05, streak:{stat}:{N_}")
            N.claim(r[1] > 1.5 * r[2] and r[3] == 0, "Beliefs: FT% over ten games has more players at p<0.05 than expected and none survives FDR")
        else:
            N.add(f"Bl{macro}AdjPct", pct(r[9], 0), f"paper_beliefs_summary agg_adjusted, streak:{stat}:{N_} (%)")
    N.claim(S["streak:min:5"][3] > S["streak:min:5"][2] * 3 and S["streak:pts:5"][3] > S["streak:pts:5"][2] * 2,
            "Beliefs: for minutes and points over 5 games, far more players survive FDR than chance gives")
    N.claim(max(adj[c] for c in combos if c[0] in STREAK_ROLE) == adj[("min", 5)],
            "Beliefs: the largest null-centred share among the role stats is minutes over 5 games")
    N.claim(all(adj[(s, 20)] < adj[(s, 5)] for s in STREAK_ROLE), "Beliefs: every role stat's null-centred share is smaller at 20 games than at 5")
    N.add("BlStreakMinWindows", integer(S["streak:pts:10"][13]), "paper_beliefs_summary floor, streak families (windows per player)")
    shoot_fdr = sum(S[f"streak:{c[0]}:{c[1]}"][3] for c in combos if c[0] in STREAK_SHOOTING)
    N.add("BlStreakShootingKfdr", integer(shoot_fdr), "paper_beliefs_summary: sum of k_fdr over the shooting-% streak families")
    N.add("BlStreakShootingTests", integer(sum(S[f"streak:{c[0]}:{c[1]}"][0] for c in combos if c[0] in STREAK_SHOOTING)),
          "paper_beliefs_summary: sum of n_units over the shooting-% streak families")
    N.claim(shoot_fdr <= 0.01 * sum(S[f"streak:{c[0]}:{c[1]}"][0] for c in combos if c[0] in STREAK_SHOOTING),
            "Beliefs: almost no shooting-% streak survives FDR (under 1% of those tests)")
    N.add("BlStreakUntestable", integer(meta["streak_players_untestable"]), "paper_beliefs_meta streak_players_untestable")
    kfdr_streak = sum(S[f"streak:{c[0]}:{c[1]}"][3] for c in combos)
    n_streak = sum(S[f"streak:{c[0]}:{c[1]}"][0] for c in combos)
    N.add("BlStreakTests", integer(n_streak), "paper_beliefs_summary: sum of n_units over streak families")
    N.add("BlStreakKfdr", integer(kfdr_streak), "paper_beliefs_summary: sum of k_fdr over streak families")

    # Situational splits, all families.
    sp = [k for k in S if k.startswith("split:")]
    N.add("BlSplitFamilies", integer(len(sp)), "paper_beliefs_summary: split families (split x stat)")
    N.add("BlSplitTests", integer(sum(S[k][0] for k in sp)), "paper_beliefs_summary: sum of n_units over split families")
    N.add("BlSplitKfive", integer(sum(S[k][1] for k in sp)), "paper_beliefs_summary: sum of k05 over split families")
    N.add("BlSplitExp", dec(sum(S[k][2] for k in sp), 0), "paper_beliefs_summary: sum of expected05 over split families")
    N.add("BlSplitKfdr", integer(sum(S[k][3] for k in sp)), "paper_beliefs_summary: sum of k_fdr over split families")

    # Team luck.
    r = S["luck:luck_per82"]
    N.add("BlLuckR", dec(r[4], 2), "paper_beliefs_summary agg_obs, luck (= luck_schedule_validation.luck_next_luck_r)")
    N.add("BlLuckNullLo", dec(r[6], 2), "paper_beliefs_summary agg_null_lo, luck")
    N.add("BlLuckNullHi", dec(r[7], 2), "paper_beliefs_summary agg_null_hi, luck")
    N.add("BlLuckP", pval(r[8]), "paper_beliefs_summary agg_p, luck")
    N.add("BlLuckPairs", integer(r[10]), "paper_beliefs_summary agg_n, luck (franchise-season pairs)")
    N.add("BlLuckBetweenP", pval(meta["luck_between_var_p"]), "paper_beliefs_meta luck_between_var_p")
    surv = rows(cur, """SELECT unit_name, stat, p_value FROM paper_beliefs WHERE key = 'luck:luck_per82' AND bh_reject
                        ORDER BY p_value""")
    N.add("BlLuckSurvivors", integer(len(surv)), "paper_beliefs: franchises with bh_reject, luck")
    if surv:
        N.add("BlLuckTopFranchise", FRANCHISE_NAMES.get(surv[0][0], surv[0][0]),
              f"paper_beliefs unit_name: the surviving franchise with the smallest p ({surv[0][0]})")
        N.add("BlLuckTopMean", dec(surv[0][1], 1), "paper_beliefs stat: its mean luck per 82 games")

    # Referees.
    for c, macro in (("fouls", "Fouls"), ("fta", "Fta"), ("pace", "Pace")):
        o, cw = S[f"referee:official:{c}"], S[f"referee:crew:{c}"]
        for name, value, src in ((f"BlRef{macro}Kfdr", o[3], "k_fdr"), (f"BlCrew{macro}Kfdr", cw[3], "k_fdr")):
            if c == "fta" and "Crew" in name:
                continue
            if "pn" + name not in N.names:            # the table's rows define the fouls / FTA ones above
                N.add(name, integer(value), f"paper_beliefs_summary {src}, referee:{'crew' if 'Crew' in name else 'official'}:{c}")
    N.add("BlRefN", integer(S["referee:official:fouls"][0]), "paper_beliefs_summary n_units, referee:official:fouls")
    N.add("BlRefExp", dec(S["referee:official:fouls"][2], 1), "paper_beliefs_summary expected05, referee:official:fouls")
    N.add("BlCrewN", integer(S["referee:crew:fouls"][0]), "paper_beliefs_summary n_units, referee:crew:fouls")
    N.add("BlRefGames", integer(meta["referee_games"]), "paper_beliefs_meta referee_games")
    N.add("BlRefFloor", integer(S["referee:official:fouls"][13]), "paper_beliefs_summary floor, referee:official:*")
    top = rows(cur, """SELECT unit_name, stat, n_obs FROM paper_beliefs WHERE key = 'referee:official:fouls' AND bh_reject
                       ORDER BY abs(stat) DESC LIMIT 1""")
    if top:
        N.add("BlRefTopName", top[0][0], "paper_beliefs unit_name: the surviving official with the largest fouls difference")
        N.add("BlRefTopDiff", dec(top[0][1], 1), "paper_beliefs stat: that difference (fouls per game vs the season mean)")
        N.add("BlRefTopGames", integer(top[0][2]), "paper_beliefs n_obs: games worked")


def ablations(cur, N):
    """Ablations (scripts/paper_ablations.py -> paper_ablation_tests, paper_ablation_metrics, paper_ablation_meta, and
    paper/tables/ablations.tex): Table ablations' cells, from the row spec the script stores ('table:rows'), and the
    numbers the Ablations subsection quotes, with claims on every sentence that depends on a sign or an interval."""
    N.start("Ablations (Section: Ablations, Table: ablations; scripts/paper_ablations.py)")
    meta = dict(rows(cur, "SELECT key, value FROM paper_ablation_meta"))   # jsonb: psycopg2 returns it parsed
    T = {(task, phase, metric, a, v): (diff, lo, hi)
         for task, phase, metric, a, v, diff, lo, hi in rows(cur, """SELECT task, phase, metric, model_a, variant, diff, ci_lo, ci_hi
                                                                   FROM paper_ablation_tests WHERE model_b LIKE '%%:full'""")}
    M = {(task, base, abl, phase, metric): v for task, base, abl, phase, metric, v in rows(cur, """
            SELECT task, base, ablation, phase, metric, value FROM paper_ablation_metrics
            WHERE phase <> 'tune' OR seasons LIKE '%%to%%'""")}
    N.claim(meta["run:resamples"] == 10_000, "Table ablations: intervals from 10,000 resamples, like every other interval in the paper")

    def t(task, base, abl, metric, phase, variant=""):
        return T[(task, phase, metric, f"{base}:{abl}", variant)]

    def out_of_ci(r):
        return r[1] > 0 or r[2] < 0

    src = "paper_ablation_tests diff (ablation minus full)"
    for r in meta["table:rows"]:
        k, sc, d = r["stem"], r["scale"], r["decimals"]
        for phase, suffix in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
            if r["task"] == "xfg" and phase == "tune":
                continue
            diff = t(r["task"], r["base"], r["ablation"], r["metric"], phase, r["variant"])[0]
            N.add(f"Ab{k}{suffix}", dec(diff * sc, d), f"{src}, {r['task']} {r['base']}:{r['ablation']} {r['metric']} {phase} (x{sc})")
        _, lo, hi = t(r["task"], r["base"], r["ablation"], r["metric"], "test", r["variant"])
        N.add(f"Ab{k}Lo", dec(lo * sc, d), f"paper_ablation_tests ci_lo, {r['task']} {r['base']}:{r['ablation']} {r['metric']} test (x{sc})")
        N.add(f"Ab{k}Hi", dec(hi * sc, d), f"paper_ablation_tests ci_hi, same row (x{sc})")

    # RAPM: the prior, its scale and the penalty.
    spec = {k.split(":")[2]: v for k, v in meta.items() if k.startswith("impact:rapm_prior:") and k.endswith(":spec")}
    N.add("AbPriorFullScale", dec(spec["scale=1"]["prior_scale"], 2), "paper_ablation_meta impact:rapm_prior:scale=1:spec")
    N.add("AbPriorDoubleScale", dec(spec["scale=2"]["prior_scale"], 2), "paper_ablation_meta impact:rapm_prior:scale=2:spec")
    # Table ablations' "LamHigh" row is the protocol's free minimum, the grid point its rule sets aside (lambda 12,000 until round
    # 8 step 6b's rebuild, 8,000 since; scripts/paper_ablations.py TABLE_ROWS).
    lam_high = next(r["ablation"] for r in meta["table:rows"] if r["stem"] == "LamHigh")
    N.add("AbLamHighLambda", integer(spec[lam_high]["lambda"]), f"paper_ablation_meta impact:rapm_prior:{lam_high}:spec")
    rp = {abl: {ph: t("impact_next", "rapm_prior", abl, "game_rmse", ph) for ph in ("tune", "validate", "test")}
          for abl in ("scale=0", "scale=1", "scale=2", lam_high, "no_poss_weight", "no_home", "no_home_rated")}
    ab_p = dict(rows(cur, """SELECT model_a, p_boot FROM paper_ablation_tests WHERE task = 'impact_next' AND phase = 'test'
                             AND metric = 'game_rmse' AND variant = '' AND model_b = 'rapm_prior:full'"""))
    N.add("AbPriorFullP", pval(ab_p["rapm_prior:scale=1"]), "paper_ablation_tests p_boot, impact_next rapm_prior:scale=1 game_rmse test")
    N.claim(all(r[0] > 0 and r[1] > 0 for r in rp["scale=0"].values()),
            "Ablations: without the prior next-season RMSE rises in every phase, each outside its interval")
    N.claim(rp["scale=1"]["tune"][1] > 0 and rp["scale=1"]["test"][0] < 0 and not out_of_ci(rp["scale=1"]["test"])
            and 0.05 <= ab_p["rapm_prior:scale=1"] < 0.06 and not out_of_ci(rp["scale=1"]["validate"]),
            "Ablations: at scale 1 the prior is behind on the tuning pairs, outside the interval, ahead on the test season with an interval "
            "that just reaches zero (p 0.05), level on validation")
    N.claim(all(r[1] > 0 for r in rp["scale=2"].values()), "Ablations: at scale 2 the prior version is behind in every phase")
    free = as_json(one(cur, "SELECT value FROM paper_eval_choices WHERE task = 'impact' AND model = 'rapm_prior' AND parameter = 'free_minimum'")[0])
    N.claim(free["lambda"] == spec[lam_high]["lambda"] and free["prior_scale"] == spec[lam_high]["prior_scale"],
            "Ablations: Table ablations' lambda row at the chosen scale is the tuning grid's free minimum that the rule sets aside")
    lh = rp[lam_high]
    N.claim(lh["tune"][0] < 0 and lh["test"][2] < 0 and lh["validate"][0] > 0 and not out_of_ci(lh["validate"]),
            "Ablations: the free minimum is ahead on the tuning pairs and the test season (outside its interval) and behind, inside its interval, on validation")
    lh_rmse = one(cur, """SELECT value FROM paper_ablation_metrics WHERE task = 'impact_next' AND base = 'rapm_prior'
                          AND ablation = %s AND phase = 'test' AND metric = 'game_rmse'""", (lam_high,))[0]
    bpm_rmse = one(cur, """SELECT value FROM paper_eval_metrics WHERE task = 'impact_next' AND model = 'bpm' AND phase = 'test'
                           AND metric = 'game_rmse'""")[0]
    N.claim(lh_rmse > bpm_rmse, "Ablations: with the grid minimum lambda RAPM + prior would still be behind BPM on the test season")
    lam_r = rows(cur, """SELECT phase, ablation, value FROM paper_ablation_metrics WHERE task = 'impact_reliability' AND base = 'rapm_prior'
                         AND (ablation LIKE 'lambda=%%' OR ablation = 'full') AND (phase <> 'tune' OR seasons LIKE '%%to%%')""")
    lam_full = int(one(cur, "SELECT value FROM paper_eval_choices WHERE task = 'impact' AND model = 'rapm_prior' AND parameter = 'lambda'")[0])
    mono = True
    for ph in ("tune", "validate", "test"):
        seq = sorted((lam_full if a == "full" else int(a.split("=")[1]), v) for p_, a, v in lam_r if p_ == ph)
        mono &= len(seq) > 10 and all(b[1] > a[1] for a, b in zip(seq, seq[1:]))
    N.claim(mono, "Ablations: RAPM + prior's year-to-year correlation rises with lambda over the whole grid in every phase")
    # Possession weights.
    N.add("AbNoWeightLambda", integer(meta["impact:no_poss_weight:lambda"]), "paper_ablation_meta impact:no_poss_weight:lambda (re-chosen on the tune pairs)")
    N.add("AbNoHomeLambda", integer(meta["impact:no_home:lambda"]), "paper_ablation_meta impact:no_home:lambda (re-chosen on the tune pairs)")
    N.claim(meta["impact:no_home_rated:lambda"] == meta["impact:no_home:lambda"] and meta["impact:no_poss_weight:prior_scale"] == spec["scale=1"]["prior_scale"] / 2
            and meta["impact:no_home:prior_scale"] == meta["impact:no_poss_weight:prior_scale"],
            "Methods, Ablations: re-chosen, the prior scale stays at the full model's in every ablation and the home ablations keep one lambda")
    sw = {ph: t("impact_next", "rapm_single", "no_poss_weight", "game_rmse", ph) for ph in ("tune", "validate", "test")}
    N.claim(sw["tune"][1] > 0 and sw["validate"][1] > 0 and sw["test"][0] > 0 and not out_of_ci(sw["test"]),
            "Ablations: without possession weights one-season RAPM is worse on the tuning and validation seasons, outside the interval, "
            "and worse on the test season inside it")
    N.add("AbNoWeightSingleTest", dec(sw["test"][0], 2), f"{src}, impact_next rapm_single:no_poss_weight game_rmse test")
    N.add("AbNoWeightSingleLo", dec(sw["test"][1], 2), "paper_ablation_tests ci_lo, same row")
    N.add("AbNoWeightSingleHi", dec(sw["test"][2], 2), "paper_ablation_tests ci_hi, same row")
    nw = rp["no_poss_weight"]
    N.claim(nw["tune"][1] > 0 and nw["validate"][0] > 0 and not out_of_ci(nw["validate"]) and not out_of_ci(nw["test"]),
            "Ablations: RAPM + prior without weights is worse on the tuning pairs, outside the interval; on the validation and test seasons "
            "the difference is inside its interval")
    rel = {ph: t("impact_reliability", "rapm_prior", "no_poss_weight", "corr", ph) for ph in ("tune", "validate", "test")}
    N.claim(all(r[1] > 0 for r in rel.values()), "Ablations: without weights RAPM + prior is more reliable year to year in every phase")
    N.add("AbNoWeightRelTest", dec(rel["test"][0], 2), "paper_ablation_tests diff, impact_reliability rapm_prior:no_poss_weight corr test")
    N.add("AbNoWeightRelLo", dec(rel["test"][1], 2), "paper_ablation_tests ci_lo, same row")
    N.add("AbNoWeightRelHi", dec(rel["test"][2], 2), "paper_ablation_tests ci_hi, same row")
    # The home term: what it does to the ratings and to the prediction.
    r_home = one(cur, """SELECT min(r) FROM (
                           SELECT a.base, corr(a.pred, f.pred) AS r FROM paper_ablation_predictions a
                           JOIN paper_ablation_predictions f ON f.task = a.task AND f.base = a.base AND f.ablation = 'full'
                                AND f.phase = a.phase AND f.season = a.season AND f.unit_id = a.unit_id
                           WHERE a.task = 'impact_reliability' AND a.ablation = 'no_home' GROUP BY a.base) x""")[0]
    N.add("AbNoHomeRatingR", dec(r_home, 3, ROUND_FLOOR), "min over rapm_single/rapm_prior of corr(rating without the home column, full rating), "
          "qualified player-seasons of paper_ablation_predictions impact_reliability (floored)")
    N.claim(r_home >= 0.999, "Ablations: without the home column the ratings correlate 0.999 or more with the full model's")
    mx = max(abs(r[0]) for base in ("rapm_single", "rapm_prior") for r in
             (t("impact_next", base, "no_home_rated", "game_rmse", ph) for ph in ("tune", "validate", "test")))
    N.add("AbNoHomeRatedMaxAbs", dec(mx, 2, ROUND_CEILING), "max |diff| over phases and both bases, impact_next no_home_rated game_rmse (rounded up)")
    nhr = rp["no_home_rated"]
    N.claim(nhr["test"][0] < 0 and nhr["test"][2] < 0, "Ablations: on the test season the no-home-in-the-fit ratings are slightly better, outside the interval")
    nh = rp["no_home"]
    N.claim(nh["tune"][1] > 0 and all(nh[ph][0] > 0 and not out_of_ci(nh[ph]) for ph in ("validate", "test")),
            "Ablations: leaving the home edge out of the prediction costs on the tuning pairs (outside its interval) and a similar amount inside the interval on the other seasons")

    # Expected FG: the feature groups.
    xf = {abl: {ph: t("xfg", "hgb", abl, "log_loss", ph) for ph in ("validate", "test")}
          for abl in ("no_coords", "no_dist_angle", "no_zone_value", "no_location", "no_clock_period", "no_season")}
    N.claim(all(r[1] > 0 for abl in ("no_location", "no_clock_period", "no_season") for r in xf[abl].values()),
            "Ablations: all location, the season and the clock/period each add, outside their intervals, in both seasons")
    N.claim(all(xf["no_season"][ph][0] > xf["no_clock_period"][ph][0] for ph in ("validate", "test")),
            "Ablations: the season matters more than the clock and period in both seasons")
    groups = [xf[a][ph] for a in ("no_coords", "no_dist_angle", "no_zone_value") for ph in ("validate", "test")]
    N.claim(not any(out_of_ci(r) for r in groups), "Ablations: removing any one location group is inside its interval in both seasons")
    gmax = max(abs(r[0]) for r in groups) * 1000
    N.add("AbXfgGroupMaxAbs", dec(gmax, 1, ROUND_CEILING), "max |diff| x 10^3 over the three single location groups and both seasons (rounded up)")
    N.add("AbXfgFullAucTest", dec(M[("xfg", "hgb", "full", "test", "roc_auc")], 2), "paper_ablation_metrics xfg hgb:full roc_auc test")
    N.add("AbXfgNoLocAucTest", dec(M[("xfg", "hgb", "no_location", "test", "roc_auc")], 2), "paper_ablation_metrics xfg hgb:no_location roc_auc test")

    # Pre-game model.
    pg = {abl: {ph: t("pregame", "pregame", abl, "log_loss", ph) for ph in ("tune", "validate", "test")} for abl in ("no_carry", "no_shrink", "no_b2b")}
    N.claim(all(r[1] > 0 for r in pg["no_shrink"].values()), "Ablations: without shrinkage pre-game log loss rises in every phase, outside its interval")
    N.claim(pg["no_carry"]["tune"][1] > 0 and pg["no_b2b"]["tune"][1] > 0 and pg["no_b2b"]["validate"][1] > 0
            and not out_of_ci(pg["no_carry"]["validate"]) and not out_of_ci(pg["no_carry"]["test"]) and not out_of_ci(pg["no_b2b"]["test"]),
            "Ablations: carry-over helps on tune only, the flags on tune and validation, neither outside its interval on the test season")
    N.claim(all(abs(pg["no_b2b"][ph][0] - v) < 1e-12 for ph, v in (
            (ph, one(cur, """SELECT diff FROM paper_eval_tests WHERE task = 'pregame' AND metric = 'log_loss' AND model_a = 'prior_rest'
                             AND model_b = 'prior' AND phase = %s""", (ph,))[0] * -1) for ph in ("tune", "validate", "test"))),
            "Ablations: the no-flags pre-game model is paper_eval's 'prior' form (the same difference as Table tests' flags comparison)")

    # Simulator.
    sim = {(abl, m): {ph: t(task, "sim", abl, m, ph, "halfway") for ph in ("tune", "validate", "test")}
           for abl in ("no_carry", "no_shrink", "no_b2b", "no_draw") for task, m in (("sim_playoffs", "log_loss"), ("sim_playoffs", "brier"),
                                                                                   ("sim_wins", "mae"), ("sim_wins", "rmse"), ("sim_wins", "cover80"))}
    N.claim(all(sim[(a, "log_loss")]["test"][2] < 0 and not out_of_ci(sim[(a, "log_loss")]["tune"]) and not out_of_ci(sim[(a, "log_loss")]["validate"])
                for a in ("no_carry", "no_shrink")),
            "Ablations: without carry-over or shrinkage the simulator's playoff log loss is better on the test season (interval below zero), inside its interval on tune and validation")
    N.claim(sim[("no_shrink", "rmse")]["tune"][1] > 0 and not out_of_ci(sim[("no_shrink", "rmse")]["validate"]) and not out_of_ci(sim[("no_shrink", "rmse")]["test"]),
            "Ablations: without shrinkage win totals are worse on the tuning seasons only")
    N.add("AbSimShrinkRmseTuneCiLo", dec(sim[("no_shrink", "rmse")]["tune"][1], 2), "paper_ablation_tests ci_lo, sim_wins sim:no_shrink rmse tune")
    N.add("AbSimShrinkRmseTuneCiHi", dec(sim[("no_shrink", "rmse")]["tune"][2], 2), "paper_ablation_tests ci_hi, same row")
    dr = sim[("no_draw", "cover80")]
    N.claim(dr["tune"][2] < 0 and all(r[0] < 0 for r in dr.values()), "Ablations: without the rating draw the ranges cover less in every phase, outside the interval on tune")
    N.add("AbSimDrawTuneCiLo", dec(dr["tune"][1] * 100, 1), "paper_ablation_tests ci_lo, sim_wins sim:no_draw cover80 tune (points)")
    N.add("AbSimDrawTuneCiHi", dec(dr["tune"][2] * 100, 1), "paper_ablation_tests ci_hi, same row (points)")
    N.add("AbSimFullTuneCoverPct", pct(M[("sim_wins", "sim", "full", "tune", "cover80")], 0), "paper_ablation_metrics sim_wins sim:full cover80 tune (pooled)")
    N.add("AbSimDrawTuneCoverPct", pct(M[("sim_wins", "sim", "no_draw", "tune", "cover80")], 0), "paper_ablation_metrics sim_wins sim:no_draw cover80 tune (pooled)")
    N.claim(abs(M[("sim_wins", "sim", "full", "tune", "cover80")] - one(cur, """SELECT value FROM paper_eval_metrics WHERE task = 'sim_wins'
                AND phase = 'tune' AND model = 'model' AND variant = 'halfway' AND metric = 'cover80' AND seasons LIKE '%%to%%'""")[0]) < 1e-12,
            "Ablations: the full simulator's coverage is Table sim's")
    mae = [sim[("no_draw", "mae")][ph] for ph in ("tune", "validate", "test")]
    N.claim(not any(out_of_ci(r) for r in mae), "Ablations: the rating draw changes win-total MAE by nothing outside its interval")
    N.add("AbSimDrawMaeMaxAbs", dec(max(abs(r[0]) for r in mae), 2, ROUND_CEILING), "max |diff| over phases, sim_wins sim:no_draw mae (wins, rounded up)")
    N.claim(not any(out_of_ci(sim[("no_b2b", m)][ph]) for m in ("log_loss", "brier", "mae", "rmse", "cover80") for ph in ("tune", "validate", "test")),
            "Ablations: the back-to-back flags change nothing in the simulator outside an interval")


def manifest(cur, N):
    """The artifact manifest (round 5 step 9): paper/manifest.json from scripts/paper_manifest.py, which must
    describe the database as it is now (table set, schemas and row counts checked live, ~2 s)."""
    N.start("Manifest (Data and Code Availability, Platform)")
    import paper_manifest as PM
    if not os.path.exists(PM.MANIFEST_JSON):
        print(f"note: {PM.MANIFEST_JSON} is missing, so the \\pnMan... macros are not written; "
              "run scripts/paper_manifest.py first (rebuild_all.sh paper-inputs does)", file=sys.stderr)
        return
    m = PM.load_manifest()
    stale = PM.stale_reasons(cur, m)
    N.claim(not stale, "Data and Code Availability: the manifest describes the database the numbers come from "
                       "(rerun scripts/paper_manifest.py): " + "; ".join(stale[:5]))
    d = m["database"]
    kinds = d["by_kind"]
    N.claim(all(PM.TABLES.get(e["table"]) == (e["kind"], e["producer"]) for e in m["tables"]),
            "Data and Code Availability: every table's kind and producer in the manifest are paper_manifest.TABLES'")
    N.add("ManTables", integer(d["tables"]), "paper/manifest.json database.tables (every base table in schema public)")
    N.add("ManRowsMillion", millions(d["rows"], 1), "paper/manifest.json database.rows, millions")
    N.add("ManDigest", d["digest_sha256"][:16], "paper/manifest.json database.digest_sha256, first 16 hex digits")
    N.add("ManLegacyTables", word(kinds["legacy"]), "manifest tables of kind 'legacy' (no loader in the repository)")
    N.add("ManCacheTables", word(kinds["cache"]), "manifest tables of kind 'cache' (written by the running app)")
    N.add("ManLegacyFirst", season(PM.LEGACY_SEASONS[0]), "paper_manifest.LEGACY_SEASONS: player_season_stats "
          "seasons loaded before the first commit (rows = the committed season CSV's players with >= 200 minutes; "
          "checked by api/tests/test_reproducibility.py)")
    N.add("ManLegacyLast", season(PM.LEGACY_SEASONS[1]), "paper_manifest.LEGACY_SEASONS, last season")
    N.claim({e["table"] for e in m["tables"] if e["kind"] == "legacy"} == {"mvp_seasons", "mvp_winners"},
            "Platform / Availability: the legacy tables are the two MVP award-label tables")


# ---------------------------------------------------------------- round 6 (step 12): the app features' results

PHASE_NAMES = (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))
TEST_COLS = ("task", "phase", "metric", "model_a", "model_b", "variant", "seasons", "n", "n_clusters", "value_a", "value_b",
             "diff", "ci_lo", "ci_hi", "p_boot", "dm_p")


class Pairs:
    """Rows of a table with paper_tests' columns (paper_eval_tests, pregame_availability_tests, lineup_predictor_tests,
    data_quality_sensitivity), printed as macros the way tests() prints paper_eval_tests'. A pair stored the other way
    round is read with the sign turned (diff -> -diff, interval mirrored); single-model rows are model_b ''."""

    def __init__(self, cur, N, table, where="TRUE"):
        self.N, self.table, self.T = N, table, {}
        for r in rows(cur, f"SELECT {', '.join(TEST_COLS)} FROM {table} WHERE {where}"):
            r = dict(zip(TEST_COLS, r))
            self.T[(r["task"], r["phase"], r["metric"], r["model_a"], r["model_b"], r["variant"])] = r

    def get(self, task, phase, metric, a, b="", variant=""):
        r = self.T.get((task, phase, metric, a, b, variant))
        if r is not None:
            return r
        r = self.T[(task, phase, metric, b, a, variant)]
        return dict(r, model_a=a, model_b=b, value_a=r["value_b"], value_b=r["value_a"], diff=-r["diff"],
                    ci_lo=-r["ci_hi"], ci_hi=-r["ci_lo"])

    def where(self, r):
        return (f"{self.table} {r['task']} {r['phase']} {r['metric']} {r['model_a']}"
                + (f" - {r['model_b']}" if r["model_b"] else "") + (f", {r['variant']}" if r["variant"] else "")
                + f" ({r['seasons']}, n {r['n']}, {r['n_clusters']} clusters)")

    def diff(self, name, task, phase, metric, a, b, d, variant="", scale=1, lo_hi=True, p=True, dm=False, cell=False):
        r = self.get(task, phase, metric, a, b, variant)
        w = self.where(r)
        self.N.add(name, dec(r["diff"] * scale, d), w + ": diff" + (f" x {scale}" if scale != 1 else ""))
        if lo_hi:
            self.N.add(name + "Lo", dec(r["ci_lo"] * scale, d), w + ": ci_lo (paired cluster bootstrap, 2.5th percentile)")
            self.N.add(name + "Hi", dec(r["ci_hi"] * scale, d), w + ": ci_hi (97.5th percentile)")
        if p:
            self.N.add(name + "P", pval(r["p_boot"]), w + ": p_boot (two-sided bootstrap p)")
        if dm:
            self.N.add(name + "DmP", pval(r["dm_p"]), w + ": dm_p (Diebold-Mariano, Newey-West variance)")
        if cell:
            self.N.add(name + "Pv", pcell(r["p_boot"]), w + ": p_boot as a table cell")
            if dm:
                self.N.add(name + "DmPv", pcell(r["dm_p"]), w + ": dm_p as a table cell")
        return r

    def value(self, name, task, phase, metric, model, d, variant="", scale=1, lo_hi=False):
        r = self.get(task, phase, metric, model, "", variant)
        w = self.where(r)
        self.N.add(name, dec(r["value_a"] * scale, d), w + ": value" + (f" x {scale}" if scale != 1 else ""))
        if lo_hi:
            self.N.add(name + "Lo", dec(r["ci_lo"] * scale, d), w + ": ci_lo (cluster bootstrap)")
            self.N.add(name + "Hi", dec(r["ci_hi"] * scale, d), w + ": ci_hi")
        return r


def excl(r):
    """The interval excludes zero."""
    return r["ci_lo"] > 0 or r["ci_hi"] < 0


def eval_metrics(cur):
    """paper_eval_metrics as {(task, phase, model, variant, seasons, metric): (value, n)} and protocol()'s pick()."""
    M = {(t, ph, m, va, se, me): (v, n) for t, ph, m, va, se, me, v, n in rows(
        cur, "SELECT task, phase, model, variant, seasons, metric, value, n FROM paper_eval_metrics")}

    def pick(task, phase, model, metric, variant=""):
        c = [(se, v, n) for (t, ph, m, va, se, me), (v, n) in M.items() if (t, ph, m, va, me) == (task, phase, model, variant, metric)]
        if phase == "tune" and len(c) > 1:
            c = [x for x in c if " to " in x[0]]
        assert len(c) == 1, (task, phase, model, metric, variant, c)
        se, v, n = c[0]
        return v, n, se
    return pick


def possessions_clock(cur, N):
    """Round 6 steps 3, 3b, 4: the possessions table (scripts/build_possessions.py), the corrected clock
    (scripts/build_event_clock.py; its accuracy is the audit's clock_lag class) and the Possession Explorer's results."""
    N.start("Possessions and the corrected clock (Data: possessions; round 6 steps 3, 3b, 4) -- possessions, possession_*, "
            "pbp_event_clock_meta, data_quality_sensitivity")
    n_poss, = one(cur, "SELECT count(*) FROM possessions")
    games, ok = one(cur, "SELECT count(*), count(*) FILTER (WHERE game_ok) FROM possession_games")
    N.add("PoPossessions", integer(n_poss), "count(*) possessions")
    N.add("PoGamesOk", integer(ok), "possession_games.game_ok (points, FGA, FTA, OREB and TOV add up to the final and the box)")
    N.claim((games, ok) == N.raw["games"], "Data: the possessions reconcile in exactly the games the stints do")
    meta = {k: as_json(v) for k, v in rows(cur, "SELECT key, value FROM possession_meta")}
    sec = meta["rules"]["transition_seconds"]
    N.add("PoTransSec", word(int(sec)), "possession_meta rules.transition_seconds (transition = first attempt within it)")
    tc = meta["transition_check"]
    N.add("PoTakeFouls", integer(tc["take_fouls"]), "possession_meta transition_check.take_fouls (transition take fouls with a start time)")
    N.add("PoTakeFoulsWithinPct", pct(tc["take_fouls_within_window"], 1), "transition_check.take_fouls_within_window (share inside the window)")
    dr = tc["dreb_ppp_by_first_attempt_second"]
    N.add("PoDrebPeakPpp", dec(max(dr), 2), "transition_check.dreb_ppp_by_first_attempt_second: highest points per possession "
          "(after a defensive rebound, by second of the first attempt)")
    N.add("PoDrebPlateauPpp", dec(dr[int(sec) + 2], 2), f"the same at {int(sec) + 2} s (the plateau the early premium fades to)")
    N.claim(max(dr) == max(dr[:int(sec)]) and dr[int(sec) + 2] < min(dr[1:int(sec)]),
            "Data: after a defensive rebound the early-shot premium peaks inside the transition window and has faded two seconds after it")
    timed, trans, tpts = one(cur, """SELECT sum(timed_poss)::float8, sum(trans_poss)::float8, sum(trans_pts)::float8 FROM possession_seasons
                                     WHERE team = 'ALL' AND start_type = 'all'""")
    N.add("PoTransSharePct", pct(trans / timed, 1), "possession_seasons ALL/all: sum(trans_poss) / sum(timed_poss), every season")
    P = Pairs(cur, N, "data_quality_sensitivity", "result = 'possessions' AND drop_set = 'none'")
    st = P.diff("PoDStealMade", "possessions", "all", "ppp", "steal", "made_fg", 2, p=False)
    P.value("PoPppSteal", "possessions", "all", "ppp", "steal", 2)
    P.value("PoPppMade", "possessions", "all", "ppp", "made_fg", 2)
    pts, poss = one(cur, "SELECT sum(pts)::float8, sum(poss)::float8 FROM possession_seasons WHERE team = 'ALL' AND start_type = 'dreb'")
    N.add("PoPppDreb", dec(pts / poss, 2), "possession_seasons ALL/dreb: points per possession after a defensive rebound, every season")
    tr = P.diff("PoDTransSettled", "possessions", "all", "ppp", "transition", "settled", 2, p=False)
    P.value("PoPppTrans", "possessions", "all", "ppp", "transition", 2)
    P.value("PoPppSettled", "possessions", "all", "ppp", "settled", 2)
    N.claim(st["diff"] > 0 and excl(st) and tr["diff"] > 0 and excl(tr),
            "Data: possessions after a steal and in transition score more, by more than their intervals")
    chk = as_json(one(cur, "SELECT value FROM data_quality_meta WHERE key = 'possessions_check_vs_possession_seasons'")[0])
    N.claim(abs(chk["steal_ppp"] - st["value_a"]) < 1e-12 and abs(chk["transition_share"] - trans / timed) < 1e-12,
            "Data: the bootstrap rows describe the possessions table (data_quality_meta's check)")
    # How much of the between-team spread is beyond chance, and how well it repeats: the computation of
    # api/routers/possessions._signal() (not imported: importing the app runs its DDL), with its rounding of ppp and
    # its standard error to four decimals, so the page and the paper print the same numbers.
    var = {(s, t): float(v) for s, t, v in rows(cur, """
        SELECT p.season, COALESCE(p.start_type, 'all'), VAR_SAMP(p.pts) FROM possessions p
        JOIN possession_games g USING (game_id) WHERE g.game_ok
        GROUP BY GROUPING SETS ((p.season, p.start_type), (p.season))""") if v is not None}
    teamrows = {(s, t, k): (poss, pts) for s, t, k, poss, pts in rows(
        cur, "SELECT season, team, start_type, poss, pts FROM possession_seasons WHERE team <> 'ALL'")}
    seasons = sorted({s for s, _t, _k in teamrows})
    teams = sorted({t for _s, t, _k in teamrows})
    sig = {}
    for key in ("all", "steal", "made_fg", "dreb"):
        shares, vals = [], {}
        for s in seasons:
            v = var[(s, key)]
            xs = {t: teamrows[(s, t, key)] for t in teams if (s, t, key) in teamrows and teamrows[(s, t, key)][0]}
            ppp = {t: round(pts / poss, 4) for t, (poss, pts) in xs.items()}
            se2 = [round(math.sqrt(v / poss), 4) ** 2 for poss, _ in xs.values()]
            m = sum(ppp.values()) / len(ppp)
            obs = sum((x - m) ** 2 for x in ppp.values()) / (len(ppp) - 1)
            shares.append(max(0.0, 1 - (sum(se2) / len(se2)) / obs))
            vals[s] = ppp
        rs = [_corr([vals[s][t] for t in teams], [vals[s + 1][t] for t in teams]) for s in seasons[:-1]]
        sig[key] = (sum(shares) / len(shares), sum(rs) / len(rs), len(rs))
    for key, K in (("all", "All"), ("steal", "Steal"), ("made_fg", "Made")):
        N.add(f"PoSignal{K}Pct", pct(sig[key][0], 0), f"possessions/possession_seasons, offence {key}: 1 - mean sampling variance / "
              "between-team variance of points per possession, averaged over seasons (api/routers/possessions._signal)")
        N.add(f"PoYty{K}", dec(sig[key][1], 2), f"the same: year-to-year r of team points per possession, mean over {sig[key][2]} pairs")
    N.add("PoYtyPairs", word(sig["all"][2]), "consecutive-season pairs")
    N.claim(sig["steal"][0] < sig["all"][0] / 2 and sig["steal"][1] < sig["all"][1] / 2,
            "Data: a team's points after steals is mostly noise (under half the signal share and half the year-to-year r of all possessions)")
    wp = as_json(one(cur, "SELECT value FROM pbp_event_clock_meta WHERE key = 'wp_check'")[0])
    for k, K in (("last_minute", "LastMin"),):
        N.add(f"PoWp{K}LlEspn", dec(wp[k]["log_loss_espn"], 4), f"pbp_event_clock_meta wp_check.{k}.log_loss_espn (Game Replay's model, ESPN's clock)")
        N.add(f"PoWp{K}LlCorr", dec(wp[k]["log_loss_corrected"], 4), f"wp_check.{k}.log_loss_corrected (the same model on the corrected clock)")
    N.add("PoWpLastMinEceEspn", dec(wp["last_minute"]["ece_espn"], 4), "wp_check.last_minute.ece_espn")
    N.add("PoWpLastMinEceCorr", dec(wp["last_minute"]["ece_corrected"], 4), "wp_check.last_minute.ece_corrected")
    N.claim(all(w["log_loss_corrected"] < w["log_loss_espn"] for k, w in wp.items() if isinstance(w, dict) and "log_loss_espn" in w),
            "Data: the win-probability model, fitted on ESPN's times, scores better on the corrected clock in every phase checked (not refitted)")
    A = {k: v for k, v in rows(cur, "SELECT key, value FROM paper_data_audit WHERE season = 0 AND key LIKE 'lag%%'")}
    N.claim(all(A[f"lag_{c}_median_odd"] == A[f"lag_{c}_median_even"] for c in ("fg_made", "ft_later_made", "reb", "tov_steal", "tov_dead", "fg_miss")),
            "Data: ESPN's lag by event class is the same on the two halves of the twin games")


def data_quality_effect(cur, N):
    """Round 6 step 11: the per-game flags and the "does it matter?" re-scores (scripts/build_data_quality.py), counted the
    way the Data Quality page counts them (api/routers/data_quality._call/_verdict): a cell is one pair in one phase,
    metric and scope of one drop set; its call is which side is lower with the interval excluding zero, or none."""
    N.start("Data quality, downstream (Section: Data quality; round 6 step 11) -- data_quality_game_flags, data_quality_sensitivity")
    lv = dict(rows(cur, "SELECT level, count(*) FROM data_quality_game_flags GROUP BY 1"))
    cg = one(cur, "SELECT count(*) FROM data_quality_game_flags WHERE level = 'flagged' AND 'chart_gaps' = ANY(classes)")[0]
    N.claim(cg > lv.get("flagged", 0) / 2, "Data quality: the flagged games are mostly games where the shot chart misses attempts")
    for k, K in (("flagged", "Flagged"), ("excluded", "Excluded"), ("worked_around", "Worked"), ("clean", "Clean")):
        N.add(f"DqsGames{K}", integer(lv.get(k, 0)), f"data_quality_game_flags level = {k}")
    cols = ("result", "drop_set", "scope", "games_dropped", "phase", "metric", "model_a", "model_b", "diff", "ci_lo", "ci_hi", "rand_p",
            "rand_lo", "rand_hi", "rand_draws")
    S = [dict(zip(cols, r)) for r in rows(cur, f"SELECT {', '.join(cols)} FROM data_quality_sensitivity WHERE model_b <> ''")]

    def call(r):
        return "a" if r["ci_hi"] < 0 else "b" if r["ci_lo"] > 0 else "none"
    full = {(r["result"], r["phase"], r["metric"], r["model_a"], r["model_b"]): call(r) for r in S if r["drop_set"] == "none"}
    cells = [(full[(r["result"], r["phase"], r["metric"], r["model_a"], r["model_b"])], call(r), r) for r in S if r["drop_set"] != "none"]
    flips = sum(f != "none" and c != "none" and f != c for f, c, _ in cells)
    clarity = sum((f == "none") != (c == "none") for f, c, _ in cells)
    controlled = [r for _f, _c, r in cells if r["rand_p"] is not None]
    beyond = sum(r["rand_p"] <= 0.05 for r in controlled)
    N.add("DqsCells", integer(len(cells)), "data_quality_sensitivity pair rows with a drop set (each compared with the every-game row)")
    N.add("DqsClarity", word(clarity), "cells whose interval starts or stops excluding zero")
    N.claim(flips == 0, "Data quality: no conclusion flips (no cell's interval excludes zero on the other side from every game's)")
    N.add("DqsControlled", integer(len(controlled)), "cells with the random-drop control (drop sets of 50+ games)")
    N.add("DqsBeyond", integer(beyond), "of those, the drop set moves the difference beyond the random drops at p <= 0.05")
    N.add("DqsBeyondExp", integer(round(0.05 * len(controlled))), "0.05 x controlled cells")
    draws = {r["rand_draws"] for r in controlled}
    N.claim(len(draws) == 1, "Data quality: one random-drop count")
    N.add("DqsRandDraws", integer(draws.pop()), "data_quality_sensitivity.rand_draws (random drops of as many games per season)")
    P = Pairs(cur, N, "data_quality_sensitivity", "drop_set = 'flagged' AND scope = 'everywhere' AND result = 'impact'")
    R = {(r["phase"]): r for r in S if r["drop_set"] == "flagged" and r["scope"] == "everywhere" and r["result"] == "impact"
         and (r["model_a"], r["model_b"], r["metric"]) == ("rapm_prior", "bpm", "game_rmse")}
    N.add("DqsImpactFlagGames", integer(R["test"]["games_dropped"]), "games dropped (flagged or excluded) for the impact result")
    for ph, Ph in (("tune", "Tune"), ("validate", "Val"), ("test", "Test")):
        r = P.diff(f"DqsImpact{Ph}", "impact_next", ph, "game_rmse", "rapm_prior", "bpm", 2, variant="drop:flagged", p=False)
        N.add(f"DqsImpact{Ph}RandLo", dec(R[ph]["rand_lo"], 2), "data_quality_sensitivity rand_lo: 2.5th percentile over the random drops")
        N.add(f"DqsImpact{Ph}RandHi", dec(R[ph]["rand_hi"], 2), "rand_hi: 97.5th percentile")
        N.add(f"DqsImpact{Ph}RandP", pval(R[ph]["rand_p"]), "rand_p: (1 + random drops moving it as far) / (1 + draws)")
    t = R["test"]
    # Until round 8 step 6c the flagged games' move stayed inside the random drops (p 0.065 at 6b); with the team-less class
    # no longer flagged (306 games, not 315) both phases' moves pass every one of the 30 random drops (p at its floor, 1/31).
    vf = P.get("impact_next", "validate", "game_rmse", "rapm_prior", "bpm", "drop:flagged")
    N.claim(vf["diff"] < 0 and not excl(vf), "Data quality: without the flagged games the validation season's lead keeps its sign but its interval reaches zero")
    tf, uf = P.get("impact_next", "test", "game_rmse", "rapm_prior", "bpm", "drop:flagged"), P.get("impact_next", "tune", "game_rmse", "rapm_prior", "bpm", "drop:flagged")
    N.claim(tf["diff"] > 0 and excl(tf) and uf["diff"] < 0 and excl(uf) and max(t["rand_p"], R["tune"]["rand_p"]) * (1 + t["rand_draws"]) < 1.5,
            "Data quality: without the flagged games the test-season gap shrinks and the tuning seasons' lead grows, each further than every random drop (p at its floor), "
            "and neither interval changes side")
    A = Pairs(cur, N, "data_quality_sensitivity", "drop_set = 'flagged' AND result = 'availability'")
    a = A.diff("DqsAvailTune", "pregame", "tune", "log_loss", "avail_bpm", "prior_rest", 4, variant="drop:flagged", p=False, lo_hi=False)
    ar = next(r for r in S if r["drop_set"] == "flagged" and r["result"] == "availability" and r["phase"] == "tune"
              and r["metric"] == "log_loss" and r["model_a"] == "avail_bpm" and r["model_b"] == "prior_rest")
    N.add("DqsAvailTuneRandP", pval(ar["rand_p"]), "availability, tune, log loss: rand_p of the flagged drop set")
    full_a = next(r for r in S if r["drop_set"] == "none" and r["result"] == "availability" and r["phase"] == "tune"
                  and r["metric"] == "log_loss" and r["model_a"] == "avail_bpm")
    # Before round 8's play-by-play rebuild the flagged games were mostly games with an unidentified player and the gain halved
    # without them (p 0.03); those players are identified now and the gain is unchanged.
    N.claim(abs(a["diff"] - full_a["diff"]) < 0.001 and ar["rand_p"] > 0.05,
            "Data quality: on the tuning seasons the gain from knowing who played is the same without the flagged games, within random drops")


def rating_tracker(cur, N):
    """Round 6 step 7: the Rating Tracker (scripts/rating_tracker_lib.py, build_rating_tracker.py), scored by paper_eval as
    rapm_tracker; and step 8's shooter-aware expected-points RAPM rows of Table rapm."""
    N.start("Rating Tracker and the shooter-aware xRAPM in Tables rapm/tests (round 6 steps 7, 8) -- rating_tracker_fit, "
            "player_rating_tracker, paper_eval_metrics, paper_eval_tests")
    f = dict(zip(("lambda0", "lambda_q", "lambda_b", "prior_scale", "phi", "estimated_on", "players", "quick", "criterion"), one(
        cur, "SELECT lambda0, lambda_q, lambda_b, prior_scale, phi, estimated_on, players, quick, criterion FROM rating_tracker_fit")))
    N.claim(not f["quick"] and f["estimated_on"] == "2020-21 to 2023-24", "Rating Tracker: hyperparameters chosen on the tuning seasons only")
    N.add("TrLambdaZero", integer(round(f["lambda0"])), "rating_tracker_fit.lambda0 (a newcomer's prior precision, per possession-weighted row)")
    N.add("TrLambdaQ", integer(round(f["lambda_q"])), "rating_tracker_fit.lambda_q (between-season drift precision)")
    N.add("TrLambdaB", integer(round(f["lambda_b"])), "rating_tracker_fit.lambda_b (precision of the season's BPM measurement)")
    N.add("TrScale", dec(f["prior_scale"], 2), "rating_tracker_fit.prior_scale (BPM's scale in the measurement)")
    N.add("TrPhi", dec(f["phi"], 2), "rating_tracker_fit.phi (carry-over of last season's rating)")
    N.add("TrPlayers", integer(f["players"]), "rating_tracker_fit.players (state dimension / 2)")
    lo, hi = one(cur, """SELECT min(r), max(r) FROM (SELECT season, corr(rapm, bpm) r FROM player_rating_tracker
                         WHERE kind = 'filtered' AND qualified GROUP BY season) x""")
    N.add("TrBpmCorrMin", dec(lo, 2), "player_rating_tracker filtered, qualified: corr(rating, BPM) by season, smallest")
    N.add("TrBpmCorrMax", dec(hi, 2), "the same, largest")
    pick = eval_metrics(cur)
    models = {"rapm_tracker": "Tracker", "xrapm_sa_prior": "XsaPrior", "xrapm_sa_single": "XsaSingle"}
    nx = {}
    for model, m in models.items():
        for ph, Ph in PHASE_NAMES:
            v, n, se = pick("impact_next", ph, model, "game_rmse")
            nx[(ph, model)] = v
            N.add(f"EvNext{Ph}{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_next {ph} {model} game_rmse ({se}, n {n})")
        v, n, se = pick("impact_next", "test", model, "game_corr")
        N.add(f"EvNextTest{m}R", dec(v, 2), f"paper_eval_metrics impact_next test {model} game_corr ({se})")
        v, n, se = pick("impact_heldout", "test", model, "game_rmse")
        N.add(f"EvHeldTest{m}Rmse", dec(v, 2), f"paper_eval_metrics impact_heldout test {model} game_rmse ({se}, n {n})")
    allm = [m for m in ("bpm", "bpm_scaled", "rapm_prior", "rapm_multi", "rapm_single", "zero", "onoff", "onoff_scaled",
                        "xrapm_prior", "xrapm_single", "rapm_tracker", "xrapm_sa_prior", "xrapm_sa_single")]
    test_all = {m: pick("impact_next", "test", m, "game_rmse")[0] for m in allm}
    # Round 8 step 6b: on the rebuilt stints the tracker's settings were re-chosen by the same rule (weaker carry-over) and BPM,
    # not the tracker, has the lowest test-season error (the tracker had it before; R8-076).
    N.claim(min(test_all, key=test_all.get) == "bpm",
            "Results: BPM has the lowest test-season next-season error of any estimator in Table rapm, the Rating Tracker included")
    v, n, se = pick("impact_reliability", "test", "rapm_tracker", "corr")
    N.add("EvYtyTestTracker", dec(v, 2), f"paper_eval_metrics impact_reliability test rapm_tracker corr ({se}, n {n})")
    P = Pairs(cur, N, "paper_eval_tests")
    tb = {ph: P.diff(f"EvDNext{Ph}TrackerBpmRmse", "impact_next", ph, "game_rmse", "rapm_tracker", "bpm", 2,
                     dm=(ph == "test"), cell=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(all(tb[ph]["diff"] < 0 and excl(tb[ph]) for ph in ("tune", "validate")) and tb["test"]["diff"] > 0 and excl(tb["test"]),
            "Results: the tracker is ahead of BPM on the tuning and validation seasons and behind it on the test season, each by more than the interval")
    tp = {ph: P.diff(f"EvDNext{Ph}TrackerPriorRmse", "impact_next", ph, "game_rmse", "rapm_tracker", "rapm_prior", 2, cell=(ph == "test"),
                     dm=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(tp["tune"]["diff"] < 0 and excl(tp["tune"]) and not excl(tp["validate"]) and not excl(tp["test"]),
            "Results: the tracker is ahead of RAPM + prior on the tuning seasons by more than the interval, level on the validation and test seasons")
    ts = {ph: P.diff(f"EvDNext{Ph}TrackerSingleRmse", "impact_next", ph, "game_rmse", "rapm_tracker", "rapm_single", 2,
                     lo_hi=(ph == "test"), p=False) for ph, Ph in PHASE_NAMES}
    tm = {ph: P.diff(f"EvDNext{Ph}TrackerMultiRmse", "impact_next", ph, "game_rmse", "rapm_tracker", "rapm_multi", 2,
                     lo_hi=(ph == "test"), p=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(all(ts[ph]["diff"] < 0 and excl(ts[ph]) for ph in ts) and not any(excl(tm[ph]) for ph in tm),
            "Results: the tracker is ahead of one-season RAPM in every phase by more than the interval and level with the three-season "
            "window in every phase")
    hb = P.diff("EvDHeldTestTrackerBpmRmse", "impact_heldout", "test", "game_rmse", "rapm_tracker", "bpm", 2, p=False)
    hp = P.diff("EvDHeldTestTrackerPriorRmse", "impact_heldout", "test", "game_rmse", "rapm_tracker", "rapm_prior", 2, p=False)
    N.claim(hb["diff"] > 0 and excl(hb) and hp["diff"] < 0 and excl(hp),
            "Results: on held-out games of the same season BPM (which saw them) stays ahead of the tracker; the tracker is ahead of RAPM + prior")
    yb = {ph: P.get("impact_reliability", ph, "corr", "bpm", "rapm_tracker") for ph, _ in PHASE_NAMES}
    P.diff("EvDYtyTestBpmTracker", "impact_reliability", "test", "corr", "bpm", "rapm_tracker", 2)
    yp = [P.get("impact_reliability", ph, "corr", "rapm_tracker", b) for ph, _ in PHASE_NAMES for b in ("rapm_prior", "rapm_single")]
    N.claim(all(r["diff"] > 0 and excl(r) for r in yb.values()) and all(r["diff"] > 0 and excl(r) for r in yp),
            "Results: year to year the tracker is more reliable than RAPM with or without a prior and less reliable than BPM, in every phase "
            "by more than the interval")
    # Table tests rows and Table rapm claims for the shooter-aware version (step 8)
    sp = {ph: P.diff(f"EvDNext{Ph}XsaPriorPriorRmse", "impact_next", ph, "game_rmse", "xrapm_sa_prior", "rapm_prior", 2,
                     cell=(ph == "test"), dm=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(not excl(sp["tune"]) and sp["validate"]["diff"] > 0 and excl(sp["validate"]) and not excl(sp["test"]),
            "Results: shooter-aware xRAPM + prior is level with RAPM + prior on the tuning and test seasons and behind on the validation season")
    sb = {ph: P.diff(f"EvDNext{Ph}XsaPriorBpmRmse", "impact_next", ph, "game_rmse", "xrapm_sa_prior", "bpm", 2,
                     lo_hi=(ph == "test"), p=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(not any(r["diff"] < 0 and excl(r) for r in sb.values()), "Results: the shooter-aware version is never ahead of BPM")
    ss = {ph: P.diff(f"EvDNext{Ph}XsaSingleSingleRmse", "impact_next", ph, "game_rmse", "xrapm_sa_single", "rapm_single", 2,
                     cell=(ph == "test"), dm=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(ss["tune"]["diff"] > 0 and excl(ss["tune"]) and ss["validate"]["diff"] > 0 and excl(ss["validate"]) and not excl(ss["test"]),
            "Results: one-season shooter-aware xRAPM is still behind one-season RAPM on the tuning and validation seasons, level on the test season")
    sx = {ph: P.diff(f"EvDNext{Ph}XsaSingleXsingleRmse", "impact_next", ph, "game_rmse", "xrapm_sa_single", "xrapm_single", 2,
                     cell=(ph == "test"), dm=(ph == "test")) for ph, Ph in PHASE_NAMES}
    sl = {ph: P.diff(f"EvDNext{Ph}XsaSingleXlfSingleRmse", "impact_next", ph, "game_rmse", "xrapm_sa_single", "xrapm_lf_single", 2,
                     lo_hi=False, p=False) for ph, Ph in PHASE_NAMES}
    N.claim(all(r["diff"] < 0 and excl(r) for r in list(sx.values()) + [P.get("impact_next", ph, "game_rmse", "xrapm_sa_single",
                                                                               "xrapm_lf_single") for ph, _ in PHASE_NAMES]),
            "Results: the shooter term improves on round 5's xRAPM, and on its own shooter-blind twin, by more than the interval in every phase")
    look = [abs(P.get("impact_next", ph, "game_rmse", f"xrapm_lf_{v}", f"xrapm_{v}")["diff"]) for ph, _ in PHASE_NAMES for v in ("single", "prior")]
    N.add("XlfMaxAbs", dec(max(look), 2, ROUND_CEILING), "paper_eval_tests: largest |look-ahead-free - round-5 xRAPM| next-season RMSE "
          "difference over phases and versions, rounded up (the look-ahead fix alone)")
    v, n, se = pick("impact_reliability", "test", "xrapm_sa_prior", "corr")
    N.add("EvYtyTestXsaPrior", dec(v, 2), f"paper_eval_metrics impact_reliability test xrapm_sa_prior corr ({se}, n {n})")


def shot_value(cur, N):
    """Round 6 step 8: Shot Value Added (scripts/shot_value_lib.py, build_shot_value.py): pricing every attempt before
    its game, with and without the shooter's skill."""
    N.start("Shot Value Added (Section: shooter-aware expected points; round 6 step 8) -- shot_value_validation, shot_value_fit, "
            "shot_value_added, paper_xrapm_players")
    est = {r[0] for r in rows(cur, "SELECT DISTINCT estimated_on FROM shot_value_fit WHERE cls <> 'models'")}
    N.claim(len(est) == 1, "Shot value: one estimation span for every class")
    first, last = est.pop().split(" to ")
    N.add("SvFitFirst", season(first), "shot_value_fit.estimated_on (first season the carry-over and drift settings are estimated on)")
    N.add("SvFitLast", season(last), "shot_value_fit.estimated_on (last)")
    N.claim(int(last[:4]) + 1 < 2021, "Shot value: the settings are estimated on seasons before any scored one")
    V = {(sc, se, c, p): dict(zip(("n", "ll", "d", "lo", "hi", "games"), r)) for sc, se, c, p, *r in rows(
        cur, """SELECT scope, seasons, cls, price, n, log_loss, d_log_loss_vs_lf, ci_lo, ci_hi, games FROM shot_value_validation
                WHERE scope <> 'yty' AND scope NOT LIKE '%%->%%'""")}
    allk = [k for k in V if k[0] == "all"]
    span = {k[1] for k in allk}
    N.claim(len(span) == 1, "Shot value: one all-season span")
    span = span.pop()
    N.add("SvAttempts", millions(V[("all", span, "fg", "sa")]["n"], 2), "shot_value_validation all fg: field-goal attempts priced (millions)")
    for c, C in (("fg", "Fg"), ("ft", "Ft")):
        for p, Pr in (("lf", "Lf"), ("pre", "Pre"), ("sa", "Sa")):
            N.add(f"Sv{C}Ll{Pr}", dec(V[("all", span, c, p)]["ll"], 4), f"shot_value_validation all {c} {p}: log loss per attempt ({span})")
        r = V[("all", span, c, "sa")]
        N.add(f"SvD{C}", dec(r["d"] * 1000, 1), f"shot_value_validation all {c} sa: log loss minus lf, x 1,000")
        N.add(f"SvD{C}Lo", dec(r["lo"] * 1000, 1), "the same: 95% interval resampling games, low")
        N.add(f"SvD{C}Hi", dec(r["hi"] * 1000, 1), "high")
    per = [V[k]["d"] for k in V if k[0] == k[1] and k[2] == "fg" and k[3] == "sa"]
    perx = [(V[k]["lo"], V[k]["hi"]) for k in V if k[0] == k[1] and k[2] == "fg" and k[3] == "sa"]
    N.claim(len(per) == 6 and all(h < 0 for _l, h in perx), "Shot value: the shooter term lowers field-goal log loss in every season, outside the interval")
    N.add("SvDFgMin", dec(min(per) * 1000, 1), "shot_value_validation per season fg sa - lf x 1,000, most negative")
    N.add("SvDFgMax", dec(max(per) * 1000, 1), "the same, least negative")
    for c, C in (("rim", "Rim"), ("three", "Three")):
        N.add(f"SvD{C}", dec(V[("all", span, c, "sa")]["d"] * 1000, 1), f"shot_value_validation all {c} sa - lf x 1,000")
    yty = {}
    for k, v in rows(cur, "SELECT price, corr FROM shot_value_validation WHERE cls = 'yty'"):
        yty.setdefault(k, []).append(v)
    for k, K in (("skill_pts", "Skill"), ("above_pts", "Above")):
        N.add(f"SvYty{K}Min", dec(min(yty[k]), 2), f"shot_value_validation yty {k}: year-to-year r of the player-season value, smallest pair")
        N.add(f"SvYty{K}Max", dec(max(yty[k]), 2), "the same, largest pair")
    N.claim(min(yty["skill_pts"]) > 0.9 and max(abs(x) for x in yty["above_pts"]) < 0.1,
            "Shot value: skill repeats year to year (r > 0.9); what a player makes beyond it does not (|r| < 0.1)")
    cs = {}
    for v in ("sa_single", "lf_single"):
        b = rows(cur, """SELECT s.skill_pts / s.fga, s.above_pts / s.fga, p.xrapm - p.rapm FROM paper_xrapm_players p
                         JOIN shot_value_added s ON s.player_id = p.player_id AND s.season = p.season
                         WHERE p.version = %s AND p.qualified AND p.rapm IS NOT NULL AND s.fga >= 200
                         ORDER BY p.season, p.player_id""", (v,))
        sk, ab, dl = (list(c) for c in zip(*b))
        cs[v] = (_corr(sk, dl), _corr(ab, dl), len(b))
    N.add("SvDeltaPairs", integer(cs["sa_single"][2]), "qualified player-seasons with 200+ attempts in shot_value_added")
    N.add("SvDeltaSkillSa", dec(cs["sa_single"][0], 2), "corr(skill per attempt, shooter-aware one-season xRAPM - RAPM)")
    N.add("SvDeltaSkillLf", dec(cs["lf_single"][0], 2), "corr(skill per attempt, look-ahead-free shooter-blind xRAPM - RAPM)")
    N.add("SvDeltaAboveSa", dec(cs["sa_single"][1], 2), "corr(made beyond skill per attempt, shooter-aware xRAPM - RAPM)")
    N.claim(abs(cs["sa_single"][0]) < 0.1 and cs["lf_single"][0] < -0.3 and cs["sa_single"][1] < -0.2,
            "Results: the shooter-aware rating's change no longer tracks shooting skill, but still tracks what a player made beyond it")


def lineup_predictor(cur, N):
    """Round 6 step 9: the Lineup Predictor (scripts/build_lineup_predictor.py): share of the real spread of new lineups'
    net ratings explained, on lineups first used after game 20."""
    N.start("Lineup Predictor (Section: lineups; round 6 step 9) -- lineup_predictor_units, _tests, _fit")
    n, s0, s1 = one(cur, "SELECT count(*), min(season), max(season) FROM lineup_predictor_units")
    N.add("LpUnits", integer(n), "count(*) lineup_predictor_units (five x team x season)")
    N.add("LpFirstSeason", season(s0), "min(season) lineup_predictor_units")
    fit = {k: (v, d) for k, v, d in rows(cur, "SELECT name, value, detail FROM lineup_predictor_fit WHERE fit_on = '' AND model = ''")}
    N.add("LpLaterGame", integer(float(fit["const:later_after"][0])), "lineup_predictor_fit const:later_after (later = first used after this game)")
    N.add("LpNoiseModel", dec(float(fit["const:noise_check_model_var"][0]), 1), "lineup_predictor_fit noise check: real variance under the noise model")
    N.add("LpNoiseSplit", dec(float(fit["const:noise_check_split_cov"][0]), 1), "the same from the split-half covariance (no noise assumption)")
    nm, ns = float(fit["const:noise_check_model_var"][0]), float(fit["const:noise_check_split_cov"][0])
    N.claim(ns > nm, "Lineups: the split-half check puts the real variance above the noise model's, so the shares may read high")
    N.add("LpNoiseOverPct", dec((ns / nm - 1) * 100, 0), "(split-half / noise-model real variance - 1) x 100: how far too high the shares may read (%)")
    P = Pairs(cur, N, "lineup_predictor_tests", "variant = 'later'")
    r = P.value("LpSumPct", "lineup", "test", "r2_true", "sum", 1, "later", scale=100, lo_hi=True)
    N.add("LpTestUnits", integer(r["n"]), "lineup_predictor_tests test later: lineups")
    P.value("LpFullPct", "lineup", "test", "r2_true", "full", 1, "later", scale=100, lo_hi=True)
    P.value("LpTeamPct", "lineup", "test", "r2_true", "team", 1, "later", scale=100)
    fs = {ph: P.diff(f"LpDFit{Ph}", "lineup", ph, "r2_true", "fit", "scaled", 1, "later", scale=100, p=False, lo_hi=(ph == "test"))
          for ph, Ph in PHASE_NAMES}
    sf = {ph: P.diff(f"LpDSeason{Ph}", "lineup", ph, "r2_true", "full", "fit", 1, "later", scale=100, p=(ph == "test"), lo_hi=(ph == "test"))
          for ph, Ph in PHASE_NAMES}
    N.claim(not any(excl(r) for r in fs.values()), "Results: spacing, roles and usage add nothing outside the interval in any phase")
    N.claim(all(r["diff"] > 0 and excl(r) for r in sf.values()), "Results: the season so far adds to the prediction in every phase, outside the interval")
    sr = P.diff("LpDScaledSum", "lineup", "test", "r2_true", "scaled", "sum", 1, "later", scale=100, p=False)
    tb = P.diff("LpDTrackerBpm", "lineup", "test", "r2_true", "scaled_tracker", "scaled_bpm", 1, "later", scale=100, p=False)
    rb = P.get("lineup", "test", "r2_true", "scaled_rapm", "scaled_bpm", "later")
    N.claim(not excl(sr) and not excl(tb) and not excl(rb), "Results: rescaling, and the RAPM or tracker sums instead of BPM's, change nothing outside the interval")


def availability(cur, N):
    """Round 6 step 6: availability-aware pre-game odds (scripts/build_pregame_availability.py), paired with the protocol's
    pre-game model on the same games."""
    N.start("Availability-aware odds (Section: pre-game; round 6 step 6) -- pregame_availability_tests")
    P = Pairs(cur, N, "pregame_availability_tests")
    d = {ph: P.diff(f"AvD{Ph}", "pregame", ph, "log_loss", "avail_bpm", "prior_rest", 4, dm=(ph == "test")) for ph, Ph in PHASE_NAMES}
    N.claim(all(r["diff"] < 0 and excl(r) for r in d.values()), "Results: knowing who played lowers log loss in every phase, outside the interval")
    P.value("AvLlTestAvail", "pregame", "test", "log_loss", "avail_bpm", 4)
    P.value("AvLlTestBase", "pregame", "test", "log_loss", "prior_rest", 4)
    N.claim(abs(P.get("pregame", "test", "log_loss", "prior_rest")["value_a"] - P.get("pregame", "test", "log_loss", "avail_bpm",
                                                                                     "prior_rest")["value_b"]) < 1e-12,
            "the base log loss is the paired row's")
    b = P.diff("AvDTestBrier", "pregame", "test", "brier", "avail_bpm", "prior_rest", 4, p=False)
    br = P.diff("AvDTestBpmRapm", "pregame", "test", "log_loss", "avail_bpm", "avail_rapm", 4, p=False)
    al = P.diff("AvDTestAll", "pregame", "test", "log_loss", "avail_bpm_all", "avail_bpm", 4, p=False, lo_hi=False)
    N.claim(b["diff"] < 0 and excl(b) and not excl(br) and not excl(P.get("pregame", "test", "log_loss", "avail_bpm_all", "avail_bpm")),
            "Results: Brier agrees; BPM and RAPM ratings of who played are indistinguishable; counting every appearance adds nothing")
    v = eval_metrics(cur)("pregame", "test", "prior_rest", "log_loss")[0]
    N.claim(abs(v - P.get("pregame", "test", "log_loss", "prior_rest")["value_a"]) < 1e-12,
            "the availability base model is the protocol's chosen pre-game form on the same games")


def ledger(cur, N):
    """Round 6 steps 1-2: the Forecast Ledger (scripts/ledger_lock.py, ledger_update.py). The locked forecast and its
    hindcast; the forward test is read from ledger_game_log / ledger_results as of the last nightly run."""
    N.start("Forecast Ledger (Section: pre-game, a forecast locked in advance; round 6 steps 1-2) -- ledger_lock, ledger_meta, "
            "ledger_game_log, ledger_runs")
    sea, sha, tag, commit, locked, tip = one(cur, "SELECT season, lock_sha256, code_tag, code_commit, locked_at, first_tip_utc FROM ledger_lock")
    meta = {k: v for k, v in rows(cur, "SELECT key, value FROM ledger_meta")}
    N.add("LgSeason", season(sea), "ledger_lock.season")
    N.add("LgLockDate", locked.astimezone(timezone.utc).strftime("%Y-%m-%d"), "ledger_lock.locked_at (UTC date)")
    N.add("LgFirstTip", tip.astimezone(timezone.utc).strftime("%Y-%m-%d"), "ledger_lock.first_tip_utc (UTC date)")
    days = (tip - locked).total_seconds() / 86400
    N.claim(days > 0, "Ledger: the forecast was locked before the first tip")
    N.add("LgDaysBefore", dec(days, 1), "first_tip_utc - locked_at, days")
    N.add("LgHashShort", sha[:16], "ledger_lock.lock_sha256, first 16 of 64 hex digits (SHA-256 of ledger_lib.canonical_csv)")
    N.add("LgTag", tag, "ledger_lock.code_tag (git tag of the frozen code)")
    N.add("LgCommit", commit[:7], "ledger_lock.code_commit, short")
    games, = one(cur, "SELECT count(*) FROM ledger_forecasts WHERE season = %s AND forecast = 'roster' AND kind = 'game'", (sea,))
    N.claim(games == int(meta["schedule_counted"]), "Ledger: every scheduled game with both teams known is forecast")
    N.add("LgGames", integer(games), "ledger_forecasts roster game rows (= ledger_meta.schedule_counted)")
    N.add("LgRuns", integer(int(meta["runs"])), "ledger_meta.runs (simulated seasons per forecast)")
    N.add("LgRosterA", dec(float(meta["roster_a"]), 2), "ledger_meta.roster_a (weight on centred team BPM of the roster)")
    N.add("LgRosterC", dec(float(meta["roster_c"]), 2), "ledger_meta.roster_c (weight on last season's SRS)")
    h = as_json(meta["hindcast"])
    N.add("LgHcTeamSeasons", integer(h["team_seasons"]), "ledger_meta.hindcast.team_seasons")
    N.add("LgHcGames", integer(h["games"]), "ledger_meta.hindcast.games")
    f, l = h["seasons"].split("-")
    N.add("LgHcFirst", season(int(f)), "ledger_meta.hindcast.seasons (first target season)")
    N.add("LgHcLast", season(int(l)), "ledger_meta.hindcast.seasons (last)")
    for k, K, d in (("srs_rmse", "Srs", 2), ("wins82_mae", "Wins", 2), ("game_log_loss", "Ll", 4)):
        r = h[k]
        N.add(f"LgHc{K}Roster", dec(r["a"], d), f"ledger_meta.hindcast.{k}.a (roster-aware, leave-one-season-out)")
        N.add(f"LgHc{K}AsIs", dec(r["b"], d), f"ledger_meta.hindcast.{k}.b (as is: the simulator's prior)")
        N.add(f"LgHcD{K}", dec(r["diff"], d), f"ledger_meta.hindcast.{k}.diff")
        N.add(f"LgHcD{K}Lo", dec(r["ci_lo"], d), f"ledger_meta.hindcast.{k}.ci_lo (cluster bootstrap by season)")
        N.add(f"LgHcD{K}Hi", dec(r["ci_hi"], d), f"ledger_meta.hindcast.{k}.ci_hi")
        N.claim(r["ci_hi"] < 0, f"Ledger: the roster-aware forecast beats the as-is one on {k} in the hindcast, outside the interval")
    last_run, = one(cur, "SELECT max(started_at) FROM ledger_runs")
    N.add("LgAsOf", last_run.astimezone(timezone.utc).strftime("%Y-%m-%d"), "max(ledger_runs.started_at), UTC date")
    import ledger_live as LL       # the page's own scoring query (api/ledger_live.py: pandas only, no model code)
    scored = int(LL.common(LL.scored(cur.connection, sea)).espn_id.nunique())
    N.add("LgScored", integer(scored), "ledger_live.common(scored()): games final and scored under every version, as of LgAsOf")
    N.claim(scored == 0, "Pre-game: no 2026-27 game has been scored yet -- once games are scored, rewrite the forward-test sentence "
                         "with ledger_tests' intervals (and drop this claim)")


def report_card(cur, N):
    """Round 6 step 10: the Model Report Card (scripts/build_report_card.py, api/report_card_lib.py): every model season by
    season with a rolling origin, pooled by random effects (DerSimonian-Laird, Hartung-Knapp interval, prediction interval
    for a new season)."""
    N.start("Model Report Card (Section: season by season; round 6 step 10) -- report_card_pooled, report_card_tests")
    cols = ("task", "metric", "variant", "model_a", "model_b", "k", "seasons", "mu", "ci_lo", "ci_hi", "p", "tau", "i2", "pi_lo", "pi_hi",
            "a_better", "b_better", "a_clear", "b_clear", "flips")
    R = {}
    for r in rows(cur, f"SELECT {', '.join(cols)} FROM report_card_pooled"):
        r = dict(zip(cols, r))
        R[(r["task"], r["metric"], r["variant"], r["model_a"], r["model_b"])] = r

    def get(task, metric, a, b, variant=""):
        r = R.get((task, metric, variant, a, b))
        if r is not None:
            return r
        r = R[(task, metric, variant, b, a)]
        return dict(r, model_a=a, model_b=b, mu=-r["mu"], ci_lo=-r["ci_hi"], ci_hi=-r["ci_lo"],
                    pi_lo=None if r["pi_hi"] is None else -r["pi_hi"], pi_hi=None if r["pi_lo"] is None else -r["pi_lo"],
                    a_better=r["b_better"], b_better=r["a_better"], a_clear=r["b_clear"], b_clear=r["a_clear"])

    def put(name, task, metric, a, b, d, variant="", scale=1, pi=False, wins=True, p=False, tau=False):
        r = get(task, metric, a, b, variant)
        w = f"report_card_pooled {task} {metric} {a} - {b}" + (f", {variant}" if variant else "") + f" ({r['seasons']}, k {r['k']})"
        N.add(name, dec(r["mu"] * scale, d), w + ": mu (random-effects mean of the per-season differences)" + (f" x {scale}" if scale != 1 else ""))
        N.add(name + "Lo", dec(r["ci_lo"] * scale, d), w + ": ci_lo (Hartung-Knapp, t with k-1 df)")
        N.add(name + "Hi", dec(r["ci_hi"] * scale, d), w + ": ci_hi")
        if wins:
            N.add(name + "Wins", word(r["a_better"]), w + f": seasons where {a} scores better")
        if p:
            N.add(name + "P", pval(r["p"]), w + ": p")
        if pi:
            N.add(name + "PiLo", dec(r["pi_lo"] * scale, d), w + ": prediction interval for a new season, low (t with k-2 df)")
            N.add(name + "PiHi", dec(r["pi_hi"] * scale, d), w + ": high")
        if tau:
            N.add(name + "Tau", dec(r["tau"] * scale, 2), w + ": tau (between-season SD of the true difference)")
            N.add(name + "IsqPct", pct(r["i2"], 0), w + ": I^2")
        return r
    pre = get("pregame", "log_loss", "prior_rest", "current")
    span = pre["seasons"].split(" to ")
    N.add("RcPreSeasons", integer(pre["k"]), "report_card_pooled pregame: seasons scored")
    N.add("RcPreFirst", season(span[0]), "report_card_pooled pregame: first season")
    imp = get("impact_next", "game_rmse", "bpm", "rapm_prior")
    N.add("RcImpactSeasons", word(imp["k"]), "report_card_pooled impact_next: seasons scored")
    N.add("RcImpactFirst", season(imp["seasons"].split(" to ")[0]), "report_card_pooled impact_next: first season")
    xs = get("xfg", "log_loss", "sa", "lf")
    N.add("RcShotSeasons", word(xs["k"]), "report_card_pooled xfg: seasons")
    N.add("RcShotFirst", season(xs["seasons"].split(" to ")[0]), "report_card_pooled xfg: first season")
    pc = put("RcPreChosenCurrent", "pregame", "log_loss", "prior_rest", "current", 1, scale=1000, pi=True)
    pr = put("RcPreRest", "pregame", "log_loss", "prior_rest", "prior", 1, scale=1000, pi=True)
    N.claim(all(r["pi_hi"] < 0 for r in (pc, pr)) and pc["a_better"] >= pc["k"] - 1 and pr["a_better"] >= pr["k"] - 1,
            "Season by season: the chosen pre-game form's edges hold in all but at most one season, and a new season's prediction interval excludes zero")
    sb = put("RcSimBrier", "sim_playoffs", "brier", "model", "record", 4, "halfway", tau=True)
    N.add("RcSimBrierLoses", word(sb["b_better"]), "report_card_pooled sim_playoffs brier halfway: seasons where the record scores better")
    N.claim(sb["ci_lo"] < 0 < sb["ci_hi"] and abs(sb["a_better"] - sb["b_better"]) <= 1,
            "Season by season: the simulator and the record carried forward split the seasons on playoff Brier and the pooled difference is inside its interval")
    sm = put("RcSimMae", "sim_wins", "mae", "model", "record", 2, "halfway")
    sc = put("RcSimCover", "sim_wins", "cover80", "model", "record", 1, "halfway", scale=100)
    N.claim(sm["ci_hi"] < 0 and sc["ci_lo"] > 0, "Season by season: the simulator's win totals and ranges beat the record's in pooled terms, outside the interval")
    bp = put("RcBpmPrior", "impact_next", "game_rmse", "bpm", "rapm_prior", 2, pi=True, tau=True)
    N.add("RcBpmPriorLoses", word(bp["b_better"]), "seasons where RAPM + prior scores better")
    # Split 2-2 until round 8 step 6b's rebuild; 1-3 since (RAPM + prior ahead in three seasons), pooled still inside its interval.
    N.claim(bp["ci_lo"] < 0 < bp["ci_hi"] and bp["pi_lo"] < 0 < bp["pi_hi"] and bp["a_better"] >= 1 and bp["b_better"] >= 1,
            "Season by season: each of BPM and RAPM + prior wins some seasons; the pooled difference and a new season's prediction interval include zero")
    gaps = [abs(r[0]) for r in rows(cur, """SELECT diff FROM paper_eval_tests WHERE task = 'impact_next' AND metric = 'game_rmse'
                                             AND model_a = 'rapm_prior' AND model_b = 'bpm' AND variant = ''""")]
    N.claim(len(gaps) == 3 and bp["tau"] >= 0.5 * max(gaps),
            "Abstract: the true BPM - RAPM + prior difference varies between seasons (tau) by about as much as the protocol's gaps")
    tb = put("RcTrackerBpm", "impact_next", "game_rmse", "rapm_tracker", "bpm", 2, p=True)
    N.claim(tb["ci_lo"] < 0 < tb["ci_hi"] and tb["a_better"] == tb["k"] - 1, "Season by season: the tracker is ahead of BPM in all but one season, the pooled difference inside its interval")
    lost = [se for se, d in rows(cur, """SELECT season, diff FROM report_card_tests WHERE task = 'impact_next' AND metric = 'game_rmse'
                                          AND variant = '' AND model_a = 'bpm' AND model_b = 'rapm_tracker'""") if d < 0]
    test_end = one(cur, "SELECT max(season) FROM paper_eval_predictions WHERE phase = 'test'")[0]
    N.claim(lost == [test_end], "Season by season: the one season the tracker loses to BPM by game margins is the protocol's test season")
    tp = put("RcTrackerPrior", "impact_next", "game_rmse", "rapm_tracker", "rapm_prior", 2)
    ps = put("RcPriorSingle", "impact_next", "game_rmse", "rapm_prior", "rapm_single", 2)
    N.claim(ps["ci_hi"] < 0 and ps["a_better"] == ps["k"], "Season by season: the prior beats one-season RAPM every season, outside the interval")
    xb = put("RcXsaBpm", "impact_next", "game_rmse", "xrapm_sa_prior", "bpm", 2)
    N.claim(xb["a_better"] == 0, "Season by season: shooter-aware xRAPM + prior is behind BPM every season")
    pp = put("RcPossTrackerBpm", "impact_poss", "poss_rmse", "rapm_tracker", "bpm", 2, scale=10000, p=True)
    pq = put("RcPossTrackerPrior", "impact_poss", "poss_rmse", "rapm_tracker", "rapm_prior", 2, scale=10000, p=True)
    # Every season and outside the interval until round 8 step 6b's rebuild (R8-076); since, most seasons and inside it.
    N.claim(pp["a_better"] > pp["k"] / 2 and pq["a_better"] > pq["k"] / 2 and pp["ci_lo"] < 0 < pp["ci_hi"] and pq["ci_lo"] < 0 < pq["ci_hi"],
            "Season by season: per possession the tracker is ahead of BPM and of RAPM + prior in most seasons, neither pooled difference outside its interval")
    sh = put("RcShotSaLf", "xfg", "log_loss", "sa", "lf", 1, scale=1000)
    N.claim(sh["a_better"] == sh["k"] and sh["ci_hi"] < 0, "Season by season: the shooter-aware price beats the shooter-blind one every season")
    k = rows(cur, "SELECT count(DISTINCT task || metric || variant || model_a || model_b) FROM report_card_pooled")[0][0]
    N.add("RcPairs", integer(k), "report_card_pooled rows (pairs pooled across seasons)")


def coaching(cur, N):
    """Round 6 step 5: Coaching Decisions (scripts/build_coaching_decisions.py, api/coaching_lib.py): four coaching beliefs
    under round 5's permutation + Benjamini-Hochberg test, on the corrected clock."""
    N.start("Coaching decisions (Section: popular beliefs; round 6 step 5) -- coaching_decision_tests, _summary, _meta")
    S = {k: dict(zip(("units", "p05", "expected", "survivors"), r)) for k, *r in rows(
        cur, "SELECT key, units, p05, expected, survivors FROM coaching_decision_summary")}
    lg = S["coaching:league"]
    N.add("CoLeagueN", word(lg["units"]), "coaching_decision_summary coaching:league units")
    N.add("CoLeagueKfive", word(lg["p05"]), "decisions at p < 0.05")
    N.add("CoLeagueExp", dec(lg["expected"], 1), "0.05 x units")
    N.add("CoLeagueKfdr", word(lg["survivors"]), "surviving Benjamini-Hochberg at 5%")
    for key, K in (("timeout:team", "TeamTimeout"), ("challenge:team", "TeamChal"), ("twoforone:team", "TeamTwo")):
        N.add(f"Co{K}N", integer(S[key]["units"]), f"coaching_decision_summary {key} units (franchises)")
        N.add(f"Co{K}Kfdr", word(S[key]["survivors"]), f"coaching_decision_summary {key} survivors")
    cols = ("family", "key", "n_treated", "n_control", "stat", "ci_lo", "ci_hi", "treated_mean", "control_mean", "p", "survives")
    T = {(r[0], r[1]): dict(zip(cols, r)) for r in rows(cur, f"SELECT {', '.join(cols)} FROM coaching_decision_tests WHERE level = 'league'")}

    def put(name, fam, key, d, scale=1, p=False, n=False):
        r = T[(fam, key)]
        w = f"coaching_decision_tests league {fam} {key}"
        N.add(name, dec(r["stat"] * scale, d), w + ": stat (decision minus matched moments, stratified)" + (f" x {scale}" if scale != 1 else ""))
        N.add(name + "Lo", dec(r["ci_lo"] * scale, d), w + ": ci_lo")
        N.add(name + "Hi", dec(r["ci_hi"] * scale, d), w + ": ci_hi")
        if p:
            N.add(name + "P", pval(r["p"]), w + ": permutation p")
            N.add(name + "Pv", pcell(r["p"]), w + ": permutation p as a table cell")
        if n:
            N.add(name + "Treated", integer(r["n_treated"]), w + ": decisions")
            N.add(name + "Control", integer(r["n_control"]), w + ": matched moments without it")
        return r
    ch = put("CoChalWp", "coaching:league", "challenge", 1, scale=100, p=True, n=True)
    put("CoChalPts", "secondary:challenge", "outcome_b", 2)
    tw = put("CoTwoPts", "coaching:league", "twoforone", 2, p=True, n=True)
    to = put("CoTimeoutPts", "coaching:league", "timeout", 2, p=True, n=True)
    td = T[("sensitivity:timeout", "dead_ball")]
    N.add("CoTimeoutDeadPts", dec(td["stat"], 2), "coaching_decision_tests sensitivity:timeout dead_ball: stat (only timeouts at a dead ball by the corrected clock)")
    fu = put("CoFoulWp", "coaching:league", "foul_up3", 1, scale=100, p=True, n=True)
    th = T[("secondary:foul_up3", "outcome_c")]
    N.add("CoFoulThreeFouledPct", pct(th["treated_mean"], 1), "secondary:foul_up3 outcome_c treated_mean (offence makes a three, fouled)")
    N.add("CoFoulThreeDefendedPct", pct(th["control_mean"], 1), "the same, defended")
    surv = {k for (f, k), r in T.items() if f == "coaching:league" and r["survives"]}
    N.claim(surv == {"challenge", "twoforone"} and ch["stat"] > 0 and tw["stat"] > 0 and to["p"] > 0.05 and fu["p"] > 0.05,
            "Popular beliefs: of the four coaching decisions, a won challenge and shooting early for a 2-for-1 survive; the timeout and fouling up 3 do not")
    meta = {k: v for k, v, _n in rows(cur, "SELECT key, value, note FROM coaching_decision_meta")}
    N.add("CoRunMin", word(int(float(meta["param:run_min"]))), "coaching_decision_meta param:run_min (run size, points unanswered)")
    N.add("CoTimeoutPoss", word(int(float(meta["param:k_possessions"]))), "coaching_decision_meta param:k_possessions (possessions after)")
    N.claim(float(meta["balance:foul_up3:exp_margin"]) > 0, "Popular beliefs: teams that foul up three were the stronger side before the game (matched strata)")
    N.add("CoChalSuccessPct", pct(float(meta["challenge:success_rate"]), 1), "coaching_decision_meta challenge:success_rate (won / with an outcome)")


SECTIONS = (data_and_pipeline, rapm, shot_quality, pregame_and_sim, luck, awards, protocol, tests, xrapm, data_audit, beliefs, ablations,
            possessions_clock, data_quality_effect, rating_tracker, shot_value, lineup_predictor, availability, ledger, report_card,
            coaching, manifest)


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
