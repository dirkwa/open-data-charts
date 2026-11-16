"""Reduce one Shom survey XYZ stream to the shoalest sounding per cell of
the global chart grid.

Input on stdin: header lines, then `lon,lat,depth` with depth in m,
down positive, LAT (drying heights negative). The caller streams it out of
the survey's 7z: `7z x -so S….7z '*.xyz' -r | python3 -m odc.reduce_survey out.npz`.

The .npz holds, one entry per occupied cell: ix, iy (int32 global grid
indices), lon, lat (int32, 1e-7 degrees, the sounding's own position) and
z (float32 depth).
"""

import os
import sys

import numpy as np

from odc.grid import CELLS_PER_DEGREE, GRID_COLUMNS, GRID_STEP

CHUNK = 16 << 20
COORD_SCALE = 10_000_000


def shoalest_per_cell(key, *cols):
    """Keep one entry per key: the one with the smallest depth (last column)."""
    order = np.lexsort((cols[-1], key))
    key = key[order]
    first = np.ones(len(key), bool)
    first[1:] = key[1:] != key[:-1]
    return (key[first], *(c[order][first] for c in cols))


SEPARATORS = (b',', b';', b'\t', b'\r')
SPACE, NEWLINE = ord(' '), ord('\n')
HALF_TURN = 180 * COORD_SCALE


def is_number(token):
    try:
        float(token)
    except ValueError:
        return False
    return True


def fields_per_line(text):
    """Number of whitespace-separated fields on each line of text."""
    a = np.frombuffer(text, np.uint8)
    newlines = np.flatnonzero(a == NEWLINE)
    gap = a == SPACE
    gap[newlines] = True
    starts = ~gap
    starts[1:] &= gap[:-1]
    line = np.searchsorted(newlines, np.flatnonzero(starts))
    return np.bincount(line, minlength=len(newlines) + 1)


def parse_lines(text):
    """Rows of a chunk. Lines that are not numbers (the header of each
    survey file, as 7z streams one after another) are skipped; a numeric
    line without exactly three fields is an error, never merged with the next."""
    counts = fields_per_line(text)
    rows = int(np.count_nonzero(counts == 3))
    if np.all((counts == 3) | (counts == 0)):
        try:
            values = np.fromstring(text, dtype=np.float64, sep=' ')
        except ValueError:  # a three-word header line; newer NumPy raises instead of stopping
            values = None
        if values is not None and len(values) == 3 * rows:
            return values.reshape(-1, 3)
    kept = []
    for line in text.split(b'\n'):
        fields = line.split()
        if not fields or not all(is_number(f) for f in fields):
            continue
        if len(fields) != 3:
            raise ValueError(f'malformed XYZ line: {line[:80]!r}')
        kept.append(line)
    return np.fromstring(b'\n'.join(kept), dtype=np.float64, sep=' ').reshape(-1, 3)


def reduce_chunk(xyz):
    lon, lat, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    ok = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(z) & (np.abs(lon) <= 180) & (np.abs(lat) < 90)
    lon = np.round(lon[ok] * COORD_SCALE).astype(np.int64)
    lat = np.round(lat[ok] * COORD_SCALE).astype(np.int64)
    lon[lon >= HALF_TURN] -= 2 * HALF_TURN  # 180° is -180°
    # Cells from the stored position, so a sounding always lies in its cell.
    ix = (lon + HALF_TURN) * CELLS_PER_DEGREE // COORD_SCALE
    iy = (HALF_TURN // 2 - lat) * CELLS_PER_DEGREE // COORD_SCALE
    return shoalest_per_cell(
        iy * GRID_COLUMNS + ix, lon.astype(np.int32), lat.astype(np.int32), z[ok].astype(np.float32)
    )


def read_xyz(stream):
    rest = b''
    while True:
        block = stream.read(CHUNK)
        data = rest + block
        if not data:
            return
        if block:
            cut = data.rfind(b'\n') + 1
            data, rest = data[:cut], data[cut:]
        else:
            rest = b''
        for sep in SEPARATORS:
            data = data.replace(sep, b' ')
        xyz = parse_lines(data)
        if len(xyz):
            yield xyz


def reduce_stream(stream):
    parts, total = [], 0
    for xyz in read_xyz(stream):
        total += len(xyz)
        parts.append(reduce_chunk(xyz))
    if parts:
        key, lon, lat, z = shoalest_per_cell(*(np.concatenate([p[i] for p in parts]) for i in range(4)))
    else:
        key = np.zeros(0, np.int64)
        lon = lat = np.zeros(0, np.int32)
        z = np.zeros(0, np.float32)
    return {
        'ix': (key % GRID_COLUMNS).astype(np.int32),
        'iy': (key // GRID_COLUMNS).astype(np.int32),
        'lon': lon,
        'lat': lat,
        'z': z,
        'step': np.float64(GRID_STEP),
    }, total


def save(path, reduced):
    """Written under a temporary name first: a half-written file must never
    carry the asset name, which is what tells a run the survey is done."""
    part = f'{path}.part'
    try:
        with open(part, 'wb') as fh:
            np.savez_compressed(fh, **reduced)
        os.replace(part, path)
    finally:
        if os.path.exists(part):
            os.remove(part)


def main():
    out = sys.argv[1]
    reduced, total = reduce_stream(sys.stdin.buffer)
    save(out, reduced)
    print(f'{out}: {total} soundings, {len(reduced["z"])} cells', flush=True)


if __name__ == '__main__':
    main()
