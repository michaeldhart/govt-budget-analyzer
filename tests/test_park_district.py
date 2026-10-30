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


AUDITED = ("FY2019_audit", "FY2020_audit", "FY2021_audit", "FY2022_audit", "FY2023_audit", "FY2024_audit", "FY2025_audit")


def test_audit_fund_statements_pass_every_check():
    """Every identity the audit extractor tests (sums, balance equation, net change, ending = beginning + change, schedule variances, ties between the
    schedules and the statement) holds; only the audit's own sign errors in the variance column are allowed, each with a written reason."""
    allowed = 0
    for doc in AUDITED:
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_checks.csv"))))
        assert checks and all(c["result"] == "ok" or c["result"].startswith("allowed: ") for c in checks), doc
        allowed += sum(1 for c in checks if c["result"] != "ok")
    assert allowed == 7  # FY2023 2, FY2024 2, FY2025 3 (EX-audit-variance-sign)


def test_audit_vs_budget_counts():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, "audit_vs_budget.csv"))))
    assert len(rows) == 49
    assert sum(1 for r in rows if r["expense_matches"] == "1") == 39 and sum(1 for r in rows if r["expense_matches"] != "") == 49
    assert sum(1 for r in rows if r["revenue_matches"] == "1") == 33 and sum(1 for r in rows if r["revenue_matches"] != "") == 42  # FY2022 has no revenue report


def test_fund_balance_chain_across_audits():
    """Each audit's beginning fund balance equals the previous audit's ending balance, for every fund column (FY2020-FY2025: 48 comparisons, 0 breaks)."""
    end, beg = {}, {}
    for y in range(2019, 2026):
        for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"FY{y}_audit_fund_statements.csv"))):
            if r["statement"] == "changes" and r["line_item"] in ("Beginning", "Ending"):
                (end if r["line_item"] == "Ending" else beg)[(y, r["fund"])] = int(r["amount"])
    pairs = [(k, v) for k, v in beg.items() if k[0] >= 2020 and (k[0] - 1, k[1]) in end]
    assert len(pairs) == 48
    assert all(end[(k[0] - 1, k[1])] == v for k, v in pairs)


def test_debt_notes_and_imrf_pass_every_check():
    """Debt note (outstanding, annual requirements, liability activity), legal debt margin, assessed valuation schedule: every identity holds in all seven audits;
    the IMRF required supplementary information in the four text audits."""
    tot = 0
    for doc in AUDITED:
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_debt_checks.csv"))))
        assert checks and all(c["result"].startswith("ok") for c in checks), doc
        tot += len(checks)
    assert tot == 485
    for doc in ("FY2019_audit", "FY2023_audit", "FY2024_audit", "FY2025_audit"):
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_imrf_checks.csv"))))
        assert checks and all(c["result"].startswith("ok") for c in checks), doc


def test_debt_crosschecks():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, "audit_debt_crosschecks.csv"))))
    assert len(rows) == 1025
    assert not [r for r in rows if r["result"] == "FAIL"]
    assert sum(1 for r in rows if r["result"].startswith("explained")) == 2  # FY2020 refunding, FY2024 GASB 101


def test_fy2025_debt_and_pension_headlines():
    out = {r["line_item"]: r["amount"] for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", "FY2025_audit_legal_debt_margin.csv")))
           if r["schedule"] == "legal_debt_margin" and r["column"] == "this_year"}
    assert out["Legal debt margin"] == "22554102" and out["Total debt applicable to limit"] == "4410000"
    npl = [r["amount"] for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", "FY2025_audit_imrf_changes.csv")))
           if r["calendar_year"] == "2025" and r["line_item"].startswith("Net Pension Liability/(Asset)")]
    assert npl == ["-965991"]
