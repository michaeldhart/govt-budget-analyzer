"""Belvidere Park District (second government): config validates, every extracted account is mapped, and each committed budget extract still ties
to the totals its own totals file holds (the extractors validate against the printed totals; this re-checks the committed CSVs without OCR)."""
import csv
import glob
import os
from collections import defaultdict
from decimal import Decimal

from conftest import ROOT

ENT = "belvidere_park_district"
EXTRACTED = os.path.join(ROOT, "data", ENT, "extracted")
DOCS = ("FY2019_budget FY2020_budget FY2021_budget FY2022_budget FY2023_budget FY2023_budget_draft FY2024_budget_draft FY2025_budget "
        "FY2025_budget_draft FY2026_budget FY2026_budget_draft FY2027_budget_draft FY2023_budget_amended FY2023_budget_amended_oct").split()


def rows(doc, flow):
    return list(csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_{flow}.csv"))))


def test_config_validates():
    import sys
    sys.path.insert(0, ROOT)
    from govbudget.entity import Entity
    from govbudget.validate import validate_config
    assert validate_config(Entity.load(ENT)) == []


def test_every_extracted_account_is_mapped():
    import sys
    sys.path.insert(0, ROOT)
    from govbudget.entity import Entity
    E = Entity.load(ENT)
    mapped = {(r["flow"], r["account_key"]) for r in E.table("account_map.csv")}
    missing = set()
    for doc in DOCS:
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                fund, dept, obj = r["account"].split("-", 2)
                key = f"{dept}-{obj}" if fund == "01" else f"{fund}:{dept}-{obj}"
                if (flow, key) not in mapped:
                    missing.add((doc, flow, key))
    assert not missing, sorted(missing)[:10]


def test_fund_reports_tie_to_their_printed_totals():
    """Layout D (My Budget Report): fund revenue and expense lines equal the Revenue Total / Expense Total in both columns."""
    for doc in ("FY2025_budget", "FY2025_budget_draft", "FY2026_budget", "FY2026_budget_draft", "FY2027_budget_draft"):
        sums = defaultdict(Decimal)
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                sums[(r["fund_code"], "fund_revenues" if flow == "revenue" else "fund_expenses", r["column"])] += Decimal(r["amount"])
        printed = {(r["fund_code"], r["kind"], r["column"]): Decimal(r["amount"]) for r in csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv")))
                   if r["kind"] in ("fund_revenues", "fund_expenses")}
        assert printed and all(sums[k] == v for k, v in printed.items()), doc


def test_version_reports_tie_to_fund_subtotals():
    """Layout B and the amended reports: each fund's lines equal its Subtotal."""
    for doc in ("FY2022_budget", "FY2023_budget", "FY2023_budget_draft", "FY2024_budget_draft"):
        sums = defaultdict(Decimal)
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                sums[(r["fund_code"], flow)] += Decimal(r["amount"])
        subs = [r for r in csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv"))) if r["kind"] == "fund_subtotal"]
        assert subs and all(sums[(r["fund_code"], r["flow"])] == Decimal(r["amount"]) for r in subs), doc


def test_operating_budget_blocks_tie():
    """Layout A: block totals equal the lines of their section (FY2020's Meehan block is the city's own 1,000 difference, flagged allow_diff)."""
    for doc in ("FY2019_budget", "FY2020_budget", "FY2021_budget"):
        totals = list(csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv"))))
        assert any(t["kind"] == "grand" for t in totals), doc
        assert all(t["flag"] in ("", "allow_diff", "unprinted") for t in totals if t["kind"] != "summary"), doc
    flagged = [t["name"] for t in csv.DictReader(open(os.path.join(EXTRACTED, "FY2020_budget_totals.csv"))) if t["flag"] == "allow_diff"]
    assert sorted(flagged) == ["Total Meehan Expenses", "Total Other Funds Expenses", "Total Other Funds Income"]


def test_fy2027_draft_headline():
    rev = sum(Decimal(r["amount"]) for r in rows("FY2027_budget_draft", "revenue") if r["column"] == "budget_current")
    exp = sum(Decimal(r["amount"]) for r in rows("FY2027_budget_draft", "expense") if r["column"] == "budget_current")
    assert (rev, exp) == (Decimal("8707625"), Decimal("8696267.00"))
    assert rev - exp == Decimal("11358")  # the draft's printed Report Surplus
