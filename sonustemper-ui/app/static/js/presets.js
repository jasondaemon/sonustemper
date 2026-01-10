(function(){
  const page = document.getElementById('presetPage');
  if(!page) return;

  const referenceForm = document.getElementById('presetGenerateForm');
  const referenceFile = document.getElementById('referenceFile');
  const referenceType = document.getElementById('referenceType');
  const referenceName = document.getElementById('referenceName');
  const referenceStatus = document.getElementById('referenceStatus');

  const presetJsonFile = document.getElementById('presetJsonFile');
  const presetJsonType = document.getElementById('presetJsonType');
  const presetJsonName = document.getElementById('presetJsonName');
  const uploadPresetJsonBtn = document.getElementById('uploadPresetJsonBtn');
  const uploadPresetJsonStatus = document.getElementById('uploadPresetJsonStatus');

  const detailTitle = document.getElementById('presetDetailTitle');
  const detailMeta = document.getElementById('presetDetailMeta');
  const detailSubtitle = document.getElementById('presetDetailSubtitle');
  const selectedHint = document.getElementById('presetSelectedHint');
  const downloadBtn = document.getElementById('presetDownloadBtn');
  const duplicateBtn = document.getElementById('presetDuplicateBtn');
  const deleteBtn = document.getElementById('presetDeleteBtn');

  let selectedPreset = null;

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

  function updateDetail(){
    if(!detailTitle || !detailMeta) return;
    if(!selectedPreset){
      detailTitle.textContent = 'No profile selected';
      detailSubtitle.textContent = 'Choose a profile from the library.';
      detailMeta.innerHTML = '<div><span class="muted">Source:</span> -</div>' +
        '<div><span class="muted">Created:</span> -</div>' +
        '<div><span class="muted">Type:</span> -</div>';
      if(downloadBtn) downloadBtn.disabled = true;
      if(duplicateBtn) duplicateBtn.disabled = true;
      if(deleteBtn) deleteBtn.disabled = true;
      if(selectedHint) selectedHint.textContent = 'Select a profile to view details.';
      return;
    }
    detailTitle.textContent = selectedPreset.title || selectedPreset.name || 'Profile';
    const subtitleParts = [selectedPreset.originLabel || 'Profile'];
    if (selectedPreset.filename) subtitleParts.push(selectedPreset.filename);
    detailSubtitle.textContent = subtitleParts.join(' | ');
    const source = selectedPreset.source_file || '-';
    const created = selectedPreset.created_at || '-';
    const kind = selectedPreset.kind || '-';
    detailMeta.innerHTML = `<div><span class="muted">Source:</span> ${source}</div>` +
      `<div><span class="muted">Created:</span> ${created}</div>` +
      `<div><span class="muted">Type:</span> ${kind}</div>`;
    if(downloadBtn) downloadBtn.disabled = false;
    if(duplicateBtn) duplicateBtn.disabled = false;
    if(deleteBtn) deleteBtn.disabled = false;
    if(selectedHint) selectedHint.textContent = `Selected: ${selectedPreset.title || selectedPreset.name}`;
  }

  function setSelectedPreset(preset){
    selectedPreset = preset;
    updateDetail();
    const browser = document.getElementById('presetBrowser');
    if(browser){
      browser.querySelectorAll('.browser-item[data-kind="preset"]').forEach(btn => {
        btn.classList.toggle('active', preset && btn.dataset.id === preset.name);
      });
    }
  }

  function parsePresetFromButton(btn){
    if(!btn) return null;
    let meta = {};
    if(btn.dataset.meta){
      try{ meta = JSON.parse(btn.dataset.meta); }catch(_err){ meta = {}; }
    }
    const titleEl = btn.querySelector('.browser-item-title');
    const title = titleEl ? titleEl.textContent.trim() : (meta.title || btn.dataset.id);
    return {
      name: meta.name || btn.dataset.id,
      filename: meta.filename,
      title,
      source_file: meta.source_file,
      created_at: meta.created_at,
      kind: meta.kind,
      origin: meta.origin,
      originLabel: meta.origin === 'generated' ? 'Generated Profile' : 'User Profile',
    };
  }

  function slugifyName(name){
    return (name || '').trim();
  }

  async function handleGenerate(e){
    e.preventDefault();
    const file = referenceFile?.files?.[0];
    if(!file){
      setStatus(referenceStatus, 'Select an audio file.');
      return;
    }
    const type = referenceType?.value || 'voicing';
    const override = slugifyName(referenceName?.value || '');
    const ext = file.name.includes('.') ? file.name.slice(file.name.lastIndexOf('.')) : '';
    const base = override || file.name.replace(ext, '') + '-' + type;
    const filename = `${base}${ext}`;
    const sendFile = new File([file], filename, { type: file.type });
    const fd = new FormData();
    fd.append('file', sendFile, sendFile.name);
    setStatus(referenceStatus, 'Uploading...');
    try{
      const res = await fetch('/api/preset/generate', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Generate failed');
      }
      const data = await res.json();
      setStatus(referenceStatus, data.message || 'Profile created.');
      referenceFile.value = '';
      refreshPresetBrowser();
    }catch(err){
      setStatus(referenceStatus, 'Create failed.');
    }
  }

  async function handleUploadJson(){
    const file = presetJsonFile?.files?.[0];
    if(!file){
      setStatus(uploadPresetJsonStatus, 'Select a JSON file.');
      return;
    }
    let data;
    try{
      const text = await file.text();
      data = JSON.parse(text);
      if(!data || typeof data !== 'object') throw new Error();
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Invalid JSON.');
      return;
    }
    const type = presetJsonType?.value || 'voicing';
    const override = slugifyName(presetJsonName?.value || '');
    data.meta = data.meta || {};
    data.meta.kind = type;
    const baseName = override || data.name || file.name.replace(/\.json$/i, '') || 'profile';
    data.name = baseName;
    if(!data.meta.title){
      data.meta.title = baseName;
    }
    const name = baseName;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const uploadFile = new File([blob], `${name}.json`, { type: 'application/json' });
    const fd = new FormData();
    fd.append('file', uploadFile, uploadFile.name);
    setStatus(uploadPresetJsonStatus, 'Uploading...');
    try{
      const res = await fetch('/api/preset/upload', { method: 'POST', body: fd });
      if(!res.ok){
        const t = await res.text();
        throw new Error(t || 'Upload failed');
      }
      const j = await res.json();
      setStatus(uploadPresetJsonStatus, j.message || 'Uploaded.');
      presetJsonFile.value = '';
      refreshPresetBrowser();
    }catch(err){
      setStatus(uploadPresetJsonStatus, 'Upload failed.');
    }
  }

  function downloadPreset(){
    if(!selectedPreset) return;
    window.location.href = `/api/preset/download/${encodeURIComponent(selectedPreset.name)}`;
  }

  async function deletePreset(){
    if(!selectedPreset) return;
    if(!confirm(`Delete profile "${selectedPreset.name}"?`)) return;
    const res = await fetch(`/api/preset/${encodeURIComponent(selectedPreset.name)}`, { method: 'DELETE' });
    if(!res.ok){
      setStatus(uploadPresetJsonStatus, 'Delete failed.');
      return;
    }
    setSelectedPreset(null);
    refreshPresetBrowser();
  }

  async function duplicatePreset(){
    if(!selectedPreset) return;
    const newName = prompt('New profile name', `${selectedPreset.name}-copy`);
    if(!newName) return;
    try{
      const res = await fetch(`/api/preset/download/${encodeURIComponent(selectedPreset.name)}`);
      if(!res.ok) throw new Error('download_failed');
      const data = await res.json();
      data.name = newName;
      data.meta = data.meta || {};
      data.meta.title = newName;
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const uploadFile = new File([blob], `${newName}.json`, { type: 'application/json' });
      const fd = new FormData();
      fd.append('file', uploadFile, uploadFile.name);
      const uploadRes = await fetch('/api/preset/upload', { method: 'POST', body: fd });
      if(!uploadRes.ok) throw new Error('upload_failed');
      refreshPresetBrowser();
    }catch(_err){
      setStatus(uploadPresetJsonStatus, 'Duplicate failed.');
    }
  }

  document.addEventListener('click', (evt)=>{
    const btn = evt.target.closest('.file-browser .browser-item');
    if(!btn || btn.dataset.kind !== 'preset') return;
    if(btn.disabled) return;
    const preset = parsePresetFromButton(btn);
    setSelectedPreset(preset);
  });

  document.addEventListener('DOMContentLoaded', ()=>{
    if(referenceForm) referenceForm.addEventListener('submit', handleGenerate);
    if(uploadPresetJsonBtn) uploadPresetJsonBtn.addEventListener('click', handleUploadJson);
    if(downloadBtn) downloadBtn.addEventListener('click', downloadPreset);
    if(duplicateBtn) duplicateBtn.addEventListener('click', duplicatePreset);
    if(deleteBtn) deleteBtn.addEventListener('click', deletePreset);
    updateDetail();
  });

  document.addEventListener('htmx:afterSwap', (evt)=>{
    const browser = document.getElementById('presetBrowser');
    if(browser && browser.contains(evt.target) && selectedPreset){
      const btn = browser.querySelector(`.browser-item[data-kind="preset"][data-id="${selectedPreset.name}"]`);
      if(btn) btn.classList.add('active');
    }
  });
})();
