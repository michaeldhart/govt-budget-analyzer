<script lang="ts">
  // Expenses by object category for the department chosen in the filter bar (or all departments).
  import StackedBars from './StackedBars.svelte';
  import { url } from '../lib/urlState.svelte';

  type Row = { fiscal_year: number; dept_code: string; department: string; object: string; amount: number; exception_ids?: string[] };
  let { rows, series, ink, exceptions = {}, units }: { rows: Row[]; series: { name: string; color: string }[]; ink: string; exceptions?: Record<string, any>; units?: any } = $props();

  const picked = $derived(rows.filter((r) => !url.dept || r.dept_code === url.dept));
  const name = $derived(url.dept ? (rows.find((r) => r.dept_code === url.dept)?.department ?? url.dept) : 'all departments');
  const data = $derived(picked.map((r) => ({ fiscal_year: r.fiscal_year, series: r.object, value: r.amount, exception_ids: r.exception_ids })));
  const used = $derived(series.filter((s) => data.some((d) => d.series === s.name)));
</script>

<StackedBars id="by-object" {exceptions} {units} rows={data} series={used} {ink} title={`Adopted expenses by object: ${name}`} filename="expenses-by-object.csv"
  totalLabel="All objects" note="Object categories group accounts by object code (personnel 5xxx, contractual 6xxx, supplies 70xx-78xx, and so on). Pick a department above." />
