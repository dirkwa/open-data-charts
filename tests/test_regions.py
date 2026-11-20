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
        shom_dems = [dem for dem in region.get('dem', []) if 'source' not in dem]
        for dem in shom_dems:
            assert dem['group'] and dem['prepackage']
        assert ('shom-dem' in region['sources'] or 'shom-ird-dem' in region['sources']) == bool(shom_dems)
        # National models bring EMODnet along for the waters beyond them.
        emodnet = any(
            dem.get('source') in ('emodnet', 'ddm', 'vaklodingen', 'kartverket') for dem in region.get('dem', [])
        )
        assert ('emodnet-dtm' in region['sources']) == emodnet
        ddm = any(dem.get('source') == 'ddm' for dem in region.get('dem', []))
        assert ('ddm' in region['sources']) == ddm
        vaklodingen = any(dem.get('source') == 'vaklodingen' for dem in region.get('dem', []))
        assert ('vaklodingen' in region['sources']) == vaklodingen
        kartverket = any(dem.get('source') == 'kartverket' for dem in region.get('dem', []))
        assert ('kartverket' in region['sources']) == kartverket


def test_no_unit_belongs_to_two_regions():
    owner = {}
    for path in REGIONS:
        region = load(path)
        for unit in units_in(*region['bbox']):
            assert unit.id not in owner, f'{unit.id} is in {owner.get(unit.id)} and {region["id"]}'
            owner[unit.id] = region['id']
