import {monitorMath} from './meterMath';
import type {MonitorMetrics} from './meterMath';
import type {ClipSpan} from './clipTimeline';
import {peakProtection} from './peakProtection';

declare const sampleRate:number;
declare const currentFrame:number;
declare function registerProcessor(name:string, processor:typeof AudioWorkletProcessor):void;
declare class AudioWorkletProcessor {
  readonly port:MessagePort;
}

export type MeterMessage = {active:boolean}|{epoch:number;peakOnly?:boolean};
export type MeterReading = {epoch:number;metrics:MonitorMetrics;clips:ClipSpan[]};
export type ProtectionMessage = {enabled:boolean}|{reset:true};

class StudioMeter extends AudioWorkletProcessor {
  private meter=monitorMath(sampleRate);
  private active=false;
  private epoch=0;
  private clips:ClipSpan[]=[];
  constructor() {
    super();
    this.port.onmessage=({data}:MessageEvent<MeterMessage>) => {
      if ('active' in data) {
        if (this.active && !data.active) this.publish();
        this.active=data.active;
      } else {
        this.epoch=data.epoch;
        if (data.peakOnly) this.meter.resetPeak();
        else {this.meter=monitorMath(sampleRate);this.clips=[];}
        this.publish();
      }
    };
  }
  private publish() {
    this.port.postMessage({epoch:this.epoch,metrics:this.meter.read(),clips:this.clips} satisfies MeterReading);
    this.clips=[];
  }
  process(inputs:Float32Array[][]) {
    const [stereo,output]=inputs;
    if (!this.active || !stereo?.length || !output?.length) return true;
    const [sl,sr=sl]=stereo, [ol,or=ol]=output;
    let first=ol.length,last=-1,channels=0,ready=false;
    for(let i=0;i<ol.length;i++) {
      const flags=(Math.abs(ol[i])>=1 ? 1 : 0) | (Math.abs(or[i])>=1 ? 2 : 0);
      if (flags) {first=Math.min(first,i);last=i;channels|=flags;}
      if (this.meter.measure(sl[i],sr[i],ol[i],or[i])) ready=true;
    }
    if (channels) this.clips.push({start:(currentFrame+first)/sampleRate,end:(currentFrame+last+1)/sampleRate,channels});
    if (ready) this.publish();
    return true;
  }
}

registerProcessor('studio-meter',StudioMeter);

class StudioPeakProtection extends AudioWorkletProcessor {
  private limiter=peakProtection(sampleRate);
  private enabled=false;
  constructor() {
    super();
    this.port.onmessage=({data}:MessageEvent<ProtectionMessage>)=>{
      if ('reset' in data) this.limiter.reset();
      else if (this.enabled!==data.enabled) {
        this.enabled=data.enabled;
        this.limiter.reset();
      }
    };
  }
  process([input]:Float32Array[][],[output]:Float32Array[][]) {
    if (!input?.length || !output?.length) return true;
    const [left,right=left]=input, [outL,outR]=output;
    if (this.enabled) this.limiter.process(left,right,outL,outR);
    else {outL.set(left);outR.set(right);}
    return true;
  }
}

registerProcessor('studio-peak-protection',StudioPeakProtection);
