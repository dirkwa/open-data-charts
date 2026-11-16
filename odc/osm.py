"""OpenStreetMap inputs: a regional extract filtered to the features a chart
uses, and the coastline as land polygons.

`osmium export` writes a closed way twice, as a line ('w' id) and as an
area ('a' id, twice the way id). The chart wants one of the two, chosen as
the prototype's Overpass reader chose it (`is_area`).
"""

import json

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
