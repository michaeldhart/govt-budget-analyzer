<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy } from '../lib/chart';

  type Row = { fiscal_year: number; group: string; variance_pct: number | null; budget: number | null; actual: number | null };
  let { rows, groups, p, title, note = '' }: { rows: Row[]; groups: string[]; p: Record<string, string>; title: string; note?: string } = $props();

  const years = $derived([...new Set(rows.filter((r) => r.actual != null).map((r) => r.fiscal_year))].sort());
  const cells = $derived(groups.flatMap((g) => years.map((y) => {
    const r = rows.find((x) => x.group === g && x.fiscal_year === y);
    return { group: g, fiscal_year: y, v: r?.variance_pct ?? null };
  })));
  const table = $derived({
    rows: cells.filter((c) => c.v != null).map((c) => ({ group: c.group, fiscal_year: c.fiscal_year, variance_pct: c.v })),
    columns: [{ key: 'group', label: 'Group' }, { key: 'fiscal_year', label: 'Fiscal year' }, { key: 'variance_pct', label: 'Actual vs budget (fraction)' }],
  });

  // too many cells for a phone: keep the full-size grid and let the wrapper scroll sideways
  function build(available: number) {
    const width = Math.max(available, 640);
    const narrow = false;
    const label = (v: number) => (Math.round(v * 100) ? `${v > 0 ? '+' : ''}${Math.round(v * 100)}` : '0');
    return Plot.plot({
      width, marginLeft: narrow ? 108 : 150, marginTop: 28, marginRight: 4, height: 52 + groups.length * 34,
      style: { fontSize: narrow ? '10px' : '12px', background: 'transparent' },
      x: { label: null, tickFormat: fy, tickRotate: narrow ? -90 : 0, axis: 'top', type: 'band' },
      y: { label: null, domain: groups, tickSize: 0 },
      color: { type: 'linear', domain: [-0.6, 0, 0.6], range: [p.down, p.neutral, p.up], clamp: true },
      marks: [
        Plot.cell(cells.filter((c) => c.v != null), { x: 'fiscal_year', y: 'group', fill: 'v', inset: 1,
          title: (c: any) => `${c.group} ${fy(c.fiscal_year)}: ${(c.v * 100).toFixed(1)}% vs budget` }),
        Plot.text(cells.filter((c) => c.v != null), { x: 'fiscal_year', y: 'group', text: (c: any) => label(c.v), fontSize: narrow ? 8 : 10,
          fill: (c: any) => (Math.abs(c.v) > 0.35 ? '#ffffff' : p.ink) }),
        Plot.text(cells.filter((c) => c.v == null), { x: 'fiscal_year', y: 'group', text: () => '–', fill: p.muted }),
      ],
    });
  }
</script>

<ChartFrame {title} {note} rows={table.rows} columns={table.columns} filename="taxes-variance-heatmap.csv">
  <div class="scroll"><PlotHost {build} label={`${title}: grid of groups by fiscal year; the Table view has every number`} /></div>
</ChartFrame>

<style>
  .scroll { overflow-x: auto; }
  .scroll :global(svg) { max-width: none; } /* Plot defaults to max-width: 100%, which would shrink the grid on a phone */
</style>
