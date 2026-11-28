"""Offshore structures and hazards from EMODnet Human Activities (CC BY 4.0):
wind farms, platforms, cables, pipelines, military areas, dumped munitions,
UKHO's worldwide wreck list, lighthouses, fish and shellfish farms, dredge
spoil grounds, aggregate extraction areas and oil and gas wells, as S-57
objects.

Planned, approved and dismantled wind farms, cables and pipelines not on
the seabed, planned or deactivated military areas and lifted wrecks are
left out; what is being built is charted under construction. Disused
cables, pipelines and platforms are charted: they may still be there. A Shom, chart or earlier object of the
same class is cut out of an EMODnet one (see `layers`). A lighthouse or a
farm EMODnet places only roughly gives way to one another source charts
near it (see `give_way`).
"""

import glob
import json
import math
import os
import urllib.parse

from osgeo import ogr

from odc import seamarks

WFS = 'https://ows.emodnet-humanactivities.eu/wfs'
SOURCE = 'EMODNET-HA'
ATTRIBUTION = (
    'EMODnet Human Activities (https://emodnet.ec.europa.eu/en/human-activities): wind farms, offshore '
    'installations, pipelines, cables, military areas, dumped munitions, UKHO worldwide wreck locations, '
    'lighthouses (Amateur Radio Lighthouse Society list), aquaculture, dredge spoil grounds, aggregate '
    'extraction areas and oil and gas wells, CC BY 4.0; not suitable for navigation'
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
    'lighthouses',
    'finfish',
    'shellfish',
    'dredgespoilpoly',
    'dredgespoil',
    'aggregateareas',
    'hydrocarbons',
    *POWER_CABLES,
    *TELECOM_CABLES,
)
WIND_FARM_BUILT = {'Production': None, 'Construction': 1}  # -> CONDTN, 1 = under construction
# Statuses of a cable or pipeline not on the seabed. One abandoned, out
# of use or not in use still lies there and is charted: it fouls anchors.
NOT_LAID = {'planned', 'proposed', 'approved', 'applicationsubmitted', 'dismantled', 'removed'}
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
# ARLHS positions are approximate, and a harbour's lights stand closer than
# this to each other, so another source's light this near is the same one.
SAME_LIGHTHOUSE_M = 500.0
SAME_FARM_M = 300.0
CATLMK_TOWER = 17
FUNCTN_LIGHT_SUPPORT = 33
CATMFA_OYSTERS_MUSSELS = 2
CATMFA_FISH = 3
CATDPG_SPOIL = 5
LIGHTHOUSE_GONE = 'Removed, relocated or destroyed'
AT_SEA = 'At sea'
# An abandoned well is plugged and cut below the seabed; a suspended one
# keeps its wellhead.
WELL_STANDING = {'Active', 'Suspended'}
EXTRACTION_IN_USE = {'Active'}
SPOIL_LAYERS = ('dredgespoilpoly', 'dredgespoil')
# The layers EMODnet places roughly, the classes another source charts the
# same object as, and how near that one must be.
GIVE_WAY = {
    'lighthouses': (('LIGHTS', 'LNDMRK'), SAME_LIGHTHOUSE_M),
    'finfish': (('MARCUL',), SAME_FARM_M),
    'shellfish': (('MARCUL',), SAME_FARM_M),
}


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
    """A platform or subsea structure, decommissioned and closed down ones
    too: EMODnet does not say whether one was removed, and a structure that
    still stands is the danger."""
    if p.get('category') == 'Subsea steel':
        objl, attrs = 'OBSTRN', named({'CATOBS': str(CATOBS_WELLHEAD), 'WATLEV': 3}, p.get('name'))
    else:
        objl, attrs = 'OFSPLF', named({}, p.get('name'))
    if p.get('current_status') == 'Under construction':
        attrs['CONDTN'] = 1
    return objl, attrs


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


def lighthouse(p):
    if p.get('status') == LIGHTHOUSE_GONE:
        return None
    attrs = {'CATLMK': str(CATLMK_TOWER), 'FUNCTN': str(FUNCTN_LIGHT_SUPPORT)}
    name = text(p.get('name'))
    return 'LNDMRK', named(attrs, None if name == 'Un-assigned' else name)


def farm(category, position_key):
    def convert(p):
        if p.get(position_key) != AT_SEA:
            return None
        return 'MARCUL', {'CATMFA': str(category)}

    return convert


def spoil_ground(p):
    return 'DMPGRD', named({'CATDPG': str(CATDPG_SPOIL)}, p.get('site_name'))


def extraction_area(p):
    in_use = p.get('status') in EXTRACTION_IN_USE or 'in use' in (p.get('site_type') or '')
    if not in_use:
        return None
    attrs = named({}, p.get('name'))
    material = text(p.get('material'))
    attrs['INFORM'] = f'Aggregate extraction area: {material}' if material else 'Aggregate extraction area'
    return 'CTNARE', attrs


def well(p):
    if p.get('status') not in WELL_STANDING:
        return None
    attrs = named({'CATOBS': str(CATOBS_WELLHEAD), 'WATLEV': 3}, p.get('name'))
    attrs['INFORM'] = f'Well ({p["status"].lower()})'
    return 'OBSTRN', attrs


CONVERT = {
    'windfarmspoly': wind_farm,
    'platforms': platform,
    'pipelines': pipeline,
    'militaryareaspoly': military_area,
    'munitionspoly': munitions,
    'munitions': munitions,
    'wwshipwrecks': wreck,
    'lighthouses': lighthouse,
    'finfish': farm(CATMFA_FISH, 'position_c'),
    'shellfish': farm(CATMFA_OYSTERS_MUSSELS, 'costal_inland'),
    'dredgespoilpoly': spoil_ground,
    'dredgespoil': spoil_ground,
    'aggregateareas': extraction_area,
    'hydrocarbons': well,
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
    near_charted = {layer: charted_near(charted, classes, radius) for layer, (classes, radius) in GIVE_WAY.items()}
    # A spoil ground is listed once for every year it was used.
    seen = set()
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
            elif layer in near_charted:
                if near_charted[layer](geom):
                    continue
            elif geom.GetDimension() > 0:
                geom = elsewhere(objl, geom)
                if geom is None:
                    continue
            if layer in SPOIL_LAYERS:
                key = (attrs.get('OBJNAM'), bytes(geom.ExportToWkb()))
                if key in seen:
                    continue
                seen.add(key)
            by_class.setdefault(objl, []).append((geom, attrs, f'{SOURCE} {feature.get("id")}'))
    return by_class


def charted_near(charted, classes, radius_m):
    """Whether a point lies within radius_m of an object of one of classes
    among charted ({class: items}), or inside one's outline."""
    points, outlines = [], []
    for objl in classes:
        for geom, _, _ in charted.get(objl, ()):
            if geom.GetDimension() == 2:
                outlines.append(geom)
            centre = geom if geom.GetDimension() == 0 else geom.PointOnSurface()
            if centre is not None and not centre.IsEmpty():
                points.append((centre.GetX(), centre.GetY()))
    near = seamarks.Nearby(points, radius_m)
    return lambda geom: near(geom.GetX(), geom.GetY()) or any(outline.Contains(geom) for outline in outlines)


def give_way_window(bbox):
    """bbox grown by the widest give-way radius, so another source's object
    just beyond it still counts."""
    minlon, minlat, maxlon, maxlat = bbox
    radius = max(r for _, r in GIVE_WAY.values())
    dlat = radius / seamarks.METRES_PER_DEGREE
    dlon = dlat / max(math.cos(math.radians(max(abs(minlat), abs(maxlat)))), 0.01)
    return minlon - dlon, minlat - dlat, maxlon + dlon, maxlat + dlat


def give_way(layers, marks):
    """Leaves out of layers the EMODnet lighthouses and farms that marks
    (OSM seamarks, {class: items}, read over give_way_window) chart near
    them: OSM's position is the surveyed one, EMODnet's often approximate."""
    for layer, (classes, radius) in GIVE_WAY.items():
        near = charted_near(marks, classes, radius)
        prefix = f'{SOURCE} {layer}.'
        for objl in classes:
            if objl in layers:
                layers[objl] = [
                    item
                    for item in layers[objl]
                    if not (isinstance(item[2], str) and item[2].startswith(prefix) and near(item[0]))
                ]
