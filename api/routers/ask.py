"""
Ask in English everywhere (round 10 step 7).

    GET  /ask/status   is the box set up here, and how accurate it was measured to be
    POST /ask          {"text": "...", "choices": {"Mike James": 2229}} → one checked action with a preview

The work is in ask_lib.py (the prompt built from ask_pages.py and the Workbench
catalogue, the Gemini call, and to_action, which checks the model's answer like
any untrusted input). Only the typed sentence and a fixed description of the app
go to Google; no board, no data, nothing about the user. The key stays on the
server (GEMINI_API_KEY in api/.env).

A `choices` entry answers an earlier `ask` action (a name two players on file
share): the name as typed, and the id the person picked.

The free tier allows only so many calls a minute and a day per Google project,
so this process keeps at most PER_MINUTE calls a minute (a Finder sentence costs
two) and answers a sentence it has already read today from memory.
"""

import datetime
import json
import os
import statistics
import threading
from collections import OrderedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import ask_lib as A
from source_badge import make_source

router = APIRouter()

PER_MINUTE = 10
CACHE_SIZE = 256
EVAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ask_eval.json")
SENDS = ("Only what you type is sent, to Google's Gemini (no board, no data, nothing about you); on the free tier "
         "Google may use it to improve its products, so don't type anything private.")

_lock = threading.Lock()
_budget = A.CallBudget(PER_MINUTE)
_answers = OrderedDict()


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=A.MAX_TEXT * 2)
    choices: dict[str, int] | None = None


def _evaluation():
    """The stored test-set result (scripts/ask_eval.py --split test --runs 3 --write), summarised. A sentence-run
    counted under `failed` got no answer (Google's daily free quota ran out): `answered` is the denominator the
    right counts stand on, and `complete` says whether every run was answered (`ask_eval.py --resume` fills them in)."""
    try:
        with open(EVAL_PATH) as f:
            ev = json.load(f)
    except (OSError, ValueError):
        return None
    runs = [r["summary"] for r in ev["runs_detail"]]
    by_action = {}
    for a in runs[0]["by_action"]:
        by_action[a] = {k: sum(r["by_action"][a][k] for r in runs) for k in ("n", "right", "wrong", "refused", "failed")}
    failed_by_run = [r["failed"] for r in runs]
    secs = [x["seconds"] for run in ev["runs_detail"] for x in run["results"] if x.get("seconds") is not None]
    return {
        "evaluated": ev["evaluated"], "model": ev["model"], "prompt_version": ev["prompt_version"],
        "sentences_written": ev["sentences_written"], "n": ev["n"], "runs": ev["runs"],
        "right_by_run": ev["right_by_run"], "failed_by_run": failed_by_run,
        "answered": sum(r["n"] - r["failed"] for r in runs), "complete": not any(failed_by_run),
        "resumed": ev.get("resumed", []),
        "by_action": by_action,
        "median_seconds": round(statistics.median(secs), 2) if secs else None,
        "current": _eval_current(ev),
    }


def _eval_current(ev):
    """Is the stored evaluation of the prompt the model gets today? The prompt names today's date and the current
    season, so its hash moves daily and on opening night: compare the prompt as it was on the evaluation day with
    the stored hash, and that day's seasons with today's."""
    try:
        day = datetime.date.fromisoformat(ev["evaluated"])
        seasons = tuple(ev["seasons"])
    except (KeyError, TypeError, ValueError):
        return False
    return (ev["model"] == A.MODEL and ev["prompt_version"] == A.prompt_version(day, seasons)
            and seasons == A.seasons_today())


def _source():
    return make_source(["player_season_stats", "team_seasons"],
                       f"Google Gemini API ({A.MODEL}) fills the form; the app's catalogue checks every piece")


@router.get("/ask/status")
def ask_status():
    return {"available": A.api_key() is not None, "model": A.MODEL, "max_text": A.MAX_TEXT, "sends": SENDS,
            "actions": list(A.ACTIONS) + ["ask"], "reasons": A.REASONS, "evaluation": _evaluation(), "_source": _source()}


@router.post("/ask")
def ask_sentence(req: AskRequest):
    try:
        text = A.clean_text(req.text)
    except A.ParseError as e:
        raise HTTPException(status_code=e.status, detail={"message": str(e)}) from e
    choices = {k: v for k, v in (req.choices or {}).items() if isinstance(v, int)}
    seasons = A.seasons_today()
    key = (text.lower(), json.dumps(sorted(choices.items())), A.today_nba().isoformat(), A.prompt_version(None, seasons))
    with _lock:
        if key in _answers:
            _answers.move_to_end(key)
            return _answers[key]
    try:
        got = A.ask(text, choices=choices, seasons=seasons, budget=_budget)
    except A.ParseError as e:
        detail = {"message": str(e), "not_understood": e.not_understood, "dropped": e.dropped}
        raise HTTPException(status_code=e.status, detail=detail) from e
    out = {"text": text, "action": got["action"], "preview": got["preview"], "notes": got["notes"],
           "guessed": got["guessed"], "not_understood": got["not_understood"], "model": got["model"],
           "_source": _source()}
    with _lock:
        _answers[key] = out
        while len(_answers) > CACHE_SIZE:
            _answers.popitem(last=False)
    return out
