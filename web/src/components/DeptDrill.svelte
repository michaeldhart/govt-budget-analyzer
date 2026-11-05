<script lang="ts">
  // Spending by object, and revenue against spending, for the department chosen in the filter bar (?dept=<fund|name>) or all departments.
  import StackedBars from './StackedBars.svelte';
  import LineSeries from './LineSeries.svelte';
  import ValueBars from './ValueBars.svelte';
  import { url } from '../lib/urlState.svelte';

  type Row = { fiscal_year: number; dept_id: string; department: string; fund: string; category: string; amount: number; exception_ids?: string[] };
  let { spending, revenue, objects, ink, up, down, exceptions = {}, units, revenueYears, noun = 'district', prefix = 'park' }: {
    spending: Row[]; revenue: Row[]; objects: { name: string; color: string }[]; ink: string; up: string; down: string; exceptions?: Record<string, any>; units?: any; revenueYears: number[]; noun?: string; prefix?: string;
  } = $props();

  const pick = (rows: Row[]) => rows.filter((r) => !url.dept || r.dept_id === url.dept);
  const sp = $derived(pick(spending)), rv = $derived(pick(revenue));
  const first = $derived(sp.find(() => true) ?? spending[0]);
  const name = $derived(url.dept ? (first ? `${first.department} (${first.fund})` : url.dept) : 'all departments');
  const obj = $derived(sp.map((r) => ({ fiscal_year: r.fiscal_year, series: r.category, value: r.amount, exception_ids: r.exception_ids })));
  const used = $derived(objects.filter((s) => obj.some((d) => d.series === s.name)));
  const total = (rows: Row[], fy: number) => rows.filter((r) => r.fiscal_year === fy).reduce((s, r) => s + r.amount, 0);
  const ids = (rows: Row[], fy: number) => [...new Set(rows.filter((r) => r.fiscal_year === fy).flatMap((r) => r.exception_ids ?? []))];
  const years = $derived(revenueYears);
  const lines = $derived(years.flatMap((y) => [
    { fiscal_year: y, series: 'Revenue', value: total(rv, y), exception_ids: ids(rv, y) },
    { fiscal_year: y, series: 'Spending', value: total(sp, y), exception_ids: ids(sp, y) },
  ]));
  const net = $derived(years.map((y) => ({ fiscal_year: y, value: total(rv, y) - total(sp, y), exception_ids: [...ids(rv, y), ...ids(sp, y)] })));
</script>

<StackedBars id="dept-object" {exceptions} {units} rows={obj} series={used} {ink} title={`Budgeted spending by object: ${name}`} filename={`${prefix}-department-spending-by-object.csv`}
  totalLabel="All objects" note="Object categories group accounts by object code. Pick a department in the filter bar above." />

<h2>Revenue against spending: {name}</h2>
<LineSeries id="dept-rev-vs-spend" {exceptions} {units} kind="money" rows={lines} series={[{ name: 'Revenue', color: up }, { name: 'Spending', color: down }]}
  title={`Budgeted revenue and spending: ${name}`} filename={`${prefix}-department-revenue-vs-spending.csv`}
  note={`Revenue is what the ${noun}'s budget prints under this department's own code; taxes and transfers are printed under the administration departments (the County's Non-Departmental) or the fund itself.`} />
<ValueBars id="dept-net" {exceptions} {units} rows={net} {up} {down} {ink} valueLabel="Revenue minus spending ($)"
  title={`Revenue minus spending: ${name}`} filename={`${prefix}-department-revenue-minus-spending.csv`}
  note="Positive: the department's printed revenue exceeds its budgeted spending; negative: spending the department's own revenue does not cover." />
