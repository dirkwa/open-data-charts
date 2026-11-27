"""The Estonian Transport Administration's spot soundings, for regions listing
`vta-his-depth`.

HIS publishes its soundings only as a WMS layer (`sea_dp`), drawn on the
Estonian grid (EPSG:3301) and not queryable. Its `black` style draws every
digit as the same bitmap, so the soundings are read back from the images by
exact glyph matching: an integer part of 8-pixel digits followed by a 6-pixel
decimal ("24₅" is 24.5 m). A reading that disagrees with the soundings around
it, almost always a label half-hidden under another, is dropped.

The mirror treats each BLOCK_M square that HIS draws soundings or contours in
as one survey, named after its south-west corner. HIS gives no dates or
versions for its data, so a survey's identity is its square and EDITION:
bumping EDITION reads every square again.
"""

import hashlib
import json
import math
import sys
import time
import urllib.parse
import uuid

import numpy as np
from osgeo import gdal

from odc import shom, vta_his
from odc.reduce_survey import reduce_rows, save

gdal.UseExceptions()

HIS_WMS = 'https://his.vta.ee:8443/HIS/WMS'
# The day the squares were read; the soundings' SORDAT and survey date.
EDITION = '20261127'
BLOCK_M = 10000
TILE_M = 2000
# HIS thins its soundings to the scale it draws at; at 1 m a pixel it draws
# them about every 30 m, and labels still overlap little.
METRES_PER_PX = 1.0
# Labels near a tile's edge are read from the tile they sit inside: each
# tile is drawn this much larger on every side.
MARGIN_PX = 40
# Discovery looks for anything drawn at 10 m a pixel, a scale at which HIS
# still draws soundings, so a square with soundings but no contour is found.
OVERVIEW_M = 20000
OVERVIEW_PX = 2000
# Pause between two requests, and at most WORKERS squares read at once, so
# HIS sees no more than a couple of clients.
PAUSE_S = 0.2
WORKERS = 2
NEIGHBOUR_M = 40.0
MIN_NEIGHBOURS = 3
# A reading further than this from its neighbours' median is not trusted.
MAX_DEVIATION_M = 2.0
MAX_DEVIATION_SHARE = 0.15
# The Estonian waters are nowhere deeper than this; two labels side by side
# that read as one number are not.
MAX_DEPTH_M = 300.0
MAX_DIGITS = 3
# An estimate for sharding: about 0.4 MB a tile.
ESTIMATED_BYTES = (BLOCK_M // TILE_M) ** 2 * 400_000

BIG = {
    '0': ('.###.', '.#.##', '##..#', '##..#', '##..#', '##..#', '.#.##', '.###.'),
    '1': ('.##', '###', '#.#', '..#', '..#', '..#', '..#', '..#'),
    '2': ('.###.', '##.##', '....#', '...##', '...#.', '..#..', '.#...', '#####'),
    '3': ('#####', '.#.##', '....#', '.###.', '...##', '....#', '#..##', '####.'),
    '4': ('...##.', '..###.', '..###.', '.#.##.', '.#.##.', '######', '...##.', '...##.'),
    '5': ('.####', '.#...', '.#...', '.####', '...##', '....#', '#..##', '####.'),
    '6': ('..###', '.##..', '.#...', '#####', '##..#', '##..#', '.#..#', '.####'),
    '7': ('#####', '....#', '...##', '...#.', '...#.', '..##.', '..#..', '.##..'),
    '8': ('.###.', '.#.##', '.#..#', '.###.', '.####', '##..#', '##..#', '.####'),
    '9': ('.###.', '.#.##', '##..#', '##..#', '.####', '....#', '...##', '.###.'),
}
SMALL = {
    '0': ('.###', '##.#', '#..#', '#..#', '##.#', '.###'),
    '1': ('..#', '###', '..#', '..#', '..#', '..#'),
    '2': ('.###', '#..#', '...#', '..#.', '.#..', '####'),
    '3': ('####', '...#', '.##.', '...#', '...#', '####'),
    '4': ('..##', '..##', '.#.#', '.#.#', '####', '...#'),
    '5': ('.###', '.#..', '####', '...#', '...#', '####'),
    '6': ('.###', '.#..', '#.##', '##.#', '##.#', '.###'),
    '7': ('####', '...#', '..##', '..#.', '..#.', '.#..'),
    '8': ('.###', '##.#', '.###', '.###', '#..#', '.###'),
    '9': ('.###', '##.#', '#..#', '.###', '...#', '.##.'),
}
BIG_H, SMALL_H = 8, 6
# The decimal starts this far below the top of the integer part.
DECIMAL_DROP = range(2, 7)
MAX_GAP_PX = 2


def bitmaps(glyphs):
    return {digit: np.array([[c == '#' for c in row] for row in rows]) for digit, rows in glyphs.items()}


BIG_BITMAPS, SMALL_BITMAPS = bitmaps(BIG), bitmaps(SMALL)


def components(mask):
    """(top, left, bitmap) of each 8-connected component of a boolean image."""
    seen = np.zeros_like(mask)
    height, width = mask.shape
    out = []
    for y0, x0 in zip(*np.nonzero(mask), strict=True):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        stack, pixels = [(int(y0), int(x0))], []
        while stack:
            y, x = stack.pop()
            pixels.append((y, x))
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys, xs = np.array(pixels).T
        top, left = ys.min(), xs.min()
        bitmap = np.zeros((ys.max() - top + 1, xs.max() - left + 1), bool)
        bitmap[ys - top, xs - left] = True
        out.append((int(top), int(left), bitmap))
    return out


def match(bitmap, glyphs):
    for digit, glyph in glyphs.items():
        if bitmap.shape == glyph.shape and (bitmap == glyph).all():
            return digit
    return None


def split_digits(bitmap):
    """Touching integer digits, peeled off from the left: [(offset, digit, width)], None if any part is unknown."""
    out, x = [], 0
    while x < bitmap.shape[1]:
        for digit, glyph in BIG_BITMAPS.items():
            w = glyph.shape[1]
            part = bitmap[:, x : x + w]
            if part.shape == glyph.shape and (part == glyph).all():
                out.append((x, digit, w))
                x += w
                break
        else:
            return None
        while x < bitmap.shape[1] and not bitmap[:, x].any():
            x += 1
    return out


def read_labels(mask):
    """(column, row, depth) of each sounding label in a tile's opaque pixels,
    at the centre of its integer part."""
    digits, decimals, unread = [], {}, np.zeros_like(mask)
    for top, left, bitmap in components(mask):
        height = bitmap.shape[0]
        if height == BIG_H:
            digit = match(bitmap, BIG_BITMAPS)
            parts = [(0, digit, bitmap.shape[1])] if digit else split_digits(bitmap)
            if parts:
                digits += [(top, left + dx, left + dx + w, d) for dx, d, w in parts]
                continue
        elif height == SMALL_H:
            digit = match(bitmap, SMALL_BITMAPS)
            if digit:
                decimals.setdefault(left, []).append((top, digit))
                continue
        unread[top : top + height, left : left + bitmap.shape[1]] |= bitmap
    digits.sort()
    labels, used = [], set()
    for i, first in enumerate(digits):
        if i in used:
            continue
        group = [first]
        for j in range(i + 1, len(digits)):
            other = digits[j]
            if other[0] != first[0]:
                break
            if j not in used and 0 <= other[1] - group[-1][2] <= MAX_GAP_PX:
                group.append(other)
                used.add(j)
        labels.append(group)
    out = []
    for group in labels:
        top, left, right = group[0][0], group[0][1], group[-1][2]
        if len(group) > MAX_DIGITS:
            continue
        # A lone digit touching unread pixels is usually what is left of a
        # label under another.
        if len(group) == 1 and unread[max(0, top - 3) : top + BIG_H + 3, max(0, left - 3) : right + 3].any():
            continue
        decimal = next(
            (
                digit
                for x in range(right, right + MAX_GAP_PX + 1)
                for dtop, digit in decimals.get(x, [])
                if dtop - top in DECIMAL_DROP
            ),
            None,
        )
        depth = float(''.join(d for *_, d in group) + (f'.{decimal}' if decimal else ''))
        if depth <= MAX_DEPTH_M:
            out.append(((left + right) / 2, top + BIG_H / 2, depth))
    return out


def plausible(xy, depth):
    """True for each sounding that agrees with its neighbours, or has too few to judge."""
    keep = np.ones(len(depth), bool)
    if not len(depth):
        return keep
    cells = np.floor(xy / NEIGHBOUR_M).astype(np.int64)
    buckets = {}
    for i, (cx, cy) in enumerate(cells):
        buckets.setdefault((int(cx), int(cy)), []).append(i)
    for i, (cx, cy) in enumerate(cells):
        near = [
            j
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for j in buckets.get((int(cx) + dx, int(cy) + dy), [])
            if j != i and math.dist(xy[i], xy[j]) <= NEIGHBOUR_M
        ]
        if len(near) >= MIN_NEIGHBOURS:
            median = float(np.median(depth[near]))
            if abs(depth[i] - median) > max(MAX_DEVIATION_M, MAX_DEVIATION_SHARE * median):
                keep[i] = False
    return keep


def get_map(layer, style, box, width, height):
    minx, miny, maxx, maxy = box
    params = {
        'SERVICE': 'WMS',
        'VERSION': '1.1.0',
        'REQUEST': 'GetMap',
        'LAYERS': layer,
        'STYLES': style,
        'SRS': 'EPSG:3301',
        'BBOX': f'{minx},{miny},{maxx},{maxy}',
        'WIDTH': width,
        'HEIGHT': height,
        'FORMAT': 'image/png',
        'TRANSPARENT': 'TRUE',
    }
    with shom.get(f'{HIS_WMS}?{urllib.parse.urlencode(params)}', timeout=300) as response:
        return response.read()


def opaque(png, width, height):
    """The drawn pixels of a transparent PNG."""
    if not png.startswith(b'\x89PNG'):
        # An error comes back as HTML or XML with a 200 now and then; it must
        # not read as an empty square.
        raise OSError(f'HIS WMS: not a PNG: {png[:200]!r}')
    path = f'/vsimem/his-{uuid.uuid4().hex}.png'
    gdal.FileFromMemBuffer(path, png)
    try:
        ds = gdal.Open(path)
        if (ds.RasterXSize, ds.RasterYSize) != (width, height):
            raise OSError(f'HIS WMS: {ds.RasterXSize}x{ds.RasterYSize} image, {width}x{height} asked for')
        if ds.RasterCount in (2, 4):
            return ds.GetRasterBand(ds.RasterCount).ReadAsArray() > 0
        band = ds.GetRasterBand(1)
        table = band.GetColorTable()
        if table is None:
            raise OSError('HIS WMS: an image without transparency')
        alpha = np.array([table.GetColorEntry(i)[3] for i in range(table.GetCount())] + [0] * 256)
        return alpha[band.ReadAsArray()] > 0
    finally:
        ds = None
        gdal.Unlink(path)


def tile_soundings(box, get=get_map):
    """(easting, northing, depth, inside) of the soundings HIS draws in an
    EPSG:3301 box and its margin; inside is True for those in the box."""
    minx, miny, maxx, maxy = box
    m = MARGIN_PX * METRES_PER_PX
    width = round((maxx - minx) / METRES_PER_PX) + 2 * MARGIN_PX
    height = round((maxy - miny) / METRES_PER_PX) + 2 * MARGIN_PX
    mask = opaque(get('sea_dp', 'black', (minx - m, miny - m, maxx + m, maxy + m), width, height), width, height)
    out = []
    for column, row, depth in read_labels(mask):
        x = minx - m + column * METRES_PER_PX
        y = maxy + m - row * METRES_PER_PX
        out.append((x, y, depth, minx <= x < maxx and miny < y <= maxy))
    return out


def block_soundings(block, get=get_map, pause=PAUSE_S):
    """The square's soundings and how many were dropped. The soundings in the
    tiles' margins outside the square are its neighbours' and only judge
    those near its edge."""
    minx, miny, maxx, maxy = block
    rows = []
    x = minx
    while x < maxx:
        y = miny
        while y < maxy:
            for sx, sy, depth, inside in tile_soundings((x, y, min(x + TILE_M, maxx), min(y + TILE_M, maxy)), get):
                beyond = not (minx <= sx < maxx and miny < sy <= maxy)
                if inside or beyond:
                    rows.append((sx, sy, depth, inside))
            time.sleep(pause)
            y += TILE_M
        x += TILE_M
    rows = np.array(rows, np.float64).reshape(-1, 4)
    own = rows[:, 3] > 0
    keep = plausible(rows[:, :2], rows[:, 2]) & own
    return rows[keep, :3], int((own & ~keep).sum())


def to_lon_lat(xyz):
    back, _ = vta_his.to_wgs84()
    if not len(xyz):
        return np.zeros((0, 3))
    lonlat = np.asarray(back.TransformPoints(xyz[:, :2].tolist()))
    return np.column_stack([lonlat[:, 0], lonlat[:, 1], xyz[:, 2]])


def survey_name(minx, miny):
    return f'vta-his-depth-{int(minx) // 1000}-{int(miny) // 1000}'


def survey(block, regions):
    minx, miny, maxx, maxy = block
    back, _ = vta_his.to_wgs84()
    corners = [back.TransformPoint(x, y)[:2] for x, y in ((minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy))]
    lons, lats = [c[0] for c in corners], [c[1] for c in corners]
    identity = {'block': list(block), 'edition': EDITION}
    return {
        'provider': 'vta-his-depth',
        'name': survey_name(minx, miny),
        'title': f'HIS spot soundings {int(minx) // 1000} km E, {int(miny) // 1000} km N',
        'block': list(block),
        'start': EDITION,
        'end': EDITION,
        'tecsou': '',
        'bbox': [min(lons), min(lats), max(lons), max(lats)],
        'geometry': {'type': 'Polygon', 'coordinates': [[*map(list, corners), list(corners[0])]]},
        'size': ESTIMATED_BYTES,
        'regions': regions,
        'md5': hashlib.md5(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
    }


def drawn_blocks(box, get=get_map, pause=PAUSE_S):
    """The BLOCK_M squares (on the global EPSG:3301 grid) that meet the box,
    as (square, drawn): drawn is True where HIS draws soundings or contours,
    None where the overview could not be read."""
    minx, miny, maxx, maxy = box
    found = {}
    per_block = OVERVIEW_PX * BLOCK_M // OVERVIEW_M
    n = OVERVIEW_M // BLOCK_M
    x = math.floor(minx / OVERVIEW_M) * OVERVIEW_M
    while x < maxx:
        y = math.floor(miny / OVERVIEW_M) * OVERVIEW_M
        while y < maxy:
            overview = (x, y, x + OVERVIEW_M, y + OVERVIEW_M)
            drawn = np.zeros((OVERVIEW_PX, OVERVIEW_PX), bool)
            try:
                for layer in ('sea_dp', 'sea_dl'):
                    drawn |= opaque(get(layer, '', overview, OVERVIEW_PX, OVERVIEW_PX), OVERVIEW_PX, OVERVIEW_PX)
                    time.sleep(pause)
            except OSError as error:
                print(f'HIS overview {overview}: {error}; its squares held', flush=True)
                drawn = None
            for i in range(n):
                for j in range(n):
                    bx, by = x + i * BLOCK_M, y + j * BLOCK_M
                    if not (bx < maxx and bx + BLOCK_M > minx and by < maxy and by + BLOCK_M > miny):
                        continue
                    square = (bx, by, bx + BLOCK_M, by + BLOCK_M)
                    if drawn is None:
                        found[square] = None
                        continue
                    # Rows run from the top, squares from the south.
                    rows = slice((n - 1 - j) * per_block, (n - j) * per_block)
                    if drawn[rows, i * per_block : (i + 1) * per_block].any():
                        found[square] = True
            y += OVERVIEW_M
        x += OVERVIEW_M
    return sorted(found.items())


def held(block, regions):
    """A square whose overview failed: the mirror keeps what it holds for it."""
    return {
        'provider': 'vta-his-depth',
        'name': survey_name(*block[:2]),
        'held': True,
        'regions': regions,
        'geometry': survey(block, regions)['geometry'],
    }


def discover(bboxes, get=get_map, pause=PAUSE_S):
    """A survey for each square HIS draws in that meets a region's bbox."""
    blocks = {}
    for region, bounds in bboxes.items():
        box = vta_his.estonian_box(bounds)
        if box is None:
            continue
        for block, drawn in drawn_blocks(box, get, pause):
            seen = blocks.setdefault(block, {'regions': [], 'drawn': drawn})
            seen['regions'].append(region)
    surveys = [
        survey(block, sorted(b['regions'])) if b['drawn'] else held(block, sorted(b['regions']))
        for block, b in sorted(blocks.items())
    ]
    unread = sum(1 for s in surveys if s.get('held'))
    print(f'vta-his-depth: {len(surveys)} squares in the regions, {unread} held', flush=True)
    return surveys


def reduce_survey(survey, out_path, get=get_map):
    xyz, dropped = block_soundings(tuple(survey['block']), get)
    reduced, soundings = reduce_rows([to_lon_lat(xyz)] if len(xyz) else [])
    save(out_path, reduced)
    return {'soundings': soundings, 'cells': len(reduced['z']), 'dropped': dropped}


def main():
    """Read one square into a reduced survey: python3 -m odc.his_depth <minx> <miny> <out.npz>."""
    minx, miny, out = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
    print(reduce_survey(survey((minx, miny, minx + BLOCK_M, miny + BLOCK_M), []), out))


if __name__ == '__main__':
    main()
