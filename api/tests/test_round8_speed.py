"""
Round 8 step 8: speed. Every fix keeps the answer the same; each test pins that (the old query or the old
code path against the new one) and, where the gain is large, the speed.

R8-083  Play Finder with a season or date filter took 45-112 s (Postgres walked play_finder_games' primary key
        backwards in a nested loop over every play of the filter): the page of rows is now picked from
        play_finder_events first, ordered by `game_no + 0`, and the counts come from one GROUP BY (cached).
R8-011  /news/current reads its six RSS feeds at the same time, combined in the list's order.
R8-084  /meta/current asked stats.nba.com for the team block on every call before opening night (an answer with
        nothing played wasn't cached).
R8-085  Guess the Game read the last score of all 7,652 games (a 3.6M-event scan) on every puzzle, guess and
        reveal; Game Replay's game list did the same per season.
R8-060  Data Quality's two slow live checks run their two passes on two connections at once, and a check runs
        once at a time (the page asks for every check at once, twice under StrictMode).
        Hot Streaks' league list splits the later games once instead of masking the season per player.
"""

import os
import sys
import threading
import time
import urllib.error
from datetime import date

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API_DIR)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


@pytest.fixture(scope="module")
def client():
    from impact_api import app
    return TestClient(app)


@pytest.fixture
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    yield conn.cursor()
    conn.close()


# ── Play Finder (R8-083) ──────────────────────────────────────────────────────

def test_play_finder_rows_can_be_joined_after_the_limit(cur):
    """The rewrite picks the page of plays before joining the games and pbp_events: that drops nothing only if
    every play has both, and the order is total only if (event_id, cat) is unique."""
    cur.execute("""SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM play_finder_games g WHERE g.game_no = p.game_no)),
                          count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM pbp_events e WHERE e.id = p.event_id)),
                          count(*) - count(DISTINCT (p.event_id, p.cat))
                   FROM play_finder_events p""")
    assert cur.fetchone() == (0, 0, 0)


OLD_ROWS = """SELECT p.event_id, p.cat FROM play_finder_events p JOIN play_finder_games gm ON gm.game_no = p.game_no
              JOIN pbp_events e ON e.id = p.event_id {where} ORDER BY {order} LIMIT %s OFFSET %s"""


@pytest.mark.parametrize("query,where,args,order", [
    ({"player_id": 2544, "sort": "newest", "limit": 50, "offset": 40}, "WHERE p.player_id = %s", [2544],
     "p.game_no DESC, p.event_id, p.cat"),
    ({"player_id": 203999, "sort": "dist", "limit": 30, "offset": 0}, "WHERE p.player_id = %s AND p.dist IS NOT NULL",
     [203999], "p.dist DESC NULLS LAST, p.game_no DESC, p.event_id, p.cat"),
    ({"game": "espn_401704628", "sort": "oldest", "limit": 100, "offset": 20}, "WHERE p.game_no = %s", None,
     "p.game_no, p.event_id, p.cat"),
])
def test_play_finder_rows_equal_the_old_query(client, cur, query, where, args, order):
    """Filters the old plan answered quickly: the new endpoint returns the same plays in the same order."""
    if args is None:
        cur.execute("SELECT game_no FROM play_finder_games WHERE game_id = %s", (query["game"],))
        args = [cur.fetchone()[0]]
    cur.execute(OLD_ROWS.format(where=where, order=order), args + [query["limit"], query["offset"]])
    old = [list(r) for r in cur.fetchall()]
    body = client.get("/plays/finder", params=query).json()
    from play_finder import CATS
    code = {v[0]: k for k, v in CATS.items()}
    assert [[r["event_id"], code[r["cat"]]] for r in body["results"]] == old


def test_play_finder_counts_equal_the_two_old_group_bys(client, cur):
    cur.execute("SELECT game_no FROM play_finder_games WHERE season = 2025 ORDER BY game_no LIMIT 1 OFFSET 600")
    first = cur.fetchone()[0]
    cur.execute("SELECT game_date FROM play_finder_games WHERE game_no = %s", (first,))
    day = cur.fetchone()[0]
    cur.execute("SELECT min(game_no), max(game_no) FROM play_finder_games WHERE game_date = %s", (day,))
    lo, hi = cur.fetchone()
    where = "WHERE p.game_no BETWEEN %s AND %s"
    cur.execute(f"SELECT p.cat, COUNT(*) FROM play_finder_events p JOIN play_finder_games gm ON gm.game_no = p.game_no "
                f"{where} GROUP BY 1", (lo, hi))
    by_cat = dict(cur.fetchall())
    cur.execute(f"""SELECT p.player_id, COUNT(*) n FROM play_finder_events p JOIN play_finder_games gm ON gm.game_no = p.game_no
                    {where} AND p.player_id IS NOT NULL GROUP BY 1 ORDER BY n DESC, p.player_id LIMIT 10""", (lo, hi))
    most = cur.fetchall()
    body = client.get("/plays/finder", params={"date_from": day.isoformat(), "date_to": day.isoformat()}).json()
    from play_finder import CATS
    assert {CATS[c][0]: n for c, n in by_cat.items()} == {b["key"]: b["n"] for b in body["by_cat"]}
    assert [(m["player_id"], m["n"]) for m in body["most"]] == [tuple(m) for m in most]
    assert body["total"] == sum(by_cat.values())


def test_play_finder_season_filter_is_fast(client):
    """One season took ~50 s and 'up to 2020-21' ~2 minutes before (R8-083); now well under a second."""
    for params in ({"season_from": 2024, "season_to": 2024}, {"season_to": 2021, "sort": "oldest"}):
        t = time.time()
        r = client.get("/plays/finder", params=params)
        assert r.status_code == 200 and r.json()["results"]
        assert time.time() - t < 5, params


# ── News (R8-011) ──────────────────────────────────────────────────────────────

def _rss(prefix, n):
    items = "".join(f"<item><title>{prefix} headline {i}</title><link>https://x/{prefix}/{i}</link>"
                    f"<description>d</description><pubDate>Mon, 05 Oct 2026 1{i}:00:00 GMT</pubDate></item>"
                    for i in range(n))
    return f"<rss><channel>{items}</channel></rss>"


def test_news_feeds_combine_in_list_order(monkeypatch):
    """Fetched at the same time, the feeds still combine in the list's order, whatever order they finish in,
    and a feed that fails adds nothing (as the one-by-one loop did)."""
    import impact_core as ic
    delays = [0.30, 0.0, 0.20, 0.05, 0.25, 0.10]

    def fake(url, headers=None, timeout=20):
        k = [u for _, u in FEEDS].index(url)
        time.sleep(delays[k])
        if k == 1:
            raise urllib.error.HTTPError(url, 404, "gone", None, None)
        return _rss(f"f{k}", 3)
    FEEDS = [("ESPN", "https://www.espn.com/espn/rss/nba/news"), ("NBA.com", "https://www.nba.com/rss/nba_rss.xml"),
             ("Google News", "https://news.google.com/rss/search?q=NBA&hl=en-US&gl=US&ceid=US:en"),
             ("Yahoo Sports", "https://sports.yahoo.com/nba/rss/"), ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
             ("Sports Illustrated", "https://www.si.com/rss/si_topic/nba")]
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    monkeypatch.setattr(ic, "fetch_text", fake)
    t = time.time()
    items = ic._fetch_current_news_uncached(None, 50, None)
    assert time.time() - t < 0.6   # the slowest feed, not the sum (0.9 s)
    assert [i["headline"] for i in items] == [f"f{k} headline {i}" for k in (0, 2, 3, 4, 5) for i in range(3)]
    assert [i["source"] for i in items][::3] == ["ESPN", "Google News", "Yahoo Sports", "CBS Sports", "Sports Illustrated"]


# ── /meta/current's live team block (R8-084) ──────────────────────────────────

def test_team_stats_cache_an_empty_answer_not_a_failure(monkeypatch):
    import impact_core as ic
    calls = []

    def fake(season):
        calls.append(season)
        return (season == 9001, None)   # 9001: stats.nba.com answered, nothing played; 9002: it failed
    monkeypatch.setattr(ic, "_fetch_nba_api_team_stats_uncached", fake)
    for season in (9001, 9001, 9002, 9002):
        assert ic.fetch_nba_api_team_stats(season) is None
    assert calls == [9001, 9002, 9002]
    for s in (9001, 9002):
        ic._CACHE["team_stats_nba_api"].pop(s, None)


# ── Guess the Game and Game Replay's list (R8-085) ────────────────────────────

def test_guess_the_game_pool_and_daily_equal_a_fresh_read(cur):
    import impact_core as ic
    from routers import guess_the_game as G
    pool = ic._guess_the_game_pool(cur)
    assert pool == ic._read_guess_the_game_pool(cur) and len(pool) > 7000
    assert ic._guess_the_game_pool(cur) is pool          # kept, not re-read
    d = date(2025, 1, 1)
    assert G._daily(d) == G._daily.__wrapped__(d)


def test_guess_the_game_pool_is_read_once_under_concurrency(cur, monkeypatch):
    import impact_core as ic
    monkeypatch.setattr(ic, "_GUESS_THE_GAME_POOL", [])
    reads = []
    real = ic._read_guess_the_game_pool
    monkeypatch.setattr(ic, "_read_guess_the_game_pool", lambda c: reads.append(1) or real(c))

    def one():
        conn = psycopg2.connect(**DB_CONFIG)
        try:
            ic._guess_the_game_pool(conn.cursor())
        finally:
            conn.close()
    threads = [threading.Thread(target=one) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reads) == 1 and len(ic._GUESS_THE_GAME_POOL) == len(real(cur))


def test_replay_list_is_the_unchanged_query(client):
    from routers import wp_replay as W
    for season in (2021, 2026):
        assert W._replay_list_rows(season) == W._replay_list_rows.__wrapped__(season)
    body = client.get("/games/wp-replay/list", params={"season": 2026}).json()
    assert len(body["games"]) == 1230


# ── Data Quality (R8-060) ─────────────────────────────────────────────────────

def test_two_pass_checks_equal_one_connection():
    import data_quality_lib as Q
    a, b = psycopg2.connect(**DB_CONFIG), psycopg2.connect(**DB_CONFIG)
    try:
        for key in sorted(Q.TWO_PASS):
            assert Q.LIVE[key](a.cursor(), b.cursor()) == Q.LIVE[key](a.cursor()), key
    finally:
        a.close()
        b.close()


# ── Hot Streaks' league list ──────────────────────────────────────────────────

def test_hot_streaks_league_list_equals_the_per_player_mask():
    """The old loop (a season mask per player for the games after the as-of date), copied here."""
    import numpy as np
    from routers import hot_streaks as H
    season, stat, window, as_of = 2025, "pts", 10, date(2025, 1, 15)

    def old():
        lines, prior = H._season_lines(season), H._prior(season)
        rng = np.random.default_rng(season * 100 + window)
        out, skipped = [], 0
        upto = lines[lines.game_date <= as_of]
        for pid, g in upto.groupby("player_id", sort=False):
            if g.game_date.iloc[-1] < as_of - H.timedelta(days=H.RECENT_DAYS):
                continue
            res = H._assess(g, prior.get(int(pid)), stat, window, rng)
            if not res["qualified"]:
                skipped += 1
                continue
            after = lines[(lines.player_id == pid) & (lines.game_date > as_of)]
            res["what_happened_next"] = H._next_games(after, stat, window)
            res["player_id"] = int(pid)
            out.append(res)
        return out, skipped

    new = H._league.__wrapped__(season, stat, window, as_of)
    assert new == old() and sum(r["what_happened_next"] is not None for r in new[0]) > 100
