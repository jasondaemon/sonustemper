export type NoiseFindingKind = 'quiet_fuzz'|'narrow_tone';
export type NoiseSettings = {
  f_low:number; f_high:number; band_depth_db:number; afftdn_strength:number; hp_hz:number; lp_hz:number;
  t0_sec:number; t1_sec:number; apply_scope:'global'|'selection';
  method?:'legacy'|'spectral'|'profile'; label?:NoiseFindingKind;
};
export type NoiseSelection = NoiseSettings & {id:string; enabled:boolean};

export const defaultNoise:NoiseSettings = {
  f_low:100, f_high:500, band_depth_db:9, afftdn_strength:0, hp_hz:0, lp_hz:0,
  t0_sec:0, t1_sec:0, apply_scope:'global', method:'spectral',
};

export function savedNoise(settings:{noise?:Partial<NoiseSettings>;noise_enabled?:boolean;noise_selections?:NoiseSelection[]}) {
  if (settings.noise_selections) return settings.noise_selections;
  if (!settings.noise) return [];
  const oldDefaults = {...defaultNoise,band_depth_db:18,afftdn_strength:.35,method:'legacy' as const};
  if (settings.noise_enabled === false && !Object.entries(settings.noise).some(([key,value]) => value !== oldDefaults[key as keyof NoiseSettings])) return [];
  const legacy = {...oldDefaults,...settings.noise};
  // Old exports treated an invalid selected time range as whole-song processing.
  if (legacy.t1_sec - legacy.t0_sec < .01) legacy.apply_scope='global';
  return [{...legacy, id:'imported', enabled:true}];
}
