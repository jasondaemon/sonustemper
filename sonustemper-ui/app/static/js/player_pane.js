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
        version_id: version.version_id,
        title: 'Version',
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
    const scopeCanvas = root.querySelector('#playerLiveScope');
    const hintEl = document.getElementById('playerPaneHint');
    const audio = options?.audioEl || root.querySelector('audio');
    const state = {
      song: null,
      tracks: [],
      activeId: null,
      wave: null,
      scope: {
        node: null,
        floatData: null,
        byteData: null,
        raf: null,
        last: 0,
      },
      audioCtx: null,
      audioSource: null,
      outputGain: null,
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
      waveTitle.textContent = track.subtitle || track.title || 'Track';
      waveMeta.textContent = track.title || 'Track';
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
      state.wave.on('error', (err) => {
        const msg = String(err || '');
        if (msg.includes('AbortError')) return;
      });
    }

    function ensureScopeGraph() {
      if (!audio || state.audioCtx) return;
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      state.audioCtx = ctx;
      try {
        state.audioSource = ctx.createMediaElementSource(audio);
      } catch (_err) {
        return;
      }
      const scope = ctx.createAnalyser();
      scope.fftSize = 2048;
      scope.smoothingTimeConstant = 0.85;
      const outputGain = ctx.createGain();
      outputGain.gain.value = 1;
      state.audioSource.connect(scope);
      scope.connect(outputGain);
      outputGain.connect(ctx.destination);
      state.outputGain = outputGain;
      state.scope.node = scope;
      state.scope.floatData = new Float32Array(scope.fftSize);
      state.scope.byteData = new Uint8Array(scope.fftSize);
    }

    function resumeScopeAudio() {
      if (!state.audioCtx) return;
      if (state.audioCtx.state === 'suspended') {
        state.audioCtx.resume().catch(() => {});
      }
    }

    function drawScopeFrame() {
      if (!scopeCanvas || !state.scope.node) return;
      const ctx = scopeCanvas.getContext('2d');
      if (!ctx) return;
      const now = performance.now();
      if (now - state.scope.last < 16) {
        state.scope.raf = requestAnimationFrame(drawScopeFrame);
        return;
      }
      state.scope.last = now;
      const width = scopeCanvas.clientWidth;
      const height = scopeCanvas.clientHeight;
      if (!width || !height) {
        state.scope.raf = requestAnimationFrame(drawScopeFrame);
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      if (scopeCanvas.width !== Math.round(width * dpr) || scopeCanvas.height !== Math.round(height * dpr)) {
        scopeCanvas.width = Math.round(width * dpr);
        scopeCanvas.height = Math.round(height * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const scopeNode = state.scope.node;
      let data = state.scope.floatData;
      let useFloat = true;
      if (!data || typeof scopeNode.getFloatTimeDomainData !== 'function') {
        useFloat = false;
        data = state.scope.byteData;
      }
      if (!data) {
        state.scope.raf = requestAnimationFrame(drawScopeFrame);
        return;
      }
      if (useFloat) {
        scopeNode.getFloatTimeDomainData(data);
      } else {
        scopeNode.getByteTimeDomainData(data);
      }
      const rootStyle = getComputedStyle(document.documentElement);
      const accent = rootStyle.getPropertyValue('--accent').trim() || '#ff8a3d';
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.4;
      ctx.globalAlpha = audio && !audio.paused ? 0.85 : 0.5;
      ctx.beginPath();
      for (let i = 0; i < data.length; i += 1) {
        const t = i / (data.length - 1);
        const x = t * width;
        const v = useFloat ? data[i] : (data[i] - 128) / 128;
        const y = (1 - (v * 0.5 + 0.5)) * height;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.globalAlpha = audio && !audio.paused ? 0.12 : 0.08;
      ctx.fillStyle = accent;
      ctx.lineTo(width, height * 0.5);
      ctx.lineTo(0, height * 0.5);
      ctx.closePath();
      ctx.fill();
      state.scope.raf = requestAnimationFrame(drawScopeFrame);
    }

    function startScope() {
      if (state.scope.raf) return;
      state.scope.last = 0;
      state.scope.raf = requestAnimationFrame(drawScopeFrame);
    }

    function stopScope() {
      if (state.scope.raf) {
        cancelAnimationFrame(state.scope.raf);
        state.scope.raf = null;
      }
    }

    function updateScopeOnce() {
      stopScope();
      state.scope.last = 0;
      drawScopeFrame();
    }

    function playWhenReady() {
      if (!audio) return;
      ensureScopeGraph();
      resumeScopeAudio();
      if (audio.readyState >= 2) {
        audio.play().catch(() => {});
        return;
      }
      const onReady = () => {
        audio.removeEventListener('canplay', onReady);
        audio.play().catch(() => {});
      };
      audio.addEventListener('canplay', onReady, { once: true });
    }

    function loadTrack(track, autoplay) {
      if (!audio || !track?.rel) return;
      const isNew = state.activeId !== track.id;
      if (!audio.paused && isNew) {
        audio.pause();
      }
      if (isNew) {
        audio.currentTime = 0;
      }
      state.activeId = track.id;
      updateWaveMeta(track);
      const url = `/api/analyze/path?path=${encodeURIComponent(track.rel)}`;
      if (audio.src !== url) {
        audio.src = url;
        audio.load();
        ensureWave();
        if (state.wave) {
          try {
            const result = state.wave.load(url);
            if (result && typeof result.catch === 'function') {
              result.catch(() => {});
            }
          } catch (_err) {}
        }
      }
      updateWaveTime();
      updateButtons();
      if (autoplay) {
        playWhenReady();
      }
    }

    function renderTracks() {
      if (!trackList) return;
      trackList.innerHTML = '';
      state.tracks.forEach((track) => {
        const card = document.createElement('div');
        card.className = 'player-track';
        card.dataset.trackId = track.id;

        const mainLine = document.createElement('div');
        mainLine.className = 'player-track-line player-track-line--main';

        const playBtn = document.createElement('button');
        playBtn.type = 'button';
        playBtn.className = 'btn ghost tiny player-track-play';
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
            playWhenReady();
          } else {
            audio.pause();
          }
        });
        mainLine.appendChild(playBtn);

        const label = document.createElement('div');
        label.className = 'player-track-title';
        label.textContent = track.kind === 'source' ? 'Source' : 'Version';
        label.title = track.subtitle || '';
        mainLine.appendChild(label);

        const pills = document.createElement('div');
        pills.className = 'player-track-pills';
        if (track.summary?.voicing) {
          pills.appendChild(makePill(track.summary.voicing, 'badge-voicing'));
        }
        if (track.summary?.loudness_profile) {
          pills.appendChild(makePill(track.summary.loudness_profile, 'badge-profile'));
        }
        mainLine.appendChild(pills);

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
        mainLine.appendChild(downloads);
        card.appendChild(mainLine);

        const actions = document.createElement('div');
        actions.className = 'player-track-line player-track-line--actions';
        actions.appendChild(makeActionButton('Open in EQ', () => options?.onOpenEq?.(track.song, track)));
        actions.appendChild(makeActionButton('Analyze', () => options?.onOpenAnalyze?.(track.song, track)));
        actions.appendChild(makeActionButton('Compare', () => options?.onOpenCompare?.(track.song, track)));
        actions.appendChild(makeActionButton('AI Toolkit', () => options?.onOpenAiToolkit?.(track.song, track)));
        actions.appendChild(makeActionButton('Add as Input', () => options?.onAddInput?.(track.song, track)));
        actions.appendChild(makeActionButton('Delete', () => options?.onDeleteTrack?.(track.song, track), 'danger'));
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

    function makeActionButton(label, handler, variant) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `btn ${variant === 'danger' ? 'danger' : 'ghost'} tiny`;
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
      loadTrack(state.tracks[0], Boolean(opts && opts.autoplay));
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
      audio.addEventListener('play', () => {
        ensureScopeGraph();
        resumeScopeAudio();
        startScope();
        updateButtons();
      });
      audio.addEventListener('pause', () => {
        updateButtons();
        updateScopeOnce();
      });
      audio.addEventListener('ended', () => {
        updateButtons();
        updateWaveTime();
        updateScopeOnce();
      });
    }

    return { loadSong, clearIfSong };
  }

  window.MasteringPlayerPane = { init };
})();
