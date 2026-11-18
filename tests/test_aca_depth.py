import numpy as np
import pytest

pytest.importorskip('osgeo')
from osgeo import gdal  # noqa: E402

from odc.aca_depth import CELLS_PER_DEGREE, cut, square_name  # noqa: E402
from odc.build_unit import (  # noqa: E402
    GRID_STEP,
    NODATA,
    SATELLITE_BANDS,
    SATELLITE_DEEP_BAND,
    bbox_polygon,
    build_depths,
    chart_grid,
    depth_quality,
    mem_raster,
    satellite_bands,
)
from odc.grid import GridBox, Unit  # noqa: E402

PACKAGE_PIXEL = 0.0000898  # the atlas's 10 m


def package(tmp_path, cm, west=-145.0, north=-16.0, name='bathymetry_0.tif', nodata=0, dtype=gdal.GDT_Int16):
    path = str(tmp_path / name)
    ds = gdal.GetDriverByName('GTiff').Create(path, cm.shape[1], cm.shape[0], 1, dtype)
    ds.SetGeoTransform((west, PACKAGE_PIXEL, 0, north, 0, -PACKAGE_PIXEL))
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(cm)
    ds = None
    return path


def test_a_package_becomes_squares_on_the_chart_grid_in_metres(tmp_path):
    cm = np.zeros((200, 200), np.int16)
    cm[50:60, 50:60] = 437
    cm[55, 55] = 120
    out = tmp_path / 'squares'
    assert cut([package(tmp_path, cm)], str(out)) == [square_name(-17, -145)]
    ds = gdal.Open(str(out / square_name(-17, -145)))
    assert ds.GetGeoTransform() == (-145.0, GRID_STEP, 0.0, -16.0, 0.0, -GRID_STEP)
    assert (ds.RasterXSize, ds.RasterYSize) == (CELLS_PER_DEGREE, CELLS_PER_DEGREE)
    m = ds.GetRasterBand(1).ReadAsArray()
    assert set(np.unique(m)) == {0, 1, 4}  # the shoalest depth of each cell


def test_the_source_nodata_takes_no_part_in_the_shoalest_depth(tmp_path):
    clean = np.zeros((200, 200), np.int16)
    clean[50:60, 50:60] = 437
    clean[50:60:2, 50:60:2] = 0
    holes = np.where(clean > 0, clean, 100).astype(np.int16)  # shoaler than any depth, were it one
    cut([package(tmp_path, clean, name='clean.tif')], str(tmp_path / 'clean'))
    cut([package(tmp_path, holes, name='holes.tif', nodata=100)], str(tmp_path / 'holes'))
    name = square_name(-17, -145)
    a = gdal.Open(str(tmp_path / 'clean' / name))
    b = gdal.Open(str(tmp_path / 'holes' / name))
    assert np.array_equal(a.GetRasterBand(1).ReadAsArray(), b.GetRasterBand(1).ReadAsArray())


def test_a_later_package_keeps_the_square_where_it_has_no_data(tmp_path):
    out = str(tmp_path / 'squares')
    first = np.zeros((100, 100), np.int16)
    first[:, :50] = 300
    cut([package(tmp_path, first, name='a.tif')], out)
    second = np.zeros((100, 100), np.int16)
    second[:, 50:] = 900
    cut([package(tmp_path, second, name='b.tif')], out)
    ds = gdal.Open(f'{out}/{square_name(-17, -145)}')
    m = ds.GetRasterBand(1).ReadAsArray()
    assert set(np.unique(m)) == {0, 3, 9}


def test_depths_beyond_the_cap_read_as_the_cap(tmp_path):
    cm = np.zeros((100, 100), np.uint16)
    cm[10:20, 10:20] = 40000
    out = tmp_path / 'squares'
    cut([package(tmp_path, cm, dtype=gdal.GDT_UInt16)], str(out))
    ds = gdal.Open(str(out / square_name(-17, -145)))
    m = ds.GetRasterBand(1).ReadAsArray()
    assert set(np.unique(m)) == {0, 255}


def test_rasters_of_one_run_keep_the_shoaler_depth_where_they_overlap(tmp_path):
    deep = np.full((100, 100), 40000, np.uint16)
    shoal = np.full((100, 100), 400, np.uint16)
    out = tmp_path / 'squares'
    cut(
        [
            package(tmp_path, shoal, name='a.tif', dtype=gdal.GDT_UInt16),
            package(tmp_path, deep, name='b.tif', dtype=gdal.GDT_UInt16),
        ],
        str(out),
    )
    ds = gdal.Open(str(out / square_name(-17, -145)))
    m = ds.GetRasterBand(1).ReadAsArray()
    assert set(np.unique(m)) == {0, 4}


def test_a_run_replaces_earlier_depths_even_where_its_second_raster_brings_them(tmp_path):
    out = str(tmp_path / 'squares')
    earlier = np.full((200, 200), 100, np.int16)
    cut([package(tmp_path, earlier, name='earlier.tif')], out)
    first = np.full((100, 100), 500, np.int16)
    second = np.full((100, 100), 900, np.int16)
    cut(
        [
            package(tmp_path, first, west=-144.5, name='a.tif'),
            package(tmp_path, second, name='b.tif'),
        ],
        out,
    )
    ds = gdal.Open(f'{out}/{square_name(-17, -145)}')
    m = ds.GetRasterBand(1).ReadAsArray()
    assert m[10, 10] == 9  # the run's second raster, over the earlier depth
    assert m[10, 4010] == 5  # the run's first raster
    assert m[120, 120] == 1  # the earlier run alone
    assert not list((tmp_path / 'squares').glob('*.run'))


def test_a_square_says_it_is_not_for_navigation(tmp_path):
    cm = np.zeros((100, 100), np.int16)
    cm[10:20, 10:20] = 437
    out = tmp_path / 'squares'
    cut([package(tmp_path, cm)], str(out))
    ds = gdal.Open(str(out / square_name(-17, -145)))
    assert 'NOT FOR NAVIGATION' in ds.GetMetadataItem('TIFFTAG_IMAGEDESCRIPTION')


def squares(tmp_path, metres, bbox):
    """A square file covering bbox on the chart grid."""
    ncols, nrows, gt = chart_grid(bbox)
    path = str(tmp_path / 'square.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, ncols, nrows, 1, gdal.GDT_Byte)
    ds.SetGeoTransform(gt)
    ds.GetRasterBand(1).SetNoDataValue(0)
    ds.GetRasterBand(1).WriteArray(metres(nrows, ncols))
    ds = None
    return [path]


BBOX = (-144.8, -16.9, -144.79, -16.89)


def test_satellite_depths_fall_into_bands_and_lose_their_specks(tmp_path):
    def metres(nrows, ncols):
        a = np.zeros((nrows, ncols), np.uint8)
        a[:, :20] = 3  # under 4 m
        a[:, 20:40] = 6
        a[:, 40:] = 12
        a[5, 70] = 1  # a lone shallow pixel in deep water
        a[30, 10] = 0  # a lone pixel without depth in shallow water
        return a

    bands = satellite_bands(squares(tmp_path, metres, BBOX), BBOX)
    assert [int(b) for b in bands[0, [0, 25, 45]]] == [1, 2, 3]
    assert bands[5, 70] == 3
    assert bands[30, 10] == 0


def test_small_deep_patches_take_the_band_above_or_none(tmp_path):
    def metres(nrows, ncols):
        a = np.zeros((nrows, ncols), np.uint8)
        a[:, :40] = 3
        a[10:13, 10:13] = 12  # 9 deep cells in shallow water
        a[10:13, 60:63] = 12  # 9 deep cells alone
        a[30:40, 50:60] = 12  # 100 deep cells alone
        return a

    bands = satellite_bands(squares(tmp_path, metres, BBOX), BBOX)
    assert set(np.unique(bands[10:13, 10:13])) == {SATELLITE_DEEP_BAND - 1}
    assert set(np.unique(bands[10:13, 60:63])) == {0}
    assert set(np.unique(bands[30:40, 50:60])) == {SATELLITE_DEEP_BAND}


def test_neighbouring_boxes_give_shared_cells_the_same_band(tmp_path):
    def metres(nrows, ncols):
        a = np.full((nrows, ncols), 12, np.uint8)
        a[20, 37:43] = 3  # six cells, three of them past the left box's edge
        return a

    paths = squares(tmp_path, metres, BBOX)
    left = (-144.8, -16.9, -144.795, -16.89)  # 40 columns
    right = (-144.796, -16.9, -144.79, -16.89)
    a = satellite_bands(paths, left)
    b = satellite_bands(paths, right)
    assert np.array_equal(a[:, 32:], b[:, :8])
    assert set(np.unique(a[20, 37:40])) == {1}


def test_unsurveyed_water_with_satellite_depth_becomes_banded_depth_areas(tmp_path):
    box = GridBox.around(*BBOX)
    bbox = box.bbox
    ncols, nrows, gt = chart_grid(bbox)
    grid = str(tmp_path / 'depth.tif')
    gdal.Translate(grid, mem_raster(np.full((nrows, ncols), NODATA, np.float32), gt))
    satellite = np.zeros((nrows, ncols), np.uint8)
    satellite[:, : ncols // 2] = 2
    water = bbox_polygon(*bbox)
    depare, depcnt, unsare = build_depths(grid, box, water, np.zeros_like(satellite, bool), satellite)
    assert depcnt == []
    assert {(a['DRVAL1'], a['DRVAL2']) for _, a in depare} == {SATELLITE_BANDS[1][1:]}
    assert all('satellite' in a['INFORM'] for _, a in depare)
    banded = sum(g.GetArea() for g, _ in depare)
    unsurveyed = sum(g.GetArea() for g, _ in unsare)
    assert banded + unsurveyed == pytest.approx(water.GetArea(), rel=1e-6)
    assert banded == pytest.approx(water.GetArea() / 2, rel=0.05)


def test_satellite_depth_counts_after_the_model():
    unit = Unit.parse('z9-50-280')
    box = unit.build_box
    land = np.zeros((box.nrows, box.ncols), bool)
    from_model = np.zeros_like(land)
    from_model[:, : box.ncols // 2] = True
    quality, _ = depth_quality([], None, None, from_model, land, box, unit, None, np.ones_like(land))
    cells = unit.cells
    rows = slice(cells.iy0 - box.iy0, cells.iy1 - box.iy0)
    cols = slice(cells.ix0 - box.ix0, cells.ix1 - box.ix0)
    model = from_model[rows, cols].mean()
    assert quality['best'] == 'model'
    assert quality['share'] == {'model': round(model, 3), 'satellite': round(1 - model, 3)}


def test_a_share_rounding_to_nothing_is_not_the_best():
    unit = Unit.parse('z9-50-280')
    box = unit.build_box
    land = np.zeros((box.nrows, box.ncols), bool)
    from_deep = np.ones_like(land)
    from_satellite = np.zeros_like(land)
    cells = unit.cells
    from_satellite[cells.iy0 - box.iy0, cells.ix0 - box.ix0] = True
    from_deep[cells.iy0 - box.iy0, cells.ix0 - box.ix0] = False
    quality, _ = depth_quality([], None, None, None, land, box, unit, from_deep, from_satellite)
    assert quality == {'best': 'model-coarse', 'share': {'model-coarse': 1.0}}


def sha(data):
    import hashlib

    return f'sha256:{hashlib.sha256(data).hexdigest()}'


def test_a_square_replaced_after_listing_is_listed_again(tmp_path, monkeypatch):
    import urllib.error

    from odc import sources

    name = square_name(-17, -145)
    # Renamed from next-<square> to <square> between the second listing and its download.
    listings = iter(
        [
            {name: ('old', sha(b'old'))},
            {f'next-{name}': ('next', sha(b'new'))},
            {name: ('new', sha(b'new'))},
        ]
    )
    monkeypatch.setattr(sources, 'release_assets', lambda release: next(listings))
    monkeypatch.setattr(sources, 'fetch_bounds', lambda region: (-144.5, -16.5, -144.4, -16.4))

    def save_url(url, path):
        if url != 'new':
            raise urllib.error.HTTPError(url, 404, 'Not Found', None, None)
        open(path, 'wb').write(b'new')

    monkeypatch.setattr(sources, 'save_url', save_url)
    sources.fetch_aca_depth({'sources': ['allen-coral-atlas']}, str(tmp_path))
    assert (tmp_path / 'aca-depth' / name).read_bytes() == b'new'


def test_a_square_listed_without_a_digest_is_not_taken(tmp_path, monkeypatch):
    from odc import sources

    name = square_name(-17, -145)
    (tmp_path / 'aca-depth').mkdir()
    (tmp_path / 'aca-depth' / name).write_text('kept')
    monkeypatch.setattr(sources, 'release_assets', lambda release: {name: ('url', None)})
    monkeypatch.setattr(sources, 'fetch_bounds', lambda region: (-144.5, -16.5, -144.4, -16.4))
    monkeypatch.setattr(sources, 'save_url', lambda url, path: open(path, 'w').write('unchecked'))
    with pytest.raises(OSError):
        sources.fetch_aca_depth({'sources': ['allen-coral-atlas']}, str(tmp_path))
    assert (tmp_path / 'aca-depth' / name).read_text() == 'kept'


def test_a_download_not_matching_its_digest_keeps_the_square(tmp_path, monkeypatch):
    from odc import sources

    name = square_name(-17, -145)
    (tmp_path / 'aca-depth').mkdir()
    (tmp_path / 'aca-depth' / name).write_text('kept')
    monkeypatch.setattr(sources, 'release_assets', lambda release: {name: ('url', 'sha256:00')})
    monkeypatch.setattr(sources, 'fetch_bounds', lambda region: (-144.5, -16.5, -144.4, -16.4))
    monkeypatch.setattr(sources, 'save_url', lambda url, path: open(path, 'w').write('trunc'))
    with pytest.raises(OSError):
        sources.fetch_aca_depth({'sources': ['allen-coral-atlas']}, str(tmp_path))
    assert (tmp_path / 'aca-depth' / name).read_text() == 'kept'
    assert not (tmp_path / 'aca-depth' / f'{name}.new').exists()


def test_a_square_replaced_with_new_bytes_after_listing_is_listed_again(tmp_path, monkeypatch):
    from odc import sources

    name = square_name(-17, -145)
    new = sha(b'new')
    listings = iter([{name: ('url', 'sha256:00')}, {name: ('url', new)}])
    monkeypatch.setattr(sources, 'release_assets', lambda release: next(listings))
    monkeypatch.setattr(sources, 'fetch_bounds', lambda region: (-144.5, -16.5, -144.4, -16.4))
    monkeypatch.setattr(sources, 'save_url', lambda url, path: open(path, 'wb').write(b'new'))
    sources.fetch_aca_depth({'sources': ['allen-coral-atlas']}, str(tmp_path))
    assert (tmp_path / 'aca-depth' / name).read_bytes() == b'new'
