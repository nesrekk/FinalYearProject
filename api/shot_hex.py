"""Hexagon grid for the Shot Quality Map, shared by scripts/build_shot_making.py
(which bins every shot into it) and api/routers/shot_quality_map.py (which
turns a cell id back into a court position), so the two can't disagree.

Court coordinates are player_shots' own: tenths of a foot, the hoop at (0, 0),
x across the court (-250..250), y away from the baseline (-52..). Cells are
pointy-top hexagons of circumradius SIZE tenths (12 -> 2.08 ft across the flats).
A cell is its axial coordinate (q, r), packed into one small integer:
id = (r - R_MIN) * Q_SPAN + (q - Q_MIN).

Only the half court is mapped: a shot from beyond y = Y_MAX (a heave from the far
half, about 0.14% of shots) is not in any cell, and before 2010-11 the shots the
NBA gave no location (about a quarter of all shots, nearly all at the rim, stored
at exactly (0, 0)) aren't either; both are counted per player-season as "off the
map" so totals still reconcile.
"""

import math

import numpy as np

SIZE = 12                 # circumradius, tenths of a foot
Y_MAX = 470               # half court, tenths of a foot
X_MAX = 250
Y_MIN = -52
Q_MIN, Q_SPAN = -27, 45
R_MIN = -4
LAST_UNLOCATED_SEASON = 2010   # season = end year: 2009-10 and earlier have shots with no recorded location
SQRT3 = math.sqrt(3.0)


def axial(x, y):
    """Axial (q, r) of the hexagon containing each (x, y) (numpy arrays or scalars)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    q = (SQRT3 / 3 * x - y / 3) / SIZE
    r = (2 / 3 * y) / SIZE
    cx, cz = q, r
    cy = -cx - cz
    rx, ry, rz = np.round(cx), np.round(cy), np.round(cz)
    dx, dy, dz = np.abs(rx - cx), np.abs(ry - cy), np.abs(rz - cz)
    fix_x = (dx > dy) & (dx > dz)
    fix_y = ~fix_x & (dy > dz)
    rx = np.where(fix_x, -ry - rz, rx)
    rz = np.where(~fix_x & ~fix_y, -rx - ry, rz)
    return rx.astype(np.int32), rz.astype(np.int32)


def cell_id(x, y):
    q, r = axial(x, y)
    return ((r - R_MIN) * Q_SPAN + (q - Q_MIN)).astype(np.int32)


def center(cid):
    """(x, y) in tenths of a foot of a cell's centre."""
    cid = np.asarray(cid, dtype=np.int64)
    r = cid // Q_SPAN + R_MIN
    q = cid % Q_SPAN + Q_MIN
    return SQRT3 * SIZE * (q + r / 2.0), 1.5 * SIZE * r


def off_map(x, y, season):
    """True for shots that belong to no cell (see the module docstring)."""
    x = np.asarray(x)
    y = np.asarray(y)
    season = np.asarray(season)
    return (y > Y_MAX) | ((x == 0) & (y == 0) & (season <= LAST_UNLOCATED_SEASON))
