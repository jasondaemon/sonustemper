import assert from 'node:assert/strict';
import test from 'node:test';
import {monitorPeakCeiling,peakProtection} from '../src/peakProtection.ts';

const rate=48000, latency=192;

test('peak protection preserves quiet audio without gain or stereo changes',()=>{
  const left=Float32Array.from({length:1024},(_,i)=>.5*Math.sin(i*.15));
  const right=Float32Array.from(left,x=>-.4*x);
  const outL=new Float32Array(left.length),outR=new Float32Array(right.length);
  peakProtection(rate).process(left,right,outL,outR);
  assert.deepEqual(outL.slice(latency),left.slice(0,-latency));
  assert.deepEqual(outR.slice(latency),right.slice(0,-latency));
});

test('peak protection catches linked stereo peaks across render blocks and recovers',()=>{
  const count=rate/2;
  const left=new Float32Array(count).fill(.25),right=new Float32Array(count).fill(-.125);
  const transient=384;
  left[transient]=1.6;right[transient]=-.8;
  right[transient+128]=-1.5;
  const limiter=peakProtection(rate);
  const outL=new Float32Array(count),outR=new Float32Array(count);
  for (let start=0;start<count;start+=128) {
    limiter.process(...[left,right,outL,outR].map(ch=>ch.subarray(start,start+128)));
  }
  assert.ok(outL[transient+latency] <= monitorPeakCeiling+.000001);
  assert.ok(outR[transient+latency+128] >= -monitorPeakCeiling-.000001);
  assert.ok(outL[transient+latency]>.8);
  assert.equal(outL[transient+latency],-2*outR[transient+latency]);
  assert.ok(outL[transient+latency-64]<.25,'attenuation begins before the peak');
  assert.ok(outL[count-1]>.249,'gain recovers after the peak');
  limiter.reset();
  limiter.process(left,right,outL,outR);
  assert.equal(outL[0],0,'reset discards delayed samples');
});
