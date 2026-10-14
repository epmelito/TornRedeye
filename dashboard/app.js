"use strict";
const labels = {
  collector_invocations: "Collection function runs", collector_duration: "Collection execution time",
  collector_throttles: "Capacity-blocked collection requests", publisher_invocations: "Snapshot publication runs",
  publisher_errors: "Snapshot publication errors", publisher_throttles: "Capacity-blocked publication requests",
  billed_compute: "Collection billed compute", sns_NumberOfMessagesPublished: "Notification publishes",
  sns_NumberOfNotificationsDelivered: "Notifications delivered", sns_NumberOfNotificationsFailed: "Notification delivery failures"
};
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function validNumber(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0; }
function metricSamples(metric) { return metric.state === "available" && Array.isArray(metric.samples) ? metric.samples : []; }
function display(value) { return validNumber(value) ? value.toLocaleString("en-US", value > 0 && value < .001 ? { maximumSignificantDigits: 3 } : { maximumFractionDigits: 3 }) : "Unavailable"; }
function timestamp(value) { return typeof value === "string" ? Date.parse(value) : NaN; }
function utc(value, compact = false) {
  const ms = typeof value === "number" ? value : typeof value === "string" ? Date.parse(value) : NaN;
  if (!Number.isFinite(ms)) return "Unavailable";
  const d = new Date(ms);
  if (!Number.isFinite(d.getTime())) return "Unavailable";
  const iso = d.toISOString();
  return compact ? `${iso.slice(11, 16)} ${iso.slice(5, 10)} UTC` : `${iso.slice(11, 19)} ${iso.slice(0, 10)} UTC`;
}
function age(value) {
  const ms = typeof value === "string" ? Date.parse(value) : NaN, minutes = (Date.now() - ms) / 60000;
  if (!Number.isFinite(ms)) return "Age unavailable";
  if (minutes < -1) return "Ahead of device clock";
  if (minutes < 1) return "Less than a minute old";
  return minutes < 60 ? `${Math.floor(minutes)} min old` : minutes < 1440 ? `${(minutes / 60).toFixed(1)} hours old` : `${(minutes / 1440).toFixed(1)} days old`;
}
function readable(value, unit) {
  if (!validNumber(value)) return "Unavailable";
  if (unit === "Bytes") {
    const index = value ? Math.max(0, Math.min(4, Math.floor(Math.log(value) / Math.log(1024)))) : 0;
    return `${display(value / 1024 ** index)} ${["B", "KiB", "MiB", "GiB", "TiB"][index]}`;
  }
  if (unit === "Milliseconds") return `${display(value / 1000)} seconds`;
  return `${display(value)} ${unit === "Count" ? "count" : unit || ""}`.trim();
}
function publicationState(snapshot, now = Date.now()) {
  const ms = typeof snapshot.published_at_utc === "string" ? Date.parse(snapshot.published_at_utc) : NaN;
  if (!Number.isFinite(ms)) return "invalid";
  if (ms > now + 60000) return "future";
  return now - ms > 2 * 3600000 ? "stale" : "current";
}
function publication(snapshot) {
  const status = publicationState(snapshot), node = document.getElementById("publication");
  node.className = status === "current" ? "" : "stale";
  node.textContent = status === "invalid" ? "Publication time unavailable" : status === "future" ? `Publication time ahead of device clock · ${utc(snapshot.published_at_utc)}` : `Snapshot ${status === "stale" ? "stale · " : ""}${age(snapshot.published_at_utc)} · published ${utc(snapshot.published_at_utc)}`;
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
function points(metric, group = 1, limit = null) {
  const raw = metricSamples(metric || {}), offset = limit === null ? 0 : Math.max(0, raw.length - limit);
  const start = timestamp(metric?.start_utc), period = metric?.period_seconds;
  const result = [];
  for (let i = offset; i < raw.length; i += group) {
    const chunk = raw.slice(i, i + group), values = chunk.filter(validNumber);
    const total = values.length ? values.reduce((a, b) => a + b, 0) : null;
    const time = Number.isFinite(start) && validNumber(period) && period > 0 ? start + i * period * 1000 : NaN;
    result.push({ value: validNumber(total) ? total : null, start: time, end: time + group * period * 1000,
      reported: values.length, slots: group, kind: !validNumber(total) ? "Unavailable" : group === 1 ? "Measured" : "Derived returned subtotal" });
  }
  return result;
}
function range(start, end) { return `${utc(start)} to ${utc(end)}`; }
function metricNotes(metric) {
  const notes = element("dl");
  [["Source", metric?.provenance || "Unavailable"], ["Source coverage", metric?.coverage || "Unavailable"],
    ["Window", range(timestamp(metric?.start_utc), timestamp(metric?.end_utc))], ["Latest returned sample", utc(timestamp(metric?.latest_sample_utc))]]
    .forEach(([label, value]) => notes.append(element("dt", label), element("dd", value)));
  return notes;
}
function inspection(root, marks, descriptions, title, initial = marks.length - 1) {
  const output = element("p", undefined, "inspection"); output.setAttribute("aria-live", "polite");
  function select(index) {
    output.textContent = descriptions[index];
    marks.forEach((mark, i) => mark.setAttribute("aria-pressed", String(i === index)));
  }
  marks.forEach((mark, i) => {
    mark.tabIndex = -1; mark.title = descriptions[i]; mark.setAttribute("aria-label", descriptions[i]);
    ["pointerenter", "click"].forEach(event => mark.addEventListener(event, () => select(i)));
  });
  root.append(output); select(initial);
}
function svgNode(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); return node;
}
function chart(metric, title, options = {}) {
  const data = points(metric, options.group || 1, options.limit ?? null), unit = metric?.unit || "Count";
  const root = element("section", undefined, options.warning ? "chart warning-chart" : "chart"); root.append(element("h3", title));
  if (options.caption) root.append(element("p", options.caption, "chart-caption"));
  if (!data.length) {
    const details = element("details"); details.append(element("summary", "Source availability"), metricNotes(metric));
    root.append(element("p", "Monitoring unavailable. No zero value is assumed."), details); return root;
  }
  const peak = Math.max(2, ...data.map(p => p.value).filter(validNumber));
  const max = unit === "Count" ? Math.ceil(peak / 2) * 2 : peak;
  const frame = element("div", undefined, "plot-frame"), scale = element("div", undefined, "count-scale"), plot = element("div", undefined, "plot");
  root.append(element("p", unit === "Count" ? "Count" : unit === "Bytes" ? "Bytes" : "Seconds", "axis-label"));
  [max, max / 2, 0].forEach(value => scale.append(element("span", unit === "Count" ? display(value) : readable(value, unit))));
  plot.style.gap = options.line || data.length > 48 ? "0px" : "4px";
  if (data.length > 48) plot.className += " dense-plot";
  const marks = data.map(p => {
    const state = p.value === null ? "unknown" : p.value === 0 ? "zero" : "positive";
    const mark = element("button", undefined, `mark ${state}${p.value !== null && p.reported < p.slots ? " partial" : ""}`); mark.type = "button";
    const bar = element("span", undefined, "bar"); bar.style.height = `${p.value === null ? 8 : Math.max(2, p.value / max * 160)}px`; mark.append(bar);
    plot.append(mark); return mark;
  });
  if (options.line) {
    plot.className += " line-plot";
    const svg = svgNode("svg", { viewBox: "0 0 1000 160", preserveAspectRatio: "none", "aria-hidden": "true" });
    data.forEach((p, i) => {
      if (p.value === null) return;
      const x = (i + .5) / data.length * 1000, y = 160 - p.value / max * 156;
      const previous = data[i - 1];
      if (previous?.value !== null && previous && previous.reported === previous.slots && p.reported === p.slots) svg.append(svgNode("line", { x1: (i - .5) / data.length * 1000, y1: 160 - previous.value / max * 156, x2: x, y2: y }));
      svg.append(svgNode("circle", { cx: x, cy: y, r: 3, class: p.reported < p.slots ? "partial-point" : "" }));
    });
    plot.append(svg);
  }
  frame.append(scale, plot); root.append(frame);
  const axis = element("div", undefined, "time-axis");
  [data[0].start, (data[0].start + data.at(-1).end) / 2, data.at(-1).end].forEach(time => axis.append(element("span", utc(time, true)))); root.append(axis);
  const describe = p => `${range(p.start, p.end)} · ${p.value === null ? "Unavailable" : `${p.kind}: ${readable(p.value, unit)}`} · ${p.reported}/${p.slots} samples reported${p.reported < p.slots ? "; Partial — missing samples" : ""}`;
  const positive = data.findLastIndex(p => p.value > 0);
  inspection(root, marks, data.map(describe), title, options.warning && positive >= 0 ? positive : data.length - 1);
  const details = element("details"); details.append(element("summary", "Source and exact measurements"), metricNotes(metric),
    table(["Interval (UTC)", "Exact source value", "Coverage"], data.map(p => [range(p.start, p.end), p.value === null ? "Unavailable" : `${p.value} ${unit} · ${p.kind}`, `${p.reported}/${p.slots}${p.reported < p.slots ? " · Partial" : ""}`])));
  root.append(details); return root;
}
function timeControls(root, draw, defaults = { hours: 24, group: 1 }) {
  const controls = element("div", undefined, "time-controls");
  function select(label, choices, value) {
    const wrap = element("label", `${label} `), input = element("select");
    choices.forEach(([v, text]) => { const option = element("option", text); option.value = String(v); input.append(option); });
    input.value = String(value); wrap.append(input); controls.append(wrap); return input;
  }
  const period = select("Period", [[24, "24 hours"], [168, "7 days"]], defaults.hours);
  const granularity = select("Interval", [[1, "Hourly"], [3, "3 hours"], [6, "6 hours"], [24, "Daily"]], defaults.group);
  const charts = element("div"); root.replaceChildren(controls, charts);
  const update = () => draw(charts, { limit: Number(period.value), group: Number(granularity.value) });
  period.addEventListener("change", update); granularity.addEventListener("change", update); update();
}
function storageSummary(metric) {
  const returned = points(metric).filter(p => p.value !== null), root = element("div", undefined, "storage-summary");
  if (!returned.length) { root.append(element("p", "Evidence storage measurement unavailable.")); return root; }
  const first = returned[0], latest = returned.at(-1);
  root.append(element("p", readable(latest.value, "Bytes"), "storage-value"), element("p", `Latest measured standard storage · ${utc(latest.start)}`));
  if (returned.length > 1) {
    const delta = latest.value - first.value;
    root.append(element("p", `Earlier: ${readable(first.value, "Bytes")} at ${utc(first.start)}. Net change: ${delta < 0 ? "-" : "+"}${readable(Math.abs(delta), "Bytes")}.`));
  } else root.append(element("p", "Earlier comparable measurement unavailable; net change unknown."));
  return root;
}
function monitoringRuns(metric) {
  const samples = metricSamples(metric || {}), start = timestamp(metric?.start_utc), period = metric?.period_seconds;
  if (!Number.isFinite(start) || period !== 60) return [];
  const runs = [];
  samples.forEach((value, i) => {
    const state = !validNumber(value) ? "unknown" : value === 0 ? "zero" : "positive";
    if (runs.at(-1)?.state === state) { runs.at(-1).minutes++; runs.at(-1).end += 60000; }
    else runs.push({ state, minutes: 1, start: start + i * 60000, end: start + (i + 1) * 60000 });
  });
  return runs;
}
function renderMonitoring(metric) {
  const root = document.getElementById("monitoring"); root.replaceChildren();
  const runs = monitoringRuns(metric);
  if (!runs.length) { root.append(element("p", "Minute monitoring information is unavailable. No absence of collection activity can be established.")); return; }
  const names = { positive: "Positive activity signal reported", zero: "No positive activity signal reported", unknown: "Monitoring information missing" };
  const timeline = element("div", undefined, "monitoring-strip"), total = runs.reduce((a, r) => a + r.minutes, 0);
  const marks = runs.map(r => { const mark = element("button", undefined, `interval ${r.state}`); mark.type = "button"; mark.style.width = `${r.minutes / total * 100}%`; timeline.append(mark); return mark; });
  root.append(timeline);
  const axis = element("div", undefined, "time-axis"); axis.append(element("span", utc(runs[0].start, true)), element("span", utc(runs.at(-1).end, true))); root.append(axis);
  inspection(root, marks, runs.map(r => `${range(r.start, r.end)} · ${names[r.state]} · ${r.minutes} min`), "minute monitoring intervals");
  ["zero", "unknown"].forEach(state => {
    const intervals = runs.filter(r => r.state === state), minutes = intervals.reduce((a, r) => a + r.minutes, 0);
    const details = element("details"); details.append(element("summary", `${names[state]} · ${minutes} min in returned monitoring`));
    const list = element("ul"); intervals.forEach(r => list.append(element("li", `${range(r.start, r.end)} · ${r.minutes} min`)));
    details.append(intervals.length ? list : element("p", "No such interval in returned minute samples. Coverage remains uncertain.")); root.append(details);
  });
}
function measurement(metric, compute = false) {
  const values = compute ? metric?.state === "available" && validNumber(metric.value) ? [metric.value] : [] : metricSamples(metric || {}).filter(validNumber);
  const value = values.length ? values.reduce((a, b) => a + b, 0) : null;
  return { value: validNumber(value) ? value : null, kind: !validNumber(value) ? "Unavailable" : compute ? "Derived returned compute subtotal" : "Derived returned subtotal" };
}
function measurements(snapshot, keys) {
  return table(["Measurement", "Value", "Classification", "Investigation details"], keys.map(key => {
    const compute = key === "billed_compute", m = compute ? snapshot.billed_compute : snapshot.metrics[key];
    const result = measurement(m, compute), details = element("details");
    details.append(element("summary", "Window, source and precision"), metricNotes(m), element("p", `Exact value: ${result.value === null ? "Unavailable" : result.value} ${m?.unit || ""}`));
    return [labels[key], readable(result.value, m?.unit), result.kind, details];
  }), "responsive-table");
}
function render(snapshot) {
  if (snapshot.schema_version !== 1 || !snapshot.metrics || !snapshot.window || !snapshot.freshness) throw new Error("invalid snapshot");
  publication(snapshot);
  const usage = Boolean(document.getElementById("usage")), operations = Boolean(document.getElementById("monitoring"));
  const confidence = document.getElementById("confidence"); confidence.replaceChildren();
  confidence.append(element("p", usage ? "Resource measurements describe returned samples. Missing periods remain unknown." : "Monitoring can be missing, delayed or repeated. Activity signals do not prove complete collection coverage."));
  const coverage = usage ? null : snapshot.freshness.coverage;
  if (/pagination|partial/i.test(coverage || "")) confidence.append(element("p", usage ? "Compute records reached the log page limit; newer records may be omitted." : "Observation lookup reached the log page limit; the selected observation may not be the latest.", "warning"));
  if (snapshot.state === "partial") confidence.append(element("p", "Some snapshot sources are unavailable.", "warning"));
  if (operations) {
    timeControls(document.getElementById("trends"), (root, options) => root.replaceChildren(
      chart(snapshot.metrics.persisted_events, "Recorded collection activity", { ...options, line: options.limit > 24, caption: "Saved-observation notices. Repeated notices can inflate counts." }),
      chart(snapshot.metrics.collector_errors, "Collection errors", { ...options, warning: true, caption: "Errors reported by the collection function." })));
    const attention = document.getElementById("attention"); attention.replaceChildren();
    [["halt_events", "Collection-stop notices"], ["collector_throttles", "Capacity-blocked requests"]].forEach(([key, label]) => {
      const data = points(snapshot.metrics[key], 1, 24), positive = data.filter(p => p.value > 0).at(-1);
      attention.append(element("p", positive ? `${label}: ${display(positive.value)} during ${range(positive.start, positive.end)} (latest hour with returned notices).` : data.some(p => p.value !== null) ? `${label}: none in returned hours (${data.filter(p => p.value !== null).length}/${data.length} hours reported).` : `${label}: monitoring unavailable.`, positive ? "warning" : ""));
    });
    renderMonitoring(snapshot.recent_heartbeat);
  } else if (!usage) {
    timeControls(document.getElementById("trends"), (root, options) => root.replaceChildren(
      ...[["persisted_events", "Recorded collection activity"], ["collector_errors", "Collection errors"], ["halt_events", "Collection-stop notices"]].map(([key, label]) => chart(snapshot.metrics[key], label, { ...options, line: key === "persisted_events", warning: key !== "persisted_events" }))), { hours: 168, group: 6 });
    const reliability = document.getElementById("reliability"); reliability.replaceChildren(measurements(snapshot, ["collector_invocations", "collector_throttles", "publisher_errors", "publisher_throttles"]));
    const hourly = document.getElementById("hourly-evidence"), keys = ["persisted_events", "collector_errors", "halt_events"], series = keys.map(key => metricSamples(snapshot.metrics[key] || {}));
    const start = timestamp(snapshot.window.start_utc), length = Math.max(...series.map(s => s.length));
    hourly.replaceChildren(length ? table(["Hour beginning (UTC)", "Activity signals", "Errors", "Stop notices"], Array.from({ length }, (_, i) => [utc(start + i * 3600000), ...series.map(s => validNumber(s[i]) ? String(s[i]) : "Unavailable")])) : element("p", "Hourly evidence unavailable."));
    const evidence = document.getElementById("freshness"); evidence.replaceChildren();
    [["Selected observation retrieved", "retrieved_at_utc"], ["Provider source updated", "source_timestamp_utc"], ["Provider export updated", "export_timestamp_utc"]].forEach(([label, key]) => {
      const time = snapshot.freshness.state === "available" ? timestamp(snapshot.freshness[key]) : NaN;
      evidence.append(element("dt", label), element("dd", `${utc(time)} · ${Number.isFinite(time) ? "Measured timestamp" : "Unavailable"}`));
    });
    evidence.append(element("dt", "Selection coverage"), element("dd", snapshot.freshness.coverage || "Unavailable"), element("dt", "Source"), element("dd", snapshot.freshness.provenance || "Unavailable"));
    const heartbeat = document.getElementById("heartbeat-coverage"); heartbeat.replaceChildren(metricNotes(snapshot.recent_heartbeat));
  } else {
    const usageNode = document.getElementById("usage"); usageNode.replaceChildren(storageSummary(snapshot.metrics.s3_BucketSizeBytes));
    const storage = document.getElementById("storage-trend"); storage.replaceChildren(chart(snapshot.metrics.s3_BucketSizeBytes, "Evidence storage growth", { line: true, caption: "Daily standard-storage totals, including evidence and control objects. Gaps are missing measurements; changes are net growth." }));
    const objects = points(snapshot.metrics.s3_NumberOfObjects).filter(p => p.value !== null).at(-1);
    storage.append(element("p", objects ? `Latest measured evidence-bucket object count: ${display(objects.value)} at ${utc(objects.start)}.` : "Object count unavailable."));
    timeControls(document.getElementById("usage-trends"), (root, options) => root.replaceChildren(
      chart(snapshot.metrics.collector_invocations, "Collection function runs", { ...options, line: true, caption: "Runs include skips and failed attempts." }),
      chart(snapshot.metrics.collector_duration, "Collection execution time", { ...options, line: true, caption: "Sum of returned execution durations in each interval." }),
      chart(snapshot.metrics.log_ingestion, "Log data entering CloudWatch", { ...options, line: true, caption: "Uncompressed incoming log bytes in each interval." })), { hours: 168, group: 6 });
    document.getElementById("other-usage").replaceChildren(measurements(snapshot, ["publisher_invocations", "sns_NumberOfMessagesPublished", "sns_NumberOfNotificationsDelivered", "sns_NumberOfNotificationsFailed"]));
  }
}
async function load() {
  try {
    const response = await fetch("data.json", { cache: "no-cache", credentials: "omit" });
    if (!response.ok) throw new Error("snapshot unavailable");
    const body = await response.text(); if (body.length > 192 * 1024) throw new Error("oversized snapshot");
    const snapshot = JSON.parse(body); render(snapshot); setInterval(() => publication(snapshot), 60000);
  } catch (_) {
    ["confidence", "trends", "attention", "monitoring", "reliability", "hourly-evidence", "freshness", "heartbeat-coverage", "usage", "usage-trends", "storage-trend", "other-usage"].forEach(id => document.getElementById(id)?.replaceChildren());
    const node = document.getElementById("publication"); node.className = "failed";
    node.textContent = "Snapshot unavailable. Collection activity and usage cannot be assessed. No zero values are assumed.";
  }
}
if (typeof module !== "undefined") module.exports = { publicationState, display, readable, points, monitoringRuns, measurement, storageSummary, chart, render, load, utc };
if (typeof document !== "undefined") load();
