"""A national bathymetric model on the chart datum: models on mean sea level
or a levelling datum are moved to Lowest Astronomical Tide, the datum of the
other regions, with the national surfaces of both above the ellipsoid (in
PROJ's grid collection). Models already on their national chart datum
(Norway's sjøkartnull: LAT, or up to 0.3 m below it) need no separation.

The model is written in chunks on a grid whose step divides the EMODnet DTM's
(1/16 arc-minute), so EMODnet fills the chunk where the model has no data
(foreign waters) without a second grid of another step in the mosaic.

Ports and docks the model leaves out are holes in its coverage. EMODnet
there reads shallow or dry where the basins are dredged, so a hole the fill
would chart shallower than build_unit.FILL_MAX_DEPTH is written as
build_unit.MODEL_HOLE instead: depth unknown.
"""

import math
import os

import numpy as np
from osgeo import gdal, osr

from odc.build_unit import FILL_MAX_DEPTH, MODEL_HOLE, dilate_disk

gdal.UseExceptions()

NODATA = -9999.0
# Both surfaces are interpolated models of a few centimetres' accuracy; in
# tideless waters they can cross, and LAT is never above the model's datum.
MIN_SEPARATION_M = 0.0
# Coastal models carry dunes and beaches beyond the OSM coastline; keep
# drying heights, or a land cell there charts as a drying bank (as with
# EMODnet's land cells).
MAX_HEIGHT_M = 5.0
# The surfaces leave cells undefined along the coast; the nearest defined
# separation is within a few kilometres there (cells are 0.01 degree).
SEPARATION_FILL_CELLS = 50
# A hole is model-less water the model's coverage closes over at this reach
# (about 900 m north-south): Aarhus' port is about 1.5 km across.
HOLE_REACH_DEG = 0.008


def wgs84():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    return srs.ExportToWkt()


def on_grid_of(path, like):
    """`path`'s values on `like`'s grid, nodata where it has none."""
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    if ds.GetGeoTransform() == like.GetGeoTransform() and (ds.RasterXSize, ds.RasterYSize) == (
        like.RasterXSize,
        like.RasterYSize,
    ):
        values = band.ReadAsArray().astype('float32')
        return np.where(values == band.GetNoDataValue(), NODATA, values)
    gt = like.GetGeoTransform()
    warped = gdal.Warp(
        '',
        ds,
        format='MEM',
        outputBounds=(gt[0], gt[3] + gt[5] * like.RasterYSize, gt[0] + gt[1] * like.RasterXSize, gt[3]),
        width=like.RasterXSize,
        height=like.RasterYSize,
        resampleAlg='bilinear',
        dstNodata=NODATA,
    )
    return warped.GetRasterBand(1).ReadAsArray()


def separation(datum_path, lat_path, path):
    """The model's datum above LAT, in metres, on the LAT surface's grid."""
    lat = gdal.Open(lat_path)
    la = lat.GetRasterBand(1)
    m_values = on_grid_of(datum_path, lat)
    la_values = la.ReadAsArray().astype('float32')
    defined = (m_values != NODATA) & (la_values != la.GetNoDataValue())
    sep = np.where(defined, np.maximum(m_values - la_values, MIN_SEPARATION_M), NODATA).astype('float32')
    out = gdal.GetDriverByName('GTiff').Create(path + '.part', lat.RasterXSize, lat.RasterYSize, 1, gdal.GDT_Float32)
    out.SetGeoTransform(lat.GetGeoTransform())
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
    out = lat = None
    os.replace(path + '.part', path)


class Grid:
    """Chunks of `chunk_deg` degrees on a grid of `step` degrees."""

    def __init__(self, step, chunk_deg):
        self.step = step
        self.chunk_deg = chunk_deg

    def chunks(self, bounds):
        minlon, minlat, maxlon, maxlat = bounds
        size = self.chunk_deg
        for s in range(math.floor(minlat / size) * size, math.ceil(maxlat), size):
            for w in range(math.floor(minlon / size) * size, math.ceil(maxlon), size):
                yield s, w


def warp(path, grid, s, w, resample, pad=0):
    """The chunk's cells, with `pad` more cells on every side. The shoalest
    value (`max`) is taken from the full-resolution samples: an overview has
    averaged a shoal into the water around it."""
    margin = pad * grid.step
    ds = gdal.Warp(
        '',
        path,
        options=['-ovr', 'NONE'] if resample == 'max' else [],
        format='MEM',
        dstSRS='EPSG:4326',
        outputBounds=(w - margin, s - margin, w + grid.chunk_deg + margin, s + grid.chunk_deg + margin),
        xRes=grid.step,
        yRes=grid.step,
        resampleAlg=resample,
        dstNodata=NODATA,
    )
    return ds.GetRasterBand(1).ReadAsArray()


def holes(model_path, grid, s, w):
    """Cells of the chunk the model's coverage closes over without data.
    Worked out on a margin as wide as the closing reaches, so neighbouring
    chunks agree on their shared edge."""
    reach = round(HOLE_REACH_DEG / grid.step)
    pad = 2 * reach
    covered = warp(model_path, grid, s, w, 'near', pad) != NODATA
    closed = ~dilate_disk(~dilate_disk(covered, reach), reach)
    return (closed & ~covered)[pad:-pad, pad:-pad]


def write_chunk(
    model_path, separation_path, fill_path, grid, s, w, path, depths=True, resample='bilinear', emodnet_path=None
):
    """Heights above LAT (negative under water) over one chunk, the model
    where it has data and the fill (heights) elsewhere; False if neither
    reaches the chunk, and then no chunk stays at `path`. The model holds
    depths, or heights if not `depths`; without `separation_path` it is
    already on the chart datum. A fill that is more than EMODnet (other
    models' chunks) names EMODnet in `emodnet_path`: only where the fill is
    EMODnet's own value can a hole in the model's coverage be unknown depth."""
    value = warp(model_path, grid, s, w, resample)
    heights = np.full(value.shape, NODATA, dtype='float32')
    if fill_path:
        heights = warp(fill_path, grid, s, w, 'near')
    if (value != NODATA).any():
        sep = warp(separation_path, grid, s, w, 'bilinear') if separation_path else np.zeros_like(value)
        # Without a separation the model is on another datum: the fill stands.
        model = (value != NODATA) & (sep != NODATA)
        heights[model] = (-value[model] if depths else value[model]) + sep[model]
        heights[model & (heights > MAX_HEIGHT_M)] = NODATA
        unknown = holes(model_path, grid, s, w) & ~model & (heights > -FILL_MAX_DEPTH)
        if emodnet_path:
            unknown &= heights == warp(emodnet_path, grid, s, w, 'near')
        heights[unknown] = MODEL_HOLE
    if not (heights != NODATA).any():
        # A chunk from an earlier run would still reach the builder.
        if os.path.exists(path):
            os.remove(path)
        return False
    out = gdal.GetDriverByName('GTiff').Create(
        path + '.part', heights.shape[1], heights.shape[0], 1, gdal.GDT_Float32, ['COMPRESS=DEFLATE', 'PREDICTOR=3']
    )
    out.SetGeoTransform((w, grid.step, 0.0, s + grid.chunk_deg, 0.0, -grid.step))
    out.SetProjection(wgs84())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.WriteArray(heights)
    out = None
    os.replace(path + '.part', path)
    return True
