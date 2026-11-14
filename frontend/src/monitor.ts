export type Band = {type: string; freq_hz: number; gain_db: number; q: number; enabled?: boolean};

export function filterNode(context: BaseAudioContext, band: Band) {
  const node = context.createBiquadFilter();
  node.type = (['peak', 'bell'].includes(band.type) ? 'peaking' : band.type) as BiquadFilterType;
  node.frequency.value = band.freq_hz;
  node.gain.value = band.gain_db;
  node.Q.value = band.q;
  return node;
}

export function monitor(media: HTMLAudioElement) {
  const context = new AudioContext();
  const input = context.createMediaElementSource(media);
  const analyser = context.createAnalyser();
  analyser.fftSize = 4096;
  analyser.connect(context.destination);
  let nodes: AudioNode[] = [];
  let filters: BiquadFilterNode[] = [];
  let layout = "";
  return {
    context, analyser,
    update(bands: Band[], width: number) {
      const active = bands.filter(band => band.enabled !== false);
      const nextLayout = JSON.stringify([active.map(band => band.type), width]);
      if (layout === nextLayout) {
        for (const [index, band] of active.entries()) {
          filters[index].frequency.setTargetAtTime(band.freq_hz, context.currentTime, .012);
          filters[index].gain.setTargetAtTime(band.gain_db, context.currentTime, .012);
          filters[index].Q.setTargetAtTime(band.q, context.currentTime, .012);
        }
        return;
      }
      layout = nextLayout;
      input.disconnect();
      nodes.forEach(node => node.disconnect());
      filters = active.map(band => filterNode(context, band));
      nodes = [...filters];
      let tail: AudioNode = input;
      for (const filter of filters) { tail.connect(filter); tail = filter; }
      if (width !== 1) {
        const split = context.createChannelSplitter(2), merge = context.createChannelMerger(2);
        tail.channelCount = 2;
        tail.connect(split);
        nodes.push(split, merge);
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
      tail.connect(analyser);
    },
    close() { input.disconnect(); nodes.forEach(node => node.disconnect()); void context.close(); }
  };
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
