"""
Workbench: type the Player Finder's sentence in plain English (round 7 step 9).

    typed text --(Gemini, structured output)--> an answer in INTENT form
               --(to_spec, this file)--------> a finder spec (POST /workbench/finder's)
               --(the block)-----------------> the finder's boxes, shown before anything runs

The model never writes SQL and never sees the database. It gets the typed
text and a fixed description of the finder built from the catalogue (stat
keys and labels, team codes, the seasons on file, the reading rules), and must
answer with JSON matching intent_schema(), whose enums are the catalogue's own
keys. Its answer is then treated like any other untrusted input: to_spec()
checks every piece against the catalogue again, drops what isn't allowed and
says what it dropped, applies the finder's defaults, and compiles the result
with workbench_finder.compile_finder (the compiler the finder itself runs), so
whatever reaches the boxes is a spec the finder accepts. Nothing is searched
until the user presses Find players.

Shared by routers/workbench_parse.py (the endpoint), scripts/workbench_parse_eval.py
(the measured accuracy) and api/tests/test_workbench_parse.py.
"""

import datetime
import hashlib
import json
import math
import os
import time
from functools import lru_cache

import requests
from dotenv import load_dotenv
from fastapi import HTTPException

import workbench_catalogue as WC
from routers.workbench import _dataset_meta, _franchises, _season_label
from routers.workbench_finder import MAX_FINDER_CONDITIONS, MAX_TESTS, FinderSpec, compile_finder

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

MODEL = "gemini-3.5-flash-lite"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 25
MAX_TEXT = 300
DEFAULT_MIN_GAMES = 40          # the finder block's starting games floor
FINDER_DATASETS = ("player_season", "player_game")
VALUE_OPS = ("gte", "gt", "lte", "lt", "eq", "ne", "between")
COUNT_OPS = ("gte", "gt", "lte", "lt", "eq")
PERS = ("game", "total", "per36", "per100")
MAX_NOTES = 6


class ParseError(Exception):
    """The sentence couldn't be turned into boxes; `status` is the HTTP status to answer with."""

    def __init__(self, message, status=422, dropped=None, not_understood=None):
        super().__init__(message)
        self.status = status
        self.dropped = dropped or []
        self.not_understood = not_understood or []


def api_key():
    return (os.environ.get("GEMINI_API_KEY") or "").strip() or None


# ─── what the model may answer with ─────────────────────────────────────────

@lru_cache(maxsize=1)
def _teams():
    """Today's franchises (code, name), the finder's opponent box."""
    return tuple((f["team"], f["name"]) for f in _franchises() if f["to"] >= 2021)


def finder_stats(ds_key):
    """The verified stats of one finder dataset, in catalogue order."""
    return [c for c in WC.DATASETS[ds_key].columns.values() if c.status == "verified"]


def intent_schema():
    """JSON Schema for the model's answer: every key it may use comes from the catalogue.
    No maxItems on the two nested arrays: with the stat enums, Gemini refuses the
    schema as too complex ("invalid argument", 2026-10-03); to_spec keeps the
    first 8 conditions and 4 tests and says so."""
    stats = sorted({c.key for k in FINDER_DATASETS for c in finder_stats(k)})
    num = {"type": ["number", "null"]}
    test = {
        "type": "object",
        "properties": {"stat": {"type": "string", "enum": stats},
                       "op": {"type": "string", "enum": list(VALUE_OPS)},
                       "value": num, "value2": num},
        "required": ["stat", "op", "value", "value2"],
    }
    cond = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["value", "count", "streak"]},
            "stat": {"type": ["string", "null"], "enum": stats + [None]},
            "per": {"type": "string", "enum": list(PERS)},
            "op": {"type": "string", "enum": list(VALUE_OPS)},
            "value": num, "value2": num,
            "min_attempts": num,
            "tests": {"type": "array", "items": test},
            "count_op": {"type": "string", "enum": list(COUNT_OPS)},
            "count": {"type": ["integer", "null"]},
        },
        "required": ["type", "stat", "per", "op", "value", "value2", "min_attempts", "tests", "count_op", "count"],
    }
    return {
        "type": "object",
        "properties": {
            "dataset": {"type": "string", "enum": list(FINDER_DATASETS)},
            "scope": {"type": "string", "enum": ["season", "span"]},
            "season_from": {"type": ["integer", "null"]},
            "season_to": {"type": ["integer", "null"]},
            "min_games": {"type": ["integer", "null"]},
            "home": {"type": "string", "enum": ["any", "home", "away"]},
            "result": {"type": "string", "enum": ["any", "wins", "losses"]},
            "opponent": {"type": ["string", "null"], "enum": [t for t, _ in _teams()] + [None]},
            "min_minutes": num,
            "conditions": {"type": "array", "items": cond},
            "not_understood": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_NOTES},
        },
        "required": ["dataset", "scope", "season_from", "season_to", "min_games", "home", "result",
                     "opponent", "min_minutes", "conditions", "not_understood"],
    }


# ─── the model's answer → a finder spec ─────────────────────────────────────

def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def has_sample_floor(col):
    """The block's rule (finderSpec.hasSampleFloor): rates on attempts, possessions or plays."""
    return col.kind not in ("count", "sum") and (col.n_unit or "") not in ("games", "seasons", "")


def default_min_n(stat, min_games):
    """The block's starting attempts floor (finderSpec.defaultMinN): the Leaderboard's
    per-game floor of the season table's stat × the games floor (at least 20)."""
    col = WC.DATASETS["player_season"].columns.get(stat)
    per_game = WC.MIN_ATTEMPTS_PER_GAME.get(col.attempts) if col is not None else None
    if not per_game:
        return None
    return math.floor(per_game * max(min_games or 0, 20) + 0.5)


def _share(col, v, notes, where):
    """A percentage arrives out of 100 (60 = 60%); the finder keeps shares (0.6)."""
    if col.fmt != "pct" or v is None:
        return v
    if 0 < abs(v) < 1:
        notes.append(f"{where}: read {v:g} as {v * 100:g}%.")
        return v
    return v / 100


def _test_value(col, t, notes, where):
    """(op, value) of a test or value condition, or None when unusable (noted)."""
    op = t.get("op")
    if op not in VALUE_OPS:
        notes.append(f"{where}: no comparison given.")
        return None
    v, v2 = t.get("value"), t.get("value2")
    if not _is_num(v):
        notes.append(f"{where}: no number given.")
        return None
    v = _share(col, float(v), notes, where)
    if op == "between":
        if not _is_num(v2):
            notes.append(f"{where}: 'between' needs two numbers.")
            return None
        v2 = _share(col, float(v2), notes, where)
        return op, [_clean_num(min(v, v2)), _clean_num(max(v, v2))]
    return op, _clean_num(v)


def _clean_num(v):
    v = float(v)
    return int(v) if v.is_integer() else round(v, 6)


def _int_season(v):
    return int(v) if _is_num(v) and float(v).is_integer() and 1900 <= v <= 2200 else None


def to_spec(intent, dropped=None):
    """The model's answer (intent_schema form) → (finder spec, its sentence, dropped notes).
    Every piece is checked against the catalogue; what can't be used is left
    out and described in `dropped`. Raises ParseError when nothing usable is left."""
    dropped = [] if dropped is None else dropped
    if not isinstance(intent, dict):
        raise ParseError("The model's answer wasn't an object.", status=502)
    ds_key = intent.get("dataset")
    if ds_key not in FINDER_DATASETS:
        dropped.append("No data set chosen; used season stats.")
        ds_key = "player_season"
    ds = WC.DATASETS[ds_key]
    meta = _dataset_meta()[ds_key]
    scope = intent.get("scope") if intent.get("scope") in ("season", "span") else "season"

    # Seasons: end years, inside what the data covers.
    lo, hi = _int_season(intent.get("season_from")), _int_season(intent.get("season_to"))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    if (hi is not None and hi < meta["from"]) or (lo is not None and lo > meta["to"]):
        asked = _season_label(lo if lo is not None else hi) + ("" if lo == hi or lo is None or hi is None
                                                               else f" to {_season_label(hi)}")
        dropped.append(f"Seasons ({asked}): {ds.label.lower()} cover {_season_label(meta['from'])} to "
                       f"{_season_label(meta['to'])}, so the seasons were left at that whole range.")
        lo = hi = None
    else:
        if lo is not None and lo <= meta["from"]:
            lo = None
        if hi is not None and hi >= meta["to"]:
            hi = None
    # One season: a player-season and "the seasons combined" are the same unit,
    # so the boxes always say "in a single season" (the finder reads "in 2022-23").
    if (meta["from"] if lo is None else lo) == (meta["to"] if hi is None else hi):
        scope = "season"

    # Which rows count: the boxes the block has (home/away, result, opponent: game logs; minutes: both).
    filters = []
    game_only = []
    home = intent.get("home")
    if home in ("home", "away"):
        if ds_key == "player_game":
            filters.append({"key": "home", "op": "eq", "value": home == "home"})
        else:
            game_only.append(f"{home} games")
    result = intent.get("result")
    if result in ("wins", "losses"):
        if ds_key == "player_game":
            filters.append({"key": "result", "op": "eq", "value": result == "wins"})
        else:
            game_only.append(result)
    opp = intent.get("opponent")
    if opp is not None:
        codes = {t for t, _ in _teams()}
        if opp not in codes:
            dropped.append(f"Opponent {opp!r} isn't a team code the finder knows.")
        elif ds_key == "player_game":
            filters.append({"key": "opponent", "op": "eq", "value": opp})
        else:
            game_only.append(f"games against {opp}")
    if game_only:
        dropped.append(f"{', '.join(game_only).capitalize()}: only game logs know single games, and season stats "
                       "were chosen.")
    mm = intent.get("min_minutes")
    if mm is not None:
        if _is_num(mm) and 0 < mm <= 60:
            filters.append({"key": "min", "op": "gte", "value": _clean_num(mm)})
        else:
            dropped.append(f"Minutes floor {mm!r}: it must be between 0 and 60.")

    single = ds_key == "player_season" and scope == "season"
    conditions = []
    raw = intent.get("conditions") if isinstance(intent.get("conditions"), list) else []
    if len(raw) > MAX_FINDER_CONDITIONS:
        dropped.append(f"Only the first {MAX_FINDER_CONDITIONS} conditions were kept.")
    for i, c in enumerate(raw[:MAX_FINDER_CONDITIONS]):
        where = f"Condition {i + 1}"
        if not isinstance(c, dict) or c.get("type") not in ("value", "count", "streak"):
            dropped.append(f"{where}: not a condition the finder has.")
            continue
        if c["type"] == "value":
            col = _verified(ds, c.get("stat"), where, dropped)
            if col is None:
                continue
            if col.kind == "none" and not single:
                dropped.append(f"{where}: {col.label} is one value per season and can't be combined over "
                               f"several {ds.row_label}; it works in a single season with season stats.")
                continue
            ov = _test_value(col, c, dropped, f"{where} ({col.label})")
            if ov is None:
                continue
            per = c.get("per") if c.get("per") in PERS else "game"
            if col.kind != "count":
                per = "game"
            elif per not in (col.per_modes or ds.per_modes):
                dropped.append(f"{where}: {col.label} can't be taken {WC.PER_MODES[per]}; used per game.")
                per = "game"
            out = {"type": "value", "stat": col.key, "op": ov[0], "value": ov[1], "per": per}
            mn = c.get("min_attempts")
            if has_sample_floor(col) and _is_num(mn) and mn > 0:
                out["min_n"] = math.floor(mn + 0.5)
            conditions.append(out)
            continue
        tests = []
        raw_tests = c.get("tests") if isinstance(c.get("tests"), list) else []
        if len(raw_tests) > MAX_TESTS:
            dropped.append(f"{where}: only the first {MAX_TESTS} tests were kept.")
        for j, t in enumerate(raw_tests[:MAX_TESTS]):
            w = f"{where}, test {j + 1}"
            col = _verified(ds, t.get("stat") if isinstance(t, dict) else None, w, dropped)
            if col is None:
                continue
            ov = _test_value(col, t, dropped, f"{w} ({col.label})")
            if ov is not None:
                tests.append({"stat": col.key, "op": ov[0], "value": ov[1]})
        if not tests or len(tests) != len(raw_tests[:MAX_TESTS]):
            if raw_tests:
                dropped.append(f"{where}: left out, since part of it couldn't be used.")
            else:
                dropped.append(f"{where}: no tests given.")
            continue
        count = c.get("count")
        if not (_is_num(count) and float(count).is_integer() and 1 <= count <= 100_000):
            if c["type"] == "count":
                count = 1
            else:
                dropped.append(f"{where}: a run needs its length (how many in a row).")
                continue
        out = {"type": c["type"], "tests": tests, "count": int(count)}
        if c["type"] == "count":
            out["count_op"] = c.get("count_op") if c.get("count_op") in COUNT_OPS else "gte"
        conditions.append(out)
    if not conditions:
        raise ParseError("Nothing in that sentence could go in the finder's boxes.", dropped=dropped)

    min_games = intent.get("min_games")
    if min_games is not None and not (_is_num(min_games) and 0 < min_games <= 5000):
        dropped.append(f"Games floor {min_games!r}: it must be between 1 and 5,000.")
        min_games = None
    spec = {"dataset": ds_key, "scope": scope, "season_from": lo, "season_to": hi, "filters": filters,
            "min_games": None if min_games is None else math.floor(min_games + 0.5), "conditions": conditions}
    try:
        spec, sentence = finalize(spec)
    except ParseError as e:
        e.dropped = dropped + e.dropped
        raise
    return spec, sentence, dropped


def _verified(ds, key, where, dropped):
    col = ds.columns.get(key) if isinstance(key, str) else None
    if col is None:
        other = next((d for d in FINDER_DATASETS if d != ds.key and isinstance(key, str)
                      and key in WC.DATASETS[d].columns), None)
        if other:
            dropped.append(f"{where}: {WC.DATASETS[other].columns[key].label} is in "
                           f"{WC.DATASETS[other].label.lower()}, not {ds.label.lower()}.")
        else:
            dropped.append(f"{where}: no stat the finder knows.")
        return None
    if col.status != "verified":
        dropped.append(f"{where}: {col.label} isn't offered ({col.reason})")
        return None
    return col


def finalize(spec):
    """Apply the finder block's defaults to a spec (a games floor of 40 when a
    value condition needs one; a percentage's attempts floor), then check it
    compiles. Used on the model's answer and on the test sentences' expected
    answers alike, so both are compared after the same rules. → (spec, the
    finder's own one-sentence restatement of it)."""
    spec = json.loads(json.dumps(spec))
    conds = spec["conditions"]
    if spec.get("min_games") is None and any(c["type"] == "value" for c in conds):
        spec["min_games"] = DEFAULT_MIN_GAMES
    ds = WC.DATASETS[spec["dataset"]]
    for c in conds:
        if c["type"] == "value":
            c.setdefault("per", "game")
            col = ds.columns[c["stat"]]
            if c.get("min_n") is None and has_sample_floor(col):
                d = default_min_n(c["stat"], spec.get("min_games"))
                if d:
                    c["min_n"] = d
                else:
                    c.pop("min_n", None)
        elif c["type"] == "count":
            c.setdefault("count_op", "gte")
    spec.setdefault("filters", [])
    try:
        model = FinderSpec(**{k: v for k, v in spec.items() if v is not None or k in ("season_from", "season_to")})
        plan = compile_finder(model)
    except HTTPException as e:
        raise ParseError(f"The finder refused it: {e.detail}", dropped=[]) from e
    except ValueError as e:
        raise ParseError(f"The finder refused it: {e}", dropped=[]) from e
    return spec, plan["sentence"]


# ─── what the model is told ─────────────────────────────────────────────────

def latest_seasons(today=None):
    """(latest season on file, what 'last season' means today): while the
    latest season is still being played (before July of its end year), 'last
    season' is the one before it."""
    today = today or datetime.date.today()
    latest = _dataset_meta()["player_season"]["to"]
    in_progress = today < datetime.date(latest, 7, 1)
    return latest, (latest - 1 if in_progress else latest)


def _stat_lines(ds_key):
    lines = []
    for c in finder_stats(ds_key):
        how = {"count": "per game unless said otherwise", "sum": "a season total",
               "none": "one value per season"}.get(c.kind, "a rate")
        unit = " (a percentage)" if c.fmt == "pct" else ""
        lines.append(f"  {c.key}: {c.label}{unit}; {how}")
    return "\n".join(lines)


def system_prompt(today=None):
    today = today or datetime.date.today()
    meta = _dataset_meta()
    ps, pg = meta["player_season"], meta["player_game"]
    latest, last = latest_seasons(today)
    teams = ", ".join(f"{code} {name}" for code, name in _teams())
    return f"""You fill in the boxes of a basketball Player Finder, which searches NBA players with conditions.
You never answer the question and never invent numbers: you only turn the user's sentence into the JSON form.
Whatever the boxes can't hold goes, in a few words, into not_understood; everything else is filled.

DATA (dataset)
- player_season: season stats, one row per player per regular season, {_season_label(ps['from'])} to {_season_label(ps['to'])}.
- player_game: game logs, one row per player per regular-season game, {_season_label(pg['from'])} to {_season_label(pg['to'])}.
Use player_game when the sentence needs single games: a number of games ("in 10 games", "3 times", "twice",
"40-point games"), games in a row, home or away, wins or losses, an opponent, a minutes floor inside a game,
or a stat only game logs have. Otherwise player_season.

ONE RESULT PER (scope)
- "season": one result per player-season. When the sentence says in a season, in a single season, in any season,
  per season, or names exactly one season.
- "span": one result per player over all the chosen seasons combined. When it says since ..., from ... to ...,
  between ... and ..., over the last N seasons, career, combined, in total, ever, all-time, or N straight seasons.
  Both ("in a season since 2015"): "season".
- No time words at all: "span" if every condition is a count or a run; otherwise "season".

SEASONS (season_from, season_to) are end years: 2022-23 = 2023. A bare year Y is the season ending in Y
("since 2015" = season_from 2015; "in 2019" = 2019 to 2019). Today is {today.isoformat()}: "this season" =
{_season_label(latest)} ({latest}); "last season" = {_season_label(last)} ({last}); "the last N seasons" = the
latest N up to {latest}. null = not said.

CONDITIONS (every one must hold)
- type "value": the stat over the player's rows: an average ("averaged 25 points"), a percentage ("shot 40% from
  three"), a rating ("a BPM of 8"). per: "game" (default), "total" ("totalled", "in total"), "per36" ("per 36
  minutes"), "per100" ("per 100 possessions"). min_attempts: only an attempts floor the sentence gives ("on 300+
  attempts"), else null. Fill stat, per, op, value (value2 for between); tests [] and count null.
- type "count": in how many rows (games, or seasons with player_season) every test holds in the same row:
  "in at least 10 games scored 30" = count_op gte, count 10, tests [pts gte 30]; "a 50-point game", "scored 50 in a
  game", "the most 40-point games" = count 1. "5 seasons averaging 20" = player_season, count 5.
- type "streak": the longest run in a row: "10 straight games of 20+" = count 10, tests [pts gte 20];
  "5 straight seasons averaging 25" = player_season, scope span.
- For count and streak fill tests and count (count_op for count) and set stat and value to null.
- Tests inside one count or streak hold in the same game (season). A triple-double in a game = three tests,
  pts, reb and ast gte 10; "averaged a triple-double" = three value conditions.

NUMBERS
- Percentages out of 100: 60% is 60.
- op: at least, or more, +, or better, or higher = gte; more than, over, above = gt; at most, or fewer, no more
  than, or less = lte; under, fewer than, less than, below = lt; exactly = eq; between A and B = between (value A,
  value2 B). A plain number ("averaged 20 points") = gte.
- "20 and 10" = points and rebounds; "25, 12 and 9" = points, rebounds and assists. dimes = assists, boards =
  rebounds, shots = field-goal attempts, threes = 3-pointers made, from the line = free throws.

OTHER BOXES
- min_games: a floor on games ("in 50+ games", "with at least 60 games"); null if not said.
- home ("on the road" = away), result (wins, losses) and opponent ("against the Celtics" = BOS) need player_game.
- min_minutes: count only games (player_game) or seasons (player_season, minutes a game) with at least this many
  minutes: "in games he played 35+ minutes". "averaged 30 minutes" is a value condition on min instead.

NOT POSSIBLE: positions (guards, centers), the team a player played for ("Lakers players", "for the Knicks"),
playoffs, rookies, awards, named players or "players like X", "or" between conditions (leave both sides of the
"or" out). Put each in not_understood and fill the rest. "the most", "the best", "top 10" need no box (results
are sorted by the first condition). If everything fits, not_understood is [].

STATS, season stats (player_season):
{_stat_lines('player_season')}
STATS, game logs (player_game):
{_stat_lines('player_game')}
TEAMS: {teams}
"""


def prompt_version(today=None):
    """A short hash of what the model is told (prompt + schema + model), recorded with every evaluation."""
    blob = json.dumps([MODEL, system_prompt(today), intent_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ─── asking Gemini ──────────────────────────────────────────────────────────

def _scrub(text, key):
    return text.replace(key, "…") if key and isinstance(text, str) else text


def ask_gemini(text, system, schema, model=MODEL, key=None, timeout=TIMEOUT_S):
    """One generateContent call with structured output → (answer object, usage).
    Raises ParseError with an HTTP status the endpoint can pass on."""
    key = key or api_key()
    if not key:
        raise ParseError("Typing a sentence isn't set up on this server (no GEMINI_API_KEY in api/.env); "
                         "use the boxes.", status=503)
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema,
                             "temperature": 0, "maxOutputTokens": 2048},
    }
    try:
        r = requests.post(API_URL.format(model=model), json=body, timeout=timeout,
                          headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.Timeout as e:
        raise ParseError(f"Google's model didn't answer within {timeout} s; try again, or use the boxes.",
                         status=504) from e
    except requests.RequestException as e:
        raise ParseError("Couldn't reach Google's model from the server; use the boxes.", status=502) from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 429:
        raise ParseError(_quota_message(data), status=429)
    if r.status_code in (400, 401, 403) and _key_problem(data):
        raise ParseError("Google refused the server's Gemini key; use the boxes.", status=503)
    if r.status_code >= 400:
        msg = _scrub(str((data.get("error") or {}).get("message") or r.status_code), key)
        raise ParseError(f"Google's model answered with an error ({msg[:160]}); use the boxes.", status=502)
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    raw = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if cand.get("finishReason") not in (None, "STOP") or not raw:
        raise ParseError(f"Google's model gave no usable answer ({cand.get('finishReason') or 'empty'}); "
                         "try rewording it, or use the boxes.", status=502)
    try:
        answer = json.loads(raw)
    except ValueError as e:
        raise ParseError("The model's answer wasn't valid JSON; try rewording it, or use the boxes.",
                         status=502) from e
    return answer, {"usage": data.get("usageMetadata"), "model_version": data.get("modelVersion")}


def _key_problem(data):
    err = data.get("error") or {}
    text = json.dumps(err).upper()
    return "API_KEY" in text or err.get("status") in ("PERMISSION_DENIED", "UNAUTHENTICATED")


def _quota_message(data):
    """Google's free tier has per-minute and per-day limits (per project); say which one ran out."""
    details = (data.get("error") or {}).get("details") or []
    ids = " ".join(v.get("quotaId", "") for d in details for v in d.get("violations", []) or [])
    delay = next((d.get("retryDelay") for d in details if d.get("retryDelay")), None)
    if "PerDay" in ids:
        return ("The free Gemini quota for today is used up (Google resets it at midnight Pacific time); "
                "use the boxes until then.")
    wait = f" in about {delay.rstrip('s').split('.')[0]} s" if delay else " in a minute"
    return f"Too many sentences at once for the free Gemini quota; try again{wait}, or use the boxes."


def clean_text(text):
    text = " ".join(str(text or "").split())
    if not text:
        raise ParseError("Type a sentence first.", status=400)
    if len(text) > MAX_TEXT:
        raise ParseError(f"Keep it under {MAX_TEXT} characters.", status=400)
    return text


def _notes(items):
    out = []
    for x in items if isinstance(items, list) else []:
        if isinstance(x, str) and x.strip():
            x = " ".join(x.split())[:120]
            if x not in out:
                out.append(x)
    return out[:MAX_NOTES]


def parse(text, model=MODEL, key=None, today=None, timeout=TIMEOUT_S):
    """Typed text → {spec, sentence, dropped, not_understood, model, intent, usage}."""
    text = clean_text(text)
    t0 = time.time()
    intent, info = ask_gemini(text, system_prompt(today), intent_schema(), model=model, key=key, timeout=timeout)
    seconds = round(time.time() - t0, 2)
    not_understood = _notes(intent.get("not_understood") if isinstance(intent, dict) else None)
    try:
        spec, sentence, dropped = to_spec(intent)
    except ParseError as e:
        e.not_understood = not_understood
        raise
    return {"spec": spec, "sentence": sentence, "dropped": dropped, "not_understood": not_understood,
            "model": info["model_version"] or model, "intent": intent, "usage": info["usage"], "seconds": seconds}
