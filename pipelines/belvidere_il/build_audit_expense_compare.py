"""Compare the audit's General Fund expenditures with the budget documents' expenses, by department group and year.

Usage: python3 pipelines/belvidere_il/build_audit_expense_compare.py   (run build_facts.py first)

Inputs : data/belvidere_il/facts.csv; entities/belvidere_il/audit_expenditure_map.csv (audit function/department -> group -> budget dept codes)
Output : data/belvidere_il/audit_expenses_vs_budget.csv   per fiscal year x department group: the audit's original budget, the budget
         document's adopted expenses, and the reconciling items between them

The audit's ORIGINAL budget column is the adopted budget at department level: after three presentation differences it equals the budget
document's adopted expenses to the dollar (exceptions EX-audit-*):
  * transfers out (object codes 9xxx, mostly 9999 Operating Transfers Out, 9990 ARPA transfer) are not General Fund expenditures in the audit;
  * the Health Insurance Reimbursement (110-4540), which the budget prints as negative expense, is revenue in the audit from FY2016 (so
    General Government is compared with it excluded) but is netted into expenses in the FY2014-FY2015 audits;
  * the sales tax rebate to developers, which the budget nets against revenue (110-4111 Sales Tax to Developer), is an expenditure in the
    audit ("Sales tax incentive", Economic development); the real estate tax rebate (110-4114) is netted in the audit as well;
  * the audit books the Utility Tax department's capital projects (budget dept 751) under Economic development.
Categories (personnel / contractual / commodities) are NOT compared: the audit classifies some lines differently from the city's object
codes (e.g. Meetings and Conferences is a commodity in the audit, personnel in the budget).
"""
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
COLUMNS = ["entity_id", "fiscal_year", "department_group", "audit_original", "audit_final", "audit_actual", "budget_lines",
           "transfers_excluded", "contra_excluded", "developer_rebates", "budget_adjusted", "diff", "note"]
CONTRA_NETTED_THROUGH = 2015  # FY2014-FY2015 audits net the health insurance reimbursement (110-4540) into expenses; later audits show it as revenue
TRANSFER_LABEL = "transfer"   # e.g. 751-8040 "Columbia Ave (transfer to Capital)": an 8xxx project code that is really a transfer out
REBATE_EXPENDITURE_FROM = 2015  # the FY2014 audit nets the $35,000 developer rebate into sales tax revenue instead (see tax_series_audit_basis notes)
DEVELOPER = {"110-4111"}  # Sales Tax to Developer: a revenue offset in the budget, an expenditure in the audit (the real estate tax rebate 110-4114 stays netted in the audit too)
REBATE_GROUP = "economic_development"


def main():
    emap = {(r["audit_function"], r["audit_department"]): r for r in E.table("audit_expenditure_map.csv")}
    by_canon = {(r["function"], r["department"]): r for r in emap.values()}
    groups = {}
    for r in emap.values():
        groups[r["department_group"]] = r["budget_dept_codes"].split("|") if r["budget_dept_codes"] else []
    aud = defaultdict(int)
    bud = defaultdict(lambda: defaultdict(int))
    docs_with_own = {int(r["fiscal_year"]) for r in read_csv(E.data_path("documents.csv")) if r["doc_type"] == "budget"}
    for f in read_csv(E.data_path("facts.csv")):
        fy = int(f["period_fy"])
        if f["source_type"] == "audit" and f["flow"] == "expense":
            fn, dp, _cat = f["audit_section"].split(" | ")
            aud[(fy, by_canon[(fn, dp)]["department_group"], f["basis"])] += int(f["amount"])
        elif f["source_type"] == "budget":
            doc_fy = int(f["doc_id"][2:6])
            own = f["basis"] == "adopted" and doc_fy == fy
            fy2014 = fy == 2014 and doc_fy == 2015 and f["basis"] == "prior_doc_budget"  # no FY2014 document extracted
            if not (own or fy2014):
                continue
            key = f["account_key"]
            dept = key.split("-")[0]
            if f["flow"] == "expense":
                part = "transfers" if int(key.split("-")[1]) >= 9000 or TRANSFER_LABEL in f["label"].lower() else "lines"
                bud[(fy, dept)][part] += int(f["amount"])
            elif f["flow"] == "contra_expense":
                bud[(fy, dept)]["contra"] += int(f["amount"])
            elif f["flow"] == "revenue" and key in DEVELOPER:
                bud[(fy, "rebate")]["rebate"] += -int(f["amount"])  # the offset is negative; the audit's expenditure is positive
    out = []
    for fy in sorted({k[0] for k in aud}):
        for g, codes in groups.items():
            if (fy, g, "audit_original") not in aud:
                continue
            lines = sum(bud[(fy, c)]["lines"] for c in codes)
            transfers = sum(bud[(fy, c)]["transfers"] for c in codes)
            contra = sum(bud[(fy, c)]["contra"] for c in codes)
            rebate = bud[(fy, "rebate")]["rebate"] if g == REBATE_GROUP and fy >= REBATE_EXPENDITURE_FROM else 0
            netted = contra if fy <= CONTRA_NETTED_THROUGH else 0
            adjusted = lines + rebate + netted  # transfers (and, after FY2015, the contra reimbursement) are not in the audit's figure
            note = "audit nets the health insurance reimbursement (EX-audit-contra-netted)" if netted else ""
            if fy not in docs_with_own and fy != 2014:
                note = "no budget document for this year"
            out.append(dict(entity_id=E.id, fiscal_year=fy, department_group=g, audit_original=aud[(fy, g, "audit_original")],
                            audit_final=aud[(fy, g, "audit_final")], audit_actual=aud[(fy, g, "actual_audited")],
                            budget_lines=lines, transfers_excluded=transfers, contra_excluded=contra, developer_rebates=rebate,
                            budget_adjusted=adjusted, diff=aud[(fy, g, "audit_original")] - adjusted if lines or rebate else "", note=note))
    write_csv(E.data_path("audit_expenses_vs_budget.csv"), COLUMNS, out)
    comparable = [r for r in out if r["diff"] != ""]
    exact = [r for r in comparable if r["diff"] == 0]
    print(f"{len(out)} group-years -> data/{E.id}/audit_expenses_vs_budget.csv: {len(exact)} of {len(comparable)} comparable group-years match the budget to the dollar")
    for r in comparable:
        if r["diff"]:
            print(f"  FY{r['fiscal_year']} {r['department_group']:24} audit {r['audit_original']:>10,} budget {r['budget_adjusted']:>10,} diff {r['diff']:>+10,}")


if __name__ == "__main__":
    main()
