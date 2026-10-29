"""Extract the budgeted funds other than the General Fund and Water/Sewer (Motor Fuel Tax #10, the TIF funds #12-#15, Farmington Fields
SSAs #16/#17, Library Operations #18, Capital Projects #41 (Public Benefit + Capital Assets accounts), Escrow #91, Police and Fire
Pension) from a Belvidere budget ordinance PDF.

Usage: python3 pipelines/belvidere_il/extract_budget_funds.py FY2019 [--prep auto|gray|thr150] [--dry]

These pages follow the Water/Sewer fund in Exhibit A (FY2015-FY2027; FY2024's PDF is missing the Police/Fire Pension page and has its
Motor Fuel Tax page out of order at the end). Every fund block has the same shape:
    <heading>   Cash Balance - beginning / Revenues (lines) / Total Revenues / Expenditures (lines) / [Total Expenditures] / Cash Balance - ending
The Capital Projects fund holds two ledgers (Public Benefit; Capital Assets, whose totals are "Capital Asset Revenues/Expenditures" or
absent); the pension funds and a few other lines have no account number (pseudo accounts FF-R-000-CODE, flag `no_account_number`).
Output (same long format as the General Fund / Water-Sewer extracts, one fund-ledger name in `fund`, account keys carry the fund code):
  data/belvidere_il/extracted/FY20XX_budget_funds_revenue.csv, _expense.csv, _totals.csv
Validation per block and column (all must tie, `allow_diff` overrides record the city's own arithmetic):
  revenue lines = Total Revenues; expense lines = Total Expenditures; beginning cash + revenues - expenditures = ending cash,
  and, for the Capital Projects fund, the printed fund roll-up (Beginning Cash / Revenue / Expenditures) = sum of the two ledgers.
Eyeballed corrections: data/belvidere_il/overrides/FY20XX_budget_funds.csv (see load_overrides).
"""
import csv
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget as B  # noqa: E402

DATA_E, SRC_E, ROOT = B.DATA_E, B.SRC_E, B.ROOT

ACCT = re.compile(r"(?<![\d-])(\d{2})\s?-\s?([45§])\s?-\s?(\d(?:\s?\d){2})\s?-\s?(\d{2}(?:\d{2}|[Xx][Xx]))(?!\d)")
ACCT_SHORT = re.compile(r"(?<![\d-])(\d{2})-(\d{4})(?!\d)")  # balance-sheet accounts printed in the Escrow fund: 91-2060
ACCT_NOHYPHEN = re.compile(r"(?<![\d-])(\d{2})-([45])-(\d{3})(\d{4})(?!\d)")
FUND_HEAD = re.compile(r"(?i)^(.*?)\bfund\s*#\s*(\d\s?\d)\b")
FUND_NAMES = {"10": "Motor Fuel Tax", "12": "Downtown TIF", "13": "Kishwaukee TIF", "14": "Belv Mall TIF Bond/Interest",
              "15": "Kishwaukee II TIF", "16": "Special Service Area 2 (Farmington Fields)", "17": "Special Service Area 3 (Farmington Fields)",
              "18": "Library Operations", "41": "Capital Projects", "91": "Escrow", "PP": "Police Pension", "FP": "Fire Pension"}
SUBLEDGERS = {"public benefit": "Public Benefit Account", "capital assets": "Capital Assets Account"}
PREPS_F = ["nl", "gray", "thr150", "nlthr150", "thr120", "thr180"]  # nl* = table rules erased before OCR (ruled lines made psm 6 drop rows)
WS_PAGE = re.compile(r"61-[45]-")
KNOWN_FUND_CODES = set(FUND_NAMES)

# unnumbered (or number-garbled) lines: label -> (flow, dept, object code). Matched on the normalized label's start, then fuzzily; pseudo codes
# are letters (no digits), real account parts are kept where the city printed them (Library 18-4-510-44XX ...).
VOCAB = [
    ("property tax", ("revenue", "000", "PTAX")), ("replacement tax", ("revenue", "000", "RTAX")),
    ("interest inc/mkt fluctuation", ("revenue", "000", "INTINC")), ("interest inc/market fluctuation", ("revenue", "000", "INTINC")),
    ("contributions", ("revenue", "000", "CONTRIB")), ("benefit payments", ("expense", "000", "BENEFIT")),
    ("contribution refunds", ("expense", "000", "REFUND")), ("refund of contributions", ("expense", "000", "REFUND")),
    ("administration", ("expense", "000", "ADMIN")), ("other expenditures", ("expense", "000", "OTHEXP")),
    ("disbursement of collected fees", ("expense", "000", "DISBURS")), ("other revenues", ("revenue", "000", "OTHREV")),
    ("other", ("revenue", "000", "OTHER")), ("il jobs now", ("revenue", "000", "ILJOBS")), ("audit adjustments", ("expense", "000", "AUDADJ")),
    ("escrow/land cash fees", ("revenue", "110", "4971")),
    ("fines, fees, and gifts", ("revenue", "510", "44XX")), ("salaries", ("expense", "510", "50XX")),
    ("book periodicals", ("expense", "510", "73XX")), ("computer equip", ("expense", "510", "62XX")),
]
HEADING_WORDS = {"revenues", "revenue", "expenditures", "expenditure", "expenses", "total revenues", "total expenditures"}


def vocab_match(label):
    """(flow, dept, obj) for an unnumbered label, None for a section heading / unknown label."""
    import difflib
    t = re.sub(r"[^a-z/, ]", "", label.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or t in HEADING_WORDS or difflib.get_close_matches(t, HEADING_WORDS, 1, 0.85):
        return "heading"
    if t.startswith("til "):  # `tL Jobs Now`
        t = "il " + t[4:]
    for key, val in sorted(VOCAB, key=lambda kv: -len(kv[0])):
        if t.startswith(key):
            return val
    best = difflib.get_close_matches(t[:32], [k for k, _ in VOCAB], 1, 0.8)
    return dict(VOCAB)[best[0]] if best else None


HEADER_LINE = re.compile(r"(?i)^[^A-Za-z0-9]*(revised\b|\*?\s*note\b|actual\b|line item\b|fy\s?\d\d\b|\d+ of \d+\b|budget\s*(ordinance|$)|\*\s*note|account #|request$|reequest)")


def pdf_path(fy):
    for p in (os.path.join(SRC_E, "budgets", f"FY{fy}_budget.pdf"), os.path.join(SRC_E, f"FY{fy}_budget_753H.pdf")):
        if os.path.exists(p):
            return p
    raise SystemExit(f"no PDF for FY{fy}")


def strip_lines(im, thr=150, hmin=120, vmin=80):
    """Erase the table rules (long horizontal / vertical dark runs) from a 300 dpi page: tesseract drops rows that sit between rules."""
    import numpy as np
    from PIL import Image

    def runs(bw, minlen):
        out = np.zeros_like(bw)
        for y in range(bw.shape[0]):
            row = bw[y]
            if row.sum() < minlen:
                continue
            d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
            for s_, e_ in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
                if e_ - s_ >= minlen:
                    out[y, s_:e_] = True
        return out

    a = np.array(im.convert("L"))
    bw = a < thr
    m = runs(bw, hmin) | runs(bw.T, vmin).T
    g = m.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        g |= np.roll(m, (dy, dx), (0, 1))
    a2 = a.copy()
    a2[g] = 255
    return Image.fromarray(a2)


def ocr_variant(pdf, page, outdir, prep):
    """B.ocr_page for plain preps (gray, thrNNN); `nl` / `nlthrNNN` erase the table rules first (see strip_lines), then tesseract --psm 6."""
    if not prep.startswith("nl"):
        return B.ocr_page(pdf, page, outdir, prep)
    txt = os.path.join(outdir, f"p{page:03d}.txt")
    if os.path.exists(txt):
        return open(txt).read()
    base = prep[2:] or "gray"
    import subprocess
    from PIL import Image
    pgm = os.path.join(outdir, f"p{page:03d}.pgm")
    png = pgm.replace(".pgm", ".png")
    subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pgmraw", "-r300", f"-dFirstPage={page}", f"-dLastPage={page}", f"-sOutputFile={pgm}", pdf], check=True)
    im = strip_lines(Image.open(pgm))
    if base.startswith("thr"):
        t = int(base[3:])
        im = im.point(lambda v: 255 if v > t else 0)
    im.save(png)
    out = subprocess.run(["tesseract", png, "-", "--psm", "6"], capture_output=True, text=True).stdout
    os.remove(pgm)
    os.remove(png)
    open(txt, "w").write(out)
    return out


def all_pages(fy, prep):
    pdf = pdf_path(fy)
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    n = B.page_count(pdf)
    return [B.ocr_page(pdf, p, outdir, prep) for p in range(1, n + 1)]


FUND_PAGE_MARK = re.compile(r"(?im)(fund\s*#\s*(10|12|13|14|15|16|17|18|41|91)\b|^capital projects fund|^(police|fire) pension fund)")


def pages_of(fy, prep):
    """The fund pages (a non-GF/WS fund heading or the pension funds), in PDF order, with their footer label. Which pages they are is decided
    from the plain-gray OCR (cached for every page); the other preps only OCR those pages."""
    gray = all_pages(fy, "gray")
    nums = []
    for n, t in enumerate(gray, 1):
        if WS_PAGE.search(t) or not FUND_PAGE_MARK.search(t):
            continue
        if re.search(r"(?i)ida public library|salary schedule|affid|table of contents", t[:300]) or ("Cash" not in t and "cash" not in t):
            continue
        nums.append(n)
    if not nums:
        raise SystemExit(f"FY{fy}: fund pages not found")
    pdf = pdf_path(fy)
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    out = []
    for n in nums:
        t = ocr_variant(pdf, n, outdir, prep)
        m = B.LABEL.search(gray[n - 1][-200:]) or B.LABEL.search(t[-200:])
        out.append((f"{m.group(1)} of {m.group(2)}" if m else f"pdf{n}", n, t))
    return out


JUNKTOK = re.compile(r"^[|}{\]\[!_~.:;,'`\"\\/]+$")


SYMTOK = re.compile(r"^[^A-Za-z0-9(){}$]+$")


def fixnum_line(raw):
    raw = raw.strip()
    toks = raw.split()
    if sum(1 for t in toks if re.search(r"\d", t)) >= 2:  # a line of figures: drop tokens that are only rules/marks (| = — , ~ ...)
        raw = " ".join(t for t in toks if not SYMTOK.match(t) or t in ("-",))
    raw = re.sub(r"^[|‘’'`~_.:;,—–\-\[\](!]+\s*", "", raw)  # a rule fragment in front of the label
    raw = re.sub(r"\$(?=\d)", "5", raw)  # these pages never print a currency sign: `$22,532` is a misread 5
    line = re.sub(r"(\d) ,(\d{3})", r"\1,\2", raw)
    line = re.sub(r"(?<=\s)[_~.:;](?=\s)", " ", line)  # a lone mark from a ruled line between two values
    line = re.sub(r"(?<=[\d)])\s*\|+\s*(?=[\d({])", " ", line)  # a column rule between two values
    line = re.sub(r"(?<=\d)[\]|!]+(?=\s|$)", "", line)  # a rule glued to the end of a value: 1,780,914]
    line = re.sub(r"(?<=\s)[\[|!]+(?=[\d({])", "", line)  # ... or to the start of one
    line = re.sub(r"(?<=\d)\}(?=\s|$)", lambda m: "}" if re.search(r"[({]\d[\d,]*$", line[:m.start()]) else "", line)
    return line.strip()


def pseudo_code(label):
    for pat, code in PSEUDO:
        if re.search(pat, label.strip()):
            return code
    return None


def cash_kind(name):
    """'beg' / 'end' for a `Cash Balance - beginning/ending` label the OCR may have garbled (`Gash Balance`, `seginning`), else None."""
    import difflib
    toks = [t for t in re.split(r"[\s\-–—]+", name.strip()) if t]
    if len(toks) < 3:
        return None
    r = lambda a, b: difflib.SequenceMatcher(None, a.lower(), b).ratio()
    if r(toks[0], "cash") >= 0.5 and r(toks[1], "balance") >= 0.6:
        if r(toks[2], "beginning") >= 0.7:
            return "beg"
        if r(toks[2], "ending") >= 0.7:
            return "end"
    return None


def slug(label):
    s = re.sub(r"[^A-Za-z]", "", label).upper()
    return (s[:10] or "UNNAMED")


def split_values(text, ncols, merge=False):
    """(leftover text, values, guessed) for the trailing numeric cells of `text`. Short junk tokens stand for printed zeros only when the
    row is short of values and the token has no real word in it."""
    vals, name = B.numbers(text, merge=merge, fill_to=ncols or None)
    guessed = list(B.numbers.guessed)
    if ncols and any(re.search(r"[A-Za-z]{3}|^[A-Za-z]{2}$", g) for g in guessed if g != "trailing"):  # a label word, not a zero
        vals, name = B.numbers(text, merge=merge)
        guessed = []
    if ncols and len(vals) == ncols + 1 and 1 <= vals[0] <= 9:  # a stray single digit (a rule fragment) in front of the figures
        vals = vals[1:]
        guessed = guessed + ["dropped_leading_digit"]
    if ncols and len(vals) > ncols:  # a ruled-line mark read as a 0 (`|}`)
        cleaned = " ".join(t for t in text.split() if not JUNKTOK.match(t))
        v2, n2 = B.numbers(cleaned, merge=merge, fill_to=ncols)
        if len(v2) == ncols:
            return n2, v2, list(B.numbers.guessed)
    return name, vals, guessed


def split_best(text, ncols):
    """Try the plain reading, then one that re-joins `3,287 944` (a comma read as a space); keep the first with ncols values."""
    name, vals, g = split_values(text, ncols)
    if ncols and len(vals) != ncols:
        for t2 in (text, re.sub(r"(?<![\d,.])(\d{1,3},\d{3}) (\d{3})(?![\d,.])", r"\1,\2", text)):
            n2, v2, g2 = split_values(t2, ncols, merge=True)
            if len(v2) == ncols:
                return n2, v2, g2
    return name, vals, g


def parse(pages, ncols):
    """-> blocks. A block = dict(fund, ledger, name, page, items=[rows], events=[printed numbers]); rows have flow revenue/expense,
    events have kind beg / revtotal / exptotal / end / rollup_* / unknown."""
    blocks, cur = [], None
    section, after_revtotal = None, False

    def start(code, name, ledger="", label=None):
        nonlocal cur, section, after_revtotal
        cur = dict(fund=code, ledger=ledger, name=name, items=[], events=[], page=label)
        blocks.append(cur)
        section, after_revtotal = None, False

    for label, pdfn, text in pages:
        for raw in text.splitlines():
            line = fixnum_line(raw)
            if not line:
                continue
            if HEADER_LINE.match(line) and not re.search(r"\d{1,3},\d{3}", line):
                continue
            # --- headings -------------------------------------------------------------------------------------------------
            m = FUND_HEAD.match(line)
            if m and not re.search(r"\d{1,3},\d{3}", line):
                code = re.sub(r"\s", "", m.group(2))
                if re.search(r"(?i)capital\s*proj", m.group(1)):
                    code = "41"  # OCR reads the digits of this one as 44 / 4 1
                start(code, FUND_NAMES.get(code, m.group(1).strip()), "", label)
                continue
            if re.match(r"(?i)^capital projects fund", line):
                start("41", FUND_NAMES["41"], "", label)
                continue
            mp = re.match(r"(?i)^(police|fire)\s*pension\s*fund", line)
            if mp:
                code = "PP" if mp.group(1).lower() == "police" else "FP"
                start(code, FUND_NAMES[code], "", label)
                continue
            ml = re.match(r"(?i)^(public benefit|capital assets)\s+account", line)
            if ml and cur is not None and cur["fund"] == "41":
                led = SUBLEDGERS[ml.group(1).lower()]
                if not cur["ledger"] and not cur["items"] and not cur["events"]:
                    cur["ledger"] = led
                else:
                    start("41", FUND_NAMES["41"], led, label)
                continue
            if cur is None:
                continue
            if cur["page"] is None:
                cur["page"] = label
            # --- numbered lines ----------------------------------------------------------------------------------------------
            m = ACCT.search(line)
            nohy = None if m else ACCT_NOHYPHEN.search(line)
            short = None if (m or nohy) else ACCT_SHORT.search(line)
            if m or nohy or short:
                mm = m or nohy or short
                if short:
                    pref, flowch, dept, obj = short.group(1), "2", "000", short.group(2)
                elif nohy:
                    pref, flowch, dept, obj = nohy.group(1), nohy.group(2), nohy.group(3), nohy.group(4)
                else:
                    pref, flowch, dept, obj = m.group(1), m.group(2).replace("§", "5"), re.sub(r"\s", "", m.group(3)), re.sub(r"\s", "", m.group(4)).upper()
                if cur["fund"] == "?" and pref in FUND_NAMES:  # heading lost: the account numbers say which fund this is
                    cur["fund"], cur["name"] = pref, FUND_NAMES[pref]
                _, vals, guessed = split_best(line[mm.end():], ncols)
                flags = ["ocr_zero_guess"] if guessed else []
                if short:
                    flags.append("balance_sheet_account")
                cur["items"].append(dict(page_label=label, flow="revenue" if flowch in ("4", "2") else "expense", dept=dept, obj=obj,
                                         line_item=line[:mm.start()].strip(" .:|"), vals=vals, raw=raw, flag=" ".join(flags), printed_prefix=pref))
                continue
            name, vals, guessed = split_best(line, ncols)
            name = re.sub(r"^[^A-Za-z0-9(]+|[^A-Za-z0-9)]+$", "", name.strip())
            if not vals:
                if re.match(r"(?i)^revenues?$", name):
                    section = "revenue"
                elif re.match(r"(?i)^expenditures?$|^expenses$", name):
                    section = "expense"
                continue
            ev = None
            ck = cash_kind(name)
            if ck:
                ev = ck
            elif re.match(r"(?i)^beginning\s+cash\b", name):  # the fund roll-up under the Capital Projects ledgers
                ev = "rollup_beg"
            elif re.match(r"(?i)^ending\s+cash\b", name):
                ev = "rollup_end"
            elif re.match(r"(?i)^(to\w{0,3}l\s*revenues?|capital asset revenues?)(\s+\W*\w{0,3})?$", name):
                ev, after_revtotal = "revtotal", True
            elif re.match(r"(?i)^(to\w{0,3}l\s*expend\w*|capital asset expenditures?)(\s+\W*\w{0,3})?$", name):
                ev = "exptotal"
            elif re.match(r"(?i)^revenues?$", name) and any(e["kind"] in ("revtotal", "rollup_beg") for e in cur["events"]):
                ev = "rollup_rev"
            elif re.match(r"(?i)^expenditures?$", name) and any(e["kind"] in ("exptotal", "rollup_beg") for e in cur["events"]):
                ev = "rollup_exp"
            if ev == "beg" and any(e["kind"] == "beg" for e in cur["events"]):
                start("?", "Unknown (heading not read)", "", label)  # a second opening balance: the next fund's heading was lost by the OCR
            if ev:
                cur["events"].append(dict(kind=ev, name=name, vals=vals, label=label, raw=raw, flag="ocr_zero_guess" if guessed else ""))
                continue
            if not name:
                continue
            vm = vocab_match(name)
            if vm == "heading":
                continue  # a heading or total label whose figures the OCR read (a row of zeros), not a line item
            if vm is None:
                cur["events"].append(dict(kind="unknown", name=name, vals=vals, label=label, raw=raw, flag=""))
                continue
            vflow, vdept, vobj = vm
            flow = section or ("expense" if after_revtotal else vflow)
            if vflow == "expense" and section != "revenue":
                flow = "expense"
            if vflow == "revenue" and section is None and not after_revtotal:
                flow = "revenue"
            cur["items"].append(dict(page_label=label, flow=flow, dept=vdept, obj=vobj, line_item=name, vals=vals, raw=raw,
                                     flag=("no_account_number" if vdept == "000" else "account_from_label") + (" ocr_zero_guess" if guessed else ""),
                                     printed_prefix=cur["fund"]))
    blocks = [b for b in blocks if b["items"] or b["events"]]
    for i, b in enumerate(blocks):
        if b["fund"] == "?" and i and blocks[i - 1]["fund"] == "PP":
            b["fund"], b["name"] = "FP", FUND_NAMES["FP"]
    cp = [b for b in blocks if b["fund"] == "41"]
    if len(cp) == 2 and not cp[0]["ledger"] and cp[1]["ledger"] == "Capital Assets Account":
        cp[0]["ledger"] = "Public Benefit Account"  # the first ledger's sub-heading was lost by the OCR
    for b in blocks:
        b["key"] = b["fund"] + ("|" + b["ledger"] if b["ledger"] else "")
        seen = Counter()
        for it in b["items"]:
            it["account"] = f"{b['fund']}-{'4' if it['flow'] == 'revenue' else '5'}-{it['dept']}-{it['obj']}"
            seen[it["account"]] += 1
            it["nth"] = seen[it["account"]]
            if it["nth"] > 1:
                it["flag"] = (it["flag"] + " duplicate_account").strip()
            if it["printed_prefix"] != b["fund"] and it["dept"] != "000" and it["printed_prefix"] in FUND_NAMES:
                it["flag"] = (it["flag"] + f" fund_prefix_{it['printed_prefix']}_printed").strip()  # e.g. SSA 3 lines printed 16-... under fund #17
            it["_key"] = (b["key"], it["account"], it["nth"])
        kinds = Counter()
        for e in b["events"]:
            kinds[e["kind"]] += 1
            e["nth"] = kinds[e["kind"]]
            e["_key"] = (b["key"], e["kind"], e["nth"])
    return blocks


# =====================================================================================================================================
# overrides
# =====================================================================================================================================
def load_overrides(fy):
    """data/belvidere_il/overrides/FY20XX_budget_funds.csv: kind, page_label, key, column, value, reason (reason is required).
    row         key = account[#n] (n-th use of the account in its block, default 1) with a block prefix `FUND[|Ledger]@`; value = `v1|v2|..` (all columns)
    cell        key = `FUND[|Ledger]@account[#n]` or `FUND[|Ledger]@kind` (beg/end/revtotal/exptotal/rollup_*); column = column label; value = int
    allow_diff  key = `FUND[|Ledger]@check`; column; value = the difference (printed - computed) the city's own arithmetic leaves
    account     key = `FUND[|Ledger]@account`; value = the corrected account string (OCR garbled the number)
    drop        key = `FUND[|Ledger]@account[#n]`: a line that is not a real row (OCR noise)
    not_in_total key = `FUND[|Ledger]@account[#n]`: a printed line the city leaves out of its own totals (kept in the extract, flagged, excluded from the sums)
    add_row     key = `FUND[|Ledger]@account`; column = the line's label; value = `v1|v2|..`: a printed line no OCR reading found (or, if read, its values)
    (`row` with key `FUND[|Ledger]@end` (or beg/revtotal/exptotal/rollup_*) also creates a printed total no OCR reading found)
    """
    ov = dict(row={}, cell={}, allow={}, account={}, drop=set(), add={}, notin=set(), reasons={})
    path = os.path.join(DATA_E, "overrides", f"FY{fy}_budget_funds.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            assert o.get("reason"), f"override without a reason: {o}"
            k = o["key"]
            if o["kind"] == "row":
                ov["row"][k] = [int(v) for v in o["value"].split("|")]
            elif o["kind"] == "cell":
                ov["cell"][(k, o["column"])] = int(o["value"])
            elif o["kind"] == "allow_diff":
                ov["allow"][(k, o["column"])] = int(o["value"])
            elif o["kind"] == "account":
                ov["account"][k] = o["value"]
            elif o["kind"] == "drop":
                ov["drop"].add(k)
            elif o["kind"] == "not_in_total":
                ov["notin"].add(k)
            elif o["kind"] == "add_row":
                ov["add"][k] = (o["column"], [int(v) for v in o["value"].split("|")])
            ov["reasons"][(o["kind"], k, o.get("column", ""))] = o["reason"]
    return ov


def item_key(b, it):
    return f"{b['key']}@{it['account']}" + (f"#{it['nth']}" if it["nth"] > 1 else "")


def event_key(b, e):
    return f"{b['key']}@{e['kind']}" + (f"#{e['nth']}" if e["nth"] > 1 else "")


def corr(h, column, raw, final, method, detail=""):
    """Remember what the OCR read before an override or an automatic cross-reading replaced it (written to FY20XX_budget_funds_corrections.csv)."""
    h.setdefault("corrections", []).append(dict(column=column, raw_value=raw, final_value=final, method=method, detail=detail))


def corr_vals(h, cols, raw, new, method, detail=""):
    """One correction per cell that changed, or one for the whole run when the OCR gave a different number of values."""
    if len(raw) == len(new):
        for i, (a, b) in enumerate(zip(raw, new)):
            if a != b:
                corr(h, cols[i], a, b, method, detail)
    else:
        corr(h, "(all)", "|".join(map(str, raw)), "|".join(map(str, new)), method, detail)


def why(ov, kind, k, col=""):
    return (ov["reasons"].get((kind, k, col)) or ov["reasons"].get((kind, k, ""))
            or next((v for (kd, kk, _), v in ov["reasons"].items() if (kd, kk) == (kind, k)), ""))  # add_row keeps the label in its column


def apply_overrides(blocks, ov, cols, ncols, used):
    for b in blocks:  # `row` overrides on a printed total that no OCR reading found create it
        for k in list(ov["row"]):
            fk, _, what = k.partition("@")
            if fk == b["key"] and what in KIND_OUT and not any(e["kind"] == what for e in b["events"]):
                b["events"].append(dict(kind=what, name=KIND_OUT[what], vals=list(ov["row"][k]), label=b["page"], raw="(added by override)", flag="added_by_override", nth=1,
                                        _key=(b["key"], what, 1),
                                        corrections=[dict(column="(all)", raw_value="", final_value="|".join(map(str, ov["row"][k])), method="add_row",
                                                          detail=why(ov, "row", k))]))
                used.add(("row", k))
        for k, vals in ov["add"].items():
            fk, _, acct = k.partition("@")
            have = [it for it in b["items"] if it["account"] == acct]
            if fk == b["key"] and have:  # already read: the override states its values
                corr_vals(have[0], cols, list(have[0]["vals"]), list(vals[1]), "row_override", why(ov, "add_row", k))
                have[0]["vals"] = list(vals[1])
                have[0]["flag"] = (have[0]["flag"] + " values_overridden").strip()
                used.add(("add", k))
            if fk == b["key"] and not have:
                label, v = vals
                flow = "revenue" if re.match(r"^\w+-4-", acct) else "expense"
                parts = acct.split("-")
                b["items"].append(dict(page_label=b["page"], flow=flow, dept=parts[2], obj=parts[3], line_item=label, vals=list(v), raw="(added by override)",
                                       flag="added_by_override", printed_prefix=b["fund"], account=acct, nth=1, _key=(b["key"], acct, 1),
                                       corrections=[dict(column="(all)", raw_value="", final_value="|".join(map(str, v)), method="add_row",
                                                         detail=why(ov, "add_row", k))]))
                used.add(("add", k))
        for it in list(b["items"]):
            k0 = f"{b['key']}@{it['account']}"
            if k0 in ov["account"]:
                corr(it, "", it["account"], ov["account"][k0], "account_override", why(ov, "account", k0))
                it["account"] = ov["account"][k0]
                it["flag"] = (it["flag"] + " account_overridden").strip()
                used.add(("account", k0))
        # recompute ordinals after account fixes
        seen = Counter()
        for it in b["items"]:
            seen[it["account"]] += 1
            it["nth"] = seen[it["account"]]
            it["_key"] = (b["key"], it["account"], it["nth"])
        for it in list(b["items"]):
            k = item_key(b, it)
            if k in ov["drop"]:
                b["items"].remove(it)
                used.add(("drop", k))
        for it in b["items"]:
            k = item_key(b, it)
            if k in ov["notin"]:
                it["flag"] = (it["flag"] + " not_in_printed_total").strip()
                used.add(("notin", k))
            if k in ov["row"]:
                corr_vals(it, cols, list(it["vals"]), list(ov["row"][k]), "row_override", why(ov, "row", k))
                it["vals"] = list(ov["row"][k])
                it["flag"] = (it["flag"] + " values_overridden").strip()
                used.add(("row", k))
            for i, c in enumerate(cols):
                if (k, c) in ov["cell"] and len(it["vals"]) == ncols:
                    if it["vals"][i] != ov["cell"][(k, c)]:
                        corr(it, c, it["vals"][i], ov["cell"][(k, c)], "cell_override", why(ov, "cell", k, c))
                    it["vals"][i] = ov["cell"][(k, c)]
                    it["flag"] = (it["flag"] + " cell_overridden").strip()
                    used.add(("cell", k, c))
        for e in b["events"]:
            k = event_key(b, e)
            if k in ov["row"]:
                corr_vals(e, cols, list(e["vals"]), list(ov["row"][k]), "total_line_override", why(ov, "row", k))
                e["vals"] = list(ov["row"][k])
                e["flag"] = (e.get("flag", "") + " values_overridden").strip()
                used.add(("row", k))
            for i, c in enumerate(cols):
                if (k, c) in ov["cell"] and len(e["vals"]) == ncols:
                    if e["vals"][i] != ov["cell"][(k, c)]:
                        corr(e, c, e["vals"][i], ov["cell"][(k, c)], "total_override", why(ov, "cell", k, c))
                    e["vals"][i] = ov["cell"][(k, c)]
                    e["flag"] = (e.get("flag", "") + " cell_overridden").strip()
                    used.add(("cell", k, c))


# =====================================================================================================================================
# validation
# =====================================================================================================================================
def ev_of(b, kind):
    return next((e for e in b["events"] if e["kind"] == kind), None)


def good(x, ncols):
    return x is not None and len(x["vals"]) == ncols


def build_checks(blocks, ncols):
    """Each check = dict(id, block, what, target (a cell holder), terms [(holder, sign)]) meaning target = sum(sign * term)."""
    checks = []
    for b in blocks:
        beg, end, rt, et = (ev_of(b, k) for k in ("beg", "end", "revtotal", "exptotal"))
        revs = [it for it in b["items"] if it["flow"] == "revenue" and good(it, ncols) and "not_in_printed_total" not in it["flag"]]
        exps = [it for it in b["items"] if it["flow"] == "expense" and good(it, ncols) and "not_in_printed_total" not in it["flag"]]
        if good(rt, ncols):
            checks.append(dict(id="revenue_lines", block=b, what="revenue lines", target=rt, terms=[(r, 1) for r in revs]))
        if good(et, ncols):
            checks.append(dict(id="expense_lines", block=b, what="expense lines", target=et, terms=[(r, 1) for r in exps]))
        if good(beg, ncols) and good(end, ncols):
            terms = [(beg, 1)]
            terms += [(rt, 1)] if good(rt, ncols) else [(r, 1) for r in revs]
            terms += [(et, -1)] if good(et, ncols) else [(r, -1) for r in exps]
            checks.append(dict(id="cash_roll", block=b, what="beginning cash + revenues - expenditures", target=end, terms=terms))
    # fund roll-up printed under the Capital Projects ledgers: each line = sum over the ledgers (printed total, else the sum of its lines)
    cp = [b for b in blocks if b["fund"] == "41"]
    if cp:
        last = cp[-1]
        for kind, base, flow in (("rollup_beg", "beg", None), ("rollup_rev", "revtotal", "revenue"), ("rollup_exp", "exptotal", "expense")):
            t = ev_of(last, kind)
            if not good(t, ncols):
                continue
            terms = []
            for b in cp:
                p = ev_of(b, base)
                if good(p, ncols):
                    terms.append((p, 1))
                elif flow:
                    terms += [(it, 1) for it in b["items"] if it["flow"] == flow and good(it, ncols) and "not_in_printed_total" not in it["flag"]]
                else:
                    terms = None
                    break
            if terms is not None:
                checks.append(dict(id=kind, block=last, what=f"sum of the Capital Projects ledgers' {base}", target=t, terms=terms))
        te, tb, tr, tx = (ev_of(last, k) for k in ("rollup_end", "rollup_beg", "rollup_rev", "rollup_exp"))
        if all(good(x, ncols) for x in (te, tb, tr, tx)):
            checks.append(dict(id="rollup_cash_roll", block=last, what="rollup beginning cash + revenue - expenditures", target=te, terms=[(tb, 1), (tr, 1), (tx, -1)]))
    return checks


def holder_name(h):
    return h.get("account") or h.get("kind")


def evaluate(checks, cols, ov, ncols, used):
    problems, notes, results, fails = [], [], [], []
    for ck in checks:
        t = ck["target"]
        got = [sum(s * h["vals"][i] for h, s in ck["terms"]) for i in range(ncols)]
        n = len(ck["terms"])
        for i, c in enumerate(cols):
            d = t["vals"][i] - got[i]
            key = (f"{ck['block']['key']}@{ck['id']}", c)
            status = "ok"
            if d:
                tol_rounding = c.startswith("actual") and abs(d) <= min(3, (n + 1) // 2)
                if ov["allow"].get(key) == d:
                    used.add(("allow",) + key)
                    status = "allowed_override"
                    notes.append(f"{key[0]} {c}: printed {t['vals'][i]:,} vs {ck['what']} {got[i]:,} (diff {d:+,}; source arithmetic, allowed by override)")
                elif (abs(d) <= 1 and c.startswith("budget")) or tol_rounding:
                    status = "rounding"
                    notes.append(f"{key[0]} {c}: off by {d:+} (the city's printed figures are often a dollar or two off)")
                else:
                    status = "FAIL"
                    problems.append(f"{ck['block']['page']} {key[0]} {c}: printed {t['vals'][i]:,} vs {ck['what']} {got[i]:,} (diff {d:,})")
                    fails.append((ck, i, d, (n + 1) // 2 if c.startswith("actual") else 1))
            results.append(dict(block=ck["block"], check=ck["id"], column=c, printed=t["vals"][i], computed=got[i], diff=d, status=status))
    return problems, notes, results, fails


# =====================================================================================================================================
# auto-resolve (alternate readings of a cell: other OCR prep, neighbouring documents, cash chain; then single-digit confusions)
# =====================================================================================================================================
CONFUSIONS = {"6": "0", "0": "6", "9": "05", "5": "9"}  # true digits a misread digit may stand for (added to extract_budget.CONFUSIONS)


def autofix(blocks, ncols, cols, ov, alts, notes_out):
    fixed = set()
    for _ in range(60):
        checks = build_checks(blocks, ncols)
        fails = evaluate(checks, cols, ov, ncols, set())[3]
        changed = False
        for ck, i, d, tol in fails:
            if abs(d) <= 1:
                continue
            real, synth = [], []
            cells = [(ck["target"], -1)] + [(h, s) for h, s in ck["terms"]]
            for h, s in cells:
                if (id(h), i) in fixed:
                    continue
                # sign: target = sum(s*term), so changing term by x changes diff by -s*x; changing target by x changes diff by +x
                for v, src in (alts.get(h.get("_key")) or {}).get(i, []):
                    if v != h["vals"][i]:
                        real.append((-(s if h is not ck["target"] else -1) * (v - h["vals"][i]), h, v, src))
                if h["vals"][i] > 0 and abs(d) >= 10:
                    for v in B.digit_variants(h["vals"][i], CONFUSIONS):
                        synth.append((-(s if h is not ck["target"] else -1) * (v - h["vals"][i]), h, v, "a single-digit OCR confusion"))
            flips = []
            for h, s_ in cells:
                if (id(h), i) not in fixed and h["vals"][i] != 0:
                    v = -h["vals"][i]
                    flips.append((-(s_ if h is not ck["target"] else -1) * (v - h["vals"][i]), h, v, "a lost/added parenthesis (sign)"))
            for tier in (real, flips, synth):
                hit = [c for c in tier if abs(c[0] + d) <= tol]  # the change in diff must cancel d
                if hit:
                    best_res = min(abs(c[0] + d) for c in hit)
                    hit = [c for c in hit if abs(c[0] + d) == best_res]
                cands = {}
                for c in hit:
                    cands.setdefault((id(c[1]), c[2]), []).append(c)
                chosen = None
                if len(cands) == 1:
                    chosen = next(iter(cands.values()))[0]
                elif len(cands) > 1 and tier is real:  # several readings tie: the one most OCR readings agree on, if clearly ahead
                    ranked = sorted(cands.values(), key=len, reverse=True)
                    if len(ranked[0]) >= len(ranked[1]) + 2:
                        chosen = ranked[0][0]
                if chosen is not None:
                    _, h, v, src = chosen
                    notes_out.append(f"auto-resolved {ck['block']['key']}@{holder_name(h)} {cols[i]}: {h['vals'][i]:,} -> {v:,} (from {src}; makes {ck['id']} tie)")
                    tag = "auto_resolved_digit" if tier is synth else ("auto_resolved_sign" if tier is flips else "auto_resolved")
                    corr(h, cols[i], h["vals"][i], v, tag, f"from {src}; makes {ck['id']} tie")
                    h["vals"][i] = v
                    if tag not in h.get("flag", ""):
                        h["flag"] = (h.get("flag", "") + " " + tag).strip()
                    fixed.add((id(h), i))
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break


def completions(h, b, ncols, alts, k):
    """Every way to complete a short cell run: the missing values come from a small pool (zeros, repeats of the row's own values, the other
    reading of the cell, cash balances / line sums for totals); the printed values keep their order."""
    import itertools
    orig = list(h["vals"])
    pool = set(orig) | {0}
    for d in (alts.get(h["_key"]) or {}).values():
        pool |= {v for v, _ in list(d)[:3]}
    if h.get("kind") in ("beg", "end"):
        for other in b["events"]:
            if other is not h and other["kind"] in ("beg", "end") and len(other["vals"]) == ncols:
                pool |= set(other["vals"])
    if h.get("kind") in ("revtotal", "exptotal"):
        fl = "revenue" if h["kind"] == "revtotal" else "expense"
        lines = [it for it in b["items"] if it["flow"] == fl and len(it["vals"]) == ncols]
        pool |= {sum(it["vals"][j] for it in lines) for j in range(ncols)}
    out = set()
    for ins in itertools.product(sorted(pool), repeat=k):
        for pos in itertools.combinations(range(ncols), k):
            new, it_i, it_o = [], iter(ins), iter(orig)
            for j in range(ncols):
                new.append(next(it_i) if j in pos else next(it_o))
            out.add(tuple(new))
    return out


def fill_short(blocks, ncols, cols, ov, alts, notes_out):
    """Rows/totals the OCR left short of values (runs of zeros or identical neighbours collapse, a cell is lost): complete them jointly, per
    block, where that makes every check of the block tie; adopted only if exactly one assignment does."""
    import itertools
    for b in blocks:
        shorts = [h for h in b["items"] + b["events"] if 1 <= ncols - len(h["vals"]) <= 2 and h.get("kind") != "unknown" and h["vals"]]
        if not shorts:
            continue
        cands = [sorted(completions(h, b, ncols, alts, ncols - len(h["vals"]))) for h in shorts]
        # keep the search small: the biggest candidate lists are dropped (those cells stay short -> reported) until the product is manageable
        while True:
            total = 1
            for c in cands:
                total *= max(1, len(c))
            if total <= 30000:
                break
            big = max(range(len(cands)), key=lambda i: len(cands[i]))
            cands[big] = []
        origs = [list(h["vals"]) for h in shorts]
        sols = []
        for combo in itertools.product(*[c or [None] for c in cands]):
            for h, v in zip(shorts, combo):
                h["vals"] = list(v) if v is not None else h["vals"]
            if any(v is None for v in combo):
                continue
            cks = build_checks([b], ncols)
            if cks and all(good(h, ncols) for h in shorts) and not evaluate(cks, cols, ov, ncols, set())[0]:
                sols.append(combo)
                if len(sols) > 3:
                    break
        for h, o in zip(shorts, origs):
            h["vals"] = o
        if len(sols) == 1:
            for h, v, o in zip(shorts, sols[0], origs):
                corr(h, "(all)", "|".join(map(str, o)), "|".join(map(str, v)), "placed_by_sum", "OCR left cells out; the only completion that ties every check of the block")
                h["vals"] = list(v)
                h["flag"] = (h.get("flag", "") + " placed_by_sum").strip()
                notes_out.append(f"{b['key']}@{holder_name(h)}: OCR gave {len(o)} values {o}; completed to {list(v)} (the only completion that ties every check of the block)")
        elif len(sols) > 1:
            notes_out.append(f"{b['key']}: {len(sols)}+ completions of the short cells {[holder_name(h) for h in shorts]} tie; left short")


def transplant(blocks, others, notes, ncols):
    """A printed total or cash balance that one OCR prep lost altogether (a ruled line swallowed its row) is taken from another prep's reading
    of the same block (same fund/ledger/page and the same numbered accounts); it still has to satisfy every check, flag `from_prep_X`."""
    for b in blocks:
        mine = sorted(it["account"] for it in b["items"] if it["dept"] != "000")
        for pname, ob_list in others.items():
            same = [x for x in ob_list if x["key"] == b["key"] and x["page"] == b["page"]
                    and sorted(it["account"] for it in x["items"] if it["dept"] != "000") == mine]
            if not same:
                continue
            ob = same[0]
            have_e = {e["kind"] for e in b["events"]}
            for e in ob["events"]:
                if e["kind"] not in have_e and e["kind"] in ("beg", "end", "revtotal", "exptotal") and len(e["vals"]) == ncols:
                    e2 = dict(e)
                    e2["flag"] = (e.get("flag", "") + f" from_prep_{pname}").strip()
                    b["events"].append(e2)
                    have_e.add(e["kind"])
                    notes.append(f"{b['key']}@{e['kind']}: missing in this OCR reading, taken from prep {pname}")
        kinds = Counter()
        for e in b["events"]:
            kinds[e["kind"]] += 1
            e["nth"] = kinds[e["kind"]]
            e["_key"] = (b["key"], e["kind"], e["nth"])


def repair_short(blocks, others, notes, ncols):
    """A row/total this OCR reading got the wrong number of values for (cells lost, or rule marks read as extra numbers) takes the other
    readings' complete version of the same cell run (same block, account/kind and ordinal): the cell-wise plurality, ties broken by prep
    order. For a short run the values we do have must appear, in order, in the replacement (one misread cell allowed). The arithmetic checks
    (and auto-resolve) still decide whether the result is right."""
    import itertools
    lookup = {}
    for pname, ob_list in others.items():
        for ob in ob_list:
            for h in ob["items"] + ob["events"]:
                if len(h["vals"]) == ncols and h.get("kind") != "unknown":
                    lookup.setdefault(h["_key"], []).append((tuple(h["vals"]), pname))
    for b in blocks:
        for h in b["items"] + b["events"]:
            if len(h["vals"]) != ncols and h.get("kind") != "unknown" and lookup.get(h["_key"]):
                reads = lookup[h["_key"]]
                best = tuple(Counter(v[i] for v, _ in reads).most_common(1)[0][0] for i in range(ncols))
                if 0 < len(h["vals"]) < ncols:
                    k = len(h["vals"])
                    if not any(sum(1 for x, j in zip(h["vals"], pos) if x != best[j]) <= 1 for pos in itertools.combinations(range(ncols), k)):
                        continue
                src = ",".join(p for v, p in reads)
                notes.append(f"{b['key']}@{holder_name(h)}: reading with {len(h['vals'])} values {h['vals']} replaced by the other readings' {list(best)} (preps {src})")
                corr(h, "(all)", "|".join(map(str, h["vals"])), "|".join(map(str, best)), "from_prep_vote", f"the other OCR readings' complete version (preps {src})")
                h["vals"] = list(best)
                h["flag"] = (h.get("flag", "") + " from_prep_vote").strip()


def vote_cells(blocks, others, notes, ncols, cols):
    """Cell-wise plurality over all OCR readings: a cell where this reading disagrees with an unambiguous majority (>= 3 readings and at
    least 2 ahead of the next value) takes the majority value. The arithmetic checks that follow decide whether that was right."""
    lookup = {}
    for pname, ob_list in others.items():
        for ob in ob_list:
            for h in ob["items"] + ob["events"]:
                if len(h["vals"]) == ncols and h.get("kind") != "unknown":
                    lookup.setdefault(h["_key"], []).append(h["vals"])
    for b in blocks:
        for h in b["items"] + b["events"]:
            if len(h["vals"]) != ncols or h.get("kind") == "unknown":
                continue
            readings = lookup.get(h["_key"], [])
            for i in range(ncols):
                votes = Counter(v[i] for v in readings)
                if not votes:
                    continue
                top = votes.most_common(2)
                (val, n1), n2 = top[0], (top[1][1] if len(top) > 1 else 0)
                if val != h["vals"][i] and n1 >= 3 and n1 - n2 >= 2 and votes.get(h["vals"][i], 0) + 1 < n1:
                    notes.append(f"{b['key']}@{holder_name(h)} {cols[i]}: {h['vals'][i]:,} -> {val:,} (majority of {n1 + n2 + votes.get(h['vals'][i], 0) * 0} OCR readings)")
                    corr(h, cols[i], h["vals"][i], val, "prep_vote", f"majority of the other OCR readings ({n1} agree)")
                    h["vals"][i] = val
                    h["flag"] = (h.get("flag", "") + " vote").strip()


def disp(b):
    """Name written to the `fund` column."""
    if b["fund"] in ("PP", "FP"):
        return b["name"] + " Fund"
    return f"{b['name']} #{b['fund']}" + (f" - {b['ledger']}" if b["ledger"] else "")


KIND_OUT = {"beg": "beginning_cash", "end": "ending_cash", "revtotal": "fund_revenues", "exptotal": "fund_expenses",
            "rollup_beg": "rollup_beginning_cash", "rollup_rev": "rollup_revenues", "rollup_exp": "rollup_expenses", "rollup_end": "rollup_ending_cash"}
KIND_IN = {v: k for k, v in KIND_OUT.items()}
CHAIN = {3: {1: [0], 2: [1]}, 4: {1: [0], 2: [0], 3: [2, 1]}}  # column c's beginning cash = the ending cash of these earlier columns


def build_alts(fy, prep, first):
    """{holder key: {column index: [(value, source)]}}: the other prep's reading, the neighbouring documents' shared column, the cash chain."""
    me = first[prep]
    cols = me["cols"]
    alts = {}
    for p_, o in first.items():
        if p_ == prep:
            continue
        for b in o["blocks"]:
            for h in b["items"] + b["events"]:
                if len(h["vals"]) == len(cols) and h.get("kind") != "unknown":
                    for i, v in enumerate(h["vals"]):
                        alts.setdefault(h["_key"], {}).setdefault(i, []).append((v, f"OCR prep {p_}"))
    # neighbouring documents (their final extracts)
    for nb in (int(fy) - 1, int(fy) + 1):
        mine = f"budget_FY{nb}" if nb < int(fy) else f"budget_FY{fy}"  # the column both documents print
        if mine not in cols:
            continue
        ci = cols.index(mine)
        rows, tots = {}, {}
        pr = os.path.join(DATA_E, "extracted", f"FY{nb}_budget_funds_revenue.csv")
        pe = os.path.join(DATA_E, "extracted", f"FY{nb}_budget_funds_expense.csv")
        pt = os.path.join(DATA_E, "extracted", f"FY{nb}_budget_funds_totals.csv")
        if not os.path.exists(pt):
            continue
        for path in (pr, pe):
            seen = Counter()
            for r in csv.DictReader(open(path)):
                if r["column"] == mine:
                    k = (r["fund"], r["account"])
                    seen[k] += 1
                    rows[(r["fund"], r["account"], seen[k])] = int(r["amount"])
        for r in csv.DictReader(open(pt)):
            if r["column"] == mine:
                tots[(r["fund"], r["kind"])] = int(r["amount"])
        for b in me["blocks"]:
            for h in b["items"]:
                v = rows.get((disp(b), h["account"], h["nth"]))
                if v is not None:
                    alts.setdefault(h["_key"], {}).setdefault(ci, []).append((v, f"the FY{nb} document"))
            for h in b["events"]:
                if h["kind"] in KIND_OUT:
                    v = tots.get((disp(b), KIND_OUT[h["kind"]]))
                    if v is not None and h["nth"] == 1:
                        alts.setdefault(h["_key"], {}).setdefault(ci, []).append((v, f"the FY{nb} document"))
    # cash chain within the document
    chain = CHAIN[len(cols)]
    for b in me["blocks"]:
        beg, end = ev_of(b, "beg"), ev_of(b, "end")
        if beg and end and len(beg["vals"]) == len(cols) and len(end["vals"]) == len(cols):
            for c, prevs in chain.items():
                for pcol in prevs:
                    alts.setdefault(beg["_key"], {}).setdefault(c, []).append((end["vals"][pcol], "the previous column's ending cash"))
                    alts.setdefault(end["_key"], {}).setdefault(pcol, []).append((beg["vals"][c], "the next column's beginning cash"))
    return alts


def run(fy, prep, alts=None, pages=None, others=None):
    pages = pages or pages_of(fy, prep)
    pre = parse(pages, 0)
    counts = Counter(len(i["vals"]) for b in pre for i in b["items"])
    ncols = 4 if int(fy) <= 2022 else 3
    guess = max(set(counts), key=counts.get)
    cols = B.column_labels(fy, ncols)
    blocks = parse(pages, ncols)
    ov = load_overrides(fy)
    used, notes = set(), []
    if others:
        transplant(blocks, others, notes, ncols)
        repair_short(blocks, others, notes, ncols)
    apply_overrides(blocks, ov, cols, ncols, used)
    for b in blocks:  # an all-zero cell run the OCR cut short
        for h in b["items"] + b["events"]:
            if len(h["vals"]) < ncols and not any(h["vals"]) and (h["vals"] or h.get("dept") not in (None, "000")):
                h["vals"], h["flag"] = [0] * ncols, (h.get("flag", "") + " padded_zeros").strip()
    if alts is not None:
        autofix(blocks, ncols, cols, ov, alts, notes)
    fill_short(blocks, ncols, cols, ov, alts or {}, notes)
    problems = []
    for b in blocks:
        for it in b["items"]:
            if len(it["vals"]) != ncols:
                problems.append(f"{it['page_label']} {b['key']}@{it['account']} {it['line_item']!r}: {len(it['vals'])} values, expected {ncols}: {it['raw']}")
        for e in b["events"]:
            if len(e["vals"]) != ncols and e["kind"] != "unknown":
                problems.append(f"{e['label']} {b['key']}@{e['kind']} {e['name']!r}: {len(e['vals'])} values, expected {ncols}: {e['raw']}")
            if e["kind"] == "unknown":
                notes.append(f"{e['label']} {b['key']}: unclassified line with numbers {e['name']!r} {e['vals']} (not extracted)")
        rollup_end = b["fund"] == "41" and any(ev_of(x, "rollup_end") for x in blocks if x["fund"] == "41")
        if not ev_of(b, "beg") or (not ev_of(b, "end") and not rollup_end):
            problems.append(f"{b['page']} {b['key']}: no printed beginning/ending cash found")
        if not b["items"]:
            problems.append(f"{b['page']} {b['key']}: no lines")
    checks = build_checks(blocks, ncols)
    p2, n2, results, _ = evaluate(checks, cols, ov, ncols, used)
    problems += p2
    notes += n2
    # informational: beginning cash of a column should equal the ending cash it follows
    chain = CHAIN[ncols]
    for b in blocks:
        beg, end = ev_of(b, "beg"), ev_of(b, "end")
        if good(beg, ncols) and good(end, ncols):
            for c, prevs in chain.items():
                if beg["vals"][c] not in [end["vals"][p] for p in prevs]:
                    d = beg["vals"][c] - end["vals"][prevs[0]]
                    results.append(dict(block=b, check="cash_chain", column=cols[c], printed=beg["vals"][c], computed=end["vals"][prevs[0]], diff=d, status="note"))
                    notes.append(f"{b['page']} {b['key']} {cols[c]}: beginning cash {beg['vals'][c]:,} differs from ending cash of {cols[prevs[0]]} {end['vals'][prevs[0]]:,} ({d:+,}) [cash chain, informational]")
                else:
                    results.append(dict(block=b, check="cash_chain", column=cols[c], printed=beg["vals"][c], computed=beg["vals"][c], diff=0, status="ok"))
    for k in set(ov["cell"]) | set(ov["allow"]):
        if ("cell",) + k not in used and ("allow",) + k not in used:
            notes.append(f"override never used: {k}")
    for kind in ("row", "account", "drop", "add", "notin"):
        for k in ov[kind]:
            if (kind, k) not in used:
                notes.append(f"override never used: {kind} {k}")
    return dict(prep=prep, cols=cols, blocks=blocks, problems=problems, notes=notes, results=results, ncols=ncols)


HDR = ["fiscal_year", "doc", "page_label", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"]


def write(fy, res):
    base = os.path.join(DATA_E, "extracted")
    ncols, cols = res["ncols"], res["cols"]
    for flow in ("revenue", "expense"):
        with open(os.path.join(base, f"FY{fy}_budget_funds_{flow}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(HDR)
            for b in res["blocks"]:
                for it in b["items"]:
                    if it["flow"] == flow and len(it["vals"]) == ncols:
                        for c, v in zip(cols, it["vals"]):
                            w.writerow([fy, f"FY{fy}_budget", it["page_label"], disp(b), it["dept"], it["account"], it["line_item"], c, v, it["flag"]])
    with open(os.path.join(base, f"FY{fy}_budget_funds_totals.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "fund", "kind", "name", "column", "amount"])
        for b in res["blocks"]:
            for e in b["events"]:
                if e["kind"] in KIND_OUT and len(e["vals"]) == ncols:
                    for c, v in zip(cols, e["vals"]):
                        w.writerow([fy, f"FY{fy}_budget", e["label"], disp(b), KIND_OUT[e["kind"]], e["name"], c, v])
    with open(os.path.join(base, f"FY{fy}_budget_funds_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "fund", "check", "column", "printed", "computed", "diff", "status"])
        for r in res["results"]:
            w.writerow([fy, f"FY{fy}_budget", disp(r["block"]), r["check"], r["column"], r["printed"], r["computed"], r["diff"], r["status"]])


def write_corrections(fy, res):
    out = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_funds_corrections.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(B.CORRECTION_HEADER)
        for b in res["blocks"]:
            for it in b["items"]:
                if len(it["vals"]) == res["ncols"]:
                    for c in it.get("corrections", []):
                        w.writerow([fy, f"FY{fy}_budget", it["page_label"], disp(b), it["dept"], it["account"], it["line_item"], c["column"], c["raw_value"],
                                    c["final_value"], c["method"], c["detail"], it.get("raw", "")])
            for e in b["events"]:
                if e["kind"] in KIND_OUT and len(e["vals"]) == res["ncols"]:
                    for c in e.get("corrections", []):
                        w.writerow([fy, f"FY{fy}_budget", e["label"], disp(b), "", "", e["name"], c["column"], c["raw_value"], c["final_value"], c["method"],
                                    c["detail"], e.get("raw", "")])
    return out


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    preps = PREPS_F if arg == "auto" else [arg]
    pages = {p: pages_of(fy, p) for p in preps}
    first = {p: run(fy, p, pages=pages[p]) for p in preps}
    results = []
    for p in preps:
        r = run(fy, p, build_alts(fy, p, first), pages[p], {q: first[q]["blocks"] for q in preps if q != p})
        results.append(r)
        print(f"prep {p}: {len(r['problems'])} problems, {sum(len(b['items']) for b in r['blocks'])} lines, {len(r['blocks'])} blocks")
    res = min(results, key=lambda r: len(r["problems"]))
    if "--dry" not in sys.argv:
        write(fy, res)
        write_corrections(fy, res)
    print(f"\nchosen prep: {res['prep']}; columns {res['cols']}; {len(res['blocks'])} blocks; {len(res['problems'])} problems")
    for n in res["notes"]:
        print("  note:", n)
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
