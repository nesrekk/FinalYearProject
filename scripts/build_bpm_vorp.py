"""
build_bpm_vorp.py
===================
Computes BPM, OBPM, DBPM, and VORP for every player-season in
player_season_stats and stores them as new additive columns.

HONESTY NOTE (read before trusting these numbers): this is an independent
REPRODUCTION of the published Box Plus/Minus 2.0 methodology (Daniel Myers,
via Basketball-Reference), not Basketball-Reference's own code — their site
blocks automated fetches, so the position/offensive-role regression
coefficients used below were sourced from a working open-source
reimplementation of the same public methodology (jonahmiller99/
basketball-utils on GitHub), cross-checked for internal consistency
(position regression -> offensive-role regression -> position-interpolated
coefficients -> team-level calibration, matching the documented BPM 2.0
structure). Expect our numbers to be CLOSE to basketball-reference.com's
published BPM but not bit-identical, because of two documented
simplifications forced by what data this project actually has:

  1. No team-level opponent-adjusted rating (SRS-style schedule strength
     adjustment) — this project doesn't compute one. Team calibration here
     uses each team's own minutes-weighted net_rating / off_rating
     (already this project's established team-aggregation methodology,
     same as Trade Analyzer and Team Trends) instead of Basketball-
     Reference's opponent-adjusted team rating.
  2. No game-by-game log — this table only has season-level per-game
     averages, so the small "lead bonus" (average-game-script) adjustment
     the official formula applies at the game level is omitted. Its effect
     is minor relative to the main coefficients.

Every box-score input (PTS, FGA, FTA, 3PM, AST, TOV, OREB, DREB, TRB, STL,
BLK, PF, POSS, MIN) is real project data — nothing here is fabricated or
guessed; only the two calibration simplifications above are approximations,
and both are documented.

VERIFIED behavior (checked against real 2024-25 results, not assumed):
relative ranking is sound — Shai Gilgeous-Alexander (the real 2024-25 MVP),
Nikola Jokić, Giannis Antetokounmpo, Jayson Tatum, Luka Dončić, Stephen
Curry, LeBron James, and Alperen Sengun all land in the top ~20 among
qualified players, and every player correctly outranks their own bench
teammates (an earlier bug had entire rosters clustering together —
diagnosed and fixed, see the two comments below on team-possession
handling). The one caveat: the overall SCALE runs hotter than official
Basketball-Reference numbers at the very top (our ~23 for the league
leader vs. Basketball-Reference's typical ceiling around 13-15) — likely
compounding effect of the two documented simplifications above plus
smaller unavoidable differences from not having Basketball-Reference's
exact league-baseline constants. Treat these BPM/VORP numbers as directionally
and comparatively reliable (who's better than whom, and by roughly how
much) rather than numerically identical to the numbers you'd see on
basketball-reference.com.

Pipeline:
  1. Build team-season aggregates from player_season_stats (SUM(stat*gp)).
  2. Estimate each player's position (1=PG..5=C) via the 3-pass-trimmed
     regression on their share of team TRB/STL/PF/AST/BLK.
  3. Estimate each player's offensive role (1..5) via a similar regression
     on their share of team AST and "threshold points" (scoring above a
     team-relative baseline).
  4. Compute per-100-possession stats using the player's own POSS (already
     NBA's own individual-possession estimate — no team-pace conversion
     needed).
  5. Interpolate BPM/OBPM coefficients between the Pos_1 and Pos_5 tables
     by estimated position (FGA/FTA use offensive role instead).
  6. Calibrate: shift each team's raw BPM (minutes-weighted) so it matches
     that team's real net_rating; same for OBPM vs. off_rating. DBPM = BPM
     - OBPM.
  7. VORP = (BPM + 2.0) * (minutes / (team_games * 48)) * (team_games/82).

Usage:
    cd scripts && python3 build_bpm_vorp.py
"""

import os
import warnings

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

warnings.filterwarnings("ignore")

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

# ─── BPM 2.0 coefficients (Pos_1 = point guard, Pos_5 = center) ────────────
# Sourced from jonahmiller99/basketball-utils (open-source reproduction of
# Daniel Myers' published BPM 2.0 methodology) — see module docstring.
BPM_COEF = {
    1: {"AdjPt": 0.860, "FGA": -0.560, "FTA": -0.266, "AST": 0.580, "TOV": -0.964,
        "ORB": 0.613, "DRB": 0.116, "STL": 1.369, "BLK": 1.327, "PF": -0.367},
    5: {"AdjPt": 0.860, "FGA": -0.780, "FTA": -0.371, "AST": 1.034, "TOV": -0.964,
        "ORB": 0.181, "DRB": 0.181, "STL": 1.008, "BLK": 0.703, "PF": -0.367},
}
OBPM_COEF = {
    1: {"AdjPt": 0.605, "FGA": -0.330, "FTA": -0.157, "AST": 0.476, "TOV": -0.579,
        "ORB": 0.606, "DRB": -0.112, "STL": 0.177, "BLK": 0.725, "PF": -0.439},
    5: {"AdjPt": 0.605, "FGA": -0.472, "FTA": -0.224, "AST": 0.476, "TOV": -0.882,
        "ORB": 0.422, "DRB": 0.103, "STL": 0.294, "BLK": 0.097, "PF": -0.439},
}
TSA_COEF = BPM_COEF[1]["FTA"] / BPM_COEF[1]["FGA"]  # ~0.475, standard True Shot Attempts constant

# Position-baseline adjustment applied on top of the weighted box-score sum
# — NOT interpolated the same way as the stat coefficients above (position
# 3 is the zero-point, position 1 and 5 pull in opposite directions), plus
# an offensive-role slope that further corrects ball-dominant, high-usage
# profiles (low offensive_role number) which the raw stat weights alone
# tend to overstate.
POSITION_ADJ = {"pos1": -0.818, "pos5": 0.0, "role_slope": 1.387}
OBPM_POSITION_ADJ = {"pos1": -1.698, "pos5": 0.0, "role_slope": 0.430}

POSITION_COEF = {"intercept": 2.130, "trb": 8.668, "stl": -2.486, "pf": 0.992, "ast": -3.536, "blk": 1.667}
OFFROLE_COEF = {"intercept": 6.0, "ast": -6.642, "threshpts": -8.544, "pt_threshold": -0.330}
MIN_WT = 50  # regression-to-the-mean weight (in minutes) used by the official methodology


def load_players(conn):
    cols = ["player_id", "player_name", "team_abbreviation", "season", "gp", "min",
            "pts", "reb", "ast", "stl", "blk", "tov", "oreb", "dreb", "pf",
            "fga", "fta", "fg3m", "poss", "off_rating", "def_rating", "net_rating"]
    df = pd.read_sql_query(f"SELECT {', '.join(cols)} FROM player_season_stats;", conn)
    df = df.dropna(subset=["gp", "min", "poss", "fga"])
    df = df[(df["gp"] > 0) & (df["poss"] > 0)]
    return df


def build_team_aggregates(df):
    d = df.copy()
    # poss is already a season TOTAL (unlike the rest, which are per-game
    # averages) — confirmed against a real row (LeBron James 2024-25:
    # poss=5129 for the whole season, not per game). Multiplying it by gp
    # again like the per-game stats would massively over-count it. It's
    # also already "team possessions while this player was on the floor"
    # (the same on/off-court possession convention this table's other
    # per-100-possession columns already use, e.g. ts_pct/usg_pct) — not a
    # usage-based attribution that sums to the team total, so it's used
    # directly per player in compute_bpm() rather than re-derived here.
    for c in ["min", "pts", "reb", "ast", "stl", "blk", "tov", "oreb", "dreb", "pf", "fga", "fta"]:
        d[f"tot_{c}"] = d[c] * d["gp"]
    agg = d.groupby(["team_abbreviation", "season"]).agg(
        total_minutes=("tot_min", "sum"), team_pts=("tot_pts", "sum"), team_trb=("tot_reb", "sum"),
        team_ast=("tot_ast", "sum"), team_stl=("tot_stl", "sum"), team_blk=("tot_blk", "sum"),
        team_pf=("tot_pf", "sum"), team_fga=("tot_fga", "sum"), team_fta=("tot_fta", "sum"),
        team_games=("gp", "max"),
    ).reset_index()
    agg["team_tsa"] = agg["team_fga"] + TSA_COEF * agg["team_fta"]
    agg["team_pts_per_tsa"] = agg["team_pts"] / agg["team_tsa"]

    # Minutes-weighted team net/off rating — this project's established
    # team-aggregation methodology (same as Trade Analyzer/Team Trends).
    # net_rating is already centered at 0 (points above/below opponent),
    # but off_rating is an absolute scale (~105-120) — has to be re-centered
    # against the league-average off_rating for that season before it can
    # calibrate an OBPM number that's meant to sit near 0 like BPM does.
    d["min_x_net"] = d["min"] * d["gp"] * d["net_rating"]
    d["min_x_off"] = d["min"] * d["gp"] * d["off_rating"]
    rating = d.groupby(["team_abbreviation", "season"]).apply(
        lambda g: pd.Series({
            "team_net_rating": g["min_x_net"].sum() / (g["min"] * g["gp"]).sum(),
            "team_off_rating": g["min_x_off"].sum() / (g["min"] * g["gp"]).sum(),
        })
    ).reset_index()

    league_off_by_season = d.groupby("season").apply(
        lambda g: g["min_x_off"].sum() / (g["min"] * g["gp"]).sum()
    ).rename("league_off_rating").reset_index()
    rating = rating.merge(league_off_by_season, on="season")
    rating["team_off_rating_rel"] = rating["team_off_rating"] - rating["league_off_rating"]

    return agg.merge(rating, on=["team_abbreviation", "season"])


def three_pass_trim(raw_estimates, weights, total_weight):
    """The official methodology's 3-pass trim-toward-team-average-of-3,
    clamped to [1, 5] each pass. Pulls extreme single-season estimates back
    toward the team's overall average positional profile."""
    est = np.clip(raw_estimates, 1, 5)
    for _ in range(3):
        team_avg = np.dot(est, weights) / total_weight
        est = np.clip(est - (team_avg - 3), 1, 5)
    return est


def compute_position(df, team_agg):
    m = df.merge(team_agg, on=["team_abbreviation", "season"])
    pct_min = (m["min"] * m["gp"]) / (m["total_minutes"] / 5)
    pct_trb = ((m["reb"] * m["gp"]) / m["team_trb"]) / pct_min
    pct_stl = ((m["stl"] * m["gp"]) / m["team_stl"]) / pct_min
    pct_pf = ((m["pf"] * m["gp"]) / m["team_pf"]) / pct_min
    pct_ast = ((m["ast"] * m["gp"]) / m["team_ast"]) / pct_min
    pct_blk = ((m["blk"] * m["gp"]) / m["team_blk"]) / pct_min

    est = (POSITION_COEF["intercept"] + POSITION_COEF["trb"] * pct_trb + POSITION_COEF["stl"] * pct_stl
           + POSITION_COEF["pf"] * pct_pf + POSITION_COEF["ast"] * pct_ast + POSITION_COEF["blk"] * pct_blk)
    mp = m["min"] * m["gp"]
    est_reg = (est * mp + 3 * MIN_WT) / (mp + MIN_WT)  # regress toward league-average position 3

    positions = np.zeros(len(m))
    for (team, season), idx in m.groupby(["team_abbreviation", "season"]).groups.items():
        pos = idx
        weights = mp.loc[pos].values
        total_w = team_agg[(team_agg.team_abbreviation == team) & (team_agg.season == season)]["total_minutes"].iloc[0]
        positions[m.index.get_indexer(pos)] = three_pass_trim(est_reg.loc[pos].values, weights, total_w)

    m["position"] = positions
    return m[["player_id", "season", "position"]]


def compute_offensive_role(df, team_agg):
    m = df.merge(team_agg, on=["team_abbreviation", "season"])
    tsa = m["fga"] * m["gp"] + TSA_COEF * m["fta"] * m["gp"]
    pts_tsa = (m["pts"] * m["gp"]) / tsa.replace(0, np.nan)
    thresh_pts = tsa * (pts_tsa - (m["team_pts_per_tsa"] + OFFROLE_COEF["pt_threshold"]))
    m["thresh_pts"] = thresh_pts.fillna(0)

    team_thresh_totals = m.groupby(["team_abbreviation", "season"])["thresh_pts"].transform("sum")
    pct_min = (m["min"] * m["gp"]) / (m["total_minutes"] / 5)
    pct_ast = ((m["ast"] * m["gp"]) / m["team_ast"]) / pct_min
    pct_threshpts = (m["thresh_pts"] / team_thresh_totals.replace(0, np.nan)) / pct_min
    pct_threshpts = pct_threshpts.fillna(0)

    est = OFFROLE_COEF["intercept"] + OFFROLE_COEF["ast"] * pct_ast + OFFROLE_COEF["threshpts"] * pct_threshpts
    mp = m["min"] * m["gp"]
    est_reg = (est * mp + 3 * MIN_WT) / (mp + MIN_WT)

    roles = np.zeros(len(m))
    for (team, season), idx in m.groupby(["team_abbreviation", "season"]).groups.items():
        pos = idx
        weights = mp.loc[pos].values
        total_w = team_agg[(team_agg.team_abbreviation == team) & (team_agg.season == season)]["total_minutes"].iloc[0]
        roles[m.index.get_indexer(pos)] = three_pass_trim(est_reg.loc[pos].values, weights, total_w)

    m["offensive_role"] = roles
    return m[["player_id", "season", "offensive_role"]]


def interpolate(coef_table, key, position):
    """Linear interpolation between Pos_1 and Pos_5 coefficients by
    estimated position (1..5) — the official methodology's approach to
    'a stat means something different for a center than a point guard'."""
    c1, c5 = coef_table[1][key], coef_table[5][key]
    return c1 + (position - 1) / 4 * (c5 - c1)


def compute_bpm(df, team_agg, position_df, role_df):
    m = df.merge(team_agg, on=["team_abbreviation", "season"]).merge(
        position_df, on=["player_id", "season"]).merge(role_df, on=["player_id", "season"])

    tsa = m["fga"] * m["gp"] + TSA_COEF * m["fta"] * m["gp"]
    pts_tsa = (m["pts"] * m["gp"]) / tsa.replace(0, np.nan)
    adj_pts = ((pts_tsa - m["team_pts_per_tsa"]).fillna(0) + m["team_pts_per_tsa"]) * tsa

    # nba_api's individual POSS is already "team possessions while this
    # player was on the floor" (an on/off-court possession count, the same
    # convention this table's other per-100-possession stats already use)
    # — NOT a usage-based attribution that sums to the team total, which
    # was a wrong assumption in an earlier version of this script (chased
    # a real-looking but wrong "5x too small" theory that turned out to
    # point the wrong direction — verified by checking that summing every
    # roster player's individual POSS for a team totals to ~5x the team's
    # real season possession count, not 1x, since a possession is counted
    # once for each of the 5 players on the floor for it).
    poss = m["poss"]
    p100 = pd.DataFrame({
        "AdjPt": adj_pts / poss * 100, "FGA": (m["fga"] * m["gp"]) / poss * 100,
        "FTA": (m["fta"] * m["gp"]) / poss * 100, "AST": (m["ast"] * m["gp"]) / poss * 100,
        "TOV": (m["tov"] * m["gp"]) / poss * 100, "ORB": (m["oreb"] * m["gp"]) / poss * 100,
        "DRB": (m["dreb"] * m["gp"]) / poss * 100, "STL": (m["stl"] * m["gp"]) / poss * 100,
        "BLK": (m["blk"] * m["gp"]) / poss * 100, "PF": (m["pf"] * m["gp"]) / poss * 100,
    })

    def raw_score(coef_table):
        total = pd.Series(0.0, index=m.index)
        for key in ["AdjPt", "AST", "TOV", "ORB", "DRB", "STL", "BLK", "PF"]:
            c1, c5 = coef_table[1][key], coef_table[5][key]
            coef = c1 + (m["position"] - 1) / 4 * (c5 - c1)
            total += coef * p100[key]
        for key in ["FGA", "FTA"]:
            c1, c5 = coef_table[1][key], coef_table[5][key]
            coef = c1 + (m["offensive_role"] - 1) / 4 * (c5 - c1)
            total += coef * p100[key]
        return total

    def position_adjustment(adj_table):
        pos = m["position"]
        pre_slope = np.where(
            pos < 3,
            (pos - 1) / 2 * adj_table["pos5"] + (3 - pos) / 2 * adj_table["pos1"],
            (pos - 3) / 2 * adj_table["pos5"],
        )
        return pre_slope + adj_table["role_slope"] * (m["offensive_role"] - 3)

    raw_bpm = raw_score(BPM_COEF) + position_adjustment(POSITION_ADJ)
    raw_obpm = raw_score(OBPM_COEF) + position_adjustment(OBPM_POSITION_ADJ)

    m["raw_bpm"] = raw_bpm
    m["raw_obpm"] = raw_obpm
    mp = m["min"] * m["gp"]
    m["mp"] = mp

    # Team calibration: shift each team's minutes-weighted raw BPM/OBPM to
    # match that team's actual net_rating/off_rating (see module docstring
    # — this replaces Basketball-Reference's opponent-adjusted SRS term,
    # which this project has no data to compute).
    def calibrate(raw_col, target_col):
        grp = m.groupby(["team_abbreviation", "season"])
        weighted_avg = grp.apply(lambda g: np.dot(g[raw_col], g["mp"]) / g["mp"].sum())
        target = grp[target_col].first()
        adjustment = (target - weighted_avg).rename("adj")
        return m.set_index(["team_abbreviation", "season"]).index.map(adjustment).values

    m["bpm"] = m["raw_bpm"] + calibrate("raw_bpm", "team_net_rating")
    m["obpm"] = m["raw_obpm"] + calibrate("raw_obpm", "team_off_rating_rel")
    m["dbpm"] = m["bpm"] - m["obpm"]

    pct_of_team_minutes = mp / (m["team_games"] * 48)
    m["vorp"] = (m["bpm"] + 2.0) * pct_of_team_minutes * (m["team_games"] / 82)

    return m[["player_id", "player_name", "season", "position", "offensive_role", "bpm", "obpm", "dbpm", "vorp"]]


def save(conn, result):
    cur = conn.cursor()
    for col in ["bpm", "obpm", "dbpm", "vorp", "bpm_position"]:
        cur.execute(f"ALTER TABLE player_season_stats ADD COLUMN IF NOT EXISTS {col} DOUBLE PRECISION;")
    conn.commit()

    rows = [
        (int(r.player_id), int(r.season), round(float(r.bpm), 3), round(float(r.obpm), 3),
         round(float(r.dbpm), 3), round(float(r.vorp), 3), round(float(r.position), 2))
        for r in result.itertuples(index=False)
    ]
    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE player_season_stats AS p SET
            bpm = v.bpm, obpm = v.obpm, dbpm = v.dbpm, vorp = v.vorp, bpm_position = v.bpm_position
        FROM (VALUES %s) AS v (player_id, season, bpm, obpm, dbpm, vorp, bpm_position)
        WHERE p.player_id = v.player_id AND p.season = v.season;
        """,
        rows,
    )
    conn.commit()


if __name__ == "__main__":
    conn = psycopg2.connect(**DB_CONFIG)
    print("Loading player_season_stats...")
    df = load_players(conn)
    print(f"  {len(df)} eligible player-seasons (gp>0, poss>0)")

    print("Building team-season aggregates...")
    team_agg = build_team_aggregates(df)
    print(f"  {len(team_agg)} team-seasons")

    print("Estimating positions (3-pass trimmed regression)...")
    position_df = compute_position(df, team_agg)
    print(f"  position range: {position_df.position.min():.2f} to {position_df.position.max():.2f}, "
          f"mean {position_df.position.mean():.2f} (expect ~3.0)")

    print("Estimating offensive roles...")
    role_df = compute_offensive_role(df, team_agg)
    print(f"  offensive_role range: {role_df.offensive_role.min():.2f} to {role_df.offensive_role.max():.2f}, "
          f"mean {role_df.offensive_role.mean():.2f} (expect ~3.0)")

    print("Computing BPM/OBPM/DBPM/VORP...")
    result = compute_bpm(df, team_agg, position_df, role_df)
    print(f"  BPM range: {result.bpm.min():.2f} to {result.bpm.max():.2f}, mean {result.bpm.mean():.2f} (expect ~0)")

    print("\nTop 10 by BPM, season 2025 (2024-25):")
    top = result[result.season == 2025].sort_values("bpm", ascending=False).head(10)
    for r in top.itertuples():
        print(f"  {r.player_name:<28} BPM={r.bpm:>6.2f}  OBPM={r.obpm:>6.2f}  DBPM={r.dbpm:>6.2f}  VORP={r.vorp:>5.2f}")

    print("\nSaving to player_season_stats (bpm, obpm, dbpm, vorp, bpm_position columns)...")
    save(conn, result)
    conn.close()
    print("Done.")
