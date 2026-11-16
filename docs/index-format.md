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
overview's rows for z < 9 when the index has one. Serve a set as that one chart, not as
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
  "attribution": "Shom, Licence Ouverte Etalab 2.0; …; (c) OpenStreetMap contributors, ODbL; …",
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
      "sources": ["shom-wfs", "shom-surveys", "shom-ird-dem", "osm", "nc-reefs", "nc-oblic", "copernicus"]
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
      "changes": "Seamarks updated"           // optional, short note for the update badge
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
| `model` | Model only | no survey; depths from a bathymetric model (100 m or coarser) |

`best` is the first of these, in this order, whose share is above zero.
Consumers show an unknown category as "Other".

## Coverage

`coverage.geojson`, next to the index, holds the simplified survey
footprints (properties `region`, `name`, `end` (YYYYMMDD), `category`), for an
optional map overlay. It can be large; fetch it only when shown.

## Assets and versions

Unit files are assets of one long-lived release per region
(`units-<region>`), named `<unit id>.<version>.mbtiles`. A new version is
uploaded before the index points to it; the old asset stays at least 14 days,
so a download that started before an update still completes.
