"""Extract the governmental funds statements and the budget-and-actual schedules of a Belvidere Park District audit (text-layer audits).

Usage: python3 pipelines/belvidere_park_district/extract_audit_fund_statements.py FY2025

Reads, with `pdftotext -layout`:
  * Balance Sheet - Governmental Funds (one page) and Statement of Revenues, Expenditures and Changes in Fund Balances - Governmental Funds (one page):
    eight amount columns, the funds listed in entities/belvidere_park_district/audit_fund_columns.csv, the last being Total Governmental Funds;
  * the Schedule of Revenues, Expenditures and Changes in Fund Balance - Budget and Actual of each fund (one page each): Original & Final Budget,
    Actual, Variance, and in older audits a prior-year Actual comparative.
Output (data/belvidere_park_district/extracted/audits/):
  <doc>_fund_statements.csv   statement (balance_sheet | changes), pdf_page, row_seq, section, line_item, fund, amount
  <doc>_budget_actual.csv     fund, pdf_page, row_seq, section, line_item, column (budget | actual | variance | prior_year_actual), amount
  <doc>_checks.csv            every identity tested, with expected, found and whether it holds
Validation (every check must hold or the script fails and writes nothing): balance sheet (assets, liabilities, deferred inflows, fund balances,
the balance equation, Total column = sum of the funds); statement of changes (revenues, expenditures, excess, other financing, net change, ending =
beginning + net change, Total column); schedules (revenue and expenditure sums, excess, net change, variance = actual - budget for revenue and the
reverse for expenditure, ending = beginning + net change); and the ties between them (a schedule's actual column = the statement's fund column).
"""
import csv
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402

ENTITY = R.ENTITY
NUM = re.compile(r"\(?\$?\s*\d[\d,]*\)?(?=\s|$)|(?<=\s)-(?=\s|$)|^-(?=\s|$)")


DASHES = ("-", "—", "–", "=", "_", ".", ":", "—-", "--")


def to_amount(tok):
    t = tok.replace("$", "").replace(",", "").strip()
    if t in DASHES or not re.search(r"\d", t):  # scan noise standing for an empty cell
        return 0
    neg = t.startswith("(")
    v = int(t.strip("()"))
    return -v if neg else v


NUMLIKE = re.compile(r"\(?\$?\d[\d,]*\)?\.?|[-—–=_.:~]{1,3}|\$")
OCR_NOISE = False  # set by extract() for scanned audits: debris and noise marks inside the numbers are then scan artefacts, not cells
DEBRIS = re.compile(r"[A-Za-z]{1,2}|\||,|[-—–=]*_+[-—–=_]*")  # short letter-only marks the scan leaves after the last number ("Mm", "a", "z")


def fix_token(t):
    if re.fullmatch(r"\$[_\W]*", t):
        return "$"
    """Scan confusions inside a number: 5 read as S or the section sign, 0 as O, 1 as l or I, a thousands comma as a period."""
    if re.search(r"\d", t):
        t = t.strip("_")  # underline artefacts glued to a number
        if re.fullmatch(r"\$?\d[\d,]*\)", t):
            t = "(" + t  # the opening parenthesis of a negative was lost
    m = re.fullmatch(r"[_|~'‘’]+(\(?\$?\d[\d,]*\)?)[,.]?|(\(?\$?\d[\d,]*\)?)[,.]", t)  # stray marks glued to a number: "_1,145,700," -> "1,145,700"
    if m:
        t = m.group(1) or m.group(2)
    if len(t) >= 3 and re.fullmatch(r"[\d,.()$§SsOolI|]+", t) and re.search(r"\d.*[,.]|[,.].*\d", t):
        t = t.replace("§", "5").replace("S", "5").replace("s", "5").replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1").replace("|", "1")
        t = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1,\2", t)
    return t


def split_row(line):
    """(label, [amounts]) of a statement line, or (label, []) for a heading. Amounts are the trailing run of number-like tokens (a dash or noise mark is an
    empty cell, a lone "$" is skipped); short letter-only debris after the last number is dropped first."""
    s = line.rstrip()
    if not s.strip():
        return None
    toks = [fix_token(m.group(0)) for m in re.finditer(r"\S+", s)]
    end = len(toks)
    while OCR_NOISE and end > 1 and DEBRIS.fullmatch(toks[end - 1]) and any(re.search(r"\d", t) for t in toks[:end - 1]):
        end -= 1
    i = end
    while i > 0 and (NUMLIKE.fullmatch(toks[i - 1]) or (OCR_NOISE and DEBRIS.fullmatch(toks[i - 1]) and i < end and i > 1 and re.search(r"\d", " ".join(toks[:i - 1])) and
                                                         NUMLIKE.fullmatch(toks[i - 2]))):
        i -= 1
    cells = []
    for j in range(i, end):
        if toks[j] == "$" or (OCR_NOISE and (re.fullmatch(r"_+", toks[j]) or DEBRIS.fullmatch(toks[j]) or (j > i and toks[j - 1] == "$" and not re.search(r"\d", toks[j]) and toks[j] != "-"))):  # a "$" and the noise mark after it are not cells
            continue
        cells.append(toks[j])
    if not any(re.search(r"\d", t) for t in cells) and not (cells and len(cells) >= 3):
        return s.strip(), []  # a heading, or a label ending in punctuation
    label = " ".join(toks[:i]).rstrip("$").strip()
    out = []
    for t in cells:
        try:
            out.append(to_amount(t.rstrip(".") if re.search(r"\d", t) else t))
        except ValueError:
            out.append(to_amount(re.sub(r"[^\d()-]", "", t) or "-"))
    return label, out


def page_lines(text):
    for raw in text.splitlines():
        if re.fullmatch(r"\s*-\s*\d+\s*-\s*", raw) or "notes to the financial statements are an integral part" in raw.lower():
            continue
        yield raw


TOP = re.compile(r"^(Assets|Liabilities|Deferred Inflows of Resources|Fund balances|Revenues|Expenditures|Other financing sources \(uses\))\s*:?$", re.I)
CLEARS = re.compile(r"^(Excess of revenues|Net change)", re.I)


def read_rows(text, start_re, ncols, stop_re=None):
    """Rows (section, label, amounts) below the first line matching start_re. A heading line followed by a row whose label starts lowercase is a wrapped label."""
    rows, stack = [], []
    started = False
    pending = None
    for raw in page_lines(text):
        if not started:
            if re.search(start_re, raw):
                started = True
            else:
                continue
        r = split_row(raw)
        if r is None:
            continue
        label, amts = r
        if OCR_NOISE:
            label = re.sub(r"^[^A-Za-z(]+", "", label)  # leading scan marks ("' Principal")
        indent = len(raw) - len(raw.lstrip())
        def push(h):  # a top-level heading (Assets:, Revenues:, ...) starts a new section; any other heading nests inside it
            h = h.rstrip(":").strip()
            if TOP.match(h):
                stack[:] = [h]
            else:
                stack.append(h)

        if pending is not None:
            plabel = pending
            pending = None
            if label and (label[0].islower() or label[0] == "(") and amts:
                label = f"{plabel} {label}"
            else:
                push(plabel)
        if not amts:
            if pending is not None:
                push(pending)
            pending = label
            continue
        label = re.sub(r"(?i)^net change\s*in\s*fund", "Net change in fund", label)  # "Net change infund balance"
        if CLEARS.match(label):
            stack[:] = []
        rows.append((": ".join(stack) if False else "; ".join(stack), label.rstrip(":"), amts, indent))
    return rows


def find_pages(pages, title_re, exclude_re=None):
    out = []
    for i, p in enumerate(pages, start=1):
        head = "\n".join(p.splitlines()[:12])
        if re.search(title_re, head) and not (exclude_re and re.search(exclude_re, head)):
            out.append(i)
    return out


def load_columns(ent):
    return [(r["column_name"], r["fund_key"]) for r in csv.DictReader(open(ent.config_path("audit_fund_columns.csv")))]


CANDIDATE_PAGES = list(range(16, 22)) + list(range(44, 56))  # where every audit of this district has its statements and schedules


def get_pages(pdf, doc, prep="gray", dpi=200):
    """Page texts (index = page - 1). Text-layer audits: pdftotext -layout. Scans: OCR of the candidate pages only; a landscape statement scanned sideways
    (its title is unreadable) is OCRed again rotated 90 degrees."""
    pages = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout.split("\f")
    if sum(len(p) for p in pages) > 800 * len(pages):
        return pages
    n = len(pages)
    ocr = R.ocr_selected(pdf, doc, [i for i in CANDIDATE_PAGES if i <= n], prep=prep, dpi=dpi)
    for i, t in list(ocr.items()):
        if "Fund" not in t[:400] and "FISCAL" not in t[:400].upper() and not re.search(r"ILLINOIS|Schedule|Statement|Balance", t[:600]):
            for rot in (90, 270):  # the direction the page was scanned differs from audit to audit
                t = R.ocr_selected(pdf, doc, [i], prep=prep, dpi=dpi, rotate=rot)[i]
                if re.search(r"ILLINOIS|Statement of|Fund", t[:400]):
                    ocr[i] = t
                    break
    return [ocr.get(i, "") for i in range(1, n + 1)]


ALT_READINGS = [("thr150", 200), ("gray", 300), ("thr150", 300), ("gray", 150)]


def keyed(rows):
    """rows -> {(label letters, occurrence number): (section, label, amts)}: the same label can repeat on a page (Interest, Other), the order tells them apart."""
    seen, out = {}, {}
    for section, label, amts, indent in rows:
        k = re.sub(r"[^a-z]", "", label.lower())
        seen[k] = seen.get(k, 0) + 1
        out[(k, seen[k])] = (section, label, amts)
    return out


def valid_statement(amts, n):
    return len(amts) == n and sum(amts[:n - 1]) == amts[n - 1]


def valid_schedule(amts, names):
    if len(amts) != len(names):
        return False
    v = dict(zip(names, amts))
    bud = v.get("budget", v.get("budget_final"))
    if "variance" in v and bud is not None:
        return abs(v["variance"]) == abs(v["actual"] - bud)  # the audit's own variance sign errors are allowed elsewhere; here only the size is tested
    return True


class Rows:
    """Row reader with the repair ladder: a row that fails its own identity is looked up in other OCR readings of the same page (unique valid reading wins,
    flagged auto_resolved), then in the overrides (`row` kind: `<statement or fund>|<label>` -> `a|b|c...`, read from the page image)."""

    def __init__(self, pdf, doc, primary, overrides, corrections):
        self.pdf, self.doc, self.primary, self.alt_pages = pdf, doc, primary, None
        self.overrides = {o["raw"]: o for o in overrides if o["kind"] == "row"}
        self.adds = {o["raw"]: o for o in overrides if o["kind"] == "add_row"}  # a row no OCR reading kept, added after the row named in `raw`
        self.used = set()
        self.corrections = corrections
        self.alt_rows = {}
        self.ocr = len(primary) and sum(len(p) for p in primary) <= 800 * len(primary)

    def alts(self, pg, start, n):
        if not self.ocr:
            return []
        if self.alt_pages is None:
            self.alt_pages = [get_pages(self.pdf, self.doc, prep=p, dpi=d) for p, d in ALT_READINGS]
        return [read_rows(ap[pg - 1], start, n) for ap in self.alt_pages if ap[pg - 1]]

    def get(self, pg, start, n, scope, names_fn, valid_fn, problems):
        """Rows of page pg as (section, label, amts); names_fn(label) gives the column names, valid_fn(amts, names) the row identity."""
        out, alts = [], None
        seen = {}
        for section, label, amts, indent in read_rows(self.primary[pg - 1], start, n):
            names = names_fn(label)
            lk = re.sub(r"[^a-z]", "", label.lower())
            seen[lk] = seen.get(lk, 0) + 1
            occ = (lk, seen[lk])
            if not valid_fn(amts, names):
                if alts is None:
                    alts = self.alts(pg, start, n)
                cands = {}
                for rows in alts:
                    hit = keyed(rows).get(occ)
                    if hit and valid_fn(hit[2], names_fn(hit[1])):
                        cands[tuple(hit[2])] = (hit[0], hit[1])
                if len(cands) == 1:
                    (fixed,) = cands
                    self.corrections.append((self.doc, pg, "auto_resolved", f"{scope}|{label}", "|".join(map(str, amts)), "|".join(map(str, fixed)),
                                             "the only OCR reading of this row that satisfies the row's own identity"))
                    amts = list(fixed)
                    if not re.fullmatch(r"[A-Za-z][A-Za-z &/.,'()-]*", label):  # the garbled reading's label is scan debris: take the clean one
                        label = cands[fixed][1]
                else:
                    key = f"{scope}|{label}"
                    o = self.overrides.get(key)
                    if o:
                        self.used.add(key)
                        self.corrections.append((self.doc, pg, "row_override", key, "|".join(map(str, amts)), o["fixed"], o["reason"]))
                        amts = [int(x) for x in o["fixed"].split("|")]
                    elif self.ocr and not valid_fn(amts, names):  # a text layer is exact: a row off its identity is the audit's own arithmetic, the checks report it
                        problems.append(f"{self.doc} p{pg} {scope}: {label!r}: {len(amts)} amounts {amts} fail the row identity ({len(cands)} alternative readings)")
            out.append((section, label, amts))
            add = self.adds.get(f"{scope}|{label}")
            if add:
                self.used.add(add["raw"])
                new_label, *cells = add["fixed"].split("|")
                self.corrections.append((self.doc, pg, "add_row", f"{scope}|{new_label}", "", "|".join(cells), add["reason"]))
                out.append((section, new_label, [int(c) for c in cells]))
        return out


def extract(pdf, fy, doc, ent, overrides=(), corrections=None, problems=None):
    corrections = corrections if corrections is not None else []
    problems = problems if problems is not None else []
    pages = get_pages(pdf, doc)
    rows_reader = Rows(pdf, doc, pages, overrides, corrections)
    global OCR_NOISE
    OCR_NOISE = bool(rows_reader.ocr)
    cols = load_columns(ent)
    n = len(cols)
    stm, ba = [], []
    for statement, title, start, exclude in (
            ("balance_sheet", r"Balance Sheet\s*\n.*Governmental Funds", r"^\s*Assets:", r"Reconciliation"),
            ("changes", r"Statement of Revenues, Expenditures and Changes in Fund Balances\s*\n.*Governmental Funds", r"^\s*Revenues:", r"Reconciliation")):
        hits = [i for i in find_pages(pages, r"(?s)" + title, exclude) if re.search(r"For the Year Ended|December 31", pages[i - 1][:600])]
        if len(hits) != 1:
            raise SystemExit(f"{doc}: {statement}: found pages {hits}")
        pg = hits[0]
        header = "\n".join(pages[pg - 1].splitlines()[:12])
        for word in ("General", "Recreation", "Bond", "Alternate", "Capital", "Museum", "Total"):
            if word not in header:
                raise SystemExit(f"{doc}: {statement}: column header lacks {word}")
        got = rows_reader.get(pg, start, n, statement, lambda label: None, lambda amts, names: valid_statement(amts, n), problems)
        for seq, (section, label, amts) in enumerate(got, start=1):
            if len(amts) != n:
                continue
            for (cname, fkey), a in zip(cols, amts):
                stm.append(dict(fiscal_year=fy, doc=doc, statement=statement, pdf_page=pg, row_seq=seq, section=section, line_item=label, fund=cname, amount=a))
    # schedules
    sched_pages = find_pages(pages, r"(?s)Budget and Actual", r"NOTES|Reconciliation")
    sched_pages = [i for i in sched_pages if re.search(r"For the Year Ended", pages[i - 1][:800])]
    for pg in sched_pages:
        head = [l.strip() for l in pages[pg - 1].splitlines()[:12] if l.strip()]
        fund = next((l for l in head if re.fullmatch(r"[A-Za-z&' ]+ Fund", l)), None)
        if not fund:
            raise SystemExit(f"{doc}: schedule p{pg}: no fund name in {head}")
        comparative = bool(re.search(r"With Comparative Totals", pages[pg - 1][:900]))
        split = bool(re.search(r"Original\s+Final", "\n".join(pages[pg - 1].splitlines()[:14])))  # separate Original and Final Budget columns
        budget_cols = ["budget_original", "budget_final"] if split else ["budget"]
        ncols = len(budget_cols) + 2 + (1 if comparative else 0)

        def names_fn(label, budget_cols=budget_cols, comparative=comparative):
            if re.match(r"(Beginning|Ending)", label):
                return ["actual", "prior_year_actual"] if comparative else ["actual"]
            return budget_cols + ["actual", "variance"] + (["prior_year_actual"] if comparative else [])
        got = rows_reader.get(pg, r"^\s*Revenues:", ncols, fund, names_fn, lambda amts, names: valid_schedule(amts, names), problems)
        for seq, (section, label, amts) in enumerate(got, start=1):
            names = names_fn(label)
            if len(amts) != len(names):
                continue
            for c, a in zip(names, amts):
                ba.append(dict(fiscal_year=fy, doc=doc, fund=fund, pdf_page=pg, row_seq=seq, section=section, line_item=label, column=c, amount=a))
    for key in list(rows_reader.overrides) + list(rows_reader.adds):
        if key not in rows_reader.used:
            problems.append(f"override not used: {key}")
    return stm, ba


def check(stm, ba, cols, doc):
    """Identity checks; returns (rows of the checks file, number of failures)."""
    out = []

    def add(name, expected, found):
        out.append((doc, name, expected, found, "ok" if expected == found else "FAIL"))

    def by(statement):
        d = {}
        for r in stm:
            if r["statement"] == statement:
                d.setdefault(r["row_seq"], {"label": r["line_item"], "section": r["section"], "vals": {}})["vals"][r["fund"]] = r["amount"]
        return [d[k] for k in sorted(d)]

    funds = [c for c, _ in cols]
    total_col = funds[-1]
    for statement in ("balance_sheet", "changes"):
        rows = by(statement)
        for r in rows:  # the Total column is the sum of the funds, on every line
            add(f"{statement}: {r['label']}: total column", sum(r["vals"][f] for f in funds[:-1]), r["vals"][total_col])
        for f in funds:
            v = {r["label"]: r["vals"][f] for r in rows}

            def first(rx):
                v = next((r["vals"][f] for r in rows if re.search(rx, r["label"])), None)
                if v is None:
                    out.append((doc, f"{statement}: {f}: missing row {rx}", "present", "missing", "FAIL"))
                    return 0
                return v

            def sum_between(sec_rx, stop_rx, exclude=r"^Total"):
                s, on = 0, False
                for r in rows:
                    if re.search(stop_rx, r["label"]) and on:
                        break
                    if on and not re.search(exclude, r["label"]):
                        s += r["vals"][f]
                    if re.search(sec_rx, r["section"] or r["label"]) and not on and not re.search(exclude, r["label"]):
                        pass
                return s
            if statement == "balance_sheet":
                assets = sum(r["vals"][f] for r in rows if r["section"].startswith("Assets") and not r["label"].startswith("Total"))
                add(f"balance_sheet: {f}: assets", first(r"^Total assets"), assets)
                liab = sum(r["vals"][f] for r in rows if r["section"].startswith("Liabilities") and not r["label"].startswith("Total"))
                add(f"balance_sheet: {f}: liabilities", first(r"^Total liabilities$"), liab)
                dfl = sum(r["vals"][f] for r in rows if r["section"].startswith("Deferred Inflows") and not r["label"].startswith("Total"))
                add(f"balance_sheet: {f}: liabilities + deferred inflows", first(r"^Total liabilities and deferred"), first(r"^Total liabilities$") + dfl)
                fb = sum(r["vals"][f] for r in rows if r["section"].startswith("Fund balances") and not r["label"].startswith("Total"))
                add(f"balance_sheet: {f}: fund balances", first(r"^Total fund balances"), fb)
                add(f"balance_sheet: {f}: balance equation", first(r"^Total assets"), first(r"^Total liabilities, deferred") )
                add(f"balance_sheet: {f}: l + d + fb", first(r"^Total liabilities, deferred"), first(r"^Total liabilities and deferred") + first(r"^Total fund balances"))
            else:
                rev = sum(r["vals"][f] for r in rows if r["section"].startswith("Revenues") and not r["label"].startswith("Total"))
                add(f"changes: {f}: revenues", first(r"^Total revenues"), rev)
                exp = sum(r["vals"][f] for r in rows if r["section"].startswith("Expenditures") and not r["label"].startswith("Total"))
                add(f"changes: {f}: expenditures", first(r"^Total expenditures"), exp)
                add(f"changes: {f}: excess", first(r"^Excess of revenues"), first(r"^Total revenues") - first(r"^Total expenditures"))
                oth = sum(r["vals"][f] for r in rows if r["section"].startswith("Other financing") and not r["label"].startswith("Total"))
                tof = first(r"^Total other financing")
                add(f"changes: {f}: other financing", tof if tof is not None else 0, oth)
                add(f"changes: {f}: net change", first(r"^Net change in fund balances"), first(r"^Excess of revenues") + (tof or 0))
                beg = sum(r["vals"][f] for r in rows if r["section"].startswith("Fund balances") and re.match(r"Beginning", r["label"]))
                add(f"changes: {f}: ending = beginning + net change", first(r"^Ending"), beg + first(r"^Net change in fund balances"))
    # schedules
    sch = {}
    for r in ba:
        sch.setdefault(r["fund"], {}).setdefault(r["row_seq"], {"label": r["line_item"], "section": r["section"], "vals": {}})["vals"][r["column"]] = r["amount"]
    for fund, d in sch.items():
        rows = [d[k] for k in sorted(d)]
        bcols = [c for c in ("budget", "budget_original", "budget_final") if any(c in r["vals"] for r in rows)]
        for c in bcols + ["actual"]:
            def val(rx):
                return next((r["vals"].get(c) for r in rows if re.search(rx, r["label"])), None)
            rev = sum(r["vals"].get(c, 0) for r in rows if r["section"].startswith("Revenues") and not r["label"].startswith("Total"))
            add(f"schedule {fund}: {c}: revenues", val(r"^Total revenues"), rev)
            exp = sum(r["vals"].get(c, 0) for r in rows if r["section"].startswith("Expenditures") and not r["label"].startswith("Total"))
            add(f"schedule {fund}: {c}: expenditures", val(r"^Total expenditures"), exp)
            oth = sum(r["vals"].get(c, 0) for r in rows if r["section"].startswith("Other financing") and not r["label"].startswith("Total"))
            tof = val(r"^Total other financing")
            if tof is not None:
                add(f"schedule {fund}: {c}: other financing", tof, oth)
            net = val(r"^Net change in fund balance")
            add(f"schedule {fund}: {c}: net change", net, (val(r"^Total revenues") or 0) - (val(r"^Total expenditures") or 0) + (tof if tof is not None else oth))
        for r in rows:
            v = r["vals"]
            bud = v.get("budget", v.get("budget_final"))  # the variance is against the final budget
            if "variance" in v and bud is not None:
                exp_line = r["section"].startswith("Expenditures") or re.match(r"Total expenditures", r["label"])
                add(f"schedule {fund}: {r['label']}: variance", (bud - v["actual"]) if exp_line else (v["actual"] - bud), v["variance"])
        beg = next((r["vals"]["actual"] for r in rows if r["label"].startswith("Beginning")), None)
        end = next((r["vals"]["actual"] for r in rows if r["label"].startswith("Ending")), None)
        net = next((r["vals"]["actual"] for r in rows if r["label"].startswith("Net change")), None)
        add(f"schedule {fund}: ending = beginning + net change", end, beg + net)
        # tie to the statement of changes
        fcol = next((c for c in funds if c.lower().replace("&", "and").replace("fund", "").strip() == fund.lower().replace("fund", "").strip()), None)
        if fcol:
            stmt = {r["line_item"]: r["amount"] for r in stm if r["statement"] == "changes" and r["fund"] == fcol}
            add(f"tie {fund}: total revenues", next(r["vals"]["actual"] for r in rows if r["label"].startswith("Total revenues")), stmt.get("Total revenues"))
            add(f"tie {fund}: total expenditures", next(r["vals"]["actual"] for r in rows if r["label"].startswith("Total expenditures")), stmt.get("Total expenditures"))
            add(f"tie {fund}: ending fund balance", end, stmt.get("Ending"))
    return out, sum(1 for o in out if o[4] != "ok")


def A_load(ent, doc):
    path = ent.data_path("overrides", f"{doc}.csv")
    return list(csv.DictReader(open(path, newline=""))) if os.path.exists(path) else []


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_audit"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    ov = A_load(ent, doc)
    corrections, problems = [], []
    stm, ba = extract(ent.source_path(src[doc]["pdf_path"]), fy, doc, ent, ov, corrections, problems)
    for pr in problems:
        print("  PROBLEM", pr)
    if problems:
        sys.exit(1)
    cols = load_columns(ent)
    checks, fails = check(stm, ba, cols, doc)
    allow = {r["raw"]: r["reason"] for r in A_load(ent, doc) if r["kind"] == "allow_diff"}  # the audit's own arithmetic, recorded with a reason
    used = set()
    for i, c in enumerate(checks):
        if c[4] == "FAIL" and c[1] in allow:
            checks[i] = c[:4] + ("allowed: " + allow[c[1]],)
            used.add(c[1])
    fails = sum(1 for c in checks if c[4] == "FAIL") + sum(1 for k in allow if k not in used)
    for k in allow:
        if k not in used:
            print("  override not used:", k)
    print(f"{doc}: {len(stm)} statement cells, {len(ba)} schedule cells, {len(checks)} checks, {fails} failures, "
          f"{sum(1 for c in checks if c[4].startswith('allowed'))} allowed")
    for c in checks:
        if c[4] == "FAIL":
            print("  FAIL", c[1], "expected", c[2], "found", c[3])
    if fails:
        sys.exit(1)
    out = ent.data_path("extracted", "audits")
    os.makedirs(out, exist_ok=True)
    for name, rows in (("fund_statements", stm), ("budget_actual", ba)):
        with open(os.path.join(out, f"{doc}_{name}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    with open(os.path.join(out, f"{doc}_corrections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "pdf_page", "method", "row", "raw_cells", "final_cells", "detail"])
        w.writerows(corrections)
    with open(os.path.join(out, f"{doc}_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "check", "expected", "found", "result"])
        w.writerows(checks)


if __name__ == "__main__":
    main(sys.argv)
