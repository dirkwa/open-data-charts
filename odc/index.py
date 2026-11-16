"""Write the published index (docs/index-format.md) after a region's build.

Usage:
  python3 -m odc.index <region.json> <plan.json> <built dir> <old site dir> <mirror dir> <released.json> <out dir>

<built dir> holds <unit>.<version>.mbtiles and <unit>.json (odc.build_unit's
unit.json) for every unit the plan built. <old site dir> holds what Pages
serves now (any file may be missing on the first run). <mirror dir> holds the mirror's
mirror-index.json and footprints.geojson. <released.json> lists the asset
names in the region's release now. Writes open-data-index.json,
coverage.geojson, retired.json (asset name -> region and the time it left
the index) and delete-assets.json (the region's retired assets past the
grace period).
"""

import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta

from osgeo import ogr

from odc.build_unit import quality_category
from odc.grid import UNIT_ZOOM, Unit

SCHEMA_VERSION = 1
REPO = 'https://github.com/dirkwa/open-data-charts'
MAXZOOM = 15
GRACE_DAYS = 14
COVERAGE_SIMPLIFY_DEG = 0.0005
DISCLAIMER = (
    'Not for navigation. These charts are built automatically from open data, are not official '
    'nautical charts and must not be used as the sole means of navigation.'
)
ATTRIBUTION = (
    'Shom, Licence Ouverte Etalab 2.0; Shom bathymetric surveys (doi:10.17183/<survey>); '
    'Shom-IRD 2021 doi:10.17183/MNT_NC100m_TSUCAL_WGS84; Gouvernement de la Nouvelle-Caledonie, '
    'Licence Ouverte; (c) OpenStreetMap contributors, ODbL; Copernicus DEM GLO-30 (c) DLR e.V. '
    '2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the '
    'European Union and ESA'
)
# Each unit is a database derived from OpenStreetMap, so the whole unit is
# published under the ODbL; the sources' own licences and attributions still apply.
UNIT_LICENSE = 'odbl-1.0'
LICENSES = [
    {
        'id': 'etalab-2.0',
        'name': 'Licence Ouverte Etalab 2.0',
        'url': 'https://www.etalab.gouv.fr/licence-ouverte-open-licence/',
        'attribution': 'Shom; Gouvernement de la Nouvelle-Caledonie',
    },
    {
        'id': 'odbl-1.0',
        'name': 'Open Database License 1.0',
        'url': 'https://opendatacommons.org/licenses/odbl/1-0/',
        'attribution': '(c) OpenStreetMap contributors',
    },
    {
        'id': 'copernicus-dem',
        'name': 'Copernicus DEM licence',
        'url': 'https://spacedata.copernicus.eu/documents/20123/121286/CSCDA_ESA_Mission-specific+Annex.pdf',
        'attribution': 'Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018',
    },
]


def load(path, default=None):
    if path == '-' or not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def asset_name(unit_id, version):
    return f'{unit_id}.{version}.mbtiles'


def unit_entry(region, unit_id, version, built_dir, today):
    unit = Unit.parse(unit_id)
    path = os.path.join(built_dir, asset_name(unit_id, version))
    info = load(os.path.join(built_dir, f'{unit_id}.json'))
    entry = {
        'id': unit_id,
        'region': region['id'],
        'z': UNIT_ZOOM,
        'x': unit.x,
        'y': unit.y,
        'bbox': [round(v, 6) for v in unit.bounds],
        'version': version,
        'url': f'{REPO}/releases/download/units-{region["id"]}/{asset_name(unit_id, version)}',
        'size': os.path.getsize(path),
        'sha256': sha256_of(path),
        'minzoom': UNIT_ZOOM,
        'maxzoom': MAXZOOM,
        'built': today,
        'surveys': len(info['surveys']),
    }
    if info['quality'] is not None:  # a unit without a water cell in the depth grid has none
        entry['quality'] = info['quality']
    return entry


def region_entry(region):
    return {
        'id': region['id'],
        'name': region['name'],
        'bbox': region['bbox'],
        'overview': None,
        'sources': region.get('sources', []),
    }


def build_index(region, plan, built_dir, old, today):
    old = old or {'units': [], 'regions': []}
    kept = {u['id']: u for u in old['units'] if u['region'] == region['id'] and u['id'] in plan['kept']}
    units = [u for u in old['units'] if u['region'] != region['id']]
    for unit_id, version in plan['versions'].items():
        units.append(kept.get(unit_id) or unit_entry(region, unit_id, version, built_dir, today))
    units.sort(key=lambda u: (u['region'], u['id']))
    regions = [r for r in old['regions'] if r['id'] != region['id']] + [region_entry(region)]
    regions.sort(key=lambda r: r['id'])
    index = {
        'schemaVersion': SCHEMA_VERSION,
        'disclaimer': DISCLAIMER,
        'attribution': ATTRIBUTION,
        'license': UNIT_LICENSE,
        'licenses': LICENSES,
        'regions': regions,
        'units': units,
    }
    unchanged = {k: v for k, v in old.items() if k != 'generated'} == index
    index['generated'] = old['generated'] if unchanged else datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    return index, not unchanged


def retire(old, index, retired, now, region_id, released):
    """The retirement ledger and this region's assets past the grace period.

    The ledger names each asset the index no longer lists, with its region
    and the time it left the index. An entry stays until its asset is gone
    from the region's release (`released`), so a failed deletion is retried
    by the next run."""
    current = {u['url'].rsplit('/', 1)[1] for u in index['units']}
    ledger = {name: entry for name, entry in (retired or {}).items() if name not in current}
    for unit in (old or {}).get('units', []):
        name = unit['url'].rsplit('/', 1)[1]
        if name not in current:
            ledger.setdefault(name, {'region': unit['region'], 'since': now})
    ledger = {name: entry for name, entry in ledger.items() if entry['region'] != region_id or name in released}
    cutoff = datetime.fromisoformat(now) - timedelta(days=GRACE_DAYS)
    delete = sorted(
        name
        for name, entry in ledger.items()
        if entry['region'] == region_id and datetime.fromisoformat(entry['since']) <= cutoff
    )
    return dict(sorted(ledger.items())), delete


def coverage(region, mirror_dir, old):
    mirror_index = load(os.path.join(mirror_dir, 'mirror-index.json'))
    footprints = load(os.path.join(mirror_dir, 'footprints.geojson'))
    features = [f for f in (old or {}).get('features', []) if f['properties'].get('region') != region['id']]
    for feature in footprints['features']:
        name = feature['properties']['name']
        meta = mirror_index['surveys'].get(name)
        if meta is None or region['id'] not in meta.get('regions', []):
            continue
        geometry = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
        geometry = geometry.SimplifyPreserveTopology(COVERAGE_SIMPLIFY_DEG)
        features.append(
            {
                'type': 'Feature',
                'properties': {
                    'region': region['id'],
                    'name': name,
                    'end': str(meta['end'])[:8],
                    'category': quality_category(meta),
                },
                'geometry': json.loads(geometry.ExportToJson(options=['COORDINATE_PRECISION=5'])),
            }
        )
    features.sort(key=lambda f: (f['properties']['region'], f['properties']['name']))
    return {'type': 'FeatureCollection', 'features': features}


def write(path, value):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(value, fh, indent=1, ensure_ascii=False)


def main():
    region_path, plan_path, built_dir, old_dir, mirror_dir, released_path, out = sys.argv[1:8]
    region, plan = load(region_path), load(plan_path)
    old = load(os.path.join(old_dir, 'open-data-index.json'))
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.date().isoformat()
    index, changed = build_index(region, plan, built_dir, old, today)
    retired, delete = retire(
        old,
        index,
        load(os.path.join(old_dir, 'retired.json'), {}),
        now.isoformat(),
        region['id'],
        set(load(released_path)),
    )
    os.makedirs(out, exist_ok=True)
    write(os.path.join(out, 'open-data-index.json'), index)
    old_coverage = load(os.path.join(old_dir, 'coverage.geojson'))
    write(os.path.join(out, 'coverage.geojson'), coverage(region, mirror_dir, old_coverage))
    write(os.path.join(out, 'retired.json'), retired)
    write(os.path.join(out, 'delete-assets.json'), delete)
    print(f'{len(index["units"])} units in the index, changed={changed}, {len(delete)} assets to delete')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as fh:
            fh.write(f'changed={"true" if changed else "false"}\n')


if __name__ == '__main__':
    main()
