"""
test_paper_ablations.py
========================
Guards round 5 step 7, the ablations (scripts/paper_ablations.py ->
paper_ablation_predictions, paper_ablation_shot_games, paper_ablation_metrics,
paper_ablation_tests, paper_ablation_meta, paper/tables/ablations.tex):

  * the full models the ablations are measured against are paper_eval's: the
    stored full-model rows equal paper_eval_predictions (RAPM next-season games
    and reliability pairs, the pre-game model, the simulator at the halfway
    date), and the expected-FG full model's per-game loss sums add up to
    paper_eval_metrics' log loss and Brier;
  * every stored metric re-derives from the stored unit rows (games, players,
    team-seasons, or per-game shot sums), and every test row's two values are
    those metrics;
  * nothing is chosen on the validation or test season: every re-chosen
    lambda and prior scale is the minimum of the tune-phase candidates stored
    beside it;
  * (task, base, ablation, phase, variant, season, unit_id) is unique (the
    predictions table has no primary key);
  * the table rows the paper prints are the script's TABLE_ROWS, stored in
    paper_ablation_meta, each with a test row in every phase it shows, and the
    table file (untracked; skipped when absent) uses only macros that
    paper/numbers.tex defines.

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_ablations.py
"""

import json
import os
import re
import sys

import numpy as np
import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402

TABLE_FILE = os.path.join(_ROOT, "paper", "tables", "ablations.tex")
NUMBERS_FILE = os.path.join(_ROOT, "paper", "numbers.tex")
TABLE_KEYS = ("block", "task", "base", "ablation", "metric", "variant", "label", "stem", "scale", "decimals")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    c.execute("SELECT to_regclass('paper_ablation_tests') IS NOT NULL AND to_regclass('paper_ablation_meta') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("paper_ablations.py has not been run")
    yield c
    conn.close()


@pytest.fixture(scope="module")
def meta(cur):
    cur.execute("SELECT key, value, note FROM paper_ablation_meta")
    return {k: (v, n) for k, v, n in cur.fetchall()}


def _rows(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def _clip(p):
    return np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)


def test_predictions_key_is_unique(cur):
    n, distinct = _rows(cur, """SELECT count(*), count(DISTINCT (task, base, ablation, phase, variant, season, unit_id))
                                FROM paper_ablation_predictions""")[0]
    assert n == distinct and n > 0


def test_full_models_are_paper_evals(cur):
    checks = [
        ("impact_next", "rapm_single", "impact_next", "rapm_single", ""),
        ("impact_next", "rapm_prior", "impact_next", "rapm_prior", ""),
        ("impact_reliability", "rapm_single", "impact_reliability", "rapm_single", ""),
        ("impact_reliability", "rapm_prior", "impact_reliability", "rapm_prior", ""),
        ("pregame", "pregame", "pregame", "prior_rest", ""),
        ("sim_playoffs", "sim", "sim_playoffs", "model", "halfway"),
        ("sim_wins", "sim", "sim_wins", "model", "halfway"),
    ]
    for task, base, e_task, e_model, variant in checks:
        n_mine, n_join, dev = _rows(cur, """
            SELECT (SELECT count(*) FROM paper_ablation_predictions WHERE task = %(t)s AND base = %(b)s AND ablation = 'full'),
                   count(*), max(greatest(abs(a.pred - e.pred), abs(a.actual - e.actual),
                                          coalesce(abs(a.lo - e.lo), 0), coalesce(abs(a.hi - e.hi), 0)))
            FROM paper_ablation_predictions a
            JOIN paper_eval_predictions e ON e.task = %(et)s AND e.model = %(em)s AND e.variant = %(v)s
                 AND e.phase = a.phase AND e.season = a.season AND e.unit_id = a.unit_id
            WHERE a.task = %(t)s AND a.base = %(b)s AND a.ablation = 'full'""",
                                   {"t": task, "b": base, "et": e_task, "em": e_model, "v": variant})[0]
        n_eval = _rows(cur, "SELECT count(*) FROM paper_eval_predictions WHERE task = %s AND model = %s AND variant = %s",
                       (e_task, e_model, variant))[0][0]
        assert n_mine == n_join == n_eval > 0, (task, base, n_mine, n_join, n_eval)
        assert dev < 1e-9, (task, base, dev)
    # The no-flags pre-game model is paper_eval's 'prior' form.
    dev = _rows(cur, """SELECT max(abs(a.pred - e.pred)), count(*) FROM paper_ablation_predictions a
                        JOIN paper_eval_predictions e ON e.task = 'pregame' AND e.model = 'prior' AND e.phase = a.phase
                             AND e.season = a.season AND e.unit_id = a.unit_id
                        WHERE a.task = 'pregame' AND a.ablation = 'no_b2b'""")[0]
    assert dev[0] < 1e-9 and dev[1] > 0


def test_xfg_full_sums_match_paper_eval(cur):
    for phase in ("validate", "test"):
        shots, ll, br = _rows(cur, """SELECT sum(shots), sum(sum_log_loss::numeric), sum(sum_brier::numeric)
                                      FROM paper_ablation_shot_games WHERE ablation = 'full' AND phase = %s""", (phase,))[0]
        ref = dict(_rows(cur, """SELECT metric, value FROM paper_eval_metrics WHERE task = 'xfg' AND model = 'hgb' AND phase = %s
                                 AND metric IN ('log_loss', 'brier')""", (phase,)))
        n_ref = _rows(cur, "SELECT count(*) FROM paper_eval_predictions WHERE task = 'xfg' AND model = 'hgb' AND phase = %s", (phase,))[0][0]
        assert shots == n_ref
        assert float(ll) / shots == pytest.approx(ref["log_loss"], abs=1e-9)
        assert float(br) / shots == pytest.approx(ref["brier"], abs=1e-9)
    # every ablation is scored on the same games with the same shot counts as the full model
    bad = _rows(cur, """SELECT count(*) FROM paper_ablation_shot_games a
                        LEFT JOIN paper_ablation_shot_games f ON f.ablation = 'full' AND f.phase = a.phase AND f.game_id = a.game_id
                        WHERE a.ablation <> 'full' AND (f.shots IS NULL OR f.shots <> a.shots)""")[0][0]
    assert bad == 0


def test_metrics_rederive_from_rows(cur):
    metrics = _rows(cur, "SELECT task, base, ablation, phase, variant, seasons, metric, value, n FROM paper_ablation_metrics")
    assert metrics
    preds = {}
    for task, base, abl, phase, variant, season, pred, actual, lo, hi in _rows(cur, """
            SELECT task, base, ablation, phase, variant, season, pred, actual, lo, hi FROM paper_ablation_predictions"""):
        preds.setdefault((task, base, abl, phase, variant), []).append((season, pred, actual, lo, hi))
    shot = {}
    for abl, phase, season, n, ll, br in _rows(cur, "SELECT ablation, phase, season, shots, sum_log_loss, sum_brier FROM paper_ablation_shot_games"):
        shot.setdefault((abl, phase), []).append((season, n, ll, br))
    label = {s: f"{s - 1}-{str(s)[-2:]}" for s in range(2011, 2027)}
    for task, base, abl, phase, variant, seasons, metric, value, n in metrics:
        if task == "xfg":
            g = np.array([r for r in shot[(abl, phase)] if label[r[0]] == seasons], float)
            if metric == "roc_auc":
                continue          # needs the per-shot rows, which are not stored
            assert int(g[:, 1].sum()) == n
            col = 2 if metric == "log_loss" else 3
            assert g[:, col].sum() / g[:, 1].sum() == pytest.approx(value, abs=1e-9), (task, abl, phase, metric)
            continue
        rows = preds[(task, base, abl, phase, variant)]
        first, last = seasons.split(" to ") if " to " in seasons else (seasons, seasons)
        lo_s, hi_s = int(first[:4]) + 1, int(last[:4]) + 1
        r = np.array([x for x in rows if lo_s <= x[0] <= hi_s], float)
        assert len(r) == n, (task, base, abl, phase, seasons, metric)
        p, a = r[:, 1], r[:, 2]
        if metric in ("game_rmse", "rmse"):
            v = np.sqrt(np.mean((p - a) ** 2))
        elif metric in ("game_corr", "corr"):
            v = np.corrcoef(p, a)[0, 1]
        elif metric == "log_loss":
            q = _clip(p)
            v = -np.mean(a * np.log(q) + (1 - a) * np.log(1 - q))
        elif metric == "brier":
            v = np.mean((p - a) ** 2)
        elif metric == "mae":
            v = np.mean(np.abs(p - a))
        elif metric == "cover80":
            v = np.mean((a >= r[:, 3]) & (a <= r[:, 4]))
        else:
            raise AssertionError(metric)
        assert v == pytest.approx(value, abs=1e-9), (task, base, abl, phase, seasons, metric)


def test_test_rows_carry_the_metrics(cur):
    rows = _rows(cur, """SELECT t.task, t.phase, t.metric, t.model_a, t.model_b, t.value_a, t.value_b, t.diff, t.ci_lo, t.ci_hi, t.p_boot,
                                t.resamples, ma.value, mb.value
                         FROM paper_ablation_tests t
                         LEFT JOIN paper_ablation_metrics ma ON ma.task = t.task AND ma.base || ':' || ma.ablation = t.model_a
                              AND ma.phase = t.phase AND ma.variant = t.variant AND ma.seasons = t.seasons AND ma.metric = t.metric
                         LEFT JOIN paper_ablation_metrics mb ON mb.task = t.task AND mb.base || ':' || mb.ablation = t.model_b
                              AND mb.phase = t.phase AND mb.variant = t.variant AND mb.seasons = t.seasons AND mb.metric = t.metric""")
    assert len(rows) > 300
    for task, phase, metric, a, b, va, vb, diff, lo, hi, p, res, ma, mb in rows:
        assert b.endswith(":full") and a.split(":")[0] == b.split(":")[0]
        assert ma is not None and mb is not None, (task, phase, metric, a)
        assert va == pytest.approx(ma, abs=1e-9) and vb == pytest.approx(mb, abs=1e-9), (task, phase, metric, a)
        assert diff == pytest.approx(va - vb, abs=1e-12) and lo <= hi and 0 <= p <= 1 and res == 10_000


def test_rechosen_hyperparameters_are_tune_minima(meta):
    keys = [k for k in meta if re.fullmatch(r"impact:no_[a-z_]+:(lambda|prior_scale)", k)]
    assert len(keys) == 6
    for k in keys:
        value, note = meta[k]
        assert "tune pairs" in note
        cands = json.loads(note.split("candidates ", 1)[1])
        best = min(cands, key=lambda c: c["game_rmse"])
        assert best["lambda" if k.endswith(":lambda") else "prior_scale"] == value, k


def test_table_spec_and_file(cur, meta):
    import paper_ablations as A
    spec = meta["table:rows"][0]
    assert spec == [dict(zip(TABLE_KEYS, r)) for r in A.TABLE_ROWS]
    stems = [r["stem"] for r in spec]
    assert len(set(stems)) == len(stems) and all(re.fullmatch(r"[A-Z][A-Za-z]*", s) for s in stems)
    for r in spec:
        phases = ("validate", "test") if r["task"] == "xfg" else ("tune", "validate", "test")
        for ph in phases:
            n = _rows(cur, """SELECT count(*) FROM paper_ablation_tests WHERE task = %s AND phase = %s AND metric = %s AND model_a = %s
                              AND variant = %s""", (r["task"], ph, r["metric"], f"{r['base']}:{r['ablation']}", r["variant"]))[0][0]
            assert n == 1, (r["stem"], ph)
    if not (os.path.exists(TABLE_FILE) and os.path.exists(NUMBERS_FILE)):
        pytest.skip("paper/ is not on this machine")
    used = set(re.findall(r"\\(pn[A-Za-z]+)", open(TABLE_FILE).read()))
    defined = set(re.findall(r"\\newcommand\{\\(pn[A-Za-z]+)\}", open(NUMBERS_FILE).read()))
    assert used and used <= defined, sorted(used - defined)
