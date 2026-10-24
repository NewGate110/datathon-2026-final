"""Add MTCC trips from a timetable CSV to the app bundle's legs.json.

The CSV uses the timetable_source.csv schema
(route,trip_id,block_id,days,seq,stop,arrive,depart), for example the CTN 2022
transcription ctn_timetable_source.csv. Each listed trip becomes one leg per
consecutive stop pair, all sharing one tripId (one boat). Stop names are matched to
stops.json by name, ignoring case, accents and punctuation. validFrom/validTo are
copied from the bundle's existing MTCC legs of the same route. Trips already in the
bundle (same tripId) are replaced, so the script can be run again.

Usage
  python add_mtcc_trips.py --bundle ../app/public/data --csv ctn_timetable_source.csv
      --trip 307_SATMONWED [--trip ...] [--build-version 2026-10-24T21:00+05:00]
"""
import argparse, csv, json, os, re, sys, unicodedata

DAY = {'Sun': 0, 'Mon': 1, 'Tue': 2, 'Wed': 3, 'Thu': 4, 'Fri': 5, 'Sat': 6}
VERSIONED = ['config.json', 'census.json']


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, data):
    # Same layout as the shipped bundle: indent 1, raw UTF-8, trailing newline.
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False) + '\n')


def key(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]', '', s)


def minutes(hhmm):
    h, m = hhmm.split(':')
    return int(h) * 60 + int(m)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--csv', required=True)
    ap.add_argument('--trip', action='append', required=True, help='trip_id in the CSV; repeat for several')
    ap.add_argument('--build-version', help='new buildVersion for meta.json and every file that has one')
    args = ap.parse_args()

    p = lambda name: os.path.join(args.bundle, name)
    stops, legs = read_json(p('stops.json')), read_json(p('legs.json'))
    stop_id = {key(s['name']): s['id'] for s in stops}
    with open(args.csv, encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if r['trip_id'] in args.trip]

    added = []
    for trip in args.trip:
        tr = sorted((r for r in rows if r['trip_id'] == trip), key=lambda r: int(r['seq']))
        if len(tr) < 2:
            sys.exit(f'{args.csv}: trip {trip} has fewer than two stops')
        route = tr[0]['route']
        same_route = [l for l in legs if l['source'] == 'mtcc' and l['routeId'] == route]
        if not same_route:
            sys.exit(f'bundle has no MTCC legs on route {route} to copy validFrom/validTo from')
        days = sorted(DAY[d] for d in tr[0]['days'].split())
        legs = [l for l in legs if l['tripId'] != trip]
        for i, (a, b) in enumerate(zip(tr, tr[1:]), 1):
            ids = []
            for r in (a, b):
                if key(r['stop']) not in stop_id:
                    sys.exit(f'{args.csv}: stop {r["stop"]!r} of trip {trip} is not in stops.json')
                ids.append(stop_id[key(r['stop'])])
            dep, arr = minutes(a['depart']), minutes(b['arrive'])
            added.append({'id': f'mtcc-{trip}:r{i:02d}', 'source': 'mtcc', 'routeId': route, 'tripId': trip,
                          'from': ids[0], 'to': ids[1], 'dep': {'min': dep, 'max': dep},
                          'arr': {'min': arr, 'max': arr}, 'days': days,
                          'validFrom': same_route[0]['validFrom'], 'validTo': same_route[0]['validTo'],
                          'cls': None, 'evidence': None})

    # new legs go after the last MTCC leg, before the inferred ones
    last = max(i for i, l in enumerate(legs) if l['source'] == 'mtcc')
    legs = legs[:last + 1] + added + legs[last + 1:]
    write_json(p('legs.json'), legs)

    if args.build_version:
        meta = read_json(p('meta.json'))
        meta['buildVersion'] = args.build_version
        write_json(p('meta.json'), meta)
        for name in VERSIONED:
            d = read_json(p(name))
            if isinstance(d, dict) and 'buildVersion' in d:
                d['buildVersion'] = args.build_version
                write_json(p(name), d)

    for l in added:
        print(f'{l["id"]}: {l["from"]} {l["dep"]["min"]} -> {l["to"]} {l["arr"]["min"]} days {l["days"]}')


if __name__ == '__main__':
    main()
