import numpy as np
import pytest

pytest.importorskip('osgeo')
from osgeo import gdal  # noqa: E402

from odc.aca_depth import CELLS_PER_DEGREE, cut, square_name  # noqa: E402
from odc.build_unit import (  # noqa: E402
    GRID_STEP,
    NODATA,
    SATELLITE_BANDS,
    bbox_polygon,
    build_depths,
    chart_grid,
    depth_quality,
    mem_raster,
    satellite_bands,
)
from odc.grid import GridBox, Unit  # noqa: E402

PACKAGE_PIXEL = 0.0000898  # the atlas's 10 m


def package(tmp_path, cm, west=-145.0, north=-16.0, name='bathymetry_0.tif'):
    path = str(tmp_path / name)
    ds = gdal.GetDriverByName('GTiff').Create(path, cm.shape[1], cm.shape[0], 1, gdal.GDT_Int16)
    ds.SetGeoTransform((west, PACKAGE_PIXEL, 0, north, 0, -PACKAGE_PIXEL))
    ds.GetRasterBand(1).SetNoDataValue(0)
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


def test_satellite_depths_fall_into_bands_and_lose_their_specks(tmp_path):
    bbox = (-144.8, -16.9, -144.79, -16.89)

    def metres(nrows, ncols):
        a = np.zeros((nrows, ncols), np.uint8)
        a[:, :20] = 3  # under 4 m
        a[:, 20:40] = 6
        a[:, 40:] = 12
        a[5, 70] = 1  # a lone shallow pixel in deep water
        return a

    bands = satellite_bands(squares(tmp_path, metres, bbox), bbox)
    assert [int(b) for b in bands[0, [0, 25, 45]]] == [1, 2, 3]
    assert bands[5, 70] == 3


def test_unsurveyed_water_with_satellite_depth_becomes_banded_depth_areas(tmp_path):
    box = GridBox.around(-144.8, -16.9, -144.79, -16.89)
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
    assert quality['best'] == 'model'
    assert set(quality['share']) == {'model', 'satellite'}
