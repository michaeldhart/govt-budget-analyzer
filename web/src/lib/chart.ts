import { dollars } from './format';

export const fy = (d: number | string): string => `FY${String(d).slice(-2)}`;

/** $20M / $1.5M / $750K with only as many decimals as needed (axis ticks). */
export function moneyTick(v: number): string {
  if (v === 0) return '0';
  const a = Math.abs(v), sign = v < 0 ? '-' : '';
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  return `${sign}$${Math.round(a / 1e3).toLocaleString('en-US')}K`;
}

export const millions = (v: number, d = 1): string => `$${(v / 1e6).toFixed(d)}M`;

/** Long rows -> one row per `rowKey` with a column per distinct `colKey` value (for a chart's table view). */
export function pivot<T extends Record<string, any>>(rows: T[], rowKey: string, colKey: string, valKey: string, colOrder: string[]) {
  const by = new Map<any, Record<string, any>>();
  for (const r of rows) {
    if (!by.has(r[rowKey])) by.set(r[rowKey], { [rowKey]: r[rowKey] });
    by.get(r[rowKey])![r[colKey]] = r[valKey];
  }
  return {
    rows: [...by.values()].sort((a, b) => a[rowKey] - b[rowKey]),
    columns: [{ key: rowKey, label: 'Fiscal year' }, ...colOrder.map((c) => ({ key: c, label: c }))],
  };
}

export { dollars };
