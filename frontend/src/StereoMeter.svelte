<script lang="ts">
  import {meterPosition, type MeterChannel} from './levels';

  let {channels, reset}: {channels:MeterChannel[]; reset:() => void} = $props();
  const thresholds = [-60,-54,-48,-42,-36,-33,-30,-27,-24,-21,-18,-16,-14,-12,-10,-9,-8,-7,-6,-5,-4,-3,-2,-1,0];
  const ticks = [0,-6,-12,-24,-60];
  const y = (db:number) => 130 - meterPosition(db, thresholds) * 120;
</script>

<div class="meters" role="group" aria-label="Stereo output level in dBFS">
  <div class="meter-face" aria-hidden="true">
  <svg viewBox="0 0 80 136" preserveAspectRatio="none">
    {#each ticks as tick}
      <path class="tick" d={`M49 ${y(tick)}h4`}/>
    {/each}
    {#each channels as channel, index}
      {@const x = 4 + index * 23}
      <rect class="clip-lamp" class:lit={channel.clipped} {x} y="1" width="19" height="6" rx="1"/>
      {#each thresholds.slice(1) as threshold, segment}
        <rect class="segment" class:amber={threshold > -9} class:lit={channel.level >= thresholds[segment] + 0.1}
          {x} y={y(threshold) + .6} width="19" height="3.8" rx=".7"/>
      {/each}
      {#if channel.hold > -60}
        <path class="hold" d={`M${x - 1} ${y(channel.hold)}h21`}/>
      {/if}
    {/each}
  </svg>
    {#each ticks as tick}
      <span class="scale" style:top={`${y(tick) / 136 * 100}%`}>{tick}</span>
    {/each}
  </div>
  <div class="channel-labels" aria-hidden="true"><span>L</span><span>R</span></div>
  <button type="button" class:over={channels.some(item => item.clipped)} onclick={reset}
    aria-label={channels.some(item => item.clipped) ? `Reset clipping: ${channels.filter(item => item.clipped).map(item => item.label).join(' and ')} channel` : 'Reset peak hold'}
    title="Reset peak hold and clip lights">{channels.some(item => item.clipped) ? 'CLIP' : 'dBFS'}</button>
</div>

<style>
  .meters{display:flex;flex-direction:column;align-items:stretch;min-width:0;min-height:0;padding:.38rem .38rem .3rem;border:1px solid var(--line);border-radius:.65rem;background:var(--surface)}
  .meter-face{position:relative;flex:1;min-height:0}
  svg{width:100%;height:100%;display:block;overflow:visible}
  .scale,.channel-labels{color:var(--muted);font: .56rem ui-monospace,monospace}
  .scale{position:absolute;left:70%;transform:translateY(-50%)}
  .channel-labels{position:relative;height:.78rem}
  .channel-labels span{position:absolute;left:16.875%;transform:translateX(-50%)}
  .channel-labels span+span{left:45.625%}
  .tick{stroke:var(--muted);opacity:.45;stroke-width:.7}
  .segment,.clip-lamp{fill:var(--text);opacity:.12}
  .segment.lit{fill:#27a365;opacity:1}
  .segment.amber.lit{fill:#e5a72b}
  .clip-lamp.lit{fill:#ee4e59;opacity:1}
  .hold{stroke:var(--text);stroke-width:1.25;paint-order:stroke;pointer-events:none;vector-effect:non-scaling-stroke}
  button{font:700 .65rem ui-monospace,monospace;letter-spacing:.04em;color:var(--muted);border:0;border-radius:.25rem;background:none;padding:.16rem;cursor:pointer}
  button:hover{background:var(--surface-hover);color:var(--text)}
  button.over{color:var(--danger);background:var(--danger-soft)}
</style>
