"""The Estonian Transport Administration's hydrographic information system
(HIS): its rocks, wrecks and obstructions, through its open WFS, for regions
listing `vta-his`.

The WFS answers in GML on the Estonian grid (EPSG:3301, northing first),
ignores paging and returns at most HIS_CAP objects a request, so the
region is asked for box by box, a box holding the cap split in four.
"""

import json
import math
import os
import re
import urllib.parse

from osgeo import osr

from odc import shom

HIS_WFS = 'https://his.vta.ee:8443/HIS/WFS'
HIS_CAP = 10000
# The extent the service gives for its layers (EPSG:3301 metres).
HIS_EXTENT = (282000.0, 6375000.0, 752000.0, 6660000.0)
HIS_BOX_M = 50000.0
HIS_LAYERS = (('kivi', 'UWTROC'), ('vrakk', 'WRECKS'), ('takist', 'OBSTRN'))
# Surveys put about 1.35 million boulders in the database, most of them far
# deeper than any keel; deeper rocks are left out.
ROCK_MAX_DEPTH_M = 10.0
UNKNOWN = 999
# Wrecks the database has no name for are numbered NIMETU-<n> ("nameless").
NAMELESS = re.compile(r'^NIMETU\b', re.IGNORECASE)
MEMBER = re.compile(r'<wfs:featureMember>(.*?)</wfs:featureMember>', re.DOTALL)
GML_ID = re.compile(r'gml:id="([^"]+)"')
POS = re.compile(r'<gml:pos[^>]*>\s*([-\d.]+)\s+([-\d.]+)\s*</gml:pos>')
FIELD = re.compile(r'<his:(\w+)>([^<]*)</his:\1>')

_TO_WGS84 = None


def to_wgs84():
    global _TO_WGS84
    if _TO_WGS84 is None:
        estonia, wgs84 = osr.SpatialReference(), osr.SpatialReference()
        estonia.ImportFromEPSG(3301)
        wgs84.ImportFromEPSG(4326)
        for srs in (estonia, wgs84):
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        _TO_WGS84 = (osr.CoordinateTransformation(estonia, wgs84), osr.CoordinateTransformation(wgs84, estonia))
    return _TO_WGS84


def unescape(text):
    return (
        text.replace('&#13;', ' ')
        .replace('&lt;', '<')
        .replace('&gt;', '>')
        .replace('&quot;', '"')
        .replace('&amp;', '&')
    )


def parse(gml):
    """(gml id, easting, northing, fields) of each object in a GetFeature answer."""
    for member in MEMBER.finditer(gml):
        body = member.group(1)
        ident, pos = GML_ID.search(body), POS.search(body)
        if not ident or not pos:
            continue
        fields = {name: unescape(value).strip() for name, value in FIELD.findall(body)}
        yield ident.group(1), float(pos.group(2)), float(pos.group(1)), fields


def number(fields, key):
    try:
        value = float(fields.get(key) or 'nan')
    except ValueError:
        return None
    return None if math.isnan(value) else value


def code(fields, key):
    value = number(fields, key)
    return None if value is None or int(value) == UNKNOWN else int(value)


def s57(layer, fields):
    """The S-57 class and attributes of an HIS object, None for a rock too deep to chart."""
    objl = dict(HIS_LAYERS)[layer]
    attrs = {}
    depth = number(fields, 'vraki_sygavus' if layer == 'vrakk' else 'sygavus')
    if layer == 'kivi' and depth is not None and depth > ROCK_MAX_DEPTH_M:
        return None
    if depth is not None:
        attrs['VALSOU'] = round(depth, 2)
    watlev = code(fields, 'watlev_id')
    if watlev is not None:
        attrs['WATLEV'] = watlev
    if layer == 'vrakk':
        catwrk = code(fields, 'catwrk_id')
        if catwrk is not None:
            attrs['CATWRK'] = catwrk
        name = fields.get('laevanimi')
        if name and not NAMELESS.match(name):
            attrs['OBJNAM'] = name
    else:
        catobs = code(fields, 'catobs_id')
        if catobs is not None:
            attrs['CATOBS'] = catobs
        if fields.get('kirjeldus'):
            attrs['INFORM'] = fields['kirjeldus']
    return objl, attrs


def estonian_box(bounds):
    """bounds (lon/lat) as an EPSG:3301 box inside the service's extent, None if outside it."""
    minlon, minlat, maxlon, maxlat = bounds
    _, forward = to_wgs84()
    steps = 8
    corners = [
        forward.TransformPoint(minlon + (maxlon - minlon) * i / steps, lat)[:2]
        for i in range(steps + 1)
        for lat in (minlat, maxlat)
    ] + [
        forward.TransformPoint(lon, minlat + (maxlat - minlat) * i / steps)[:2]
        for i in range(steps + 1)
        for lon in (minlon, maxlon)
    ]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    box = (
        max(min(xs), HIS_EXTENT[0]),
        max(min(ys), HIS_EXTENT[1]),
        min(max(xs), HIS_EXTENT[2]),
        min(max(ys), HIS_EXTENT[3]),
    )
    return box if box[0] < box[2] and box[1] < box[3] else None


def get_box(layer, box):
    minx, miny, maxx, maxy = box
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': layer,
        'bbox': f'{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::3301',
    }
    with shom.get(f'{HIS_WFS}?{urllib.parse.urlencode(params)}', timeout=300) as response:
        return response.read().decode('utf-8')


def layer_objects(layer, box, get=get_box):
    """Every object of a layer in an EPSG:3301 box, by gml id."""
    found = {}
    pending = [box]
    while pending:
        minx, miny, maxx, maxy = pending.pop()
        gml = get(layer, (minx, miny, maxx, maxy))
        if gml.count('<wfs:featureMember>') >= HIS_CAP:
            midx, midy = (minx + maxx) / 2, (miny + maxy) / 2
            pending += [
                (minx, miny, midx, midy),
                (midx, miny, maxx, midy),
                (minx, midy, midx, maxy),
                (midx, midy, maxx, maxy),
            ]
            continue
        for ident, x, y, fields in parse(gml):
            if minx <= x <= maxx and miny <= y <= maxy:
                found[ident] = (x, y, fields)
    return found


def boxes(box):
    minx, miny, maxx, maxy = box
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            yield (x, y, min(x + HIS_BOX_M, maxx), min(y + HIS_BOX_M, maxy))
            y += HIS_BOX_M
        x += HIS_BOX_M


def features(region_bounds, get=get_box):
    """The region's HIS objects as GeoJSON points with S-57 attributes, sorted by id."""
    box = estonian_box(region_bounds)
    if box is None:
        return []
    back, _ = to_wgs84()
    minlon, minlat, maxlon, maxlat = region_bounds
    out = []
    for layer, _ in HIS_LAYERS:
        found = {}
        for part in boxes(box):
            found.update(layer_objects(layer, part, get))
        for ident, (x, y, fields) in found.items():
            charted = s57(layer, fields)
            if charted is None:
                continue
            lon, lat = back.TransformPoint(x, y)[:2]
            if not (minlon <= lon <= maxlon and minlat <= lat <= maxlat):
                continue
            objl, attrs = charted
            out.append(
                {
                    'type': 'Feature',
                    'id': f'vta-his-{ident}',
                    'geometry': {'type': 'Point', 'coordinates': [round(lon, 7), round(lat, 7)]},
                    'properties': {'OBJL': objl, **attrs},
                }
            )
        print(f"{len(found):7d}  HIS {layer} in the region's boxes", flush=True)
    return sorted(out, key=lambda f: f['id'])


def write(path, items):
    with open(path + '.part', 'w', encoding='utf-8') as fh:
        for feature in items:
            fh.write(json.dumps(feature, ensure_ascii=False, sort_keys=True) + '\n')
    os.replace(path + '.part', path)


def read(path, bbox):
    """(objl, lon, lat, attrs, id) of the objects in bbox."""
    minlon, minlat, maxlon, maxlat = bbox
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            feature = json.loads(line)
            lon, lat = feature['geometry']['coordinates']
            if minlon <= lon <= maxlon and minlat <= lat <= maxlat:
                attrs = dict(feature['properties'])
                yield attrs.pop('OBJL'), lon, lat, attrs, feature['id']
