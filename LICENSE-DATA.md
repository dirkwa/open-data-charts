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
  the models of its region.

The scanned Shom paper charts (RasterMarine) are under a commercial licence
and are not used.

## Gouvernement de la Nouvelle-Calédonie

Licence Ouverte (<https://data.gouv.nc/>), via the Géorep services.

- Reef geomorphology (IRD / DINUM), mapped from satellite imagery; outlines
  are approximate (tens of metres).
- OBLIC coastal structures of Nouméa, 2016 (DIMENC / SGNC).

## OpenStreetMap

© OpenStreetMap contributors, Open Database License (ODbL) 1.0
(<https://opendatacommons.org/licenses/odbl/>). Used for coastlines, land,
harbour structures, reefs, buildings near the sea and peaks.

## Copernicus DEM GLO-30

"Copernicus DEM GLO-30, © DLR e.V. 2010-2014 and © Airbus Defence and Space
GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA".
Used for the land contours.
