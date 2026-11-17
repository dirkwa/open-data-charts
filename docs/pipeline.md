# Pipeline

## Survey mirror

`mirror-surveys.yml` keeps every Shom survey of the configured regions as a
reduced `.npz` (the shoalest sounding per grid cell, `odc.reduce_survey`) in
the draft release `mirror`. Only new surveys, or those whose source md5
changed, are downloaded and reduced again.

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
marks the water as unsurveyed.
