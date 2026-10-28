"use strict";

const POLL_MS = 30000;
const NAMES = { arg: "Argentina", can: "Canada", cay: "Cayman Islands", chi: "China", haw: "Hawaii", jap: "Japan", mex: "Mexico", sou: "South Africa", swi: "Switzerland", uae: "United Arab Emirates", uni: "United Kingdom" };
const number = n => Number.isSafeInteger(n) && n >= 0;
const date = s => typeof s === "string" && /(?:Z|[+-]\d\d:\d\d)$/.test(s) ? Date.parse(s) : NaN;
const utc = s => Number.isFinite(date(s)) ? new Date(date(s)).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC") : "Unknown";
const count = n => number(n) ? n.toLocaleString("en-US") : "Unknown";
const delta = n => Number.isSafeInteger(n) ? (n > 0 ? "+" : "") + n.toLocaleString("en-US") : "Unknown";

function validateSnapshot(s) {
  if (!s || s.kind !== "stock_snapshot" || s.schema_version !== 1 || !Number.isFinite(date(s.published_at_utc)) || !Number.isFinite(date(s.retrieved_at_utc)) || !["complete", "partial", "collection_failed", "unavailable"].includes(s.state) || !Array.isArray(s.countries) || s.countries.length > 64) throw Error("Invalid stock snapshot");
  const codes = new Set(); let total = 0;
  for (const c of s.countries) {
    if (typeof c.code !== "string" || !/^[a-z][a-z0-9_-]{0,15}$/.test(c.code) || codes.has(c.code) || typeof c.reported !== "boolean" || (c.source_timestamp_utc !== null && (!Number.isFinite(date(c.source_timestamp_utc)) || date(c.source_timestamp_utc) > date(s.retrieved_at_utc))) || !Array.isArray(c.items)) throw Error("Invalid country");
    codes.add(c.code); const ids = new Set();
    for (const i of c.items) {
      if (!number(i.id) || !i.id || ids.has(i.id) || (i.name !== null && (typeof i.name !== "string" || i.name.length > 128)) || ![i.quantity, i.price].every(v => v === null || number(v)) || typeof i.reported !== "boolean" || !i.comparison || !["unavailable", "cached", "compared", "source_conflict", "source_regressed"].includes(i.comparison.state)) throw Error("Invalid item");
      for (const k of ["quantity_change", "price_change"]) if (i.comparison[k] !== null && !Number.isSafeInteger(i.comparison[k])) throw Error("Invalid comparison");
      for (const k of ["source_interval_utc", "retrieval_interval_utc"]) if (i.comparison[k] !== null && (!Array.isArray(i.comparison[k]) || i.comparison[k].length !== 2 || !i.comparison[k].every(v => Number.isFinite(date(v))))) throw Error("Invalid comparison interval");
      if (!i.reported && (i.quantity !== null || i.price !== null)) throw Error("Missing item has values");
      ids.add(i.id); if (++total > 4096) throw Error("Too many items");
    }
  }
  return s;
}

function ageLabel(stamp, now) {
  const age = now - date(stamp);
  if (!Number.isFinite(age)) return "Source time unknown";
  if (age < -60000) return "Source time is ahead of your device clock";
  return `Source report ${Math.max(0, Math.floor(age / 60000))} min old${age > 300000 ? " · Older than 5 minutes" : ""}`;
}

function changeLabel(i) {
  const c = i.comparison;
  if (!i.reported) return "Not reported";
  const retained = c.state === "cached" && [c.quantity_change, c.price_change].some(v => Number.isSafeInteger(v) && v !== 0);
  if (c.state === "cached" && !retained) return "Same source report";
  if (c.state === "source_conflict") return "Conflicting source report";
  if (c.state === "source_regressed") return "Source time moved backward";
  if (c.state !== "compared" && !retained) return "No comparable prior report";
  if (c.quantity_change === 0 && c.price_change === 0) return "No reported change";
  return `${retained ? "Last change (same source report): " : ""}Quantity ${delta(c.quantity_change)} · Price ${Number.isSafeInteger(c.price_change) ? "$" + delta(c.price_change) : "Unknown"}`;
}

function start(env = {}) {
  const doc = env.document || document, win = env.window || window;
  const request = env.fetch || win.fetch.bind(win), now = env.now || Date.now;
  const later = env.setTimeout || win.setTimeout.bind(win), cancel = env.clearTimeout || win.clearTimeout.bind(win);
  const Abort = env.AbortController || win.AbortController;
  const node = id => doc.getElementById(id);
  let snapshot = null, failure = false, busy = false, stopped = false, timer = null, ageTimer = null, navKey = null;
  function element(tag, text) { const e = doc.createElement(tag); if (text !== undefined) e.textContent = text; return e; }
  function render(agesOnly = false) {
    const status = node("publication");
    if (!snapshot) { status.textContent = failure ? "Stock snapshot unavailable. Retrying automatically." : "Loading stock snapshot…"; status.className = failure ? "failed" : ""; return; }
    const publicationAge = now() - date(snapshot.published_at_utc), retrievalAge = now() - date(snapshot.retrieved_at_utc);
    const stale = publicationAge > 120000 || retrievalAge > 120000;
    status.className = failure ? "failed" : stale ? "stale" : "";
    status.textContent = `${failure ? "Update failed; showing the last received snapshot. " : ""}${publicationAge < -60000 || retrievalAge < -60000 ? "Timestamp ahead of your device clock. " : stale ? "Updates are delayed. " : ""}Published ${utc(snapshot.published_at_utc)}`;
    node("stock-health").textContent = ({ complete: "Reported observations. Unseen changes and collection gaps remain possible.", partial: "Partial report. Missing items and fields are unknown, not zero.", collection_failed: "The latest collection attempt failed. Stock quantities are unknown.", unavailable: "The latest source export was unusable. Stock quantities are unknown." })[snapshot.state];
    const codes = snapshot.countries.map(c => c.code), key = codes.join("|");
    let selected;
    try { selected = decodeURIComponent(win.location.hash.slice(1)); } catch { selected = ""; }
    if (!codes.includes(selected)) selected = codes[0];
    if (navKey !== key) {
      node("countries").replaceChildren(...snapshot.countries.map(c => { const a = element("a", NAMES[c.code] || c.code.toUpperCase()); a.setAttribute("href", "#" + c.code); return a; }));
      navKey = key;
    }
    for (const a of node("countries").children) { if (a.getAttribute("href") === "#" + selected) a.setAttribute("aria-current", "location"); else a.removeAttribute("aria-current"); }
    const country = snapshot.countries.find(c => c.code === selected);
    node("country-title").textContent = country ? NAMES[country.code] || country.code.toUpperCase() : "No countries reported";
    node("source-age").textContent = country ? !country.reported ? "Country not reported in this snapshot · Source time unknown" : `${country.source_timestamp_utc === null ? "Country reported · " : ""}${ageLabel(country.source_timestamp_utc, now())} · ${utc(country.source_timestamp_utc)}` : "Source time unknown";
    node("source-age").className = country && now() - date(country.source_timestamp_utc) > 300000 ? "warning" : "";
    if (agesOnly === true) return;
    node("observation-time").textContent = `Collected ${utc(snapshot.retrieved_at_utc)} · Export timestamp ${utc(snapshot.export_timestamp_utc)}`;
    const compared = country && country.items.find(i => i.reported && ["compared", "cached"].includes(i.comparison.state) && i.comparison.source_interval_utc);
    node("comparison-window").textContent = compared ? `${compared.comparison.state === "cached" ? "Last change; same source report. " : ""}Changes compare source reports ${compared.comparison.source_interval_utc.map(utc).join(" to ")}. Intervening changes may be unseen; individual intervals appear on each change.` : "No advancing, comparable source reports for this snapshot.";
    node("stock-caption").textContent = `${country ? NAMES[country.code] || country.code.toUpperCase() : "Overseas"} · reported shop quantities and prices`;
    node("stock-items").replaceChildren(...(country ? country.items : []).map(i => {
      const tr = element("tr"), name = element("th", i.name || `Item ${i.id}`); name.setAttribute("scope", "row"); tr.append(name);
      for (const [label, value] of [["Reported quantity", count(i.quantity)], ["Shop price", number(i.price) ? "$" + count(i.price) : "Unknown"], ["Reported change", changeLabel(i)]]) {
        const cell = element("td", value); cell.setAttribute("data-label", label);
        if (value === "Unknown" || i.comparison.state === "source_conflict") cell.className = "warning";
        if (label === "Reported change" && i.comparison.source_interval_utc) cell.setAttribute("title", `Source reports: ${i.comparison.source_interval_utc.map(utc).join(" to ")}. Collection gaps may hide changes.`);
        tr.append(cell);
      }
      return tr;
    }));
    node("empty-stock").textContent = country && country.items.length ? "" : "No item observations in this snapshot. This does not mean zero stock.";
  }
  async function refresh() {
    if (stopped || busy || doc.hidden) return;
    busy = true; node("refresh").disabled = true;
    const controller = new Abort(), timeout = later(() => controller.abort(), 8000);
    try {
      const response = await request("stock.json", { cache: "no-cache", signal: controller.signal });
      if (!response.ok) throw Error("Snapshot request failed");
      const text = await response.text(); if (text.length > 256 * 1024) throw Error("Snapshot too large");
      const next = validateSnapshot(JSON.parse(text));
      if (!snapshot || date(next.retrieved_at_utc) >= date(snapshot.retrieved_at_utc)) snapshot = next;
      failure = false;
    } catch { failure = true; }
    finally { cancel(timeout); busy = false; node("refresh").disabled = false; render(); }
  }
  async function tick() { await refresh(); if (!stopped) timer = later(tick, POLL_MS); }
  function visible() { render(); if (!doc.hidden) refresh(); }
  node("refresh").addEventListener("click", refresh);
  doc.addEventListener("visibilitychange", visible);
  win.addEventListener("hashchange", render);
  function ages() { if (!stopped) { render(true); ageTimer = later(ages, 10000); } }
  tick(); ages();
  return { refresh, render, snapshot: () => snapshot, stop() { stopped = true; cancel(timer); cancel(ageTimer); doc.removeEventListener("visibilitychange", visible); win.removeEventListener("hashchange", render); } };
}

if (typeof module !== "undefined") module.exports = { POLL_MS, validateSnapshot, ageLabel, changeLabel, start };
if (typeof window !== "undefined" && typeof document !== "undefined") start();
