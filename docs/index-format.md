# Open Data Charts index

The pipeline publishes one index of every chart unit on GitHub Pages:

    https://dirkwa.github.io/open-data-charts/open-data-index.json

with its JSON Schema at `schema/open-data-index.schema.json` (also served
next to the index as `open-data-index.schema.json`). Consumers fetch it with
`If-None-Match` (Pages sends an ETag) and keep the last good copy for offline
use.

**Not for navigation.** Every unit, this index and every consumer UI must
say so.

## Compatibility policy

- Additive changes (new optional fields, new quality categories, new
  regions) keep `schemaVersion`. Consumers ignore fields they do not know.
- A consumer skips a unit or region entry it cannot read, never the whole
  file.
- `schemaVersion` changes only for a change existing consumers would read
  wrongly. A consumer that sees a higher major version than it knows shows
  the index as unsupported and keeps its installed charts.

## Units

A unit is the z9 Web-Mercator tile `x`, `y` (XYZ scheme, y from the north),
about 0.7° wide. Its MBTiles holds tiles z9 to `maxzoom` (17 for every
unit; a set never mixes max zooms, as plotters do not overzoom a missing
vector tile) that all lie inside that tile, so no tile at z ≥ 9 belongs to two units. A chart set
on the boat is the union of its units' `tiles` rows, plus the region
overview's rows for z < 9 when the index has one. An overview covers a whole
sea (`overviews/<id>.json`), so the regions of one sea list the same file:
a set takes it once. A set with regions of two seas takes both overviews;
near the seas' edge both hold the same z4 to z8 tiles, so a tile in both is
merged, its layers' features joined, rather than one replacing the other. Serve a set as that one chart, not as
one chart per unit: a renderer draws a label from the tile that holds its
point and clips each chart to its own bounds, so labels next to a unit edge
would be cut in half.

The unit MBTiles is a normal vector MBTiles (`format=pbf`, `type=S-57`,
TMS row order in `tiles`), one MVT layer per S-57 object class. Its
metadata also carries `x-odc-unit` (the unit id) and `x-odc-version`.

## File

```jsonc
{
  "schemaVersion": 1,
  "generated": "2026-12-01T03:00:00Z",
  "disclaimer": "Not for navigation. …",
  "attribution": "Shom, Licence Ouverte Etalab 2.0; …; (c) OpenStreetMap contributors, ODbL; …",  // every region's sources
  "license": "odbl-1.0",                    // licence of every unit file as a whole
  "licenses": [
    { "id": "etalab-2.0", "name": "Licence Ouverte Etalab 2.0", "url": "https://…", "attribution": "Shom, <year>" }
  ],
  "regions": [
    {
      "id": "new-caledonia",
      "name": "New Caledonia",
      "bbox": [158.0, -26.0, 172.0, -17.0],
      "overview": null,                       // or { "url", "sha256", "size", "version", "minzoom", "maxzoom" }
      "sources": ["shom-wfs", "shom-surveys", "shom-ird-dem", "osm", "nc-reefs", "nc-oblic", "copernicus"],
      "attribution": "Shom, Licence Ouverte Etalab 2.0; …"   // the attribution of this region's units
    }
  ],
  "units": [
    {
      "id": "z9-492-288",
      "region": "new-caledonia",
      "z": 9, "x": 492, "y": 288,
      "bbox": [165.9375, -22.593726, 166.640625, -21.943046],   // [west, south, east, north]
      "version": "3f9a0c1d2e4b5a67",          // changes when, and only when, the unit's inputs or the builder change
      "url": "https://github.com/dirkwa/open-data-charts/releases/download/units-new-caledonia/z9-492-288.3f9a0c1d2e4b5a67.mbtiles",
      "size": 34567890,                       // bytes
      "sha256": "…",                          // of the file at url
      "minzoom": 9,
      "maxzoom": 17,
      "built": "2026-12-01",
      "quality": {
        "best": "multibeam",
        "share": { "multibeam": 0.18, "survey-recent": 0.22, "survey-old": 0.35, "model": 0.25 }
      },
      "surveys": 41,
      "changes": "Seamarks updated",          // optional, short note for the update badge
      "hillshade": {                          // optional, see Terrain shading
        "url": "https://github.com/dirkwa/open-data-charts/releases/download/units-new-caledonia/z9-492-288.3f9a0c1d2e4b5a67.hillshade.mbtiles",
        "size": 1417216,
        "sha256": "…",
        "minzoom": 9,
        "maxzoom": 12
      }
    }
  ]
}
```

### Quality categories

Shares of the unit's water area, by where its depths come from:

| id | label | meaning |
|---|---|---|
| `multibeam` | Multibeam survey | multibeam or lidar survey ended 2005 or later |
| `survey-recent` | Survey since 1990 | any other survey ended 1990 or later |
| `survey-old` | Survey before 1990 | single-beam or lead-line survey before 1990 |
| `chart` | Official chart | depth areas, contours and soundings taken from a hydrographic office's chart data (LINZ's for New Zealand, the Estonian Transport Administration's soundings) |
| `model` | Model only | no survey; depths from a bathymetric model (100 m or coarser) |
| `satellite` | Satellite-derived | no survey or model; coarse depth bands from satellite-derived bathymetry (Allen Coral Atlas, 10 m pixels, to about 20 m) |
| `model-coarse` | Deep water, coarse model | no survey or finer model; deep water (200 m and more) from a global model at about 460 m |
| `none` | No depth data | neither a survey nor a model; charted as unsurveyed |

`best` is the first of these, in this order, whose share is above zero.
Consumers show an unknown category as "Other".

## Overviews

A region's `overview` is a vector MBTiles like a unit's (`type=S-57`, one
MVT layer per class) with zooms 4 to 8 over every published unit of its
sea, for planning a passage between regions. It holds the land, the depth
areas, unsurveyed areas and every danger, traffic separation scheme,
restricted, military and wind farm area; contours and point dangers from
z6, lights, cables, pipelines and fishing gear from z7. Soundings, buoys
and buildings are left to the units. Its metadata carries `x-odc-overview`
(the sea's id) and `x-odc-version`, which changes when, and only when, one
of its units' versions does. An overview may also draw the part of another
sea's region that faces its own sea (Jutland's west coast in the North Sea
overview); that region's `overview` stays its own sea's. A region indexed for the first time has
`overview: null` until its sea's next overview build; a region whose units
change keeps its overview until then, so an overview may lag its units.

## Terrain shading

A unit with land carries `hillshade`: a raster MBTiles (`format=webp`,
`type=overlay`, 256 px tiles, TMS row order) of the land's relief from the
Copernicus DEM GLO-30, lit from the north-west. Shadows are black and lit
slopes white, each partly transparent; flat ground and water are fully
transparent. A plotter draws it over the chart as its own layer, which the
user can switch off. Like the unit, every tile lies inside the unit's z9 tile,
so a set is the union of its units' rows, served as one layer. Tiles stop at
`maxzoom` (12, the DEM's 30 m); a plotter enlarges z12 tiles beyond it. A
tile without any shading is left out, so a missing tile inside the set is
transparent. The file shares the unit's `version` and is replaced with it.

## Coverage

`coverage.geojson`, next to the index, holds the simplified survey
footprints (properties `region`, `name`, `end` (YYYYMMDD), `category`), for an
optional map overlay. It can be large; fetch it only when shown.

## Assets and versions

Unit files are assets of a region's long-lived releases
`units-<region>-<digit>`, by the first hex digit of the version (GitHub allows
1000 assets per release), named `<unit id>.<version>.mbtiles`, and their
terrain shading `<unit id>.<version>.hillshade.mbtiles`. Units published
before that split stay in `units-<region>` until they are replaced; read each
unit's `url` rather than building it. A new version is
uploaded before the index points to it; the old asset stays at least 14 days,
so a download that started before an update still completes.
