"""The survey mirror: every Shom survey of the configured regions, reduced
once to the global grid and kept as a release asset.

A reduced survey's asset is named after the survey and the md5 of its
source files, `<survey>.<md5-12>.npz`, so the asset list alone tells which
surveys are current. A run that stops halfway loses nothing: the next one
finds the assets it already uploaded.

  python3 -m odc.mirror plan <regions dir> <assets.json> <work dir> [max shard bytes]
  python3 -m odc.mirror reduce <shard.json> <out dir>
  python3 -m odc.mirror index <work dir> <assets.json> <old index or -> <out dir>

assets.json is the release's asset list as the GitHub API gives it
(name, size, digest).
"""

import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from odc import shom
from odc.reduce_survey import reduce_stream, save

INDEX_SCHEMA_VERSION = 1
SHARD_BYTES = 12 << 30  # about 1.5 h of downloads per job at Shom's rate
LISTING_THREADS = 4
# Shom gives each connection ~400 KB/s; with one survey per CPU this keeps
# a job at about eight connections.
CONNECTIONS_PER_SURVEY = 2
# A discovery that finds far fewer surveys than the last index points at a
# failing service, not at Shom withdrawing its data.
MIN_KEPT_SHARE = 0.9


def load_regions(regions_dir):
    return [json.load(open(p, encoding='utf-8')) for p in sorted(glob.glob(os.path.join(regions_dir, '*.json')))]


def source_md5(files):
    if len(files) == 1:
        return files[0]['fileMd5']
    return hashlib.md5(''.join(f['fileMd5'] for f in sorted(files, key=lambda f: f['fileName'])).encode()).hexdigest()


def asset_name(survey):
    return f'{survey["name"]}.{survey["md5"][:12]}.npz'


def discover(regions):
    surveys = {}
    for region in regions:
        for survey in shom.discover_surveys(region['bbox']):
            survey['regions'] = sorted({*surveys.get(survey['name'], {}).get('regions', []), region['id']})
            surveys[survey['name']] = survey

    def with_files(survey):
        files = shom.list_files(survey)
        return {**survey, 'files': files, 'size': sum(f['fileSize'] for f in files), 'md5': source_md5(files)}

    with ThreadPoolExecutor(LISTING_THREADS) as pool:
        return sorted(pool.map(with_files, surveys.values()), key=lambda s: s['name'])


def shard(todo, max_bytes):
    """Largest first, each into the shard with the least bytes so far.

    A survey larger than max_bytes cannot be split, so it gets a shard of its
    own; leaving it out would keep it out of the mirror for good."""
    shards = []
    for survey in sorted(todo, key=lambda s: -s['size']):
        if survey['size'] > max_bytes:
            print(f'{survey["name"]}: {survey["size"]} bytes, over the shard limit; mirrored alone', flush=True)
        fits = [s for s in shards if s['bytes'] + survey['size'] <= max_bytes]
        target = min(fits, key=lambda s: s['bytes']) if fits else None
        if target is None:
            target = {'bytes': 0, 'surveys': []}
            shards.append(target)
        target['bytes'] += survey['size']
        target['surveys'].append(survey)
    return shards


def plan(regions_dir, assets_path, work, max_bytes=SHARD_BYTES):
    os.makedirs(os.path.join(work, 'shards'), exist_ok=True)
    surveys = discover(load_regions(regions_dir))
    have = {a['name'] for a in json.load(open(assets_path, encoding='utf-8'))}
    todo = [s for s in surveys if asset_name(s) not in have]
    with open(os.path.join(work, 'discovery.json'), 'w', encoding='utf-8') as fh:
        json.dump(surveys, fh)
    shards = shard(todo, max_bytes)
    for i, s in enumerate(shards):
        with open(os.path.join(work, 'shards', f'{i}.json'), 'w', encoding='utf-8') as fh:
            json.dump([{k: v for k, v in survey.items() if k != 'geometry'} for survey in s['surveys']], fh)
    print(
        f'{len(surveys)} surveys, {len(todo)} to mirror '
        f'({sum(s["size"] for s in todo) / 1e9:.1f} GB) in {len(shards)} shards',
        flush=True,
    )
    return len(shards)


def reduce_survey(survey, out_dir):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        for f in survey['files']:
            dst = os.path.join(tmp, f['fileName'])
            url = shom.file_url(survey, f['fileName'])
            shom.download(url, dst, f['fileSize'], f['fileMd5'], CONNECTIONS_PER_SURVEY)
        first = sorted(f['fileName'] for f in survey['files'])[0]
        seven = subprocess.Popen(['7z', 'x', '-so', os.path.join(tmp, first), '*.xyz', '-r'], stdout=subprocess.PIPE)
        reduced, soundings = reduce_stream(seven.stdout)
        if seven.wait():
            raise OSError(f'7z failed on {survey["name"]}')
    path = os.path.join(out_dir, asset_name(survey))
    save(path, reduced)
    return {
        'name': survey['name'],
        'soundings': soundings,
        'cells': len(reduced['z']),
        'bytes': os.path.getsize(path),
        'sourceBytes': survey['size'],
        'seconds': round(time.monotonic() - started),
    }


def reduce_shard(shard_path, out_dir):
    """Reduce a shard's surveys, one per CPU: parsing the XYZ is the slow part."""
    os.makedirs(out_dir, exist_ok=True)
    surveys = json.load(open(shard_path, encoding='utf-8'))
    results, failed = [], []
    with ProcessPoolExecutor(os.cpu_count()) as pool:
        futures = {pool.submit(reduce_survey, survey, out_dir): survey['name'] for survey in surveys}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                failed.append(futures[future])
                print(f'{futures[future]}: {error}', flush=True)
                continue
            print(json.dumps(result), flush=True)
            results.append(result)
    with open(os.path.join(out_dir, 'results.json'), 'w', encoding='utf-8') as fh:
        json.dump(results, fh)
    if failed:
        raise SystemExit(f'{len(failed)} surveys failed: {", ".join(sorted(failed))}')


def index_entry(survey, asset):
    return {
        'group': survey['group'],
        'start': survey['start'],
        'end': survey['end'],
        'tecsou': survey['tecsou'],
        'bbox': survey['bbox'],
        'regions': survey['regions'],
        'source': {'files': [f['fileName'] for f in survey['files']], 'bytes': survey['size'], 'md5': survey['md5']},
        'reduced': {'asset': asset['name'], 'bytes': asset['size'], 'sha256': asset['digest'].removeprefix('sha256:')},
    }


def build_index(work, assets_path, old_index_path, out_dir):
    surveys = json.load(open(os.path.join(work, 'discovery.json'), encoding='utf-8'))
    assets = {a['name']: a for a in json.load(open(assets_path, encoding='utf-8'))}
    old = json.load(open(old_index_path, encoding='utf-8')) if old_index_path != '-' else {'surveys': {}}
    if len(surveys) < MIN_KEPT_SHARE * len(old['surveys']):
        raise SystemExit(f'discovery found {len(surveys)} surveys, the mirror has {len(old["surveys"])}: not replacing')
    entries, missing = {}, []
    for survey in surveys:
        asset = assets.get(asset_name(survey))
        if asset:
            entries[survey['name']] = index_entry(survey, asset)
            continue
        missing.append(survey['name'])
        previous = old['surveys'].get(survey['name'])
        # A survey whose new source failed to reduce keeps its last reduction
        # until a run brings the replacement.
        if previous and previous['reduced']['asset'] in assets:
            entries[survey['name']] = previous
    index = {'schemaVersion': INDEX_SCHEMA_VERSION, 'surveys': entries}
    footprints = {
        'type': 'FeatureCollection',
        'features': [
            {'type': 'Feature', 'properties': {'name': s['name']}, 'geometry': s['geometry']}
            for s in surveys
            if s['name'] in entries
        ],
    }
    changed = index['surveys'] != old['surveys']
    index['updated'] = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ') if changed else old.get('updated')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'mirror-index.json'), 'w', encoding='utf-8') as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
    with open(os.path.join(out_dir, 'footprints.geojson'), 'w', encoding='utf-8') as fh:
        json.dump(footprints, fh)
    current = {e['reduced']['asset'] for e in entries.values()} | {'mirror-index.json', 'footprints.geojson'}
    stale = sorted(set(assets) - current)
    with open(os.path.join(out_dir, 'stale-assets.json'), 'w', encoding='utf-8') as fh:
        json.dump(stale, fh)
    print(f'{len(entries)} surveys mirrored, {len(missing)} missing, {len(stale)} stale assets, changed={changed}')
    return changed, missing


def main():
    command, *args = sys.argv[1:]
    if command == 'plan':
        n = plan(*args[:3], *(int(a) for a in args[3:]))
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
                fh.write(f'shards={json.dumps(list(range(n)))}\n')
    elif command == 'reduce':
        reduce_shard(*args)
    elif command == 'index':
        changed, missing = build_index(*args)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
                fh.write(f'changed={str(changed).lower()}\nmissing={len(missing)}\n')
    else:
        raise SystemExit(f'unknown command {command}')


if __name__ == '__main__':
    main()
