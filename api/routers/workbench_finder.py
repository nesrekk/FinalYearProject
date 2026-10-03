"""
Workbench Player Finder (round 7 step 6): players who meet conditions written
like a sentence, on any verified stat of the Workbench catalogue.

    POST /workbench/finder      run a finder spec (below)

    "Players who, in a single season from 2020-21 to 2025-26, over at least
     20 games, averaged at least 25 points per game and had a true shooting %
     of at least 60% (on at least 300 attempts) and in at least 10 games had
     at least 30 points and at least 5 assists in the same game."

Spec:

    {"dataset": "player_season" | "player_game",   # rows are seasons or games
     "scope": "season" | "span",                    # one result per player-season,
                                                    # or per player over every chosen row
     "season_from": 2021, "season_to": 2026,
     "entities": "all" | [player ids],              # e.g. a board's set
     "filters": [{"key": "home", "op": "eq", "value": true}],   # which rows count
     "min_games": 20,
     "conditions": [
        {"type": "value", "stat": "pts", "per": "game", "op": "gte", "value": 25},
        {"type": "value", "stat": "ts_pct", "op": "gte", "value": 0.6, "min_n": 300},
        {"type": "count", "tests": [{"stat": "pts", "op": "gte", "value": 30},
                                    {"stat": "ast", "op": "gte", "value": 5}],
         "count_op": "gte", "count": 10},
        {"type": "streak", "tests": [{"stat": "pts", "op": "gte", "value": 20}], "count": 5}],
     "sort": {"key": "c0" | "n_games" | "season", "dir": "desc"},
     "limit": 100, "offset": 0}

What each condition means (all of them must hold, over the same rows):

    value   the stat combined over the unit's rows exactly as the Workbench
            table combines it (workbench._value_sql, the catalogue's `kind`):
            averages are summed stat ÷ summed games (or per 36 / per 100 /
            totals, `per`); percentages are summed makes ÷ summed attempts
            (or the season table's attempt-weighted mean, the same thing);
            `min_n` is a floor on the value's sample in its n unit (attempts
            for a shooting %), so 3/3 from three never counts as 100%.
    count   how many rows (games, or seasons) of the unit meet every test
            *in the same row*; two count conditions are two separate counts
            ("in 10 games scored 30, and in 10 games had 10 assists" need not
            be the same games). A row whose stat isn't recorded (no attempts
            for a %) never meets a test (the Game Finder's rule).
    streak  the longest run of consecutive rows meeting every test is at least
            `count` long. Rows are in date (season) order, and only rows that
            count (season range, filters) are in the sequence: a game he sat
            out has no row and doesn't break a run, nor does a game a filter
            leaves out (the Game Finder's streak rule, checked equal in
            test_workbench_finder.py). With scope "season" a run ends with the
            season; with "span" it carries across seasons.

`min_games` floors the games in the unit (summed games for season rows).

The response restates the spec as one sentence (what was run, in words), and
gives every result the n behind each condition: a value's sample in its unit
and Stat Stability's reliability (greyed under 0.5, the Leaderboard's rule), a
count's hits out of the unit's rows, a streak's length and dates.

Safety is the query layer's: a fixed request schema (unknown keys refused),
every stat/field/operator looked up in the catalogue, values bound, a
read-only transaction with a statement_timeout, a row cap.
"""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt

import workbench_catalogue as WC
from routers.game_log import _names
from routers.leaderboard import _sample
from routers.workbench import (
    NUM_OPS, ROW_CAP, Condition, _bad, _column, _dim_condition, _entities, _gate, _num_condition, _out,
    _season_label, _stable, _value_sql, column_meta, execute_readonly, _dataset_meta,
)
from source_badge import make_source

router = APIRouter()

DATASETS = ("player_season", "player_game")
MAX_FINDER_CONDITIONS = 8
MAX_TESTS = 4
OP_WORDS = {"gte": "at least", "gt": "more than", "lte": "at most", "lt": "less than", "eq": "exactly",
            "ne": "other than", "between": "between"}
# Row order inside a unit, for streaks: the date (then the game id: ESPN's feed
# gives a handful of players two lines on one date) or the season.
ORDER = {"player_game": ("l.game_date", "f.game_id"), "player_season": ("s.season",)}
ORDER_LABEL = {"player_game": "l.game_date", "player_season": "s.season"}
TEAM = {"player_game": "l.team_abbreviation", "player_season": "s.team_abbreviation"}


class Test(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stat: str = Field(max_length=40)
    op: str = Field("gte", max_length=10)
    value: Any = None
    per: Literal["game", "total", "per36", "per100"] = "game"


class FinderCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["value", "count", "streak"]
    stat: str | None = Field(None, max_length=40)       # value
    op: str = Field("gte", max_length=10)               # value
    value: Any = None                                   # value
    per: Literal["game", "total", "per36", "per100"] = "game"   # value
    min_n: float | None = Field(None, ge=0)             # value
    tests: list[Test] = Field(default_factory=list, max_length=MAX_TESTS)   # count, streak
    count_op: Literal["gte", "gt", "lte", "lt", "eq"] = "gte"               # count
    count: StrictInt | None = Field(None, ge=1, le=100_000)                # count, streak


class FinderSort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=20)
    dir: Literal["asc", "desc"] = "desc"


class FinderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Literal["player_season", "player_game"] = "player_season"
    scope: Literal["season", "span"] = "season"
    season_from: int | None = None
    season_to: int | None = None
    entities: Literal["all"] | list[StrictInt] = "all"
    filters: list[Condition] = Field(default_factory=list, max_length=12)
    min_games: float | None = Field(None, ge=0)
    conditions: list[FinderCondition] = Field(min_length=1, max_length=MAX_FINDER_CONDITIONS)
    sort: FinderSort | None = None
    limit: int = Field(100, ge=1, le=ROW_CAP)
    offset: int = Field(0, ge=0, le=1_000_000)


# ─── words ──────────────────────────────────────────────────────────────────

def _num_text(v, fmt):
    if fmt == "pct":
        return f"{v * 100:g}%"
    if abs(v) >= 1000:
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,g}"
    return f"{v:g}"


def _op_text(op, value, fmt):
    if op == "between":
        return f"between {_num_text(value[0], fmt)} and {_num_text(value[1], fmt)}"
    return f"{OP_WORDS[op]} {_num_text(value, fmt)}"


def _stat_phrase(col, per, op, value, grouped):
    """'averaged at least 25 points per game', 'had a true shooting % of at least 60%'."""
    label = col.label if col.label[:2].isupper() else col.label[0].lower() + col.label[1:]
    if col.kind == "count":
        if per == "total":
            return f"totalled {_op_text(op, value, 'int')} {label}"
        unit = {"game": "per game", "per36": "per 36 minutes", "per100": "per 100 possessions"}[per]
        if not grouped:
            return f"had {_op_text(op, value, col.fmt)} {label}"
        return f"averaged {_op_text(op, value, col.fmt)} {label} {unit}"
    if col.kind == "sum":
        return f"had {_op_text(op, value, col.fmt)} {label}"
    return f"had a {label} of {_op_text(op, value, col.fmt)}"


def _row_phrase(col, test, game_rows):
    """One test on one row: 'had at least 30 points' (a game), 'averaged ... ' (a season row)."""
    if col.kind == "count" and not game_rows:
        return _stat_phrase(col, test.per, test.op, test.value, True)
    if col.kind == "count" and test.per != "game":
        return _stat_phrase(col, test.per, test.op, test.value, True)
    return _stat_phrase(col, "game", test.op, test.value, False)


# ─── compiling ──────────────────────────────────────────────────────────────

def _row_test_sql(ds, test, params, where):
    col = _column(ds, test.stat, "Test")
    expr = _value_sql(ds, col, test.per, grouped=False)[0]
    return f"COALESCE({_num_condition(expr, test.op, test.value, where, params)}, FALSE)", col


def compile_finder(spec: FinderSpec):
    ds = WC.DATASETS[spec.dataset]
    meta = _dataset_meta()[ds.key]
    game_rows = ds.key == "player_game"
    one_row = ds.key == "player_season" and spec.scope == "season"   # a unit is one season row
    if spec.season_from is not None and spec.season_to is not None and spec.season_from > spec.season_to:
        _bad("season_from is after season_to.")
    lo = meta["from"] if spec.season_from is None else max(spec.season_from, meta["from"])
    hi = meta["to"] if spec.season_to is None else min(spec.season_to, meta["to"])
    if lo > hi:
        _bad(f"{ds.label} cover {_season_label(meta['from'])} to {_season_label(meta['to'])}.")

    # Which rows count: the dataset's own rules, the seasons, the set, the filters.
    wparams = list(ds.from_params)
    where = list(ds.where) + [f"{ds.season_sql} BETWEEN %s AND %s"]
    wparams += [lo, hi]
    ent = _entities(ds, spec.entities, wparams)
    if ent:
        where.append(ent)
    filter_words = []
    for c in spec.filters:
        if c.key in ds.dims:
            where.append(_dim_condition(ds, ds.dims[c.key], c.op, c.value, wparams))
            filter_words.append(_dim_words(ds.dims[c.key], c.op, c.value))
        else:
            col = _column(ds, c.key, "Filter")
            where.append(_num_condition(_gate(ds, col, col.sql), c.op, c.value, f"Filter on {c.key}", wparams))
            filter_words.append(f"{'games' if game_rows else 'seasons'} with {col.label.lower()} "
                                f"{_op_text(c.op, c.value, col.fmt)}")
    where_sql = " AND ".join(where)

    unit = [f"{ds.entity_sql}"] + ([ds.season_sql] if spec.scope == "season" else [])
    unit_names = ["pid"] + (["season"] if spec.scope == "season" else [])
    unit_sel = ", ".join(f"{u} AS {n}" for u, n in zip(unit, unit_names))
    join_on = " AND ".join(f"a.{n} = b{{i}}.{n}" for n in unit_names)
    order = ", ".join(ORDER[ds.key])

    # agg: one row per unit, every value and count condition.
    agg_sel = [unit_sel, "COUNT(*) AS n_rows", f"SUM({ds.games_sql}) AS n_games",
               f"(array_agg({TEAM[ds.key]} ORDER BY {order} DESC))[1] AS team"]
    if not ds.names_from_ids:
        agg_sel.append(f"(array_agg(s.player_name ORDER BY {order} DESC))[1] AS player_name")
    agg_params = []
    streaks = []            # (index, hit SQL, params)
    conds, cmeta, words = [], [], []
    for i, c in enumerate(spec.conditions):
        where_txt = f"Condition {i + 1}"
        if c.type == "value":
            if not c.stat:
                _bad(f"{where_txt}: a value condition needs a stat.")
            if c.tests:
                _bad(f"{where_txt}: tests belong to count and streak conditions.")
            col = _column(ds, c.stat, where_txt)
            if col.kind == "none" and not one_row:
                _bad(f"{where_txt}: {col.label} can't be combined over several rows (one value per "
                     f"{ds.row_label[:-1]}); use it with player seasons, one season at a time, or in a "
                     "count or streak condition.")
            if col.kind == "none":
                v_sql, n_sql = _value_sql(ds, col, c.per, grouped=False)
                v_sql, n_sql = f"MAX({v_sql})", f"SUM({n_sql})"
            else:
                v_sql, n_sql = _value_sql(ds, col, c.per, grouped=True)
            agg_sel += [f"{v_sql} AS v{i}", f"{n_sql} AS n{i}"]
            stable = _stable(col)
            if stable:
                agg_sel.append(f"SUM(CASE WHEN ({_gate(ds, col, col.sql)}) IS NOT NULL THEN {col.sample} END) AS m{i}")
            conds.append((i, "value", col, stable))
            p = []
            test = _num_condition(f"a.v{i}", c.op, c.value, where_txt, p)
            cond_sql = [test]
            if c.min_n:
                cond_sql.append("a.n{} >= %s".format(i))
                p.append(float(c.min_n))
            cmeta.append((" AND ".join(cond_sql), p))
            per = c.per if col.kind == "count" else "game"
            if col.kind == "count" and per not in (col.per_modes or ds.per_modes):
                _bad(f"{where_txt}: {col.label} can't be shown {WC.PER_MODES[per]}.")
            phrase = _stat_phrase(col, per, c.op, c.value, True)
            if c.min_n:
                unit_words = (col.n_unit or ds.row_label).split(" (")[0]
                phrase += f" (on at least {_num_text(c.min_n, 'int')} {unit_words})"
            words.append(phrase)
        else:
            if c.stat is not None or c.value is not None:
                _bad(f"{where_txt}: a {c.type} condition takes tests, not stat/value.")
            if not c.tests:
                _bad(f"{where_txt}: a {c.type} condition needs at least one test.")
            if c.count is None:
                _bad(f"{where_txt}: a {c.type} condition needs a count.")
            tp, parts, tcols = [], [], []
            for j, t in enumerate(c.tests):
                sql, col = _row_test_sql(ds, t, tp, f"{where_txt}, test {j + 1}")
                parts.append(sql)
                tcols.append(col)
            hit = "(" + " AND ".join(parts) + ")"
            row = "game" if game_rows else "season"
            tw = " and ".join(p if k == 0 or not p.startswith("had ") else p[4:]
                              for k, p in enumerate(_row_phrase(col, t, game_rows) for col, t in zip(tcols, c.tests)))
            same = f" in the same {row}" if len(c.tests) > 1 else ""
            if c.type == "count":
                agg_sel.append(f"SUM(CASE WHEN {hit} THEN 1 ELSE 0 END) AS k{i}")
                agg_params += tp
                conds.append((i, "count", tcols, None))
                cmeta.append((f"a.k{i} {NUM_OPS[c.count_op]} %s", [c.count]))
                words.append(f"in {OP_WORDS[c.count_op]} {c.count:,} {row}{'' if c.count == 1 else 's'} {tw}{same}")
            else:
                streaks.append((i, hit, tp))
                conds.append((i, "streak", tcols, None))
                cmeta.append((f"COALESCE(b{i}.len, 0) >= %s", [c.count]))
                words.append(f"in at least {c.count:,} straight {row}s he played {tw}{same}")

    params = []
    sql = f"WITH a AS (\n  SELECT {', '.join(agg_sel)}\n  {ds.from_sql}\n  WHERE {where_sql}\n  GROUP BY {', '.join(unit)}\n)"
    # The agg SELECT's test parameters come before the WHERE's in the text.
    assert not ds.from_params, ds.key
    params = agg_params + wparams
    if streaks:
        seq_cols = ", ".join(f"{h} AS h{i}" for i, h, _ in streaks)
        part = ", ".join(unit)
        sql += (f",\nseq AS (\n  SELECT {unit_sel}, {ORDER_LABEL[ds.key]} AS ord, {seq_cols},\n"
                f"         row_number() OVER (PARTITION BY {part} ORDER BY {order}) AS rn\n"
                f"  {ds.from_sql}\n  WHERE {where_sql}\n)")
        for i, _h, tp in streaks:
            params += tp
        params += wparams
        pn = ", ".join(unit_names)
        for i, _h, _tp in streaks:
            sql += (f",\nr{i} AS (\n  SELECT {pn}, COUNT(*) AS len, MIN(ord) AS first, MAX(ord) AS last FROM (\n"
                    f"    SELECT {pn}, ord, rn - row_number() OVER (PARTITION BY {pn} ORDER BY rn) AS grp\n"
                    f"    FROM seq WHERE h{i}) x\n  GROUP BY {pn}, grp\n),\n"
                    f"b{i} AS (SELECT DISTINCT ON ({pn}) {pn}, len, first, last FROM r{i} "
                    f"ORDER BY {pn}, len DESC, first)")
    # final: the units that meet everything.
    sel = ["a.*"]
    joins = []
    for i, _h, _tp in streaks:
        sel += [f"b{i}.len AS s{i}", f"b{i}.first AS sf{i}", f"b{i}.last AS sl{i}"]
        joins.append(f"LEFT JOIN b{i} ON {join_on.format(i=i)}")
    final_where = []
    if spec.min_games is not None:
        final_where.append("a.n_games >= %s")
        params.append(float(spec.min_games))
    for csql, p in cmeta:
        final_where.append(f"({csql})")
        params += p
    sql += (f",\nfinal AS (\n  SELECT {', '.join(sel)} FROM a {' '.join(joins)}\n"
            f"  WHERE {' AND '.join(final_where) if final_where else 'TRUE'}\n)")

    # ORDER BY: a condition's number, games, rows or season; then the unit.
    sort_sql = {"n_games": "n_games", "n_rows": "n_rows"}
    if spec.scope == "season":
        sort_sql["season"] = "season"
    for i, kind, _c, _s in conds:
        sort_sql[f"c{i}"] = {"value": f"v{i}", "count": f"k{i}", "streak": f"s{i}"}[kind]
    if spec.sort is None:
        first = spec.conditions[0]
        dir_ = "asc" if first.type == "value" and first.op in ("lte", "lt") else "desc"
        sort = FinderSort(key="c0", dir=dir_)
    else:
        sort = spec.sort
    if sort.key not in sort_sql:
        _bad(f"Can't sort by '{sort.key}': use {', '.join(sort_sql)}.")
    order_by = [f"{sort_sql[sort.key]} {sort.dir.upper()} NULLS LAST", "pid"] + (["season"] if spec.scope == "season" else [])
    pool_where = "WHERE n_games >= %s" if spec.min_games is not None else ""
    sql += (f"\nSELECT final.*, COUNT(*) OVER () AS _matched, (SELECT COUNT(DISTINCT pid) FROM final) AS _players,\n"
            f"       (SELECT COUNT(*) FROM a {pool_where}) AS _pool\nFROM final\n"
            f"ORDER BY {', '.join(order_by)}\nLIMIT %s OFFSET %s")
    if spec.min_games is not None:
        params.append(float(spec.min_games))
    params += [spec.limit, spec.offset]

    sentence = _sentence(ds, spec, lo, hi, filter_words, words)
    return {"ds": ds, "sql": sql, "params": params, "conds": conds, "lo": lo, "hi": hi, "sort": sort,
            "sentence": sentence, "words": words}


def _dim_words(dim, op, value):
    neg = op == "ne"
    if dim.type == "bool":
        on = bool(value) != neg
        return {"home": "home games" if on else "away games", "result": "wins" if on else "losses",
                "b2b": "second nights of back-to-backs" if on else "games that aren't the second night of a back-to-back",
                }.get(dim.key, f"rows where {dim.label.lower()} is {'yes' if on else 'no'}")
    if dim.type == "date":
        if op == "between":
            return f"games from {value[0]} to {value[1]}"
        return f"games dated {OP_WORDS[op]} {value}"
    vals = value if isinstance(value, list) else [value]
    what = {"opponent": "games against", "team": "games for" if dim.key == "team" else "rows for"}.get(dim.key, dim.label)
    return f"{what} {'anyone but ' if neg else ''}{', '.join(str(v) for v in vals)}"


def _sentence(ds, spec, lo, hi, filter_words, words):
    rows = "games" if ds.key == "player_game" else "seasons"
    span = f"in {_season_label(lo)}" if lo == hi else f"from {_season_label(lo)} to {_season_label(hi)}"
    if spec.scope == "season":
        head = f"Players who, {span}" if lo == hi else f"Players who, in a single season {span}"
    else:
        head = f"Players who, over all their {rows} {span} combined"
    if spec.entities != "all":
        head = head.replace("Players who", f"Of the {len(set(spec.entities))} chosen players, those who", 1)
    bits = [head]
    if filter_words:
        bits.append("counting only " + "; ".join(filter_words))
    if spec.min_games:
        bits.append(f"over at least {_num_text(spec.min_games, 'int')} games")
    text = ", ".join(bits) + ", " + "; and ".join(words)
    n_value = sum(1 for c in spec.conditions if c.type == "value")
    if n_value > 1:
        text += f" (all over the same {rows})"
    return text + "."


# ─── the endpoint ───────────────────────────────────────────────────────────

@router.post("/workbench/finder")
def workbench_finder(spec: FinderSpec):
    plan = compile_finder(spec)
    ds = plan["ds"]
    rows = execute_readonly(plan["sql"], plan["params"])
    names = _names() if ds.names_from_ids else None
    matched = players = pool = 0
    cols = _columns_of(plan, spec)    # execute_readonly returns tuples: names in the SELECT's order
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        matched, players, pool = int(d["_matched"]), int(d["_players"]), int(d["_pool"])
        row = {"player_id": d["pid"],
               "player_name": names.get(d["pid"]) if names is not None else d.get("player_name"),
               "team": d["team"], "n_rows": int(d["n_rows"]), "n_games": _out(d["n_games"])}
        if spec.scope == "season":
            row["season"] = d["season"]
        conds = []
        for i, kind, col, stable in plan["conds"]:
            if kind == "value":
                m = d.get(f"m{i}")
                conds.append({"value": _out(d[f"v{i}"]), "n": _out(d[f"n{i}"]),
                              "reliability": _sample(_out(m), stable["stable_n"]) if stable else None})
            elif kind == "count":
                conds.append({"count": int(d[f"k{i}"]), "of": int(d["n_rows"])})
            else:
                conds.append({"streak": int(d[f"s{i}"] or 0), "from": _out(d[f"sf{i}"]), "to": _out(d[f"sl{i}"])})
        row["conditions"] = conds
        results.append(row)
    if not rows and spec.offset == 0:
        # Nothing matched: still say how many players were looked at.
        pool = _pool_only(plan, spec)
    meta = []
    for (i, kind, col, _s), c in zip(plan["conds"], spec.conditions):
        m = {"index": i, "type": kind, "text": plan["words"][i]}
        if kind == "value":
            m["column"] = column_meta(ds, col, c.per, True)
            m["op"], m["value"], m["min_n"] = c.op, c.value, c.min_n
        else:
            m["tests"] = [{**t.model_dump(), "label": tc.label, "format": tc.fmt} for t, tc in zip(c.tests, col)]
            m["count"], m["count_op"] = c.count, (c.count_op if kind == "count" else "gte")
        meta.append(m)
    notes = []
    for _i, kind, col, _s in plan["conds"]:
        for cc in (col if isinstance(col, list) else [col]):
            if cc.first_season > plan["lo"]:
                n = (f"{cc.label} is recorded from {_season_label(cc.first_season)} on; earlier "
                     f"{ds.row_label} count as not recorded (they never meet a condition on it).")
                if n not in notes:
                    notes.append(n)
    if any(c.type == "streak" for c in spec.conditions):
        notes.append(f"Streaks run over the {ds.row_label} that count, in order: a game he sat out has no row "
                     "and doesn't break a run, nor does one the filters leave out"
                     + ("; with 'in a single season' a run ends with the season." if spec.scope == "season"
                        else "; runs carry across seasons."))
    notes += list(ds.notes)
    truncated = matched > spec.offset + len(results)
    return {
        "dataset": {"key": ds.key, "label": ds.label, "row_label": ds.row_label},
        "spec": {**spec.model_dump(), "season_from": plan["lo"], "season_to": plan["hi"],
                 "sort": plan["sort"].model_dump()},
        "sentence": plan["sentence"],
        "conditions": meta,
        "rows": results,
        "n": {"rows": len(results), "matched": matched, "players": players, "pool": pool},
        "truncated": truncated,
        "notes": notes,
        "_source": make_source(list(ds.tables), ds.upstream),
    }


def _columns_of(plan, spec):
    """Names of final.*'s columns, in the order the SQL selects them."""
    ds = plan["ds"]
    cols = ["pid"] + (["season"] if spec.scope == "season" else []) + ["n_rows", "n_games", "team"]
    if not ds.names_from_ids:
        cols.append("player_name")
    for i, kind, _col, stable in plan["conds"]:
        if kind == "value":
            cols += [f"v{i}", f"n{i}"] + ([f"m{i}"] if stable else [])
        elif kind == "count":
            cols.append(f"k{i}")
    for i, kind, _c, _s in plan["conds"]:
        if kind == "streak":
            cols += [f"s{i}", f"sf{i}", f"sl{i}"]
    return cols + ["_matched", "_players", "_pool"]


def _pool_only(plan, spec):
    """How many units the finder looked at, when none matched (the main query
    returns no row to carry it)."""
    sql = plan["sql"]
    head = sql[:sql.index("\nSELECT final.*")]
    params = plan["params"][:-2]
    if spec.min_games is not None:
        params = params[:-1]
        tail = "\nSELECT COUNT(*) FROM a WHERE n_games >= %s"
        params = params + [float(spec.min_games)]
    else:
        tail = "\nSELECT COUNT(*) FROM a"
    return int(execute_readonly(head + tail, params)[0][0])
