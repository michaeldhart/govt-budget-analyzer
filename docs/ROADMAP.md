# Roadmap and remaining work

Status as of 2026-10-26. This file is the single place to see what is done, what is left, and what has to be decided. `CLAUDE.md` holds how the pipeline works; this holds what to do next. When you finish or change something here, update it in the same commit.

## 1. Goal

Turn the Belvidere, IL budget analysis proof of concept into a website or web app that visualizes full government budgets across years: budgeted revenue and expenses, then audited actuals, then the budgets of other local governments in the area. Output has two parts: a Python pipeline (PDF to validated tables) and a site that reads the tables.

## 2. Where things stand

| Area | Status |
|---|---|
| Repo on GitHub (public, Apache-2.0), shared data model, per-government pipelines, exceptions registry | Done |
| Belvidere budgets FY2015-27: General Fund revenue and expenses, Water/Sewer (fund 61), other funds (Motor Fuel Tax, TIFs, SSAs, Capital Projects, Escrow, pension funds, Ida Library) | Done, every extract validated to printed totals |
| Belvidere audits FY2012-26: General Fund revenue and expenditure schedules, governmental fund statements, Water/Sewer schedules and statements, other-fund schedules and combining statements, debt, pensions and OPEB | Done, 0 validation problems |
| Audit vs budget reconciliations (General Fund revenue and expenses, Water/Sewer, other funds) | Done; known misses are recorded as exceptions |
| Website | Not started |
| Other governments | Not started |
| FY2027 audited actuals | Blocked: the FY2027 audit is not published yet |

Headline numbers and per-table detail are in `CLAUDE.md`. Rebuild everything derived with `pipelines/belvidere_il/rebuild_all.sh`.

## 3. Decisions needed from the owner

1. **Site stack.** Recommendation: Astro (static) with Svelte islands and Observable Plot, reading static JSON/CSV the pipeline writes; GitHub Pages hosting; monorepo with `pipeline/`, `data/`, `web/`. Runner-up: a Vite + Svelte single-page app (like `itpss-visualizer`). Avoid staying on Jekyll for interactive views and avoid a backend until one is justified (uploads, saved views, an API, or data too big for the browser). Not yet confirmed.
2. **Next government.** Which body to add second (another city, the county, a school district). Pick one whose budgets and audits are published as PDFs the project can download; it is the test of whether the shared model really abstracts.
3. **Scope of the first site release.** Suggested: General Fund revenue (taxes by category over time), expenses by department and object, budget vs audited actual, fund balance. Water/Sewer, other funds and debt/pensions as later pages.

## 4. Work items

Priority: P1 do next, P2 soon, P3 when convenient. Each item says what "done" means.

### Site
- **S1 (P1) Site skeleton.** Slice order: (1) exporter + shared palette + tests, DONE 2026-10-26 (`govbudget/export_site.py` writes `web/public/data/<entity>/*.json` and `downloads/`; `govbudget/palette.json` read by both PDF scripts and the site; `tests/test_export_site.py`); (2) Astro skeleton + Pages workflow, DONE 2026-10-26 (`web/`, `.github/workflows/pages.yml`; base path `/govt-budget-analyzer` until a custom domain exists, then set `SITE_URL`/`BASE_PATH` in the workflow; the repo's Settings > Pages source must be "GitHub Actions"); (3) FY2027 breakdown page, DONE 2026-10-26 (`/[entity]/taxes/[year]/` for every adopted year FY2014-27: summary, category bar chart with Chart/Table toggle and CSV download via `ChartFrame.svelte`, every tax line with page label, the exceptions that touch them; `web/scripts/check-build.mjs` pins the FY2027 totals in the built HTML); (4) multi-year tax pages, DONE 2026-10-26 (`/[entity]/taxes/`: summary tiles and computed findings, stacked composition, offsets, budget vs audited actual line and variance bars, small multiples by audit group, variance heatmap; new dataset `taxes_budget_vs_actual`; each chart has the table view and CSV download); (5) URL state + CSV download on every chart, DONE 2026-10-26 (`?from=&to=&hide=&table=` on `/[entity]/taxes/`, `?table=` on the year pages; each chart has an `id` that is also its `#anchor`; filter bar `TaxFilters.svelte`; `web/src/lib/urlParams.ts` is unit-tested by `npm test`). S1 is complete. Needs decision 1. Add `web/` with the chosen framework, a GitHub Pages workflow, a pipeline step that writes site data (JSON or CSV from the canonical tables) into `web/public/data/`, and a shared palette file read by both the PDF scripts and the site. Done = the existing report's charts (stacked tax composition, small multiples by category, budget vs actual, variance heatmap, the FY2027 breakdown and tables) reproduced from the data with URL-addressable state, a table view and CSV download for every chart.
- **S2 (P2) Exceptions in the UI.** Every chart that uses an affected line shows its exceptions (`entities/belvidere_il/exceptions.csv`, attached to facts by `exception_ids`) and links to the source page (needs P3 below).
- **S3 (P2) Expense and fund-balance pages.** Slices: (1) exporter datasets `expenses_by_dept_object`, `expenses_fund_totals`, `expenses_audit_vs_budget`, `fund_balance`, DONE 2026-10-26 (tested in `tests/test_export_site.py`); (2) `/[entity]/expenses/`: departments and objects over time, DONE 2026-10-26 (function-group and object stacks, department picker via `?dept=`, department table FY2015 vs FY2027, tie-out table; generic `StackedBars.svelte` now also draws the tax charts; colors for `expense_object` and new scheme `function_group` are in `categories.csv`); (3) audit vs budget expenses; (4) fund balance and months of reserves; (5) URL state and polish. Departments and objects over time, audit vs budget, fund balance and months of reserves, using `facts.csv`, `fund_balance_summary.csv`, `audit_expenses_vs_budget.csv`.
- **S4 (P3) Water/Sewer, other funds, debt and pensions pages** from `ws_summary.csv`, `other_funds_summary.csv`, the debt/pension tables.
- **S5 (P3) Compare view across governments** once a second government exists.

### Data quality and tooling
- **T1 (P1, done 2026-10-26) Regression test suite.** DONE: `tests/` (pytest; `python3 -m pytest`): `test_golden_rebuild.py` rebuilds every derived table in a scratch copy and requires each CSV in `data/belvidere_il/` to equal the committed one (marked `slow`), and `test_tieouts.py` pins the headline tie-out counts and validators. `test_structure.py` (schema snapshot of every canonical table and the columns the site reads, key uniqueness, references to documents/accounts/exceptions, rollup and identity checks) and `test_parsers.py` (number parsing and account keys, pinned on the OCR quirks). Extractor-level behaviour is covered only through the committed extracts, not unit-tested. Original spec: A small test run (pytest or a script) that rebuilds derived tables from the extracts and asserts the headline invariants: FY2027 gross/offset/net taxes, every tie-out count recorded in `CLAUDE.md` (for example 179 of 182 General Fund expense group-years, 31 of 36 Water/Sewer, 45 of 45 verified other-fund years, General Fund statements vs schedules 14 of 15), 0 integrity problems from `govbudget.validate`, and no change in the committed derived outputs. This session found several breakages only by hand; this prevents it.
- **T2 (P2) Reference tables.** Population, CPI (and an inflation base year), and equalized assessed valuation, as small tables keyed by fiscal year, with sources recorded. Needed for per-capita and inflation-adjusted views.
- **T3 (P2) Provenance.** Add a PDF page number to budget rows, keep the raw OCR value next to each override-corrected value, and add a source manifest with URLs (document hashes already exist in `documents.csv`).
- **T4 (P3) Curated labels** for expense accounts (current labels are OCR text with typos such as "Dissburse"), kept in `account_map.csv`.
- **T5 (P3) Consolidate duplicated extractor logic** (`extract_budget.py`, `extract_budget_ws.py`, `extract_budget_funds.py` share OCR, number parsing and the auto-resolve idea) into shared helpers without changing outputs; T1 makes this safe.
- **T6 (P3) One command for the extractors.** `rebuild_all.sh` rebuilds derived tables only; add a documented entry point that runs the per-year extractors and the debt/pension build in the right order.

### Remaining data gaps (Belvidere)
- **D1 (P3)** Budget Exhibit B salary schedule (positions and pay).
- **D2 (P3)** Water/Sewer budget cash sub-ledgers (bond reserves, connection fees, depreciation funding, bond payments).
- **D3 (P3)** FY2012 debt service to maturity; FY2012-15 pension funding-progress schedules; police/fire plan net position tables; IMRF asset allocation.
- **D4 (P3)** Prove or drop the mapping of the audit's 1990 and 1993 TIF funds to budget funds #12 and #14; compare Capital Projects budget (two ledgers) with the audit fund by another route than totals.
- **D5 (P3)** Water/Sewer audit: no independent second reading exists for FY2013-15 statements (OCR only); spot-check them against page images if they become important.
- **D6 (blocked)** FY2027 audited actuals when the audit is published (expected after the FY2027 year ends April 30, 2027): run the same extractors, add the year to `entity.json` ranges, rebuild.
- **D7 (P3)** The FY2027 budget revision (780H) is treated as a one-salary change; confirm if it ever matters.

### Other governments
- **G1 (P2) Choose and scope the second government** (decision 2): list its documents, years, fiscal-year start month, chart of accounts style and audit layout; create `entities/<id>/entity.json`.
- **G2 (P2) Build its extraction pipeline** under `pipelines/<id>/` following the Belvidere pattern: per-document extracts, validation to printed totals, overrides with reasons, `build_facts.py` equivalent writing the canonical tables.
- **G3 (P2) Fix whatever does not abstract.** Expect changes to `govbudget/` (fund-scoped keys, fiscal-year labelling, category schemes, comparable categories across governments). Record every government-specific oddity in its exceptions registry.
- **G4 (P3) Shared comparison taxonomy** so categories compare across governments (a mapping per entity onto a common set).

## 5. Suggested order

1. T1 (tests), then S1 (skeleton) as soon as decision 1 is made.
2. T2 (reference tables) and S2/S3 in parallel with T3.
3. G1-G3 once the site shows Belvidere well, so a second government is checked against a real consumer.
4. P3 items as time allows; D6 when the audit appears.

## 6. How to work on this project (applies to any chat or agent)

- Read `CLAUDE.md` first. Run `pipelines/belvidere_il/rebuild_all.sh` after any change to config or extracts and confirm existing outputs did not change.
- Every extracted table must tie to a total the source prints, in every column and year. Where the source's own arithmetic is off or a line is left out of its totals, keep the numbers, record the case in `entities/<id>/exceptions.csv`, and never force a total.
- Corrections read from page images go in `data/<id>/overrides/` with a written reason. Automatic corrections must make a printed total tie exactly and uniquely and be flagged (`auto_resolved`).
- Shared code (`govbudget/`) stays entity-agnostic; government-specific code lives in `pipelines/<id>/` and `entities/<id>/`.
- When running several agents in parallel in one checkout: each gets new, task-named files only, none runs git or the shared table builder, and one lead integrates and commits.
- Commit and push only when the owner asks.
