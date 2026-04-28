// aafbrowser web GUI — vanilla JS, no build step.
//
// State model is minimal: server holds the open file, frontend caches the
// Mob index per open and re-renders on filter changes. All click-to-jump
// and inspector behavior in steps 9-12 hangs off this same state.
//
// XSS posture: this is a local dev tool that loads paths the user types,
// but AAF property values can include arbitrary strings. The `el` helper
// only supports textContent (no innerHTML), and every value rendered into
// the DOM goes through it.

"use strict";

// ---------- tiny helpers ----------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v === true) node.setAttribute(k, "");
    else if (v === false || v === null || v === undefined) {/* skip */}
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
};

const tailMobId = (urn) => {
  // Display the last 8 hex chars of a URN for compactness.
  if (!urn) return "";
  const last = urn.replace(/[^0-9a-fA-F]/g, "");
  return last.slice(-8);
};

// ---------- state ----------

const state = {
  file: null,        // {path, sha256, mob_count, classes_summary}
  mobs: [],          // [{mob_id, class, name, slot_count}, ...]
  filter: "",        // case-insensitive substring on name
  selected: null,    // mob_id of currently selected row
};

// ---------- API wrappers ----------

const api = {
  async open(path) {
    const r = await fetch("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async close() {
    const r = await fetch("/api/close", { method: "POST" });
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async file() {
    const r = await fetch("/api/file");
    if (r.status === 404) return null;
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async mobs() {
    const r = await fetch("/api/mobs");
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
};

async function apiError(r) {
  let body = null;
  try { body = await r.json(); } catch (_) { /* fall through */ }
  const msg = body && body.detail ? body.detail
            : body && body.error  ? body.error
            : `${r.status} ${r.statusText}`;
  const err = new Error(msg);
  err.status = r.status;
  err.body = body;
  return err;
}

// ---------- topbar / open dialog ----------

function setFileStatus() {
  const status = $("#file-status");
  const closeBtn = $("#btn-close");
  if (state.file) {
    status.textContent = state.file.path;
    status.classList.remove("muted");
    closeBtn.hidden = false;
  } else {
    status.textContent = "No file open";
    status.classList.add("muted");
    closeBtn.hidden = true;
  }
}

function openDialog() {
  const dlg = $("#open-dialog");
  $("#open-error").hidden = true;
  $("#open-error").textContent = "";
  $("#open-path").value = "";
  if (typeof dlg.showModal === "function") dlg.showModal();
  else dlg.setAttribute("open", "");
  setTimeout(() => $("#open-path").focus(), 0);
}

function wireTopbar() {
  $("#btn-open").addEventListener("click", openDialog);
  $("#btn-close").addEventListener("click", async () => {
    try {
      await api.close();
    } catch (_) {
      // fall through; we still reset locally
    }
    state.file = null;
    state.mobs = [];
    state.selected = null;
    setFileStatus();
    renderMobList();
  });

  $("#open-form").addEventListener("submit", async (ev) => {
    const action = ev.submitter && ev.submitter.value;
    if (action === "cancel") {
      // native dialog form behavior: closes
      return;
    }
    ev.preventDefault();
    const path = $("#open-path").value.trim();
    const errEl = $("#open-error");
    errEl.hidden = true;
    if (!path) {
      errEl.textContent = "path required";
      errEl.hidden = false;
      return;
    }
    try {
      const meta = await api.open(path);
      state.file = meta;
      state.selected = null;
      setFileStatus();
      $("#open-dialog").close();
      const mobs = await api.mobs();
      state.mobs = mobs.mobs;
      renderMobList();
    } catch (e) {
      errEl.textContent = e.message || String(e);
      errEl.hidden = false;
    }
  });
}

// ---------- left pane: tabs + filter + Mob list ----------

function wireTabs() {
  $$(".tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tabs .tab").forEach((t) => t.classList.toggle("active", t === tab));
      const which = tab.dataset.tab;
      $$(".tab-pane").forEach((p) =>
        p.classList.toggle("active", p.dataset.pane === which)
      );
    });
  });
}

function wireFilter() {
  $("#mob-filter").addEventListener("input", (ev) => {
    state.filter = ev.target.value.trim().toLowerCase();
    renderMobList();
  });
}

function renderMobList() {
  const root = $("#mob-list");
  root.replaceChildren();
  if (!state.file) {
    root.appendChild(el("p", { class: "muted", style: "padding:14px;" },
      "No file open."));
    return;
  }
  const filter = state.filter;
  const matchName = (n) =>
    !filter || (n && n.toLowerCase().includes(filter));

  // Group by class, preserving the eager-index iteration order within each.
  const groups = new Map();
  for (const m of state.mobs) {
    if (!matchName(m.name)) continue;
    if (!groups.has(m.class)) groups.set(m.class, []);
    groups.get(m.class).push(m);
  }

  if (groups.size === 0) {
    root.appendChild(el("p", { class: "muted", style: "padding:14px;" },
      filter ? "No matches." : "No Mobs in this file."));
    return;
  }

  // Stable class ordering: Composition / Master / Source / others alpha
  const classOrder = (c) => {
    if (c === "CompositionMob") return 0;
    if (c === "MasterMob") return 1;
    if (c === "SourceMob") return 2;
    return 3;
  };
  const sortedClasses = Array.from(groups.keys()).sort((a, b) =>
    classOrder(a) - classOrder(b) || a.localeCompare(b)
  );

  const sectionTpl = $("#tpl-class-section");
  const rowTpl = $("#tpl-mob-row");

  for (const className of sortedClasses) {
    const items = groups.get(className);
    const frag = sectionTpl.content.cloneNode(true);
    const details = frag.querySelector("details");
    // Auto-open small sections; collapse big ones to keep scroll usable.
    if (items.length <= 50) details.open = true;
    frag.querySelector(".class-name").textContent = className;
    frag.querySelector(".class-count").textContent = `(${items.length})`;
    const rowsHost = frag.querySelector(".class-rows");
    for (const m of items) {
      const row = rowTpl.content.cloneNode(true);
      const div = row.querySelector(".mob-row");
      div.dataset.mobId = m.mob_id;
      const nameEl = row.querySelector(".name");
      if (m.name) {
        nameEl.textContent = m.name;
      } else {
        nameEl.textContent = "untitled";
        nameEl.classList.add("untitled");
      }
      const idEl = row.querySelector(".badge.mob-id");
      idEl.textContent = tailMobId(m.mob_id);
      idEl.title = m.mob_id;
      div.addEventListener("click", () => selectMob(m.mob_id));
      if (state.selected === m.mob_id) div.classList.add("selected");
      rowsHost.appendChild(row);
    }
    root.appendChild(frag);
  }
}

// Stub — full implementation in step 9
async function selectMob(mobId) {
  state.selected = mobId;
  $$(".mob-row").forEach((r) =>
    r.classList.toggle("selected", r.dataset.mobId === mobId)
  );
  // Inspector rendering lands in step 9
  const inspector = $("#inspector");
  inspector.replaceChildren(
    el("p", { class: "muted" },
      `Selected ${tailMobId(mobId)} — full inspector arrives in step 9.`)
  );
}

// ---------- bootstrap ----------

async function init() {
  wireTopbar();
  wireTabs();
  wireFilter();
  setFileStatus();
  renderMobList();

  // If a file was opened via `aafbrowser web <path>`, the server already
  // has it loaded — fetch metadata and mob list at startup.
  try {
    const meta = await api.file();
    if (meta) {
      state.file = meta;
      setFileStatus();
      const mobs = await api.mobs();
      state.mobs = mobs.mobs;
      renderMobList();
    }
  } catch (_) { /* no-op; user can open manually */ }
}

document.addEventListener("DOMContentLoaded", init);
