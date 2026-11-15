import assert from 'node:assert/strict';
import test from 'node:test';
import {matchingBrowserTone,readBrowserTones,toneSnapshot} from '../src/browserTonePresets.ts';

const tone = {preset:'album-v1/wide',bands:[{type:'peaking',freq_hz:250,gain_db:2}],
  strength:154,stereo_width:1.28,guardrails:false};

test('the snapshot keeps exact tone and intensity but no album settings or track data',()=>{
  const input={...tone,cleanup_enabled:true,repairs:[{id:'one'}],lufs:-16,voicing_enabled:true};
  const snapshot=toneSnapshot(input);
  assert.deepEqual(snapshot,{...tone,bands:[{...tone.bands[0],q:.7,enabled:true}]});
  input.bands[0]={...input.bands[0],gain_db:4};
  assert.equal(snapshot.bands[0].gain_db,2);
  assert.equal(toneSnapshot({...tone,preset:'voicing/retired-import'}),undefined);
  assert.equal(toneSnapshot({...tone,strength:201}),undefined);
});

test('browser preset storage ignores malformed entries and duplicate names',()=>{
  assert.deepEqual(readBrowserTones('bad JSON'),[]);
  const saved=readBrowserTones(JSON.stringify([
    {name:'  Stage ',tone},{name:'stage',tone},{name:'',tone},
    {name:'Invalid',tone:{...tone,stereo_width:0}},{name:'Club',tone},
  ]));
  assert.deepEqual(saved.map(item=>item.name),['Stage','Club']);
  assert.equal(matchingBrowserTone(saved,tone,'Club').name,'Club');
  assert.equal(matchingBrowserTone(saved,{...tone,strength:50}),undefined);
});
