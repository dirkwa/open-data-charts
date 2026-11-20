import numpy as np
from osgeo import gdal, osr

from odc import lat_model

GRID = lat_model.Grid(1 / 1920, 2)


def raster(path, epsg, origin, step, values, nodata):
    rows, cols = values.shape
    ds = gdal.GetDriverByName('GTiff').Create(str(path), cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((origin[0], step, 0.0, origin[1], 0.0, -step))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(values)
    ds = None
    return str(path)


def model_near(tmp_path, lon, lat, depth):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3034)
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    x, y, _ = osr.CoordinateTransformation(wgs, srs).TransformPoint(lon, lat)
    values = np.full((40, 40), depth, dtype='float32')
    return raster(tmp_path / 'ddm.tif', 3034, (x - 1000, y + 1000), 50, values, -9999.0)


def test_the_model_lands_on_lat_and_emodnet_fills_beyond_it(tmp_path):
    msl = np.full((300, 400), 40.5, dtype='float32')
    lat = np.full((300, 400), 40.2, dtype='float32')
    lat[150, 200] = -32768.0  # an undefined cell, as along the coast
    surfaces = [
        raster(tmp_path / 'msl.tif', 4326, (8.0, 58.0), 0.01, msl, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (8.0, 58.0), 0.01, lat, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    lat_model.separation(*surfaces, separation)
    sep = gdal.Open(separation).ReadAsArray()
    assert np.allclose(sep, 0.3, atol=1e-4)

    emodnet = np.full((1920, 1920), -20.0, 'float32')
    fill = raster(tmp_path / 'emodnet.tif', 4326, (10.0, 58.0), 1 / 960, emodnet, -9999.0)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    out = str(tmp_path / 'chunk.tif')
    assert lat_model.write_chunk(model, separation, fill, GRID, 56, 10, out)
    ds = gdal.Open(out)
    assert ds.GetGeoTransform() == (10.0, GRID.step, 0.0, 58.0, 0.0, -GRID.step)
    heights = ds.ReadAsArray()
    row, col = int((58.0 - 57.0) / GRID.step), int((11.0 - 10.0) / GRID.step)
    assert abs(heights[row, col] - (-9.7)) < 1e-3
    assert heights[10, 10] == -20.0


def test_a_chunk_neither_source_reaches_is_not_written(tmp_path):
    msl = np.full((10, 10), 40.5, dtype='float32')
    surfaces = [
        raster(tmp_path / 'msl.tif', 4326, (8.0, 58.0), 0.01, msl, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (8.0, 58.0), 0.01, msl - 0.1, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    lat_model.separation(*surfaces, separation)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    out = tmp_path / 'chunk.tif'
    out.write_bytes(b'an earlier run')
    assert not lat_model.write_chunk(model, separation, None, GRID, 52, 0, str(out))
    assert not out.exists()


def test_the_model_gives_way_to_the_fill_where_no_separation_reaches(tmp_path):
    msl = np.full((10, 10), 40.5, dtype='float32')
    surfaces = [
        raster(tmp_path / 'msl.tif', 4326, (8.0, 58.0), 0.01, msl, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (8.0, 58.0), 0.01, msl - 0.1, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    lat_model.separation(*surfaces, separation)
    emodnet = np.full((1920, 1920), -20.0, 'float32')
    fill = raster(tmp_path / 'emodnet.tif', 4326, (10.0, 58.0), 1 / 960, emodnet, -9999.0)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    out = str(tmp_path / 'chunk.tif')
    assert lat_model.write_chunk(model, separation, fill, GRID, 56, 10, out)
    heights = gdal.Open(out).ReadAsArray()
    row, col = int((58.0 - 57.0) / GRID.step), int((11.0 - 10.0) / GRID.step)
    assert heights[row, col] == -20.0


def test_a_height_model_on_a_levelling_datum_lands_on_lat_on_the_lat_surface_grid(tmp_path):
    # NAP 1.2 m above LAT; the NAP surface is coarser than the LAT one.
    nap = np.full((50, 50), 41.2, dtype='float32')
    lat = np.full((100, 100), 40.0, dtype='float32')
    surfaces = [
        raster(tmp_path / 'nap.tif', 4326, (4.0, 54.0), 0.02, nap, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (4.0, 54.0), 0.01, lat, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    lat_model.separation(*surfaces, separation)
    sep = gdal.Open(separation)
    assert sep.GetGeoTransform() == (4.0, 0.01, 0.0, 54.0, 0.0, -0.01)
    assert np.allclose(sep.ReadAsArray()[1:-1, 1:-1], 1.2, atol=1e-4)

    grid = lat_model.Grid(1 / 3840, 1)
    heights = np.full((40, 40), -3.0, dtype='float32')
    heights[0, 0] = 2.0  # a drying bank
    heights[0, 1] = 20.0  # a dune
    model = raster(tmp_path / 'heights.tif', 4326, (4.4, 53.6), 0.0005, heights, 3.4e38)
    out = str(tmp_path / 'chunk.tif')
    assert lat_model.write_chunk(model, separation, None, grid, 53, 4, out, depths=False)
    chunk = gdal.Open(out).ReadAsArray()

    def at(lon, lat_):
        return chunk[int((54.0 - lat_) / grid.step), int((lon - 4.0) / grid.step)]

    assert abs(at(4.41, 53.59) - (-1.8)) < 1e-3
    assert abs(at(4.40025, 53.59975) - 3.2) < 0.5
    assert at(4.40075, 53.59975) == lat_model.NODATA


def test_a_model_already_on_lat_needs_no_separation(tmp_path):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25833)
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    x, y, _ = osr.CoordinateTransformation(wgs, srs).TransformPoint(5.3, 60.4)
    heights = np.full((40, 40), -12.5, dtype='float32')
    model = raster(tmp_path / 'kv.tif', 25833, (x - 1000, y + 1000), 50, heights, 0.0)
    out = str(tmp_path / 'chunk.tif')
    assert lat_model.write_chunk(model, None, None, GRID, 60, 4, out, depths=False)
    chunk = gdal.Open(out).ReadAsArray()
    assert abs(chunk[int((62.0 - 60.4) / GRID.step), int((5.3 - 4.0) / GRID.step)] - (-12.5)) < 1e-3
