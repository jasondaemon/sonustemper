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
  const voiceSummary = document.getElementById('eqVoiceSummary');
  const voiceDeesserEnable = document.getElementById('eqVoiceDeesserEnable');
  const voiceDeesserFreq = document.getElementById('eqVoiceDeesserFreq');
  const voiceDeesserAmount = document.getElementById('eqVoiceDeesserAmount');
  const voiceDeesserVal = document.getElementById('eqVoiceDeesserVal');
  const voiceDeesserShow = document.getElementById('eqVoiceDeesserShow');
  const voiceDeesserListen = document.getElementById('eqVoiceDeesserListen');
  const voiceSmoothEnable = document.getElementById('eqVoiceSmoothEnable');
  const voiceSmoothAmount = document.getElementById('eqVoiceSmoothAmount');
  const voiceSmoothVal = document.getElementById('eqVoiceSmoothVal');
  const voiceSmoothShow = document.getElementById('eqVoiceSmoothShow');
  const voiceSmoothListen = document.getElementById('eqVoiceSmoothListen');
  const voiceDeharshEnable = document.getElementById('eqVoiceDeharshEnable');
  const voiceDeharshFreq = document.getElementById('eqVoiceDeharshFreq');
  const voiceDeharshAmount = document.getElementById('eqVoiceDeharshAmount');
  const voiceDeharshVal = document.getElementById('eqVoiceDeharshVal');
  const voiceDeharshShow = document.getElementById('eqVoiceDeharshShow');
  const voiceDeharshListen = document.getElementById('eqVoiceDeharshListen');
  let libraryBrowser = null;

  const EQ_DEBUG = new URLSearchParams(window.location.search).get('debug') === '1'
    || localStorage.getItem('st_eq_debug') === '1';
  const SEND_UI_LOGS = EQ_DEBUG && (new URLSearchParams(window.location.search).get('debug_server') === '1'
    || localStorage.getItem('st_eq_debug_server') === '1');

  function sendUiLog(level, msg, data) {
    if (!SEND_UI_LOGS) return;
    try {
      fetch('/api/ui-log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          level,
          tag: 'EQ',
          msg,
          data: data || {},
        }),
      }).catch(() => {});
    } catch (_) {}
  }

  function eqLog(...args) {
    if (!EQ_DEBUG) return;
    console.debug('[EQ]', ...args);
    sendUiLog('debug', String(args[0] || ''), args[1]);
  }
  function eqWarn(...args) {
    if (!EQ_DEBUG) return;
    console.warn('[EQ]', ...args);
    sendUiLog('warn', String(args[0] || ''), args[1]);
  }
  function eqErr(...args) {
    console.error('[EQ]', ...args);
    sendUiLog('error', String(args[0] || ''), args[1]);
  }

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
    hoverBandId: null,
    hoverVoiceId: null,
    dragVoiceId: null,
    lastSpectrumFrame: 0,
    spectrumDirty: false,
    voiceNodes: [],
    voice: {
      deesser: { enabled: false, show: false, listen: false, freq_hz: 6500, amount_db: 0 },
      vocal_smooth: { enabled: false, show: false, listen: false, amount_db: 0 },
      deharsh: { enabled: false, show: false, listen: false, freq_hz: 3200, amount_db: 0 },
      bypass: false,
    },
    voiceListenActive: null,
    listenBand: null,
    listenGain: null,
    normalGain: null,
    trackMode: 'any',
    rebuildTimes: [],
    paramUpdateTimes: [],
    lastDragLog: {},
  };

  let renderPending = false;
  function requestRender(reason) {
    state.lastRenderReason = reason || '';
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      drawFrame();
    });
  }

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

  function recordRebuild(reason, details) {
    const now = performance.now();
    state.rebuildTimes = state.rebuildTimes.filter((t) => now - t < 2000);
    state.rebuildTimes.push(now);
    if (state.rebuildTimes.length > 10) {
      eqWarn('rebuild storm', { count: state.rebuildTimes.length, windowMs: 2000, reason });
    }
    eqWarn('rebuildFilterChain', { reason, ...details });
  }

  function recordParamUpdate(kind, data) {
    const now = performance.now();
    state.paramUpdateTimes = state.paramUpdateTimes.filter((t) => now - t < 1000);
    state.paramUpdateTimes.push(now);
    if (state.paramUpdateTimes.length > 60) {
      eqWarn('param update storm', { count: state.paramUpdateTimes.length, windowMs: 1000 });
    }
    const last = state.lastDragLog[kind] || 0;
    if (now - last > 500) {
      state.lastDragLog[kind] = now;
      eqLog('drag updates', { type: kind, ...data });
    }
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
      eqLog('AudioContext created', { sampleRate: state.audioCtx.sampleRate, state: state.audioCtx.state });
      state.audioCtx.onstatechange = () => {
        eqWarn('AudioContext statechange', state.audioCtx.state);
      };
    }
    return state.audioCtx;
  }

  function ensureAudioGraph() {
    const ctx = ensureAudioContext();
    if (state.sourceNode) return;
    try {
      state.sourceNode = ctx.createMediaElementSource(audioEl);
    } catch (err) {
      eqWarn('createMediaElementSource failed', { err: String(err) });
      return;
    }
    eqLog('audio graph init', { sampleRate: ctx.sampleRate });
    state.analyser = ctx.createAnalyser();
    state.analyser.fftSize = 2048;
    state.analyser.smoothingTimeConstant = 0.85;
    state.normalGain = ctx.createGain();
    state.normalGain.gain.value = 1;
    state.listenGain = ctx.createGain();
    state.listenGain.gain.value = 0;
    rebuildFilterChain('graph_init');
  }

  function updateVoiceSummary() {
    const active = [];
    if (state.voice.deesser.enabled && state.voice.deesser.amount_db < 0) active.push('De-esser');
    if (state.voice.vocal_smooth.enabled && state.voice.vocal_smooth.amount_db < 0) active.push('Vocal Smooth');
    if (state.voice.deharsh.enabled && state.voice.deharsh.amount_db < 0) active.push('De-harsh');
    if (voiceSummary) {
      voiceSummary.textContent = active.length ? `Voice Controls: ${active.join(', ')}` : 'Voice Controls: none';
    }
  }

  function buildVoiceChain(inputNode) {
    state.voiceNodes.forEach((node) => {
      try {
        node.disconnect();
      } catch (err) {
        eqErr('voice disconnect failed', err);
      }
    });
    state.voiceNodes = [];
    let last = inputNode;
    if (state.voice.bypass) return last;
    const deesser = state.voice.deesser;
    if (deesser.enabled && deesser.amount_db < 0) {
      const node = state.audioCtx.createBiquadFilter();
      node.type = 'peaking';
      node.frequency.value = Math.max(3000, Math.min(10000, deesser.freq_hz));
      node.Q.value = 2.0;
      node.gain.value = deesser.amount_db;
      try {
        last.connect(node);
      } catch (err) {
        eqErr('voice connect failed', err);
      }
      last = node;
      state.voiceNodes.push(node);
    }
    const vocal = state.voice.vocal_smooth;
    if (vocal.enabled && vocal.amount_db < 0) {
      const node = state.audioCtx.createBiquadFilter();
      node.type = 'peaking';
      node.frequency.value = 4500;
      node.Q.value = 1.2;
      node.gain.value = vocal.amount_db;
      try {
        last.connect(node);
      } catch (err) {
        eqErr('voice connect failed', err);
      }
      last = node;
      state.voiceNodes.push(node);
    }
    const deharsh = state.voice.deharsh;
    if (deharsh.enabled && deharsh.amount_db < 0) {
      const node = state.audioCtx.createBiquadFilter();
      node.type = 'peaking';
      node.frequency.value = Math.max(1500, Math.min(6000, deharsh.freq_hz));
      node.Q.value = 1.5;
      node.gain.value = deharsh.amount_db;
      try {
        last.connect(node);
      } catch (err) {
        eqErr('voice connect failed', err);
      }
      last = node;
      state.voiceNodes.push(node);
    }
    return last;
  }

  function setListenMode(kind, enabled) {
    if (enabled) {
      state.voiceListenActive = kind;
      state.voice.deesser.listen = kind === 'deesser';
      state.voice.vocal_smooth.listen = kind === 'vocal_smooth';
      state.voice.deharsh.listen = kind === 'deharsh';
    } else {
      if (state.voiceListenActive === kind) state.voiceListenActive = null;
      state.voice.deesser.listen = false;
      state.voice.vocal_smooth.listen = false;
      state.voice.deharsh.listen = false;
    }
    eqLog('listen toggle', { kind, enabled });
    syncVoiceControls();
    ensureListenChain();
  }

  function rebuildFilterChain(reason = 'unknown') {
    if (!state.sourceNode || !state.analyser) return;
    const t0 = performance.now();
    try {
      state.sourceNode.disconnect();
    } catch (err) {
      eqErr('source disconnect failed', err);
    }
    state.bandNodes.forEach((node) => {
      try {
        node.disconnect();
      } catch (err) {
        eqErr('band disconnect failed', err);
      }
    });
    state.bandNodes.clear();
    let lastNode = buildVoiceChain(state.sourceNode);
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
        try {
          lastNode.connect(node);
        } catch (err) {
          eqErr('eq connect failed', err);
        }
        lastNode = node;
        state.bandNodes.set(band.id, node);
      });
    }
    try {
      lastNode.connect(state.analyser);
      state.analyser.connect(state.normalGain);
      state.normalGain.connect(state.audioCtx.destination);
    } catch (err) {
      eqErr('chain connect failed', err);
    }
    ensureListenChain();
    const eqEnabled = state.bands.filter((band) => band.enabled).length;
    const voiceEnabled = ['deesser', 'vocal_smooth', 'deharsh']
      .filter((key) => state.voice[key].enabled).length;
    recordRebuild(reason, {
      eqEnabled,
      voiceEnabled,
      bypassEQ: state.bypass,
      voiceBypass: state.voice.bypass,
      listen: state.voiceListenActive,
      ms: Number((performance.now() - t0).toFixed(1)),
    });
  }

  function setGain(node, value) {
    if (!node || !state.audioCtx) return;
    const now = state.audioCtx.currentTime;
    node.gain.cancelScheduledValues(now);
    node.gain.setTargetAtTime(value, now, 0.02);
  }

  function ensureListenChain() {
    if (!state.sourceNode || !state.listenGain) return;
    if (state.listenBand) {
      try {
        state.listenBand.disconnect();
      } catch (err) {
        eqErr('listen disconnect failed', err);
      }
      state.listenBand = null;
    }
    if (!state.voiceListenActive) {
      setGain(state.listenGain, 0);
      setGain(state.normalGain, 1);
      return;
    }
    const listenId = state.voiceListenActive;
    let center = 4500;
    let q = 1.6;
    if (listenId === 'deesser') {
      center = Math.max(3000, Math.min(10000, state.voice.deesser.freq_hz));
      q = 2.0;
    } else if (listenId === 'deharsh') {
      center = Math.max(1500, Math.min(6000, state.voice.deharsh.freq_hz));
      q = 1.6;
    } else if (listenId === 'vocal_smooth') {
      center = 4500;
      q = 1.4;
    }
    const band = state.audioCtx.createBiquadFilter();
    band.type = 'bandpass';
    band.frequency.value = center;
    band.Q.value = q;
    try {
      state.sourceNode.connect(band);
      band.connect(state.listenGain);
      state.listenGain.connect(state.audioCtx.destination);
    } catch (err) {
      eqErr('listen connect failed', err);
    }
    state.listenBand = band;
    setGain(state.normalGain, 0);
    setGain(state.listenGain, 1);
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
    rebuildFilterChain('band_add');
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
    rebuildFilterChain('band_remove');
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
      is_template: false,
    };
  }

  function defaultSoundboardBands() {
    const now = Date.now();
    return [
      { id: `b_hpf_${now}`, type: 'highpass', freq_hz: 30, gain_db: 0, q: 0.71, enabled: true, is_template: true },
      { id: `b_low_${now}`, type: 'lowshelf', freq_hz: 100, gain_db: 0, q: 0.71, enabled: true, is_template: true },
      { id: `b_lm_${now}`, type: 'peaking', freq_hz: 250, gain_db: 0, q: 1.0, enabled: true, is_template: true },
      { id: `b_mid_${now}`, type: 'peaking', freq_hz: 1000, gain_db: 0, q: 1.0, enabled: true, is_template: true },
      { id: `b_hm_${now}`, type: 'peaking', freq_hz: 3500, gain_db: 0, q: 1.0, enabled: true, is_template: true },
      { id: `b_high_${now}`, type: 'highshelf', freq_hz: 10000, gain_db: 0, q: 0.71, enabled: true, is_template: true },
      { id: `b_lpf_${now}`, type: 'lowpass', freq_hz: 18000, gain_db: 0, q: 0.71, enabled: false, is_template: true },
    ];
  }

  function typeLabel(type) {
    switch (type) {
      case 'peaking':
        return 'Bell';
      case 'lowshelf':
        return 'Low Shelf';
      case 'highshelf':
        return 'High Shelf';
      case 'highpass':
        return 'HPF';
      case 'lowpass':
        return 'LPF';
      default:
        return type;
    }
  }

  function renderBands() {
    if (!bandListEl) return;
    bandListEl.innerHTML = '';
    state.bands.forEach((band) => {
      const chip = document.createElement('div');
      chip.className = `eq-band-chip${band.id === state.selectedBandId ? ' active' : ''}${band.enabled ? '' : ' muted'}`;
      const enable = document.createElement('input');
      enable.type = 'checkbox';
      enable.className = 'eq-band-enable';
      enable.checked = !!band.enabled;
      enable.addEventListener('click', (evt) => {
        evt.stopPropagation();
        band.enabled = enable.checked;
        rebuildFilterChain('band_enable_toggle');
        renderBands();
        drawSpectrumOnce();
      });
      chip.appendChild(enable);
      const typeSpan = document.createElement('span');
      typeSpan.className = 'eq-band-type';
      typeSpan.textContent = typeLabel(band.type);
      chip.appendChild(typeSpan);
      const freqSpan = document.createElement('span');
      freqSpan.className = 'eq-band-freq';
      freqSpan.textContent = `${formatFreq(band.freq_hz)}Hz`;
      chip.appendChild(freqSpan);
      const gainSpan = document.createElement('span');
      gainSpan.className = 'eq-band-gain';
      gainSpan.textContent = `${band.gain_db.toFixed(1)}dB`;
      chip.appendChild(gainSpan);
      if (!band.is_template) {
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'eq-band-remove';
        remove.textContent = '×';
        remove.addEventListener('click', (evt) => {
          evt.stopPropagation();
          removeBand(band.id);
        });
        chip.appendChild(remove);
      }
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
      if (bandEnabled) bandEnabled.checked = false;
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
    if (bandEnabled) bandEnabled.checked = !!band.enabled;
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
    if (bandEnabled) {
      bandEnabled.addEventListener('change', () => {
        const band = selectedBand();
        if (!band) return;
        band.enabled = bandEnabled.checked;
        rebuildFilterChain('band_enable_toggle');
        renderBands();
        drawSpectrumOnce();
      });
    }
    bandType.addEventListener('change', () => {
      updateSelectedBand({ type: bandType.value });
    });
    bandFreq.addEventListener('input', () => {
      updateSelectedBand({ freq_hz: freqFromRange(bandFreq.value) });
      bandFreqRange.value = bandFreq.value;
      recordParamUpdate('band', { freq: freqFromRange(bandFreq.value) });
    });
    bandFreqRange.addEventListener('input', () => {
      updateSelectedBand({ freq_hz: freqFromRange(bandFreqRange.value) });
      bandFreq.value = bandFreqRange.value;
      recordParamUpdate('band', { freq: freqFromRange(bandFreqRange.value) });
    });
    bandGain.addEventListener('input', () => {
      let next = gainFromRange(bandGain.value);
      if (Math.abs(next) <= 0.3) next = 0;
      updateSelectedBand({ gain_db: next });
      bandGainRange.value = next;
      recordParamUpdate('band', { gain: next });
    });
    bandGainRange.addEventListener('input', () => {
      let next = gainFromRange(bandGainRange.value);
      if (Math.abs(next) <= 0.3) next = 0;
      updateSelectedBand({ gain_db: next });
      bandGain.value = next;
      recordParamUpdate('band', { gain: next });
    });
    bandQ.addEventListener('input', () => {
      updateSelectedBand({ q: qFromRange(bandQ.value) });
      bandQRange.value = bandQ.value;
      recordParamUpdate('band', { q: qFromRange(bandQ.value) });
    });
    bandQRange.addEventListener('input', () => {
      updateSelectedBand({ q: qFromRange(bandQRange.value) });
      bandQ.value = bandQRange.value;
      recordParamUpdate('band', { q: qFromRange(bandQRange.value) });
    });
    bypassToggle.addEventListener('change', () => {
      state.bypass = bypassToggle.checked;
      rebuildFilterChain('bypass_toggle');
    });
  }

  function bindVoiceControls() {
    if (voiceDeesserEnable) {
      voiceDeesserEnable.addEventListener('change', () => {
        state.voice.deesser.enabled = voiceDeesserEnable.checked;
        if (state.voice.deesser.enabled && !state.voice.deesser.show) {
          state.voice.deesser.show = true;
          syncVoiceControls();
        }
        if (!state.voice.deesser.enabled) {
          state.voice.deesser.listen = false;
          if (state.voiceListenActive === 'deesser') state.voiceListenActive = null;
        }
        rebuildFilterChain('voice_enable_toggle');
        updateVoiceSummary();
        drawSpectrumOnce();
        ensureListenChain();
      });
    }
    if (voiceDeesserFreq) {
      voiceDeesserFreq.addEventListener('input', () => {
        state.voice.deesser.freq_hz = Math.max(3000, Math.min(10000, Number(voiceDeesserFreq.value)));
        rebuildFilterChain('voice_param_change');
        recordParamUpdate('deesser', { freq: state.voice.deesser.freq_hz, amount: state.voice.deesser.amount_db });
        drawSpectrumOnce();
      });
    }
    if (voiceDeesserAmount) {
      voiceDeesserAmount.addEventListener('input', () => {
        state.voice.deesser.amount_db = Math.max(-12, Math.min(0, Number(voiceDeesserAmount.value)));
        if (voiceDeesserVal) voiceDeesserVal.textContent = `${state.voice.deesser.amount_db.toFixed(1)} dB`;
        rebuildFilterChain('voice_param_change');
        recordParamUpdate('deesser', { freq: state.voice.deesser.freq_hz, amount: state.voice.deesser.amount_db });
        updateVoiceSummary();
        drawSpectrumOnce();
      });
    }
    if (voiceDeesserShow) {
      voiceDeesserShow.addEventListener('change', () => {
        state.voice.deesser.show = voiceDeesserShow.checked;
        drawSpectrumOnce();
      });
    }
    if (voiceDeesserListen) {
      voiceDeesserListen.addEventListener('change', () => {
        setListenMode('deesser', voiceDeesserListen.checked);
      });
    }
    if (voiceSmoothEnable) {
      voiceSmoothEnable.addEventListener('change', () => {
        state.voice.vocal_smooth.enabled = voiceSmoothEnable.checked;
        if (state.voice.vocal_smooth.enabled && !state.voice.vocal_smooth.show) {
          state.voice.vocal_smooth.show = true;
          syncVoiceControls();
        }
        if (!state.voice.vocal_smooth.enabled) {
          state.voice.vocal_smooth.listen = false;
          if (state.voiceListenActive === 'vocal_smooth') state.voiceListenActive = null;
        }
        rebuildFilterChain('voice_enable_toggle');
        updateVoiceSummary();
        drawSpectrumOnce();
        ensureListenChain();
      });
    }
    if (voiceSmoothAmount) {
      voiceSmoothAmount.addEventListener('input', () => {
        state.voice.vocal_smooth.amount_db = Math.max(-6, Math.min(0, Number(voiceSmoothAmount.value)));
        if (voiceSmoothVal) voiceSmoothVal.textContent = `${state.voice.vocal_smooth.amount_db.toFixed(1)} dB`;
        rebuildFilterChain('voice_param_change');
        recordParamUpdate('vocal_smooth', { freq: 4500, amount: state.voice.vocal_smooth.amount_db });
        updateVoiceSummary();
        drawSpectrumOnce();
      });
    }
    if (voiceSmoothShow) {
      voiceSmoothShow.addEventListener('change', () => {
        state.voice.vocal_smooth.show = voiceSmoothShow.checked;
        drawSpectrumOnce();
      });
    }
    if (voiceSmoothListen) {
      voiceSmoothListen.addEventListener('change', () => {
        setListenMode('vocal_smooth', voiceSmoothListen.checked);
      });
    }
    if (voiceDeharshEnable) {
      voiceDeharshEnable.addEventListener('change', () => {
        state.voice.deharsh.enabled = voiceDeharshEnable.checked;
        if (state.voice.deharsh.enabled && !state.voice.deharsh.show) {
          state.voice.deharsh.show = true;
          syncVoiceControls();
        }
        if (!state.voice.deharsh.enabled) {
          state.voice.deharsh.listen = false;
          if (state.voiceListenActive === 'deharsh') state.voiceListenActive = null;
        }
        rebuildFilterChain('voice_enable_toggle');
        updateVoiceSummary();
        drawSpectrumOnce();
        ensureListenChain();
      });
    }
    if (voiceDeharshFreq) {
      voiceDeharshFreq.addEventListener('input', () => {
        state.voice.deharsh.freq_hz = Math.max(1500, Math.min(6000, Number(voiceDeharshFreq.value)));
        rebuildFilterChain('voice_param_change');
        recordParamUpdate('deharsh', { freq: state.voice.deharsh.freq_hz, amount: state.voice.deharsh.amount_db });
        drawSpectrumOnce();
      });
    }
    if (voiceDeharshAmount) {
      voiceDeharshAmount.addEventListener('input', () => {
        state.voice.deharsh.amount_db = Math.max(-6, Math.min(0, Number(voiceDeharshAmount.value)));
        if (voiceDeharshVal) voiceDeharshVal.textContent = `${state.voice.deharsh.amount_db.toFixed(1)} dB`;
        rebuildFilterChain('voice_param_change');
        recordParamUpdate('deharsh', { freq: state.voice.deharsh.freq_hz, amount: state.voice.deharsh.amount_db });
        updateVoiceSummary();
        drawSpectrumOnce();
      });
    }
    if (voiceDeharshShow) {
      voiceDeharshShow.addEventListener('change', () => {
        state.voice.deharsh.show = voiceDeharshShow.checked;
        drawSpectrumOnce();
      });
    }
    if (voiceDeharshListen) {
      voiceDeharshListen.addEventListener('change', () => {
        setListenMode('deharsh', voiceDeharshListen.checked);
      });
    }
    updateVoiceSummary();
  }

  function syncVoiceControls() {
    if (voiceDeesserEnable) voiceDeesserEnable.checked = state.voice.deesser.enabled;
    if (voiceDeesserFreq) voiceDeesserFreq.value = state.voice.deesser.freq_hz;
    if (voiceDeesserAmount) voiceDeesserAmount.value = state.voice.deesser.amount_db;
    if (voiceDeesserVal) voiceDeesserVal.textContent = `${state.voice.deesser.amount_db.toFixed(1)} dB`;
    if (voiceDeesserShow) voiceDeesserShow.checked = state.voice.deesser.show;
    if (voiceDeesserListen) voiceDeesserListen.checked = state.voice.deesser.listen;
    if (voiceSmoothEnable) voiceSmoothEnable.checked = state.voice.vocal_smooth.enabled;
    if (voiceSmoothAmount) voiceSmoothAmount.value = state.voice.vocal_smooth.amount_db;
    if (voiceSmoothVal) voiceSmoothVal.textContent = `${state.voice.vocal_smooth.amount_db.toFixed(1)} dB`;
    if (voiceSmoothShow) voiceSmoothShow.checked = state.voice.vocal_smooth.show;
    if (voiceSmoothListen) voiceSmoothListen.checked = state.voice.vocal_smooth.listen;
    if (voiceDeharshEnable) voiceDeharshEnable.checked = state.voice.deharsh.enabled;
    if (voiceDeharshFreq) voiceDeharshFreq.value = state.voice.deharsh.freq_hz;
    if (voiceDeharshAmount) voiceDeharshAmount.value = state.voice.deharsh.amount_db;
    if (voiceDeharshVal) voiceDeharshVal.textContent = `${state.voice.deharsh.amount_db.toFixed(1)} dB`;
    if (voiceDeharshShow) voiceDeharshShow.checked = state.voice.deharsh.show;
    if (voiceDeharshListen) voiceDeharshListen.checked = state.voice.deharsh.listen;
    updateVoiceSummary();
  }

  function drawFrame() {
    if (!spectrumCanvas) return;
    const ctx = spectrumCanvas.getContext('2d');
    const rect = spectrumCanvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);
    drawEqGrid(ctx, rect.width, rect.height);
    if (state.spectrumSmooth) {
      drawSpectrumBehindCurve(ctx, rect.width, rect.height);
    }
    drawVoiceOverlays(ctx, rect.width, rect.height);
    drawFilterRegions(ctx, rect.width, rect.height);
    drawEqCurve(ctx, rect.width, rect.height);
    drawBandHandles(ctx, rect.width, rect.height);
  }

  function drawSpectrumOnce() {
    requestRender('once');
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

  function drawEqGrid(ctx, width, height) {
    ctx.save();
    ctx.font = '10px sans-serif';
    ctx.fillStyle = 'rgba(175,195,220,0.6)';
    ctx.strokeStyle = 'rgba(120,140,165,0.25)';
    ctx.lineWidth = 1;
    const freqs = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
    freqs.forEach((freq) => {
      const x = freqToX(freq, width);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
      const label = freq >= 1000 ? `${(freq / 1000).toFixed(freq === 1000 ? 0 : 1)}k` : `${freq}`;
      ctx.fillText(label, Math.min(width - 28, x + 4), height - 4);
    });
    const gains = [12, 6, 0, -6, -12];
    gains.forEach((gain) => {
      const y = gainToY(gain, height);
      ctx.strokeStyle = gain === 0 ? 'rgba(200,220,255,0.5)' : 'rgba(120,140,165,0.2)';
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.fillText(`${gain > 0 ? '+' : ''}${gain}dB`, 6, Math.max(12, y - 2));
    });
    ctx.fillStyle = 'rgba(160,180,205,0.5)';
    ctx.fillText('Spectrum (relative)', width - 120, 14);
    ctx.restore();
  }

  function drawSpectrumBehindCurve(ctx, width, height) {
    if (!state.spectrumSmooth || !state.analyser) return;
    const sr = state.audioCtx?.sampleRate || 44100;
    const bins = state.spectrumSmooth.length;
    const dbMin = -80;
    const dbMax = 0;
    ctx.save();
    ctx.fillStyle = 'rgba(64,110,170,0.18)';
    ctx.strokeStyle = 'rgba(90,140,200,0.35)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < bins; i += 1) {
      const freq = (i * sr) / (state.analyser.fftSize || 2048);
      if (freq < 20 || freq > 20000) continue;
      const x = freqToX(freq, width);
      const mag = Math.max(state.spectrumSmooth[i] / 255, 1e-4);
      const db = 20 * Math.log10(mag);
      const clamped = Math.max(dbMin, Math.min(dbMax, db));
      const y = height - ((clamped - dbMin) / (dbMax - dbMin)) * height;
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    started = false;
    for (let i = 0; i < bins; i += 1) {
      const freq = (i * sr) / (state.analyser.fftSize || 2048);
      if (freq < 20 || freq > 20000) continue;
      const x = freqToX(freq, width);
      const mag = Math.max(state.spectrumSmooth[i] / 255, 1e-4);
      const db = 20 * Math.log10(mag);
      const clamped = Math.max(dbMin, Math.min(dbMax, db));
      const y = height - ((clamped - dbMin) / (dbMax - dbMin)) * height;
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  function getVoiceHandles(width, height) {
    const handles = [];
    if (state.voice.deesser.enabled && state.voice.deesser.show) {
      handles.push({
        id: 'deesser',
        label: 'De-esser',
        color: 'rgba(80,220,220,0.2)',
        handleColor: 'rgba(80,220,220,0.55)',
        freq: state.voice.deesser.freq_hz,
        amount: state.voice.deesser.amount_db,
        allowX: true,
        allowY: true,
      });
    }
    if (state.voice.vocal_smooth.enabled && state.voice.vocal_smooth.show) {
      handles.push({
        id: 'vocal_smooth',
        label: 'Vocal Smooth',
        color: 'rgba(110,220,150,0.2)',
        handleColor: 'rgba(110,220,150,0.55)',
        freq: 4500,
        amount: state.voice.vocal_smooth.amount_db,
        allowX: false,
        allowY: true,
      });
    }
    if (state.voice.deharsh.enabled && state.voice.deharsh.show) {
      handles.push({
        id: 'deharsh',
        label: 'De-harsh',
        color: 'rgba(160,120,220,0.2)',
        handleColor: 'rgba(160,120,220,0.55)',
        freq: state.voice.deharsh.freq_hz,
        amount: state.voice.deharsh.amount_db,
        allowX: true,
        allowY: true,
      });
    }
    return handles.map((handle) => {
      const leftHz = handle.freq / Math.sqrt(2);
      const rightHz = handle.freq * Math.sqrt(2);
      const x0 = freqToX(leftHz, width);
      const x1 = freqToX(rightHz, width);
      const x = freqToX(handle.freq, width);
      const y = gainToY(handle.amount, height);
      return { ...handle, x0, x1, x, y };
    });
  }

  function drawVoiceOverlays(ctx, width, height) {
    const overlays = getVoiceHandles(width, height);
    let tooltip = null;
    overlays.forEach((overlay) => {
      ctx.save();
      ctx.fillStyle = overlay.color;
      ctx.fillRect(overlay.x0, 0, overlay.x1 - overlay.x0, height);
      ctx.fillStyle = overlay.handleColor;
      ctx.beginPath();
      ctx.arc(overlay.x, overlay.y, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
      if (overlay.id === state.hoverVoiceId || overlay.id === state.dragVoiceId) {
        tooltip = {
          text: `Pre-EQ ${overlay.label} ${formatFreq(overlay.freq)}Hz ${overlay.amount.toFixed(1)}dB`,
          x: overlay.x,
          y: overlay.y,
        };
      }
    });
    if (tooltip) {
      drawTooltip(ctx, tooltip.text, tooltip.x, tooltip.y, width, height);
    }
  }

  function bandColor(band) {
    if (band.type === 'highpass') return '#7dffb0';
    if (band.type === 'lowpass') return '#7aa5ff';
    if (band.type === 'lowshelf') return '#86e3ff';
    if (band.type === 'highshelf') return '#ffd18a';
    return '#7aa5ff';
  }

  function drawFilterRegions(ctx, width, height) {
    const zeroY = gainToY(0, height);
    const minY = gainToY(-12, height);
    state.bands.forEach((band) => {
      if (!band.enabled || state.bypass) return;
      if (band.type !== 'highpass' && band.type !== 'lowpass') return;
      const x = freqToX(band.freq_hz, width);
      const color = bandColor(band);
      ctx.save();
      ctx.fillStyle = `${color}22`;
      if (band.type === 'highpass') {
        ctx.fillRect(0, zeroY, x, minY - zeroY);
      } else {
        ctx.fillRect(x, zeroY, width - x, minY - zeroY);
      }
      ctx.restore();
    });
  }

  function filterAttenuation(band, freq) {
    if (band.type !== 'highpass' && band.type !== 'lowpass') return 0;
    const fc = Math.max(20, Math.min(20000, band.freq_hz));
    const q = Math.max(0.2, Math.min(12, band.q));
    const slope = 12 * (0.8 + 0.2 * (q / 12));
    if (band.type === 'highpass') {
      if (freq >= fc) return 0;
      const x = Math.log2(fc / Math.max(freq, 1e-6));
      return -Math.min(12, x * slope);
    }
    if (freq <= fc) return 0;
    const x = Math.log2(Math.max(freq, 1e-6) / fc);
    return -Math.min(12, x * slope);
  }

  function drawTooltip(ctx, text, x, y, width, height) {
    if (!text) return;
    ctx.save();
    ctx.font = '11px sans-serif';
    const padding = 6;
    const metrics = ctx.measureText(text);
    const boxW = metrics.width + padding * 2;
    const boxH = 20;
    let bx = x + 10;
    let by = y - 28;
    if (bx + boxW > width) bx = width - boxW - 6;
    if (by < 6) by = y + 10;
    ctx.fillStyle = 'rgba(10,14,20,0.9)';
    ctx.strokeStyle = 'rgba(120,140,165,0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    if (ctx.roundRect) {
      ctx.roundRect(bx, by, boxW, boxH, 6);
    } else {
      ctx.rect(bx, by, boxW, boxH);
    }
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = 'rgba(220,235,255,0.9)';
    ctx.fillText(text, bx + padding, by + 14);
    ctx.restore();
  }

  function updateSpectrumData() {
    if (!state.analyser) return;
    if (!state.spectrumData || !state.spectrumSmooth || state.spectrumData.length !== state.analyser.frequencyBinCount) {
      state.spectrumData = new Uint8Array(state.analyser.frequencyBinCount);
      state.spectrumSmooth = new Float32Array(state.spectrumData.length);
    }
    state.analyser.getByteFrequencyData(state.spectrumData);
    const bins = state.spectrumData.length;
    const alpha = 0.2;
    for (let i = 0; i < bins; i += 1) {
      const val = state.spectrumData[i];
      state.spectrumSmooth[i] = state.spectrumSmooth[i] * (1 - alpha) + val * alpha;
    }
    state.spectrumDirty = true;
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
        if (band.type === 'highpass' || band.type === 'lowpass') {
          gain += filterAttenuation(band, freq);
          return;
        }
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
    let tooltip = null;
    state.bands.forEach((band) => {
      if (!band.enabled || state.bypass) return;
      const x = freqToX(band.freq_hz, width);
      const y = gainToY(band.type === 'highpass' || band.type === 'lowpass' ? 0 : band.gain_db, height);
      const selected = band.id === state.selectedBandId;
      const baseColor = bandColor(band);
      ctx.fillStyle = selected ? '#ffd18a' : baseColor;
      ctx.beginPath();
      ctx.arc(x, y, selected ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
      if (selected) {
        ctx.strokeStyle = 'rgba(255,210,140,0.9)';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (band.id === state.selectedBandId || band.id === state.hoverBandId) {
        if (!state.hoverVoiceId && !state.dragVoiceId) {
          tooltip = {
            text: `${typeLabel(band.type)} ${formatFreq(band.freq_hz)}Hz ${band.gain_db.toFixed(1)}dB Q ${band.q.toFixed(2)}`,
            x,
            y,
          };
        }
      }
    });
    if (tooltip) {
      drawTooltip(ctx, tooltip.text, tooltip.x, tooltip.y, width, height);
    }
  }

  function startSpectrum() {
    cancelAnimationFrame(state.spectrumRaf);
    const loop = () => {
      const now = performance.now();
      if (!state.lastSpectrumFrame || now - state.lastSpectrumFrame > 32) {
        updateSpectrumData();
        requestRender('spectrum');
        state.lastSpectrumFrame = now;
      }
      state.spectrumRaf = requestAnimationFrame(loop);
    };
    loop();
  }

  function stopSpectrum() {
    cancelAnimationFrame(state.spectrumRaf);
    state.spectrumRaf = null;
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
    eqLog('play requested', { t: audioEl.currentTime, ctx: state.audioCtx?.state });
    ensureAudioContext().resume().then(() => {
      audioEl.play().then(() => {
        state.isPlaying = true;
        playBtn.textContent = 'Pause';
        playBtn.classList.add('playing');
        startSpectrum();
      }).catch((err) => {
        eqWarn('play failed', { err: String(err) });
      });
    });
  }

  function handlePause() {
    audioEl.pause();
    state.isPlaying = false;
    playBtn.textContent = 'Play';
    playBtn.classList.remove('playing');
    stopSpectrum();
    eqLog('paused', { t: audioEl.currentTime });
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
    ['play', 'pause', 'ended', 'error', 'stalled', 'waiting', 'loadstart', 'loadedmetadata', 'canplay'].forEach((evtName) => {
      audioEl.addEventListener(evtName, (evt) => {
        eqWarn('audioEl event', {
          type: evt.type,
          t: audioEl.currentTime,
          readyState: audioEl.readyState,
          networkState: audioEl.networkState,
        });
      });
    });
    audioEl.addEventListener('ended', () => {
      handlePause();
      updatePlayhead();
    });
    addBandBtn.addEventListener('click', () => addBand(bandDefaults()));
    resetBtn.addEventListener('click', () => {
      if (!confirm('Reset EQ to the default 7-band layout?')) return;
      state.bands = defaultSoundboardBands();
      state.selectedBandId = state.bands.find((band) => band.type === 'peaking')?.id || state.bands[0]?.id || null;
      rebuildFilterChain('reset_default');
      renderBands();
      drawSpectrumOnce();
    });
    bindBandControls();
    bindVoiceControls();
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
    document.addEventListener('keydown', (evt) => {
      if (evt.key !== 'Delete' && evt.key !== 'Backspace') return;
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((evt.target || {}).tagName)) return;
      const band = selectedBand();
      if (!band) return;
      if (band.is_template) {
        if (!confirm('Remove this template band?')) return;
      }
      removeBand(band.id);
    });
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
    const voicePayload = {
      bypass: !!state.voice.bypass,
      deesser: {
        enabled: !!state.voice.deesser.enabled,
        freq_hz: state.voice.deesser.freq_hz,
        amount_db: state.voice.deesser.amount_db,
      },
      vocal_smooth: {
        enabled: !!state.voice.vocal_smooth.enabled,
        amount_db: state.voice.vocal_smooth.amount_db,
      },
      deharsh: {
        enabled: !!state.voice.deharsh.enabled,
        freq_hz: state.voice.deharsh.freq_hz,
        amount_db: state.voice.deharsh.amount_db,
      },
    };
    try {
      const res = await fetch('/api/eq/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: state.selectedPath,
          song_id: state.selectedSongId,
          voice_controls: voicePayload,
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
      saveStatus.textContent = 'Saved.';
      saveResult.innerHTML = '';
      const link = document.createElement('a');
      link.href = data.download_url || `/api/utility-download?path=${encodeURIComponent(data.output_rel)}`;
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
        const analyzeRel = data.output_rel;
        url.searchParams.set('path', analyzeRel);
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
    let dragVoiceId = null;
    function pointToFreqGain(x, y, rect) {
      const freq = 20 * Math.pow(20000 / 20, x / rect.width);
      const gain = 12 - (y / rect.height) * 24;
      return { freq: freqFromRange(freq), gain: gainFromRange(gain) };
    }
    function findVoiceAt(x, y, rect) {
      const maxDist = 10;
      let hit = null;
      const handles = getVoiceHandles(rect.width, rect.height);
      handles.forEach((handle) => {
        const dist = Math.hypot(handle.x - x, handle.y - y);
        if (dist <= maxDist) hit = handle;
      });
      return hit;
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
      const voiceHit = findVoiceAt(x, y, rect);
      if (voiceHit) {
        state.hoverVoiceId = voiceHit.id;
        dragVoiceId = voiceHit.id;
        state.dragVoiceId = voiceHit.id;
        if (state.voiceListenActive && state.voiceListenActive !== voiceHit.id) {
          setListenMode(voiceHit.id, true);
        }
        drawSpectrumOnce();
        return;
      }
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
    spectrumCanvas.addEventListener('mousemove', (evt) => {
      const rect = spectrumCanvas.getBoundingClientRect();
      const x = evt.clientX - rect.left;
      const y = evt.clientY - rect.top;
      const voiceHit = findVoiceAt(x, y, rect);
      if (voiceHit) {
        state.hoverVoiceId = voiceHit.id;
        state.hoverBandId = null;
      } else {
        state.hoverVoiceId = null;
        const hit = findBandAt(x, y, rect);
        state.hoverBandId = hit?.id || null;
      }
      requestRender('mousemove');
    });
    spectrumCanvas.addEventListener('mouseleave', () => {
      state.hoverBandId = null;
      state.hoverVoiceId = null;
      requestRender('mouseleave');
    });
    document.addEventListener('mousemove', (evt) => {
      if (dragVoiceId) {
        const rect = spectrumCanvas.getBoundingClientRect();
        const x = Math.max(0, Math.min(rect.width, evt.clientX - rect.left));
        const y = Math.max(0, Math.min(rect.height, evt.clientY - rect.top));
        const point = pointToFreqGain(x, y, rect);
        const damp = evt.shiftKey ? 0.25 : 1;
        if (dragVoiceId === 'deesser') {
          const nextFreq = state.voice.deesser.freq_hz + (point.freq - state.voice.deesser.freq_hz) * damp;
          const nextGain = state.voice.deesser.amount_db + (point.gain - state.voice.deesser.amount_db) * damp;
          state.voice.deesser.freq_hz = Math.max(3000, Math.min(10000, nextFreq));
          state.voice.deesser.amount_db = Math.max(-12, Math.min(0, nextGain));
          if (voiceDeesserFreq) voiceDeesserFreq.value = state.voice.deesser.freq_hz;
          if (voiceDeesserAmount) voiceDeesserAmount.value = state.voice.deesser.amount_db;
          if (voiceDeesserVal) voiceDeesserVal.textContent = `${state.voice.deesser.amount_db.toFixed(1)} dB`;
          recordParamUpdate('deesser', { freq: state.voice.deesser.freq_hz, amount: state.voice.deesser.amount_db });
        } else if (dragVoiceId === 'deharsh') {
          const nextFreq = state.voice.deharsh.freq_hz + (point.freq - state.voice.deharsh.freq_hz) * damp;
          const nextGain = state.voice.deharsh.amount_db + (point.gain - state.voice.deharsh.amount_db) * damp;
          state.voice.deharsh.freq_hz = Math.max(1500, Math.min(6000, nextFreq));
          state.voice.deharsh.amount_db = Math.max(-6, Math.min(0, nextGain));
          if (voiceDeharshFreq) voiceDeharshFreq.value = state.voice.deharsh.freq_hz;
          if (voiceDeharshAmount) voiceDeharshAmount.value = state.voice.deharsh.amount_db;
          if (voiceDeharshVal) voiceDeharshVal.textContent = `${state.voice.deharsh.amount_db.toFixed(1)} dB`;
          recordParamUpdate('deharsh', { freq: state.voice.deharsh.freq_hz, amount: state.voice.deharsh.amount_db });
        } else if (dragVoiceId === 'vocal_smooth') {
          const nextGain = state.voice.vocal_smooth.amount_db + (point.gain - state.voice.vocal_smooth.amount_db) * damp;
          state.voice.vocal_smooth.amount_db = Math.max(-6, Math.min(0, nextGain));
          if (voiceSmoothAmount) voiceSmoothAmount.value = state.voice.vocal_smooth.amount_db;
          if (voiceSmoothVal) voiceSmoothVal.textContent = `${state.voice.vocal_smooth.amount_db.toFixed(1)} dB`;
          recordParamUpdate('vocal_smooth', { freq: 4500, amount: state.voice.vocal_smooth.amount_db });
        }
        updateVoiceSummary();
        rebuildFilterChain('voice_drag');
        ensureListenChain();
        drawSpectrumOnce();
        return;
      }
      if (!dragBandId) return;
      const rect = spectrumCanvas.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, evt.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, evt.clientY - rect.top));
      const point = pointToFreqGain(x, y, rect);
      const snappedGain = Math.abs(point.gain) <= 0.3 && !evt.shiftKey ? 0 : point.gain;
      if (state.selectedBandId !== dragBandId) {
        state.selectedBandId = dragBandId;
      }
      updateSelectedBand({ freq_hz: point.freq, gain_db: snappedGain });
      recordParamUpdate('band', { freq: point.freq, gain: snappedGain });
    });
    document.addEventListener('mouseup', () => {
      dragBandId = null;
      dragVoiceId = null;
      state.dragVoiceId = null;
    });
    spectrumCanvas.addEventListener('wheel', (evt) => {
      if (!evt.shiftKey) return;
      const band = selectedBand();
      if (!band) return;
      evt.preventDefault();
      const baseQ = Number(band.q) || 0;
      const step = evt.deltaY > 0 ? -0.2 : 0.2;
      const nextQ = qFromRange(baseQ + step);
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
    state.bands = defaultSoundboardBands();
    state.selectedBandId = state.bands.find((band) => band.type === 'peaking')?.id || state.bands[0]?.id || null;
    rebuildFilterChain('init_default');
    renderBands();
    syncVoiceControls();
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
