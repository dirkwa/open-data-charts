"""Aids to navigation from OpenStreetMap's seamark tags (the OpenSeaMap
scheme), as the S-57 objects the Shom WFS gives French waters.

The scheme mirrors S-57: `seamark:type` names the object class, and
`seamark:<type>:<attribute>` holds its attributes as words for S-57's enumerated
values. Lights, topmarks, fog signals, daymarks and radar transponders on
an object are their own S-57 objects at its position, as in an ENC.
Traffic separation schemes, restricted and military areas, wind farms,
cables, pipelines, fishing gear and marine farms keep their outline: on open water they are what a
passage must keep clear of. Cables and pipelines in the water and wind
turbines off the land that carry no seamark tags are charted too.
"""

import math
import re

from osgeo import ogr

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
    'part-submerged': 1,
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
CATRTB = {'ramark': 1, 'racon': 2, 'leading': 3}
CATLIT = {
    'directional': 1,
    'leading': 4,
    'aero': 5,
    'air_obstruction': 6,
    'fog_detector': 7,
    'floodlight': 8,
    'strip_light': 9,
    'subsidiary': 10,
    'spotlight': 11,
    'front': 12,
    'rear': 13,
    'lower': 14,
    'upper': 15,
    'moire': 16,
    'emergency': 17,
    'bearing': 18,
    'horizontal': 19,
    'vertical': 20,
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
CATREA = {
    'safety': 1,
    'offshore_safety_zone': 1,
    'nature_reserve': 4,
    'bird_sanctuary': 5,
    'game_reserve': 6,
    'seal_sanctuary': 7,
    'degaussing_range': 8,
    'military': 9,
    'historic_wreck': 10,
    'navigational_aid_safety': 12,
    'minefield': 14,
    'swimming': 18,
    'waiting': 19,
    'research': 20,
    'dredging': 21,
    'fish_sanctuary': 22,
    'ecological_reserve': 23,
    'no_wake': 24,
    'swinging': 25,
}
RESTRN = {
    'no_anchoring': 1,
    'restricted_anchoring': 2,
    'no_fishing': 3,
    'restricted_fishing': 4,
    'no_trawling': 5,
    'restricted_trawling': 6,
    'no_entry': 7,
    'restricted_entry': 8,
    'no_dredging': 9,
    'restricted_dredging': 10,
    'no_diving': 11,
    'restricted_diving': 12,
    'no_wake': 13,
    'area_to_be_avoided': 14,
    'to_be_avoided': 14,
    'no_construction': 15,
}
CATMPA = {
    'torpedo_exercise': 2,
    'submarine_exercise': 3,
    'firing_danger_area': 4,
    'mine-laying_practice': 5,
    'small_arms_firing': 6,
}
CATPRA = {
    'quarry': 1,
    'mine': 2,
    'stockpile': 3,
    'power_station': 4,
    'refinery': 5,
    'timber_yard': 6,
    'factory': 7,
    'tank_farm': 8,
    'wind_farm': 9,
}
CATCBL = {'power': 1, 'transmission': 3, 'telephone': 4, 'telegraph': 5, 'mooring': 6}
CATPIP = {'outfall': 2, 'intake': 3, 'sewer': 4, 'bubbler': 5, 'supply': 6}
CATFIF = {'stake': 1, 'trap': 2, 'weir': 3, 'tunny': 4}
CATMFA = {'crustaceans': 1, 'oysters_mussels': 2, 'fish': 3, 'seaweed': 4}
CATACH = {
    'unrestricted': 1,
    'deep_water': 2,
    'tanker': 3,
    'explosives': 4,
    'quarantine': 5,
    'sea-plane': 6,
    'small_craft': 7,
    'small_craft_mooring': 8,
    '24_hour': 9,
}
# seamark:type -> (S-57 class, the geometries it may have, the class's category attribute and its values)
OUTLINED = {
    'separation_zone': ('TSEZNE', 'area', None),
    'separation_line': ('TSELNE', 'line', None),
    'separation_boundary': ('TSSBND', 'line', None),
    'separation_lane': ('TSSLPT', 'area', None),
    'separation_crossing': ('TSSCRS', 'area', None),
    'separation_roundabout': ('TSSRON', 'area', None),
    'inshore_traffic_zone': ('ISTZNE', 'area', None),
    'precautionary_area': ('PRCARE', 'point area', None),
    'restricted_area': ('RESARE', 'area', ('CATREA', CATREA)),
    'military_area': ('MIPARE', 'point area', ('CATMPA', CATMPA)),
    'production_area': ('PRDARE', 'point area', ('CATPRA', CATPRA)),
    'cable_submarine': ('CBLSUB', 'line', ('CATCBL', CATCBL)),
    'cable_area': ('CBLARE', 'area', ('CATCBL', CATCBL)),
    'pipeline_submarine': ('PIPSOL', 'point line', ('CATPIP', CATPIP)),
    'pipeline_area': ('PIPARE', 'point area', ('CATPIP', CATPIP)),
    'anchorage': ('ACHARE', 'point area', ('CATACH', CATACH)),
    'dumping_ground': ('DMPGRD', 'point area', None),
    'fishing_facility': ('FSHFAC', 'point line area', ('CATFIF', CATFIF)),
    'marine_farm': ('MARCUL', 'point line area', ('CATMFA', CATMFA)),
}
OUTLINED_CLASSES = {objl for objl, _, _ in OUTLINED.values()}
DIMENSION = {'point': 0, 'line': 1, 'area': 2}
MULTI = {0: ogr.wkbMultiPoint, 1: ogr.wkbMultiLineString, 2: ogr.wkbMultiPolygon}
COLLECTIONS = {ogr.wkbGeometryCollection, *MULTI.values()}
RESTRICTED_CLASSES = {
    'ACHARE',
    'CBLARE',
    'DMPGRD',
    'ISTZNE',
    'MARCUL',
    'MIPARE',
    'PIPARE',
    'PRCARE',
    'RESARE',
    'TSSCRS',
    'TSSLPT',
    'TSSRON',
}
# Two sources' outlines of one object lie this close; what another source
# charts within it is taken for the same object.
SAME_OUTLINE_DEG = 0.002
# A lighthouse is tagged by its light alone.
LIGHT_ONLY = {'light_major', 'light_minor', 'light'}
# Parts an object carries, each its own S-57 object at the object's position.
PARTS = {
    'topmark': 'TOPMAR',
    'daymark': 'DAYMAR',
    'fog_signal': 'FOGSIG',
    'radar_transponder': 'RTPBCN',
}
WATER_LOCATIONS = {'underwater', 'submarine'}
TURBINE = 'odc:wind_turbine'
DANGERS = {'WRECKS', 'UWTROC', 'OBSTRN', 'OFSPLF', 'PILPNT', 'MORFAC'}
AID_CLASSES = ({objl for objl, _, _ in TYPES.values()} - DANGERS) | set(PARTS.values()) | {'LIGHTS'}
# A unit holding French and foreign coast has Shom's aids and OSM's; an OSM
# mark this close to a Shom aid is taken to be one Shom already charts.
SHOM_AID_RADIUS_M = 1852.0
METRES_PER_DEGREE = 111320.0
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


def outlined_object(kind, tags):
    objl, _, category = OUTLINED[kind]
    keys = subkeys(tags, kind)
    attrs = {}
    name = tags.get('seamark:name') or tags.get('name')
    if name:
        attrs['OBJNAM'] = name
    if category:
        put(attrs, category[0], enum_list(keys.get('category'), category[1]))
    if objl in RESTRICTED_CLASSES:
        put(attrs, 'RESTRN', enum_list(keys.get('restriction'), RESTRN))
    if objl == 'TSSLPT':
        put(attrs, 'ORIENT', number(keys.get('orientation')))
    return objl, attrs


def fits(kind, geom):
    """Whether geom has a dimension S-57 allows for the type's class."""
    allowed = {DIMENSION[word] for word in OUTLINED[kind][1].split()}
    return geom.GetDimension() in allowed


def parts_of_dimension(geom, dimension):
    """geom's parts of that dimension as one geometry, or None: cutting or
    repairing an area can leave lines and points beside it."""
    if geom is None or geom.IsEmpty():
        return None
    if ogr.GT_Flatten(geom.GetGeometryType()) not in COLLECTIONS:
        return geom if geom.GetDimension() == dimension else None
    multi = ogr.Geometry(MULTI[dimension])
    for i in range(geom.GetGeometryCount()):
        part = parts_of_dimension(geom.GetGeometryRef(i), dimension)
        if part is None:
            continue
        if ogr.GT_Flatten(part.GetGeometryType()) in COLLECTIONS:
            for j in range(part.GetGeometryCount()):
                multi.AddGeometry(part.GetGeometryRef(j))
        else:
            multi.AddGeometry(part)
    return multi if multi.GetGeometryCount() else None


def clip(geom, bbox_geom):
    """geom cut to bbox_geom, keeping only parts of geom's own dimension, or None."""
    if bbox_geom.Contains(geom):
        return geom
    return parts_of_dimension(geom.MakeValid().Intersection(bbox_geom), geom.GetDimension())


def part_object(objl, keys):
    attrs = {}
    common(attrs, keys)
    if objl in ('TOPMAR', 'DAYMAR'):
        put(attrs, 'TOPSHP', enum(keys.get('shape'), TOPSHP))
    if objl == 'FOGSIG':
        put(attrs, 'CATFOG', enum(keys.get('category'), CATFOG))
        put(attrs, 'SIGPER', number(keys.get('period')))
        put(attrs, 'VALMXR', number(keys.get('range')))
    if objl == 'RTPBCN':
        put(attrs, 'CATRTB', enum(keys.get('category'), CATRTB))
        group = (keys.get('group') or '').strip()
        put(attrs, 'SIGGRP', f'({group})' if group else None)
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
    found = lights(tags)
    if not found and kind in LIGHT_ONLY:
        found = [{}]  # the type alone says there is a light
    out += [('LIGHTS', attrs) for attrs in found]
    return out


class Nearby:
    """Whether a point lies within radius_m of any of the given points."""

    def __init__(self, points, radius_m):
        self.cell = radius_m / METRES_PER_DEGREE
        self.radius_m = radius_m
        self.cells = {}
        for lon, lat in points:
            self.cells.setdefault(self.key(lon, lat), []).append((lon, lat))

    def key(self, lon, lat):
        return math.floor(lon / self.cell), math.floor(lat / self.cell)

    def __call__(self, lon, lat):
        kx, ky = self.key(lon, lat)
        # A degree of longitude is never longer than one of latitude, so
        # cells one radius wide in longitude reach far enough at any latitude.
        reach = math.ceil(1 / max(math.cos(math.radians(lat)), 0.01))
        for dx in range(-reach, reach + 1):
            for dy in (-1, 0, 1):
                for x, y in self.cells.get((kx + dx, ky + dy), ()):
                    east = (x - lon) * METRES_PER_DEGREE * math.cos(math.radians(lat))
                    north = (y - lat) * METRES_PER_DEGREE
                    if math.hypot(east, north) <= self.radius_m:
                        return True
        return False


def aid_window(bbox):
    """bbox grown by SHOM_AID_RADIUS_M, so a Shom aid just beyond it still counts."""
    minlon, minlat, maxlon, maxlat = bbox
    dlat = SHOM_AID_RADIUS_M / METRES_PER_DEGREE
    dlon = dlat / max(math.cos(math.radians(max(abs(minlat), abs(maxlat)))), 0.01)
    return minlon - dlon, minlat - dlat, maxlon + dlon, maxlat + dlat


def shom_aids(wfs_layers):
    """Nearby over the aids to navigation among the Shom WFS objects."""
    points = []
    for objl in AID_CLASSES:
        for geom, _, _ in wfs_layers.get(objl, []):
            centre = geom if geom.GetDimension() == 0 else geom.PointOnSurface()
            if centre is not None and not centre.IsEmpty():
                points.append((centre.GetX(), centre.GetY()))
    return Nearby(points, SHOM_AID_RADIUS_M)


def as_seamark(tags):
    """The tags in the seamark scheme: a cable or pipeline in the water or a
    wind turbine mapped without seamark tags reads as the seamark it is.
    None for an object that is no seamark (a cable on land), and a turbine
    is marked TURBINE, since only one off the land is charted."""
    if 'seamark:type' in tags:
        return tags
    in_water = tags.get('location') in WATER_LOCATIONS
    if tags.get('power') == 'cable' and in_water:
        return {**tags, 'seamark:type': 'cable_submarine', 'seamark:cable_submarine:category': 'power'}
    if tags.get('man_made') == 'pipeline' and in_water:
        return {**tags, 'seamark:type': 'pipeline_submarine'}
    if tags.get('generator:source') == 'wind':
        return {**tags, 'seamark:type': 'landmark', 'seamark:landmark:category': 'windmotor', TURBINE: True}
    return None


def seamark_layers(items, bbox_geom, source, charted=None, outlined_elsewhere=None, on_land=None):
    """S-57 items per class from (tags, geometry) of seamark-tagged OSM objects
    inside bbox_geom, each at a point of its object, leaving out aids
    charted(lon, lat) says another source has. Dangers are always kept: an
    aid nearby does not mean the other source charts the wreck or rock.
    Outlined objects keep their geometry, cut to bbox_geom, less what
    outlined_elsewhere(objl, geom) says another source charts."""
    layers = {}
    for osm_tags, geom in items:
        tags = as_seamark(osm_tags)
        if tags is None:
            continue
        kind = tags.get('seamark:type')
        if kind in OUTLINED:
            if not fits(kind, geom):
                continue
            objl, attrs = outlined_object(kind, tags)
            geom = clip(geom, bbox_geom)
            if geom is not None and outlined_elsewhere is not None:
                geom = outlined_elsewhere(objl, geom)
            if geom is None:
                continue
            layers.setdefault(objl, []).append((geom.Clone(), {**attrs, 'INFORM': source}, None))
            continue
        found = objects(tags)
        if not found:
            continue
        where = geom if geom.GetDimension() == 0 else geom.PointOnSurface()
        if where is None or where.IsEmpty() or not bbox_geom.Contains(where):
            continue
        if found[0][0] in AID_CLASSES and charted is not None and charted(where.GetX(), where.GetY()):
            continue
        if TURBINE in tags and on_land is not None and on_land(where):
            continue
        for objl, attrs in found:
            layers.setdefault(objl, []).append((where.Clone(), {**attrs, 'INFORM': source}, None))
    return layers


def outlined_in(layers):
    """outlined_elsewhere for seamark_layers: the part of geom that no
    object of the same class among layers covers within SAME_OUTLINE_DEG,
    or None. Judged point by point, so two units agree on every cell more
    than SAME_OUTLINE_DEG inside both their boxes, which the build margin
    makes every cell of each unit's own bounds. An object shown at some
    zooms only covers nothing: the other source's stays at every zoom."""
    covers = {}

    def cover(objl):
        if objl not in covers:
            shapes = ogr.Geometry(ogr.wkbMultiPolygon)
            for other, attrs, _ in layers.get(objl, ()):
                if other.GetDimension() == 0 or '_minzoom' in attrs or '_maxzoom' in attrs:
                    continue
                grown = parts_of_dimension(other.Buffer(SAME_OUTLINE_DEG), 2)
                if grown is not None:
                    grown = ogr.ForceToMultiPolygon(grown)
                    for i in range(grown.GetGeometryCount()):
                        shapes.AddGeometry(grown.GetGeometryRef(i))
            covers[objl] = shapes.UnionCascaded() if shapes.GetGeometryCount() else None
        return covers[objl]

    def uncharted(objl, geom):
        covered = cover(objl)
        if covered is None or not covered.Intersects(geom):
            return geom
        return parts_of_dimension(geom.Difference(covered), geom.GetDimension())

    return uncharted
