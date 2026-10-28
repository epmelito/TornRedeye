import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { catalog, changeLabel, createFeed, filterRows, observe, price, quantity, sourceStatus, supportedChange, validateSnapshot } from '../src/stock.ts';
import type { Snapshot } from '../src/stock.ts';
const fixture = JSON.parse(readFileSync(new URL('../../tests/fixtures/stock-preview.json', import.meta.url), 'utf8')) as Snapshot;
const copy = () => structuredClone(fixture);

test('accepts the existing public contract and rejects malformed/unbounded evidence', () => {
  assert.equal(validateSnapshot(copy()).countries.length, 3);
  const mutations: ((s: Snapshot) => void)[] = [
    s => { s.schema_version = 2 as 1; }, s => { s.countries.push(s.countries[0]); },
    s => { s.countries[0].items[0].quantity = -1; }, s => { s.countries[0].items[0].reported = false; },
    s => { s.countries[0].items[0].comparison.source_interval_utc = ['invalid', 'invalid']; },
    s => { s.countries[0].source_timestamp_utc = '2030-01-01T00:00:00Z'; },
  ];
  for (const mutate of mutations) { const s = copy(); mutate(s); assert.throws(() => validateSnapshot(s)); }
});

test('distinguishes omitted, unreported, missing and observed zero without inventing sale locations', () => {
  const s = copy(), id = s.countries[0].items[0].id;
  assert.equal(observe(s, 'uae', id).label, 'Country omitted');
  assert.equal(observe(s, 'can', id).label, 'Item not reported');
  assert.equal(observe(s, 'can', 1030).quantity, 0);
  assert.equal(observe(s, 'can', 1032).quantity, null);
  s.countries[0].reported = false;
  assert.equal(observe(s, 'arg', id).quantity, null);
  assert.equal(observe(s, 'arg', id).price, null);
  assert.equal(quantity(0), '0'); assert.equal(quantity(null), 'Unknown'); assert.equal(price(0), '$0');
});

test('keeps cached comparisons, conflicts, regressions and source age explicit', () => {
  const i = copy().countries[0].items[0], now = Date.parse(fixture.published_at_utc);
  assert.equal(supportedChange(i), true); assert.equal(changeLabel(i), '+125');
  i.comparison.state = 'cached'; assert.equal(supportedChange(i), true);
  i.comparison.state = 'source_conflict'; assert.equal(supportedChange(i), false); assert.match(changeLabel(i), /Conflicting/);
  i.comparison.state = 'source_regressed'; assert.match(changeLabel(i), /backward/);
  assert.equal(sourceStatus(undefined, now).label, 'Country omitted');
  const c = copy().countries[0]; c.source_timestamp_utc = null; assert.equal(sourceStatus(c, now).label, 'Source time unknown');
  c.source_timestamp_utc = new Date(now + 120000).toISOString(); assert.match(sourceStatus(c, now).label, /ahead/);
  c.source_timestamp_utc = new Date(now - 301000).toISOString(); assert.equal(sourceStatus(c, now).tone, 'warning');
});

test('catalog and numerical filters sort unknown values after reported quantities and costs', () => {
  const s = copy(); const id = s.countries[0].items[0].id;
  assert.equal(catalog(s).length, 9);
  const rows = ['arg','can','uae'].map(code => observe(s, code, id));
  assert.equal(filterRows(rows, 'available', 'quantity').length, 1);
  assert.equal(filterRows(rows, 'unknown', 'price').length, 2);
  assert.equal(filterRows(rows, 'all', 'quantity')[0].code, 'arg');
});

function harness() {
  let time = Date.parse(fixture.published_at_utc), serial = 0, visible = true;
  const timers = new Map<number,{ at: number; fn: () => void }>();
  let resolve: ((r: Response) => void) | null = null, reject: ((e: Error) => void) | null = null;
  const calls: { url: string; signal?: AbortSignal | null; cache?: RequestCache }[] = [];
  const feed = createFeed({ now: () => time, visible: () => visible,
    later: (fn, ms) => { timers.set(++serial, { at: time + ms, fn }); return serial as unknown as ReturnType<typeof setTimeout>; },
    cancel: id => { timers.delete(id as unknown as number); },
    fetch: ((url: string, options: RequestInit) => new Promise<Response>((done, fail) => {
      calls.push({url,...options}); resolve = done; reject = fail;
      options.signal?.addEventListener('abort', () => fail(Error('timeout')));
    })) as typeof fetch,
  });
  return { feed, calls, timers, hide: () => { visible = false; }, show: () => { visible = true; },
    reply: (s: unknown) => { resolve!(new Response(JSON.stringify(s),{status:200})); },
    bad: () => reject!(Error('offline')),
    advance: async (ms: number) => { time += ms; for (const [id,t] of [...timers]) if (t.at <= time) { timers.delete(id); t.fn(); } await new Promise(setImmediate); },
  };
}

test('one relative, bounded request at a time; failed refresh retains the last snapshot and older reports cannot replace it', async () => {
  const h = harness(), first = h.feed.refresh();
  await h.feed.refresh(); assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, 'stock.json'); assert.equal(h.calls[0].cache, 'no-cache');
  h.reply(copy()); await first; assert.equal(h.feed.state().failed, false);
  const good = h.feed.state().snapshot;
  const failed = h.feed.refresh(); h.bad(); await failed;
  assert.equal(h.feed.state().snapshot, good); assert.equal(h.feed.state().failed, true);
  const older = copy(); older.retrieved_at_utc = '2026-10-26T12:00:00Z'; older.countries = [];
  const attempt = h.feed.refresh(); h.reply(older); await attempt;
  assert.equal(h.feed.state().snapshot, good); assert.equal(h.feed.state().failed, false);
  const invalid = h.feed.refresh(); h.reply({ ...copy(), schema_version: 99 }); await invalid;
  assert.equal(h.feed.state().snapshot, good); assert.equal(h.feed.state().failed, true);
  h.feed.stop();
});

test('initial failure, eight-second timeout, hidden-tab checks, stop cleanup and 30-second polling', async () => {
  const h = harness(); let reports = 0; const unsubscribe = h.feed.subscribe(() => reports++);
  const initial = h.feed.refresh(); await h.advance(8000); await initial;
  assert.equal(h.feed.state().snapshot, null); assert.equal(h.feed.state().failed, true);
  h.hide(); await h.feed.refresh(); assert.equal(h.calls.length, 1);
  h.show(); h.feed.start(); h.reply(copy()); await new Promise(setImmediate);
  await h.advance(29999); assert.equal(h.calls.length, 2);
  await h.advance(1); assert.equal(h.calls.length, 3);
  const count = reports; unsubscribe(); h.feed.stop(); h.reply(copy()); await new Promise(setImmediate);
  assert.equal(reports, count); assert.equal(h.timers.size, 0);
});
