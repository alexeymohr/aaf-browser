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
  view: "tracks",    // "tracks" | "mobs" | "cfb" — top-level view tab
};

// Session-summary state: drives the top headline bar and
// is the source of truth for per-clip timecode math.
const sessionState = {
  summary: null,    // SessionSummary dict from /api/session
  timecode: null,   // {edit_rate, edit_rate_value, fps_nominal, drop, start_frames, ...}
};

// Sources-view state — cross-track pull list.
const sourcesState = {
  sources: [],          // SourceInventoryEntry dicts from /api/sources
  loaded: false,        // true once /api/sources has been fetched at least once
  filter: "",           // case-insensitive substring filter on name
  selectedMobId: null,  // currently selected source mob_id
};

// Tracks-view state. Independent of the geek-view state above.
//
// The center pane is a recursive tree of nodes. Each node has:
//   id        — stable unique key, used for expansion + selection state
//   kind      — "clip" (top-level, from /api/track/clips) or "object"
//               (an aaf_object dict from /api/object)
//   data      — the underlying clip dict or aaf_object dict
//   getRow    — () => DOM (the row content for that node)
//   getKids   — async () => Node[]   (children in the tree)
//
// Selection and expansion are independent: caret toggles expansion;
// label click selects + populates the inspector with that node's info.
const tracksState = {
  tracks: [],                 // Track[] from /api/tracks
  topmost: null,              // {mob_id, name} or null
  selectedSlotId: null,       // currently selected track's slot_id
  clips: [],                  // Clip[] from /api/track/clips
  expanded: new Set(),        // node.id for every expanded node
  childrenCache: new Map(),   // node.id -> Node[] | "loading" | {error}
  selectedNodeId: null,       // highlight + inspector source-of-truth
  selectedNode: null,         // the actual node (for inspector render)
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
  async tracks() {
    const r = await fetch("/api/tracks");
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async trackClips(slotId) {
    const r = await fetch("/api/track/clips?slot=" + encodeURIComponent(slotId));
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async session() {
    const r = await fetch("/api/session");
    if (!r.ok) throw await apiError(r);
    return r.json();
  },
  async sources() {
    const r = await fetch("/api/sources");
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
//
// /api/open is the slow step on large sessions (server iterates all
// mobs to build the eager index). beginAafLoading/endAafLoading
// surface a spinner across the topbar + every visible pane so the
// user knows the app isn't frozen during the wait.
async function loadAafFile(path) {
  const basename = path.split("/").pop();
  beginAafLoading(basename);
  try {
    const meta = await api.open(path);
    state.file = meta;
    state.selected = null;
    cfbState.tree = null;
    cfbState.selectedPath = null;
    cfbState.expanded = new Set();
    inspectorState.trail = [];
    inspectorState.current = null;
    findState.classes = new Set();
    resetTracksState();
    renderClassFilterOptions();
    const mobs = await api.mobs();
    state.mobs = mobs.mobs;
    renderMobList();
    // Always fetch session summary + tracks — Tracks is the default
    // landing and the session bar lives above all views.
    await Promise.all([loadSession(), loadTracks()]);
  } finally {
    endAafLoading();
  }
}

// Show the file-load spinner across the topbar and each pane that
// would otherwise display a stale or "no file open" message during
// the load. setFileStatus / renderTrackList / renderMobList /
// loadCfbTreeIfNeeded restore real content after endAafLoading runs.
function beginAafLoading(basename) {
  document.title = `AAF Browser — Loading ${basename}…`;
  // Hide the previous file's headline bar so the user doesn't see
  // stale info during the swap.
  $("#session-bar").hidden = true;
  const status = $("#file-status");
  status.classList.remove("muted");
  status.replaceChildren(
    el("span", { class: "spinner" }),
    document.createTextNode(` Loading ${basename}…`),
  );
  const openBtn = $("#btn-open");
  openBtn.disabled = true;
  openBtn.classList.add("loading");
  // Per-pane hints
  $("#tracks-summary").replaceChildren(
    el("span", { class: "spinner" }),
    document.createTextNode(` Loading ${basename}…`),
  );
  $("#track-list").replaceChildren();
  $("#clips-summary").textContent = "";
  $("#clips-tree").replaceChildren();
  const loadingRow = (label) =>
    el("div", { class: "loading-row" }, [
      el("span", { class: "spinner" }),
      document.createTextNode(label),
    ]);
  $("#mob-list").replaceChildren(loadingRow(`Loading ${basename}…`));
  $("#cfb-tree").replaceChildren(loadingRow(`Loading ${basename}…`));
  $("#inspector").replaceChildren(
    el("p", { class: "loading-row" }, [
      el("span", { class: "spinner large" }),
      document.createTextNode(` Loading ${basename}…`),
    ])
  );
  $("#breadcrumb").replaceChildren();
}

function endAafLoading() {
  $("#btn-open").disabled = false;
  $("#btn-open").classList.remove("loading");
  setFileStatus();
  syncWindowTitle();
  // beginAafLoading replaced #inspector with a spinner. After the load
  // there's no selection yet, so clear the spinner and show a neutral
  // hint. If a selection happens later, renderTracksInspector /
  // selectMob etc. overwrite this.
  if (tracksState.selectedNode == null && state.selected == null) {
    $("#inspector").replaceChildren(
      el("p", { class: "muted" },
        "Select a track and a clip to inspect it.")
    );
    $("#breadcrumb").replaceChildren();
  }
}

function resetTracksState() {
  tracksState.tracks = [];
  tracksState.topmost = null;
  tracksState.selectedSlotId = null;
  tracksState.clips = [];
  tracksState.expanded = new Set();
  tracksState.childrenCache = new Map();
  tracksState.selectedNodeId = null;
  tracksState.selectedNode = null;
  tracksState.editRateValue = null;
  sessionState.summary = null;
  sessionState.timecode = null;
  sourcesState.sources = [];
  sourcesState.loaded = false;
  sourcesState.filter = "";
  sourcesState.selectedMobId = null;
}

// Mirror the open file's basename into document.title. pywebview's
// WKWebView reflects document.title to the macOS window title; in a
// regular browser the same title shows up in the tab. Either way, a
// glance at the title bar tells the user which AAF they're inspecting.
function syncWindowTitle() {
  const base = state.file && state.file.path
    ? state.file.path.split("/").pop()
    : null;
  document.title = base ? `AAF Browser — ${base}` : "AAF Browser";
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
    resetTracksState();
    setFileStatus();
    syncWindowTitle();
    renderMobList();
    renderTrackList();
    renderClipsPane();
    renderSessionBar();
    renderClassFilterOptions();
    $("#cfb-tree").replaceChildren();
    $("#inspector").replaceChildren(
      el("p", { class: "muted" }, "Open a file, then select something to inspect it.")
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

// ---------- top-level view tabs (Tracks / All Mobs / CFB) ----------

function wireViewTabs() {
  $$("#view-tabs .view-tab").forEach((tab) => {
    tab.addEventListener("click", () => activateView(tab.dataset.view));
  });
}

function activateView(view) {
  state.view = view;
  document.body.classList.remove(
    "view-tracks", "view-sources", "view-mobs", "view-cfb"
  );
  document.body.classList.add("view-" + view);
  $$("#view-tabs .view-tab").forEach((t) => {
    const active = t.dataset.view === view;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  // Lazy initialization per view
  if (view === "cfb") loadCfbTreeIfNeeded();
  if (view === "sources") loadSourcesIfNeeded();
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
      env.rows.forEach((row) => {
        const hex = row.hex_bytes.padEnd(16 * 3 - 1, " ");
        lines.push(`${row.offset.toString(16).padStart(8, "0")}  ${hex}  |${row.ascii_bytes}|`);
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
  // Translate the legacy AAF/CFB tab names used by jumpToMatch into the
  // new top-level view names.
  const view = which === "aaf" ? "mobs" : which === "cfb" ? "cfb" : which;
  activateView(view);
}

// ---------- Tracks view (operator-first surface) ----------

async function loadTracks() {
  if (!state.file) {
    renderTrackList();
    renderClipsPane();
    return;
  }
  try {
    const env = await api.tracks();
    tracksState.tracks = env.tracks || [];
    tracksState.topmost = env.topmost_composition || null;
    sessionState.timecode = env.timecode || null;
    // Audio + video tracks share an edit_rate in well-formed Avid AAFs;
    // grab the first one for the per-clip seconds math. Fall back to
    // the timecode rate if no tracks are present.
    tracksState.editRateValue =
      (tracksState.tracks[0] && parseRational(tracksState.tracks[0].edit_rate))
      || (sessionState.timecode && sessionState.timecode.edit_rate_value)
      || null;
    renderTrackList();
    renderClipsPane();
  } catch (e) {
    const summary = $("#tracks-summary");
    if (summary) summary.textContent = "Error: " + (e.message || String(e));
    $("#track-list").replaceChildren();
  }
}

async function loadSession() {
  if (!state.file) {
    renderSessionBar();
    return;
  }
  try {
    const env = await api.session();
    sessionState.summary = env.session;
    renderSessionBar();
  } catch (e) {
    sessionState.summary = null;
    renderSessionBar();
  }
}

function renderSessionBar() {
  const bar = $("#session-bar");
  bar.replaceChildren();
  const s = sessionState.summary;
  if (!state.file || !s) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;

  const cell = (label, valueNodes, sub) => {
    const valueRow = el("span", { class: "value" }, valueNodes);
    const children = [el("span", { class: "label" }, label), valueRow];
    if (sub) children.push(el("span", { class: "sub" }, sub));
    return el("div", { class: "cell" }, children);
  };

  // FILE: basename + size (full path on hover)
  const fullPath = state.file && state.file.path ? state.file.path : "";
  const basename = fullPath ? fullPath.split("/").pop() : "(unknown)";
  bar.appendChild((() => {
    const c = cell("File", [basename], formatBytes(s.file_size_bytes));
    c.title = fullPath;
    return c;
  })());

  // COMPOSITION
  bar.appendChild(cell(
    "Composition",
    [s.topmost_composition_name || "(none)"],
    `${s.composition_mob_count} comp · ${s.master_mob_count} master · ${s.source_mob_count} source mobs`
  ));

  // TRACKS
  const trackVal = el("span", {}, [
    el("span", { class: "value accent" }, String(s.audio_track_count)),
    document.createTextNode(" audio"),
    s.video_track_count
      ? document.createTextNode(`  ·  ${s.video_track_count} video`)
      : null,
  ].filter(Boolean));
  bar.appendChild(cell("Tracks", [trackVal],
    s.timecode_track_count
      ? `${s.timecode_track_count} timecode slot${s.timecode_track_count === 1 ? "" : "s"}`
      : null));

  // CLIPS
  bar.appendChild(cell("Clips", [String(s.total_clip_count)],
    "across all audio + video tracks"));

  // TIMECODE
  if (s.timecode) {
    const tc = s.timecode;
    const fpsLabel = tc.edit_rate_value
      ? (Math.round(tc.edit_rate_value * 100) / 100).toString()
      : (tc.fps_nominal ? String(tc.fps_nominal) : "?");
    const drop = tc.drop ? "DF" : "NDF";
    bar.appendChild(cell("TC rate", [`${fpsLabel} ${drop}`],
      tc.edit_rate ? tc.edit_rate : null));
    bar.appendChild(cell("Start TC",
      [el("span", { class: "value accent" }, tc.start_timecode || "—")],
      tc.start_frames != null ? `frame ${tc.start_frames}` : null));
  }

  // DURATION
  if (s.duration_timecode || s.duration_seconds != null) {
    bar.appendChild(cell("Duration",
      [el("span", { class: "value warn" }, s.duration_timecode || "—")],
      s.duration_seconds != null ? formatSecondsJs(s.duration_seconds) : null));
  }

  // AUDIO — sample rate / bit depth / channel-layout breakdown across
  // all SourceMobs with usable descriptors. Render single-value cells
  // when uniform, otherwise compact "mixed: A(n), B(m)" form.
  if (s.audio && s.audio.audio_source_count > 0) {
    bar.appendChild(cell(
      "Sample rate",
      [formatAudioField(s.audio.sample_rates, formatSampleRate)],
      `${s.audio.audio_source_count} audio source${s.audio.audio_source_count === 1 ? "" : "s"}`
    ));
    bar.appendChild(cell(
      "Bit depth",
      [formatAudioField(s.audio.bit_depths, (b) => `${b}-bit`)],
      null
    ));
    bar.appendChild(cell(
      "Channels",
      [formatAudioField(s.audio.channel_counts, formatChannelCount)],
      null
    ));
  }

  // AUTHORED — ProductName from the most recent Header.IdentificationList
  // entry. Tells the operator at a glance which NLE wrote the file
  // (Avid Media Composer, Premiere Pro, Pro Tools, etc.).
  if (s.authoring && s.authoring.product_name) {
    const a = s.authoring;
    const subBits = [];
    if (a.platform) subBits.push(a.platform);
    if (a.identification_count > 1) subBits.push(`${a.identification_count} revisions`);
    bar.appendChild(cell("Authored by",
      [a.product_name],
      subBits.join(" · ") || (a.company_name || null)));
  }

  // MODIFIED — Header.LastModified (or fallback to authoring date).
  const modified = s.last_modified || (s.authoring && s.authoring.date) || null;
  if (modified) {
    bar.appendChild(cell("Modified", [formatDateShort(modified)], modified));
  }
}

function formatAudioField(counts, labelFn) {
  // Render an aggregated audio-format field: single value if uniform,
  // otherwise "mixed: A(n) · B(m)" with the most common first. Returns
  // a DOM node so we can highlight uniform values vs mixed ones.
  const entries = Object.entries(counts || {})
    .map(([k, v]) => [k, Number(v)])
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return el("span", { class: "muted" }, "—");
  if (entries.length === 1) {
    return el("span", { class: "value accent" }, labelFn(entries[0][0]));
  }
  // Mixed — compact list, colored as a warning so it stands out.
  const parts = entries.map(([k, n]) => `${labelFn(k)}(${n})`).join(" · ");
  return el("span", { class: "value warn", title: "mixed across sources" }, parts);
}

function formatSampleRate(rate) {
  // rate is the dict key, e.g. "48000/1" or "96000/1". Display as kHz.
  if (typeof rate !== "string") return String(rate);
  const m = rate.match(/^(\d+)(?:\/(\d+))?$/);
  if (!m) return rate;
  const num = Number(m[1]);
  const den = m[2] ? Number(m[2]) : 1;
  if (!den) return rate;
  const hz = num / den;
  if (hz >= 1000) return `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)} kHz`;
  return `${hz} Hz`;
}

function formatChannelCount(c) {
  const n = Number(c);
  if (n === 1) return "mono";
  if (n === 2) return "stereo";
  if (n === 6) return "5.1";
  if (n === 8) return "7.1";
  return `${n}ch`;
}

function formatDateShort(iso) {
  // Render an ISO timestamp as YYYY-MM-DD HH:MM. No timezone math —
  // AAF files often carry the wall-clock time at write without TZ
  // info; rendering the raw string is more honest than a guess.
  if (!iso || typeof iso !== "string") return "—";
  // Match "YYYY-MM-DDTHH:MM:SS" or "YYYY-MM-DD HH:MM:SS" prefix.
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  if (m) return `${m[1]} ${m[2]}`;
  return iso.slice(0, 16);
}

function renderTrackList() {
  const root = $("#track-list");
  const summary = $("#tracks-summary");
  root.replaceChildren();

  if (!state.file) {
    summary.textContent = "No file open.";
    return;
  }
  const tracks = tracksState.tracks;
  if (tracks.length === 0) {
    summary.textContent = tracksState.topmost
      ? "Topmost composition has no audio or video tracks."
      : "No CompositionMob in this file. Use the All Mobs tab.";
    return;
  }
  const compName = tracksState.topmost && tracksState.topmost.name
    ? tracksState.topmost.name
    : "(unnamed composition)";
  summary.textContent =
    `${tracks.length} track${tracks.length === 1 ? "" : "s"} — ${compName}`;

  // Compute Pro Tools-style positional labels (A1, A2, ... / V1, V2, ...)
  // by walking the track list in display order. Per-kind index, not
  // tied to slot_id or PhysicalTrackNumber. Stored on the track for
  // reuse by trackContextLabel() below.
  let audioIdx = 0;
  let videoIdx = 0;
  for (const t of tracks) {
    if (t.kind === "audio") {
      audioIdx++;
      t._positional = `A${audioIdx}`;
    } else if (t.kind === "video") {
      videoIdx++;
      t._positional = `V${videoIdx}`;
    } else {
      t._positional = `slot ${t.slot_id}`;
    }
  }

  for (const t of tracks) {
    const nameNode = el(
      "span",
      { class: "track-name" + (t.name ? "" : " untitled") },
      t.name || t._positional
    );
    if (t.pan_channel) {
      // Premiere stereo-split tag: "L" / "R" pill next to the name.
      nameNode.appendChild(
        el("span", { class: "track-pan",
                     title: `Premiere stereo split (Pan = ${t.pan_channel})` },
          t.pan_channel)
      );
    }
    const row = el(
      "div",
      {
        class: "track-row kind-" + t.kind
          + (tracksState.selectedSlotId === t.slot_id ? " selected" : ""),
        dataset: { slotId: String(t.slot_id) },
        onclick: () => selectTrack(t.slot_id),
      },
      [
        el("span", { class: "track-ordinal" }, String(t.ordinal)),
        el("span", { class: "track-kind-tag" }, t.kind),
        nameNode,
        el(
          "span",
          { class: "track-meta muted" },
          [
            `${t.clip_count} clip${t.clip_count === 1 ? "" : "s"}`,
            t.length != null ? ` · len ${t.length}` : "",
          ].join("")
        ),
      ]
    );
    root.appendChild(row);
  }
}

function trackContextLabel(slotId) {
  // Used in the center-pane summary and the inspector breadcrumb.
  // Pattern: "<name> (<positional>, Slot N)" if named, "<positional>
  // (Slot N)" if unnamed. Pro Tools-style positional is computed in
  // renderTrackList and cached on the track object.
  const t = tracksState.tracks.find((x) => x.slot_id === slotId);
  if (!t) return `Slot ${slotId}`;
  const pos = t._positional || `slot ${slotId}`;
  if (t.name) return `${t.name} (${pos}, Slot ${slotId})`;
  return `${pos} (Slot ${slotId})`;
}

async function selectTrack(slotId) {
  tracksState.selectedSlotId = slotId;
  // Clear per-track tree state (selection + expansion + caches)
  tracksState.expanded = new Set();
  tracksState.childrenCache = new Map();
  tracksState.selectedNodeId = null;
  tracksState.selectedNode = null;
  $$(".track-row").forEach((r) =>
    r.classList.toggle("selected", Number(r.dataset.slotId) === slotId)
  );
  $("#clips-summary").replaceChildren(
    el("span", { class: "spinner" }),
    document.createTextNode(` Loading clips for ${trackContextLabel(slotId)}…`),
  );
  $("#clips-tree").replaceChildren();
  resetTracksInspector();
  try {
    const env = await api.trackClips(slotId);
    tracksState.clips = env.clips || [];
    renderClipsPane();
  } catch (e) {
    $("#clips-summary").textContent = "Error: " + (e.message || String(e));
  }
}

function renderClipsPane() {
  const summary = $("#clips-summary");
  const root = $("#clips-tree");
  root.replaceChildren();
  if (!state.file) {
    summary.textContent = "No file open.";
    return;
  }
  if (tracksState.selectedSlotId == null) {
    summary.textContent = "Select a track.";
    return;
  }
  const clips = tracksState.clips;
  const ctx = trackContextLabel(tracksState.selectedSlotId);
  if (clips.length === 0) {
    summary.textContent = `${ctx} — no clips.`;
    return;
  }
  summary.textContent =
    `${ctx} — ${clips.length} clip${clips.length === 1 ? "" : "s"}`;
  for (const clip of clips) {
    root.appendChild(renderTreeNode(buildClipNode(clip)));
  }
}

// ---------- tree node builders ----------

function buildClipNode(clip) {
  const id = `clip:${tracksState.selectedSlotId}:${clip.index}`;
  // Multi-input combiner clips have sub_clips; their tree expansion
  // shows one row per input (each itself a clip node) instead of a
  // direct AAF-object drill-down.
  const hasSubClips = Array.isArray(clip.sub_clips) && clip.sub_clips.length > 0;
  const expandable = hasSubClips ||
    (clip.component_class === "SourceClip" && !!clip.source_mob_id);
  // Visual variant: recovery_status drives an additional class
  // on top of the recorder/other classification.
  const baseClass = clip.is_recorder_source
    ? "kind-clip-recorder"
    : (clip.component_class === "SourceClip" ? "" : "kind-clip-other");
  const recoveryClass = clip.recovery_status === "unrecoverable"
    ? "unrecoverable"
    : (clip.recovery_status === "ambiguous" ? "ambiguous" : "");
  return {
    id,
    kind: "clip",
    data: clip,
    rowClass: [baseClass, recoveryClass].filter(Boolean).join(" "),
    expandable,
    renderRow: () => renderClipRowContent(clip),
    fetchKids: async () => {
      if (hasSubClips) {
        // Multi-input combiner: each sub_clip is its own clip node,
        // renderable with the same clip-row template. Mark them with
        // a sub-clip class for visual distinction.
        return clip.sub_clips.map((sub, i) => {
          const subId = `${id}/sub${i}`;
          const subHasKids = sub.component_class === "SourceClip" && !!sub.source_mob_id;
          const subRecovery = sub.recovery_status === "unrecoverable"
            ? "unrecoverable"
            : (sub.recovery_status === "ambiguous" ? "ambiguous" : "");
          return {
            id: subId,
            kind: "clip",
            data: sub,
            rowClass: [(sub.is_recorder_source
              ? "kind-clip-recorder"
              : (sub.component_class === "SourceClip" ? "" : "kind-clip-other")),
              subRecovery, "sub-clip"].filter(Boolean).join(" "),
            expandable: subHasKids,
            renderRow: () => renderClipRowContent(sub),
            fetchKids: async () => {
              if (!sub.source_mob_id) return [];
              const env = await api.object({ mob_id: sub.source_mob_id });
              return [buildObjectNode({
                idPrefix: subId,
                propPath: "",
                propName: sub.source_mob_name
                  ? `→ ${sub.source_mob_name}`
                  : "→ source",
                obj: env.object,
              })];
            },
          };
        });
      }
      // Normal SourceClip: drill into the source MasterMob.
      const env = await api.object({ mob_id: clip.source_mob_id });
      const child = buildObjectNode({
        idPrefix: id,
        propPath: "",
        propName: clip.source_mob_name
          ? `→ ${clip.source_mob_name}`
          : "→ source",
        obj: env.object,
      });
      return [child];
    },
  };
}

function buildObjectNode({ idPrefix, propPath, propName, obj }) {
  const subPath = (propPath ? propPath + "/" : "") + (propName || "");
  const id = idPrefix + "::" + subPath;
  const navigable = collectNavigableChildren(obj);
  return {
    id,
    kind: "object",
    data: obj,
    propName,
    rowClass: "",
    expandable: navigable.length > 0,
    renderRow: () => renderObjectRowContent(obj, propName),
    fetchKids: async () =>
      navigable.map((child) =>
        buildObjectNode({
          idPrefix,
          propPath: subPath,
          propName: child.label,
          obj: child.obj,
        })
      ),
  };
}

function collectNavigableChildren(obj) {
  // Only StrongRef nested aaf_object values (and arrays of those) become
  // tree children. Scalars and weakrefs render in the inspector for the
  // selected node, not as separate tree rows.
  const out = [];
  const props = obj && obj.properties ? obj.properties : {};
  for (const [pname, value] of Object.entries(props)) {
    if (Array.isArray(value)) {
      value.forEach((item, i) => {
        if (item && item._type === "aaf_object") {
          const nm = item.name ? `"${item.name}"` : tailMobId(item.mob_id || "");
          out.push({
            label: `${pname}[${i}] ${item.class || "?"}` + (nm ? ` ${nm}` : ""),
            obj: item,
          });
        }
      });
    } else if (value && value._type === "aaf_object") {
      const nm = value.name ? `"${value.name}"` : tailMobId(value.mob_id || "");
      out.push({
        label: `${pname} ${value.class || "?"}` + (nm ? ` ${nm}` : ""),
        obj: value,
      });
    }
  }
  return out;
}

// ---------- generic tree renderer ----------

function renderTreeNode(node) {
  const isExpanded = tracksState.expanded.has(node.id);
  const isSelected = tracksState.selectedNodeId === node.id;

  const caret = el(
    "span",
    {
      class: "tree-caret" + (node.expandable ? "" : " empty"),
      onclick: (ev) => {
        ev.stopPropagation();
        if (node.expandable) toggleTreeExpand(node);
      },
    },
    node.expandable ? (isExpanded ? "▼" : "▶") : ""
  );
  const label = el(
    "span",
    {
      class: "tree-label",
      onclick: (ev) => {
        ev.stopPropagation();
        selectTreeNode(node);
      },
    },
    [node.renderRow()]
  );
  const row = el(
    "div",
    { class: "tree-row " + node.rowClass + (isSelected ? " selected" : "") },
    [caret, label]
  );

  const host = el("div", { class: "tree-node", dataset: { nodeId: node.id } }, [row]);

  if (isExpanded) {
    const childHost = el("div", { class: "tree-children" });
    const cached = tracksState.childrenCache.get(node.id);
    if (cached === "loading") {
      childHost.appendChild(el("div", { class: "tree-loading" }, "Loading…"));
    } else if (cached && cached._error) {
      childHost.appendChild(
        el("div", { class: "tree-error" }, "Error: " + cached._error)
      );
    } else if (Array.isArray(cached)) {
      if (cached.length === 0) {
        childHost.appendChild(el("div", { class: "tree-loading" }, "(no children)"));
      } else {
        for (const childNode of cached) {
          childHost.appendChild(renderTreeNode(childNode));
        }
      }
    }
    host.appendChild(childHost);
  }
  return host;
}

function renderClipRowContent(clip) {
  const isSourceClip = clip.component_class === "SourceClip";

  // Three-format start position: TC (most useful for sound operators),
  // mm:ss.fff (intuitive duration sense), and the raw slot-edit-rate
  // unit count (debugging / chain-walk math). Use sessionState.timecode
  // + the track's edit_rate to compute. Each is null-safe.
  const tc = sessionState.timecode;
  const rateValue = tracksState.editRateValue;  // set when tracks load
  let posTc = null;
  if (tc && tc.fps_nominal != null && clip.timeline_start != null) {
    posTc = formatTimecodeJs(
      (tc.start_frames || 0) + clip.timeline_start,
      tc.fps_nominal,
      !!tc.drop
    );
  }
  let posSecs = null;
  if (rateValue && clip.timeline_start != null) {
    posSecs = formatSecondsJs(clip.timeline_start / rateValue);
  }
  const posRaw = clip.timeline_start != null ? String(clip.timeline_start) : "";

  // Length in slot-edit-rate units, and a TC-style duration when we
  // have a frame rate.
  let lenStr = "";
  if (clip.length != null) {
    if (tc && tc.fps_nominal != null) {
      lenStr = formatTimecodeJs(clip.length, tc.fps_nominal, !!tc.drop);
    } else {
      lenStr = String(clip.length);
    }
  }

  const pos = el("span", { class: "clip-pos" }, [
    el("span", { class: "pos-tc", title: "absolute timecode" }, posTc || "—"),
    el("span", { class: "pos-secs", title: "elapsed seconds on the timeline" }, posSecs || "—"),
    el("span", { class: "pos-raw", title: "raw count in slot edit-rate units" }, posRaw),
  ]);

  const text = el("span", { class: "clip-text" });
  if (!isSourceClip) {
    text.appendChild(el("span", { class: "reason" }, clip.component_class.toLowerCase()));
  } else {
    if (clip.mic_identity) {
      text.appendChild(el("span", { class: "mic" }, clip.mic_identity));
    }
    if (clip.source_mob_name) {
      text.appendChild(el(
        "span",
        { class: "src" },
        clip.mic_identity ? ` ← ${clip.source_mob_name}` : clip.source_mob_name
      ));
    } else if (!clip.mic_identity) {
      text.appendChild(el("span", { class: "src" }, "(unnamed source)"));
    }
    if (clip.terminal_reason && clip.terminal_reason !== "essence") {
      text.appendChild(el("span", { class: "reason" }, `[${clip.terminal_reason}]`));
    }
    if (clip.physical_track_number != null && !clip.is_recorder_source) {
      text.appendChild(el("span", { class: "reason" }, `ptn ${clip.physical_track_number}`));
    }
  }
  // Offline indicator: red dot when any source locator is explicitly
  // offline (file:// path doesn't exist). Operator-meaningful: needs
  // a fetch from the recordist before conform.
  let offlineMarker = null;
  if (Array.isArray(clip.source_locators) && clip.source_locators.length > 0) {
    const anyOffline = clip.source_locators.some((l) => l.online === false);
    if (anyOffline) {
      offlineMarker = el("span", {
        class: "online-marker offline",
        title: "source file path doesn't exist on disk",
      });
    }
  }
  if (offlineMarker) text.appendChild(offlineMarker);

  return el("span", { class: "tree-row-grid" }, [
    pos,
    el("span", { class: "clip-len", title: "duration" }, lenStr),
    text,
  ]);
}

// ---------- TC + seconds formatters (mirrors core/operator.py) ----------

function pad2(n) { return String(n).padStart(2, "0"); }

function parseRational(s) {
  if (!s || typeof s !== "string") return null;
  const parts = s.split("/").map(Number);
  if (parts.length !== 2 || !parts[1]) return null;
  return parts[0] / parts[1];
}

function formatTimecodeJs(frames, fpsNominal, drop) {
  if (frames == null || fpsNominal == null) return null;
  let f = Math.floor(Number(frames));
  const fps = Number(fpsNominal) | 0;
  if (fps <= 0) return null;
  let sep = ":";
  if (drop && (fps === 30 || fps === 60)) {
    const dropFrames = fps === 30 ? 2 : 4;
    const fpm = (60 * fps) - dropFrames;
    const fp10m = (10 * 60 * fps) - 9 * dropFrames;
    const d = Math.floor(f / fp10m);
    const m = f % fp10m;
    if (m > dropFrames) {
      f += (dropFrames * 9 * d) + dropFrames * Math.floor((m - dropFrames) / fpm);
    } else {
      f += dropFrames * 9 * d;
    }
    sep = ";";
  }
  const fr = f % fps;
  const sec = Math.floor(f / fps) % 60;
  const mn = Math.floor(f / (fps * 60)) % 60;
  const hr = Math.floor(f / (fps * 60 * 60));
  return `${pad2(hr)}:${pad2(mn)}:${pad2(sec)}${sep}${pad2(fr)}`;
}

function formatSecondsJs(seconds) {
  if (seconds == null || !isFinite(seconds)) return null;
  const total = Math.abs(seconds);
  const sign = seconds < 0 ? "-" : "";
  const hr = Math.floor(total / 3600);
  const mn = Math.floor((total % 3600) / 60);
  const sc = total - hr * 3600 - mn * 60;
  const scStr = sc.toFixed(3).padStart(6, "0");
  return hr > 0 ? `${sign}${hr}:${pad2(mn)}:${scStr}` : `${sign}${mn}:${scStr}`;
}

function formatBytes(b) {
  if (b == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let u = 0, n = b;
  while (n >= 1024 && u < units.length - 1) { n /= 1024; u++; }
  if (n < 10) return `${n.toFixed(2)} ${units[u]}`;
  if (n < 100) return `${n.toFixed(1)} ${units[u]}`;
  return `${n.toFixed(0)} ${units[u]}`;
}

function renderObjectRowContent(obj, propName) {
  // For a tree node representing a nested aaf_object, show its property
  // path label (the prop name passed in) plus the object class + name.
  const cls = obj.class || "?";
  const text = el("span", { class: "clip-text" });
  if (propName) {
    text.appendChild(el("span", { class: "obj-prop" }, propName));
  } else {
    text.appendChild(el("span", { class: "obj-class" }, cls));
    if (obj.name) text.appendChild(el("span", { class: "obj-name" }, `"${obj.name}"`));
    else if (obj.mob_id) {
      text.appendChild(el("span", { class: "obj-name" }, tailMobId(obj.mob_id)));
    }
  }
  return text;
}

function formatSamples(n) {
  return n == null ? "" : String(n);
}

function toggleTreeExpand(node) {
  if (tracksState.expanded.has(node.id)) {
    tracksState.expanded.delete(node.id);
    renderClipsPane();
    return;
  }
  tracksState.expanded.add(node.id);
  // Lazy-fetch children if not cached yet.
  if (!tracksState.childrenCache.has(node.id)) {
    tracksState.childrenCache.set(node.id, "loading");
    renderClipsPane();
    Promise.resolve()
      .then(() => node.fetchKids())
      .then((kids) => {
        tracksState.childrenCache.set(node.id, kids || []);
        renderClipsPane();
      })
      .catch((e) => {
        tracksState.childrenCache.set(node.id, { _error: e.message || String(e) });
        renderClipsPane();
      });
  } else {
    renderClipsPane();
  }
}

function selectTreeNode(node) {
  tracksState.selectedNodeId = node.id;
  tracksState.selectedNode = node;
  // Update selection highlight without a full re-render of the tree
  // (re-render would also lose any in-progress focus state).
  $$("#clips-tree .tree-row").forEach((r) => r.classList.remove("selected"));
  const dom = document.querySelector(
    `#clips-tree .tree-node[data-node-id="${cssEscape(node.id)}"] > .tree-row`
  );
  if (dom) dom.classList.add("selected");
  renderTracksInspector();
}

// ---------- inspector for the tracks-view selection ----------

function resetTracksInspector() {
  // Clear breadcrumb + inspector when no selection, so switching tracks
  // doesn't leave stale content from the previous selection.
  $("#breadcrumb").replaceChildren();
  $("#inspector").replaceChildren(
    el("p", { class: "muted" }, "Select a clip or expanded child to inspect it.")
  );
}

function renderTracksInspector() {
  const node = tracksState.selectedNode;
  if (!node) return resetTracksInspector();
  if (node.kind === "clip") return renderClipInspector(node.data);
  if (node.kind === "object") return renderObjectInspector(node.data, node.propName);
  resetTracksInspector();
}

function renderClipInspector(clip) {
  // Just the operator summary for the selected clip — no auto-fetch of
  // the source mob. The user expands the tree to navigate into the
  // source; selecting a deeper node updates the inspector to that level.
  $("#breadcrumb").replaceChildren(
    el("span", { class: "muted" }, "track "),
    el("span", { class: "crumb head" },
      `${trackContextLabel(tracksState.selectedSlotId)} / clip ${clip.index}`),
  );
  $("#inspector").replaceChildren(operatorSummaryFor(clip));
}

function renderObjectInspector(obj, propName) {
  // The selected node is a nested AAF object reached via the tree.
  // Show ONLY this object's information — its class header, its
  // mob_id, and the existing property table. Nested-property
  // expansion within the inspector is the existing
  // populateNestedObject behavior (independent of the center-pane
  // tree's expansion).
  const labelBits = [];
  if (propName) labelBits.push(el("span", { class: "muted" }, propName + " "));
  labelBits.push(el(
    "span",
    { class: "crumb head" },
    obj.name ? `${obj.class || "?"} "${obj.name}"` : (obj.class || "?")
  ));
  $("#breadcrumb").replaceChildren(...labelBits);
  $("#inspector").replaceChildren(renderObjectInline(obj));
}

function operatorSummaryFor(clip) {
  // Operator-meaningful summary for an operator_clip. Stands apart
  // from the existing geek-view object dump via .operator-summary.
  const dl = el("dl");
  const row = (k, v, cls) => {
    dl.appendChild(el("dt", {}, k));
    dl.appendChild(el("dd", cls ? { class: cls } : {}, v == null ? "—" : String(v)));
  };
  row("Component", clip.component_class);
  row("Timeline start", clip.timeline_start);
  row("Length", clip.length);
  if (clip.component_class === "SourceClip") {
    row("Source MasterMob", clip.source_mob_name || "(unnamed)");
    row("Source slot", clip.source_mob_slot_id);
    row("Recovered mic", clip.mic_identity || "(none)",
        clip.mic_identity ? "mic" : "muted");
    row("Recorder source?", clip.is_recorder_source ? "yes" : "no");
    row("PhysicalTrackNumber", clip.physical_track_number);
    row("Chain hops", clip.chain_length);
    row("Terminal reason", clip.terminal_reason);
    // Per-clip audio specs + handles
    const sr = clip.audio_sample_rate
      ? formatSampleRate(clip.audio_sample_rate)
      : null;
    if (sr || clip.audio_bits_per_sample || clip.audio_channels) {
      row("Audio format",
        [sr,
         clip.audio_bits_per_sample ? `${clip.audio_bits_per_sample}-bit` : null,
         clip.audio_channels ? formatChannelCount(clip.audio_channels) : null,
        ].filter(Boolean).join(" · ") || "—");
    }
    if (clip.head_handle_frames != null || clip.tail_handle_frames != null) {
      const headStr = clip.head_handle_seconds != null
        ? `${clip.head_handle_frames} frames (${formatSecondsJs(clip.head_handle_seconds)})`
        : (clip.head_handle_frames != null
            ? `${clip.head_handle_frames} frames` : "—");
      const tailStr = clip.tail_handle_seconds != null
        ? `${clip.tail_handle_frames} frames (${formatSecondsJs(clip.tail_handle_seconds)})`
        : (clip.tail_handle_frames != null
            ? `${clip.tail_handle_frames} frames` : "—");
      row("Head handle", headStr);
      row("Tail handle", tailStr);
    }
  }

  // Sub-clips (multi-input combiner) summary
  if (clip.sub_clips && clip.sub_clips.length > 0) {
    row("Combiner inputs", clip.sub_clips.length);
  }

  // Format-aware recovery status. Color the value when
  // unrecoverable/ambiguous so the operator notices.
  if (clip.recovery_status) {
    const cls = clip.recovery_status === "recoverable" ? "mic"
              : clip.recovery_status === "unrecoverable" ? "muted"
              : "muted";
    row("Recovery", clip.recovery_status, cls);
    if (clip.recovery_method) row("Recovery method", clip.recovery_method);
  }

  return el("section", { class: "operator-summary" }, [
    el("h4", {}, "Operator summary"),
    dl,
    clip.source_locators && clip.source_locators.length > 0
      ? renderLocatorsList(clip.source_locators)
      : null,
  ].filter(Boolean));
}

// Render an aaf_object the same way the geek-view inspector does,
// reusing its property-row + nested-expansion helpers.
function renderObjectInline(obj) {
  const host = el("div", { class: "operator-nested-object" });
  const header = el("div", { class: "obj-header" }, [
    el("span", { class: "class-tag" }, obj.class || "?"),
    obj.name ? el("span", { class: "obj-name" }, obj.name) : null,
    obj.mob_id
      ? el("span", { class: "obj-mob-id", title: obj.mob_id }, obj.mob_id)
      : null,
  ].filter(Boolean));
  host.appendChild(header);
  const tbl = el("div", { class: "props-table" });
  const props = obj.properties || {};
  for (const pname of Object.keys(props)) {
    tbl.appendChild(renderPropertyRow(pname, props[pname]));
  }
  host.appendChild(tbl);
  return host;
}

// ---------- Sources view — cross-track pull list ----------

async function loadSourcesIfNeeded(force = false) {
  if (!state.file) {
    renderSourceList();
    return;
  }
  if (sourcesState.loaded && !force) return;
  // First fetch is slow (server walks every clip in the topmost
  // composition); show a loading hint in the list.
  $("#source-list").replaceChildren(
    el("div", { class: "loading-row" }, [
      el("span", { class: "spinner" }),
      document.createTextNode(" Building source inventory…"),
    ])
  );
  try {
    const env = await api.sources();
    sourcesState.sources = env.sources || [];
    sourcesState.loaded = true;
    renderSourceList();
  } catch (e) {
    $("#source-list").replaceChildren(
      el("p", { class: "error", style: "padding: 14px;" },
        "Error: " + (e.message || String(e)))
    );
  }
}

function renderSourceList() {
  const root = $("#source-list");
  root.replaceChildren();
  if (!state.file) {
    root.appendChild(el("p", { class: "muted", style: "padding: 14px;" },
      "No file open."));
    return;
  }
  const filter = sourcesState.filter.toLowerCase();
  const matches = sourcesState.sources.filter((s) => {
    if (!filter) return true;
    return (s.name || "").toLowerCase().includes(filter);
  });
  if (matches.length === 0) {
    root.appendChild(el("p", { class: "muted", style: "padding: 14px;" },
      filter ? "No matches." : "No sources."));
    return;
  }
  for (const src of matches) {
    root.appendChild(renderSourceRow(src));
  }
}

function renderSourceRow(src) {
  const isSelected = sourcesState.selectedMobId === src.mob_id;
  const isUsed = (src.use_count || 0) > 0;
  // Online indicator: choose the strongest signal across the source's
  // locators. green dot if any locator is online; red if any is
  // explicitly offline; grey if all are unknown (non-local URLs); none
  // if no locators.
  let onlineSignal = "none";
  if (src.locators && src.locators.length > 0) {
    onlineSignal = "unknown";
    for (const loc of src.locators) {
      if (loc.online === true) { onlineSignal = "online"; break; }
      if (loc.online === false) onlineSignal = "offline";
    }
  }
  const indicator = onlineSignal === "none"
    ? null
    : el("span", {
        class: "online-dot " + (onlineSignal === "online" ? "" : onlineSignal),
        title: onlineSignal === "online"
          ? "source file resolves locally"
          : (onlineSignal === "offline"
              ? "source file path doesn't exist on disk"
              : "source URL not resolvable locally"),
      });

  return el(
    "div",
    {
      class: "source-row" + (isSelected ? " selected" : "")
              + (isUsed ? "" : " unused"),
      onclick: () => selectSource(src.mob_id),
    },
    [
      el("span", { class: "source-uses", title: `${src.use_count} clip uses` },
        src.use_count != null ? String(src.use_count) : "0"),
      el("span", { class: "source-name" + (src.name ? "" : " untitled") },
        src.name || `untitled ${tailMobId(src.mob_id)}`),
      el("span", { class: "source-format" }, formatSourceFormat(src)),
      indicator,
    ].filter(Boolean)
  );
}

function formatSourceFormat(src) {
  // Short label combining sample rate / bit depth / channels +
  // descriptor class (e.g. "48k 24-bit mono · WAVE").
  const bits = [];
  if (src.sample_rate) {
    bits.push(formatSampleRate(src.sample_rate));
  }
  if (src.bits_per_sample) {
    bits.push(`${src.bits_per_sample}-bit`);
  }
  if (src.channels) {
    bits.push(formatChannelCount(src.channels));
  }
  if (src.descriptor_class) {
    const short = src.descriptor_class
      .replace(/Descriptor$/, "")
      .replace(/^Import$/, "imp")
      .replace(/^Tape$/, "tape");
    bits.push(short);
  }
  return bits.length ? bits.join(" · ") : "—";
}

function selectSource(mobId) {
  sourcesState.selectedMobId = mobId;
  $$(".source-row").forEach((r) => r.classList.remove("selected"));
  // Find and highlight the row by mob_id (use a data attribute? or
  // re-render). Re-render is simpler; the source list is small.
  renderSourceList();
  showSourceInInspector(mobId);
}

async function showSourceInInspector(mobId) {
  const src = sourcesState.sources.find((s) => s.mob_id === mobId);
  if (!src) return;
  $("#breadcrumb").replaceChildren(
    el("span", { class: "muted" }, "source "),
    el("span", { class: "crumb head" },
      src.name || tailMobId(mobId)),
  );
  // Operator-summary-style header for the source, then the full
  // /api/object dump for the underlying SourceMob, then a "Used by"
  // panel with clickable jumps to clips.
  $("#inspector").replaceChildren(operatorSummaryForSource(src));
  const inspector = $("#inspector");
  // Used-by list
  if (src.used_by && src.used_by.length > 0) {
    inspector.appendChild(renderUsedByPanel(src));
  }
  // Underlying SourceMob object dump
  const loading = el("p", { class: "muted" }, "Loading source mob…");
  inspector.appendChild(loading);
  try {
    const env = await api.object({ mob_id: mobId });
    loading.remove();
    inspector.appendChild(operatorSubheader("Source mob (raw)"));
    inspector.appendChild(renderObjectInline(env.object));
  } catch (e) {
    loading.textContent = "Error: " + (e.message || String(e));
    loading.classList.remove("muted");
    loading.classList.add("error");
  }
}

function operatorSummaryForSource(src) {
  const dl = el("dl");
  const row = (k, v, cls) => {
    dl.appendChild(el("dt", {}, k));
    dl.appendChild(el("dd", cls ? { class: cls } : {}, v == null ? "—" : String(v)));
  };
  row("Name", src.name || "(unnamed)", src.name ? "mic" : "muted");
  row("Mob ID", tailMobId(src.mob_id));
  row("Descriptor", src.descriptor_class || "—");
  row("Sample rate", src.sample_rate ? formatSampleRate(src.sample_rate) : "—");
  row("Bit depth", src.bits_per_sample ? `${src.bits_per_sample}-bit` : "—");
  row("Channels", src.channels ? formatChannelCount(src.channels) : "—");
  row("Use count", src.use_count);
  return el("section", { class: "operator-summary" }, [
    el("h4", {}, "Source summary"),
    dl,
    src.locators && src.locators.length > 0
      ? renderLocatorsList(src.locators)
      : null,
  ].filter(Boolean));
}

function renderLocatorsList(locators) {
  const host = el("div", {
    style: "margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border);"
  });
  host.appendChild(el("h4", {}, "Locators"));
  for (const loc of locators) {
    const onlineLabel = loc.online === true ? "online"
                      : loc.online === false ? "offline"
                      : "unknown";
    const onlineClass = loc.online === true ? "" :
                       loc.online === false ? "offline" : "unknown";
    host.appendChild(el("div",
      { style: "display: flex; gap: 8px; align-items: center; padding: 2px 0;" },
      [
        el("span", { class: "online-dot " + onlineClass,
                      title: onlineLabel }),
        el("span", { class: "muted", style: "font-size:10px; min-width:48px;" },
          loc.kind),
        el("span", { style: "word-break: break-all;" }, loc.url),
      ]));
  }
  return host;
}

function renderUsedByPanel(src) {
  const panel = el("section", { class: "used-by-panel" });
  panel.appendChild(el("h4", {},
    `Used by ${src.used_by.length} clip${src.used_by.length === 1 ? "" : "s"}` +
    (src.use_count > src.used_by.length
      ? ` (showing ${src.used_by.length} of ${src.use_count})` : "")));
  for (const u of src.used_by) {
    const trackLabel = u.track_name || `slot ${u.track_slot_id}`;
    const tcLabel = sessionState.timecode
      ? formatTimecodeJs(
          (sessionState.timecode.start_frames || 0) + (u.timeline_start || 0),
          sessionState.timecode.fps_nominal,
          !!sessionState.timecode.drop)
      : null;
    panel.appendChild(el(
      "div",
      {
        class: "used-by-row",
        title: "Jump to this clip",
        onclick: () => jumpToClipFromSource(u.track_slot_id, u.clip_index),
      },
      [
        el("span", { class: "ub-track" }, trackLabel),
        el("span", { class: "ub-clip" }, `clip ${u.clip_index}`),
        el("span", { class: "ub-tc" }, tcLabel || String(u.timeline_start || 0)),
      ]
    ));
  }
  return panel;
}

async function jumpToClipFromSource(slotId, clipIndex) {
  // Switch to Tracks view, select the track, then highlight the clip.
  activateView("tracks");
  await selectTrack(slotId);
  // After selectTrack the clips are loaded. Find the clip and select it.
  const clip = tracksState.clips.find((c) => c.index === clipIndex);
  if (clip) {
    selectTreeNode(buildClipNode(clip));
    // Scroll the row into view
    setTimeout(() => {
      const row = document.querySelector(
        `#clips-tree .tree-node[data-node-id="clip:${slotId}:${clipIndex}"] > .tree-row`
      );
      if (row) row.scrollIntoView({ block: "center" });
    }, 0);
  }
}

function wireSourceFilter() {
  const input = $("#source-filter");
  if (!input) return;
  input.addEventListener("input", (ev) => {
    sourcesState.filter = ev.target.value.trim();
    renderSourceList();
  });
}

// ---------- splitter drag handlers ----------

function wireSplitters() {
  $$(".splitter").forEach((sp) => {
    sp.addEventListener("pointerdown", (ev) => beginResize(ev, sp));
  });
}

function beginResize(ev, sp) {
  ev.preventDefault();
  const varName = sp.dataset.resize;
  const min = Number(sp.dataset.min || "120");
  const max = Number(sp.dataset.max || "1200");
  // Read current pixel width from the resolved CSS variable.
  const cs = getComputedStyle(document.body);
  const startWidth = parseFloat(cs.getPropertyValue(varName)) || 240;
  const startX = ev.clientX;
  sp.classList.add("dragging");
  document.body.classList.add("resizing");
  sp.setPointerCapture(ev.pointerId);

  const onMove = (e) => {
    const delta = e.clientX - startX;
    const next = Math.max(min, Math.min(max, startWidth + delta));
    document.body.style.setProperty(varName, next + "px");
  };
  const onUp = (e) => {
    sp.releasePointerCapture(ev.pointerId);
    sp.classList.remove("dragging");
    document.body.classList.remove("resizing");
    sp.removeEventListener("pointermove", onMove);
    sp.removeEventListener("pointerup", onUp);
    sp.removeEventListener("pointercancel", onUp);
  };
  sp.addEventListener("pointermove", onMove);
  sp.addEventListener("pointerup", onUp);
  sp.addEventListener("pointercancel", onUp);
}

// ---------- bootstrap ----------

async function init() {
  wireTopbar();
  wireViewTabs();
  wireSplitters();
  wireFilter();
  wireSourceFilter();
  wireCfbControls();
  wireFindPanel();
  setFileStatus();
  renderMobList();
  renderTrackList();
  renderClipsPane();
  // Default landing: Tracks view
  activateView("tracks");

  // If a file was opened via `aafbrowser web <path>` (or by the
  // bundled .app's argv), the server already has it loaded — fetch
  // the index + tracks. Wrap in the loading-state UI: even though
  // /api/file is fast, /api/tracks does the chain-walks for the
  // topmost composition's slots and is worth flagging.
  try {
    const meta = await api.file();
    if (meta) {
      const basename = meta.path.split("/").pop();
      beginAafLoading(basename);
      try {
        state.file = meta;
        renderClassFilterOptions();
        const mobs = await api.mobs();
        state.mobs = mobs.mobs;
        renderMobList();
        await Promise.all([loadSession(), loadTracks()]);
      } finally {
        endAafLoading();
      }
    }
  } catch (_) { /* no-op; user can open manually */ }
}

// --------- inspector layout sync (right-edge gutter) ---------
//
// WebKit's CSS sizing for the inspector pane (overflow:auto + nested
// flex/grid descendants with intrinsic widths) was producing
// inconsistent results — the inspector's box would sometimes be
// narrower than its descendants, letting the descendants visually
// overflow past the inspector's right edge into the sibling gutter.
// CSS workarounds (`min-width: max-content`, grid `max-content`
// columns, inline-block shrink-to-fit) all hit edge cases.
//
// This JS-driven fix is bulletproof: after every content change in
// #inspector, we explicitly set #inspector.style.width to its
// scrollWidth (the actual extent of all its descendants), and place
// the gutter at that width via position: absolute. The scroll
// container's scrollWidth then equals inspector.width + gutter.width,
// giving exactly 28px of breathing room past the rightmost descendant
// regardless of how WebKit sizes things.
function syncInspectorLayout() {
  const insp = document.getElementById("inspector");
  const gutter = document.getElementById("inspector-end-gutter");
  if (!insp || !gutter) return;
  // Reset width so we can measure the natural content extent.
  insp.style.width = "auto";
  // Force layout reflow before measuring.
  void insp.offsetWidth;
  // scrollWidth includes overflowing descendants — this is the
  // true rightmost pixel of any content in the inspector.
  const naturalWidth = insp.scrollWidth;
  // Lock width so flex/grid sizing can't compress it later.
  insp.style.width = naturalWidth + "px";
  // Position the gutter right after the content. The +0 is just
  // explicit — left should equal inspector's right edge.
  gutter.style.left = naturalWidth + "px";
}

function setupInspectorLayoutSync() {
  const insp = document.getElementById("inspector");
  if (!insp) return;
  // MutationObserver fires on any DOM change inside #inspector.
  // We debounce with requestAnimationFrame to coalesce bursts of
  // changes (e.g. expanding multiple nodes) into one measurement.
  let pending = false;
  const obs = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      syncInspectorLayout();
    });
  });
  obs.observe(insp, { childList: true, subtree: true, characterData: true });
  // Also resync when the window resizes (changes pane width).
  window.addEventListener("resize", () => {
    requestAnimationFrame(syncInspectorLayout);
  });
  // Initial sync.
  requestAnimationFrame(syncInspectorLayout);
}

document.addEventListener("DOMContentLoaded", () => {
  init();
  setupInspectorLayoutSync();
});
