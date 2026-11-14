<script lang="ts">
  import { onMount } from 'svelte';
  import EqPanel from './EqPanel.svelte';
  import {monitor, filterNode, spectrumPath, defaultBands, type Band} from './monitor';
  type Preset = {id: string; title: string; bands: Band[]; width?: number; meta: {tags?: string[]}};
  type LoudnessProfile = {name: string; origin: string; meta: {title: string; lufs: number | null; tp: number | null; category?: string; order?: number}};
  type Correction = {enabled: boolean; value: number; options?: Record<string, number | boolean>};
  const treatments = [
    {id:'ai_deglass', title:'Soften harsh highs', value:-2},
    {id:'ai_vocal_smooth', title:'Smooth vocals', value:-2},
    {id:'ai_bass_tight', title:'Tighten bass', value:-2},
    {id:'ai_transient_soften', title:'Soften transients', value:-2},
    {id:'ai_reverb_reduce', title:'Reduce reverb', value:25},
  ];
  let profiles = $state<LoudnessProfile[]>([]);
  let profile = $state('guiding-night');
  let strength = $state(100), width = $state(1), guardrails = $state(true);
  let activePreset = $derived(presets.find(item => item.id === presetBase));
  let waveform = $state<HTMLDivElement>();
  let waveError = $state(false);
  type Result = {track: string; mp3: string; lufs: number; true_peak: number; mp3_true_peak: number; mp3_peak_warning: boolean};
  type Preflight = {tracks:number; existing:string[]; cover:string|null; can_update_tags:boolean; will_replace:boolean};
  let locations = $state<{name:string;available:boolean;writable:boolean}[]>([]);
  let presets = $state<Preset[]>([]);
  let artists = $state<string[]>([]), albums = $state<string[]>([]);
  let library = $state<Record<string,string[]>>({});
  let location = $state(''), artist = $state(''), album = $state(''), track = $state('');
  let source = $state('raw');
  let preset = $state(''), presetBase = $state(''), bands = $state<Band[]>(defaultBands());
  const reverbControls = [
    {toggle:'enable_side_reduction', key:'side_reduction_pct', title:'Side reduction', min:0,max:40,step:1,unit:'%'},
    {toggle:'enable_mid_suppress',key:'mid_suppress_db',title:'Mid suppression',min:-6,max:0,step:.1,unit:'dB'},
    {toggle:'enable_tail_gate',key:'gate_threshold_db',title:'Tail gate threshold',min:-55,max:-25,step:1,unit:'dB'},
    {toggle:'enable_low_cut',key:'low_cut_hz',title:'Low cut',min:60,max:220,step:1,unit:'Hz'},
    {toggle:'enable_high_cut',key:'high_cut_hz',title:'High cut',min:6000,max:14000,step:100,unit:'Hz'},
  ];
  const reverbDefaults = {enable_side_reduction:true,side_reduction_pct:25,enable_mid_suppress:true,
    mid_suppress_db:-2,enable_tail_gate:false,gate_threshold_db:-38,gate_ratio:2.5,
    enable_low_cut:false,low_cut_hz:120,enable_high_cut:false,high_cut_hz:9500};
  let corrections = $state<Record<string, Correction>>(Object.fromEntries(treatments.map(tool => [
    tool.id, {enabled:false, value:tool.value, ...(tool.id === 'ai_reverb_reduce'
      ? {options:{...reverbDefaults}}
      : {})}
  ])));
  let balance = $state('album'), lufs = $state(-16), peak = $state(-1.5);
  let artistTag = $state(''), albumArtist = $state(''), albumTag = $state(''), cover = $state('');
  let year = $state(new Date().getFullYear()), title = $state(''), number = $state('');
  let lyrics = $state(''), prompt = $state(''), style = $state('');
  let replace = $state(false), sidecar = $state(false), auditionFormat = $state<"wav"|"mp3">("wav");
  let previewURL = $state(''), previewKey = $state(''), previewBusy = $state(false);
  let audition = $state(false), player = $state<HTMLAudioElement>();
  let status = $state(''), error = $state(''), busy = $state(false);
  let results = $state<Result[]>([]);
  let exportReview = $state<{key:string; check:Preflight; tagsOnly:boolean}>();
  let cleanupEnabled = $state(false), eqEnabled = $state(false), noiseEnabled = $state(false);
  let voiceControls = $state({
    deesser:{enabled:false,freq_hz:6500,amount_db:0},
    vocal_smooth:{enabled:false,freq_hz:4500,amount_db:0},
    deharsh:{enabled:false,freq_hz:3200,amount_db:0}
  });
  const defaultNoise = {f_low:100, f_high:500, band_depth_db:18, afftdn_strength:.35, hp_hz:0, lp_hz:0,
    t0_sec:0,t1_sec:0,apply_scope:'global'};
  let noise = $state({...defaultNoise});
  let noisePresets = $state<{id:string;origin:string;meta:{title:string};noise:Partial<typeof defaultNoise>}[]>([]);
  let noiseURL = $state(''), noiseMode = $state(''), noiseBusy = $state(false);
  let sourceDuration = $state(0);
  let compactPlayer = $state(false);
  let selection = $state<{x:number;y:number;endX:number;endY:number}>();
  let dragStart: {x:number;y:number}|undefined;
  let draggingNoise = $state(false);
  let specScale = $state('log'), specRange = $state(120);
  const frequencyAt = (y:number) => Math.round(specScale === 'log' ? 20 * 1000 ** (1-y) : 20 + 19980 * (1-y));
  const frequencyY = (hz:number) => 1 - (specScale === 'log' ? Math.log(hz/20)/Math.log(1000) : (hz-20)/19980);
  let visibleSelection = $derived(draggingNoise ? selection : sourceDuration > 0 && noise.t1_sec > noise.t0_sec ? {
    x:noise.t0_sec/sourceDuration, endX:Math.min(1,noise.t1_sec/sourceDuration),
    y:frequencyY(noise.f_high), endY:frequencyY(noise.f_low),
  } : undefined);
  let engine = $state<ReturnType<typeof monitor>>();
  let monitorMedia:HTMLAudioElement | undefined;
  let spectrum = $state(''), cleanupCurve = $state(''), eqCurve = $state(''), scopePath = $state('');
  let samplePeak = $state(-120), clipped = $state(false), original = $state(false);
  let animation = 0;
  let selectionVersion = 0;
  let findings = $state<{summary:string;suggested_tool_id:string;suggested_value?:number;recommended_settings?:Record<string,number|boolean>}[]>([]);
  let writable = $derived(locations.find(item => item.name === location)?.writable);
  let tagEdits = $state<Record<string,{title:string;number:string}>>({});
  let keys = $derived({location, artist_folder: artist, album_folder: album, source});
  let tracks = $derived(library[source] || []);
  let processing = $derived({
    preset:presetBase, bands, voicing_enabled:eqEnabled, voice_controls:voiceControls,
    cleanup_enabled:cleanupEnabled, corrections, noise_enabled:noiseEnabled, noise,
    strength, stereo_width:width, guardrails
  });
  type Processing = typeof processing;
  type Draft = {defaults:Processing; overrides:Record<string,Processing>; tags:typeof tagEdits;
    albumSettings:typeof albumSettings; track:string};
  let defaults = $state<Processing>();
  let overrides = $state<Record<string,Processing>>({});
  let inherited = $state('');
  let readyDraftKey = $state('');
  let draftKey = $derived('sonustemper:album:' + JSON.stringify([location, artist, album, source]));
  let albumSettings = $derived({balance, lufs, peak, artist:artistTag, album_artist:albumArtist,
    album:albumTag, cover, year, replace, sidecar, profile, auditionFormat});
  function copy<T>(value:T):T {return JSON.parse(JSON.stringify(value));}
  const initialProcessing = {preset:'', bands:defaultBands(), voicing_enabled:false,
    voice_controls:{}, cleanup_enabled:false, corrections:{}, noise_enabled:false,
    noise:defaultNoise, strength:100, stereo_width:1, guardrails:true} as Processing;
  let trackCustom = $derived(Boolean(track && inherited && JSON.stringify(processing) !== inherited));
  let effectiveOverrides = $derived.by(() => {
    const saved = {...overrides};
    if (trackCustom) saved[track] = processing;
    else delete saved[track];
    return saved;
  });
  let draft = $derived({defaults:defaults ?? initialProcessing, overrides:effectiveOverrides,
    tags:{...tagEdits,[track]:{title,number}}, albumSettings, track});
  let settings = $derived({...keys, ...draft.defaults, tracks, ...albumSettings,
    tags:draft.tags, track_overrides:effectiveOverrides});
  let previewSettings = $derived({...settings,audition_track:track,audition_format:auditionFormat});
  let exportKey = $derived(JSON.stringify(settings));
  let currentKey = $derived(JSON.stringify(previewSettings));
  let canAudition = $derived(Boolean(previewURL && previewKey === currentKey));
  let audioURL = $derived(track ? '/api/albums/audio?' + new URLSearchParams({...keys, track}) : '');

  let playbackURL = $derived(noiseMode ? noiseURL : audition ? previewURL : audioURL);

  $effect(() => {
    if (track && readyDraftKey === draftKey) {
      try { localStorage.setItem(draftKey, JSON.stringify(draft)); }
      catch { /* Save album session remains available when browser storage is full. */ }
    }
  });

  $effect(() => {
    const container = waveform, media = player, url = playbackURL;
    const WaveSurfer = (window as unknown as {WaveSurfer?: {create(options: object): {
      on(event: string, callback: () => void): void; destroy(): void
    }}}).WaveSurfer;
    if (!container || !media || !url || !WaveSurfer) return;
    waveError = false;
    const wave = WaveSurfer.create({container, media, url, height:240, normalize:false,
      waveColor:'#4c6451', progressColor:'#b3d68f', cursorColor:'#e8f2d7',
      barWidth:2, barGap:1, dragToSeek:true, autoScroll:false});
    wave.on('error', () => {waveError = true;});
    return () => wave.destroy();
  });

  function selectProfile() {
    if (profile === 'guiding-night') { lufs = -16; peak = -1.5; return; }
    const selected = profiles.find(item => item.origin + '/' + item.name === profile);
    if (selected && selected.meta.lufs != null && selected.meta.tp != null) {
      lufs = selected.meta.lufs;
      peak = Math.min(0, selected.meta.tp);
    }
  }

  async function api<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
    const result = await fetch('/api/albums/' + path, body === undefined ? {cache:'no-store'} : {
      method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    });
    if (!result.ok) {
      const payload = await result.json().catch(() => ({}));
      throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Request failed (' + result.status + ')');
    }
    return result.json();
  }

  async function action(run: () => Promise<void>) {
    error = '';
    try { await run(); } catch (cause) { error = String(cause instanceof Error ? cause.message : cause); }
  }

  async function selectLocation() {
    const version = ++selectionVersion;
    readyDraftKey = '';
    artist = album = track = ''; artists = []; albums = []; library = {};
    const found = location ? await api<string[]>('browse?' + new URLSearchParams({location})) : [];
    if (version === selectionVersion) artists = found;
  }

  async function selectArtist() {
    const version = ++selectionVersion;
    readyDraftKey = '';
    album = track = ''; albums = []; library = {};
    const found = artist ? await api<string[]>('browse?' + new URLSearchParams({location, artist_folder: artist})) : [];
    if (version === selectionVersion) albums = found;
  }

  function loadProcessing(value:Processing) {
    const draft = copy(value);
    presetBase = draft.preset;
    bands = draft.bands;
    const presetBands = presets.find(item=>item.id===draft.preset)?.bands;
    preset = presetBands && JSON.stringify(presetBands.map(item=>({...item,gain_db:item.gain_db ?? 0,q:item.q ?? .7}))) === JSON.stringify(bands) ? draft.preset : '';
    eqEnabled = draft.voicing_enabled;
    voiceControls = {
      deesser:{enabled:false,freq_hz:6500,amount_db:0,...draft.voice_controls.deesser},
      vocal_smooth:{enabled:false,freq_hz:4500,amount_db:0,...draft.voice_controls.vocal_smooth},
      deharsh:{enabled:false,freq_hz:3200,amount_db:0,...draft.voice_controls.deharsh},
    };
    corrections = Object.fromEntries(treatments.map(tool => [tool.id, {
      enabled:false, value:tool.value, ...draft.corrections[tool.id],
      ...(tool.id === 'ai_reverb_reduce' ? {options:{...reverbDefaults,...draft.corrections[tool.id]?.options}} : {}),
    }]));
    cleanupEnabled = draft.cleanup_enabled ?? Object.keys(draft.corrections).length > 0;
    noiseEnabled = draft.noise_enabled ?? Boolean(draft.noise);
    noise = {...defaultNoise,...draft.noise};
    strength = draft.strength; guardrails = draft.guardrails;
    width = draft.stereo_width ?? activePreset?.width ?? 1;
  }

  function rememberTrack() {
    if (!track) return;
    tagEdits[track] = {title,number};
    if (trackCustom) overrides[track] = copy(processing);
    else delete overrides[track];
  }

  function applyToAlbum() {
    const otherEdits = tracks.some(item => item !== track && overrides[item]);
    if (otherEdits && !confirm('Replace the other tracks’ custom processing with these settings?')) return;
    const current = copy(processing);
    const trackOnlyNoise = current.noise_enabled && current.noise.apply_scope === 'selection';
    defaults = trackOnlyNoise ? {...current, noise_enabled:false, noise:{...defaultNoise}} : current;
    overrides = trackOnlyNoise ? {[track]:current} : {};
    loadTrackProcessing();
    status = trackOnlyNoise ? 'Applied to album. The selected noise range stays on this track.' : 'Processing applied to all tracks';
  }

  function loadTrackProcessing() {
    loadProcessing(defaults ?? initialProcessing);
    inherited = JSON.stringify(processing);
    if (overrides[track]) loadProcessing(overrides[track]);
  }

  async function saveSession() {
    await api('session',{...keys,draft},'PUT');
    status = 'Album session saved';
  }

  async function reopenSession() {
    const version = selectionVersion;
    let saved: Draft | null = null;
    try { saved = JSON.parse(localStorage.getItem(draftKey) ?? 'null'); } catch { /* Fall back to server save. */ }
    if (!saved) ({draft:saved} = await api<{draft:Draft|null}>('session?'+new URLSearchParams(keys)));
    if (version !== selectionVersion || !saved) return;
    defaults=saved.defaults;
    overrides=saved.overrides;
    tagEdits=saved.tags;
    const a=saved.albumSettings;
    ({balance,lufs,peak,year,replace,sidecar,profile,auditionFormat}=a);
    cover=library.covers.includes(a.cover) ? a.cover : '';
    artistTag=a.artist;albumArtist=a.album_artist;albumTag=a.album;
    if (tracks.includes(saved.track)) track=saved.track;
    await selectTrack();
    status='Album session reopened';
  }

  async function selectAlbum() {
    const version = ++selectionVersion;
    readyDraftKey = '';
    library = {}; track = ''; results = [];
    if (!album) return;
    const found = await api<Record<string,string[]>>('browse?' + new URLSearchParams(keys));
    if (version !== selectionVersion) return;
    library = found;
    source = found.raw.length ? 'raw' : 'mastered';
    artistTag = albumArtist = artist; albumTag = album;
    cover = found.covers.includes('cover.png') ? 'cover.png' : '';
    await selectStage();
  }

  async function refreshLibrary() {
    const version = selectionVersion;
    const foundLocations = await api<typeof locations>('locations');
    if (version !== selectionVersion) return;
    locations = foundLocations;
    if (!location) return;
    if (!artist) return selectLocation();
    if (!album) return selectArtist();
    const found = await api<Record<string,string[]>>('browse?' + new URLSearchParams(keys));
    if (version !== selectionVersion) return;
    library = found;
    if (!found.covers.includes(cover)) cover = '';
    if (!tracks.includes(track)) {
      if (!tracks.length && found[source === 'raw' ? 'mastered' : 'raw'].length) {
        source = source === 'raw' ? 'mastered' : 'raw';
        await selectStage();
      } else {
        rememberTrack();
        track = tracks[0] || '';
        await selectTrack();
      }
    }
    status = 'Library refreshed from storage';
  }

  async function selectStage() {
    readyDraftKey=''; defaults=undefined; overrides={}; tagEdits={};
    balance='album'; profile='guiding-night'; selectProfile();
    year=new Date().getFullYear(); replace=false; sidecar=false; auditionFormat='wav';
    artistTag=albumArtist=artist; albumTag=album;
    cover=library.covers?.includes('cover.png') ? 'cover.png' : '';
    status=''; results=[];
    track = (library[source] || [])[0] || '';
    const ready = selectTrack();
    const version = selectionVersion;
    await ready.catch(cause => { if (version === selectionVersion) error = String(cause); });
    const key = draftKey;
    if (track && version === selectionVersion) {
      try { await reopenSession(); }
      finally { if (key === draftKey) readyDraftKey=key; }
    }
  }

  async function selectTrack() {
    const version = ++selectionVersion;
    removeSeekListener?.();
    noiseMode=''; sourceDuration=0; selection=undefined; audition = false; original = false; clipped = false; scopePath = ''; lyrics = prompt = style = ''; findings = [];
    loadTrackProcessing();
    const stem = track.replace(/\.wav$/i, '');
    const match = stem.match(/^(\d+)\.\s*(.*)$/);
    title = tagEdits[track]?.title ?? match?.[2] ?? stem;
    number = tagEdits[track]?.number ?? match?.[1] ?? '';
    if (!track) return;
    const data = await api<{lyrics:string;prompt:string;style:string}>('text?' + new URLSearchParams({...keys, track}));
    if (version === selectionVersion) ({lyrics, prompt, style} = data);
  }

  function loadPreset() {
    const selected = presets.find(item => item.id === preset);
    presetBase = preset;
    eqEnabled = true;
    listenLive();
    strength = 100;
    width = selected?.width ?? 1;
    bands = (selected?.bands ?? defaultBands()).map(band => ({...band, gain_db:band.gain_db ?? 0, q:band.q ?? .7}));
  }

  function markCustom(reset = false) {
    eqEnabled = true;
    listenLive();
    if (preset && !reset && strength !== 100) {
      for (const band of bands) band.gain_db *= strength / 100;
      strength = 100;
    }
    preset = '';
    if (reset) {
      presetBase = '';
      strength = 100;
      width = 1;
    }
  }

  const band = (type:string, freq_hz:number, gain_db=0, q=.7):Band => ({type,freq_hz,gain_db,q});
  let cleanupBands = $derived.by(() => {
    const result:Band[] = [];
    if (!cleanupEnabled) return result;
    for (const [id, config] of Object.entries(corrections)) {
      if (!config.enabled) continue;
      const value = config.value;
      if (id==='ai_deglass') {
        result.push(band('highshelf',11000,value));
        if (value<=-2) result.push(band('lowpass',20000-(Math.min(6,Math.abs(value))-2)*1500));
      }
      if (id==='ai_vocal_smooth') {
        result.push(band('peaking',4500,value,1.2));
        if (value<=-3) result.push(band('peaking',7500,-.5-1.5*Math.min(1,(Math.abs(value)-3)/3),2));
      }
      if (id==='ai_bass_tight' && value<0) result.push(band('highpass',Math.min(80,30+Math.abs(value)/6*50)),band('peaking',220,value/2,1));
      if (id==='ai_bass_tight' && value>0) result.push(band('highpass',25+value/6*10),band('lowshelf',90,value/2));
      if (id==='ai_transient_soften' && value>0) result.push(band('peaking',3200,1.5*Math.min(4,value)/4,1));
      if (id==='ai_transient_soften' && value<0) {
        const depth=Math.min(6,Math.abs(value))/6;
        result.push(band('peaking',3200,-.3-1.2*depth,1),band('highshelf',8000,-.3-1.5*depth));
      }
      if (id==='ai_reverb_reduce' && config.options) {
        const options=config.options;
        if (options.enable_mid_suppress) result.push(band('peaking',Number(options.mid_freq_hz ?? 1800),Number(options.mid_suppress_db ?? -2),.8));
        if (options.enable_low_cut) result.push(band('highpass',Number(options.low_cut_hz ?? 120)));
        if (options.enable_high_cut) result.push(band('lowpass',Number(options.high_cut_hz ?? 9500)));
      }
    }
    return result;
  });
  let voiceBands = $derived(Object.entries(voiceControls).filter(([,control]) => control.enabled)
    .map(([name,control]) => band('peaking', control.freq_hz, control.amount_db, name==='deesser'?2:name==='deharsh'?1.5:1.2)));
  let liveBands = $derived(source==='raw' && !original && !audition && !noiseMode ? [
    ...cleanupBands,
    ...(eqEnabled ? [...voiceBands,...bands.map(item=>({...item,gain_db:item.gain_db*strength/100}))] : []),
  ] : []);

  let offlineCurveContext: OfflineAudioContext | undefined;

  function drawCurve(context:BaseAudioContext, filters:Band[]) {
    const frequencies = Float32Array.from({length:160}, (_,i)=>Math.min(context.sampleRate/2-.01,20*1000**(i/159)));
    const db = new Float32Array(frequencies.length);
    for (const item of filters.filter(item => item.enabled!==false && Number.isFinite(item.freq_hz))) {
      const magnitude = new Float32Array(frequencies.length);
      filterNode(context,item).getFrequencyResponse(frequencies,magnitude,new Float32Array(frequencies.length));
      magnitude.forEach((value,i)=>db[i]+=20*Math.log10(Math.max(.00001,value)));
    }
    return Array.from(db,(value,i)=>(i?'L':'M')+(i*600/159)+' '+Math.max(0,Math.min(150,75-value*5))).join(' ');
  }

  $effect(() => {
    const audio = engine;
    const valid = liveBands.filter(item => Number.isFinite(item.freq_hz) && Number.isFinite(item.gain_db) && Number.isFinite(item.q));
    const widthValue = source==='raw' && !original && !audition && !noiseMode && eqEnabled
      ? (Number.isFinite(width) ? width : 1) : 1;
    audio?.update(valid,widthValue);
    const curveContext = audio?.context ?? (offlineCurveContext ??= new OfflineAudioContext(1, 1, 48000));
    cleanupCurve = drawCurve(curveContext,cleanupBands);
    eqCurve = drawCurve(curveContext,[
      ...voiceBands,...bands.map(item=>({...item,gain_db:item.gain_db*strength/100}))
    ]);
  });

  function startMonitoring() {
    if (!player) return;
    if (monitorMedia !== player) {engine?.close(); engine=monitor(player);monitorMedia=player;}
    void engine?.context.resume();
    cancelAnimationFrame(animation);
    const audio = engine;
    if (!audio) return;
    const bins = new Float32Array(audio.analyser.frequencyBinCount);
    const samples = new Float32Array(audio.analyser.fftSize);
    const frame = () => {
      audio.analyser.getFloatFrequencyData(bins);
      audio.analyser.getFloatTimeDomainData(samples);
      spectrum=spectrumPath(bins,audio.context.sampleRate);
      let max=0;
      for (const value of samples) max=Math.max(max,Math.abs(value));
      samplePeak=20*Math.log10(Math.max(.000001,max));
      clipped ||= max>=1;
      scopePath=Array.from({length:180},(_,i)=>(i?'L':'M')+(i*600/179)+' '+(50-Math.max(-1,Math.min(1,samples[Math.floor(i*samples.length/180)]))*48)).join(' ');
      animation=requestAnimationFrame(frame);
    };
    frame();
  }
  async function renderPreview() {
    if (previewBusy) return;
    previewBusy = true;
    const signature = currentKey;
    status = 'Rendering export audition…';
    try {
      const result = await fetch('/api/albums/preview', {method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(previewSettings)});
      if (!result.ok) throw new Error((await result.json()).detail || 'Preview failed');
      const blob = await result.blob();
      if (signature !== currentKey) return;
      const previous = previewURL;
      const next = URL.createObjectURL(blob);
      if (audition && !noiseMode) restorePlayhead(next);
      previewURL = next;
      if (previous) URL.revokeObjectURL(previous);
      previewKey = signature;
      status = 'Preview ready';
    } finally {
      previewBusy = false;
      if (status.startsWith('Rendering export')) status = '';
    }
  }

  let removeSeekListener: (() => void) | undefined;

  function restorePlayhead(url: string, seekTo?: number, play?: boolean) {
    removeSeekListener?.();
    if (!player) return;
    const element = player;
    const wasPlaying = play ?? !element.paused;
    const position = seekTo ?? element.currentTime;
    function restore() {
      if (element.currentSrc !== new URL(url, document.baseURI).href) return;
      element.removeEventListener('loadedmetadata', restore);
      removeSeekListener = undefined;
      element.currentTime = Math.min(position, Math.max(0, element.duration - .05));
      if (wasPlaying) void element.play().catch(() => { status='Audio ready. Press play to listen.'; });
    }
    element.addEventListener('loadedmetadata', restore);
    removeSeekListener = () => element.removeEventListener('loadedmetadata', restore);
  }

  function setPlayback(mode: 'original' | 'live' | 'export') {
    if (mode === 'export' && !canAudition) return;
    const useRender = mode === 'export';
    if (noiseMode || audition !== useRender) restorePlayhead(useRender ? previewURL : audioURL);
    noiseMode = '';
    original = mode === 'original';
    audition = useRender;
  }

  function listenLive() {
    if (source === 'raw') setPlayback('live');
  }

  function selectionPoint(event:PointerEvent) {
    const bounds=(event.currentTarget as HTMLElement).getBoundingClientRect();
    return {x:Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width)),
      y:Math.max(0,Math.min(1,(event.clientY-bounds.top)/bounds.height))};
  }
  function drawNoise(event:PointerEvent) {
    if (!dragStart) return;
    const point=selectionPoint(event);
    selection={x:Math.min(dragStart.x,point.x),y:Math.min(dragStart.y,point.y),
      endX:Math.max(dragStart.x,point.x),endY:Math.max(dragStart.y,point.y)};
  }
  function finishNoise(event:PointerEvent) {
    drawNoise(event);
    dragStart=undefined;draggingNoise=false;
    if (!selection || !player || selection.endX-selection.x < .002 || selection.endY-selection.y < .002) {
      selection=undefined;return;
    }
    noise.f_low=frequencyAt(selection.endY);noise.f_high=frequencyAt(selection.y);
    noise.t0_sec=selection.x*sourceDuration;noise.t1_sec=selection.endX*sourceDuration;
    noise.apply_scope='selection';noiseEnabled=true;
    if (noiseMode) listenLive();
  }
  async function auditionNoise(mode:'remove'|'solo') {
    const version=selectionVersion;
    const payload=JSON.stringify({...keys,...processing,noise,tracks:[track]});
    noiseBusy=true;
    try {
      const response=await fetch('/api/albums/noise-audition?mode='+mode,{method:'POST',
        headers:{'Content-Type':'application/json'},body:payload});
      if (!response.ok) throw new Error((await response.json()).detail);
      const blob=await response.blob();
      if (version!==selectionVersion || !player || payload!==JSON.stringify({...keys,...processing,noise,tracks:[track]})) return;
      const next=URL.createObjectURL(blob);
      restorePlayhead(next, noise.apply_scope==='selection' ? noise.t0_sec : undefined, true);
      if (noiseURL) URL.revokeObjectURL(noiseURL);
      noiseURL=next;
      noiseMode=mode;
      status=mode==='solo'?'Solo noise audition':'Filtered track audition';
    } finally {noiseBusy=false;}
  }

  async function reviewExport(tagsOnly = false) {
    const key = exportKey;
    const check = await api<Preflight>('preflight', {...settings, metadata_only:tagsOnly});
    if (key === exportKey) exportReview = {key, check, tagsOnly};
  }

  async function run() {
    if (!exportReview || exportReview.key !== exportKey) return;
    const request = {...settings, metadata_only:exportReview.tagsOnly};
    const {location, artist_folder, album_folder} = request;
    exportReview = undefined;
    busy = true; results = [];
    try {
      const {id} = await api<{id:string}>('jobs', request);
      for (;;) {
        const job = await api<{status:string;stage:string;track?:string;error?:string;results?:Result[]}>('jobs/' + id);
        const sameAlbum = keys.location===location && keys.artist_folder===artist_folder && keys.album_folder===album_folder;
        if (sameAlbum) status = job.stage + (job.track ? ' · ' + job.track : '');
        if (job.status === 'failed') { if (sameAlbum) status = 'Export failed'; throw new Error(job.error); }
        if (job.status === 'complete') {
          if (sameAlbum) {
            results = job.results || [];
            await refreshLibrary();
            status = request.metadata_only ? 'Tags updated' : 'Export complete';
          }
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 800));
      }
    } finally { busy = false; }
  }

  onMount(() => {
    void action(async () => {
      [locations, presets] = await Promise.all([api('locations'), api('presets')]);
      const catalog = await fetch('/api/profiles', {cache:'no-store'});
      if (!catalog.ok) throw new Error('Could not load loudness profiles');
      profiles = (await catalog.json()).items.filter((item: LoudnessProfile) =>
        Number.isFinite(item.meta.lufs) && Number.isFinite(item.meta.tp));
      const catalogNoise=await fetch('/api/library/noise_filters?origin=all');
      if (catalogNoise.ok) noisePresets=(await catalogNoise.json()).items.filter((item:{noise:unknown})=>item.noise);
      location = locations.find(item => item.available)?.name || '';
      await selectLocation();
    });
    return () => { removeSeekListener?.(); if (previewURL) URL.revokeObjectURL(previewURL); if (noiseURL) URL.revokeObjectURL(noiseURL); cancelAnimationFrame(animation); engine?.close(); };
  });
</script>

<header><a href="/">SonusTemper</a><span>ALBUM STUDIO</span></header>
<main>
  <aside>
    <div class="library-head"><h2>Production library</h2><button onclick={() => action(refreshLibrary)}>Refresh</button></div>
    <label>Storage<select bind:value={location} onchange={() => action(selectLocation)}><option value="">Choose storage</option>{#each locations as item}<option value={item.name} disabled={!item.available}>{item.name}{item.available ? '' : ' · offline'}</option>{/each}</select></label>
    <label>Artist<select bind:value={artist} onchange={() => action(selectArtist)}><option value="">Choose artist</option>{#each artists as item}<option>{item}</option>{/each}</select></label>
    <label>Album<select bind:value={album} onchange={() => action(selectAlbum)}><option value="">Choose album</option>{#each albums as item}<option>{item}</option>{/each}</select></label>
    {#if album}
      <label>Start from<select bind:value={source} onchange={() => action(selectStage)}><option value="raw">Raw WAVs</option><option value="mastered">Existing masters</option></select></label>
      <div class="tracks" aria-label="Tracks">
        {#each tracks as item}
          <button class:active={item === track} aria-pressed={item === track} onclick={() => {if (track !== item) {rememberTrack(); track = item; void action(selectTrack);}}}>{item.replace(/\.wav$/i, '')}{(item === track ? trackCustom : overrides[item]) ? ' · Custom' : ''}</button>
        {:else}<p>No WAVs in this stage.</p>{/each}
      </div>
    {/if}
  </aside>

  <section class="workspace" aria-label="Album workflow">
    <div class="transport" class:compact={compactPlayer}>
      <div><small>{artist || 'Production workspace'}</small><h1>{album || 'Album Studio'}</h1><p>{track ? track.replace(/\.wav$/i, '') : 'Choose an artist and album to begin'}</p></div>
      {#if track}
        <div class="wave-stack">
          <div class="waveform" bind:this={waveform} aria-label="Track waveform"></div>
          <svg class="scope" viewBox="0 0 600 100" preserveAspectRatio="none" aria-label="Live output waveform" role="img"><rect class:hot={clipped} x="0" y="0" width="600" height="9"/><rect class:hot={clipped} x="0" y="91" width="600" height="9"/>
            <text x="8" y="7">CLIP</text><path d={scopePath}/></svg>
        </div>
        {#if waveError}<small>Waveform unavailable. Playback controls are available below.</small>{/if}
        <audio controls preload="metadata" bind:this={player} onloadedmetadata={() => {if (!audition && !noiseMode && player) sourceDuration=player.duration;}} onplay={startMonitoring} onpause={() => cancelAnimationFrame(animation)} src={playbackURL}></audio>
        <div class="row">
          <button aria-pressed={compactPlayer} onclick={() => compactPlayer=!compactPlayer}>{compactPlayer ? "Expand player" : "Compact player"}</button>
          <button aria-pressed={!noiseMode && (source === 'mastered' || original) && !audition} onclick={() => setPlayback('original')}>Original</button>
          <button aria-pressed={!noiseMode && !audition && !original && source === 'raw'} disabled={source !== 'raw'} onclick={listenLive}>Live monitor</button>
          <button disabled={previewBusy} onclick={() => action(renderPreview)}>{previewBusy ? 'Rendering audition…' : 'Render export audition'}</button>
          <button class:clip={clipped} onclick={() => clipped=false}>{clipped?'CLIP · reset':'Peak '+samplePeak.toFixed(1)+' dBFS'}</button>
          <button aria-pressed={!noiseMode && audition} disabled={!canAudition} onclick={() => setPlayback('export')}>Hear export</button>
          {#if noiseMode}<small>{noiseMode==='solo'?'Solo noise':'Filtered track'} audition</small>{/if}
          {#if audition && !canAudition}<small>Previous export audition · render again for current settings</small>{/if}
          <label>Audition format<select bind:value={auditionFormat}><option value="wav">Delivery WAV</option><option value="mp3">320 kbps MP3</option></select></label>
        </div>
      {/if}
    </div>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if track}
      <div class="row session-controls">
        <strong>{trackCustom ? 'Custom track processing' : 'Using album processing'}</strong>
        {#if source === 'raw'}
          <button onclick={applyToAlbum}>Apply processing to all tracks</button>
          <button disabled={!trackCustom} onclick={() => {delete overrides[track];loadTrackProcessing();}}>Use album processing for this track</button>
        {/if}
        <button onclick={() => action(saveSession)}>Save album session</button>
      </div>
      {#if source === 'raw'}
      <details><summary>AI cleanup <input class="stage-toggle" type="checkbox" aria-label="Enable AI cleanup" bind:checked={cleanupEnabled} onclick={(event)=>event.stopPropagation()} onchange={() => {if (cleanupEnabled) listenLive();}}></summary>
        <div class="cleanup-layout"><div><p>Track spectrum and effect curve</p>
        <svg class="effect" viewBox="0 0 600 150" preserveAspectRatio="none" aria-label="Track spectrum and cleanup effect curve" role="img"><path class="grid" d="M0 75H600"/><path class="spectrum" d={spectrum}/><path class="curve" d={cleanupCurve || 'M0 75H600'}/></svg>
        <div class="frequency-scale"><span>20 Hz</span><span>200 Hz</span><span>2 kHz</span><span>20 kHz</span></div>
        <small>Live monitor shows tonal adjustments. Hear export includes denoising, dynamics, reverb gating, and side reduction.</small></div><div>
        <button disabled={source !== 'raw'} onclick={() => action(async () => {
          const version = selectionVersion;
          const result = await api<{findings:typeof findings}>('analyze?' + new URLSearchParams({...keys, track}));
          if (version === selectionVersion) findings = result.findings;
        })}>Analyze track</button>
        {#each findings as finding}
          <p>{finding.summary}
          {#if finding.suggested_tool_id === 'ai_platform_safe'}
            <button onclick={() => {lufs=finding.suggested_value ?? -16;profile='manual';}}>Use suggested loudness target</button>
          {:else if corrections[finding.suggested_tool_id]}
            <button onclick={() => {cleanupEnabled=true; listenLive(); corrections[finding.suggested_tool_id] = {enabled:true, value:finding.suggested_value ?? corrections[finding.suggested_tool_id].value, options:finding.recommended_settings ?? corrections[finding.suggested_tool_id].options};}}>Apply suggestion</button>
          {/if}</p>
        {/each}
        <div class="treatments">
          {#each treatments as tool}
            <div class="treatment">
              <label class="check"><input type="checkbox" bind:checked={corrections[tool.id].enabled} onchange={() => {cleanupEnabled=true;listenLive();}} disabled={source !== 'raw'}> {tool.title}</label>
              {#if tool.id === 'ai_reverb_reduce'}
                {#each reverbControls as option}
                  {@const config = corrections[tool.id].options!}
                  <div class="treatment-option">
                    <label class="check"><input type="checkbox" checked={Boolean(config[option.toggle])} onchange={(event)=>config[option.toggle]=event.currentTarget.checked}> {option.title}</label>
                    <input aria-label={option.title} type="range" min={option.min} max={option.max} step={option.step}
                      value={Number(config[option.key])} disabled={!corrections[tool.id].enabled || !config[option.toggle]}
                      oninput={(event)=>config[option.key]=+event.currentTarget.value}>
                    <output>{config[option.key]} {option.unit}</output>
                  </div>
                {/each}
                <label>Gate ratio<input type="number" min="1.5" max="4" step=".1" bind:value={corrections[tool.id].options!.gate_ratio}></label>
              {:else}
                <label class="inline-control"><span>Amount</span><input aria-label={tool.title + ' amount'} type="range" min="-6" max="4" step=".1" bind:value={corrections[tool.id].value} oninput={listenLive} disabled={!corrections[tool.id].enabled}><output>{corrections[tool.id].value} dB</output></label>
              {/if}
            </div>
          {/each}
        </div>
        </div></div>
      </details>
      <details open><summary>Realtime EQ and stereo <input class="stage-toggle" type="checkbox" aria-label="Enable Realtime EQ and stereo" bind:checked={eqEnabled} onclick={(event)=>event.stopPropagation()} onchange={() => {if (eqEnabled) listenLive();}} disabled={source!=='raw'}></summary>
        {#snippet presetControls()}
          <label>EQ Preset<select bind:value={preset} onchange={loadPreset} disabled={source !== 'raw'}>
            <option value="">Custom</option>
            {#each presets as item}
              <option value={item.id}>{item.title}{item.id.startsWith('voicing/') ? ' · User' : ''}</option>
            {/each}
          </select></label>
          {#if preset}<label class="inline-control"><span>Strength</span><input aria-label="Preset strength" type="range" min="0" max="100" bind:value={strength} oninput={() => {eqEnabled=true;listenLive();}}><output>{strength}%</output></label>{/if}
        {/snippet}
        {#snippet stereoControls()}
          <strong>Stereo and dynamics</strong>
          <label class="inline-control"><span>Stereo width</span><input aria-label="Stereo width" type="range" min=".5" max="1.5" step=".01" bind:value={width} oninput={() => {eqEnabled=true;listenLive();}}><output>{width.toFixed(2)}</output></label>
          <label class="check"><input type="checkbox" bind:checked={guardrails}> Compression guardrails</label>
          {#if presetBase}<small>Preset compression is heard in the export audition.</small>{/if}
        {/snippet}
        <EqPanel bind:bands bind:voice={voiceControls} {presetControls} {stereoControls} onEdit={markCustom} onVoiceEdit={() => {eqEnabled=true;listenLive();}} {spectrum} curve={eqCurve || 'M0 75H600'} />
      </details>
      <details><summary>Noise removal <input class="stage-toggle" type="checkbox" aria-label="Enable Noise removal" bind:checked={noiseEnabled} onclick={(event)=>event.stopPropagation()} onchange={() => {if (noiseEnabled) listenLive();}} disabled={source!=='raw'}></summary>
        <div class="noise-grid">
          <div>
            <div class="spectrogram" role="region" aria-label="Drag to select a time and frequency region"
              onpointerdown={(event)=>{if (!(sourceDuration>0)) return; dragStart=selectionPoint(event);draggingNoise=true;event.currentTarget.setPointerCapture(event.pointerId);}}
              onpointermove={drawNoise} onpointerup={finishNoise} onpointercancel={()=>{dragStart=undefined;draggingNoise=false;selection=undefined;}}>
              <img loading="lazy" alt="Source track spectrogram" src={'/api/albums/spectrogram?'+new URLSearchParams({...keys,track,scale:specScale,range_db:String(specRange)})}>
              {#if visibleSelection}<div class="marquee" style:left={visibleSelection.x*100+'%'} style:top={visibleSelection.y*100+'%'}
                style:width={(visibleSelection.endX-visibleSelection.x)*100+'%'} style:height={(visibleSelection.endY-visibleSelection.y)*100+'%'}></div>{/if}
            </div>
            {#if noise.t1_sec>noise.t0_sec}<p>{noise.t0_sec.toFixed(1)}–{noise.t1_sec.toFixed(1)} s · {noise.f_low}–{noise.f_high} Hz</p>{/if}
            <small>Drag to select a time and frequency range. Hear filtered track renders the current noise settings.</small>
          </div>
          <div>
            <div class="row"><label>Scale<select bind:value={specScale}><option value="log">Log</option><option value="lin">Linear</option></select></label>
              <label>Display range<input type="number" min="40" max="160" bind:value={specRange}></label></div>
            <div class="row"><label>Low frequency (Hz)<input type="number" min="20" max="20000" bind:value={noise.f_low}></label>
              <label>High frequency (Hz)<input type="number" min="20" max="20000" bind:value={noise.f_high}></label></div>
            <label>Filter depth ({noise.band_depth_db} dB)<input type="range" min="0" max="30" bind:value={noise.band_depth_db}></label>
            <label>Denoise strength ({noise.afftdn_strength})<input type="range" min="0" max="1" step=".01" bind:value={noise.afftdn_strength}></label>
            <div class="row"><label>High-pass (Hz, 0 off)<input type="number" min="0" max="20000" bind:value={noise.hp_hz}></label>
              <label>Low-pass (Hz, 0 off)<input type="number" min="0" max="20000" bind:value={noise.lp_hz}></label></div>
            <label>Noise preset<select aria-label="Noise preset" onchange={(event)=>{
              const item=noisePresets.find(preset=>preset.origin+'/'+preset.id===event.currentTarget.value);
              if(item){noise={...noise,...Object.fromEntries(Object.entries(item.noise).filter(([key,value])=>key in defaultNoise && typeof value==='number'))};noiseEnabled=true;listenLive();}
            }}><option value="">Manual</option>{#each noisePresets as item}<option value={item.origin+'/'+item.id}>{item.meta.title}</option>{/each}</select></label>
            <label>Apply to<select bind:value={noise.apply_scope}><option value="global">Entire track</option><option value="selection" disabled={noise.t1_sec<=noise.t0_sec}>Selected time range</option></select></label>
            <div class="row">
              <button disabled={!noiseEnabled || noiseBusy || draggingNoise || source!=='raw'} onclick={()=>action(()=>auditionNoise('solo'))}>Solo noise</button>
              <button disabled={!noiseEnabled || noiseBusy || draggingNoise || source!=='raw'} onclick={()=>action(()=>auditionNoise('remove'))}>Hear filtered track</button>
              <button onclick={()=>{selection=undefined;noise.t0_sec=0;noise.t1_sec=0;noise.apply_scope='global';listenLive();}}>Clear selection</button>
            </div>
          </div>
        </div>
      </details>
      {/if}
      <details><summary>Album loudness</summary>
        <div class="row">
          <label>Profile<select bind:value={profile} onchange={selectProfile}>
            <option value="guiding-night">Guiding Night</option><option value="manual">Manual</option>
            {#each ['user', 'builtin'] as origin}
              <optgroup label={origin === 'user' ? 'User profiles' : 'Built-in profiles'}>
                {#each profiles.filter(item => item.origin === origin) as item}
                  <option value={item.origin + '/' + item.name}>{item.meta.title}</option>
                {/each}
              </optgroup>
            {/each}
          </select></label>
          <label>Apply gain<select bind:value={balance}><option value="album">Preserve album balance</option><option value="track">Normalize each track</option></select></label>
          <label>Target LUFS<input type="number" min="-70" max="-5" step=".1" bind:value={lufs} oninput={() => profile = 'manual'}></label>
          <label>Peak ceiling<input type="number" min="-9" max="0" step=".1" bind:value={peak} oninput={() => profile = 'manual'}></label>
        </div>
        {#if profiles.some(item => item.origin + '/' + item.name === profile && (item.meta.tp ?? 0) > 0)}
          <p>This profile requests a positive true peak. WAV export caps the ceiling at 0 dBTP.</p>
        {/if}
        <p>Static gain respects the WAV peak ceiling. The results report decoded MP3 peaks separately.</p>
      </details>
      <details><summary>Tags and artwork</summary>
        {#if cover}<img class="cover" alt="Selected album cover" src={'/api/albums/artwork?'+new URLSearchParams({...keys,name:cover})}>{/if}
        <div class="row"><label>Artist<input bind:value={artistTag}></label><label>Album artist<input bind:value={albumArtist}></label><label>Album<input bind:value={albumTag}></label></div>
        <div class="row"><label>Track title<input bind:value={title}></label><label>Track number<input bind:value={number}></label><label>Year<input type="number" bind:value={year}></label><label>Cover<select bind:value={cover}><option value="">No artwork</option>{#each library.covers || [] as item}<option>{item}</option>{/each}</select></label></div>
      </details>
      <details><summary>Lyrics and Suno prompts</summary>
        <label>Approved lyrics<textarea rows="9" bind:value={lyrics}></textarea></label>
        <div class="row"><button disabled={!writable} onclick={() => action(async () => {await api('lyrics', {...keys,track,text:lyrics}, 'PUT');status='Lyrics saved';})}>Save lyrics</button>
        <button onclick={() => action(async () => {await navigator.clipboard.writeText(lyrics);status='Lyrics copied';})}>Copy submission text</button></div>
        <label>Suno lyric prompt<textarea rows="5" bind:value={prompt}></textarea></label>
        <div class="row"><button onclick={() => action(async () => {await navigator.clipboard.writeText(prompt);status='Prompt copied';})}>Copy prompt</button>
        <button onclick={() => action(async () => {if (lyrics && !window.confirm('Replace the unsaved lyric text with a draft?')) return; lyrics = (await api<{draft:string}>('lyrics/draft', {text:prompt})).draft;status='Draft ready for listening review';})}>Draft lyrics from prompt</button></div>
        <label>Suno style<textarea rows="4" bind:value={style}></textarea></label>
        <button onclick={() => action(async () => {await navigator.clipboard.writeText(style);status='Style copied';})}>Copy style</button>
      </details>
      <details open><summary>Export</summary>
        <p>{source === 'raw' ? 'mastered / ' : ''}clean / normalized / mp3s</p>
        <div class="row"><strong>All {tracks.length} tracks</strong>
        <label class="check"><input type="checkbox" bind:checked={replace}> Replace previous outputs</label>
        <label class="check"><input type="checkbox" bind:checked={sidecar}> Include untimed LRC</label></div>
        {#if !writable}<p>Choose the writable test copy to export.</p>{/if}
        <div class="row">
          <button class="primary" disabled={busy || !writable} onclick={() => action(() => reviewExport())}>{busy ? 'Export in progress…' : 'Review export'}</button>
          <button disabled={busy || !writable} onclick={() => action(() => reviewExport(true))}>Review tag update</button>
        </div>
        {#if exportReview && exportReview.key === exportKey}
          {@const {check, tagsOnly} = exportReview}
          <div class="export-review">
            <strong>{check.tracks} {check.tracks === 1 ? 'track' : 'tracks'} · {tagsOnly ? 'Update tags and lyrics' : 'Render WAV and MP3'}</strong>
            <small>{check.cover ? `Cover: ${check.cover}` : 'No cover selected'}{tagsOnly ? ' · Encoded audio stays intact' : ' · Delivery names include measured loudness and peak'}</small>
            {#if tagsOnly && !check.can_update_tags}<p>Export these tracks before updating their tags.</p>{/if}
            {#if !tagsOnly && check.existing.length}
              <details><summary>{check.existing.length} existing {check.existing.length === 1 ? 'file' : 'files'}</summary>
                <ul>{#each check.existing as path}<li>{path}</li>{/each}</ul>
              </details>
              {#if !check.will_replace}<p>Enable Replace previous outputs and review again to proceed.</p>{/if}
            {/if}
            <button class="primary" disabled={busy || (tagsOnly ? !check.can_update_tags : !!check.existing.length && !check.will_replace)} onclick={() => action(run)}>{tagsOnly ? 'Update tags' : 'Export album'}</button>
          </div>
        {/if}
        {#each results as item}
          <div class="result"><strong>{item.track}</strong><span>{item.lufs} LUFS · WAV {item.true_peak} dBTP · MP3 {item.mp3_true_peak} dBTP{item.mp3_peak_warning ? ' · above ceiling' : ''}</span><small>{item.mp3}</small></div>
        {/each}
      </details>
    {/if}
    <p class="status" aria-live="polite">{status}</p>
  </section>
</main>

<style>
  :global(*){box-sizing:border-box} :global(body){margin:0;background:#111411;color:#eaece2;font:15px system-ui,sans-serif}
  header{display:flex;align-items:center;gap:1.3rem;padding:.8rem 1.25rem;border-bottom:1px solid #30372e}
  header a{color:#eaece2;text-decoration:none;font-size:1.25rem;font-weight:650} header span,small{color:#a9b99e} header span{font-size:.67rem;letter-spacing:.2em}
  main{width:100%;padding:1rem 1.25rem;display:grid;grid-template-columns:minmax(220px,270px) minmax(0,1fr);gap:1.8rem}
  aside{align-self:start;position:sticky;top:1rem;background:#191e18;padding:1.15rem;border-radius:14px;border:1px solid #30372e}
  h2{margin:0 0 1rem;font-size:1.02rem} h1{margin:.18rem 0;font-size:clamp(2rem,4vw,3.25rem);letter-spacing:-.055em}
  .library-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem}.library-head h2{margin:0}.library-head button{font-size:.75rem;padding:.3rem .45rem}
  p{color:#a9b99e;margin:.5rem 0 1rem} label{display:grid;gap:.35rem;color:#c8d1c1;font-size:.83rem;margin:.55rem 0}
  select,input,textarea,button{font:inherit;color:inherit;background:#222a20;border:1px solid #414e3d;border-radius:7px;padding:.55rem;min-width:0}
  select,input,textarea{width:100%} textarea{resize:vertical} button{cursor:pointer} button:hover,.active,button[aria-pressed=true]{border-color:#b3d68f;background:#35482c} button:disabled{opacity:.45;cursor:default}
  .tracks{display:grid;gap:.35rem;margin-top:.8rem}.tracks button{text-align:left}
  .waveform{min-height:240px;background:#121a12;border-radius:8px;margin-bottom:.35rem}
  .treatments{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.65rem;margin-top:.75rem}
  .treatment{background:#222a20;border:1px solid #414e3d;border-radius:8px;padding:.5rem .75rem}
  .treatment .check{display:flex;align-items:center;gap:.55rem;font-weight:650}
  .treatment-option{display:grid;grid-template-columns:minmax(7.5rem,1.2fr) minmax(0,1fr) 4.3rem;align-items:center;gap:.45rem}
  .treatment-option label{margin:.2rem 0}
  .treatment-option output,.inline-control output{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
  .treatment:has(.treatment-option){grid-column:1/-1}
  .frequency-scale{display:flex;justify-content:space-between;margin:.4rem 0;color:#a9b99e;font-size:.75rem}
  .workspace{min-width:0}.transport{padding:1.1rem;background:#1d281b;border:1px solid #3e5137;border-radius:14px;margin-bottom:1rem}
  .transport.compact{position:sticky;top:0;z-index:2}
  audio{width:100%;height:38px}.row{display:flex;gap:.65rem;align-items:end;flex-wrap:wrap;margin:.55rem 0}.row label{flex:1;min-width:100px}
  .row .check{display:flex;align-items:center;flex:none;min-width:0}.check input{width:auto}
  details{padding:.85rem 1rem;margin:.65rem 0;border:1px solid #30372e;border-radius:12px;background:#191e18}
  summary{cursor:pointer;font-weight:650;padding:.25rem 0} details[open] summary{margin-bottom:.65rem}
  .wave-stack{position:relative}.scope{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
  .scope path,.curve{stroke:#ffa45f;fill:none;stroke-width:1.5}.spectrum{stroke:#55778f;fill:none}.grid{stroke:#46545d}
  .scope rect{fill:#ae3c33;opacity:.18}.scope rect.hot{opacity:.65}.scope text{font-size:5px;fill:#ff9b90}
  .cleanup-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem}
  .cleanup-layout .effect{height:240px}
  @media(max-width:899px){.cleanup-layout{grid-template-columns:1fr}}
  .clip{background:#912b26;color:#fff}.effect{width:100%;height:150px;background:#101923;border-radius:8px}
  .noise-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);gap:1rem}
  .noise-grid img{width:100%;min-width:0;border-radius:8px}.cover{max-width:120px;max-height:120px;object-fit:contain}
  .transport.compact .wave-stack{display:none}
  .transport.compact h1{font-size:1.15rem;letter-spacing:0}
  .transport.compact p{margin:.2rem 0}
  .stage-toggle{width:auto;margin-left:.45rem;vertical-align:middle}
  .inline-control{display:grid;grid-template-columns:max-content minmax(60px,1fr) 3rem;align-items:center;gap:.5rem}
  .spectrogram{position:relative;touch-action:none;cursor:crosshair}.spectrogram img{display:block;pointer-events:none}
  .marquee{position:absolute;border:1px solid #ffb36b;background:#ffad4933;pointer-events:none}
  @media(max-width:850px){.noise-grid{grid-template-columns:1fr}}
  .primary{background:#b3d68f;color:#131d0f;font-weight:700}
  .result{display:grid;gap:.25rem;padding:.7rem 0;border-top:1px solid #30372e;margin-top:.5rem}.error{color:#ffb4a4}.status{min-height:1.2em}
  .export-review{display:grid;justify-items:start;gap:.5rem;padding:.8rem;border:1px solid #414e3d;border-radius:8px;overflow-wrap:anywhere}
  @media(max-width:700px){main{display:block;padding:1rem}aside,.transport.compact{position:static}aside{margin-bottom:1rem}}
</style>
