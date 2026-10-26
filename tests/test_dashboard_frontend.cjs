"use strict";
// no frontend dependencies: use each page's actual ids with a minimal DOM
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const app = require("../dashboard/app.js");
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.style = {}; this.listeners = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.textContent = ""; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); }
  fire(event) { (this.listeners[event] || []).forEach(fn => fn()); }
  text() { return this.textContent + this.children.map(n => n.text()).join(" "); }
}
const pages = ["index.html", "diagnostics.html", "usage.html"];
const html = Object.fromEntries(pages.map(p => [p, fs.readFileSync(path.join(__dirname, "../dashboard", p), "utf8")]));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/dashboard-preview.json"), "utf8"));
const copy = () => structuredClone(fixture);
let nodes;
function mount(page) {
  nodes = Object.fromEntries([...html[page].matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, new Element("div")]));
  global.document = { createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), getElementById: id => nodes[id] || null };
}
function walk(node) { return [node, ...node.children.flatMap(walk)]; }
const now = Date.now();
for (const [value, state] of [[now - 7200000, "current"], [now - 7200001, "stale"], [now + 60001, "future"]]) assert.equal(app.publicationState({ published_at_utc: new Date(value).toISOString() }, now), state);
for (const value of [null, 0, "invalid"]) assert.equal(app.publicationState({ published_at_utc: value }, now), "invalid");
assert.equal(app.utc("2026-10-14T17:50:00Z"), "17:50:00 2026-10-14 UTC");
assert.equal(app.utc(null), "Unavailable");
assert.equal(app.utc(1e30), "Unavailable");
assert.equal(app.display(0), "0");
for (const value of [null, NaN, Infinity]) assert.equal(app.display(value), "Unavailable");
assert.equal(app.readable(1048576, "Bytes"), "1 MiB");
assert.equal(app.readable(2500, "Milliseconds"), "2.5 seconds");
assert.equal(app.readable(0, "Bytes"), "0 B");
assert.notEqual(app.readable(.0001, "GB-seconds"), "0 GB-seconds");
const shortMetric = { state: "available", samples: [null, 0, 2, 3], start_utc: "2026-10-14T00:00:00Z", period_seconds: 3600 };
const grouped = app.points(shortMetric, 4)[0];
assert.equal(grouped.value, 5); assert.equal(grouped.reported, 3); assert.equal(grouped.slots, 4);
assert.equal(grouped.kind, "Derived returned subtotal");
for (const [samples, value, kind] of [[[0, 0], 0, "Derived returned subtotal"], [[null, null], null, "Unavailable"], [[Number.MAX_VALUE, Number.MAX_VALUE], null, "Unavailable"]]) {
  const p = app.points({ ...shortMetric, samples }, 2)[0]; assert.equal(p.value, value); assert.equal(p.kind, kind);
}
assert.deepEqual(app.points({ ...shortMetric, state: "unavailable" }), []);
for (const page of pages) {
  mount(page); app.render(copy());
  for (const target of pages) {
    assert.ok(html[page].includes(`href="${target}"`));
    assert.equal(new URL(target, `http://localhost/public/${page}`).pathname, `/public/${target}`);
  }
  assert.equal((html[page].match(/aria-current="page"/g) || []).length, 1);
  assert.match(html[page], /href="#content"/);
  assert.doesNotMatch(html[page], /possible collection gaps|candidates|schedule remains disabled|publication is manual|Updated hourly|<progress/);
  if (page !== "usage.html") assert.match(nodes.confidence.text(), /log page limit/);
  const s = copy(); s.state = "partial"; app.render(s);
  assert.match(nodes.confidence.text(), /Some snapshot sources are unavailable/);
  s.published_at_utc = new Date(now + 3600000).toISOString(); app.render(s);
  assert.match(nodes.publication.text(), /ahead of device clock/);
  assert.doesNotMatch(nodes.publication.text(), /unavailable|old|stale/);
  s.published_at_utc = null; app.render(s);
  assert.equal(nodes.publication.text(), "Publication time unavailable");
  assert.throws(() => app.render({ schema_version: 2 }));
}
assert.match(html["index.html"], /stock observations across countries and items.*YATA.*Torn/);
assert.doesNotMatch(html["index.html"], /id="activity"|id="freshness"/);
assert.doesNotMatch(html["diagnostics.html"], /id="usage"|id="monitoring"|id="cost"/);
assert.match(html["usage.html"], /Infrastructure &amp; Usage/);
for (const page of pages) assert.doesNotMatch(html[page], /slider|arrow keys|Costs|spending|allowance|headroom|billing|budget/);
function marks(chart) { return walk(chart).filter(n => n.className?.startsWith("mark ")); }
function output(chart) { return walk(chart).find(n => n.className === "inspection"); }
function charts() { return (nodes.trends || nodes["usage-trends"]).children[1].children; }
function selectors() { return walk(nodes.trends || nodes["usage-trends"]).filter(n => n.tag === "select"); }
for (const page of pages) {
  mount(page); app.render(copy());
  const [period, interval] = selectors();
  assert.equal(period.value, page === "index.html" ? "24" : "168");
  assert.equal(interval.value, page === "index.html" ? "1" : "6");
  assert.ok(charts().every(c => marks(c).length === (page === "index.html" ? 24 : 28)));
  for (const hours of [24, 168]) for (const group of [1, 3, 6, 24]) {
    period.value = String(hours); period.fire("change"); interval.value = String(group); interval.fire("change");
    charts().forEach(c => {
      const buttons = marks(c); assert.equal(buttons.length, hours / group);
      if (buttons.length > 48) assert.equal(walk(c).find(n => n.className?.startsWith("plot ")).style.gap, "0px");
      buttons[0].fire("pointerenter"); assert.equal(buttons[0]["aria-pressed"], "true");
      assert.match(output(c).textContent, /UTC.*Measured|UTC.*Derived|UTC.*Unavailable/);
      buttons[1]?.fire("click"); if (buttons[1]) assert.equal(buttons[1]["aria-pressed"], "true");
      assert.ok(walk(c).some(n => n.tag === "table"));
      assert.ok(!walk(c).some(n => n.type === "range"));
    });
  }
}
mount("index.html"); app.render(copy());
assert.match(charts()[1].text(), /14:00:00 2026-10-14 UTC.*15:00:00 2026-10-14 UTC.*Measured: 3 count/);
const runs = app.monitoringRuns(fixture.recent_heartbeat);
assert.equal(runs.filter(r => r.state === "zero").reduce((a, r) => a + r.minutes, 0), 24);
assert.equal(runs.filter(r => r.state === "unknown").reduce((a, r) => a + r.minutes, 0), 41);
assert.equal(runs.find(r => r.state === "unknown" && r.minutes === 20).start, Date.parse("2026-10-14T16:00:00Z"));
assert.equal(runs.filter(r => r.state === "zero").at(-1).start, Date.parse("2026-10-14T16:20:00Z"));
assert.ok(runs.some(r => r.state === "unknown" && r.minutes === 1 && r.start === Date.parse("2026-10-14T15:05:00Z")));
assert.equal(runs.filter(r => r.state === "zero").length, 3);
assert.match(nodes.monitoring.text(), /No positive activity signal reported.*24 min/);
assert.match(nodes.monitoring.text(), /Monitoring information missing.*41 min/);
const intervals = walk(nodes.monitoring).filter(n => n.className?.startsWith("interval "));
intervals.find(n => n.className === "interval unknown" && n.title.includes("16:00:00")).fire("click");
assert.match(output(nodes.monitoring).textContent, /16:00:00.*16:20:00 2026-10-14 UTC.*Monitoring information missing/);
const unavailable = copy(); unavailable.recent_heartbeat.state = "unavailable"; app.render(unavailable);
assert.match(nodes.monitoring.text(), /monitoring information is unavailable/);
assert.equal(walk(nodes.monitoring).filter(n => n.type === "range").length, 0);
for (const [value, state] of [[0, "zero"], [null, "unknown"]]) {
  const s = copy(); s.recent_heartbeat.samples = Array(180).fill(value); s.recent_gaps = [];
  const r = app.monitoringRuns(s.recent_heartbeat); assert.equal(r.length, 1); assert.equal(r[0].state, state); assert.equal(r[0].minutes, 180);
  app.render(s); assert.match(nodes.monitoring.text(), /180 min/);
}
mount("diagnostics.html"); app.render(copy());
assert.equal(charts().length, 3);
const partialMark = marks(charts()[0]).find(m => m.className.includes("partial"));
assert.ok(partialMark); partialMark.fire("click");
assert.match(output(charts()[0]).textContent, /samples reported; Partial/);
const countScale = walk(charts()[1]).find(n => n.className === "count-scale");
assert.ok(countScale.children.every(n => /^\d[\d,]*$/.test(n.textContent)));
assert.equal(walk(nodes["hourly-evidence"]).filter(n => n.tag === "tbody")[0].children.length, 168);
for (const value of [null, 0, "invalid timestamp", "2026-10-14T17:50:00Z"]) {
  const s = copy(); s.freshness.retrieved_at_utc = value; app.render(s);
  assert.match(nodes.freshness.children[1].text(), value === "2026-10-14T17:50:00Z" ? /Measured timestamp/ : /Unavailable/);
}
mount("usage.html"); app.render(copy());
const storage = fixture.metrics.s3_BucketSizeBytes.samples.filter(appValue => typeof appValue === "number");
assert.match(nodes.usage.text(), new RegExp(app.readable(storage.at(-1), "Bytes").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
assert.match(nodes.usage.text(), /Net change:/);
assert.doesNotMatch(nodes["usage-trends"].text() + nodes.usage.text(), /USD|month|bill|allowance/);
assert.equal(marks(nodes["storage-trend"].children[0]).length, 7);
assert.equal(walk(nodes["storage-trend"]).filter(n => n.tag === "select").length, 0);
const single = copy(); single.metrics.s3_BucketSizeBytes.samples = [null, 0]; app.render(single);
assert.match(nodes.usage.text(), /0 B.*net change unknown/);
const decreasing = copy(); decreasing.metrics.s3_BucketSizeBytes.samples = [1024, null, 0]; app.render(decreasing);
assert.match(nodes.usage.text(), /Net change: -1 KiB/);
assert.equal(walk(nodes["storage-trend"]).filter(n => n.tag === "line").length, 0);
for (const page of pages) {
  mount(page); const s = copy();
  for (const metric of Object.values(s.metrics)) metric.samples = metric.samples.map(() => null);
  app.render(s);
  const roots = (nodes.trends || nodes["usage-trends"]).children[1];
  assert.ok(roots.children.every(c => marks(c).every(m => m.className.includes("unknown"))));
  assert.doesNotMatch(roots.text(), /Measured: 0/);
  for (const metric of Object.values(s.metrics)) { metric.state = "unavailable"; metric.samples = [7]; }
  app.render(s); const unavailableCharts = charts(); assert.ok(unavailableCharts.every(c => marks(c).length === 0));
  assert.ok(unavailableCharts.every(c => /Monitoring unavailable/.test(c.text())));
}
// partial trailing groups and selected windows retain source alignment
const trailing = app.points({ ...shortMetric, samples: [2, 3] }, 3)[0];
assert.equal(trailing.value, 5); assert.equal(trailing.slots, 3); assert.equal(trailing.reported, 2);
const last24 = app.points(fixture.metrics.persisted_events, 6, 24);
assert.equal(last24[0].start, Date.parse(fixture.window.end_utc) - 24 * 3600000);
assert.equal(last24.at(-1).end, Date.parse(fixture.window.end_utc));
assert.equal(last24[0].value, fixture.metrics.persisted_events.samples.slice(-24, -18).filter(v => typeof v === "number").reduce((a, b) => a + b, 0));
const gapLine = app.chart({ ...shortMetric, samples: [1, null, 2, 3] }, "Gap", { line: true });
assert.equal(walk(gapLine).filter(n => n.tag === "line").length, 1);
const partialLine = app.chart({ ...shortMetric, samples: [1, null, 2, 3] }, "Partial", { line: true, group: 2 });
assert.equal(walk(partialLine).filter(n => n.tag === "line").length, 0);
const daily = app.points(fixture.metrics.s3_BucketSizeBytes);
assert.deepEqual(daily.map(p => p.value), fixture.metrics.s3_BucketSizeBytes.samples);
const unavailableFixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/dashboard-unavailable.json"), "utf8"));
for (const page of pages) {
  mount(page); app.render(unavailableFixture);
  assert.match(nodes.confidence.text(), /Some snapshot sources are unavailable/);
  assert.ok(charts().every(c => /Monitoring unavailable/.test(c.text())));
}
async function checkLoading() {
  for (const page of pages) {
    mount(page);
    for (const response of [{ ok: false }, { ok: true, text: async () => "invalid JSON" }, { ok: true, text: async () => "x".repeat(192 * 1024 + 1) }]) {
      global.fetch = async () => response; await app.load(); assert.match(nodes.publication.text(), /Snapshot unavailable/);
    }
    global.fetch = async () => { throw new Error("private exception"); };
    await app.load(); assert.doesNotMatch(nodes.publication.text(), /private exception/);
    let timer; global.setInterval = (fn, period) => { assert.equal(period, 60000); timer = fn; };
    const s = copy(); s.published_at_utc = new Date(now).toISOString();
    global.fetch = async (url, options) => { assert.equal(url, "data.json"); assert.equal(options.credentials, "omit"); return { ok: true, text: async () => JSON.stringify(s) }; };
    await app.load(); assert.match(nodes.publication.text(), /Snapshot.*published/);
    const originalNow = Date.now; Date.now = () => now + 7200001;
    timer(); assert.match(nodes.publication.text(), /Snapshot stale/); Date.now = originalNow;
  }
  console.log("Frontend checks passed: three-page routes, readable UTC, partial aggregates, inspection events, separate zero/missing intervals, classifications and loading failures.");
}
checkLoading().catch(error => { console.error(error); process.exitCode = 1; });
