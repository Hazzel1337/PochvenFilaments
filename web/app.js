"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  data: null,
  view: "exits",
  filter: "",
  sort: { key: "min_jumps", dir: 1 },
  exitSort: { key: "jumps", dir: 1 },
  detailSort: { key: "jumps", dir: 1 },
  open: new Set(),
  openExits: new Set(),
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

/* ---------- data shaping ---------- */

// One row per 2.5 LY exit system, with every Pochven origin that can reach it.
function exitGroups() {
  const map = new Map();
  for (const origin of state.data.origins) {
    for (const exit of origin.exits) {
      let group = map.get(exit.id);
      if (!group) {
        group = {
          id: exit.id,
          name: exit.name,
          region: exit.region,
          security: exit.security,
          jumps: exit.jumps,
          links: [],
        };
        map.set(exit.id, group);
      }
      group.links.push({
        origin: origin.name,
        constellation: origin.constellation || "",
        ly: exit.ly,
      });
    }
  }
  const groups = [...map.values()];
  for (const group of groups) {
    group.links.sort((a, b) => a.ly - b.ly);
    group.ly = group.links[0].ly;
    group.origin = group.links[0].origin;
    group.krai = group.links[0].constellation;
  }
  return groups;
}

function groupMatches(group, needle) {
  if (!needle) return true;
  if (group.name.toLowerCase().includes(needle)) return true;
  if (group.region.toLowerCase().includes(needle)) return true;
  return group.links.some(
    (link) =>
      link.origin.toLowerCase().includes(needle) ||
      (link.constellation || "").toLowerCase().includes(needle)
  );
}

/* ---------- rendering ---------- */

function renderOrigins() {
  const needle = state.filter.trim().toLowerCase();
  const rows = state.data.origins.filter(
    (origin) =>
      !needle ||
      origin.name.toLowerCase().includes(needle) ||
      (origin.constellation || "").toLowerCase().includes(needle)
  );
  const origins = sortRows(rows, state.sort.key, state.sort.dir);
  const body = $("#origins-body");
  let html = "";
  let exits = 0;
  origins.forEach((origin, index) => {
    const open = state.open.has(origin.name);
    exits += origin.exits.length;
    html += `
      <tr class="origin${open ? " open" : ""}" data-origin="${esc(origin.name)}">
        <td class="rank">${index + 1}</td>
        <td class="name"><span class="caret">${open ? "\u25BE" : "\u25B8"}</span>${esc(origin.name)}</td>
        <td class="krai">${esc(origin.constellation || "")}</td>
        <td class="num">${origin.exits.length}</td>
        <td class="num">${origin.min_jumps}</td>
        <td class="num">${origin.avg_jumps.toFixed(1)}</td>
        <td class="num">${origin.max_jumps}</td>
      </tr>`;
    if (open) html += originDetail(origin);
  });
  body.innerHTML = html;
  markSort($("#origins-table"), state.sort, "sort");
  body.querySelectorAll(".detail-wrap table").forEach((t) => markSort(t, state.detailSort, "detailSort"));
  $("#empty").hidden = origins.length > 0;
  return `${origins.length} origins \u00B7 ${fmtInt(exits)} exits`;
}

function originDetail(origin) {
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
  return `<tr class="detail"><td colspan="7"><div class="detail-wrap"><table><thead>${head}</thead><tbody>${rows}</tbody></table></div></td></tr>`;
}

function renderExits() {
  const needle = state.filter.trim().toLowerCase();
  const groups = sortRows(
    exitGroups().filter((group) => groupMatches(group, needle)),
    state.exitSort.key,
    state.exitSort.dir
  );
  const body = $("#exits-body");
  let links = 0;
  let html = "";
  for (const group of groups) {
    links += group.links.length;
    const open = state.openExits.has(group.name);
    const linkable = group.links.length > 1;
    html += `
      <tr class="exit${linkable ? " linkable" : ""}" data-exit="${esc(group.name)}">
        <td class="num">${group.jumps}</td>
        <td>${esc(group.name)}${group.jumps === 0 ? '<span class="badge">target</span>' : ""}</td>
        <td class="num sec ${secClass(group.security)}">${fmtSec(group.security)}</td>
        <td>${esc(group.region)}</td>
        <td class="num">${group.ly.toFixed(3)}</td>
        <td>${esc(group.origin)}${linkable ? `<span class="more">+${group.links.length - 1}</span>` : ""}</td>
        <td class="krai">${esc(group.krai)}</td>
      </tr>`;
    if (open && linkable) html += exitDetail(group);
  }
  body.innerHTML = html;
  markSort($("#exits-table"), state.exitSort, "sort");
  $("#empty").hidden = groups.length > 0;
  return `${fmtInt(groups.length)} exit systems \u00B7 ${fmtInt(links)} origin links`;
}

function exitDetail(group) {
  const rows = group.links
    .map(
      (link) => `
      <tr>
        <td>${esc(link.origin)}</td>
        <td class="krai">${esc(link.constellation)}</td>
        <td class="num">${link.ly.toFixed(3)}</td>
      </tr>`
    )
    .join("");
  return `
    <tr class="detail"><td colspan="7"><div class="detail-wrap">
      <div class="detail-title">${esc(group.name)} is within 2.5 LY of these Pochven systems</div>
      <table>
        <thead><tr><th>Pochven origin</th><th>Krai</th><th class="num">LY from origin</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div></td></tr>`;
}

function render() {
  if (!state.data) return;
  $("#toolbar").hidden = false;
  $("#count").textContent = state.view === "exits" ? renderExits() : renderOrigins();
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
    state.openExits.clear();
    if (state.view === "origins" && body.origins.length) state.open.add(body.origins[0].name);
    const pinned = new URLSearchParams(location.search).get("open");
    if (pinned) state.openExits.add(pinned);
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

    $("#provenance").textContent = `SDE cache ${body.meta.generated_utc.replace("T", " ").replace("Z", " UTC")} \u00B7 krai = Pochven constellation`;
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
  $("#exits-panel").hidden = view !== "exits";
  $("#origins-panel").hidden = view !== "origins";
  const url = new URL(location.href);
  if (view === "origins") url.searchParams.set("view", "origins");
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

    const exitRow = event.target.closest("tr.exit.linkable");
    if (exitRow) {
      const name = exitRow.dataset.exit;
      if (state.openExits.has(name)) state.openExits.delete(name);
      else state.openExits.add(name);
      return render();
    }

    const originRow = event.target.closest("tr.origin");
    if (originRow) {
      const name = originRow.dataset.origin;
      if (state.open.has(name)) state.open.delete(name);
      else state.open.add(name);
      return render();
    }
  });

  loadSystemList();

  const params = new URLSearchParams(location.search);
  const target = params.get("target") || "Jita";
  const minSec = params.get("min_sec") || "";
  $("#target").value = target;
  $("#min-sec").value = minSec;
  if (params.get("view") === "origins") switchView("origins");
  load(target, minSec);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
