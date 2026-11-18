"""Aids to navigation from OpenStreetMap's seamark tags (the OpenSeaMap
scheme), as the S-57 objects the Shom WFS gives French waters.

The scheme mirrors S-57: `seamark:type` names the object class, and
`seamark:<type>:<attribute>` holds its attributes as words for S-57's enumerated
values. Lights, topmarks, fog signals, daymarks and radar transponders on
an object are their own S-57 objects at its position, as in an ENC.
"""

import re

COLOUR = {
    'white': 1,
    'black': 2,
    'red': 3,
    'green': 4,
    'blue': 5,
    'yellow': 6,
    'grey': 7,
    'brown': 8,
    'amber': 9,
    'violet': 10,
    'orange': 11,
    'magenta': 12,
    'pink': 13,
}
COLPAT = {'horizontal': 1, 'vertical': 2, 'diagonal': 3, 'squared': 4, 'stripes': 5, 'border': 6}
BOYSHP = {
    'conical': 1,
    'can': 2,
    'spherical': 3,
    'pillar': 4,
    'spar': 5,
    'barrel': 6,
    'super-buoy': 7,
    'ice_buoy': 8,
}
BCNSHP = {'stake': 1, 'withy': 2, 'tower': 3, 'lattice': 4, 'pile': 5, 'cairn': 6, 'buoyant': 7}
TOPSHP = {
    'cone, point up': 1,
    'cone, point down': 2,
    'sphere': 3,
    '2 spheres': 4,
    'cylinder': 5,
    'board': 6,
    'x-shape': 7,
    'cross': 8,
    'cube, point up': 9,
    '2 cones point together': 10,
    '2 cones base together': 11,
    'rhombus': 12,
    '2 cones up': 13,
    '2 cones down': 14,
    'besom, point up': 15,
    'besom, point down': 16,
    'flag': 17,
    'sphere over rhombus': 18,
    'square': 19,
    'rectangle, horizontal': 20,
    'rectangle, vertical': 21,
    'trapezium, up': 22,
    'trapezium, down': 23,
    'triangle, point up': 24,
    'triangle, point down': 25,
    'circle': 26,
    'two upright crosses': 27,
    't-shape': 28,
    'triangle, point up over circle': 29,
    'upright cross over circle': 30,
    'rhombus over circle': 31,
    'circle over triangle, point up': 32,
    'other shape': 33,
}
CATLAM = {'port': 1, 'starboard': 2, 'preferred_channel_starboard': 3, 'preferred_channel_port': 4}
CATCAM = {'north': 1, 'east': 2, 'south': 3, 'west': 4}
CATSPM = {
    'firing_danger_area': 1,
    'target': 2,
    'marker_ship': 3,
    'degaussing_range': 4,
    'barge': 5,
    'cable': 6,
    'spoil_ground': 7,
    'outfall': 8,
    'odas': 9,
    'recording': 10,
    'seaplane_anchorage': 11,
    'recreation_zone': 12,
    'private': 13,
    'mooring': 14,
    'lanby': 15,
    'leading': 16,
    'measured_distance': 17,
    'notice': 18,
    'tss': 19,
}
CATLMK = {
    'cairn': 1,
    'cemetery': 2,
    'chimney': 3,
    'dish_aerial': 4,
    'flagstaff': 5,
    'flare_stack': 6,
    'mast': 7,
    'windsock': 8,
    'monument': 9,
    'column': 10,
    'memorial': 11,
    'obelisk': 12,
    'statue': 13,
    'cross': 14,
    'dome': 15,
    'radar_scanner': 16,
    'tower': 17,
    'windmill': 18,
    'windmotor': 19,
    'spire': 20,
    'boulder': 21,
}
CATMOR = {'dolphin': 1, 'deviation_dolphin': 2, 'bollard': 3, 'tie-up_wall': 4, 'post': 5, 'chain': 6, 'buoy': 7}
CATWRK = {'non-dangerous': 1, 'dangerous': 2, 'distributed_remains': 3, 'mast_showing': 4, 'hull_showing': 5}
CATOBS = {
    'stump': 1,
    'wellhead': 2,
    'diffuser': 3,
    'crib': 4,
    'fish_haven': 5,
    'foul_area': 6,
    'foul_ground': 7,
    'ice_boom': 8,
    'ground_tackle': 9,
    'boom': 10,
}
CATOFP = {
    'oil_derrick': 1,
    'production': 2,
    'observation': 3,
    'alp': 4,
    'salm': 5,
    'mooring': 6,
    'artificial_island': 7,
    'fpso': 8,
    'accommodation': 9,
    'nccb': 10,
}
CATFOG = {
    'explosive': 1,
    'diaphone': 2,
    'siren': 3,
    'nautophone': 4,
    'reed': 5,
    'tyfon': 6,
    'bell': 7,
    'whistle': 8,
    'gong': 9,
    'horn': 10,
}
WATLEV = {
    'partially_submerged': 1,
    'dry': 2,
    'submerged': 3,
    'covers': 4,
    'awash': 5,
    'floods': 6,
    'floating': 7,
}
LITCHR = {
    'F': 1,
    'Fl': 2,
    'LFl': 3,
    'Q': 4,
    'VQ': 5,
    'UQ': 6,
    'Iso': 7,
    'Oc': 8,
    'IQ': 9,
    'IVQ': 10,
    'IUQ': 11,
    'Mo': 12,
    'FFl': 13,
    'FlLFl': 14,
    'OcFl': 15,
    'FLFl': 16,
    'Al.Oc': 17,
    'Al.LFl': 18,
    'Al.Fl': 19,
    'Al.Gr': 20,
    'Q+LFl': 25,
    'VQ+LFl': 26,
    'UQ+LFl': 27,
    'Al': 28,
    'Al.FFl': 29,
}
CATLIT = {
    'directional': 1,
    'leading': 4,
    'aero': 5,
    'air_obstruction': 6,
    'fog_detector': 7,
    'flood': 8,
    'strip': 9,
    'subsidiary': 10,
    'spotlight': 11,
    'front': 12,
    'rear': 13,
    'lower': 14,
    'upper': 15,
    'moire': 16,
    'emergency': 17,
    'bearing': 18,
    'horizontally': 19,
    'vertically': 20,
}
LITVIS = {
    'high': 1,
    'low': 2,
    'faint': 3,
    'intensified': 4,
    'unintensified': 5,
    'restricted': 6,
    'obscured': 7,
    'part_obscured': 8,
}
EXCLIT = {'24h': 1, 'day': 2, 'fog': 3, 'night': 4}

# seamark:type -> (S-57 class, the class's category attribute and its values, shape attribute and values)
TYPES = {
    'buoy_lateral': ('BOYLAT', ('CATLAM', CATLAM), ('BOYSHP', BOYSHP)),
    'buoy_cardinal': ('BOYCAR', ('CATCAM', CATCAM), ('BOYSHP', BOYSHP)),
    'buoy_isolated_danger': ('BOYISD', None, ('BOYSHP', BOYSHP)),
    'buoy_safe_water': ('BOYSAW', None, ('BOYSHP', BOYSHP)),
    'buoy_special_purpose': ('BOYSPP', ('CATSPM', CATSPM), ('BOYSHP', BOYSHP)),
    'buoy_installation': ('BOYINB', None, ('BOYSHP', BOYSHP)),
    'beacon_lateral': ('BCNLAT', ('CATLAM', CATLAM), ('BCNSHP', BCNSHP)),
    'beacon_cardinal': ('BCNCAR', ('CATCAM', CATCAM), ('BCNSHP', BCNSHP)),
    'beacon_isolated_danger': ('BCNISD', None, ('BCNSHP', BCNSHP)),
    'beacon_safe_water': ('BCNSAW', None, ('BCNSHP', BCNSHP)),
    'beacon_special_purpose': ('BCNSPP', ('CATSPM', CATSPM), ('BCNSHP', BCNSHP)),
    'light_vessel': ('LITVES', None, None),
    'light_float': ('LITFLT', None, None),
    'landmark': ('LNDMRK', ('CATLMK', CATLMK), None),
    'mooring': ('MORFAC', ('CATMOR', CATMOR), ('BOYSHP', BOYSHP)),
    'wreck': ('WRECKS', ('CATWRK', CATWRK), None),
    'rock': ('UWTROC', None, None),
    'obstruction': ('OBSTRN', ('CATOBS', CATOBS), None),
    'platform': ('OFSPLF', ('CATOFP', CATOFP), None),
    'pile': ('PILPNT', None, None),
}
# A lighthouse is tagged by its light alone.
LIGHT_ONLY = {'light_major', 'light_minor', 'light'}
# Parts an object carries, each its own S-57 object at the object's position.
PARTS = {
    'topmark': 'TOPMAR',
    'daymark': 'DAYMAR',
    'fog_signal': 'FOGSIG',
    'radar_transponder': 'RTPBCN',
}
NUMBER = re.compile(r'[-+]?\d+(?:\.\d+)?')
LIGHT_KEY = re.compile(r'^seamark:light(?::(\d+))?:([a-z_]+)$')
GROUP = re.compile(r'^\s*([A-Za-z.+]+)\s*\(([^)]*)\)\s*$')


def number(value):
    match = NUMBER.search(value or '')
    if not match:
        return None
    n = float(match.group())
    return int(n) if n.is_integer() else n


def enum_list(value, table):
    """S-57 list attribute ('3,1') from OSM's ';'-separated words; unknown words drop out."""
    codes = [table[word.strip()] for word in (value or '').split(';') if word.strip() in table]
    return ','.join(str(code) for code in codes) or None


def enum(value, table):
    return table.get((value or '').split(';')[0].strip())


def put(attrs, key, value):
    if value is not None:
        attrs[key] = value


def common(attrs, keys):
    put(attrs, 'COLOUR', enum_list(keys.get('colour'), COLOUR))
    put(attrs, 'COLPAT', enum_list(keys.get('colour_pattern'), COLPAT))
    put(attrs, 'HEIGHT', number(keys.get('height')))


def subkeys(tags, prefix):
    start = f'seamark:{prefix}:'
    return {key[len(start) :]: value for key, value in tags.items() if key.startswith(start)}


def main_object(kind, tags):
    objl, category, shape = TYPES[kind]
    keys = subkeys(tags, kind)
    attrs = {}
    name = tags.get('seamark:name') or tags.get('name')
    if name:
        attrs['OBJNAM'] = name
    common(attrs, keys)
    if category:
        put(attrs, category[0], enum_list(keys.get('category'), category[1]))
    if shape:
        put(attrs, shape[0], enum(keys.get('shape'), shape[1]))
    if objl in ('WRECKS', 'UWTROC', 'OBSTRN'):
        put(attrs, 'WATLEV', enum(keys.get('water_level'), WATLEV))
        put(attrs, 'VALSOU', number(keys.get('depth')))
    return objl, attrs


def part_object(objl, keys):
    attrs = {}
    common(attrs, keys)
    if objl in ('TOPMAR', 'DAYMAR'):
        put(attrs, 'TOPSHP', enum(keys.get('shape'), TOPSHP))
    if objl == 'FOGSIG':
        put(attrs, 'CATFOG', enum(keys.get('category'), CATFOG))
        put(attrs, 'SIGPER', number(keys.get('period')))
        put(attrs, 'VALMXR', number(keys.get('range')))
    return attrs


def light_character(keys):
    """(LITCHR, SIGGRP) from character and group, also written together as 'Fl(2)'."""
    character, group = (keys.get('character') or '').strip(), (keys.get('group') or '').strip()
    match = GROUP.match(character)
    if match:
        character, group = match.group(1), group or match.group(2)
    return LITCHR.get(character), f'({group})' if group else None


def lights(tags):
    """One LIGHTS per sector: `seamark:light:<n>:*` are sectors, unnumbered
    keys the light itself or what every sector shares."""
    sectors = {}
    for key, value in tags.items():
        match = LIGHT_KEY.match(key)
        if match:
            sectors.setdefault(match.group(1), {})[match.group(2)] = value
    shared = sectors.pop(None, {})
    out = []
    for keys in [{**shared, **sectors[n]} for n in sorted(sectors, key=int)] or ([shared] if shared else []):
        attrs = {}
        put(attrs, 'COLOUR', enum_list(keys.get('colour'), COLOUR))
        litchr, siggrp = light_character(keys)
        put(attrs, 'LITCHR', litchr)
        put(attrs, 'SIGGRP', siggrp)
        put(attrs, 'SIGPER', number(keys.get('period')))
        put(attrs, 'VALNMR', number(keys.get('range')))
        put(attrs, 'HEIGHT', number(keys.get('height')))
        put(attrs, 'SECTR1', number(keys.get('sector_start')))
        put(attrs, 'SECTR2', number(keys.get('sector_end')))
        put(attrs, 'ORIENT', number(keys.get('orientation')))
        put(attrs, 'CATLIT', enum_list(keys.get('category'), CATLIT))
        put(attrs, 'LITVIS', enum_list(keys.get('visibility'), LITVIS))
        put(attrs, 'EXCLIT', enum(keys.get('exhibition'), EXCLIT))
        if attrs:
            out.append(attrs)
    return out


def objects(tags):
    """(S-57 class, attributes) for every object one seamark-tagged OSM object stands for."""
    kind = tags.get('seamark:type')
    out = []
    if kind == 'mooring' and tags.get('seamark:mooring:category') != 'buoy':
        return out  # posts, dolphins and bollards line every quay and canal
    if kind in TYPES:
        out.append(main_object(kind, tags))
    elif kind in PARTS:
        out.append((PARTS[kind], part_object(PARTS[kind], subkeys(tags, kind))))
    elif kind not in LIGHT_ONLY:
        return out
    for prefix, objl in PARTS.items():
        keys = subkeys(tags, prefix)
        if keys and kind != prefix:
            out.append((objl, part_object(objl, keys)))
    out += [('LIGHTS', attrs) for attrs in lights(tags)]
    return out


def seamark_layers(items, bbox_geom, source):
    """S-57 items per class from (tags, geometry) of seamark-tagged OSM objects
    inside bbox_geom, each at a point of its object."""
    layers = {}
    for tags, geom in items:
        found = objects(tags)
        if not found:
            continue
        where = geom if geom.GetDimension() == 0 else geom.PointOnSurface()
        if where is None or where.IsEmpty() or not bbox_geom.Contains(where):
            continue
        for objl, attrs in found:
            layers.setdefault(objl, []).append((where.Clone(), {**attrs, 'INFORM': source}, None))
    return layers
