#!/bin/sh
# Rebuild every canonical and derived table for Belvidere from the per-year extracts in data/belvidere_il/extracted/.
# (The extractors themselves, which read the PDFs, are run per year: see CLAUDE.md. Run those first when a source changes.)
set -e
cd "$(dirname "$0")/../.."
P=pipelines/belvidere_il
python3 $P/build_facts.py                      # documents, accounts, facts, control_totals, fund_statements (validates config and tables)
python3 $P/build_tax_series.py > /dev/null     # tax series (General Fund revenue)
python3 $P/build_audit_compare.py > /dev/null  # audit vs budget: revenue groups
python3 $P/build_audit_expense_compare.py      # audit vs budget: General Fund expenses
python3 $P/build_audit_ws_compare.py           # audit vs budget: Water / Sewer operating expenses
python3 $P/build_fund_summary.py | head -2     # fund balance summary
python3 $P/build_ws_summary.py | head -1       # Water / Sewer summary
python3 $P/build_other_funds_summary.py        # other funds: audit schedules beside the budget
python3 $P/build_reference.py                 # population, CPI and assessed valuation by fiscal year (from the committed snapshots and extracts)
python3 -m govbudget.reconcile belvidere_il | head -1
# reference snapshots (CPI, population): python3 $P/fetch_reference.py (network); assessed valuation extracts: python3 $P/extract_audit_eav.py
# debt, pension and OPEB tables are built from their own per-year extracts:
#   for y in 2012 .. 2026: python3 $P/extract_audit_debt_pension.py FY$y ; then python3 $P/extract_audit_debt_pension.py --build
#   for the Water / Sewer statements: python3 $P/audit_ws_build_statements.py
