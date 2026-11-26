"""Which units a region has, and each unit's version: a hash of every input
that reaches the unit's build box, so a unit is rebuilt when, and only when,
something it is built from changes.

Usage:
  python3 -m odc.units plan <region.json> <inputs dir> <mirror-index.json> <old index or -> <out plan.json> [batch size]
  python3 -m odc.units surveys <mirror-index.json> <unit id> ...   (mirror assets the units read)
"""

import glob
import hashlib
import json
import os
import sys

import numpy as np
from osgeo import gdal, ogr

from odc import attribution, build_unit, chart, seamarks, shom, sources
from odc.grid import Unit, units_in
from odc.mirror import MIRROR_TAG

gdal.UseExceptions()
VERSION_HEX = 16
SEARCH_MARGIN_DEG = 0.05
ANTIMERIDIAN_LOOKUP = 180.0 - 1e-9
BATCH = 8
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Everything that turns a unit's inputs into its file; the reduction is
# covered by the reduced surveys' own hashes.
BUILDER_FILES = (
    'builder/Dockerfile',
    'odc/build_unit.py',
    'odc/chart.py',
    'odc/hillshade.py',
    'odc/linz_hydro_layers.json',
    'odc/grid.py',
    'odc/osm.py',
    'odc/tile.py',
    'odc/run_unit.py',
    'odc/seamarks.py',
)


def bounds_polygon(minlon, minlat, maxlon, maxlat):
    return build_unit.bbox_polygon(minlon, minlat, maxlon, maxlat)


def dem_has_data(paths, bounds):
    minlon, minlat, maxlon, maxlat = bounds
    for path in paths:
        ds = gdal.Open(path)
        x0, dx, _, y0, _, dy = ds.GetGeoTransform()
        c0 = max(0, int(np.floor((minlon - x0) / dx)))
        c1 = min(ds.RasterXSize, int(np.ceil((maxlon - x0) / dx)))
        r0 = max(0, int(np.floor((maxlat - y0) / dy)))
        r1 = min(ds.RasterYSize, int(np.ceil((minlat - y0) / dy)))
        if c1 <= c0 or r1 <= r0:
            continue
        band = ds.GetRasterBand(1)
        window = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0)
        if (window != band.GetNoDataValue()).any():
            return True
    return False


def discover(region, inputs):
    """The region's units that have water and something to chart near it:
    land, the region's bathymetric model, or a coastal or larger chart. Survey footprints do not count:
    old ocean crossings cover hundreds of units of open ocean in French
    Polynesia with a sounding every few miles."""
    land = ogr.Open(os.path.join(inputs, 'land.gpkg'))
    layer = land.GetLayer(0)
    dems = sources.dem_grids(inputs)
    charts = os.path.join(inputs, 'chart')
    units = []
    for unit in units_in(*region['bbox']):
        area = bounds_polygon(*unit.bounds)
        layer.SetSpatialFilter(area)
        # One union, not one difference per island: an archipelago unit
        # meets thousands of them, and differences one by one take minutes.
        land_parts = ogr.Geometry(ogr.wkbGeometryCollection)
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry.Intersects(area):
                land_parts.AddGeometry(geometry)
        layer.ResetReading()
        has_land = land_parts.GetGeometryCount() > 0
        water = area.Difference(land_parts.UnaryUnion()) if has_land else area
        # OSM's land polygons hold the lakes, which a chart's depth areas reach.
        if water.IsEmpty() and not chart.charted(charts, unit.bounds, depth_only=True):
            continue
        if has_land or dem_has_data(dems, unit.bounds) or chart.charted(charts, unit.bounds):
            units.append(unit)
    return units


class Digests:
    """Collects, per unit, the inputs that reach its build box."""

    def __init__(self, units):
        self.boxes = {unit.id: unit.build_box.bbox for unit in units}
        self.parts = {unit.id: [] for unit in units}

    def units_meeting(self, bbox):
        minlon, minlat, maxlon, maxlat = bbox
        m = SEARCH_MARGIN_DEG
        # Chart features east of the antimeridian keep longitudes past 180,
        # as the builder clips them; unit_at would wrap such an edge to the
        # far west and find no unit, so the lookup stops at the last column.
        west, east = (min(max(lon, -180.0), ANTIMERIDIAN_LOOKUP) for lon in (minlon - m, maxlon + m))
        for unit in units_in(west, minlat - m, east, maxlat + m):
            box = self.boxes.get(unit.id)
            if box is not None and shom.intersects(box, bbox):
                yield unit.id

    def add(self, bbox, text):
        for unit_id in self.units_meeting(bbox):
            self.parts[unit_id].append(text)

    def add_all(self, text):
        for parts in self.parts.values():
            parts.append(text)

    def versions(self):
        out = {}
        for unit_id, parts in self.parts.items():
            digest = hashlib.sha256()
            for text in sorted(parts):
                digest.update(text.encode())
                digest.update(b'\n')
            out[unit_id] = digest.hexdigest()[:VERSION_HEX]
        return out


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def add_feature_collection(digests, label, path, grow=None):
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    for feature in data.get('features') or []:
        if feature.get('geometry'):
            bbox = shom.bbox_of(feature['geometry'])
            digests.add(grow(bbox) if grow else bbox, f'{label} {canonical(feature)}')


def add_geojsonseq(digests, label, path):
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            feature = json.loads(line.lstrip('\x1e'))
            digests.add(shom.bbox_of(feature['geometry']), f'{label} {canonical(feature)}')


def add_aca(digests, paths):
    """A reef crossing a square's edge is in both squares' files: hashed once."""
    seen = set()
    for path in paths:
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if line not in seen:
                    seen.add(line)
                    feature = json.loads(line)
                    digests.add(shom.bbox_of(feature['geometry']), f'aca {canonical(feature)}')


def add_land(digests, path):
    source = ogr.Open(path)  # the layer is only valid while its data source lives
    for feature in source.GetLayer(0):
        geometry = feature.GetGeometryRef()
        minlon, maxlon, minlat, maxlat = geometry.GetEnvelope()
        digests.add((minlon, minlat, maxlon, maxlat), 'land ' + geometry.ExportToWkb().hex())


def add_raster_windows(digests, label, paths, margin=0.0):
    """Each unit hashes only the raster window the builder reads: its build box plus margin."""
    for path in paths:
        ds = gdal.Open(path)
        x0, dx, _, y0, _, dy = ds.GetGeoTransform()
        band = ds.GetRasterBand(1)
        name = os.path.basename(path)
        # How the builder reads the samples is part of the window.
        reading = f'{ds.GetGeoTransform()} {band.GetNoDataValue()}'
        for unit_id, box in digests.boxes.items():
            minlon, minlat, maxlon, maxlat = box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin
            c0 = max(0, int(np.floor((minlon - x0) / dx)))
            c1 = min(ds.RasterXSize, int(np.ceil((maxlon - x0) / dx)))
            r0 = max(0, int(np.floor((maxlat - y0) / dy)))
            r1 = min(ds.RasterYSize, int(np.ceil((minlat - y0) / dy)))
            if c1 <= c0 or r1 <= r0:
                continue
            window = band.ReadAsArray(c0, r0, c1 - c0, r1 - r0)
            digest = hashlib.sha256(f'{reading} {window.dtype}'.encode() + window.tobytes()).hexdigest()
            digests.parts[unit_id].append(f'{label} {name} {c0} {r0} {digest}')


def add_squares(digests, label, paths):
    """Whole rasters for the units meeting them: the builder sieves a
    satellite depth square as a whole (build_unit.square_bands), so any
    cell of it can change a unit's bands."""
    for path in paths:
        ds = gdal.Open(path)
        x0, dx, _, y0, _, dy = ds.GetGeoTransform()
        bbox = (x0, y0 + dy * ds.RasterYSize, x0 + dx * ds.RasterXSize, y0)
        band = ds.GetRasterBand(1)
        cells = band.ReadAsArray()
        reading = f'{ds.GetGeoTransform()} {band.GetNoDataValue()} {cells.dtype}'
        digest = hashlib.sha256(reading.encode() + cells.tobytes()).hexdigest()
        # The same test as build_unit.satellite_bands, on every box: a square
        # only touching a box takes no part in it.
        for unit_id, (minlon, minlat, maxlon, maxlat) in digests.boxes.items():
            if bbox[0] < maxlon and bbox[2] > minlon and bbox[1] < maxlat and bbox[3] > minlat:
                digests.parts[unit_id].append(f'{label} {os.path.basename(path)} {digest}')


def add_surveys(digests, mirror_index):
    for name, survey in mirror_index['surveys'].items():
        # start, end and tecsou set the survey's priority and quality category,
        # provider, title, product and doi its soundings' INFORM (Shom entries
        # have none of them).
        keys = ('start', 'end', 'tecsou') + tuple(k for k in ('provider', 'title', 'product', 'doi') if k in survey)
        meta = canonical({k: survey.get(k) for k in keys})
        digests.add(survey['bbox'], f'survey {name} {survey["reduced"]["sha256"]} {meta}')


def mosaic_reading(paths, **options):
    """The mosaic's own geotransform and nodata, which every unit reads through."""
    if not paths:
        return None
    mosaic = gdal.BuildVRT('/vsimem/units-dem.vrt', paths, **options)
    reading = mosaic.GetGeoTransform(), mosaic.GetRasterBand(1).GetNoDataValue()
    mosaic = None
    gdal.Unlink('/vsimem/units-dem.vrt')
    return reading


def unit_versions(units, inputs, mirror_index, builder, attribution_text):
    digests = Digests(units)
    digests.add_all(f'builder {builder}')
    digests.add_all(f'attribution {attribution_text}')
    seamarks_path = os.path.join(inputs, 'seamarks.geojsonseq')
    # OSM aids give way to Shom aids up to a mile beyond the build box.
    with_seamarks = os.path.exists(seamarks_path)
    for path in sorted(glob.glob(os.path.join(inputs, 'wfs', '*.json'))):
        aids = with_seamarks and build_unit.wfs_layer_class(build_unit.wfs_layer(path)) in seamarks.AID_CLASSES
        add_feature_collection(digests, 'wfs ' + os.path.basename(path), path, seamarks.aid_window if aids else None)
    for path in sorted(glob.glob(os.path.join(inputs, 'nc', '*.geojson'))):
        add_feature_collection(digests, 'nc ' + os.path.basename(path), path)
    for path in chart.chart_files(os.path.join(inputs, 'chart')):
        add_feature_collection(digests, 'chart ' + os.path.basename(path), path)
    add_aca(digests, sorted(glob.glob(os.path.join(inputs, 'aca', '*.geojsonseq'))))
    osm_path = os.path.join(inputs, 'osm.geojsonseq')
    if os.path.exists(osm_path):
        add_geojsonseq(digests, 'osm', osm_path)
    if os.path.exists(seamarks_path):
        add_geojsonseq(digests, 'seamarks', seamarks_path)
    water_path = os.path.join(inputs, 'water.geojsonseq')
    if os.path.exists(water_path):
        add_geojsonseq(digests, 'water', water_path)
    add_land(digests, os.path.join(inputs, 'land.gpkg'))
    dem = sources.dem_grids(inputs)
    # The mosaic's origin, pixel size and nodata depend on every grid, so they reach every unit.
    options = sources.DEM_MOSAIC_OPTIONS
    digests.add_all(f'dem mosaic {canonical(options)} {mosaic_reading(dem, **options)}')
    add_raster_windows(digests, 'dem', dem, build_unit.DEM_MARGIN_DEG)
    etopo = sorted(glob.glob(os.path.join(inputs, 'etopo', '*.tif')))
    add_raster_windows(digests, 'etopo', etopo, build_unit.ETOPO_MARGIN_DEG)
    add_squares(digests, 'aca-depth', sorted(glob.glob(os.path.join(inputs, 'aca-depth', '*.tif'))))
    relief = sorted(glob.glob(os.path.join(inputs, 'relief', '*.tif')))
    # The shading reads every unit through one mosaic of all relief tiles.
    if relief:
        digests.add_all(f'relief mosaic {mosaic_reading(relief)}')
    add_raster_windows(digests, 'relief', relief)
    add_surveys(digests, mirror_index)
    return digests.versions()


def plan(region, units, versions, old_index, batch=BATCH, attribution_text=None):
    """The units whose version is not the one the published index has."""
    published = {u['id']: u['version'] for u in (old_index or {}).get('units', []) if u.get('region') == region['id']}
    build = [u.id for u in units if published.get(u.id) != versions[u.id]]
    kept = [u.id for u in units if u.id not in build]
    removed = sorted(set(published) - {u.id for u in units})
    batches = [build[i : i + batch] for i in range(0, len(build), batch)]
    return {
        'region': region['id'],
        'attribution': attribution_text or attribution.of_region(region),
        'versions': {u.id: versions[u.id] for u in units},
        'build': build,
        'kept': kept,
        'removed': removed,
        'batches': [{'index': i, 'units': b} for i, b in enumerate(batches)],
    }


def attribution_texts(region, inputs):
    """The text unit versions hash, without dates, and the dated one units carry."""
    return attribution.of_region(region), attribution.of_region(region, attribution.dataset_dates(inputs))


def builder_id(root=ROOT):
    digest = hashlib.sha256()
    for name in BUILDER_FILES:
        with open(os.path.join(root, name), 'rb') as fh:
            digest.update(name.encode() + b'\0' + fh.read())
    return digest.hexdigest()


def surveys_for(mirror_index, unit_ids):
    boxes = [Unit.parse(u).build_box.bbox for u in unit_ids]
    surveys = mirror_index['surveys'].values()
    # An index written before the mirror spread over several releases has no release field.
    return sorted(
        f'{s["reduced"].get("release", MIRROR_TAG)} {s["reduced"]["asset"]}'
        for s in surveys
        if any(shom.intersects(s['bbox'], b) for b in boxes)
    )


def main():
    command, *args = sys.argv[1:]
    if command == 'surveys':
        mirror_index = json.load(open(args[0], encoding='utf-8'))
        print('\n'.join(surveys_for(mirror_index, args[1:])))
        return
    if command != 'plan':
        sys.exit(__doc__)
    region_path, inputs, mirror_path, old_path, out, *rest = args
    region = json.load(open(region_path, encoding='utf-8'))
    mirror_index = json.load(open(mirror_path, encoding='utf-8'))
    old_index = None if old_path == '-' else json.load(open(old_path, encoding='utf-8'))
    units = discover(region, inputs)
    hashed, dated = attribution_texts(region, inputs)
    versions = unit_versions(units, inputs, mirror_index, builder_id(), hashed)
    result = plan(region, units, versions, old_index, int(rest[0]) if rest else BATCH, dated)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, indent=1)
    print(f'{len(units)} units, {len(result["build"])} to build, {len(result["removed"])} removed')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
            fh.write(f'batches={json.dumps([b["index"] for b in result["batches"]])}\n')
            fh.write(f'build={len(result["build"])}\n')
            fh.write(f'removed={len(result["removed"])}\n')


if __name__ == '__main__':
    main()
