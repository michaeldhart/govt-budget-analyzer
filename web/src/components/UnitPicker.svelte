<script lang="ts">
  // Dollars as reported, adjusted for inflation, per resident, or both; saved in the URL (?unit=).
  import { url, setUnit } from '../lib/urlState.svelte';
  import type { Unit } from '../lib/urlParams';
  let { base }: { base: number } = $props();
</script>

<label class="unit">Dollars
  <select value={url.unit ?? ''} onchange={(e) => setUnit((e.currentTarget.value || null) as Unit | null)}>
    <option value="">As reported</option>
    <option value="real">{`Adjusted for inflation (FY${base} dollars)`}</option>
    <option value="capita">Per resident</option>
    <option value="real_capita">{`Per resident, inflation-adjusted`}</option>
  </select>
</label>

<style>
  .unit { display: flex; flex-direction: column; gap: 2px; color: var(--ink2); }
  select { font: inherit; padding: 3px 8px; border: 1px solid var(--rule); border-radius: 4px; background: var(--surface); color: var(--ink); max-width: 100%; }
</style>
