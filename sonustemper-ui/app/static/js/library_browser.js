(() => {
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

  function isNoiseRemovedUtility(utility) {
    const util = normalizeText(utility);
    return util === 'noise removed' || util === 'noise cleanup' || util === 'noise removal';
  }

  function isNoiseProfileUtility(utility) {
    const util = normalizeText(utility);
    return util === 'noise profile';
  }

  function notify(msg) {
    if (typeof window.showToast === 'function') {
      window.showToast(msg);
    } else {
      console.info(msg);
    }
  }

  let metricsModal = null;
  let metricsModalTitle = null;
  let metricsModalList = null;
  let metricsModalClose = null;

  function ensureMetricsModal() {
    if (metricsModal) return;
    metricsModal = document.createElement('div');
    metricsModal.className = 'library-metrics-modal';
    metricsModal.hidden = true;
    metricsModal.innerHTML = `
      <div class="library-metrics-dialog" role="dialog" aria-modal="true">
        <div class="library-metrics-head">
          <div class="library-metrics-title"></div>
          <button type="button" class="btn ghost tiny library-metrics-close">Close</button>
        </div>
        <div class="library-metrics-body"></div>
      </div>
    `;
    document.body.appendChild(metricsModal);
    metricsModalTitle = metricsModal.querySelector('.library-metrics-title');
    metricsModalList = metricsModal.querySelector('.library-metrics-body');
    metricsModalClose = metricsModal.querySelector('.library-metrics-close');
    metricsModalClose.addEventListener('click', () => closeMetricsModal());
    metricsModal.addEventListener('click', (evt) => {
      if (evt.target === metricsModal) closeMetricsModal();
    });
    metricsModal.addEventListener('keydown', (evt) => {
      if (evt.key === 'Escape') closeMetricsModal();
    });
  }

  function openMetricsModal(title, lines) {
    ensureMetricsModal();
    metricsModalTitle.textContent = title || 'Metrics';
    metricsModalList.innerHTML = '';
    if (Array.isArray(lines) && lines.length) {
      const list = document.createElement('ul');
      list.className = 'library-metrics-list';
      lines.forEach((line) => {
        const item = document.createElement('li');
        item.textContent = line;
        list.appendChild(item);
      });
      metricsModalList.appendChild(list);
    } else {
      const empty = document.createElement('div');
      empty.className = 'muted';
      empty.textContent = 'No metrics available.';
      metricsModalList.appendChild(empty);
    }
    metricsModal.hidden = false;
    metricsModalClose.focus();
  }

  function closeMetricsModal() {
    if (!metricsModal) return;
    metricsModal.hidden = true;
  }

  function noisePresetSettingsFromVersion(version) {
    const noise = version?.summary?.noise || version?.meta?.noise || version?.noise;
    if (!noise || typeof noise !== 'object') return null;
    const fLow = Number(noise.f_low);
    const fHigh = Number(noise.f_high);
    if (!Number.isFinite(fLow) || !Number.isFinite(fHigh) || fHigh <= fLow) return null;
    const bandDepth = Number.isFinite(Number(noise.band_depth_db)) ? Number(noise.band_depth_db) : -18;
    const strength = Number.isFinite(Number(noise.afftdn_strength)) ? Number(noise.afftdn_strength) : 0.35;
    const hp = Number.isFinite(Number(noise.hp_hz)) ? Number(noise.hp_hz) : null;
    const lp = Number.isFinite(Number(noise.lp_hz)) ? Number(noise.lp_hz) : null;
    const mode = normalizeText(noise.mode) === 'solo' ? 'remove' : (noise.mode || 'remove');
    return {
      f_low: fLow,
      f_high: fHigh,
      band_depth_db: bandDepth,
      afftdn_strength: strength,
      hp_hz: hp,
      lp_hz: lp,
      mode,
    };
  }

  async function convertNoiseProfileToPreset(song, version) {
    const settings = noisePresetSettingsFromVersion(version);
    if (!settings) {
      notify('Noise settings missing for this profile.');
      return;
    }
    const titleBase = song?.title || version?.title || version?.label || 'Noise Filter';
    const title = `${titleBase} Noise Filter`;
    try {
      const res = await fetch('/api/analyze/noise/preset/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title,
          target_kind: 'noise_filter',
          settings,
          source_hint: { from_file: song?.source?.rel || version?.rel || '' },
        }),
      });
      if (!res.ok) {
        const err = await res.text();
        const detail = (err || 'preset_failed').toString().trim();
        throw new Error(detail.slice(0, 200));
      }
      notify('Noise Filter Preset created.');
      window.dispatchEvent(new CustomEvent('sonustemper:noise-presets-changed'));
    } catch (_err) {
      const message = _err?.message ? `Failed to create Noise Filter Preset: ${_err.message}` : 'Failed to create Noise Filter Preset.';
      notify(message);
    }
  }

  function renderLibrary(container, options = {}) {
    const module = options.module || container.dataset.module || 'generic';
    const isMastering = module === 'mastering';
    const state = {
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
          <button class="btn success tiny library-import-btn" type="button">Import Song(s)</button>
        </div>
        <div class="library-browser-list"></div>
      </div>
    `;

    const searchInput = container.querySelector('.library-search');
    const sortSelect = container.querySelector('.library-sort');
    const importBtn = container.querySelector('.library-import-btn');
    const listEl = container.querySelector('.library-browser-list');
    const unsortedWrap = null;
    const unsortedList = null;

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
      container.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }));
    }

    function toggleExpanded(songId) {
      state.expanded[songId] = !state.expanded[songId];
      saveJson(EXPAND_KEY, state.expanded);
      renderList();
    }

    function expandSong(songId) {
      if (!songId) return;
      state.expanded[songId] = true;
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
      header.appendChild(caret);
      header.appendChild(title);
      if (!isMastering) {
        const meta = document.createElement('div');
        meta.className = 'library-song-meta';
        const duration = formatDuration(song.source?.duration_sec);
        if (duration) meta.appendChild(makeBadge(duration, 'badge-format'));
        if (song.source?.format && isLossy(song.source.format)) {
          meta.appendChild(makeBadge(song.source.format.toUpperCase(), 'badge-format'));
        }
        header.appendChild(meta);
      }
      const songTooltip = metricsTooltip(song.source?.metrics);
      if (songTooltip) {
        header.title = songTooltip;
      }
      if (!isMastering) {
        header.addEventListener('click', () => {
          if (disabled) return;
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
      } else {
        header.addEventListener('click', () => {
          if (disabled) return;
          emit('library:play-song', { song });
        });
      }

      row.appendChild(header);
      if (isMastering) {
        row.classList.add('library-song--compact');
        const actions = document.createElement('div');
        actions.className = 'library-song-actions';
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn ghost tiny';
        addBtn.classList.add('library-add-btn');
        addBtn.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 4v16M4 12h16" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
          </svg>
        `;
        addBtn.title = 'Add to Input';
        addBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:add-to-input', { song });
        });
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn ghost tiny';
        delBtn.textContent = '🗑';
        delBtn.title = 'Delete Song';
        delBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:delete-song', { song });
        });
        actions.appendChild(addBtn);
        actions.appendChild(delBtn);
        header.appendChild(actions);
      }

      if (state.expanded[song.song_id]) {
        const list = document.createElement('div');
        list.className = 'library-version-list';
        const versions = Array.isArray(song.versions) ? song.versions : [];
        const filtered = [];
        let noiseRemovedKept = false;
        versions.forEach((version) => {
          if (isNoiseRemovedUtility(version.utility)) {
            if (noiseRemovedKept) return;
            noiseRemovedKept = true;
          }
          filtered.push(version);
        });
        filtered.forEach((version) => {
          list.appendChild(renderVersionRow(song, { kind: 'version', ...version }));
        });
        row.appendChild(list);
      }
      return row;
    }

    function renderVersionRow(song, version) {
      const row = document.createElement('div');
      row.className = 'library-version-row';
      const renditions = Array.isArray(version.renditions) ? version.renditions : [];
      const primary = primaryRendition(renditions);
      const primaryRel = primary?.rel || version.rel || '';
      const canOpen = Boolean(primaryRel);
      const canDelete = Boolean(song?.song_id && version?.version_id);
      const canDownload = renditions.length > 0;
      const hasMp3 = renditions.some((item) => String(item.format || '').toLowerCase() === 'mp3')
        || (primaryRel && String(primaryRel).toLowerCase().endsWith('.mp3'));
      const meta = document.createElement('div');
      meta.className = 'library-version-meta';
      const utilityLabel = isNoiseRemovedUtility(version.utility) ? 'Noise Removed' : version.utility;
      if (utilityLabel) meta.appendChild(makeBadge(utilityLabel, 'badge-utility'));
      if (version.summary?.voicing) meta.appendChild(makeBadge(version.summary.voicing, 'badge-voicing'));
      const metaOverflow = makeBadge('i', 'badge-param library-meta-overflow');
      const metaLines = [];
      if (version.label) metaLines.push(`Label: ${version.label}`);
      if (version.title) metaLines.push(`Title: ${version.title}`);
      if (utilityLabel) metaLines.push(`Utility: ${utilityLabel}`);
      if (version.summary?.voicing) metaLines.push(`Voicing: ${version.summary.voicing}`);
      if (version.summary?.loudness_profile) metaLines.push(`Profile: ${version.summary.loudness_profile}`);
      const metrics = version.metrics || {};
      const metricsMap = [
        ['LUFS', metrics.lufs_i],
        ['TP', metrics.true_peak_dbtp ?? metrics.true_peak_db],
        ['LRA', metrics.lra],
        ['Crest', metrics.crest_factor],
        ['DR', metrics.dynamic_range],
        ['RMS', metrics.rms_level],
        ['Peak', metrics.peak_level],
        ['Noise', metrics.noise_floor],
        ['Width', metrics.width],
        ['Duration', metrics.duration_sec ? `${Math.round(metrics.duration_sec)}s` : null],
      ];
      metricsMap.forEach(([label, value]) => {
        if (typeof value === 'number' || typeof value === 'string') {
          metaLines.push(`${label}: ${typeof value === 'number' ? value.toFixed(1) : value}`);
        }
      });
      if (metaLines.length) {
        metaOverflow.setAttribute('role', 'button');
        metaOverflow.setAttribute('tabindex', '0');
        metaOverflow.setAttribute('aria-label', 'Show metadata');
        metaOverflow.addEventListener('click', (evt) => {
          evt.stopPropagation();
          openMetricsModal('Metrics', metaLines);
        });
        metaOverflow.addEventListener('keydown', (evt) => {
          if (evt.key === 'Enter') {
            evt.preventDefault();
            openMetricsModal('Metrics', metaLines);
          }
        });
        meta.appendChild(metaOverflow);
      }

      const actions = document.createElement('div');
      actions.className = 'library-version-actions';
      if (isMastering) {
        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'btn ghost tiny library-add-btn';
        addBtn.title = 'Add to Input';
        addBtn.innerHTML = `
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 4v16M4 12h16" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
          </svg>
        `;
        addBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          emit('library:add-to-input', { song, version });
        });
        actions.appendChild(addBtn);
      }
      const menu = document.createElement('details');
      menu.className = 'library-action-menu';
      menu.addEventListener('mouseleave', () => {
        menu.open = false;
      });
      const menuSummary = document.createElement('summary');
      menuSummary.textContent = '⋯';
      menuSummary.className = 'btn ghost tiny';
      menuSummary.addEventListener('click', (evt) => evt.stopPropagation());
      menu.appendChild(menuSummary);
      const menuList = document.createElement('div');
      menuList.className = 'library-action-list';
      const applyDisabled = (btn, reason) => {
        if (!btn) return;
        btn.disabled = true;
        btn.setAttribute('aria-disabled', 'true');
        btn.classList.add('is-disabled');
        if (reason) btn.title = reason;
      };
      if (isMastering) {
        const analyzeBtn = document.createElement('button');
        analyzeBtn.type = 'button';
        analyzeBtn.textContent = 'Noise Removal';
        analyzeBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (!canOpen) return;
          emit('library:action', { action: 'open-analyze', song, version });
        });
        if (!canOpen) applyDisabled(analyzeBtn, 'No audio file available for this version.');
        menuList.appendChild(analyzeBtn);
        const compareBtn = document.createElement('button');
        compareBtn.type = 'button';
        compareBtn.textContent = 'Compare';
        compareBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (!canOpen) return;
          emit('library:action', { action: 'open-compare', song, version });
        });
        if (!canOpen) applyDisabled(compareBtn, 'No audio file available for this version.');
        menuList.appendChild(compareBtn);
        const eqBtn = document.createElement('button');
        eqBtn.type = 'button';
        eqBtn.textContent = 'Open in EQ';
        eqBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (!canOpen) return;
          emit('library:action', { action: 'open-eq', song, version });
        });
        if (!canOpen) applyDisabled(eqBtn, 'No audio file available for this version.');
        menuList.appendChild(eqBtn);
        if (isNoiseProfileUtility(version.utility)) {
          const convertBtn = document.createElement('button');
          convertBtn.type = 'button';
          convertBtn.textContent = 'Convert to Filter Preset';
          convertBtn.addEventListener('click', (evt) => {
            evt.stopPropagation();
            convertNoiseProfileToPreset(song, version).finally(() => {
              menu.open = false;
            });
          });
          menuList.appendChild(convertBtn);
        }
        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.textContent = 'Delete';
        delBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (!canDelete) return;
          emit('library:action', { action: 'delete-version', song, version });
        });
        if (!canDelete) applyDisabled(delBtn, 'Missing song/version id.');
        menuList.appendChild(delBtn);
      } else {
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
          if (!canOpen) return;
          emit('library:action', { action: 'open-compare', song, version });
        });
        if (!canOpen) applyDisabled(openCompare, 'No audio file available for this version.');
        menuList.appendChild(openCompare);
        const openEq = document.createElement('button');
        openEq.type = 'button';
        openEq.textContent = 'Open in EQ';
        openEq.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (!canOpen) return;
          emit('library:action', { action: 'open-eq', song, version });
        });
        if (!canOpen) applyDisabled(openEq, 'No audio file available for this version.');
        menuList.appendChild(openEq);
        if (module === 'tagging' && !hasMp3) {
          const convertBtn = document.createElement('button');
          convertBtn.type = 'button';
          convertBtn.textContent = 'Convert to MP3';
          convertBtn.addEventListener('click', (evt) => {
            evt.stopPropagation();
            const rel = primaryRel || version.rel || song?.source?.rel || '';
            if (!rel) return;
            emit('library:action', { action: 'ensure-mp3', song, version, rel });
          });
          if (!primaryRel && !version.rel && !song?.source?.rel) {
            applyDisabled(convertBtn, 'No source file available for conversion.');
          }
          menuList.appendChild(convertBtn);
        }
        if (isNoiseProfileUtility(version.utility)) {
          const convertBtn = document.createElement('button');
          convertBtn.type = 'button';
          convertBtn.textContent = 'Convert to Filter Preset';
          convertBtn.addEventListener('click', (evt) => {
            evt.stopPropagation();
            convertNoiseProfileToPreset(song, version).finally(() => {
              menu.open = false;
            });
          });
          menuList.appendChild(convertBtn);
        }

        const download = document.createElement('details');
        download.className = 'library-download-menu';
        download.addEventListener('mouseleave', () => {
          download.open = false;
        });
        const downloadSummary = document.createElement('summary');
        downloadSummary.textContent = 'Download';
        downloadSummary.className = 'btn ghost tiny';
        downloadSummary.addEventListener('click', (evt) => evt.stopPropagation());
        if (!canDownload) {
          downloadSummary.classList.add('is-disabled');
          downloadSummary.setAttribute('aria-disabled', 'true');
          downloadSummary.title = 'No renditions available for this version.';
          downloadSummary.addEventListener('click', (evt) => {
            evt.preventDefault();
            evt.stopPropagation();
          });
        }
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
          if (!canDelete) return;
          emit('library:action', { action: 'delete-version', song, version });
        });
        if (!canDelete) applyDisabled(delBtn, 'Missing song/version id.');
        menuList.appendChild(delBtn);
      }

      menu.appendChild(menuList);
      actions.appendChild(menu);

      row.appendChild(meta);
      row.appendChild(actions);
      const versionTooltip = metricsTooltip(version.metrics);
      if (versionTooltip) {
        row.title = versionTooltip;
      }
      if (!isMastering) {
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
      }
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
    }

    async function loadLibrary() {
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 12000);
        const res = await fetch('/api/library', { cache: 'no-store', signal: controller.signal });
        clearTimeout(timeout);
        if (!res.ok) throw new Error('library_failed');
        const data = await res.json();
        state.songs = Array.isArray(data.songs) ? data.songs : [];
        renderList();
      } catch (_err) {
        console.error('Library load failed', _err);
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
    importBtn.addEventListener('click', () => emit('library:action', { action: 'import-file' }));

    loadLibrary();

    return {
      reload: loadLibrary,
      setDisabledSongIds,
      expandSong,
    };
  }

  window.LibraryBrowser = { init: renderLibrary };
})();
