"""
paper_figures.py
=================
The conference paper's figures (paper/figures/*.pdf), drawn from the same
database rows its tables and numbers come from (round 5 step 8). Vector PDF,
IEEE column width (3.5 in) or full width (7.16 in), 8 pt STIX (Times-like)
type with the fonts embedded as TrueType, no titles inside the figures (the
captions carry them), readable in grayscale: phases differ by marker shape
and fill as well as by colour.

  fig_pipeline.pdf     sources -> parser -> reconciled stints and lines -> models,
                       and the evaluation protocol's seasons (Section: Data, Methods)
  fig_forest.pdf       every paired difference of the protocol that the paper
                       states, in all three phases, with its 95% paired
                       cluster-bootstrap interval (paper_eval_tests; Results)
  fig_calibration.pdf  the expected-FG model's and the pre-game model's
                       reliability on the test season (paper_eval_predictions)
  fig_rapm_sens.pdf    RAPM + prior's next-season RMSE as its prior scale and
                       penalty move off the chosen values, per phase, against
                       BPM (paper_ablation_tests, paper_eval_tests; Ablations)
  fig_streaks.pdf      the Miller-Sanjurjo check: each hot-streak family's
                       observed persistence share against its within-season
                       shuffled null (paper_beliefs_summary; Popular beliefs)
  fig_possessions.pdf  points per possession by how it began, and after a
                       defensive rebound by the second of the first attempt
                       (possessions, possession_seasons, possession_meta; round 6)
  fig_quality.pdf      three headline differences with every game, without the
                       flagged games, and under random drops of as many games
                       (data_quality_sensitivity; round 6 step 11)
  fig_reportcard.pdf   four pairs season by season with the random-effects pool
                       and the prediction interval (report_card_tests/_pooled;
                       round 6 step 10)

Same data as the text, checked: the script first builds the paper's macros
in-process with paper_numbers.build() (read-only, ~4 s) and then, for every
plotted number that the paper also prints, checks that the plotted value
rounds (with paper_numbers' own formatter) to the printed macro. Any mismatch
stops the run and names the macro, so a figure can never disagree with the
sentence or table beside it. Labels in the figures that carry a number
(season spans, counts in the pipeline) are the macros' own text. If
paper/numbers.tex on disk differs from the freshly built macros the run says
so (rerun paper_numbers.py) but still draws from the database.

Judgment calls (stated in the captions too):
  * forest panel D puts the simulator's differences on one axis as a share of
    the record baseline's own value on the full sample (the stored interval
    divided by the same constant), and turns 80% range coverage into the
    share of misses, so that every row reads "negative favours the simulator";
  * differences that fall off an axis are drawn as an arrow at the edge with
    their printed value (on/off as published against zero);
  * the pre-game calibration bins carry Wilson 95% intervals (one row per
    game, so the game-clustered bootstrap reduces to the binomial); the
    expected-FG bins, with tens of thousands of shots each, carry none, and
    bins holding under 1% of the season's shots (the rule the paper's largest
    calibration gap uses) are drawn open.

Deterministic: no random draws; PDFs are written without creation dates, so
two runs give byte-identical files (the test checks). Read-only on the
database.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_figures.py                      # writes ../paper/figures/*.pdf
    cd scripts && python3 paper_figures.py --only forest,streaks
    cd scripts && python3 paper_figures.py --png $TMPDIR/figs   # also 300 dpi PNG previews there
"""

import argparse
import math
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

import paper_numbers as PN  # noqa: E402  (import-safe: its work is in main())

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "paper", "figures")

COL_W, FULL_W = 3.5, 7.16            # IEEE column and text width, inches
FONT_PT = 8

PHASES = (("tune", "Tune"), ("validate", "Val"), ("test", "Test"))
# Grayscale-safe: shape and fill differ as well as colour; the test season, scored once, is solid black.
STYLE = {
    "tune": dict(marker="o", color="#8c8c8c", mfc="white", ms=3.6, label="Tuning seasons"),
    "validate": dict(marker="s", color="#2b6cb0", mfc="white", ms=3.4, label="Validation season"),
    "test": dict(marker="D", color="#000000", mfc="#000000", ms=3.2, label="Test season"),
}
OFFSET = {"tune": 0.26, "validate": 0.0, "test": -0.26}

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["STIXGeneral"], "mathtext.fontset": "stix",
    "font.size": FONT_PT, "axes.labelsize": FONT_PT, "xtick.labelsize": FONT_PT, "ytick.labelsize": FONT_PT,
    "legend.fontsize": FONT_PT, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "lines.linewidth": 0.9, "axes.spines.top": False,
    "axes.spines.right": False, "pdf.fonttype": 42, "savefig.dpi": 300, "axes.unicode_minus": True,
    "svg.hashsalt": "paper_figures",
})
MINUS = "\u2212"


# ---------------------------------------------------------------- macros and checks

def parse_macros(text):
    """{name: value} from numbers.tex text (values may hold nested braces)."""
    out = {}
    for m in re.finditer(r"\\newcommand\{\\(pn[A-Za-z]+)\}\{", text):
        i, depth = m.end(), 1
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out[m.group(1)] = text[m.end():i - 1]
    return out


def plain(tex):
    """A macro's LaTeX text as figure text: 7{,}220 -> 7,220, 2020--21 -> 2020-21 (en dash), minus signs."""
    s = tex.replace("{,}", ",").replace("\\ensuremath{-}", MINUS).replace("--", "\u2013").replace("\\%", "%")
    s = re.sub(r"\\ensuremath\{([^}]*)\}", r"\1", s)
    if "\\" in s or "{" in s:
        raise ValueError(f"macro text {tex!r} has markup plain() can't turn into figure text")
    return s


class Checks:
    """Every plotted number the paper also prints must round to the printed macro."""

    def __init__(self, macros):
        self.M = macros
        self.n = 0
        self.bad = []

    def text(self, name):
        return plain(self.M["pn" + name])

    def has(self, name):
        return ("pn" + name) in self.M

    def expect(self, name, value, d, scale=1, optional=False, fmt=None):
        full = "pn" + name
        if full not in self.M:
            if not optional:
                self.bad.append(f"{full}: not defined by paper_numbers")
            return
        got = (fmt or PN.dec)(value * scale, d)
        self.n += 1
        if got != self.M[full]:
            self.bad.append(f"{full}: the figure plots {value!r} (-> {got}) but the paper prints {self.M[full]}")

    def claim(self, ok, what):
        self.n += 1
        if not ok:
            self.bad.append(what)


def rows(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def tests_table(cur, table):
    cols = ("task", "phase", "metric", "model_a", "model_b", "variant", "diff", "ci_lo", "ci_hi", "value_a", "value_b")
    T = {}
    for r in rows(cur, f"SELECT {', '.join(cols)} FROM {table}"):
        r = dict(zip(cols, r))
        T[(r["task"], r["phase"], r["metric"], r["model_a"], r["model_b"], r["variant"])] = r
    return T


def save(fig, out_dir, name, png_dir):
    path = os.path.join(out_dir, name + ".pdf")
    fig.savefig(path, format="pdf", metadata={"Creator": "scripts/paper_figures.py", "Producer": None,
                                              "CreationDate": None, "ModDate": None})
    if png_dir:
        fig.savefig(os.path.join(png_dir, name + ".png"), format="png", dpi=300)
    plt.close(fig)
    return path


def phase_legend(fig, y, ncol=3, handles_extra=(), short=False):
    h = [Line2D([], [], ls="none", marker=s["marker"], color=s["color"], mfc=s["mfc"], ms=s["ms"] + 0.6, mew=0.8,
                label=s["label"].split()[0] if short else s["label"]) for s in (STYLE[p] for p, _ in PHASES)]
    fig.legend(handles=h + list(handles_extra), loc="upper center", bbox_to_anchor=(0.5, y), ncol=ncol + len(handles_extra),
               frameon=False, handletextpad=0.3, columnspacing=1.2, borderaxespad=0)


# ---------------------------------------------------------------- figure: pipeline and protocol

def fig_pipeline(cur, C, out_dir, png_dir):
    t = C.text
    fig = plt.figure(figsize=(COL_W, 2.6))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 74.3)
    ax.axis("off")

    def box(x, y, w, h, s, dashed=False, fc="white", color="black"):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2", lw=0.6, ec="black",
                                    fc=fc, ls=(0, (2.5, 1.5)) if dashed else "-"))
        if s:
            ax.text(x + w / 2, y + h / 2, s, ha="center", va="center", linespacing=1.15, color=color)

    def arrow(x0, y0, x1, y1):
        ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=6, lw=0.6, color="black",
                                     shrinkA=0, shrinkB=0))

    # the public sources, audited together (dashed outline)
    box(0.5, 53.5, 99, 20.3, "", dashed=True)
    box(2, 60, 30.5, 12.3, f"ESPN play-by-play\n{t('EspnEventsMillion')}M events")
    box(34.75, 60, 30.5, 12.3, f"ESPN scoreboard\n{t('GameScoreRows')} team-games")
    box(67.5, 60, 30.5, 12.3, f"NBA shot chart\n{t('ShotsMillion')}M located shots")
    ax.text(50, 56.6, f"Data-quality audit: {t('DqClasses')} error classes, each re-measured", ha="center", va="center")
    # the parser, which reconciles every game to its real final score
    box(2, 37, 70, 12.3, f"One parser, one replay per game: stints, possessions,\ngame lines, corrected clock; "
                            f"{t('GamesReconciled')} of {t('GamesParsed')} reconcile")
    arrow(37, 53.5, 37, 49.3)
    # the models
    xs = (2, 26.5, 51, 75.5)
    w = 22.5
    for x, s in zip(xs, ("RAPM, tracker,\nxRAPM, BPM", "Popular\nbeliefs", "Pre-game odds,\nsimulator", "Expected FG\n(per shot)")):
        box(x, 17, w, 13, s)
    for x in xs[:3]:
        arrow(x + w / 2, 37, x + w / 2, 30)
    arrow(xs[3] + w / 2, 53.5, xs[3] + w / 2, 30)
    # the protocol's seasons: history (training only) | tune | validate | test
    y0, h = 1.2, 10.3
    segs = ((1, 30, "History\n(training only)", "white"),
            (31, 36, f"Tune\n{t('EvTuneFirst')} to {t('EvTuneLast')}", "#e6e6e6"),
            (67, 16, f"Validate\n{t('EvValidate')}", "#bdbdbd"),
            (83, 16, f"Test\n{t('EvTest')}", "#404040"))
    for x, wd, s, fc in segs:
        ax.add_patch(plt.Rectangle((x, y0), wd, h, lw=0.6, ec="black", fc=fc))
        ax.text(x + wd / 2, y0 + h / 2, s, ha="center", va="center", linespacing=1.1,
                color="white" if fc == "#404040" else "black")
    ax.text(50, 14.2, "Evaluation protocol: one split for every model", ha="center", va="center",
            bbox=dict(fc="white", ec="none", pad=0.4), zorder=3)
    for x in xs:
        ax.plot([x + w / 2] * 2, [17, y0 + h], color="black", lw=0.6, ls=(0, (1, 1.2)), zorder=0)
    return save(fig, out_dir, "fig_pipeline", png_dir)


# ---------------------------------------------------------------- figure: forest plot of paired differences

FOREST = {
    "A": dict(xlabel="$\\Delta$ next-season game-margin RMSE (points)", scale=1, xlim=(-0.62, 1.22), rows=[
        ("RAPM + prior $-$ BPM", ("impact_next", "game_rmse", "rapm_prior", "bpm", ""), "EvDNext{P}PriorBpmRmse", 2),
        ("RAPM, {multi} seasons $-$ BPM", ("impact_next", "game_rmse", "rapm_multi", "bpm", ""), "EvDNext{P}MultiBpmRmse", 2),
        ("RAPM, one season $-$ RAPM + prior", ("impact_next", "game_rmse", "rapm_single", "rapm_prior", ""), "EvDNext{P}SinglePriorRmse", 2),
        ("xRAPM $-$ RAPM, both one season", ("impact_next", "game_rmse", "xrapm_single", "rapm_single", ""), "EvDNext{P}XsingleSingleRmse", 2),
        ("xRAPM $-$ RAPM, both with prior", ("impact_next", "game_rmse", "xrapm_prior", "rapm_prior", ""), "EvDNext{P}XpriorPriorRmse", 2),
        ("On/off $\\times$ {onoff} $-$ zero", ("impact_next", "game_rmse", "onoff_scaled", "zero", ""), "EvDNext{P}OnoffScaledZeroRmse", 2),
        ("On/off (as published) $-$ zero", ("impact_next", "game_rmse", "onoff", "zero", ""), "EvDNext{P}OnoffZeroRmse", 2),
        ("Rating Tracker $-$ BPM", ("impact_next", "game_rmse", "rapm_tracker", "bpm", ""), "EvDNext{P}TrackerBpmRmse", 2),
        ("xRAPM-SA $-$ xRAPM, both one season", ("impact_next", "game_rmse", "xrapm_sa_single", "xrapm_single", ""), "EvDNext{P}XsaSingleXsingleRmse", 2),
    ]),
    "B": dict(xlabel="$\\Delta$ year-to-year correlation $r$", scale=1, xlim=(-0.12, 0.52), rows=[
        ("BPM $-$ RAPM + prior", ("impact_reliability", "corr", "bpm", "rapm_prior", ""), "EvDYty{P}BpmPrior", 2),
        ("RAPM + prior $-$ RAPM, one season", ("impact_reliability", "corr", "rapm_prior", "rapm_single", ""), "EvDYty{P}PriorSingle", 2),
        ("RAPM, one season $-$ on/off", ("impact_reliability", "corr", "rapm_single", "onoff", ""), "EvDYty{P}SingleOnoff", 2),
        ("xRAPM $-$ RAPM, both with prior", ("impact_reliability", "corr", "xrapm_prior", "rapm_prior", ""), "EvDYty{P}XpriorPrior", 2),
        ("xRAPM $-$ RAPM, both one season", ("impact_reliability", "corr", "xrapm_single", "rapm_single", ""), "EvDYty{P}XsingleSingle", 2),
        ("Shot quality $-$ shot-making", ("xfg_reliability", "corr", "quality", "shot_making", "fga>=200"), "EvDXfgYty{P}QualityMaking", 2),
        ("BPM $-$ Rating Tracker", ("impact_reliability", "corr", "bpm", "rapm_tracker", ""), "EvDYty{P}BpmTracker", 2),
    ]),
    "C": dict(xlabel="$\\Delta$ log loss ($\\times 10^{-3}$)", scale=1000, xlim=(-22.5, 9.5), rows=[
        ("Pre-game: chosen $-$ this season only", ("pregame", "log_loss", "prior_rest", "current", ""), "EvDPregame{P}ChosenCurrentLogLoss", 4),
        ("Pre-game: chosen $-$ probit baseline", ("pregame", "log_loss", "prior_rest", "baseline", ""), "EvDPregame{P}ChosenBaselineLogLoss", 4),
        ("Pre-game: chosen $-$ no back-to-back", ("pregame", "log_loss", "prior_rest", "prior", ""), "EvDPregame{P}ChosenPriorLogLoss", 4),
        ("Expected FG: boosting $-$ logistic", ("xfg", "log_loss", "hgb", "logreg", ""), "EvDXfg{P}HgbLogregLogLoss", 4),
        ("Expected FG: boosting $-$ zone", ("xfg", "log_loss", "hgb", "zone", ""), "EvDXfg{P}HgbZoneLogLoss", 4),
        ("Expected FG: logistic $-$ zone", ("xfg", "log_loss", "logreg", "zone", ""), "EvDXfg{P}LogregZoneLogLoss", 4),
    ]),
    "D": dict(xlabel="$\\Delta$ as % of the record baseline's value", scale=None, xlim=None, rows=[
        ("Playoff Brier score", ("sim_playoffs", "brier", "model", "record", "halfway"), "EvDSim{P}Brier", 4),
        ("Playoff log loss", ("sim_playoffs", "log_loss", "model", "record", "halfway"), "EvDSim{P}LogLoss", 3),
        ("Win-total MAE", ("sim_wins", "mae", "model", "record", "halfway"), "EvDSim{P}Mae", 2),
        ("Win-total RMSE", ("sim_wins", "rmse", "model", "record", "halfway"), "EvDSim{P}Rmse", 2),
        ("Final totals outside the 80% range", ("sim_wins", "cover80", "model", "record", "halfway"), "EvDSim{P}Cover", 1),
    ]),
}
PANEL_TITLES = {"A": "(a) Player impact: next-season prediction", "B": "(b) Player impact: year-to-year reliability",
                "C": "(c) Pre-game odds and expected FG", "D": "(d) Season simulator at the halfway point"}


def forest_values(T, key, phase, panel):
    """(diff, lo, hi) on the panel's axis, or None where the phase isn't scored (the shot model has no tuning phase)."""
    task, metric, a, b, variant = key
    r = T.get((task, phase, metric, a, b, variant))
    if r is None:
        return None, r
    d, lo, hi = r["diff"], r["ci_lo"], r["ci_hi"]
    if panel == "D":                   # as a share of the record baseline's own full-sample value
        if metric == "cover80":         # coverage -> misses, so negative favours the simulator on every row
            base = 1 - r["value_b"]
            d, lo, hi = -d, -hi, -lo
        else:
            base = r["value_b"]
        return (100 * d / base, 100 * lo / base, 100 * hi / base), r
    s = FOREST[panel]["scale"]
    return (d * s, lo * s, hi * s), r


def fig_forest(cur, C, out_dir, png_dir):
    T = tests_table(cur, "paper_eval_tests")
    fig = plt.figure(figsize=(FULL_W, 3.9))
    # label column inside each panel's left margin
    boxes = {"A": [0.268, 0.585, 0.225, 0.325], "B": [0.768, 0.585, 0.225, 0.325],
             "C": [0.268, 0.115, 0.225, 0.325], "D": [0.768, 0.115, 0.225, 0.325]}
    subs = {"multi": C.text("RapmMultiSeasons"), "onoff": C.text("EvOnoffScale")}
    for panel, spec in FOREST.items():
        ax = fig.add_axes(boxes[panel])
        n = len(spec["rows"])
        ax.axvline(0, color="black", lw=0.6, zorder=1)
        for i in range(n):
            if i % 2 == 0:
                ax.axhspan(-i - 0.5, -i + 0.5, color="#f2f2f2", lw=0, zorder=0)
        if spec["xlim"] is None:          # fit every interval, 5% padding
            vals = [v for _, key, _, _ in spec["rows"] for ph, _ in PHASES
                    for v in (forest_values(T, key, ph, panel)[0] or ())]
            pad = 0.05 * (max(vals) - min(vals))
            spec = dict(spec, xlim=(min(vals) - pad, max(vals) + pad))
        lo_x, hi_x = spec["xlim"]
        for i, (label, key, macro, d) in enumerate(spec["rows"]):
            off_axis = []
            for phase, P in PHASES:
                v, r = forest_values(T, key, phase, panel)
                if v is None:
                    continue
                # the check: the plotted difference and interval are the numbers the paper prints
                C.expect(macro.format(P=P), r["diff"], d, scale=100 if key[1] == "cover80" else 1, optional=True)
                C.expect(macro.format(P=P) + "Lo", r["ci_lo"], d, scale=100 if key[1] == "cover80" else 1, optional=True)
                C.expect(macro.format(P=P) + "Hi", r["ci_hi"], d, scale=100 if key[1] == "cover80" else 1, optional=True)
                x, lo, hi = v
                y = -i + OFFSET[phase]
                st = STYLE[phase]
                if lo > hi_x:           # the whole interval is off the right edge: an arrow and the printed value
                    ax.plot([hi_x], [y], ls="none", marker=">", color=st["color"], mfc=st["mfc"], ms=3.2, mew=0.8,
                            clip_on=False, zorder=3)
                    off_axis.append(C.text(macro.format(P=P)))
                    continue
                ax.plot([max(lo, lo_x), min(hi, hi_x)], [y, y], color=st["color"], lw=0.9, zorder=2, solid_capstyle="butt")
                ax.plot([x], [y], ls="none", marker=st["marker"], color=st["color"], mfc=st["mfc"], ms=st["ms"], mew=0.8, zorder=3)
            if off_axis:
                ax.text(hi_x - 0.05 * (hi_x - lo_x), -i + 0.02, "$\\Delta$ = " + ", ".join(off_axis) + " off axis", ha="right", va="center",
                        bbox=dict(fc="white", ec="none", pad=0.6), zorder=4)
        ax.set_yticks([-i for i in range(n)])
        ax.set_yticklabels([lab.format(**subs) for lab, *_ in spec["rows"]])
        ax.tick_params(axis="y", length=0)
        ax.set_ylim(-n + 0.45, 0.55)
        ax.set_xlim(lo_x, hi_x)
        ax.spines["left"].set_visible(False)
        ax.set_xlabel(spec["xlabel"], labelpad=1.5)
        fig.text(boxes[panel][0] - 0.255, boxes[panel][1] + boxes[panel][3] + 0.012, PANEL_TITLES[panel],
                 ha="left", va="bottom", fontweight="bold")
    phase_legend(fig, 0.995)
    return save(fig, out_dir, "fig_forest", png_dir)


# ---------------------------------------------------------------- figure: calibration on the test season

def wilson(k, n, z=1.959963984540054):
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return c - h, c + h


def calib_bins(p, y):
    bins = np.minimum((p * 10).astype(int), 9)
    n = np.bincount(bins, minlength=10).astype(float)
    sp = np.bincount(bins, weights=p, minlength=10)
    sy = np.bincount(bins, weights=y, minlength=10)
    return n, sp, sy


def fig_calibration(cur, C, out_dir, png_dir):
    import paper_tests as PT        # the calibration's own constants (clip, the 1% rule)
    T = tests_table(cur, "paper_eval_tests")
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.95))
    fig.subplots_adjust(left=0.13, right=0.985, bottom=0.2, top=0.97, wspace=0.42)

    # (a) expected FG, test season: the chosen boosting model, ten fixed-width bins as in paper_tests.calibration_rows
    ax = axes[0]
    xr = rows(cur, "SELECT pred, actual FROM paper_eval_predictions WHERE task = 'xfg' AND phase = 'test' AND model = 'hgb' "
                   "ORDER BY unit_id")
    p = PT.clip(np.array([r[0] for r in xr], float))
    y = np.array([r[1] for r in xr], float)
    n, sp, sy = calib_bins(p, y)
    ok = n > 0
    gap = np.where(ok, np.abs(sy / np.where(ok, n, 1) - sp / np.where(ok, n, 1)), 0.0)
    ece = float((gap * n).sum() / n.sum())
    big = n >= PT.CALIB_MIN_SHARE * n.sum()
    mx = float(np.where(big, gap, 0.0).max())
    tr = [r for k, r in T.items() if k[:4] == ("xfg", "test", "ece", "hgb")][0]
    C.claim(abs(ece - tr["value_a"]) < 1e-12, f"calibration: the plotted ECE {ece} is paper_eval_tests' {tr['value_a']}")
    C.expect("EvXfgTestEce", ece, 3)
    C.expect("EvXfgTestMaxGap", mx, 3)
    ax.plot([0, 1], [0, 1], color="#8c8c8c", lw=0.6, ls=(0, (3, 2)), zorder=1)
    mp, obs = sp[ok] / n[ok], sy[ok] / n[ok]
    for xx, yy, b in zip(mp, obs, big[ok]):
        ax.plot([xx], [yy], ls="none", marker="o", ms=3.4, mew=0.8, color="black", mfc="black" if b else "white", zorder=3)
    ax.plot(mp[big[ok]], obs[big[ok]], color="black", lw=0.7, zorder=2)
    ax.set_xlabel("Predicted make probability", labelpad=1.5)
    ax.set_ylabel("Observed make rate", labelpad=1.5)
    ax.text(0.03, 0.97, "(a) Expected FG", transform=ax.transAxes, ha="left", va="top")

    # (b) pre-game odds, test season: the chosen form, P(home team wins)
    ax = axes[1]
    gr = rows(cur, "SELECT pred, actual FROM paper_eval_predictions WHERE task = 'pregame' AND phase = 'test' "
                   "AND model = 'prior_rest' ORDER BY unit_id")
    p = np.array([r[0] for r in gr], float)
    y = np.array([r[1] for r in gr], float)
    ll = float(np.mean(-(y * np.log(PT.clip(p)) + (1 - y) * np.log(1 - PT.clip(p)))))
    C.expect("EvPregameTestPriorRestLogLoss", ll, 4)
    C.expect("EvPregameTestFavWinPct", float(np.mean(np.where(p >= 0.5, y, 1 - y))), 1, scale=100)
    C.expect("EvPregameTestGames", len(p), 0, fmt=lambda v, d: PN.integer(v))
    n, sp, sy = calib_bins(p, y)
    ax.plot([0, 1], [0, 1], color="#8c8c8c", lw=0.6, ls=(0, (3, 2)), zorder=1)
    ok = n > 0
    for i in np.flatnonzero(ok):
        lo, hi = wilson(sy[i], n[i])
        ax.plot([sp[i] / n[i]] * 2, [lo, hi], color="black", lw=0.7, zorder=2)
    ax.plot(sp[ok] / n[ok], sy[ok] / n[ok], ls="-", lw=0.7, marker="D", ms=3.0, color="black", zorder=3)
    ax.set_xlabel("Predicted P(home team wins)", labelpad=1.5)
    ax.set_ylabel("Observed home win rate", labelpad=1.5)
    ax.text(0.03, 0.97, "(b) Pre-game odds", transform=ax.transAxes, ha="left", va="top")
    for a in axes:
        a.set_xlim(-0.02, 1.04)
        a.set_ylim(-0.02, 1.04)
        a.set_xticks([0, 0.25, 0.5, 0.75, 1])
        a.set_xticklabels(["0", ".25", ".5", ".75", "1"])
        a.set_yticks([0, 0.25, 0.5, 0.75, 1])
        a.set_yticklabels(["0", ".25", ".5", ".75", "1"])
        a.set_aspect("equal")
    return save(fig, out_dir, "fig_calibration", png_dir)


# ---------------------------------------------------------------- figure: RAPM + prior's sensitivity to its two settings

def fig_rapm_sens(cur, C, out_dir, png_dir):
    A = tests_table(cur, "paper_ablation_tests")
    T = tests_table(cur, "paper_eval_tests")
    chosen_scale = float(plain(C.M["pnEvPriorScale"]))
    chosen_lam = float(plain(C.M["pnEvLambdaSingle"]).replace(",", ""))
    grid = {"scale": [], "lambda": []}
    for (task, phase, metric, a, b, v) in A:
        if task == "impact_next" and metric == "game_rmse" and a.startswith("rapm_prior:") and phase == "test":
            kind, _, val = a.split(":")[1].partition("=")
            if kind in grid:
                grid[kind].append(float(val))
    C.claim(chosen_scale not in grid["scale"] and chosen_lam not in grid["lambda"], "the chosen settings are the full model, not a grid point")
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.9), sharey=True)
    fig.subplots_adjust(left=0.135, right=0.985, bottom=0.21, top=0.86, wspace=0.08)
    xoff = {"scale": {"tune": -0.035, "validate": 0.0, "test": 0.035}, "lambda": {"tune": 0.94, "validate": 1.0, "test": 1.064}}
    ylim = (-0.5, 1.02)
    for ax, kind in zip(axes, ("scale", "lambda")):
        xs = sorted(grid[kind] + [chosen_scale if kind == "scale" else chosen_lam])
        ax.axhline(0, color="black", lw=0.6, zorder=1)
        for phase, P in PHASES:
            st = STYLE[phase]
            bpm = -T[("impact_next", phase, "game_rmse", "rapm_prior", "bpm", "")]["diff"]   # BPM minus the chosen model
            ax.axhline(bpm, color=st["color"], lw=0.7, ls=(0, (4, 2)) if phase != "test" else (0, (1.5, 1.2)), zorder=1)
            pts = []
            for x in xs:
                if x == (chosen_scale if kind == "scale" else chosen_lam):
                    pts.append((x, 0.0, 0.0, 0.0))
                    continue
                name = f"rapm_prior:{kind}={x:g}"
                r = A[("impact_next", phase, "game_rmse", name, "rapm_prior:full", "")]
                pts.append((x, r["diff"], r["ci_lo"], r["ci_hi"]))
            px = [x + xoff[kind][phase] if kind == "scale" else x * xoff[kind][phase] for x, *_ in pts]
            ax.plot(px, [d for _, d, _, _ in pts], color=st["color"], lw=0.7, zorder=2)
            for (x, d, lo, hi), xx in zip(pts, px):
                if lo != hi:
                    ax.plot([xx, xx], [max(lo, ylim[0]), min(hi, ylim[1])], color=st["color"], lw=0.7, zorder=2)
                if d > ylim[1]:
                    ax.plot([xx], [ylim[1]], ls="none", marker="^", color=st["color"], mfc=st["mfc"], ms=3.0, mew=0.8,
                            clip_on=False, zorder=3)
                else:
                    ax.plot([xx], [d], ls="none", marker=st["marker"], color=st["color"], mfc=st["mfc"], ms=st["ms"] - 0.4,
                            mew=0.8, zorder=3)
            # the check: the points the Ablations subsection quotes
            if kind == "scale":
                r0 = A[("impact_next", phase, "game_rmse", "rapm_prior:scale=0", "rapm_prior:full", "")]
                r1 = A[("impact_next", phase, "game_rmse", "rapm_prior:scale=1", "rapm_prior:full", "")]
                C.expect(f"AbPriorNone{P}", r0["diff"], 2)
                C.expect(f"AbPriorFull{P}", r1["diff"], 2)
            else:
                rh = A[("impact_next", phase, "game_rmse", "rapm_prior:lambda=12000", "rapm_prior:full", "")]
                C.expect(f"AbLamHigh{P}", rh["diff"], 2)
            C.expect(f"EvDNext{P}PriorBpmRmse", -bpm, 2)
        ax.set_ylim(*ylim)
        if kind == "scale":
            ax.set_xlabel("Prior scale", labelpad=1.5)
            ax.set_xticks([0, 0.5, 1, 1.5, 2])
            ax.set_xticklabels(["0", "0.5", "1", "1.5", "2"])
            ax.set_ylabel("$\\Delta$ RMSE vs. chosen (points)", labelpad=1.5)
            ax.text(0.03, 0.03, "(a)", transform=ax.transAxes, ha="left", va="bottom")
        else:
            ax.set_xscale("log")
            ax.set_xlabel("Penalty $\\lambda$", labelpad=1.5)
            ax.set_xticks([300, 1000, 3000, 10000, 30000, 100000])
            ax.set_xticklabels(["300", "1k", "3k", "10k", "30k", "100k"])
            ax.minorticks_off()
            ax.text(0.03, 0.03, "(b)", transform=ax.transAxes, ha="left", va="bottom")
        ax.axvline(chosen_scale if kind == "scale" else chosen_lam, color="black", lw=0.5, ls=(0, (1, 1.5)), zorder=0)
    # Caption: "the setting that minimises error differs between phases, and no setting reaches BPM on the test season"
    for kind, chosen in (("scale", chosen_scale), ("lambda", chosen_lam)):
        best = {}
        for phase, _ in PHASES:
            d = {x: A[("impact_next", phase, "game_rmse", f"rapm_prior:{kind}={x:g}", "rapm_prior:full", "")]["diff"] for x in grid[kind]}
            d[chosen] = 0.0
            best[phase] = min(d, key=d.get)
            if phase == "test":
                bpm_test = -T[("impact_next", "test", "game_rmse", "rapm_prior", "bpm", "")]["diff"]
                C.claim(min(d.values()) > bpm_test, f"rapm_sens caption: no {kind} setting reaches BPM on the test season")
        C.claim(len(set(best.values())) > 1, f"rapm_sens caption: the best {kind} differs between phases")
    C.expect("AbPriorFullScale", 1.0, 2)
    C.expect("AbPriorDoubleScale", 2.0, 2)
    C.expect("AbLamHighLambda", 12000.0, 0)
    bpm_h = Line2D([], [], color="black", lw=0.7, ls=(0, (4, 2)), label="BPM, same phase")
    phase_legend(fig, 0.995, handles_extra=(bpm_h,), short=True)
    return save(fig, out_dir, "fig_rapm_sens", png_dir)


# ---------------------------------------------------------------- figure: hot streaks against their shuffled null

STREAK_LABELS = {"fg_pct": "FG%", "fg3_pct": "3P%", "ft_pct": "FT%", "ts_pct": "TS%", "pts": "Points", "reb": "Rebounds",
                 "ast": "Assists", "stl": "Steals", "blk": "Blocks", "tov": "Turnovers", "fg3m": "Threes made",
                 "fta": "FT attempts", "min": "Minutes", "usg_pct": "Usage"}


def fig_streaks(cur, C, out_dir, png_dir):
    S = {k: (o, m, lo, hi, p, adj) for k, o, m, lo, hi, p, adj in rows(
        cur, "SELECT key, agg_obs, agg_null_mean, agg_null_lo, agg_null_hi, agg_p, agg_adjusted FROM paper_beliefs_summary "
             "WHERE family = 'streak' AND key LIKE 'streak:%%'")}
    stats = list(PN.STREAK_SHOOTING) + list(PN.STREAK_ROLE)
    windows = sorted({int(k.split(":")[2]) for k in S})
    C.claim(set(STREAK_LABELS) == set(stats) and len(S) == len(stats) * len(windows),
            "streak figure: every stat x window family of paper_beliefs_summary is drawn")
    nulls = [v[1] for v in S.values()]
    C.expect("BlStreakNullMinPct", min(nulls), 0, fmt=PN.pct)
    C.expect("BlStreakNullMaxPct", max(nulls), 0, fmt=PN.pct)
    C.expect("BlStreakCombos", len(S), 0, fmt=lambda v, d: PN.integer(v))
    for stat, macro in PN.STREAK_PROSE.items():
        o, m, lo, hi, p, adj = S[f"streak:{stat}:10"]
        C.expect(f"Bl{macro}TenObsPct", o, 0, fmt=PN.pct, optional=True)
        C.expect(f"Bl{macro}TenNullPct", m, 0, fmt=PN.pct, optional=True)
        C.expect(f"Bl{macro}TenNullLoPct", lo, 0, fmt=PN.pct, optional=True)
        C.expect(f"Bl{macro}TenNullHiPct", hi, 0, fmt=PN.pct, optional=True)
        C.expect(f"Bl{macro}TenAdjPct", adj, 0, fmt=PN.pct, optional=True)
    o, m, *_ = S["streak:min:20"]
    C.expect("BlStreakMinTwentyObsPct", o, 0, fmt=PN.pct)
    C.expect("BlStreakMinTwentyNullPct", m, 0, fmt=PN.pct)

    # Caption: "shooting percentages sit inside their null except ..., most counting and role stats above it, minutes over 20 below"
    role_above = sum(1 for s in PN.STREAK_ROLE for w in windows if S[f"streak:{s}:{w}"][0] > S[f"streak:{s}:{w}"][3])
    C.claim(role_above > 0.5 * len(PN.STREAK_ROLE) * len(windows), "streak caption: most counting/role families lie above their null range")
    C.claim(S["streak:min:20"][0] < S["streak:min:20"][2], "streak caption: minutes over 20 games lie below their null range")
    C.expect("BlStreakShootingInsideNull", sum(1 for s in PN.STREAK_SHOOTING for w in windows if S[f"streak:{s}:{w}"][4] >= 0.05), 0,
             fmt=lambda v, d: PN.integer(v))
    fig, axes = plt.subplots(1, len(windows), figsize=(COL_W, 2.3), sharey=True)
    fig.subplots_adjust(left=0.205, right=0.985, bottom=0.165, top=0.93, wspace=0.12)
    xmax = 5 * math.ceil(max(100 * max(v[0], v[3]) for v in S.values()) / 5 + 0.5)
    ys = {s: -i - (0.6 if i >= len(PN.STREAK_SHOOTING) else 0) for i, s in enumerate(stats)}
    for ax, w in zip(axes, windows):
        ax.axhline(-len(PN.STREAK_SHOOTING) + 0.2, color="#8c8c8c", lw=0.5)
        for s in stats:
            o, m, lo, hi, p, adj = S[f"streak:{s}:{w}"]
            y = ys[s]
            ax.plot([100 * lo, 100 * hi], [y, y], color="#8c8c8c", lw=2.4, solid_capstyle="butt", zorder=1)
            ax.plot([100 * m], [y], ls="none", marker="|", color="#4d4d4d", ms=5, mew=0.9, zorder=2)
            ax.plot([100 * o], [y], ls="none", marker="o", color="black", mfc="black" if p < 0.05 else "white", ms=3.4,
                    mew=0.8, zorder=3)
        ax.axvline(0, color="black", lw=0.6)
        ax.set_xlim(-5, xmax)
        ax.set_xticks([0, 25, 50, 75])
        ax.set_title(f"Last {w} games", fontsize=FONT_PT, pad=2)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
    axes[0].set_yticks([ys[s] for s in stats])
    axes[0].set_yticklabels([STREAK_LABELS[s] for s in stats])
    axes[0].set_ylim(min(ys.values()) - 0.6, 0.6)
    fig.text(0.595, 0.012, "Persistence share (%)", ha="center", va="bottom")
    return save(fig, out_dir, "fig_streaks", png_dir)


# ---------------------------------------------------------------- round 6 (step 12): possessions, data quality, report card

POSS_STARTS = (("steal", "After a steal"), ("dreb", "After a def. rebound"), ("made_fg", "After a made shot"),
               ("made_ft", "After a made free throw"), ("dead_tov", "After a dead-ball turnover"))


def fig_possessions(cur, C, out_dir, png_dir):
    """(a) points per possession by start type, every season pooled, intervals treating possessions as independent (the
    Possession Explorer's); (b) after a defensive rebound, by the second of the first attempt (possession_meta)."""
    import json
    var = {k: (float(v), int(n)) for k, v, n in rows(cur, """
        SELECT p.start_type, VAR_SAMP(p.pts), COUNT(*) FROM possessions p JOIN possession_games g USING (game_id)
        WHERE g.game_ok GROUP BY 1""")}
    ps = {k: (float(pts), float(poss)) for k, pts, poss in rows(
        cur, "SELECT start_type, sum(pts), sum(poss) FROM possession_seasons WHERE team = 'ALL' GROUP BY 1")}
    fig, axes = plt.subplots(1, 2, figsize=(COL_W, 1.75), gridspec_kw={"width_ratios": [1.05, 1]})
    fig.subplots_adjust(left=0.36, right=0.985, bottom=0.22, top=0.97, wspace=0.38)
    ax = axes[0]
    for i, (k, label) in enumerate(POSS_STARTS):
        pts, poss = ps[k]
        C.claim(int(poss) == var[k][1], f"possessions: possession_seasons' {k} count is the possessions table's")
        ppp = pts / poss
        se = math.sqrt(var[k][0] / poss)
        name = {"steal": "PoPppSteal", "dreb": "PoPppDreb", "made_fg": "PoPppMade"}.get(k)
        if name:
            C.expect(name, ppp, 2)
        ax.plot([ppp - 1.96 * se, ppp + 1.96 * se], [-i, -i], color="black", lw=0.8)
        ax.plot([ppp], [-i], ls="none", marker="o", ms=3.4, color="black")
    ax.set_yticks([-i for i in range(len(POSS_STARTS))])
    ax.set_yticklabels([lab for _k, lab in POSS_STARTS])
    ax.set_ylim(-len(POSS_STARTS) + 0.4, 0.6)
    ax.set_xlabel("Points per possession", labelpad=1.5)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.text(0.98, 0.02, "(a)", transform=ax.transAxes, ha="right", va="bottom")
    ax = axes[1]
    tc = rows(cur, "SELECT value FROM possession_meta WHERE key = 'transition_check'")[0][0]
    tc = tc if isinstance(tc, dict) else json.loads(tc)
    rules = rows(cur, "SELECT value FROM possession_meta WHERE key = 'rules'")[0][0]
    rules = rules if isinstance(rules, dict) else json.loads(rules)
    w = rules["transition_seconds"]
    y = np.array(tc["dreb_ppp_by_first_attempt_second"], float)
    x = np.arange(len(y))
    C.expect("PoDrebPeakPpp", float(y.max()), 2)
    C.expect("PoDrebPlateauPpp", float(y[int(w) + 2]), 2)
    ax.axvspan(-0.5, w - 0.5, color="#e6e6e6", lw=0, zorder=0)
    ax.plot(x, y, color="black", lw=0.8, marker="o", ms=2.2, zorder=2)
    ax.set_xlim(-0.5, len(y) - 0.5)
    ax.set_xticks([0, int(w), 12, 18, 23])
    ax.set_xlabel("First attempt (s)", labelpad=1.5)
    ax.set_ylabel("Points per possession", labelpad=1.5)
    ax.text(0.98, 0.97, "(b)", transform=ax.transAxes, ha="right", va="top")
    return save(fig, out_dir, "fig_possessions", png_dir)


def fig_quality(cur, C, out_dir, png_dir):
    """data_quality_sensitivity: every game vs the flagged games dropped vs random drops of as many games."""
    cols = ("result", "drop_set", "scope", "variant", "phase", "metric", "model_a", "model_b", "diff", "ci_lo", "ci_hi", "rand_lo", "rand_hi")
    S = [dict(zip(cols, r)) for r in rows(cur, f"""SELECT {', '.join(cols)} FROM data_quality_sensitivity
                                                 WHERE drop_set IN ('none', 'flagged') AND model_b <> ''""")]

    def get(result, drop, phase, metric, a, b, scope=None):
        c = [r for r in S if (r["result"], r["drop_set"], r["phase"], r["metric"], r["model_a"], r["model_b"]) == (result, drop, phase, metric, a, b)
             and (scope is None or r["scope"] == scope)]
        assert len(c) == 1, (result, drop, phase, metric, a, b, scope, len(c))
        return c[0]
    panels = [
        ("(a) RAPM + prior $-$ BPM (RMSE)", "impact", "game_rmse", "rapm_prior", "bpm", "everywhere", 1,
         [("tune", "Tune"), ("validate", "Val."), ("test", "Test")]),
        ("(b) Who played $-$ pre-game ($\\times10^{3}$ log loss)", "availability", "log_loss", "avail_bpm", "prior_rest", "scoring", 1000,
         [("tune", "Tune"), ("validate", "Val."), ("test", "Test")]),
        ("(c) Points per possession", "possessions", "ppp", None, None, "everywhere", 1,
         [(("steal", "made_fg"), "Steal $-$ make"), (("transition", "settled"), "Trans. $-$ settled")]),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(COL_W, 3.3), gridspec_kw={"height_ratios": [3, 3, 2]})
    fig.subplots_adjust(left=0.22, right=0.97, bottom=0.07, top=0.95, hspace=0.95)
    for ax, (title, result, metric, a, b, scope, sc, rws) in zip(axes, panels):
        for i, (key, label) in enumerate(rws):
            if result == "possessions":
                (aa, bb), ph = key, "all"
            else:
                aa, bb, ph = a, b, key
            full = get(result, "none", ph, metric, aa, bb)
            drop = get(result, "flagged", ph, metric, aa, bb, scope)
            y = -i
            ax.plot([drop["rand_lo"] * sc, drop["rand_hi"] * sc], [y, y], color="#bdbdbd", lw=5, solid_capstyle="butt", zorder=1)
            for r, dy, mk, mfc in ((full, 0.16, "o", "white"), (drop, -0.16, "D", "black")):
                ax.plot([r["ci_lo"] * sc, r["ci_hi"] * sc], [y + dy, y + dy], color="black", lw=0.8, zorder=2)
                ax.plot([r["diff"] * sc], [y + dy], ls="none", marker=mk, ms=3.2, color="black", mfc=mfc, mew=0.8, zorder=3)
            if result == "impact":
                Ph = {"tune": "Tune", "validate": "Val", "test": "Test"}[ph]
                C.expect(f"EvDNext{Ph}PriorBpmRmse", full["diff"], 2)
                if ph in ("tune", "test"):
                    C.expect(f"DqsImpact{Ph}", drop["diff"], 2)
                    C.expect(f"DqsImpact{Ph}Lo", drop["ci_lo"], 2)
                    C.expect(f"DqsImpact{Ph}Hi", drop["ci_hi"], 2)
                    C.expect(f"DqsImpact{Ph}RandLo", drop["rand_lo"], 2)
                    C.expect(f"DqsImpact{Ph}RandHi", drop["rand_hi"], 2)
            if result == "availability":
                C.expect({"tune": "AvDTune", "validate": "AvDVal", "test": "AvDTest"}[ph], full["diff"], 4)
                if ph == "tune":
                    C.expect("DqsAvailTune", drop["diff"], 4)
            if result == "possessions":
                C.expect("PoDStealMade" if aa == "steal" else "PoDTransSettled", full["diff"], 2)
        ax.axvline(0, color="black", lw=0.6)
        ax.set_yticks([-i for i in range(len(rws))])
        ax.set_yticklabels([lab for _k, lab in rws])
        ax.set_ylim(-len(rws) + 0.45, 0.55)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.set_title(title, fontsize=FONT_PT, loc="left", pad=2)
    h = [Line2D([], [], ls="none", marker="o", color="black", mfc="white", ms=3.4, label="Every game"),
         Line2D([], [], ls="none", marker="D", color="black", ms=3.2, label="Flagged dropped"),
         Line2D([], [], color="#bdbdbd", lw=5, label="Random drops")]
    fig.legend(handles=h, loc="lower center", bbox_to_anchor=(0.55, -0.005), ncol=3, frameon=False, handletextpad=0.5,
               handlelength=1.6, columnspacing=1.4)
    fig.subplots_adjust(bottom=0.12)
    return save(fig, out_dir, "fig_quality", png_dir)


REPORT_PANELS = (
    ("(a) Pre-game log loss ($\\times10^{3}$)", ("pregame", "log_loss", "", "prior_rest", "current"), 1000, "RcPreChosenCurrent", 1),
    ("(b) Playoff Brier, midpoint", ("sim_playoffs", "brier", "halfway", "model", "record"), 1, "RcSimBrier", 4),
    ("(c) BPM $-$ RAPM + prior", ("impact_next", "game_rmse", "", "bpm", "rapm_prior"), 1, "RcBpmPrior", 2),
    ("(d) Tracker $-$ BPM", ("impact_next", "game_rmse", "", "rapm_tracker", "bpm"), 1, "RcTrackerBpm", 2),
)


def fig_reportcard(cur, C, out_dir, png_dir):
    """report_card_tests (per season) and report_card_pooled (random effects), oriented first model minus second."""
    T = {}
    for task, metric, variant, se, a, b, d, lo, hi in rows(cur, """SELECT task, metric, variant, season, model_a, model_b, diff, ci_lo, ci_hi
                                                               FROM report_card_tests WHERE model_b <> ''"""):
        T[(task, metric, variant, a, b, se)] = (d, lo, hi)
        T[(task, metric, variant, b, a, se)] = (-d, -hi, -lo)
    P = {}
    for task, metric, variant, a, b, mu, lo, hi, pl, ph in rows(cur, """SELECT task, metric, variant, model_a, model_b, mu, ci_lo, ci_hi,
                                                                        pi_lo, pi_hi FROM report_card_pooled"""):
        P[(task, metric, variant, a, b)] = (mu, lo, hi, pl, ph)
        P[(task, metric, variant, b, a)] = (-mu, -hi, -lo, None if ph is None else -ph, None if pl is None else -pl)
    fig, axes = plt.subplots(1, 4, figsize=(FULL_W, 2.55), sharey=False)
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.1, top=0.9, wspace=0.5)
    most = max(len({k[5] for k in T if k[:5] == key}) for _t, key, _s, _m, _d in REPORT_PANELS)
    for ax, (title, (task, metric, variant, a, b), sc, macro, d) in zip(axes, REPORT_PANELS):
        seasons = sorted(se for (t, m, v, aa, bb, se) in T if (t, m, v, aa, bb) == (task, metric, variant, a, b))
        for i, se in enumerate(seasons):
            dd, lo, hi = T[(task, metric, variant, a, b, se)]
            y = -i
            ax.plot([lo * sc, hi * sc], [y, y], color="black", lw=0.8)
            ax.plot([dd * sc], [y], ls="none", marker="o", ms=3.0, color="black", mfc="black" if (lo > 0 or hi < 0) else "white", mew=0.8)
        mu, lo, hi, pl, ph = P[(task, metric, variant, a, b)]
        C.expect(macro, mu, d, scale=sc)
        C.expect(macro + "Lo", lo, d, scale=sc)
        C.expect(macro + "Hi", hi, d, scale=sc)
        if pl is not None:
            C.expect(macro + "PiLo", pl, d, scale=sc, optional=True)
            C.expect(macro + "PiHi", ph, d, scale=sc, optional=True)
        y = -len(seasons) - 0.6
        if pl is not None:
            ax.plot([pl * sc, ph * sc], [y, y], color="#bdbdbd", lw=5, solid_capstyle="butt", zorder=1)
        hh = 0.32 * (len(seasons) + 1.9) / (most + 1.9)       # the same printed height in every panel
        ax.fill([lo * sc, mu * sc, hi * sc, mu * sc], [y, y + hh, y, y - hh], color="black", zorder=2)
        ax.axvline(0, color="black", lw=0.6)
        ax.axhline(-len(seasons) + 0.2, color="#8c8c8c", lw=0.5)
        ax.set_yticks([-i for i in range(len(seasons))] + [y])
        ax.set_yticklabels([PN.season(se).replace("--", "–") for se in seasons] + ["Pooled"])
        ax.set_ylim(y - 0.7, 0.6)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.set_title(title, fontsize=FONT_PT, loc="left", pad=3)
    return save(fig, out_dir, "fig_reportcard", png_dir)


# ---------------------------------------------------------------- driver

FIGURES = {"pipeline": fig_pipeline, "forest": fig_forest, "calibration": fig_calibration,
           "rapm_sens": fig_rapm_sens, "streaks": fig_streaks, "possessions": fig_possessions, "quality": fig_quality,
           "reportcard": fig_reportcard}


def build(conn, out_dir, only=None, png_dir=None):
    """Draw the figures; returns (paths, number of checks). Stops if a plotted number disagrees with the paper's."""
    text = PN.build(conn)
    C = Checks(parse_macros(text))
    cur = conn.cursor()
    os.makedirs(out_dir, exist_ok=True)
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
    paths = []
    for name, fn in FIGURES.items():
        if only and name not in only:
            continue
        paths.append(fn(cur, C, out_dir, png_dir))
    cur.close()
    if C.bad:
        for p in paths:
            os.remove(p)
        raise SystemExit("A figure disagrees with the numbers the paper prints (figures removed):\n  - " + "\n  - ".join(C.bad))
    return paths, C.n, text


def check_paper(paper_path, out_dir):
    """Problems between the paper and the figures: an included file not written, a figure label never referenced,
    a figure written but not included."""
    with open(paper_path) as f:
        tex = PN.strip_comments(f.read())
    included = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    problems = [f"{g}: included but not in {out_dir}" for g in included
                if not os.path.exists(os.path.join(out_dir, os.path.basename(g)))]
    for env in re.findall(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", tex, flags=re.S):
        for lab in re.findall(r"\\label\{([^}]+)\}", env):
            if len(re.findall(r"\\ref\{" + re.escape(lab) + r"\}", tex)) == 0:
                problems.append(f"{lab}: never referenced in the text")
    names = {os.path.splitext(os.path.basename(g))[0] for g in included}
    problems += [f"fig_{k}: written but not included" for k in FIGURES if f"fig_{k}" not in names]
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", help="comma-separated: " + ",".join(FIGURES))
    ap.add_argument("--png", help="also write 300 dpi PNG previews to this directory")
    args = ap.parse_args()
    only = set(args.only.split(",")) if args.only else None
    if only and only - set(FIGURES):
        raise SystemExit(f"unknown figure(s): {', '.join(sorted(only - set(FIGURES)))}")
    conn = PN.connect()
    try:
        paths, n_checks, text = build(conn, args.out, only, args.png)
    finally:
        conn.close()
    on_disk = os.path.join(ROOT, "paper", "numbers.tex")
    if os.path.exists(on_disk):
        with open(on_disk) as f:
            if f.read() != text:
                print("note: paper/numbers.tex differs from the macros built now; rerun paper_numbers.py")
    for p in paths:
        print(f"wrote {os.path.relpath(p, ROOT)} ({os.path.getsize(p) / 1024:.0f} kB)")
    print(f"{n_checks} plotted numbers checked against the paper's macros")
    paper = os.path.join(ROOT, "paper", "nba_hub_paper.tex")
    if os.path.exists(paper) and not only and os.path.abspath(args.out) == os.path.abspath(DEFAULT_OUT):
        problems = check_paper(paper, args.out)
        if problems:
            raise SystemExit("paper and figures disagree:\n  - " + "\n  - ".join(problems))
        print("every figure is included in the paper and referenced in its text")


if __name__ == "__main__":
    main()
