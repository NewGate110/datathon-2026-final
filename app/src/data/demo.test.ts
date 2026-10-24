import { describe, expect, it } from 'vitest';
import stops from '../../public/data/stops.json';
import legs from '../../public/data/legs.json';
import config from '../../public/data/config.json';
import census from '../../public/data/census.json';
import meta from '../../public/data/meta.json';
import { validateBundle } from './load';
import {
  applyCancellation,
  compareReachability,
  MTCC_APPROVAL_NOTE,
  proposeShifts,
} from '../router/disruption';
import { findJourneys } from '../router/planner';
import { expandOccurrences } from '../router/time';
import type { Connection } from '../router/model';

// Demo on the real bundle: Thoddoo to Malé on Sunday 2026-10-25, then cancel Rasdhoo to Malé 11:00.
describe('demo disruption on the real bundle', () => {
  const bundle = validateBundle({ stops, legs, config, census, meta });
  const query = { origin: 'THODDOO', destination: 'MALE', date: '2026-10-25', earliestDep: 360 };
  // The inferred boats via Rasdhoo arrive hours earlier and fill the top results, so the
  // official journey is checked on the official-only view.
  const officialQuery = { ...query, officialOnly: true };
  const DATE = query.date;
  const toRas = `mtcc-cfn_2026_all:p28:b2:r01@${DATE}`; // route 303 Thoddoo 06:30 -> Rasdhoo 07:40
  const rasMale = 'mtcc-cfn_2026_all:p28:b2:r04'; // route 303 Rasdhoo 11:00 -> Malé 14:10
  const cancelled = `${rasMale}@${DATE}`;
  // inferred Thoddoo 06:33 -> Rasdhoo, change, Rasdhoo 07:27 -> Malé
  const viaRas = `inf-sb-THODDOO-RASDHOO-0633@${DATE}>inf-sb-RASDHOO-MALE-0727@${DATE}`;
  const official = `${toRas}>${cancelled}`;
  const baseline = () => expandOccurrences(bundle.legs, DATE, bundle.config.horizonHours);

  it('the cancelled occurrence is route 303 Rasdhoo to Malé at 11:00', () => {
    const leg = bundle.legs.find((l) => l.id === rasMale)!;
    expect(leg).toMatchObject({ source: 'mtcc', routeId: '303', from: 'RASDHOO', to: 'MALE' });
    expect(leg.dep).toEqual({ min: 660, max: 660 });
    expect(leg.arr).toEqual({ min: 850, max: 850 });
    expect(baseline().some((o) => o.id === cancelled)).toBe(true);
  });

  it('baseline: official change at Rasdhoo is a long wait; inferred boats via Rasdhoo are offered', () => {
    const off = findJourneys(bundle, officialQuery, baseline()).find((j) => j.id === official)!;
    expect(off).toBeDefined();
    expect(off.dep).toEqual({ min: 390, max: 390 });
    expect(off.arr).toEqual({ min: 850, max: 850 });
    expect(off.transfers).toHaveLength(1);
    const t = off.transfers[0];
    expect(t.stop).toBe('RASDHOO');
    expect(off.rides[0].arr).toEqual({ min: 460, max: 460 });
    expect(t.assessment).toMatchObject({ status: 'safe', waitMin: [200, 200], long: true });

    const journeys = findJourneys(bundle, query, baseline());
    const inf = journeys.find((j) => j.id === viaRas)!;
    expect(inf).toBeDefined();
    expect(inf.usesInferred).toBe(true);
    expect(inf.transfers).toHaveLength(1);
    expect(inf.transfers[0]).toMatchObject({ stop: 'RASDHOO', assessment: { status: 'safe' } });
    expect(inf.dep).toEqual({ min: 393, max: 410 });
    expect(inf.arr).toEqual({ min: 502, max: 515 });
    expect(inf.rides[0].occurrences[0].leg.evidence).toMatchObject({ devices: 4, days: 19 });
    expect(journeys[0].id).toBe(viaRas);
  });

  it('cancelling Rasdhoo to Malé 11:00 loses the official journey, with no same-day inferred replacement', () => {
    const offResult = compareReachability(bundle, officialQuery, cancelled);
    expect(offResult.lostJourneys.map((j) => j.id)).toEqual([official]);
    const result = compareReachability(bundle, query, cancelled);
    for (const j of [...offResult.after, ...result.after]) {
      expect(j.id.includes(cancelled)).toBe(false);
    }
    expect(
      result.brokenConnections.some((c) => c.arriving.id === toRas && c.departing.id === cancelled),
    ).toBe(true);
    expect(result.newBest?.id).toBe(viaRas);
    expect(result.recovery.every((j) => j.usesInferred)).toBe(true);

    // No inferred Rasdhoo -> Malé boat on the same day at or after 10:55.
    const disrupted = applyCancellation(baseline(), cancelled);
    const rasInf = disrupted.filter(
      (o) =>
        o.leg.source === 'inferred' &&
        o.leg.from === 'RASDHOO' &&
        o.leg.to === 'MALE' &&
        o.serviceDate === DATE,
    );
    expect(rasInf.every((o) => o.dep.min < 655)).toBe(true);
    expect(rasInf.map((o) => o.dep)).toEqual([
      { min: 393, max: 405 },
      { min: 447, max: 458 },
      { min: 453, max: 464 },
    ]);
  });

  it('shift proposals: none offered in the recovery, and any proposal is a bounded MTCC recommendation', () => {
    const result = compareReachability(bundle, query, cancelled);
    const disrupted = applyCancellation(baseline(), cancelled);
    // DisruptionPanel only asks for shifts on non-Safe transfers fed by an MTCC ride in the recovery.
    const targets = result.recovery.flatMap((j) =>
      j.transfers.filter(
        (t) => t.assessment.status !== 'safe' && j.rides[t.fromRide].source === 'mtcc',
      ),
    );
    expect(targets).toEqual([]);

    // The baseline Thoddoo -> Rasdhoo -> 11:00 connection is already Safe. Whether proposeShifts
    // returns nothing for a Safe target or returns shifts that keep it Safe, these invariants hold.
    const occs = baseline();
    const off = findJourneys(bundle, officialQuery, occs).find((j) => j.id === official)!;
    const t = off.transfers[0];
    const target: Connection = {
      stop: t.stop,
      arriving: off.rides[t.fromRide].occurrences.at(-1)!,
      departing: off.rides[t.toRide].occurrences[0],
      assessment: t.assessment,
    };
    for (const shifts of [
      proposeShifts(bundle, occs, target),
      proposeShifts(bundle, disrupted, target),
    ]) {
      expect(shifts.rejected).toEqual([]);
      for (const p of shifts.proposals) {
        expect(p.occurrenceId).toBe(toRas);
        expect(Math.abs(p.shiftMin) % bundle.config.shiftStepMin).toBe(0);
        expect(p.shiftMin).not.toBe(0);
        expect(Math.abs(p.shiftMin)).toBeLessThanOrEqual(bundle.config.maxShiftMin);
        expect(p.note).toBe(MTCC_APPROVAL_NOTE);
      }
    }
  });

  it('reset: the baseline bundle is unchanged after the cancellation', () => {
    const before = JSON.stringify(bundle);
    const beforeJourneys = findJourneys(bundle, query, baseline()).map((j) => j.id);
    compareReachability(bundle, query, cancelled);
    applyCancellation(baseline(), cancelled);
    expect(JSON.stringify(bundle)).toBe(before);
    expect(baseline().some((o) => o.id === cancelled)).toBe(true);
    expect(findJourneys(bundle, query, baseline()).map((j) => j.id)).toEqual(beforeJourneys);
  });
});
