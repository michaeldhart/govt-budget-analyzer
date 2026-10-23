"""Extract General Fund revenue lines from a Belvidere budget ordinance PDF.

Usage: python scripts/extract_budget.py FY2015 [--prep auto|gray|thr150] [--force]

--prep auto (default) extracts with every prep, keeps the one with the fewest validation
problems (ties go to the earlier in PREPS), and writes only that result.

Reads sources/budgets/FY20XX_budget.pdf (scanned), OCRs the General Fund Exhibit A
pages (cached in data/ocr/budget_pages/), and writes
data/extracted/FY20XX_budget_gf_revenue.csv in long format (one row per account per column).

Validation: each department's revenue rows must sum to its printed "Total ... Revenues"
line, and all 01-4 rows to "TOTAL GENERAL FUND REVENUES", in every column. Anything that
fails is listed and the script exits non-zero. OCR mistakes that have been checked by eye
against the page image are recorded in data/overrides/FY20XX_budget.csv (never silent):
    kind,page_label,key,column,value,note
    account,1 of 16,Replacement Tax|01-4-110-4420,,01-4-110-4120,...
    row,1 of 16,01-4-110-4440,,108364|93600|93600|180000,...   (all column values, checked by eye)
    row,3 of 16,Total Police Department Revenues,,1414931|1445664|1445664|1432063,...   (a whole printed total line, when OCR garbles several columns)
    total,3 of 16,Total Police Department Revenues,budget_FY2015,1275181,...
    add_row,7 of 16,01-4-340-UNLABELED,,0|0|26000,...   (a revenue line the source prints with no label or account; inserted into its department block)
    not_in_total,7 of 15,01-4-615-4490,budget_FY2021,,...   (revenue row the city leaves out of its printed fund total; blank column = every column)
    allow_diff,7 of 16,TOTAL GENERAL FUND REVENUES,budget_FY2016,-1,...   (city's printed total differs from its own rows)
"""
import csv
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
PREPS = ["gray", "thr150"]
# Fund digits and the dept code are sometimes misread/split ("071-5-215", "01-4-7 40"); fund is normalized to 01.
ACCT = re.compile(r"(?<![\d-])(\d{1,3})-([45])-(\d(?:\s?\d){1,3})-(\d{4}|\d{3}\s\d)\d?(?!\d)")  # last group may be split by a space ("447 1")
ACCT_NOHYPHEN = re.compile(r"(?<![\d-])(\d{1,3})-([45])-(\d{3})(\d{4})(?!\d)")  # city prints some as 01-4-1104114
LABEL = re.compile(r"(\d+)\.? of (\d+)")  # footer; OCR sometimes yields "1. of 16"
TOTAL_REV = re.compile(r"(?i)^(total\s.*|.*\sdepartment\s*-?\s*|.*\s-\s*)revenues?$")  # also "Street Department - Revenues", "Community Dev - Revenues"
FUND_TOTAL = re.compile(r"(?i)^total general fund revenues")
FIX = str.maketrans({"§": "5", "Q": "0", "r": "1", "t": "1", "s": "5", "S": "5", "O": "0", "o": "0", "l": "1", "I": "1", "g": "9", "B": "8"})
ZERO_JUNK = re.compile(r"^(?:[¢©]\)?|\w?\))$")  # OCR renderings of a printed 0, e.g. "¢)" or "4)"


def ocr_page(pdf, page, outdir, prep):
    """Render one page at 300 dpi, optionally binarize at the given threshold, tesseract psm 6.

    Which prep works best varies by year: FY2015 needed thr150 to recover the yellow-highlighted
    column; FY2016 reads better as plain grayscale (thresholding corrupts account digits).
    """
    txt = os.path.join(outdir, f"p{page:03d}.txt")
    if os.path.exists(txt):
        return open(txt).read()
    pgm = os.path.join(outdir, f"p{page:03d}.pgm")
    png = pgm.replace(".pgm", ".png")
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pgmraw", "-r300",
                    f"-dFirstPage={page}", f"-dLastPage={page}", f"-sOutputFile={pgm}", pdf], check=True)
    im = Image.open(pgm)
    if prep.startswith("thr"):
        t = int(prep[3:])
        im = im.point(lambda v: 255 if v > t else 0)
    im.save(png)
    out = subprocess.run(["tesseract", png, "-", "--psm", "6"], capture_output=True, text=True).stdout
    os.remove(pgm)
    os.remove(png)
    open(txt, "w").write(out)
    return out


def page_count(pdf):
    return int(subprocess.run(["qpdf", "--show-npages", pdf], capture_output=True, text=True).stdout.strip())


def to_int(tok):
    """OCR-tolerant number parse. Returns None if not a number."""
    t = tok.strip().replace("{", "(").replace("}", ")")
    neg = t.startswith("(")
    t = t.strip("()-").translate(FIX).replace(",", "").replace(".", "")
    if not t.isdigit():
        return None
    return -int(t) if neg else int(t)


def numbers(s, merge=False, fill_to=None):
    """Trailing numeric columns of a line as ints, plus the leftover text.

    merge=True also rejoins '989 000' -> '989,000' (OCR turning a comma into a space).
    It is only a retry because it wrongly glues real adjacent columns like '0 200'.
    fill_to=N: while fewer than N values are found, a short junk token (<=3 chars, e.g. 'fe)',
    '(e)', ']') is read as a printed 0. The caller flags such rows; sums still validate them.
    """
    if merge:
        s = re.sub(r"(?<![\d,])(\d{1,3}) (\d{3})(?![\d,])", r"\1,\2", s)
    s = re.sub(r"(?<=\d),\s+(?=\d{3}\b)", ",", s)  # "1445, 664" -> "1445,664"
    toks = s.rstrip(" :;|").split()
    # Trailing junk tokens (`!`, `) |`, `ie)`) after real numbers: stripped; if a row is then
    # short of values they stand in for printed trailing zeros.
    trailing = 0
    while len(toks) > 1 and len(toks[-1]) <= 3 and to_int(toks[-1].rstrip(":;|")) is None \
            and toks[-1] not in ("-", "—") and not ZERO_JUNK.match(toks[-1]) \
            and any(to_int(t) is not None for t in toks[-3:-1]):
        toks.pop()
        trailing += 1
    vals, guessed = [], []
    while toks:
        tk = toks[-1].rstrip(":;|")  # stray punctuation glued to a value, e.g. "6,500:"
        if tk in ("-", "—"):  # budgets print zeros as 0, so a lone dash is OCR noise
            toks.pop()
            continue
        v = 0 if ZERO_JUNK.match(tk) else to_int(tk)
        if v is None and fill_to and len(vals) < fill_to and len(tk) <= 3:
            v = 0
            guessed.append(tk)
        if v is None:
            break
        vals.append(v)
        toks.pop()
    if fill_to and trailing and len(vals) < fill_to:  # vals runs right-to-left here
        pad = min(trailing, fill_to - len(vals))
        vals = [0] * pad + vals
        guessed.extend(["trailing"] * pad)
    numbers.guessed = guessed
    return list(reversed(vals)), " ".join(toks)


def column_labels(fy, n):
    """Header text is unreliable under OCR, so derive labels from column count and FY."""
    y = int(fy)
    if n == 3:
        return [f"actual_FY{y-2}", f"budget_FY{y-1}", f"budget_FY{y}"]
    if n == 4:
        return [f"actual_FY{y-2}", f"budget_FY{y-1}", f"budget_revised_FY{y-1}", f"budget_FY{y}"]
    raise SystemExit(f"unexpected column count {n}: check headers by eye")


def resolve_dept(d, known):
    """Garbled department codes ('1 710' for '110') are matched to a known code that is a
    subsequence of the OCR'd digits, when exactly one such code exists."""
    if d in known or len(d) <= 3 and not known:
        return d
    def subseq(a, b):
        it = iter(b)
        return all(c in it for c in a)
    cands = [k for k in known if subseq(k, d)] if len(d) > 3 else []
    return cands[0] if len(cands) == 1 else d


def parse(pages, ncols=None):
    """pages: list of (page_label, text). Returns revenue rows, 'Total ... Revenues' lines, fund total.

    Each total records how many revenue rows precede it (`pos`); grouping into departments
    happens later, after account overrides are applied.
    """
    rows, checks, fund_total = [], [], None
    known = set()  # department codes that OCR'd cleanly as exactly 3 digits
    for _, text in pages:
        known.update(re.sub(r"\s", "", m.group(3)) for m in ACCT.finditer(text) if len(re.sub(r"\s", "", m.group(3))) == 3)
    for label, text in pages:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = ACCT.search(line)
            if not m:
                m2 = ACCT_NOHYPHEN.search(line)
                if m2:  # rebuild the match shape the code below expects
                    line = line[:m2.start()] + f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}-{m2.group(4)}" + line[m2.end():]
                    m = ACCT.search(line)
            if m:
                # Revenue = object code 4xxx. The source sometimes prints 01-5-... on a
                # revenue line (e.g. 01-5-310-4940 Expense Reimbursement); keep those, flagged.
                dept = resolve_dept(re.sub(r"\s", "", m.group(3)), known)
                obj = m.group(4).replace(" ", "")
                if m.group(2) == "4" or obj.startswith("4"):
                    vals, _ = numbers(line[m.end():], fill_to=ncols)
                    guess = bool(numbers.guessed)
                    if ncols and len(vals) != ncols:
                        alt, _ = numbers(line[m.end():], merge=True, fill_to=ncols)
                        if len(alt) == ncols:
                            vals, guess = alt, bool(numbers.guessed)
                    rows.append(dict(page_label=label, dept_code=dept,
                                     account=f"01-{m.group(2)}-{dept}-{obj}",
                                     line_item=line[:m.start()].strip(" .:|"), vals=vals, raw=raw,
                                     flag=("" if m.group(2) == "4" else "expense_prefix_on_revenue_line")
                                     + (" ocr_zero_guess" if guess else "")))
                continue
            vals, name = numbers(line)
            if ncols and vals and len(vals) != ncols:
                alt, alt_name = numbers(line, merge=True)
                if len(alt) == ncols:
                    vals, name = alt, alt_name
            if vals and FUND_TOTAL.match(name):
                fund_total = (name, vals, label)
            elif vals and TOTAL_REV.match(name):
                checks.append((name, vals, label, len(rows)))
    return rows, checks, fund_total


def load_overrides(fy):
    path = os.path.join(ROOT, "data", "overrides", f"FY{fy}_budget.csv")
    acct, total, allow, rowv, notin, addr = {}, {}, {}, {}, {}, []
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "account":
                acct[(o["page_label"], o["key"])] = o["value"]
            elif o["kind"] == "total":
                total[(o["page_label"], o["key"], o["column"])] = int(o["value"])
            elif o["kind"] == "row":
                rowv[(o["page_label"], o["key"])] = [int(v) for v in o["value"].split("|")]
            elif o["kind"] == "add_row":
                addr.append((o["page_label"], o["key"], [int(v) for v in o["value"].split("|")], o["note"]))
            elif o["kind"] == "not_in_total":
                notin.setdefault((o["page_label"], o["key"]), set())
                if o["column"]:
                    notin[(o["page_label"], o["key"])].add(o["column"])
            elif o["kind"] == "allow_diff":
                allow[(o["page_label"], o["key"], o["column"])] = int(o["value"])
    return acct, total, allow, rowv, notin, addr


def extract(fy, prep, force=False):
    """OCR (cached), parse and validate one budget with one prep. Returns a result dict; writes nothing."""
    pdf = os.path.join(ROOT, "sources", "budgets", f"FY{fy}_budget.pdf")
    if not os.path.exists(pdf):  # FY2027 lives directly under sources/ with its ordinance number
        alt = os.path.join(ROOT, "sources", f"FY{fy}_budget_753H.pdf")
        pdf = alt if os.path.exists(alt) else pdf
    outdir = os.path.join(ROOT, "data", "ocr", "budget_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    if force:
        for f in os.listdir(outdir):
            os.remove(os.path.join(outdir, f))
    n = page_count(pdf)
    with ThreadPoolExecutor(4) as ex:
        texts = list(ex.map(lambda p: ocr_page(pdf, p, outdir, prep), range(1, n + 1)))

    # First General Fund page: "GENERAL FUND" + "EXHIBIT A" in either order, or (FY2024) a page that
    # opens with a bare "GENERAL FUND" heading and holds the first General Administration revenue line.
    start = next(i for i, t in enumerate(texts)
                 if re.search(r"GENERAL FUND\s+EXHIBIT A|EXHIBIT A\s+GENERAL\s?FUND", t, re.I)
                 or (re.search(r"GENERAL\s?FUND", t[:100], re.I) and re.search(r"-4-110-4010", t)))
    end = next(i for i, t in enumerate(texts) if re.search(r"TOTAL GENERAL FUND REVENUES", t, re.I))
    pages = []
    for n_, t in enumerate(texts[start:end + 1], start + 1):
        m = LABEL.search(t[-200:])
        pages.append((f"{m.group(1)} of {m.group(2)}" if m else f"pdf{n_}", t))  # footer unreadable: use pdf page

    rows, _, _ = parse(pages)
    counts = [len(r["vals"]) for r in rows]
    ncols = max(set(counts), key=counts.count)
    cols = column_labels(fy, ncols)
    rows, checks, fund_total = parse(pages, ncols)

    problems_pre = []
    acct_fix, total_fix, allow_diff, row_fix, not_in_total, add_rows = load_overrides(fy)
    used = set()
    for r in rows:
        k = (r["page_label"], f"{r['line_item']}|{r['account']}")
        if k in acct_fix:
            r["account"], r["dept_code"] = acct_fix[k], acct_fix[k].split("-")[2]
            used.add(k)
        # row overrides match on the account alone (line-item text is OCR noise); the long
        # "item|account" form is still accepted
        kr = next((k for k in ((r["page_label"], r["account"]), (r["page_label"], f"{r['line_item']}|{r['account']}"))
                   if k in row_fix), None)
        if (r["page_label"], r["account"]) in not_in_total:
            r["flag"] = (r["flag"] + " not_in_printed_total").strip()
            r["skip_cols"] = not_in_total[(r["page_label"], r["account"])] or set(cols)
            used.add((r["page_label"], r["account"]))
        if kr:
            r["vals"] = row_fix[kr]
            r["flag"] = (r["flag"] + " " if r["flag"] else "") + "values_overridden"
            used.add(kr)

    for page_label, key, vals, note in add_rows:
        dept = key.split("-")[2]
        # insert after the last revenue row of that department on that page, i.e. inside its block
        at = max((i for i, r in enumerate(rows) if r["page_label"] == page_label and r["dept_code"] == dept), default=None)
        if at is None:
            problems_pre.append(f"add_row {key}: no rows for dept {dept} on page {page_label}")
            continue
        rows.insert(at + 1, dict(page_label=page_label, dept_code=dept, account=key, line_item="(unlabeled in source)",
                                 vals=vals, raw=note, flag="added_by_override"))
        # totals record how many rows precede them; shift those that come after the insertion point
        checks[:] = [(n_, v_, l_, pos + 1 if pos > at else pos) for n_, v_, l_, pos in checks]
        used.add((page_label, key))

    def printed(label, name, vals):
        """Printed total per column, with any eyeballed overrides applied."""
        out = list(vals)
        for i, c in enumerate(cols):
            if (label, name, c) in total_fix:
                out[i] = total_fix[(label, name, c)]
                used.add((label, name, c))
        return out

    problems, notes = list(problems_pre), []
    good = [r for r in rows if len(r["vals"]) == ncols]
    for r in rows:
        if len(r["vals"]) != ncols:
            problems.append(f"{r['page_label']} {r['account']} {r['line_item']!r}: {len(r['vals'])} values, expected {ncols}: {r['raw']}")
    seen = {}
    for r in good:
        key = r["account"]
        if key in seen:
            problems.append(f"duplicate account {key}: {seen[key]['line_item']!r} ({seen[key]['page_label']}) and {r['line_item']!r} ({r['page_label']}); one is an OCR misread")
        seen[key] = r

    def compare(label, name, vals, got_rows, tol):
        if (label, name) in row_fix:  # whole printed total line replaced after checking the page image
            vals = row_fix[(label, name)]
            used.add((label, name))
        if len(vals) != ncols:
            problems.append(f"{label} '{name}': {len(vals)} values read, expected {ncols}: {vals}")
            return
        vals = printed(label, name, vals)
        for i, c in enumerate(cols):
            got = sum(r["vals"][i] for r in got_rows
                      if len(r["vals"]) == ncols and c not in r.get("skip_cols", ()))
            diff = vals[i] - got
            if diff and c.startswith("actual") and abs(diff) <= (len(got_rows) + 1) // 2:
                notes.append(f"{label} '{name}' {c}: off by {diff} (each printed actual is rounded from cents; tolerance is half a dollar per row)")
            elif diff and allow_diff.get((label, name, c)) == diff:
                used.add((label, name, c))
                notes.append(f"{label} '{name}' {c}: printed total is {diff:+,} vs its own rows (source arithmetic; allowed by override)")
            elif diff:
                msg = f"{label} '{name}' {c}: printed {vals[i]:,} vs rows {got:,} (diff {diff:,})"
                if "revised" in c:  # leading 1 is often misread as 4 here; show where revised != budget
                    j = i - 1
                    sus = [f"{r['account']} {r['vals'][j]:,}->{r['vals'][i]:,}" for r in got_rows
                           if len(r["vals"]) == ncols and r["vals"][i] != r["vals"][j]]
                    if sus:
                        msg += "; rows where revised != budget: " + ", ".join(sus[:6])
                problems.append(msg)

    # Prior-year check: this document's prior-year budget column should equal the previous
    # year's extract (true for every shared account so far). Mismatches point at OCR errors in
    # one of the two documents; they are listed as hints on failing totals, not failures.
    prior_hint = {}
    prev = os.path.join(ROOT, "data", "extracted", f"FY{int(fy)-1}_budget_gf_revenue.csv")
    if os.path.exists(prev):
        pc = f"budget_FY{int(fy)-1}"
        pv = {r["account"]: int(r["amount"]) for r in csv.DictReader(open(prev)) if r["column"] == pc}
        ci = cols.index(pc)
        for r in good:
            if r["account"] in pv and pv[r["account"]] != r["vals"][ci]:
                prior_hint[r["account"]] = f"{r['account']} {r['line_item']!r} {pc}: {r['vals'][ci]:,} here vs {pv[r['account']]:,} in FY{int(fy)-1} extract"
    for h in prior_hint.values():
        notes.append("prior-year mismatch: " + h)

    claimed, total_at = set(), {}
    for name, vals, label, pos in checks:
        # A total follows its department's last revenue row, so that row names the department.
        # It claims that department's unclaimed rows above it. Others (Audit, which has no
        # total; 01-4-110-4540, which sits in the expense block) stay unclaimed.
        dept = rows[pos - 1]["dept_code"]
        mine = [i for i in range(pos) if i not in claimed and rows[i]["dept_code"] == dept]
        claimed.update(mine)
        total_at.setdefault(dept, pos)
        compare(label, name, vals, [rows[i] for i in mine], None)
    # A 01-4 row for a department that appears after that department's own revenue total
    # sits in its expense block (a contra-expense such as 01-4-110-4540 Health Insurance
    # Reimbursement). It is excluded from the fund revenue total.
    for i, r in enumerate(rows):
        if i not in claimed and r["dept_code"] in total_at and i >= total_at[r["dept_code"]]:
            r["flag"] = (r["flag"] + " expense_block_offset").strip()
    unclaimed = [r for i, r in enumerate(rows) if i not in claimed]
    if unclaimed:
        notes.append("rows outside any department total: " + ", ".join(f"{r['account']} ({r['line_item']})" for r in unclaimed))
    if fund_total:
        name, vals, label = fund_total
        compare(label, name, vals, [r for r in rows if "expense_block_offset" not in r["flag"]], None)
    else:
        problems.append("no TOTAL GENERAL FUND REVENUES line found")
    for k in (set(acct_fix) | set(total_fix) | set(allow_diff) | set(row_fix) | set(not_in_total) | {(a_[0], a_[1]) for a_ in add_rows}) - used:
        notes.append(f"override never used (OCR text may have changed): {k}")

    return dict(prep=prep, rows=rows, good=good, cols=cols, checks=checks, problems=problems, notes=notes,
                pdf_pages=(start + 1, end + 1))


def write_csv(fy, res):
    os.makedirs(os.path.join(ROOT, "data", "extracted"), exist_ok=True)
    out = os.path.join(ROOT, "data", "extracted", f"FY{fy}_budget_gf_revenue.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"])
        for r in res["good"]:
            for c, v in zip(res["cols"], r["vals"]):
                w.writerow([fy, f"FY{fy}_budget", r["page_label"], "General", r["dept_code"], r["account"], r["line_item"], c, v, r["flag"]])
    return out


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    force = "--force" in sys.argv
    results = []
    for prep in (PREPS if arg == "auto" else [arg]):
        res = extract(fy, prep, force)
        results.append(res)
        print(f"prep {prep}: {len(res['problems'])} problems, {len(res['good'])}/{len(res['rows'])} rows usable")
    res = min(results, key=lambda r: len(r["problems"]))  # min() keeps the earliest on ties
    out = write_csv(fy, res)
    print(f"\nchosen prep: {res['prep']}; General Fund pdf pages {res['pdf_pages'][0]}-{res['pdf_pages'][1]}")
    print("columns:", res["cols"])
    print(f"{len(res['rows'])} revenue rows, {len(res['good'])} written -> {os.path.relpath(out, ROOT)}")
    print(f"{len(res['checks'])} department totals + fund total checked; {len(res['problems'])} problems, {len(res['notes'])} notes")
    for p in res["notes"]:
        print("  note:", p)
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
