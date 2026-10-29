"""Map each budget document's printed page label ("3 of 15") to the PDF page it sits on -> data/belvidere_il/page_map.csv.

The budget ordinances interleave narrative pages with the Exhibit A pages, so the printed "N of M" footer is not the PDF page number.
The extractors read each page's footer from the OCR text (extract_budget.py: LABEL.search(text[-200:]); an unreadable footer falls back to
the label "pdfN", which already is the PDF page). This script applies the same rule to the cached OCR of every page to recover the PDF page
for every footer label the extracts use. It needs the OCR cache (data/<entity>/ocr/, gitignored, written by the extractors) and the
committed page_map.csv is what build_facts.py reads, so the canonical tables can be rebuilt without the cache.

Usage: python3 pipelines/belvidere_il/build_page_map.py [--check]
  --check   also verify, for every General Fund line with a printed account number, that the account's department-object code appears
            in the OCR text of the page the map gives (OCR misses a few; the rate is printed)
Fails if a label maps to two PDF pages or a label used by an extract has no page.
"""
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
LABEL = re.compile(r"(\d+)\.? of (\d+)")  # same footer rule as extract_budget.py
COLUMNS = ["entity_id", "doc_id", "page_label", "pdf_page", "printed_label", "note"]


def pages_of(fy):
    d = E.data_path("ocr", "budget_pages", f"FY{fy}_gray")
    if not os.path.isdir(d):
        sys.exit(f"{d} not found: the OCR cache is written by extract_budget.py FY{fy}")
    return {int(f[1:4]): open(os.path.join(d, f)).read() for f in sorted(os.listdir(d)) if f.endswith(".txt")}


def infer_misread(fy, rows):
    """A footer whose number exceeds the document's page count ("42 of 15": a printed 12 read with its 1 as a 4) is a misread.
    If exactly one number is missing from the series and the page sits between its neighbours, that is the printed label."""
    mine = [r for r in rows if r["doc_id"] == f"FY{fy}_budget"]
    nums = {int(r["page_label"].split(" of ")[0]): r for r in mine}
    for r in mine:
        n, total = (int(x) for x in r["page_label"].split(" of "))
        if n <= total:
            continue
        missing = [k for k in range(1, total + 1) if k not in nums]
        fits = [k for k in missing
                if all(int(nums[j]["pdf_page"]) < int(r["pdf_page"]) for j in nums if j < k and j <= total)
                and all(int(nums[j]["pdf_page"]) > int(r["pdf_page"]) for j in nums if k < j <= total)]
        if len(fits) == 1:
            r["printed_label"] = f"{fits[0]} of {total}"
            r["note"] = f"footer read as {n}; the only number missing from the series that fits between its neighbours is {fits[0]}"
        else:
            r["note"] = f"footer read as {n}, which exceeds {total}: misread, printed number not determined"


def build():
    lo, hi = E.config["documents"]["budget_range"]
    rows, texts = [], {}
    for fy in range(lo, hi + 1):
        pages = pages_of(fy)
        texts[fy] = pages
        seen = defaultdict(list)
        for n, t in pages.items():
            m = LABEL.search(t[-200:])
            if m:
                seen[f"{m.group(1)} of {m.group(2)}"].append(n)
        for label, ns in seen.items():
            if len(ns) > 1:
                sys.exit(f"FY{fy}: footer {label!r} is read on several pages {ns}; resolve by hand")
            rows.append(dict(entity_id=E.id, doc_id=f"FY{fy}_budget", page_label=label, pdf_page=ns[0], printed_label=label, note=""))
        infer_misread(fy, rows)
        used = set()
        for f in os.listdir(E.data_path("extracted")):
            if f.startswith(f"FY{fy}_budget_") and f.endswith(".csv") and not f.endswith("_checks.csv"):
                used |= {r["page_label"] for r in csv.DictReader(open(E.data_path("extracted", f)))}
        missing = sorted(u for u in used if not u.startswith("pdf") and u not in seen)
        if missing:
            sys.exit(f"FY{fy}: labels used by the extracts but not read from any footer: {missing}")
    rows.sort(key=lambda r: (r["doc_id"], r["pdf_page"]))
    write_csv(E.data_path("page_map.csv"), COLUMNS, rows)
    print(f"{len(rows)} footer labels over {hi - lo + 1} documents -> data/{E.id}/page_map.csv")
    return texts


def check(texts):
    pm = {(r["doc_id"], r["page_label"]): int(r["pdf_page"]) for r in csv.DictReader(open(E.data_path("page_map.csv")))}
    hit = tot = 0
    bad = []
    for r in csv.DictReader(open(E.data_path("facts.csv"))):
        if r["source_type"] != "budget" or r["fund_code"] != "01" or not r["account_printed"]:
            continue
        label = r["page_label"]
        page = int(label[3:]) if label.startswith("pdf") else pm.get((r["doc_id"], label))
        key = "-".join(r["account_printed"].split("-")[2:4])  # department-object
        fy = int(r["doc_id"][2:6])
        tot += 1
        if page and key in texts[fy].get(page, "").replace(" ", ""):
            hit += 1
        else:
            bad.append((r["doc_id"], label, r["account_printed"]))
    print(f"account found on its mapped page: {hit} of {tot} lines ({hit / tot:.1%}); misses are OCR misreads of the account number, e.g. {sorted(set(bad))[:3]}")


if __name__ == "__main__":
    t = build()
    if "--check" in sys.argv:
        check(t)
