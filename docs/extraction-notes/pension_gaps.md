# D3: gaps closed in the debt / pension / OPEB extraction (2026-10-31)

Code: `pipelines/belvidere_il/audit_pension_gaps.py` (readers and validators, per year: `python3 pipelines/belvidere_il/audit_pension_gaps.py FY2020`), `audit_pension_gaps_build.py` (consolidation, cross-year checks; also run by `extract_audit_debt_pension.py --build`), FY2012 debt service in `audit_debt_pension_old.run_year` via `audit_pension_gaps.debt_service_2012`. Overrides: `data/belvidere_il/overrides/audit_pension_gaps_FY20XX.csv` (kinds `allow` = a difference the source itself contains, `fixtok` = OCR token fix from the page image; both with a reason) and, for the FY2012 schedule, `audit_debt_pension_FY2012.csv` (`fixtok`, `fixline`, `allow`). Needs the OCR word-box cache of FY2012-15 (built on demand into `data/belvidere_il/ocr/audit_pages/FY20XX_dp_tsv_gray/`, all pages) and poppler.

## What each source prints (and what it does not)

| Item | Where | Result |
|---|---|---|
| Police / fire plan net position and changes | combining statements of the pension trust funds (every audit, FY2012 "net assets"), the pension note tables `a. Plan Net Position` / `b. Changes` (FY2016-26 only), the basic fiduciary statement (pension trust column) | read from all three; 15 audits x police / fire / total, `pension_plan_net_position.csv` (2,526 rows) |
| IMRF target allocation | IMRF note, "Long-Term Expected Rate of Return" table | printed in FY2025 and FY2026 only; earlier audits give the rate and refer to IMRF's report |
| Police / fire target allocation | investment policy paragraph of each plan note | printed FY2016-FY2021 (both plans) and FY2022 (police); from FY2022 (fire) / FY2023 (police) the funds sit in the Illinois pooled funds and the audit points to a website; FY2012-15 print the investment policy in words only |
| RSI funding progress and employer contributions (GASB 27 era) | FY2012: three valuation years per plan, no contribution schedule; FY2013-15: six years of both for IMRF, SLEP, police, fire, OPEB | `pension_funding_progress.csv` (91 rows), `pension_employer_contributions.csv` (89 rows); GASB 67/68 audits (FY2016+) print their RSI in `pension_summary` / `pension_rollforward` already |
| FY2012 debt service to maturity | note 5, page 54, three tables with five-year bands | 64 rows in `debt_service_schedule.csv` (annual rows 2013-2017 `year`, bands `band`, plus TOTAL) |

## Validation (all 15 audits, 0 failed)

- Every print: total column = police + fire on every line; assets = total assets, liabilities, net position = assets - liabilities; contributions, investment income, net investment income, additions, deductions, net change, beginning + change = ending, for police, fire and total.
- Two prints of the same table (combining statement vs note, FY2016-26) agree cell by cell; the basic fiduciary statement's pension column equals the combining total column; ending net position (changes) = net position; the pension note's fiduciary net position = the combining statement (police and fire); employer contributions = the RSI / trend table (FY2012-26).
- Cross-year: beginning net position = previous ending (42 chains); every funding-progress and contribution row repeated by later audits agrees (432 rows).
- Funding progress: ratio = assets / AAL, UAAL = AAL - assets, UAAL / payroll, contributions / required; latest valuation = the note's funded status; contributions = the trend table.
- FY2012-15 Statement of Net Position (current portion, total noncurrent, each noncurrent line = debt table ending - current portion), reconciliation of governmental funds (each long-term liability line = debt table ending), debt table pension obligation = the police + fire note, OPEB = the OPEB note (`snp|` checks).
- FY2012 schedule: years sum to the printed TOTAL in every column, governmental total principal = its principal columns, business-type total payments = principal + interest of all six instruments, each instrument's principal total = a balance in the liability table.

Counts: `audit_pension_gaps_checks.csv` (3,103 passed, 6 accepted, 0 failed), `audit_pension_gaps_crosschecks.csv` (474 pass, 4 n/a); FY2012's own debt/pension checks went from 66 to 87.

## Source slips accepted (exceptions in `entities/belvidere_il/exceptions.csv`)

$1: FY2015 fire contributions (786,258 vs 786,259), FY2016 police RSI contribution (1,079,608 vs 1,079,609), FY2017 police fiduciary net position in the note (19,976,484 vs 19,976,483), FY2012 net pension obligation (3,332,770 vs 3,332,769); `EX-adp-plan-npos-rounding`, `EX-adp-fy2012-npo-rounding`. $1,300: FY2013 firefighters' FY2011 contribution in the RSI (427,000) vs the trend table (425,700), `EX-adp-fire-contrib-2011`. FY2012 debt service: the 2016 total principal omits the revolving loan fund's 8,750 (and so does the printed TOTAL row, 1,226,372), the IEPA loan principal is $2 under its balance (`EX-adp-fy2012-sched-2016-total`, `EX-adp-fy2012-iepa-sched-2`).

## OCR repairs (each makes a printed total or identity tie; verified on the page image)

FY2012 police State and local obligations 724,234 (read 124,234, page 91); FY2012 schedule: capital lease 2015 principal 119,760 (read 419,760), 2014 total payments 1,246,940 (read 4,246,940), the 2023-2032 row (dropped dash, split 46,643) (page 54); FY2014 SLEP 2012 AAL 424,583 (read 424.583; generic rule: `ddd.ddd` in a dollar column is a thousands comma, flag `ocr_point_comma`, `EX-adp-funding-ocr-point`). Other OCR noise is cleaned by rule (a lone "(" before a number, "=" or "~" for a dash, trailing marks, a number split at a space when there are too many numbers), always followed by the identities above. `EX-adp-fy2012-ocr-gaps`.

## Conventions

- Investment expense is stored negative; FY2012 prints it positive (`EX-adp-fy2012-expense-sign`, flag `sign_normalized`).
- UAAL (`unfunded_liability`) is positive when the plan is underfunded (as the RSI prints it; the note's funded-status table prints the same as a negative in parentheses and `pension_summary.uaal_signed` keeps that sign).
- FY2012-15 OPEB valuations exist every third year (N/A rows left out); the FY2012 OPEB covered payroll is not printed (blank, flag `payroll_not_printed`).
- Not done, because the audits do not print it: IMRF allocation FY2016-24, police / fire allocation after FY2022, any allocation before FY2016 (`EX-adp-allocation-not-printed`).
