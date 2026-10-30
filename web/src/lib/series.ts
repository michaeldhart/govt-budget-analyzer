// Helpers for the fund-organised pages: turn dataset rows into the {fiscal_year, series, value} rows the charts take.
const HUES = ['#2a78d6', '#1baf7a', '#eb6834', '#e87ba4', '#eda100', '#4a3aa7', '#52514e', '#b4b2a9', '#7a9a2e', '#2b9aa0'];

/** Rows grouped to one value per year x series name; `key` names the series (a fund, a category) and `color` may come from the dataset's own category colors. */
export function toRows(rows: any[], key: string, value = 'amount') {
  return rows.map((r) => ({ fiscal_year: r.fiscal_year, series: r[key], value: r[value], exception_ids: r.exception_ids ?? [] }));
}

/** Series list (name + color) for the names found in `rows`, largest total first; colors by the scheme's own color when given, else a fixed hue order. */
export function seriesOf(rows: any[], key: string, colors: Record<string, string> = {}, value = 'amount') {
  const totals = new Map<string, number>();
  for (const r of rows) totals.set(r[key], (totals.get(r[key]) ?? 0) + Math.abs(r[value] ?? 0));
  return [...totals].sort((a, b) => b[1] - a[1]).map(([name], i) => ({ name, color: colors[name] ?? HUES[i % HUES.length] }));
}

export const colorsOf = (scheme: any[]): Record<string, string> => Object.fromEntries(scheme.filter((c) => c.color).map((c) => [c.name, c.color]));
