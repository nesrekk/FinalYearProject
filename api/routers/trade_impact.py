"""
Trade Impact: one screen for a 1-for-1 trade, built from three things that
already exist and nothing new is estimated here.

  * projected win% change   — /trade/simulate (the win model on the
                              minute-weighted roster net rating + TS%)
  * starting-five spacing   — the Spacing Lab's lineup report (Gravity is
                              additive over any five players)
  * payroll and surplus     — Contract Value (real salaries, fair value)

Each block says whether that season has the data; nothing is filled in.
"""

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from routers.contract_value import PLAYER_COLS, PLAYER_KEYS, _season_row
from routers.spacing_lab import lineup_report
from routers.trade_analyzer import simulate_trade
from source_badge import make_source

router = APIRouter()

MIN_LINEUP_POSS = 100
SEASON_GAMES = 82

CONTRACT_COVERAGE = "2005-06 to 2016-17, 2018-19, 2019-20 and 2024-25"

NOTES = {
    "wins": (
        "Projected win% is the Trade Analyzer's number: each roster's minute-weighted net rating and true-shooting % "
        "before and after the swap, fed to a linear regression (leave-one-season-out R² = 0.92, average error about "
        "2.5 wins over 82 games). Players carry their own season stats to the new team, so role, usage and minutes "
        "changes are not simulated. 'Wins' multiplies the win% change by 82 games."
    ),
    "spacing": (
        "Spacing is a proxy, not tracking gravity: the sum of the five players' Gravity Index (3PA rate, "
        "catch-and-shoot 3P% and contested-3PA share, each z-scored within the season). The 'likely starting five' "
        "is the team's most-used real five-man lineup that season; after the trade the incoming player takes the "
        "outgoing player's spot, or, if the outgoing player wasn't in that five, the spot of the starter with the "
        "fewest minutes per game. Available from 2013-14 (tracking era) and only when all five have 500+ minutes."
    ),
    "payroll": (
        f"Payroll is the sum of the real salaries the Contract Value tool matched for players who actually played "
        f"for the team that season, not the team's full cap sheet (two-way and injured players who never played are "
        f"missing, and the third-party salary data has known errors). Surplus = fair value − salary, where fair value "
        f"prices each player's WAR at that season's real cost per win. Covered seasons: {CONTRACT_COVERAGE}."
    ),
}


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _most_used_lineup(cursor, team, season):
    cursor.execute(
        """SELECT player_ids, group_name, gp, minutes, poss, off_rating, def_rating, net_rating
           FROM lineup_stats
           WHERE season = %s AND team_abbreviation = %s AND cardinality(player_ids) = 5 AND poss >= %s
           ORDER BY poss DESC LIMIT 1;""",
        (season, team, MIN_LINEUP_POSS),
    )
    r = cursor.fetchone()
    if not r:
        return None
    return {"player_ids": [int(i) for i in r[0]], "group_name": r[1], "gp": r[2], "minutes": r[3], "poss": r[4],
            "off_rating": r[5], "def_rating": r[6], "net_rating": r[7]}


def _spacing_block(cursor, season, team, roster, outgoing, incoming):
    """Before/after starting-five spacing for one team, or why it isn't available."""
    lineup = _most_used_lineup(cursor, team, season)
    if lineup is None:
        return {"available": False,
                "reason": f"No real {team} five-man lineup with {MIN_LINEUP_POSS}+ possessions on file for "
                          f"{_label(season)} (lineup data starts 2013-14)."}

    before_ids = lineup["player_ids"]
    mpg = {r["player_id"]: (r["min"] or 0) for r in roster}
    if outgoing["player_id"] in before_ids:
        replaced_id, rule = outgoing["player_id"], "outgoing_starter"
    else:
        replaced_id = min(before_ids, key=lambda i: mpg.get(i, 0))
        rule = "lowest_minutes_starter"
    after_ids = [incoming["player_id"] if i == replaced_id else i for i in before_ids]

    try:
        before = lineup_report(cursor, season, before_ids)
        after = lineup_report(cursor, season, after_ids)
    except HTTPException as exc:
        return {"available": False, "reason": exc.detail, "lineup": lineup, "rule": rule,
                "replaced_player_id": replaced_id}

    names = {r["player_id"]: r["player_name"] for r in roster}
    names[incoming["player_id"]] = incoming["player_name"]

    def slim(rep):
        return {
            "players": rep["players"],
            "spacing": rep["spacing"],
            "percentile_vs_real_lineups": rep["percentile_vs_real_lineups"],
            "predicted_ortg_change": rep["predicted_ortg_change"],
            "within_real_range": rep["within_real_range"],
            "no_effect_message": rep["no_effect_message"],
            "real_lineup": rep["real_lineup"],
        }

    return {
        "available": True,
        "lineup": lineup,
        "rule": rule,
        "replaced_player_id": replaced_id,
        "replaced_player_name": names.get(replaced_id),
        "before": slim(before),
        "after": slim(after),
        "delta_spacing": after["spacing"] - before["spacing"],
        "n_real_lineups": before["n_real_lineups"],
        "median_real_spacing": before["median_real_spacing"],
        "real_range": before["real_range"],
    }


def _team_contracts(cursor, season, team):
    cursor.execute(
        f"SELECT {PLAYER_COLS} FROM contract_value WHERE season = %s AND team_abbreviation = %s;",
        (season, team),
    )
    return [dict(zip(PLAYER_KEYS, r)) for r in cursor.fetchall()]


def _player_contract(cursor, season, player_id):
    cursor.execute(f"SELECT {PLAYER_COLS} FROM contract_value WHERE season = %s AND player_id = %s;",
                   (season, player_id))
    r = cursor.fetchone()
    return dict(zip(PLAYER_KEYS, r)) if r else None


def _totals(rows):
    return {
        "n_priced": len(rows),
        "payroll": float(sum(r["salary"] or 0 for r in rows)),
        "surplus": float(sum(r["surplus"] or 0 for r in rows if r["surplus"] is not None)),
        "n_with_surplus": sum(1 for r in rows if r["surplus"] is not None),
    }


def _payroll_block(cursor, season, team, outgoing_id, incoming_id):
    rows = _team_contracts(cursor, season, team)
    out_row = next((r for r in rows if r["player_id"] == outgoing_id), None)
    in_row = _player_contract(cursor, season, incoming_id)
    if not rows:
        return {"available": False, "reason": f"No matched salaries for {team} in {_label(season)}."}
    missing = [n for n, r in (("outgoing", out_row), ("incoming", in_row)) if r is None]
    if missing:
        return {"available": False,
                "reason": f"No matched salary on file for the {' and '.join(missing)} player in {_label(season)}.",
                "before": _totals(rows)}
    after_rows = [r for r in rows if r["player_id"] != outgoing_id] + [in_row]
    before, after = _totals(rows), _totals(after_rows)
    return {
        "available": True,
        "before": before,
        "after": after,
        "delta_payroll": after["payroll"] - before["payroll"],
        "delta_surplus": after["surplus"] - before["surplus"],
        "outgoing": out_row,
        "incoming": in_row,
    }


def _win_block(summary):
    b, a = summary["before"]["predicted_win_pct"], summary["after"]["predicted_win_pct"]
    if b is None or a is None:
        return {"available": False, "reason": "The win model isn't loaded (win_model.pkl missing)."}
    return {
        "available": True,
        "before_pct": b, "after_pct": a, "delta_pct": a - b,
        "before_wins": b * SEASON_GAMES, "after_wins": a * SEASON_GAMES, "delta_wins": (a - b) * SEASON_GAMES,
        "net_rating": {"before": summary["before"]["net_rating"], "after": summary["after"]["net_rating"]},
        "ts_pct": {"before": summary["before"]["ts_pct"], "after": summary["after"]["ts_pct"]},
        "season_games": SEASON_GAMES,
    }


@router.get("/trade/impact")
def trade_impact(season: int, team_a: str, player_a_id: int, team_b: str, player_b_id: int):
    team_a, team_b = team_a.upper(), team_b.upper()
    if team_a == team_b:
        raise HTTPException(status_code=400, detail="Pick two different teams.")
    sim = simulate_trade(season, team_a, player_a_id, team_b, player_b_id)
    side_a, side_b = sim["trade"]["team_a"], sim["trade"]["team_b"]

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_gravity'), to_regclass('public.contract_value');")
        has_gravity, has_contracts = cursor.fetchone()

        # Rosters with minutes, to pick the lowest-minute starter when needed.
        cursor.execute(
            "SELECT player_id, player_name, min FROM player_season_stats WHERE season = %s AND team_abbreviation = ANY(%s);",
            (season, [team_a, team_b]),
        )
        roster = [{"player_id": r[0], "player_name": r[1], "min": r[2]} for r in cursor.fetchall()]

        if has_gravity:
            cursor.execute("SELECT 1 FROM player_gravity WHERE season = %s AND gravity IS NOT NULL LIMIT 1;", (season,))
            gravity_season = cursor.fetchone() is not None
        else:
            gravity_season = False
        if gravity_season:
            spacing = {
                "available": True,
                "team_a": _spacing_block(cursor, season, team_a, roster, side_a["sends"], side_a["receives"]),
                "team_b": _spacing_block(cursor, season, team_b, roster, side_b["sends"], side_b["receives"]),
            }
        else:
            spacing = {"available": False,
                       "reason": f"No real tracking data for {_label(season)}; the Gravity Index starts in 2013-14."}

        cv_season = _season_row(cursor, season) if has_contracts else None
        if cv_season and cv_season["included"]:
            payroll = {
                "available": True,
                "cost_per_win": cv_season["cost_per_win"],
                "team_a": _payroll_block(cursor, season, team_a, player_a_id, player_b_id),
                "team_b": _payroll_block(cursor, season, team_b, player_b_id, player_a_id),
            }
        else:
            payroll = {"available": False,
                       "reason": f"No reliable salary data for {_label(season)} (covered: {CONTRACT_COVERAGE})."}

    return {
        "season": season,
        "season_label": _label(season),
        "trade": sim["trade"],
        "wins": {
            "team_a": _win_block(sim["team_a_summary"]),
            "team_b": _win_block(sim["team_b_summary"]),
        },
        "spacing": spacing,
        "payroll": payroll,
        "notes": NOTES,
        "coverage": {"wins": "2009-10 onward", "spacing": "2013-14 onward", "payroll": CONTRACT_COVERAGE},
        "_source": make_source(
            ["player_season_stats", "player_clusters", "lineup_stats", "player_gravity", "gravity_validation",
             "contract_value", "contract_value_seasons"],
            "nba_api (stats.nba.com), Kaggle salary datasets",
        ),
    }
