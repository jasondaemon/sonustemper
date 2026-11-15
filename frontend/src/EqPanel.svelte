<script lang="ts">
  import type { Snippet } from 'svelte';
  import { defaultBands, type Band } from './monitor';
  let {bands = $bindable(), spectrum, curve, combined='', strength=100, presetControls, stereoControls, onEdit, legacyVoice}:
    {bands:Band[]; spectrum:string; curve:string; combined?:string; strength?:number; presetControls:Snippet; stereoControls:Snippet; onEdit:(reset?:boolean)=>void; legacyVoice?:Snippet} = $props();
  let selected = $state<number | null>(null);
  let dragging = $state(false);
  let band = $derived(selected === null ? undefined : bands[selected]);
  const types = {peaking:'Bell', lowshelf:'Low shelf', highshelf:'High shelf', highpass:'High pass', lowpass:'Low pass', notch:'Notch', bandpass:'Band pass'};
  const hasGain = (type: string) => ['peaking', 'peak', 'bell', 'lowshelf', 'highshelf'].includes(type);
  const x = (freq:number) => Math.log(freq / 20) / Math.log(1000) * 600;
  const y = (gain:number) => 75 - gain * 5;
  function drag(event:PointerEvent) {
    if (!dragging || !band) return;
    onEdit();
    const rect = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
    band.freq_hz = Math.round(20 * 1000 ** Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)));
    if (hasGain(band.type)) {
      band.gain_db = Math.round(Math.max(-12, Math.min(12, 15 - 30 * (event.clientY - rect.top) / rect.height)) * 10) / 10;
    }
  }
</script>
<div class="editor">
  <div>
    <svg role="img" aria-label="Realtime EQ spectrum and band handles" viewBox="0 0 600 150" preserveAspectRatio="none"
      onpointermove={drag} onpointerup={() => dragging=false} onpointercancel={() => dragging=false}>
      <path class="grid" d="M0 15H600 M0 45H600 M0 75H600 M0 105H600 M0 135H600 M0 0V150 M200 0V150 M400 0V150 M600 0V150"/>
      <path class="spectrum" d={spectrum}/><path class="curve" d={curve}/>
      {#if combined}<path class="combined" d={combined}/>{/if}
      {#each bands as item, index}
        <circle aria-hidden="true" cx={x(item.freq_hz)} cy={y(hasGain(item.type) ? item.gain_db*strength/100 : 0)} r={selected===index ? 7 : 4}
          class:off={item.enabled === false}
          onpointerdown={(event) => {selected=index;dragging=true;((event.currentTarget as SVGCircleElement).ownerSVGElement)?.setPointerCapture(event.pointerId);}}/>
      {/each}
    </svg>
    <div class="scale"><span>20 Hz</span><span>200 Hz</span><span>2 kHz</span><span>20 kHz</span></div>
    <small class="legend" class:inactive={!combined} aria-hidden={!combined}>Solid: EQ. Dashed: EQ plus active tonal repairs at the playhead.</small>
  </div>
  <div class="controls">
    <div>
      {@render presetControls()}
      <strong>Band list</strong>
      {#each bands as item, index}
        <div class="band"><input type="checkbox" aria-label={'Enable band '+(index+1)} checked={item.enabled !== false} onchange={(event) => {item.enabled=event.currentTarget.checked;onEdit();}}>
          <button class:active={selected===index} aria-pressed={selected===index} onclick={() => selected=index}>
            <span>{types[item.type as keyof typeof types] ?? item.type}</span><span>{item.freq_hz} Hz</span>
          </button>
        </div>
      {/each}
    </div>
    <div class="parameters">
      <strong>Band controls</strong>
      {#if band}
        <label><span>Type</span><select bind:value={band.type} onchange={() => onEdit()}>{#each Object.entries(types) as [type, name]}<option value={type}>{name}</option>{/each}</select></label>
        <label class="slider-row"><span>Frequency (Hz)</span>
          <input aria-label="Frequency slider" type="range" min={Math.log(20)} max={Math.log(20000)} step=".001"
            value={Math.log(band.freq_hz || 20)} oninput={(event)=>{band.freq_hz=Math.round(Math.exp(+event.currentTarget.value));onEdit();}}>
          <input aria-label="Frequency (Hz)" type="number" min="20" max="20000" bind:value={band.freq_hz} oninput={() => onEdit()}>
        </label>
        <label class="slider-row"><span>Gain (dB)</span>
          <input aria-label="Gain slider" type="range" min="-12" max="12" step=".1" disabled={!hasGain(band.type)} bind:value={band.gain_db} oninput={() => onEdit()}>
          <input aria-label="Gain (dB)" type="number" min="-12" max="12" step=".1" disabled={!hasGain(band.type)} bind:value={band.gain_db} oninput={() => onEdit()}>
        </label>
        <label class="slider-row"><span>Q</span>
          <input aria-label="Q slider" type="range" min=".2" max="12" step=".1" bind:value={band.q} oninput={() => onEdit()}>
          <input aria-label="Q" type="number" min=".2" max="12" step=".1" bind:value={band.q} oninput={() => onEdit()}>
        </label>
      {:else}
        <small>Select a band or graph handle.</small>
      {/if}
      <div class="band-actions">
        <button disabled={bands.length>=20} onclick={() => {onEdit();bands.push({type:'peaking',freq_hz:1000,gain_db:0,q:1,enabled:true});selected=bands.length-1;}}>Add band</button>
        <button disabled={!band} onclick={() => {
          if (selected === null || !band) return;
          bands.splice(selected, 1);
          selected = null;
          onEdit();
        }}>Remove band</button>
        <button onclick={() => {bands=defaultBands();selected=null;onEdit(true);}}>Reset EQ</button>
      </div>
    </div>
  </div>
</div>
<div class="lower-controls">
{#if legacyVoice}<div class="legacy-voice">{@render legacyVoice()}</div>{/if}
<section class="stereo">{@render stereoControls()}</section>
</div>
<style>
  .editor{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem}
  svg{display:block;width:100%;height:360px;touch-action:none;background:var(--chart);border:1px solid var(--chart-grid);border-radius:.7rem}
  path{fill:none}.grid{stroke:var(--chart-grid)}.spectrum{stroke:var(--chart-spectrum)}.curve{stroke:var(--chart-line);stroke-width:2}
  .combined{stroke:var(--text);stroke-width:1.5;stroke-dasharray:5 4;opacity:.75}
  circle{fill:var(--chart-handle);stroke:var(--chart-line);cursor:grab}.off{opacity:.45}
  .scale{display:flex;justify-content:space-between;color:var(--muted);font-size:.75rem;margin:.4rem 0}
  .legend{display:block}
  .legend.inactive{visibility:hidden}
  .controls{display:grid;grid-template-columns:minmax(9rem,2fr) minmax(0,3fr);align-items:start;gap:.9rem}
  .parameters,label{display:grid;gap:.35rem}
  .parameters{align-content:start}
  .parameters strong{margin-bottom:.2rem}
  .parameters label{grid-template-columns:6.4rem minmax(0,1fr);align-items:center;font-size:.85rem}
  .parameters label > span{white-space:nowrap}
  .parameters label.slider-row{grid-template-columns:6.4rem minmax(0,1fr) 4.3rem}
  .parameters input,.parameters select{width:100%;min-width:0}
  .parameters select{max-width:10rem}
  .band-actions{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.3rem;font-size:.8rem}
  button:disabled{opacity:.42;cursor:default}
  .lower-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem;margin-top:1rem}
  .lower-controls:not(:has(.legacy-voice)){grid-template-columns:minmax(0,1fr)}
  .legacy-voice{grid-column:span 2}
  .stereo{background:var(--surface);border:1px solid var(--line);border-radius:.7rem;padding:.75rem}
  .band{display:flex;gap:.5rem;align-items:center;margin:.35rem 0}
  .band button{display:flex;justify-content:space-between;gap:.35rem;flex:1;text-align:left;font-size:.8rem}
  .band button span:last-child{white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted)}
  input,select,button{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line);border-radius:.5rem;padding:.35rem}
  button:not(:disabled):hover{background:var(--surface-hover)}
  .active{border-color:var(--accent);background:var(--accent-soft)}
  @media(max-width:1150px){.editor{grid-template-columns:1fr}}
  @media(max-width:900px){.lower-controls{grid-template-columns:1fr}.legacy-voice{grid-column:auto}}
  @media(max-width:500px){.controls{grid-template-columns:1fr}}
</style>
