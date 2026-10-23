"""Join the per-year budget extracts into one multi-year General Fund tax series.

Usage: python scripts/build_tax_series.py

Inputs : data/extracted/FY2015..FY2027_budget_gf_revenue.csv (scripts/extract_budget.py)
         data/fy2027_general_fund_tax_lines.csv (the category/treatment mapping)
Outputs: data/tax_series_long.csv         one row per fiscal year x measure x tax account
         data/tax_series_by_category.csv  gross taxes, offsets and net per year x measure x category

Methodology (see CLAUDE.md): the account list, categories and treatments are exactly those of the
FY2027 file. Gross taxes (treatment `tax`) are reported separately from offsets (negative,
`offset`); fees (`excluded_fee`, now only franchise fees) are left out of both. Accounts are keyed on department + object
code (segments 3 and 4), because the city flips the second segment between 4 and 5 for some lines.

Measures for fiscal year Y:
  budget_adopted      the budget document's own "Budget Request" column (document Y), which is the ordinance's
                      adopted budget: the audits' ORIGINAL and FINAL budget columns equal it on 134 of 135
                      comparable line-years (scripts/build_audit_compare.py). FY2014 has no extracted document
                      of its own, so it is document FY2015's prior-year budget column. FY2027 is the ordinance.
  budget_next_doc     the following document's prior-year plain "Budget" column. Equals budget_adopted except FY2024
                      utility taxes and the FY2025 Audit and IMRF levies, where the audit shows the own-document
                      figure is the budget actually adopted, so those later columns are NOT used.
  budget_revised      later document's REVISED budget column (document Y+1; only documents FY2015-FY2022)
  actual_budget_doc   document Y+2's actual column (the city's own reported actuals; preliminary, and in some
                      years the replacement-tax pension distribution is a copy of the budget). Audited actuals are
                      in tax_series_audit_basis.csv (scripts/build_audit_compare.py), on the audit's own groups.
"""
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
DOCS = range(2015, 2028)


def key_of(account):
    """Department + object code. Accepts the city's no-hyphen typo '01-4-1104114'."""
    p = account.replace("1104114", "110-4114").split("-")
    return f"{p[2]}-{p[3]}"


def canonical(account):
    return account.replace("1104114", "110-4114")


def load_mapping():
    m = {}
    for r in csv.DictReader(open(os.path.join(DATA, "fy2027_general_fund_tax_lines.csv"))):
        m[key_of(r["account"])] = dict(account=canonical(r["account"]), department=r["department"],
                                       line_item=r["line_item"], category=r["category"],
                                       treatment=r["treatment"], fy27=int(r["amount"]))
    return m


def load_extracts(mapping):
    """ex[doc_year][(key, column)] = row, for mapped tax-relevant keys only."""
    ex = {}
    for y in DOCS:
        d = {}
        for r in csv.DictReader(open(os.path.join(DATA, "extracted", f"FY{y}_budget_gf_revenue.csv"))):
            k = key_of(r["account"])
            if k not in mapping:
                continue
            if "expense_block_offset" in r["flag"]:
                continue  # e.g. 01-5-110-4013 Telecom Tax Rebate shares an object code with a fee line
            slot = (k, r["column"])
            if slot in d:
                sys.exit(f"FY{y} document: two rows for {k} {r['column']}: {d[slot]['line_item']!r} and {r['line_item']!r}")
            d[slot] = r
        ex[y] = d
    return ex


def build():
    mapping = load_mapping()
    ex = load_extracts(mapping)
    out = []  # long rows

    def emit(fy, measure, k, row, doc, column, note=""):
        m = mapping[k]
        out.append(dict(fiscal_year=fy, measure=measure, account=m["account"], department=m["department"],
                        line_item=m["line_item"], amount=int(row["amount"]), category=m["category"],
                        treatment=m["treatment"], source_doc=f"FY{doc}_budget", source_column=column,
                        source_page_label=row["page_label"], note=note))

    for fy in range(2013, 2028):
        for k in mapping:
            own = (k, f"budget_FY{fy}") in ex[fy] if fy in DOCS else False
            nxt = fy + 1
            # budget_adopted: own document (FY2015+); FY2014 from document FY2015's prior-year column
            if own:
                emit(fy, "budget_adopted", k, ex[fy][(k, f"budget_FY{fy}")], fy, f"budget_FY{fy}")
            elif fy == 2014 and (k, "budget_FY2014") in ex[2015]:
                emit(fy, "budget_adopted", k, ex[2015][(k, "budget_FY2014")], 2015, "budget_FY2014",
                     "no FY2014 document extracted; document FY2015's prior-year column (audit FY2014 original budget agrees)")
            # budget_next_doc / budget_revised: next document's prior-year columns
            if nxt in DOCS:
                if (k, f"budget_FY{fy}") in ex[nxt]:
                    emit(fy, "budget_next_doc", k, ex[nxt][(k, f"budget_FY{fy}")], nxt, f"budget_FY{fy}")
                if (k, f"budget_revised_FY{fy}") in ex[nxt]:
                    emit(fy, "budget_revised", k, ex[nxt][(k, f"budget_revised_FY{fy}")], nxt, f"budget_revised_FY{fy}")
            # actual_budget_doc: document two years later
            if fy + 2 in DOCS and (k, f"actual_FY{fy}") in ex[fy + 2]:
                emit(fy, "actual_budget_doc", k, ex[fy + 2][(k, f"actual_FY{fy}")], fy + 2, f"actual_FY{fy}")
    return mapping, out


def summarize(out):
    agg = defaultdict(lambda: dict(gross=0, offsets=0))
    for r in out:
        if r["treatment"] not in ("tax", "offset"):
            continue
        for cat in (r["category"], "ALL TAXES"):
            a = agg[(r["fiscal_year"], r["measure"], cat)]
            a["gross" if r["treatment"] == "tax" else "offsets"] += r["amount"]
    rows = []
    for (fy, measure, cat), a in sorted(agg.items()):
        rows.append(dict(fiscal_year=fy, measure=measure, category=cat, gross_tax=a["gross"],
                         offsets=a["offsets"], net=a["gross"] + a["offsets"]))
    return rows


def main():
    mapping, out = build()
    out.sort(key=lambda r: (r["fiscal_year"], r["measure"], r["account"]))
    summ = summarize(out)

    # The FY2027 tax lines must reproduce the totals recorded in CLAUDE.md.
    t = next(r for r in summ if (r["fiscal_year"], r["measure"], r["category"]) == (2027, "budget_adopted", "ALL TAXES"))
    assert (t["gross_tax"], t["offsets"], t["net"]) == (20998468, -532141, 20466327), t  # incl. the $62,000 infrastructure maintenance line, a tax since 2026-10-22
    for r in out:  # and each FY2027 line must equal the mapping file's amount
        if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted":
            assert r["amount"] == mapping[r["account"].split("-")[2] + "-" + r["account"].split("-")[3]]["fy27"], r

    cols = ["fiscal_year", "measure", "account", "department", "line_item", "amount", "category", "treatment",
            "source_doc", "source_column", "source_page_label", "note"]
    with open(os.path.join(DATA, "tax_series_long.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(out)
    with open(os.path.join(DATA, "tax_series_by_category.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["fiscal_year", "measure", "category", "gross_tax", "offsets", "net"])
        w.writeheader()
        w.writerows(summ)
    print(f"{len(out)} long rows -> data/tax_series_long.csv; {len(summ)} summary rows -> data/tax_series_by_category.csv")
    print("FY2027 reproduces gross $20,998,468 / offsets -$532,141 / net $20,466,327")

    print("\nAll taxes, gross (offsets), by fiscal year:")
    print(f"{'FY':>4} {'adopted':>22} {'next-doc col':>22} {'actual (budget doc)':>22}")
    by = {(r["fiscal_year"], r["measure"]): r for r in summ if r["category"] == "ALL TAXES"}
    for fy in range(2013, 2028):
        cells = []
        for m in ("budget_adopted", "budget_next_doc", "actual_budget_doc"):
            r = by.get((fy, m))
            cells.append(f"{r['gross_tax']:>12,} ({r['offsets']:>8,})" if r else " " * 22)
        print(f"{fy:>4} " + " ".join(cells))


if __name__ == "__main__":
    main()
