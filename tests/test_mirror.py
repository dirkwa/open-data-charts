import json

from odc.mirror import asset_name, build_index, shard


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


def asset(name):
    return {'name': name, 'size': 1, 'digest': 'sha256:00'}


def test_a_failed_replacement_keeps_the_last_reduction(tmp_path):
    old_survey, new_survey = survey('S1', md5='a' * 32), survey('S1', md5='b' * 32)
    old_asset = asset(asset_name(old_survey))
    old_index = {'surveys': {'S1': {'reduced': {'asset': old_asset['name'], 'bytes': 1, 'sha256': '00'}}}}
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
    assert json.loads((tmp_path / 'out' / 'stale-assets.json').read_text()) == [old_asset['name']]


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
