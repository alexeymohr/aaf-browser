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
  async object({ mob_id, path }) {
    const qs = mob_id
      ? "mob_id=" + encodeURIComponent(mob_id)
      : "path=" + encodeURIComponent(path);
    const r = await fetch("/api/object?" + qs);
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

// ---------- inspector ----------

// Breadcrumb model: an array of {label, fetch} segments. Each fetch is
// invoked with no args and returns a promise resolving to the same
// envelope /api/object returns ({sha256, object}). The current head is
// rendered; clicking an earlier crumb pops back to it.
const inspectorState = {
  trail: [], // [{label, fetch}]
  current: null, // last response (envelope.object)
};

async function selectMob(mobId) {
  state.selected = mobId;
  $$(".mob-row").forEach((r) =>
    r.classList.toggle("selected", r.dataset.mobId === mobId)
  );
  const found = state.mobs.find((m) => m.mob_id === mobId);
  const label = found && found.name
    ? `${found.class}:${found.name}`
    : `${(found && found.class) || "Mob"}:${tailMobId(mobId)}`;
  inspectorState.trail = [{
    label,
    fetch: () => api.object({ mob_id: mobId }),
  }];
  await renderInspectorAt(0);
}

async function renderInspectorAt(index) {
  // Truncate trail to index + render that node.
  inspectorState.trail = inspectorState.trail.slice(0, index + 1);
  const seg = inspectorState.trail[index];
  if (!seg) return;
  showInspectorLoading(seg.label);
  let envelope;
  try {
    envelope = await seg.fetch();
  } catch (e) {
    showInspectorError(e);
    return;
  }
  inspectorState.current = envelope.object;
  renderBreadcrumb();
  renderInspectorObject(envelope.object);
}

function showInspectorLoading(label) {
  renderBreadcrumb();
  $("#inspector").replaceChildren(
    el("p", { class: "muted" }, `Loading ${label}…`)
  );
}

function showInspectorError(e) {
  $("#inspector").replaceChildren(
    el("p", { class: "error" }, `Error: ${e.message || String(e)}`)
  );
}

function renderBreadcrumb() {
  const root = $("#breadcrumb");
  root.replaceChildren();
  if (inspectorState.trail.length === 0) {
    root.appendChild(el("span", { class: "muted" }, "no selection"));
    return;
  }
  inspectorState.trail.forEach((seg, i) => {
    if (i > 0) root.appendChild(el("span", { class: "sep" }, "/"));
    const isHead = i === inspectorState.trail.length - 1;
    const node = el(
      "span",
      isHead ? { class: "crumb head" } : {
        class: "crumb",
        onclick: () => renderInspectorAt(i),
      },
      seg.label
    );
    root.appendChild(node);
  });
}

function renderInspectorObject(obj) {
  const inspector = $("#inspector");
  inspector.replaceChildren();
  if (!obj) {
    inspector.appendChild(el("p", { class: "muted" }, "<null>"));
    return;
  }
  if (obj._type === "scalar_leaf") {
    inspector.appendChild(el("h3", {}, "scalar leaf"));
    inspector.appendChild(renderValueCell(obj.value));
    return;
  }
  if (obj._type === "aaf_object_cycle") {
    inspector.appendChild(el("p", { class: "error" },
      `cycle: ${obj.class} ${obj.mob_id || ""}`));
    return;
  }

  // Header
  const header = el("div", { class: "obj-header" }, [
    el("span", { class: "class-tag" }, obj.class || "?"),
    obj.name
      ? el("span", { class: "obj-name" }, obj.name)
      : null,
    obj.mob_id
      ? el(
          "span",
          {
            class: "obj-mob-id",
            title: "click to copy: " + obj.mob_id,
            onclick: () => copyToClipboard(obj.mob_id),
          },
          obj.mob_id
        )
      : null,
  ].filter(Boolean));
  inspector.appendChild(header);

  // Properties
  const table = el("div", { class: "props-table" });
  const props = obj.properties || {};
  const propNames = Object.keys(props);
  if (propNames.length === 0) {
    inspector.appendChild(el("p", { class: "muted" }, "no properties"));
    return;
  }
  for (const pname of propNames) {
    const value = props[pname];
    const { typeBadge, valueCell } = renderProperty(pname, value);
    table.appendChild(el("div", { class: "prop-name" }, pname));
    table.appendChild(typeBadge);
    table.appendChild(valueCell);
  }
  inspector.appendChild(table);
}

function renderProperty(pname, value) {
  // value is whatever core.serialize_property emitted: a primitive, a
  // typed envelope ({_type: rational | datetime | auid | mobid | bytes |
  // weakref | aaf_object | aaf_object_cycle}), an array (vector/set), or
  // null.
  let typeName = describeType(value);
  const typeBadge = el("span", { class: "prop-type" }, typeName);
  const valueCell = el("div", { class: "prop-value" });
  populateValue(valueCell, value, pname);
  return { typeBadge, valueCell };
}

function describeType(v) {
  if (v === null) return "null";
  if (Array.isArray(v)) return `vector[${v.length}]`;
  if (typeof v === "string") return "str";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (typeof v === "object") {
    if (v._type) return v._type;
    return "object";
  }
  return typeof v;
}

function populateValue(cell, value, pname) {
  if (value === null) {
    cell.appendChild(el("span", { class: "muted" }, "null"));
    return;
  }
  if (Array.isArray(value)) {
    populateVector(cell, value, pname);
    return;
  }
  if (typeof value === "object") {
    populateTypedEnvelope(cell, value, pname);
    return;
  }
  cell.classList.add("scalar");
  cell.appendChild(document.createTextNode(String(value)));
}

function populateVector(cell, items, pname) {
  if (items.length === 0) {
    cell.appendChild(el("span", { class: "muted" }, "[empty]"));
    return;
  }
  const summary = el("span", { class: "expandable" },
    `[${items.length} items] ▶`);
  cell.appendChild(summary);
  let expanded = false;
  const host = el("div", { class: "nested-obj", hidden: true });
  cell.appendChild(host);
  summary.addEventListener("click", () => {
    expanded = !expanded;
    host.hidden = !expanded;
    summary.textContent = expanded
      ? `[${items.length} items] ▼`
      : `[${items.length} items] ▶`;
    if (expanded && host.childElementCount === 0) {
      items.forEach((item, i) => {
        const row = el("div", { class: "vec-item" }, [
          el("span", { class: "muted" }, `[${i}] `),
        ]);
        const inner = el("div", { class: "prop-value" });
        populateValue(inner, item, `${pname}/${i}`);
        row.appendChild(inner);
        host.appendChild(row);
      });
    }
  });
}

function populateTypedEnvelope(cell, v, pname) {
  switch (v._type) {
    case "rational":
      cell.appendChild(document.createTextNode(
        `${v.num}/${v.den}` + (v.value != null ? `  (${v.value})` : "")));
      return;
    case "datetime":
      cell.appendChild(document.createTextNode(v.value));
      return;
    case "auid":
      cell.appendChild(el("span", { class: "badge" }, v.value));
      return;
    case "mobid":
      // Step 10 wires this to navigate; for now show as a tail-8 badge.
      cell.appendChild(renderMobIdBadge(v.value));
      return;
    case "umid":
      cell.appendChild(el("span", { class: "badge" }, v.value));
      return;
    case "bytes":
      cell.appendChild(document.createTextNode(
        `<${v.length} bytes${v.truncated ? ", truncated" : ""}>`));
      return;
    case "weakref":
      populateWeakRef(cell, v);
      return;
    case "aaf_object":
      // StrongRef nested object: inline expand.
      populateNestedObject(cell, v, pname);
      return;
    case "aaf_object_cycle":
      cell.appendChild(el("span", { class: "error" },
        `<cycle: ${v.class} ${v.mob_id || ""}>`));
      return;
    case "unknown":
      cell.appendChild(document.createTextNode(
        `<unknown ${v.python_type}: ${v.repr}>`));
      return;
    default:
      cell.appendChild(document.createTextNode(JSON.stringify(v)));
  }
}

function populateNestedObject(cell, obj, pname) {
  const summary = el("span", { class: "expandable" },
    summarizeNested(obj) + " ▶");
  cell.appendChild(summary);
  const host = el("div", { class: "nested-obj", hidden: true });
  cell.appendChild(host);
  let expanded = false;
  summary.addEventListener("click", () => {
    expanded = !expanded;
    host.hidden = !expanded;
    summary.textContent = summarizeNested(obj) + (expanded ? " ▼" : " ▶");
    if (expanded && host.childElementCount === 0) {
      // Render the nested object's properties inline as a fresh table
      const tbl = el("div", { class: "props-table" });
      const props = obj.properties || {};
      for (const pname2 of Object.keys(props)) {
        const { typeBadge, valueCell } =
          renderProperty(pname2, props[pname2]);
        tbl.appendChild(el("div", { class: "prop-name" }, pname2));
        tbl.appendChild(typeBadge);
        tbl.appendChild(valueCell);
      }
      host.appendChild(tbl);
    }
  });
}

function summarizeNested(obj) {
  const cls = obj.class || "?";
  if (obj.name) return `${cls} "${obj.name}"`;
  if (obj.mob_id) return `${cls} ${tailMobId(obj.mob_id)}`;
  return cls;
}

function populateWeakRef(cell, v) {
  cell.classList.add("weakref");
  const summary = el(
    "span",
    { class: "expandable" },
    `→ ${v.target_class}` +
      (v.target_name ? ` "${v.target_name}"` : "") +
      " ▶"
  );
  cell.appendChild(summary);
  const detail = el("div", { class: "nested-obj", hidden: true });
  cell.appendChild(detail);
  let expanded = false;
  summary.addEventListener("click", () => {
    expanded = !expanded;
    detail.hidden = !expanded;
    summary.textContent =
      `→ ${v.target_class}` +
      (v.target_name ? ` "${v.target_name}"` : "") +
      (expanded ? " ▼" : " ▶");
    if (expanded && detail.childElementCount === 0) {
      // Dictionary targets are not exposed as Mobs, so we render a small
      // identifying panel rather than navigating.
      const tbl = el("div", { class: "props-table" });
      const rows = [
        ["target_class", v.target_class || "?"],
        ["target_name", v.target_name || ""],
        ["target_auid", v.target_auid || ""],
      ];
      for (const [k, val] of rows) {
        tbl.appendChild(el("div", { class: "prop-name" }, k));
        tbl.appendChild(el("span", { class: "prop-type" }, "weakref"));
        const vc = el("div", { class: "prop-value scalar" });
        vc.textContent = val;
        tbl.appendChild(vc);
      }
      detail.appendChild(tbl);
    }
  });
}

function renderMobIdBadge(urn) {
  // Clicking navigates the inspector and the left list.
  return el(
    "span",
    {
      class: "badge mob-id",
      title: urn,
      onclick: (ev) => {
        ev.stopPropagation();
        navigateToMobId(urn);
      },
    },
    tailMobId(urn)
  );
}

async function navigateToMobId(urn) {
  const known = state.mobs.find((m) => m.mob_id === urn);
  if (known) {
    // Ensure the class section is open and the row is in view.
    const row = document.querySelector(
      `.mob-row[data-mob-id="${cssEscape(urn)}"]`
    );
    if (row) {
      const details = row.closest("details.class-section");
      if (details && !details.open) details.open = true;
      row.scrollIntoView({ block: "nearest" });
    }
    selectMob(urn);
    return;
  }
  // Reference points outside the eager index. Resolve via /api/resolve so
  // we get class/name back, then load via /api/object directly.
  try {
    const r = await fetch("/api/resolve?ref=" + encodeURIComponent(urn));
    if (!r.ok) {
      showInspectorError(await apiError(r));
      return;
    }
    const info = await r.json();
    inspectorState.trail = [{
      label: `${info.class}:${info.name || tailMobId(urn)} (orphan)`,
      fetch: () => api.object({ mob_id: urn }),
    }];
    state.selected = null;
    $$(".mob-row").forEach((row) => row.classList.remove("selected"));
    await renderInspectorAt(0);
  } catch (e) {
    showInspectorError(e);
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
  } else {
    fallbackCopy(text);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (_) { /* ignore */ }
  document.body.removeChild(ta);
}

function cssEscape(s) {
  // CSS.escape may not exist in all environments; fall back to a
  // hex-style escape on every non-alnum character.
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(s);
  return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) =>
    "\\" + c.charCodeAt(0).toString(16) + " "
  );
}

function renderValueCell(v) {
  const c = el("div", { class: "prop-value" });
  populateValue(c, v, "");
  return c;
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
