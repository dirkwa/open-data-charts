import numpy as np
from osgeo import gdal, osr

from odc import ddm


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
    ddm.separation(*surfaces, separation)
    sep = gdal.Open(separation).ReadAsArray()
    assert np.allclose(sep, 0.3, atol=1e-4)

    emodnet = np.full((1920, 1920), -20.0, 'float32')
    fill = raster(tmp_path / 'emodnet.tif', 4326, (10.0, 58.0), 1 / 960, emodnet, -9999.0)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    out = str(tmp_path / 'chunk.tif')
    assert ddm.write_chunk(model, separation, fill, 56, 10, out)
    ds = gdal.Open(out)
    assert ds.GetGeoTransform() == (10.0, ddm.STEP, 0.0, 58.0, 0.0, -ddm.STEP)
    heights = ds.ReadAsArray()
    row, col = int((58.0 - 57.0) / ddm.STEP), int((11.0 - 10.0) / ddm.STEP)
    assert abs(heights[row, col] - (-9.7)) < 1e-3
    assert heights[10, 10] == -20.0


def test_a_chunk_neither_source_reaches_is_not_written(tmp_path):
    msl = np.full((10, 10), 40.5, dtype='float32')
    surfaces = [
        raster(tmp_path / 'msl.tif', 4326, (8.0, 58.0), 0.01, msl, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (8.0, 58.0), 0.01, msl - 0.1, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    ddm.separation(*surfaces, separation)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    assert not ddm.write_chunk(model, separation, None, 52, 0, str(tmp_path / 'chunk.tif'))


def test_the_model_gives_way_to_the_fill_where_no_separation_reaches(tmp_path):
    msl = np.full((10, 10), 40.5, dtype='float32')
    surfaces = [
        raster(tmp_path / 'msl.tif', 4326, (8.0, 58.0), 0.01, msl, -32768.0),
        raster(tmp_path / 'lat.tif', 4326, (8.0, 58.0), 0.01, msl - 0.1, -32768.0),
    ]
    separation = str(tmp_path / 'sep.tif')
    ddm.separation(*surfaces, separation)
    emodnet = np.full((1920, 1920), -20.0, 'float32')
    fill = raster(tmp_path / 'emodnet.tif', 4326, (10.0, 58.0), 1 / 960, emodnet, -9999.0)
    model = model_near(tmp_path, 11.0, 57.0, 10.0)
    out = str(tmp_path / 'chunk.tif')
    assert ddm.write_chunk(model, separation, fill, 56, 10, out)
    heights = gdal.Open(out).ReadAsArray()
    row, col = int((58.0 - 57.0) / ddm.STEP), int((11.0 - 10.0) / ddm.STEP)
    assert heights[row, col] == -20.0
