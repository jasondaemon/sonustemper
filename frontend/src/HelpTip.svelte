<script lang="ts">
  import type {Snippet} from 'svelte';

  let {id,label,children}:{id:string;label:string;children:Snippet} = $props();
  let anchor:HTMLSpanElement;
  let pinned=$state(false), dismissed=$state(false);

  function dismiss() {
    pinned=false;
    dismissed=true;
  }
</script>

<svelte:window onpointerdown={(event)=>{if (pinned && !anchor?.contains(event.target as Node)) dismiss();}}
  onkeydown={(event)=>{if (event.key==='Escape') dismiss();}}/>

<span class="help-tip" class:pinned class:dismissed bind:this={anchor}>
  <button type="button" aria-label={label} aria-describedby={id}
    onpointerenter={()=>dismissed=false} onfocus={()=>dismissed=false} onblur={dismiss}
    onclick={()=>{if (pinned) dismiss(); else {pinned=true;dismissed=false;}}}>?</button>
  <span class="help-copy" {id} role="tooltip">{@render children()}</span>
</span>

<style>
  .help-tip{display:inline-flex}
  button{display:grid;place-items:center;min-width:1.5rem;min-height:1.5rem;padding:0;border:1px solid var(--line);
    border-radius:50%;background:transparent;color:var(--muted);font-family:inherit;font-size:.74rem;font-weight:650;line-height:1;cursor:pointer}
  button:hover,button:focus-visible{color:var(--accent);border-color:var(--accent)}
  .help-copy{display:none;position:absolute;z-index:3;inset-block-start:100%;inset-inline-start:0;
    width:max-content;max-width:min(100%,21rem);padding:.45rem .6rem;border:1px solid var(--line);border-radius:.45rem;
    color:var(--text);background:var(--card);box-shadow:0 4px 14px #0002;font-size:.76rem;line-height:1.4}
  .help-tip.pinned:not(.dismissed) .help-copy,.help-tip:not(.dismissed):has(button:focus-visible) .help-copy{display:block}
  @media(hover:hover){.help-tip:not(.dismissed):hover .help-copy{display:block}}
</style>
