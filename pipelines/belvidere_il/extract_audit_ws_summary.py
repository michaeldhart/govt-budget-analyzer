"""Extract the Water and Sewer Fund "Schedule of Revenues, Expenses, and Changes in Net Position - Budget and Actual" from an audit.

Usage: python3 pipelines/belvidere_il/extract_audit_ws_summary.py FY2020 [--prep text|gray|auto]

One page: operating revenues (charges for services), operating expenses by department (Water, Sewer, Collection system),
depreciation, non-operating items, transfers or capital grants, the change in net position on the budgetary basis (original,
final, actual) and, for the actual column only, the adjustments to the GAAP basis and the net position roll-forward.
Output: data/belvidere_il/extracted/audits/FY20XX_audit_ws_summary.csv (one row per line; original/final blank on actual-only lines).

Validation (the identities the schedule prints): total operating revenues = its lines; total operating expenses = Water + Sewer +
Collection system; operating income = revenues - expenses - depreciation; non-operating total = its lines; income before transfers =
operating income + non-operating; change in net position (budgetary) = that + transfers / capital grants; total adjustment to
GAAP = its lines; change (GAAP) = budgetary + adjustments; ending net position = beginning (restated) + change (GAAP).
"""
import csv
import os
import re
import sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, ROOT, audit_pdf, page_texts_for_locating, split_numbers  # noqa: E402
from extract_audit_fund_statements import tidy  # noqa: E402
from extract_budget import ocr_page  # noqa: E402

COLS = ("original_budget", "final_budget", "actual")


def locate(fy):
    texts = page_texts_for_locating(fy)
    for i, t in enumerate(texts, 1):
        h = re.sub(r"\s+", " ", t[:400]).upper()
        if "SCHEDULE OF REVENUES, EXPENSES" in h and "BUDGET AND ACTUAL" in h and "WATER" in h and len(re.findall(r"\d{1,3},\d{3}", t)) > 20:
            return i
    raise SystemExit(f"FY{fy}: Water and Sewer summary schedule not found")


def get_page(fy, prep, page):
    if prep == "text":
        with pdfplumber.open(audit_pdf(fy)) as d:
            return d.pages[page - 1].extract_text() or ""
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    return ocr_page(audit_pdf(fy), page, outdir, prep)


def parse(text, fixline=None):
    rows, section, pend = [], "", ""
    for raw in text.splitlines():
        line = tidy(raw)
        if fixline and line in fixline:
            line = fixline[line]
        if not line or re.match(r"(?i)^(city of belvidere|schedule of|in net position|water and sewer fund|for the year|original|budget\b|\(see|- ?\d+ ?-?$)", line):
            continue
        label, vals = split_numbers(line)
        if not vals:
            if re.match(r"(?i)^(operating|non-?operating|transfers|capital grants|adjustments|charges for|income|total)", label):
                if re.match(r"(?i)^(operating revenues|operating expenses|non-?operating|transfers|adjustments)", label):
                    section = label.title()
                pend = label if re.match(r"(?i)^(income|total|capital grants)", label) else ""
            continue
        if pend:
            label, pend = f"{pend} {label}".strip(), ""
        rows.append(dict(section=section, label=label, vals=vals))
    return rows


def validate(rows):
    problems = []

    def find(pat, start=0):
        for i in range(start, len(rows)):
            if re.search(pat, rows[i]["label"], re.I):
                return i
        return None

    def col(r, c):  # c = 0 original, 1 final, 2 actual; an actual-only line has one value
        v = rows[r]["vals"]
        return v[c] if len(v) == 3 else (v[0] if c == 2 and len(v) == 1 else None)

    def check(name, got, want, cols=(0, 1, 2)):
        for c in cols:
            if got[c] is None or want[c] is None:
                continue
            if got[c] != want[c]:
                problems.append(f"{name} ({COLS[c]}): lines {got[c]:,} vs printed {want[c]:,}")

    def sums(a, b, cols=(0, 1, 2)):
        return [sum(col(i, c) or 0 for i in range(a + 1, b) if rows[i]["vals"] and col(i, c) is not None) for c in range(3)]

    def vec(i):
        return [col(i, c) for c in range(3)]

    tor, toe = find(r"^total operating revenues"), find(r"^total operating expenses")
    if tor is None or toe is None:
        return ["total operating revenues / expenses not found"]
    first = 0
    check("total operating revenues", sums(first - 1, tor), vec(tor))
    check("total operating expenses", sums(tor, toe), vec(toe))
    oi = find(r"^operating income|^operating (loss|income \(loss\))", toe)
    if oi is None:
        return problems + ["operating income not found"]
    dep = next((i for i in range(toe + 1, oi) if re.search(r"^depreciation$", rows[i]["label"], re.I)), None)
    check("operating income", [(col(tor, c) or 0) - (col(toe, c) or 0) - ((col(dep, c) or 0) if dep is not None else 0) for c in range(3)], vec(oi))
    tno = find(r"^total non-?operating", oi)
    if tno is not None:
        check("total non-operating", sums(oi, tno), vec(tno))
    ibt = find(r"^income \(loss\) before", oi)
    if ibt is not None and tno is not None:
        check("income before transfers", [(col(oi, c) or 0) + (col(tno, c) or 0) for c in range(3)], vec(ibt))
    chg = find(r"^change in net position(?!.*gaap)", oi)
    if chg is None:
        return problems + ["change in net position (budgetary basis) not found"]
    base = ibt if ibt is not None else oi
    extra = [0, 0, 0]
    if ibt is None and tno is not None:  # some years print no "income before" line: the change adds the non-operating total to operating income
        extra = [col(tno, c) or 0 for c in range(3)]
    tt = find(r"^total transfers", base)
    cg = find(r"^capital grants and contributions", base)
    for k in (tt, cg):
        if k is not None:
            extra = [extra[c] + (col(k, c) or 0) for c in range(3)]
    # The original and final columns of this line are the budget's own bottom line (it can differ from income + transfers, e.g. FY2026
    # prints (701,572) under income of 701,572); only the actual column is an identity.
    check("change in net position (budgetary basis)", [(col(base, c) or 0) + extra[c] for c in range(3)], vec(chg), (2,))
    adj_total = find(r"^total adjustment", chg)
    gaap = find(r"^change in net position.*gaap", chg)
    if adj_total is not None and gaap is not None:
        adj_start = find(r"^adjustments", chg)
        check("total adjustment to GAAP", [None, None, sum(col(i, 2) or 0 for i in range((adj_start if adj_start is not None else chg) + 1, adj_total))], [None, None, col(adj_total, 2)], (2,))
        check("change in net position (GAAP basis)", [None, None, (col(chg, 2) or 0) + (col(adj_total, 2) or 0)], [None, None, col(gaap, 2)], (2,))
    end = find(r"^net position,? april", chg)
    last = gaap if gaap is not None else chg  # without GAAP adjustments (FY2016-FY2017) the printed change is the change
    if end is not None:
        beg = find(r"restated", last) or find(r"^net position,? may", last) or find(r"^net position,? may", chg)
        if beg is not None:
            if beg < last:  # beginning printed above the change (FY2016-FY2017 layout)
                pass
            check("ending net position", [None, None, (col(beg, 2) or 0) + (col(last, 2) or 0)], [None, None, col(end, 2)], (2,))
        else:
            problems.append("beginning net position not found")
    else:
        problems.append("ending net position not found")
    return problems


def load_overrides(fy):
    path = os.path.join(DATA_E, "overrides", f"audit_ws_summary_FY{fy}.csv")
    if not os.path.exists(path):
        return {}
    return {o["key"]: o["value"] for o in csv.DictReader(open(path)) if o["kind"] == "fixline"}


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    page = locate(int(fy))
    ov = load_overrides(fy)
    results = []
    for p in (["text", "gray"] if arg == "auto" else [arg]):
        rows = parse(get_page(fy, p, page), ov)
        probs = validate(rows)
        results.append((len(probs), p, rows, probs))
        print(f"prep {p}: {len(probs)} problems, {len(rows)} lines (pdf page {page})")
    n, prep, rows, probs = min(results, key=lambda r: (not r[2], r[0]))
    path = os.path.join(DATA_E, "extracted", "audits", f"FY{fy}_audit_ws_summary.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "section", "line_item", *COLS])
        for r in rows:
            v = r["vals"]
            w.writerow([fy, f"FY{fy}_audit", page, r["section"], r["label"], *(v if len(v) == 3 else ["", "", v[0]])])
    print(f"\nchosen prep: {prep} -> {os.path.relpath(path, ROOT)}; {n} problems")
    for p in probs:
        print("  !", p)
    sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
