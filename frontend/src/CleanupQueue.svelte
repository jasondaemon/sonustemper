<script lang="ts">
  import {untrack, type Snippet} from 'svelte';
  import {rangeLabel, repairColors, repairTypes, type Finding, type ReviewItem} from './repairs';
  let {items, active, cleanupEnabled, ignored, select, editor}: {
    items:ReviewItem[];active:string;cleanupEnabled:boolean;ignored:(item:Finding)=>boolean;
    select:(item:ReviewItem)=>void;editor:Snippet<[ReviewItem]>;
  } = $props();

  type View = 'all'|'review'|'applied';
  let view = $state<View>('all');
  let kind = $state<'all'|Finding['kind']>('all');
  const views = [{id:'all',title:'All'}, {id:'review',title:'To review'}, {id:'applied',title:'Applied'}] as const;
  const kinds = [{id:'all',title:'All types'}, {id:'rumble',title:'Low end'}, {id:'presence',title:'Presence'},
    {id:'highs',title:'Highs'}, {id:'peak',title:'Peaks'}] as const;
  const needsReview = (item:ReviewItem) => !item.fix && !ignored(item.findings[0]);
  let counts = $derived({all:items.length,review:items.filter(needsReview).length,applied:items.filter(item=>item.fix).length});
  let visible = $derived(items.filter(item=>(kind==='all'||item.kind===kind)
    && (view==='all'||(view==='applied'?item.fix:needsReview(item)))));
  let list = $state<HTMLOListElement>();

  $effect(() => {
    const selected=items.find(item=>item.id===active);
    untrack(() => {
      if (!selected) return;
      if (kind!=='all' && selected.kind!==kind) kind='all';
      if ((view==='applied' && !selected.fix) || (view==='review' && !needsReview(selected))) view='all';
    });
  });

  function navigate(event:KeyboardEvent,index:number) {
    if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const next=event.key==='Home' ? 0 : event.key==='End' ? visible.length-1 : index+(event.key==='ArrowDown'?1:-1);
    const at=Math.max(0,Math.min(visible.length-1,next));
    list?.querySelectorAll<HTMLButtonElement>('.queue-row')[at]?.focus();
  }
</script>

<section class="cleanup-queue" aria-label="Cleanup review list">
  <div class="queue-toolbar">
    <div class="queue-views" role="group" aria-label="Filter by review status">
      {#each views as item}<button type="button" aria-pressed={view===item.id} onclick={()=>{view=item.id;list?.scrollTo(0,0);}}>{item.title} <small>{counts[item.id]}</small></button>{/each}
    </div>
    <label>Type <select bind:value={kind} aria-label="Filter cleanup items by type" onchange={()=>list?.scrollTo(0,0)}>
      {#each kinds as item}{#if item.id==='all' || items.some(option=>option.kind===item.id)}<option value={item.id}>{item.title}</option>{/if}{/each}
    </select></label>
  </div>
  <ol bind:this={list} class:many={visible.length>7}>
    {#each visible as item,index (item.id)}
      {@const expanded = item.id===active}
      {@const status = item.fix ? cleanupEnabled && item.fix.enabled && item.fix.amount>0 ? 'Applied' : 'Bypassed'
        : ignored(item.findings[0]) ? 'Skipped' : item.kind==='peak' ? 'Warning' : 'To review'}
      <li id={`cleanup-row-${item.id}`} class:expanded>
        <button type="button" class="queue-row" aria-expanded={expanded} onclick={()=>select(item)} onkeydown={event=>navigate(event,index)}>
          <span class="queue-time">{rangeLabel(item)}</span>
          <strong>{#if item.number}<span class="queue-number" style:--fix={repairColors[(item.number-1)%repairColors.length]}>{item.number}</span>{/if}{item.kind==='peak' ? 'Near full scale' : repairTypes[item.kind].title}</strong>
          <span class="queue-status" class:applied={status==='Applied'} class:warning={status==='Warning'}>{status}</span>
          <span class="queue-caret" aria-hidden="true">{expanded ? '−' : '+'}</span>
        </button>
        {#if expanded}<div class="inline-editor">{@render editor(item)}</div>{/if}
      </li>
    {:else}<li class="empty-queue">No items in this view.</li>{/each}
  </ol>
</section>

<style>
  .cleanup-queue{border:1px solid var(--line);border-radius:.6rem;overflow:hidden;margin:.65rem 0}
  .queue-toolbar{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:.45rem;padding:.4rem .55rem;background:var(--surface);border-bottom:1px solid var(--line)}
  .queue-views{display:flex;flex-wrap:wrap;gap:.24rem}
  .queue-toolbar button,.queue-toolbar select{font:inherit;font-size:.78rem;padding:.26rem .4rem;border:1px solid var(--line);border-radius:.35rem;background:var(--card);color:var(--text)}
  .queue-toolbar button{cursor:pointer}.queue-toolbar button[aria-pressed=true]{background:var(--accent-soft);color:var(--accent)}
  .queue-toolbar label{display:flex;gap:.35rem;align-items:center;font-size:.75rem;color:var(--muted)}
  small,.queue-time{font-variant-numeric:tabular-nums;color:var(--muted)}
  ol{list-style:none;margin:0;padding:0}.many{max-height:min(33rem,70vh);overflow:auto;overscroll-behavior-y:contain}
  li+li{border-top:1px solid var(--line)}
  .queue-row{display:grid;grid-template-columns:8.4rem minmax(0,1fr) max-content 1rem;gap:.5rem;align-items:center;text-align:left;width:100%;padding:.48rem .63rem;border:0;background:var(--card);color:var(--text);font:inherit;font-size:.81rem;cursor:pointer}
  .queue-row:hover{background:var(--surface-hover)}.expanded .queue-row{background:var(--accent-soft)}
  .queue-row:focus-visible{outline-offset:-2px}
  .queue-row strong{display:flex;align-items:center;gap:.36rem;overflow-wrap:anywhere}
  .queue-status{font-size:.7rem;padding:.12rem .3rem;border-radius:.27rem;background:var(--surface);color:var(--muted);white-space:nowrap}
  .queue-status.applied{background:var(--good-soft);color:var(--good)}.queue-status.warning{background:var(--warning-soft);color:var(--warning)}
  .queue-number{border-left:2px solid var(--fix);padding-left:.24rem;font-size:.69rem;color:var(--muted)}
  .queue-caret{text-align:right;color:var(--muted)}
  .inline-editor{padding:.58rem .65rem;background:var(--surface);border-top:1px solid var(--line)}
  .empty-queue{padding:.7rem;color:var(--muted);font-size:.8rem}
  @media(max-width:560px){.queue-row{grid-template-columns:minmax(0,1fr) max-content 1rem;gap:.2rem .35rem}.queue-time{grid-column:1;font-size:.72rem}.queue-row strong{grid-column:1/3;grid-row:2}.queue-status{grid-column:2;grid-row:1}.queue-caret{grid-column:3;grid-row:1/3}}
</style>
