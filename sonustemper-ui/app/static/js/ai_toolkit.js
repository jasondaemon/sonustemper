(() => {
  const toolDefs = [
    {
      id: 'ai_deglass',
      title: 'Reduce AI Hiss / Glass',
      subtitle: 'Soften brittle top-end and reduce hiss.',
      fallback: 35,
    },
    {
      id: 'ai_vocal_smooth',
      title: 'Smooth Harsh Vocals',
      subtitle: 'Tame harsh presence and sibilant edges.',
      fallback: 30,
    },
    {
      id: 'ai_bass_tight',
      title: 'Tighten Bass / Remove Rumble',
      subtitle: 'Clean sub rumble and low-mid mud.',
      fallback: 40,
    },
    {
      id: 'ai_transient_soften',
      title: 'Reduce Pumping / Over-Transients',
      subtitle: 'Soften clicky transients and harsh bite.',
      fallback: 25,
    },
    {
      id: 'ai_platform_safe',
      title: 'Platform Ready (AI Safe Loudness)',
      subtitle: 'Safer loudness for streaming without harsh clipping.',
      fallback: 0,
    },
  ];

  const aiPairBrowser = document.getElementById('aiPairBrowser');
  const aiAnyBrowser = document.getElementById('aiAnyBrowser');
  const aiPairWrap = document.getElementById('aiPairBrowserWrap');
  const aiAnyWrap = document.getElementById('aiAnyBrowserWrap');
  const aiAnyUploadBtn = document.getElementById('aiAnyUploadBtn');
  const aiAnyUploadInput = document.getElementById('aiAnyUploadInput');
  const aiAnyUploadStatus = document.getElementById('aiAnyUploadStatus');
  const aiSelectedName = document.getElementById('aiSelectedName');
  const aiSelectedMeta = document.getElementById('aiSelectedMeta');
  const aiWaveform = document.getElementById('aiWaveform');
  const aiAudio = document.getElementById('aiAudio');
  const aiPlayBtn = document.getElementById('aiPlayBtn');
  const aiStopBtn = document.getElementById('aiStopBtn');
  const aiTimeLabel = document.getElementById('aiTimeLabel');
  const aiClipRiskPill = document.getElementById('aiClipRiskPill');
  const aiEngineIndicator = document.getElementById('aiEngineIndicator');
  const aiEngineDebug = document.getElementById('aiEngineDebug');
  const aiSpectrumCanvas = document.getElementById('aiSpectrumCanvas');
  const aiSaveBtn = document.getElementById('aiSaveBtn');
  const aiSaveStatus = document.getElementById('aiSaveStatus');
  const aiSaveResult = document.getElementById('aiSaveResult');

  const modeRadios = Array.from(document.querySelectorAll('input[name="aiSourceMode"]'));
  const toolRows = Array.from(document.querySelectorAll('.ai-tool-row'));

  const state = {
    mode: 'source',
    source: null,
    processed: null,
    any: null,
    selected: null,
    duration: null,
    wave: null,
    audioCtx: null,
    nodes: null,
    analyser: null,
    spectrum: {
      data: null,
      smooth: null,
      raf: null,
      last: 0,
    },
    strengths: {},
    resizing: null,
    dryIsolation: null,
  };

  function formatTime(raw) {
    const secs = Number(raw);
    if (!Number.isFinite(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  function setMode(mode) {
    state.mode = mode;
    if (aiPairWrap) aiPairWrap.hidden = mode === 'any';
    if (aiAnyWrap) aiAnyWrap.hidden = mode !== 'any';
    updateSelectedFromMode();
  }

  function setActiveItem(container, node) {
    if (!container) return;
    container.querySelectorAll('.browser-item.active').forEach((el) => el.classList.remove('active'));
    if (node) node.classList.add('active');
  }

  function setPlayState(playing) {
    if (!aiPlayBtn) return;
    aiPlayBtn.textContent = playing ? 'Pause' : 'Play';
  }

  function updateTimeLabel() {
    if (!aiTimeLabel) return;
    const current = aiAudio ? aiAudio.currentTime : 0;
    const duration = Number.isFinite(state.duration) ? state.duration : (aiAudio ? aiAudio.duration : 0);
    aiTimeLabel.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
  }

  function updateSaveState() {
    if (aiSaveBtn) aiSaveBtn.disabled = !state.selected;
  }

  function updateEngineIndicator() {
    if (!aiEngineIndicator) return;
    const ctx = state.audioCtx;
    if (state.nodes && ctx) {
      if (ctx.state === 'running') {
        if (state.dryIsolation === true) {
          aiEngineIndicator.textContent = 'Audio Engine: ON (isolated)';
        } else if (state.dryIsolation === false) {
          aiEngineIndicator.textContent = 'Audio Engine: ON (dry+wet)';
        } else {
          aiEngineIndicator.textContent = 'Audio Engine: ON';
        }
        aiEngineIndicator.classList.add('is-on');
        return;
      }
    }
    aiEngineIndicator.textContent = 'Audio Engine: click Play';
    aiEngineIndicator.classList.remove('is-on');
  }

  function sampleSignalMax() {
    if (!state.analyser) return 0;
    const data = new Uint8Array(state.analyser.frequencyBinCount);
    state.analyser.getByteFrequencyData(data);
    let max = 0;
    for (let i = 0; i < data.length; i += 1) {
      if (data[i] > max) max = data[i];
    }
    return max;
  }

  function applyDryIsolation() {
    if (!aiAudio) return;
    if (state.dryIsolation === true) {
      aiAudio.muted = false;
      aiAudio.volume = 0;
      return;
    }
    aiAudio.muted = false;
    aiAudio.volume = 1;
  }

  function probeDryIsolation() {
    if (!aiAudio || !state.analyser || aiAudio.paused) return;
    if (state.dryIsolation !== null) {
      applyDryIsolation();
      updateEngineIndicator();
      return;
    }
    const prevVol = aiAudio.volume;
    aiAudio.volume = 0;
    setTimeout(() => {
      const max = sampleSignalMax();
      if (max > 2) {
        state.dryIsolation = true;
        aiAudio.volume = 0;
      } else {
        state.dryIsolation = false;
        aiAudio.volume = prevVol || 1;
      }
      updateEngineIndicator();
    }, 120);
  }

  function setClipRisk(active) {
    if (!aiClipRiskPill) return;
    aiClipRiskPill.hidden = !active;
  }

  function hasClipRisk(metrics) {
    if (!metrics || typeof metrics !== 'object') return false;
    const clipped = Number(metrics.clipped_samples ?? 0);
    const peak = Number(metrics.peak_level);
    if (Number.isFinite(clipped) && clipped > 0) return true;
    if (Number.isFinite(peak) && peak >= -0.2) return true;
    return false;
  }

  function updateSelectedSummary(selected) {
    if (!aiSelectedName || !aiSelectedMeta) return;
    if (!selected) {
      aiSelectedName.textContent = '-';
      aiSelectedMeta.textContent = 'No file selected.';
      setClipRisk(false);
      return;
    }
    aiSelectedName.textContent = selected.name || selected.rel || 'Selected';
  }

  async function updateFileInfo(rel) {
    if (!rel) return;
    try {
      const res = await fetch(`/api/ai-tool/info?path=${encodeURIComponent(rel)}`);
      if (!res.ok) throw new Error('info_failed');
      const data = await res.json();
      const parts = [];
      if (data.duration_s) parts.push(`Duration: ${formatTime(data.duration_s)}`);
      if (data.sample_rate) parts.push(`Sample rate: ${data.sample_rate} Hz`);
      if (data.channels) parts.push(`${data.channels} ch`);
      if (aiSelectedMeta) {
        aiSelectedMeta.textContent = parts.length ? parts.join(' · ') : 'Metadata unavailable.';
      }
      if (Number.isFinite(data.duration_s)) state.duration = data.duration_s;
    } catch (_err) {
      if (aiSelectedMeta) aiSelectedMeta.textContent = 'Metadata unavailable.';
    }
  }

  function updateSelectedFromMode() {
    if (state.mode === 'any') {
      setSelectedFile(state.any);
      return;
    }
    if (state.mode === 'processed') {
      setSelectedFile(state.processed || state.source);
      return;
    }
    setSelectedFile(state.source || state.processed);
  }

  function resetSpectrumBuffers() {
    if (!state.analyser) return;
    const len = state.analyser.frequencyBinCount;
    state.spectrum.data = new Uint8Array(len);
    state.spectrum.smooth = new Float32Array(len);
  }

  function ensureAudioContext() {
    if (state.audioCtx) return state.audioCtx;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    state.audioCtx = new Ctx();
    return state.audioCtx;
  }

  function setParam(param, value) {
    if (!param) return;
    const ctx = state.audioCtx;
    const now = ctx ? ctx.currentTime : 0;
    try {
      param.cancelScheduledValues(now);
      param.setTargetAtTime(value, now, 0.03);
    } catch (_err) {
      try {
        param.value = value;
      } catch (_err2) {
        return;
      }
    }
  }

  function buildAudioGraph() {
    if (!aiAudio) return;
    const ctx = ensureAudioContext();
    if (!ctx) return;
    if (state.nodes) return;
    let mediaSource = null;
    try {
      mediaSource = ctx.createMediaElementSource(aiAudio);
    } catch (_err) {
      return;
    }
    aiAudio.muted = false;
    aiAudio.volume = 1;
    const bassHp = ctx.createBiquadFilter();
    bassHp.type = 'highpass';
    bassHp.frequency.value = 25;
    const bassMud = ctx.createBiquadFilter();
    bassMud.type = 'peaking';
    bassMud.frequency.value = 220;
    bassMud.Q.value = 1.0;
    bassMud.gain.value = 0;

    const vocalSmooth = ctx.createBiquadFilter();
    vocalSmooth.type = 'peaking';
    vocalSmooth.frequency.value = 4500;
    vocalSmooth.Q.value = 1.1;
    vocalSmooth.gain.value = 0;

    const sibilant = ctx.createBiquadFilter();
    sibilant.type = 'peaking';
    sibilant.frequency.value = 7500;
    sibilant.Q.value = 2.0;
    sibilant.gain.value = 0;

    const deglassShelf = ctx.createBiquadFilter();
    deglassShelf.type = 'highshelf';
    deglassShelf.frequency.value = 11000;
    deglassShelf.gain.value = 0;

    const deglassLp = ctx.createBiquadFilter();
    deglassLp.type = 'lowpass';
    deglassLp.frequency.value = 20000;

    const transientComp = ctx.createDynamicsCompressor();
    transientComp.threshold.value = -18;
    transientComp.ratio.value = 2;
    transientComp.attack.value = 0.02;
    transientComp.release.value = 0.25;

    const platformComp = ctx.createDynamicsCompressor();
    platformComp.threshold.value = -14;
    platformComp.ratio.value = 2;
    platformComp.attack.value = 0.01;
    platformComp.release.value = 0.3;

    const outputGain = ctx.createGain();
    outputGain.gain.value = 1;

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.8;

    mediaSource
      .connect(bassHp)
      .connect(bassMud)
      .connect(vocalSmooth)
      .connect(sibilant)
      .connect(deglassShelf)
      .connect(deglassLp)
      .connect(transientComp)
      .connect(platformComp)
      .connect(outputGain)
      .connect(analyser)
      .connect(ctx.destination);

    state.nodes = {
      mediaSource,
      bassHp,
      bassMud,
      vocalSmooth,
      sibilant,
      deglassShelf,
      deglassLp,
      transientComp,
      platformComp,
      outputGain,
    };
    state.analyser = analyser;
    applyDryIsolation();
    updateEngineIndicator();
    if (ctx) {
      ctx.onstatechange = () => updateEngineIndicator();
    }
    resetSpectrumBuffers();
  }

  function updateFilters() {
    if (!state.nodes) {
      if (aiEngineDebug) aiEngineDebug.textContent = '';
      return;
    }
    const ctx = state.audioCtx;
    if (!ctx) return;
    const s = state.strengths;
    const deglass = (s.ai_deglass || 0) / 100;
    const vocal = (s.ai_vocal_smooth || 0) / 100;
    const bass = (s.ai_bass_tight || 0) / 100;
    const trans = (s.ai_transient_soften || 0) / 100;
    const platform = (s.ai_platform_safe || 0) / 100;

    const hpFreq = 25 + 30 * bass;
    setParam(state.nodes.bassHp.frequency, hpFreq);
    setParam(state.nodes.bassMud.gain, -3 * bass);

    setParam(state.nodes.vocalSmooth.gain, -4 * vocal);
    const sBoost = Math.max(0, (s.ai_vocal_smooth || 0) - 60) / 40;
    setParam(state.nodes.sibilant.gain, -2 * sBoost);

    setParam(state.nodes.deglassShelf.gain, -4 * deglass);
    const lpFreq = deglass > 0.3 ? 18000 - 3000 * ((deglass - 0.3) / 0.7) : 20000;
    setParam(state.nodes.deglassLp.frequency, lpFreq);

    const transThreshold = -18 - (6 * trans);
    const transRatio = 2 + trans;
    setParam(state.nodes.transientComp.threshold, transThreshold);
    setParam(state.nodes.transientComp.ratio, transRatio);

    const platformThreshold = -14 - (8 * platform);
    const platformRatio = 2 + platform * 1.2;
    setParam(state.nodes.platformComp.threshold, platformThreshold);
    setParam(state.nodes.platformComp.ratio, platformRatio);

    const gainDb = -3 * platform;
    const gain = Math.pow(10, gainDb / 20);
    setParam(state.nodes.outputGain.gain, gain);
    if (aiEngineDebug) {
      const deglassDb = -4 * deglass;
      aiEngineDebug.textContent = `Deglass shelf: ${deglassDb.toFixed(2)} dB`;
    }
  }

  function computeEffectCurve(width, height) {
    const points = [];
    const n = 96;
    const minF = 20;
    const maxF = 20000;
    const logMin = Math.log10(minF);
    const logMax = Math.log10(maxF);
    const s = state.strengths;
    const deglass = (s.ai_deglass || 0) / 100;
    const vocal = (s.ai_vocal_smooth || 0) / 100;
    const bass = (s.ai_bass_tight || 0) / 100;
    const trans = (s.ai_transient_soften || 0) / 100;
    const platform = (s.ai_platform_safe || 0) / 100;
    const range = 6;

    const gauss = (x, sigma) => Math.exp(-(x * x) / (2 * sigma * sigma));

    for (let i = 0; i < n; i += 1) {
      const t = i / (n - 1);
      const f = Math.pow(10, logMin + (logMax - logMin) * t);
      let db = 0;

      const hp = 25 + 30 * bass;
      if (f < hp && bass > 0) {
        db += -3 * (1 - f / hp) * bass;
      }
      db += -3 * bass * gauss(Math.log2(f / 220), 0.6);

      db += -4 * vocal * gauss(Math.log2(f / 4500), 0.5);
      if ((s.ai_vocal_smooth || 0) > 60) {
        const extra = ((s.ai_vocal_smooth || 0) - 60) / 40;
        db += -2 * extra * gauss(Math.log2(f / 7500), 0.45);
      }

      const shelf = 1 / (1 + Math.exp(-5 * Math.log2(f / 11000)));
      db += -4 * deglass * shelf;
      if (deglass > 0.3 && f > 15000) {
        db += -2 * (deglass - 0.3);
      }

      db += -2 * trans * gauss(Math.log2(f / 3200), 0.5);
      db += -2 * trans * gauss(Math.log2(f / 8000), 0.6);

      db += -3 * platform;

      db = Math.max(-range, Math.min(range, db));
      const x = t * width;
      const y = height * 0.5 - (db / range) * (height * 0.5);
      points.push({ x, y });
    }
    return points;
  }

  function drawSpectrumFrame() {
    if (!aiSpectrumCanvas) return;
    const ctx = aiSpectrumCanvas.getContext('2d');
    if (!ctx) return;
    const now = performance.now();
    if (now - state.spectrum.last < 33) {
      state.spectrum.raf = requestAnimationFrame(drawSpectrumFrame);
      return;
    }
    state.spectrum.last = now;
    const width = aiSpectrumCanvas.clientWidth;
    const height = aiSpectrumCanvas.clientHeight;
    if (!width || !height) {
      state.spectrum.raf = requestAnimationFrame(drawSpectrumFrame);
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    if (aiSpectrumCanvas.width !== Math.round(width * dpr) || aiSpectrumCanvas.height !== Math.round(height * dpr)) {
      aiSpectrumCanvas.width = Math.round(width * dpr);
      aiSpectrumCanvas.height = Math.round(height * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    if (state.analyser && state.spectrum.data && state.spectrum.smooth) {
      state.analyser.getByteFrequencyData(state.spectrum.data);
      for (let i = 0; i < state.spectrum.data.length; i += 1) {
        const next = state.spectrum.data[i];
        state.spectrum.smooth[i] = state.spectrum.smooth[i] * 0.85 + next * 0.15;
      }

      const len = state.spectrum.smooth.length;
      const minF = 20;
      const maxF = 20000;
      const logMin = Math.log10(minF);
      const logMax = Math.log10(maxF);

      ctx.strokeStyle = '#2a3a4f';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      for (let i = 0; i < len; i += 1) {
        const freq = (i / len) * (maxF - minF) + minF;
        const t = (Math.log10(freq) - logMin) / (logMax - logMin);
        const x = t * width;
        const mag = state.spectrum.smooth[i] / 255;
        const y = height - mag * height;
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }

    const curve = computeEffectCurve(width, height);
    ctx.strokeStyle = '#ff8a3d';
    ctx.lineWidth = 2;
    ctx.beginPath();
    curve.forEach((pt, idx) => {
      if (idx === 0) ctx.moveTo(pt.x, pt.y);
      else ctx.lineTo(pt.x, pt.y);
    });
    ctx.stroke();

    state.spectrum.raf = requestAnimationFrame(drawSpectrumFrame);
  }

  function startSpectrum() {
    if (state.spectrum.raf) return;
    state.spectrum.last = 0;
    state.spectrum.raf = requestAnimationFrame(drawSpectrumFrame);
  }

  function stopSpectrum() {
    if (state.spectrum.raf) {
      cancelAnimationFrame(state.spectrum.raf);
      state.spectrum.raf = null;
    }
  }

  function updateSliderRow(row) {
    const slider = row.querySelector('[data-role="strength"]');
    const label = row.querySelector('[data-role="strength-value"]');
    if (!slider || !label) return;
    label.textContent = slider.value;
  }

  function setToolStrength(id, value) {
    const row = toolRows.find((r) => r.dataset.toolId === id);
    if (!row) return;
    const slider = row.querySelector('[data-role="strength"]');
    if (!slider) return;
    slider.value = String(value);
    updateSliderRow(row);
    state.strengths[id] = parseInt(slider.value || '0', 10) || 0;
  }

  function updateAllStrengths() {
    toolRows.forEach((row) => {
      const slider = row.querySelector('[data-role="strength"]');
      if (!slider) return;
      const val = parseInt(slider.value || '0', 10) || 0;
      state.strengths[row.dataset.toolId] = val;
      updateSliderRow(row);
    });
  }

  function updateSpectrumOnce() {
    stopSpectrum();
    state.spectrum.last = 0;
    drawSpectrumFrame();
  }

  function applyDetectorDefaults(rel) {
    const defaults = {};
    toolDefs.forEach((tool) => {
      defaults[tool.id] = tool.fallback;
    });

    const applyDefaults = () => {
      Object.entries(defaults).forEach(([id, value]) => setToolStrength(id, value));
      updateAllStrengths();
      updateFilters();
      updateSpectrumOnce();
    };

    if (!rel) {
      setClipRisk(false);
      applyDefaults();
      return;
    }

    fetch(`/api/ai-tool/detect?path=${encodeURIComponent(rel)}&mode=fast`, { cache: 'no-store' })
      .then((res) => {
        if (!res.ok) throw new Error('detect_failed');
        return res.json();
      })
      .then((data) => {
        const findings = Array.isArray(data.findings) ? data.findings : [];
        findings.forEach((finding) => {
          const toolId = finding.suggested_tool_id;
          if (!toolId || !(toolId in defaults)) return;
          const severity = Number(finding.severity || 0);
          const strength = Math.min(70, Math.max(0, Math.round(severity * 80)));
          defaults[toolId] = Math.max(defaults[toolId], strength);
        });
        setClipRisk(hasClipRisk(data?.metrics?.fullband));
        applyDefaults();
      })
      .catch(() => {
        setClipRisk(false);
        applyDefaults();
      });
  }

  function setSelectedFile(selected) {
    state.selected = selected;
    state.dryIsolation = null;
    updateSelectedSummary(selected);
    updateSaveState();
    if (!aiAudio || !selected?.rel) {
      if (aiAudio) aiAudio.removeAttribute('src');
      if (state.wave && typeof state.wave.empty === 'function') state.wave.empty();
      stopSpectrum();
      return;
    }
    applyDryIsolation();
    const url = `/api/analyze/path?path=${encodeURIComponent(selected.rel)}`;
    aiAudio.src = url;
    aiAudio.load();
    if (state.wave) {
      state.wave.load(url);
    }
    updateFileInfo(selected.rel);
    applyDetectorDefaults(selected.rel);
  }

  function initWaveSurfer() {
    if (!aiWaveform || !aiAudio || !window.WaveSurfer) return;
    if (state.wave) {
      state.wave.destroy();
    }
    state.wave = WaveSurfer.create({
      container: aiWaveform,
      waveColor: '#2a3a4f',
      progressColor: '#3b4f66',
      cursorColor: '#6b829a',
      height: 160,
      barWidth: 2,
      barGap: 1,
      normalize: true,
      backend: 'MediaElement',
      media: aiAudio,
      minPxPerSec: 1,
      fillParent: true,
      autoScroll: false,
      autoCenter: false,
      dragToSeek: true,
    });
    state.wave.on('ready', () => {
      state.duration = state.wave.getDuration();
      updateTimeLabel();
    });
    state.wave.on('interaction', () => updateTimeLabel());
  }

  async function resolveRun(song, out, solo) {
    const params = new URLSearchParams();
    params.set('song', song);
    if (out) params.set('out', out);
    if (solo) params.set('solo', '1');
    const res = await fetch(`/api/analyze-resolve?${params.toString()}`);
    if (!res.ok) throw new Error('resolve_failed');
    return res.json();
  }

  async function resolveFile(kind, rel) {
    const params = new URLSearchParams();
    if (kind === 'source') params.set('src', rel);
    if (kind === 'import') params.set('imp', rel);
    const res = await fetch(`/api/analyze-resolve-file?${params.toString()}`);
    if (!res.ok) throw new Error('resolve_failed');
    return res.json();
  }

  function applyResolvePayload(payload) {
    state.source = payload.source_rel ? { rel: payload.source_rel, name: payload.source_name || 'Source' } : null;
    state.processed = payload.processed_rel ? { rel: payload.processed_rel, name: payload.processed_name || 'Processed' } : null;
    state.duration = payload.duration_s || payload.metrics?.input?.duration_sec || payload.metrics?.output?.duration_sec || null;
    updateSelectedFromMode();
  }

  async function selectRun(item, node) {
    if (!item?.song) return;
    setActiveItem(aiPairBrowser, node);
    try {
      const payload = await resolveRun(item.song, item.out || '', item.solo);
      applyResolvePayload(payload);
    } catch (_err) {
      if (typeof showToast === 'function') showToast('Failed to load run');
    }
  }

  async function selectAnyFile(item, node) {
    if (!item?.rel) return;
    setActiveItem(aiAnyBrowser, node);
    try {
      const payload = await resolveFile(item.kind, item.rel);
      state.any = { rel: payload.source_rel, name: payload.source_name || item.rel };
      if (state.mode === 'any') {
        state.source = null;
        state.processed = null;
        setSelectedFile(state.any);
      }
    } catch (_err) {
      if (typeof showToast === 'function') showToast('Failed to load file');
    }
  }

  async function uploadAnyFile(file) {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const xhr = new XMLHttpRequest();
    return new Promise((resolve, reject) => {
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
  }

  function updateStrengthFromUI() {
    updateAllStrengths();
    buildAudioGraph();
    updateFilters();
    updateSpectrumOnce();
  }

  async function saveCleanedCopy() {
    if (!state.selected?.rel) return;
    if (aiSaveStatus) aiSaveStatus.textContent = 'Rendering cleaned copy...';
    const settings = {};
    toolDefs.forEach((tool) => {
      settings[tool.id] = { strength: state.strengths[tool.id] || 0 };
    });
    try {
      const res = await fetch('/api/ai-tool/render_combo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: state.selected.rel, settings }),
      });
      if (!res.ok) throw new Error('render_failed');
      const data = await res.json();
      if (aiSaveStatus) aiSaveStatus.textContent = 'Cleaned copy saved.';
      if (aiSaveResult) {
        const sourceRel = state.selected.rel;
        aiSaveResult.innerHTML = `
          <div class="ai-save-name">${data.output_name || 'Output'}</div>
          <div class="ai-save-actions">
            <a class="btn ghost tiny" href="${data.url}" download>Download</a>
            <button class="btn ghost tiny" type="button" data-action="compare">Open in Compare</button>
          </div>
        `;
        const btn = aiSaveResult.querySelector('[data-action="compare"]');
        if (btn) {
          btn.addEventListener('click', () => {
            const url = new URL('/compare', window.location.origin);
            url.searchParams.set('src', sourceRel);
            url.searchParams.set('proc', data.output_rel);
            window.location.assign(`${url.pathname}${url.search}`);
          });
        }
      }
    } catch (_err) {
      if (aiSaveStatus) aiSaveStatus.textContent = 'Save failed.';
      if (typeof showToast === 'function') showToast('Save failed');
    }
  }

  if (aiAudio) {
    aiAudio.addEventListener('timeupdate', updateTimeLabel);
    aiAudio.addEventListener('durationchange', updateTimeLabel);
    aiAudio.addEventListener('play', () => {
      setPlayState(true);
      buildAudioGraph();
      updateFilters();
      startSpectrum();
      updateEngineIndicator();
    });
    aiAudio.addEventListener('pause', () => {
      setPlayState(false);
      stopSpectrum();
      updateSpectrumOnce();
      updateEngineIndicator();
    });
  }

  if (aiPlayBtn) {
    aiPlayBtn.addEventListener('click', () => {
      if (!state.selected || !aiAudio) return;
      buildAudioGraph();
      applyDryIsolation();
      const ctx = ensureAudioContext();
      if (ctx && ctx.state === 'suspended') {
        ctx.resume().catch(() => {});
      }
      if (aiAudio.paused) {
        aiAudio.play().catch(() => {});
        setTimeout(probeDryIsolation, 200);
      } else {
        aiAudio.pause();
      }
    });
  }

  if (aiStopBtn) {
    aiStopBtn.addEventListener('click', () => {
      if (!aiAudio) return;
      aiAudio.pause();
      aiAudio.currentTime = 0;
      if (state.wave) state.wave.setTime(0);
      updateTimeLabel();
    });
  }

  toolRows.forEach((row) => {
    updateSliderRow(row);
    const slider = row.querySelector('[data-role="strength"]');
    if (!slider) return;
    slider.addEventListener('input', () => {
      updateSliderRow(row);
      updateStrengthFromUI();
    });
  });

  if (modeRadios.length) {
    modeRadios.forEach((radio) => {
      radio.addEventListener('change', () => {
        if (radio.checked) setMode(radio.value);
      });
    });
  }

  if (aiPairBrowser) {
    aiPairBrowser.addEventListener('click', (evt) => {
      const itemNode = evt.target.closest('.browser-item');
      if (!itemNode || itemNode.disabled) return;
      const kind = itemNode.dataset.kind || '';
      if (kind !== 'mastering_output') return;
      let meta = {};
      if (itemNode.dataset.meta) {
        try {
          meta = JSON.parse(itemNode.dataset.meta || '{}');
        } catch (_err) {
          meta = {};
        }
      }
      selectRun({ song: meta.song, out: meta.out || '', solo: Boolean(meta.solo) }, itemNode);
    });
  }

  if (aiAnyBrowser) {
    aiAnyBrowser.addEventListener('click', (evt) => {
      const itemNode = evt.target.closest('.browser-item');
      if (!itemNode || itemNode.disabled) return;
      const kind = itemNode.dataset.kind || '';
      if (kind !== 'source' && kind !== 'import') return;
      let meta = {};
      if (itemNode.dataset.meta) {
        try {
          meta = JSON.parse(itemNode.dataset.meta || '{}');
        } catch (_err) {
          meta = {};
        }
      }
      const rel = meta.rel || itemNode.dataset.id || '';
      if (!rel) return;
      selectAnyFile({ kind, rel }, itemNode);
    });
  }

  if (aiAnyUploadBtn && aiAnyUploadInput) {
    aiAnyUploadBtn.addEventListener('click', () => aiAnyUploadInput.click());
    aiAnyUploadInput.addEventListener('change', async () => {
      const file = (aiAnyUploadInput.files || [])[0];
      if (!file) return;
      if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Uploading...';
      try {
        const data = await uploadAnyFile(file);
        if (data.rel) {
          state.any = { rel: `analysis/${data.rel}`, name: data.source_name || file.name };
          setMode('any');
          setSelectedFile(state.any);
        }
        if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Upload complete.';
        if (typeof showToast === 'function') showToast('Upload complete');
      } catch (_err) {
        if (aiAnyUploadStatus) aiAnyUploadStatus.textContent = 'Upload failed.';
        if (typeof showToast === 'function') showToast('Upload failed');
      } finally {
        aiAnyUploadInput.value = '';
      }
    });
  }

  if (aiSaveBtn) {
    aiSaveBtn.addEventListener('click', saveCleanedCopy);
  }

  document.addEventListener('htmx:afterSwap', (evt) => {
    if (aiPairBrowser && aiPairBrowser.contains(evt.target)) {
      updateSelectedFromMode();
    }
    if (aiAnyBrowser && aiAnyBrowser.contains(evt.target)) {
      updateSelectedFromMode();
    }
  });

  const observerTarget = aiSpectrumCanvas?.parentElement;
  if (observerTarget && typeof ResizeObserver !== 'undefined') {
    const resizeObserver = new ResizeObserver(() => {
      if (state.resizing) clearTimeout(state.resizing);
      state.resizing = setTimeout(() => {
        updateSpectrumOnce();
      }, 80);
    });
    resizeObserver.observe(observerTarget);
  }

  initWaveSurfer();
  updateAllStrengths();
  updateFilters();
  updateSpectrumOnce();
  updateSaveState();
  updateTimeLabel();
})();
