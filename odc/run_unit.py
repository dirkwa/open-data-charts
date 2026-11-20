"""Build and tile units from a region's inputs (odc.sources) and reduced surveys.

Usage: python3 -m odc.run_unit <inputs dir> <surveys dir> <mirror-index.json> <plan.json> <out dir> <unit id> ...

Writes <out>/<unit>.<version>.mbtiles and <out>/<unit>.json for each unit,
and <out>/<unit>.<version>.hillshade.mbtiles for a unit with terrain to shade.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

from osgeo import gdal

from odc import chart, hillshade
from odc.grid import MAX_ZOOM, Unit
from odc.sources import dem_grids
from odc.tile import set_metadata

gdal.UseExceptions()


def dem_mosaic(inputs, work):
    """All of the region's DEM grids as one raster, as odc.units hashes them,
    or None for a region without a DEM."""
    grids = dem_grids(inputs)
    if not grids:
        return None
    os.makedirs(work, exist_ok=True)
    path = os.path.join(work, 'dem.vrt')
    gdal.BuildVRT(path, grids)
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
        '--seamarks': os.path.join(inputs, 'seamarks.geojsonseq'),
        '--water': os.path.join(inputs, 'water.geojsonseq'),
        '--nc-reefs': os.path.join(inputs, 'nc', 'reefs.geojson'),
        '--nc-structures': os.path.join(inputs, 'nc', 'structures.geojson'),
        '--aca-reefs': os.path.join(inputs, 'aca'),
    }
    args += [item for flag, path in optional.items() if os.path.exists(path) for item in (flag, path)]
    charts = os.path.join(inputs, 'chart')
    if chart.chart_files(charts):
        return args + ['--chart', charts] + relief_args(inputs)
    dem = dem_mosaic(inputs, work)
    if dem:
        args += ['--dem', dem]
    deep = sorted(glob.glob(os.path.join(inputs, 'etopo', '*.tif')))
    if deep:
        args += ['--deep-dem', *deep]
    satellite = sorted(glob.glob(os.path.join(inputs, 'aca-depth', '*.tif')))
    if satellite:
        args += ['--satellite-depth', *satellite]
    return args + relief_args(inputs)


def relief_args(inputs):
    relief = sorted(glob.glob(os.path.join(inputs, 'relief', '*.tif')))
    return ['--relief-dem', *relief] if relief else []


def run_unit(inputs, surveys_dir, mirror_index, unit_id, version, attribution, out):
    with tempfile.TemporaryDirectory() as tmp:
        work, geojson = os.path.join(tmp, 'work'), os.path.join(tmp, 'geojson')
        subprocess.run(build_args(inputs, surveys_dir, mirror_index, unit_id, work, geojson), check=True)
        mbtiles = os.path.join(out, f'{unit_id}.{version}.mbtiles')
        name = f'Open Data Charts {unit_id} (not for navigation)'
        subprocess.run(
            [sys.executable, '-m', 'odc.tile', geojson, mbtiles, unit_id, name, str(MAX_ZOOM), attribution], check=True
        )
        set_metadata(mbtiles, {'x-odc-version': version})
        relief = sorted(glob.glob(os.path.join(inputs, 'relief', '*.tif')))
        shaded = os.path.join(out, hillshade.asset_name(unit_id, version))
        hillshade.write_unit(
            relief,
            os.path.join(inputs, 'land.gpkg'),
            shaded,
            Unit.parse(unit_id),
            version,
            os.path.join(inputs, 'water.geojsonseq'),
        )
        shutil.copy(os.path.join(geojson, 'unit.json'), os.path.join(out, f'{unit_id}.json'))


def main():
    inputs, surveys_dir, mirror_index, plan_path, out, *unit_ids = sys.argv[1:]
    with open(plan_path, encoding='utf-8') as fh:
        plan = json.load(fh)
    os.makedirs(out, exist_ok=True)
    for unit_id in unit_ids:
        print(f'== {unit_id}', flush=True)
        run_unit(inputs, surveys_dir, mirror_index, unit_id, plan['versions'][unit_id], plan['attribution'], out)


if __name__ == '__main__':
    main()
