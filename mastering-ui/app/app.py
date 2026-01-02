import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

IN_DIR = Path("/nfs/mastering/in")
OUT_DIR = Path("/nfs/mastering/out")
PRESET_DIR = Path("/mnt/external-ssd/mastering/presets")

MASTER_ONE = Path("/nfs/mastering/master.py")
MASTER_PACK = Path("/nfs/mastering/master_pack.py")

app = FastAPI()

OUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/out", StaticFiles(directory=str(OUT_DIR), html=True), name="out")

def read_metrics_for_wav(wav: Path) -> dict | None:
    mp = wav.with_suffix(".metrics.json")
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "metrics_read_failed"}

def read_run_metrics(folder: Path) -> dict | None:
    mp = folder / "metrics.json"
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None




def fmt_metrics(m: dict | None) -> str:
    if not m:
        return ""
    if "error" in m:
        return "metrics: (error)"
    I = m.get("I"); TP = m.get("TP"); LRA = m.get("LRA")
    dI = m.get("delta_I")
    margin = m.get("tp_margin")
    w = m.get("width")
    parts = []
    if I is not None:
        parts.append(f"I={I} LUFS" + (f" (Δ {dI:+.1f})" if isinstance(dI,(int,float)) else ""))
    if TP is not None:
        # measured TP is dBFS in our ebur128 output
        parts.append(f"TP={TP} dBFS" + (f" (m {margin:+.1f})" if isinstance(margin,(int,float)) else ""))
    if LRA is not None:
        parts.append(f"LRA={LRA}")
    if isinstance(w,(int,float)) and abs(w-1.0) > 1e-6:
        parts.append(f"W={w:.2f}")
    return " ".join(parts) if parts else "metrics: (unavailable)"



def bust_url(song: str, filename: str) -> str:
    fp = Path('/nfs/mastering/out') / song / filename
    try:
        v = int(fp.stat().st_mtime)
    except Exception:
        v = 0
    return f"/out/{song}/{filename}?v={v}"

BUILD_STAMP = os.getenv("MASTERING_BUILD")
if not BUILD_STAMP:
    try:
        BUILD_STAMP = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        BUILD_STAMP = "dev"

HTML_TEMPLATE = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Local Mastering</title>
  <style>
    :root{
      --bg:#0b0f14; --card:#121a23; --muted:#9fb0c0; --text:#e7eef6;
      --line:#203042; --accent:#ff8a3d; --accent2:#2bd4bd; --danger:#ff4d4d;
    }
    body{ margin:0; background:linear-gradient(180deg,#0b0f14,#070a0e); color:var(--text);
      font-family:-apple-system,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    .wrap{ max-width:1200px; margin:0 auto; padding:26px 18px 40px; }
    .top{ display:flex; gap:14px; align-items:flex-end; justify-content:space-between; flex-wrap:wrap; }
    h1{ font-size:20px; margin:0; letter-spacing:.2px; }
    .sub{ color:var(--muted); font-size:13px; margin-top:6px; }
    .grid{ display:grid; grid-template-columns: 1fr 1.2fr; gap:14px; margin-top:16px; }
    @media (max-width: 980px){ .grid{ grid-template-columns:1fr; } }
    .card{ background:rgba(18,26,35,.9); border:1px solid var(--line); border-radius:16px; padding:16px; }
    .card h2{ font-size:14px; margin:0 0 12px 0; color:#cfe0f1; }
    .row{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    label{ color:#cfe0f1; font-size:13px; font-weight:600; }
    select,input[type="range"],input[type="file"],button{
      border-radius:12px; border:1px solid var(--line); background:#0f151d; color:var(--text);
      padding:10px 12px; font-size:14px;
    }
    select{ min-width:260px; }
    button{ cursor:pointer; }
    .btn{ background:linear-gradient(180deg, rgba(255,138,61,.95), rgba(255,138,61,.75));
      border:0; color:#1a0f07; font-weight:800; }
    .btn2{ background:linear-gradient(180deg, rgba(43,212,189,.95), rgba(43,212,189,.75));
      border:0; color:#05110f; font-weight:800; }
    .btnGhost{ background:#0f151d; }
    .btnDanger{ background:rgba(255,77,77,.15); border:1px solid rgba(255,77,77,.35); color:#ffd0d0; }
    .pill{ font-size:12px; color:var(--muted); border:1px solid var(--line); border-radius:999px; padding:6px 10px; }
    .mono{ font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12px; color:#cfe0f1; }
    .hr{ height:1px; background:var(--line); margin:12px 0; }
    .result{ white-space:pre-wrap; background:#0f151d; border:1px solid var(--line);
      border-radius:14px; padding:12px; font-family: ui-monospace, Menlo, Consolas, monospace; font-size:12px; color:#d7e6f5; }
    .links a{ color: #ffd3b3; text-decoration:none; }
    .links a:hover{ text-decoration:underline; }
    .outlist{ margin-top:10px; display:flex; flex-direction:column; gap:10px; }
    .outitem{ padding:10px; border:1px solid var(--line); border-radius:14px; background:#0f151d; }
    audio{ width:100%; margin-top:8px; }
    .small{ color:var(--muted); font-size:12px; }
    .toggle{ display:flex; gap:8px; align-items:center; }
    input[type="checkbox"]{ transform: scale(1.15); }
    .twoCol{ display:grid; grid-template-columns: 1fr 1fr; gap:14px; }
    @media (max-width: 980px){ .twoCol{ grid-template-columns:1fr; } }
    .runRow{ display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .runLeft{ display:flex; flex-direction:column; gap:4px; }
    .runBtns{ display:flex; gap:8px; }
    .linkish{ color:#ffd3b3; text-decoration:none; }
    .linkish:hover{ text-decoration:underline; }
  </style>

<style>
/* --- Mastering UI control rows --- */
.control-row{
  display:flex;
  align-items:center;
  gap:12px;
  width:100%;
  margin-top:10px;
  flex-wrap:nowrap;
}
.control-row label{
  min-width:220px;
  display:flex;
  align-items:center;
  gap:8px;
}
.control-row input[type="range"]{
  flex:1;
  min-width:260px;
}
.control-row .pill{
  min-width:96px;
  text-align:center;
}
@media (max-width: 900px){
  .control-row{
    flex-wrap:wrap;
  }
  .control-row label{
    min-width:100%;
  }
  .control-row input[type="range"]{
    min-width:100%;
  }
}
</style>



<style>
/* --- Responsive layout fix (force on-screen) --- */
html, body{
  width:100%;
  max-width:100%;
  overflow-x:hidden;
}

body{
  margin:0;
}

/* Make the main wrapper fluid */
.wrap{
  width:100% !important;
  max-width:1400px;
  margin:0 auto;
  padding:16px;
  box-sizing:border-box;
}

/* Grid: 2 columns on wide screens, 1 column on small */
.grid{
  display:grid !important;
  grid-template-columns: 360px 1fr;
  gap:16px;
}

@media (max-width: 1000px){
  .grid{
    grid-template-columns: 1fr;
  }
}

/* Cards should not force overflow */
.card{
  min-width:0 !important;
}

/* Control rows should wrap and never push past viewport */
.control-row{
  min-width:0 !important;
  flex-wrap:wrap;
}
.control-row label{
  min-width:180px;
}
@media (max-width: 700px){
  .control-row label{
    min-width:100%;
  }
}

/* Make sliders behave */
input[type="range"]{
  min-width: 180px !important;
  max-width: 100%;
}

/* Make selects fluid */
select, input[type="text"], input[type="file"]{
  max-width:100%;
  min-width:0 !important;
}
</style>

</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>Local Mastering <span style="font-size:12px;opacity:.65">(build {{BUILD_STAMP}})</span></h1>
        <div class="sub">
          <span class="pill">IN: <span class="mono">/nfs/mastering/in</span></span>
          <span class="pill">OUT: <span class="mono">/nfs/mastering/out</span></span>
        </div>
      </div>
      <div class="row">
        <button class="btnGhost" onclick="refreshAll()">Refresh lists</button>
<div id="statusMsg" class="small" style="margin-top:8px;opacity:.85"></div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2>Upload</h2>
        <form id="uploadForm">
          <div class="row">
            <input type="file" id="file" name="file" accept=".wav,.mp3,.flac,.aiff,.aif" required />
            <button class="btn2" type="submit">Upload to /in</button>
          </div>
        </form>
        <div id="uploadResult" class="small" style="margin-top:10px;"></div>

        <div class="hr"></div>

        <h2>Previous Runs</h2>
        <div class="small">Click a run to load outputs. Delete removes the entire song output folder.</div>
        <div id="recent" class="outlist" style="margin-top:10px;"></div>
      </div>

      <div class="card">
        <h2>Master</h2>

        <div class="row">
          <label>Input file</label>
          <select id="infile"></select>
        </div>

        <div class="control-row">
          <label>Preset</label>
          <select id="preset"></select>
        </div>

        <div class="control-row">
          <label>Strength</label>
          <input type="range" id="strength" min="0" max="100" value="80" oninput="strengthVal.textContent=this.value">
          <span class="pill">S=<span id="strengthVal">80</span></span>
        </div>

        <div class="hr"></div>

        <div class="control-row">
          <label>Loudness Mode</label>
          <select id="loudnessMode"></select>
        </div>
        <div class="small" id="loudnessHint" style="margin-top:-6px; margin-bottom:6px; color:var(--muted);"></div>

        <div id="overrides">
          <div class="control-row">
            <label><input type="checkbox" id="useLufs"> Override Target LUFS</label>
            <input type="range" id="lufs" min="-20" max="-8" step="0.5" value="-14">
            <span class="pill" id="lufsVal">-14.0 LUFS</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="useTp"> Override True Peak (TP)</label>
            <input type="range" id="tp" min="-3.0" max="0.0" step="0.1" value="-1.0">
            <span class="pill" id="tpVal">-1.0 dBTP</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="ov_width"> Override Stereo Width</label>
            <input type="range" id="width" min="0.90" max="1.40" step="0.01" value="1.12">
            <span class="pill" id="widthVal">1.12</span>
          </div>

          <div class="control-row">
            <label><input type="checkbox" id="ov_mono_bass"> Mono Bass Below (Hz)</label>
            <input type="range" id="mono_bass" min="60" max="200" step="5" value="120">
            <span class="pill" id="monoBassVal">120</span>
          </div>
        </div>

        <div class="row" style="margin-top:12px;">
          <button class="btn" onclick="runOne()">Master (single)</button>
          <button class="btn2" onclick="runPack()">Run A/B Pack</button>
        </div>

        <div class="hr"></div>

        <div class="small">Job output:</div>
        <div id="result" class="result">(waiting)</div>

        <div id="links" class="links small" style="margin-top:10px;"></div>
        <div id="outlist" class="outlist"></div>
      </div>
    </div>
  </div>

<script>
function setStatus(msg) {
  const el = document.getElementById('statusMsg');
  if (el) el.textContent = msg;
}

const LOUDNESS_MODES = {
  apple: { label: "Apple Music", lufs: -16.0, tp: -1.0, hint: "Target -16 LUFS / -1.0 dBTP" },
  streaming: { label: "Streaming Safe", lufs: -14.0, tp: -1.0, hint: "Target -14 LUFS / -1.0 dBTP" },
  loud: { label: "Loud", lufs: -9.0, tp: -0.8, hint: "Target -9 LUFS / -0.8 dBTP" },
  manual: { label: "Manual", hint: "Use LUFS/TP sliders (optional)" },
};
const LOUDNESS_MODE_KEY = "loudnessMode";
const LOUDNESS_MANUAL_KEY = "loudnessManualValues";
const LOUDNESS_ORDER = ["apple", "streaming", "loud", "manual"];

function setLoudnessHint(text){
  const el = document.getElementById('loudnessHint');
  if (el) el.textContent = text || '';
}

function setSliderValue(id, value){
  const el = document.getElementById(id);
  if (!el || value === undefined || value === null) return;
  el.value = value;
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function loadManualLoudness(){
  try {
    const raw = localStorage.getItem(LOUDNESS_MANUAL_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function saveManualLoudness(){
  const mode = getCurrentLoudnessMode();
  if (mode !== 'manual') return;
  const payload = {
    lufs: document.getElementById('lufs')?.value,
    tp: document.getElementById('tp')?.value,
    useLufs: document.getElementById('useLufs')?.checked,
    useTp: document.getElementById('useTp')?.checked,
  };
  try { localStorage.setItem(LOUDNESS_MANUAL_KEY, JSON.stringify(payload)); } catch {}
}

function getCurrentLoudnessMode(){
  const sel = document.getElementById('loudnessMode');
  if (sel && sel.value) return sel.value;
  const stored = localStorage.getItem(LOUDNESS_MODE_KEY);
  if (stored && LOUDNESS_MODES[stored]) return stored;
  return "apple";
}

function applyLoudnessMode(modeKey, { fromInit=false } = {}){
  const cfg = LOUDNESS_MODES[modeKey] || LOUDNESS_MODES.apple;
  const sel = document.getElementById('loudnessMode');
  if (sel && sel.value !== modeKey) sel.value = modeKey;
  try { localStorage.setItem(LOUDNESS_MODE_KEY, modeKey); } catch {}

  const lock = modeKey !== 'manual';
  const lufsInput = document.getElementById('lufs');
  const tpInput = document.getElementById('tp');
  const useLufs = document.getElementById('useLufs');
  const useTp = document.getElementById('useTp');

  if (lock) {
    if (lufsInput) { lufsInput.disabled = true; setSliderValue('lufs', cfg.lufs); }
    if (tpInput) { tpInput.disabled = true; setSliderValue('tp', cfg.tp); }
    if (useLufs) { useLufs.checked = true; useLufs.disabled = true; }
    if (useTp) { useTp.checked = true; useTp.disabled = true; }
  } else {
    if (lufsInput) lufsInput.disabled = false;
    if (tpInput) tpInput.disabled = false;
    if (useLufs) useLufs.disabled = false;
    if (useTp) useTp.disabled = false;

    const manual = loadManualLoudness();
    if (manual) {
      if (manual.lufs !== undefined && manual.lufs !== null) setSliderValue('lufs', manual.lufs);
      if (manual.tp !== undefined && manual.tp !== null) setSliderValue('tp', manual.tp);
      if (useLufs && typeof manual.useLufs === 'boolean') useLufs.checked = manual.useLufs;
      if (useTp && typeof manual.useTp === 'boolean') useTp.checked = manual.useTp;
    }
  }

  setLoudnessHint(cfg.hint || "");
}

function initLoudnessMode(){
  const sel = document.getElementById('loudnessMode');
  if (!sel) return;
  sel.innerHTML = '';
  LOUDNESS_ORDER.forEach(key => {
    const cfg = LOUDNESS_MODES[key];
    if (!cfg) return;
    const o = document.createElement('option');
    o.value = key;
    o.textContent = cfg.label;
    sel.appendChild(o);
  });

  const initial = getCurrentLoudnessMode();
  sel.value = LOUDNESS_MODES[initial] ? initial : "apple";
  sel.addEventListener('change', () => applyLoudnessMode(sel.value));
  applyLoudnessMode(sel.value, { fromInit: true });
}

async function refreshRecent() {
  const el = document.getElementById('recent');
  if (!el) return;

  try {
    const r = await fetch('/api/recent?limit=30', { cache: 'no-store' });
    const data = await r.json();

    el.innerHTML = '';
    const items = (data && data.items) ? data.items : [];

    if (!items.length) {
      el.innerHTML = '<div class="small" style="opacity:.75;">No runs yet.</div>';
      return;
    }

    for (const it of items) {
      const div = document.createElement('div');
      div.className = 'outitem';
      div.innerHTML = `
        <div class="runRow">
          <div class="runLeft">
            <div class="mono"><a class="linkish" href="#" onclick="loadSong('${it.song}'); return false;">${it.song || it.name}</a></div>
            <div class="small">
              ${it.folder ? `<a class="linkish" href="${it.folder}" target="_blank">folder</a>` : ''}
              ${it.ab ? `&nbsp;|&nbsp;<a class="linkish" href="${it.ab}" target="_blank">A/B page</a>` : ''}
            </div>
          </div>
          <div class="runBtns">
            <button class="btnGhost" onclick="loadSong('${it.song}')">Load</button>
            <button class="btnDanger" onclick="deleteSong('${it.song}')">Delete</button>
          </div>
        </div>
        ${it.mp3 ? `<audio controls preload="none" src="${it.mp3}"></audio>` : `<div class="small">No previews yet</div>`}
      `;
      el.appendChild(div);
    }

    const last = localStorage.getItem("lastSong");
    if (last && items.find(x => x.song === last)) {
      // Optional auto-restore could be added here if desired.
    }
  } catch (e) {
    console.error('refreshRecent failed', e);
  }
}

function wireUI() {
  // If this shows up, JS is definitely running.
  setStatus("UI ready.");

  const bind = (chkId, sliderId, pillId, fmt=(v)=>v) => {
    const chk = document.getElementById(chkId);
    const slider = document.getElementById(sliderId);
    const pill = document.getElementById(pillId);

    if (!slider || !pill) return;

    const update = () => { pill.textContent = fmt(slider.value); };

    slider.addEventListener('input', update);
    slider.addEventListener('change', update);
    update();

    if (chk) {
      // Keep sliders usable even when override is off; checkbox only gates payload
      const syncEnabled = () => { slider.disabled = false; };
      chk.addEventListener('change', syncEnabled);
      syncEnabled();
    }
  };

  // Strength (no checkbox)
  const strength = document.getElementById('strength');
  const strengthVal = document.getElementById('strengthVal');
  if (strength && strengthVal) {
    const u = () => strengthVal.textContent = strength.value;
    strength.addEventListener('input', u);
    strength.addEventListener('change', u);
    u();
  }

  // Overrides (checkbox + slider + pill)
  bind('useLufs', 'lufs', 'lufsVal', (v)=>Number(v).toFixed(1));
  bind('useTp', 'tp', 'tpVal', (v)=>Number(v).toFixed(1));
  bind('ov_width', 'width', 'widthVal', (v)=>Number(v).toFixed(2));
  bind('ov_mono_bass', 'mono_bass', 'monoBassVal', (v)=>String(parseInt(v,10)));

  // If your IDs are different, this will silently no-op rather than crash.

  const trackManual = () => saveManualLoudness();
  const lufsInput = document.getElementById('lufs');
  const tpInput = document.getElementById('tp');
  const useLufs = document.getElementById('useLufs');
  const useTp = document.getElementById('useTp');
  [lufsInput, tpInput, useLufs, useTp].forEach(el => {
    if (el) {
      el.addEventListener('input', trackManual);
      el.addEventListener('change', trackManual);
    }
  });
}

async function refreshAll() {
  try {
    setStatus("Loading lists...");
    const r = await fetch("/api/files", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    const infileSel = document.getElementById("infile");
    const presetSel = document.getElementById("preset");
    if (!infileSel || !presetSel) throw new Error("Missing #infile or #preset element");

    const prevIn = infileSel.value;
    const prevPreset = presetSel.value;

    // Populate input files
    infileSel.innerHTML = "";
    (data.files || []).forEach(f => {
      const o = document.createElement("option");
      o.value = f;
      o.textContent = f;
      infileSel.appendChild(o);
    });

    // Populate presets
    presetSel.innerHTML = "";
    (data.presets || []).forEach(pr => {
      const o = document.createElement("option");
      o.value = pr;
      o.textContent = pr;
      presetSel.appendChild(o);
    });

    // restore selection if possible
    if (prevIn && [...infileSel.options].some(o => o.value === prevIn)) infileSel.value = prevIn;
    if (prevPreset && [...presetSel.options].some(o => o.value === prevPreset)) presetSel.value = prevPreset;

    setStatus("");
  } catch (e) {
    console.error("refreshAll failed:", e);
    setStatus("ERROR loading lists (open console)");
  }
  await refreshRecent();
}

function setResult(text){ document.getElementById('result').textContent = text || '(no output)'; }
function setLinks(html){ document.getElementById('links').innerHTML = html || ''; }
function clearOutList(){ document.getElementById('outlist').innerHTML = ''; }

function appendOverrides(fd){
  const addIfChecked = (chkId, inputId, key) => {
    const chk = document.getElementById(chkId);
    const input = document.getElementById(inputId);
    if (chk && input && chk.checked) fd.append(key, input.value);
  };
  addIfChecked('useLufs', 'lufs', 'lufs');
  addIfChecked('useTp', 'tp', 'tp');
  addIfChecked('ov_width', 'width', 'width');
  addIfChecked('ov_mono_bass', 'mono_bass', 'mono_bass');
}

async function loadSong(song){
  localStorage.setItem("lastSong", song);

  setLinks(`
    Output folder: <a href="/out/${song}/" target="_blank">/out/${song}/</a>
    &nbsp;|&nbsp;
    A/B page: <a href="/out/${song}/index.html" target="_blank">index.html</a>
  `);

  const r = await fetch(`/api/outlist?song=${encodeURIComponent(song)}`);
  const j = await r.json();

  const out = document.getElementById('outlist');
  out.innerHTML = '';
  j.items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'outitem';
    div.innerHTML = `
      <div class="mono">${it.name}</div>
      ${it.metrics ? `<div class="small">${it.metrics}</div>` : `<div class="small">metrics: (not available yet)</div>`}
      ${it.mp3 ? `<audio controls preload="none" src="${it.mp3}"></audio>` : ''}
      <div class="small">
        ${it.wav ? `<a class="linkish" href="${it.wav}" target="_blank">WAV</a>` : ''}
        ${it.mp3 ? `&nbsp;|&nbsp;<a class="linkish" href="${it.mp3}" target="_blank">MP3</a>` : ''}
        ${it.ab ? `&nbsp;|&nbsp;<a class="linkish" href="${it.ab}" target="_blank">A/B</a>` : ''}
      </div>
    `;
    out.appendChild(div);
  });
}

async function showOutputsFromText(text){
  const lines = (text || '').split('\n').map(x => x.trim()).filter(Boolean);
  if (!lines.length) return;

  const m = lines[0].match(/\/nfs\/mastering\/out\/([^\/]+)\//);
  if (!m) return;
  const song = m[1];

  await loadSong(song);
  await refreshRecent();
}

async function runOne(){
  clearOutList(); setLinks(''); setResult('Running...');

  const infile = document.getElementById('infile').value;
  const preset = document.getElementById('preset').value;
  const strength = document.getElementById('strength').value;

  const fd = new FormData();
  fd.append('infile', infile);
  fd.append('preset', preset);
  fd.append('strength', strength);
  appendOverrides(fd);

  const r = await fetch('/api/master', { method:'POST', body: fd });
  const t = await r.text();
  setResult(t);
  await showOutputsFromText(t);

  // Auto-refresh lists after a run so outputs + previous runs appear
  try { await refreshAll(); } catch (e) { console.error(e); }
}

async function runPack(){
  clearOutList(); setLinks(''); setResult('Running A/B pack...');

  const infile = document.getElementById('infile').value;
  const strength = document.getElementById('strength').value;

  const fd = new FormData();
  fd.append('infile', infile);
  fd.append('strength', strength);
  appendOverrides(fd);

  const r = await fetch('/api/master-pack', { method:'POST', body: fd });
  const t = await r.text();
  setResult(t);
  await showOutputsFromText(t);

  // Auto-refresh lists/runs after job completes
  try { await refreshAll(); } catch (e) { console.error('post-job refreshAll failed', e); }
}

async function deleteSong(song){
  if (!confirm(`Delete all outputs for "${song}"? This removes /nfs/mastering/out/${song}/`)) return;

  const r = await fetch(`/api/song/${encodeURIComponent(song)}`, { method:'DELETE' });
  const j = await r.json();
  setResult(j.message || 'Deleted.');
  await refreshRecent();

  const last = localStorage.getItem("lastSong");
  if (last === song) {
    localStorage.removeItem("lastSong");
    setLinks('');
    clearOutList();
  }
}

document.getElementById('uploadForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  setResult('(waiting)'); setLinks(''); clearOutList();

  const f = document.getElementById('file').files[0];
  const fd = new FormData();
  fd.append('file', f);

  const r = await fetch('/api/upload', { method:'POST', body: fd });
  const j = await r.json();
  document.getElementById('uploadResult').textContent = j.message;
  
  try { await refreshAll(); } catch (e) { console.error('post-upload refreshAll failed', e); }
});

document.addEventListener('DOMContentLoaded', () => {
  try {
    wireUI();
    initLoudnessMode();
    refreshAll();
  } catch(e){
    console.error(e);
    setStatus("UI init error (open console)");
  }
});
</script>

</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_TEMPLATE.replace("{{BUILD_STAMP}}", BUILD_STAMP)

@app.get("/api/files")
def list_files():
    IN_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted([p.name for p in IN_DIR.iterdir()
                    if p.is_file() and p.suffix.lower() in [".wav",".mp3",".flac",".aiff",".aif"]])
    presets = sorted([p.stem for p in PRESET_DIR.iterdir()
                      if p.is_file() and p.suffix.lower()==".json"])
    return {"files": files, "presets": presets}


@app.get("/api/presets")
def presets():
    # Return list of preset names derived from preset files on disk
    preset_dir = Path("/nfs/mastering/presets")
    if not preset_dir.exists():
        return []
    names = sorted([p.stem for p in preset_dir.glob("*.txt")])
    return names



@app.get("/api/recent")
def recent(limit: int = 30):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    folders = [d for d in OUT_DIR.iterdir() if d.is_dir()]
    folders.sort(key=lambda d: d.stat().st_mtime, reverse=True)

    items = []
    for d in folders[:limit]:
        mp3s = sorted([f.name for f in d.iterdir() if f.is_file() and f.suffix.lower()==".mp3"])
        metrics = read_run_metrics(d)
        items.append({
            "song": d.name,
            "folder": f"/out/{d.name}/",
            "ab": f"/out/{d.name}/index.html",
            "mp3": f"/out/{d.name}/{mp3s[0]}" if mp3s else None,
            "metrics": metrics,
        })
    return {"items": items}

@app.delete("/api/song/{song}")
def delete_song(song: str):
    # Safety: only delete direct child folders in OUT_DIR
    target = (OUT_DIR / song).resolve()
    if OUT_DIR.resolve() not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        return {"message": f"Nothing to delete for {song}."}
    shutil.rmtree(target)
    return {"message": f"Deleted outputs for {song}."}

@app.get("/api/outlist")
def outlist(song: str):
    folder = OUT_DIR / song
    items = []
    if folder.exists() and folder.is_dir():
        wavs = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower()==".wav"])
        mp3s = {p.stem: p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower()==".mp3"}

        for w in wavs:
            stem = w.stem
            m = read_metrics_for_wav(w)
            items.append({
                "name": stem,
                "wav": f"/out/{song}/{w.name}",
                "mp3": f"/out/{song}/{mp3s[stem]}" if stem in mp3s else None,
                "ab": f"/out/{song}/index.html",
                "metrics": fmt_metrics(m),
            })
    return {"items": items}

@app.get("/api/metrics")
def run_metrics(song: str):
    folder = OUT_DIR / song
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="run_not_found")
    m = read_run_metrics(folder)
    if not m:
        raise HTTPException(status_code=404, detail="metrics_not_found")
    return m

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    IN_DIR.mkdir(parents=True, exist_ok=True)
    dest = IN_DIR / Path(file.filename).name
    dest.write_bytes(await file.read())
    return JSONResponse({"message": f"Uploaded: {dest.name}"})

@app.post("/api/master")
def master(
    infile: str = Form(...),
    preset: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
):
    cmd = [str(MASTER_ONE), "--preset", preset, "--infile", infile, "--strength", str(strength)]
    if lufs is not None:
        cmd += ["--lufs", str(lufs)]
    if tp is not None:
        cmd += ["--tp", str(tp)]
    if width is not None:
        cmd += ["--width", str(width)]
    if mono_bass is not None:
        cmd += ["--mono_bass", str(mono_bass)]
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.output)

@app.post("/api/master-pack")
def master_pack(
    infile: str = Form(...),
    strength: int = Form(80),
    lufs: float | None = Form(None),
    tp: float | None = Form(None),
    width: float | None = Form(None),
    mono_bass: float | None = Form(None),
):
    cmd = [str(MASTER_PACK), "--infile", infile, "--strength", str(strength)]
    if lufs is not None:
        cmd += ["--lufs", str(lufs)]
    if tp is not None:
        cmd += ["--tp", str(tp)]
    if width is not None:
        cmd += ["--width", str(width)]
    if mono_bass is not None:
        cmd += ["--mono_bass", str(mono_bass)]
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.output)
