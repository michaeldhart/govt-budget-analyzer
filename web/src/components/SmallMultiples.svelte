<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, moneyTick, millions } from '../lib/chart';

  type Row = { fiscal_year: number; group: string; budget: number | null; actual: number | null };
  let { rows, groups, ink, ink2, surface, title, note = '' }: {
    rows: Row[]; groups: { name: string; color: string }[]; ink2: string; ink: string; surface: string; title: string; note?: string;
  } = $props();

  const panels = $derived(groups.map((g) => {
    const mine = rows.filter((r) => r.group === g.name);
    const bud = mine.filter((r) => r.budget != null);
    return { ...g, bud, act: mine.filter((r) => r.actual != null), last: bud.at(-1) };
  }).filter((p) => p.bud.length));
  const table = $derived({
    rows: rows.filter((r) => groups.some((g) => g.name === r.group)),
    columns: [{ key: 'group', label: 'Group' }, { key: 'fiscal_year', label: 'Fiscal year' }, { key: 'budget', label: 'Budget ($)' },
              { key: 'actual', label: 'Audited actual ($)' }],
  });

  const build = (p: (typeof panels)[number]) => (width: number) => {
    const top = Math.max(...p.bud.map((r) => r.budget!), ...p.act.map((r) => r.actual!)) * 1.12;
    return Plot.plot({
      width, height: 120, marginLeft: 44, marginRight: 6, marginTop: 6, marginBottom: 20, style: { fontSize: '10px', background: 'transparent' },
      x: { label: null, domain: [2012, 2027], ticks: [2012, 2019, 2026], tickFormat: fy },
      y: { label: null, domain: [0, top], ticks: 3, tickFormat: moneyTick, grid: true },
      marks: [
        Plot.lineY(p.bud, { x: 'fiscal_year', y: 'budget', stroke: p.color, strokeWidth: 2 }),
        Plot.dot(p.act, { x: 'fiscal_year', y: 'actual', fill: surface, stroke: ink2, r: 3,
          title: (r: Row) => `${fy(r.fiscal_year)} audited actual ${r.actual!.toLocaleString('en-US')}` }),
        Plot.ruleY([0]),
      ],
    });
  };
</script>

<ChartFrame {title} {note} rows={table.rows} columns={table.columns} filename="taxes-budget-vs-actual-by-group.csv">
  <div class="grid">
    {#each panels as p (p.name)}
      <div class="panel">
        <h4>{p.name.replace(' (net)', '')}</h4>
        <div class="sub">FY{String(p.last!.fiscal_year).slice(-2)} budget {millions(p.last!.budget!, 2)}</div>
        <PlotHost build={build(p)} label={`${p.name}: budget line and audited actual dots by fiscal year`} minWidth={120} />
      </div>
    {/each}
  </div>
</ChartFrame>

<style>
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr)); gap: 14px 12px; }
  h4 { margin: 0; font-size: 0.875rem; }
  .sub { color: var(--ink2); font-size: 0.75rem; margin-bottom: 2px; }
</style>
