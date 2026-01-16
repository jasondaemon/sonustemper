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

  function buildTrackUrl(rel) {
    const url = new URL('/api/analyze/path', window.location.origin);
    url.searchParams.set('path', rel);
    return url.toString();
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
        metrics: song.source?.metrics || null,
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
        metrics: version.metrics || null,
        utility: version.utility || version.kind || null,
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
    const waveBody = root.querySelector('.player-wave-body');
    const waveTitle = root.querySelector('#playerWaveTitle');
    const waveMeta = root.querySelector('#playerWaveMeta');
    const waveTime = root.querySelector('#playerWaveTime');
    const waveDuration = root.querySelector('#playerWaveDuration');
    const clipMeta = root.querySelector('#playerWaveClipMeta');
    const trackList = root.querySelector('#playerTrackList');
    const scopeCanvas = root.querySelector('#playerLiveScope');
    const hintEl = document.getElementById('playerPaneHint');
    const audio = options?.audioEl || root.querySelector('audio');
    const state = {
      song: null,
      tracks: [],
      activeId: null,
      playheadTime: 0,
      waveform: null,
      waveLoadToken: 0,
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
      pendingAutoplay: null,
      pendingTimer: null,
      pendingSeek: null,
    };

    function PlayerWaveform(container, media, callbacks) {
      this.container = container;
      this.media = media;
      this.callbacks = callbacks || {};
      this.wave = null;
    }

    PlayerWaveform.prototype.create = function create() {
      if (!window.WaveSurfer || !this.container || !this.media) return;
      if (this.wave) {
        this.wave.destroy();
        this.wave = null;
      }
      this.wave = WaveSurfer.create({
        container: this.container,
        waveColor: '#2a3a4f',
        progressColor: '#3b4f66',
        cursorColor: '#6b829a',
        height: 160,
        barWidth: 2,
        barGap: 1,
        normalize: false,
        barAlign: 'center',
        backend: 'MediaElement',
        media: this.media,
        minPxPerSec: 1,
        fillParent: true,
        autoScroll: false,
        autoCenter: false,
        dragToSeek: true,
      });
      this.wave.on('ready', () => {
        if (this.callbacks.onReady) this.callbacks.onReady(this.wave.getDuration());
      });
      this.wave.on('audioprocess', (time) => {
        if (this.callbacks.onTime) this.callbacks.onTime(time);
      });
      this.wave.on('seek', () => {
        if (this.callbacks.onTime) this.callbacks.onTime(this.wave.getCurrentTime());
      });
      this.wave.on('finish', () => {
        if (this.callbacks.onFinish) this.callbacks.onFinish();
      });
      this.wave.on('error', (err) => {
        const msg = String(err || '');
        if (msg.includes('AbortError')) return;
      });
    };

    PlayerWaveform.prototype.load = function load(url) {
      if (!this.wave) this.create();
      if (!this.wave) return null;
      return this.wave.load(url);
    };

    PlayerWaveform.prototype.play = function play() {
      if (this.wave) this.wave.play();
    };

    PlayerWaveform.prototype.pause = function pause() {
      if (this.wave) this.wave.pause();
    };

    PlayerWaveform.prototype.stop = function stop() {
      if (this.wave) this.wave.stop();
    };

    PlayerWaveform.prototype.seekTo = function seekTo(seconds) {
      if (!this.wave) return;
      const duration = this.wave.getDuration() || this.media?.duration || 0;
      if (!duration) return;
      this.wave.seekTo(Math.max(0, Math.min(1, seconds / duration)));
    };

    PlayerWaveform.prototype.destroy = function destroy() {
      if (this.wave) {
        this.wave.destroy();
        this.wave = null;
      }
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

    function updateWaveTime(forcedTime) {
      if (!audio || !waveTime || !waveDuration) return;
      const time = typeof forcedTime === 'number' ? forcedTime : (audio.currentTime || 0);
      waveTime.textContent = formatDuration(time);
      waveDuration.textContent = formatDuration(audio.duration || 0);
      state.playheadTime = time;
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

    function ensureWaveform(url) {
      if (!waveContainer || !audio || !window.WaveSurfer) return;
      const token = ++state.waveLoadToken;
      waveContainer.innerHTML = '';
      if (state.waveform) {
        state.waveform.destroy();
        state.waveform = null;
      }
      state.waveform = new PlayerWaveform(waveContainer, audio, {
        onReady: () => {
          if (token !== state.waveLoadToken) return;
          applyPendingSeek();
          updateWaveTime();
          if (state.pendingAutoplay && state.pendingAutoplay === state.activeId) {
            state.pendingAutoplay = null;
            if (state.pendingTimer) {
              clearTimeout(state.pendingTimer);
              state.pendingTimer = null;
            }
            playWhenReady();
          }
        },
        onTime: (time) => updateWaveTime(time),
        onFinish: () => {
          updateButtons();
          updateWaveTime();
        },
      });
      const result = state.waveform.load(url);
      if (result && typeof result.catch === 'function') {
        result.catch(() => {});
      }
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
      const width = waveContainer?.clientWidth || scopeCanvas.clientWidth;
      const height = waveContainer?.clientHeight || scopeCanvas.clientHeight;
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

    function applyPendingSeek() {
      if (!audio || state.pendingSeek === null) return;
      if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
      const target = Math.max(0, Math.min(state.pendingSeek, Math.max(0, audio.duration - 0.01)));
      if (state.waveform) {
        state.waveform.seekTo(target);
      } else {
        audio.currentTime = target;
      }
      state.pendingSeek = null;
      updateWaveTime();
    }

    function playWhenReady() {
      if (!audio) return;
      ensureScopeGraph();
      resumeScopeAudio();
      if (state.pendingSeek !== null && (!Number.isFinite(audio.duration) || audio.duration <= 0)) {
        const onReady = () => {
          audio.removeEventListener('loadedmetadata', onReady);
          applyPendingSeek();
          if (state.waveform) {
            state.waveform.play();
          } else {
            audio.play().catch(() => {});
          }
        };
        audio.addEventListener('loadedmetadata', onReady, { once: true });
        return;
      }
      applyPendingSeek();
      if (audio.readyState >= 2) {
        if (state.waveform) {
          state.waveform.play();
        } else {
          audio.play().catch(() => {});
        }
        return;
      }
      const onReady = () => {
        audio.removeEventListener('canplay', onReady);
        applyPendingSeek();
        if (state.waveform) {
          state.waveform.play();
        } else {
          audio.play().catch(() => {});
        }
      };
      audio.addEventListener('canplay', onReady, { once: true });
    }

    function setClipState(track) {
      if (!waveBody) return;
      const metrics = normalizeMetrics(track?.metrics) || {};
      const tp = metrics.true_peak_dbtp ?? metrics.true_peak_db ?? metrics.true_peak ?? metrics.TP;
      const peak = metrics.peak_level ?? metrics.peak_db ?? metrics.peak;
      const clip = (typeof tp === 'number' && tp >= 0) || (typeof peak === 'number' && peak >= 0);
      waveBody.classList.toggle('is-clip', Boolean(clip));
      if (clipMeta) {
        const tpMargin = metrics.tp_margin;
        if (typeof tpMargin === 'number' && Number.isFinite(tpMargin)) {
          clipMeta.textContent = `TP Margin ${tpMargin.toFixed(1)}`;
        } else {
          clipMeta.textContent = 'TP Margin —';
        }
      }
    }

    function loadTrack(track, autoplay) {
      if (!audio || !track?.rel) return;
      const isNew = state.activeId !== track.id;
      const wasPlaying = !audio.paused;
      const resumeTime = isNew && wasPlaying ? audio.currentTime : null;
      if (wasPlaying && isNew && state.waveform) {
        state.waveform.stop();
      } else if (wasPlaying && isNew) {
        audio.pause();
      }
      if (isNew) {
        state.pendingSeek = resumeTime !== null ? resumeTime : 0;
      }
      state.activeId = track.id;
      updateWaveMeta(track);
      setClipState(track);
      const nextUrlAbs = buildTrackUrl(track.rel);
      const currentAbs = String(audio.currentSrc || audio.src || '');
      const srcChanged = currentAbs !== nextUrlAbs;
      if (srcChanged) {
        if (state.waveform) state.waveform.stop();
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
        audio.src = nextUrlAbs;
        audio.load();
        ensureWaveform(nextUrlAbs);
      } else if (isNew) {
        ensureWaveform(nextUrlAbs);
      } else if (state.pendingSeek !== null) {
        applyPendingSeek();
      }
      updateWaveTime();
      updateButtons();
      if (autoplay) {
        if (srcChanged) {
          state.pendingAutoplay = track.id;
          if (state.pendingTimer) clearTimeout(state.pendingTimer);
          state.pendingTimer = setTimeout(() => {
            if (state.pendingAutoplay === track.id) {
              state.pendingAutoplay = null;
              playWhenReady();
            }
          }, 800);
        } else {
          playWhenReady();
        }
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
          if (state.waveform) {
            state.waveform.pause();
          } else {
            audio.pause();
          }
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
        if (track.kind === 'version' && track.utility) {
          pills.appendChild(makePill(track.utility, 'badge-utility'));
        }
        if (track.summary?.voicing) {
          pills.appendChild(makePill(track.summary.voicing, 'badge-voicing'));
        }
        if (track.summary?.loudness_profile) {
          pills.appendChild(makePill(track.summary.loudness_profile, 'badge-profile'));
        }
        mainLine.appendChild(pills);

        const metricsData = renderMetricPills(track);
        let metricDetails = null;
        if (metricsData) {
          const metricsWrap = document.createElement('div');
          metricsWrap.className = 'player-track-metrics';
          metricsWrap.appendChild(metricsData.container);
          mainLine.appendChild(metricsWrap);
          if (metricsData.rows && metricsData.rows.length > 4 && metricsData.overflowBtn) {
            metricDetails = renderMetricDetails(metricsData.rows);
            metricsData.overflowBtn.addEventListener('click', (evt) => {
              evt.stopPropagation();
              const shouldShow = metricDetails.hidden;
              metricDetails.hidden = !shouldShow;
              metricsData.overflowBtn.classList.toggle('is-open', shouldShow);
            });
          }
        }
        card.appendChild(mainLine);
        if (metricDetails) {
          card.appendChild(metricDetails);
        }

        const actions = document.createElement('div');
        actions.className = 'player-track-line player-track-line--actions';
        actions.appendChild(makeActionButton('Open in EQ', () => options?.onOpenEq?.(track.song, track)));
        actions.appendChild(makeActionButton('Analyze', () => options?.onOpenAnalyze?.(track.song, track)));
        actions.appendChild(makeActionButton('Compare', () => options?.onOpenCompare?.(track.song, track)));
        actions.appendChild(makeActionButton('AI Toolkit', () => options?.onOpenAiToolkit?.(track.song, track)));
        actions.appendChild(makeActionButton('Add as Input', () => options?.onAddInput?.(track.song, track)));
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
        actions.appendChild(downloads);
        actions.appendChild(makeActionButton('Delete', () => options?.onDeleteTrack?.(track.song, track), 'danger'));
        card.appendChild(actions);

        trackList.appendChild(card);
      });
      updateButtons();
    }

    function normalizeMetrics(metrics) {
      if (!metrics || typeof metrics !== 'object') return null;
      if (metrics.output && typeof metrics.output === 'object') return metrics.output;
      if (metrics.input && typeof metrics.input === 'object') return metrics.input;
      return metrics;
    }

    function metricValue(metrics, keys) {
      const normalized = normalizeMetrics(metrics);
      if (!normalized || typeof normalized !== 'object') return null;
      const list = Array.isArray(keys) ? keys : [keys];
      for (const key of list) {
        if (typeof normalized[key] === 'number') return normalized[key];
      }
      return null;
    }

    function formatDelta(value, sourceValue) {
      if (typeof value !== 'number' || typeof sourceValue !== 'number') return '';
      const diff = value - sourceValue;
      if (!Number.isFinite(diff)) return '';
      const sign = diff >= 0 ? '+' : '';
      return ` (${sign}${diff.toFixed(1)})`;
    }

    function buildMetricData(track) {
      const metrics = normalizeMetrics(track.metrics);
      const sourceMetrics = normalizeMetrics(track.song?.source?.metrics);
      if (track.kind === 'version') {
        if (!metrics) return null;
      } else if (!metrics && !sourceMetrics) {
        return null;
      }
      const displayMetrics = track.kind === 'version' ? metrics : (metrics || sourceMetrics);
      const items = [
        { label: 'LUFS', keys: ['lufs_i', 'lufs', 'I'], unit: '' },
        { label: 'TP', keys: ['true_peak_dbtp', 'true_peak_db', 'true_peak', 'TP'], unit: '' },
        { label: 'LRA', keys: ['lra', 'LRA'], unit: '' },
        { label: 'Crest', keys: ['crest_db', 'crest', 'crest_factor'], unit: '' },
        { label: 'RMS', keys: ['rms_db', 'rms_level'], unit: '' },
        { label: 'Peak', keys: ['peak_db', 'peak_level'], unit: '' },
        { label: 'DR', keys: ['dynamic_range_db', 'dynamic_range'], unit: '' },
        { label: 'Noise', keys: ['noise_floor_db', 'noise_floor'], unit: '' },
        { label: 'Corr', keys: ['stereo_corr'], unit: '' },
        { label: 'Width', keys: ['width'], unit: '' },
        { label: 'Target I', keys: ['target_i', 'target_I'], unit: '' },
        { label: 'Target TP', keys: ['target_tp', 'target_TP'], unit: '' },
        { label: 'TP Margin', keys: ['tp_margin'], unit: '' },
        { label: 'Dur', keys: ['duration_sec'], unit: 's' },
      ];
      const canDelta = track.kind === 'version' && metrics && sourceMetrics;
      const rows = [];
      items.forEach((item) => {
        const value = metricValue(displayMetrics, item.keys);
        if (typeof value !== 'number') return;
        const sourceValue = metricValue(sourceMetrics, item.keys);
        const delta = canDelta ? formatDelta(value, sourceValue) : '';
        const valueText = item.label === 'Dur'
          ? `${Math.round(value)}${item.unit}${delta}`
          : `${value.toFixed(1)}${item.unit}${delta}`;
        rows.push({
          label: item.label,
          value: valueText,
          pill: `${item.label} ${valueText}`,
        });
      });
      return rows.length ? { rows } : null;
    }

    function renderMetricPills(track) {
      const data = buildMetricData(track);
      if (!data) return null;
      const container = document.createElement('div');
      container.className = 'player-track-metric-pills';
      const primary = data.rows.slice(0, 4);
      primary.forEach((row) => {
        const pill = document.createElement('span');
        pill.className = 'badge badge-param';
        pill.textContent = row.pill;
        container.appendChild(pill);
      });
      const remaining = data.rows.length - primary.length;
      let overflowBtn = null;
      if (remaining > 0) {
        overflowBtn = document.createElement('button');
        overflowBtn.type = 'button';
        overflowBtn.className = 'badge badge-param player-track-metric-toggle';
        overflowBtn.textContent = '▾';
        overflowBtn.setAttribute('aria-label', 'Show metrics');
        container.appendChild(overflowBtn);
      }
      return { container, rows: data.rows, overflowBtn };
    }

    function renderMetricDetails(rows) {
      const details = document.createElement('div');
      details.className = 'player-track-details';
      details.hidden = true;
      const table = document.createElement('div');
      table.className = 'player-metrics-table';
      rows.forEach((row) => {
        const item = document.createElement('div');
        item.className = 'player-metrics-item';
        const label = document.createElement('span');
        label.className = 'player-metrics-label';
        label.textContent = row.label;
        const value = document.createElement('span');
        value.className = 'player-metrics-value';
        value.textContent = row.value;
        item.appendChild(label);
        item.appendChild(value);
        table.appendChild(item);
      });
      details.appendChild(table);
      return details;
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
      if (waveBody) waveBody.classList.remove('is-clip');
      if (audio) {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
      }
      if (state.waveform) {
        state.waveform.destroy();
        state.waveform = null;
      }
    }

    if (audio) {
      audio.addEventListener('timeupdate', updateWaveTime);
      audio.addEventListener('loadedmetadata', () => {
        applyPendingSeek();
        updateWaveTime();
      });
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
        if (state.waveform) {
          state.waveform.stop();
        }
        updateButtons();
        updateWaveTime();
        updateScopeOnce();
      });
    }

    return { loadSong, clearIfSong };
  }

  window.MasteringPlayerPane = { init };
})();
