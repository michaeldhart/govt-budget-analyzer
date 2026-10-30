"""Structural invariants on the park district's canonical tables (the counterpart of test_structure.py): keys, references, vocabularies, ranges."""
import csv
import os
from collections import Counter

import pytest

from conftest import ROOT
from govbudget import schema

ENT = "belvidere_park_district"
D = os.path.join(ROOT, "data", ENT)


def table(name):
    with open(os.path.join(D, name + ".csv"), newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def entity():
    from govbudget.entity import Entity
    return Entity.load(ENT)


def test_facts_values_and_ranges():
    facts = table("facts")
    assert all(r["amount"].lstrip("-").isdigit() for r in facts)
    years = {int(r["period_fy"]) for r in facts}
    assert years <= set(range(2018, 2028)) and {2019, 2027} <= years  # 2018 appears only as the FY2019 audit's prior-year comparative
    assert all(r["pdf_page"].isdigit() and int(r["pdf_page"]) >= 1 for r in facts)
    assert all(r["account_key"] for r in facts if r["source_type"] == "budget")
    assert all(not r["account_key"] for r in facts if r["source_type"] == "audit")


def test_fund_codes_are_known():
    codes = {r["fund_code"] for r in csv.DictReader(open(os.path.join(ROOT, "entities", ENT, "funds.csv")))}
    assert {r["fund_code"] for r in table("facts")} <= codes


def test_bases_follow_the_document_status():
    """A draft only gives `requested`, an adopted budget `adopted`, the amended reports `revised`; audits give the audit bases; facts sit in their document's year."""
    docs = {r["doc_id"]: r for r in table("documents")}
    for f in table("facts"):
        d = docs[f["doc_id"]]
        if f["source_type"] == "budget":
            assert int(f["period_fy"]) == int(d["fiscal_year"]), f
            want = {"draft": {"requested"}, "adopted": {"adopted"}, "final": {"revised"}}[d["status"]]
            assert f["basis"] in want, f
        else:
            assert d["doc_type"] == "audit" and f["basis"] in ("audit_original", "audit_final", "actual_audited"), f
            assert int(f["period_fy"]) in (int(d["fiscal_year"]), int(d["fiscal_year"]) - 1)


def test_accounts_table_agrees_with_facts():
    accounts = {(r["flow"], r["account_key"]): r for r in table("accounts")}
    assert len(accounts) == len(table("accounts")) == 816
    adopted = {}  # first_fy / last_fy span the account's ADOPTED budget years
    for r in table("facts"):
        if r["source_type"] == "budget" and r["basis"] == "adopted":
            adopted.setdefault((r["flow"], r["account_key"]), set()).add(int(r["period_fy"]))
    for k, years in adopted.items():
        assert (int(accounts[k]["first_fy"]), int(accounts[k]["last_fy"])) == (min(years), max(years)), k
    for k, a in accounts.items():
        assert bool(a["first_fy"]) == (k in adopted), k


def test_documents_unique_and_complete():
    docs = table("documents")
    ids = [r["doc_id"] for r in docs]
    assert len(ids) == len(set(ids)) == 44
    assert Counter(r["doc_type"] for r in docs) == {"budget": 13, "appropriation": 9, "audit": 8, "levy": 7, "treasurer": 4, "budget_amendment": 2, "afr_form": 1}
    used = ({r["doc_id"] for r in table("facts")} | {r["doc_id"] for r in table("fund_statements")} | {r["doc_id"] for r in table("control_totals")}
            | {r["doc_id"] for r in table("corrections")})
    assert used <= set(ids)


def test_vocabularies():
    assert all(r["kind"] in schema.TOTAL_KINDS and r["basis"] in schema.BASES for r in table("control_totals"))
    assert all(r["statement"] in schema.STATEMENTS for r in table("fund_statements"))
    assert all(int(r["period_fy"]) in range(2019, 2026) for r in table("fund_statements"))
    assert all(r["flow"] in schema.FLOWS and r["source_type"] in schema.SOURCE_TYPES for r in table("facts"))


@pytest.mark.parametrize("name", sorted(set(schema.DEBT_PENSION_TABLES) - {"opeb_rollforward"}) + sorted(set(schema.TAX_DEBT_TABLES) - {"ordinance_lines", "ordinance_summary"}))
def test_audit_tables_cover_the_audits(name):
    rows = table(name)
    want = {str(y) for y in (range(2019, 2026) if not name.startswith("pension") else (2019, 2023, 2024, 2025))}
    if name.startswith("lease_sbita"):
        want = {"2024", "2025"}  # the first subscription (GASB 96) is in the FY2024 audit, the first lease in FY2025
    if name == "debt_issue_terms":
        want = {str(y) for y in range(2019, 2026)}
    assert {r["period_fy"] for r in rows} == want, name
    assert all(r["pdf_page"].isdigit() for r in rows)


def test_exception_ids_resolve_everywhere(entity):
    ids = {e["exception_id"] for e in entity.exceptions()}
    assert len(ids) == len(entity.exceptions()), "duplicate exception_id"
    for name in list(schema.DEBT_PENSION_TABLES) + list(schema.TAX_DEBT_TABLES) + ["facts"]:
        if not os.path.exists(os.path.join(D, name + ".csv")):
            continue
        for r in table(name):
            for x in r["exception_ids"].split("|"):
                assert not x or x in ids, f"{name}: unknown exception {x}"
    for e in entity.exceptions():
        assert e["kind"] in schema.EXCEPTION_KINDS and e["scope"] in schema.EXCEPTION_SCOPES and e["effect"] in schema.EXCEPTION_EFFECTS


def test_source_urls_cover_documents(entity):
    """Every document links to its own file on the district's site; where the PDF is present locally its size equals the size the server reported."""
    manifest = {r["doc_id"]: r for r in entity.table("source_urls.csv")}
    docs = {r["doc_id"]: r for r in table("documents")}
    assert set(docs) == set(manifest)
    assert all(r["url"].startswith("https://www.belviderepark.org/wp-content/uploads/") and r["url"].endswith(".pdf") for r in manifest.values())
    assert len({r["url"] for r in manifest.values()}) == len(manifest)
    assert all(docs[d]["source_url"] == manifest[d]["url"] for d in docs)
    checked = 0
    for d, r in docs.items():
        path = os.path.join(ROOT, "sources", ENT, r["pdf_path"])
        if os.path.exists(path):
            assert os.path.getsize(path) == int(manifest[d]["size_bytes"]), d
            checked += 1
    assert checked in (0, len(docs))  # the PDFs are gitignored: a CI checkout has none, a full local checkout all 44
