(function(){
  const page = document.getElementById('taggingPage');
  if(!page) return;

  const tagState = {
    working: [],
    selectedId: null,
    selectedIds: new Set(),
    fileDetails: {},
    artInfoCache: {},
    albumArt: { mode: 'keep', uploadId: null, mime: null, size: 0, preview: null },
    dirty: false,
  };

  function tagToast(msg){
    const el = document.getElementById('tagSaveStatus');
    if(el) el.textContent = msg || '';
  }

  function markDirty(){
    tagState.dirty = true;
    updateDownloadState();
  }

  function updateDownloadState(){
    const zipBtn = document.getElementById('albDownloadBtn');
    if(zipBtn){
      zipBtn.disabled = tagState.dirty || !tagState.working.length;
    }
    document.querySelectorAll('.trackDlBtn').forEach(btn => {
      btn.disabled = tagState.dirty;
    });
  }

  function updateSelectedCount(){
    const el = document.getElementById('tagSelectedCount');
    if(el) el.textContent = `${tagState.selectedIds.size} selected`;
  }

  function syncWorkingSelected(){
    tagState.selectedIds = new Set(tagState.working.map(w => w.id));
    updateSelectedCount();
    syncBrowserSelection();
  }

  function parseBadgesFromRow(row){
    if(!row) return [];
    try{
      return JSON.parse(row.dataset.badges || '[]');
    }catch(_err){
      return [];
    }
  }

  function buildItemFromButton(btn){
    if(!btn) return null;
    const meta = btn.dataset.meta ? JSON.parse(btn.dataset.meta) : {};
    const titleEl = btn.querySelector('.browser-item-title');
    const title = titleEl ? titleEl.textContent.trim() : meta.basename || btn.dataset.id;
    const badgeRow = btn.querySelector('.badge-row');
    const badges = parseBadgesFromRow(badgeRow);
    const basename = meta.basename || title;
    const relpath = meta.relpath || meta.full_name || basename;
    return {
      id: btn.dataset.id,
      root: meta.root || '',
      basename,
      relpath,
      full_name: meta.full_name || relpath || basename,
      display_title: title,
      badges: badges,
    };
  }

  function addToWorking(item){
    if(!item || !item.id) return;
    if(tagState.working.find(w => w.id === item.id)) return;
    tagState.working.push(item);
    if(!tagState.selectedId) tagState.selectedId = item.id;
    syncWorkingSelected();
    renderWorkingList();
    updateEditorView();
    updateDownloadState();
  }

  function removeFromWorking(id){
    tagState.working = tagState.working.filter(w => w.id !== id);
    if(tagState.selectedId === id){
      tagState.selectedId = tagState.working.length ? tagState.working[0].id : null;
    }
    syncWorkingSelected();
    renderWorkingList();
    updateEditorView();
  }

  function renderWorkingList(){
    const list = document.getElementById('workingList');
    if(!list) return;
    list.innerHTML = '';
    if(!tagState.working.length){
      list.innerHTML = '<div class="muted">Add files from Library to start.</div>';
      return;
    }
    tagState.working.forEach(it => {
      const row = document.createElement('div');
      row.className = `tag-item${tagState.selectedId === it.id ? ' active' : ''}`;
      row.dataset.id = it.id;

      const left = document.createElement('div');
      left.className = 'tag-row';

      const title = document.createElement('div');
      title.className = 'tag-row-title';
      title.textContent = it.display_title || it.basename || it.relpath || '(untitled)';
      title.title = it.full_name || it.basename || it.relpath || '';

      const badgeRow = document.createElement('div');
      badgeRow.className = 'badge-row';
      badgeRow.dataset.badges = JSON.stringify(it.badges || []);

      left.appendChild(title);
      left.appendChild(badgeRow);

      const actions = document.createElement('div');
      actions.className = 'tag-actions';
      const rem = document.createElement('button');
      rem.className = 'btn small ghost';
      rem.textContent = 'Remove';
      rem.addEventListener('click', (e)=>{
        e.stopPropagation();
        removeFromWorking(it.id);
      });
      actions.appendChild(rem);

      row.appendChild(left);
      row.appendChild(actions);

      row.addEventListener('click', ()=>{
        tagState.selectedId = it.id;
        renderWorkingList();
        updateEditorView({ fromSelection: true });
      });

      list.appendChild(row);
    });
    if(typeof layoutBadgeRows === 'function'){
      layoutBadgeRows(list);
    }
  }

  function updateEditorView(opts = {}){
    const albumPane = document.getElementById('tagAlbumForm');
    const albumEmpty = document.getElementById('tagAlbumEmpty');
    if(!tagState.working.length){
      if(albumEmpty) albumEmpty.style.display = 'block';
      if(albumPane) albumPane.style.display = 'none';
      updateDownloadState();
      return;
    }
    if(albumEmpty) albumEmpty.style.display = 'none';
    if(albumPane) albumPane.style.display = 'flex';
    renderAlbumForm();

    if(opts.fromSelection){
      if(tagState.selectedId){
        ensureFileDetail(tagState.selectedId).then(renderAlbumForm);
      }
      return;
    }
    const targetId = tagState.selectedId || tagState.working[0].id;
    tagState.selectedId = targetId;
    ensureFileDetail(targetId).then(renderAlbumForm);
  }

  async function ensureFileDetail(id){
    if(tagState.fileDetails[id]) return tagState.fileDetails[id];
    try{
      const res = await fetch(`/api/tagger/file/${encodeURIComponent(id)}`, { cache: 'no-store' });
      if(!res.ok) throw new Error();
      const data = await res.json();
      tagState.fileDetails[id] = data;
      return data;
    }catch(_err){
      return null;
    }
  }

  function renderAlbumForm(){
    const ids = tagState.working.map(w => w.id);
    const empty = document.getElementById('tagAlbumEmpty');
    const form = document.getElementById('tagAlbumForm');
    if(!form || !empty) return;
    if(!ids.length){
      empty.style.display = 'block';
      form.style.display = 'none';
      updateDownloadState();
      return;
    }
    empty.style.display = 'none';
    form.style.display = 'flex';
    syncWorkingSelected();

    const tbody = document.getElementById('albTableBody');
    if(!tbody) return;
    tbody.innerHTML = '';

    const workingById = new Map(tagState.working.map(item => [item.id, item]));

    ids.forEach((id)=>{
      const row = document.createElement('tr');
      row.dataset.id = id;
      const detail = tagState.fileDetails[id];
      if(!detail){
        ensureFileDetail(id).then(renderAlbumForm);
      }
      const item = workingById.get(id);
      const tags = detail?.tags || {};
      const base = detail?.basename || item?.display_title || item?.basename || id;
      const trackVal = tags.track || '';
      const titleVal = tags.title || item?.display_title || base;
      const artistVal = tags.artist || '';
      const tds = [
        `<button class="btn small ghost trackDlBtn" type="button" data-id="${id}" title="Download track">DL</button>`,
        `<input name="albTrack" value="${trackVal || ''}">`,
        `<input name="albTitle" value="${titleVal || ''}">`,
        `<input name="albArtist" value="${artistVal || ''}">`,
        `<div class="tag-filename" title="${base}">${base}</div>`,
      ];
      tds.forEach(html => {
        const td = document.createElement('td');
        td.innerHTML = html;
        row.appendChild(td);
      });
      tbody.appendChild(row);
    });

    document.querySelectorAll('.trackDlBtn').forEach(btn => {
      btn.disabled = tagState.dirty;
      btn.onclick = ()=> downloadSingle(btn.dataset.id);
    });

    updateArtworkStatus(ids);
    updateDownloadState();

    document.querySelectorAll('#tagAlbumForm input').forEach(inp => {
      inp.oninput = markDirty;
    });
  }

  async function fetchArtInfo(ids){
    if(!ids || !ids.length) return;
    const missing = ids.filter(id => !tagState.artInfoCache[id]);
    await Promise.all(missing.map(async (id)=>{
      try{
        const res = await fetch(`/api/tagger/file/${encodeURIComponent(id)}/artwork-info`, { cache: 'no-store' });
        if(!res.ok) throw new Error();
        const data = await res.json();
        tagState.artInfoCache[id] = data;
      }catch(_err){
        tagState.artInfoCache[id] = { present: false, sha256: null, mime: null };
      }
    }));
  }

  function updateArtworkStatus(ids){
    const artStatus = document.getElementById('albArtStatus');
    const artNone = document.getElementById('albArtNone');
    const artThumb = document.getElementById('albArtThumb');
    const artImg = document.getElementById('albArtImg');
    const info = document.getElementById('albArtInfo');

    if(tagState.albumArt.preview){
      if(info) info.textContent = 'Uploaded (pending apply)';
      if(artImg && artThumb){
        artImg.src = URL.createObjectURL(tagState.albumArt.preview);
        artThumb.style.display = 'inline-block';
      }
      if(artStatus) artStatus.textContent = 'Uploaded (pending apply)';
      if(artNone) artNone.style.display = 'none';
      return;
    }

    if(!ids.length){
      if(artStatus) artStatus.textContent = 'No artwork';
      if(artNone) artNone.style.display = 'block';
      if(artThumb) artThumb.style.display = 'none';
      return;
    }

    if(ids.length === 1){
      const fid = ids[0];
      const detail = tagState.fileDetails[fid];
      const present = !!(detail?.tags?.artwork && detail.tags.artwork.present);
      if(present){
        if(artImg && artThumb){
          artImg.src = `/api/tagger/file/${encodeURIComponent(fid)}/artwork?cb=${Date.now()}`;
          artThumb.style.display = 'inline-block';
        }
        if(artStatus) artStatus.textContent = 'Present';
        if(artNone) artNone.style.display = 'none';
      }else{
        if(artStatus) artStatus.textContent = 'No artwork';
        if(artNone) artNone.style.display = 'block';
        if(artThumb) artThumb.style.display = 'none';
      }
      return;
    }

    fetchArtInfo(ids).then(()=>{
      const infos = ids.map(id => tagState.artInfoCache[id]);
      const allNone = infos.length && infos.every(i => i && i.present === false);
      const allPresent = infos.length && infos.every(i => i && i.present);
      const sameHash = allPresent && infos.every(i => i.sha256 === infos[0].sha256);
      if(artThumb) artThumb.style.display = 'none';
      if(allNone){
        if(artStatus) artStatus.textContent = 'No artwork in current working set.';
        if(artNone) artNone.style.display = 'block';
      }else if(allPresent && sameHash){
        if(artStatus) artStatus.textContent = 'Artwork is consistent across working set.';
        if(artNone) artNone.style.display = 'none';
        if(artImg && artThumb && ids[0]){
          artImg.src = `/api/tagger/file/${encodeURIComponent(ids[0])}/artwork?cb=${Date.now()}`;
          artThumb.style.display = 'inline-block';
        }
      }else{
        if(artStatus) artStatus.textContent = 'Current working set artwork varies.';
        if(artNone) artNone.style.display = 'none';
      }
    });
  }

  function downloadZip(){
    const ids = tagState.working.map(w => w.id);
    if(!ids.length || tagState.dirty) return;
    const name = document.getElementById('albAlbum').value || 'album';
    const q = encodeURIComponent(ids.join(','));
    const n = encodeURIComponent(name);
    window.location.href = `/api/tagger/album/download?ids=${q}&name=${n}`;
  }

  function downloadSingle(id){
    if(tagState.dirty || !id) return;
    window.location.href = `/api/tagger/file/${encodeURIComponent(id)}/download`;
  }

  function syncBrowserSelection(){
    const browser = document.getElementById('taggingBrowser');
    if(!browser) return;
    browser.querySelectorAll('.browser-item[data-kind="mp3"]').forEach(btn => {
      const selected = tagState.selectedIds.has(btn.dataset.id);
      if(selected){
        btn.classList.add('disabled');
        btn.setAttribute('disabled', 'disabled');
      }else{
        btn.classList.remove('disabled');
        btn.removeAttribute('disabled');
      }
    });
  }

  function isItemVisible(btn){
    if(!btn || btn.disabled) return false;
    if(btn.closest('.file-browser-section')?.classList.contains('collapsed')) return false;
    if(btn.closest('.file-browser-section')?.classList.contains('is-hidden')) return false;
    if(btn.offsetParent === null) return false;
    return true;
  }

  function addAllVisible(){
    const browser = document.getElementById('taggingBrowser');
    if(!browser) return;
    let added = false;
    browser.querySelectorAll('.browser-item[data-kind="mp3"]').forEach(btn => {
      if(!isItemVisible(btn)) return;
      const item = buildItemFromButton(btn);
      if(!item || !item.id) return;
      if(tagState.working.find(w => w.id === item.id)) return;
      tagState.working.push(item);
      added = true;
    });
    if(added){
      if(!tagState.selectedId && tagState.working.length){
        tagState.selectedId = tagState.working[0].id;
      }
      syncWorkingSelected();
      renderWorkingList();
      updateEditorView();
      updateDownloadState();
    }
  }

  function applyScope(scope){
    const browser = document.getElementById('taggingBrowser');
    if(!browser) return;
    const sections = browser.querySelectorAll('.file-browser-section');
    sections.forEach(section => {
      const key = section.dataset.section;
      let show = true;
      if(scope === 'out') show = key === 'mastered';
      if(scope === 'tag') show = key === 'imported';
      if(scope === 'all') show = true;
      section.classList.toggle('is-hidden', !show);
    });
  }

  function setScope(scope){
    const buttons = document.querySelectorAll('#tagScopeBtns button');
    buttons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.scope === scope);
    });
    applyScope(scope);
  }

  function refreshBrowserSections(){
    const browser = document.getElementById('taggingBrowser');
    if(!browser || !window.htmx) return;
    browser.querySelectorAll('.file-browser-list[data-endpoint]').forEach(list => {
      const endpoint = list.dataset.endpoint;
      if(endpoint){
        window.htmx.ajax('GET', endpoint, { target: list, swap: 'innerHTML' });
      }
    });
  }

  document.addEventListener('click', (evt)=>{
    const btn = evt.target.closest('.file-browser .browser-item');
    if(!btn || !btn.dataset.kind || btn.dataset.kind !== 'mp3') return;
    if(btn.disabled) return;
    const item = buildItemFromButton(btn);
    if(item) addToWorking(item);
  });

  document.addEventListener('DOMContentLoaded', ()=>{
    document.getElementById('tagSelectAllBtn')?.addEventListener('click', addAllVisible);
    document.getElementById('tagClearSelBtn')?.addEventListener('click', ()=>{
      tagState.working = [];
      tagState.selectedId = null;
      syncWorkingSelected();
      renderWorkingList();
      updateEditorView();
      tagState.dirty = false;
      updateDownloadState();
    });

    document.getElementById('tagImportBtn')?.addEventListener('click', ()=>{
      document.getElementById('tagImportFile')?.click();
    });

    document.getElementById('tagImportFile')?.addEventListener('change', async (e)=>{
      const status = document.getElementById('tagImportStatus');
      const setStatus = (msg) => { if(status) status.textContent = msg || ''; };
      const file = e.target.files[0];
      if(!file) return;
      setStatus('Uploading...');
      const fd = new FormData();
      fd.append('file', file, file.name);
      try{
        const res = await fetch('/api/tagger/import', { method: 'POST', body: fd });
        if(!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setStatus('Imported.');
        refreshBrowserSections();
        if(data && data.id){
          const displayTitle = (data.basename || '').replace(/\.mp3$/i, '') || data.id;
          const item = {
            id: data.id,
            root: data.root || 'tag',
            basename: data.basename || displayTitle,
            relpath: data.relpath || data.basename || displayTitle,
            full_name: data.relpath || data.basename || displayTitle,
            display_title: displayTitle,
            badges: [{ key: 'format', label: 'Imported', title: 'Imported' }],
          };
          addToWorking(item);
        }
      }catch(_err){
        setStatus('Import failed.');
      }finally{
        e.target.value = '';
      }
    });

    document.querySelectorAll('#tagScopeBtns button').forEach(btn => {
      btn.addEventListener('click', ()=>{
        setScope(btn.dataset.scope || 'out');
      });
    });

    document.getElementById('albArtUploadBtn')?.addEventListener('click', ()=>{
      document.getElementById('albArtFile')?.click();
    });

    document.getElementById('albArtFile')?.addEventListener('change', async (e)=>{
      const file = e.target.files[0];
      if(!file) return;
      const info = document.getElementById('albArtInfo');
      if(info) info.textContent = 'Uploading...';
      const fd = new FormData();
      fd.append('file', file, file.name);
      try{
        const res = await fetch('/api/tagger/artwork', { method: 'POST', body: fd });
        if(res.status === 413) throw new Error('size_exceeded');
        if(!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        tagState.albumArt = { mode: 'apply', uploadId: data.upload_id || data.uploadId, mime: data.mime, size: data.size, preview: file };
        if(info) info.textContent = `Ready to apply (${(data.size / 1024).toFixed(1)} KB)`;
        const status = document.getElementById('albArtStatus');
        if(status) status.textContent = 'Uploaded (pending apply)';
        const thumb = document.getElementById('albArtThumb');
        const img = document.getElementById('albArtImg');
        const none = document.getElementById('albArtNone');
        if(img) img.src = URL.createObjectURL(file);
        if(thumb) thumb.style.display = 'inline-block';
        if(none) none.style.display = 'none';
        markDirty();
      }catch(err){
        if(info){
          info.textContent = err && err.message === 'size_exceeded' ? 'File size exceeded.' : 'Upload failed.';
        }
      }finally{
        e.target.value = '';
      }
    });

    document.getElementById('albArtClearBtn')?.addEventListener('click', ()=>{
      tagState.albumArt = { mode: 'clear', uploadId: null, mime: null, size: 0, preview: null };
      const status = document.getElementById('albArtStatus');
      const info = document.getElementById('albArtInfo');
      const thumb = document.getElementById('albArtThumb');
      const img = document.getElementById('albArtImg');
      const none = document.getElementById('albArtNone');
      if(status) status.textContent = 'Will clear artwork';
      if(info) info.textContent = '';
      if(img) img.src = '';
      if(thumb) thumb.style.display = 'none';
      if(none) none.style.display = 'block';
      markDirty();
    });

    document.getElementById('albAutoNumberBtn')?.addEventListener('click', ()=>{
      const ids = tagState.working.map(w => w.id);
      ids.forEach((id, idx)=>{
        const row = document.querySelector(`tr[data-id="${id}"]`);
        if(row){
          const inp = row.querySelector('input[name="albTrack"]');
          if(inp) inp.value = `${idx + 1}/${ids.length}`;
        }
      });
      markDirty();
    });

    document.getElementById('albApplyBtn')?.addEventListener('click', async ()=>{
      const status = document.getElementById('albStatus');
      const ids = tagState.working.map(w => w.id);
      if(!ids.length){
        if(status) status.textContent = 'No tracks selected.';
        return;
      }
      if(status) status.textContent = 'Applying...';
      const shared = {
        album: document.getElementById('albAlbum').value,
        album_artist: document.getElementById('albAlbumArtist').value,
        artist: document.getElementById('albArtist').value,
        year: document.getElementById('albYear').value,
        genre: document.getElementById('albGenre').value,
        comment: document.getElementById('albComment').value,
        disc: document.getElementById('albDisc').value,
      };
      const tracks = [];
      document.querySelectorAll('#albTableBody tr').forEach(tr => {
        const id = tr.dataset.id;
        const val = (sel)=>{ const el = tr.querySelector(sel); return el ? el.value : ''; };
        tracks.push({
          id,
          track: val('input[name="albTrack"]'),
          title: val('input[name="albTitle"]'),
          artist: val('input[name="albArtist"]'),
          disc: val('input[name="albDisc"]'),
        });
      });
      const payload = {
        file_ids: ids,
        shared,
        tracks,
        artwork: { mode: tagState.albumArt.mode || 'keep', upload_id: tagState.albumArt.uploadId }
      };
      try{
        const res = await fetch('/api/tagger/album/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if(!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if(status) status.textContent = `Updated ${data.updated.length} files${data.errors?.length ? ', errors: ' + data.errors.length : ''}`;
        tagState.dirty = false;
        tagState.albumArt = { mode: 'keep', uploadId: null, mime: null, size: 0, preview: null };
        updateDownloadState();
        refreshBrowserSections();
      }catch(_err){
        if(status) status.textContent = 'Apply failed';
      }
    });

    document.getElementById('albDownloadBtn')?.addEventListener('click', downloadZip);

    setScope('out');
    updateDownloadState();
  });

  document.addEventListener('htmx:afterSwap', (evt)=>{
    const browser = document.getElementById('taggingBrowser');
    if(browser && browser.contains(evt.target)){
      syncBrowserSelection();
    }
  });
})();
