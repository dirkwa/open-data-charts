import json
import sys

R = {
    'Bretagne (Shom, Referenz)': [(47.2, -5.2, 48.9, -1.9)],
    'Cote d Azur (Shom, Referenz)': [(42.9, 5.0, 43.8, 7.6)],
    'Balearen': [(38.6, 1.1, 40.1, 4.4)],
    'Katalonien': [(40.5, 0.5, 42.5, 3.4)],
    'Spanien Sued/Ost': [(36.0, -5.6, 40.5, 0.5)],
    'Ligurien': [(43.7, 7.5, 44.5, 10.2)],
    'Toskana/Elba': [(42.3, 10.2, 43.9, 11.8)],
    'Sardinien': [(38.8, 8.1, 41.3, 9.9)],
    'Sizilien': [(36.6, 12.3, 38.4, 15.7)],
    'Malta': [(35.7, 14.1, 36.1, 14.6)],
    'Nordadria/Istrien': [(44.5, 12.2, 45.8, 14.5)],
    'Dalmatien': [(42.3, 14.5, 44.8, 18.6)],
    'Montenegro/Albanien': [(39.6, 18.6, 42.5, 20.1)],
    'Ionisches Meer': [(37.8, 19.3, 40.0, 21.3)],
    'Saronischer Golf/Kykladen': [(36.3, 22.8, 38.2, 26.0)],
    'Dodekanes/Bodrum': [(35.8, 26.0, 38.0, 28.3)],
    'Tuerkei Suedkueste': [(35.9, 28.3, 37.0, 31.0)],
    'Zypern': [(34.5, 32.2, 35.8, 34.7)],
    'Tunesien': [(33.0, 7.5, 37.6, 11.6)],
    'Kuba': [(19.6, -85.1, 23.4, -74.0)],
    'Kolumbien Karibik': [(9.0, -77.0, 12.5, -71.0)],
    'San Andres': [(12.4, -81.8, 13.5, -81.3)],
    'Bahamas': [(20.9, -80.5, 27.3, -72.7)],
    'Jamaika': [(17.6, -78.5, 18.6, -76.1)],
    'Dominikanische Rep.': [(17.4, -72.1, 20.0, -68.3)],
    'Jungferninseln/Leewards': [(16.9, -65.0, 18.8, -61.6)],
    'Windwards/Grenadinen': [(11.9, -62.0, 14.2, -59.4)],
    'ABC-Inseln': [(11.9, -70.2, 12.7, -68.1)],
    'Belize/Bay Islands': [(15.7, -89.2, 18.6, -85.8)],
    'Panama San Blas/Bocas': [(8.8, -82.6, 9.8, -77.2)],
    'Yucatan': [(18.0, -88.2, 22.0, -86.5)],
    'Fidschi': [(-21.0, 176.8, -15.7, 180.0), (-21.0, -180.0, -15.7, -178.3)],
    'Tonga': [(-22.5, -176.3, -15.5, -173.5)],
    'Vanuatu': [(-20.4, 166.4, -13.0, 170.3)],
    'Norwegen Suedkueste': [(57.8, 5.0, 59.5, 11.2)],
    'Irland': [(51.3, -10.7, 55.5, -5.3)],
    'Daenemark': [(54.5, 8.0, 57.8, 12.8)],
    'Hauraki Gulf NZ': [(-37.5, 174.4, -35.5, 176.0)],
    'Whitsundays AU': [(-21.0, 148.3, -19.8, 149.3)],
}

seen, counts = set(), {k: {'buoys_beacons': 0, 'lights': 0} for k in R}
for path in sys.argv[1:]:
    for line in open(path, encoding='utf-8'):
        f = json.loads(line.lstrip('\x1e'))
        nid = f.get('id')
        if nid in seen:
            continue
        seen.add(nid)
        x, y = f['geometry']['coordinates']
        t = f['properties'].get('seamark:type') or ''
        kind = 'lights' if t.startswith('light') else 'buoys_beacons' if t.startswith(('buoy', 'beacon')) else None
        if not kind:
            continue
        for name, boxes in R.items():
            if any(s <= y <= n and w <= x <= e for s, w, n, e in boxes):
                counts[name][kind] += 1
json.dump(counts, open('counts.json', 'w'), indent=1)
for k, v in counts.items():
    print(f'| {k} | {v["buoys_beacons"]} | {v["lights"]} |')
