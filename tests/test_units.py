import json

import numpy as np
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


def write_grid(path, minlon, maxlat, step, size):
    from osgeo import gdal

    grid = gdal.GetDriverByName('GTiff').Create(str(path), size, size, 1, gdal.GDT_Float32)
    grid.SetGeoTransform((minlon, step, 0.0, maxlat, 0.0, -step))
    grid.GetRasterBand(1).Fill(-10.0)
    grid = None


def test_a_new_model_grid_changes_every_unit_through_the_mosaic(tmp_path):
    (tmp_path / 'dem').mkdir()
    write_land(tmp_path / 'land.gpkg', [box_wkt(166.2, -22.3, 166.21, -22.29)])
    mirror_index = {'surveys': {}}
    write_grid(tmp_path / 'dem' / 'emodnet_a.tif', A.bounds[0], A.bounds[3], 0.01, 50)
    before = units.unit_versions([A, B], str(tmp_path), mirror_index, 'b', 'x')
    write_grid(tmp_path / 'dem' / 'emodnet_b.tif', B.bounds[0], B.bounds[3], 0.001, 50)
    after = units.unit_versions([A, B], str(tmp_path), mirror_index, 'b', 'x')
    assert before[A.id] != after[A.id]


def test_dem_grids_skip_an_unfinished_download(tmp_path):
    from odc import sources

    (tmp_path / 'dem').mkdir()
    for name in ('emodnet_+53_+004.tif', 'emodnet_+53_+006.tif.wcs', 'shom.asc'):
        (tmp_path / 'dem' / name).write_text('')
    found = [p.rsplit('/', 1)[1] for p in sources.dem_grids(str(tmp_path))]
    assert found == ['emodnet_+53_+004.tif', 'shom.asc']


def test_renaming_a_distant_grid_keeps_other_units(tmp_path):
    (tmp_path / 'dem').mkdir()
    write_land(tmp_path / 'land.gpkg', [box_wkt(166.2, -22.3, 166.21, -22.29)])
    write_grid(tmp_path / 'dem' / 'emodnet_a.tif', A.bounds[0], A.bounds[3], 0.01, 50)
    write_grid(tmp_path / 'dem' / 'emodnet_b.tif', B.bounds[0], B.bounds[3], 0.01, 50)
    before = units.unit_versions([A, B], str(tmp_path), {'surveys': {}}, 'b', 'x')
    (tmp_path / 'dem' / 'emodnet_b.tif').rename(tmp_path / 'dem' / 'emodnet_c.tif')
    after = units.unit_versions([A, B], str(tmp_path), {'surveys': {}}, 'b', 'x')
    assert before[A.id] == after[A.id]


def test_a_change_in_the_dem_fill_margin_changes_the_unit(tmp_path):
    from osgeo import gdal

    (tmp_path / 'dem').mkdir()
    write_land(tmp_path / 'land.gpkg', [box_wkt(166.2, -22.3, 166.21, -22.29)])
    grid_path = tmp_path / 'dem' / 'emodnet_a.tif'
    box = A.build_box.bbox
    step = 0.001
    write_grid(grid_path, box[0] - 0.05, box[3] + 0.05, step, 1000)
    before = units.unit_versions([A], str(tmp_path), {'surveys': {}}, 'b', 'x')
    grid = gdal.Open(str(grid_path), gdal.GA_Update)
    # A pixel 0.005 deg west of the build box: inside the fill margin.
    col = int((0.05 - 0.005) / step)
    row = int((0.05 + 0.1) / step)
    grid.GetRasterBand(1).WriteArray(np.full((1, 1), -3.0, dtype=np.float32), col, row)
    grid = None
    after = units.unit_versions([A], str(tmp_path), {'surveys': {}}, 'b', 'x')
    assert before[A.id] != after[A.id]


def reef(lon, lat, cls='Reef Crest'):
    ring = [[lon, lat], [lon + 0.001, lat], [lon + 0.001, lat + 0.001], [lon, lat]]
    return json.dumps(
        {'type': 'Feature', 'properties': {'class_name': cls}, 'geometry': {'type': 'Polygon', 'coordinates': [ring]}}
    )


def aca_versions(tmp_path, squares):
    aca = tmp_path / 'aca'
    aca.mkdir(exist_ok=True)
    for old in aca.iterdir():
        old.unlink()
    for name, lines in squares.items():
        (aca / name).write_text(''.join(line + '\n' for line in lines))
    digests = units.Digests([A, B])
    units.add_aca(digests, sorted(str(p) for p in aca.iterdir()))
    return digests.versions()


def test_a_reef_seen_from_two_squares_counts_once(tmp_path):
    crest = reef(166.2, -22.3)
    before = aca_versions(tmp_path, {'reefs_-23_+166.geojsonseq': [crest]})
    assert (
        aca_versions(tmp_path, {'reefs_-23_+166.geojsonseq': [crest], 'reefs_-23_+165.geojsonseq': [crest]}) == before
    )
    changed = aca_versions(tmp_path, {'reefs_-23_+166.geojsonseq': [reef(166.2, -22.3, 'Inner Reef Flat')]})
    assert changed[A.id] != before[A.id]


def test_surveys_are_fetched_from_their_release():
    def entry(asset, **release):
        return {'bbox': [166.0, -22.5, 166.1, -22.4], 'reduced': {'asset': asset, **release}}

    mirror_index = {
        'surveys': {
            'S1': entry('S1.b0.npz', release='mirror-b'),
            'S2': entry('S2.c0.npz'),
            'S3': {**entry('S3.d0.npz', release='mirror-d'), 'bbox': [10.0, 40.0, 10.1, 40.1]},
        }
    }
    assert units.surveys_for(mirror_index, [A.id]) == ['mirror S2.c0.npz', 'mirror-b S1.b0.npz']


def square_versions(tmp_path, east):
    """Versions with one satellite square ending at longitude east, beside A's build box."""
    from osgeo import gdal

    minlon, minlat, maxlon, maxlat = A.build_box.bbox
    path = str(tmp_path / f'depth_{east}.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, 10, 10, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((east - 0.1, 0.01, 0, maxlat, 0, -0.01))
    ds.GetRasterBand(1).WriteArray(np.full((10, 10), 3, np.uint8))
    ds = None
    digests = units.Digests([A, B])
    units.add_squares(digests, 'aca-depth', [path])
    return digests.versions()


def test_a_square_only_touching_the_build_box_does_not_reach_the_unit(tmp_path):
    minlon = A.build_box.bbox[0]
    none = units.Digests([A, B]).versions()
    assert square_versions(tmp_path, minlon)[A.id] == none[A.id]
    assert square_versions(tmp_path, minlon + 0.05)[A.id] != none[A.id]


def test_a_square_at_the_antimeridian_reaches_the_unit_beside_it(tmp_path):
    from osgeo import gdal

    from odc.grid import unit_at

    unit = unit_at(-179.95, -16.5)
    path = str(tmp_path / 'depth_-17_-180.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, 10, 10, 1, gdal.GDT_Byte)
    ds.SetGeoTransform((-180.0, 0.1, 0, -16.0, 0, -0.1))
    ds.GetRasterBand(1).WriteArray(np.full((10, 10), 3, np.uint8))
    ds = None
    digests = units.Digests([unit])
    units.add_squares(digests, 'aca-depth', [path])
    assert digests.parts[unit.id]
