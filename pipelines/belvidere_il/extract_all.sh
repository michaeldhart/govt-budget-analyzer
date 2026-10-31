#!/bin/sh
# One entry point for the Belvidere extractors (roadmap T6): runs every per-year extractor in the documented order, then rebuild_all.sh.
#
#   sh pipelines/belvidere_il/extract_all.sh                 # everything (slow: OCR of ~60 scanned documents; the OCR cache in data/belvidere_il/ocr/ is reused)
#   sh pipelines/belvidere_il/extract_all.sh --dry-run       # print the commands only
#   sh pipelines/belvidere_il/extract_all.sh budgets         # one stage: budgets | audits | ws-audits | debt-pension | reference | build
#   sh pipelines/belvidere_il/extract_all.sh budgets FY2024  # one stage, one year (the stages that read per-year documents)
#
# The extractors write data/belvidere_il/extracted/; with unchanged sources the output equals the committed extracts, so a new document is the usual reason to
# run this (add the year to the lists below and to entity.json "documents"). Needs tesseract, pdftoppm and poppler's pdftotext. Order matters only in that
# the debt/pension and pension-gap builds (`--build`) run after their per-year extracts, and `build` (rebuild_all.sh) runs last.
set -e
cd "$(dirname "$0")/../.."
P=pipelines/belvidere_il
RUN=""
[ "$1" = "--dry-run" ] && RUN="echo" && shift
STAGE="${1:-all}"
ONLY="$2"

years() {  # years FIRST LAST -> FYfirst .. FYlast, or just $ONLY when one year was asked for
  if [ -n "$ONLY" ]; then echo "$ONLY"; return; fi
  y=$1; while [ "$y" -le "$2" ]; do echo "FY$y"; y=$((y + 1)); done
}
each() {  # each SCRIPT FIRST LAST [extra args]
  s=$1; a=$2; b=$3; shift 3
  for fy in $(years "$a" "$b"); do $RUN python3 $P/$s "$fy" "$@"; done
}
want() { [ "$STAGE" = all ] || [ "$STAGE" = "$1" ]; }

if want budgets; then
  each extract_budget.py 2015 2027                     # General Fund revenue and expenses, totals
  each extract_budget_ws.py 2015 2027                  # Water / Sewer fund 61
  each extract_budget_ws_subledgers.py 2015 2027       # Water / Sewer cash sub-ledgers
  each extract_budget_funds.py 2015 2027               # other budgeted funds
  each budget_funds_library.py 2020 2027               # Ida Public Library budget
  each extract_salaries.py 2015 2027                   # Exhibit B salary schedule
fi
if want audits; then
  each extract_audit.py 2012 2026                      # General Fund revenue schedule
  each extract_audit_expenditures.py 2012 2026         # General Fund expenditure schedule
  each extract_audit_fund_statements.py 2012 2026      # governmental fund statements
  each extract_audit_other_funds.py 2012 2026          # other-fund schedules and combining statements
fi
if want ws-audits; then
  each extract_audit_expenditures.py 2016 2026 --schedule ws   # Water / Sewer operating expenses
  each extract_audit_ws_summary.py 2016 2026
  each extract_audit_ws_old.py 2012 2015               # FY2012-15 schedules
  each extract_audit_ws_statements.py 2012 2026
  $RUN python3 $P/audit_ws_build_statements.py
fi
if want debt-pension; then
  each extract_audit_debt_pension.py 2012 2026
  [ -n "$ONLY" ] || $RUN python3 $P/extract_audit_debt_pension.py --build
  each audit_pension_gaps.py 2012 2026
  [ -n "$ONLY" ] || $RUN python3 $P/audit_pension_gaps.py --build
fi
if want reference; then
  $RUN python3 $P/extract_audit_eav.py                 # assessed valuation (the CPI and population snapshots come from fetch_reference.py, network)
fi
if want build; then
  $RUN sh $P/rebuild_all.sh
fi
