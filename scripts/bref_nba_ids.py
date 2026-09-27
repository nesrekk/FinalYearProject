"""
bref_nba_ids.py
================
Basketball-Reference player id -> NBA person id, for every BAA/NBA player
in the local Basketball-Reference export (nba_data/kaggle_1947_present/).
Shared by load_kaggle_historical_seasons.py (player_id_map and pre-2010
player_season_stats) and load_draft_history_bref.py, so the two can't
disagree about who is who.

NBA ids come from nba_api's bundled all-time player list (offline) plus the
names already in player_season_stats (current players missing from that
list). A candidate NBA id for a Basketball-Reference player must pass every
check that applies:

  name       exact (with Jr./II kept), else without the suffix, else ignoring
             spaces and hyphens ("Wang Zhi-zhi"), else the same
             last name with a first-name form from FIRST_NAME_FORMS, else an
             entry in NICKNAMES (checked by hand, keyed by bbref id).
  seasons    if the id already has 2010+ rows in player_season_stats (loaded
             from nba_api, so keyed correctly), they must fall inside the
             Basketball-Reference career (+/- 1 season).
  id eras    two numbering patterns, each verified against every
             unambiguously matched player with no exception:
             - ids 200000-599999 were issued to players entering 2006-07 or
               later;
             - ids 76000-79999 belong to careers that ended by 1995-96.
             A career that clearly contradicts an id's era rules it out.

Then one person per id: players with a single remaining candidate claim it,
claimed ids are removed from everyone else's candidates, and this repeats
(so the last unresolved member of a same-name group is matched only if
exactly one consistent id is left). Anything still ambiguous is left
unmatched rather than guessed; the reason is recorded.
"""

import os
import re
import unicodedata

import pandas as pd
from nba_api.stats.static import players as nba_players

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")

# Same person, different first-name form (only forms seen in real mismatches).
FIRST_NAME_FORMS = [{"mike", "michael"}, {"steve", "steven"}, {"ron", "ronald", "ronnie"}, {"mel", "melvin"},
                    {"clar", "clarence"}, {"flip", "ronald"}, {"pearl", "dwayne"}, {"dave", "david"}, {"dan", "danny", "daniel"},
                    {"ed", "eddie", "edmund"}, {"ken", "kenny"}, {"bill", "billy"}, {"john", "johnny"},
                    {"tom", "tommy"}, {"fred", "freddie"}, {"joe", "joey"}, {"walt", "walter"}]

# Nicknames with no shared first-name form, checked by hand (bbref id -> NBA name).
NICKNAMES = {
    "architi01": "Nate Archibald",          # Nate "Tiny" Archibald
    "leverfa01": "Lafayette Lever",         # Lafayette "Fat" Lever
    "kerrre01": "Johnny Kerr",              # Johnny "Red" Kerr
    "jackslu01": "Lucious Jackson",         # Lucious "Luke" Jackson (1964 draft), not the 2004 Luke Jackson
    "richami01": "Micheal Ray Richardson",  # NBA spells it Micheal
}

MODERN = (200000, 600000)
LEGACY_BLOCK = (76000, 80000)


def norm(name, keep_suffix=False):
    name = str(name).replace("ı", "i").replace("ß", "ss")  # letters NFKD doesn't decompose
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[.']", "", name.lower()).replace("-", " ")
    if not keep_suffix:
        name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


def first_names_compatible(a, b):
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    return any(a in f and b in f for f in FIRST_NAME_FORMS)


def load_bref_players():
    """Every BAA/NBA player with a season in Advanced.csv: id, name, first and
    last season (end-year ints)."""
    adv = pd.read_csv(os.path.join(KAGGLE, "Advanced.csv"))
    adv = adv[adv.lg.isin(["NBA", "BAA"])]
    g = adv.groupby("player_id").agg(player=("player", "first"), first=("season", "min"), last=("season", "max"))
    return g.reset_index()


def resolve(bref, nba_seasons):
    """bref: DataFrame(player_id, player, first, last). nba_seasons: dict
    nba_id -> (first, last) season from nba_api-sourced rows (2010+).
    Returns dict bref_id -> (nba_id or None, how)."""
    nba_list = {p["id"]: p["full_name"] for p in nba_players.get_players()}
    nba_list.update(nba_seasons.get("_names", {}))
    by_full, by_plain, by_last, by_squashed = {}, {}, {}, {}
    for pid, name in nba_list.items():
        by_squashed.setdefault(norm(name).replace(" ", ""), set()).add(pid)  # "Wang Zhi-zhi" vs "Wang Zhizhi"
        by_full.setdefault(norm(name, keep_suffix=True), set()).add(pid)
        by_plain.setdefault(norm(name), set()).add(pid)
        parts = norm(name).split()
        if parts:
            by_last.setdefault(parts[-1], set()).add((pid, parts[0]))
    by_name_exact = {}
    for pid, name in nba_list.items():
        by_name_exact.setdefault(name, set()).add(pid)

    def consistent(pid, first, last):
        span = nba_seasons.get(pid)
        if span is not None and not (span[0] >= first - 1 and span[1] <= last + 1):
            return False
        if MODERN[0] <= pid < MODERN[1] and last <= 2006:
            return False
        if LEGACY_BLOCK[0] <= pid < LEGACY_BLOCK[1] and last > 1996:
            return False
        return True

    cands, how = {}, {}
    for r in bref.itertuples(index=False):
        first, last = int(r.first), int(r.last)
        if r.player_id in NICKNAMES:
            pool, method = by_name_exact.get(NICKNAMES[r.player_id], set()), "nickname"
        else:
            pool, method = by_full.get(norm(r.player, keep_suffix=True), set()), "exact_name"
            if not pool:
                pool, method = by_plain.get(norm(r.player), set()), "name_without_suffix"
            if not pool:
                pool, method = by_squashed.get(norm(r.player).replace(" ", ""), set()), "name_ignoring_spaces"
            if not pool:
                parts = norm(r.player).split()
                pool = {pid for pid, f in by_last.get(parts[-1], ())
                        if len(parts) >= 2 and first_names_compatible(f, parts[0])} if parts else set()
                method = "first_name_form"
        cands[r.player_id] = {pid for pid in pool if consistent(pid, first, last)}
        how[r.player_id] = method if pool else None

    result = {}
    changed = True
    while changed:
        changed = False
        claims = {}
        for bid, c in cands.items():
            if bid not in result and len(c) == 1:
                claims.setdefault(next(iter(c)), []).append(bid)
        for pid, bids in claims.items():
            if len(bids) == 1:
                result[bids[0]] = (pid, how[bids[0]])
                for other, c in cands.items():
                    if other != bids[0] and pid in c:
                        c.discard(pid)
                changed = True
            else:
                for bid in bids:  # two players, one id, nothing to tell them apart
                    cands[bid] = set()
                    result[bid] = (None, f"NBA id {pid} fits {len(bids)} different players")
                changed = True
    for bid, c in cands.items():
        if bid not in result:
            if how[bid] is None:
                result[bid] = (None, "no NBA name match")
            elif not c:
                result[bid] = (None, "name matches, but no id consistent with the career")
            else:
                result[bid] = (None, f"{len(c)} ids still fit after all checks")
    return result
