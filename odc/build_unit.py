#!/usr/bin/env python3
"""Build the S-57-shaped GeoJSON layers of one unit (a z9 Web-Mercator tile).

Runs in the builder image (GDAL Python bindings). Everything is computed on
the unit's build box (the unit plus a margin, on the global grid, see
odc.grid) and written as one GeoJSON FeatureCollection per S-57 object
class, shaped the way signalk-charts-provider-simple's S-57 pipeline shapes
GDAL's S-57 export: upper-case attribute acronyms, list attributes as
comma-separated strings, plus OBJL / RCID / LNAM / PRIM. That is the schema
Freeboard-SK's S-52 renderer reads. odc.tile then cuts the tiles to the
unit's own bounds, so neighbouring units meet without overlap.

Sources (all open):
  - Shom WFS layers named after S-57 classes (Licence Ouverte Etalab 2.0)
  - Shom bathymetric surveys (levés), reduced by odc.reduce_survey
  - a bathymetric DEM of the region (New Caledonia: Shom-IRD, LAT datum)
  - OpenStreetMap coastline, piers, breakwaters, slipways, reefs,
    buildings near the sea, peaks (ODbL)
  - Copernicus DEM GLO-30 for the land contours (free licence, attribution)
  - NOAA NCEI ETOPO 2022 for deep water no other source covers (no use restrictions)
  - Gouvernement de la Nouvelle-Calédonie (Licence Ouverte): reef
    geomorphology (IRD / DINUM) and OBLIC coastal structures 2016 of Nouméa
  - Allen Coral Atlas reef geomorphology (CC BY 4.0)
None of it is meant for navigation.
"""

import argparse
import csv
import glob
import json
import math
import os
import time

import numpy as np
from osgeo import gdal, ogr, osr

from odc import osm, seamarks
from odc.grid import GRID_STEP, MAX_ZOOM, UNIT_ZOOM, Unit, mercator_pixel

gdal.UseExceptions()
ogr.UseExceptions()

S57_CSV_DIR = '/usr/share/gdal'
# Depth contours / area band edges in metres below chart datum (LAT).
DEPTH_LEVELS = [0, 2, 5, 10, 20, 30, 50, 100, 200, 500, 1000, 2000, 3000, 5000]
DRYING_DRVAL1 = -5.0  # drying areas: DRVAL1 = -(max drying height)
DRYING_TOP_Z = 50.0  # above any drying height in the DEM (max ~5 m)
MIN_UNSARE_DEG2 = 1e-9  # only numerical crumbs; real gaps must not turn into holes
FILL_SEARCH_CELLS = 4  # fill DEM gaps up to 4 cells (~400 m) from data
FILL_MAX_DEPTH = 2.0  # filled near-shore cells are at most 2 m deep
EMPTY_DEM_STEP = 0.001  # degrees; about the 100 m of the Shom DEMs
SIMPLIFY_DEG = 0.00003  # ~3 m, applied to grid-derived geometry only
MIN_AREA_DEG2 = 1e-8
COAST_EDGE_DEG = 1e-7  # see build_land()  # ~120 m2; drop slivers from land/DEM mismatch
NODATA = -9999.0
# Survey soundings are reduced to the shoalest value per grid cell. Gaps
# between single-beam lines are bridged by interpolation up to this many
# cells (~150 m). Where the 100 m DEM is clearly shallower than such an
# interpolated value (a reef between two lines), the DEM wins.
INTERP_CELLS = 12
# Contours below SMOOTH_BELOW_M are drawn from a grid averaged over
# SMOOTH_CELLS x SMOOTH_CELLS cells (~100 m): over flat multibeam ground the
# raw cells draw hundreds of noise rings around the 20/30 m levels. A cell
# may come out at most max(2 m, 10 %) deeper than measured, never above
# SMOOTH_BELOW_M; shallower is always allowed. Soundings stay unsmoothed.
SMOOTH_BELOW_M = 10.0
SMOOTH_CELLS = 8
SMOOTH_MAX_DEEPER_M = 2.0
SMOOTH_MAX_DEEPER_REL = 0.10
DEM_SHOAL_TOL_M = 1.0
DEM_SHOAL_TOL_REL = 0.15
# SOUNDG thinning: per zoom, each 256 px tile is cut into this many squares
# a side (~43 px each), and each square keeps at most one sounding, the
# shoalest; it stays visible at all higher zooms. Squares nest inside tiles,
# and tiles from z10 on inside units, so a unit thins alone and its
# neighbours agree on its edge.
SOUNDING_SQUARES_PER_TILE = 6
CONTOUR_WINDOW_CELLS = 512  # see grid_tiles()
LAND_PIECES = 16  # see tiled_parts()
# Closed DEPCNT rings shorter than this (degrees of perimeter) are dropped:
# (deepest VALDCO the rule applies to, minimum length).
DEPCNT_MIN_RING = ((10.0, 0.0005), (5000.0, 0.004))  # ~55 m; ~430 m (~140 m across)
SOUNDING_MINZOOM = 10
QUALITY_ORDER = ('multibeam', 'survey-recent', 'survey-old', 'model', 'satellite', 'model-coarse', 'none')
# ETOPO 2022 (15 arc-seconds, ~460 m) fills only deep water nothing finer
# covers: pixels at least ETOPO_MIN_DEPTH_M deep whose neighbours within
# ETOPO_CLEAR_PIXELS are too, so a pixel mixing reef or shore with the
# slope below it never charts as deep water near a hazard.
ETOPO_PIXEL_DEG = 1 / 240
ETOPO_MIN_DEPTH_M = 200.0
ETOPO_CLEAR_PIXELS = 2
ETOPO_MARGIN_DEG = (ETOPO_CLEAR_PIXELS + 2) * ETOPO_PIXEL_DEG
SURVEY_CATEGORIES = QUALITY_ORDER[:3]
TECSOU = (('multi-beam', '3'), ('single-beam', '1'), ('sidescan', '2'), ('plummet', '5'))

# Harbour structures. OSM man_made -> S-57 CATSLC (shoreline construction).
OSM_CATSLC = {'breakwater': '1', 'groyne': '2', 'pier': '4', 'quay': '6'}
CATSLC_SLIPWAY = '13'
# OBLIC 2016 coastal structures (Nouméa) -> CATSLC. Bridges and pipes are
# left out (OSM has the bridges; S-52 has no symbol for a pipe outline).
OBLIC_CATSLC = {'Wharf': '6', 'Enrochement': '8', "Cale de mise à l'eau": '13', 'Muret': '10'}
# Reef map classes drawn as reef: WATLEV 4 = covers and uncovers (Freeboard
# fills it like a drying area, dashed outline), 3 = always under water
# (danger area of unknown depth, dotted outline). NATSUR 14 = coral.
REEF_WATLEV = {'reef flat': '4', 'pass reef flat': '4', 'subtidal reef flat': '3'}
NATSUR_CORAL = '14'
REEF_SIMPLIFY_DEG = 0.00004  # ~4 m; the reef map's edges are raster steps
# Allen Coral Atlas classes charted as reef, mapped from satellite imagery
# down to about 10-15 m. Flats and crests dry or nearly so; patch reefs,
# back-reef slopes and shallow lagoons (under about 5 m) are a danger of
# unknown depth. Deep lagoon and fore-reef slopes are left to the depths.
ACA_WATLEV = {
    'Reef Crest': '4',
    'Outer Reef Flat': '4',
    'Inner Reef Flat': '4',
    'Terrestrial Reef Flat': '4',
    'Microatoll': '4',
    'Patch Reefs': '3',
    'Small Reef': '3',
    'Back Reef Slope': '3',
    'Shallow Lagoon': '3',
}
PIER_MINZOOM = 14  # pontoon/jetty lines would be a smudge below that
STRUCTURE_MINZOOM = 13
# Buildings (BUISGL) and land relief (LNDELV) are S-52 display category
# "Other": Freeboard-SK 3.5 does not draw them yet; they are in the chart
# for when it does (and for other viewers).
BUILDING_MINZOOM = 15
BUILDING_NEAR_WATER_CELLS = 12  # keep buildings within ~150 m of the sea
RELIEF_INTERVAL_M = 100
RELIEF_CELL_DEG = 1 / 1800  # contour the 30 m DEM averaged to ~60 m: DSM noise
RELIEF_SIMPLIFY_DEG = 0.0001  # ~10 m
RELIEF_MINZOOM = 10
COP30_SRC = (
    'Copernicus DEM GLO-30, (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH '
    '2014-2018 provided under COPERNICUS by the European Union and ESA'
)
OSM_SRC = 'OpenStreetMap contributors (ODbL)'
OBLIC_SRC = 'OBLIC coastal structures 2016 (Gouvernement NC / DIMENC, Licence Ouverte)'
REEF_SRC = (
    'Geomorphologie des recifs (IRD / Gouvernement NC, Licence Ouverte), '
    'mapped from satellite imagery, position approximate'
)
ACA_SRC = 'Allen Coral Atlas (CC BY 4.0), mapped from satellite imagery, position approximate'
SDB_SRC = 'Allen Coral Atlas satellite-derived bathymetry (CC BY 4.0), unsurveyed, depth band approximate'

WRECK_QUASOU = {
    'brassiage minimal connu': '6',  # least depth known
    'brassiage inconnu': '2',  # depth unknown
    'brassiage inconnu mais estimé supérieur au brassiage indiqué': '7',
    'valeur signalée n’ayant pas fait l’objet d’un levé': '8',  # reported, not surveyed
}


T0 = time.monotonic()


def step(msg):
    print(f'[{time.monotonic() - T0:6.0f}s] {msg}', flush=True)


def load_s57_tables():
    classes, attributes = {}, set()
    with open(os.path.join(S57_CSV_DIR, 's57objectclasses.csv'), newline='') as fh:
        for row in csv.DictReader(fh):
            classes[row['Acronym']] = int(row['Code'])
    with open(os.path.join(S57_CSV_DIR, 's57attributes.csv'), newline='') as fh:
        for row in csv.DictReader(fh):
            attributes.add(row['Acronym'])
    return classes, attributes


def bbox_polygon(minx, miny, maxx, maxy):
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)):
        ring.AddPoint_2D(x, y)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def polygon_parts(geom):
    """Yield the non-empty polygons inside any geometry."""
    if geom is None or geom.IsEmpty():
        return
    gtype = ogr.GT_Flatten(geom.GetGeometryType())
    if gtype == ogr.wkbPolygon:
        yield geom.Clone()
    elif gtype in (ogr.wkbMultiPolygon, ogr.wkbGeometryCollection):
        for i in range(geom.GetGeometryCount()):
            yield from polygon_parts(geom.GetGeometryRef(i))


def line_parts(geom):
    if geom is None or geom.IsEmpty():
        return
    gtype = ogr.GT_Flatten(geom.GetGeometryType())
    if gtype == ogr.wkbLineString:
        yield geom.Clone()
    elif gtype in (ogr.wkbMultiLineString, ogr.wkbGeometryCollection):
        for i in range(geom.GetGeometryCount()):
            yield from line_parts(geom.GetGeometryRef(i))


def linestring(coords):
    line = ogr.Geometry(ogr.wkbLineString)
    for x, y in coords:
        line.AddPoint_2D(x, y)
    return line


# --------------------------------------------------------------- coastline


def build_land(land_path, bbox_geom, water=None):
    """Land and coastline from OpenStreetMap's land polygons (osmdata.openstreetmap.de),
    which are assembled from the coastline and validated every day, less the
    water areas behind the coastline. Returns those too, as the inland water."""
    ds = ogr.Open(land_path)
    layer = ds.GetLayer(0)
    layer.SetSpatialFilter(bbox_geom)
    pieces = ogr.Geometry(ogr.wkbMultiPolygon)
    for feat in layer:
        for part in polygon_parts(feat.GetGeometryRef().Intersection(bbox_geom)):
            pieces.AddGeometry(part)
    land = pieces.UnaryUnion() if pieces.GetGeometryCount() else ogr.Geometry(ogr.wkbMultiPolygon)
    inland = ogr.Geometry(ogr.wkbMultiPolygon)
    if water is not None and not water.IsEmpty() and not land.IsEmpty():
        inland = water.Intersection(land)
        land = land.Difference(water).MakeValid()
    # The box's own edges are not coastline.
    inside = bbox_polygon(*shrunk(bbox_geom.GetEnvelope(), COAST_EDGE_DEG))
    coast = list(line_parts(land.GetBoundary().Intersection(inside))) if not land.IsEmpty() else []
    return land, coast, inland


def shrunk(envelope, d):
    minx, maxx, miny, maxy = envelope
    return minx + d, miny + d, maxx - d, maxy - d


# --------------------------------------------------------------------- DEM


def empty_dem(path, window):
    minx, maxy, maxx, miny = window
    ncols, nrows = (round((maxx - minx) / EMPTY_DEM_STEP), round((maxy - miny) / EMPTY_DEM_STEP))
    ds = gdal.GetDriverByName('GTiff').Create(path, ncols, nrows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((minx, EMPTY_DEM_STEP, 0.0, maxy, 0.0, -EMPTY_DEM_STEP))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(NODATA)
    band.Fill(NODATA)
    ds = None


def dem_meets(dem_path, window):
    minx, maxy, maxx, miny = window
    ds = gdal.Open(dem_path)
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    x1, y1 = x0 + dx * ds.RasterXSize, y0 + dy * ds.RasterYSize
    return x0 < maxx and minx < x1 and y1 < maxy and miny < y0


# The DEM is read this far beyond the build box, so the gap fill sees past its edge.
DEM_MARGIN_DEG = 0.01


def prepare_dem(dem_path, bbox, work):
    minx, miny, maxx, maxy = bbox
    margin = DEM_MARGIN_DEG
    clipped = os.path.join(work, 'dem_clip.tif')
    window = [minx - margin, maxy + margin, maxx + margin, miny - margin]
    if dem_path and dem_meets(dem_path, window):
        # The .asc grid carries no CRS; it is WGS84 geographic per the metadata.
        gdal.Translate(clipped, dem_path, outputSRS='EPSG:4326', projWin=window)
    else:
        empty_dem(clipped, window)
    # The DEM's land mask is 100 m blocks, so between its last water cell
    # and the OSM coastline there is a stair-stepped strip without depth.
    # Fill it from the neighbouring cells, but never deeper than
    # FILL_MAX_DEPTH: an unknown near-shore strip reads as shallow, not deep.
    filled = os.path.join(work, 'dem_filled.tif')
    gdal.Translate(filled, clipped)
    ds = gdal.Open(filled, gdal.GA_Update)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    before = band.ReadAsArray()
    gdal.FillNodata(
        targetBand=band,
        maskBand=None,
        maxSearchDist=FILL_SEARCH_CELLS,
        smoothingIterations=0,
        options=['TEMP_FILE_DRIVER=MEM'],
    )
    after = band.ReadAsArray()
    new = (before == nodata) & (after != nodata)
    after[new] = after[new].clip(min=-FILL_MAX_DEPTH)
    band.WriteArray(after)
    ds.FlushCache()
    ds = None
    return clipped, filled


def chart_grid(bbox):
    minx, miny, maxx, maxy = bbox
    ncols = int(round((maxx - minx) / GRID_STEP))
    nrows = int(round((maxy - miny) / GRID_STEP))
    return ncols, nrows, (minx, GRID_STEP, 0.0, maxy, 0.0, -GRID_STEP)


def mem_raster(arr, gt, dtype=gdal.GDT_Float32, nodata=NODATA):
    ds = gdal.GetDriverByName('MEM').Create('', arr.shape[1], arr.shape[0], 1, dtype)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(arr)
    return ds


# ----------------------------------------------------------------- surveys


def load_surveys(surveys, box):
    """Composite the reduced surveys onto the box's grid, newest on top.

    surveys: [(meta, npz path)] with meta holding name, start, end, tecsou.
    Returns (entries, depth, rank): entries sorted oldest first as
    (end, start, name, path, meta), depth in m (down positive, NaN where no
    survey), rank = index into entries of the survey that supplied each cell
    (-1 for none).
    """
    entries = sorted((str(m['end']), str(m['start']), m['name'], path, m) for m, path in surveys)
    depth = np.full(box.nrows * box.ncols, np.nan, np.float32)
    rank = np.full(box.nrows * box.ncols, -1, np.int16)
    for r, (_, _, _, path, _) in enumerate(entries):
        with np.load(path) as d:
            ix, iy = d['ix'], d['iy']
            inside = box.contains(ix, iy)
            cell = box.flat_index(ix[inside], iy[inside])
            depth[cell] = d['z'][inside]
            rank[cell] = r
    print(f'  {len(entries)} surveys, {int((rank >= 0).sum())} grid cells with soundings')
    return entries, depth.reshape(box.nrows, box.ncols), rank.reshape(box.nrows, box.ncols)


def deep_water(etopo_paths, bbox):
    """ETOPO depths (down positive) on the chart grid where ETOPO_MIN_DEPTH_M
    and the clearance hold, NaN elsewhere. Worked on ETOPO's own global
    pixels, so two units agree on every cell they share."""
    ncols, nrows, _ = chart_grid(bbox)
    minx, miny, maxx, maxy = bbox
    m = ETOPO_MARGIN_DEG
    mosaic = gdal.BuildVRT('/vsimem/etopo.vrt', etopo_paths)
    window = gdal.Translate('', mosaic, format='MEM', projWin=[minx - m, maxy + m, maxx + m, miny - m])
    z = window.GetRasterBand(1).ReadAsArray()
    deep = z <= -ETOPO_MIN_DEPTH_M
    clear = deep.copy()
    c = ETOPO_CLEAR_PIXELS
    padded = np.pad(deep, c, constant_values=False)
    for dy in range(2 * c + 1):
        for dx in range(2 * c + 1):
            clear &= padded[dy : dy + deep.shape[0], dx : dx + deep.shape[1]]
    kept = mem_raster(np.where(clear, z, NODATA).astype(np.float32), window.GetGeoTransform())
    grid = gdal.Warp(
        '',
        kept,
        format='MEM',
        outputBounds=list(bbox),
        width=ncols,
        height=nrows,
        resampleAlg='bilinear',
        srcNodata=NODATA,
        dstNodata=NODATA,
    ).ReadAsArray()
    mosaic = window = kept = None
    gdal.Unlink('/vsimem/etopo.vrt')
    return np.where(grid == NODATA, np.nan, -grid).astype(np.float32)


def build_depth_grid(dem_filled, bbox, work, survey_depth, deep=None, inland=None):
    """Merge surveys, DEM and deep water into one z-negative grid for contouring.

    Returns the grid's path, the cells whose depth came from a survey,
    measured or interpolated (None without surveys), and the cells whose
    depth came from deep water alone (None without it). In inland water
    (cells of the inland mask) the model is kept only below chart datum: its
    heights there are the land it sees around a narrow canal or basin."""
    ncols, nrows, gt = chart_grid(bbox)
    dem_path = os.path.join(work, 'dem_grid.tif')
    gdal.Warp(
        dem_path,
        dem_filled,
        outputBounds=list(bbox),
        width=ncols,
        height=nrows,
        resampleAlg='cubicspline',
        dstNodata=NODATA,
        outputType=gdal.GDT_Float32,
    )
    dem = gdal.Open(dem_path).ReadAsArray()
    if inland is not None:
        dry = inland & (dem != NODATA) & (dem >= 0)
        if dry.any():
            dem[dry] = NODATA
            ds = gdal.Open(dem_path, gdal.GA_Update)
            ds.GetRasterBand(1).WriteArray(dem)
            ds = None
    final = np.where(dem == NODATA, np.nan, -dem).astype(np.float32)  # depth, down positive
    del dem
    from_deep = None
    if deep is not None:
        from_deep = np.isnan(final) & np.isfinite(deep)
        final[from_deep] = deep[from_deep]
    from_survey = None
    if survey_depth is not None:
        measured = np.isfinite(survey_depth)
        ds = mem_raster(np.where(measured, survey_depth, NODATA).astype(np.float32), gt)
        band = ds.GetRasterBand(1)
        gdal.FillNodata(
            targetBand=band,
            maskBand=None,
            maxSearchDist=INTERP_CELLS,
            smoothingIterations=0,
            options=['TEMP_FILE_DRIVER=MEM'],
        )
        interp = band.ReadAsArray()
        ds = None
        gap = ~measured & (interp != NODATA)
        tol = np.maximum(DEM_SHOAL_TOL_M, DEM_SHOAL_TOL_REL * np.abs(interp))
        dem_shoaler = np.isfinite(final) & (final < interp - tol)
        use = gap & ~dem_shoaler
        final[use] = interp[use]
        final[measured] = survey_depth[measured]
        from_survey = measured | use
        if from_deep is not None:
            from_deep &= ~from_survey
        print(
            f'  cells: {int(measured.sum())} measured, {int(use.sum())} interpolated, '
            f'{int((gap & dem_shoaler).sum())} DEM shoaler than interpolation'
        )
        del interp, gap, tol, dem_shoaler, use
    if SMOOTH_CELLS > 1:
        src = mem_raster(np.where(np.isfinite(final), final, NODATA).astype(np.float32), gt)
        coarse = gdal.Warp(
            '',
            src,
            format='MEM',
            width=ncols // SMOOTH_CELLS,
            height=nrows // SMOOTH_CELLS,
            resampleAlg='average',
            srcNodata=NODATA,
            dstNodata=NODATA,
        )
        fine = gdal.Warp(
            '',
            coarse,
            format='MEM',
            width=ncols,
            height=nrows,
            outputBounds=list(bbox),
            resampleAlg='bilinear',
            srcNodata=NODATA,
            dstNodata=NODATA,
        )
        smooth = fine.ReadAsArray()
        src = coarse = fine = None
        deep = np.isfinite(final) & (final > SMOOTH_BELOW_M) & (smooth != NODATA)
        upper = final + np.maximum(SMOOTH_MAX_DEEPER_M, SMOOTH_MAX_DEEPER_REL * final)
        final[deep] = np.clip(smooth[deep], SMOOTH_BELOW_M + 0.01, upper[deep])
        del smooth, deep, upper
    out = os.path.join(work, 'depth_grid.tif')
    z = np.where(np.isfinite(final), -final, NODATA).astype(np.float32)
    gdal.Translate(out, mem_raster(z, gt), creationOptions=['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES'])
    return out, from_survey, from_deep


def land_mask(land, bbox, all_touched=False):
    ncols, nrows, gt = chart_grid(bbox)
    ds = mem_raster(np.zeros((nrows, ncols), np.uint8), gt, gdal.GDT_Byte, None)
    if land is not None and not land.IsEmpty():
        mem = ogr.GetDriverByName('MEM').CreateDataSource('land')
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        layer = mem.CreateLayer('land', srs, ogr.wkbMultiPolygon)
        feat = ogr.Feature(layer.GetLayerDefn())
        feat.SetGeometry(land)
        layer.CreateFeature(feat)
        gdal.RasterizeLayer(ds, [1], layer, burn_values=[1], options=['ALL_TOUCHED=TRUE'] if all_touched else [])
    return ds.ReadAsArray().astype(bool)


def inland_mask(land, inland, bbox):
    """The cells inland water reaches, a canal narrower than a cell included,
    but no cell whose centre is in the sea: the sea keeps its drying banks."""
    if inland.IsEmpty():
        return None
    behind_coast = land_mask(land, bbox) | land_mask(inland, bbox)
    return land_mask(inland, bbox, all_touched=True) & behind_coast


def tecsou(text):
    codes = [code for key, code in TECSOU if key in (text or '').lower()]
    return ','.join(dict.fromkeys(codes))


def sounding_squares(lon, lat, zoom):
    px, py = mercator_pixel(lon, lat, zoom)
    n = 2**zoom * SOUNDING_SQUARES_PER_TILE
    row = (py * SOUNDING_SQUARES_PER_TILE / 256).astype(np.int64)
    return row * n + (px * SOUNDING_SQUARES_PER_TILE / 256).astype(np.int64)


def shoalest(key, *cols):
    """One entry per key, the shoalest; cols[2] is the depth."""
    order = np.lexsort((cols[2], key))
    key = key[order]
    first = np.ones(len(key), bool)
    first[1:] = key[1:] != key[:-1]
    return [c[order][first] for c in cols]


def thin_soundings(lon, lat, z, maxzoom):
    """minzoom per sounding, -1 for the ones no zoom shows."""
    minzoom = np.full(len(z), -1, np.int8)
    order = np.argsort(z, kind='stable')  # shoalest first
    for zoom in range(SOUNDING_MINZOOM, maxzoom + 1):
        k = sounding_squares(lon, lat, zoom)
        taken = np.unique(k[minzoom >= 0])
        cand = order[minzoom[order] < 0]
        cand = cand[~np.isin(k[cand], taken)]
        _, first = np.unique(k[cand], return_index=True)
        minzoom[cand[first]] = zoom
    return minzoom


def build_soundings(entries, rank, land_cells, box, unit, maxzoom):
    """SOUNDG points inside the unit: shoal-biased selection from the
    surveys that won each cell."""
    rank_flat = rank.ravel()
    on_land = land_cells.ravel()
    parts = []
    for r, (_, _, _, path, _) in enumerate(entries):
        with np.load(path) as d:
            ix, iy = d['ix'], d['iy']
            inside = box.contains(ix, iy)
            cell = box.flat_index(ix[inside], iy[inside])
            lon, lat, z = d['lon'][inside] / 1e7, d['lat'][inside] / 1e7, d['z'][inside]
        win = (rank_flat[cell] == r) & ~on_land[cell]
        lon, lat, z = lon[win], lat[win], z[win]
        px, py = mercator_pixel(lon, lat, UNIT_ZOOM)
        mine = ((px // 256).astype(np.int64) == unit.x) & ((py // 256).astype(np.int64) == unit.y)
        lon, lat, z = lon[mine], lat[mine], z[mine]
        src = np.full(len(z), r, np.int16)
        parts.append(shoalest(sounding_squares(lon, lat, maxzoom), lon, lat, z, src))
    if not parts:
        return []
    lon, lat, z, src = (np.concatenate([p[i] for p in parts]) for i in range(4))
    lon, lat, z, src = shoalest(sounding_squares(lon, lat, maxzoom), lon, lat, z, src)
    minzoom = thin_soundings(lon, lat, z, maxzoom)

    feats = []
    for i in np.flatnonzero(minzoom >= 0):
        meta = entries[src[i]][4]
        name = entries[src[i]][2]
        # Round towards the shallow side (S-4: soundings are truncated).
        attrs = {
            'DEPTH': math.floor(float(z[i]) * 10) / 10,
            'SORDAT': str(meta['end']),
            'INFORM': f'Shom survey {name} ({str(meta["end"])[:4]}), doi:10.17183/{name}',
            '_minzoom': int(minzoom[i]),
        }
        tech = tecsou(meta.get('tecsou'))
        if tech:
            attrs['TECSOU'] = tech
        pt = ogr.Geometry(ogr.wkbPoint)
        pt.AddPoint_2D(float(lon[i]), float(lat[i]))
        feats.append((pt, attrs, None))
    counts = np.bincount(minzoom[minzoom >= 0] - SOUNDING_MINZOOM)
    print('  soundings by minzoom:', {SOUNDING_MINZOOM + i: int(n) for i, n in enumerate(counts)})
    return feats


def contour_layer(src, polygonize):
    ds = gdal.Open(src) if isinstance(src, str) else src
    band = ds.GetRasterBand(1)
    mem = ogr.GetDriverByName('MEM').CreateDataSource('c')
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    layer = mem.CreateLayer('c', srs, ogr.wkbMultiPolygon if polygonize else ogr.wkbLineString)
    layer.CreateField(ogr.FieldDefn('ID', ogr.OFTInteger))
    # DEM is z-negative (depth < 0). The extra top level closes the band
    # above chart datum, so drying flats get a polygon too.
    levels = sorted(-d for d in DEPTH_LEVELS) + [DRYING_TOP_Z]
    opts = ['FIXED_LEVELS=' + ','.join(str(v) for v in levels), 'ID_FIELD=0']
    if polygonize:
        for name in ('ZMIN', 'ZMAX'):
            layer.CreateField(ogr.FieldDefn(name, ogr.OFTReal))
        opts += ['ELEV_FIELD_MIN=1', 'ELEV_FIELD_MAX=2', 'POLYGONIZE=YES']
    else:
        layer.CreateField(ogr.FieldDefn('Z', ogr.OFTReal))
        opts += ['ELEV_FIELD=1']
    nodata = band.GetNoDataValue()
    if nodata is not None:
        opts.append(f'NODATA={nodata}')
    gdal.ContourGenerateEx(band, layer, options=opts)
    return mem, layer


def window_edges(start, count):
    """Window edges (offsets into a box row or column) on global multiples
    of CONTOUR_WINDOW_CELLS, plus the box's first and last cell."""
    inner = range(-(-start // CONTOUR_WINDOW_CELLS) * CONTOUR_WINDOW_CELLS, start + count - 1, CONTOUR_WINDOW_CELLS)
    return sorted({0, count - 1, *(g - start for g in inner if 0 < g - start < count - 1)})


def grid_tiles(ds, box):
    """Split a grid into windows that overlap by one cell.

    GDAL's contour polygon mode slows down sharply with the number of rings:
    a whole ~13 m grid of 8400 x 6400 cells took 150 s, the same grid in
    16x16 windows 7 s. Neighbouring windows share a row/column of cell
    centres, so their contours meet exactly; DEPARE has no outline, so the
    seams do not show. The windows sit on the global grid, so neighbouring
    units cut their depth areas in the same places.
    Yields (srcWin, box); box spans the window's outer cell centres, so the
    boxes of neighbouring windows touch but do not overlap.
    """
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    xs = window_edges(box.ix0, ds.RasterXSize)
    ys = window_edges(box.iy0, ds.RasterYSize)
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            win = [xs[i], ys[j], xs[i + 1] - xs[i] + 1, ys[j + 1] - ys[j] + 1]
            poly = bbox_polygon(
                x0 + (xs[i] + 0.5) * dx,
                y0 + (ys[j + 1] + 0.5) * dy,
                x0 + (xs[i + 1] + 0.5) * dx,
                y0 + (ys[j] + 0.5) * dy,
            )
            yield win, poly


def is_valid(geom):
    gdal.PushErrorHandler('CPLQuietErrorHandler')  # GEOS reports every self-intersection
    try:
        return geom.IsValid()
    finally:
        gdal.PopErrorHandler()


def simplified(geom):
    """Simplify polygon by polygon: on a whole multipolygon band GEOS's
    topology-preserving simplifier took ~15x longer."""
    out = ogr.Geometry(ogr.wkbMultiPolygon)
    for part in polygon_parts(geom):
        for p in polygon_parts(part.SimplifyPreserveTopology(SIMPLIFY_DEG).MakeValid()):
            out.AddGeometry(p)
    return out if is_valid(out) else out.MakeValid()


def min_ring_length(valdco):
    for depth, length in DEPCNT_MIN_RING:
        if valdco <= depth:
            return length
    return DEPCNT_MIN_RING[-1][1]


# Satellite-derived bathymetry reads deep: against Shom soundings in the
# Tuamotus the true depth's 5th percentile is ~1.5 m at SDB < 4 m, ~3 m at
# 4-8 m and ~5 m beyond. Each band starts at that depth. Deeper than that the
# satellite cannot tell 8 m from 20 m.
SATELLITE_BANDS = ((4, 0.0, 2.0), (8, 2.0, 5.0), (None, 5.0, None))  # SDB m, DRVAL1, DRVAL2
SATELLITE_MIN_CELLS = 6
# In the deepest band the satellite's noise draws thousands of specks that
# tell a skipper nothing: there, only patches of about 100 m across stay.
SATELLITE_DEEP_BAND = 3
SATELLITE_DEEP_MIN_CELLS = 50  # ~1200 m2; a lone pixel is noise, a 40 m bommie is not


def satellite_bands(sdb_paths, bbox):
    """Chart-grid cells numbered by their SATELLITE_BANDS index + 1 (0: no
    satellite depth), or None. The squares (odc.aca_depth) are on the chart
    grid already, in metres."""
    minlon, minlat, maxlon, maxlat = bbox
    squares = []
    for path in sdb_paths or []:
        ds = gdal.Open(path)
        x0, dx, _, y0, _, dy = ds.GetGeoTransform()
        if x0 < maxlon and x0 + dx * ds.RasterXSize > minlon and y0 > minlat and y0 + dy * ds.RasterYSize < maxlat:
            squares.append(square_bands(ds))
    if not squares:
        return None
    ncols, nrows, _ = chart_grid(bbox)
    ds = gdal.Warp(
        '',
        squares,
        format='MEM',
        outputBounds=list(bbox),
        width=ncols,
        height=nrows,
        resampleAlg='nearest',
        srcNodata=0,
        dstNodata=0,
        outputType=gdal.GDT_Byte,
    )
    bands = ds.GetRasterBand(1).ReadAsArray()
    return bands if bands.any() else None


def square_bands(ds):
    """A whole square's cells in bands, specks removed. Sieving the whole
    square, never a unit's box, gives every unit the same band in a cell."""
    gt = ds.GetGeoTransform()
    sdb = ds.GetRasterBand(1).ReadAsArray()
    bands = np.zeros(sdb.shape, np.uint8)
    lower = 0
    for i, (upper, _, _) in enumerate(SATELLITE_BANDS):
        cells = sdb > lower if upper is None else (sdb > lower) & (sdb <= upper)
        bands[cells] = i + 1
        lower = upper
    mds = mem_raster(bands, gt, gdal.GDT_Byte, None)
    band = mds.GetRasterBand(1)
    gdal.SieveFilter(band, None, band, SATELLITE_MIN_CELLS, 8)
    bands = band.ReadAsArray()
    bands[sdb == 0] = 0  # a speck of no data stays without a depth
    # Deep patches too small to be the open water they claim: the shallower
    # band around them, or no depth where none is around.
    deep, shallower, nothing = 1, 2, 3
    kind = np.where(bands >= SATELLITE_DEEP_BAND, deep, np.where(bands > 0, shallower, nothing)).astype(np.uint8)
    kds = mem_raster(kind, gt, gdal.GDT_Byte, None)
    gdal.SieveFilter(kds.GetRasterBand(1), None, kds.GetRasterBand(1), SATELLITE_DEEP_MIN_CELLS, 8)
    sieved = kds.GetRasterBand(1).ReadAsArray()
    bands[(kind == deep) & (sieved == shallower)] = SATELLITE_DEEP_BAND - 1
    bands[(kind == deep) & (sieved == nothing)] = 0
    return mem_raster(bands, gt, gdal.GDT_Byte, 0)


def gap_polygons(cells, gt, water):
    if not cells.any():
        return ogr.Geometry(ogr.wkbMultiPolygon)
    mds = mem_raster(cells.astype(np.uint8), gt, gdal.GDT_Byte, None)
    band = mds.GetRasterBand(1)
    mem = ogr.GetDriverByName('MEM').CreateDataSource('u')
    layer = mem.CreateLayer('u', None, ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn('v', ogr.OFTInteger))
    gdal.Polygonize(band, band, layer, 0)
    # Band edges run through cell centres, these polygons along cell
    # edges: grow them by half a cell so no unpainted sliver remains.
    union = ogr.Geometry(ogr.wkbMultiPolygon)
    for feat in layer:
        for part in polygon_parts(feat.GetGeometryRef().Buffer(GRID_STEP / 2, 2)):
            union.AddGeometry(part)
    return union.UnaryUnion().Intersection(water)


def build_depths(depth_grid, box, water, land_cells, satellite=None, inland=None):
    """DEPARE bands, DEPCNT lines, and UNSARE for water without any depth.

    Where satellite bathymetry covers such water, it becomes DEPARE in the
    coarse SATELLITE_BANDS, with no contours or soundings."""
    depare, depcnt = [], []
    ds = gdal.Open(depth_grid)
    tiles = []
    for win, window in grid_tiles(ds, box):
        water_tile = water.Intersection(window)
        if water_tile is None or water_tile.IsEmpty():
            continue
        tiles.append((win, window, water_tile, water_tile.GetArea() >= window.GetArea() * (1 - 1e-9)))

    for win, window, water_tile, all_water in tiles:
        tile = gdal.Translate('', ds, format='MEM', srcWin=win)
        mem, layer = contour_layer(tile, polygonize=True)
        for feat in layer:
            zmin, zmax = feat.GetField('ZMIN'), feat.GetField('ZMAX')
            geom = simplified(feat.GetGeometryRef())
            # Contour polygons reach the window's outer cell edges, half a
            # cell past its box: clip to the box so neighbours do not overlap.
            geom = geom.Intersection(window if all_water else water_tile)
            if zmin >= 0:  # above chart datum but outside OSM land: drying
                drval1, drval2 = DRYING_DRVAL1, 0.0
            else:
                drval1 = max(0.0, -zmax)
                drval2 = -zmin
            for part in polygon_parts(geom):
                if part.GetArea() >= MIN_AREA_DEG2:
                    depare.append((part, {'DRVAL1': round(drval1, 1), 'DRVAL2': round(drval2, 1)}))
        del mem, layer, tile

    # Contour lines come from the whole grid (fast in line mode), so a small
    # closed ring can be told from a long contour cut by a tile edge. Over
    # flat multibeam ground the noise draws hundreds of tiny rings around
    # the 20/30 m levels; paper charts leave those out. Shallow levels keep
    # all but the tiniest rings: a ring there can be a rock.
    step('  contour lines')
    mem, layer = contour_layer(ds, polygonize=False)
    lines = ogr.GetDriverByName('MEM').CreateDataSource('l')
    kept = lines.CreateLayer('l', None, ogr.wkbLineString)
    kept.CreateField(ogr.FieldDefn('Z', ogr.OFTReal))
    dropped = 0
    for feat in layer:
        z = feat.GetField('Z')
        geom = feat.GetGeometryRef()
        n = geom.GetPointCount()
        closed = n > 2 and geom.GetPoint_2D(0) == geom.GetPoint_2D(n - 1)
        if closed and geom.Length() < min_ring_length(-z):
            dropped += 1
            continue
        out = ogr.Feature(kept.GetLayerDefn())
        out.SetField('Z', z)
        out.SetGeometry(geom.SimplifyPreserveTopology(SIMPLIFY_DEG))
        kept.CreateFeature(out)
    del mem, layer
    print(f'  {dropped} small closed contours dropped, {kept.GetFeatureCount()} kept')
    # Clip to water tile by tile (one big Intersection per line with the
    # whole water polygon is slow). Tile boxes touch but do not overlap.
    for _win, window, water_tile, all_water in tiles:
        bx0, bx1, by0, by1 = window.GetEnvelope()
        kept.SetSpatialFilterRect(bx0, by0, bx1, by1)
        for feat in kept:
            geom = feat.GetGeometryRef()
            ex0, ex1, ey0, ey1 = geom.GetEnvelope()
            if all_water and bx0 < ex0 and ex1 < bx1 and by0 < ey0 and ey1 < by1:
                pieces = [geom.Clone()]
            else:
                pieces = line_parts(geom.Intersection(water_tile))
            for part in pieces:
                if part.Length() > 0:
                    depcnt.append((part, {'VALDCO': round(-feat.GetField('Z'), 1)}))
    del kept, lines

    # Water with no depth at all (where neither a survey nor the DEM reaches
    # the OSM coastline): mark it unsurveyed rather than invent a depth.
    step('  UNSARE')
    arr = ds.GetRasterBand(1).ReadAsArray()
    gaps = (arr == NODATA) & ~land_cells
    if inland is not None:
        gaps |= (arr == NODATA) & inland  # a canal without a cell centre; water clips it to the canal
    del arr
    gt = ds.GetGeoTransform()
    covered = ogr.Geometry(ogr.wkbMultiPolygon)
    if satellite is not None:
        for i, (_, drval1, drval2) in enumerate(SATELLITE_BANDS):
            area = gap_polygons(gaps & (satellite == i + 1), gt, water).Difference(covered)
            attrs = {'DRVAL1': drval1, 'INFORM': SDB_SRC}
            if drval2 is not None:
                attrs['DRVAL2'] = drval2
            # Only what is drawn: a dropped sliver stays unsurveyed.
            drawn = ogr.Geometry(ogr.wkbMultiPolygon)
            for part in polygon_parts(area):
                if part.GetArea() >= MIN_AREA_DEG2:
                    depare.append((part, attrs))
                    drawn.AddGeometry(part)
            covered = covered.Union(drawn)
    unsare = []
    if gaps.any():
        area = gap_polygons(gaps, gt, water)
        if not covered.IsEmpty():
            area = area.Difference(covered)
        unsare = [(p, {}) for p in polygon_parts(area) if p.GetArea() >= MIN_UNSARE_DEG2]
    return depare, depcnt, unsare


# --------------------------------------------------------------------- WFS


def wreck_props(p):
    out = {}
    if p.get('brassiage') is not None:
        out['VALSOU'] = p['brassiage']
    quasou = WRECK_QUASOU.get(p.get('precis_bra') or '')
    if quasou:
        out['QUASOU'] = quasou
    if p.get('precis_loc') is not None:
        out['POSACC'] = p['precis_loc']
    if p.get('nom'):
        out['OBJNAM'] = p['nom']
    info = [p.get(k) for k in ('type_obj', 'caract_bat', 'caract_obj', 'circ_nauf')]
    info = '; '.join(str(v).strip() for v in info if v and str(v).strip() not in ('Inconnu', 'Inconnues'))
    if info:
        out['INFORM'] = info
    return out


def s57_props(p, attributes):
    out = {}
    for key, value in p.items():
        acronym = key.upper()
        if value in (None, '') or acronym not in attributes:
            continue
        out[acronym] = value
    return out


def wfs_layer_class(layer):
    """The S-57 class of a WFS layer's objects, None for place names (LNDRGN or SEAARE)."""
    if layer.startswith('toponymes'):
        return None
    if layer in ('wrecks', 'obstrn'):
        return layer.upper()
    return layer.split('_')[0].upper()


def wfs_layer(path):
    return os.path.basename(path)[:-5].split('__', 1)[1]


def wfs_features(wfs_dir, attributes, classes, land, bbox_geom, only=None):
    """S-57 features from the WFS downloads, clipped to the chart bbox.

    The WFS returns whole features that touch the bbox: submarine cables to
    Australia, EEZ-sized restricted areas. Unclipped they stretch the tile
    pyramid over half the Pacific.
    """
    by_class = {}
    for path in sorted(glob.glob(os.path.join(wfs_dir, '*__*.json'))):
        layer = wfs_layer(path)
        if only is not None and wfs_layer_class(layer) not in only:
            continue
        data = json.load(open(path, encoding='utf-8'))
        for feat in data.get('features', []):
            geom_json = feat.get('geometry')
            if not geom_json:
                continue
            geom = ogr.CreateGeometryFromJson(json.dumps(geom_json))
            if not bbox_geom.Contains(geom):
                geom = geom.MakeValid().Intersection(bbox_geom)
                if geom is None or geom.IsEmpty():
                    continue
            props = feat.get('properties') or {}
            if layer.startswith('toponymes'):
                if not props.get('nom'):
                    continue
                on_land = land.Contains(geom) if land and not land.IsEmpty() else False
                objl = 'LNDRGN' if on_land else 'SEAARE'
                attrs = {'OBJNAM': props['nom']}
            elif layer in ('wrecks', 'obstrn'):
                objl = wfs_layer_class(layer)
                attrs = wreck_props(props)
            else:
                objl = wfs_layer_class(layer)
                attrs = s57_props(props, attributes)
            if objl not in classes:
                print(f'skipping unknown class {objl} ({layer})')
                continue
            lnam = props.get('inspireid') or feat.get('id')
            by_class.setdefault(objl, []).append((geom, attrs, lnam))
    return by_class


def shom_aids_near(wfs_dir, attributes, classes, bbox):
    """The Shom aids an OSM aid in bbox may give way to, a mile past its edge included."""
    window = bbox_polygon(*seamarks.aid_window(bbox))
    return seamarks.shom_aids(wfs_features(wfs_dir, attributes, classes, None, window, seamarks.AID_CLASSES))


# ------------------------------------------------------------------ output


# ---------------------------------------------------- harbour structures, reefs


def point(x, y):
    p = ogr.Geometry(ogr.wkbPoint)
    p.AddPoint_2D(x, y)
    return p


def multipolygon(polys):
    out = ogr.Geometry(ogr.wkbMultiPolygon)
    for poly in polys:
        for part in polygon_parts(poly):
            out.AddGeometry(part)
    return out


def clip_to(geom, bbox_geom):
    if geom is None or geom.IsEmpty():
        return None
    if bbox_geom.Contains(geom):
        return geom
    geom = geom.MakeValid().Intersection(bbox_geom)
    return None if geom.IsEmpty() else geom


def osm_marine(osm_items, bbox_geom):
    """Shoreline constructions, pontoons, slipways and reefs from OSM.

    Returns (solid, layers, reefs): solid structure polygons (moles,
    breakwaters, quays, fixed piers) to merge into the land, S-57 items per
    object class, and the reef geometries (sorted out against the reef map
    later). Floating pontoons stay water (PONTON).
    """
    solid, layers, reefs = [], {}, []
    for t, geom in osm_items:
        man_made, natural = t.get('man_made'), t.get('natural')
        if natural != 'reef' and t.get('leisure') != 'slipway' and man_made not in OSM_CATSLC:
            continue
        geom = clip_to(geom, bbox_geom)
        if geom is None:
            continue
        attrs = {'INFORM': OSM_SRC}
        if t.get('name'):
            attrs['OBJNAM'] = t['name']
        dim = geom.GetDimension()
        if natural == 'reef':
            reefs.append((geom, attrs))
        elif t.get('leisure') == 'slipway':
            if dim > 0:  # S-52 has no point symbol for a slipway in Freeboard
                layers.setdefault('SLCONS', []).append(
                    (geom, {**attrs, 'CATSLC': CATSLC_SLIPWAY, '_minzoom': STRUCTURE_MINZOOM}, None)
                )
        elif dim > 0:
            minzoom = PIER_MINZOOM if man_made == 'pier' and dim == 1 else STRUCTURE_MINZOOM
            if t.get('floating') == 'yes':
                layers.setdefault('PONTON', []).append((geom, {**attrs, '_minzoom': minzoom}, None))
                continue
            if dim == 2:
                solid.append(geom)
            layers.setdefault('SLCONS', []).append(
                (geom, {**attrs, 'CATSLC': OSM_CATSLC[man_made], '_minzoom': minzoom}, None)
            )
    return solid, layers, reefs


def oblic_structures(path, bbox_geom):
    """Wharves, rip-rap, slipways and sea walls traced on the 2016 orthophoto
    of Nouméa (OBLIC), drawn as shoreline-construction outlines."""
    items = []
    for f in json.load(open(path, encoding='utf-8')).get('features', []):
        p = f.get('properties') or {}
        cat = OBLIC_CATSLC.get(p.get('type') or '')
        if not cat or not f.get('geometry'):
            continue
        geom = clip_to(ogr.CreateGeometryFromJson(json.dumps(f['geometry'])).MakeValid(), bbox_geom)
        if geom is None:
            continue
        items.append(
            (
                geom,
                {'CATSLC': cat, 'SORDAT': '2016', 'INFORM': f'{p["type"]}: {OBLIC_SRC}', '_minzoom': STRUCTURE_MINZOOM},
                None,
            )
        )
    return items


def tiled_parts(geom, bbox, n=LAND_PIECES):
    """Cut a big (multi)polygon such as the land into n x n tiles, so that
    later per-feature overlays only meet small pieces: (piece, envelope)."""
    minx, miny, maxx, maxy = bbox
    dx, dy = (maxx - minx) / n, (maxy - miny) / n
    out = []
    for i in range(n):
        for j in range(n):
            box = bbox_polygon(minx + i * dx, miny + j * dy, minx + (i + 1) * dx, miny + (j + 1) * dy)
            for part in polygon_parts(geom.Intersection(box)):
                out.append((part, part.GetEnvelope()))
    return out


def overlapping(index, geom):
    env = geom.GetEnvelope()
    for part, e in index:
        if e[0] <= env[1] and e[1] >= env[0] and e[2] <= env[3] and e[3] >= env[2] and part.Intersects(geom):
            yield part


def minus(geom, index):
    for part in overlapping(index, geom):
        geom = geom.Difference(part)
        if geom.IsEmpty():
            return None
    return geom


def aca_classes(aca_dir, bbox_geom):
    """The Allen Coral Atlas polygons inside the box, unioned per class."""
    minx, maxx, miny, maxy = bbox_geom.GetEnvelope()
    seen, parts = set(), {}
    for s in range(math.floor(miny), math.ceil(maxy)):
        for w in range(math.floor(minx), math.ceil(maxx)):
            path = os.path.join(aca_dir, f'reefs_{s:+03d}_{w:+04d}.geojsonseq')
            if not os.path.exists(path):
                continue
            with open(path, encoding='utf-8') as fh:
                for line in fh:
                    if line in seen:
                        continue
                    seen.add(line)
                    f = json.loads(line)
                    if f['properties']['class_name'] not in ACA_WATLEV:
                        continue
                    geom = clip_to(ogr.CreateGeometryFromJson(json.dumps(f['geometry'])), bbox_geom)
                    if geom is not None:
                        parts.setdefault(f['properties']['class_name'], []).append(geom)
    return {name: multipolygon(geoms).UnionCascaded().MakeValid() for name, geoms in sorted(parts.items())}


def reef_item(part, watlev, inform):
    return (part, {'WATLEV': watlev, 'NATSUR': NATSUR_CORAL, 'INFORM': inform}, None)


REEF_MATCH_DEG = 0.0005  # ~50 m: an OSM outline this close to a mapped reef is that reef


def build_reefs(reef_path, aca_dir, osm_reefs, land_index, bbox_geom):
    """OBSTRN reef areas: the reef map's reef flats, the Allen Coral Atlas
    where the reef map has no reef, and OSM reefs where neither has one.
    Clipped off the land."""
    items, mapped = [], []
    if reef_path:
        for f in json.load(open(reef_path, encoding='utf-8')).get('features', []):
            p = f.get('properties') or {}
            if p.get('reef') != 1 or not f.get('geometry'):
                continue
            geom = clip_to(ogr.CreateGeometryFromJson(json.dumps(f['geometry'])).MakeValid(), bbox_geom)
            if geom is None:
                continue
            mapped.append((geom, geom.GetEnvelope()))
            watlev = REEF_WATLEV.get(p.get('l4_attrib'))
            if not watlev:
                continue  # forereef slopes, terraces: not a hazard outline
            geom = geom.SimplifyPreserveTopology(REEF_SIMPLIFY_DEG).MakeValid()
            geom = minus(geom, land_index)
            if geom is None:
                continue
            for part in polygon_parts(geom):
                if part.GetArea() >= MIN_AREA_DEG2:
                    items.append(reef_item(part, watlev, f'{p["l4_attrib"]}, {p.get("rb_attrib")}: {REEF_SRC}'))
    n_map = len(items)
    reef_map = multipolygon([geom for geom, _ in mapped]).UnionCascaded() if mapped else None
    for name, geom in aca_classes(aca_dir, bbox_geom).items() if aca_dir else ():
        if reef_map is not None:
            geom = geom.Difference(reef_map)
        mapped += [(part, part.GetEnvelope()) for part in polygon_parts(geom)]
        geom = minus(geom.SimplifyPreserveTopology(REEF_SIMPLIFY_DEG).MakeValid(), land_index)
        items += [
            reef_item(part, ACA_WATLEV[name], f'{name}: {ACA_SRC}')
            for part in polygon_parts(geom)
            if part.GetArea() >= MIN_AREA_DEG2
        ]
    n_aca, n_osm = len(items) - n_map, 0
    for geom, attrs in osm_reefs:
        charted, dim = list(overlapping(mapped, geom.Buffer(REEF_MATCH_DEG))), geom.GetDimension()
        if dim == 0:
            if charted:
                continue  # the maps have this reef
        else:
            for part in charted:  # keep only what the maps leave out
                geom = geom.Difference(part.Buffer(REEF_MATCH_DEG))
            if dim == 2:
                geom = minus(geom, land_index) if not geom.IsEmpty() else None
                parts = [p for p in polygon_parts(geom) if p.GetArea() >= MIN_AREA_DEG2]
                if not parts:
                    continue
                geom = multipolygon(parts)
            else:
                parts = list(line_parts(geom))
                if not parts:
                    continue
                geom = ogr.Geometry(ogr.wkbMultiLineString)
                for part in parts:
                    geom.AddGeometry(part)
        items.append((geom, {**attrs, 'NATSUR': NATSUR_CORAL}, None))
        n_osm += 1
    print(f'  reefs: {n_map} reef-map areas, {n_aca} Allen Coral Atlas areas, {n_osm} OSM reefs the maps lack')
    return items


def dilate(mask, cells):
    out = mask.copy()
    for _ in range(cells):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def dilate_disk(mask, radius):
    """Cells within Euclidean distance radius of a True cell, the reach of
    gdal.FillNodata's maxSearchDist. Each row band is widened once per half
    width, so it costs O(radius) array passes, not O(radius**2)."""
    height = mask.shape[0]
    out = mask.copy()
    wide = mask.copy()
    for half in range(radius + 1):
        if half:
            grown = wide.copy()
            grown[:, 1:] |= wide[:, :-1]
            grown[:, :-1] |= wide[:, 1:]
            wide = grown
        for dy in range(-radius, radius + 1):
            if math.isqrt(radius * radius - dy * dy) != half:
                continue
            if dy >= 0:
                out[dy:] |= wide[: height - dy]
            else:
                out[:dy] |= wide[-dy:]
    return out


def osm_buildings(osm_items, land_cells, bbox):
    """BUISGL areas for the buildings within ~150 m of the sea; the ones
    further inland do not help anybody on the water and only cost size."""
    minlon, _, _, maxlat = bbox
    near = dilate(~land_cells, BUILDING_NEAR_WATER_CELLS)
    nrows, ncols = near.shape
    items, total = [], 0
    for t, geom in osm_items:
        if 'building' not in t or geom.GetDimension() != 2:
            continue
        total += 1
        c = geom.Centroid()
        col = int((c.GetX() - minlon) / GRID_STEP)
        row = int((maxlat - c.GetY()) / GRID_STEP)
        if not (0 <= row < nrows and 0 <= col < ncols) or not near[row, col]:
            continue
        attrs = {'_minzoom': BUILDING_MINZOOM}
        if t.get('name'):
            attrs['OBJNAM'] = t['name']
        items.append((geom, attrs, None))
    print(f'  buildings: {len(items)} of {total} within ~{BUILDING_NEAR_WATER_CELLS * 13} m of the sea')
    return items


def parse_height(value):
    try:
        return float(str(value).replace(',', '.').split()[0].rstrip('m'))
    except (ValueError, IndexError):
        return None


def relief_bounds(bbox):
    """bbox grown to the global relief grid, so neighbours average the same cells."""
    minlon, minlat, maxlon, maxlat = (v / RELIEF_CELL_DEG for v in bbox)
    return (
        math.floor(round(minlon, 6)) * RELIEF_CELL_DEG,
        math.floor(round(minlat, 6)) * RELIEF_CELL_DEG,
        math.ceil(round(maxlon, 6)) * RELIEF_CELL_DEG,
        math.ceil(round(maxlat, 6)) * RELIEF_CELL_DEG,
    )


def build_relief(dem_paths, osm_items, bbox, work, bbox_geom):
    """LNDELV: land contours every RELIEF_INTERVAL_M from the Copernicus DEM,
    and spot heights from OSM peaks (name and height)."""
    items = []
    if dem_paths:
        vrt = gdal.BuildVRT(os.path.join(work, 'cop30.vrt'), dem_paths)
        dem = gdal.Warp(
            os.path.join(work, 'relief.tif'),
            vrt,
            outputBounds=relief_bounds(bbox),
            xRes=RELIEF_CELL_DEG,
            yRes=RELIEF_CELL_DEG,
            resampleAlg='average',
            creationOptions=['COMPRESS=DEFLATE'],
        )
        vrt = None
        top = float(np.nanmax(dem.GetRasterBand(1).ReadAsArray()))
        levels = [float(v) for v in range(RELIEF_INTERVAL_M, int(top) + 1, RELIEF_INTERVAL_M)]
        mem = ogr.GetDriverByName('MEM').CreateDataSource('relief')
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        layer = mem.CreateLayer('relief', srs, ogr.wkbLineString)
        layer.CreateField(ogr.FieldDefn('ELEV', ogr.OFTReal))
        if levels:
            gdal.ContourGenerateEx(
                dem.GetRasterBand(1),
                layer,
                options=['FIXED_LEVELS=' + ','.join(str(v) for v in levels), 'ELEV_FIELD=ELEV'],
            )
        for feat in layer:
            geom = feat.GetGeometryRef().SimplifyPreserveTopology(RELIEF_SIMPLIFY_DEG)
            if geom.Length() < 0.002:  # ~200 m: knolls the 60 m grid barely resolves
                continue
            items.append(
                (geom, {'ELEVAT': int(feat.GetField('ELEV')), 'INFORM': COP30_SRC, '_minzoom': RELIEF_MINZOOM}, None)
            )
        dem = None
    n_lines = len(items)
    for t, geom in osm_items:
        if t.get('natural') not in ('peak', 'volcano'):
            continue
        height = parse_height(t.get('ele'))
        if height is None or geom.GetDimension() != 0:
            continue
        if not bbox_geom.Contains(geom):
            continue
        attrs = {'ELEVAT': round(height), 'INFORM': OSM_SRC}
        if t.get('name'):
            attrs['OBJNAM'] = t['name']
        items.append((geom, attrs, None))
    print(f'  relief: {n_lines} contour lines, {len(items) - n_lines} spot heights')
    return items


def explode(geom):
    gtype = ogr.GT_Flatten(geom.GetGeometryType())
    if gtype in (ogr.wkbMultiPoint, ogr.wkbMultiLineString, ogr.wkbMultiPolygon, ogr.wkbGeometryCollection):
        for i in range(geom.GetGeometryCount()):
            yield from explode(geom.GetGeometryRef(i))
    elif not geom.IsEmpty():
        yield geom


PRIM = {ogr.wkbPoint: 1, ogr.wkbLineString: 2, ogr.wkbPolygon: 3}


def write_layers(out_dir, layers, classes, unit):
    os.makedirs(out_dir, exist_ok=True)
    for objl in classes:  # a layer of an earlier build would be tiled again
        stale = os.path.join(out_dir, f'{objl}.geojson')
        if os.path.exists(stale):
            os.remove(stale)
    rcid = 0
    summary = {}
    for objl, items in sorted(layers.items()):
        feats = []
        for geom, attrs, lnam in items:
            attrs = dict(attrs)
            minzoom = attrs.pop('_minzoom', None)
            for part in explode(geom):
                if ogr.GT_Flatten(part.GetGeometryType()) == ogr.wkbPoint and not unit.owns(part.GetX(), part.GetY()):
                    continue  # the neighbour unit has it
                rcid += 1
                props = {
                    'OBJL': classes[objl],
                    'RCID': rcid,
                    'PRIM': PRIM.get(ogr.GT_Flatten(part.GetGeometryType()), 255),
                    'LNAM': str(lnam or f'SHOM{rcid:08d}'),
                }
                props.update(attrs)
                feat = {
                    'type': 'Feature',
                    'properties': props,
                    'geometry': json.loads(part.ExportToJson(['COORDINATE_PRECISION=7'])),
                }
                if minzoom is not None:
                    feat['tippecanoe'] = {'minzoom': minzoom}  # stands in for SCAMIN
                feats.append(feat)
        with open(os.path.join(out_dir, f'{objl}.geojson'), 'w', encoding='utf-8') as fh:
            json.dump({'type': 'FeatureCollection', 'features': feats}, fh, ensure_ascii=False)
        summary[objl] = len(feats)
    return summary


def quality_category(meta):
    tech = (meta.get('tecsou') or '').lower()
    end = int(str(meta['end'])[:8])
    if end >= 20050101 and ('multi-beam' in tech or 'lidar' in tech):
        return 'multibeam'
    return 'survey-recent' if end >= 19900101 else 'survey-old'


def depth_quality(entries, rank, from_survey, from_model, land_cells, box, unit, from_deep=None, from_satellite=None):
    """Share of the unit's water by where its depths come from (see
    docs/index-format.md), and the ranks of the surveys that reach the unit.

    A survey covers the cells it sounded and the cells the depth grid
    interpolated from it (INTERP_CELLS around them, where the model did not
    reject the interpolation). A measured cell counts for the survey that
    sounded it, an interpolated one for the best category within reach. A
    survey reaches the unit when it sounded a cell within INTERP_CELLS of it.
    Water no survey covers is the model's where the bathymetric model has a
    depth, then satellite-derived bathymetry's, and 'none' (charted
    unsurveyed) elsewhere."""
    cells = unit.cells
    rows = slice(cells.iy0 - box.iy0, cells.iy1 - box.iy0)
    cols = slice(cells.ix0 - box.ix0, cells.ix1 - box.ix0)
    water = ~land_cells[rows, cols]
    total = int(water.sum())
    if not total:
        return None, []
    taken = np.zeros_like(water)
    share = {}
    if rank is not None and entries:
        categories = np.array([quality_category(e[4]) for e in entries])
        # A measured cell belongs to the survey that won it; an interpolated one
        # to the best survey within INTERP_CELLS.
        measured = rank[rows, cols] >= 0
        for category in SURVEY_CATEGORIES:
            mask = np.isin(rank, np.flatnonzero(categories == category))
            reached = np.where(measured, mask[rows, cols], dilate_disk(mask, INTERP_CELLS)[rows, cols])
            covered = reached & from_survey[rows, cols] & water & ~taken
            taken |= covered
            share[category] = int(covered.sum())
    for category, cells in (('model', from_model), ('model-coarse', from_deep), ('satellite', from_satellite)):
        if cells is not None:
            covered = cells[rows, cols] & water & ~taken
            taken |= covered
            share[category] = int(covered.sum())
    share['none'] = total - int(taken.sum())
    share = {k: published for k, v in share.items() if (published := round(v / total, 3))}
    best = next(c for c in QUALITY_ORDER if c in share)
    if rank is None or not entries:
        return {'best': best, 'share': share}, []
    near = rank[
        max(0, rows.start - INTERP_CELLS) : rows.stop + INTERP_CELLS,
        max(0, cols.start - INTERP_CELLS) : cols.stop + INTERP_CELLS,
    ]
    used = [int(r) for r in np.unique(near[near >= 0])]
    return {'best': best, 'share': share}, used


def write_unit_info(out_dir, unit, quality, surveys):
    info = {
        'unit': unit.id,
        'quality': quality,
        'surveys': [
            {'name': name, 'end': str(meta['end']), 'tecsou': meta.get('tecsou'), 'doi': f'10.17183/{name}'}
            for _, _, name, _, meta in surveys
        ],
    }
    with open(os.path.join(out_dir, 'unit.json'), 'w', encoding='utf-8') as fh:
        json.dump(info, fh, indent=1)


def survey_list(surveys_json):
    """Survey metadata from a mirror index (odc.mirror) or a plain list."""
    data = json.load(open(surveys_json, encoding='utf-8'))
    if isinstance(data, list):
        return [(meta, f'{meta["name"]}.npz') for meta in data]
    return [({**meta, 'name': name}, meta['reduced']['asset']) for name, meta in data['surveys'].items()]


def survey_inputs(leves_dir, surveys_json, box):
    """The surveys whose reduced file exists and whose footprint bbox meets the box."""
    minx, miny, maxx, maxy = box.bbox
    out = []
    for meta, file_name in survey_list(surveys_json):
        path = os.path.join(leves_dir, file_name)
        bbox = meta.get('bbox')
        if not os.path.exists(path):
            continue
        if bbox and (bbox[0] > maxx or bbox[2] < minx or bbox[1] > maxy or bbox[3] < miny):
            continue
        out.append((meta, path))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--unit', required=True, help='unit id, e.g. z9-492-288')
    ap.add_argument('--wfs', required=True)
    ap.add_argument('--land', required=True, help='OSM land polygons (osmdata.openstreetmap.de), any OGR format')
    ap.add_argument('--dem', help='bathymetric DEM; without it, depths come from the surveys alone')
    ap.add_argument('--leves', help='directory of <survey>.npz files from odc.reduce_survey')
    ap.add_argument('--surveys', help='mirror-index.json, or a JSON list of surveys (name, start, end, tecsou)')
    ap.add_argument('--maxzoom', type=int, default=MAX_ZOOM, help='max tile zoom (SOUNDG thinning)')
    ap.add_argument('--osm', help='GeoJSONSeq from osmium export of the filtered extract (odc.osm)')
    ap.add_argument('--seamarks', help='GeoJSONSeq of OSM seamark:* objects (odc.sources), for waters without Shom WFS')
    ap.add_argument('--water', help='GeoJSONSeq of OSM water areas (odc.sources), cut out of the land')
    ap.add_argument('--relief-dem', nargs='*', help='Copernicus DEM GLO-30 tiles')
    ap.add_argument('--deep-dem', nargs='*', help='ETOPO 2022 15 arc-second tiles, for deep water only')
    ap.add_argument('--nc-reefs', help='reef geomorphology GeoJSON (odc.sources)')
    ap.add_argument('--aca-reefs', help='directory of Allen Coral Atlas reef GeoJSONSeq squares (odc.sources)')
    ap.add_argument('--satellite-depth', nargs='*', help='satellite-derived bathymetry squares (odc.aca_depth)')
    ap.add_argument('--nc-structures', help='OBLIC coastal structures GeoJSON (odc.sources)')
    ap.add_argument('--work', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)

    classes, attributes = load_s57_tables()
    unit = Unit.parse(args.unit)
    box = unit.build_box
    args.bbox = box.bbox
    bbox_geom = bbox_polygon(*args.bbox)
    step(f'Unit {unit.id}, build box {args.bbox}')

    step('DEM: clip + fill near-shore gaps')
    _, filled = prepare_dem(args.dem, args.bbox, args.work)

    step('Land from OSM land polygons and water areas')
    water_areas = osm.read_water(args.water, args.bbox) if args.water else None
    land, coast, inland = build_land(args.land, bbox_geom, water_areas)
    del water_areas
    osm_items = list(osm.read_features(args.osm, args.bbox)) if args.osm else []
    marine_layers, osm_reefs = {}, []
    if osm_items:
        step('OSM harbour structures and reefs')
        solid, marine_layers, osm_reefs = osm_marine(osm_items, bbox_geom)
        if solid:  # moles, breakwaters, fixed piers are land on a chart
            land = land.Union(multipolygon(solid).UnaryUnion()).MakeValid()
    water = bbox_geom.Difference(land) if not land.IsEmpty() else bbox_geom

    entries, survey_depth, rank = [], None, None
    if args.leves:
        step('Surveys: composite, newest on top')
        entries, survey_depth, rank = load_surveys(survey_inputs(args.leves, args.surveys, box), box)
    deep = None
    if args.deep_dem:
        step('Deep water from ETOPO')
        deep = deep_water(args.deep_dem, args.bbox)
    step('Depth grid: surveys + DEM')
    inland_cells = inland_mask(land, inland, args.bbox)
    depth_grid, from_survey, from_deep = build_depth_grid(
        filled, args.bbox, args.work, survey_depth, deep, inland_cells
    )
    del deep
    del survey_depth
    land_cells = land_mask(land, args.bbox)
    soundings = []
    from_model = gdal.Open(os.path.join(args.work, 'dem_grid.tif')).ReadAsArray() != NODATA
    satellite = satellite_bands(args.satellite_depth, args.bbox)
    in_sdb = None if satellite is None else satellite > 0
    quality, used = depth_quality(entries, rank, from_survey, from_model, land_cells, box, unit, from_deep, in_sdb)
    del in_sdb
    del from_model, from_deep
    del from_survey
    if entries:
        step('Soundings')
        soundings = build_soundings(entries, rank, land_cells, box, unit, args.maxzoom)
    del rank

    step('Depth areas + contours')
    depare, depcnt, unsare = build_depths(depth_grid, box, water, land_cells, satellite, inland_cells)
    del inland_cells
    del satellite

    step('Shom WFS objects')
    layers = wfs_features(args.wfs, attributes, classes, land, bbox_geom)
    layers['LNDARE'] = [(p, {}, None) for p in polygon_parts(land)]
    layers['COALNE'] = [(line, {}, None) for line in coast]
    layers['DEPARE'] = [(g, a, None) for g, a in depare]
    layers['DEPCNT'] = [(g, a, None) for g, a in depcnt]
    if unsare:
        layers['UNSARE'] = [(g, a, None) for g, a in unsare]
    if soundings:
        layers['SOUNDG'] = soundings
    for objl, items in marine_layers.items():
        layers.setdefault(objl, []).extend(items)
    if args.seamarks:
        step('OSM seamarks')
        charted = shom_aids_near(args.wfs, attributes, classes, args.bbox)
        marks = seamarks.seamark_layers(osm.read_features(args.seamarks, args.bbox), bbox_geom, OSM_SRC, charted)
        for objl, items in marks.items():
            layers.setdefault(objl, []).extend(items)
    if args.nc_structures:
        step('OBLIC coastal structures')
        layers.setdefault('SLCONS', []).extend(oblic_structures(args.nc_structures, bbox_geom))
    if args.nc_reefs or args.aca_reefs or osm_reefs:
        step('Reefs')
        land_index = tiled_parts(land, args.bbox)
        reefs = build_reefs(args.nc_reefs, args.aca_reefs, osm_reefs, land_index, bbox_geom)
        layers.setdefault('OBSTRN', []).extend(reefs)
    if osm_items:
        step('Buildings near the sea')
        layers.setdefault('BUISGL', []).extend(osm_buildings(osm_items, land_cells, args.bbox))
    if args.relief_dem or osm_items:
        step('Land relief')
        layers.setdefault('LNDELV', []).extend(
            build_relief(args.relief_dem, osm_items, args.bbox, args.work, bbox_geom)
        )

    step('Write GeoJSON')
    summary = write_layers(args.out, layers, classes, unit)
    write_unit_info(args.out, unit, quality, [entries[r] for r in used])
    step('done')
    for objl, n in summary.items():
        print(f'{n:7d}  {objl}')


if __name__ == '__main__':
    main()
