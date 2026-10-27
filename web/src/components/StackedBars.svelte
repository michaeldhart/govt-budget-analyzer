<script lang="ts">
  // Stacked columns by fiscal year, one segment per series (category, function group, object...). Honors the page's year range.
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import { fy, moneyTick, millions, pivot, dollars, emptyNote } from '../lib/chart';

  type Row = { fiscal_year: number; series: string; value: number };
  let { id, rows: allRows, series: allSeries, ink, title, filename, note = '', respectHide = false, negative = false, totalLabel = 'All', maxLabels = 5 }: {
    id: string; rows: Row[]; series: { name: string; color: string }[]; ink: string; title: string; filename: string; note?: string;
    respectHide?: boolean; negative?: boolean; totalLabel?: string; maxLabels?: number;
  } = $props();

  // `hide` (the categories switched off on the taxes page) applies only where the chart says so
  const on = (name: string) => !respectHide || !url.hide.includes(name);
  const series = $derived(allSeries.filter((s) => on(s.name)));
  // one segment per year x series: several source rows (one per department, say) are summed first, or the stack shows seams
  const rows = $derived.by(() => {
    const sums = new Map<string, Row>();
    for (const r of allRows) {
      if (!inRange(url, r.fiscal_year) || !on(r.series)) continue;
      const k = `${r.fiscal_year}|${r.series}`;
      sums.set(k, { ...r, value: (sums.get(k)?.value ?? 0) + r.value });
    }
    return [...sums.values()];
  });
  const names = $derived(series.map((s) => s.name));
  const totals = $derived([...Map.groupBy(rows, (r) => r.fiscal_year)].map(([y, rs]) => ({ fiscal_year: y, total: rs.reduce((s, r) => s + r.value, 0) })));
  const years = $derived(totals.map((t) => t.fiscal_year));
  const labelYears = $derived(new Set([years[0], years.at(-1), ...years.filter((y) => y % 4 === 0)].slice(0, maxLabels + 3)));
  const table = $derived.by(() => {
    const p = pivot(rows, 'fiscal_year', 'series', 'value', names);
    return { rows: p.rows.map((r) => ({ ...r, total: totals.find((t) => t.fiscal_year === r.fiscal_year)!.total })),
             columns: [...p.columns, { key: 'total', label: totalLabel }] };
  });

  function build(width: number) {
    if (!rows.length) return emptyNote();
    const narrow = width < 520;
    const top = Math.max(...totals.map((t) => t.total)), bottom = Math.min(...totals.map((t) => t.total));
    return Plot.plot({
      width, height: narrow ? 360 : 400, marginLeft: negative ? 60 : 44, marginRight: 8, marginBottom: 40,
      style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0 },
      y: { label: null, tickFormat: moneyTick, grid: true, domain: negative ? [bottom * 1.1, 0] : [0, top * 1.08] },
      color: { domain: names, range: series.map((s) => s.color), legend: true },
      marks: [
        Plot.barY(rows, { x: 'fiscal_year', y: 'value', fill: 'series', order: names, insetLeft: 1, insetRight: 1,
          title: (r: Row) => `${fy(r.fiscal_year)} ${r.series}\n${dollars(r.value)}` }),
        ...(negative ? [] : [Plot.text(totals.filter((t) => labelYears.has(t.fiscal_year)), { x: 'fiscal_year', y: 'total', text: (t: any) => millions(t.total),
          dy: -8, fontWeight: 'bold', fill: ink, fontSize: narrow ? 10 : 12 })]),
        Plot.ruleY([0]),
      ],
    });
  }
</script>

<ChartFrame {id} {title} {note} rows={table.rows} columns={table.columns} {filename}>
  <PlotHost {build} label={`${title}: stacked columns by fiscal year; the Table view has every number`} />
</ChartFrame>
