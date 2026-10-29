<script lang="ts">
  import * as Plot from '@observablehq/plot';
  import { url } from '../lib/urlState.svelte';
  import { inRange } from '../lib/urlParams';
  import type { Exception } from './ExceptionList.svelte';
  import ChartFrame from './ChartFrame.svelte';
  import PlotHost from './PlotHost.svelte';
  import { fy, moneyTick, moneyShort } from '../lib/chart';
  import { scaleValue, isCapita, unitPhrase, unitNote, type Ref } from '../lib/units';

  type Row = { fiscal_year: number; group: string; budget: number | null; actual: number | null; exception_ids?: string[] };
  // `actualAsLine` draws the actual values as the line (for a single series such as a balance, with no budget); id and filename name the chart in the URL and the CSV
  let { rows: allRows, groups, ink, ink2, surface, title, note = '', exceptions = {}, id = 'multiples', filename = 'taxes-budget-vs-actual-by-group.csv', actualAsLine = false, subLabel = 'budget', units }: {
    rows: Row[]; groups: { name: string; color: string }[]; ink2: string; ink: string; surface: string; title: string; note?: string; exceptions?: Record<string, Exception>;
    id?: string; filename?: string; actualAsLine?: boolean; subLabel?: string; units?: Ref;
  } = $props();

  const rows = $derived(allRows.filter((r) => inRange(url, r.fiscal_year)).map((r) => ({ ...r, budget: r.budget == null ? null : scaleValue(r.budget, r.fiscal_year, url.unit, units), actual: r.actual == null ? null : scaleValue(r.actual, r.fiscal_year, url.unit, units) })));
  const money = (v: number) => (isCapita(url.unit) ? `$${Math.round(v).toLocaleString('en-US')}` : moneyShort(v));
  const exceptionIds = $derived([...new Set(rows.filter((r) => groups.some((g) => g.name === r.group)).flatMap((r) => r.exception_ids ?? []))]);
  const span = $derived([Math.min(...rows.map((r) => r.fiscal_year)), Math.max(...rows.map((r) => r.fiscal_year))]);
  const panels = $derived(groups.map((g) => {
    const mine = rows.filter((r) => r.group === g.name);
    const bud = mine.filter((r) => r.budget != null);
    const act = mine.filter((r) => r.actual != null);
    return { ...g, bud, act, last: actualAsLine ? (act.at(-1) ? { fiscal_year: act.at(-1)!.fiscal_year, budget: act.at(-1)!.actual } : undefined) : bud.at(-1) };
  }).filter((p) => (actualAsLine ? p.act.length : p.bud.length)));
  const table = $derived({
    rows: rows.filter((r) => groups.some((g) => g.name === r.group)),
    columns: [{ key: 'group', label: 'Group' }, { key: 'fiscal_year', label: 'Fiscal year' }, { key: 'budget', label: 'Budget ($)' },
              { key: 'actual', label: 'Audited actual ($)' }],
  });

  const build = (p: (typeof panels)[number]) => (width: number) => {
    const vals = [...p.bud.map((r) => r.budget!), ...p.act.map((r) => r.actual!)];
    const top = Math.max(...vals, 0) * 1.12 || 1, bottom = Math.min(...vals, 0) * 1.12;
    return Plot.plot({
      width, height: 120, marginLeft: 44, marginRight: 16, marginTop: 6, marginBottom: 20, style: { fontSize: '10px', background: 'transparent' },
      x: { label: null, domain: span, ticks: [...new Set([span[0], Math.round((span[0] + span[1]) / 2), span[1]])], tickFormat: fy },
      y: { label: null, domain: [bottom, top], ticks: 3, tickFormat: moneyTick, grid: true },
      marks: [
        actualAsLine ? Plot.lineY(p.act, { x: 'fiscal_year', y: 'actual', stroke: p.color, strokeWidth: 2 }) : Plot.lineY(p.bud, { x: 'fiscal_year', y: 'budget', stroke: p.color, strokeWidth: 2 }),
        Plot.dot(p.act, { x: 'fiscal_year', y: 'actual', fill: surface, stroke: actualAsLine ? p.color : ink2, r: 3,
          title: (r: Row) => `${fy(r.fiscal_year)} audited actual ${r.actual!.toLocaleString('en-US')}` }),
        Plot.ruleY([0]),
      ],
    });
  };
</script>

<ChartFrame {id} title={unitPhrase(url.unit, units) ? `${title} (${unitPhrase(url.unit, units)})` : title} note={[note, unitNote(url.unit, units, [...new Set(rows.map((r) => r.fiscal_year))])].filter(Boolean).join(' ')} rows={table.rows} columns={table.columns} {filename} {exceptionIds} {exceptions}>
  {#if !panels.length}<p class="muted">Nothing to show with these filters.</p>{/if}
  <div class="grid">
    {#each panels as p (p.name)}
      <div class="panel">
        <h4>{p.name.replace(' (net)', '')}</h4>
        <div class="sub">FY{String(p.last!.fiscal_year).slice(-2)} {subLabel} {money(p.last!.budget!)}</div>
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
