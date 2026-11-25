import json

import pytest

pytest.importorskip('osgeo')
from osgeo import ogr  # noqa: E402

from odc import chart  # noqa: E402
from odc.build_unit import bbox_polygon, chart_quality, land_mask, uncharted_water, write_layers  # noqa: E402
from odc.grid import Unit  # noqa: E402

BOX = (174.0, -37.0, 175.0, -36.0)


def save(chart_dir, band, objl, layer_id, features):
    path = chart_dir / chart.file_name(band, objl, layer_id)
    path.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))


def feature(geometry, **props):
    return {'type': 'Feature', 'id': f'f{len(props)}', 'geometry': geometry, 'properties': props}


def square(x0, y0, x1, y1):
    return {'type': 'Polygon', 'coordinates': [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_file_names_round_trip():
    name = chart.file_name(3, 'DEPARE', '51234')
    assert chart.parse_file_name(f'/inputs/chart/{name}') == (3, 'DEPARE', '51234')


def test_every_listed_layer_is_a_known_class_in_a_known_band():
    from odc.build_unit import load_s57_tables

    classes, _ = load_s57_tables()
    layers = chart.linz_layers()
    assert len({layer_id for layer_id, _, _ in layers}) == len(layers)
    for _, objl, band in layers:
        assert objl in classes
        assert 0 <= band < len(chart.BAND_MINZOOM)


def test_an_object_gives_way_where_a_larger_scale_has_coverage():
    areas = [None] * len(chart.BAND_MINZOOM)
    areas[3] = bbox_polygon(174.0, -37.0, 174.5, -36.0)
    geom = bbox_polygon(174.0, -37.0, 175.0, -36.0)
    parts = list(chart.zoom_parts(geom, 2, areas))
    assert [maxzoom for _, maxzoom in parts] == [chart.BAND_MINZOOM[3] - 1, None]
    inside, rest = parts[0][0], parts[1][0]
    assert inside.GetEnvelope() == pytest.approx((174.0, 174.5, -37.0, -36.0))
    assert rest.GetEnvelope() == pytest.approx((174.5, 175.0, -37.0, -36.0))


def test_a_larger_scale_from_the_same_zoom_takes_the_place_entirely():
    areas = [None] * len(chart.BAND_MINZOOM)
    areas[1] = bbox_polygon(*BOX)  # bands 0 and 1 both start at zoom 9
    assert list(chart.zoom_parts(bbox_polygon(*BOX), 0, areas)) == []


def test_chart_layers_take_coverage_and_zooms_from_the_bands(tmp_path):
    save(tmp_path, 1, 'M_COVR', '1', [feature(square(*BOX), catcov=1)])
    save(tmp_path, 3, 'M_COVR', '2', [feature(square(174.0, -37.0, 174.5, -36.0), catcov=1)])
    save(tmp_path, 3, 'M_COVR', '3', [feature(square(174.5, -37.0, 175.0, -36.0), catcov=2)])
    save(tmp_path, 1, 'DEPARE', '4', [feature(square(*BOX), drval1=10.0, drval2=20.0)])
    sounding = {'type': 'Point', 'coordinates': [174.2, -36.5]}
    save(tmp_path, 3, 'SOUNDG', '5', [feature(sounding, depth=12.3, quasou='1')])
    bbox_geom = bbox_polygon(*BOX)
    areas = chart.coverage(str(tmp_path), bbox_geom)
    assert areas[0] is None and areas[2] is None and areas[4] is None
    assert areas[3].GetEnvelope() == pytest.approx((174.0, 174.5, -37.0, -36.0))

    def upper(props):
        return {k.upper(): v for k, v in props.items() if k != 'depth'}

    layers = chart.chart_layers(str(tmp_path), upper, {'DEPARE': 42, 'SOUNDG': 129}, bbox_geom, areas)
    assert 'M_COVR' not in layers
    depare = sorted(layers['DEPARE'], key=lambda item: item[0].GetEnvelope()[0])
    assert [attrs for _, attrs, _ in depare] == [
        {'DRVAL1': 10.0, 'DRVAL2': 20.0, '_maxzoom': chart.BAND_MINZOOM[3] - 1},
        {'DRVAL1': 10.0, 'DRVAL2': 20.0},
    ]
    [(point, attrs, _)] = layers['SOUNDG']
    assert attrs == {'QUASOU': '1', 'DEPTH': 12.3, '_minzoom': chart.BAND_MINZOOM[3]}


def test_only_coastal_coverage_makes_a_unit_charted(tmp_path):
    save(tmp_path, 1, 'M_COVR', '1', [feature(square(170.0, -40.0, 180.0, -30.0), catcov=1)])
    chart.coverage_index.cache_clear()
    assert not chart.charted(str(tmp_path), BOX)
    save(tmp_path, 2, 'M_COVR', '2', [feature(square(174.4, -36.6, 174.6, -36.4), catcov=1)])
    chart.coverage_index.cache_clear()
    assert chart.charted(str(tmp_path), BOX)
    assert not chart.charted(str(tmp_path), (176.0, -37.0, 177.0, -36.0))


def test_chart_quality_is_the_share_of_water_a_chart_covers():
    unit = Unit(504, 316)
    box = unit.build_box
    minx, miny, maxx, maxy = unit.bounds
    land = bbox_polygon(*box.bbox).Difference(bbox_polygon(minx, miny, maxx, maxy))
    land_cells = land_mask(land, box.bbox)
    half = bbox_polygon(minx, miny, (minx + maxx) / 2, maxy)
    quality = chart_quality([None, half, None, None, None], land_cells, box, unit)
    assert quality['best'] == 'chart'
    assert quality['share'] == pytest.approx({'chart': 0.5, 'none': 0.5}, abs=0.01)
    assert chart_quality([None] * 5, land_cells, box, unit) == {'best': 'none', 'share': {'none': 1.0}}


def test_written_features_carry_both_zooms(tmp_path):
    unit = Unit(504, 316)
    minx, miny, maxx, maxy = unit.bounds
    area = bbox_polygon(minx, miny, maxx, maxy)
    write_layers(str(tmp_path), {'DEPARE': [(area, {'_minzoom': 11, '_maxzoom': 12}, 'x')]}, {'DEPARE': 42}, unit)
    [feat] = json.loads((tmp_path / 'DEPARE.geojson').read_text())['features']
    assert feat['tippecanoe'] == {'minzoom': 11, 'maxzoom': 12}
    assert '_maxzoom' not in feat['properties']
    assert ogr.CreateGeometryFromJson(json.dumps(feat['geometry'])).GetArea() > 0


def test_water_no_chart_covers_is_unsurveyed():
    water = bbox_polygon(174.0, -37.0, 175.0, -36.0)
    half = bbox_polygon(174.0, -37.0, 174.5, -36.0)
    unsare = uncharted_water(water, [None, half, None, None, None])
    assert len(unsare) == 1 and unsare[0][0].GetArea() == pytest.approx(0.5)
    assert uncharted_water(water, [water] + [None] * 4) == []


def test_a_unit_reads_only_the_squares_it_meets(tmp_path):
    for west in (24, 25, 26):
        save(tmp_path, 1, 'DEPCNT', f'traficom-DepthContour_L{chart.square_name(west, 60)}', [])
    save(tmp_path, 1, 'DEPCNT', '4', [])
    names = [chart.parse_file_name(p)[2] for p in chart.chart_files(str(tmp_path), (25.2, 60.1, 25.8, 60.4))]
    assert names == ['4', 'traficom-DepthContour_L-e25n60']
    assert len(chart.chart_files(str(tmp_path))) == 4


def test_traficom_depth_areas_are_its_coverage_and_chart_lake_units(tmp_path):
    lake = (28.0, 61.2, 28.5, 61.5)
    save(tmp_path, 1, 'DEPCNT', 'traficom-DepthContour_L-e28n61', [feature(square(28.0, 61.0, 29.0, 62.0))])
    chart.coverage_index.cache_clear()
    assert not chart.charted(str(tmp_path), lake, depth_only=True)
    save(tmp_path, 1, 'M_COVR', '1', [feature(square(28.0, 61.0, 29.0, 62.0), catcov=1)])
    save(tmp_path, 2, 'M_COVR', '2', [feature(square(28.0, 61.0, 29.0, 62.0), catcov=1)])
    chart.coverage_index.cache_clear()
    assert chart.charted(str(tmp_path), lake)
    assert not chart.charted(str(tmp_path), lake, depth_only=True)
    save(tmp_path, 1, 'DEPARE', 'traficom-DepthArea_A-e28n61', [feature(square(28.1, 61.3, 28.2, 61.4), drval1=2.0)])
    chart.coverage_index.cache_clear()
    assert chart.charted(str(tmp_path), lake, depth_only=True)
    assert not chart.charted(str(tmp_path), (28.6, 61.2, 28.9, 61.5), depth_only=True)
    areas = chart.coverage(str(tmp_path), bbox_polygon(*lake))
    assert areas[1].GetArea() == pytest.approx(0.15)  # the whole lake box, M_COVR and depth area merged


def test_single_scale_soundings_are_thinned_per_zoom(tmp_path):
    from odc.build_unit import thin_chart_soundings

    point = {'type': 'Point', 'coordinates': [24.5, 59.85]}
    near = {'type': 'Point', 'coordinates': [24.503, 59.853]}
    save(
        tmp_path,
        1,
        'SOUNDG',
        'traficom-Sounding_P-e24n59',
        [feature(point, depth=49.0, quasou='1'), feature(near, depth=12.0)],
    )
    box = bbox_polygon(24.0, 59.5, 25.0, 60.0)
    areas = chart.coverage(str(tmp_path), box)
    layers = chart.chart_layers(
        str(tmp_path), lambda p: {k.upper(): v for k, v in p.items()}, {'SOUNDG': 129}, box, areas
    )
    soundings = thin_chart_soundings(layers['SOUNDG'], 17)
    by_depth = {attrs['DEPTH']: attrs for _, attrs, _ in soundings}
    assert '_thin' not in by_depth[12.0]
    assert by_depth[12.0]['_minzoom'] < by_depth[49.0]['_minzoom']  # the shoaler first
    assert by_depth[49.0]['QUASOU'] == '1'
