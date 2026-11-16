"""Build and tile units from a region's inputs (odc.sources) and reduced surveys.

Usage: python3 -m odc.run_unit <inputs dir> <surveys dir> <mirror-index.json> <plan.json> <out dir> <unit id> ...

Writes <out>/<unit>.<version>.mbtiles and <out>/<unit>.json for each unit.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

from osgeo import gdal

from odc.grid import MAX_ZOOM
from odc.tile import set_metadata

gdal.UseExceptions()


def dem_mosaic(inputs, work):
    """All of the package's DEM grids as one raster, as odc.units hashes them."""
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, 'dem.vrt')
    gdal.BuildVRT(path, sorted(glob.glob(os.path.join(inputs, 'dem', '*.asc'))))
    return path


def build_args(inputs, surveys_dir, mirror_index, unit_id, work, geojson):
    args = [
        sys.executable,
        '-m',
        'odc.build_unit',
        '--unit',
        unit_id,
        '--wfs',
        os.path.join(inputs, 'wfs'),
        '--land',
        os.path.join(inputs, 'land.gpkg'),
        '--dem',
        dem_mosaic(inputs, work),
        '--leves',
        surveys_dir,
        '--surveys',
        mirror_index,
        '--maxzoom',
        str(MAX_ZOOM),
        '--work',
        work,
        '--out',
        geojson,
    ]
    optional = {
        '--osm': os.path.join(inputs, 'osm.geojsonseq'),
        '--nc-reefs': os.path.join(inputs, 'nc', 'reefs.geojson'),
        '--nc-structures': os.path.join(inputs, 'nc', 'structures.geojson'),
    }
    args += [item for flag, path in optional.items() if os.path.exists(path) for item in (flag, path)]
    relief = sorted(glob.glob(os.path.join(inputs, 'relief', '*.tif')))
    if relief:
        args += ['--relief-dem', *relief]
    return args


def run_unit(inputs, surveys_dir, mirror_index, unit_id, version, out):
    with tempfile.TemporaryDirectory() as tmp:
        work, geojson = os.path.join(tmp, 'work'), os.path.join(tmp, 'geojson')
        subprocess.run(build_args(inputs, surveys_dir, mirror_index, unit_id, work, geojson), check=True)
        mbtiles = os.path.join(out, f'{unit_id}.{version}.mbtiles')
        name = f'Open Data Charts {unit_id} (not for navigation)'
        subprocess.run([sys.executable, '-m', 'odc.tile', geojson, mbtiles, unit_id, name, str(MAX_ZOOM)], check=True)
        set_metadata(mbtiles, {'x-odc-version': version})
        shutil.copy(os.path.join(geojson, 'unit.json'), os.path.join(out, f'{unit_id}.json'))


def main():
    inputs, surveys_dir, mirror_index, plan_path, out, *unit_ids = sys.argv[1:]
    with open(plan_path, encoding='utf-8') as fh:
        versions = json.load(fh)['versions']
    os.makedirs(out, exist_ok=True)
    for unit_id in unit_ids:
        print(f'== {unit_id}', flush=True)
        run_unit(inputs, surveys_dir, mirror_index, unit_id, versions[unit_id], out)


if __name__ == '__main__':
    main()
