// Page state that lives in the query string, so a link reproduces what the sender saw:
//   ?from=2018&to=2026         year range for every chart on the page
//   &hide=Utility+taxes        categories switched off (repeat the parameter)
//   &table=composition         charts shown as a table instead of a chart (repeat the parameter)
export type UrlState = { from: number | null; to: number | null; hide: string[]; table: string[] };

export const emptyState = (): UrlState => ({ from: null, to: null, hide: [], table: [] });

const year = (v: string | null): number | null => (v && /^\d{4}$/.test(v) ? Number(v) : null);

export function parse(search: string): UrlState {
  const p = new URLSearchParams(search);
  return { from: year(p.get('from')), to: year(p.get('to')), hide: [...new Set(p.getAll('hide'))], table: [...new Set(p.getAll('table'))] };
}

/** Query string without the leading '?'; defaults are left out so an untouched page keeps a clean URL. */
export function serialize(s: UrlState): string {
  const p = new URLSearchParams();
  if (s.from != null) p.set('from', String(s.from));
  if (s.to != null) p.set('to', String(s.to));
  s.hide.forEach((h) => p.append('hide', h));
  s.table.forEach((t) => p.append('table', t));
  return p.toString();
}

/** Is fiscal year `fy` inside the selected range? An open end is unbounded; a reversed range is read the right way round. */
export function inRange(s: Pick<UrlState, 'from' | 'to'>, fy: number): boolean {
  const a = s.from ?? -Infinity, b = s.to ?? Infinity;
  return fy >= Math.min(a, b) && fy <= Math.max(a, b);
}
