import numpy as np
import pytest
from osgeo import gdal, osr

from odc import ausseabed
from odc.build_unit import survey_inform
from odc.grid import CELLS_PER_DEGREE, GRID_STEP
from odc.mirror import index_entry

CC_BY = ausseabed.LICENCE


def properties(**overrides):
    return {
        'NAME': 'Darwin Harbour Bathymetry 1m 2011',
        'DATA_URL': 'https://files.ausseabed.gov.au/survey/DarwinHarbour-Bathymetry-1m-2011.zip',
        'LEGAL_CONSTRAINTS': CC_BY,
        'PRODUCT_STATUS': 'PUBLISHED',
        'EMBARGO': 'No',
        'BATHY_TYPES': 'Multibeam',
        'START_DATE': '2011-05-02Z',
        'END_DATE': '2011-05-20Z',
        **overrides,
    }


def feature(bbox=(130.7, -12.6, 130.9, -12.4), **overrides):
    w, s, e, n = bbox
    return {
        'properties': properties(**overrides),
        'geometry': {'type': 'MultiPolygon', 'coordinates': [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]]},
    }


@pytest.mark.parametrize(
    'name, resolution',
    [
        ('Darwin Harbour Bathymetry 1m 2011', 1.0),
        ('Southern Great Barrier Reef Shelf Bathymetry 2-64m 2020', 2.0),
        ('Wilsons Promontory Marine National Park Bathymetry 0.25m 2013', 0.25),
        ('Lord Howe Rise Bathymetry 70-90m 2017', 70.0),
        ('Abbot Point to Hydrographers Passage, QLD Bathymetry (HIPP SI 1007) 30m 2020', 30.0),
        ('Arafura Sea Bathymetry 5-8m SS 05/2005', 5.0),
        ('Some survey without a resolution', None),
    ],
)
def test_the_finest_resolution_comes_from_the_register_name(name, resolution):
    assert ausseabed.finest_resolution(name) == resolution


@pytest.mark.parametrize(
    'overrides, reason',
    [
        ({}, None),
        ({'LEGAL_CONSTRAINTS': 'Restricted'}, 'licence'),
        ({'LEGAL_CONSTRAINTS': 'Creative Commons - Attribution-NonCommercial 4.0'}, 'licence'),
        ({'PRODUCT_STATUS': 'PLANNED'}, 'not published'),
        ({'DATA_URL': 'https://data.csiro.au/collection/csiro:57351'}, 'not on files.ausseabed.gov.au'),
        ({'NAME': 'Forster, NSW Backscatter 5m 2019'}, 'backscatter'),
        ({'NAME': 'Fremantle to Hobart Bathymetry 128m 1997'}, 'coarse'),
        ({'NAME': 'Abbot Point to Hydrographers Passage, QLD Bathymetry (HIPP SI 1007) 30m 2020'}, 'coarse'),
        ({'NAME': 'Seamounts, Canyons and Reefs of the Coral Sea Bathymetry 16-64m 2020'}, None),
    ],
)
def test_only_published_cc_by_depth_surveys_on_the_file_server_are_mirrored(overrides, reason):
    assert ausseabed.wanted(properties(**overrides)) == reason


def test_an_archive_shared_by_several_register_entries_is_mirrored_once():
    regions = {'australia-north': [129.0, -16.0, 137.0, -10.0], 'australia-nsw': [150.0, -38.0, 154.0, -28.0]}
    found = ausseabed.entries(
        [
            feature(),
            feature(NAME='Darwin Harbour Bathymetry 1m 2011 (copy)'),
            feature(bbox=(10.0, 50.0, 11.0, 51.0), DATA_URL='https://files.ausseabed.gov.au/survey/Elsewhere.zip'),
        ],
        regions,
    )
    assert [e['regions'] for e in found] == [['australia-north']]


def test_a_survey_changes_its_asset_only_when_its_archive_changes():
    [entry] = ausseabed.entries([feature()], {'australia-north': [129.0, -16.0, 137.0, -10.0]})
    survey = ausseabed.survey(entry, 780332079, '"2560e62e-149"')
    assert survey['name'] == 'GA-DarwinHarbour-Bathymetry-1m-2011'
    assert (survey['start'], survey['end'], survey['tecsou']) == ('20110502', '20110520', 'multi-beam')
    assert ausseabed.survey(entry, 780332079, '"2560e62e-149"')['md5'] == survey['md5']
    assert ausseabed.survey(entry, 780332079, '"ffff-1"')['md5'] != survey['md5']


@pytest.mark.parametrize(
    'names, members',
    [
        (
            ['x_EPSG4326_EGM2008_Depth_2m.tiff', 'x_Depth_2m_cog.tiff', 'x_Depth_2m_hs.tiff', 'metadata.txt'],
            ['x_Depth_2m_cog.tiff'],
        ),
        (['AHO_SI1030_30m_LAT_WGS84.tiff', 'AHO_SI1030_30m_MSL_WGS84.tiff'], ['AHO_SI1030_30m_MSL_WGS84.tiff']),
        (
            ['Shellharbour_Bathymetry_2m_MSL_cog.tif', 'Shellharbour_Backscatter_5m_Unknown_cog.tif', 'a.csar'],
            ['Shellharbour_Bathymetry_2m_MSL_cog.tif'],
        ),
        (['AHO_SI1046_30m_MSL_WGS84.tiff'], ['AHO_SI1046_30m_MSL_WGS84.tiff']),
    ],
)
def test_the_depth_grids_of_an_archive(names, members):
    assert ausseabed.depth_members(names) == members


class Flat:
    def __init__(self, metres):
        self.metres = metres

    def at(self, lon, lat):
        return np.full(len(lon), self.metres)


def utm_grid(path, heights, x0=700000.0, y0=8610000.0, res=1.0):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), heights.shape[1], heights.shape[0], 1, gdal.GDT_Float32)
    ds.SetGeoTransform((x0, res, 0, y0, 0, -res))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32752)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(3.4028234663852886e38)
    band.WriteArray(heights)
    ds = None
    return str(path)


def test_a_grid_is_reduced_to_its_shoalest_value_per_chart_cell_on_lat(tmp_path):
    heights = np.full((60, 60), -12.0, np.float32)
    heights[30, 30] = -4.5
    heights[:5, :5] = 3.4028234663852886e38
    path = utm_grid(tmp_path / 'g.tif', heights)
    reduced = ausseabed.reduce_grids([path], Flat(1.5))
    assert reduced['z'].min() == pytest.approx(3.0)
    assert np.all(reduced['z'] <= 10.5 + 1e-6)
    keys = reduced['iy'].astype(np.int64) * 10**8 + reduced['ix']
    assert len(np.unique(keys)) == len(keys)
    lon = reduced['lon'] / 1e7
    assert np.all(np.floor((lon + 180) * CELLS_PER_DEGREE) == reduced['ix'])
    assert float(reduced['step']) == GRID_STEP


def test_deep_water_is_left_out(tmp_path):
    heights = np.full((40, 40), -350.0, np.float32)
    heights[10:20, 10:20] = -8.0
    path = utm_grid(tmp_path / 'g.tif', heights)
    reduced = ausseabed.reduce_grids([path], Flat(1.5))
    assert set(np.round(reduced['z'], 3)) == {6.5}


def test_a_grid_coarser_than_the_chart_grid_is_left_out(tmp_path, monkeypatch):
    utm_grid(tmp_path / 'fine.tif', np.full((20, 20), -8.0, np.float32), res=16.2)
    utm_grid(tmp_path / 'coarse.tif', np.full((20, 20), -8.0, np.float32), res=30.0)
    local = gdal.Open
    monkeypatch.setattr(ausseabed.gdal, 'Open', lambda path: local(str(tmp_path / path.rsplit('/', 1)[1])))
    assert ausseabed.fine_enough('https://x/a.zip', 'fine.tif')
    assert not ausseabed.fine_enough('https://x/a.zip', 'coarse.tif')


def test_blocks_merged_on_the_way_keep_the_shoalest(tmp_path, monkeypatch):
    monkeypatch.setattr(ausseabed, 'MERGE_ROWS', 10)
    monkeypatch.setattr(ausseabed, 'BLOCK', 16)
    heights = np.full((60, 60), -12.0, np.float32)
    heights[45, 45] = -4.5
    reduced = ausseabed.reduce_grids([utm_grid(tmp_path / 'g.tif', heights)], Flat(1.5))
    keys = reduced['iy'].astype(np.int64) * 10**8 + reduced['ix']
    assert len(np.unique(keys)) == len(keys) and reduced['z'].min() == pytest.approx(3.0)


def test_ausseabed_surveys_chart_with_their_title_and_no_doi():
    [entry] = ausseabed.entries([feature()], {'australia-north': [129.0, -16.0, 137.0, -10.0]})
    survey = ausseabed.survey(entry, 1, '"e"')
    indexed = index_entry(survey, {'release': 'mirror-2', 'name': 'x.npz', 'size': 1, 'digest': 'sha256:00'})
    assert indexed['provider'] == 'ausseabed' and indexed['source']['url'] == survey['url']
    assert survey_inform(survey['name'], indexed) == (
        'Geoscience Australia survey Darwin Harbour Bathymetry 1m 2011, AusSeabed, CC BY 4.0'
    )
