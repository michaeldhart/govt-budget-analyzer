"""Extract a Belvidere Park District budget from the district's budget-software report ("My Budget Report", Account Summary), text-layer PDFs.

Usage: python3 pipelines/belvidere_park_district/extract_budget_report.py FY2027        (or a document id: FY2026_budget_draft)

Layout (FY2027 draft): per fund, `Revenue` lines, `Revenue Total:`, `Expense` lines, `Expense Total:`, `Fund: NN - Name Surplus (Deficit):`; then the
`Group Summary` (revenue, expense, surplus per fund and `Report Surplus (Deficit):`) and the `Fund Summary` (surplus per fund). Each line has six
numbers: Original Total Budget, Current Total Budget, Period Activity, Fiscal Activity, Variance Favorable (Unfavorable), Percent Remaining.
A draft prints no actuals (activity is 0), so the extract keeps the two budget columns.

Output (data/belvidere_park_district/extracted/):
  <doc_id>_revenue.csv, _expense.csv  one row per account x column: fiscal_year, doc, page_label, pdf_page, fund_code, fund, dept_code,
                                            account, line_item, column, amount, flag
  <doc_id>_totals.csv                       every printed total: fiscal_year, doc, page_label, pdf_page, fund_code, fund, kind, name, column, amount
Validation (all must tie in both columns; a failure exits non-zero and writes nothing): revenue lines = Revenue Total; expense lines = Expense
Total; revenue total - expense total = the fund's Surplus (Deficit); the Group Summary and Fund Summary equal the fund pages; Report Surplus =
sum of fund surpluses; Variance = activity - budget for revenue, budget - activity for expense.
"""
import csv
import os
import re
import subprocess
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from govbudget.entity import Entity  # noqa: E402

ENTITY = "belvidere_park_district"
# OCR noise makes fixed-width patterns brittle ("$88,097.80", "+205,581.00", "‘0.00", "~1,381,445.00"), so lines are matched on their head and the
# amounts are read as money tokens: the first two are Original and Current Total Budget, the next three Period Activity, Fiscal Activity and Variance.
MONEY = re.compile(r"([-~—(+]{0,3})\s?[$‘']?(\d[\d,]*\.\d{2})")
ACCT = re.compile(r"^\W*([0-9OQoIlSs]{1,3})-{1,2}(\d{3,4})-{1,2}(\d{3,4})\s+(.*)$")
TOTAL = re.compile(r"^\W*(Revenue|Expense)\s+Total\W*(.*)$")
SURPLUS = re.compile(r"^\W*Fund:\s*(\d{2})\s*-\s*(.+?)\s*Surplus\s*\(Deficit\W*(.*)$")
REPORT_SURPLUS = re.compile(r"^\W*Report Surplus\s*\(Deficit\W*(.*)$")
FUND_HEAD = re.compile(r"^\W*Fund:\s*(\d{2})\s*-\s*(?!.*Surplus)(.+?)\s*$")
GROUP_LINE = re.compile(r"^\W*(Revenue|Expense)\s+(\S.*)$")
FUND_SUMMARY = re.compile(r"^\W*(\d{2}) - (.+?)\s+([-~\d$].*)$")
COLUMNS = ("budget_original", "budget_current")


def tokens(text):
    """Money amounts in a string, in order, signed (a leading - ~ — or ( is negative)."""
    text = re.sub(r"(?<=\d),(\d{2})(?!\d)", r".\1", text)  # OCR reads the decimal point as a comma ("420,00"); thousands commas are followed by 3 digits
    # a single - ~ — ( before the number is a minus; a run of marks ("—-1,332,934.00") is scan noise
    return [(-1 if m.group(1) in ("-", "~", "—", "(") else 1) * Decimal(m.group(2).replace(",", "")) for m in MONEY.finditer(text)]


def clean(v):
    """Exact amount: a whole number of dollars as int, otherwise dollars and cents (facts round to dollars later, flag rounded_from_cents)."""
    return int(v) if v == v.to_integral_value() else v


def ocr_pages(pdf, doc, prep="gray", dpi=300):
    """OCR every page (cached under data/<entity>/ocr/<doc>_<prep>/page-N.txt; the cache is gitignored)."""
    cache = os.path.join(ROOT, "data", ENTITY, "ocr", f"{doc}_{prep}" + ("" if dpi == 300 else f"_{dpi}"))
    os.makedirs(cache, exist_ok=True)
    n = int(next(l.split()[1] for l in subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, check=True).stdout.splitlines()
                 if l.startswith("Pages")))
    out = []
    for i in range(1, n + 1):
        txt = os.path.join(cache, f"page-{i}.txt")
        if not os.path.exists(txt):
            png = os.path.join(cache, f"page-{i}")
            subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(i), "-l", str(i), "-gray", "-png", "-singlefile", pdf, png], check=True)
            if prep.startswith("thr"):  # threshold the grayscale page (thr150: pixels above 150 become white), as the city's extractors do
                from PIL import Image
                im = Image.open(png + ".png")
                im.point(lambda v, t=int(prep[3:]): 255 if v > t else 0).save(png + ".png")
            subprocess.run(["tesseract", png + ".png", txt[:-4], "--psm", "6"], capture_output=True, check=True)
            os.remove(png + ".png")
        out.append(open(txt).read())
    return out


def ocr_selected(pdf, doc, pages, prep="gray", dpi=300, psm=6, rotate=0):
    """OCR only the given 1-based pages (cache data/<entity>/ocr/<doc>_<prep>[_<dpi>][_psm<n>]/page-N.txt, same layout as ocr_pages); dict page -> text."""
    cache = os.path.join(ROOT, "data", ENTITY, "ocr", f"{doc}_{prep}" + ("" if dpi == 300 else f"_{dpi}") + ("" if psm == 6 else f"_psm{psm}") + (f"_rot{rotate}" if rotate else ""))
    os.makedirs(cache, exist_ok=True)
    out = {}
    for i in pages:
        txt = os.path.join(cache, f"page-{i}.txt")
        if not os.path.exists(txt):
            png = os.path.join(cache, f"page-{i}")
            subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(i), "-l", str(i), "-gray", "-png", "-singlefile", pdf, png], check=True)
            if prep.startswith("thr") or rotate:
                from PIL import Image
                im = Image.open(png + ".png")
                if rotate:
                    im = im.rotate(rotate, expand=True)  # degrees counter-clockwise: the landscape statements are scanned sideways
                if prep.startswith("thr"):
                    im = im.point(lambda v, t=int(prep[3:]): 255 if v > t else 0)
                im.save(png + ".png")
            subprocess.run(["tesseract", png + ".png", txt[:-4], "--psm", str(psm)], capture_output=True, check=True)
            os.remove(png + ".png")
        out[i] = open(txt).read()
    return out


def pages_of(pdf, doc, prep="gray", dpi=300):
    text = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    if len(text.strip()) > 800 * text.count("\f"):
        return text.split("\f")
    return ocr_pages(pdf, doc, prep=prep, dpi=dpi)


def load_overrides(ent, doc):
    path = ent.data_path("overrides", f"{doc}.csv")
    return list(csv.DictReader(open(path, newline=""))) if os.path.exists(path) else []


def parse(pdf, fy, doc, overrides=(), corrections=None):
    revenue, expense, totals, problems = [], [], [], []
    used, notes = set(), []
    section, fund = None, None
    group, fund_summary, surplus = {}, {}, {}
    for pdf_page, page in enumerate(pages_of(pdf, doc), start=1):
        m = re.search(r"Page (\d+) of (\d+)", page)
        label = f"{m.group(1)} of {m.group(2)}" if m else f"pdf{pdf_page}"
        in_group = "Group Summary" in page
        in_fund_summary = "Fund Summary" in page
        for line in page.splitlines():
            for i, o in enumerate(overrides):
                if line.startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, o["kind"], line if o["kind"] == "line" else o["raw"], o["fixed"], o["reason"]))
                    line = o["fixed"] if o["kind"] == "line" else o["fixed"] + line[len(o["raw"]):]
                    used.add(i)
            if in_group:
                g = GROUP_LINE.match(line)
                if g and len(tokens(g.group(2))) >= 2:
                    for c, v in zip(COLUMNS, tokens(g.group(2))):
                        group[(fund, g.group(1), c)] = v
                    continue
                if FUND_HEAD.match(line):
                    fund = FUND_HEAD.match(line).group(1)
                    continue
            if in_fund_summary:
                f = FUND_SUMMARY.match(line)
                if f and len(tokens(f.group(3))) >= 2:
                    for c, v in zip(COLUMNS, tokens(f.group(3))):
                        fund_summary[(f.group(1), c)] = v
                    continue
            if in_group or in_fund_summary:
                r = REPORT_SURPLUS.match(line)
                if r and len(tokens(r.group(1))) >= 2:
                    for c, v in zip(COLUMNS, tokens(r.group(1))):
                        totals.append((label, pdf_page, "", "", "report_net", "Report Surplus (Deficit)", c, v))
                continue
            h = FUND_HEAD.match(line)
            if h:
                fund, fund_name = h.group(1), h.group(2)
                section = None
                continue
            r = REPORT_SURPLUS.match(line)
            if r and len(tokens(r.group(1))) >= 2:
                for c, v in zip(COLUMNS, tokens(r.group(1))):
                    totals.append((label, pdf_page, "", "", "report_net", "Report Surplus (Deficit)", c, v))
                continue
            if line.strip() == "Revenue":
                section = "revenue"
                continue
            if line.strip() == "Expense":
                section = "expense"
                continue
            a = ACCT.match(line)
            if a and len(tokens(a.group(4))) >= 2:
                printed_fund, dept, obj = a.group(1), a.group(2), a.group(3)
                rest = a.group(4)
                first = MONEY.search(rest)
                label_text = rest[:first.start()].strip()
                fund_code = fund  # the fund heading is the reliable context; OCR garbles the account's 2-digit fund prefix (001-, 901-, Q1-)
                vals = tokens(rest)
                flags = ["account_prefix_repaired"] if printed_fund != fund else []
                if len(vals) < 5:
                    flags.append("amount_columns_unchecked")
                row = dict(fiscal_year=fy, doc=doc, page_label=label, pdf_page=pdf_page, fund_code=fund_code, fund=fund_name,
                           dept_code=dept, account=f"{fund_code}-{dept}-{obj}", line_item=label_text)
                for c, v in zip(COLUMNS, vals[:2]):
                    out = dict(row, column=c, amount=clean(v), flag=";".join(flags))
                    (revenue if section == "revenue" else expense).append(out)
                # variance identity: favorable(unfavorable) = activity - budget (revenue) or budget - activity (expense)
                if len(vals) >= 5:
                    budget, activity, variance = vals[1], vals[3], vals[4]
                    want = activity - budget if section == "revenue" else budget - activity
                    if abs(variance) != abs(want):  # a derived column with OCR noise and shifted tokens: informational, the budget cells are checked by the totals
                        notes.append(f"{label}: {row['account']} variance column reads {variance}, expected {want}")
                continue
            t = TOTAL.match(line)
            if t and len(tokens(t.group(2))) >= 2:
                kind = "fund_revenues" if t.group(1) == "Revenue" else "fund_expenses"
                for c, v in zip(COLUMNS, tokens(t.group(2))):
                    totals.append((label, pdf_page, fund, fund_name, kind, f"Total {t.group(1)}", c, v))
                continue
            s = SURPLUS.match(line)
            if s and len(tokens(s.group(3))) >= 2:
                for c, v in zip(COLUMNS, tokens(s.group(3))):
                    totals.append((label, pdf_page, s.group(1), s.group(2), "fund_net", "Surplus (Deficit)", c, v))
                    surplus[(s.group(1), c)] = v
                continue
    for i, o in enumerate(overrides):
        if i not in used:
            problems.append(f"override not used: {o['raw']}")
    return revenue, expense, totals, group, fund_summary, problems, notes


def validate(revenue, expense, totals, group, fund_summary, problems):
    sums = {}
    for flow, rows in (("revenue", revenue), ("expense", expense)):
        for r in rows:
            sums[(r["fund_code"], flow, r["column"])] = sums.get((r["fund_code"], flow, r["column"]), 0) + r["amount"]
    printed = {}
    for label, pg, fund, name, kind, nm, c, v in totals:
        printed[(fund, kind, c)] = v
    funds = sorted({k[0] for k in sums})
    for f in funds:
        for c in COLUMNS:
            rev, exp = sums.get((f, "revenue", c), 0), sums.get((f, "expense", c), 0)
            pr, pe, pn = printed.get((f, "fund_revenues", c)), printed.get((f, "fund_expenses", c)), printed.get((f, "fund_net", c))
            if pr is None or pe is None or pn is None:
                problems.append(f"fund {f} {c}: missing printed total")
                continue
            if rev != pr:
                problems.append(f"fund {f} {c}: revenue lines {rev} != printed {pr}")
            if exp != pe:
                problems.append(f"fund {f} {c}: expense lines {exp} != printed {pe}")
            if pr - pe != pn:
                problems.append(f"fund {f} {c}: printed revenue - expense {pr - pe} != surplus {pn}")
            if group:  # the summary pages are absent from some documents (FY2025 has 7 pages, no summaries)
                for flow, want in (("Revenue", pr), ("Expense", pe)):
                    g = group.get((f, flow, c))
                    if g is None or g != want:
                        problems.append(f"fund {f} {c}: group summary {flow} {g} != {want}")
            if fund_summary:
                fs = fund_summary.get((f, c))
                if fs != pn:
                    problems.append(f"fund {f} {c}: fund summary {fs} != surplus {pn}")
    for c in COLUMNS:
        total = sum(printed.get((f, "fund_net", c), 0) for f in funds)
        if printed.get(("", "report_net", c)) is None or printed[("", "report_net", c)] != total:
            problems.append(f"{c}: report surplus {printed.get(('', 'report_net', c))} != sum of funds {total}")
    return problems


def write(ent, fy, doc, revenue, expense, totals):
    out = os.path.join(ent.data_path("extracted"))
    os.makedirs(out, exist_ok=True)
    cols = ["fiscal_year", "doc", "page_label", "pdf_page", "fund_code", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"]
    for name, rows in (("revenue", revenue), ("expense", expense)):
        with open(os.path.join(out, f"{doc}_{name}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    with open(os.path.join(out, f"{doc}_totals.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "pdf_page", "fund_code", "fund", "kind", "name", "column", "amount"])
        for label, pg, fund, name, kind, nm, c, v in totals:
            w.writerow([fy, doc, label, pg, fund, name, kind, nm, c, clean(v)])


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_budget"  # FY2026 -> FY2026_budget, or a full doc id such as FY2026_budget_draft
    fy = int(doc[2:6])
    ent = Entity.load(ENTITY)
    rows = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    if doc not in rows and f"{doc}_draft" in rows:
        doc = f"{doc}_draft"
    pdf = ent.source_path(rows[doc]["pdf_path"])
    corrections = []
    revenue, expense, totals, group, fund_summary, problems, notes = parse(pdf, fy, doc, load_overrides(ent, doc), corrections)
    validate(revenue, expense, totals, group, fund_summary, problems)
    print(f"{doc}: {len(revenue)} revenue cells, {len(expense)} expense cells, {len(totals)} totals, {len(problems)} problems")
    if notes:
        print(f"  {len(notes)} variance-column notes (derived column, OCR noise)")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    write(ent, fy, doc, revenue, expense, totals)
    with open(ent.data_path("extracted", f"{doc}_corrections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "page_label", "pdf_page", "method", "raw_text", "final_text", "detail"])
        w.writerows(corrections)


if __name__ == "__main__":
    main(sys.argv)
