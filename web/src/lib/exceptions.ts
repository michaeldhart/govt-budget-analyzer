import { readData } from './data';

/** The registry entries for the exception ids found in `rows` (any rows with an `exception_ids` list), keyed by id, ready to pass to charts. */
export function exceptionsUsedBy(entity: string, ...rowSets: { exception_ids?: string[] }[][]): Record<string, any> {
  const all: any[] = readData(entity, 'exceptions.json').exceptions;
  const used = new Set(rowSets.flat().flatMap((r) => r.exception_ids ?? []));
  return Object.fromEntries(all.filter((e) => used.has(e.exception_id)).map((e) => [e.exception_id, e]));
}
