"""Tile a unit's GeoJSON layers into one vector MBTiles cut to the unit.

Flags follow signalk-charts-provider-simple's S-57 tiling, except that line
simplification and tiny-polygon reduction stay on: full detail gave files
several times larger, for detail the 100 m DEM does not have. Shared borders
between depth bands are still simplified identically. -r1 keeps every point
(no lights dropped by rate). Soundings and other details carry a
per-feature tippecanoe minzoom, standing in for SCAMIN.

Usage: python3 -m odc.tile <geojson-dir> <out.mbtiles> <unit id> <name> [maxzoom]
"""

import glob
import json
import os
import sqlite3
import subprocess
import sys

from odc.grid import UNIT_ZOOM, Unit

DESCRIPTION = (
    'Built from open data: Shom (WFS layers, bathymetric surveys, bathymetric model; depths in m '
    'below LAT), Gouvernement de la Nouvelle-Caledonie (reef geomorphology, OBLIC coastal '
    'structures), OpenStreetMap and the Copernicus DEM. NOT FOR NAVIGATION.'
)
ATTRIBUTION = (
    'Shom, Licence Ouverte Etalab 2.0; Shom bathymetric datasets (doi:10.17183/S..., see INFORM '
    'of each sounding); Shom-IRD 2021 doi:10.17183/MNT_NC100m_TSUCAL_WGS84; Gouvernement de la '
    'Nouvelle-Caledonie (IRD/DINUM reef geomorphology, DIMENC/OBLIC), Licence Ouverte; '
    '(c) OpenStreetMap contributors, ODbL; Copernicus DEM GLO-30 (c) DLR e.V. 2010-2014 and '
    '(c) Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union '
    'and ESA'
)


def tippecanoe_args(geojson_dir, out, unit, name, maxzoom):
    minlon, minlat, maxlon, maxlat = unit.bounds
    layers = [
        f'--named-layer={os.path.basename(f)[:-8]}:{f}'
        for f in sorted(glob.glob(os.path.join(geojson_dir, '*.geojson')))
    ]
    return [
        'tippecanoe',
        '-o',
        out,
        '-n',
        name,
        f'--description={DESCRIPTION}',
        f'--attribution={ATTRIBUTION}',
        '-Z',
        str(UNIT_ZOOM),
        '-z',
        str(maxzoom),
        '-r1',
        '--no-tile-size-limit',
        '--no-feature-limit',
        '--detect-shared-borders',
        '--no-simplification-of-shared-nodes',
        '--buffer=80',
        f'--clip-bounding-box={minlon!r},{minlat!r},{maxlon!r},{maxlat!r}',
        '--force',
        '--quiet',
        *layers,
    ]


def set_metadata(path, values):
    with sqlite3.connect(path) as db:
        for key, value in values.items():
            db.execute('DELETE FROM metadata WHERE name = ?', (key,))
            db.execute('INSERT INTO metadata (name, value) VALUES (?, ?)', (key, value))


def keep_unit_tiles(path, unit):
    """Drop the tiles tippecanoe adds around the unit for its buffer: they
    belong to the neighbours, and each tile must come from one unit."""
    with sqlite3.connect(path) as db:
        is_view = db.execute("SELECT type FROM sqlite_master WHERE name = 'tiles'").fetchone()[0] == 'view'
        table = 'map' if is_view else 'tiles'
        for (z,) in db.execute(f'SELECT DISTINCT zoom_level FROM {table}').fetchall():
            shift = z - UNIT_ZOOM
            x0, x1 = unit.x << shift, ((unit.x + 1) << shift) - 1
            # MBTiles rows count from the south (TMS).
            rows = 1 << z
            y0, y1 = rows - ((unit.y + 1) << shift), rows - (unit.y << shift) - 1
            db.execute(
                f'DELETE FROM {table} WHERE zoom_level = ? AND NOT '
                '(tile_column BETWEEN ? AND ? AND tile_row BETWEEN ? AND ?)',
                (z, x0, x1, y0, y1),
            )
        if is_view:
            db.execute('DELETE FROM images WHERE tile_id NOT IN (SELECT tile_id FROM map)')
    with sqlite3.connect(path) as db:
        db.execute('VACUUM')


def main():
    geojson_dir, out, unit_id, name = sys.argv[1:5]
    maxzoom = int(sys.argv[5]) if len(sys.argv) > 5 else 15
    unit = Unit.parse(unit_id)
    subprocess.run(tippecanoe_args(geojson_dir, out, unit, name, maxzoom), check=True)
    keep_unit_tiles(out, unit)
    set_metadata(out, {'type': 'S-57', 'bounds': ','.join(repr(v) for v in unit.bounds), 'x-odc-unit': unit.id})
    with sqlite3.connect(out) as db:
        tiles = db.execute('SELECT count(*) FROM tiles').fetchone()[0]
    print(json.dumps({'unit': unit.id, 'tiles': tiles, 'bytes': os.path.getsize(out)}))


if __name__ == '__main__':
    main()
