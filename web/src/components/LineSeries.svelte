<script lang="ts">
  // One or more lines by fiscal year with dots, an optional horizontal reference line, and the year range from the URL.
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import { fy, pivot, emptyNote, moneyTick, millions } from '../lib/chart';
  import { dollars } from '../lib/format';

  type Row = { fiscal_year: number; series: string; value: number };
  // `kind` picks the number format (functions cannot be passed from an Astro page into an island)
  let { id, rows: allRows, series, title, filename, note = '', kind, reference = null }: {
    id: string; rows: Row[]; series: { name: string; color: string }[]; title: string; filename: string; note?: string;
    kind: 'money' | 'months';
    reference?: { value: number; label: string; color: string } | null;
  } = $props();

  const tick = (v: number) => (kind === 'money' ? moneyTick(v) : String(v));
  const label = (v: number) => (kind === 'money' ? millions(v, 1) : v.toFixed(1));
  const valueTitle = (v: number) => (kind === 'money' ? dollars(v) : `${v.toFixed(1)} months`);
  const rows = $derived(allRows.filter((r) => inRange(url, r.fiscal_year)));
  const names = $derived(series.map((s) => s.name));
  const table = $derived(pivot(rows, 'fiscal_year', 'series', 'value', names));

  function build(width: number) {
    if (!rows.length) return emptyNote();
    const narrow = width < 520;
    const last = Math.max(...rows.map((r) => r.fiscal_year));
    const top = Math.max(...rows.map((r) => r.value), reference?.value ?? 0);
    return Plot.plot({
      width, height: 300, marginLeft: 48, marginRight: narrow ? 40 : 56, style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, ticks: Math.min(8, new Set(rows.map((r) => r.fiscal_year)).size) },
      y: { label: null, grid: true, tickFormat: tick, domain: [0, top * 1.1] },
      color: { domain: names, range: series.map((s) => s.color), legend: names.length > 1 },
      marks: [
        Plot.lineY(rows, { x: 'fiscal_year', y: 'value', stroke: 'series', strokeWidth: 2.2 }),
        Plot.dot(rows, { x: 'fiscal_year', y: 'value', fill: 'series', r: 3, title: (r: Row) => `${fy(r.fiscal_year)} ${r.series}\n${valueTitle(r.value)}` }),
        Plot.text(rows.filter((r) => r.fiscal_year === last), { x: 'fiscal_year', y: 'value', text: (r: Row) => label(r.value), dx: 8, textAnchor: 'start', fontSize: 11 }),
        ...(reference ? [Plot.ruleY([reference.value], { stroke: reference.color, strokeDasharray: '4 3' }),
          Plot.text([reference], { x: rows[0].fiscal_year, y: reference.value, text: 'label', dy: -6, textAnchor: 'start', fill: reference.color, fontSize: 11 })] : []),
        Plot.ruleY([0]),
      ],
    });
  }
</script>

<ChartFrame {id} {title} {note} rows={table.rows} columns={table.columns} {filename}>
  <PlotHost {build} label={`${title}: line chart by fiscal year; the Table view has every number`} />
</ChartFrame>
