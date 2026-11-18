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
