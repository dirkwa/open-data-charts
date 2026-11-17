import json

from osgeo import ogr

from odc import osm, sources

BOUNDS = (0.0, 0.0, 0.1, 0.1)


def write_land(path, wkt):
    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(path))
    layer = ds.CreateLayer('land', geom_type=ogr.wkbPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
    layer.CreateFeature(feature)
    ds = None


def building(lon, lat, **tags):
    d = 0.00005
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    return {
        'type': 'Feature',
        'id': 'a2',
        'properties': {'building': 'yes', **tags},
        'geometry': {'type': 'Polygon', 'coordinates': [ring]},
    }


def test_only_buildings_near_the_sea_are_kept(tmp_path):
    # Land east of 0.05; the sea west of it.
    write_land(tmp_path / 'land.gpkg', 'POLYGON((0.05 0,0.1 0,0.1 0.1,0.05 0.1,0.05 0))')
    near = sources.near_sea(str(tmp_path / 'land.gpkg'), BOUNDS)
    pier = {
        'type': 'Feature',
        'id': 'w1',
        'properties': {'man_made': 'pier'},
        'geometry': {'type': 'LineString', 'coordinates': [[0.09, 0.05], [0.095, 0.05]]},
    }
    features = [
        building(0.0515, 0.05, name='coast'),
        building(0.09, 0.05, name='inland'),
        building(0.5, 0.05, name='outside'),
        pier,
    ]
    src = tmp_path / 'in.geojsonseq'
    src.write_text(''.join(json.dumps(f) + '\n' for f in features))
    kept, dropped = osm.drop_inland_buildings(
        str(src), str(tmp_path / 'out.geojsonseq'), near, BOUNDS, sources.NEAR_SEA_STEP
    )
    out = [json.loads(line) for line in (tmp_path / 'out.geojsonseq').read_text().splitlines()]
    assert (kept, dropped) == (2, 2)
    assert [f['properties'].get('name') for f in out] == ['coast', None]


def test_the_sea_mask_reaches_further_than_the_builder_keeps_buildings(tmp_path):
    write_land(tmp_path / 'land.gpkg', 'POLYGON((0.05 0,0.1 0,0.1 0.1,0.05 0.1,0.05 0))')
    near = sources.near_sea(str(tmp_path / 'land.gpkg'), BOUNDS)
    row = 50
    inland_reach = 0.0015
    col = int((0.05 + inland_reach) / sources.NEAR_SEA_STEP)
    assert near[row, col]


def test_a_building_reaching_a_near_cell_is_kept(tmp_path):
    write_land(tmp_path / 'land.gpkg', 'POLYGON((0.05 0,0.1 0,0.1 0.1,0.05 0.1,0.05 0))')
    near = sources.near_sea(str(tmp_path / 'land.gpkg'), BOUNDS)
    d = 0.00005
    # Most of the building lies by the coast; a small far part moves its bbox centre inland.
    feature = {
        'type': 'Feature',
        'id': 'a4',
        'properties': {'building': 'yes'},
        'geometry': {
            'type': 'MultiPolygon',
            'coordinates': [
                [[[0.0505, 0.05], [0.0515, 0.05], [0.0515, 0.051], [0.0505, 0.051], [0.0505, 0.05]]],
                [[[0.0698, 0.05], [0.0698 + d, 0.05], [0.0698 + d, 0.05 + d], [0.0698, 0.05 + d], [0.0698, 0.05]]],
            ],
        },
    }
    src = tmp_path / 'in.geojsonseq'
    src.write_text(json.dumps(feature) + '\n')
    kept, dropped = osm.drop_inland_buildings(
        str(src), str(tmp_path / 'out.geojsonseq'), near, BOUNDS, sources.NEAR_SEA_STEP
    )
    assert (kept, dropped) == (1, 0)


def test_an_inlet_narrower_than_a_cell_seeds_the_mask(tmp_path):
    # Land east of 0.02, cut by an inlet 0.0004 wide that falls between cell centres.
    write_land(
        tmp_path / 'land.gpkg',
        'MULTIPOLYGON(((0.02 0,0.1 0,0.1 0.0498,0.02 0.0498,0.02 0)),'
        '((0.02 0.0502,0.1 0.0502,0.1 0.1,0.02 0.1,0.02 0.0502)))',
    )
    near = sources.near_sea(str(tmp_path / 'land.gpkg'), BOUNDS)
    row = int((BOUNDS[3] - 0.0503) / sources.NEAR_SEA_STEP)
    col = int(0.08 / sources.NEAR_SEA_STEP)
    assert near[row, col]


def test_an_osm_fetch_fetches_land_first_when_it_is_missing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, 'FETCHERS', {n: (lambda r, o, n=n: calls.append(n)) for n in ('land', 'osm')})
    region = tmp_path / 'region.json'
    region.write_text('{}')
    monkeypatch.setattr('sys.argv', ['sources', str(region), str(tmp_path / 'out'), 'osm'])
    sources.main()
    assert calls == ['land', 'osm']
    calls.clear()
    (tmp_path / 'out' / 'land.gpkg').write_text('')
    sources.main()
    assert calls == ['osm']
