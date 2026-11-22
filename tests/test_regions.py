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
            dem.get('source') in ('emodnet', 'ddm', 'vaklodingen', 'bsh', 'kartverket') for dem in region.get('dem', [])
        )
        assert ('emodnet-dtm' in region['sources']) == emodnet
        ddm = any(dem.get('source') == 'ddm' for dem in region.get('dem', []))
        assert ('ddm' in region['sources']) == ddm
        vaklodingen = any(dem.get('source') == 'vaklodingen' for dem in region.get('dem', []))
        assert ('vaklodingen' in region['sources']) == vaklodingen
        bsh = any(dem.get('source') == 'bsh' for dem in region.get('dem', []))
        assert ('bsh' in region['sources']) == bsh
        wsv = any(dem.get('source') == 'wsv' for dem in region.get('dem', []))
        assert ('wsv' in region['sources']) == wsv
        kartverket = any(dem.get('source') == 'kartverket' for dem in region.get('dem', []))
        assert ('kartverket' in region['sources']) == kartverket
        hr = [dem for dem in region.get('dem', []) if dem.get('source') == 'emodnet-hr']
        assert ('emodnet-hr' in region['sources']) == bool(hr)
        for dem in hr:
            assert all(dtm['id'] and dtm['cells_per_degree'] in (3840, 7680) for dtm in dem['dtms'])
        infomar = any(dem.get('source') == 'infomar' for dem in region.get('dem', []))
        assert ('infomar' in region['sources']) == infomar
        # INFOMAR covers Irish waters only; EMODnet charts the rest.
        assert not infomar or emodnet
        icgc = any(dem.get('source') == 'icgc' for dem in region.get('dem', []))
        assert ('icgc' in region['sources']) == icgc
        ausseabed = any(dem.get('source') == 'ausseabed' for dem in region.get('dem', []))
        assert ('ausseabed' in region['sources']) == ausseabed


def test_no_unit_belongs_to_two_regions():
    owner = {}
    for path in REGIONS:
        region = load(path)
        for unit in units_in(*region['bbox']):
            assert unit.id not in owner, f'{unit.id} is in {owner.get(unit.id)} and {region["id"]}'
            owner[unit.id] = region['id']
