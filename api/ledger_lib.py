"""Forecast Ledger: the frozen rules behind the locked preseason forecasts of a season
(Teams > Forecast Ledger). Shared by scripts/ledger_lock.py (makes and stores the lock),
api/routers/ledger.py (shows it and re-exports the exact CSV the hash is taken of) and, from
round 6 step 2, the nightly update, which must import this file, season_sim_lib.py and
luck_lib.py from the lock's git tag (LOCK_TAG), not from the working tree.

Two forecasts, both locked before the season's first tip:

  as_is    the Season Simulator's opening-day prior, unchanged: every team's rating is
           carry x last season's final SRS (carry, the prior variance tau2 and hca_n0 from
           season_sim_params), home court is last season's, and the pre-game model is the chosen
           form in pregame_model_fit (logistic on the expected margin plus both back-to-back
           flags). This is the model the paper evaluated.
  roster   the same machinery with a roster-aware prior mean: a * (the team's projected BPM
           from the players on its ESPN roster at lock time, league-centred) + c * last season's
           final SRS, with a, c and the prior variance fitted on a 2010-11 to 2025-26 hindcast
           (scripts/ledger_lock.py). The minutes rule (allocate_minutes) is written below.

Frozen rules (the lock stores each as text in ledger_meta, and its code commit):

  * Team minutes: a rostered player's projected minutes a game and projected BPM come from
    scripts/build_projections.py's Marcel method for the season (the same functions; the stored
    player_projections for the 501 players who played last season, plus players who sat out all
    of last season, whom the stored table leaves out). A player with no projection (a rookie, or
    under 250 NBA minutes over the last three seasons) takes no minutes; a player with projected
    minutes but no projected BPM plays at replacement level. A depth chart: players in order of
    projected minutes each get their projected minutes until the team's 240 a game are used (the
    last one gets what is left); if the roster's projected minutes add to less than 240, the rest
    go to a replacement-level player. Replacement level = BPM -2.0 (the level VORP is measured
    from; scripts/build_bpm_vorp.py). Team BPM = sum(minutes x BPM) / 48, per 100 possessions.
    Chosen over scaling every rostered player's minutes down to 240, which was tried first: with
    training-camp rosters of 18-21 players that rule depends on how many veterans a team brought
    to camp, and the hindcast can't tell the two apart (ledger_meta.rule_minutes_alternative).
    Injury status at lock time is stored, not used.
  * Schedule: ESPN's regular-season schedule at lock time. Back-to-back = the team also played
    the previous calendar day (equal to team_game_fatigue.rest_days = 0 in all 38,236 team-games
    2010-11 to 2025-26). Neutral-site games get no home court.
  * Missing games: the NBA announces 80 games per team before the season and adds the last two
    after the NBA Cup group stage (knockout games, or one home and one road game for the teams
    eliminated in the group stage; Wikipedia "NBA Cup", read 2026-09-30). Every game a team is
    still owed to reach 82 is simulated against a league-average opponent (rating 0), with
    venues that bring its home and road games closest to 41-41 (home first on a tie).
  * Simulation: season_sim_lib.simulate from the prior (each run draws every team's rating from
    N(prior mean, prior variance)), then season_sim_lib.simulate_playoffs (fixed bracket, best
    of seven, 2-2-1-1-1, the same pre-game model without rest terms). Runs and seeds in
    ledger_meta.
  * In-season odds (round 6 step 2): for a game on date D, ratings_on(D) = season_sim_lib's
    ratings_as_of on the final scores of games before D, with each forecast's locked prior mean
    and variance per team and the locked home court, game SD and hca_n0; P(home wins) from the
    locked coefficients, back-to-backs from the dates the games were actually played.

Nothing here changes after the lock: a fix goes into a new season's lock, never into this one.
"""

import csv
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

import season_sim_lib as L
from luck_lib import FRANCHISE

SEASON = 2027                     # end year: 2026-27
LOCK_TAG = "ledger-2026-27"
FORECASTS = ("as_is", "roster")
FORECAST_LABELS = {
    "as_is": "As is: the Season Simulator's prior (0.60 x last season's SRS)",
    "roster": "Roster-aware: projected BPM of each team's current roster, blended with last season's SRS",
}
GAMES_PER_TEAM = 82
TEAM_MINUTES = 240.0
REPLACEMENT_BPM = -2.0
HINDCAST_FROM = 2011              # first target season of the hindcast (needs the season before for SRS)
DIGITS = 6                        # every float in the locked tables is rounded to this before storing

# The locked tables, the order the canonical CSV writes them in, their columns and row order.
# ledger_lock (the hash itself) is not part of the CSV.
TABLES = {
    "ledger_meta": (["season", "key", "value", "note"], ["key"]),
    "ledger_schedule": (["season", "espn_id", "game_date", "tip_utc", "time_valid", "home", "away", "neutral_site",
                         "venue", "city", "note", "counted", "home_b2b", "away_b2b"], ["game_date", "tip_utc", "espn_id"]),
    "ledger_rosters": (["season", "team", "espn_athlete_id", "player_name", "birth_date", "position", "experience",
                        "injury_status", "has_contract", "player_id", "match_method", "proj_min", "proj_bpm",
                        "counted", "minutes", "contribution"], ["team", "espn_athlete_id"]),
    "ledger_hindcast": (["season", "target_season", "team", "players", "team_bpm", "team_bpm_centred", "gap_minutes",
                         "srs_prev", "srs_final", "pred_as_is", "pred_roster_loso", "wins", "games",
                         "exp_wins_as_is", "exp_wins_roster"], ["target_season", "team"]),
    "ledger_forecasts": (["season", "forecast", "kind", "key", "conference", "game_date", "home", "away", "venue",
                          "home_b2b", "away_b2b", "team_bpm", "srs_prev", "prior_mean", "prior_sd", "games_scheduled",
                          "games_placeholder", "exp_margin", "p_home", "mean_wins", "sd_wins", "wins_p10", "wins_p50",
                          "wins_p90", "p_playoffs", "p_top6", "p_playin", "p_first", "p_round2", "p_conf_finals",
                          "p_finals", "p_title", "locked_at"], ["forecast", "kind", "key"]),
}


def franchise(team):
    return FRANCHISE.get(team, team)


def rnd(x):
    """Round a float the way the locked tables store it (None and NaN stay None)."""
    if x is None:
        return None
    x = float(x)
    return None if np.isnan(x) else round(x, DIGITS)


# ── players ─────────────────────────────────────────────────────────────────

def norm_name(s):
    """Lower case, no accents, no punctuation, no generational suffix (Jr., II, III...)."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'\-]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def match_players(roster, bio, recent, recent_from):
    """NBA player ids for ESPN roster rows, in four tiers, first hit wins:
      name_dob       same normalised name and birth date in player_bio
      name_recent    same normalised name, one player_bio player who played since `recent_from`
                     (the two sources disagree on a few birth dates)
      dob_lastname   same birth date and last name, one such recent player_bio player (Ron / Ronald)
      name_stats     same normalised name, one player in player_season_stats since `recent_from`
                     (players with no player_bio row)
    roster: DataFrame (player_name, birth_date as YYYY-MM-DD). bio: player_id, player_name, birth_date,
    last_season. recent: player_id, player_name (player_season_stats since recent_from).
    Returns (player_id Series, method Series) aligned with roster; unmatched = None / 'none'."""
    bio = bio.assign(n=bio.player_name.map(norm_name), bd=bio.birth_date.astype(str))
    bio["last"] = bio.n.str.split().str[-1]
    rb = bio[bio.last_season >= recent_from]
    rec = recent.assign(n=recent.player_name.map(norm_name)).drop_duplicates(["player_id", "n"])
    by_name_dob = bio.groupby(["n", "bd"]).player_id.agg(list).to_dict()
    by_name_recent = rb.groupby("n").player_id.agg(list).to_dict()
    by_dob_last = rb.groupby(["bd", "last"]).player_id.agg(list).to_dict()
    by_stats = rec.groupby("n").player_id.agg(lambda s: sorted(set(s))).to_dict()
    ids, how = [], []
    for name, bd in zip(roster.player_name, roster.birth_date):
        n = norm_name(name)
        last = n.split()[-1] if n else ""
        for method, hit in (("name_dob", by_name_dob.get((n, bd))), ("name_recent", by_name_recent.get(n)),
                            ("dob_lastname", by_dob_last.get((bd, last))), ("name_stats", by_stats.get(n))):
            if hit and len(set(hit)) == 1:
                ids.append(int(hit[0]))
                how.append(method)
                break
        else:
            ids.append(None)
            how.append("none")
    return pd.Series(ids, index=roster.index, dtype=object), pd.Series(how, index=roster.index)


def allocate_minutes(players):
    """The minutes rule for one team (a depth chart). players: DataFrame with proj_min and proj_bpm
    (NaN = none) and a stable sort key column `order_key`. Returns (players with counted / minutes /
    contribution columns, team BPM per 100 possessions, minutes left to a replacement-level player)."""
    p = players.copy()
    p["_m"] = p.proj_min.fillna(0.0).clip(lower=0.0)
    p = p.sort_values(["_m", "order_key"], ascending=[False, True], kind="mergesort")
    left, mins = TEAM_MINUTES, []
    for m in p._m:
        x = min(float(m), left)
        mins.append(x)
        left -= x
    p["minutes"] = mins
    p["counted"] = p.minutes > 0
    bpm = p.proj_bpm.fillna(REPLACEMENT_BPM)
    p["contribution"] = p.minutes * bpm / 48.0
    team_bpm = float(p.contribution.sum() + left * REPLACEMENT_BPM / 48.0)
    return p.drop(columns="_m"), team_bpm, left


def allocate_minutes_scaled(players):
    """The rule tried first and dropped before the lock (kept to report its hindcast in ledger_meta): the 15
    players with the most projected minutes, all scaled down to 240 if they add to more."""
    p = players.copy()
    p["_m"] = p.proj_min.fillna(0.0).clip(lower=0.0)
    p = p.sort_values(["_m", "order_key"], ascending=[False, True], kind="mergesort")
    top = np.zeros(len(p), bool)
    top[:15] = True
    p["counted"] = top & (p._m.to_numpy() > 0)
    total = float(p._m[p.counted].sum())
    scale = TEAM_MINUTES / total if total > TEAM_MINUTES else 1.0
    p["minutes"] = np.where(p.counted, p._m * scale, 0.0)
    gap = max(TEAM_MINUTES - float(p.minutes.sum()), 0.0)
    p["contribution"] = p.minutes * p.proj_bpm.fillna(REPLACEMENT_BPM) / 48.0
    return p.drop(columns="_m"), float(p.contribution.sum() + gap * REPLACEMENT_BPM / 48.0), gap


# ── schedule ────────────────────────────────────────────────────────────────

def add_b2b(sched):
    """home_b2b / away_b2b for counted games: the team also played the calendar day before."""
    s = sched.copy()
    s["game_date"] = pd.to_datetime(s.game_date).dt.date
    c = s[s.counted]
    long = pd.concat([c[["espn_id", "game_date", "home"]].rename(columns={"home": "team"}).assign(side="home"),
                      c[["espn_id", "game_date", "away"]].rename(columns={"away": "team"}).assign(side="away")])
    long = long.sort_values(["team", "game_date", "espn_id"])
    prev = long.groupby("team").game_date.shift()
    long["b2b"] = [(d - p).days == 1 if isinstance(p, date) else False for d, p in zip(long.game_date, prev)]
    flags = long.pivot_table(index="espn_id", columns="side", values="b2b", aggfunc="first")
    s["home_b2b"] = s.espn_id.map(flags.get("home", pd.Series(dtype=bool))).where(s.counted)
    s["away_b2b"] = s.espn_id.map(flags.get("away", pd.Series(dtype=bool))).where(s.counted)
    return s


def home_rows(sched):
    """Counted games as the simulator's home rows (home, away, venue, home_b2b, away_b2b)."""
    c = sched[sched.counted].copy()
    c["venue"] = np.where(c.neutral_site, 0, 1)
    c["home_b2b"] = c.home_b2b.astype(bool)
    c["away_b2b"] = c.away_b2b.astype(bool)
    return c.reset_index(drop=True)


def placeholder_games(teams, rows, played_home=None, played_away=None):
    """(team, venue) for every game a team is still owed to reach 82: against a league-average opponent,
    venues bringing its home and road games closest to 41-41 (home first on a tie). rows: the home rows
    still to play; played_home / played_away: games already played by venue (dicts; neutral counts as neither)."""
    played_home = played_home or {}
    played_away = played_away or {}
    h = rows[rows.venue == 1].home.value_counts().to_dict()
    a = rows[rows.venue == 1].away.value_counts().to_dict()
    n = pd.concat([rows.home, rows.away]).value_counts().to_dict()
    out = []
    for t in teams:
        home = h.get(t, 0) + played_home.get(t, 0)
        away = a.get(t, 0) + played_away.get(t, 0)
        have = n.get(t, 0) + played_home.get(t, 0) + played_away.get(t, 0)
        for _ in range(max(GAMES_PER_TEAM - have, 0)):
            if home <= away:
                out.append((t, 1))
                home += 1
            else:
                out.append((t, -1))
                away += 1
    return out


# ── ratings, odds, simulation ───────────────────────────────────────────────

def prior_for(prior_means, prior_var, hca_prev, sigma_prev, hca_n0):
    """season_sim_lib (prior, params) from one forecast's locked prior ({team: mean}, one variance, stored in
    ledger_forecasts / ledger_meta): the means go in as ratings with carry 1, so ratings_as_of's posterior is
    the locked prior updated by the games played (for as_is exactly the Season Simulator's)."""
    prior = {"ratings": {franchise(t): float(m) for t, m in prior_means.items()},
             "hca": float(hca_prev), "sigma": float(sigma_prev)}
    params = {"carry": 1.0, "tau2": float(prior_var), "hca_n0": float(hca_n0)}
    return prior, params


def ratings_on(played, teams, prior, params):
    """Ratings as of a morning from the team-game rows played before it (luck_lib.prepare format)."""
    return L.ratings_as_of(played, teams, prior, params)


def game_odds(rows, rat, beta):
    """(expected margin, P(home wins)) for home rows from ratings as of their morning."""
    venue = rows.venue.to_numpy(float)
    em = rows.home.map(rat["r_post"]).to_numpy(float) - rows.away.map(rat["r_post"]).to_numpy(float) + venue * rat["hca"]
    p = L.win_prob(beta, em, rows.home_b2b.to_numpy(float), rows.away_b2b.to_numpy(float))
    return em, p


EMPTY_PLAYED = pd.DataFrame({"team_abbreviation": pd.Series(dtype=str), "opponent": pd.Series(dtype=str),
                             "venue": pd.Series(dtype=int), "win": pd.Series(dtype=bool),
                             "margin": pd.Series(dtype=float), "game_date": pd.Series(dtype=object)})


def simulate_preseason(teams, rows, rat, beta, extras, runs, seed):
    """A whole season from opening day, then the playoffs. Returns per-team summary dicts."""
    st = L.Standings(teams, EMPTY_PLAYED)
    rng = np.random.default_rng(seed)
    draws = L.draw_ratings(rat["r_post"], rat["var_post"], teams, runs, rng)
    sim = L.simulate(st, rows, SEASON, L.model_p_matrix(rows, st, beta, rat["hca"]), draws, beta, rat["hca"],
                     runs, rng, extra_games=extras)
    summ = L.summarize(sim, st, SEASON)
    po = L.simulate_playoffs(sim, st, draws, beta, rat["hca"], rng)
    for i, t in enumerate(st.teams):
        for k in ("round2", "conf_finals", "finals", "title"):
            summ[t][f"p_{k}"] = float(po[k][:, i].mean())
    checks = {"title_sum": float(po["title"].sum(1).mean()), "finals_sum": float(po["finals"].sum(1).mean()),
              "playoffs_sum": float(sim["playoffs"].sum(1).mean()), "games_min": float(sim["games"].min()),
              "games_max": float(sim["games"].max())}
    return summ, checks


# ── canonical export and hash ───────────────────────────────────────────────

def fmt(v):
    """One value as the canonical CSV writes it."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return "" if np.isnan(v) else f"{float(v):.{DIGITS}f}"
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def canonical_csv(cur, season=SEASON):
    """The lock's canonical CSV, read back from the database: one line per stored value,
    `table,row,field,value`, tables in TABLES order, rows in each table's key order, fields in column
    order (the season column is left out: every row is this season's). Timestamps are written in UTC
    whatever the session's time zone. Returns bytes (UTF-8, \\n)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["table", "row", "field", "value"])
    for table, (cols, keys) in TABLES.items():
        fields = [c for c in cols if c != "season"]
        cur.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE season = %s ORDER BY {', '.join(keys)}", (season,))
        for rec in cur.fetchall():
            d = dict(zip(cols, rec))
            row = "|".join(fmt(d[k]) for k in keys)
            for f in fields:
                w.writerow([table, row, f, fmt(d[f])])
    return buf.getvalue().encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()
