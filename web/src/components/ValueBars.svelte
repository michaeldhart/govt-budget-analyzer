<script lang="ts">
  // Single series of bars by fiscal year, blue when positive and red when negative, with a value label on each bar.
  import * as Plot from '@observablehq/plot';
  import type { Exception } from './ExceptionList.svelte';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import { fy, moneyTick, millions, emptyNote } from '../lib/chart';

  type Row = { fiscal_year: number; value: number; exception_ids?: string[] };
  let { id, rows: allRows, title, filename, note = '', up, down, ink, valueLabel = 'Amount ($)', exceptions = {} }: {
    id: string; rows: Row[]; title: string; filename: string; note?: string; up: string; down: string; ink: string; valueLabel?: string; exceptions?: Record<string, Exception>;
  } = $props();

  const rows = $derived(allRows.filter((r) => inRange(url, r.fiscal_year)));
  const exceptionIds = $derived([...new Set(rows.flatMap((r) => r.exception_ids ?? []))]);
  const columns = $derived([{ key: 'fiscal_year', label: 'Fiscal year' }, { key: 'value', label: valueLabel }]);
  const fmt = (v: number) => (v < 0 ? '-' : '+') + millions(Math.abs(v), 1);

  function build(width: number) {
    if (!rows.length) return emptyNote();
    const narrow = width < 520;
    // on a phone the labels would collide: label only the first, last, largest and smallest bar (the table has the rest)
    const keepLabel = (r: Row) => !narrow || r === rows[0] || r === rows.at(-1) || r.value === Math.max(...rows.map((x) => x.value)) || r.value === Math.min(...rows.map((x) => x.value));
    const hi = Math.max(...rows.map((r) => r.value), 0), lo = Math.min(...rows.map((r) => r.value), 0), pad = (hi - lo) * 0.12;
    return Plot.plot({
      width, height: 260, marginLeft: 56, marginRight: 8, marginBottom: 40, style: { fontSize: '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0 },
      y: { label: null, grid: true, tickFormat: moneyTick, domain: [lo - pad, hi + pad] },
      marks: [
        Plot.barY(rows, { x: 'fiscal_year', y: 'value', fill: (r: Row) => (r.value >= 0 ? up : down), insetLeft: 1, insetRight: 1,
          title: (r: Row) => `${fy(r.fiscal_year)}\n${r.value.toLocaleString('en-US')}` }),
        ...[[(r: Row) => r.value >= 0, -8], [(r: Row) => r.value < 0, 8]].map(([keep, dy]: any) =>
          Plot.text(rows.filter(keep).filter(keepLabel), { x: 'fiscal_year', y: 'value', text: (r: Row) => fmt(r.value), dy, textAnchor: 'middle', fill: ink, fontSize: narrow ? 8 : 10 })),
        Plot.ruleY([0]),
      ],
    });
  }
</script>

<ChartFrame {id} {title} {note} {rows} {columns} {filename} {exceptionIds} {exceptions}>
  <PlotHost {build} label={`${title}: bar chart by fiscal year; the Table view has every number`} />
</ChartFrame>
