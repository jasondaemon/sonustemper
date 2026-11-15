<script module lang="ts">
  export type AlbumProgress = {completed:number; total:number};
  export type PlaylistSong = {title:string; number:string; url:string; lufs:number; peak:number};
  export type AlbumDialog = {
    action:string; album:string; artist:string; cover:string; trackCount:number;
  } & (
    {view:'progress'; task:string; progress?:AlbumProgress; song?:string; trackNumber?:number}
    | {view:'player'; message:string; songs:PlaylistSong[]}
    | {view:'error'; error:string}
  );
</script>

<script lang="ts">
  import {tick} from 'svelte';

  let {model, close}:{model:AlbumDialog; close:()=>void}=$props();
  let dialog:HTMLDialogElement, audio=$state<HTMLAudioElement>();
  let closeButton=$state<HTMLButtonElement>();
  let coverFailed=$state(false), index=$state(0), playing=$state(false), time=$state(0), duration=$state(0);
  let volume=$state(1), playError=$state('');
  let playOnLoad=false;
  let song=$derived(model.view==='player' ? model.songs[index] : undefined);
  const clock=(seconds:number)=>{
    const value=Math.floor(seconds || 0);
    return Math.floor(value/60)+':'+String(value%60).padStart(2,'0');
  };

  $effect(()=>{
    if (!dialog.open) dialog.showModal();
  });
  $effect(()=>{
    if (model.view!=='progress') void tick().then(()=>closeButton?.focus());
  });
  $effect(()=>{if (audio) audio.volume=volume;});

  function dismiss() {
    if (model.view==='progress') return;
    audio?.pause();
    dialog.close();
    close();
  }

  function resume() {
    playError='';
    void audio?.play().catch(()=>{playError='Playback could not start. Try Play again.';});
  }

  function selectSong(next:number) {
    if (model.view!=='player' || !model.songs[next]) return;
    if (next===index) {resume();return;}
    audio?.pause();
    index=next;duration=0;time=0;playError='';playOnLoad=true;
  }
</script>

<svelte:window onbeforeunload={(event)=>{if (model.view==='progress') {event.preventDefault();event.returnValue='';}}}/>

<dialog class="theme-dialog album-job-dialog" bind:this={dialog} aria-labelledby="album-job-title"
  oncancel={(event)=>{event.preventDefault();dismiss();}}>
  <header>
    <div>
      <small>{model.view==='progress' ? 'Album job' : model.view==='player' ? model.message : 'Album job stopped'}</small>
      <h2 id="album-job-title">{model.album}</h2>
      <p>{model.artist} · {model.trackCount} {model.trackCount===1 ? 'song' : 'songs'}</p>
    </div>
    {#if model.view!=='progress'}<button bind:this={closeButton} type="button" onclick={dismiss}>Close</button>{/if}
  </header>

  {#if model.view==='progress'}
    <section class="job-progress" aria-label={model.action}>
      <div class="meter-label">
        <strong>{model.action}</strong>
        {#if model.progress}<span>{Math.floor(model.progress.completed/model.progress.total*100)}%</span>{/if}
      </div>
      <progress aria-label="Album job progress" max={model.progress?.total ?? 1} value={model.progress?.completed}></progress>
      <div class="job-stage" role="status">
        <strong>{model.task}</strong>
        {#if model.trackNumber}<span>Song {model.trackNumber} of {model.trackCount}{model.song ? ' · '+model.song : ''}</span>{/if}
      </div>
      {#if model.progress}<small>{model.progress.completed} of {model.progress.total} steps complete</small>{/if}
    </section>
  {:else if model.view==='error'}
    <p class="job-error" role="alert">{model.error}</p>
  {:else if song}
    <div class="album-deck">
      <div class="cover" aria-hidden={!model.cover || coverFailed}>
        {#if model.cover && !coverFailed}
          <img src={model.cover} alt="Album cover" onerror={()=>coverFailed=true}>
        {:else}
          <div class="record-art" aria-hidden="true"><span></span></div>
        {/if}
      </div>
      <div class="deck-controls">
        <small>TRACK {String(index+1).padStart(2,'0')} / {String(model.songs.length).padStart(2,'0')} <span>WAV</span></small>
        <strong class="now-playing" aria-live="polite">{song.title}</strong>
        <label class="seek">
          <input aria-label="Seek song" type="range" min="0" max={duration || 1} step=".1" value={time}
            disabled={!duration} oninput={(event)=>{if(audio) audio.currentTime=+event.currentTarget.value;}}>
        </label>
        <div class="time-code"><span>{clock(time)}</span><span>{clock(duration)}</span></div>
        <div class="play-controls">
          <button type="button" disabled={index===0} aria-label="Previous song" onclick={()=>selectSong(index-1)}>‹</button>
          <button type="button" class="play" onclick={()=>playing ? audio?.pause() : resume()}>{playing?'Pause':'Play'}</button>
          <button type="button" disabled={index+1>=model.songs.length} aria-label="Next song" onclick={()=>selectSong(index+1)}>›</button>
          <input aria-label="Volume" title="Volume" type="range" min="0" max="1" step=".01" bind:value={volume}>
        </div>
      </div>
      <audio bind:this={audio} src={song.url} preload="metadata"
        onloadedmetadata={()=>{duration=Number.isFinite(audio?.duration) ? audio!.duration : 0;if(playOnLoad){playOnLoad=false;resume();}}}
        ontimeupdate={()=>{time=audio?.currentTime ?? 0;}} onplay={()=>playing=true} onpause={()=>playing=false}
        onended={()=>{playing=false;selectSong(index+1);}}
        onerror={()=>{playOnLoad=false;playing=false;playError='This WAV is unavailable.';}}></audio>
    </div>
    {#if playError}<p class="job-error" role="status">{playError}</p>{/if}
    <section class="playlist" aria-label="Album playlist">
      <header><strong>Playlist</strong><small>In album order</small></header>
      <ol>
        {#each model.songs as row, i (row.url)}
          <li><button type="button" aria-current={index===i ? 'true' : undefined} onclick={()=>selectSong(i)}>
            <span class="track-number">{row.number.padStart(2,'0')}</span>
            <span class="track-name">{row.title}</span>
            <small>{row.lufs.toFixed(1)} LUFS</small>
            <small>{row.peak.toFixed(1)} dBTP</small>
          </button></li>
        {/each}
      </ol>
    </section>
  {/if}
</dialog>

<style>
  :global(body:has(.album-job-dialog[open])){overflow:hidden}
  .album-job-dialog{width:min(41rem,calc(100% - 1.2rem));max-height:calc(100dvh - 1.2rem);padding:1rem;overflow:auto}
  header{display:flex;align-items:start;justify-content:space-between;gap:.7rem}
  header>div{min-width:0}
  header h2{margin:.12rem 0;overflow-wrap:anywhere}
  header p{margin:.2rem 0;color:var(--muted);font-size:.83rem}
  .job-progress{margin-top:.8rem;padding:.85rem;border:1px solid var(--line);border-radius:.6rem;background:var(--surface)}
  .meter-label,.time-code{display:flex;justify-content:space-between;gap:.55rem;font-variant-numeric:tabular-nums}
  progress{width:100%;height:.8rem;margin:.6rem 0;accent-color:var(--accent-fill)}
  .job-stage{display:grid;gap:.2rem;margin-bottom:.42rem;overflow-wrap:anywhere}
  .job-stage span,.job-progress small{color:var(--muted);font-size:.82rem}
  .job-error{color:var(--danger);overflow-wrap:anywhere}
  .album-deck{display:grid;grid-template-columns:clamp(5.3rem,23%,9rem) minmax(0,1fr);gap:.8rem;margin-top:.85rem;
    padding:.7rem;border:1px solid var(--line);border-radius:.65rem;background:linear-gradient(145deg,var(--chart),var(--surface))}
  .cover{width:100%;aspect-ratio:1;border:1px solid var(--line);border-radius:.4rem;overflow:hidden;background:var(--chart)}
  .cover img{display:block;width:100%;height:100%;object-fit:cover}
  .record-art{height:100%;display:grid;place-items:center;background:linear-gradient(140deg,var(--chart-grid),var(--chart))}
  .record-art span{width:72%;aspect-ratio:1;border-radius:50%;background:repeating-radial-gradient(circle,#252d39 0 2px,#384251 3px 4px);
    border:1px solid #67768b;box-shadow:0 2px 8px #151c2780}
  .record-art span::after{content:"";display:block;width:28%;aspect-ratio:1;border-radius:50%;margin:36% auto;background:var(--accent-fill)}
  .deck-controls{display:grid;align-content:start;gap:.2rem;min-width:0}
  .deck-controls>small,.time-code,.track-number{font: .73rem/1.25 ui-monospace,monospace;color:var(--muted)}
  .deck-controls>small{display:flex;justify-content:space-between}
  .now-playing{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--accent)}
  .seek input{display:block;width:100%;margin:.15rem 0 0}
  .play-controls{display:flex;align-items:center;gap:.33rem;flex-wrap:wrap;margin-top:.3rem}
  .play-controls button{padding:.25rem .45rem}
  .play-controls .play{min-width:3.8rem}
  .play-controls input{width:clamp(2.2rem,13vw,6rem);margin-inline-start:auto}
  button:disabled{opacity:.45}
  .playlist{margin-top:.7rem;border:1px solid var(--line);border-radius:.55rem;overflow:hidden}
  .playlist header{align-items:center;padding:.55rem .65rem;background:var(--surface)}
  .playlist ol{list-style:none;margin:0;padding:0;max-height:min(36dvh,17rem);overflow:auto}
  .playlist li+li{border-top:1px solid var(--line)}
  .playlist button{display:grid;width:100%;grid-template-columns:1.75rem minmax(0,1fr) auto auto;gap:.45rem;align-items:center;
    text-align:left;border:0;border-radius:0;padding:.42rem .6rem;background:var(--card);color:var(--text)}
  .playlist button:hover{background:var(--surface-hover)}
  .playlist button[aria-current=true]{background:var(--accent-soft);box-shadow:inset 3px 0 var(--accent);color:var(--accent)}
  .playlist button:focus-visible{outline-offset:-2px}
  .track-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .playlist small{white-space:nowrap;font-variant-numeric:tabular-nums}
  @media(max-width:480px){.playlist button{grid-template-columns:1.5rem minmax(0,1fr) auto}.playlist button small:last-child{display:none}}
</style>
