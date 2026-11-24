import json
import os

import numpy as np
import pytest
from osgeo import gdal, osr

from odc import litto3d, mirror
from odc.build_unit import quality_category, survey_doi, survey_inform
from odc.mirror import index_entry

# Saint-Malo: 0330_6855 is the package whose north-west corner is at
# x 330 km, y 6855 km in Lambert-93.
FILES = [{'fileName': '0330_6855.7z', 'fileSize': 100, 'fileMd5': 'b' * 32}]


def to_lambert(lon, lat):
    source = osr.SpatialReference()
    source.ImportFromEPSG(4326)
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(2154)
    x, y, _ = osr.CoordinateTransformation(source, target).TransformPoint(lon, lat)
    return x, y


def test_a_package_is_a_lidar_survey_over_its_five_kilometres():
    survey = litto3d.survey('LITTO3D_BZH_2018_2021', '0330_6855', 'L3D_BZH_2018_2021')
    assert survey['name'] == 'LITTO3D_BZH_2018_2021_0330_6855'
    assert survey['group'] == 'LITTO3D_BZH_2018_2021_PACK_DL'
    assert (survey['start'], survey['end']) == ('20180101', '20211231')
    w, s, e, n = survey['bbox']
    for x, y in ((330_000, 6_850_000), (335_000, 6_855_000)):
        corner = litto3d.to_wgs84().TransformPoint(x, y)
        assert w < corner[0] < e and s < corner[1] < n
    assert litto3d.survey('LITTO3D_LR_2009', '0800_6295', None)['end'] == '20091231'
    assert 'doi' not in litto3d.survey('LITTO3D_LR_2009', '0800_6295', None)


def test_packages_come_from_the_products_catalogue_records(monkeypatch):
    monkeypatch.setattr(
        litto3d,
        'records',
        lambda text: [
            'BATHYMETRIE_LITTO3D_BZH_2018_2021_PREPAQUET_0330_6855.xml',
            'BATHYMETRIE_LITTO3D_BZH_2018_2021.xml',
            'BATHYMETRIE_LITTO3D_BZH_2018_2021_PREPAQUET_0330_6855.xml',
            'BATHYMETRIE_L3D_MAR_LITTO3D_BZH_2018_2021_PREPAQUET_0100_6800.xml',
        ],
    )
    assert litto3d.packages('LITTO3D_BZH_2018_2021') == ['0330_6855']


def test_discovery_keeps_the_packages_meeting_a_region_that_lists_the_product(monkeypatch):
    monkeypatch.setattr(litto3d, 'product_doi', lambda product: 'L3D_BZH_2018_2021')
    monkeypatch.setattr(litto3d.shom, 'list_files', lambda package: FILES)
    regions = [
        {'id': 'france-channel', 'bbox': [-5.3, 48.46, 2.6, 51.15], 'litto3d': ['LITTO3D_BZH_2018_2021']},
        {'id': 'corsica', 'bbox': [8.44, 41.3, 9.6, 43.05], 'litto3d': ['L3D_MAR_CORSE_2017_2018']},
    ]
    monkeypatch.setattr(litto3d, 'packages', lambda product: ['0330_6855'] if 'BZH' in product else [])
    [survey] = litto3d.discover(regions)
    assert survey['regions'] == ['france-channel'] and survey['size'] == 100


def test_litto3d_charts_as_lidar_with_its_products_citation():
    survey = {
        **litto3d.survey('LITTO3D_BZH_2018_2021', '0330_6855', 'L3D_BZH_2018_2021'),
        'files': FILES,
        'size': 100,
        'md5': mirror.source_md5(FILES),
        'regions': ['france-channel'],
    }
    entry = index_entry(survey, {'release': 'mirror-b', 'name': 'x.npz', 'size': 1, 'digest': 'sha256:00'})
    assert entry['provider'] == 'shom-litto3d' and entry['source']['md5'] == 'b' * 32
    assert quality_category(entry) == 'multibeam'
    assert (
        survey_inform(survey['name'], entry)
        == 'Shom - IGN Litto3D LITTO3D_BZH_2018_2021 (2021), doi:10.17183/L3D_BZH_2018_2021'
    )
    assert survey_doi(survey['name'], entry) == {'doi': '10.17183/L3D_BZH_2018_2021'}
    old = {k: v for k, v in entry.items() if k != 'doi'} | {'product': 'LITTO3D_LR_2009', 'end': '20091231'}
    assert (
        survey_inform('LITTO3D_LR_2009_0800_6295', old)
        == 'Shom - IGN Litto3D LITTO3D_LR_2009 (2009), Licence Ouverte 2.0'
    )
    assert survey_doi('LITTO3D_LR_2009_0800_6295', old) == {}


def lambert_raster(path, values, x0, y0, step, dtype, nodata):
    driver = gdal.GetDriverByName('AAIGrid' if path.endswith('.asc') else 'GTiff')
    mem = gdal.GetDriverByName('MEM').Create('', values.shape[1], values.shape[0], 1, dtype)
    mem.SetGeoTransform([x0, step, 0, y0, 0, -step])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(2154)
    mem.SetProjection(srs.ExportToWkt())
    band = mem.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(values)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    driver.CreateCopy(path, mem)


def constant_surface(path, value):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 200, 200, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([-5.0, 0.05, 0, 50.0, 0, -0.05])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(litto3d.SURFACE_NODATA)
    ds.GetRasterBand(1).Fill(value)
    ds = None
    return str(path)


def square(tmp_path, with_masks=True):
    """One square kilometre near Saint-Malo: four 1 m nodes, each in a chart
    cell of its own, with a 5 m model beside them that must not be used."""
    folder = tmp_path / 'x' / '0330_6855' / 'LITTO3D_FRA_0330_6855_20231201_LAMB93_RGF93_IGN69'
    x0, y0 = to_lambert(-2.03, 48.65)
    x0, y0 = round(x0), round(y0)
    spacing = 40  # metres between the nodes, more than a chart cell
    heights = np.full((3 * spacing + 1, 3 * spacing + 1), -99999.0)
    sources = np.full(heights.shape, 0, np.uint8)
    distances = np.full(heights.shape, 255, np.uint8)
    nodes = [(-12.0, 30, 0), (-8.0, 69, 0), (-5.0, 30, 25), (9.0, 52, 0)]
    for i, (h, src, dst) in enumerate(nodes):
        heights[0, i * spacing] = h
        sources[0, i * spacing] = src
        distances[0, i * spacing] = dst
    lambert_raster(str(folder / 'MNT1m' / 'm.asc'), heights, x0, y0, 1.0, gdal.GDT_Float32, -99999)
    lambert_raster(str(folder / 'MNT5m' / 'm5.asc'), np.full((2, 2), -50.0), x0, y0, 5.0, gdal.GDT_Float32, -99999)
    if with_masks:
        lambert_raster(str(folder / 'Masque_Source' / 'T_SRC_.tif'), sources, x0, y0, 1.0, gdal.GDT_Byte, 255)
        lambert_raster(str(folder / 'Masque_Distance' / 'T_DST_.tif'), distances, x0, y0, 1.0, gdal.GDT_Byte, 255)
    return str(folder)


def surfaces(tmp_path):
    # ZH 6 m below NGF-IGN69, the highest tide 7 m above it (Saint-Malo).
    paths = {
        'IGN69': {
            'ZH': constant_surface(tmp_path / 'zh.tif', -6.0),
            'PHMA': constant_surface(tmp_path / 'phma.tif', 7.0),
        }
    }
    return litto3d.load_surfaces(paths)


def test_a_square_gives_measured_nodes_below_the_highest_tide_on_chart_datum(tmp_path):
    rows = litto3d.square_rows(square(tmp_path), surfaces(tmp_path), litto3d.to_wgs84())
    # The flattened harbour surface (69), the node interpolated over 25 m
    # and the land above the highest tide are left out; -12 m on NGF is
    # 6 m below chart datum.
    assert rows[:, 2].tolist() == [6.0]
    assert rows[0, 0] == pytest.approx(-2.03, abs=1e-3) and rows[0, 1] == pytest.approx(48.65, abs=1e-3)


def test_a_square_without_masks_keeps_every_node_below_the_highest_tide(tmp_path):
    rows = litto3d.square_rows(square(tmp_path, with_masks=False), surfaces(tmp_path), litto3d.to_wgs84())
    assert sorted(rows[:, 2].tolist()) == [-1.0, 2.0, 6.0]


def test_a_square_on_an_unknown_datum_is_refused(tmp_path):
    folder = square(tmp_path)
    renamed = folder.replace('IGN69', 'NGG77')
    os.rename(folder, renamed)
    with pytest.raises(ValueError, match='levelling datum'):
        litto3d.square_rows(renamed, surfaces(tmp_path), litto3d.to_wgs84())


def test_the_datums_are_built_once_per_run(tmp_path, monkeypatch):
    (tmp_path / 'litto3d-datums.json').write_text(json.dumps({'IGN69': {'ZH': 'a', 'PHMA': 'b'}}))
    monkeypatch.setattr(litto3d, 'bathyelli', lambda level, tmp: pytest.fail('fetched again'))
    assert litto3d.datums(str(tmp_path)) == {'IGN69': {'ZH': 'a', 'PHMA': 'b'}}
