"""The site's data files (govbudget.export_site): shapes, identities and references."""
import gzip
import json
import os
import re

import pytest

from conftest import ENTITY, ROOT
from govbudget import export_site, schema

DATASET_COLUMNS = {
    "taxes_by_category": ["fiscal_year", "measure", "basis", "category", "gross_tax", "offsets", "net", "exception_ids"],
    "taxes_audit_basis": ["fiscal_year", "group", "budget_audit_original", "budget_audit_final", "actual_audited",
                          "budget_docs_adopted", "actual_budget_doc", "note", "exception_ids"],
    "taxes_budget_vs_actual": ["fiscal_year", "group", "is_total", "budget", "actual", "variance_pct", "exception_ids"],
    "expenses_by_dept_object": ["fiscal_year", "dept_code", "department", "function", "object_id", "object", "amount", "exception_ids"],
    "expenses_fund_totals": ["fiscal_year", "lines_total", "printed_total", "diff", "page_label", "exception_ids"],
    "expenses_audit_vs_budget": ["fiscal_year", "department_group", "label", "function", "is_total", "audit_original", "audit_final",
                                 "audit_actual", "variance_pct", "budget_adjusted", "diff", "note", "exception_ids"],
    "fund_balance": ["fiscal_year", "fund", "cash", "total_assets", "total_liabilities", "total_deferred_inflows", "nonspendable", "restricted",
                     "committed", "assigned", "unassigned", "total_fund_balance", "revenues", "expenditures",
                     "excess_revenues_over_expenditures", "other_financing", "net_change", "beginning_balance", "prior_period_adjustment",
                     "ending_balance", "unassigned_months", "exception_ids"],
    "tax_lines": ["fiscal_year", "measure", "basis", "account", "department", "line_item", "amount", "category", "treatment",
                  "source_doc", "source_column", "page_label", "pdf_page", "source_url", "note", "exception_ids", "corrections"],
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    export_site.export([ENTITY], str(out))

    def load(*parts):
        with open(os.path.join(out, *parts)) as f:
            return json.load(f)
    load.out = str(out)
    return load


@pytest.mark.parametrize("name", sorted(DATASET_COLUMNS))
def test_dataset_envelope_and_columns(site, name):
    d = site(ENTITY, name + ".json")
    assert d["dataset"] == name and d["entity_id"] == ENTITY
    assert d["title"] and d["description"] and d["sources"]
    assert list(d["columns"]) == DATASET_COLUMNS[name]  # the documented columns are exactly the row keys, in order
    assert d["rows"] and all(list(r) == DATASET_COLUMNS[name] for r in d["rows"])


def test_index_meta_and_palette(site):
    idx = site("index.json")["entities"]
    assert [e["entity_id"] for e in idx] == [ENTITY] and set(idx[0]["datasets"]) == set(DATASET_COLUMNS)
    meta = site(ENTITY, "meta.json")
    assert meta["fy_start_month"] == 5 and set(meta["bases"]) == set(schema.BASES) and meta["datasets"] == idx[0]["datasets"]
    pal = site("palette.json")
    assert pal and all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in pal.values())


def test_categories_json_matches_config(site, entity):
    c = site(ENTITY, "categories.json")["schemes"]
    assert set(c) == {r["scheme"] for r in entity.categories()}
    for scheme, rows in c.items():
        assert [r["sort"] for r in rows] == sorted(r["sort"] for r in rows)
        assert all(r["color"] is None or re.fullmatch(r"#[0-9a-f]{6}", r["color"]) for r in rows)
    assert [r["color"] for r in c["budget_category"][:2]] == ["#2a78d6", "#eb6834"]


def test_fy2027_adopted_taxes(site):
    rows = [r for r in site(ENTITY, "taxes_by_category.json")["rows"] if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted"]
    assert not any(r["category"] == "ALL TAXES" for r in rows)  # rollup rows are not exported
    assert (sum(r["gross_tax"] for r in rows), sum(r["offsets"] for r in rows), sum(r["net"] for r in rows)) == (
        20998468, -532141, 20466327)
    lines = [r for r in site(ENTITY, "tax_lines.json")["rows"] if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted"]
    assert sum(r["amount"] for r in lines if r["treatment"] == "tax") == 20998468
    assert sum(r["amount"] for r in lines if r["treatment"] == "offset") == -532141


def test_category_names_exist_in_categories_json(site):
    names = {r["name"] for r in site(ENTITY, "categories.json")["schemes"]["budget_category"]}
    assert {r["category"] for r in site(ENTITY, "taxes_by_category.json")["rows"]} <= names
    groups = {r["name"] for r in site(ENTITY, "categories.json")["schemes"]["report_group"]}
    assert {r["group"] for r in site(ENTITY, "taxes_audit_basis.json")["rows"]} <= groups


def test_exception_ids_resolve_and_are_attached(site):
    known = {e["exception_id"] for e in site(ENTITY, "exceptions.json")["exceptions"]}
    used = set()
    for name in DATASET_COLUMNS:
        for r in site(ENTITY, name + ".json")["rows"]:
            if "exception_ids" in r:
                assert isinstance(r["exception_ids"], list)
                used.update(r["exception_ids"])
    assert used and used <= known
    # group-scoped exceptions reach the audit-basis rows (telecommunications: 'EX-telecom-group')
    assert any("EX-telecom-group" in r["exception_ids"] for r in site(ENTITY, "taxes_audit_basis.json")["rows"])
    assert any("EX-stale-utility-fy24" in r["exception_ids"] for r in site(ENTITY, "taxes_by_category.json")["rows"])


def test_downloads_and_size_budget(site):
    dl = os.path.join(site.out, ENTITY, "downloads")
    with gzip.open(os.path.join(dl, "facts.csv.gz"), "rt") as f, open(os.path.join(ROOT, "data", ENTITY, "facts.csv")) as g:
        assert f.read() == g.read()
    for name in ("tax_series_by_category.csv", "tax_series_long.csv", "tax_series_audit_basis.csv"):
        assert os.path.exists(os.path.join(dl, name))
    for name in DATASET_COLUMNS:  # a page loads its datasets whole: keep each small
        assert os.path.getsize(os.path.join(site.out, ENTITY, name + ".json")) < 1_000_000


def test_export_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    export_site.export([ENTITY], str(a))
    export_site.export([ENTITY], str(b))
    for root, _, files in os.walk(a):
        for f in files:
            pa = os.path.join(root, f)
            with open(pa, "rb") as x, open(os.path.join(b, os.path.relpath(pa, a)), "rb") as y:
                assert x.read() == y.read(), pa


def test_budget_vs_actual_totals_and_variance(site, table):
    rows = site(ENTITY, "taxes_budget_vs_actual.json")["rows"]
    tot = {r["fiscal_year"]: r for r in rows if r["is_total"]}
    parts = {}
    for r in rows:
        if not r["is_total"]:
            parts.setdefault(r["fiscal_year"], []).append(r)
    for fy, t in tot.items():  # the total row is the sum of its groups
        assert t["budget"] == (None if all(p["budget"] is None for p in parts[fy]) else sum(p["budget"] or 0 for p in parts[fy]))
        assert t["actual"] == (None if all(p["actual"] is None for p in parts[fy]) else sum(p["actual"] or 0 for p in parts[fy]))
    assert sorted(tot) == list(range(2012, 2028))
    assert tot[2027]["actual"] is None and tot[2027]["variance_pct"] is None  # no FY2027 audit yet
    # audited taxes exceeded the original budget in 14 of the 15 audited years (CLAUDE.md)
    audited = [t for fy, t in tot.items() if t["variance_pct"] is not None]
    assert len(audited) == 15 and sum(1 for t in audited if t["variance_pct"] > 0) == 14
    for r in rows:
        if r["variance_pct"] is not None:
            assert abs(r["variance_pct"] - (r["actual"] - r["budget"]) / r["budget"]) < 1e-6


def test_expense_datasets(site):
    by = site(ENTITY, "expenses_by_dept_object.json")["rows"]
    totals = {}
    for r in by:
        totals[r["fiscal_year"]] = totals.get(r["fiscal_year"], 0) + r["amount"]
    assert sorted(totals) == list(range(2015, 2028)) and totals[2027] == 36163223  # FY2027 adopted fund expenses, lines (printed 36,163,222)
    cats = {c["id"] for c in site(ENTITY, "categories.json")["schemes"]["expense_object"]}
    assert {r["object_id"] for r in by} <= cats
    transfers = sum(r["amount"] for r in by if r["fiscal_year"] == 2027 and r["object_id"] == "exp_transfers")
    assert transfers > 10_000_000  # the $10.9M transfer out of reserves for capital projects
    ft = {r["fiscal_year"]: r for r in site(ENTITY, "expenses_fund_totals.json")["rows"]}
    assert all(ft[y]["lines_total"] == totals[y] for y in totals)
    big = {y for y, r in ft.items() if abs(r["diff"]) > 3}
    assert big == {2021, 2024}  # the two years the city's printed total is off (EX-fund-expense-5050 and the stale FY2024 subtotal)
    assert all("EX-fund-expense-5050" in ft[y]["exception_ids"] for y in big)
    av = site(ENTITY, "expenses_audit_vs_budget.json")["rows"]
    comparable = [r for r in av if r["diff"] is not None]
    assert len(comparable) == 182 and sum(1 for r in comparable if r["diff"] == 0) == 179
    misses = {(r["fiscal_year"], r["department_group"]) for r in comparable if r["diff"] != 0}
    assert {(y, g) for y, g in misses if y != 2020} == {(2014, "engineering"), (2017, "street")} and sorted(y for y, _ in misses) == [2014, 2017, 2020]
    assert all(r["exception_ids"] for r in comparable if r["diff"] != 0)  # every miss is explained by an EX-audit-* exception
    tot = {r["fiscal_year"]: r for r in av if r["is_total"]}
    assert sorted(tot) == list(range(2012, 2027))
    assert sum(1 for t in tot.values() if t["variance_pct"] < 0) == 14 and tot[2025]["variance_pct"] > 0  # under budget in 14 of 15 audited years
    for fy, t in tot.items():
        parts = [r for r in av if not r["is_total"] and r["fiscal_year"] == fy]
        assert t["audit_actual"] == sum(r["audit_actual"] or 0 for r in parts)


def test_fund_balance_dataset(site):
    rows = site(ENTITY, "fund_balance.json")["rows"]
    gf = [r for r in rows if r["fund"] == "General"]
    assert [r["fiscal_year"] for r in gf] == list(range(2012, 2027))
    assert gf[0]["total_fund_balance"] == 7770209 and gf[-1]["ending_balance"] == gf[-1]["total_fund_balance"]
    assert all(r["beginning_balance"] + r["net_change"] == r["ending_balance"] for r in gf)  # beginning is already restated (FY2016: -207,812)
    assert gf[-1]["unassigned_months"] > 20  # 21 months of expenditures (CLAUDE.md)


def test_tax_lines_link_to_their_source_page(site):
    rows = site(ENTITY, "tax_lines.json")["rows"]
    assert all(isinstance(r["pdf_page"], int) and r["pdf_page"] >= 1 and r["source_url"].endswith(".pdf") for r in rows)
    one = next(r for r in rows if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted" and r["account"] == "01-4-110-4010")
    assert one["page_label"] == "1 of 15" and one["pdf_page"] == 5  # FY2027 Exhibit A starts on PDF page 5
    assert "753H" in one["source_url"]


def test_corrected_tax_lines_keep_the_raw_reading(site):
    rows = site(ENTITY, "tax_lines.json")["rows"]
    fixed = [r for r in rows if r["corrections"]]
    assert 8 <= len(fixed) <= 40
    one = next(r for r in rows if r["fiscal_year"] == 2019 and r["measure"] == "budget_revised" and r["account"] == "01-4-110-4010")
    c = one["corrections"][0]
    assert c["raw_value"] != c["final_value"] and c["detail"] and int(c["final_value"]) == one["amount"]


def test_exceptions_list_where_they_appear_in_the_source(site):
    ex = {e["exception_id"]: e for e in site(ENTITY, "exceptions.json")["exceptions"]}
    t = ex["EX-telecom-class"]
    assert t["sources"] and all(s["pdf_page"] >= 1 and s["source_url"].endswith(".pdf") for s in t["sources"])
    assert any(s["doc_id"] == "FY2027_budget" and s["pdf_page"] == 5 for s in t["sources"])  # the FY2027 Municipal Infrastructure Maintenance line
    assert all(isinstance(e["sources"], list) for e in ex.values())
