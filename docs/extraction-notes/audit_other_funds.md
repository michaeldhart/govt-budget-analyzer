> Status: integrated into the canonical tables on 2026-10-25 (see CLAUDE.md). The 'integration' / 'lead applies' sections below are historical. Written by the extraction agent.

# Audit fund-level detail for the non-General governmental funds (FY2012-FY2026)

Status: all 15 audits extracted, **0 validation problems in every year**, 772 independent tie-out comparisons, 0 differences.
Nothing existing was modified (no git, no build_facts.py).

## Files written
Scripts (pipelines/belvidere_il/): `extract_audit_other_funds.py FY20XX [--source auto|text|gray]` (driver, locator, parsers, validators),
`audit_other_funds_reader.py` (positional word reader: text layer or tesseract TSV), `audit_other_funds_tieout.py` (independent tie-outs).
Data (data/belvidere_il/extracted/audits/): `FY20XX_audit_fund_schedules.csv` (852 rows total incl. headers), `FY20XX_audit_fund_combining.csv` (3,987 rows),
`audit_other_funds_tieout.csv` (772 comparisons). Overrides (data/belvidere_il/overrides/): `audit_other_funds_FY2012.csv`, `_FY2014.csv`, `_FY2015.csv`.

## Schemas
**FY20XX_audit_fund_schedules.csv**: `fiscal_year, doc, pdf_page, fund, section, subsection, line_item, line_kind, original_budget, final_budget, actual, variance, prior_year_actual, flag`.
One row per line of each fund's "Schedule of Revenues, Expenditures and Changes in Fund Balance - Budget and Actual". `fund` is canonical (Motor Fuel Tax, Kishwaukee TIF, Kishwaukee TIF #2, 1990 TIF, 1993 TIF, Farmington SSA #2, Farmington SSA #3, Capital Projects). `section` = Revenues / Expenditures / Other Financing / Fund Balance; `subsection` = heading path (" > "). `line_kind` in revenue, expenditure, other_financing, subtotal, total_revenues, total_expenditures, excess, total_ofs, net_change, fb_begin, fb_adjust, fb_restated, fb_end. Budget cells are blank on fund-balance rows (actual only). `variance` and `prior_year_actual` (FY2011 actual) are filled only in FY2012 (that audit prints five columns); blank otherwise. Amounts are whole dollars, negatives negative.
**FY20XX_audit_fund_combining.csv**: `fiscal_year, doc, pdf_page, statement (combining_balance_sheet | combining_changes_in_fund_balance), section, subsection, line_item, fund, amount, flag` - same layout as fund_statements.csv, one row per line x fund column; `fund` includes `Total` (the nonmajor total column). Columns exist for Motor Fuel Tax (nonmajor except FY2023), Kishwaukee TIF, Kishwaukee TIF #2, 1990/1993 TIF, Farmington SSA #2/#3, Foreign Fire Insurance, Police Restricted (FY2023+), and Capital Projects (nonmajor in FY2016 only). Funds with a schedule in a year: see below; Kishwaukee TIF #2, Foreign Fire Insurance, Police Restricted, SSA #3 pre-FY2017 and 1993 TIF from FY2017 have combining columns but no budget schedule (not budgeted).
Flags: `label_added_unlabeled_in_source` (FY2013-15 print some subtotals with no label; label supplied from context), `blank_cell_as_zero`.

## Method
Positional reading: every number is assigned to a column by its right edge, so blank cells stay blank (the text layer's junk space glyph that splits "6 83,730" is removed first). FY2016-26 use the text layer (OCR cross-read: FY2016 OCR also 0 problems; other OCR runs show scan-noise problems, the text layer wins); FY2012-15 use 300 dpi grey tesseract TSV (FY2012's combining pages are sideways: rotated by tesseract OSD). Combining statements are 2 pages (labels left, remaining columns right, matched by vertical offset) or 1 rotated page (FY2012).
Validation (per schedule): revenue items = Total revenues; expenditure items = Total expenditures; printed subtotals = items above; excess = revenues - expenditures; other financing items = total; net change = excess + other financing; ending = beginning (+ adjustment/restatement) + net change; FY2012 repeats these on the variance and prior-year columns (variance sign convention: expenditure variances are favourable-positive). Combining: totals = items, balance sheet balances, Total column = sum of fund columns on every line, excess/net change/ending identities (reuses `extract_audit_fund_statements.validate`). FY2024-26 print no "excess" line in the combining statement: derived internally, not output.
Column header names are checked (count and uniqueness) against the value columns.

## Per-year result (all 0 problems)
FY2012-15 OCR (gray); FY2016-26 text layer. Schedules per year: FY2012-14 MFT, CP, 1990 TIF, Kishwaukee TIF, TIF #2, 1993 TIF, Farmington; FY2015 same without 1990 TIF; FY2016 MFT, CP, Kish TIF, 1993 TIF, Farmington #2; FY2017-26 MFT, CP, Kish TIF, Farmington #2 and #3. Note FY2013-15 Motor Fuel Tax has a single expenditure item and no printed total (checked through the net change identity).
## Tie-outs (audit_other_funds_tieout.csv)
- T1 combining Total column == fund_statements.csv Nonmajor column, every mapped line (cash, totals, revenues, expenditures, net change, beginning/ending...): 209 ok, 0 diff.
- T2 each schedule == its column in the combining statements (revenues, expenditures, net change, beginning, ending): 342 ok, 1 skip (FY2013 1993 TIF net change blank in one source).
- T3 Capital Projects / Motor Fuel Tax schedules == their own fund_statements.csv columns in the years they are major funds: 50 ok.
- T4 fund-balance chain ending(Y-1) == beginning(Y): schedules 74 ok / 4 skips (fund opens or closes: 1990 TIF FY2015, 1993 TIF FY2017, SSA #3 FY2017, TIF #2 FY2016 not scheduled); combining 92 ok.
- Budget-ordinance comparison not done (the other agent's FY20XX_budget_fund*.csv extracts were not present/used).

## Anomalies and overrides (suggested exceptions.csv rows below)
1. FY2012 Motor Fuel Tax schedule: the city's printed variance on the net change line (33,063) = revenue variance minus expenditure variance; all other columns tie (allow_diff override).
2. FY2012 OCR fixes from page images: 1990 TIF Contractual services final and prior-year 7,500 (OCR 7,900); Kishwaukee TIF Total revenues actual 152,022 (OCR 452,022); FY2012 combining column headers set by `columns` override (wrapped scan headers).
3. FY2014: MFT beginning balance 570,361 (OCR 70,361; equals FY2013 ending); combining balance sheet Kishwaukee TIF #2 Other Taxes 0 (junk digit).
4. FY2015: MFT Street Maintenance final 696,081 (OCR 69608); combining changes MFT Interest and Fiscal Charges 0 (stray 5).
5. FY2013-15 print an unlabeled "Total liabilities" (combining BS) and FY2012 Capital Projects an unlabeled capital-outlay subtotal; labels supplied from context.
6. FY2012-15 print one unnumbered "Farmington" fund; mapped to Farmington SSA #2 (FY2015 ending 28,086 = FY2016 SSA #2 beginning).
7. Fund-list changes: 1990 TIF FY2012-14 only; 1993 TIF scheduled FY2012-16 then combining only; SSA #3 from FY2017; Police Restricted from FY2023; MFT is a major fund (own column in fund_statements) only in FY2023 and Capital Projects major FY2017+/(nonmajor FY2016); Kishwaukee TIF FY2020 expenditure 448,443 is a large one-off.

Suggested exceptions.csv rows (exception_id,kind,scope,scope_id,fy_from,fy_to,bases,effect,summary,detail,decided_on,evidence):
```
EX-fundsched-fy2012-mft-variance,source_typo,document,FY2012_audit,2012,2012,,informational,"MFT schedule variance on the net change line (33,063) is not additive","Printed as revenue variance minus expenditure variance; actual column ties",,audit FY2012 page 85
EX-fundsched-farmington-name,definitional,entity,belvidere_il,2012,2015,,informational,"Audits FY2012-15 print one unnumbered Farmington fund; treated as Farmington SSA #2","FY2015 ending 28,086 equals FY2016 SSA #2 beginning",,audits FY2015/FY2016
EX-fundsched-unlabeled-subtotals,source_typo,entity,belvidere_il,2012,2015,,informational,"Unlabeled subtotals in combining balance sheets and the FY2012 Capital Projects schedule","Labels supplied from context (flag label_added_unlabeled_in_source)",,page images
EX-fundsched-mft-no-total,structural_change,account,Motor Fuel Tax,2013,2015,,informational,"MFT schedules print no Total expenditures (single item)","Checked through the net change identity",,audits FY2013-15
EX-fundsched-ocr-fixes,ocr_correction,entity,belvidere_il,2012,2015,,adjusted,"Six eyeballed OCR cell corrections","See data/belvidere_il/overrides/audit_other_funds_FY2012/14/15.csv",,page images
```

## Integration needed (not applied)
- `build_facts.py`: read `FY20XX_audit_fund_schedules.csv` as facts (new `source_type=audit`, `fund_code` by fund; basis audit_original/audit_final/actual_audited from the three amount columns; `audit_section` = Section | Subsection) and `FY20XX_audit_fund_combining.csv` into `fund_statements.csv` with new statements `combining_balance_sheet` / `combining_changes_in_fund_balance` (fund = the column).
- `entities/belvidere_il/`: add a funds table (code, canonical name, years) and extend `fund_statement_lines.csv` patterns for `combining_*` statements (same patterns apply); `departments.csv`/`account_map.csv` are not involved (audit lines have no account numbers).
- `govbudget/schema.py`: add the two statement names to the fund-statement vocabulary and `variance`/`prior_year_actual` is FY2012-only (can be dropped); `govbudget.validate` should accept fund names above.
- Overrides use new kinds (`cell`, `allow_diff`, `drop_line`, `label`, `columns`) in their own files only.

## CLAUDE.md section (paste)
```
## Audited other-fund detail (done 2026-10-25, governmental funds other than General, audits FY2012-FY2026)
`extract_audit_other_funds.py FY20XX` reads each nonmajor/special fund's "Schedule of Revenues, Expenditures and Changes in Fund Balance - Budget and Actual" (Motor Fuel Tax, Capital Projects, Kishwaukee TIF, 1990/1993 TIF, Farmington SSA #2/#3; original, final, actual) into `data/belvidere_il/extracted/audits/FY20XX_audit_fund_schedules.csv`, and the Combining Balance Sheet and Combining Statement of Revenues, Expenditures and Changes in Fund Balances of the nonmajor funds into `FY20XX_audit_fund_combining.csv` (one row per line x fund column + Total). Reading is positional (`audit_other_funds_reader.py`): numbers are assigned to columns by right edge; text layer FY2016-26, tesseract TSV FY2012-15 (FY2012 combining pages are sideways). All 15 years pass with 0 problems (items = totals, excess, net change, ending = beginning + change, Total column = sum of funds). `audit_other_funds_tieout.py` ties them independently to fund_statements.csv (combining Total = Nonmajor column; CP/MFT schedules = their major-fund columns; schedules = combining columns; fund-balance chain year to year): 772 comparisons, 0 differences. Overrides: `data/belvidere_il/overrides/audit_other_funds_FY20XX.csv` (cell, allow_diff, columns, drop_line, label) - FY2012, FY2014, FY2015 only (6 OCR cell fixes from page images, 1 source variance typo, FY2012 column headers).
```

## Headlines
- Motor Fuel Tax fund balance (April 30): $0.29M (FY2012) -> $0.90M (FY2016) -> $1.07M (FY2017), drawn to $0.40M (FY2018, $1.36M spending), rebuilt to $2.47M (FY2022, revenue $1.6-1.8M in FY2021-22), then $0.86M (FY2023 after $2.95M spending) and $1.22M (FY2026). Revenue grew from $0.7M to $1.2M a year.
- Capital Projects fund balance: $0.15M (FY2012) -> $1.25M (FY2016) -> $1.59M (FY2020) -> negative $0.04M (FY2022) and $0.09M (FY2025), $0.40M (FY2026); spending $3.2-3.6M a year in FY2023-26 vs under $0.7M before FY2017, funded mostly by transfers in from the General Fund.
- Kishwaukee TIF: balance $0.21M (FY2012) -> $0.44M (FY2019), drops to $0.01M in FY2020 (one-off $448K expenditure), then rebuilds slowly to $0.11M (FY2026); revenue is only about $18-27K a year of property tax in recent years.
- Farmington SSA #2/#3, Foreign Fire Insurance, Police Restricted are small (tens of thousands to a few hundred thousand dollars).
