<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, pivot, dollars } from '../lib/chart';

  type Row = { fiscal_year: number; category: string; offsets: number };
  let { rows, categories, title, note = '' }: {
    rows: Row[]; categories: { name: string; color: string }[]; title: string; note?: string;
  } = $props();

  const used = $derived(categories.filter((c) => rows.some((r) => r.category === c.name && r.offsets !== 0)));
  const names = $derived(used.map((c) => c.name));
  const data = $derived(rows.filter((r) => names.includes(r.category)));
  const table = $derived(pivot(data, 'fiscal_year', 'category', 'offsets', names));

  function build(width: number) {
    const narrow = width < 520;
    return Plot.plot({
      width, height: 260, marginLeft: 60, marginRight: 8, marginBottom: 40, style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0 },
      y: { label: null, grid: true, tickFormat: (v: number) => (v ? `-$${Math.abs(v / 1e3)}K` : '0') },
      color: { domain: names, range: used.map((c) => c.color), legend: true },
      marks: [
        Plot.barY(data, { x: 'fiscal_year', y: 'offsets', fill: 'category', order: names, insetLeft: 1, insetRight: 1,
          title: (r: Row) => `${fy(r.fiscal_year)} ${r.category}\n${dollars(r.offsets)}` }),
        Plot.ruleY([0]),
      ],
    });
  }
</script>

<ChartFrame {title} {note} rows={table.rows} columns={table.columns} filename="taxes-adopted-offsets.csv">
  <PlotHost {build} label={`${title}: stacked columns by fiscal year; the Table view has every number`} />
</ChartFrame>
