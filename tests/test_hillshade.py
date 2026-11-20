import json
import sqlite3

import numpy as np
import pytest

pytest.importorskip('osgeo')

from osgeo import gdal, ogr, osr  # noqa: E402

from odc import attribution, hillshade, index  # noqa: E402
from odc.grid import Unit, mercator_pixel, tile_lon  # noqa: E402
from odc.osm import read_water  # noqa: E402

A, B = Unit(492, 288), Unit(493, 288)
EDGE = tile_lon(B.x, 9)
HILL = (EDGE, -22.27)


def wgs84():
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def write_dem(tmp_path, height=800.0):
    """A round hill across the edge between A and B, on a 3 arcsec grid."""
    step = 1 / 1200
    west, north, cols, rows = 165.9, -21.9, 1800, 960
    lon = west + (np.arange(cols) + 0.5) * step
    lat = north - (np.arange(rows) + 0.5) * step
    dx, dy = np.meshgrid(lon - HILL[0], lat - HILL[1])
    dem = height * np.exp(-(dx**2 + dy**2) / (2 * 0.08**2))
    path = str(tmp_path / 'relief.tif')
    ds = gdal.GetDriverByName('GTiff').Create(path, cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((west, step, 0, north, 0, -step))
    ds.SetProjection(wgs84().ExportToWkt())
    ds.GetRasterBand(1).WriteArray(dem.astype(np.float32))
    ds = None
    return path


def write_land(tmp_path, polygons):
    path = str(tmp_path / 'land.gpkg')
    source = ogr.GetDriverByName('GPKG').CreateDataSource(path)
    layer = source.CreateLayer('land', wgs84(), ogr.wkbPolygon)
    for minlon, minlat, maxlon, maxlat in polygons:
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for lon, lat in ((minlon, minlat), (maxlon, minlat), (maxlon, maxlat), (minlon, maxlat), (minlon, minlat)):
            ring.AddPoint_2D(lon, lat)
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(polygon)
        layer.CreateFeature(feature)
    source = None
    return path


EVERYWHERE = (165.0, -23.5, 168.0, -21.0)


def render_unit(dem, land, unit, z):
    k = 1 << (z - 9)
    return hillshade.render(dem, land, unit.x * k * 256, unit.y * k * 256, k * 256, k * 256, z)


@pytest.mark.parametrize('z', [10, hillshade.MAXZOOM])
def test_neighbouring_units_shade_as_one_window_would(tmp_path, z):
    dem, land = write_dem(tmp_path), write_land(tmp_path, [EVERYWHERE])
    k = 1 << (z - 9)
    both = hillshade.render(dem, land, A.x * k * 256, A.y * k * 256, 2 * k * 256, k * 256, z)
    apart = np.concatenate([render_unit(dem, land, A, z), render_unit(dem, land, B, z)], axis=1)
    assert both[..., 3].any()
    assert np.array_equal(both, apart)


def test_slopes_facing_north_west_are_lit_and_south_east_shaded(tmp_path):
    z = 11
    dem, land = write_dem(tmp_path), write_land(tmp_path, [EVERYWHERE])
    k = 1 << (z - 9)
    rgba = render_unit(dem, land, B, z)
    hill_px = (np.array(mercator_pixel(*HILL, z)) - (B.x * k * 256, B.y * k * 256)).astype(int)
    offset = 60
    north_west = rgba[hill_px[1] - offset, hill_px[0] + 1]
    south_east = rgba[hill_px[1] + offset, hill_px[0] + offset]
    assert tuple(north_west[:3]) == (255, 255, 255) and north_west[3] > 0
    assert tuple(south_east[:3]) == (0, 0, 0) and south_east[3] > 0


def test_water_and_flat_ground_stay_transparent(tmp_path):
    z = 10
    dem = write_dem(tmp_path)
    # Land only west of the hill's top: everything east of it is water.
    land = write_land(tmp_path, [(165.0, -23.5, HILL[0] - 0.05, -21.0)])
    assert not render_unit(dem, land, B, z)[..., 3].any()
    flat = render_unit(write_dem(tmp_path, height=0.0), write_land(tmp_path, [EVERYWHERE]), A, z)
    assert not flat[..., 3].any()


def test_inland_water_inside_the_land_stays_transparent(tmp_path):
    z = 11
    dem, land = write_dem(tmp_path), write_land(tmp_path, [EVERYWHERE])
    k = 1 << (z - 9)
    # A lake on the hill's south-east slope, which is shaded around it.
    lake = (HILL[0] + 0.03, HILL[1] - 0.06, HILL[0] + 0.07, HILL[1] - 0.03)
    ring = [[lake[0], lake[1]], [lake[2], lake[1]], [lake[2], lake[3]], [lake[0], lake[3]], [lake[0], lake[1]]]
    water_path = tmp_path / 'water.geojsonseq'
    water_path.write_text(
        json.dumps({'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': [ring]}}) + '\n'
    )
    water = read_water(str(water_path), B.build_box.bbox)
    dry = render_unit(dem, land, B, z)
    wet = hillshade.render(dem, land, B.x * k * 256, B.y * k * 256, k * 256, k * 256, z, water)
    origin = np.array([B.x * k * 256, B.y * k * 256])
    (x0, y0), (x1, y1) = (
        (np.array(mercator_pixel(lon, lat, z)) - origin).astype(int)
        for lon, lat in ((lake[0], lake[3]), (lake[2], lake[1]))
    )
    inside = (slice(y0 + 2, y1 - 2), slice(x0 + 2, x1 - 2))
    assert dry[inside][..., 3].any()
    assert not wet[inside][..., 3].any()
    outside = (slice(y1 + 2, y1 + 10), slice(x0, x1))
    assert np.array_equal(dry[outside], wet[outside])


def test_a_unit_file_holds_only_its_own_tiles_in_tms_rows(tmp_path):
    dem, land = write_dem(tmp_path), write_land(tmp_path, [EVERYWHERE])
    out = str(tmp_path / hillshade.asset_name(A.id, 'v1'))
    count = hillshade.write_unit([dem], land, out, A, 'v1')
    with sqlite3.connect(out) as db:
        rows = db.execute('SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles').fetchall()
        meta = dict(db.execute('SELECT name, value FROM metadata').fetchall())
    assert count == len(rows) > 0
    assert {z for z, *_ in rows} == set(range(hillshade.MINZOOM, hillshade.MAXZOOM + 1))
    for z, x, row, data in rows:
        k = 1 << (z - 9)
        y = (1 << z) - 1 - row
        assert A.x * k <= x < (A.x + 1) * k and A.y * k <= y < (A.y + 1) * k
        assert data[:4] == b'RIFF' and data[8:12] == b'WEBP'
    assert meta['format'] == 'webp' and meta['type'] == 'overlay'
    assert attribution.COPERNICUS_DEM in meta['attribution'] and attribution.OSM in meta['attribution']
    assert meta['x-odc-unit'] == A.id and meta['x-odc-version'] == 'v1'
    assert meta['minzoom'] == str(hillshade.MINZOOM) and meta['maxzoom'] == str(hillshade.MAXZOOM)


def test_a_unit_without_relief_or_shading_gets_no_file(tmp_path):
    land = write_land(tmp_path, [EVERYWHERE])
    out = tmp_path / 'none.mbtiles'
    out.write_bytes(b'an earlier build')
    assert hillshade.write_unit([], land, str(out), A, 'v1') == 0
    assert not out.exists()
    out.write_bytes(b'an earlier build')
    assert hillshade.write_unit([write_dem(tmp_path, height=0.0)], land, str(out), A, 'v1') == 0
    assert not out.exists()


def built_unit(tmp_path, unit, version, shaded):
    (tmp_path / index.asset_name(unit.id, version)).write_bytes(b'unit')
    (tmp_path / f'{unit.id}.json').write_text(json.dumps({'surveys': [], 'quality': None}))
    if shaded:
        (tmp_path / hillshade.asset_name(unit.id, version)).write_bytes(b'shade')


def test_the_index_lists_a_unit_s_shading_only_when_it_has_one(tmp_path):
    region = {'id': 'test', 'name': 'Test', 'bbox': [165.9, -22.6, 167.3, -21.95]}
    built_unit(tmp_path, A, 'aaaa', shaded=True)
    built_unit(tmp_path, B, 'bbbb', shaded=False)
    shaded = index.unit_entry(region, A.id, 'aaaa', str(tmp_path), '2026-11-20')
    plain = index.unit_entry(region, B.id, 'bbbb', str(tmp_path), '2026-11-20')
    assert shaded['hillshade']['url'].endswith('/units-test/z9-492-288.aaaa.hillshade.mbtiles')
    assert shaded['hillshade']['size'] == 5
    assert (shaded['hillshade']['minzoom'], shaded['hillshade']['maxzoom']) == (9, 12)
    assert 'hillshade' not in plain


def test_a_replaced_unit_retires_its_shading_with_it():
    def entry(version):
        base = f'https://example.org/units-test/{A.id}.{version}'
        return {'region': 'test', 'url': base + '.mbtiles', 'hillshade': {'url': base + '.hillshade.mbtiles'}}

    old = {'units': [entry('old')]}
    new = {'units': [entry('new')]}
    names = {f'{A.id}.old.mbtiles', f'{A.id}.old.hillshade.mbtiles'}
    ledger, _ = index.retire(old, new, {}, '2026-11-20T00:00:00+00:00', 'test', names)
    assert set(ledger) == names
