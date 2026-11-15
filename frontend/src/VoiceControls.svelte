<script lang="ts">
  let {voice=$bindable(), onEdit}: {voice:Record<string,{enabled:boolean;freq_hz:number;amount_db:number}>;onEdit:()=>void} = $props();
</script>

<section class="voice" aria-label="Corrective voice filters">
  <strong>Voice filters · Pre-EQ</strong>
  {#each Object.entries(voice) as [name, control]}
    {@const title = name === 'deesser' ? 'De-esser' : name === 'deharsh' ? 'De-harsh' : 'Vocal Smooth'}
    <div class="voice-row">
      <label><input type="checkbox" bind:checked={control.enabled} onchange={onEdit}> {title}</label>
      {#if name !== 'vocal_smooth'}<input aria-label={title+' frequency (Hz)'} type="number" min={name==='deesser'?3000:1500} max={name==='deesser'?10000:6000} bind:value={control.freq_hz} oninput={onEdit}>{/if}
      <div class="voice-amount">
        <input aria-label={title+' amount (dB)'} type="range" min={name==='deesser'?-12:-6} max="0" step=".5" bind:value={control.amount_db} oninput={onEdit}>
        <output>{control.amount_db} dB</output>
      </div>
    </div>
  {/each}
</section>

<style>
  .voice{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(10.5rem,100%),1fr));gap:.5rem .8rem;background:var(--surface);border:1px solid var(--line);border-radius:.7rem;padding:.75rem}
  strong,.voice-amount{grid-column:1/-1}
  .voice-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:.35rem;font-size:.85rem}
  label{display:flex;align-items:center;gap:.2rem}
  input[type=number]{width:4.4rem;padding:.25rem;font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line);border-radius:.4rem}
  .voice-amount{display:grid;grid-template-columns:minmax(0,1fr) 3.4rem;align-items:center;gap:.45rem}
  output{text-align:right;font-variant-numeric:tabular-nums}
  input[type=range]{width:100%;min-width:0}
</style>
