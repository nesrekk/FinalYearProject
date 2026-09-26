"""
build_college_pipeline.py
==========================
College-to-NBA pipeline: every NBA draft pick 2013-2025 who came from a US
college, linked to how good their college team was in their draft season,
and to what they went on to do in the NBA.

Inputs (all real, all local):
  - Draft picks + college: Basketball-Reference's draft history
    (nba_data/kaggle_1947_present/Draft Pick History.csv). The NBA's own
    draft_history table would carry NBA player ids, but it is still empty
    (stats.nba.com timed out when fetch_draft_history.py was run).
  - NBA outcome: Basketball-Reference Win Shares (Advanced.csv, same
    player ids as the draft file, so no name matching on the NBA side).
    Traded players: the combined "2TM"/"3TM" row is used for that season,
    never the team rows on top of it.
  - College team: college_team_seasons (load_college_teams.py, Bart
    Torvik ratings). Team season = the player's last college season, which
    is the draft year unless the CollegeBasketballData.com player table
    (college_player_season_stats) shows an earlier final season.

Outcome and expectation:
  ws4       = NBA Win Shares in the first four seasons after the draft.
  expected  = a + b * ln(pick), least squares on EVERY NBA pick 2013-2022
              (college or not). Classes 2023-2025 have fewer than four
              seasons, so they are stored but excluded from the analysis.
  ws4_vs_expected = ws4 - expected.

College names are mapped to Torvik's spellings with a fixed rule
("X State" -> "X St.", drop "University") plus an explicit alias list; the
script prints every name it could not map instead of guessing.

Usage:
    cd scripts && python3 build_college_pipeline.py
"""

import math
import os
import re
import unicodedata

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
FIRST_CLASS, LAST_CLASS = 2013, 2025
MATURE_CLASS = 2022  # last class with four NBA seasons played (2022-23 .. 2025-26)

ALIASES = {
    "UNC": "North Carolina",
    "UConn": "Connecticut",
    "Miami (FL)": "Miami FL",
    "Pitt": "Pittsburgh",
    "NC State": ["North Carolina St.", "N.C. State"],
    "Louisiana": ["Louisiana Lafayette", "Louisiana"],
    "Detroit Mercy": ["Detroit", "Detroit Mercy"],
    "Central Florida": "UCF",
    "UW-Milwaukee": "Milwaukee",
    "Loyola (MD)": "Loyola MD",
    "Ohio University": "Ohio",
    "UMass": "Massachusetts",
    "Cal State Long Beach": "Long Beach St.",
}

# Hand-checked exceptions (Basketball-Reference id -> last college season,
# or None to leave the player out):
#  - Shaedon Sharpe enrolled at Kentucky in 2022 but never played a game.
#  - P.J. Hairston's last college season was 2012-13 at UNC; he spent
#    2013-14 in the D-League (the player table starts in 2014, so the
#    general rule can't see this).
OVERRIDES = {"sharpsh01": None, "hairspj02": 2013}


def torvik_name(college, season, torvik):
    """Torvik's spelling of a college for that season (Torvik renamed a few
    schools in 2025, e.g. North Carolina St. -> N.C. State)."""
    if college in ALIASES:
        candidates = ALIASES[college]
        candidates = [candidates] if isinstance(candidates, str) else candidates
    else:
        name = re.sub(r"^University of ", "", college)
        name = re.sub(r" University$", "", name)
        candidates = [re.sub(r" State$", " St.", name), name]
    for name in candidates:
        if season in torvik.get(name, ()):
            return name
    return None


def norm_person(name):
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"[.']", "", name.lower()).replace("-", " ")
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


def season_win_shares():
    adv = pd.read_csv(os.path.join(KAGGLE, "Advanced.csv"))
    adv = adv[adv.lg == "NBA"]
    is_multi = adv.team.astype(str).str.match(r"^\dTM$")
    multi_keys = set(zip(adv[is_multi].player_id, adv[is_multi].season))
    keep = is_multi | ~pd.Series(
        [k in multi_keys for k in zip(adv.player_id, adv.season)], index=adv.index
    )
    return adv[keep].groupby(["player_id", "season"], as_index=False)[["ws", "mp", "g"]].sum()


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    draft = pd.read_csv(os.path.join(KAGGLE, "Draft Pick History.csv"))
    draft = draft[(draft.lg == "NBA") & draft.season.between(FIRST_CLASS, LAST_CLASS)].copy()

    ws = season_win_shares()
    ws_by_player = {pid: g for pid, g in ws.groupby("player_id")}

    def outcome(row):
        g = ws_by_player.get(row.player_id)
        if g is None:
            return 0.0, 0, 0.0, 0
        first4 = g[g.season.between(row.season + 1, row.season + 4)]
        return float(first4.ws.sum()), int((first4.g > 0).sum()), float(g.ws.sum()), int(first4.mp.sum())

    draft[["ws4", "seasons4", "ws_career", "mp4"]] = draft.apply(
        lambda r: pd.Series(outcome(r)), axis=1
    )

    mature = draft[draft.season <= MATURE_CLASS]
    x = np.log(mature.overall_pick.astype(float))
    a, b = np.linalg.lstsq(np.vstack([np.ones_like(x), x]).T, mature.ws4, rcond=None)[0]
    print(f"Expected WS4 = {a:.3f} + {b:.3f} * ln(pick), fit on {len(mature)} picks {FIRST_CLASS}-{MATURE_CLASS}")
    draft["expected_ws4"] = (a + b * np.log(draft.overall_pick.astype(float))).clip(lower=0)
    draft["ws4_vs_expected"] = draft.ws4 - draft.expected_ws4

    cur.execute("SELECT season, team FROM college_team_seasons;")
    torvik = {}
    for season, team in cur.fetchall():
        torvik.setdefault(team, set()).add(season)

    # Last real college season per player (CollegeBasketballData.com, 2014-2025).
    cur.execute("SELECT name, season, team FROM college_player_season_stats WHERE minutes > 0;")
    cbbd = {}
    for name, season, team in cur.fetchall():
        cbbd.setdefault(norm_person(name), []).append((season, team))

    # NBA player id (for headshots) from this project's own player table.
    cur.execute("SELECT DISTINCT ON (player_name) player_name, player_id FROM player_season_stats WHERE season >= 2014 ORDER BY player_name, season DESC;")
    nba_ids = {}
    for name, pid in cur.fetchall():
        nba_ids.setdefault(norm_person(name), set()).add(pid)

    college = draft[draft.college.notna()].copy()
    unmapped, verify = {}, {"draft_year": 0, "earlier_season": 0, "not_in_player_table": 0, "hand_checked": 0}
    rows = []
    for r in college.itertuples(index=False):
        if r.player_id in OVERRIDES:
            if OVERRIDES[r.player_id] is None:
                unmapped[f"{r.player} (never played a college game)"] = 1
                continue
            seasons = [OVERRIDES[r.player_id]]
        else:
            seasons = sorted(s for s, _ in cbbd.get(norm_person(r.player), []) if s <= r.season)
        if r.player_id in OVERRIDES:
            college_season, check = seasons[0], "hand_checked"
        elif seasons and seasons[-1] < r.season:
            college_season, check = seasons[-1], "earlier_season"
        elif seasons:
            college_season, check = r.season, "draft_year"
        else:
            college_season, check = r.season, "not_in_player_table"
        team = torvik_name(r.college, college_season, torvik)
        if team is None:
            unmapped[f"{r.college} ({college_season})"] = unmapped.get(f"{r.college} ({college_season})", 0) + 1
            continue
        verify[check] += 1
        ids = nba_ids.get(norm_person(r.player), set())
        rows.append((
            r.player_id, r.player, int(ids.pop()) if len(ids) == 1 else None,
            int(r.season), int(r.overall_pick), int(r.round), r.tm, r.college, team, college_season, check,
            round(r.ws4, 1), int(r.seasons4), int(r.mp4), round(r.ws_career, 1),
            round(float(r.expected_ws4), 2), round(float(r.ws4_vs_expected), 2),
            bool(r.season <= MATURE_CLASS),
        ))

    print(f"{len(draft)} NBA picks {FIRST_CLASS}-{LAST_CLASS}; {len(college)} list a college; "
          f"{len(rows)} linked to a Torvik team-season")
    print("Team season check:", verify)
    print("Not linked (college not in Torvik's D1 list):", unmapped)
    print("NBA ids found for headshots:", sum(r[2] is not None for r in rows))

    cur.execute("DROP TABLE IF EXISTS college_draft_pipeline;")
    cur.execute("""
        CREATE TABLE college_draft_pipeline (
            bref_id TEXT NOT NULL,
            player_name TEXT NOT NULL,
            player_id INTEGER,
            draft_year INTEGER NOT NULL,
            overall_pick INTEGER NOT NULL,
            round INTEGER,
            nba_team TEXT,
            college_listed TEXT,
            college_team TEXT NOT NULL,
            college_season INTEGER NOT NULL,
            season_check TEXT,
            ws4 REAL, seasons4 INTEGER, mp4 INTEGER, ws_career REAL,
            expected_ws4 REAL, ws4_vs_expected REAL,
            mature BOOLEAN,
            PRIMARY KEY (bref_id, draft_year)
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO college_draft_pipeline VALUES %s;", rows)
    # Comparison group: mature picks with no US college listed (international,
    # G League, high school). Same expectation curve.
    other_df = draft[draft.college.isna() & (draft.season <= MATURE_CLASS)]
    other = other_df.ws4_vs_expected.to_numpy()
    # Draft-and-stash confound: share who played no NBA games in the four seasons.
    college_mature = draft[draft.college.notna() & (draft.season <= MATURE_CLASS)]
    other_zero = float((other_df.seasons4 == 0).mean())
    college_zero = float((college_mature.seasons4 == 0).mean())
    other_played = other_df[other_df.seasons4 > 0].ws4_vs_expected
    college_played = college_mature[college_mature.seasons4 > 0].ws4_vs_expected
    print(f"No NBA games in first 4 seasons: no-college {other_zero:.1%}, college {college_zero:.1%}; "
          f"among players who did play: no-college {other_played.mean():+.2f} (n={len(other_played)}), "
          f"college {college_played.mean():+.2f} (n={len(college_played)})")
    rng = np.random.default_rng(0)
    boot = [rng.choice(other, len(other)).mean() for _ in range(4000)]
    lo, hi = np.percentile(boot, [2.5, 97.5])
    print(f"No-college picks {FIRST_CLASS}-{MATURE_CLASS}: n={len(other)}, WS4 vs expected {other.mean():+.2f} [{lo:+.2f}, {hi:+.2f}]")

    cur.execute("DROP TABLE IF EXISTS college_pipeline_meta;")
    cur.execute("CREATE TABLE college_pipeline_meta (key TEXT PRIMARY KEY, value REAL);")
    psycopg2.extras.execute_values(cur, "INSERT INTO college_pipeline_meta VALUES %s;", [
        ("fit_a", float(a)), ("fit_b", float(b)), ("fit_n", float(len(mature))),
        ("n_picks", float(len(draft))), ("n_with_college", float(len(college))),
        ("n_linked", float(len(rows))), ("mature_class", float(MATURE_CLASS)),
        ("other_n", float(len(other))), ("other_mean", float(other.mean())),
        ("other_lo", float(lo)), ("other_hi", float(hi)),
        ("other_zero_share", other_zero), ("college_zero_share", college_zero),
        ("other_played_mean", float(other_played.mean())), ("other_played_n", float(len(other_played))),
        ("college_played_mean", float(college_played.mean())), ("college_played_n", float(len(college_played))),
    ])
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
