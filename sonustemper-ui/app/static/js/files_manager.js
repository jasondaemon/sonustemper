(() => {
  const listEl = document.getElementById('filesLibraryList');
  const detailEl = document.getElementById('filesDetailBody');
  const searchInput = document.getElementById('filesSearch');
  const refreshBtn = document.getElementById('filesRefresh');
  const syncBtn = document.getElementById('filesSync');
  const syncStatus = document.getElementById('filesSyncStatus');
  const deleteSelectedBtn = document.getElementById('filesDeleteSelected');
  const downloadSelectedBtn = document.getElementById('filesDownloadSelected');
  const deleteAllBtn = document.getElementById('filesDeleteAll');
  const uploadInput = document.getElementById('filesUploadInput');
  const uploadBtn = document.getElementById('filesUploadBtn');
  const visualizerEl = document.getElementById('filesVisualizer');
  const visualizerCanvas = document.getElementById('filesVisualizerCanvas');
  const visualizerClose = document.getElementById('filesVisualizerClose');
  const visualizerMode = document.getElementById('filesVisualizerMode');

  const state = {
    library: { songs: [] },
    expandedSongs: new Set(),
    expandedVersions: new Set(),
    selectedSongs: new Set(),
    selectedVersions: new Set(),
    selectedRenditions: new Set(),
    active: null,
    search: '',
    libraryBrowser: null,
    visualizer: {
      ctx: null,
      analyser: null,
      source: null,
      raf: null,
      idleTimer: null,
      chromeHidden: false,
      audio: null,
    },
  };

  function notify(msg) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg);
    } else {
      console.info(msg);
    }
  }

  function formatDuration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return null;
    const mins = Math.floor(value / 60);
    const secs = Math.round(value % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  }

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
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

  function renderMetricPills(metrics) {
    if (!metrics || typeof metrics !== 'object') return '';
    const pills = [];
    if (typeof metrics.lufs_i === 'number') pills.push(`LUFS ${metrics.lufs_i.toFixed(1)}`);
    if (typeof metrics.true_peak_dbtp === 'number') pills.push(`TP ${metrics.true_peak_dbtp.toFixed(1)}`);
    if (typeof metrics.crest_factor === 'number') pills.push(`Crest ${metrics.crest_factor.toFixed(1)}`);
    if (typeof metrics.rms_level === 'number') pills.push(`RMS ${metrics.rms_level.toFixed(1)}`);
    if (typeof metrics.peak_level === 'number') pills.push(`Peak ${metrics.peak_level.toFixed(1)}`);
    if (typeof metrics.noise_floor === 'number') pills.push(`Noise ${metrics.noise_floor.toFixed(1)}`);
    return pills.map(text => `<span class="badge badge-param">${text}</span>`).join('');
  }

  function makeKey(...parts) {
    return parts.filter(Boolean).join('::');
  }

  function setSongSelected(song, checked) {
    if (!song?.song_id) return;
    if (checked) {
      state.selectedSongs.add(song.song_id);
      state.selectedVersions.delete(makeKey(song.song_id, '*'));
      (song.versions || []).forEach((version) => {
        state.selectedVersions.delete(makeKey(song.song_id, version.version_id));
        (version.renditions || []).forEach((rendition) => {
          state.selectedRenditions.delete(makeKey(song.song_id, version.version_id, rendition.rel));
        });
      });
    } else {
      state.selectedSongs.delete(song.song_id);
    }
    updateBulkButtons();
    renderList();
  }

  function setVersionSelected(song, version, checked) {
    if (!song?.song_id || !version?.version_id) return;
    const key = makeKey(song.song_id, version.version_id);
    if (checked) {
      state.selectedVersions.add(key);
      state.selectedSongs.delete(song.song_id);
      (version.renditions || []).forEach((rendition) => {
        state.selectedRenditions.delete(makeKey(song.song_id, version.version_id, rendition.rel));
      });
    } else {
      state.selectedVersions.delete(key);
    }
    updateBulkButtons();
    renderList();
  }

  function setRenditionSelected(song, version, rendition, checked) {
    if (!song?.song_id || !version?.version_id || !rendition?.rel) return;
    const key = makeKey(song.song_id, version.version_id, rendition.rel);
    if (checked) {
      state.selectedRenditions.add(key);
      state.selectedSongs.delete(song.song_id);
      state.selectedVersions.delete(makeKey(song.song_id, version.version_id));
    } else {
      state.selectedRenditions.delete(key);
    }
    updateBulkButtons();
    renderList();
  }

  function updateBulkButtons() {
    const hasSelection = state.selectedSongs.size || state.selectedVersions.size || state.selectedRenditions.size;
    if (deleteSelectedBtn) deleteSelectedBtn.disabled = !hasSelection;
    if (downloadSelectedBtn) downloadSelectedBtn.disabled = !hasSelection;
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
      if (state.expandedSongs.has(song.song_id)) {
        (song.versions || []).forEach((version) => {
          listEl.appendChild(renderVersionRow(song, version));
          if (state.expandedVersions.has(makeKey(song.song_id, version.version_id))) {
            (version.renditions || []).forEach((rendition) => {
              listEl.appendChild(renderRenditionRow(song, version, rendition));
            });
          }
        });
      }
    });
  }

  function renderSongRow(song) {
    const row = document.createElement('div');
    row.className = 'files-row files-row--song';
    row.dataset.songId = song.song_id;
    if (state.active?.kind === 'song' && state.active?.songId === song.song_id) {
      row.classList.add('is-active');
    }

    const caret = document.createElement('button');
    caret.type = 'button';
    caret.className = 'files-row-caret';
    caret.textContent = state.expandedSongs.has(song.song_id) ? '▾' : '▸';
    caret.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (state.expandedSongs.has(song.song_id)) {
        state.expandedSongs.delete(song.song_id);
      } else {
        state.expandedSongs.add(song.song_id);
      }
      renderList();
    });
    row.appendChild(caret);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'files-row-check';
    check.checked = state.selectedSongs.has(song.song_id);
    check.addEventListener('click', (evt) => evt.stopPropagation());
    check.addEventListener('change', () => setSongSelected(song, check.checked));
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

    if (state.selectedSongs.has(song.song_id)) row.classList.add('is-selected');
    return row;
  }

  function renderVersionRow(song, version) {
    const row = document.createElement('div');
    row.className = 'files-row files-row--version';
    row.dataset.songId = song.song_id;
    row.dataset.versionId = version.version_id;
    const versionKey = makeKey(song.song_id, version.version_id);
    if (state.active?.kind === 'version' && state.active?.versionId === version.version_id) {
      row.classList.add('is-active');
    }

    const spacer = document.createElement('div');
    spacer.className = 'files-row-spacer';
    row.appendChild(spacer);

    const caret = document.createElement('button');
    caret.type = 'button';
    caret.className = 'files-row-caret files-row-caret--version';
    caret.textContent = state.expandedVersions.has(versionKey) ? '▾' : '▸';
    caret.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (state.expandedVersions.has(versionKey)) {
        state.expandedVersions.delete(versionKey);
      } else {
        state.expandedVersions.add(versionKey);
      }
      renderList();
    });
    row.appendChild(caret);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'files-row-check';
    check.checked = state.selectedVersions.has(versionKey);
    check.addEventListener('click', (evt) => evt.stopPropagation());
    check.addEventListener('change', () => setVersionSelected(song, version, check.checked));
    row.appendChild(check);

    const title = document.createElement('button');
    title.type = 'button';
    title.className = 'files-row-title';
    title.textContent = version.label || version.title || 'Version';
    title.addEventListener('click', () => {
      state.active = { kind: 'version', songId: song.song_id, versionId: version.version_id };
      renderList();
      renderVersionDetail(song, version);
    });
    row.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'files-row-meta';
    if (version.utility) meta.appendChild(makeBadge(version.utility, 'badge-utility'));
    if (version.summary?.voicing) meta.appendChild(makeBadge(version.summary.voicing, 'badge-voicing'));
    if (version.summary?.loudness_profile) meta.appendChild(makeBadge(version.summary.loudness_profile, 'badge-profile'));
    row.appendChild(meta);

    if (state.selectedVersions.has(versionKey)) row.classList.add('is-selected');
    return row;
  }

  function renderRenditionRow(song, version, rendition) {
    const row = document.createElement('div');
    row.className = 'files-row files-row--rendition';
    row.dataset.songId = song.song_id;
    row.dataset.versionId = version.version_id;
    row.dataset.rel = rendition.rel || '';

    const spacer = document.createElement('div');
    spacer.className = 'files-row-spacer files-row-spacer--deep';
    row.appendChild(spacer);

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'files-row-check';
    check.checked = state.selectedRenditions.has(makeKey(song.song_id, version.version_id, rendition.rel));
    check.addEventListener('click', (evt) => evt.stopPropagation());
    check.addEventListener('change', () => setRenditionSelected(song, version, rendition, check.checked));
    row.appendChild(check);

    const title = document.createElement('div');
    title.className = 'files-row-title files-row-title--rendition';
    title.textContent = (rendition.format || 'FILE').toUpperCase();
    row.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'files-row-meta';
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'btn ghost tiny';
    download.textContent = 'Download';
    download.addEventListener('click', (evt) => {
      evt.stopPropagation();
      if (!rendition.rel) return;
      const link = document.createElement('a');
      link.href = `/api/analyze/path?path=${encodeURIComponent(rendition.rel)}`;
      link.setAttribute('download', '');
      link.click();
    });
    meta.appendChild(download);
    row.appendChild(meta);

    row.addEventListener('click', () => {
      state.active = { kind: 'rendition', songId: song.song_id, versionId: version.version_id, rel: rendition.rel };
      renderList();
      renderRenditionDetail(song, version, rendition);
    });

    return row;
  }

  function collectSelections() {
    const selections = [];
    (state.library.songs || []).forEach((song) => {
      if (state.selectedSongs.has(song.song_id)) {
        selections.push({ kind: 'song', song });
        return;
      }
      (song.versions || []).forEach((version) => {
        const versionKey = makeKey(song.song_id, version.version_id);
        if (state.selectedVersions.has(versionKey)) {
          selections.push({ kind: 'version', song, version });
          return;
        }
        (version.renditions || []).forEach((rendition) => {
          const key = makeKey(song.song_id, version.version_id, rendition.rel);
          if (state.selectedRenditions.has(key)) {
            selections.push({ kind: 'rendition', song, version, rendition });
          }
        });
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
      } else if (item.kind === 'rendition') {
        const res = await fetch('/api/library/delete_rendition', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            song_id: item.song.song_id,
            version_id: item.version.version_id,
            rel: item.rendition.rel,
          }),
        });
        if (!res.ok) {
          const msg = await res.text();
          notify(`Failed to delete format: ${msg}`);
        }
      }
    }
    state.selectedSongs.clear();
    state.selectedVersions.clear();
    state.selectedRenditions.clear();
    await loadLibrary();
    refreshBrowser();
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
      } else if (item.kind === 'rendition') {
        if (item.rendition.rel) rels.push(item.rendition.rel);
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
    state.selectedRenditions.clear();
    await loadLibrary();
    refreshBrowser();
  }

  function renderSongDetail(song) {
    if (!detailEl) return;
    const metrics = song?.source?.metrics || {};
    const duration = formatDuration(song?.source?.duration_sec);
    const history = (song.versions || []).map((version) => {
      const label = version.label || version.title || version.kind || 'Version';
      const util = version.utility ? `(${version.utility})` : '';
      return `<div class="files-history-row">${escapeHtml(label)} ${escapeHtml(util)}</div>`;
    }).join('');
    detailEl.innerHTML = `
      <div class="detail-section">
        <div class="detail-title">Song</div>
        <div class="detail-row">
          <label class="control-label">Title</label>
          <div class="detail-value">${escapeHtml(song?.title || '')}</div>
        </div>
        <div class="detail-player">
          <audio id="filesDetailAudio" controls preload="metadata"></audio>
          <button type="button" class="btn ghost tiny" id="filesVisualizerOpen">Visualizer</button>
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
        <div class="detail-subtitle">History</div>
        <div class="files-history-list">${history || '<div class="muted">No versions yet.</div>'}</div>
      </div>
    `;
    const audio = detailEl.querySelector('#filesDetailAudio');
    if (audio && song?.source?.rel) {
      audio.src = `/api/analyze/path?path=${encodeURIComponent(song.source.rel)}`;
    }
    detailEl.querySelector('#filesSongAnalyze')?.addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/analyze', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongCompare')?.addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/compare', window.location.origin);
      url.searchParams.set('src', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongAi')?.addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/ai', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesSongEq')?.addEventListener('click', () => {
      if (!song?.source?.rel) return;
      const url = new URL('/eq', window.location.origin);
      url.searchParams.set('path', song.source.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVisualizerOpen')?.addEventListener('click', () => {
      openVisualizer(audio);
    });
  }

  function renderVersionDetail(song, version) {
    if (!detailEl) return;
    const renditions = Array.isArray(version?.renditions) ? version.renditions : [];
    const primary = primaryRendition(renditions) || {};
    const metrics = version?.metrics || {};
    const downloads = collectRenditionFormats(renditions).map((rendition) => {
      if (!rendition.rel) return '';
      return `<a href="/api/analyze/path?path=${encodeURIComponent(rendition.rel)}" class="badge badge-format" download>${escapeHtml(String(rendition.format || 'FILE').toUpperCase())}</a>`;
    }).join('');
    detailEl.innerHTML = `
      <div class="detail-section">
        <div class="detail-title">Version</div>
        <div class="pill-row">
          ${version.utility ? `<span class="badge badge-utility">${escapeHtml(version.utility)}</span>` : ''}
          ${version.summary?.voicing ? `<span class="badge badge-voicing">${escapeHtml(version.summary.voicing)}</span>` : ''}
          ${version.summary?.loudness_profile ? `<span class="badge badge-profile">${escapeHtml(version.summary.loudness_profile)}</span>` : ''}
        </div>
        <div class="detail-player">
          <audio id="filesDetailAudio" controls preload="metadata"></audio>
          <button type="button" class="btn ghost tiny" id="filesVisualizerOpen">Visualizer</button>
        </div>
        <div class="pill-row">${renderMetricPills(metrics)}</div>
        <div class="detail-downloads">${downloads || '<span class="muted">No renditions.</span>'}</div>
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
    detailEl.querySelector('#filesVersionAnalyze')?.addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/analyze', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionCompare')?.addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/compare', window.location.origin);
      if (song?.source?.rel) url.searchParams.set('src', song.source.rel);
      url.searchParams.set('proc', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionAi')?.addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/ai', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionEq')?.addEventListener('click', () => {
      if (!primary.rel) return;
      const url = new URL('/eq', window.location.origin);
      url.searchParams.set('path', primary.rel);
      window.location.assign(`${url.pathname}${url.search}`);
    });
    detailEl.querySelector('#filesVersionDelete')?.addEventListener('click', async () => {
      if (!confirm('Delete this version?')) return;
      await fetch('/api/library/delete_version', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id, version_id: version.version_id }),
      });
      state.active = { kind: 'song', songId: song.song_id };
      await loadLibrary();
      refreshBrowser();
    });
    detailEl.querySelector('#filesVisualizerOpen')?.addEventListener('click', () => {
      openVisualizer(audio);
    });
  }

  function renderRenditionDetail(song, version, rendition) {
    if (!detailEl) return;
    detailEl.innerHTML = `
      <div class="detail-section">
        <div class="detail-title">Format</div>
        <div class="pill-row">
          <span class="badge badge-format">${escapeHtml(String(rendition.format || 'FILE').toUpperCase())}</span>
        </div>
        <div class="detail-player">
          <audio id="filesDetailAudio" controls preload="metadata"></audio>
          <button type="button" class="btn ghost tiny" id="filesVisualizerOpen">Visualizer</button>
        </div>
        <div class="detail-actions">
          <a class="btn ghost tiny" href="/api/analyze/path?path=${encodeURIComponent(rendition.rel || '')}" download>Download</a>
          <button type="button" class="btn danger tiny" id="filesRenditionDelete">Delete Format</button>
        </div>
      </div>
    `;
    const audio = detailEl.querySelector('#filesDetailAudio');
    if (audio && rendition?.rel) {
      audio.src = `/api/analyze/path?path=${encodeURIComponent(rendition.rel)}`;
    }
    detailEl.querySelector('#filesRenditionDelete')?.addEventListener('click', async () => {
      if (!confirm('Delete this format?')) return;
      const res = await fetch('/api/library/delete_rendition', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song_id: song.song_id, version_id: version.version_id, rel: rendition.rel }),
      });
      if (!res.ok) {
        const msg = await res.text();
        notify(`Failed to delete format: ${msg}`);
        return;
      }
      state.active = { kind: 'version', songId: song.song_id, versionId: version.version_id };
      await loadLibrary();
      refreshBrowser();
    });
    detailEl.querySelector('#filesVisualizerOpen')?.addEventListener('click', () => {
      openVisualizer(audio);
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

  function setVisualizerChromeHidden(hidden) {
    if (!visualizerEl) return;
    visualizerEl.classList.toggle('is-idle', hidden);
    state.visualizer.chromeHidden = hidden;
  }

  function resetVisualizerIdleTimer() {
    if (!visualizerEl) return;
    if (state.visualizer.idleTimer) clearTimeout(state.visualizer.idleTimer);
    setVisualizerChromeHidden(false);
    state.visualizer.idleTimer = setTimeout(() => setVisualizerChromeHidden(true), 10000);
  }

  function openVisualizer(audioEl) {
    if (!visualizerEl || !visualizerCanvas || !audioEl) return;
    state.visualizer.audio = audioEl;
    if (!state.visualizer.ctx) {
      state.visualizer.ctx = new (window.AudioContext || window.webkitAudioContext)();
      state.visualizer.analyser = state.visualizer.ctx.createAnalyser();
      state.visualizer.analyser.fftSize = 2048;
    }
    if (state.visualizer.ctx.state === 'suspended') {
      state.visualizer.ctx.resume().catch(() => {});
    }
    if (!state.visualizer.source || state.visualizer.mediaEl !== audioEl) {
      try {
        if (state.visualizer.source) state.visualizer.source.disconnect();
        state.visualizer.source = state.visualizer.ctx.createMediaElementSource(audioEl);
        state.visualizer.source.connect(state.visualizer.analyser);
        state.visualizer.analyser.connect(state.visualizer.ctx.destination);
        state.visualizer.mediaEl = audioEl;
      } catch (_err) {
        // ignore duplicate node errors
      }
    }
    visualizerEl.hidden = false;
    resetVisualizerIdleTimer();
    drawVisualizer();
  }

  function closeVisualizer() {
    if (!visualizerEl) return;
    visualizerEl.hidden = true;
    if (state.visualizer.raf) cancelAnimationFrame(state.visualizer.raf);
    state.visualizer.raf = null;
  }

  function drawVisualizer() {
    if (!visualizerEl || visualizerEl.hidden || !visualizerCanvas || !state.visualizer.analyser) return;
    const ctx = visualizerCanvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const width = visualizerCanvas.clientWidth || 600;
    const height = visualizerCanvas.clientHeight || 300;
    visualizerCanvas.width = Math.floor(width * dpr);
    visualizerCanvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const mode = visualizerMode?.value || 'osc';
    const analyser = state.visualizer.analyser;
    if (mode === 'osc') {
      const buffer = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(buffer);
      ctx.strokeStyle = '#3fe0c5';
      ctx.lineWidth = 2;
      ctx.beginPath();
      buffer.forEach((val, idx) => {
        const x = (idx / (buffer.length - 1)) * width;
        const y = (1 - (val * 0.5 + 0.5)) * height;
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    } else {
      const buffer = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(buffer);
      if (mode === 'bars') {
        const barWidth = Math.max(2, width / buffer.length * 2.5);
        buffer.forEach((val, idx) => {
          const x = idx * barWidth;
          const h = (val / 255) * height;
          ctx.fillStyle = '#4b7bec';
          ctx.fillRect(x, height - h, barWidth - 1, h);
        });
      } else {
        const cx = width / 2;
        const cy = height / 2;
        const radius = Math.min(width, height) * 0.2;
        buffer.forEach((val, idx) => {
          const angle = (idx / buffer.length) * Math.PI * 2;
          const mag = (val / 255) * radius;
          const x = cx + Math.cos(angle) * (radius + mag);
          const y = cy + Math.sin(angle) * (radius + mag);
          ctx.strokeStyle = '#ffb347';
          ctx.beginPath();
          ctx.moveTo(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
          ctx.lineTo(x, y);
          ctx.stroke();
        });
      }
    }
    state.visualizer.raf = requestAnimationFrame(drawVisualizer);
  }

  function bindVisualizerEvents() {
    if (!visualizerEl) return;
    visualizerEl.addEventListener('mousemove', resetVisualizerIdleTimer);
    visualizerEl.addEventListener('click', (evt) => {
      if (evt.target === visualizerEl || evt.target.classList.contains('files-visualizer-backdrop')) {
        closeVisualizer();
      }
    });
    document.addEventListener('keydown', (evt) => {
      if (evt.key === 'Escape' && !visualizerEl.hidden) closeVisualizer();
    });
    visualizerClose?.addEventListener('click', closeVisualizer);
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    if (syncStatus) syncStatus.textContent = 'Uploading…';
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch('/api/analyze-upload', { method: 'POST', body: form });
      if (!res.ok) {
        notify(`Upload failed: ${file.name}`);
      }
    }
    if (syncStatus) syncStatus.textContent = 'Upload complete.';
    await loadLibrary();
    refreshBrowser();
  }

  async function runSync() {
    if (!syncBtn) return;
    syncBtn.disabled = true;
    if (syncStatus) syncStatus.textContent = 'Scanning…';
    try {
      const res = await fetch('/api/library/import_scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ delete_after_import: true }),
      });
      if (!res.ok) throw new Error(await res.text());
      const summary = await res.json();
      await loadLibrary();
      refreshBrowser();
      if (syncStatus) {
        const parts = [];
        parts.push(`Imported ${summary.imported || 0} song(s)`);
        parts.push(`skipped ${summary.skipped || 0}`);
        if (summary.errors?.length) parts.push(`errors ${summary.errors.length}`);
        syncStatus.textContent = parts.join(' · ');
      }
    } catch (_err) {
      if (syncStatus) syncStatus.textContent = 'Scan failed. Check logs.';
    } finally {
      syncBtn.disabled = false;
    }
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
      } else if (state.active?.kind === 'rendition') {
        const song = state.library.songs.find(s => s.song_id === state.active.songId);
        const version = song?.versions?.find(v => v.version_id === state.active.versionId);
        const rendition = version?.renditions?.find(r => r.rel === state.active.rel);
        if (song && version && rendition) renderRenditionDetail(song, version, rendition);
      }
    } catch (_err) {
      if (listEl) listEl.innerHTML = '<div class="muted">Library unavailable.</div>';
    }
  }

  function refreshBrowser() {
    if (state.libraryBrowser?.reload) state.libraryBrowser.reload();
  }

  function initBrowser() {
    const container = document.getElementById('filesLibraryBrowser');
    if (!container || !window.LibraryBrowser) return;
    state.libraryBrowser = window.LibraryBrowser.init(container, { module: 'files' });
    container.addEventListener('library:select', (evt) => {
      const song = evt.detail?.song;
      const track = evt.detail?.track;
      if (!song) return;
      if (track?.kind === 'source' || !track?.version_id) {
        state.active = { kind: 'song', songId: song.song_id };
        renderList();
        renderSongDetail(song);
      } else {
        state.active = { kind: 'version', songId: song.song_id, versionId: track.version_id };
        renderList();
        renderVersionDetail(song, track);
      }
    });
    container.addEventListener('library:action', (evt) => {
      if (evt.detail?.action === 'import-file') {
        uploadInput?.click();
      }
    });
  }

  bindVisualizerEvents();

  searchInput?.addEventListener('input', () => {
    state.search = searchInput.value || '';
    renderList();
  });
  refreshBtn?.addEventListener('click', loadLibrary);
  syncBtn?.addEventListener('click', runSync);
  deleteSelectedBtn?.addEventListener('click', deleteSelected);
  downloadSelectedBtn?.addEventListener('click', downloadSelected);
  deleteAllBtn?.addEventListener('click', deleteAllSongs);
  uploadBtn?.addEventListener('click', () => uploadInput?.click());
  uploadInput?.addEventListener('change', () => uploadFiles(uploadInput.files));

  loadLibrary();
  initBrowser();
})();
