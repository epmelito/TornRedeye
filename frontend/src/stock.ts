export type Comparison = {
  state: 'unavailable' | 'cached' | 'compared' | 'source_conflict' | 'source_regressed';
  quantity_change: number | null; price_change: number | null;
  source_interval_utc: [string, string] | null;
  retrieval_interval_utc: [string, string] | null;
};
export type Item = { id: number; name: string | null; reported: boolean; quantity: number | null; price: number | null; comparison: Comparison };
export type Country = { code: string; reported: boolean; source_timestamp_utc: string | null; items: Item[] };
export type Snapshot = { kind: 'stock_snapshot'; schema_version: 1; published_at_utc: string; retrieved_at_utc: string; export_timestamp_utc: string | null; state: 'complete' | 'partial' | 'collection_failed' | 'unavailable'; countries: Country[] };
export const COUNTRIES: Record<string, { name: string; short: string }> = {
  mex: { name: 'Mexico', short: 'MEX' }, cay: { name: 'Cayman Islands', short: 'CYM' },
  can: { name: 'Canada', short: 'CAN' }, haw: { name: 'Hawaii', short: 'HAW' },
  arg: { name: 'Argentina', short: 'ARG' }, uni: { name: 'United Kingdom', short: 'GBR' },
  swi: { name: 'Switzerland', short: 'CHE' }, jap: { name: 'Japan', short: 'JPN' },
  chi: { name: 'China', short: 'CHN' }, uae: { name: 'United Arab Emirates', short: 'UAE' },
  sou: { name: 'South Africa', short: 'ZAF' },
};
const object = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x);
const unsigned = (x: unknown): x is number => Number.isSafeInteger(x) && Number(x) >= 0;
export const timestamp = (x: unknown): number => typeof x === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(x) ? Date.parse(x) : NaN;
export function validateSnapshot(value: unknown): Snapshot {
  const fail = () => { throw Error('Invalid stock snapshot'); };
  if (!object(value) || value.kind !== 'stock_snapshot' || value.schema_version !== 1 ||
      !Number.isFinite(timestamp(value.published_at_utc)) || !Number.isFinite(timestamp(value.retrieved_at_utc)) ||
      !['complete', 'partial', 'collection_failed', 'unavailable'].includes(String(value.state)) ||
      !(value.export_timestamp_utc === null || Number.isFinite(timestamp(value.export_timestamp_utc))) ||
      !Array.isArray(value.countries) || value.countries.length > 64) return fail();
  const codes = new Set<string>(); let total = 0;
  for (const c of value.countries) {
    if (!object(c) || typeof c.code !== 'string' || !/^[a-z][a-z0-9_-]{0,15}$/.test(c.code) || codes.has(c.code) ||
        typeof c.reported !== 'boolean' || !(c.source_timestamp_utc === null || Number.isFinite(timestamp(c.source_timestamp_utc)) && timestamp(c.source_timestamp_utc) <= timestamp(value.retrieved_at_utc)) || !Array.isArray(c.items)) return fail();
    codes.add(c.code); const ids = new Set<number>();
    for (const i of c.items) {
      if (!object(i) || !unsigned(i.id) || !i.id || ids.has(i.id) || !(i.name === null || typeof i.name === 'string' && i.name.length <= 128) ||
          typeof i.reported !== 'boolean' || ![i.quantity, i.price].every(n => n === null || unsigned(n)) ||
          !object(i.comparison) || !['unavailable', 'cached', 'compared', 'source_conflict', 'source_regressed'].includes(String(i.comparison.state))) return fail();
      const comp = i.comparison;
      for (const k of ['quantity_change', 'price_change']) if (!(comp[k] === null || Number.isSafeInteger(comp[k]))) return fail();
      for (const k of ['source_interval_utc', 'retrieval_interval_utc']) {
        const pair = comp[k];
        if (!(pair === null || Array.isArray(pair) && pair.length === 2 && pair.every(t => Number.isFinite(timestamp(t))))) return fail();
      }
      if (!i.reported && (i.quantity !== null || i.price !== null)) return fail();
      ids.add(i.id); if (++total > 4096) return fail();
    }
  }
  return value as Snapshot;
}
const numbers = new Intl.NumberFormat('en-US');
const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const clock = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' });
const date = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });
export const quantity = (n: number | null | undefined) => n == null ? 'Unknown' : numbers.format(n);
export const price = (n: number | null | undefined) => n == null ? 'Unknown' : money.format(n);
export const utc = (s: string | null | undefined) => Number.isFinite(timestamp(s)) ? `${clock.format(new Date(s!))} UTC` : 'Unknown';
export const fullDate = (s: string | null | undefined) => Number.isFinite(timestamp(s)) ? `${date.format(new Date(s!))}, ${utc(s)}` : 'Unknown';
export const countryName = (code: string) => COUNTRIES[code]?.name || code.toUpperCase();
export function sourceStatus(c: Country | undefined, now: number) {
  if (!c) return { tone: 'unknown', label: 'Country omitted' };
  if (!c.reported) return { tone: 'unknown', label: 'Not reported' };
  if (c.source_timestamp_utc === null) return { tone: 'unknown', label: 'Source time unknown' };
  const age = now - timestamp(c.source_timestamp_utc);
  if (age < -60000) return { tone: 'warning', label: 'Source clock ahead' };
  if (age >= 86400000) return { tone: 'warning', label: `${numbers.format(Math.floor(age / 86400000))} days old` };
  if (age >= 3600000) return { tone: 'warning', label: `${Math.floor(age / 3600000)} hr old` };
  if (age > 300000) return { tone: 'warning', label: `${Math.floor(age / 60000)} min old` };
  return { tone: 'recent', label: age < 60000 ? 'Under 1 min old' : `${Math.floor(age / 60000)} min old` };
}
export type Observation = { code: string; country?: Country; item?: Item; quantity: number | null; price: number | null; label: string };
export function observe(s: Snapshot, code: string, id: number | null): Observation {
  const country = s.countries.find(c => c.code === code), item = country?.items.find(i => i.id === id);
  const valid = country?.reported && item?.reported;
  const q = valid ? item.quantity : null, p = valid ? item.price : null;
  return { code, country, item, quantity: q, price: p,
    label: !country ? 'Country omitted' : !country.reported ? 'Country not reported' : !item || !item.reported ? 'Item not reported' : q === null ? 'Quantity unknown' : q === 0 ? 'Observed zero' : 'Reported available' };
}
export function catalog(s: Snapshot): { id: number; name: string }[] {
  const items = new Map<number, string>();
  for (const c of s.countries) for (const i of c.items) if (!items.has(i.id) || i.name) items.set(i.id, i.name || `Item ${i.id}`);
  return [...items].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}
export function supportedChange(i: Item | undefined) {
  const c = i?.comparison;
  return Boolean(i?.reported && c && (c.state === 'compared' || c.state === 'cached') && c.source_interval_utc && c.retrieval_interval_utc);
}
export function changeLabel(i: Item | undefined) {
  if (!i?.reported) return 'Not reported';
  const c = i.comparison;
  if (c.state === 'source_conflict') return 'Conflicting source report';
  if (c.state === 'source_regressed') return 'Source time moved backward';
  if (c.state === 'cached' && !supportedChange(i)) return 'Same source report';
  if (!supportedChange(i) || c.quantity_change === null) return 'No comparison';
  return c.quantity_change === 0 ? 'No net change' : `${c.quantity_change > 0 ? '+' : '\u2212'}${numbers.format(Math.abs(c.quantity_change))}`;
}
export function filterRows(rows: Observation[], availability: string, sort: string) {
  return rows.filter(o => availability === 'all' || availability === 'available' && o.quantity !== null && o.quantity > 0 || availability === 'zero' && o.quantity === 0 || availability === 'unknown' && o.quantity === null)
    .sort((a, b) => sort === 'quantity' ? (b.quantity ?? -1) - (a.quantity ?? -1) : sort === 'price' ? (a.price ?? Infinity) - (b.price ?? Infinity) : 0);
}
export type FeedState = { snapshot: Snapshot | null; busy: boolean; failed: boolean };
export function createFeed(options: {
  fetch: typeof fetch; visible: () => boolean; now: () => number;
  later: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  cancel: (id: ReturnType<typeof setTimeout>) => void;
}) {
  let state: FeedState = { snapshot: null, busy: false, failed: false }, stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined;
  const listeners = new Set<(s: FeedState) => void>();
  const emit = () => { for (const listener of listeners) listener(state); };
  async function refresh() {
    if (stopped || state.busy || !options.visible()) return;
    controller = new AbortController(); const active = controller;
    state = { ...state, busy: true }; emit();
    const timeout = options.later(() => active.abort(), 8000);
    try {
      const response = await options.fetch('stock.json', { cache: 'no-cache', signal: active.signal });
      if (!response.ok) throw Error('Snapshot request failed');
      const text = await response.text(); if (text.length > 256 * 1024) throw Error('Stock snapshot too large');
      const next = validateSnapshot(JSON.parse(text));
      if (!stopped && (!state.snapshot || timestamp(next.retrieved_at_utc) >= timestamp(state.snapshot.retrieved_at_utc))) state = { ...state, snapshot: next, failed: false };
      else if (!stopped) state = { ...state, failed: false };
    } catch { if (!stopped) state = { ...state, failed: true }; }
    finally { options.cancel(timeout); if (!stopped) { state = { ...state, busy: false }; emit(); } }
  }
  async function tick() { await refresh(); if (!stopped) timer = options.later(() => { void tick(); }, 30000); }
  return {
    state: () => state,
    subscribe(listener: (s: FeedState) => void) { listeners.add(listener); listener(state); return () => { listeners.delete(listener); }; },
    start() { void tick(); }, refresh,
    stop() { stopped = true; if (timer !== undefined) options.cancel(timer); controller?.abort(); listeners.clear(); },
  };
}
