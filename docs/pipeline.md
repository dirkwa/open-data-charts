# Pipeline

## Survey mirror

`mirror-surveys.yml` keeps every Shom survey of the configured regions as a
reduced `.npz` (the shoalest sounding per grid cell, `odc.reduce_survey`).
GitHub allows 1000 assets per release, so the surveys are spread over the
draft releases `mirror-0` to `mirror-f` by the first digit of their source
md5, and the index records each survey's release. The draft release `mirror`
holds the index, the footprints and the surveys reduced before the split,
until a new source replaces them. Only new surveys, or those whose source
md5 changed, are downloaded and reduced again.

### Measured: New Caledonia, first full mirror (2026-11-16)

One run of `odc.mirror reduce` over the whole region on a 4-CPU machine
(one survey per CPU, 2 download connections per survey):

| | |
|---|---|
| Surveys | 332 |
| Source 7z downloaded | 5.49 GB |
| Soundings read | 773 million |
| Reduced `.npz` | 174 MB (3 % of the source) |
| Wall time | 24 min |
| Sum of per-survey times | 95 min |
| Longest survey | S202200100: 552 MB source, 104 million soundings, 10 min |

Shom serves about 9 MB/s to one client, so downloads and reduction overlap.
The reader used here was replaced by one about 2.5 times faster on the same
survey (S202000400: 97 s before, 39 s after). On GitHub's 4-CPU runners the
whole region fits in one job; the shard limit (12 GiB of source per job)
only matters for the large metropolitan regions.

## Units

`build-units.yml` builds one region a day, taking the files in `regions/` in
turn, and skips a region until every survey of it is in the mirror. A
region without `shom-surveys` in its sources (an EMODnet region) is not in
the mirror and builds at once. A region has
the units of its bbox that are not all land and that hold land or data in
the region's bathymetric model. A survey footprint alone does not qualify a
unit: old ocean crossings would add hundreds of open-ocean units to French
Polynesia with a sounding every few miles. Where neither a survey nor a model gives a depth, the unit
charts the satellite-derived depth bands where they cover the water (see
*Satellite depths*), and marks the rest as unsurveyed.

## Seamarks

Regions without Shom's WFS take their aids to navigation from
OpenStreetMap's `seamark:*` tags when `openseamap` is in their sources
(`odc/seamarks.py`). The tagging scheme mirrors S-57, so each object maps
onto the class and attributes Shom's WFS gives French waters; its lights
(one per sector), topmark, daymark, fog signal and racon become objects of
their own at its position, as in an ENC. Mooring posts, dolphins and
bollards are left out: in the Dutch extracts they are about 7,500 of 9,000
moorings and line every quay and canal, so only mooring buoys are kept. In
the Wadden unit z9-263-166 (Harlingen, Vlieland) this gives 876 lateral
buoys, 265 lights, 497 topmarks and some 460 other aids.

A region with both, the Channel with England's south coast, charts Shom's
aids and OpenStreetMap's on the other coast: an OSM aid within a nautical
mile of a Shom aid is left out as one Shom already charts. OSM wrecks,
rocks and other dangers are kept wherever they are. Shom's WFS stops at
French waters, so the English coast keeps its OSM marks.

## Satellite depths

The Allen Coral Atlas publishes satellite-derived bathymetry only in
download packages a person requests for an area. `aca-depth.yml` takes the
link the atlas mails and cuts the package into 1-degree squares on the chart
grid (`odc/aca_depth.py`): each cell keeps the shoalest depth inside it, in
whole metres. The atlas maps whole atolls and the water around them, so a
square over an atoll group is about 25 MB. The
squares are assets of the release `aca-depth`, and `odc.sources` fetches
those under a region's units for every region with `allen-coral-atlas` in its
sources. A run uploads each square as `next-<square>` and renames it over
the old one once it is complete; the next run finishes a rename a stopped
run left. Runs queue, each merging into what the last one published.

The builder bands and sieves a square as a whole (`square_bands`), never a
unit's box, so neighbouring units agree on every cell, and a unit's version
hashes every square it meets. Specks under 6 cells take the band around
them, and patches of the deepest band under 50 cells the band above it, or
no depth where only water without satellite depth surrounds them.

### Measured: Tahanea, Tuamotus (2026-11-18)

Against 34,598 Shom soundings of Tahanea, the satellite depth reads deeper
than the survey: by a median of 1.2 m at 2-5 m, 2.5 m at 5-10 m and 3.9 m at
10-15 m, and it stays at about 18 m over water of 60 m and more. Per
satellite depth, the 5th percentile of the true depth is about 1.5 m below
4 m, 3 m at 4-8 m, 5 m at 8-16 m and 11 m beyond. The builder's bands
(`SATELLITE_BANDS`) start there: 0-2 m, 2-5 m and over 5 m. A fourth band
over 10 m was tried and dropped, since around the 10 m line the satellite's
noise drew thousands of specks and nearly tripled a unit's size.
