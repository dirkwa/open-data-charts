"""BSH's hydrographic survey database (SVDB) through its public portal: the
surveys of German waters, one dataset each, under CC0, not for navigation.

The portal is a WMS whose layers are the datasets (a short id, the survey
title, its dates and extent) plus the download service its viewer uses: a
session is opened for a layer, the server packs the layer as XYZ with a CSV
of its metadata, and the zip is fetched from the portal's files. The XYZ is
in ETRS89 / UTM (the CSV's srid), depths in metres below NHN (DHHN2016, or
DHHN92 for surveys from before it), down positive; they are moved to LAT with the German separation surface
(odc.sources.german_separation), as BSH's model is.

The server leaves out the intermediate certificate of its TLS chain; it is
kept in odc/certs and added to the default trust store.
"""

import csv
import hashlib
import io
import json
import os
import re
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from osgeo import gdal, osr

from odc import shom
from odc.reduce_survey import reduce_stream, save

gdal.UseExceptions()

PORTAL = 'https://svdb-portal.bsh.de/svdb-portal'
WMS_NS = {'w': 'http://www.opengis.net/wms'}
CERT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'certs', 'geant-tls-rsa-1.pem')
# Groups of the layer tree, not datasets.
GROUP_LAYERS = {'Layers', 'SVDB', 'svdb_public'}
# NHN in DHHN2016 and in DHHN92, which surveys before 2016 are on. The two
# differ by a few centimetres at most along the German coasts, less than a
# sounding's accuracy, so both take the same separation to LAT.
NHN_DATUMS = {'7837', '5783'}
# What the survey's technique says, in the words of the Shom metadata that
# build_unit's TECSOU and quality categories read.
TECSOU = {'Fächerecholot': 'multi-beam', 'Echolot': 'single-beam'}
# The download size is not known before the server packs a layer; its XYZ
# zips to about 3.5 bytes a point, which is what sharding needs.
ESTIMATED_BYTES_PER_POINT = 3.5
SEARCH_PAGE = 1000
SEARCH_SINCE = '1900-01-01'
PREPARE_POLL_S = 5
# Short enough that a survey started at odc.mirror.REDUCE_BUDGET_S still ends
# within the reduce job's limit.
PREPARE_TIMEOUT_S = 90 * 60
# The portal now and then packs a layer as an empty zip; packing it again
# gives the data.
PACK_ATTEMPTS = 3
CHUNK = 1 << 20


def context():
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(CERT)
    return ctx


def get(url, timeout=600, tries=4):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': shom.USER_AGENT})
            return urllib.request.urlopen(request, timeout=timeout, context=context())
        except Exception as error:
            if shom.is_client_error(error) or attempt == tries - 1:
                raise
            time.sleep(10 * (attempt + 1))


def get_json(url):
    stamp = f'&_t={int(time.time() * 1000)}'
    with get(url + stamp, timeout=120) as response:
        return json.load(response)


def dates(text):
    """The layer abstract's 'dd.mm.yyyy-dd.mm.yyyy' as start and end, YYYYMMDD."""
    found = sorted(f'{y}{m}{d}' for d, m, y in re.findall(r'(\d\d)\.(\d\d)\.(\d{4})', text or ''))
    if not found:
        raise ValueError(f'no dates in {text!r}')
    return found[0], found[-1]


def survey_name(title):
    return 'BSH-' + re.sub(r'[^A-Za-z0-9_-]', '-', title)


def polygon(bbox):
    w, s, e, n = bbox
    return {'type': 'Polygon', 'coordinates': [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def layers(capabilities):
    """The datasets of a GetCapabilities document: id, title, start, end, bbox."""
    root = ET.fromstring(capabilities)
    found = []
    for layer in root.iter(f'{{{WMS_NS["w"]}}}Layer'):
        name = layer.findtext('w:Name', namespaces=WMS_NS)
        box = layer.find('w:EX_GeographicBoundingBox', WMS_NS)
        if not name or name in GROUP_LAYERS or box is None or layer.find('w:Layer', WMS_NS) is not None:
            continue
        w, e, s, n = (float(c.text) for c in box)
        start, end = dates(layer.findtext('w:Abstract', namespaces=WMS_NS))
        found.append(
            {
                'id': name,
                'title': layer.findtext('w:Title', namespaces=WMS_NS),
                'start': start,
                'end': end,
                'bbox': [w, s, e, n],
            }
        )
    return found


def survey_from_layer(layer, record):
    """A layer and its search record as a mirror survey. Its md5 stands for
    the dataset's content: the layer id is the portal's and is left out, so a
    renumbering alone does not bring every survey down again."""
    identity = {k: layer[k] for k in ('title', 'start', 'end')}
    identity['bbox'] = [round(v, 5) for v in layer['bbox']]
    identity.update({k: record.get(k) for k in ('Datenart', 'Unterkategorie', 'Datenpunkte')})
    return {
        'provider': 'bsh-svdb',
        'name': survey_name(layer['title']),
        'layer': layer['id'],
        'title': layer['title'],
        'start': layer['start'],
        'end': layer['end'],
        'tecsou': TECSOU.get(record.get('Unterkategorie'), ''),
        'kind': record.get('Datenart', ''),
        'bbox': layer['bbox'],
        'geometry': polygon(layer['bbox']),
        'size': max(1, round(int(record.get('Datenpunkte') or 0) * ESTIMATED_BYTES_PER_POINT)),
        'md5': hashlib.md5(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }


def search():
    """Every dataset's search record (Titel, Datenart, Unterkategorie,
    Datenpunkte, …) by layer id, from the metadata search the viewer runs."""
    wms = f'{PORTAL}/wms?pass='
    today = time.strftime('%Y-%m-%d', time.gmtime())
    key = get_json(f'{wms}&REQUEST=SEARCH&FILTERS=TimeFilter&TIMERANGE={SEARCH_SINCE},{today}')['key']
    records, start = {}, 0
    while True:
        page = get_json(f'{wms}&REQUEST=LOADRESULTPAGE&RS={key}&draw=1&start={start}&length={SEARCH_PAGE}')
        for row in page['data']:
            records[row['Name']] = row
        start += SEARCH_PAGE
        if not page['data'] or start >= int(page['recordsFiltered']):
            return records


def discover(bboxes):
    """The surveys whose extent meets one of the bboxes; each carries the ids
    of the regions it meets, in the order of bboxes' keys."""
    with get(f'{PORTAL}/wms?version=1.3.0&request=GetCapabilities&service=WMS') as response:
        found = layers(response.read())
    records = search()
    surveys = []
    for layer in found:
        regions = sorted(r for r, box in bboxes.items() if shom.intersects(layer['bbox'], box))
        if not regions:
            continue
        record = records.get(layer['id'])
        if record is None:
            # Without its search record a layer has no technique to chart it
            # by: it keeps what the mirror holds for it until the search lists
            # it again, and is not mirrored anew.
            surveys.append(held(layer, regions))
            continue
        surveys.append({**survey_from_layer(layer, record), 'regions': regions})
    unsearched = sum(1 for s in surveys if s.get('held'))
    print(
        f'svdb: {len(found)} layers, {len(surveys)} surveys in the regions, {unsearched} not in the search', flush=True
    )
    return surveys


def held(layer, regions):
    return {
        'provider': 'bsh-svdb',
        'name': survey_name(layer['title']),
        'held': True,
        'regions': regions,
        'geometry': polygon(layer['bbox']),
    }


def prepare(layer_id):
    """Have the portal pack a layer as XYZ with CSV metadata; the zip's name."""
    download = f'{PORTAL}/download?pass='
    session = get_json(f'{download}&REQUEST=INIT&LAYERS={urllib.parse.quote(layer_id)}')['Session']
    get_json(f'{download}&REQUEST=START&ID={session}&ACCEPTTERMSOFUSE=true&md=CSV&grd=XYZ&tin=XYZ')
    deadline = time.monotonic() + PREPARE_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(PREPARE_POLL_S)
        info = get_json(f'{download}&REQUEST=GETINFO&ID={session}&ACCEPTTERMSOFUSE=true')
        if info.get('EndTime', -1) != -1:
            if not info.get('Filename'):
                raise OSError(f'SVDB packed layer {layer_id} without a file: {info}')
            return info['Filename']
    raise TimeoutError(f'SVDB did not pack layer {layer_id} in {PREPARE_TIMEOUT_S} s')


def fetch(layer_id, path):
    for _ in range(PACK_ATTEMPTS):
        filename = prepare(layer_id)
        with (
            get(f'{PORTAL}/files/{urllib.parse.quote(filename)}', timeout=1800) as response,
            open(path + '.part', 'wb') as fh,
        ):
            while block := response.read(CHUNK):
                fh.write(block)
        with zipfile.ZipFile(path + '.part') as archive:
            packed = any(n.lower().endswith('.xyz') for n in archive.namelist())
        if packed:
            os.replace(path + '.part', path)
            return
    os.remove(path + '.part')
    raise OSError(f'SVDB packed layer {layer_id} without its XYZ {PACK_ATTEMPTS} times')


def read_metadata(text):
    """The CSV's single record (rows: types, names, values) as a dict."""
    rows = list(csv.reader(io.StringIO(text), delimiter=';'))
    # Some datasets pad their values ("7837 ").
    return {name.strip(): value.strip() for name, value in zip(rows[1], rows[2], strict=False)}


class Separation:
    """NHN above LAT, bilinear on its grid; NaN where the surface has no value."""

    def __init__(self, path):
        ds = gdal.Open(path)
        band = ds.GetRasterBand(1)
        values = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            values[values == nodata] = np.nan
        self.values = values
        self.x0, self.dx, _, self.y0, _, self.dy = ds.GetGeoTransform()

    def at(self, lon, lat):
        fx = (lon - self.x0) / self.dx - 0.5
        fy = (lat - self.y0) / self.dy - 0.5
        rows, cols = self.values.shape
        x0 = np.floor(fx).astype(np.int64)
        y0 = np.floor(fy).astype(np.int64)
        inside = (x0 >= 0) & (y0 >= 0) & (x0 < cols - 1) & (y0 < rows - 1)
        out = np.full(len(lon), np.nan)
        x0, y0, tx, ty = x0[inside], y0[inside], fx[inside] - x0[inside], fy[inside] - y0[inside]
        v = self.values
        out[inside] = (
            v[y0, x0] * (1 - tx) * (1 - ty)
            + v[y0, x0 + 1] * tx * (1 - ty)
            + v[y0 + 1, x0] * (1 - tx) * ty
            + v[y0 + 1, x0 + 1] * tx * ty
        )
        return out


def to_lat_lon_lat(srid, separation):
    """Rows of UTM easting, northing, depth below NHN to lon, lat, depth below LAT."""
    source = osr.SpatialReference()
    source.ImportFromEPSG(int(srid))
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(source, target)

    def convert(xyz):
        lonlat = np.asarray(transform.TransformPoints(xyz[:, :2]))
        lon, lat = lonlat[:, 0], lonlat[:, 1]
        # Depth below LAT is depth below NHN less NHN's height above LAT.
        return np.column_stack([lon, lat, xyz[:, 2] - separation.at(lon, lat)])

    return convert


def reduce_zip(path, separation):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        [csv_name] = [n for n in names if n.lower().endswith('.csv')]
        [xyz_name] = [n for n in names if n.lower().endswith('.xyz')]
        meta = read_metadata(archive.read(csv_name).decode('utf-8-sig'))
        if meta.get('vertdat') not in NHN_DATUMS:
            raise ValueError(f'{path}: vertical datum {meta.get("vertdat")}, not NHN')
        with archive.open(xyz_name) as stream:
            reduced, soundings = reduce_stream(stream, to_lat_lon_lat(meta['srid'], separation))
    return reduced, soundings, meta


def reduce_survey(survey, out_path, separation):
    with tempfile.TemporaryDirectory(dir=os.path.dirname(out_path)) as tmp:
        archive = os.path.join(tmp, 'layer.zip')
        fetch(survey['layer'], archive)
        source_bytes = os.path.getsize(archive)
        reduced, soundings, meta = reduce_zip(archive, separation)
    save(out_path, reduced)
    return {
        'soundings': soundings,
        'cells': len(reduced['z']),
        'sourceBytes': source_bytes,
        'kind': meta.get('data_type'),
    }
