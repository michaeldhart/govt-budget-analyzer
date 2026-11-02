"""Read the detail tables of Boone County's budget books FY2017-FY2024 (text layer; the older, pre-ERP layout).

Each book prints, per fund, tables such as "General Fund (001) / Revenue Detail" (categories of revenue lines, each with a Subtotal) and "General Fund (001) /
Expenditure Detail (by Department)" (departments "Grants (52)", sections Personnel / Materials & Services / ..., each line `401.153  Label  n n n n`, a Total per
department), and one page per special fund. Column headers give the fiscal year and kind of each number ("FY 2022 Actual | FY 2023 Budget | FY 2023 Estimate |
FY 2024 Budget"). Codes `301.000` are the ERP object codes written with a dot, departments `(52)` the ERP department 0052 and funds `(001)` the fund code.

Output data/boone_county_il/extracted/<doc>_book_lines.csv (doc_id, pdf_page, row_seq, fund_code, dept_code, table, section, code, label, period_fy, measure,
column_label, amount, flag), <doc>_book_totals.csv (printed Subtotal / Total lines with the sum of their lines) and <doc>_book_checks.csv.
measure: actual (closed year), budget, estimate (year-end estimate), proposed, partial (year to date: "Actual - Aug"), percent is dropped.
Overrides: data/boone_county_il/overrides/<doc>_book.csv (kind allow_diff|fix_code|fix_line, reason required).
Usage: python3 pipelines/boone_county_il/extract_budget_book.py FY2024_budget [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

from extract_audit_fund_statements import parse_line

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/budgets"
OUT = ROOT / "data/boone_county_il/extracted"
OVR = ROOT / "data/boone_county_il/overrides"

YEAR_TOK = re.compile(r"FY\s*'?\s*(\d{2,4})")
LABEL_TOK = re.compile(r"Percent\s+Change\s*\(\w+\)|Actual\s*-\s*\w+|Actual|Budget|Estimated?|Proposed|Percent|Projected|Request(?:ed)?|Appropriation")
FUND_LINE = re.compile(r"^(.*?)\s*\((\d{1,3})\)\s*(?:[–-].*)?$")
DEPT_LINE = re.compile(r"^(.*?)\s*\((\d{1,3})\)\s*$")
CODE = re.compile(r"^(\d{3}(?:\.\d{1,3})?)\s+(.*)$")


def measure_of(label):
    l = label.lower()
    if re.match(r"actual\s*-", l):
        return "partial"
    return {"actual": "actual", "budget": "budget", "estimate": "estimate", "estimated": "estimate", "proposed": "proposed", "percent": "percent",
            "projected": "estimate", "request": "proposed", "requested": "proposed", "appropriation": "budget"}[l]


def year4(t):
    y = int(t)
    return y if y > 99 else 2000 + y


def title_of(lines):
    """-> (fund_code, fund_name, table) from the title block at the top of a page (after 'Boone County, Illinois'), or None."""
    L = [re.sub(r"\s+", " ", l.strip()) for l in lines if l.strip()][:7]
    if not L or not any("Boone County" in x for x in L[:2]):
        return None
    title = []
    for x in L[1:]:
        if YEAR_TOK.search(x) or re.search(r"\d,\d{3}", x):
            break
        title.append(x)
    fund = next((FUND_LINE.match(x) for x in title if FUND_LINE.match(x) and not re.search(r"(?i)revenue|expenditure", x)), None)
    table = " ".join(x for x in title if not (fund and x == fund.string))
    if not fund and re.match(r"General Fund\b", table):  # FY2020-21 books title the General Fund without its number
        return "001", "General Fund", re.sub(r"^General Fund\s*", "", table)
    return ((fund.group(2).zfill(3) if fund else ""), (fund.group(1) if fund else ""), table)


def fixfy(line):
    """The text layer reads FY'18 as FY'l8 in some books."""
    return re.sub(r"(FY\s*'?\s*)[lI](\d)", r"\g<1>1\2", line)


def columns_at(lines, i):
    """Column spec if lines[i] is a year row ('FY 2022 FY 2023 FY 2023 FY 2024') and the next non-empty line holds the same number of labels."""
    years = YEAR_TOK.findall(re.sub(r"\s+", " ", fixfy(lines[i])))
    if len(years) < 2:
        return None
    j = i + 1
    while j < len(lines) and not lines[j].strip():
        j += 1
    if j >= len(lines):
        return None
    labels = LABEL_TOK.findall(re.sub(r"Change\s*\(\w+\)", "", re.sub(r"\s+", " ", lines[j])))
    if len(labels) != len(years):
        return None
    return j, [(year4(y), measure_of(lab), lab) for y, lab in zip(years, labels)]


def num_tokens(line):
    label, vals = parse_line(line)
    return label, vals


def load_overrides(doc):
    p = OVR / f"{doc}_book.csv"
    return list(csv.DictReader(open(p, newline=""))) if p.exists() else []


def main(doc):
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    overrides = load_overrides(doc)
    lines_out, totals_out, checks = [], [], []
    seq = 0
    acc = {}  # (fund, table) -> accumulators that persist across the pages of a table
    last_cols = {}
    for pn, page in enumerate(pages, 1):
        raw = page.split("\n")
        h = title_of(raw)
        if h is None:
            continue
        fund, fund_name, table = h
        dept, section = "", ""
        st = acc.setdefault((fund, re.sub(r"\s*\(Continued\).*$", "", table)), dict(total={}, sub={}, subsum={}))
        since_total, since_sub, subsum = st["total"], st["sub"], st["subsum"]
        cols = last_cols.get(fund) if "continued" in table.lower() else None  # a continuation page repeats no column header
        skip_to = -1
        edges = {}
        for nc in {len(c[1]) for c in (columns_at(raw, i) for i in range(len(raw))) if c}:
            full_edges = []
            for line in raw:
                lab_, v_ = num_tokens(line)
                if len(v_) == nc and not DEPT_LINE.match(re.sub(r"\s+", " ", line.strip())):
                    full_edges.append([e for _, e in v_])
            if full_edges:
                edges[nc] = [sorted(x[k] for x in full_edges)[len(full_edges) // 2] for k in range(nc)]
        out_of_scope = re.search(r"(?i)debt service schedule|cash (&|and) investment|bank account|balance sheet|cash flow report|capital improvements detail|salary compensation|accrual basis",
                                 " ".join(page.split()[:30]))
        if cols is None and not out_of_scope and not any(columns_at(raw, i) for i in range(len(raw))) and len(re.findall(r"\d{1,3}(?:,\d{3})+", page)) >= 8:
            checks.append((f"p{pn} {fund} {table}: no column header found on a page with figures", False, " ".join(page.split()[:12])))
        for li, line in enumerate(raw):
            if li <= skip_to:
                continue
            c = columns_at(raw, li)
            if c:
                skip_to, cols = c[0], c[1]
                last_cols[fund] = cols
                pairs = [(y, m) for y, m, _ in cols]
                if len(pairs) != len(set(pairs)) and cols[-1][1] in ("budget", "proposed") and cols[-1][0] != int(doc[2:6]):
                    # the book prints a wrong year in one header (Document Storage Fund page of the FY2021 book: 'FY 20' over the FY2021 budget column)
                    checks.append((f"p{pn} {fund}: header year {cols[-1][0]} read as {int(doc[2:6])} for the document's own budget column", True,
                                   "duplicate period/measure pair in the header"))
                    cols = cols[:-1] + [(int(doc[2:6]), cols[-1][1], cols[-1][2])]
                continue
            if cols is None:
                continue
            if not line.strip() or re.search(r"(?i)estimated expenditure column|^\s*\d+\s*$", line):
                continue
            line = re.sub(r"\s(?:-?\d+(?:\.\d+)?%|n/a|N/A)\s*$", "", line)  # the FY2021 book's 'Percent Change (Budget)' column is dropped
            dm = DEPT_LINE.match(re.sub(r"\s+", " ", line.strip()))
            if dm and not re.search(r"\d,\d|\d{3,}", dm.group(1)):
                dept, section = dm.group(2).zfill(4), ""
                continue
            label, vals = num_tokens(line)
            if not vals:
                m = DEPT_LINE.match(label.strip())
                if m:
                    dept, section = m.group(2).zfill(4), ""
                else:
                    section = label.strip()
                continue
            ncol = len(cols)
            if len(vals) > ncol:
                label = f"{label} {' '.join(str(v) for v, _ in vals[:len(vals) - ncol])}".strip()
                vals = vals[len(vals) - ncol:]
            if len(vals) == 1 and ncol > 1 and not re.match(r"(?i)\s*(sub)?total\b", label):
                continue  # cash flow, fund balance policy and other one-number lines are not budget lines
            if len(vals) != ncol:
                if 2 <= len(vals) < ncol and edges.get(ncol):
                    cells, ed = [0] * ncol, edges[ncol]
                    for v, e in vals:
                        cells[min(range(ncol), key=lambda k: abs(ed[k] - e))] = (v, e)
                    vals = [(c[0] if c else 0, c[1] if c else 0) for c in cells]
                    flag_short = "placed_by_position"
                else:
                    checks.append((f"p{pn} {fund} {table}: {len(vals)} numbers for {ncol} columns", False, label))
                    continue
            else:
                flag_short = ""
            amounts = [v for v, _ in vals]
            m = re.match(r"(?i)\s*(sub)?total\b", label)
            if m:
                kind = "subtotal" if m.group(1) else "total"
                sums = since_sub if kind == "subtotal" else since_total
                for c, (fy, meas, lab) in enumerate(cols):
                    if meas in ("percent",):
                        continue
                    s_ = sums.get(c, 0)
                    via_subtotals = subsum.get(c) if kind == "total" else None
                    totals_out.append(dict(doc_id=doc, pdf_page=pn, fund_code=fund, dept_code=dept, table=table, kind=kind, label=label.strip(), period_fy=fy,
                                           measure=meas, printed=amounts[c], lines_sum=s_, diff=s_ - amounts[c],
                                           subtotals_sum=via_subtotals if via_subtotals is not None else ""))
                if kind == "subtotal":
                    for c in range(len(cols)):
                        subsum[c] = subsum.get(c, 0) + amounts[c]
                    since_sub.clear()
                else:
                    since_total.clear(); since_sub.clear(); subsum.clear()
                continue
            code_m = CODE.match(label.strip())
            code, lab = (code_m.group(1), code_m.group(2).strip()) if code_m else ("", label.strip())
            seq += 1
            flag = flag_short
            if code and "." in code and len(code.split(".")[1]) < 3:
                flag = "code_short"
            for c, (fy, meas, collab) in enumerate(cols):
                if meas == "percent":
                    continue
                lines_out.append(dict(doc_id=doc, pdf_page=pn, row_seq=seq, fund_code=fund, dept_code=dept, table=table, section=section, code=code, label=lab,
                                      period_fy=fy, measure=meas, column_label=collab, amount=amounts[c], flag=flag))
                since_total[c] = since_total.get(c, 0) + amounts[c]
                since_sub[c] = since_sub.get(c, 0) + amounts[c]
    allowed = {(o["fund_code"], o["dept_code"], o["label"], o["period_fy"], o["measure"]): int(o["value"]) for o in overrides if o["kind"] == "allow_diff"}
    n_lines = {}
    for r in lines_out:
        n_lines.setdefault((r["doc_id"], r["fund_code"], r["table"]), set()).add(r["row_seq"])
    for t in totals_out:
        if t["diff"] == 0 or t["subtotals_sum"] == t["printed"] or (t["lines_sum"] == 0 and t["kind"] == "total"):
            continue  # ties, or a grand total of subtotals, or a summary page whose lines are not read
        tol = 0 if t["measure"] in ("budget", "proposed") else 0.5 * 25  # actual and estimate columns are rounded by the county from cents
        if abs(t["diff"]) <= 6 and t["measure"] not in ("budget", "proposed"):  # actual, estimate and year-to-date columns are rounded from cents line by line
            continue
        if allowed.get((t["fund_code"], t["dept_code"], t["label"], str(t["period_fy"]), t["measure"])) == t["diff"]:
            t["flag"] = "allow_diff"
            continue
        checks.append((f"p{t['pdf_page']} fund {t['fund_code']} dept {t['dept_code']} {t['label']} {t['period_fy']} {t['measure']}", False,
                       f"printed {t['printed']} lines {t['lines_sum']} diff {t['diff']}"))
    for t in totals_out:
        t.setdefault("flag", "")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("lines", lines_out), ("totals", totals_out)):
        with open(OUT / f"{doc}_book_{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    with open(OUT / f"{doc}_book_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: {seq} lines, {len(lines_out)} cells, {len(totals_out)} printed totals, {len(bad)} problems")
    for c in bad[:30]:
        print("   FAIL", c[0], c[2])


if __name__ == "__main__":
    for d in sys.argv[1:]:
        main(d)
