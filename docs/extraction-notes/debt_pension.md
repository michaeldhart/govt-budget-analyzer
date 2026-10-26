> Status: integrated into the canonical tables on 2026-10-25 (see CLAUDE.md). The 'integration' / 'lead applies' sections below are historical. Written by the extraction agent.

# Handoff: audit long-term debt, pension and OPEB disclosures (FY2012-FY2026)

Written by the debt/pension agent. Nothing shared was modified: no existing script, config CSV, CLAUDE.md, `govbudget/` or facts table was touched, no git command was run, `build_facts.py` was not run. All files below are NEW.

## 1. What was built

| File | Purpose |
|---|---|
| `pipelines/belvidere_il/extract_audit_debt_pension.py` | CLI. `FY20XX` extracts one audit; `--build` consolidates all years and runs the cross-year checks |
| `pipelines/belvidere_il/audit_debt_pension_common.py` | positioned-word reader (text layer via `pdftotext -bbox`, OCR via tesseract TSV), number parsing, column assignment, `Checker` (every check is counted, accepted differences are listed, never silent) |
| `audit_debt_pension_debt.py` | debt note: activity table, issue terms, debt service to maturity (FY2016-26 layout) |
| `audit_debt_pension_pension.py` | RSI schedules (NPL roll-forward, contributions, investment returns) and the pension note (roll-forwards, membership, sensitivity, deferred items, assumptions) |
| `audit_debt_pension_opeb.py` | OPEB: GASB 45 net OPEB obligation tables (FY2016-18), GASB 75 total OPEB liability roll-forward (FY2019-26), OPEB RSI, Ida Public Library plan and debt |
| `audit_debt_pension_stmt.py` | independent cross-check sources: Statement of Net Position and the reconciliation of governmental funds to governmental activities |
| `audit_debt_pension_old.py` | FY2012-FY2015 readers (OCR word boxes; pre GASB 68/75 layouts) |
| `audit_debt_pension_build.py` | consolidation + cross-year checks |
| `data/belvidere_il/extracted/audits/FY20XX_audit_{debt_balances,debt_issues,debt_service,pension_rollforward,pension_summary,opeb_rollforward,debt_pension_checks}.csv` | per-year extracts (105 files) |
| `data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv` | overrides FY2012-14, 2015, 2016, 2020, 2021, 2026 (kinds `allow`, `fixtok`; each with a reason) |
| `data/belvidere_il/{debt_balances,debt_issue_terms,debt_service_schedule,pension_rollforward,pension_summary,opeb_rollforward}.csv` | canonical long tables |
| `data/belvidere_il/audit_debt_pension_checks.csv`, `audit_debt_pension_crosschecks.csv` | validation log (per year x check group) and cross-year / cross-statement checks |
| `data/belvidere_il/ocr/audit_pages/FY201[2-5]_dp_tsv_gray/` | OCR word-box cache for FY2012-15 pages 50-92 |

Run order: `extract_audit_debt_pension.py FY2012` ... `FY2026` (each takes ~10 s; FY2012-15 need the OCR cache, which is built on first run) then `extract_audit_debt_pension.py --build`. Needs poppler `pdftotext` (new dependency; tesseract/gs/qpdf as before).

## 2. Schemas (canonical tables, all with `entity_id=belvidere_il`, `doc_id=FY20XX_audit`, `period_fy`)

- **debt_balances**: `activity` (governmental | business_type | component_unit = Ida Public Library), `line_id` (debt_certificates, tif_note, capital_lease, installment_contract, revolving_loan, mortgage_loan, revenue_bonds, landfill_postclosure, compensated_absences, opeb, npl_imrf, npl_slep, npl_police, npl_fire, net_pension_obligation, asset_retirement, bond_premium, total), `line_item` (as printed), `measure` (beginning, additions, reductions, ending, current_portion), `amount`, `pdf_page`, `flag`, `exception_ids`. FY2012 prints 4 columns (no current portion; it is in the issue table).
- **debt_issue_terms**: `activity, issue, interest_rate_text, maturity_text, ending_balance, current_portion, description, pdf_page, flag`. `flag=derived_gap` rows are the IEPA loan draws that have no payment schedule (see 6.1).
- **debt_service_schedule**: `activity, instrument, line_id` (matched to the balance line whose ending equals the principal total), `year_ending` (year, or `TOTAL`), `principal, interest, total, row_kind` (year|total), `flag` (`total_derived` = library schedule prints no total column).
- **pension_rollforward**: `plan_id` (imrf_regular, imrf_eco, imrf_slep, police, fire), `source` (`note` = current-year note table with 3 columns tpl/fnp/npl; `rsi` = every column of the 10-year schedule; `note_gasb27` = pre-2016 net pension obligation table), `measurement_date`, `column_year`, `component` (tpl|fnp|npl|npo), `line_id` (beginning, service_cost, interest, benefit_terms, experience, assumptions, employer/member/other_contributions, investment_income, benefit_payments, administrative_expense, other, net_change, ending).
- **pension_summary** (also holds the OPEB plans, `plan_id` opeb_city / opeb_library): `measure`, `period_label` (RSI column year or discount-rate label), `value`, `value_text`, `unit`, `source` (rsi|note). Measures: tpl, fnp, npl, funded_ratio_pct, covered_payroll, npl_pct_covered_payroll, adc, employer_contribution_actual, contribution_deficiency, contribution_pct_covered_payroll, money_weighted_return_pct, members_{retirees,terminated_vested,active,total}, npl_at_discount_rate_{minus1,current,plus1}, deferred_outflow:<source> / deferred_inflow:<source>, deferred_amortization:<year>, pension_expense, imrf_allocation:<topic>:<activity>, discount_rate, assump_*, employer_contribution_pct_payroll, employee_contribution_rate, money_weighted_return, funded_status_pct_text; OPEB: opeb_expense, tol_at_{discount_rate,healthcare_trend}_*, discount_rate, healthcare_trend, valuation_date. FY2012-15: funded_ratio_pct, aal, ava, uaal_signed, covered_payroll, uaal_pct_payroll, employer_rate_pct, employee_rate_pct, trend_{annual_pension_cost,employer_contribution,pct_apc_contributed,npo}.
- **opeb_rollforward**: `plan_id`, `source` (`note` GASB 75 roll-forward, `rsi` multi-year, `note_gasb45` net OPEB obligation / funded status lines, `note_trend`), `line_id` (beginning, service_cost, interest, benefit_terms, experience, assumptions, benefit_payments, net_change, ending, covered_payroll; GASB 45: arc, interest_on_noo, arc_adjustment, annual_opeb_cost, contributions, increase, noo_beginning, noo_ending, funded_aal ...).
- **audit_debt_pension_checks**: `period_fy, check_group (debt, rsi, rf, def, mem, sens, alloc, opeb, opeb_rsi, pen, xchk), passed, accepted_exceptions, failed`.
- **audit_debt_pension_crosschecks**: `check_group, key, value_a, value_b, diff, status (pass | DIFF-explained | n/a), note`.

## 3. Validation results (0 failed checks in every year)

| FY | checks passed | accepted source anomalies | failed |
|---|---|---|---|
| 2012 | 50 | 2 | 0 |
| 2013 | 83 | 1 | 0 |
| 2014 | 72 | 2 | 0 |
| 2015 | 78 | 2 | 0 |
| 2016 | 308 | 10 | 0 |
| 2017 | 365 | 0 | 0 |
| 2018 | 387 | 0 | 0 |
| 2019 | 421 | 0 | 0 |
| 2020 | 453 | 6 | 0 |
| 2021 | 507 | 2 | 0 |
| 2022 | 548 | 0 | 0 |
| 2023 | 601 | 0 | 0 |
| 2024 | 647 | 0 | 0 |
| 2025 | 689 | 0 | 0 |
| 2026 | 683 | 2 | 0 |

Identities enforced (each in every year it is printed):
- **Debt**: beginning + additions - reductions = ending per line; lines sum to the printed total in all 5 columns (FY2013-15: to the unlabeled subtotal); issue table items sum to its TOTAL and tie to the debt lines; schedule rows principal + interest = total, years sum to the printed TOTAL row, and each instrument's principal total equals an outstanding balance. FY2013-15 bond/TIF/loan issue tables: beginning + issuances - retirements = ending and sum to the printed total.
- **Pensions (FY2016-26)**: RSI and note roll-forwards: lines sum to net change, beginning + net = ending (TPL and FNP), NPL = TPL - FNP, funded ratio = FNP/TPL and NPL/payroll within rounding, year N ending = year N+1 beginning inside each table, contributions: deficiency = ADC - actual, % of payroll; membership classes = TOTAL; sensitivity ordering; deferred outflow / inflow items = TOTAL; amortisation years = TOTAL and = net deferred items; IMRF totals by governmental / business-type / library = TOTAL. **FY2012-15 (GASB 27)**: ARC + interest + adjustment = APC; APC - contribution = change in NPO; NPO beginning + change = ending; funded status UAAL = AAL - AVA, funded %, UAAL % payroll; trend table % contributed and NPO chain.
- **OPEB**: GASB 75 roll-forward, sentence total = roll-forward ending, sensitivity current column = TOL, deferred items, RSI (lines, roll, chain, % of payroll); GASB 45: ARC + interest + adjustment = cost, cost - contributions = increase, NOO roll, funded status, trend % and NOO chain.

Independent cross-checks (all in `audit_debt_pension_crosschecks.csv`; 313 pass in-year `xchk`):
- Debt table net pension liability lines (police, fire, SLEP, IMRF across governmental + business-type + library) = the note's NPL (an asset is shown as 0); OPEB lines (governmental + business-type) = total OPEB liability; library OPEB = library note. FY2016, whose debt table has no pension lines, ties to the Statement of Net Position instead.
- Statement of Net Position: long-term liabilities "due within one year" = debt table current portion, "due in more than one year" = total ending - current (governmental, business-type, library columns); deferred outflows / inflows by plan = the pension and OPEB notes.
- Reconciliation of governmental funds to governmental activities: every long-term liability line (installment contract, certificates, TIF note, capital lease, landfill, compensated absences, OPEB, each NPL) = governmental debt table ending.
- Governmental funds statement (`fund_statements.csv`): "Principal" expenditure = reductions on debt certificates / capital lease / installment contract / revolving loan in the debt note: 14 of 15 years tie to the dollar (FY2012 is $46,408 higher: landfill post-closure payments are presented as principal).
- Cross-year: 201 line chains pass, 3,905 NPL-schedule cells repeated in later audits agree to the dollar (0 differences), 2,560 other RSI cells agree (37 early-year restatements explained), 123 pension note chains and 14 OPEB chains pass, RSI last column = note ending in 136 plan-years.

## 4. Not resolved / limitations

- **FY2012 debt service to maturity** (two multi-instrument tables with 5-year bands, page 54) is NOT extracted; the FY2012 activity table, issue table (ties to the activity table), pension (trend, ARC / NPO) and OPEB are.
- **FY2012-FY2015 statement cross-checks** (Statement of Net Position, reconciliation) are not implemented for the OCR years; the notes there validate on their own identities and on the cross-year chain only.
- **FY2012-15 pension coverage is the GASB 27 disclosure** (no TPL/FNP/NPL exists before GASB 68): funded status (AAL, AVA, UAAL) is captured FY2013-15 (FY2012's note has none; it is in its RSI, not extracted), plus ARC/APC/NPO and trend. The RSI funding-progress schedules of FY2012-15 were not read.
- FY2012-15 OPEB: net OPEB obligation table, funded status and trend (FY2013-15), no library plan before FY2016 (FY2015 gives the library portion of the NOO in one line, captured as `noo_library_portion`).
- Police / fire "Plan Net Position" and "Changes in Plan Net Position" tables in the notes, the IMRF target asset allocation, and the pledged-revenue tables were not extracted.
- Library (component unit) pension roll-forward is not in the audit separately (its share is in the IMRF allocation table, extracted).

## 5. Anomalies and suggested `entities/belvidere_il/exceptions.csv` rows

Rows already referenced by `exception_ids` in the canonical tables (ids are mine, invented; adopt or rename):

```
exception_id,kind,scope,scope_id,fy_from,fy_to,bases,effect,summary,detail,decided_on,evidence
EX-adp-police-fy2016-note-rounding,source_typo,doc,FY2016_audit,2016,2016,,informational,"FY2016 police pension note roll-forward is $1 off in the fiduciary column (ending 18,648,664 vs RSI 18,648,665; employee contributions 291,900 vs 291,901)","Lines do not sum by $1; the next audit's opening balance uses 18,648,664; Statement of Net Position NPL 13,386,846 vs note 13,386,847",2026-10-25,audit FY2016 pdf p76 and p105
EX-adp-slep-fy2016-amort-3,source_typo,doc,FY2016_audit,2016,2016,,informational,"SLEP deferred amortisation schedule totals 44,194; the deferred outflows table nets to 44,191","$3 difference inside the source",2026-10-25,audit FY2016 pdf p69
EX-adp-imrf-fy2016-deferred-1,source_typo,doc,FY2016_audit,2016,2016,,informational,"Statement of Net Position IMRF deferred outflows 1,232,590 vs the three IMRF notes 1,232,591","$1",2026-10-25,audit FY2016 pdf p24 vs p67-69
EX-adp-library-fy2016-sched-typo,source_typo,doc,FY2016_audit,2016,2016,,informational,"Library debt service schedule prints TOTAL interest 578,516; the years sum to 57,516","Principal total 344,437 agrees; the total column is derived",2026-10-25,audit FY2016 pdf p92 (component unit note)
EX-adp-imrf-fy2020-sign,source_typo,doc,FY2020_audit,2020,2020,,informational,"IMRF regular note roll-forward prints the NPL column 'Net changes' as 2,281,444 without parentheses; it is (2,281,444)","907,201 - 3,188,645; verified on the page image; RSI is correct",2026-10-25,audit FY2020 pdf p67
EX-adp-opeb-fy2020-netchange,source_typo,doc,FY2020_audit,2020,2020,,informational,"City OPEB note prints net changes 3,607,811; the lines and the RSI give 3,067,811","Beginning 8,196,747 + 3,067,811 = ending 11,264,558, which is what is printed",2026-10-25,audit FY2020 OPEB note vs RSI
EX-adp-police-fy2021-amort-sign,source_typo,doc,FY2021_audit,2021,2021,,informational,"Police deferred amortisation TOTAL printed 3,535,401 without parentheses; the years sum to (3,535,401) which equals the reconciliation line","",2026-10-25,audit FY2021 pdf p76
EX-adp-rsi-fy2026-deficiency,source_typo,doc,FY2026_audit,2026,2026,,informational,"FY2026 police and fire contribution schedules print a $1 deficiency for 2021 where ADC = contributions (FY2021-25 audits print a dash)","",2026-10-25,audit FY2026 pdf p103-104
EX-adp-police-npo-2011-typo,source_typo,doc,FY2013_audit,2013,2013,,informational,"FY2013 trend table prints the 2011 police net pension obligation as 1,749,189 (page image confirmed); FY2012 prints 1,746,189 and only that value chains","$3,000 difference",2026-10-25,audit FY2013 pdf p76
EX-adp-pension-fy2014-apc-rounding,source_typo,doc,FY2014_audit,2014,2015,,informational,"Trend table APC 1,038,032 / 806,331 vs the annual pension cost table 1,038,033 / 806,330","$1 each",2026-10-25,audits FY2014 pdf p73, FY2015 pdf p70
EX-adp-fy2012-npo-2010,source_typo,doc,FY2012_audit,2012,2012,,informational,"FY2012 trend table: 2010 NPO (police 1,269,471; fire 1,314,074) is $30,135/$30,136 below NPO 2009 + APC - contributions","as printed",2026-10-25,audit FY2012 pdf p63
```

Additional suggested rows (informational / structural; not tagged in the tables):

```
EX-adp-iepa-partial-disbursement,structural_change,entity,iepa-loans,2021,2025,,informational,"IEPA loans #L17-3623 / #L17-4188 were not fully disbursed: the activity table balance exceeds the 'comprise the following' table (which lists only loan #L17-3622); no payment schedule exists for the difference","Derived gap (29,974; 1,090,091; 3,077,082; 3,600,158; 3,497,370) is added as a derived_gap issue row; FY2024 and FY2025 notes quote the same amounts. FY2022 gap = 29,974 + 1,682,010 draws - 621,893 forgiven.",2026-10-25,debt_issue_terms.csv flag=derived_gap
EX-adp-gasb68-presentation,structural_change,entity,debt-note,2016,2017,,informational,"FY2016 debt table has no pension lines (liability shown on the face statement); FY2017 restates opening balances and adds net pension liability lines","debt chain FY2015->16 and FY2016->17 differ by the pension lines",2026-10-25,audit_debt_pension_crosschecks.csv
EX-adp-gasb75-restatement,structural_change,entity,opeb,2019,2019,,informational,"FY2019 adopted GASB 75: OPEB liability restated from the net OPEB obligation (governmental 3,061,591 -> 6,391,111; library 69,092 -> 150,148)","",2026-10-25,audit FY2019 note 8
EX-adp-imrf-fy2016-col-label,structural_change,doc,FY2016_audit,2016,2016,,informational,"FY2016 IMRF RSI single column is headed 2016 (fiscal year) but holds the December 31, 2015 measurement; the extractor relabels it 2015 so later audits' columns align","",2026-10-25,audit FY2016 RSI p102-104
EX-adp-opeb-fy2015-noo-library,definitional,doc,FY2015_audit,2015,2016,,informational,"FY2015 net OPEB obligation 2,196,448 = city 2,154,179 + library 42,269; the FY2016 audit reports the city portion only","explains the FY2015->FY2016 NOO chain difference and the FY2016 trend table",2026-10-25,audit FY2015 pdf p73
EX-adp-rsi-early-restatements,structural_change,entity,rsi-contributions,2015,2016,,informational,"The first GASB 67/68 RSI columns (2015-2016) for covered payroll, ADC and contributions were restated or re-labelled in the next audit; 37 cells differ","Each audit ties to its own identities",2026-10-25,audit_debt_pension_crosschecks.csv status DIFF-explained
EX-adp-fy2025-iepa-opening,structural_change,doc,FY2025_audit,2025,2025,,informational,"FY2025 opening IEPA balance is $152,924 below FY2024 ending (loan #L17-4188 reported at April 30, 2024 but not disbursed)","",2026-10-25,audit FY2025 vs FY2024 debt note
EX-adp-fy2012-schedule-not-extracted,not_comparable,doc,FY2012_audit,2012,2012,,informational,"FY2012 debt service to maturity (multi-instrument tables with 5-year bands) not extracted","",2026-10-25,this handoff
EX-adp-ocr-fixtok-fy2012,ocr_correction,doc,FY2012_audit,2012,2012,,informational,"3 OCR tokens corrected from the page image / identities (IEPA additions 1,312,357; compensated absences 57,511; firefighters APC 724,988)","overrides/audit_debt_pension_FY2012.csv kind fixtok",2026-10-25,audit FY2012 pdf p52, p63
EX-adp-imrf-amort-sign,source_typo,doc,FY2020_audit,2020,2020,,informational,"IMRF regular amortisation schedule is printed with the opposite sign to net deferred items (positive 1,181,570 for a net inflow)","accepted by the check as a sign convention slip",2026-10-25,audit FY2020 pdf p69
```

## 6. Integration instructions for the lead (not applied)

1. **`govbudget/schema.py`**: add the six table names and column lists (copy `COLS` from `audit_debt_pension_build.py`): `DEBT_BALANCE_COLUMNS, DEBT_ISSUE_COLUMNS, DEBT_SERVICE_COLUMNS, PENSION_ROLLFORWARD_COLUMNS, PENSION_SUMMARY_COLUMNS, OPEB_ROLLFORWARD_COLUMNS`; extend `STATEMENTS`/docs only if desired. `exception_ids` follow the existing semicolon convention.
2. **`pipelines/belvidere_il/build_facts.py`**: these tables are not budget/audit revenue-expense facts, so no `facts.csv` change is needed. Add one call after the audit extracts: `import audit_debt_pension_build; audit_debt_pension_build.main()` (it only writes the eight new files), and add the files to `govbudget.validate` if it checks table headers. If the lead prefers canonical `documents.csv` linkage, doc_id `FY20XX_audit` already matches.
3. **`entities/belvidere_il/`**: append the exception rows in section 5 to `exceptions.csv`; optionally move my `overrides/audit_debt_pension_FY*.csv` `allow` rows' exception ids there (they are the same ids). No account/category mapping is affected (debt and pension lines are not General Fund accounts). Optional new config `debt_lines.csv` mapping `line_id` -> display name / debt vs liability (the `DEBT_TYPES` set in `audit_debt_pension_debt.py` is the current rule).
4. **CLAUDE.md**: paste section 7; add `pdftotext` to the tool list; update "Not done yet" (debt and pension notes are done; remaining: FY2012 schedule, RSI funding progress FY2012-15, fiduciary net position tables).
5. Possible follow-ups: attach the fund-level pension contributions (Police / Fire pension trust funds) to the pension summary; per-capita and inflation views of the NPL.

## 7. Ready-to-paste CLAUDE.md section

```
## Audited long-term debt, pensions and OPEB (done 2026-10-25, audits FY2012-FY2026)
`extract_audit_debt_pension.py FY20XX` reads the Notes to the Financial Statements and RSI of each audit into `data/belvidere_il/extracted/audits/FY20XX_audit_{debt_balances,debt_issues,debt_service,pension_rollforward,pension_summary,opeb_rollforward,debt_pension_checks}.csv`; `--build` consolidates them into `data/belvidere_il/{debt_balances,debt_issue_terms,debt_service_schedule,pension_rollforward,pension_summary,opeb_rollforward}.csv` (entity_id, doc_id, period_fy, ... long format, `exception_ids`) plus `audit_debt_pension_checks.csv` and `audit_debt_pension_crosschecks.csv`. Helpers: `audit_debt_pension_{common,debt,pension,opeb,stmt,old,build}.py`. FY2016-26 are read from the text layer as positioned words (`pdftotext -bbox`, needs poppler); FY2012-15 from tesseract word boxes (their text layers are poor), cached in `data/belvidere_il/ocr/audit_pages/FY20XX_dp_tsv_gray/`.
- **Validation: 0 failed checks in all 15 years** (50-689 checks a year, `audit_debt_pension_checks.csv`): debt lines roll forward and sum to totals, issue tables and debt service schedules tie to the outstanding balances; NPL = TPL - FNP, funded ratio, roll-forward sums, year-to-year chains, contributions, deferred items, membership, sensitivity; OPEB roll-forwards. Independent ties: debt table pension / OPEB lines = the notes; Statement of Net Position (due within / after one year, deferred items by plan); reconciliation of governmental funds (each long-term liability line); governmental funds "Principal" expenditure = debt-note reductions (14 of 15 years to the dollar). Cross-year: 3,905 RSI cells repeated in later audits agree exactly; `audit_debt_pension_crosschecks.csv` explains every difference.
- **Source anomalies are accepted only through `data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv`** (`allow` rows with the exact difference and a reason, `fixtok` OCR fixes): FY2016 police note $1 rounding, FY2020 IMRF NPL net-change sign (printed without parentheses) and OPEB net-change typo (540,000), FY2021 police amortisation sign, FY2026 $1 RSI deficiency, FY2013 police 2011 NPO typo, FY2016 library schedule interest total typo. Exceptions `EX-adp-*`.
- **Layouts**: FY2016-18 OPEB is GASB 45 (net OPEB obligation), FY2019+ GASB 75 (total OPEB liability, restated at adoption); pensions FY2016+ are GASB 68 (RSI up to 10 columns; IMRF measured December 31 of the prior calendar year, police/fire April 30; the FY2016 IMRF RSI is headed by fiscal year and relabelled to the measurement year); FY2012-15 are GASB 27 (ARC, APC, NPO, funded status). The FY2016 debt table has no pension lines; FY2017+ lists NPL and OPEB lines. IEPA loans #L17-3623 / #L17-4188 are drawn but have no payment schedule (FY2021-25): the activity balance exceeds the issue table by a derived gap (matches the note's own amounts in FY2024-25).
- Not done: FY2012 debt service schedule; FY2012-15 Statement of Net Position cross-checks and RSI funding-progress schedules; police/fire plan net position tables; IMRF asset allocation.
```

## 8. Headline findings

- **Net pension liability** of the two public-safety funds (police + fire, April 30 measurement): $25.5M (FY2016) rising to $33.7M (FY2024), then $24.2M at FY2026 after a 23% investment return in FY2026 (IMRF added $3.5M in FY2016 and is a net asset of $2.55M in FY2026). Police NPL $13.4M (FY2016) -> $19.8M (FY2024) -> $13.0M (FY2026); fire $12.1M -> $14.1M (FY2025) -> $11.2M (FY2026).
- **Funded ratios** (FNP / TPL): police 58.2% (FY2016) -> 73.1% (FY2021) -> 59.5% (FY2023) -> 75.6% (FY2026); fire 52.8% -> 68.0% (FY2021) -> 57.6% (FY2023) -> 71.0% (FY2026). IMRF regular was 84% (Dec 2015), over 100% in 2020-21 and 2025 (net pension asset $2.55M), SLEP 80.6% -> 99.0%.
- **City contributions** to the police fund went from 35.9% of covered payroll (FY2016) to 56.0% (FY2026) and fire from 40.6% to 58.3%; IMRF employer rate fell from 14.1% to 5.6%.
- **OPEB**: net OPEB obligation $1.15M (FY2012) -> $3.06M (FY2018, GASB 45); total OPEB liability $8.2M at the FY2019 GASB 75 restatement, peak $11.3M (FY2020), $9.8M (FY2026); library plan $46,526.
- **Borrowed debt** shrank: governmental debt certificates + revolving loan $0.91M (FY2012) -> installment contract $0.19M (FY2026); business-type bonds, IEPA loans and lease $5.9M (FY2012) -> $4.1M IEPA loans (FY2026, all bonds retired by FY2020). Reported governmental long-term liabilities rose from $5.8M (FY2012) to $32.9M (FY2026) almost entirely from pension and OPEB liabilities (GASB 68/75 recognised from FY2016/FY2019).
- The city's own printed arithmetic is clean: 12 small slips found in 15 audits (section 5), the largest a 540,000 OPEB net-change typo.
