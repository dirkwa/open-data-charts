"""Shom's open download services: survey footprints, prepackage listings
and files.

The server gives each connection roughly 400 KB/s, so a file comes in
PIECE-sized HTTP ranges over a few parallel connections.
"""

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

USER_AGENT = 'open-data-charts/0.1 (+https://github.com/dirkwa/open-data-charts)'
WFS = 'https://services.data.shom.fr/INSPIRE/wfs'
DOWNLOAD = 'https://services.data.shom.fr/INSPIRE/telechargement/prepackageGroup'
SURVEY_LAYERS = (
    'LEVES_BATHY_AVANT_1970_WFS:leves_bathy_avant_1970',
    'LEVES_BATHY_1970-1990_WFS:leves_bathy_1970_1990',
    'LEVES_BATHY_1990-2005_WFS:leves_bathy_1990_2005',
    'LEVES_BATHY_APRES_2005_WFS:leves_bathy_apres_2005',
)
CONNECTIONS = 8
PIECE = 8 << 20


def get(url, timeout=120, tries=4, headers=None):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
            return urllib.request.urlopen(request, timeout=timeout)
        except Exception as error:
            client_error = isinstance(error, urllib.error.HTTPError) and 400 <= error.code < 500 and error.code != 429
            if client_error or attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def get_json(url, timeout=120, tries=4):
    for attempt in range(tries):
        try:
            with get(url, timeout=timeout) as response:
                return json.load(response)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def bbox_of(geometry):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for part in c:
                walk(part)

    walk(geometry['coordinates'])
    return [min(xs), min(ys), max(xs), max(ys)]


def intersects(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def survey_from_feature(feature):
    p = feature['properties']
    return {
        'name': p['name'],
        'group': p['id_md'].split('/')[0],
        'start': p['sursta'],
        'end': p['surend'],
        'tecsou': p['tecsou'],
        'bbox': bbox_of(feature['geometry']),
        'geometry': feature['geometry'],
    }


def discover_surveys(bbox=None):
    """Every survey footprint, or those whose bbox meets the given one."""
    surveys = []
    for layer in SURVEY_LAYERS:
        params = {
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'GetFeature',
            'typeNames': layer,
            'outputFormat': 'application/json',
            'srsName': 'urn:ogc:def:crs:EPSG::4326',
        }
        if bbox is not None:
            # WFS 2.0 with an EPSG URN takes latitude first.
            params['bbox'] = f'{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},urn:ogc:def:crs:EPSG::4326'
        features = get_json(f'{WFS}?{urllib.parse.urlencode(params)}', timeout=600)['features']
        for feature in features:
            if not feature.get('geometry'):
                continue
            survey = survey_from_feature(feature)
            if bbox is None or intersects(survey['bbox'], bbox):
                surveys.append(survey)
    return surveys


def list_files(survey):
    return get_json(f'{DOWNLOAD}/{survey["group"]}/prepackage/{survey["name"]}')['downloadFiles']


def file_url(survey, file_name):
    return f'{DOWNLOAD}/{survey["group"]}/prepackage/{survey["name"]}/file/{urllib.parse.quote(file_name)}'


def md5_of(path):
    digest = hashlib.md5()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, dst, size, md5, connections=CONNECTIONS):
    """Fetch url into dst in parallel ranges and check its md5."""
    part = dst + '.part'
    with open(part, 'wb') as fh:
        fh.truncate(size)
    lock = threading.Lock()
    failed = []

    def fetch(start):
        end = min(size, start + PIECE)
        for attempt in range(5):
            try:
                with get(url, timeout=300, headers={'Range': f'bytes={start}-{end - 1}'}) as response:
                    body = response.read()
                if len(body) != end - start:
                    raise OSError(f'short read {len(body)} != {end - start}')
                break
            except Exception as error:
                if attempt == 4:
                    with lock:
                        failed.append(error)
                    return
                time.sleep(5 * (attempt + 1))
        fd = os.open(part, os.O_WRONLY)
        try:
            os.pwrite(fd, body, start)
        finally:
            os.close(fd)

    with ThreadPoolExecutor(connections) as pool:
        list(pool.map(fetch, range(0, size, PIECE)))
    if failed:
        raise failed[0]
    if md5_of(part) != md5:
        os.remove(part)
        raise OSError(f'md5 mismatch for {url}')
    os.replace(part, dst)
