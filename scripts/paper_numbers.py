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

RECORDED: a few data-quality counts describe a repair that has already been
applied (the wrong-player events) or need a full play-by-play parse to
recount (missed threes the text called twos). They are copied from the
README entry that measured them, with its date, and marked RECORDED in the
output; round 5 step 5 (the data-quality audit) replaces them with live
measurements.

Read-only: one read-only autocommit session, no table is created or changed.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_numbers.py               # writes ../paper/numbers.tex
    cd scripts && python3 paper_numbers.py --check       # also checks the paper's macros
    cd scripts && python3 paper_numbers.py --out FILE    # write elsewhere
"""

import argparse
import json
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

# Measured once, not recomputable from the current tables without a full
# re-parse or from data a repair has since corrected. Round 5 step 5 replaces
# each with a live count.
RECORDED = {
    "WrongPlayerEvents": (66, "integer", "README Known real gaps 'ESPN names matched to the wrong player' (2026-09-29): "
                          "repair_espn_player_ids.py fixed 66 events (Keon Johnson 59, Jalen McDaniels 7); already repaired"),
    "WrongPlayerGames": (9, "word", "same entry: 5 + 4 games"),
    "MissedThreesAsTwos": (8688, "integer", "README Just shipped 'Game Log three-point attempts fixed' (2026-09-29): "
                           "pbp_lineups.miss_three_calls(), NBA chart three / text two, needs a full parse"),
    "MissedThreesMedianFt": (26, "integer", "same entry: median distance of those shots"),
    "TagTextDisagree": (65, "integer", "README Known real gaps (2026-09-29): 'about 65 single events' where ESPN's "
                        "tag and text name different players; repair_espn_player_ids.py docstring"),
}


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
               "% Values marked RECORDED were measured once (see paper_numbers.RECORDED) and are not re-read.", ""]
        last = None
        for section, name, value, source in self.items:
            if section != last:
                out += ["", f"% ---- {section}"]
                last = section
            out.append(f"\\newcommand{{\\{name}}}{{{value}}}% {source}")
        return "\n".join(out).lstrip("\n") + "\n"


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

    N.start("Data quality counts measured once (RECORDED; round 5 step 5 re-measures)")
    for name, (value, fmt, source) in RECORDED.items():
        N.add(name, word(value) if fmt == "word" else integer(value), "RECORDED " + source)

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

    models = {"bpm": "Bpm", "rapm_prior": "Prior", "rapm_multi": "Multi", "rapm_single": "Single", "zero": "Zero",
              "onoff": "Onoff"}
    tests = {"held_out_games": "Held", "next_season": "Next"}
    got = {}
    for test, model, rmse, r in rows(cur, """SELECT test, model, avg(game_rmse), avg(game_corr) FROM rapm_validation
                                             WHERE test IN ('held_out_games', 'next_season') GROUP BY 1, 2"""):
        got[(test, model)] = (rmse, r)
    for test, t in tests.items():
        for model, m in models.items():
            rmse, r = got[(test, model)]
            src = f"avg over seasons of rapm_validation.game_rmse / game_corr, test {test}, model {model}"
            N.add(f"Rapm{t}{m}Rmse", dec(rmse, 2), src)
            N.add(f"Rapm{t}{m}R", dec(r, 2), src)
    nx = {m: got[("next_season", m)][0] for m in models}
    N.claim(nx["onoff"] > nx["zero"], "Results: on/off as published is worse than predicting zero next season")
    N.claim(nx["rapm_single"] > nx["rapm_prior"], "Results: plain single-season RAPM is worse than RAPM + prior")
    N.claim(abs(nx["rapm_prior"] - nx["bpm"]) < 0.1, "abstract/Results: RAPM + prior 'as well as' / 'matches' BPM (next season)")

    for test, t in tests.items():
        lo, hi = one(cur, "SELECT min(scale_fit), max(scale_fit) FROM rapm_validation WHERE model = 'onoff' AND test = %s", (test,))
        N.add(f"OnoffScale{t}Min", dec(lo, 2), f"min rapm_validation.scale_fit, model onoff, test {test}")
        N.add(f"OnoffScale{t}Max", dec(hi, 2), f"max of the same")

    yty = dict(rows(cur, "SELECT model, avg(corr) FROM rapm_validation WHERE test = 'year_to_year' GROUP BY 1"))
    for model, m in (("bpm", "Bpm"), ("rapm_prior", "Prior"), ("rapm_single", "Single"), ("onoff", "Onoff")):
        N.add(f"RapmYty{m}", dec(yty[model], 2), f"avg over seasons of rapm_validation.corr, test year_to_year, model {model}")
    N.claim(yty["bpm"] > yty["rapm_prior"] > yty["rapm_single"] > yty["onoff"],
            "Results: year-to-year order BPM > RAPM+prior > single RAPM > on/off")


def shot_quality(cur, N):
    N.start("Expected FG% and shot-making (Methods, Table: shot, abstract)")
    hold = {m: (n, ll, br, auc, as_json(notes)) for m, n, ll, br, auc, notes in rows(cur, """
        SELECT model_type, n_test, log_loss, brier, roc_auc, notes FROM shot_making_validation WHERE scope = 'holdout'""")}
    n_test = {v[0] for v in hold.values()}
    N.claim(len(n_test) == 1, "every holdout model is scored on the same shots")
    N.add("XfgHoldoutShots", integer(n_test.pop()), "shot_making_validation.n_test, scope holdout")
    hs = hold["hgb"][4]["holdout_season"]
    N.add("XfgHoldoutSeason", season(hs), "shot_making_validation.notes.holdout_season")
    N.add("XfgConstantSeason", season(int(hs[:4])), "the season before the holdout (the constant's league FG%)")
    for model, m in (("constant", "Const"), ("zone_baseline", "Zone"), ("logreg", "Logreg"), ("hgb", "Hgb")):
        _, ll, br, auc, _ = hold[model]
        src = f"shot_making_validation scope holdout, model_type {model}"
        N.add(f"Xfg{m}LogLoss", dec(ll, 4), src + ": log_loss")
        N.add(f"Xfg{m}Brier", dec(br, 4), src + ": brier")
        N.add(f"Xfg{m}Auc", dec(auc, 3), src + ": roc_auc")
    best = hold["hgb"]
    N.claim(all(best[1] < v[1] and best[2] < v[2] and best[3] > v[3] for k, v in hold.items() if k != "hgb"),
            "Table shot: gradient boosting best on log loss, Brier and AUC (bold)")

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
    N.start("Pre-game odds and season simulation (Methods, Results, Table: sim)")
    carry = one(cur, "SELECT value FROM season_sim_params WHERE name = 'carry'")[0]
    N.add("SimCarry", dec(carry, 2), "season_sim_params.carry (weight on last season's rating)")
    N.add("SimPlayInFirstSeason", season(season_sim_lib.PLAY_IN_FROM),
          "api/season_sim_lib.PLAY_IN_FROM (the simulator plays the play-in from this season; a rule, not a table)")
    fits = {f: (b, ll, br, fav, n, ch) for f, b, ll, br, fav, n, ch in rows(cur, """
        SELECT form, beta, loso_log_loss, loso_brier, favourite_win_rate, n, chosen FROM pregame_model_fit""")}
    chosen = [f for f, v in fits.items() if v[5]]
    N.claim(len(chosen) == 1, "one chosen pre-game form")
    c = fits[chosen[0]]
    N.add("PregameCoefs", word(len(as_json(c[0]))), f"number of coefficients of the chosen form ({chosen[0]}), pregame_model_fit.beta")
    N.add("PregameGames", integer(c[4]), "pregame_model_fit.n, chosen form")
    s0, s1, ng = one(cur, "SELECT min(season), max(season), count(*) FROM game_pregame_odds")
    N.claim(ng == c[4], "game_pregame_odds holds the games the fit reports")
    N.add("PregameFirstSeason", season(s0), "min(season) game_pregame_odds")
    N.add("PregameLastSeason", season(s1), "max(season) game_pregame_odds")
    N.add("PregameLogLoss", dec(c[1], 4), "pregame_model_fit.loso_log_loss, chosen form")
    N.add("PregameBrier", dec(c[2], 4), "pregame_model_fit.loso_brier, chosen form")
    N.add("PregameFavWinPct", pct(c[3], 1), "pregame_model_fit.favourite_win_rate, chosen form")
    N.add("PregameBaselineLogLoss", dec(fits["baseline"][1], 4), "pregame_model_fit.loso_log_loss, form baseline")
    N.add("PregameCurrentLogLoss", dec(fits["current"][1], 4), "pregame_model_fit.loso_log_loss, form current")
    N.claim(c[1] < fits["current"][1] < fits["baseline"][1], "Results: chosen < current-only < baseline on log loss")

    sim = {(m, k): (v, n) for m, k, v, n in rows(cur, """SELECT method, metric, value, n FROM season_sim_backtest_summary
                                                        WHERE checkpoint = 'halfway'""")}
    n = {sim[k][1] for k in sim if k[1] != "top6_brier"}
    N.claim(len(n) == 1, "one team-season count at the halfway checkpoint")
    N.add("SimTeamSeasons", integer(n.pop()), "season_sim_backtest_summary.n, checkpoint halfway")
    fmt = {"playoffs_brier": 4, "playoffs_log_loss": 3, "wins_mae": 2, "wins_rmse": 2, "wins_cover80": 3}
    names = {"playoffs_brier": "PlayoffBrier", "playoffs_log_loss": "PlayoffLogLoss", "wins_mae": "WinsMae",
             "wins_rmse": "WinsRmse", "wins_cover80": "CoverEighty"}
    for metric, d in fmt.items():
        for method, m in (("model", "Model"), ("record", "Record")):
            N.add(f"Sim{m}{names[metric]}", dec(sim[(method, metric)][0], d),
                  f"season_sim_backtest_summary halfway, method {method}, metric {metric}")
    for method, m in (("model", "Model"), ("record", "Record")):
        N.add(f"Sim{m}CoverPct", pct(sim[(method, "wins_cover80")][0], 1), f"same, wins_cover80 as a percentage")
    g = lambda m, k: sim[(m, k)][0]  # noqa: E731
    N.claim(g("record", "playoffs_brier") < g("model", "playoffs_brier"), "Table sim: record-only wins on playoff Brier (bold)")
    N.claim(all(g("model", k) < g("record", k) for k in ("playoffs_log_loss", "wins_mae", "wins_rmse")),
            "Table sim: simulator wins on log loss, win-total MAE and RMSE (bold)")
    N.claim(abs(g("model", "wins_cover80") - 0.8) < abs(g("record", "wins_cover80") - 0.8),
            "Table sim: simulator's 80% range closer to nominal (bold)")


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


SECTIONS = (data_and_pipeline, rapm, shot_quality, pregame_and_sim, luck, awards)


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


def check_paper(numbers_text, paper_path):
    """(undefined macros the paper uses, macros defined but unused)."""
    with open(paper_path) as f:
        paper = f.read()
    defined, used = defined_macros(numbers_text), used_macros(paper)
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
