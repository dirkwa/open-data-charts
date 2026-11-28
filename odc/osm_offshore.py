"""A sea's OpenStreetMap seamarks as one OSM extract, for the regions whose
country extracts end near the coast: Geofabrik's and openstreetmap.fr's
reach about 12 nm out, so traffic separation schemes, buoys and platforms
farther offshore are in none of them.

The extract is read from the Overpass API, square by square, and published
in the release `osm-offshore` as `<sea>.osm.pbf`; a region lists that asset
among its extracts (`OFFSHORE_URL`), and odc.sources merges it with the
others like any extract.

Usage:
  python3 -m odc.osm_offshore <offshore/<id>.json> <out.osm.pbf>
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

from odc import shom

RELEASE = 'osm-offshore'
OFFSHORE_URL = 'https://github.com/dirkwa/open-data-charts/releases/download/' + RELEASE + '/{id}.osm.pbf'
# Instances that answer a sea's square in about a minute; a refusal or a
# timeout goes on to the next.
ENDPOINTS = (
    'https://overpass-api.de/api/interpreter',
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
)
SQUARE_DEG = 2.0
QUERY_TIMEOUT_S = 300
ROUNDS = 3
ROUND_WAIT_S = 120
# What the region builds read from OSM out there: the seamarks and what
# odc.seamarks reads as one without their tags (osm.SEAMARK_FILTER).
SELECTORS = (
    'nwr["seamark:type"]',
    'way["power"="cable"]["location"~"^(underwater|submarine)$"]',
    'way["man_made"="pipeline"]["location"~"^(underwater|submarine)$"]',
    'node["generator:source"="wind"]',
)


def squares(bbox, step=SQUARE_DEG):
    """bbox cut into squares on whole multiples of step, so the same square
    is asked for whatever the sea's box."""
    minlon, minlat, maxlon, maxlat = bbox
    for y in range(math.floor(minlat / step), math.ceil(maxlat / step)):
        for x in range(math.floor(minlon / step), math.ceil(maxlon / step)):
            yield (
                max(minlon, x * step),
                max(minlat, y * step),
                min(maxlon, (x + 1) * step),
                min(maxlat, (y + 1) * step),
            )


def query(square):
    """Overpass QL for the objects in square and everything they are made
    of, with the versions and times odc.sources needs to merge extracts."""
    w, s, e, n = square
    selected = ''.join(f'{selector};' for selector in SELECTORS)
    return f'[out:xml][timeout:{QUERY_TIMEOUT_S}][bbox:{s},{w},{n},{e}];({selected});(._;>;);out meta;'


def complete(path):
    """An Overpass answer that ran out of time or memory still ends well
    formed, with a remark instead of the objects."""
    with open(path, 'rb') as fh:
        fh.seek(max(0, os.path.getsize(path) - 4096))
        tail = fh.read()
    return tail.rstrip().endswith(b'</osm>') and b'<remark>' not in tail


def fetch_square(square, path, endpoints=ENDPOINTS, rounds=ROUNDS, wait=ROUND_WAIT_S):
    data = urllib.parse.urlencode({'data': query(square)})
    for attempt in range(rounds):
        if attempt:
            time.sleep(wait * attempt)
        for endpoint in endpoints:
            try:
                with shom.get(f'{endpoint}?{data}', timeout=QUERY_TIMEOUT_S + 60, tries=1) as response:
                    with open(path, 'wb') as fh:
                        fh.write(response.read())
            except OSError as error:
                print(f'osm-offshore: {endpoint} {square} failed ({error})', flush=True)
                continue
            if complete(path):
                return
            print(f'osm-offshore: {endpoint} {square} answered incompletely', flush=True)
    raise RuntimeError(f'no Overpass instance answered for {square}')


def build(sea, out):
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, square in enumerate(squares(sea['bbox'])):
            parts.append(os.path.join(tmp, f'square{i}.osm'))
            fetch_square(square, parts[-1])
            print(f'osm-offshore: {square} {os.path.getsize(parts[-1])} bytes', flush=True)
        # An object crossing a square's edge comes in both answers: merge
        # writes it once, and time-filter keeps the newest version where two
        # instances answered from different snapshots.
        merged = os.path.join(tmp, 'merged.osm.pbf')
        subprocess.run(['osmium', 'merge', '-O', '-o', merged, *parts], check=True)
        subprocess.run(['osmium', 'time-filter', '-O', '-o', out, merged], check=True)


def main():
    path, out = sys.argv[1:3]
    with open(path, encoding='utf-8') as fh:
        build(json.load(fh), out)


if __name__ == '__main__':
    main()
