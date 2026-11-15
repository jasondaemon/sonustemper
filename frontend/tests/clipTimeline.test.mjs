import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeClips,songClips} from '../src/clipTimeline.ts';

test('clip times follow media time and playback rate, not the audio clock',()=>{
  const clips=[{start:7.5,end:7.75,channels:1},{start:7.75,end:8,channels:2}];
  assert.deepEqual(songClips(clips,8,20,2,7.6,90),[
    {start:19.2,end:19.5,channels:1},{start:19.5,end:20,channels:2},
  ]);
  assert.deepEqual(songClips(clips,8,0,1,8,90),[]);
});

test('only nearby clip events merge and a replay preserves the original positions',()=>{
  const prior=[{start:2,end:2.1,channels:1},{start:9,end:9.1,channels:2}];
  assert.deepEqual(mergeClips(prior,[{start:2.04,end:2.16,channels:2},{start:2.4,end:2.5,channels:1}]),[
    {start:2,end:2.16,channels:3},{start:2.4,end:2.5,channels:1},{start:9,end:9.1,channels:2},
  ]);
  assert.equal(prior[0].end,2.1);
});
