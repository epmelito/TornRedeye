import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { COUNTRIES, catalog, changeLabel, countryName, createFeed, filterRows, fullDate, observe, price, quantity, sourceStatus, supportedChange, timestamp, utc } from './stock';
import type { Country, FeedState, Item, Observation, Snapshot } from './stock';
import { Icon, ItemArt, Logo } from './ui';

type Route = { mode: 'items' | 'countries'; country: string; item: number | null; query: string; availability: string; sort: string; panel: 'destinations' | 'timeline'; saved: boolean };
function readRoute(): Route {
  const q = new URLSearchParams(location.search), hash = location.hash.slice(1);
  const id = Number(q.get('item'));
  return { mode: q.get('mode') === 'countries' ? 'countries' : 'items', country: q.get('country') || (COUNTRIES[hash] ? hash : 'jap'),
    item: Number.isSafeInteger(id) && id > 0 ? id : null, query: q.get('q') || '', availability: ['all', 'available', 'zero', 'unknown'].includes(q.get('availability') || '') ? q.get('availability')! : 'all', sort: ['destination', 'quantity', 'price'].includes(q.get('sort') || '') ? q.get('sort')! : 'destination', panel: q.get('panel') === 'timeline' ? 'timeline' : 'destinations', saved: q.get('saved') === '1' };
}
function routeURL(route: Route) {
  const q = new URLSearchParams({ mode: route.mode, country: route.country, q: route.query, availability: route.availability, sort: route.sort, panel: route.panel, saved: route.saved ? '1' : '0' });
  if (route.item) q.set('item', String(route.item));
  return `?${q}`;
}
function useStock() {
  const [state, setState] = useState<FeedState>({ snapshot: null, busy: true, failed: false });
  const feed = useRef<ReturnType<typeof createFeed> | null>(null);
  useEffect(() => {
    const f = createFeed({ fetch: window.fetch.bind(window), visible: () => !document.hidden, now: Date.now, later: (fn, ms) => setTimeout(fn, ms), cancel: id => clearTimeout(id) });
    feed.current = f; const unsubscribe = f.subscribe(setState); f.start();
    const visible = () => { if (!document.hidden) void f.refresh(); };
    document.addEventListener('visibilitychange', visible);
    return () => { unsubscribe(); f.stop(); document.removeEventListener('visibilitychange', visible); };
  }, []);
  return { ...state, refresh: () => feed.current?.refresh() };
}
function Tone({ country, now }: { country?: Country; now: number }) { const s = sourceStatus(country, now); return <span className={`source-tone ${s.tone}`}>
<i/>{s.label}</span>; }
function Amount({ observation: o }: { observation: Observation }) { return <span className={`amount ${o.quantity === null ? 'unknown' : o.quantity === 0 ? 'zero' : 'positive'}`}>{quantity(o.quantity)}{o.quantity === 0 && <span className="sr-only">Observed zero</span>}</span>; }
function Change({ item }: { item?: Item }) { return <span className={`change ${supportedChange(item) && (item?.comparison.quantity_change || 0) > 0 ? 'positive' : ''}`}>{changeLabel(item)}{supportedChange(item) && item?.comparison.state === 'cached' && <small>Retained change</small>}</span>; }
function Evidence({ o, snapshot, now }: { o: Observation; snapshot: Snapshot; now: number }) {
  const comp = o.item?.comparison;
  return <>
<dl className="provenance">
<div>
<dt>Source report</dt>
<dd>{fullDate(o.country?.source_timestamp_utc)}</dd>
</div>
<div>
<dt>Collected</dt>
<dd>{fullDate(snapshot.retrieved_at_utc)}</dd>
</div>
<div>
<dt>Published</dt>
<dd>{fullDate(snapshot.published_at_utc)}</dd>
</div>
</dl>
    {o.label.includes('not reported') || o.label === 'Country omitted' ? <p className="detail-note">{o.label}. This does not establish zero stock or that this item is sold here. Retained names, where present, are last reported labels.</p> : o.quantity === null ? <p className="detail-note warning">The report does not supply a quantity. Unknown is not zero.</p> : null}
    {sourceStatus(o.country, now).tone !== 'recent' && <p className="detail-note warning">{sourceStatus(o.country, now).label}. Verify stock in game before relying on this report.</p>}
    <h3>{comp?.state === 'cached' ? 'Last supported change' : 'Change between reports'}</h3>
    {supportedChange(o.item) ? <>
<p className="interval">{fullDate(comp!.source_interval_utc![0])}<br/>
<span>to</span> {fullDate(comp!.source_interval_utc![1])}</p>
<p className="detail-change">
<Change item={o.item}/>
</p>
<p className="detail-note">Shop price change: {comp!.price_change === null ? 'unknown' : comp!.price_change === 0 ? 'unchanged' : price(comp!.price_change)}.</p>
<p className="detail-note">{comp?.state === 'cached' ? 'Retained on the same source report. ' : ''}Net change within this interval, not an exact restock time or a sales count.</p>
<details>
<summary>Collection interval</summary>
<p>{comp!.retrieval_interval_utc!.map(fullDate).join(' to ')}</p>
</details>
</> : <p className="detail-note">{changeLabel(o.item)}. Source gaps, conflicting reports and missing values can prevent comparison.</p>}</>;
}
function Timeline({ observation: o }: { observation: Observation }) {
  const [forecast, setForecast] = useState(false), [table, setTable] = useState(false);
  const pair = o.item?.comparison.source_interval_utc;
  const q = o.item?.comparison.quantity_change;
  const before = o.quantity !== null && q !== null && q !== undefined ? o.quantity - q : null;
  const valid = supportedChange(o.item) && before !== null && Number.isSafeInteger(before) && before >= 0 && pair && timestamp(pair[1]) > timestamp(pair[0]);
  const max = Math.max(1, before || 0, o.quantity || 0), y = (n: number) => 110 - n / max * 72;
  return <section className="timeline-section" id="timeline" aria-labelledby="timeline-title">
<div className="timeline-title">
<h2 id="timeline-title">Stock observations</h2>
<span>{countryName(o.code)}</span>
</div>
<div className="timeline-tools">
<div className="segmented" aria-label="Observation view">
<button type="button" aria-pressed={!forecast} onClick={() => setForecast(false)}>Supported observations</button>
<button type="button" aria-pressed={forecast} onClick={() => setForecast(true)}>Forecasts <span>Unavailable</span>
</button>
</div>{valid && !forecast && <button className="quiet" type="button" aria-pressed={table} onClick={() => setTable(!table)}>{table ? 'View endpoints' : 'View as table'}</button>}</div>
    {forecast ? <div className="history-empty">
<Icon name="timeline" size={26}/>
<h3>No validated forecasts available</h3>
<p>Forecast issuances and measured outcomes will appear here only after separately validated outputs are integrated.</p>
<label>Forecast issuance<select disabled aria-label="Forecast issuance">
<option>No forecasts issued</option>
</select>
</label>
</div> : valid ? <>
<p className="history-help">Two supported source-report endpoints. Intervening quantities and changes are unknown; no historical archive is loaded.</p>{table ? <table className="endpoint-table">
<caption>Source-report comparison endpoints</caption>
<thead>
<tr>
<th scope="col">Source report</th>
<th scope="col">Quantity</th>
</tr>
</thead>
<tbody>
<tr>
<th scope="row">{fullDate(pair[0])}</th>
<td>{quantity(before)}</td>
</tr>
<tr>
<th scope="row">{fullDate(pair[1])}</th>
<td>{quantity(o.quantity)}</td>
</tr>
</tbody>
</table> : <svg className="endpoint-chart" viewBox="0 0 600 164" role="img" aria-label={`Two supported quantities: ${quantity(before)} at ${fullDate(pair[0])}; ${quantity(o.quantity)} at ${fullDate(pair[1])}. Quantities between reports are unknown.`}>
<path className="grid" d="M42 38h516M42 74h516M42 110h516"/>
<text x="42" y="20">Reported quantity</text>
<circle cx="70" cy={y(before!)} r="5"/>
<circle cx="526" cy={y(o.quantity!)} r="5"/>
<text x="83" y={y(before!) + 18}>{quantity(before)}</text>
<text x="513" y={y(o.quantity!) + 18} textAnchor="end">{quantity(o.quantity)}</text>
<text x="70" y="143">{utc(pair[0])}</text>
<text x="526" y="143" textAnchor="end">{utc(pair[1])}</text>
<text x="300" y="79" textAnchor="middle" className="gap-label">Unobserved interval</text>
</svg>}</> : <div className="history-empty">
<Icon name="timeline" size={25}/>
<h3>No comparable source observations</h3>
<p>{changeLabel(o.item)}. This snapshot does not provide a historical timeline for this selection.</p>
</div>}</section>;
}
export function App() {
  const feed = useStock(), snapshot = feed.snapshot;
  const [now, setNow] = useState(Date.now), [route, setRoute] = useState(readRoute), [mobileDetail, setMobileDetail] = useState<string | null>(null);
  const timelineScroll = useRef(route.panel === 'timeline'), snapshotReady = snapshot !== null;
  // Mobile CSS reveals this target after the route commit; direct links also need data.
  useLayoutEffect(() => {
    if (route.panel !== 'timeline') timelineScroll.current = false;
    if (timelineScroll.current && snapshotReady) {
      document.getElementById('timeline')?.scrollIntoView({ block: 'start' });
      timelineScroll.current = false;
    }
  }, [route, snapshotReady]);
  const [saved, setSaved] = useState<number[]>(() => { try { const v: unknown = JSON.parse(localStorage.getItem('tornredeye-saved') || '[]'); return Array.isArray(v) ? v.filter((n): n is number => Number.isSafeInteger(n) && n > 0).slice(0,4096) : []; } catch { return []; } });
  const [saveError, setSaveError] = useState('');
  const [mobile, setMobile] = useState(() => matchMedia('(max-width:700px)').matches);
  useEffect(() => { const m = matchMedia('(max-width:700px)'); const resized = () => setMobile(m.matches); m.addEventListener('change', resized); return () => m.removeEventListener('change', resized); }, []);
  const menu = useRef<HTMLDialogElement>(null);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 10000); const back = () => { setRoute(readRoute()); setMobileDetail(null); }; window.addEventListener('popstate', back); window.addEventListener('hashchange', back); return () => { clearInterval(timer); window.removeEventListener('popstate', back); window.removeEventListener('hashchange', back); }; }, []);
  const items = useMemo(() => snapshot ? catalog(snapshot) : [], [snapshot]);
  const preferredId = route.item ?? (items.some(i => i.id === 206) ? 206 : items[0]?.id ?? null);
  const id = route.saved && saved.length && !saved.includes(preferredId || 0) ? saved[0] : preferredId;
  const itemName = items.find(i => i.id === id)?.name || (id ? `Item ${id}` : 'Overseas stock');
  const codes = [...new Set([...Object.keys(COUNTRIES), ...snapshot?.countries.map(c => c.code) || []])];
  const country = codes.includes(route.country) ? route.country : codes[0];
  function navigate(patch: Partial<Route>, push = false) { const next = { ...route, item: id, country, ...patch }; history[push ? 'pushState' : 'replaceState'](null, '', routeURL(next)); setRoute(next); }
  function saveItem() { if (!id) return; const next = saved.includes(id) ? saved.filter(i => i !== id) : [...saved,id]; try { localStorage.setItem('tornredeye-saved', JSON.stringify(next)); setSaved(next); setSaveError(''); } catch { setSaveError('Saving items is unavailable in this browser.'); } }
  const focused = snapshot ? observe(snapshot, country, id) : null;
  let rows: Observation[] = [];
  if (snapshot) rows = route.mode === 'items' ? codes.map(code => observe(snapshot, code, id)) : (snapshot.countries.find(c => c.code === country)?.items || []).map(i => observe(snapshot, country, i.id)).filter(o => (o.item?.name || `Item ${o.item?.id}`).toLowerCase().includes(route.query.toLowerCase()) && (!route.saved || saved.includes(o.item!.id)));
  rows = route.saved && id && !saved.includes(id) ? [] : filterRows(rows, route.availability, route.sort);
  const matchingItems = items.filter(i => i.name.toLowerCase().includes(route.query.toLowerCase()) && (!route.saved || saved.includes(i.id)));
  const delayed = snapshot && (now - timestamp(snapshot.published_at_utc) > 120000 || now - timestamp(snapshot.retrieved_at_utc) > 120000);
  const ahead = snapshot && (timestamp(snapshot.published_at_utc) - now > 60000 || timestamp(snapshot.retrieved_at_utc) - now > 60000);
  const preview = document.querySelector<HTMLMetaElement>('meta[name="stock-preview"]')?.content;
  const archive = preview || '';
  const nav = <>
<div className="rail-heading">
<h2>Destinations</h2>
<span>{codes.length}</span>
</div>
<p className="rail-help">{route.mode === 'items' ? 'Compare availability by item.' : 'Choose your overseas shop.'}</p>
<nav className="destinations" aria-label="Destinations">{codes.map(code => <a key={code} href={routeURL({ ...route, country: code, item: id })} aria-current={country === code ? 'location' : undefined} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ country: code }); menu.current?.close(); setMobileDetail(null); }}>
<span className="country-code">{COUNTRIES[code]?.short || code.toUpperCase()}</span>
<span className="destination-name">{countryName(code)}<small>{snapshot ? sourceStatus(snapshot.countries.find(c => c.code === code), now).label : 'Awaiting snapshot'}</small>
</span>{snapshot && <i className={`status-dot ${sourceStatus(snapshot.countries.find(c => c.code === code), now).tone}`}/>}</a>)}</nav>
<div className="rail-legend">
<span>
<i className="status-dot recent"/>Recent source report</span>
<span>
<i className="status-dot warning"/>Stale or clock warning</span>
<span>
<i className="status-dot unknown"/>Unknown or omitted</span>
</div>
</>;
  function renderEvidence(o: Observation) { return <>
<header className="detail-header">
<span className="country-code">{COUNTRIES[o.code]?.short || o.code}</span>
<div>
<h2>{route.mode === 'countries' ? o.item?.name || itemName : itemName}</h2>
<span>{countryName(o.code)}</span>
</div>
</header>
<div className="detail-amounts">
<div>
<Amount observation={o}/>
<span>Reported quantity</span>
<span className="observation-label">{o.label}</span>
</div>
<div>
<strong>{price(o.price)}</strong>
<span>Shop price (T$)</span>
</div>
</div>
<Evidence o={o} snapshot={snapshot!} now={now}/>
<a className="detail-timeline" href={routeURL({ ...route, country: o.code, panel: 'timeline', item: o.item?.id || id })} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); timelineScroll.current = true; navigate({ country: o.code, item: o.item?.id || id, panel: 'timeline' }); }}>
<Icon name="timeline" size={16}/>View {countryName(o.code)} observations</a>{route.mode === 'countries' && <button className="text-button" type="button" onClick={() => navigate({ mode: 'items', item: o.item?.id || id, query: '', panel: 'destinations' },true)}>Compare this item across destinations</button>}</>; }
  return <>
<a href="#main" className="skip">Skip to stock</a>{archive && <div className="archive-banner">
<Icon name="info" size={17}/>{archive}</div>}<div className="app-frame">
<header className="topbar">
<button type="button" className="mobile-menu icon-button" aria-label="Choose destination" onClick={() => menu.current?.showModal()}>
<Icon name="menu"/>
</button>
<a className="brand" href="stock.html" aria-label="TornRedeye stock home">
<Logo/>
</a>
<nav className="primary-nav" aria-label="Explore stock">{(['items','countries'] as const).map(mode => <a key={mode} href={routeURL({ ...route, country, item: id, mode, query: '', saved: false, panel: 'destinations' })} aria-current={route.mode === mode && !route.saved ? 'page' : undefined} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ mode, query: '', saved: false, panel: 'destinations' },true); }}>Explore {mode}</a>)}</nav>
<div className="header-status">
<i className={`status-dot ${feed.failed || delayed || ahead ? 'warning' : snapshot ? 'recent' : 'unknown'}`}/>
<div>
<span>{feed.failed ? 'Update failed' : ahead ? 'Clock warning' : delayed ? 'Updates delayed' : snapshot ? 'Snapshot received' : 'Loading snapshot…'}</span>
<time>{snapshot ? utc(snapshot.published_at_utc) : 'Publication unknown'}</time>
</div>
</div>
<span className="coverage-badge">{snapshot?.state === 'partial' ? 'Partial snapshot' : snapshot?.state === 'complete' ? 'Reported snapshot' : snapshot ? 'Source unavailable' : 'Connecting'}</span>
<button className="icon-button refresh" type="button" aria-label="Check for stock updates" disabled={feed.busy} onClick={() => void feed.refresh()}>
<Icon name="refresh"/>
</button>
</header>
    <div className="app-layout">
<aside className="country-rail">{nav}</aside>
<main id="main">
<header className="workspace-header">
<div className="identity">
<ItemArt name={route.mode === 'items' ? itemName : ''}/>
<div>
<div className="title-row">
<h1>{route.saved ? 'Saved items' : route.mode === 'items' ? itemName : countryName(country)}</h1>
{route.mode === 'items' && <button type="button" className="mobile-save icon-button" aria-label={`${id && saved.includes(id) ? 'Unsave' : 'Save'} ${itemName}`} aria-pressed={Boolean(id && saved.includes(id))} disabled={!id} onClick={saveItem}>
<Icon name="star" size={17}/>
</button>}
</div>
<p>{route.mode === 'items' ? `Reported shop stock · ${codes.length} destination comparisons` : `Overseas shop observations - ${snapshot ? sourceStatus(snapshot.countries.find(c => c.code === country), now).label : 'Awaiting snapshot'}`}</p>
</div>
</div>
<div className="identity-actions">
<a className="outlined" href={routeURL({ ...route, mode: route.mode === 'items' ? 'countries' : 'items', item: id, query: '' })} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ mode: route.mode === 'items' ? 'countries' : 'items', query: '' },true); }}>
<Icon name="arrow" size={16}/>
<span>{route.mode === 'items' ? `${countryName(country)} shop` : 'Compare destinations'}</span>
</a>
{route.mode === 'items' && <button className={`save-button ${id && saved.includes(id) ? 'is-saved' : ''}`} type="button" aria-pressed={Boolean(id && saved.includes(id))} disabled={!id} onClick={saveItem}>
<Icon name="star" size={16}/>{id && saved.includes(id) ? 'Saved' : 'Save'}</button>}
</div>
</header>
    <div className={`finder ${route.mode === 'countries' ? 'country-finder' : ''}`}>{route.mode === 'items' && <label className="item-select">
<span>Item</span>
<select name="item" value={id || ''} aria-label="Choose stock item" onChange={e => { navigate({ item: Number(e.target.value), query: '', saved: false }); setMobileDetail(null); }}>{items.length === 0 ? <option value="">No items reported</option> : <>{id && !items.some(i => i.id === id) && <option value={id}>Item {id} - Not reported</option>}{items.map(i => <option value={i.id} key={i.id}>{i.name}</option>)}</>}</select>
</label>}<label className="search-field">
<span>{route.mode === 'items' ? 'Find another item' : 'Search this shop'}</span>
<div>
<Icon name="search" size={18}/>
<input type="search" name="search" autoComplete="off" value={route.query} onChange={e => navigate({ query: e.target.value })} placeholder={route.mode === 'items' ? 'Search for another item…' : 'Search shop items…'}/>
</div>
</label>
<label className="availability">
<span>Availability</span>
<select name="availability" value={route.availability} onChange={e => navigate({ availability: e.target.value })}>
<option value="all">All observations</option>
<option value="available">Reported available</option>
<option value="zero">Observed zero</option>
<option value="unknown">Unknown</option>
</select>
</label>
<label className="sort">
<span>Sort by</span>
<select name="sort" value={route.sort} onChange={e => navigate({ sort: e.target.value })}>
<option value="destination">{route.mode === 'items' ? 'Destination' : 'Shop order'}</option>
<option value="quantity">Highest quantity</option>
<option value="price">Lowest shop price</option>
</select>
</label>
</div>
    {route.mode === 'items' && (route.query || route.saved) && <div className="search-results" aria-live="polite">{matchingItems.length ? matchingItems.slice(0, 12).map(i => <button type="button" key={i.id} aria-pressed={i.id === id} onClick={() => navigate({ item: i.id, query: '' })}>{i.name}</button>) : <p>{route.saved ? 'No saved items match. Save an item from its heading, or browse all items.' : 'No matching items. Try a shorter name; your selected item is unchanged.'}</p>}{matchingItems.length > 12 && <p>Showing 12 of {matchingItems.length} matches. Keep typing to narrow your search.</p>}</div>}
    <div className="report-line" role="status">{feed.failed ? <p className="warning">{snapshot ? 'Update failed. Showing the last received snapshot; its timestamps continue to age.' : 'Stock could not load. Retrying automatically; you can check for updates above.'}</p> : !snapshot ? <p>Loading reported stock…</p> : snapshot.state === 'collection_failed' ? <p className="warning">Latest collection failed. Current quantities and prices are unknown.</p> : snapshot.state === 'unavailable' ? <p className="warning">Latest source export was unusable. Current quantities and prices are unknown.</p> : <p>{snapshot.state === 'partial' ? 'Partial coverage. Missing values are unknown, not zero.' : 'Observed stock can change before you arrive.'}{delayed ? ' Publication or collection is delayed.' : ''}{ahead ? ' A timestamp is ahead of your device clock.' : ''}</p>}<details className="source-details">
<summary>Source details</summary>{snapshot ? <div>
<dl>
<dt>Published</dt>
<dd>{fullDate(snapshot.published_at_utc)}</dd>
<dt>Collected</dt>
<dd>{fullDate(snapshot.retrieved_at_utc)}</dd>
<dt>Export timestamp</dt>
<dd>{fullDate(snapshot.export_timestamp_utc)}</dd>
</dl>
<p>Source times vary by country. Ages use your device clock. Checks run every 30 seconds while visible.</p>
</div> : <p>No stock snapshot received.</p>}</details>
</div>{saveError && <p className="warning" role="status">{saveError}</p>}
    <div className="mobile-panel-switch segmented" aria-label="Stock section">
<button type="button" aria-pressed={route.panel === 'destinations'} onClick={() => navigate({ panel: 'destinations' })}>{route.mode === 'items' ? 'Destinations' : 'Shop items'} <span>{route.mode === 'items' ? codes.length : rows.length}</span>
</button>
<button type="button" aria-pressed={route.panel === 'timeline'} onClick={() => navigate({ panel: 'timeline' })}>
<Icon name="timeline" size={16}/>Observations</button>
</div>
    <div className={`workbench panel-${route.panel}`}>
<div className="primary-content">
<section className="stock-section" aria-label={route.mode === 'items' ? 'Destination comparisons' : 'Country stock'}>{!snapshot ? <div className="loading-stock" aria-label={feed.failed ? 'Stock unavailable' : 'Loading stock'}>{feed.failed ? <div className="empty-state">
<Icon name="info" size={28}/>
<h2>Stock is unavailable</h2>
<p>No snapshot has been received. Check for updates to retry.</p>
<button type="button" onClick={() => void feed.refresh()}>Check for updates</button>
</div> : <>{Array.from({ length: 7 }, (_,n) => <div className="skeleton-row" key={n}/>)}</>}</div> : !id || !rows.length ? <div className="empty-state">
<Icon name="items" size={28}/>
<h2>{route.saved && !saved.length ? 'No saved items' : !id ? 'No item observations' : route.mode === 'countries' && !snapshot.countries.find(c => c.code === country) ? 'Country omitted from snapshot' : route.mode === 'countries' && !snapshot.countries.find(c => c.code === country)?.items.length ? 'No item observations' : 'No matching observations'}</h2>
<p>{route.saved && !saved.length ? 'Save an item from its heading to keep it here, or browse all items.' : 'Missing observations do not establish zero stock. Try another destination or clear your filters.'}</p>
<button type="button" onClick={() => { if (!id) void feed.refresh(); else if (route.mode === 'countries' && !snapshot.countries.find(c => c.code === country)?.items.length) navigate({ mode: 'items', query: '', availability: 'all', panel: 'destinations' },true); else navigate({ query: '', availability: 'all', saved: false }); }}>{route.saved && !saved.length ? 'Browse all items' : !id ? 'Check for updates' : route.mode === 'countries' && !snapshot.countries.find(c => c.code === country)?.items.length ? 'Compare destinations' : 'Clear filters'}</button>
</div> : route.mode === 'items' ? <>
<table className="comparison-table">
<caption className="sr-only">{itemName}: reported quantities and shop prices across destinations</caption>
<thead>
<tr>
<th scope="col">Destination</th>
<th scope="col" className="numeric">Quantity</th>
<th scope="col" className="numeric">Shop price (T$)</th>
<th scope="col">Last net change</th>
<th scope="col">Source freshness</th>
</tr>
</thead>
<tbody>{rows.map(o => <tr key={o.code} className={country === o.code ? 'selected' : ''}>
<th scope="row">
<button type="button" aria-pressed={country === o.code} onClick={() => navigate({ country: o.code })}>
<span className="country-code">{COUNTRIES[o.code]?.short || o.code}</span>{countryName(o.code)}</button>
</th>
<td className="numeric">
<Amount observation={o}/>
</td>
<td className="numeric">{price(o.price)}</td>
<td>
<Change item={o.item}/>
</td>
<td>
<Tone country={o.country} now={now}/>{o.quantity === null && <span className="sr-only">{o.label}</span>}</td>
</tr>)}</tbody>
</table>
<div className="mobile-comparisons">{rows.map(o => <article className={country === o.code ? 'selected' : ''} key={o.code}>
<button className="mobile-observation" type="button" aria-expanded={mobileDetail === o.code} aria-controls={`details-${o.code}`} onClick={() => { navigate({ country: o.code }); setMobileDetail(mobileDetail === o.code ? null : o.code); }}>
<span className="mobile-country">
<span className="country-code">{COUNTRIES[o.code]?.short || o.code}</span>
<span className="country-with-age">{countryName(o.code)}<small>{sourceStatus(o.country, now).label}</small>
</span>
</span>
<Amount observation={o}/>
<span className="mobile-price">{o.price === null ? '\u2014' : price(o.price)}</span>
<i className={`status-dot ${sourceStatus(o.country, now).tone}`}/>
</button>
<div className="mobile-inline-details" id={`details-${o.code}`} hidden={mobileDetail !== o.code}>
<Tone country={o.country} now={now}/>{renderEvidence(o)}</div>
</article>)}</div>
</> : <div className="country-shop">{rows.map(o => <article key={o.item!.id} className={o.item!.id === id ? 'selected' : ''}>
<button type="button" className="shop-item" aria-pressed={o.item!.id === id} aria-expanded={mobile ? mobileDetail === String(o.item!.id) : undefined} aria-controls={mobile ? `shop-details-${o.item!.id}` : undefined} onClick={() => { navigate({ item: o.item!.id }); setMobileDetail(mobileDetail === String(o.item!.id) ? null : String(o.item!.id)); }}>
<ItemArt name={o.item!.name || ''}/>
<span className="shop-name">
<strong>{o.item!.name || `Item ${o.item!.id}`}</strong>
<small>{price(o.price)} shop price</small>
<Change item={o.item}/>
</span>
<span>
<Amount observation={o}/>
<small>{o.label}</small>
</span>
</button>
<div className="mobile-inline-details" id={`shop-details-${o.item!.id}`} hidden={mobileDetail !== String(o.item!.id)}>{renderEvidence(o)}</div>
</article>)}</div>}</section>{focused && <Timeline observation={focused}/>}<button className="mobile-view-timeline primary-button" type="button" onClick={() => { navigate({ panel: 'timeline' }); window.scrollTo({ top: 0, behavior: 'auto' }); }}>
<Icon name="timeline"/>View observations</button>
</div>{focused && <aside className="context-panel" aria-label="Selected observation">{renderEvidence(focused)}</aside>}</div>
    <footer className="stock-footer">
<p>Observations via <a href="https://yata.yt/">YATA</a> for <a href="https://www.torn.com/">Torn</a>. Shop prices are in Torn dollars, not resale values. Reports do not reserve stock.</p>
<nav aria-label="Operations pages">
<a href="index.html">Operations</a>
<a href="diagnostics.html">Diagnostics</a>
<a href="usage.html">Infrastructure &amp; Usage</a>
</nav>
</footer>
</main>
</div>
</div>
    <nav className="mobile-bottom-nav" aria-label="Browse stock">
<a href={routeURL({ ...route, country, item: id, mode: 'items', query: '', saved: false, panel: 'destinations' })} aria-current={route.mode === 'items' && !route.saved ? 'page' : undefined} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ mode: 'items', query: '', saved: false, panel: 'destinations' },true); }}>
<Icon name="items"/>Items</a>
<a href={routeURL({ ...route, country, item: id, mode: 'countries', query: '', saved: false, panel: 'destinations' })} aria-current={route.mode === 'countries' && !route.saved ? 'page' : undefined} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ mode: 'countries', query: '', saved: false, panel: 'destinations' },true); }}>
<Icon name="countries"/>Countries</a>
<a href={routeURL({ ...route, country, item: id, mode: 'items', query: '', saved: true, panel: 'destinations' })} aria-current={route.saved ? 'page' : undefined} onClick={e => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return; e.preventDefault(); navigate({ mode: 'items', query: '', saved: true, panel: 'destinations' },true); }}>
<Icon name="star"/>Saved {saved.length || ''}</a>
</nav>
    <dialog className="destination-dialog" ref={menu} aria-labelledby="destination-dialog-title">
<header>
<h2 id="destination-dialog-title">Choose destination</h2>
<button className="icon-button" type="button" aria-label="Close destinations" onClick={() => menu.current?.close()}>
<Icon name="close"/>
</button>
</header>{nav}</dialog>
</>;
}
