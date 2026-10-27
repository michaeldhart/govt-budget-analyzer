import { test } from 'node:test';
import assert from 'node:assert/strict';
import { emptyState, inRange, parse, serialize } from '../src/lib/urlParams.ts';

test('an untouched page has an empty query string', () => {
  assert.equal(serialize(emptyState()), '');
  assert.deepEqual(parse(''), emptyState());
});

test('state round-trips, including names with spaces and repeated parameters', () => {
  const s = { from: 2018, to: 2026, hide: ['Utility taxes', 'Other taxes'], table: ['composition', 'heatmap'] };
  const qs = serialize(s);
  assert.equal(qs, 'from=2018&to=2026&hide=Utility+taxes&hide=Other+taxes&table=composition&table=heatmap');
  assert.deepEqual(parse('?' + qs), s);
});

test('bad input is ignored', () => {
  assert.deepEqual(parse('?from=abc&to=20&hide=&hide=a&hide=a'), { from: null, to: null, hide: ['', 'a'], table: [] });
});

test('year range: open ends, and a reversed range is read the right way round', () => {
  assert.ok(inRange(emptyState(), 1999));
  assert.ok(inRange({ from: 2018, to: null }, 2027) && !inRange({ from: 2018, to: null }, 2017));
  assert.ok(inRange({ from: null, to: 2016 }, 2012) && !inRange({ from: null, to: 2016 }, 2017));
  assert.ok(inRange({ from: 2020, to: 2016 }, 2018) && !inRange({ from: 2020, to: 2016 }, 2021));
});
