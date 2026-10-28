"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path");
const app = require("../dashboard/stock.js");
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/stock-preview.json"), "utf8"));
const html = fs.readFileSync(path.join(__dirname, "../dashboard/stock.html"), "utf8");
const copy = () => structuredClone(fixture);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.attrs = {}; this.listeners = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  text() { return this.textContent + " " + this.children.map(c => c.text()).join(" "); }
}
async function main() {
  assert.equal(app.POLL_MS, 30000);
  assert.equal(app.validateSnapshot(copy()).countries.length, 3);
  for (const alter of [s => { s.schema_version = 2; }, s => { s.countries.push(s.countries[0]); }, s => { s.countries[0].items[0].quantity = -1; }, s => { s.countries[0].items[0].reported = false; }]) {
    const s = copy(); alter(s); assert.throws(() => app.validateSnapshot(s));
  }
  const base = Date.parse(fixture.published_at_utc);
  assert.match(app.ageLabel(null, base), /unknown/);
  assert.match(app.ageLabel(new Date(base + 120000).toISOString(), base), /ahead/);
  assert.match(app.ageLabel(new Date(base - 301000).toISOString(), base), /Older than 5 minutes/);
  assert.match(app.changeLabel(fixture.countries[0].items[0]), /Quantity \+125/);
  const retained = structuredClone(fixture.countries[0].items[0]); retained.comparison.state = "cached";
  assert.match(app.changeLabel(retained), /Last change \(same source report\): Quantity \+125/);
  retained.comparison.quantity_change = retained.comparison.price_change = null;
  assert.equal(app.changeLabel(retained), "Same source report");
  for (const state of ["source_conflict", "source_regressed", "unavailable"]) {
    const i = structuredClone(fixture.countries[0].items[0]); i.comparison.state = state;
    assert.doesNotMatch(app.changeLabel(i), /Quantity \+125/);
  }
  for (const route of ["stock.html", "index.html", "diagnostics.html", "usage.html"]) assert.ok(html.includes(`href="${route}"`));
  assert.doesNotMatch(html, /<script[^>]+https:|confidence rating|restock ETA/);
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, new Element("div")]));
  const events = {}, listeners = {}, timers = new Map(); let serial = 0, time = base, failing = false, calls = [];
  let data = copy(), cached = copy(), cacheUntil = time + 15000;
  const doc = { hidden: false, createElement: tag => new Element(tag), getElementById: id => nodes[id], addEventListener: (k, fn) => { events[k] = fn; }, removeEventListener: k => { delete events[k]; } };
  const win = { location: { hash: "#can" }, addEventListener: (k, fn) => { listeners[k] = fn; }, removeEventListener: k => { delete listeners[k]; } };
  const controller = app.start({ document: doc, window: win, now: () => time, AbortController,
    setTimeout: (fn, ms) => { const id = ++serial; timers.set(id, { fn, at: time + ms }); return id; }, clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      calls.push({ at: time, url, options }); if (failing) throw Error("offline");
      if (time >= cacheUntil) { cached = structuredClone(data); cacheUntil = time + 15000; }
      return { ok: true, text: async () => JSON.stringify(cached) };
    } });
  await new Promise(setImmediate);
  async function advance(ms) {
    const target = time + ms;
    while (true) {
      const next = [...timers].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      time = next[1].at; timers.delete(next[0]); await next[1].fn();
    }
    time = target;
  }
  assert.equal(nodes["country-title"].textContent, "Canada");
  assert.match(nodes["stock-items"].text(), /Unknown/);
  assert.match(nodes["stock-health"].textContent, /Partial/);
  const countryLink = nodes.countries.children[0];
  win.location.hash = "#arg"; listeners.hashchange();
  assert.equal(nodes["country-title"].textContent, "Argentina");
  assert.match(nodes["stock-items"].text(), /\$0/);
  assert.match(nodes["stock-items"].text(), /\+125/);
  data = copy();
  data.countries[0].items[0].comparison.state = "cached";
  cached = structuredClone(data); await controller.refresh();
  assert.match(nodes["stock-items"].text(), /Last change \(same source report\): Quantity \+125/);
  assert.match(nodes["comparison-window"].textContent, /Last change; same source report/);
  const changeCell = nodes["stock-items"].children[0].children[3];
  for (const stamp of data.countries[0].items[0].comparison.source_interval_utc) {
    assert.ok(changeCell.getAttribute("title").includes(stamp.slice(0, 19).replace("T", " ")));
  }
  data.countries[0].reported = false; data.countries[0].source_timestamp_utc = null;
  for (const item of data.countries[0].items) { item.reported = false; item.quantity = item.price = null; item.comparison.state = "unavailable"; }
  cached = structuredClone(data); await controller.refresh();
  assert.match(nodes["source-age"].textContent, /Country not reported in this snapshot/);
  assert.match(nodes["stock-items"].text(), /Not reported/);
  data = copy(); data.countries[0].source_timestamp_utc = null;
  data.countries[0].items[0].quantity = 0;
  data.countries[0].items[0].comparison.state = "unavailable";
  cached = structuredClone(data); await controller.refresh();
  assert.match(nodes["source-age"].textContent, /Country reported · Source time unknown/);
  assert.doesNotMatch(nodes["source-age"].textContent, /not reported/);
  assert.equal(nodes["stock-items"].children[0].children[1].textContent, "0");
  data = copy(); cached = structuredClone(data); await controller.refresh();
  // Another visitor fills the shared cache immediately before publication.
  await advance(15999); cached = structuredClone(data); cacheUntil = time + 15000;
  await advance(1); const publishedAt = time;
  data = copy(); data.published_at_utc = data.retrieved_at_utc = new Date(time).toISOString(); data.countries[0].items[0].quantity = 9876;
  await advance(14000); assert.doesNotMatch(nodes["stock-items"].text(), /9,876/);
  await advance(30000); assert.match(nodes["stock-items"].text(), /9,876/);
  assert.equal(time - publishedAt, 44000);
  assert.equal(nodes.countries.children[0], countryLink);
  assert.ok(calls.every(c => c.url === "stock.json" && c.options.cache === "no-cache"));
  const refreshedRow = nodes["stock-items"].children[0];
  failing = true; await advance(10000); assert.equal(nodes["stock-items"].children[0], refreshedRow);
  await advance(20000);
  assert.match(nodes.publication.textContent, /Update failed/); assert.match(nodes["stock-items"].text(), /9,876/);
  await advance(120000); assert.match(nodes.publication.textContent, /Updates are delayed/);
  const before = calls.length; doc.hidden = true; await advance(60000); assert.equal(calls.length, before);
  failing = false; doc.hidden = false; events.visibilitychange(); await new Promise(setImmediate); assert.equal(calls.length, before + 1);
  data = copy(); data.schema_version = 2; cacheUntil = time;
  await controller.refresh(); assert.match(nodes.publication.textContent, /Update failed/);
  data = copy(); data.published_at_utc = data.retrieved_at_utc = new Date(time).toISOString();
  data.state = "collection_failed";
  for (const c of data.countries) { c.source_timestamp_utc = null; c.reported = false; for (const i of c.items) { i.quantity = i.price = null; i.reported = false; } }
  cached = data; cacheUntil = time + 15000;
  await controller.refresh(); assert.match(nodes["stock-health"].textContent, /collection attempt failed/);
  assert.match(nodes["source-age"].textContent, /unknown/); assert.match(nodes["stock-items"].text(), /Unknown/);
  // Exercise the complete country navigation and item population without private data.
  data = copy(); data.published_at_utc = data.retrieved_at_utc = new Date(time).toISOString();
  data.countries = ["arg", "can", "cay", "chi", "haw", "jap", "mex", "sou", "swi", "uae", "uni"].map((code, index) => {
    const country = structuredClone(fixture.countries[0]); country.code = code;
    country.items = Array.from({ length: index < 7 ? 21 : 20 }, (_, j) => ({ ...structuredClone(country.items[0]), id: 1000 + index * 30 + j }));
    return country;
  });
  cached = data; await controller.refresh();
  assert.equal(nodes.countries.children.length, 11); let renderedItems = 0;
  for (const country of data.countries) { win.location.hash = "#" + country.code; listeners.hashchange(); assert.equal(nodes["stock-items"].children.length, country.items.length); renderedItems += nodes["stock-items"].children.length; }
  assert.equal(renderedItems, 227);
  controller.stop();
  for (const id of Object.keys(nodes)) nodes[id] = new Element("div");
  const unavailable = app.start({ document: doc, window: win, AbortController, now: () => time,
    setTimeout: () => 1, clearTimeout: () => {}, fetch: async () => { throw Error("unavailable"); } });
  await new Promise(setImmediate);
  assert.match(nodes.publication.textContent, /Stock snapshot unavailable/);
  assert.equal(nodes["stock-items"].children.length, 0);
  unavailable.stop();
  console.log("Stock frontend checks passed; controlled 15s shared-cache + 30s polling publication-to-display latency: 44,000 ms.");
}
main().catch(e => { console.error(e); process.exitCode = 1; });
