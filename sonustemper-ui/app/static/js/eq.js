(() => {
  const waveEl = document.getElementById('eqWaveform');
  const playBtn = document.getElementById('eqPlayBtn');
  const stopBtn = document.getElementById('eqStopBtn');
  const timeLabel = document.getElementById('eqTimeLabel');
  const volumeSlider = document.getElementById('eqVolume');
  const playhead = document.getElementById('eqPlayhead');
  const audioEl = document.getElementById('eqAudio');
  const spectrumCanvas = document.getElementById('eqSpectrumCanvas');
  const bandListEl = document.getElementById('eqBandList');
  const addBandBtn = document.getElementById('eqAddBandBtn');
  const resetBtn = document.getElementById('eqResetBtn');
  const bypassToggle = document.getElementById('eqBypassToggle');
  const bandEnabled = document.getElementById('eqBandEnabled');
  const bandType = document.getElementById('eqBandType');
  const bandFreq = document.getElementById('eqBandFreq');
  const bandFreqRange = document.getElementById('eqBandFreqRange');
  const bandGain = document.getElementById('eqBandGain');
  const bandGainRange = document.getElementById('eqBandGainRange');
  const bandQ = document.getElementById('eqBandQ');
  const bandQRange = document.getElementById('eqBandQRange');
  const gainField = document.getElementById('eqGainField');
  const saveBtn = document.getElementById('eqSaveBtn');
  const saveStatus = document.getElementById('eqSaveStatus');
  const saveResult = document.getElementById('eqSaveResult');
  const openCompareAfter = document.getElementById('eqOpenCompareAfter');
  const selectedName = document.getElementById('eqSelectedName');
  const selectedMeta = document.getElementById('eqSelectedMeta');
  const openCompareBtn = document.getElementById('eqOpenCompareBtn');
  const openAnalyzeBtn = document.getElementById('eqOpenAnalyzeBtn');
  const trackModeInputs = Array.from(document.querySelectorAll('input[name="eqTrackMode"]'));
  const libraryBrowserEl = document.getElementById('eqLibraryBrowser');
  let libraryBrowser = null;

  const state = {
    wave: null,
    audioCtx: null,
    sourceNode: null,
    analyser: null,
    bandNodes: new Map(),
    bands: [],
    selectedBandId: null,
    bypass: false,
    isPlaying: false,
    selected: null,
    selectedSong: null,
    selectedPath: null,
    selectedSongId: null,
    spectrumRaf: null,
    spectrumData: null,
    spectrumSmooth: null,
    trackMode: 'any',
  };

  function formatTime(total) {
    if (!Number.isFinite(total) || total <= 0) return '0:00';
    const mins = Math.floor(total / 60);
    const secs = Math.floor(total % 60);
    return `${mins}:${String(secs).padStart(2, '0')}`;
  }

  function formatFreq(freq) {
    if (!Number.isFinite(freq)) return '-';
    if (freq >= 1000) return `${(freq / 1000).toFixed(2)}k`;
    return `${Math.round(freq)}`;
  }

  function resetPlaybackState() {
    try {
      audioEl.pause();
      audioEl.currentTime = 0;
    } catch (_) {}
    state.isPlaying = false;
    if (playBtn) {
      playBtn.textContent = 'Play';
      playBtn.classList.remove('playing');
    }
    updateTimeLabel();
    updatePlayhead();
  }

  function updateTimeLabel() {
    const current = audioEl?.currentTime || 0;
    const duration = Number.isFinite(audioEl?.duration) ? audioEl.duration : 0;
    if (timeLabel) {
      timeLabel.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
    }
  }

  function updatePlayhead() {
    if (!playhead) return;
    const duration = Number.isFinite(audioEl?.duration) ? audioEl.duration : 0;
    const current = audioEl?.currentTime || 0;
    const ratio = duration > 0 ? Math.max(0, Math.min(current / duration, 1)) : 0;
    playhead.style.left = `${ratio * 100}%`;
  }

  function ensureAudioContext() {
    if (!state.audioCtx) {
      state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    return state.audioCtx;
  }

  function ensureAudioGraph() {
    const ctx = ensureAudioContext();
    if (state.sourceNode) return;
    try {
      state.sourceNode = ctx.createMediaElementSource(audioEl);
    } catch (err) {
      console.warn('[eq] createMediaElementSource failed', err);
      return;
    }
    state.analyser = ctx.createAnalyser();
    state.analyser.fftSize = 2048;
    state.analyser.smoothingTimeConstant = 0.8;
    rebuildFilterChain();
  }

  function rebuildFilterChain() {
    if (!state.sourceNode || !state.analyser) return;
    try {
      state.sourceNode.disconnect();
    } catch (_) {}
    state.bandNodes.forEach((node) => {
      try {
        node.disconnect();
      } catch (_) {}
    });
    state.bandNodes.clear();
    let lastNode = state.sourceNode;
    if (!state.bypass) {
      state.bands.forEach((band) => {
        if (!band.enabled) return;
        const node = state.audioCtx.createBiquadFilter();
        node.type = band.type;
        node.frequency.value = band.freq_hz;
        node.Q.value = band.q;
        if (node.type === 'peaking' || node.type === 'lowshelf' || node.type === 'highshelf') {
          node.gain.value = band.gain_db;
        }
        lastNode.connect(node);
        lastNode = node;
        state.bandNodes.set(band.id, node);
      });
    }
    lastNode.connect(state.analyser);
    state.analyser.connect(state.audioCtx.destination);
  }

  function updateBandNode(band) {
    const node = state.bandNodes.get(band.id);
    if (!node) return;
    node.type = band.type;
    node.frequency.value = band.freq_hz;
    node.Q.value = band.q;
    if (node.type === 'peaking' || node.type === 'lowshelf' || node.type === 'highshelf') {
      node.gain.value = band.gain_db;
    }
  }

  function addBand(band) {
    if (state.bands.length >= 8) return;
    state.bands.push(band);
    state.selectedBandId = band.id;
    rebuildFilterChain();
    renderBands();
    drawSpectrumOnce();
  }

  function removeBand(id) {
    const idx = state.bands.findIndex((b) => b.id === id);
    if (idx < 0) return;
    state.bands.splice(idx, 1);
    if (state.selectedBandId === id) {
      state.selectedBandId = state.bands[idx]?.id || state.bands[idx - 1]?.id || null;
    }
    rebuildFilterChain();
    renderBands();
    drawSpectrumOnce();
  }

  function bandDefaults() {
    return {
      id: `b_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
      type: 'peaking',
      freq_hz: 1000,
      gain_db: 0,
      q: 1,
      enabled: true,
    };
  }

  function renderBands() {
    if (!bandListEl) return;
    bandListEl.innerHTML = '';
    state.bands.forEach((band) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = `eq-band-chip${band.id === state.selectedBandId ? ' active' : ''}`;
      chip.innerHTML = `
        <span class="eq-band-type">${band.type}</span>
        <span class="eq-band-freq">${formatFreq(band.freq_hz)}Hz</span>
        <span class="eq-band-gain">${band.gain_db.toFixed(1)}dB</span>
      `;
      chip.addEventListener('click', () => {
        state.selectedBandId = band.id;
        renderBands();
        syncBandControls();
      });
      bandListEl.appendChild(chip);
    });
    syncBandControls();
  }

  function selectedBand() {
    return state.bands.find((b) => b.id === state.selectedBandId) || null;
  }

  function syncBandControls() {
    const band = selectedBand();
    if (!band) {
      bandEnabled.checked = false;
      bandType.value = 'peaking';
      bandFreq.value = '';
      bandFreqRange.value = 1000;
      bandGain.value = '';
      bandGainRange.value = 0;
      bandQ.value = '';
      bandQRange.value = 1;
      if (gainField) gainField.classList.add('disabled');
      return;
    }
    bandEnabled.checked = !!band.enabled;
    bandType.value = band.type;
    bandFreq.value = Math.round(band.freq_hz);
    bandFreqRange.value = Math.round(band.freq_hz);
    bandGain.value = band.gain_db.toFixed(1);
    bandGainRange.value = band.gain_db.toFixed(1);
    bandQ.value = band.q.toFixed(2);
    bandQRange.value = band.q.toFixed(2);
    const gainActive = band.type !== 'highpass' && band.type !== 'lowpass';
    if (gainField) gainField.classList.toggle('disabled', !gainActive);
    bandGain.disabled = !gainActive;
    bandGainRange.disabled = !gainActive;
  }

  function updateSelectedBand(patch) {
    const band = selectedBand();
    if (!band) return;
    Object.assign(band, patch);
    updateBandNode(band);
    renderBands();
    drawSpectrumOnce();
  }

  function freqFromRange(val) {
    return Math.max(20, Math.min(20000, Number(val)));
  }

  function gainFromRange(val) {
    return Math.max(-12, Math.min(12, Number(val)));
  }

  function qFromRange(val) {
    return Math.max(0.2, Math.min(12, Number(val)));
  }

  function bindBandControls() {
    bandEnabled.addEventListener('change', () => {
      const band = selectedBand();
      if (!band) return;
      band.enabled = bandEnabled.checked;
      rebuildFilterChain();
      renderBands();
      drawSpectrumOnce();
    });
    bandType.addEventListener('change', () => {
      updateSelectedBand({ type: bandType.value });
    });
    bandFreq.addEventListener('input', () => {
      updateSelectedBand({ freq_hz: freqFromRange(bandFreq.value) });
      bandFreqRange.value = bandFreq.value;
    });
    bandFreqRange.addEventListener('input', () => {
      updateSelectedBand({ freq_hz: freqFromRange(bandFreqRange.value) });
      bandFreq.value = bandFreqRange.value;
    });
    bandGain.addEventListener('input', () => {
      updateSelectedBand({ gain_db: gainFromRange(bandGain.value) });
      bandGainRange.value = bandGain.value;
    });
    bandGainRange.addEventListener('input', () => {
      updateSelectedBand({ gain_db: gainFromRange(bandGainRange.value) });
      bandGain.value = bandGainRange.value;
    });
    bandQ.addEventListener('input', () => {
      updateSelectedBand({ q: qFromRange(bandQ.value) });
      bandQRange.value = bandQ.value;
    });
    bandQRange.addEventListener('input', () => {
      updateSelectedBand({ q: qFromRange(bandQRange.value) });
      bandQ.value = bandQRange.value;
    });
    bypassToggle.addEventListener('change', () => {
      state.bypass = bypassToggle.checked;
      rebuildFilterChain();
    });
  }

  function drawSpectrumOnce() {
    if (!spectrumCanvas) return;
    const ctx = spectrumCanvas.getContext('2d');
    const { width, height } = spectrumCanvas;
    ctx.clearRect(0, 0, width, height);
  }

  function setupSpectrumCanvas() {
    if (!spectrumCanvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = spectrumCanvas.getBoundingClientRect();
    spectrumCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
    spectrumCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
    const ctx = spectrumCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
    state.spectrumData = new Uint8Array(state.analyser ? state.analyser.frequencyBinCount : 0);
    state.spectrumSmooth = new Float32Array(state.spectrumData.length);
  }

  function drawSpectrum() {
    if (!state.analyser || !spectrumCanvas) return;
    if (!state.spectrumData || !state.spectrumSmooth) {
      state.spectrumData = new Uint8Array(state.analyser.frequencyBinCount);
      state.spectrumSmooth = new Float32Array(state.spectrumData.length);
    }
    state.analyser.getByteFrequencyData(state.spectrumData);
    const ctx = spectrumCanvas.getContext('2d');
    const rect = spectrumCanvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    const bins = state.spectrumData.length;
    const alpha = 0.2;
    for (let i = 0; i < bins; i += 1) {
      const val = state.spectrumData[i];
      state.spectrumSmooth[i] = state.spectrumSmooth[i] * (1 - alpha) + val * alpha;
    }
    ctx.strokeStyle = '#4aa3ff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < bins; i += 1) {
      const freq = (i / bins) * 20000;
      const x = freqToX(freq, rect.width);
      const y = rect.height - (state.spectrumSmooth[i] / 255) * rect.height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    drawEqCurve(ctx, rect.width, rect.height);
    drawBandHandles(ctx, rect.width, rect.height);
  }

  function freqToX(freq, width) {
    const min = 20;
    const max = 20000;
    const clamped = Math.max(min, Math.min(max, freq));
    const norm = (Math.log(clamped) - Math.log(min)) / Math.log(max / min);
    return norm * width;
  }

  function gainToY(gain, height) {
    const min = -12;
    const max = 12;
    const clamped = Math.max(min, Math.min(max, gain));
    const norm = (clamped - min) / (max - min);
    return height - norm * height;
  }

  function drawEqCurve(ctx, width, height) {
    const points = 200;
    ctx.strokeStyle = '#ffa36b';
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < points; i += 1) {
      const freq = 20 * Math.pow(20000 / 20, i / (points - 1));
      let gain = 0;
      state.bands.forEach((band) => {
        if (!band.enabled || state.bypass) return;
        if (band.type === 'highpass' || band.type === 'lowpass') return;
        const q = Math.max(0.2, band.q);
        const dist = Math.log2(freq / band.freq_hz);
        const curve = Math.exp(-(dist * dist) * q);
        gain += band.gain_db * curve;
      });
      const x = freqToX(freq, width);
      const y = gainToY(gain, height);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  function drawBandHandles(ctx, width, height) {
    state.bands.forEach((band) => {
      if (!band.enabled || state.bypass) return;
      const x = freqToX(band.freq_hz, width);
      const y = gainToY(band.type === 'highpass' || band.type === 'lowpass' ? 0 : band.gain_db, height);
      ctx.fillStyle = band.id === state.selectedBandId ? '#ffd18a' : '#7aa5ff';
      ctx.beginPath();
      ctx.arc(x, y, band.id === state.selectedBandId ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function startSpectrum() {
    cancelAnimationFrame(state.spectrumRaf);
    const loop = () => {
      drawSpectrum();
      state.spectrumRaf = requestAnimationFrame(loop);
    };
    loop();
  }

  function stopSpectrum() {
    cancelAnimationFrame(state.spectrumRaf);
    state.spectrumRaf = null;
    drawSpectrumOnce();
  }

  function initWaveSurfer() {
    if (!waveEl || !window.WaveSurfer || !audioEl) return;
    if (state.wave) state.wave.destroy();
    state.wave = WaveSurfer.create({
      container: waveEl,
      waveColor: '#2a3a4f',
      progressColor: '#3b4f66',
      cursorColor: '#6b829a',
      height: 160,
      normalize: false,
      backend: 'MediaElement',
      media: audioEl,
      barWidth: 2,
      barGap: 1,
      minPxPerSec: 1,
      fillParent: true,
      autoScroll: false,
      autoCenter: false,
      dragToSeek: true,
    });
    state.wave.on('ready', () => {
      updateTimeLabel();
      updatePlayhead();
    });
    state.wave.on('interaction', (time) => {
      if (!Number.isFinite(time)) return;
      audioEl.currentTime = Math.max(0, time);
      updateTimeLabel();
      updatePlayhead();
    });
  }

  function updateSelectedSummary(selected) {
    if (selectedName) selectedName.textContent = selected?.label || selected?.title || selected?.name || '-';
    if (selectedMeta) {
      const meta = [];
      if (selected?.format) meta.push(String(selected.format).toUpperCase());
      if (selected?.duration_sec) meta.push(`${Math.round(selected.duration_sec)}s`);
      selectedMeta.textContent = meta.length ? meta.join(' · ') : 'No file selected.';
    }
  }

  function primaryRendition(renditions) {
    if (!Array.isArray(renditions)) return null;
    const order = ['wav', 'flac', 'aiff', 'aif', 'm4a', 'aac', 'mp3', 'ogg'];
    for (const fmt of order) {
      const hit = renditions.find((r) => String(r.format || '').toLowerCase() === fmt);
      if (hit) return hit;
    }
    return renditions[0] || null;
  }

  function selectTrackFromSong(song) {
    if (!song) return null;
    if (state.trackMode === 'source') {
      return { kind: 'source', rel: song.source?.rel, label: song.title, format: song.source?.format, duration_sec: song.source?.duration_sec };
    }
    if (state.trackMode === 'processed') {
      const latest = (song.versions || [])[0];
      if (latest) {
        const primary = primaryRendition(latest.renditions);
        return {
          kind: 'version',
          rel: primary?.rel || latest.rel,
          format: primary?.format,
          title: latest.title || latest.label,
          duration_sec: latest.metrics?.duration_sec,
        };
      }
    }
    return null;
  }

  function loadTrack(selected, song) {
    resetPlaybackState();
    state.selected = selected;
    state.selectedSong = song || null;
    state.selectedSongId = song?.song_id || null;
    state.selectedPath = selected?.rel || null;
    updateSelectedSummary({
      label: selected?.label || selected?.title || selected?.name || song?.title || '-',
      format: selected?.format,
      duration_sec: selected?.duration_sec,
    });
    if (!audioEl || !selected?.rel) {
      if (audioEl) audioEl.removeAttribute('src');
      if (state.wave && typeof state.wave.empty === 'function') state.wave.empty();
      stopSpectrum();
      return;
    }
    audioEl.pause();
    audioEl.currentTime = 0;
    const url = `/api/analyze/path?path=${encodeURIComponent(selected.rel)}`;
    audioEl.src = url;
    audioEl.load();
    if (state.wave) state.wave.load(url);
    updateOpenLinks();
  }

  async function loadMostRecentSelection() {
    try {
      const res = await fetch('/api/library', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      const songs = data.songs || [];
      let latest = null;
      for (const song of songs) {
        const versions = song.versions || [];
        for (const version of versions) {
          const rel = primaryRendition(version.renditions)?.rel || version.rel;
          if (!rel) continue;
          const ts = Date.parse(version.created_at || '') || 0;
          if (!latest || ts > latest.ts) {
            latest = { song, version, rel, ts };
          }
        }
      }
      if (latest) {
        loadTrack({
          kind: 'version',
          rel: latest.rel,
          label: latest.version.label || latest.version.title || 'Version',
          format: primaryRendition(latest.version.renditions)?.format,
          duration_sec: latest.version.metrics?.duration_sec,
        }, latest.song);
        return;
      }
      const demo = songs.find(song => song.song_id === 'demo_sonustemper')
        || songs.find(song => (song.title || '').toLowerCase().includes('sonustemper'));
      if (demo?.source?.rel) {
        loadTrack({ kind: 'source', rel: demo.source.rel, label: demo.title, format: demo.source.format, duration_sec: demo.source.duration_sec }, demo);
      }
    } catch (_err) {
      return;
    }
  }

  async function resolveRelSelection(rel) {
    if (!rel) return null;
    const res = await fetch('/api/library', { cache: 'no-store' });
    if (!res.ok) return null;
    const data = await res.json();
    for (const song of data.songs || []) {
      if (song.source?.rel === rel) {
        return { song, track: { kind: 'source', rel, label: song.title, format: song.source?.format, duration_sec: song.source?.duration_sec } };
      }
      for (const version of song.versions || []) {
        const primary = primaryRendition(version.renditions);
        if (primary?.rel === rel || version.rel === rel) {
          return {
            song,
            track: {
              kind: 'version',
              rel: primary?.rel || version.rel,
              label: version.label || version.title,
              format: primary?.format,
              duration_sec: version.metrics?.duration_sec,
            },
          };
        }
      }
    }
    return null;
  }

  function updateOpenLinks() {
    const rel = state.selectedPath;
    openCompareBtn.disabled = !rel;
    openAnalyzeBtn.disabled = !rel;
  }

  function handleTrackModeChange(mode) {
    state.trackMode = mode;
    if (!state.selectedSong) return;
    const track = selectTrackFromSong(state.selectedSong);
    if (track?.rel) {
      loadTrack(track, state.selectedSong);
    }
  }

  function handlePlay() {
    if (!audioEl?.src) return;
    ensureAudioGraph();
    ensureAudioContext().resume().then(() => {
      audioEl.play().then(() => {
        state.isPlaying = true;
        playBtn.textContent = 'Pause';
        playBtn.classList.add('playing');
        startSpectrum();
      }).catch((err) => {
        console.warn('[eq] play failed', err);
      });
    });
  }

  function handlePause() {
    audioEl.pause();
    state.isPlaying = false;
    playBtn.textContent = 'Play';
    playBtn.classList.remove('playing');
    stopSpectrum();
  }

  function bindEvents() {
    if (playBtn) {
      playBtn.addEventListener('click', () => {
        if (state.isPlaying) handlePause();
        else handlePlay();
      });
    }
    if (stopBtn) {
      stopBtn.addEventListener('click', () => {
        handlePause();
        audioEl.currentTime = 0;
        updatePlayhead();
        updateTimeLabel();
      });
    }
    if (volumeSlider) {
      volumeSlider.addEventListener('input', () => {
        audioEl.volume = Number(volumeSlider.value);
      });
    }
    audioEl.addEventListener('timeupdate', () => {
      updateTimeLabel();
      updatePlayhead();
    });
    audioEl.addEventListener('ended', () => {
      handlePause();
      updatePlayhead();
    });
    addBandBtn.addEventListener('click', () => addBand(bandDefaults()));
    resetBtn.addEventListener('click', () => {
      if (!confirm('Reset EQ? This will remove all bands.')) return;
      state.bands = [];
      state.selectedBandId = null;
      rebuildFilterChain();
      renderBands();
      drawSpectrumOnce();
    });
    bindBandControls();
    trackModeInputs.forEach((input) => {
      input.addEventListener('change', () => {
        if (input.checked) handleTrackModeChange(input.value);
      });
    });
    if (openCompareBtn) {
      openCompareBtn.addEventListener('click', () => {
        if (!state.selectedPath) return;
        const url = new URL('/compare', window.location.origin);
        url.searchParams.set('src', state.selectedSong?.source?.rel || state.selectedPath);
        url.searchParams.set('proc', state.selectedPath);
        window.location.assign(`${url.pathname}${url.search}`);
      });
    }
    if (openAnalyzeBtn) {
      openAnalyzeBtn.addEventListener('click', () => {
        if (!state.selectedPath) return;
        const url = new URL('/noise_removal', window.location.origin);
        url.searchParams.set('path', state.selectedPath);
        window.location.assign(`${url.pathname}${url.search}`);
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        await saveEqCopy();
      });
    }
  }

  async function saveEqCopy() {
    if (!state.selectedPath) return;
    saveStatus.textContent = 'Saving...';
    saveBtn.disabled = true;
    const bands = state.bands.map((band) => ({
      id: band.id,
      type: band.type,
      freq_hz: band.freq_hz,
      gain_db: band.gain_db,
      q: band.q,
      enabled: band.enabled,
    }));
    try {
      const res = await fetch('/api/eq/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.selectedPath,
          song_id: state.selectedSongId,
          bands,
          bypass: state.bypass,
          output_format: 'same',
        }),
      });
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err || 'render_failed');
      }
      const data = await res.json();
      const addRes = await fetch('/api/library/add_version', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          song_id: data.song_id || state.selectedSongId,
          kind: 'eq',
          label: 'EQ',
          title: state.selectedSong?.title || 'EQ',
          rel: data.output_rel,
          version_id: data.version_id,
          utility: 'EQ',
          summary: { eq: { bands } },
          metrics: data.metrics || {},
        }),
      });
      if (!addRes.ok) {
        const err = await addRes.text();
        throw new Error(err || 'library_failed');
      }
      saveStatus.textContent = 'Saved.';
      saveResult.innerHTML = '';
      const link = document.createElement('a');
      link.href = `/api/analyze/path?path=${encodeURIComponent(data.output_rel)}`;
      link.textContent = data.output_name || 'Download';
      link.className = 'btn ghost tiny';
      link.setAttribute('download', '');
      saveResult.appendChild(link);
      const openCompare = document.createElement('button');
      openCompare.type = 'button';
      openCompare.className = 'btn ghost tiny';
      openCompare.textContent = 'Open in Compare';
      openCompare.addEventListener('click', () => {
        const url = new URL('/compare', window.location.origin);
        url.searchParams.set('src', state.selectedSong?.source?.rel || state.selectedPath);
        url.searchParams.set('proc', data.output_rel);
        window.location.assign(`${url.pathname}${url.search}`);
      });
      saveResult.appendChild(openCompare);
      const openAnalyze = document.createElement('button');
      openAnalyze.type = 'button';
      openAnalyze.className = 'btn ghost tiny';
      openAnalyze.textContent = 'Open in Noise Removal';
      openAnalyze.addEventListener('click', () => {
        const url = new URL('/noise_removal', window.location.origin);
        url.searchParams.set('path', data.output_rel);
        window.location.assign(`${url.pathname}${url.search}`);
      });
      saveResult.appendChild(openAnalyze);
      if (openCompareAfter?.checked) {
        const url = new URL('/compare', window.location.origin);
        url.searchParams.set('src', state.selectedSong?.source?.rel || state.selectedPath);
        url.searchParams.set('proc', data.output_rel);
        window.location.assign(`${url.pathname}${url.search}`);
      }
      if (libraryBrowser?.reload) {
        libraryBrowser.reload();
      }
    } catch (err) {
      saveStatus.textContent = `Save failed: ${err.message || 'error'}`;
    } finally {
      saveBtn.disabled = false;
    }
  }

  function initSpectrumInteractions() {
    if (!spectrumCanvas) return;
    let dragBandId = null;
    function pointToFreqGain(x, y, rect) {
      const freq = 20 * Math.pow(20000 / 20, x / rect.width);
      const gain = 12 - (y / rect.height) * 24;
      return { freq: freqFromRange(freq), gain: gainFromRange(gain) };
    }
    function findBandAt(x, y, rect) {
      const maxDist = 10;
      let hit = null;
      state.bands.forEach((band) => {
        const bx = freqToX(band.freq_hz, rect.width);
        const by = gainToY(band.type === 'highpass' || band.type === 'lowpass' ? 0 : band.gain_db, rect.height);
        const dist = Math.hypot(bx - x, by - y);
        if (dist <= maxDist) hit = band;
      });
      return hit;
    }
    spectrumCanvas.addEventListener('mousedown', (evt) => {
      const rect = spectrumCanvas.getBoundingClientRect();
      const x = evt.clientX - rect.left;
      const y = evt.clientY - rect.top;
      const hit = findBandAt(x, y, rect);
      if (hit) {
        state.selectedBandId = hit.id;
        dragBandId = hit.id;
        renderBands();
        return;
      }
      const point = pointToFreqGain(x, y, rect);
      const band = bandDefaults();
      band.freq_hz = point.freq;
      band.gain_db = point.gain;
      addBand(band);
      dragBandId = band.id;
    });
    document.addEventListener('mousemove', (evt) => {
      if (!dragBandId) return;
      const rect = spectrumCanvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, evt.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, evt.clientY - rect.top));
      const point = pointToFreqGain(x, y, rect);
      if (state.selectedBandId !== dragBandId) {
        state.selectedBandId = dragBandId;
      }
      updateSelectedBand({ freq_hz: point.freq, gain_db: point.gain });
    });
    document.addEventListener('mouseup', () => {
      dragBandId = null;
    });
    spectrumCanvas.addEventListener('wheel', (evt) => {
      if (!evt.shiftKey) return;
      const band = selectedBand();
      if (!band) return;
      evt.preventDefault();
      const nextQ = qFromRange(band.q + (evt.deltaY > 0 ? -0.2 : 0.2));
      updateSelectedBand({ q: nextQ });
    });
  }

  function initLibrary() {
    if (!libraryBrowserEl || !window.LibraryBrowser) return;
    const browser = window.LibraryBrowser.init(libraryBrowserEl, { module: 'eq' });
    libraryBrowser = browser;
    libraryBrowserEl.addEventListener('library:select', (evt) => {
      const { song, track } = evt.detail || {};
      state.selectedSong = song;
      if (track?.rel) {
        loadTrack(track, song);
      }
    });
    document.addEventListener('library:action', (evt) => {
      const { action, song, version } = evt.detail || {};
      if (action === 'open-eq') {
        const rel = primaryRendition(version?.renditions)?.rel || version?.rel;
        if (!rel) return;
        const url = new URL('/eq', window.location.origin);
        url.searchParams.set('path', rel);
        window.location.assign(`${url.pathname}${url.search}`);
      }
    });
  }

  async function bootstrap() {
    initWaveSurfer();
    bindEvents();
    initSpectrumInteractions();
    initLibrary();
    setupSpectrumCanvas();
    window.addEventListener('resize', setupSpectrumCanvas);
    const params = new URLSearchParams(window.location.search);
    const rel = params.get('path');
    if (rel) {
      const resolved = await resolveRelSelection(rel);
      if (resolved?.track?.rel) {
        state.selectedSong = resolved.song;
        loadTrack(resolved.track, resolved.song);
        return;
      }
    }
    await loadMostRecentSelection();
  }

  bootstrap();
})();
