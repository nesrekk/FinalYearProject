"""
build_referee_tendencies.py
=============================
Aggregates real per-game official assignments (game_officials) and real
per-team-game box stats (game_team_box, both built by
fetch_referee_officials.py) into referee_tendencies: for each real
official, their real average fouls called and real FTA per game they
worked, and a real pace estimate for those games, each compared against
the real league average for the same real seasons — with a 95%
confidence interval on the difference, and a small-sample warning
rather than either hiding or overstating thin real samples.

Deliberately neutral by construction: this measures real games-called
totals against real league baselines, nothing about *why* a difference
exists (crew composition, team style faced, era, schedule assignment
are all real, unmeasured confounders) — the API/UI wording says so
explicitly rather than implying bias or intent.

Usage:
    cd scripts && python3 build_referee_tendencies.py
"""

import psycopg2
import psycopg2.extras
from scipy import stats

from db_config import DB_CONFIG

MIN_GAMES_SMALL_N = 25  # below this, a wide/unstable real CI gets flagged


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referee_tendencies (
            official_id BIGINT PRIMARY KEY,
            official_name TEXT NOT NULL,
            n_games INTEGER NOT NULL,
            season_min INTEGER,
            season_max INTEGER,
            avg_total_fouls DOUBLE PRECISION,
            league_avg_fouls DOUBLE PRECISION,
            fouls_diff DOUBLE PRECISION,
            fouls_diff_pct DOUBLE PRECISION,
            fouls_ci_low DOUBLE PRECISION,
            fouls_ci_high DOUBLE PRECISION,
            avg_total_fta DOUBLE PRECISION,
            league_avg_fta DOUBLE PRECISION,
            fta_diff DOUBLE PRECISION,
            fta_diff_pct DOUBLE PRECISION,
            fta_ci_low DOUBLE PRECISION,
            fta_ci_high DOUBLE PRECISION,
            avg_pace DOUBLE PRECISION,
            league_avg_pace DOUBLE PRECISION,
            pace_diff_pct DOUBLE PRECISION,
            small_n_warning BOOLEAN NOT NULL
        );
    """)


def mean_ci_95(values):
    """Real sample mean and a real 95% CI via the t-distribution. n=1 -> no CI."""
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, None, None
    sd = float(stats.tstd(values))
    if sd == 0:
        return mean, mean, mean
    se = sd / (n ** 0.5)
    t_crit = float(stats.t.ppf(0.975, df=n - 1))
    return mean, mean - t_crit * se, mean + t_crit * se


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    # Real per-game totals: sum fouls/FTA across both real teams in the
    # game, average the two real per-team possessions estimates for a
    # real game-pace proxy.
    cursor.execute("""
        SELECT game_id, season, SUM(pf) AS total_fouls, SUM(fta) AS total_fta, AVG(poss_est) AS pace
        FROM game_team_box
        GROUP BY game_id, season
        HAVING COUNT(*) = 2;
    """)
    game_rows = cursor.fetchall()
    games = {gid: {"season": season, "fouls": float(fouls), "fta": float(fta), "pace": float(pace)}
              for gid, season, fouls, fta, pace in game_rows}
    print(f"{len(games)} real games with complete two-team box stats.")

    # Real league averages per real season.
    by_season = {}
    for g in games.values():
        by_season.setdefault(g["season"], {"fouls": [], "fta": [], "pace": []})
        by_season[g["season"]]["fouls"].append(g["fouls"])
        by_season[g["season"]]["fta"].append(g["fta"])
        by_season[g["season"]]["pace"].append(g["pace"])
    league_avg = {
        season: {
            "fouls": sum(v["fouls"]) / len(v["fouls"]),
            "fta": sum(v["fta"]) / len(v["fta"]),
            "pace": sum(v["pace"]) / len(v["pace"]),
        }
        for season, v in by_season.items()
    }

    cursor.execute("SELECT official_id, official_name, game_id FROM game_officials;")
    off_rows = cursor.fetchall()

    by_official = {}
    for official_id, official_name, game_id in off_rows:
        g = games.get(game_id)
        if g is None:
            continue  # box stats missing for this real game, skip
        rec = by_official.setdefault(official_id, {"name": official_name, "games": []})
        rec["games"].append(g)

    upsert_rows = []
    for official_id, rec in by_official.items():
        gs = rec["games"]
        n = len(gs)
        fouls_vals = [g["fouls"] for g in gs]
        fta_vals = [g["fta"] for g in gs]
        pace_vals = [g["pace"] for g in gs]
        seasons = [g["season"] for g in gs]

        # Season-adjusted league baseline: this official's real game mix
        # weighted against each real game's own real season average,
        # so a crew working mostly recent (higher-pace) seasons isn't
        # compared against a stale league-wide blend.
        league_fouls_vals = [league_avg[g["season"]]["fouls"] for g in gs]
        league_fta_vals = [league_avg[g["season"]]["fta"] for g in gs]
        league_pace_vals = [league_avg[g["season"]]["pace"] for g in gs]

        fouls_diffs = [f - lf for f, lf in zip(fouls_vals, league_fouls_vals)]
        fta_diffs = [f - lf for f, lf in zip(fta_vals, league_fta_vals)]

        avg_fouls = sum(fouls_vals) / n
        avg_fta = sum(fta_vals) / n
        avg_pace = sum(pace_vals) / n
        league_fouls_blend = sum(league_fouls_vals) / n
        league_fta_blend = sum(league_fta_vals) / n
        league_pace_blend = sum(league_pace_vals) / n

        fouls_diff_mean, fouls_ci_lo, fouls_ci_hi = mean_ci_95(fouls_diffs)
        fta_diff_mean, fta_ci_lo, fta_ci_hi = mean_ci_95(fta_diffs)

        upsert_rows.append((
            official_id, rec["name"], n, min(seasons), max(seasons),
            round(avg_fouls, 2), round(league_fouls_blend, 2),
            round(fouls_diff_mean, 3), round(100 * fouls_diff_mean / league_fouls_blend, 2),
            round(fouls_ci_lo, 3) if fouls_ci_lo is not None else None,
            round(fouls_ci_hi, 3) if fouls_ci_hi is not None else None,
            round(avg_fta, 2), round(league_fta_blend, 2),
            round(fta_diff_mean, 3), round(100 * fta_diff_mean / league_fta_blend, 2),
            round(fta_ci_lo, 3) if fta_ci_lo is not None else None,
            round(fta_ci_hi, 3) if fta_ci_hi is not None else None,
            round(avg_pace, 2), round(league_pace_blend, 2),
            round(100 * (avg_pace - league_pace_blend) / league_pace_blend, 2),
            n < MIN_GAMES_SMALL_N,
        ))

    cursor.execute("DELETE FROM referee_tendencies;")
    psycopg2.extras.execute_values(
        cursor,
        """INSERT INTO referee_tendencies
           (official_id, official_name, n_games, season_min, season_max,
            avg_total_fouls, league_avg_fouls, fouls_diff, fouls_diff_pct, fouls_ci_low, fouls_ci_high,
            avg_total_fta, league_avg_fta, fta_diff, fta_diff_pct, fta_ci_low, fta_ci_high,
            avg_pace, league_avg_pace, pace_diff_pct, small_n_warning)
           VALUES %s;""",
        upsert_rows,
    )
    conn.commit()
    conn.close()
    print(f"✅ referee_tendencies rebuilt: {len(upsert_rows)} real officials.")


if __name__ == "__main__":
    main()
