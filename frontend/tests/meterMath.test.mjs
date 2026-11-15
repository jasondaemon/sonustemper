import test from 'node:test';
import assert from 'node:assert/strict';
import { monitorMath } from '../src/meterMath.ts';

function sine(meter, rate, seconds, frequency, amplitude, phase=0, inverted=false, mono=false) {
  let sample=0;
  for(let i=0;i<rate*seconds;i++) {
    const ramp=Math.min(1, i/(rate*.01));
    const left=ramp*amplitude*Math.sin(2*Math.PI*frequency*i/rate+phase);
    const right=inverted ? -left : left;
    const fold=(left+right)/2;
    sample=Math.max(sample,Math.abs(left));
    meter.measure(left,right,mono?fold:left,mono?fold:right);
  }
  return 20*Math.log10(sample);
}

test('calibrated stereo tone reads -23 LUFS only after three seconds', () => {
  for(const rate of [44100,48000,96000]) {
    const meter=monitorMath(rate);
    sine(meter,rate,2,1000,10**(-23/20));
    assert.equal(meter.read().loudness,null);
    sine(meter,rate,1,1000,10**(-23/20));
    assert.ok(Math.abs(meter.read().loudness+23)<.1);
    assert.equal(meter.read().correlation,1);
    sine(meter,rate,3,1000,10**(-33/20));
    assert.ok(Math.abs(meter.read().loudness+33)<.1);
  }
});

test('true peak detects inter-sample overload and resets without changing loudness', () => {
  const rate=48000, meter=monitorMath(rate);
  const samplePeak=sine(meter,rate,3,rate/4,1.41,Math.PI/4);
  assert.ok(samplePeak<0);
  const prior=meter.read();
  assert.ok(prior.truePeak>2.6 && prior.truePeak<3.2, prior.truePeak);
  meter.resetPeak();
  assert.equal(meter.read().truePeak,null);
  assert.equal(meter.read().loudness,prior.loudness);
});

test('stereo correlation stays negative while monitor audio cancels in mono', () => {
  const rate=44100, meter=monitorMath(rate);
  sine(meter,rate,3,440,.12,0,true,true);
  assert.deepEqual(meter.read(),{correlation:-1,truePeak:null,loudness:null});
});
