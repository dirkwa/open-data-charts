"""Denmark's Depth Model (DDM, 50 m, the Danish Geodata Agency) on the chart
datum: the model is on mean sea level (DKMSL(2022)); the agency's DKMSL and
DKLAT surfaces (CC BY 4.0, in PROJ's grid collection) give the separation to
Lowest Astronomical Tide, the datum of the other regions.

The model is written in 2-degree chunks on a 1/32 arc-minute grid, half the
EMODnet DTM's step, so EMODnet fills the chunk where the model has no data
(foreign waters) without a second grid of another step in the mosaic.
"""

import math
import os

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()

DKMSL = 'https://cdn.proj.org/dk_sdfi_dkmsl_2022.tif'
DKLAT = 'https://cdn.proj.org/dk_sdfi_dklat_2023.tif'
STEP = 1 / 1920
CHUNK_DEG = 2
NODATA = -9999.0
# Both surfaces are interpolated models of a few centimetres' accuracy; near
# the tideless Baltic they can cross, and LAT is never above mean sea level.
MIN_SEPARATION_M = 0.0
# The surfaces leave cells undefined along the coast; the nearest defined
# separation is within a few kilometres there (cells are 0.01 degree).
SEPARATION_FILL_CELLS = 50


def wgs84():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    return srs.ExportToWkt()


def separation(msl_path, lat_path, path):
    """Mean sea level above LAT, in metres, on the surfaces' own grid."""
    msl = gdal.Open(msl_path)
    lat = gdal.Open(lat_path)
    m = msl.GetRasterBand(1)
    la = lat.GetRasterBand(1)
    m_values = m.ReadAsArray().astype('float32')
    la_values = la.ReadAsArray().astype('float32')
    defined = (m_values != m.GetNoDataValue()) & (la_values != la.GetNoDataValue())
    sep = np.where(defined, np.maximum(m_values - la_values, MIN_SEPARATION_M), NODATA).astype('float32')
    out = gdal.GetDriverByName('GTiff').Create(path + '.part', msl.RasterXSize, msl.RasterYSize, 1, gdal.GDT_Float32)
    out.SetGeoTransform(msl.GetGeoTransform())
    # ETRS89 and WGS84 differ by well under a metre, nothing on a 0.01-degree surface.
    out.SetProjection(wgs84())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.WriteArray(sep)
    gdal.FillNodata(
        targetBand=band,
        maskBand=None,
        maxSearchDist=SEPARATION_FILL_CELLS,
        smoothingIterations=0,
        options=['TEMP_FILE_DRIVER=MEM'],
    )
    out = msl = lat = None
    os.replace(path + '.part', path)


def chunks(bounds):
    minlon, minlat, maxlon, maxlat = bounds
    for s in range(math.floor(minlat / CHUNK_DEG) * CHUNK_DEG, math.ceil(maxlat), CHUNK_DEG):
        for w in range(math.floor(minlon / CHUNK_DEG) * CHUNK_DEG, math.ceil(maxlon), CHUNK_DEG):
            yield s, w


def warp(path, s, w, resample):
    ds = gdal.Warp(
        '',
        path,
        format='MEM',
        dstSRS='EPSG:4326',
        outputBounds=(w, s, w + CHUNK_DEG, s + CHUNK_DEG),
        xRes=STEP,
        yRes=STEP,
        resampleAlg=resample,
        dstNodata=NODATA,
    )
    return ds.GetRasterBand(1).ReadAsArray()


def write_chunk(ddm_path, separation_path, fill_path, s, w, path):
    """Heights above LAT (negative under water) over one chunk, the model
    where it has data and the fill (heights) elsewhere; False if neither
    reaches the chunk."""
    depth = warp(ddm_path, s, w, 'bilinear')
    model = depth != NODATA
    heights = np.full(depth.shape, NODATA, dtype='float32')
    if fill_path:
        heights = warp(fill_path, s, w, 'near')
    if model.any():
        sep = warp(separation_path, s, w, 'bilinear')
        heights[model] = -depth[model] + np.where(sep[model] == NODATA, 0.0, sep[model])
    if not (heights != NODATA).any():
        return False
    out = gdal.GetDriverByName('GTiff').Create(
        path + '.part', heights.shape[1], heights.shape[0], 1, gdal.GDT_Float32, ['COMPRESS=DEFLATE', 'PREDICTOR=3']
    )
    out.SetGeoTransform((w, STEP, 0.0, s + CHUNK_DEG, 0.0, -STEP))
    out.SetProjection(wgs84())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.WriteArray(heights)
    out = None
    os.replace(path + '.part', path)
    return True
