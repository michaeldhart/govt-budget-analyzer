"""Structural invariants on the canonical tables the site will read: schema snapshot, keys, references, ranges."""
import csv
import os
from collections import Counter

import pytest

from conftest import ROOT, read
from govbudget import schema

CANONICAL = {
    "facts": schema.FACT_COLUMNS,
    "documents": schema.DOCUMENT_COLUMNS,
    "accounts": schema.ACCOUNT_COLUMNS,
    "control_totals": schema.CONTROL_TOTAL_COLUMNS,
    "fund_statements": schema.FUND_STATEMENT_COLUMNS,
    **schema.DEBT_PENSION_TABLES,
}
# Derived tables the site reads: the columns it may rely on (new columns may be added, these must not vanish or be renamed).
DERIVED = {
    "tax_series_by_category": ["entity_id", "fiscal_year", "measure", "basis", "category", "is_rollup", "gross_tax", "offsets", "net"],
    "tax_series_audit_basis": None,
    "audit_expenses_vs_budget": ["fiscal_year", "department_group", "audit_original", "audit_final", "audit_actual", "budget_adjusted", "diff"],
    "audit_ws_vs_budget": ["fiscal_year", "department", "audit_original", "audit_final", "audit_actual", "budget_adjusted", "diff"],
    "fund_balance_summary": ["fiscal_year", "fund", "cash", "total_fund_balance", "unassigned", "revenues", "expenditures", "net_change",
                             "beginning_balance", "ending_balance", "unassigned_months"],
    "other_funds_summary": ["fiscal_year", "fund_code", "fund", "revenues_actual", "expenditures_actual", "ending_balance", "match"],
    "ws_summary": None,
    "reconciliation": ["doc_id", "period_fy", "basis", "kind", "printed", "lines", "diff"],
}
FY_RANGE = range(2011, 2028)  # FY2011 appears only as a prior-year comparative


def header(name, data_dir):
    with open(os.path.join(data_dir, name + ".csv"), newline="") as f:
        return next(csv.reader(f))


@pytest.mark.parametrize("name", sorted(CANONICAL))
def test_canonical_columns_match_schema(name, data_dir):
    assert header(name, data_dir) == CANONICAL[name]


@pytest.mark.parametrize("name", sorted(DERIVED))
def test_derived_columns_present(name, data_dir):
    cols = header(name, data_dir)
    assert len(cols) == len(set(cols)), "duplicate column names"
    for c in DERIVED[name] or []:
        assert c in cols, f"{name}.csv lost column {c!r}"


def test_facts_row_keys_unique(table):
    # row_seq numbers a source line; a line yields one fact per period and basis (budget, final budget, actual, prior year ...)
    keys = Counter((r["doc_id"], r["row_seq"], r["period_fy"], r["basis"]) for r in table("facts"))
    dup = [k for k, n in keys.items() if n > 1][:5]
    assert not dup, f"duplicate (doc_id, row_seq, period_fy, basis): {dup}"


def test_facts_values(table):
    facts = table("facts")
    fy = {int(r["period_fy"]) for r in facts}
    assert fy <= set(FY_RANGE) and {2012, 2027} <= fy
    bad = [r for r in facts if not r["amount"].lstrip("-").isdigit()]
    assert not bad, f"non-integer amounts, e.g. {bad[:2]}"
    assert all(r["account_key"] for r in facts if r["source_type"] == "budget")


def test_facts_budget_accounts_in_account_map(table, entity):
    amap = {(flow, key) for flow, key in entity.account_map()}
    missing = {(r["flow"], r["account_key"]) for r in table("facts")
               if r["source_type"] == "budget" and (r["flow"], r["account_key"]) not in amap}
    assert not missing, f"{len(missing)} budget accounts not in account_map.csv, e.g. {sorted(missing)[:5]}"


def test_accounts_table_agrees_with_facts(table):
    accounts = {(r["flow"], r["account_key"]): r for r in table("accounts")}
    assert len(accounts) == len(table("accounts")), "duplicate accounts"
    adopted = {}  # first_fy / last_fy span the account's ADOPTED budget years (blank if it only appears in prior-year columns)
    for r in table("facts"):
        if r["source_type"] == "budget" and r["basis"] == "adopted":
            adopted.setdefault((r["flow"], r["account_key"]), set()).add(int(r["period_fy"]))
    for k, years in adopted.items():
        a = accounts.get(k)
        assert a, f"{k} in facts but not accounts.csv"
        assert (int(a["first_fy"]), int(a["last_fy"])) == (min(years), max(years)), k
    for k, a in accounts.items():
        assert bool(a["first_fy"]) == (k in adopted), k


def test_documents_unique_and_complete(table):
    docs = table("documents")
    ids = [r["doc_id"] for r in docs]
    assert len(ids) == len(set(ids))
    assert Counter(r["doc_type"] for r in docs)["audit"] == 15
    assert all(r["sha256"] for r in docs if r["pdf_path"])
    used = {r["doc_id"] for r in table("facts")} | {r["doc_id"] for r in table("fund_statements")} | {r["doc_id"] for r in table("control_totals")}
    assert used <= set(ids)


def test_control_totals_and_statements_vocabulary(table):
    assert all(r["kind"] in schema.TOTAL_KINDS and r["basis"] in schema.BASES for r in table("control_totals"))
    assert all(r["statement"] in schema.STATEMENTS for r in table("fund_statements"))
    assert all(int(r["period_fy"]) in FY_RANGE for r in table("fund_statements"))


@pytest.mark.parametrize("name", sorted(schema.DEBT_PENSION_TABLES))
def test_debt_pension_tables_cover_all_audits(name, table):
    rows = table(name)
    assert rows, name
    if name != "debt_service_schedule":  # FY2012's schedule is not extracted (CLAUDE.md)
        assert {r["period_fy"] for r in rows} >= {str(y) for y in range(2013, 2027)}


def test_exception_ids_resolve_everywhere(table, entity):
    ids = {e["exception_id"] for e in entity.exceptions()}
    assert len(ids) == len(entity.exceptions()), "duplicate exception_id"
    for name in schema.DEBT_PENSION_TABLES:
        for r in table(name):
            for x in r["exception_ids"].split("|"):
                assert not x or x in ids, f"{name}: unknown exception {x}"
    for e in entity.exceptions():
        assert e["kind"] in schema.EXCEPTION_KINDS and e["scope"] in schema.EXCEPTION_SCOPES and e["effect"] in schema.EXCEPTION_EFFECTS


def test_rollup_rows_are_only_all_taxes(table):
    rows = table("tax_series_by_category")
    assert {r["category"] for r in rows if r["is_rollup"] == "1"} == {"ALL TAXES"}
    assert all(r["is_rollup"] == "1" for r in rows if r["category"] == "ALL TAXES")


def test_category_rows_sum_to_rollup(table):
    rows = table("tax_series_by_category")
    roll = {(r["fiscal_year"], r["measure"]): r for r in rows if r["is_rollup"] == "1"}
    parts = {}
    for r in rows:
        if r["is_rollup"] != "1":
            p = parts.setdefault((r["fiscal_year"], r["measure"]), [0, 0, 0])
            p[0] += int(r["gross_tax"]); p[1] += int(r["offsets"]); p[2] += int(r["net"])
    assert parts.keys() == roll.keys()
    for k, p in parts.items():
        assert p == [int(roll[k]["gross_tax"]), int(roll[k]["offsets"]), int(roll[k]["net"])], k


def test_tax_series_net_is_gross_plus_offsets(table):
    for r in table("tax_series_by_category"):
        assert int(r["gross_tax"]) + int(r["offsets"]) == int(r["net"]), r


def test_one_series_value_per_year_measure_category(table):
    keys = Counter((r["fiscal_year"], r["measure"], r["category"]) for r in table("tax_series_by_category"))
    assert max(keys.values()) == 1


def test_fund_summary_identities(table):
    off = []
    for r in table("fund_balance_summary"):
        if r["revenues"] and r["expenditures"]:
            d = int(r["revenues"]) - int(r["expenditures"]) - int(r["excess_revenues_over_expenditures"])
            if d:
                off.append((r["fiscal_year"], r["fund"], d))
    assert off == [("2012", "Total governmental funds", -1)], off  # the FY2012 audit's own $1 rounding
    for r in table("fund_balance_summary"):
        if r["beginning_balance"] and r["net_change"] and r["ending_balance"]:
            assert int(r["beginning_balance"]) + int(r["net_change"]) == int(r["ending_balance"]), (r["fund"], r["fiscal_year"])
