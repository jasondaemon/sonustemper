<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import EqPanel from './EqPanel.svelte';
  import StereoMeter from './StereoMeter.svelte';
  import RepairTimeline from './RepairTimeline.svelte';
  import CleanupQueue from './CleanupQueue.svelte';
  import VoiceControls from './VoiceControls.svelte';
  import {defaultNoise, savedNoise, type NoiseSelection, type NoiseSettings} from './noiseSelections';
  import {defaultFindingCut, findingKey, findingPlan, findingTitle, isNoiseFinding, maxRepairs, noiseForFinding, overlappingFix, rangeLabel, repairBand, repairTypes, reviewItems, timeLabel, type Analysis, type Finding, type Range, type Repair, type RepairKind, type ReviewItem} from './repairs';
  import {advanceMeter, emptyMeters} from './levels';
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
  let waveform = $state<HTMLDivElement>();
  let waveError = $state(false);
  type Result = {track: string; mp3: string; lufs: number; true_peak: number; mp3_true_peak: number; mp3_peak_warning: boolean};
  type AlbumJob = {status:string;stage:string;track?:string;track_number?:number;error?:string;results?:Result[]};
  type Folder = {name:string; playable:boolean; pending:number|null};
  type Cover = {name:string; size:number};
  type PromptKind = 'style' | 'negative' | 'prompt';
  type TextKind = PromptKind | 'lyrics';
  type TrackText = Record<TextKind | 'draft', string>;
  type AlbumFiles = Partial<Record<'raw'|'mastered'|'clean'|'normalized'|'mp3s', string[]>> & {
    delivery?:Record<string,string[]>; covers?:Cover[]; text_status?:Record<string,Partial<Record<TextKind,boolean>>>;
  };
  const promptButtons = [{kind:'style', label:'Style Prompt'}, {kind:'negative', label:'Negative Prompt'},
    {kind:'prompt', label:'Lyric Prompt'}] as const;
  type LibraryPosition = {location:string; artist:string; album:string; source:string; track:string};
  const positionKey = 'sonustemper:album-library-position';
  let locations = $state<{name:string;available:boolean;writable:boolean}[]>([]);
  let presets = $state<Preset[]>([]);
  let artists = $state<Folder[]>([]), albums = $state<Folder[]>([]);
  let library = $state<AlbumFiles>({});
  let location = $state(''), artist = $state(''), album = $state(''), track = $state('');
  let source = $state<'raw'|'mastered'>('raw');
  let loadingLibrary = $state(false);
  let preset = $state(''), presetBase = $state(''), bands = $state<Band[]>(defaultBands());
  let activePreset = $derived(presets.find(item => item.id === presetBase));
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
  let reverbConfig = $derived(corrections.ai_reverb_reduce.options!);
  let balance = $state('album'), lufs = $state(-16), peak = $state(-1.5);
  let artistTag = $state(''), albumArtist = $state(''), albumTag = $state(''), cover = $state('');
  let year = $state(new Date().getFullYear());
  let promptDialog: HTMLDialogElement;
  let promptEditor = $state<{kind:PromptKind; track:string; keys:typeof keys}>();
  let promptText = $state(''), submissionLyrics = $state('');
  let promptLoading = $state(false), promptWorking = $state(false), promptLoaded = $state(false);
  let promptError = $state(''), promptMessage = $state('');
  let sidecar = $state(false), player = $state<HTMLAudioElement>();
  type PlaybackMode = 'original'|'cleanup'|'live';
  let playbackMode = $state<PlaybackMode>('original');
  let cleanupAudio = $state<Record<string,{key:string;url:string}>>({});
  let cleanupRunning = $state<Record<string,boolean>>({});
  let status = $state(''), error = $state(''), busy = $state(false);
  let results = $state<Result[]>([]);
  let exportStatus = $state(''), exportError = $state('');
  let cleanupEnabled = $state(false), eqEnabled = $state(false), noiseEnabled = $state(false);
  let cleanupVersion = $state<1|2>(2), repairs = $state<Repair[]>([]);
  let repairRange = $state<Range>(), activeFinding = $state<Finding>(), activeRepair = $state('');
  let manualKind = $state<RepairKind>('presence'), loopRange = $state(false);
  let analysis = $state<Analysis>(), analyzeBusy = $state(false), cleanupError = $state('');
  let findingCut = $state(defaultFindingCut), appliedMessage = $state('');
  let rowCut = $state<number>();
  let ignoredFindings = $state<string[]>([]);
  let lastApplied = $state<{ids:string[];wasEnabled:boolean;manual:string}>();
  let compareBusy = $state(false), compareActive = $state(false), customRepairOpen = $state(false);
  type BatchMode = 'pending'|'applied';
  let compareResult = $state<{before:string;after:string;removed?:string;start?:number;key:string;label:string;batch?:BatchMode;count?:number}>();
  let batchBusy = $state<BatchMode>();
  type ComparisonSide = 'before'|'after'|'removed';
  let compareSide = $state<ComparisonSide>('after');
  let voiceControls = $state({
    deesser:{enabled:false,freq_hz:6500,amount_db:0},
    vocal_smooth:{enabled:false,freq_hz:4500,amount_db:0},
    deharsh:{enabled:false,freq_hz:3200,amount_db:0}
  });
  let noise = $state<NoiseSettings>({...defaultNoise}), noiseSelections = $state<NoiseSelection[]>([]);
  let noiseEditorOpen = $state(false), editingNoise = $state(''), noiseError = $state('');
  let noisePanelElement = $state<HTMLDetailsElement>(), noiseDraftId = $state('');
  let findingSimilar = $state(false), noiseMatchMessage = $state('');
  let hasNoise = $derived(noiseEnabled && noiseSelections.some(item=>item.enabled));
  let noisePresets = $state<{id:string;origin:string;meta:{title:string};noise:Partial<typeof defaultNoise>}[]>([]);
  let noiseURL = $state(''), noiseMode = $state(''), noiseBusy = $state(false);
  let sourceDuration = $state(0);
  let compactPlayer = $state(false);
  let selection = $state<{x:number;y:number;endX:number;endY:number}>();
  let dragStart: {x:number;y:number}|undefined;
  let draggingNoise = $state(false);
  let specScale = $state<'log'|'lin'>('log'), specRange = $state(120);
  let specZoom = $state(1), specStart = $state(0), specWidth = $state(1200);
  let specEnd = $derived(specStart + 1/specZoom);
  let specFailed = $state(false), specLoading = $state(false);
  let cursor = $state<{x:number;y:number}>();
  let playhead = $state(0);
  const frequencyAt = (y:number) => Math.round(specScale === 'log' ? 20 * 1000 ** (1-y) : 24000 * (1-y));
  const frequencyY = (hz:number) => 1 - (specScale === 'log' ? Math.log(hz/20)/Math.log(1000) : hz/24000);
  const asNoiseBox = (item:NoiseSettings) => ({
    x:item.apply_scope==='global' ? 0 : Math.min(1,item.t0_sec/sourceDuration),
    endX:item.apply_scope==='global' ? 1 : Math.min(1,item.t1_sec/sourceDuration),
    y:frequencyY(item.f_high), endY:frequencyY(item.f_low),
  });
  let visibleSelection = $derived(draggingNoise ? selection : sourceDuration > 0 && noiseEditorOpen && noise.f_high>noise.f_low &&
    (noise.apply_scope==='global' || noise.t1_sec>noise.t0_sec) ? asNoiseBox(noise) : undefined);
  let engine = $state<ReturnType<typeof monitor>>();
  let monitorMedia:HTMLAudioElement | undefined;
  let spectrum = $state(''), eqCurve = $state(''), scopePath = $state('');
  let meterChannels = $state(emptyMeters());
  let outputClipped = $derived(meterChannels.some(channel => channel.clipped));
  let animation = 0;
  let selectionVersion = 0;
  let writable = $derived(locations.find(item => item.name === location)?.writable);
  let tagEdits = $state<Record<string,{title:string;number:string}>>({});
  let keys = $derived({location, artist_folder: artist, album_folder: album, source});
  let tracks = $derived(library[source] || []);
  let pendingTracks = $derived(tracks.filter(name => library.delivery?.[name]?.length).length);
  let processing = $derived({
    preset:presetBase, bands, voicing_enabled:eqEnabled, voice_controls:voiceControls,
    cleanup_enabled:cleanupEnabled, corrections, noise_enabled:noiseEnabled, noise_selections:noiseSelections,
    strength, stereo_width:width, guardrails, cleanup_version:cleanupVersion, repairs
  });
  type Processing = Omit<typeof processing,'voice_controls'|'corrections'> & {
    voice_controls:{[K in keyof typeof voiceControls]?:Partial<(typeof voiceControls)[K]>};
    corrections:Record<string,Partial<Correction>|undefined>;
    noise?:Partial<NoiseSettings>;
  };
  type Draft = {defaults:Processing; overrides:Record<string,Processing>; tags:typeof tagEdits;
    albumSettings:typeof albumSettings; shareTone?:boolean};
  let defaults = $state<Processing>();
  let overrides = $state<Record<string,Processing>>({});
  let inherited = $state.raw<Processing>();
  let shareTone = $state(false);
  let readyDraftKey = $state('');
  let sessionBaseline = '';
  let sessionSyncFailed = $state<Record<string, boolean>>({});
  const sessionSaves = new Map<string, {body:string; timer?:ReturnType<typeof setTimeout>; saving?:boolean}>();
  let draftKey = $derived('sonustemper:album:' + JSON.stringify([location, artist, album, source]));
  let albumSettings = $derived({balance, lufs, peak, artist:artistTag, album_artist:albumArtist,
    album:albumTag, cover, year, sidecar, profile});
  function copy<T>(value:T):T {return JSON.parse(JSON.stringify(value));}
  const initialProcessing:Processing = {preset:'', bands:defaultBands(), voicing_enabled:false,
    voice_controls:{}, cleanup_enabled:false, corrections:{}, noise_enabled:false,
    noise_selections:[], strength:100, stereo_width:1, guardrails:true, cleanup_version:2, repairs:[]};
  function toneOf({preset,bands,voicing_enabled,strength,stereo_width,guardrails}:Processing) {
    return {preset,bands,voicing_enabled,strength,stereo_width,guardrails};
  }
  let sharedTone = $derived(shareTone ? toneOf(processing) : undefined);
  let trackCustom = $derived(Boolean(track && inherited &&
    JSON.stringify(processing) !== JSON.stringify({...inherited,...sharedTone})));
  let effectiveDefaults = $derived(shareTone ? {...(defaults ?? initialProcessing),...sharedTone} : (defaults ?? initialProcessing));
  let effectiveOverrides = $derived.by(() => {
    const saved = {...overrides};
    if (trackCustom) saved[track] = processing;
    else delete saved[track];
    if (sharedTone) for (const name of Object.keys(saved)) saved[name] = {...saved[name],...sharedTone};
    return saved;
  });
  let draft = $derived({defaults:effectiveDefaults, overrides:effectiveOverrides, tags:tagEdits, albumSettings, shareTone});
  let settings = $derived({...keys, ...draft.defaults, tracks, ...albumSettings,
    tags:draft.tags, track_overrides:effectiveOverrides});
  let cleanupSong = $derived(JSON.stringify([draftKey,track]));
  let cleanupRequest = $derived({...keys,tracks:[track],cleanup_version:cleanupVersion,cleanup_enabled:cleanupEnabled,
    repairs,corrections,voice_controls:voiceControls,noise_enabled:noiseEnabled,noise_selections:noiseSelections,voicing_enabled:false});
  let cleanupKey = $derived(JSON.stringify(cleanupRequest));
  let canRenderCleanup = $derived(Boolean(track && source==='raw' && (hasNoise || (cleanupEnabled &&
    (repairs.some(fix=>fix.enabled && fix.amount>0) || Object.values(corrections).some(item=>item.enabled) ||
      Object.values(voiceControls).some(item=>item.enabled && item.amount_db<0))))));
  let cleanupBusy = $derived(Boolean(cleanupRunning[cleanupSong]));
  let postReady = $derived(Boolean(cleanupAudio[cleanupSong]?.key===cleanupKey));
  let postError = $state('');
  let audibleMode = $derived(source==='mastered' || (playbackMode==='cleanup' && !postReady) ? 'original' : playbackMode);
  let repairDuration = $derived(analysis?.duration ?? sourceDuration);
  let selectedFix = $derived(repairs.find(item => item.id === activeRepair));
  let reviewQueue = $derived(reviewItems(analysis?.findings ?? [],repairs,noiseSelections));
  let activeRow = $derived(activeRepair || (activeFinding ? `finding-${findingKey(activeFinding)}` : ''));
  let activeNoiseRow = $derived(reviewQueue.find(row=>row.id===activeRow && isNoiseFinding(row.kind)));
  let bulkPlan = $derived(findingPlan((analysis?.findings ?? []).filter(item=>!ignoredFindings.includes(findingKey(item))),repairs,findingCut));
  let itemCut = $derived(rowCut ?? findingCut);
  let proposedFix = $derived(activeFinding ? findingPlan([activeFinding],[],itemCut).proposed[0] : undefined);
  let findingIgnored = $derived(Boolean(activeFinding && ignoredFindings.includes(findingKey(activeFinding))));
  let comparisonFix = $derived(proposedFix ?? selectedFix);
  let canCompare = $derived(Boolean(proposedFix || (comparisonFix?.enabled && comparisonFix.amount>0 && cleanupEnabled)));
  let compareKey = $derived(JSON.stringify({...keys,track,processing,context:activeFinding ?? activeRepair,proposal:proposedFix}));
  let appliedSuggestionIds = $derived(cleanupEnabled ? repairs.filter(fix=>fix.id.startsWith('finding-') && fix.enabled && fix.amount>0).map(fix=>fix.id) : []);
  let pendingBatchKey = $derived(JSON.stringify(['pending',keys,track,processing,bulkPlan.proposed]));
  let appliedBatchKey = $derived(JSON.stringify(['applied',keys,track,processing]));
  let batchVisible = $derived(Boolean(compareResult?.batch && compareResult.key===(compareResult.batch==='pending' ? pendingBatchKey : appliedBatchKey)));
  let rowCompareReady = $derived(Boolean(compareResult && !compareResult.batch && compareResult.key===compareKey));
  let editorNoiseCandidate = $derived(noiseEditorOpen ? {...noise,id:editingNoise || noiseDraftId || 'unsaved-selection',enabled:true} : undefined);
  let rowNoiseKey = $derived(activeNoiseRow ? noiseComparisonKey(noiseForRow(activeNoiseRow)) : '');
  let editorNoiseKey = $derived(noiseComparisonKey(editorNoiseCandidate));
  let playingCompare = $derived(compareActive && (batchVisible || rowCompareReady || noiseComparisonReady(rowNoiseKey) || noiseComparisonReady(editorNoiseKey)));
  let audioURL = $derived(track ? '/api/albums/audio?' + new URLSearchParams({...keys, track}) : '');

  let playbackURL = $derived(playingCompare && compareResult ? compareResult[compareSide] ?? audioURL
    : noiseMode ? noiseURL : audibleMode!=='original' && postReady ? cleanupAudio[cleanupSong].url : audioURL);
  let lastMediaURL='', lastMediaSong='';

  $effect.pre(() => {
    const song=cleanupSong, url=playbackURL;
    if (song===lastMediaSong && lastMediaURL && url && url!==lastMediaURL) {
      untrack(() => {if (!removeSeekListener) restorePlayhead(url);});
    }
    lastMediaSong=song;lastMediaURL=url;
  });

  $effect(() => {if (compareActive && !playingCompare) compareActive=false;});

  $effect(() => {
    if (track && readyDraftKey === draftKey) {
      const snapshot = JSON.stringify(draft);
      if (snapshot === sessionBaseline) return;
      sessionBaseline = snapshot;
      try {
        localStorage.setItem(draftKey, snapshot);
        localStorage.setItem(draftKey+':pending', '1');
      } catch { /* Server autosave also works without browser storage. */ }
      scheduleSessionSave(draftKey, JSON.stringify({...keys,draft}));
    }
  });

  $effect(() => {
    if (location && album && track && tracks.includes(track)) {
      try { localStorage.setItem(positionKey, JSON.stringify({location, artist, album, source, track})); }
      catch { /* Browser storage can be disabled. */ }
    }
  });

  $effect(() => {
    const container = waveform, media = player, url = playbackURL;
    const WaveSurfer = (window as unknown as {WaveSurfer?: {create(options: object): {
      on(event: 'error', callback: (error: unknown) => void): void;
      setOptions(options: object): void;
      destroy(): void;
    }}}).WaveSurfer;
    if (!container || !media || !url || !WaveSurfer) return;
    waveError = false;
    function waveformColors() {
      const colors = getComputedStyle(document.documentElement);
      return {waveColor:colors.getPropertyValue('--chart-spectrum').trim(),
        progressColor:colors.getPropertyValue('--chart-line').trim(),
        cursorColor:colors.getPropertyValue('--accent').trim()};
    }
    const wave = WaveSurfer.create({container, media, url:new URL(url, document.baseURI).href, height:240, normalize:false,
      ...waveformColors(),
      barWidth:2, barGap:1, dragToSeek:true, autoScroll:false});
    wave.on('error', error => {if (!(error instanceof DOMException && error.name === 'AbortError')) waveError = true;});
    const theme = new MutationObserver(() => wave.setOptions(waveformColors()));
    theme.observe(document.documentElement, {attributeFilter:['data-theme']});
    return () => {theme.disconnect(); wave.destroy();};
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

  function scheduleSessionSave(key:string, body:string) {
    let entry = sessionSaves.get(key);
    if (!entry) {
      entry = {body};
      sessionSaves.set(key, entry);
    }
    entry.body = body;
    clearTimeout(entry.timer);
    if (!entry.saving) entry.timer = setTimeout(() => void syncSession(key), 500);
  }

  async function syncSession(key:string) {
    const entry = sessionSaves.get(key);
    if (!entry || entry.saving) return;
    const {body} = entry;
    entry.saving = true;
    try {
      const result = await fetch('/api/albums/session', {method:'PUT', headers:{'Content-Type':'application/json'}, body});
      if (!result.ok) throw new Error('Session sync failed');
      delete sessionSyncFailed[key];
      if (entry.body === body) {
        try { localStorage.removeItem(key+':pending'); } catch { /* Browser storage can be disabled. */ }
      }
    } catch {
      sessionSyncFailed[key] = true;
    } finally {
      entry.saving = false;
      if (entry.body !== body) void syncSession(key);
      else sessionSaves.delete(key);
    }
  }

  async function action(run: () => Promise<void>) {
    error = '';
    try { await run(); } catch (cause) { error = String(cause instanceof Error ? cause.message : cause); }
  }

  function browseFolders(location: string, artist_folder = '') {
    return api<Folder[]>('browse?' + new URLSearchParams({location, artist_folder, status:'true'}));
  }

  function chooseFolder(items: Folder[], name?: string) {
    return (items.find(item => item.name === name && item.playable) ?? items.find(item => item.playable) ?? items[0])?.name ?? '';
  }

  async function selectLocation(position?: LibraryPosition) {
    const version = ++selectionVersion;
    readyDraftKey = '';
    artist = album = track = ''; artists = []; albums = []; library = {};
    loadingLibrary = false;
    if (!location) return;
    try { localStorage.setItem(positionKey, JSON.stringify(position ?? {location})); }
    catch { /* Browser storage can be disabled. */ }
    loadingLibrary = true;
    try {
      const found = await browseFolders(location);
      if (version !== selectionVersion) return;
      artists = found;
      artist = chooseFolder(found, position?.artist);
      await selectArtist(artist === position?.artist ? position : undefined);
    } finally {
      if (!artist && version === selectionVersion) loadingLibrary = false;
    }
  }

  async function selectArtist(position?: LibraryPosition) {
    const version = ++selectionVersion;
    readyDraftKey = '';
    album = track = ''; albums = []; library = {};
    loadingLibrary = true;
    try {
      const found = artist ? await browseFolders(location, artist) : [];
      if (version !== selectionVersion) return;
      albums = found;
      album = chooseFolder(found, position?.album);
      await selectAlbum(album === position?.album ? position : undefined);
    } finally {
      if (version === selectionVersion) loadingLibrary = false;
    }
  }

  function loadProcessing(value:Processing) {
    const draft = copy(value);
    cleanupVersion = draft.cleanup_version ?? 1;
    repairs = draft.repairs ?? [];
    presetBase = draft.preset;
    bands = draft.bands;
    const presetBands = presets.find(item=>item.id===draft.preset)?.bands;
    preset = presetBands && JSON.stringify(presetBands.map(item=>({...item,gain_db:item.gain_db ?? 0,q:item.q ?? .7}))) === JSON.stringify(bands) ? draft.preset : '';
    eqEnabled = draft.voicing_enabled;
    voiceControls = {
      deesser:{enabled:false,freq_hz:6500,amount_db:0,...draft.voice_controls?.deesser},
      vocal_smooth:{enabled:false,freq_hz:4500,amount_db:0,...draft.voice_controls?.vocal_smooth},
      deharsh:{enabled:false,freq_hz:3200,amount_db:0,...draft.voice_controls?.deharsh},
    };
    corrections = Object.fromEntries(treatments.map(tool => [tool.id, {
      enabled:false, value:tool.value, ...draft.corrections?.[tool.id],
      ...(tool.id === 'ai_reverb_reduce' ? {options:{...reverbDefaults,...draft.corrections?.[tool.id]?.options}} : {}),
    }]));
    cleanupEnabled = draft.cleanup_enabled ?? Object.keys(draft.corrections ?? {}).length > 0;
    noiseEnabled = draft.noise_enabled ?? Boolean(draft.noise);
    noiseSelections = savedNoise(draft);
    noise = {...defaultNoise}; noiseEditorOpen=false; editingNoise=''; noiseDraftId=''; noiseError='';noiseMatchMessage='';findingSimilar=false;
    strength = draft.strength ?? 100; guardrails = draft.guardrails ?? true;
    width = draft.stereo_width ?? activePreset?.width ?? 1;
  }

  function rememberTrack() {
    if (!track) return;
    if (trackCustom) overrides[track] = copy(processing);
    else delete overrides[track];
    if (shareTone) defaults = copy(effectiveDefaults);
  }

  function setToneScope(checked:boolean) {
    if (!checked) {
      const nextOverrides = copy(effectiveOverrides);
      defaults = copy(effectiveDefaults);
      overrides = nextOverrides;
      if (inherited) inherited = {...inherited,...copy(toneOf(processing))};
    }
    shareTone = checked;
  }

  function loadTrackProcessing() {
    loadProcessing(defaults ?? initialProcessing);
    inherited = copy(processing);
    const trackSettings=overrides[track];
    if (trackSettings) loadProcessing({... (defaults ?? initialProcessing), ...trackSettings,
      ...(trackSettings.noise && !trackSettings.noise_selections ? {noise_selections:savedNoise(trackSettings)} : {}),
      ...(shareTone ? toneOf(processing) : {})});
  }

  async function reopenSession() {
    const version = selectionVersion;
    let saved: Draft | null = null;
    try { saved = JSON.parse(localStorage.getItem(draftKey) ?? 'null'); } catch { /* Fall back to server save. */ }
    if (!saved) ({draft:saved} = await api<{draft:Draft|null}>('session?'+new URLSearchParams(keys)));
    if (version !== selectionVersion || !saved) return;
    defaults=saved.defaults;
    overrides=saved.overrides;
    shareTone=saved.shareTone ?? false;
    tagEdits=saved.tags;
    const a=saved.albumSettings;
    ({balance,lufs,peak,year,sidecar,profile}=a);
    cover=library.covers?.some(item => item.name === a.cover) ? a.cover : '';
    artistTag=a.artist;albumArtist=a.album_artist;albumTag=a.album;
    loadTrackProcessing();
  }

  async function selectAlbum(position?: LibraryPosition) {
    const version = ++selectionVersion;
    readyDraftKey = '';
    library = {}; track = ''; results = [];
    loadingLibrary = true;
    try {
      if (!album) return;
      const found = await api<AlbumFiles>('browse?' + new URLSearchParams(keys));
      if (version !== selectionVersion) return;
      library = found;
      const rememberedSource = position?.source;
      source = (rememberedSource === 'raw' || rememberedSource === 'mastered') && position && found[rememberedSource]?.includes(position.track)
        ? rememberedSource : found.raw?.length ? 'raw' : 'mastered';
    } finally {
      if (version === selectionVersion) loadingLibrary = false;
    }
    if (version === selectionVersion) await selectStage(position?.track);
  }

  async function refreshLibrary() {
    const version = selectionVersion;
    const foundLocations = await api<typeof locations>('locations');
    if (version !== selectionVersion) return;
    locations = foundLocations;
    if (!location || !locations.some(item => item.name === location && item.available)) {
      location = locations.find(item => item.available)?.name || '';
      return selectLocation();
    }
    const refreshedArtists = await browseFolders(location);
    if (version !== selectionVersion) return;
    artists = refreshedArtists;
    if (!artists.some(item => item.name === artist)) {
      artist = chooseFolder(artists);
      return selectArtist();
    }
    const refreshedAlbums = await browseFolders(location, artist);
    if (version !== selectionVersion) return;
    albums = refreshedAlbums;
    if (!albums.some(item => item.name === album)) {
      album = chooseFolder(albums);
      return selectAlbum();
    }
    const found = await api<AlbumFiles>('browse?' + new URLSearchParams(keys));
    if (version !== selectionVersion) return;
    library = found;
    if (!found.covers?.some(item => item.name === cover)) cover = '';
    if (!tracks.includes(track)) {
      if (!tracks.length && found[source === 'raw' ? 'mastered' : 'raw']?.length) {
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

  async function selectStage(preferredTrack?: string) {
    readyDraftKey=''; defaults=undefined; overrides={}; tagEdits={}; shareTone=false;
    balance='album'; profile='guiding-night'; selectProfile();
    year=new Date().getFullYear(); sidecar=false;
    artistTag=albumArtist=artist; albumTag=album;
    cover=library.covers?.find(item => item.name === 'cover.png')?.name ?? library.covers?.[0]?.name ?? '';
    status=''; exportStatus=''; exportError=''; results=[];
    track = preferredTrack && tracks.includes(preferredTrack) ? preferredTrack : tracks[0] || '';
    const ready = selectTrack();
    const version = selectionVersion;
    await ready.catch(cause => { if (version === selectionVersion) error = String(cause); });
    const key = draftKey;
    if (track && version === selectionVersion) {
      try { await reopenSession(); }
      finally {
        if (key === draftKey && version === selectionVersion && track) {
          sessionBaseline=JSON.stringify(draft);
          readyDraftKey=key;
          try {
            if (localStorage.getItem(key+':pending')) scheduleSessionSave(key, JSON.stringify({...keys,draft}));
          } catch { /* No browser draft to retry. */ }
        }
      }
    }
  }

  async function selectTrack() {
    ++selectionVersion;
    removeSeekListener?.();
    noiseMode=''; sourceDuration=0; playhead=0; cursor=undefined; selection=undefined;
    dragStart=undefined;draggingNoise=false;
    specStart=0; specZoom=1; specWidth=1200;
    playbackMode='original';meterChannels=emptyMeters();scopePath='';
    analysis=undefined; analyzeBusy=false; cleanupError=''; postError=''; activeFinding=undefined; activeRepair=''; repairRange=undefined; loopRange=false;customRepairOpen=false;
    appliedMessage='';lastApplied=undefined;
    ignoredFindings=[];
    try {
      const previous:unknown=JSON.parse(localStorage.getItem(findingStorageKey()) ?? '[]');
      if (Array.isArray(previous)) ignoredFindings=previous.filter((id):id is string=>typeof id==='string');
    } catch { /* The review still works without browser storage. */ }
    clearCompare();
    loadTrackProcessing();
  }

  async function analyzeSong() {
    if (analyzeBusy) return;
    const version=selectionVersion;
    analyzeBusy=true; cleanupError='';
    try {
      const result=await api<Analysis>('analyze?' + new URLSearchParams({...keys,track}));
      if (version === selectionVersion) {analysis=result;activeFinding=undefined;}
    } catch (cause) {
      if (version === selectionVersion) cleanupError=String(cause instanceof Error ? cause.message : cause);
    } finally {
      if (version === selectionVersion) analyzeBusy=false;
    }
  }

  function seekTo(seconds:number) {
    if (player && Number.isFinite(player.duration)) player.currentTime=Math.min(seconds,player.duration);
  }

  function chooseFinding(item:Finding) {
    const row=reviewQueue.find(row=>row.findings.some(finding=>findingKey(finding)===findingKey(item)));
    if (row) selectReviewItem(row,true);
  }

  function chooseRepair(id:string) {
    const row=reviewQueue.find(item=>item.id===id);
    if (row) selectReviewItem(row,true);
  }

  function selectReviewItem(row:ReviewItem, fromTimeline=false) {
    if (compareResult?.removed) compareActive=false;
    if (!fromTimeline && activeRow===row.id) {
      activeFinding=undefined;activeRepair='';loopRange=false;repairRange=undefined;
      return;
    }
    activeRepair=row.fix?.id ?? row.noise?.id ?? '';
    activeFinding=row.fix || row.noise ? undefined : row.findings[0];
    repairRange=row.end===null ? undefined : {start:row.start,end:row.end};
    loopRange=false;cleanupError='';rowCut=undefined;
    if (isNoiseFinding(row.kind)) focusNoiseRow(row);
    else if (row.kind!=='peak') manualKind=row.kind;
    if (row.end!==null) seekTo(Math.max(0,row.start-.25));
    cleanupPanel?.setAttribute('open','');
    if (fromTimeline) void tick().then(()=>document.getElementById(`cleanup-row-${row.id}`)?.scrollIntoView({block:'nearest'}));
  }

  let cleanupPanel = $state<HTMLDetailsElement>();

  function upgradeCleanup() {
    if (cleanupVersion === 2) return true;
    if (!confirm('Use repair before EQ for this track? Noise moves before EQ, and enabled voice controls move to Cleanup.')) return false;
    if (!eqEnabled) {
      for (const control of Object.values(voiceControls)) control.enabled=false;
    }
    cleanupEnabled ||= Object.values(voiceControls).some(control => control.enabled);
    cleanupVersion=2;
    return true;
  }

  function setRepairRange(range:Range) {
    activeFinding=undefined;repairRange=range;
    if (!activeRepair) customRepairOpen=true;
  }

  function setFixScope(kind:RepairKind, regional:boolean, id='') {
    cleanupError='';
    if (!id && repairs.length >= maxRepairs) {cleanupError=`A track can have up to ${maxRepairs} fixes.`;return;}
    const range=regional ? repairRange : undefined;
    if (regional && (!range || range.end-range.start < .05 || range.end > repairDuration + .05)) {
      cleanupError='Choose a range of at least 0.05 seconds inside the song.';return;
    }
    const start=range?.start ?? 0, end=range?.end ?? null;
    const overlap=overlappingFix({kind,start,end},repairs,id);
    if (overlap) {
      activeRepair=overlap.id;
      cleanupError='That type of fix already covers part of this range. Edit or remove the existing fix first.';
      return;
    }
    if (!upgradeCleanup()) return;
    if (id) repairs=repairs.map(fix=>fix.id===id ? {...fix,start,end} : fix);
    else {
      id=Array.from(crypto.getRandomValues(new Uint8Array(12)), byte=>byte.toString(16).padStart(2,'0')).join('');
      repairs=[...repairs,{id,kind,enabled:true,amount:2,frequency:repairTypes[kind].frequency,start,end}];
    }
    activeFinding=undefined;activeRepair=id;cleanupEnabled=true;
  }

  function applyFindings(all=false) {
    if (!all && (!activeFinding || findingIgnored)) return;
    const amount=all ? findingCut : itemCut;
    const plan=all ? bulkPlan : findingPlan([activeFinding!],repairs,amount);
    if (!plan.proposed.length) return;
    const wasEnabled=cleanupEnabled;
    if (!upgradeCleanup()) return;
    const additions=[...plan.proposed].sort((a,b)=>a.kind.localeCompare(b.kind) || a.start-b.start);
    repairs=[...repairs,...additions];cleanupEnabled=true;
    if (activeFinding) {
      const selected=overlappingFix(activeFinding,repairs);
      if (selected) {activeRepair=selected.id;activeFinding=undefined;}
    }
    lastApplied={ids:additions.map(fix=>fix.id),wasEnabled,manual:JSON.stringify({corrections,voiceControls})};
    const skipped=[plan.covered ? `${plan.covered} already covered` : '',plan.warnings ? `${plan.warnings} warnings only` : '',
      plan.limited ? `${plan.limited} over the song limit` : ''].filter(Boolean);
    appliedMessage=`Applied ${additions.length} ${additions.length===1?'fix':'fixes'} at ${amount} dB${skipped.length ? ` · ${skipped.join(' · ')}` : ''}.`;
    cleanupError='';clearCompare();
    if (all) void renderPostCleanup();
  }

  function removeReviewFix(row:ReviewItem) {
    if (!row.fix) return;
    repairs=repairs.filter(fix=>fix.id!==row.id);
    activeRepair='';activeFinding=row.findings[0];rowCut=undefined;clearCompare();
  }

  function findingStorageKey() {return `${draftKey}:skipped:${track}`;}

  function toggleIgnoredFinding() {
    if (!activeFinding) return;
    const id=findingKey(activeFinding);
    ignoredFindings=findingIgnored ? ignoredFindings.filter(item=>item!==id) : [...ignoredFindings,id];
    try {localStorage.setItem(findingStorageKey(),JSON.stringify(ignoredFindings));}
    catch { /* Keep the skip in this review. */ }
  }

  function undoFindings() {
    if (!lastApplied) return;
    const {ids,wasEnabled,manual}=lastApplied;
    repairs=repairs.filter(fix=>!ids.includes(fix.id));
    if (!wasEnabled && !repairs.some(fix=>fix.enabled) && manual===JSON.stringify({corrections,voiceControls})) cleanupEnabled=false;
    if (ids.includes(activeRepair)) activeRepair='';
    lastApplied=undefined;appliedMessage='Last set of suggestions removed.';clearCompare();
  }

  function hearFinding() {
    if (!player || !activeFinding) return;
    loopRange=true;
    const start=Math.max(0,activeFinding.start-.25);
    if (playbackURL !== audioURL) restorePlayhead(audioURL,start,true);
    else {seekTo(start);void player.play().catch(()=>{});}
    playbackMode='original';noiseMode='';compareActive=false;
  }

  function clearCompare() {
    compareActive=false;compareResult=undefined;
  }

  async function renderComparison() {
    if (compareBusy || batchBusy || !comparisonFix || !canCompare) return;
    const key=compareKey;
    const fix=comparisonFix,candidate=proposedFix;
    const start=fix.end===null ? playhead : Math.max(0,fix.start-.25);
    compareBusy=true;cleanupError='';
    try {
      const data=await api<{before:string;after:string;count:number}>('repair-batch-audition', {
        ...keys,...processing,tracks:[track],audition_track:track,
        ...(candidate ? {candidate_repairs:[candidate],cleanup_version:2,...(cleanupVersion===1 && !eqEnabled ? {voice_controls:{}} : {})}
          : {bypass_repair_ids:[fix.id]}),
      });
      if (key !== compareKey) return;
      clearCompare();compareSide='after';
      compareResult={...data,start,key,label:repairTypes[fix.kind].title};
    } catch (cause) {
      if (key===compareKey) cleanupError=String(cause instanceof Error ? cause.message : cause);
    } finally {compareBusy=false;}
  }

  async function renderBatchComparison(mode:BatchMode) {
    if (compareBusy || batchBusy || (mode==='pending' ? !bulkPlan.proposed.length : !appliedSuggestionIds.length)) return;
    const key=mode==='pending' ? pendingBatchKey : appliedBatchKey;
    batchBusy=mode;cleanupError='';
    try {
      const data=await api<{before:string;after:string;count:number}>('repair-batch-audition', {
        ...keys,...processing, tracks:[track], audition_track:track,
        ...(mode==='pending' ? {cleanup_version:2,cleanup_enabled:true,
          ...(cleanupVersion===1 && !eqEnabled ? {voice_controls:{}} : {}),
          candidate_repairs:[...bulkPlan.proposed].sort((a,b)=>a.kind.localeCompare(b.kind) || a.start-b.start)}
          : {bypass_repair_ids:appliedSuggestionIds}),
      });
      if (key !== (mode==='pending' ? pendingBatchKey : appliedBatchKey)) return;
      clearCompare();compareSide='after';
      compareResult={...data,batch:mode,key,label:mode==='pending'?'Proposed fixes':'Applied suggestions'};
    } catch (cause) {
      if (key===(mode==='pending' ? pendingBatchKey : appliedBatchKey)) cleanupError=String(cause instanceof Error ? cause.message : cause);
    } finally {batchBusy=undefined;}
  }

  function switchComparison(side:ComparisonSide) {
    const url=compareResult?.[side];
    if (!player || !url || (!batchVisible && !rowCompareReady && !noiseComparisonReady(rowNoiseKey) && !noiseComparisonReady(editorNoiseKey))) return;
    if (playingCompare && compareSide===side) {void player.play().catch(()=>{});return;}
    restorePlayhead(url,playingCompare ? undefined : compareResult?.start,true);
    noiseMode='';compareSide=side;compareActive=true;
  }

  function updatePlayhead() {
    if (!player) return;
    if (loopRange && repairRange && repairRange.end > repairRange.start && player.currentTime >= repairRange.end) player.currentTime=repairRange.start;
    playhead=player.currentTime;
  }

  async function openPrompt(name: string, kind: PromptKind) {
    const editor = {kind, track:name, keys:{...keys}};
    promptEditor = editor;
    promptText = submissionLyrics = promptError = promptMessage = '';
    promptLoading = true;
    promptLoaded = false;
    promptDialog.showModal();
    try {
      const data = await api<TrackText>('text?' + new URLSearchParams({...editor.keys, track:name}));
      if (!promptDialog.open || promptEditor?.track !== name || promptEditor.kind !== kind) return;
      promptText = data[kind];
      submissionLyrics = data.lyrics || data.draft;
      promptLoaded = true;
    } catch (cause) {
      promptError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      promptLoading = false;
    }
  }

  async function promptAction(run: () => Promise<void>) {
    promptError = promptMessage = '';
    promptWorking = true;
    try { await run(); }
    catch (cause) { promptError = cause instanceof Error ? cause.message : String(cause); }
    finally { promptWorking = false; }
  }

  async function savePrompt(kind: TextKind) {
    if (!promptEditor) return;
    const {keys:albumKeys, track:name} = promptEditor;
    const text = kind === 'lyrics' ? submissionLyrics : promptText;
    const saved = await api<{text:string; draft?:string}>('text', {...albumKeys, track:name, kind, text}, 'PUT');
    library.text_status ??= {};
    library.text_status[name] ??= {};
    library.text_status[name][kind] = Boolean(saved.text.trim());
    if (kind === 'lyrics') submissionLyrics = saved.text;
    if (kind === 'prompt' && !submissionLyrics.trim()) submissionLyrics = saved.draft ?? '';
    promptMessage = kind === 'lyrics' ? 'Submission lyrics saved' : 'Prompt saved';
  }

  async function formatLyrics() {
    submissionLyrics = (await api<{draft:string}>('lyrics/draft', {text:promptText})).draft;
    promptMessage = 'Check the draft against the recording before submitting.';
  }

  async function copyPromptText(kind: 'prompt'|'lyrics') {
    await navigator.clipboard.writeText(kind === 'lyrics' ? submissionLyrics : promptText);
    promptMessage = kind === 'lyrics' ? 'Submission lyrics copied' : 'Prompt copied';
  }

  function songTags(name: string, index: number) {
    const stem = name.replace(/\.wav$/i, '');
    const match = stem.match(/^(\d+)\.\s*(.*)$/);
    return {title:tagEdits[name]?.title ?? (match?.[2] || stem), number:tagEdits[name]?.number ?? match?.[1] ?? String(index + 1)};
  }

  function editSongTag(name: string, index: number, field: 'number'|'title', value: string) {
    tagEdits[name] = {...songTags(name, index), [field]:value};
  }

  function sizeLabel(bytes: number) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
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
  let offlineCurveContext: OfflineAudioContext | undefined;
  let toneBands = $derived(bands.map(item=>({...item,gain_db:item.gain_db*strength/100})));
  let selectedCurve = $derived(comparisonFix ? drawCurve(offlineCurveContext ??= new OfflineAudioContext(1,1,48000),
    [{...repairBand(comparisonFix),enabled:true}]) : '');
  let activeRepairIds = $derived(cleanupEnabled ? repairs.filter(fix => fix.enabled && (fix.end === null || (playhead >= fix.start && playhead < fix.end))).map(fix => fix.id).join(',') : '');
  let boostAtFix = $derived(selectedFix && eqEnabled ? gainAt(toneBands, selectedFix.frequency) : 0);
  let combinedCurve = $derived(activeRepairIds && eqEnabled ? drawCurve(offlineCurveContext ??= new OfflineAudioContext(1,1,48000), [
    ...toneBands, ...cleanupBands, ...voiceBands, ...repairs.filter(fix=>activeRepairIds.split(',').includes(fix.id)).map(repairBand),
  ]) : '');
  let liveEQ = $derived(source==='raw' && audibleMode==='live' && !noiseMode && !playingCompare && eqEnabled);
  let liveBands = $derived(liveEQ ? [...(cleanupVersion===1 ? voiceBands : []),...toneBands] : []);


  function gainAt(filters:Band[], frequency:number) {
    const context = offlineCurveContext ??= new OfflineAudioContext(1,1,48000);
    const frequencyArray = new Float32Array([frequency]);
    const magnitude = new Float32Array(1), phase = new Float32Array(1);
    let gain = 0;
    for (const item of filters) {
      if (item.enabled === false || !Number.isFinite(item.freq_hz) || !Number.isFinite(item.gain_db) || !Number.isFinite(item.q)) continue;
      filterNode(context,item).getFrequencyResponse(frequencyArray,magnitude,phase);
      gain += 20*Math.log10(Math.max(.00001,magnitude[0]));
    }
    return gain;
  }

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
    const widthValue = liveEQ
      ? (Number.isFinite(width) ? width : 1) : 1;
    audio?.update(valid,widthValue);
    const curveContext = audio?.context ?? (offlineCurveContext ??= new OfflineAudioContext(1, 1, 48000));
    eqCurve = drawCurve(curveContext,[...(cleanupVersion === 1 ? voiceBands : []), ...toneBands]);
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
    const peaks = [0, 0];
    let previous = performance.now() / 1000;
    const frame = (timestamp:number) => {
      const now = timestamp / 1000;
      const elapsed = Math.max(0, now - previous);
      previous = now;
      const playing = player && !player.paused && !player.ended;
      if (playing) {
        audio.readPeaks(peaks);
        audio.analyser.getFloatFrequencyData(bins);
        audio.analyser.getFloatTimeDomainData(samples);
        spectrum=spectrumPath(bins,audio.context.sampleRate);
        scopePath=Array.from({length:180},(_,i)=>(i?'L':'M')+(i*600/179)+' '+(50-Math.max(-1,Math.min(1,samples[Math.floor(i*samples.length/180)]))*48)).join(' ');
      }
      meterChannels = meterChannels.map((channel, index) => advanceMeter(channel, playing ? peaks[index] : 0, now, elapsed));
      if (playing || meterChannels.some(channel => channel.hold > -60)) animation=requestAnimationFrame(frame);
    };
    animation=requestAnimationFrame(frame);
  }
  async function renderPostCleanup() {
    if (cleanupBusy || !canRenderCleanup || !upgradeCleanup()) return;
    const song=cleanupSong, signature=cleanupKey;
    cleanupRunning[song]=true;postError='';
    try {
      const result = await fetch('/api/albums/render-cleanup', {method:'POST',headers:{'Content-Type':'application/json'},body:signature});
      if (!result.ok) throw new Error((await result.json()).detail || 'Cleanup render failed');
      const blob = await result.blob();
      if (song===cleanupSong && signature!==cleanupKey) return;
      const previous=cleanupAudio[song]?.url;
      delete cleanupAudio[song];
      cleanupAudio[song]={key:signature,url:URL.createObjectURL(blob)};
      if (previous) URL.revokeObjectURL(previous);
      const stale=Object.keys(cleanupAudio).filter(key=>key!==cleanupSong && key!==song);
      while (Object.keys(cleanupAudio).length>4 && stale.length) {
        const key=stale.shift()!;
        URL.revokeObjectURL(cleanupAudio[key].url);delete cleanupAudio[key];
      }
    } catch (cause) {
      if (song===cleanupSong && signature===cleanupKey) postError=String(cause instanceof Error ? cause.message : cause);
    } finally {delete cleanupRunning[song];}
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

  function setPlayback(mode:PlaybackMode) {
    if (mode==='cleanup' && !postReady) return;
    noiseMode = '';compareActive=false;
    playbackMode=mode;
  }

  function listenLive() {
    if (source === 'raw') setPlayback('live');
  }

  function selectionPoint(event:PointerEvent) {
    const bounds=(event.currentTarget as HTMLElement).getBoundingClientRect();
    return {x:Math.max(0,Math.min(1,(event.clientX-bounds.left)/bounds.width)),
      y:Math.max(0,Math.min(1,(event.clientY-bounds.top)/bounds.height))};
  }
  function centerSpectrum(time:number) {
    if (sourceDuration>0) specStart=Math.max(0,Math.min(1-1/specZoom,time/sourceDuration-.5/specZoom));
  }

  function zoomSpectrum(value:number) {
    const center=specZoom===1 ? playhead : (specStart+.5/specZoom)*sourceDuration;
    specZoom=value;
    centerSpectrum(center);
  }

  function loadSpectrumDetail() {specWidth=specZoom<=1?1200:specZoom<=3?2400:specZoom<=6?4800:8192;}

  let specQuery = $derived({...keys,track,scale:specScale,range_db:String(specRange)});
  let specImage = $derived('/api/albums/spectrogram?'+new URLSearchParams({...specQuery,width:String(specWidth)}));
  let specOverview = $derived('/api/albums/spectrogram?'+new URLSearchParams({...specQuery,overview:'true'}));
  $effect(() => {if (specImage) {specFailed=false;specLoading=true;}});
  function drawNoise(event:PointerEvent) {
    const point=selectionPoint(event);
    cursor=point;
    if (!dragStart) return;
    selection={x:Math.min(dragStart.x,point.x),y:Math.min(dragStart.y,point.y),
      endX:Math.max(dragStart.x,point.x),endY:Math.max(dragStart.y,point.y)};
  }
  function finishNoise(event:PointerEvent) {
    if (!dragStart) return;
    drawNoise(event);
    dragStart=undefined;draggingNoise=false;
    const box=selection;selection=undefined;
    if (!box || !player || (box.endX-box.x)*(event.currentTarget as HTMLElement).clientWidth < 4 || box.endY-box.y < .01) return;
    const start=+(box.x*sourceDuration).toFixed(2), end=+(box.endX*sourceDuration).toFixed(2);
    if (end-start<.01) return;
    const low=Math.max(20,frequencyAt(box.endY)),high=Math.min(20000,frequencyAt(box.y));
    if (high<=low) {noiseError='Choose a band between 20 Hz and 20 kHz.';return;}
    if (!noiseEditorOpen) editNoise();
    noise={...noise,f_low:low,f_high:high,t0_sec:start,t1_sec:end,apply_scope:'selection'};
  }

  function editNoise(item?:NoiseSelection) {
    noise=item ? {...item} : {...defaultNoise};
    editingNoise=item?.id ?? '';noiseDraftId='';noiseError='';noiseMatchMessage='';noiseEditorOpen=true;
    if (item?.apply_scope==='selection') {
      centerSpectrum((item.t0_sec+item.t1_sec)/2);
      seekTo(item.t0_sec);
    }
  }

  function noiseForRow(row:ReviewItem) {
    if (noiseDraftId===row.id && editorNoiseCandidate) return editorNoiseCandidate;
    return row.noise ? {...row.noise,enabled:true} : row.findings[0] && noiseForFinding(row.findings[0]);
  }

  function focusNoiseRow(row:ReviewItem, jump=false) {
    const item=noiseForRow(row);
    if (!item) return;
    if (noiseDraftId!==row.id) {
      editNoise(row.noise);
      noise={...item};noiseDraftId=row.id;
    }
    if (noise.apply_scope==='selection') {
      specZoom=Math.min(48,Math.max(1,Math.floor(repairDuration/Math.max(3,(noise.t1_sec-noise.t0_sec)*1.8))));
      centerSpectrum((noise.t0_sec+noise.t1_sec)/2);
      loadSpectrumDetail();
    }
    if (noisePanelElement) noisePanelElement.open=true;
    if (jump) void tick().then(()=>noisePanelElement?.scrollIntoView({block:'start'}));
  }

  function closeNoiseEditor() {noiseEditorOpen=false;editingNoise='';noiseDraftId='';noiseError='';}

  function validNoise(item:NoiseSettings) {
    return [item.f_low,item.f_high,item.band_depth_db,item.afftdn_strength,item.hp_hz,item.lp_hz].every(Number.isFinite)
      && item.f_low>=20 && item.f_high<=20000 && item.f_high>item.f_low
      && item.hp_hz>=0 && item.hp_hz<=20000 && item.lp_hz>=0 && item.lp_hz<=20000
      && (item.apply_scope==='global' ? item.method!=='profile' : (Number.isFinite(item.t0_sec) && Number.isFinite(item.t1_sec) && item.t0_sec>=0
        && item.t1_sec-item.t0_sec>=.01 && item.t1_sec<=Math.min(36000,repairDuration+.01)));
  }
  let validNoiseDraft = $derived(validNoise(noise));

  function stopNoisePreview() {if (noiseMode) setPlayback(playbackMode);}

  function saveNoise() {
    noiseError='';
    if (!validNoiseDraft) {noiseError='Check that frequencies and times are in order and the range is inside the song.';return;}
    if (!editingNoise && noiseSelections.length>=24) {noiseError='A track can have up to 24 noise selections.';return;}
    if (editingNoise) {
      noiseSelections=noiseSelections.map(item=>item.id===editingNoise ? {...noise,id:item.id,enabled:item.enabled} : item);
    } else {
      const id=noiseDraftId || Array.from(crypto.getRandomValues(new Uint8Array(12)),byte=>byte.toString(16).padStart(2,'0')).join('');
      noiseSelections=[...noiseSelections,{...noise,id,enabled:true}];
      noiseEnabled=true;
      if (noiseDraftId && activeRow===noiseDraftId) {activeRepair=id;activeFinding=undefined;}
    }
    stopNoisePreview();closeNoiseEditor();
  }

  function removeNoise(id:string) {
    noiseSelections=noiseSelections.filter(item=>item.id!==id);
    if (editingNoise===id) closeNoiseEditor();
    if (activeRepair===id) activeRepair='';
    stopNoisePreview();
  }

  function applyNoiseSuggestion(row:ReviewItem) {
    focusNoiseRow(row);
    if (noiseDraftId===row.id) saveNoise();
  }

  function noiseComparisonKey(candidate?:NoiseSelection) {
    return candidate ? JSON.stringify(['spectral',cleanupKey,candidate]) : '';
  }

  function noiseComparisonReady(key:string) {
    return Boolean(key && compareResult?.removed && compareResult.key===key);
  }

  async function renderNoiseComparison(candidate?:NoiseSelection) {
    if (!candidate || compareBusy || batchBusy) return;
    const key=noiseComparisonKey(candidate);
    const version=selectionVersion;
    compareBusy=true;cleanupError='';noiseError='';
    try {
      const result=await api<{before:string;after:string;removed:string}>('noise-comparison',{...cleanupRequest,candidate});
      if (version!==selectionVersion || (key!==rowNoiseKey && key!==editorNoiseKey)) return;
      clearCompare();compareSide='after';
      compareResult={...result,key,label:candidate.label ? findingTitle(candidate.label) : 'Noise selection',
        start:candidate.apply_scope==='global' ? playhead : Math.max(0,candidate.t0_sec-.2)};
    } catch (cause) {
      if (version===selectionVersion) cleanupError=noiseError=String(cause instanceof Error ? cause.message : cause);
    } finally {compareBusy=false;}
  }

  async function findSimilarNoise() {
    if (findingSimilar || !validNoiseDraft) return;
    const version=selectionVersion, start=noise.t0_sec, end=noise.t1_sec;
    findingSimilar=true;noiseError='';noiseMatchMessage='';
    try {
      const {findings}=await api<{findings:Finding[]}>('noise-matches?'+new URLSearchParams({...keys,track,start:String(start),end:String(end)}));
      if (version!==selectionVersion) return;
      const known=analysis?.findings ?? [];
      const added=findings.filter(item=>!known.some(before=>before.kind===item.kind && item.start<before.end && before.start<item.end));
      analysis={duration:repairDuration,windows:analysis?.windows ?? 0,findings:[...known,...added]};
      noiseMatchMessage=added.length ? `${added.length} more ${added.length===1?'spot':'spots'} in the cleanup list.`
        : findings.length ? 'Similar spots are already in the cleanup list.' : 'No other quiet spots matched this sample.';
    } catch (cause) {
      if (version===selectionVersion) noiseError=String(cause instanceof Error ? cause.message : cause);
    } finally {if (version===selectionVersion) findingSimilar=false;}
  }

  async function auditionNoise(mode:'remove'|'solo') {
    const version=selectionVersion;
    const build=()=>JSON.stringify({...cleanupRequest,...(mode==='solo'?{noise}:{})});
    const payload=build();
    noiseBusy=true;
    try {
      const response=await fetch('/api/albums/noise-audition?mode='+mode,{method:'POST',
        headers:{'Content-Type':'application/json'},body:payload});
      if (!response.ok) throw new Error((await response.json()).detail);
      const blob=await response.blob();
      if (version!==selectionVersion || !player || payload!==build()) return;
      const next=URL.createObjectURL(blob);
      restorePlayhead(next, mode==='solo' && noise.apply_scope==='selection' ? noise.t0_sec : undefined, true);
      if (noiseURL) URL.revokeObjectURL(noiseURL);
      noiseURL=next;
      compareActive=false;
      noiseMode=mode;
      status=mode==='solo'?'Solo noise selection':'All enabled noise selections audition';
    } finally {noiseBusy=false;}
  }

  async function runAlbum(tagsOnly = false) {
    if (busy || !writable || !tracks.length) return;
    const request = copy({...settings, replace:!tagsOnly, metadata_only:tagsOnly});
    const jobKey = draftKey;
    busy = true; results = []; exportError = ''; exportStatus = 'Submitting album…';
    try {
      if (tagsOnly && !(await api<{can_update_tags:boolean}>('preflight', request)).can_update_tags) {
        throw new Error('Process the full album before updating its MP3 tags.');
      }
      const {id} = await api<{id:string}>('jobs', request);
      for (;;) {
        const job = await api<AlbumJob>('jobs/' + id);
        const trackProgress = job.track_number ? ` ${job.track_number} of ${request.tracks.length}` : '';
        if (draftKey === jobKey) exportStatus = job.stage + trackProgress + (job.track ? ' · ' + job.track : '');
        if (job.status === 'failed') throw new Error(job.error || 'Album processing stopped before completion.');
        if (job.status === 'complete') {
          if (draftKey === jobKey) {
            results = job.results || [];
            try { await refreshLibrary(); }
            catch { if (draftKey === jobKey) exportError = 'Files were saved, but the library could not refresh.'; }
            if (draftKey === jobKey) exportStatus = tagsOnly ? 'MP3 tags updated.' : 'Album processed. All outputs published.';
          }
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 800));
      }
    } catch (cause) {
      if (draftKey === jobKey) {
        exportStatus = '';
        exportError = cause instanceof Error ? cause.message : String(cause);
      }
    } finally { busy = false; }
  }

  onMount(() => {
    void action(async () => {
      [locations, presets] = await Promise.all([api<typeof locations>('locations'), api<Preset[]>('presets')]);
      let position: LibraryPosition | null = null;
      try { position = JSON.parse(localStorage.getItem(positionKey) ?? 'null'); }
      catch { /* Use the first available storage location. */ }
      location = locations.find(item => item.available && item.name === position?.location)?.name
        ?? locations.find(item => item.available)?.name ?? '';
      await Promise.all([
        selectLocation(position && location === position.location ? position : undefined),
        fetch('/api/profiles', {cache:'no-store'}).then(async catalog => {
          if (!catalog.ok) throw new Error('Could not load loudness profiles');
          profiles = (await catalog.json()).items.filter((item: LoudnessProfile) =>
            Number.isFinite(item.meta.lufs) && Number.isFinite(item.meta.tp));
        }),
        fetch('/api/library/noise_filters?origin=all').then(async catalog => {
          if (catalog.ok) noisePresets=(await catalog.json()).items.filter((item:{noise:unknown})=>item.noise);
        }),
      ]);
    });
    return () => { removeSeekListener?.();clearCompare();for(const item of Object.values(cleanupAudio)) URL.revokeObjectURL(item.url);if (noiseURL) URL.revokeObjectURL(noiseURL);cancelAnimationFrame(animation);engine?.close(); };
  });
</script>

{#snippet folderList(items: Folder[], selected: string, name: string, choose: (name: string) => void)}
  <section class="library-section" aria-label={name}>
    <h3>{name}</h3>
    <ul class="library-list">
      {#each items as item (item.name)}
        <li><button aria-pressed={item.name === selected} onclick={() => choose(item.name)}>
          <span>{item.name}</span>
          {#if item.pending === null}<small>Status unavailable</small>
          {:else if !item.playable}<small>No playable WAVs</small>
          {:else if item.pending}<small class="pending">{item.pending} to process</small>
          {:else}<small class="delivered">Processed</small>{/if}
        </button></li>
      {:else}<li class="empty-list">{loadingLibrary ? 'Loading…' : `No ${name.toLowerCase()} found`}</li>{/each}
    </ul>
  </section>
{/snippet}

<main>
  <aside>
    <a class="app-brand" href="/"><img src="/static/img/sonustemper256.png" alt="">SonusTemper</a>
    <div class="studio-location" aria-current="page">Album Studio</div>
    <div class="library-browser">
    <div class="library-head"><h2>Production library</h2><button onclick={() => action(refreshLibrary)}>Refresh</button></div>
    <label>Storage<select bind:value={location} onchange={() => action(selectLocation)}><option value="">Choose storage</option>{#each locations as item}<option value={item.name} disabled={!item.available}>{item.name}{item.available ? '' : ' · offline'}</option>{/each}</select></label>
    {#if location}
      {@render folderList(artists, artist, 'Artists', name => {
        if (name !== artist) {artist = name; void action(selectArtist);}
      })}
    {/if}
    {#if artist}
      {@render folderList(albums, album, 'Albums', name => {
        if (name !== album) {album = name; void action(selectAlbum);}
      })}
    {/if}
    {#if album}
      <section class="library-section" aria-label="Songs">
        <h3>Songs</h3>
        {#if library.raw?.length && library.mastered?.length}
          <div class="source-picker" role="group" aria-label="Start from">
            <button aria-pressed={source === 'raw'} onclick={() => {if (source !== 'raw') {source = 'raw'; void action(selectStage);}}}>Raw WAVs</button>
            <button aria-pressed={source === 'mastered'} onclick={() => {if (source !== 'mastered') {source = 'mastered'; void action(selectStage);}}}>Existing masters</button>
          </div>
        {/if}
        <ul class="library-list tracks">
          {#each tracks as item (item)}
            {@const missing = library.delivery?.[item]}
            <li><button aria-pressed={item === track} disabled={readyDraftKey !== draftKey} onclick={() => {if (track !== item) {rememberTrack(); track = item; void action(selectTrack);}}}>
              <span>{item.replace(/\.wav$/i, '')}</span>
              {#if missing?.length}<small class="pending">Missing {missing.join(', ')}</small>
              {:else if missing}<small class="delivered">Processed</small>{/if}
            </button></li>
          {:else}<li class="empty-list">{loadingLibrary ? 'Loading…' : 'No WAVs in this album'}</li>{/each}
        </ul>
      </section>
    {/if}
    </div>
    <footer class="sidebar-footer"><button class="theme-settings-link" type="button" data-theme-settings aria-haspopup="dialog">Settings</button></footer>
  </aside>

  <section class="workspace" aria-label="Album workflow">
    <div id="album-player" class="transport" class:compact={compactPlayer}>
      <div class="transport-title">
        <div><small>{artist || 'Album Studio'}</small><h1>{album || 'Production library'}</h1><p>{track ? track.replace(/\.wav$/i, '') : loadingLibrary ? 'Loading library…' : 'Select an album with raw or mastered WAVs'}</p></div>
        {#if tracks.length}
          <div class="album-delivery"><strong>{tracks.length} {tracks.length === 1 ? 'song' : 'songs'}</strong><small>{pendingTracks ? `${pendingTracks} to process` : 'All processed'}</small></div>
        {/if}
      </div>
      {#if track}
        <div class="player-panel">
          <div class="player-audio">
            <div class="wave-stack">
              <div class="waveform" bind:this={waveform} aria-label="Track waveform"></div>
              <svg class="scope" class:clipped={outputClipped} viewBox="0 0 600 100" preserveAspectRatio="none"
                aria-label={outputClipped ? 'Live output waveform, clipping detected' : 'Live output waveform'} role="img">
                <path class="scope-wave" d={scopePath}/>
                <path class="clip-lines" d="M0 2H600 M0 98H600"/>
              </svg>
              {#if repairRange && repairDuration > 0 && source === 'raw'}
                <div class="repair-overlay" aria-hidden="true" style:left={`${Math.min(100,repairRange.start/repairDuration*100)}%`}
                  style:width={`${Math.max(0,Math.min(100,(repairRange.end-repairRange.start)/repairDuration*100))}%`}></div>
              {/if}
            </div>
            {#if source === 'raw'}
              <div class="repair-timeline"><RepairTimeline {repairs} findings={analysis?.findings ?? []} duration={repairDuration}
                range={repairRange} selected={activeRepair} enabled={cleanupEnabled} selectFinding={chooseFinding} selectFix={chooseRepair} setRange={setRepairRange}/></div>
            {/if}
            {#if waveError}<small>Waveform unavailable. Playback controls are available below.</small>{/if}
            <audio controls preload="metadata" bind:this={player} onloadedmetadata={() => {if (playbackURL===audioURL && player) sourceDuration=player.duration;}}
              ontimeupdate={updatePlayhead} onplay={startMonitoring} onemptied={() => {meterChannels=emptyMeters();scopePath='';}}
              onerror={()=>{if (playingCompare) {compareActive=false;cleanupError='Could not load the comparison. Render it again.';}}} src={playbackURL}></audio>
          </div>
          <StereoMeter channels={meterChannels} reset={() => meterChannels=emptyMeters()} />
        </div>
        <div class="row transport-controls">
          <button aria-pressed={compactPlayer} onclick={() => compactPlayer=!compactPlayer}>{compactPlayer ? "Expand player" : "Compact player"}</button>
          <div class="play-source" role="group" aria-label="Playback source">
            <button aria-pressed={!playingCompare && !noiseMode && audibleMode==='original'} onclick={() => setPlayback('original')}>Original</button>
            <button aria-pressed={!playingCompare && !noiseMode && audibleMode==='cleanup'} disabled={!postReady} title={postReady?'Cleanup only, with no live EQ':'Available once cleanup is prepared'} onclick={() => setPlayback('cleanup')}>Post Cleanup</button>
            <button aria-pressed={!playingCompare && !noiseMode && audibleMode==='live'} disabled={source !== 'raw'} onclick={listenLive}>Live Monitor</button>
          </div>
          {#if noiseMode}<small>{noiseMode==='solo'?'Solo noise':'Filtered track'} audition</small>{/if}
          {#if cleanupBusy}<small role="status">Preparing Post Cleanup…</small>
          {:else if !postReady && canRenderCleanup}
            <button type="button" onclick={renderPostCleanup} title="Prepare a WAV with cleanup and noise removal, without EQ or loudness changes">{cleanupAudio[cleanupSong]?'Update Post Cleanup':'Prepare Post Cleanup'}</button>
          {:else if !playingCompare && !noiseMode && audibleMode==='live'}<small>Live EQ on {postReady?'Post Cleanup':'Original'}</small>{/if}
        </div>
        {#if postError}<p class="error" role="alert">{postError}</p>{/if}
        {#if playingCompare && compareResult}
          <div class="player-comparison" role="status"><span><strong>{compareSide==='before'?'Before':compareSide==='removed'?'Removed':'After'}</strong> · {compareResult.label}</span>
            <button type="button" onclick={()=>compareActive=false}>Exit comparison</button></div>
        {/if}
      {/if}
    </div>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
    {#if track && readyDraftKey !== draftKey}<p role="status">Loading album settings…</p>{/if}
    {#if track && readyDraftKey === draftKey}
      {#if sessionSyncFailed[draftKey]}<p class="error" role="status">Could not sync album settings. Changes are kept here when browser storage is available.</p>{/if}
      {#if source === 'raw'}
      <details bind:this={cleanupPanel}><summary>Cleanup &amp; repair <input class="stage-toggle" type="checkbox" aria-label="Enable cleanup" title="Enable EQ repairs and manual cleanup. Noise removal has its own switch." bind:checked={cleanupEnabled} onclick={(event)=>event.stopPropagation()}><span class="stage-status">{cleanupEnabled ? 'Enabled' : hasNoise ? 'Noise only' : 'Off'}</span></summary>
        <div class="repair-header">
          <div><h3>Review repairs</h3><small>Review tone and noise findings here. They don’t change the audio until you apply them.</small></div>
          <button type="button" class="primary" disabled={analyzeBusy} onclick={analyzeSong}>{analyzeBusy ? 'Analyzing…' : 'Analyze full song'}</button>
        </div>
        {#if analyzeBusy}<p role="status">Scanning from start to finish. You can keep adjusting the song.</p>
        {:else if analysis}<p role="status">{analysis.windows ? `Analyzed ${timeLabel(analysis.duration)} · ` : 'Noise search · '}{analysis.findings.length ? `${analysis.findings.length} findings` : 'No findings at these thresholds'}</p>{/if}
        {#if bulkPlan.proposed.length || lastApplied}
          <div class="finding-actions">
            {#if bulkPlan.proposed.length}
              <label><span>Default cut</span><input type="range" aria-label="Suggested cut" min=".5" max="6" step=".5" bind:value={findingCut}><output>{findingCut} dB</output></label>
              <button type="button" class="primary" disabled={cleanupBusy} onclick={()=>applyFindings(true)} title="Add tonal EQ suggestions and prepare Post Cleanup. Spectral findings are reviewed individually.">Apply all EQ ({bulkPlan.proposed.length})</button>
            {/if}
            {#if lastApplied}<button type="button" onclick={undoFindings}>Undo last apply</button>{/if}
          </div>
        {/if}
        {#if appliedMessage}<p class="applied-message" role="status">{appliedMessage}</p>{/if}
        {#if cleanupError}<p class="error" role="alert">{cleanupError}</p>{/if}
        {#if cleanupVersion === 1}<small>Saved legacy chain. <button type="button" onclick={upgradeCleanup}>Use repair before EQ</button></small>{/if}
        {#if reviewQueue.length}
          <CleanupQueue items={reviewQueue} active={activeRow} {cleanupEnabled} {noiseEnabled} ignored={(item)=>ignoredFindings.includes(findingKey(item))}
            select={(item)=>selectReviewItem(item)} editor={reviewEditor}/>
        {/if}
        {#if bulkPlan.proposed.length || appliedSuggestionIds.length}
          <details class="custom-repair"><summary>Batch audition</summary>
            <div class="repair-row-actions">
              {#if bulkPlan.proposed.length}
                <button type="button" disabled={compareBusy || Boolean(batchBusy)} onclick={()=>renderBatchComparison('pending')}>{batchBusy==='pending'?'Preparing full song…':`Preview all (${bulkPlan.proposed.length})`}</button>
              {/if}
              {#if appliedSuggestionIds.length}
                <button type="button" disabled={compareBusy || Boolean(batchBusy)} onclick={()=>renderBatchComparison('applied')}>{batchBusy==='applied'?'Preparing full song…':'Compare applied suggestions'}</button>
              {/if}
            </div>
            {#if batchVisible}{@render comparisonButtons(true)}
            {:else}<small>Loudness-matched before and after in the main player.</small>{/if}
          </details>
        {/if}
        <details class="custom-repair" bind:open={customRepairOpen}><summary>Add a custom repair</summary>
        <div class="row repair-range-controls">
          <label>Range start (s)<input aria-label="Repair range start" type="number" min="0" max={repairDuration} step=".01" value={repairRange?.start ?? 0}
            oninput={(event) => setRepairRange({start:Math.max(0,Number(event.currentTarget.value)),end:repairRange?.end ?? Math.min(repairDuration,5)})}></label>
          <label>Range end (s)<input aria-label="Repair range end" type="number" min="0" max={repairDuration} step=".01" value={repairRange?.end ?? ''}
            oninput={(event) => setRepairRange({start:repairRange?.start ?? 0,end:Math.min(repairDuration,Math.max(0,Number(event.currentTarget.value)))})}></label>
          <button type="button" aria-pressed={loopRange} disabled={!repairRange || repairRange.end<=repairRange.start} onclick={() => {
            loopRange=!loopRange;if (loopRange && repairRange) seekTo(repairRange.start);
          }}>Loop range</button>
          <button type="button" disabled={!repairRange} onclick={() => {repairRange=undefined;activeFinding=undefined;loopRange=false;}}>Clear range</button>
        </div>
        <div class="row repair-range-controls">
          <label>Correction<select aria-label="Correction type" bind:value={manualKind}>{#each Object.entries(repairTypes) as [kind, config]}<option value={kind}>{config.title}</option>{/each}</select></label>
          <button type="button" disabled={!repairRange || repairRange.end-repairRange.start < .05} onclick={() => setFixScope(manualKind,true)}>Add to range</button>
          <button type="button" onclick={() => setFixScope(manualKind,false)}>Add to whole song</button>
        </div>
        </details>
        <details class="repair-manual"><summary>Manual cleanup tools</summary>
        <p>Use Post Cleanup in the main player to hear these changes on their own.</p>
        <div class="manual-tools">
          <div class="manual-tonal">
            <h3>Tonal adjustments</h3>
            {#each treatments.filter(item=>item.id!=='ai_reverb_reduce') as tool}
              <div class="treatment-option">
                <label class="check"><input type="checkbox" bind:checked={corrections[tool.id].enabled} onchange={() => cleanupEnabled=true}> {tool.title}</label>
                <input aria-label={tool.title + ' amount'} type="range" min="-6" max="4" step=".1" bind:value={corrections[tool.id].value} disabled={!corrections[tool.id].enabled}><output>{corrections[tool.id].value} dB</output>
              </div>
            {/each}
          </div>
          <details class="manual-tail">
            <summary>Stereo &amp; tail shaping <span class="stage-status">{corrections.ai_reverb_reduce.enabled?'On':'Off'}</span></summary>
                <label class="check"><input type="checkbox" bind:checked={corrections.ai_reverb_reduce.enabled} onchange={() => cleanupEnabled=true}> Enable tail shaping</label>
                {#each reverbControls as option}
                  <div class="treatment-option">
                    <label class="check"><input type="checkbox" disabled={!corrections.ai_reverb_reduce.enabled} checked={Boolean(reverbConfig[option.toggle])} onchange={(event)=>reverbConfig[option.toggle]=event.currentTarget.checked}> {option.title}</label>
                    <input aria-label={option.title} type="range" min={option.min} max={option.max} step={option.step}
                      value={Number(reverbConfig[option.key])} disabled={!corrections.ai_reverb_reduce.enabled || !reverbConfig[option.toggle]}
                      oninput={(event)=>reverbConfig[option.key]=+event.currentTarget.value}>
                    <output>{reverbConfig[option.key]} {option.unit}</output>
                  </div>
                {/each}
                <label class="gate-ratio">Gate ratio<input type="number" min="1.5" max="4" step=".1" disabled={!corrections.ai_reverb_reduce.enabled || !reverbConfig.enable_tail_gate} bind:value={reverbConfig.gate_ratio}></label>
                <small>Shapes the mix; it does not detect or isolate reverb.</small>
          </details>
          {#if cleanupVersion === 2}<div class="manual-voice"><VoiceControls bind:voice={voiceControls} onEdit={() => cleanupEnabled=true}/></div>{/if}
        </div>
        </details>
        {@render noisePanel()}
      </details>
      {#snippet comparisonButtons(batch=false,spectral=false)}
        <div class="compare-switch" role="group" aria-label={batch?'Batch comparison':spectral?'Compare this noise selection':'Compare this repair'}>
          {#if batch}<strong>{compareResult?.count} {compareResult?.batch==='pending'?'proposed':'applied'} fixes</strong>{/if}
          <button type="button" aria-pressed={playingCompare && compareSide==='before'} onclick={()=>switchComparison('before')}>Before</button>
          <button type="button" aria-pressed={playingCompare && compareSide==='after'} onclick={()=>switchComparison('after')}>After</button>
          {#if spectral}
            <button type="button" aria-pressed={playingCompare && compareSide==='removed'} onclick={()=>switchComparison('removed')}>Removed</button>
            <small>Same playback gain</small>
          {:else}<small>Loudness matched</small>{/if}
          <a href="#album-player">Main player ↑</a>
        </div>
      {/snippet}
      {#snippet reviewEditor(row:ReviewItem)}
        {@const fix=row.fix}
        {@const finding=row.findings[0]}
        {#if isNoiseFinding(row.kind)}
          {@const item=noiseForRow(row)}
          {#if item}
          <div class="repair-row-content">
            <div class="repair-row-settings">
              <strong>{item.f_low}–{item.f_high} Hz · {item.band_depth_db} dB max reduction</strong>
              {#if finding}
                <small>{row.kind==='quiet_fuzz' ? `Steady broadband energy at about ${finding.evidence_db} dBFS. Check that this isn’t a wanted tail.`
                  : `Persistent narrow line about ${finding.evidence_db} dB above neighboring frequencies. Check that it isn’t a musical note.`}</small>
              {/if}
              <div class="repair-row-actions">
                <button type="button" onclick={()=>focusNoiseRow(row,true)}>Edit spectrogram box</button>
                {#if row.noise}<button type="button" onclick={()=>removeNoise(row.id)}>Remove fix</button>
                {:else}
                  {#if !findingIgnored}
                    <button type="button" class="primary" disabled={!validNoise(item) || noiseSelections.length>=24} onclick={()=>applyNoiseSuggestion(row)}>Apply selection</button>
                  {/if}
                  <button type="button" onclick={toggleIgnoredFinding}>{findingIgnored?'Restore suggestion':'Skip finding'}</button>
                {/if}
              </div>
            </div>
            <div class="repair-row-audition">
              <small>Compare this one edit. Removed plays the actual audio taken out.</small>
              <div class="repair-row-actions">
                {#if noiseComparisonReady(rowNoiseKey)}{@render comparisonButtons(false,true)}
                {:else}<button type="button" disabled={compareBusy || Boolean(batchBusy) || !validNoise(item)}
                  onclick={()=>renderNoiseComparison(item)}>{compareBusy?'Preparing…':'Preview comparison'}</button>{/if}
              </div>
            </div>
          </div>
          {/if}
        {:else if row.kind==='peak' && finding}
          <div class="row repair-row-actions"><small>{finding.evidence_db} dBFS. Near full scale isn’t proof of clipping.</small><button type="button" onclick={hearFinding}>Listen to original</button></div>
        {:else}
          <div class="repair-row-content">
            <div class="repair-row-settings">
              {#if fix}
                <label class="check repair-toggle"><input type="checkbox" aria-label={`Enable fix ${row.number}`} bind:checked={fix.enabled}
                  onchange={()=>{if (fix.enabled) cleanupEnabled=true;}}> Enable this fix</label>
                <label class="inline-control"><span>Cut</span><input aria-label={`Fix ${row.number} amount`} type="range" min="0" max="12" step=".1" bind:value={fix.amount}><output>{fix.amount} dB</output></label>
                <label class="inline-control"><span>Frequency</span><input aria-label={`Fix ${row.number} frequency`} type="range"
                  min={fix.kind==='rumble'?20:fix.kind==='presence'?2000:6500} max={fix.kind==='rumble'?180:fix.kind==='presence'?6500:16000} step="5" bind:value={fix.frequency}><output>{fix.frequency} Hz</output></label>
              {:else if proposedFix}
                <label class="inline-control"><span>This cut</span><input aria-label="Finding cut" type="range" min=".5" max="6" step=".5" value={itemCut} oninput={(event)=>rowCut=+event.currentTarget.value}><output>{itemCut} dB</output></label>
                <small>Centered at {proposedFix.frequency} Hz</small>
              {/if}
              {#if finding}<small>Measured band: {finding.evidence_db} dB relative to the full signal{row.findings.length>1 ? ` · ${row.findings.length} linked findings` : ''}.</small>{/if}
              {#if fix && cleanupEnabled && fix.enabled && fix.amount && boostAtFix>.5}
                <small class="repair-overlap">Tone EQ adds about {boostAtFix.toFixed(1)} dB at {fix.frequency} Hz, where this fix cuts.</small>
              {/if}
              {#if fix}
                <details class="item-scope"><summary>Edit time range</summary>
                  <div class="row repair-range-controls">
                    <label>Start (s)<input type="number" min="0" max={repairDuration} step=".01" value={repairRange?.start ?? fix.start}
                      oninput={(event)=>setRepairRange({start:Math.max(0,Number(event.currentTarget.value)),end:repairRange?.end ?? fix.end ?? repairDuration})}></label>
                    <label>End (s)<input type="number" min="0" max={repairDuration} step=".01" value={repairRange?.end ?? fix.end ?? ''}
                      oninput={(event)=>setRepairRange({start:repairRange?.start ?? fix.start,end:Math.min(repairDuration,Math.max(0,Number(event.currentTarget.value)))})}></label>
                    <button type="button" disabled={!repairRange || repairRange.end-repairRange.start<.05 || (fix.start===repairRange.start && fix.end===repairRange.end)} onclick={()=>setFixScope(fix.kind,true,fix.id)}>Save range</button>
                    {#if fix.end!==null}<button type="button" onclick={()=>setFixScope(fix.kind,false,fix.id)}>Use whole song</button>{/if}
                  </div>
                </details>
              {/if}
              <div class="repair-row-actions">
                {#if fix}<button type="button" onclick={()=>removeReviewFix(row)}>Remove fix</button>
                {:else if proposedFix}
                  {#if !findingIgnored}<button type="button" class="primary" disabled={repairs.length>=maxRepairs} onclick={()=>applyFindings()}>Apply finding</button>{/if}
                  <button type="button" onclick={toggleIgnoredFinding}>{findingIgnored?'Include in Apply all':'Skip finding'}</button>
                {/if}
              </div>
            </div>
            <div class="repair-row-audition">
              {#if selectedCurve}
                <div class="row-curve"><svg viewBox="0 0 600 150" preserveAspectRatio="none" aria-label="Response of this repair" role="img">
                  <path class="grid" d="M0 75H600 M200 0V150 M400 0V150"/>
                  <path class="repair-curve" class:proposed={!fix} d={selectedCurve}/>
                </svg><small><span>20 Hz</span><span>200 Hz</span><span>2 kHz</span><span>20 kHz</span></small></div>
              {/if}
              <div class="repair-row-actions">
                {#if rowCompareReady}{@render comparisonButtons()}
                {:else}<button type="button" disabled={!canCompare || compareBusy || Boolean(batchBusy)} onclick={renderComparison}>{compareBusy?'Preparing…':'Preview before / after'}</button>{/if}
                {#if row.end!==null}<button type="button" aria-pressed={loopRange} onclick={()=>{loopRange=!loopRange;if (loopRange) seekTo(row.start);}}>Loop passage</button>{/if}
              </div>
            </div>
          </div>
        {/if}
      {/snippet}
      <details open><summary>Tone &amp; Stereo <input class="stage-toggle" type="checkbox" aria-label="Enable Realtime EQ and stereo" bind:checked={eqEnabled} onclick={(event)=>event.stopPropagation()} onchange={() => {if (eqEnabled) listenLive();}}><span class="stage-status">{eqEnabled ? 'Enabled' : 'Off'}</span></summary>
        <div class="tone-scope">
          <label class="check"><input type="checkbox" checked={shareTone} aria-describedby="tone-scope-note"
            onchange={(event)=>setToneScope(event.currentTarget.checked)}> Apply to full album</label>
          <small id="tone-scope-note">{shareTone ? 'All songs use this EQ and stereo setup. Uncheck to edit this song separately.' : 'Share this EQ and stereo setup across all songs. Cleanup stays per song.'}</small>
        </div>
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
          {#if presetBase}<small>Preset compression is applied when processing the album, not in Live Monitor.</small>{/if}
        {/snippet}
        {#snippet legacyVoice()}
          <VoiceControls bind:voice={voiceControls} onEdit={() => {eqEnabled=true;listenLive();}}/>
        {/snippet}
        <EqPanel bind:bands {presetControls} {stereoControls} onEdit={markCustom} {spectrum} curve={eqCurve || 'M0 75H600'}
          combined={combinedCurve} legacyVoice={cleanupVersion===1 ? legacyVoice : undefined}/>
      </details>
      {#snippet noisePanel()}
      <details class="repair-manual noise-panel" bind:this={noisePanelElement}><summary>Noise removal <input class="stage-toggle" type="checkbox" aria-label="Enable Noise removal" bind:checked={noiseEnabled} onclick={(event)=>event.stopPropagation()} onchange={stopNoisePreview}><span class="stage-status">{hasNoise ? `${noiseSelections.filter(item=>item.enabled).length} active` : 'Off'}</span></summary>
        <p>{cleanupVersion===2 ? 'Noise removal runs before repairs and Tone & Stereo.' : 'Legacy session: noise removal runs after the EQ chain.'} Drag a box or open a suggestion from the cleanup list.</p>
        <div class="spectrogram-toolbar">
          <div class="switch-pair" role="group" aria-label="Frequency scale">
            <button type="button" aria-pressed={specScale==='log'} onclick={()=>specScale='log'}>Log</button>
            <button type="button" aria-pressed={specScale==='lin'} onclick={()=>specScale='lin'}>Linear</button>
          </div>
          <label>Time zoom <input aria-label="Spectrogram time zoom" type="range" min="1" max="48" step="1" value={specZoom}
            oninput={event=>zoomSpectrum(+event.currentTarget.value)} onchange={loadSpectrumDetail}><output>{specZoom}×</output></label>
          <button type="button" disabled={specZoom===1} onclick={()=>{zoomSpectrum(1);loadSpectrumDetail();}}>Fit song</button>
          <label>Display range <input aria-label="Spectrogram display range" type="number" min="40" max="160" step="5" value={specRange}
            onchange={(event)=>specRange=Math.max(40,Math.min(160,Number(event.currentTarget.value)||120))}></label>
          <output class="spec-cursor">{cursor && sourceDuration ? `${timeLabel(cursor.x*sourceDuration)} · ${frequencyAt(cursor.y)} Hz` : 'Time · Frequency'}</output>
        </div>
        <div class="spectro-viewport">
            <div class="spectrogram" role="region" aria-label="Drag to select a time and frequency region"
              style:width={`${specZoom*100}%`} style:left={`${-specStart*specZoom*100}%`}
              onpointerdown={(event)=>{if (event.button!==0 || !(sourceDuration>0) || specFailed || specLoading) return; dragStart=selectionPoint(event);draggingNoise=true;event.currentTarget.setPointerCapture(event.pointerId);}}
              onpointermove={drawNoise} onpointerleave={() => cursor=undefined} onpointerup={finishNoise} onpointercancel={()=>{dragStart=undefined;draggingNoise=false;selection=undefined;}}>
              <img loading="lazy" alt="Source track spectrogram" src={specImage} onload={()=>specLoading=false} onerror={()=>{specFailed=true;specLoading=false;}}>
              <div class="spec-grid" aria-hidden="true">
                {#each specScale==='lin' ? [4800,9600,14400,19200] : [100,1000,10000] as freq}
                  <span style:top={`${frequencyY(freq)*100}%`}></span>
                {/each}
              </div>
              {#if sourceDuration>0}
                {#each reviewQueue.filter(row=>isNoiseFinding(row.kind) && !row.noise && row.id!==noiseDraftId && !ignoredFindings.includes(findingKey(row.findings[0]))) as row (row.id)}
                  {@const suggestion=noiseForFinding(row.findings[0])}
                  {#if suggestion}
                    {@const box=asNoiseBox(suggestion)}
                    <div class="suggested-noise-box" style:left={box.x*100+'%'} style:top={box.y*100+'%'}
                      style:width={(box.endX-box.x)*100+'%'} style:height={(box.endY-box.y)*100+'%'} aria-hidden="true"></div>
                  {/if}
                {/each}
                {#each noiseSelections as item,index (item.id)}
                  {@const box=asNoiseBox(item)}
                  <div class="saved-noise-box" class:bypassed={!noiseEnabled||!item.enabled} class:focused={editingNoise===item.id}
                    style:left={`${box.x*100}%`} style:top={`${box.y*100}%`} style:width={`${(box.endX-box.x)*100}%`}
                    style:height={`${(box.endY-box.y)*100}%`} aria-hidden="true"><span>{index+1}</span></div>
                {/each}
                <div class="spectrogram-playhead" aria-hidden="true" style:left={`${Math.min(100,playhead/sourceDuration*100)}%`}></div>
              {/if}
              {#if visibleSelection}<div class="marquee" style:left={visibleSelection.x*100+'%'} style:top={visibleSelection.y*100+'%'}
                style:width={(visibleSelection.endX-visibleSelection.x)*100+'%'} style:height={(visibleSelection.endY-visibleSelection.y)*100+'%'}></div>{/if}
            </div>
            {#if specLoading || specFailed}<span class="spectro-status" role="status">{specFailed ? 'Could not load the spectrogram.' : 'Drawing spectrogram…'}</span>{/if}
        </div>
        <div class="spec-overview">
          <img loading="lazy" alt="Full-song spectrogram overview" src={specOverview}>
          <div class="overview-window" style:left={`${specStart*100}%`} style:width={`${100/specZoom}%`} aria-hidden="true"></div>
          {#if sourceDuration>0}<div class="spectrogram-playhead" aria-hidden="true" style:left={`${Math.min(100,playhead/sourceDuration*100)}%`}></div>{/if}
          <input aria-label="Scrub full song" type="range" min="0" max={sourceDuration||1} step=".01" value={playhead}
            disabled={sourceDuration<=0} oninput={(event)=>{const time=+event.currentTarget.value;centerSpectrum(time);seekTo(time);}}>
        </div>
        <div class="overview-labels"><small>{timeLabel(specStart*sourceDuration)} – {timeLabel(specEnd*sourceDuration)} in view</small><small>Drag overview to scrub and move the close-up</small></div>
        {#if noiseError}<p class="error" role="alert">{noiseError}</p>{/if}
        <div class="noise-workbench">
          <div class="noise-list">
            <div class="fix-heading"><h3>Noise selections <small>{noiseSelections.length}</small></h3><button type="button" onclick={()=>editNoise()}>New selection</button></div>
            {#each noiseSelections as item,index (item.id)}
              <div class="noise-item">
                <input type="checkbox" aria-label={`Enable noise selection ${index+1}`} bind:checked={item.enabled} onchange={stopNoisePreview}>
                <button type="button" aria-pressed={editingNoise===item.id} onclick={()=>editNoise(item)}>
                  <strong>{index+1}. {item.f_low}–{item.f_high} Hz</strong>
                  <small>{item.apply_scope==='global' ? 'Whole song' : rangeLabel({start:item.t0_sec,end:item.t1_sec})} · {item.band_depth_db} dB cut</small>
                </button>
                <button type="button" aria-label={`Remove noise selection ${index+1}`} onclick={()=>removeNoise(item.id)}>Remove</button>
              </div>
            {:else}<p>No selections yet. Drag on the spectrogram or enter a band manually.</p>{/each}
            <button type="button" disabled={!hasNoise||noiseBusy} onclick={()=>action(()=>auditionNoise('remove'))}>{noiseBusy ? 'Rendering…' : 'Hear filtered track'}</button>
            <small>Plays all enabled selections. Where they overlap, the cuts stack.</small>
          </div>
          {#if noiseEditorOpen}
          <div class="noise-editor">
            <h3>{editingNoise ? 'Edit selection' : noiseDraftId ? 'Suggested selection' : 'New selection'}</h3>
            <div class="switch-pair" role="group" aria-label="Noise selection scope">
              <button type="button" aria-pressed={noise.apply_scope==='selection'} onclick={()=>noise.apply_scope='selection'}>Time range</button>
              <button type="button" disabled={noise.method==='profile'} aria-pressed={noise.apply_scope==='global'} onclick={()=>noise.apply_scope='global'}>Whole song</button>
            </div>
            {#if noise.apply_scope==='selection'}<div class="row"><label>Start (s)<input type="number" min="0" max={sourceDuration} step=".01" bind:value={noise.t0_sec}></label>
              <label>End (s)<input type="number" min="0" max={sourceDuration} step=".01" bind:value={noise.t1_sec}></label></div>{/if}
            <div class="row"><label>Low frequency (Hz)<input type="number" min="20" max="20000" bind:value={noise.f_low}></label>
              <label>High frequency (Hz)<input type="number" min="20" max="20000" bind:value={noise.f_high}></label></div>
            <label class="inline-control"><span>Filter depth</span><input aria-label="Filter depth" type="range" min="0" max="30" bind:value={noise.band_depth_db}><output>{noise.band_depth_db} dB</output></label>
            <label>Method<select aria-label="Noise removal method" value={noise.method ?? 'legacy'} onchange={(event)=>{
              const method=event.currentTarget.value as NoiseSettings['method'];
              noise={...noise,method,...(method==='profile' ? {apply_scope:'selection' as const} : {})};
            }}><option value="spectral">Spectral cut</option><option value="profile">Sample noise in this box</option><option value="legacy">EQ band cut (legacy)</option></select></label>
            {#if noise.method==='profile'}
              <small>Use a box containing only unwanted sound for the noise profile.</small>
              <div class="repair-row-actions">
                <button type="button" disabled={findingSimilar||!validNoiseDraft||noise.t1_sec-noise.t0_sec<.3||noise.t1_sec-noise.t0_sec>10}
                  onclick={findSimilarNoise}>{findingSimilar?'Searching full song…':'Find similar fuzz'}</button>
                {#if noiseMatchMessage}<small role="status">{noiseMatchMessage}</small>{/if}
              </div>
            {/if}
            {#if !noise.method || noise.method==='legacy'}<details class="noise-advanced"><summary>Advanced filters and presets</summary>
              <label class="inline-control"><span>Denoise strength</span><input aria-label="Denoise strength" type="range" min="0" max="1" step=".01" bind:value={noise.afftdn_strength}><output>{noise.afftdn_strength}</output></label>
              <small>Denoise and high/low pass affect more than the highlighted band.</small>
              <div class="row"><label>High-pass (Hz, 0 off)<input type="number" min="0" max="20000" bind:value={noise.hp_hz}></label>
                <label>Low-pass (Hz, 0 off)<input type="number" min="0" max="20000" bind:value={noise.lp_hz}></label></div>
              <label>Noise preset<select aria-label="Noise preset" onchange={(event)=>{
                const item=noisePresets.find(preset=>preset.origin+'/'+preset.id===event.currentTarget.value);
                if(item) noise={...noise,...Object.fromEntries(Object.entries(item.noise).filter(([key,value])=>key in defaultNoise && typeof value==='number' && key!=='t0_sec' && key!=='t1_sec'))};
              }}><option value="">Manual</option>{#each noisePresets as item}<option value={item.origin+'/'+item.id}>{item.meta.title}</option>{/each}</select></label>
            </details>{/if}
            <div class="row">
              <button type="button" class="primary" disabled={!validNoiseDraft} onclick={saveNoise}>{editingNoise?'Save selection':'Add selection'}</button>
              {#if !noiseComparisonReady(editorNoiseKey)}
                <button type="button" disabled={!validNoiseDraft||compareBusy||Boolean(batchBusy)} onclick={()=>renderNoiseComparison(editorNoiseCandidate)}>Preview comparison</button>
              {/if}
              <button type="button" onclick={closeNoiseEditor}>Cancel</button>
            </div>
            {#if noiseComparisonReady(editorNoiseKey)}{@render comparisonButtons(false,true)}{/if}
          </div>
          {/if}
        </div>
      </details>
      {/snippet}
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
        <div class="album-tag-fields"><label>Album<input bind:value={albumTag}></label><label>Album artist<input bind:value={albumArtist}></label><label>Artist<input bind:value={artistTag}></label><label>Year<input type="number" min="1" max="9999" bind:value={year}></label></div>
        <div class="cover-heading"><h3>Album artwork</h3>{#if cover}<button type="button" onclick={() => cover = ''}>Clear selection</button>{/if}</div>
        {#if library.covers?.length}
          <div class="cover-grid" role="group" aria-label="Album artwork, 3000 by 3000 pixels">
            {#each library.covers as item (item.name)}
              <button type="button" class="cover-option" aria-pressed={item.name === cover} aria-label={`Use ${item.name}, ${sizeLabel(item.size)}`} title={item.name} onclick={() => cover = item.name}>
                <img alt="" loading="lazy" width="360" height="360" src={'/api/albums/artwork?'+new URLSearchParams({...keys,name:item.name,thumbnail:'true'})}>
                <span class="cover-filename" aria-hidden="true"><span>{item.name.slice(0, Math.max(0, item.name.length - 11))}</span><span>{item.name.slice(-11)}</span></span>
                <small>{sizeLabel(item.size)}</small>
              </button>
            {/each}
          </div>
        {:else}<p>No 3000 × 3000 images found in this album folder.</p>{/if}
        <h3>Album tracks</h3>
        <div class="album-tags-scroll">
          <table class="album-track-tags">
            <thead><tr><th scope="col">Track #</th><th scope="col">Title</th><th scope="col">Source file</th></tr></thead>
            <tbody>
              {#each tracks as name, index (name)}
                {@const tags = songTags(name, index)}
                <tr>
                  <td><input aria-label={`Track number for ${name}`} inputmode="numeric" value={tags.number} oninput={event => editSongTag(name, index, 'number', event.currentTarget.value)}></td>
                  <td><input aria-label={`Title for ${name}`} value={tags.title} oninput={event => editSongTag(name, index, 'title', event.currentTarget.value)}></td>
                  <td title={name}>{name}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </details>
      <details><summary>Prompts and Lyrics</summary>
        <ul class="prompt-list">
          {#each tracks as name, index (name)}
            <li>
              <div><strong>{songTags(name,index).title || name}</strong></div>
              <div class="prompt-actions">
                {#each promptButtons as {kind, label}}
                  {@const saved = library.text_status?.[name]?.[kind]}
                  <button type="button" class:saved aria-haspopup="dialog" aria-label={`${label} for ${name}, ${saved ? 'saved' : 'empty'}`} onclick={() => openPrompt(name,kind)}>
                    {label}<small>{saved ? 'Saved' : 'Empty'}</small>
                  </button>
                {/each}
              </div>
            </li>
          {/each}
        </ul>
      </details>
      <details open><summary>Finalize album</summary>
        <p>{source === 'raw' ? 'Process every song using its current settings and the album loudness targets.' : 'Use the existing mastered WAVs and apply the album loudness targets.'} Matching outputs are replaced after the full album renders. Raw recordings stay untouched.</p>
        <div class="output-folders" aria-label="Output folders">
          {#if source === 'raw'}<span><code>mastered/</code> Processed WAV</span>{/if}
          <span><code>clean/</code> Clean WAV</span>
          <span><code>normalized/</code> Final WAV</span>
          <span><code>mp3s/</code> Final MP3</span>
        </div>
        <p>Final WAVs use a new container with no source metadata. Your tags, artwork, and saved lyrics go into the MP3s.</p>
        <label class="check"><input type="checkbox" bind:checked={sidecar} disabled={busy}> Include untimed lyrics files (.lrc)</label>
        {#if !writable}<p>Choose a writable storage location to process this album.</p>{/if}
        <div class="row">
          <button class="primary" disabled={busy || !writable} onclick={() => runAlbum()}>{busy ? 'Album job running…' : `Process album (${tracks.length} ${tracks.length === 1 ? 'song' : 'songs'})`}</button>
          <button title="Update MP3 tags, cover art and lyrics without re-encoding audio" disabled={busy || !writable} onclick={() => runAlbum(true)}>Update MP3 tags only</button>
        </div>
        {#if exportStatus}<p role="status">{exportStatus}</p>{/if}
        {#if exportError}<p class="error" role="alert">{exportError}</p>{/if}
        {#each results as item}
          <div class="result"><strong>{item.track}</strong><span>{item.lufs} LUFS · WAV {item.true_peak} dBTP · MP3 {item.mp3_true_peak} dBTP{item.mp3_peak_warning ? ' · above ceiling' : ''}</span><small>{item.mp3}</small></div>
        {/each}
      </details>
    {/if}
    <p class="status" aria-live="polite">{status}</p>
  </section>
</main>

<dialog bind:this={promptDialog} class="theme-dialog prompt-dialog" aria-labelledby="prompt-title"
  oncancel={(event) => {if (promptWorking || promptLoading) event.preventDefault();}}>
  {#if promptEditor}
    <div class="prompt-heading">
      <div><h2 id="prompt-title">{promptButtons.find(item => item.kind === promptEditor?.kind)?.label}</h2><small>{promptEditor.track.replace(/\.wav$/i, '')}</small></div>
      <button type="button" class="prompt-secondary" disabled={promptWorking || promptLoading} onclick={() => promptDialog.close()}>Close</button>
    </div>
    {#if promptLoading}<p role="status">Loading saved text…</p>{/if}
    {#if promptLoaded}
      <label>Prompt<textarea rows="7" maxlength="30000" bind:value={promptText} readonly={!writable || promptWorking}></textarea></label>
      <div class="prompt-toolbar">
        <button type="button" class="prompt-secondary" disabled={promptWorking || !promptText.trim()} onclick={() => promptAction(() => copyPromptText('prompt'))}>Copy prompt</button>
        <button type="button" disabled={!writable || promptWorking} onclick={() => promptAction(() => savePrompt(promptEditor!.kind))}>Save prompt</button>
      </div>
      {#if promptEditor.kind === 'prompt'}
        <label>Submission lyrics<textarea rows="9" maxlength="30000" bind:value={submissionLyrics} readonly={promptWorking}></textarea></label>
        <div class="prompt-toolbar">
          <button type="button" class="prompt-secondary" disabled={promptWorking || !promptText.trim()} onclick={() => {
            if (!submissionLyrics.trim() || confirm('Replace the submission text in this editor with a fresh draft?')) void promptAction(formatLyrics);
          }}>Format lyric prompt</button>
          <button type="button" class="prompt-secondary" disabled={promptWorking || !submissionLyrics.trim()} onclick={() => promptAction(() => copyPromptText('lyrics'))}>Copy for DistroKid</button>
          <button type="button" disabled={!writable || promptWorking} onclick={() => promptAction(() => savePrompt('lyrics'))}>Save lyrics</button>
        </div>
        <small>Check the text against the recording, including repeated lines, before submitting.</small>
      {/if}
    {/if}
    {#if promptMessage}<p role="status">{promptMessage}</p>{/if}
    {#if promptError}<p class="error" role="alert">{promptError}</p>{/if}
  {/if}
</dialog>

<style>
  :global(*){box-sizing:border-box}
  :global(body){margin:0;min-height:100vh;background:radial-gradient(ellipse at 20% 0%,var(--frame-glow),var(--frame) 70%);color:var(--text);font:15px var(--font-ui)}
  main{width:calc(100% - clamp(1.4rem,5vw,4rem));max-width:1800px;margin:clamp(.7rem,3vw,2.5rem) auto;padding:.45rem;display:grid;grid-template-columns:minmax(220px,270px) minmax(0,1fr);gap:.65rem;background:var(--bg);border-radius:1.45rem;box-shadow:0 0 0 1px #ffffff2c}
  aside{min-width:0;align-self:start;min-height:calc(100dvh - clamp(2rem,6vw,5rem) - .9rem);display:flex;flex-direction:column;background:var(--card);padding:1rem;border-radius:1.12rem;box-shadow:var(--card-shadow)}
  .app-brand{display:flex;align-items:center;gap:.55rem;margin:0 0 .85rem;color:var(--text);text-decoration:none;font-size:1.25rem;font-weight:750;letter-spacing:-.035em}
  .app-brand img{width:2rem;height:2rem;object-fit:contain}
  .studio-location{padding:.52rem .7rem;margin-bottom:1rem;border-radius:1rem;background:var(--selected);color:var(--selected-text);font-size:.82rem;font-weight:650}
  .library-browser{padding-bottom:.55rem}
  .sidebar-footer{margin-top:auto;padding-top:.65rem;border-top:1px solid var(--line)}
  h2{margin:0;font-size:.9rem} h1{margin:.12rem 0;font-size:clamp(1.55rem,2.8vw,2.35rem);letter-spacing:-.045em;line-height:1.14;overflow-wrap:anywhere}
  small{color:var(--muted)}
  .library-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem}.library-head h2{margin:0}.library-head button{font-size:.75rem;padding:.3rem .45rem}
  p{color:var(--muted);margin:.5rem 0 1rem} label{display:grid;gap:.35rem;color:var(--muted);font-size:.83rem;margin:.55rem 0}
  select,input,textarea,button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:.55rem;padding:.48rem;min-width:0}
  select,input,textarea{width:100%} textarea{resize:vertical} button{cursor:pointer} button:not(:disabled):hover{background:var(--surface-hover)} button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)} button:disabled{opacity:.5;cursor:default}
  .sidebar-footer .theme-settings-link{background:transparent;border:0;color:var(--muted);font-size:.83rem;padding:.36rem .45rem}
  .sidebar-footer .theme-settings-link:hover{background:var(--surface);color:var(--text)}
  .library-section{margin-top:.65rem;border-top:1px solid var(--line);padding-top:.55rem}
  .library-section h3{margin:0 0 .3rem;color:var(--muted);font-size:.76rem}
  .library-list{list-style:none;display:grid;gap:.25rem;margin:0;padding:0;max-height:clamp(6rem,20vh,15rem);overflow:auto}
  .tracks{max-height:none;overflow:visible}
  .library-list button{display:grid;justify-items:start;gap:.15rem;width:100%;text-align:left;padding:.4rem .48rem;overflow-wrap:anywhere;background:transparent;border-color:transparent;font-size:.83rem}
  .library-list button[aria-pressed=true]{background:var(--accent-soft);border-color:transparent;color:var(--text);font-weight:680}
  .library-list small,.empty-list{font-size:.69rem;color:var(--muted);font-weight:550}
  .library-list .pending,.library-list .delivered{padding:.08rem .35rem;border-radius:.45rem}
  .library-list .pending{color:var(--warning);background:var(--warning-soft)}.library-list .delivered{color:var(--good);background:var(--good-soft)}
  .source-picker{display:flex;gap:.3rem;margin-bottom:.4rem}
  .source-picker button{font-size:.72rem;padding:.3rem .45rem}
  .waveform{min-height:240px;background:var(--chart);border-radius:.7rem;margin-bottom:.35rem}
  .manual-tools{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:.55rem;max-width:66rem}
  .manual-tonal,.manual-tail{background:var(--surface);border:1px solid var(--line);border-radius:.55rem;padding:.48rem .65rem}
  .manual-tail{box-shadow:none;margin:0}
  .manual-voice{grid-column:1/-1}
  .manual-tools h3{font-size:.84rem;margin:.25rem 0 .35rem}
  .treatment-option{display:grid;grid-template-columns:minmax(5.5rem,1.18fr) minmax(2.3rem,1fr) max-content;align-items:center;gap:.38rem;min-height:1.8rem}
  .treatment-option+.treatment-option{border-top:1px solid var(--line)}
  .treatment-option label{margin:.15rem 0;font-size:.76rem;color:var(--text)}
  .treatment-option input[type=range]{padding:0}
  .treatment-option output{min-width:3.5rem;font-size:.75rem}
  .gate-ratio{grid-template-columns:auto minmax(0,4rem);justify-content:start;align-items:center}
  .treatment-option output,.inline-control output{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
  .workspace{min-width:0}
  .transport,details{background:var(--card);border-radius:1.12rem;box-shadow:var(--card-shadow)}
  .transport{padding:1rem 1.15rem;margin-bottom:.65rem}
  .transport-title{display:flex;justify-content:space-between;align-items:start;gap:.8rem;margin-bottom:.55rem}
  .transport-title p{margin:.24rem 0 0}
  .album-delivery{flex:none;display:grid;gap:.12rem;min-width:6.8rem;padding:.55rem .75rem;border-radius:.7rem;background:var(--lavender);color:var(--lavender-text)}
  .album-delivery small{color:inherit}
  .transport.compact{position:sticky;top:0;z-index:2}
  audio{width:100%;height:38px}.row{display:flex;gap:.65rem;align-items:end;flex-wrap:wrap;margin:.55rem 0}.row label{flex:1;min-width:100px}
  .check{display:flex;align-items:center;gap:.4rem}.check input{width:auto}
  .tone-scope{display:flex;flex-wrap:wrap;align-items:center;gap:.28rem .65rem;margin:.1rem 0 .65rem;padding:.45rem .6rem;border:1px solid var(--line);border-radius:.5rem;background:var(--surface)}
  .tone-scope label{margin:0;font-weight:650}
  .transport-controls{align-items:center}
  .player-comparison{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.35rem;background:var(--accent-soft);color:var(--text);border:1px solid var(--line);border-radius:.45rem;padding:.42rem .6rem;font-size:.8rem}
  .player-comparison button{font-size:inherit;padding:.25rem .4rem}
  .play-source{display:flex;flex-wrap:wrap;gap:.28rem}
  .transport-controls button{font-size:.83rem;padding:.42rem .6rem}
  details{padding:.85rem 1rem;margin:.65rem 0}
  summary{cursor:pointer;font-weight:650;padding:.25rem 0} details[open] summary{margin-bottom:.65rem}
  .player-panel{display:grid;grid-template-columns:minmax(0,1fr) clamp(3.8rem,8vw,5rem);gap:.55rem}
  .player-audio{min-width:0}
  .wave-stack{position:relative;overflow:hidden;border-radius:.7rem;margin-bottom:.35rem}.scope{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
  .repair-overlay{position:absolute;inset-block:0;pointer-events:none;background:var(--accent-soft);opacity:.42;border-inline:1px solid var(--accent)}
  .scope-wave{stroke:var(--chart-line);fill:none;stroke-width:1.5}.grid{stroke:var(--chart-grid)}
  .clip-lines{fill:none;stroke:var(--danger);stroke-width:1;opacity:.25;vector-effect:non-scaling-stroke}
  .scope.clipped .clip-lines{stroke:#ee4e59;stroke-width:2;opacity:1}
  .repair-header,.fix-heading{display:flex;justify-content:space-between;align-items:center;gap:.45rem;flex-wrap:wrap}
  .repair-header h3{margin:.35rem 0;font-size:.9rem}
  .noise-workbench h3{font-size:.84rem;margin:.2rem 0 .4rem}
  .finding-actions{display:flex;flex-wrap:wrap;align-items:center;gap:.35rem .6rem;margin:.5rem 0;padding:.48rem .6rem;background:var(--surface);border:1px solid var(--line);border-radius:.55rem}
  .finding-actions label{display:flex;align-items:center;gap:.4rem;margin:0}
  .finding-actions input{width:clamp(4rem,13vw,9rem);padding:0}
  .finding-actions output{white-space:nowrap;min-width:3.5rem;font-variant-numeric:tabular-nums}
  .finding-actions button,.repair-row-actions button,.repair-range-controls button,.compare-switch button{font-size:.8rem;padding:.34rem .5rem}
  .applied-message{color:var(--good);font-size:.85rem;margin:.4rem 0}
  .custom-repair{border:1px solid var(--line);padding:.3rem .45rem;box-shadow:none;font-size:.83rem;border-radius:.4rem;margin:.6rem 0 .15rem}
  .custom-repair[open] summary{margin-bottom:.2rem}
  .repair-range-controls label{flex:0 1 auto;max-width:15rem}
  .repair-range-controls input[type=number]{width:6rem}
  .repair-row-content{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:start;gap:.75rem 1rem}
  .repair-row-settings{min-width:0}.repair-row-settings small{display:block;margin:.25rem 0}
  .repair-row-audition{min-width:0;border-inline-start:1px solid var(--line);padding-inline-start:1rem}
  .repair-row-settings .inline-control{grid-template-columns:5rem minmax(0,1fr) 4rem;margin:.18rem 0}
  .repair-toggle{margin:.2rem 0}
  .repair-row-actions,.compare-switch{display:flex;align-items:center;flex-wrap:wrap;gap:.3rem}
  .repair-row-actions{margin:.4rem 0 0}
  .compare-switch a{color:var(--accent);font-size:.75rem;padding:.12rem .2rem}
  .custom-repair > .compare-switch,.custom-repair > small{margin-top:.35rem}
  .custom-repair > small{display:block}
  .compare-switch strong{font-size:.78rem}
  .row-curve svg{display:block;width:100%;height:clamp(60px,7vw,90px);background:var(--chart);border:1px solid var(--line);border-radius:.35rem}
  .row-curve small{display:flex;justify-content:space-between;font-size:.65rem}
  .repair-curve{fill:none;stroke:var(--chart-line);stroke-width:1.8;vector-effect:non-scaling-stroke}
  .repair-curve.proposed{stroke:var(--warning);stroke-dasharray:5 4}
  .item-scope{margin:.35rem 0 0;padding:.15rem .3rem;background:transparent;box-shadow:none;font-size:.76rem}
  .item-scope[open] summary{margin-bottom:0}
  .repair-overlap{background:var(--warning-soft);padding:.4rem .5rem;border-radius:.3rem;color:var(--warning)}
  .repair-manual{border:1px solid var(--line);box-shadow:none}
  @media(max-width:700px){.repair-row-content{grid-template-columns:1fr}.repair-row-audition{padding-inline-start:0;border-inline-start:0;border-top:1px solid var(--line);padding-top:.55rem}}
  .noise-workbench{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem;margin-top:.7rem}
  .noise-editor,.noise-list{min-width:0;border:1px solid var(--line);border-radius:.55rem;padding:.5rem .65rem}
  .noise-list>small{display:block;margin-top:.35rem}
  .noise-item{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:.35rem;padding:.3rem 0;border-top:1px solid var(--line)}
  .noise-item input{width:auto}.noise-item button{font-size:.76rem;padding:.28rem .4rem}
  .noise-item button[aria-pressed]{display:grid;gap:.12rem;text-align:left}
  .noise-item small{overflow-wrap:anywhere}
  .noise-advanced{border:1px solid var(--line);border-radius:.45rem;padding:.4rem .55rem;box-shadow:none;font-size:.8rem}
  .album-tag-fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) 6rem;gap:.6rem}
  .cover-heading{display:flex;align-items:center;justify-content:space-between;gap:.5rem}
  .cover-heading button{font-size:.8rem}
  .cover-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(8rem,10rem));gap:.65rem;margin:.4rem 0 1rem}
  .cover-option{display:grid;gap:.28rem;text-align:left;min-width:0}
  .cover-option img{width:100%;height:auto;aspect-ratio:1;object-fit:cover;border-radius:.35rem;background:var(--card)}
  .cover-filename{display:flex;min-width:0;font-size:.8rem;white-space:nowrap}
  .cover-filename span:first-child{overflow:hidden;text-overflow:ellipsis}
  .cover-filename span:last-child{flex:none}
  .album-tags-scroll{overflow-x:auto}
  .album-track-tags{width:100%;border-collapse:collapse;text-align:left;font-size:.85rem}
  .album-track-tags th,.album-track-tags td{border-bottom:1px solid var(--line);padding:.4rem .5rem}
  .album-track-tags th:first-child{width:6rem}
  .album-track-tags td:last-child{width:36%;overflow-wrap:anywhere;color:var(--muted)}
  .prompt-list{list-style:none;padding:0;margin:.3rem 0 0}
  .prompt-list li{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.55rem;padding:.55rem 0;border-top:1px solid var(--line)}
  .prompt-list strong{overflow-wrap:anywhere;font-size:.88rem}
  .prompt-actions,.prompt-toolbar{display:flex;flex-wrap:wrap;gap:.35rem}
  .prompt-actions button{display:grid;justify-items:start;font-size:.8rem;gap:.1rem;padding:.38rem .52rem}
  .prompt-actions button.saved{background:var(--good-soft);border-color:transparent;color:var(--good)}
  .prompt-actions button.saved small{color:inherit}
  .prompt-dialog{width:min(48rem,calc(100% - 1.25rem))}
  .prompt-heading{display:flex;justify-content:space-between;align-items:start;gap:.75rem}
  .prompt-dialog label{margin-top:.9rem}
  .prompt-dialog textarea{line-height:1.45}
  .prompt-dialog .prompt-secondary{background:var(--surface);color:var(--text);border:1px solid var(--line)}
  .prompt-toolbar{margin:.35rem 0 .7rem}
  @media(max-width:600px){.album-tag-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.album-track-tags{min-width:23rem}}
  .transport.compact .wave-stack,.transport.compact .repair-timeline{display:none}
  .transport.compact .player-panel{height:6rem;align-items:stretch}
  .transport.compact .player-audio{align-self:end}
  .transport.compact h1{font-size:1.15rem;letter-spacing:0}
  .transport.compact p{margin:.2rem 0}
  .stage-toggle{width:auto;margin-left:.45rem;vertical-align:middle}
  .stage-status{float:right;padding:.12rem .45rem;border-radius:1rem;background:var(--surface);font-size:.75rem;color:var(--muted);font-weight:550}
  .stage-toggle:checked + .stage-status{background:var(--accent-soft);color:var(--accent)}
  .inline-control{display:grid;grid-template-columns:max-content minmax(60px,1fr) 3rem;align-items:center;gap:.5rem}
  .spectrogram-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .7rem;margin-bottom:.45rem}
  .switch-pair{display:flex;gap:.18rem}.switch-pair button,.spectrogram-toolbar button{font-size:.76rem;padding:.28rem .42rem}
  .spectrogram-toolbar label{display:flex;align-items:center;margin:0;white-space:nowrap}
  .spectrogram-toolbar input[type=number]{width:3.75rem;padding:.25rem}
  .spectrogram-toolbar input[type=range]{width:clamp(4rem,11vw,9rem);padding:0}
  .spectrogram-toolbar output{font-size:.76rem;font-variant-numeric:tabular-nums;color:var(--muted)}
  .spec-cursor{margin-left:auto}
  .spectro-viewport,.spec-overview{position:relative;overflow:hidden;border-radius:.4rem;background:var(--chart);isolation:isolate}
  .spectro-viewport{height:clamp(13rem,29vw,21rem)}
  .spectrogram{position:absolute;inset-block:0;touch-action:none;cursor:crosshair}
  .spectrogram img,.spec-overview img{display:block;width:100%;height:100%;pointer-events:none}
  .spec-grid{position:absolute;inset:0;pointer-events:none}.spec-grid span{position:absolute;inset-inline:0;border-top:1px solid #ffffff18}
  .spectro-status{position:absolute;inset:50% auto auto 50%;transform:translate(-50%,-50%);padding:.3rem .5rem;background:var(--card);color:var(--text);border-radius:.3rem}
  .spec-overview{height:2.7rem;margin-top:.28rem}
  .spec-overview input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:ew-resize}
  .spec-overview:focus-within{outline:2px solid var(--accent);outline-offset:2px}
  .overview-window{position:absolute;inset-block:0;border:2px solid #fff;border-radius:.3rem;background:#ffffff1c;box-shadow:0 0 0 100vw #0005;pointer-events:none}
  .overview-labels{display:flex;justify-content:space-between;gap:.5rem;margin-top:.25rem}
  .spectrogram-playhead{position:absolute;inset-block:0;width:1px;background:#edf2d9;pointer-events:none}
  .marquee{position:absolute;border:1px solid #ffb36b;background:#ffad4933;pointer-events:none}
  .suggested-noise-box{position:absolute;pointer-events:none;min-height:2px;border:1px dashed #ffb36b;background:#ffad4926}
  .saved-noise-box{position:absolute;pointer-events:none;min-height:2px;border:1px solid #86e9ed;background:#86e9ed26}
  .saved-noise-box span{position:absolute;top:0;left:0;line-height:1;padding:.1rem .16rem;background:#15292a;color:#d9ffff;font-size:.65rem}
  .saved-noise-box.focused{outline:1px solid #fff}
  .saved-noise-box.bypassed{opacity:.45;border-style:dashed}
  @media(max-width:850px){.manual-tools,.noise-workbench{grid-template-columns:1fr}}
  .primary,.primary:not(:disabled):hover{background:var(--accent-fill);color:#fff;border-color:transparent;font-weight:700}
  .primary:not(:disabled):hover{filter:brightness(.94)}
  .result{display:grid;gap:.25rem;padding:.7rem 0;border-top:1px solid var(--line);margin-top:.5rem}.error{color:var(--danger)}.status{min-height:1.2em}
  .output-folders{display:flex;flex-wrap:wrap;gap:.35rem .9rem;margin:.5rem 0 .8rem;color:var(--muted);font-size:.8rem}
  .output-folders code{color:var(--text)}
  @media(max-width:700px){main{display:block}.transport.compact{position:static}aside{min-height:0;margin-bottom:.65rem}.sidebar-footer{margin-top:.5rem}}
</style>
