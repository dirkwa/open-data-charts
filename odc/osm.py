"""OpenStreetMap inputs: a regional extract filtered to the features a chart
uses, and the coastline as land polygons.

`osmium export` writes a closed way twice, as a line ('w' id) and as an
area ('a' id, twice the way id). The chart wants one of the two, chosen as
the prototype's Overpass reader chose it (`is_area`).
"""

import json
import os

from osgeo import ogr

from odc.shom import bbox_of, intersects

OSMIUM_FILTER = (
    'nwr/man_made=pier,breakwater,groyne,quay',
    'nwr/leisure=slipway',
    'nwr/natural=reef,peak,volcano',
    'w/building',
    'r/building',
)
AREA_MAN_MADE = ('breakwater', 'groyne', 'quay')


def is_area(tags, from_relation):
    if tags.get('area') == 'no':
        return False
    return (
        from_relation
        or tags.get('area') == 'yes'
        or tags.get('natural') == 'reef'
        or tags.get('man_made') in AREA_MAN_MADE
        or 'building' in tags
    )


def drop_inland_buildings(src, dst, near, bounds, step):
    """Copies the export, leaving out buildings whose bbox covers no True cell
    of near (see sources.near_sea)."""
    minlon, _, _, maxlat = bounds
    nrows, ncols = near.shape
    kept = dropped = 0
    with open(src, encoding='utf-8') as fh, open(dst + '.part', 'w', encoding='utf-8') as out:
        for line in fh:
            feature = json.loads(line.lstrip('\x1e'))
            if 'building' in (feature.get('properties') or {}):
                x0, y0, x1, y1 = bbox_of(feature['geometry'])
                r0 = max(int((maxlat - y1) / step), 0)
                r1 = min(int((maxlat - y0) / step), nrows - 1)
                c0 = max(int((x0 - minlon) / step), 0)
                c1 = min(int((x1 - minlon) / step), ncols - 1)
                if r0 > r1 or c0 > c1 or not near[r0 : r1 + 1, c0 : c1 + 1].any():
                    dropped += 1
                    continue
            out.write(line)
            kept += 1
    os.replace(dst + '.part', dst)
    return kept, dropped


def read_features(path, bbox=None):
    """(tags, geometry) for each OSM object meeting bbox, closed ways as line or area."""
    lines, areas = {}, {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            feature = json.loads(line.lstrip('\x1e'))
            kind, number = feature['id'][0], int(feature['id'][1:])
            if bbox is not None and not intersects(bbox_of(feature['geometry']), bbox):
                continue
            tags = feature.get('properties') or {}
            geometry = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
            if kind == 'a':
                from_relation = bool(number % 2)
                if is_area(tags, from_relation):
                    areas[('r' if from_relation else 'w', number // 2)] = (tags, geometry)
            elif kind == 'w':
                lines[('w', number)] = (tags, geometry)
            else:
                yield tags, geometry
    for key, item in lines.items():
        if key not in areas:
            yield item
    yield from areas.values()
