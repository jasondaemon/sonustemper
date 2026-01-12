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
  const voicingModeSimpleBtn = document.getElementById('presetVoicingModeSimple');
  const voicingModeAdvancedBtn = document.getElementById('presetVoicingModeAdvanced');
  const voicingMacro = document.getElementById('presetVoicingMacro');
  const voicingAdvancedWrap = document.getElementById('presetVoicingAdvancedWrap');
  const voicingAddBandBtn = document.getElementById('presetVoicingAddBandBtn');

  const selectedHint = document.getElementById('presetSelectedHint');
  const downloadBtn = document.getElementById('presetDownloadBtn');
  const moveBtn = document.getElementById('presetMoveBtn');
  const duplicateBtn = document.getElementById('presetDuplicateBtn');
  const deleteBtn = document.getElementById('presetDeleteBtn');

  let selectedItem = null;
  let selectedVoicingFull = null;
  let voicingMode = 'simple';
  let voicingLoadToken = 0;
  let profileBaseline = null;
  let voicingBaseline = null;
  let voicingEqBaseline = null;

  let statusLines = [];
  let statusRaf = null;
  const EQ_TYPES = ['lowshelf', 'highshelf', 'peaking', 'highpass', 'lowpass', 'bandpass', 'notch'];
  const EQ_LABELS = {
    lowshelf: 'Low Shelf',
    highshelf: 'High Shelf',
    peaking: 'Peaking',
    highpass: 'High Pass',
    lowpass: 'Low Pass',
    bandpass: 'Band Pass',
    notch: 'Notch',
  };
  const MACRO_SLOTS = [
    {
      key: 'low',
      label: 'Low',
      type: 'lowshelf',
      defaultFreq: 85,
      defaultQ: 0.8,
      choices: [60, 80, 100, 120],
    },
    {
      key: 'low_mid',
      label: 'Low-mid',
      type: 'peaking',
      defaultFreq: 220,
      defaultQ: 1.0,
      choices: [180, 220, 300, 350],
    },
    {
      key: 'presence',
      label: 'Presence',
      type: 'peaking',
      defaultFreq: 3800,
      defaultQ: 1.1,
      choices: [2500, 3200, 3800, 4500],
    },
    {
      key: 'air',
      label: 'Air',
      type: 'highshelf',
      defaultFreq: 11000,
      defaultQ: 0.7,
      choices: [8000, 10000, 11000, 14000],
    },
  ];

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

  function readNumericInput(value){
    const raw = String(value ?? '').trim();
    if(!raw) return null;
    const num = Number(raw);
    return Number.isFinite(num) ? num : NaN;
  }

  function numericEqual(a, b){
    if(a === null && b === null) return true;
    if(!Number.isFinite(a) || !Number.isFinite(b)) return false;
    return Math.abs(a - b) < 0.001;
  }

  function clampValue(value, min, max){
    if(!Number.isFinite(value)) return value;
    return Math.min(Math.max(value, min), max);
  }

  function ensureVoicingShape(data){
    if(!data || typeof data !== 'object') return null;
    if(!data.meta || typeof data.meta !== 'object') data.meta = {};
    if(!data.chain || typeof data.chain !== 'object') data.chain = {};
    if(!Array.isArray(data.chain.eq)) data.chain.eq = [];
    if(!data.chain.stereo || typeof data.chain.stereo !== 'object') data.chain.stereo = {};
    if(!data.chain.dynamics || typeof data.chain.dynamics !== 'object') data.chain.dynamics = {};
    return data;
  }

  function voicingDataFromItem(item){
    if(!item) return null;
    const meta = item.meta && typeof item.meta === 'object' ? { ...item.meta } : {};
    const chain = {};
    if(Array.isArray(meta.eq)){
      chain.eq = meta.eq.map(band => ({
        type: band?.type,
        freq_hz: band?.freq_hz ?? band?.freq,
        gain_db: band?.gain_db ?? band?.gain,
        q: band?.q,
      }));
    } else {
      chain.eq = [];
    }
    const stereoWidth = meta.width ?? meta.stereo?.width;
    chain.stereo = {};
    if(Number.isFinite(Number(stereoWidth))){
      chain.stereo.width = Number(stereoWidth);
    }
    chain.dynamics = { ...(meta.dynamics || {}) };
    return ensureVoicingShape({ id: item.id, meta, chain });
  }

  function getVoicingEqList(){
    if(!selectedVoicingFull || !selectedVoicingFull.chain) return [];
    const eq = selectedVoicingFull.chain.eq;
    if(!Array.isArray(eq)) return [];
    return eq;
  }

  function setVoicingMode(mode){
    const nextMode = mode === 'advanced' ? 'advanced' : 'simple';
    voicingMode = nextMode;
    if(voicingModeSimpleBtn) voicingModeSimpleBtn.classList.toggle('is-active', nextMode === 'simple');
    if(voicingModeAdvancedBtn) voicingModeAdvancedBtn.classList.toggle('is-active', nextMode === 'advanced');
    if(voicingMacro) voicingMacro.classList.toggle('is-hidden', nextMode !== 'simple');
    if(voicingAdvancedWrap) voicingAdvancedWrap.classList.toggle('is-hidden', nextMode !== 'advanced');
    if(nextMode === 'simple'){
      renderVoicingMacro();
    }else{
      renderVoicingAdvanced();
    }
    if(selectedVoicingFull){
      renderEqPreview(getVoicingEqList());
    }
    updateSaveButtonStates();
  }

  function syncVoicingModeButtons(){
    if(!voicingModeSimpleBtn || !voicingModeAdvancedBtn) return;
    voicingModeSimpleBtn.classList.toggle('is-active', voicingMode === 'simple');
    voicingModeAdvancedBtn.classList.toggle('is-active', voicingMode === 'advanced');
  }

  function profileStateFromEditor(){
    if(!profileEditor) return null;
    return {
      title: sanitizeInputValue(profileEditor.querySelector('[data-field="title"]')?.value || ''),
      lufs: readNumericInput(profileEditor.querySelector('[data-field="lufs"]')?.value || ''),
      tpp: readNumericInput(profileEditor.querySelector('[data-field="tpp"]')?.value || ''),
      category: sanitizeInputValue(profileEditor.querySelector('[data-field="category"]')?.value || ''),
      order: readNumericInput(profileEditor.querySelector('[data-field="order"]')?.value || ''),
    };
  }

  function voicingStateFromEditor(){
    if(!voicingEditor) return null;
    return {
      width: readNumericInput(voicingEditor.querySelector('[data-field="width"]')?.value || ''),
      density: readNumericInput(voicingEditor.querySelector('[data-field="density"]')?.value || ''),
      transient_focus: readNumericInput(voicingEditor.querySelector('[data-field="transient_focus"]')?.value || ''),
      smoothness: readNumericInput(voicingEditor.querySelector('[data-field="smoothness"]')?.value || ''),
    };
  }

  function voicingEqStateFromEditor(){
    if(selectedVoicingFull && Array.isArray(selectedVoicingFull.chain?.eq)){
      return selectedVoicingFull.chain.eq.map(band => ({
        type: normalizeEqType(band?.type),
        freq: readNumericInput(band?.freq_hz ?? band?.freq ?? ''),
        gain: readNumericInput(band?.gain_db ?? band?.gain ?? ''),
        q: readNumericInput(band?.q ?? ''),
      }));
    }
    if(!voicingEq) return null;
    const rows = voicingEq.querySelectorAll('.preset-eq-row[data-type]');
    return Array.from(rows).map(row => ({
      type: normalizeEqType(row.dataset.type),
      freq: readNumericInput(row.querySelector('[data-field="freq"]')?.value || ''),
      gain: readNumericInput(row.querySelector('[data-field="gain"]')?.value || ''),
      q: readNumericInput(row.querySelector('[data-field="q"]')?.value || ''),
    }));
  }

  function profileStatesEqual(a, b){
    if(!a || !b) return false;
    return a.title === b.title &&
      numericEqual(a.lufs, b.lufs) &&
      numericEqual(a.tpp, b.tpp) &&
      a.category === b.category &&
      numericEqual(a.order, b.order);
  }

  function voicingStatesEqual(a, b){
    if(!a || !b) return false;
    return numericEqual(a.width, b.width) &&
      numericEqual(a.density, b.density) &&
      numericEqual(a.transient_focus, b.transient_focus) &&
      numericEqual(a.smoothness, b.smoothness);
  }

  function voicingEqStatesEqual(a, b){
    if(!a || !b) return false;
    if(a.length !== b.length) return false;
    for(let i = 0; i < a.length; i += 1){
      const left = a[i];
      const right = b[i];
      if(left.type !== right.type) return false;
      if(!numericEqual(left.freq, right.freq)) return false;
      if(!numericEqual(left.gain, right.gain)) return false;
      if(!numericEqual(left.q, right.q)) return false;
    }
    return true;
  }

  function setProfileBaselineFromEditor(){
    if(!selectedItem || selectedItem.kind !== 'profile'){
      profileBaseline = null;
      return;
    }
    profileBaseline = profileStateFromEditor();
  }

  function setVoicingBaselineFromEditor(){
    if(!selectedItem || selectedItem.kind !== 'voicing'){
      voicingBaseline = null;
      voicingEqBaseline = null;
      return;
    }
    voicingBaseline = voicingStateFromEditor();
    voicingEqBaseline = voicingEqStateFromEditor();
  }

  function updateSaveButtonStates(){
    if(profileSaveBtn){
      const canEdit = Boolean(selectedItem && selectedItem.kind === 'profile' && (selectedItem.origin === 'user' || selectedItem.origin === 'staging'));
      const current = canEdit ? profileStateFromEditor() : null;
      const dirty = Boolean(canEdit && profileBaseline && current && !profileStatesEqual(current, profileBaseline));
      profileSaveBtn.disabled = !canEdit;
      profileSaveBtn.classList.toggle('dirty', dirty && canEdit);
    }
    if(voicingSaveBtn){
      const canEdit = Boolean(selectedItem && selectedItem.kind === 'voicing' && (selectedItem.origin === 'user' || selectedItem.origin === 'staging'));
      const currentVoicing = canEdit ? voicingStateFromEditor() : null;
      const currentEq = canEdit ? voicingEqStateFromEditor() : null;
      const voicingDirty = Boolean(canEdit && voicingBaseline && currentVoicing && !voicingStatesEqual(currentVoicing, voicingBaseline));
      const eqDirty = Boolean(canEdit && voicingEqBaseline && currentEq && !voicingEqStatesEqual(currentEq, voicingEqBaseline));
      const dirty = voicingDirty || eqDirty;
      voicingSaveBtn.disabled = !canEdit;
      voicingSaveBtn.classList.toggle('dirty', dirty && canEdit);
    }
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

  function normalizeEqType(value){
    return String(value || '').toLowerCase().trim();
  }

  function eqTypeLabel(type){
    return EQ_LABELS[type] || type || 'Band';
  }

  function renderVoicingEqEditor(bands, options = {}){
    if(!voicingEq) return;
    const items = Array.isArray(bands) ? bands : [];
    const canEdit = Boolean(options.canEdit);

    voicingEq.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'preset-eq-row preset-eq-header';
    ['Type', 'Freq', 'Gain', 'Q', ''].forEach(text => {
      const cell = document.createElement('div');
      cell.textContent = text;
      header.appendChild(cell);
    });
    voicingEq.appendChild(header);

    if(!items.length){
      const emptyRow = document.createElement('div');
      emptyRow.className = 'preset-eq-row';
      const cell = document.createElement('div');
      cell.textContent = 'No EQ bands yet.';
      cell.style.gridColumn = '1 / -1';
      cell.className = 'muted';
      emptyRow.appendChild(cell);
      voicingEq.appendChild(emptyRow);
      return;
    }

    items.forEach((rowData, index) => {
      const row = document.createElement('div');
      row.className = 'preset-eq-row';
      row.dataset.type = normalizeEqType(rowData?.type);
      row.dataset.index = String(index);

      const typeCell = document.createElement('div');
      typeCell.textContent = eqTypeLabel(row.dataset.type);

      const freqInput = document.createElement('input');
      freqInput.className = 'preset-eq-input';
      freqInput.type = 'number';
      freqInput.step = '1';
      freqInput.min = '20';
      freqInput.max = '20000';
      freqInput.placeholder = 'Hz';
      if (Number.isFinite(Number(rowData?.freq_hz ?? rowData?.freq))) {
        freqInput.value = Number(rowData.freq_hz ?? rowData.freq).toFixed(0);
      }
      freqInput.dataset.field = 'freq';
      freqInput.disabled = !canEdit;

      const gainInput = document.createElement('input');
      gainInput.className = 'preset-eq-input';
      gainInput.type = 'number';
      gainInput.step = '0.1';
      gainInput.min = '-6';
      gainInput.max = '6';
      gainInput.placeholder = 'dB';
      if (Number.isFinite(Number(rowData?.gain_db ?? rowData?.gain))) {
        gainInput.value = Number(rowData.gain_db ?? rowData.gain).toFixed(1);
      }
      gainInput.dataset.field = 'gain';
      gainInput.disabled = !canEdit;

      const qInput = document.createElement('input');
      qInput.className = 'preset-eq-input';
      qInput.type = 'number';
      qInput.step = '0.01';
      qInput.min = '0.3';
      qInput.max = '4';
      qInput.placeholder = 'Q';
      if (Number.isFinite(Number(rowData?.q))) {
        qInput.value = Number(rowData.q).toFixed(2);
      }
      qInput.dataset.field = 'q';
      qInput.disabled = !canEdit;

      const actionCell = document.createElement('div');
      actionCell.className = 'preset-eq-action';
      const deleteBtn = document.createElement('button');
      deleteBtn.className = 'btn tiny ghost';
      deleteBtn.type = 'button';
      deleteBtn.textContent = 'Delete';
      deleteBtn.disabled = !canEdit;
      deleteBtn.dataset.action = 'delete-eq';
      actionCell.appendChild(deleteBtn);

      row.appendChild(typeCell);
      row.appendChild(freqInput);
      row.appendChild(gainInput);
      row.appendChild(qInput);
      row.appendChild(actionCell);
      voicingEq.appendChild(row);
    });
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
    const canEdit = item.origin === 'user' || item.origin === 'staging';
    const meta = item.meta || {};
    const titleInput = document.createElement('input');
    titleInput.className = 'preset-profile-input';
    titleInput.type = 'text';
    titleInput.value = meta.title || item.title || '';
    titleInput.dataset.field = 'title';
    titleInput.disabled = !canEdit;

    const lufsInput = document.createElement('input');
    lufsInput.className = 'preset-profile-input';
    lufsInput.type = 'number';
    lufsInput.step = '0.1';
    lufsInput.value = Number.isFinite(Number(meta.lufs)) ? Number(meta.lufs).toFixed(1) : '';
    lufsInput.dataset.field = 'lufs';
    lufsInput.disabled = !canEdit;

    const tpInput = document.createElement('input');
    tpInput.className = 'preset-profile-input';
    tpInput.type = 'number';
    tpInput.step = '0.1';
    const tpValue = Number.isFinite(Number(meta.tp)) ? Number(meta.tp) : (Number.isFinite(Number(meta.tpp)) ? Number(meta.tpp) : null);
    tpInput.value = Number.isFinite(tpValue) ? tpValue.toFixed(1) : '';
    tpInput.dataset.field = 'tpp';
    tpInput.disabled = !canEdit;

    const categoryInput = document.createElement('input');
    categoryInput.className = 'preset-profile-input';
    categoryInput.type = 'text';
    categoryInput.value = meta.category || '';
    categoryInput.dataset.field = 'category';
    categoryInput.disabled = !canEdit;

    const orderInput = document.createElement('input');
    orderInput.className = 'preset-profile-input';
    orderInput.type = 'number';
    orderInput.step = '1';
    orderInput.min = '0';
    orderInput.max = '9999';
    orderInput.value = Number.isFinite(Number(meta.order)) ? Number(meta.order).toString() : '';
    orderInput.dataset.field = 'order';
    orderInput.disabled = !canEdit;

    profileEditor.appendChild(makeProfileRow('Title', titleInput));
    profileEditor.appendChild(makeProfileRow('LUFS', lufsInput));
    profileEditor.appendChild(makeProfileRow('True Peak', tpInput));
    profileEditor.appendChild(makeProfileRow('Category', categoryInput));
    profileEditor.appendChild(makeProfileRow('Order', orderInput));
    if(profileSaveBtn) profileSaveBtn.disabled = !canEdit;
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
    const canEdit = item.origin === 'user' || item.origin === 'staging';
    const meta = item.meta || {};
    const chain = item.chain && typeof item.chain === 'object' ? item.chain : {};
    const stereo = chain.stereo && typeof chain.stereo === 'object' ? chain.stereo : (meta.stereo || {});
    const dynamics = chain.dynamics && typeof chain.dynamics === 'object' ? chain.dynamics : (meta.dynamics || {});
    const widthValue = stereo.width ?? meta.width;
    const densityValue = dynamics.density;
    const transientValue = dynamics.transient_focus;
    const smoothValue = dynamics.smoothness;

    const widthInput = document.createElement('input');
    widthInput.className = 'preset-voicing-input';
    widthInput.type = 'number';
    widthInput.step = '0.01';
    widthInput.min = '0.9';
    widthInput.max = '1.1';
    widthInput.value = Number.isFinite(Number(widthValue)) ? Number(widthValue).toFixed(2) : '';
    widthInput.dataset.field = 'width';
    widthInput.disabled = !canEdit;

    const densityInput = document.createElement('input');
    densityInput.className = 'preset-voicing-input';
    densityInput.type = 'number';
    densityInput.step = '0.01';
    densityInput.min = '0';
    densityInput.max = '1';
    densityInput.value = Number.isFinite(Number(densityValue)) ? Number(densityValue).toFixed(2) : '';
    densityInput.dataset.field = 'density';
    densityInput.disabled = !canEdit;

    const transientInput = document.createElement('input');
    transientInput.className = 'preset-voicing-input';
    transientInput.type = 'number';
    transientInput.step = '0.01';
    transientInput.min = '0';
    transientInput.max = '1';
    transientInput.value = Number.isFinite(Number(transientValue)) ? Number(transientValue).toFixed(2) : '';
    transientInput.dataset.field = 'transient_focus';
    transientInput.disabled = !canEdit;

    const smoothInput = document.createElement('input');
    smoothInput.className = 'preset-voicing-input';
    smoothInput.type = 'number';
    smoothInput.step = '0.01';
    smoothInput.min = '0';
    smoothInput.max = '1';
    smoothInput.value = Number.isFinite(Number(smoothValue)) ? Number(smoothValue).toFixed(2) : '';
    smoothInput.dataset.field = 'smoothness';
    smoothInput.disabled = !canEdit;

    voicingEditor.appendChild(makeVoicingRow('Width', widthInput));
    voicingEditor.appendChild(makeVoicingRow('Density', densityInput));
    voicingEditor.appendChild(makeVoicingRow('Transient', transientInput));
    voicingEditor.appendChild(makeVoicingRow('Smoothness', smoothInput));
    if(voicingSaveBtn) voicingSaveBtn.disabled = !canEdit;
  }

  function ensureMacroBands(eqList){
    const used = new Set();
    const slots = MACRO_SLOTS.map((slot) => {
      let bestIndex = -1;
      let bestDistance = Infinity;
      eqList.forEach((band, index) => {
        if(used.has(index)) return;
        const bandType = normalizeEqType(band?.type);
        if(bandType !== slot.type) return;
        const freq = Number(band?.freq_hz ?? band?.freq);
        if(!Number.isFinite(freq)) return;
        const distance = Math.abs(freq - slot.defaultFreq);
        if(distance < bestDistance){
          bestDistance = distance;
          bestIndex = index;
        }
      });
      if(bestIndex === -1){
        if(eqList.length < 10){
          const newBand = {
            type: slot.type,
            freq_hz: slot.defaultFreq,
            gain_db: 0,
            q: slot.defaultQ,
          };
          eqList.push(newBand);
          bestIndex = eqList.length - 1;
        }else{
          eqList.forEach((band, index) => {
            const bandType = normalizeEqType(band?.type);
            if(bandType !== slot.type) return;
            const freq = Number(band?.freq_hz ?? band?.freq);
            if(!Number.isFinite(freq)) return;
            const distance = Math.abs(freq - slot.defaultFreq);
            if(distance < bestDistance){
              bestDistance = distance;
              bestIndex = index;
            }
          });
          if(bestIndex === -1 && eqList.length){
            bestIndex = 0;
          }
        }
      }
      used.add(bestIndex);
      return { slot, index: bestIndex, band: eqList[bestIndex] };
    });
    const extras = eqList
      .map((band, index) => ({ band, index }))
      .filter(item => !used.has(item.index))
      .sort((a, b) => {
        const aFreq = Number(a.band?.freq_hz ?? a.band?.freq);
        const bFreq = Number(b.band?.freq_hz ?? b.band?.freq);
        const aVal = Number.isFinite(aFreq) ? aFreq : Number.POSITIVE_INFINITY;
        const bVal = Number.isFinite(bFreq) ? bFreq : Number.POSITIVE_INFINITY;
        return aVal - bVal;
      });
    return { slots, extras };
  }

  function renderVoicingMacro(){
    if(!voicingMacro) return;
    voicingMacro.innerHTML = '';
    if(!selectedVoicingFull) return;
    const canEdit = selectedItem && (selectedItem.origin === 'user' || selectedItem.origin === 'staging');
    const eqList = getVoicingEqList();
    const { slots, extras } = ensureMacroBands(eqList);
    const entries = [
      ...slots.map(({ slot, index, band }) => ({ kind: 'macro', slot, index, band })),
      ...extras.map(({ band, index }) => ({ kind: 'extra', index, band })),
    ].filter(entry => entry.band);
    entries.sort((a, b) => {
      const aFreq = Number(a.band?.freq_hz ?? a.band?.freq);
      const bFreq = Number(b.band?.freq_hz ?? b.band?.freq);
      const aVal = Number.isFinite(aFreq) ? aFreq : Number.POSITIVE_INFINITY;
      const bVal = Number.isFinite(bFreq) ? bFreq : Number.POSITIVE_INFINITY;
      return aVal - bVal;
    });
    entries.forEach((entry) => {
      if(entry.kind === 'macro'){
        const { slot, index, band } = entry;
        const row = document.createElement('div');
        row.className = 'preset-voicing-macro-row';
        row.dataset.index = String(index);
        row.dataset.slot = slot.key;

        const label = document.createElement('div');
        label.className = 'preset-voicing-macro-label';
        label.textContent = slot.label;

        const select = document.createElement('select');
        select.className = 'preset-voicing-macro-select';
        select.dataset.field = 'freq';
        select.disabled = !canEdit;
        const rawFreq = Number(band?.freq_hz ?? band?.freq);
        const currentFreq = Number.isFinite(rawFreq) ? rawFreq : slot.defaultFreq;
        band.freq_hz = currentFreq;
        if(!Number.isFinite(Number(band?.q))){
          band.q = slot.defaultQ;
        }
        const choices = slot.choices.slice();
        if(Number.isFinite(currentFreq) && !choices.includes(currentFreq)){
          choices.push(currentFreq);
          choices.sort((a, b) => a - b);
        }
        choices.forEach((freq) => {
          const option = document.createElement('option');
          option.value = String(freq);
          option.textContent = `${freq} Hz`;
          if(Number.isFinite(currentFreq) && Math.round(currentFreq) === Math.round(freq)){
            option.selected = true;
          }
          select.appendChild(option);
        });

        const slider = document.createElement('input');
        slider.className = 'preset-voicing-macro-slider';
        slider.type = 'range';
        slider.min = '-3';
        slider.max = '3';
        slider.step = '0.1';
        slider.dataset.field = 'gain';
        const gainVal = clampValue(Number(band?.gain_db ?? band?.gain ?? 0), -3, 3);
        slider.value = Number.isFinite(gainVal) ? String(gainVal) : '0';
        slider.disabled = !canEdit;
        if(Number.isFinite(gainVal)){
          band.gain_db = gainVal;
        }

        const value = document.createElement('div');
        value.className = 'preset-voicing-macro-value';
        value.textContent = `${Number(slider.value).toFixed(1)} dB`;

        row.appendChild(label);
        row.appendChild(select);
        row.appendChild(slider);
        row.appendChild(value);
        voicingMacro.appendChild(row);
        return;
      }
      const band = entry.band;
      const row = document.createElement('div');
      row.className = 'preset-voicing-macro-row preset-voicing-macro-row-extra';
      const label = document.createElement('div');
      label.className = 'preset-voicing-macro-label';
      label.textContent = 'User Band';
      const freq = document.createElement('div');
      freq.className = 'preset-voicing-macro-text';
      const freqVal = Number(band?.freq_hz ?? band?.freq);
      freq.textContent = Number.isFinite(freqVal) ? `${freqVal.toFixed(0)} Hz` : '-';
      const gain = document.createElement('div');
      gain.className = 'preset-voicing-macro-text';
      const gainVal = Number(band?.gain_db ?? band?.gain);
      gain.textContent = Number.isFinite(gainVal) ? `${gainVal.toFixed(1)} dB` : '-';
      const q = document.createElement('div');
      q.className = 'preset-voicing-macro-text';
      const qVal = Number(band?.q);
      q.textContent = Number.isFinite(qVal) ? `Q ${qVal.toFixed(2)}` : '-';
      row.appendChild(label);
      row.appendChild(freq);
      row.appendChild(gain);
      row.appendChild(q);
      voicingMacro.appendChild(row);
    });
  }

  function renderVoicingAdvanced(){
    const eqList = getVoicingEqList();
    const canEdit = selectedItem && (selectedItem.origin === 'user' || selectedItem.origin === 'staging');
    renderVoicingEqEditor(eqList, { canEdit });
    if(voicingAddBandBtn){
      voicingAddBandBtn.disabled = !canEdit || eqList.length >= 10;
    }
  }

  async function loadVoicingFull(item){
    if(!item) return null;
    const token = ++voicingLoadToken;
    const params = new URLSearchParams({
      id: item.id,
      kind: 'voicing',
      origin: item.origin,
    });
    try{
      const res = await fetch(`/api/library/item/download?${params.toString()}`, { cache: 'no-store' });
      if(!res.ok) throw new Error('load_failed');
      const text = await res.text();
      const data = JSON.parse(text);
      if(token !== voicingLoadToken) return null;
      return ensureVoicingShape(data);
    }catch(_err){
      return null;
    }
  }

  function handleVoicingMacroInput(evt){
    if(!selectedVoicingFull) return;
    const row = evt.target.closest('.preset-voicing-macro-row');
    if(!row) return;
    const eqList = getVoicingEqList();
    const index = Number(row.dataset.index);
    if(!Number.isFinite(index) || !eqList[index]) return;
    const band = eqList[index];
    const field = evt.target.dataset.field;
    if(field === 'freq'){
      const freqVal = Number(evt.target.value);
      if(Number.isFinite(freqVal)){
        band.freq_hz = freqVal;
      }
    }
    if(field === 'gain'){
      const gainVal = clampValue(Number(evt.target.value), -3, 3);
      if(Number.isFinite(gainVal)){
        band.gain_db = gainVal;
        const valueEl = row.querySelector('.preset-voicing-macro-value');
        if(valueEl) valueEl.textContent = `${gainVal.toFixed(1)} dB`;
      }
    }
    if(!Number.isFinite(Number(band.q))){
      const slot = MACRO_SLOTS.find(item => item.key === row.dataset.slot);
      band.q = slot ? slot.defaultQ : 1.0;
    }
    renderEqPreview(eqList);
    updateSaveButtonStates();
  }

  function handleVoicingEqInput(evt){
    if(!selectedVoicingFull) return;
    const input = evt.target;
    if(!input.dataset.field) return;
    const row = input.closest('.preset-eq-row');
    if(!row) return;
    const eqList = getVoicingEqList();
    const index = Number(row.dataset.index);
    if(!Number.isFinite(index) || !eqList[index]) return;
    const band = eqList[index];
    if(input.dataset.field === 'freq'){
      const freqVal = Number(input.value);
      band.freq_hz = Number.isFinite(freqVal) ? freqVal : null;
    }
    if(input.dataset.field === 'gain'){
      const gainVal = Number(input.value);
      band.gain_db = Number.isFinite(gainVal) ? gainVal : null;
    }
    if(input.dataset.field === 'q'){
      const qVal = Number(input.value);
      band.q = Number.isFinite(qVal) ? qVal : null;
    }
    renderEqPreview(eqList);
    updateSaveButtonStates();
  }

  function handleVoicingEqClick(evt){
    const deleteBtn = evt.target.closest('[data-action="delete-eq"]');
    if(!deleteBtn) return;
    if(!selectedVoicingFull) return;
    const row = deleteBtn.closest('.preset-eq-row');
    if(!row) return;
    const eqList = getVoicingEqList();
    const index = Number(row.dataset.index);
    if(!Number.isFinite(index)) return;
    eqList.splice(index, 1);
    renderEqPreview(eqList);
    renderVoicingAdvanced();
    if(voicingMode === 'simple'){
      renderVoicingMacro();
    }
    updateSaveButtonStates();
  }

  function handleVoicingEditorInput(evt){
    if(!selectedVoicingFull) return;
    const input = evt.target;
    const field = input.dataset.field;
    if(!field) return;
    const chain = selectedVoicingFull.chain || {};
    if(field === 'width'){
      const widthVal = Number(input.value);
      if(!chain.stereo) chain.stereo = {};
      chain.stereo.width = Number.isFinite(widthVal) ? widthVal : null;
    }else{
      if(!chain.dynamics) chain.dynamics = {};
      const val = Number(input.value);
      chain.dynamics[field] = Number.isFinite(val) ? val : null;
    }
    selectedVoicingFull.chain = chain;
    updateSaveButtonStates();
  }

  function addVoicingBand(){
    if(!selectedVoicingFull) return;
    const eqList = getVoicingEqList();
    if(eqList.length >= 10) return;
    eqList.push({
      type: 'peaking',
      freq_hz: 1000,
      gain_db: 0,
      q: 1.0,
    });
    renderEqPreview(eqList);
    renderVoicingAdvanced();
    if(voicingMode === 'simple'){
      renderVoicingMacro();
    }
    updateSaveButtonStates();
  }

  async function saveProfileEdits(){
    if(!selectedItem || selectedItem.kind !== 'profile') return;
    if(!(selectedItem.origin === 'user' || selectedItem.origin === 'staging')) return;
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

    const category = sanitizeInputValue(profileEditor.querySelector('[data-field=\"category\"]')?.value || '');
    if(category){
      fields.category = category;
    }else{
      fields.category = '';
    }

    const orderRaw = profileEditor.querySelector('[data-field=\"order\"]')?.value || '';
    if(orderRaw === ''){
      fields.order = '';
    }else{
      const orderVal = Number(orderRaw);
      if(!Number.isFinite(orderVal) || orderVal < 0 || orderVal > 9999){
        addStatusLine('Order must be between 0 and 9999.');
        return;
      }
      fields.order = Math.round(orderVal);
    }

    try{
      const res = await fetch('/api/library/item/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: 'profile',
          origin: selectedItem.origin,
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
    if(!selectedItem || selectedItem.kind !== 'voicing') return;
    if(!(selectedItem.origin === 'user' || selectedItem.origin === 'staging')) return;
    if(!voicingEditor) return;
    if(!voicingEq) return;
    if(!selectedVoicingFull){
      selectedVoicingFull = voicingDataFromItem(selectedItem);
    }
    const readNum = (field) => {
      const raw = voicingEditor.querySelector(`[data-field="${field}"]`)?.value || '';
      if(!raw) return null;
      const num = Number(raw);
      return Number.isFinite(num) ? num : NaN;
    };
    const width = readNum('width');
    if(width !== null && (!Number.isFinite(width) || width < 0.9 || width > 1.1)){
      addStatusLine('Width must be between 0.90 and 1.10.');
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
    const eqBands = [];
    const eqList = getVoicingEqList();
    for (const band of eqList) {
      const type = normalizeEqType(band?.type);
      if (!type) continue;
      const freq = Number(band?.freq_hz ?? band?.freq);
      if (!Number.isFinite(freq) || freq < 20 || freq > 20000) {
        addStatusLine(`EQ ${eqTypeLabel(type)} frequency must be 20-20000 Hz.`);
        return;
      }
      const gain = Number(band?.gain_db ?? band?.gain ?? 0);
      if (!Number.isFinite(gain) || gain < -6 || gain > 6) {
        addStatusLine(`EQ ${eqTypeLabel(type)} gain must be between -6 and 6 dB.`);
        return;
      }
      const q = Number(band?.q ?? 1.0);
      if (!Number.isFinite(q) || q < 0.3 || q > 4.0) {
        addStatusLine(`EQ ${eqTypeLabel(type)} Q must be between 0.3 and 4.0.`);
        return;
      }
      eqBands.push({
        type,
        freq_hz: freq,
        gain_db: gain,
        q,
      });
    }
    const meta = selectedVoicingFull?.meta || selectedItem.meta || {};
    const title = sanitizeInputValue(meta.title || '');
    const tags = Array.isArray(meta.tags) ? meta.tags.map(tag => sanitizeInputValue(tag)).filter(Boolean) : [];
    try{
      const res = await fetch('/api/library/item/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: selectedItem.id,
          kind: 'voicing',
          origin: selectedItem.origin,
          fields: {
            title: title || undefined,
            tags,
            width,
            density,
            transient_focus: transient,
            smoothness,
            eq: eqBands,
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
      selectedVoicingFull = null;
      if(voicingEq) voicingEq.innerHTML = '';
      renderProfileEditor(null);
      renderVoicingEditor(null);
      if(downloadBtn) downloadBtn.disabled = true;
      if(moveBtn) moveBtn.disabled = true;
      if(duplicateBtn) duplicateBtn.disabled = true;
      if(deleteBtn) deleteBtn.disabled = true;
    if(detailHint) detailHint.textContent = 'Select an item to view details.';
    if(selectedHint) selectedHint.textContent = 'Select a voicing or profile to view details.';
    setProfileBaselineFromEditor();
    setVoicingBaselineFromEditor();
    updateSaveButtonStates();
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
      const canEdit = selectedItem.origin === 'user' || selectedItem.origin === 'staging';
      if(!selectedVoicingFull || selectedVoicingFull.id !== selectedItem.id){
        selectedVoicingFull = voicingDataFromItem(selectedItem);
      }
      selectedVoicingFull = ensureVoicingShape(selectedVoicingFull);
      const meta = selectedVoicingFull?.meta || selectedItem.meta || {};
      const chain = selectedVoicingFull?.chain || {};
      const tags = Array.isArray(meta.tags) ? meta.tags : [];
      detailSummary.textContent = tags[0] || 'No description available.';
      if(detailVoicing) detailVoicing.hidden = false;
      if(detailProfile) detailProfile.hidden = true;
      const width = Number.isFinite(Number(chain?.stereo?.width))
        ? Number(chain.stereo.width)
        : Number(meta?.width ?? meta?.stereo?.width);
      const dynamics = chain?.dynamics || meta?.dynamics || {};
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
      setVoicingMode('simple');
      renderVoicingEditor({ ...selectedItem, ...selectedVoicingFull });
      if(voicingAddBandBtn) voicingAddBandBtn.disabled = !canEdit;
      loadVoicingFull(selectedItem).then((fullData) => {
        if(!fullData) return;
        const fullId = fullData.id || fullData.name;
        if(!selectedItem || selectedItem.kind !== 'voicing' || (fullId && selectedItem.id !== fullId)) return;
        selectedVoicingFull = fullData;
        renderVoicingEditor({ ...selectedItem, ...selectedVoicingFull });
        renderEqPreview(Array.isArray(fullData.chain?.eq) ? fullData.chain.eq : []);
        if(voicingMode === 'advanced'){
          renderVoicingAdvanced();
        }else{
          renderVoicingMacro();
        }
        setProfileBaselineFromEditor();
        setVoicingBaselineFromEditor();
        updateSaveButtonStates();
      });
    }else{
      selectedVoicingFull = null;
      const lufs = formatNumber(selectedItem.meta?.lufs, 1);
      const tp = formatNumber(selectedItem.meta?.tp ?? selectedItem.meta?.tpp, 1);
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
      if(selectedItem.origin === 'builtin'){
        detailHint.textContent = 'Duplicate to User to edit this preset.';
      }else{
        detailHint.textContent = selectedItem.origin === 'staging'
          ? 'Move to User to store this item in your library.'
          : 'Download, duplicate, or delete this item.';
      }
    }
    if(selectedHint) selectedHint.textContent = `Selected: ${selectedItem.title || selectedItem.id}`;
    setProfileBaselineFromEditor();
    setVoicingBaselineFromEditor();
    updateSaveButtonStates();
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
    if(profileEditor) profileEditor.addEventListener('input', updateSaveButtonStates);
    if(voicingEditor) voicingEditor.addEventListener('input', handleVoicingEditorInput);
    if(voicingEq) voicingEq.addEventListener('input', handleVoicingEqInput);
    if(voicingEq) voicingEq.addEventListener('click', handleVoicingEqClick);
    if(voicingMacro) voicingMacro.addEventListener('input', handleVoicingMacroInput);
    if(voicingModeSimpleBtn) voicingModeSimpleBtn.addEventListener('click', ()=> setVoicingMode('simple'));
    if(voicingModeAdvancedBtn) voicingModeAdvancedBtn.addEventListener('click', ()=> setVoicingMode('advanced'));
    if(voicingAddBandBtn) voicingAddBandBtn.addEventListener('click', addVoicingBand);
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
