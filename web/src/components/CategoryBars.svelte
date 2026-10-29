<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import type { Exception } from './ExceptionList.svelte';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { dollars, percent } from '../lib/format';

  type Row = { category: string; gross_tax: number; offsets: number; net: number; color: string; exception_ids?: string[] };
  let { rows, year, title, note = '', exceptions = {} }: { rows: Row[]; year: number; title: string; note?: string; exceptions?: Record<string, Exception> } = $props();
  const exceptionIds = $derived([...new Set(rows.flatMap((r) => r.exception_ids ?? []))]);

  const sorted = $derived([...rows].sort((a, b) => b.gross_tax - a.gross_tax));
  const total = $derived(rows.reduce((s, r) => s + r.gross_tax, 0));
  const data = $derived(sorted.map((r) => ({ ...r, share: percent(r.gross_tax, total) })));
  const columns = [
    { key: 'category', label: 'Category' }, { key: 'gross_tax', label: 'Gross tax ($)' }, { key: 'offsets', label: 'Offsets ($)' },
    { key: 'net', label: 'Net ($)' }, { key: 'share', label: 'Share of gross' },
  ];

  function build(width: number) {
    const narrow = width < 520; // phone: label with the share only and leave more room for the bars
    return Plot.plot({
      width, marginLeft: narrow ? 105 : 150, marginRight: narrow ? 48 : 150, height: 40 + sorted.length * 34,
      style: { fontSize: '13px', background: 'transparent' },
      x: { label: null, axis: null, domain: [0, Math.max(...sorted.map((r) => r.gross_tax))] },
      y: { label: null, domain: sorted.map((r) => r.category), tickSize: 0 },
      marks: [
        Plot.barX(sorted, { y: 'category', x: 'gross_tax', fill: 'color', insetTop: 3, insetBottom: 3,
          title: (r: Row) => `${r.category}\nGross ${dollars(r.gross_tax)} (${percent(r.gross_tax, total)})\nOffsets ${dollars(r.offsets)}\nNet ${dollars(r.net)}` }),
        Plot.text(sorted, { y: 'category', x: 'gross_tax', text: (r: Row) => (narrow ? percent(r.gross_tax, total) : `${dollars(r.gross_tax)}  ${percent(r.gross_tax, total)}`),
          textAnchor: 'start', dx: 6, fill: 'currentColor' }),
      ],
    });
  }
</script>

<ChartFrame id="by-category" {title} {note} rows={data} {columns} filename={`taxes-fy${year}-by-category.csv`} {exceptionIds} {exceptions}>
  <PlotHost {build} label={`${title}: bar chart of gross tax by category; the Table view has the same numbers`} />
</ChartFrame>
