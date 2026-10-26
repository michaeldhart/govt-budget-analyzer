"""Headline invariants recorded in CLAUDE.md, asserted on the committed canonical and derived tables.
A failure means either a regression or a deliberate change: in the second case update the number here and in CLAUDE.md."""
import hashlib
import os
from collections import Counter

import pytest

from conftest import ROOT, read


def test_config_and_tables_validate(entity):
    from govbudget.validate import validate_config, validate_tables
    assert validate_config(entity) == []
    assert validate_tables(entity) == []


def test_fy2027_taxes(table):
    (r,) = [x for x in table("tax_series_by_category")
            if x["fiscal_year"] == "2027" and x["measure"] == "budget_adopted" and x["is_rollup"] == "1"]
    assert (int(r["gross_tax"]), int(r["offsets"]), int(r["net"])) == (20998468, -532141, 20466327)


def test_fy2027_tax_lines_file(table):
    rows = table("fy2027_general_fund_tax_lines")
    assert len(rows) >= 31


def test_general_fund_expense_vs_audit(table):
    rows = [r for r in table("audit_expenses_vs_budget") if r["diff"] != ""]
    assert (len(rows), sum(r["diff"] == "0" for r in rows)) == (182, 179)


def test_water_sewer_vs_audit(table):
    rows = [r for r in table("audit_ws_vs_budget") if r["diff"] != ""]
    assert (len(rows), sum(r["diff"] == "0" for r in rows)) == (36, 31)


def test_verified_other_funds_agree(table):
    rows = [r for r in table("other_funds_summary") if r["match"] == "verified"]
    diffs = [int(d) for r in rows for d in (r["revenues_diff"], r["expenditures_diff"]) if d != ""]
    assert diffs and not any(diffs)
    assert sum(1 for r in rows if r["revenues_diff"] != "" or r["expenditures_diff"] != "") == 45


def test_general_fund_statements_vs_schedules(table):
    rows = [r for r in table("fund_balance_summary") if r["fund"] == "General"]
    assert len(rows) == 15
    exact = [r for r in rows if r["revenues_vs_schedule"] == "0" and r["expenditures_vs_schedule"] == "0"]
    assert len(exact) == 14
    for r in rows:  # the one miss is FY2012 cents rounding
        assert abs(int(r["revenues_vs_schedule"])) <= 3 and abs(int(r["expenditures_vs_schedule"])) <= 3


def test_fund_balance_chain(table):
    rows = sorted((r for r in table("fund_balance_summary") if r["fund"] == "General"), key=lambda r: int(r["fiscal_year"]))
    for prev, cur in zip(rows, rows[1:]):
        adj = int(cur["prior_period_adjustment"] or 0)
        assert int(cur["beginning_balance"]) == int(prev["ending_balance"]) + adj, cur["fiscal_year"]


def test_printed_totals_reconciliation(table):
    c = Counter("exact" if r["diff"] == "0" else "1-3" if abs(int(r["diff"])) <= 3 else "more" for r in table("reconciliation"))
    assert (c["exact"], c["1-3"], c["more"]) == (49, 25, 20)
    assert sum(c.values()) == 94


def test_audit_debt_and_pension_checks(table):
    rows = table("audit_debt_pension_checks")
    assert len({r["period_fy"] for r in rows}) == 15
    assert sum(int(r["failed"]) for r in rows) == 0


def test_water_sewer_audit_statements(table):
    assert sum(int(r["problems"]) for r in table("ws_audit_statements_validation")) == 0
    assert sum(int(r["problems"]) for r in table("ws_audit_old_schedules_validation")) == 0


def test_facts_integrity(table, entity):
    from govbudget.schema import BASES, FLOWS, SOURCE_TYPES
    facts = table("facts")
    docs = {r["doc_id"] for r in table("documents")}
    exc = {e["exception_id"] for e in entity.exceptions()}
    assert all(r["basis"] in BASES and r["flow"] in FLOWS and r["source_type"] in SOURCE_TYPES for r in facts)
    assert all(r["doc_id"] in docs for r in facts)
    assert all(x in exc for r in facts for x in r["exception_ids"].split("|") if x)
    assert all(r["amount"] != "" for r in facts)


def test_source_pdfs_match_recorded_hashes(table):
    checked = 0
    for r in table("documents"):
        p = os.path.join(ROOT, "sources", "belvidere_il", r["pdf_path"])
        if r["pdf_path"] and r["sha256"] and os.path.exists(p):
            with open(p, "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == r["sha256"], r["doc_id"]
            checked += 1
    if not checked:
        pytest.skip("source PDFs not present")
