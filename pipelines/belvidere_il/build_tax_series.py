"""Build Belvidere's multi-year General Fund tax series from the canonical tables.

Usage: python3 pipelines/belvidere_il/build_tax_series.py   (run build_facts.py first)

Inputs : data/belvidere_il/facts.csv, accounts.csv     (build_facts.py)
         entities/belvidere_il/{account_map,categories,exceptions}.csv   (which accounts are taxes, and how)
Outputs: data/belvidere_il/tax_series_long.csv         one row per fiscal year x measure x tax account
         data/belvidere_il/tax_series_by_category.csv  gross taxes, offsets and net per year x measure x category

Methodology (see CLAUDE.md): the account list, categories and treatments come from account_map.csv (31 tax-relevant
accounts). Gross taxes (treatment `tax`) are reported separately from offsets (negative, `offset`); fees
(`excluded_fee`) are left out of both. Accounts are keyed on department + object code (entity.json key_segments).
Only flow `revenue` rows are used, so expense-block rebates (flow `contra_expense`) never enter the series.

Measures for fiscal year Y (measure -> canonical basis; see govbudget.schema.BASES):
  budget_adopted      basis `adopted` from the document of year Y: the ordinance's adopted budget. The audits' ORIGINAL and
                      FINAL budget columns equal it on 134 of 135 comparable line-years (build_audit_compare.py). FY2014
                      has no extracted document of its own, so it is document FY2015's prior-year budget column.
  budget_next_doc     basis `prior_doc_budget` from document Y+1. Equals budget_adopted except FY2024 utility taxes and
                      the FY2025 Audit and IMRF levies, where the audit shows the own-document figure is the budget
                      actually adopted (exceptions EX-stale-*), so those later columns are NOT used.
  budget_revised      basis `revised` from document Y+1 (only documents FY2015-FY2022)
  actual_budget_doc   basis `actual_prelim` from document Y+2 (preliminary; in some years the replacement-tax pension
                      distribution is a copy of the budget). Audited actuals are in tax_series_audit_basis.csv.
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
FIRST_FY, LAST_FY = 2013, 2027
LONG_COLS = ["entity_id", "fiscal_year", "measure", "basis", "account", "department", "line_item", "amount", "category",
             "treatment", "source_doc", "source_column", "source_page_label", "note", "exception_ids"]
SUMMARY_COLS = ["entity_id", "fiscal_year", "measure", "basis", "category", "is_rollup", "gross_tax", "offsets", "net"]
ROLLUP = "ALL TAXES"


def load_facts():
    """index[(doc_fy, key, period_fy, basis)] -> fact row (revenue only, mapped tax-relevant accounts only)."""
    amap = {k: m for (flow, k), m in E.account_map().items() if flow == "revenue" and m["treatment"] != "non_tax"}
    idx, latest = {}, {}
    for r in read_csv(E.data_path("facts.csv")):
        if r["source_type"] != "budget" or r["flow"] != "revenue" or r["account_key"] not in amap:
            continue
        doc_fy = int(r["doc_id"][2:6])
        slot = (doc_fy, r["account_key"], int(r["period_fy"]), r["basis"])
        if slot in idx:
            sys.exit(f"{r['doc_id']}: two rows for {r['account_key']} {r['source_column']}: {idx[slot]['label']!r} and {r['label']!r}")
        idx[slot] = r
        if r["basis"] == "adopted":
            latest[r["account_key"]] = r  # documents are in fiscal-year order: the last adopted row is the newest
    return amap, idx, latest


def build():
    amap, idx, latest = load_facts()
    cat_name = {c["category_id"]: c["name"] for c in E.categories("budget_category")}
    docs = {y for (y, *_rest) in idx}
    out = []

    def emit(fy, measure, basis, key, row, note=""):
        m = amap[key]
        out.append(dict(entity_id=E.id, fiscal_year=fy, measure=measure, basis=basis, account=latest[key]["account_printed"],
                        department=m["department"], line_item=m["label"], amount=int(row["amount"]),
                        category=cat_name[m["category_id"]] if m["treatment"] != "excluded_fee" else "",
                        treatment=m["treatment"], source_doc=row["doc_id"], source_column=row["source_column"],
                        source_page_label=row["page_label"], note=note, exception_ids=row["exception_ids"]))

    for fy in range(FIRST_FY, LAST_FY + 1):
        for key in amap:
            own = (fy, key, fy, "adopted")
            if own in idx:
                emit(fy, "budget_adopted", "adopted", key, idx[own])
            elif fy == 2014 and (2015, key, 2014, "prior_doc_budget") in idx:
                emit(fy, "budget_adopted", "adopted", key, idx[(2015, key, 2014, "prior_doc_budget")],
                     "no FY2014 document extracted; document FY2015's prior-year column (audit FY2014 original budget agrees)")
            for measure, basis, doc in (("budget_next_doc", "prior_doc_budget", fy + 1), ("budget_revised", "revised", fy + 1),
                                        ("actual_budget_doc", "actual_prelim", fy + 2)):
                if doc in docs and (doc, key, fy, basis) in idx:
                    emit(fy, measure, basis, key, idx[(doc, key, fy, basis)])
    return amap, out


def summarize(out):
    agg = defaultdict(lambda: dict(gross=0, offsets=0))
    basis_of = {}
    for r in out:
        basis_of[r["measure"]] = r["basis"]
        if r["treatment"] not in ("tax", "offset"):
            continue
        for cat in (r["category"], ROLLUP):
            a = agg[(r["fiscal_year"], r["measure"], cat)]
            a["gross" if r["treatment"] == "tax" else "offsets"] += r["amount"]
    return [dict(entity_id=E.id, fiscal_year=fy, measure=measure, basis=basis_of[measure], category=cat,
                 is_rollup=int(cat == ROLLUP), gross_tax=a["gross"], offsets=a["offsets"], net=a["gross"] + a["offsets"])
            for (fy, measure, cat), a in sorted(agg.items())]


def main():
    amap, out = build()
    out.sort(key=lambda r: (r["fiscal_year"], r["measure"], r["account"]))
    summ = summarize(out)

    # The FY2027 tax lines must reproduce the totals recorded in CLAUDE.md ...
    t = next(r for r in summ if (r["fiscal_year"], r["measure"], r["category"]) == (2027, "budget_adopted", ROLLUP))
    assert (t["gross_tax"], t["offsets"], t["net"]) == (20998468, -532141, 20466327), t  # incl. the $62,000 infrastructure maintenance line, a tax since 2026-10-22
    # ... and each FY2027 line must equal the independently transcribed FY2027 file (data/belvidere_il/fy2027_general_fund_tax_lines.csv).
    fy27 = {E.account_key(r["account"]): int(r["amount"]) for r in read_csv(E.data_path("fy2027_general_fund_tax_lines.csv"))}
    n = 0
    for r in out:
        if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted":
            assert r["amount"] == fy27[E.account_key(r["account"])], r
            n += 1
    assert n == len(fy27) == 31, (n, len(fy27))

    write_csv(E.data_path("tax_series_long.csv"), LONG_COLS, out)
    write_csv(E.data_path("tax_series_by_category.csv"), SUMMARY_COLS, summ)
    print(f"{len(out)} long rows -> data/{E.id}/tax_series_long.csv; {len(summ)} summary rows -> data/{E.id}/tax_series_by_category.csv")
    print("FY2027 reproduces gross $20,998,468 / offsets -$532,141 / net $20,466,327")

    print("\nAll taxes, gross (offsets), by fiscal year:")
    print(f"{'FY':>4} {'adopted':>22} {'next-doc col':>22} {'actual (budget doc)':>22}")
    by = {(r["fiscal_year"], r["measure"]): r for r in summ if r["is_rollup"]}
    for fy in range(FIRST_FY, LAST_FY + 1):
        cells = []
        for m in ("budget_adopted", "budget_next_doc", "actual_budget_doc"):
            r = by.get((fy, m))
            cells.append(f"{r['gross_tax']:>12,} ({r['offsets']:>8,})" if r else " " * 22)
        print(f"{fy:>4} " + " ".join(cells))


if __name__ == "__main__":
    main()
