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

  function renderLibrary(container, options = {}) {
    const module = options.module || container.dataset.module || 'generic';
    const state = {
      view: localStorage.getItem(VIEW_KEY) || 'simple',
      sort: localStorage.getItem(SORT_KEY) || 'recent',
      expanded: loadJson(EXPAND_KEY, {}),
      search: localStorage.getItem(SEARCH_KEY) || '',
      songs: [],
      unsorted: [],
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
        <div class="library-unsorted" hidden>
          <div class="library-section-title">Unsorted Outputs</div>
          <div class="library-unsorted-list"></div>
        </div>
      </div>
    `;

    const searchInput = container.querySelector('.library-search');
    const sortSelect = container.querySelector('.library-sort');
    const viewToggle = container.querySelector('.library-view-toggle');
    const importBtn = container.querySelector('.library-import-btn');
    const listEl = container.querySelector('.library-browser-list');
    const unsortedWrap = container.querySelector('.library-unsorted');
    const unsortedList = container.querySelector('.library-unsorted-list');

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
        if (normalizeText(version.label).includes(text)) return true;
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
      if (song.source?.format) meta.appendChild(makeBadge(song.source.format.toUpperCase(), 'badge-format'));
      if ((song.versions || []).length) meta.appendChild(makeBadge('Latest', 'badge-tag'));
      header.appendChild(caret);
      header.appendChild(title);
      header.appendChild(meta);
      header.addEventListener('click', () => {
        const latest = song.latest_version || (song.versions || []).slice(-1)[0];
        if (latest) {
          emit('library:select', {
            song_id: song.song_id,
            song,
            track: { kind: 'version', version_id: latest.version_id, rel: latest.rel, label: latest.label, summary: latest.summary, metrics: latest.metrics },
          });
        } else if (song.source?.rel) {
          emit('library:select', {
            song_id: song.song_id,
            song,
            track: { kind: 'source', rel: song.source.rel, label: song.title },
          });
        }
      });

      const actions = document.createElement('div');
      actions.className = 'library-song-actions';
      const renameBtn = document.createElement('button');
      renameBtn.type = 'button';
      renameBtn.className = 'btn ghost tiny';
      renameBtn.textContent = 'Rename';
      renameBtn.addEventListener('click', (evt) => {
        evt.stopPropagation();
        emit('library:action', { action: 'rename-song', song });
      });
      actions.appendChild(renameBtn);

      row.appendChild(header);
      row.appendChild(actions);

      const showVersions = state.view === 'extended' || state.expanded[song.song_id];
      if (showVersions) {
        const list = document.createElement('div');
        list.className = 'library-version-list';
        if (song.source?.rel) {
          const sourceRow = renderVersionRow(song, { kind: 'source', label: 'Source', rel: song.source.rel });
          list.appendChild(sourceRow);
        }
        (song.versions || []).forEach((version) => {
          list.appendChild(renderVersionRow(song, { kind: 'version', ...version }));
        });
        row.appendChild(list);
      }
      return row;
    }

    function renderVersionRow(song, version) {
      const row = document.createElement('div');
      row.className = 'library-version';
      const label = document.createElement('div');
      label.className = 'library-version-label';
      label.textContent = version.label || version.kind || 'Version';
      const meta = document.createElement('div');
      meta.className = 'library-version-meta';
      if (version.kind && version.kind !== 'version') {
        meta.appendChild(makeBadge(version.kind.toUpperCase(), 'badge-container'));
      }
      if (version.summary?.voicing) meta.appendChild(makeBadge(version.summary.voicing, 'badge-voicing'));
      if (version.summary?.loudness_profile) meta.appendChild(makeBadge(version.summary.loudness_profile, 'badge-profile'));
      if (version.summary?.aitk) meta.appendChild(makeBadge('AITK', 'badge-param'));
      if (version.summary?.noise) meta.appendChild(makeBadge('Noise', 'badge-param'));
      if (version.summary?.eq) meta.appendChild(makeBadge('EQ', 'badge-param'));

      const actions = document.createElement('div');
      actions.className = 'library-version-actions';
      if (module === 'compare' && version.kind !== 'source') {
        const setProc = document.createElement('button');
        setProc.type = 'button';
        setProc.className = 'btn ghost tiny';
        setProc.textContent = 'Set Processed';
        setProc.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:action', { action: 'set-processed', song, version });
        });
        actions.appendChild(setProc);
      }
      if (module === 'mastering' && version.kind !== 'source') {
        const useSource = document.createElement('button');
        useSource.type = 'button';
        useSource.className = 'btn ghost tiny';
        useSource.textContent = 'Use as Source';
        useSource.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:action', { action: 'use-as-source', song, version });
        });
        actions.appendChild(useSource);
      }
      if (version.rel) {
        const download = document.createElement('a');
        download.className = 'btn ghost tiny';
        download.href = `/api/analyze/path?path=${encodeURIComponent(version.rel)}`;
        download.textContent = 'Download';
        download.setAttribute('download', '');
        actions.appendChild(download);

        const openCompare = document.createElement('button');
        openCompare.type = 'button';
        openCompare.className = 'btn ghost tiny';
        openCompare.textContent = 'Open in Compare';
        openCompare.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:action', { action: 'open-compare', song, version });
        });
        actions.appendChild(openCompare);

        if (version.kind !== 'source') {
          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.className = 'btn ghost tiny';
          delBtn.textContent = 'Delete';
          delBtn.addEventListener('click', (evt) => {
            evt.stopPropagation();
            emit('library:action', { action: 'delete-version', song, version });
          });
          actions.appendChild(delBtn);
        }
      }

      row.appendChild(label);
      row.appendChild(meta);
      row.appendChild(actions);
      row.addEventListener('click', () => {
        if (version.kind === 'source') {
          emit('library:select', { song_id: song.song_id, song, track: { kind: 'source', rel: version.rel, label: song.title } });
          return;
        }
        emit('library:select', {
          song_id: song.song_id,
          song,
          track: { kind: 'version', version_id: version.version_id, rel: version.rel, label: version.label, summary: version.summary, metrics: version.metrics },
        });
      });
      return row;
    }

    function renderUnsorted() {
      unsortedList.innerHTML = '';
      if (!state.unsorted.length) {
        unsortedWrap.hidden = true;
        return;
      }
      unsortedWrap.hidden = false;
      state.unsorted.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'library-unsorted-row';
        const label = document.createElement('div');
        label.textContent = item.name || item.rel;
        const actions = document.createElement('div');
        actions.className = 'library-unsorted-actions';
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn ghost tiny';
        addBtn.textContent = 'Add to Library';
        addBtn.addEventListener('click', () => {
          emit('library:action', { action: 'add-unsorted', item });
        });
        actions.appendChild(addBtn);
        row.appendChild(label);
        row.appendChild(actions);
        unsortedList.appendChild(row);
      });
    }

    function renderList() {
      const filtered = state.songs.filter((song) => songMatches(song, state.search));
      filtered.sort(songSort);
      listEl.innerHTML = '';
      filtered.forEach((song) => listEl.appendChild(renderSongRow(song)));
      renderUnsorted();
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
        state.unsorted = Array.isArray(data.unsorted) ? data.unsorted : [];
        renderList();
      } catch (_err) {
        listEl.innerHTML = '<div class="muted">Library unavailable.</div>';
      }
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
    };
  }

  window.LibraryBrowser = { init: renderLibrary };
})();
