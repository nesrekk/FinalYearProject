import json
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException

from impact_core import (
    _CACHE,
    _CACHE_TTL_SECONDS,
    fetch_json,
)

router = APIRouter()


@router.get("/media/player-image/{player_name}")
def get_player_image(player_name: str):
    """
    Player image from TheSportsDB by player name.
    """
    name = (player_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="player_name is required.")

    key = name.lower()
    now = time.time()
    cached = _CACHE["player_images"].get(key)
    if cached and (now - cached["ts"] < _CACHE_TTL_SECONDS):
        return {"player_name": name, "image_url": cached["url"]}

    try:
        data = fetch_json(
            f"https://www.thesportsdb.com/api/v1/json/123/searchplayers.php?p={quote(name)}"
        )
        players = data.get("player", []) or []
        image_url = None
        if players:
            best = players[0]
            image_url = best.get("strThumb") or best.get("strCutout") or best.get("strRender")
        _CACHE["player_images"][key] = {"ts": now, "url": image_url}
        return {"player_name": name, "image_url": image_url}
    except Exception:
        return {"player_name": name, "image_url": None}
