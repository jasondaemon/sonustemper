(() => {
  const toolDefs = [
    {
      id: 'ai_deglass',
      title: 'Reduce AI Hiss / Glass',
      subtitle: 'Soften brittle top-end and reduce hiss.',
      fallback: 0,
    },
    {
      id: 'ai_vocal_smooth',
      title: 'Smooth Harsh Vocals',
      subtitle: 'Tame harsh presence and sibilant edges.',
      fallback: 0,
    },
    {
      id: 'ai_bass_tight',
      title: 'Tighten Bass / Remove Rumble',
      subtitle: 'Clean sub rumble and low-mid mud.',
      fallback: 0,
    },
    {
      id: 'ai_transient_soften',
      title: 'Reduce Pumping / Over-Transients',
      subtitle: 'Soften clicky transients and harsh bite.',
      fallback: 0,
    },
    {
      id: 'ai_platform_safe',
      title: 'Platform Ready (AI Safe Loudness)',
      subtitle: 'Safer loudness for streaming without harsh clipping.',
      fallback: -14,
    },
  ];
  const reverbDefaults = {
    side_reduction: { enabled: false, value: 0 },
    mid_suppress: { enabled: false, value: 0, freq: 1800 },
    tail_gate: { enabled: false, threshold: -38, ratio: 2.5 },
    low_cut: { enabled: false, value: 120 },
    high_cut: { enabled: false, value: 9500 },
  };

  const aiLibraryBrowser = document.getElementById('aiLibraryBrowser');
  const aiLibraryUploadInput = document.getElementById('aiLibraryUploadInput');
  const aiSelectedName = document.getElementById('aiSelectedName');
  const aiSelectedMeta = document.getElementById('aiSelectedMeta');
  const aiStatusList = document.getElementById('aiStatusList');
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
  const aiRecoPanel = document.getElementById('aiRecommendations');
  const aiRecoList = document.getElementById('aiRecoList');
  const aiRecoEmpty = document.getElementById('aiRecoEmpty');
  const aiRecoApplyAll = document.getElementById('aiRecoApplyAll');

  const toolRows = Array.from(document.querySelectorAll('.ai-tool-row[data-tool-id]'));
  const reverbRows = Array.from(document.querySelectorAll('.ai-reverb-row'));

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
    tools: {},
    reverb: JSON.parse(JSON.stringify(reverbDefaults)),
    recommendations: [],
    resizing: null,
    recoReqId: 0,
  };
  let libraryBrowser = null;
  let statusLines = [];
  let statusRenderPending = false;

  function formatTime(raw) {
    const secs = Number(raw);
    if (!Number.isFinite(secs)) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  function normalizeRelPath(rel) {
    if (!rel) return '';
    const trimmed = String(rel).trim();
    if (!trimmed) return '';
    if (trimmed.includes('/api/analyze/path')) {
      try {
        const url = new URL(trimmed, window.location.origin);
        const param = url.searchParams.get('path');
        if (param) return param;
      } catch (_err) {
        // ignore
      }
    }
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
      try {
        const url = new URL(trimmed);
        const param = url.searchParams.get('path');
        if (param) return param;
      } catch (_err) {
        // ignore
      }
    }
    return trimmed.replace(/^\/+/, '');
  }

  function setPlayState(playing) {
    if (!aiPlayBtn) return;
    aiPlayBtn.textContent = playing ? 'Pause' : 'Play';
  }

  function resetPlaybackState() {
    try {
      if (aiAudio) {
        aiAudio.pause();
        aiAudio.currentTime = 0;
      }
    } catch (_) {}
    setPlayState(false);
    if (state.wave) {
      try {
        state.wave.setTime(0);
      } catch (_) {}
    }
    updateTimeLabel();
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

  function renderStatus() {
    if (statusRenderPending) return;
    statusRenderPending = true;
    requestAnimationFrame(() => {
      statusRenderPending = false;
      if (!aiStatusList) return;
      aiStatusList.textContent = statusLines.length ? statusLines.join('\n') : '(waiting)';
      aiStatusList.scrollTop = aiStatusList.scrollHeight;
    });
  }

  function addStatusLine(message) {
    if (!message) return;
    const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    statusLines.push(`${ts} ${message}`);
    if (statusLines.length > 200) statusLines = statusLines.slice(-200);
    renderStatus();
  }

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
    const reverbSplit = ctx.createChannelSplitter(2);
    const reverbMidL = ctx.createGain();
    const reverbMidR = ctx.createGain();
    const reverbSideL = ctx.createGain();
    const reverbSideR = ctx.createGain();
    const reverbMidSum = ctx.createGain();
    const reverbSideSum = ctx.createGain();
    const reverbSideReduce = ctx.createGain();
    const reverbMidEq = ctx.createBiquadFilter();
    const reverbGate = ctx.createDynamicsCompressor();
    const reverbHp = ctx.createBiquadFilter();
    const reverbLp = ctx.createBiquadFilter();
    const reverbMixL = ctx.createGain();
    const reverbMixR = ctx.createGain();
    const reverbSideToL = ctx.createGain();
    const reverbSideToR = ctx.createGain();
    const reverbMerge = ctx.createChannelMerger(2);

    reverbMidL.gain.value = 0.5;
    reverbMidR.gain.value = 0.5;
    reverbSideL.gain.value = 0.5;
    reverbSideR.gain.value = -0.5;
    reverbSideReduce.gain.value = 1;
    reverbMidEq.type = 'peaking';
    reverbMidEq.frequency.value = 1800;
    reverbMidEq.Q.value = 0.8;
    reverbMidEq.gain.value = 0;
    reverbGate.threshold.value = 0;
    reverbGate.ratio.value = 1;
    reverbGate.attack.value = 0.005;
    reverbGate.release.value = 0.2;
    reverbHp.type = 'highpass';
    reverbHp.frequency.value = 20;
    reverbLp.type = 'lowpass';
    reverbLp.frequency.value = 20000;
    reverbMixL.gain.value = 1;
    reverbMixR.gain.value = 1;
    reverbSideToL.gain.value = 1;
    reverbSideToR.gain.value = -1;
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
      .connect(reverbSplit);
    reverbSplit.connect(reverbMidL, 0);
    reverbSplit.connect(reverbMidR, 1);
    reverbMidL.connect(reverbMidSum);
    reverbMidR.connect(reverbMidSum);
    reverbSplit.connect(reverbSideL, 0);
    reverbSplit.connect(reverbSideR, 1);
    reverbSideL.connect(reverbSideSum);
    reverbSideR.connect(reverbSideSum);
    reverbSideSum.connect(reverbSideReduce);
    reverbMidSum.connect(reverbMidEq);
    reverbMidEq.connect(reverbMixL);
    reverbMidEq.connect(reverbMixR);
    reverbSideReduce.connect(reverbSideToL);
    reverbSideReduce.connect(reverbSideToR);
    reverbSideToL.connect(reverbMixL);
    reverbSideToR.connect(reverbMixR);
    reverbMixL.connect(reverbMerge, 0, 0);
    reverbMixR.connect(reverbMerge, 0, 1);
    reverbMerge
      .connect(reverbGate)
      .connect(reverbHp)
      .connect(reverbLp)
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
      reverbSplit,
      reverbMidL,
      reverbMidR,
      reverbSideL,
      reverbSideR,
      reverbMidSum,
      reverbSideSum,
      reverbSideReduce,
      reverbMidEq,
      reverbGate,
      reverbHp,
      reverbLp,
      reverbMixL,
      reverbMixR,
      reverbSideToL,
      reverbSideToR,
      reverbMerge,
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
    const deglass = ensureToolState('ai_deglass');
    const vocal = ensureToolState('ai_vocal_smooth');
    const bass = ensureToolState('ai_bass_tight');
    const trans = ensureToolState('ai_transient_soften');
    const platform = ensureToolState('ai_platform_safe');
    const reverb = ensureReverbState();

    if (deglass.enabled) {
      setParam(state.nodes.deglassShelf.gain, deglass.value);
      if (deglass.value <= -2) {
        const lp = 20000 - (Math.min(6, Math.abs(deglass.value)) - 2) / 4 * 6000;
        setParam(state.nodes.deglassLp.frequency, Math.max(14000, lp));
      } else {
        setParam(state.nodes.deglassLp.frequency, 20000);
      }
    } else {
      setParam(state.nodes.deglassShelf.gain, 0);
      setParam(state.nodes.deglassLp.frequency, 20000);
    }

    if (vocal.enabled) {
      setParam(state.nodes.vocalSmooth.gain, vocal.value);
      if (vocal.value <= -3) {
        const extra = Math.min(2, Math.abs(vocal.value) - 3);
        setParam(state.nodes.sibilant.gain, -0.5 - extra);
      } else {
        setParam(state.nodes.sibilant.gain, 0);
      }
    } else {
      setParam(state.nodes.vocalSmooth.gain, 0);
      setParam(state.nodes.sibilant.gain, 0);
    }

    if (bass.enabled) {
      if (bass.value < 0) {
        const hp = 30 + (Math.min(6, Math.abs(bass.value)) / 6) * 40;
        setParam(state.nodes.bassHp.frequency, hp);
        setParam(state.nodes.bassMud.gain, -3 * (Math.abs(bass.value) / 6));
      } else {
        const hp = 25 + (bass.value / 6) * 10;
        setParam(state.nodes.bassHp.frequency, hp);
        setParam(state.nodes.bassMud.gain, 0);
      }
    } else {
      setParam(state.nodes.bassHp.frequency, 25);
      setParam(state.nodes.bassMud.gain, 0);
    }

    if (trans.enabled) {
      if (trans.value < 0) {
        const depth = Math.min(6, Math.abs(trans.value));
        setParam(state.nodes.transientComp.threshold, -18 - depth * 2);
        setParam(state.nodes.transientComp.ratio, 2 + depth * 0.4);
      } else {
        const lift = Math.min(4, trans.value);
        setParam(state.nodes.transientComp.threshold, -10 + lift * 1.5);
        setParam(state.nodes.transientComp.ratio, 1.2 + lift * 0.2);
      }
    } else {
      setParam(state.nodes.transientComp.threshold, -10);
      setParam(state.nodes.transientComp.ratio, 1.2);
    }

    if (platform.enabled) {
      const delta = platform.value - (-14);
      const gainDb = Math.max(-6, Math.min(6, delta));
      const gain = Math.pow(10, gainDb / 20);
      setParam(state.nodes.outputGain.gain, gain);
      const intensity = Math.max(0, Math.min(8, (platform.value + 14)));
      setParam(state.nodes.platformComp.threshold, -14 + intensity * 1.2);
      setParam(state.nodes.platformComp.ratio, 2 + intensity * 0.2);
    } else {
      setParam(state.nodes.outputGain.gain, 1);
      setParam(state.nodes.platformComp.threshold, -14);
      setParam(state.nodes.platformComp.ratio, 2);
    }

    if (reverb.side_reduction.enabled) {
      const pct = Math.max(0, Math.min(40, reverb.side_reduction.value));
      setParam(state.nodes.reverbSideReduce.gain, 1 - pct / 100);
    } else {
      setParam(state.nodes.reverbSideReduce.gain, 1);
    }
    if (reverb.mid_suppress.enabled) {
      setParam(state.nodes.reverbMidEq.frequency, reverb.mid_suppress.freq || 1800);
      setParam(state.nodes.reverbMidEq.gain, reverb.mid_suppress.value);
    } else {
      setParam(state.nodes.reverbMidEq.gain, 0);
    }
    if (reverb.tail_gate.enabled) {
      setParam(state.nodes.reverbGate.threshold, reverb.tail_gate.threshold);
      setParam(state.nodes.reverbGate.ratio, reverb.tail_gate.ratio);
    } else {
      setParam(state.nodes.reverbGate.threshold, 0);
      setParam(state.nodes.reverbGate.ratio, 1);
    }
    if (reverb.low_cut.enabled) {
      setParam(state.nodes.reverbHp.frequency, reverb.low_cut.value);
    } else {
      setParam(state.nodes.reverbHp.frequency, 20);
    }
    if (reverb.high_cut.enabled) {
      setParam(state.nodes.reverbLp.frequency, reverb.high_cut.value);
    } else {
      setParam(state.nodes.reverbLp.frequency, 20000);
    }
    if (aiEngineDebug) {
      const deglassMsg = deglass.enabled ? `${deglass.value.toFixed(1)} dB` : 'off';
      const platformMsg = platform.enabled ? `${platform.value.toFixed(1)} LUFS` : 'off';
      aiEngineDebug.textContent = `Deglass: ${deglassMsg} | Loudness target: ${platformMsg}`;
    }
  }

  function computeEffectCurve(width, height) {
    const points = [];
    const n = 96;
    const minF = 20;
    const maxF = 20000;
    const logMin = Math.log10(minF);
    const logMax = Math.log10(maxF);
    const deglass = ensureToolState('ai_deglass');
    const vocal = ensureToolState('ai_vocal_smooth');
    const bass = ensureToolState('ai_bass_tight');
    const trans = ensureToolState('ai_transient_soften');
    const platform = ensureToolState('ai_platform_safe');
    const range = 6;

    const gauss = (x, sigma) => Math.exp(-(x * x) / (2 * sigma * sigma));

    for (let i = 0; i < n; i += 1) {
      const t = i / (n - 1);
      const f = Math.pow(10, logMin + (logMax - logMin) * t);
      let db = 0;

      if (bass.enabled) {
        if (bass.value < 0) {
          const hp = 30 + (Math.min(6, Math.abs(bass.value)) / 6) * 40;
          if (f < hp) {
            db += -3 * (1 - f / hp) * (Math.abs(bass.value) / 6);
          }
          db += -3 * (Math.abs(bass.value) / 6) * gauss(Math.log2(f / 220), 0.6);
        } else if (bass.value > 0) {
          db += 2 * (bass.value / 6) * gauss(Math.log2(f / 90), 0.5);
        }
      }

      if (vocal.enabled) {
        db += vocal.value * gauss(Math.log2(f / 4500), 0.5);
        if (vocal.value <= -3) {
          db += -(Math.abs(vocal.value) / 6) * gauss(Math.log2(f / 7500), 0.45);
        }
      }

      if (deglass.enabled) {
        const shelf = 1 / (1 + Math.exp(-5 * Math.log2(f / 11000)));
        db += deglass.value * shelf;
        if (deglass.value <= -2 && f > 15000) {
          db += -2 * (Math.abs(deglass.value) / 6);
        }
      }

      if (trans.enabled) {
        if (trans.value < 0) {
          db += -2 * (Math.abs(trans.value) / 6) * gauss(Math.log2(f / 3200), 0.5);
          db += -1.5 * (Math.abs(trans.value) / 6) * gauss(Math.log2(f / 8000), 0.6);
        } else if (trans.value > 0) {
          db += 1.5 * (trans.value / 4) * gauss(Math.log2(f / 3200), 0.5);
        }
      }

      if (platform.enabled) {
        const delta = platform.value - (-14);
        db += -(delta / 6);
      }

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

  function formatDb(value) {
    if (!Number.isFinite(value)) return '0.0 dB';
    return `${value.toFixed(1)} dB`;
  }

  function formatLufs(value) {
    if (!Number.isFinite(value)) return '-14.0 LUFS';
    return `${value.toFixed(1)} LUFS`;
  }
  function formatPercent(value) {
    if (!Number.isFinite(value)) return '0%';
    return `${Math.round(value)}%`;
  }
  function formatHz(value) {
    if (!Number.isFinite(value)) return '0 Hz';
    return `${Math.round(value)} Hz`;
  }
  function formatRatio(value) {
    if (!Number.isFinite(value)) return '1.0:1';
    return `${value.toFixed(1)}:1`;
  }

  function formatValue(toolId, value) {
    if (toolId === 'ai_platform_safe') return formatLufs(value);
    return formatDb(value);
  }

  function ensureToolState(toolId) {
    if (!state.tools[toolId]) {
      const def = toolDefs.find((tool) => tool.id === toolId);
      state.tools[toolId] = {
        enabled: false,
        value: def ? def.fallback : 0,
      };
    }
    return state.tools[toolId];
  }

  function ensureReverbState() {
    if (!state.reverb) {
      state.reverb = JSON.parse(JSON.stringify(reverbDefaults));
    }
    return state.reverb;
  }

  function resetReverbState() {
    state.reverb = JSON.parse(JSON.stringify(reverbDefaults));
  }

  function updateReverbRow(row) {
    const reverbId = row.dataset.reverbId;
    const check = row.querySelector('[data-role="enabled"]');
    if (!check) return;
    const r = ensureReverbState();
    const entry = r[reverbId];
    if (!entry) return;
    entry.enabled = Boolean(check.checked);
    if (reverbId === 'tail_gate') {
      const thresholdInput = row.querySelector('[data-role="threshold"]');
      const ratioInput = row.querySelector('[data-role="ratio"]');
      const thresholdPill = row.querySelector('[data-role="threshold-pill"]');
      const ratioPill = row.querySelector('[data-role="ratio-pill"]');
      if (thresholdInput) entry.threshold = parseFloat(thresholdInput.value);
      if (ratioInput) entry.ratio = parseFloat(ratioInput.value);
      if (thresholdPill) thresholdPill.textContent = `${Math.round(entry.threshold)} dB`;
      if (ratioPill) ratioPill.textContent = formatRatio(entry.ratio);
      if (thresholdInput) thresholdInput.disabled = !entry.enabled;
      if (ratioInput) ratioInput.disabled = !entry.enabled;
      return;
    }
    const slider = row.querySelector('[data-role="value"]');
    const label = row.querySelector('[data-role="value-pill"]');
    if (slider) entry.value = parseFloat(slider.value);
    if (label) {
      if (reverbId === 'side_reduction') label.textContent = formatPercent(entry.value);
      else if (reverbId === 'low_cut' || reverbId === 'high_cut') label.textContent = formatHz(entry.value);
      else label.textContent = formatDb(entry.value);
    }
    if (slider) slider.disabled = !entry.enabled;
  }

  function updateAllReverb() {
    reverbRows.forEach((row) => updateReverbRow(row));
  }

  function applyReverbSettings(settings) {
    const r = ensureReverbState();
    const next = settings || {};
    if (typeof next.side_reduction_pct === 'number') {
      r.side_reduction.value = next.side_reduction_pct;
      r.side_reduction.enabled = Boolean(next.enable_side_reduction);
    }
    if (typeof next.mid_suppress_db === 'number') {
      r.mid_suppress.value = next.mid_suppress_db;
      r.mid_suppress.enabled = Boolean(next.enable_mid_suppress);
    }
    if (typeof next.mid_freq_hz === 'number') {
      r.mid_suppress.freq = next.mid_freq_hz;
    }
    if (typeof next.gate_threshold_db === 'number') {
      r.tail_gate.threshold = next.gate_threshold_db;
      r.tail_gate.enabled = Boolean(next.enable_tail_gate);
    }
    if (typeof next.gate_ratio === 'number') {
      r.tail_gate.ratio = next.gate_ratio;
    }
    if (typeof next.low_cut_hz === 'number') {
      r.low_cut.value = next.low_cut_hz;
      r.low_cut.enabled = Boolean(next.enable_low_cut);
    }
    if (typeof next.high_cut_hz === 'number') {
      r.high_cut.value = next.high_cut_hz;
      r.high_cut.enabled = Boolean(next.enable_high_cut);
    }
    reverbRows.forEach((row) => {
      const id = row.dataset.reverbId;
      const entry = r[id];
      if (!entry) return;
      const check = row.querySelector('[data-role="enabled"]');
      if (check) check.checked = entry.enabled;
      if (id === 'tail_gate') {
        const thresholdInput = row.querySelector('[data-role="threshold"]');
        const ratioInput = row.querySelector('[data-role="ratio"]');
        if (thresholdInput) thresholdInput.value = String(entry.threshold);
        if (ratioInput) ratioInput.value = String(entry.ratio);
      } else {
        const slider = row.querySelector('[data-role="value"]');
        if (slider) slider.value = String(entry.value);
      }
    });
    updateAllReverb();
  }

  function updateToolRow(row) {
    const toolId = row.dataset.toolId;
    const slider = row.querySelector('[data-role="value"]');
    const label = row.querySelector('[data-role="value-pill"]');
    const enabled = row.querySelector('[data-role="enabled"]');
    if (!slider || !label || !enabled) return;
    const tool = ensureToolState(toolId);
    tool.enabled = Boolean(enabled.checked);
    tool.value = parseFloat(slider.value);
    label.textContent = formatValue(toolId, tool.value);
    slider.disabled = !tool.enabled;
  }

  function updateAllTools() {
    toolRows.forEach((row) => updateToolRow(row));
  }

  function setToolValue(toolId, value, enabled) {
    const row = toolRows.find((r) => r.dataset.toolId === toolId);
    if (!row) return;
    const slider = row.querySelector('[data-role="value"]');
    const label = row.querySelector('[data-role="value-pill"]');
    const check = row.querySelector('[data-role="enabled"]');
    if (slider) slider.value = String(value);
    if (check && typeof enabled === 'boolean') check.checked = enabled;
    if (label) label.textContent = formatValue(toolId, parseFloat(slider?.value || '0'));
    updateToolRow(row);
  }

  function updateSpectrumOnce() {
    stopSpectrum();
    state.spectrum.last = 0;
    drawSpectrumFrame();
  }

  function renderRecommendations(items) {
    if (!aiRecoPanel || !aiRecoList || !aiRecoEmpty || !aiRecoApplyAll) return;
    state.recommendations = Array.isArray(items) ? items : [];
    aiRecoList.innerHTML = '';
    if (!state.recommendations.length) {
      aiRecoEmpty.hidden = false;
      aiRecoApplyAll.disabled = true;
      return;
    }
    aiRecoEmpty.hidden = true;
    aiRecoApplyAll.disabled = false;
    state.recommendations.forEach((item) => {
      const tool = toolDefs.find((t) => t.id === item.toolId);
      const title = item.title || tool?.title || item.toolId;
      let suggestionText = 'Suggested: custom';
      if (typeof item.value === 'number') {
        suggestionText = `Suggested: ${formatValue(item.toolId, item.value)}`;
      } else if (item.settings) {
        const pct = item.settings.side_reduction_pct;
        const db = item.settings.mid_suppress_db;
        const low = item.settings.low_cut_hz;
        const high = item.settings.high_cut_hz;
        const parts = [];
        if (typeof pct === 'number') parts.push(`${Math.round(pct)}% side`);
        if (typeof db === 'number') parts.push(`${db.toFixed(1)} dB`);
        if (typeof low === 'number') parts.push(`${Math.round(low)} Hz`);
        if (typeof high === 'number') parts.push(`${Math.round(high)} Hz`);
        if (parts.length) suggestionText = `Suggested: ${parts.join(' · ')}`;
      }
      const row = document.createElement('div');
      row.className = 'ai-reco-item';
      row.innerHTML = `
        <div class="ai-reco-meta">
          <div class="ai-reco-summary">${item.summary || title}</div>
          <div class="ai-reco-sub">Severity ${(item.severity || 0).toFixed(2)} · ${String(item.confidence || 'low')}</div>
          <div class="ai-reco-sub">${suggestionText}</div>
        </div>
        <div class="ai-reco-actions">
          <button type="button" class="btn ghost tiny" data-action="apply">Apply</button>
        </div>
      `;
      row.querySelector('[data-action="apply"]')?.addEventListener('click', () => {
        if (typeof item.apply === 'function') {
          item.apply();
          updateFilters();
          updateSpectrumOnce();
          return;
        }
        setToolValue(item.toolId, item.value, true);
        updateAllTools();
        updateFilters();
        updateSpectrumOnce();
      });
      aiRecoList.appendChild(row);
    });
  }

  function applyDetectorDefaults(rel) {
    const defaults = {};
    toolDefs.forEach((tool) => {
      defaults[tool.id] = tool.fallback;
      ensureToolState(tool.id);
    });

    const applyDefaults = () => {
      Object.entries(defaults).forEach(([id, value]) => setToolValue(id, value, false));
      applyReverbSettings({
        enable_side_reduction: false,
        side_reduction_pct: reverbDefaults.side_reduction.value,
        enable_mid_suppress: false,
        mid_suppress_db: reverbDefaults.mid_suppress.value,
        enable_tail_gate: false,
        gate_threshold_db: reverbDefaults.tail_gate.threshold,
        gate_ratio: reverbDefaults.tail_gate.ratio,
        enable_low_cut: false,
        low_cut_hz: reverbDefaults.low_cut.value,
        enable_high_cut: false,
        high_cut_hz: reverbDefaults.high_cut.value,
      });
      updateAllTools();
      updateAllReverb();
      updateFilters();
      updateSpectrumOnce();
      renderRecommendations([]);
    };

    const detectRel = normalizeRelPath(rel);
    if (!detectRel) {
      setClipRisk(false);
      applyDefaults();
      return;
    }
    if (aiRecoEmpty) {
      aiRecoEmpty.textContent = 'Analyzing…';
      aiRecoEmpty.hidden = false;
    }
    const reqId = ++state.recoReqId;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);
    addStatusLine(`Recommendations: analyzing ${detectRel}`);
    console.debug('[ai_toolkit] detect start', { rel: detectRel, reqId });

    const detectUrl = `/api/ai-tool/detect?path=${encodeURIComponent(detectRel)}&mode=fast`;
    fetch(detectUrl, {
      cache: 'no-store',
      signal: controller.signal,
    })
      .then((res) => {
        if (!res.ok) {
          return res.text().then((text) => {
            const detail = (text || '').trim().slice(0, 200);
            throw new Error(`detect_failed:${res.status}${detail ? `:${detail}` : ''}`);
          });
        }
        console.debug('[ai_toolkit] detect ok', { rel: detectRel, reqId });
        return res.json();
      })
      .then((data) => {
        if (reqId !== state.recoReqId) return;
        console.debug('detect response', data);
        try {
          const findings = Array.isArray(data.findings) ? data.findings : [];
          const suggestions = findings.slice(0, 3).map((finding) => {
            const toolId = finding.suggested_tool_id;
            const severity = Number(finding.severity || 0);
            if (toolId === 'ai_reverb_reduce') {
              return {
                toolId,
                severity,
                confidence: finding.confidence || 'low',
                summary: finding.summary || '',
                title: finding.title || 'Reverb Reduction (Perceptual)',
                settings: finding.recommended_settings || {},
                apply: () => {
                  applyReverbSettings(finding.recommended_settings || {});
                },
              };
            }
            let value = 0;
            if (toolId === 'ai_deglass') value = -1.5 - severity * 2.5;
            if (toolId === 'ai_vocal_smooth') value = -1 - severity * 2.5;
            if (toolId === 'ai_bass_tight') value = -1 - severity * 3;
            if (toolId === 'ai_transient_soften') value = -0.5 - severity * 2.5;
            if (toolId === 'ai_platform_safe') value = severity > 0.6 ? -16 : -14;
            return {
              toolId,
              severity,
              confidence: finding.confidence || 'low',
              summary: finding.summary || '',
              value,
            };
          }).filter(item => item.toolId);
          setClipRisk(hasClipRisk(data?.metrics?.fullband));
          applyDefaults();
          renderRecommendations(suggestions);
          if (aiRecoEmpty) {
            aiRecoEmpty.textContent = suggestions.length ? '' : 'No recommendations for this track.';
            aiRecoEmpty.hidden = !!suggestions.length;
          }
          addStatusLine(`Recommendations: ${suggestions.length ? `${suggestions.length} suggestion(s)` : 'none'}.`);
        } catch (e) {
          console.error('[ai_toolkit] reco render failed', e);
          throw e;
        }
      })
      .catch((err) => {
        if (reqId !== state.recoReqId) return;
        setClipRisk(false);
        applyDefaults();
        if (aiRecoEmpty) {
          aiRecoEmpty.textContent = 'Recommendations unavailable.';
        }
        console.debug('detect error', err?.name, err?.message);
        addStatusLine(`Recommendations: ${err.message || 'unavailable'}`);
        console.warn('[ai_toolkit] detect failed', err);
      })
      .finally(() => {
        clearTimeout(timeoutId);
        if (reqId !== state.recoReqId) return;
        if (aiRecoEmpty && aiRecoEmpty.textContent === 'Analyzing…') {
          aiRecoEmpty.textContent = 'Recommendations unavailable.';
        }
      });
  }

  function setSelectedFile(selected) {
    resetPlaybackState();
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
      addStatusLine('Upload: starting...');
      xhr.open('POST', '/api/analyze-upload', true);
      xhr.responseType = 'json';
      let lastPct = -1;
      xhr.upload.addEventListener('progress', (evt) => {
        if (!evt.lengthComputable) return;
        const pct = Math.floor((evt.loaded / evt.total) * 100);
        if (pct >= lastPct + 10 || pct === 100) {
          lastPct = pct;
          addStatusLine(`Upload: ${pct}%`);
        }
      });
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          addStatusLine('Upload: complete.');
          resolve(xhr.response || {});
        } else {
          addStatusLine('Upload: failed.');
          reject(new Error('upload_failed'));
        }
      });
      xhr.addEventListener('error', () => {
        addStatusLine('Upload: failed.');
        reject(new Error('upload_failed'));
      });
      xhr.send(fd);
    });
  }

  function updateStrengthFromUI() {
    updateAllTools();
    updateAllReverb();
    buildAudioGraph();
    updateFilters();
    updateSpectrumOnce();
  }

  async function saveCleanedCopy() {
    if (!state.selected?.rel) return;
    if (aiSaveStatus) aiSaveStatus.textContent = 'Rendering cleaned copy...';
    addStatusLine('Save: rendering cleaned copy...');
    if (!state.selectedSongId) {
      try {
        const res = await fetch('/api/library/import_source', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: state.selected.rel,
            title: state.selected.name || 'AI Toolkit',
          }),
        });
        if (res.ok) {
          const data = await res.json();
          state.selectedSongId = data?.song?.song_id || null;
        }
      } catch (_err) {
        state.selectedSongId = null;
      }
      if (!state.selectedSongId) {
        if (aiSaveStatus) aiSaveStatus.textContent = 'Save failed (missing song).';
        addStatusLine('Save failed: missing song.');
        return;
      }
    }
    const settings = {};
    toolDefs.forEach((tool) => {
      const toolState = ensureToolState(tool.id);
      settings[tool.id] = { enabled: toolState.enabled, value: toolState.value };
    });
    const reverb = ensureReverbState();
    const reverbEnabled = Object.values(reverb).some((entry) => entry && entry.enabled);
    if (reverbEnabled) {
      settings.ai_reverb_reduce = {
        enabled: true,
        value: 0,
        options: {
          enable_side_reduction: reverb.side_reduction.enabled,
          side_reduction_pct: reverb.side_reduction.value,
          enable_mid_suppress: reverb.mid_suppress.enabled,
          mid_suppress_db: reverb.mid_suppress.value,
          mid_freq_hz: reverb.mid_suppress.freq,
          enable_tail_gate: reverb.tail_gate.enabled,
          gate_threshold_db: reverb.tail_gate.threshold,
          gate_ratio: reverb.tail_gate.ratio,
          enable_low_cut: reverb.low_cut.enabled,
          low_cut_hz: reverb.low_cut.value,
          enable_high_cut: reverb.high_cut.enabled,
          high_cut_hz: reverb.high_cut.value,
        },
      };
    }
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
          .filter((tool) => ensureToolState(tool.id).enabled)
          .map((tool) => tool.title);
        if (reverbEnabled) {
          activeTools.push('Reverb Reduction');
        }
        const rel = data.output_rel;
        const format = (rel.split('.').pop() || '').toLowerCase();
        const addRes = await fetch('/api/library/add_version', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            song_id: state.selectedSongId,
            kind: 'aitk',
            label: 'AI Toolkit',
            title: 'AI Toolkit',
            utility: 'AITK',
            renditions: [{ format, rel }],
            summary: { aitk: 'AI Toolkit' },
            metrics: data.metrics || {},
            tags: activeTools,
            version_id: data.version_id,
          }),
        });
        if (!addRes.ok) {
          throw new Error('library_register_failed');
        }
        if (libraryBrowser) {
          if (state.selectedSongId && typeof libraryBrowser.expandSong === 'function') {
            libraryBrowser.expandSong(state.selectedSongId);
          }
          libraryBrowser.reload();
        }
        addStatusLine('Save: registered in library.');
        window.dispatchEvent(new CustomEvent('sonustemper:library-changed'));
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
      addStatusLine('Save failed.');
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
    updateToolRow(row);
    const slider = row.querySelector('[data-role="value"]');
    const checkbox = row.querySelector('[data-role="enabled"]');
    if (slider) {
      slider.addEventListener('input', () => {
        updateToolRow(row);
        updateStrengthFromUI();
      });
    }
    if (checkbox) {
      checkbox.addEventListener('change', () => {
        updateToolRow(row);
        updateStrengthFromUI();
      });
    }
  });
  reverbRows.forEach((row) => {
    updateReverbRow(row);
    const checkbox = row.querySelector('[data-role="enabled"]');
    const slider = row.querySelector('[data-role="value"]');
    const threshold = row.querySelector('[data-role="threshold"]');
    const ratio = row.querySelector('[data-role="ratio"]');
    if (checkbox) {
      checkbox.addEventListener('change', () => {
        updateReverbRow(row);
        updateStrengthFromUI();
      });
    }
    if (slider) {
      slider.addEventListener('input', () => {
        updateReverbRow(row);
        updateStrengthFromUI();
      });
    }
    if (threshold) {
      threshold.addEventListener('input', () => {
        updateReverbRow(row);
        updateStrengthFromUI();
      });
    }
    if (ratio) {
      ratio.addEventListener('input', () => {
        updateReverbRow(row);
        updateStrengthFromUI();
      });
    }
  });

  if (aiSaveBtn) {
    aiSaveBtn.addEventListener('click', saveCleanedCopy);
  }

  if (aiRecoApplyAll) {
    aiRecoApplyAll.addEventListener('click', () => {
      if (!state.recommendations.length) return;
      state.recommendations.forEach((item) => {
        if (typeof item.apply === 'function') {
          item.apply();
          return;
        }
        setToolValue(item.toolId, item.value, true);
      });
      updateAllTools();
      updateAllReverb();
      updateFilters();
      updateSpectrumOnce();
    });
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
          addStatusLine('Upload: ready.');
        } catch (_err) {
          addStatusLine('Upload: failed.');
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
  updateAllTools();
  updateAllReverb();
  updateFilters();
  updateSpectrumOnce();
  updateScopeOnce();
  updateSaveState();
  updateTimeLabel();
})();
