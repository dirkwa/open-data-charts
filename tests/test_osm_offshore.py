import pytest

from odc import osm_offshore


def test_squares_lie_on_whole_multiples_and_cover_the_box():
    found = list(osm_offshore.squares([-4.0, 51.0, 1.0, 54.3]))
    assert found[0] == (-4.0, 51.0, -2.0, 52.0)
    assert found[-1] == (0.0, 54.0, 1.0, 54.3)
    assert len(found) == 3 * 3
    assert sum((e - w) * (n - s) for w, s, e, n in found) == pytest.approx(5.0 * 3.3)


def test_the_query_asks_for_every_selector_with_what_it_is_made_of():
    query = osm_offshore.query((2.0, 54.0, 4.0, 56.0))
    assert '[bbox:54.0,2.0,56.0,4.0]' in query
    assert all(f'{selector};' in query for selector in osm_offshore.SELECTORS)
    assert query.endswith('(._;>;);out meta;')


def test_an_answer_that_ran_out_of_time_is_not_complete(tmp_path):
    whole = tmp_path / 'whole.osm'
    whole.write_text('<?xml version="1.0"?><osm version="0.6"><node id="1" lat="0" lon="0"/></osm>\n')
    cut = tmp_path / 'cut.osm'
    cut.write_text('<?xml version="1.0"?><osm version="0.6"><remark> runtime error: Query timed out </remark></osm>')
    truncated = tmp_path / 'truncated.osm'
    truncated.write_text('<?xml version="1.0"?><osm version="0.6"><node id="1"')
    assert osm_offshore.complete(str(whole))
    assert not osm_offshore.complete(str(cut))
    assert not osm_offshore.complete(str(truncated))


def test_a_failing_instance_is_followed_by_the_next(tmp_path, monkeypatch):
    asked = []

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def get(url, timeout, tries):
        asked.append(url.split('?')[0])
        if url.startswith('https://a'):
            raise OSError('reset')
        return Answer(b'<osm version="0.6"></osm>')

    monkeypatch.setattr(osm_offshore.shom, 'get', get)
    path = tmp_path / 'square.osm'
    osm_offshore.fetch_square((0, 0, 1, 1), str(path), endpoints=('https://a', 'https://b'), wait=0)
    assert asked == ['https://a', 'https://b']
    assert path.read_bytes().endswith(b'</osm>')
