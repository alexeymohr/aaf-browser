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
  async pickFile() {
    // Prefer pywebview's native NSOpenPanel JS bridge when the page
    // is rendered inside the bundled .app's WKWebView. Falls back to
    // the osascript-based HTTP endpoint when running in a regular
    // browser (i.e. via `aafbrowser web` from a pip install).
    if (
      window.pywebview &&
      window.pywebview.api &&
      typeof window.pywebview.api.pick_file === "function"
    ) {
      const path = await window.pywebview.api.pick_file();
      return { path: path || null };
    }
    // HTTP fallback: returns {path: string|null} on 200, throws on
    // 501 / 5xx so the caller can fall back to the text-paste dialog.
    const r = await fetch("/api/pick_file", { method: "POST" });
    if (r.status === 501) {
      const err = new Error("native picker unavailable");
      err.status = 501;
      throw err;
    }
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
  async cfbTree({ includeMetadict }) {
    const qs = includeMetadict ? "?include_metadict=1" : "";
    const r = await fetch("/api/cfb/tree" + qs);
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async cfbStream({ path, offset = 0, length = 4096 }) {
    const qs = new URLSearchParams({
      path,
      offset: String(offset),
      length: String(length),
    });
    const r = await fetch("/api/cfb/stream?" + qs.toString());
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async find({ pattern, scope, layer, classes }) {
    const qs = new URLSearchParams({
      pattern, in: scope, layer,
    });
    for (const c of classes || []) qs.append("class", c);
    const r = await fetch("/api/find?" + qs.toString());
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async walk({ mob_id, path, slot_id, max_hops }) {
    const qs = new URLSearchParams();
    if (mob_id) qs.set("mob_id", mob_id);
    if (path) qs.set("path", path);
    if (slot_id != null) qs.set("slot_id", String(slot_id));
    if (max_hops != null) qs.set("max_hops", String(max_hops));
    const r = await fetch("/api/walk?" + qs.toString());
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

// Load an AAF by path: hit /api/open, reset all per-file UI state,
// fetch the Mob index. Used by both the native picker and the
// text-paste fallback dialog.
async function loadAafFile(path) {
  const meta = await api.open(path);
  state.file = meta;
  state.selected = null;
  cfbState.tree = null;
  cfbState.selectedPath = null;
  cfbState.expanded = new Set();
  inspectorState.trail = [];
  inspectorState.current = null;
  findState.classes = new Set();
  setFileStatus();
  renderClassFilterOptions();
  const mobs = await api.mobs();
  state.mobs = mobs.mobs;
  renderMobList();
}

// Open click: try the native picker first. If the platform doesn't
// support it (501) or the call errors, fall back to the text-paste
// dialog. User cancellation in the native picker is a no-op.
async function handleOpenClick() {
  try {
    const { path } = await api.pickFile();
    if (!path) return;  // user cancelled
    try {
      await loadAafFile(path);
    } catch (e) {
      openDialog();
      $("#open-path").value = path;
      const errEl = $("#open-error");
      errEl.textContent = e.message || String(e);
      errEl.hidden = false;
    }
  } catch (e) {
    // 501 (no native picker) or any other error → fall back to dialog.
    openDialog();
    if (e && e.status !== 501) {
      const errEl = $("#open-error");
      errEl.textContent = e.message || String(e);
      errEl.hidden = false;
    }
  }
}

function wireTopbar() {
  $("#btn-open").addEventListener("click", handleOpenClick);
  $("#btn-quit").addEventListener("click", async (ev) => {
    ev.preventDefault();
    try {
      await fetch("/api/quit", { method: "POST" });
    } catch (_) {
      // Server may already be gone by the time the response would
      // arrive — that's fine, that's exactly what we wanted.
    }
    // Replace the page with a "stopped" message so it's clear the
    // tab is no longer interactive.
    document.body.replaceChildren(
      el("p", { style: "padding:24px; font-family:var(--mono); color:var(--fg-muted);" },
        "aafbrowser stopped. You can close this tab.")
    );
  });
  // Closing the tab also kills the server. sendBeacon survives the
  // page-unload race that fetch() loses to.
  window.addEventListener("beforeunload", () => {
    try { navigator.sendBeacon("/api/quit"); } catch (_) {}
  });
  $("#btn-close").addEventListener("click", async () => {
    try {
      await api.close();
    } catch (_) {
      // fall through; we still reset locally
    }
    state.file = null;
    state.mobs = [];
    state.selected = null;
    cfbState.tree = null;
    cfbState.selectedPath = null;
    cfbState.expanded = new Set();
    inspectorState.trail = [];
    inspectorState.current = null;
    findState.classes = new Set();
    setFileStatus();
    renderMobList();
    renderClassFilterOptions();
    $("#cfb-tree").replaceChildren();
    $("#inspector").replaceChildren(
      el("p", { class: "muted" }, "Open a file, then select a Mob to inspect it.")
    );
    $("#breadcrumb").replaceChildren();
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
      await loadAafFile(path);
      $("#open-dialog").close();
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
      if (which === "cfb") loadCfbTreeIfNeeded();
    });
  });
}

function wireCfbControls() {
  $("#cfb-include-metadict").addEventListener("change", () => {
    cfbState.tree = null; // force reload
    loadCfbTreeIfNeeded(true);
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

// ---------- CFB tab ----------

const cfbState = {
  tree: null,             // last loaded tree
  selectedPath: null,     // path of currently selected entry
  expanded: new Set(),    // set of paths whose storages are expanded
};

async function loadCfbTreeIfNeeded(force = false) {
  if (!state.file) {
    $("#cfb-tree").replaceChildren(
      el("p", { class: "muted", style: "padding:14px;" }, "No file open.")
    );
    return;
  }
  if (cfbState.tree && !force) return;
  $("#cfb-tree").replaceChildren(
    el("p", { class: "muted", style: "padding:14px;" }, "Loading CFB tree…")
  );
  try {
    const includeMetadict = $("#cfb-include-metadict").checked;
    const env = await api.cfbTree({ includeMetadict });
    cfbState.tree = env.tree;
    cfbState.expanded = new Set(["/"]); // root is open by default
    renderCfbTree();
  } catch (e) {
    $("#cfb-tree").replaceChildren(
      el("p", { class: "error", style: "padding:14px;" },
        "Error: " + (e.message || String(e)))
    );
  }
}

function renderCfbTree() {
  const root = $("#cfb-tree");
  root.replaceChildren();
  if (!cfbState.tree) return;
  root.appendChild(renderCfbNode(cfbState.tree, 0));
}

function renderCfbNode(node, depth) {
  // Two special placeholder shapes from core.cfb_tree:
  //   {_type: "cfb_metadict_filtered", ...}
  //   {_type: "cfb_run_collapsed", ...}
  const host = el("div", { class: "cfb-node" });

  if (node._type === "cfb_metadict_filtered") {
    host.appendChild(
      el("div", { class: "cfb-row" }, [
        el("span", { class: "twisty" }, ""),
        el("span", { class: "icon" }, "🗀"),
        el("span", { class: "cfb-name" }, "MetaDictionary-1/"),
        el("span", { class: "meta" },
          ` filtered: ${node.storage_count} storages, ${node.stream_count} streams`),
      ])
    );
    return host;
  }
  if (node._type === "cfb_run_collapsed") {
    host.appendChild(
      el("div", { class: "cfb-row" }, [
        el("span", { class: "twisty" }, ""),
        el("span", { class: "icon" }, "≡"),
        el("span", { class: "cfb-name" },
          `${node.first_name} … ${node.last_name}`),
        el("span", { class: "meta" },
          ` ×${node.count}, ${node.byte_size} bytes each`),
      ])
    );
    return host;
  }

  // Normal storage node.
  const isOpen = cfbState.expanded.has(node.path);
  const row = el("div", {
    class: "cfb-row" + (cfbState.selectedPath === node.path ? " selected" : ""),
    onclick: () => {
      const wasOpen = cfbState.expanded.has(node.path);
      if (wasOpen) cfbState.expanded.delete(node.path);
      else cfbState.expanded.add(node.path);
      cfbState.selectedPath = node.path;
      renderCfbTree();
      showCfbStorageDetail(node);
    },
  }, [
    el("span", { class: "twisty " + (isOpen ? "expanded" : "collapsed") },
      isOpen ? "▼" : "▶"),
    el("span", { class: "icon" }, "🗀"),
    el("span", { class: "cfb-name" },
      depth === 0 ? "/" : (node.name + "/")),
    node.class_name
      ? el("span", { class: "class-name meta" },
          " [" + node.class_name + "]")
      : (node.class_id
          ? el("span", { class: "meta" }, " [unknown class]")
          : null),
  ].filter(Boolean));
  host.appendChild(row);

  if (isOpen) {
    const children = el("div", { class: "cfb-children" });
    for (const sub of node.storages || []) {
      children.appendChild(renderCfbNode(sub, depth + 1));
    }
    for (const st of node.streams || []) {
      children.appendChild(renderCfbStreamRow(st));
    }
    host.appendChild(children);
  }
  return host;
}

function renderCfbStreamRow(st) {
  if (st._type === "cfb_run_collapsed") {
    return renderCfbNode(st, 0);
  }
  const row = el("div", {
    class: "cfb-row" + (cfbState.selectedPath === st.path ? " selected" : ""),
    onclick: () => {
      cfbState.selectedPath = st.path;
      renderCfbTree();
      showCfbStreamHexView(st);
    },
  }, [
    el("span", { class: "twisty" }, ""),
    el("span", { class: "icon" }, "📄"),
    el("span", { class: "cfb-name" }, st.name),
    el("span", { class: "meta" }, ` (${st.byte_size} bytes)`),
    st.class_name
      ? el("span", { class: "class-name meta" }, " [" + st.class_name + "]")
      : null,
  ].filter(Boolean));
  return row;
}

function showCfbStorageDetail(node) {
  // Inspector pane shows DirEntry-level info for a storage.
  const inspector = $("#inspector");
  inspector.replaceChildren();
  $("#breadcrumb").replaceChildren(
    el("span", { class: "muted" }, "cfb storage "),
    el("span", { class: "crumb head" }, node.path),
  );
  inspector.appendChild(
    el("div", { class: "obj-header" }, [
      el("span", { class: "class-tag" }, node.class_name || "Storage"),
      el("span", { class: "obj-name" }, node.path),
      node.class_id
        ? el("span", { class: "obj-mob-id", title: node.class_id },
            node.class_id)
        : null,
    ].filter(Boolean))
  );
  const tbl = el("div", { class: "props-table" });
  const rows = [
    ["path", node.path],
    ["name", node.name],
    ["class_id", node.class_id || ""],
    ["class_name", node.class_name || ""],
    ["storages", String((node.storages || []).length)],
    ["streams", String((node.streams || []).length)],
  ];
  for (const [k, v] of rows) {
    const nameEl = el("div", { class: "prop-name", title: k }, k);
    const typeEl = el("span", { class: "prop-type" }, "cfb");
    const vc = el("div", { class: "prop-value scalar" });
    vc.textContent = v;
    tbl.appendChild(el("div", { class: "prop-row" }, [nameEl, typeEl, vc]));
  }
  inspector.appendChild(tbl);
}

async function showCfbStreamHexView(st) {
  const inspector = $("#inspector");
  $("#breadcrumb").replaceChildren(
    el("span", { class: "muted" }, "cfb stream "),
    el("span", { class: "crumb head" }, st.path),
  );
  inspector.replaceChildren();
  inspector.appendChild(
    el("div", { class: "obj-header" }, [
      el("span", { class: "class-tag" }, "Stream"),
      el("span", { class: "obj-name" }, st.path),
      el("span", { class: "obj-mob-id", title: String(st.byte_size)},
        `${st.byte_size} bytes`),
    ])
  );
  const offsetIn = el("input", {
    type: "number", min: "0", value: "0",
    style: "width:120px;",
  });
  const lengthIn = el("input", {
    type: "number", min: "0", value: "4096",
    style: "width:120px;",
  });
  const refresh = el("button", { type: "button" }, "Refresh");
  const controls = el("div", { class: "bytes-controls" }, [
    el("label", { class: "muted" }, "offset "), offsetIn,
    el("label", { class: "muted" }, "length "), lengthIn,
    refresh,
  ]);
  inspector.appendChild(controls);

  const hexHost = el("pre", { class: "hex-view" });
  inspector.appendChild(hexHost);

  const load = async () => {
    hexHost.textContent = "Loading…";
    try {
      const env = await api.cfbStream({
        path: st.path,
        offset: Number(offsetIn.value) || 0,
        length: Number(lengthIn.value) || 0,
      });
      const lines = [];
      const offset0 = env.offset;
      env.hex.forEach((row, i) => {
        const off = offset0 + i * 16;
        const hex = row.padEnd(16 * 3 - 1, " ");
        const ascii = env.ascii[i] || "";
        lines.push(`${off.toString(16).padStart(8, "0")}  ${hex}  |${ascii}|`);
      });
      const trunc = env.truncated
        ? `\n…  truncated; total ${env.byte_size} bytes`
        : "";
      hexHost.textContent = lines.join("\n") + trunc;
    } catch (e) {
      hexHost.textContent = "Error: " + (e.message || String(e));
    }
  };
  refresh.addEventListener("click", load);
  load();
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
    walkButtonForObject(obj),
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
    table.appendChild(renderPropertyRow(pname, props[pname]));
  }
  inspector.appendChild(table);
}

function walkButtonForObject(obj) {
  // Show Walk affordance on Mobs (Composition/Master/Source). Mobs have
  // a meaningful chain entry; non-Mob objects don't.
  if (!obj || obj._type !== "aaf_object") return null;
  if (!obj.class || !obj.class.endsWith("Mob")) return null;
  if (!obj.mob_id) return null;

  const slotIds = collectSlotIds(obj);
  const container = el("span", { class: "walk-controls" });
  let slotSelect = null;
  if (slotIds.length > 1) {
    slotSelect = el("select", {
      class: "slot-select",
      title: "Starting slot for the chain walk",
    });
    for (const sid of slotIds) {
      slotSelect.appendChild(
        el("option", { value: String(sid) }, `slot ${sid}`)
      );
    }
  }
  if (slotSelect) container.appendChild(slotSelect);
  container.appendChild(
    el(
      "button",
      {
        type: "button",
        class: "walk-btn",
        onclick: () =>
          runChainWalk(
            obj.mob_id,
            slotSelect ? Number(slotSelect.value) : null
          ),
      },
      "Walk chain"
    )
  );
  return container;
}

function collectSlotIds(obj) {
  const ids = [];
  const slots = obj && obj.properties && obj.properties.Slots;
  if (!Array.isArray(slots)) return ids;
  for (const slot of slots) {
    if (!slot || !slot.properties) continue;
    const sid = slot.properties.SlotID;
    if (typeof sid === "number") ids.push(sid);
  }
  return ids;
}

async function runChainWalk(mobId, slotId) {
  const inspector = $("#inspector");
  // Append (or replace) a #chain-panel below the property table.
  let panel = $("#chain-panel");
  if (!panel) {
    panel = el("section", { id: "chain-panel", class: "chain-panel" });
    inspector.appendChild(panel);
  }
  panel.replaceChildren(
    el("h3", { class: "chain-header" }, "Chain"),
    el("p", { class: "muted" }, "Walking…")
  );
  try {
    const env = await api.walk({ mob_id: mobId, slot_id: slotId });
    renderChain(panel, env);
  } catch (e) {
    panel.replaceChildren(
      el("h3", { class: "chain-header" }, "Chain"),
      el("p", { class: "error" }, "Error: " + (e.message || String(e)))
    );
  }
}

function renderChain(panel, env) {
  panel.replaceChildren(
    el("h3", { class: "chain-header" }, "Chain")
  );
  for (let i = 0; i < env.hops.length; i++) {
    const h = env.hops[i];
    const row = el(
      "div",
      {
        class: "chain-hop" + (h.terminal ? " terminal" : ""),
        title: h.mob_id,
        onclick: () => navigateToMobId(h.mob_id),
      },
      [
        el("span", { class: "hop-marker" }, h.terminal ? "●" : "○"),
        el("span", { class: "hop-class" }, h.mob_class),
        h.mob_name
          ? el("span", { class: "hop-name" }, `"${h.mob_name}"`)
          : el("span", { class: "muted hop-name" }, "untitled"),
        el("span", { class: "hop-meta" }, `slot ${h.slot_id}`),
        el("span", { class: "hop-meta" }, `seg ${h.segment_class}`),
        h.physical_track_number != null
          ? el("span", { class: "hop-meta" }, `ptn ${h.physical_track_number}`)
          : null,
        h.edit_rate
          ? el("span", { class: "hop-meta" }, h.edit_rate)
          : null,
        h.terminal && h.terminal_reason
          ? el(
              "span",
              { class: "hop-terminal" },
              "★ " + h.terminal_reason
            )
          : null,
      ].filter(Boolean)
    );
    panel.appendChild(row);
  }
}

function renderPropertyRow(pname, value) {
  // One row = one independent flex container. Name/type cells are
  // fixed-width so rows align vertically; value cell is auto-width and
  // grows rightward without affecting siblings.
  const typeName = describeType(value);
  const nameEl = el("div", { class: "prop-name", title: pname }, pname);
  const typeEl = el("span", { class: "prop-type", title: typeName }, typeName);
  const valueEl = el("div", { class: "prop-value" });
  populateValue(valueEl, value, pname);
  return el("div", { class: "prop-row" }, [nameEl, typeEl, valueEl]);
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
      const tbl = el("div", { class: "props-table" });
      const props = obj.properties || {};
      for (const pname2 of Object.keys(props)) {
        tbl.appendChild(renderPropertyRow(pname2, props[pname2]));
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
        const nameEl = el("div", { class: "prop-name", title: k }, k);
        const typeEl = el("span", { class: "prop-type" }, "weakref");
        const vc = el("div", { class: "prop-value scalar" });
        vc.textContent = val;
        tbl.appendChild(el("div", { class: "prop-row" }, [nameEl, typeEl, vc]));
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

// ---------- find panel ----------

const findState = {
  matches: [],
  pattern: "",
  classes: new Set(), // Mob classes to filter to; empty = all
};

function wireFindPanel() {
  const panel = $("#find-panel");
  $("#btn-find-toggle").addEventListener("click", () => {
    panel.classList.toggle("collapsed");
  });
  $("#btn-find").addEventListener("click", runFind);
  $("#find-pattern").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      runFind();
    }
  });
  // Close the class filter popover on outside click.
  document.addEventListener("click", (ev) => {
    const det = $("#find-class-filter");
    if (det && det.open && !det.contains(ev.target)) {
      det.open = false;
    }
  });
}

function renderClassFilterOptions() {
  const host = $("#find-class-options");
  host.replaceChildren();
  if (!state.file || !state.file.classes_summary) {
    host.appendChild(el("p", { class: "muted" }, "Open a file to see classes."));
    updateClassFilterSummary();
    return;
  }
  const entries = Object.entries(state.file.classes_summary).sort((a, b) => {
    const order = (c) =>
      c === "CompositionMob" ? 0
      : c === "MasterMob" ? 1
      : c === "SourceMob" ? 2
      : 3;
    return order(a[0]) - order(b[0]) || a[0].localeCompare(b[0]);
  });
  for (const [cls, count] of entries) {
    const cb = el("input", { type: "checkbox", value: cls });
    cb.checked = findState.classes.has(cls);
    cb.addEventListener("change", () => {
      if (cb.checked) findState.classes.add(cls);
      else findState.classes.delete(cls);
      updateClassFilterSummary();
    });
    host.appendChild(
      el("label", {}, [
        cb,
        el("span", {}, cls),
        el("span", { class: "count" }, String(count)),
      ])
    );
  }
  updateClassFilterSummary();
}

function updateClassFilterSummary() {
  const summary = $("#find-class-summary");
  if (!summary) return;
  const count = findState.classes.size;
  if (count === 0) summary.textContent = "all classes";
  else if (count === 1) summary.textContent = [...findState.classes][0];
  else summary.textContent = `${count} classes`;
}

async function runFind() {
  if (!state.file) return;
  const pattern = $("#find-pattern").value.trim();
  if (!pattern) return;
  const scope = $("#find-scope").value;
  const layer = $("#find-layer").value;
  const classes = [...findState.classes];
  const panel = $("#find-panel");
  panel.classList.remove("collapsed");
  const summary = $("#find-summary");
  const classNote = classes.length ? ` (in ${classes.join(", ")})` : "";
  summary.textContent = `Searching "${pattern}"${classNote} …`;
  summary.classList.add("muted");
  const host = $("#find-results");
  host.replaceChildren();
  try {
    const env = await api.find({ pattern, scope, layer, classes });
    findState.matches = env.matches;
    findState.pattern = pattern;
    summary.textContent =
      env.total === 0
        ? `No matches for "${pattern}"${classNote}.`
        : `${env.total} match${env.total === 1 ? "" : "es"} for "${pattern}"${classNote}`;
    renderFindResults(env.matches);
  } catch (e) {
    summary.textContent = "Error: " + (e.message || String(e));
  }
}

function renderFindResults(matches) {
  const host = $("#find-results");
  host.replaceChildren();
  if (matches.length === 0) return;
  for (const m of matches) {
    host.appendChild(
      el(
        "div",
        {
          class: "find-row",
          title: m.path,
          onclick: () => jumpToMatch(m),
        },
        [
          el("span", { class: "layer-tag" }, `${m.layer}/${m.where}`),
          el("span", { class: "find-path" },
            `${m.path}  (${m.classname}.${m.field})`),
          el("span", { class: "find-value" }, m.value || ""),
        ]
      )
    );
  }
}

async function jumpToMatch(m) {
  if (m.layer === "aaf") {
    // core.find_in_aaf paths use integer indices for set/vector children,
    // e.g. `Mobs/2155/Slots/0/PhysicalTrackNumber`. Translate the Mobs
    // index into the URN via the eager Mob list (same iteration order
    // server-side) so we can navigate to the right row.
    const parts = m.path.split("/").filter(Boolean);
    if (parts.length >= 2 && parts[0] === "Mobs") {
      let urn = parts[1];
      if (/^\d+$/.test(urn)) {
        const idx = parseInt(urn, 10);
        if (idx >= 0 && idx < state.mobs.length) {
          urn = state.mobs[idx].mob_id;
        }
      }
      const remaining = parts.slice(2);
      activateTab("aaf");
      await navigateToMobId(urn);
      if (remaining.length) {
        const trailLabel =
          inspectorState.trail[0]?.label || `Mob:${tailMobId(urn)}`;
        // Re-set breadcrumb to show the deeper path as muted context.
        // Future improvement: walk and fetch each level so each segment
        // is clickable.
        $("#breadcrumb").replaceChildren(
          el("span", { class: "crumb head" }, trailLabel),
          el("span", { class: "sep" }, "/"),
          el("span", { class: "muted" }, remaining.join("/")),
        );
      }
      return;
    }
    // Non-mob AAF path (rare). Try to resolve directly via /api/object.
    activateTab("aaf");
    inspectorState.trail = [{
      label: m.path,
      fetch: () => api.object({ path: m.path }),
    }];
    state.selected = null;
    $$(".mob-row").forEach((r) => r.classList.remove("selected"));
    await renderInspectorAt(0);
    return;
  }
  // CFB layer: open the tree if it isn't loaded, expand ancestors, scroll
  // to the entry, and render its detail.
  activateTab("cfb");
  if (!cfbState.tree) await loadCfbTreeIfNeeded(true);
  // Ensure all ancestor storages are expanded.
  const segs = m.path.split("/").filter(Boolean);
  let cur = "/";
  cfbState.expanded.add(cur);
  for (const s of segs.slice(0, -1)) {
    cur = (cur === "/" ? "/" : cur + "/") + s;
    cfbState.expanded.add(cur);
  }
  cfbState.selectedPath = m.path;
  renderCfbTree();
  // Show the detail. Streams are leaves, storages may be containers.
  const found = findCfbEntry(cfbState.tree, m.path);
  if (found && found.kind === "stream") showCfbStreamHexView(found.entry);
  else if (found) showCfbStorageDetail(found.entry);
  // Scroll the matching row into view if it's been rendered.
  const row = document.querySelector(
    `.cfb-row.selected`
  );
  if (row) row.scrollIntoView({ block: "nearest" });
}

function findCfbEntry(node, path) {
  if (!node) return null;
  if (node.path === path) return { kind: "storage", entry: node };
  for (const sub of node.storages || []) {
    const r = findCfbEntry(sub, path);
    if (r) return r;
  }
  for (const st of node.streams || []) {
    if (st.path === path) return { kind: "stream", entry: st };
  }
  return null;
}

function activateTab(which) {
  $$(".tabs .tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.tab === which)
  );
  $$(".tab-pane").forEach((p) =>
    p.classList.toggle("active", p.dataset.pane === which)
  );
  if (which === "cfb") loadCfbTreeIfNeeded();
}

// ---------- bootstrap ----------

async function init() {
  wireTopbar();
  wireTabs();
  wireFilter();
  wireCfbControls();
  wireFindPanel();
  setFileStatus();
  renderMobList();

  // If a file was opened via `aafbrowser web <path>`, the server already
  // has it loaded — fetch metadata and mob list at startup.
  try {
    const meta = await api.file();
    if (meta) {
      state.file = meta;
      setFileStatus();
      renderClassFilterOptions();
      const mobs = await api.mobs();
      state.mobs = mobs.mobs;
      renderMobList();
    }
  } catch (_) { /* no-op; user can open manually */ }
}

document.addEventListener("DOMContentLoaded", init);
