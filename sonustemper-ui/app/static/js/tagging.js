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
    temp: { session: null, items: [] },
  };
  let libraryBrowser = null;

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
      const active = !tagState.dirty && !!tagState.working.length;
      zipBtn.disabled = !active;
      zipBtn.classList.toggle('is-active', active);
    }
    const saveBtn = document.getElementById('albApplyBtn');
    if(saveBtn){
      saveBtn.disabled = !tagState.dirty || !tagState.working.length;
    }
    document.querySelectorAll('.trackDlBtn').forEach(btn => {
      btn.disabled = tagState.dirty;
    });
  }

  function updateSelectedCount(){
    const el = document.getElementById('tagSelectedCount');
    if(el) el.textContent = `${tagState.selectedIds.size} selected`;
  }

  function formatTitleFromRel(rel){
    if(!rel) return '';
    const raw = String(rel);
    const base = raw.split('/').pop() || raw;
    return base.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
  }

  function buildBadgesFromTrack(track){
    const badges = [];
    if(track?.summary?.utility){
      badges.push({ key: 'utility', label: track.summary.utility, title: track.summary.utility });
    }
    if(track?.summary?.voicing){
      badges.push({ key: 'voicing', label: track.summary.voicing, title: `Voicing: ${track.summary.voicing}` });
    }
    if(track?.summary?.loudness_profile){
      badges.push({ key: 'profile', label: track.summary.loudness_profile, title: `Profile: ${track.summary.loudness_profile}` });
    }
    badges.push({ key: 'format', label: 'MP3', title: 'MP3 rendition' });
    return badges;
  }

  function pickMp3Rendition(track){
    if(!track) return null;
    if(Array.isArray(track.renditions)){
      const mp3 = track.renditions.find(r => String(r.format || '').toLowerCase() === 'mp3');
      if(mp3?.rel) return mp3.rel;
    }
    if(track.rel && String(track.rel).toLowerCase().endsWith('.mp3')) return track.rel;
    return null;
  }

  async function resolveTaggerId(rel){
    if(!rel) return null;
    try{
      const res = await fetch(`/api/tagger/resolve?path=${encodeURIComponent(rel)}`, { cache: 'no-store' });
      if(!res.ok) return null;
      const data = await res.json();
      return data.id || null;
    }catch(_err){
      return null;
    }
  }

  function buildItemFromLibrary(song, track, rel, fileId){
    const displayTitle = track?.title || track?.label || song?.title || formatTitleFromRel(rel) || rel;
    const basename = (rel || '').split('/').pop() || displayTitle;
    return {
      id: fileId,
      song_id: song?.song_id || null,
      root: 'library',
      basename,
      relpath: rel,
      full_name: rel,
      display_title: displayTitle,
      badges: buildBadgesFromTrack(track),
    };
  }

  function buildItemFromTemp(rel, fileId){
    const displayTitle = formatTitleFromRel(rel) || rel;
    const basename = (rel || '').split('/').pop() || displayTitle;
    return {
      id: fileId,
      song_id: null,
      root: 'temp',
      basename,
      relpath: rel,
      full_name: rel,
      display_title: displayTitle,
      badges: [{ key: 'temp', label: 'TEMP', title: 'Not in Library' }, { key: 'format', label: 'MP3', title: 'MP3' }],
    };
  }

  function primaryRendition(renditions){
    const list = Array.isArray(renditions) ? renditions : [];
    if (!list.length) return null;
    const prefer = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    for (const fmt of prefer) {
      const hit = list.find((item) => String(item.format || '').toLowerCase() === fmt);
      if (hit) return hit;
    }
    return list[0];
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
      rem.textContent = 'X';
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
        `<button class="btn small ghost trackDlBtn" type="button" data-id="${id}" title="Download track" aria-label="Download">
          <svg class="tag-dl-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 19h16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>`,
        `<input name="albTrack" value="${trackVal || ''}" class="track-input" size="3">`,
        `<input name="albTitle" value="${titleVal || ''}">`,
        `<input name="albArtist" value="${artistVal || ''}">`,
        `<div class="tag-filename" title="${base}">${base}</div>`,
        `<button class="btn small ghost trackRemoveBtn" type="button" data-id="${id}" title="Remove track" aria-label="Remove">X</button>`,
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
    document.querySelectorAll('.trackRemoveBtn').forEach(btn => {
      btn.onclick = ()=> removeFromWorking(btn.dataset.id);
    });

    const allDetails = ids.length && ids.every(id => tagState.fileDetails[id] && tagState.fileDetails[id].tags);
    if(allDetails){
      const tagList = ids.map(id => tagState.fileDetails[id].tags || {});
      const useCommon = (inputId, keys) => {
        const input = document.getElementById(inputId);
        if(!input) return;
        if(tagState.dirty && input.value) return;
        let common = null;
        for(const tags of tagList){
          let val = null;
          for(const key of keys){
            if(tags[key]){
              val = tags[key];
              break;
            }
          }
          if(!val) return;
          if(common === null) common = val;
          if(common !== val) return;
        }
        if(common !== null){
          input.value = common;
        }
      };
      useCommon('albAlbum', ['album']);
      useCommon('albAlbumArtist', ['album_artist', 'albumartist', 'albumArtist']);
      useCommon('albArtist', ['artist']);
      useCommon('albYear', ['year', 'date']);
      useCommon('albGenre', ['genre']);
      useCommon('albDisc', ['disc']);
      useCommon('albComment', ['comment']);
    }

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

  async function ensureMp3(rel){
    if(!rel) return null;
    const res = await fetch('/api/tagger/ensure-mp3', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: rel, bitrate_k: 320 }),
    });
    if(!res.ok) throw new Error(await res.text());
    return await res.json();
  }

  function renderTempList(){
    const list = document.getElementById('tagTempList');
    if(!list) return;
    list.innerHTML = '';
    if(!tagState.temp.items.length){
      list.innerHTML = '<div class="muted">No temp uploads yet.</div>';
      return;
    }
    tagState.temp.items.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'tag-item';
      row.dataset.rel = item.rel;
      const title = document.createElement('div');
      title.className = 'tag-row-title';
      title.textContent = item.name || item.rel;
      title.title = item.rel || item.name;
      row.appendChild(title);
      row.addEventListener('click', async () => {
        const fileId = await resolveTaggerId(item.rel);
        if(!fileId){
          tagToast('MP3 not indexed yet.');
          return;
        }
        addToWorking(buildItemFromTemp(item.rel, fileId));
      });
      list.appendChild(row);
    });
  }

  async function loadTempSession(){
    const session = localStorage.getItem('tagTempSession') || '';
    if(!session) return;
    try{
      const res = await fetch(`/api/tagger/temp-list?session=${encodeURIComponent(session)}`);
      if(!res.ok) return;
      const data = await res.json();
      tagState.temp.session = session;
      tagState.temp.items = Array.isArray(data.items) ? data.items : [];
      renderTempList();
    }catch(_err){
      return;
    }
  }

  function syncBrowserSelection(){
    if(!libraryBrowser) return;
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

  function initTempControls(){
    const btn = document.getElementById('tagTempUploadBtn');
    const input = document.getElementById('tagTempFiles');
    const clearBtn = document.getElementById('tagTempClearBtn');
    if(!btn || !input) return;
    if(btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      input.click();
    });
    if(input.dataset.bound !== '1'){
      input.dataset.bound = '1';
      input.addEventListener('change', async (e)=>{
        const status = document.getElementById('tagTempStatus');
        const setStatus = (msg) => { if(status) status.textContent = msg || ''; };
        const files = Array.from(e.target.files || []);
        if(!files.length) return;
        try{
          let index = 0;
          for (const file of files) {
            index += 1;
            setStatus(`Uploading ${index}/${files.length}...`);
            const fd = new FormData();
            fd.append('files', file, file.name);
            const res = await fetch('/api/tagger/upload-mp3', { method: 'POST', body: fd });
            if(!res.ok) throw new Error(await res.text());
            const data = await res.json();
            tagState.temp.session = data.session || tagState.temp.session;
            if(tagState.temp.session) localStorage.setItem('tagTempSession', tagState.temp.session);
            tagState.temp.items = (data.items || []).concat(tagState.temp.items || []);
            renderTempList();
          }
          setStatus('Ready.');
        }catch(_err){
          setStatus('Upload failed.');
        }finally{
          e.target.value = '';
        }
      });
    }
    if(clearBtn && clearBtn.dataset.bound !== '1'){
      clearBtn.dataset.bound = '1';
      clearBtn.addEventListener('click', async ()=>{
        const session = tagState.temp.session || localStorage.getItem('tagTempSession') || '';
        if(!session) return;
        await fetch('/api/tagger/temp-clear', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session }),
        });
        tagState.temp.items = [];
        tagState.temp.session = null;
        localStorage.removeItem('tagTempSession');
        renderTempList();
      });
    }
  }

  document.addEventListener('DOMContentLoaded', ()=>{
    document.getElementById('tagClearSelBtn')?.addEventListener('click', ()=>{
      tagState.working = [];
      tagState.selectedId = null;
      syncWorkingSelected();
      renderWorkingList();
      updateEditorView();
      tagState.dirty = false;
      updateDownloadState();
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
        const xhr = new XMLHttpRequest();
        const uploadRes = await new Promise((resolve, reject) => {
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
        setStatus('Analyzing...');
        const rel = uploadRes.rel;
        const song = uploadRes.song || null;
        let doneStatus = 'Ready.';
        if (rel) {
          const fileId = await resolveTaggerId(rel);
          if (fileId) {
            const item = buildItemFromLibrary(song, { kind: 'source', rel, summary: {} }, rel, fileId);
            addToWorking(item);
            doneStatus = 'Ready.';
          } else {
            doneStatus = 'Uploaded (MP3 not indexed yet).';
          }
          if (libraryBrowser) libraryBrowser.reload();
        }
        setStatus(doneStatus);
      }catch(_err){
        setStatus('Import failed.');
      }finally{
        e.target.value = '';
      }
    });

    initTempControls();

    const browser = document.getElementById('taggingBrowser');
    if (browser && window.LibraryBrowser) {
      libraryBrowser = window.LibraryBrowser.init(browser, { module: 'tagging' });
      browser.addEventListener('library:select', async (evt) => {
        const { song, track } = evt.detail || {};
        const rel = pickMp3Rendition(track);
        if (!rel) {
          tagToast('No MP3 rendition available for tagging.');
          return;
        }
        const fileId = await resolveTaggerId(rel);
        if (!fileId) {
          tagToast('MP3 not indexed yet.');
          return;
        }
        addToWorking(buildItemFromLibrary(song, track, rel, fileId));
      });
      browser.addEventListener('library:action', (evt) => {
        const { action, song, version, rel } = evt.detail || {};
        if (action === 'import-file') {
          document.getElementById('tagImportFile')?.click();
          return;
        }
        if (action === 'ensure-mp3') {
          const sourceRel = rel || primaryRendition(version?.renditions)?.rel || version?.rel || song?.source?.rel;
          if (!sourceRel) {
            tagToast('No source file for conversion.');
            return;
          }
          tagToast('Converting to MP3...');
          ensureMp3(sourceRel).then(async (data) => {
            const mp3Rel = data.mp3_rel;
            const fileId = await resolveTaggerId(mp3Rel);
            if (!fileId) {
              tagToast('MP3 not indexed yet.');
              return;
            }
            addToWorking(buildItemFromLibrary(song, version || { summary: {} }, mp3Rel, fileId));
            if (libraryBrowser) libraryBrowser.reload();
            tagToast('MP3 ready.');
          }).catch((err) => {
            tagToast(`Convert failed: ${err.message || 'error'}`);
          });
          return;
        }
        if (action === 'open-compare' && song?.source?.rel && version) {
          const rel = primaryRendition(version.renditions)?.rel || version.rel;
          if (!rel) return;
          const url = new URL('/compare', window.location.origin);
          url.searchParams.set('src', song.source.rel);
          url.searchParams.set('proc', rel);
          window.location.assign(`${url.pathname}${url.search}`);
          return;
        }
        if (action === 'delete-version' && song?.song_id && version?.version_id) {
          fetch('/api/library/delete_version', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ song_id: song.song_id, version_id: version.version_id }),
          }).then(() => {
            if (libraryBrowser) libraryBrowser.reload();
          });
        }
      });
    }

    loadTempSession();

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
    if(evt.target && evt.target.querySelector && evt.target.querySelector('#tagTempUploadBtn')){
      initTempControls();
    }
  });
})();
