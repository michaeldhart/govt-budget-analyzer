# Boone County, IL: plan for the third government

Status: Phases 0-1 done 2026-10-31 (see results below); no extraction yet. Entity id (proposed): `boone_county_il`. Follows the pattern of `PARK_DISTRICT_PLAN.md`: every extracted table ties to a total the source prints, anomalies go in `entities/<id>/exceptions.csv`, corrections in `data/<id>/overrides/` with a reason.

## What the Financial Information page offers (checked 2026-10-31)
Page: https://www.boonecountyil.gov/government/financial_information/index.php (files under `/Financial Information/...`).
- **Budgets, FY2017-FY2026** (10 adopted) plus **FY27 Preliminary Budget "for layover"**. FY2021 and FY2022 files are titled "Proposed Budget - All Funds - Final". FY2024 is "Final Budget Book", FY2025 "Budget with Hyperlinks".
- **Audits, FY2015-FY2025** (11, "Financial Report"/"Final Audit"). No ACFR.
- **Circuit Clerk audits** (separate reports, 2009-2025 with gaps: no 2013, 2015, 2016, 2024).
- **Employee compensation** FY2017-FY2026 (projected, PDFs; FY2023 is an XLSX).
- Budget 101 guide, IMRF link. **Not on the page**: appropriation ordinances, levy ordinances, treasurer reports, anything before FY2015/FY2017.

## Probed so far (FY2026 budget, FY2017 budget, FY2025 audit, FY2015 audit)
- **Fiscal year is Dec 1 - Nov 30**; FY2026 = Dec 1, 2025 - Nov 30, 2026 (named by the ending year, same as the city). `fy_start_month` 12. Audits are "year ended November 30".
- **All four probed PDFs have a text layer** (budgets 104/116 pages, audits 173/159). Much less OCR than the city or park; the FY2015 audit's text layer may still be poor, check.
- **Budget layout** (FY2026): ERP "Budget Worksheet", account `001-0000-301000` = fund-department-object, five columns (prior-year budget, prior-year activity, current-year budget, current-year YTD, final budget), `Fund: 001 - General Fund / Department: 0000 / Revenue` headings. Covers all funds, so fund-based like the park district (`fund_based_budget`), not the city's General-Fund-first model. Older years (2017-2022) likely have different layouts; expect 2-4 readers as for the park.
- Revenue mix visible in the General Fund: property tax, 1 cent and 1/4 cent sales tax, state income tax, replacement tax, video gaming, cannabis, plus fees, court fees and state salary reimbursements (these reimbursements are not taxes; classify as `non_tax`).
- Large capital program (~$22M courthouse, health department, transit facility; ARPA, reserves, bonds, Rebuild Illinois grant) will dominate FY2024-26 capital funds and needs an exception note like the city's FY2027 transfer.

## Proposed phases
0. **Decisions** (below), create `entities/boone_county_il/entity.json` (`fy_start_month` 12, account format), `funds.csv`.
1. **Sources**: fetch all PDFs/XLSX to `sources/boone_county_il/{budgets,audits,compensation,circuit_clerk}/`, `source_urls.csv` with sizes, `inventory_sources.py` (pages, text vs scan, sha256). Check the county site for levy/appropriation documents and the County Clerk/Treasurer sites for extension data. Wayback/Comptroller warehouse only if gaps matter.
2. **Budget readers**, newest first: FY2026/FY2025 (text layer, tie to fund totals), then identify the layout of each year; validate revenue and expense lines to printed fund totals; FY27 preliminary stored as `requested`. The 5-column layout gives a cross-read (prior-year budget column vs previous document's final budget, as for the city).
3. **Audit readers**: governmental funds balance sheet and statement of revenues/expenditures/changes, General Fund budget-and-actual, the fund-level schedules, debt note, IMRF/sheriff-pension notes, legal debt margin, assessed valuations (levy/extension table is the best tax-side source). Reuse the park's `extract_audit_fund_statements` approach (`pdftotext -layout`, identity checks).
4. **Mappings and canonical build**: `account_map.csv`, `departments.csv` (sheriff, courts, highway, health, etc.), `categories.csv`, `audit_map.csv`/`comparable_groups.csv`, `build_facts.py`, `govbudget.validate`.
5. **Audit vs budget comparison** (original budget in audit vs adopted), exceptions registry, reference series (population, CPI, EAV) via `build_reference.py` pattern.
6. **Site**: `export_site` datasets (fund-based set as `export_park.py`, probably reused/generalized), pages for revenue, spending, levy/taxes, audit vs budget, fund balances, liabilities; add to the entity index.
7. **Tests and golden rebuild**: `tests/test_boone_county.py`, extend golden rebuild and structure tests.
8. **Optional**: compensation reports (like Exhibit B; names stay out of canonical tables), Circuit Clerk audits, first cross-government comparison (S5/G4) now that there are three entities.

## Decisions needed from the owner
1. **Scope of pass one**: county primary government only (recommended), or also the Circuit Clerk audits and component units (Boone County health department, nursing home / Maple Crest lease)? Recommendation: county only; Circuit Clerk and compensation later.
2. **Years**: budgets FY2017-FY2026 and audits FY2015-FY2025 as published (recommended); FY2016 budget and earlier only if found.
3. **Tax focus**: the city's work is tax-centred. For the county, recommend revenue by source (property tax, sales, income, fees, grants) across all funds, not only the General Fund.
4. **Compensation reports**: include in pass one (named individuals; only positions and amounts in canonical tables) or defer.
5. **Housekeeping**: confirm the entity id and that fiscal years are named by their ending year (FY2026 = Dec 2025 - Nov 2026).

## Risks / things that will probably not abstract
- Fund numbers and account structure changes across years (three-digit funds `001`; check older layouts).
- Budget worksheet columns mix budget and partial-year YTD activity; "actual" must come from audits, YTD columns are `actual_prelim` at best (flag the YTD-through-date label).
- Blend of state-reimbursed salaries and pass-through funds (courts, probation) that inflate revenue and expense without being local taxes.
- Pension-like funds (IMRF, sheriff's law enforcement) and the capital bond issue timing.
- FY2021/FY2022 titled "Proposed ... Final" files: confirm adopted status from the board minutes.

## Next step
Phases 0-1 are small and can start immediately once decisions 1-5 are answered.

## Phase 0 decisions (2026-10-31)
Owner: **county government only** (no Circuit Clerk, health department or other units). The other four decisions take the recommended defaults unless the owner says otherwise: budgets FY2017-FY2026 plus the FY27 preliminary, audits FY2015-FY2025; revenue by source across all funds; compensation reports downloaded but extraction deferred; entity id `boone_county_il`, fiscal years named by the year they end.

## Phase 1 results (done 2026-10-31)
- `pipelines/boone_county_il/fetch_sources.py [--refresh-index] [--check]` builds `entities/boone_county_il/source_urls.csv` from the Financial Information page and downloads **32 files (46 MB)** to `sources/boone_county_il/{budgets,audits,compensation}/` (budgets and audits gitignored by the existing rules, compensation added). Quirks: the CMS redirects to cms8.revize.com and answers HEAD requests unreliably (size falls back to the downloaded size); it is case-sensitive, so the FY2017 audit linked as `.PDF` is fetched as `.pdf` (note in the manifest). Circuit Clerk audits are skipped.
- `pipelines/boone_county_il/inventory_sources.py` -> `data/boone_county_il/source_inventory.csv` (pages, text or scan, sha256).
- **Layers**: all 11 audits (158-193 pages) have a text layer. 10 of 12 budgets have one; **FY2018 and FY2023 budgets are scans** (need OCR). FY2027 preliminary is 86 pages.
- `entities/boone_county_il/entity.json` (`fy_start_month` 12, `fund_based_budget` true, funds still empty).
- **Budget layouts differ a lot** (first look): FY2025, FY2026 and FY2027-prelim carry the ERP "Budget Worksheet" with account numbers (`001-0000-301000`), FY2025 four columns (prior activity, prior budget, YTD, current budget), FY2026 five, FY2027 five; FY2017-FY2024 are narrative budget books with a revenue summary by category (e.g. "Sales Taxes") and fund sections with line labels but, at least in FY2017, three-digit codes like `303.000` rather than the ERP format. So the account-level reader covers only the last three years; the older years need their own readers, and the account key must be built from fund + the old code. Each budget book also prints a projected property tax rate / levy table and the equalized assessed valuation (useful for the levy and reference series).
- FY2021 and FY2022 files are headed "Proposed Budget" with the county board meeting dates (Nov 18, 2020 and Nov 18, 2021; the filenames say "Final"); confirm adoption from board minutes before treating them as `adopted`.

## Next (Phase 2)
Read the newest budget first: `extract_budget_worksheet.py FY2026|FY2025|FY2027_budget_prelim` (text layer, fund/department totals as the checks), then map the older layouts one at a time. Parallel with that, the audits' governmental-funds statements (all text layer, can reuse the park's approach).

## Phase 2 progress (2026-10-31): ERP worksheet budgets
- `pipelines/boone_county_il/extract_budget_worksheet.py <doc_id>` reads the Budget Worksheet in FY2025, FY2026 and FY2027-preliminary (text layer) into `data/boone_county_il/extracted/<doc>_{lines,totals,checks}.csv`. Column meaning is read from each page's two-row header (`2025-2026` = FY2026; measures budget, activity, ytd, final, prelim). Lines: 828 (FY2026, 4,142 cells), 821 (FY2025, four columns), 811 (FY2027 prelim, whole dollars). Funds: about 50 (001 General through 202; Health 013 is last).
- **Validation, 0 open problems in all three**: every printed `Revenue Total:` / `Expense Total:` equals its lines in every column (625, 488 and 610 total cells); the whole-dollar FY2027 allows half a dollar per line. Accepted source differences are `allow_diff` rows in `data/boone_county_il/overrides/<doc>.csv` with reasons (FY2026: IT dept FY2024 budget -3, Tort Immunity +18, plus one `place` override for a Health Fund line whose two blank cells the text layer cannot place; FY2025: IT -2, Tort +18, three memorial/sale-in-error funds whose printed revenue totals are 0.00 above a non-zero line; FY2027: General Fund FY2026 revenue total $15,000 below its lines, Tort +18).
- **Cross-check between documents** (`tests/test_boone_county.py`, which also pins the totals): the FY2025 final budget equals the FY2026 document's prior-year budget column on 777 of 793 shared accounts (16 restated, e.g. Public Safety Sales Tax Fund interest 8,000 -> 10,000, Transport grant lines); the FY2024 budget agrees between the FY2025 and FY2026 documents on 789 of 793; the FY2026 final budget vs the FY2027 document's prior-year column differs on 8 of 790 (e.g. Health insurance 2,950,000 vs 2,945,040). These restatements become exceptions when facts are built.
- The FY2026 worksheet carries the FY2024 "Total Activity" column (prelim actual for the year ended Nov 30, 2024) and FY2025 year-to-date through 9/30/25; the FY2025 worksheet is a "Condensed" version with FY2023 activity and FY2024 YTD through 11/15/2024; FY2027 has FY2025 activity and FY2026 YTD through 10/6/26. Activity columns are preliminary; audits supply the real actuals.
- Next: FY2024 and older budget books (different layouts, FY2018 and FY2023 are scans), then audits.

## Phase 3 progress (2026-10-31): audited governmental fund statements, FY2015-FY2025
- `pipelines/boone_county_il/extract_audit_fund_statements.py FY20XX` reads the governmental funds **Balance Sheet** and **Statement of Revenues, Expenditures and Changes in Fund Balances** from each audit's text layer into `data/boone_county_il/extracted/audits/FY20XX_audit_{fund_statements,fund_checks}.csv` (one row per line x fund column; fund names per audit in `entities/boone_county_il/audit_fund_columns.csv`).
- **Layouts vary**: four to six columns (General plus Bridge, IMRF/Retirement, Motor Fuel Tax, American Rescue Plan Grant and Other/Nonmajor, and Total); FY2015-17 print the balance sheet and statement on two pages each (assets then liabilities; left funds then right funds), FY2021-22 split the funds across two pages; dates in labels ("NOVEMBER 30", "DECEMBER 1, 2014") are protected from being read as amounts; a row with a blank cell is placed by right edge (FY2025 "Other" receivable). The county's fund structure changes: in FY2025 the audit moves the Motor Fuel Tax and Bridge funds out of nonmajor ("Change in financial reporting entity" in the opening balance), which will need an exception.
- **Validation, all 11 audits pass 18-20 checks each, 0 failed**: Total column = sum of the fund columns on every line; total assets = lines and = liabilities + deferred inflows + fund balances; total revenues, expenditures and other financing = lines; excess = revenues - expenditures; net change = excess + other financing; ending = net change + beginning (the "as restated" line when the audit prints one).
- **Year-to-year chain** (`tests/test_boone_county.py`): the General Fund ending balance equals the next audit's opening balance in 9 of 10 pairs; FY2021's opening General Fund balance is restated by +100,024 (Note 15, prior year restatement); total governmental funds are restated by +850,726 (FY2020 to FY2021 opening) and -839,679 (FY2022 to FY2023 opening). To become exceptions when facts are built. Headline: General Fund balance $3.5M (Nov 30, 2015), $17.9M (2024), $13.8M (2025, after $6.6M transfers out for the capital program); total governmental funds $24.5M (2015), $43.7M (2025).
- Next in the audits: the General Fund detailed revenue and expenditure schedules (tax lines against budget), the per-fund budget-and-actual schedules (about 40 funds in FY2025, with original/final budget, to compare with the worksheet budgets), debt and pension notes, property tax levy and collections table.
