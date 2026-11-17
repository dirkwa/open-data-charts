# AGENTS.md

Guidance for AI coding agents and human contributors. `CONTRIBUTING.md` is
the shorter walk-through for people.

## What this is

A data pipeline: GitHub Actions build vector nautical charts from open data
(Shom, Gouvernement de la Nouvelle-Calédonie, OpenStreetMap, Copernicus) as
one MBTiles per *unit*, a z9 Web-Mercator tile, and publish them as release
assets with an index on GitHub Pages. The consumer is the "Open Data Charts"
tab of signalk-charts-provider-simple. **Not for navigation**: every chart,
the index and the docs say so.

## Layout

- `regions/<id>.json`: a region's bbox, its sources and their download
  details. Adding a region starts here.
- `odc/grid.py`: the global 0.000125° chart grid, the z9 units and
  `MAX_ZOOM` (17 for every unit). Every grid computation happens on this
  grid, so neighbouring units agree on their shared edge.
- `odc/shom.py`: Shom's download services (footprints, listings, ranged
  downloads over a few connections).
- `odc/reduce_survey.py`: one survey's XYZ (streamed out of its 7z) to the
  shoalest sounding per grid cell.
- `odc/mirror.py`: the survey mirror, one reduced `.npz` per survey in the
  draft release `mirror`, named `<survey>.<md5-12>.npz`.
- `odc/sources.py`, `odc/osm.py`: a region's other inputs (Shom WFS, the
  Shom-IRD DEM, OpenStreetMap, the Gouvernement's reefs and structures,
  Copernicus DEM).
- `odc/units.py`: which units a region has and each unit's version, a hash
  of the builder and of every input reaching the unit's build box.
- `odc/build_unit.py`: one unit's S-57-shaped GeoJSON layers, built on the
  unit plus a margin (`Unit.build_box`).
- `odc/tile.py`: tippecanoe, cut to the unit's bounds, `type=S-57`.
- `odc/run_unit.py`: builds and tiles the units of one CI job.
- `odc/index.py`: the published index, coverage and the retirement ledger.
- `schema/open-data-index.schema.json` and `docs/index-format.md`: the
  index contract. `docs/pipeline.md`: pipeline notes and measurements.
- `builder/Dockerfile`: the build runtime, the charts-toolbox image
  (GDAL Python, numpy, tippecanoe) plus 7z and osmium.

## Workflows

- `mirror-surveys.yml` (weekly): reduces new or changed Shom surveys into
  the `mirror` release.
- `build-units.yml` (weekly): plans the units whose version changed, builds
  them in a matrix and, when publishing is on (`ODC_PUBLISH` or a manual
  run with `publish`), uploads them to `units-<region>` and deploys the
  index to Pages.
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
