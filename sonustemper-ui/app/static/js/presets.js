(function(){
  const page = document.getElementById('presetPage');
  if(!page) return;

  const referenceForm = document.getElementById('presetGenerateForm');
  const referenceFile = document.getElementById('referenceFile');
  const referenceName = document.getElementById('referenceName');
  const presetStatusList = document.getElementById('presetStatusList');
  const referenceGenerateBtn = document.getElementById('referenceGenerateBtn');
  const generateVoicing = document.getElementById('generateVoicing');
  const generateProfile = document.getElementById('generateProfile');

  const presetJsonFile = document.getElementById('presetJsonFile');
  const presetJsonName = document.getElementById('presetJsonName');
  const uploadPresetJsonBtn = document.getElementById('uploadPresetJsonBtn');
  const builtinProfileSelect = document.getElementById('builtinProfileSelect');
  const builtinVoicingSelect = document.getElementById('builtinVoicingSelect');
  const duplicateBuiltinProfileBtn = document.getElementById('duplicateBuiltinProfileBtn');
  const duplicateBuiltinVoicingBtn = document.getElementById('duplicateBuiltinVoicingBtn');

  const detailTitle = document.getElementById('presetDetailTitle');
  const detailKind = document.getElementById('presetDetailKind');
  const detailSummary = document.getElementById('presetDetailSummary');
  const detailMeta = document.getElementById('presetDetailMeta');
  const detailSubtitle = document.getElementById('presetDetailSubtitle');
  const detailHint = document.getElementById('presetDetailHint');
  const detailVoicing = document.getElementById('presetDetailVoicing');
  const detailProfile = document.getElementById('presetDetailProfile');
  const voicingStats = document.getElementById('presetVoicingStats');
  const voicingEq = document.getElementById('presetVoicingEq');
  const voicingEditor = document.getElementById('presetVoicingEditor');
  const voicingSaveBtn = document.getElementById('presetVoicingSaveBtn');
  const profileStats = document.getElementById('presetProfileStats');
  const profileEditor = document.getElementById('presetProfileEditor');
  const profileSaveBtn = document.getElementById('presetProfileSaveBtn');
  const eqPreview = document.getElementById('presetEqPreview');

  const selectedHint = document.getElementById('presetSelectedHint');
  const downloadBtn = document.getElementById('presetDownloadBtn');
  const moveBtn = document.getElementById('presetMoveBtn');
  const duplicateBtn = document.getElementById('presetDuplicateBtn');
  const deleteBtn = document.getElementById('presetDeleteBtn');

  let selectedItem = null;

  let statusLines = [];
  let statusRaf = null;

  function scheduleStatusRender(){
    if(statusRaf) cancelAnimationFrame(statusRaf);
    statusRaf = requestAnimationFrame(() => {
      if (!presetStatusList) return;
      presetStatusList.textContent = statusLines.length ? statusLines.join('\n') : '(waiting)';
      presetStatusList.scrollTop = presetStatusList.scrollHeight;
    });
  }

  function addStatusLine(message){
    const stamp = new Date().toLocaleTimeString();
    statusLines.push(`${stamp} ${message}`);
    if (statusLines.length > 120) statusLines = statusLines.slice(-120);
    scheduleStatusRender();
  }

  function refreshPresetBrowser(){
    const browser = document.getElementById('presetBrowser');
    if(!browser || !window.htmx) return;
    browser.querySelectorAll('.file-browser-list[data-endpoint]').forEach(list => {
      const endpoint = list.dataset.endpoint;
      if(endpoint){
        window.htmx.ajax('GET', endpoint, { target: list, swap: 'innerHTML' });
      }
    });
  }

  async function loadBuiltinPresets(){
    if(!builtinProfileSelect || !builtinVoicingSelect) return;
    builtinProfileSelect.innerHTML = '';
    builtinVoicingSelect.innerHTML = '';
    try{
      const res = await fetch('/api/library/builtins', { cache: 'no-store' });
      if(!res.ok) throw new Error('load_failed');
      const data = await res.json();
      const items = data.items || [];
      if(!items.length){
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No provided presets found';
        builtinProfileSelect.appendChild(opt.cloneNode(true));
        builtinVoicingSelect.appendChild(opt);
        if(duplicateBuiltinProfileBtn) duplicateBuiltinProfileBtn.disabled = true;
        if(duplicateBuiltinVoicingBtn) duplicateBuiltinVoicingBtn.disabled = true;
        return;
      }
      const profiles = items.filter(item => (item.kind || item.meta?.kind) === 'profile');
      const voicings = items.filter(item => (item.kind || item.meta?.kind) === 'voicing');
      const fillSelect = (select, list) => {
        select.innerHTML = '';
        if(!list.length){
          const opt = document.createElement('option');
          opt.value = '';
          opt.textContent = 'No provided presets found';
          select.appendChild(opt);
          return;
        }
        list.sort((a, b) => {
          const aTitle = a.meta?.title || a.title || a.name || '';
          const bTitle = b.meta?.title || b.title || b.name || '';
          return aTitle.localeCompare(bTitle);
        }).forEach(item => {
          const option = document.createElement('option');
          const itemId = item.id || item.name || '';
          option.value = itemId;
          option.textContent = item.meta?.title || item.title || itemId || 'Preset';
          select.appendChild(option);
        });
      };
      fillSelect(builtinProfileSelect, profiles);
      fillSelect(builtinVoicingSelect, voicings);
      if(duplicateBuiltinProfileBtn) duplicateBuiltinProfileBtn.disabled = !profiles.length;
      if(duplicateBuiltinVoicingBtn) duplicateBuiltinVoicingBtn.disabled = !voicings.length;
    }catch(_err){
      addStatusLine('Failed to load provided presets.');
      if(duplicateBuiltinProfileBtn) duplicateBuiltinProfileBtn.disabled = true;
      if(duplicateBuiltinVoicingBtn) duplicateBuiltinVoicingBtn.disabled = true;
    }
  }

  function syncActiveStates(){
    const browser = document.getElementById('presetBrowser');
    if(!browser) return;
    browser.querySelectorAll('.browser-item').forEach(btn => {
      const active = selectedItem && btn.dataset.id === selectedItem.id;
      btn.classList.toggle('active', Boolean(active));
    });
  }

  function formatNumber(value, digits){
    const num = Number(value);
    if(!Number.isFinite(num)) return null;
    return num.toFixed(digits ?? 1);
  }

  function renderEqPreview(eqBands){
    if(!eqPreview) return;
    const bands = Array.isArray(eqBands) ? eqBands : [];
    const w = eqPreview.viewBox?.baseVal?.width || eqPreview.clientWidth || 240;
    const h = eqPreview.viewBox?.baseVal?.height || eqPreview.clientHeight || 64;
    const padding = 8;
    const plotW = w - padding * 2;
    const plotH = h - padding * 2;
    const mid = h / 2;
    const samples = 96;
    const minF = 20;
    const maxF = 20000;
    const logStep = Math.log(maxF / minF);
    const range = bands.some(band => Math.abs(parseFloat(band?.gain_db ?? band?.gain ?? 0)) > 2.0) ? 8 : 6;
    const values = [];

    for(let i = 0; i < samples; i += 1){
      const t = i / (samples - 1);
      const f = minF * Math.exp(logStep * t);
      let y = 0;
      bands.forEach(band => {
        if(!band) return;
        const f0 = parseFloat(band.freq_hz ?? band.freq);
        if(!Number.isFinite(f0) || f0 <= 0) return;
        const gain = parseFloat(band.gain_db ?? band.gain ?? 0);
        if(!Number.isFinite(gain) || gain === 0) return;
        const q = parseFloat(band.q ?? 1.0);
        const qSafe = Math.max(q, 0.2);
        const x = Math.log2(f / f0);
        const type = String(band.type || '').toLowerCase();
        if(type === 'peaking' || type === 'peak' || type === 'bell'){
          const sigma = 0.55 / qSafe;
          y += gain * Math.exp(-(x * x) / (2 * sigma * sigma));
        }else if(type === 'highshelf'){
          const k = 6 * qSafe;
          const s = 1 / (1 + Math.exp(-k * x));
          y += gain * s;
        }else if(type === 'lowshelf'){
          const k = 6 * qSafe;
          const s = 1 / (1 + Math.exp(-k * x));
          y += gain * (1 - s);
        }
      });
      y = Math.max(-range, Math.min(range, y));
      values.push({ t, y });
    }

    const points = values.map((pt, idx) => {
      const x = padding + pt.t * plotW;
      const yPx = mid - (pt.y / range) * (plotH / 2);
      return `${idx === 0 ? 'M' : 'L'}${x.toFixed(2)},${yPx.toFixed(2)}`;
    }).join(' ');
    const path = points || `M${padding},${mid} L${w - padding},${mid}`;
    const grid = [
      { y: padding, cls: 'eq-grid' },
      { y: mid, cls: 'eq-grid eq-grid-mid' },
      { y: h - padding, cls: 'eq-grid' },
    ];
    const ticks = [100, 1000, 10000].map(freq => {
      const x = padding + (Math.log(freq / minF) / logStep) * plotW;
      return x;
    });
    const gridLines = grid.map(line => (
      `<line class="${line.cls}" x1="${padding}" y1="${line.y}" x2="${w - padding}" y2="${line.y}" />`
    )).join('');
    const tickLines = ticks.map(x => (
      `<line class="eq-grid eq-grid-vert" x1="${x.toFixed(2)}" y1="${padding}" x2="${x.toFixed(2)}" y2="${h - padding}" />`
    )).join('');
    const fillPath = `${path} L${w - padding},${mid} L${padding},${mid} Z`;
    eqPreview.innerHTML = `
      ${gridLines}
      ${tickLines}
      <path class="eq-fill" d="${fillPath}"></path>
      <path class="eq-curve" d="${path}"></path>
    `;
  }

  function makeProfileRow(labelText, inputEl){
    const row = document.createElement('div');
    row.className = 'preset-profile-row';
    const label = document.createElement('div');
    label.className = 'preset-profile-label';
    label.textContent = labelText;
    row.appendChild(label);
    row.appendChild(inputEl);
    return row;
  }

  function renderProfileEditor(item){
    if(!profileEditor) return;
    profileEditor.innerHTML = '';
    if(!item){
      if(profileSaveBtn) profileSaveBtn.disabled = true;
      return;
    }
    const meta = item.meta || {};
    const titleInput = document.createElement('input');
    titleInput.className = 'preset-profile-input';
    titleInput.type = 'text';
    titleInput.value = meta.title || item.title || '';
    titleInput.dataset.field = 'title';

    const lufsInput = document.createElement('input');
    lufsInput.className = 'preset-profile-input';
    lufsInput.type = 'number';
    lufsInput.step = '0.1';
    lufsInput.value = Number.isFinite(Number(meta.lufs)) ? Number(meta.lufs).toFixed(1) : '';
    lufsInput.dataset.field = 'lufs';

    const tpInput = document.createElement('input');
    tpInput.className = 'preset-profile-input';
    tpInput.type = 'number';
    tpInput.step = '0.1';
    tpInput.value = Number.isFinite(Number(meta.tp)) ? Number(meta.tp).toFixed(1) : '';
    tpInput.dataset.field = 'tpp';

    profileEditor.appendChild(makeProfileRow('Title', titleInput));
    profileEditor.appendChild(makeProfileRow('LUFS', lufsInput));
    profileEditor.appendChild(makeProfileRow('True Peak', tpInput));
    if(profileSaveBtn) profileSaveBtn.disabled = item.origin !== 'user';
  }

  function sanitizeInputValue(value){
    return String(value || '').replace(/\\s+/g, ' ').trim();
  }

  function makeVoicingRow(labelText, inputEl){
    const row = document.createElement('div');
    row.className = 'preset-voicing-row';
    const label = document.createElement('div');
    label.className = 'preset-voicing-label';
    label.textContent = labelText;
    row.appendChild(label);
    row.appendChild(inputEl);
    return row;
  }

  function renderVoicingEditor(item){
    if(!voicingEditor) return;
    voicingEditor.innerHTML = '';
    if(!item){
      if(voicingSaveBtn) voicingSaveBtn.disabled = true;
      return;
    }
    const meta = item.meta || {};
    const widthValue = meta.width ?? meta.stereo?.width;
    const dynamics = meta.dynamics || {};
    const densityValue = dynamics.density;
    const transientValue = dynamics.transient_focus;
    const smoothValue = dynamics.smoothness;

    const widthInput = document.createElement('input');
    widthInput.className = 'preset-voicing-input';
    widthInput.type = 'number';
    widthInput.step = '0.01';
    widthInput.value = Number.isFinite(Number(widthValue)) ? Number(widthValue).toFixed(2) : '';
    widthInput.dataset.field = 'width';

    const densityInput = document.createElement('input');
    densityInput.className = 'preset-voicing-input';
    densityInput.type = 'number';
    densityInput.step = '0.01';
    densityInput.value = Number.isFinite(Number(densityValue)) ? Number(densityValue).toFixed(2) : '';
    densityInput.dataset.field = 'density';

    const transientInput = document.createElement('input');
    transientInput.className = 'preset-voicing-input';
    transientInput.type = 'number';
    transientInput.step = '0.01';
    transientInput.value = Number.isFinite(Number(transientValue)) ? Number(transientValue).toFixed(2) : '';
    transientInput.dataset.field = 'transient_focus';

    const smoothInput = document.createElement('input');
    smoothInput.className = 'preset-voicing-input';
    smoothInput.type = 'number';
    smoothInput.step = '0.01';
    smoothInput.value = Number.isFinite(Number(smoothValue)) ? Number(smoothValue).toFixed(2) : '';
    smoothInput.dataset.field = 'smoothness';

    voicingEditor.appendChild(makeVoicingRow('Width', widthInput));
    voicingEditor.appendChild(makeVoicingRow('Density', densityInput));
    voicingEditor.appendChild(makeVoicingRow('Transient', transientInput));
    voicingEditor.appendChild(makeVoicingRow('Smoothness', smoothInput));
    if(voicingSaveBtn) voicingSaveBtn.disabled = item.origin !== 'user';
  }

  async function saveProfileEdits(){
    if(!selectedItem || selectedItem.kind !== 'profile' || selectedItem.origin !== 'user') return;
    if(!profileEditor) return;
    const fields = {};
    const title = sanitizeInputValue(profileEditor.querySelector('[data-field=\"title\"]')?.value || '');
    if(!title){
      addStatusLine('Title is required.');
      return;
    }
    fields.title = title;

    const lufsRaw = profileEditor.querySelector('[data-field=\"lufs\"]')?.value || '';
    const lufs = Number(lufsRaw);
    if(!Number.isFinite(lufs) || lufs < -60 || lufs > 0){
      addStatusLine('LUFS must be between -60 and 0.');
      return;
    }
    fields.lufs = lufs;

    const tpRaw = profileEditor.querySelector('[data-field=\"tpp\"]')?.value || '';
    const tpp = Number(tpRaw);
    if(!Number.isFinite(tpp) || tpp < -20 || tpp > 2){
      addStatusLine('True Peak must be between -20 and 2 dBTP.');
      return;
    }
    fields.tpp = tpp;

    try{
      const res = await fetch('/api/library/item/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: 'profile',
          origin: 'user',
          fields,
        }),
      });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'update_failed');
      }
      const data = await res.json();
      setSelectedItem(data.item || null);
      refreshPresetBrowser();
      addStatusLine('Profile saved.');
    }catch(_err){
      addStatusLine('Profile save failed.');
    }
  }

  async function saveVoicingEdits(){
    if(!selectedItem || selectedItem.kind !== 'voicing' || selectedItem.origin !== 'user') return;
    if(!voicingEditor) return;
    const readNum = (field) => {
      const raw = voicingEditor.querySelector(`[data-field="${field}"]`)?.value || '';
      if(!raw) return null;
      const num = Number(raw);
      return Number.isFinite(num) ? num : NaN;
    };
    const width = readNum('width');
    if(width !== null && (!Number.isFinite(width) || width < 0.5 || width > 2.0)){
      addStatusLine('Width must be between 0.50 and 2.00.');
      return;
    }
    const density = readNum('density');
    if(density !== null && (!Number.isFinite(density) || density < 0 || density > 1)){
      addStatusLine('Density must be between 0 and 1.');
      return;
    }
    const transient = readNum('transient_focus');
    if(transient !== null && (!Number.isFinite(transient) || transient < 0 || transient > 1)){
      addStatusLine('Transient must be between 0 and 1.');
      return;
    }
    const smoothness = readNum('smoothness');
    if(smoothness !== null && (!Number.isFinite(smoothness) || smoothness < 0 || smoothness > 1)){
      addStatusLine('Smoothness must be between 0 and 1.');
      return;
    }
    try{
      const res = await fetch('/api/library/item/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: 'voicing',
          origin: 'user',
          fields: {
            width,
            density,
            transient_focus: transient,
            smoothness,
          },
        }),
      });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'update_failed');
      }
      const data = await res.json();
      setSelectedItem(data.item || null);
      refreshPresetBrowser();
      addStatusLine('Voicing saved.');
    }catch(_err){
      addStatusLine('Voicing save failed.');
    }
  }

  function updateDetail(){
    if(!detailTitle || !detailMeta) return;
    if(!selectedItem){
      detailTitle.textContent = 'No item selected';
      detailSubtitle.textContent = 'Choose a voicing or profile from the library.';
      detailKind.hidden = true;
      detailSummary.textContent = '';
      detailMeta.innerHTML = '<div><span class="muted">Source:</span> -</div>' +
        '<div><span class="muted">Created:</span> -</div>' +
        '<div><span class="muted">Type:</span> -</div>';
      if(detailVoicing) detailVoicing.hidden = true;
      if(detailProfile) detailProfile.hidden = true;
      if(voicingEq) voicingEq.innerHTML = '';
      renderProfileEditor(null);
      renderVoicingEditor(null);
      if(downloadBtn) downloadBtn.disabled = true;
      if(moveBtn) moveBtn.disabled = true;
      if(duplicateBtn) duplicateBtn.disabled = true;
      if(deleteBtn) deleteBtn.disabled = true;
      if(detailHint) detailHint.textContent = 'Select an item to view details.';
      if(selectedHint) selectedHint.textContent = 'Select a voicing or profile to view details.';
      return;
    }

    const kindLabel = selectedItem.kind === 'voicing' ? 'Voicing' : 'Profile';
    const originLabel = selectedItem.origin === 'staging' ? 'Staging' : 'User';
    const sourceLabel = selectedItem.meta?.source || originLabel.toLowerCase();
    detailTitle.textContent = selectedItem.title || selectedItem.id;
    detailSubtitle.textContent = `${originLabel} ${kindLabel}`;
    if(detailKind){
      detailKind.textContent = kindLabel;
      detailKind.className = `badge badge-${selectedItem.kind === 'voicing' ? 'voicing' : 'profile'}`;
      detailKind.hidden = false;
    }
    detailMeta.innerHTML = `<div><span class="muted">Source:</span> ${sourceLabel || '-'}</div>` +
      `<div><span class="muted">Created:</span> ${selectedItem.meta?.created_at || '-'}</div>` +
      `<div><span class="muted">Type:</span> ${kindLabel}</div>`;

    if(selectedItem.kind === 'voicing'){
      const tags = Array.isArray(selectedItem.meta?.tags) ? selectedItem.meta.tags : [];
      detailSummary.textContent = tags[0] || 'No description available.';
      if(detailVoicing) detailVoicing.hidden = false;
      if(detailProfile) detailProfile.hidden = true;
      const width = Number.isFinite(Number(selectedItem.meta?.width))
        ? Number(selectedItem.meta.width)
        : Number(selectedItem.meta?.stereo?.width);
      const dynamics = selectedItem.meta?.dynamics || {};
      if(voicingStats){
        voicingStats.innerHTML = '';
        const parts = [];
        if(Number.isFinite(width)){
          parts.push({ label: `Width: ${width.toFixed(2)}` });
        }
        if(Number.isFinite(Number(dynamics.density))){
          parts.push({ label: `Density: ${Number(dynamics.density).toFixed(2)}` });
        }
        if(Number.isFinite(Number(dynamics.transient_focus))){
          parts.push({ label: `Transient: ${Number(dynamics.transient_focus).toFixed(2)}` });
        }
        if(Number.isFinite(Number(dynamics.smoothness))){
          parts.push({ label: `Smoothness: ${Number(dynamics.smoothness).toFixed(2)}` });
        }
        if(!parts.length){
          parts.push({ label: 'No voicing stats available' });
        }
        parts.forEach(part => {
          const span = document.createElement('span');
          span.className = 'badge';
          span.textContent = part.label;
          voicingStats.appendChild(span);
        });
      }
      if(voicingEq){
        const bands = Array.isArray(selectedItem.meta?.eq) ? selectedItem.meta.eq : [];
        voicingEq.innerHTML = '';
        if(!bands.length){
          const empty = document.createElement('div');
          empty.className = 'preset-eq-row';
          const msg = document.createElement('span');
          msg.className = 'muted';
          msg.textContent = 'No EQ bands defined.';
          empty.appendChild(msg);
          voicingEq.appendChild(empty);
        }else{
          const header = document.createElement('div');
          header.className = 'preset-eq-row preset-eq-header';
          ['Type', 'Freq', 'Gain', 'Q'].forEach(text => {
            const cell = document.createElement('div');
            cell.textContent = text;
            header.appendChild(cell);
          });
          voicingEq.appendChild(header);
          bands.forEach(band => {
            const row = document.createElement('div');
            row.className = 'preset-eq-row';
            const type = String(band?.type || band?.filter || 'band');
            const freq = Number.isFinite(Number(band?.freq_hz ?? band?.freq)) ? `${Number(band.freq_hz ?? band.freq).toFixed(0)} Hz` : '-';
            const gain = Number.isFinite(Number(band?.gain_db ?? band?.gain)) ? `${Number(band.gain_db ?? band.gain).toFixed(1)} dB` : '0.0 dB';
            const q = Number.isFinite(Number(band?.q)) ? Number(band.q).toFixed(2) : '1.00';
            [type, freq, gain, q].forEach(text => {
              const cell = document.createElement('div');
              cell.textContent = text;
              row.appendChild(cell);
            });
            voicingEq.appendChild(row);
          });
        }
      }
      renderVoicingEditor(selectedItem);
      renderEqPreview(selectedItem.meta?.eq || []);
    }else{
      const lufs = formatNumber(selectedItem.meta?.lufs, 1);
      const tp = formatNumber(selectedItem.meta?.tp, 1);
      const parts = [];
      if(lufs) parts.push(`${lufs} LUFS`);
      if(tp) parts.push(`${tp} dBTP`);
      detailSummary.textContent = parts.length ? `Target: ${parts.join(' / ')}` : 'No loudness target available.';
      if(detailVoicing) detailVoicing.hidden = true;
      if(detailProfile) detailProfile.hidden = false;
      if(profileStats){
        profileStats.innerHTML = '';
        const statParts = [];
        if(lufs) statParts.push({ label: `LUFS: ${lufs}` });
        if(tp) statParts.push({ label: `TP: ${tp}` });
        if(selectedItem.meta?.category){
          statParts.push({ label: `Category: ${selectedItem.meta.category}` });
        }
        if(!statParts.length){
          statParts.push({ label: 'No profile stats available' });
        }
        statParts.forEach(part => {
          const span = document.createElement('span');
          span.className = 'badge';
          span.textContent = part.label;
          profileStats.appendChild(span);
        });
      }
      if(voicingEq) voicingEq.innerHTML = '';
      renderProfileEditor(selectedItem);
      renderVoicingEditor(null);
      renderEqPreview([]);
    }

    if(downloadBtn) downloadBtn.disabled = false;
    if(moveBtn){
      moveBtn.disabled = selectedItem.origin !== 'staging';
      moveBtn.textContent = selectedItem.kind === 'voicing' ? 'Move to User Voicings' : 'Move to User Profiles';
    }
    if(duplicateBtn) duplicateBtn.disabled = selectedItem.origin !== 'user';
    if(deleteBtn) deleteBtn.disabled = !(selectedItem.origin === 'user' || selectedItem.origin === 'staging');
    if(detailHint){
      detailHint.textContent = selectedItem.origin === 'staging'
        ? 'Move to User to store this item in your library.'
        : 'Download, duplicate, or delete this item.';
    }
    if(selectedHint) selectedHint.textContent = `Selected: ${selectedItem.title || selectedItem.id}`;
  }

  function setSelectedItem(item){
    selectedItem = item;
    updateDetail();
    syncActiveStates();
  }

  function parseItemFromButton(btn){
    if(!btn) return null;
    let meta = {};
    if(btn.dataset.meta){
      try{ meta = JSON.parse(btn.dataset.meta); }catch(_err){ meta = {}; }
    }
    const titleEl = btn.querySelector('.browser-item-title');
    const title = titleEl ? titleEl.textContent.trim() : (meta.title || meta.name || meta.id || btn.dataset.id);
    const kind = (meta.kind || 'profile').toLowerCase();
    const origin = (meta.origin || 'user').toLowerCase();
    return {
      id: meta.id || meta.name || btn.dataset.id,
      title,
      kind,
      origin: origin === 'generated' ? 'staging' : origin,
      meta,
    };
  }

  async function handleGenerate(){
    const file = referenceFile?.files?.[0];
    if(!file){
      addStatusLine('Select an audio file.');
      return;
    }
    const wantsVoicing = Boolean(generateVoicing?.checked);
    const wantsProfile = Boolean(generateProfile?.checked);
    if(!wantsVoicing && !wantsProfile){
      addStatusLine('Select at least one item to generate.');
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('base_name', (referenceName?.value || '').trim());
    fd.append('generate_voicing', wantsVoicing ? 'true' : 'false');
    fd.append('generate_profile', wantsProfile ? 'true' : 'false');
    addStatusLine('Reference upload started.');
    try{
      const res = await fetch('/api/generate_from_reference', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Generate failed');
      }
      const data = await res.json();
      const createdCount = Array.isArray(data.items) ? data.items.length : 0;
      addStatusLine(createdCount ? `Generated ${createdCount} item(s).` : 'Generated.');
      if(referenceFile) referenceFile.value = '';
      refreshPresetBrowser();
    }catch(_err){
      addStatusLine('Generate failed.');
    }
  }

  async function handleUploadJson(){
    const file = presetJsonFile?.files?.[0];
    if(!file){
      addStatusLine('Select a JSON file.');
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('name', (presetJsonName?.value || '').trim());
    addStatusLine('Import started.');
    try{
      const res = await fetch('/api/import_json_to_staging', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Upload failed');
      }
      addStatusLine('Imported to staging.');
      if(presetJsonFile) presetJsonFile.value = '';
      refreshPresetBrowser();
    }catch(_err){
      addStatusLine('Import failed.');
    }
  }

  function downloadPreset(){
    if(!selectedItem) return;
    const params = new URLSearchParams({
      id: selectedItem.id,
      kind: selectedItem.kind,
      origin: selectedItem.origin,
    });
    window.location.href = `/api/library/item/download?${params.toString()}`;
  }

  async function deletePreset(){
    if(!selectedItem) return;
    if(!confirm(`Delete ${selectedItem.kind} "${selectedItem.title}"?`)) return;
    try{
      const res = await fetch('/api/library/item', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: selectedItem.kind,
          origin: selectedItem.origin,
        }),
      });
      if(!res.ok) throw new Error('delete_failed');
      setSelectedItem(null);
      refreshPresetBrowser();
      addStatusLine('Item deleted.');
    }catch(_err){
      addStatusLine('Delete failed.');
    }
  }

  async function moveToUser(){
    if(!selectedItem || selectedItem.origin !== 'staging') return;
    try{
      const res = await fetch('/api/staging/move_to_user', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: selectedItem.id, kind: selectedItem.kind }),
      });
      if(!res.ok) throw new Error('move_failed');
      const data = await res.json();
      setSelectedItem(data.item || null);
      refreshPresetBrowser();
      addStatusLine('Moved to user library.');
    }catch(_err){
      addStatusLine('Move failed.');
    }
  }

  async function duplicateSelected(){
    if(!selectedItem || selectedItem.origin !== 'user') return;
    const newName = prompt(`New ${selectedItem.kind} name`, `${selectedItem.title}-copy`);
    if(!newName) return;
    try{
      const res = await fetch('/api/library/duplicate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: selectedItem.kind,
          origin: 'user',
          name: newName,
        }),
      });
      if(!res.ok) throw new Error('duplicate_failed');
      const data = await res.json();
      setSelectedItem(data.item || null);
      refreshPresetBrowser();
      addStatusLine('Item duplicated.');
    }catch(_err){
      addStatusLine('Duplicate failed.');
    }
  }

  async function duplicateBuiltin(kind){
    const select = kind === 'voicing' ? builtinVoicingSelect : builtinProfileSelect;
    if(!select) return;
    const id = select.value;
    if(!id) return;
    const newName = prompt(`New ${kind} name`, `${id}-copy`);
    if(!newName) return;
    try{
      const res = await fetch('/api/library/duplicate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id,
          kind,
          origin: 'builtin',
          name: newName,
        }),
      });
      if(!res.ok) throw new Error('duplicate_failed');
      addStatusLine(`Duplicated provided ${kind}.`);
      refreshPresetBrowser();
    }catch(_err){
      addStatusLine('Duplicate failed.');
    }
  }

  document.addEventListener('click', (evt)=>{
    const btn = evt.target.closest('.file-browser .browser-item');
    if(!btn) return;
    if(btn.disabled) return;
    const item = parseItemFromButton(btn);
    if(item) setSelectedItem(item);
  });

  document.addEventListener('DOMContentLoaded', ()=>{
    if(referenceForm) referenceForm.addEventListener('submit', (e)=>{ e.preventDefault(); });
    if(referenceGenerateBtn) referenceGenerateBtn.addEventListener('click', handleGenerate);
    if(uploadPresetJsonBtn) uploadPresetJsonBtn.addEventListener('click', handleUploadJson);
    if(downloadBtn) downloadBtn.addEventListener('click', downloadPreset);
    if(moveBtn) moveBtn.addEventListener('click', moveToUser);
    if(duplicateBtn) duplicateBtn.addEventListener('click', duplicateSelected);
    if(deleteBtn) deleteBtn.addEventListener('click', deletePreset);
    if(duplicateBuiltinProfileBtn) duplicateBuiltinProfileBtn.addEventListener('click', ()=> duplicateBuiltin('profile'));
    if(duplicateBuiltinVoicingBtn) duplicateBuiltinVoicingBtn.addEventListener('click', ()=> duplicateBuiltin('voicing'));
    if(profileSaveBtn) profileSaveBtn.addEventListener('click', saveProfileEdits);
    if(voicingSaveBtn) voicingSaveBtn.addEventListener('click', saveVoicingEdits);
    updateDetail();
    loadBuiltinPresets();
    scheduleStatusRender();
  });

  document.addEventListener('htmx:afterSwap', (evt)=>{
    const browser = document.getElementById('presetBrowser');
    if(browser && browser.contains(evt.target)){
      syncActiveStates();
    }
  });
})();
