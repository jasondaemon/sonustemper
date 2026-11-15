export type MonitorMetrics = {loudness:number|null; truePeak:number|null; correlation:number|null};
export const emptyMonitorMetrics = ():MonitorMetrics => ({loudness:null,truePeak:null,correlation:null});

function biquad(b:number[],a:number[]) {
  let first=0,second=0;
  return (sample:number) => {
    const output=b[0]*sample+first;
    first=b[1]*sample-a[1]*output+second;
    second=b[2]*sample-a[2]*output;
    return output;
  };
}

function kWeight(rate:number) {
  // BS.1770 K-weighting prototypes, bilinear-transformed at the monitor sample rate.
  const shelf=Math.tan(Math.PI*1681.974450955533/rate), q=.7071752369554196;
  const high=10**(3.999843853973347/20), mid=high**.4996667741545416;
  const d=1+shelf/q+shelf*shelf;
  const boost=biquad([(high+mid*shelf/q+shelf*shelf)/d,2*(shelf*shelf-high)/d,(high-mid*shelf/q+shelf*shelf)/d],
    [1,2*(shelf*shelf-1)/d,(1-shelf/q+shelf*shelf)/d]);
  const bass=Math.tan(Math.PI*38.13547087602444/rate), r=.5003270373238773, p=1+bass/r+bass*bass;
  const cut=biquad([1,-2,1],[1,2*(bass*bass-1)/p,(1-bass/r+bass*bass)/p]);
  return (sample:number) => cut(boost(sample));
}

// BS.1770 Annex 2's four interpolating phases, in units of 1/8192.
const phase0=[14,90,-161,272,-487,1125,7964,-838,390,-218,122,-68];
const phase1=[-239,240,-424,730,-1364,3810,6388,-1641,832,-477,271,-155];
const phases=[phase0,phase1,[...phase1].reverse(),[...phase0].reverse()].map(row=>row.map(value=>value/8192));

function peakDetector() {
  const history = new Float64Array(24);
  let cursor=0;
  return (sample:number) => {
    history[cursor]=history[cursor+12]=sample;
    cursor=(cursor+1)%12;
    let peak=Math.abs(sample);
    for (const phase of phases) {
      let interpolation=0;
      for (let i=0;i<12;i++) interpolation+=phase[i]*history[cursor+i];
      peak=Math.max(peak,Math.abs(interpolation));
    }
    return peak;
  };
}

export function monitorMath(rate:number) {
  const reportEvery=Math.round(rate/10);
  const power=new Float64Array(30), left=new Float64Array(5), right=new Float64Array(5), cross=new Float64Array(5);
  const weightLeft=kWeight(rate), weightRight=kWeight(rate);
  let peakLeft=peakDetector(), peakRight=peakDetector();
  let frame=0, blocks=0, total=0, sum=0, a=0, b=0, ab=0, peak=0;
  return {
    resetPeak() {peak=0;peakLeft=peakDetector();peakRight=peakDetector();},
    measure(stereoL:number,stereoR:number,monitorL:number,monitorR:number) {
      const l=weightLeft(monitorL), r=weightRight(monitorR);
      sum+=l*l+r*r;
      a+=stereoL*stereoL;b+=stereoR*stereoR;ab+=stereoL*stereoR;
      peak=Math.max(peak,peakLeft(monitorL),peakRight(monitorR));
      if (++frame<reportEvery) return false;
      const i=blocks%power.length, j=blocks%left.length;
      total+=sum-power[i];power[i]=sum;
      left[j]=a;right[j]=b;cross[j]=ab;
      blocks++;
      frame=0;sum=0;a=0;b=0;ab=0;
      return true;
    },
    read():MonitorMetrics {
      const sumLeft=left.reduce((x,y)=>x+y), sumRight=right.reduce((x,y)=>x+y), crossSum=cross.reduce((x,y)=>x+y);
      const validStereo=Math.min(sumLeft,sumRight)>reportEvery*left.length*1e-10;
      const mean=Math.max(0,total)/(power.length*reportEvery);
      return {
        loudness:blocks>=power.length && mean>1e-12 ? -.691+10*Math.log10(mean) : null,
        truePeak:peak>0 ? 20*Math.log10(peak) : null,
        correlation:validStereo ? Math.max(-1,Math.min(1,crossSum/Math.sqrt(sumLeft*sumRight))) : null,
      };
    },
  };
}
