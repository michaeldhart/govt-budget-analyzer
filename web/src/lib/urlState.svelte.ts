// One reactive copy of the query-string state, shared by every island on the page (they are separate Svelte roots,
// but this module is bundled once). Read the URL after hydration (initUrlState from onMount) so server and client markup agree.
import { emptyState, parse, serialize, type UrlState } from './urlParams';

export const url: UrlState & { ready: boolean } = $state({ ...emptyState(), ready: false });

export function initUrlState() {
  if (url.ready || typeof window === 'undefined') return;
  Object.assign(url, parse(location.search));
  url.ready = true;
}

function persist() {
  const qs = serialize(url);
  history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
}

const toggle = (list: string[], v: string, on: boolean) => (on ? [...new Set([...list, v])] : list.filter((x) => x !== v));

export function setTable(id: string, on: boolean) { url.table = toggle(url.table, id, on); persist(); }
export function setHidden(name: string, hidden: boolean) { url.hide = toggle(url.hide, name, hidden); persist(); }
export function setRange(from: number | null, to: number | null) { url.from = from; url.to = to; persist(); }
export function setDept(code: string | null) { url.dept = code || null; persist(); }
export function resetFilters() { url.from = null; url.to = null; url.hide = []; url.dept = null; persist(); }
