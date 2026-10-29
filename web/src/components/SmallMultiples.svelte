<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import type { Exception } from './ExceptionList.svelte';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, moneyTick, millions } from '../lib/chart';

  type Row = { fiscal_year: number; group: string; budget: number | null; actual: number | null; exception_ids?: string[] };
  let { rows: allRows, groups, ink, ink2, surface, title, note = '', exceptions = {} }: {
    rows: Row[]; groups: { name: string; color: string }[]; ink2: string; ink: string; surface: string; title: string; note?: string; exceptions?: Record<string, Exception>;
  } = $props();

  const rows = $derived(allRows.filter((r) => inRange(url, r.fiscal_year)));
  const exceptionIds = $derived([...new Set(rows.filter((r) => groups.some((g) => g.name === r.group)).flatMap((r) => r.exception_ids ?? []))]);
  const span = $derived([Math.min(...rows.map((r) => r.fiscal_year)), Math.max(...rows.map((r) => r.fiscal_year))]);
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
      width, height: 120, marginLeft: 44, marginRight: 16, marginTop: 6, marginBottom: 20, style: { fontSize: '10px', background: 'transparent' },
      x: { label: null, domain: span, ticks: [...new Set([span[0], Math.round((span[0] + span[1]) / 2), span[1]])], tickFormat: fy },
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

<ChartFrame id="multiples" {title} {note} rows={table.rows} columns={table.columns} filename="taxes-budget-vs-actual-by-group.csv" {exceptionIds} {exceptions}>
  {#if !panels.length}<p class="muted">Nothing to show with these filters.</p>{/if}
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
