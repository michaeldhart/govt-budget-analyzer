"""Extract the IMRF required supplementary information of a Belvidere Park District audit (text-layer audits FY2019, FY2023, FY2024, FY2025).

Usage: python3 pipelines/belvidere_park_district/extract_audit_imrf.py FY2025

Reads the Multiyear Schedule of Changes in Net Pension Liability and Related Ratios (up to 10 calendar years, one column per year) and the Multiyear
Schedule of Contributions (actuarially determined contribution, actual, deficiency, covered payroll, percent) with `pdftotext -layout`.
Output (data/belvidere_park_district/extracted/audits/): <doc>_imrf_changes.csv (calendar_year, line_item, amount; ratios in percent),
<doc>_imrf_contributions.csv, <doc>_imrf_checks.csv.
Validation (all must hold): in every year the total pension liability changes add (service cost + interest + benefit changes + experience + assumption changes
+ benefit payments = net change; beginning + net change = ending), the plan fiduciary net position changes add, net pension liability = TPL - FNP, the funded
ratio = FNP / TPL and the NPL / payroll ratio (rounded to 0.01 point), each year's beginning equals the previous year's ending, the contributions schedule's
deficiency = ADC - actual and its percent = actual / payroll, and the actual contributions equal the changes schedule's employer contributions.
"""
import csv
import os
import re
import subprocess
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402

ENTITY = R.ENTITY


def num(tok):
    t = tok.replace("$", "").replace(",", "").replace("%", "").strip()
    if t in ("-", ""):
        return Decimal(0)
    neg = t.startswith("(") or t.startswith("-")
    v = Decimal(t.strip("()-"))
    return -v if neg else v


def tokens_row(line):
    """(label, [values]): trailing run of number-like tokens ($, %, parentheses, dash = 0)."""
    toks = line.split()
    i = len(toks)
    while i > 0 and re.fullmatch(r"\(?-?\$?\(?\d[\d,]*\.?\d*\)?%?|-|\$", toks[i - 1]):
        i -= 1
    cells = [t for t in toks[i:] if t != "$"]
    return " ".join(toks[:i]).strip(), [num(t) for t in cells]


def find_pages(pages):
    ch = next(i for i, p in enumerate(pages) if "Total Pension Liability - Ending" in p)
    co = next(i for i, p in enumerate(pages) if "Multiyear Schedule of Contributions" in p[:900] and "Actuarially" in p)
    return ch, co


def read_changes(page):
    """Rows of the change schedule: a label may wrap (`Difference between Expected and Actual` / `Experience`), the numbers sit on the last line."""
    years = None
    rows, pend = [], []
    for raw in page.splitlines():
        if re.fullmatch(r"\s*-\s*\d+\s*-\s*", raw) or not raw.strip():
            continue
        m = re.search(r"Calendar year ending December 31,\s+((?:\d{4}\s*)+)", raw)
        if m:
            years = [int(y) for y in m.group(1).split()]
            continue
        label, vals = tokens_row(raw.strip())
        if years and len(vals) == len(years):
            rows.append((" ".join(pend + [label]).strip(), vals))
            pend = []
        elif not vals and years:
            if not re.fullmatch(r"Total Pension Liability|Plan Fiduciary Net Position", label):  # section headings, not label starts
                pend.append(label)
        elif vals and years and len(vals) != len(years):
            pend = []
    return years, rows


def read_contrib(page):
    rows = []
    for raw in page.splitlines():
        m = re.match(r"^\s*(\d{4})\s+(.*)$", raw)
        if not m:
            continue
        label, vals = tokens_row("x " + m.group(2))
        if len(vals) == 5:
            rows.append((int(m.group(1)), vals))
    return rows


def key(label, rx):
    return re.search(rx, label, re.I)


def check(doc, years, rows, contrib):
    out = []

    def add(name, expected, found, tol=0):
        out.append((doc, name, expected, found, "ok" if expected == found else ("ok: rounding" if abs(expected - found) <= tol else "FAIL")))
    by = {}
    for label, vals in rows:
        by.setdefault(label, vals)

    def g(rx, nth=0):
        hits = [v for l, v in rows if re.search(rx, l, re.I)]
        return hits[nth]
    for j, y in enumerate(years):
        c = lambda rx, nth=0: g(rx, nth)[j]
        add(f"{y}: TPL changes add up", c(r"Net Change in Total Pension"),
            c(r"^Service Cost") + c(r"^Interest on") + c(r"^Benefit Changes") + c(r"Expected and Actual") + c(r"^Assumption Changes") + c(r"^Benefit Payments and Refunds", 0))
        add(f"{y}: TPL beginning + change = ending", c(r"Total Pension Liability - Ending"), c(r"Total Pension Liability - Beginning") + c(r"Net Change in Total Pension"))
        add(f"{y}: FNP changes add up", c(r"Net Change in Plan Fiduciary"),
            c(r"^Employer Contributions") + c(r"^Employee Contributions") + c(r"Net Investment Income") + c(r"^Benefit Payments and Refunds", 1) + c(r"^Other"))
        add(f"{y}: FNP beginning + change = ending", c(r"Plan Fiduciary Net Position - Ending"), c(r"Plan Fiduciary Net Position - Beginning") + c(r"Net Change in Plan Fiduciary"))
        add(f"{y}: NPL = TPL - FNP", c(r"Net Pension Liability/\(Asset\)"), c(r"Total Pension Liability - Ending") - c(r"Plan Fiduciary Net Position - Ending"))
        add(f"{y}: funded ratio", c(r"Plan Fiduciary Net Position as a Percentage"),
            (c(r"Plan Fiduciary Net Position - Ending") / c(r"Total Pension Liability - Ending") * 100).quantize(Decimal("0.01")), tol=Decimal("0.01"))
        add(f"{y}: NPL as a percentage of payroll", c(r"Net Pension Liability as a Percentage"),
            (c(r"Net Pension Liability/\(Asset\)") / c(r"Covered Valuation Payroll") * 100).quantize(Decimal("0.01")), tol=Decimal("0.01"))
        if j + 1 < len(years):  # previous year is the next column
            add(f"{y}: TPL beginning = {years[j + 1]} ending", g(r"Total Pension Liability - Beginning")[j], g(r"Total Pension Liability - Ending")[j + 1])
            add(f"{y}: FNP beginning = {years[j + 1]} ending", g(r"Plan Fiduciary Net Position - Beginning")[j], g(r"Plan Fiduciary Net Position - Ending")[j + 1])
    emp = dict(zip(years, g(r"^Employer Contributions")))
    pay = dict(zip(years, g(r"Covered Valuation Payroll")))
    for y, (adc, act, defi, payroll, pct) in contrib:
        add(f"contributions {y}: deficiency = ADC - actual", defi, adc - act)
        add(f"contributions {y}: percent of payroll", pct, (act / payroll * 100).quantize(Decimal("0.01")), tol=Decimal("0.01"))
        if y in emp:
            add(f"contributions {y}: actual = employer contributions in the change schedule", emp[y], act)
    return out


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_audit"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    pdf = ent.source_path(src[doc]["pdf_path"])
    pages = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    ch, co = find_pages(pages)
    years, rows = read_changes(pages[ch] + "\n" + (pages[ch + 1] if "Net Investment Income" not in pages[ch] else ""))
    contrib = read_contrib(pages[co])
    checks = check(doc, years, rows, contrib)
    fails = [c for c in checks if c[4] == "FAIL"]
    print(f"{doc}: {len(years)} years {years[0]}-{years[-1]}, {len(rows)} rows, {len(contrib)} contribution years, {len(checks)} checks, {len(fails)} failures")
    for c in fails:
        print("  FAIL", c[1], "expected", c[2], "found", c[3])
    if fails:
        sys.exit(1)
    out = ent.data_path("extracted", "audits")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{doc}_imrf_changes.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "calendar_year", "line_item", "amount", "pdf_page"])
        seen = {}
        for label, vals in rows:
            seen[label] = seen.get(label, 0) + 1
            name = label if seen[label] == 1 else f"{label} (2)"
            for y, v in zip(years, vals):
                w.writerow([fy, doc, y, name, v, ch + 1])
    with open(os.path.join(out, f"{doc}_imrf_contributions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "calendar_year", "actuarially_determined", "actual", "deficiency", "covered_payroll", "percent_of_payroll", "pdf_page"])
        for y, v in contrib:
            w.writerow([fy, doc, y] + v + [co + 1])
    with open(os.path.join(out, f"{doc}_imrf_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "check", "expected", "found", "result"])
        w.writerows(checks)


if __name__ == "__main__":
    main(sys.argv)
