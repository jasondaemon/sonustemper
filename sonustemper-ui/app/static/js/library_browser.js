(() => {
  const VIEW_KEY = 'sonustemper.libraryView';
  const SORT_KEY = 'sonustemper.librarySort';
  const EXPAND_KEY = 'sonustemper.libraryExpanded';
  const SEARCH_KEY = 'sonustemper.librarySearch';

  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_err) {
      return fallback;
    }
  }

  function saveJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (_err) {
      return;
    }
  }

  function normalizeText(raw) {
    return String(raw || '').toLowerCase();
  }

  function formatDuration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return null;
    const mins = Math.floor(value / 60);
    const secs = Math.round(value % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  }

  function makeBadge(text, className) {
    const span = document.createElement('span');
    span.className = `badge ${className || ''}`.trim();
    span.textContent = text;
    return span;
  }

  function isLossy(format) {
    const fmt = String(format || '').toLowerCase();
    return ['mp3', 'ogg', 'aac', 'm4a'].includes(fmt);
  }

  function primaryRendition(renditions) {
    const list = Array.isArray(renditions) ? renditions : [];
    if (!list.length) return null;
    const prefer = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    for (const fmt of prefer) {
      const hit = list.find((item) => String(item.format || '').toLowerCase() === fmt);
      if (hit) return hit;
    }
    return list[0];
  }

  function metricsTooltip(metrics) {
    if (!metrics || typeof metrics !== 'object') return '';
    const parts = [];
    const lufs = metrics.lufs_i ?? metrics.lufs;
    const tp = metrics.true_peak_db ?? metrics.true_peak;
    const crest = metrics.crest_db ?? metrics.crest;
    if (typeof lufs === 'number') parts.push(`LUFS: ${lufs.toFixed(1)}`);
    if (typeof tp === 'number') parts.push(`TP: ${tp.toFixed(1)} dBTP`);
    if (typeof crest === 'number') parts.push(`Crest: ${crest.toFixed(1)} dB`);
    return parts.join(' · ');
  }

  function renderLibrary(container, options = {}) {
    const module = options.module || container.dataset.module || 'generic';
    const state = {
      view: localStorage.getItem(VIEW_KEY) || 'simple',
      sort: localStorage.getItem(SORT_KEY) || 'recent',
      expanded: loadJson(EXPAND_KEY, {}),
      search: localStorage.getItem(SEARCH_KEY) || '',
      songs: [],
      disabledSongIds: new Set(),
    };

    container.innerHTML = `
      <div class="library-browser">
        <div class="library-browser-controls">
          <input type="search" class="library-search" placeholder="Search songs or versions">
          <select class="library-sort">
            <option value="recent">Recent</option>
            <option value="az">A–Z</option>
            <option value="versions">Most Versions</option>
          </select>
          <div class="pill-toggle library-view-toggle">
            <button class="btn ghost tiny" data-view="simple" type="button">Simple</button>
            <button class="btn ghost tiny" data-view="extended" type="button">Extended</button>
          </div>
          <button class="btn ghost tiny library-import-btn" type="button">Import file</button>
        </div>
        <div class="library-browser-list"></div>
      </div>
    `;

    const searchInput = container.querySelector('.library-search');
    const sortSelect = container.querySelector('.library-sort');
    const viewToggle = container.querySelector('.library-view-toggle');
    const importBtn = container.querySelector('.library-import-btn');
    const listEl = container.querySelector('.library-browser-list');
    const unsortedWrap = null;
    const unsortedList = null;

    function setView(view) {
      state.view = view === 'extended' ? 'extended' : 'simple';
      localStorage.setItem(VIEW_KEY, state.view);
      renderList();
    }

    function setSort(value) {
      state.sort = value;
      localStorage.setItem(SORT_KEY, state.sort);
      renderList();
    }

    function setSearch(value) {
      state.search = value;
      localStorage.setItem(SEARCH_KEY, state.search);
      renderList();
    }

    function songMatches(song, term) {
      if (!term) return true;
      const text = normalizeText(term);
      if (normalizeText(song.title).includes(text)) return true;
      for (const tag of song.tags || []) {
        if (normalizeText(tag).includes(text)) return true;
      }
      for (const version of song.versions || []) {
        if (normalizeText(version.title || version.label).includes(text)) return true;
        for (const tag of version.tags || []) {
          if (normalizeText(tag).includes(text)) return true;
        }
      }
      return false;
    }

    function songSort(a, b) {
      if (state.sort === 'az') {
        return normalizeText(a.title).localeCompare(normalizeText(b.title));
      }
      if (state.sort === 'versions') {
        return (b.versions?.length || 0) - (a.versions?.length || 0);
      }
      const aTime = Date.parse(a.last_used_at || a.created_at || '') || 0;
      const bTime = Date.parse(b.last_used_at || b.created_at || '') || 0;
      return bTime - aTime;
    }

    function emit(name, detail) {
      container.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function toggleExpanded(songId) {
      state.expanded[songId] = !state.expanded[songId];
      saveJson(EXPAND_KEY, state.expanded);
      renderList();
    }

    function renderSongRow(song) {
      const row = document.createElement('div');
      row.className = 'library-song';
      const disabled = state.disabledSongIds.has(song.song_id);
      if (disabled) row.classList.add('is-disabled');
      const header = document.createElement('div');
      header.className = 'library-song-head';
      const caret = document.createElement('button');
      caret.type = 'button';
      caret.className = 'library-caret';
      caret.textContent = state.expanded[song.song_id] ? '▾' : '▸';
      caret.addEventListener('click', (evt) => {
        evt.stopPropagation();
        toggleExpanded(song.song_id);
      });
      const title = document.createElement('div');
      title.className = 'library-song-title';
      title.textContent = song.title || 'Untitled';
      const meta = document.createElement('div');
      meta.className = 'library-song-meta';
      const duration = formatDuration(song.source?.duration_sec);
      if (duration) meta.appendChild(makeBadge(duration, 'badge-format'));
      if (song.source?.format && isLossy(song.source.format)) {
        meta.appendChild(makeBadge(song.source.format.toUpperCase(), 'badge-format'));
      }
      header.appendChild(caret);
      header.appendChild(title);
      header.appendChild(meta);
      const songTooltip = metricsTooltip(song.source?.metrics);
      if (songTooltip) {
        header.title = songTooltip;
      }
      header.addEventListener('click', () => {
        if (disabled) return;
        if (module === 'mastering' && song.source?.rel) {
          emit('library:select', {
            song_id: song.song_id,
            song,
            track: { kind: 'source', rel: song.source.rel, label: song.title },
          });
          return;
        }
        const latest = song.latest_version || (song.versions || []).slice(-1)[0];
        if (latest) {
          const primary = primaryRendition(latest.renditions);
          emit('library:select', {
            song_id: song.song_id,
            song,
            track: {
              kind: 'version',
              version_id: latest.version_id,
              rel: primary?.rel || latest.rel,
              format: primary?.format,
              title: latest.title || latest.label,
              summary: latest.summary,
              metrics: latest.metrics,
              renditions: latest.renditions || [],
            },
          });
        } else if (song.source?.rel) {
          emit('library:select', {
            song_id: song.song_id,
            song,
            track: { kind: 'source', rel: song.source.rel, label: song.title },
          });
        }
      });

      row.appendChild(header);

      const showVersions = state.view === 'extended' || state.view === 'simple' || state.expanded[song.song_id];
      if (showVersions) {
        const list = document.createElement('div');
        list.className = 'library-version-list';
        if (state.view === 'simple') {
          const latest = song.latest_version || (song.versions || []).slice(-1)[0];
          if (latest) {
            list.appendChild(renderVersionRow(song, { kind: 'version', ...latest }));
          }
        } else {
          (song.versions || []).forEach((version) => {
            list.appendChild(renderVersionRow(song, { kind: 'version', ...version }));
          });
        }
        row.appendChild(list);
      }
      return row;
    }

    function renderVersionRow(song, version) {
      const row = document.createElement('div');
      row.className = 'library-version-row';
      const label = document.createElement('div');
      label.className = 'library-version-label';
      if (version.kind === 'master') {
        label.textContent = 'Master';
      } else {
        label.textContent = version.label || version.title || version.kind || 'Version';
      }
      const meta = document.createElement('div');
      meta.className = 'library-version-meta';
      if (version.summary?.voicing) meta.appendChild(makeBadge(version.summary.voicing, 'badge-voicing'));
      if (version.summary?.loudness_profile) meta.appendChild(makeBadge(version.summary.loudness_profile, 'badge-profile'));
      const renditions = Array.isArray(version.renditions) ? version.renditions : [];
      const lossyFormats = [];
      renditions.forEach((item) => {
        const fmt = String(item.format || '').toLowerCase();
        if (isLossy(fmt) && !lossyFormats.includes(fmt)) {
          lossyFormats.push(fmt);
        }
      });
      if (lossyFormats.length) {
        const label = lossyFormats.slice(0, 2).map((f) => f.toUpperCase()).join('+');
        meta.appendChild(makeBadge(label, 'badge-format'));
      }

      const actions = document.createElement('div');
      actions.className = 'library-version-actions';
      const menu = document.createElement('details');
      menu.className = 'library-action-menu';
      const menuSummary = document.createElement('summary');
      menuSummary.textContent = '⋯';
      menuSummary.className = 'btn ghost tiny';
      menuSummary.addEventListener('click', (evt) => evt.stopPropagation());
      menu.appendChild(menuSummary);
      const menuList = document.createElement('div');
      menuList.className = 'library-action-list';

      if (module === 'compare' && version.kind !== 'source') {
        const setProc = document.createElement('button');
        setProc.type = 'button';
        setProc.textContent = 'Set as Processed';
        setProc.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:action', { action: 'set-processed', song, version });
        });
        menuList.appendChild(setProc);
      }
      if (module === 'mastering' && version.kind !== 'source') {
        const useSource = document.createElement('button');
        useSource.type = 'button';
        useSource.textContent = 'Use as Source';
        useSource.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:action', { action: 'use-as-source', song, version });
        });
        menuList.appendChild(useSource);
      }
      const openCompare = document.createElement('button');
      openCompare.type = 'button';
      openCompare.textContent = 'Open in Compare';
      openCompare.addEventListener('click', (evt) => {
        evt.stopPropagation();
        emit('library:action', { action: 'open-compare', song, version });
      });
      menuList.appendChild(openCompare);

      const download = document.createElement('details');
      download.className = 'library-download-menu';
      const downloadSummary = document.createElement('summary');
      downloadSummary.textContent = 'Download';
      downloadSummary.className = 'btn ghost tiny';
      downloadSummary.addEventListener('click', (evt) => evt.stopPropagation());
      download.appendChild(downloadSummary);
      const downloadList = document.createElement('div');
      downloadList.className = 'library-download-list';
      const ordered = [];
      ['wav', 'flac', 'm4a', 'aac', 'mp3', 'ogg'].forEach((fmt) => {
        renditions.forEach((item) => {
          if (String(item.format || '').toLowerCase() === fmt) ordered.push(item);
        });
      });
      (ordered.length ? ordered : renditions).forEach((rendition) => {
        const rel = rendition.rel;
        if (!rel) return;
        const fmt = String(rendition.format || '').toUpperCase() || 'FILE';
        const link = document.createElement('a');
        link.href = `/api/analyze/path?path=${encodeURIComponent(rel)}`;
        link.textContent = fmt;
        link.setAttribute('download', '');
        downloadList.appendChild(link);
      });
      download.appendChild(downloadList);
      menuList.appendChild(download);

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.textContent = 'Delete Version';
      delBtn.addEventListener('click', (evt) => {
        evt.stopPropagation();
        emit('library:action', { action: 'delete-version', song, version });
      });
      menuList.appendChild(delBtn);

      menu.appendChild(menuList);
      actions.appendChild(menu);

      row.appendChild(label);
      row.appendChild(meta);
      row.appendChild(actions);
      const versionTooltip = metricsTooltip(version.metrics);
      if (versionTooltip) {
        row.title = versionTooltip;
      }
      row.addEventListener('click', () => {
        if (version.kind === 'source') {
          emit('library:select', { song_id: song.song_id, song, track: { kind: 'source', rel: version.rel, label: song.title } });
          return;
        }
        const primary = primaryRendition(version.renditions);
        emit('library:select', {
          song_id: song.song_id,
          song,
          track: {
            kind: 'version',
            version_id: version.version_id,
            rel: primary?.rel || version.rel,
            format: primary?.format,
            title: version.title || version.label,
            summary: version.summary,
            metrics: version.metrics,
            renditions: version.renditions || [],
          },
        });
      });
      return row;
    }

    function renderList() {
      const filtered = state.songs.filter((song) => songMatches(song, state.search));
      filtered.sort(songSort);
      listEl.innerHTML = '';
      filtered.forEach((song) => listEl.appendChild(renderSongRow(song)));
      if (unsortedWrap) {
        unsortedWrap.hidden = true;
      }
      const buttons = viewToggle.querySelectorAll('button');
      buttons.forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.view === state.view);
      });
    }

    async function loadLibrary() {
      try {
        const res = await fetch('/api/library', { cache: 'no-store' });
        if (!res.ok) throw new Error('library_failed');
        const data = await res.json();
        state.songs = Array.isArray(data.songs) ? data.songs : [];
        renderList();
      } catch (_err) {
        listEl.innerHTML = '<div class="muted">Library unavailable.</div>';
      }
    }

    function setDisabledSongIds(ids) {
      const next = new Set(Array.isArray(ids) ? ids : []);
      state.disabledSongIds = next;
      renderList();
    }

    searchInput.value = state.search;
    sortSelect.value = state.sort;
    searchInput.addEventListener('input', () => setSearch(searchInput.value));
    sortSelect.addEventListener('change', () => setSort(sortSelect.value));
    viewToggle.addEventListener('click', (evt) => {
      const btn = evt.target.closest('button[data-view]');
      if (!btn) return;
      setView(btn.dataset.view);
    });
    importBtn.addEventListener('click', () => emit('library:action', { action: 'import-file' }));

    setView(state.view);
    loadLibrary();

    return {
      reload: loadLibrary,
      setDisabledSongIds,
    };
  }

  window.LibraryBrowser = { init: renderLibrary };
})();
