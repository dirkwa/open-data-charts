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


def of_regions(region_ids, regions_dir=REGIONS_DIR):
    """One attribution for an index that lists units of all these regions."""
    regional = []
    for path in sorted(glob.glob(os.path.join(regions_dir, '*.json'))):
        with open(path, encoding='utf-8') as fh:
            region = json.load(fh)
        if region['id'] in region_ids:
            regional += [a for a in region.get('attribution', []) if a not in regional]
    return compose(regional)
