import zipfile

import numpy as np
import pytest
from osgeo import gdal, osr

from odc import svdb
from odc.build_unit import quality_category, survey_inform
from odc.mirror import index_entry

CAPABILITIES = b"""<?xml version="1.0"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0"><Capability>
<Layer><Name>Layers</Name><Title>Layers</Title>
 <Layer><Name>SVDB</Name><Title>SVDB</Title>
  <Layer><Name>svdb_public</Name><Title>svdb_public</Title><Abstract>02.04.2026-20.07.2012</Abstract>
   <EX_GeographicBoundingBox><westBoundLongitude>5.5</westBoundLongitude><eastBoundLongitude>14.4</eastBoundLongitude>
   <southBoundLatitude>53.4</southBoundLatitude><northBoundLatitude>55.2</northBoundLatitude></EX_GeographicBoundingBox>
   <Layer><Name>c43b3</Name><Title>202260103_T</Title><Abstract>28.03.2026-31.03.2026</Abstract>
    <EX_GeographicBoundingBox><westBoundLongitude>10.839</westBoundLongitude><eastBoundLongitude>10.888</eastBoundLongitude>
    <southBoundLatitude>54.467</southBoundLatitude><northBoundLatitude>54.518</northBoundLatitude></EX_GeographicBoundingBox>
   </Layer>
  </Layer>
 </Layer>
</Layer></Capability></WMS_Capabilities>"""

RECORD = {
    'Name': 'c43b3',
    'Titel': '202260103_T',
    'Datenart': '1m Raster',
    'Unterkategorie': 'Fächerecholot',
    'Datenpunkte': '6777859',
}


def test_the_datasets_are_the_leaf_layers_with_their_dates_and_extent():
    assert svdb.layers(CAPABILITIES) == [
        {
            'id': 'c43b3',
            'title': '202260103_T',
            'start': '20260328',
            'end': '20260331',
            'bbox': [10.839, 54.467, 10.888, 54.518],
        }
    ]


def test_a_renumbered_layer_keeps_its_survey_and_asset():
    [layer] = svdb.layers(CAPABILITIES)
    survey = svdb.survey_from_layer(layer, RECORD)
    assert survey['name'] == 'BSH-202260103_T'
    assert survey['tecsou'] == 'multi-beam'
    assert svdb.survey_from_layer({**layer, 'id': 'ffff0'}, RECORD)['md5'] == survey['md5']
    assert svdb.survey_from_layer({**layer, 'end': '20260401'}, RECORD)['md5'] != survey['md5']


def test_svdb_surveys_chart_as_bsh_multibeam_without_a_doi():
    [layer] = svdb.layers(CAPABILITIES)
    survey = {**svdb.survey_from_layer(layer, RECORD), 'regions': ['germany-baltic']}
    entry = index_entry(survey, {'release': 'mirror-1', 'name': 'x.npz', 'size': 1, 'digest': 'sha256:00'})
    assert entry['provider'] == 'bsh-svdb' and entry['source']['layer'] == 'c43b3'
    assert quality_category(entry) == 'multibeam'
    assert survey_inform(survey['name'], entry) == 'BSH survey 202260103_T (2026), SVDB, CC0'
    single = svdb.survey_from_layer(layer, {**RECORD, 'Datenart': 'Vermessungsdaten', 'Unterkategorie': 'Echolot'})
    assert quality_category(single) == 'survey-recent'


def separation_tif(path, value):
    """A constant NHN-above-LAT surface over German waters, as german_separation writes it."""
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 100, 100, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([5.0, 0.1, 0, 60.0, 0, -0.1])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999.0)
    ds.GetRasterBand(1).Fill(value)
    ds = None
    return str(path)


def test_the_separation_is_interpolated_and_missing_outside_its_grid(tmp_path):
    sep = svdb.Separation(separation_tif(tmp_path / 'sep.tif', 0.4))
    values = sep.at(np.array([10.5, 30.0]), np.array([54.5, 54.5]))
    assert values[0] == pytest.approx(0.4)
    assert np.isnan(values[1])


def layer_zip(path, vertdat='7837'):
    meta = (
        'integer;string;string;string\n'
        'srid;vertdat;data_type;data_subtype\n'
        f'25832;{vertdat};1m Raster;Fächerecholot\n'
        '1;4;2;2\n'
    )
    # Two soundings in one grid cell (1 m apart) and one in another.
    xyz = '619312.0 6038860.0 13.95\n619313.0 6038860.0 13.50\n619412.0 6038860.0 14.00\n'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('202260103_T.csv', meta)
        archive.writestr('202260103_T.xyz', xyz)
    return str(path)


def test_a_layer_reduces_to_lat_on_the_chart_grid(tmp_path):
    sep = svdb.Separation(separation_tif(tmp_path / 'sep.tif', 0.4))
    reduced, soundings, meta = svdb.reduce_zip(layer_zip(tmp_path / 'layer.zip'), sep)
    assert soundings == 3
    assert meta['data_type'] == '1m Raster'
    assert sorted(np.round(reduced['z'], 2)) == [13.1, 13.6]
    lon, lat = reduced['lon'] / 1e7, reduced['lat'] / 1e7
    assert ((lon > 10.83) & (lon < 10.85) & (lat > 54.48) & (lat < 54.50)).all()


def test_a_layer_on_another_datum_is_refused(tmp_path):
    sep = svdb.Separation(separation_tif(tmp_path / 'sep.tif', 0.4))
    with pytest.raises(ValueError, match='vertical datum'):
        svdb.reduce_zip(layer_zip(tmp_path / 'layer.zip', vertdat='5783'), sep)


def test_a_layer_missing_from_the_search_is_held(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return CAPABILITIES

    monkeypatch.setattr(svdb, 'get', lambda url, **kw: Response())
    monkeypatch.setattr(svdb, 'search', lambda: {})
    [survey] = svdb.discover({'germany-baltic': [10, 54, 11, 55]})
    assert survey['held'] and survey['name'] == 'BSH-202260103_T' and survey['regions'] == ['germany-baltic']
