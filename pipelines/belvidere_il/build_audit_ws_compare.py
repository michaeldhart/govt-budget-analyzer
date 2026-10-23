"""Compare the audit's Water and Sewer operating expenses with the budget documents' fund 61 expenses, by department and year.

Usage: python3 pipelines/belvidere_il/build_audit_ws_compare.py   (run build_facts.py first)

Inputs : data/belvidere_il/facts.csv; entities/belvidere_il/audit_expenditure_map.csv (fund 61 rows: audit department -> budget dept code)
Output : data/belvidere_il/audit_ws_vs_budget.csv   per fiscal year x department (Water, Sewer, Collection system)

As for the General Fund, the audit's ORIGINAL budget is the adopted budget; three items in the budget block are not operating expenses in
the audit and are shown separately: the depreciation / bond payment set-asides (unnumbered lines, 61-5-DDD-DEPSA/BONDSA, which move cash to
the depreciation and bond funds), transfers out (object 9999) and the card processing fee (object 4595, which the audit nets against revenue).
adjusted budget = lines - those three; diff = audit original - adjusted budget (0 when they agree).
"""
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
COLUMNS = ["entity_id", "fiscal_year", "department", "audit_original", "audit_final", "audit_actual", "budget_lines", "set_asides_excluded",
           "transfers_excluded", "card_fee_excluded", "budget_adjusted", "diff"]


def main():
    groups = {r["audit_function"]: r for r in E.table("audit_expenditure_map.csv") if r["fund_code"] == "61"}
    by_canon = {r["function"]: r for r in groups.values()}
    aud = defaultdict(int)
    bud = defaultdict(lambda: defaultdict(int))
    own = {int(r["fiscal_year"]) for r in read_csv(E.data_path("documents.csv")) if r["doc_type"] == "budget"}
    for f in read_csv(E.data_path("facts.csv")):
        if f["fund_code"] != "61" or f["flow"] != "expense":
            continue
        fy = int(f["period_fy"])
        if f["source_type"] == "audit" and not f["audit_section"].startswith("Water and Sewer summary"):
            aud[(fy, f["audit_section"].split(" | ")[0], f["basis"])] += int(f["amount"])
        elif f["source_type"] == "budget" and f["basis"] == "adopted" and int(f["doc_id"][2:6]) == fy:
            dept, obj = f["account_key"].split(":")[1].split("-")
            part = "set_asides" if obj in ("DEPSA", "BONDSA") else "transfers" if obj == "9999" else "card_fee" if obj == "4595" else "lines"
            bud[(fy, dept)][part] += int(f["amount"])
    out = []
    for fy in sorted({k[0] for k in aud}):
        for g, m in sorted(by_canon.items(), key=lambda x: x[1]["budget_dept_codes"]):
            if (fy, g, "audit_original") not in aud:
                continue
            b = bud[(fy, m["budget_dept_codes"])]
            have = fy in own
            adjusted = b["lines"]
            out.append(dict(entity_id=E.id, fiscal_year=fy, department=g, audit_original=aud[(fy, g, "audit_original")],
                            audit_final=aud[(fy, g, "audit_final")], audit_actual=aud[(fy, g, "actual_audited")], budget_lines=b["lines"] + b["set_asides"] + b["transfers"] + b["card_fee"] if have else "",
                            set_asides_excluded=b["set_asides"] if have else "", transfers_excluded=b["transfers"] if have else "",
                            card_fee_excluded=b["card_fee"] if have else "", budget_adjusted=adjusted if have else "",
                            diff=aud[(fy, g, "audit_original")] - adjusted if have else ""))
    write_csv(E.data_path("audit_ws_vs_budget.csv"), COLUMNS, out)
    comparable = [r for r in out if r["diff"] != ""]
    exact = [r for r in comparable if r["diff"] == 0]
    print(f"{len(out)} department-years -> data/{E.id}/audit_ws_vs_budget.csv: {len(exact)} of {len(comparable)} match the budget to the dollar")
    for r in comparable:
        if r["diff"]:
            print(f"  FY{r['fiscal_year']} {r['department']:18} audit {r['audit_original']:>10,} budget {r['budget_adjusted']:>10,} diff {r['diff']:>+9,}")


if __name__ == "__main__":
    main()
