import json

import numpy as np
import pytest

pytest.importorskip('osgeo')
from osgeo import ogr  # noqa: E402

from odc.build_unit import (  # noqa: E402
    CONTOUR_WINDOW_CELLS,
    ETOPO_PIXEL_DEG,
    GRID_STEP,
    MODEL_HOLE,
    MODEL_INFORM,
    MODEL_SOUNDING_MAXZOOM,
    SOUNDING_MINZOOM,
    bbox_polygon,
    build_reefs,
    build_soundings,
    deep_water,
    depth_quality,
    dilate_disk,
    linestring,
    model_soundings,
    prepare_dem,
    sounding_squares,
    submerge_reefs,
    thin_soundings,
    window_edges,
)
from odc.grid import Unit  # noqa: E402


def test_contour_windows_sit_on_global_grid_lines():
    # These neighbours' boxes overlap across a window boundary (ix 2829312).
    a, b = Unit(502, 288).build_box, Unit(503, 288).build_box
    edges_a = {a.ix0 + e for e in window_edges(a.ix0, a.ncols)}
    edges_b = {b.ix0 + e for e in window_edges(b.ix0, b.ncols)}
    shared = {e for e in edges_a & edges_b if e % CONTOUR_WINDOW_CELLS == 0}
    overlap = range(b.ix0, a.ix1)
    expected = {e for e in overlap if e % CONTOUR_WINDOW_CELLS == 0 and b.ix0 < e < a.ix1 - 1}
    assert expected
    assert shared == expected


def test_window_edges_include_box_ends():
    edges = window_edges(1000, 2000)
    assert edges[0] == 0 and edges[-1] == 1999
    assert all((1000 + e) % CONTOUR_WINDOW_CELLS == 0 for e in edges[1:-1])


def test_sounding_squares_nest_inside_tiles():
    rng = np.random.default_rng(1)
    lon = rng.uniform(165.94, 166.64, 2000)
    lat = rng.uniform(-22.59, -21.95, 2000)
    for zoom in range(SOUNDING_MINZOOM, 16):
        fine = sounding_squares(lon, lat, zoom + 1)
        coarse = sounding_squares(lon, lat, zoom)
        pairs = {}
        for f, c in zip(fine, coarse, strict=True):
            assert pairs.setdefault(f, c) == c


def test_a_unit_thins_its_soundings_as_a_bigger_area_would():
    rng = np.random.default_rng(2)
    a, b = Unit(492, 288), Unit(493, 288)
    lon = rng.uniform(a.bounds[0], b.bounds[2], 20000)
    lat = rng.uniform(a.bounds[1], a.bounds[3], 20000)
    z = rng.uniform(0, 50, 20000).astype(np.float32)
    together = thin_soundings(lon, lat, z, 15)
    mine = a.owns(lon, lat)
    alone = thin_soundings(lon[mine], lat[mine], z[mine], 15)
    assert np.array_equal(together[mine], alone)


def test_write_layers_replaces_only_its_own_layers(tmp_path):
    from odc.build_unit import write_layers

    (tmp_path / 'SOUNDG.geojson').write_text('{}')
    (tmp_path / 'structures.geojson').write_text('{}')
    write_layers(str(tmp_path), {}, {'SOUNDG': 129}, Unit(492, 288))
    assert not (tmp_path / 'SOUNDG.geojson').exists()
    assert (tmp_path / 'structures.geojson').exists()


def test_a_measured_cell_counts_for_the_survey_that_sounded_it():
    unit = Unit(492, 288)
    box = unit.build_box
    r, c = unit.cells.iy0 - box.iy0 + 100, unit.cells.ix0 - box.ix0 + 100
    rank = np.full((box.nrows, box.ncols), -1, np.int16)
    rank[r : r + 100, c : c + 50] = 0
    rank[r : r + 100, c + 50 : c + 100] = 1
    land = np.ones_like(rank, bool)
    land[r : r + 100, c : c + 100] = False
    entries = [
        (None, None, 'A', None, {'end': '20100101', 'tecsou': 'multi-beam echosounder'}),
        (None, None, 'B', None, {'end': '20100101', 'tecsou': 'single-beam echosounder'}),
    ]
    quality, used = depth_quality(entries, rank, rank >= 0, rank >= 0, land, box, unit)
    assert quality['share'] == {'multibeam': 0.5, 'survey-recent': 0.5}
    assert used == [0, 1]


def test_water_without_a_survey_or_model_depth_is_not_model():
    unit = Unit(492, 288)
    box = unit.build_box
    r, c = unit.cells.iy0 - box.iy0 + 100, unit.cells.ix0 - box.ix0 + 100
    land = np.ones((box.nrows, box.ncols), bool)
    land[r : r + 100, c : c + 100] = False
    from_model = np.zeros_like(land)
    from_model[r : r + 100, c : c + 25] = True
    quality, used = depth_quality([], None, None, from_model, land, box, unit)
    assert quality == {'best': 'model', 'share': {'model': 0.25, 'none': 0.75}}
    assert used == []
    quality, _ = depth_quality([], None, None, np.zeros_like(land), land, box, unit)
    assert quality == {'best': 'none', 'share': {'none': 1.0}}


def test_dilate_disk_reaches_exactly_the_euclidean_radius():
    mask = np.zeros((41, 43), bool)
    mask[20, 21] = True
    mask[3, 40] = True
    rows, cols = np.indices(mask.shape)
    expected = np.zeros_like(mask)
    for r, c in zip(*np.nonzero(mask), strict=True):
        expected |= (rows - r) ** 2 + (cols - c) ** 2 <= 12**2
    assert (dilate_disk(mask, 12) == expected).all()


def etopo_tile(tmp_path, z):
    from osgeo import gdal

    path = str(tmp_path / 'etopo.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, z.shape[1], z.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((-145.0, ETOPO_PIXEL_DEG, 0, -16.0, 0, -ETOPO_PIXEL_DEG))
    ds.GetRasterBand(1).SetNoDataValue(-99999)
    ds.GetRasterBand(1).WriteArray(z)
    ds = None
    return [path]


def test_deep_water_keeps_clear_of_anything_shallower(tmp_path):
    z = np.full((120, 120), -1000.0, np.float32)
    reef_row, reef_col = 90, 30
    z[reef_row, reef_col] = -50.0
    bbox = (-144.9, -16.4, -144.85, -16.35)
    depth = deep_water(etopo_tile(tmp_path, z), bbox)
    reef_lon = -145.0 + (reef_col + 0.5) * ETOPO_PIXEL_DEG
    reef_lat = -16.0 - (reef_row + 0.5) * ETOPO_PIXEL_DEG
    assert bbox[0] < reef_lon < bbox[2] and bbox[1] < reef_lat < bbox[3]
    col = int((reef_lon - bbox[0]) / GRID_STEP)
    row = int((bbox[3] - reef_lat) / GRID_STEP)
    clear = int(2.5 * ETOPO_PIXEL_DEG / GRID_STEP)
    around = depth[row - clear + 1 : row + clear, col - clear + 1 : col + clear]
    assert around.shape == (2 * clear - 1, 2 * clear - 1)
    assert np.isnan(around).all()
    assert np.isfinite(depth[0, -1])
    assert np.nanmin(depth) > 900


def test_neighbouring_boxes_agree_on_deep_water(tmp_path):
    rng = np.random.default_rng(3)
    z = rng.uniform(-3000, -100, (120, 120)).astype(np.float32)
    paths = etopo_tile(tmp_path, z)
    a = (-144.9, -16.4, -144.85, -16.35)
    b = (-144.875, -16.4, -144.825, -16.35)
    da, db = deep_water(paths, a), deep_water(paths, b)
    shift = round((b[0] - a[0]) / GRID_STEP)
    np.testing.assert_allclose(da[:, shift:], db[:, : da.shape[1] - shift], atol=0.01)


def test_deep_water_counts_as_coarse_model():
    unit = Unit.parse('z9-50-280')
    box = unit.build_box
    land = np.zeros((box.nrows, box.ncols), bool)
    from_deep = np.ones_like(land)
    quality, _ = depth_quality([], None, None, np.zeros_like(land), land, box, unit, from_deep)
    assert quality == {'best': 'model-coarse', 'share': {'model-coarse': 1.0}}


def square(x, y, size=0.001):
    return {'type': 'Polygon', 'coordinates': [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]}


def write_aca(tmp_path, features, name='reefs_-17_-145.geojsonseq'):
    aca = tmp_path / 'aca'
    aca.mkdir(exist_ok=True)
    with open(aca / name, 'a', encoding='utf-8') as fh:
        for fid, cls, geometry in features:
            fh.write(
                json.dumps({'type': 'Feature', 'id': fid, 'properties': {'class_name': cls}, 'geometry': geometry})
            )
            fh.write('\n')
    return str(aca)


def test_coral_atlas_reefs_merge_per_class_and_count_once(tmp_path):
    box = bbox_polygon(-144.9, -16.9, -144.8, -16.8)
    crest = [('a', 'Reef Crest', square(-144.85, -16.85)), ('b', 'Reef Crest', square(-144.849, -16.85))]
    aca = write_aca(tmp_path, [*crest, ('c', 'Deep Lagoon', square(-144.86, -16.86))])
    write_aca(tmp_path, crest[:1], 'reefs_-17_-146.geojsonseq')  # the same feature seen from a neighbouring square
    items = build_reefs(None, aca, [], [], box)
    assert len(items) == 1
    geom, attrs, _ = items[0]
    assert attrs['WATLEV'] == '4' and attrs['INFORM'].startswith('Reef Crest: Allen Coral Atlas')
    assert abs(geom.GetArea() - 2e-6) < 1e-8


def test_the_reef_map_wins_over_the_coral_atlas(tmp_path):
    box = bbox_polygon(-144.9, -16.9, -144.8, -16.8)
    aca = write_aca(tmp_path, [('a', 'Inner Reef Flat', square(-144.85, -16.85, 0.002))])
    reef_map = tmp_path / 'reefs.geojson'
    mapped = {'reef': 1, 'l4_attrib': 'reef flat', 'rb_attrib': 'x'}
    reef_map.write_text(
        json.dumps({'features': [{'type': 'Feature', 'properties': mapped, 'geometry': square(-144.85, -16.85)}]})
    )
    items = build_reefs(str(reef_map), aca, [], [], box)
    areas = sorted((a['INFORM'].split(':')[0], round(g.GetArea() * 1e6, 3)) for g, a, _ in items)
    assert areas == [('Inner Reef Flat', 3.0), ('reef flat, x', 1.0)]


def test_an_osm_reef_keeps_only_what_the_maps_leave_out(tmp_path):
    box = bbox_polygon(-144.9, -16.9, -144.8, -16.8)
    aca = write_aca(tmp_path, [('a', 'Inner Reef Flat', square(-144.85, -16.85, 0.002))])
    long_reef = {
        'type': 'Polygon',
        'coordinates': [
            [[-144.85, -16.85], [-144.84, -16.85], [-144.84, -16.848], [-144.85, -16.848], [-144.85, -16.85]]
        ],
    }
    covered = square(-144.8495, -16.8495)
    osm = [
        (ogr.CreateGeometryFromJson(json.dumps(g)), {'OBJNAM': n})
        for n, g in (('long', long_reef), ('covered', covered))
    ]
    items = build_reefs(None, aca, osm, [], box)
    kept = [(a['OBJNAM'], round(g.GetArea() * 1e6, 3)) for g, a, _ in items if 'OBJNAM' in a]
    assert kept == [('long', round((0.01 - 0.0025) * 0.002 * 1e6, 3))]


def test_an_osm_reef_line_keeps_only_what_the_maps_leave_out(tmp_path):
    box = bbox_polygon(-144.9, -16.9, -144.8, -16.8)
    aca = write_aca(tmp_path, [('a', 'Inner Reef Flat', square(-144.85, -16.85, 0.002))])
    lines = {'long': [(-144.85, -16.849), (-144.84, -16.849)], 'covered': [(-144.8495, -16.849), (-144.8485, -16.849)]}
    osm = [(linestring(coords), {'OBJNAM': n}) for n, coords in lines.items()]
    items = build_reefs(None, aca, osm, [], box)
    kept = [(a['OBJNAM'], round(g.Length(), 6)) for g, a, _ in items if 'OBJNAM' in a]
    assert kept == [('long', 0.01 - 0.0025)]


def test_water_areas_behind_the_coastline_are_cut_out_of_the_land(tmp_path):
    from odc import build_unit

    ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(tmp_path / 'land.gpkg'))
    layer = ds.CreateLayer('land', geom_type=ogr.wkbPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkt('POLYGON((0 0,0.01 0,0.01 0.01,0 0.01,0 0))'))
    layer.CreateFeature(feature)
    ds = layer = None
    basin = ogr.CreateGeometryFromWkt('POLYGON((0.004 0.004,0.006 0.004,0.006 0.006,0.004 0.006,0.004 0.004))')
    box = bbox_polygon(-0.001, -0.001, 0.011, 0.011)
    land, coast, inland = build_unit.build_land(str(tmp_path / 'land.gpkg'), box, basin)
    assert land.GetArea() == pytest.approx(0.01**2 - 0.002**2)
    assert inland.GetArea() == pytest.approx(0.002**2)
    assert sum(line.Length() for line in coast) == pytest.approx(0.04 + 0.008)
    _, _, none = build_unit.build_land(str(tmp_path / 'land.gpkg'), box)
    assert none.IsEmpty()


def test_in_inland_water_the_model_keeps_only_depths(tmp_path):
    from osgeo import gdal

    from odc import build_unit

    bbox = (0.0, 0.0, 0.01, 0.01)
    ncols, nrows, gt = build_unit.chart_grid(bbox)
    heights = np.full((nrows, ncols), -3.0, np.float32)  # elevation, depth below datum negative
    heights[:, : ncols // 2] = 1.0
    dem = str(tmp_path / 'dem.tif')
    gdal.GetDriverByName('GTiff').CreateCopy(dem, build_unit.mem_raster(heights, gt))
    inland = np.zeros((nrows, ncols), bool)
    inland[: nrows // 2] = True
    path, _, _ = build_unit.build_depth_grid(dem, bbox, str(tmp_path), None, None, inland)
    z = gdal.Open(path).ReadAsArray()
    model = gdal.Open(str(tmp_path / 'dem_grid.tif')).ReadAsArray()
    top, bottom = slice(2, nrows // 2 - 2), slice(nrows // 2 + 2, nrows - 2)
    west, east = slice(2, ncols // 2 - 2), slice(ncols // 2 + 2, ncols - 2)
    assert (z[top, west] == build_unit.NODATA).all() and (model[top, west] == build_unit.NODATA).all()
    assert (z[top, east] == -3.0).all()
    assert (z[bottom, west] == 1.0).all()


def test_read_water_keeps_the_areas_meeting_the_box(tmp_path):
    from odc import osm

    def area(x, kind='Polygon'):
        ring = [[x, 0], [x + 0.001, 0], [x + 0.001, 0.001], [x, 0.001], [x, 0]]
        coordinates = [ring] if kind == 'Polygon' else [[ring]]
        return {'type': 'Feature', 'id': 'a2', 'properties': {}, 'geometry': {'type': kind, 'coordinates': coordinates}}

    line = {
        'type': 'Feature',
        'id': 'w1',
        'properties': {},
        'geometry': {'type': 'LineString', 'coordinates': [[0, 0], [1, 1]]},
    }
    path = tmp_path / 'water.geojsonseq'
    path.write_text(''.join(json.dumps(f) + '\n' for f in (area(0.0), area(0.002, 'MultiPolygon'), area(0.5), line)))
    water = osm.read_water(str(path), (0.0, 0.0, 0.01, 0.01))
    assert water.GetArea() == pytest.approx(2e-6)


def test_a_canal_narrower_than_a_cell_reaches_the_inland_mask_but_not_the_sea():
    from odc import build_unit

    step = GRID_STEP
    bbox = (0.0, 0.0, 40 * step, 40 * step)
    land = bbox_polygon(20 * step, 0.0, 40 * step, 40 * step)  # the sea is the west half
    # Half a cell wide, between two columns of cell centres, from the sea into the land.
    canal = bbox_polygon(10 * step, 19.1 * step, 30 * step, 19.4 * step)
    inland = canal.Intersection(land)
    mask = build_unit.inland_mask(land.Difference(canal), inland, bbox)
    row = 40 - 20  # rows run from the top
    assert mask[row, 21:30].all()
    assert not mask[row, :20].any()
    assert build_unit.land_mask(inland, bbox).sum() == 0
    assert build_unit.inland_mask(land, ogr.Geometry(ogr.wkbMultiPolygon), bbox) is None


def test_a_ditch_along_the_coast_is_unsurveyed_without_reaching_the_drying_sea():
    from odc import build_unit

    step = GRID_STEP
    bbox = (0.0, 0.0, 40 * step, 40 * step)
    land = bbox_polygon(20 * step, 0.0, 40 * step, 40 * step)
    ditch = bbox_polygon(20.1 * step, 5 * step, 20.4 * step, 35 * step)  # in the first land column, no cell centre
    land_cells = build_unit.land_mask(land.Difference(ditch), bbox)
    narrow = build_unit.inland_mask(land.Difference(ditch), ditch, bbox) & land_cells
    gaps = np.zeros_like(narrow)  # the sea has its drying heights
    water = bbox_polygon(*bbox).Difference(land.Difference(ditch))
    _, _, gt = build_unit.chart_grid(bbox)
    empty = ogr.Geometry(ogr.wkbMultiPolygon)
    area = build_unit.unsurveyed(gaps, narrow, gt, water, ditch, empty)
    assert area.GetArea() == pytest.approx(ditch.GetArea())
    assert area.Intersection(bbox_polygon(0.0, 0.0, 20 * step, 40 * step)).GetArea() == pytest.approx(0.0, abs=1e-15)


def test_a_narrow_canal_leaves_out_piers_and_satellite_cover():
    from odc import build_unit

    step = GRID_STEP
    bbox = (0.0, 0.0, 40 * step, 40 * step)
    land = bbox_polygon(20 * step, 0.0, 40 * step, 40 * step)
    ditch = bbox_polygon(20.1 * step, 5 * step, 20.4 * step, 35 * step)
    pier = bbox_polygon(20.0 * step, 10 * step, 21 * step, 11 * step)
    charted = land.Difference(ditch).Union(pier)
    land_cells = build_unit.land_mask(charted, bbox)
    narrow = build_unit.inland_mask(charted, ditch, bbox) & land_cells
    water = bbox_polygon(*bbox).Difference(charted)
    _, _, gt = build_unit.chart_grid(bbox)
    satellite = bbox_polygon(20 * step, 30 * step, 21 * step, 35 * step)
    area = build_unit.unsurveyed(np.zeros_like(narrow), narrow, gt, water, ditch, satellite)
    assert area.Intersection(pier).GetArea() == pytest.approx(0.0, abs=1e-15)
    assert area.Intersection(satellite).GetArea() == pytest.approx(0.0, abs=1e-15)
    assert area.GetArea() == pytest.approx(ditch.Difference(pier).Difference(satellite).GetArea())


def model_dem(path, minlon, maxlat, step, values):
    from osgeo import gdal, osr

    ds = gdal.GetDriverByName('GTiff').Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((minlon, step, 0.0, maxlat, 0.0, -step))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(values)
    ds = None
    return str(path)


def test_model_soundings_come_from_the_models_own_water_pixels(tmp_path):
    unit = Unit(492, 288)
    box = unit.build_box
    minlon, minlat, maxlon, maxlat = unit.bounds
    step = 0.001
    values = np.full((int((maxlat - minlat) / step), int((maxlon - minlon) / step)), -12.34, np.float32)
    # Each pixel left out is shallower than the rest, so a kept one would win its square.
    values[0, 0] = -9999.0  # no data
    values[0, 1] = 1.5  # dries
    values[0, 2] = -3.0  # on land in the chart
    values[0, 3] = -2.0  # surveyed
    values[0, 4] = -0.05  # shallower than a decimetre
    values[0, 5] = -1.0  # on a bank the depth areas chart as drying
    land = np.zeros((box.nrows, box.ncols), bool)
    surveyed = np.zeros_like(land)
    charted = np.full((box.nrows, box.ncols), -12.0, np.float32)
    from odc.grid import cell_x, cell_y

    def cell(col):
        return cell_y(maxlat - step / 2) - box.iy0, cell_x(minlon + (col + 0.5) * step) - box.ix0

    land[cell(2)] = True
    surveyed[cell(3)] = True
    charted[cell(5)] = 0.5
    grid = model_dem(tmp_path / 'grid.tif', *box.bbox[::3], GRID_STEP, charted)
    dem = model_dem(tmp_path / 'dem.tif', minlon, maxlat, step, values)
    lon, lat, z = model_soundings(dem, grid, surveyed, land, None, box, unit, 17)
    assert len(z) and np.allclose(z, 12.34)
    centres = (lon - minlon) / step - 0.5
    assert np.allclose(centres, np.round(centres))
    assert unit.owns(lon, lat).all()


def test_a_square_shallower_than_half_a_metre_shows_no_model_number(tmp_path):
    unit = Unit(492, 288)
    box = unit.build_box
    minlon, minlat, maxlon, maxlat = unit.bounds
    step = 0.001
    shape = (int((maxlat - minlat) / step), int((maxlon - minlon) / step))
    land = np.zeros((box.nrows, box.ncols), bool)
    grid = model_dem(
        tmp_path / 'grid.tif', *box.bbox[::3], GRID_STEP, np.full((box.nrows, box.ncols), -12.0, np.float32)
    )

    def numbers(values, name):
        dem = model_dem(tmp_path / name, minlon, maxlat, step, values)
        return model_soundings(dem, grid, np.zeros_like(land), land, None, box, unit, 17)

    deep = np.full(shape, -12.34, np.float32)
    shallow = deep.copy()
    shallow[0, 0] = -0.3
    _, _, all_deep = numbers(deep, 'deep.tif')
    _, _, z = numbers(shallow, 'shallow.tif')
    assert len(z) == len(all_deep) - 1
    assert np.allclose(z, 12.34)


def test_a_model_sounding_gives_way_to_a_survey_sounding(tmp_path):
    from odc.grid import cell_x, cell_y

    unit = Unit(492, 288)
    box = unit.build_box
    lon0, lat0 = unit.bounds[0] + 0.01, unit.bounds[3] - 0.01
    # Same square up to z14: the survey takes it although the model is shallower.
    lon, lat = np.array([lon0, lon0 + 0.0002]), np.array([lat0, lat0])
    thin = thin_soundings(lon, lat, np.array([5.0, 1.0], np.float32), 17, np.array([False, True]))
    assert thin[0] == SOUNDING_MINZOOM and thin[1] == -1

    ix, iy = cell_x(lon[:1]), cell_y(lat[:1])
    survey = tmp_path / 'survey.npz'
    np.savez(
        survey,
        ix=ix,
        iy=iy,
        lon=(lon[:1] * 1e7).astype(np.int64),
        lat=(lat[:1] * 1e7).astype(np.int64),
        z=np.array([5.0]),
    )
    rank = np.full((box.nrows, box.ncols), -1, np.int16)
    rank.ravel()[box.flat_index(ix, iy)] = 0
    entries = [(None, None, 'S1', str(survey), {'end': 20200101})]
    land = np.zeros((box.nrows, box.ncols), bool)
    far = (np.array([lon0 + 0.05]), np.array([lat0]), np.array([4.0], np.float32))
    model = tuple(
        np.concatenate([a, b]) for a, b in zip((lon[1:], lat[1:], np.array([1.0], np.float32)), far, strict=True)
    )
    feats = build_soundings(entries, rank, land, box, unit, 17, model)
    assert sorted((f[1]['DEPTH'], f[1]['INFORM'] == MODEL_INFORM) for f in feats) == [(4.0, True), (5.0, False)]


def test_model_numbers_stop_thinning_out_past_their_zoom():
    rng = np.random.default_rng(3)
    unit = Unit(492, 288)
    lon = rng.uniform(unit.bounds[0], unit.bounds[2], 20000)
    lat = rng.uniform(unit.bounds[1], unit.bounds[3], 20000)
    z = rng.uniform(0, 50, 20000).astype(np.float32)
    minzoom = thin_soundings(lon, lat, z, 17, np.ones(len(z), bool))
    assert minzoom.max() == MODEL_SOUNDING_MAXZOOM
    assert thin_soundings(lon, lat, z, 17).max() == 17


def test_a_model_hole_stays_unknown_and_feeds_no_fill(tmp_path):
    from osgeo import gdal, osr

    step = 1 / 1920
    values = np.full((100, 100), -6.0, dtype='float32')
    values[40:60, 40:60] = MODEL_HOLE
    values[50, 50] = -9999.0  # a gap with only marked cells in reach
    values[10, 10:20] = -9999.0  # a gap next to data
    ds = gdal.GetDriverByName('GTiff').Create(str(tmp_path / 'dem.tif'), 100, 100, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((10.0, step, 0.0, 57.0, 0.0, -step))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds.GetRasterBand(1).WriteArray(values)
    ds = None
    # The bbox plus prepare_dem's margin is the whole raster.
    bbox = (10.01, 57.0 - 100 * step + 0.01, 10.0 + 100 * step - 0.01, 56.99)
    _, filled = prepare_dem(str(tmp_path / 'dem.tif'), bbox, str(tmp_path))
    grid = gdal.Open(filled).ReadAsArray()
    assert (grid[40:60, 40:60] == -9999.0).all()
    assert grid[39, 50] == -6.0
    assert grid[10, 15] == -2.0


def test_smoothing_never_makes_a_deep_shoal_deeper(tmp_path):
    from osgeo import gdal

    from odc import build_unit

    bbox = (0.0, 0.0, 0.01, 0.01)
    ncols, nrows, gt = build_unit.chart_grid(bbox)
    rng = np.random.default_rng(1)
    heights = (-30.0 + rng.uniform(-3, 3, (nrows, ncols))).astype(np.float32)
    heights[nrows // 2, ncols // 2] = -12.0
    dem = str(tmp_path / 'dem.tif')
    gdal.GetDriverByName('GTiff').CreateCopy(dem, build_unit.mem_raster(heights, gt))
    path, _, _ = build_unit.build_depth_grid(dem, bbox, str(tmp_path), None)
    z = gdal.Open(path).ReadAsArray()
    model = gdal.Open(str(tmp_path / 'dem_grid.tif')).ReadAsArray()
    centre = nrows // 2, ncols // 2
    assert -25.0 < model[centre] < -build_unit.SMOOTH_BELOW_M
    assert z[centre] == model[centre]
    assert (z >= model).all()


def test_a_one_cell_shoal_on_the_chart_grid_keeps_its_depth(tmp_path):
    from osgeo import gdal

    from odc import build_unit

    bbox = (0.0, 0.0, 0.01, 0.01)
    ncols, nrows, gt = build_unit.chart_grid(bbox)
    heights = np.full((nrows, ncols), -10.0, np.float32)
    heights[nrows // 2, ncols // 2] = -1.0
    dem = str(tmp_path / 'dem.tif')
    gdal.GetDriverByName('GTiff').CreateCopy(dem, build_unit.mem_raster(heights, gt))
    build_unit.build_depth_grid(dem, bbox, str(tmp_path), None)
    model = gdal.Open(str(tmp_path / 'dem_grid.tif')).ReadAsArray()
    assert model[nrows // 2, ncols // 2] == -1.0
    assert (model == heights).all()


def test_a_reef_flat_is_charted_under_water_where_the_depths_show_water():
    flat = ogr.CreateGeometryFromJson(json.dumps(square(166.38, -22.26, 0.002)))
    reefs = [
        (flat, {'WATLEV': '4', 'NATSUR': '14', 'INFORM': 'reef flat'}, None),
        (flat.Clone(), {'WATLEV': '3', 'INFORM': 'patch reef'}, None),
    ]

    def depth_area(x, drval1, **attrs):
        ring = [[x, -22.26], [x + 0.001, -22.26], [x + 0.001, -22.258], [x, -22.258], [x, -22.26]]
        geom = ogr.CreateGeometryFromJson(json.dumps({'type': 'Polygon', 'coordinates': [ring]}))
        return (geom, {'DRVAL1': drval1, **attrs}, None)

    depare = [
        depth_area(166.38, 0.0),  # shallow water over the reef's western half stays drying
        depth_area(166.381, 2.0),
        depth_area(166.38, 2.0, INFORM='satellite'),  # satellite depths do not count
    ]
    out = sorted((a['INFORM'], a['WATLEV'], round(g.GetArea() * 1e6, 3)) for g, a, _ in submerge_reefs(reefs, depare))
    assert out == [('patch reef', '3', 4.0), ('reef flat', '3', 2.0), ('reef flat', '4', 2.0)]
    assert submerge_reefs(reefs[:1], depare[:1]) == reefs[:1]
    # A flat cut into pieces smaller than MIN_AREA_DEG2 keeps them all, and the
    # flats after it still meet every depth area.
    small = ogr.CreateGeometryFromJson(json.dumps(square(166.38095, -22.2595, 0.0001)))
    kept = submerge_reefs([(small, {'WATLEV': '4'}, None), reefs[0]], depare)
    assert sorted((a['WATLEV'], round(g.GetArea() * 1e8, 3)) for g, a, _ in kept) == [
        ('3', 0.5),
        ('3', 200.0),
        ('4', 0.5),
        ('4', 200.0),
    ]
