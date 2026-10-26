// Reads the files written by `python3 -m govbudget.export_site` (web/public/data) at build time.
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

// astro build/dev run with the web/ directory as cwd (module URLs point into the bundle, so they cannot locate public/)
const DIR = join(process.cwd(), 'public', 'data') + '/';

export function readData<T = any>(...parts: string[]): T {
  const path = DIR + parts.join('/');
  if (!existsSync(path)) {
    throw new Error(`${path} not found: run \`python3 -m govbudget.export_site\` from the repository root first`);
  }
  return JSON.parse(readFileSync(path, 'utf8'));
}

/** Site-relative URL that respects the configured base path (and works unchanged on a custom domain). */
export function href(path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  return `${base}/${path.replace(/^\//, '')}`;
}
