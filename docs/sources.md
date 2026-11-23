# Data sources by region

Where each region's depths, seamarks, land and terrain come from, and the
datum its depths are on. Each region is a file in [`regions/`](../regions/);
the licences and attribution are in [`LICENSE-DATA.md`](../LICENSE-DATA.md).

The regions are mainland France (the Channel and North Sea coast, the
Atlantic coast, the Mediterranean coast and Corsica) and the French overseas
territories with Shom open data: New Caledonia, French Polynesia, Wallis and
Futuna, Réunion, Mayotte, Guadeloupe, Martinique, Saint-Martin and
Saint-Barthélemy, French Guiana and Saint-Pierre and Miquelon. Each is built
from Shom's surveys, seamarks and, where Shom publishes one, its bathymetric
model of the coast, together with OpenStreetMap and the Copernicus DEM.
Every unit with land comes with terrain shading from the Copernicus DEM, a
separate layer the plotter can show over the chart or leave off.
Shom publishes Corsica's model on mean sea level rather than on the lowest
astronomical tide; the tide there ranges over a few tens of centimetres.
New Caledonia adds the Gouvernement de la Nouvelle-Calédonie's reef map and
coastal structures, and the tropical regions the Allen Coral Atlas's reef
map, which charts reef flats, crests and shallow lagoons where no finer reef
map has them. Both are traced from satellite images, so their outlines are
approximate: a reef flat is charted as covering and uncovering except where
a survey or model shows 2 m of water or more over it, which is charted as a
reef always under water. In the units charted for their land or model, water that the
atlas's satellite-derived bathymetry covers and no survey or model does is
charted in coarse depth bands (0-2, 2-5 and over 5 m) without soundings or
contours. The bands are set shallower than the satellite reads: against
Shom's soundings in the Tuamotus its median reads 1 to 4 m too deep,
depending on the depth, and single readings differ more. The index marks
the water as satellite-derived.

The United Kingdom (the Isles of Scilly and Land's End, England and Wales
with the Irish Sea, and Scotland with Northern Ireland and North East
England) is built for coasts without Shom data: its depths come from the
EMODnet Bathymetry DTM (a model at about 115 m). Ireland (its Atlantic
coast from Kerry to Mayo, and Cork, the Shannon Estuary and Galway Bay; its
east and north coasts lie in the two UK regions) and those UK regions take
their depths in Irish waters from INFOMAR's surveys, gridded at 10 m inshore
and 25 m beyond, on LAT. The Dutch Wadden
Sea takes its depths from Rijkswaterstaat's coastal and Wadden surveys
(the latest measured bed, gridded at 20 m), moved from NAP to LAT with
NSGI's NLGEO2018 and NLLAT2018 surfaces, and from EMODnet beyond them.
Denmark takes its depths from the Danish Geodata Agency's Denmark's Depth
Model (50 m), moved from mean sea level to LAT with the agency's DKMSL and
DKLAT surfaces, and from EMODnet beyond it and where no LAT separation
reaches. The German North Sea and Baltic, and the German waters inside the
Denmark region, take theirs from BSH's seabed model (50 m), moved from NHN
to LAT with BKG's GCG2016 and the Danish and Dutch LAT surfaces. In the
German North Sea, BSH's model lies over Rijkswaterstaat's grid around the
Ems, and WSV's models of the Ems, Jade, Weser and outer Elbe estuaries
(DGM-W, 1 to 5 m) lie on top of both, kept to where LAT is the chart datum:
seaward of Emden, Bremerhaven and Cuxhaven. EMODnet fills beyond them.
Panama has no open national depth data: its reefs (San Blas, Bocas del Toro,
Las Perlas) come from the Allen Coral Atlas, its coast and seamarks from
OpenStreetMap and OpenSeaMap, and its deep water from ETOPO; the atlas's
satellite-derived depths join once they are published for it. Southern Norway, from the Oslofjord to Stad, takes its depths from Kartverket's seabed terrain model (50 m) on sjøkartnull, the datum of Norway's own charts: LAT, except 0.2 m below LAT from the Swedish border to Utsira and 0.3 m below it in the inner Oslofjord, where depths read that much shallower than on LAT; EMODnet fills beyond it. England's south coast lies in the Channel region and
takes its depths from Shom's model there. The rest of the Mediterranean, from Gibraltar to the Levant, is built the
same way from the EMODnet DTM alone, in nine regions by sea area, and so
are Atlantic Iberia (Galicia, Portugal and the Gulf of Cadiz to Huelva),
the Canary Islands, Madeira and the Azores. In Portugal, Madeira and the
Azores, the Instituto Hidrográfico's high-resolution models from EMODnet
(surveys of ports and estuaries, and the coastal lidar, 14 to 29 m, on
LAT) lie on top of the DTM where they reach. Along the Catalan coast, out to
about 50 m, the western Mediterranean takes its depths from ICGC's
topobathymetric model (lidar and multibeam, 1 m) instead, each chart cell
keeping the model's shoalest value. ICGC's model is on mean sea level, which
lies a decimetre or two above the chart datum that EMODnet's Mediterranean
depths are on.
Australian waters, from the Kimberley and the Top End to the Torres Strait
and the Great Barrier Reef down to the Gold Coast, take their depths from
Geoscience Australia's AusBathyTopo models (30 m, compiled from the
Australian Hydrographic Office's multibeam, singlebeam and lidar surveys,
research cruises and satellite-derived bathymetry: Northern Australia 2018,
Great Barrier Reef 2020, Torres Strait 2023, Bass Strait 2022), moved from
mean sea level to LAT with AIMS' lowest predicted tide from the EOT20 tide
model. New South Wales, from the Gold Coast to Eden, adds the NSW
government's 5 m model from marine lidar and multibeam surveys, which covers
the shore, the harbours and estuaries and the first few kilometres out. New
South Wales, Victoria and Tasmania lay these over Geoscience Australia's
national 250 m model of 2026, which fills the water between the coastal
models and the shelf edge. Chunks with no water shallower than 300 m are
left to ETOPO.
Outside Shom's waters and New Zealand, buoys,
beacons, lights and other aids to navigation come from OpenStreetMap's
seamark tags (the OpenSeaMap scheme), which volunteers keep and which can
lag behind changes at sea. New Zealand is charted from LINZ's chart data
(the New Zealand Hydrographic Authority's Hydro layers, as of April 2023):
its depth areas, contours, soundings, aids, hazards and areas come from the
charts themselves, each chart scale from its own zoom, with land and
buildings from OpenStreetMap.

Where no survey sounding is near, depth numbers come from the bathymetric
model's own pixels, the shoalest of each area thinned per zoom like the
surveys' soundings but at most one per zoom-14 square (about 230 m wide at
55 degrees); each says in its INFORM that it is a model depth, not a
surveyed sounding. Satellite bands and ETOPO's deep water carry none.

Everywhere, deep water (200 m and more) that no survey or finer model
covers takes its depths from NOAA's ETOPO 2022 global model (about 460 m),
kept well clear of reefs and shores. The index marks it as a coarse model.
