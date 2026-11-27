import json
import os
import uuid

import numpy as np
import pytest
from osgeo import gdal

from odc import build_unit, his_depth, mirror

# A 200 x 140 pixel crop of sea_dp in the `black` style, off Pirita at 1 m a pixel.
TILE = os.path.join(os.path.dirname(__file__), 'data', 'his-sea-dp-black.png')


def png(mask):
    height, width = mask.shape
    ds = gdal.GetDriverByName('MEM').Create('', width, height, 4, gdal.GDT_Byte)
    for band in (1, 2, 3):
        ds.GetRasterBand(band).Fill(0)
    ds.GetRasterBand(4).WriteArray(np.where(mask, 255, 0).astype(np.uint8))
    path = f'/vsimem/test-{uuid.uuid4().hex}.png'
    gdal.GetDriverByName('PNG').CreateCopy(path, ds)
    f = gdal.VSIFOpenL(path, 'rb')
    data = gdal.VSIFReadL(1, gdal.VSIStatL(path).size, f)
    gdal.VSIFCloseL(f)
    gdal.Unlink(path)
    return bytes(data)


def label(text):
    integer, _, decimal = text.partition('.')
    glyphs = [his_depth.BIG_BITMAPS[d] for d in integer]
    width = sum(g.shape[1] for g in glyphs) + (1 + his_depth.SMALL_BITMAPS[decimal].shape[1] if decimal else 0)
    out = np.zeros((his_depth.BIG_H + 4, width), bool)
    x = 0
    for g in glyphs:
        out[: his_depth.BIG_H, x : x + g.shape[1]] = g
        x += g.shape[1]
    if decimal:
        small = his_depth.SMALL_BITMAPS[decimal]
        out[4 : 4 + his_depth.SMALL_H, x + 1 : x + 1 + small.shape[1]] = small
    return out


def test_the_soundings_of_a_real_tile_are_read():
    labels = his_depth.read_labels(his_depth.opaque(open(TILE, 'rb').read(), 200, 140))
    found = {(round(x), round(y)): depth for x, y, depth in labels}
    assert len(labels) == 24
    assert found[(36, 103)] == 24.1
    assert found[(10, 98)] == 24.9
    assert found[(125, 29)] == 24.4
    assert found[(89, 31)] == 24.6
    assert found[(68, 32)] == 25.4 or found[(69, 32)] == 25.4
    assert all(24.0 <= d <= 25.5 for d in found.values())


def test_a_label_without_a_decimal_reads_as_whole_metres():
    mask = np.zeros((30, 40), bool)
    bitmap = label('12')
    mask[10 : 10 + bitmap.shape[0], 5 : 5 + bitmap.shape[1]] = bitmap
    [(_, _, depth)] = his_depth.read_labels(mask)
    assert depth == 12.0


def test_touching_digits_are_split():
    touching = np.hstack([his_depth.BIG_BITMAPS['3'], his_depth.BIG_BITMAPS['8']])
    assert his_depth.split_digits(touching) == [(0, '3', 5), (5, '8', 5)]
    assert his_depth.split_digits(np.ones((his_depth.BIG_H, 7), bool)) is None


def test_a_lone_digit_against_unreadable_pixels_is_dropped():
    mask = np.zeros((30, 40), bool)
    mask[10 : 10 + his_depth.BIG_H, 5:10] = his_depth.BIG_BITMAPS['7']
    mask[8:20, 11:13] = True
    assert his_depth.read_labels(mask) == []


def test_a_reading_far_from_its_neighbours_is_dropped():
    xy = np.array([[0, 0], [10, 0], [0, 10], [10, 10], [5, 5], [500, 500]], float)
    depth = np.array([24.1, 24.5, 24.3, 24.6, 4.5, 3.0])
    assert his_depth.plausible(xy, depth).tolist() == [True, True, True, True, False, True]


def test_a_tile_is_read_with_a_margin_and_placed_on_the_estonian_grid():
    asked = []

    def get(layer, style, box, width, height):
        asked.append((layer, style, box, width, height))
        mask = np.zeros((height, width), bool)
        bitmap = label('7.3')
        # Inside the tile, 100 m east and 50 m south of its north-west corner.
        row, col = his_depth.MARGIN_PX + 50 - 4, his_depth.MARGIN_PX + 100 - 2
        mask[row : row + bitmap.shape[0], col : col + bitmap.shape[1]] = bitmap
        # In the margin only: read by the neighbouring tile.
        mask[5 : 5 + bitmap.shape[0], 5 : 5 + bitmap.shape[1]] = bitmap
        return png(mask)

    found = his_depth.tile_soundings((540000.0, 6590000.0, 540200.0, 6590200.0), get)
    [(x, y, depth, _)] = [s for s in found if s[3]]
    assert len(found) == 2
    m = his_depth.MARGIN_PX
    assert asked == [
        ('sea_dp', 'black', (540000.0 - m, 6590000.0 - m, 540200.0 + m, 6590200.0 + m), 200 + 2 * m, 200 + 2 * m)
    ]
    assert depth == 7.3
    assert abs(x - 540100.5) <= 1.5 and abs(y - 6590150) <= 1


def test_an_answer_that_is_not_an_image_fails():
    with pytest.raises(OSError):
        his_depth.opaque(b'<html>500</html>', 10, 10)


def test_a_cut_off_image_fails_like_an_error_page():
    with pytest.raises(OSError):
        his_depth.opaque(png(np.ones((50, 50), bool))[:60], 50, 50)


def test_a_sounding_in_two_tile_margins_counts_once(monkeypatch):
    def tile(box, get):
        # Both tiles see the same two soundings beyond the square's north edge.
        beyond = [(1990.0, 2005.0, 20.0, False), (2010.0, 2005.0, 20.0, False)]
        own = [(1995.0, 1995.0, 4.0, True)] if box[0] == 0 else []
        return own + beyond

    monkeypatch.setattr(his_depth, 'tile_soundings', tile)
    monkeypatch.setattr(his_depth, 'MIN_NEIGHBOURS', 3)
    xyz, dropped = his_depth.block_soundings((0, 0, 4000, 2000), pause=0)
    assert len(xyz) == 1 and dropped == 0


def test_squares_are_found_where_the_overview_draws_anything():
    def get(layer, style, box, width, height):
        mask = np.zeros((height, width), bool)
        if layer == 'sea_dp' and box[:2] == (500000, 6580000):
            # One pixel in the south-west square of the overview.
            mask[height - 3, 2] = True
        return png(mask)

    found = his_depth.drawn_blocks((505000.0, 6585000.0, 518000.0, 6595000.0), get, pause=0)
    assert found == [((500000, 6580000, 510000, 6590000), True)]


def test_squares_of_an_overview_that_fails_are_held():
    def get(layer, style, box, width, height):
        if box[:2] == (500000, 6580000):
            return b'<html>503</html>'
        return png(np.zeros((height, width), bool))

    surveys = his_depth.discover({'estonia': (24.0, 59.35, 24.2, 59.45)}, get, pause=0)
    held = [s for s in surveys if s.get('held')]
    assert held and all(s['regions'] == ['estonia'] and s['provider'] == 'vta-his-depth' for s in held)
    assert not [s for s in surveys if not s.get('held')]


def test_two_labels_side_by_side_are_not_read_as_one_number():
    mask = np.zeros((30, 60), bool)
    digits = np.hstack([his_depth.BIG_BITMAPS[d] for d in '2418'])
    mask[10 : 10 + his_depth.BIG_H, 5 : 5 + digits.shape[1]] = digits
    assert his_depth.read_labels(mask) == []


def test_a_square_edge_is_judged_with_the_soundings_beyond_it(monkeypatch):
    def tile(box, get):
        minx, miny = box[:2]
        # Three soundings at 20 m just outside the west edge, a 4 m misread just inside.
        out = [(minx + 5, miny + 100, 4.0, True)]
        if minx == 540000:
            out += [(minx - 10, miny + 100 + dy, 20.0, False) for dy in (-10, 0, 10)]
        return out

    monkeypatch.setattr(his_depth, 'tile_soundings', tile)
    xyz, dropped = his_depth.block_soundings((540000, 6590000, 542000, 6592000), pause=0)
    assert len(xyz) == 0 and dropped == 1


def test_a_square_is_a_mirror_survey_named_after_its_corner():
    survey = his_depth.survey((540000, 6590000, 550000, 6600000), ['estonia'])
    assert survey['name'] == 'vta-his-depth-540-6590'
    assert survey['provider'] == 'vta-his-depth'
    assert survey['start'] == survey['end'] == his_depth.EDITION
    assert his_depth.survey((540000, 6590000, 550000, 6600000), [])['md5'] == survey['md5']
    minlon, minlat, maxlon, maxlat = survey['bbox']
    assert 24.6 < minlon < maxlon < 24.9 and 59.4 < minlat < maxlat < 59.6


def test_the_mirror_indexes_and_credits_a_his_square():
    survey = his_depth.survey((540000, 6590000, 550000, 6600000), ['estonia'])
    asset = {'release': 'mirror-0', 'name': mirror.asset_name(survey), 'size': 10, 'digest': 'sha256:ab'}
    entry = mirror.index_entry(survey, asset)
    assert entry['provider'] == 'vta-his-depth' and entry['source']['edition'] == his_depth.EDITION
    assert 'Estonian Transport Administration' in build_unit.survey_inform(survey['name'], entry)
    assert build_unit.survey_doi(survey['name'], entry) == {}


def test_a_square_reduces_to_the_chart_grid(tmp_path, monkeypatch):
    monkeypatch.setattr(his_depth, 'tile_soundings', lambda box, get: [(box[0] + 5, box[1] + 5, 3.4, True)])
    monkeypatch.setattr(his_depth, 'PAUSE_S', 0)
    survey = his_depth.survey((540000, 6590000, 544000, 6594000), ['estonia'])
    result = his_depth.reduce_survey(survey, str(tmp_path / 'out.npz'), get=None)
    assert result['soundings'] == 4 and result['cells'] == 4 and result['dropped'] == 0
    with np.load(tmp_path / 'out.npz') as d:
        assert np.allclose(d['z'], 3.4)


def test_his_soundings_are_chart_depths_without_a_survey_date():
    survey = his_depth.survey((540000, 6590000, 550000, 6600000), ['estonia'])
    assert build_unit.quality_category(survey) == 'chart'
    attrs = build_unit.survey_attributes(('', '', survey['name'], '', survey), 12.34, 14)
    assert 'SORDAT' not in attrs and attrs['DEPTH'] == 12.3


def test_a_his_square_the_overview_missed_keeps_its_reduction(tmp_path, monkeypatch):
    survey = his_depth.survey((540000, 6590000, 550000, 6600000), ['estonia'])
    asset = {'release': 'mirror-0', 'name': mirror.asset_name(survey), 'size': 10, 'digest': 'sha256:ab'}
    old = {'surveys': {survey['name']: mirror.index_entry(survey, asset)}}
    (tmp_path / 'work').mkdir()
    (tmp_path / 'work' / 'discovery.json').write_text('[]')
    (tmp_path / 'assets.json').write_text(json.dumps([asset]))
    (tmp_path / 'old.json').write_text(json.dumps(old))
    paths = (tmp_path / 'work', tmp_path / 'assets.json', tmp_path / 'old.json', tmp_path / 'out')
    mirror.build_index(*map(str, paths))
    index = json.loads((tmp_path / 'out' / 'mirror-index.json').read_text())
    assert survey['name'] in index['surveys']
    assert index['regions'] == {'estonia': {'surveys': 1, 'mirrored': 1}}
