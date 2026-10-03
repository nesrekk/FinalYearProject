"""
Workbench query layer (round 7 step 1): a JSON spec in, rows out, built only
from api/workbench_catalogue.py.

    GET  /workbench/catalogue   every dataset, its stats (verified and
                                excluded, with the reason), groupings,
                                filters, season range and row count
    POST /workbench/query       run a spec (below)

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
0.5, the Leaderboard's rule).

Safety: the request is a fixed schema (unknown keys are refused), every
dataset/column/field/operator is looked up in the catalogue, values are bound
parameters, and SQL text comes only from the catalogue. Each query runs in a
read-only transaction with a statement_timeout and a row cap.

The season ranges, franchise lists and stability estimates are lru-cached per
process: restart impact_api after rebuilding a source table.
"""

import datetime
import decimal
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

import psycopg2
import psycopg2.errors
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

import workbench_catalogue as WC
from impact_core import get_db
from routers.game_log import _names
from routers.leaderboard import RELIABLE, _sample, stable_samples
from source_badge import make_source
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
           "date": ("gte", "gt", "lte", "lt", "eq", "between")}
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
    select: list                # (alias, role, name, sql); role field | value | n | sample | n_rows | n_games
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
        n = f"(CASE WHEN ({v}) IS NOT NULL THEN 1 END)"
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

    having, having_params = [], []
    for c in spec.having:
        col = _column(ds, c.key, "having")
        v = values.get(c.key) or _value_sql(ds, col, per, grouped)[0]
        having.append(_num_condition(v, c.op, c.value, f"having on {c.key}", having_params))
    if spec.min_games is not None:
        games = f"SUM({ds.games_sql})" if grouped else ds.games_sql
        having.append(f"({games} >= %s)")
        having_params.append(float(spec.min_games))

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
    }
    if per is not None:
        p = _applied_per(ds, col, per)
        out["per"] = p and WC.PER_MODES[p]
    return out


@router.post("/workbench/query")
def workbench_query(spec: QuerySpec):
    plan = compile_query(spec)
    ds = plan.ds
    rows = execute_readonly(plan.sql, plan.params)
    grouped = plan.group is not None
    names = _names() if ds.names_from_ids else None
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
        if names is not None and "player_id" in row:
            row["player_name"] = names.get(row["player_id"])
        row["n"] = n
        out.append(row)
    matched = int(rows[0][-2]) if rows else 0
    source_rows = int(rows[0][-1]) if rows else 0
    notes = list(plan.notes)
    if matched > spec.offset + len(out):
        notes.insert(0, f"Showing {spec.offset + 1:,}-{spec.offset + len(out):,} of {matched:,}; raise the limit "
                        f"(up to {ROW_CAP:,}) or narrow the query.")
    fields = []
    for _a, role, name, _s in plan.select:
        if role == "field" and name not in fields:
            fields.append(name)
    if names is not None and "player_id" in fields:
        fields.insert(fields.index("player_id") + 1, "player_name")
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
        "_source": make_source(list(ds.tables), ds.upstream),
    }


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
            "columns": [column_meta(ds, c) for c in ds.columns.values()],
            "notes": list(ds.notes),
            "_source": make_source(list(ds.tables), ds.upstream),
        })
    return {
        "datasets": datasets,
        "ops": {"numbers": [*NUM_OPS, "between"], "fields": DIM_OPS},
        "limits": {"row_cap": ROW_CAP, "timeout_seconds": TIMEOUT_MS / 1000, "max_columns": MAX_COLUMNS,
                   "max_conditions": MAX_CONDITIONS, "max_entities": MAX_ENTITIES, "max_sort": MAX_SORT},
        "formats": list(WC.FORMATS),
        "reliable_at": RELIABLE,
        "_source": make_source(sorted({t for d in WC.DATASETS.values() for t in d.tables} | {"stat_stability"}),
                               "nba_api (stats.nba.com), Basketball-Reference, ESPN play-by-play and scoreboard"),
    }
