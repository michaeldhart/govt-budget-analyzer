// Run after `astro build`: the built pages must show the numbers the pipeline's tests pin.
import { readFileSync } from 'node:fs';

const page = readFileSync('dist/belvidere_il/taxes/2027/index.html', 'utf8');
const need = ['$20,998,468', '-$532,141', '$20,466,327', 'FY2027 gross taxes by category', 'Download CSV', 'EX-telecom-class'];
const missing = need.filter((s) => !page.includes(s));
if (missing.length) {
  console.error(`dist/belvidere_il/taxes/2027/index.html is missing: ${missing.join(', ')}`);
  process.exit(1);
}
console.log('build check ok');
