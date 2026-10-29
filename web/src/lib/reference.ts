import { readData } from './data';

/** Per-fiscal-year price factor and population for the unit picker, or undefined when an entity has no reference data. */
export function referenceFor(entity: string): { units: Record<number, any>; base: number } | undefined {
  const meta = readData(entity, 'meta.json');
  if (!meta.datasets.includes('reference')) return undefined;
  const rows: any[] = readData(entity, 'reference.json').rows;
  return {
    base: rows[0].cpi_base_fy,
    units: Object.fromEntries(rows.map((r) => [r.fiscal_year, { factor: r.cpi_factor, population: r.population, cpi_flag: r.cpi_flag, population_flag: r.population_flag, base: r.cpi_base_fy }])),
  };
}
