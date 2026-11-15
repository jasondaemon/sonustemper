<script lang="ts">
  let {correlation, mono, toggle, unavailable}:
    {correlation:number|null;mono:boolean;toggle:()=>void;unavailable:boolean}=$props();
</script>

<div class="stereo-check" role="group" aria-label="Stereo compatibility check">
  <div class="indicator" title={unavailable ? 'Extended metering unavailable' : 'Correlation before the Mono switch. Sustained negative readings suggest cancellation in mono.'}>
    <div class="caption"><span>Correlation</span><strong>{correlation?.toFixed(2) ?? '—'}</strong></div>
    <div class="scale" role="meter" aria-label="Stereo correlation" aria-valuemin="-1" aria-valuemax="1"
      aria-valuenow={correlation ?? undefined} aria-valuetext={correlation===null ? 'No reading' : undefined}>
      {#if correlation!==null}<span class="marker" style:left={((correlation+1)*50)+'%'}></span>{/if}
    </div>
    <div class="ticks" aria-hidden="true"><span>−1</span><span>0</span><span>+1</span></div>
  </div>
  <button type="button" aria-pressed={mono} onclick={toggle} title="Sum to mono for listening only; exports stay unchanged">Mono</button>
</div>

<style>
  .stereo-check{display:flex;align-items:center;gap:.5rem}
  .indicator{width:7.3rem;min-width:0;color:var(--muted);font:.6rem ui-monospace,monospace}
  .caption,.ticks{display:flex;justify-content:space-between;gap:.3rem}
  .caption strong{color:var(--text);font-variant-numeric:tabular-nums}
  .scale{position:relative;height:.34rem;border-radius:.3rem;margin:.17rem 0;background:linear-gradient(90deg,var(--warning-soft) 50%,var(--good-soft) 50%)}
  .scale::before{content:'';position:absolute;height:100%;left:50%;border-left:1px solid var(--muted)}
  .marker{position:absolute;inset-block:-.12rem;width:.16rem;border-radius:.2rem;background:var(--text);transform:translateX(-50%)}
  button{border:1px solid var(--line);border-radius:.5rem;background:var(--surface);color:var(--text);
    font:inherit;font-size:.83rem;padding:.42rem .6rem;cursor:pointer}
  button:hover{background:var(--surface-hover)}
  button[aria-pressed=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
</style>
