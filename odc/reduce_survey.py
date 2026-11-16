"""Reduce one Shom survey XYZ stream to the shoalest sounding per cell of
the global chart grid.

Input on stdin: three header lines, then `lon,lat,depth` with depth in m,
down positive, LAT (drying heights negative). The caller streams it out of
the survey's 7z: `7z x -so S….7z '*.xyz' -r | python3 -m odc.reduce_survey out.npz`.

The .npz holds, one entry per occupied cell: ix, iy (int32 global grid
indices), lon, lat (int32, 1e-7 degrees, the sounding's own position) and
z (float32 depth).
"""

import sys

import numpy as np

from odc.grid import CELLS_PER_DEGREE, GRID_COLUMNS, GRID_STEP

CHUNK = 64 << 20
COORD_SCALE = 10_000_000


def shoalest_per_cell(key, *cols):
    """Keep one entry per key: the one with the smallest depth (last column)."""
    order = np.lexsort((cols[-1], key))
    key = key[order]
    first = np.ones(len(key), bool)
    first[1:] = key[1:] != key[:-1]
    return (key[first], *(c[order][first] for c in cols))


def is_data_line(line):
    parts = line.replace(b',', b' ').replace(b';', b' ').split()
    if len(parts) != 3:
        return False
    try:
        [float(p) for p in parts]
    except ValueError:
        return False
    return True


def reduce_chunk(xyz):
    lon, lat, z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    ok = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(z) & (np.abs(lon) <= 180) & (np.abs(lat) < 90)
    lon, lat, z = lon[ok], lat[ok], z[ok]
    ix = np.floor((lon + 180.0) * CELLS_PER_DEGREE).astype(np.int64) % GRID_COLUMNS
    iy = np.floor((90.0 - lat) * CELLS_PER_DEGREE).astype(np.int64)
    key = iy * GRID_COLUMNS + ix
    return shoalest_per_cell(
        key,
        np.round(lon * COORD_SCALE).astype(np.int32),
        np.round(lat * COORD_SCALE).astype(np.int32),
        z.astype(np.float32),
    )


def read_xyz(stream):
    rest = b''
    header_done = False
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
        if not header_done:
            lines = data.split(b'\n')
            i = 0
            while i < len(lines) and not is_data_line(lines[i]):
                i += 1
            if i == len(lines):
                continue
            data = b'\n'.join(lines[i:])
            header_done = True
        values = np.fromstring(data.replace(b',', b' ').replace(b';', b' '), dtype=np.float64, sep=' ')
        if len(values) % 3:
            raise ValueError(f'malformed XYZ chunk ({len(values)} values)')
        yield values.reshape(-1, 3)


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
    with open(path, 'wb') as fh:
        np.savez_compressed(fh, **reduced)


def main():
    out = sys.argv[1]
    reduced, total = reduce_stream(sys.stdin.buffer)
    save(out, reduced)
    print(f'{out}: {total} soundings, {len(reduced["z"])} cells', flush=True)


if __name__ == '__main__':
    main()
