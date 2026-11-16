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

## Licences

The code is Apache-2.0 (`LICENSE`). The published charts carry the licences
and attribution of their sources, listed in `LICENSE-DATA.md`.
