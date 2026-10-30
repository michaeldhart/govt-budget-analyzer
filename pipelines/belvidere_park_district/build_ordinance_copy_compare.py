"""Compare the appropriation ordinance copies appended to budget documents with the standalone ordinance of the same year.

Usage: python3 pipelines/belvidere_park_district/build_ordinance_copy_compare.py

The copies (entities/belvidere_park_district/ordinance_copies.csv; extracted by extract_ordinance_appropriation.py <copy_id>) are not documents of their own, so
they are not in ordinance_lines.csv; this derived table puts every line and SUMMARY item of a copy beside the other ordinance. Lines are matched by position inside
(fund, section) because the scans misspell the labels; `label_similarity` flags a pair whose labels differ more than the OCR noise explains.
Output: data/belvidere_park_district/ordinance_copy_comparison.csv (entity_id, copy_id, compared_to, fiscal_year, fund, section, position, line_item_copy,
line_item_other, amount_copy, amount_other, diff, printed, status, exception_ids); status is same | amount_differs | only_in_copy | only_in_other.
"""
import csv
import difflib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from decimal import Decimal  # noqa: E402
import extract_budget_report as R  # noqa: E402

ENTITY = R.ENTITY
# (copy, ordinance compared with, exception ids that explain its differences)
PAIRS = [
    ("FY2023_appropriation_in_draft", "FY2023_appropriation", ""),
    ("FY2024_appropriation_in_draft", "FY2024_appropriation", ""),
    ("FY2023_appropriation_amended_oct", "FY2023_appropriation", "EX-ordinance-fy2023-amended-oct"),
    ("FY2023_appropriation_amended_oct", "FY2023_appropriation_in_draft", "EX-ordinance-fy2023-amended-oct"),
]
COLUMNS = ["entity_id", "copy_id", "compared_to", "fiscal_year", "fund", "section", "position", "line_item_copy", "line_item_other", "amount_copy",
           "amount_other", "diff", "printed", "status", "label_similarity", "exception_ids"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load(ent, doc):
    base = ent.data_path("extracted", "ordinances")
    if os.path.exists(os.path.join(base, f"{doc}_lines.csv")):
        lines = list(csv.DictReader(open(os.path.join(base, f"{doc}_lines.csv"))))
        summ = {r["item"]: r["amount"] for r in csv.DictReader(open(os.path.join(base, f"{doc}_summary.csv")))}
        return lines, summ
    raise SystemExit(f"{doc}: no extract")


def by_group(lines):
    g = {}
    for r in lines:
        g.setdefault((r["fund"], r["section"]), []).append(r)
    return g


def main():
    ent = R.Entity.load(ENTITY)
    out = []
    for copy, other, ex in PAIRS:
        a, sa = load(ent, copy)
        b, sb = load(ent, other)
        ga, gb = by_group(a), by_group(b)
        for key in list(ga) + [k for k in gb if k not in ga]:
            la, lb = ga.get(key, []), gb.get(key, [])
            for i in range(max(len(la), len(lb))):
                x = la[i] if i < len(la) else None
                y = lb[i] if i < len(lb) else None
                ax = Decimal(x["amount"]) if x else None
                ay = Decimal(y["amount"]) if y else None
                status = "only_in_copy" if y is None else "only_in_other" if x is None else "same" if ax == ay else "amount_differs"
                sim = difflib.SequenceMatcher(None, norm(x["line_item"]), norm(y["line_item"])).ratio() if x and y else ""
                out.append([ENTITY, copy, other, (x or y)["fiscal_year"], key[0], key[1], i + 1,
                            x["line_item"] if x else "", y["line_item"] if y else "", R.clean(ax) if x else "", R.clean(ay) if y else "",
                            R.clean(ax - ay) if x and y else "", (x or y)["printed"], status, f"{sim:.2f}" if sim != "" else "",
                            ex if status != "same" else ""])
        for k in sorted(set(sa) | set(sb)):
            ax = Decimal(sa[k]) if k in sa else None
            ay = Decimal(sb[k]) if k in sb else None
            status = "only_in_copy" if ay is None else "only_in_other" if ax is None else "same" if ax == ay else "amount_differs"
            out.append([ENTITY, copy, other, copy[2:6], "(summary)", "summary", k, k, k, R.clean(ax) if ax is not None else "", R.clean(ay) if ay is not None else "",
                        R.clean(ax - ay) if ax is not None and ay is not None else "", 1, status, "", ex if status != "same" else ""])
    path = ent.data_path("ordinance_copy_comparison.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(out)
    for copy, other, _ in PAIRS:
        rs = [r for r in out if r[1] == copy and r[2] == other]
        n = {s: sum(r[13] == s for r in rs) for s in ("same", "amount_differs", "only_in_copy", "only_in_other")}
        print(f"{copy} vs {other}: {len(rs)} items {n}")


if __name__ == "__main__":
    main()
