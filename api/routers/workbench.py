"""
Workbench query layer (round 7 step 1): a JSON spec in, rows out, built only
from api/workbench_catalogue.py.

    GET  /workbench/catalogue   every dataset, its stats (verified and
                                excluded, with the reason), groupings,
                                filters, season range and row count
    POST /workbench/query       run a spec (below)
    GET  /workbench/entities    players by name (with their NBA ids) or every
                                franchise, for the board's player/team sets
    POST /workbench/context     {spec, column, by?, bins?}: quantiles (and a
                                histogram) of one column over every row a spec
                                matches, no row cap: a chart's grey population
    POST /workbench/trend       {spec, x, y}: straight-line fit over the rows a
                                chart draws; slope clustered by player/team,
                                r's interval a cluster bootstrap
    POST /workbench/aging       {stat, player_ids, era}: the Aging Curves page's
                                typical curve (with its 95% range) and each
                                player's seasons on it, for a chart overlay

Spec:

    {"dataset": "player_season",           # a catalogue dataset
     "entities": "all" | [ids],            # player ids (ints) or team codes
                                           # (a code means its franchise: NJN = BKN)
     "columns": ["pts", "ts_pct"],         # catalogue stats (1-40)
     "season_from": 2016, "season_to": 2026,
     "filters": [{"key": "min", "op": "gte", "value": 20}],
     "group_by": "none" | "entity" | "season" | "team" | [keys] | "all",
     "per": "game" | "total" | "per36" | "per100",
     "having": [{"key": "pts", "op": "gte", "value": 25}],
     "min_games": 20,
     "min_poss": 100,                      # datasets with possessions (lineups, pairs)
     "players": [ids], "players_match": "any" | "all",
                                           # team-entity datasets with player ids in a row
                                           # (lineups, pairs): only rows with these players
     "sort": [{"key": "pts", "dir": "desc"}],
     "limit": 100, "offset": 0}

`filters` apply to single rows before anything is combined, in the row's own
unit (a season row's per-game value, one game's value) or on a dataset field
(team, opponent, home, result, ...). `group_by` combines rows as each column's
catalogue entry says (`kind`); "none" returns the rows themselves and "all"
combines everything into one row. `per` scales counting stats (rates and
totals ignore it, and the response says so). `having` and `min_games` apply
to the combined value in the chosen `per`.

Every row carries its n: `n_rows` (seasons or games combined), `n_games`, and
`n` per column in that column's own unit (games, attempts, possessions,
minutes) counting only the rows that went into its value; player stats with a
Stat Stability estimate also get `reliability` (n / (n + M), "noisy" under
0.5, the Leaderboard's rule). Columns whose source gives an interval for one
row (model ratings, projections, on/off) also return `ci` [low, high] on
ungrouped rows; combined rows get none (an interval doesn't pool by summing).

Safety: the request is a fixed schema (unknown keys are refused), every
dataset/column/field/operator is looked up in the catalogue, values are bound
parameters, and SQL text comes only from the catalogue. Each query runs in a
read-only transaction with a statement_timeout and a row cap.

The season ranges, franchise lists and stability estimates are lru-cached per
process: restart impact_api after rebuilding a source table.
"""

import datetime
import decimal
import functools
import hashlib
import json
import math
import re
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import numpy as np
import psycopg2
import psycopg2.errors
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

import workbench_catalogue as WC
from impact_core import get_db
from season_team import season_team_fixes
from routers.aging import ERA_ORDER as AGING_ERAS, _tables as aging_tables, aging_player
from routers.game_log import _names
from routers.leaderboard import RELIABLE, _sample, stable_samples
from source_badge import make_source
from stats_lib import wls_cluster
from teams_lib import NBA_TO_BREF, franchise_of, lookup_codes

router = APIRouter()

ROW_CAP = 5000
TIMEOUT_MS = 8000
MAX_COLUMNS = 40
MAX_CONDITIONS = 20
MAX_ENTITIES = 500
MAX_SORT = 3
NUM_OPS = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<", "eq": "=", "ne": "<>"}
OPS = {**NUM_OPS, "between": "between", "in": "in"}
DIM_OPS = {"team": ("eq", "ne", "in"), "text": ("eq", "ne", "in"), "bool": ("eq", "ne"),
           "date": ("gte", "gt", "lte", "lt", "eq", "between"), "players": ("any", "all")}
CODE_RE = re.compile(r"^[A-Za-z]{2,4}$")
GROUP_ALIASES = {"none": None, "all": []}


# ─── the request ────────────────────────────────────────────────────────────

class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=40)
    op: str = Field(max_length=10)
    value: Any = None


class SortKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=40)
    dir: Literal["asc", "desc"] = "desc"


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str = Field(max_length=40)
    entities: Literal["all"] | list[StrictInt | StrictStr] = "all"
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    season_from: int | None = None
    season_to: int | None = None
    filters: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    group_by: str | list[str] = "none"
    per: Literal["game", "total", "per36", "per100"] = "game"
    having: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    min_games: float | None = Field(None, ge=0)
    min_poss: float | None = Field(None, ge=0)
    players: list[StrictInt] | None = Field(None, min_length=1, max_length=MAX_ENTITIES)
    players_match: Literal["any", "all"] = "any"
    sort: list[SortKey] = Field(default_factory=list, max_length=MAX_SORT)
    limit: int = Field(100, ge=1, le=ROW_CAP)
    offset: int = Field(0, ge=0, le=1_000_000)


def _bad(msg):
    raise HTTPException(status_code=400, detail=msg)


def _season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


# ─── live facts about the datasets (cached per process) ────────────────────

@lru_cache(maxsize=1)
def _dataset_meta():
    """{dataset: {"from", "to", "rows", "entities"}}; team datasets also list their franchises."""
    out = {}
    with get_db() as conn:
        cur = conn.cursor()
        for ds in WC.DATASETS.values():
            where = f"WHERE {' AND '.join(ds.where)}" if ds.where else ""
            cur.execute(f"SELECT MIN({ds.season_sql}), MAX({ds.season_sql}), COUNT(*) {ds.from_sql} {where}",
                        list(ds.from_params))
            lo, hi, n = cur.fetchone()
            meta = {"from": lo, "to": hi, "rows": n}
            if ds.entity == "team":
                cur.execute(f"SELECT DISTINCT {ds.entity_sql} {ds.from_sql} {where}", list(ds.from_params))
                meta["franchises"] = sorted(r[0] for r in cur.fetchall() if r[0])
            out[ds.key] = meta
        conn.rollback()
    return out


@lru_cache(maxsize=1)
def _stability():
    """(season-table units, play-by-play units): {stat: {stable_n, unit_label}} from stat_stability."""
    with get_db() as conn:
        cur = conn.cursor()
        scaled = stable_samples(cur)
        raw = {}
        if scaled:
            cur.execute("""SELECT stat, stable_n, unit_label FROM stat_stability
                           WHERE variant = 'catalogue' AND stable_n IS NOT NULL""")
            raw = {k: {"stable_n": round(float(m), 1), "unit_label": ul} for k, m, ul in cur.fetchall()}
        conn.rollback()
    return scaled, raw


def _stable(col):
    if not col.stability or col.sample is None:
        return None
    scaled, raw = _stability()
    return (scaled if col.stability_scaled else raw).get(col.stability)


# ─── compiling a spec into SQL ──────────────────────────────────────────────

@dataclass
class Plan:
    ds: Any
    cols: list
    group: list | None          # None = rows as they are
    per: str
    sql: str
    params: list
    select: list                # (alias, role, name, sql); role field | value | n | sample | ci_lo | ci_hi
                                # | n_rows | n_games
    season_from: int
    season_to: int
    notes: list


def _gate(ds, col, expr):
    """A column counts only from its first reliable season on."""
    if expr is None:
        return None
    if col.first_season <= _dataset_meta()[ds.key]["from"]:
        return expr
    return f"(CASE WHEN {ds.season_sql} >= {int(col.first_season)} THEN {expr} END)"


def _applied_per(ds, col, per):
    if col.kind != "count":
        return None
    modes = col.per_modes or ds.per_modes
    return per if per in modes else "game"


def _scale_den(ds, per):
    return {"game": ds.games_sql, "total": ds.games_sql, "per36": ds.minutes_sql, "per100": ds.poss_sql}[per]


def _value_sql(ds, col, per, grouped):
    """The column's value (and the SQL of its n) on one row or combined over a group."""
    k = col.kind
    if k == "count":
        p = _applied_per(ds, col, per)
        tot = _gate(ds, col, col.total)
        den = _scale_den(ds, p)
        mult = {"game": "", "total": "", "per36": "36.0 * ", "per100": "100.0 * "}[p]
        n = f"(CASE WHEN ({tot}) IS NOT NULL AND ({den}) IS NOT NULL THEN {ds.games_sql} END)"
        if not grouped:
            if p == "game":
                return _gate(ds, col, col.sql), n
            if p == "total":
                return tot, n
            return f"({mult}({tot}) / NULLIF({den}, 0))", n
        num = f"SUM(CASE WHEN ({den}) IS NOT NULL THEN {tot} END)"
        if p == "total":
            return num, f"SUM({n})"
        return (f"({mult}{num}::float8 / NULLIF(SUM(CASE WHEN ({tot}) IS NOT NULL THEN {den} END), 0))",
                f"SUM({n})")
    if k in ("sum", "none"):
        v = _gate(ds, col, col.sql)
        n = f"(CASE WHEN ({v}) IS NOT NULL THEN {col.n_sql or 1} END)"
        if not grouped:
            return v, n
        if k == "none":
            _bad(f"{col.label} can't be combined over several rows (the table has one value per "
                 f"{ds.row_label[:-1]} and nothing to pool it by); use group_by \"none\" for it.")
        return f"SUM({v})", f"SUM({n})"
    if k == "ratio":
        num = _gate(ds, col, col.num)
        n_expr = col.n_sql or col.den
        n = f"(CASE WHEN ({num}) IS NOT NULL AND ({col.den}) IS NOT NULL THEN {n_expr} END)"
        if not grouped:
            return _gate(ds, col, col.sql), n
        return (f"(SUM(CASE WHEN ({col.den}) IS NOT NULL THEN {num} END)::float8 / "
                f"NULLIF(SUM(CASE WHEN ({num}) IS NOT NULL THEN {col.den} END)::float8, 0))", f"SUM({n})")
    if k == "diff":
        # Two pooled rates, one minus the other (on-court minus off-court net rating).
        num, num2 = _gate(ds, col, col.num), _gate(ds, col, col.num2)
        ok = f"({num}) IS NOT NULL AND ({col.den}) IS NOT NULL AND ({num2}) IS NOT NULL AND ({col.den2}) IS NOT NULL"
        n = f"(CASE WHEN {ok} THEN {col.n_sql or col.den} END)"
        if not grouped:
            return _gate(ds, col, col.sql), n
        rate = lambda a, b: (f"(SUM(CASE WHEN {ok} THEN {a} END)::float8 / "  # noqa: E731
                             f"NULLIF(SUM(CASE WHEN {ok} THEN {b} END)::float8, 0))")
        return f"({rate(num, col.den)} - {rate(num2, col.den2)})", f"SUM({n})"
    if k == "wmean":
        v = _gate(ds, col, col.sql)
        n = f"(CASE WHEN ({v}) IS NOT NULL THEN {col.weight} END)"
        if not grouped:
            return v, n
        return (f"(SUM(({v}) * ({col.weight}))::float8 / NULLIF(SUM({n})::float8, 0))", f"SUM({n})")
    raise AssertionError(k)


def _column(ds, key, what="Column"):
    col = ds.columns.get(key)
    if col is None:
        _bad(f"{what} '{key}' isn't in the {ds.key} catalogue. See GET /workbench/catalogue.")
    if col.status != "verified":
        _bad(f"{col.label} isn't offered: {col.reason}")
    return col


def _number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _bad(f"{where} needs a number, not {value!r}.")
    return float(value)


def _team_codes(value, where):
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > MAX_ENTITIES:
        _bad(f"{where} needs 1 to {MAX_ENTITIES} team codes.")
    codes = set()
    for v in values:
        if not isinstance(v, str) or not CODE_RE.match(v):
            _bad(f"{where}: {v!r} isn't a team code (2-4 letters, e.g. BOS).")
        codes |= lookup_codes(v)
    return sorted(codes)


def _dim_condition(ds, dim, op, value, params):
    where = f"Filter on {dim.key}"
    if op not in DIM_OPS[dim.type]:
        _bad(f"{where}: op must be one of {', '.join(DIM_OPS[dim.type])}.")
    if dim.type == "team":
        params.append(_team_codes(value, where))
        return f"{'NOT ' if op == 'ne' else ''}({dim.sql} = ANY(%s))"
    if dim.type == "text":
        values = value if isinstance(value, list) else [value]
        if not values or any(v not in dim.values for v in values):
            _bad(f"{where}: values must be among {', '.join(dim.values)}.")
        params.append(list(values))
        return f"{'NOT ' if op == 'ne' else ''}({dim.sql} = ANY(%s))"
    if dim.type == "bool":
        if not isinstance(value, bool):
            _bad(f"{where} needs true or false.")
        params.append(value)
        return f"({dim.sql} {NUM_OPS[op]} %s)"
    if dim.type == "players":
        params.append(_player_ids(value, where))
        return f"({dim.sql} {'&&' if op == 'any' else '@>'} %s::int[])"
    if dim.type == "date":
        def as_date(v):
            try:
                return datetime.date.fromisoformat(v)
            except (TypeError, ValueError):
                _bad(f"{where}: {v!r} isn't a date (YYYY-MM-DD).")
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                _bad(f"{where}: between needs [from, to].")
            params.extend([as_date(value[0]), as_date(value[1])])
            return f"({dim.sql} BETWEEN %s AND %s)"
        params.append(as_date(value))
        return f"({dim.sql} {NUM_OPS[op]} %s)"
    raise AssertionError(dim.type)


def _player_ids(value, where):
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > MAX_ENTITIES or any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        _bad(f"{where} needs 1 to {MAX_ENTITIES} NBA player ids (whole numbers).")
    if any(not 0 < v < 2 ** 31 for v in values):
        _bad(f"{where}: player ids are positive whole numbers.")
    return sorted(set(values))


def _num_condition(expr, op, value, where, params):
    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            _bad(f"{where}: between needs [low, high].")
        lo, hi = (_number(v, where) for v in value)
        params.extend([lo, hi])
        return f"({expr} BETWEEN %s AND %s)"
    if op not in NUM_OPS:
        _bad(f"{where}: op must be one of {', '.join(list(NUM_OPS) + ['between'])}.")
    params.append(_number(value, where))
    return f"({expr} {NUM_OPS[op]} %s)"


def _entities(ds, entities, params):
    if entities == "all":
        return None
    if not entities or len(entities) > MAX_ENTITIES:
        _bad(f"entities needs 1 to {MAX_ENTITIES} ids, or \"all\".")
    if ds.entity == "player":
        if any(not isinstance(e, int) for e in entities):
            _bad("Player entities are NBA player ids (whole numbers).")
        params.append(sorted(set(entities)))
    else:
        known = set(_dataset_meta()[ds.key]["franchises"])
        keys = set()
        for e in entities:
            if not isinstance(e, str) or not CODE_RE.match(e):
                _bad(f"{e!r} isn't a team code (2-4 letters, e.g. BOS).")
            code = e.upper()
            fr = franchise_of(NBA_TO_BREF.get(code, code))
            if fr not in known:
                _bad(f"No {ds.label.lower()} on file for team '{e}'.")
            keys.add(fr)
        params.append(sorted(keys))
    return f"({ds.entity_sql} = ANY(%s))"


def _group_keys(ds, group_by):
    if isinstance(group_by, str):
        if group_by in GROUP_ALIASES:
            return GROUP_ALIASES[group_by]
        group_by = [group_by]
    if len(group_by) > len(ds.groupings):
        _bad("Too many group_by keys.")
    out = []
    for g in group_by:
        if g not in ds.groupings:
            _bad(f"Can't group {ds.key} by '{g}'. Use none, all, or {', '.join(ds.groupings)}.")
        if g not in out:
            out.append(g)
    return out


def compile_query(spec: QuerySpec) -> Plan:
    ds = WC.DATASETS.get(spec.dataset)
    if ds is None:
        _bad(f"Unknown dataset '{spec.dataset}'. Use one of {', '.join(WC.DATASETS)}.")
    meta = _dataset_meta()[ds.key]
    keys = list(dict.fromkeys(spec.columns))
    cols = [_column(ds, k) for k in keys]
    group = _group_keys(ds, spec.group_by)
    grouped = group is not None
    per = spec.per
    if per not in ds.per_modes:
        _bad(f"per '{per}' isn't available for {ds.label.lower()}: use {', '.join(ds.per_modes)}.")

    if spec.season_from is not None and spec.season_to is not None and spec.season_from > spec.season_to:
        _bad("season_from is after season_to.")
    lo = meta["from"] if spec.season_from is None else max(spec.season_from, meta["from"])
    hi = meta["to"] if spec.season_to is None else min(spec.season_to, meta["to"])
    if lo > hi:
        _bad(f"{ds.label} cover {_season_label(meta['from'])} to {_season_label(meta['to'])}.")

    params = list(ds.from_params)
    where = list(ds.where) + [f"{ds.season_sql} BETWEEN %s AND %s"]
    params += [lo, hi]
    ent = _entities(ds, spec.entities, params)
    if ent:
        where.append(ent)
    if spec.players is not None:
        if not ds.players_sql:
            _bad(f"players picks rows by the players in them; {ds.label.lower()} have one player a row "
                 f"(use entities) or none.")
        params.append(_player_ids(spec.players, "players"))
        where.append(f"({ds.players_sql} {'&&' if spec.players_match == 'any' else '@>'} %s::int[])")
    for c in spec.filters:
        if c.key in ds.dims:
            where.append(_dim_condition(ds, ds.dims[c.key], c.op, c.value, params))
        else:
            col = _column(ds, c.key, "Filter")
            where.append(_num_condition(_gate(ds, col, col.sql), c.op, c.value, f"Filter on {c.key}", params))

    # SELECT: the fields that name each row, the counts, every column's value, n and sample.
    select, by = [], []
    def add(sql, role, name):
        select.append((f"c{len(select)}", role, name, sql))
    if grouped:
        for g in group:
            gp = ds.groupings[g]
            for sql, name in zip(gp.by, gp.fields):
                add(sql, "field", name)
                by.append(sql)
            for name, sql in gp.extra:
                add(sql, "field", name)
        add("COUNT(*)", "n_rows", None)
        add(f"SUM({ds.games_sql})", "n_games", None)
    else:
        for name, sql in ds.row_fields:
            add(sql, "field", name)
        add("1", "n_rows", None)
        add(ds.games_sql, "n_games", None)
    values = {}
    for col in cols:
        v, n = _value_sql(ds, col, per, grouped)
        values[col.key] = v
        add(v, "value", col.key)
        add(n, "n", col.key)
        if _stable(col):
            s = f"(CASE WHEN ({_gate(ds, col, col.sql)}) IS NOT NULL THEN {col.sample} END)"
            add(f"SUM({s})" if grouped else s, "sample", col.key)
        if col.ci_lo and not grouped:
            add(_gate(ds, col, col.ci_lo), "ci_lo", col.key)
            add(_gate(ds, col, col.ci_hi), "ci_hi", col.key)

    having, having_params = [], []
    for c in spec.having:
        col = _column(ds, c.key, "having")
        v = values.get(c.key) or _value_sql(ds, col, per, grouped)[0]
        having.append(_num_condition(v, c.op, c.value, f"having on {c.key}", having_params))
    if spec.min_games is not None:
        games = f"SUM({ds.games_sql})" if grouped else ds.games_sql
        having.append(f"({games} >= %s)")
        having_params.append(float(spec.min_games))
    if spec.min_poss is not None:
        if not ds.poss_sql:
            _bad(f"{ds.label} have no possessions to set a floor on.")
        poss = f"SUM({ds.poss_sql})" if grouped else ds.poss_sql
        having.append(f"(COALESCE({poss}, 0) >= %s)")
        having_params.append(float(spec.min_poss))

    # ORDER BY: the requested keys, then the row's own fields so ties come back in one order.
    sortable = {name: sql for alias, role, name, sql in select if role in ("field", "value")}
    sortable.update({"n_rows": next(s for _, r, _, s in select if r == "n_rows"),
                     "n_games": next(s for _, r, _, s in select if r == "n_games")})
    order = []
    sort = spec.sort or [SortKey(key=cols[0].key, dir="asc" if cols[0].higher_is_better is False else "desc")]
    for s in sort:
        if s.key not in sortable or (ds.names_from_ids and s.key == "player_name"):
            _bad(f"Can't sort by '{s.key}': sort by a chosen column, a row field or n_rows / n_games.")
        order.append(f"{sortable[s.key]} {s.dir.upper()} NULLS LAST")
    order += [f"{sql} ASC" for sql in (by if grouped else [s for _, s in ds.row_fields])]

    total = ["COUNT(*) OVER ()", "SUM(COUNT(*)) OVER ()" if grouped else "COUNT(*) OVER ()"]
    sql = (f"SELECT {', '.join(f'{s} AS {a}' for a, _, _, s in select)}, "
           f"{total[0]} AS _matched, {total[1]} AS _source_rows\n"
           f"{ds.from_sql}\nWHERE {' AND '.join(where)}\n")
    if grouped:
        if by:
            sql += f"GROUP BY {', '.join(by)}\n"
        if having:
            sql += f"HAVING {' AND '.join(having)}\n"
    elif having:
        sql = sql.rstrip("\n") + f" AND {' AND '.join(having)}\n"
    sql += f"ORDER BY {', '.join(order)}\nLIMIT %s OFFSET %s"
    params += having_params + [spec.limit, spec.offset]

    notes = []
    for col in cols:
        if col.first_season > lo:
            notes.append(f"{col.label} is recorded from {_season_label(col.first_season)} on; earlier "
                         f"{ds.row_label} count as not recorded.")
    ignored = [c.label for c in cols if c.kind == "count" and _applied_per(ds, c, per) != per]
    unscaled = [c.label for c in cols if c.kind != "count"]
    if per != "game" and (ignored or unscaled):
        notes.append(f"'{WC.PER_MODES[per]}' applies to counting stats only: "
                     + "; ".join(x for x in (
                         f"{', '.join(ignored)} shown per game" if ignored else "",
                         f"{', '.join(unscaled)} are rates or totals" if unscaled else "") if x) + ".")
    if spec.players is not None:
        notes.insert(0, f"Only {ds.label.lower()} with {'any' if spec.players_match == 'any' else 'all'} of the "
                        f"{len(set(spec.players))} chosen players.")
    notes += list(ds.notes)
    return Plan(ds, cols, group, per, sql, params, select, lo, hi, notes)


# ─── running it ─────────────────────────────────────────────────────────────

def execute_readonly(sql, params, timeout_ms=TIMEOUT_MS):
    """Run one statement in a read-only transaction with a statement timeout;
    the transaction always ends here, so nothing leaks into the pooled connection."""
    with get_db() as conn:
        conn.rollback()
        cur = conn.cursor()
        try:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
            cur.execute(sql, params)
            return cur.fetchall()
        except psycopg2.errors.QueryCanceled:
            raise HTTPException(status_code=504, detail=(
                f"The query took longer than {timeout_ms / 1000:g} s and was stopped. "
                "Narrow the seasons, the players or the columns."))
        finally:
            conn.rollback()


def _out(v):
    if isinstance(v, decimal.Decimal):
        v = float(v)
    if isinstance(v, float):
        return None if not math.isfinite(v) else round(v, 4)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def _fmt(ds, col, per, grouped):
    """Format of the value as returned: averages and scaled counts get a decimal, totals don't."""
    p = _applied_per(ds, col, per)
    if col.kind == "count" and p == "total":
        return "signed1" if col.fmt.startswith("signed") else "int"
    if col.kind == "count" and p in ("per36", "per100"):
        return col.agg_fmt or "num1"
    if grouped and col.kind in ("count", "wmean"):
        return col.agg_fmt or ("num1" if col.fmt == "int" else col.fmt)
    return col.fmt


def column_meta(ds, col, per=None, grouped=False):
    stable = _stable(col)
    out = {
        "key": col.key, "label": col.label, "short": col.short, "group": col.group,
        "format": col.fmt if per is None else _fmt(ds, col, per, grouped),
        "kind": col.kind, "combines": WC.agg_label(col), "first_season": col.first_season,
        "higher_is_better": col.higher_is_better, "n_unit": col.n_unit or ds.row_label,
        "per_modes": list(col.per_modes or ds.per_modes) if col.kind == "count" else [],
        "attempts": col.attempts, "min_attempts_per_game": WC.MIN_ATTEMPTS_PER_GAME.get(col.attempts),
        "reliability": ({"stable_n": stable["stable_n"], "unit_label": stable["unit_label"],
                         "reliable_at": RELIABLE} if stable else None),
        "status": col.status, "reason": col.reason, "note": col.note, "sources": list(col.sources),
        "interval": col.ci_label, "method": col.method,
    }
    if per is not None:
        p = _applied_per(ds, col, per)
        out["per"] = p and WC.PER_MODES[p]
    return out


def _run(spec, limit=None):
    """Compile and run a spec; returns (plan, rows as dicts, matched, source_rows).
    `limit` overrides spec.limit for the internal summaries below, which read
    every matched row but return only a few numbers."""
    plan = compile_query(spec if limit is None else spec.model_copy(update={"limit": limit, "offset": 0}))
    ds = plan.ds
    rows = execute_readonly(plan.sql, plan.params)
    names = _names() if ds.names_from_ids or ds.name_fields else None
    stables = {c.key: _stable(c) for c in plan.cols}
    out = []
    for r in rows:
        row, n = {}, {}
        for (alias, role, name, _sql), v in zip(plan.select, r):
            v = _out(v)
            if role in ("field", "value"):
                row[name] = v
            elif role == "n":
                n[name] = v
            elif role == "n_rows":
                row["n_rows"] = v
            elif role == "n_games":
                row["n_games"] = v
            elif role == "sample":
                row.setdefault("reliability", {})[name] = _sample(v, stables[name]["stable_n"])
            elif role in ("ci_lo", "ci_hi"):
                pair = row.setdefault("ci", {}).setdefault(name, [None, None])
                pair[0 if role == "ci_lo" else 1] = v
        if names is not None and "player_id" in row:
            row["player_name"] = names.get(row["player_id"])
        for id_field, name_field in ds.name_fields:
            if id_field in row:
                ids = row[id_field]
                lookup = names if names is not None else _names()
                row[name_field] = ([lookup.get(i) for i in ids] if isinstance(ids, list)
                                   else lookup.get(ids))
        row["n"] = n
        out.append(row)
    matched = int(rows[0][-2]) if rows else 0
    source_rows = int(rows[0][-1]) if rows else 0
    return plan, out, matched, source_rows


@router.post("/workbench/query")
def workbench_query(spec: QuerySpec):
    plan, out, matched, source_rows = _run(spec)
    ds = plan.ds
    grouped = plan.group is not None
    names = ds.names_from_ids
    notes = list(plan.notes)
    if matched > spec.offset + len(out):
        notes.insert(0, f"Showing {spec.offset + 1:,}-{spec.offset + len(out):,} of {matched:,}; raise the limit "
                        f"(up to {ROW_CAP:,}) or narrow the query.")
    fields = []
    for _a, role, name, _s in plan.select:
        if role == "field" and name not in fields:
            fields.append(name)
    if names and "player_id" in fields:
        fields.insert(fields.index("player_id") + 1, "player_name")
    for id_field, name_field in ds.name_fields:
        if id_field in fields:
            fields.insert(fields.index(id_field) + 1, name_field)
    return {
        "dataset": {"key": ds.key, "label": ds.label, "entity": ds.entity, "row_label": ds.row_label},
        "spec": {**spec.model_dump(), "group_by": plan.group if grouped else "none",
                 "season_from": plan.season_from, "season_to": plan.season_to},
        "fields": fields,
        "columns": [column_meta(ds, c, plan.per, grouped) for c in plan.cols],
        "rows": out,
        "n": {"rows": len(out), "matched": matched, "source_rows": source_rows},
        "truncated": matched > spec.offset + len(out),
        "notes": notes,
        "_source": make_source(*WC.source_tables(ds, plan.cols)),
    }


# ─── response cache ─────────────────────────────────────────────────────────
# The summaries below (context, trend, aging) and the Player Finder read many
# rows and return few, so the same request (a starter board every visitor
# opens, a chart re-drawn after a move) is answered from memory the second
# time. Safe for the same reason as the catalogue's lru_caches: the tables
# change only on a rebuild, after which impact_api is restarted (README). The
# key is the validated request, so key order or defaults filled in by the
# schema don't make two copies. Errors are never kept; /workbench/query isn't
# cached (its responses can hold 5,000 rows, and it runs in milliseconds).

RESPONSE_CACHE_SIZE = 256
_responses = OrderedDict()
_responses_lock = threading.Lock()


def response_cache(kind, keep=lambda out: True):
    """Decorator for a POST endpoint taking one request model."""
    def wrap(fn):
        @functools.wraps(fn)
        def endpoint(*args, **kwargs):
            req = (args or tuple(kwargs.values()))[0]
            key = (kind, req.model_dump_json())
            with _responses_lock:
                if key in _responses:
                    _responses.move_to_end(key)
                    return _responses[key]
            out = fn(*args, **kwargs)
            if keep(out):
                with _responses_lock:
                    _responses[key] = out
                    while len(_responses) > RESPONSE_CACHE_SIZE:
                        _responses.popitem(last=False)
            return out
        return endpoint
    return wrap


# ─── chart support: the population behind a chart, and its trend line ──────
# Charts (round 7 step 4) draw a set's rows from /workbench/query. These two
# read every matching row of the same spec and return only a few numbers, so
# they aren't held to the 5,000-row cap of a query that returns rows; they
# keep its timeout and read-only transaction.

CONTEXT_CAP = 200_000
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: QuerySpec
    column: str = Field(max_length=40)
    by: str | None = Field(None, max_length=40)
    bins: int | None = Field(None, ge=1, le=100)


class TrendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: QuerySpec
    x: str = Field(max_length=40)
    y: str = Field(max_length=40)


def _finite(v):
    return v is not None and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _r6(v):
    return None if v is None else float(f"{float(v):.6g}")


def _nice_step(span, bins):
    raw = span / max(1, bins)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def _histogram(vals, bins):
    lo, hi = float(vals.min()), float(vals.max())
    if hi <= lo:
        return {"edges": [_r6(lo), _r6(lo)], "counts": [int(len(vals))]}
    step = _nice_step(hi - lo, bins)
    start = math.floor(lo / step) * step
    k = max(1, math.ceil((hi - start) / step - 1e-9))
    edges = np.array([start + i * step for i in range(k + 1)])
    if edges[-1] < hi:
        edges = np.append(edges, edges[-1] + step)
    counts, _ = np.histogram(vals, edges)
    return {"edges": [_r6(e) for e in edges], "counts": [int(c) for c in counts]}


def _summary(vals):
    q = np.quantile(vals, QUANTILES)
    return {"n": int(len(vals)), "mean": _r6(vals.mean()),
            **{f"p{int(round(p * 100))}": _r6(v) for p, v in zip(QUANTILES, q)}}


@router.post("/workbench/context")
@response_cache("context")
def workbench_context(req: ContextRequest):
    """Quantiles (10/25/50/75/90th) of one column over every row the spec
    matches, overall and per value of `by` (a field of the rows, e.g. season),
    plus a histogram on round bin edges when `bins` is given. Each row counts
    once; rows with no value are counted and left out."""
    spec = req.spec
    if req.column not in spec.columns:
        _bad(f"'{req.column}' isn't one of the spec's columns.")
    plan, rows, matched, _src = _run(spec, limit=CONTEXT_CAP)
    if matched > CONTEXT_CAP:
        _bad(f"{matched:,} rows match; summaries read at most {CONTEXT_CAP:,}. Narrow the seasons or add a games floor.")
    fields = [name for _a, role, name, _s in plan.select if role == "field"]
    if req.by is not None and req.by not in fields:
        _bad(f"Can't split by '{req.by}': use one of the rows' fields ({', '.join(fields) or 'none'}).")
    col = next(c for c in plan.cols if c.key == req.column)
    vals, groups, missing = [], {}, 0
    for r in rows:
        v = r.get(req.column)
        if not _finite(v):
            missing += 1
            continue
        vals.append(v)
        if req.by is not None:
            groups.setdefault(r.get(req.by), []).append(v)
    arr = np.array(vals, float)
    grouped = plan.group is not None
    return {
        "column": column_meta(plan.ds, col, plan.per, grouped),
        "by": req.by,
        "n_rows": matched,
        "n_missing": missing,
        "overall": _summary(arr) if len(arr) else None,
        "groups": [{"key": k, **_summary(np.array(g, float))}
                   for k, g in sorted(groups.items(), key=lambda kv: (kv[0] is None, kv[0]))],
        "histogram": _histogram(arr, req.bins) if req.bins and len(arr) else None,
        "spec": {**spec.model_dump(), "group_by": plan.group if grouped else "none",
                 "season_from": plan.season_from, "season_to": plan.season_to},
        "_source": make_source(*WC.source_tables(plan.ds, plan.cols)),
    }


BOOT_RESAMPLES = 2000


def _cluster_boot_r(x, y, groups, seed, resamples=BOOT_RESAMPLES):
    """95% percentile interval of Pearson's r from resampling whole clusters
    (players or teams) with replacement. Each resample's r comes from summed
    per-cluster moments, so it costs a matrix product, not a refit."""
    _, inv = np.unique(groups, return_inverse=True)
    k = int(inv.max()) + 1
    xc, yc = x - x.mean(), y - y.mean()
    S = np.zeros((k, 6))
    np.add.at(S, inv, np.column_stack([np.ones(len(x)), xc, yc, xc * xc, yc * yc, xc * yc]))
    rng = np.random.default_rng(seed)
    rs = []
    for start in range(0, resamples, 250):
        b = min(250, resamples - start)
        counts = np.stack([np.bincount(rng.integers(0, k, k), minlength=k) for _ in range(b)])
        n, sx, sy, sxx, syy, sxy = (counts @ S).T
        vx, vy = sxx - sx * sx / n, syy - sy * sy / n
        with np.errstate(invalid="ignore", divide="ignore"):
            rs.append((sxy - sx * sy / n) / np.sqrt(vx * vy))
    rs = np.concatenate(rs)
    rs = rs[np.isfinite(rs)]
    return np.percentile(rs, [2.5, 97.5]), int(len(rs))


@router.post("/workbench/trend")
@response_cache("trend")
def workbench_trend(req: TrendRequest):
    """Straight-line fit of y on x over exactly the rows the spec returns (the
    points a chart draws). The slope, its interval and the line's 95% band
    use errors clustered by player or team, so a player's repeated seasons
    don't count as independent evidence (stats_lib.wls_cluster: CR1, t on
    clusters − 1). r is Pearson's; its interval resamples whole players or
    teams 2,000 times (cluster bootstrap, percentile; the seed is fixed by the
    request, so the same chart always shows the same interval). A sandwich
    interval on the standardised slope was tried first and dropped: it treats
    both SDs as known and ran 2-3 times too wide for strong relationships
    (minutes vs points 2025-26: ±0.05 against the bootstrap's ±0.016)."""
    spec = req.spec
    for k in (req.x, req.y):
        if k not in spec.columns:
            _bad(f"'{k}' isn't one of the spec's columns.")
    if req.x == req.y:
        _bad("x and y are the same column.")
    plan, rows, matched, _src = _run(spec)
    if matched > spec.offset + len(rows) or spec.offset:
        _bad(f"{matched:,} rows match but the fit uses every row a chart draws, and a chart draws at most "
             f"{ROW_CAP:,} from the start. Narrow the query.")
    fields = [name for _a, role, name, _s in plan.select if role == "field"]
    key = "player_id" if "player_id" in fields else "franchise" if "franchise" in fields else None
    cluster_by = {"player_id": "player", "franchise": "team"}.get(key, "row")
    xs, ys, gs = [], [], []
    for i, r in enumerate(rows):
        x, y = r.get(req.x), r.get(req.y)
        if _finite(x) and _finite(y):
            xs.append(float(x)); ys.append(float(y)); gs.append(r[key] if key else i)
    x, y, g = np.array(xs), np.array(ys), np.array(gs, dtype=object).astype(str)
    grouped = plan.group is not None
    out = {
        "x": column_meta(plan.ds, next(c for c in plan.cols if c.key == req.x), plan.per, grouped),
        "y": column_meta(plan.ds, next(c for c in plan.cols if c.key == req.y), plan.per, grouped),
        "n": int(len(x)), "n_dropped": len(rows) - int(len(x)), "cluster_by": cluster_by,
        "n_clusters": int(len(set(gs))), "fit": None, "reason": None,
        "method": ("Least squares, every point weighted equally. Slope and band: errors clustered by "
                   f"{cluster_by} (CR1, t with clusters − 1 degrees of freedom). r: 95% interval from "
                   f"{BOOT_RESAMPLES:,} resamples of whole {cluster_by}s."),
        "_source": make_source(*WC.source_tables(plan.ds, plan.cols)),
    }
    if out["n"] < 3 or out["n_clusters"] < 3:
        out["reason"] = f"A fit needs at least 3 points from 3 different {cluster_by}s."
        return out
    if x.std() == 0 or y.std() == 0:
        out["reason"] = "One of the two stats has the same value on every point."
        return out
    ones = np.ones(len(x))
    fit = wls_cluster(y, np.column_stack([ones, x]), ones, g)
    cov, t = fit["cov"], fit["tcrit"]
    grid = np.linspace(x.min(), x.max(), 41)
    se = np.sqrt(np.maximum(0, cov[0, 0] + 2 * grid * cov[0, 1] + grid ** 2 * cov[1, 1]))
    yhat = fit["beta"][0] + fit["beta"][1] * grid
    r = float(np.corrcoef(x, y)[0, 1])
    seed = int(hashlib.md5(json.dumps([req.spec.model_dump(), req.x, req.y], sort_keys=True, default=str)
                           .encode()).hexdigest()[:8], 16)
    (lo, hi), kept = _cluster_boot_r(x, y, g, seed)
    out["fit"] = {
        "r": _r6(r),
        "r_ci": [_r6(lo), _r6(hi)],
        "r_resamples": kept,
        "p": _r6(fit["p"][1]),
        "slope": _r6(fit["beta"][1]), "slope_ci": [_r6(fit["ci_low"][1]), _r6(fit["ci_high"][1])],
        "intercept": _r6(fit["beta"][0]),
        "line": [{"x": _r6(a), "y": _r6(b), "lo": _r6(b - t * s), "hi": _r6(b + t * s)} for a, b, s in zip(grid, yhat, se)],
    }
    return out


# ─── players and teams for the board's sets ─────────────────────────────────

ENTITY_LIMIT = 25
# Accented letters in player names and their plain lower-case letters, for the name search.
# Both cases are listed: lower() only folds ASCII under the database's C locale.
_LOWER = "áàâäãåāăąçćčďéèêëēėęěğģíìîïīįıķľłńňņñóòôöõøōőřšśşșťțúùûüūůűųýÿžźżđ"
_ACCENTED = _LOWER + "".join(dict.fromkeys(c.upper() for c in _LOWER if len(c.upper()) == 1 and not c.upper().isascii())) + "İ"
_PLAIN = "".join({"ø": "o", "ł": "l", "đ": "d", "ı": "i", "İ": "i"}.get(c) or {"ø": "o", "ł": "l", "đ": "d"}.get(c.lower()) or unicodedata.normalize("NFKD", c.lower())[0]
                 for c in _ACCENTED)


def _fold(text):
    return text.translate(str.maketrans(_ACCENTED, _PLAIN)).lower()


@lru_cache(maxsize=1)
def _franchises():
    """Every franchise in team_seasons: its latest name and code, and its season span."""
    rows = execute_readonly("""
        SELECT franchise, (array_agg(team_name ORDER BY season DESC))[1],
               (array_agg(abbreviation ORDER BY season DESC))[1], MIN(season), MAX(season)
        FROM team_seasons WHERE NOT is_league_avg AND franchise IS NOT NULL
        GROUP BY franchise ORDER BY MAX(season) DESC, franchise""", [])
    return [{"id": fr, "name": name, "team": code, "from": lo, "to": hi} for fr, name, code, lo, hi in rows]


@router.get("/workbench/entities")
def workbench_entities(kind: Literal["player", "team"], q: str = "", ids: str = "", limit: int = ENTITY_LIMIT):
    """Players matching `q` (or the comma-separated NBA ids in `ids`) with their
    span and latest team, or every franchise (today's code, latest name)."""
    limit = max(1, min(int(limit), ENTITY_LIMIT))
    source = make_source(["player_season_stats"] if kind == "player" else ["team_seasons"],
                         "nba_api (stats.nba.com) from 2009-10 + Basketball-Reference")
    if kind == "team":
        return {"kind": kind, "results": _franchises(), "_source": source}
    # Text only: LIKE's wildcards and the escape character are taken out, so a
    # query matches its letters anywhere in the name and nothing else.
    text = _fold(q.strip()).replace("\\", "").replace("%", "").replace("_", "")
    if ids:
        try:
            wanted = sorted({int(x) for x in ids.split(",") if x.strip()})[:MAX_ENTITIES]
        except ValueError:
            _bad("ids must be comma-separated NBA player ids.")
        where, params = "s.player_id = ANY(%s)", [wanted]
    elif len(text) >= 2:
        # Accents ignored on both sides, so "jokic" finds Nikola Jokić.
        where, params = "translate(lower(s.player_name), %s, %s) LIKE %s", [_ACCENTED, _PLAIN, f"%{text}%"]
    else:
        return {"kind": kind, "results": [], "_source": source}
    rows = execute_readonly(f"""
        SELECT s.player_id, (array_agg(s.player_name ORDER BY s.season DESC))[1],
               MIN(s.season), MAX(s.season), (array_agg(s.team_abbreviation ORDER BY s.season DESC))[1]
        FROM player_season_stats s WHERE {where} AND s.player_id > 0
        GROUP BY s.player_id
        ORDER BY MAX(s.season) DESC, SUM(s.gp * s.min) DESC NULLS LAST, s.player_id
        LIMIT %s""", params + [len(wanted) if ids else limit])
    # The latest season's team as the pages show it (season_team.py: not a team he never played for).
    with get_db() as conn:
        fixes = season_team_fixes(conn.cursor())
    return {"kind": kind, "results": [{"id": pid, "name": name, "from": lo, "to": hi,
                                       "team": fixes.get((pid, hi), (None, team))[1]}
                                      for pid, name, lo, hi, team in rows], "_source": source}


@router.get("/workbench/catalogue")
def workbench_catalogue():
    meta = _dataset_meta()
    datasets = []
    for ds in WC.DATASETS.values():
        m = meta[ds.key]
        datasets.append({
            "key": ds.key, "label": ds.label, "entity": ds.entity, "description": ds.description,
            "seasons": {"from": m["from"], "to": m["to"]}, "rows": m["rows"], "row_label": ds.row_label,
            "per_modes": [{"key": p, "label": WC.PER_MODES[p]} for p in ds.per_modes],
            "group_by": ["none", "all", *ds.groupings],
            "fields": {"row": [f for f, _ in ds.row_fields],
                       "group": {g: [*gp.fields, *(f for f, _ in gp.extra)] for g, gp in ds.groupings.items()}},
            "dims": [{"key": d.key, "label": d.label, "type": d.type, "ops": list(DIM_OPS[d.type]),
                      "groupable": d.key in ds.groupings, "values": list(d.values) or None, "note": d.note}
                     for d in ds.dims.values()],
            "teams": m.get("franchises"),
            "players_filter": bool(ds.players_sql),
            "poss_floor": ds.poss_floor,
            "name_fields": [{"ids": i, "names": n} for i, n in ds.name_fields],
            "columns": [column_meta(ds, c) for c in ds.columns.values()],
            "notes": list(ds.notes),
            "_source": make_source(list(ds.tables) + list(ds.optional[0] if ds.optional else ()),
                                   ds.upstream + (f"; {ds.optional[1]}" if ds.optional else "")),
        })
    return {
        "datasets": datasets,
        "ops": {"numbers": [*NUM_OPS, "between"], "fields": DIM_OPS},
        "limits": {"row_cap": ROW_CAP, "timeout_seconds": TIMEOUT_MS / 1000, "max_columns": MAX_COLUMNS,
                   "max_conditions": MAX_CONDITIONS, "max_entities": MAX_ENTITIES, "max_sort": MAX_SORT},
        "formats": list(WC.FORMATS),
        "reliable_at": RELIABLE,
        "aging": _aging_meta(),
        "_source": make_source(sorted({t for d in WC.DATASETS.values() for t in d.tables} | {"stat_stability"}),
                               "nba_api (stats.nba.com), Basketball-Reference, ESPN play-by-play and scoreboard"),
    }


# ─── aging curve overlay (round 7 step 7) ──────────────────────────────────
# A chart of set members' seasons by age against the Aging Curves page's
# typical curve. Reuses routers/aging.py: the curves it reads and its
# per-player route, so the overlay is the page's own numbers.

MAX_AGING_PLAYERS = 12


class AgingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stat: str = Field(max_length=40)
    player_ids: list[StrictInt] = Field(min_length=1, max_length=MAX_AGING_PLAYERS)
    era: str = Field("all", max_length=20)


def _aging_meta():
    summary = aging_tables()[0]
    stats = {}
    for (stat, era), s in summary.items():
        e = stats.setdefault(stat, {"key": stat, "label": s["label"], "eras": []})
        e["eras"].append(era)
    eras = {era: s["era_label"] for (_st, era), s in summary.items()}
    return {"dataset": "player_season",
            "stats": [stats[k] for k in sorted(stats)],
            "eras": [{"key": e, "label": eras[e]} for e in AGING_ERAS if e in eras]}


@router.post("/workbench/aging")
@response_cache("aging")
def workbench_aging(req: AgingRequest):
    """The typical aging curve of one stat (level and 95% range by age, against
    that season's league average) and each player's seasons on the same scale,
    with the curve moved to his level (routers/aging.py's /aging/player)."""
    summary, curves = aging_tables()
    if req.era not in AGING_ERAS:
        _bad(f"era must be one of {', '.join(AGING_ERAS)}.")
    if (req.stat, req.era) not in summary:
        have = sorted({k[0] for k in summary})
        _bad(f"No aging curve for '{req.stat}' in that era. Curves exist for: {', '.join(have)}.")
    s = summary[(req.stat, req.era)]
    ref = float(s["ref_level"])
    curve = [{"age": r["age"], "level": _r6(r["level"]),
              "lo": _r6(ref + r["ci_lo"]) if r["ci_lo"] is not None else None,
              "hi": _r6(ref + r["ci_hi"]) if r["ci_hi"] is not None else None,
              "pairs": r["pairs"], "thin": bool(r["thin"])} for r in curves[(req.stat, req.era)]]
    players, missing = [], []
    for pid in dict.fromkeys(req.player_ids):
        try:
            p = aging_player(pid, req.stat, req.era)
        except HTTPException:
            missing.append(pid)
            continue
        players.append({
            "player_id": pid, "player_name": p["player"]["player_name"], "offset": _r6(p["offset"]),
            "path": [{"age": x["age"], "level": _r6(x["level"])} for x in (p["path"] or [])],
            "seasons": [{k: (_r6(x[k]) if isinstance(x[k], float) else x[k])
                         for k in ("season", "team", "age", "value", "league_average", "vs_league", "qualified",
                                   "minutes", "weight", "note")} for x in p["seasons"]],
            "notes": p["notes"],
        })
    return {
        "stat": req.stat, "era": req.era,
        "summary": {k: s[k] for k in ("label", "kind", "weight", "era_label", "season_from", "season_to", "peak_age",
                                      "ref_age", "ref_level", "min_minutes", "min_attempts", "higher_is_better",
                                      "pairs", "players")},
        "curve": curve, "players": players, "missing": missing,
        "method": ("The Aging Curves page's typical curve (delta method: every pair of consecutive qualified seasons "
                   "gives one change, measured against each season's league average; 95% range from 300 resamples "
                   "of whole careers), anchored at the average 27-year-old. Each player's dashed line is that curve "
                   "moved to his level (his qualified seasons' weighted gap from it): how a typical player at his "
                   "level ages, not a forecast."),
        "_source": make_source(["aging_curves", "aging_curve_summary", "aging_league_average", "player_season_stats",
                                "player_bio"], "nba_api (stats.nba.com) + Basketball-Reference (seasons, birth dates)"),
    }
