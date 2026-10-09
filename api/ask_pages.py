"""
ask_pages.py
============
What "ask in English everywhere" (round 10, part B) may open: every page of the
app (App.jsx PAGES), the Analytics tabs, and for each the link inputs it reads
(utils/useUrlState.js parseParam / useUrlSync keys in its component), with each
input's type and allowed values. ask_lib.py builds the model's prompt and
answer schema from this, and to_action() checks the model's answer against it;
nothing here runs a query.

api/tests/test_ask.py checks this registry against the frontend: every page id
is in App.jsx, every tab in analyticsTabs.js, every key here is read by the
page's component, and every literal list of allowed values equals the
component's. Keep the two in step: a page that gains a link input gets its key
here in the same commit.

Input types (`K`):
    player        a player's full name, resolved to his NBA id by the app's own search (an id in the link)
    team          one of today's 30 team codes (teams_lib.FRANCHISES)
    season        an end year (2022-23 = 2023), within the page's data range
    season_label  the same season written "2022-23" (Shot Charts)
    date          YYYY-MM-DD
    int / num     a number within [lo, hi]
    enum          one of `values`
    list          comma-separated items, each checked by `item` ("stat:op:value", "stat:value", a stat, a period)

Pages whose component reads nothing from the link have no keys: an English ask
lands on them bare (Stat Leaders → the Leaderboard Builder instead, Team
Comparison → a board; R10-010).
"""

from dataclasses import dataclass, field

from teams_lib import FRANCHISES

CODES = tuple(sorted(FRANCHISES))
assert len(CODES) == 30, CODES

# The data's first season per source (CLAUDE.md "Environment gotchas"; checked against the tables 2026-10-09).
FIRST_PLAYER_SEASON = 1950          # player_season_stats
FIRST_TEAM_SEASON = 1947            # team_seasons
FIRST_SHOT_SEASON = 1997            # player_shots (shot locations)
FIRST_PBP_SEASON = 2021             # player_game_lines and every play-by-play tool
FIRST_COMPARE_SEASON = 2010         # Player Comparison's season picker (NBA.com's per-season pools)
FIRST_SCORE_DATE = "2009-10-27"     # game_scores
LAST_SALARY_SEASON = 2025           # player_salaries

# Enumerated values the pages read (their components' literal lists or the options routes they fetch; the
# lists are copied here so the registry needs no database and the test can compare them with the code).
LEADERBOARD_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min", "fg_pct",
                     "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct", "tov_pct",
                     "off_rating", "def_rating", "net_rating", "plus_minus", "bpm", "obpm", "dbpm", "vorp",
                     "impact_score_raw", "age")
STAT_LABELS = {
    "pts": "points", "reb": "rebounds", "ast": "assists", "stl": "steals", "blk": "blocks", "tov": "turnovers",
    "fg3m": "3-pointers made", "fg3a": "3-point attempts", "fta": "free-throw attempts", "oreb": "offensive rebounds",
    "dreb": "defensive rebounds", "min": "minutes", "fg_pct": "FG%", "fg3_pct": "3P%", "ft_pct": "FT%",
    "ts_pct": "true shooting %", "efg_pct": "effective FG%", "usg_pct": "usage %", "ast_pct": "assist %",
    "reb_pct": "rebound %", "oreb_pct": "offensive rebound %", "tov_pct": "turnover %", "off_rating": "offensive rating",
    "def_rating": "defensive rating", "net_rating": "net rating", "plus_minus": "plus-minus", "bpm": "BPM (box plus-minus)",
    "obpm": "offensive BPM", "dbpm": "defensive BPM", "vorp": "VORP", "impact_score_raw": "Impact Score", "age": "age",
    "fgm": "field goals made", "fga": "field-goal attempts", "ftm": "free throws made",
}
GAME_FINDER_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "oreb", "dreb", "fgm", "fga", "fg3m", "fg3a", "ftm",
                     "fta", "min", "fg_pct", "fg3_pct", "ft_pct", "ts_pct", "plus_minus")
GAME_FINDER_OPS = ("gte", "gt", "lte", "lt", "eq")
LINE_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3a", "fg3_pct", "ft_pct", "ts_pct", "usg_pct", "ast_pct",
              "reb_pct", "net_rating", "min", "age")
PCT_STATS = ("fg_pct", "fg3_pct", "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct", "tov_pct")
HOT_STREAK_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fta", "min", "fg_pct", "fg3_pct", "ft_pct",
                    "ts_pct", "usg_pct")
HOT_STREAK_WINDOWS = ("5", "10", "20")
SPLITS = ("home", "rest", "travel", "opp")
SPLIT_LABELS = {"home": "home / away", "rest": "rest days (back-to-backs)", "travel": "travel", "opp": "opponent strength"}
SPLIT_STATS = ("min", "pts", "reb", "ast", "stl", "blk", "tov", "fg3a", "ts_pct", "fg3_pct", "usg_pct")
AGING_STATS = ("ast", "ast_pct", "blk", "bpm", "dbpm", "efg_pct", "fg3_pct", "fg3a", "fg3m", "fg_pct", "ft_pct", "fta",
               "min", "obpm", "oreb", "oreb_pct", "pts", "reb", "reb_pct", "stl", "tov", "tov_pct", "ts_pct", "usg_pct")
STABILITY_STATS = ("ast", "ast_pct", "blk", "def_rating", "efg_pct", "fg3_pct", "fg3a", "fg3m", "fg_pct", "ft_pct",
                   "fta", "min", "net_rating", "off_rating", "oreb", "oreb_pct", "plus_minus", "pts", "reb", "reb_pct",
                   "stl", "tov", "tov_pct", "ts_pct", "usg_pct")
PROJ_STATS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min", "fg_pct", "fg3_pct",
              "ft_pct", "ts_pct", "efg_pct", "usg_pct", "ast_pct", "reb_pct", "oreb_pct", "tov_pct", "bpm", "obpm", "dbpm")
PLAY_CATS = {
    "fga": "shot attempts", "made": "made shots", "made2": "made twos", "made3": "made threes", "miss": "missed shots",
    "miss2": "missed twos", "miss3": "missed threes", "ast": "assists", "ast3": "assists on threes", "blk": "blocks",
    "stl": "steals", "tov": "turnovers", "foul": "fouls", "ft": "free throws", "ftm": "made free throws",
    "ftx": "missed free throws", "reb": "rebounds", "oreb": "offensive rebounds", "dreb": "defensive rebounds",
}
PERIODS = ("1", "2", "3", "4", "ot")
ROLE_PRESETS = {
    "three_and_d": "3-and-D wing", "rim_protector": "rim protector", "stretch_big": "stretch big",
    "secondary_creator": "secondary creator", "point_of_attack": "point-of-attack defender",
    "glass_cleaner": "glass cleaner", "floor_spacer": "floor spacer",
}
GAMES = {"guess": "Guess the Player", "blurred": "Blurred Player", "higherlower": "Higher or Lower", "trivia": "Trivia",
         "guessgame": "Guess the Game"}
# Methodology cards (components/pages/methodologyContent.js SECTIONS[].items[].id), in the page's order.
METHODOLOGY_CARDS = {
    "awards": "award models (MVP, DPOY, ROY, All-NBA)", "wp": "win probability", "projections": "next-season projections",
    "simulator": "season simulator", "ledger": "forecast ledger", "lineuppredictor": "lineup predictor",
    "reportcard": "model report card", "shotmaking": "shot-making model", "hotstreaks": "hot streaks",
    "splits": "situational splits", "madness": "March Madness", "bpm": "BPM", "rapm": "RAPM", "dad": "DAD Index",
    "rim": "rim deterrence", "assists": "assist networks", "bestgames": "best games", "gravity": "gravity / spacing",
    "contracts": "contract value", "tradeimpact": "trade impact", "rolefinder": "role player finder",
    "garbage": "garbage-time deflator", "helio": "heliocentricity", "era": "era translator",
    "similarity": "season similarity", "statline": "stat line finder", "roles": "player roles",
    "offstyle": "offensive style", "prospects": "draft prospects", "coaching": "coaching decisions",
    "clutch": "clutch", "pipeline": "college to NBA pipeline", "draft": "draft value", "referees": "referee tendencies",
    "matchups": "matchups", "stints": "lineup stints", "lineups": "lineup chemistry", "pairs": "pair chemistry",
    "onoff": "on/off", "luck": "luck and schedule", "scouting": "scouting reports", "stability": "stat stability",
    "aging": "aging curves", "dataquality": "data quality", "workbench": "workbench charts", "finder": "player finder",
    "finderparse": "typing the Finder's search in English", "ask": "ask in English everywhere",
}


@dataclass(frozen=True)
class K:
    """One link input: how to read and check a value for it."""
    type: str                         # player | team | season | season_label | date | int | num | enum | list
    what: str = ""                    # for the prompt
    values: tuple = ()                # enum
    labels: dict = field(default_factory=dict)   # enum value → words, for the prompt and the preview
    lo: float = None                  # int / num
    hi: float = None
    item: str = None                  # list: "cond" (stat:op:value), "line" (stat:value), "stat", "period"
    stats: tuple = ()                 # list items' stats
    default: str = None               # enum: the page's own default; a value equal to it is never written to the link


@dataclass(frozen=True)
class Page:
    label: str                        # the nav's label (components/layout/navConfig.js)
    what: str                         # one line for the prompt
    keys: dict = field(default_factory=dict)          # key → K
    first_season: int = None          # the page's data starts here (a season before it = no_data)
    free: tuple = ()                  # link keys the page reads that the engine never fills (names beside ids, paging)
    bare_hint: str = None             # pages with no link inputs: what to do instead


def _enum(what, values, labels=None, default=None):
    return K("enum", what, tuple(values), dict(labels or {}), default=default)


SEASON = K("season", "a season as its end year (2022-23 = 2023)")
TEAM = K("team", "a team code")
PLAYER = K("player", "a player's full name")
SEASON_FROM = K("season", "first season of a range (end year)")
SEASON_TO = K("season", "last season of a range (end year)")
STAT_ENUM = _enum("a stat", LEADERBOARD_STATS, STAT_LABELS)

PAGES = {
    "dashboard": Page("Dashboard", "the landing page: today's games, news and leaders."),
    "scores": Page("Live Scores", "a day's scoreboard: today by default, or a past date.",
                   {"date": K("date", "a past date, YYYY-MM-DD; leave out for today")}),
    "news": Page("News", "league news."),
    "standings": Page("Standings", "the standings (current season)."),
    "teams": Page("Team Comparison", "two teams side by side; reads nothing from a link.",
                  bare_hint="never for named teams: 'Celtics vs Lakers' = build_board with teams [BOS, LAL]"),
    "players": Page("Player Stats", "a sortable table of every player's season; reads nothing from a link.",
                    bare_hint="a stat's leaders go to the Leaderboard Builder"),
    "compare": Page("Player Comparison", "two players side by side in one season (the current one by default); two "
                    "players' careers or several seasons = a board.",
                    {"aid": K("player", "the first player"), "bid": K("player", "the second player"), "season": SEASON},
                    first_season=FIRST_COMPARE_SEASON, free=("a", "b")),
    "shotcharts": Page("Shot Charts", "one player's shot chart, heat map, shot-making, quality map or shot value; "
                       "regular season unless games says otherwise.",
                       {"pid": K("player", "the player"),
                        "season": K("season_label", "the season (end year or '2015-16'); leave out for his latest"),
                        "games": _enum("which games; leave out for the regular season", ("playoffs", "all"),
                                       {"playoffs": "playoffs and play-in", "all": "all games"}),
                        "view": _enum("a view other than the dots", ("heatmap", "shotmaking", "quality", "value"),
                                      {"heatmap": "heat map", "shotmaking": "shot-making (expected FG%)",
                                       "quality": "shot quality map (hexagons)", "value": "shot value added"})},
                       first_season=FIRST_SHOT_SEASON, free=("player",)),
    "analytics": Page("Analytics", "the Analytics tools; always with a tab (below)."),
    "leaders": Page("Stat Leaders", "fixed leaderboards; reads nothing from a link.",
                    bare_hint="a stat's leaders in a season or range go to the Leaderboard Builder (builder)"),
    "trade": Page("Trade Analyzer", "a trade between two teams: salaries and fit.",
                  {"ta": K("team", "the first team"), "pa": K("player", "a player from the first team"),
                   "tb": K("team", "the second team"), "pb": K("player", "a player from the second team"),
                   "season": SEASON}),
    "tradeimpact": Page("Trade Impact", "what a trade would do to both teams' ratings.",
                        {"ta": K("team", "the first team"), "pa": K("player", "a player from the first team"),
                         "tb": K("team", "the second team"), "pb": K("player", "a player from the second team"),
                         "season": SEASON}),
    "draft": Page("Draft Value Guide", "value by draft pick; reads nothing from a link."),
    "hof": Page("Hall of Fame", "Hall of Fame members and chances; reads nothing from a link."),
    "greats": Page("Greats of the Game", "profiles of the greats; reads nothing from a link."),
    "rookies": Page("Rookie Class Tracker", "the current rookie class; reads nothing from a link."),
    "games": Page("Games", "the quiz games.", {"g": _enum("which game", tuple(GAMES), GAMES)}),
    "learn": Page("Learn the Game", "explanations of the stats and the rules; reads nothing from a link."),
    "methodology": Page("Methodology", "how each model works and was checked.",
                        {"card": _enum("the model or tool asked about", tuple(METHODOLOGY_CARDS), METHODOLOGY_CARDS)}),
    "builder": Page("Leaderboard Builder", "the leaders in one stat over a season or a range of seasons, optionally one "
                    "team's players; the place for 'who led', 'best', 'most', 'top N', 'lowest'.",
                    {"stat": STAT_ENUM, "from": SEASON_FROM, "to": SEASON_TO, "team": TEAM,
                     "order": _enum("'low' only for the lowest / fewest / worst", ("high", "low"), default="high"),
                     "n": _enum("how many rows, only when a number is asked ('top 10')", ("10", "25", "50", "100")),
                     "gp": K("int", "a games floor, only when asked", lo=1, hi=82),
                     "mpg": K("num", "a minutes-per-game floor, only when asked", lo=0, hi=48)},
                    first_season=FIRST_PLAYER_SEASON),
    "regression": Page("Regression Explorer", "how two season stats move together across players.",
                       {"x": _enum("the stat on the x axis", LEADERBOARD_STATS, STAT_LABELS),
                        "y": _enum("the stat on the y axis", LEADERBOARD_STATS, STAT_LABELS),
                        "from": SEASON_FROM, "to": SEASON_TO}, first_season=FIRST_PLAYER_SEASON),
    "breakouts": Page("Breakout Detector", "players whose stats jumped (or fell) most from the season before.",
                      {"season": SEASON, "dir": _enum("'down' for declines", ("up", "down"), default="up"),
                       "stats": K("list", "the stats to weigh, comma-separated", item="stat", stats=LEADERBOARD_STATS)},
                      first_season=FIRST_PLAYER_SEASON),
    "stability": Page("Stat Stability", "how many games a stat needs before it is half signal.",
                      {"stat": _enum("the stat", STABILITY_STATS, STAT_LABELS)}),
    "player": Page("Player Profile", "one player's page: career stats, game log (2020-21 on), shot zones, on/off, RAPM, "
                   "Rating Tracker, clutch, next-season projection, contract, awards.",
                   {"id": K("player", "the player")}),
    "team": Page("Team Profile", "one team's season: record, roster, ratings, schedule; its latest season by default.",
                 {"abbr": TEAM, "season": SEASON}, first_season=FIRST_TEAM_SEASON),
    "rolefinder": Page("Role Player Finder", "players who fit a role (3-and-D, rim protector, …).",
                       {"p": _enum("the role", tuple(ROLE_PRESETS), ROLE_PRESETS)}),
    "era": Page("Era Translator", "one player-season translated to another era's pace and scoring.",
                {"player": K("player", "the player"), "season": K("season", "his season to translate (end year)"),
                 "to": K("season", "the season to translate into (end year)")}, first_season=FIRST_PLAYER_SEASON),
    "aging": Page("Aging Curves", "how a stat changes with age, with one player's seasons laid over the curve.",
                  {"stat": _enum("the stat", AGING_STATS, STAT_LABELS),
                   "era": _enum("which seasons the curve is fitted on", ("all", "three_point", "modern"),
                                {"all": "every season", "three_point": "the three-point era", "modern": "2009-10 on"}, default="all"),
                   "player": K("player", "a player to lay over the curve")}),
    "projections": Page("Projections", "next season's projection for every current player, sorted by one stat; "
                        "one player's own projection is on his profile.",
                        {"stat": _enum("the stat to sort by", PROJ_STATS, STAT_LABELS),
                         "team": K("team", "only one team's players")}),
    "statline": Page("Stat Line Finder", "seasons most like a stat line ('seasons like 27 points, 7 rebounds, 7 "
                     "assists').",
                     {"line": K("list", "the line, comma-separated stat:value (percentages as shares: ts_pct:0.65)",
                                item="line", stats=LINE_STATS),
                      "season": K("season", "only seasons from this one season"), "from": SEASON_FROM, "to": SEASON_TO,
                      "gp": K("int", "a games floor, only when asked", lo=0, hi=82),
                      "n": _enum("how many results, only when a number is asked", ("10", "25", "50"))},
                     first_season=FIRST_PLAYER_SEASON),
    "gamefinder": Page("Game Finder", "single games meeting conditions (one player's or everyone's): 'Curry's 50-point "
                       "games', 'all 60-point games', 'triple-doubles this season'; mode streaks for the longest run.",
                       {"f": K("list", "the conditions, comma-separated stat:op:value (ops gte, gt, lte, lt, eq; a "
                                       "triple-double = pts:gte:10,reb:gte:10,ast:gte:10)",
                               item="cond", stats=GAME_FINDER_STATS),
                        "player": K("player", "one player's games only"),
                        "mode": _enum("'streaks' for the longest run of such games", ("games", "streaks"), default="games"),
                        "from": SEASON_FROM, "to": SEASON_TO, "team": TEAM,
                        "home": _enum("home or away games only", ("home", "away"), {"away": "on the road"}),
                        "result": _enum("wins or losses only", ("W", "L"), {"W": "wins", "L": "losses"}),
                        "min": K("num", "a minutes floor in the game", lo=0, hi=60),
                        "n": K("int", "how many rows, only when asked", lo=25, hi=200)},
                       first_season=FIRST_PBP_SEASON, free=("pg",)),
    "plays": Page("Play Finder", "single plays from the play-by-play: one player's made threes, blocks, assists …, by "
                  "period, clutch time, team or opponent.",
                  {"player": K("player", "the player"), "cat": _enum("the kind of play", tuple(PLAY_CATS), PLAY_CATS),
                   "from": SEASON_FROM, "to": SEASON_TO, "team": TEAM, "opp": K("team", "the opponent"),
                   "home": _enum("home or away only", ("home", "away")),
                   "per": K("list", "periods, comma-separated (1, 2, 3, 4, ot)", item="period"),
                   "clutch": _enum("'1' for clutch time only (last 5 minutes within 5 points)", ("1",))},
                  first_season=FIRST_PBP_SEASON, free=("pg",)),
    "hotstreaks": Page("Hot Streak Checker", "who is hot or cold over the last N games, in one stat.",
                       {"stat": _enum("the stat", HOT_STREAK_STATS, STAT_LABELS),
                        "window": _enum("the last N games", HOT_STREAK_WINDOWS),
                        "dir": _enum("'cold' for slumps", ("hot", "cold"), default="hot"), "season": SEASON},
                       first_season=FIRST_PBP_SEASON),
    "rapm": Page("RAPM", "RAPM ratings and their leaders (any ask naming RAPM, xRAPM or the Rating Tracker): one "
                 "season, with a BPM prior, three seasons, shot-aware, or the Rating Tracker.",
                 {"version": _enum("which rating", ("single", "prior", "multi", "shotaware", "tracker"),
                                   {"single": "one-season RAPM", "prior": "RAPM with a BPM prior",
                                    "multi": "three-season RAPM", "shotaware": "shot-aware xRAPM",
                                    "tracker": "the Rating Tracker"}, default="single"),
                  "season": SEASON, "team": TEAM,
                  "kind": _enum("tracker: as of then (filtered) or with hindsight (smoothed); shot-aware: prior or single",
                                ("filtered", "smoothed", "prior", "single"))},
                 first_season=FIRST_PBP_SEASON),
    "rotations": Page("Rotations", "a team's rotation chart: who played when, lineups and stints.",
                      {"team": TEAM, "season": SEASON}, first_season=FIRST_PBP_SEASON),
    "assists": Page("Assist Network", "who assists whom on a team.",
                    {"team": TEAM, "season": SEASON, "player": K("player", "one player's assists only"),
                     "v": _enum("'duos' for the best two-man connections", ("team", "duos"), default="team")},
                    first_season=FIRST_PBP_SEASON),
    "simulator": Page("Season Simulator", "the rest of a season simulated: playoff and title odds.",
                      {"season": SEASON, "team": TEAM}),
    "ledger": Page("Forecast Ledger", "the locked preseason forecasts and how they are scoring.",
                   {"tab": _enum("which view", ("live", "weekly", "preseason"),
                                 {"live": "live scoring", "weekly": "the weekly report", "preseason": "the preseason lock"}, default="live"),
                    "team": TEAM}),
    "bestgames": Page("Best Games & Upsets", "the best games and biggest upsets by win probability.",
                      {"v": _enum("'upsets' for upsets", ("games", "upsets"), default="games"), "season": SEASON, "team": TEAM,
                       "side": _enum("games a team won or lost", ("won", "lost"))}, first_season=FIRST_PBP_SEASON),
    "possessions": Page("Possession Explorer", "a team's possessions by how they start, offence and defence.",
                        {"team": TEAM, "season": SEASON, "v": _enum("offence or defence", ("off", "def"), default="off")},
                        first_season=FIRST_PBP_SEASON),
    "coaching": Page("Coaching Decisions", "timeouts after runs, challenges, fouling up three, two-for-ones.",
                     {"v": _enum("which decision", ("timeout", "challenge", "foul_up3", "twoforone", "tests"),
                                 {"timeout": "timeouts", "challenge": "challenges", "foul_up3": "fouling up 3 late",
                                  "twoforone": "two-for-ones", "tests": "all tests"}),
                      "team": TEAM, "season": SEASON}, first_season=FIRST_PBP_SEASON),
    "splits": Page("Situational Splits", "home/away, rest, travel and opponent-strength splits in one stat.",
                   {"split": _enum("the split", SPLITS, SPLIT_LABELS), "stat": _enum("the stat", SPLIT_STATS, STAT_LABELS),
                    "season": SEASON, "team": TEAM}, first_season=FIRST_PBP_SEASON),
    "watchlist": Page("Watchlist", "the user's starred players; reads nothing from a link."),
    "saved": Page("Saved analyses", "the user's saved views; reads nothing from a link."),
    "report": Page("Report builder", "the user's report; reads nothing from a link."),
    "coverage": Page("Data Coverage", "what data the app has, by season; reads nothing from a link."),
    "reportcard": Page("Model Report Card", "every model's held-out scores; reads nothing from a link."),
    "quality": Page("Data Quality", "the data-quality checks; reads nothing from a link."),
    "workbench": Page("Workbench", "boards of tables, charts and tools; a new board is built with build_board."),
}

# The Analytics tabs (components/analytics/analyticsTabs.js) and the link inputs their sections read.
TABS = {
    "mvp": Page("Awards Race", "MVP, DPOY, ROY and All-NBA chances this season."),
    "impact": Page("Impact Rankings", "the Impact Score rankings (the app's own composite; not RAPM, which has its own page)."),
    "validation": Page("Model Validation", "how the award models did on past seasons."),
    "ledger": Page("Prediction Ledger", "the logged award predictions."),
    "similarity": Page("Season Similarity", "seasons most like one player-season."),
    "archetypes": Page("Player Archetypes", "player clusters."),
    "radar": Page("Radar Compare", "radar charts of players."),
    "trends": Page("Trend Analysis", "league trends over the years."),
    "trajectory": Page("Career Trajectory", "career paths."),
    "helio": Page("Heliocentricity", "how much of a team's offence runs through one player."),
    "wpa": Page("Clutch WPA", "clutch win-probability added, the leaders."),
    "matchups": Page("Matchup Finder", "head-to-head matchups."),
    "garbage": Page("Garbage-Time Deflator", "stats with garbage time taken out."),
    "dad": Page("DAD Index", "defensive activity and deterrence."),
    "rim": Page("Rim Deterrence", "who keeps shots away from the rim.",
                {"season": SEASON, "team": TEAM, "pos": _enum("'bigs' for bigs only", ("all", "bigs"), default="all")},
                first_season=FIRST_PBP_SEASON),
    "vegas": Page("Vegas Scanner", "markets against the app's own odds (never advice)."),
    "playoffs": Page("Playoff Forecaster", "playoff odds."),
    "lineups": Page("Lineup Chemistry", "five-man lineups, best and worst.",
                    {"season": SEASON, "order": _enum("'worst' for the worst lineups", ("best", "worst"), default="best")},
                    first_season=FIRST_PBP_SEASON),
    "pairs": Page("Pair Chemistry", "two-man pairs on a team.",
                  {"season": SEASON, "team": TEAM, "m": _enum("the metric", ("net", "off", "def"),
                                                                {"net": "net rating", "off": "offence", "def": "defence"}, default="net")},
                  first_season=FIRST_PBP_SEASON),
    "onoff": Page("On/Off", "a team's players on and off the floor (or the league's leaders, or stars with and without).",
                  {"season": SEASON, "team": TEAM,
                   "view": _enum("'league' for league leaders, 'stars' for team with and without its stars",
                                 ("team", "league", "stars"), default="team")}, first_season=FIRST_PBP_SEASON),
    "luck": Page("Luck & Schedule", "how much of a team's record is luck and schedule.",
                 {"season": SEASON, "team": TEAM}),
    "spacing": Page("Spacing Lab", "spacing and gravity."),
    "contracts": Page("Contract Value", f"contract value against production (salaries through {LAST_SALARY_SEASON - 1}-"
                      f"{str(LAST_SALARY_SEASON)[-2:]})."),
    "replay": Page("Game Replay", "a stored game replayed on its win-probability curve."),
    "withwithout": Page("With/Without a Star", "a team with and without a star."),
    "fatigue": Page("Schedule Fatigue", "rest, travel and back-to-backs."),
    "referees": Page("Referee Tendencies", "referee crews' tendencies."),
    "prospects": Page("Draft Prospects", "the draft prospects model."),
    "pipeline": Page("College → NBA", "which college numbers carry to the NBA."),
    "madness": Page("March Madness", "the tournament model."),
}

# Which page an ask that names a per-player block should open (the file's rule: a player's X with no page named
# opens his profile); listed for the prompt.
PROFILE_BLOCKS = ("game log", "next-season projection", "on/off", "RAPM", "Rating Tracker", "clutch", "contract and "
                  "salary", "shot zones", "awards")


def all_keys():
    """Every link key the engine may fill, across pages and tabs (the answer schema's enum)."""
    keys = set()
    for p in list(PAGES.values()) + list(TABS.values()):
        keys |= set(p.keys)
    return tuple(sorted(keys))


def page_for(page, tab):
    """The registry entry whose keys apply: the tab's on Analytics, else the page's."""
    if page == "analytics":
        return TABS.get(tab)
    return PAGES.get(page)
