// Dollars as reported, or adjusted for inflation (constant base-year dollars) and/or divided by the resident population.
import type { Unit } from './urlParams';

export type Ref = Record<number, { factor: number; population: number; cpi_flag?: string; population_flag?: string; base: number }>;

export const isCapita = (u: Unit | null): boolean => u === 'capita' || u === 'real_capita';
export const isReal = (u: Unit | null): boolean => u === 'real' || u === 'real_capita';

/** `v` dollars of fiscal year `fy`, in the chosen unit; a year the reference does not cover is left as reported. */
export function scaleValue(v: number, fy: number, unit: Unit | null, ref?: Ref): number {
  const r = ref?.[fy];
  if (!unit || !r) return v;
  return (isReal(unit) ? v * r.factor : v) / (isCapita(unit) ? r.population : 1);
}

export function unitPhrase(unit: Unit | null, ref?: Ref): string {
  if (!unit || !ref) return '';
  const base = Object.values(ref)[0]?.base;
  return isReal(unit) && isCapita(unit) ? `per resident, in FY${base} dollars` : isReal(unit) ? `in FY${base} dollars` : 'per resident';
}

/** A sentence for the chart note saying how the numbers were adjusted and where the adjustment is provisional. */
export function unitNote(unit: Unit | null, ref: Ref | undefined, years: number[]): string {
  if (!unit || !ref) return '';
  const base = Object.values(ref)[0]?.base;
  const bits: string[] = [];
  if (isReal(unit)) bits.push(`Dollars adjusted for inflation with the CPI-U (U.S. city average, fiscal-year mean) to FY${base} dollars.`);
  if (isCapita(unit)) bits.push("Divided by the Census Bureau's population estimate for the City of Belvidere.");
  const prov = years.filter((y) => ref[y]?.cpi_flag === 'provisional' && isReal(unit));
  const carried = years.filter((y) => ref[y]?.population_flag === 'carried_forward' && isCapita(unit));
  if (prov.length) bits.push(`FY${prov.join(', FY')} uses a price level for part of the year only.`);
  if (carried.length) bits.push(`FY${carried.join(', FY')} uses the latest published population estimate.`);
  return bits.join(' ');
}
