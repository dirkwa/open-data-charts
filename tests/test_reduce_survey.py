import io

import numpy as np
import pytest

from odc.grid import GRID_STEP, cell_x, cell_y
from odc.reduce_survey import reduce_stream, save

HEADER = b'Bathymetry produced by Shom - 540\nS000000000\nlong(DD),lat(DD),depth(m - down positive - LAT)\n'


def test_keeps_the_shoalest_sounding_per_cell_with_its_own_position():
    lon0, lat0 = 166.2, -22.2
    rows = [
        (lon0 + GRID_STEP * 0.2, lat0 - GRID_STEP * 0.2, 12.5),
        (lon0 + GRID_STEP * 0.7, lat0 - GRID_STEP * 0.6, 11.0),  # same cell, shoaler
        (lon0 + GRID_STEP * 1.5, lat0 - GRID_STEP * 0.5, -0.4),  # next cell, drying
    ]
    text = HEADER + b''.join(f'{x:.8f},{y:.8f},{z}\n'.encode() for x, y, z in rows)
    reduced, total = reduce_stream(io.BytesIO(text))
    assert total == 3
    assert len(reduced['z']) == 2
    order = np.argsort(reduced['ix'])
    ix, iy, z = reduced['ix'][order], reduced['iy'][order], reduced['z'][order]
    assert list(z) == [11.0, np.float32(-0.4)]
    assert ix[0] == cell_x(lon0 + GRID_STEP * 0.7) and iy[0] == cell_y(lat0 - GRID_STEP * 0.6)
    assert ix[1] == ix[0] + 1
    lon = reduced['lon'][order][0] / 1e7
    assert abs(lon - (lon0 + GRID_STEP * 0.7)) < 1e-7


def test_empty_survey():
    reduced, total = reduce_stream(io.BytesIO(HEADER))
    assert total == 0 and len(reduced['z']) == 0


def test_chunk_boundaries_do_not_split_lines(monkeypatch):
    import odc.reduce_survey as rs

    monkeypatch.setattr(rs, 'CHUNK', 7)
    text = HEADER + b'166.1,-22.1,5.0\n166.2,-22.2,6.0\n'
    reduced, total = rs.reduce_stream(io.BytesIO(text))
    assert total == 2 and sorted(reduced['z']) == [5.0, 6.0]


def test_reads_every_file_of_a_concatenated_stream():
    text = HEADER + b'166.1,-22.1,5.0\n' + HEADER + b'166.2,-22.2,6.0\n'
    reduced, total = reduce_stream(io.BytesIO(text))
    assert total == 2 and sorted(reduced['z']) == [5.0, 6.0]


def test_rejects_rows_with_fewer_than_three_fields():
    text = HEADER + b'166,-22,5\n166.1,-22.1\n166.2,-22.2,7\n'
    with pytest.raises(ValueError):
        reduce_stream(io.BytesIO(text))


def test_drops_columns_after_the_depth():
    text = HEADER + b'166.1 -22.1 5.0 1 \n166.2 -22.2 6.0 1 \n'
    reduced, total = reduce_stream(io.BytesIO(text))
    assert total == 2 and sorted(reduced['z']) == [5.0, 6.0]


def test_drops_columns_after_the_depth_without_a_header():
    reduced, total = reduce_stream(io.BytesIO(b'166.1 -22.1 5.0 1\n166.2 -22.2 6.0 1\n'))
    assert total == 2 and sorted(reduced['z']) == [5.0, 6.0]


def test_reads_files_of_three_and_four_columns_in_one_stream():
    text = HEADER + b'166.1,-22.1,5.0\n' + HEADER + b'166.2,-22.2,6.0,1\n'
    reduced, total = reduce_stream(io.BytesIO(text))
    assert total == 2 and sorted(reduced['z']) == [5.0, 6.0]


def test_antimeridian_is_one_column():
    reduced, _ = reduce_stream(io.BytesIO(HEADER + b'180,0,5\n-180,0.0001,6\n'))
    assert set(reduced['ix']) == {0}
    assert set(reduced['lon']) == {-1_800_000_000}


def test_skips_a_header_of_three_words():
    reduced, total = reduce_stream(io.BytesIO(b'lon lat depth\n166.1,-22.1,5.0\n'))
    assert total == 1 and list(reduced['z']) == [5.0]


def test_a_failed_save_leaves_no_file_under_the_asset_name(tmp_path, monkeypatch):
    path = tmp_path / 'S1.abc.npz'

    def fail(fh, **arrays):
        fh.write(b'partial')
        raise OSError('disk full')

    monkeypatch.setattr(np, 'savez_compressed', fail)
    with pytest.raises(OSError):
        save(str(path), {'z': np.zeros(1)})
    assert list(tmp_path.iterdir()) == []
