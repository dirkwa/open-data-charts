"""Offshore structures and hazards from EMODnet Human Activities (CC BY 4.0):
wind farms, platforms, cables, pipelines, military areas, dumped munitions
and UKHO's worldwide wreck list, as S-57 objects.

Only what is in the water now is charted: planned, approved and dismantled
wind farms, cables and pipelines not yet laid, planned or deactivated
military areas and lifted wrecks are left out; what is being built is
charted under construction. A Shom, chart or earlier object of the
same class is cut out of an EMODnet one (see `layers`).
"""

import glob
import json
import os
import urllib.parse

from osgeo import ogr

from odc import seamarks

WFS = 'https://ows.emodnet-humanactivities.eu/wfs'
ATTRIBUTION = (
    'EMODnet Human Activities (https://emodnet.ec.europa.eu/en/human-activities): wind farms, offshore '
    'installations, pipelines, cables, military areas, dumped munitions, and UKHO worldwide wreck locations, '
    'CC BY 4.0; not suitable for navigation'
)
POWER_CABLES = ('pcablesbshcontis', 'pcablesshom', 'pcablesnve')
TELECOM_CABLES = ('bshcontiscables', 'cicacables', 'shomcables', 'sigcables', 'maltacables', 'ukfibrecables')
LAYERS = (
    'windfarmspoly',
    'platforms',
    'pipelines',
    'militaryareaspoly',
    'munitionspoly',
    'munitions',
    'wwshipwrecks',
    *POWER_CABLES,
    *TELECOM_CABLES,
)
WIND_FARM_BUILT = {'Production': None, 'Construction': 1}  # -> CONDTN, 1 = under construction
NOT_LAID = {'planned', 'approved', 'applicationsubmitted'}
UNDER_CONSTRUCTION = 'underconstruction'
MILITARY_IN_FORCE = {'Active', 'Unknown'}
CATMPA = {'Firing Area': 4, 'Mine Hunting Exercise': 5, 'Underwater Exercise': 3}
CATWRK = {
    'Non-dangerous wreck': 1,
    'Dangerous wreck': 2,
    'Distributed remains of wreck': 3,
    'Wreck showing mast/masts': 4,
    'Wreck showing any portion of hull or superstructure': 5,
}
WATLEV = {
    'partly submerged at high water': 1,
    'always dry': 2,
    'always under water/submerged': 3,
    'covers and uncovers': 4,
    'awash': 5,
}
# A wreck's depth is charted only on a low-water datum; on mean sea level it
# would read deeper than a chart's sounding by up to half the tidal range.
LOW_WATER_DATUMS = {
    'Lowest Astronomical Tide',
    'Approximate Lowest Astronomical Tide',
    'Mean Low Water Springs',
    'Mean Lower Low Water Springs',
    'Lowest Low Water',
}
DEPTH_KNOWN = {'least depth known', 'depth known', 'least depth unknown, safe clearance at value shown'}
CATDPG_EXPLOSIVES = 4
CATOBS_WELLHEAD = 2
CATCBL_POWER = 1
CATCBL_TELEPHONE = 4
# Two sources' positions of one wreck differ by up to this much.
SAME_WRECK_M = 150.0


def text(value):
    value = (value or '').strip() if isinstance(value, str) else value
    return None if value in (None, '', 'n/a', 'None') else value


def named(attrs, name):
    name = text(name)
    if name:
        attrs['OBJNAM'] = name
    return attrs


def wind_farm(p):
    if p.get('status') not in WIND_FARM_BUILT:
        return None
    attrs = named({'CATPRA': '9'}, p.get('name'))
    seamarks.put(attrs, 'CONDTN', WIND_FARM_BUILT[p['status']])
    return 'PRDARE', attrs


def platform(p):
    if p.get('category') == 'Subsea steel':
        return 'OBSTRN', named({'CATOBS': str(CATOBS_WELLHEAD), 'WATLEV': 3}, p.get('name'))
    attrs = named({}, p.get('name'))
    if p.get('current_status') == 'Under construction':
        attrs['CONDTN'] = 1
    return 'OFSPLF', attrs


def status(p):
    """The status without case, spaces or separators: the layers spell one
    status several ways (Under construction, UnderConstruction)."""
    return ''.join(ch for ch in (text(p.get('status')) or '').lower() if ch.isalnum())


def laid(objl, attrs, p):
    """A cable or pipeline in the water, CONDTN 1 while it is being laid."""
    if status(p) in NOT_LAID:
        return None
    if status(p) == UNDER_CONSTRUCTION:
        attrs['CONDTN'] = 1
    return objl, attrs


def pipeline(p):
    return laid('PIPSOL', named({}, p.get('name')), p)


def cable(category):
    def convert(p):
        return laid('CBLSUB', named({'CATCBL': str(category)}, p.get('name_')), p)

    return convert


def military_area(p):
    if p.get('status') not in MILITARY_IN_FORCE:
        return None
    attrs = {}
    category = CATMPA.get(p.get('type_1'))
    seamarks.put(attrs, 'CATMPA', str(category) if category else None)
    seamarks.put(attrs, 'INFORM', text(p.get('type_1')))
    return 'MIPARE', attrs


def munitions(p):
    attrs = {'CATDPG': str(CATDPG_EXPLOSIVES)}
    seamarks.put(attrs, 'INFORM', text(p.get('description')) or text(p.get('munition_type')))
    return 'DMPGRD', attrs


def wreck(p):
    if 'lifted' in (p.get('status') or ''):
        return None
    attrs = named({}, p.get('name'))
    category = CATWRK.get(p.get('wreck_cate'))
    seamarks.put(attrs, 'CATWRK', str(category) if category else None)
    seamarks.put(attrs, 'WATLEV', WATLEV.get(p.get('water_leve')))
    if p.get('vertical_d') in LOW_WATER_DATUMS and p.get('depth_qual') in DEPTH_KNOWN:
        seamarks.put(attrs, 'VALSOU', seamarks.number(text(p.get('depth'))))
    return 'WRECKS', attrs


CONVERT = {
    'windfarmspoly': wind_farm,
    'platforms': platform,
    'pipelines': pipeline,
    'militaryareaspoly': military_area,
    'munitionspoly': munitions,
    'munitions': munitions,
    'wwshipwrecks': wreck,
    **{layer: cable(CATCBL_POWER) for layer in POWER_CABLES},
    **{layer: cable(CATCBL_TELEPHONE) for layer in TELECOM_CABLES},
}


def request(layer, bbox):
    minlon, minlat, maxlon, maxlat = bbox
    params = {
        'service': 'WFS',
        'version': '2.0.0',
        'request': 'GetFeature',
        'typeNames': f'emodnet:{layer}',
        'outputFormat': 'application/json',
        'srsName': 'urn:ogc:def:crs:EPSG::4326',
        # WFS 2.0 with an EPSG URN: axis order lat,lon.
        'bbox': f'{minlat},{minlon},{maxlat},{maxlon},urn:ogc:def:crs:EPSG::4326',
    }
    return f'{WFS}?{urllib.parse.urlencode(params)}'


def fetch(bbox, target, get_json):
    """Every layer's features meeting bbox, one FeatureCollection per layer."""
    os.makedirs(target, exist_ok=True)
    for layer in LAYERS:
        # The layers share no key to page by in a stable order; the service
        # answers a whole region's layer at once, and the count proves it.
        page = get_json(request(layer, bbox))
        features = page.get('features') or []
        matched = page.get('numberMatched')
        # A total the service cannot give is no proof the answer is whole.
        if not isinstance(matched, int) or matched != len(features):
            raise RuntimeError(f'EMODnet Human Activities answered {len(features)} of {matched} {layer}')
        features.sort(key=lambda f: str(f.get('id')))
        path = os.path.join(target, f'{layer}.json')
        with open(path + '.part', 'w', encoding='utf-8') as fh:
            json.dump({'type': 'FeatureCollection', 'features': features}, fh)
        os.replace(path + '.part', path)
        print(f'{len(features):6d}  {layer}', flush=True)


def layer_files(directory):
    return sorted(glob.glob(os.path.join(directory, '*.json')))


def wreck_positions(items):
    return seamarks.Nearby([(g.GetX(), g.GetY()) for g, _, _ in items if g.GetDimension() == 0], SAME_WRECK_M)


def not_charted_wrecks(items, charted):
    """The wreck items not within SAME_WRECK_M of a charted one."""
    near = wreck_positions(charted)
    return [item for item in items if item[0].GetDimension() > 0 or not near(item[0].GetX(), item[0].GetY())]


def layers(directory, bbox_geom, charted):
    """S-57 items per class from the fetched layers, cut to bbox_geom. What
    another source charts in the same class is cut out of an outline
    (seamarks.outlined_in), and a wreck within SAME_WRECK_M of a charted one
    is left out; charted is {class: items}."""
    by_class = {}
    elsewhere = seamarks.outlined_in(charted)
    wrecks = wreck_positions(charted.get('WRECKS', ()))
    for path in layer_files(directory):
        layer = os.path.splitext(os.path.basename(path))[0]
        convert = CONVERT.get(layer)
        if convert is None:
            continue
        with open(path, encoding='utf-8') as fh:
            features = json.load(fh).get('features', [])
        for feature in features:
            if not feature.get('geometry'):
                continue
            found = convert(feature.get('properties') or {})
            if found is None:
                continue
            objl, attrs = found
            geom = seamarks.clip(ogr.CreateGeometryFromJson(json.dumps(feature['geometry'])), bbox_geom)
            if geom is None:
                continue
            if objl == 'WRECKS':
                if wrecks(geom.GetX(), geom.GetY()):
                    continue
            elif geom.GetDimension() > 0:
                geom = elsewhere(objl, geom)
                if geom is None:
                    continue
            by_class.setdefault(objl, []).append((geom, attrs, f'EMODNET-HA {feature.get("id")}'))
    return by_class
