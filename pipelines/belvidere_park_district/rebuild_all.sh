#!/bin/sh
# Rebuild every canonical and derived table for the Belvidere Park District from the extracts in data/belvidere_park_district/extracted/.
# (The extractors themselves read the PDFs and are run per document; run those first when a source changes. Order, from the plan in docs/PARK_DISTRICT_PLAN.md:
#   budgets : extract_budget_operating.py FY2019|FY2020|FY2021         (layout A)
#             extract_budget_version.py FY2022 FY2023 FY2023_budget_draft FY2024_budget_draft   (layout B: the adopted FY2023 before its draft)
#             extract_budget_report.py FY2025 FY2026 FY2027 and the drafts FY2025_budget_draft FY2026_budget_draft   (layout D)
#             extract_budget_amended.py FY2023_budget_amended
#   audits  : extract_audit_fund_statements.py FY2019..FY2025, extract_audit_debt.py FY2019..FY2025, extract_audit_imrf.py FY2019 FY2023 FY2024 FY2025
#   config  : build_account_map.py --write adds accounts the extracts have and the account map lacks.)
set -e
cd "$(dirname "$0")/../.."
P=pipelines/belvidere_park_district
python3 $P/build_facts.py                       # documents, accounts, facts, control_totals, corrections, fund_statements, debt/pension/tax tables (validates)
python3 $P/build_audit_compare.py > /dev/null   # audit budget-and-actual vs the budget documents, fund by fund
python3 $P/build_audit_categories.py          # audit expenditure classes vs the budget's object categories
python3 $P/build_debt_crosschecks.py            # debt note, legal margin, IMRF and statements across the seven audits
