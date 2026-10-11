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
  metrics: { persisted_events: { samples: [null, 0, 2], start_utc: "2026-10-01T00:00:00Z", end_utc: "2026-10-01T03:00:00Z", unit: "Count", reported_slots: 2, coverage: "unknown" } },
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
