<script lang="ts">
  // Filters for the charts on the page. They live in the URL (?from=&to=&hide=), so a link reproduces the view.
  import { onMount } from 'svelte';
  import { url, initUrlState, setRange, setHidden, resetFilters } from '../lib/urlState.svelte';

  let { years, categories }: { years: number[]; categories: { name: string; color: string }[] } = $props();
  onMount(initUrlState);

  const changed = $derived(url.from != null || url.to != null || url.hide.length > 0);
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
  <fieldset>
    <legend>Categories (composition and offsets charts)</legend>
    {#each categories as c}
      <label class="cat">
        <input type="checkbox" checked={!url.hide.includes(c.name)} onchange={(e) => setHidden(c.name, !e.currentTarget.checked)} />
        <span class="swatch" style={`background:${c.color}`}></span>{c.name}
      </label>
    {/each}
  </fieldset>
  <button type="button" onclick={resetFilters} disabled={!changed}>Reset</button>
</form>

<style>
  .filters { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: flex-end; background: var(--panel); padding: 12px 14px; border-radius: 6px; margin: 12px 0 4px; font-size: 0.875rem; }
  label { display: flex; flex-direction: column; gap: 2px; color: var(--ink2); }
  select, button { font: inherit; padding: 3px 8px; border: 1px solid var(--rule); border-radius: 4px; background: var(--surface); color: var(--ink); }
  button { cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: default; }
  fieldset { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; flex: 1 1 16rem; }
  legend { color: var(--ink2); padding: 0; margin-bottom: 4px; }
  label.cat { flex-direction: row; align-items: center; gap: 5px; color: var(--ink); }
  .swatch { display: inline-block; width: 0.75em; height: 0.75em; border-radius: 2px; }
</style>
