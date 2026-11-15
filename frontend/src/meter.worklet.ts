import {monitorMath} from './meterMath';

declare const sampleRate:number;
declare function registerProcessor(name:string, processor:typeof AudioWorkletProcessor):void;
declare class AudioWorkletProcessor {
  readonly port:MessagePort;
}

export type MeterMessage = {active:boolean}|{epoch:number;peakOnly?:boolean};

class StudioMeter extends AudioWorkletProcessor {
  private meter=monitorMath(sampleRate);
  private active=false;
  private epoch=0;
  constructor() {
    super();
    this.port.onmessage=({data}:MessageEvent<MeterMessage>) => {
      if ('active' in data) {
        if (this.active && !data.active) this.publish();
        this.active=data.active;
      } else {
        this.epoch=data.epoch;
        if (data.peakOnly) this.meter.resetPeak();
        else this.meter=monitorMath(sampleRate);
        this.publish();
      }
    };
  }
  private publish() {
    this.port.postMessage({epoch:this.epoch,metrics:this.meter.read()});
  }
  process(inputs:Float32Array[][]) {
    const [stereo,output]=inputs;
    if (!this.active || !stereo?.length || !output?.length) return true;
    const [sl,sr=sl]=stereo, [ol,or=ol]=output;
    for(let i=0;i<ol.length;i++) if (this.meter.measure(sl[i],sr[i],ol[i],or[i])) this.publish();
    return true;
  }
}

registerProcessor('studio-meter',StudioMeter);
