<script lang="ts">
  import {findingTitle, isNoiseFinding, rangeLabel, repairColors, repairTypes, type Finding, type Range, type Repair} from './repairs';
  let {repairs, findings, duration, range, selected, enabled, selectFinding, selectFix, setRange}: {
    repairs:Repair[]; findings:Finding[]; duration:number; range:Range|undefined; selected:string; enabled:boolean;
    selectFinding:(item:Finding)=>void; selectFix:(id:string)=>void; setRange:(range:Range)=>void;
  } = $props();
  let drag = $state<number>();
  let moved = false;
  const x = (second:number) => Math.max(0,Math.min(100,second * 100 / duration));
  function point(event:PointerEvent) {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return Math.round(Math.max(0, Math.min(1,(event.clientX-box.left)/box.width)) * duration * 100) / 100;
  }
</script>

{#if duration > 0}
  <div class="timeline" aria-label="Cleanup timeline">
    <div class="range-lane" role="region" aria-label="Drag to select a repair range. Exact times can be entered in Cleanup."
      onpointerdown={(event) => {if (event.button !== 0) return; drag=point(event);moved=false;event.currentTarget.setPointerCapture(event.pointerId);}}
      onpointermove={(event) => {
        if (drag === undefined) return;
        const at=point(event);
        if (Math.abs(at-drag) >= .05) moved=true;
        if (moved) setRange({start:Math.min(drag,at),end:Math.max(drag,at)});
      }}
      onpointerup={() => drag=undefined} onpointercancel={() => drag=undefined}>
      {#if range}<span class="selection" style:left={`${x(range.start)}%`} style:width={`${x(range.end)-x(range.start)}%`}></span>{/if}
      {#if !range && !findings.length}<small>Drag here to mark a repair range</small>{/if}
    </div>
    {#if findings.length}
      <div class="findings-lane" aria-label="Analysis findings">
        {#each findings as item}
          <button type="button" class:peak={item.kind==='peak'} class:spectral={isNoiseFinding(item.kind)} style:left={`${x(item.start)}%`}
            style:width={`${x(item.end)-x(item.start)}%`} title={`${findingTitle(item.kind)} · ${rangeLabel(item)}`}
            aria-label={`${findingTitle(item.kind)}, ${rangeLabel(item)}`} onclick={() => selectFinding(item)}></button>
        {/each}
      </div>
    {/if}
    {#if repairs.length>5}<small class="lanes-label">{repairs.length} approved fixes · scroll to review</small>{/if}
    <div class="fix-lanes" aria-label="Approved repair timeline" class:many={repairs.length>5}>
    {#each repairs as fix, index (fix.id)}
      <button type="button" class="fix-lane" class:active={fix.id===selected} class:off={!enabled || !fix.enabled} style:--fix={repairColors[index%repairColors.length]}
        aria-label={`${repairTypes[fix.kind].title}, ${rangeLabel(fix)}, ${fix.amount} dB`} aria-pressed={fix.id===selected}
        title={`${repairTypes[fix.kind].title} · ${rangeLabel(fix)} · ${fix.amount} dB`} onclick={() => selectFix(fix.id)}>
        <span class="fix-line" style:left={`${x(fix.start)}%`} style:width={`${x(fix.end ?? duration)-x(fix.start)}%`} style:top={`${20 + fix.amount/12*62}%`}></span>
        <span class="lane-number">{index+1}</span>
      </button>
    {/each}
    </div>
  </div>
{/if}

<style>
  .timeline{display:grid;gap:1px;border:1px solid var(--chart-grid);border-radius:.45rem;overflow:hidden;background:var(--chart);margin:.2rem 0 .45rem}
  .range-lane{position:relative;height:1.3rem;cursor:crosshair;touch-action:none;display:grid;align-items:center}
  small{color:var(--muted);padding-inline:.4rem;pointer-events:none;font-size:.68rem}
  .selection{position:absolute;inset-block:0;background:var(--accent-soft);border-inline:1px solid var(--accent);pointer-events:none}
  .findings-lane{position:relative;height:.62rem;background:var(--surface)}
  .findings-lane button{position:absolute;inset-block:1px;border:0;padding:0;min-width:4px;border-radius:2px;background:var(--warning);opacity:.75;cursor:pointer}
  .findings-lane button.peak{background:var(--danger)}
  .findings-lane button.spectral{background:var(--chart-line)}
  .fix-lanes{display:grid}.fix-lanes.many{max-height:5.2rem;overflow:auto;overscroll-behavior-y:contain}
  .lanes-label{padding-block:.12rem;border-top:1px solid var(--chart-grid)}
  .fix-lane{position:relative;border:0;border-top:1px solid var(--chart-grid);background:none;cursor:pointer;padding:0;min-height:1rem;text-align:left;color:var(--text)}
  .fix-lane:hover,.fix-lane.active{background:var(--surface-hover)}
  .fix-line{position:absolute;height:2px;min-width:3px;background:var(--fix);filter:brightness(var(--repair-brightness,1));border-radius:1px}
  .fix-lane.off .fix-line{opacity:.35}
  .lane-number{position:relative;padding:0 .22rem;background:var(--chart);border-radius:.2rem;font:600 .62rem ui-monospace,monospace}
  :global(html[data-theme=dark]) .timeline{--repair-brightness:1.45}
</style>
