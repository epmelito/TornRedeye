"use strict";
const labels = {
  persisted_events: "Persistence matching events", halt_events: "Halt matching events",
  collector_invocations: "Collector requests", collector_errors: "Collector errors",
  collector_throttles: "Collector throttles", collector_duration: "Collector execution duration",
  publisher_invocations: "Publisher requests", publisher_errors: "Publisher errors",
  publisher_throttles: "Publisher throttles", publisher_duration: "Publisher execution duration",
  log_ingestion: "Collector log ingestion (uncompressed)", s3_BucketSizeBytes: "Evidence standard storage (daily)",
  s3_NumberOfObjects: "Evidence object count (daily)",
  sns_NumberOfMessagesPublished: "SNS published messages", sns_NumberOfNotificationsDelivered: "SNS delivered notifications",
  sns_NumberOfNotificationsFailed: "SNS failed notifications"
};
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function validNumber(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0; }
function metricSamples(metric) { return metric.state === "available" && Array.isArray(metric.samples) ? metric.samples : []; }
function display(value) { return validNumber(value) ? value.toLocaleString("en-US", { maximumFractionDigits: 3 }) : "Unavailable"; }
function utc(value) { const ms = typeof value === "string" ? Date.parse(value) : NaN; return Number.isFinite(ms) ? new Date(ms).toISOString().replace(".000", "") : "Unavailable"; }
function age(value) { const ms = typeof value === "string" ? Date.parse(value) : NaN; return Number.isFinite(ms) && ms <= Date.now() ? `${((Date.now() - ms) / 3600000).toFixed(2)} hours old · derived age` : "Age unavailable"; }
function publicationState(snapshot, now = Date.now()) {
  const ms = Date.parse(snapshot.published_at_utc);
  if (!Number.isFinite(ms) || ms > now + 60000) return "invalid";
  return now - ms > 2 * 3600000 ? "stale" : "current";
}
function publication(snapshot) {
  const status = publicationState(snapshot), node = document.getElementById("publication");
  node.className = status === "current" ? "" : "stale";
  node.textContent = `${status === "current" ? "Publication current" : status === "stale" ? "Publication stale — over two hours old" : "Publication timestamp unavailable"} · ${utc(snapshot.published_at_utc)} · ${snapshot.state === "partial" ? "Partial source availability" : "Source availability reported; historical coverage remains uncertain"}`;
}
function table(headers, rows, className = "") {
  const wrap = element("div", undefined, "table-wrap"), t = element("table"), head = element("thead"), tr = element("tr");
  t.className = className;
  if (className) { t.setAttribute("role", "table"); head.setAttribute("role", "rowgroup"); tr.setAttribute("role", "row"); }
  headers.forEach(x => { const th = element("th", x); th.scope = "col"; tr.append(th); });
  head.append(tr); t.append(head);
  const body = element("tbody");
  if (className) body.setAttribute("role", "rowgroup");
  rows.forEach(row => {
    const r = element("tr");
    if (className) r.setAttribute("role", "row");
    row.forEach((x, i) => {
      const cell = element(className && i === 0 ? "th" : "td");
      if (className && i === 0) cell.scope = "row";
      else cell.setAttribute("data-label", headers[i]);
      if (className) cell.setAttribute("role", i === 0 ? "rowheader" : "cell");
      if (typeof x === "object" && x !== null) cell.append(x);
      else cell.textContent = String(x);
      r.append(cell);
    });
    body.append(r);
  });
  t.append(body); wrap.append(t); return wrap;
}
function render(snapshot) {
  if (snapshot.schema_version !== 1 || !snapshot.metrics || !snapshot.window || !snapshot.freshness) throw new Error("invalid snapshot");
  publication(snapshot);
  const freshness = document.getElementById("freshness");
  freshness.replaceChildren();
  [["Observation retrieval", "retrieved_at_utc"], ["Provider source timestamp", "source_timestamp_utc"], ["Provider export timestamp", "export_timestamp_utc"]].forEach(([label, key]) => {
    const timestamp = utc(snapshot.freshness[key]);
    const classification = timestamp === "Unavailable" ? "Unavailable" : "Measured";
    const c = element("div", undefined, "card"); c.append(element("span", label), element("strong", timestamp), element("small", age(snapshot.freshness[key])), element("p", `${classification} · selected normalized evidence`)); freshness.append(c);
  });
  freshness.append(element("p", snapshot.freshness.coverage));
  document.getElementById("window").textContent = `${utc(snapshot.window.start_utc)} → ${utc(snapshot.window.end_utc)} · hourly, completed UTC hours`;
  const trends = document.getElementById("trends"); trends.replaceChildren();
  ["persisted_events", "collector_invocations", "collector_errors", "halt_events", "collector_throttles"].forEach(key => {
    const m = snapshot.metrics[key] || {}, samples = metricSamples(m);
    const section = element("div"); section.append(element("h3", labels[key]));
    const spark = element("div", undefined, "spark"), max = Math.max(1, ...samples.filter(validNumber)); spark.setAttribute("aria-hidden", "true");
    samples.forEach(v => { const bar = element("span", undefined, !validNumber(v) ? "missing" : v === 0 ? "zero" : ""); bar.style.height = `${validNumber(v) ? Math.max(2, v / max * 42) : 5}px`; spark.append(bar); });
    const reported = samples.filter(validNumber).length;
    section.append(spark, element("p", `${reported}/${samples.length} reported slots · ${reported ? "Measured Count for returned values; missing values unavailable" : "Unavailable · no reported values"} · gray = unknown, white = reported zero`));
    const details = element("details"); details.append(element("summary", "Hourly values and coverage"));
    details.append(element("p", m.coverage || "Unavailable"));
    details.append(table(["Hour beginning (UTC)", "Matching events / requests / errors"], samples.map((v, i) => [utc(new Date(Date.parse(m.start_utc) + i * 3600000).toISOString()), display(v)])));
    section.append(details); trends.append(section);
  });
  const gaps = document.getElementById("gaps"); gaps.replaceChildren();
  if (snapshot.recent_heartbeat?.state !== "available") gaps.append(element("p", "Gap retrieval unavailable; absence of listed gaps does not mean healthy collection."));
  else if (!snapshot.recent_gaps?.length) gaps.append(element("p", "No 15-minute candidates in returned telemetry. This does not establish complete collection coverage."));
  else gaps.append(table(["Start UTC", "End UTC", "Minutes", "Unknown minutes"], snapshot.recent_gaps.map(g => [utc(g.start_utc), utc(g.end_utc), display(g.minutes), display(g.unknown_minutes)]), "responsive-table"));
  const usage = document.getElementById("usage"); usage.replaceChildren();
  const rows = Object.entries(labels).map(([key, label]) => {
    const m = snapshot.metrics[key] || {}, values = metricSamples(m).filter(validNumber);
    const daily = key.startsWith("s3_");
    const value = values.length ? (daily ? values[values.length - 1] : values.reduce((a,b) => a+b, 0)) : null;
    return [label, display(value), m.unit || "—", !validNumber(value) ? "Unavailable" : daily ? "Measured latest returned daily sample" : "Derived subtotal of returned samples", `${m.reported_slots ?? 0}/${m.samples?.length ?? 0} slots; ${m.coverage || "coverage unknown"}`, `${utc(m.start_utc)} → ${utc(m.end_utc)}`, utc(m.latest_sample_utc), m.provenance || "Unavailable"];
  });
  const billed = snapshot.billed_compute || {};
  const billedAvailable = billed.state === "available" && validNumber(billed.value);
  rows.push(["Collector billed compute", billedAvailable ? display(billed.value) : "Unavailable", "GB-seconds", billedAvailable ? "Derived REPORT subtotal" : "Unavailable", billed.coverage || "Unavailable", `${utc(billed.start_utc)} → ${utc(billed.end_utc)}`, "Unavailable", billed.provenance || "Unavailable"]);
  usage.append(table(["Signal", "Value and unit", "Classification", "Details"], rows.map(row => {
    const details = element("details"); details.append(element("summary", "Coverage and time window"));
    const notes = element("dl");
    [["Coverage", row[4]], ["Window UTC", row[5]], ["Latest metric bucket UTC", row[6]], ["Provenance", row[7]]].forEach(([label, value]) => notes.append(element("dt", label), element("dd", value)));
    details.append(notes);
    return [row[0], `${row[1]} ${row[2]}`, row[3], details];
  }), "responsive-table consumption-table"));
  usage.append(element("p", "Execution Duration is not billed duration. S3 storage samples are daily and delayed; log ingestion is not stored-log size. SNS notifications are not billable API requests or email-only delivery counts."));
  const cost = document.getElementById("cost"); cost.replaceChildren();
  const estimate = snapshot.cost?.publisher_compute_scenario;
  cost.append(element("p", Array.isArray(estimate) && estimate.every(validNumber) ? `Estimated publisher Lambda scenario: $${estimate[0].toFixed(4)}–$${estimate[1].toFixed(4)} USD/month before allowances. ${snapshot.cost.assumptions}.` : "Publisher cost scenario unavailable."));
  cost.append(element("p", "Total cost: unavailable. Hosting requests/traffic, S3 operations, log storage and account allowance consumption are not fully measured. Existing monitoring resources: two custom metrics and three alarms; the dashboard adds none."));
}
async function load() {
  try {
    const response = await fetch("data.json", { cache: "no-cache", credentials: "omit" });
    if (!response.ok) throw new Error("snapshot unavailable");
    const body = await response.text();
    if (body.length > 192 * 1024) throw new Error("oversized snapshot");
    const snapshot = JSON.parse(body); render(snapshot);
    setInterval(() => publication(snapshot), 60000);
  } catch (_) {
    const node = document.getElementById("publication"); node.className = "failed";
    node.textContent = "Snapshot unavailable. No health or zero-usage conclusions can be drawn. Try again later.";
  }
}
if (typeof module !== "undefined") module.exports = { publicationState, display, render, load };
if (typeof document !== "undefined") load();
