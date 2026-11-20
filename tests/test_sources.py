import io
import urllib.parse

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
