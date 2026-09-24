
from fastapi import APIRouter


router = APIRouter()


@router.get("/")
def root():
    return {
        "service": "NBA Impact Score API",
        "version": "1.0.0",
        "endpoints": [
            "/impact/raw/{season}",
            "/impact/star/{season}",
            "/impact/player/{player_name}/{season}",
            "/radar/{player_name}?season=",
            "/players/history/{player_name}",
            "/teams/history/{team_abbr}",
            "/trade/teams/{season}",
            "/trade/roster/{team_abbr}/{season}",
            "/trade/simulate",
        ],
    }
