"""One row per fiscal year for the Water / Sewer Fund: the audit's summary schedule (budget and actual), net position, and the budget.

Usage: python3 pipelines/belvidere_il/build_ws_summary.py   (run build_facts.py first)

Output: data/belvidere_il/ws_summary.csv. Audit columns exist for FY2012-FY2026;
budget columns for FY2015-FY2027 (the fund's department totals as printed, adopted basis, set-asides included). Amounts are dollars.
  operating_revenues_*     the audit's total operating revenues (charges for services)      *_original / *_actual
  operating_expenses_*     operating expenses excluding depreciation, by the budgetary basis (the actual includes capital outlay)
  net_position_*           from the statement of net position roll-forward (actual only)
  budget_revenues / budget_expenses   the budget document's Water + Sewer department totals (expenses include the set-asides)
"""
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
COLUMNS = ["entity_id", "fiscal_year", "operating_revenues_original", "operating_revenues_actual", "operating_expenses_original",
           "operating_expenses_actual", "depreciation_actual", "net_position_beginning", "net_position_change", "net_position_ending",
           "budget_revenues", "budget_expenses"]


def main():
    a = defaultdict(lambda: defaultdict(int))
    for f in read_csv(E.data_path("facts.csv")):
        if f["fund_code"] != "61" or f["source_type"] != "audit" or not f["audit_section"].startswith("Water and Sewer summary"):
            continue
        sec = f["audit_section"].split(" | ")[1].lower()
        fy = int(f["period_fy"])
        key = {"audit_original": "original", "actual_audited": "actual"}.get(f["basis"])
        if key is None:
            continue
        if sec.startswith("operating revenues"):
            a[fy]["rev_" + key] += int(f["amount"])
        elif f["label"].lower() == "depreciation":
            if key == "actual":
                a[fy]["dep"] += int(f["amount"])
        elif sec.startswith("operating expenses"):
            a[fy]["exp_" + key] += int(f["amount"])
    np = defaultdict(dict)
    for r in read_csv(E.data_path("fund_statements.csv")):  # the GAAP statement of revenues, expenses and changes in net position (FY2012-FY2026)
        if r["statement"] == "ws_stmt_income" and r["line_id"] in ("np_beginning", "np_restated", "np_ending", "change_in_net_position") \
                and "prior_year_comparative" not in r["flag"]:
            np[int(r["period_fy"])]["np_change" if r["line_id"] == "change_in_net_position" else r["line_id"]] = int(r["amount"])
    bud = defaultdict(lambda: defaultdict(int))
    for t in read_csv(E.data_path("control_totals.csv")):
        if t["kind"] in ("dept_revenues", "dept_expenses") and t["basis"] == "adopted" and t["name"].split(":")[0] in ("Water", "Sewer"):
            bud[int(t["period_fy"])][t["kind"]] += int(t["amount"])
    out = []
    for fy in sorted(set(a) | set(bud)):
        n = np.get(fy, {})
        out.append(dict(entity_id=E.id, fiscal_year=fy,
                        operating_revenues_original=a[fy].get("rev_original", ""), operating_revenues_actual=a[fy].get("rev_actual", ""),
                        operating_expenses_original=a[fy].get("exp_original", ""), operating_expenses_actual=a[fy].get("exp_actual", ""),
                        depreciation_actual=a[fy].get("dep", ""), net_position_beginning=n.get("np_restated", n.get("np_beginning", "")),
                        net_position_change=n.get("np_change", ""), net_position_ending=n.get("np_ending", ""),
                        budget_revenues=bud[fy].get("dept_revenues", "") if fy in bud else "", budget_expenses=bud[fy].get("dept_expenses", "") if fy in bud else ""))
    write_csv(E.data_path("ws_summary.csv"), COLUMNS, out)
    print(f"{len(out)} rows -> data/{E.id}/ws_summary.csv")
    for r in out:
        if r["net_position_ending"] != "":
            print(f"  FY{r['fiscal_year']}  op revenues {r['operating_revenues_actual']:>10,}  op expenses (budgetary) {r['operating_expenses_actual']:>10,}  net position {r['net_position_ending']:>11,}  budget rev {r['budget_revenues'] if r['budget_revenues'] == '' else format(r['budget_revenues'], ',')}")


if __name__ == "__main__":
    main()
