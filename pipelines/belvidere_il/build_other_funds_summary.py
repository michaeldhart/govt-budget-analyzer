"""One row per fiscal year x non-General, non-Water/Sewer governmental fund: the audit's budget-and-actual schedule beside the budget document.

Usage: python3 pipelines/belvidere_il/build_other_funds_summary.py   (run build_facts.py first)

Inputs : data/belvidere_il/extracted/audits/FY20XX_audit_fund_schedules.csv   audit totals per fund (original, final, actual)
         data/belvidere_il/control_totals.csv                                  the budget ordinances' printed fund totals (other_fund_*)
         entities/belvidere_il/funds.csv                                       which audit fund is which budget fund (and how sure that is)
Output : data/belvidere_il/other_funds_summary.csv

Audit columns come from each fund's schedule: total revenues and expenditures (original budget and actual), net other financing, net change
in fund balance and the beginning and ending fund balance. Budget columns are the ordinance's adopted Total Revenues / Expenditures for the
same fiscal year. `match` says how far to trust a comparison: funds.csv budget_match `verified` means the audit's original budget equals the
budget page in many year/total comparisons; `by_name` or blank means the funds are the same only by name (Capital Projects: the budget has
two ledgers and counts transfers in as revenue) or not proven, and the diff columns are left blank.
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
COLUMNS = ["entity_id", "fiscal_year", "fund_code", "fund", "revenues_original", "revenues_actual", "expenditures_original", "expenditures_actual",
           "net_other_financing_actual", "net_change_actual", "beginning_balance", "ending_balance", "budget_revenues", "budget_expenses",
           "revenues_diff", "expenditures_diff", "match"]


def num(x):
    return int(x) if x not in ("", None) else ""


def main():
    funds = {r["audit_name"]: r for r in E.table("funds.csv") if r["audit_name"]}
    rows = defaultdict(dict)  # (fy, fund_code) -> measures
    for path in sorted(glob.glob(E.data_path("extracted", "audits", "FY*_audit_fund_schedules.csv"))):
        for r in csv.DictReader(open(path)):
            fm = funds.get(r["fund"])
            k = r["line_kind"]
            if fm is None or k not in ("total_revenues", "total_expenditures", "total_ofs", "net_change", "fb_begin", "fb_restated", "fb_end"):
                continue
            d = rows[(int(r["fiscal_year"]), fm["fund_code"])]
            d["fund"] = fm["name"]
            if k == "total_revenues":
                d["rev_o"], d["rev_a"] = r["original_budget"], r["actual"]
            elif k == "total_expenditures":
                d["exp_o"], d["exp_a"] = r["original_budget"], r["actual"]
            elif k == "total_ofs":
                d["ofs"] = r["actual"]
            elif k == "net_change":
                d["chg"] = r["actual"]
            elif k == "fb_begin":
                d.setdefault("beg", r["actual"])
            elif k == "fb_restated":
                d["beg"] = r["actual"]
            elif k == "fb_end":
                d["end"] = r["actual"]
    bud = defaultdict(int)
    for t in read_csv(E.data_path("control_totals.csv")):
        if t["kind"] in ("other_fund_revenues", "other_fund_expenses") and t["basis"] == "adopted":
            m = re.search(r"#(\d+)", t["name"])
            code = m.group(1) if m else ("PP" if t["name"].startswith("Police Pension") else "FP" if t["name"].startswith("Fire Pension") else "")
            bud[(int(t["period_fy"]), code, t["kind"])] += int(t["amount"])
    out = []
    keys = set(rows) | {(fy, code) for fy, code, _ in bud}
    for fy, code in sorted(keys):
        d = rows.get((fy, code), {})
        fm = next((r for r in E.table("funds.csv") if r["fund_code"] == code), None)
        br, be = bud.get((fy, code, "other_fund_revenues"), ""), bud.get((fy, code, "other_fund_expenses"), "")
        verified = fm is not None and fm["budget_match"] == "verified"
        rd = (num(d.get("rev_o", "")) - br) if verified and d.get("rev_o", "") != "" and br != "" else ""
        ed = (num(d.get("exp_o", "")) - be) if verified and d.get("exp_o", "") != "" and be != "" else ""
        out.append(dict(entity_id=E.id, fiscal_year=fy, fund_code=code, fund=d.get("fund", fm["name"] if fm else code),
                        revenues_original=num(d.get("rev_o", "")), revenues_actual=num(d.get("rev_a", "")), expenditures_original=num(d.get("exp_o", "")),
                        expenditures_actual=num(d.get("exp_a", "")), net_other_financing_actual=num(d.get("ofs", "")), net_change_actual=num(d.get("chg", "")),
                        beginning_balance=num(d.get("beg", "")), ending_balance=num(d.get("end", "")), budget_revenues=br, budget_expenses=be,
                        revenues_diff=rd, expenditures_diff=ed, match=(fm["budget_match"] if fm else "") or "none"))
    write_csv(E.data_path("other_funds_summary.csv"), COLUMNS, out)
    cmp_ = [r for r in out if r["revenues_diff"] != "" or r["expenditures_diff"] != ""]
    exact = [r for r in cmp_ if r["revenues_diff"] in ("", 0) and r["expenditures_diff"] in ("", 0)]
    print(f"{len(out)} fund-years -> data/{E.id}/other_funds_summary.csv; verified funds: {len(exact)} of {len(cmp_)} year comparisons agree with the budget to the dollar")
    for r in cmp_:
        if r["revenues_diff"] not in ("", 0) or r["expenditures_diff"] not in ("", 0):
            print(f"  FY{r['fiscal_year']} {r['fund']:40} revenues diff {r['revenues_diff']!s:>9}  expenditures diff {r['expenditures_diff']!s:>9}")


if __name__ == "__main__":
    main()
