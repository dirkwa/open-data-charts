"""The survey mirror: every survey of the configured regions, from Shom
(`shom-surveys` in a region's sources), from BSH's SVDB (`bsh-surveys`,
odc.svdb), from Geoscience Australia's AusSeabed (`ausseabed-surveys`,
odc.ausseabed), the Estonian Transport Administration's spot soundings
read from its WMS (`vta-his-depth`, odc.his_depth) and the packages of
Shom and IGN's Litto3D lidar models (`shom-litto3d`, odc.litto3d), reduced
once to the global grid and kept as a release asset.

A reduced survey's asset is named after the survey and the md5 of its
source files (for SVDB, of its metadata; for AusSeabed, of its archive's
link, size and ETag and of its LAT separation; for HIS, of its square and
odc.his_depth.EDITION), `<survey>.<md5-12>.npz`, so the asset list alone tells which
surveys are current. A run that stops halfway loses nothing: the next one
finds the assets it already uploaded.

GitHub holds at most 1000 assets per release, so the assets are spread over
the draft releases `mirror-0` to `mirror-f` by the first digit of the md5.
The draft release `mirror` holds the index and the footprints, and the
assets uploaded before the split until a new source replaces them.

  python3 -m odc.mirror plan <regions dir> <assets.json> <work dir> [max shard bytes]
  python3 -m odc.mirror reduce <shard.json> <out dir>
  python3 -m odc.mirror index <work dir> <assets.json> <old index or -> <out dir>
  python3 -m odc.mirror releases <asset path>...

assets.json lists the assets of all mirror releases as the GitHub API gives
them (name, size, digest), each with its release's tag.
"""

import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from datetime import UTC, datetime

from odc import ausseabed, his_depth, litto3d, shom, svdb
from odc.reduce_survey import reduce_stream, save

INDEX_SCHEMA_VERSION = 1
SHARD_BYTES = 12 << 30  # about 1.5 h of downloads per job at Shom's rate
# A run holds the unit builds back while it mirrors, so a large first fill
# (Litto3D's hundreds of gigabytes) spreads over several runs.
MAX_SHARDS = 24
# A reduce job starts no survey after this, so the surveys it finished are
# uploaded before the job's 340-minute limit; the rest wait for the next run.
REDUCE_BUDGET_S = 3 * 3600
LISTING_THREADS = 4
# Shom gives each connection ~400 KB/s; with one survey per CPU this keeps
# a job at about eight connections.
CONNECTIONS_PER_SURVEY = 2
# A discovery that finds far fewer surveys than the last index points at a
# failing service, not at Shom withdrawing its data.
MIN_KEPT_SHARE = 0.9


def load_regions(regions_dir, source='shom-surveys'):
    """The regions built from one provider's surveys."""
    regions = [json.load(open(p, encoding='utf-8')) for p in sorted(glob.glob(os.path.join(regions_dir, '*.json')))]
    return [region for region in regions if source in region.get('sources', [])]


def source_md5(files):
    if len(files) == 1:
        return files[0]['fileMd5']
    return hashlib.md5(''.join(f['fileMd5'] for f in sorted(files, key=lambda f: f['fileName'])).encode()).hexdigest()


MIRROR_TAG = 'mirror'
BUCKET_RELEASES = [f'{MIRROR_TAG}-{digit}' for digit in '0123456789abcdef']


def asset_name(survey):
    return f'{survey["name"]}.{survey["md5"][:12]}.npz'


def asset_release(name):
    return f'{MIRROR_TAG}-{name.split(".")[1][0]}'


def discover_shom(regions):
    surveys = {}
    for region in regions:
        for survey in shom.discover_surveys(region['bbox']):
            survey['regions'] = sorted({*surveys.get(survey['name'], {}).get('regions', []), region['id']})
            surveys[survey['name']] = survey

    def with_files(survey):
        try:
            files = shom.list_files(survey)
        except (OSError, ValueError) as error:
            # Shom's listing service refuses a request now and then (403 too):
            # the survey keeps what the mirror holds until a later run lists it.
            print(f'{survey["name"]}: listing failed ({error}); held', flush=True)
            return {'name': survey['name'], 'held': True, 'regions': survey['regions'], 'geometry': survey['geometry']}
        return {**survey, 'files': files, 'size': sum(f['fileSize'] for f in files), 'md5': source_md5(files)}

    with ThreadPoolExecutor(LISTING_THREADS) as pool:
        return list(pool.map(with_files, surveys.values()))


def discover(regions_dir):
    surveys = discover_shom(load_regions(regions_dir, 'shom-surveys'))
    bsh = load_regions(regions_dir, 'bsh-surveys')
    if bsh:
        surveys += svdb.discover({region['id']: region['bbox'] for region in bsh})
    australian = load_regions(regions_dir, 'ausseabed-surveys')
    if australian:
        surveys += ausseabed.discover({region['id']: region['bbox'] for region in australian})
    estonian = load_regions(regions_dir, 'vta-his-depth')
    if estonian:
        surveys += his_depth.discover({region['id']: region['bbox'] for region in estonian})
    lidar = load_regions(regions_dir, 'shom-litto3d')
    if lidar:
        datums = litto3d.datums_id()
        surveys += [
            s if s.get('held') else {**s, 'md5': litto3d.identity(source_md5(s['files']), datums)}
            for s in litto3d.discover(lidar)
        ]
    names = [s['name'] for s in surveys]
    if len(names) != len(set(names)):
        raise SystemExit('two surveys share a name')
    return sorted(surveys, key=lambda s: s['name'])


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


def plan(regions_dir, assets_path, work, max_bytes=SHARD_BYTES, max_shards=MAX_SHARDS):
    os.makedirs(os.path.join(work, 'shards'), exist_ok=True)
    surveys = discover(regions_dir)
    have = {a['name'] for a in json.load(open(assets_path, encoding='utf-8'))}
    todo = [s for s in surveys if not s.get('held') and asset_name(s) not in have]
    with open(os.path.join(work, 'discovery.json'), 'w', encoding='utf-8') as fh:
        json.dump(surveys, fh)
    # HIS squares get shards of their own, read by few workers (odc.his_depth.WORKERS).
    his = [s for s in todo if s.get('provider') == 'vta-his-depth']
    shards = shard([s for s in todo if s.get('provider') != 'vta-his-depth'], max_bytes) + shard(his, max_bytes)
    later = shards[max_shards:]
    shards = shards[:max_shards]
    for i, s in enumerate(shards):
        with open(os.path.join(work, 'shards', f'{i}.json'), 'w', encoding='utf-8') as fh:
            json.dump([{k: v for k, v in survey.items() if k != 'geometry'} for survey in s['surveys']], fh)
    print(
        f'{len(surveys)} surveys, {len(todo)} to mirror '
        f'({sum(s["size"] for s in todo) / 1e9:.1f} GB) in {len(shards)} shards',
        flush=True,
    )
    if later:
        print(f'{sum(len(s["surveys"]) for s in later)} surveys in {len(later)} more shards wait for a later run')
    return len(shards)


PROVIDERS = {'bsh-svdb': svdb, 'ausseabed': ausseabed}


def reduce_survey(survey, out_dir, separations=None, datums=None):
    if survey.get('provider') in ('shom-litto3d', 'vta-his-depth'):
        started = time.monotonic()
        path = os.path.join(out_dir, asset_name(survey))
        if survey['provider'] == 'shom-litto3d':
            result = litto3d.reduce_survey(survey, path, datums)
        else:
            # HIS depths are on the Estonian chart datum (EH2000) already.
            result = his_depth.reduce_survey(survey, path)
        return {
            'name': survey['name'],
            **result,
            'bytes': os.path.getsize(path),
            'seconds': round(time.monotonic() - started),
        }
    provider = PROVIDERS.get(survey.get('provider'))
    if provider:
        started = time.monotonic()
        path = os.path.join(out_dir, asset_name(survey))
        result = provider.reduce_survey(survey, path, svdb.Separation(separations[survey['provider']]))
        return {
            'name': survey['name'],
            **result,
            'bytes': os.path.getsize(path),
            'seconds': round(time.monotonic() - started),
        }
    return reduce_shom_survey(survey, out_dir)


def reduce_shom_survey(survey, out_dir):
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


def run_until(pool, jobs, workers, deadline):
    """(name, future) for each job as it finishes, at most `workers` running
    at once; no job starts after the deadline (time.monotonic())."""
    pending, running = list(jobs), {}
    while pending or running:
        while pending and len(running) < workers and time.monotonic() < deadline:
            name, (fn, *args) = pending.pop(0)
            running[pool.submit(fn, *args)] = name
        if not running:
            return
        done, _ = wait(running, return_when=FIRST_COMPLETED)
        for future in done:
            yield running.pop(future), future


def reduce_shard(shard_path, out_dir, budget_s=REDUCE_BUDGET_S):
    """Reduce a shard's surveys, one per CPU: parsing the XYZ is the slow part."""
    os.makedirs(out_dir, exist_ok=True)
    surveys = json.load(open(shard_path, encoding='utf-8'))
    separations = {}
    providers = {s.get('provider') for s in surveys}
    if providers & set(PROVIDERS):
        # GDAL and the datum grids only where a provider needs them.
        from odc.sources import ausseabed_separation, german_separation

        datum = os.path.join(out_dir, 'datum')
        os.makedirs(datum, exist_ok=True)
        if 'bsh-svdb' in providers:
            separations['bsh-svdb'] = german_separation(datum)
        if 'ausseabed' in providers:
            separations['ausseabed'] = ausseabed_separation(datum)
    datums = None
    if 'shom-litto3d' in providers:
        os.makedirs(os.path.join(out_dir, 'datum'), exist_ok=True)
        datums = litto3d.datums(os.path.join(out_dir, 'datum'))
    results, failed = [], []
    jobs = [(survey['name'], (reduce_survey, survey, out_dir, separations, datums)) for survey in surveys]
    deadline = time.monotonic() + budget_s
    workers = os.cpu_count() or 1
    if providers == {'vta-his-depth'}:
        workers = min(workers, his_depth.WORKERS)
    with ProcessPoolExecutor(workers) as pool:
        finished = run_until(pool, jobs, workers, deadline)
        for name, future in finished:
            try:
                result = future.result()
            except Exception as error:
                failed.append(name)
                print(f'{name}: {error}', flush=True)
                continue
            print(json.dumps(result), flush=True)
            results.append(result)
    postponed = len(surveys) - len(results) - len(failed)
    if postponed:
        print(f'{postponed} surveys not started within {budget_s} s; the next run takes them up', flush=True)
    with open(os.path.join(out_dir, 'results.json'), 'w', encoding='utf-8') as fh:
        json.dump(results, fh)
    if failed:
        raise SystemExit(f'{len(failed)} surveys failed: {", ".join(sorted(failed))}')


def index_entry(survey, asset):
    if survey.get('provider') == 'shom-litto3d':
        provider = {
            'provider': 'shom-litto3d',
            'product': survey['product'],
            **({'doi': survey['doi']} if 'doi' in survey else {}),
            'group': survey['group'],
            'source': {
                'files': [f['fileName'] for f in survey['files']],
                'bytes': survey['size'],
                'md5': source_md5(survey['files']),
            },
        }
    elif survey.get('provider') == 'bsh-svdb':
        provider = {
            'provider': 'bsh-svdb',
            'title': survey['title'],
            'kind': survey['kind'],
            'source': {'layer': survey['layer'], 'md5': survey['md5']},
        }
    elif survey.get('provider') == 'vta-his-depth':
        provider = {
            'provider': 'vta-his-depth',
            'title': survey['title'],
            'source': {'block': survey['block'], 'edition': his_depth.EDITION, 'md5': survey['md5']},
        }
    elif survey.get('provider') == 'ausseabed':
        provider = {
            'provider': 'ausseabed',
            'title': survey['title'],
            'kind': survey['kind'],
            'source': {'url': survey['url'], 'bytes': survey['size'], 'md5': survey['md5']},
        }
    else:
        provider = {
            'group': survey['group'],
            'source': {
                'files': [f['fileName'] for f in survey['files']],
                'bytes': survey['size'],
                'md5': survey['md5'],
            },
        }
    return {
        **provider,
        'start': survey['start'],
        'end': survey['end'],
        'tecsou': survey['tecsou'],
        'bbox': survey['bbox'],
        'regions': survey['regions'],
        'reduced': {
            'release': asset['release'],
            'asset': asset['name'],
            'bytes': asset['size'],
            'sha256': asset['digest'].removeprefix('sha256:'),
        },
    }


def region_counts(surveys, entries):
    """Per region, the surveys discovery found and those the index holds: a
    region is built only once the two agree."""
    counts = {}
    for survey in surveys:
        held = survey.get('held') and survey['name'] not in entries
        for region in survey['regions']:
            count = counts.setdefault(region, {'surveys': 0, 'mirrored': 0})
            # A survey held without a reduction has nothing to wait for; it is
            # counted apart, so a region with nothing else is still listed.
            if held:
                count['held'] = count.get('held', 0) + 1
                continue
            count['surveys'] += 1
            count['mirrored'] += survey['name'] in entries
    return counts


def build_index(work, assets_path, old_index_path, out_dir):
    surveys = json.load(open(os.path.join(work, 'discovery.json'), encoding='utf-8'))
    assets = {a['name']: a for a in json.load(open(assets_path, encoding='utf-8'))}
    old = json.load(open(old_index_path, encoding='utf-8')) if old_index_path != '-' else {'surveys': {}}
    if len(surveys) < MIN_KEPT_SHARE * len(old['surveys']):
        raise SystemExit(f'discovery found {len(surveys)} surveys, the mirror has {len(old["surveys"])}: not replacing')
    entries, missing = {}, []
    for survey in surveys:
        asset = None if survey.get('held') else assets.get(asset_name(survey))
        if asset:
            entries[survey['name']] = index_entry(survey, asset)
            continue
        if not survey.get('held'):
            missing.append(survey['name'])
        previous = old['surveys'].get(survey['name'])
        # A survey whose new source failed to reduce, or that discovery could
        # only hold, keeps its last reduction until a run brings a new one.
        if previous and previous['reduced']['asset'] in assets:
            release = assets[previous['reduced']['asset']]['release']
            entries[survey['name']] = {**previous, 'reduced': {**previous['reduced'], 'release': release}}
    # A HIS square is found only where an overview draws something; one that
    # an overview missed this time keeps its reduction until EDITION changes.
    for name, previous in old['surveys'].items():
        if (
            name not in entries
            and previous.get('provider') == 'vta-his-depth'
            and previous['source'].get('edition') == his_depth.EDITION
            and previous['reduced']['asset'] in assets
        ):
            release = assets[previous['reduced']['asset']]['release']
            entries[name] = {**previous, 'reduced': {**previous['reduced'], 'release': release}}
    index = {'schemaVersion': INDEX_SCHEMA_VERSION, 'surveys': entries, 'regions': region_counts(surveys, entries)}
    footprints = {
        'type': 'FeatureCollection',
        'features': [
            {'type': 'Feature', 'properties': {'name': s['name']}, 'geometry': s['geometry']}
            for s in surveys
            if s['name'] in entries
        ],
    }
    # Region counts change alone when a new survey fails to reduce; the
    # build's readiness check must see that.
    changed = index['surveys'] != old['surveys'] or index['regions'] != old.get('regions')
    index['updated'] = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ') if changed else old.get('updated')
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'mirror-index.json'), 'w', encoding='utf-8') as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
    with open(os.path.join(out_dir, 'footprints.geojson'), 'w', encoding='utf-8') as fh:
        json.dump(footprints, fh)
    current = {e['reduced']['asset'] for e in entries.values()} | {'mirror-index.json', 'footprints.geojson'}
    stale = [{'release': assets[name]['release'], 'name': name} for name in sorted(set(assets) - current)]
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
    elif command == 'releases':
        for path in args:
            print(asset_release(os.path.basename(path)), path)
    elif command == 'index':
        changed, missing = build_index(*args)
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
                fh.write(f'changed={str(changed).lower()}\nmissing={len(missing)}\n')
    else:
        raise SystemExit(f'unknown command {command}')


if __name__ == '__main__':
    main()
