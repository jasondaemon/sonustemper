import type {Band} from './monitor';

export const browserToneKey = 'sonustemper:browser-tone-presets:v1';
export const maxToneName = 60;
export const maxSavedTones = 50;

export type ToneSettings = {preset:string; bands:Band[]; strength:number; stereo_width:number; guardrails:boolean};
export type BrowserTone = {name:string; tone:ToneSettings};

function fields(value:unknown):Record<string,unknown> {
  return value && typeof value==='object' ? value as Record<string,unknown> : {};
}

function bounded(value:unknown,min:number,max:number):value is number {
  return typeof value==='number' && Number.isFinite(value) && value>=min && value<=max;
}

export function toneSnapshot(value:unknown):ToneSettings|undefined {
  const {preset,bands,strength,stereo_width,guardrails}=fields(value);
  if (typeof preset!=='string' || (preset && !/^(album-v1|builtin)\/[a-z0-9-]+$/.test(preset)) ||
      !Array.isArray(bands) || bands.length>20 ||
      !bounded(strength,0,preset.startsWith('album-v1/') ? 200 : 100) || !Number.isInteger(strength) ||
      !bounded(stereo_width,.5,1.5) || typeof guardrails!=='boolean') return;

  const normalized:Band[]=[];
  for (const item of bands) {
    const {type,freq_hz,gain_db=0,q=.7,enabled=true}=fields(item);
    if (typeof type!=='string' || !bounded(freq_hz,20,20000) || !bounded(gain_db,-24,24) ||
        !bounded(q,.1,20) || typeof enabled!=='boolean') return;
    normalized.push({type,freq_hz,gain_db,q,enabled});
  }
  return {preset,bands:normalized,strength,stereo_width,guardrails};
}

export function readBrowserTones(raw:string|null):BrowserTone[] {
  let entries:unknown;
  try {entries=JSON.parse(raw ?? '[]');}
  catch {return [];}
  if (!Array.isArray(entries)) return [];

  const result:BrowserTone[]=[];
  const seen=new Set<string>();
  for (const entry of entries) {
    const {name,tone}=fields(entry);
    if (typeof name!=='string') continue;
    const title=name.trim(), key=title.toLowerCase();
    if (!title || title.length>maxToneName || seen.has(key)) continue;
    const snapshot=toneSnapshot(tone);
    if (!snapshot) continue;
    seen.add(key);
    result.push({name:title,tone:snapshot});
    if (result.length>=maxSavedTones) break;
  }
  return result;
}

export function matchingBrowserTone(tones:BrowserTone[], value:unknown, preferred='') {
  if (!tones.length) return;
  const snapshot=toneSnapshot(value);
  if (snapshot) {
    const settings=JSON.stringify(snapshot);
    const matches=tones.filter(({tone})=>JSON.stringify(tone)===settings);
    return matches.find(({name})=>name===preferred) ?? matches[0];
  }
}
