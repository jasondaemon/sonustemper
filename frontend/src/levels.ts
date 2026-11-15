const floor = -60;
const holdSeconds = 1.15;

export type MeterChannel = {label: string; level: number; hold: number; until: number; clipped: boolean};

export function emptyMeters(): MeterChannel[] {
  return ['Left', 'Right'].map(label => ({label, level:floor, hold:floor, until:0, clipped:false}));
}

export function advanceMeter(channel: MeterChannel, peak: number, now: number, elapsed: number): MeterChannel {
  const db = peak > 0 ? Math.max(floor, Math.min(0, 20 * Math.log10(peak))) : floor;
  const level = Math.max(db, channel.level - 32 * elapsed);
  const hit = db > floor && db >= channel.hold;
  const hold = hit ? db : Math.max(level, channel.hold - 12 * Math.min(elapsed, Math.max(0, now - channel.until)));
  return {...channel, level, hold, until:hit ? now + holdSeconds : channel.until, clipped:channel.clipped || peak >= 1};
}

export function meterPosition(db:number, thresholds:number[]): number {
  const upper = thresholds.findIndex(value => value >= db);
  if (upper === 0) return 0;
  if (upper < 0) return 1;
  return (upper - 1 + (db - thresholds[upper - 1]) / (thresholds[upper] - thresholds[upper - 1])) / (thresholds.length - 1);
}
