<script lang="ts">
  import {timeLabel} from './repairs';
  type Box = {start:number;end:number;low:number;high:number};
  let {url, bounds, box, resize}: {url:string;bounds:Box;box?:Box;resize?:(box:Box)=>void}=$props();
  let anchor:{x:number;y:number}|undefined;
  let marquee=$state<Box>();
  let failed=$state(false);
  const clamp = (value:number) => Math.max(0,Math.min(1,value));
  const x = (time:number) => clamp((time-bounds.start)/(bounds.end-bounds.start))*100;
  const y = (hz:number) => clamp((bounds.high-hz)/(bounds.high-bounds.low))*100;
  let shown=$derived(marquee ?? box);
  $effect(() => {if (url) failed=false;});

  function point(event:PointerEvent) {
    const view=(event.currentTarget as HTMLElement).getBoundingClientRect();
    return {x:clamp((event.clientX-view.left)/view.width),y:clamp((event.clientY-view.top)/view.height)};
  }

  function draw(event:PointerEvent) {
    if (!anchor) return;
    const {x,y}=point(event);
    const time=(fraction:number) => Math.round((bounds.start+fraction*(bounds.end-bounds.start))*1000)/1000;
    const freq=(fraction:number) => Math.max(20,Math.min(20000,Math.round(bounds.high-fraction*(bounds.high-bounds.low))));
    marquee={start:time(Math.min(x,anchor.x)),end:time(Math.max(x,anchor.x)),
      low:freq(Math.max(y,anchor.y)),high:freq(Math.min(y,anchor.y))};
  }

  function finish(event:PointerEvent) {
    if (!anchor) return;
    const next=point(event), origin=anchor;
    draw(event);
    const draft=marquee;
    anchor=undefined;marquee=undefined;
    if (draft && Math.abs(next.x-origin.x)>=.01 && Math.abs(next.y-origin.y)>=.01 &&
      draft.end-draft.start>=.01 && draft.high>draft.low) resize?.(draft);
  }

  const hzLabel=(hz:number) => hz>=1000 ? `${+(hz/1000).toFixed(2)} kHz` : `${hz} Hz`;
</script>

<figure>
  <figcaption><strong>Passage spectrogram</strong><small>Linear frequency{resize ? ' · drag to refine box' : ''}</small></figcaption>
  <div class="plot-layout">
    <div class="frequency-axis" aria-hidden="true"><span>{hzLabel(bounds.high)}</span><span>{hzLabel((bounds.high+bounds.low)/2)}</span><span>{hzLabel(bounds.low)}</span></div>
    <div class="plot" class:editable={resize && !failed} role="region" aria-label={resize?'Drag to refine the selected time and frequency range':'Source spectrogram for this passage'}
      onpointerdown={(event)=>{if(!resize||failed||event.button!==0)return;anchor=point(event);event.currentTarget.setPointerCapture(event.pointerId);}}
      onpointermove={draw} onpointerup={finish} onpointercancel={()=>{anchor=undefined;marquee=undefined;}}>
      {#if failed}<small class="failure">Spectrogram unavailable for this passage.</small>
      {:else}
        <img src={url} alt="Source audio spectrogram for this passage" draggable="false" onerror={()=>failed=true}>
        <div class="grid" aria-hidden="true"></div>
        {#if shown}
          <div class="box" class:band={resize} aria-hidden="true" style:left={x(shown.start)+'%'} style:width={(x(shown.end)-x(shown.start))+'%'}
            style:top={y(shown.high)+'%'} style:height={(y(shown.low)-y(shown.high))+'%'}></div>
        {/if}
      {/if}
    </div>
    <div class="time-axis" aria-hidden="true"><span>{timeLabel(bounds.start)}</span><span>{timeLabel((bounds.start+bounds.end)/2)}</span><span>{timeLabel(bounds.end)}</span></div>
  </div>
</figure>

<style>
  figure{margin:0;min-width:0}
  figcaption{display:flex;justify-content:space-between;align-items:baseline;gap:.2rem .5rem;flex-wrap:wrap;font-size:.78rem;margin-bottom:.26rem}
  figcaption small,.frequency-axis,.time-axis{color:var(--muted);font-size:.66rem;font-variant-numeric:tabular-nums}
  .plot-layout{display:grid;grid-template-columns:auto minmax(0,1fr);gap:.14rem .32rem}
  .plot{position:relative;overflow:hidden;min-width:0;height:clamp(114px,15vw,176px);border:1px solid var(--line);border-radius:.35rem;background:#090b12}
  .plot.editable{cursor:crosshair;touch-action:none}
  .plot img{width:100%;height:100%;display:block;pointer-events:none}
  .frequency-axis,.time-axis{display:flex;justify-content:space-between;white-space:nowrap}
  .frequency-axis{flex-direction:column;text-align:right}
  .time-axis{grid-column:2}
  .box,.grid{position:absolute;pointer-events:none}
  .grid{inset:50% 0 auto;border-top:1px solid #f5f7ff2b}
  .box{min-height:2px;border-inline:1px dashed #9cdafa;background:#79cfff13}
  .box.band{border:1px solid #ffbf75;background:#ffad4926}
  .failure{display:grid;place-items:center;height:100%;color:#e0e3f0}
</style>
