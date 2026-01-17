(function(){
  const listEl = document.getElementById('filesLibraryList');
  const detailEl = document.getElementById('filesDetailBody');
  const searchInput = document.getElementById('filesSearch');
  const refreshBtn = document.getElementById('filesRefresh');
  const syncBtn = document.getElementById('filesSync');
  const syncStatus = document.getElementById('filesSyncStatus');
  const deleteSelectedBtn = document.getElementById('filesDeleteSelected');
  const downloadSelectedBtn = document.getElementById('filesDownloadSelected');
  const deleteAllBtn = document.getElementById('filesDeleteAll');

  const state = {
    library: { songs: [] },
    expanded: new Set(),
    selectedSongs: new Set(),
    selectedVersions: new Set(),
    active: null,
    search: '',
  };

  function formatDuration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return null;
    const mins = Math.floor(value / 60);
    const secs = Math.round(value % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  }

  function makeVersionKey(songId, versionId) {
    return `${songId || 'song'}::${versionId || 'version'}`;
  }

  function renderMetricPills(metrics) {
    if (!metrics) return '';
    const pills = [];
    if (typeof metrics.lufs_i === 'number') pills.push(`LUFS ${metrics.lufs_i.toFixed(1)}`);
    if (typeof metrics.true_peak_db === 'number') pills.push(`TP ${metrics.true_peak_db.toFixed(1)}`);
    if (typeof metrics.crest_db === 'number') pills.push(`Crest ${metrics.crest_db.toFixed(1)}`);
    if (typeof metrics.rms_db === 'number') pills.push(`RMS ${metrics.rms_db.toFixed(1)}`);
    if (typeof metrics.peak_dbfs === 'number') pills.push(`Peak ${metrics.peak_dbfs.toFixed(1)}`);
    if (typeof metrics.clipped_samples === 'number' && metrics.clipped_samples > 0) {
      pills.push(`Clips ${metrics.clipped_samples}`);
    }
    if (!pills.length) return '';
    return pills.map(text => `<span class="badge badge-param">${text}</span>`).join('');
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function primaryRendition(renditions) {
    const list = Array.isArray(renditions) ? renditions : [];
    if (!list.length) return null;
    const prefer = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    for (const fmt of prefer) {
      const hit = list.find(item => String(item.format || '').toLowerCase() === fmt);
      if (hit) return hit;
    }
    return list[0];
  }

  function renderList() {
    if (!listEl) return;
    listEl.innerHTML = '';
    const query = state.search.trim().toLowerCase();
    const songs = (state.library.songs || []).filter(song => songMatches(song, query));
    if (!songs.length) {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No items.';
      listEl.appendChild(empty);
      return;
    }
    songs.forEach((song) => {
      listEl.appendChild(renderSongRow(song));
      if (state.expanded.has(song.song_id)) {
        (song.versions || []).forEach((version) => {
          listEl.appendChild(renderVersionRow(song, version));
        });
      }
    });
  }

  function songMatches(song, query) {
    if (!query) return true;
    const title = String(song.title || '').toLowerCase();
    if (title.includes(query)) return true;
    return (song.versions || []).some((version) => {
      const label = String(version.label || version.title || version.kind || '').toLowerCase();
      const voicing = String(version.summary?.voicing || '').toLowerCase();
      const profile = String(version.summary?.loudness_profile || '').toLowerCase();
      return label.includes(query) || voicing.includes(query) || profile.includes(query);
    });
  }

  function renderSongRow(song) {
    const row = document.createElement('div');
    row.className = 'files-row files-row--song';
    row.dataset.songId = song.song_id;
    if (state.active?.songId === song.song_id && state.active?.kind === 'song') {
      row.classList.add('is-active');
    }

    const caret = document.createElement('button');
    caret.type = 'button';
    caret.className = 'files-row-caret';
    caret.textContent = state.expanded.has(song.song_id) ? '▾' : '▸';
    caret.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (state.expanded.has(song.song_id)) {
        state.expanded.delete(song.song_id);
      } else {
        state.expanded.add(song.song_id);
      }
      renderList();
    });
    row.appendChild(caret);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'files-row-check';
    check.checked = state.selectedSongs.has(song.song_id);
    check.addEventListener('change', () => {
      setSongSelected(song, check.checked);
    });
    row.appendChild(check);

    const title = document.createElement('button');
    title.type = 'button';
    title.className = 'files-row-title';
    title.textContent = song.title || 'Untitled';
    title.title = song.title || '';
    title.addEventListener('click', () => {
      state.active = { kind: 'song', songId: song.song_id };
      renderList();
      renderSongDetail(song);
    });
    row.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'files-row-meta';
    const duration = formatDuration(song.source?.duration_sec);
    if (duration) meta.appendChild(makeBadge(duration, 'badge-format'));
    if (song.source?.format && isLossy(song.source.format)) {
      meta.appendChild(makeBadge(String(song.source.format).toUpperCase(), 'badge-format'));
    }
    row.appendChild(meta);

    if (state.selectedSongs.has(song.song_id)) {
      row.classList.add('is-selected');
    }

    return row;
  }

  function renderVersionRow(song, version) {
    const row = document.createElement('div');
    row.className = 'files-row files-row--version';
    row.dataset.songId = song.song_id;
    row.dataset.versionId = version.version_id;
    const versionKey = makeVersionKey(song.song_id, version.version_id);
    if (state.active?.kind === 'version' && state.active?.versionId === version.version_id) {
      row.classList.add('is-active');
    }

    const spacer = document.createElement('div');
    spacer.className = 'files-row-spacer';
    row.appendChild(spacer);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'files-row-check';
    check.checked = state.selectedVersions.has(versionKey);
    check.addEventListener('change', () => {
      setVersionSelected(song, version, check.checked);
    });
    row.appendChild(check);

    const title = document.createElement('button');
    title.type = 'button';
    title.className = 'files-row-title';
    title.textContent = version.kind === 'master' ? 'Master' : (version.label || version.title || 'Version');
    title.addEventListener('click', () => {
      state.active = { kind: 'version', songId: song.song_id, versionId: version.version_id };
      renderList();
      renderVersionDetail(song, version);
    });
    row.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'files-row-meta';
    if (version.summary?.voicing) meta.appendChild(makeBadge(version.summary.voicing, 'badge-voicing'));
    if (version.summary?.loudness_profile) meta.appendChild(makeBadge(version.summary.loudness_profile, 'badge-profile'));
    row.appendChild(meta);

    if (state.selectedVersions.has(versionKey)) {
      row.classList.add('is-selected');
    }

    return row;
  }

  function isLossy(format) {
    const fmt = String(format || '').toLowerCase();
    return ['mp3', 'ogg', 'aac', 'm4a'].includes(fmt);
  }

  function makeBadge(text, cls) {
    const badge = document.createElement('span');
    badge.className = `badge ${cls || ''}`.trim();
    badge.textContent = text;
    return badge;
  }

  function setSongSelected(song, checked) {
    if (!song?.song_id) return;
    if (checked) {
      state.selectedSongs.add(song.song_id);
      (song.versions || []).forEach((version) => {
        state.selectedVersions.add(makeVersionKey(song.song_id, version.version_id));
      });
    } else {
      state.selectedSongs.delete(song.song_id);
      (song.versions || []).forEach((version) => {
        state.selectedVersions.delete(makeVersionKey(song.song_id, version.version_id));
      });
    }
    updateBulkButtons();
    renderList();
  }

  function setVersionSelected(song, version, checked) {
    if (!song?.song_id || !version?.version_id) return;
    const key = makeVersionKey(song.song_id, version.version_id);
    if (checked) {
      state.selectedVersions.add(key);
    } else {
      state.selectedVersions.delete(key);
    }
    if (state.selectedSongs.has(song.song_id)) {
      state.selectedSongs.delete(song.song_id);
    }
    updateBulkButtons();
    renderList();
  }

  function updateBulkButtons() {
    const hasSelection = state.selectedSongs.size || state.selectedVersions.size;
    if (deleteSelectedBtn) deleteSelectedBtn.disabled = !hasSelection;
    if (downloadSelectedBtn) downloadSelectedBtn.disabled = !hasSelection;
  }

  function collectSelections() {
    const selections = [];
    (state.library.songs || []).forEach((song) => {
      if (state.selectedSongs.has(song.song_id)) {
        selections.push({ kind: 'song', song });
        return;
      }
      (song.versions || []).forEach((version) => {
        const key = makeVersionKey(song.song_id, version.version_id);
        if (state.selectedVersions.has(key)) {
          selections.push({ kind: 'version', song, version });
        }
      });
    });
    return selections;
  }

  async function deleteSelected() {
    const selections = collectSelections();
    if (!selections.length) return;
    if (!confirm(`Delete ${selections.length} selected item(s)?`)) return;
    for (const item of selections) {
      if (item.kind === 'song') {
        await fetch('/api/library/delete_song', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: item.song.song_id }),
        });
      } else if (item.kind === 'version') {
        await fetch('/api/library/delete_version', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: item.song.song_id, version_id: item.version.version_id }),
        });
      }
    }
    state.selectedSongs.clear();
    state.selectedVersions.clear();
    await loadLibrary();
  }

  function downloadSelected() {
    const selections = collectSelections();
    const rels = [];
    selections.forEach((item) => {
      if (item.kind === 'song') {
        if (item.song?.source?.rel) rels.push(item.song.source.rel);
        (item.song.versions || []).forEach((version) => {
          (version.renditions || []).forEach((rendition) => {
            if (rendition.rel) rels.push(rendition.rel);
          });
        });
      } else if (item.kind === 'version') {
        (item.version.renditions || []).forEach((rendition) => {
          if (rendition.rel) rels.push(rendition.rel);
        });
      }
    });
    rels.forEach((rel) => {
      const link = document.createElement('a');
      link.href = `/api/analyze/path?path=${encodeURIComponent(rel)}`;
      link.setAttribute('download', '');
      link.click();
    });
  }

  async function deleteAllSongs() {
    if (!confirm('Delete ALL songs from the library?')) return;
    for (const song of state.library.songs || []) {
      await fetch('/api/library/delete_song', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id }),
      });
    }
    state.selectedSongs.clear();
    state.selectedVersions.clear();
    await loadLibrary();
  }

  function renderSongDetail(song) {
    if (!detailEl) return;
    const metrics = song?.source?.metrics || {};
    const duration = formatDuration(song?.source?.duration_sec);
    detailEl.innerHTML = `
      <div class="detail-section">
        <div class="detail-title">Song</div>
        <div class="detail-row">
          <label class="control-label">Title</label>
          <input type="text" id="filesSongTitle" value="${escapeHtml(song?.title || '')}">
          <button type="button" class="btn primary tiny" id="filesSongRename">Save Rename</button>
          <button type="button" class="btn danger tiny" id="filesSongDelete">Delete Song</button>
        </div>
        <div class="detail-player">
          <audio id="filesDetailAudio" controls preload="metadata"></audio>
        </div>
        <div class="pill-row">
          ${duration ? `<span class="badge badge-format">${duration}</span>` : ''}
          ${song?.source?.format ? `<span class="badge badge-format">${escapeHtml(String(song.source.format).toUpperCase())}</span>` : ''}
          ${renderMetricPills(metrics)}
        </div>
        <div class="detail-actions">
          <button type="button" class="btn ghost tiny" id="filesSongAnalyze">Analyze</button>
          <button type="button" class="btn ghost tiny" id="filesSongCompare">Compare</button>
          <button type="button" class="btn ghost tiny" id="filesSongAi">AI Toolkit</button>
          <button type="button" class="btn ghost tiny" id="filesSongEq">Open in EQ</button>
        </div>
      </div>
    `;
    const audio = detailEl.querySelector('#filesDetailAudio');
    if (audio && song?.source?.rel) {
      audio.src = `/api/analyze/path?path=${encodeURIComponent(song.source.rel)}`;
    }
    detailEl.querySelector('#filesSongRename').addEventListener('click', async () => {
      const title = detailEl.querySelector('#filesSongTitle').value || '';
      await fetch('/api/library/rename_song', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id, title }),
      });
      await loadLibrary();
    });
    detailEl.querySelector('#filesSongDelete').addEventListener('click', async () => {
      if (!confirm('Delete this song and all versions?')) return;
      await fetch('/api/library/delete_song', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id }),
      });
      state.active = null;
      await loadLibrary();
      detailEl.innerHTML = '<div class="muted">Select a song or version to view details.</div>';
    });
    detailEl.querySelector('#filesSongAnalyze').addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/analyze', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongCompare').addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/compare', window.location.origin);
      url.searchParams.set('src', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongAi').addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/ai', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongEq').addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/eq', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
  }

  function renderVersionDetail(song, version) {
    if (!detailEl) return;
    const renditions = Array.isArray(version?.renditions) ? version.renditions : [];
    const primary = primaryRendition(renditions) || {};
    const metrics = version?.metrics || {};
    detailEl.innerHTML = `
      <div class="detail-section">
        <div class="detail-title">Version</div>
        <div class="pill-row">
          ${version.summary?.voicing ? `<span class="badge badge-voicing">${escapeHtml(version.summary.voicing)}</span>` : ''}
          ${version.summary?.loudness_profile ? `<span class="badge badge-profile">${escapeHtml(version.summary.loudness_profile)}</span>` : ''}
        </div>
        <div class="detail-player">
          <audio id="filesDetailAudio" controls preload="metadata"></audio>
        </div>
        <div class="pill-row">
          ${renderMetricPills(metrics)}
        </div>
        <div class="detail-downloads" id="filesRenditions"></div>
        <div class="detail-actions">
          <button type="button" class="btn ghost tiny" id="filesVersionAnalyze">Analyze</button>
          <button type="button" class="btn ghost tiny" id="filesVersionCompare">Compare</button>
          <button type="button" class="btn ghost tiny" id="filesVersionAi">AI Toolkit</button>
          <button type="button" class="btn ghost tiny" id="filesVersionEq">Open in EQ</button>
          <button type="button" class="btn danger tiny" id="filesVersionDelete">Delete Version</button>
        </div>
      </div>
    `;
    const audio = detailEl.querySelector('#filesDetailAudio');
    if (audio && primary.rel) {
      audio.src = `/api/analyze/path?path=${encodeURIComponent(primary.rel)}`;
    }
    const renditionsWrap = detailEl.querySelector('#filesRenditions');
    collectRenditionFormats(renditions).forEach((rendition) => {
      if (!rendition.rel) return;
      const link = document.createElement('a');
      link.href = `/api/analyze/path?path=${encodeURIComponent(rendition.rel)}`;
      link.className = 'badge badge-format';
      link.textContent = String(rendition.format || 'FILE').toUpperCase();
      link.setAttribute('download', '');
      renditionsWrap.appendChild(link);
    });
    detailEl.querySelector('#filesVersionAnalyze').addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/analyze', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionCompare').addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/compare', window.location.origin);
      if (song?.source?.rel) url.searchParams.set('src', song.source.rel);
      url.searchParams.set('proc', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionAi').addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/ai', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionEq').addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/eq', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionDelete').addEventListener('click', async () => {
      if (!confirm('Delete this version?')) return;
      await fetch('/api/library/delete_version', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id, version_id: version.version_id }),
      });
      state.active = { kind: 'song', songId: song.song_id };
      await loadLibrary();
    });
  }

  function collectRenditionFormats(renditions) {
    const list = Array.isArray(renditions) ? renditions : [];
    const ordered = [];
    const prefer = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    prefer.forEach((fmt) => {
      list.forEach((item) => {
        if (String(item.format || '').toLowerCase() === fmt) ordered.push(item);
      });
    });
    return ordered.length ? ordered : list;
  }

  async function loadLibrary() {
    try {
      const res = await fetch('/api/library', { cache: 'no-store' });
      if (!res.ok) throw new Error('library_failed');
      const data = await res.json();
      state.library = data || { songs: [] };
      renderList();
      updateBulkButtons();
      if (state.active?.kind === 'song') {
        const song = state.library.songs.find(s => s.song_id === state.active.songId);
        if (song) renderSongDetail(song);
      } else if (state.active?.kind === 'version') {
        const song = state.library.songs.find(s => s.song_id === state.active.songId);
        const version = song?.versions?.find(v => v.version_id === state.active.versionId);
        if (song && version) renderVersionDetail(song, version);
      }
    } catch (_err) {
      if (listEl) listEl.innerHTML = '<div class="muted">Library unavailable.</div>';
    }
  }

  async function runSync() {
    if (!syncBtn) return;
    syncBtn.disabled = true;
    if (syncStatus) syncStatus.textContent = 'Scanning…';
    try {
      const res = await fetch('/api/library/sync', { method: 'POST' });
      if (!res.ok) throw new Error('sync_failed');
      const summary = await res.json();
      await loadLibrary();
      if (syncStatus) {
        const parts = [];
        parts.push(`Imported ${summary.imported_songs || 0} song(s)`);
        parts.push(`versions ${summary.imported_versions || 0}`);
        parts.push(`removed ${summary.removed_songs || 0}/${summary.removed_versions || 0}`);
        parts.push(`inbox ${summary.imported_from_inbox || 0}`);
        syncStatus.textContent = parts.join(' · ');
      }
    } catch (_err) {
      if (syncStatus) syncStatus.textContent = 'Scan failed. Check logs.';
    } finally {
      syncBtn.disabled = false;
    }
  }

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      state.search = searchInput.value || '';
      renderList();
    });
  }
  if (refreshBtn) refreshBtn.addEventListener('click', loadLibrary);
  if (syncBtn) syncBtn.addEventListener('click', runSync);
  if (deleteSelectedBtn) deleteSelectedBtn.addEventListener('click', deleteSelected);
  if (downloadSelectedBtn) downloadSelectedBtn.addEventListener('click', downloadSelected);
  if (deleteAllBtn) deleteAllBtn.addEventListener('click', deleteAllSongs);

  loadLibrary();
})();
