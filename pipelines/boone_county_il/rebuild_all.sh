#!/bin/sh
# Rebuild Boone County's canonical tables.  Usage: sh pipelines/boone_county_il/rebuild_all.sh [extract|build]
# extract: re-read the PDFs in sources/boone_county_il (needs poppler pdftotext and the downloaded PDFs: fetch_sources.py)
# build  : mappings and canonical tables from the committed extracts (default)
set -e
cd "$(dirname "$0")/../.."
stage="${1:-build}"
if [ "$stage" = "extract" ]; then
  python3 pipelines/boone_county_il/extract_budget_worksheet.py FY2025_budget FY2026_budget FY2027_budget_prelim
  for y in 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025; do
    python3 pipelines/boone_county_il/extract_audit_fund_statements.py FY$y
    python3 pipelines/boone_county_il/extract_audit_gf_schedules.py FY$y
    python3 pipelines/boone_county_il/extract_audit_fund_schedules.py FY$y
  done
fi
python3 pipelines/boone_county_il/build_account_map.py --write
python3 pipelines/boone_county_il/build_audit_fund_names.py
python3 pipelines/boone_county_il/build_facts.py
