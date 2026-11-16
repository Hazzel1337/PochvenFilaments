"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  data: null,
  view: "origins",
  filter: "",
  sort: { key: "min_jumps", dir: 1 },
  exitSort: { key: "jumps", dir: 1 },
  detailSort: { key: "jumps", dir: 1 },
  open: new Set(),
  busy: false,
};

/* ---------- formatting ---------- */

const esc = (value) =>
  String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtSec(value) {
  if (Math.abs(value) < 0.05) return "0.0";
  return value.toFixed(1);
}

function secClass(value) {
  if (value >= 0.45) return "high";
  if (value >= 0.0) return "low";
  return "null";
}

const fmtInt = (n) => n.toLocaleString("en-US");

/* ---------- sorting ---------- */

function compare(a, b, key) {
  const x = a[key];
  const y = b[key];
  if (typeof x === "number" && typeof y === "number") return x - y;
  return String(x).localeCompare(String(y), undefined, { sensitivity: "base" });
}

function sortRows(rows, key, dir) {
  return rows.slice().sort((a, b) => dir * compare(a, b, key));
}

function toggleSort(sort, key) {
  if (sort.key === key) sort.dir *= -1;
  else {
    sort.key = key;
    sort.dir = 1;
  }
}

function markSort(table, sort, prop) {
  table.querySelectorAll("thead th").forEach((th) => {
    const key = th.dataset[prop];
    if (!key) return;
    const on = key === sort.key;
    th.classList.toggle("sorted", on);
    const arrow = th.querySelector(".arrow");
    if (arrow) arrow.textContent = on ? (sort.dir > 0 ? "\u25B2" : "\u25BC") : "";
  });
}

/* ---------- rendering ---------- */

function visibleOrigins() {
  const needle = state.filter.trim().toLowerCase();
  const rows = state.data.origins.filter((o) => !needle || o.name.toLowerCase().includes(needle));
  return sortRows(rows, state.sort.key, state.sort.dir);
}

function detailRow(origin) {
  const exits = sortRows(origin.exits, state.detailSort.key, state.detailSort.dir);
  const head = `
    <tr>
      <th data-detail-sort="jumps" class="num">Jumps <span class="arrow"></span></th>
      <th data-detail-sort="ly" class="num">LY <span class="arrow"></span></th>
      <th data-detail-sort="security" class="num">Sec <span class="arrow"></span></th>
      <th data-detail-sort="name">Exit system <span class="arrow"></span></th>
      <th data-detail-sort="region">Region <span class="arrow"></span></th>
    </tr>`;
  const rows = exits
    .map(
      (e) => `
      <tr>
        <td class="num">${e.jumps}</td>
        <td class="num">${e.ly.toFixed(3)}</td>
        <td class="num sec ${secClass(e.security)}">${fmtSec(e.security)}</td>
        <td>${esc(e.name)}${e.jumps === 0 ? '<span class="badge">target</span>' : ""}</td>
        <td>${esc(e.region)}</td>
      </tr>`
    )
    .join("");
  return `<tr class="detail"><td colspan="6"><div class="detail-wrap"><table><thead>${head}</thead><tbody>${rows}</tbody></table></div></td></tr>`;
}

function renderOrigins() {
  const origins = visibleOrigins();
  const body = $("#origins-body");
  let html = "";
  origins.forEach((origin, index) => {
    const open = state.open.has(origin.name);
    html += `
      <tr class="origin${open ? " open" : ""}" data-origin="${esc(origin.name)}">
        <td class="rank">${index + 1}</td>
        <td class="name"><span class="caret">${open ? "\u25BE" : "\u25B8"}</span>${esc(origin.name)}</td>
        <td class="num">${origin.exits.length}</td>
        <td class="num">${origin.min_jumps}</td>
        <td class="num">${origin.avg_jumps.toFixed(1)}</td>
        <td class="num">${origin.max_jumps}</td>
      </tr>`;
    if (open) html += detailRow(origin);
  });
  body.innerHTML = html;
  markSort($("#origins-table"), state.sort, "sort");
  body.querySelectorAll(".detail-wrap table").forEach((t) => markSort(t, state.detailSort, "detailSort"));
  $("#empty").hidden = !state.data || state.view !== "origins" || origins.length > 0;
}

function flatRows() {
  const needle = state.filter.trim().toLowerCase();
  const rows = [];
  for (const origin of state.data.origins) {
    for (const exit of origin.exits) {
      if (needle && !origin.name.toLowerCase().includes(needle) && !exit.name.toLowerCase().includes(needle)) {
        continue;
      }
      rows.push({ origin: origin.name, ...exit });
    }
  }
  return sortRows(rows, state.exitSort.key, state.exitSort.dir);
}

function renderExits() {
  const rows = flatRows();
  $("#exits-body").innerHTML = rows
    .map(
      (e) => `
      <tr>
        <td>${esc(e.origin)}</td>
        <td>${esc(e.name)}${e.jumps === 0 ? '<span class="badge">target</span>' : ""}</td>
        <td>${esc(e.region)}</td>
        <td class="num sec ${secClass(e.security)}">${fmtSec(e.security)}</td>
        <td class="num">${e.ly.toFixed(3)}</td>
        <td class="num">${e.jumps}</td>
      </tr>`
    )
    .join("");
  markSort($("#exits-table"), state.exitSort, "sort");
  $("#empty").hidden = !state.data || state.view !== "exits" || rows.length > 0;
}

function render() {
  if (!state.data) return;
  $("#toolbar").hidden = false;
  if (state.view === "origins") {
    renderOrigins();
  } else {
    renderExits();
  }
  const meta = state.data.meta;
  $("#count").textContent = `${state.data.origins.length} origins \u00B7 ${fmtInt(meta.exit_pairs)} exits`;
}

/* ---------- status ---------- */

function setStatus(text, isError = false) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("error", isError);
}

/* ---------- data loading ---------- */

async function load(target, minSec) {
  if (state.busy) return;
  state.busy = true;
  const button = $("#go");
  button.disabled = true;
  setStatus("Routing\u2026");

  try {
    const params = new URLSearchParams({ target });
    if (minSec) params.set("min_sec", String(minSec));
    const response = await fetch(`/api/route?${params.toString()}`);
    const body = await response.json();
    if (!response.ok) throw body;

    state.data = body;
    state.open.clear();
    if (body.origins.length) state.open.add(body.origins[0].name);
    render();

    const t = body.target;
    const parts = [
      `${t.name} \u2014 ${t.region}, sec ${fmtSec(t.security)}`,
      `${fmtInt(body.meta.reachable_systems)} gate-connected systems`,
    ];
    if (body.min_sec !== null) parts.push(`exits \u2265 ${body.min_sec}`);
    setStatus(parts.join(" \u00B7 "));

    const url = new URL(location.href);
    url.searchParams.set("target", t.name);
    if (body.min_sec !== null) url.searchParams.set("min_sec", String(body.min_sec));
    else url.searchParams.delete("min_sec");
    history.replaceState(null, "", url);

    $("#provenance").textContent = `SDE cache ${body.meta.generated_utc.replace("T", " ").replace("Z", " UTC")} \u00B7 ranges computed from star coordinates`;
  } catch (err) {
    const message = err && err.error ? err.error : "request failed";
    const suggestions = err && err.suggestions && err.suggestions.length ? ` Did you mean: ${err.suggestions.join(", ")}?` : "";
    setStatus(message + suggestions, true);
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}

async function loadSystemList() {
  try {
    const response = await fetch("/api/systems");
    const body = await response.json();
    const fragment = document.createDocumentFragment();
    for (const name of body.systems) {
      const option = document.createElement("option");
      option.value = name;
      fragment.append(option);
    }
    $("#system-list").append(fragment);
  } catch {
    /* datalist is a convenience only */
  }
}

/* ---------- events ---------- */

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".tabs button").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $("#origins-panel").hidden = view !== "origins";
  $("#exits-panel").hidden = view !== "exits";
  const url = new URL(location.href);
  if (view === "exits") url.searchParams.set("view", "exits");
  else url.searchParams.delete("view");
  history.replaceState(null, "", url);
  render();
}

function init() {
  document.querySelectorAll("thead th[data-sort]").forEach((th) => {
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    th.append(arrow);
  });

  $("#route-form").addEventListener("submit", (event) => {
    event.preventDefault();
    load($("#target").value.trim(), $("#min-sec").value.trim());
  });

  $("#filter").addEventListener("input", (event) => {
    state.filter = event.target.value;
    render();
  });

  document.addEventListener("click", (event) => {
    const tab = event.target.closest(".tabs button");
    if (tab) return switchView(tab.dataset.view);

    const detailHeader = event.target.closest("th[data-detail-sort]");
    if (detailHeader) {
      toggleSort(state.detailSort, detailHeader.dataset.detailSort);
      return render();
    }

    const header = event.target.closest("thead th[data-sort]");
    if (header) {
      const table = header.closest("table");
      if (table.id === "origins-table") toggleSort(state.sort, header.dataset.sort);
      else if (table.id === "exits-table") toggleSort(state.exitSort, header.dataset.sort);
      return render();
    }

    const row = event.target.closest("tr.origin");
    if (row) {
      const name = row.dataset.origin;
      if (state.open.has(name)) state.open.delete(name);
      else state.open.add(name);
      renderOrigins();
    }
  });

  loadSystemList();

  const params = new URLSearchParams(location.search);
  const target = params.get("target") || "Jita";
  const minSec = params.get("min_sec") || "";
  $("#target").value = target;
  $("#min-sec").value = minSec;
  if (params.get("view") === "exits") switchView("exits");
  load(target, minSec);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
