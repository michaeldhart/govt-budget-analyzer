"""Read Boone County's ERP "Budget Worksheet" (FY2025, FY2026, FY2027 preliminary budgets; text layer).

Layout: `Fund: 001 - General Fund`, `Department: 0051 - Administration`, then account lines
`001-0051-401000  Label  n n n n n` (fund-department-object, 4 or 5 amount columns whose meaning is printed in a
two-row header on every page), `Revenue Total:` / `Expense Total:` after each block. Column headers give the
fiscal year range (2025-2026 = FY2026, December to November) and the kind of figure.

Writes data/boone_county_il/extracted/<doc_id>_{lines,totals,checks}.csv:
  lines : doc_id, row_seq, pdf_page, fund_code, dept_code, account_printed, label, flow, period_fy, measure, column_label, amount, flag
  totals: doc_id, pdf_page, fund_code, dept_code, kind, period_fy, measure, printed, lines_sum, diff
  checks: the problems (an empty file means every printed total equals its lines).
measure: budget (a "Total Budget" column), activity (a "Total Activity" column), ytd (year to date), final (the
document's own "Final Budget"), prelim (a "Prelim Budget" request). Overrides: data/boone_county_il/overrides/<doc_id>.csv.
Usage: python3 pipelines/boone_county_il/extract_budget_worksheet.py FY2026_budget [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/budgets"
OUT = ROOT / "data/boone_county_il/extracted"
OVR = ROOT / "data/boone_county_il/overrides"

NUM = r"\(?-?[\d,]+(?:\.\d+)?\)?"
ACCT = re.compile(rf"^\s*(\d{{3}})-(\d{{4}})-(\d{{6}})\s+(.*?)((?:\s+{NUM})+)\s*$")
TOTAL = re.compile(rf"^\s*(Revenue|Expense) Total:((?:\s+{NUM})+)\s*$")
FUND = re.compile(r"^\s*(?:Boone County, Illinois\s+)?Fund:?\s*(\d{3})\s*-\s*(.*)$")
DEPT = re.compile(r"^\s*Department:\s*(\d{4})\s*-\s*(.*)$")
YEARS = re.compile(r"(?<![\d-])\d{4}-\d{4}(?![\d-])")
MEASURES = [("Final Budget", "final"), ("Prelim Budget", "prelim"), ("Total Budget", "budget"),
            ("Total Activity", "activity"), ("Thru", "ytd"), ("YTD", "ytd")]


def to_num(tok):
    neg = tok.startswith("(") or tok.startswith("-")
    v = float(tok.strip("()-").replace(",", ""))
    return -v if neg else v


def header_columns(lines, i):
    """Column spec from the year row at lines[i] and the label row after it: [(period_fy, measure, label, end_x)]."""
    years = [(m.group(), m.end()) for m in YEARS.finditer(lines[i])]
    j = i + 1
    while not lines[j].strip():
        j += 1
    labels = [(m.group().strip(), m.end()) for m in re.finditer(r"\S+(?: \S+)*", lines[j])]
    if len(labels) != len(years):
        raise ValueError(f"header mismatch: {lines[i].strip()!r} / {lines[j].strip()!r}")
    cols = []
    for (y, ye), (lab, le) in zip(years, labels):
        measure = next(m for key, m in MEASURES if lab.startswith(key))
        cols.append((int(y[-4:]), measure, lab, le))
    return cols


def load_overrides(doc_id):
    p = OVR / f"{doc_id}.csv"
    return list(csv.DictReader(open(p, newline=""))) if p.exists() else []


def extract(doc_id):
    overrides = load_overrides(doc_id)
    places = {o["account"]: o for o in overrides if o["kind"] == "place"}
    pdf = SRC / f"{doc_id}.pdf"
    text = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    rows, totals, problems = [], [], []
    fund = dept = None
    cols = None
    pending = {"revenue": [], "expense": []}  # (period_fy, measure) -> running sums, per block
    sums = {}
    seq = 0
    for page_no, page in enumerate(text.split("\f"), start=1):
        lines = page.split("\n")
        for i, line in enumerate(lines):
            if YEARS.search(line) and re.search(r"Total|Final|Prelim|Thru|YTD", lines[min(i + 2, len(lines) - 1)] + lines[min(i + 1, len(lines) - 1)]):
                try:
                    cols = header_columns(lines, i)
                except (IndexError, StopIteration, ValueError):
                    pass  # a garbled repeat of the header: the columns of the document do not change
                continue
            m = DEPT.match(line)
            if m:
                dept = m.group(1)
                continue
            m = FUND.match(line)
            if m and not ACCT.match(line):
                fund = m.group(1)
                dept = None
                continue
            m = ACCT.match(line)
            if m and cols:
                f, d, o, label, nums = m.groups()
                toks = re.findall(NUM, nums)
                if len(toks) > len(cols):  # a number that ends the label ("IT Wages (3)", "Jail Medical & Dental Claims 18") is part of the label
                    label = f"{label} {' '.join(toks[:len(toks) - len(cols)])}"
                    toks = toks[len(toks) - len(cols):]
                flow = "revenue" if o[0] == "3" else "expense"
                flag = ""
                ends = [mm.end() for mm in re.finditer(NUM, line[len(line) - len(nums):])]
                base = len(line) - len(nums)
                acct = f"{f}-{d}-{o}"
                if acct in places:
                    given = dict(kv.split("=") for kv in places[acct]["value"].split("|"))
                    values = [(c, given.get(f"{c[0]}:{c[1]}", "0")) for c in cols]
                    flag = "placed_by_override"
                elif len(toks) == len(cols):
                    values = list(zip(cols, toks))
                else:
                    # short row: place each number in the nearest column by its right edge
                    values = []
                    for tok, e in zip(toks, ends):
                        c = min(cols, key=lambda c: abs(c[3] - (base + e)))
                        values.append((c, tok))
                    flag = "placed_by_position"
                seq += 1
                for (fy, measure, lab, _), tok in values:
                    amt = to_num(tok)
                    rows.append(dict(doc_id=doc_id, row_seq=seq, pdf_page=page_no, fund_code=f, dept_code=d,
                                     account_printed=f"{f}-{d}-{o}", label=label.strip(), flow=flow, period_fy=fy,
                                     measure=measure, column_label=lab, amount=f"{amt:.2f}", flag=flag))
                    sums[(f, d, flow, fy, measure)] = sums.get((f, d, flow, fy, measure), 0) + amt
                continue
            m = TOTAL.match(line)
            if m and cols:
                kind = m.group(1).lower()
                toks = re.findall(NUM, m.group(2))
                if len(toks) != len(cols):
                    problems.append(f"p{page_no}: {kind} total has {len(toks)} numbers for {len(cols)} columns (fund {fund} dept {dept})")
                    continue
                for (fy, measure, lab, _), tok in zip(cols, toks):
                    # a block's lines are every line of that flow in the current fund (and department, if the fund has them)
                    # since the previous total of the same flow
                    key_lines = [r for r in rows if r["fund_code"] == fund and r["flow"] == kind and r["pdf_page"] <= page_no
                                 and r["period_fy"] == fy and r["measure"] == measure and r["row_seq"] > last_total.get((fund, kind), 0)]
                    s = sum(float(r["amount"]) for r in key_lines)
                    totals.append(dict(doc_id=doc_id, pdf_page=page_no, fund_code=fund, dept_code=dept, kind=kind,
                                       period_fy=fy, measure=measure, printed=f"{to_num(tok):.2f}", lines_sum=f"{s:.2f}", n_lines=len({r["row_seq"] for r in key_lines}),
                                       diff=f"{s - to_num(tok):.2f}"))
                last_total[(fund, kind)] = seq
    return rows, totals, problems


last_total = {}


def main(doc_id):
    last_total.clear()
    rows, totals, problems = extract(doc_id)
    # a document that prints whole dollars rounds every line, so a block may differ from its total by half a dollar per line
    whole = all(float(r["amount"]) == int(float(r["amount"])) for r in rows)
    allowed = {(o["fund_code"], o["dept_code"], o["period_fy"], o["measure"]): float(o["value"])
               for o in load_overrides(doc_id) if o["kind"] == "allow_diff"}
    for t in totals:
        tol = max(1.0, 0.5 * t["n_lines"]) if whole else 1.0
        if abs(float(t["diff"])) > tol:
            if allowed.get((t["fund_code"], t["dept_code"] or "", str(t["period_fy"]), t["measure"])) == float(t["diff"]):
                t["flag"] = "allow_diff"
                continue
            problems.append(f"p{t['pdf_page']}: fund {t['fund_code']} dept {t['dept_code']} {t['kind']} {t['period_fy']} {t['measure']}: "
                            f"printed {t['printed']} lines {t['lines_sum']} diff {t['diff']}")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("lines", rows), ("totals", totals)):
        for t in data:
            t.setdefault("flag", "")
        with open(OUT / f"{doc_id}_{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    with open(OUT / f"{doc_id}_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["problem"])
        w.writerows([[p] for p in problems])
    n_lines = len({r["row_seq"] for r in rows})
    print(f"{doc_id}: {n_lines} account lines, {len(rows)} cells, {len(totals)} total cells, {len(problems)} problems")
    for p in problems[:40]:
        print("  ", p)


if __name__ == "__main__":
    for d in sys.argv[1:]:
        main(d)
