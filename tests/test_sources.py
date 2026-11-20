import io
import json
import urllib.parse

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


def test_chart_layers_need_the_key_and_go_when_the_region_drops_them(tmp_path, monkeypatch):
    stale = tmp_path / 'chart' / '1__DEPARE__4.json'
    stale.parent.mkdir()
    stale.write_text('{}')
    sources.fetch_chart({'sources': ['osm']}, str(tmp_path))
    assert not stale.exists()
    monkeypatch.delenv(sources.LINZ_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=sources.LINZ_KEY_ENV):
        sources.fetch_chart({'sources': ['linz-hydro'], 'bbox': [174.0, -37.0, 175.0, -36.0]}, str(tmp_path))
