# Pipeline

## Survey mirror

`mirror-surveys.yml` keeps every Shom survey of the configured regions as a
reduced `.npz` (the shoalest sounding per grid cell, `odc.reduce_survey`).
GitHub allows 1000 assets per release, so the surveys are spread over the
draft releases `mirror-0` to `mirror-f` by the first digit of their source
md5, and the index records each survey's release. The draft release `mirror`
holds the index, the footprints and the surveys reduced before the split,
until a new source replaces them. Only new surveys, or those whose source
md5 changed, are downloaded and reduced again. A run takes at most
`MAX_SHARDS` shards (about 290 GB) and leaves the rest to the next run, so a
large first fill, such as Litto3D's, does not hold the unit builds back for
days; the regions it reaches build once their surveys are all mirrored.

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
region without `shom-surveys`, `bsh-surveys`, `ausseabed-surveys` or `vta-his-depth` in its
sources (an EMODnet region) is not in the mirror and builds at once. A region has
the units of its bbox that are not all land and that hold land or data in
the region's bathymetric model. A survey footprint alone does not qualify a
unit: old ocean crossings would add hundreds of open-ocean units to French
Polynesia with a sounding every few miles. Where neither a survey nor a model gives a depth, the unit
charts the satellite-derived depth bands where they cover the water (see
*Satellite depths*), and marks the rest as unsurveyed.

## Overviews

`build-overview.yml` (weekly, one sea a week in turn, or by hand) makes a
sea's overview (`odc/overview.py`) from the units the index publishes for
the regions in `overviews/<id>.json`. A unit's z9 tile is already the unit
at its coarsest, with every source's priority applied, so the overview
downloads each unit, decodes that one tile, cuts it back to the unit (a
tile reaches a buffer into its neighbours), keeps the classes a passage
plan needs and tiles them again at zooms 4 to 8. Each unit's file is
deleted once read. The run is a no-op while every region of the sea lists
the overview's version, a hash of its units' versions and of the code.

### Measured: 46 Danish and German units (2026-11-26)

The overview of 46 units is 8.8 MB, 1.6 MB of it z4 to z6, in 31 s on a
4-CPU machine, downloads included. No seam shows at the unit edges.

## Land and inland water

The land comes from OpenStreetMap's land polygons, which follow the
coastline only: a harbour behind a lock, a canal, a river past its mouth
or a lake is land in them. Every OSM water area (`natural=water`,
`waterway=riverbank` or `dock`, `landuse=basin` or `reservoir`) is cut out
of that land, so harbours, canals, rivers, lakes and even ditches chart as
water with a shoreline. No survey reaches them, and the bathymetric
model's cells there mostly see the land around a narrow canal: inside them
the model is kept only below chart datum, so a canal never charts as a
drying bank. The rest is unsurveyed.

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

Traffic separation schemes, restricted and military areas, wind farms
(`production_area`), anchorages, dumping grounds, fishing gear, marine
farms, submarine cables and pipelines keep their outline or line, cut to
the unit, since on open water a passage is planned around them. In the
Schleswig-Holstein extract around Kiel Bay this adds 151 restricted areas,
14 dumping grounds, 13 fishing facilities, 10 cables and the Kiel
lighthouse TSS. Where Shom or a published chart already charts an object
of the same class, that part of the OSM one, with about 200 m around it,
is cut out. The cut is decided point by point, so two units that share a
cell agree on it; a cut by the whole object would depend on what each
unit's build box holds. A chart object shown at some zooms only cuts
nothing.

A region with both, the Channel with England's south coast, charts Shom's
aids and OpenStreetMap's on the other coast: an OSM aid within a nautical
mile of a Shom aid, inside the unit or just beyond it, is left out as one
Shom already charts. OSM wrecks, rocks and other dangers are kept wherever
they are. Shom's WFS stops at French waters, so the English coast keeps its
OSM marks.

## Offshore structures and wrecks

European regions with `emodnet-human-activities` in their sources chart
EMODnet Human Activities' wind farms, offshore installations, pipelines,
power and telecom cables, military areas and dumped munitions, and UKHO's
worldwide wreck list, which EMODnet republishes (`odc/human_activities.py`).
Planned and dismantled wind farms, cables and pipelines planned,
proposed, approved, applied for, dismantled or removed, planned or
deactivated military areas and lifted wrecks are left out; wind farms,
cables, pipelines and platforms under construction are charted as such
(`CONDTN`). Abandoned or out-of-use cables and pipelines and decommissioned
or closed-down platforms stay charted: EMODnet does not say whether they
were removed, and one left in place fouls an anchor or a keel. A wreck's depth is charted only where it is
given on a low-water datum (LAT, MLWS, LLW); one on mean sea level keeps
its symbol without a depth. Shom's or a published chart's object of the
same class stands over EMODnet's, and EMODnet's over OpenStreetMap's: what
is charted in the same class is cut out of an outline, as for seamarks, and
a wreck within 150 m of a charted one is left out. Between Bornholm and Gotland (54-58° N,
14-21° E) this gives 1,394 wrecks, 23 military areas in force and the
wind farms in production or under construction.

The same source charts lighthouses (the Amateur Radio Lighthouse Society's
list, as light towers), fish and shellfish farms at sea (`MARCUL`), dredge
spoil grounds (`DMPGRD`, once per site though EMODnet lists every year of
use), aggregate extraction areas in use (`CTNARE`) and oil and gas wells
that keep a wellhead (active or suspended, as wellhead obstructions; an
abandoned well is plugged and cut below the seabed). Removed lighthouses,
farms on land and expired or exploration areas are left out. EMODnet's
lighthouse and farm positions are often approximate, so one gives way to
a light or landmark another source charts within 500 m, or a farm within
300 m or inside its outline, OpenSeaMap's included. Ocean energy test
devices are not charted: the list holds finished test projects. For the
southern Baltic (`baltic-south`) this adds 78 lighthouses, about 800 spoil
grounds and munitions dumps, 14 extraction areas and 33 standing wells; on
the Norwegian coast (`norway-south`) 1,225 fish and mussel farms.

An installation that stands above the water and is still there gets the
500 m safety zone that UK, Norwegian, Dutch and Danish law put round it,
drawn from its position as a restricted area (`RESARE`, `CATREA` 1, entry
prohibited) whose `INFORM` says it is not from a chart. Decommissioned and
closed-down platforms keep it, since the zone lasts while the structure
stands. Subsea structures and buoys get none. A unit's version covers the
platforms within a zone's reach of its build box. In the North Sea
(51-62° N, 4° W-12° E) EMODnet lists 1,212 installations, and 879 of them
get a zone.

## Satellite depths

The Allen Coral Atlas publishes satellite-derived bathymetry only in
download packages a person requests for an area. `aca-depth.yml` takes the
link the atlas mails, downloads and unpacks the package on the runner and
cuts it into 1-degree squares on the chart
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

## Terrain shading

Each unit with land also gets a raster MBTiles of its relief
(`odc/hillshade.py`), from the same Copernicus DEM tiles the land contours
come from. Every zoom from 9 to 12 is shaded on its own Web-Mercator pixel
grid, with one pixel of margin for the slope at the edge: the DEM is
averaged into the pixels below z12 and interpolated at z12, so a pixel is
the same whichever unit computes it and units meet without a seam. The
shading is masked to the land polygons; the DEM is a surface model, flat
over water, but its coastal pixels would shade into the sea.

The light comes from the north-west at 45°. Shadows are black up to 75 %
opacity, lit slopes white up to 35 %, so the layer works over a day palette
and, at lower opacity, over a night one.

### Measured: Nouméa, z9-492-288 (2026-11-20)

50 tiles (1, 3, 10 and 36 at z9 to z12), 1.4 MB as WebP at quality 75,
built in under 2 s. A unit that is mostly land is several times that.
