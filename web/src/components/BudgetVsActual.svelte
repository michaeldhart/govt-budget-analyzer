<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, moneyTick, millions, emptyNote } from '../lib/chart';

  type Row = { fiscal_year: number; budget: number | null; actual: number | null; variance_pct: number | null };
  // Reused for taxes and for spending: `labels` carry the wording, `yDomain` the line chart's range (default: fit the data).
  type Labels = { lineTitle: string; varianceTitle: string; budget: string; actual: string; note?: string; fileStem: string; aboveBelow?: string };
  let { rows: allRows, p, labels, yDomain }: { rows: Row[]; p: Record<string, string>; labels: Labels; yDomain?: [number, number] } = $props();

  const rows = $derived(allRows.filter((r) => inRange(url, r.fiscal_year)));
  const years = $derived(rows.map((r) => r.fiscal_year));
  const tidy = $derived(rows.flatMap((r) => [
    ...(r.budget != null ? [{ fiscal_year: r.fiscal_year, series: labels.budget, amount: r.budget }] : []),
    ...(r.actual != null ? [{ fiscal_year: r.fiscal_year, series: labels.actual, amount: r.actual }] : []),
  ]));
  const variance = $derived(rows.filter((r) => r.variance_pct != null));
  const table = $derived({ columns: [{ key: 'fiscal_year', label: 'Fiscal year' }, { key: 'budget', label: `${labels.budget} ($)` },
                            { key: 'actual', label: `${labels.actual} ($)` }, { key: 'variance_pct', label: 'Actual vs budget (fraction)' }] });

  function lines(width: number) {
    if (!tidy.length) return emptyNote();
    return Plot.plot({
      width, height: 300, marginRight: 44, style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, domain: [Math.min(...years), Math.max(...years)], ticks: Math.min(8, years.length) },
      y: { label: null, grid: true, tickFormat: moneyTick, domain: yDomain ?? [0, Math.max(...tidy.map((t) => t.amount)) * 1.08] },
      color: { domain: [labels.budget, labels.actual], range: [p.adopted_gray, p.ink], legend: true },
      marks: [
        Plot.lineY(tidy, { x: 'fiscal_year', y: 'amount', stroke: 'series', strokeWidth: 2.2 }),
        Plot.dot(tidy, { x: 'fiscal_year', y: 'amount', fill: 'series', r: 3.2,
          title: (r: any) => `${fy(r.fiscal_year)} ${r.series}\n${r.amount.toLocaleString('en-US')}` }),
        Plot.text(tidy.filter((r) => r.fiscal_year === Math.max(...tidy.filter((t) => t.series === r.series).map((t) => t.fiscal_year))),
          { x: 'fiscal_year', y: 'amount', text: (r: any) => millions(r.amount), dy: -10, fill: p.ink2, fontSize: 11 }),
      ],
    });
  }

  function bars(width: number) {
    if (!variance.length) return emptyNote();
    const narrow = width < 520;
    return Plot.plot({
      width, height: 260, marginLeft: 48, marginRight: 8, marginBottom: 40, style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0 },
      y: { label: null, grid: true, domain: [Math.min(...variance.map((r) => r.variance_pct!), 0) - 0.04, Math.max(...variance.map((r) => r.variance_pct!)) + 0.03],
        tickFormat: (v: number) => (v ? `${v > 0 ? '+' : ''}${(v * 100).toFixed(0)}%` : '0') },
      marks: [
        Plot.barY(variance, { x: 'fiscal_year', y: 'variance_pct', fill: (r: Row) => (r.variance_pct! >= 0 ? p.up : p.down), insetLeft: 1, insetRight: 1 }),
        ...[[(r: Row) => r.variance_pct! >= 0, -8], [(r: Row) => r.variance_pct! < 0, 8]].map(([keep, dy]: any) =>
          Plot.text(variance.filter(keep), { x: 'fiscal_year', y: 'variance_pct', textAnchor: 'middle', dy,
            text: (r: Row) => `${r.variance_pct! >= 0 ? '+' : ''}${(r.variance_pct! * 100).toFixed(0)}%`, fill: p.ink, fontSize: narrow ? 9 : 11 })),
        Plot.ruleY([0], { stroke: p.ink2 }),
      ],
    });
  }
</script>

<ChartFrame id="budget-vs-actual" title={labels.lineTitle} rows={rows as any} columns={table.columns} filename={`${labels.fileStem}-budget-vs-actual.csv`} note={labels.note}>
  <PlotHost build={lines} label="Line chart of total budgeted and audited taxes by fiscal year; the Table view has every number" />
</ChartFrame>
<ChartFrame id="variance" title={labels.varianceTitle} rows={variance as any} columns={table.columns} filename={`${labels.fileStem}-variance.csv`}>
  <PlotHost build={bars} label="Bar chart of audited actual minus budget as a percent of budget, by fiscal year; the Table view has every number" />
</ChartFrame>
