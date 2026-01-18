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

  const aiLibraryBrowser = document.getElementById('aiLibraryBrowser');
  const aiLibraryUploadInput = document.getElementById('aiLibraryUploadInput');
  const aiSelectedName = document.getElementById('aiSelectedName');
  const aiSelectedMeta = document.getElementById('aiSelectedMeta');
  const aiWaveform = document.getElementById('aiWaveform');
  const aiLiveScope = document.getElementById('aiLiveScope');
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

  const toolRows = Array.from(document.querySelectorAll('.ai-tool-row'));

  const state = {
    source: null,
    processed: null,
    any: null,
    selected: null,
    selectedSongId: null,
    duration: null,
    wave: null,
    audioCtx: null,
    nodes: null,
    analyser: null,
    scope: {
      node: null,
      floatData: null,
      byteData: null,
      raf: null,
      last: 0,
    },
    spectrum: {
      data: null,
      smooth: null,
      raf: null,
      last: 0,
      flatCount: 0,
    },
    strengths: {},
    resizing: null,
  };
  let libraryBrowser = null;

  function formatTime(raw) {
    const secs = Number(raw);
    if (!Number.isFinite(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
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
        aiEngineIndicator.textContent = 'Audio Engine: ON';
        aiEngineIndicator.classList.add('is-on');
        return;
      }
    }
    aiEngineIndicator.textContent = 'Audio Engine: click Play';
    aiEngineIndicator.classList.remove('is-on');
  }

  function muteDryOutput() {}

  function drawScopeFrame() {
    if (!aiLiveScope || !state.scope.node) return;
    const ctx = aiLiveScope.getContext('2d');
    if (!ctx) return;
    const now = performance.now();
    if (now - state.scope.last < 16) {
      state.scope.raf = requestAnimationFrame(drawScopeFrame);
      return;
    }
    state.scope.last = now;
    const width = aiLiveScope.clientWidth;
    const height = aiLiveScope.clientHeight;
    if (!width || !height) {
      state.scope.raf = requestAnimationFrame(drawScopeFrame);
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    if (aiLiveScope.width !== Math.round(width * dpr) || aiLiveScope.height !== Math.round(height * dpr)) {
      aiLiveScope.width = Math.round(width * dpr);
      aiLiveScope.height = Math.round(height * dpr);
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
    if (aiAudio && !aiAudio.paused) {
      const flat = useFloat ? data.every((v) => Math.abs(v) < 0.0001) : data.every((v) => v === 128);
      if (flat) {
        state.scope.flatCount = (state.scope.flatCount || 0) + 1;
        if (state.scope.flatCount === 12) {
          console.warn('[ai_toolkit] analyser flatline (scope)', {
            ctx: state.audioCtx?.state,
            muted: aiAudio.muted,
            readyState: aiAudio.readyState,
          });
        }
      } else {
        state.scope.flatCount = 0;
      }
    }

    const rootStyle = getComputedStyle(document.documentElement);
    const accent = rootStyle.getPropertyValue('--accent').trim() || '#ff8a3d';
    ctx.strokeStyle = accent;
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = aiAudio && !aiAudio.paused ? 0.85 : 0.5;
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

    ctx.globalAlpha = aiAudio && !aiAudio.paused ? 0.12 : 0.08;
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

  function updateSelectedSummary(selected) {
    if (!aiSelectedName || !aiSelectedMeta) return;
    if (!selected) {
      aiSelectedName.textContent = '-';
      aiSelectedMeta.textContent = 'No file selected.';
      setClipRisk(false);
      return;
    }
    aiSelectedName.textContent = selected.name || selected.title || selected.rel || 'Selected';
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
    if (aiAudio) aiAudio.muted = false;
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

    const scope = ctx.createAnalyser();
    scope.fftSize = 2048;
    scope.smoothingTimeConstant = 0.85;

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
      .connect(scope)
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
      scope,
    };
    state.analyser = analyser;
    state.scope.node = scope;
    state.scope.floatData = new Float32Array(scope.fftSize);
    state.scope.byteData = new Uint8Array(scope.fftSize);
    if (aiAudio) aiAudio.muted = false;
    updateEngineIndicator();
    if (ctx) {
      ctx.onstatechange = () => updateEngineIndicator();
    }
    resetSpectrumBuffers();
  }

  function syncWaveToAudio() {
    if (!state.wave || !aiAudio) return;
    const wave = state.wave;
    const renderer = typeof wave.getRenderer === 'function' ? wave.getRenderer() : wave.renderer;
    if (!renderer || typeof renderer.renderProgress !== 'function') return;
    const durRaw = aiAudio.duration || wave.getDuration() || 0;
    const duration = Number.isFinite(durRaw) && durRaw > 0 ? durRaw : 0.001;
    const progress = Math.max(0, Math.min(1, aiAudio.currentTime / duration));
    renderer.renderProgress(progress, false);
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
      if (aiAudio && !aiAudio.paused) {
        const flat = state.spectrum.data.every((v) => v === 0);
        if (flat) {
          state.spectrum.flatCount = (state.spectrum.flatCount || 0) + 1;
          if (state.spectrum.flatCount === 12) {
            console.warn('[ai_toolkit] analyser flatline (spectrum)', {
              ctx: state.audioCtx?.state,
              muted: aiAudio.muted,
              readyState: aiAudio.readyState,
            });
          }
        } else {
          state.spectrum.flatCount = 0;
        }
      }
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
    state.selectedSongId = selected?.song_id || null;
    updateSelectedSummary(selected);
    updateSaveState();
    if (!aiAudio || !selected?.rel) {
      if (aiAudio) aiAudio.removeAttribute('src');
      if (state.wave && typeof state.wave.empty === 'function') state.wave.empty();
      stopSpectrum();
      return;
    }
    aiAudio.pause();
    aiAudio.currentTime = 0;
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
      normalize: false,
      backend: 'MediaElement',
      mediaControls: false,
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
      syncWaveToAudio();
    });
    state.wave.on('interaction', (time) => {
      const durRaw = aiAudio?.duration || state.wave?.getDuration?.() || 0;
      const duration = Number.isFinite(durRaw) && durRaw > 0 ? durRaw : 0;
      if (!duration || !aiAudio || !Number.isFinite(time)) return;
      aiAudio.currentTime = Math.max(0, Math.min(time, duration));
      updateTimeLabel();
    });
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
        body: JSON.stringify({
          path: state.selected.rel,
          song_id: state.selectedSongId,
          settings,
        }),
      });
      if (!res.ok) throw new Error('render_failed');
      const data = await res.json();
      if (state.selectedSongId && data.output_rel) {
        const activeTools = toolDefs
          .filter((tool) => (state.strengths[tool.id] || 0) > 0)
          .map((tool) => tool.title);
        const rel = data.output_rel;
        const format = (rel.split('.').pop() || '').toLowerCase();
        await fetch('/api/library/add_version', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            song_id: state.selectedSongId,
            kind: 'aitk',
            label: 'AI Toolkit',
            title: 'AI Toolkit',
            renditions: [{ format, rel }],
            summary: { aitk: 'AI Toolkit' },
            tags: activeTools,
            version_id: data.version_id,
          }),
        });
        if (libraryBrowser) libraryBrowser.reload();
      }
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
    aiAudio.addEventListener('timeupdate', () => {
      updateTimeLabel();
      syncWaveToAudio();
    });
    aiAudio.addEventListener('durationchange', updateTimeLabel);
    aiAudio.addEventListener('error', () => {
      console.warn('AI Toolkit audio error', aiAudio.error);
    });
    aiAudio.addEventListener('stalled', () => {
      console.warn('AI Toolkit audio stalled');
    });
    aiAudio.addEventListener('waiting', () => {
      console.warn('AI Toolkit audio waiting');
    });
    aiAudio.addEventListener('play', () => {
      setPlayState(true);
      buildAudioGraph();
      updateFilters();
      startSpectrum();
      startScope();
      updateEngineIndicator();
    });
    aiAudio.addEventListener('pause', () => {
      setPlayState(false);
      stopSpectrum();
      updateSpectrumOnce();
      stopScope();
      updateScopeOnce();
      updateEngineIndicator();
    });
  }

  if (aiPlayBtn) {
    aiPlayBtn.addEventListener('click', () => {
      if (!state.selected || !aiAudio) return;
      buildAudioGraph();
      const ctx = ensureAudioContext();
      const resume = ctx && ctx.state === 'suspended' ? ctx.resume().catch(() => {}) : Promise.resolve();
      resume.finally(() => {
        console.debug('[ai_toolkit] play', {
          ctx: ctx ? ctx.state : 'none',
          muted: aiAudio.muted,
          paused: aiAudio.paused,
          time: aiAudio.currentTime,
        });
        if (aiAudio.paused) {
          aiAudio.play().catch(() => {});
        } else {
          aiAudio.pause();
        }
      });
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

  if (aiSaveBtn) {
    aiSaveBtn.addEventListener('click', saveCleanedCopy);
  }

  if (aiLibraryBrowser && window.LibraryBrowser) {
    const browser = window.LibraryBrowser.init(aiLibraryBrowser, { module: 'ai' });
    libraryBrowser = browser;
    aiLibraryBrowser.addEventListener('library:select', (evt) => {
      const { song, track } = evt.detail || {};
      if (!track?.rel) return;
      setSelectedFile({
        rel: track.rel,
        name: track.title || track.label || song?.title || track.rel,
        song_id: song?.song_id || null,
      });
    });
    aiLibraryBrowser.addEventListener('library:action', async (evt) => {
      const { action, song, version, item } = evt.detail || {};
      if (action === 'import-file' && aiLibraryUploadInput) {
        aiLibraryUploadInput.click();
        return;
      }
      if (action === 'rename-song' && song?.song_id) {
        const next = prompt('Rename song', song.title || '');
        if (!next) return;
        await fetch('/api/library/rename_song', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: song.song_id, title: next }),
        });
        browser.reload();
        return;
      }
      if (action === 'delete-version' && song?.song_id && version?.version_id) {
        await fetch('/api/library/delete_version', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ song_id: song.song_id, version_id: version.version_id }),
        });
        browser.reload();
        return;
      }
      if (action === 'open-compare') {
        const rel = primaryRendition(version?.renditions)?.rel || version?.rel;
        if (!rel) return;
        const url = new URL('/compare', window.location.origin);
        if (song?.source?.rel) url.searchParams.set('src', song.source.rel);
        url.searchParams.set('proc', rel);
        window.location.assign(`${url.pathname}${url.search}`);
        return;
      }
      if (action === 'add-unsorted' && item?.rel) {
        const res = await fetch('/api/library', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const songs = Array.isArray(data.songs) ? data.songs : [];
        const list = songs.map((song, idx) => `${idx + 1}) ${song.title || 'Untitled'}`).join('\n');
        const choice = prompt(`Add output to which song?\n${list}\n\nEnter number or type a new title.`, '');
        if (!choice) return;
        const idx = parseInt(choice, 10);
        if (!Number.isNaN(idx) && songs[idx - 1]) {
          const song = songs[idx - 1];
          const rel = item.rel;
          const format = (rel.split('.').pop() || '').toLowerCase();
          await fetch('/api/library/add_version', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              song_id: song.song_id,
              kind: 'manual',
              label: item.name || 'Output',
              title: item.name || 'Output',
              renditions: [{ format, rel }],
              summary: {},
              tags: [],
            }),
          });
        } else {
          const title = choice.trim() || item.name || item.rel;
          await fetch('/api/library/import_source', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: item.rel, title }),
          });
        }
        browser.reload();
      }
    });
    if (aiLibraryUploadInput) {
      aiLibraryUploadInput.addEventListener('change', async () => {
        const file = (aiLibraryUploadInput.files || [])[0];
        if (!file) return;
        try {
          const data = await uploadAnyFile(file);
          if (data.rel) {
            setSelectedFile({
              rel: data.rel,
              name: data.source_name || file.name,
              song_id: data.song?.song_id || null,
            });
            browser.reload();
          }
          if (typeof showToast === 'function') showToast('Upload complete');
        } catch (_err) {
          if (typeof showToast === 'function') showToast('Upload failed');
        } finally {
          aiLibraryUploadInput.value = '';
        }
      });
    }
  }

  const observerTarget = aiSpectrumCanvas?.parentElement;
  if (observerTarget && typeof ResizeObserver !== 'undefined') {
    const resizeObserver = new ResizeObserver(() => {
      if (state.resizing) clearTimeout(state.resizing);
      state.resizing = setTimeout(() => {
        updateSpectrumOnce();
        updateScopeOnce();
      }, 80);
    });
    resizeObserver.observe(observerTarget);
  }

  initWaveSurfer();
  updateAllStrengths();
  updateFilters();
  updateSpectrumOnce();
  updateScopeOnce();
  updateSaveState();
  updateTimeLabel();
})();
