"""The global chart grid and the z9 Web-Mercator units built on it.

Every depth, sounding and land mask is computed on one worldwide grid of
GRID_STEP degrees, counted from (-180, 90): column ix grows east, row iy
grows south. A z9 tile is exactly CELLS_PER_UNIT_X columns wide, so units
start and end on grid lines in longitude; their latitude edges follow
Mercator and fall inside a row.
"""

import math
from dataclasses import dataclass

import numpy as np

GRID_STEP = 0.000125
CELLS_PER_DEGREE = 8000
UNIT_ZOOM = 9
CELLS_PER_UNIT_X = 5625
GRID_COLUMNS = 360 * CELLS_PER_DEGREE
# Grid-derived work (interpolation, smoothing, near-shore DEM fill, contour
# continuity) needs this many cells beyond a unit's edge to come out the
# same on both sides of it.
UNIT_MARGIN_CELLS = 64
# Build boxes snap outward to multiples of this, so averaging windows line
# up across units.
BOX_ALIGN_CELLS = 64
_EDGE_EPS = 1e-6


def _floor(v):
    return math.floor(v + _EDGE_EPS)


def _ceil(v):
    return math.ceil(v - _EDGE_EPS)


def cell_x(lon):
    return np.floor((np.asarray(lon, np.float64) + 180.0) * CELLS_PER_DEGREE).astype(np.int64)


def cell_y(lat):
    return np.floor((90.0 - np.asarray(lat, np.float64)) * CELLS_PER_DEGREE).astype(np.int64)


def lon_of(ix):
    return ix / CELLS_PER_DEGREE - 180.0


def lat_of(iy):
    return 90.0 - iy / CELLS_PER_DEGREE


def tile_lon(x, z):
    return x / 2**z * 360.0 - 180.0


def tile_lat(y, z):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2**z))))


def mercator_pixel(lon, lat, z):
    """Global Web-Mercator pixel coordinates (256 px tiles) at zoom z."""
    size = 256.0 * 2**z
    lon = np.asarray(lon, np.float64)
    lat = np.radians(np.asarray(lat, np.float64))
    px = (lon + 180.0) / 360.0 * size
    py = (1.0 - np.log(np.tan(lat) + 1.0 / np.cos(lat)) / math.pi) / 2.0 * size
    return px, py


@dataclass(frozen=True)
class GridBox:
    """Half-open cell range [ix0, ix1) x [iy0, iy1) of the global grid."""

    ix0: int
    iy0: int
    ix1: int
    iy1: int

    @property
    def ncols(self):
        return self.ix1 - self.ix0

    @property
    def nrows(self):
        return self.iy1 - self.iy0

    @property
    def bbox(self):
        """(minlon, minlat, maxlon, maxlat) of the cells' outer edges."""
        return lon_of(self.ix0), lat_of(self.iy1), lon_of(self.ix1), lat_of(self.iy0)

    @property
    def geotransform(self):
        minlon, _, _, maxlat = self.bbox
        return (minlon, GRID_STEP, 0.0, maxlat, 0.0, -GRID_STEP)

    def contains(self, ix, iy):
        return (ix >= self.ix0) & (ix < self.ix1) & (iy >= self.iy0) & (iy < self.iy1)

    def flat_index(self, ix, iy):
        return (iy - self.iy0) * self.ncols + (ix - self.ix0)

    @classmethod
    def around(cls, minlon, minlat, maxlon, maxlat):
        """The smallest box whose cells cover the given bounds."""
        return cls(
            _floor((minlon + 180.0) * CELLS_PER_DEGREE),
            _floor((90.0 - maxlat) * CELLS_PER_DEGREE),
            _ceil((maxlon + 180.0) * CELLS_PER_DEGREE),
            _ceil((90.0 - minlat) * CELLS_PER_DEGREE),
        )

    def grown(self, cells, align=1):
        return GridBox(
            (self.ix0 - cells) // align * align,
            (self.iy0 - cells) // align * align,
            -((-(self.ix1 + cells)) // align) * align,
            -((-(self.iy1 + cells)) // align) * align,
        )


@dataclass(frozen=True, order=True)
class Unit:
    x: int
    y: int

    @property
    def id(self):
        return f'z{UNIT_ZOOM}-{self.x}-{self.y}'

    @classmethod
    def parse(cls, unit_id):
        z, x, y = unit_id.split('-')
        if z != f'z{UNIT_ZOOM}':
            raise ValueError(f'not a z{UNIT_ZOOM} unit: {unit_id}')
        return cls(int(x), int(y))

    @property
    def bounds(self):
        """(minlon, minlat, maxlon, maxlat)."""
        return (
            tile_lon(self.x, UNIT_ZOOM),
            tile_lat(self.y + 1, UNIT_ZOOM),
            tile_lon(self.x + 1, UNIT_ZOOM),
            tile_lat(self.y, UNIT_ZOOM),
        )

    @property
    def cells(self):
        return GridBox.around(*self.bounds)

    @property
    def build_box(self):
        return self.cells.grown(UNIT_MARGIN_CELLS, BOX_ALIGN_CELLS)

    def owns(self, lon, lat):
        """Points inside the unit; each point on the earth has one owner."""
        minlon, minlat, maxlon, maxlat = self.bounds
        lon = np.asarray(lon)
        lat = np.asarray(lat)
        return (lon >= minlon) & (lon < maxlon) & (lat > minlat) & (lat <= maxlat)


def unit_at(lon, lat):
    px, py = mercator_pixel(lon, lat, UNIT_ZOOM)
    return Unit(int(px // 256), int(py // 256))


def units_in(minlon, minlat, maxlon, maxlat):
    """Every unit that intersects the given bounds."""
    nw, se = unit_at(minlon, maxlat), unit_at(maxlon, minlat)
    return [Unit(x, y) for y in range(nw.y, se.y + 1) for x in range(nw.x, se.x + 1)]
