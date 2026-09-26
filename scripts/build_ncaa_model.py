"""
build_ncaa_model.py
====================
March Madness model: game-level win probabilities for NCAA tournament
games, and full-bracket odds (reach each round, win the title) from Monte
Carlo simulation of the real bracket, 2013-2026 (no 2020 tournament).

Every input is known before the tournament starts (cbb_games, from
fetch_cbb_games.py):
  - elo     CollegeBasketballData.com's Elo for the team at tip-off of its
            first NCAA game (built only from games already played).
  - margin  Opponent- and venue-adjusted scoring margin, fitted here by ridge
            least squares on every D1-vs-D1 game played before the first
            NCAA game (D1 = any team listed with a conference in some game
            that season; the source leaves a few D1 teams' conference blank
            in some games, e.g. LIU Brooklyn all of 2017-18 until the NCAAs) (margin per game capped at +/-25 so blowouts of weak
            teams don't dominate; small ridge penalty so every team gets a
            finite rating). Units: points per game better than an average
            D1 team on a neutral floor.
  - seed    The committee's seed.
Torvik's end-of-season ratings (college_team_seasons) are NOT used: for
2013-2025 they include the tournament games themselves.

Model: logistic regression on the difference between the two teams
(A - B), no intercept, each game entered in both orientations so
P(A beats B) = 1 - P(B beats A). Five feature sets are compared by
leave-one-season-out backtest on 2013-2025 (train on every other season,
predict the held-out one); the one with the lowest held-out log loss is
the model. 2026 is never used for training or model choice: it's the
final out-of-sample test, predicted by a model fitted on 2013-2025.

Bracket: rebuilt from the real games (First Four -> Round of 64 -> ... ->
final; each later game's participants are the winners of two earlier
games). 20,000 simulations per season, every game drawn from the model's
probability for whichever two teams meet. Backtest seasons use their
held-out model, so no season's odds were fitted on its own results.

Usage:
    cd scripts && python3 build_ncaa_model.py
"""

import math
import re

import numpy as np
import psycopg2
import psycopg2.extras
from sklearn.linear_model import LogisticRegression

from db_config import DB_CONFIG

SEASONS = [s for s in range(2013, 2027) if s != 2020]
TEST_SEASON = 2026
TRAIN_SEASONS = [s for s in SEASONS if s != TEST_SEASON]
MARGIN_CAP = 25
RIDGE = 2.0
N_SIMS = 20_000
ROUND_SIZES = [("First Four", 4), ("Round of 64", 32), ("Round of 32", 16), ("Sweet 16", 8),
               ("Elite 8", 4), ("Final Four", 2), ("Championship", 1)]
# Rounds a team can reach, as columns: p_r32 = won its Round of 64 game, etc.
# The source files one program under two names in the same season; every
# tournament team was checked for a low pre-tournament game count and this
# is the only case (LIU's 2017-18 regular season is under the second name;
# its 2013 tournament game is under the second name too).
NAME_ALIASES = {"Long Island University": "LIU Brooklyn"}
REACH = ["r64", "r32", "s16", "e8", "f4", "final", "champ"]
FEATURE_SETS = {
    "seed": ["seed"],
    "elo": ["elo"],
    "margin": ["margin"],
    "margin+elo": ["margin", "elo"],
    "margin+elo+seed": ["margin", "elo", "seed"],
}


def fit_margins(games):
    """Ridge least squares: capped margin = r_home - r_away + hca * (not neutral)."""
    teams = sorted({g[0] for g in games} | {g[1] for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((len(games), n + 1))
    y = np.zeros(len(games))
    for k, (home, away, hp, ap, neutral) in enumerate(games):
        X[k, idx[home]] = 1
        X[k, idx[away]] = -1
        X[k, n] = 0 if neutral else 1
        y[k] = max(-MARGIN_CAP, min(MARGIN_CAP, hp - ap))
    penalty = np.full(n + 1, RIDGE)
    penalty[n] = 0
    coef = np.linalg.solve(X.T @ X + np.diag(penalty), X.T @ y)
    ratings = coef[:n] - coef[:n].mean()
    return {t: float(ratings[idx[t]]) for t in teams}, float(coef[n])


def build_bracket(ncaa):
    """ncaa: tournament games sorted by start time. Returns games with round
    index and feeder links; raises if the real games don't form a bracket."""
    # The source is missing two 2017 First Four games (the Tuesday pair);
    # those winners then enter at the Round of 64 and the losers aren't listed.
    n_first_four = len(ncaa) - sum(n for _, n in ROUND_SIZES[1:])
    if not 0 <= n_first_four <= 4:
        raise ValueError(f"expected 63-67 tournament games, got {len(ncaa)}")
    sizes = [n_first_four] + [n for _, n in ROUND_SIZES[1:]]
    games, pos = [], 0
    for r, size in enumerate(sizes):
        for g in ncaa[pos:pos + size]:
            games.append({**g, "round": r})
        pos += size

    def winner(g):
        if g["home_points"] is not None and g["away_points"] is not None and g["home_points"] != g["away_points"]:
            return g["home_team"] if g["home_points"] > g["away_points"] else g["away_team"]
        return None  # no-contest (2021 Oregon-VCU): resolved from the next round below

    for r in range(1, len(ROUND_SIZES)):
        prev = [g for g in games if g["round"] == r - 1]
        for g in (g for g in games if g["round"] == r):
            g["feeders"] = []
            for side in ("home_team", "away_team"):
                team = g[side]
                src = [p for p in prev if team in (p["home_team"], p["away_team"])]
                if r == 1 and not src:  # seeded straight into the Round of 64
                    g["feeders"].append(("team", team))
                    continue
                if len(src) != 1:
                    raise ValueError(f"{team} in round {r} has {len(src)} feeder games")
                w = winner(src[0])
                if w is None:
                    src[0]["resolved_winner"] = team
                elif w != team:
                    raise ValueError(f"{team} reached round {r} without winning its game")
                g["feeders"].append(("game", src[0]["game_id"]))
    for g in games:
        if g["round"] == 0:
            g["feeders"] = [("team", g["home_team"]), ("team", g["away_team"])]
        g["winner"] = winner(g) or g.get("resolved_winner")
    return games


def region_names(games):
    """Region label for each Elite 8 game, from the game notes when they name one."""
    labels = {}
    for g in games:
        if g["round"] == 4:
            m = re.search(r"(East|West|South|Midwest|Southeast|Southwest)\b", g["game_notes"] or "")
            labels[g["game_id"]] = f"{m.group(1)} Region" if m else None
    return labels


def played(g):
    """A real, finished game: excludes 2021's Oregon-VCU no-contest, which the
    source stores as a 1-0 'scheduled' game (Oregon advanced without playing)."""
    return g["status"] == "final" and g["winner"] is not None and g["home_points"] != g["away_points"]


def design(pairs, feats, fs):
    rows = []
    for a, b in pairs:
        rows.append([feats[a][f] - feats[b][f] for f in FEATURE_SETS[fs]])
    return np.asarray(rows, dtype=float)


def fit_model(games_by_season, feats_by_season, seasons, fs):
    X, y = [], []
    for s in seasons:
        for g in games_by_season[s]:
            if not played(g):
                continue
            a, b = g["home_team"], g["away_team"]
            won = 1 if g["winner"] == a else 0
            X.append(design([(a, b)], feats_by_season[s], fs)[0]); y.append(won)
            X.append(design([(b, a)], feats_by_season[s], fs)[0]); y.append(1 - won)
    model = LogisticRegression(fit_intercept=False, C=1e4, max_iter=2000)
    model.fit(np.asarray(X), np.asarray(y))
    return model


def game_metrics(model, games, feats, fs):
    out = []
    for g in games:
        if not played(g):
            continue
        p = float(model.predict_proba(design([(g["home_team"], g["away_team"])], feats, fs))[0, 1])
        won = g["winner"] == g["home_team"]
        out.append((p, won))
    return out


def simulate(games, feats, model, fs, rng):
    teams = sorted({g["home_team"] for g in games} | {g["away_team"] for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    X = np.array([[feats[a][f] for f in FEATURE_SETS[fs]] for a in teams])
    diff = X[:, None, :] - X[None, :, :]
    logits = diff @ model.coef_[0]
    P = 1 / (1 + np.exp(-logits))

    winners = {}
    reach = np.zeros((len(teams), len(REACH)))
    for g in sorted(games, key=lambda g: g["round"]):
        sides = []
        for kind, ref in g["feeders"]:
            sides.append(np.full(N_SIMS, idx[ref]) if kind == "team" else winners[ref])
        a, b = sides
        w = np.where(rng.random(N_SIMS) < P[a, b], a, b)
        winners[g["game_id"]] = w
        if g["round"] >= 1:
            np.add.at(reach[:, g["round"] - 1], a, 1)
            np.add.at(reach[:, g["round"] - 1], b, 1)
        if g["round"] == len(ROUND_SIZES) - 1:
            np.add.at(reach[:, -1], w, 1)
    return teams, reach / N_SIMS


def actual_reach(games):
    got = {}
    for g in games:
        for side in ("home_team", "away_team"):
            got.setdefault(g[side], 0)
            if g["round"] >= 1:
                got[g[side]] = max(got[g[side]], g["round"])
        if g["round"] == len(ROUND_SIZES) - 1:
            got[g["winner"]] = len(REACH)
    return got  # number of REACH columns achieved (0 = lost in First Four)


def accuracy(res):
    """Share of games called right; an exact 50/50 call (seed-only model,
    same-seed games) gets half credit instead of counting as a miss."""
    return float(np.mean([0.5 if p == 0.5 else float((p > 0.5) == w) for p, w in res]))


def reach_log_loss(teams, probs, got):
    total, n = 0.0, 0
    for i, t in enumerate(teams):
        for r in range(len(REACH)):
            p = min(max(probs[i, r], 1e-4), 1 - 1e-4)
            hit = got[t] >= r + 1
            total -= math.log(p if hit else 1 - p)
            n += 1
    return total / n


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    games_by_season, feats_by_season, hca_by_season, n_reg_by_season = {}, {}, {}, {}

    for season in SEASONS:
        cur.execute(
            """SELECT game_id, start_date, home_team, away_team, home_points, away_points, home_seed,
                      away_seed, home_elo_start, away_elo_start, game_notes, status
               FROM cbb_games WHERE season = %s AND tournament = 'NCAA' ORDER BY start_date, game_id""",
            (season,),
        )
        cols = [d[0] for d in cur.description]
        ncaa = [dict(zip(cols, r)) for r in cur.fetchall()]
        for g in ncaa:
            g["home_team"] = NAME_ALIASES.get(g["home_team"], g["home_team"])
            g["away_team"] = NAME_ALIASES.get(g["away_team"], g["away_team"])
        games = build_bracket(ncaa)
        first_tip = ncaa[0]["start_date"]

        cur.execute(
            """WITH d1 AS (
                   SELECT home_team AS team FROM cbb_games WHERE season = %(s)s AND home_conference IS NOT NULL
                   UNION SELECT away_team FROM cbb_games WHERE season = %(s)s AND away_conference IS NOT NULL
               )
               SELECT home_team, away_team, home_points, away_points, neutral_site
               FROM cbb_games
               WHERE season = %(s)s AND start_date < %(tip)s AND status = 'final'
                 AND home_team IN (SELECT team FROM d1) AND away_team IN (SELECT team FROM d1)
                 AND home_points IS NOT NULL AND away_points IS NOT NULL""",
            {"s": season, "tip": first_tip},
        )
        regular = [(NAME_ALIASES.get(h, h), NAME_ALIASES.get(a, a), hp, ap, n)
                   for h, a, hp, ap, n in cur.fetchall()]
        margins, hca = fit_margins(regular)

        # Pre-tournament Elo = Elo at tip-off of the team's first NCAA game.
        # The one gap is 2021's Oregon-VCU no-contest (no Elo on that game):
        # Oregon's next game is its first real one, so its Elo there is still
        # pre-tournament; VCU played no NCAA game, so it takes the Elo from
        # the start of its last pre-tournament game (one game stale).
        first_elo, seed_of = {}, {}
        for g in sorted(games, key=lambda g: g["start_date"]):
            for side in ("home", "away"):
                team = g[f"{side}_team"]
                seed_of.setdefault(team, g[f"{side}_seed"])
                if team not in first_elo and g[f"{side}_elo_start"] is not None:
                    first_elo[team] = g[f"{side}_elo_start"]
        for team in seed_of:
            if team not in first_elo:
                cur.execute(
                    """SELECT CASE WHEN home_team = %(t)s THEN home_elo_start ELSE away_elo_start END
                       FROM cbb_games WHERE season = %(s)s AND start_date < %(tip)s
                         AND (home_team = %(t)s OR away_team = %(t)s)
                       ORDER BY start_date DESC LIMIT 1""",
                    {"t": team, "s": season, "tip": first_tip},
                )
                first_elo[team] = cur.fetchone()[0]
                print(f"  {season} {team}: no Elo on its NCAA games, using its last pre-tournament game")
        feats = {}
        for team in seed_of:
            if team not in margins:
                raise ValueError(f"{season} {team} has no pre-tournament D1 games")
            feats[team] = {"elo": first_elo[team] / 100.0, "margin": margins[team], "seed": float(seed_of[team])}
        games_by_season[season], feats_by_season[season] = games, feats
        hca_by_season[season], n_reg_by_season[season] = hca, len(regular)
        champ = [g for g in games if g["round"] == 6][0]["winner"]
        print(f"{season}: {len(regular)} pre-tournament D1 games, home edge {hca:.1f} pts, "
              f"{len(feats)} teams, champion {champ}")

    # Model choice: leave-one-season-out on 2013-2025 only.
    rng = np.random.default_rng(2026)
    backtest = []
    for fs in FEATURE_SETS:
        pooled = []
        for s in TRAIN_SEASONS:
            model = fit_model(games_by_season, feats_by_season, [t for t in TRAIN_SEASONS if t != s], fs)
            res = game_metrics(model, games_by_season[s], feats_by_season[s], fs)
            pooled += res
            backtest.append((fs, s, len(res),
                             float(np.mean([-math.log(p if w else 1 - p) for p, w in res])),
                             float(np.mean([(p - w) ** 2 for p, w in res])),
                             accuracy(res)))
        ll = np.mean([-math.log(p if w else 1 - p) for p, w in pooled])
        acc = accuracy(pooled)
        print(f"  {fs:17s} held-out log loss {ll:.4f}, accuracy {acc:.3f} ({len(pooled)} games)")

    def pooled_ll(fs):
        rows = [b for b in backtest if b[0] == fs]
        return sum(b[2] * b[3] for b in rows) / sum(b[2] for b in rows)

    chosen = min(FEATURE_SETS, key=pooled_ll)
    print(f"Chosen by held-out log loss: {chosen}")

    # Bracket odds: held-out model per backtest season; 2013-2025 model for 2026.
    odds_rows, season_rows, game_rows = [], [], []
    for s in SEASONS:
        train = [t for t in TRAIN_SEASONS if t != s]
        per_fs = {}
        for fs in (chosen, "seed"):
            model = fit_model(games_by_season, feats_by_season, train, fs)
            teams, probs = simulate(games_by_season[s], feats_by_season[s], model, fs, rng)
            per_fs[fs] = (model, teams, probs)
        model, teams, probs = per_fs[chosen]
        _, seed_teams, seed_probs = per_fs["seed"]
        # Same-seed teams are interchangeable to a seed-only model, so their
        # odds differ only by simulation noise: average them within each
        # (seed, played-in-First-Four) group, and rank ties as ties.
        ff = {t for g in games_by_season[s] if g["round"] == 0 for t in (g["home_team"], g["away_team"])}
        groups = {}
        for i, t in enumerate(seed_teams):
            groups.setdefault((feats_by_season[s][t]["seed"], t in ff), []).append(i)
        for idxs in groups.values():
            seed_probs[idxs] = seed_probs[idxs].mean(axis=0)
        got = actual_reach(games_by_season[s])
        champ = [g for g in games_by_season[s] if g["round"] == 6][0]["winner"]
        order = np.argsort(-probs[:, -1])
        champ_rank = int(np.where(np.array(teams)[order] == champ)[0][0]) + 1
        champ_seed_p = seed_probs[seed_teams.index(champ), -1]
        seed_champ_rank = int((seed_probs[:, -1] > champ_seed_p + 1e-9).sum()) + 1
        seed_champ_tied = int((np.abs(seed_probs[:, -1] - champ_seed_p) <= 1e-9).sum())
        res = game_metrics(model, games_by_season[s], feats_by_season[s], chosen)
        seed_res = game_metrics(per_fs["seed"][0], games_by_season[s], feats_by_season[s], "seed")
        season_rows.append((
            s, s == TEST_SEASON, chosen, champ, champ_rank, float(probs[teams.index(champ), -1]),
            seed_champ_rank, seed_champ_tied, float(seed_probs[seed_teams.index(champ), -1]),
            reach_log_loss(teams, probs, got), reach_log_loss(seed_teams, seed_probs, got),
            len(res), float(np.mean([-math.log(p if w else 1 - p) for p, w in res])),
            accuracy(res),
            float(np.mean([-math.log(p if w else 1 - p) for p, w in seed_res])),
            accuracy(seed_res),
            hca_by_season[s], n_reg_by_season[s],
        ))

        regions = region_names(games_by_season[s])
        region_of = {}

        def mark(gid, label):
            g = next(x for x in games_by_season[s] if x["game_id"] == gid)
            for kind, ref in g["feeders"]:
                if kind == "team":
                    region_of[ref] = label
                else:
                    mark(ref, label)

        for k, (gid, label) in enumerate(sorted(regions.items())):
            mark(gid, label or f"Region {k + 1}")

        for i, t in enumerate(teams):
            f = feats_by_season[s][t]
            odds_rows.append((s, t, int(f["seed"]), region_of.get(t), round(f["margin"], 2),
                              int(round(f["elo"] * 100)), *[round(float(v), 4) for v in probs[i]],
                              round(float(seed_probs[seed_teams.index(t), -1]), 4), got[t]))

        for g in games_by_season[s]:
            p = float(model.predict_proba(design([(g["home_team"], g["away_team"])], feats_by_season[s], chosen))[0, 1])
            game_rows.append((g["game_id"], s, ROUND_SIZES[g["round"]][0], g["round"], g["start_date"],
                              g["home_team"], g["home_seed"], g["home_points"], g["away_team"], g["away_seed"],
                              g["away_points"], g["winner"], round(p, 4)))
        tag = "TEST" if s == TEST_SEASON else "held-out"
        print(f"  {s} ({tag}): champion {champ} ranked #{champ_rank} by model "
              f"({probs[teams.index(champ), -1]:.1%}), seed-only #{seed_champ_rank} (tied with {seed_champ_tied - 1})")

    cur.execute("DROP TABLE IF EXISTS ncaa_bracket_odds, ncaa_model_seasons, ncaa_model_backtest, ncaa_tourney_games;")
    cur.execute(f"""
        CREATE TABLE ncaa_bracket_odds (
            season INTEGER, team TEXT, seed INTEGER, region TEXT, adj_margin REAL, elo INTEGER,
            {", ".join(f"p_{r} REAL" for r in REACH)},
            p_champ_seed_only REAL, actual_reach INTEGER,
            PRIMARY KEY (season, team)
        );
        CREATE TABLE ncaa_model_seasons (
            season INTEGER PRIMARY KEY, is_test BOOLEAN, feature_set TEXT, champion TEXT,
            champion_rank INTEGER, champion_p REAL, seed_champion_rank INTEGER, seed_champion_tied INTEGER,
            seed_champion_p REAL,
            reach_log_loss REAL, seed_reach_log_loss REAL, n_games INTEGER, game_log_loss REAL,
            game_accuracy REAL, seed_game_log_loss REAL, seed_game_accuracy REAL,
            home_edge REAL, n_pretourney_games INTEGER
        );
        CREATE TABLE ncaa_model_backtest (
            feature_set TEXT, season INTEGER, n_games INTEGER, log_loss REAL, brier REAL, accuracy REAL,
            chosen BOOLEAN, PRIMARY KEY (feature_set, season)
        );
        CREATE TABLE ncaa_tourney_games (
            game_id INTEGER PRIMARY KEY, season INTEGER, round_name TEXT, round INTEGER, start_date TIMESTAMPTZ,
            team_a TEXT, seed_a INTEGER, points_a INTEGER, team_b TEXT, seed_b INTEGER, points_b INTEGER,
            winner TEXT, p_a REAL
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO ncaa_bracket_odds VALUES %s;", odds_rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO ncaa_model_seasons VALUES %s;", season_rows)
    psycopg2.extras.execute_values(cur, "INSERT INTO ncaa_model_backtest VALUES %s;",
                                   [(*b, b[0] == chosen) for b in backtest])
    psycopg2.extras.execute_values(cur, "INSERT INTO ncaa_tourney_games VALUES %s;", game_rows)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
