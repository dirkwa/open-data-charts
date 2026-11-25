import json

from odc import mirror
from odc.mirror import BUCKET_RELEASES, asset_name, asset_release, build_index, shard


def survey(name, size=10, md5='a' * 32):
    return {
        'name': name,
        'size': size,
        'md5': md5,
        'group': 'g',
        'start': '2020',
        'end': '2020',
        'tecsou': 'multibeam',
        'bbox': [0, 0, 1, 1],
        'regions': ['r'],
        'files': [{'fileName': f'{name}.7z'}],
        'geometry': {'type': 'Point', 'coordinates': [0, 0]},
    }


def test_a_survey_over_the_limit_is_sharded_alone():
    shards = shard([survey('big', 50), survey('a', 6), survey('b', 6)], 20)
    assert [[s['name'] for s in sh['surveys']] for sh in shards] == [['big'], ['a', 'b']]


def write(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return str(path)


def asset(name, release=None):
    return {'release': release or asset_release(name), 'name': name, 'size': 1, 'digest': 'sha256:00'}


def test_assets_spread_over_the_bucket_releases_by_md5():
    assert asset_release(asset_name(survey('S1', md5='3' * 32))) == 'mirror-3'
    assert {asset_release(f'S1.{d}0.npz') for d in '0123456789abcdef'} == set(BUCKET_RELEASES)


def test_a_failed_replacement_keeps_the_last_reduction(tmp_path):
    old_survey, new_survey = survey('S1', md5='a' * 32), survey('S1', md5='b' * 32)
    old_asset = asset(asset_name(old_survey), 'mirror')
    old_index = {
        'surveys': {'S1': {'reduced': {'release': 'mirror', 'asset': old_asset['name'], 'bytes': 1, 'sha256': '00'}}},
        'regions': {'r': {'surveys': 1, 'mirrored': 1}},
    }
    (tmp_path / 'work').mkdir()
    write(tmp_path / 'work', 'discovery.json', [new_survey])
    changed, missing = build_index(
        str(tmp_path / 'work'),
        write(tmp_path, 'assets.json', [old_asset]),
        write(tmp_path, 'old.json', old_index),
        str(tmp_path / 'out'),
    )
    index = json.loads((tmp_path / 'out' / 'mirror-index.json').read_text())
    assert missing == ['S1']
    assert not changed
    assert index['surveys']['S1'] == old_index['surveys']['S1']
    assert json.loads((tmp_path / 'out' / 'stale-assets.json').read_text()) == []


def test_a_reduced_replacement_supersedes_the_last_one(tmp_path):
    old_survey, new_survey = survey('S1', md5='a' * 32), survey('S1', md5='b' * 32)
    old_asset, new_asset = asset(asset_name(old_survey)), asset(asset_name(new_survey))
    old_index = {'surveys': {'S1': {'reduced': {'asset': old_asset['name'], 'bytes': 1, 'sha256': '00'}}}}
    (tmp_path / 'work').mkdir()
    write(tmp_path / 'work', 'discovery.json', [new_survey])
    build_index(
        str(tmp_path / 'work'),
        write(tmp_path, 'assets.json', [old_asset, new_asset]),
        write(tmp_path, 'old.json', old_index),
        str(tmp_path / 'out'),
    )
    index = json.loads((tmp_path / 'out' / 'mirror-index.json').read_text())
    assert index['surveys']['S1']['reduced']['asset'] == new_asset['name']
    assert index['surveys']['S1']['reduced']['release'] == 'mirror-b'
    stale = json.loads((tmp_path / 'out' / 'stale-assets.json').read_text())
    assert stale == [{'release': old_asset['release'], 'name': old_asset['name']}]


def test_the_index_counts_each_regions_missing_surveys(tmp_path):
    done, failed = survey('S1'), survey('S2')
    (tmp_path / 'work').mkdir()
    write(tmp_path / 'work', 'discovery.json', [done, failed])
    build_index(
        str(tmp_path / 'work'),
        write(tmp_path, 'assets.json', [asset(asset_name(done))]),
        '-',
        str(tmp_path / 'out'),
    )
    index = json.loads((tmp_path / 'out' / 'mirror-index.json').read_text())
    assert index['regions'] == {'r': {'surveys': 2, 'mirrored': 1}}


def index_after(tmp_path, discovered, assets, old_index):
    (tmp_path / 'work').mkdir(parents=True)
    write(tmp_path / 'work', 'discovery.json', discovered)
    changed, _ = build_index(
        str(tmp_path / 'work'),
        write(tmp_path, 'assets.json', assets),
        write(tmp_path, 'old.json', old_index),
        str(tmp_path / 'out'),
    )
    return changed, json.loads((tmp_path / 'out' / 'mirror-index.json').read_text())


def test_a_new_survey_that_failed_still_changes_the_index(tmp_path):
    done, failed = survey('S1'), survey('S2')
    assets = [asset(asset_name(done))]
    _, old_index = index_after(tmp_path / 'a', [done], assets, {'surveys': {}})
    changed, index = index_after(tmp_path / 'b', [done, failed], assets, old_index)
    assert index['surveys'] == old_index['surveys']
    assert index['regions'] == {'r': {'surveys': 2, 'mirrored': 1}}
    assert changed


def test_an_index_without_region_counts_is_rewritten(tmp_path):
    done = survey('S1')
    _, first = index_after(tmp_path / 'a', [done], [asset(asset_name(done))], {'surveys': {}})
    old_index = {'surveys': first['surveys']}
    changed, index = index_after(tmp_path / 'b', [done], [asset(asset_name(done))], old_index)
    assert changed
    assert index['regions'] == {'r': {'surveys': 1, 'mirrored': 1}}


def test_an_entry_from_before_the_split_learns_its_release(tmp_path):
    done = survey('S1')
    legacy = asset(asset_name(done), 'mirror')
    _, first = index_after(tmp_path / 'a', [done], [legacy], {'surveys': {}})
    old_entry = first['surveys']['S1']
    old_entry['reduced'].pop('release')
    changed, index = index_after(
        tmp_path / 'b', [done], [legacy], {'surveys': {'S1': old_entry}, 'regions': first['regions']}
    )
    assert changed
    assert index['surveys']['S1']['reduced']['release'] == 'mirror'


def test_a_held_survey_keeps_its_reduction_and_counts_only_when_it_has_one(tmp_path):
    kept, unknown = survey('S1'), survey('S2')
    assets = [asset(asset_name(kept))]
    _, old_index = index_after(tmp_path / 'a', [kept], assets, {'surveys': {}})
    held = [{'name': s['name'], 'held': True, 'regions': ['r'], 'geometry': s['geometry']} for s in (kept, unknown)]
    changed, index = index_after(tmp_path / 'b', held, assets, old_index)
    assert index['surveys'] == old_index['surveys']
    assert index['regions'] == {'r': {'surveys': 1, 'mirrored': 1, 'held': 1}}
    assert changed
    assert json.loads((tmp_path / 'b' / 'out' / 'stale-assets.json').read_text()) == []


def test_a_region_whose_surveys_are_all_held_unreduced_is_listed(tmp_path):
    held = {'name': 'S1', 'held': True, 'regions': ['r'], 'geometry': survey('S1')['geometry']}
    _, index = index_after(tmp_path, [held], [], {'surveys': {}})
    assert index['regions'] == {'r': {'surveys': 0, 'mirrored': 0, 'held': 1}}


def test_shards_beyond_a_runs_share_wait_for_the_next_run(tmp_path, monkeypatch):
    surveys = [survey(f'S{i}', 15, md5=f'{i:x}' * 32) for i in range(5)]
    monkeypatch.setattr(mirror, 'discover', lambda regions_dir: surveys)
    assets = write(tmp_path, 'assets.json', [])
    work = tmp_path / 'work'
    assert mirror.plan('regions', assets, str(work), max_bytes=20, max_shards=3) == 3
    planned = [json.loads((work / 'shards' / f'{i}.json').read_text()) for i in range(3)]
    assert len({s['name'] for shard in planned for s in shard}) == 3
    assert len(json.loads((work / 'discovery.json').read_text())) == 5


def test_no_survey_starts_after_the_deadline():
    import time
    from concurrent.futures import ThreadPoolExecutor

    from odc.mirror import run_until

    started = []

    def job(name):
        started.append(name)
        time.sleep(0.05)
        return name

    jobs = [(f's{i}', (job, f's{i}')) for i in range(6)]
    with ThreadPoolExecutor(2) as pool:
        finished = [name for name, _ in run_until(pool, jobs, 2, time.monotonic() + 0.08)]
    assert sorted(finished) == sorted(started)
    assert 2 <= len(started) < 6


def test_every_job_runs_before_the_deadline():
    import time
    from concurrent.futures import ThreadPoolExecutor

    from odc.mirror import run_until

    jobs = [(f's{i}', (str, i)) for i in range(5)]
    with ThreadPoolExecutor(2) as pool:
        results = {name: future.result() for name, future in run_until(pool, jobs, 2, time.monotonic() + 60)}
    assert results == {f's{i}': str(i) for i in range(5)}
