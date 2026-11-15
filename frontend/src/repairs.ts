import type {Band} from './monitor';

export const repairTypes = {
  rumble: {title:'Reduce low-end rumble', frequency:55, type:'lowshelf', q:.7},
  presence: {title:'Soften harsh presence', frequency:3500, type:'peaking', q:1.4},
  highs: {title:'Tame bright highs', frequency:9500, type:'highshelf', q:.7},
} as const;
export type RepairKind = keyof typeof repairTypes;
export type Range = {start:number; end:number};
export type Repair = {id:string; kind:RepairKind; enabled:boolean; amount:number; frequency:number; start:number; end:number|null};
export type Finding = Range & {kind:RepairKind|'peak'; evidence_db:number; amount?:number; frequency?:number};
export type Analysis = {duration:number; windows:number; findings:Finding[]};

export const maxRepairs = 128;
export const defaultFindingCut = 2;
export const findingKey = (item:Finding) => `${item.kind}-${item.start}-${item.end}`;

export function overlappingFix(range:{kind:Finding['kind'];start:number;end:number|null}, repairs:Repair[], ignore='') {
  return repairs.find(fix => fix.id!==ignore && fix.kind===range.kind && range.start<(fix.end ?? Infinity)
    && fix.start<(range.end ?? Infinity));
}

export function findingPlan(findings:Finding[], existing:Repair[], amount:number) {
  const proposed:Repair[] = [];
  let covered=0, warnings=0, limited=0;
  for (const item of findings) {
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
