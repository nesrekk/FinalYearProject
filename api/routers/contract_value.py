from typing import Optional

from season_team import season_team_sql, select_list
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    get_db,
)

router = APIRouter()

LIABILITY_MIN_SALARY = 25_000_000
BARGAIN_MIN_MINUTES = 500

METHODOLOGY = (
    "Fair value = max(WAR, 0) x cost per win + that season's league minimum; surplus = fair value - real salary. "
    "WAR starts from VORP x 2.7 (Basketball-Reference's documented conversion), using Basketball-Reference's "
    "published VORP. The league's positive WAR sums to 1.25-1.31x the real wins above replacement, so "
    "each season's WAR is scaled so that sum equals the real wins above replacement, keeping WAR and cost per win on "
    "the same real-win scale. Cost per win = (real salaries of players who actually played that season - players x "
    "league minimum) / (real league wins - wins a .200 team would get), from real salaries, real team records and "
    "the real CBA minimum salary. Only players who played are counted because the salary data carries stale rows "
    "for retired players; so this is the price of on-court production, not total team spending. Salaries come from "
    "two public Kaggle datasets built from HoopsHype and Basketball-Reference (no license stated), checked against "
    "real contracts; 2020-21 to 2023-24 are left out because that data is inflation-adjusted, 2017-18 because too "
    "many players are missing, and some individual rows are known to be wrong upstream. A value estimate, not a "
    "valuation of the contract's full terms (years, options, injury risk)."
)


def _require(cursor):
    cursor.execute("SELECT to_regclass('public.contract_value');")
    if cursor.fetchone()[0] is None:
        raise HTTPException(status_code=503, detail="Contract value data hasn't been built yet — run scripts/load_salaries.py "
                                                    "then scripts/build_contract_value.py (needs nba_data/salaries/).")


def _season_row(cursor, season):
    cursor.execute(
        """SELECT season, oncourt_payroll, n_paid_players, n_players, min_salary_0yr, min_salary_source,
                  replacement_cost, wins, games, replacement_wins, wins_above_replacement, cost_per_win,
                  minutes_coverage, war_positive_sum, war_to_real_wins_ratio, war_scale_k, included
           FROM contract_value_seasons WHERE season = %s;""",
        (season,),
    )
    r = cursor.fetchone()
    if r is None:
        return None
    keys = ["season", "oncourt_payroll", "n_paid_players", "n_players", "min_salary_0yr", "min_salary_source",
            "replacement_cost", "wins", "games", "replacement_wins", "wins_above_replacement", "cost_per_win",
            "minutes_coverage", "war_positive_sum", "war_to_real_wins_ratio", "war_scale_k", "included"]
    return dict(zip(keys, r))


PLAYER_COLS = ("player_id, player_name, team_abbreviation, minutes, salary, vorp, war_raw, war, fair_value, surplus, "
               "rookie_scale_years, draft_pick, draft_year")
PLAYER_KEYS = [c.strip() for c in PLAYER_COLS.split(",")]


@router.get("/contracts/value")
def get_contract_value(season: Optional[int] = None, top_n: int = 10):
    top_n = max(1, min(top_n, 50))
    with get_db() as conn:
        cursor = conn.cursor()
        _require(cursor)
        cursor.execute("SELECT season, included FROM contract_value_seasons ORDER BY season;")
        all_seasons = cursor.fetchall()
        seasons = [s for s, inc in all_seasons if inc]
        excluded = [s for s, inc in all_seasons if not inc]
        if season is None:
            season = seasons[-1]
        if season not in seasons:
            raise HTTPException(
                status_code=404,
                detail=f"No reliable salary data for {season - 1}-{str(season)[-2:]} (covered: 2005-06 to 2016-17, "
                       "2018-19, 2019-20 and 2024-25).",
            )
        summary = _season_row(cursor, season)
        cursor.execute(
            f"""SELECT {select_list(cursor, PLAYER_COLS)} FROM contract_value WHERE season = %s AND minutes >= %s AND surplus IS NOT NULL
                ORDER BY surplus DESC LIMIT %s;""",
            (season, BARGAIN_MIN_MINUTES, top_n),
        )
        bargains = [dict(zip(PLAYER_KEYS, r)) for r in cursor.fetchall()]
        cursor.execute(
            f"""SELECT {select_list(cursor, PLAYER_COLS)} FROM contract_value WHERE season = %s AND salary >= %s AND surplus IS NOT NULL
                ORDER BY surplus ASC LIMIT %s;""",
            (season, LIABILITY_MIN_SALARY, top_n),
        )
        liabilities = [dict(zip(PLAYER_KEYS, r)) for r in cursor.fetchall()]
        cursor.execute(
            f"""SELECT player_id, player_name, {season_team_sql(cursor)}, salary, war, fair_value, surplus, minutes
               FROM contract_value WHERE season = %s AND war IS NOT NULL;""",
            (season,),
        )
        points = [
            {"player_id": r[0], "player_name": r[1], "team_abbreviation": r[2], "salary": r[3], "war": r[4],
             "fair_value": r[5], "surplus": r[6], "minutes": r[7]}
            for r in cursor.fetchall()
        ]

    return {
        "season": season,
        "seasons_available": seasons,
        "seasons_excluded_low_coverage": excluded,
        "methodology": METHODOLOGY,
        "summary": summary,
        "thresholds": {"liability_min_salary": LIABILITY_MIN_SALARY, "bargain_min_minutes": BARGAIN_MIN_MINUTES},
        "bargains": bargains,
        "liabilities": liabilities,
        "points": points,
        "_source": make_source(
            ["contract_value", "contract_value_seasons", "player_salaries", "league_minimum_salary",
             "player_season_stats"],
            "Kaggle salary datasets (HoopsHype / Basketball-Reference), CBA minimums, Team Summaries",
        ),
    }


@router.get("/contracts/player/{player_id}")
def get_player_contract_value(player_id: int, season: int):
    with get_db() as conn:
        cursor = conn.cursor()
        _require(cursor)
        summary = _season_row(cursor, season)
        if summary is None or not summary["included"]:
            return {"season": season, "player_id": player_id, "available": False,
                    "reason": "No reliable salary data for this season (covered: 2005-06 to 2016-17, 2018-19, 2019-20 "
                              "and 2024-25)."}
        cursor.execute(f"SELECT {select_list(cursor, PLAYER_COLS)} FROM contract_value WHERE season = %s AND player_id = %s;",
                       (season, player_id))
        r = cursor.fetchone()
    if r is None:
        return {"season": season, "player_id": player_id, "available": False,
                "reason": "No matched salary on file for this player that season."}
    return {"season": season, "player_id": player_id, "available": True, "player": dict(zip(PLAYER_KEYS, r)),
            "cost_per_win": summary["cost_per_win"]}
