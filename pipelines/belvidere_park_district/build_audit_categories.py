"""Audit expenditure categories against the budget's object categories, fund by fund -> data/belvidere_park_district/audit_categories_vs_budget.csv.

Usage: python3 pipelines/belvidere_park_district/build_audit_categories.py     (after build_facts.py)

The audits print each fund's expenditures in four classes (Personnel, Contractual services, Material & supplies, Capital outlay) and debt service
(Principal, Interest); the budget classifies every account by object (entities/belvidere_park_district/account_map.csv, scheme expense_object). The two are
compared in four groups the classifications can share: personnel, contractual, supplies_capital (the audit's Material & supplies and Capital outlay; the
budget's supplies, maintenance and equipment purchases and its capital projects), debt_service. Reads facts.csv and accounts.csv only: the audit's original
budget against the budget document's adopted budget (FY2024: the draft's requested column, see EX-fy2024-budget-placeholder).
A group `matches` when the two agree to the dollar (a dollar of rounding allowed). The fund totals agree in 39 of 49 fund-years (audit_vs_budget.csv); the
groups agree less often because the audits and the budget draw the lines between classes differently: the table shows by how much.
"""
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402

E = Entity.load("belvidere_park_district")
AUDIT_GROUP = {"Personnel": "personnel", "Contractual": "contractual", "Contractual services": "contractual", "Material & supplies": "supplies_capital",
               "Capital outlay": "supplies_capital", "Capital Outlay": "supplies_capital", "Principal": "debt_service", "Principal payments": "debt_service",
               "Interest": "debt_service", "Interest payments": "debt_service", "Bond issue costs": "debt_service"}
BUDGET_GROUP = {"exp_personnel": "personnel", "exp_contractual": "contractual", "exp_supplies": "supplies_capital", "exp_capital": "supplies_capital",
                "exp_debt_service": "debt_service", "exp_other": "supplies_capital"}
GROUPS = ("personnel", "contractual", "supplies_capital", "debt_service")
COLUMNS = ["fiscal_year", "fund", "doc_fund_code", "group", "budget_doc", "budget_basis", "audit_original", "budget", "diff", "diff_pct", "matches"]


def main():
    facts = list(csv.DictReader(open(E.data_path("facts.csv"))))
    category = {(r["flow"], r["account_key"]): r["category_id"] for r in csv.DictReader(open(E.data_path("accounts.csv")))}
    schedule_name = {r["statement_column"]: r["schedule_name"] for r in E.table("audit_fund_codes.csv")}
    order = list(schedule_name.values())
    audit, budget, funds = defaultdict(int), defaultdict(int), {}
    docs = defaultdict(set)
    for f in facts:
        fy, code, amount = int(f["period_fy"]), f["fund_code"], int(f["amount"])
        if f["flow"] != "expense":
            continue
        if f["source_type"] == "audit" and f["basis"] == "audit_original" and "prior_year" not in f["flag"]:
            audit[(fy, code, AUDIT_GROUP[f["label"]])] += amount
            funds.setdefault((fy, code), schedule_name[f["audit_section"].split(" | ")[0]])
        elif f["source_type"] == "budget" and f["basis"] in ("adopted", "requested"):
            budget[(fy, code, f["basis"], BUDGET_GROUP[category[(f["flow"], f["account_key"])]])] += amount
            docs[(fy, f["basis"])].add(f["doc_id"])
    out = []
    for (fy, code), name in sorted(funds.items(), key=lambda kv: (kv[0][0], order.index(kv[1]))):
        basis = "adopted" if (fy, "adopted") in docs else "requested"
        doc = ";".join(sorted(docs[(fy, basis)]))
        has = any(k[:3] == (fy, code, basis) for k in budget)
        for g in GROUPS:
            a, b = audit.get((fy, code, g), 0), budget.get((fy, code, basis, g), 0)
            if a == 0 and b == 0:
                continue
            out.append(dict(fiscal_year=fy, fund=name, doc_fund_code=code, group=g, budget_doc=doc, budget_basis=basis, audit_original=a,
                            budget=b if has else "", diff=b - a if has else "", diff_pct=f"{(b - a) / a * 100:.1f}" if has and a else "",
                            matches=int(abs(b - a) <= 1) if has else ""))
    with open(E.data_path("audit_categories_vs_budget.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    comp = [r for r in out if r["matches"] != ""]
    print(f"{len(out)} fund-year-groups ({len(comp)} comparable): {sum(r['matches'] for r in comp)} agree to the dollar, "
          f"{sum(1 for r in comp if r['diff_pct'] != '' and abs(float(r['diff_pct'])) <= 1)} within 1%, {sum(1 for r in comp if r['diff_pct'] != '' and abs(float(r['diff_pct'])) <= 5)} within 5%")


if __name__ == "__main__":
    main()
