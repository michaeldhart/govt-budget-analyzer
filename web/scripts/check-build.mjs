// Run after `astro build`: the built pages must show the numbers the pipeline's tests pin.
import { readFileSync } from 'node:fs';

const checks = {
  'dist/belvidere_il/taxes/2027/index.html': ['$20,998,468', '-$532,141', '$20,466,327', 'FY2027 gross taxes by category', 'Download CSV', 'EX-telecom-class'],
  'dist/belvidere_il/taxes/index.html': ['FY2027 adopted gross taxes', '$21.00M', 'above the budget in 14 of 15 audited years', 'Adopted gross taxes by category',
    'Taxes: budget vs audited actual', 'Audited actual vs budget, percent by group and year', 'Adopted offsets by category', 'aria-label="Chart filters"', 'id="composition"', 'id="heatmap"'],
  'dist/belvidere_il/expenses/index.html': ['$36.2M', 'General Fund expenses over time', 'id="by-function"', 'id="by-object"', 'aria-label="Chart filters"',
    'General Administration', '$20,140,228', 'EX-fund-expense-5050'],
  'dist/belvidere_il/expenses/audit/index.html': ['14 of 15', 'id="budget-vs-actual"', 'id="heatmap"', 'id="multiples"', 'EX-audit-transfers-excluded', 'How the audit and the budget line up',
    '179 of 182'],
  'dist/belvidere_il/fund-balance/index.html': ['aria-label="Belvidere pages"', 'aria-current="page"','$42.6M', '$39.6M', '21.0', 'id="balance"', 'id="months"', 'id="flows"', 'id="net-change"', 'EX-fund-stmt-fy2016-restatement',
    'GFOA'],
};
let bad = false;
for (const [file, need] of Object.entries(checks)) {
  const page = readFileSync(file, 'utf8');
  const missing = need.filter((s) => !page.includes(s));
  if (missing.length) { console.error(`${file} is missing: ${missing.join(', ')}`); bad = true; }
}
if (bad) process.exit(1);
console.log('build check ok');
