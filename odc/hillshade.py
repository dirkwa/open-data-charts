"""Terrain shading over land from the Copernicus DEM: a raster MBTiles per
unit, drawn by plotters as a semi-transparent layer over the chart.

Shadows are black and lit slopes white, each with an alpha, and flat ground
and water stay transparent, so the layer darkens and lightens whatever chart
lies under it. Each zoom is shaded on its own Web-Mercator pixel grid from
the DEM resampled to that grid, so a pixel comes out the same whichever unit
computes it.

Usage: python3 -m odc.hillshade <land.gpkg> <out.mbtiles> <unit id> <version> <relief tif> ...
"""

import math
import os
import sqlite3
import sys

import numpy as np
from osgeo import gdal, ogr, osr

from odc.attribution import COPERNICUS_DEM
from odc.grid import UNIT_ZOOM, Unit
from odc.tile import set_metadata

gdal.UseExceptions()

MINZOOM = UNIT_ZOOM
# GLO-30's 30 m: a z12 pixel is 38 m at the equator, finer zooms only enlarge it.
MAXZOOM = 12
TILE = 256
# Cartographic convention: light from the north-west reads as relief, not as
# its inverse.
AZIMUTH_DEG = 315.0
ALTITUDE_DEG = 45.0
SHADOW_ALPHA = 0.75
LIGHT_ALPHA = 0.35
WEBP_QUALITY = 75
# One pixel on each side for the slope at the unit's edge.
MARGIN_PX = 1
EARTH_RADIUS = 6378137.0
WORLD = 2 * math.pi * EARTH_RADIUS
DESCRIPTION = 'Terrain shading from the Copernicus DEM GLO-30. NOT FOR NAVIGATION.'


def asset_name(unit_id, version):
    return f'{unit_id}.{version}.hillshade.mbtiles'


def merc_x(px, z):
    return px * WORLD / (TILE << z) - WORLD / 2


def merc_y(py, z):
    return WORLD / 2 - py * WORLD / (TILE << z)


def mercator():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    return srs


def lon_of(px, z):
    return px / (TILE << z) * 360.0 - 180.0


def lat_of(py, z):
    return math.degrees(math.atan(math.sinh(merc_y(py, z) / EARTH_RADIUS)))


def warp_dem(relief, px0, py0, width, height, z):
    """The DEM on global pixels px0.. and py0.. of zoom z. Where no DEM tile
    exists (open sea) the height is 0."""
    ds = gdal.Warp(
        '',
        relief,
        format='MEM',
        dstSRS='EPSG:3857',
        outputBounds=(merc_x(px0, z), merc_y(py0 + height, z), merc_x(px0 + width, z), merc_y(py0, z)),
        width=width,
        height=height,
        outputType=gdal.GDT_Float32,
        # Below MAXZOOM a pixel spans several DEM samples.
        resampleAlg='bilinear' if z == MAXZOOM else 'average',
        warpOptions=['INIT_DEST=0'],
    )
    return ds.GetRasterBand(1).ReadAsArray().astype(np.float64)


def land_mask(land_path, px0, py0, width, height, z):
    ds = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
    pixel = WORLD / (TILE << z)
    ds.SetGeoTransform((merc_x(px0, z), pixel, 0.0, merc_y(py0, z), 0.0, -pixel))
    ds.SetProjection(mercator().ExportToWkt())
    source = ogr.Open(land_path)
    layer = source.GetLayer(0)
    layer.SetSpatialFilterRect(lon_of(px0, z), lat_of(py0 + height, z), lon_of(px0 + width, z), lat_of(py0, z))
    gdal.RasterizeLayer(ds, [1], layer, burn_values=[1])
    return ds.GetRasterBand(1).ReadAsArray().astype(bool)


def shade(dem, py0, z):
    """Lambertian shading, 1 facing the light, sin(altitude) on flat ground."""
    pixel = WORLD / (TILE << z)
    rows = py0 + np.arange(dem.shape[0]) + 0.5
    lat = np.arctan(np.sinh(merc_y(rows, z) / EARTH_RADIUS))
    ground = pixel * np.cos(lat)[:, None]
    # Rows grow south, so the northward slope is the negative row gradient.
    d_row, d_col = np.gradient(dem)
    east, north = d_col / ground, -d_row / ground
    az, alt = math.radians(AZIMUTH_DEG), math.radians(ALTITUDE_DEG)
    light = (math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt))
    lit = -east * light[0] - north * light[1] + light[2]
    return np.clip(lit / np.sqrt(east**2 + north**2 + 1), 0.0, 1.0)


def render(relief, land_path, px0, py0, width, height, z):
    """RGBA of global pixels px0.. and py0.. at zoom z."""
    m = MARGIN_PX
    dem = warp_dem(relief, px0 - m, py0 - m, width + 2 * m, height + 2 * m, z)
    lit = shade(dem, py0 - m, z)[m:-m, m:-m]
    flat = math.sin(math.radians(ALTITUDE_DEG))
    dark = np.clip((flat - lit) / flat, 0.0, 1.0) * SHADOW_ALPHA
    bright = np.clip((lit - flat) / (1.0 - flat), 0.0, 1.0) * LIGHT_ALPHA
    alpha = np.where(dark > 0, dark, bright) * land_mask(land_path, px0, py0, width, height, z)
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., :3] = np.where(dark > 0, 0, 255)[..., None]
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    return rgba


def encode(rgba):
    ds = gdal.GetDriverByName('MEM').Create('', TILE, TILE, 4, gdal.GDT_Byte)
    for band in range(4):
        ds.GetRasterBand(band + 1).WriteArray(rgba[..., band])
    path = '/vsimem/hillshade-tile.webp'
    gdal.GetDriverByName('WEBP').CreateCopy(path, ds, options=[f'QUALITY={WEBP_QUALITY}'])
    data = gdal.VSIFOpenL(path, 'rb')
    try:
        gdal.VSIFSeekL(data, 0, 2)
        size = gdal.VSIFTellL(data)
        gdal.VSIFSeekL(data, 0, 0)
        return bytes(gdal.VSIFReadL(1, size, data))
    finally:
        gdal.VSIFCloseL(data)
        gdal.Unlink(path)


def unit_tiles(relief, land_path, unit):
    """(z, x, y, webp) for each tile of the unit with any shading, XYZ rows."""
    for z in range(MINZOOM, MAXZOOM + 1):
        k = 1 << (z - UNIT_ZOOM)
        rgba = render(relief, land_path, unit.x * k * TILE, unit.y * k * TILE, k * TILE, k * TILE, z)
        for ty in range(k):
            for tx in range(k):
                tile = rgba[ty * TILE : (ty + 1) * TILE, tx * TILE : (tx + 1) * TILE]
                if tile[..., 3].any():
                    yield z, unit.x * k + tx, unit.y * k + ty, encode(tile)


def write_unit(relief, land_path, out, unit, version):
    """The unit's shading as a raster MBTiles, or no file when the unit has
    none. Returns the number of tiles."""
    if not relief:
        return 0
    vrt = '/vsimem/hillshade-relief.vrt'
    gdal.BuildVRT(vrt, relief).Close()
    try:
        tiles = list(unit_tiles(vrt, land_path, unit))
    finally:
        gdal.Unlink(vrt)
    if not tiles:
        return 0
    if os.path.exists(out):
        os.remove(out)
    with sqlite3.connect(out) as db:
        db.execute('CREATE TABLE metadata (name TEXT, value TEXT)')
        db.execute('CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)')
        db.execute('CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)')
        # MBTiles rows count from the south (TMS).
        db.executemany(
            'INSERT INTO tiles VALUES (?, ?, ?, ?)', [(z, x, (1 << z) - 1 - y, data) for z, x, y, data in tiles]
        )
    set_metadata(
        out,
        {
            'name': f'Open Data Charts {unit.id} terrain shading (not for navigation)',
            'description': DESCRIPTION,
            'attribution': COPERNICUS_DEM,
            'format': 'webp',
            'type': 'overlay',
            'bounds': ','.join(repr(v) for v in unit.bounds),
            'minzoom': str(MINZOOM),
            'maxzoom': str(MAXZOOM),
            'x-odc-unit': unit.id,
            'x-odc-version': version,
        },
    )
    with sqlite3.connect(out) as db:
        db.execute('VACUUM')
    return len(tiles)


def main():
    land_path, out, unit_id, version, *relief = sys.argv[1:]
    print(write_unit(relief, land_path, out, Unit.parse(unit_id), version), 'tiles')


if __name__ == '__main__':
    main()
