export type ClipSpan = {start:number;end:number;channels:number};

export function songClips(clips:ClipSpan[], clock:number, playhead:number, rate:number,
                          validFrom:number, duration:number):ClipSpan[] {
  if (!Number.isFinite(duration) || duration<=0) return [];
  return clips.flatMap(clip=>{
    if (clip.end<=validFrom) return [];
    const start=playhead-(clock-Math.max(validFrom,clip.start))*rate;
    const end=playhead-(clock-clip.end)*rate;
    if (end<0 || start>duration) return [];
    return [{start:Math.max(0,start),end:Math.min(duration,end),channels:clip.channels}];
  });
}

export function mergeClips(existing:ClipSpan[], incoming:ClipSpan[]):ClipSpan[] {
  const ordered=[...existing,...incoming].sort((a,b)=>a.start-b.start);
  const merged:ClipSpan[]=[];
  for (const mark of ordered) {
    const previous=merged.at(-1);
    if (previous && mark.start<=previous.end+.075) {
      previous.end=Math.max(previous.end,mark.end);
      previous.channels|=mark.channels;
    } else merged.push({...mark});
  }
  return merged;
}
