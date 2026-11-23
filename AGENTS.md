# AGENTS.md

Guidance for AI coding agents and human contributors. `CONTRIBUTING.md` is
the shorter walk-through for people.

## What this is

A data pipeline: GitHub Actions build vector nautical charts from open data
(Shom, EMODnet, LINZ, Gouvernement de la Nouvelle-Calédonie, OpenStreetMap,
Copernicus) as one MBTiles per *unit*, a z9 Web-Mercator tile, and publish
them as release assets with an index on GitHub Pages. The consumer is the "Open Data Charts"
tab of signalk-charts-provider-simple. **Not for navigation**: every chart,
the index and the docs say so.

## Layout

- `regions/<id>.json`: a region's bbox, its sources, its bathymetric
  models (`dem`, none or several: Shom prepackages, or
  `{"source": "emodnet-hr", "dtms": [...]}` for EMODnet's high-resolution DTMs (each an `id` and its `cells_per_degree`) on top of it, the later on top, or
  `{"source": "emodnet"}` for the EMODnet DTM over the region (`"microtidal": true` charts its shore cells, ports included, as unknown depth where no drying bank can be), or
  `{"source": "ddm"}` for Denmark's Depth Model on LAT with EMODnet beyond it and where no LAT separation reaches it, or
  `{"source": "vaklodingen"}` for Rijkswaterstaat's Dutch coastal bathymetry the same way, or
  `{"source": "bsh"}` for BSH's seabed model of German waters, moved from NHN to LAT, on top of the region's earlier models (or EMODnet without them), or
  `{"source": "wsv"}` for WSV's 1 to 5 m models of the Ems, Jade, Weser and Elbe estuaries (DGM-W), cut to where LAT is the chart datum and moved there the same way, no data beyond them so the mosaic lays them over the region's other models, or
  `{"source": "kartverket"}` for Kartverket's Norwegian seabed model, kept on sjøkartnull (Norway's chart datum, LAT or up to 0.3 m below), with EMODnet beyond it, or
  `{"source": "infomar"}` for INFOMAR's Irish bathymetry on LAT, its 25 m and 10 m grids on top of the region's EMODnet, or
  `{"source": "icgc"}` for ICGC's 1 m topobathymetric model of the Catalan coast, mirrored in the release `icgc` and kept on mean sea level, beside the region's EMODnet, or
  `{"source": "ausseabed"}` for Geoscience Australia's 30 m AusBathyTopo models of northern Australia, the Torres Strait, the Great Barrier Reef and Bass Strait and the NSW 5 m marine lidar and multibeam model (mirrored in the release `nsw-bathymetry`), each read by range out of its zip and, if projected, warped onto the chart grid, with `"national": true` the 250 m national model under them, moved from mean sea level to LAT with AIMS' lowest predicted tide, only where they reach water shallower than 300 m), its OpenStreetMap extracts (`osm`, each a
  list of alternative URLs; their water areas are cut out of the land, and with `openseamap` in its sources their seamarks are charted too) and the attribution of its own sources. Adding a
  region starts here; no unit may belong to two regions (tested).
- `odc/attribution.py`: the shared attribution, Shom's for regions built
  from Shom data, plus each region's, with the dates some licences ask for
  (`{name}` in a region's attribution, from the inputs' `dataset-dates.json`).
- `odc/grid.py`: the global 0.000125° chart grid, the z9 units and
  `MAX_ZOOM` (17 for every unit). Every grid computation happens on this
  grid, so neighbouring units agree on their shared edge.
- `odc/shom.py`: Shom's download services (footprints, listings, ranged
  downloads over a few connections).
- `odc/reduce_survey.py`: one survey's XYZ (streamed out of its 7z) to the
  shoalest sounding per grid cell.
- `odc/mirror.py`: the survey mirror, one reduced `.npz` per survey,
  named `<survey>.<md5-12>.npz`, in the draft releases `mirror-0` to
  `mirror-f` by the md5's first digit (GitHub allows 1000 assets per
  release); the draft release `mirror` holds the index.
- `odc/seamarks.py`: aids to navigation from OpenStreetMap's `seamark:*`
  tags (OpenSeaMap), as S-57 objects, for regions listing `openseamap`
  (those without Shom's WFS).
- `odc/sources.py`, `odc/osm.py`: a region's other inputs (Shom WFS, the
  region's bathymetric models from Shom or EMODnet, OpenStreetMap, New Caledonia's reefs and
  structures, the Allen Coral Atlas's reefs and satellite depths, Copernicus DEM, ETOPO 2022
  for deep water).
- `odc/lat_model.py`: a national model moved from its datum (mean sea
  level, NAP) to LAT with the national surfaces of both above the
  ellipsoid (Denmark's DKMSL and DKLAT, NSGI's NLGEO2018 and NLLAT2018, BKG's GCG2016);
  where they give no separation, the EMODnet fill stands instead of a depth
  on another datum. A model already on its chart datum (Kartverket's) is written the
  same way without a separation.
- `odc/chart.py`: chart objects as published vector layers (LINZ's New
  Zealand Hydro layers, listed in `odc/linz_hydro_layers.json`, for regions
  with `linz-hydro`), each chart scale from its zoom and giving way to a
  larger scale where that one has coverage. Such a unit takes its depths
  from the chart, not from a model.
- `odc/aca_depth.py`: an Allen Coral Atlas download package's satellite-derived
  bathymetry, cut into 1-degree squares on the chart grid for the release
  `aca-depth`.
- `odc/units.py`: which units a region has (those with land or model data)
  and each unit's version, a hash of the builder and of every input reaching
  the unit's build box.
- `odc/build_unit.py`: one unit's S-57-shaped GeoJSON layers, built on the
  unit plus a margin (`Unit.build_box`).
- `odc/tile.py`: tippecanoe, cut to the unit's bounds, `type=S-57`.
- `odc/hillshade.py`: the unit's terrain shading from the Copernicus DEM, a
  second, raster MBTiles (z9 to z12, WebP) shaded per zoom on the global
  Web-Mercator pixel grid.
- `odc/run_unit.py`: builds and tiles the units of one CI job.
- `odc/index.py`: the published index, coverage and the retirement ledger.
- `schema/open-data-index.schema.json` and `docs/index-format.md`: the
  index contract. `docs/pipeline.md`: pipeline notes and measurements.
- `builder/Dockerfile`: the build runtime, the charts-toolbox image
  (GDAL Python, numpy, tippecanoe) plus 7z and osmium.

## Workflows

- `mirror-surveys.yml` (weekly): reduces new or changed Shom surveys into
  the `mirror` release.
- `build-units.yml` (daily, one region a day in turn, once all of the
  region's surveys are mirrored): plans the units whose version changed, builds
  them in a matrix and, when publishing is on (`ODC_PUBLISH` or a manual
  run with `publish`), uploads them to `units-<region>` and deploys the
  index to Pages.
- `aca-depth.yml` (manual, with the link of an Allen Coral Atlas download
  package): adds the package's satellite depths to the `aca-depth` release.
- `builder-image.yml`: builds the builder image. `ci.yml`: lint and tests.

Scheduled runs are no-ops when nothing changed. A run that stops halfway
must never leave an index that lists an asset missing from the release:
assets go up first, the index after, and retired assets are deleted only
14 days after the served index stopped listing them.

## Rules that keep units seamless

- Anything computed per cell must be the same in two units that both see
  the cell: work on `Unit.build_box`, keep windows, averaging blocks and
  thinning squares on global positions, never relative to a box origin.
- A unit only emits point features it owns (`Unit.owns`), and tippecanoe
  clips everything else to the unit's bounds.
- A unit's version covers everything it is built from. When a change to
  the builder or an input changes the output, it must change the version,
  or boats keep the old unit.

## Index contract

The index is read by plotters on boats that update rarely. Follow the
compatibility policy in `docs/index-format.md`: additive changes keep
`schemaVersion`, consumers skip what they cannot read. Change the schema
file and the doc with the code.

## Outside services

Requests carry the generic User-Agent
`open-data-charts/<version> (+https://github.com/dirkwa/open-data-charts)`
and no personal data. Use only sources whose licence allows redistribution;
Shom's RasterMarine charts are not used.

## Commands

- `ruff check` and `ruff format --check`: lint (CI).
- `docker build -f builder/Dockerfile -t odc-builder .` then
  `docker run --rm -v "$PWD:/src" -w /src odc-builder python3 -m pytest -q`:
  tests (GDAL comes from the image).

## Code style

- Code explains itself. A comment says why, never what; no docstrings for
  their own sake.
- Docs describe the code as it is now; history belongs in commits and PRs.

## Workflow

- Conventional commits and PR titles: `<type>(<scope>): <subject>`.
- One logical change per PR. Branch names `feat/…`, `fix/…`, `chore/…`.
- Rebase on `main`, never merge it in. PRs are squash-merged.
- No AI attribution in commits or PRs.
