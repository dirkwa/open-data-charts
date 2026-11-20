"""Chart objects published as vector layers, one layer per S-57 class,
geometry and scale band: LINZ's New Zealand charts (odc.sources.fetch_chart).

A band's objects show from its zoom (BAND_MINZOOM) up. Where a larger-scale
band has coverage (its M_COVR areas), they give way to it at that band's
zoom, so each place shows the most detailed chart for the zoom, as a
plotter switches charts.
"""

import functools
import glob
import json
import os

from osgeo import ogr

LAYERS_FILE = os.path.join(os.path.dirname(__file__), 'linz_hydro_layers.json')
# Bands from the smallest scale: 1:1.5M and smaller, 1:350k-1.5M,
# 1:90k-350k, 1:22k-90k, 1:4k-22k (LINZ's usage bands).
BAND_MINZOOM = (9, 9, 11, 13, 15)
COVERAGE = 'M_COVR'
CATCOV_AVAILABLE = 1
# The band from which a unit counts as charted: coastal charts and larger.
DISCOVERY_MIN_BAND = 2


@functools.cache
def linz_layers():
    """(layer id, S-57 class, band) of each LDS layer the charts use."""
    with open(LAYERS_FILE, encoding='utf-8') as fh:
        return tuple(tuple(entry) for entry in json.load(fh))


def file_name(band, objl, layer_id):
    return f'{band}__{objl}__{layer_id}.json'


def parse_file_name(path):
    band, objl, layer_id = os.path.basename(path)[:-5].split('__')
    return int(band), objl, layer_id


def chart_files(chart_dir):
    return sorted(glob.glob(os.path.join(chart_dir, '*__*__*.json')))


def read_features(path, bbox_geom):
    """(geometry clipped to bbox_geom, properties, id) of a layer file."""
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    for feat in data.get('features', []):
        if not feat.get('geometry'):
            continue
        geom = ogr.CreateGeometryFromJson(json.dumps(feat['geometry']))
        if not bbox_geom.Contains(geom):
            geom = geom.MakeValid().Intersection(bbox_geom)
            if geom is None or geom.IsEmpty():
                continue
        yield geom, feat.get('properties') or {}, feat.get('id')


def available(props):
    return str(props.get('catcov')) == str(CATCOV_AVAILABLE)  # WFS layers may carry it as text


def coverage(chart_dir, bbox_geom):
    """Per band, the area its charts cover within bbox_geom (None if none)."""
    areas = [None] * len(BAND_MINZOOM)
    for path in chart_files(chart_dir):
        band, objl, _ = parse_file_name(path)
        if objl != COVERAGE:
            continue
        for geom, props, _ in read_features(path, bbox_geom):
            if not available(props):
                continue
            areas[band] = geom if areas[band] is None else areas[band].Union(geom)
    return areas


def zoom_parts(geom, band, areas):
    """(part, maxzoom or None) of a band's object: the parts a larger-scale
    band covers end below that band's zoom, the rest shows to the last zoom."""
    remaining = geom
    for finer in range(band + 1, len(BAND_MINZOOM)):
        area = areas[finer]
        if area is None or not remaining.Intersects(area):
            continue
        maxzoom = BAND_MINZOOM[finer] - 1
        inside = remaining.Intersection(area)
        if maxzoom >= BAND_MINZOOM[band] and not inside.IsEmpty():
            yield inside, maxzoom
        remaining = remaining.Difference(area)
        if remaining.IsEmpty():
            return
    yield remaining, None


def chart_layers(chart_dir, to_attributes, classes, bbox_geom, areas):
    """S-57 objects of the chart layers, keyed by class, each with the zooms
    it shows at; areas is coverage() over bbox_geom."""
    layers = {}
    for path in chart_files(chart_dir):
        band, objl, _ = parse_file_name(path)
        if objl == COVERAGE or objl not in classes:
            continue
        for geom, props, lnam in read_features(path, bbox_geom):
            attrs = to_attributes(props)
            if objl == 'SOUNDG' and props.get('depth') is not None:
                attrs['DEPTH'] = props['depth']  # not an S-57 attribute: GDAL's name for the sounding's value
            for part, maxzoom in zoom_parts(geom, band, areas):
                zooms = {'_minzoom': BAND_MINZOOM[band]} if BAND_MINZOOM[band] > BAND_MINZOOM[0] else {}
                if maxzoom is not None:
                    zooms['_maxzoom'] = maxzoom
                layers.setdefault(objl, []).append((part, {**attrs, **zooms}, lnam))
    return layers


def charted(chart_dir, bounds):
    """Whether a coastal or larger chart covers part of bounds."""
    minlon, minlat, maxlon, maxlat = bounds
    for band, area in enumerate(coverage_index(chart_dir)):
        if band < DISCOVERY_MIN_BAND:
            continue
        for x0, y0, x1, y1 in area:
            if x0 < maxlon and x1 > minlon and y0 < maxlat and y1 > minlat:
                return True
    return False


@functools.cache
def coverage_index(chart_dir):
    """Per band, the bounding boxes of its coverage areas."""
    boxes = [[] for _ in BAND_MINZOOM]
    for path in chart_files(chart_dir):
        band, objl, _ = parse_file_name(path)
        if objl != COVERAGE:
            continue
        with open(path, encoding='utf-8') as fh:
            for feat in json.load(fh).get('features', []):
                if available(feat.get('properties') or {}) and feat.get('geometry'):
                    env = ogr.CreateGeometryFromJson(json.dumps(feat['geometry'])).GetEnvelope()
                    boxes[band].append((env[0], env[2], env[1], env[3]))
    return boxes
