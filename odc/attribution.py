"""The attribution every unit and the index carry: the sources all regions
share, plus each region's own (its DEM, local data) from its region file."""

import glob
import json
import os

SHARED_FIRST = (
    'Shom, Licence Ouverte Etalab 2.0',
    'Shom bathymetric datasets (doi:10.17183/S..., see INFORM of each sounding)',
)
SHARED_LAST = (
    '(c) OpenStreetMap contributors, ODbL',
    'Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 '
    'provided under COPERNICUS by the European Union and ESA',
)
REGIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'regions')


def compose(regional):
    return '; '.join([*SHARED_FIRST, *regional, *SHARED_LAST])


def of_region(region):
    return compose(region.get('attribution', []))


def merge(texts):
    """One attribution naming every source of these composed attributions once."""
    shared = {*SHARED_FIRST, *SHARED_LAST}
    regional = []
    for text in texts:
        regional += [part for part in text.split('; ') if part not in shared and part not in regional]
    return compose(regional)


def configured(region_id, regions_dir=REGIONS_DIR):
    """The attribution a region's file gives now, for index entries written
    before regions carried their own."""
    for path in glob.glob(os.path.join(regions_dir, '*.json')):
        with open(path, encoding='utf-8') as fh:
            region = json.load(fh)
        if region['id'] == region_id:
            return of_region(region)
    return compose([])
