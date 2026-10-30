"""Audit budget-and-actual schedules against the budget documents, fund by fund -> data/belvidere_park_district/audit_vs_budget.csv.

Usage: python3 pipelines/belvidere_park_district/build_audit_compare.py

For every audit year extracted and every audited fund: the audit's budget (the Original budget where an audit prints Original and Final separately, else
the Original & Final column), its actual, and the adopted budget document's total revenues and expenses for the same fund. The audit books proceeds
of debt, leases and subscriptions as other financing sources, the budget document counts them as revenue, so the revenue difference is shown with and
without the audit's other financing sources. A fund-year `matches` when the budget document equals the audit budget to the dollar (a $1 rounding
allowed; the budget documents print cents from FY2026 and in a few funds earlier).
Funds are matched by the era's numbering (entities/belvidere_park_district/funds.csv): Museum and Special Recreation swapped their codes between the FY2024
and FY2025 documents. Document used for each year: FY2019-FY2021 operating budget, FY2022 version report (expenditures only), FY2023 version report
(approved), FY2024 the DRAFT version report (placeholder, exception EX-fy2024-budget-placeholder), FY2025 My Budget Report.
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402

ENT = R.Entity.load(R.ENTITY)
DOCS = {2019: ("FY2019_budget", "budget"), 2020: ("FY2020_budget", "budget"), 2021: ("FY2021_budget", "budget"), 2022: ("FY2022_budget", "approved"),
        2023: ("FY2023_budget", "approved"), 2024: ("FY2024_budget_draft", "requested"), 2025: ("FY2025_budget", "budget_current"),
        2026: ("FY2026_budget", "budget_current")}
FUNDS = [("General Fund", "01", "01"), ("Recreation Fund", "02", "02"), ("Special Recreation Fund", "09", "05"), ("Museum Fund", "03", "08"),
         ("Bond and Interest Fund", "12", "12"), ("Alternate Bond and Interest Fund", "10", "10"), ("Capital Projects Fund", "21", "21")]


def doc_totals(doc, col):
    sums = defaultdict(Decimal)
    for flow in ("revenue", "expense"):
        path = ENT.data_path("extracted", f"{doc}_{flow}.csv")
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path)):
            if r["column"] == col:
                sums[(r["fund_code"], flow)] += Decimal(r["amount"])
    return sums


def main():
    out = []
    for path in sorted(glob.glob(ENT.data_path("extracted", "audits", "FY*_audit_budget_actual.csv"))):
        doc_audit = os.path.basename(path).split("_budget_actual")[0]
        fy = int(doc_audit[2:6])
        ba = list(csv.DictReader(open(path)))
        bdoc, col = DOCS[fy]
        totals = doc_totals(bdoc, col)
        for name, old, new in FUNDS:
            rows = [r for r in ba if r["fund"] == name]
            if not rows:
                continue
            code = new if fy >= 2025 else old

            def v(rx, c):
                return next((Decimal(r["amount"]) for r in rows if re.match(rx, r["line_item"]) and r["column"] == c), None)
            bcol = "budget_original" if any(r["column"] == "budget_original" for r in rows) else "budget"
            a_rev, a_exp = v(r"Total revenues", bcol), v(r"Total expenditures", bcol)
            a_oth = v(r"Total other financing", bcol)
            if a_oth is None:  # a schedule with a single financing line prints no total
                a_oth = sum((Decimal(r["amount"]) for r in rows if r["column"] == bcol and r["section"].startswith("Other financing")), Decimal(0))
            d_rev, d_exp = totals.get((code, "revenue")), totals.get((code, "expense"))
            rec = dict(fiscal_year=fy, fund=name, budget_doc=bdoc, budget_basis=col, doc_fund_code=code,
                       audit_budget_revenues=a_rev, audit_budget_other_financing=a_oth, audit_budget_expenditures=a_exp,
                       audit_actual_revenues=v(r"Total revenues", "actual"), audit_actual_expenditures=v(r"Total expenditures", "actual"),
                       doc_revenues=d_rev, doc_expenses=d_exp)
            rec["revenue_diff"] = None if d_rev is None else d_rev - a_rev
            rec["revenue_diff_with_financing"] = None if d_rev is None else d_rev - a_rev - a_oth
            rec["expense_diff"] = None if d_exp is None else d_exp - a_exp
            rec["revenue_matches"] = "" if d_rev is None else int(min(abs(rec["revenue_diff"]), abs(rec["revenue_diff_with_financing"])) <= 1)
            rec["expense_matches"] = "" if d_exp is None else int(abs(rec["expense_diff"]) <= 1)
            out.append(rec)
    cols = list(out[0])
    with open(ENT.data_path("audit_vs_budget.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    comp = [r for r in out if r["expense_matches"] != ""]
    print(f"{len(out)} fund-years; expense matches {sum(r['expense_matches'] for r in comp)} of {len(comp)}; "
          f"revenue matches {sum(r['revenue_matches'] for r in out if r['revenue_matches'] != '')} of {sum(1 for r in out if r['revenue_matches'] != '')}")
    for r in out:
        flag = ("" if r["expense_matches"] in ("", 1) else f" expense diff {r['expense_diff']}") + ("" if r["revenue_matches"] in ("", 1) else f" revenue diff {r['revenue_diff']} / with financing {r['revenue_diff_with_financing']}")
        if flag:
            print(f"  {r['fiscal_year']} {r['fund']}:{flag}")


if __name__ == "__main__":
    main()
