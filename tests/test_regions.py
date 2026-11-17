import glob
import json
import os

from odc.grid import units_in

REGIONS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), '..', 'regions', '*.json')))


def load(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def test_every_region_names_its_extracts_and_dems():
    for path in REGIONS:
        region = load(path)
        assert os.path.basename(path) == f'{region["id"]}.json'
        assert region['osm'] and all(isinstance(alternatives, list) and alternatives for alternatives in region['osm'])
        for dem in region.get('dem', []):
            assert dem['group'] and dem['prepackage']
        assert ('shom-dem' in region['sources'] or 'shom-ird-dem' in region['sources']) == bool(region.get('dem'))


def test_no_unit_belongs_to_two_regions():
    owner = {}
    for path in REGIONS:
        region = load(path)
        for unit in units_in(*region['bbox']):
            assert unit.id not in owner, f'{unit.id} is in {owner.get(unit.id)} and {region["id"]}'
            owner[unit.id] = region['id']
