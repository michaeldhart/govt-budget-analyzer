<script module lang="ts">
  export type Exception = {
    exception_id: string; kind: string; scope: string; scope_id: string; fy_from: number | null; fy_to: number | null; effect: string;
    summary: string; detail: string; decided_on: string; evidence: string;
    sources: { doc_id: string; fiscal_year: number; pdf_page: number; source_url: string }[];
  };
</script>

<script lang="ts">
  // The exceptions registry entries behind a chart or page: where the source or the data model does not behave as the numbers suggest.
  let { items, compact = false }: { items: Exception[]; compact?: boolean } = $props();

  const KIND: Record<string, string> = {
    definitional: 'definition', classification: 'classification', source_typo: 'source error', stale_column: 'stale column',
    omitted_from_total: 'left out of total', structural_change: 'structural change', not_comparable: 'not comparable', ocr_correction: 'scan correction',
  };
  const EFFECT: Record<string, string> = {
    excluded: 'left out of the numbers', included: 'counted in the numbers', adjusted: 'numbers adjusted', informational: 'for information',
  };
  const order = (e: Exception) => ['adjusted', 'excluded', 'included', 'informational'].indexOf(e.effect);
  const sorted = $derived([...items].sort((a, b) => order(a) - order(b) || a.exception_id.localeCompare(b.exception_id)));
  const years = (e: Exception) => (e.fy_from && e.fy_to ? (e.fy_from === e.fy_to ? `FY${e.fy_from}` : `FY${e.fy_from}–FY${e.fy_to}`) : e.fy_from ? `from FY${e.fy_from}` : e.fy_to ? `to FY${e.fy_to}` : '');
  const shown = (e: Exception) => (compact ? e.sources.slice(0, 6) : e.sources.slice(0, 14));
</script>

<ul class="exlist">
  {#each sorted as e (e.exception_id)}
    <li id={e.exception_id}>
      <div class="head">
        <strong>{e.summary}</strong>
        <span class="chips"><span class="chip">{KIND[e.kind] ?? e.kind}</span><span class="chip">{EFFECT[e.effect] ?? e.effect}</span>{#if years(e)}<span class="chip">{years(e)}</span>{/if}</span>
      </div>
      {#if e.detail && e.detail !== e.summary}<p>{e.detail}</p>{/if}
      <p class="meta">
        {e.exception_id}{#if e.decided_on}{' · decided ' + e.decided_on}{/if}{#if e.evidence}{' · evidence: ' + e.evidence}{/if}
      </p>
      {#if e.sources.length}
        <p class="meta">Seen in the source:
          {#each shown(e) as s, i}<a href={`${s.source_url}#page=${s.pdf_page}`}>{`FY${s.fiscal_year} ${s.doc_id.endsWith('audit') ? 'audit' : 'budget'} p.${s.pdf_page}`}</a>{i < shown(e).length - 1 ? ', ' : ''}{/each}{#if e.sources.length > shown(e).length}, and {e.sources.length - shown(e).length} more{/if}
        </p>
      {/if}
    </li>
  {/each}
</ul>

<style>
  .exlist { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
  li { border-left: 3px solid var(--rule); padding-left: 10px; font-size: 0.875rem; scroll-margin-top: 12px; }
  .head { display: flex; flex-wrap: wrap; gap: 4px 10px; justify-content: space-between; align-items: baseline; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chip { font-size: 0.6875rem; color: var(--ink2); background: var(--panel); border-radius: 3px; padding: 1px 6px; white-space: nowrap; }
  p { margin: 4px 0 0; }
  .meta { color: var(--ink2); font-size: 0.75rem; }
</style>
