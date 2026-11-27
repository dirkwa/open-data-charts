import json

import pytest
from osgeo import ogr

from odc import build_unit, human_activities, seamarks

BBOX = build_unit.bbox_polygon(0.0, 0.0, 1.0, 1.0)


def point(lon, lat):
    return {'type': 'Point', 'coordinates': [lon, lat]}


def square(x0, y0, x1, y1):
    return {'type': 'Polygon', 'coordinates': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def write(directory, layer, *features):
    collection = {
        'type': 'FeatureCollection',
        'features': [
            {'type': 'Feature', 'id': f'{layer}.{i}', 'geometry': geometry, 'properties': properties}
            for i, (geometry, properties) in enumerate(features)
        ],
    }
    (directory / f'{layer}.json').write_text(json.dumps(collection))


def test_only_wind_farms_in_the_water_are_charted(tmp_path):
    write(
        tmp_path,
        'windfarmspoly',
        (square(0.1, 0.1, 0.2, 0.2), {'name': 'Baltic 2', 'status': 'Production'}),
        (square(0.3, 0.3, 0.4, 0.4), {'name': 'Building', 'status': 'Construction'}),
        (square(0.5, 0.5, 0.6, 0.6), {'name': 'Someday', 'status': 'Planned'}),
        (square(0.7, 0.7, 0.8, 0.8), {'name': 'Gone', 'status': 'Dismantled'}),
    )
    farms = human_activities.layers(tmp_path, BBOX, {})['PRDARE']
    assert [attrs for _, attrs, _ in farms] == [
        {'CATPRA': '9', 'OBJNAM': 'Baltic 2'},
        {'CATPRA': '9', 'OBJNAM': 'Building', 'CONDTN': 1},
    ]


def test_a_wreck_keeps_its_category_and_its_depth_only_on_a_low_water_datum(tmp_path):
    known = {'depth_qual': 'least depth known', 'depth': '12', 'water_leve': 'always under water/submerged'}
    write(
        tmp_path,
        'wwshipwrecks',
        (point(0.1, 0.1), {**known, 'wreck_cate': 'Dangerous wreck', 'vertical_d': 'Lowest Astronomical Tide'}),
        (point(0.2, 0.2), {**known, 'wreck_cate': 'Dangerous wreck', 'vertical_d': 'Mean Sea Level'}),
        (point(0.3, 0.3), {**known, 'wreck_cate': 'Non-dangerous wreck', 'status': 'lifted'}),
    )
    wrecks = [attrs for _, attrs, _ in human_activities.layers(tmp_path, BBOX, {})['WRECKS']]
    assert wrecks == [
        {'CATWRK': '2', 'WATLEV': 3, 'VALSOU': 12},
        {'CATWRK': '2', 'WATLEV': 3},
    ]


def test_what_another_source_charts_stands_and_is_cut_out(tmp_path):
    write(
        tmp_path,
        'wwshipwrecks',
        (point(0.1, 0.1), {'wreck_cate': 'Dangerous wreck'}),
        (point(0.5, 0.5), {'wreck_cate': 'Dangerous wreck'}),
    )
    write(
        tmp_path,
        'windfarmspoly',
        (square(0.1, 0.1, 0.2, 0.2), {'status': 'Production'}),
        (square(0.4, 0.1, 0.6, 0.2), {'status': 'Production'}),
    )
    charted = {
        # About 110 m from the first wreck.
        'WRECKS': [(ogr.CreateGeometryFromWkt('POINT (0.101 0.1)'), {}, None)],
        'PRDARE': [
            (ogr.CreateGeometryFromJson(json.dumps(square(0.1, 0.1, 0.2, 0.2))), {}, None),
            (ogr.CreateGeometryFromJson(json.dumps(square(0.5, 0.1, 0.7, 0.2))), {}, None),
        ],
    }
    found = human_activities.layers(tmp_path, BBOX, charted)
    assert [(g.GetX(), g.GetY()) for g, _, _ in found['WRECKS']] == [(0.5, 0.5)]
    ((rest, _, _),) = found['PRDARE']
    assert abs(rest.GetArea() - (0.5 - seamarks.SAME_OUTLINE_DEG - 0.4) * 0.1) < 1e-9


def test_osm_wrecks_near_a_charted_one_are_left_out():
    charted = [(ogr.CreateGeometryFromWkt('POINT (0.1 0.1)'), {}, None)]
    osm = [
        (ogr.CreateGeometryFromWkt('POINT (0.1005 0.1)'), {'INFORM': 'OSM'}, None),
        (ogr.CreateGeometryFromWkt('POINT (0.2 0.1)'), {'INFORM': 'OSM'}, None),
    ]
    kept = human_activities.not_charted_wrecks(osm, charted)
    assert [g.GetX() for g, _, _ in kept] == [0.2]


def test_cables_and_pipelines_are_cut_to_the_box_and_only_laid_ones_charted(tmp_path):
    line = {'type': 'LineString', 'coordinates': [[0.5, 0.5], [2.0, 0.5]]}
    write(
        tmp_path,
        'pcablesbshcontis',
        (line, {'status': 'inUse', 'name_': 'Baltic Cable'}),
        (line, {'status': 'UnderConstruction'}),
        (line, {'status': 'Planned'}),
        (line, {'status': 'application submitted'}),
    )
    write(
        tmp_path,
        'pipelines',
        (line, {'status': 'Active', 'name': 'Nord Stream'}),
        (line, {'status': 'Under construction'}),
        (line, {'status': 'planned'}),
        (line, {'status': 'Proposed'}),
        (line, {'status': 'Dismantled'}),
        (line, {'status': 'Abandoned', 'name': 'Old line'}),
    )
    found = human_activities.layers(tmp_path, BBOX, {})
    (cable, attrs, _), (_, building, _) = found['CBLSUB']
    assert attrs == {'CATCBL': '1', 'OBJNAM': 'Baltic Cable'} and abs(cable.Length() - 0.5) < 1e-9
    assert building == {'CATCBL': '1', 'CONDTN': 1}
    assert [attrs for _, attrs, _ in found['PIPSOL']] == [
        {'OBJNAM': 'Nord Stream'},
        {'CONDTN': 1},
        {'OBJNAM': 'Old line'},
    ]


def test_platforms_under_construction_say_so_and_disused_ones_stay(tmp_path):
    write(
        tmp_path,
        'platforms',
        (point(0.1, 0.1), {'category': 'Subsea steel', 'current_status': 'Under construction'}),
        (point(0.2, 0.1), {'category': 'Subsea steel', 'current_status': 'Operational'}),
        (point(0.3, 0.1), {'category': 'Fixed steel', 'current_status': 'Under construction'}),
        (point(0.4, 0.1), {'category': 'Fixed steel', 'current_status': 'Decommissioned'}),
    )
    found = human_activities.layers(tmp_path, BBOX, {})
    assert [a.get('CONDTN') for _, a, _ in found['OBSTRN']] == [1, None]
    assert [a.get('CONDTN') for _, a, _ in found['OFSPLF']] == [1, None]


def test_military_areas_in_force_and_munitions_dumps(tmp_path):
    write(
        tmp_path,
        'militaryareaspoly',
        (square(0.1, 0.1, 0.2, 0.2), {'status': 'Active', 'type_1': 'Firing Area'}),
        (square(0.3, 0.3, 0.4, 0.4), {'status': 'Planned', 'type_1': 'Firing Area'}),
    )
    write(tmp_path, 'munitions', (point(0.6, 0.6), {'munition_type': 'Chemical', 'description': None}))
    found = human_activities.layers(tmp_path, BBOX, {})
    assert [a for _, a, _ in found['MIPARE']] == [{'CATMPA': '4', 'INFORM': 'Firing Area'}]
    assert [a for _, a, _ in found['DMPGRD']] == [{'CATDPG': '4', 'INFORM': 'Chemical'}]


def test_a_layer_the_service_answers_in_part_fails_the_fetch(tmp_path):
    def get_json(url):
        return {'features': [], 'numberMatched': 3}

    with pytest.raises(RuntimeError, match='0 of 3'):
        human_activities.fetch((0, 0, 1, 1), str(tmp_path), get_json)


def test_a_layer_answered_without_a_total_fails_the_fetch(tmp_path):
    for page in ({'features': []}, {'features': [], 'numberMatched': 'unknown'}):
        with pytest.raises(RuntimeError):
            human_activities.fetch((0, 0, 1, 1), str(tmp_path), lambda url, page=page: page)


def test_the_fetch_asks_for_the_box_in_lat_lon_order(tmp_path):
    urls = []

    def get_json(url):
        urls.append(url)
        return {'features': [], 'numberMatched': 0}

    human_activities.fetch((10.5, 54.0, 11.0, 54.5), str(tmp_path), get_json)
    assert len(urls) == len(human_activities.LAYERS)
    assert 'bbox=54.0%2C10.5%2C54.5%2C11.0' in urls[0]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(f'{layer}.json' for layer in human_activities.LAYERS)
