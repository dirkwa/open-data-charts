"""Shom and IGN's Litto3D: the coastal lidar models of mainland France and
Corsica (Licence Ouverte 2.0), for regions listing `shom-litto3d` in their
sources and the Litto3D products in `litto3d`.

A product (`LITTO3D_BZH_2018_2021`, `L3D_MAR_CORSE_2017_2018`, …) is
published as packages of 5 km by 5 km in Lambert-93, named after their
north-west corner in kilometres (`0230_6745`), each a 7z with one folder per
square kilometre: a 1 m model (some products also a 5 m one), the lidar
points and, for most products, the source and interpolation-distance masks
of the 1 m model. The mirror takes each package as a survey: the finest
model of each square kilometre, reduced to the shoalest node per chart grid
cell, so a rock between the 5 m nodes is kept.

Heights are on the levelling datum named in the folder (NGF-IGN69 on the
mainland, NGF-IGN78 in Corsica). Shom's BathyElli surfaces give chart datum
(ZH, Zéro Hydrographique) and the highest astronomical tide (PHMA) above the
ellipsoid, IGN's geoid grids the levelling datum: a node's depth below chart
datum is ZH's height on the levelling datum less the node's, and nodes above
the highest tide are land, left out.
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from osgeo import gdal, osr

from odc import shom
from odc.reduce_survey import reduce_rows, save
from odc.svdb import Separation as Surface

gdal.UseExceptions()

CSW = 'https://services.data.shom.fr/geonetwork/srv/fre/csw'
CSW_PAGE = 100
CSW_NS = {'csw': 'http://www.opengis.net/cat/csw/2.0.2', 'dc': 'http://purl.org/dc/elements/1.1/'}
RECORD_PREFIX = 'BATHYMETRIE_'
PACKAGE = re.compile(r'^BATHYMETRIE_(?P<product>[A-Z0-9_]+)_PREPAQUET_(?P<x>\d{4})_(?P<y>\d{4})\.xml$')
DOI = re.compile(r'doi\.org/10\.17183/([A-Za-z0-9_.-]+)')
PACKAGE_KM = 5
# Some products' squares overlap the package's edge by a node or two.
PACKAGE_MARGIN_KM = 1
LAMBERT93 = 2154
LISTING_THREADS = 4
HTTP_NOT_FOUND = 404

# Shom's BathyElli v2.1: ZH and PHMA above the GRS80 ellipsoid (RGF93) as
# scattered points, one file each for the Channel and Atlantic and for the
# Mediterranean with Corsica.
BATHYELLI_GROUP = 'BATHYELLI_2_1_PACK_DL'
BATHYELLI_LEVELS = ('ZH', 'PHMA')
# Mainland France with Corsica, on a grid finer than the surfaces' spacing
# along the coast.
SURFACE_BOUNDS = (-7.6, 41.2, 10.3, 51.4)
SURFACE_STEP = 0.01
SURFACE_NODATA = -9999.0
# Lidar squares reach a few kilometres inland of the surfaces' last points.
SURFACE_FILL_CELLS = 50
GEOIDS = {
    'IGN69': 'https://cdn.proj.org/fr_ign_RAF20.tif',
    'IGN78': 'https://cdn.proj.org/fr_ign_RAC23.tif',
}
# A reduction depends on the datum surfaces as much as on its package: a
# change of either is a new reduction.
DATUMS_ID = ' '.join([BATHYELLI_GROUP, *GEOIDS.values()])

# The 1 m model's masks (Litto3D's Descriptif de contenu, 4.1.4): sources
# that are no measurement, and interpolation distances beyond what a chart
# should take for a depth.
NO_DATA_SOURCE = 0
FICTITIOUS_SOURCE = 69  # water surfaces of ports, estuaries and ponds, made flat
MAX_INTERPOLATION_M = 10  # 253-255 are other models, seams and nodes without height
MODEL_DIRS = ('MNT1m', 'MNT', 'MNT5m', 'MNS')


def identity(files_md5):
    return hashlib.md5(f'{files_md5} {DATUMS_ID}'.encode()).hexdigest()


def get_xml(params):
    with shom.get(f'{CSW}?{urllib.parse.urlencode(params)}', timeout=300) as response:
        return ET.fromstring(response.read())


def records(text):
    """Identifiers of the catalogue records whose text holds `text`."""
    found, start = [], 1
    while True:
        root = get_xml(
            {
                'service': 'CSW',
                'version': '2.0.2',
                'request': 'GetRecords',
                'typeNames': 'csw:Record',
                'resultType': 'results',
                'elementSetName': 'brief',
                'maxRecords': CSW_PAGE,
                'startPosition': start,
                'constraintLanguage': 'CQL_TEXT',
                'constraint_language_version': '1.1.0',
                'constraint': f"AnyText like '%{text}%'",
            }
        )
        result = root.find('csw:SearchResults', CSW_NS)
        found += [e.text for e in result.iter(f'{{{CSW_NS["dc"]}}}identifier')]
        start = int(result.get('nextRecord', 0))
        if start == 0 or start > int(result.get('numberOfRecordsMatched')):
            return found


def product_doi(product):
    """The product's DOI, or None: Shom gave DOIs to the newer products only."""
    root = get_xml(
        {
            'service': 'CSW',
            'version': '2.0.2',
            'request': 'GetRecordById',
            'outputSchema': 'http://www.isotc211.org/2005/gmd',
            'elementSetName': 'full',
            'id': f'{RECORD_PREFIX}{product}.xml',
        }
    )
    found = DOI.search(ET.tostring(root, encoding='unicode'))
    return found.group(1) if found else None


def years(product):
    """The product's first and last year as start and end, YYYYMMDD."""
    found = re.findall(r'_(\d{4})(?=_|$)', product)
    if not found:
        raise ValueError(f'no year in {product}')
    return f'{found[0]}0101', f'{found[-1]}1231'


def to_wgs84():
    source = osr.SpatialReference()
    source.ImportFromEPSG(LAMBERT93)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(source, target)


def package_bbox(x_km, y_km):
    """The package's extent in degrees, from its north-west corner in Lambert-93 kilometres."""
    m = PACKAGE_MARGIN_KM
    w, e = (x_km - m) * 1000, (x_km + PACKAGE_KM + m) * 1000
    s, n = (y_km - PACKAGE_KM - m) * 1000, (y_km + m) * 1000
    corners = np.asarray(to_wgs84().TransformPoints([(w, s), (w, n), (e, s), (e, n)]))
    return [
        round(float(corners[:, 0].min()), 6),
        round(float(corners[:, 1].min()), 6),
        round(float(corners[:, 0].max()), 6),
        round(float(corners[:, 1].max()), 6),
    ]


def polygon(bbox):
    w, s, e, n = bbox
    return {'type': 'Polygon', 'coordinates': [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def survey(product, package, doi):
    x, y = (int(v) for v in package.split('_'))
    start, end = years(product)
    bbox = package_bbox(x, y)
    return {
        'provider': 'shom-litto3d',
        'name': f'{product}_{package}',
        'product': product,
        **({'doi': doi} if doi else {}),
        'group': f'{product}_PACK_DL',
        'package': package,
        'start': start,
        'end': end,
        'tecsou': 'lidar',
        'bbox': bbox,
        'geometry': polygon(bbox),
    }


def packages(product):
    """The package names of a product, from its records in the catalogue."""
    found = set()
    for identifier in records(product):
        match = PACKAGE.match(identifier or '')
        if match and match['product'] == product:
            found.add(f'{match["x"]}_{match["y"]}')
    return sorted(found)


def discover(regions):
    """The packages of the regions' products that meet the region's bbox;
    each carries the ids of the regions it meets."""
    surveys = {}
    for product in sorted({p for region in regions for p in region['litto3d']}):
        doi = product_doi(product)
        for package in packages(product):
            s = survey(product, package, doi)
            meets = [r['id'] for r in regions if product in r['litto3d'] and shom.intersects(s['bbox'], r['bbox'])]
            if meets:
                surveys[s['name']] = {**s, 'regions': sorted(meets)}

    def with_files(s):
        try:
            files = shom.list_files({'group': s['group'], 'name': s['package']})
        except urllib.error.HTTPError as error:
            # The catalogue lists a few packages the download service does not
            # have; each keeps what the mirror holds for it until it has it again.
            if error.code != HTTP_NOT_FOUND:
                raise
            return {**s, 'held': True}
        return {**s, 'files': files, 'size': sum(f['fileSize'] for f in files)}

    with ThreadPoolExecutor(LISTING_THREADS) as pool:
        found = list(pool.map(with_files, surveys.values()))
    missing = sorted(s['name'] for s in found if s.get('held'))
    print(f'litto3d: {len(found)} packages in the regions, {len(missing)} not downloadable: {missing}', flush=True)
    return found


# ------------------------------------------------------------------ datums


def grid_points(lines, path):
    """Scattered `lon lat height …` lines onto the surface grid, linear
    between the points, filled a few kilometres beyond them."""
    xyz = os.path.splitext(path)[0] + '.csv'
    with open(xyz, 'w', encoding='utf-8') as fh:
        fh.write('x,y,z\n')
        for line in lines:
            fields = line.split()
            if len(fields) >= 3:
                fh.write(f'{fields[0]},{fields[1]},{fields[2]}\n')
    vrt = os.path.splitext(path)[0] + '.vrt'
    layer = os.path.splitext(os.path.basename(xyz))[0]
    with open(vrt, 'w', encoding='utf-8') as fh:
        fh.write(
            f'<OGRVRTDataSource><OGRVRTLayer name="{layer}"><SrcDataSource>{xyz}</SrcDataSource>'
            '<GeometryType>wkbPoint</GeometryType><LayerSRS>EPSG:4326</LayerSRS>'
            '<GeometryField encoding="PointFromColumns" x="x" y="y" z="z"/></OGRVRTLayer></OGRVRTDataSource>'
        )
    w, s, e, n = SURFACE_BOUNDS
    gdal.Grid(
        path,
        vrt,
        format='GTiff',
        algorithm=f'linear:radius=0:nodata={SURFACE_NODATA}',
        outputBounds=[w, n, e, s],
        width=round((e - w) / SURFACE_STEP),
        height=round((n - s) / SURFACE_STEP),
        outputType=gdal.GDT_Float32,
        outputSRS='EPSG:4326',
    )
    ds = gdal.Open(path, gdal.GA_Update)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(SURFACE_NODATA)
    gdal.FillNodata(
        targetBand=band,
        maskBand=None,
        maxSearchDist=SURFACE_FILL_CELLS,
        smoothingIterations=0,
        options=['TEMP_FILE_DRIVER=MEM'],
    )
    ds = None


def bathyelli(level, tmp):
    """A BathyElli surface's points, every file of its package."""
    package = {'group': BATHYELLI_GROUP, 'name': f'BATHYELLI_{level}_ell_V2_1'}
    lines = []
    for item in shom.list_files(package):
        archive = os.path.join(tmp, item['fileName'])
        shom.download(shom.file_url(package, item['fileName']), archive, item['fileSize'], item['fileMd5'])
        out = os.path.join(tmp, f'bathyelli-{level}')
        subprocess.run(['7z', 'e', '-y', f'-o{out}', archive, '*.glhi', '-r'], check=True, stdout=subprocess.DEVNULL)
        for path in sorted(glob.glob(os.path.join(out, '*.glhi'))):
            with open(path, encoding='ascii') as fh:
                lines += fh.read().splitlines()
    if not lines:
        raise OSError(f'BathyElli {level}: no points')
    return lines


def on_surface_grid(path, like_path):
    like = gdal.Open(like_path)
    gt = like.GetGeoTransform()
    ds = gdal.Warp(
        '',
        path,
        format='MEM',
        dstSRS='EPSG:4326',
        outputBounds=(gt[0], gt[3] + gt[5] * like.RasterYSize, gt[0] + gt[1] * like.RasterXSize, gt[3]),
        width=like.RasterXSize,
        height=like.RasterYSize,
        resampleAlg='bilinear',
        dstNodata=SURFACE_NODATA,
    )
    return ds.GetRasterBand(1).ReadAsArray()


def write_like(values, like_path, path):
    like = gdal.Open(like_path)
    out = gdal.GetDriverByName('GTiff').Create(path + '.part', like.RasterXSize, like.RasterYSize, 1, gdal.GDT_Float32)
    out.SetGeoTransform(like.GetGeoTransform())
    out.SetProjection(like.GetProjection())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(SURFACE_NODATA)
    band.WriteArray(values)
    out = None
    os.replace(path + '.part', path)


def datums(tmp):
    """Per levelling datum, the paths of ZH's and PHMA's heights on it:
    {'IGN69': {'ZH': path, 'PHMA': path}, …}. Built once per run."""
    done = os.path.join(tmp, 'litto3d-datums.json')
    if os.path.exists(done):
        with open(done, encoding='utf-8') as fh:
            return json.load(fh)
    ellipsoidal = {}
    for level in BATHYELLI_LEVELS:
        ellipsoidal[level] = os.path.join(tmp, f'bathyelli-{level}.tif')
        grid_points(bathyelli(level, tmp), ellipsoidal[level])
    out = {}
    for datum, url in GEOIDS.items():
        geoid = os.path.join(tmp, os.path.basename(url))
        with shom.get(url, timeout=600) as response, open(geoid, 'wb') as fh:
            fh.write(response.read())
        out[datum] = {}
        for level, path in ellipsoidal.items():
            ds = gdal.Open(path)
            h = ds.GetRasterBand(1).ReadAsArray()
            ds = None
            n = on_surface_grid(geoid, path)
            defined = (h != SURFACE_NODATA) & (n != SURFACE_NODATA)
            on_datum = os.path.join(tmp, f'litto3d-{level}-{datum}.tif')
            write_like(np.where(defined, h - n, SURFACE_NODATA).astype('float32'), path, on_datum)
            out[datum][level] = on_datum
    with open(done, 'w', encoding='utf-8') as fh:
        json.dump(out, fh)
    return out


# ------------------------------------------------------------------ packages


def vertical_datum(path):
    for datum in GEOIDS:
        if datum in path:
            return datum
    raise ValueError(f'{path}: no known levelling datum in its name')


def square_model(folder):
    """The finest model of a square kilometre's folder, and its masks if any:
    (model, source mask or None, distance mask or None)."""
    models = []
    for path in glob.glob(os.path.join(folder, '**', '*.asc'), recursive=True):
        if os.path.basename(os.path.dirname(path)) in MODEL_DIRS:
            ds = gdal.Open(path)
            models.append((ds.GetGeoTransform()[1], path))
    if not models:
        return None, None, None
    model = min(models)[1]
    masks = [glob.glob(os.path.join(folder, '**', f'*_{kind}_*.tif'), recursive=True) for kind in ('SRC', 'DST')]
    return model, *(found[0] if len(found) == 1 else None for found in masks)


def mask_at(path, x, y):
    """A mask's values at Lambert-93 positions (nodata outside it)."""
    ds = gdal.Open(path)
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    values = ds.GetRasterBand(1).ReadAsArray()
    col = np.floor((x - x0) / dx).astype(np.int64)
    row = np.floor((y - y0) / dy).astype(np.int64)
    inside = (col >= 0) & (row >= 0) & (col < values.shape[1]) & (row < values.shape[0])
    out = np.full(len(x), -1, np.int64)
    out[inside] = values[row[inside], col[inside]]
    return out


def square_rows(folder, surfaces, transform):
    """A square kilometre's model nodes as lon, lat, depth below ZH."""
    model, source, distance = square_model(folder)
    if model is None:
        return np.zeros((0, 3))
    ds = gdal.Open(model)
    band = ds.GetRasterBand(1)
    height = band.ReadAsArray().astype(np.float64)
    x0, dx, _, y0, _, dy = ds.GetGeoTransform()
    rows, cols = np.nonzero(height != band.GetNoDataValue())
    x = x0 + (cols + 0.5) * dx
    y = y0 + (rows + 0.5) * dy
    h = height[rows, cols]
    keep = np.ones(len(h), bool)
    if source:
        s = mask_at(source, x, y)
        keep &= (s != NO_DATA_SOURCE) & (s != FICTITIOUS_SOURCE)
    if distance:
        d = mask_at(distance, x, y)
        keep &= (d >= 0) & (d <= MAX_INTERPOLATION_M)
    x, y, h = x[keep], y[keep], h[keep]
    if not len(h):
        return np.zeros((0, 3))
    lonlat = np.asarray(transform.TransformPoints(np.column_stack([x, y])))
    lon, lat = lonlat[:, 0], lonlat[:, 1]
    zh, phma = (surfaces[vertical_datum(model)][level].at(lon, lat) for level in BATHYELLI_LEVELS)
    # Nodes above the highest tide are land; NaN (no surface) fails the test too.
    sea = h <= phma
    return np.column_stack([lon[sea], lat[sea], (zh - h)[sea]])


def reduce_archive(archive, surfaces, tmp):
    out = os.path.join(tmp, 'x')
    subprocess.run(
        ['7z', 'x', '-y', f'-o{out}', archive, '*.asc', '*_SRC_*.tif', '*_DST_*.tif', '-r'],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    folders = sorted(
        {os.path.dirname(os.path.dirname(p)) for p in glob.glob(os.path.join(out, '**', '*.asc'), recursive=True)}
    )
    transform = to_wgs84()
    return reduce_rows(square_rows(folder, surfaces, transform) for folder in folders)


def load_surfaces(paths):
    return {datum: {level: Surface(path) for level, path in levels.items()} for datum, levels in paths.items()}


def reduce_survey(survey, out_path, surface_paths):
    with tempfile.TemporaryDirectory(dir=os.path.dirname(out_path)) as tmp:
        [item] = survey['files']
        archive = os.path.join(tmp, item['fileName'])
        group = {'group': survey['group'], 'name': survey['package']}
        shom.download(shom.file_url(group, item['fileName']), archive, item['fileSize'], item['fileMd5'], 2)
        reduced, nodes = reduce_archive(archive, load_surfaces(surface_paths), tmp)
    save(out_path, reduced)
    return {'soundings': nodes, 'cells': len(reduced['z']), 'sourceBytes': survey['size']}
