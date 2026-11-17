# Open Data Charts

Vector nautical charts built from open data, for
[Freeboard-SK](https://github.com/SignalK/freeboard-sk) and other Signal K
chart plotters.

> **Not for navigation.** These charts are compiled automatically from open
> data sets that their publishers themselves mark "not for navigation"
> (Shom: *ne pas utiliser pour la navigation / product not certified for
> navigation*). They are no substitute for official charts.

## What it is

GitHub Actions turn open survey data, seamarks and coastlines into small
S-57-shaped vector MBTiles, one per *unit* (a z9 Web-Mercator tile, about
0.7° wide). The units are published as release assets and listed in an
index on GitHub Pages, with their coverage, data quality, version and
checksum. The "Open Data Charts" tab of
[signalk-charts-provider-simple](https://github.com/dirkwa/signalk-charts-provider-simple)
reads that index, lets the user pick units on a map, downloads only those
units (and on updates only the units that changed) and assembles them into
one chart on the boat.

The first region is New Caledonia, from Shom open data (surveys, seamarks,
the Shom-IRD bathymetric model), the Gouvernement de la Nouvelle-Calédonie's
reef map and coastal structures, OpenStreetMap and the Copernicus DEM.

## Using the charts

Install signalk-charts-provider-simple on a Signal K server, open its
"Open Data Charts" tab, select the units you want on the map and download
them as a chart set. The tab checks the index for changed units and updates
only those.

The index is public, so any other plotter can use it too:

    https://dirkwa.github.io/open-data-charts/open-data-index.json

Its format and compatibility policy are in
[docs/index-format.md](docs/index-format.md), with a JSON Schema in
[schema/](schema/open-data-index.schema.json).

## How it is built

- Every week a workflow mirrors the region's Shom surveys, each reduced to
  the shoalest sounding per cell of one global grid.
- Every week another workflow works out which units have new inputs,
  builds only those, uploads them to the region's release and republishes
  the index. A unit's version is a hash of everything it is built from, so
  an unchanged unit is never rebuilt or downloaded again.
- Units are built on a shared grid with a margin around each one, so
  neighbouring units meet without seams.

[docs/pipeline.md](docs/pipeline.md) has notes and measurements, and
[AGENTS.md](AGENTS.md) the code.

## Contributing

New regions and open data sources are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licences

The code is Apache-2.0 (`LICENSE`). The published charts carry the licences
and attribution of their sources, listed in `LICENSE-DATA.md`.
