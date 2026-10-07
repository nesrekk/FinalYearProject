"""
build_assist_network.py
========================
Who assists whom: every assisted basket of every regular-season game
2020-21 to 2025-26, from the ESPN play-by-play.

Every made field goal comes from the shared play-by-play parser
(pbp_lineups.Game.parse: the same code, and so the same shots and the same
names, as player_game_lines; nothing here re-parses shots or matches names
itself). ESPN writes the passer into the made shot's text, "Nikola Jokic
makes 2-foot dunk (Jamal Murray assists)"; the parser resolves that name to
an NBA id the same way it does for the player lines (the game's own
players first, then player_season_stats for that season and team, then
names only one player has had since 2010). A made shot is worth two or
three as the parser calls it (the shooter's team score step when it is 2 or
3), and its kind comes from ESPN's action type:
  rim      layups, dunks, alley-oops, finger rolls, tips, putbacks
  floater  floaters and hooks
  jumper   every other two (jump shots, pull-ups, fadeaways, a handful of
           untyped twos)
  three    any made three.

Games: every ESPN game linked to a real regular-season result in
game_scores, i.e. all of them except the three NBA Cup finals (they don't
count in regular-season stats; player_game_lines keeps them, Game Log drops
them the same way). Minutes and games come from player_game_lines over the
same games; its one row with team 'NaN' (3 minutes, 2021-22) is skipped.

Left out of the pairs, and counted:
  - assisted makes whose passer's name matches no id (ESPN gives ~100-155
    players a season no id and their names aren't in player_season_stats,
    the parser's only id source; about 0.3% of assists). The make still
    counts as assisted for the scorer (the text says so);
  - makes by a shooter with no id: stored as scorer_id 0 ("unidentified");
  - 3 made shots credited as assisted by the shooter himself and one
    "Double Personal Foul" event the parser reads as a make with the other
    team's passer: data errors, left out of pairs and shares alike.

Tables written (dropped and rebuilt):
  assist_pairs            season, team, passer, scorer: assists, games with
                          one, assisted 2s and 3s, points on those makes,
                          and assists by shot kind;
  player_assisted_share   season, team, player: minutes and games (from
                          player_game_lines), his made 2s and 3s and how
                          many were assisted (by kind), his own assists and
                          the points on them, how many of his assisted
                          makes came from an unidentified passer;
  assist_seasons          per season: league made shots and assisted share
                          by kind, and the checks below.

Checks printed (and stored in assist_seasons):
  - each player-season's assists here equal his player_game_lines assists
    (same parser, so they must match exactly; the 4 errors above aside);
  - every team-season's assisted makes against the league's known
    assisted share (~60-64% of made shots);
  - the parser's assists against NBA.com's season totals
    (player_season_stats, players with 20+ games).

Usage:
    cd scripts && python3 build_assist_network.py     (~1 min)
    cd scripts && python3 build_assist_network.py --season 2027
        # round 9 step 3: only that season's rows of the three tables are
        # deleted and rebuilt (the same per-game parse and per-season sums);
        # needs the full build's tables.
Rerun after new play-by-play is loaded or build_player_game_lines.py is
rebuilt (they must agree).
"""

import re
import time
import warnings
from collections import Counter, defaultdict

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import ASSIST_RE, Game, load_espn, load_season_names
import season_mode as SM

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

KINDS = ["rim", "floater", "jumper", "three"]
RIM_RE = re.compile(r"layup|dunk|alley oop|finger roll|tip|putback", re.I)
FLOATER_RE = re.compile(r"float|hook", re.I)
# ESPN games that are regular-season games: linked to a real result in game_scores. The only ESPN games
# without one are the three NBA Cup finals, which don't count in regular-season stats.
REGULAR_SQL = "SELECT DISTINCT 'espn_' || espn_id FROM game_scores WHERE espn_id IS NOT NULL"


def shot_kind(action, val):
    if val == 3:
        return "three"
    action = (action or "").replace("\n", " ")
    if RIM_RE.search(action):
        return "rim"
    if FLOATER_RE.search(action):
        return "floater"
    return "jumper"


def collect_makes(conn, cur, season=None):
    """Every made field goal the parser counts, with the named passer (`season`: that season's games only)."""
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn, season=season)
    cur.execute(REGULAR_SQL)
    regular = {gid for (gid,) in cur.fetchall()}
    cup_finals = sorted(set(games.game_id) - regular)
    print(f"left out: {len(cup_finals)} games with no regular-season result in game_scores (the NBA Cup finals): "
          f"{', '.join(cup_finals)}")
    rows, unmatched = [], Counter()
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None or g.game_id not in regular:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        game.home = g.home_team  # parse() needs the home side for score steps (walk() sets it the same way)
        desc = dict(zip(ev["action_number"].astype(int), ev["description"]))
        for e in game.parse():
            if e["kind"] != "fg" or not e["made"]:
                continue
            named = ASSIST_RE.search(desc.get(e["action_number"], ""))
            rows.append((g.game_id, int(g.season), e["team"], e["pid"] or 0, e.get("assist") or 0, bool(named),
                         int(e["val"]), shot_kind(e["action"], e["val"]),
                         game.team_of.get(e["assist"]) if e.get("assist") else None))
        unmatched.update({k: v for k, v in game.unmatched.items()})
        if i % 1500 == 0:
            print(f"  {i} of {len(games)} games, {len(rows):,} makes")
    df = pd.DataFrame(rows, columns=["game_id", "season", "team", "scorer", "passer", "assisted", "val", "kind",
                                     "passer_team"])
    return df, unmatched


def main():
    season = SM.parse_season()
    season_and = "" if season is None else f" AND season = {int(season)}"
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    makes, unmatched = collect_makes(conn, cur, season)
    print(f"{len(makes):,} made field goals, {makes.assisted.sum():,} assisted ({time.time() - t0:.0f}s)")

    # Data errors: self-assists and a passer from the other team.
    bad = (makes.passer > 0) & ((makes.passer == makes.scorer) |
                                (makes.passer_team.notna() & (makes.passer_team != makes.team)))
    print(f"left out as data errors: {int(bad.sum())} makes "
          f"({int(((makes.passer == makes.scorer) & (makes.passer > 0)).sum())} self-assists)")
    lines_check = makes[makes.passer > 0].groupby(["season", "passer"]).size()  # the lines count these too
    m = makes[~bad].copy()
    m["pts"] = m["val"]
    unknown_passer = m.assisted & (m.passer == 0)
    print(f"assisted makes with an unidentified passer: {int(unknown_passer.sum()):,} "
          f"({unknown_passer.sum() / m.assisted.sum():.2%} of assists)")

    # ── assist_pairs ────────────────────────────────────────────────
    a = m[m.passer > 0].copy()
    for k in KINDS:
        a[k] = (a.kind == k).astype(int)
    a = a.assign(ast2=(a.val == 2).astype(int), ast3=(a.val == 3).astype(int))
    pairs = (a.groupby(["season", "team", "passer", "scorer"])
             .agg(ast=("val", "size"), games=("game_id", "nunique"), ast2=("ast2", "sum"), ast3=("ast3", "sum"),
                  pts=("pts", "sum"), rim=("rim", "sum"), floater=("floater", "sum"), jumper=("jumper", "sum"))
             .reset_index())
    print(f"assist_pairs: {len(pairs):,} rows")

    # ── player_assisted_share ───────────────────────────────────────
    lines = pd.read_sql_query(
        f"""SELECT season, team_abbreviation AS team, player_id, SUM(seconds) / 60.0 AS minutes,
                  COUNT(*) FILTER (WHERE seconds > 0) AS games, SUM(ast) AS lines_ast
           FROM player_game_lines WHERE team_abbreviation IS NOT NULL AND team_abbreviation <> 'NaN'
             AND game_id IN ({REGULAR_SQL}){season_and} GROUP BY 1, 2, 3""", conn)
    own = m[m.scorer > 0].copy()
    agg = {"fgm": ("val", "size")}
    for v in (2, 3):
        own[f"fgm{v}"] = (own.val == v).astype(int)
        own[f"ast_fgm{v}"] = ((own.val == v) & own.assisted).astype(int)
        agg[f"fgm{v}"] = (f"fgm{v}", "sum")
        agg[f"ast_fgm{v}"] = (f"ast_fgm{v}", "sum")
    for k in KINDS[:3]:
        own[f"fgm_{k}"] = (own.kind == k).astype(int)
        own[f"ast_{k}"] = ((own.kind == k) & own.assisted).astype(int)
        agg[f"fgm_{k}"] = (f"fgm_{k}", "sum")
        agg[f"ast_{k}"] = (f"ast_{k}", "sum")
    own["ast_unknown_passer"] = (own.assisted & (own.passer == 0)).astype(int)
    agg["ast_unknown_passer"] = ("ast_unknown_passer", "sum")
    scored = own.groupby(["season", "team", "scorer"]).agg(**agg).reset_index().rename(columns={"scorer": "player_id"})
    gave = (a.groupby(["season", "team", "passer"]).agg(ast=("val", "size"), ast_pts=("pts", "sum"),
                                                         ast3_given=("ast3", "sum"))
            .reset_index().rename(columns={"passer": "player_id"}))
    share = lines.merge(scored, on=["season", "team", "player_id"], how="outer").merge(
        gave, on=["season", "team", "player_id"], how="outer")
    count_cols = [c for c in share.columns if c not in ("season", "team", "player_id", "minutes", "lines_ast")]
    share[count_cols] = share[count_cols].fillna(0).astype(int)
    share["minutes"] = share["minutes"].fillna(0.0).round(1)
    no_line = share[share.minutes == 0]
    if len(no_line):
        print(f"  {len(no_line)} player-season-team rows with makes/assists but no minutes in player_game_lines")
    print(f"player_assisted_share: {len(share):,} rows")

    # ── checks ──────────────────────────────────────────────────────
    lines_ps = lines.groupby(["season", "player_id"])["lines_ast"].sum()
    mine_ps = lines_check.rename("mine")
    cmp = pd.concat([lines_ps.rename("lines"), mine_ps], axis=1).fillna(0)
    diff = cmp[cmp.lines != cmp.mine]
    print(f"assists vs player_game_lines (same parser): {len(cmp):,} player-seasons, {len(diff)} differ, "
          f"total {int(cmp.lines.sum()):,} vs {int(cmp.mine.sum()):,}")
    if len(diff):
        print(diff.head(10))

    cur.execute(f"""
        WITH l AS (SELECT player_id, season, SUM(ast) ast FROM player_game_lines
                   WHERE game_id IN ({REGULAR_SQL}){season_and} GROUP BY 1, 2)
        SELECT l.season, SUM(l.ast) / SUM(s.ast * s.gp) FROM l JOIN player_season_stats s USING (player_id, season)
        WHERE s.gp >= 20 GROUP BY 1 ORDER BY 1""")
    nba_ratio = {int(s): float(r) for s, r in cur.fetchall()}

    seasons = []
    for season, x in m.groupby("season"):
        row = {"season": int(season), "fgm": len(x), "assisted": int(x.assisted.sum()),
               "fgm2": int((x.val == 2).sum()), "ast_fgm2": int(((x.val == 2) & x.assisted).sum()),
               "fgm3": int((x.val == 3).sum()), "ast_fgm3": int(((x.val == 3) & x.assisted).sum()),
               "unknown_passer": int((x.assisted & (x.passer == 0)).sum()),
               "unknown_scorer": int((x.scorer == 0).sum()),
               "data_errors": int(bad[makes.season == season].sum()),
               "pairs": int((pairs.season == season).sum()),
               "lines_players": int((cmp.loc[season].shape[0]) if season in cmp.index.get_level_values(0) else 0),
               "lines_mismatch": int(diff.loc[season].shape[0]) if season in diff.index.get_level_values(0) else 0,
               "ast_vs_nba": round(nba_ratio.get(int(season), float("nan")), 4)}
        for k in KINDS[:3]:
            row[f"fgm_{k}"] = int((x.kind == k).sum())
            row[f"ast_{k}"] = int(((x.kind == k) & x.assisted).sum())
        row["games"] = int(x.game_id.nunique())
        seasons.append(row)
    seasons = pd.DataFrame(seasons)
    print(seasons[["season", "games", "fgm", "assisted", "ast_fgm2", "fgm2", "ast_fgm3", "fgm3", "unknown_passer",
                   "lines_mismatch", "ast_vs_nba"]].to_string(index=False))
    for r in seasons.itertuples():
        print(f"  {r.season}: {r.assisted / r.fgm:.1%} of makes assisted; 2s {r.ast_fgm2 / r.fgm2:.1%}, "
              f"3s {r.ast_fgm3 / r.fgm3:.1%}; rim {r.ast_rim / r.fgm_rim:.1%}, floaters {r.ast_floater / r.fgm_floater:.1%}, "
              f"2pt jumpers {r.ast_jumper / r.fgm_jumper:.1%}")
    print("most common unmatched names:", unmatched.most_common(8))

    # ── write ───────────────────────────────────────────────────────
    if season is not None:
        SM.require_tables(cur, ["assist_pairs", "player_assisted_share", "assist_seasons"], season)
        n = sum(SM.delete_season(cur, t, season) for t in ("assist_pairs", "player_assisted_share", "assist_seasons"))
        print(f"--season {season}: {n:,} stored rows of the season deleted")
    else:
        cur.execute("DROP TABLE IF EXISTS assist_pairs;")
        cur.execute("""CREATE TABLE assist_pairs (
        season INTEGER NOT NULL, team_abbreviation TEXT NOT NULL, passer_id BIGINT NOT NULL, scorer_id BIGINT NOT NULL,
        ast INTEGER, games INTEGER, ast2 INTEGER, ast3 INTEGER, pts INTEGER, rim INTEGER, floater INTEGER,
        jumper INTEGER, PRIMARY KEY (season, team_abbreviation, passer_id, scorer_id));""")
    psycopg2.extras.execute_values(
        cur, "INSERT INTO assist_pairs VALUES %s",
        [tuple(int(v) if not isinstance(v, str) else v for v in r) for r in
         pairs[["season", "team", "passer", "scorer", "ast", "games", "ast2", "ast3", "pts", "rim", "floater",
                "jumper"]].itertuples(index=False)], page_size=5000)
    if season is None:
        cur.execute("CREATE INDEX ON assist_pairs (passer_id);")
        cur.execute("CREATE INDEX ON assist_pairs (scorer_id);")

    cols = ["season", "team", "player_id", "minutes", "games", "fgm", "fgm2", "ast_fgm2", "fgm3", "ast_fgm3",
            "fgm_rim", "ast_rim", "fgm_floater", "ast_floater", "fgm_jumper", "ast_jumper", "ast_unknown_passer",
            "ast", "ast_pts", "ast3_given"]
    if season is None:
        cur.execute("DROP TABLE IF EXISTS player_assisted_share;")
        cur.execute(f"""CREATE TABLE player_assisted_share (
        season INTEGER NOT NULL, team_abbreviation TEXT NOT NULL, player_id BIGINT NOT NULL, minutes DOUBLE PRECISION,
        {', '.join(f'{c} INTEGER' for c in cols[4:])},
        PRIMARY KEY (season, team_abbreviation, player_id));""")
    psycopg2.extras.execute_values(
        cur, "INSERT INTO player_assisted_share VALUES %s",
        [(int(r[0]), r[1], int(r[2]), float(r[3])) + tuple(int(v) for v in r[4:])
         for r in share[cols].itertuples(index=False)], page_size=5000)
    if season is None:
        cur.execute("CREATE INDEX ON player_assisted_share (player_id);")

    scols = list(seasons.columns)
    if season is None:
        cur.execute("DROP TABLE IF EXISTS assist_seasons;")
        cur.execute(f"""CREATE TABLE assist_seasons (season INTEGER PRIMARY KEY,
        {', '.join(f'{c} DOUBLE PRECISION' if c == 'ast_vs_nba' else f'{c} INTEGER' for c in scols if c != 'season')});""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO assist_seasons ({', '.join(scols)}) VALUES %s",
        # a season with no NBA.com assists to compare with (a live season before its season rows load) stores NULL, not NaN
        [tuple((None if v != v else float(v)) if c == "ast_vs_nba" else int(v) for c, v in zip(scols, r))
         for r in seasons.itertuples(index=False)])
    conn.commit()

    for t in ("assist_pairs", "player_assisted_share", "assist_seasons"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")
    conn.close()
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
