"""Download a region's chart inputs other than the surveys (see odc.mirror):

  wfs/<namespace>__<layer>.json   Shom WFS S-57 objects (Licence Ouverte 2.0)
  dem/*.asc                       Shom bathymetric DEM of the region's facade
  osm.geojsonseq                  OSM features the chart uses (ODbL)
  land.gpkg                       OSM land polygons (osmdata.openstreetmap.de, ODbL)
  relief/*.tif                    Copernicus DEM GLO-30 land tiles
  nc/reefs.geojson, nc/structures.geojson   Gouvernement de la Nouvelle-Calédonie (Licence Ouverte)

Usage: python3 -m odc.sources <region.json> <out dir> [source ...]
"""

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

from odc import osm, shom

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
ARCGIS_PAGE = 1000
CHUNK = 1 << 20


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', 'w', encoding='utf-8') as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + '.part', path)


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


def fetch_wfs(region, out):
    for name in wfs_layers():
        features = sorted(wfs_features(name, region['bbox']), key=lambda f: str(f.get('id')))
        save_json(
            os.path.join(out, 'wfs', name.replace(':', '__') + '.json'),
            {'type': 'FeatureCollection', 'features': features},
        )
        print(f'{len(features):6d}  {name}', flush=True)


def fetch_dem(region, out):
    dem = region['dem']
    survey = {'group': dem['group'], 'name': dem['prepackage']}
    target = os.path.join(out, 'dem')
    os.makedirs(target, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        for item in shom.list_files(survey):
            archive = os.path.join(tmp, item['fileName'])
            shom.download(shom.file_url(survey, item['fileName']), archive, item['fileSize'], item['fileMd5'])
            subprocess.run(
                ['7z', 'e', '-y', f'-o{target}', archive, '*.asc', '-r'], check=True, stdout=subprocess.DEVNULL
            )
    print('dem:', sorted(os.listdir(target)), flush=True)


def fetch_osm(region, out):
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        extract = os.path.join(tmp, 'extract.osm.pbf')
        for url in region['osm']:
            try:
                save_url(url, extract)
                break
            except (OSError, urllib.error.URLError) as exc:
                print(f'osm: {url} failed ({exc})', flush=True)
        else:
            raise RuntimeError('no OSM extract could be downloaded')
        filtered = os.path.join(tmp, 'filtered.osm.pbf')
        subprocess.run(['osmium', 'tags-filter', '-O', '-o', filtered, extract, *osm.OSMIUM_FILTER], check=True)
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
                os.path.join(out, 'osm.geojsonseq'),
                filtered,
            ],
            check=True,
        )


def fetch_land(region, out):
    minlon, minlat, maxlon, maxlat = region['bbox']
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
    minlon, minlat, maxlon, maxlat = region['bbox']
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
        meta, features = arcgis_features(layer['url'], layer.get('where', '1=1'), region['bbox'])
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


FETCHERS = {
    'wfs': fetch_wfs,
    'dem': fetch_dem,
    'osm': fetch_osm,
    'land': fetch_land,
    'relief': fetch_relief,
    'arcgis': fetch_arcgis,
}


def main():
    region_path, out, *names = sys.argv[1:]
    with open(region_path, encoding='utf-8') as fh:
        region = json.load(fh)
    os.makedirs(out, exist_ok=True)
    for name in names or FETCHERS:
        print(f'== {name}', flush=True)
        FETCHERS[name](region, out)


if __name__ == '__main__':
    main()
