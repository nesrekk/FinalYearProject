"""
workbench_catalogue.py
======================
One description of every dataset and stat the Workbench can query (round 7
step 1), and the single place the existing stat catalogues now come from:
the Leaderboard Builder's STATS (and through it Composite, the Regression
Explorer, the Breakout Detector, the Era Translator and the player profile)
and the Game Finder's STATS / BASE_FROM are built from the columns here
(`leaderboard_stats()`, `game_finder_stats()`), so a stat is defined once.
The query layer that runs a spec against it is api/routers/workbench.py.

Datasets (one row of each is the unit a filter sees):

    player_season  player_season_stats, 1949-50 on: per-game values (NBA.com
                   from 2009-10, Basketball-Reference before), one row per
                   player-season (a traded player under his last team from
                   2009-10, 2TM/3TM before).
    player_game    player_game_lines joined like the Game Log / Game Finder
                   (team_game_fatigue on team + date, which keeps only games
                   in the standings: the three NBA Cup finals drop out), 2020-21
                   on, rebuilt from ESPN play-by-play.
    team_season    team_seasons (Basketball-Reference), 1946-47 on, league-
                   average rows left out; the entity is the franchise.
    team_game      game_scores (real final scores) with team_game_fatigue
                   (schedule, rest, travel), 2009-10 on; from 2020-21 also
                   NBA.com's team box score (game_team_box) for possessions.

Every column says how it combines over several rows (`kind`):

    count   a counting stat. The row value is per game; combined, it is the
            summed total divided by summed games (per game), minutes (per 36)
            or possessions (per 100), or the total itself.
    sum     a total that only adds up (games, wins, VORP, attendance).
    ratio   summed numerator / summed denominator (FG% = made / attempted).
    wmean   a weighted mean of the row values, weight stated (attempts for a
            season table's shooting %, which makes it summed makes / summed
            attempts; possessions for ratings; minutes for BPM and usage).
    none    can't be combined (the table holds a rate, not the counts behind
            it); offered only one row at a time.

`first_season` is the first season a column is recorded for at least 90% of
the dataset's rows (the Leaderboard's rule; api/tests/test_workbench.py
checks it for every column); earlier rows count as not recorded. `status` is
"verified" or "excluded" with the reason shown in the UI: a stat whose source
is known to be wrong is not offered until it is fixed.

SQL fragments here are code, never user text: the query layer takes only
catalogue keys from a request and binds every value as a parameter. No
fragment may contain a percent sign (psycopg2 placeholders).

Season-table totals are per-game averages x games, and both sources publish
per-game values rounded to 0.1, so a season total can be off by up to
0.05 x games (Curry 2015-16: 30.1 x 79 = 2,378 for 2,375 points).
"""

from dataclasses import dataclass, field

from stat_samples import SEASON_SAMPLE_SQL
from teams_lib import BREF_TO_NBA, FRANCHISE_OF_BREF, NBA_TO_BREF, franchise_of

FORMATS = ("int", "num1", "num2", "pct", "signed1", "signed2")
KINDS = ("count", "sum", "ratio", "wmean", "diff", "none")
PER_MODES = {"game": "per game", "total": "totals", "per36": "per 36 minutes", "per100": "per 100 possessions"}
AGG_LABELS = {
    "count": "summed total ÷ summed games (or minutes, or possessions)",
    "sum": "summed",
    "ratio": "summed numerator ÷ summed denominator",
    "diff": "one pooled rate minus another (each summed numerator ÷ summed denominator)",
    "none": "not combined over several rows",
}

# Default minimum attempts per game for shooting percentages (the Leaderboard
# Builder's, also used by Composite, Regression, Breakouts and the Era
# Translator; similarity_api.LINE_ATTEMPTS must equal it): without one,
# 2025-26's top 3P% was 100% on 0.0 3PA a game.
MIN_ATTEMPTS_PER_GAME = {"fga": 5.0, "fg3a": 2.0, "fta": 2.0}


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    short: str
    group: str
    fmt: str                        # format of one row's value
    kind: str
    sql: str                        # one row's value (per game for season rows)
    first_season: int
    higher_is_better: bool | None = True
    total: str | None = None        # count: one row's total
    num: str | None = None          # ratio
    den: str | None = None          # ratio
    weight: str | None = None       # wmean
    weight_label: str | None = None
    n_sql: str | None = None        # sample behind the value, summed (default from the kind)
    n_unit: str | None = None
    per_modes: tuple | None = None  # count: per modes it honours (others show per game)
    attempts: str | None = None     # Leaderboard: per-game attempts column of a shooting %
    sample: str | None = None       # Stat Stability sample of one row, in its stable_n units
    stability: str | None = None    # stat_stability.stat key whose stable_n applies
    stability_scaled: bool = True   # divide stable_n by nba_unit_scale (season-table units)
    status: str = "verified"
    reason: str | None = None
    note: str | None = None
    sources: tuple = ()
    pages: tuple = ()               # existing pages that read this entry ("leaderboard", "game_finder")
    agg_fmt: str | None = None      # format once combined or scaled (default: num1 for int counts)
    agg_text: str | None = None     # how it combines, in words (default from the kind)
    num2: str | None = None         # diff: the second rate's numerator ...
    den2: str | None = None         # ... and denominator (value = num/den - num2/den2)
    ci_lo: str | None = None        # an interval the source gives for one row (models: RAPM's
    ci_hi: str | None = None        # 95% interval, a projection's 80% range), shown ungrouped only
    ci_label: str | None = None
    method: str | None = None       # Methodology card id (frontend/src/components/pages/methodologyContent.js)
    among: str | None = None        # the rows the 90% first-season rule counts, when a stat applies to only
                                    # some rows by design (a projected 3P% exists only over 50 3PA)


@dataclass(frozen=True)
class Dim:
    """A field rows can be filtered or grouped on (not a stat)."""
    key: str
    label: str
    sql: str
    type: str          # int | team | bool | date | text | players (an array of player ids in the row)
    group: bool = True
    values: tuple = ()  # text dims: the allowed values
    note: str | None = None


@dataclass(frozen=True)
class Grouping:
    """group_by key -> what it groups on and the fields each output row gets."""
    by: tuple                   # SQL expressions in GROUP BY, paired with field names
    fields: tuple               # output field names, one per `by`
    extra: tuple = ()           # (field, aggregate SQL) shown with the group, e.g. the latest name


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    entity: str                 # player | team
    description: str
    from_sql: str
    where: tuple
    season_sql: str
    entity_sql: str
    games_sql: str
    minutes_sql: str | None
    poss_sql: str | None
    per_modes: tuple
    row_label: str              # what one row is, for n ("seasons", "games")
    tables: tuple
    upstream: str
    row_fields: tuple           # (field, SQL) on every ungrouped row
    groupings: dict
    dims: dict
    columns: dict = field(default_factory=dict)
    from_params: tuple = ()     # bound parameters the from_sql needs, in order
    names_from_ids: bool = False
    notes: tuple = ()
    name_fields: tuple = ()     # (id field, name field): player ids in a row field get names
                                # (an array of ids -> an array of names)
    players_sql: str | None = None   # team-entity datasets: the row's player ids as an int array, so a
                                     # player set can pick the rows its players are in
    poss_floor: int | None = None    # default possessions floor a block offers (spec min_poss)
    optional: tuple = ()             # (tables, upstream) joined only for some columns: a response names
                                     # them only when one of its columns reads them


# ─── helpers ────────────────────────────────────────────────────────────────

def _cols(*cols):
    out = {}
    for c in cols:
        assert c.key not in out, c.key
        out[c.key] = c
    return out


# ─── player_season ──────────────────────────────────────────────────────────
PS_SOURCES = ("player_season_stats",)


def _ps_count(key, label, short, first, *, group="Per game", hib=True, pages=("leaderboard",), fmt="num1",
              per_modes=None, note=None):
    return Column(key, label, short, group, fmt, "count", f"s.{key}", first, hib, total=f"s.{key} * s.gp",
                  n_unit="games", per_modes=per_modes,
                  sample=SEASON_SAMPLE_SQL.get(key), stability=key if key in SEASON_SAMPLE_SQL else None,
                  sources=PS_SOURCES, pages=pages, note=note)


def _ps_wmean(key, label, short, group, fmt, first, weight, weight_label, n_unit, *, hib=True, attempts=None,
              pages=("leaderboard",), note=None):
    return Column(key, label, short, group, fmt, "wmean", f"s.{key}", first, hib, weight=weight,
                  weight_label=weight_label, n_unit=n_unit, attempts=attempts,
                  sample=SEASON_SAMPLE_SQL.get(key), stability=key if key in SEASON_SAMPLE_SQL else None,
                  sources=PS_SOURCES, pages=pages, note=note)


# Model outputs per player-season (round 7 step 7), each from its own table joined on (player, season).
# Every join hits a unique key, so Postgres drops the joins a query doesn't use (checked with EXPLAIN):
# box-score queries cost what they did before.
def _model_join(alias, table, where, cols):
    """A model table joined on (player, season) as a subquery that exposes only the columns used, renamed so none
    collides with an unqualified name of the season table's SQL (stat_samples.SEASON_SAMPLE_SQL says "fga * gp")."""
    picked = ", ".join(f"{c} AS {a}" if c != a else c for c, a in cols)
    cond = f" WHERE {where}" if where else ""
    return (f"\n    LEFT JOIN (SELECT player_id AS pid, season AS yr, {picked} FROM {table}{cond}) {alias} "
            f"ON {alias}.pid = s.player_id AND {alias}.yr = s.season")


_RAPM_COLS = (("rapm", "rapm"), ("orapm", "orapm"), ("drapm", "drapm"), ("orapm_se", "orapm_se"),
              ("drapm_se", "drapm_se"), ("rapm_ci_low", "rapm_ci_low"), ("rapm_ci_high", "rapm_ci_high"),
              ("poss", "n_poss"))
_TRACKER_COLS = (("rapm", "rapm"), ("orapm", "orapm"), ("drapm", "drapm"), ("orapm_sd", "orapm_sd"),
                 ("drapm_sd", "drapm_sd"), ("rapm_ci_low", "rapm_ci_low"), ("rapm_ci_high", "rapm_ci_high"),
                 ("poss", "n_poss"))
MODEL_JOINS = "".join((
    _model_join("r1", "player_rapm", "version = 'single'", _RAPM_COLS),
    _model_join("r2", "player_rapm", "version = 'prior'", _RAPM_COLS),
    _model_join("r3", "player_rapm", "version = 'multi'", _RAPM_COLS),
    _model_join("tf", "player_rating_tracker", "kind = 'filtered'", _TRACKER_COLS),
    _model_join("tsm", "player_rating_tracker", "kind = 'smoothed'", _TRACKER_COLS),
    _model_join("xp", "paper_xrapm_players", "version = 'sa_prior'", (("xrapm", "xrapm"), ("poss", "n_poss"))),
    _model_join("xs", "paper_xrapm_players", "version = 'sa_single'", (("xrapm", "xrapm"), ("poss", "n_poss"))),
    _model_join("v", "shot_value_added", None, (("sva", "sva"), ("total_pts", "total_pts"),
                                                 ("ft_total_pts", "ft_total_pts"), ("beyond", "beyond"),
                                                 ("fga", "sv_fga"), ("fta", "sv_fta"))),
))
MODEL_FIRST = 2021
Z95 = 1.959964
RAPM_GROUP = "Impact models (2020-21 on)"
CI95 = "95% interval"


def _rating(key, label, short, alias, col, *, table, first=MODEL_FIRST, ci=None, sd=None, ci_label=CI95,
            note=None, method="rapm"):
    """A model rating: one value per player-season, never combined; n = the possessions behind it."""
    lo = hi = None
    if ci:
        lo, hi = f"{alias}.{ci[0]}", f"{alias}.{ci[1]}"
    elif sd:
        lo = f"({alias}.{col} - {Z95} * {alias}.{sd})"
        hi = f"({alias}.{col} + {Z95} * {alias}.{sd})"
    return Column(key, label, short, RAPM_GROUP, "signed1", "none", f"{alias}.{col}", first, True,
                  n_sql=f"{alias}.n_poss", n_unit="possessions", ci_lo=lo, ci_hi=hi, ci_label=ci_label if lo else None,
                  method=method, sources=(table,), note=note)


RAPM_NOTE = ("Points per 100 possessions above an average player, from every five-man stint (offence and "
             "defence columns, ridge-shrunk toward zero; λ chosen by cross-validation). The interval is the "
             "fit's 95% interval.")
PRIOR_NOTE = ("RAPM shrunk toward his box-score BPM instead of zero (the same λ): steadier, and better at "
              "predicting next season.")
MULTI_NOTE = "One fit over this season and the two before (possessions weighted alike), from 2022-23."
TRACKER_NOTE = ("RAPM whose ratings carry over from season to season (a Kalman filter over all stints, BPM folded "
                "into each season's prior): as of the end of this season, using nothing later. Interval ± 1.96 SD.")
SMOOTHED_NOTE = ("The Rating Tracker in hindsight: each season's rating also uses the seasons after it (Rauch-"
                 "Tung-Striebel smoother). Better for describing a season, not a forecast.")
XRAPM_NOTE = ("RAPM fitted on expected points (each shot priced by its location and the shooter's own record "
              "before the game, free throws by his record) instead of points scored. No interval is stored for it.")

MODEL_COLUMNS = (
    _rating("rapm", "RAPM (one season)", "RAPM", "r1", "rapm", table="player_rapm", ci=("rapm_ci_low", "rapm_ci_high"),
            note=RAPM_NOTE),
    _rating("orapm", "Offensive RAPM (one season)", "ORAPM", "r1", "orapm", table="player_rapm", sd="orapm_se",
            ci_label="95% interval (± 1.96 SE)", note=RAPM_NOTE),
    _rating("drapm", "Defensive RAPM (one season)", "DRAPM", "r1", "drapm", table="player_rapm", sd="drapm_se",
            ci_label="95% interval (± 1.96 SE)", note=RAPM_NOTE),
    _rating("rapm_prior", "RAPM with a BPM prior", "RAPM+", "r2", "rapm", table="player_rapm",
            ci=("rapm_ci_low", "rapm_ci_high"), note=PRIOR_NOTE),
    _rating("orapm_prior", "Offensive RAPM with a BPM prior", "ORAPM+", "r2", "orapm", table="player_rapm",
            sd="orapm_se", ci_label="95% interval (± 1.96 SE)", note=PRIOR_NOTE),
    _rating("drapm_prior", "Defensive RAPM with a BPM prior", "DRAPM+", "r2", "drapm", table="player_rapm",
            sd="drapm_se", ci_label="95% interval (± 1.96 SE)", note=PRIOR_NOTE),
    _rating("rapm_multi", "RAPM (3-season window)", "RAPM3", "r3", "rapm", table="player_rapm", first=2023,
            ci=("rapm_ci_low", "rapm_ci_high"), note=MULTI_NOTE),
    _rating("tracker", "Rating Tracker (as of the season's end)", "Tracker", "tf", "rapm",
            table="player_rating_tracker", ci=("rapm_ci_low", "rapm_ci_high"), ci_label="95% interval (± 1.96 SD)",
            note=TRACKER_NOTE),
    _rating("tracker_o", "Rating Tracker, offence", "Trk O", "tf", "orapm", table="player_rating_tracker",
            sd="orapm_sd", ci_label="95% interval (± 1.96 SD)", note=TRACKER_NOTE),
    _rating("tracker_d", "Rating Tracker, defence", "Trk D", "tf", "drapm", table="player_rating_tracker",
            sd="drapm_sd", ci_label="95% interval (± 1.96 SD)", note=TRACKER_NOTE),
    _rating("tracker_smoothed", "Rating Tracker in hindsight", "Trk hind", "tsm", "rapm",
            table="player_rating_tracker", ci=("rapm_ci_low", "rapm_ci_high"), ci_label="95% interval (± 1.96 SD)",
            note=SMOOTHED_NOTE),
    _rating("xrapm_sa_prior", "Shot-aware xRAPM with a BPM prior", "xRAPM+", "xp", "xrapm",
            table="paper_xrapm_players", note=XRAPM_NOTE),
    _rating("xrapm_sa", "Shot-aware xRAPM (one season)", "xRAPM", "xs", "xrapm", table="paper_xrapm_players",
            note=XRAPM_NOTE),
)

SV_GROUP = "Shot value (2020-21 on)"
SV_N = "(v.sv_fga + v.sv_fta)"
SV_NOTE = ("Every regular-season shot priced before its game twice: for an average shooter, and for this shooter "
           "given his record so far (scripts/build_shot_value.py).")
SHOT_VALUE_COLUMNS = (
    Column("sva", "Shot value added", "SVA", SV_GROUP, "signed1", "sum", "v.sva", MODEL_FIRST, n_sql=SV_N,
           n_unit="shots (FGA + FTA)", method="shotmaking", sources=("shot_value_added",),
           note=SV_NOTE + " Shot value added = what his record says his shooting adds, in points over the season "
                          "(field goals and free throws): the repeatable part."),
    Column("sva_per100", "Shot value added per 100 shots", "SVA/100", SV_GROUP, "signed1", "ratio",
           f"(100.0 * v.sva / NULLIF({SV_N}, 0))", MODEL_FIRST, num="100.0 * v.sva", den=SV_N, n_unit="shots (FGA + FTA)",
           agg_text="100 × summed shot value added ÷ summed shots", method="shotmaking", sources=("shot_value_added",),
           note=SV_NOTE),
    Column("shot_pts_above", "Points above an average shooter", "Pts vs avg", SV_GROUP, "signed1", "sum",
           "(v.total_pts + v.ft_total_pts)", MODEL_FIRST, n_sql=SV_N, n_unit="shots (FGA + FTA)", method="shotmaking",
           sources=("shot_value_added",),
           note=SV_NOTE + " Points scored minus what an average shooter would score on the same shots (skill plus "
                          "this season's luck)."),
    Column("shot_beyond", "Points beyond his expected shooting", "Beyond", SV_GROUP, "signed1", "sum", "v.beyond",
           MODEL_FIRST, n_sql=SV_N, n_unit="shots (FGA + FTA)", method="shotmaking", sources=("shot_value_added",),
           note=SV_NOTE + " Points scored minus what his own record predicted: barely repeats year to year (r about 0), "
                          "so mostly luck."),
)


SHOOTING_WEIGHT = "attempts (summed makes ÷ summed attempts)"
AGE_FEB1 = "date_part('year', age(make_date(s.season, 2, 1), b.birth_date))"

PLAYER_SEASON_COLUMNS = _cols(
    # The Leaderboard Builder's 31 stats, in its order.
    _ps_count("pts", "Points", "PTS", 1950),
    _ps_count("reb", "Rebounds", "REB", 1951),
    _ps_count("ast", "Assists", "AST", 1950),
    _ps_count("stl", "Steals", "STL", 1974),
    _ps_count("blk", "Blocks", "BLK", 1974),
    _ps_count("tov", "Turnovers", "TOV", 1978, hib=False),
    _ps_count("fg3m", "3-pointers made", "3PM", 1980),
    _ps_count("fg3a", "3-point attempts", "3PA", 1980),
    _ps_count("fta", "Free-throw attempts", "FTA", 1950),
    _ps_count("oreb", "Offensive rebounds", "OREB", 1974),
    _ps_count("min", "Minutes", "MIN", 1952, per_modes=("game", "total")),
    _ps_wmean("fg_pct", "Field-goal %", "FG%", "Shooting", "pct", 1950, "s.fga * s.gp", SHOOTING_WEIGHT,
              "field-goal attempts", attempts="fga"),
    _ps_wmean("fg3_pct", "3-point %", "3P%", "Shooting", "pct", 1980, "s.fg3a * s.gp", SHOOTING_WEIGHT,
              "3-point attempts", attempts="fg3a"),
    _ps_wmean("ft_pct", "Free-throw %", "FT%", "Shooting", "pct", 1950, "s.fta * s.gp", SHOOTING_WEIGHT,
              "free-throw attempts", attempts="fta"),
    _ps_wmean("ts_pct", "True shooting %", "TS%", "Shooting", "pct", 1950, "(s.fga + 0.44 * s.fta) * s.gp",
              "shooting attempts, FGA + 0.44 × FTA (summed points ÷ summed attempts)",
              "shooting attempts (FGA + 0.44 × FTA)", attempts="fga"),
    _ps_wmean("efg_pct", "Effective FG %", "eFG%", "Shooting", "pct", 1980, "s.fga * s.gp", SHOOTING_WEIGHT,
              "field-goal attempts", attempts="fga"),
    _ps_wmean("usg_pct", "Usage %", "USG%", "Rates", "pct", 1978, "s.min * s.gp", "minutes", "minutes"),
    _ps_wmean("ast_pct", "Assist %", "AST%", "Rates", "pct", 1965, "s.min * s.gp", "minutes", "minutes"),
    _ps_wmean("reb_pct", "Rebound %", "REB%", "Rates", "pct", 1971, "s.min * s.gp", "minutes", "minutes"),
    _ps_wmean("oreb_pct", "Offensive rebound %", "OREB%", "Rates", "pct", 1974, "s.min * s.gp", "minutes",
              "minutes"),
    _ps_wmean("tov_pct", "Turnover %", "TOV%", "Rates", "pct", 1978, "(s.fga + 0.44 * s.fta + s.ast + s.tov) * s.gp",
              "plays (FGA + 0.44 × FTA + AST + TOV)", "plays", hib=False),
    _ps_wmean("off_rating", "Offensive rating", "ORTG", "Impact", "num1", 2010, "s.poss", "possessions",
              "possessions"),
    _ps_wmean("def_rating", "Defensive rating", "DRTG", "Impact", "num1", 2010, "s.poss", "possessions",
              "possessions", hib=False),
    _ps_wmean("net_rating", "Net rating", "NET", "Impact", "signed1", 2010, "s.poss", "possessions",
              "possessions"),
    Column("plus_minus", "Plus-minus", "+/-", "Impact", "signed1", "count", "s.plus_minus", 2010,
           total="s.plus_minus * s.gp", n_unit="games", sample=SEASON_SAMPLE_SQL["plus_minus"],
           stability="plus_minus", sources=PS_SOURCES, pages=("leaderboard",), agg_fmt="signed1",
           note="NBA.com's season plus-minus (the team's margin while he was on the floor), per game."),
    _ps_wmean("bpm", "BPM", "BPM", "Impact", "signed1", 1974, "s.min * s.gp", "minutes", "minutes",
              note="Basketball-Reference's published Box Plus/Minus."),
    _ps_wmean("obpm", "Offensive BPM", "OBPM", "Impact", "signed1", 1974, "s.min * s.gp", "minutes", "minutes"),
    _ps_wmean("dbpm", "Defensive BPM", "DBPM", "Impact", "signed1", 1974, "s.min * s.gp", "minutes", "minutes"),
    Column("vorp", "VORP", "VORP", "Impact", "num1", "sum", "s.vorp", 1974, n_unit="seasons", sources=PS_SOURCES,
           pages=("leaderboard",), note="Value over replacement player: a season total, so it only adds up."),
    Column("impact_score_raw", "Impact score (raw)", "Impact", "Impact", "num2", "none", "s.impact_score_raw", 2010,
           n_unit="seasons", sources=PS_SOURCES, pages=("leaderboard",),
           note="The project's own season-level impact model; one season at a time."),
    Column("age", "Age", "Age", "Other", "int", "none", "s.age", 1950, sources=PS_SOURCES, pages=("leaderboard",),
           status="excluded",
           reason=("This table's age uses two conventions (NBA.com's from 2009-10, about 45% of players a year "
                   "older than Basketball-Reference's age on February 1, used before), so ages across 2009-10 "
                   "don't compare. Use 'Age on Feb 1', from birth dates.")),
    # Beyond the Leaderboard.
    Column("gp", "Games", "GP", "Playing time", "int", "sum", "s.gp", 1950, n_unit="seasons", sources=PS_SOURCES),
    _ps_count("fgm", "Field goals made", "FGM", 1950, pages=()),
    _ps_count("fga", "Field-goal attempts", "FGA", 1950, pages=()),
    _ps_count("ftm", "Free throws made", "FTM", 1950, pages=()),
    _ps_count("dreb", "Defensive rebounds", "DREB", 1974, pages=()),
    _ps_count("pf", "Personal fouls", "PF", 1950, hib=False, pages=()),
    Column("poss", "Possessions", "POSS", "Playing time", "int", "sum", "s.poss", 2010, n_unit="seasons",
           sources=PS_SOURCES, note="NBA.com's count of the possessions he was on the floor for, a season total."),
    Column("age_feb1", "Age on Feb 1", "Age", "Other", "int", "wmean", AGE_FEB1, 1950, None, weight="s.gp",
           weight_label="games", n_unit="games", sources=("player_bio",),
           note=("Age on February 1 of the season, from player_bio's birth date (Basketball-Reference's "
                 "convention, used by Aging Curves). Combined, it's the games-weighted mean.")),
    *MODEL_COLUMNS,
    *SHOT_VALUE_COLUMNS,
)

PLAYER_SEASON = Dataset(
    key="player_season", label="Player seasons", entity="player",
    description="One row per player-season, 1949-50 on: per-game box score, shooting, rates and impact.",
    from_sql="FROM player_season_stats s LEFT JOIN player_bio b ON b.player_id = s.player_id" + MODEL_JOINS,
    where=(),
    season_sql="s.season", entity_sql="s.player_id", games_sql="s.gp", minutes_sql="s.min * s.gp",
    poss_sql="s.poss", per_modes=("game", "total", "per36", "per100"), row_label="seasons",
    tables=("player_season_stats", "player_bio"),
    upstream="nba_api (stats.nba.com) from 2009-10 + Basketball-Reference before and for BPM/VORP",
    optional=(("player_rapm", "player_rating_tracker", "paper_xrapm_players", "shot_value_added"),
              "ESPN play-by-play and NBA.com shot charts (models)"),
    row_fields=(("player_id", "s.player_id"), ("player_name", "s.player_name"), ("season", "s.season"),
                ("team", "s.team_abbreviation")),
    groupings={
        "entity": Grouping(("s.player_id",), ("player_id",),
                           (("player_name", "(array_agg(s.player_name ORDER BY s.season DESC))[1]"),)),
        "season": Grouping(("s.season",), ("season",)),
        "team": Grouping(("s.team_abbreviation",), ("team",)),
    },
    dims={
        "team": Dim("team", "Team", "s.team_abbreviation", "team",
                    note="The team on the season row: a traded player's last team from 2009-10 on, 2TM/3TM before."),
    },
    columns=PLAYER_SEASON_COLUMNS,
    notes=("Season totals, per 36 and per 100 are per-game averages × games; the sources round per-game "
           "values to 0.1, so a total can be off by up to 0.05 × games.",
           "Model ratings (RAPM, the Rating Tracker, shot-aware xRAPM) exist from 2020-21, one value per player-"
           "season with its interval; they aren't combined over several seasons (that would need the models "
           "refitted, not averaged). Players under 200 minutes × games before 2025-26 have no season row, so "
           "their (very uncertain) ratings aren't here."),
)


# ─── player_game ────────────────────────────────────────────────────────────
# The Game Log / Game Finder rows: a line he played, in a game that counts in
# the standings (the join drops the three NBA Cup finals and one 2021-22 line
# with no team).
PLAYER_GAME_FROM = """
    FROM player_game_lines l
    JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
    LEFT JOIN game_scores gs ON gs.game_id = f.game_id AND gs.team_abbreviation = f.team_abbreviation
"""
PLAYER_GAME_WHERE = ("l.seconds > 0",)
TEAM_MARGIN_SQL = "(gs.pts_for - gs.pts_against)"
ON_COURT_POSS = ("((l.tm_fga + 0.44 * l.tm_fta - l.tm_oreb + l.tm_tov) + "
                 "(l.op_fga + 0.44 * l.op_fta - l.op_oreb + l.op_tov)) / 2.0")
PG_SOURCES = ("player_game_lines",)
# On-floor points and plus-minus (round 7 step 2): from player_game_onfloor (scripts/
# build_player_game_onfloor.py), the stints' lineups and points with free throws credited to the
# lineup at the foul, as the box score does. Not player_game_lines' tm_pts / op_pts, which credit stale
# ESPN score fields (a team's five don't add up to 5x the margin in 1 team-game in 4). Shown only
# in games that reconcile (game_ok: 7,220 of 7,232).
ONFLOOR_SOURCES = ("player_game_onfloor", "lineup_stints")
ONFLOOR_NOTE = ("From the five-man stints (points from made shots and free throws, free throws credited to "
                "the players on the floor at the foul, as the box score does): equals ESPN's box-score +/- for "
                "98% of player-games, within 2 for 99.7% (300 random games, 2026-10-03). Left out of the 12 "
                "games whose play-by-play doesn't reconcile with the final score.")


def _onfloor(col):
    return f"(CASE WHEN o.game_ok THEN o.{col} END)"


GAME_FIRST = 2021


def _pg_count(key, label, short, sql=None, *, hib=True, fmt="int", sample="1", stability=None, pages=("game_finder",),
              per_modes=None, total=None, n_unit="games", group="Box score", note=None):
    sql = sql or f"l.{key}"
    return Column(key, label, short, group, fmt, "count", sql, GAME_FIRST, hib, total=total or sql, n_unit=n_unit,
                  per_modes=per_modes, sample=sample, stability=stability, stability_scaled=False,
                  sources=PG_SOURCES, pages=pages, note=note)


def _pg_ratio(key, label, short, sql, num, den, n_sql, n_unit, sample, stability, pages=("game_finder",),
              agg_text="summed makes ÷ summed attempts"):
    return Column(key, label, short, "Shooting", "pct", "ratio", sql, GAME_FIRST, True, num=num, den=den,
                  n_sql=n_sql, n_unit=n_unit, sample=sample, stability=stability, stability_scaled=False,
                  sources=PG_SOURCES, pages=pages, agg_text=agg_text)


PLAYER_GAME_COLUMNS = _cols(
    # The Game Finder's 19 stats, in its order (its SQL exactly).
    _pg_count("pts", "Points", "PTS", stability="pts"),
    _pg_count("reb", "Rebounds", "REB", "(l.oreb + l.dreb)", stability="reb"),
    _pg_count("ast", "Assists", "AST", stability="ast"),
    _pg_count("stl", "Steals", "STL", stability="stl"),
    _pg_count("blk", "Blocks", "BLK", stability="blk"),
    _pg_count("tov", "Turnovers", "TOV", hib=False, stability="tov"),
    _pg_count("oreb", "Offensive rebounds", "OREB", stability="oreb"),
    _pg_count("dreb", "Defensive rebounds", "DREB"),
    _pg_count("fgm", "Field goals made", "FGM"),
    _pg_count("fga", "Field-goal attempts", "FGA"),
    _pg_count("fg3m", "3-pointers made", "3PM", stability="fg3m"),
    _pg_count("fg3a", "3-point attempts", "3PA", stability="fg3a"),
    _pg_count("ftm", "Free throws made", "FTM"),
    _pg_count("fta", "Free-throw attempts", "FTA", stability="fta"),
    _pg_count("min", "Minutes", "MIN", "(l.seconds / 60.0)", fmt="num1", stability="min", group="Playing time",
              per_modes=("game", "total")),
    _pg_ratio("fg_pct", "Field-goal %", "FG%", "(l.fgm::float / NULLIF(l.fga, 0))", "l.fgm", "l.fga", "l.fga",
              "field-goal attempts", "l.fga", "fg_pct"),
    _pg_ratio("fg3_pct", "3-point %", "3P%", "(l.fg3m::float / NULLIF(l.fg3a, 0))", "l.fg3m", "l.fg3a", "l.fg3a",
              "3-point attempts", "l.fg3a", "fg3_pct"),
    _pg_ratio("ft_pct", "Free-throw %", "FT%", "(l.ftm::float / NULLIF(l.fta, 0))", "l.ftm", "l.fta", "l.fta",
              "free-throw attempts", "l.fta", "ft_pct"),
    _pg_ratio("ts_pct", "True shooting %", "TS%", "(l.pts / NULLIF(2 * (l.fga + 0.44 * l.fta), 0))", "l.pts",
              "2 * (l.fga + 0.44 * l.fta)", "(l.fga + 0.44 * l.fta)", "shooting attempts (FGA + 0.44 × FTA)",
              "(l.fga + 0.44 * l.fta)", "ts_pct", agg_text="summed points ÷ 2 × summed (FGA + 0.44 × FTA)"),
    # Beyond the Game Finder.
    _pg_ratio("efg_pct", "Effective FG %", "eFG%", "((l.fgm + 0.5 * l.fg3m) / NULLIF(l.fga, 0))",
              "(l.fgm + 0.5 * l.fg3m)", "l.fga", "l.fga", "field-goal attempts", "l.fga", "efg_pct", pages=()),
    Column("poss", "Possessions on the floor", "POSS", "Playing time", "num1", "count", ON_COURT_POSS, GAME_FIRST,
           total=ON_COURT_POSS, n_unit="games", per_modes=("game", "total"), sources=PG_SOURCES,
           note=("FGA + 0.44 × FTA − OREB + TOV of both teams while he was on the floor, averaged over the two "
                 "(On/Off's estimate); a team's five players add up to five times the team's possessions in "
                 "11,559 of 11,560 fully tracked games.")),
    Column("team_margin", "Team's final margin", "Margin", "Team result", "signed1", "count", TEAM_MARGIN_SQL,
           GAME_FIRST, None, total=TEAM_MARGIN_SQL, n_unit="games", per_modes=("game", "total"),
           sources=("game_scores",), agg_fmt="signed1",
           note="The real final score (ESPN's scoreboard), not on-court plus-minus."),
    Column("age_feb1", "Age on Feb 1", "Age", "Other", "int", "wmean",
           "date_part('year', age(make_date(l.season, 2, 1), b.birth_date))", GAME_FIRST, None, weight="1",
           weight_label="games", n_unit="games", sources=("player_bio",),
           note="Age on February 1 of the season, from player_bio's birth date."),
    Column("plus_minus", "On-court plus-minus", "+/-", "On the floor", "signed1", "count", _onfloor("plus_minus"),
           GAME_FIRST, total=_onfloor("plus_minus"), n_unit="games", sources=ONFLOOR_SOURCES, agg_fmt="signed1",
           note="The team's points minus the opponent's while he was on the floor. " + ONFLOOR_NOTE),
    Column("onfloor_pts_for", "Team points while on the floor", "PTS on", "On the floor", "int", "count",
           _onfloor("pts_for"), GAME_FIRST, total=_onfloor("pts_for"), n_unit="games", sources=ONFLOOR_SOURCES,
           note=ONFLOOR_NOTE),
    Column("onfloor_pts_against", "Opponent points while on the floor", "OPP on", "On the floor", "int", "count",
           _onfloor("pts_against"), GAME_FIRST, False, total=_onfloor("pts_against"), n_unit="games",
           sources=ONFLOOR_SOURCES, note=ONFLOOR_NOTE),
)

# The Game Finder's own wording, kept so its page's responses don't change.
GAME_FINDER_LABELS = {
    "fga": "Field goal attempts", "fg3m": "Threes made", "fg3a": "Three-point attempts",
    "ftm": "Free throws made", "fta": "Free throw attempts", "fg_pct": "FG%", "fg3_pct": "3P%", "ft_pct": "FT%",
}

PLAYER_GAME = Dataset(
    key="player_game", label="Player games", entity="player",
    description=("One row per player-game he played, regular season 2020-21 on, rebuilt from ESPN play-by-play "
                 "(the Game Log's rows)."),
    from_sql=(PLAYER_GAME_FROM + "    LEFT JOIN player_bio b ON b.player_id = l.player_id\n"
              "    LEFT JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id\n"),
    where=PLAYER_GAME_WHERE,
    season_sql="l.season", entity_sql="l.player_id", games_sql="1", minutes_sql="(l.seconds / 60.0)",
    poss_sql=ON_COURT_POSS, per_modes=("game", "total", "per36", "per100"), row_label="games",
    tables=("player_game_lines", "team_game_fatigue", "game_scores", "player_bio", "player_season_stats",
            "player_game_onfloor"),
    upstream="ESPN play-by-play (lines rebuilt from it) and scoreboard, nba_api (stats.nba.com) schedule",
    row_fields=(("player_id", "l.player_id"), ("season", "l.season"), ("date", "l.game_date"),
                ("game_id", "f.game_id"), ("team", "l.team_abbreviation"), ("opponent", "f.opponent"),
                ("home", "f.is_home"), ("win", "f.win")),
    groupings={
        "entity": Grouping(("l.player_id",), ("player_id",)),
        "season": Grouping(("l.season",), ("season",)),
        "team": Grouping(("l.team_abbreviation",), ("team",)),
        "opponent": Grouping(("f.opponent",), ("opponent",)),
        "home": Grouping(("f.is_home",), ("home",)),
        "result": Grouping(("f.win",), ("win",)),
    },
    dims={
        "team": Dim("team", "Team", "l.team_abbreviation", "team"),
        "opponent": Dim("opponent", "Opponent", "f.opponent", "team"),
        "home": Dim("home", "Home game", "f.is_home", "bool"),
        "result": Dim("result", "Team won", "f.win", "bool"),
        "b2b": Dim("b2b", "Second night of a back-to-back", "f.is_b2b", "bool"),
        "date": Dim("date", "Date", "l.game_date", "date", group=False),
    },
    columns=PLAYER_GAME_COLUMNS,
    names_from_ids=True,
    notes=("Regular season 2020-21 on only (the play-by-play the project has). NBA Cup finals are left out: they "
           "don't count in regular-season stats. Games a player sat out have no row.",
           "Per 100 possessions uses his on-court possessions (On/Off's estimate from the play-by-play)."),
)


# ─── team_season ────────────────────────────────────────────────────────────
TS_SOURCES = ("team_seasons",)
TEAM_POSS = "t.pace * t.g * COALESCE(t.mp_per_game, 240.0) / 240.0"


def _ts(key, label, short, group, fmt, kind, sql, first, hib=True, **kw):
    return Column(key, label, short, group, fmt, kind, sql, first, hib, sources=TS_SOURCES, **kw)


def _ts_rate(key, label, short, sql, first, hib=True, group="Four factors"):
    return _ts(key, label, short, group, "pct", "none", sql, first, hib, n_unit="seasons",
               note="Basketball-Reference's season rate; the table has no counts behind it, so seasons aren't combined.")


TEAM_SEASON_COLUMNS = _cols(
    _ts("g", "Games", "G", "Record", "int", "sum", "t.g", 1947, n_unit="seasons"),
    _ts("w", "Wins", "W", "Record", "int", "sum", "t.w", 1947, n_unit="seasons"),
    _ts("l", "Losses", "L", "Record", "int", "sum", "t.l", 1947, False, n_unit="seasons"),
    _ts("w_pct", "Win %", "W%", "Record", "pct", "ratio", "(t.w::float / NULLIF(t.w + t.l, 0))", 1947,
        num="t.w", den="(t.w + t.l)", n_unit="games", agg_text="summed wins ÷ summed games"),
    _ts("pw", "Expected wins (Pythagorean)", "PW", "Record", "int", "sum", "t.pw", 1947, n_unit="seasons"),
    _ts("pl", "Expected losses (Pythagorean)", "PL", "Record", "int", "sum", "t.pl", 1947, False, n_unit="seasons"),
    _ts("pts", "Points", "PTS", "Scoring", "num1", "count", "t.pts_per_game", 1947, total="t.pts_per_game * t.g",
        n_unit="games"),
    _ts("opp_pts", "Opponent points", "OPP", "Scoring", "num1", "count", "t.opp_pts_per_game", 1947, False,
        total="t.opp_pts_per_game * t.g", n_unit="games"),
    _ts("mov", "Point margin", "MOV", "Scoring", "signed1", "count", "t.mov", 1947, total="t.mov * t.g",
        n_unit="games", agg_fmt="signed1"),
    _ts("srs", "Simple rating system", "SRS", "Strength", "signed2", "wmean", "t.srs", 1947, weight="t.g",
        weight_label="games", n_unit="games", note="Point margin adjusted for schedule (Basketball-Reference)."),
    _ts("sos", "Strength of schedule", "SOS", "Strength", "signed2", "wmean", "t.sos", 1947, None, weight="t.g",
        weight_label="games", n_unit="games"),
    _ts("o_rtg", "Offensive rating", "ORTG", "Strength", "num1", "wmean", "t.o_rtg", 1951, weight=TEAM_POSS,
        weight_label="possessions", n_unit="possessions"),
    _ts("d_rtg", "Defensive rating", "DRTG", "Strength", "num1", "wmean", "t.d_rtg", 1951, False, weight=TEAM_POSS,
        weight_label="possessions", n_unit="possessions"),
    _ts("n_rtg", "Net rating", "NET", "Strength", "signed1", "wmean", "t.n_rtg", 1951, weight=TEAM_POSS,
        weight_label="possessions", n_unit="possessions"),
    _ts("pace", "Pace", "Pace", "Strength", "num1", "wmean", "t.pace", 1951, None, weight="t.g",
        weight_label="games", n_unit="games", note="Possessions per 48 minutes."),
    _ts("age", "Average age", "Age", "Other", "num1", "wmean", "t.age", 1952, None, weight="t.g",
        weight_label="games", n_unit="games", note="Basketball-Reference's minutes-weighted age of the roster."),
    _ts_rate("ts_pct", "True shooting %", "TS%", "t.ts_percent", 1947, group="Shooting"),
    _ts_rate("efg_pct", "Effective FG %", "eFG%", "t.e_fg_percent", 1947),
    _ts_rate("tov_pct", "Turnover %", "TOV%", "(t.tov_percent / 100.0)", 1971, False),
    _ts_rate("orb_pct", "Offensive rebound %", "ORB%", "(t.orb_percent / 100.0)", 1974),
    _ts_rate("ft_fga", "Free throws per FGA", "FT/FGA", "t.ft_fga", 1947),
    _ts_rate("opp_efg_pct", "Opponent effective FG %", "oeFG%", "t.opp_e_fg_percent", 1971, False),
    _ts_rate("opp_tov_pct", "Opponent turnover %", "oTOV%", "(t.opp_tov_percent / 100.0)", 1971),
    _ts_rate("drb_pct", "Defensive rebound %", "DRB%", "(t.drb_percent / 100.0)", 1974),
    _ts_rate("opp_ft_fga", "Opponent free throws per FGA", "oFT/FGA", "t.opp_ft_fga", 1971, False),
    _ts_rate("fta_rate", "Free-throw attempt rate", "FTr", "t.f_tr", 1947, group="Shooting"),
    _ts_rate("fg3a_rate", "3-point attempt rate", "3PAr", "t.x3p_ar", 1980, group="Shooting"),
    _ts("attend", "Attendance", "ATT", "Attendance", "int", "sum", "t.attend", 1981, None, n_unit="seasons",
        note="Home attendance; 2020-21 had no or limited crowds."),
    _ts("attend_g", "Attendance per game", "ATT/G", "Attendance", "int", "wmean",
        "(CASE WHEN t.attend IS NOT NULL THEN t.attend_g END)", 1981, None, weight="t.g", weight_label="games",
        n_unit="games",
        note=("Only where the season total is on file: before 1980-81 the export has per-game figures without "
              "one that don't match known crowds (Knicks 1979-80: 1,432 a game).")),
)

TEAM_SEASON = Dataset(
    key="team_season", label="Team seasons", entity="team",
    description="One row per team-season, 1946-47 on (Basketball-Reference): record, ratings, four factors.",
    from_sql="FROM team_seasons t",
    where=("NOT t.is_league_avg",),
    season_sql="t.season", entity_sql="t.franchise", games_sql="t.g", minutes_sql=None, poss_sql=TEAM_POSS,
    per_modes=("game", "total", "per100"), row_label="seasons",
    tables=("team_seasons",), upstream="Basketball-Reference (Kaggle export)",
    row_fields=(("franchise", "t.franchise"), ("team", "t.abbreviation"), ("team_name", "t.team_name"),
                ("season", "t.season")),
    groupings={
        "entity": Grouping(("t.franchise",), ("franchise",),
                           (("team_name", "(array_agg(t.team_name ORDER BY t.season DESC))[1]"),)),
        "season": Grouping(("t.season",), ("season",)),
        "team": Grouping(("t.abbreviation",), ("team",)),
    },
    dims={
        "team": Dim("team", "Team code at the time", "t.abbreviation", "team"),
        "league": Dim("league", "League", "t.lg", "text", values=("BAA", "NBA")),
        "playoffs": Dim("playoffs", "Made the playoffs", "t.playoffs", "bool"),
    },
    columns=TEAM_SEASON_COLUMNS,
    notes=("A team is its franchise (the NBA's own records: the Seattle SuperSonics are the Thunder). Team codes "
           "follow the season's own name.",
           "Possessions (per 100, rating weights) are pace × games × minutes ÷ 240; before 1964-65 the table "
           "has no minutes played, so overtime is left out (about 0.5%)."),
)


# ─── team_game ──────────────────────────────────────────────────────────────
# Franchise of every team code in the game tables (NBA codes from 2009-10:
# NJN -> BKN, NOH -> NOP), bound as two arrays.
_ALL_CODES = sorted(set(FRANCHISE_OF_BREF) | set(BREF_TO_NBA.values()))
FRANCHISE_MAP = (_ALL_CODES, [franchise_of(NBA_TO_BREF.get(c, c)) for c in _ALL_CODES])

TG_FROM = """
    FROM game_scores g
    JOIN team_game_fatigue f ON f.game_id = g.game_id AND f.team_abbreviation = g.team_abbreviation
    LEFT JOIN game_team_box b ON b.game_id = g.game_id AND b.team_abbreviation = g.team_abbreviation
    LEFT JOIN game_team_box ob ON ob.game_id = g.game_id AND ob.team_abbreviation = g.opponent
    LEFT JOIN unnest(%s::text[], %s::text[]) AS fr(code, franchise) ON fr.code = g.team_abbreviation
"""
BOX_POSS = "((b.fga + 0.44 * b.fta - b.oreb + b.tov) + (ob.fga + 0.44 * ob.fta - ob.oreb + ob.tov)) / 2.0"
GAME_MINUTES = "(48 + 5 * (g.periods - 4))"
TG_SOURCES = ("game_scores", "team_game_fatigue")
BOX_SOURCES = ("game_team_box",)
BOX_FIRST = 2021


def _tg(key, label, short, group, fmt, kind, sql, first=2010, hib=True, sources=TG_SOURCES, **kw):
    return Column(key, label, short, group, fmt, kind, sql, first, hib, sources=sources, **kw)


def _box(key, label, short, sql, hib=True, group="Box score"):
    return _tg(key, label, short, group, "int", "count", sql, BOX_FIRST, hib, BOX_SOURCES, total=sql, n_unit="games",
               per_modes=("game", "total", "per100"))


TEAM_GAME_COLUMNS = _cols(
    _tg("games", "Games", "G", "Record", "int", "sum", "1", n_unit="games"),
    _tg("wins", "Wins", "W", "Record", "int", "sum", "(f.win)::int", n_unit="games"),
    _tg("win_pct", "Win %", "W%", "Record", "pct", "ratio", "(f.win)::int::float", num="(f.win)::int", den="1",
        n_unit="games", agg_text="summed wins ÷ summed games"),
    _tg("pts", "Points", "PTS", "Scoring", "int", "count", "g.pts_for", total="g.pts_for", n_unit="games",
        per_modes=("game", "total", "per100")),
    _tg("opp_pts", "Opponent points", "OPP", "Scoring", "int", "count", "g.pts_against", hib=False,
        total="g.pts_against", n_unit="games", per_modes=("game", "total", "per100")),
    _tg("margin", "Point margin", "MOV", "Scoring", "signed1", "count", "(g.pts_for - g.pts_against)",
        total="(g.pts_for - g.pts_against)", n_unit="games", per_modes=("game", "total"), agg_fmt="signed1",
        note="The real final score (ESPN's scoreboard)."),
    _tg("rest_days", "Days of rest before", "Rest", "Schedule", "num1", "wmean", "f.rest_days", hib=None, weight="1",
        weight_label="games", n_unit="games", note="Not set for a team's first game of the season."),
    _tg("games_last_7", "Games in the last 7 days", "G/7d", "Schedule", "num1", "wmean", "f.games_last_7_days",
        hib=None, weight="1", weight_label="games", n_unit="games"),
    _tg("travel_miles", "Miles travelled since the last game", "Miles", "Schedule", "int", "count",
        "f.travel_miles_since_last", hib=None, total="f.travel_miles_since_last", n_unit="games",
        per_modes=("game", "total")),
    _tg("timezones", "Time zones crossed since the last game", "TZ", "Schedule", "int", "count",
        "f.timezones_crossed_since_last", hib=None, total="f.timezones_crossed_since_last", n_unit="games",
        per_modes=("game", "total")),
    _tg("poss", "Possessions", "POSS", "Pace and ratings", "num1", "count", BOX_POSS, BOX_FIRST, None, BOX_SOURCES,
        total=BOX_POSS, n_unit="games", per_modes=("game", "total"),
        note="FGA + 0.44 × FTA − OREB + TOV from NBA.com's box score, averaged over both teams."),
    _tg("pace", "Pace", "Pace", "Pace and ratings", "num1", "ratio", f"(48.0 * {BOX_POSS} / {GAME_MINUTES})", BOX_FIRST,
        None, BOX_SOURCES, num=f"48.0 * {BOX_POSS}", den=GAME_MINUTES, n_sql="1", n_unit="games",
        agg_text="48 × summed possessions ÷ summed minutes", note="Possessions per 48 minutes (overtime counted)."),
    _tg("off_rating", "Offensive rating", "ORTG", "Pace and ratings", "num1", "ratio",
        f"(100.0 * g.pts_for / NULLIF({BOX_POSS}, 0))", BOX_FIRST, True, BOX_SOURCES, num="100.0 * g.pts_for",
        den=BOX_POSS, n_unit="possessions", agg_text="100 × summed points ÷ summed possessions",
        note="Points per 100 possessions."),
    _tg("def_rating", "Defensive rating", "DRTG", "Pace and ratings", "num1", "ratio",
        f"(100.0 * g.pts_against / NULLIF({BOX_POSS}, 0))", BOX_FIRST, False, BOX_SOURCES,
        num="100.0 * g.pts_against", den=BOX_POSS, n_unit="possessions",
        agg_text="100 × summed points allowed ÷ summed possessions"),
    _tg("net_rating", "Net rating", "NET", "Pace and ratings", "signed1", "ratio",
        f"(100.0 * (g.pts_for - g.pts_against) / NULLIF({BOX_POSS}, 0))", BOX_FIRST, True, BOX_SOURCES,
        num="100.0 * (g.pts_for - g.pts_against)", den=BOX_POSS, n_unit="possessions",
        agg_text="100 × summed margin ÷ summed possessions"),
    _box("fga", "Field-goal attempts", "FGA", "b.fga"),
    _box("fta", "Free-throw attempts", "FTA", "b.fta"),
    _box("oreb", "Offensive rebounds", "OREB", "b.oreb"),
    _box("tov", "Turnovers", "TOV", "b.tov", False),
    _box("pf", "Personal fouls", "PF", "b.pf", False),
    _box("opp_fga", "Opponent field-goal attempts", "oFGA", "ob.fga", None, "Opponent box score"),
    _box("opp_fta", "Opponent free-throw attempts", "oFTA", "ob.fta", False, "Opponent box score"),
    _box("opp_oreb", "Opponent offensive rebounds", "oOREB", "ob.oreb", False, "Opponent box score"),
    _box("opp_tov", "Opponent turnovers", "oTOV", "ob.tov", True, "Opponent box score"),
    _tg("tov_pct", "Turnovers per possession", "TOV%", "Pace and ratings", "pct", "ratio",
        f"(b.tov / NULLIF({BOX_POSS}, 0))", BOX_FIRST, False, BOX_SOURCES, num="b.tov", den=BOX_POSS,
        n_unit="possessions", agg_text="summed turnovers ÷ summed possessions"),
    _tg("fta_rate", "Free-throw attempt rate", "FTr", "Pace and ratings", "pct", "ratio",
        "(b.fta::float / NULLIF(b.fga, 0))", BOX_FIRST, True, BOX_SOURCES, num="b.fta", den="b.fga",
        n_unit="field-goal attempts", agg_text="summed FTA ÷ summed FGA", note="FTA per field-goal attempt."),
)

TEAM_GAME = Dataset(
    key="team_game", label="Team games", entity="team",
    description=("One row per team-game, regular season 2009-10 on: real final score, schedule and rest; "
                 "NBA.com's box score (possessions, ratings) from 2020-21."),
    from_sql=TG_FROM,
    where=(),
    season_sql="g.season", entity_sql="COALESCE(fr.franchise, g.team_abbreviation)", games_sql="1",
    minutes_sql=None, poss_sql=BOX_POSS, per_modes=("game", "total", "per100"), row_label="games",
    tables=("game_scores", "team_game_fatigue", "game_team_box"),
    upstream="ESPN scoreboard (final scores), nba_api (stats.nba.com) schedule and team box scores",
    row_fields=(("franchise", "COALESCE(fr.franchise, g.team_abbreviation)"), ("team", "g.team_abbreviation"),
                ("season", "g.season"), ("date", "g.game_date"), ("game_id", "g.game_id"),
                ("opponent", "g.opponent"), ("home", "g.is_home"), ("win", "f.win")),
    groupings={
        "entity": Grouping(("COALESCE(fr.franchise, g.team_abbreviation)",), ("franchise",)),
        "season": Grouping(("g.season",), ("season",)),
        "team": Grouping(("g.team_abbreviation",), ("team",)),
        "opponent": Grouping(("g.opponent",), ("opponent",)),
        "home": Grouping(("g.is_home",), ("home",)),
        "result": Grouping(("f.win",), ("win",)),
    },
    dims={
        "team": Dim("team", "Team code at the time", "g.team_abbreviation", "team"),
        "opponent": Dim("opponent", "Opponent", "g.opponent", "team"),
        "home": Dim("home", "Home game", "g.is_home", "bool"),
        "result": Dim("result", "Won", "f.win", "bool"),
        "b2b": Dim("b2b", "Second night of a back-to-back", "f.is_b2b", "bool"),
        "overtime": Dim("overtime", "Went to overtime", "(g.periods > 4)", "bool"),
        "neutral": Dim("neutral", "Neutral site", "g.neutral_site", "bool"),
        "date": Dim("date", "Date", "g.game_date", "date", group=False),
    },
    columns=TEAM_GAME_COLUMNS,
    from_params=FRANCHISE_MAP,
    notes=("A team is its franchise (NJN games count for the Nets, NOH for the Pelicans).",
           "Possessions and ratings need NBA.com's box score, which the project has from 2020-21 on."),
)

# ─── player_onoff (round 7 step 7) ──────────────────────────────────────────
# On/off per player-season-team, computed here from the corrected on-floor
# points (so it can combine seasons and carry a closed-form interval).
# player_on_off (the On/Off page, scripts/build_player_on_off.py) reads the same
# sources since 2026-10-03 and gives the same numbers in every row
# (api/tests/test_workbench_step7.py); before, it took its on-court points from
# player_game_lines' tm_pts / op_pts, which double-count in games with a stale
# ESPN score field (about 10 points of on-court +/- a player-season-team).
# Off = the team's game total minus his on-court, over the games he played:
# points from the real final score (game_scores, which the on-floor points add
# up to in 12,873 of 12,874 fully tracked team-games), possessions from the
# play-by-play (team_game_totals, On/Off's own estimate, which the on-court
# possessions add up to). Only games whose play-by-play reconciles (game_ok).
#
# Interval: game-clustered, by linearisation of the ratio difference
# (z_g = 100 [(a_g - A/B b_g)/B - (c_g - C/E e_g)/E], var = G/(G-1) sum z_g^2).
# It reproduces the On/Off page's 2,000-resample game bootstrap SE: median
# ratio 1.01, 5th-95th percentile 0.97-1.07 on the corrected points (3,610
# player-season-teams with an SE, 2026-10-03), as it did on the page's old
# points before the rebuild (api/tests/test_workbench_step7.py re-checks it).
_ONOFF_GAMES = f"""
        SELECT l.player_id, l.season, l.team_abbreviation AS team, l.seconds,
               o.pts_for AS pf_on, o.pts_against AS pa_on, {ON_COURT_POSS} AS poss_on,
               GREATEST(0, gs.pts_for - o.pts_for) AS pf_off, GREATEST(0, gs.pts_against - o.pts_against) AS pa_off,
               GREATEST(0, tt.poss - {ON_COURT_POSS}) AS poss_off, GREATEST(0, tt.game_seconds - l.seconds) AS seconds_off
        FROM player_game_lines l
        JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
        JOIN game_scores gs ON gs.game_id = f.game_id AND gs.team_abbreviation = f.team_abbreviation
        JOIN team_game_totals tt ON tt.game_id = l.game_id AND tt.team_abbreviation = l.team_abbreviation
        JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id AND o.game_ok
        WHERE l.seconds > 0"""


def _onoff_se(on_num, off_num):
    """Game-clustered SE of 100 x (on rate - off rate), rates = summed points / summed possessions."""
    z = (f"100.0 * ((({on_num}) - (SUM({on_num}) OVER w) / NULLIF(SUM(poss_on) OVER w, 0) * poss_on) "
         f"/ NULLIF(SUM(poss_on) OVER w, 0) - (({off_num}) - (SUM({off_num}) OVER w) / NULLIF(SUM(poss_off) OVER w, 0) "
         f"* poss_off) / NULLIF(SUM(poss_off) OVER w, 0))")
    return z


ONOFF_FROM = f"""
    FROM (
      SELECT player_id, season, team, COUNT(*) AS games, SUM(seconds) / 60.0 AS minutes_on,
             SUM(seconds_off) / 60.0 AS minutes_off, SUM(pf_on) AS pf_on, SUM(pa_on) AS pa_on, SUM(poss_on) AS poss_on,
             SUM(pf_off) AS pf_off, SUM(pa_off) AS pa_off, SUM(poss_off) AS poss_off,
             CASE WHEN COUNT(*) >= 2 THEN sqrt(COUNT(*)::float8 / (COUNT(*) - 1) * SUM(z_net * z_net)) END AS se_net,
             CASE WHEN COUNT(*) >= 2 THEN sqrt(COUNT(*)::float8 / (COUNT(*) - 1) * SUM(z_o * z_o)) END AS se_o,
             CASE WHEN COUNT(*) >= 2 THEN sqrt(COUNT(*)::float8 / (COUNT(*) - 1) * SUM(z_d * z_d)) END AS se_d
      FROM (
        SELECT g.*, {_onoff_se("pf_on - pa_on", "pf_off - pa_off")} AS z_net,
               {_onoff_se("pf_on", "pf_off")} AS z_o, {_onoff_se("pa_on", "pa_off")} AS z_d
        FROM ({_ONOFF_GAMES}
        ) g
        WINDOW w AS (PARTITION BY player_id, season, team)
      ) z
      GROUP BY player_id, season, team
    ) oo
"""
OO_RATE = "(100.0 * ({num}) / NULLIF({den}, 0))"
OO_SOURCES = ("player_game_onfloor", "player_game_lines", "game_scores", "team_game_totals")
OO_INTERVAL = "95% interval (game-clustered)"


def _oo_rate(key, label, short, num, den, hib=True, group="On the floor"):
    return Column(key, label, short, group, "num1" if "net" not in key else "signed1", "ratio",
                  OO_RATE.format(num=num, den=den), GAME_FIRST, hib, num=f"100.0 * ({num})", den=den,
                  n_unit="possessions", agg_text="100 × summed points ÷ summed possessions", method="onoff",
                  sources=OO_SOURCES)


def _oo_diff(key, label, short, on, off, se, hib=True, note=None):
    est = (f"({OO_RATE.format(num=on, den='oo.poss_on')} - {OO_RATE.format(num=off, den='oo.poss_off')})")
    return Column(key, label, short, "On minus off", "signed1", "diff", est, GAME_FIRST, hib,
                  num=f"100.0 * ({on})", den="oo.poss_on", num2=f"100.0 * ({off})", den2="oo.poss_off",
                  n_sql="oo.poss_on", n_unit="possessions on the floor",
                  agg_text="pooled on-court rate − pooled off-court rate (summed points ÷ summed possessions each)",
                  ci_lo=f"({est} - {Z95} * oo.{se})", ci_hi=f"({est} + {Z95} * oo.{se})", ci_label=OO_INTERVAL,
                  method="onoff", sources=OO_SOURCES, note=note)


PLAYER_ONOFF = Dataset(
    key="player_onoff", label="Player on/off", entity="player",
    description=("One row per player-season-team, 2020-21 on: the team per 100 possessions with him on the floor "
                 "and off it, in the games he played, from the corrected on-floor points."),
    from_sql=ONOFF_FROM, where=(),
    season_sql="oo.season", entity_sql="oo.player_id", games_sql="oo.games", minutes_sql=None, poss_sql="oo.poss_on",
    per_modes=("game",), row_label="player-team-seasons",
    tables=OO_SOURCES + ("team_game_fatigue",),
    upstream="ESPN play-by-play (lineups, points) and scoreboard (final scores)",
    row_fields=(("player_id", "oo.player_id"), ("season", "oo.season"), ("team", "oo.team")),
    groupings={
        "entity": Grouping(("oo.player_id",), ("player_id",)),
        "season": Grouping(("oo.season",), ("season",)),
        "team": Grouping(("oo.team",), ("team",)),
    },
    dims={"team": Dim("team", "Team", "oo.team", "team")},
    columns=_cols(
        Column("games", "Games", "G", "Playing time", "int", "sum", "oo.games", GAME_FIRST, n_unit="games",
               sources=OO_SOURCES),
        Column("minutes_on", "Minutes on the floor", "MIN on", "Playing time", "int", "sum", "oo.minutes_on",
               GAME_FIRST, n_unit="games", sources=OO_SOURCES),
        Column("minutes_off", "Team minutes with him off", "MIN off", "Playing time", "int", "sum", "oo.minutes_off",
               GAME_FIRST, None, n_unit="games", sources=OO_SOURCES),
        Column("poss_on", "Possessions on the floor", "POSS on", "Playing time", "int", "sum", "oo.poss_on",
               GAME_FIRST, n_unit="games", sources=OO_SOURCES),
        Column("poss_off", "Team possessions with him off", "POSS off", "Playing time", "int", "sum", "oo.poss_off",
               GAME_FIRST, None, n_unit="games", sources=OO_SOURCES),
        Column("pm_on", "On-court plus-minus (total)", "+/-", "On the floor", "signed1", "sum",
               "(oo.pf_on - oo.pa_on)", GAME_FIRST, n_sql="oo.poss_on", n_unit="possessions", sources=OO_SOURCES,
               note=ONFLOOR_NOTE),
        _oo_rate("ortg_on", "Team offensive rating, on", "ORTG on", "oo.pf_on", "oo.poss_on"),
        _oo_rate("drtg_on", "Team defensive rating, on", "DRTG on", "oo.pa_on", "oo.poss_on", False),
        _oo_rate("net_on", "Team net rating, on", "NET on", "oo.pf_on - oo.pa_on", "oo.poss_on"),
        _oo_rate("ortg_off", "Team offensive rating, off", "ORTG off", "oo.pf_off", "oo.poss_off", None, "Off the floor"),
        _oo_rate("drtg_off", "Team defensive rating, off", "DRTG off", "oo.pa_off", "oo.poss_off", None, "Off the floor"),
        _oo_rate("net_off", "Team net rating, off", "NET off", "oo.pf_off - oo.pa_off", "oo.poss_off", None,
                 "Off the floor"),
        _oo_diff("on_off_net", "On/off net rating", "On/off", "oo.pf_on - oo.pa_on", "oo.pf_off - oo.pa_off", "se_net",
                 note=("Team net rating with him on minus with him off, in the games he played. Raw: it carries "
                       "who he played with and against (RAPM adjusts for that).")),
        _oo_diff("on_off_ortg", "On/off offensive rating", "On/off O", "oo.pf_on", "oo.pf_off", "se_o"),
        _oo_diff("on_off_drtg", "On/off defensive rating", "On/off D", "oo.pa_on", "oo.pa_off", "se_d", False),
    ),
    names_from_ids=True,
    notes=("Computed from the corrected on-floor points (free throws credited to the players on the floor at the "
           "foul) and the real final score, the same numbers as the On/Off page.",
           "Off-court is the team's total minus his on-court in the games he played; games he missed aren't in it "
           "(With/Without a Star's job). The 12 games whose play-by-play doesn't reconcile are left out.",
           "Intervals: 95%, clustered by game (each game resampled as a whole), shown one row at a time."),
)


# ─── lineup_season / pair_season (round 7 step 7) ──────────────────────────
# Five-man units and pairs from the play-by-play stints (lineup_seasons /
# pair_seasons, scripts/build_lineup_stints.py): tracked stints only (five
# identified players a side), possessions averaged over both sides like
# On/Off, points from made shots and free throws credited at the shot (README
# Known real gaps). A team is its franchise; a player set picks the units its
# players are in (spec `players`).
UNIT_FIRST = 2021
LS_SOURCES = ("lineup_seasons", "lineup_stints")
PR_SOURCES = ("pair_seasons", "lineup_stints")
STINT_NOTE = ("From the five-man stints of the play-by-play (stints with all ten players identified), "
              "possessions averaged over both sides; free throws are credited to the players on the floor at the "
              "shot, not at the foul (README Known real gaps).")


def _unit_cols(a, sources):
    def rate(key, label, short, num, hib=True, fmt="num1"):
        return Column(key, label, short, "Ratings", fmt, "ratio", f"(100.0 * ({num}) / NULLIF({a}.poss, 0))",
                      UNIT_FIRST, hib, num=f"100.0 * ({num})", den=f"{a}.poss", n_unit="possessions",
                      agg_text="100 × summed points ÷ summed possessions", sources=sources)

    def tot(key, label, short, sql, fmt="int", hib=True):
        return Column(key, label, short, "Totals", fmt, "sum", sql, UNIT_FIRST, hib, n_unit="seasons",
                      sources=sources)

    return (
        tot("games", "Games together", "G", f"{a}.games"),
        tot("minutes", "Minutes together", "MIN", f"{a}.minutes", "num1"),
        tot("poss", "Possessions together", "POSS", f"{a}.poss", "num1"),
        tot("pts_for", "Points scored", "PTS", f"{a}.pts_for"),
        tot("pts_against", "Points allowed", "OPP", f"{a}.pts_against", "int", False),
        tot("plus_minus", "Plus-minus", "+/-", f"({a}.pts_for - {a}.pts_against)", "signed1"),
        rate("off_rating", "Offensive rating", "ORTG", f"{a}.pts_for"),
        rate("def_rating", "Defensive rating", "DRTG", f"{a}.pts_against", False),
        rate("net_rating", "Net rating", "NET", f"{a}.pts_for - {a}.pts_against", True, "signed1"),
    )


LINEUP_SEASON = Dataset(
    key="lineup_season", label="Five-man lineups", entity="team",
    description=("One row per five-man lineup per team-season, 2020-21 on, from the play-by-play stints: minutes, "
                 "possessions, ratings and four factors."),
    from_sql="""
    FROM lineup_seasons ls
    LEFT JOIN unnest(%s::text[], %s::text[]) AS fr(code, franchise) ON fr.code = ls.team_abbreviation
""",
    where=(),
    season_sql="ls.season", entity_sql="COALESCE(fr.franchise, ls.team_abbreviation)", games_sql="ls.games",
    minutes_sql=None, poss_sql="ls.poss", per_modes=("game",), row_label="lineup-seasons",
    tables=LS_SOURCES, upstream="ESPN play-by-play (lineups and points)",
    row_fields=(("franchise", "COALESCE(fr.franchise, ls.team_abbreviation)"), ("team", "ls.team_abbreviation"),
                ("season", "ls.season"), ("player_ids", "ls.player_ids")),
    groupings={
        "entity": Grouping(("COALESCE(fr.franchise, ls.team_abbreviation)",), ("franchise",)),
        "season": Grouping(("ls.season",), ("season",)),
        "team": Grouping(("ls.team_abbreviation",), ("team",)),
        "lineup": Grouping(("ls.player_ids", "COALESCE(fr.franchise, ls.team_abbreviation)"), ("player_ids", "franchise")),
    },
    dims={
        "team": Dim("team", "Team code at the time", "ls.team_abbreviation", "team"),
        "players": Dim("players", "With these players", "ls.player_ids", "players", group=False),
    },
    columns=_cols(
        *_unit_cols("ls", LS_SOURCES),
        Column("efg_pct", "Effective FG %", "eFG%", "Four factors", "pct", "ratio",
               "((ls.fgm + 0.5 * ls.fg3m) / NULLIF(ls.fga, 0))", UNIT_FIRST, num="(ls.fgm + 0.5 * ls.fg3m)",
               den="ls.fga", n_unit="field-goal attempts", agg_text="summed (FGM + 0.5 × 3PM) ÷ summed FGA",
               sources=LS_SOURCES),
        Column("fg3_pct", "3-point %", "3P%", "Four factors", "pct", "ratio", "(ls.fg3m::float / NULLIF(ls.fg3a, 0))",
               UNIT_FIRST, num="ls.fg3m", den="ls.fg3a", n_unit="3-point attempts", agg_text="summed makes ÷ summed attempts",
               sources=LS_SOURCES),
        Column("fg3a_rate", "3-point attempt rate", "3PAr", "Four factors", "pct", "ratio",
               "(ls.fg3a::float / NULLIF(ls.fga, 0))", UNIT_FIRST, None, num="ls.fg3a", den="ls.fga",
               n_unit="field-goal attempts", agg_text="summed 3PA ÷ summed FGA", sources=LS_SOURCES),
        Column("fta_rate", "Free-throw attempt rate", "FTr", "Four factors", "pct", "ratio",
               "(ls.fta::float / NULLIF(ls.fga, 0))", UNIT_FIRST, num="ls.fta", den="ls.fga",
               n_unit="field-goal attempts", agg_text="summed FTA ÷ summed FGA", sources=LS_SOURCES),
        Column("tov_pct", "Turnovers per possession", "TOV%", "Four factors", "pct", "ratio",
               "(ls.tov / NULLIF(ls.poss_for, 0))", UNIT_FIRST, False, num="ls.tov", den="ls.poss_for",
               n_unit="possessions", agg_text="summed turnovers ÷ summed own possessions", sources=LS_SOURCES),
    ),
    from_params=FRANCHISE_MAP,
    name_fields=(("player_ids", "player_names"),),
    players_sql="ls.player_ids",
    poss_floor=100,
    notes=(STINT_NOTE,
           "A team is its franchise. A lineup's net rating over 100 possessions still swings by about ±25 points "
           "per 100 from luck alone (a possession is worth 0 to 3 points), so the possessions floor matters."),
)

PAIR_SEASON = Dataset(
    key="pair_season", label="Two-man pairs", entity="team",
    description="One row per pair of teammates per team-season, 2020-21 on: the team with both on the floor.",
    from_sql="""
    FROM pair_seasons pr
    LEFT JOIN unnest(%s::text[], %s::text[]) AS fr(code, franchise) ON fr.code = pr.team_abbreviation
""",
    where=(),
    season_sql="pr.season", entity_sql="COALESCE(fr.franchise, pr.team_abbreviation)", games_sql="pr.games",
    minutes_sql=None, poss_sql="pr.poss", per_modes=("game",), row_label="pair-seasons",
    tables=PR_SOURCES, upstream="ESPN play-by-play (lineups and points)",
    row_fields=(("franchise", "COALESCE(fr.franchise, pr.team_abbreviation)"), ("team", "pr.team_abbreviation"),
                ("season", "pr.season"), ("player_a", "pr.player_a"), ("player_b", "pr.player_b")),
    groupings={
        "entity": Grouping(("COALESCE(fr.franchise, pr.team_abbreviation)",), ("franchise",)),
        "season": Grouping(("pr.season",), ("season",)),
        "team": Grouping(("pr.team_abbreviation",), ("team",)),
        "pair": Grouping(("pr.player_a", "pr.player_b", "COALESCE(fr.franchise, pr.team_abbreviation)"),
                         ("player_a", "player_b", "franchise")),
    },
    dims={
        "team": Dim("team", "Team code at the time", "pr.team_abbreviation", "team"),
        "players": Dim("players", "With these players", "ARRAY[pr.player_a, pr.player_b]", "players", group=False),
    },
    columns=_cols(*_unit_cols("pr", PR_SOURCES)),
    from_params=FRANCHISE_MAP,
    name_fields=(("player_a", "player_a_name"), ("player_b", "player_b_name")),
    players_sql="ARRAY[pr.player_a, pr.player_b]",
    poss_floor=250,
    notes=(STINT_NOTE, "Only time with both on the floor; Pair Chemistry on the Analytics page has the grid."),
)


# ─── team_possessions (round 7 step 7) ─────────────────────────────────────
# possession_seasons (scripts/build_possessions.py) pivoted to one row per
# team-season: every possession, and each way a possession starts, for the
# team (offence) and its opponents (defence). Labels as on Possession Explorer.
POSS_STARTS = (
    ("made_fg", "After a made shot"), ("dreb", "After a defensive rebound"), ("steal", "After a steal"),
    ("dead_tov", "After a dead-ball turnover"), ("made_ft", "After a made last free throw"),
    ("dreb_ft", "After a rebounded free throw"), ("team_dreb", "After a team rebound"),
    ("period_start", "Start of a period"),
)
_PS_FIELDS = ("games", "poss", "pts", "timed_poss", "trans_poss", "trans_pts", "oreb_poss", "second_chance_pts",
              "fg3a", "fta", "tov", "d_poss", "d_pts", "d_timed_poss", "d_trans_poss")
_PS_PIVOT = ",\n".join(
    [f"        MAX(CASE WHEN start_type = 'all' THEN {f} END) AS {f}" for f in _PS_FIELDS]
    + ["        MAX(CASE WHEN start_type = 'all' THEN d_trans_ppp * d_trans_poss END) AS d_trans_pts"]
    + [f"        MAX(CASE WHEN start_type = '{k}' THEN {f} END) AS {f}_{k}" for k, _ in POSS_STARTS
       for f in ("poss", "pts", "d_poss", "d_pts")])
POSS_FROM = f"""
    FROM (
      SELECT season::int AS season, team,
{_PS_PIVOT}
      FROM possession_seasons WHERE team <> 'ALL'
      GROUP BY season, team
    ) pp
    LEFT JOIN unnest(%s::text[], %s::text[]) AS fr(code, franchise) ON fr.code = pp.team
"""
PP_SOURCES = ("possession_seasons",)


def _pp_ratio(key, label, short, group, num, den, fmt="num2", hib=True, n_unit="possessions", agg=None, note=None):
    return Column(key, label, short, group, fmt, "ratio", f"(({num})::float8 / NULLIF({den}, 0))", UNIT_FIRST, hib,
                  num=num, den=den, n_unit=n_unit, agg_text=agg or "summed numerator ÷ summed possessions",
                  sources=PP_SOURCES, note=note)


def _pp_sum(key, label, short, group, sql, hib=None):
    return Column(key, label, short, group, "int", "sum", sql, UNIT_FIRST, hib, n_unit="seasons", sources=PP_SOURCES)


_pp_cols = [
    _pp_sum("poss", "Possessions", "POSS", "All possessions", "pp.poss"),
    _pp_ratio("ppp", "Points per possession", "PPP", "All possessions", "pp.pts", "pp.poss"),
    _pp_ratio("trans_share", "Transition share", "Trans%", "All possessions", "pp.trans_poss", "pp.timed_poss", "pct",
              None, agg="summed transition possessions ÷ summed timed possessions",
              note=("Possessions whose first shot came within 7 seconds, of those whose start time is known (after "
                    "turnovers it isn't: ESPN stamps a turnover late).")),
    _pp_ratio("trans_ppp", "Points per transition possession", "Trans PPP", "All possessions", "pp.trans_pts",
              "pp.trans_poss"),
    _pp_ratio("second_chance", "Second-chance points per possession", "2nd ch", "All possessions",
              "pp.second_chance_pts", "pp.poss"),
    _pp_ratio("fg3a_per_poss", "3-point attempts per possession", "3PA/P", "All possessions", "pp.fg3a", "pp.poss"),
    _pp_ratio("fta_per_poss", "Free-throw attempts per possession", "FTA/P", "All possessions", "pp.fta", "pp.poss"),
    _pp_ratio("tov_per_poss", "Turnovers per possession", "TOV/P", "All possessions", "pp.tov", "pp.poss", "pct", False),
    _pp_sum("d_poss", "Opponent possessions", "oPOSS", "Defence", "pp.d_poss"),
    _pp_ratio("d_ppp", "Points allowed per possession", "oPPP", "Defence", "pp.d_pts", "pp.d_poss", hib=False),
    _pp_ratio("d_trans_share", "Opponent transition share", "oTrans%", "Defence", "pp.d_trans_poss", "pp.d_timed_poss",
              "pct", False, agg="summed transition possessions ÷ summed timed possessions"),
    _pp_ratio("d_trans_ppp", "Points allowed per transition possession", "oTrans PPP", "Defence", "pp.d_trans_pts",
              "pp.d_trans_poss", hib=False),
    Column("net_ppp", "Points per possession, net", "Net PPP", "All possessions", "signed2", "diff",
           "(pp.pts::float8 / NULLIF(pp.poss, 0) - pp.d_pts::float8 / NULLIF(pp.d_poss, 0))", UNIT_FIRST,
           num="pp.pts", den="pp.poss", num2="pp.d_pts", den2="pp.d_poss", n_sql="pp.poss", n_unit="possessions",
           agg_text="pooled points per possession − pooled points allowed per possession", sources=PP_SOURCES),
]
for _k, _lab in POSS_STARTS:
    _g = _lab
    _pp_cols += [
        _pp_ratio(f"share_{_k}", f"Share of possessions: {_lab.lower()}", f"%{_k}", _g, f"pp.poss_{_k}", "pp.poss",
                  "pct", None, agg="summed possessions of this start ÷ summed possessions"),
        _pp_ratio(f"ppp_{_k}", f"Points per possession {_lab.lower()}", f"PPP {_k}", _g, f"pp.pts_{_k}", f"pp.poss_{_k}"),
        _pp_ratio(f"d_ppp_{_k}", f"Allowed per possession {_lab.lower()}", f"oPPP {_k}", _g, f"pp.d_pts_{_k}",
                  f"pp.d_poss_{_k}", hib=False),
    ]

TEAM_POSSESSIONS = Dataset(
    key="team_possessions", label="Team possessions", entity="team",
    description=("One row per team-season, 2020-21 on: possessions cut from the play-by-play, how they start "
                 "(after a make, a steal, a rebound …) and the points they bring, for and against."),
    from_sql=POSS_FROM, where=(),
    season_sql="pp.season", entity_sql="COALESCE(fr.franchise, pp.team)", games_sql="pp.games", minutes_sql=None,
    poss_sql="pp.poss", per_modes=("game",), row_label="seasons",
    tables=PP_SOURCES + ("possessions",), upstream="ESPN play-by-play, cut into possessions by scripts/build_possessions.py",
    row_fields=(("franchise", "COALESCE(fr.franchise, pp.team)"), ("team", "pp.team"), ("season", "pp.season")),
    groupings={
        "entity": Grouping(("COALESCE(fr.franchise, pp.team)",), ("franchise",)),
        "season": Grouping(("pp.season",), ("season",)),
        "team": Grouping(("pp.team",), ("team",)),
    },
    dims={"team": Dim("team", "Team code", "pp.team", "team")},
    columns=_cols(*_pp_cols),
    from_params=FRANCHISE_MAP,
    notes=("Possessions are counted from the play-by-play (Possession Explorer's): about 2.3 a team-game fewer than "
           "the FGA + 0.44 FTA − OREB + TOV estimate, because ESPN logs team offensive rebounds the estimate doesn't "
           "subtract.",
           "No interval is stored for these rates; at a team-season's ~600 possessions of one start type, points "
           "per possession moves by about ±0.09 from chance alone (Possession Explorer shows each interval)."),
)


# ─── player_projection (round 7 step 7) ────────────────────────────────────
# Next-season projections with their 80% ranges (scripts/build_projections.py):
# the backtest's projections for 2000-01 to 2025-26 beside what happened, and
# the live projections for 2026-27 (no actual yet).
PROJ_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min",
              "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct",
              "tov_pct", "bpm", "obpm", "dbpm")
PROJ_PER36 = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb")
# The backtest scores a shooting % only for seasons over its attempts floor.
PROJ_AMONG = {"fg3_pct": "s.fg3a * s.gp >= 50", "ft_pct": "s.fta * s.gp >= 50"}
_PROJ_KEYS = list(PROJ_STATS) + [f"{k}36" for k in PROJ_PER36]
_PJ_PIVOT = ",\n".join(f"        MAX(CASE WHEN pr.stat = '{k}' THEN pr.{c} END) AS {k}_{a}"
                       for k in _PROJ_KEYS for c, a in (("projection", "p"), ("lo", "lo"), ("hi", "hi"), ("actual", "a")))
PROJ_FROM = f"""
    FROM (
      SELECT pr.player_id, pr.season, MAX(pr.team) AS team,
{_PJ_PIVOT}
      FROM (SELECT player_id, season, stat, projection, lo, hi, actual, NULL::text AS team FROM projection_backtest_rows
            UNION ALL
            SELECT player_id, season, stat, projection, lo, hi, NULL, team FROM player_projections) pr
      GROUP BY pr.player_id, pr.season
    ) pj
    LEFT JOIN player_season_stats s ON s.player_id = pj.player_id AND s.season = pj.season
"""
PJ_SOURCES = ("projection_backtest_rows", "player_projections")
PROJ_NOTE = ("Marcel-style: his last three seasons weighted 5/4/3, regressed toward the league by Stat Stability's "
             "sample sizes, moved along the aging curve. 80% range: the 10th to 90th percentile of past misses for "
             "players like him.")


def _proj_cols():
    out = []
    for k in _PROJ_KEYS:
        base_key = k[:-2] if k.endswith("36") else k
        base = PLAYER_SEASON_COLUMNS[base_key]
        label = base.label + (" per 36" if k.endswith("36") else "")
        short = base.short + ("/36" if k.endswith("36") else "")
        fmt = base.fmt if base.fmt != "int" else "num1"
        among = PROJ_AMONG.get(k)
        hib = base.higher_is_better
        out += [
            Column(f"proj_{k}", f"Projected {label.lower() if label[:2] != label[:2].upper() else label}", f"p{short}",
                   "Projected", fmt, "none", f"pj.{k}_p", 2001, hib, n_unit="player-seasons",
                   ci_lo=f"pj.{k}_lo", ci_hi=f"pj.{k}_hi", ci_label="80% range", method="projections",
                   among=among, sources=PJ_SOURCES, note=PROJ_NOTE),
            Column(f"act_{k}", f"Actual {label.lower() if label[:2] != label[:2].upper() else label}", f"a{short}",
                   "Actual", fmt, "none", f"pj.{k}_a", 2001, hib, n_unit="player-seasons", among=among,
                   sources=PJ_SOURCES + ("player_season_stats",),
                   note="What happened that season (backtest seasons only; none yet for 2026-27)."),
            Column(f"miss_{k}", f"Miss on {label.lower() if label[:2] != label[:2].upper() else label}",
                   f"Δ{short}", "Actual − projected", "pct" if fmt == "pct" else "signed1" if fmt != "signed2" else fmt,
                   "wmean", f"(pj.{k}_a - pj.{k}_p)", 2001, None, weight="1", weight_label="player-seasons",
                   n_unit="player-seasons", among=among, method="projections", sources=PJ_SOURCES,
                   note="Actual minus projected; combined, the mean miss (the projection's bias)."),
            Column(f"in_{k}", f"Inside the 80% range: {label.lower() if label[:2] != label[:2].upper() else label}",
                   f"in {short}", "Inside the 80% range", "pct", "ratio",
                   f"(CASE WHEN pj.{k}_a IS NULL OR pj.{k}_lo IS NULL THEN NULL WHEN pj.{k}_a BETWEEN pj.{k}_lo AND "
                   f"pj.{k}_hi THEN 1.0 ELSE 0.0 END)", 2001, None,
                   num=(f"(CASE WHEN pj.{k}_a IS NULL OR pj.{k}_lo IS NULL THEN NULL WHEN pj.{k}_a BETWEEN pj.{k}_lo "
                        f"AND pj.{k}_hi THEN 1 ELSE 0 END)"), den="1", n_unit="player-seasons",
                   agg_text="share of player-seasons whose actual fell inside the range (80% if calibrated)",
                   among=among, method="projections", sources=PJ_SOURCES),
        ]
    return out


PLAYER_PROJECTION = Dataset(
    key="player_projection", label="Projections", entity="player",
    description=("Next-season projections with 80% ranges: the backtest for 2000-01 to 2025-26 (made from earlier "
                 "seasons only) beside what happened, and 2026-27's projections."),
    from_sql=PROJ_FROM, where=(),
    season_sql="pj.season", entity_sql="pj.player_id", games_sql="COALESCE(s.gp, 0)", minutes_sql=None, poss_sql=None,
    per_modes=("game",), row_label="seasons",
    tables=PJ_SOURCES + ("player_season_stats",), upstream="nba_api (stats.nba.com) + Basketball-Reference seasons",
    row_fields=(("player_id", "pj.player_id"), ("season", "pj.season"), ("team", "COALESCE(pj.team, s.team_abbreviation)")),
    groupings={
        "entity": Grouping(("pj.player_id",), ("player_id",)),
        "season": Grouping(("pj.season",), ("season",)),
    },
    dims={},
    columns=_cols(*_proj_cols()),
    names_from_ids=True,
    notes=("Backtest rows are players with 500+ minutes that season (shooting % also 100 FGA, 50 3PA or 50 FTA), "
           "projected from earlier seasons only; 2026-27's are every player with 250+ minutes over his last three "
           "seasons.",
           "A projection and its range describe one season; only the misses and the range coverage combine.",
           "Games (G) are the games he actually played that season (none yet for 2026-27)."),
)

DATASETS = {d.key: d for d in (PLAYER_SEASON, PLAYER_GAME, TEAM_SEASON, TEAM_GAME, PLAYER_ONOFF, LINEUP_SEASON,
                                PAIR_SEASON, TEAM_POSSESSIONS, PLAYER_PROJECTION)}


# ─── the existing pages' catalogues, built from the columns above ──────────

def leaderboard_stats():
    """routers/leaderboard.py STATS: key -> (label, group, format, first_season,
    higher_is_better, attempts_column), in the Leaderboard's order. Its SQL
    reads the season table's column named by the key."""
    return {c.key: (c.label, c.group, c.fmt, c.first_season, c.higher_is_better, c.attempts)
            for c in PLAYER_SEASON_COLUMNS.values() if "leaderboard" in c.pages}


def game_finder_stats():
    """routers/game_log.py STATS: key -> (label, per-game SQL, format), in the
    Game Finder's order, with its own wording."""
    return {c.key: (GAME_FINDER_LABELS.get(c.key, c.label), c.sql, c.fmt)
            for c in PLAYER_GAME_COLUMNS.values() if "game_finder" in c.pages}


def source_tables(ds, cols):
    """(tables, upstream) behind a response that reads these columns."""
    if not ds.optional:
        return list(ds.tables), ds.upstream
    extra = sorted({t for c in cols for t in c.sources if t in ds.optional[0]})
    return list(ds.tables) + extra, ds.upstream + (f"; {ds.optional[1]}" if extra else "")


def agg_label(col):
    """How a column combines over several rows, in words."""
    if col.agg_text:
        return col.agg_text
    if col.kind == "wmean":
        return f"mean weighted by {col.weight_label}"
    return AGG_LABELS[col.kind]
