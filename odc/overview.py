"""A sea's overview chart: one MBTiles of zooms OVERVIEW_MINZOOM to 8 over
every published unit of the sea's regions, for planning a passage between
them. A chart set adds its rows below the units' z9 (docs/index-format.md).

Each unit's z9 tile is already the unit at its coarsest, with every source's
priority applied, so the overview is made from those tiles alone: decoded,
cut back to the unit (a tile reaches a buffer into its neighbours), and
tiled again. The overview leaves out what only helps close in (soundings,
buoys, buildings) and keeps every danger, so a planned track shows what it
crosses.

Usage:
  python3 -m odc.overview version <overviews/<id>.json> <index.json>
  python3 -m odc.overview collect <overviews/<id>.json> <index.json> <work dir>
  python3 -m odc.overview tile <overviews/<id>.json> <index.json> <work dir> <out.mbtiles>
"""

import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from osgeo import ogr

from odc import attribution, shom, tile
from odc.build_unit import bbox_polygon
from odc.grid import UNIT_ZOOM, Unit

OVERVIEW_MINZOOM = 4
OVERVIEW_MAXZOOM = UNIT_ZOOM - 1
# Class -> the first overview zoom that shows it. Areas a passage must keep
# clear of show from the smallest zoom; point dangers once a tile is a few
# hundred kilometres wide, cables and fishing gear once it is about 150 km.
MINZOOM = {
    'LNDARE': 4,
    'COALNE': 4,
    'DEPARE': 4,
    'UNSARE': 4,
    'DRGARE': 4,
    'PRDARE': 4,
    'TSSLPT': 4,
    'TSEZNE': 4,
    'TSELNE': 4,
    'TSSBND': 4,
    'TSSCRS': 4,
    'TSSRON': 4,
    'ISTZNE': 4,
    'PRCARE': 4,
    'RESARE': 4,
    'MIPARE': 4,
    'DMPGRD': 5,
    'SEAARE': 5,
    'LNDRGN': 6,
    'DEPCNT': 6,
    'WRECKS': 6,
    'UWTROC': 6,
    'OBSTRN': 6,
    'OFSPLF': 6,
    'LIGHTS': 7,
    'CBLSUB': 7,
    'PIPSOL': 7,
    'FSHFAC': 7,
    'MARCUL': 7,
    'ACHARE': 7,
}
PARALLEL_DOWNLOADS = 8


def load(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def drawn(overview, unit):
    """Whether the overview draws the unit: every unit of its own regions, and
    those of a region another sea's overview serves that lie in the box the
    overview shares of it (`shared`, region id -> bbox), so a coast that
    faces both seas shows in both."""
    if unit['region'] in overview['regions']:
        return True
    box = overview.get('shared', {}).get(unit['region'])
    if box is None:
        return False
    w, s, e, n = Unit.parse(unit['id']).bounds
    return w < box[2] and e > box[0] and s < box[3] and n > box[1]


def units_of(overview, index):
    return sorted((u for u in index['units'] if drawn(overview, u)), key=lambda u: u['id'])


def version(overview, units):
    """Changes when, and only when, a unit it is made from or the overview's code changes."""
    digest = hashlib.sha256()
    with open(__file__, 'rb') as fh:
        digest.update(fh.read())
    digest.update(json.dumps(overview, sort_keys=True).encode())
    for unit in units:
        digest.update(f'{unit["id"]} {unit["version"]}\n'.encode())
    return digest.hexdigest()[:16]


def asset_name(overview_id, overview_version):
    return f'overview-{overview_id}.{overview_version}.mbtiles'


def release_tag(overview_id):
    return f'overview-{overview_id}'


def kept(layer, feature, unit_box):
    """The feature cut to its unit, with its overview zoom, or None."""
    geometry = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
    if geometry is None or geometry.IsEmpty():
        return None
    if geometry.GetDimension() > 0 and not unit_box.Contains(geometry):
        geometry = geometry.MakeValid().Intersection(unit_box)
        if geometry is None or geometry.IsEmpty() or geometry.GetDimension() == 0:
            return None
    return {
        'type': 'Feature',
        'properties': feature.get('properties') or {},
        'geometry': json.loads(geometry.ExportToJson(['COORDINATE_PRECISION=6'])),
        'tippecanoe': {'minzoom': MINZOOM[layer]},
    }


def unit_features(mbtiles, unit):
    """(layer, feature) of the unit's z9 tile that the overview keeps."""
    decoded = subprocess.run(
        ['tippecanoe-decode', '-z', str(UNIT_ZOOM), '-Z', str(UNIT_ZOOM), mbtiles],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    box = bbox_polygon(*unit.bounds)
    for tile_doc in json.loads(decoded)['features']:
        where = tile_doc.get('properties') or {}
        if (where.get('x'), where.get('y')) != (unit.x, unit.y):
            continue
        for layer in tile_doc['features']:
            name = layer['properties']['layer']
            if name not in MINZOOM:
                continue
            for feature in layer['features']:
                found = kept(name, feature, box)
                if found is not None:
                    yield name, found


def fetch_unit(unit, work):
    path = os.path.join(work, f'{unit["id"]}.mbtiles')
    with shom.get(unit['url'], timeout=600) as response, open(path + '.part', 'wb') as fh:
        shutil.copyfileobj(response, fh)
    os.replace(path + '.part', path)
    return path


def collect(overview, index, work):
    """Every kept feature of the overview's units, one GeoJSONSeq per layer
    in work/layers. A unit's file is deleted once read: a sea's units do not
    fit the disk of a CI runner at once."""
    layers_dir = os.path.join(work, 'layers')
    shutil.rmtree(layers_dir, ignore_errors=True)
    os.makedirs(layers_dir)
    files = {}
    units = units_of(overview, index)
    counts = {}
    with tempfile.TemporaryDirectory(dir=work) as tmp, ThreadPoolExecutor(PARALLEL_DOWNLOADS) as pool:
        # At most PARALLEL_DOWNLOADS units wait on the disk at once.
        pending = deque()
        queue = iter(units)
        for unit in itertools.islice(queue, PARALLEL_DOWNLOADS):
            pending.append((unit, pool.submit(fetch_unit, unit, tmp)))
        while pending:
            unit, future = pending.popleft()
            path = future.result()
            for name, feature in unit_features(path, Unit.parse(unit['id'])):
                if name not in files:
                    files[name] = open(os.path.join(layers_dir, f'{name}.geojsonseq'), 'w', encoding='utf-8')
                files[name].write(json.dumps(feature, ensure_ascii=False) + '\n')
                counts[name] = counts.get(name, 0) + 1
            os.remove(path)
            for following in itertools.islice(queue, 1):
                pending.append((following, pool.submit(fetch_unit, following, tmp)))
    for fh in files.values():
        fh.close()
    print(f'{len(units)} units: {json.dumps(dict(sorted(counts.items())))}', flush=True)
    return counts


def attribution_of(overview, index):
    regions = set(overview['regions']) | set(overview.get('shared', {}))
    texts = [r.get('attribution', '') for r in index['regions'] if r['id'] in regions]
    return attribution.merge(t for t in texts if t)


def tippecanoe_args(layers_dir, out, name, attribution_text):
    layers = [
        f'--named-layer={f[: -len(".geojsonseq")]}:{os.path.join(layers_dir, f)}'
        for f in sorted(os.listdir(layers_dir))
        if f.endswith('.geojsonseq')
    ]
    return [
        'tippecanoe',
        '-o',
        out,
        '-n',
        name,
        f'--description={tile.DESCRIPTION}',
        f'--attribution={attribution_text}',
        '-Z',
        str(OVERVIEW_MINZOOM),
        '-z',
        str(OVERVIEW_MAXZOOM),
        # Every danger stays at every zoom that shows it.
        '-r1',
        '--no-tile-size-limit',
        '--no-feature-limit',
        '--detect-shared-borders',
        '--force',
        '--quiet',
        *layers,
    ]


def tile_overview(overview, index, work, out):
    units = units_of(overview, index)
    overview_version = version(overview, units)
    subprocess.run(
        tippecanoe_args(
            os.path.join(work, 'layers'), out, f'{overview["name"]} overview', attribution_of(overview, index)
        ),
        check=True,
    )
    tile.set_metadata(out, {'type': 'S-57', 'x-odc-overview': overview['id'], 'x-odc-version': overview_version})
    print(json.dumps({'overview': overview['id'], 'version': overview_version, 'bytes': os.path.getsize(out)}))
    return overview_version


def main():
    command, overview_path, index_path, *rest = sys.argv[1:]
    overview, index = load(overview_path), load(index_path)
    if command == 'version':
        units = units_of(overview, index)
        print(version(overview, units) if units else '')
        return
    work = rest.pop(0)
    os.makedirs(work, exist_ok=True)
    if command == 'collect':
        collect(overview, index, work)
    elif command == 'tile':
        tile_overview(overview, index, work, rest[0])
    else:
        sys.exit(f'unknown command {command}')


if __name__ == '__main__':
    main()
