"""General Fund revenue by source: the budget documents' accounts against the audits' revenue lines, by comparable group -> data/boone_county_il/audit_revenue_by_source.csv.

One row per fiscal year x group: the budget amount (the year's adopted budget where a document has one, else the earliest later document's prior-year column), the audit's original
(or final) budget, the audit's actual, and the worksheet/book 'activity' (a preliminary actual).  Groups: entities/boone_county_il/comparable_groups.csv and audit_map.csv
(build_revenue_groups.py).  Transfers in and sale proceeds are not revenue by source (the audit reports them as other financing).
Usage: python3 pipelines/boone_county_il/build_audit_revenue_compare.py
"""
import csv, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402
from build_revenue_groups import audit_group, budget_group  # noqa: E402

E = Entity.load("boone_county_il")
COLUMNS = ["entity_id", "period_fy", "comparable_group", "report_group", "budget_basis", "budget_doc", "budget", "audit_budget_basis", "audit_budget", "budget_diff",
           "worksheet_activity", "audit_actual", "actual_diff", "budget_status", "actual_status"]


def main():
    rep = {r["comparable_group"]: r["report_group"] for r in E.table("comparable_groups.csv")}
    cat = {r["account_key"]: r["category_id"] for r in E.table("account_map.csv") if r["flow"] == "revenue"}
    bud = defaultdict(int)   # (fy, group, basis, doc)
    aud = defaultdict(int)   # (fy, group, basis)
    for f in csv.DictReader(open(E.data_path("facts.csv"))):
        fy, amt = int(f["period_fy"]), int(f["amount"])
        if f["fund_code"] != "001" or f["flow"] != "revenue":
            continue
        if f["source_type"] == "budget":
            g = budget_group(f["account_key"], f["label"], cat.get(f["account_key"]))
            if g and f["basis"] in ("adopted", "prior_doc_budget", "actual_prelim"):
                bud[(fy, g, f["basis"], f["doc_id"])] += amt
        elif f["flag"] == "" or "other_financing" not in f["flag"]:
            heading = f["audit_section"].split(" | ")[0]
            g = audit_group(heading, f["label"])
            if g is None:
                sys.exit(f"{f['doc_id']}: audit revenue line {heading!r} / {f['label']!r} has no comparable group (audit_map.csv)")
            if f["basis"] == "audit_final" and "combined_column" in f["flag"]:
                continue
            aud[(fy, g, f["basis"])] += amt
    rows = []
    for fy in range(2015, 2026):
        for g in rep:
            cands = sorted(((b, d, v) for (y, gg, b, d), v in bud.items() if y == fy and gg == g and b in ("adopted", "prior_doc_budget")), key=lambda c: (c[0] != "adopted", c[1]))
            bb, bdoc, bv = cands[0] if cands else ("", "", "")
            ab_basis = "audit_original" if (fy, g, "audit_original") in aud else ("audit_final" if (fy, g, "audit_final") in aud else "")
            ab = aud.get((fy, g, ab_basis)) if ab_basis else ""
            aa = aud.get((fy, g, "actual_audited"), "")
            act = sorted(((d, v) for (y, gg, b, d), v in bud.items() if y == fy and gg == g and b == "actual_prelim"))
            wa = act[-1][1] if act else ""
            bd = (ab - bv) if (bv != "" and ab != "") else ""
            ad = (aa - wa) if (wa != "" and aa != "") else ""
            if bv == "" and ab == "" and aa == "":
                continue
            rows.append(dict(entity_id=E.id, period_fy=fy, comparable_group=g, report_group=rep[g], budget_basis=bb, budget_doc=bdoc, budget=bv, audit_budget_basis=ab_basis,
                             audit_budget=ab, budget_diff=bd, worksheet_activity=wa, audit_actual=aa, actual_diff=ad,
                             budget_status="" if bd == "" else ("agree" if bd == 0 else "differs"), actual_status="" if ad == "" else ("agree" if ad == 0 else "differs")))
    write_csv(E.data_path("audit_revenue_by_source.csv"), COLUMNS, rows)
    b = [r for r in rows if r["budget_status"]]
    print(len(rows), "rows;", sum(r["budget_status"] == "agree" for r in b), "of", len(b), "budget comparisons agree")


if __name__ == "__main__":
    main()
