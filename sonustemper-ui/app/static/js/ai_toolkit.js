(() => {
  const toolCards = Array.from(document.querySelectorAll('.ai-tool-card'));
  const toolLabels = {
    ai_deglass: 'Reduce AI Hiss / Glass',
    ai_vocal_smooth: 'Smooth Harsh Vocals',
    ai_bass_tight: 'Tighten Bass / Remove Rumble',
    ai_transient_soften: 'Reduce Pumping / Over-Transients',
    ai_platform_safe: 'Platform Ready (AI Safe Loudness)',
  };

  const toolDefaults = {
    ai_deglass: {
      strength: 35,
      options: { lp_enabled: true, preserve_air: false, afftdn_strength: 0.3 },
    },
    ai_vocal_smooth: {
      strength: 30,
      options: { center_hz: 4500, s_cut: false, s_hz: 7500, afftdn_strength: 0 },
    },
    ai_bass_tight: {
      strength: 40,
      options: { hp_hz: 40, mud_hz: 220, punch: false },
    },
    ai_transient_soften: {
      strength: 25,
      options: { keep_punch: false },
    },
    ai_platform_safe: {
      strength: 40,
      options: { preset: 'streaming' },
    },
  };

  const aiPairBrowser = document.getElementById('aiPairBrowser');
  const aiAnyBrowser = document.getElementById('aiAnyBrowser');
  const aiPairWrap = document.getElementById('aiPairBrowserWrap');
  const aiAnyWrap = document.getElementById('aiAnyBrowserWrap');
  const aiAnyUploadBtn = document.getElementById('aiAnyUploadBtn');
  const aiAnyUploadInput = document.getElementById('aiAnyUploadInput');
  const aiAnyUploadStatus = document.getElementById('aiAnyUploadStatus');
  const aiSelectedName = document.getElementById('aiSelectedName');
  const aiSelectedMeta = document.getElementById('aiSelectedMeta');
  const aiSelectedAudio = document.getElementById('aiSelectedAudio');
  const aiPreviewAudio = document.getElementById('aiPreviewAudio');
  const aiPreviewStatus = document.getElementById('aiPreviewStatus');
  const aiPreviewOriginal = document.getElementById('aiPreviewOriginal');
  const aiPreviewProcessed = document.getElementById('aiPreviewProcessed');
  const aiResultsList = document.getElementById('aiResultsList');
  const aiPresetName = document.getElementById('aiPresetName');
  const aiPresetTool = document.getElementById('aiPresetTool');
  const aiPresetSaveBtn = document.getElementById('aiPresetSaveBtn');
  const aiPresetStatus = document.getElementById('aiPresetStatus');
  const aiPresetList = document.getElementById('aiPresetList');

  const modeRadios = Array.from(document.querySelectorAll('input[name="aiSourceMode"]'));

  const state = {
    mode: 'source',
    source: null,
    processed: null,
    any: null,
    selected: null,
    duration: null,
    preview: {
      processed: null,
      original: null,
      focus: null,
      len: 10,
      path: null,
    },
  };

  function formatSeconds(raw) {
    const secs = Number(raw);
    if (!Number.isFinite(secs)) return '-';
    const m = Math.floor(secs / 60);
    const s = Math.round(secs % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  function formatDate(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString();
  }

  function setMode(mode) {
    state.mode = mode;
    if (aiPairWrap) aiPairWrap.hidden = mode === 'any';
    if (aiAnyWrap) aiAnyWrap.hidden = mode !== 'any';
    updateSelectedFromMode();
  }

  function setActiveItem(container, node) {
    if (!container) return;
    container.querySelectorAll('.browser-item.active').forEach((el) => el.classList.remove('active'));
    if (node) node.classList.add('active');
  }

  function setSelectedFile(selected) {
    if (selected?.rel && selected.rel !== state.preview.path) {
      state.preview.processed = null;
      state.preview.original = null;
      state.preview.focus = null;
      state.preview.path = selected.rel;
      if (aiPreviewOriginal) aiPreviewOriginal.disabled = true;
      if (aiPreviewProcessed) aiPreviewProcessed.disabled = true;
    }
    state.selected = selected;
    if (!aiSelectedName || !aiSelectedMeta || !aiSelectedAudio) return;
    if (!selected) {
      aiSelectedName.textContent = '-';
      aiSelectedMeta.textContent = 'No file selected.';
      aiSelectedAudio.hidden = true;
      aiSelectedAudio.removeAttribute('src');
      state.preview.processed = null;
      state.preview.original = null;
      state.preview.focus = null;
      state.preview.path = null;
      if (aiPreviewOriginal) aiPreviewOriginal.disabled = true;
      if (aiPreviewProcessed) aiPreviewProcessed.disabled = true;
      updateToolButtons();
      return;
    }
    aiSelectedName.textContent = selected.name || selected.rel || 'Selected';
    aiSelectedAudio.hidden = false;
    aiSelectedAudio.src = `/api/analyze/path?path=${encodeURIComponent(selected.rel)}`;
    updateFileInfo(selected.rel);
    updateToolButtons();
  }

  async function updateFileInfo(rel) {
    if (!rel) return;
    try {
      const res = await fetch(`/api/ai-tool/info?path=${encodeURIComponent(rel)}`);
      if (!res.ok) throw new Error('info_failed');
      const data = await res.json();
      const parts = [];
      if (data.duration_s) parts.push(`Duration: ${formatSeconds(data.duration_s)}`);
      if (data.sample_rate) parts.push(`Sample rate: ${data.sample_rate} Hz`);
      const stamp = formatDate(data.mtime);
      if (stamp) parts.push(`Modified: ${stamp}`);
      aiSelectedMeta.textContent = parts.length ? parts.join(' · ') : 'Metadata unavailable.';
      state.duration = data.duration_s || state.duration;
    } catch (_err) {
      aiSelectedMeta.textContent = 'Metadata unavailable.';
    }
  }

  function updateSelectedFromMode() {
    if (state.mode === 'any') {
      setSelectedFile(state.any);
      return;
    }
    if (state.mode === 'processed') {
      setSelectedFile(state.processed || state.source);
      return;
    }
    setSelectedFile(state.source || state.processed);
  }

  function updateToolButtons() {
    toolCards.forEach((card) => {
      card.querySelectorAll('[data-action="preview"], [data-action="apply"]').forEach((btn) => {
        btn.disabled = !state.selected;
      });
    });
  }

  async function resolveRun(song, out, solo) {
    const params = new URLSearchParams();
    params.set('song', song);
    if (out) params.set('out', out);
    if (solo) params.set('solo', '1');
    const res = await fetch(`/api/analyze-resolve?${params.toString()}`);
    if (!res.ok) throw new Error('resolve_failed');
    return res.json();
  }

  async function resolveFile(kind, rel) {
    const params = new URLSearchParams();
    if (kind === 'source') params.set('src', rel);
    if (kind === 'import') params.set('imp', rel);
    const res = await fetch(`/api/analyze-resolve-file?${params.toString()}`);
    if (!res.ok) throw new Error('resolve_failed');
    return res.json();
  }

  function applyResolvePayload(payload) {
    state.source = payload.source_rel ? { rel: payload.source_rel, name: payload.source_name || 'Source' } : null;
    state.processed = payload.processed_rel ? { rel: payload.processed_rel, name: payload.processed_name || 'Processed' } : null;
    state.duration = payload.duration_s || payload.metrics?.input?.duration_sec || payload.metrics?.output?.duration_sec || null;
    updateSelectedFromMode();
  }

  async function selectRun(item, node) {
    if (!item?.song) return;
    setActiveItem(aiPairBrowser, node);
    try {
      const payload = await resolveRun(item.song, item.out || '', item.solo);
      applyResolvePayload(payload);
      if (aiPreviewStatus) aiPreviewStatus.textContent = 'Ready to preview.';
    } catch (_err) {
      if (typeof showToast === 'function') showToast('Failed to load run');
    }
  }

  async function selectAnyFile(item, node) {
    if (!item?.rel) return;
    setActiveItem(aiAnyBrowser, node);
    try {
      const payload = await resolveFile(item.kind, item.rel);
      state.any = { rel: payload.source_rel, name: payload.source_name || item.rel };
      if (state.mode === 'any') {
        state.source = null;
        state.processed = null;
        setSelectedFile(state.any);
      }
      if (aiPreviewStatus) aiPreviewStatus.textContent = 'Ready to preview.';
    } catch (_err) {
      if (typeof showToast === 'function') showToast('Failed to load file');
    }
  }

  async function uploadAnyFile(file) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const xhr = new XMLHttpRequest();
    return new Promise((resolve, reject) => {
      xhr.open('POST', '/api/analyze-upload', true);
      xhr.responseType = 'json';
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(xhr.response || {});
        } else {
          reject(new Error('upload_failed'));
        }
      });
      xhr.addEventListener('error', () => reject(new Error('upload_failed')));
      xhr.send(fd);
    });
  }

  function getToolState(card) {
    const toolId = card.dataset.toolId;
    const strengthInput = card.querySelector('[data-role="strength"]');
    const strength = strengthInput ? parseInt(strengthInput.value || '0', 10) : 0;
    const options = {};
    card.querySelectorAll('[data-option]').forEach((input) => {
      const key = input.dataset.option;
      if (!key) return;
      if (input.type === 'checkbox') {
        options[key] = input.checked;
        return;
      }
      if (key === 'preset') {
        options[key] = input.value;
        return;
      }
      const scale = parseFloat(input.dataset.scale || '1');
      const raw = parseFloat(input.value || '0');
      options[key] = Number.isFinite(raw) ? raw * scale : 0;
    });
    return { toolId, strength, options };
  }

  function updateStrengthDisplay(card) {
    const input = card.querySelector('[data-role="strength"]');
    const label = card.querySelector('[data-role="strength-value"]');
    if (!input || !label) return;
    label.textContent = input.value || '0';
  }

  function resetTool(card) {
    const toolId = card.dataset.toolId;
    const defaults = toolDefaults[toolId];
    if (!defaults) return;
    const strengthInput = card.querySelector('[data-role="strength"]');
    if (strengthInput) strengthInput.value = String(defaults.strength);
    updateStrengthDisplay(card);
    card.querySelectorAll('[data-option]').forEach((input) => {
      const key = input.dataset.option;
      const targetVal = defaults.options[key];
      if (input.type === 'checkbox') {
        input.checked = Boolean(targetVal);
        return;
      }
      if (key === 'preset') {
        input.value = targetVal || 'streaming';
        return;
      }
      const scale = parseFloat(input.dataset.scale || '1');
      if (Number.isFinite(scale) && scale !== 0 && targetVal !== undefined) {
        input.value = String(targetVal / scale);
      }
    });
  }

  function setCardStatus(card, message) {
    const status = card.querySelector('[data-role="status"]');
    if (!status) return;
    status.textContent = message || '';
  }

  async function runPreview(card) {
    if (!state.selected) {
      setCardStatus(card, 'Select a file first.');
      return;
    }
    const { toolId, strength, options } = getToolState(card);
    setCardStatus(card, 'Generating preview...');
    try {
      const res = await fetch('/api/ai-tool/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.selected.rel,
          tool_id: toolId,
          strength,
          options,
          preview_len_sec: 10,
          preview_focus_sec: state.preview.focus,
        }),
      });
      if (!res.ok) throw new Error('preview_failed');
      const data = await res.json();
      state.preview.processed = data.url;
      state.preview.focus = data.preview_start + (data.duration * 0.5);
      state.preview.len = data.duration;
      state.preview.path = state.selected.rel;
      if (aiPreviewAudio) {
        aiPreviewAudio.src = data.url;
        aiPreviewAudio.play().catch(() => {});
      }
      if (aiPreviewStatus) {
        const label = toolLabels[toolId] || toolId;
        aiPreviewStatus.textContent = `Previewing ${label}`;
      }
      if (aiPreviewProcessed) aiPreviewProcessed.disabled = false;
      if (aiPreviewOriginal) aiPreviewOriginal.disabled = false;
      setCardStatus(card, 'Preview ready.');
    } catch (_err) {
      setCardStatus(card, 'Preview failed.');
      if (typeof showToast === 'function') showToast('Preview failed');
    }
  }

  async function fetchOriginalPreview() {
    if (!state.preview.path || !state.preview.focus) return null;
    try {
      const res = await fetch('/api/ai-tool/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.preview.path,
          tool_id: 'original',
          strength: 0,
          options: {},
          preview_len_sec: state.preview.len || 10,
          preview_focus_sec: state.preview.focus,
        }),
      });
      if (!res.ok) throw new Error('preview_failed');
      const data = await res.json();
      state.preview.original = data.url;
      return data.url;
    } catch (_err) {
      return null;
    }
  }

  async function runApply(card) {
    if (!state.selected) {
      setCardStatus(card, 'Select a file first.');
      return;
    }
    const { toolId, strength, options } = getToolState(card);
    setCardStatus(card, 'Rendering full track...');
    try {
      const res = await fetch('/api/ai-tool/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.selected.rel,
          tool_id: toolId,
          strength,
          options,
        }),
      });
      if (!res.ok) throw new Error('render_failed');
      const data = await res.json();
      addResultRow(data, toolId);
      setCardStatus(card, 'Render complete.');
    } catch (_err) {
      setCardStatus(card, 'Render failed.');
      if (typeof showToast === 'function') showToast('Render failed');
    }
  }

  function addResultRow(result, toolId) {
    if (!aiResultsList) return;
    const row = document.createElement('div');
    row.className = 'ai-result-row';
    const name = result.output_name || result.output_rel || 'Output';
    const label = toolLabels[toolId] || toolId;
    const sourceRel = state.selected?.rel || '';
    row.innerHTML = `
      <div class="ai-result-main">
        <div class="ai-result-name">${name}</div>
        <div class="ai-result-meta">${label}</div>
      </div>
      <div class="ai-result-actions">
        <a class="btn ghost tiny" href="${result.url}" download>Download</a>
        <button class="btn ghost tiny" type="button" data-action="compare">Open in Compare</button>
      </div>
    `;
    const btn = row.querySelector('[data-action="compare"]');
    if (btn) {
      btn.addEventListener('click', () => {
        if (!sourceRel || !result.output_rel) return;
        const url = new URL('/compare', window.location.origin);
        url.searchParams.set('src', sourceRel);
        url.searchParams.set('proc', result.output_rel);
        window.location.assign(`${url.pathname}${url.search}`);
      });
    }
    if (aiResultsList.querySelector('.muted')) {
      aiResultsList.innerHTML = '';
    }
    aiResultsList.prepend(row);
  }

  async function loadPresets() {
    if (!aiPresetList) return;
    try {
      const res = await fetch('/api/ai-tool/preset/list', { cache: 'no-store' });
      if (!res.ok) throw new Error('preset_failed');
      const data = await res.json();
      const items = Array.isArray(data.items) ? data.items : [];
      renderPresets(items);
    } catch (_err) {
      aiPresetList.innerHTML = '<div class="muted">Failed to load presets.</div>';
    }
  }

  function renderPresets(items) {
    if (!aiPresetList) return;
    if (!items.length) {
      aiPresetList.innerHTML = '<div class="muted">No presets yet.</div>';
      return;
    }
    aiPresetList.innerHTML = '';
    items.forEach((preset) => {
      const row = document.createElement('div');
      row.className = 'ai-preset-row';
      row.innerHTML = `
        <div class="ai-preset-main">
          <div class="ai-preset-title">${preset.title || preset.id}</div>
          <div class="ai-preset-meta">${toolLabels[preset.tool_id] || preset.tool_id}</div>
        </div>
        <div class="ai-preset-actions">
          <button class="btn ghost tiny" type="button" data-action="load">Load</button>
          <button class="btn ghost tiny" type="button" data-action="delete">Delete</button>
        </div>
      `;
      row.querySelector('[data-action="load"]').addEventListener('click', () => {
        applyPreset(preset);
      });
      row.querySelector('[data-action="delete"]').addEventListener('click', () => {
        deletePreset(preset.id);
      });
      aiPresetList.appendChild(row);
    });
  }

  function applyPreset(preset) {
    const card = toolCards.find((c) => c.dataset.toolId === preset.tool_id);
    if (!card) return;
    const strengthInput = card.querySelector('[data-role="strength"]');
    if (strengthInput && preset.strength !== undefined && preset.strength !== null) {
      strengthInput.value = String(preset.strength);
    }
    card.querySelectorAll('[data-option]').forEach((input) => {
      const key = input.dataset.option;
      if (!key || !preset.options) return;
      const value = preset.options[key];
      if (input.type === 'checkbox') {
        input.checked = Boolean(value);
        return;
      }
      if (key === 'preset') {
        input.value = value || 'streaming';
        return;
      }
      const scale = parseFloat(input.dataset.scale || '1');
      if (Number.isFinite(scale) && scale !== 0 && value !== undefined) {
        input.value = String(value / scale);
      }
    });
    updateStrengthDisplay(card);
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  async function savePreset() {
    if (!aiPresetTool || !aiPresetName || !aiPresetSaveBtn) return;
    const toolId = aiPresetTool.value;
    const card = toolCards.find((c) => c.dataset.toolId === toolId);
    if (!card) return;
    const name = (aiPresetName.value || '').trim();
    if (!name) {
      if (aiPresetStatus) aiPresetStatus.textContent = 'Enter a preset name.';
      return;
    }
    const { strength, options } = getToolState(card);
    aiPresetStatus.textContent = 'Saving...';
    try {
      const res = await fetch('/api/ai-tool/preset/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: name, tool_id: toolId, strength, options }),
      });
      if (!res.ok) throw new Error('save_failed');
      aiPresetName.value = '';
      aiPresetStatus.textContent = 'Saved.';
      loadPresets();
    } catch (_err) {
      aiPresetStatus.textContent = 'Save failed.';
    }
  }

  async function deletePreset(id) {
    if (!id) return;
    try {
      const res = await fetch(`/api/ai-tool/preset/delete?preset_id=${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error('delete_failed');
      loadPresets();
    } catch (_err) {
      if (aiPresetStatus) aiPresetStatus.textContent = 'Delete failed.';
    }
  }

  toolCards.forEach((card) => {
    updateStrengthDisplay(card);
    const strengthInput = card.querySelector('[data-role="strength"]');
    if (strengthInput) {
      strengthInput.addEventListener('input', () => updateStrengthDisplay(card));
    }
    if (card.dataset.toolId === 'ai_vocal_smooth') {
      const sCut = card.querySelector('[data-option="s_cut"]');
      const sHz = card.querySelector('[data-option="s_hz"]');
      const syncS = () => { if (sHz) sHz.disabled = !(sCut && sCut.checked); };
      if (sCut) sCut.addEventListener('change', syncS);
      syncS();
    }
    card.querySelectorAll('[data-action]').forEach((btn) => {
      const action = btn.dataset.action;
      if (action === 'preview') {
        btn.addEventListener('click', () => runPreview(card));
      } else if (action === 'apply') {
        btn.addEventListener('click', () => runApply(card));
      } else if (action === 'reset') {
        btn.addEventListener('click', () => resetTool(card));
      }
    });
  });

  if (modeRadios.length) {
    modeRadios.forEach((radio) => {
      radio.addEventListener('change', () => {
        if (radio.checked) setMode(radio.value);
      });
    });
  }

  if (aiPairBrowser) {
    aiPairBrowser.addEventListener('click', (evt) => {
      const itemNode = evt.target.closest('.browser-item');
      if (!itemNode || itemNode.disabled) return;
      const kind = itemNode.dataset.kind || '';
      if (kind !== 'mastering_output') return;
      let meta = {};
      if (itemNode.dataset.meta) {
        try {
          meta = JSON.parse(itemNode.dataset.meta || '{}');
        } catch (_err) {
          meta = {};
        }
      }
      selectRun({ song: meta.song, out: meta.out || '', solo: Boolean(meta.solo) }, itemNode);
    });
  }

  if (aiAnyBrowser) {
    aiAnyBrowser.addEventListener('click', (evt) => {
      const itemNode = evt.target.closest('.browser-item');
      if (!itemNode || itemNode.disabled) return;
      const kind = itemNode.dataset.kind || '';
      if (kind !== 'source' && kind !== 'import') return;
      let meta = {};
      if (itemNode.dataset.meta) {
        try {
          meta = JSON.parse(itemNode.dataset.meta || '{}');
        } catch (_err) {
          meta = {};
        }
      }
      const rel = meta.rel || itemNode.dataset.id || '';
      if (!rel) return;
      selectAnyFile({ kind, rel }, itemNode);
    });
  }

  if (aiAnyUploadBtn && aiAnyUploadInput) {
    aiAnyUploadBtn.addEventListener('click', () => aiAnyUploadInput.click());
    aiAnyUploadInput.addEventListener('change', async () => {
      const file = (aiAnyUploadInput.files || [])[0];
      if (!file) return;
      if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Uploading...';
      try {
        const data = await uploadAnyFile(file);
        if (data.rel) {
          state.any = { rel: `analysis/${data.rel}`, name: data.source_name || file.name };
          setMode('any');
          setSelectedFile(state.any);
        }
        if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Upload complete.';
        if (typeof showToast === 'function') showToast('Upload complete');
      } catch (_err) {
        if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Upload failed.';
        if (typeof showToast === 'function') showToast('Upload failed');
      } finally {
        aiAnyUploadInput.value = '';
      }
    });
  }

  if (aiPreviewOriginal) {
    aiPreviewOriginal.addEventListener('click', async () => {
      if (!aiPreviewAudio) return;
      if (!state.preview.original) {
        const url = await fetchOriginalPreview();
        if (url) state.preview.original = url;
      }
      if (state.preview.original) {
        aiPreviewAudio.src = state.preview.original;
        aiPreviewAudio.play().catch(() => {});
      }
    });
  }

  if (aiPreviewProcessed) {
    aiPreviewProcessed.addEventListener('click', () => {
      if (!aiPreviewAudio || !state.preview.processed) return;
      aiPreviewAudio.src = state.preview.processed;
      aiPreviewAudio.play().catch(() => {});
    });
  }

  if (aiPresetTool) {
    aiPresetTool.innerHTML = '';
    Object.keys(toolLabels).forEach((toolId) => {
      const opt = document.createElement('option');
      opt.value = toolId;
      opt.textContent = toolLabels[toolId];
      aiPresetTool.appendChild(opt);
    });
  }

  if (aiPresetSaveBtn) {
    aiPresetSaveBtn.addEventListener('click', savePreset);
  }

  document.addEventListener('htmx:afterSwap', (evt) => {
    if (aiPairBrowser && aiPairBrowser.contains(evt.target)) {
      updateSelectedFromMode();
    }
    if (aiAnyBrowser && aiAnyBrowser.contains(evt.target)) {
      updateSelectedFromMode();
    }
  });

  loadPresets();
  updateToolButtons();
})();
