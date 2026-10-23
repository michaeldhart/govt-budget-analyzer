"""Compare the sum of an entity's fact lines with the totals the source prints (control_totals.csv).

Usage: python3 -m govbudget.reconcile <entity_id>      -> prints a summary and writes data/<entity_id>/reconciliation.csv

One row per document x period x basis for the fund totals (revenues and expenses). `diff` = printed - sum of lines.
A non-zero diff is not necessarily an extraction error: the source's own arithmetic is sometimes off or leaves lines
out of its totals (see exceptions.csv); the extractor's per-document validation is where those cases are explained,
this table is how a consumer sees them. Expense totals include contra_expense facts (revenue-coded reimbursements the
source prints in the expense block, which the printed expense subtotals count as negative expense).
"""
import sys
from collections import defaultdict

from .entity import Entity, read_csv, write_csv

COLUMNS = ["entity_id", "doc_id", "period_fy", "basis", "kind", "printed", "lines", "diff"]


def reconcile(E):
    sums = defaultdict(int)
    for f in read_csv(E.data_path("facts.csv")):
        if f["source_type"] != "budget":
            continue
        k = (f["doc_id"], int(f["period_fy"]), f["basis"])
        if f["flow"] == "revenue":
            sums[k + ("fund_revenues",)] += int(f["amount"])
        elif f["flow"] in ("expense", "contra_expense"):
            sums[k + ("fund_expenses",)] += int(f["amount"])
    rows = []
    for t in read_csv(E.data_path("control_totals.csv")):
        if t["kind"] not in ("fund_revenues", "fund_expenses"):
            continue
        k = (t["doc_id"], int(t["period_fy"]), t["basis"], t["kind"])
        # revenue lines the source prints in the expense block are in sums["fund_expenses"], not in revenues
        lines = sums[k]
        rows.append(dict(entity_id=E.id, doc_id=t["doc_id"], period_fy=t["period_fy"], basis=t["basis"], kind=t["kind"],
                         printed=t["amount"], lines=lines, diff=int(t["amount"]) - lines))
    rows.sort(key=lambda r: (r["doc_id"], int(r["period_fy"]), r["basis"], r["kind"]))
    return rows


def main():
    E = Entity.load(sys.argv[1])
    rows = reconcile(E)
    write_csv(E.data_path("reconciliation.csv"), COLUMNS, rows)
    exact = sum(1 for r in rows if r["diff"] == 0)
    small = sum(1 for r in rows if r["diff"] and abs(r["diff"]) <= 3)
    print(f"{len(rows)} fund totals: {exact} tie exactly, {small} are off by $1-3, {len(rows) - exact - small} differ more -> data/{E.id}/reconciliation.csv")
    for r in rows:
        if abs(r["diff"]) > 3:
            print(f"  {r['doc_id']} {r['basis']:17} FY{r['period_fy']} {r['kind']:14} printed {int(r['printed']):>12,}  lines {r['lines']:>12,}  diff {r['diff']:>+10,}")


if __name__ == "__main__":
    main()
