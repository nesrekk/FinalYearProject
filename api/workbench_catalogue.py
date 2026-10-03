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
KINDS = ("count", "sum", "ratio", "wmean", "none")
PER_MODES = {"game": "per game", "total": "totals", "per36": "per 36 minutes", "per100": "per 100 possessions"}
AGG_LABELS = {
    "count": "summed total ÷ summed games (or minutes, or possessions)",
    "sum": "summed",
    "ratio": "summed numerator ÷ summed denominator",
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


@dataclass(frozen=True)
class Dim:
    """A field rows can be filtered or grouped on (not a stat)."""
    key: str
    label: str
    sql: str
    type: str          # int | team | bool | date | text
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
)

PLAYER_SEASON = Dataset(
    key="player_season", label="Player seasons", entity="player",
    description="One row per player-season, 1949-50 on: per-game box score, shooting, rates and impact.",
    from_sql="FROM player_season_stats s LEFT JOIN player_bio b ON b.player_id = s.player_id",
    where=(),
    season_sql="s.season", entity_sql="s.player_id", games_sql="s.gp", minutes_sql="s.min * s.gp",
    poss_sql="s.poss", per_modes=("game", "total", "per36", "per100"), row_label="seasons",
    tables=("player_season_stats", "player_bio"),
    upstream="nba_api (stats.nba.com) from 2009-10 + Basketball-Reference before and for BPM/VORP",
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
           "values to 0.1, so a total can be off by up to 0.05 × games.",),
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

DATASETS = {d.key: d for d in (PLAYER_SEASON, PLAYER_GAME, TEAM_SEASON, TEAM_GAME)}


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


def agg_label(col):
    """How a column combines over several rows, in words."""
    if col.agg_text:
        return col.agg_text
    if col.kind == "wmean":
        return f"mean weighted by {col.weight_label}"
    return AGG_LABELS[col.kind]
