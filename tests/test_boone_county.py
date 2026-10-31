"""Boone County: the committed budget-worksheet extracts tie to the totals the county prints (no PDFs needed)."""
import csv
from pathlib import Path

EX = Path(__file__).resolve().parents[1] / "data/boone_county_il/extracted"
DOCS = {"FY2025_budget": 821, "FY2026_budget": 828, "FY2027_budget_prelim": 811}


def rows(name):
    return list(csv.DictReader(open(EX / name, newline="")))


def test_worksheet_extracts_have_no_open_problems():
    for doc, n_lines in DOCS.items():
        assert rows(f"{doc}_checks.csv") == [], doc
        assert len({r["row_seq"] for r in rows(f"{doc}_lines.csv")}) == n_lines, doc


def test_every_printed_total_ties_or_is_an_accepted_difference():
    for doc in DOCS:
        for t in rows(f"{doc}_totals.csv"):
            if abs(float(t["diff"])) > max(1.0, 0.5 * int(t["n_lines"])):
                assert t["flag"] == "allow_diff", (doc, t)


def test_adjacent_documents_agree_on_the_shared_budget_column():
    def load(doc):
        return {(r["account_printed"], r["period_fy"], r["measure"]): float(r["amount"]) for r in rows(f"{doc}_lines.csv")}
    a, b = load("FY2025_budget"), load("FY2026_budget")
    shared = [(k, v) for k, v in a.items() if k[1:] == ("2025", "final") and (k[0], "2025", "budget") in b]
    bad = [k for k, v in shared if abs(v - b[(k[0], "2025", "budget")]) > 1]
    assert len(shared) == 793 and len(bad) == 16  # restated prior-year budgets, to be listed as exceptions


AUD = EX / "audits"


def audit_rows(fy):
    return list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_statements.csv", newline="")))


def test_audit_fund_statements_pass_every_check_in_all_eleven_audits():
    for fy in range(2015, 2026):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_checks.csv", newline="")))
        assert len(checks) >= 18 and all(c["ok"] == "True" for c in checks), fy


def test_general_fund_balance_chain_between_audits():
    def lines(fy, col):
        return [(r["label"], int(r["amount"])) for r in audit_rows(fy) if r["statement"] == "changes" and r["col"] == col]

    def beginning(fy, col):
        return next(v for l, v in lines(fy, col) if "beginning" in l.lower() or "DECEMBER 1" in l.upper())
    for fy in range(2015, 2025):
        want = lines(fy, "General")[-1][1]
        got = beginning(fy + 1, "General")
        # FY2021's audit restates the General Fund opening balance by 100,024 (Note 15: prior year restatement)
        assert got == want or (fy, got - want) == (2021, 100024), (fy, want, got)
    assert lines(2025, "General")[-1][1] == 13812064
    assert lines(2025, "Total Governmental")[-1][1] == 43666257
