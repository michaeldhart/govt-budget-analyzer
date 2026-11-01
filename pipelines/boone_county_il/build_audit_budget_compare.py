"""Compare the audits' budgets and actuals with the budget worksheets, fund by fund -> data/boone_county_il/audit_vs_budget.csv.

Usage: python3 pipelines/boone_county_il/build_audit_budget_compare.py
Years: the worksheets give FY2025 (adopted) and FY2024 (the prior-year budget column of the FY2025 document); the audit's original budget is compared with
that, the audit's actual with the worksheet's closed-year activity (FY2023 from the FY2025 document, FY2024 from FY2026, FY2025 from FY2027 preliminary).
Presentation rules (the audit reports transfers and sale proceeds as other financing, the worksheets as revenue and expense accounts):
  revenue        = worksheet revenue accounts except transfers in (399xxx) and proceeds (390000, 395000)   vs audit revenue items
  expenditure    = worksheet expense accounts except transfers out (89xxxx)                                  vs audit expenditure items
  other_financing= transfers in + proceeds - transfers out (worksheet)                                       vs the sum of the audit's other financing items
Columns: measure, budget_basis (adopted | prior_doc_budget), budget_doc, worksheet_budget, audit_budget, budget_diff, activity_doc, worksheet_activity, audit_actual, actual_diff, status.
"""
import csv, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402

E = Entity.load("boone_county_il")
COLUMNS = ["entity_id", "fund_code", "fund_name", "period_fy", "measure", "budget_basis", "budget_doc", "worksheet_budget", "audit_budget", "budget_diff",
           "activity_doc", "worksheet_activity", "audit_actual", "actual_diff", "budget_status", "actual_status", "audit_funds"]
FINANCING_CATS = {"transfers_in", "proceeds"}


def main():
    amap = E.account_map()
    funds = {r["fund_code"]: r["name"] for r in E.table("funds.csv")}
    ws = defaultdict(int)        # (fund, measure, period, basis, doc) -> amount
    aud = defaultdict(int)       # (fund, measure, period, basis) -> amount
    audit_names = defaultdict(set)
    for f in csv.DictReader(open(E.data_path("facts.csv"))):
        amt, fy = int(f["amount"]), int(f["period_fy"])
        if f["source_type"] == "budget":
            cat = amap[(f["flow"], f["account_key"])]["category_id"]
            if f["flow"] == "revenue":
                measure, sign = ("other_financing", 1) if cat in FINANCING_CATS else ("revenue", 1)
            else:
                measure, sign = ("other_financing", -1) if cat == "exp_transfers" else ("expenditure", 1)
            ws[(f["fund_code"], measure, fy, f["basis"], f["doc_id"])] += sign * amt
        else:
            if f["basis"] == "audit_final" and "combined_column" in f["flag"]:
                continue  # the same amount is also audit_original
            measure = "other_financing" if "other_financing" in f["flag"] else ("revenue" if f["flow"] == "revenue" else "expenditure")
            aud[(f["fund_code"], measure, fy, f["basis"])] += amt
            audit_names[(f["fund_code"], fy)].add(f["audit_section"].split(" | ")[0] or "General Fund")
    rows = []
    for (fund, measure, fy), _ in sorted({(k[0], k[1], k[2]): 0 for k in aud if k[2] >= 2023}.items()):
        # budget column to compare: the document's own adopted budget if it has one, else the earliest document's prior-year column
        cands = [(k[3], k[4], v) for k, v in ws.items() if k[:3] == (fund, measure, fy) and k[3] in ("adopted", "prior_doc_budget")]
        cands.sort(key=lambda c: (c[0] != "adopted", c[1]))
        basis, doc, wsb = cands[0] if cands else ("", "", "")
        ab = aud.get((fund, measure, fy, "audit_original"), aud.get((fund, measure, fy, "audit_final")))
        act = [(k[4], v) for k, v in ws.items() if k[:3] == (fund, measure, fy) and k[3] == "actual_prelim"]
        act.sort()
        adoc, wsa = act[-1] if act else ("", "")
        aa = aud.get((fund, measure, fy, "actual_audited"))
        bd = (ab - wsb) if (wsb != "" and ab is not None) else ""
        ad = (aa - wsa) if (wsa != "" and aa is not None) else ""
        budget_status = "" if bd == "" else ("agree" if bd == 0 else "differs")
        actual_status = "" if ad == "" else ("agree" if ad == 0 else "differs")
        rows.append(dict(entity_id=E.id, fund_code=fund, fund_name=funds.get(fund, ""), period_fy=fy, measure=measure, budget_basis=basis, budget_doc=doc,
                         worksheet_budget=wsb, audit_budget=ab if ab is not None else "", budget_diff=bd, activity_doc=adoc, worksheet_activity=wsa,
                         audit_actual=aa if aa is not None else "", actual_diff=ad, budget_status=budget_status, actual_status=actual_status,
                         audit_funds="; ".join(sorted(audit_names.get((fund, fy), [])))[:120]))
    write_csv(E.data_path("audit_vs_budget.csv"), COLUMNS, rows)
    print(len(rows), "rows;", sum(r["budget_status"] == "agree" for r in rows), "budget comparisons agree,", sum(r["budget_status"] == "differs" for r in rows), "differ")
    gf_functions(amap)


FUNCTION_COLUMNS = ["entity_id", "period_fy", "function", "worksheet_budget", "audit_budget", "budget_diff", "worksheet_activity", "audit_actual", "actual_diff"]


def gf_functions(amap):
    """General Fund expenditures by function: the audit's functions against the worksheet's departments grouped by departments.csv (County Corrections
    counts as Judiciary and courts, as the audit has it). The worksheet's Non-Departmental health insurance is shown as its own line."""
    dept = {(r["fund_code"], r["dept_code"]): r["function"] for r in E.table("departments.csv")}
    ws, au = defaultdict(int), defaultdict(int)
    for f in csv.DictReader(open(E.data_path("facts.csv"))):
        if f["fund_code"] != "001" or f["flow"] != "expense":
            continue
        fy = int(f["period_fy"])
        if f["source_type"] == "budget":
            if amap[("expense", f["account_key"])]["category_id"] == "exp_transfers":
                continue
            if f["basis"] in ("adopted", "prior_doc_budget") and f["doc_id"] != "FY2025_budget":
                continue  # one document per budget year: FY2025 adopted and FY2024 from the FY2025 document
            fn = "County-wide health insurance (Non-Departmental)" if f["account_key"] == "001:0000-414200" else dept[("001", f["dept_code"])]
            ws[(fy, f["basis"], fn)] += int(f["amount"])
        elif f["basis"] in ("audit_original", "audit_final", "actual_audited"):
            if f["basis"] == "audit_final" and "combined_column" in f["flag"]:
                continue
            fn = f["audit_section"].split(" | ")[0].capitalize() if f["audit_section"] else ""
            au[(fy, "actual" if f["basis"] == "actual_audited" else "budget", fn)] += int(f["amount"])
    names = {c["name"].lower(): c["name"] for c in E.categories("function_group")}
    names["debt service"] = "Debt service"
    rows = []
    for fy in (2024, 2025):
        budget_basis = "adopted" if fy == 2025 else "prior_doc_budget"
        for fn in sorted({k[2] for k in ws if k[0] == fy} | {names.get(k[2].lower(), k[2]) for k in au if k[0] == fy}):
            wb = ws.get((fy, budget_basis, fn), 0)
            wa = max((v for (y, b, f_), v in ws.items() if y == fy and b == "actual_prelim" and f_ == fn), default=0)
            ab = sum(v for (y, k, f_), v in au.items() if y == fy and k == "budget" and names.get(f_.lower(), f_) == fn)
            aa = sum(v for (y, k, f_), v in au.items() if y == fy and k == "actual" and names.get(f_.lower(), f_) == fn)
            rows.append(dict(entity_id=E.id, period_fy=fy, function=fn, worksheet_budget=wb, audit_budget=ab, budget_diff=ab - wb, worksheet_activity=wa,
                             audit_actual=aa, actual_diff=aa - wa))
    write_csv(E.data_path("audit_vs_budget_gf_functions.csv"), FUNCTION_COLUMNS, rows)


if __name__ == "__main__":
    main()
