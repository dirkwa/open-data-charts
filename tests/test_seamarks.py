import json

from osgeo import ogr

from odc import build_unit, seamarks

BBOX = build_unit.bbox_polygon(0.0, 0.0, 1.0, 1.0)


def node(lon, lat):
    return ogr.CreateGeometryFromWkt(f'POINT ({lon} {lat})')


def classes(tags):
    return [objl for objl, _ in seamarks.objects(tags)]


def test_a_lateral_buoy_with_topmark_and_light_is_three_objects():
    tags = {
        'seamark:type': 'buoy_lateral',
        'seamark:name': 'Bar',
        'seamark:buoy_lateral:category': 'port',
        'seamark:buoy_lateral:shape': 'can',
        'seamark:buoy_lateral:colour': 'red',
        'seamark:topmark:shape': 'cylinder',
        'seamark:topmark:colour': 'red',
        'seamark:light:colour': 'red',
        'seamark:light:character': 'Fl',
        'seamark:light:group': '2',
        'seamark:light:period': '6',
        'seamark:light:range': '3',
    }
    found = dict(seamarks.objects(tags))
    assert found['BOYLAT'] == {'OBJNAM': 'Bar', 'COLOUR': '3', 'CATLAM': '1', 'BOYSHP': 2}
    assert found['TOPMAR'] == {'COLOUR': '3', 'TOPSHP': 5}
    assert found['LIGHTS'] == {'COLOUR': '3', 'LITCHR': 2, 'SIGGRP': '(2)', 'SIGPER': 6, 'VALNMR': 3}


def test_a_cardinal_keeps_its_colour_list_and_pattern():
    tags = {
        'seamark:type': 'beacon_cardinal',
        'seamark:beacon_cardinal:category': 'north',
        'seamark:beacon_cardinal:colour': 'black;yellow',
        'seamark:beacon_cardinal:colour_pattern': 'horizontal',
        'seamark:beacon_cardinal:shape': 'pile',
    }
    assert dict(seamarks.objects(tags))['BCNCAR'] == {'COLOUR': '2,6', 'COLPAT': '1', 'CATCAM': '1', 'BCNSHP': 5}


def test_a_sectored_lighthouse_is_one_light_per_sector_sharing_the_unnumbered_keys():
    tags = {
        'seamark:type': 'light_major',
        'seamark:light:character': 'Oc(3)',
        'seamark:light:period': '12',
        'seamark:light:1:colour': 'white',
        'seamark:light:1:sector_start': '10',
        'seamark:light:1:sector_end': '120.5',
        'seamark:light:2:colour': 'red',
        'seamark:light:2:sector_start': '120.5',
        'seamark:light:2:sector_end': '200',
        'seamark:light:10:colour': 'green',
    }
    lights = [attrs for objl, attrs in seamarks.objects(tags)]
    assert [light['COLOUR'] for light in lights] == ['1', '3', '4']
    assert all(x['LITCHR'] == 8 and x['SIGGRP'] == '(3)' and x['SIGPER'] == 12 for x in lights)
    assert (lights[0]['SECTR1'], lights[0]['SECTR2']) == (10, 120.5)


def test_unknown_words_and_types_are_left_out():
    assert classes({'seamark:type': 'harbour'}) == []
    tags = {'seamark:type': 'buoy_special_purpose', 'seamark:buoy_special_purpose:colour': 'plaid;yellow'}
    assert dict(seamarks.objects(tags))['BOYSPP'] == {'COLOUR': '6'}


def test_a_daymark_is_one_object_alone_and_a_part_on_a_beacon():
    tags = {'seamark:type': 'daymark', 'seamark:daymark:shape': 'board'}
    assert seamarks.objects(tags) == [('DAYMAR', {'TOPSHP': 6})]
    tags = {'seamark:type': 'beacon_special_purpose', 'seamark:daymark:shape': 'board'}
    assert classes(tags) == ['BCNSPP', 'DAYMAR']


def test_a_wreck_keeps_its_depth_and_water_level():
    tags = {
        'seamark:type': 'wreck',
        'seamark:wreck:category': 'dangerous',
        'seamark:wreck:water_level': 'submerged',
        'seamark:wreck:depth': '4.2 m',
    }
    assert dict(seamarks.objects(tags))['WRECKS'] == {'CATWRK': '2', 'WATLEV': 3, 'VALSOU': 4.2}


def test_layers_put_area_objects_on_a_point_inside_and_drop_what_lies_outside():
    tower = ogr.CreateGeometryFromWkt('POLYGON ((0.5 0.5,0.6 0.5,0.6 0.6,0.5 0.6,0.5 0.5))')
    items = [
        ({'seamark:type': 'landmark', 'seamark:landmark:category': 'tower'}, tower),
        ({'seamark:type': 'buoy_safe_water'}, node(0.2, 0.2)),
        ({'seamark:type': 'buoy_safe_water'}, node(2.0, 0.2)),
    ]
    layers = seamarks.seamark_layers(items, BBOX, 'OSM')
    ((geom, attrs, _),) = layers['LNDMRK']
    assert geom.GetGeometryName() == 'POINT' and tower.Contains(geom)
    assert attrs == {'CATLMK': '17', 'INFORM': 'OSM'}
    assert len(layers['BOYSAW']) == 1


def test_only_mooring_buoys_are_charted():
    assert classes({'seamark:type': 'mooring', 'seamark:mooring:category': 'post'}) == []
    tags = {'seamark:type': 'mooring', 'seamark:mooring:category': 'buoy', 'seamark:mooring:shape': 'spherical'}
    assert seamarks.objects(tags) == [('MORFAC', {'CATMOR': '7', 'BOYSHP': 3})]


def test_light_categories_use_the_documented_words():
    tags = {'seamark:type': 'light_minor', 'seamark:light:category': 'horizontal;floodlight;strip_light;vertical'}
    assert dict(seamarks.objects(tags))['LIGHTS']['CATLIT'] == '19,8,9,20'


def test_a_racon_keeps_its_category_morse_letter_and_range():
    tags = {
        'seamark:type': 'radar_transponder',
        'seamark:radar_transponder:category': 'racon',
        'seamark:radar_transponder:group': 'Z',
        'seamark:radar_transponder:range': '10',
    }
    assert seamarks.objects(tags) == [('RTPBCN', {'CATRTB': 2, 'SIGGRP': '(Z)', 'VALMXR': 10})]


def test_osm_marks_near_a_shom_aid_are_left_to_shom():
    shom = {
        'BOYLAT': [(node(0.5, 0.5), {}, None)],
        'WRECKS': [(node(0.2, 0.2), {}, None)],
    }
    charted = seamarks.shom_aids(shom)
    one_mile = 1852 / 111320
    items = [
        ({'seamark:type': 'buoy_lateral'}, node(0.5 + 0.9 * one_mile, 0.5)),
        ({'seamark:type': 'buoy_lateral'}, node(0.5 + 1.1 * one_mile, 0.5)),
        ({'seamark:type': 'buoy_lateral'}, node(0.2, 0.2)),
    ]
    kept = seamarks.seamark_layers(items, BBOX, 'OSM', charted)['BOYLAT']
    assert sorted(round(g.GetX(), 4) for g, _, _ in kept) == [0.2, round(0.5 + 1.1 * one_mile, 4)]


def test_nearby_measures_longitude_shorter_away_from_the_equator():
    near = seamarks.Nearby([(0.0, 60.0)], 1852)
    assert near(1852 / 111320 / 0.5 * 0.95, 60.0)
    assert not near(1852 / 111320 / 0.5 * 1.05, 60.0)


def test_osm_dangers_near_a_shom_aid_are_kept():
    charted = seamarks.shom_aids({'BOYLAT': [(node(0.5, 0.5), {}, None)]})
    items = [
        ({'seamark:type': 'wreck', 'seamark:wreck:category': 'dangerous'}, node(0.5, 0.5)),
        ({'seamark:type': 'rock', 'seamark:rock:water_level': 'awash'}, node(0.5, 0.5)),
    ]
    assert sorted(seamarks.seamark_layers(items, BBOX, 'OSM', charted)) == ['UWTROC', 'WRECKS']


def test_the_documented_part_submerged_water_level_is_read():
    tags = {'seamark:type': 'wreck', 'seamark:wreck:water_level': 'part-submerged'}
    assert dict(seamarks.objects(tags))['WRECKS']['WATLEV'] == 1


def test_a_light_tagged_with_its_type_alone_is_still_a_light():
    assert seamarks.objects({'seamark:type': 'light_minor', 'seamark:name': 'Pier'}) == [('LIGHTS', {})]
    assert classes({'seamark:type': 'buoy_lateral'}) == ['BOYLAT']


def test_the_aid_window_reaches_a_mile_beyond_the_box():
    window = seamarks.aid_window((0.0, 60.0, 1.0, 61.0))
    mile = 1852 / 111320
    assert window[1] == 60.0 - mile and window[3] == 61.0 + mile
    assert abs(window[0] + mile / 0.4848) < 1e-3  # cos 61 degrees


def test_with_seamarks_the_shom_aids_are_fetched_a_mile_past_the_region():
    from odc import sources

    region = {'bbox': [-1.0, 49.0, 0.0, 50.0], 'sources': ['shom-wfs']}
    plain = sources.wfs_bounds(region)
    wider = sources.wfs_bounds({**region, 'sources': ['shom-wfs', 'openseamap']})
    assert plain == sources.fetch_bounds(region)
    assert wider == seamarks.aid_window(plain)


def test_a_shom_aid_past_the_build_box_still_takes_the_place_of_an_osm_aid(tmp_path):
    mile = 1852 / 111320
    shom = {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [-0.5 * mile, 0.5]}}
    wreck = {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [-0.5 * mile, 0.6]}}
    (tmp_path / 'BALISAGE_BDD_WFS__boylat.json').write_text(json.dumps({'features': [shom]}))
    (tmp_path / 'EPAVES_BDD_WFS__wrecks.json').write_text(json.dumps({'features': [wreck]}))
    classes, attributes = build_unit.load_s57_tables()
    charted = build_unit.shom_aids_near(str(tmp_path), attributes, classes, (0.0, 0.0, 1.0, 1.0))
    items = [
        ({'seamark:type': 'buoy_lateral'}, node(0.1 * mile, 0.5)),
        ({'seamark:type': 'buoy_lateral'}, node(0.1 * mile, 0.6)),
    ]
    kept = seamarks.seamark_layers(items, BBOX, 'OSM', charted)['BOYLAT']
    assert [round(g.GetY(), 1) for g, _, _ in kept] == [0.6]
