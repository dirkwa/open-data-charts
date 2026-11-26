"""Download a region's chart inputs other than the surveys (see odc.mirror):

  wfs/<namespace>__<layer>.json   Shom WFS S-57 objects (Licence Ouverte 2.0)
  dem/*.asc                       Shom bathymetric DEMs of the region's coasts, if any
  dem/emodnet_*.tif               EMODnet Bathymetry DTM over the region (CC BY 4.0), if asked for
  dem/emodnethr_*.tif             EMODnet Bathymetry high-resolution DTMs the region lists, on top
                                  of every other model, the later in the list on top, if asked for
  dem/ddm_*.tif                   Denmark's Depth Model on LAT, EMODnet beyond it (odc.lat_model), if asked for
  dem/vaklodingen_*.tif           Rijkswaterstaat's Dutch coastal bathymetry on LAT, EMODnet beyond it
                                  (odc.lat_model), if asked for
  dem/bsh_*.tif                   BSH's German North Sea and Baltic seabed model on LAT, on top of
                                  what the region's earlier models wrote, EMODnet where there are
                                  none (odc.lat_model), if asked for
  dem/wsv_*.tif                   WSV's estuary models (DGM-W) on LAT, no data beyond them, over
                                  the region's earlier models in the mosaic (odc.lat_model), if asked for
  dem/kartverket_*.tif            Kartverket's Norwegian seabed model on sjøkartnull, EMODnet
                                  beyond it (odc.lat_model), if asked for
  dem/infomar_*.tif               INFOMAR's Irish bathymetry on LAT (CC BY 4.0), on the chart grid
                                  or a coarser one, if asked for
  dem/nonna_*.tif                 CHS's NONNA-100 and NONNA-10 models of Canadian waters on chart
                                  datum (CHS NONNA Licence), no data beyond them, if asked for
  dem/icgc_*.tif                  ICGC's Catalan coastal topobathymetric model on mean sea level,
                                  no data beyond it (odc.lat_model), if asked for
  dem/ausseabed_*.tif             Geoscience Australia's AusBathyTopo models and NSW's 5 m model
                                  on LAT (CC BY 4.0, CC BY 3.0 AU), no data beyond them nor in deep
                                  water (odc.lat_model), if asked for
  chart/<band>__<CLASS>__<layer>.json   LINZ's New Zealand chart objects (CC BY 4.0), one file
                                  per LDS layer, band 0 the smallest scale (odc.chart), and
                                  Traficom's Finnish depths, one file per layer and square, if asked for
  dataset-dates.json              when datasets whose attribution names that date were obtained
  osm.geojsonseq                  OSM features the chart uses, buildings only near the sea (ODbL)
  seamarks.geojsonseq             OSM seamark:* objects (OpenSeaMap tagging), if asked for (ODbL)
  water.geojsonseq                OSM water areas: harbours, canals, rivers, lakes (ODbL)
  land.gpkg                       OSM land polygons (osmdata.openstreetmap.de, ODbL)
  relief/*.tif                    Copernicus DEM GLO-30 land tiles
  etopo/*.tif                     NOAA NCEI ETOPO 2022 15 arc-second tiles, for deep water
  nc/reefs.geojson, nc/structures.geojson   Gouvernement de la Nouvelle-Calédonie (Licence Ouverte)
  aca/reefs_*.geojsonseq          Allen Coral Atlas reef geomorphology (CC BY 4.0), per 1-degree square
  aca-depth/depth_*.tif           Allen Coral Atlas satellite-derived bathymetry (CC BY 4.0), the
                                  1-degree squares the aca-depth release has (odc.aca_depth)

Usage: python3 -m odc.sources <region.json> <out dir> [source ...]
"""

import datetime
import glob
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import zipfile
import zlib

import numpy as np
from osgeo import gdal, ogr, osr

from odc import aca_depth, attribution, build_unit, chart, lat_model, osm, seamarks, shom
from odc.grid import GRID_STEP, units_in

gdal.UseExceptions()

WFS_NAMESPACES = (
    'BALISAGE_BDD_WFS',
    'EPAVES_BDD_WFS',
    'CABLES_BDD_WFS',
    'INFORMATIONS_PORTUAIRES_BDD_WFS',
    'REGLEMENTATION_NAVIGATION_BDD_WFS',
    'TOPONYMIE_BDD_WFS',
)
WFS_PAGE = 5000
# LINZ Data Service: the WFS takes the account's key as a header, which keeps
# it out of URLs and logs.
LINZ_WFS = 'https://data.linz.govt.nz/services/wfs'
LINZ_ATTEMPTS = 3
LINZ_RETRY_WAIT_S = 60
LINZ_REASON_BYTES = 2000
LINZ_KEY_ENV = 'LINZ_API_KEY'
# Traficom's chart depths of Finnish waters, sea and lakes, used with
# Traficom's permission (attribution in the region files).
CHART_SOURCES = {'linz-hydro', 'traficom-depths'}
TRAFICOM_WFS = 'https://julkinen.traficom.fi/inspirepalvelu/rajoitettu/wfs'
TRAFICOM_LAYERS = (
    ('DepthArea_A', 'DEPARE'),
    ('DepthContour_L', 'DEPCNT'),
    ('Sounding_P', 'SOUNDG'),
    ('UnsurveyedArea_A', 'UNSARE'),
)
# One scale for all of it, shown from the first zoom like LINZ's smallest bands.
TRAFICOM_BAND = 1
TRAFICOM_PAGE = 5000
# The attributes the charts use: the layers also carry update times that
# change with every republication and would rebuild every unit.
TRAFICOM_ATTRIBUTES = {'DRVAL1': float, 'DRVAL2': float, 'VALDCO': float, 'QUASOU': str, 'TECSOU': str, 'SORDAT': str}
# A Geofabrik extract with no other mirror fails a whole region's run on one
# dropped connection, so each alternative is tried a few times.
OSM_ATTEMPTS = 3
OSM_RETRY_WAIT_S = 60
LAND_POLYGONS = 'https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip'
LAND_POLYGONS_SHP = 'land-polygons-split-4326/land_polygons.shp'
COPERNICUS = 'https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif'
ETOPO = (
    'https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/15s_surface_elev_gtif/'
    'ETOPO_2022_v1_15s_{n}_surface.tif'
)
ETOPO_TILE_DEG = 15
# The builder charts buildings up to 12 grid steps (0.0015 deg) from the sea
# (build_unit.BUILDING_NEAR_WATER_CELLS); a coarse mask with a wide margin
# keeps a superset and drops the millions of inland ones a European extract
# carries.
NEAR_SEA_STEP = 0.001
NEAR_SEA_CELLS = 5
# Whole degrees fall on the DTM's 1/16 arc-minute grid, so chunks fit edge to edge.
EMODNET_WCS = (
    'https://ows.emodnet-bathymetry.eu/wcs?service=WCS&version=2.0.1&request=GetCoverage'
    '&coverageId=emodnet__mean&format=image/tiff&subset=Lat({s},{n})&subset=Long({w},{e})'
)
EMODNET_CHUNK_DEG = 2
# The DTM carries land elevations, where a Shom model has no data. Keep
# drying heights (a few metres above chart datum) and drop the rest, or a
# 115 m land cell spilling past the OSM coastline charts as a drying bank.
EMODNET_MAX_HEIGHT_M = 5.0
EMODNET_NODATA = -9999.0
# Where the tide ranges over a few tens of centimetres, a cell the DTM reads
# this shallow or higher is its coastline, not water: it covers ports and
# marinas at 0 to 2 m. Such a cell is charted as depth unknown
# (build_unit.MODEL_HOLE), neither a drying bank nor a source of fill.
EMODNET_MICROTIDAL_SHORE_M = -0.5
# A high-resolution DTM's EMO file, one line per grid cell centre:
# lon;lat;deepest;shoalest;mean;... in metres below its datum (LAT for the
# Instituto Hidrográfico's), negative above it.
EMODNET_HR = 'https://downloads.emodnet-bathymetry.eu/high_resolution/{id}.emo.zip'
# Denmark's Depth Model, 50 m, mean depth: the public share Dataforsyningen's
# dataset 4817 links to, one folder per release (the year).
DDM_SHARE = 'https://ftp.sdfe.dk/main.html?download&weblink=b4324b6389898704fd8ec7484882dbf3'
DDM_FILE = 'ddm_50m.dybde.tiff'
# Mean sea level and LAT above the ellipsoid, the Danish Geodata Agency (CC BY 4.0).
DKMSL = 'https://cdn.proj.org/dk_sdfi_dkmsl_2022.tif'
DKLAT = 'https://cdn.proj.org/dk_sdfi_dklat_2023.tif'
# 1/32 arc-minute, half the EMODnet DTM's step: the model is 50 m.
DDM_GRID = lat_model.Grid(1 / 1920, 2)
# Rijkswaterstaat's coastal and Wadden surveys (vaklodingen), the latest
# measured bed height up to the release year, 20 m, metres above NAP (CC0).
VAKLODINGEN = 'https://downloads.rijkswaterstaatdata.nl/bodemhoogte_20mtr/bodemhoogte_20mtr.tif'
# NAP and LAT above the ellipsoid, NSGI (CC BY 4.0).
NLGEO = 'https://cdn.proj.org/nl_nsgi_nlgeo2018.tif'
NLLAT = 'https://cdn.proj.org/nl_nsgi_nllat2018.tif'
# 1/64 arc-minute (about 29 m by 17 m at 53 degrees) keeps most of the 20 m
# grid; 1-degree chunks keep each in memory small.
VAKLODINGEN_GRID = lat_model.Grid(1 / 3840, 1)
# BSH's seabed model of the German North Sea and Baltic, 50 m, metres above
# NHN in ETRS89 UTM 32, one WCS coverage per sea, no data outside German
# waters (Datenlizenz Deutschland Namensnennung 2.0). The coverages' cell
# edges lie on multiples of 50 m.
BSH_WCS = (
    'https://www.geoseaportal.de/geoserver/ELC_INSPIRE/ows?service=WCS&version=2.0.1&request=GetCoverage'
    '&coverageId=ELC_INSPIRE__EL.GridCoverage_{sea}&format=image/tiff&subset=E({w},{e})&subset=N({s},{n})'
)
# Each coverage's extent: a request reaching past it is refused.
BSH_SEAS = {
    'northsea': (146900, 5901750, 521900, 6211450),
    'balticsea': (500000, 5950000, 900000, 6150000),
}
BSH_CELL_M = 50
BSH_MARGIN_M = 1000
BSH_NODATA = -99999.0
# BSH's server refuses connections for minutes at a time; a region asks it
# for dozens of chunks, so a refusal is waited out before the run gives up.
BSH_ATTEMPTS = 4
BSH_RETRY_WAIT_S = 120
# NHN (DHHN2016) above the ellipsoid, BKG (CC BY 4.0). No LAT surface covers
# German waters whole: DKLAT reaches them from 53.8 N, NLLAT the Ems. Both go
# onto DKLAT's 0.01-degree grid, extended south over NLLAT's extent, NLLAT on
# top, and the separation is filled between them and along the East Frisian
# coast from the nearest defined cells.
GCG2016 = 'https://cdn.proj.org/de_bkg_gcg2016.tif'
BSH_LAT_BOUNDS = (1.995, 49.995, 17.005, 59.505)
BSH_LAT_STEP = 0.01
BSH_LAT_NODATA = -32768.0
# As fine as the vaklodingen's grid, so a chunk replacing theirs loses none of it.
BSH_GRID = lat_model.Grid(1 / 3840, 1)
# WSV's models of the estuaries (DGM-W), 1 to 5 m, metres above NHN in
# ETRS89 UTM 32 (GeoNutzV), oldest first so the later lie on top. Up a tidal
# river chart datum is set in steps of its own rather than at LAT, which the
# LAT surfaces do not give: each model is kept (`keep`, UTM 32 metres) to the
# estuary seaward of Emden, Bremerhaven and Cuxhaven, where they do.
WSV_ARCHIVE = 'https://www.kuestendaten.de/media/zdm/portaltide{river}/Allgemeine_Informationen/Archiv/GIS/'
WSV_MODELS = (
    {
        'url': WSV_ARCHIVE.format(river='ems') + 'DGM-W_Aussen-und_Unterems_2015_5x5m_Geotiff_ETRS89.zip',
        'member': 'DGM-W_2015_Ems_5x5m.tif',
        'keep': (326000, 5909000, 420000, 5965000),
    },
    {
        'url': WSV_ARCHIVE.format(river='weser') + 'DGM-W_Jade_2018/DGM-W_2018_Jade_5x5m.zip',
        'member': 'DGM-W_2018_Jade_5x5m.tif',
    },
    {
        'url': WSV_ARCHIVE.format(river='weser') + 'DGM-W_2021/DGM-W_2021_Weser_Nordsee_2x2m_GeoTiff.zip',
        'member': 'DGM-W_Weser_2021_2x2m.tif',
        'keep': (440000, 5930000, 510000, 5982000),
    },
    *(
        {
            'url': WSV_ARCHIVE.format(river='elbe')
            + f'DGM-W_Aussenelbe_2024_1x1m/Geotiff/DGM-W_2024_Aussenelbe_1x1m_Teil{part}.tif',
            'keep': (440000, 5940000, 484000, 6000000),
        }
        for part in (1, 2, 3)
    ),
)
WSV_SRS = 'EPSG:25832'
WSV_GRID = lat_model.Grid(GRID_STEP, 1)
# Kartverket's seabed terrain model, 50 m, metres above sjøkartnull in UTM 33,
# 0 where it has no data (CC BY 4.0). Sjøkartnull, the datum of Norway's
# charts and tide tables, is LAT except from the Swedish border to Utsira
# (0.2 m below LAT) and in the Oslofjord north of Drøbaksundet (0.3 m below);
# the depths stay on it, so they read as Norway's own charts do and never
# deeper than on LAT.
KARTVERKET_WCS = (
    'https://wms.geonorge.no/skwms1/wms.dtm2?service=WCS&version=2.0.1&request=GetCoverage'
    '&coverageId=bathymetry50m&format=image/tiff&subset=x({w},{e})&subset=y({s},{n})'
)
KARTVERKET_CELL_M = 50
# The coverage's cell edges, so a request returns its own cells unresampled.
KARTVERKET_CELL_EDGE_M = 25
# Bilinear warping onto the chunk reads a cell or two past its edge.
KARTVERKET_MARGIN_M = 1000
KARTVERKET_GRID = lat_model.Grid(1 / 1920, 2)
# ICGC's topobathymetric model of the Catalan coast, 1 m, one Cloud Optimized
# GeoTIFF in ETRS89 UTM 31N, heights above mean sea level at Alicante, the
# datum of Spain's levelling, negative under water (CC BY 4.0). Mean sea level
# lies a decimetre or two above the chart datum of this microtidal coast, the
# datum EMODnet's Mediterranean depths are on too. ICGC's server refuses
# GitHub's runners, so the file is mirrored, cut into parts under GitHub's
# 2 GiB asset limit, in this repository's release ICGC_RELEASE.
ICGC_FILE = 'batimetria-v2r1-elevacions-2021-2025.tif'
ICGC_RELEASE = 'icgc'
ICGC_BOUNDS = (0.513995, 40.406269, 3.335166, 42.440739)
# The chart grid, so a channel a few cells wide survives.
ICGC_GRID = lat_model.Grid(GRID_STEP, 1)
# Depth models of Australian waters, heights above approximate mean sea
# level (AHD for New South Wales), negative under water. Each is an archive,
# the members its dataset needs, the first of them opened, and the extent it
# reaches, so a region fetches only the members of the models it needs.
# Later models lie on top where they overlap.
# Geoscience Australia's AusBathyTopo models: 30 m, one Cloud Optimized
# GeoTIFF per tile in WGS84 (CC BY 4.0, with the Australian Hydrographic
# Office's surveys in them under the same licence).
AUSSEABED = 'https://files.ausseabed.gov.au/survey/'
# New South Wales' bathymetry from its 2018 marine lidar and multibeam
# surveys: 5 m, an ArcInfo grid in a transverse Mercator projection (CC BY
# 3.0 AU). The State's portal turns scripts away, so this repository's
# release nsw-bathymetry holds an unchanged copy of the zip.
NSW_ARCHIVE = (
    'https://github.com/dirkwa/open-data-charts/releases/download/nsw-bathymetry/'
    'bathymetrymosaic_marinelidar_mbes.zip.part-00'
)
NSW_MEMBERS = tuple(
    f'BathymetryMosaic_MarineLidar_MBES/ml_mb_dem0/{name}.adf'
    for name in ('hdr', 'w001001', 'w001001x', 'dblbnd', 'sta', 'prj')
)
# The newer Torres Strait model over the Great Barrier Reef one, and New
# South Wales' 5 m over the Great Barrier Reef's southern tile.
AUSSEABED_MODELS = (
    (
        AUSSEABED + 'Great%20Barrier%20Reef%20Bathymetry%202020%2030m.zip',
        ('Great_Barrier_Reef_A_2020_30m_MSL_cog.tif',),
        (142.0, -17.0, 147.0, -10.0),
    ),
    (
        AUSSEABED + 'Great%20Barrier%20Reef%20Bathymetry%202020%2030m.zip',
        ('Great_Barrier_Reef_B_2020_30m_MSL_cog.tif',),
        (144.0, -23.0, 149.0, -16.0),
    ),
    (
        AUSSEABED + 'Great%20Barrier%20Reef%20Bathymetry%202020%2030m.zip',
        ('Great_Barrier_Reef_C_2020_30m_MSL_cog.tif',),
        (148.0, -24.0, 154.0, -18.0),
    ),
    (
        AUSSEABED + 'Great%20Barrier%20Reef%20Bathymetry%202020%2030m.zip',
        ('Great_Barrier_Reef_D_2020_30m_MSL_cog.tif',),
        (150.0, -29.0, 156.0, -23.0),
    ),
    (
        AUSSEABED + 'North%20Australia%20Bathymetry%202018%2030m.zip',
        ('North_Australia_Bathymetry_A_2018_30m_cog.tif',),
        (121.0, -18.0, 124.0, -8.0),
    ),
    (
        AUSSEABED + 'North%20Australia%20Bathymetry%202018%2030m.zip',
        ('North_Australia_Bathymetry_B_2018_30m_cog.tif',),
        (123.0, -17.0, 127.0, -8.0),
    ),
    (
        AUSSEABED + 'North%20Australia%20Bathymetry%202018%2030m.zip',
        ('North_Australia_Bathymetry_C_2018_30m_cog.tif',),
        (126.0, -16.0, 130.0, -8.0),
    ),
    (
        AUSSEABED + 'North%20Australia%20Bathymetry%202018%2030m.zip',
        ('North_Australia_Bathymetry_D_2018_30m_cog.tif',),
        (129.0, -16.0, 133.0, -8.0),
    ),
    (
        AUSSEABED + 'Bass%20Strait%20Bathymetry%202022%2030m.zip',
        ('Bass_Strait_Bathymetry_2022_30m_Unknown_cog.tif',),
        (143.0, -42.0, 150.0, -37.0),
    ),
    (
        AUSSEABED + 'Australian%20Bathymetry%20Topography%20(Torres%20Strait)%202023%2030m.zip',
        ('Australian_Bathymetry_Topography__Torres_Strait__2023_30m_MSL_cog.tif',),
        (139.0, -13.0, 146.0, -8.0),
    ),
    (NSW_ARCHIVE, NSW_MEMBERS, (150.1, -37.6, 153.8, -28.1)),
)
# AusBathyTopo (Australia) 250m 2026, under the others for regions that ask
# for it: where no finer model reaches, between the coast and deep water.
AUSSEABED_NATIONAL = (
    AUSSEABED + 'AusBathyTopo_Australia_2026_250m.zip',
    ('AusBathyTopo_Australia_2026_250m/AusBathyTopo_Australia_2026_250m_MSL_cog.tif',),
    (92.0, -60.0, 172.0, -8.0),
)
# On the chart grid, so the builder takes the chunks cell for cell and the
# max resampling's one-cell shoals keep their depth.
AUSSEABED_GRID = lat_model.Grid(GRID_STEP, 1)
# The models reach far into the Coral Sea; a chunk with nothing
# shallower is left to ETOPO, so the region's units stay near the coast.
AUSSEABED_DEEP_M = 300.0
# AIMS' Lowest Predicted Tide over 2021 to 2025 from the EOT20 tide model,
# metres below mean sea level on a 1/32-degree grid, NaN over land (CC BY
# 4.0): LAT for Australian waters. Cells along the coast are filled from the
# nearest defined ones within AUSSEABED_SEPARATION_FILL_CELLS.
AUSSEABED_LPT = (
    'https://nextcloud.eatlas.org.au/public.php/dav/files/8CTMtLc6QFaYmqb/out/'
    'AU_AIMS_EOT20-tide-stats_LPT_2021-01-2025-12.tif'
)
AUSSEABED_SEPARATION_FILL_CELLS = 16
ARCGIS_PAGE = 1000
ACA_WFS = (
    'https://allencoralatlas.org/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature'
    '&typeNames=coral-atlas:geomorphic_data_verbose&outputFormat=application/json'
)
ACA_PAGE = 5000
CHUNK = 1 << 20
SHORT_READ_WAIT_S = 10


def save_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.part', 'w', encoding='utf-8') as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True)
    os.replace(path + '.part', path)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save_url(url, path, timeout=600, tries=3):
    """A connection the server drops mid-body ends the read without an
    error, so the length it announced is checked."""
    for attempt in range(tries):
        with shom.get(url, timeout=timeout) as response, open(path + '.part', 'wb') as fh:
            shutil.copyfileobj(response, fh, CHUNK)
            expected, received = response.headers.get('Content-Length'), fh.tell()
        if expected is None or received == int(expected):
            os.replace(path + '.part', path)
            return
        if attempt == tries - 1:
            raise OSError(f'{url}: {received} of {expected} bytes after {tries} tries')
        print(f'{url}: {received} of {expected} bytes, trying again', flush=True)
        time.sleep(SHORT_READ_WAIT_S * (attempt + 1))


def remote_size(url):
    with shom.get(url, headers={'Range': 'bytes=0-0'}) as response:
        return int(response.headers['Content-Range'].rsplit('/', 1)[1])


class RemoteFile(io.RawIOBase):
    """A file over HTTP, each read one range request: enough for zipfile to read a large archive's directory."""

    def __init__(self, url):
        self.url = url
        self.size = remote_size(url)
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = base + offset
        return self.position

    def readinto(self, buffer):
        end = min(self.position + len(buffer), self.size)
        if end <= self.position:
            return 0
        with shom.get(self.url, headers={'Range': f'bytes={self.position}-{end - 1}'}) as response:
            data = response.read()
        buffer[: len(data)] = data
        self.position += len(data)
        return len(data)


ZIP_LOCAL_HEADER = struct.Struct('<26xHH')


def save_zip_members(url, members, folder):
    """Members of a remote zip under `folder`, each fetched by its own byte
    range in pieces retried on their own, so neither the rest of the archive
    nor a dropped connection costs a download of gigabytes."""
    remote = RemoteFile(url)
    with zipfile.ZipFile(io.BufferedReader(remote, 1 << 16)) as archive:
        infos = [archive.getinfo(member) for member in members]
    root = os.path.realpath(folder)
    for info in infos:
        path = os.path.realpath(os.path.join(root, info.filename))
        if os.path.commonpath([root, path]) != root:
            raise RuntimeError(f'{info.filename} in {url} lies outside the archive')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        remote.seek(info.header_offset)
        name_length, extra_length = ZIP_LOCAL_HEADER.unpack(remote.read(ZIP_LOCAL_HEADER.size))
        packed = path + '.packed'
        start = info.header_offset + ZIP_LOCAL_HEADER.size + name_length + extra_length
        shom.download(url, packed, info.compress_size, offset=start)
        if info.compress_type == zipfile.ZIP_STORED:
            os.replace(packed, path)
            continue
        if info.compress_type != zipfile.ZIP_DEFLATED:
            raise RuntimeError(f'{info.filename} in {url} is compressed with method {info.compress_type}')
        inflate = zlib.decompressobj(-zlib.MAX_WBITS)
        crc = 0
        with open(packed, 'rb') as source, open(path, 'wb') as out:
            for block in iter(lambda: source.read(CHUNK), b''):
                data = inflate.decompress(block)
                crc = zlib.crc32(data, crc)
                out.write(data)
            data = inflate.flush()
            crc = zlib.crc32(data, crc)
            out.write(data)
        os.remove(packed)
        if crc != info.CRC:
            raise OSError(f'{info.filename} in {url} fails its CRC')


def wfs_layers():
    caps = shom.get(f'{shom.WFS}?service=WFS&version=2.0.0&request=GetCapabilities').read().decode('utf-8', 'replace')
    names = re.findall(r'<(?:wfs:)?Name>([^<]+)</(?:wfs:)?Name>', caps)
    return sorted({n for n in names if n.split(':')[0] in WFS_NAMESPACES})


# Shom's WFS pages a layer only in a stated order: its numeric key, named
# pgid in most layers and gid in some.
WFS_SORT_KEYS = ('pgid', 'gid')


def wfs_sort_key(type_name):
    query = urllib.parse.urlencode(
        {'service': 'WFS', 'version': '2.0.0', 'request': 'DescribeFeatureType', 'typeNames': type_name}
    )
    schema = shom.get(f'{shom.WFS}?{query}').read().decode('utf-8', 'replace')
    names = set(re.findall(r'name="([^"]+)"', schema))
    for key in WFS_SORT_KEYS:
        if key in names:
            return key
    raise RuntimeError(f'{type_name} has none of {WFS_SORT_KEYS} to page by')


def wfs_features(type_name, bbox):
    minlon, minlat, maxlon, maxlat = bbox
    features = []
    sort_key = wfs_sort_key(type_name)
    while True:
        params = {
            'service': 'WFS',
            'version': '2.0.0',
            'request': 'GetFeature',
            'typeNames': type_name,
            'outputFormat': 'application/json',
            'srsName': 'urn:ogc:def:crs:EPSG::4326',
            # WFS 2.0 with an EPSG URN: axis order lat,lon.
            'bbox': f'{minlat},{minlon},{maxlat},{maxlon},urn:ogc:def:crs:EPSG::4326',
            'count': str(WFS_PAGE),
            'startIndex': str(len(features)),
            'sortBy': sort_key,
        }
        page = shom.get_json(f'{shom.WFS}?{urllib.parse.urlencode(params)}')
        batch = page.get('features') or []
        for feature in batch:
            # The service makes up a new feature id on every request; the key
            # keeps the saved layer, and so the unit versions, unchanged.
            feature['id'] = f'{type_name.split(":")[-1]}.{feature["properties"][sort_key]}'
        features.extend(batch)
        matched = page.get('numberMatched')
        if len(batch) < WFS_PAGE or (isinstance(matched, int) and len(features) >= matched):
            return features


def fetch_bounds(region):
    """The region's units with their build margins: discovery takes whole
    units, which reach past the region's bbox."""
    boxes = [unit.build_box.bbox for unit in units_in(*region['bbox'])]
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def wfs_bounds(region):
    """With OSM seamarks, a mile wider: an OSM aid gives way to a Shom aid
    up to a mile beyond the region's edge as well."""
    bounds = fetch_bounds(region)
    return seamarks.aid_window(bounds) if 'openseamap' in region.get('sources', []) else bounds


def fetch_wfs(region, out):
    if 'shom-wfs' not in region.get('sources', []):
        for stale in glob.glob(os.path.join(out, 'wfs', '*.json')):
            os.remove(stale)
        return
    for name in wfs_layers():
        features = sorted(wfs_features(name, wfs_bounds(region)), key=lambda f: str(f.get('id')))
        save_json(
            os.path.join(out, 'wfs', name.replace(':', '__') + '.json'),
            {'type': 'FeatureCollection', 'features': features},
        )
        print(f'{len(features):6d}  {name}', flush=True)


def east_of_greenwich(coords):
    """New Zealand's charts reach past the antimeridian, and the WFS wraps
    their longitudes into [-180, 180]: a depth area across it would then span
    the globe and cover the land. All of LINZ's chart data lies east of
    Greenwich, so a negative longitude moves 360 degrees east."""
    if isinstance(coords[0], (int, float)):
        if coords[0] < 0:
            coords[0] += 360
        return
    for part in coords:
        east_of_greenwich(part)


def linz_get(url, key, wait=LINZ_RETRY_WAIT_S):
    """LINZ's WFS answers a passing failure behind it with 400 Bad Request as
    well, so a 400 is asked again before the run gives up, with the
    service's own explanation in the error."""
    for attempt in range(1, LINZ_ATTEMPTS + 1):
        try:
            with shom.get(url, timeout=600, headers={'Authorization': f'key {key}'}) as r:
                return json.load(r)
        except urllib.error.HTTPError as error:
            reason = error.read(LINZ_REASON_BYTES).decode('utf-8', 'replace').strip()
            if error.code != 400 or attempt == LINZ_ATTEMPTS:
                raise RuntimeError(f'LINZ answered {error.code} {error.reason}: {reason}') from error
            print(f'LINZ answered 400 ({reason[:200]}), asking again', flush=True)
            time.sleep(wait * attempt)


def linz_layer(layer_id, bbox, key):
    minlon, minlat, maxlon, maxlat = bbox
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': f'layer-{layer_id}',
        'outputFormat': 'application/json',
        'srsName': 'urn:ogc:def:crs:EPSG::4326',
        # WFS 2.0 with an EPSG URN: axis order lat,lon.
        'bbox': f'{minlat},{minlon},{maxlat},{maxlon},urn:ogc:def:crs:EPSG::4326',
    }
    page = linz_get(f'{LINZ_WFS}?{urllib.parse.urlencode(params)}', key)
    features = page.get('features') or []
    matched = page.get('numberMatched')
    if isinstance(matched, int) and matched > len(features):
        raise RuntimeError(f'LINZ layer {layer_id}: {len(features)} of {matched} features returned')
    for feature in features:
        if feature.get('geometry'):
            east_of_greenwich(feature['geometry']['coordinates'])
        # fidn, LINZ's feature id, keeps the saved layer and so the unit versions stable.
        feature['id'] = f'linz-{layer_id}.{(feature.get("properties") or {}).get("fidn", feature.get("id"))}'
    return sorted(features, key=lambda f: str(f['id']))


MULTI = {0: ogr.wkbMultiPoint, 1: ogr.wkbMultiLineString, 2: ogr.wkbMultiPolygon}


def singles(geom):
    if geom.GetGeometryType() in (ogr.wkbGeometryCollection, *MULTI.values()):
        for i in range(geom.GetGeometryCount()):
            yield from singles(geom.GetGeometryRef(i))
    elif not geom.IsEmpty():
        yield geom


def same_dimension(geom, dimension):
    """The parts of a cut of the given dimension: a cut along an edge also
    leaves points and lines."""
    if geom is None:
        return None
    kept = [part for part in singles(geom) if part.GetDimension() == dimension]
    if not kept:
        return None
    multi = ogr.Geometry(MULTI[dimension])
    for part in kept:
        multi.AddGeometry(part)
    return multi


def traficom_square(layer, west, south):
    """A layer's objects in one square, cut to it, in WGS84."""
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': f'rajoitettu:{layer}',
        'outputFormat': 'application/json',
        'srsName': 'urn:ogc:def:crs:EPSG::4326',
        'bbox': f'{south},{west},{south + chart.SQUARE_DEG},{west + chart.SQUARE_DEG},urn:ogc:def:crs:EPSG::4326',
        'sortBy': 'IDENTIFIER',
        'count': TRAFICOM_PAGE,
    }
    square = ogr.CreateGeometryFromWkt(
        f'POLYGON(({west} {south},{west + chart.SQUARE_DEG} {south},{west + chart.SQUARE_DEG} '
        f'{south + chart.SQUARE_DEG},{west} {south + chart.SQUARE_DEG},{west} {south}))'
    )
    features, start = [], 0
    while True:
        page = shom.get_json(f'{TRAFICOM_WFS}?{urllib.parse.urlencode({**params, "startIndex": start})}', timeout=600)
        for feature in page.get('features') or []:
            if not feature.get('geometry'):
                continue
            geom = ogr.CreateGeometryFromJson(json.dumps(feature['geometry']))
            geom.FlattenTo2D()
            if geom.GetGeometryType() == ogr.wkbPoint:
                # A sounding on a square's edge belongs to the square east or north of it only.
                x, y = geom.GetX(), geom.GetY()
                if not (west <= x < west + chart.SQUARE_DEG and south <= y < south + chart.SQUARE_DEG):
                    continue
            elif not square.Contains(geom):
                geom = same_dimension(geom.MakeValid().Intersection(square), geom.GetDimension())
                if geom is None:
                    continue
            props = feature.get('properties') or {}
            kept = {
                k.lower(): cast(props[k]) for k, cast in TRAFICOM_ATTRIBUTES.items() if props.get(k) not in (None, '')
            }
            if props.get('DEPTH') is not None:
                kept['depth'] = float(props['DEPTH'])
            identifier = props.get('IDENTIFIER', feature.get('id'))
            features.append(
                {
                    'type': 'Feature',
                    'id': f'traficom-{layer}.{identifier}{chart.square_name(west, south)}',
                    'geometry': json.loads(geom.ExportToJson()),
                    'properties': kept,
                }
            )
        returned = len(page.get('features') or [])
        start += returned
        if returned < TRAFICOM_PAGE:
            return features


def fetch_traficom(region, target):
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    squares = [
        (west, south)
        for south in range(math.floor(minlat), math.ceil(maxlat), chart.SQUARE_DEG)
        for west in range(math.floor(minlon), math.ceil(maxlon), chart.SQUARE_DEG)
    ]
    for layer, objl in TRAFICOM_LAYERS:
        total = 0
        for west, south in squares:
            features = traficom_square(layer, west, south)
            if not features:
                continue
            layer_id = f'traficom-{layer}{chart.square_name(west, south)}'
            save_json(
                os.path.join(target, chart.file_name(TRAFICOM_BAND, objl, layer_id)),
                {'type': 'FeatureCollection', 'features': features},
            )
            total += len(features)
        print(f'{total:7d}  {objl} (Traficom {layer})', flush=True)


def fetch_chart(region, out):
    target = os.path.join(out, 'chart')
    for stale in glob.glob(os.path.join(target, '*.json')):
        os.remove(stale)
    if 'traficom-depths' in region.get('sources', []):
        fetch_traficom(region, target)
    if 'linz-hydro' not in region.get('sources', []):
        return
    key = os.environ.get(LINZ_KEY_ENV)
    if not key:
        raise RuntimeError(f'{LINZ_KEY_ENV} is not set: LINZ chart data needs the account key')
    bounds = wfs_bounds(region)
    for layer_id, objl, band in chart.linz_layers():
        features = linz_layer(layer_id, bounds, key)
        save_json(
            os.path.join(target, chart.file_name(band, objl, layer_id)),
            {'type': 'FeatureCollection', 'features': features},
        )
        print(f'{len(features):6d}  {objl} band {band} (layer {layer_id})', flush=True)


# A fine model on top of the region's EMODnet keeps its own cells, and the
# coarser grids are smoothed onto them as the builder smooths a DEM onto the
# chart grid, not blown up into blocks. odc.units hashes the mosaic built
# with the same options.
DEM_MOSAIC_OPTIONS = {'resolution': 'highest', 'resampleAlg': 'cubicspline'}


def dem_grids(inputs):
    """The region's bathymetric model grids, in the order the mosaic
    stacks them: a later grid wins where they overlap."""
    dem = os.path.join(inputs, 'dem')
    return (
        sorted(glob.glob(os.path.join(dem, 'emodnet_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'icgc_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'ausseabed_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'ddm_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'vaklodingen_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'bsh_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'wsv_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'kartverket_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'infomar_25m_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'infomar_10m_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'nonna_100m_*.tif')))
        + sorted(glob.glob(os.path.join(dem, 'nonna_10m_*.tif')))
        + sorted(glob.glob(os.path.join(dem, '*.asc')))
        # A survey's own DTM is finer than any model it lies in.
        + sorted(glob.glob(os.path.join(dem, 'emodnethr_*.tif')))
    )


def fetch_emodnet(bounds, target, microtidal=False):
    minlon, minlat, maxlon, maxlat = bounds
    step = EMODNET_CHUNK_DEG
    for s in range(math.floor(minlat), math.ceil(maxlat), step):
        for w in range(math.floor(minlon), math.ceil(maxlon), step):
            url = EMODNET_WCS.format(s=s, n=s + step, w=w, e=w + step)
            path = os.path.join(target, f'emodnet_{s:+03d}_{w:+04d}.tif')
            raw = path + '.wcs'  # outside dem_grids' pattern, so a broken run never feeds it to the mosaic
            save_url(url, raw, timeout=1800)
            source = gdal.Open(raw)
            heights = source.GetRasterBand(1).ReadAsArray()
            heights[heights > EMODNET_MAX_HEIGHT_M] = EMODNET_NODATA
            if microtidal:
                heights[(heights != EMODNET_NODATA) & (heights >= EMODNET_MICROTIDAL_SHORE_M)] = build_unit.MODEL_HOLE
            part = path + '.part'
            grid = gdal.GetDriverByName('GTiff').CreateCopy(part, source, options=['COMPRESS=DEFLATE', 'PREDICTOR=3'])
            band = grid.GetRasterBand(1)
            band.SetNoDataValue(EMODNET_NODATA)
            band.WriteArray(heights)
            grid = source = None
            os.replace(part, path)
            os.remove(raw)


def emodnet_hr_grid(lines, cells_per_degree):
    """Heights on the DTM's own grid, the shoalest where cells repeat, and
    the grid's west and north edges."""
    cols, rows, heights = [], [], []
    for line in lines:
        fields = line.split(';')
        depth = fields[3] or fields[4]
        if not depth:
            continue
        height = -float(depth)
        if height > EMODNET_MAX_HEIGHT_M:
            continue
        cols.append(math.floor(float(fields[0]) * cells_per_degree))
        rows.append(math.floor(float(fields[1]) * cells_per_degree))
        heights.append(height)
    if not heights:
        return None
    cols, rows, heights = np.array(cols), np.array(rows), np.array(heights, dtype='float32')
    west, north = cols.min(), rows.max()
    grid = np.full((north - rows.min() + 1, cols.max() - west + 1), EMODNET_NODATA, dtype='float32')
    np.maximum.at(grid, (north - rows, cols - west), heights)
    return grid, west / cells_per_degree, (north + 1) / cells_per_degree


def smooth_on_chart_grid(heights, west, north, cells_per_degree):
    """The DTM bilinear on the chart grid, through its cell corners, each the
    shoalest of the cells it touches: smooth contours, and every point of a
    cell at least as shoal as the cell."""
    rows, cols = heights.shape
    padded = np.full((rows + 2, cols + 2), -np.inf, dtype='float32')
    padded[1:-1, 1:-1] = np.where(heights == EMODNET_NODATA, -np.inf, heights)
    corners = np.maximum.reduce([padded[:-1, :-1], padded[:-1, 1:], padded[1:, :-1], padded[1:, 1:]])
    corners[np.isneginf(corners)] = EMODNET_NODATA
    step = 1 / cells_per_degree
    # Pixels are areas to GDAL: a corner is the centre of a pixel half a cell out.
    src = gdal.GetDriverByName('MEM').Create('', cols + 1, rows + 1, 1, gdal.GDT_Float32)
    src.SetGeoTransform((west - step / 2, step, 0.0, north + step / 2, 0.0, -step))
    src.SetProjection(lat_model.wgs84())
    src.GetRasterBand(1).SetNoDataValue(EMODNET_NODATA)
    src.GetRasterBand(1).WriteArray(corners)
    east, south = west + cols * step, north - rows * step
    bounds = (
        math.floor(west / GRID_STEP) * GRID_STEP,
        math.floor(south / GRID_STEP) * GRID_STEP,
        math.ceil(east / GRID_STEP) * GRID_STEP,
        math.ceil(north / GRID_STEP) * GRID_STEP,
    )
    out = gdal.Warp(
        '',
        src,
        format='MEM',
        outputBounds=bounds,
        xRes=GRID_STEP,
        yRes=GRID_STEP,
        resampleAlg='bilinear',
        srcNodata=EMODNET_NODATA,
        dstNodata=EMODNET_NODATA,
    )
    return out.GetRasterBand(1).ReadAsArray(), bounds[0], bounds[3]


def fetch_emodnet_hr(dtms, target, tmp):
    for i, dtm in enumerate(dtms):
        archive = os.path.join(tmp, f'{dtm["id"]}.zip')
        save_url(EMODNET_HR.format(id=dtm['id']), archive, timeout=1800)
        # A DTM finer than the chart grid is first taken to the shoalest
        # depth in each chart cell, so no shoal between two chart cells is lost.
        cells_per_degree = min(dtm['cells_per_degree'], round(1 / GRID_STEP))
        try:
            with zipfile.ZipFile(archive) as zf, zf.open(f'{dtm["id"]}.emo') as fh:
                result = emodnet_hr_grid(io.TextIOWrapper(fh, encoding='utf-8'), cells_per_degree)
        except (OSError, zipfile.BadZipFile, zlib.error, EOFError, ValueError) as e:
            raise RuntimeError(f'{dtm["id"]}: broken archive ({e})') from e
        os.remove(archive)
        if result is None:
            raise RuntimeError(f'{dtm["id"]}: no depths in the EMO file')
        heights, west, north = smooth_on_chart_grid(*result, cells_per_degree)
        path = os.path.join(target, f'emodnethr_{i:02d}_{dtm["id"]}.tif')
        out = gdal.GetDriverByName('GTiff').Create(
            path + '.part', heights.shape[1], heights.shape[0], 1, gdal.GDT_Float32, ['COMPRESS=DEFLATE', 'PREDICTOR=3']
        )
        out.SetGeoTransform((west, GRID_STEP, 0.0, north, 0.0, -GRID_STEP))
        out.SetProjection(lat_model.wgs84())
        band = out.GetRasterBand(1)
        band.SetNoDataValue(EMODNET_NODATA)
        band.WriteArray(heights)
        out = None
        os.replace(path + '.part', path)
        print(f'emodnet-hr: {dtm["id"]} {heights.shape[1]}x{heights.shape[0]}', flush=True)


def ddm_release():
    listing = shom.get(DDM_SHARE).read().decode('utf-8', 'replace')
    releases = re.findall(r"&subfolder=(\d{4})'", listing)
    if not releases:
        raise RuntimeError(f'no release folder in {DDM_SHARE}')
    return max(releases)


def emodnet_fill(name, bounds, tmp):
    emodnet = os.path.join(tmp, f'{name}-emodnet')
    os.makedirs(emodnet)
    fetch_emodnet(bounds, emodnet)
    fill = os.path.join(tmp, f'{name}-emodnet.vrt')
    gdal.BuildVRT(fill, sorted(glob.glob(os.path.join(emodnet, 'emodnet_*.tif'))))
    return fill


def on_lat(model, datum_url, lat_url, grid, name, bounds, target, tmp, depths):
    """Writes the model on LAT in chunks `<name>_*.tif`, EMODnet beyond it."""
    surfaces = []
    for url in (datum_url, lat_url):
        surfaces.append(os.path.join(tmp, os.path.basename(url)))
        save_url(url, surfaces[-1])
    separation = os.path.join(tmp, f'{name}-separation.tif')
    lat_model.separation(*surfaces, separation)
    fill = emodnet_fill(name, bounds, tmp)
    for s, w in grid.chunks(bounds):
        path = os.path.join(target, f'{name}_{s:+03d}_{w:+04d}.tif')
        lat_model.write_chunk(model, separation, fill, grid, s, w, path, depths)


def fetch_ddm(bounds, target, tmp):
    """Writes the chunks and returns the date the model was obtained."""
    release = ddm_release()
    print(f'ddm: release {release}', flush=True)
    model = os.path.join(tmp, DDM_FILE)
    query = urllib.parse.urlencode({'subfolder': release, 'realfilename': DDM_FILE})
    save_url(f'{DDM_SHARE}&{query}', model, timeout=1800)
    obtained = datetime.date.today().isoformat()
    on_lat(model, DKMSL, DKLAT, DDM_GRID, 'ddm', bounds, target, tmp, depths=True)
    return obtained


def fetch_vaklodingen(bounds, target, tmp):
    model = os.path.join(tmp, os.path.basename(VAKLODINGEN))
    save_url(VAKLODINGEN, model, timeout=1800)
    on_lat(model, NLGEO, NLLAT, VAKLODINGEN_GRID, 'vaklodingen', bounds, target, tmp, depths=False)


def kartverket_request(grid, s, w):
    """The WCS request for a chunk: its UTM 33 bounds on the coverage's cell
    edges, with a margin."""
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm = osr.SpatialReference()
    utm.ImportFromEPSG(25833)
    utm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    west, south, east, north = osr.CoordinateTransformation(wgs84, utm).TransformBounds(
        w, s, w + grid.chunk_deg, s + grid.chunk_deg, 21
    )
    cell, edge, margin = KARTVERKET_CELL_M, KARTVERKET_CELL_EDGE_M, KARTVERKET_MARGIN_M

    def down(x):
        return math.floor((x - margin - edge) / cell) * cell + edge

    def up(x):
        return math.ceil((x + margin - edge) / cell) * cell + edge

    return KARTVERKET_WCS.format(w=down(west), e=up(east), s=down(south), n=up(north))


def fetch_kartverket(bounds, target, tmp):
    fill = emodnet_fill('kartverket', bounds, tmp)
    grid = KARTVERKET_GRID
    for s, w in grid.chunks(bounds):
        raw = os.path.join(tmp, 'kartverket.wcs')
        save_url(kartverket_request(grid, s, w), raw, timeout=1800)
        model = os.path.join(tmp, 'kartverket.tif')
        gdal.Translate(model, raw, noData=0)
        path = os.path.join(target, f'kartverket_{s:+03d}_{w:+04d}.tif')
        lat_model.write_chunk(model, None, fill, grid, s, w, path, depths=False)


def bsh_request(sea, grid, s, w):
    """The WCS request for a chunk on one sea's coverage: its UTM 32 bounds
    on the coverage's cell edges, with a margin, cut to the coverage; None
    if the chunk lies outside it."""
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm = osr.SpatialReference()
    utm.ImportFromEPSG(25832)
    utm.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    west, south, east, north = osr.CoordinateTransformation(wgs84, utm).TransformBounds(
        w, s, w + grid.chunk_deg, s + grid.chunk_deg, 21
    )
    cell, margin = BSH_CELL_M, BSH_MARGIN_M
    cw, cs, ce, cn = BSH_SEAS[sea]
    w_ = max(cw, math.floor((west - margin) / cell) * cell)
    s_ = max(cs, math.floor((south - margin) / cell) * cell)
    e_ = min(ce, math.ceil((east + margin) / cell) * cell)
    n_ = min(cn, math.ceil((north + margin) / cell) * cell)
    if w_ >= e_ or s_ >= n_:
        return None
    return BSH_WCS.format(sea=sea, w=w_, e=e_, s=s_, n=n_)


def bsh_save(url, path, wait=BSH_RETRY_WAIT_S):
    for attempt in range(1, BSH_ATTEMPTS + 1):
        try:
            save_url(url, path, timeout=1800)
            return
        # urllib leaves a timeout reading the headers or the body unwrapped.
        except (urllib.error.URLError, TimeoutError) as error:
            if shom.is_client_error(error) or attempt == BSH_ATTEMPTS:
                raise RuntimeError(f'BSH did not answer {url}: {error}') from error
            print(f'bsh: {error}, asking again', flush=True)
            time.sleep(wait * attempt)


def has_data(path):
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    return bool((band.ReadAsArray() != band.GetNoDataValue()).any())


def german_separation(tmp):
    """NHN above LAT over German waters, built once per run."""
    separation = os.path.join(tmp, 'german-separation.tif')
    if os.path.exists(separation):
        return separation
    datum = os.path.join(tmp, os.path.basename(GCG2016))
    save_url(GCG2016, datum)
    lat_surfaces = []
    for url in (DKLAT, NLLAT):
        lat_surfaces.append(os.path.join(tmp, os.path.basename(url)))
        save_url(url, lat_surfaces[-1])
    lat = os.path.join(tmp, 'german-lat.tif')
    gdal.Warp(
        lat,
        lat_surfaces,
        dstSRS='EPSG:4326',
        outputBounds=BSH_LAT_BOUNDS,
        xRes=BSH_LAT_STEP,
        yRes=BSH_LAT_STEP,
        resampleAlg='bilinear',
        dstNodata=BSH_LAT_NODATA,
    )
    lat_model.separation(datum, lat, separation)
    return separation


def fetch_bsh(bounds, target, tmp):
    """On top of what the region's earlier models wrote, which already carry
    EMODnet beyond themselves, so the BSH chunks replace theirs whole; on the
    region's EMODnet where there are none."""
    separation = german_separation(tmp)
    emodnet = emodnet_fill('bsh', bounds, tmp)
    # WSV's chunks lie over BSH's in the mosaic, never under them.
    earlier = [
        path for path in dem_grids(os.path.dirname(target)) if not os.path.basename(path).startswith(('bsh_', 'wsv_'))
    ]
    fill = os.path.join(tmp, 'bsh-fill.vrt')
    if earlier:
        gdal.BuildVRT(fill, earlier, resolution='highest')
    else:
        fill = emodnet
    grid = BSH_GRID
    for s, w in grid.chunks(bounds):
        parts = []
        for sea in BSH_SEAS:
            url = bsh_request(sea, grid, s, w)
            if url:
                parts.append(os.path.join(tmp, f'bsh-{sea}.tif'))
                bsh_save(url, parts[-1])
        path = os.path.join(target, f'bsh_{s:+03d}_{w:+04d}.tif')
        if not any(has_data(part) for part in parts):
            if earlier:
                # The earlier models' chunks stand as they are.
                if os.path.exists(path):
                    os.remove(path)
            else:
                lat_model.write_chunk(emodnet, None, None, grid, s, w, path, depths=False, resample='near')
            continue
        model = os.path.join(tmp, 'bsh.vrt')
        gdal.BuildVRT(model, parts, srcNodata=BSH_NODATA, VRTNodata=lat_model.NODATA)
        lat_model.write_chunk(model, separation, fill, grid, s, w, path, depths=False, emodnet_path=emodnet)


def wsv_model(tmp):
    """The estuary models as one mosaic in UTM 32, each cut to what it keeps."""
    layers = []
    for i, model in enumerate(WSV_MODELS):
        local = os.path.join(tmp, f'wsv-{i}-' + os.path.basename(model['url']))
        save_url(model['url'], local, timeout=3600)
        source = f'/vsizip/{local}/{model["member"]}' if 'member' in model else local
        layers.append(os.path.join(tmp, f'wsv-{i}.vrt'))
        ds = gdal.Open(source)
        gt = ds.GetGeoTransform()
        west, south, east, north = model.get('keep', (-math.inf, -math.inf, math.inf, math.inf))
        # Not every file names its reference system.
        gdal.Translate(
            layers[-1],
            ds,
            format='VRT',
            outputSRS=WSV_SRS,
            projWin=(
                max(west, gt[0]),
                min(north, gt[3]),
                min(east, gt[0] + gt[1] * ds.RasterXSize),
                max(south, gt[3] + gt[5] * ds.RasterYSize),
            ),
        )
    mosaic = os.path.join(tmp, 'wsv.vrt')
    gdal.BuildVRT(mosaic, layers, resolution='highest', VRTNodata=lat_model.NODATA)
    return mosaic


def raster_bounds(path):
    """A raster's extent in WGS84."""
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform()
    source = osr.SpatialReference(wkt=ds.GetProjection())
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return osr.CoordinateTransformation(source, wgs84).TransformBounds(
        gt[0], gt[3] + gt[5] * ds.RasterYSize, gt[0] + gt[1] * ds.RasterXSize, gt[3], 21
    )


def fetch_wsv(bounds, target, tmp):
    """The estuary models' shoalest value in each chart cell, on LAT, and no
    data beyond them: the mosaic lays them over the region's earlier models,
    which a copy onto the finer chart grid would turn blocky."""
    model = wsv_model(tmp)
    w, s, e, n = raster_bounds(model)
    covered = (max(bounds[0], w), max(bounds[1], s), min(bounds[2], e), min(bounds[3], n))
    written = set()
    if covered[0] < covered[2] and covered[1] < covered[3]:
        separation = german_separation(tmp)
        for s, w in WSV_GRID.chunks(covered):
            path = os.path.join(target, f'wsv_{s:+03d}_{w:+04d}.tif')
            if lat_model.write_chunk(model, separation, None, WSV_GRID, s, w, path, depths=False, resample='max'):
                written.add(path)
    # A chunk from an earlier run outside today's coverage would still reach the builder.
    for path in glob.glob(os.path.join(target, 'wsv_*.tif')):
        if path not in written:
            os.remove(path)


def ausseabed_separation(tmp):
    """Mean sea level above LAT over Australian waters."""
    lpt = os.path.join(tmp, 'ausseabed-lpt.tif')
    save_url(AUSSEABED_LPT, lpt)
    source = gdal.Open(lpt)
    below = -source.GetRasterBand(1).ReadAsArray().astype('float32')
    sep = np.where(np.isnan(below), lat_model.NODATA, np.maximum(below, lat_model.MIN_SEPARATION_M))
    path = os.path.join(tmp, 'ausseabed-separation.tif')
    out = gdal.GetDriverByName('GTiff').Create(path, source.RasterXSize, source.RasterYSize, 1, gdal.GDT_Float32)
    out.SetGeoTransform(source.GetGeoTransform())
    out.SetProjection(lat_model.wgs84())
    band = out.GetRasterBand(1)
    band.SetNoDataValue(lat_model.NODATA)
    band.WriteArray(sep.astype('float32'))
    gdal.FillNodata(
        targetBand=band,
        maskBand=None,
        maxSearchDist=AUSSEABED_SEPARATION_FILL_CELLS,
        smoothingIterations=0,
        options=['TEMP_FILE_DRIVER=MEM'],
    )
    out = source = None
    return path


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def ausseabed_model(bounds, tmp, national=False):
    """The models reaching `bounds` as one mosaic in WGS84, or None."""
    models = [AUSSEABED_NATIONAL] if national else []
    models += [model for model in AUSSEABED_MODELS if overlaps(model[2], bounds)]
    if not models:
        return None
    layers = []
    grid = {'resolution': 'highest'}
    for i, (url, members, extent) in enumerate(models):
        folder = os.path.join(tmp, f'ausseabed-{i}')
        save_zip_members(url, members, folder)
        path = os.path.join(folder, members[0])
        if not is_wgs84(path):
            path = onto_chart_grid(path, intersection(bounds, extent), folder)
            # The mosaic's origin otherwise comes from the union of its layers and would
            # sample the chart-grid layer off its own cells.
            grid = {'resolution': 'user', 'xRes': GRID_STEP, 'yRes': GRID_STEP, 'targetAlignedPixels': True}
        layers.append(path)
    mosaic = os.path.join(tmp, 'ausseabed.vrt')
    gdal.BuildVRT(mosaic, layers, VRTNodata=lat_model.NODATA, **grid)
    return mosaic


def is_wgs84(path):
    srs = osr.SpatialReference(wkt=gdal.Open(path).GetProjection())
    return srs.GetAuthorityCode(None) == '4326'


def intersection(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def onto_chart_grid(path, bounds, folder):
    """A projected model in WGS84 on the chart grid over `bounds`, each cell
    its shoalest sample, so the mosaic can stack it with the others."""
    west, south, east, north = (
        math.floor(bounds[0] / GRID_STEP) * GRID_STEP,
        math.floor(bounds[1] / GRID_STEP) * GRID_STEP,
        math.ceil(bounds[2] / GRID_STEP) * GRID_STEP,
        math.ceil(bounds[3] / GRID_STEP) * GRID_STEP,
    )
    out = os.path.join(folder, 'chart-grid.tif')
    gdal.Warp(
        out,
        path,
        options=['-ovr', 'NONE'],
        dstSRS='EPSG:4326',
        outputBounds=(west, south, east, north),
        xRes=GRID_STEP,
        yRes=GRID_STEP,
        resampleAlg='max',
        # Exact transforms, so a cell's footprint does not move with the warp's chunking.
        errorThreshold=0,
        dstNodata=lat_model.NODATA,
        outputType=gdal.GDT_Float32,
        creationOptions=['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3', 'SPARSE_OK=TRUE', 'BIGTIFF=YES'],
        multithread=True,
        warpMemoryLimit=1024,
    )
    return out


def fetch_ausseabed(bounds, target, tmp, national=False):
    """The models' shoalest value in each cell, on LAT, where a chunk reaches
    water shallower than AUSSEABED_DEEP_M."""
    model = ausseabed_model(bounds, tmp, national)
    written = set()
    if model:
        separation = ausseabed_separation(tmp)
        w, s, e, n = raster_bounds(model)
        covered = (max(bounds[0], w), max(bounds[1], s), min(bounds[2], e), min(bounds[3], n))
        for s, w in AUSSEABED_GRID.chunks(covered):
            path = os.path.join(target, f'ausseabed_{s:+03d}_{w:+04d}.tif')
            if not lat_model.write_chunk(
                model, separation, None, AUSSEABED_GRID, s, w, path, depths=False, resample='max'
            ):
                continue
            heights = gdal.Open(path).ReadAsArray()
            if (heights[heights != lat_model.NODATA] > -AUSSEABED_DEEP_M).any():
                written.add(path)
    # Deep chunks, and chunks from an earlier run outside today's coverage, would still reach the builder.
    for path in glob.glob(os.path.join(target, 'ausseabed_*.tif')):
        if path not in written:
            os.remove(path)


INFOMAR = (
    'https://gsi.geodata.gov.ie/imagehost/rest/services/Marine/IE_GSI_MI_Bathymetry_{layer}_WGS84_LAT_GRID/ImageServer'
)
# Each layer is fetched on the grid nearest its own cells, the 25 m grid
# first so the 10 m one lies on top of it in the mosaic. On a grid as fine as
# the layer's own cells, nearest neighbour keeps every measured height.
INFOMAR_LAYERS = (('25m_IE_Waters', GRID_STEP * 2.5, 0.5), ('10m_Inshore_IE', GRID_STEP, 0.25))
INFOMAR_PROBE_STEP = 0.005
# The service answers 0 where it has no data.
INFOMAR_NODATA = 0.0


def infomar_export(layer, bounds, width, height, path):
    query = urllib.parse.urlencode(
        {
            'bbox': ','.join(f'{v:.6f}' for v in bounds),
            'bboxSR': 4326,
            'imageSR': 4326,
            'size': f'{width},{height}',
            'format': 'tiff',
            'compression': 'LZ77',
            'pixelType': 'F32',
            'interpolation': 'RSP_NearestNeighbor',
            'f': 'image',
        }
    )
    save_url(f'{INFOMAR.format(layer=layer)}/exportImage?{query}', path, timeout=900)
    return gdal.Open(path).ReadAsArray()


def infomar_tiles(layer, bounds, tile_deg, tmp):
    """The tiles of `tile_deg` degrees on a global grid that the layer has data in."""
    minlon, minlat, maxlon, maxlat = (
        math.floor(bounds[0] / tile_deg) * tile_deg,
        math.floor(bounds[1] / tile_deg) * tile_deg,
        math.ceil(bounds[2] / tile_deg) * tile_deg,
        math.ceil(bounds[3] / tile_deg) * tile_deg,
    )
    per_tile = round(tile_deg / INFOMAR_PROBE_STEP)
    cols, rows = round((maxlon - minlon) / tile_deg), round((maxlat - minlat) / tile_deg)
    probe = infomar_export(
        layer, (minlon, minlat, maxlon, maxlat), cols * per_tile, rows * per_tile, os.path.join(tmp, 'probe.tif')
    )
    data = (probe != INFOMAR_NODATA) & np.isfinite(probe)
    present = data.reshape(rows, per_tile, cols, per_tile).any(axis=(1, 3))
    for row, col in zip(*np.nonzero(present), strict=True):
        north = maxlat - row * tile_deg
        west = minlon + col * tile_deg
        yield round(north - tile_deg, 6), round(west, 6)


def fetch_infomar(bounds, target, tmp):
    written = set()
    for layer, step, tile_deg in INFOMAR_LAYERS:
        cells = round(tile_deg / step)
        for s, w in infomar_tiles(layer, bounds, tile_deg, tmp):
            heights = infomar_export(
                layer, (w, s, w + tile_deg, s + tile_deg), cells, cells, os.path.join(tmp, 'infomar.tif')
            )
            heights[(heights == INFOMAR_NODATA) | ~np.isfinite(heights) | (heights > EMODNET_MAX_HEIGHT_M)] = (
                EMODNET_NODATA
            )
            if not (heights != EMODNET_NODATA).any():
                continue
            path = os.path.join(target, f'infomar_{layer.split("_")[0]}_{s:+08.3f}_{w:+08.3f}.tif')
            out = gdal.GetDriverByName('GTiff').Create(
                path + '.part', cells, cells, 1, gdal.GDT_Float32, ['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES']
            )
            out.SetGeoTransform((w, step, 0.0, s + tile_deg, 0.0, -step))
            out.SetProjection(lat_model.wgs84())
            band = out.GetRasterBand(1)
            band.SetNoDataValue(EMODNET_NODATA)
            band.WriteArray(heights.astype(np.float32))
            out = None
            os.replace(path + '.part', path)
            written.add(path)
    # A tile from an earlier run that this one no longer has would still
    # reach the mosaic.
    for path in glob.glob(os.path.join(target, 'infomar_*.tif')):
        if path not in written:
            os.remove(path)


NONNA_WCS = (
    'https://nonna-geoserver.data.chs-shc.ca/geoserver/wcs?service=WCS&version=2.0.1&request=GetCoverage'
    '&format=image/tiff&geotiff:compression=Deflate'
    '&subsettingCrs=http://www.opengis.net/def/crs/EPSG/0/4326'
)
# NONNA-100 first, on a grid near its own ~100 m cells, so NONNA-10 lies on
# top of it in the mosaic. NONNA-10 has data only where NONNA-100 has, so
# NONNA-100's tiles say which NONNA-10 tiles to ask for. Tiles stay small:
# a request of several degrees knocks the service out for minutes.
NONNA_COVERAGES = (('100', GRID_STEP * 8, 1.0), ('10', GRID_STEP, 0.5))
NONNA_ATTEMPTS = 4
NONNA_RETRY_WAIT_S = 60


def close_seams(heights, data):
    """The service's mosaic leaves a one-cell gap along the edges of its
    0.1-degree source tiles; a gap cell with data on both sides takes the
    shoaler of the two."""
    for axis in (0, 1):
        before, after = np.roll(heights, 1, axis), np.roll(heights, -1, axis)
        has_before, has_after = np.roll(data, 1, axis), np.roll(data, -1, axis)
        edge = np.zeros_like(data)
        if axis == 0:
            edge[[0, -1], :] = True
        else:
            edge[:, [0, -1]] = True
        gap = ~data & has_before & has_after & ~edge
        heights[gap] = np.maximum(before[gap], after[gap])
        data = data | gap
    return heights, data


def nonna_coverage(name, bounds, path, wait=NONNA_RETRY_WAIT_S):
    """Where the coverage has data over `bounds`; its heights (negative
    under water) are left at `path` on its own cells. The service answers
    an overloaded request with an exception report, not an error status."""
    west, south, east, north = bounds
    url = (
        f'{NONNA_WCS}&coverageId={urllib.parse.quote(f"nonna__NONNA {name} Coverage")}'
        f'&subset=Long({west:.6f},{east:.6f})&subset=Lat({south:.6f},{north:.6f})'
    )
    for attempt in range(NONNA_ATTEMPTS):
        if attempt:
            time.sleep(wait * attempt)
        save_url(url, path, timeout=900)
        try:
            ds = gdal.Open(path, gdal.GA_Update)
        except RuntimeError:
            with open(path, 'rb') as fh:
                print(f'NONNA-{name} {bounds}: {fh.read(600)!r}', flush=True)
            continue
        band = ds.GetRasterBand(1)
        heights = band.ReadAsArray()
        nodata = band.GetNoDataValue()
        data = np.isfinite(heights) & (heights != nodata if nodata is not None else True)
        heights, data = close_seams(heights, data)
        band.WriteArray(heights)
        ds = None
        return data
    raise RuntimeError(f'NONNA-{name}: no coverage for {bounds} after {NONNA_ATTEMPTS} attempts')


def nonna_tiles(bounds, tile_deg):
    minlon, minlat, maxlon, maxlat = bounds
    for s in np.arange(math.floor(minlat / tile_deg) * tile_deg, maxlat, tile_deg):
        for w in np.arange(math.floor(minlon / tile_deg) * tile_deg, maxlon, tile_deg):
            yield round(float(s), 6), round(float(w), 6)


def fetch_nonna(bounds, target, tmp):
    """CHS's NONNA models, the shoalest sample in each cell; drying banks
    are kept, land above them left to the land data."""
    written = set()
    present = None
    for name, step, tile_deg in NONNA_COVERAGES:
        found = []
        for s, w in nonna_tiles(bounds, tile_deg):
            if present is not None and not any(ps <= s < ps + pdeg and pw <= w < pw + pdeg for ps, pw, pdeg in present):
                continue
            raw = os.path.join(tmp, 'nonna.tif')
            data = nonna_coverage(name, (w, s, w + tile_deg, s + tile_deg), raw)
            if not data.any():
                continue
            found.append((s, w, tile_deg))
            cells = round(tile_deg / step)
            # The service's cells are Mercator ones; the shoalest sample in each chart cell.
            warped = gdal.Warp(
                '',
                raw,
                format='MEM',
                dstSRS='EPSG:4326',
                outputBounds=(w, s, w + tile_deg, s + tile_deg),
                width=cells,
                height=cells,
                resampleAlg='max',
                dstNodata=EMODNET_NODATA,
            )
            heights = warped.GetRasterBand(1).ReadAsArray()
            warped = None
            heights[heights > EMODNET_MAX_HEIGHT_M] = EMODNET_NODATA
            if not (heights != EMODNET_NODATA).any():
                continue
            path = os.path.join(target, f'nonna_{name}m_{s:+08.3f}_{w:+09.3f}.tif')
            out = gdal.GetDriverByName('GTiff').Create(
                path + '.part', cells, cells, 1, gdal.GDT_Float32, ['COMPRESS=DEFLATE', 'PREDICTOR=3', 'TILED=YES']
            )
            out.SetGeoTransform((w, step, 0.0, s + tile_deg, 0.0, -step))
            out.SetProjection(lat_model.wgs84())
            band = out.GetRasterBand(1)
            band.SetNoDataValue(EMODNET_NODATA)
            band.WriteArray(heights.astype(np.float32))
            out = None
            os.replace(path + '.part', path)
            written.add(path)
        present = found
    # A tile from an earlier run that this one no longer has would still reach the mosaic.
    for path in glob.glob(os.path.join(target, 'nonna_*.tif')):
        if path not in written:
            os.remove(path)


def icgc_model(tmp):
    """The mirrored model, its parts joined in name order and each checked against its digest."""
    repo = os.environ.get('GITHUB_REPOSITORY', 'dirkwa/open-data-charts')
    assets = release_assets(f'https://api.github.com/repos/{repo}/releases/tags/{ICGC_RELEASE}')
    parts = sorted(name for name in assets if name.startswith(ICGC_FILE + '.part'))
    if not parts:
        raise RuntimeError(f'the release {ICGC_RELEASE} has no parts of {ICGC_FILE}')
    model = os.path.join(tmp, ICGC_FILE)
    with open(model, 'wb') as joined:
        for name in parts:
            part = os.path.join(tmp, name)
            save_square(*assets[name], part)
            with open(part, 'rb') as fh:
                shutil.copyfileobj(fh, joined, CHUNK)
            os.remove(part)
    return model


def fetch_icgc(bounds, target, tmp):
    """The model's shoalest value in each cell, so a shoal never reads
    deeper than the model has it; the region's own EMODnet stands beyond it."""
    minlon, minlat, maxlon, maxlat = ICGC_BOUNDS
    covered = (max(bounds[0], minlon), max(bounds[1], minlat), min(bounds[2], maxlon), min(bounds[3], maxlat))
    if covered[0] >= covered[2] or covered[1] >= covered[3]:
        return
    model = icgc_model(tmp)
    for s, w in ICGC_GRID.chunks(covered):
        path = os.path.join(target, f'icgc_{s:+03d}_{w:+04d}.tif')
        lat_model.write_chunk(model, None, None, ICGC_GRID, s, w, path, depths=False, resample='max')
    os.remove(model)


def fetch_dem(region, out):
    target = os.path.join(out, 'dem')
    os.makedirs(target, exist_ok=True)
    dates = {}
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        for dem in region.get('dem', []):
            if dem.get('source') == 'emodnet':
                fetch_emodnet(fetch_bounds(region), target, dem.get('microtidal', False))
                continue
            if dem.get('source') == 'emodnet-hr':
                fetch_emodnet_hr(dem['dtms'], target, tmp)
                continue
            if dem.get('source') == 'ddm':
                dates['ddm'] = fetch_ddm(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'vaklodingen':
                fetch_vaklodingen(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'bsh':
                fetch_bsh(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'wsv':
                fetch_wsv(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'kartverket':
                fetch_kartverket(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'infomar':
                fetch_infomar(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'nonna':
                fetch_nonna(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'icgc':
                fetch_icgc(fetch_bounds(region), target, tmp)
                continue
            if dem.get('source') == 'ausseabed':
                fetch_ausseabed(fetch_bounds(region), target, tmp, dem.get('national', False))
                continue
            survey = {'group': dem['group'], 'name': dem['prepackage']}
            for item in shom.list_files(survey):
                archive = os.path.join(tmp, item['fileName'])
                shom.download(shom.file_url(survey, item['fileName']), archive, item['fileSize'], item['fileMd5'])
                subprocess.run(
                    ['7z', 'e', '-y', f'-o{target}', archive, '*.asc', '-r'], check=True, stdout=subprocess.DEVNULL
                )
                os.remove(archive)
    if dates:
        with open(os.path.join(out, attribution.DATES_FILE), 'w', encoding='utf-8') as fh:
            json.dump(dates, fh)
    print('dem:', sorted(os.listdir(target)), flush=True)


def download_extract(alternatives, path, wait=OSM_RETRY_WAIT_S):
    for attempt in range(OSM_ATTEMPTS):
        if attempt:
            time.sleep(wait * attempt)
        for url in alternatives:
            try:
                save_url(url, path)
                return
            except (OSError, urllib.error.URLError) as exc:
                print(f'osm: {url} failed ({exc})', flush=True)
    raise RuntimeError(f'no OSM extract could be downloaded from {alternatives}')


def export_filtered(extract, rules, target, tmp, areas_only=False):
    filtered = os.path.join(tmp, 'filtered.osm.pbf')
    subprocess.run(['osmium', 'tags-filter', '-O', '-o', filtered, extract, *rules], check=True)
    subprocess.run(
        [
            'osmium',
            'export',
            '-O',
            '-f',
            'geojsonseq',
            '--add-unique-id=type_id',
            '-x',
            'print_record_separator=false',
            *(['--geometry-types=polygon'] if areas_only else []),
            '-o',
            target,
            filtered,
        ],
        check=True,
    )


def merge_extracts(parts, extract, tmp):
    if len(parts) == 1:
        os.replace(parts[0], extract)
        return
    merged = os.path.join(tmp, 'merged.osm.pbf')
    subprocess.run(['osmium', 'merge', '-O', '-o', merged, *parts], check=True)
    # Extracts cut at different times can hold different versions of an
    # object on their shared border, and merge keeps both; osmium export
    # refuses such a file. time-filter keeps each object's newest version.
    subprocess.run(['osmium', 'time-filter', '-O', '-o', extract, merged], check=True)
    os.remove(merged)


def fetch_osm(region, out):
    """region['osm']: the extracts covering the region, each a list of
    alternative URLs for the same extract."""
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        parts = []
        for i, alternatives in enumerate(region['osm']):
            parts.append(os.path.join(tmp, f'part{i}.osm.pbf'))
            download_extract(alternatives, parts[-1])
        extract = os.path.join(tmp, 'extract.osm.pbf')
        merge_extracts(parts, extract, tmp)
        exported = os.path.join(tmp, 'exported.geojsonseq')
        export_filtered(extract, osm.OSMIUM_FILTER, exported, tmp)
        target = os.path.join(out, 'water.geojsonseq')
        export_filtered(extract, osm.WATER_FILTER, target + '.part', tmp, areas_only=True)
        os.replace(target + '.part', target)
        target = os.path.join(out, 'seamarks.geojsonseq')
        if 'openseamap' in region.get('sources', []):
            export_filtered(extract, (osm.SEAMARK_FILTER,), target + '.part', tmp)
            os.replace(target + '.part', target)
        elif os.path.exists(target):
            os.remove(target)
        bounds = fetch_bounds(region)
        near = near_sea(os.path.join(out, 'land.gpkg'), bounds)
        target = os.path.join(out, 'osm.geojsonseq')
        kept, dropped = osm.drop_inland_buildings(exported, target, near, bounds, NEAR_SEA_STEP)
        print(f'osm: {kept} features kept, {dropped} inland buildings dropped', flush=True)


def near_sea(land_path, bounds):
    """Cells of NEAR_SEA_STEP within NEAR_SEA_CELLS of a cell holding sea,
    over bounds (row 0 at the top). A cell holds sea when its centre is off
    land or a coastline crosses it, so inlets narrower than a cell count."""
    minlon, minlat, maxlon, maxlat = bounds
    ncols = math.ceil((maxlon - minlon) / NEAR_SEA_STEP)
    nrows = math.ceil((maxlat - minlat) / NEAR_SEA_STEP)
    land_ds = gdal.OpenEx(land_path, gdal.OF_VECTOR)
    land = land_ds.GetLayer(0)
    coast_ds = gdal.GetDriverByName('MEM').CreateVector('')
    coast = coast_ds.CreateLayer('coast', geom_type=ogr.wkbMultiLineString)
    for feature in land:
        line = ogr.Feature(coast.GetLayerDefn())
        line.SetGeometry(feature.GetGeometryRef().Boundary())
        coast.CreateFeature(line)

    def burn(layer, options=None):
        raster = gdal.GetDriverByName('MEM').Create('', ncols, nrows, 1, gdal.GDT_Byte)
        raster.SetGeoTransform((minlon, NEAR_SEA_STEP, 0.0, maxlat, 0.0, -NEAR_SEA_STEP))
        gdal.RasterizeLayer(raster, [1], layer, burn_values=[1], options=options or [])
        return raster.GetRasterBand(1).ReadAsArray() == 1

    land.ResetReading()
    near = ~burn(land) | burn(coast, ['ALL_TOUCHED=TRUE'])
    for _ in range(NEAR_SEA_CELLS):
        grown = near.copy()
        grown[1:] |= near[:-1]
        grown[:-1] |= near[1:]
        grown[:, 1:] |= near[:, :-1]
        grown[:, :-1] |= near[:, 1:]
        near = grown
    return near


def fetch_land(region, out):
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        archive = os.path.join(tmp, 'land.zip')
        save_url(LAND_POLYGONS, archive, timeout=1800)
        target = os.path.join(out, 'land.gpkg')
        subprocess.run(
            [
                'ogr2ogr',
                '-overwrite',
                '-f',
                'GPKG',
                '-nln',
                'land',
                '-spat',
                str(minlon),
                str(minlat),
                str(maxlon),
                str(maxlat),
                target + '.part.gpkg',
                f'/vsizip/{archive}/{LAND_POLYGONS_SHP}',
            ],
            check=True,
        )
        os.replace(target + '.part.gpkg', target)


def copernicus_name(lat, lon):
    ns = f'N{lat:02d}' if lat >= 0 else f'S{-lat:02d}'
    ew = f'E{lon:03d}' if lon >= 0 else f'W{-lon:03d}'
    return f'Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM'


def fetch_relief(region, out):
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    target = os.path.join(out, 'relief')
    os.makedirs(target, exist_ok=True)
    for lat in range(math.floor(minlat), math.ceil(maxlat)):
        for lon in range(math.floor(minlon), math.ceil(maxlon)):
            name = copernicus_name(lat, lon)
            try:
                save_url(COPERNICUS.format(n=name), os.path.join(target, name + '.tif'))
            except urllib.error.HTTPError as exc:
                if exc.code not in (403, 404):  # all-sea tiles do not exist
                    raise


def etopo_name(top, left):
    ns = f'N{top:02d}' if top >= 0 else f'S{-top:02d}'
    ew = f'E{left:03d}' if left >= 0 else f'W{-left:03d}'
    return ns + ew


def fetch_etopo(region, out):
    """The ETOPO tiles under the region's units, each named after its
    north-west corner; none where charts give the depths."""
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    m, step = build_unit.ETOPO_MARGIN_DEG, ETOPO_TILE_DEG
    tops = range(math.ceil((maxlat + m) / step) * step, math.floor((minlat - m) / step) * step, -step)
    lefts = range(math.floor((minlon - m) / step) * step, math.ceil((maxlon + m) / step) * step, step)
    names = {etopo_name(top, left) + '.tif' for top in tops for left in lefts}
    if CHART_SOURCES & set(region.get('sources', [])):
        names = set()
    target = os.path.join(out, 'etopo')
    os.makedirs(target, exist_ok=True)
    for stale in set(os.listdir(target)) - names:
        os.remove(os.path.join(target, stale))
    for name in sorted(names - set(os.listdir(target))):
        save_url(ETOPO.format(n=name.removesuffix('.tif')), os.path.join(target, name))
    print('etopo:', sorted(names), flush=True)


def arcgis_features(url, where, bbox):
    meta = shom.get_json(f'{url}?f=json')
    features = []
    while True:
        params = {
            'where': where,
            'geometry': ','.join(map(str, bbox)),
            'geometryType': 'esriGeometryEnvelope',
            'inSR': 4326,
            'outSR': 4326,
            'outFields': '*',
            'f': 'geojson',
            'orderByFields': meta.get('objectIdField') or 'objectid',
            'resultOffset': len(features),
            'resultRecordCount': ARCGIS_PAGE,
        }
        page = shom.get_json(f'{url}/query?{urllib.parse.urlencode(params)}', timeout=180)
        batch = page.get('features') or []
        features.extend(batch)
        more = page.get('exceededTransferLimit') or (page.get('properties') or {}).get('exceededTransferLimit')
        if not batch or not more:
            return meta, features


def fetch_arcgis(region, out):
    for layer in region.get('arcgis', []):
        meta, features = arcgis_features(layer['url'], layer.get('where', '1=1'), fetch_bounds(region))
        save_json(
            os.path.join(out, layer['file']),
            {
                'type': 'FeatureCollection',
                'source': layer['url'],
                'copyright': meta.get('copyrightText'),
                'features': features,
            },
        )
        print(f'{len(features):6d}  {layer["file"]}', flush=True)


def aca_request(cql, **params):
    return f'{ACA_WFS}&{urllib.parse.urlencode({"CQL_FILTER": cql, **params})}'


def aca_features(classes, w, s, e, n):
    """Every feature of these classes meeting the box. The layer has no key
    to page in a stable order, so a box with more than ACA_PAGE features is
    split instead, and each answer must hold all the features it matched."""
    cql = f"class_name IN ({classes}) AND BBOX(geom,{w},{s},{e},{n},'EPSG:4326')"
    with shom.get(aca_request(cql, resultType='hits'), timeout=600) as response:
        hits = int(re.search(rb'numberMatched="(\d+)"', response.read()).group(1))
    if hits > ACA_PAGE:
        mx, my = (w + e) / 2, (s + n) / 2
        quarters = ((w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n))
        return [f for q in quarters for f in aca_features(classes, *q)]
    features = []
    if hits:
        features = shom.get_json(aca_request(cql, count=ACA_PAGE), timeout=600).get('features') or []
    if len(features) != hits:
        raise RuntimeError(f'Allen Coral Atlas answered {len(features)} of {hits} features in {w},{s},{e},{n}')
    return features


def fetch_aca(region, out):
    """The Allen Coral Atlas classes the builder charts, one file per
    1-degree square that has any. A reef crossing a square's edge is in
    both files; the builder and the versions read each feature once. The
    service's feature ids change between requests, so the files leave them
    out and stay the same while the atlas does."""
    target = os.path.join(out, 'aca')
    shutil.rmtree(target, ignore_errors=True)
    if 'allen-coral-atlas' not in region.get('sources', []):
        return
    os.makedirs(target)
    classes = ','.join(f"'{name}'" for name in build_unit.ACA_WATLEV)
    minlon, minlat, maxlon, maxlat = fetch_bounds(region)
    total = 0
    for s in range(math.floor(minlat), math.ceil(maxlat)):
        for w in range(math.floor(minlon), math.ceil(maxlon)):
            lines = {
                json.dumps(
                    {
                        'type': 'Feature',
                        'properties': {'class_name': f['properties']['class_name']},
                        'geometry': f['geometry'],
                    },
                    separators=(',', ':'),
                )
                for f in aca_features(classes, w, s, w + 1, s + 1)
            }
            if not lines:
                continue
            path = os.path.join(target, f'reefs_{s:+03d}_{w:+04d}.geojsonseq')
            with open(path + '.part', 'w', encoding='utf-8') as fh:
                fh.writelines(line + '\n' for line in sorted(lines))
            os.replace(path + '.part', path)
            total += len(lines)
    print(f'aca: {total} reef features in {len(os.listdir(target))} squares', flush=True)


ACA_DEPTH_RELEASE = 'https://api.github.com/repos/{repo}/releases/tags/' + aca_depth.RELEASE


def release_assets(url):
    """Asset name -> (download URL, 'sha256:<hex>'), or {} while the release does not exist."""
    token = os.environ.get('GITHUB_TOKEN')
    headers = {'Accept': 'application/vnd.github+json', **({'Authorization': f'Bearer {token}'} if token else {})}
    try:
        with shom.get(url, headers=headers) as response:
            release = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise
    # Listed from the release's own asset list, which pages.
    assets, page = {}, 1
    while True:
        with shom.get(f'{release["assets_url"]}?per_page=100&page={page}', headers=headers) as response:
            listed = json.load(response)
        # An asset still uploading is listed too, but cannot be downloaded whole yet.
        assets.update(
            {
                a['name']: (a['browser_download_url'], a.get('digest'))
                for a in listed
                if a.get('state', 'uploaded') == 'uploaded' and a.get('size', 1) > 0
            }
        )
        if len(listed) < 100:
            return assets
        page += 1


def aca_depth_assets(release, names):
    """Download URL and digest of each listed square among names."""
    listed = release_assets(release)
    assets = {}
    for name in names:
        # While aca-depth.yml replaces a square, it may be listed only as next-<square>.
        found = listed.get(name) or listed.get(f'next-{name}')
        if found:
            assets[name] = found
    return assets


# A square replaced while a fetch lists and downloads it can move on more than once.
SQUARE_FETCH_ATTEMPTS = 3


class DigestMismatch(OSError):
    pass


def save_square(url, digest, path):
    """Replaces the square at path only with a download that matches its digest:
    a connection closing early can end a download without an error."""
    if not digest:
        raise DigestMismatch(f'{url}: the release lists no digest to check the download against')
    save_url(url, path + '.new')
    if f'sha256:{sha256_of(path + ".new")}' != digest:
        os.remove(path + '.new')
        raise DigestMismatch(f'{url}: the download does not match {digest}')
    os.replace(path + '.new', path)


def fetch_aca_depth(region, out):
    """The satellite-derived bathymetry squares under the region's units,
    for regions charting Allen Coral Atlas reefs."""
    target = os.path.join(out, 'aca-depth')
    assets = {}
    if 'allen-coral-atlas' in region.get('sources', []):
        repo = os.environ.get('GITHUB_REPOSITORY', 'dirkwa/open-data-charts')
        release = ACA_DEPTH_RELEASE.format(repo=repo)
        minlon, minlat, maxlon, maxlat = fetch_bounds(region)
        names = {
            aca_depth.square_name(s, w)
            for s in range(math.floor(minlat), math.ceil(maxlat))
            for w in range(math.floor(minlon), math.ceil(maxlon))
        }
        assets = aca_depth_assets(release, names)
    os.makedirs(target, exist_ok=True)
    for stale in set(os.listdir(target)) - set(assets):
        os.remove(os.path.join(target, stale))
    for name in sorted(assets):
        url, digest = assets[name]
        path = os.path.join(target, name)
        if os.path.exists(path) and f'sha256:{sha256_of(path)}' == digest:
            continue
        for attempt in range(SQUARE_FETCH_ATTEMPTS):
            try:
                save_square(url, digest, path)
                break
            except (urllib.error.HTTPError, DigestMismatch) as error:
                # An import may have replaced the square since it was listed: list it again.
                missing = isinstance(error, urllib.error.HTTPError)
                if (missing and error.code != 404) or attempt == SQUARE_FETCH_ATTEMPTS - 1:
                    raise
                relisted = aca_depth_assets(release, [name]).get(name)
                # A 404 can come between deleting a square and renaming its replacement
                # into place, which may list the same URL and digest again.
                if relisted is None or (relisted == (url, digest) and not missing):
                    raise
                url, digest = relisted
    print('aca-depth:', sorted(assets), flush=True)


FETCHERS = {
    'wfs': fetch_wfs,
    'chart': fetch_chart,
    'dem': fetch_dem,
    'land': fetch_land,
    'osm': fetch_osm,
    'relief': fetch_relief,
    'etopo': fetch_etopo,
    'arcgis': fetch_arcgis,
    'aca': fetch_aca,
    'aca-depth': fetch_aca_depth,
}


def main():
    region_path, out, *names = sys.argv[1:]
    with open(region_path, encoding='utf-8') as fh:
        region = json.load(fh)
    os.makedirs(out, exist_ok=True)
    names = list(names or FETCHERS)
    if 'osm' in names and ('land' in names or not os.path.exists(os.path.join(out, 'land.gpkg'))):
        if 'land' in names:
            names.remove('land')
        names.insert(names.index('osm'), 'land')  # near_sea reads it
    for name in names:
        print(f'== {name}', flush=True)
        FETCHERS[name](region, out)


if __name__ == '__main__':
    main()
