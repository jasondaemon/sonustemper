import type {Band} from './monitor';
import {defaultNoise, type NoiseFindingKind, type NoiseSelection} from './noiseSelections';

export const repairTypes = {
  rumble: {title:'Reduce low-end rumble', frequency:55, type:'lowshelf', q:.7},
  presence: {title:'Soften harsh presence', frequency:3500, type:'peaking', q:1.4},
  highs: {title:'Tame bright highs', frequency:9500, type:'highshelf', q:.7},
} as const;
export type RepairKind = keyof typeof repairTypes;
export type Range = {start:number; end:number};
export type Repair = {id:string; kind:RepairKind; enabled:boolean; amount:number; frequency:number; start:number; end:number|null};
export type Finding = Range & {kind:RepairKind|'peak'|NoiseFindingKind; evidence_db:number; amount?:number; frequency?:number;
  f_low?:number; f_high?:number; method?:'spectral'|'profile'};
export type Analysis = {duration:number; windows:number; findings:Finding[]};
export type ReviewItem = {id:string;kind:Finding['kind'];start:number;end:number|null;fix?:Repair;noise?:NoiseSelection;findings:Finding[];number?:number};

export const isNoiseFinding = (kind:Finding['kind']):kind is NoiseFindingKind => kind==='quiet_fuzz'||kind==='narrow_tone';
export function findingTitle(kind:Finding['kind']) {
  if (kind==='quiet_fuzz') return 'Possible quiet-gap fuzz';
  if (kind==='narrow_tone') return 'Possible narrow tone';
  return kind==='peak' ? 'Near full scale' : repairTypes[kind].title;
}

export const maxRepairs = 128;
export const defaultFindingCut = 2;
export const findingKey = (item:Finding) => `${item.kind}-${item.start}-${item.end}${isNoiseFinding(item.kind) ? `-${item.f_low}` : ''}`;

export function noiseForFinding(item:Finding):NoiseSelection|undefined {
  if (!isNoiseFinding(item.kind) || !item.f_low || !item.f_high) return;
  return {...defaultNoise,id:`finding-${findingKey(item)}`, enabled:true,label:item.kind,
    f_low:item.f_low,f_high:item.f_high,band_depth_db:item.amount ?? 8,method:item.method ?? 'spectral',
    apply_scope:'selection',t0_sec:item.start,t1_sec:item.end};
}

export function overlappingFix(range:{kind:Finding['kind'];start:number;end:number|null}, repairs:Repair[], ignore='') {
  return repairs.find(fix => fix.id!==ignore && fix.kind===range.kind && range.start<(fix.end ?? Infinity)
    && fix.start<(range.end ?? Infinity));
}

export function reviewItems(findings:Finding[], repairs:Repair[], selections:NoiseSelection[]): ReviewItem[] {
  const applied = repairs.map((fix, index):ReviewItem => ({
    id:fix.id,kind:fix.kind,start:fix.start,end:fix.end,fix,findings:[],number:index+1,
  }));
  for (const noise of selections) if (noise.label) {
    applied.push({id:noise.id,kind:noise.label,start:noise.apply_scope==='global'?0:noise.t0_sec,
      end:noise.apply_scope==='global'?null:noise.t1_sec,noise,findings:[]});
  }
  const queue=[...applied];
  for (const finding of findings) {
    const id=`finding-${findingKey(finding)}`;
    const cover=overlappingFix(finding,repairs);
    const row=applied.find(item=>item.id===id) ?? (cover && applied.find(item=>item.id===cover.id));
    if (row) row.findings.push(finding);
    else queue.push({id,kind:finding.kind,start:finding.start,end:finding.end,findings:[finding]});
  }
  return queue.sort((a,b)=>a.start-b.start || a.kind.localeCompare(b.kind));
}

export function findingPlan(findings:Finding[], existing:Repair[], amount:number) {
  const proposed:Repair[] = [];
  let covered=0, warnings=0, limited=0;
  for (const item of findings) {
    if (isNoiseFinding(item.kind)) continue;
    if (item.kind==='peak' || item.end-item.start<.05) {warnings++;continue;}
    if (overlappingFix(item,existing) || overlappingFix(item,proposed)) {covered++;continue;}
    if (existing.length+proposed.length>=maxRepairs) {limited++;continue;}
    proposed.push({id:`finding-${findingKey(item)}`,kind:item.kind,enabled:true,amount,
      start:item.start,end:item.end,frequency:item.frequency ?? repairTypes[item.kind].frequency});
  }
  return {proposed,covered,warnings,limited};
}

export function repairBand(fix:Repair): Band {
  const {type,q} = repairTypes[fix.kind];
  return {type,q,freq_hz:fix.frequency,gain_db:-fix.amount,enabled:fix.enabled};
}

export function timeLabel(seconds:number) {
  const total = Math.round(seconds * 10);
  return `${Math.floor(total/600)}:${(total%600/10).toFixed(1).padStart(4,'0')}`;
}

export function rangeLabel(range:{start:number;end:number|null}) {
  return range.end === null ? 'Whole song' : `${timeLabel(range.start)}–${timeLabel(range.end)}`;
}

export const repairColors = ['#087b9a','#a0550d','#7656a8','#277b58','#ba4e77','#3f67ba'];
