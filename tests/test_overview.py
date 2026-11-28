import json
import os
import sqlite3
import subprocess
from concurrent.futures import Future

import pytest

pytest.importorskip('osgeo')

from osgeo import ogr  # noqa: E402

from odc import index, overview  # noqa: E402
from odc.grid import Unit  # noqa: E402

UNIT = Unit(274, 164)
BALTIC = {'id': 'baltic', 'name': 'Baltic Sea', 'regions': ['a', 'b']}
OVERVIEW_URL = 'https://github.com/dirkwa/open-data-charts/releases/download/overview-baltic/overview-baltic.v1.mbtiles'


def region(region_id, overview_entry=None):
    return {'id': region_id, 'name': region_id, 'bbox': [0, 0, 1, 1], 'overview': overview_entry, 'attribution': 'x'}


def entry(url=OVERVIEW_URL):
    return {'url': url, 'sha256': '0' * 64, 'size': 1, 'version': 'v1', 'minzoom': 4, 'maxzoom': 8}


def test_a_region_build_keeps_the_region_s_overview():
    old = {'units': [], 'regions': [region('a', entry())]}
    plan = {'versions': {}, 'kept': []}
    new, _ = index.build_index({'id': 'a', 'name': 'a', 'bbox': [0, 0, 1, 1]}, plan, '/nonexistent', old, 'today')
    assert new['regions'][0]['overview'] == entry()


def test_the_overview_goes_to_every_listed_region_of_its_sea():
    old = {'units': [], 'regions': [region('a'), region('b'), region('c')]}
    new, changed = index.with_overview(old, BALTIC, entry())
    assert changed
    assert [r['overview'] for r in new['regions']] == [entry(), entry(), None]


def test_a_replaced_overview_retires_on_its_own_release_and_not_on_a_region_s():
    old = {'units': [], 'regions': [region('a', entry())]}
    new = {'units': [], 'regions': [region('a', entry(OVERVIEW_URL.replace('v1', 'v2')))]}
    released = {'overview-baltic.v1.mbtiles', 'overview-baltic.v2.mbtiles'}
    retired, _ = index.retire(old, new, {}, '2026-11-01T00:00:00+00:00', 'overview-baltic', released)
    assert retired == {'overview-baltic.v1.mbtiles': {'region': 'overview-baltic', 'since': None}}
    # A region's run while Pages still serves the overview: nothing retires.
    retired, _ = index.retire(old, old, {}, '2026-11-01T00:00:00+00:00', 'a', set())
    assert retired == {}


def test_the_version_follows_the_units():
    units = [{'id': 'z9-1-1', 'version': 'aaaa'}]
    assert overview.version(BALTIC, units) == overview.version(BALTIC, list(units))
    assert overview.version(BALTIC, units) != overview.version(BALTIC, [{'id': 'z9-1-1', 'version': 'bbbb'}])


def feature(geometry, **props):
    return {'type': 'Feature', 'properties': props, 'geometry': geometry}


def test_a_unit_s_tile_is_cut_back_to_the_unit_and_keeps_only_overview_classes(tmp_path):
    w, s, e, n = UNIT.bounds
    beyond = [[w + 0.1, s + 0.1], [e + 0.2, s + 0.1], [e + 0.2, n - 0.1], [w + 0.1, n - 0.1], [w + 0.1, s + 0.1]]
    layers = {
        'DEPARE': feature({'type': 'Polygon', 'coordinates': [beyond]}, DRVAL1=5),
        'WRECKS': feature({'type': 'Point', 'coordinates': [w + 0.2, s + 0.2]}, CATWRK='2'),
        'SOUNDG': feature({'type': 'Point', 'coordinates': [w + 0.3, s + 0.2]}),
    }
    for name, f in layers.items():
        (tmp_path / f'{name}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': [f]}))
    mbtiles = str(tmp_path / 'unit.mbtiles')
    subprocess.run(
        ['tippecanoe', '-q', '-o', mbtiles, '-Z9', '-z9', '--buffer=80', '-r1']
        + [f'--named-layer={name}:{tmp_path / name}.geojson' for name in layers],
        check=True,
    )
    found = list(overview.unit_features(mbtiles, UNIT))
    assert sorted(name for name, _ in found) == ['DEPARE', 'WRECKS']
    depare = next(f for name, f in found if name == 'DEPARE')
    xmin, xmax, _, _ = ogr.CreateGeometryFromJson(json.dumps(depare['geometry'])).GetEnvelope()
    assert xmax <= e + 1e-9 and xmin < e
    assert depare['tippecanoe'] == {'minzoom': overview.MINZOOM['DEPARE']}


def test_the_entry_reads_the_version_and_zooms_the_file_carries(tmp_path):
    path = tmp_path / 'o.mbtiles'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata (name text, value text)')
        db.executemany(
            'INSERT INTO metadata VALUES (?, ?)', [('x-odc-version', 'abc'), ('minzoom', '4'), ('maxzoom', '8')]
        )
    found = index.overview_entry(BALTIC, str(path))
    assert found['url'].endswith('/overview-baltic/overview-baltic.abc.mbtiles')
    assert (found['version'], found['minzoom'], found['maxzoom'], found['size']) == ('abc', 4, 8, os.path.getsize(path))


def test_every_overview_names_known_regions_and_no_region_is_in_two():
    root = os.path.join(os.path.dirname(__file__), '..')
    regions = {name[:-5] for name in os.listdir(os.path.join(root, 'regions'))}
    seen = {}
    for name in sorted(os.listdir(os.path.join(root, 'overviews'))):
        with open(os.path.join(root, 'overviews', name), encoding='utf-8') as fh:
            sea = json.load(fh)
        assert name == f'{sea["id"]}.json'
        for region_id in sea['regions']:
            assert region_id in regions
            assert region_id not in seen, f'{region_id} is in {seen.get(region_id)} and {sea["id"]}'
            seen[region_id] = sea['id']


def test_collect_keeps_only_a_few_units_on_the_disk_at_once(tmp_path, monkeypatch):
    units = [
        {'id': Unit(UNIT.x + i, UNIT.y).id, 'region': 'a', 'url': f'u{i}', 'version': 'v'}
        for i in range(3 * overview.PARALLEL_DOWNLOADS)
    ]
    most = []

    def fetch_unit(unit, work):
        path = os.path.join(work, unit['url'])
        open(path, 'w').close()
        most.append(len(os.listdir(work)))
        return path

    class AtOnce:
        # Runs each download as it is submitted, so one submitted early is on the disk at once.
        def __init__(self, workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, fn, *args):
            done = Future()
            done.set_result(fn(*args))
            return done

        def map(self, fn, items):
            return iter([fn(item) for item in items])

    monkeypatch.setattr(overview, 'ThreadPoolExecutor', AtOnce)
    monkeypatch.setattr(overview, 'fetch_unit', fetch_unit)
    monkeypatch.setattr(overview, 'unit_features', lambda path, unit: iter(()))
    overview.collect(BALTIC, {'units': units}, str(tmp_path))
    assert len(most) == len(units) and max(most) <= overview.PARALLEL_DOWNLOADS


def test_a_unit_listed_by_two_regions_is_taken_once_from_the_latest_build():
    units = [
        {'id': UNIT.id, 'region': 'b', 'version': 'new', 'built': '2026-11-28'},
        {'id': UNIT.id, 'region': 'a', 'version': 'old', 'built': '2026-11-26'},
    ]
    assert [u['version'] for u in overview.units_of(BALTIC, {'units': units})] == ['new']


def test_a_region_taking_a_unit_over_drops_the_other_region_s_entry():
    old = {
        'units': [
            {'id': UNIT.id, 'region': 'a', 'version': 'old', 'url': 'https://x/u.old.mbtiles'},
            {'id': 'z9-1-1', 'region': 'a', 'version': 'v', 'url': 'https://x/v.mbtiles'},
        ],
        'regions': [region('a')],
    }
    kept = {'id': UNIT.id, 'region': 'b', 'version': 'new', 'url': 'https://x/u.new.mbtiles'}
    old['units'].append(kept)
    plan = {'versions': {UNIT.id: 'new'}, 'kept': [UNIT.id]}
    new, _ = index.build_index({'id': 'b', 'name': 'b', 'bbox': [0, 0, 1, 1]}, plan, '/nonexistent', old, 'today')
    assert sorted((u['region'], u['id']) for u in new['units']) == [('a', 'z9-1-1'), ('b', UNIT.id)]
