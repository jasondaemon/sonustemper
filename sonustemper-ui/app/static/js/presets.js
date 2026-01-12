(function(){
  const page = document.getElementById('presetPage');
  if(!page) return;

  const referenceForm = document.getElementById('presetGenerateForm');
  const referenceFile = document.getElementById('referenceFile');
  const referenceName = document.getElementById('referenceName');
  const referenceStatus = document.getElementById('referenceStatus');
  const referenceGenerateBtn = document.getElementById('referenceGenerateBtn');
  const generateVoicing = document.getElementById('generateVoicing');
  const generateProfile = document.getElementById('generateProfile');

  const presetJsonFile = document.getElementById('presetJsonFile');
  const presetJsonName = document.getElementById('presetJsonName');
  const uploadPresetJsonBtn = document.getElementById('uploadPresetJsonBtn');
  const uploadPresetJsonStatus = document.getElementById('uploadPresetJsonStatus');
  const builtinPresetSelect = document.getElementById('builtinPresetSelect');
  const duplicateBuiltinBtn = document.getElementById('duplicateBuiltinBtn');
  const builtinPresetStatus = document.getElementById('builtinPresetStatus');

  const detailTitle = document.getElementById('presetDetailTitle');
  const detailKind = document.getElementById('presetDetailKind');
  const detailSummary = document.getElementById('presetDetailSummary');
  const detailMeta = document.getElementById('presetDetailMeta');
  const detailSubtitle = document.getElementById('presetDetailSubtitle');
  const detailHint = document.getElementById('presetDetailHint');
  const detailVoicing = document.getElementById('presetDetailVoicing');
  const detailProfile = document.getElementById('presetDetailProfile');
  const voicingStats = document.getElementById('presetVoicingStats');
  const profileStats = document.getElementById('presetProfileStats');
  const eqPreview = document.getElementById('presetEqPreview');

  const selectedHint = document.getElementById('presetSelectedHint');
  const downloadBtn = document.getElementById('presetDownloadBtn');
  const moveBtn = document.getElementById('presetMoveBtn');
  const duplicateBtn = document.getElementById('presetDuplicateBtn');
  const deleteBtn = document.getElementById('presetDeleteBtn');

  let selectedItem = null;

  function setStatus(el, msg){
    if(el) el.textContent = msg || '';
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
    if(!builtinPresetSelect) return;
    builtinPresetSelect.innerHTML = '';
    try{
      const res = await fetch('/api/library/builtins', { cache: 'no-store' });
      if(!res.ok) throw new Error('load_failed');
      const data = await res.json();
      const items = data.items || [];
      if(!items.length){
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No provided presets found';
        builtinPresetSelect.appendChild(opt);
        if(duplicateBuiltinBtn) duplicateBuiltinBtn.disabled = true;
        return;
      }
      const groups = {};
      items.forEach(item => {
        const kind = (item.kind || item.meta?.kind || 'profile').toLowerCase();
        if(!groups[kind]) groups[kind] = [];
        groups[kind].push(item);
      });
      Object.keys(groups).sort().forEach(kind => {
        const optgroup = document.createElement('optgroup');
        optgroup.label = kind === 'voicing' ? 'Provided Voicings' : 'Provided Profiles';
        groups[kind].sort((a, b) => {
          const aTitle = a.meta?.title || a.title || a.name || '';
          const bTitle = b.meta?.title || b.title || b.name || '';
          return aTitle.localeCompare(bTitle);
        }).forEach(item => {
          const option = document.createElement('option');
          const itemId = item.id || item.name || '';
          option.value = `${kind}:${itemId}`;
          option.textContent = item.meta?.title || item.title || itemId || 'Preset';
          optgroup.appendChild(option);
        });
        builtinPresetSelect.appendChild(optgroup);
      });
      if(duplicateBuiltinBtn) duplicateBuiltinBtn.disabled = false;
    }catch(_err){
      setStatus(builtinPresetStatus, 'Failed to load provided presets.');
      if(duplicateBuiltinBtn) duplicateBuiltinBtn.disabled = true;
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
      setStatus(referenceStatus, 'Select an audio file.');
      return;
    }
    const wantsVoicing = Boolean(generateVoicing?.checked);
    const wantsProfile = Boolean(generateProfile?.checked);
    if(!wantsVoicing && !wantsProfile){
      setStatus(referenceStatus, 'Select at least one item to generate.');
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('base_name', (referenceName?.value || '').trim());
    fd.append('generate_voicing', wantsVoicing ? 'true' : 'false');
    fd.append('generate_profile', wantsProfile ? 'true' : 'false');
    setStatus(referenceStatus, 'Uploading...');
    try{
      const res = await fetch('/api/generate_from_reference', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Generate failed');
      }
      const data = await res.json();
      const createdCount = Array.isArray(data.items) ? data.items.length : 0;
      setStatus(referenceStatus, createdCount ? `Generated ${createdCount} item(s).` : 'Generated.');
      if(referenceFile) referenceFile.value = '';
      refreshPresetBrowser();
    }catch(_err){
      setStatus(referenceStatus, 'Generate failed.');
    }
  }

  async function handleUploadJson(){
    const file = presetJsonFile?.files?.[0];
    if(!file){
      setStatus(uploadPresetJsonStatus, 'Select a JSON file.');
      return;
    }
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('name', (presetJsonName?.value || '').trim());
    setStatus(uploadPresetJsonStatus, 'Uploading...');
    try{
      const res = await fetch('/api/import_json_to_staging', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Upload failed');
      }
      setStatus(uploadPresetJsonStatus, 'Imported to staging.');
      if(presetJsonFile) presetJsonFile.value = '';
      refreshPresetBrowser();
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Import failed.');
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
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Delete failed.');
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
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Move failed.');
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
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Duplicate failed.');
    }
  }

  async function duplicateBuiltin(){
    if(!builtinPresetSelect) return;
    const value = builtinPresetSelect.value;
    if(!value) return;
    const [kind, id] = value.split(':');
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
      setStatus(builtinPresetStatus, 'Preset duplicated.');
      refreshPresetBrowser();
    }catch(_err){
      setStatus(builtinPresetStatus, 'Duplicate failed.');
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
    if(duplicateBuiltinBtn) duplicateBuiltinBtn.addEventListener('click', duplicateBuiltin);
    updateDetail();
    loadBuiltinPresets();
  });

  document.addEventListener('htmx:afterSwap', (evt)=>{
    const browser = document.getElementById('presetBrowser');
    if(browser && browser.contains(evt.target)){
      syncActiveStates();
    }
  });
})();
