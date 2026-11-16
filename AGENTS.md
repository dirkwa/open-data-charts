# AGENTS.md

Guidance for AI coding agents and human contributors.

## What this is

A data pipeline: GitHub Actions build vector nautical charts from open data
(Shom, Gouvernement de la Nouvelle-Calédonie, OpenStreetMap, Copernicus) as
one MBTiles per *unit*, a z9 Web-Mercator tile, and publish them as release
assets with an index on GitHub Pages. The consumer is the "Open Data Charts"
tab of signalk-charts-provider-simple. **Not for navigation**: every chart,
the index and the docs say so.

## Layout

- `odc/grid.py`: the global 0.000125° chart grid and the z9 units. Every
  grid computation happens on this grid, so neighbouring units agree on
  their shared edge.
- `odc/reduce_survey.py`: one survey's XYZ (streamed out of its 7z) to the
  shoalest sounding per grid cell.
- `odc/build_unit.py`: one unit's S-57-shaped GeoJSON layers, built on the
  unit plus a margin (`Unit.build_box`).
- `odc/tile.py`: tippecanoe, cut to the unit's bounds, `type=S-57`.
- `builder/Dockerfile`: the build runtime, the charts-toolbox image
  (GDAL Python, numpy, tippecanoe) plus 7z and osmium.

## Rules that keep units seamless

- Anything computed per cell must be the same in two units that both see
  the cell: work on `Unit.build_box`, keep windows, averaging blocks and
  thinning squares on global positions, never relative to a box origin.
- A unit only emits point features it owns (`Unit.owns`), and tippecanoe
  clips everything else to the unit's bounds.

## Commands

- `ruff check` and `ruff format --check`: lint (CI).
- `docker build -f builder/Dockerfile -t odc-builder .` then
  `docker run --rm -v "$PWD:/src" -w /src odc-builder python3 -m pytest -q`:
  tests (GDAL comes from the image).

## Workflow

- Conventional commits and PR titles: `<type>(<scope>): <subject>`.
- One logical change per PR. Branch names `feat/…`, `fix/…`.
- No AI attribution in commits or PRs.
