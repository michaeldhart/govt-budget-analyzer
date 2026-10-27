<script lang="ts">
  // Year range and department for the expense charts; both live in the URL (?from=&to=&dept=).
  import { onMount } from 'svelte';
  import { url, initUrlState, setRange, setDept, resetFilters } from '../lib/urlState.svelte';

  let { years, departments = [], showDept = true }: { years: number[]; departments?: { code: string; name: string; function: string }[]; showDept?: boolean } = $props();
  onMount(initUrlState);

  const functions = $derived([...new Set(departments.map((d) => d.function))]);
  const changed = $derived(url.from != null || url.to != null || url.dept != null);
  const num = (v: string) => (v === '' ? null : Number(v));
</script>

<form class="filters" onsubmit={(e) => e.preventDefault()} aria-label="Chart filters">
  <label>From
    <select value={url.from ?? ''} onchange={(e) => setRange(num(e.currentTarget.value), url.to)}>
      <option value="">{`FY${years[0]}`}</option>
      {#each years as y}<option value={y}>{`FY${y}`}</option>{/each}
    </select>
  </label>
  <label>To
    <select value={url.to ?? ''} onchange={(e) => setRange(url.from, num(e.currentTarget.value))}>
      <option value="">{`FY${years.at(-1)}`}</option>
      {#each years as y}<option value={y}>{`FY${y}`}</option>{/each}
    </select>
  </label>
  {#if showDept}<label class="dept">Department (object charts)
    <select value={url.dept ?? ''} onchange={(e) => setDept(e.currentTarget.value)}>
      <option value="">All departments</option>
      {#each functions as f}
        <optgroup label={f}>
          {#each departments.filter((d) => d.function === f) as d}<option value={d.code}>{d.name}</option>{/each}
        </optgroup>
      {/each}
    </select>
  </label>{/if}
  <button type="button" onclick={resetFilters} disabled={!changed}>Reset</button>
</form>

<style>
  .filters { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: flex-end; background: var(--panel); padding: 12px 14px; border-radius: 6px; margin: 12px 0 4px; font-size: 0.875rem; }
  label { display: flex; flex-direction: column; gap: 2px; color: var(--ink2); }
  select, button { font: inherit; padding: 3px 8px; border: 1px solid var(--rule); border-radius: 4px; background: var(--surface); color: var(--ink); }
  button { cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: default; }
  .dept select { min-width: 14rem; max-width: 100%; }
</style>
