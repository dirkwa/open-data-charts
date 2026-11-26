import pytest

from odc import build_unit, vta_his

ROCK = """<wfs:featureMember>
    <his:kivi gml:id="kivi_1294532">
        <his:id>1294532</his:id>
        <his:sygavus>3.54</his:sygavus>
        <his:watlev_id>3</his:watlev_id>
        <his:catobs_id>999</his:catobs_id>
        <his:koord><gml:Point srsName="urn:ogc:def:crs:EPSG::3301">
            <gml:pos srsDimension="2">6590820.25 541370.75</gml:pos>
        </gml:Point></his:koord>
        <his:naita>0</his:naita>
    </his:kivi>
</wfs:featureMember>"""


def rock(ident, northing, easting, depth):
    return (
        ROCK.replace('kivi_1294532', ident)
        .replace('6590820.25 541370.75', f'{northing} {easting}')
        .replace('<his:sygavus>3.54', f'<his:sygavus>{depth}')
    )


NAMESPACES = (
    'xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml"'
    ' xmlns:his="https://his.vta.ee:8443/HIS/WFS"'
)


def answer(*members):
    return f'<wfs:FeatureCollection {NAMESPACES}>' + ''.join(members) + '</wfs:FeatureCollection>'


def test_an_answer_gives_each_object_easting_first():
    [(ident, x, y, fields)] = vta_his.parse(answer(ROCK))
    assert (ident, x, y) == ('kivi_1294532', 541370.75, 6590820.25)
    assert fields['sygavus'] == '3.54' and fields['catobs_id'] == '999'


def test_rocks_keep_their_depth_and_drop_unknown_codes():
    [(_, _, _, fields)] = vta_his.parse(answer(ROCK))
    assert vta_his.s57('kivi', fields) == ('UWTROC', {'VALSOU': 3.54, 'WATLEV': 3})


def test_rocks_too_deep_for_any_keel_are_left_out():
    [(_, _, _, fields)] = vta_his.parse(answer(rock('kivi_1', 6590820.25, 541370.75, 12.5)))
    assert vta_his.s57('kivi', fields) is None


def test_wrecks_keep_their_name_unless_the_database_numbers_them():
    named = {'vraki_sygavus': '70.46', 'watlev_id': '3', 'catwrk_id': '2', 'laevanimi': 'KRAB'}
    nameless = {**named, 'laevanimi': 'NIMETU-338'}
    assert vta_his.s57('vrakk', named) == ('WRECKS', {'VALSOU': 70.46, 'WATLEV': 3, 'CATWRK': 2, 'OBJNAM': 'KRAB'})
    assert 'OBJNAM' not in vta_his.s57('vrakk', nameless)[1]
    assert vta_his.s57('vrakk', {'vraki_sygavus': '', 'watlev_id': '3'}) == ('WRECKS', {'WATLEV': 3})


def test_obstructions_carry_their_category_and_description():
    fields = {'sygavus': '2.32', 'watlev_id': '3', 'catobs_id': '6', 'kirjeldus': 'kivihunnik'}
    attrs = {'VALSOU': 2.32, 'WATLEV': 3, 'CATOBS': 6, 'INFORM': 'kivihunnik'}
    assert vta_his.s57('takist', fields) == ('OBSTRN', attrs)


def test_a_full_box_is_asked_again_in_quarters(monkeypatch):
    monkeypatch.setattr(vta_his, 'HIS_CAP', 2)
    rocks = [('kivi_1', 10.0, 10.0), ('kivi_2', 30.0, 10.0), ('kivi_3', 30.0, 30.0)]
    asked = []

    def get(layer, box):
        asked.append(box)
        minx, miny, maxx, maxy = box
        inside = [rock(i, y, x, 1.0) for i, x, y in rocks if minx <= x <= maxx and miny <= y <= maxy]
        return answer(*inside)

    found = vta_his.layer_objects('kivi', (0.0, 0.0, 40.0, 40.0), get)
    assert sorted(found) == ['kivi_1', 'kivi_2', 'kivi_3']
    assert asked[0] == (0.0, 0.0, 40.0, 40.0) and len(asked) == 5


def test_an_answer_that_is_not_a_whole_collection_fails():
    box = (0.0, 0.0, 40.0, 40.0)
    assert vta_his.layer_objects('kivi', box, lambda layer, box: '<?xml version="1.0"?>\n' + answer()) == {}
    assert vta_his.layer_objects('kivi', box, lambda layer, box: f'<wfs:FeatureCollection {NAMESPACES}/>') == {}
    unclosed = answer(ROCK.replace('</wfs:featureMember>', ''))
    exception = '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1">busy</ows:ExceptionReport>'
    for body in (exception, answer(ROCK)[:-10], unclosed):
        with pytest.raises(RuntimeError):
            vta_his.layer_objects('kivi', box, lambda layer, box, body=body: body)


def test_the_region_s_objects_come_as_points_inside_its_bounds():
    tallinn = (541370.75, 6590820.25)

    def get(layer, box):
        minx, miny, maxx, maxy = box
        if layer != 'kivi' or not (minx <= tallinn[0] <= maxx and miny <= tallinn[1] <= maxy):
            return answer()
        return answer(ROCK)

    [feature] = vta_his.features((24.0, 59.0, 25.5, 59.6), get)
    lon, lat = feature['geometry']['coordinates']
    assert 24.7 < lon < 24.8 and 59.4 < lat < 59.5
    assert feature['id'] == 'vta-his-kivi_1294532'
    assert feature['properties'] == {'OBJL': 'UWTROC', 'VALSOU': 3.54, 'WATLEV': 3}
    assert vta_his.features((10.0, 50.0, 11.0, 51.0), get) == []


def test_a_unit_shows_the_shoalest_rock_first(tmp_path):
    path = str(tmp_path / 'his.geojsonseq')
    features = [
        {
            'type': 'Feature',
            'id': f'vta-his-kivi_{i}',
            'geometry': {'type': 'Point', 'coordinates': [24.75 + i * 1e-5, 59.45]},
            'properties': {'OBJL': 'UWTROC', 'VALSOU': depth},
        }
        for i, depth in enumerate([4.0, 1.5, 3.0])
    ] + [
        {
            'type': 'Feature',
            'id': 'vta-his-vrakk_6',
            'geometry': {'type': 'Point', 'coordinates': [24.76, 59.45]},
            'properties': {'OBJL': 'WRECKS', 'VALSOU': 7.0},
        }
    ]
    vta_his.write(path, features)
    layers = build_unit.his_layers(path, (24.7, 59.4, 24.8, 59.5), 17)
    first = min(layers['UWTROC'], key=lambda item: item[1]['_minzoom'])
    assert first[1]['VALSOU'] == 1.5 and first[1]['_minzoom'] == build_unit.SOUNDING_MINZOOM
    assert [attrs for _, attrs, _ in layers['WRECKS']] == [{'VALSOU': 7.0}]
