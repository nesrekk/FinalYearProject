"""
build_rim_deterrence.py
========================
Rim deterrence: what opponents shot, and from where, with each defender on
the floor and off it, every regular-season game 2020-21 to 2025-26.

For every field-goal attempt in the ESPN play-by-play the shared lineup
parser (pbp_lineups.Game.walk, the same code behind player_game_lines and
lineup_stints; nothing here re-parses substitutions) gives the shooter, his
team, make or miss and two or three. Each attempt is mapped to its stint in
`lineup_stints` by its `action_number` (a stint holds an inclusive
action_from..action_to range within its game), which says which five were
defending. Only `tracked_ok` stints are used (reconciled game, five
identified players a side), the same cut as RAPM and the lineup tables.

Distance, in this order:
  coords   the NBA's own shot chart (`player_shots`, loc_x/loc_y in tenths of
           a foot, hoop at 0,0) for the same shot: ESPN and NBA shots are
           matched by order within (game, shooter, period) when both sources
           list the same number of attempts with the same make/miss sequence
           there (about 99% of attempts). The clocks can't be used to match:
           the two feeds' clocks for the same shot differ by a median 4 s and
           26 s at the 99th percentile (paper_data_audit.py).
           Distance is sqrt(x^2 + y^2), never player_shots.shot_distance
           (it is 0 on 11-17% of threes a season);
  text     ESPN's "N-foot" when the shot wasn't matched;
  rule     a layup, dunk, tip, finger roll, putback or alley-oop with neither
           counts as 0-3 ft (the rule the plan asked for). Measured on the
           matched shots, where both are known: the rule is right for ~86% of
           such shots, the rest are 4-9 ft (driving layups from 4-6 feet);
  unknown  any other two with neither (kept in the totals, in no band).
A three is a three whatever its distance: the NBA's shot type where the
shot was matched, else the parser's call (they differ on 0.8% of matched
shots: mostly missed ~26 ft threes the ESPN text doesn't call threes). Bands for twos: 0-3 ft
(the rim: distance under 4 ft, about the restricted area), 4-9, 10-15,
16 ft to the arc. How many attempts each source placed is stored per season.

On/off, per player, season and team (the player_on_off convention):
  on    the opponents' attempts in tracked stints with him in the defending
        five;
  off   the opponents' attempts in his team's other tracked stints of the
        games he played (player_game_lines); games he missed aren't counted.
  Possessions: FGA + 0.44 FTA - OREB + TOV averaged over the stint's two
  sides, exactly as player_on_off / lineup_seasons, so "per 100" means per
  100 of the same possessions the On/Off page's DRtg uses.
Rates on and off: opponents' attempts per 100 possessions by band, share of
their attempts by band, FG% by band. Headline numbers with a game-clustered
bootstrap 95% interval (his games resampled with replacement 2,000 times,
seed fixed): on-minus-off rim attempts per 100 possessions, rim FG%, and rim
points per 100 (2 x rim makes per 100: both effects together; and-ones and
fouls not included).

This is on/off, not an adjusted number: it moves with who he shares the
floor with (a second rim protector, switchable wings), who his backup is and
which opponents' units he faced. The page says so.

Tables written (dropped and rebuilt):
  rim_deterrence          one row per player-season-team: minutes, games,
                          possessions and, on and off, attempts and makes per
                          band; the headline differences with SE and 95% CI;
                          his own blocks (player_game_lines);
  rim_deterrence_seasons  per season: league attempts and makes per band and
                          per 100 possessions, how many attempts each distance
                          source placed, the no-distance rule's accuracy on
                          matched shots, the stint reconciliation check, and
                          how many qualified players' intervals clear zero.

Checks printed at the end: every tracked stint's opponent attempts equal its
stored FGA (the event -> stint mapping), band shares against player_shots'
own coordinates, and the known rim protectors (Gobert, Wembanyama, Lopez)
against each season's biggest drops.

Usage:
    cd scripts && python3 build_rim_deterrence.py     (~2 min)
Rerun after build_lineup_stints.py (new play-by-play) or after reloading
player_shots.
"""

import re
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import DIST_RE, PERIOD_SECONDS, Game, load_espn, load_season_names, match_coordinates

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

BOOTSTRAPS = 2000
SEED = 20260929
QUALIFIED_MINUTES = 1000    # the API's default floor; rows carry minutes, the floor is applied live
BANDS = ["rim", "short", "mid", "long2", "three", "unk"]
BAND_LABELS = {"rim": "0-3 ft", "short": "4-9 ft", "mid": "10-15 ft", "long2": "16 ft-arc", "three": "3PT",
               "unk": "2PT, distance unknown"}
RIM_TYPE_RE = re.compile(r"layup|dunk|tip|finger roll|putback|alley oop", re.I)
SOURCES = ["coords", "text", "rule", "unknown", "three"]


def band_of(feet):
    """Band of a two-point attempt from its distance in feet (None = unknown)."""
    if feet is None or feet != feet:
        return "unk"
    if feet < 4:
        return "rim"
    if feet < 10:
        return "short"
    if feet < 16:
        return "mid"
    return "long2"


def collect_shots(conn, cur):
    """Every field-goal attempt the lineup parser counts: game, action_number,
    shooter, team, period, clock, made, value, and the ESPN text."""
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn)
    rows = []
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        teams = (g.home_team, g.away_team)
        desc = dict(zip(ev["action_number"].astype(int), ev["description"]))
        action = dict(zip(ev["action_number"].astype(int), ev["action_type"]))

        def on_event(e, lineups, period, t, game=game, g=g, teams=teams, desc=desc, action=action):
            if e["kind"] == "sub":
                game.apply_sub(e, lineups)
            elif e["kind"] == "fg" and e["team"] in teams:
                n = e["action_number"]
                rows.append((g.game_id, int(g.season), n, e["team"], e["pid"], e["period"], e["secs"],
                             bool(e["made"]), int(e["val"]), desc.get(n, ""), action.get(n, "")))

        game.walk(g.home_team, lambda *a: None, on_event)
        if i % 1500 == 0:
            print(f"  {i} of {len(games)} games, {len(rows):,} attempts")
    shots = pd.DataFrame(rows, columns=["game_id", "season", "action_number", "team", "pid", "period", "secs",
                                        "made", "val", "description", "action_type"])
    shots["action_type"] = shots["action_type"].str.replace("\n", " ", regex=False)
    return shots


def assign_bands(shots):
    text_ft = shots.description.str.extract(DIST_RE.pattern)[0].astype(float)
    rim_type = shots.action_type.str.contains(RIM_TYPE_RE)
    has_coord = shots.coord_ft.notna()
    has_text = text_ft.notna()
    # Two or three: the NBA's shot type where the shot was matched, the parser's call otherwise. They
    # differ on ~0.8% of matched shots: mostly missed threes from ~26 ft that the ESPN text doesn't
    # call threes (the parser then defaults to two), and a few 22-23 ft twos it puts past the arc.
    three = np.where(has_coord, shots.shot_type.fillna("").str.startswith("3"), shots.val == 3)
    src = np.select([three, has_coord, has_text, rim_type], ["three", "coords", "text", "rule"], "unknown")
    feet = np.where(has_coord, shots.coord_ft, np.where(has_text, text_ft, np.where(rim_type, 0.0, np.nan)))
    shots["dist_source"] = src
    shots["feet"] = feet
    shots["band"] = np.where(three, "three", [band_of(f) for f in feet])
    # The no-distance rule measured where it can be: matched twos of a rim type with no text distance.
    probe = (~three) & has_coord & (~has_text) & rim_type
    rule_n = int(probe.sum())
    rule_right = int((probe & (shots.coord_ft < 4)).sum())
    three_check = shots[has_coord]
    nba3 = three_check.shot_type.str.startswith("3")
    three_agree = float(((three_check.val == 3) == nba3).mean())
    off = three_check[(three_check.val == 3) != nba3]
    print("parser 2/3 vs NBA shot type, disagreements by season (parser 2 & NBA 3 / parser 3 & NBA 2) and median "
          "coordinate distance:")
    for season, x in off.groupby("season"):
        a, b = x[x.val == 2], x[x.val == 3]
        print(f"  {season}: {len(a)} / {len(b)} of {int((three_check.season == season).sum())}; "
              f"median {a.coord_ft.median():.1f} ft / {b.coord_ft.median():.1f} ft; made {a.made.mean():.0%} / {b.made.mean():.0%}")
    return shots, {"rule_probe": rule_n, "rule_right": rule_right, "three_agree": three_agree,
                   "rule_probe_by_season": shots[probe].groupby("season").size().to_dict(),
                   "rule_right_by_season": shots[probe & (shots.coord_ft < 4)].groupby("season").size().to_dict()}


def map_to_stints(conn, shots):
    """Stint of each attempt (action_number within the stint's range), and the
    per-stint check that the attempts found equal the stint's stored FGA."""
    st = pd.read_sql_query(
        """SELECT stint_id, game_id, season, home_team, away_team, seconds, home_ids, away_ids, action_from, action_to,
                  home_fga, away_fga, home_poss, away_poss, tracked_ok
           FROM lineup_stints ORDER BY game_id, action_from""", conn)
    right = st[st.action_from.notna()][["game_id", "action_from", "action_to", "stint_id"]].copy()
    right["action_from"] = right.action_from.astype(int)
    left = shots.sort_values("action_number").copy()
    left["action_number"] = left.action_number.astype(int)
    right = right.sort_values("action_from")
    m = pd.merge_asof(left, right, left_on="action_number", right_on="action_from", by="game_id", direction="backward")
    inside = m.stint_id.notna() & (m.action_number <= m.action_to)
    m.loc[~inside, "stint_id"] = np.nan
    unmapped = int((~inside).sum())
    m = m[inside].copy()
    m["stint_id"] = m.stint_id.astype(int)
    m = m.merge(st[["stint_id", "home_team", "tracked_ok"]], on="stint_id")
    m["side"] = np.where(m.team == m.home_team, "home", "away")
    cnt = m.groupby(["stint_id", "side"]).size().unstack(fill_value=0).reindex(columns=["home", "away"], fill_value=0)
    chk = st.set_index("stint_id")[["home_fga", "away_fga", "tracked_ok"]].join(cnt, how="left").fillna(0)
    bad = chk[(chk.home != chk.home_fga) | (chk.away != chk.away_fga)]
    return m, st, {"unmapped": unmapped, "stints_fga_mismatch": int(len(bad)),
                   "tracked_stints_fga_mismatch": int(bad.tracked_ok.astype(bool).sum())}


def defense_sides(m, st):
    """One row per tracked stint and defending side: the five, possessions and the
    opponents' attempts/makes per band."""
    t = st[st.tracked_ok].copy()
    t["poss"] = (t.home_poss + t.away_poss) / 2
    sides = []
    for d_side, o_side in (("home", "away"), ("away", "home")):
        x = t[["stint_id", "game_id", "season", "seconds", "poss"]].copy()
        x["team"] = t[f"{d_side}_team"]
        x["ids"] = t[f"{d_side}_ids"]
        x["o_side"] = o_side
        sides.append(x)
    d = pd.concat(sides, ignore_index=True)
    sh = m[m.tracked_ok]
    agg = sh.groupby(["stint_id", "side", "band"]).agg(fga=("made", "size"), fgm=("made", "sum")).reset_index()
    wide = agg.pivot_table(index=["stint_id", "side"], columns="band", values=["fga", "fgm"], fill_value=0)
    wide.columns = [f"{b}_{k}" for k, b in wide.columns]
    wide = wide.reset_index().rename(columns={"side": "o_side"})
    d = d.merge(wide, on=["stint_id", "o_side"], how="left")
    for b in BANDS:
        for k in ("fga", "fgm"):
            c = f"{b}_{k}"
            d[c] = d[c].fillna(0).astype(int) if c in d else 0
    return d


COUNT_COLS = [f"{b}_{k}" for b in BANDS for k in ("fga", "fgm")]


def rate(n, d, scale=1.0):
    return float(scale * n / d) if d > 0 else None


def bootstrap(rng, g):
    """g: G x 6 per-game (rim_fga_on, rim_fgm_on, poss_on, rim_fga_off, rim_fgm_off, poss_off).
    Returns (se, lo, hi) for on-minus-off rim attempts/100, rim FG% and rim points/100."""
    idx = rng.integers(0, g.shape[0], size=(BOOTSTRAPS, g.shape[0]))
    s = g[idx].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = {
            "rim_fga100": 100 * (s[:, 0] / s[:, 2] - s[:, 3] / s[:, 5]),
            "rim_fg": s[:, 1] / s[:, 0] - s[:, 4] / s[:, 3],
            "rim_pts100": 200 * (s[:, 1] / s[:, 2] - s[:, 4] / s[:, 5]),
        }
    res = {}
    for k, v in out.items():
        v = v[np.isfinite(v)]
        if len(v) < BOOTSTRAPS * 0.9:
            res[k] = (None, None, None)
        else:
            lo, hi = np.percentile(v, [2.5, 97.5])
            res[k] = (float(v.std(ddof=1)), float(lo), float(hi))
    return res


def player_rows(conn, d):
    lines = pd.read_sql_query(
        """SELECT player_id, game_id, season, team_abbreviation AS team, blk FROM player_game_lines
           WHERE seconds > 0 AND season >= 2021""", conn)
    on = d.explode("ids").rename(columns={"ids": "player_id"})
    on["player_id"] = on.player_id.astype(int)
    cols = ["seconds", "poss"] + COUNT_COLS
    on_g = on.groupby(["player_id", "game_id", "team"])[cols].sum()
    team_g = d.groupby(["game_id", "team"])[cols].sum()
    pg = lines.set_index(["player_id", "game_id", "team"])[["season", "blk"]]
    pg = pg.join(on_g, how="left")
    pg = pg.reset_index()
    tg = team_g.reset_index()
    pg = pg.merge(tg, on=["game_id", "team"], how="inner", suffixes=("_on", "_tm"))
    for c in cols:
        pg[f"{c}_on"] = pg[f"{c}_on"].fillna(0)
        pg[f"{c}_off"] = pg[f"{c}_tm"] - pg[f"{c}_on"]
    rng = np.random.default_rng(SEED)
    out = []
    groups = pg.groupby(["player_id", "season", "team"], sort=True)
    for i, ((pid, season, team), g) in enumerate(groups):
        s = g.sum(numeric_only=True)
        if s.seconds_on <= 0:
            continue
        r = {"player_id": int(pid), "season": int(season), "team_abbreviation": team,
             "games": int((g.seconds_on > 0).sum()), "blk": int(s.blk),
             "minutes_on": round(float(s.seconds_on) / 60, 1), "minutes_off": round(float(s.seconds_off) / 60, 1),
             "poss_on": round(float(s.poss_on), 1), "poss_off": round(float(s.poss_off), 1)}
        for side in ("on", "off"):
            for c in COUNT_COLS:
                r[f"{c}_{side}"] = int(s[f"{c}_{side}"])
            fga = sum(r[f"{b}_fga_{side}"] for b in BANDS)
            fgm = sum(r[f"{b}_fgm_{side}"] for b in BANDS)
            r[f"fga_{side}"], r[f"fgm_{side}"] = fga, fgm
            poss = r[f"poss_{side}"]
            r[f"rim_fga100_{side}"] = rate(r[f"rim_fga_{side}"], poss, 100)
            r[f"rim_fg_{side}"] = rate(r[f"rim_fgm_{side}"], r[f"rim_fga_{side}"])
            r[f"rim_pts100_{side}"] = rate(2 * r[f"rim_fgm_{side}"], poss, 100)
            r[f"rim_share_{side}"] = rate(r[f"rim_fga_{side}"], fga)
            r[f"opp_fg_{side}"] = rate(fgm, fga)
        for k in ("rim_fga100", "rim_fg", "rim_pts100", "rim_share", "opp_fg"):
            a, b = r[f"{k}_on"], r[f"{k}_off"]
            r[f"{k}_diff"] = None if a is None or b is None else a - b
        per_game = g[["rim_fga_on", "rim_fgm_on", "poss_on", "rim_fga_off", "rim_fgm_off", "poss_off"]].to_numpy(float)
        per_game = per_game[(g.seconds_on > 0).to_numpy() | (g.seconds_off > 0).to_numpy()]
        boot = bootstrap(rng, per_game) if len(per_game) >= 2 and r["rim_fga100_diff"] is not None \
            and r["rim_fg_diff"] is not None else {}
        for k in ("rim_fga100", "rim_fg", "rim_pts100"):
            se, lo, hi = boot.get(k, (None, None, None))
            r[f"{k}_se"], r[f"{k}_lo"], r[f"{k}_hi"] = se, lo, hi
        out.append(r)
        if i % 1000 == 0:
            print(f"  {i} of {groups.ngroups} player-season-teams")
    return pd.DataFrame(out)


def season_rows(shots, d, players, checks, rule):
    rows = []
    tracked = shots[shots.tracked_ok]
    for season, x in d.groupby("season"):
        sh = tracked[tracked.season == season]
        allsh = shots[shots.season == season]
        poss = float(x.poss.sum())
        r = {"season": int(season), "stints": int(x.stint_id.nunique()), "games": int(x.game_id.nunique()),
             "poss": round(poss, 1), "attempts": int(len(allsh)), "tracked_attempts": int(len(sh))}
        for b in BANDS:
            fga, fgm = int(x[f"{b}_fga"].sum()), int(x[f"{b}_fgm"].sum())
            r[f"{b}_fga"], r[f"{b}_fgm"] = fga, fgm
            r[f"{b}_fga100"] = round(100 * fga / poss, 3)
            r[f"{b}_fg"] = round(fgm / fga, 4) if fga else None
        r["matched"] = int(allsh.coord_ft.notna().sum())
        for s in SOURCES:
            r[f"src_{s}"] = int((allsh.dist_source == s).sum())
        r["rule_probe"] = int(rule["rule_probe_by_season"].get(season, 0))
        r["rule_right"] = int(rule["rule_right_by_season"].get(season, 0))
        q = players[(players.season == season) & (players.minutes_on >= QUALIFIED_MINUTES)]
        r["qualified"] = int(len(q))
        for k in ("rim_fga100", "rim_fg", "rim_pts100"):
            qq = q.dropna(subset=[f"{k}_lo"])
            r[f"{k}_ci_excl"] = int(((qq[f"{k}_lo"] > 0) | (qq[f"{k}_hi"] < 0)).sum())
        r["qualified_minutes"] = QUALIFIED_MINUTES
        r["bootstraps"] = BOOTSTRAPS
        r["stints_fga_mismatch"] = checks["stints_fga_mismatch"]
        r["three_agree"] = round(rule["three_agree"], 5)
        rows.append(r)
    return pd.DataFrame(rows)


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def write(cur, name, df, pk, extra_index=()):
    cur.execute(f"DROP TABLE IF EXISTS {name};")
    types = []
    for c in df.columns:
        dt = df[c].dtype
        if c in ("team_abbreviation",):
            t = "TEXT"
        elif pd.api.types.is_bool_dtype(dt):
            t = "BOOLEAN"
        elif pd.api.types.is_integer_dtype(dt):
            t = "BIGINT" if c == "player_id" else "INTEGER"
        else:
            t = "DOUBLE PRECISION"
        types.append(f"{c} {t}")
    cur.execute(f"CREATE TABLE {name} ({', '.join(types)}, PRIMARY KEY ({', '.join(pk)}));")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {name} ({', '.join(df.columns)}) VALUES %s",
        [tuple(clean(v) for v in r) for r in df.itertuples(index=False)], page_size=2000)
    for idx in extra_index:
        cur.execute(f"CREATE INDEX ON {name} ({idx});")


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    shots = collect_shots(conn, cur)
    print(f"{len(shots):,} field-goal attempts from the parser ({time.time() - t0:.0f}s)")
    shots = match_coordinates(conn, shots)
    shots, rule = assign_bands(shots)
    print(f"distance sources: {shots.dist_source.value_counts().to_dict()}")
    print(f"no-distance rule on matched shots: {rule['rule_right']:,} of {rule['rule_probe']:,} really 0-3 ft "
          f"({rule['rule_right'] / rule['rule_probe']:.1%}); parser's 2/3 vs NBA shot type: {rule['three_agree']:.4%} agree")

    m, st, checks = map_to_stints(conn, shots)
    shots = shots.merge(m[["game_id", "action_number", "tracked_ok"]], on=["game_id", "action_number"], how="left")
    shots["tracked_ok"] = shots.tracked_ok.fillna(False).astype(bool)
    print(f"event -> stint: {checks['unmapped']} attempts outside every stint's range; stints whose attempts differ "
          f"from their stored FGA: {checks['stints_fga_mismatch']} ({checks['tracked_stints_fga_mismatch']} tracked)")

    d = defense_sides(m, st)
    players = player_rows(conn, d)
    for c in players.columns:
        if players[c].dtype == float:
            players[c] = players[c].round(4)
    seasons = season_rows(shots, d, players, checks, rule)
    print(f"{len(players):,} player-season-team rows ({time.time() - t0:.0f}s)")

    write(cur, "rim_deterrence", players, ["player_id", "season", "team_abbreviation"],
          ["season, team_abbreviation", "player_id"])
    write(cur, "rim_deterrence_seasons", seasons, ["season"])
    conn.commit()

    # ── Checks ──
    print("\nPer season:")
    show = ["season", "games", "attempts", "matched", "tracked_attempts", "rim_fga100", "rim_fg", "short_fg", "three_fg",
            "src_coords", "src_text", "src_rule", "src_unknown", "rule_probe", "rule_right", "qualified",
            "rim_fga100_ci_excl", "rim_fg_ci_excl", "rim_pts100_ci_excl"]
    print(seasons[show].to_string(index=False))

    cmp = pd.read_sql_query(
        """SELECT season, COUNT(*) FILTER (WHERE shot_type LIKE '3%%') AS three,
                  COUNT(*) FILTER (WHERE shot_type NOT LIKE '3%%' AND SQRT(loc_x^2 + loc_y^2) < 40) AS rim,
                  COUNT(*) AS n
           FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21' GROUP BY 1 ORDER BY 1""", conn)
    print("\nRim share of all attempts: here (tracked stints) vs player_shots coordinates (every shot)")
    for r, s in zip(cmp.itertuples(), seasons.itertuples()):
        here = s.rim_fga / sum(getattr(s, f"{b}_fga") for b in BANDS)
        print(f"  {r.season}: here {here:.1%}, player_shots {r.rim / r.n:.1%}; threes here "
              f"{s.three_fga / sum(getattr(s, f'{b}_fga') for b in BANDS):.1%}, player_shots {r.three / r.n:.1%}")

    names = pd.read_sql_query("SELECT player_id, player_name FROM player_bio", conn)
    p = players.merge(names, on="player_id", how="left")
    q = p[p.minutes_on >= 1000]
    for metric, label in (("rim_fga100_diff", "rim attempts per 100"), ("rim_fg_diff", "rim FG%"),
                          ("rim_pts100_diff", "rim points per 100")):
        print(f"\nBiggest drops in opponents' {label}, 1,000+ minutes:")
        for season, x in q.groupby("season"):
            top = x.nsmallest(6, metric)
            fmt = (lambda v: f"{v * 100:+.1f}") if metric == "rim_fg_diff" else (lambda v: f"{v:+.1f}")
            print(f"  {season}: " + "; ".join(f"{r.player_name} {fmt(getattr(r, metric))}" for r in top.itertuples()))
    print("\nKnown rim protectors (rank among 1,000+ minute players by rim points per 100 on-off, of N):")
    for pid in (203497, 1641705, 201572):
        for r in p[(p.player_id == pid)].itertuples():
            x = q[q.season == r.season]
            rk = lambda c: int((x[c] < getattr(r, c)).sum()) + 1 if r.minutes_on >= 1000 else None
            print(f"  {r.player_name} {r.season} {r.team_abbreviation} {r.minutes_on:.0f} min: rim att/100 "
                  f"{r.rim_fga100_on:.1f} on vs {r.rim_fga100_off:.1f} off ({r.rim_fga100_diff:+.1f}, "
                  f"{r.rim_fga100_lo:+.1f} to {r.rim_fga100_hi:+.1f}) rank {rk('rim_fga100_diff')}; rim FG% "
                  f"{r.rim_fg_on:.1%} vs {r.rim_fg_off:.1%} rank {rk('rim_fg_diff')}; rim pts/100 {r.rim_pts100_diff:+.1f} "
                  f"rank {rk('rim_pts100_diff')} of {len(x)}")
    for t in ("rim_deterrence", "rim_deterrence_seasons"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        print(f"  {t}: {cur.fetchone()}")
    print(f"done ({time.time() - t0:.0f}s)")
    conn.close()


if __name__ == "__main__":
    main()
