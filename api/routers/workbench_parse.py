"""
Workbench: type the Player Finder's sentence in plain English (round 7 step 9).

    GET  /workbench/parse/status   is the box set up here, and how accurate it was measured to be
    POST /workbench/parse          {"text": "..."} → the finder spec for the boxes, shown before anything runs

The work is in workbench_parse_lib.py (the prompt, the Gemini call, and to_spec,
which checks the model's answer against the catalogue like any untrusted
input). Only the typed sentence and a fixed description of the finder go to
Google; no board, no set, nothing from the database. The key stays on the
server (GEMINI_API_KEY in api/.env).

The free tier allows only so many calls a minute and a day per Google
project, so this process keeps at most PER_MINUTE calls a minute and answers
a sentence it has already parsed today from memory.
"""

import json
import os
import threading
import time
from collections import OrderedDict, deque
import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import workbench_parse_lib as P
from source_badge import make_source

router = APIRouter()

PER_MINUTE = 10
CACHE_SIZE = 256
EVAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workbench_parse_eval.json")
SENDS = ("Only what you type is sent, to Google's Gemini (no board, no set, no data); on the free tier Google "
         "may use it to improve its products, so don't type anything private.")

_lock = threading.Lock()
_calls = deque()
_answers = OrderedDict()


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=P.MAX_TEXT * 2)


def _evaluation():
    """The stored test-set result (scripts/workbench_parse_eval.py --write), summarised."""
    try:
        with open(EVAL_PATH) as f:
            ev = json.load(f)
    except (OSError, ValueError):
        return None
    runs = [r["summary"] for r in ev["runs_detail"]]
    first = runs[0]
    return {
        "evaluated": ev["evaluated"], "model": ev["model"], "prompt_version": ev["prompt_version"],
        "sentences_written": ev["sentences_written"], "n": ev["n"], "runs": ev["runs"],
        "all_right_by_run": ev["all_right_by_run"],
        "boxes_right_by_run": [r["boxes_right"] for r in runs],
        "per_box": {b: {"right": sum(r["per_box"][b]["right"] for r in runs), "of": sum(r["per_box"][b]["of"] for r in runs)}
                    for b in first["per_box"]},
        "unsupported_reported": {"right": sum(r["unsupported_reported"]["right"] for r in runs),
                                 "of": sum(r["unsupported_reported"]["of"] for r in runs)},
        "false_reports": sum(r["false_reports"] for r in runs),
        "median_seconds": first["median_seconds"],
        "current": ev["prompt_version"] == P.prompt_version() and ev["model"] == P.MODEL,
    }


def _source():
    return make_source(["player_season_stats", "player_game_lines", "team_seasons"],
                       f"Google Gemini API ({P.MODEL}) fills the finder's boxes; the catalogue checks them")


@router.get("/workbench/parse/status")
def parse_status():
    return {"available": P.api_key() is not None, "model": P.MODEL, "max_text": P.MAX_TEXT,
            "sends": SENDS, "evaluation": _evaluation(), "_source": _source()}


@router.post("/workbench/parse")
def parse_sentence(req: ParseRequest):
    try:
        text = P.clean_text(req.text)
    except P.ParseError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
    key = (text.lower(), datetime.date.today().isoformat(), P.prompt_version())
    with _lock:
        if key in _answers:
            _answers.move_to_end(key)
            return _answers[key]
        now = time.monotonic()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= PER_MINUTE:
            raise HTTPException(status_code=429, detail=(
                f"This server sends at most {PER_MINUTE} sentences a minute to the free Gemini tier; "
                "try again in a minute, or use the boxes."))
        _calls.append(now)
    try:
        got = P.parse(text)
    except P.ParseError as e:
        detail = {"message": str(e), "not_understood": e.not_understood, "dropped": e.dropped}
        raise HTTPException(status_code=e.status, detail=detail) from e
    out = {"text": text, "spec": got["spec"], "sentence": got["sentence"], "dropped": got["dropped"],
           "not_understood": got["not_understood"], "model": got["model"], "_source": _source()}
    with _lock:
        _answers[key] = out
        while len(_answers) > CACHE_SIZE:
            _answers.popitem(last=False)
    return out
