import json

import pytest

pytest.importorskip('osgeo')

from odc import index, units  # noqa: E402
from odc.grid import Unit  # noqa: E402

REGION = {'id': 'test', 'name': 'Test', 'bbox': [165.9, -22.6, 167.3, -21.95]}
A, B = Unit(492, 288), Unit(494, 288)


def point(lon, lat, **props):
    return {'type': 'Feature', 'properties': props, 'geometry': {'type': 'Point', 'coordinates': [lon, lat]}}


def versions_for(tmp_path, features):
    (tmp_path / 'wfs').mkdir(exist_ok=True)
    (tmp_path / 'wfs' / 'NS__layer.json').write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
    digests = units.Digests([A, B])
    units.add_feature_collection(digests, 'wfs', str(tmp_path / 'wfs' / 'NS__layer.json'))
    return digests.versions()


def test_a_changed_feature_changes_only_the_units_it_reaches(tmp_path):
    lon_a, lat_a = 166.2, -22.3
    lon_b, lat_b = 167.7, -22.3
    before = versions_for(tmp_path, [point(lon_a, lat_a, name='x'), point(lon_b, lat_b, name='y')])
    after = versions_for(tmp_path, [point(lon_a, lat_a, name='x'), point(lon_b, lat_b, name='z')])
    assert before[A.id] == after[A.id]
    assert before[B.id] != after[B.id]


def test_feature_order_does_not_change_versions(tmp_path):
    features = [point(166.2, -22.3, name='x'), point(166.3, -22.3, name='y')]
    assert versions_for(tmp_path, features) == versions_for(tmp_path, features[::-1])


def test_plan_builds_only_new_versions():
    versions = {A.id: 'aaaa', B.id: 'bbbb'}
    old = {
        'units': [
            {'id': A.id, 'region': 'test', 'version': 'aaaa'},
            {'id': 'z9-1-1', 'region': 'test', 'version': 'cccc'},
        ]
    }
    result = units.plan(REGION, [A, B], versions, old, batch=1)
    assert result['build'] == [B.id]
    assert result['kept'] == [A.id]
    assert result['removed'] == ['z9-1-1']
    assert result['batches'] == [{'index': 0, 'units': [B.id]}]


def test_unchanged_index_keeps_generated_time():
    old_unit = {'id': A.id, 'region': 'test', 'version': 'aaaa', 'url': f'https://x/{A.id}.aaaa.mbtiles'}
    plan = {'versions': {A.id: 'aaaa'}, 'kept': [A.id]}
    first, _ = index.build_index(REGION, plan, '/nonexistent', {'units': [old_unit], 'regions': []}, '2026-11-16')
    second, changed = index.build_index(REGION, plan, '/nonexistent', first, '2026-11-17')
    assert not changed
    assert second['generated'] == first['generated']


def test_replaced_assets_are_deleted_after_the_grace_period():
    old = {'units': [{'url': 'https://x/u.old.mbtiles'}]}
    new = {'units': [{'url': 'https://x/u.new.mbtiles'}]}
    retired, delete = index.retire(old, new, {}, '2026-11-01')
    assert retired == {'u.old.mbtiles': '2026-11-01'} and delete == []
    retired, delete = index.retire(new, new, retired, '2026-11-15')
    assert retired == {} and delete == ['u.old.mbtiles']
