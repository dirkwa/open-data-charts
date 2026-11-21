import io
import json
import shutil
import urllib.error
import urllib.parse

import numpy as np
import pytest

from odc import shom, sources


def test_wfs_layers_are_paged_by_their_key_and_keep_stable_ids(monkeypatch):
    rows = [{'type': 'Feature', 'id': f'x.fid-{i}', 'properties': {'gid': 10 + i}, 'geometry': None} for i in range(5)]
    asked = []

    def get(url, **_):
        assert 'DescribeFeatureType' in url
        return io.BytesIO(b'<xsd:element name="gid"/><xsd:element name="nom"/>')

    def get_json(url, **_):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        asked.append(query)
        start = int(query['startIndex'])
        return {'features': [dict(row) for row in rows[start : start + 2]]}

    monkeypatch.setattr(shom, 'get', get)
    monkeypatch.setattr(shom, 'get_json', get_json)
    monkeypatch.setattr(sources, 'WFS_PAGE', 2)
    features = sources.wfs_features('TOPONYMIE_BDD_WFS:toponymes', (0.0, 0.0, 1.0, 1.0))
    assert [f['id'] for f in features] == [f'toponymes.{10 + i}' for i in range(5)]
    assert {q['sortBy'] for q in asked} == {'gid'}
    assert [q['startIndex'] for q in asked] == ['0', '2', '4']


def test_the_newest_release_of_the_danish_model_is_taken(monkeypatch):
    listing = ''.join(
        f"<a href='/main.html?download&weblink=x&subfolder={year}'>{year}</a>" for year in ('2022', '2024', '2023')
    )
    monkeypatch.setattr(shom, 'get', lambda url, **kw: io.BytesIO(listing.encode()))
    assert sources.ddm_release() == '2024'


def test_a_kartverket_request_covers_its_chunk_on_the_coverage_cells():
    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(sources.kartverket_request(sources.KARTVERKET_GRID, 60, 4)).query
    )
    (x, y) = ([float(v) for v in query_subset[2:-1].split(',')] for query_subset in sorted(query['subset']))
    for edge in (*x, *y):
        assert (edge - sources.KARTVERKET_CELL_EDGE_M) % sources.KARTVERKET_CELL_M == 0
    # 60-62 N, 4-6 E reaches (-111654, 6685590) to (29693, 6923060) in UTM 33.
    assert x[0] < -111654 and x[1] > 29693 and y[0] < 6685590 and y[1] > 6923060


def test_a_bsh_request_covers_its_chunk_on_the_coverage_cells_and_stays_inside_it():
    query = urllib.parse.parse_qs(urllib.parse.urlparse(sources.bsh_request('northsea', sources.BSH_GRID, 54, 7)).query)
    (e, n) = ([float(v) for v in subset[2:-1].split(',')] for subset in sorted(query['subset']))
    for edge in (*e, *n):
        assert edge % sources.BSH_CELL_M == 0
    # 54-55 N, 7-8 E reaches (369317, 5984362) to (436171, 6096012) in UTM 32.
    assert e[0] < 369317 and e[1] > 436171 and n[0] < 5984362 and n[1] > 6096012
    # The Baltic coverage starts at 500000 E: a chunk of 8-9 E reaches just past it.
    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(sources.bsh_request('balticsea', sources.BSH_GRID, 54, 8)).query
    )
    (e, n) = ([float(v) for v in subset[2:-1].split(',')] for subset in sorted(query['subset']))
    assert e[0] == sources.BSH_SEAS['balticsea'][0]
    assert sources.bsh_request('balticsea', sources.BSH_GRID, 54, 6) is None


def bsh_world(monkeypatch, emodnet_height):
    """BSH's North Sea 10 m below NHN west of 400000 E (about 7.5 E), with a
    600 m gap around 7.3 E 54.5 N; NHN 2 m above LAT north of 53.8 N (DKLAT),
    2.5 m south of it (NLLAT); EMODnet flat at `emodnet_height`."""
    from osgeo import osr

    from odc import lat_model

    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm = osr.SpatialReference()
    utm.ImportFromEPSG(25832)
    utm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    gap_e, gap_n, _ = osr.CoordinateTransformation(wgs84, utm).TransformPoint(7.3, 54.5)

    def save_url(url, path, timeout=600):
        if url == sources.GCG2016:
            raster(path, 4326, (6.0, 0.01, 0, 57.0, 0, -0.01), np.full((500, 500), 40.0, 'float32'), -9999.0)
        elif url == sources.DKLAT:
            raster(path, 4326, (6.0, 0.01, 0, 57.0, 0, -0.01), np.full((320, 500), 38.0, 'float32'), -32768.0)
        elif url == sources.NLLAT:
            raster(path, 4326, (6.0, 0.01, 0, 53.8, 0, -0.01), np.full((180, 500), 37.5, 'float32'), -32768.0)
        elif url.startswith('https://ows.emodnet-bathymetry.eu/'):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            (lat, lon) = ([int(v) for v in subset.split('(')[1][:-1].split(',')] for subset in sorted(query['subset']))
            cells = np.full(((lat[1] - lat[0]) * 960, (lon[1] - lon[0]) * 960), emodnet_height, 'float32')
            raster(path, 4326, (lon[0], 1 / 960, 0, lat[1], 0, -1 / 960), cells, -9999.0)
        else:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            (e, n) = ([int(v) for v in subset[2:-1].split(',')] for subset in sorted(query['subset']))
            cells = np.full(((n[1] - n[0]) // 50, (e[1] - e[0]) // 50), sources.BSH_NODATA, 'float32')
            if 'northsea' in query['coverageId'][0]:
                cells[:, : max(0, (400000 - e[0]) // 50)] = -10.0
                row, col = int((n[1] - gap_n) // 50), int((gap_e - e[0]) // 50)
                if 0 <= row < cells.shape[0] and 0 <= col < cells.shape[1]:
                    cells[max(0, row - 6) : row + 6, max(0, col - 6) : col + 6] = sources.BSH_NODATA
            raster(path, 25832, (e[0], 50, 0, n[1], 0, -50), cells, sources.BSH_NODATA)

    monkeypatch.setattr(sources, 'save_url', save_url)
    monkeypatch.setattr(sources, 'BSH_GRID', lat_model.Grid(1 / 240, 1))


def raster(path, srs, gt, values, nodata):
    from osgeo import gdal, osr

    ds = gdal.GetDriverByName('GTiff').Create(path, values.shape[1], values.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform(gt)
    ref = osr.SpatialReference()
    ref.ImportFromEPSG(srs)
    ds.SetProjection(ref.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(values)


def cell(chunk, s, w, lon, lat, step=1 / 240):
    return chunk[int((s + 1 - lat) / step), int((lon - w) / step)]


def test_bsh_depths_go_on_lat_on_top_of_the_earlier_model_and_leave_its_other_chunks_alone(monkeypatch, tmp_path):
    from osgeo import gdal

    bsh_world(monkeypatch, -3.0)
    dem = tmp_path / 'dem'
    dem.mkdir()
    for s in (53, 54):
        for w in (7, 8):
            values = np.full((240, 240), -1.0, 'float32')
            raster(str(dem / f'ddm_{s:+03d}_{w:+04d}.tif'), 4326, (w, 1 / 240, 0, s + 1, 0, -1 / 240), values, -9999.0)
    stale = dem / 'bsh_+54_+008.tif'
    stale.write_bytes(b'')
    tmp = tmp_path / 'tmp'
    tmp.mkdir()
    sources.fetch_bsh((7.1, 53.1, 8.9, 54.9), str(dem), str(tmp))
    north = gdal.Open(str(dem / 'bsh_+54_+007.tif')).ReadAsArray()
    south = gdal.Open(str(dem / 'bsh_+53_+007.tif')).ReadAsArray()
    # BSH's 10 m on LAT: 8 m where DKLAT has NHN 2 m above it, 7.5 m south of 53.8 N where NLLAT has 2.5 m.
    assert cell(north, 54, 7, 7.2, 54.2) == pytest.approx(-8.0)
    assert cell(south, 53, 7, 7.2, 53.4) == pytest.approx(-7.5)
    # East of BSH's waters, and in its gap, the earlier model's depths stand.
    assert cell(north, 54, 7, 7.9, 54.5) == pytest.approx(-1.0)
    assert cell(north, 54, 7, 7.3, 54.5) == pytest.approx(-1.0)
    # 8-9 E has no BSH data: the earlier model's chunks stand alone.
    assert not stale.exists()
    assert sources.dem_grids(str(tmp_path))[-1].endswith('bsh_+54_+007.tif')


def test_bsh_on_emodnet_alone_marks_its_gaps_unknown_and_keeps_emodnet_beyond(monkeypatch, tmp_path):
    from osgeo import gdal

    from odc import build_unit

    bsh_world(monkeypatch, -1.0)
    dem = tmp_path / 'dem'
    dem.mkdir()
    tmp = tmp_path / 'tmp'
    tmp.mkdir()
    sources.fetch_bsh((7.1, 54.1, 8.9, 54.9), str(dem), str(tmp))
    west = gdal.Open(str(dem / 'bsh_+54_+007.tif')).ReadAsArray()
    assert cell(west, 54, 7, 7.3, 54.5) == build_unit.MODEL_HOLE
    assert cell(west, 54, 7, 7.9, 54.5) == pytest.approx(-1.0)
    # A chunk without BSH data still carries EMODnet: no other model stands under it.
    east = gdal.Open(str(dem / 'bsh_+54_+008.tif')).ReadAsArray()
    assert np.allclose(east, -1.0)


def test_a_microtidal_shore_in_the_emodnet_dtm_is_charted_as_unknown(monkeypatch, tmp_path):
    import numpy as np
    from osgeo import gdal, osr

    from odc import build_unit

    heights = np.array([[-12.0, -3.0, -0.4, 0.0, 1.5, 40.0, -9999.0]], dtype='float32')

    def save_url(url, path, timeout=600):
        ds = gdal.GetDriverByName('GTiff').Create(path, heights.shape[1], 1, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((2.0, 1 / 960, 0.0, 41.0, 0.0, -1 / 960))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        ds.SetProjection(srs.ExportToWkt())
        ds.GetRasterBand(1).SetNoDataValue(-9999.0)
        ds.GetRasterBand(1).WriteArray(heights)

    monkeypatch.setattr(sources, 'save_url', save_url)
    hole = build_unit.MODEL_HOLE
    for microtidal, expected in (
        (False, [-12, -3, -0.4, 0, 1.5, -9999, -9999]),
        (True, [-12, -3, hole, hole, hole, -9999, -9999]),
    ):
        target = tmp_path / str(microtidal)
        target.mkdir()
        sources.fetch_emodnet((2.1, 39.1, 2.2, 39.2), str(target), microtidal)
        out = gdal.Open(str(target / 'emodnet_+39_+002.tif')).ReadAsArray()[0]
        assert np.allclose(out, expected)


def test_a_linz_layer_is_asked_for_with_the_key_in_a_header_and_never_truncated(monkeypatch):
    asked = []

    def get(url, headers=None, **_):
        asked.append((url, headers))
        rows = [{'type': 'Feature', 'id': f'layer-7.{i}', 'properties': {'fidn': 100 - i}} for i in range(3)]
        rows[0]['geometry'] = {'type': 'Polygon', 'coordinates': [[[179.0, -40.0], [-179.0, -40.0], [-179.0, -41.0]]]}
        return io.BytesIO(json.dumps({'features': rows, 'numberMatched': matched}).encode())

    monkeypatch.setattr(shom, 'get', get)
    matched = 3
    features = sources.linz_layer('7', (174.0, -37.0, 175.0, -36.0), 'secret')
    assert [f['id'] for f in features] == ['linz-7.100', 'linz-7.98', 'linz-7.99']
    # Across the antimeridian a ring stays east of Greenwich instead of spanning the globe.
    assert features[0]['geometry']['coordinates'] == [[[179.0, -40.0], [181.0, -40.0], [181.0, -41.0]]]
    url, headers = asked[0]
    assert headers == {'Authorization': 'key secret'} and 'secret' not in url
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert query['typeNames'] == 'layer-7' and query['bbox'].startswith('-37.0,174.0,-36.0,175.0')
    matched = 4
    with pytest.raises(RuntimeError, match='3 of 4'):
        sources.linz_layer('7', (174.0, -37.0, 175.0, -36.0), 'secret')


def test_a_linz_400_is_asked_again_and_explained_when_it_stays(monkeypatch):
    answers = []

    def get(url, **_):
        answers.append(url)
        if len(answers) < 3:
            raise urllib.error.HTTPError(url, 400, 'Bad Request', {}, io.BytesIO(b'<Exception>busy</Exception>'))
        return io.BytesIO(b'{"features": []}')

    monkeypatch.setattr(shom, 'get', get)
    assert sources.linz_get('https://example.org/wfs', 'secret', wait=0) == {'features': []}
    assert len(answers) == 3
    answers.clear()
    monkeypatch.setattr(sources, 'LINZ_ATTEMPTS', 2)
    with pytest.raises(RuntimeError, match='400 Bad Request: <Exception>busy'):
        sources.linz_get('https://example.org/wfs', 'secret', wait=0)
    assert len(answers) == 2


def test_chart_layers_need_the_key_and_go_when_the_region_drops_them(tmp_path, monkeypatch):
    stale = tmp_path / 'chart' / '1__DEPARE__4.json'
    stale.parent.mkdir()
    stale.write_text('{}')
    sources.fetch_chart({'sources': ['osm']}, str(tmp_path))
    assert not stale.exists()
    monkeypatch.delenv(sources.LINZ_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=sources.LINZ_KEY_ENV):
        sources.fetch_chart({'sources': ['linz-hydro'], 'bbox': [174.0, -37.0, 175.0, -36.0]}, str(tmp_path))


def test_an_osm_extract_is_tried_again_after_a_dropped_connection(monkeypatch, tmp_path):
    calls = []

    def save_url(url, path, timeout=600):
        calls.append(url)
        if len(calls) < 3:
            raise OSError(101, 'Network is unreachable')
        open(path, 'wb').close()

    monkeypatch.setattr(sources, 'save_url', save_url)
    target = str(tmp_path / 'malta.osm.pbf')
    sources.download_extract(['https://example.org/malta.osm.pbf'], target, wait=0)
    assert calls == ['https://example.org/malta.osm.pbf'] * 3
    assert (tmp_path / 'malta.osm.pbf').exists()


def test_an_emodnet_hr_grid_keeps_the_shoalest_depth_on_its_own_cells():
    import numpy as np

    lines = [
        '-9.10136719;38.73092448;8.79;8.54;8.62;0.10;4;;;;x;;',
        '-9.10149740;38.73079427;;;3.24;;1;0;;;x;;',
        '-9.10136719;38.73079427;-0.20;-0.90;-0.50;0.07;9;;;;x;;',
        '-9.10136719;38.73079427;6.00;5.00;5.50;0.07;9;;;;x;;',
        '-9.10149740;38.73092448;-20.0;-30.0;-25.0;;1;;;;x;;',
    ]
    heights, west, north = sources.emodnet_hr_grid(lines, 7680)
    assert heights.shape == (2, 2)
    assert west == np.floor(-9.10149740 * 7680) / 7680 and north == (np.floor(38.73092448 * 7680) + 1) / 7680
    # Row 0 is north: the land cell there is dropped, the drying bank wins its repeated cell.
    assert np.allclose(heights, [[sources.EMODNET_NODATA, -8.54], [-3.24, 0.9]])


def test_a_smoothed_emodnet_hr_grid_never_reads_deeper_than_a_cell_it_lies_in():
    import numpy as np

    from odc.grid import GRID_STEP

    cpd = 3840
    heights = np.full((6, 6), -10.0, dtype='float32')
    heights[2, 3] = -1.0
    heights[0, 0] = sources.EMODNET_NODATA
    west, north = -9.0, 38.75
    smooth, sw, sn = sources.smooth_on_chart_grid(heights, west, north, cpd)
    assert (sw / GRID_STEP).is_integer() and (sn / GRID_STEP).is_integer()
    rows, cols = np.indices(smooth.shape)
    lon = sw + (cols + 0.5) * GRID_STEP
    lat = sn - (rows + 0.5) * GRID_STEP
    r, c = np.floor((north - lat) * cpd).astype(int), np.floor((lon - west) * cpd).astype(int)
    inside = (r >= 0) & (r < 6) & (c >= 0) & (c < 6)
    cell = np.full(smooth.shape, np.nan)
    cell[inside] = heights[r[inside], c[inside]]
    data = inside & (cell != sources.EMODNET_NODATA) & (smooth != sources.EMODNET_NODATA)
    assert data.sum() > 0.8 * inside.sum()
    assert (smooth[data] >= cell[data] - 1e-4).all()
    # Between the shoal and deep water the depth changes gradually, not in one step.
    assert len(np.unique(np.round(smooth[data], 2))) > 3


def test_infomar_writes_only_tiles_with_data_on_their_grid(monkeypatch, tmp_path):
    from osgeo import gdal

    from odc.grid import GRID_STEP

    def export(layer, bounds, width, height, path):
        heights = np.zeros((height, width), np.float32)
        west, south, east, north = bounds
        if east - west > 0.25:
            # The probe: data only in the tile 53.0-53.25 N, 9.5-9.25 W.
            heights[
                round((north - 53.2) / sources.INFOMAR_PROBE_STEP), round((-9.4 - west) / sources.INFOMAR_PROBE_STEP)
            ] = -12.0
        elif (west, south) == (-9.5, 53.0):
            heights[:] = -12.0
            heights[0, 0] = 0.0  # no data
            heights[0, 1] = 40.0  # land
        return heights

    monkeypatch.setattr(sources, 'infomar_export', export)
    monkeypatch.setattr(sources, 'INFOMAR_LAYERS', (('10m_Inshore_IE', GRID_STEP, 0.25),))
    (tmp_path / 'infomar_10m_+052.750_-009.500.tif').write_text('')  # an earlier run's tile
    sources.fetch_infomar((-9.7, 52.9, -9.1, 53.4), str(tmp_path), str(tmp_path))
    written = sorted(p.name for p in tmp_path.glob('infomar_*.tif'))
    assert written == ['infomar_10m_+053.000_-009.500.tif']
    ds = gdal.Open(str(tmp_path / written[0]))
    assert ds.GetGeoTransform() == pytest.approx((-9.5, GRID_STEP, 0.0, 53.25, 0.0, -GRID_STEP))
    band = ds.GetRasterBand(1)
    heights = band.ReadAsArray()
    assert heights[0, 0] == band.GetNoDataValue() and heights[0, 1] == band.GetNoDataValue()
    assert heights[1, 1] == -12.0


def test_the_catalan_model_keeps_the_shoalest_metre_of_each_cell_and_only_its_own_chunks(tmp_path, monkeypatch):
    import numpy as np
    from osgeo import gdal, osr

    from odc import lat_model

    # 2 km of a 1 m model off Barcelona in UTM 31N: a 4 m channel bed with a
    # 1 m rock in each 10 m square, and land of 20 m to the north.
    heights = np.full((2000, 2000), -4.0, dtype='float32')
    heights[::10, ::10] = -1.0
    heights[:500] = 20.0
    model = str(tmp_path / 'icgc.tif')
    ds = gdal.GetDriverByName('GTiff').Create(model, 2000, 2000, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((431000.0, 1.0, 0.0, 4582000.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25831)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds.GetRasterBand(1).WriteArray(heights)
    # Like the real model's overviews, which average the rocks away.
    ds.BuildOverviews('AVERAGE', [2, 4, 8, 16, 32])
    ds = None
    monkeypatch.setattr(sources, 'icgc_model', lambda tmp: str(tmp_path / 'copy.tif'))
    shutil.copy(model, tmp_path / 'copy.tif')
    monkeypatch.setattr(sources, 'ICGC_GRID', lat_model.Grid(1 / 4000, 1))
    target = tmp_path / 'dem'
    target.mkdir()
    sources.fetch_icgc((-1.0, 39.0, 3.5, 43.0), str(target), str(tmp_path))
    assert sorted(p.name for p in target.iterdir()) == ['icgc_+41_+002.tif']
    out = gdal.Open(str(target / 'icgc_+41_+002.tif')).ReadAsArray()
    water = out[(out != lat_model.NODATA) & (out < 0)]
    # Only a cell that a chunk edge cuts to a sliver can miss its rocks.
    assert water.size and (water == -1.0).mean() > 0.95
    assert not ((out > lat_model.MAX_HEIGHT_M) & (out != lat_model.NODATA)).any()


def test_the_mirrored_catalan_model_is_joined_from_its_checked_parts(monkeypatch, tmp_path):
    parts = {f'{sources.ICGC_FILE}.part-0{i}': bytes([i]) * 3 for i in range(2)}
    assets = {name: (f'https://example.org/{name}', f'sha256:{name}') for name in parts}
    assets['other.tif'] = ('https://example.org/other.tif', 'sha256:x')
    monkeypatch.setattr(sources, 'release_assets', lambda url: assets)
    checked = []

    def save_square(url, digest, path):
        name = url.rsplit('/', 1)[1]
        checked.append(digest)
        (tmp_path / name).write_bytes(parts[name])

    monkeypatch.setattr(sources, 'save_square', save_square)
    model = sources.icgc_model(str(tmp_path))
    assert open(model, 'rb').read() == bytes([0, 0, 0, 1, 1, 1])
    assert checked == [f'sha256:{name}' for name in sorted(parts)]


def test_a_region_away_from_catalonia_fetches_no_catalan_model(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, 'icgc_model', lambda tmp: pytest.fail('fetched'))
    sources.fetch_icgc((10.0, 40.0, 12.0, 42.0), str(tmp_path), str(tmp_path))
