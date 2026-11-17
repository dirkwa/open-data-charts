import numpy as np
import pytest

pytest.importorskip('osgeo')

from odc.build_unit import (  # noqa: E402
    CONTOUR_WINDOW_CELLS,
    ETOPO_PIXEL_DEG,
    GRID_STEP,
    SOUNDING_MINZOOM,
    deep_water,
    depth_quality,
    dilate_disk,
    sounding_squares,
    thin_soundings,
    window_edges,
)
from odc.grid import Unit  # noqa: E402


def test_contour_windows_sit_on_global_grid_lines():
    # These neighbours' boxes overlap across a window boundary (ix 2829312).
    a, b = Unit(502, 288).build_box, Unit(503, 288).build_box
    edges_a = {a.ix0 + e for e in window_edges(a.ix0, a.ncols)}
    edges_b = {b.ix0 + e for e in window_edges(b.ix0, b.ncols)}
    shared = {e for e in edges_a & edges_b if e % CONTOUR_WINDOW_CELLS == 0}
    overlap = range(b.ix0, a.ix1)
    expected = {e for e in overlap if e % CONTOUR_WINDOW_CELLS == 0 and b.ix0 < e < a.ix1 - 1}
    assert expected
    assert shared == expected


def test_window_edges_include_box_ends():
    edges = window_edges(1000, 2000)
    assert edges[0] == 0 and edges[-1] == 1999
    assert all((1000 + e) % CONTOUR_WINDOW_CELLS == 0 for e in edges[1:-1])


def test_sounding_squares_nest_inside_tiles():
    rng = np.random.default_rng(1)
    lon = rng.uniform(165.94, 166.64, 2000)
    lat = rng.uniform(-22.59, -21.95, 2000)
    for zoom in range(SOUNDING_MINZOOM, 16):
        fine = sounding_squares(lon, lat, zoom + 1)
        coarse = sounding_squares(lon, lat, zoom)
        pairs = {}
        for f, c in zip(fine, coarse, strict=True):
            assert pairs.setdefault(f, c) == c


def test_a_unit_thins_its_soundings_as_a_bigger_area_would():
    rng = np.random.default_rng(2)
    a, b = Unit(492, 288), Unit(493, 288)
    lon = rng.uniform(a.bounds[0], b.bounds[2], 20000)
    lat = rng.uniform(a.bounds[1], a.bounds[3], 20000)
    z = rng.uniform(0, 50, 20000).astype(np.float32)
    together = thin_soundings(lon, lat, z, 15)
    mine = a.owns(lon, lat)
    alone = thin_soundings(lon[mine], lat[mine], z[mine], 15)
    assert np.array_equal(together[mine], alone)


def test_write_layers_replaces_only_its_own_layers(tmp_path):
    from odc.build_unit import write_layers

    (tmp_path / 'SOUNDG.geojson').write_text('{}')
    (tmp_path / 'structures.geojson').write_text('{}')
    write_layers(str(tmp_path), {}, {'SOUNDG': 129}, Unit(492, 288))
    assert not (tmp_path / 'SOUNDG.geojson').exists()
    assert (tmp_path / 'structures.geojson').exists()


def test_a_measured_cell_counts_for_the_survey_that_sounded_it():
    unit = Unit(492, 288)
    box = unit.build_box
    r, c = unit.cells.iy0 - box.iy0 + 100, unit.cells.ix0 - box.ix0 + 100
    rank = np.full((box.nrows, box.ncols), -1, np.int16)
    rank[r : r + 100, c : c + 50] = 0
    rank[r : r + 100, c + 50 : c + 100] = 1
    land = np.ones_like(rank, bool)
    land[r : r + 100, c : c + 100] = False
    entries = [
        (None, None, 'A', None, {'end': '20100101', 'tecsou': 'multi-beam echosounder'}),
        (None, None, 'B', None, {'end': '20100101', 'tecsou': 'single-beam echosounder'}),
    ]
    quality, used = depth_quality(entries, rank, rank >= 0, rank >= 0, land, box, unit)
    assert quality['share'] == {'multibeam': 0.5, 'survey-recent': 0.5}
    assert used == [0, 1]


def test_water_without_a_survey_or_model_depth_is_not_model():
    unit = Unit(492, 288)
    box = unit.build_box
    r, c = unit.cells.iy0 - box.iy0 + 100, unit.cells.ix0 - box.ix0 + 100
    land = np.ones((box.nrows, box.ncols), bool)
    land[r : r + 100, c : c + 100] = False
    from_model = np.zeros_like(land)
    from_model[r : r + 100, c : c + 25] = True
    quality, used = depth_quality([], None, None, from_model, land, box, unit)
    assert quality == {'best': 'model', 'share': {'model': 0.25, 'none': 0.75}}
    assert used == []
    quality, _ = depth_quality([], None, None, np.zeros_like(land), land, box, unit)
    assert quality == {'best': 'none', 'share': {'none': 1.0}}


def test_dilate_disk_reaches_exactly_the_euclidean_radius():
    mask = np.zeros((41, 43), bool)
    mask[20, 21] = True
    mask[3, 40] = True
    rows, cols = np.indices(mask.shape)
    expected = np.zeros_like(mask)
    for r, c in zip(*np.nonzero(mask), strict=True):
        expected |= (rows - r) ** 2 + (cols - c) ** 2 <= 12**2
    assert (dilate_disk(mask, 12) == expected).all()


def etopo_tile(tmp_path, z):
    from osgeo import gdal

    path = str(tmp_path / 'etopo.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, z.shape[1], z.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((-145.0, ETOPO_PIXEL_DEG, 0, -16.0, 0, -ETOPO_PIXEL_DEG))
    ds.GetRasterBand(1).SetNoDataValue(-99999)
    ds.GetRasterBand(1).WriteArray(z)
    ds = None
    return [path]


def test_deep_water_keeps_clear_of_anything_shallower(tmp_path):
    z = np.full((120, 120), -1000.0, np.float32)
    z[60, 60] = -50.0  # a reef pixel
    bbox = (-144.9, -16.4, -144.85, -16.35)
    depth = deep_water(etopo_tile(tmp_path, z), bbox)
    assert np.nanmin(depth) > 900
    reef_lon, reef_lat = -145.0 + 60.5 * ETOPO_PIXEL_DEG, -16.0 - 60.5 * ETOPO_PIXEL_DEG
    col = int((reef_lon - bbox[0]) / GRID_STEP)
    row = int((bbox[3] - reef_lat) / GRID_STEP)
    clear = int(2.5 * ETOPO_PIXEL_DEG / GRID_STEP)
    assert np.isnan(depth[row - clear + 1 : row + clear, col - clear + 1 : col + clear]).all()
    assert np.isfinite(depth[0, 0])


def test_neighbouring_boxes_agree_on_deep_water(tmp_path):
    rng = np.random.default_rng(3)
    z = rng.uniform(-3000, -100, (120, 120)).astype(np.float32)
    paths = etopo_tile(tmp_path, z)
    a = (-144.9, -16.4, -144.85, -16.35)
    b = (-144.875, -16.4, -144.825, -16.35)
    da, db = deep_water(paths, a), deep_water(paths, b)
    shift = round((b[0] - a[0]) / GRID_STEP)
    np.testing.assert_allclose(da[:, shift:], db[:, : da.shape[1] - shift], atol=0.01)


def test_deep_water_counts_as_coarse_model():
    unit = Unit.parse('z9-50-280')
    box = unit.build_box
    land = np.zeros((box.nrows, box.ncols), bool)
    from_deep = np.ones_like(land)
    quality, _ = depth_quality([], None, None, np.zeros_like(land), land, box, unit, from_deep)
    assert quality == {'best': 'model-coarse', 'share': {'model-coarse': 1.0}}
