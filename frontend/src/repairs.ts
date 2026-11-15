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
