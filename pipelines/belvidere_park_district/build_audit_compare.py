"""Audit budget-and-actual schedules against the budget documents, fund by fund -> data/belvidere_park_district/audit_vs_budget.csv, from the canonical facts.

Usage: python3 pipelines/belvidere_park_district/build_audit_compare.py     (after build_facts.py)

Reads only facts.csv (and entities/belvidere_park_district/audit_fund_codes.csv for the fund names): for every audit year and audited fund, the audit's
original budget (the revenue lines, the other financing lines and the expenditure lines of the budget-and-actual schedule), its actual, and the budget
document's revenues and expenses for the same fund and year. The audit books proceeds of debt, leases and subscriptions as other financing sources, the
budget document counts them as revenue, so the revenue difference is also shown with the audit's other financing added. A fund-year `matches` when the
budget document equals the audit budget to the dollar (a dollar of rounding allowed: the facts are whole dollars and some documents print cents).
The budget document of a year is its adopted budget; The row's `budget_basis` says which basis it used. Fund codes are the era's own (Museum and Special Recreation swap codes after FY2024), the same in the audit and the budget.
"""
import csv
import os
import sys
from collections import defaultdict
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402

E = Entity.load("belvidere_park_district")
COLUMNS = ["fiscal_year", "fund", "budget_doc", "budget_basis", "doc_fund_code", "audit_budget_revenues", "audit_budget_other_financing",
           "audit_budget_expenditures", "audit_actual_revenues", "audit_actual_expenditures", "doc_revenues", "doc_expenses", "revenue_diff",
           "revenue_diff_with_financing", "expense_diff", "revenue_matches", "expense_matches"]


def main():
    facts = list(csv.DictReader(open(E.data_path("facts.csv"))))
    schedule_name = {r["statement_column"]: r["schedule_name"] for r in E.table("audit_fund_codes.csv")}
    audit = defaultdict(int)       # (fy, fund_code, fund name, kind, basis) -> dollars, kind revenue | other_financing | expense
    funds = {}                     # (fy, fund_code) -> fund name, in the order the audit prints them
    budget = defaultdict(int)      # (fy, fund_code, flow, basis, doc) -> dollars
    for f in facts:
        fy, code, amount = int(f["period_fy"]), f["fund_code"], int(f["amount"])
        if f["source_type"] == "audit":
            if "prior_year_comparative" in f["flag"]:
                continue
            name = schedule_name[f["audit_section"].split(" | ")[0]]
            kind = "other_financing" if "other_financing" in f["flag"] else f["flow"]
            audit[(fy, code, kind, f["basis"])] += amount
            funds.setdefault((fy, code), name)
        elif f["basis"] in ("adopted", "requested"):
            budget[(fy, code, f["flow"], f["basis"], f["doc_id"])] += amount
    out = []
    for (fy, code), name in sorted(funds.items(), key=lambda kv: (kv[0][0], list(schedule_name.values()).index(kv[1]))):
        basis = "adopted" if any(k[0] == fy and k[3] == "adopted" for k in budget) else "requested"
        docs = sorted({k[4] for k in budget if k[0] == fy and k[3] == basis})
        doc = docs[0] if len(docs) == 1 else ";".join(docs)
        rev = sum(v for k, v in budget.items() if k[:3] == (fy, code, "revenue") and k[3] == basis)
        exp = sum(v for k, v in budget.items() if k[:3] == (fy, code, "expense") and k[3] == basis)
        has_rev = any(k[:3] == (fy, code, "revenue") and k[3] == basis for k in budget)
        has_exp = any(k[:3] == (fy, code, "expense") and k[3] == basis for k in budget)
        a_rev, a_oth, a_exp = (audit[(fy, code, k, "audit_original")] for k in ("revenue", "other_financing", "expense"))
        rec = dict(fiscal_year=fy, fund=name, budget_doc=doc, budget_basis=basis, doc_fund_code=code, audit_budget_revenues=a_rev,
                   audit_budget_other_financing=a_oth, audit_budget_expenditures=a_exp, audit_actual_revenues=audit[(fy, code, "revenue", "actual_audited")],
                   audit_actual_expenditures=audit[(fy, code, "expense", "actual_audited")], doc_revenues=rev if has_rev else "", doc_expenses=exp if has_exp else "")
        rec["revenue_diff"] = rev - a_rev if has_rev else ""
        rec["revenue_diff_with_financing"] = rev - a_rev - a_oth if has_rev else ""
        rec["expense_diff"] = exp - a_exp if has_exp else ""
        rec["revenue_matches"] = int(min(abs(rec["revenue_diff"]), abs(rec["revenue_diff_with_financing"])) <= 1) if has_rev else ""
        rec["expense_matches"] = int(abs(rec["expense_diff"]) <= 1) if has_exp else ""
        out.append(rec)
    with open(E.data_path("audit_vs_budget.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    comp = [r for r in out if r["expense_matches"] != ""]
    rcomp = [r for r in out if r["revenue_matches"] != ""]
    print(f"{len(out)} fund-years; expense matches {sum(r['expense_matches'] for r in comp)} of {len(comp)}; "
          f"revenue matches {sum(r['revenue_matches'] for r in rcomp)} of {len(rcomp)}")
    for r in out:
        flag = ("" if r["expense_matches"] in ("", 1) else f" expense diff {r['expense_diff']}") + \
            ("" if r["revenue_matches"] in ("", 1) else f" revenue diff {r['revenue_diff']} / with financing {r['revenue_diff_with_financing']}")
        if flag:
            print(f"  {r['fiscal_year']} {r['fund']}:{flag}")


if __name__ == "__main__":
    main()
