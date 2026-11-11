"use strict";
const $ = (selector) => document.querySelector(selector);
const form = $("#run-form");
const selected = new Set();
const results = new Map();
let catalog,
  runs = [],
  active = null,
  detailId = null,
  busy = false,
  snapshot = "";
const colors = ["#367651", "#5283ac", "#be8a43", "#a373a4"];
const friendly = {
  seed_min_limit: "Seed",
  no_seed_lower: "Lower first",
  ladder_multi: "Multi BUY",
  ladder_one: "One BUY",
  strict_skip_ambiguous: "Strict",
  touch_buy_priority: "BUY priority",
  touch_sell_priority: "SELL priority",
  reentry_near_fresh_book: "Fresh reference",
  reentry_one_step_below_sell: "Step below SELL",
};
const fields = {
  markets: "Markets",
  start: "From · Moscow",
  end: "Until · Moscow",
  capital: "Capital / market",
  reserve: "Cash reserve / market",
  spacing_pct: "Grid spacing · %",
  net_target: "Basket minimum profit",
  entry: "First entry",
  ladder: "Lower BUY policy",
  levels: "Pending levels (multi only)",
  max_lots: "Max held lots",
  ohlc: "OHLC convention",
  reentry: "Reentry",
  buy_fee_pct: "BUY fee · %",
  sell_fee_pct: "SELL fee · %",
  spread_pct: "Spread · %",
  latency: "Delay · bars",
};
function node(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function clear(e) {
  e.replaceChildren();
}
function error(message) {
  $("#error").textContent = message;
  $("#error").hidden = !message;
}
function fmt(v, digits = 2) {
  return v === null || v === undefined
    ? "Unknown"
    : Number(v).toLocaleString("en-US", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      });
}
function pct(v) {
  return v === null || v === undefined ? "Unknown" : fmt(Number(v) * 100) + "%";
}
function local(ms) {
  return new Date(Number(ms) + 10800000).toISOString().slice(0, 16);
}
function date(value) {
  return local(typeof value === "number" ? value : Date.parse(value)).replace(
    "T",
    " ",
  );
}
function field(key) {
  return form.elements.namedItem(key);
}
function marketKeys() {
  return [...document.querySelectorAll("#markets input:checked")].map(
    (e) => e.value,
  );
}
function bounds() {
  const keys = marketKeys();
  const sources = catalog.sources.filter((s) =>
    keys.includes(s.venue + ":" + s.market),
  );
  return sources.length
    ? [
        Math.max(...sources.map((s) => Date.parse(s.first_utc))),
        Math.min(...sources.map((s) => Date.parse(s.last_utc) + 60000)),
      ]
    : null;
}
function updateCoverage() {
  const b = bounds();
  $("#market-count").textContent = marketKeys().length + " selected";
  $("#coverage").textContent = b
    ? `Available: ${date(b[0])} → ${date(b[1])}. Archive gaps are preserved.`
    : "Choose at least one market.";
  field("levels").disabled = field("ladder").value === "ladder_one";
}
function range(days) {
  const b = bounds();
  if (!b) return;
  field("start").value = local(
    days === "full" ? b[0] : Math.max(b[0], b[1] - Number(days) * 86400000),
  );
  field("end").value = local(b[1]);
}
function populate(value) {
  for (const key of Object.keys(catalog.defaults))
    field(key).value = value[key];
  document
    .querySelectorAll("#markets input")
    .forEach((e) => (e.checked = value.markets.includes(e.value)));
  field("start").value = local(Date.parse(value.start));
  field("end").value = local(Date.parse(value.end));
  updateCoverage();
}
function reset() {
  for (const [key, value] of Object.entries(catalog.defaults))
    field(key).value = value;
  document
    .querySelectorAll("#markets input")
    .forEach((e, i) => (e.checked = i === 0));
  updateCoverage();
  range("7");
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      message = JSON.parse(text).error || text;
    } catch {}
    throw new Error(message);
  }
  return response.json();
}
function post(url, body) {
  return api(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Run-Token": catalog.token,
    },
    body: JSON.stringify(body),
  });
}
async function result(key) {
  if (!results.has(key)) results.set(key, await api(`/api/runs/${key}/result`));
  return results.get(key);
}
function status(row) {
  const cls =
    row.status === "PASS"
      ? ""
      : row.status === "RUNNING"
        ? " running"
        : " failed";
  return node("span", row.status, "badge" + cls);
}
function renderRuns() {
  const list = $("#runs");
  clear(list);
  const query = $("#search").value.toLowerCase();
  for (const row of runs.filter((r) =>
    (r.name + " " + r.settings.markets.join(" ")).toLowerCase().includes(query),
  )) {
    const tr = node("tr", undefined, selected.has(row.id) ? "selected" : "");
    const check = node("input");
    check.type = "checkbox";
    check.checked = selected.has(row.id);
    check.disabled = row.status !== "PASS";
    check.setAttribute("aria-label", "Compare " + row.name);
    check.addEventListener("change", async () => {
      if (check.checked && selected.size >= 4) {
        check.checked = false;
        error("Choose at most four runs.");
        return;
      }
      error("");
      check.checked ? selected.add(row.id) : selected.delete(row.id);
      renderRuns();
      await compare().catch((e) => error(e.message));
    });
    const first = node("td");
    first.append(check);
    tr.append(first);
    const title = node("td");
    const open = node("button", row.name, "run-title");
    open.title = row.name;
    open.addEventListener("click", () =>
      showDetails(row.id).catch((e) => error(e.message)),
    );
    title.append(
      open,
      node(
        "small",
        `${row.origin} · ${row.settings.markets.length} market(s) · ${row.settings.spacing_pct}% · ${friendly[row.settings.entry]} / ${friendly[row.settings.ladder]} / ${friendly[row.settings.ohlc]}`,
      ),
    );
    tr.append(title);
    const badge = node("td");
    badge.append(status(row));
    badge.title =
      row.error ||
      "PASS validates this simulation's accounting, not profitability";
    tr.append(badge);
    tr.append(
      node("td", row.portfolio ? pct(row.portfolio.net.net_return) : "—"),
      node(
        "td",
        row.portfolio ? pct(row.portfolio.net.max_observed_drawdown) : "—",
      ),
    );
    const last = node("td"),
      reuse = node("button", "Reuse");
    reuse.addEventListener("click", () => {
      populate({ ...row.settings, name: row.name.slice(0, 73) + " · copy" });
      $("#setup-title").scrollIntoView({ behavior: "smooth" });
    });
    last.append(reuse);
    tr.append(last);
    list.append(tr);
  }
  $("#empty-runs").hidden = runs.length > 0;
  $("#run-count").textContent = runs.length;
  const previous = detailId;
  clear($("#detail-choice"));
  for (const row of runs) {
    const o = node("option", row.name);
    o.value = row.id;
    $("#detail-choice").append(o);
  }
  if (previous) $("#detail-choice").value = previous;
  $("#submit").disabled = Boolean(active) || busy;
  $("#progress").hidden = !active;
  if (active) {
    const row = runs.find((r) => r.id === active);
    $("#progress-text").textContent =
      `Running ${row?.name || "backtest"}… No percentage estimate is available.`;
  }
  $("#connection").textContent = active
    ? "Engine running"
    : "Ready · local engine";
}
function metricRows(rows) {
  const get = (fn) => rows.map(fn);
  const p = (fn) => get((r) => (r.portfolio ? fn(r.portfolio) : null));
  const metrics = [
    ["Beginning NAV · USDT", p((x) => fmt(x.net.beginning_nav))],
    ["Ending net NAV · USDT", p((x) => fmt(x.net.ending_nav))],
    ["Net return", p((x) => pct(x.net.net_return))],
    ["Total PnL · USDT", p((x) => fmt(x.net.net_pnl))],
    ["Realized PnL", p((x) => fmt(x.realized_pnl))],
    ["Unrealized PnL · net", p((x) => fmt(x.net_unrealized_pnl))],
    ["Max observed drawdown", p((x) => pct(x.net.max_observed_drawdown))],
    ["Unknown NAV observations", p((x) => fmt(x.net.unknown_samples, 0))],
    ["Buy & hold · net return", p((x) => pct(x.buy_hold_net.net_return))],
    ["Cash hold · return", get(() => "0.00%")],
    ["Fees · USDT", p((x) => fmt(x.metrics.fees_quote))],
    [
      "BUY / SELL fills",
      p((x) => `${x.metrics.buy_fills} / ${x.metrics.sell_fills}`),
    ],
    ["Remaining position cost", p((x) => fmt(x.position_cost))],
    ["Reserved quote", p((x) => fmt(x.reserved_quote))],
    [
      "Profit factor",
      p((x) =>
        x.metrics.profit_factor === null
          ? "Undefined"
          : fmt(x.metrics.profit_factor),
      ),
    ],
    ["Engine elapsed · seconds", get((r) => fmt(r.seconds, 1))],
  ];
  for (const [key, label] of Object.entries(fields))
    metrics.push([
      label,
      get((r) =>
        key === "markets"
          ? r.settings[key].join(", ")
          : ["start", "end"].includes(key)
            ? date(r.settings[key])
            : friendly[r.settings[key]] || r.settings[key],
      ),
    ]);
  return metrics;
}
function svg(tag, attrs, text) {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
function chart(data, rows) {
  const el = $("#chart");
  clear(el);
  const series = data.map((r, i) => ({
    name: rows[i].name,
    color: colors[i],
    values: r.nav_curve.map((p) => [
      p.timestamp_ms,
      p.net_nav === null
        ? null
        : (Number(p.net_nav) / Number(r.portfolio.net.beginning_nav) - 1) * 100,
    ]),
  }));
  if (data.length === 1)
    series.push({
      name: "Buy & hold",
      color: "#98a08f",
      dash: true,
      values: data[0].nav_curve.map((p) => [
        p.timestamp_ms,
        p.buy_hold_net_nav === null
          ? null
          : (Number(p.buy_hold_net_nav) /
              Number(data[0].portfolio.net.beginning_nav) -
              1) *
            100,
      ]),
    });
  const points = series.flatMap((s) => s.values),
    known = points.filter((p) => p[1] !== null);
  if (!known.length) {
    el.append(
      svg(
        "text",
        { x: 80, y: 120 },
        "NAV is unknown throughout these samples.",
      ),
    );
    return;
  }
  let lo = Math.min(0, ...known.map((p) => p[1])),
    hi = Math.max(0, ...known.map((p) => p[1]));
  if (hi - lo < 0.2) {
    hi += 0.1;
    lo -= 0.1;
  }
  const pad = (hi - lo) * 0.08;
  hi += pad;
  lo -= pad;
  const start = Math.min(...points.map((p) => p[0])),
    end = Math.max(...points.map((p) => p[0]));
  const x = (t) => 65 + ((t - start) / Math.max(1, end - start)) * 810,
    y = (v) => 225 - ((v - lo) / (hi - lo)) * 205;
  for (let i = 0; i < 5; i++) {
    const v = lo + ((hi - lo) * i) / 4;
    el.append(
      svg("line", { x1: 65, x2: 875, y1: y(v), y2: y(v), class: "grid-line" }),
      svg(
        "text",
        { x: 52, y: y(v) + 3, "text-anchor": "end" },
        fmt(v, 1) + "%",
      ),
    );
  }
  el.append(
    svg("text", { x: 65, y: 253 }, date(start).slice(0, 10)),
    svg(
      "text",
      { x: 875, y: 253, "text-anchor": "end" },
      date(end).slice(0, 10),
    ),
  );
  for (const s of series) {
    let d = "",
      move = true;
    for (const [t, v] of s.values) {
      if (v === null) {
        move = true;
        continue;
      }
      d += (move ? "M" : "L") + x(t).toFixed(2) + "," + y(v).toFixed(2) + " ";
      move = false;
    }
    el.append(
      svg("path", {
        d,
        stroke: s.color,
        ...(s.dash ? { "stroke-dasharray": "5 4" } : {}),
      }),
    );
  }
}
async function compare() {
  const rows = [...selected]
    .map((key) => runs.find((r) => r.id === key))
    .filter(Boolean);
  $("#comparison").hidden = !rows.length;
  $("#compare-empty").hidden = Boolean(rows.length);
  $("#export").disabled = !rows.length;
  if (!rows.length) return;
  const data = await Promise.all(rows.map((r) => result(r.id)));
  const keys = rows.map((r) => r.id).join();
  if (keys !== [...selected].join()) return;
  const differences = Object.keys(fields).filter(
    (key) => new Set(rows.map((r) => JSON.stringify(r.settings[key]))).size > 1,
  );
  const mismatch = differences.filter((key) =>
    ["markets", "start", "end", "capital", "reserve"].includes(key),
  );
  const buildDiff = new Set(rows.map((r) => r.executable_sha256)).size > 1;
  $("#comparison-notice").textContent =
    (mismatch.length
      ? "Context differs — " +
        mismatch.map((k) => fields[k]).join(", ") +
        ". Returns are not a matched strategy comparison. "
      : "") +
    (buildDiff
      ? "Engine builds differ; inspect their receipts before attributing changes to settings. "
      : "") +
    (differences.length
      ? "Changed settings: " +
        differences.map((k) => fields[k]).join(", ") +
        "."
      : "Same displayed settings and context.") +
    " All results remain assumption-based.";
  clear($("#legend"));
  rows.forEach((r, i) => {
    const label = node("span");
    label.append(
      node("i", undefined, "swatch c" + i),
      document.createTextNode(r.name),
    );
    $("#legend").append(label);
  });
  if (rows.length === 1)
    $("#legend").append(node("span", "Dashed: funded buy & hold"));
  chart(data, rows);
  const all = metricRows(rows);
  const split = all.length - Object.keys(fields).length;
  for (const [id, content] of [
    ["#comparison-table", all.slice(0, split)],
    ["#comparison-settings", all.slice(split)],
  ]) {
    const table = $(id);
    clear(table);
    const head = node("tr");
    head.append(node("th", "Measure / setting"));
    rows.forEach((r) => head.append(node("th", r.name)));
    const thead = node("thead");
    thead.append(head);
    table.append(thead);
    const tbody = node("tbody");
    for (const [label, values] of content) {
      const tr = node("tr");
      tr.append(node("td", label));
      for (const value of values) tr.append(node("td", value));
      tbody.append(tr);
    }
    table.append(tbody);
  }
}
async function showDetails(key) {
  detailId = key;
  $("#detail-choice").value = key;
  const row = runs.find((r) => r.id === key);
  if (!row) return;
  $("#detail-empty").hidden = true;
  $("#detail").hidden = false;
  clear($("#allocations"));
  clear($("#downloads"));
  const files =
    row.status === "PASS"
      ? ["stdout.json", "receipt.json", "input.tsv"]
      : row.status === "RUNNING"
        ? ["input.tsv"]
        : ["receipt.json", "stderr.txt", "input.tsv"];
  for (const name of files) {
    const a = node("a", name);
    a.href = `/api/runs/${key}/files/${name}`;
    $("#downloads").append(a);
  }
  $("#settings-json").textContent = JSON.stringify(row.settings, null, 2);
  $("#detail-note").textContent =
    row.error ||
    `Status: ${row.status}. Exact result and input downloads are retained with the run.`;
  if (row.status !== "PASS") return;
  const r = await result(key);
  if (detailId !== key) return;
  $("#detail-note").textContent =
    `${fmt(r.processed_rows, 0)} rows replayed; ${fmt(r.unobserved_timestamp_minutes, 0)} timestamp minutes absent from every selected source. Complete groups: ${fmt(r.complete_timestamp_groups, 0)} / ${fmt(r.observed_timestamp_groups, 0)} observed. Ending valuation: ${date(r.ending_nav_timestamp_ms)} Moscow. Strict historical performance: ${r.strict_historical_performance}.`;
  for (const a of r.allocations) {
    const tr = node("tr");
    for (const v of [
      a.venue + " · " + a.symbol,
      pct(a.net.net_return),
      fmt(a.realized_pnl),
      fmt(a.net_unrealized_pnl),
      fmt(a.position_cost),
      fmt(a.owned_base, 6),
    ])
      tr.append(node("td", v));
    $("#allocations").append(tr);
  }
  $("#settings-json").textContent = JSON.stringify(
    {
      settings: row.settings,
      source_status: r.source_status,
      source_coverage: r.sources,
      terminal_assessment: r.terminal_assessment,
      cycles: r.cycles,
      nav_sampling: r.nav_sampling,
      limitations: r.limitations,
      executable_sha256: row.executable_sha256,
    },
    null,
    2,
  );
}
function downloadCSV() {
  const rows = [...selected].map((id) => runs.find((r) => r.id === id));
  const table = [
    ["Measure / setting", ...rows.map((r) => r.name)],
    ["Evidence", ...rows.map(() => "UNVERIFIED SOURCE / ASSUMPTION-BASED")],
    ...metricRows(rows).map(([label, values]) => [label, ...values]),
  ];
  // Prefix spreadsheet formulas without changing ordinary negative numeric metrics.
  const quote = (v) => {
    let text = String(v ?? "");
    if (/^[=+@\t\r-]/.test(text) && !/^-?[\d,]+(?:\.\d+)?%?$/.test(text))
      text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const blob = new Blob(
    [table.map((r) => r.map(quote).join(",")).join("\r\n")],
    { type: "text/csv;charset=utf-8" },
  );
  const a = node("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = "backtest-comparison.csv";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function refresh() {
  const data = await api("/api/runs");
  const nextSnapshot = JSON.stringify(data);
  if (snapshot === nextSnapshot) return;
  snapshot = nextSnapshot;
  const previous = active;
  runs = data.runs;
  active = data.active;
  renderRuns();
  if (previous && !active) {
    await showDetails(previous);
    if (
      runs.find((r) => r.id === previous)?.status === "PASS" &&
      selected.size < 4
    ) {
      selected.add(previous);
      renderRuns();
      await compare();
    }
  }
}
async function init() {
  catalog = await api("/api/catalog");
  $("#output").textContent = catalog.output;
  if (catalog.notices.length) error(catalog.notices.join("\n"));
  for (const s of catalog.sources) {
    const label = node("label");
    const check = node("input");
    check.type = "checkbox";
    check.value = s.venue + ":" + s.market;
    check.addEventListener("change", updateCoverage);
    label.append(
      check,
      document.createTextNode(s.market),
      node("small", s.venue),
    );
    label.title = `${fmt(s.rows, 0)} rows · ${fmt(s.missing_minutes, 0)} missing minutes in full file · structurally checked; provenance unverified`;
    $("#markets").append(label);
  }
  reset();
  $("#reset").addEventListener("click", reset);
  $("#all-markets").addEventListener("click", () => {
    document
      .querySelectorAll("#markets input")
      .forEach((e) => (e.checked = true));
    updateCoverage();
    range("7");
  });
  $("#one-market").addEventListener("click", () => {
    document
      .querySelectorAll("#markets input")
      .forEach((e, i) => (e.checked = i === 0));
    updateCoverage();
    range("7");
  });
  document
    .querySelectorAll("[data-range]")
    .forEach((e) => e.addEventListener("click", () => range(e.dataset.range)));
  field("ladder").addEventListener("change", updateCoverage);
  $("#search").addEventListener("input", renderRuns);
  $("#detail-choice").addEventListener("change", (e) =>
    showDetails(e.target.value).catch((e) => error(e.message)),
  );
  $("#export").addEventListener("click", downloadCSV);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy || active) return;
    busy = true;
    $("#submit").disabled = true;
    error("");
    try {
      const payload = {};
      for (const key of Object.keys(catalog.defaults))
        payload[key] = field(key).value;
      payload.markets = marketKeys();
      for (const key of ["start", "end"])
        payload[key] = new Date(field(key).value + ":00+03:00").toISOString();
      await post("/api/runs", payload);
      await refresh();
    } catch (e) {
      error(e.message);
    } finally {
      busy = false;
      $("#submit").disabled = Boolean(active);
    }
  });
  $("#cancel").addEventListener("click", async () => {
    const button = $("#cancel");
    button.disabled = true;
    try {
      await post("/api/cancel", {});
      await refresh();
    } catch (e) {
      error(e.message);
    } finally {
      button.disabled = false;
    }
  });
  await refresh();
  const initial =
    runs.find(
      (r) => r.status === "PASS" && r.id === "study-seed_multi_strict",
    ) || runs.find((r) => r.status === "PASS");
  if (initial) {
    selected.add(initial.id);
    renderRuns();
    await compare();
    await showDetails(initial.id);
  }
  async function poll() {
    try {
      await refresh();
    } catch (e) {
      $("#connection").textContent = "Disconnected";
      error(
        "Local server unavailable. Restart it, then reload this page. " +
          e.message,
      );
    }
    setTimeout(poll, 3000);
  }
  setTimeout(poll, 3000);
}
init().catch((e) => error("Could not load the local workspace: " + e.message));
