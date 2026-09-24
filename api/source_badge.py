"""
source_badge.py
=================
Shared `_source` disclosure object, attached to each main Analytics
endpoint's JSON response so the frontend can render a real "where did
this come from" chip (`SourceBadge.jsx`) instead of leaving provenance
implicit. Small and uniform on purpose — this isn't a data-quality
report, just: which real Postgres tables backed this response, which
real upstream API/dataset those tables were originally built from, and
(when the endpoint does a real live fetch rather than reading a
precomputed table) roughly when that real data was last refreshed.

Usage:
    from source_badge import make_source
    ...
    return {..., "_source": make_source(
        tables=["player_season_stats"],
        upstream_api="nba_api (stats.nba.com)",
    )}
"""

from typing import List, Optional


def make_source(tables: List[str], upstream_api: str, as_of: Optional[str] = None, live: bool = False):
    return {
        "tables": tables,
        "upstream_api": upstream_api,
        "live": live,
        "as_of": as_of,
    }
