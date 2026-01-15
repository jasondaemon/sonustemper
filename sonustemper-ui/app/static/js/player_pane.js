(function(){
  function formatDuration(totalSeconds) {
    const value = Number(totalSeconds);
    if (!Number.isFinite(value) || value < 0) return '0:00';
    const mins = Math.floor(value / 60);
    const secs = Math.floor(value % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
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

  function collectRenditionFormats(renditions) {
    const list = Array.isArray(renditions) ? renditions : [];
    const ordered = [];
    const prefer = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    prefer.forEach((fmt) => {
      list.forEach((item) => {
        if (String(item.format || '').toLowerCase() === fmt) {
          ordered.push(item);
        }
      });
    });
    return ordered.length ? ordered : list;
  }

  function trackId(songId, kind, versionId, rel) {
    return [songId || 'song', kind || 'track', versionId || 'none', rel || ''].join('::');
  }

  function buildTracks(song) {
    const tracks = [];
    if (song?.source?.rel) {
      tracks.push({
        id: trackId(song.song_id, 'source', 'source', song.source.rel),
        kind: 'source',
        title: 'Source',
        subtitle: song.title || 'Untitled',
        rel: song.source.rel,
        format: song.source.format || '',
        duration: song.source?.duration_sec || null,
        song,
        renditions: [],
        summary: null,
      });
    }
    (song?.versions || []).forEach((version) => {
      const primary = primaryRendition(version.renditions) || { rel: version.rel };
      if (!primary?.rel) return;
      tracks.push({
        id: trackId(song.song_id, 'version', version.version_id, primary.rel),
        kind: 'version',
        title: version.kind === 'master' ? 'Master' : (version.label || version.title || 'Version'),
        subtitle: song.title || 'Untitled',
        rel: primary.rel,
        format: primary.format || '',
        duration: song.source?.duration_sec || null,
        song,
        version,
        renditions: version.renditions || [],
        summary: version.summary || {},
      });
    });
    return tracks;
  }

  function init(root, options) {
    if (!root) return null;
    const waveContainer = root.querySelector('#playerWaveform');
    const waveTitle = root.querySelector('#playerWaveTitle');
    const waveMeta = root.querySelector('#playerWaveMeta');
    const waveTime = root.querySelector('#playerWaveTime');
    const waveDuration = root.querySelector('#playerWaveDuration');
    const trackList = root.querySelector('#playerTrackList');
    const hintEl = document.getElementById('playerPaneHint');
    const audio = options?.audioEl || root.querySelector('audio');
    const state = {
      song: null,
      tracks: [],
      activeId: null,
      wave: null,
    };

    function setHint(text) {
      if (!hintEl) return;
      hintEl.textContent = text || '';
    }

    function updateWaveMeta(track) {
      if (!waveTitle || !waveMeta) return;
      if (!track) {
        waveTitle.textContent = 'No track loaded';
        waveMeta.textContent = '—';
        return;
      }
      waveTitle.textContent = track.title || 'Track';
      waveMeta.textContent = track.subtitle || '';
    }

    function updateWaveTime() {
      if (!audio || !waveTime || !waveDuration) return;
      waveTime.textContent = formatDuration(audio.currentTime || 0);
      waveDuration.textContent = formatDuration(audio.duration || 0);
    }

    function updateButtons() {
      if (!trackList) return;
      const buttons = trackList.querySelectorAll('[data-action="toggle-play"]');
      buttons.forEach((btn) => {
        const id = btn.dataset.trackId;
        const isActive = id === state.activeId;
        const isPlaying = isActive && audio && !audio.paused;
        btn.textContent = isPlaying ? '⏸' : '▶';
      });
      const cards = trackList.querySelectorAll('.player-track');
      cards.forEach((card) => {
        card.classList.toggle('is-active', card.dataset.trackId === state.activeId);
      });
    }

    function ensureWave() {
      if (!window.WaveSurfer || !waveContainer || !audio) return;
      if (state.wave) return;
      state.wave = WaveSurfer.create({
        container: waveContainer,
        waveColor: '#2a3a4f',
        progressColor: '#3b4f66',
        cursorColor: '#6b829a',
        height: 140,
        barWidth: 2,
        barGap: 1,
        normalize: true,
        backend: 'MediaElement',
        media: audio,
        minPxPerSec: 1,
        fillParent: true,
        autoScroll: false,
        autoCenter: false,
        dragToSeek: true,
      });
      state.wave.on('ready', updateWaveTime);
      state.wave.on('interaction', updateWaveTime);
    }

    function loadTrack(track, autoplay) {
      if (!audio || !track?.rel) return;
      state.activeId = track.id;
      updateWaveMeta(track);
      const url = `/api/analyze/path?path=${encodeURIComponent(track.rel)}`;
      if (audio.src !== url) {
        audio.src = url;
        audio.load();
        ensureWave();
        if (state.wave) state.wave.load(url);
      }
      updateWaveTime();
      updateButtons();
      if (autoplay) {
        audio.play().catch(() => {});
      }
    }

    function renderTracks() {
      if (!trackList) return;
      trackList.innerHTML = '';
      state.tracks.forEach((track) => {
        const card = document.createElement('div');
        card.className = 'player-track';
        card.dataset.trackId = track.id;

        const head = document.createElement('div');
        head.className = 'player-track-head';
        const title = document.createElement('div');
        title.className = 'player-track-title';
        title.textContent = track.title || 'Track';
        title.title = track.subtitle || '';
        head.appendChild(title);

        const pills = document.createElement('div');
        pills.className = 'player-track-pills';
        if (track.summary?.voicing) {
          pills.appendChild(makePill(track.summary.voicing, 'badge-voicing'));
        }
        if (track.summary?.loudness_profile) {
          pills.appendChild(makePill(track.summary.loudness_profile, 'badge-profile'));
        }
        head.appendChild(pills);
        card.appendChild(head);

        const controls = document.createElement('div');
        controls.className = 'player-track-controls';
        const playBtn = document.createElement('button');
        playBtn.type = 'button';
        playBtn.className = 'btn ghost tiny';
        playBtn.dataset.action = 'toggle-play';
        playBtn.dataset.trackId = track.id;
        playBtn.textContent = '▶';
        playBtn.addEventListener('click', (evt) => {
          evt.stopPropagation();
          if (state.activeId !== track.id) {
            loadTrack(track, true);
            return;
          }
          if (audio.paused) {
            audio.play().catch(() => {});
          } else {
            audio.pause();
          }
        });
        controls.appendChild(playBtn);

        const time = document.createElement('div');
        time.className = 'player-track-time muted';
        time.textContent = track.duration ? formatDuration(track.duration) : (track.subtitle || '');
        controls.appendChild(time);

        const downloads = document.createElement('div');
        downloads.className = 'player-track-downloads';
        const renditions = track.renditions?.length ? track.renditions : [{ rel: track.rel, format: track.format }];
        collectRenditionFormats(renditions).forEach((rendition) => {
          if (!rendition.rel) return;
          const link = document.createElement('a');
          link.href = `/api/analyze/path?path=${encodeURIComponent(rendition.rel)}`;
          link.className = 'badge badge-format';
          link.textContent = String(rendition.format || 'FILE').toUpperCase();
          link.setAttribute('download', '');
          downloads.appendChild(link);
        });
        controls.appendChild(downloads);
        card.appendChild(controls);

        const actions = document.createElement('div');
        actions.className = 'player-track-actions';
        actions.appendChild(makeActionButton('Open in EQ', () => options?.onOpenEq?.(track.song, track)));
        actions.appendChild(makeActionButton('Analyze', () => options?.onOpenAnalyze?.(track.song, track)));
        actions.appendChild(makeActionButton('Compare', () => options?.onOpenCompare?.(track.song, track)));
        actions.appendChild(makeActionButton('AI Toolkit', () => options?.onOpenAiToolkit?.(track.song, track)));
        actions.appendChild(makeActionButton('Add as Input', () => options?.onAddInput?.(track.song, track)));
        card.appendChild(actions);

        trackList.appendChild(card);
      });
      updateButtons();
    }

    function makePill(text, className) {
      const pill = document.createElement('span');
      pill.className = `badge ${className || ''}`.trim();
      pill.textContent = text;
      return pill;
    }

    function makeActionButton(label, handler) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn ghost tiny';
      btn.textContent = label;
      btn.addEventListener('click', (evt) => {
        evt.stopPropagation();
        handler();
      });
      return btn;
    }

    function loadSong(song, opts) {
      if (!song) return;
      state.song = song;
      state.tracks = buildTracks(song);
      renderTracks();
      setHint(song.title ? `Loaded ${song.title}` : 'Loaded');
      if (!state.tracks.length) {
        updateWaveMeta(null);
        return;
      }
      loadTrack(state.tracks[0], opts?.autoplay);
    }

    function clearIfSong(songId) {
      if (!state.song || state.song.song_id !== songId) return;
      state.song = null;
      state.tracks = [];
      state.activeId = null;
      trackList.innerHTML = '';
      updateWaveMeta(null);
      setHint('Select a song to preview.');
      if (audio) {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      }
      if (state.wave) {
        state.wave.destroy();
        state.wave = null;
      }
    }

    if (audio) {
      audio.addEventListener('timeupdate', updateWaveTime);
      audio.addEventListener('play', updateButtons);
      audio.addEventListener('pause', updateButtons);
      audio.addEventListener('ended', () => {
        updateButtons();
        updateWaveTime();
      });
    }

    return { loadSong, clearIfSong };
  }

  window.MasteringPlayerPane = { init };
})();
