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


def test_replaced_assets_are_deleted_14_days_after_the_served_index_dropped_them():
    old = {'units': [{'region': 'test', 'url': 'https://x/u.old.mbtiles'}]}
    new = {'units': [{'region': 'test', 'url': 'https://x/u.new.mbtiles'}]}
    released = {'u.old.mbtiles', 'u.new.mbtiles'}
    retired, delete = index.retire(old, new, {}, '2026-11-01T23:59:00+00:00', 'test', released)
    assert retired == {'u.old.mbtiles': {'region': 'test', 'since': None}} and delete == []
    retired, delete = index.retire(new, new, retired, '2026-11-08T12:00:00+00:00', 'test', released)
    assert retired['u.old.mbtiles']['since'] == '2026-11-08T12:00:00+00:00' and delete == []
    retired, delete = index.retire(new, new, retired, '2026-11-22T11:59:00+00:00', 'test', released)
    assert delete == []
    retired, delete = index.retire(new, new, retired, '2026-11-22T12:00:00+00:00', 'test', released)
    assert delete == ['u.old.mbtiles']
    assert 'u.old.mbtiles' in retired  # until the release no longer holds it


def test_a_retired_asset_leaves_the_ledger_once_deleted():
    retired = {
        'u.old.mbtiles': {'region': 'test', 'since': '2026-11-01T00:00:00+00:00'},
        'v.old.mbtiles': {'region': 'other', 'since': '2026-11-01T00:00:00+00:00'},
    }
    new = {'units': []}
    retired, delete = index.retire(new, new, retired, '2026-12-01T00:00:00+00:00', 'test', set())
    assert list(retired) == ['v.old.mbtiles'] and delete == []


def write_land(path, polygons):
    from osgeo import ogr

    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
    layer = ds.CreateLayer('land', geom_type=ogr.wkbPolygon)
    for wkt in polygons:
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        layer.CreateFeature(feature)
    ds = None


def box_wkt(minlon, minlat, maxlon, maxlat):
    return f'POLYGON(({minlon} {minlat},{maxlon} {minlat},{maxlon} {maxlat},{minlon} {maxlat},{minlon} {minlat}))'


def test_units_without_land_or_model_are_left_out(tmp_path):
    island = A.bounds[0] + 0.1, A.bounds[1] + 0.1
    write_land(tmp_path / 'land.gpkg', [box_wkt(*island, island[0] + 0.01, island[1] + 0.01)])
    region = {'id': 'test', 'name': 'Test', 'bbox': [A.bounds[0] + 0.01, -22.5, 167.9, -22.0]}
    found = {u.id for u in units.discover(region, str(tmp_path))}
    assert found == {A.id}


def test_attribution_names_each_region_source_once():
    from odc import attribution

    text = attribution.merge([attribution.compose(['DEM A', 'Local']), attribution.compose(['DEM B', 'Local'])])
    assert text.count('Local') == 1 and 'DEM A' in text and 'DEM B' in text
    assert text.count('OpenStreetMap') == 1 and text.startswith('Shom')


def test_only_regions_built_from_shom_data_credit_shom():
    from odc import attribution

    emodnet = attribution.of_region({'sources': ['emodnet-dtm', 'osm'], 'attribution': ['EMODnet']})
    shom = attribution.of_region({'sources': ['shom-wfs', 'osm'], 'attribution': ['Model']})
    assert emodnet.startswith('EMODnet') and 'Shom' not in emodnet
    assert attribution.merge([emodnet]) == emodnet
    assert attribution.merge([emodnet, shom]).startswith('Shom') and 'EMODnet' in attribution.merge([emodnet, shom])


def test_a_retained_region_keeps_its_published_attribution():
    old_region = {'id': 'other', 'name': 'Other', 'bbox': [0, 0, 1, 1], 'attribution': 'Shom; Old model'}
    plan = {'versions': {}, 'kept': []}
    built, _ = index.build_index(REGION, plan, '/nonexistent', {'units': [], 'regions': [old_region]}, '2026-11-17')
    assert 'Old model' in built['attribution']
