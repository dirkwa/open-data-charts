import numpy as np
import pytest

pytest.importorskip('osgeo')
from osgeo import gdal  # noqa: E402

from odc import chart, run_unit  # noqa: E402


def write_grid(path):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 4, 4, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((20.0, 0.25, 0, 65.0, 0, -0.25))
    ds.GetRasterBand(1).WriteArray(np.full((4, 4), -20.0, np.float32))
    ds = None


def test_a_charted_region_with_a_model_hands_both_to_the_builder(tmp_path):
    inputs = tmp_path / 'inputs'
    (inputs / 'dem').mkdir(parents=True)
    (inputs / 'chart').mkdir()
    write_grid(inputs / 'dem' / 'emodnet_E4.tif')
    name = chart.file_name(1, 'DEPARE', f'traficom-DepthArea_A{chart.square_name(24, 65)}')
    (inputs / 'chart' / name).write_text('{"type": "FeatureCollection", "features": []}')
    args = run_unit.build_args(str(inputs), 'surveys', 'index.json', 'z9-0-0', str(tmp_path / 'work'), 'out')
    assert '--chart' in args and '--dem' in args
