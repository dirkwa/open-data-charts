# Data licences and attribution

The charts published in this repository's releases are built from the
sources below. Each chart's MBTiles metadata (`attribution`) repeats the
attribution of the sources it uses. The code in this repository is licensed
separately (Apache-2.0, see `LICENSE`).

## Licence of the published charts

Each published chart unit is a database derived from OpenStreetMap among
other sources, so every unit file is made available as a whole under the
Open Database License (ODbL) 1.0
(<https://opendatacommons.org/licenses/odbl/1-0/>). The attribution of every
source below still applies and travels with each file. A chart built on the
boat by combining units is a derived database under the same terms.

**Not for navigation.** None of these sources is a certified nautical chart,
and neither are the charts built from them.

## Shom

- **Seamarks, regulations, port information, wrecks, place names**: Shom WFS
  `https://services.data.shom.fr/INSPIRE/wfs`.
  Licence Ouverte / Open Licence Etalab 2.0
  (<https://www.etalab.gouv.fr/licence-ouverte-open-licence/>).
  Attribution: "Shom, <year>".
- **Bathymetric surveys (levés bathymétriques)**: Licence Ouverte Etalab 2.0.
  Attribution per survey: "Shom, bathymetric dataset. DOI: 10.17183/<survey>".
  Every sounding names its survey in `INFORM`. Use limitation: product not
  certified for navigation.
- **MNT bathymétrique de façade de la Nouvelle-Calédonie (LAT)**: Licence
  Ouverte Etalab 2.0. Attribution: "Shom-IRD 2021,
  doi:10.17183/MNT_NC100m_TSUCAL_WGS84".
- **Other bathymetric models of the coast (MNT bathymétriques de façade,
  LAT)**: Licence Ouverte Etalab 2.0. Each region file in `regions/` names
  the models it uses and their attribution, for example "Shom 2016, MNT
  bathymétrique de façade de La Réunion (Projet Homonim),
  doi:10.17183/MNT_REU100m_HOMONIM_WGS84". Every unit's attribution lists
  the models of its region. Corsica's model is the one Shom publishes on
  mean sea level (`_NM`); its lowest-astronomical-tide version comes only
  as BAG, which the builder cannot read.

The scanned Shom paper charts (RasterMarine) are under a commercial licence
and are not used.

## EMODnet Bathymetry

EMODnet Digital Bathymetry (DTM 2024), doi:10.12770/cf51df64-56f9-4a99-b1aa-36b8d7b743a1:
"This data product was created by EMODnet (<https://emodnet.ec.europa.eu/en/>),
and is owned by the EU and licensed under the Creative Commons Attribution
4.0 International (CC BY 4.0) license." Use limitation: do not use for
navigation. Used as the bathymetric model of the regions whose file asks
for it (`"dem": [{"source": "emodnet"}]`), outside Shom's coverage.

## Gouvernement de la Nouvelle-Calédonie

Licence Ouverte (<https://data.gouv.nc/>), via the Géorep services.

- Reef geomorphology (IRD / DINUM), mapped from satellite imagery; outlines
  are approximate (tens of metres).
- OBLIC coastal structures of Nouméa, 2016 (DIMENC / SGNC).

## Allen Coral Atlas

Allen Coral Atlas (2022), Imagery, maps and monitoring of the world's
tropical coral reefs, doi:10.5281/zenodo.3833242, Creative Commons
Attribution 4.0 International (CC BY 4.0), through its public WFS. Its
geomorphic map, mapped from satellite imagery down to about 10-15 m, gives
the reef flats, reef crests, patch reefs, back-reef slopes and shallow
lagoons of the regions whose file lists `allen-coral-atlas`, where a finer
reef map has none. Outlines are approximate.

## NOAA NCEI ETOPO 2022

NOAA National Centers for Environmental Information, 2022: ETOPO 2022
15 Arc-Second Global Relief Model, doi:10.25921/fd45-gt74. NOAA NCEI
distributes it without use restrictions and asks for this citation. Its
ocean depths come largely from GEBCO. Used only for deep water (200 m and
more, away from anything shallower) that no survey or finer model covers.

## Rijkswaterstaat and NSGI (Netherlands)

- **Bathymetrie Nederland - kust en vaklodingen 20 m**: Rijkswaterstaat,
  `https://downloads.rijkswaterstaatdata.nl/bodemhoogte_20mtr/`, Creative
  Commons Zero 1.0 (<https://creativecommons.org/publicdomain/zero/1.0/>).
  Attribution (not required, given anyway): "Rijkswaterstaat, Bathymetrie
  Nederland - kust en vaklodingen 20 m (CC0 1.0)". Heights above NAP, moved
  to LAT with the surfaces below. Used as the bathymetric model of the
  regions whose file asks for it (`"dem": [{"source": "vaklodingen"}]`).
- **NLGEO2018 and NLLAT2018**: Nederlandse Samenwerking Geodetische
  Infrastructuur (NSGI), the NAP and LAT surfaces above the ellipsoid, from
  the PROJ data CDN, Creative Commons Attribution 4.0 International (CC BY
  4.0). Attribution: "NSGI, NLGEO2018 and NLLAT2018, CC BY 4.0
  (https://creativecommons.org/licenses/by/4.0/)".

## Kartverket (Norway)

Dybdedata, terrengmodell 50 m: Kartverket's seabed terrain model, depths on
sjøkartnull (the datum of Norway's charts: LAT, or 0.2 to 0.3 m below it
in the Skagerrak and the inner Oslofjord), through its WCS
`https://wms.geonorge.no/skwms1/wms.dtm2`, Creative Commons Attribution 4.0
International (CC BY 4.0). Attribution: "© Kartverket, Dybdedata
terrengmodell 50 m, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)".
Used as the bathymetric model of the regions whose file asks for it
(`"dem": [{"source": "kartverket"}]`).

## LINZ (New Zealand)

The Hydro layers of the LINZ Data Service (<https://data.linz.govt.nz>):
the New Zealand Hydrographic Authority's chart data as S-57 objects, one
layer per object class and chart scale, published April 2023, through the
service's WFS (an account key, kept as the repository secret
`LINZ_API_KEY`). Creative Commons Attribution 4.0 International (CC BY 4.0).
Attribution: "Sourced from the LINZ Data Service (https://data.linz.govt.nz)
and licensed for reuse under the CC BY 4.0 licence: New Zealand
Hydrographic Authority chart data (Hydro layers, as of April 2023)". Used
for the depths, aids, hazards and areas of the regions that list
`linz-hydro`; the layers taken are in `odc/linz_hydro_layers.json`.

## EMODnet high-resolution DTMs (Portugal)

The Instituto Hidrográfico's (IHPT) high-resolution DTMs of Portuguese
ports, estuaries and coasts, Madeira and the Azores, on LAT, downloaded
from EMODnet Bathymetry (`https://downloads.emodnet-bathymetry.eu/high_resolution/`),
whose records give Creative Commons Attribution 4.0 International (CC BY
4.0) and "do not use for navigation". The coastal lidar models are by the
Agência Portuguesa do Ambiente (APA) and the Instituto Geográfico Português
(IGeo), distributed by IHPT. Attribution: "Instituto Hidrográfico (IHPT),
high-resolution DTMs from EMODnet Bathymetry (https://emodnet.ec.europa.eu),
CC BY 4.0", naming APA and IGeo where the lidar models are used. Used as
the bathymetric models of the regions whose file lists them
(`"dem": [..., {"source": "emodnet-hr", "dtms": [...]}]`).

## OpenStreetMap

© OpenStreetMap contributors, Open Database License (ODbL) 1.0
(<https://opendatacommons.org/licenses/odbl/>). Used for coastlines, land,
harbour structures, reefs, buildings near the sea and peaks.

## Copernicus DEM GLO-30

"Copernicus DEM GLO-30, © DLR e.V. 2010-2014 and © Airbus Defence and Space
GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA".
Used for the land contours.
