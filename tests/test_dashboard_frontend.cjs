"use strict";
// no frontend dependencies: exercise rendering with a minimal DOM contract
const assert = require("node:assert/strict");
const app = require("../dashboard/app.js");
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ""; this.style = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.textContent = ""; }
  setAttribute(key, value) { this[key] = value; }
  text() { return this.textContent + this.children.map(n => n.text()).join(" "); }
}
const nodes = Object.fromEntries(["publication", "freshness", "window", "trends", "gaps", "usage", "cost"].map(k => [k, new Element("div")]));
global.document = { createElement: tag => new Element(tag), getElementById: id => nodes[id] };
const now = Date.now();
assert.equal(app.publicationState({ published_at_utc: new Date(now - 7200000).toISOString() }, now), "current");
assert.equal(app.publicationState({ published_at_utc: new Date(now - 7200001).toISOString() }, now), "stale");
assert.equal(app.publicationState({ published_at_utc: "invalid" }, now), "invalid");
assert.equal(app.display(0), "0");
assert.equal(app.display(null), "Unavailable");
assert.equal(app.display(NaN), "Unavailable");
const snapshot = { schema_version: 1, published_at_utc: new Date(now - 10800000).toISOString(), state: "partial",
  window: { start_utc: "2026-10-01T00:00:00Z", end_utc: "2026-10-08T00:00:00Z" },
  freshness: { retrieved_at_utc: null, source_timestamp_utc: null, export_timestamp_utc: null, coverage: "bounded" },
  metrics: { persisted_events: { state: "available", kind: "measured", samples: [null, 0, 2], start_utc: "2026-10-01T00:00:00Z", end_utc: "2026-10-01T03:00:00Z", unit: "Count", reported_slots: 2, coverage: "unknown" } },
  recent_heartbeat: { state: "unavailable" }, recent_gaps: [], billed_compute: { value: null }, cost: {} };
app.render(snapshot);
assert.match(nodes.publication.text(), /stale.*Partial/);
assert.match(nodes.trends.text(), /Unavailable.*0.*2/);
assert.match(nodes.gaps.text(), /retrieval unavailable/);
assert.match(nodes.usage.text(), /Derived subtotal/);
assert.match(nodes.cost.text(), /Total cost: unavailable/);
assert.match(nodes.freshness.text(), /Unavailable/);
assert.doesNotMatch(nodes.freshness.text(), /Measured/);
for (const value of [null, "invalid timestamp", "2026-10-10T12:00:00Z"]) {
  snapshot.freshness.retrieved_at_utc = value;
  app.render(snapshot);
  assert.equal(nodes.freshness.children[0].children[3].text(), `${value === "2026-10-10T12:00:00Z" ? "Measured" : "Unavailable"} · selected normalized evidence`);
}
snapshot.freshness.retrieved_at_utc = null;
snapshot.recent_heartbeat.state = "available";
app.render(snapshot);
assert.match(nodes.gaps.text(), /does not establish complete/);
snapshot.recent_gaps = [{ start_utc: "2026-10-01T00:00:00Z", end_utc: "2026-10-01T00:15:00Z", minutes: 15, unknown_minutes: 0 }];
app.render(snapshot);
assert.match(nodes.gaps.text(), /15.*0/);
assert.throws(() => app.render({ schema_version: 2 }));
function usageRow(label) {
  return nodes.usage.children[0].children[0].children[1].children.find(row => row.children[0].text() === label);
}
const metric = snapshot.metrics.persisted_events;
for (const [state, samples, classification, value] of [
  ["available", [null, 0, 2], "Derived subtotal of returned samples", "2 Count"],
  ["available", [0], "Derived subtotal of returned samples", "0 Count"],
  ["available", [null, null], "Unavailable", "Unavailable Count"],
  ["unavailable", [], "Unavailable", "Unavailable Count"],
  ["unavailable", [2], "Unavailable", "Unavailable Count"]
]) {
  snapshot.metrics.persisted_events = { ...metric, state, samples };
  app.render(snapshot);
  const row = usageRow("Persistence matching events");
  assert.equal(row.children[1].text(), value);
  assert.equal(row.children[2].text(), classification);
  assert.match(row.text(), /unknown/);
  const chart = nodes.trends.children[0];
  if (classification === "Unavailable") {
    assert.match(chart.text(), /Unavailable · no reported values/);
    assert.doesNotMatch(chart.text(), /Measured|Derived/);
  } else assert.match(chart.text(), /Measured Count for returned values; missing values unavailable/);
}
snapshot.metrics.persisted_events = { ...metric, samples: [Number.MAX_VALUE, Number.MAX_VALUE] };
app.render(snapshot);
assert.equal(usageRow("Persistence matching events").children[1].text(), "Unavailable Count");
assert.equal(usageRow("Persistence matching events").children[2].text(), "Unavailable");
for (const [state, samples, classification] of [
  ["available", [null, 0], "Measured latest returned daily sample"],
  ["available", [120], "Measured latest returned daily sample"],
  ["available", [null], "Unavailable"], ["unavailable", [], "Unavailable"]
]) {
  snapshot.metrics.s3_BucketSizeBytes = { ...metric, state, samples, unit: "Bytes" };
  app.render(snapshot);
  const row = usageRow("Evidence standard storage (daily)");
  assert.equal(row.children[2].text(), classification);
  if (samples.includes(0)) assert.equal(row.children[1].text(), "0 Bytes");
}
for (const [state, value, classification] of [
  ["available", 0, "Derived REPORT subtotal"], ["available", .125, "Derived REPORT subtotal"],
  ["available", null, "Unavailable"], ["unavailable", null, "Unavailable"], ["unavailable", .125, "Unavailable"]
]) {
  snapshot.billed_compute = { state, value, kind: "derived", coverage: "bounded subtotal; coverage unknown" };
  app.render(snapshot);
  assert.equal(usageRow("Collector billed compute").children[2].text(), classification);
}
// representative seven-day density with unknown hours, measured zeros and partial availability
const sevenDayMetric = { ...metric, start_utc: snapshot.window.start_utc, end_utc: snapshot.window.end_utc, period_seconds: 3600 };
const hourly = Array.from({ length: 168 }, (_, i) => i % 11 === 0 ? null : i % 7 === 0 ? 0 : 35 + i % 20);
snapshot.metrics.persisted_events = { ...sevenDayMetric, samples: hourly, reported_slots: hourly.filter(value => value !== null).length };
snapshot.metrics.collector_errors = { ...sevenDayMetric, samples: Array(168).fill(0), reported_slots: 168 };
snapshot.metrics.halt_events = { state: "unavailable", kind: "unavailable", samples: [], coverage: "query failed" };
snapshot.recent_gaps.push({ start_utc: "2026-10-01T01:00:00Z", end_utc: "2026-10-01T01:20:00Z", minutes: 20, unknown_minutes: 4 });
app.render(snapshot);
assert.equal(nodes.trends.children[0].children[1].children.length, 168);
assert.match(nodes.trends.children[1].text(), /Unavailable · no reported values/);
assert.match(nodes.trends.children[2].text(), /Measured Count/);
assert.equal(nodes.usage.children[0].children[0].role, "table");
assert.equal(usageRow("Collector errors").children[0].scope, "row");
assert.equal(usageRow("Collector errors").children[3].children[0].children[0].tag, "summary");
assert.match(nodes.gaps.text(), /20.*4/);
const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "../dashboard/index.html"), "utf8");
assert.ok(html.indexOf('id="gap-title"') < html.indexOf('id="trend-title"'));
assert.ok(html.indexOf('id="freshness-title"') < html.indexOf('id="trend-title"'));
assert.match(html, /<details><summary>View hourly trends and values<\/summary>/);
async function checkLoading() {
  for (const response of [{ ok: false }, { ok: true, text: async () => "invalid JSON" },
                           { ok: true, text: async () => "x".repeat(192 * 1024 + 1) }]) {
    global.fetch = async () => response;
    await app.load(); assert.match(nodes.publication.text(), /Snapshot unavailable/);
  }
  global.fetch = async () => { throw new Error("private exception"); };
  await app.load();
  assert.match(nodes.publication.text(), /Snapshot unavailable/);
  assert.doesNotMatch(nodes.publication.text(), /private exception/);
  let timer;
  global.setInterval = (fn, period) => { assert.equal(period, 60000); timer = fn; };
  snapshot.published_at_utc = new Date(now).toISOString();
  global.fetch = async (url, options) => {
    assert.equal(url, "data.json"); assert.equal(options.credentials, "omit");
    return { ok: true, text: async () => JSON.stringify(snapshot) };
  };
  await app.load(); assert.match(nodes.publication.text(), /Publication current/);
  const originalNow = Date.now;
  Date.now = () => now + 7200001;
  timer(); assert.match(nodes.publication.text(), /Publication stale/);
  Date.now = originalNow;
  console.log("Frontend checks passed: zero, missing, partial, stale timer, gap coverage, HTTP/JSON/size/network failures and successful loading.");
}
checkLoading().catch(error => { console.error(error); process.exitCode = 1; });
