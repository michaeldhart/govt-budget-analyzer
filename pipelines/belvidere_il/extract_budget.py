"""Extract General Fund revenue and expense lines from a Belvidere budget ordinance PDF.

Usage: python3 pipelines/belvidere_il/extract_budget.py FY2015 [--prep auto|gray|thr150] [--force]

--prep auto (default) extracts with every prep, keeps the one with the fewest validation
problems (ties go to the earlier in PREPS), and writes only that result.

Reads sources/belvidere_il/budgets/FY20XX_budget.pdf (scanned), OCRs the General Fund Exhibit A
pages (cached in data/belvidere_il/ocr/budget_pages/), and writes
data/belvidere_il/extracted/FY20XX_budget_gf_revenue.csv, ..._gf_expense.csv (both long format: one row per account per column)
and ..._gf_totals.csv (every total the city prints, for reconciliation).

Validation: each department's revenue rows must sum to its printed "Total ... Revenues"
line, and all 01-4 rows to "TOTAL GENERAL FUND REVENUES", in every column. Expense rows (01-5, object 5xxx-9xxx) must tie to
every printed subtotal / department expense total / NET line / TOTAL GENERAL FUND EXPENSES (check_expenses). Cells whose
failure another reading of the same cell explains exactly are resolved automatically and flagged (autofix). Anything that
fails is listed and the script exits non-zero. OCR mistakes that have been checked by eye
against the page image are recorded in data/belvidere_il/overrides/FY20XX_budget.csv (never silent):
    kind,page_label,key,column,value,note
    account,1 of 16,Replacement Tax|01-4-110-4420,,01-4-110-4120,...
    row,1 of 16,01-4-110-4440,,108364|93600|93600|180000,...   (all column values, checked by eye)
    row,3 of 16,Total Police Department Revenues,,1414931|1445664|1445664|1432063,...   (a whole printed total line, when OCR garbles several columns)
    total,3 of 16,Total Police Department Revenues,budget_FY2015,1275181,...
    add_row,7 of 16,01-4-340-UNLABELED,,0|0|26000,...   (a revenue line the source prints with no label or account; inserted into its department block;
                                                            a 01-5 key adds an expense line the OCR lost, and the `column` field may carry its label)
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
ROOT = os.path.join(HERE, "..", "..")
DATA_E = os.path.join(ROOT, "data", "belvidere_il")
SRC_E = os.path.join(ROOT, "sources", "belvidere_il")
PREPS = ["gray", "thr150"]
# Fund digits and the dept code are sometimes misread/split ("071-5-215", "01-4-7 40"); fund is normalized to 01.
ACCT = re.compile(r"(?<![\d-])(\d{1,3})-([45§])-(\d(?:\s?\d){1,3})-(\d{4}|\d{3}\s\d)\d?(?!\d)")  # last group may be split by a space ("447 1")
ACCT_NOHYPHEN = re.compile(r"(?<![\d-])(\d{1,3})-([45])-(\d{3})(\d{4})(?!\d)")  # city prints some as 01-4-1104114
LABEL = re.compile(r"(\d+)\.? of (\d+)")  # footer; OCR sometimes yields "1. of 16"
TOTAL_REV = re.compile(r"(?i)^(total\s.*|.*\sdepartment\s*-?\s*|.*\s-\s*)revenues?$")  # also "Street Department - Revenues", "Community Dev - Revenues"
FUND_TOTAL = re.compile(r"(?i)^total general fund revenues")
# Expense side. A department block runs revenue rows, optional revenue total, expense rows (with optional
# Personnel / Contractual / Supplies subtotals), a department expense total, then a NET line.
FUND_EXP_TOTAL = re.compile(r"(?i)^total general fund expens")
FUND_NET = re.compile(r"(?i)^net rev")
NET_LINE = re.compile(r"(?i)^(net\b|\w+ net$)")  # "NET - POLICE DEPARTMENT", "STROLLS NET"
SUBTOTAL = re.compile(r"(?i)(personn?e|benefit|contractual|supplies).*expens\w*$")  # OCR reads "Personne!" for "Personnel"
EXP_TOTAL = re.compile(r"(?i)^(total\s.*expens\w*|.*\s(department|dept)\s*-?\s*expens\w*|.*\sexpens\w*)$")
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
                if m.group(2) in ("4",) or obj.startswith("4"):
                    vals, _ = numbers(line[m.end():], fill_to=ncols)
                    guess = bool(numbers.guessed)
                    if ncols and len(vals) != ncols:
                        alt, _ = numbers(line[m.end():], merge=True, fill_to=ncols)
                        if len(alt) == ncols:
                            vals, guess = alt, bool(numbers.guessed)
                    rows.append(dict(page_label=label, dept_code=dept,
                                     account=f"01-{m.group(2).replace(chr(167), chr(53))}-{dept}-{obj}",
                                     line_item=line[:m.start()].strip(" .:|"), vals=vals, raw=raw,
                                     flag=("" if m.group(2) in ("4",) else "expense_prefix_on_revenue_line")
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


def parse_expenses(pages, ncols):
    """Expense rows (object codes 5xxx-9xxx) and the lines that check them, in page order.

    Returns (exp_rows, events). Every event records `rev_pos` and `exp_pos`, the number of revenue and expense
    rows seen before it, so a check can slice either list. Event kinds: net (a department's NET line),
    exptotal (a department's expense total), subtotal (Personnel / Contractual / Supplies), revtotal (a department
    revenue total, which only marks a boundary), fund_exp (TOTAL GENERAL FUND EXPENSES), fund_net.
    """
    exp_rows, events, nrev = [], [], 0
    known = set()
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
                if m2:
                    line = line[:m2.start()] + f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}-{m2.group(4)}" + line[m2.end():]
                    m = ACCT.search(line)
            if m:
                dept = resolve_dept(re.sub(r"\s", "", m.group(3)), known)
                obj = m.group(4).replace(" ", "")
                if m.group(2) in ("4",) or obj.startswith("4"):
                    nrev += 1  # revenue rows are parsed by parse(); only their count matters here
                    continue
                tail = re.sub(r"(\d) ,(\d{3})", r"\1,\2", line[m.end():])  # "297 ,036" -> "297,036"
                vals, _ = numbers(tail, fill_to=ncols)
                guess = bool(numbers.guessed)
                if ncols and len(vals) != ncols:
                    alt, _ = numbers(tail, merge=True, fill_to=ncols)
                    if len(alt) == ncols:
                        vals, guess = alt, bool(numbers.guessed)
                exp_rows.append(dict(page_label=label, dept_code=dept, account=f"01-5-{dept}-{obj}",
                                     line_item=line[:m.start()].strip(" .:|"), vals=vals, raw=raw,
                                     flag="ocr_zero_guess" if guess else ""))
                continue
            line = re.sub(r"(\d) ,(\d{3})", r"\1,\2", line)
            vals, name = numbers(line)
            if ncols and vals and len(vals) != ncols:
                alt, alt_name = numbers(line, merge=True)
                if len(alt) != ncols:  # a seven-digit total whose last comma was lost: "7,224 853"
                    alt, alt_name = numbers(re.sub(r"(?<![\d,])(\d{1,3},\d{3}) (\d{3})(?![\d,])", r"\1,\2", line))
                if len(alt) == ncols:
                    vals, name = alt, alt_name
            if not vals:
                continue
            name = name.lstrip("| ")  # a table border read as a leading pipe
            if FUND_EXP_TOTAL.match(name):
                kind = "fund_exp"
            elif FUND_NET.match(name):
                kind = "fund_net"
            elif FUND_TOTAL.match(name):
                continue
            elif NET_LINE.match(name):
                kind = "net"
            elif TOTAL_REV.match(name):
                kind = "revtotal"
            elif SUBTOTAL.search(name):
                kind = "subtotal"
            elif EXP_TOTAL.match(name):
                kind = "exptotal"
            else:
                continue
            events.append(dict(kind=kind, name=name, vals=vals, label=label, rev_pos=nrev, exp_pos=len(exp_rows)))
    return exp_rows, events


def load_overrides(fy):
    path = os.path.join(DATA_E, "overrides", f"FY{fy}_budget.csv")
    acct, total, allow, rowv, notin, addr = {}, {}, {}, {}, {}, []
    scope = {}
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "account":
                acct[(o["page_label"], o["key"])] = o["value"]
            elif o["kind"] == "total":
                total[(o["page_label"], o["key"], o["column"])] = int(o["value"])
            elif o["kind"] == "row":
                rowv[(o["page_label"], o["key"])] = [int(v) for v in o["value"].split("|")]
            elif o["kind"] == "add_row":
                addr.append((o["page_label"], o["key"], [int(v) for v in o["value"].split("|")], o["note"], o["column"]))
            elif o["kind"] == "not_in_total":
                notin.setdefault((o["page_label"], o["key"]), set())
                if o["value"]:
                    scope[(o["page_label"], o["key"])] = o["value"]  # "fund": omitted from the fund total only, not from department checks
                if o["column"]:
                    notin[(o["page_label"], o["key"])].add(o["column"])
            elif o["kind"] == "allow_diff":
                allow[(o["page_label"], o["key"], o["column"])] = int(o["value"])
    return acct, total, allow, rowv, notin, addr, scope


def check_expenses(events, rows, exp_rows, fund_total, cols, compare):
    """Tie the expense rows to every printed expense total, subtotal and NET line.

    Expense of a block = its expense rows plus its contra rows (01-4 revenue lines printed in the expense block, flagged
    expense_block_offset, which reduce expense). NET = revenue (excluding contra rows) - expense. `compare` is the
    revenue check's comparator, so overrides (total, row, allow_diff) and the rounding tolerance work the same way.
    """
    def skip(r, kind):
        """Columns a row is left out of for one kind of check (not_in_total value `fund` = fund total only)."""
        return r.get("skip_cols", ()) if (r.get("skip_scope") != "fund" or kind == "fund_exp") else ()

    def neg(r, kind):
        return dict(vals=[-v for v in r["vals"]], account=r.get("account", "(contra)"), skip_cols=skip(r, kind),
                    _row=r.get("_row"), _sign=-1)

    def contra(i):
        return "expense_block_offset" in rows[i]["flag"]

    def expense(ra, rb, ea, eb, kind):
        return [dict(r, skip_cols=skip(r, kind), _row=r, _sign=1)
                for r in exp_rows[ea:eb] + [rows[i] for i in range(ra, rb) if contra(i)]]

    def revenue(ra, rb):
        # skip_cols marks revenue the city leaves out of its printed REVENUE totals; its NET lines still include it
        return [dict(vals=rows[i]["vals"], account=rows[i]["account"]) for i in range(ra, rb) if not contra(i)]

    net_start = (0, 0)        # (rev_pos, exp_pos) after the previous NET line
    sub_start = (0, 0)        # after the previous subtotal / revenue total / NET line
    for e in events:
        pos = (e["rev_pos"], e["exp_pos"])
        k, name, vals, label = e["kind"], e["name"], e["vals"], e["label"]
        if k == "revtotal":
            sub_start = pos
        elif k == "subtotal":
            compare(label, name, vals, expense(sub_start[0], pos[0], sub_start[1], pos[1], k), None)
            sub_start = pos
        elif k == "exptotal":
            compare(label, name, vals, expense(net_start[0], pos[0], net_start[1], pos[1], k), None)
        elif k == "net":
            compare(label, name, vals, revenue(net_start[0], pos[0]) +
                    [neg(r, k) for r in expense(net_start[0], pos[0], net_start[1], pos[1], k)], None)
            net_start = sub_start = pos
        elif k == "fund_exp":
            compare(label, name, vals, expense(0, pos[0], 0, pos[1], k), None)
        elif k == "fund_net" and fund_total:
            _, rev_vals, _ = fund_total
            fe = next((x for x in events if x["kind"] == "fund_exp"), None)
            if fe and len(rev_vals) == len(cols) == len(vals) == len(fe["vals"]):
                compare(label, name, vals, [dict(vals=rev_vals, account="fund revenues"), dict(vals=[-v for v in fe["vals"]], account="fund expenses")], None)


# Single-digit OCR confusions seen in these scans (a leading 1 read as 4, 6 read as 8, ...).
CONFUSIONS = {"1": "47", "4": "1", "6": "85", "8": "630", "5": "6", "3": "8", "0": "8", "7": "1"}


def digit_variants(v, extra=None):
    """Values one confusable digit away from v (v > 0), each a plausible OCR misread's true value. `extra` adds confusions
    ({digit: possible true digits}) for a caller that has seen more of them."""
    sv, out = str(abs(v)), set()
    conf = {d: CONFUSIONS.get(d, "") + (extra or {}).get(d, "") for d in set(CONFUSIONS) | set(extra or {})}
    for k, d in enumerate(sv):
        for c in conf.get(d, ""):
            t = sv[:k] + c + sv[k + 1:]
            if t[0] != "0" or len(t) == 1:
                out.add(int(t))
    out.discard(abs(v))
    return out


def autofix(events, rows, exp_good, fund_total, cols, ncols, alts, compare_diffs, notes):
    """Resolve OCR misreads without looking at the page.

    Tier 1: a cell can have several independent readings (the other OCR prep; the neighbouring year's document, which
    prints the same budget figure in its prior-year column). Tier 2: a single-digit OCR confusion (CONFUSIONS). A
    candidate is adopted only when swapping it in makes a failing printed total tie (within the half-dollar-per-row
    rounding tolerance of actual columns) and it is the only candidate that does (a pair of cells is tried when no
    single one explains the difference), so the printed totals stay the judge. The row is flagged `auto_resolved`
    (tier 2: `auto_resolved_digit`, the weaker evidence) and every change is listed in the notes.
    """
    def close(change, diff, tol):
        return abs(change - diff) <= tol

    fixed = set()  # (row, column) cells already changed: a cell is never changed twice
    for _ in range(60):
        fails = []
        check_expenses(events, rows, exp_good, fund_total, cols, lambda label, name, vals, got, tol: compare_diffs(label, name, vals, got, fails))
        changed = False
        done = set()
        for got_rows, i, diff, tol in fails:
            if abs(diff) <= 1:  # the city's own totals are often a dollar off its lines; that is not an OCR misread
                continue
            real, synth = [], []
            for g in got_rows:
                r = g.get("_row")
                if r is None or id(r) in done or (id(r), i) in fixed or "_sign" not in g or len(r["vals"]) != ncols:
                    continue
                a_ = alts.get(r.get("_key"))
                if a_:
                    for v, src in a_["cells"][i]:
                        if v != r["vals"][i]:
                            real.append((g["_sign"] * (v - r["vals"][i]), r, i, v, src))
                if r["vals"][i] > 0:
                    for v in digit_variants(r["vals"][i]):
                        synth.append((g["_sign"] * (v - r["vals"][i]), r, i, v, "a single-digit OCR confusion"))
            pick = None
            for tier in (real, synth if abs(diff) >= 10 else []):  # a digit confusion is too weak to explain a small difference
                single = [c for c in tier if close(c[0], diff, tol)]
                if len({(id(c[1]), c[3]) for c in single}) == 1:
                    pick = [single[0]]
                elif not single:
                    pairs = [(a, b) for k, a in enumerate(tier) for b in tier[k + 1:] if a[1] is not b[1] and close(a[0] + b[0], diff, tol)]
                    if len(pairs) == 1 and tier is real:
                        pick = list(pairs[0])
                if pick:
                    weak = tier is synth
                    break
            if pick:
                for _d, r, col, v, src in pick:
                    notes.append(f"auto-resolved {r['account']} {cols[col]}: {r['vals'][col]:,} -> {v:,} (from {src}; makes the printed totals tie)")
                    r["vals"][col] = v
                    tag = "auto_resolved_digit" if weak else "auto_resolved"
                    if tag not in r["flag"]:
                        r["flag"] = (r["flag"] + " " + tag).strip()
                    done.add(id(r))
                    fixed.add((id(r), col))
                changed = True
                break  # the other failures were measured before this fix: recheck everything before the next one
        if not changed:
            break


def build_alts(fy, prep, first):
    """Other readings of every expense cell of this prep's extract: the other preps' rows and the neighbouring documents."""
    res = first[prep]
    cols = res["cols"]
    alts = {}
    occ = {}
    for r in res["exp_alt_rows"]:
        n_ = occ[r["account"]] = occ.get(r["account"], 0) + 1
        alts[(r["account"], n_)] = dict(cells=[[] for _ in cols], full=[])
    other = {}
    for p_, o in first.items():
        if p_ == prep:
            continue
        seen = {}
        for r in o["exp_alt_rows"]:
            n_ = seen[r["account"]] = seen.get(r["account"], 0) + 1
            if len(r["vals"]) == len(cols):
                other[(r["account"], n_)] = (r["vals"], f"OCR prep {p_}")
    for key, (vals, src) in other.items():
        if key in alts:
            alts[key]["full"].append((vals, src))
            for i, v in enumerate(vals):
                alts[key]["cells"][i].append((v, src))
    # neighbouring documents print the same budget figure: document Y-1's request column is this document's prior-year column
    for nb, mine, theirs in ((int(fy) - 1, f"budget_FY{int(fy)-1}", f"budget_FY{int(fy)-1}"), (int(fy) + 1, f"budget_FY{fy}", f"budget_FY{fy}")):
        path = os.path.join(DATA_E, "extracted", f"FY{nb}_budget_gf_expense.csv")
        if not os.path.exists(path) or mine not in cols:
            continue
        nbv = {}
        for r in csv.DictReader(open(path)):
            if r["column"] == theirs:
                nbv.setdefault(r["account"], []).append(int(r["amount"]))
        ci = cols.index(mine)
        occ = {}
        for r in res["exp_alt_rows"]:
            n_ = occ[r["account"]] = occ.get(r["account"], 0) + 1
            vs = nbv.get(r["account"], [])
            if n_ <= len(vs):
                alts[(r["account"], n_)]["cells"][ci].append((vs[n_ - 1], f"the FY{nb} document"))
    return alts


def extract(fy, prep, force=False, alts=None):
    """OCR (cached), parse and validate one budget with one prep. Returns a result dict; writes nothing."""
    pdf = os.path.join(SRC_E, "budgets", f"FY{fy}_budget.pdf")
    if not os.path.exists(pdf):  # FY2027 lives directly under sources/ with its ordinance number
        alt = os.path.join(SRC_E, f"FY{fy}_budget_753H.pdf")
        pdf = alt if os.path.exists(alt) else pdf
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"FY{fy}_{prep}")
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
    exp_rows, events = parse_expenses(pages, ncols)

    problems_pre = []
    auto_notes = []
    alts = alts or {}
    occ = {}
    for r in exp_rows:  # alternate readings of the same row (other OCR prep, neighbouring documents), keyed by account + nth occurrence
        n_ = occ[r["account"]] = occ.get(r["account"], 0) + 1
        r["_key"] = (r["account"], n_)
    acct_fix, total_fix, allow_diff, row_fix, not_in_total, add_rows, not_in_total_scope = load_overrides(fy)
    used = set()
    for r in rows + exp_rows:
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
            r["skip_scope"] = not_in_total_scope.get((r["page_label"], r["account"]), "")
            used.add((r["page_label"], r["account"]))
        if kr:
            r["vals"] = row_fix[kr]
            r["flag"] = (r["flag"] + " " if r["flag"] else "") + "values_overridden"
            used.add(kr)

    for page_label, key, vals, note, label in add_rows:
        dept = key.split("-")[2]
        is_exp = key.split("-")[1] == "5"  # an expense line the source prints without a label or an OCR-readable row
        lst = exp_rows if is_exp else rows
        # insert after the last row of that department on that page, i.e. inside its block
        at = max((i for i, r in enumerate(lst) if r["page_label"] == page_label and r["dept_code"] == dept), default=None)
        if at is None:
            problems_pre.append(f"add_row {key}: no rows for dept {dept} on page {page_label}")
            continue
        lst.insert(at + 1, dict(page_label=page_label, dept_code=dept, account=key,
                                line_item=label or "(unlabeled in source)",  # add_row's `column` field carries the label, when the page shows one
                                vals=vals, raw=note, flag="added_by_override"))
        if is_exp:
            for e in events:
                if e["exp_pos"] > at:
                    e["exp_pos"] += 1
        else:
            # totals record how many rows precede them; shift those that come after the insertion point
            checks[:] = [(n_, v_, l_, pos + 1 if pos > at else pos) for n_, v_, l_, pos in checks]
            for e in events:
                if e["rev_pos"] > at:
                    e["rev_pos"] += 1
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
    exp_notes = []
    printed_log = []  # every printed total line checked, with eyeballed corrections applied
    good = [r for r in rows if len(r["vals"]) == ncols]
    for r in rows:
        if len(r["vals"]) != ncols:
            problems.append(f"{r['page_label']} {r['account']} {r['line_item']!r}: {len(r['vals'])} values, expected {ncols}: {r['raw']}")
    for r in exp_rows:  # a row this prep could not read in full: take another reading of the whole row if there is one
        a_ = alts.get(r.get("_key"))
        if a_ and len(r["vals"]) != ncols and a_["full"]:
            r["vals"], src = a_["full"][0]
            r["flag"] = (r["flag"] + " auto_resolved").strip()
            auto_notes.append(f"auto-resolved row {r['account']} on {r['page_label']}: read in full from {src}")
    exp_good = [r for r in exp_rows if len(r["vals"]) == ncols]
    for r in exp_rows:
        if len(r["vals"]) != ncols:
            problems.append(f"expense row: {r['page_label']} {r['account']} {r['line_item']!r}: {len(r['vals'])} values, expected {ncols}: {r['raw']}")
    seen = {}
    for r in good + exp_good:
        key = r["account"]
        if key in seen:
            problems.append(f"{'expense row: ' if '-5-' in key and key in {r_['account'] for r_ in exp_good} else ''}duplicate account {key}: {seen[key]['line_item']!r} ({seen[key]['page_label']}) and {r['line_item']!r} ({r['page_label']}); one is an OCR misread")
        seen[key] = r

    def compare(label, name, vals, got_rows, tol, sink=None, nsink=None):
        sink = problems if sink is None else sink
        nsink = notes if nsink is None else nsink
        if (label, name) in row_fix:  # whole printed total line replaced after checking the page image
            vals = row_fix[(label, name)]
            used.add((label, name))
        if len(vals) != ncols:
            sink.append(f"{label} '{name}': {len(vals)} values read, expected {ncols}: {vals}")
            return
        vals = printed(label, name, vals)
        printed_log.append((label, name, list(vals)))
        for i, c in enumerate(cols):
            got = sum(r["vals"][i] for r in got_rows
                      if len(r["vals"]) == ncols and c not in r.get("skip_cols", ()))
            diff = vals[i] - got
            if diff and c.startswith("actual") and abs(diff) <= (len(got_rows) + 1) // 2:
                nsink.append(f"{label} '{name}' {c}: off by {diff} (each printed actual is rounded from cents; tolerance is half a dollar per row)")
            elif diff and allow_diff.get((label, name, c)) == diff:
                used.add((label, name, c))
                nsink.append(f"{label} '{name}' {c}: printed total is {diff:+,} vs its own rows (source arithmetic; allowed by override)")
            elif diff:
                msg = f"{label} '{name}' {c}: printed {vals[i]:,} vs rows {got:,} (diff {diff:,})"
                if "revised" in c:  # leading 1 is often misread as 4 here; show where revised != budget
                    j = i - 1
                    sus = [f"{r['account']} {r['vals'][j]:,}->{r['vals'][i]:,}" for r in got_rows
                           if len(r["vals"]) == ncols and r["vals"][i] != r["vals"][j]]
                    if sus:
                        msg += "; rows where revised != budget: " + ", ".join(sus[:6])
                sink.append(msg)

    def compare_diffs(label, name, vals, got_rows, fails):
        """Like compare(), but only records (rows, column index, diff) for every unexplained difference."""
        if (label, name) in row_fix:
            vals = row_fix[(label, name)]
        if len(vals) != ncols:
            return
        vals = [total_fix.get((label, name, c), v) for c, v in zip(cols, vals)]
        for i, c in enumerate(cols):
            got = sum(r["vals"][i] for r in got_rows if len(r["vals"]) == ncols and c not in r.get("skip_cols", ()))
            diff = vals[i] - got
            if diff and not (c.startswith("actual") and abs(diff) <= (len(got_rows) + 1) // 2) and allow_diff.get((label, name, c)) != diff:
                fails.append((got_rows, i, diff, (len(got_rows) + 1) // 2 if c.startswith("actual") else 0))

    # Prior-year check: this document's prior-year budget column should equal the previous
    # year's extract (true for every shared account so far). Mismatches point at OCR errors in
    # one of the two documents; they are listed as hints on failing totals, not failures.
    prior_hint = {}
    prev = os.path.join(DATA_E, "extracted", f"FY{int(fy)-1}_budget_gf_revenue.csv")
    if os.path.exists(prev):
        pc = f"budget_FY{int(fy)-1}"
        pv = {r["account"]: int(r["amount"]) for r in csv.DictReader(open(prev)) if r["column"] == pc}
        ci = cols.index(pc)
        for r in good:
            if r["account"] in pv and pv[r["account"]] != r["vals"][ci]:
                prior_hint[r["account"]] = f"{r['account']} {r['line_item']!r} {pc}: {r['vals'][ci]:,} here vs {pv[r['account']]:,} in FY{int(fy)-1} extract"
    for h in prior_hint.values():
        notes.append("prior-year mismatch: " + h)
    # Same hint for expenses against the previous year's expense extract (when it exists).
    prev_e = os.path.join(DATA_E, "extracted", f"FY{int(fy)-1}_budget_gf_expense.csv")
    if os.path.exists(prev_e) and exp_good:
        pc = f"budget_FY{int(fy)-1}"
        pv = {r["account"]: int(r["amount"]) for r in csv.DictReader(open(prev_e)) if r["column"] == pc}
        ci = cols.index(pc)
        for r in exp_good:
            if r["account"] in pv and pv[r["account"]] != r["vals"][ci]:
                exp_notes.append(f"prior-year mismatch (expense): {r['account']} {r['line_item']!r} {pc}: {r['vals'][ci]:,} here vs {pv[r['account']]:,} in FY{int(fy)-1} extract")

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
    autofix(events, rows, exp_rows, fund_total, cols, ncols, alts, compare_diffs, auto_notes)
    exp_notes.extend(auto_notes)
    exp_problems = [p_ for p_ in problems if p_.startswith("expense row:")]
    problems[:] = [p_ for p_ in problems if not p_.startswith("expense row:")]
    check_expenses(events, rows, exp_rows, fund_total, cols, lambda *a_: compare(*a_, sink=exp_problems, nsink=exp_notes))  # all rows: event positions index exp_rows
    for k in (set(acct_fix) | set(total_fix) | set(allow_diff) | set(row_fix) | set(not_in_total) | {(a_[0], a_[1]) for a_ in add_rows}) - used:
        # an override belongs to the revenue or the expense side; the sides pick their OCR prep independently
        side = exp_notes if (len(k) > 1 and re.search(r"(?i)-5-|expens|^net\b|\bnet$", str(k[1]))) else notes
        side.append(f"override never used (OCR text may have changed): {k}")

    return dict(prep=prep, totals=printed_log, exp_problems=exp_problems, exp_alt_rows=exp_rows, exp_notes=exp_notes, rows=rows, good=good, exp_rows=exp_rows, exp_good=exp_good, events=events, cols=cols, checks=checks, problems=problems, notes=notes,
                pdf_pages=(start + 1, end + 1))


HEADER = ["fiscal_year", "doc", "page_label", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"]


def total_kind(name):
    """What a printed total line is: fund_revenues, fund_expenses, fund_net, dept_net, subtotal, dept_revenues, dept_expenses."""
    if FUND_TOTAL.match(name):
        return "fund_revenues"
    if FUND_EXP_TOTAL.match(name):
        return "fund_expenses"
    if FUND_NET.match(name):
        return "fund_net"
    if NET_LINE.match(name):
        return "dept_net"
    if TOTAL_REV.match(name):
        return "dept_revenues"
    if SUBTOTAL.search(name):
        return "subtotal"
    return "dept_expenses"


REV_KINDS = {"fund_revenues", "dept_revenues"}


def write_totals(fy, rev, exp):
    """The city's printed totals as extracted (after any eyeballed correction): revenue ones from the revenue side's OCR prep,
    the rest from the expense side's."""
    out = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_gf_totals.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "fund", "kind", "name", "column", "amount"])
        for res, want_rev in ((rev, True), (exp, False)):
            for label, name, vals in res["totals"]:
                name = name.lstrip("| ")
                k = total_kind(name)
                if (k in REV_KINDS) != want_rev:
                    continue
                for c, v in zip(res["cols"], vals):
                    w.writerow([fy, f"FY{fy}_budget", label, "General", k, name, c, v])
    return out


def write_rows(fy, res, rows, name):
    os.makedirs(os.path.join(DATA_E, "extracted"), exist_ok=True)
    out = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_gf_{name}.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            for c, v in zip(res["cols"], r["vals"]):
                w.writerow([fy, f"FY{fy}_budget", r["page_label"], "General", r["dept_code"], r["account"], r["line_item"], c, v, r["flag"]])
    return out


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    force = "--force" in sys.argv
    results = []
    preps = PREPS if arg == "auto" else [arg]
    first = {prep: extract(fy, prep, force) for prep in preps}
    for prep in preps:
        res = extract(fy, prep, False, build_alts(fy, prep, first))
        results.append(res)
        print(f"prep {prep}: revenue {len(res['problems'])} problems, {len(res['good'])}/{len(res['rows'])} rows usable; "
              f"expenses {len(res['exp_problems'])} problems, {len(res['exp_good'])}/{len(res['exp_rows'])} rows usable")
    # The revenue and expense sides are chosen independently: a prep that reads one side badly may read the other well.
    rev = min(results, key=lambda r: len(r["problems"]))  # min() keeps the earliest on ties
    exp = min(results, key=lambda r: len(r["exp_problems"]))
    out = write_rows(fy, rev, rev["good"], "revenue")
    out_exp = write_rows(fy, exp, exp["exp_good"], "expense")
    write_totals(fy, rev, exp)
    print(f"\nchosen prep: revenue {rev['prep']}, expenses {exp['prep']}; General Fund pdf pages {rev['pdf_pages'][0]}-{rev['pdf_pages'][1]}")
    print("columns:", rev["cols"])
    print(f"{len(rev['rows'])} revenue rows, {len(rev['good'])} written -> {os.path.relpath(out, ROOT)}")
    print(f"{len(exp['exp_rows'])} expense rows, {len(exp['exp_good'])} written -> {os.path.relpath(out_exp, ROOT)}")
    print(f"{len(rev['checks'])} revenue totals + {sum(1 for e in exp['events'] if e['kind'] in ('net', 'exptotal', 'subtotal', 'fund_exp'))} "
          f"expense checks; {len(rev['problems'])} revenue and {len(exp['exp_problems'])} expense problems")
    def unused_everywhere(n):  # an override only the other prep could use (its OCR text differs) is not worth a note
        return not n.startswith("override never used") or all(n in r_["notes"] + r_["exp_notes"] for r_ in results)
    for p in rev["notes"] + exp["exp_notes"]:
        if unused_everywhere(p):
            print("  note:", p)
    for p in rev["problems"]:
        print("  !", p)
    for p in exp["exp_problems"]:
        print("  ! (expense)", p)
    sys.exit(1 if rev["problems"] or exp["exp_problems"] else 0)


if __name__ == "__main__":
    main()
