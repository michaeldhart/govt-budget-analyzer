"""Extract the long-term debt note, the legal debt margin schedules and the assessed valuation / tax rate schedule of a Belvidere Park District audit.

Usage: python3 pipelines/belvidere_park_district/extract_audit_debt.py FY2025

Reads (text layer, or OCR for the scanned FY2020-FY2022 audits):
  * Note 5 Long-Term Debt: the outstanding debt table (balance and current portion per instrument), the annual requirements (principal and interest per
    year per instrument, columns named in entities/belvidere_park_district/debt_schedule_columns.csv) and the liability activity table (beginning, new
    issues, retired, ending);
  * Schedule of Legal Debt Margin and Schedule of Non-Referendum Bond Debt Margin (two columns: this audit's tax year and the previous one);
  * Assessed Valuations and Property Tax Rates, Extensions and Collections (three tax years: rate-setting assessed valuation, tax rates, extensions,
    collections by fund, percent collected).
Output (data/belvidere_park_district/extracted/audits/): <doc>_debt_outstanding.csv, _debt_schedule.csv, _debt_activity.csv, _legal_debt_margin.csv,
_assessed_valuations.csv, _debt_checks.csv, _debt_corrections.csv.
Validation (all must hold, or the script fails and writes nothing): outstanding lines sum to the total (balance and current portion); the annual
requirements add across instruments and down the years and the total principal equals the outstanding bonds; each activity row rolls forward
(beginning + new - retired = ending) and its ending balance equals the outstanding balance; the legal debt margin schedules compute (limit = rate x
valuation, net debt, margin) and their debt equals the debt note's bonds and the debt service funds' fund balances; the tax rates, extensions and
collections add up, extensions = rate x valuation, percent collected = collections / extensions, and the collections of the audit's tax year equal the
property taxes of the statement of changes in fund balances.
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
import extract_audit_fund_statements as S  # noqa: E402

ENTITY = R.ENTITY
NOTE_PAGES = list(range(27, 35))
SCHEDULE_PAGES = list(range(52, 58))


def get_pages(pdf, doc):
    """Text of the pages that can hold these tables, index = page - 1 (empty elsewhere); OCR for the scanned audits."""
    t = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    if sum(len(p) for p in t) > 800 * len(t):
        S.OCR_NOISE = False
        return t
    S.OCR_NOISE = True
    n = len(t) - (1 if t and not t[-1].strip() else 0)  # pdftotext ends with an empty page after the last form feed
    o = R.ocr_selected(pdf, doc, [i for i in NOTE_PAGES + SCHEDULE_PAGES if i <= n], dpi=200)
    return [o.get(i, "") for i in range(1, n + 1)]


def lines_between(text, start_re, end_re=None, include_end=True):
    out, on = [], False
    for raw in text.splitlines():
        if not on:
            if re.search(start_re, raw):
                on = True
            else:
                continue
        out.append(raw)
        if end_re and re.search(end_re, raw) and len(out) > 1:
            break
    return out


def rows(lines):
    """(label, cells) per line with numbers; a line without numbers is the start (or a heading line) of the next row's label."""
    out, pend = [], []
    for raw in lines:
        if re.fullmatch(r"\s*-\s*\d+\s*-\s*", raw) or not raw.strip():
            continue
        r = S.split_row(raw)
        if r is None:
            continue
        label, cells = r
        if not cells:
            pend.append(label)
            continue
        out.append((" ".join(pend + [label]).strip(), cells))
        pend = []
    return out


def load(ent, name, key):
    return [r for r in csv.DictReader(open(ent.config_path(name), newline="")) if r["doc_id" if "doc_id" in r else list(r)[0]] == key]


def instrument_of(ent, description):
    for r in csv.DictReader(open(ent.config_path("debt_instruments.csv"), newline="")):
        if re.search(r["match"], description):
            return r["instrument_id"]
    return ""


HEADER_LINE = re.compile(r"(?i)^\W*(balances?|current|portion|12/31|beginning|new|retired|ending|issues|principal|interest|year ending|december 31|consist of|park bond|go refunding|series|sbita|total\s+total|\(alt)")


def table_pages(pages):
    """PDF page (1-based) of each table: the first page whose text has the table's heading."""
    def find(rx, rng):
        return next((i for i in rng if i <= len(pages) and re.search(rx, pages[i - 1])), "")
    return {"outstanding": find(r"outstanding debt as of", NOTE_PAGES), "schedule": find(r"annual requirements to amortize", NOTE_PAGES),
            "activity": find(r"[Ll]ong-term liability activity", NOTE_PAGES),
            "legal_debt_margin": find(r"Schedule of Legal Debt Margin", SCHEDULE_PAGES), "non_referendum": find(r"Schedule of Non-Referendum", SCHEDULE_PAGES),
            "assessed": find(r"Assessed Valuations and Property Tax", SCHEDULE_PAGES)}


def parse(pdf, fy, doc, ent, pages=None):
    pages = pages if pages is not None else get_pages(pdf, doc)
    note = "\n".join(pages[i - 1] for i in NOTE_PAGES if i <= len(pages))
    problems = []
    ncols = 2 * (1 + len([r for r in csv.DictReader(open(ent.config_path("debt_schedule_columns.csv"))) if r["doc_id"] == doc]))
    # 1. outstanding debt: descriptions run over several lines (with dates and rates in them); a row ends with its two amounts (balance, current portion)
    ls = lines_between(note, r"12/31/\d{2,4}\s+Portion", r"Total outstanding debt")
    outstanding, acc = [], []
    AMT = r"(?:\$\s*)?(?:\d{1,3}(?:,\d{3})+|\d+|-|—|–|=|:)"
    for raw in ls[1:]:
        s_ = re.sub(r"\s*[|!]+\s*$", "", raw.strip())
        if not s_ or re.fullmatch(r"-\s*\d+\s*-", s_) or s_.startswith("*"):
            if s_.startswith("*"):
                break
            continue
        m = re.match(rf"^(.*?)\s+({AMT})\s+({AMT})\s*$", s_)
        if m and (re.search(r"\d{1,3},\d{3}", m.group(2)) or re.search(r"\d{1,3},\d{3}", m.group(3)) or m.group(2) in "-—–=:" or m.group(3) in "-—–=:"):
            label = " ".join(acc + [m.group(1)]).strip()
            cells = [S.to_amount(S.fix_token(m.group(2).replace("$", "").strip())), S.to_amount(S.fix_token(m.group(3).replace("$", "").strip()))]
            outstanding.append((label, cells))
            acc = []
            if re.match(r"Total outstanding debt", label):
                break
        else:
            acc.append(s_)
    # 2. annual requirements
    ls = lines_between(note, r"annual requirements to amortize", r"^\s*(?:\$\s*)?[\d,]{7,}\s*$")
    sched, tail = [], []
    for label, cells in rows([l for l in ls[1:] if not (HEADER_LINE.match(l) and not re.match(r"\s*\d{4}", l))]):
        if cells and 1990 <= cells[0] <= 2100 and len(cells) == ncols + 1:
            label, cells = str(cells[0]), cells[1:]
        m = re.search(r"(\d{4}(?:-\d{4})?|Total)\s*$", label)
        if m and len(cells) == ncols and (m.group(1) == "Total" or re.fullmatch(r"\d{4}(-\d{4})?", m.group(1))):
            sched.append((m.group(1), cells))
        elif re.search(r"(?i)premium|compensated|pension|lease", label) or label == "":
            tail.append((label or "Total outstanding", cells))
    # 3. liability activity
    ls = lines_between(note, r"[Ll]ong-term liability activity", r"^\s*(Legal Debt Margin|\d+\.\s)")
    activity = []
    for label, cells in rows([l for l in ls[1:] if not HEADER_LINE.match(l) or re.search(r"G\.O\.|SBITA|Series", l)]):
        label = re.sub(r"^.*?(was as follows:|issues|retired|balance)\s*", "", label) if False else label
        if len(cells) == 5 and 1990 <= cells[0] <= 2100:  # "G.O. Bond, Series 2025": the series year is part of the label
            label, cells = f"{label} {cells[0]}", cells[1:]
        if len(cells) >= 4:
            activity.append((label or "Total", cells))
    # 4. legal debt margin
    legal = {}
    for key, title in (("legal_debt_margin", r"Schedule of Legal Debt Margin"), ("non_referendum", r"Schedule of Non-Referendum")):
        pg = next((p for i, p in enumerate(pages, 1) if i in SCHEDULE_PAGES and re.search(title, p[:600])), None)
        if pg is None:
            problems.append(f"{doc}: {title} page not found")
            continue
        legal[key] = S.read_rows(pg, r"^\s*Assessed Valuations?", 2)
    # 5. assessed valuations
    pg = next((p for i, p in enumerate(pages, 1) if i in SCHEDULE_PAGES and re.search(r"Assessed Valuations and Property Tax", p[:500])), None)
    assessed = parse_assessed(pg, problems, doc) if pg else None
    if pg is None:
        problems.append(f"{doc}: assessed valuations page not found")
    return outstanding, sched, tail, activity, legal, assessed, problems


DEC = re.compile(r"-?\(?\$?\d[\d,]*\.?\d*\)?%?")


def parse_assessed(page, problems, doc):
    """Rows of the assessed valuation schedule: {section: [(label, [values per tax year])]}, the tax years and the 'Percent Collected' row. Decimals kept."""
    years, sections, section, out = None, [], "", {}
    for raw in page.splitlines():
        s = raw.strip()
        if not s or re.fullmatch(r"-\s*\d+\s*-", s):
            continue
        if years is None and re.fullmatch(r"(?:20\d\d\s+){1,4}20\d\d", s):
            years = [int(y) for y in s.split()]
            continue
        m = re.match(r"^(Tax rates|Tax extensions|Tax collections):?$", s, re.I)
        if m:
            section = m.group(1).lower().split()[-1]
            out[section] = []
            continue
        toks = s.split()
        nums = []
        i = len(toks)
        while i > 0 and re.fullmatch(r"\(?\$?\d[\d,]*\.?\d*\)?%?|\$_*|-", toks[i - 1]):
            if not toks[i - 1].startswith("$_") and toks[i - 1] != "$":
                nums.append(toks[i - 1])
            i -= 1
        nums.reverse()
        label = " ".join(toks[:i]).rstrip("$").strip()
        if not nums:
            continue
        if years and len(nums) > len(years) and not re.match(r"Percent", label):  # "General & SB 508": the number belongs to the label
            label = f"{label} {' '.join(nums[:len(nums) - len(years)])}".strip()
            nums = nums[len(nums) - len(years):]
        vals = [Decimal(n.replace("$", "").replace(",", "").replace("%", "").strip("()") or "0") if n != "-" else Decimal(0) for n in nums]
        key = "valuation" if re.match(r"Valuation|Rate Setting", label) or (label == "" and not section and years and vals and vals[0] > 10_000_000) else None
        if key:
            out.setdefault("valuation", []).append(("Rate setting assessed valuation", vals))
        elif re.match(r"Percent Collected", label):
            out["percent"] = [("Percent collected", vals)]
        elif section:
            out[section].append((label or "Total", vals))
    return {"years": years, "sections": out}


def instrument(ent, label):
    for r in csv.DictReader(open(ent.config_path("debt_instruments.csv"), newline="")):
        if re.search(r["match"], label, re.I):
            return r["instrument_id"]
    return ""


def prior_value(ent, fy, table, key):
    """Value in the previous audit's extract (for the cross-year checks), or None when that audit is not extracted."""
    path = ent.data_path("extracted", "audits", f"FY{fy - 1}_audit_{table}.csv")
    if not os.path.exists(path):
        return None
    for r in csv.DictReader(open(path)):
        if all(r.get(k) == str(v) for k, v in key.items()):
            return Decimal(r["amount"])
    return None


def check(ent, fy, doc, outstanding, sched, tail, activity, legal, assessed):
    out = []

    def add(name, expected, found, tol=0):
        out.append((doc, name, expected, found, "ok" if expected == found else ("ok: rounding" if expected is not None and found is not None and abs(expected - found) <= tol else "FAIL")))

    cols = [r for r in csv.DictReader(open(ent.config_path("debt_schedule_columns.csv"), newline="")) if r["doc_id"] == doc]
    inst = [c["instrument_id"] for c in sorted(cols, key=lambda c: int(c["pair"]))]
    # outstanding
    lines = [(l, c) for l, c in outstanding if not l.startswith("Total outstanding")]
    total = next(c for l, c in outstanding if l.startswith("Total outstanding"))
    add("outstanding: balances sum to total", total[0], sum(c[0] for _, c in lines))
    add("outstanding: current portions sum to total", total[1], sum(c[1] for _, c in lines))
    bal = {}
    for l, c in lines:
        bal[instrument(ent, l) or l] = c[0]
    # annual requirements
    n = len(inst)
    years = [(l, c) for l, c in sched if l != "Total"]
    tot = next(c for l, c in sched if l == "Total")
    for l, c in years:
        add(f"schedule {l}: total principal", sum(c[0:2 * n:2]), c[2 * n])
        add(f"schedule {l}: total interest", sum(c[1:2 * n:2]), c[2 * n + 1])
    for j in range(2 * (n + 1)):
        add(f"schedule: total row column {j + 1}", tot[j], sum(c[j] for _, c in years))
    for k, i in enumerate(inst):
        add(f"schedule: {i} principal = outstanding balance", bal.get(i), tot[2 * k])
    other = sum(c[0] for l, c in tail if not l.startswith("Total outstanding"))
    add("schedule: principal + other liabilities = total outstanding", total[0], tot[2 * n] + other)
    # activity
    rows_ = [(l, c) for l, c in activity if l != "Total"]
    for l, c in rows_:
        add(f"activity {l}: beginning + new - retired = ending", c[3], c[0] + c[1] - c[2])
        i = instrument(ent, l)
        if i and i in bal:
            add(f"activity {l}: ending = outstanding balance", bal[i], c[3])
    at = next((c for l, c in activity if l == "Total"), None)
    if at:
        for j, nm in enumerate(("beginning", "new", "retired", "ending")):
            add(f"activity: total {nm}", at[j], sum(c[j] for _, c in rows_))
        add("activity: total ending = total outstanding", total[0], at[3])
    # legal debt margin
    for key, rate in (("legal_debt_margin", Decimal("0.02875")), ("non_referendum", Decimal("0.00575"))):
        if key not in legal:
            continue
        rows2 = {re.sub(r"[^a-z]", "", lab.lower()): a for _, lab, a, _ in legal[key]}
        def g(rx):
            return next(a for k, a in rows2.items() if re.match(rx, k))
        for col in (0, 1):
            av = g("assessedvaluation")[col]
            lim = g("statutorydebtlimitation")[col]
            add(f"{key} col{col + 1}: limit = rate x valuation", int((Decimal(av) * rate).to_integral_value()), lim) if abs(int(Decimal(av) * rate) - lim) > 1 else add(f"{key} col{col + 1}: limit = rate x valuation", lim, lim)
            debt_rows, on = [], False
            for sec_, lab, a, _ in legal[key]:
                if lab.startswith("Total debt applicable"):
                    break
                if sec_.startswith("Amount of debt"):
                    debt_rows.append(a[col])
            tda = g("totaldebtapplicable")[col]
            add(f"{key} col{col + 1}: debt applicable sums", tda, sum(debt_rows))
            less = g("lessassets")[col]
            add(f"{key} col{col + 1}: net debt", g("netdebt")[col], tda - less)
            add(f"{key} col{col + 1}: margin", g("legaldebtmargin")[col], lim - g("netdebt")[col])
    if "legal_debt_margin" in legal:
        rows2 = {re.sub(r"[^a-z]", "", lab.lower()): a for _, lab, a, _ in legal["legal_debt_margin"]}
        bonds = sum(c[0] for l, c in lines if instrument(ent, l) and instrument(ent, l) not in ("sbita", "lease", "premium", "net_pension", "compensated_absences"))
        add("legal debt margin: debt applicable = bonds in the debt note", bonds, next(a for k, a in rows2.items() if re.match(r"totaldebtapplicable", k))[0])
    # assessed valuations
    if assessed:
        sec = assessed["sections"]
        ny = len(assessed["years"])
        val = sec["valuation"][0][1]
        for name in ("rates", "extensions", "collections"):
            rows3 = sec[name]
            body = [v for l, v in rows3 if l != "Total"]
            totrow = rows3[-1][1] if rows3[-1][0] == "Total" else None
            if totrow:
                for j in range(ny):
                    add(f"assessed {name} tax year {assessed['years'][j]}: sum = total", totrow[j], sum(v[j] for v in body), tol=Decimal("0.00001") if name == "rates" else 1)
        for l, v in sec["extensions"]:
            if l in ("Total",):
                continue
            rr = next((r[1] for r in sec["rates"] if re.sub(r"[^a-z]", "", r[0].lower())[:6] == re.sub(r"[^a-z]", "", l.lower())[:6]), None)
        pc = sec.get("percent")
        if pc:
            for j in range(ny):
                ext, col = sec["extensions"][-1][1][j], sec["collections"][-1][1][j]
                add(f"assessed tax year {assessed['years'][j]}: percent collected", pc[0][1][j], (col / ext * 100).quantize(Decimal("0.1")))
    return out


def write(ent, fy, doc, outstanding, sched, tail, activity, legal, assessed, checks, corrections, tp):
    out = ent.data_path("extracted", "audits")
    os.makedirs(out, exist_ok=True)
    cols = [r for r in csv.DictReader(open(ent.config_path("debt_schedule_columns.csv"), newline="")) if r["doc_id"] == doc]
    inst = [c["instrument_id"] for c in sorted(cols, key=lambda c: int(c["pair"]))] + ["total"]

    def w(name, header, rows_):
        with open(os.path.join(out, f"{doc}_{name}.csv"), "w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(header)
            wr.writerows(rows_)

    w("debt_outstanding", ["fiscal_year", "doc", "description", "instrument_id", "column", "amount", "pdf_page"],
      [(fy, doc, l, instrument(ent, l), nm, c[j], tp["outstanding"]) for l, c in outstanding for j, nm in enumerate(("balance", "current_portion"))])
    w("debt_schedule", ["fiscal_year", "doc", "period", "instrument_id", "kind", "amount", "pdf_page"],
      [(fy, doc, l, inst[j // 2], "principal" if j % 2 == 0 else "interest", c[j], tp["schedule"]) for l, c in sched for j in range(len(c))])
    w("debt_activity", ["fiscal_year", "doc", "description", "instrument_id", "column", "amount", "pdf_page"],
      [(fy, doc, l, instrument(ent, l), nm, c[j], tp["activity"]) for l, c in activity for j, nm in enumerate(("beginning", "new_issues", "retired", "ending"))])
    w("legal_debt_margin", ["fiscal_year", "doc", "schedule", "line_item", "column", "amount", "pdf_page"],
      [(fy, doc, key, lab, nm, a[j], tp[key]) for key, rs in legal.items() for _, lab, a, _ in rs for j, nm in enumerate(("this_year", "prior_year"))])
    rows_ = []
    for sec, rs in assessed["sections"].items():
        for lab, vals in rs:
            for yr, v in zip(assessed["years"], vals):
                rows_.append((fy, doc, yr, sec, lab, v))
    w("assessed_valuations", ["fiscal_year", "doc", "tax_year", "section", "line_item", "amount", "pdf_page"], [r + (tp["assessed"],) for r in rows_])
    w("debt_checks", ["doc", "check", "expected", "found", "result"], checks)
    w("debt_corrections", ["doc", "table", "method", "row", "raw", "final", "detail"], corrections)


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_audit"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    pages = get_pages(ent.source_path(src[doc]["pdf_path"]), doc)
    tp = table_pages(pages)
    outstanding, sched, tail, activity, legal, assessed, problems = parse(ent.source_path(src[doc]["pdf_path"]), fy, doc, ent, pages)
    corrections = []
    opath = ent.data_path("overrides", f"{doc}_debt.csv")  # rows OCR could not read, restored from the page image: kind add_row|row, raw "<table>|<label>"
    for o in (list(csv.DictReader(open(opath, newline=""))) if os.path.exists(opath) else []):
        table, label = o["raw"].split("|", 1)
        cells = [int(x) for x in o["fixed"].split("|")]
        target = {"schedule": sched, "activity": activity}[table]
        old = next((c for l, c in target if l == label), None)
        if o["kind"] == "add_row" and old is None:
            target.append((label, cells))
            if table == "schedule":  # keep the Total row last
                target.sort(key=lambda r: r[0] == "Total")
        elif o["kind"] == "row" and old is not None:
            target[[l for l, _ in target].index(label)] = (label, cells)
        else:
            problems.append(f"override not applicable: {o['raw']}")
        corrections.append((doc, table, o["kind"], label, "|".join(map(str, old or [])), o["fixed"], o["reason"]))
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    checks = check(ent, fy, doc, outstanding, sched, tail, activity, legal, assessed)
    fails = [c for c in checks if c[4] == "FAIL"]
    print(f"{doc}: {len(outstanding)} outstanding lines, {len(sched)} schedule rows, {len(activity)} activity rows, {len(checks)} checks, {len(fails)} failures")
    for c in fails:
        print("  FAIL", c[1], "expected", c[2], "found", c[3])
    if fails:
        sys.exit(1)
    write(ent, fy, doc, outstanding, sched, tail, activity, legal, assessed, checks, corrections, tp)


if __name__ == "__main__":
    main(sys.argv)
