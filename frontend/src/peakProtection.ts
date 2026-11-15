export const monitorPeakCeiling = 10 ** (-1.5 / 20);

export function peakProtection(rate:number) {
  const delay = Math.max(1,Math.round(rate*.004));
  const left = new Float32Array(delay), right = new Float32Array(delay);
  const size = delay+2;
  const times = new Float64Array(size), peaks = new Float32Array(size);
  const attack = Math.exp(-1/(rate*.0006)), release = Math.exp(-1/(rate*.08));
  let frame=0, head=0, tail=0, gain=1;
  return {
    reset() {
      frame=head=tail=0;gain=1;left.fill(0);right.fill(0);
    },
    process(l:Float32Array,r:Float32Array,outL:Float32Array,outR:Float32Array) {
      for (let i=0;i<outL.length;i++,frame++) {
        const position=frame%delay, l0=left[position], r0=right[position];
        left[position]=l[i];right[position]=r[i];
        const peak=Math.max(Math.abs(l[i]),Math.abs(r[i]));
        // Monotonic queue: the strongest peak in the lookahead window is at the head.
        if (head!==tail && times[head]<frame-delay) head=(head+1)%size;
        while (head!==tail && peaks[(tail+size-1)%size]<=peak) tail=(tail+size-1)%size;
        peaks[tail]=peak;times[tail]=frame;tail=(tail+1)%size;
        const target=Math.min(1,monitorPeakCeiling/peaks[head]);
        const smoothing=target<gain ? attack : release;
        gain=target+(gain-target)*smoothing;
        const safe=Math.min(gain,monitorPeakCeiling/Math.max(Math.abs(l0),Math.abs(r0)));
        outL[i]=l0*safe;outR[i]=r0*safe;
      }
    },
  };
}
