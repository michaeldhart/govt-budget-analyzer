"""Consolidate the per-year extracts of audit_pension_gaps.py into canonical optional tables and run the cross-year checks.

Writes data/belvidere_il/{pension_plan_net_position,pension_investment_allocation,pension_funding_progress,pension_employer_contributions}.csv (columns in
govbudget.schema.PENSION_DETAIL_TABLES), audit_pension_gaps_checks.csv (per-year validation counts) and audit_pension_gaps_crosschecks.csv (cross-year checks).
Run through `python3 pipelines/belvidere_il/audit_pension_gaps.py --build` (also called at the end of audit_debt_pension_build.main()).
"""
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from audit_debt_pension_common import DATA_E, ENTITY_ID, OUT_DIR  # noqa: E402
from govbudget.schema import PENSION_DETAIL_TABLES  # noqa: E402
import audit_pension_gaps as G  # noqa: E402

YEARS = range(2012, 2027)


def rd(fy, name):
    path = os.path.join(OUT_DIR, f"FY{fy}_audit_{name}.csv")
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def num(x):
    if x in ("", None):
        return None
    v = float(x)
    return int(v) if v == int(v) and "." not in str(x) else v


def exception_rules(fy):
    """allow overrides of audit_pension_gaps_FY20XX.csv that name an exception: (check key parts, exception id)."""
    return [(o["key"].split("|"), o["exception_id"]) for o in G.load_gap_overrides(fy) if o["kind"] == "allow" and o.get("exception_id")]


def tag(table, row, fy, rules):
    ids = set()
    for k, eid in rules:
        if table == "pension_plan_net_position":
            if k[:2] == ["npos", "tie_contrib"] and row["plan_id"] == k[2] and row["line_id"] == "employer_contributions" and row["source"] != "statement":
                ids.add(eid)
            if k[:2] == ["npos", "tie_fnp"] and row["plan_id"] == k[2] and row["line_id"] == "net_position" and row["statement"] == "net_position":
                ids.add(eid)
        elif table == "pension_employer_contributions" and k[:2] == ["contrib", "note"] and row["plan_id"] == k[2] and str(row["contribution_fy"]) == k[3]:
            ids.add(eid)
    if table == "pension_plan_net_position" and fy == 2012:
        if row["line_id"] == "investment_expense":
            ids.add("EX-adp-fy2012-expense-sign")
        if re.search(r"(?i)state and local", row["line_item"]) and row["statement"] == "net_position":
            ids.add("EX-adp-fy2012-ocr-gaps")
    if table == "pension_funding_progress" and row["flag"] == "ocr_point_comma":
        ids.add("EX-adp-funding-ocr-point")
    return ";".join(sorted(ids))


def tables():
    T = defaultdict(list)
    for fy in YEARS:
        rules = exception_rules(fy)
        for r in rd(fy, "pension_plan_net_position"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, statement=r["statement"], source=r["source"], plan_id=r["plan_id"], section=r["section"], line_item=r["line_item"],
                       line_id=r["line_id"], amount=r["amount"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("pension_plan_net_position", row, fy, rules)
            T["pension_plan_net_position"].append(row)
        for r in rd(fy, "pension_investment_allocation"):
            T["pension_investment_allocation"].append(dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], asset_class=r["asset_class"], range_text=r["range_text"],
                                                           target_pct=r["target_pct"], expected_return_pct=r["expected_return_pct"], expected_return_text=r["expected_return_text"],
                                                           pdf_page=r["pdf_page"], flag=r["flag"], exception_ids=""))
        for r in rd(fy, "pension_funding_progress"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], valuation_date=r["valuation_date"], actuarial_value_of_assets=r["actuarial_value_of_assets"],
                       accrued_liability=r["accrued_liability"], funded_ratio_pct=r["funded_ratio_pct"], unfunded_liability=r["unfunded_liability"], covered_payroll=r["covered_payroll"],
                       unfunded_pct_covered_payroll=r["unfunded_pct_covered_payroll"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("pension_funding_progress", row, fy, rules)
            T["pension_funding_progress"].append(row)
        for r in rd(fy, "pension_employer_contributions"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], contribution_fy=r["contribution_fy"], employer_contributions=r["employer_contributions"],
                       required_contribution=r["required_contribution"], pct_contributed=r["pct_contributed"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("pension_employer_contributions", row, fy, rules)
            T["pension_employer_contributions"].append(row)
    return T


class X:
    def __init__(self):
        self.rows = []

    def add(self, group, key, a, b, note="", tol=0):
        if a is None or b is None:
            self.rows.append([group, key, a, b, "", "n/a", note])
            return
        d = a - b
        self.rows.append([group, key, a, b, round(d, 4) if isinstance(d, float) else d, "pass" if abs(d) <= tol else "DIFF", note])


KNOWN = [
    (r"plan_net_position_chain.*FY2016", "FY2016 prior-period adjustment is not in the pension funds' statements; the opening balance equals the prior year's ending balance"),
]


def crosschecks(T):
    x = X()
    # plan net position: beginning(N) = ending(N-1) in the combining statement of changes (police, fire, total)
    end, beg = {}, {}
    for r in T["pension_plan_net_position"]:
        if r["source"] == "combining" and r["statement"] == "changes" and r["line_id"] in ("net_position_beginning", "net_position_ending"):
            (beg if r["line_id"] == "net_position_beginning" else end)[(int(r["period_fy"]), r["plan_id"])] = num(r["amount"])
    for (fy, plan), b in sorted(beg.items()):
        if (fy - 1, plan) in end:
            x.add("plan_net_position_chain", f"FY{fy - 1}->FY{fy} {plan}", end[(fy - 1, plan)], b, "ending net position (N-1) vs beginning (N)")
    # the same figure printed in two statements of one audit is checked inside the year; here: ending net position vs the pension note's FNP chain for FY2016+ is in audit_debt_pension_crosschecks
    # funding progress and contributions: a later audit repeats earlier valuation years
    fp = defaultdict(dict)
    for r in T["pension_funding_progress"]:
        for col in ("actuarial_value_of_assets", "accrued_liability", "unfunded_liability", "covered_payroll", "funded_ratio_pct", "unfunded_pct_covered_payroll"):
            fp[(r["plan_id"], r["valuation_date"], col)][int(r["period_fy"])] = num(r[col])
    for k, byd in sorted(fp.items()):
        ys = sorted(byd)
        for y0, y1 in zip(ys, ys[1:]):
            x.add("funding_progress_vs_earlier_audit", f"{k[0]} {k[1]} {k[2]} FY{y0} vs FY{y1}", byd[y1], byd[y0], "same valuation row in consecutive audits")
    ec = defaultdict(dict)
    for r in T["pension_employer_contributions"]:
        for col in ("employer_contributions", "required_contribution"):
            ec[(r["plan_id"], r["contribution_fy"], col)][int(r["period_fy"])] = num(r[col])
    for k, byd in sorted(ec.items()):
        ys = sorted(byd)
        for y0, y1 in zip(ys, ys[1:]):
            x.add("employer_contributions_vs_earlier_audit", f"{k[0]} FY{k[1]} {k[2]} FY{y0} vs FY{y1}", byd[y1], byd[y0], "same contribution row in consecutive audits")
    return x.rows


def main():
    T = tables()
    for name, rows in T.items():
        path = os.path.join(DATA_E, f"{name}.csv")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PENSION_DETAIL_TABLES[name])
            w.writeheader()
            w.writerows(rows)
        print(f"{os.path.relpath(path, os.path.join(HERE, '..', '..'))}: {len(rows):,} rows")
    out = []
    for fy in YEARS:
        out += [[ENTITY_ID, f"FY{fy}_audit", fy, r["check_group"], r["passed"], r["accepted_exceptions"], r["failed"]] for r in rd(fy, "pension_gaps_checks")]
    with open(os.path.join(DATA_E, "audit_pension_gaps_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "doc_id", "period_fy", "check_group", "passed", "accepted_exceptions", "failed"])
        w.writerows(out)
    xr = crosschecks(T)
    with open(os.path.join(DATA_E, "audit_pension_gaps_crosschecks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check_group", "key", "value_a", "value_b", "diff", "status", "note"])
        w.writerows(xr)
    from collections import Counter
    c = Counter((r[0], r[5]) for r in xr)
    for k in sorted(c):
        print(f"  {k[0]:42s} {k[1]:5s} {c[k]}")


if __name__ == "__main__":
    main()
