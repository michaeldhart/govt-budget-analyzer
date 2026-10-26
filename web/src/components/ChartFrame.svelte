<script lang="ts">
  // Wraps any chart with what every chart on the site offers: a title, the same data as a table, and a CSV download.
  import type { Snippet } from 'svelte';
  import { toCsv, type Column } from '../lib/csv';

  let { title, note = '', rows, columns, filename, children }: {
    title: string; note?: string; rows: Record<string, unknown>[]; columns: Column[]; filename: string; children: Snippet;
  } = $props();

  let view: 'chart' | 'table' = $state('chart');

  function download() {
    const url = URL.createObjectURL(new Blob([toCsv(rows, columns)], { type: 'text/csv' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    a.click();
    URL.revokeObjectURL(url);
  }
  const show = (v: unknown) => (typeof v === 'number' ? v.toLocaleString('en-US') : Array.isArray(v) ? v.join(', ') : (v ?? ''));
</script>

<figure>
  <figcaption>
    <strong>{title}</strong>
    <span class="controls">
      <button type="button" aria-pressed={view === 'chart'} onclick={() => (view = 'chart')}>Chart</button>
      <button type="button" aria-pressed={view === 'table'} onclick={() => (view = 'table')}>Table</button>
      <button type="button" onclick={download}>Download CSV</button>
    </span>
  </figcaption>
  {#if view === 'chart'}
    {@render children()}
  {:else}
    <div class="scroll">
      <table>
        <thead><tr>{#each columns as c}<th class={typeof rows[0]?.[c.key] === 'number' ? 'num' : ''}>{c.label}</th>{/each}</tr></thead>
        <tbody>{#each rows as r}<tr>{#each columns as c}<td class={typeof r[c.key] === 'number' ? 'num' : ''}>{show(r[c.key])}</td>{/each}</tr>{/each}</tbody>
      </table>
    </div>
  {/if}
  {#if note}<p class="note">{note}</p>{/if}
</figure>

<style>
  figure { margin: 24px 0; }
  figcaption { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
  .controls { display: flex; gap: 6px; }
  button { font: inherit; font-size: 0.8125rem; padding: 3px 10px; border: 1px solid var(--rule); background: var(--surface); color: var(--ink2); border-radius: 4px; cursor: pointer; }
  button[aria-pressed='true'] { background: var(--panel); color: var(--ink); border-color: var(--muted); }
  .scroll { overflow-x: auto; }
  .note { color: var(--ink2); font-size: 0.8125rem; margin: 6px 0 0; }
</style>
