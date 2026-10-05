"""
local_only.py
==============
Tables kept in the local database only, never copied to the Layerbase cloud mirror (round 8 step 10,
"Layerbase slimming", the owner's decision of 2026-10-05). Layerbase's free tier is 5 GB; these six are
per-unit rows that only the paper's scripts read (no page or route reads them; the Data Coverage page counts
report_card_units and says "kept local" on the mirror), about 0.6 GB with their indexes.

Used by:
  scripts/migrate_to_layerbase.py   never copies them (and --check doesn't count them as missing)
  scripts/paper_manifest.py         --compare skips them and names them
  api/routers/meta.py               Data Coverage marks them instead of "table not found" on the mirror
  api/tests/conftest.py             on DB_TARGET=layerbase a test stopped by one of these tables being
                                    absent is skipped with that reason, not failed

Before adding a table here, grep api/ and frontend/src for it: a table any page reads must stay on the mirror.
"""

from db_config import DB_TARGET

LOCAL_ONLY = (
    "paper_eval_predictions",       # paper_eval.py's per-unit predictions (~390 MB)
    "shot_xfg",                     # build_shot_making.py's per-shot P(make), read by paper_xrapm / shot value builds
    "paper_ablation_predictions",   # paper_ablations.py's per-unit predictions
    "report_card_units",            # build_report_card.py's per-unit rows (the page reads its _tests/_pooled tables)
    "report_card_game_sums",        # build_report_card.py's per-game sums
    "paper_ablation_shot_games",    # paper_ablations.py's per-game shot sums
)

NOTE = "kept local, not on the cloud mirror"


def on_mirror():
    """True when this process reads the Layerbase mirror (DB_TARGET=layerbase), where LOCAL_ONLY tables are absent."""
    return DB_TARGET == "layerbase"


def kept_local_reason(missing):
    """On the mirror, why a test skips when the tables it found missing include LOCAL_ONLY ones (else None)."""
    kept = [t for t in missing if t in LOCAL_ONLY]
    if not (on_mirror() and kept):
        return None
    return f"{', '.join(kept)} {'is' if len(kept) == 1 else 'are'} {NOTE} (api/local_only.py)"
