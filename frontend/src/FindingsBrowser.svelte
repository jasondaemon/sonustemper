<script lang="ts">
  import {untrack} from 'svelte';
  import {rangeLabel, repairTypes, type Finding} from './repairs';
  let {findings, active, select}: {findings:Finding[];active:Finding|undefined;select:(item:Finding)=>void} = $props();
  const categories = [
    {kind:'all', title:'All'}, {kind:'rumble', title:'Low end'}, {kind:'presence', title:'Presence'},
    {kind:'highs', title:'Highs'}, {kind:'peak', title:'Peaks'},
  ] as const;
  let filter = $state<(typeof categories)[number]['kind']>('all');
  let counts = $derived(Object.fromEntries(categories.map(({kind}) => [kind, kind==='all' ? findings.length : findings.filter(item=>item.kind===kind).length])));
  let visible = $derived(findings.filter(item => filter==='all' || item.kind===filter));
  let list = $state<HTMLUListElement>();

  $effect(() => {
    const kind=active?.kind;
    untrack(() => {if (kind && filter!=='all' && kind!==filter) filter='all';});
  });

  function move(event:KeyboardEvent, index:number) {
    if (!['ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key==='Home' ? 0 : event.key==='End' ? visible.length-1 : index + (event.key==='ArrowDown'?1:-1);
    const candidate = Math.max(0,Math.min(visible.length-1,next));
    list?.querySelectorAll<HTMLButtonElement>('button')[candidate]?.focus();
    if (visible[candidate]) select(visible[candidate]);
  }
</script>

<section class="findings-browser" aria-label="Full-song findings">
  <div class="filters" role="group" aria-label="Filter findings by type">
    {#each categories as group}
      {#if counts[group.kind]}
        <button type="button" aria-pressed={filter===group.kind} onclick={() => {filter=group.kind;list?.scrollTo(0,0);}}>
          {group.title} <span>{counts[group.kind]}</span>
        </button>
      {/if}
    {/each}
  </div>
  <ul bind:this={list} aria-label="Findings in song order">
    {#each visible as item, index}
      <li><button type="button" aria-pressed={item===active} onclick={() => select(item)} onkeydown={event=>move(event,index)}>
        <span class="time">{rangeLabel(item)}</span>
        <span class="title">{item.kind==='peak' ? 'Near full scale' : repairTypes[item.kind].title}</span>
        <small>{item.evidence_db} {item.kind==='peak'?'dBFS':'dB rel.'}</small>
      </button></li>
    {/each}
  </ul>
</section>

<style>
  .findings-browser{border:1px solid var(--line);border-radius:.55rem;background:var(--surface);min-width:0;overflow:hidden}
  .filters{display:flex;flex-wrap:wrap;gap:.28rem;padding:.42rem;border-bottom:1px solid var(--line)}
  button{font:inherit;font-size:.78rem;color:var(--text);cursor:pointer;background:none;border:1px solid transparent;border-radius:.35rem}
  button:hover{background:var(--surface-hover)}
  button[aria-pressed=true]{background:var(--accent-soft);color:var(--accent)}
  .filters button{padding:.22rem .36rem;white-space:nowrap}
  .filters button span{color:var(--muted);font-variant-numeric:tabular-nums;margin-left:.12rem}
  ul{list-style:none;margin:0;padding:.18rem .34rem;max-height:17rem;overflow:auto;overscroll-behavior-y:contain}
  li+li{border-top:1px solid var(--line)}
  li button{width:100%;display:grid;grid-template-columns:max-content minmax(0,1fr) max-content;align-items:center;gap:.38rem;text-align:left;padding:.38rem .32rem}
  li button:focus-visible{outline-offset:-2px}
  .time,small{font-variant-numeric:tabular-nums;white-space:nowrap}
  .time{font-size:.72rem;color:var(--muted)}
  .title{font-weight:650}
  small{font-size:.68rem;color:var(--muted)}
  @media(max-width:520px){li button{grid-template-columns:1fr max-content}.title{grid-row:2;grid-column:1/-1}}
</style>
