function defaultNormalizer(raw, section) {
  const kind = raw.kind || section.kind || section.key || "item";
  const label = raw.label || raw.title || raw.name || raw.filename || raw.rel || "Untitled";
  const rel = raw.rel || raw.name || raw.filename || raw.path || "";
  const runId = raw.runId || raw.run_id || raw.song || raw.run || "";
  const out = raw.out || raw.output || raw.stem || "";
  const meta = raw.meta || {};
  return { kind, label, rel, runId, out, meta, raw };
}

function makeItemKey(item) {
  return [item.kind || "", item.runId || "", item.out || "", item.rel || ""].join("|");
}

function matchesSelection(item, selected) {
  if (!item || !selected) return false;
  return makeItemKey(item) === makeItemKey(selected);
}

function serializeForSearch(item) {
  const parts = [item.label, item.rel, item.runId, item.out];
  if (item.meta) {
    try {
      parts.push(JSON.stringify(item.meta));
    } catch (_err) {
      parts.push(String(item.meta));
    }
  }
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function createFileBrowser({ mountId, sections, onSelect, getSelected, setSelected }) {
  const root = document.getElementById(mountId);
  if (!root) return null;
  const searchInput = document.getElementById(`${mountId}-search`);
  const state = {
    itemsBySection: new Map(),
    nodesBySection: new Map(),
  };

  const getCurrent = typeof getSelected === "function"
    ? getSelected
    : () => state.selected;
  const setCurrent = typeof setSelected === "function"
    ? setSelected
    : (item) => { state.selected = item; };

  function collapsedKey(sectionKey) {
    return `fileBrowser:${mountId}:${sectionKey}:collapsed`;
  }

  function setCollapsed(sectionKey, collapsed) {
    try {
      localStorage.setItem(collapsedKey(sectionKey), collapsed ? "1" : "0");
    } catch (_err) {
      // ignore storage errors
    }
  }

  function getCollapsed(sectionKey) {
    try {
      return localStorage.getItem(collapsedKey(sectionKey)) === "1";
    } catch (_err) {
      return false;
    }
  }

  function renderSection(section, items) {
    const listEl = document.getElementById(`${mountId}-list-${section.key}`);
    if (!listEl) return;
    listEl.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "file-browser-empty";
      empty.textContent = "No files";
      listEl.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "file-browser-item";
      btn.dataset.browserItem = "1";
      btn.dataset.title = (item.label || "").toLowerCase();
      btn.dataset.itemKey = makeItemKey(item);

      const title = document.createElement("div");
      title.className = "file-browser-item-title";
      title.textContent = item.label;
      btn.appendChild(title);

      const detail = item.meta && (item.meta.detail || item.meta.subtitle || item.meta.sub);
      if (detail) {
        const meta = document.createElement("div");
        meta.className = "file-browser-item-meta";
        meta.textContent = detail;
        btn.appendChild(meta);
      }

      btn.addEventListener("click", () => {
        setCurrent(item);
        highlightSelection();
        if (typeof onSelect === "function") {
          onSelect(item);
        }
      });
      listEl.appendChild(btn);
    });
    applySearch();
    highlightSelection();
  }

  function highlightSelection() {
    const selected = getCurrent();
    sections.forEach((section) => {
      const listEl = document.getElementById(`${mountId}-list-${section.key}`);
      if (!listEl) return;
      listEl.querySelectorAll(".file-browser-item").forEach((el) => {
        const key = el.dataset.itemKey || "";
        const item = (state.nodesBySection.get(section.key) || new Map()).get(key);
        const active = item && matchesSelection(item, selected);
        el.classList.toggle("active", !!active);
        el.setAttribute("aria-selected", active ? "true" : "false");
      });
    });
  }

  function applySearch() {
    const query = (searchInput && searchInput.value || "").trim().toLowerCase();
    sections.forEach((section) => {
      const listEl = document.getElementById(`${mountId}-list-${section.key}`);
      if (!listEl) return;
      const nodes = state.nodesBySection.get(section.key);
      let visible = 0;
      listEl.querySelectorAll(".file-browser-item").forEach((el) => {
        const item = nodes ? nodes.get(el.dataset.itemKey || "") : null;
        const text = item ? serializeForSearch(item) : "";
        const match = !query || text.includes(query);
        el.style.display = match ? "" : "none";
        if (match) visible += 1;
      });
      const empty = listEl.querySelector(".file-browser-empty");
      if (empty) {
        empty.textContent = query && visible === 0 ? "No matches" : "No files";
      }
    });
  }

  async function loadSection(section) {
    const listEl = document.getElementById(`${mountId}-list-${section.key}`);
    if (!listEl) return;
    listEl.innerHTML = '<div class="file-browser-empty">Loading...</div>';
    let data = null;
    try {
      const res = await fetch(section.endpoint, { cache: "no-store" });
      if (!res.ok) throw new Error("load_failed");
      data = await res.json();
    } catch (_err) {
      listEl.innerHTML = '<div class="file-browser-empty">Failed to load</div>';
      return;
    }
    const rawItems = Array.isArray(data) ? data : (data.items || []);
    const items = rawItems.map((raw) => defaultNormalizer(raw, section));
    const nodeMap = new Map();
    items.forEach((item) => nodeMap.set(makeItemKey(item), item));
    state.itemsBySection.set(section.key, items);
    state.nodesBySection.set(section.key, nodeMap);
    renderSection(section, items);
  }

  function loadAll() {
    sections.forEach((section) => loadSection(section));
  }

  function wireSection(section) {
    const sectionEl = root.querySelector(`.file-browser-section[data-section="${section.key}"]`);
    const toggle = document.getElementById(`${mountId}-toggle-${section.key}`);
    if (sectionEl) {
      const collapsed = getCollapsed(section.key);
      sectionEl.classList.toggle("collapsed", collapsed);
    }
    if (toggle && sectionEl) {
      toggle.addEventListener("click", () => {
        const isCollapsed = sectionEl.classList.toggle("collapsed");
        setCollapsed(section.key, isCollapsed);
      });
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", applySearch);
  }
  sections.forEach((section) => wireSection(section));
  loadAll();

  return {
    reload: loadAll,
    reloadSection: loadSection,
    highlight: highlightSelection,
    getSelected: getCurrent,
    setSelected: setCurrent,
  };
}
