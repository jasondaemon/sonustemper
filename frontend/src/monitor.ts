/// <reference types="vite/client" />
import meterURL from './meter.worklet.ts?worker&url';
import type {MonitorMetrics} from './meterMath';
import type {MeterMessage, MeterReading} from './meter.worklet';
import {songClips, type ClipSpan} from './clipTimeline';

export type Band = {type: string; freq_hz: number; gain_db: number; q: number; enabled?: boolean};

export function activeBand(band: Band, rate: number) {
  return band.enabled !== false && Number.isFinite(band.freq_hz) && band.freq_hz > 0 && band.freq_hz < rate/2 &&
    Number.isFinite(band.gain_db) && Number.isFinite(band.q) && band.q > 0;
}

const isShelf = (band:Band) => band.type === 'lowshelf' || band.type === 'highshelf';
const webQ = (band:Band) => ['lowpass','highpass'].includes(band.type) ? 20 * Math.log10(band.q) : band.q;

export function filterNode(context: BaseAudioContext, band: Band) {
  if (!activeBand(band,context.sampleRate)) return context.createIIRFilter([1],[1]);
  if (isShelf(band)) {
    // Cookbook shelves with the Q-based damping used by FFmpeg's bass/treble.
    const direction = band.type === 'lowshelf' ? 1 : -1;
    const omega = 2*Math.PI*band.freq_hz/context.sampleRate;
    const A = 10 ** (band.gain_db/40);
    const center = A+1, slope = direction*(A-1), cosine = Math.cos(omega);
    const damping = Math.sqrt(2*A)*Math.sin(omega)/(2*band.q);
    return context.createIIRFilter([
      A*(center - slope*cosine + damping), 2*A*(slope - center*cosine), A*(center - slope*cosine - damping),
    ], [
      center + slope*cosine + damping, -2*(slope + center*cosine), center + slope*cosine - damping,
    ]);
  }
  const node = context.createBiquadFilter();
  const aliases:Record<string,BiquadFilterType> = {peak:'peaking',bell:'peaking',bandreject:'notch'};
  node.type = aliases[band.type] ?? band.type as BiquadFilterType;
  node.frequency.value = band.freq_hz;
  node.gain.value = band.gain_db;
  node.Q.value = webQ(band);
  return node;
}

export function monitor(media: HTMLAudioElement, sampleRate:number|undefined, report:(metrics:MonitorMetrics)=>void,
                        unavailable:()=>void, reportClips:(clips:ClipSpan[])=>void) {
  const context = openContext(sampleRate);
  const input = context.createMediaElementSource(media);
  const analyser = context.createAnalyser();
  analyser.fftSize = 4096;
  analyser.connect(context.destination);
  // Match playback's speaker mix before splitting, so mono plays and meters on both sides.
  const stereo = context.createGain();
  stereo.channelCount = 2;
  stereo.channelCountMode = 'explicit';
  const dry=context.createGain(), mono=context.createGain(), half=context.createGain();
  const stereoSplit=context.createChannelSplitter(2), folded=context.createChannelMerger(2);
  mono.gain.value=0;half.gain.value=.5;
  stereo.connect(dry).connect(analyser);
  stereo.connect(stereoSplit);
  stereoSplit.connect(half,0);
  stereoSplit.connect(half,1);
  half.connect(folded,0,0);
  half.connect(folded,0,1);
  folded.connect(mono).connect(analyser);
  const splitter = context.createChannelSplitter(2);
  analyser.connect(splitter);
  const channels = [context.createAnalyser(), context.createAnalyser()];
  const samples = new Float32Array(2048);
  for (const [index, channel] of channels.entries()) {
    channel.fftSize = samples.length;
    splitter.connect(channel, index);
  }
  let meter:AudioWorkletNode|undefined, meterOutput:GainNode|undefined, meterEpoch=0, closed=false;
  let meterActive=!media.paused && media.readyState>=3;
  let timestamping=false, clipFrom=context.currentTime, stopClock=context.currentTime;
  const forgetClipTiming=()=>{clipFrom=context.currentTime;};
  const send=(message:MeterMessage)=>meter?.port.postMessage(message);
  const setActive=(active:boolean)=>{meterActive=active;send({active});};
  const play=()=>setActive(true);
  const stop=()=>{if (meterActive) stopClock=context.currentTime;setActive(false);};
  const timingEvents=['seeking','seeked','ratechange','emptied'];
  const stopEvents=['pause','waiting','ended','emptied'];
  media.addEventListener('playing',play);
  for (const event of stopEvents) media.addEventListener(event,stop);
  for (const event of timingEvents) media.addEventListener(event,forgetClipTiming);
  void (async()=>{
    try {
      await context.audioWorklet.addModule(meterURL);
      if (closed) return;
      meter=new AudioWorkletNode(context,'studio-meter',{numberOfInputs:2,numberOfOutputs:1,
        outputChannelCount:[1],channelCount:2,channelCountMode:'explicit'});
      meter.port.onmessage=({data}:MessageEvent<MeterReading>)=>{
        if (closed || data.epoch!==meterEpoch) return;
        timestamping=true;
        report(data.metrics);
        if (data.clips.length && !media.seeking) {
          const clips=songClips(data.clips,meterActive?context.currentTime:stopClock,
            media.currentTime,media.playbackRate,clipFrom,media.duration);
          if (clips.length) reportClips(clips);
        }
      };
      meter.onprocessorerror=()=>{timestamping=false;unavailable();};
      stereo.connect(meter,0,0);
      analyser.connect(meter,0,1);
      meterOutput=context.createGain();
      meterOutput.gain.value=0;
      meter.connect(meterOutput).connect(context.destination);
      send({epoch:meterEpoch});
      send({active:meterActive});
    } catch {
      if (!closed) unavailable();
    }
  })();
  type Path = {entry:AudioNode; mix:GainNode; nodes:AudioNode[]; filters:(IIRFilterNode|BiquadFilterNode)[]; timer?:ReturnType<typeof setTimeout>};
  let path:Path|undefined;
  const paths = new Set<Path>();
  let layout = "";
  function disconnect(previous:Path) {
    clearTimeout(previous.timer);
    input.disconnect(previous.entry);
    previous.nodes.forEach(node => node.disconnect());
    paths.delete(previous);
  }
  return {
    context, analyser,
    timestampsClips:()=>timestamping,
    forgetClipTiming,
    setMono(enabled:boolean) {
      dry.gain.setTargetAtTime(enabled?0:1,context.currentTime,.008);
      mono.gain.setTargetAtTime(enabled?1:0,context.currentTime,.008);
    },
    resetMetrics(peakOnly=false) {send({epoch:++meterEpoch,peakOnly});},
    readPeaks(peaks: number[]) {
      for (const [index, channel] of channels.entries()) {
        channel.getFloatTimeDomainData(samples);
        let peak = 0;
        for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
        peaks[index] = peak;
      }
    },
    update(bands: Band[], width: number) {
      const active = bands.filter(band => activeBand(band,context.sampleRate));
      const nextLayout = JSON.stringify([active.map(band => isShelf(band) ? [band.type,band.freq_hz,band.gain_db,band.q] : band.type), width]);
      if (path && layout === nextLayout) {
        for (const [index, band] of active.entries()) {
          const filter = path.filters[index];
          if (!('Q' in filter)) continue;
          filter.frequency.setTargetAtTime(band.freq_hz, context.currentTime, .012);
          filter.gain.setTargetAtTime(band.gain_db, context.currentTime, .012);
          filter.Q.setTargetAtTime(webQ(band), context.currentTime, .012);
        }
        return;
      }
      layout = nextLayout;
      const entry = context.createGain(), mix = context.createGain();
      const filters = active.map(band => filterNode(context, band));
      const nodes:AudioNode[] = [entry,mix,...filters];
      let tail: AudioNode = entry;
      for (const filter of filters) { tail.connect(filter); tail = filter; }
      if (width !== 1) {
        const speakers = context.createGain();
        speakers.channelCount = 2;
        speakers.channelCountMode = 'explicit';
        const split = context.createChannelSplitter(2), merge = context.createChannelMerger(2);
        tail.connect(speakers);
        speakers.connect(split);
        nodes.push(speakers, split, merge);
        for (let channel = 0; channel < 2; channel++) {
          for (let destination = 0; destination < 2; destination++) {
            const gain = context.createGain();
            gain.gain.value = (1 + (channel === destination ? width : -width)) / 2;
            split.connect(gain, channel);
            gain.connect(merge, 0, destination);
            nodes.push(gain);
          }
        }
        tail = merge;
      }
      tail.connect(mix);
      mix.connect(stereo);
      const previous = path;
      path = {entry,mix,nodes,filters};
      paths.add(path);
      if (previous && context.state === 'running') {
        const now = context.currentTime, end = now+.018;
        mix.gain.setValueAtTime(0,now);
        mix.gain.linearRampToValueAtTime(1,end);
        if (typeof previous.mix.gain.cancelAndHoldAtTime === 'function') previous.mix.gain.cancelAndHoldAtTime(now);
        else {
          const gain = previous.mix.gain.value;
          previous.mix.gain.cancelScheduledValues(now);
          previous.mix.gain.setValueAtTime(gain,now);
        }
        previous.mix.gain.linearRampToValueAtTime(0,end);
        previous.timer = setTimeout(() => disconnect(previous),55);
      } else if (previous) disconnect(previous);
      input.connect(entry);
    },
    close() {
      closed=true;
      media.removeEventListener('playing',play);
      for (const event of stopEvents) media.removeEventListener(event,stop);
      for (const event of timingEvents) media.removeEventListener(event,forgetClipTiming);
      if (meter) {
        stereo.disconnect(meter);
        analyser.disconnect(meter);
        meter.disconnect();
        meter.port.close();
      }
      meterOutput?.disconnect();
      paths.forEach(disconnect);
      void context.close();
    }
  };
}

function openContext(sampleRate?:number) {
  try {return new AudioContext({sampleRate});}
  catch (error) {
    if (!sampleRate) throw error;
    return new AudioContext();
  }
}

export function spectrumPath(values: Float32Array, sampleRate: number) {
  return Array.from({length:160}, (_, i) => {
    const frequency = 20 * 1000 ** (i / 159);
    const bin = Math.min(values.length - 1, Math.round(frequency * values.length * 2 / sampleRate));
    const y = 150 - Math.max(0, Math.min(1, (values[bin] + 95) / 85)) * 145;
    return `${i ? 'L' : 'M'}${i * 600 / 159} ${y}`;
  }).join(' ');
}


export function defaultBands(): Band[] {
  return [
    {type:'highpass',freq_hz:30,gain_db:0,q:.71},
    {type:'lowshelf',freq_hz:100,gain_db:0,q:.71},
    {type:'peaking',freq_hz:250,gain_db:0,q:1},
    {type:'peaking',freq_hz:1000,gain_db:0,q:1},
    {type:'peaking',freq_hz:3500,gain_db:0,q:1},
    {type:'highshelf',freq_hz:10000,gain_db:0,q:.71},
    {type:'lowpass',freq_hz:18000,gain_db:0,q:.71,enabled:false},
  ];
}
