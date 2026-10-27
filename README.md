# Govt Budget Analyzer

Local-government budgets and audits are published as scanned PDFs. This project turns them into validated, machine-readable tables and a website that shows budgeted revenue and expenses against audited actuals across years.

Currently covered: the **City of Belvidere, IL** (fiscal year May 1 to April 30), with budget ordinances for FY2015-FY2027 and audited annual financial reports for FY2012-FY2026. That covers the General Fund, Water/Sewer, the other budgeted funds (Motor Fuel Tax, TIFs, SSAs, Capital Projects, pensions, Ida Public Library), audited fund statements, debt, pensions and OPEB.

Live site: https://michaeldhart.com/govt-budget-analyzer/

## How it works

- **Every extracted table must tie to totals the source prints.** Each extractor OCRs (or reads the text layer of) a document and fails loudly unless line items sum to the subtotals, department totals and fund totals the city itself prints, in every column. OCR misreads are fixed through documented overrides, never silently.
- **One shared data model.** `govbudget/` defines the canonical tables (`documents.csv`, `accounts.csv`, `facts.csv`, and so on) for any government. A custom pipeline per government is the only code that knows that government's PDF layouts.
- **Anomalies go to an exceptions registry** (`entities/<entity_id>/exceptions.csv`): typos in the source, lines the city leaves out of its own totals, restated columns, definitional differences. Affected rows carry the exception ids.
- **Mappings are data**: account-to-category, audit-line-to-comparable-group and similar mappings are CSVs you edit, then rebuild.
- **The site only reads canonical tables.** `python3 -m govbudget.export_site` writes static JSON, and the Astro site in `web/` is built from it.

`CLAUDE.md` holds the detailed notes (architecture, per-table validation, source quirks, methodology decisions, headline findings). `docs/ROADMAP.md` holds status, open decisions and next steps. `docs/extraction-notes/` has the extraction hand-off notes.

## Repository layout

| Path | Contents |
|---|---|
| `govbudget/` | Shared, entity-agnostic code: schema, entity config and account keys, validation, reconciliation, site export, chart palette |
| `entities/<entity_id>/` | Hand-edited, committed config per government: `entity.json`, category, account and audit mappings, comparable groups, `exceptions.csv` |
| `pipelines/<entity_id>/` | That government's extractors (OCR etc.), builders and report scripts; `rebuild_all.sh` rebuilds every derived table |
| `data/<entity_id>/` | `extracted/` per-document extracts, `overrides/`, the canonical tables and derived series |
| `sources/<entity_id>/` | Source PDFs (the FY2027 budget is committed; older ones are gitignored) |
| `output/<entity_id>/` | Generated PDF reports and charts |
| `web/` | The website: Astro 7, Svelte 5, Observable Plot, static output |
| `tests/` | pytest suite for the Python side (tie-outs, table structure, parsers, site export, golden rebuild) |
| `docs/` | `ROADMAP.md` and extraction notes |

## Install

**Python.** CI uses Python 3.12. Packages found by reading the imports in `govbudget/`, `pipelines/` and `tests/`:

```sh
pip install pytest pdfplumber pillow matplotlib reportlab numpy
```

Only some are needed for a given task: tests and the site export need `pytest` (CI also installs `pdfplumber` and `pillow` because the parser tests import the extractors); the PDF reports use `matplotlib` and `reportlab`; `numpy` is imported by `extract_budget_funds.py`. There is no requirements file or `pyproject.toml` yet.

**System tools** (only needed to run the extractors against source PDFs, not for tests or the site):

- `tesseract` (OCR), called by the budget and audit extractors
- poppler's `pdftotext`
- Ghostscript (`gs`), used to rasterize pages in the other-funds, library and debt/pension extractors

(`ocrmypdf` is not called by the code.)

**Node.** The site needs Node >=22.23.4 (`engines` in `web/package.json`); CI uses the version in `web/.nvmrc`. Then:

```sh
cd web && npm ci
```

## Usage

Run commands from the repository root unless noted.

```sh
# Python tests (fast, about a few seconds)
python3 -m pytest
python3 -m pytest -m "not slow"     # what CI runs
python3 -m pytest -m slow           # golden rebuild: runs rebuild_all.sh in a scratch copy and compares the CSVs, about a minute

# Rebuild every derived Belvidere table from the committed per-year extracts
pipelines/belvidere_il/rebuild_all.sh

# Export the static JSON the site reads (default web/public/data/, gitignored)
python3 -m govbudget.export_site [entity_id] [--out DIR]

# The site (cd web)
npm run dev        # dev server
npm run build      # production build into web/dist (also runs scripts/check-build.mjs)
npm run preview    # serve the production build
npm test           # site unit tests (URL state)
```

Export the data before `npm run dev` or `npm run build`: the site reads `web/public/data/` at build time. `npm run export` in `web/` is a shortcut for the export command. The site defaults to base path `/govt-budget-analyzer`; set `SITE_URL` and `BASE_PATH` to build for another host.

`rebuild_all.sh` starts from the committed extracts in `data/belvidere_il/extracted/`. Re-running the extractors themselves (`extract_budget.py FY20XX`, `extract_audit.py FY20XX`, and the other `extract_*.py` scripts) needs the source PDFs and is slow; the per-year run order is in `CLAUDE.md` ("Pipeline run order"). Each extractor takes a fiscal year, for example `python3 pipelines/belvidere_il/extract_budget.py FY2019`.

CI (`.github/workflows/pages.yml`) runs the fast tests, the export, the site tests and the build on every push and pull request, and deploys to GitHub Pages from `main`.

## Data sources

- Budget ordinances FY2015-FY2027: https://www.belvidereil.gov/budget/
- Audited annual financial reports FY2012-FY2026: https://www.belvidereil.gov/audit/

The FY2027 budget (and its revision) is committed under `sources/belvidere_il/`. The older budget and audit PDFs (`sources/*/budgets/`, `sources/*/audits/`) are large and gitignored; download them from the pages above into those folders (named like `FY2019_budget.pdf` and `FY2019_audit.pdf`). The committed tables record each PDF's sha256 in `documents.csv`. Tests and the site do not need the PDFs.

## Adding a government

Create `entities/<id>/` (`entity.json` with `fy_start_month` and `account_format`, categories, account map), write `pipelines/<id>/` extractors and a `build_facts.py` that produces the canonical tables, and run `python3 -m govbudget.validate`. Everything downstream reads only the canonical tables. See the "Adding a government" bullet in `CLAUDE.md`'s architecture section.

## Contributing

Issues and pull requests are welcome. Keep the rule that numbers must tie to the source's printed totals, record source anomalies in `exceptions.csv` rather than in code, change classifications by editing the mapping CSVs, and run `python3 -m pytest` (and `npm test` in `web/` for site changes) before submitting. A deliberate change to a number means updating the pinned values in `tests/` and regenerating the CSVs.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
