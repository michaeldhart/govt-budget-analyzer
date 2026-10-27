<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, moneyTick, millions, pivot, dollars } from '../lib/chart';

  type Row = { fiscal_year: number; category: string; gross_tax: number };
  let { rows, categories, ink, title, note = '' }: {
    rows: Row[]; categories: { name: string; color: string }[]; ink: string; title: string; note?: string;
  } = $props();

  const names = $derived(categories.map((c) => c.name));
  const totals = $derived([...Map.groupBy(rows, (r) => r.fiscal_year)].map(([y, rs]) => ({ fiscal_year: y, total: rs.reduce((s, r) => s + r.gross_tax, 0) })));
  const years = $derived(totals.map((t) => t.fiscal_year));
  const labelYears = $derived(new Set([years[0], years.at(-1), ...years.filter((y) => y % 4 === 0)]));
  const table = $derived.by(() => {
    const p = pivot(rows, 'fiscal_year', 'category', 'gross_tax', names);
    const withTotal = p.rows.map((r) => ({ ...r, total: totals.find((t) => t.fiscal_year === r.fiscal_year)!.total }));
    return { rows: withTotal, columns: [...p.columns, { key: 'total', label: 'All taxes' }] };
  });

  function build(width: number) {
    const narrow = width < 520;
    return Plot.plot({
      width, height: narrow ? 360 : 400, marginRight: 8, marginBottom: 40,
      style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0 },
      y: { label: null, tickFormat: moneyTick, grid: true, domain: [0, Math.max(...totals.map((t) => t.total)) * 1.08] },
      color: { domain: names, range: categories.map((c) => c.color), legend: true },
      marks: [
        Plot.barY(rows, { x: 'fiscal_year', y: 'gross_tax', fill: 'category', order: names, insetLeft: 1, insetRight: 1,
          title: (r: Row) => `${fy(r.fiscal_year)} ${r.category}\n${dollars(r.gross_tax)}` }),
        Plot.text(totals.filter((t) => labelYears.has(t.fiscal_year)), { x: 'fiscal_year', y: 'total', text: (t: any) => millions(t.total),
          dy: -8, fontWeight: 'bold', fill: ink, fontSize: narrow ? 10 : 12 }),
        Plot.ruleY([0]),
      ],
    });
  }
</script>

<ChartFrame {title} {note} rows={table.rows} columns={table.columns} filename="taxes-adopted-by-category.csv">
  <PlotHost {build} label={`${title}: stacked columns by fiscal year; the Table view has every number`} />
</ChartFrame>
