import math

import numpy as np
import pytest

from odc.grid import CELLS_PER_UNIT_X, GRID_STEP, GridBox, Unit, cell_x, cell_y, lat_of, lon_of, unit_at, units_in


def test_unit_longitude_edges_fall_on_grid_lines():
    assert CELLS_PER_UNIT_X * GRID_STEP == pytest.approx(360 / 512)
    for x in (0, 1, 255, 492, 511):
        box = Unit(x, 288).cells
        assert box.ix0 == x * CELLS_PER_UNIT_X
        assert box.ncols == CELLS_PER_UNIT_X


def test_noumea_unit():
    u = Unit(492, 288)
    assert u.id == 'z9-492-288'
    assert u.bounds == pytest.approx((165.9375, -22.593726, 166.640625, -21.943046), abs=1e-6)
    assert unit_at(166.45, -22.27) == u
    assert Unit.parse('z9-492-288') == u


def test_prototype_bbox_covers_six_units():
    ids = {u.id for u in units_in(165.9, -22.75, 166.95, -21.95)}
    assert ids == {f'z9-{x}-{y}' for x in (491, 492, 493) for y in (288, 289)}


def test_cells_cover_unit_and_rows_straddle_mercator_edges():
    u = Unit(492, 288)
    minlon, minlat, maxlon, maxlat = u.cells.bbox
    ulon0, ulat0, ulon1, ulat1 = u.bounds
    assert minlon == ulon0 and maxlon == ulon1
    assert minlat <= ulat0 < minlat + GRID_STEP
    assert maxlat - GRID_STEP < ulat1 <= maxlat


def test_build_box_is_aligned_and_has_margin():
    u = Unit(492, 288)
    b, c = u.build_box, u.cells
    for v in (b.ix0, b.iy0, b.ix1, b.iy1):
        assert v % 64 == 0
    assert c.ix0 - b.ix0 >= 64 and b.ix1 - c.ix1 >= 64
    assert c.iy0 - b.iy0 >= 64 and b.iy1 - c.iy1 >= 64


def test_cell_index_round_trip():
    lon = np.array([165.9, 166.0000625, -179.9999, 179.9999])
    lat = np.array([-21.95, -22.0000625, 89.9999, -89.9999])
    ix, iy = cell_x(lon), cell_y(lat)
    assert np.all(lon_of(ix) <= lon) and np.all(lon < lon_of(ix + 1))
    assert np.all(lat_of(iy + 1) < lat) and np.all(lat <= lat_of(iy) + 1e-12)


def test_prototype_grid_is_the_global_grid_shifted():
    # The prototype counted cells from (165.9, -21.95); both edges are grid lines.
    box = GridBox.around(165.9, -22.75, 166.95, -21.95)
    assert (box.ix0, box.iy0, box.ncols, box.nrows) == (2767200, 895600, 8400, 6400)


def test_owner_is_unique_on_shared_edges():
    a, b = Unit(492, 288), Unit(493, 288)
    edge = a.bounds[2]
    assert not a.owns(edge, -22.2) and b.owns(edge, -22.2)
    c = Unit(492, 289)
    lat = a.bounds[1]
    assert not a.owns(166.2, lat) and c.owns(166.2, lat)
    assert math.isclose(lat, c.bounds[3])
