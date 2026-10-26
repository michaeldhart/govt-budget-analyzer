> Status: integrated into the canonical tables on 2026-10-25 (see CLAUDE.md). The 'integration' / 'lead applies' sections below are historical. Written by the extraction agent.

# Budgeted funds other than General and Water/Sewer: handoff

Status 2026-10-25. Extracted from the budget ordinances FY2015-FY2027. Nothing shared was modified: only new files were created (list at the end). `build_facts.py`, `entities/`, `CLAUDE.md`, `govbudget/` are untouched; the integration changes are in section 5-7 for the lead to apply.

## 1. What was extracted

Fund pages (Exhibit A, after the Water/Sewer fund) by `pipelines/belvidere_il/extract_budget_funds.py FY20XX`:

| fund (name in `fund` column) | code | years |
|---|---|---|
| Motor Fuel Tax #10 | 10 | FY2015-27 |
| Downtown TIF #12 (expired) | 12 | FY2015-16 |
| Kishwaukee TIF #13 | 13 | FY2015-27 |
| Belv Mall TIF Bond/Interest #14 | 14 | FY2015-17 |
| Kishwaukee II TIF #15 | 15 | FY2015-27 |
| Special Service Area 2 (Farmington Fields) #16 (FY2015-17: SSAs 2 and 3 combined) | 16 | FY2015-27 |
| Special Service Area 3 (Farmington Fields) #17 | 17 | FY2018-27 |
| Library Operations #18 | 18 | FY2015-19 (then the library's own budget, below) |
| Capital Projects #41 - Public Benefit Account / - Capital Assets Account (two ledgers, each with its own cash) | 41 | FY2015-27 |
| Escrow #91 | 91 | FY2015-27 |
| Police Pension Fund / Fire Pension Fund | PP / FP (pseudo codes, the pages print no fund number) | FY2015-23, FY2025-27 (FY2024's PDF has no pension page) |

Ida Public Library budget (the library's own line budget, 5-digit library accounts, dollars and cents, 1-3 columns) by `pipelines/belvidere_il/budget_funds_library.py FY20XX`: FY2020-FY2027 (fund name `Ida Public Library`, pseudo fund code `IPL`).

Not extracted (decided): the Exhibit B non-union salary schedule (positions and pay rates, not revenue/expense); the Water/Sewer sub-ledgers (Depreciation Funding, Bond Payments, Connection Fees; fund 61, already scoped out of `extract_budget_ws.py`); the rental-property and Sullivan sub-budgets printed after the library total in FY2023 (pdf 21-22); the Fire/Police pension pages of FY2024 do not exist in that PDF (23 pages, "15 of 15" absent), so those funds have no FY2024 document values (the FY2025 document's prior-year column has the FY2024 budget; its FY2023 actual column the FY2023 actual).

## 2. Files written and schemas

`data/belvidere_il/extracted/` (13 years x 4 files, 8 years x 4 library files):

* `FY20XX_budget_funds_revenue.csv`, `FY20XX_budget_funds_expense.csv` : same long format as the GF/WS extracts: `fiscal_year, doc, page_label, fund, dept_code, account, line_item, column, amount, flag`. `account` = `FF-R-DDD-OOOO` with `FF` the fund code from the page heading (not the printed prefix), `R` = 4 revenue / 5 expense. Lines the city prints without an account number (pension funds, Escrow "Disbursement of Collected Fees", "IL Jobs Now", Library "Other Revenues") have pseudo accounts `FF-R-000-CODE` (`PTAX RTAX INTINC CONTRIB BENEFIT REFUND ADMIN OTHEXP DISBURS OTHER OTHREV ILJOBS AUDADJ`), flag `no_account_number`. Library Operations lines printed with `XX` accounts keep them (`18-4-510-44XX`, `18-5-510-50XX/62XX/73XX`). Balance-sheet accounts printed in Escrow (`91-2046`, `91-2060`, counted in Total Revenues) become `91-4-000-2046/2060`, flag `balance_sheet_account`. `column`: `actual_FYy-2, budget_FYy-1, [budget_revised_FYy-1,] budget_FYy` (4 columns FY2015-22, 3 from FY2023). `amount` whole dollars; negative = printed in parentheses.
* `FY20XX_budget_funds_totals.csv` : `fiscal_year, doc, page_label, fund, kind, name, column, amount`; kinds `beginning_cash, ending_cash, fund_revenues, fund_expenses` (the city's printed "Total Revenues/Expenditures"; `Capital Asset Revenues/Expenditures` for the Capital Assets ledger) and, for fund 41, the printed "Capital Fund Totals" roll-up (FY2027 only): `rollup_beginning_cash, rollup_revenues, rollup_expenses, rollup_ending_cash`.
* `FY20XX_budget_funds_checks.csv` : every validation, `fiscal_year, doc, fund, check, column, printed, computed, diff, status`; checks `revenue_lines, expense_lines, cash_roll, cash_chain, rollup_*`; status `ok`, `rounding` (printed vs lines within the city's $1, or <=3 in actual columns), `allowed_override`, `note` (cash_chain only, informational).
* `FY20XX_budget_library_{revenue,expense,totals,checks}.csv` : same shapes. `account` = `IPL-4-000-CODE` / `IPL-5-000-CODE` (library code, e.g. `40100`; lines with no code get a letters slug, flag `no_account_number`; repeated codes flag `duplicate_account`); `column` is `budget_FYy` for the library budget year May y-1 - April y (library names years by start year; the ordinance cover says e.g. "May 2026 - April 2027" = FY2027); blank cells are simply not written (flag `blank_cell` is not used; absence = the library printed nothing). `amount` in dollars, cents kept when non-zero (e.g. 510.32, 614982.1). Totals file kinds `library_revenue_total, library_expense_total, library_subtotal` (name = printed label, e.g. TOTAL PERSONNEL).

`data/belvidere_il/overrides/`: `FY20XX_budget_funds.csv` (FY2019, 2023, 2024, 2025, 2026, 2027) and `FY20XX_budget_library.csv` (FY2021, 2023, 2026); header `kind,page_label,key,column,value,reason`, **reason mandatory**. Fund kinds: `row` (key `FUND[|Ledger]@account` or `@end/@beg/@revtotal/@exptotal`; values `v1|v2|..`; creates a printed total no OCR found), `cell`, `add_row` (ensure a line exists with these values), `allow_diff` (key `FUND@check`; the difference the city's arithmetic leaves), `not_in_total` (a printed line the city leaves out of its own totals; kept, flagged `not_in_printed_total`, excluded from sums), `drop`, `account`. Library kinds: `add_row, recode, cell, event_cell, allow_diff, not_in_total, drop`.

Flags on rows: `auto_resolved` (another OCR reading / neighbouring document makes a failing total tie exactly), `auto_resolved_digit` (single-digit confusion that makes it tie, >= $10), `auto_resolved_sign` (lost parenthesis; none needed in the end), `from_prep_vote` (short/garbled reading replaced by the other OCR readings' complete version), `placed_by_sum` (missing zero/repeated cells completed where the only completion ties every check), `padded_zeros`, `ocr_zero_guess`, `values_overridden`, `added_by_override`, `not_in_printed_total`, `duplicate_account`, `no_account_number`, `fund_prefix_16_printed` (see exceptions).

## 3. Method

* OCR: six readings of each fund page (cached under `data/belvidere_il/ocr/budget_pages/FY20XX_<prep>/`): `gray, thr150, thr120, thr180` plus two new ones, `nl` and `nlthr150`, which **erase the table rules before OCR** (long dark runs, `strip_lines()`): tesseract psm 6 silently drops rows that sit between the rules (every "Total Expenditures" / "Cash Balance - ending" line missing from `gray` in FY2019 came back). The extractor parses every reading, takes the one with the fewest problems, and uses the others plus neighbouring documents (shared prior-year/request column) and the cash chain (beginning cash of a column = ending cash before it) as alternative readings of a cell, adopted only when they make a failing printed total tie exactly and uniquely (more votes win only by a margin of 2).
* Page-boundary robustness: heading lost -> a second "Cash Balance - beginning" opens a new block whose fund comes from the account prefix; `CAPITAL PROJECTS FUND` is always 41; `Police/Fire Pension Fund` headings tolerate run-together OCR.
* Library: words with boxes (tesseract TSV on the rule-erased image), amounts placed in columns by x position (blank cells exist), dollar sign misread as 5/S/9 repaired when it makes a subtotal tie.

## 4. Validation results (all 13 fund years and 8 library years: 0 problems)

Checks per block and column: revenue lines = printed Total Revenues; expense lines = printed Total Expenditures; beginning + revenues - expenditures = ending cash (printed numbers); for fund 41 FY2027 the Capital Fund Totals roll-up (beginning, revenue, expenditures, ending). Where a fund prints no Total Expenditures (Motor Fuel Tax, the TIFs in some years) the cash roll is the only check of the expense lines.

| FY | blocks | lines (per column) | checks ok | within $1-3 | allowed overrides | cash-chain breaks |
|---|---|---|---|---|---|---|
| 2015 | 12 | 86 | 138 | 6 | 0 | 0 |
| 2016 | 12 | 86 | 136 | 8 | 0 | 0 |
| 2017 | 11 | 80 | 129 | 3 | 0 | 0 |
| 2018 | 11 | 92 | 124 | 8 | 0 | 4 (SSA 2/3 split) |
| 2019 | 11 | 92 | 126 | 6 | 0 | 0 |
| 2020 | 10 | 68 | 115 | 5 | 0 | 0 |
| 2021 | 10 | 69 | 113 | 7 | 0 | 0 |
| 2022 | 10 | 73 | 118 | 2 | 0 | 0 |
| 2023 | 10 | 73 | 86 | 1 | 0 (one `not_in_total`) | 1 |
| 2024 | 8 | 59 | 72 | 0 | 0 (one `not_in_total`) | 1 |
| 2025 | 10 | 75 | 88 | 1 | 1 | 0 |
| 2026 | 10 | 80 | 87 | 2 | 1 | 0 |
| 2027 | 10 | 91 | 92 | 4 | 0 (three `not_in_total`) | 2 |

Library (printed totals checked: TOTAL REVENUE / TOTAL EXPENSES in every column, plus every section subtotal in FY2026-27, the subtotal-of-subtotals TOTAL COLLECTION): FY2020 94 lines, FY2021 80, FY2022 87, FY2023 108, FY2024 106, FY2025 98, FY2026 102, FY2027 119; 93 checks, 91 ok, 2 `allowed_override` (FY2021 prior-year column). FY2020-22, 24, 25 print only the grand totals, so line-level errors that cancel are not excluded there.

Independent checks run on the final extracts (not part of the extractor): prior-year column vs the previous document's request column on 1,094 shared cells; the mismatches are all structural or restated figures where each document's own totals tie (library re-charting FY2018, SSA 2/3 split FY2018, Capital Assets transfers FY2020, Rebuild Illinois in MFT FY2022, pension property tax restated FY2023, MFT expense lines FY2026); none looked like an OCR error. Cash-chain: 347 of 355 column joins agree.

Hand-verified from page images: every override below, plus Capital Projects FY2022 and FY2027, Fire Pension FY2025-26, MFT FY2019-21/24, Kishwaukee FY2016/2021, the FY2023 and FY2026 library pages. Auto-resolved cells (about 20 `auto_resolved`, 1 `auto_resolved_digit`, ~60 `from_prep_vote`) were not all eyeballed; each makes an exact printed total tie.

## 5. Anomalies and suggested `exceptions.csv` rows

Columns: `exception_id,kind,scope,scope_id,fy_from,fy_to,bases,effect,summary,detail,decided_on,evidence`. (`decided_on` left for the lead.)

```
EX-capital-assets-omitted-grant,omitted_from_total,account,41:752-4150,2021,2021,actual_prelim,informational,"Capital Assets Grants line (25,000) is printed but left out of the city's Capital Asset Revenues total","FY2023 document, actual FY21 column: lines add to 602,144; printed total 577,144; cash roll ties to the printed ending cash without the grant. Flag not_in_printed_total.",,FY2023 budget p14 of 15
EX-capital-assets-omitted-misc,omitted_from_total,account,41:752-7900,2022,2022,actual_prelim,informational,"Capital Assets Misc. Expense (1,900) is printed but left out of Capital Asset Expenditures","FY2024 document, actual FY22 column: lines 613,329 vs printed total 611,429; cash roll ties without it.",,FY2024 budget p14 of 15
EX-capital-assets-stale-lines-fy27,omitted_from_total,account,41:752-8300|41:752-8910|41:752-8920,2025,2027,actual_prelim|adopted|prior_doc_budget,informational,"First Vehicles / Bond-Loan principal / interest lines in the FY2027 Capital Assets ledger are stale duplicates","The page prints Vehicles, Bond/Loan Prin, Bond/Loan Int twice (387,022/60,000/4,100 actual FY25, then 692,603/65,000/1,439 + lease lines). The city's Capital Fund Totals count only the second set (expenditures 2,763,259 = 1,562,846 + 1,200,413). First occurrence flagged not_in_printed_total (nth=1 of each account).",,FY2027 budget p14 of 15
EX-pb-hidden-expense-fy23,omitted_from_total,document,FY2025_budget,2023,2023,actual_prelim,informational,"Public Benefit actual FY23 printed Total Expenditures (2,336,580) is 645,628 above its listed lines","FY2025 document p14. The cash roll (853,747 + 1,381,652 - 2,336,580 = -101,181) ties to the printed ending cash, so the total includes expense lines the page does not show.",,FY2025 budget p14 of 15
EX-fire-pension-cash-fy24,source_arithmetic,account,FP:000-PTAX,2024,2024,actual_prelim,informational,"Fire Pension actual FY24: printed ending cash is 20,275 below beginning + revenues - expenditures","FY2026 document p16. 19,304,690 + 3,566,745 - 1,899,230 = 20,972,205 vs printed 20,951,930 (which equals the next column's beginning cash).",,FY2026 budget p16 of 16
EX-fire-pension-blurred-fy25,ocr_correction,document,FY2025_budget,2025,2025,adopted,informational,"Fire Pension FY25 request column is blurred in the scan","Values taken from the FY2026 document's prior-year column (they tie to the printed totals); ending cash 19,965,803 read from a blurred digit (arithmetic gives 19,965,802).",,FY2025 budget p15 of 15; FY2026 budget p16 of 16
EX-fy24-pension-pages-missing,structural_change,document,FY2024_budget,2024,2024,adopted,excluded,"FY2024 budget PDF has no Police/Fire Pension page and the Motor Fuel Tax page is out of order (last page)","23-page PDF; footer 12 of 15 is pdf 23, 15 of 15 absent. Pension funds have no FY2024-document values.",,sources/belvidere_il/budgets/FY2024_budget.pdf
EX-ssa-split-fy17,structural_change,account,16:110-4010|16:110-6090|16:110-7900,2015,2018,,informational,"Special Service Area #2 and #3 are one fund (#16) in FY2015-17 documents and two funds (#16, #17) from FY2018","The page note says the auditors asked for the split beginning FY2017. FY2018 prior-year columns therefore differ from the FY2017 request (and beginning cash chains break at SSA 2/3 in FY2018).",,FY2017/FY2018 budgets
EX-ssa3-account-prefix-fy18,source_typo,account,17:110-4010|17:110-6090|17:110-7900|17:110-4600,2018,2018,,informational,"SSA #3 lines are printed with fund prefix 16 (16-4-110-4010 ...) under the Fund #17 heading in FY2018","Account strings here use the heading's fund (17); flag fund_prefix_16_printed.",,FY2018 budget p13 of 16
EX-fund-duplicate-accounts,definitional,account,10:310-6000|10:310-4140|41:752-8300|41:752-8910|41:752-8920|41:752-8921,2015,2027,,informational,"Two different lines share one printed account in the same fund","MFT Street Maintenance and Contracted Work are both 10-5-310-6000; State Allotment and Transportation Tax (FY2022-27) both 10-4-310-4140; FY2027 Capital Assets prints some codes twice. Rows carry flag duplicate_account; key facts by (account_key, label) or sum.",,
EX-pension-no-accounts,definitional,entity,belvidere_il,2015,2027,,informational,"Police/Fire Pension and parts of Escrow/Library/MFT print lines without account numbers","Pseudo accounts PP/FP-R-000-CODE; fund codes PP and FP are this project's, not the city's. Administration (FY2021+) vs Other Expenditures (FY2015-20) are the same slot with a renamed label but separate pseudo codes (ADMIN, OTHEXP).",,
EX-library-fund-to-ipl,structural_change,entity,belvidere_il,2015,2020,,informational,"Library is Library Operations Fund #18 on the fund pages FY2015-19 and the library's own 5-digit budget from FY2020","Different chart of accounts (18-4-510-4010 vs 40100). Not mapped to each other; fund codes 18 and IPL.",,
EX-library-list-books-fy26,omitted_from_total,account,IPL:000-73100,2026,2026,adopted,informational,"List Books (73100, 4,000) is printed under Other Collection but the city's Total Other Collection, TOTAL COLLECTION and TOTAL EXPENSES omit it","FY2026 document p23: Other Collection lines add to 29,500, printed 25,500.",,FY2026 budget p23
EX-library-fy21-prior-totals,source_arithmetic,document,FY2021_budget,2020,2020,adopted,informational,"FY2021 library prior-year column prints 844,000 revenue and expense totals that its lines do not reach (841,900 / 839,750)","The FY2020 document prints the same 844,000 with more detail lines.",,FY2021 budget p39-40
EX-library-fy23-subbudgets,structural_change,document,FY2023_budget,2023,2023,adopted,excluded,"Library FY2023 pages also print a rental-property and a Sullivan account sub-budget after TOTAL EXPENSES","Not extracted; library total (896,250) is the first Total Expenses.",,FY2023 budget p21-22
EX-negative-cash-balances,definitional,entity,belvidere_il,2015,2027,,informational,"Several budgets plan negative ending cash (MFT FY2023-24, SSA 3, Capital Projects Public Benefit FY2016-27 columns)","Printed in parentheses; stored negative. Not an extraction error.",,
```

Prior-year restatements (informational, each document's own totals tie, so both versions are kept as separate `basis` rows): see section 4; they follow the existing `EX-restated-prior-expense-fy*` pattern and could be added the same way if wanted.

## 6. Suggested config rows

* `account_map.csv`: `docs/handoff/budget_other_funds_account_map_suggested.csv` (347 rows, header = the account_map header; fund-scoped keys `10:310-6000`, `41:110-8020`, `41:752-8100`, `PP:000-PTAX`, `IPL:000-40100`; `treatment` non_tax; category suggestions by object-code range reusing `exp_*` and adding the new categories below; library lines map with `IPL` keys, first digit 5/6/7/9 -> personnel / contractual / supplies / miscellaneous).
* `categories.csv` new rows (scheme `budget_category`, flow revenue): `fund_tax_levy` "Property tax and replacement tax levied for the fund", `fund_intergovernmental` "State allotment, grants", `fund_transfer_in` "Operating transfers in", `fund_contributions` "Pension contributions", `fund_interest` "Interest", `fund_other_revenue` "Other fund revenue (fees, rent, gifts, escrow)"; scheme `expense_object`: `exp_debt_service` "object codes 8900-8999: bond/loan principal and interest", `exp_pension_payments` "Pension benefit payments and refunds (no object code)".
* `departments.csv` new rows (`fund_code,dept_code,name,function,note`): `10,310,Motor Fuel Tax (street),Public works,`; `12,110,Downtown TIF,Community development,`; `13,110,Kishwaukee TIF,Community development,`; `14,110,Belv Mall TIF Bond/Interest,Community development,`; `15,110,Kishwaukee II TIF,Community development,`; `16,110,SSA 2 Farmington Fields,Public works,`; `17,110,SSA 3 Farmington Fields,Public works,`; `18,510,Library Operations,Health and welfare,`; `41,110,Capital Projects - Public Benefit,Capital projects and transfers,`; `41,752,Capital Projects - Capital Assets,Capital projects and transfers,`; `91,110,Escrow,Community development,`; `PP,000,Police Pension,Public safety,pseudo fund code`; `FP,000,Fire Pension,Public safety,pseudo fund code`; `IPL,000,Ida Public Library,Health and welfare,library's own chart of accounts`.
* `entity.json` `funds`: add `{"code":"10","name":"Motor Fuel Tax"}`, 12, 13, 14, 15, 16, 17, 18, 41, 91, PP, FP, IPL (names from the table in section 1).

## 7. Exact code changes for the lead

1. `pipelines/belvidere_il/build_facts.py`, budget loop (after the Water/Sewer block, ~line 140): add a loop over `FY{fy}_budget_funds_{revenue,expense}.csv` and `FY{fy}_budget_library_{revenue,expense}.csv` copying the WS code with `fund_code` and key derived from the account string: `fc, rest = r["account"].split("-", 1)[0], ...` -> `key = fc + ":" + E.account_key(r["account"])` where for pseudo and library accounts `E.account_key` must return `DDD-OOOO` for `FF-R-000-CODE` (dept `000`, object may be letters or 5 digits: check `account_key` regex accepts non-numeric/5-char objects, else build `f"{parts[2]}-{parts[3]}"` directly). `flow` = the file kind; `fund_code` = the `FF` prefix (`PP`, `FP`, `IPL` are not numeric: check `schema.py` validation of `fund_code`). Totals: read `*_budget_funds_totals.csv` and `*_budget_library_totals.csv` like the WS totals (`kind` values listed in section 2; the totals have `fund` names, not codes; map via the same table as the account map suggestion, or add a `fund_code` when integrating). Skip the doc-year when the file is missing (nothing missing: all 13 years exist).
2. Document `doc_id`/`source` rows: the new extracts come from the same PDFs as FY20XX_budget; no new documents.
3. `govbudget/validate.py` (if it enforces account_map coverage): the 347 suggested rows must be added before running.
4. Duplicate accounts: `facts.csv` key is `(doc_id, period_fy, basis, account_key)` if unique-enforced: lines flagged `duplicate_account` break it. Suggest adding the extract row order (`row_seq` already exists in the long loop) to the uniqueness key, or suffix the key (`10:310-6000#2`) for `nth > 1`.
5. `exceptions.csv`: section 5 rows. `reconciliation`/`control_totals`: add the printed totals of `*_totals.csv` as control totals (kinds fund_revenues, fund_expenses, beginning_cash, ending_cash, library_*).
6. Rebuild order: `extract_budget_funds.py FY20XX` for FY2015..FY2027 in ascending order, then once more (the second pass lets each year use its neighbours' extracts as alternative readings; the results are stable after the second pass), `budget_funds_library.py FY2020..FY2027`, then `build_facts.py`.
7. The fund pages need no changes to `extract_budget.py` / `extract_budget_ws.py` (imported, never edited).

## 8. Ready-to-paste CLAUDE.md section

```
## Other budgeted funds (done 2026-10-25: budgets FY2015-FY2027; Ida Public Library budget FY2020-FY2027)
`extract_budget_funds.py FY20XX` reads the fund pages after Water/Sewer in Exhibit A: Motor Fuel Tax #10, TIF #12 (FY2015-16), #13, #14 (FY2015-17), #15, SSA #16 (and #17 from FY2018; FY2015-17 print 2 and 3 combined), Library Operations #18 (FY2015-19), Capital Projects #41 (Public Benefit and Capital Assets ledgers), Escrow #91, Police/Fire Pension (fund codes PP/FP are ours). Writes `FY20XX_budget_funds_{revenue,expense,totals,checks}.csv` (3,718 cells) in the usual long format; account strings carry the heading's fund (`10-5-310-6000`), lines without a number get pseudo accounts `PP-4-000-PTAX`. All 13 years validate with 0 problems: revenue lines = Total Revenues, expense lines = Total Expenditures, beginning + revenues - expenditures = ending cash (the only check where no Total Expenditures is printed), fund 41 roll-up (FY2027). Method: six OCR readings per page (gray, thr150/120/180 and `nl`/`nlthr150` which erase the table rules first: tesseract psm 6 drops rows between ruled lines), cross-read cell by cell with the neighbouring documents' shared column and the cash chain; a reading is adopted only if it makes a failing printed total tie exactly. Overrides `data/belvidere_il/overrides/FY20XX_budget_funds.csv` (kinds row, cell, add_row, allow_diff, not_in_total, drop, account; reason required). Source quirks (exceptions in the handoff doc): city leaves printed lines out of its totals (Capital Assets FY2021/FY2022 actual, FY2027 stale duplicate Vehicles/Bond lines, Public Benefit FY2023 actual total 645,628 above its lines), Fire Pension FY2024 actual cash roll is 20,275 off, the Fire Pension FY2025 request column is blurred in the scan, FY2024's PDF has no pension page and its Motor Fuel Tax page is last, SSA 2/3 split in FY2018, SSA 3 printed with prefix 16 in FY2018, negative cash balances in parentheses (OCR drops them: sign flips are auto-resolved only when a total ties), duplicate accounts in one fund (flag `duplicate_account`).
`budget_funds_library.py FY20XX` reads the Ida Public Library budget (FY2020-27; 5-digit library codes, `IPL-4-000-40100`, dollars and cents): TSV words on the rule-erased page placed in columns by x, validated to TOTAL REVENUE/EXPENSES and (FY2026-27) every section subtotal; columns `budget_FYy` = May y-1..April y; FY2023 sub-budgets after the total are skipped; overrides `FY20XX_budget_library.csv` (FY2021 prior-year totals allowed, FY2023 13 lines missing from every OCR reading added from the page image, FY2026 List Books left out of the city's totals).
Not extracted: salary schedule (Exhibit B), Water/Sewer sub-ledgers, FY2024 pension funds (absent from the PDF).
```

## 9. Files created (all new)

* `pipelines/belvidere_il/extract_budget_funds.py`, `pipelines/belvidere_il/budget_funds_library.py`
* `data/belvidere_il/extracted/FY2015-FY2027_budget_funds_{revenue,expense,totals,checks}.csv`, `FY2020-FY2027_budget_library_{revenue,expense,totals,checks}.csv`
* `data/belvidere_il/overrides/FY2019/2023/2024/2025/2026/2027_budget_funds.csv`, `FY2021/2023/2026_budget_library.csv`
* OCR caches `data/belvidere_il/ocr/budget_pages/FY20XX_{nl,nlthr150,thr120,thr180,gray_p4,thr150_p4}/` and `FY20XX_nl_tsv/` (the `_p4` caches are unused leftovers and can be deleted)
* `docs/handoff/budget_other_funds.md`, `docs/handoff/budget_other_funds_account_map_suggested.csv`
