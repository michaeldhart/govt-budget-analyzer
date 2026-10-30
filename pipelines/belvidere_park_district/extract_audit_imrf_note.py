"""Extract the IMRF pension note of a Belvidere Park District audit (the note's own tables and assumptions; the required supplementary information is
extract_audit_imrf.py).

Usage: python3 pipelines/belvidere_park_district/extract_audit_imrf_note.py FY2025

Reads, for all seven audits (text layer FY2019 and FY2023-FY2025; the cached 200 dpi OCR of the note pages for the scanned FY2020-FY2022):
  * membership (retirees and beneficiaries, inactive members, active members, total);
  * contribution rates (member rate, the district's rate for the year) and the dollars the district contributed;
  * actuarial assumptions (cost method, asset valuation, inflation, salary increases, investment rate of return, mortality scale, experience study),
    the single discount rate with its two components (expected return, municipal bond rate);
  * the target asset allocation and the expected returns by asset class;
  * the changes in net pension liability (total pension liability, plan fiduciary net position, net pension liability: balance, every change, balance);
  * the sensitivity of the net pension liability to the discount rate (1% lower, current, 1% higher);
  * pension expense, deferred outflows and inflows by source and the schedule of their recognition.
Output (data/belvidere_park_district/extracted/audits/): <doc>_imrf_note.csv (section, line_item, column, value, value_text, unit), <doc>_imrf_note_checks.csv.
Validation (all must hold, or the script fails and writes nothing): membership adds; the target allocation adds to 100%; in each of the three columns the
change rows add up to Net Changes and the opening balance + Net Changes = the closing balance; (A) - (B) = (A - B) on every row; the sensitivity's net pension
liability = total pension liability - plan fiduciary net position and its middle column equals the closing balance, with the discount rate 1% either side;
the discount rate equals the rate of return (and the municipal bond rate is only used when the plan is projected to run out); deferred outflows and inflows
add to their totals, outflows - inflows = net on every row, the recognition schedule adds to the net total; and the note ties to the statements: the closing
balances equal the required supplementary information's (any audit that prints the calendar year), the net pension liability (asset) equals the Statement of
Net Position, the deferred outflows and inflows equal its lines, the opening balances equal the previous audit's closing balances, the employer
contributions in the change table equal the dollars the note says the district contributed. A source typo is accepted only through overrides/<doc>_imrf_note.csv (kind explained).
"""
import csv
import glob
import os
import re
import subprocess
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_audit_imrf as I  # noqa: E402

ENTITY = R.ENTITY
OCR_PAGES = list(range(14, 24)) + list(range(27, 39))
PCT = r"-?\d+(?:\.\d+)?%"


def get_pages(pdf, doc):
    t = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    if sum(len(p) for p in t) > 800 * len(t):
        return t
    n = len(t) - (1 if t and not t[-1].strip() else 0)
    o = R.ocr_selected(pdf, doc, [i for i in OCR_PAGES if i <= n], dpi=200)
    return [o.get(i, "") for i in range(1, n + 1)]


def numtok(t):
    """Decimal of a printed figure ('(5,407)', '-', '$ 1,000', '7.25%'); None for NA / N/A."""
    t = t.replace("$", "").replace("%", "").replace(",", "").strip()
    if t in ("NA", "N/A"):
        return None
    if t in ("-", "—", "–", ""):
        return Decimal(0)
    neg = t.startswith("(") or t.startswith("-")
    return -Decimal(t.strip("()-")) if neg else Decimal(t)


def clean(s):
    """OCR noise that is not a reading of a figure: `$__` for `$`, a space inside a figure (`(388, 142)`), a stray mark before a label (`: Total`)."""
    s = re.sub(r"\$_+", "$", s)
    s = re.sub(r"(\d), (\d{3})\b", r"\1,\2", s)
    return re.sub(r"^[:;|]\s*", "", s)


def note_lines(pages):
    """[(pdf_page, line)] of the IMRF note, from its first sub-heading to the next note (postemployment benefits)."""
    out, on = [], False
    for i, p in enumerate(pages, 1):
        for raw in p.splitlines():
            s = clean(raw.strip())
            if not on and re.match(r"Employees Covered by Benefit Terms", s):
                on = True
            elif on and re.match(r"^\d+\.\s+POSTEMPLOYMENT", s):
                return out
            if on and s and not re.fullmatch(r"-\s*\d+\s*-|BELVIDERE PARK DISTRICT, ILLINOIS|NOTES TO FINANCIAL STATEMENTS \(Continued\)|December 31, \d{4}|\d+\.\s+EMPLOYEE RETIREMENT SYSTEM \(Continued\)", s):
                out.append((i, s))
    return out


def trailing(s):
    """(label, [values]) of a line whose figures are at the end; a '$' or '%' alone is skipped."""
    toks = s.split()
    i = len(toks)
    while i > 0 and re.fullmatch(r"\(?-?\$?\(?\d[\d,]*\.?\d*\)?%?|-|\$|NA|N/A", toks[i - 1]):
        i -= 1
    return " ".join(toks[:i]), [numtok(t) for t in toks[i:] if t != "$"]


def rows_between(lines, start_rx, end_rx, ncols):
    """[(label, page, [ncols values])] for the lines between two markers; a label may wrap onto the line that has the figures."""
    out, pend, on = [], [], False
    for pg, s in lines:
        if not on:
            on = bool(re.search(start_rx, s))
            continue
        if re.search(end_rx, s):
            break
        m = re.match(r"^Balance,\s+December 31,\s+(\d{4})\s+(.*)$", s)
        if m:
            label, vals = f"Balance, December 31, {m.group(1)}", trailing(m.group(2))[1]
        else:
            label, vals = trailing(s)
        if len(vals) == ncols and (label or pend):
            out.append((" ".join(pend + [label]).strip(), pg, vals))
            pend = []
        elif not vals:
            pend.append(label)
        else:
            pend = []
    return out


def parse(lines, flat):
    out = {}   # section -> list of (line_item, column, value, value_text, unit, page)
    first_page = lines[0][0]

    def add(sec, item, col, value=None, text="", unit="", pg=first_page):
        out.setdefault(sec, []).append((item, col, "" if value is None else value, text, unit, pg))

    def pg_of(rx):
        return next((pg for pg, s in lines if re.search(rx, s)), first_page)
    for item, rx in (("Retirees and beneficiaries currently receiving benefits", r"^Retirees and Beneficiaries"), ("Inactive plan members entitled to but not yet receiving benefits", r"^Inactive Plan"),
                     ("Active plan members", r"^Active Plan"), ("Total", r"^Total\s+\d+\S?\s*$")):
        s = next(s for pg, s in lines if re.search(rx, s))
        add("membership", item, "count", Decimal(re.findall(r"\d+", s)[-1]), unit="members", pg=pg_of(rx))
    m = re.search(r"required to contribute ([\d.]+)%", flat)
    add("contributions", "Member contribution rate", "rate", Decimal(m.group(1)), unit="percent", pg=pg_of(r"required to contribute"))
    m = re.search(r"annual contribution rate for calendar year \d{4} was ([\d.]+)%", flat)
    add("contributions", "District annual contribution rate", "rate", Decimal(m.group(1)), unit="percent", pg=pg_of(r"annual contribution rate"))
    m = re.search(r"the District contributed \$([\d,]+)", flat)
    add("contributions", "District contribution", "amount", Decimal(m.group(1).replace(",", "")), unit="usd", pg=pg_of(r"District contributed"))
    for item, rx, unit in (("Inflation rate", r"Inflation Rate was assumed to be ([\d.]+)%", "percent"), ("Investment rate of return", r"Investment Rate of Return was assumed to be ([\d.]+)%", "percent"),
                           ("Single discount rate", r"Single Discount Rate of ([\d.]+)% was used", "percent"), ("Municipal bond rate", r"municipal bond rate is ([\d.]+)%", "percent"),
                           ("Expected rate of return on plan investments", r"expected rate of return on plan investments is ([\d.]+)%", "percent")):
        m = re.search(rx, flat)
        add("assumptions", item, "rate", Decimal(m.group(1)), unit=unit, pg=pg_of(rx.split("(")[0].replace(" ", r"\s+")))
    m = re.search(r"Salary Increases were expected to be ([\d.]+)% to ([\d.]+)%", flat)
    add("assumptions", "Salary increases", "low", Decimal(m.group(1)), unit="percent", pg=pg_of(r"Salary Increases"))
    add("assumptions", "Salary increases", "high", Decimal(m.group(2)), unit="percent", pg=pg_of(r"Salary Increases"))
    add("assumptions", "Actuarial cost method", "text", None, re.search(r"Actuarial Cost Method used was (.+?)\.", flat).group(1), pg=pg_of(r"Actuarial Cost Method"))
    add("assumptions", "Asset valuation method", "text", None, re.search(r"Asset Valuation Method used was (.+?)\.", flat).group(1), pg=pg_of(r"Asset Valuation Method"))
    add("assumptions", "Mortality improvement scale", "text", None, re.search(r"scale (MP-\d{4})", flat).group(1), pg=pg_of(r"scale MP"))
    m = re.search(r"experience study of the period (\d{4}-\d{4})", flat)
    add("assumptions", "Experience study period", "text", None, m.group(1), pg=pg_of(r"experience study"))
    # asset allocation
    alloc, on = [], False
    for pg, s in lines:
        if re.match(r"Asset Class", s):
            on = True
            continue
        if on and re.match(r"Single Discount Rate$", s):
            break
        if on:
            label, vals = trailing(s)
            if label and vals:
                alloc.append((label, pg, vals))
    for label, pg, v in alloc:
        if len(v) == 1:
            cols = [("target allocation", v[0])]
        elif len(v) == 4:
            cols = list(zip(("target allocation", "return for the year", "one-year arithmetic", "ten-year geometric"), v))
        elif len(v) == 2:
            cols = list(zip(("target allocation", "return for the year"), v))
        elif len(v) == 3:  # sub-class of alternatives: target allocation not printed
            cols = list(zip(("return for the year", "one-year arithmetic", "ten-year geometric"), v))
        else:
            continue
        for c, x in cols:
            if x is not None:  # N/A (hedge funds, private equity's allocation) is not a figure
                add("asset_allocation", label, c, x, unit="percent", pg=pg)
    # changes in net pension liability
    cols = ("total pension liability", "plan fiduciary net position", "net pension liability (asset)")
    chg = []
    on, done = False, False
    pend = []
    for pg, s in lines:
        if not on:
            on = bool(re.match(r"Balance, December 31, \d{4}", s))
        if not on or done:
            continue
        m = re.match(r"^Balance,\s+December 31,\s+(\d{4})\s+(.*)$", s)
        label, vals = (f"Balance, December 31, {m.group(1)}", trailing(m.group(2))[1]) if m else trailing(s)
        if len(vals) == 3:
            chg.append((" ".join(pend + [label]).strip(), pg, vals))
            pend = []
            if m and len(chg) > 1:
                done = True
        elif not vals:
            if not re.match(r"Changes for the year", label):
                pend.append(label)
    for label, pg, v in chg:
        for c, x in zip(cols, v):
            add("net_pension_liability_changes", label, c, x, unit="usd", pg=pg)
    # sensitivity
    # rates sit on one line: '6.25% Assumption 7.25% 8.25%'
    rl = next(s for pg, s in lines if len(re.findall(PCT, s)) == 3 and re.search("Assumption", s) or (len(re.findall(PCT, s)) == 3 and not re.search(r"[A-Za-z]{4}", s)))
    sens_rates = [Decimal(x.rstrip("%")) for x in re.findall(r"\d+(?:\.\d+)?%", rl)]
    for c, r in zip(("1% decrease", "current", "1% increase"), sens_rates):
        add("sensitivity", "Single discount rate", c, r, unit="percent", pg=pg_of(r"Sensitivity of the Net Pension"))
    for label, pg, v in rows_between(lines, r"^Sensitivity of the Net Pension", r"^Pension Expense", 3):
        if re.match(r"Total Pension Liability|Plan Fiduciary|Net Pension Liability", label):
            for c, x in zip(("1% decrease", "current", "1% increase"), v):
                add("sensitivity", label.replace("/(Asset)", " (asset)"), c, x, unit="usd", pg=pg)
    # pension expense and deferred items
    m = re.search(r"realized (negative )?pension (expense|income) of (\$?\()?\$?([\d,]+)\)?", flat)
    neg = bool(m.group(1) or m.group(2) == "income" or m.group(3) and "(" in m.group(3))
    amt = Decimal(m.group(4).replace(",", ""))
    add("pension_expense", "Pension expense (income)", "amount", -amt if neg else amt, unit="usd", pg=pg_of(r"realized (negative )?pension"))
    deferred = []
    on, pend = False, []
    for pg, s in lines:
        if not on:
            on = bool(re.search(r"^Differences between expected", s))
        if not on:
            continue
        if re.match(r"Amounts reported", s):
            break
        label, vals = trailing(s)
        if len(vals) == 3:
            deferred.append((" ".join(pend + [label]).strip(), pg, vals))
            pend = []
        elif not vals:
            pend.append(label)
    for label, pg, v in deferred:
        for c, x in zip(("deferred outflows", "deferred inflows", "net deferred outflows"), v):
            add("deferred_items", label, c, x, unit="usd", pg=pg)
    on = False
    for pg, s in lines:
        if re.match(r"Amounts reported", s):
            on = True
            continue
        if on:
            m = re.match(r"^(\d{4}|Thereafter|Total)\s+\$?\s*(.*)$", s)
            if m:
                v = trailing("x " + m.group(2))[1]
                if len(v) == 1:
                    add("deferred_recognition", m.group(1), "net deferred outflows", v[0], unit="usd", pg=pg)
    return out


def snp_values(pages):
    """Net pension asset (negative) or liability, deferred outflows and inflows of the Statement of Net Position."""
    for p in pages:
        if re.search(r"Statement of Net Position", p[:400]) and re.search(r"Deferred Outflows of Resources", p):
            t = p
            break
    else:
        raise SystemExit("Statement of Net Position page not found")
    nums = lambda rx: [Decimal(x.replace(",", "")) for x in re.findall(rx, t, re.M)]
    pen = re.search(r"^\s*Net pension (asset|liability)\s*-?\s*(?:IMRF)?\s+\$?\s*([\d,]+)(?:\s+\$?\s*(?:[\d,]+|-))?\s*$", t, re.M | re.I)
    items = nums(r"^\s*Pension items\s*-\s*IMRF\s+\$?\s*([\d,]+)(?:\s+\$?\s*(?:[\d,]+|-))?\s*$")
    return (None if pen is None else (-1 if pen.group(1).lower() == "asset" else 1) * Decimal(pen.group(2).replace(",", ""))), items


def check(ent, fy, doc, out, snp, pages):
    res = []

    def add(name, expected, found, tol=0):
        res.append((doc, name, expected, found, "ok" if expected == found else ("ok: rounding" if expected is not None and found is not None and abs(expected - found) <= tol else "FAIL")))

    def g(sec, item, col):
        return next(v for i, c, v, _, _, _ in out[sec] if i == item and c == col)
    mem = {i: v for i, c, v, _, _, _ in out["membership"]}
    add("membership adds", mem["Total"], sum(v for i, v in mem.items() if i != "Total"))
    alloc = [(i, v) for i, c, v, _, _, _ in out["asset_allocation"] if c == "target allocation"]
    add("target allocation adds to 100%", Decimal(100), sum(v for _, v in alloc))
    # change table
    ch = {}
    for i, c, v, _, _, _ in out["net_pension_liability_changes"]:
        ch.setdefault(i, {})[c] = v
    labels = list(ch)
    begin, end = labels[0], labels[-1]
    net = next(l for l in labels if l.startswith("Net Changes"))
    body = [l for l in labels if l not in (begin, end, net)]
    cols = ("total pension liability", "plan fiduciary net position", "net pension liability (asset)")
    for c in cols:
        add(f"changes ({c}): rows add to Net Changes", ch[net][c], sum(ch[l][c] for l in body))
        add(f"changes ({c}): opening + Net Changes = closing", ch[end][c], ch[begin][c] + ch[net][c])
    for l in labels:
        add(f"changes {l}: (A) - (B) = net pension liability", ch[l][cols[2]], ch[l][cols[0]] - ch[l][cols[1]])
    # sensitivity
    sc = ("1% decrease", "current", "1% increase")
    rates = [g("sensitivity", "Single discount rate", c) for c in sc]
    add("sensitivity: rates are 1% either side", [rates[1] - 1, rates[1], rates[1] + 1], rates)
    add("sensitivity: current rate = single discount rate", g("assumptions", "Single discount rate", "rate"), rates[1])
    add("single discount rate = expected rate of return", g("assumptions", "Expected rate of return on plan investments", "rate"), rates[1])
    for c in sc:
        add(f"sensitivity {c}: net pension liability = TPL - FNP", g("sensitivity", "Net Pension Liability (asset)", c), g("sensitivity", "Total Pension Liability", c) - g("sensitivity", "Plan Fiduciary Net Position", c))
        add(f"sensitivity {c}: plan fiduciary net position = closing balance", ch[end][cols[1]], g("sensitivity", "Plan Fiduciary Net Position", c))
    add("sensitivity: current total pension liability = closing balance", ch[end][cols[0]], g("sensitivity", "Total Pension Liability", "current"))
    # deferred items
    d = {}
    for i, c, v, _, _, _ in out["deferred_items"]:
        d.setdefault(i, {})[c] = v
    dl = [l for l in d if l != "Total"]
    for c in ("deferred outflows", "deferred inflows", "net deferred outflows"):
        add(f"deferred items: {c} add to total", d["Total"][c], sum(d[l][c] for l in dl))
    for l in d:
        add(f"deferred {l}: outflows - inflows = net", d[l]["net deferred outflows"], d[l]["deferred outflows"] - d[l]["deferred inflows"])
    rec = [(i, v) for i, c, v, _, _, _ in out["deferred_recognition"]]
    add("recognition schedule adds to the net total", next(v for i, v in rec if i == "Total"), sum(v for i, v in rec if i != "Total"))
    add("recognition schedule total = net deferred outflows", d["Total"]["net deferred outflows"], next(v for i, v in rec if i == "Total"))
    # statements
    npl, items = snp
    if npl is not None:
        add("net pension liability (asset) = Statement of Net Position", npl, ch[end][cols[2]])
    else:  # the Statement of Net Position combines it into noncurrent liabilities; the debt note lists it (FY2019 only: a liability)
        p = ent.data_path("extracted", "audits", f"{doc}_debt_outstanding.csv")
        dn = [Decimal(r["amount"]) for r in csv.DictReader(open(p)) if r["instrument_id"] == "net_pension" and r["column"] == "balance"]
        add("net pension liability = the debt note's line (the Statement of Net Position combines liabilities)", dn[0] if dn else None, ch[end][cols[2]])
    if len(items) >= 2:
        add("deferred outflows = Statement of Net Position", items[0], d["Total"]["deferred outflows"])
        add("deferred inflows = Statement of Net Position", items[1], d["Total"]["deferred inflows"])
    # required supplementary information (any audit that prints the calendar year)
    cy = int(end.split()[-1])
    rsi = {}
    for p in glob.glob(ent.data_path("extracted", "audits", "FY*_audit_imrf_changes.csv")):
        for r in csv.DictReader(open(p)):
            if int(r["calendar_year"]) == cy:
                rsi.setdefault(r["line_item"], Decimal(r["amount"]))
    for c, rx in zip(cols, ("Total Pension Liability - Ending", "Plan Fiduciary Net Position - Ending", r"Net Pension Liability/\(Asset\)")):
        k = next((k for k in rsi if re.search(rx, k)), None)
        if k:
            add(f"closing {c} = required supplementary information ({cy})", rsi[k], ch[end][c])
    k = next((k for k in rsi if re.match(r"Employer Contributions", k)), None)
    contrib = g("contributions", "District contribution", "amount")
    if k:
        add(f"employer contributions in the change table = required supplementary information ({cy})", rsi[k], -ch["Contributions - employer"][cols[2]])
    add("employer contributions in the change table = contributions the note says the district made", contrib, ch["Contributions - employer"][cols[1]])
    for c in cols[:2]:  # opening balances = previous audit's closing balances
        pp = ent.data_path("extracted", "audits", f"FY{fy - 1}_audit_imrf_note.csv")
        if os.path.exists(pp):
            prev = [r for r in csv.DictReader(open(pp)) if r["section"] == "net_pension_liability_changes" and r["column"] == c and r["line_item"].startswith("Balance, December 31")]
            add(f"opening {c} = previous audit's closing", Decimal(prev[-1]["value"]), ch[begin][c])
    return res


def explain(ent, doc, checks):
    """A failing check the audit itself causes (a typo in the note) is accepted only through overrides/<doc>_imrf_note.csv: kind explained, raw = the check's name, reason."""
    path = ent.data_path("overrides", f"{doc}_imrf_note.csv")
    ex = {o["raw"]: o["reason"] for o in csv.DictReader(open(path, newline=""))} if os.path.exists(path) else {}
    return [(*c[:4], "explained: " + ex[c[1]]) if c[4] == "FAIL" and c[1] in ex else c for c in checks]


def write(ent, fy, doc, out, checks):
    d = ent.data_path("extracted", "audits")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{doc}_imrf_note.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "section", "line_item", "column", "value", "value_text", "unit", "pdf_page"])
        for sec, rows in out.items():
            for r in rows:
                w.writerow([fy, doc, sec, *r])
    with open(os.path.join(d, f"{doc}_imrf_note_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "check", "expected", "found", "result"])
        w.writerows(checks)


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_audit"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    pages = get_pages(ent.source_path(src[doc]["pdf_path"]), doc)
    lines = note_lines(pages)
    flat = " ".join(s for _, s in lines)
    out = parse(lines, flat)
    checks = explain(ent, doc, check(ent, fy, doc, out, snp_values(pages), pages))
    fails = [c for c in checks if c[4] == "FAIL"]
    print(f"{doc}: {sum(len(v) for v in out.values())} figures, {len(checks)} checks, {len(fails)} failures")
    for c in fails:
        print("  FAIL", c[1], "expected", c[2], "found", c[3])
    if fails:
        sys.exit(1)
    write(ent, fy, doc, out, checks)


if __name__ == "__main__":
    main(sys.argv)
