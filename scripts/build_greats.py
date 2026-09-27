"""
build_greats.py
================
Greats of the Game: the NBA's 75th Anniversary Team (nba75_team) plus
today's stars, each with real career numbers, data-derived facts and
confirmed trivia. Writes the `greats` table read by GET /greats.

Who's in:
  - all 76 members of the NBA 75th Anniversary Team;
  - today's stars, by a fixed rule: played in the latest season, not on
    the 75th team, MIN_ALL_NBA+ All-NBA selections with at least one in the
    last RECENT_SEASONS All-NBA seasons on file.

Numbers (Basketball-Reference, local Kaggle export; one row per player-
season, a traded player's combined "2TM"/"3TM" row): NBA/BAA regular-season
totals only (ABA seasons are not counted), Win Shares, best season by Win
Shares, MVPs, All-NBA (and first team), All-Defense and NBA All-Star
selections, Naismith Hall of Fame flag. No championship data exists in the
project, so rings are never shown.

Facts are generated from that data, never written by hand: all-time rank in
career points/rebounds/assists (top 25), times leading the league in
season TOTALS (per-game titles depended on era-specific qualifying rules),
top-3 MVP finishes, best season, draft pick (pick numbers omitted before
1966: territorial-pick era, the export's numbering can't be trusted), college
and age at debut.

Trivia comes in two kinds, both labelled on the page:
  - "data": league-wide records the data proves (all-time career leaders,
    single-season records, season-long triple-doubles, unanimous MVP, MVP
    and DPOY in one season, most MVPs, lowest draft pick to win MVP, a whole
    NBA career with one team code, ROY and MVP in one season);
  - "source": well-known facts not in the data, each checked against the
    linked Wikipedia article on 2026-09-27 (SOURCED below). Checking caught
    one stale claim: Kobe Bryant's 81 is no longer the second-highest game
    (Bam Adebayo scored 83 in 2026), so only the game itself is stated.

Photos: NBA CDN headshots. The CDN serves an identical blank silhouette for
players it has no photo of; has_photo is false when the 260x190 image
matches that file's hash (Jason Kidd, Lenny Wilkens, Patrick Ewing as of
2026-09-27), so the page shows an initials tile instead.

Usage:
    cd scripts && python3 build_greats.py
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import psycopg2
import psycopg2.extras
import requests

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
MIN_ALL_NBA = 3
RECENT_SEASONS = 3
FIRST_NUMBERED_DRAFT = 1966
PLACEHOLDER_MD5 = "7475ba96"  # NBA CDN's blank silhouette at 260x190 (prefix)
PHOTO_URL = "https://cdn.nba.com/headshots/nba/latest/260x190/{}.png"
NBA_LEAGUES = ["NBA", "BAA"]

W = "https://en.wikipedia.org/wiki/"
SOURCED = {
    "Wilt Chamberlain": [("Scored 100 points in one game: Philadelphia 169, New York 147, on March 2, 1962 in Hershey, Pennsylvania, the NBA single-game record.", W + "Wilt_Chamberlain%27s_100-point_game")],
    "Kobe Bryant": [("Scored 81 points against the Toronto Raptors on January 22, 2006.", W + "Kobe_Bryant%27s_81-point_game")],
    "Bill Russell": [("Won 11 NBA championships in his 13 seasons with Boston.", W + "Bill_Russell"),
                     ("Became the first Black head coach in NBA history.", W + "Bill_Russell")],
    "Magic Johnson": [("Started at center as a rookie in Game 6 of the 1980 Finals and scored 42 points; he remains the only rookie to win Finals MVP.", W + "Magic_Johnson")],
    "Tim Duncan": [("Grew up wanting to be an Olympic swimmer in the U.S. Virgin Islands; after Hurricane Hugo destroyed the island's only Olympic-size pool in 1989, he turned to basketball.", W + "Tim_Duncan")],
    "Dirk Nowitzki": [("The first European player to be named NBA MVP (2006-07).", W + "Dirk_Nowitzki")],
    "Michael Jordan": [("Won six championships with Chicago and was Finals MVP a record six times.", W + "Michael_Jordan"),
                       ("Was deemed too short (5 ft 11 in) for his high school's varsity team as a sophomore.", W + "Michael_Jordan")],
    "Hakeem Olajuwon": [("Born in Lagos, Nigeria, he played soccer as a goalkeeper and didn't play basketball until he was 15.", W + "Hakeem_Olajuwon")],
    "Stephen Curry": [("Credited with revolutionizing the game by popularizing the three-point shot at every level of basketball.", W + "Stephen_Curry"),
                      ("His father, Dell Curry, also played in the NBA.", W + "Stephen_Curry")],
    "Giannis Antetokounmpo": [("Born and raised in Athens to Nigerian parents; fans who couldn't pronounce his surname nicknamed him the 'Greek Freak'.", W + "Giannis_Antetokounmpo")],
    "Pete Maravich": [("The all-time top scorer in NCAA Division I men's basketball: 3,667 points at 44.2 a game for LSU.", W + "Pete_Maravich")],
    "Kareem Abdul-Jabbar": [("Born Ferdinand Lewis Alcindor Jr.; his trademark was the skyhook.", W + "Kareem_Abdul-Jabbar"),
                            ("Broke the NBA career scoring record in 1984 and held it until 2023.", W + "Kareem_Abdul-Jabbar")],
    "Nikola Jokić": [("On draft night his pick was shown on a ticker during a Taco Bell ad on ESPN's broadcast.", W + "Nikola_Joki%C4%87"),
                     ("Loved harness racing as a child and competed as an amateur.", W + "Nikola_Joki%C4%87")],
    "Luka Dončić": [("Debuted for Real Madrid at 16 and became the youngest EuroLeague MVP before joining the NBA.", W + "Luka_Don%C4%8Di%C4%87")],
    "Shai Gilgeous-Alexander": [("Born in Toronto and raised in Hamilton, Ontario; the second Canadian MVP after Steve Nash.", W + "Shai_Gilgeous-Alexander"),
                                ("Led Oklahoma City to its first championship and was named Finals MVP (2025).", W + "Shai_Gilgeous-Alexander")],
    "Oscar Robertson": [("The first player to average a triple-double for a season (1961-62: 30.8 points, 12.5 rebounds, 11.4 assists).", W + "Oscar_Robertson"),
                        ("His lawsuit against the NBA delayed the ABA merger and reformed the draft and free agency.", W + "Oscar_Robertson")],
    "Shaquille O'Neal": [("Named Finals MVP in all three of the Lakers' title runs, 2000 to 2002.", W + "Shaquille_O%27Neal")],
}

TEAM_NAMES = {
    "ATL": "Atlanta", "BAL": "Baltimore", "BLB": "Baltimore", "BOS": "Boston", "BRK": "Brooklyn", "BUF": "Buffalo",
    "CHA": "Charlotte", "CHH": "Charlotte", "CHI": "Chicago", "CIN": "Cincinnati", "CLE": "Cleveland", "DAL": "Dallas",
    "DEN": "Denver", "DET": "Detroit", "FTW": "Fort Wayne", "GSW": "Golden State", "HOU": "Houston", "IND": "Indiana",
    "KCK": "Kansas City", "KCO": "Kansas City", "LAC": "the Clippers", "LAL": "the Lakers", "MEM": "Memphis",
    "MIA": "Miami", "MIL": "Milwaukee", "MIN": "Minnesota", "MNL": "the Minneapolis Lakers", "NJN": "New Jersey",
    "NOH": "New Orleans", "NOJ": "New Orleans", "NYK": "New York", "OKC": "Oklahoma City", "ORL": "Orlando",
    "PHI": "Philadelphia", "PHO": "Phoenix", "PHW": "Philadelphia", "POR": "Portland", "ROC": "Rochester",
    "SAC": "Sacramento", "SAS": "San Antonio", "SDC": "San Diego", "SDR": "San Diego", "SEA": "Seattle",
    "SFW": "San Francisco", "STL": "St. Louis", "SYR": "Syracuse", "TOR": "Toronto", "TRI": "Tri-Cities",
    "UTA": "Utah", "VAN": "Vancouver", "WAS": "Washington", "WSB": "Washington", "CHZ": "Chicago", "CAP": "Capital",
}


def lab(season):
    return f"{season - 1}-{str(season)[-2:]}"


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def one_row(df):
    """One row per player-season: the combined 2TM/3TM row for a traded player."""
    df = df[df.lg.isin(NBA_LEAGUES)].copy()
    df["_combined"] = df.team.astype(str).str.match(r"^\dTM$")
    return df.sort_values("_combined", ascending=False).drop_duplicates(["player_id", "season"])


def load():
    k = lambda f: pd.read_csv(os.path.join(KAGGLE, f))
    raw_totals = k("Player Totals.csv")
    return {
        "tot": one_row(raw_totals),
        "per": one_row(k("Player Per Game.csv")),
        "adv": one_row(k("Advanced.csv")),
        "team_rows": raw_totals[raw_totals.lg.isin(NBA_LEAGUES) & ~raw_totals.team.astype(str).str.match(r"^\dTM$")],
        "teams": k("End of Season Teams.csv").query("lg == 'NBA'"),
        "awards": k("Player Award Shares.csv"),
        "allstar": k("All-Star Selections.csv").query("lg == 'NBA'"),
        "career": k("Player Career Info.csv").set_index("player_id"),
        "draft": k("Draft Pick History.csv").query("lg in @NBA_LEAGUES"),
    }


def pick_players(cur, d):
    cur.execute("SELECT nba_player_id, bbref_id FROM player_id_map WHERE nba_player_id IS NOT NULL;")
    nba_to_bref = dict(cur.fetchall())
    cur.execute("SELECT player_id FROM nba75_team;")
    team75 = [r[0] for r in cur.fetchall()]
    missing = [p for p in team75 if p not in nba_to_bref]
    assert not missing, f"75th-team ids with no Basketball-Reference link: {missing}"

    cur.execute("SELECT max(season) FROM player_season_stats;")
    latest = cur.fetchone()[0]
    cur.execute("SELECT player_id FROM player_season_stats WHERE season = %s;", (latest,))
    active = {r[0] for r in cur.fetchall()}
    all_nba = d["teams"][d["teams"].type == "All-NBA"]
    last = all_nba.season.max()
    counts = all_nba.groupby("player_id").agg(n=("season", "size"), recent=("season", lambda s: (s > last - RECENT_SEASONS).any()))
    stars = []
    for pid, bref in nba_to_bref.items():
        if pid in active and pid not in team75 and bref in counts.index:
            row = counts.loc[bref]
            if row.n >= MIN_ALL_NBA and row.recent:
                stars.append(pid)
    rule = (f"Played in {lab(latest)}, not on the 75th Anniversary Team, {MIN_ALL_NBA}+ All-NBA selections with at "
            f"least one in the last {RECENT_SEASONS} All-NBA seasons ({lab(last - RECENT_SEASONS + 1)} to {lab(last)}).")
    return [(p, nba_to_bref[p], "75") for p in team75] + [(p, nba_to_bref[p], "now") for p in stars], rule


def league_trivia(d):
    """bref_id -> [trivia text]; every line is a league-wide record the data proves."""
    tot, per, aw, draft = d["tot"], d["per"], d["awards"], d["draft"]
    out = {}
    add = lambda b, t: out.setdefault(b, []).append(t)
    latest = tot.season.max()

    for col, word, since in [("pts", "points", 1947), ("trb", "rebounds", 1951), ("ast", "assists", 1947),
                             ("stl", "steals", 1974), ("blk", "blocks", 1974), ("x3p", "three-pointers made", 1980),
                             ("g", "games played", 1947)]:
        c = tot[tot.season >= since].groupby("player_id")[col].sum().sort_values(ascending=False)
        add(c.index[0], f"The NBA's all-time leader in {word}: {int(c.iloc[0]):,}"
                        + (f" (tracked since {lab(since)})" if since > 1947 else ""))
    for col, word, since, per_game in [("x3p", "three-pointers made in a season", 1980, False),
                                       ("pts_per_game", "points per game in a season", 1947, True),
                                       ("trb_per_game", "rebounds per game in a season", 1951, True),
                                       ("ast_per_game", "assists per game in a season", 1947, True)]:
        src = per if per_game else tot
        s = src[(src.season >= since) & (src.g >= 50)].dropna(subset=[col])
        r = s.loc[s[col].idxmax()]
        value = f"{r[col]:.1f}" if per_game else f"{int(r[col])}"
        add(r.player_id, f"Holds the single-season record for {word}: {value} in {lab(int(r.season))}")
    td = per[(per.pts_per_game >= 10) & (per.trb_per_game >= 10) & (per.ast_per_game >= 10) & (per.g >= 50)]
    for b, grp in td.groupby("player_id"):
        seasons = sorted(grp.season)
        add(b, f"Averaged a triple-double for a whole season {len(seasons)} time{'s' if len(seasons) > 1 else ''} "
               f"(first in {lab(seasons[0])})")
    mvp = aw[(aw.award == "nba mvp") & (aw.winner == True)][["season", "player_id"]]  # noqa: E712
    for r in aw[(aw.award == "nba mvp") & (aw.share >= 0.999)].itertuples():
        add(r.player_id, f"The only unanimous MVP in NBA history ({lab(r.season)}: all {int(r.first)} first-place votes)")
    dpoy = aw[(aw.award == "nba dpoy") & (aw.winner == True)][["season", "player_id"]]  # noqa: E712
    both = mvp.merge(dpoy, on=["season", "player_id"])
    for r in both.itertuples():
        add(r.player_id, f"Won MVP and Defensive Player of the Year in the same season ({lab(r.season)}), "
                         f"one of only {len(both)} players ever")
    roy = aw[(aw.award == "nba roy") & (aw.winner == True)][["season", "player_id"]]  # noqa: E712
    for r in mvp.merge(roy, on=["season", "player_id"]).itertuples():
        add(r.player_id, f"Won Rookie of the Year and MVP in the same season ({lab(r.season)})")
    counts = mvp.groupby("player_id").size()
    for b in counts[counts == counts.max()].index:
        add(b, f"Won the most MVP awards of anyone: {counts.max()}")
    picks = draft[draft.player_id.isin(set(mvp.player_id)) & (draft.season >= FIRST_NUMBERED_DRAFT)].dropna(subset=["overall_pick"])
    r = picks.loc[picks.overall_pick.idxmax()]
    add(r.player_id, f"The lowest draft pick ever to win MVP: No. {int(r.overall_pick)} in {int(r.season)}")
    team_codes = d["team_rows"].groupby("player_id").team.agg(set)
    n_seasons = tot.groupby("player_id").season.nunique()
    last_season = tot.groupby("player_id").season.max()
    for b, codes in team_codes.items():
        if len(codes) == 1 and n_seasons.get(b, 0) >= 10:
            active = last_season[b] == latest
            add(b, f"{'Has played' if active else 'Played'} all {n_seasons[b]} of his NBA seasons for one team "
                   f"({next(iter(codes))}){' so far' if active else ''}")
    return out


def player_facts(b, d, ranks, leaders, mvp_rank):
    tot, per, adv, car, draft = d["tot"], d["per"], d["adv"], d["career"], d["draft"]
    facts = []
    for col, word in [("pts", "points"), ("trb", "rebounds"), ("ast", "assists")]:
        r = ranks[col].get(b)
        if r is not None and r <= 25:
            facts.append((100 - r, f"{ordinal(r)} all-time in career {word} (NBA/BAA regular season)"))
    for col, word in [("pts", "points"), ("trb", "rebounds"), ("ast", "assists")]:
        if b in leaders[col].index:
            years = leaders[col][b]
            n = len(years)
            facts.append((90 + n, f"Led the league in total {word} {n} time{'s' if n > 1 else ''}"
                                  + (f" ({lab(years[0])})" if n == 1 else f", first in {lab(years[0])}")))
    top3 = int((mvp_rank[mvp_rank.player_id == b]["rank"] <= 3).sum())
    if top3 >= 2:
        facts.append((80 + top3, f"Finished top 3 in MVP voting {top3} times"))
    a = adv[adv.player_id == b]
    peak = None
    if len(a):
        pk = a.loc[a.ws.idxmax()]
        pg = per[(per.player_id == b) & (per.season == pk.season)].iloc[0]
        peak = (int(pk.season), round(float(pk.ws), 1))
        facts.append((60, f"Best season by Win Shares: {lab(int(pk.season))}, {pg.pts_per_game:.1f} points, "
                          f"{pg.trb_per_game:.1f} rebounds and {pg.ast_per_game:.1f} assists a game ({pk.ws:.1f} WS)"))
    dr = draft[draft.player_id == b].sort_values("season")
    if len(dr):
        dr = dr.iloc[-1]
        team = TEAM_NAMES.get(dr.tm, dr.tm)
        numbered = dr.season >= FIRST_NUMBERED_DRAFT and not pd.isna(dr.overall_pick)
        if numbered:
            text = f"Drafted No. {int(dr.overall_pick)} overall in {int(dr.season)} by {team}"
            if dr.overall_pick >= 13:
                text += ", a late pick for an all-time great"
        else:
            text = f"Drafted in {int(dr.season)} by {team}"
        facts.append((50 if not numbered or dr.overall_pick > 3 else 40, text))
    elif int(car.loc[b, "from"]) >= 1952:
        facts.append((55, "Never selected in the NBA draft"))
    college = car.loc[b, "colleges"]
    age = (pd.to_datetime(str(car.loc[b, "debut"])[:10]) - pd.to_datetime(car.loc[b, "birth_date"])).days / 365.25
    facts.append((30, f"Debuted at {age:.0f}" + (f", out of {college}" if isinstance(college, str) else ", with no US college listed")))
    facts.sort(key=lambda x: -x[0])
    return [t for _, t in facts], peak


def has_photo(pid):
    body = requests.get(PHOTO_URL.format(pid), timeout=30).content
    return not hashlib.md5(body).hexdigest().startswith(PLACEHOLDER_MD5)


def main():
    d = load()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    players, rule = pick_players(cur, d)

    tot, adv, aw, teams = d["tot"], d["adv"], d["awards"], d["teams"]
    career = tot.groupby("player_id")[["pts", "trb", "ast"]].sum()
    ranks = {c: career[c].rank(ascending=False, method="min") for c in ["pts", "trb", "ast"]}
    leaders = {}
    for col, since in [("pts", 1947), ("trb", 1951), ("ast", 1947)]:
        s = tot[tot.season >= since].dropna(subset=[col])
        top = s[s[col] == s.groupby("season")[col].transform("max")]
        leaders[col] = top.groupby("player_id").season.apply(sorted)
    mvp_rank = aw[aw.award == "nba mvp"].copy()
    mvp_rank["rank"] = mvp_rank.groupby("season").share.rank(ascending=False, method="min")
    trivia = league_trivia(d)
    all_nba = teams[teams.type == "All-NBA"]

    with ThreadPoolExecutor(12) as pool:
        photos = dict(zip([p for p, _, _ in players], pool.map(has_photo, [p for p, _, _ in players])))

    rows = []
    for pid, b, group in players:
        t = tot[tot.player_id == b]
        games = int(t.g.sum())
        reb = int(t.trb.fillna(0).sum())
        facts, peak = player_facts(b, d, ranks, leaders, mvp_rank)
        name = d["career"].loc[b, "player"]
        items = [{"text": x, "basis": "data"} for x in trivia.get(b, [])]
        sourced = SOURCED.get(name, [])
        if sourced and name == "Oscar Robertson":  # the sourced line already covers his triple-double season
            items = [x for x in items if "triple-double" not in x["text"]]
        items += [{"text": x, "basis": "source", "url": u} for x, u in sourced]
        hof = d["career"].loc[b, "hof"]
        rows.append((
            pid, b, name, group, int(t.season.min()), int(t.season.max()), int(t.season.nunique()),
            d["career"].loc[b, "pos"], None if pd.isna(d["career"].loc[b, "ht_in_in"]) else int(d["career"].loc[b, "ht_in_in"]),
            bool(hof) if not pd.isna(hof) else False,
            games, int(t.pts.sum()), reb, int(t.ast.sum()),
            round(float(t.pts.sum()) / games, 1), round(reb / games, 1), round(float(t.ast.sum()) / games, 1),
            round(float(adv[adv.player_id == b].ws.sum()), 1), peak[0] if peak else None, peak[1] if peak else None,
            int(((aw.award == "nba mvp") & (aw.winner == True) & (aw.player_id == b)).sum()),  # noqa: E712
            int((all_nba.player_id == b).sum()), int(((all_nba.player_id == b) & (all_nba.number_tm == "1st")).sum()),
            int(((teams.type == "All-Defense") & (teams.player_id == b)).sum()),
            int((d["allstar"].player_id == b).sum()),
            photos[pid], psycopg2.extras.Json(facts), psycopg2.extras.Json(items),
        ))

    by_name = {r[2]: r for r in rows}
    assert len([r for r in rows if r[3] == "75"]) == 76
    assert (by_name["Michael Jordan"][20], by_name["Michael Jordan"][24]) == (5, 14), by_name["Michael Jordan"][20:25]
    assert by_name["Kareem Abdul-Jabbar"][20] == 6
    assert by_name["Michael Jordan"][11] == 32292

    cur.execute("DROP TABLE IF EXISTS greats;")
    cur.execute("""
        CREATE TABLE greats (
            player_id INTEGER PRIMARY KEY, bref_id TEXT NOT NULL, player_name TEXT NOT NULL, grp TEXT NOT NULL,
            first_season INTEGER, last_season INTEGER, seasons INTEGER, position TEXT, height_in INTEGER,
            hall_of_fame BOOLEAN, games INTEGER, pts INTEGER, reb INTEGER, ast INTEGER,
            ppg REAL, rpg REAL, apg REAL, win_shares REAL, peak_season INTEGER, peak_ws REAL,
            mvps INTEGER, all_nba INTEGER, all_nba_first INTEGER, all_defense INTEGER, all_star INTEGER,
            has_photo BOOLEAN, facts JSONB, trivia JSONB
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO greats VALUES %s;", rows)
    cur.execute("DROP TABLE IF EXISTS greats_meta;")
    cur.execute("CREATE TABLE greats_meta (key TEXT PRIMARY KEY, value TEXT);")
    cur.execute("INSERT INTO greats_meta VALUES ('stars_rule', %s);", (rule,))
    conn.commit()
    conn.close()

    print(f"{len(rows)} greats: 76 from the 75th team + {len(rows) - 76} today's stars")
    print("Today's stars:", ", ".join(r[2] for r in rows if r[3] == "now"))
    print("No NBA photo (initials tile):", ", ".join(r[2] for r in rows if not r[25]))
    print(f"Trivia: {sum(bool(r[27].adapted) for r in rows)} players, "
          f"{sum(len(r[27].adapted) for r in rows)} items")


if __name__ == "__main__":
    main()
