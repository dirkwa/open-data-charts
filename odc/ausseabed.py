"""Geoscience Australia's AusSeabed survey grids: one archive per survey on
files.ausseabed.gov.au, listed with its licence in AusSeabed's marine data
register (a WFS of the survey footprints). Surveys under CC BY 4.0 that
reach a region listing `ausseabed-surveys` are mirrored; restricted ones
(state agencies, ports) and those the register links elsewhere are not.

An archive holds GeoTIFF grids of the survey, heights in metres, negative
under water, in UTM or WGS84, on mean sea level (EGM2008 or AHD; the AHO's
surveys are reduced to LAT and adjusted back to MSL). Where an archive
holds a grid labelled LAT beside one labelled MSL, the two carry the same
values, so the label is not trusted: every grid is read as MSL, which errs
on the shallow side. Each grid finer than MAX_RESOLUTION_M is reduced to
the shoalest sample per chart grid cell, as a Shom survey's soundings are,
moved from MSL to LAT with AIMS' Lowest Predicted Tide like the
AusBathyTopo models (odc.sources), and kept where it is shallower than the
depth the region charts from models at all.
"""

import hashlib
import io
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from osgeo import gdal, osr

from odc import shom
from odc.grid import GRID_COLUMNS, GRID_STEP
from odc.reduce_survey import COORD_SCALE, reduce_chunk, save, shoalest_per_cell

gdal.UseExceptions()

WFS = 'https://warehouse.ausseabed.gov.au/geoserver/wfs'
REGISTER = 'ausseabed:MARINEDATAREGISTER_ACQUISITIONS_INDEX'
FILES = 'https://files.ausseabed.gov.au/'
LICENCE = 'Creative Commons - Attribution 4.0 International'
# A grid no finer than the chart grid adds no shoal the 30 m models miss,
# yet gives every sample a cell of its own: a 32 m grid of the Great Barrier
# Reef shelf alone would hold hundreds of millions.
MAX_RESOLUTION_M = 20.0
METRES_PER_DEGREE = 111_320.0
# The models' limit (odc.sources.AUSSEABED_DEEP_M): deeper water is left to ETOPO.
DEEP_M = 300.0
TECSOU = {'multibeam': 'multi-beam', 'singlebeam': 'single-beam'}
NOT_DEPTHS = re.compile(r'backscatter|intensity|_hs\.|hillshade', re.IGNORECASE)
LABELLED_LAT = re.compile(r'_LAT_')
HEAD_THREADS = 8
# Larger values are nodata markers a grid does not declare.
MAX_ABS_HEIGHT_M = 9000.0
MERGE_ROWS = 10_000_000
# Samples read at a time: 1024 x 1024, so a block's transformed points stay small.
BLOCK = 1024


def register_url(bbox):
    w, s, e, n = bbox
    cql = f"BBOX(GEOM,{w},{s},{e},{n},'EPSG:4326') AND LEGAL_CONSTRAINTS='{LICENCE}'"
    query = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': REGISTER,
        'outputFormat': 'application/json',
        'srsName': 'EPSG:4326',
        'CQL_FILTER': cql,
    }
    return f'{WFS}?{urllib.parse.urlencode(query)}'


def geometry_bbox(geometry):
    lons, lats = [], []

    def walk(coords):
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0])
            lats.append(coords[1])
        else:
            for c in coords:
                walk(c)

    walk(geometry['coordinates'])
    return [min(lons), min(lats), max(lons), max(lats)]


def finest_resolution(name):
    """The finest grid resolution the register's name gives, in metres, or None."""
    found = [float(v.replace('_', '.')) for v in re.findall(r'(\d+(?:[._]\d+)?)\s*(?:-\s*\d+(?:\.\d+)?\s*)?m\b', name)]
    return min(found) if found else None


def day(value):
    return (value or '')[:10].replace('-', '')


def survey_name(url):
    stem = urllib.parse.unquote(url.rsplit('/', 1)[1]).removesuffix('zip').removesuffix('.')
    return 'GA-' + re.sub(r'[^A-Za-z0-9_-]', '-', stem)


def wanted(properties):
    """Why the register entry is left out, or None to mirror it."""
    url = properties.get('DATA_URL') or ''
    name = properties.get('NAME') or ''
    if properties.get('PRODUCT_STATUS') != 'PUBLISHED' or properties.get('EMBARGO') != 'No':
        return 'not published'
    if properties.get('LEGAL_CONSTRAINTS') != LICENCE:
        return 'licence'
    if not url.startswith(FILES) or not url.endswith('zip'):
        return 'not on files.ausseabed.gov.au'
    if 'backscatter' in name.lower():
        return 'backscatter'
    resolution = finest_resolution(name)
    if resolution is not None and resolution > MAX_RESOLUTION_M:
        return 'coarse'
    return None


def entries(features, bboxes):
    """The register's features to mirror, one per archive (a survey's
    bathymetry and backscatter entries often share it), with their regions."""
    found = {}
    for feature in features:
        properties = feature['properties']
        if wanted(properties) or not feature.get('geometry'):
            continue
        bbox = geometry_bbox(feature['geometry'])
        regions = sorted(r for r, box in bboxes.items() if shom.intersects(bbox, box))
        if not regions:
            continue
        url = properties['DATA_URL']
        if url in found:
            continue
        found[url] = {'properties': properties, 'bbox': bbox, 'regions': regions}
    return list(found.values())


def head(url):
    """The archive's size and ETag, or None where the register's link is broken."""
    try:
        request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': shom.USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            return int(response.headers['Content-Length']), response.headers.get('ETag', '')
    except Exception as error:
        print(f'ausseabed: {url}: {error}', flush=True)
        return None


def survey(entry, size, etag):
    p = entry['properties']
    url = p['DATA_URL']
    identity = {'url': url, 'size': size, 'etag': etag}
    w, s, e, n = entry['bbox']
    return {
        'provider': 'ausseabed',
        'name': survey_name(url),
        'url': url,
        'title': p['NAME'],
        'start': day(p.get('START_DATE')) or day(p.get('END_DATE')),
        'end': day(p.get('END_DATE')) or day(p.get('START_DATE')),
        'tecsou': ','.join(
            dict.fromkeys(
                code
                for kind in (p.get('BATHY_TYPES') or '').split('|')
                if (code := TECSOU.get(kind.strip().lower().replace('-', '')))
            )
        ),
        'kind': p.get('BATHY_TYPES') or '',
        'bbox': entry['bbox'],
        'geometry': {'type': 'Polygon', 'coordinates': [[[w, s], [e, s], [e, n], [w, n], [w, s]]]},
        'size': size,
        'regions': entry['regions'],
        'md5': hashlib.md5(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
    }


def discover(bboxes):
    """The CC BY surveys reaching the bboxes (region id -> bbox)."""
    union = [
        min(b[0] for b in bboxes.values()),
        min(b[1] for b in bboxes.values()),
        max(b[2] for b in bboxes.values()),
        max(b[3] for b in bboxes.values()),
    ]
    with shom.get(register_url(union), timeout=600) as response:
        features = json.load(response)['features']
    found = entries(features, bboxes)
    with ThreadPoolExecutor(HEAD_THREADS) as pool:
        heads = list(pool.map(lambda e: head(e['properties']['DATA_URL']), found))
    surveys = [survey(e, *h) for e, h in zip(found, heads, strict=True) if h]
    names = [s['name'] for s in surveys]
    if len(names) != len(set(names)):
        raise SystemExit('ausseabed: two archives share a survey name')
    print(
        f'ausseabed: {len(features)} CC BY register entries, {len(found)} archives in the regions, '
        f'{len(found) - len(surveys)} unreachable',
        flush=True,
    )
    return surveys


def depth_members(names):
    """The archive's depth grids. A grid also published as a Cloud Optimized
    GeoTIFF, or labelled both MSL and LAT, is read from one copy."""
    grids = [n for n in names if n.lower().endswith(('.tif', '.tiff')) and not NOT_DEPTHS.search(n)]
    grids = [n for n in grids if not LABELLED_LAT.search(n)] or grids
    cogs = [n for n in grids if 'cog' in n.lower()]
    return cogs or grids


def to_wgs84(ds):
    """Projected x, y of a grid to longitude, latitude; None for a grid in WGS84."""
    source = osr.SpatialReference(wkt=ds.GetProjection())
    if source.IsGeographic() and source.GetAuthorityCode(None) == '4326':
        return None
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)

    def convert(x, y):
        lonlat = np.asarray(transform.TransformPoints(np.column_stack([x, y])))
        return lonlat[:, 0], lonlat[:, 1]

    return convert


def resolution_m(ds):
    srs = osr.SpatialReference(wkt=ds.GetProjection())
    size = abs(ds.GetGeoTransform()[1])
    return size * METRES_PER_DEGREE if srs.IsGeographic() else size * srs.GetLinearUnits()


def soundings(path):
    """Each valid sample of the grid as lon, lat, depth below the grid's
    datum, reduced block by block to the shoalest per chart cell. Only valid
    samples are transformed, so a long, narrow transit costs no more than its
    data, whatever its extent."""
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    x0, dx, rx, y0, ry, dy = ds.GetGeoTransform()
    convert = to_wgs84(ds)
    for row in range(0, ds.RasterYSize, BLOCK):
        rows = min(BLOCK, ds.RasterYSize - row)
        for col in range(0, ds.RasterXSize, BLOCK):
            cols = min(BLOCK, ds.RasterXSize - col)
            h = band.ReadAsArray(col, row, cols, rows)
            # Water deeper than DEEP_M below MSL is deeper still below LAT.
            valid = np.isfinite(h) & (h > -DEEP_M) & (h < MAX_ABS_HEIGHT_M)
            if nodata is not None:
                valid &= h != nodata
            y, x = np.nonzero(valid)
            if not len(y):
                continue
            px, py = col + x + 0.5, row + y + 0.5
            gx, gy = x0 + px * dx + py * rx, y0 + px * ry + py * dy
            lon, lat = convert(gx, gy) if convert else (gx, gy)
            yield reduce_chunk(np.column_stack([lon, lat, -h[y, x].astype(np.float64)]))


def reduce_grids(paths, separation):
    """The grids' shoalest depth below LAT per chart cell, shallower than DEEP_M."""
    merged = (np.zeros(0, np.int64), np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.float32))
    parts, rows = [], 0
    for path in paths:
        for part in soundings(path):
            parts.append(part)
            rows += len(part[0])
            # Neighbouring blocks share only their edge cells, so merging as
            # it goes keeps memory near the survey's own cell count.
            if rows > MERGE_ROWS:
                merged, parts, rows = merge([merged, *parts]), [], 0
    key, lon, lat, z = merge([merged, *parts])
    # Depth below LAT is depth below MSL less MSL's height above LAT.
    depth = z - separation.at(lon / COORD_SCALE, lat / COORD_SCALE)
    keep = np.isfinite(depth) & (depth < DEEP_M)
    return {
        'ix': (key[keep] % GRID_COLUMNS).astype(np.int32),
        'iy': (key[keep] // GRID_COLUMNS).astype(np.int32),
        'lon': lon[keep],
        'lat': lat[keep],
        'z': depth[keep].astype(np.float32),
        'step': np.float64(GRID_STEP),
    }


def merge(parts):
    return shoalest_per_cell(*(np.concatenate([p[i] for p in parts]) for i in range(4)))


def fine_enough(url, member):
    """Whether a grid is finer than MAX_RESOLUTION_M, read from its header
    without fetching the grid."""
    ds = gdal.Open(f'/vsizip/{{/vsicurl/{url}}}/{member}')
    if resolution_m(ds) <= MAX_RESOLUTION_M:
        return True
    print(f'{member}: coarser than {MAX_RESOLUTION_M:g} m, left out', flush=True)
    return False


def reduce_survey(survey, out_path, separation):
    from odc.sources import RemoteFile, save_zip_members  # GDAL's other inputs only where a survey needs them

    with tempfile.TemporaryDirectory(dir=os.path.dirname(out_path)) as tmp:
        url = survey['url'].replace(' ', '%20')
        with zipfile.ZipFile(io.BufferedReader(RemoteFile(url), 1 << 16)) as archive:
            members = depth_members(archive.namelist())
        if not members:
            raise ValueError(f'{survey["name"]}: no depth grid in the archive')
        members = [m for m in members if fine_enough(url, m)]
        save_zip_members(url, members, tmp)
        source_bytes = sum(os.path.getsize(os.path.join(tmp, m)) for m in members)
        reduced = reduce_grids([os.path.join(tmp, m) for m in members], separation)
    save(out_path, reduced)
    return {
        'cells': len(reduced['z']),
        'grids': len(members),
        'sourceBytes': source_bytes,
    }
