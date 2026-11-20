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

The regions are mainland France (the Channel and North Sea coast, the
Atlantic coast, the Mediterranean coast and Corsica) and the French overseas
territories with Shom open data: New Caledonia, French Polynesia, Wallis and
Futuna, Réunion, Mayotte, Guadeloupe, Martinique, Saint-Martin and
Saint-Barthélemy, French Guiana and Saint-Pierre and Miquelon. Each is built
from Shom's surveys, seamarks and, where Shom publishes one, its bathymetric
model of the coast, together with OpenStreetMap and the Copernicus DEM.
Shom publishes Corsica's model on mean sea level rather than on the lowest
astronomical tide; the tide there ranges over a few tens of centimetres.
New Caledonia adds the Gouvernement de la Nouvelle-Calédonie's reef map and
coastal structures, and the tropical regions the Allen Coral Atlas's reef
map, which charts reef flats, crests and shallow lagoons where no finer reef
map has them. In the units charted for their land or model, water that the
atlas's satellite-derived bathymetry covers and no survey or model does is
charted in coarse depth bands (0-2, 2-5 and over 5 m) without soundings or
contours. The bands are set shallower than the satellite reads: against
Shom's soundings in the Tuamotus its median reads 1 to 4 m too deep,
depending on the depth, and single readings differ more. The index marks
the water as satellite-derived.

The United Kingdom (the Isles of Scilly and Land's End, England and Wales
with the Irish Sea, and Scotland with Northern Ireland and North East
England) is built for coasts without Shom data: its depths come from the
EMODnet Bathymetry DTM alone (a model at about 115 m). The Dutch Wadden
Sea takes its depths from Rijkswaterstaat's coastal and Wadden surveys
(the latest measured bed, gridded at 20 m), moved from NAP to LAT with
NSGI's NLGEO2018 and NLLAT2018 surfaces, and from EMODnet beyond them. Southern Norway, from the Oslofjord to Stad, takes its depths from Kartverket's seabed terrain model (50 m) on sjøkartnull, the datum of Norway's own charts: LAT, except 0.2 m below LAT from the Swedish border to Utsira and 0.3 m below it in the inner Oslofjord, where depths read that much shallower than on LAT; EMODnet fills beyond it. England's south coast lies in the Channel region and
takes its depths from Shom's model there. Outside Shom's waters, buoys,
beacons, lights and other aids to navigation come from OpenStreetMap's
seamark tags (the OpenSeaMap scheme), which volunteers keep and which can
lag behind changes at sea. Each region is a file in `regions/`.

Where no survey sounding is near, depth numbers come from the bathymetric
model's own pixels, the shoalest of each area thinned per zoom like the
surveys' soundings but at most one per zoom-14 square (about 230 m wide at
55 degrees); each says in its INFORM that it is a model depth, not a
surveyed sounding. Satellite bands and ETOPO's deep water carry none.

Everywhere, deep water (200 m and more) that no survey or finer model
covers takes its depths from NOAA's ETOPO 2022 global model (about 460 m),
kept well clear of reefs and shores. The index marks it as a coarse model.

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

- Every week a workflow mirrors the regions' Shom surveys, each reduced to
  the shoalest sounding per cell of one global grid.
- Every day another workflow takes one region in turn, works out which of
  its units have new inputs, builds only those, uploads them to the
  region's release and republishes the index. A unit's version is a hash of everything it is built from, so
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
