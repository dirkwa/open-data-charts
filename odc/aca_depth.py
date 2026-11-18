"""Allen Coral Atlas satellite-derived bathymetry, cut into 1-degree squares.

The atlas hands out its bathymetry only as download packages a person
requests for an area. This module turns the GeoTIFFs of such a package into
one file per 1-degree square holding data, which the `aca-depth` release
serves and odc.sources fetches for a region's units. A square is on the
chart grid (odc.grid), each cell the shoalest depth inside it in whole
metres (1-255, 0 for no data): the builder reads nothing finer, and the
files stay a fraction of the package's size.

Usage: python3 -m odc.aca_depth <out dir> <GeoTIFF, package .zip or its download link> ...

A square already in the out dir keeps its data where the new package has
none, so packages of neighbouring areas can be added one after another.
"""

import math
import os
import sys

import numpy as np
from osgeo import gdal

from odc.grid import GRID_STEP

gdal.UseExceptions()

RELEASE = 'aca-depth'
NODATA = 0
PACKAGE_CM_PER_UNIT = 100  # package depths are in cm, down positive
CELLS_PER_DEGREE = round(1 / GRID_STEP)
DESCRIPTION = (
    'Allen Coral Atlas satellite-derived bathymetry, shoalest depth per chart cell in metres. NOT FOR NAVIGATION.'
)
CREATION = ['COMPRESS=DEFLATE', 'ZLEVEL=9', 'PREDICTOR=2', 'TILED=YES', 'BLOCKXSIZE=512', 'BLOCKYSIZE=512']


def square_name(south, west):
    return f'depth_{south:+03d}_{west:+04d}.tif'


def package_rasters(path):
    """The bathymetry GeoTIFFs of a package (a .zip file or its download
    link), read in place inside the zip."""
    if path.startswith(('http://', 'https://')):
        path = f'/vsicurl/{path}'
    elif not path.endswith('.zip'):
        return [path]
    root = f'/vsizip/{path}'
    names = [n for n in gdal.ReadDirRecursive(root) or [] if n.startswith('Bathymetry') and n.endswith('.tif')]
    if not names:
        raise SystemExit(f'{path}: no bathymetry GeoTIFF in the package')
    return [f'{root}/{name}' for name in sorted(names)]


STRIP_CELLS = 400  # chart rows warped at a time; a package raster is read top to bottom once


def to_metres(cm):
    m = np.clip(np.rint(cm / PACKAGE_CM_PER_UNIT), 1, 255).astype(np.uint8)
    return np.where(cm > 0, m, NODATA).astype(np.uint8)


def degree_rows(src):
    """(south, west, depths) per row of 1-degree squares the raster reaches,
    north to south, depths the row's chart cells in metres.

    A package's GeoTIFF sits deflated inside the zip, where reading anywhere
    but forward means inflating again from the start: the raster is read in
    full-width strips, in order, each warped onto the chart cells it covers."""
    x0, dx, _, y0, _, dy = src.GetGeoTransform()
    x1, y1 = x0 + dx * src.RasterXSize, y0 + dy * src.RasterYSize
    west, east = math.floor(x0), math.ceil(x1)
    ncols = (east - west) * CELLS_PER_DEGREE
    band = src.GetRasterBand(1)
    for north in range(math.ceil(y0), math.floor(y1), -1):
        row = np.zeros((CELLS_PER_DEGREE, ncols), np.uint8)
        for top in range(0, CELLS_PER_DEGREE, STRIP_CELLS):
            lat_top = north - top * GRID_STEP
            lat_bottom = lat_top - STRIP_CELLS * GRID_STEP
            r0 = max(0, math.floor((lat_top - y0) / dy) - 1)
            r1 = min(src.RasterYSize, math.ceil((lat_bottom - y0) / dy) + 1)
            if r1 <= r0:
                continue
            strip = mem_strip(src, band.ReadAsArray(0, r0, src.RasterXSize, r1 - r0), r0)
            cells = gdal.Warp(
                '',
                strip,
                format='MEM',
                outputBounds=[west, lat_bottom, east, lat_top],
                width=ncols,
                height=STRIP_CELLS,
                resampleAlg='min',
                srcNodata=NODATA,
                dstNodata=NODATA,
                outputType=gdal.GDT_Int16,
            )
            row[top : top + STRIP_CELLS] = to_metres(cells.GetRasterBand(1).ReadAsArray())
        yield north - 1, west, row


def mem_strip(src, data, r0):
    # Only depths below the surface take part in the minimum: the source's
    # own nodata, whatever its value, becomes NODATA.
    nodata = src.GetRasterBand(1).GetNoDataValue()
    keep = data > 0 if nodata is None else (data > 0) & (data != nodata)
    data = np.where(keep, np.minimum(data, np.iinfo(np.int16).max), NODATA).astype(np.int16)
    x0, dx, _, y0, _, dy = src.GetGeoTransform()
    ds = gdal.GetDriverByName('MEM').Create('', data.shape[1], data.shape[0], 1, gdal.GDT_Int16)
    ds.SetGeoTransform((x0, dx, 0.0, y0 + r0 * dy, 0.0, dy))
    ds.SetProjection(src.GetProjection())
    ds.GetRasterBand(1).WriteArray(data)
    ds.GetRasterBand(1).SetNoDataValue(NODATA)
    return ds


def read_square(path):
    ds = gdal.Open(path)
    return ds.GetRasterBand(1).ReadAsArray()


def write_square(path, data, south, west, projection):
    out = gdal.GetDriverByName('MEM').Create('', CELLS_PER_DEGREE, CELLS_PER_DEGREE, 1, gdal.GDT_Byte)
    out.SetGeoTransform((west, GRID_STEP, 0.0, south + 1, 0.0, -GRID_STEP))
    out.SetProjection(projection)
    out.GetRasterBand(1).WriteArray(data)
    out.GetRasterBand(1).SetNoDataValue(NODATA)
    out.SetMetadataItem('TIFFTAG_IMAGEDESCRIPTION', DESCRIPTION)
    gdal.Translate(path + '.part', out, format='GTiff', creationOptions=CREATION)
    os.replace(path + '.part', path)


def cut(sources, out_dir):
    """Writes the run's depths into the squares in out_dir. Where the run's
    rasters overlap, the shoaler depth wins; where the run has a depth, it
    replaces what earlier runs wrote, which stays where the run has none."""
    os.makedirs(out_dir, exist_ok=True)
    squares = {}
    for source in sources:
        src = gdal.Open(source)
        for south, west, row in degree_rows(src):
            for i in range(row.shape[1] // CELLS_PER_DEGREE):
                data = row[:, i * CELLS_PER_DEGREE : (i + 1) * CELLS_PER_DEGREE]
                if not data.any():
                    continue
                name = square_name(south, west + i)
                path = os.path.join(out_dir, name)
                if name in squares:
                    kept = read_square(path)
                    data = np.where((kept != NODATA) & ((data == NODATA) | (kept < data)), kept, data)
                else:
                    squares[name] = (south, west + i, src.GetProjection())
                    if os.path.exists(path):
                        os.replace(path, path + '.earlier')
                write_square(path, data, south, west + i, src.GetProjection())
                print(name, file=sys.stderr, flush=True)
    for name, (south, west, projection) in squares.items():
        path = os.path.join(out_dir, name)
        if os.path.exists(path + '.earlier'):
            data = read_square(path)
            write_square(path, np.where(data != NODATA, data, read_square(path + '.earlier')), south, west, projection)
            os.remove(path + '.earlier')
    return list(squares)


def main():
    out_dir, *packages = sys.argv[1:]
    sources = [raster for package in packages for raster in package_rasters(package)]
    gdal.SetCacheMax(1 << 30)  # holds the tile row a strip shares with the next
    for name in cut(sources, out_dir):
        print(name, flush=True)


if __name__ == '__main__':
    main()
