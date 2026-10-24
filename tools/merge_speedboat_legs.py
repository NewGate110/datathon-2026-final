"""Replace the inferred legs in the app bundle with the rebuilt speedboat set.

The rebuilt legs (Maldives time, UTC+5) live on branch data/speedboat-routes of the
dashboard repo. This script keeps every MTCC leg in the bundle as it is, drops all
inferred legs, and adds the source's inferred legs whose stops are all in the bundle's
region (bundle stops.json plus the added stops). A multi-stop trip (shared tripId) is
kept whole or dropped whole, never cut. It also adds the new stops to stops.json and
stamps one buildVersion on meta.json and every other file that carries one.

Usage
  python merge_speedboat_legs.py --bundle ../app/public/data --build-version 2026-10-24T20:00+05:00
      [--source-repo ../../../dashboard --source-ref data/speedboat-routes]
      [--source-legs legs.json --source-stops stops.json]

Run build_shapes.py --bundle afterwards so the new stop pairs get water paths.
"""
import argparse, json, os, subprocess, sys
from collections import defaultdict

ADD_STOPS = ['VELANA_AIRPORT']
TIMEZONE_STATUS = 'confirmed: FollowMe device_date is UTC; inferred times shifted to UTC+5'
VERSIONED = ['config.json', 'census.json']


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def write_json(path, data):
    # Same layout as the shipped bundle: indent 1, raw UTF-8, trailing newline.
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False) + '\n')


def source_json(args, name):
    path = getattr(args, f'source_{name}')
    if path:
        return read_json(path)
    out = subprocess.run(['git', '-C', args.source_repo, 'show', f'{args.source_ref}:app/public/data/{name}.json'],
                         check=True, capture_output=True)
    return json.loads(out.stdout.decode('utf-8'))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bundle', required=True, help='app data folder (stops.json, legs.json, meta.json, ...)')
    ap.add_argument('--build-version', required=True, help='new buildVersion, e.g. 2026-10-24T20:00+05:00')
    ap.add_argument('--source-repo', default=os.path.normpath(os.path.join(here, '..', '..', '..', 'dashboard')))
    ap.add_argument('--source-ref', default='data/speedboat-routes')
    ap.add_argument('--source-legs', help='read source legs from this file instead of git')
    ap.add_argument('--source-stops', help='read source stops from this file instead of git')
    args = ap.parse_args()

    p = lambda name: os.path.join(args.bundle, name)
    stops, legs, meta = read_json(p('stops.json')), read_json(p('legs.json')), read_json(p('meta.json'))
    src_stops, src_legs = source_json(args, 'stops'), source_json(args, 'legs')

    # stops: append the added stops exactly as the source has them
    have = {s['id'] for s in stops}
    by_id = {s['id']: s for s in src_stops}
    for sid in ADD_STOPS:
        if sid not in have:
            if sid not in by_id:
                sys.exit(f'source stops.json has no {sid}')
            stops.append(by_id[sid])
    region = {s['id'] for s in stops}

    # legs: official as is, inferred from the source, whole trips only
    official = [l for l in legs if l['source'] == 'mtcc']
    old_inferred = len(legs) - len(official)
    trips = defaultdict(list)
    for l in src_legs:
        if l['source'] == 'inferred':
            trips[l['tripId']].append(l)
    kept, dropped_legs, dropped_trips, outside = [], 0, 0, set()
    for tl in trips.values():
        out = {s for l in tl for s in (l['from'], l['to'])} - region
        if out:
            dropped_trips += 1
            dropped_legs += len(tl)
            outside |= out
        else:
            kept.extend(tl)
    kept_ids = {l['id'] for l in kept}
    kept = [l for l in src_legs if l['id'] in kept_ids]  # keep source order
    multi = sum(1 for tl in trips.values() if len(tl) > 1 and tl[0]['id'] in kept_ids)

    # buildVersion everywhere it appears
    meta['buildVersion'] = args.build_version
    meta['timezoneStatus'] = TIMEZONE_STATUS
    for name in VERSIONED:
        d = read_json(p(name))
        if isinstance(d, dict) and 'buildVersion' in d:
            d['buildVersion'] = args.build_version
            write_json(p(name), d)

    write_json(p('stops.json'), stops)
    write_json(p('legs.json'), official + kept)
    write_json(p('meta.json'), meta)

    print(f'official legs kept: {len(official)}; old inferred removed: {old_inferred}')
    print(f'source inferred: {sum(map(len, trips.values()))} legs in {len(trips)} trips')
    print(f'added: {len(kept)} inferred legs in {len(trips) - dropped_trips} trips ({multi} multi-stop)')
    print(f'dropped out of region: {dropped_legs} legs in {dropped_trips} trips; stops: {", ".join(sorted(outside))}')


if __name__ == '__main__':
    main()
