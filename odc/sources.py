"""Download a region's chart inputs other than the surveys (see odc.mirror):

  wfs/<namespace>__<layer>.json   Shom WFS S-57 objects (Licence Ouverte 2.0)
  dem/*.asc                       Shom bathymetric DEMs of the region's coasts, if any
  dem/emodnet_*.tif               EMODnet Bathymetry DTM over the region (CC BY 4.0), if asked for
  osm.geojsonseq                  OSM features the chart uses, buildings only near the sea (ODbL)
  land.gpkg                       OSM land polygons (osmdata.openstreetmap.de, ODbL)
  relief/*.tif                    Copernicus DEM GLO-30 land tiles
  etopo/*.tif                     NOAA NCEI ETOPO 2022 15 arc-second tiles, for deep water
  nc/reefs.geojson, nc/structures.geojson   Gouvernement de la Nouvelle-Calédonie (Licence Ouverte)
  aca/reefs_*.geojsonseq          Allen Coral Atlas reef geomorphology (CC BY 4.0), per 1-degree square
  aca-depth/depth_*.tif           Allen Coral Atlas satellite-derived bathymetry (CC BY 4.0), the
                                  1-degree squares the aca-depth release has (odc.aca_depth)

Usage: python3 -m odc.sources <region.json> <out dir> [source ...]
"""

import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse

from osgeo import gdal, ogr

from odc import aca_depth, build_unit, osm, shom
from odc.grid import units_in

gdal.UseExceptions()

WFS_NAMESPACES = (
    'BALISAGE_BDD_WFS',
    'EPAVES_BDD_WFS',
    'CABLES_BDD_WFS',
    'INFORMATIONS_PORTUAIRES_BDD_WFS',
    'REGLEMENTATION_NAVIGATION_BDD_WFS',
    'TOPONYMIE_BDD_WFS',
)
WFS_PAGE = 5000
LAND_POLYGONS = 'https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip'
LAND_POLYGONS_SHP = 'land-polygons-split-4326/land_polygons.shp'
COPERNICUS = 'https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif'
ETOPO = (
    'https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/15s_surface_elev_gtif/'
    'ETOPO_2022_v1_15s_{n}_surface.tif'
)
ETOPO_TILE_DEG = 15
# The builder charts buildings up to 12 grid steps (0.0015 deg) from the sea
# (build_unit.BUILDING_NEAR_WATER_CELLS); a coarse mask with a wide margin
# keeps a superset and drops the millions of inland ones a European extract
# carries.
NEAR_SEA_STEP = 0.001
NEAR_SEA_CELLS = 5
# Whole degrees fall on the DTM's 1/16 arc-minute grid, so chunks fit edge to edge.
EMODNET_WCS = (
    'https://ows.emodnet-bathymetry.eu/wcs?service=WCS&version=2.0.1&request=GetCoverage'
    '&coverageId=emodnet__mean&format=image/tiff&subset=Lat({s},{n})&subset=Long({w},{e})'
)
EMODNET_CHUNK_DEG = 2
# The DTM carries land elevations, where a Shom model has no data. Keep
# drying heights (a few metres above chart datum) and drop the rest, or a
# 115 m land cell spilling past the OSM coastline charts as a drying bank.
EMODNET_MAX_HEIGHT_M = 5.0
EMODNET_NODATA = -9999.0
ARCGIS_PAGE = 1000
ACA_WFS = (
    'https://allencoralatlas.org/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature'
    '&typeNames=coral-atlas:geomorphic_data_verbose&outputFormat=application/json'
)
ACA_PAGE = 5000
CHUNK = 1 << 20


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', 'w', encoding='utf-8') as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + '.part', path)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save_url(url, path, timeout=600):
    with shom.get(url, timeout=timeout) as response, open(path + '.part', 'wb') as fh:
        shutil.copyfileobj(response, fh, CHUNK)
    os.replace(path + '.part', path)


def wfs_layers():
    caps = shom.get(f'{shom.WFS}?service=WFS&version=2.0.0&request=GetCapabilities').read().decode('utf-8', 'replace')
    names = re.findall(r'<(?:wfs:)?Name>([^<]+)</(?:wfs:)?Name>', caps)
    return sorted({n for n in names if n.split(':')[0] in WFS_NAMESPACES})


def wfs_features(type_name, bbox):
    minlon, minlat, maxlon, maxlat = bbox
    features = []
    while True:
        params = {
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'GetFeature',
            'typeNames': type_name,
            'outputFormat': 'application/json',
            'srsName': 'urn:ogc:def:crs:EPSG::4326',
            # WFS 2.0 with an EPSG URN: axis order lat,lon.
            'bbox': f'{minlat},{minlon},{maxlat},{maxlon},urn:ogc:def:crs:EPSG::4326',
            'count': str(WFS_PAGE),
            'startIndex': str(len(features)),
        }
        page = shom.get_json(f'{shom.WFS}?{urllib.parse.urlencode(params)}')
        batch = page.get('features') or []
        features.extend(batch)
        matched = page.get('numberMatched')
        if len(batch) < WFS_PAGE or (isinstance(matched, int) and len(features) >= matched):
            return features


def fetch_bounds(region):
    """The region's units with their build margins: discovery takes whole
    units, which reach past the region's bbox."""
    boxes = [unit.build_box.bbox for unit in units_in(*region['bbox'])]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def fetch_wfs(region, out):
    if 'shom-wfs' not in region.get('sources', []):
        for stale in glob.glob(os.path.join(out, 'wfs', '*.json')):
            os.remove(stale)
        return
    for name in wfs_layers():
        features = sorted(wfs_features(name, fetch_bounds(region)), key=lambda f: str(f.get('id')))
        save_json(
            os.path.join(out, 'wfs', name.replace(':', '__') + '.json'),
            {'type': 'FeatureCollection', 'features': features},
        )
        print(f'{len(features):6d}  {name}', flush=True)


def dem_grids(inputs):
    """The region's bathymetric model grids, in the order the mosaic
    stacks them: a later grid wins where they overlap."""
    dem = os.path.join(inputs, 'dem')
    return sorted(glob.glob(os.path.join(dem, 'emodnet_*.tif'))) + sorted(glob.glob(os.path.join(dem, '*.asc')))


def fetch_emodnet(bounds, target):
    minlon, minlat, maxlon, maxlat = bounds
    step = EMODNET_CHUNK_DEG
    for s in range(math.floor(minlat), math.ceil(maxlat), step):
        for w in range(math.floor(minlon), math.ceil(maxlon), step):
            url = EMODNET_WCS.format(s=s, n=s + step, w=w, e=w + step)
            path = os.path.join(target, f'emodnet_{s:+03d}_{w:+04d}.tif')
            raw = path + '.wcs'  # outside dem_grids' pattern, so a broken run never feeds it to the mosaic
            save_url(url, raw, timeout=1800)
            source = gdal.Open(raw)
            heights = source.GetRasterBand(1).ReadAsArray()
            heights[heights > EMODNET_MAX_HEIGHT_M] = EMODNET_NODATA
            part = path + '.part'
            grid = gdal.GetDriverByName('GTiff').CreateCopy(part, source, options=['COMPRESS=DEFLATE', 'PREDICTOR=3'])
            band = grid.GetRasterBand(1)
            band.SetNoDataValue(EMODNET_NODATA)
            band.WriteArray(heights)
            grid = source = None
            os.replace(part, path)
            os.remove(raw)


def fetch_dem(region, out):
    target = os.path.join(out, 'dem')
    os.makedirs(target, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        for dem in region.get('dem', []):
            if dem.get('source') == 'emodnet':
                fetch_emodnet(fetch_bounds(region), target)
                continue
            survey = {'group': dem['group'], 'name': dem['prepackage']}
            for item in shom.list_files(survey):
                archive = os.path.join(tmp, item['fileName'])
                shom.download(shom.file_url(survey, item['fileName']), archive, item['fileSize'], item['fileMd5'])
                subprocess.run(
                    ['7z', 'e', '-y', f'-o{target}', archive, '*.asc', '-r'], check=True, stdout=subprocess.DEVNULL
                )
                os.remove(archive)
    print('dem:', sorted(os.listdir(target)), flush=True)


def download_extract(alternatives, path):
    for url in alternatives:
        try:
            save_url(url, path)
            return
        except (OSError, urllib.error.URLError) as exc:
            print(f'osm: {url} failed ({exc})', flush=True)
    raise RuntimeError(f'no OSM extract could be downloaded from {alternatives}')


def fetch_osm(region, out):
    """region['osm']: the extracts covering the region, each a list of
    alternative URLs for the same extract."""
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        parts = []
        for i, alternatives in enumerate(region['osm']):
            parts.append(os.path.join(tmp, f'part{i}.osm.pbf'))
            download_extract(alternatives, parts[-1])
        extract = os.path.join(tmp, 'extract.osm.pbf')
        if len(parts) == 1:
            os.replace(parts[0], extract)
        else:
            subprocess.run(['osmium', 'merge', '-O', '-o', extract, *parts], check=True)
        filtered = os.path.join(tmp, 'filtered.osm.pbf')
        subprocess.run(['osmium', 'tags-filter', '-O', '-o', filtered, extract, *osm.OSMIUM_FILTER], check=True)
        exported = os.path.join(tmp, 'exported.geojsonseq')
        subprocess.run(
            [
                'osmium',
                'export',
                '-O',
                '-f',
                'geojsonseq',
                '--add-unique-id=type_id',
                '-x',
                'print_record_separator=false',
                '-o',
                exported,
                filtered,
            ],
            check=True,
        )
        bounds = fetch_bounds(region)
        near = near_sea(os.path.join(out, 'land.gpkg'), bounds)
        target = os.path.join(out, 'osm.geojsonseq')
        kept, dropped = osm.drop_inland_buildings(exported, target, near, bounds, NEAR_SEA_STEP)
        print(f'osm: {kept} features kept, {dropped} inland buildings dropped', flush=True)


def near_sea(land_path, bounds):
    """Cells of NEAR_SEA_STEP within NEAR_SEA_CELLS of a cell holding sea,
    over bounds (row 0 at the top). A cell holds sea when its centre is off
    land or a coastline crosses it, so inlets narrower than a cell count."""
    minlon, minlat, maxlon, maxlat = bounds
    ncols = math.ceil((maxlon - minlon) / NEAR_SEA_STEP)
    nrows = math.ceil((maxlat - minlat) / NEAR_SEA_STEP)
    land_ds = gdal.OpenEx(land_path, gdal.OF_VECTOR)
    land = land_ds.GetLayer(0)
    coast_ds = gdal.GetDriverByName('MEM').CreateVector('')
    coast = coast_ds.CreateLayer('coast', geom_type=ogr.wkbMultiLineString)
    for feature in land:
        line = ogr.Feature(coast.GetLayerDefn())
        line.SetGeometry(feature.GetGeometryRef().Boundary())
        coast.CreateFeature(line)

    def burn(layer, options=None):
        raster = gdal.GetDriverByName('MEM').Create('', ncols, nrows, 1, gdal.GDT_Byte)
        raster.SetGeoTransform((minlon, NEAR_SEA_STEP, 0.0, maxlat, 0.0, -NEAR_SEA_STEP))
        gdal.RasterizeLayer(raster, [1], layer, burn_values=[1], options=options or [])
        return raster.GetRasterBand(1).ReadAsArray() == 1

    land.ResetReading()
    near = ~burn(land) | burn(coast, ['ALL_TOUCHED=TRUE'])
    for _ in range(NEAR_SEA_CELLS):
        grown = near.copy()
        grown[1:] |= near[:-1]
        grown[:-1] |= near[1:]
        grown[:, 1:] |= near[:, :-1]
        grown[:, :-1] |= near[:, 1:]
        near = grown
    return near


def fetch_land(region, out):
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        archive = os.path.join(tmp, 'land.zip')
        save_url(LAND_POLYGONS, archive, timeout=1800)
        target = os.path.join(out, 'land.gpkg')
        subprocess.run(
            [
                'ogr2ogr',
                '-overwrite',
                '-f',
                'GPKG',
                '-nln',
                'land',
                '-spat',
                str(minlon),
                str(minlat),
                str(maxlon),
                str(maxlat),
                target + '.part.gpkg',
                f'/vsizip/{archive}/{LAND_POLYGONS_SHP}',
            ],
            check=True,
        )
        os.replace(target + '.part.gpkg', target)


def copernicus_name(lat, lon):
    ns = f'N{lat:02d}' if lat >= 0 else f'S{-lat:02d}'
    ew = f'E{lon:03d}' if lon >= 0 else f'W{-lon:03d}'
    return f'Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM'


def fetch_relief(region, out):
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    target = os.path.join(out, 'relief')
    os.makedirs(target, exist_ok=True)
    for lat in range(math.floor(minlat), math.ceil(maxlat)):
        for lon in range(math.floor(minlon), math.ceil(maxlon)):
            name = copernicus_name(lat, lon)
            try:
                save_url(COPERNICUS.format(n=name), os.path.join(target, name + '.tif'))
            except urllib.error.HTTPError as exc:
                if exc.code not in (403, 404):  # all-sea tiles do not exist
                    raise


def etopo_name(top, left):
    ns = f'N{top:02d}' if top >= 0 else f'S{-top:02d}'
    ew = f'E{left:03d}' if left >= 0 else f'W{-left:03d}'
    return ns + ew


def fetch_etopo(region, out):
    """The ETOPO tiles under the region's units, each named after its
    north-west corner."""
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    m, step = build_unit.ETOPO_MARGIN_DEG, ETOPO_TILE_DEG
    tops = range(math.ceil((maxlat + m) / step) * step, math.floor((minlat - m) / step) * step, -step)
    lefts = range(math.floor((minlon - m) / step) * step, math.ceil((maxlon + m) / step) * step, step)
    names = {etopo_name(top, left) + '.tif' for top in tops for left in lefts}
    target = os.path.join(out, 'etopo')
    os.makedirs(target, exist_ok=True)
    for stale in set(os.listdir(target)) - names:
        os.remove(os.path.join(target, stale))
    for name in sorted(names - set(os.listdir(target))):
        save_url(ETOPO.format(n=name.removesuffix('.tif')), os.path.join(target, name))
    print('etopo:', sorted(names), flush=True)


def arcgis_features(url, where, bbox):
    meta = shom.get_json(f'{url}?f=json')
    features = []
    while True:
        params = {
            'where': where,
            'geometry': ','.join(map(str, bbox)),
            'geometryType': 'esriGeometryEnvelope',
            'inSR': 4326,
            'outSR': 4326,
            'outFields': '*',
            'f': 'geojson',
            'orderByFields': meta.get('objectIdField') or 'objectid',
            'resultOffset': len(features),
            'resultRecordCount': ARCGIS_PAGE,
        }
        page = shom.get_json(f'{url}/query?{urllib.parse.urlencode(params)}', timeout=180)
        batch = page.get('features') or []
        features.extend(batch)
        more = page.get('exceededTransferLimit') or (page.get('properties') or {}).get('exceededTransferLimit')
        if not batch or not more:
            return meta, features


def fetch_arcgis(region, out):
    for layer in region.get('arcgis', []):
        meta, features = arcgis_features(layer['url'], layer.get('where', '1=1'), fetch_bounds(region))
        save_json(
            os.path.join(out, layer['file']),
            {
                'type': 'FeatureCollection',
                'source': layer['url'],
                'copyright': meta.get('copyrightText'),
                'features': features,
            },
        )
        print(f'{len(features):6d}  {layer["file"]}', flush=True)


def aca_request(cql, **params):
    return f'{ACA_WFS}&{urllib.parse.urlencode({"CQL_FILTER": cql, **params})}'


def aca_features(classes, w, s, e, n):
    """Every feature of these classes meeting the box. The layer has no key
    to page in a stable order, so a box with more than ACA_PAGE features is
    split instead, and each answer must hold all the features it matched."""
    cql = f"class_name IN ({classes}) AND BBOX(geom,{w},{s},{e},{n},'EPSG:4326')"
    with shom.get(aca_request(cql, resultType='hits'), timeout=600) as response:
        hits = int(re.search(rb'numberMatched="(\d+)"', response.read()).group(1))
    if hits > ACA_PAGE:
        mx, my = (w + e) / 2, (s + n) / 2
        quarters = ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n))
        return [f for q in quarters for f in aca_features(classes, *q)]
    features = []
    if hits:
        features = shom.get_json(aca_request(cql, count=ACA_PAGE), timeout=600).get('features') or []
    if len(features) != hits:
        raise RuntimeError(f'Allen Coral Atlas answered {len(features)} of {hits} features in {w},{s},{e},{n}')
    return features


def fetch_aca(region, out):
    """The Allen Coral Atlas classes the builder charts, one file per
    1-degree square that has any. A reef crossing a square's edge is in
    both files; the builder and the versions read each feature once. The
    service's feature ids change between requests, so the files leave them
    out and stay the same while the atlas does."""
    target = os.path.join(out, 'aca')
    shutil.rmtree(target, ignore_errors=True)
    if 'allen-coral-atlas' not in region.get('sources', []):
        return
    os.makedirs(target)
    classes = ','.join(f"'{name}'" for name in build_unit.ACA_WATLEV)
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    total = 0
    for s in range(math.floor(minlat), math.ceil(maxlat)):
        for w in range(math.floor(minlon), math.ceil(maxlon)):
            lines = {
                json.dumps(
                    {
                        'type': 'Feature',
                        'properties': {'class_name': f['properties']['class_name']},
                        'geometry': f['geometry'],
                    },
                    separators=(',', ':'),
                )
                for f in aca_features(classes, w, s, w + 1, s + 1)
            }
            if not lines:
                continue
            path = os.path.join(target, f'reefs_{s:+03d}_{w:+04d}.geojsonseq')
            with open(path + '.part', 'w', encoding='utf-8') as fh:
                fh.writelines(line + '\n' for line in sorted(lines))
            os.replace(path + '.part', path)
            total += len(lines)
    print(f'aca: {total} reef features in {len(os.listdir(target))} squares', flush=True)


ACA_DEPTH_RELEASE = 'https://api.github.com/repos/{repo}/releases/tags/' + aca_depth.RELEASE


def release_assets(url):
    """Asset name -> (download URL, 'sha256:<hex>'), or {} while the release does not exist."""
    token = os.environ.get('GITHUB_TOKEN')
    headers = {'Accept': 'application/vnd.github+json', **({'Authorization': f'Bearer {token}'} if token else {})}
    try:
        with shom.get(url, headers=headers) as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise
    # Listed from the release's own asset list, which pages.
    assets, page = {}, 1
    while True:
        with shom.get(f'{release["assets_url"]}?per_page=100&page={page}', headers=headers) as response:
            listed = json.load(response)
        # An asset still uploading is listed too, but cannot be downloaded whole yet.
        assets.update(
            {
                a['name']: (a['browser_download_url'], a.get('digest'))
                for a in listed
                if a.get('state', 'uploaded') == 'uploaded' and a.get('size', 1) > 0
            }
        )
        if len(listed) < 100:
            return assets
        page += 1


def aca_depth_assets(release, names):
    """Download URL and digest of each listed square among names."""
    listed = release_assets(release)
    assets = {}
    for name in names:
        # While aca-depth.yml replaces a square, it may be listed only as next-<square>.
        found = listed.get(name) or listed.get(f'next-{name}')
        if found:
            assets[name] = found
    return assets


# A square replaced while a fetch lists and downloads it can move on more than once.
SQUARE_FETCH_ATTEMPTS = 3


class DigestMismatch(OSError):
    pass


def save_square(url, digest, path):
    """Replaces the square at path only with a download that matches its digest:
    a connection closing early can end a download without an error."""
    if not digest:
        raise DigestMismatch(f'{url}: the release lists no digest to check the download against')
    save_url(url, path + '.new')
    if f'sha256:{sha256_of(path + ".new")}' != digest:
        os.remove(path + '.new')
        raise DigestMismatch(f'{url}: the download does not match {digest}')
    os.replace(path + '.new', path)


def fetch_aca_depth(region, out):
    """The satellite-derived bathymetry squares under the region's units,
    for regions charting Allen Coral Atlas reefs."""
    target = os.path.join(out, 'aca-depth')
    assets = {}
    if 'allen-coral-atlas' in region.get('sources', []):
        repo = os.environ.get('GITHUB_REPOSITORY', 'dirkwa/open-data-charts')
        release = ACA_DEPTH_RELEASE.format(repo=repo)
        minlon, minlat, maxlon, maxlat = fetch_bounds(region)
        names = {
            aca_depth.square_name(s, w)
            for s in range(math.floor(minlat), math.ceil(maxlat))
            for w in range(math.floor(minlon), math.ceil(maxlon))
        }
        assets = aca_depth_assets(release, names)
    os.makedirs(target, exist_ok=True)
    for stale in set(os.listdir(target)) - set(assets):
        os.remove(os.path.join(target, stale))
    for name in sorted(assets):
        url, digest = assets[name]
        path = os.path.join(target, name)
        if os.path.exists(path) and f'sha256:{sha256_of(path)}' == digest:
            continue
        for attempt in range(SQUARE_FETCH_ATTEMPTS):
            try:
                save_square(url, digest, path)
                break
            except (urllib.error.HTTPError, DigestMismatch) as error:
                # An import may have replaced the square since it was listed: list it again.
                missing = isinstance(error, urllib.error.HTTPError)
                if (missing and error.code != 404) or attempt == SQUARE_FETCH_ATTEMPTS - 1:
                    raise
                relisted = aca_depth_assets(release, [name]).get(name)
                # A 404 can come between deleting a square and renaming its replacement
                # into place, which may list the same URL and digest again.
                if relisted is None or (relisted == (url, digest) and not missing):
                    raise
                url, digest = relisted
    print('aca-depth:', sorted(assets), flush=True)


FETCHERS = {
    'wfs': fetch_wfs,
    'dem': fetch_dem,
    'land': fetch_land,
    'osm': fetch_osm,
    'relief': fetch_relief,
    'etopo': fetch_etopo,
    'arcgis': fetch_arcgis,
    'aca': fetch_aca,
    'aca-depth': fetch_aca_depth,
}


def main():
    region_path, out, *names = sys.argv[1:]
    with open(region_path, encoding='utf-8') as fh:
        region = json.load(fh)
    os.makedirs(out, exist_ok=True)
    names = list(names or FETCHERS)
    if 'osm' in names and ('land' in names or not os.path.exists(os.path.join(out, 'land.gpkg'))):
        if 'land' in names:
            names.remove('land')
        names.insert(names.index('osm'), 'land')  # near_sea reads it
    for name in names:
        print(f'== {name}', flush=True)
        FETCHERS[name](region, out)


if __name__ == '__main__':
    main()
