"""Gaps left by the first debt / pension / OPEB extraction (roadmap D3): police and fire pension plan net position, target investment
allocations, the FY2012-FY2015 schedules of funding progress and employer contributions, the FY2012-FY2015 Statement of Net Position and
reconciliation cross-checks, and the FY2012 debt service to maturity.

Usage:
  python3 pipelines/belvidere_il/audit_pension_gaps.py FY2020   # one audit -> data/belvidere_il/extracted/audits/FY2020_audit_{pension_plan_net_position,
                                                                #    pension_investment_allocation,pension_funding_progress,pension_employer_contributions,
                                                                #    pension_gaps_checks}.csv
  python3 pipelines/belvidere_il/audit_pension_gaps.py --build  # consolidate into data/belvidere_il/{pension_plan_net_position,...}.csv + audit_pension_gaps_checks.csv

Pages are read like the rest of the debt / pension extractors: positioned words from the text layer (FY2016+) or tesseract word boxes (FY2012-15),
`audit_debt_pension_common.page_lines`.  Every table is validated by identities (totals = lines, total column = police + fire, roll-forward) and by a
second, independent print of the same figures (the combining statement vs the note, the basic statement vs the combining statement, the plan's
fiduciary net position in the pension note, the previous year's ending balance).  Overrides (kind `allow`, `fixtok`) live in the same
data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv files as the other debt / pension extractors.
"""
import csv
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_debt_pension_common import (Checker, DATA_E, ENTITY_ID, OUT_DIR, all_plain_text, assign_columns, is_page_no, load_allow, load_overrides,  # noqa: E402
                                       n_pages, page_lines)

YEARS = range(2012, 2027)
TEXT_FROM = 2016
PLANS = ("police", "fire", "total")

PLAN_TABLE_COLUMNS = ["fiscal_year", "doc", "pdf_page", "statement", "source", "plan_id", "section", "line_item", "line_id", "amount", "flag"]
ALLOC_COLUMNS = ["fiscal_year", "doc", "pdf_page", "plan_id", "asset_class", "range_text", "target_pct", "expected_return_pct", "expected_return_text", "flag"]
FUNDING_COLUMNS = ["fiscal_year", "doc", "pdf_page", "plan_id", "valuation_date", "actuarial_value_of_assets", "accrued_liability", "funded_ratio_pct",
                   "unfunded_liability", "covered_payroll", "unfunded_pct_covered_payroll", "flag"]
CONTRIB_COLUMNS = ["fiscal_year", "doc", "pdf_page", "plan_id", "contribution_fy", "employer_contributions", "required_contribution", "pct_contributed", "flag"]


def doc(fy):
    return f"FY{fy}_audit"


def out_path(fy, name):
    return os.path.join(OUT_DIR, f"FY{fy}_audit_{name}.csv")


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def source_of(fy):
    return "text" if fy >= TEXT_FROM else "ocr"


# ---- page texts (for locating) -------------------------------------------------------------------------------
_TEXTS = {}


def page_texts(fy):
    """[text of page 1, 2, ...] used only to find the pages: the text layer's layout text (FY2016+) or the OCR words (FY2012-15)."""
    if fy not in _TEXTS:
        if fy >= TEXT_FROM:
            _TEXTS[fy] = all_plain_text(fy)
        else:
            n = n_pages(fy)
            pl = page_lines(fy, list(range(1, n + 1)), "ocr", "gray")
            _TEXTS[fy] = ["\n".join(" ".join(ln.text.split()) for ln in pl[p]) for p in range(1, n + 1)]
    return _TEXTS[fy]


def squash(t):
    return " ".join(t.split())


def is_toc(t):
    return len(re.findall(r"\.{6,}", t)) > 3


# ---- overrides (data/belvidere_il/overrides/audit_pension_gaps_FY20XX.csv: kind, key, value, exception_id, reason) --------------------------
def load_gap_overrides(fy):
    path = os.path.join(DATA_E, "overrides", f"audit_pension_gaps_FY{fy}.csv")
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def gap_allow(fy):
    """kind `allow`: key = check key, value = printed minus computed difference the source itself contains."""
    return {o["key"]: (float(o["value"]) if "." in o["value"] else int(o["value"])) for o in load_gap_overrides(fy) if o["kind"] == "allow"}


def apply_fixtok(fy, lines):
    """kind `fixtok`: key `page|wrong token`, value the token read from the page image (OCR misreads only)."""
    fixes = [o for o in load_gap_overrides(fy) if o["kind"] == "fixtok"]
    for ln in lines:
        for w in ln.words:
            for o in fixes:
                pg, frm = o["key"].split("|")
                if int(pg) == ln.page and w.t == frm:
                    w.t = o["value"]
                    ln.text = " ".join(x.t for x in ln.words)


JUNK_TOKENS = {":", ".", "|", ";", "_", ",", "'", "`"}


def clean_ocr_line(ln):
    """Scan noise: stray marks after the numbers, a lone '(' before a number, '=' or '~' for a dash."""
    ws = []
    for w in ln.words:
        if w.t in ("=", "~", "—", "–"):
            w.t = "-"
        ws.append(w)
    out, i = [], 0
    while i < len(ws):
        w = ws[i]
        if w.t == "(" and i + 1 < len(ws):
            nxt = ws[i + 1]
            nxt.t = "(" + nxt.t
            nxt.x0 = w.x0
            i += 1
            continue
        out.append(w)
        i += 1
    while out and out[-1].t in JUNK_TOKENS:
        out.pop()
    ln.words = out
    ln.text = " ".join(w.t for w in out)


# =============================================================================================================
# A. Plan net position (police and firefighters' pension): combining statements, notes (FY2016+), basic statements
# =============================================================================================================
NP_IDS = [
    (r"^total investments", "total_investments"), (r"^total receivables", "total_receivables"), (r"^total assets", "total_assets"),
    (r"^total liabilities", "total_liabilities"), (r"^accounts payable", "accounts_payable"),
    (r"^net (position|assets).*(restricted|held)|^for pensions?$", "net_position"),
    (r"^(cash|non-pooled)", "cash_and_short_term_investments"), (r"^prepaid", "prepaid_items"),
]
CH_IDS = [
    (r"^total contributions", "total_contributions"), (r"^(contributions - )?employer", "employer_contributions"),
    (r"^(contributions - )?(employee|plan members?)", "employee_contributions"), (r"^other$", "other_contributions"),
    (r"^total investment income", "total_investment_income"), (r"^(net )?(appreciation|depreciation|change in fair)|fair value", "fair_value_change"),
    (r"^interest", "interest"), (r"investment expense", "investment_expense"), (r"^net investment income", "net_investment_income"),
    (r"^total additions", "total_additions"), (r"^benefits", "benefits_and_refunds"), (r"^admin", "administrative_expenses"),
    (r"^total deductions", "total_deductions"), (r"^(net (increase|decrease)|change in net (position|assets))", "net_change"), (r"^refund", "refund_of_contributions"),
]
SKIP_RE = re.compile(r"(?i)^(city of belvidere|notes to (the )?financial|see accompanying|\(see independent|the notes to|pension trust fund|combining statement|"
                     r"statement of|schedule of|for the year ended|year ended|april 30|fiduciary funds|police pension plan and|continued|police\s+firefighters|"
                     r"pension\s+pension|police pension$|firefighters.? pension$|\d+\.\s+defined benefit)")


def slug(t):
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:40]


def read_plan_table(lines, kind, ncols=3, ocr=False):
    """Lines of one statement (several pages) -> rows (section, label, line_id, [values per column], page)."""
    # column anchors per page: median right edges of the lines that carry all columns
    by_page = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    anchors = {}
    for p, pls in by_page.items():
        if ocr:
            for ln in pls:
                clean_ocr_line(ln)
        full = [ln.split()[1] for ln in pls if len(ln.split()[1]) == ncols]
        if full:
            anchors[p] = [statistics.median([f[c][0] for f in full]) for c in range(ncols)]
    rows, section, sub, pend = [], "", "", []
    last_id = ""
    ids = NP_IDS if kind == "net_position" else CH_IDS
    for ln in lines:
        t = " ".join(ln.text.split())
        if not t or is_page_no(t) or ln.page not in anchors or re.match(r"(?i)^(for the year ended )?april 30,? ?\d{4}$", t):
            continue
        if ocr:
            clean_ocr_line(ln)
        label, nums = ln.split()
        nums = merge_split_numbers(ln, label, nums, ncols)
        u = label.upper().strip().rstrip(":")
        sect = None
        if kind == "net_position":
            sect = {"ASSETS": "assets", "LIABILITIES": "liabilities", "NET POSITION": "net_position", "NET ASSETS": "net_position"}.get(u)
        else:
            sect = {"ADDITIONS": "additions", "DEDUCTIONS": "deductions", "ADDITIONS (CONTINUED)": "additions", "NET ASSETS": "roll"}.get(u)
        if sect:
            section, sub, pend = sect, "", []
            continue
        label = label.rstrip(":").strip() if not nums else label
        if nums and not label and kind == "net_position" and section == "liabilities":
            label = "Accounts payable"       # OCR lost the only liability line's label (FY2015); the total column equals the net position gap
        if not nums:
            if re.match(r"(?i)^net position (restricted|held)", label) or re.match(r"(?i)^net position$", label):
                if kind == "changes":
                    section = "roll"
                pend = [label]
                continue
            if re.match(r"(?i)^for pensions?$", label) and kind == "changes":
                section = "roll"
                pend = []
                continue
            if re.match(r"(?i)^investments?,?( at fair value)?$", label):
                sub = "inv"
                pend = []
                continue
            if re.match(r"(?i)^receivables$", label):
                sub = "rec"
                pend = []
                continue
            if SKIP_RE.match(label) or not label:
                continue
            pend.append(label)
            continue
        cols = assign_columns(nums, anchors[ln.page], tol=24)
        vals = [cols[c][0] if c in cols else None for c in range(ncols)]
        joins = bool(pend) and (label[:1].islower() or pend[-1][-1:] in "'’" or re.match(r"(?i)^net position", pend[0]) is not None
                                or re.match(r"(?i)^for pensions?", label) is not None or not label)
        full_label = " ".join(pend + [label]).strip() if joins else label
        pend = []
        lid = ""
        low = re.sub(r"^\W+", "", full_label.lower())
        for pat, i in ids:
            if re.search(pat, low):
                lid = i
                break
        if not lid and not full_label and kind == "changes" and last_id in ("fair_value_change", "interest"):
            lid = "total_investment_income"
        if kind == "changes" and section == "roll":
            if re.search(r"(?i)(^|\s)(may|beginning|january)$|^(may|beginning|january)\b", full_label):
                lid = "net_position_beginning"
            elif re.search(r"(?i)(^|\s)(april|ending|december)$|^(april|ending|december)\b", full_label):
                lid = "net_position_ending"
        if kind == "net_position" and section == "net_position" and not lid:
            lid = "net_position"
        if kind == "changes" and lid == "net_position":
            lid = ""
        if not lid:
            lid = "item:" + slug(full_label or "unlabeled")
        if kind == "net_position" and lid.startswith("item:") and section == "assets":
            lid = ("investment:" if sub == "inv" else "receivable:" if sub == "rec" else "asset:") + slug(full_label)
        if lid == "total_investments" or lid == "total_receivables":
            sub = ""
        if all(v is None for v in vals):
            continue
        rows.append((section, full_label or "(unlabeled subtotal)", lid, vals, ln.page, sub))
        last_id = lid
    # the expense is printed as a deduction in parentheses, in FY2012 as a positive number: keep it negative (it is subtracted)
    fixed = []
    for r in rows:
        if r[2] == "investment_expense":
            r = (r[0], r[1], r[2], [(-abs(v) if v is not None else None) for v in r[3]], r[4], r[5])
        fixed.append(r)
    return fixed


def merge_split_numbers(ln, label, nums, ncols):
    """OCR splits a number at a space ('888 586' for 888,586): merge small neighbours when there are more numbers than columns."""
    if len(nums) <= ncols:
        return nums
    out, i = [], 0
    while i < len(nums):
        x1, v, k, raw = nums[i]
        if i + 1 < len(nums) and re.fullmatch(r"\d{1,3}", raw) and re.fullmatch(r"\d{3}\)?", nums[i + 1][3]) and len(out) + (len(nums) - i) > ncols:
            nx = nums[i + 1]
            out.append((nx[0], int(raw + nx[3].rstrip(")")) * (-1 if nx[3].endswith(")") else 1), "num", raw + nx[3]))
            i += 2
            continue
        out.append(nums[i])
        i += 1
    return out


def find_pages(fy):
    """Pages of the four prints: combining net position, combining changes, note tables (FY2016+), basic statements."""
    texts = page_texts(fy)
    res = dict(comb_np=None, comb_ch=None, note=[], stmt_np=None, stmt_ch=None)
    for i, t in enumerate(texts, 1):
        if is_toc(t):
            continue
        head = squash(t)[:700].upper()
        if res["comb_np"] is None and re.search(r"COMBINING STATEMENT OF (FIDUCIARY )?NET (POSITION|ASSETS)", head) and "PENSION" in head and i > 40:
            res["comb_np"] = i
        elif res["comb_ch"] is None and re.search(r"COMBINING STATEMENT OF CHANGES IN (PLAN |FIDUCIARY )?NET (POSITION|ASSETS)", head) and "PENSION" in head and i > 40:
            res["comb_ch"] = i
        elif res["stmt_np"] is None and re.search(r"STATEMENT OF (FIDUCIARY NET (POSITION|ASSETS)|NET (POSITION|ASSETS) - FIDUCIARY)", head) and "COMBINING" not in head and 20 < i < 50:
            res["stmt_np"] = i
        elif res["stmt_ch"] is None and re.search(r"STATEMENT OF CHANGES IN (FIDUCIARY )?NET (POSITION|ASSETS)", head) and "FIDUCIARY" in head and "COMBINING" not in head and "BUDGET" not in head and 20 < i < 50:
            res["stmt_ch"] = i
    if fy >= TEXT_FROM:
        for i, t in enumerate(texts, 1):
            if re.search(r"a\.\s+Plan Net Position", t) and "Firefighters" in t and not is_toc(t):
                res["note"] = [i]
                j = i
                while j < len(texts) and not re.search(r"April 30\s+\$?\s*[\d,]+\s+\$?\s*[\d,]+\s+\$?\s*[\d,]+", texts[j - 1]):
                    j += 1
                res["note"] = list(range(i, j + 1))
                break
    return res


def pull(lines, start_pat, end_pat, include_end=True):
    """Sub-list of lines from the first line matching start_pat to the first later line matching end_pat."""
    on, out = False, []
    for ln in lines:
        t = " ".join(ln.text.split())
        if not on and re.search(start_pat, t):
            on = True
        if on:
            out.append(ln)
            if re.search(end_pat, t) and len(out) > 3:
                break
    return out


def collect_plan_tables(fy):
    """-> {(statement, source): rows} for the pages found."""
    pg = find_pages(fy)
    src = source_of(fy)
    prep = "gray"
    want = [p for p in [pg["comb_np"], pg["comb_ch"], pg["stmt_np"], pg["stmt_ch"]] if p] + pg["note"]
    if pg["comb_ch"]:
        want.append(pg["comb_ch"] + 1) if fy < TEXT_FROM and False else None
    pls = page_lines(fy, sorted(set(want)), src, prep) if want else {}
    out = {}
    for key in ("comb_np", "comb_ch"):
        p = pg[key]
        if p:
            kind = "net_position" if key == "comb_np" else "changes"
            lines = pls[p]
            apply_fixtok(fy, lines)
            out[(kind, "combining")] = (read_plan_table(lines, kind, ocr=fy < TEXT_FROM), p)
    for key, kind in (("stmt_np", "net_position"), ("stmt_ch", "changes")):
        p = pg[key]
        if p:
            lines = pls[p]
            apply_fixtok(fy, lines)
            hdr = next((ln for ln in lines if re.match(r"(?i)^(total assets|total additions)", " ".join(ln.text.split()))), None)
            ncols = len(hdr.split()[1]) if hdr is not None else 1
            out[(kind, "statement")] = (read_plan_table(lines, kind, ncols=ncols, ocr=fy < TEXT_FROM), p)
    if pg["note"]:
        lines = [ln for p in pg["note"] for ln in pls[p]]
        apply_fixtok(fy, lines)
        i0 = next((i for i, ln in enumerate(lines) if re.search(r"(?i)a\.\s+plan net position", " ".join(ln.text.split()))), 0)
        lines = lines[i0:]
        ia = next((i for i, ln in enumerate(lines) if re.search(r"(?i)b\.\s+changes in plan net position", " ".join(ln.text.split()))), len(lines))
        na, nb = lines[:ia], lines[ia:]
        out[("net_position", "note")] = (read_plan_table(na, "net_position", ocr=fy < TEXT_FROM), pg["note"][0])
        out[("changes", "note")] = (read_plan_table(nb, "changes", ocr=fy < TEXT_FROM), pg["note"][-1])
    return out, pg, pls


def validate_plan(fy, kind, source, rows, chk, label, ncols=3):
    """Identities within one print of a statement; rows' vals = [police, fire, total]."""
    k = f"npos|{kind}|{source}"
    byid = {}
    for sec, lab, lid, vals, pg, sub in rows:
        byid.setdefault(lid, []).append(vals)

    def one(lid):
        v = byid.get(lid)
        return v[0] if v else None
    # total column = police + fire on every line
    for sec, lab, lid, vals, pg, sub in rows:
        if len(vals) == 3 and source != "statement" and vals[0] is not None and vals[1] is not None and vals[2] is not None:
            chk.eq(f"{k}|total_col|{lid}|{lab[:30]}", vals[2], vals[0] + vals[1], f"{label}: {lab!r} total column = police + fire")
    for c, nm in enumerate(("police", "fire", "total") if source != "statement" else ("total",)):
        def g(lid, c=c):
            v = one(lid)
            return None if v is None or v[c] is None else v[c]
        if kind == "net_position":
            det = [r[3][c] or 0 for r in rows if r[0] == "assets" and r[2] not in ("total_investments", "total_receivables", "total_assets")]
            if g("total_assets") is not None:
                chk.eq(f"{k}|assets|{nm}", g("total_assets"), sum(det), f"{label}: assets sum to total assets ({nm})")
            for tid, sb in (("total_investments", "inv"), ("total_receivables", "rec")):
                if g(tid) is not None:
                    chk.eq(f"{k}|{tid}|{nm}", g(tid), sum(r[3][c] or 0 for r in rows if r[5] == sb and r[2] not in ("total_investments", "total_receivables")),
                           f"{label}: {tid} = lines ({nm})")
            lia = [r[3][c] or 0 for r in rows if r[0] == "liabilities" and r[2] not in ("total_liabilities", "net_position")]
            if g("total_liabilities") is not None:
                chk.eq(f"{k}|liabilities|{nm}", g("total_liabilities"), sum(lia), f"{label}: liabilities sum to total ({nm})")
            if g("total_assets") is not None and g("net_position") is not None:
                chk.eq(f"{k}|net_position|{nm}", g("net_position"), g("total_assets") - sum(lia), f"{label}: net position = assets - liabilities ({nm})")
        else:
            contrib = [r[3][c] or 0 for r in rows if r[2] in ("employer_contributions", "employee_contributions", "other_contributions")]
            if g("total_contributions") is not None:
                chk.eq(f"{k}|contributions|{nm}", g("total_contributions"), sum(contrib), f"{label}: contributions sum ({nm})")
            inc = [r[3][c] or 0 for r in rows if r[2] in ("fair_value_change", "interest")]
            ded = [r[3][c] or 0 for r in rows if r[0] == "deductions" and r[2] not in ("total_deductions", "net_change")]
            if g("total_investment_income") is not None:
                chk.eq(f"{k}|investment_income|{nm}", g("total_investment_income"), sum(inc), f"{label}: investment income sums ({nm})")
            if g("net_investment_income") is not None and g("total_investment_income") is not None:
                chk.eq(f"{k}|net_investment|{nm}", g("net_investment_income"), g("total_investment_income") + (g("investment_expense") or 0), f"{label}: net investment income ({nm})")
            if g("total_additions") is not None:
                chk.eq(f"{k}|additions|{nm}", g("total_additions"), (g("total_contributions") or 0) + (g("net_investment_income") or 0), f"{label}: additions ({nm})")
            if g("total_deductions") is not None:
                chk.eq(f"{k}|deductions|{nm}", g("total_deductions"), sum(ded), f"{label}: deductions sum ({nm})")
            if g("net_change") is not None and g("total_additions") is not None:
                chk.eq(f"{k}|net_change|{nm}", g("net_change"), g("total_additions") - (g("total_deductions") or 0), f"{label}: net change ({nm})")
            if g("net_position_beginning") is not None and g("net_position_ending") is not None and g("net_change") is not None:
                chk.eq(f"{k}|roll|{nm}", g("net_position_ending"), g("net_position_beginning") + g("net_change"), f"{label}: beginning + change = ending ({nm})")


STMT_IDS = ("total_assets", "total_liabilities", "net_position", "total_contributions", "total_investment_income", "net_investment_income", "total_additions",
            "total_deductions", "net_change", "net_position_beginning", "net_position_ending", "employer_contributions", "employee_contributions")
PLAIN = ("total_investments", "total_receivables", "total_assets", "total_liabilities", "accounts_payable", "net_position", "cash_and_short_term_investments",
         "prepaid_items", "total_contributions", "employer_contributions", "employee_contributions", "other_contributions", "total_investment_income",
         "fair_value_change", "interest", "investment_expense", "net_investment_income", "total_additions", "benefits_and_refunds", "administrative_expenses",
         "total_deductions", "net_change", "net_position_beginning", "net_position_ending", "refund_of_contributions")


def first_by_id(rows):
    res = {}
    for sec, lab, lid, vals, pg, sub in rows:
        res.setdefault(lid, vals)
    return res


def read_extract(fy, name):
    path = out_path(fy, name)
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def tie_plan_tables(fy, tables, chk):
    """Second prints of the same figures: note vs combining statement, basic statement vs combining statement, ending balance = net position,
    plan fiduciary net position in the pension note, employer contributions in the RSI / trend table."""
    comb = {k: first_by_id(tables[(k, "combining")][0]) for k in ("net_position", "changes") if (k, "combining") in tables}
    for kind in ("net_position", "changes"):
        if (kind, "combining") in tables and (kind, "note") in tables:
            a, b = comb[kind], first_by_id(tables[(kind, "note")][0])
            for lid in sorted(set(a) & set(b) & set(PLAIN)):
                for c, nm in enumerate(("police", "fire", "total")):
                    if a[lid][c] is not None and b[lid][c] is not None:
                        chk.eq(f"npos|tie_note|{kind}|{lid}|{nm}", b[lid][c], a[lid][c], f"note vs combining statement: {kind} {lid} ({nm})")
        if (kind, "combining") in tables and (kind, "statement") in tables:
            a, b = comb[kind], first_by_id(tables[(kind, "statement")][0])
            for lid in STMT_IDS:
                if lid in a and lid in b and b[lid][0] is not None and a[lid][2] is not None:
                    chk.eq(f"npos|tie_stmt|{kind}|{lid}", b[lid][0], a[lid][2], f"basic fiduciary statement vs combining statement (total column): {kind} {lid}")
    if "net_position" in comb and "changes" in comb and "net_position_ending" in comb["changes"] and "net_position" in comb["net_position"]:
        for c, nm in enumerate(("police", "fire", "total")):
            chk.eq(f"npos|tie_end|{nm}", comb["changes"]["net_position_ending"][c], comb["net_position"]["net_position"][c], f"ending net position (changes) = net position ({nm})")
    # plan fiduciary net position in the pension notes (GASB 67/68, FY2016+): market value, so it equals the combining statement
    if "net_position" in comb:
        for r in read_extract(fy, "pension_rollforward"):
            if r["source"] == "note" and r["component"] == "fnp" and r["line_id"] == "ending" and r["plan_id"] in ("police", "fire"):
                c = 0 if r["plan_id"] == "police" else 1
                chk.eq(f"npos|tie_fnp|{r['plan_id']}", int(float(r["amount"])), comb["net_position"]["net_position"][c], f"{r['plan_id']}: fiduciary net position in the pension note = combining statement")
    if "changes" in comb:
        contrib = {}
        for r in read_extract(fy, "pension_summary"):
            if r["measure"] in ("employer_contribution_actual", "trend_employer_contribution") and r["plan_id"] in ("police", "fire") and r["period_label"] == str(fy) and r["value"] != "":
                contrib[r["plan_id"]] = int(float(r["value"]))
        for plan, c in (("police", 0), ("fire", 1)):
            if plan in contrib and "employer_contributions" in comb["changes"]:
                chk.eq(f"npos|tie_contrib|{plan}", contrib[plan], comb["changes"]["employer_contributions"][c], f"{plan}: employer contributions in the RSI / trend table = combining statement")


def plan_rows_out(fy, tables):
    rows = []
    for (kind, src), (trs, pg) in tables.items():
        for sec, lab, lid, vals, p, sub in trs:
            if src == "statement":
                if lid not in STMT_IDS:
                    continue
                cols = [("total", vals[0])]
            else:
                cols = list(zip(PLANS, vals))
            for plan, v in cols:
                if v is None:
                    continue
                flag = "sign_normalized" if lid == "investment_expense" and src == "combining" and fy == 2012 else ""
                rows.append([fy, doc(fy), p, kind, src, plan, sec, lab, lid, v, flag])
    return rows


# =============================================================================================================
# B. Target investment allocation (police, fire pension plans; IMRF) - text layer years only
# =============================================================================================================
ALLOC_ROW = re.compile(r"^\s*(?P<label>[A-Za-z][^%]*?[A-Za-z/)])\s+(?:(?P<range>\d+%\s*-\s*\d+%)\s+)?(?P<target>\d+(?:\.\d+)?)%"
                       r"(?:\s+(?P<ret>N/A|\d+(?:\.\d+)?%(?:\s+to\s+\d+(?:\.\d+)?%)?))?\s*$")
ALLOC_END = re.compile(r"^\s*(The |Best |Although |In |Securities |Based |\d+\.\s)")
PLAN_HEAD = [(re.compile(r"^\s{0,12}Police Pension Plan( \(Continued\))?\s*$"), "police"), (re.compile(r"^\s{0,12}Firefighters.? Pension Plan( \(Continued\))?\s*$"), "fire"),
             (re.compile(r"^\s{0,12}Illinois Municipal Retirement Fund"), "imrf")]


def read_allocations(fy):
    """FY2016+: every 'target allocation' table in the pension notes -> rows (plan, class, range, target, return, return_text, page) + the stated overall mix."""
    texts = page_texts(fy)
    rows, mixes = [], []
    plan = ""
    for pno, t in enumerate(texts, 1):
        if is_toc(t):
            continue
        lines = t.split("\n")
        for i, ln in enumerate(lines):
            for pat, pl in PLAN_HEAD:
                if pat.match(ln):
                    plan = pl
            if not re.search(r"(?i)target allocation (across asset classes|and best estimates)", ln):
                continue
            tbl = []
            for ln2 in lines[i + 1:] + (texts[pno].split("\n") if pno < len(texts) else []):
                if not ln2.strip():
                    if tbl:
                        # blank lines separate rows of the printed table; keep going until a sentence starts
                        pass
                    continue
                if re.match(r"^\s*TOTAL\s+100", ln2):
                    tbl.append(("TOTAL", "", 100.0, None, ""))
                    break
                if tbl and ALLOC_END.match(ln2):
                    break
                m = ALLOC_ROW.match(ln2)
                if m:
                    ret = m.group("ret") or ""
                    rv = float(ret.rstrip("%")) if re.fullmatch(r"\d+(\.\d+)?%", ret) else None
                    tbl.append((" ".join(m.group("label").split()), (m.group("range") or "").replace(" ", ""), float(m.group("target")), rv, ret))
                elif tbl and not re.search(r"\d", ln2):
                    # a wrapped label ('Funds') continues the previous row
                    tbl[-1] = (tbl[-1][0] + " " + ln2.strip(), *tbl[-1][1:])
                elif tbl and re.search(r"\d", ln2) and not ALLOC_END.match(ln2):
                    break
            who = plan
            for r in tbl:
                rows.append((who, pno, *r))
            # the overall mix sentence below the table
            tail = " ".join(" ".join(lines[i:]).split())
            m = re.search(r"approximately (\d+)% invested in fixed income securities and (\d+)% invested in equity", tail)
            if m:
                mixes.append((who, pno, int(m.group(1)), int(m.group(2))))
    return rows, mixes


def validate_allocations(fy, rows, mixes, chk):
    plans = sorted({r[0] for r in rows})
    for pl in plans:
        pr = [r for r in rows if r[0] == pl]
        items = [r for r in pr if r[2] != "TOTAL"]
        tot = round(sum(r[4] for r in items), 2)
        chk.near(f"alloc|{pl}|sum", 100.0, tot, 0.005, f"{pl}: target allocations sum to 100%")
        for (w, pg, fixed, eq) in mixes:
            if w == pl:
                equity = round(sum(r[4] for r in items if re.search(r"(?i)stock|equity", r[2])), 2)
                chk.near(f"alloc|{pl}|mix", eq, equity, 0.5, f"{pl}: equity classes = the stated overall equity share")


# =============================================================================================================
# C. FY2012-FY2015 schedules of funding progress and employer contributions (required supplementary information)
# =============================================================================================================
RSI_PLANS = [(r"(?i)municipal retirement fund\s*\(?slep|sheriff", "imrf_slep"), (r"(?i)illinois municipal retirement fund|municipal retirement", "imrf_regular"),
             (r"(?i)police pension", "police"), (r"(?i)firefighters", "fire"), (r"(?i)post.?employment benefit", "opeb_city")]


def plan_of_rsi_page(text):
    head = " ".join(text.split())[:420]
    for pat, pl in RSI_PLANS:
        if re.search(pat, head):
            return pl
    return ""


POINT_FIXES = []
POINT_FLAGGED = set()


def num_tok(tok):
    t = tok.strip().replace("$", "").replace("%", "")
    if re.fullmatch(r"\d{1,3}\.\d{3}", t) or re.fullmatch(r"\d{1,3}\.\d{3}\.\d{3}", t):
        t = t.replace(".", ",")          # OCR read the thousands comma as a point (424.583); the funding identities confirm it
        POINT_FIXES.append(tok)
    t = t.replace(",", "")
    if t in ("N/A", "NA"):
        return "na"
    if t in ("-", "—", "–", "=", "~"):
        return 0
    neg = t.startswith("(") or t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t) if "." in t else int(t)
    except ValueError:
        return None
    return -v if neg else v


def read_funding_progress(fy):
    """-> (progress rows, contribution rows, pages).  Each row carries its plan and page."""
    texts = page_texts(fy)
    prog, contrib, pages = [], [], []
    for pno, t in enumerate(texts, 1):
        if pno < 60 or is_toc(t) or not re.search(r"(?i)schedule of funding progress", t) or "Required Supplementary" not in t.replace("REQUIRED SUPPLEMENTARY", "Required Supplementary"):
            continue
        plan = plan_of_rsi_page(t)
        if not plan:
            continue
        in_contrib = False
        before = len(prog) + len(contrib)
        for ln in page_lines(fy, [pno], "ocr", "gray")[pno]:
            clean_ocr_line(ln)
            s = " ".join(ln.text.split())
            if re.match(r"(?i)employer contributions", s):
                in_contrib = True
                continue
            m = re.match(r"^(?:(\d{2})/(\d{2})/(\d{2})|(20\d\d))\s+(.*)$", s)
            if not m:
                continue
            if m.group(4):
                year = int(m.group(4))
            else:
                year = 2000 + int(m.group(3))
            rest = m.group(5).replace("$", " ").replace(" %", "%")
            raw = rest.split()
            n_fix = len(POINT_FIXES)
            if plan != "opeb_city":
                raw = [x for x in raw if x not in ("-", "—", "–", "=", "~")]      # a stray rule mark between values
            toks = [num_tok(x) for x in raw]
            toks = [x for x in toks if x is not None]
            md = "12-31" if plan.startswith("imrf") else "04-30"
            if in_contrib:
                if len(toks) == 3 and "na" not in toks:
                    contrib.append((plan, year, toks[0], toks[1], toks[2], pno))
                continue
            if len(toks) >= 6 and "na" not in toks:
                toks = toks[:6]
                if fy == 2012:                 # FY2012's order: assets, AAL, UAAL, funded ratio, payroll, UAAL %
                    toks = [toks[0], toks[1], toks[3], toks[2], toks[4], toks[5]]
                prog.append((plan, f"{year}-{md}", *toks, pno))
                if len(POINT_FIXES) > n_fix:
                    POINT_FLAGGED.add((plan, f"{year}-{md}"))
        if len(prog) + len(contrib) > before:
            pages.append(pno)
    return prog, contrib, pages


def validate_funding(fy, prog, contrib, chk):
    for (plan, vd, ava, aal, ratio, uaal, pay, pct, pg) in prog:
        k = f"fund|{plan}|{vd[:4]}"
        chk.eq(f"{k}|uaal", uaal, aal - ava, f"{plan} {vd}: UAAL = AAL - actuarial value of assets")
        if ratio is not None and aal:
            chk.near(f"{k}|ratio", ratio, ava / aal * 100, 0.011, f"{plan} {vd}: funded ratio = assets / AAL")
        if pay:
            chk.near(f"{k}|pct", pct, uaal / pay * 100, 0.011, f"{plan} {vd}: UAAL / covered payroll")
    for (plan, y, c, arc, pct, pg) in contrib:
        if arc:
            chk.near(f"contrib|{plan}|{y}|pct", pct, c / arc * 100, 0.011, f"{plan} FY{y}: contributions / required = percent contributed")


# =============================================================================================================
# D. FY2012-FY2015: Statement of Net Position (liabilities) and reconciliation of governmental funds against the debt table
# =============================================================================================================
SNP_HEADERS = re.compile(r"(?i)^(current|noncurrent) liabilities:?$|^liabilities:?$|^deferred inflows of resources$|^net position$|^component$|^unit$|^governmental$|"
                         r"^activities$|^business.type$|^ida public$|^library$|^totals?$|^city of belvidere|^statement of net|^april|^the notes to|^noncurrent liabilities")
SNP_IDS = [(r"compensated absences", "compensated_absences"), (r"net pension obligation|pension obligation", "net_pension_obligation"), (r"post.?employment", "opeb"),
           (r"mortgage", "mortgage_loan"), (r"revolving", "revolving_loan"), (r"debt certificates", "debt_certificates"), (r"tax increment", "tif_note"),
           (r"landfill", "landfill_postclosure"), (r"capital leases?|lease payable", "capital_lease"), (r"iepa", "revolving_loan"),
           (r"revenue bonds|alternate revenue", "revenue_bonds"), (r"premium", "bond_premium")]
REC_IDS_OLD = [(r"debt certificates?", "debt_certificates"), (r"landfill", "landfill_postclosure"), (r"pension", "net_pension_obligation"), (r"post.?employment", "opeb"),
               (r"compensated absences", "compensated_absences"), (r"revolving", "revolving_loan"), (r"tax increment", "tif_note")]


def old_pages(fy, head_pat, tail_pat=None, first=1, last=60):
    texts = page_texts(fy)
    return [i for i, t in enumerate(texts, 1) if first <= i <= last and re.search(head_pat, squash(t), re.I) and (tail_pat is None or re.search(tail_pat, squash(t), re.I))]


def read_snp_liabilities(fy):
    """FY2013-15: liability lines of the Statement of Net Position -> [(label, [gov, bt, total, library], page)], plus the page."""
    pgs = old_pages(fy, r"current portion of long-term liabilities", r"total liabilities")
    if not pgs:
        return [], None
    p = pgs[0]
    lines = page_lines(fy, [p], "ocr", "gray")[p]
    for ln in lines:
        clean_ocr_line(ln)
    tl = next((ln for ln in lines if re.match(r"(?i)^total liabilities$", ln.split()[0].strip()) and len(ln.split()[1]) >= 3), None)
    if tl is None:
        return [], p
    anchors = [x[0] for x in tl.split()[1]]
    if len(anchors) == 3:
        anchors = anchors + [anchors[-1] + (anchors[-1] - anchors[-2])]
    rows, pend = [], []
    for ln in lines:
        label, nums = ln.split()
        if not nums:
            if label and not SNP_HEADERS.match(label.strip()):
                pend.append(label)
            else:
                pend = []
            continue
        cols = assign_columns(nums, anchors, tol=30)
        vals = [cols[c][0] if c in cols else 0 for c in range(len(anchors))]
        rows.append((" ".join(pend + [label]).strip(), vals, ln.page))
        pend = []
    return rows, p


def debt_rows(fy):
    """FY20XX_audit_debt_balances.csv -> {(activity, line_id): {measure: amount}} (blank line ids are kept under '')"""
    res = {}
    for r in read_extract(fy, "debt_balances"):
        if r["amount"] in ("", None):
            continue
        res.setdefault((r["activity"], r["line_id"]), {})[r["measure"]] = int(float(r["amount"]))
    return res


def snp_crosschecks(fy, chk):
    """Statement of Net Position and reconciliation of governmental funds against the debt table (the debt note), FY2012-FY2015."""
    debt = debt_rows(fy)
    out = []

    def end(a, lid, m="ending"):
        d = debt.get((a, lid))
        return None if d is None else d.get(m)
    if fy >= 2013:
        rows, pg = read_snp_liabilities(fy)
        cols = (("governmental", 0), ("business_type", 1))
        acc = {}
        for label, vals, p in rows:
            if re.match(r"(?i)^current portion of long-term", label):
                for a, c in cols:
                    t = end(a, "total", "current_portion")
                    if t is not None:
                        chk.eq(f"snp|{a}|current_portion", vals[c], t, f"{a}: Statement of Net Position current portion of long-term liabilities = debt table total current portion")
                continue
            if re.match(r"(?i)^total noncurrent liabilities", label):
                for a, c in cols:
                    e, cu = end(a, "total"), end(a, "total", "current_portion")
                    if e is not None and cu is not None:
                        chk.eq(f"snp|{a}|noncurrent", vals[c], e - cu, f"{a}: Statement of Net Position total noncurrent liabilities = debt table total ending - current portion")
                continue
            if re.match(r"(?i)^total liabilities", label):
                break
            lid = next((i for pat, i in SNP_IDS if re.search(pat, label, re.I)), None)
            if not lid or re.match(r"(?i)^total", label):
                continue
            for a, c in cols:       # lines that map to one debt line (Revolving Loans + IEPA Loans) are added up
                acc[(a, lid)] = acc.get((a, lid), 0) + vals[c]
        for (a, lid), v in sorted(acc.items()):
            e = end(a, lid)
            cu = end(a, lid, "current_portion") or 0
            if e is None:
                if v:
                    chk.fail(f"snp|{a}|{lid}", f"{a}: Statement of Net Position {lid} line ({v:,}) has no '{lid}' line in the debt table")
                continue
            if lid == "bond_premium":
                continue
            chk.eq(f"snp|{a}|{lid}", v, e - cu, f"{a}: Statement of Net Position {lid} lines = debt table ending - current portion")
    else:
        # FY2012 (Statement of Net Assets, a different presentation): the labelled single lines only
        pgs = old_pages(fy, r"statement of net assets \(continued\)", r"total liabilities")
        if pgs:
            lines = page_lines(fy, [pgs[0]], "ocr", "gray")[pgs[0]]
            for ln in lines:
                clean_ocr_line(ln)
            for ln in lines:
                label, nums = ln.split()
                if len(nums) >= 3 and re.match(r"(?i)^compensated absences$", label.strip()):
                    chk.eq("snp|governmental|compensated_absences", nums[0][1], end("governmental", "compensated_absences") or -1, "governmental: Statement of Net Assets compensated absences = debt table")
                    chk.eq("snp|business_type|compensated_absences", nums[1][1], end("business_type", "compensated_absences") or -1, "business-type: Statement of Net Assets compensated absences = debt table")
                if len(nums) >= 3 and re.match(r"(?i)^net pension obligation$", label.strip()):
                    chk.eq("snp|governmental|net_pension_obligation", nums[0][1], end("governmental", "net_pension_obligation") or -1, "governmental: Statement of Net Assets net pension obligation = debt table")
    # the debt table's pension and OPEB lines against the notes
    npo = {}
    for r in read_extract(fy, "pension_rollforward"):
        if r["source"] == "note_gasb27" and r["line_id"] == "npo_ending" and r["plan_id"] in ("police", "fire", "imrf_regular", "imrf_slep"):
            npo[r["plan_id"]] = int(float(r["amount"]))
    if npo and end("governmental", "net_pension_obligation") is not None:
        chk.eq("snp|note|net_pension_obligation", end("governmental", "net_pension_obligation"), sum(npo.values()), "debt table net pension obligation = police + fire (+ IMRF, SLEP) net pension obligation in the pension note")
    noo = {r["line_id"]: int(float(r["amount"])) for r in read_extract(fy, "opeb_rollforward") if r["source"] == "note_gasb45" and r["line_id"] in ("noo_ending", "noo_city_portion")}
    if noo:
        want = noo.get("noo_city_portion", noo.get("noo_ending"))
        have = end("governmental", "opeb")
        if have is None:
            have = next((v.get("ending") for (a, i), v in debt.items() if a == "governmental" and i == "" and v.get("ending") == want), None)
        if have is not None:
            chk.eq("snp|note|opeb", have, want, "debt table other post-employment benefits = net OPEB obligation in the OPEB note (the city's share)")
    # reconciliation of governmental funds to governmental activities: each long-term liability line = the debt table's governmental ending
    rp = old_pages(fy, r"reconciliation of (total )?(governmental )?fund balances? (of governmental funds )?to", r"net (position|assets) of governmental activities", last=45)
    if rp:
        lines = page_lines(fy, [rp[0]], "ocr", "gray")[rp[0]]
        on, pend = False, []
        for ln in lines:
            clean_ocr_line(ln)
            t = " ".join(ln.text.split())
            if re.search(r"(?i)liabilities consist of|long-term liabilities.*(not due|including)", t) or re.search(r"(?i)^in the funds:?$|reported in the funds", t) and not on and False:
                on = True
                pend = []
                continue
            if re.search(r"(?i)^net (position|assets) of governmental activities", t):
                break
            if not on:
                continue
            label, nums = ln.split()
            if not nums:
                if label and not re.match(r"(?i)^(payable in the current|in the funds|the use of|as liabilities)", label):
                    pend.append(label)
                continue
            full = " ".join(pend + [label]).strip()
            pend = []
            if re.search(r"(?i)accrued interest", full):
                continue
            lid = next((i for pat, i in REC_IDS_OLD if re.search(pat, full, re.I)), None)
            if not lid:
                continue
            amt = abs(nums[0][1])
            e = end("governmental", lid)
            if e is None:         # the FY2012 debt table has no id on the post-employment benefits line: match its amount among the unlabeled lines
                e = next((v.get("ending") for (a, i), v in debt.items() if a == "governmental" and i == "" and v.get("ending") == amt), None)
            if e is None:
                chk.fail(f"snp|reconciliation|{lid}", f"reconciliation line {full!r} ({amt:,}) has no '{lid}' line in the governmental debt table")
                continue
            chk.eq(f"snp|reconciliation|{lid}", amt, e, f"reconciliation of governmental funds: {full[-40:]!r} = governmental debt table ending")
    return out


# =============================================================================================================
# E. FY2012 debt service to maturity (two multi-instrument tables with five-year bands, page 54)
# =============================================================================================================
SCHED_TABLES = [
    # (activity, [(instrument, balance line id)], columns: one principal + interest pair per instrument, then optional total column, total column kind)
    ("governmental", [("G.O. Debt Certificates", "debt_certificates"), ("Landfill Post Closure Care Cost", "landfill_postclosure"), ("Revolving Loan Fund - City", "revolving_loan")], "principal"),
    ("business_type", [("Alternate Revenue Source Series 2003", "revenue_bonds"), ("Refunding Bonds Series 2003", ""), ("Revenue Bonds Series 2004", "")], None),
    ("business_type", [("Capital Lease Payable", "capital_lease"), ("IEPA Wastewater Treatment Works Loan", "revolving_loan")], "payments"),
]


def debt_service_2012(pages, act_rows, chk, fy=2012):
    """Read the FY2012 debt service tables from the (already cleaned) OCR lines of the notes.  Returns debt_service rows in the per-year layout:
    [fy, doc, page, activity, group_idx, instrument, year_label, principal, interest, total, row_kind, line_id, flag]."""
    page = next((p for p, ls in sorted(pages.items()) if any(re.search(r"(?i)annual debt service requirements", l.text) for l in ls)), None)
    if page is None:
        chk.fail("debt|sched|2012|page", "FY2012 debt service requirements page not found")
        return []
    lines = pages[page]
    tables, cur = [], None
    fixlines = {o["key"]: o["value"] for o in load_overrides(fy) if o["kind"] == "fixline"}
    for ln in lines:
        t = " ".join(ln.text.split())
        t = fixlines.get(f"{page}|{t}", t)
        if re.match(r"(?i)^governmental activities", t):
            cur = dict(idx=0, rows=[], total=None)
            tables.append(cur)
        elif re.match(r"(?i)^business-?type activities", t):
            cur = dict(idx=1, rows=[], total=None)
            tables.append(cur)
        elif cur is not None and cur["idx"] == 1 and cur["total"] is not None and re.search(r"(?i)capital lease", t):
            cur = dict(idx=2, rows=[], total=None)
            tables.append(cur)
        if cur is None:
            continue
        toks = [x for x in t.replace("$", " ").split() if x not in ("-", "—") or True]
        m = re.match(r"^(20\d\d)(?:\s*-?\s*(20\d\d))?\s*(.*)$", t.replace("$", " "))
        if m:
            label = m.group(1) if not m.group(2) else f"{m.group(1)}-{m.group(2)}"
            vals = [num_tok(x) for x in m.group(3).split()]
            cur["rows"].append((label, vals, ln.page))
        elif re.match(r"(?i)^total\b", t) or (cur["idx"] == 2 and re.match(r"^\$?\s*[\d,]+\s+[\d,]+", t) and cur["rows"] and len(cur["rows"]) >= 7):
            vals = [num_tok(x) for x in re.sub(r"(?i)^total", "", t).replace("$", " ").split()]
            if cur["total"] is None and len(vals) >= 4:
                cur["total"] = vals
    out = []
    ends = [(r[0], r[2][3]) for r in act_rows]       # (activity, ending balance) of every line of the liability table
    gi = 0
    for tb in tables:
        spec = SCHED_TABLES[tb["idx"]]
        activity, insts, total_kind = spec
        n_pairs = len(insts)
        ncols = 2 * n_pairs + (1 if total_kind else 0)
        good = [r for r in tb["rows"] if r[1] and None not in r[1] and len(r[1]) == ncols]
        k = f"debt|sched|2012|t{tb['idx']}"
        if len(good) != 7 or tb["total"] is None or len(tb["total"]) != ncols:
            chk.fail(f"{k}|shape", f"FY2012 debt service table {tb['idx']}: expected 7 rows of {ncols} values and a total row; got {len(good)} rows / total {tb['total']}")
            continue
        tot = tb["total"]
        for c in range(ncols):
            chk.eq(f"{k}|col{c}", tot[c], sum(r[1][c] for r in good), f"FY2012 debt service table {tb['idx']} column {c}: years sum to the printed total")
        if total_kind == "principal":
            for ri, (lab, vals, pg) in enumerate(good):
                chk.eq(f"{k}|row{lab}", vals[-1], sum(vals[0:2 * n_pairs:2]), f"FY2012 governmental {lab}: total principal payments = sum of the principal columns")
        for g, (inst, lid) in enumerate(insts):
            pt = tot[2 * g]
            matches = [v for (a, v) in ends if a == activity and v == pt] or sorted((v for (a, v) in ends if a == activity and abs(v - pt) <= 5), key=lambda v: abs(v - pt))
            if not matches:
                chk.fail(f"{k}|tie{g}", f"FY2012 {inst}: principal total {pt:,} equals no ending balance of the liability table")
            else:
                chk.eq(f"{k}|tie{g}", pt, matches[0], f"FY2012 {inst}: principal total = outstanding balance in the liability table")
            for lab, vals, pg in good:
                kind = "band" if "-" in lab else "year"       # '2018-2022': a five-year band, not a single fiscal year
                out.append([fy, f"FY{fy}_audit", pg, activity, gi, inst, lab, vals[2 * g], vals[2 * g + 1], vals[2 * g] + vals[2 * g + 1], kind, lid, ""])
            out.append([fy, f"FY{fy}_audit", good[0][2], activity, gi, inst, "TOTAL", pt, tot[2 * g + 1], pt + tot[2 * g + 1], "total", lid, ""])
            gi += 1
        if total_kind == "payments":
            # total payments of the business-type activities = every instrument's principal + interest (both tables)
            prev = next((t_ for t_ in tables if t_["idx"] == 1), None)
            if prev and prev["total"] is not None:
                pg_rows = {r[0]: r[1] for r in tb["rows"] if r[1] and len(r[1]) == ncols}
                pv_rows = {r[0]: r[1] for r in prev["rows"] if r[1] and len(r[1]) == 6}
                for lab, vals, pg in good:
                    if lab in pv_rows:
                        chk.eq(f"{k}|payments{lab}", vals[-1], sum(vals[:-1]) + sum(pv_rows[lab]), f"FY2012 business-type {lab}: total payments = principal and interest of all instruments")
    return out


def old_line_id_for(label):
    import audit_debt_pension_old as OLD
    return OLD.old_line_id(label) if label else ""


# =============================================================================================================
# per-year run and consolidation
# =============================================================================================================
def tie_funding(fy, prog, contrib, chk):
    """The schedules of funding progress against the notes' funded status (latest valuation) and trend table (employer contributions)."""
    summ = read_extract(fy, "pension_summary")
    for plan in ("imrf_regular", "imrf_slep", "police", "fire"):
        mine = [r for r in prog if r[0] == plan]
        if not mine:
            continue
        latest = max(mine, key=lambda r: r[1])
        for meas, idx, sign in (("ava", 2, 1), ("aal", 3, 1), ("uaal_signed", 5, -1), ("covered_payroll", 6, 1)):
            for r in summ:
                if r["plan_id"] == plan and r["measure"] == meas and r["value"] != "":
                    chk.eq(f"fund|note|{plan}|{meas}", latest[idx], sign * int(float(r["value"])), f"{plan}: schedule of funding progress (latest valuation) = funded status in the note: {meas}")
    for (plan, y, c, arc, pct, pg) in contrib:
        for r in summ:
            if r["plan_id"] == plan and r["measure"] == "trend_employer_contribution" and r["period_label"] == str(y) and r["value"] != "":
                chk.eq(f"contrib|note|{plan}|{y}", c, int(float(r["value"])), f"{plan} FY{y}: schedule of employer contributions = trend table in the note")


def run_year(fy, write=True, quiet=False):
    chk = Checker(gap_allow(fy))
    T = {k: [] for k in ("plan_net_position", "investment_allocation", "funding_progress", "employer_contributions")}
    d = doc(fy)
    # A. plan net position
    tables, pg, pls = collect_plan_tables(fy)
    for (kind, src), (rows, p) in tables.items():
        if src != "statement":
            validate_plan(fy, kind, src, rows, chk, f"{kind}/{src}")
    tie_plan_tables(fy, tables, chk)
    T["plan_net_position"] = plan_rows_out(fy, tables)
    # B. allocation
    if fy >= TEXT_FROM:
        arows, mixes = read_allocations(fy)
        validate_allocations(fy, arows, mixes, chk)
        for (plan, p, label, rng, target, rv, rtext) in arows:
            if label == "TOTAL":
                continue
            T["investment_allocation"].append([fy, d, p, plan, label, rng, target, "" if rv is None else rv, rtext, ""])
    # C. funding progress, FY2012-15
    if fy < TEXT_FROM:
        prog, contrib, fpages = read_funding_progress(fy)
        validate_funding(fy, prog, contrib, chk)
        tie_funding(fy, prog, contrib, chk)
        for (plan, vd, ava, aal, ratio, uaal, pay, pct, p) in prog:
            blank = plan == "opeb_city" and not pay
            flag = "payroll_not_printed" if blank else "ocr_point_comma" if (plan, vd) in POINT_FLAGGED else ""
            T["funding_progress"].append([fy, d, p, plan, vd, ava, aal, ratio, uaal, "" if blank else pay, "" if blank else pct, flag])
        for (plan, y, c, arc, pct, p) in contrib:
            T["employer_contributions"].append([fy, d, p, plan, y, c, arc, pct, ""])
        snp_crosschecks(fy, chk)
    if write:
        os.makedirs(OUT_DIR, exist_ok=True)
        write_csv(out_path(fy, "pension_plan_net_position"), PLAN_TABLE_COLUMNS, T["plan_net_position"])
        write_csv(out_path(fy, "pension_investment_allocation"), ALLOC_COLUMNS, T["investment_allocation"])
        write_csv(out_path(fy, "pension_funding_progress"), FUNDING_COLUMNS, T["funding_progress"])
        write_csv(out_path(fy, "pension_employer_contributions"), CONTRIB_COLUMNS, T["employer_contributions"])
        crow = []
        for t in sorted(set(chk.n_pass) | set(chk.n_fail)):
            acc = sum(1 for a in chk.accepted if a["key"].split("|")[0] == t)
            crow.append([fy, t, chk.n_pass.get(t, 0), acc, chk.n_fail.get(t, 0)])
        write_csv(out_path(fy, "pension_gaps_checks"), ["fiscal_year", "check_group", "passed", "accepted_exceptions", "failed"], crow)
    if not quiet:
        print(f"FY{fy}: plan net position {len(T['plan_net_position'])} cells, allocation {len(T['investment_allocation'])}, funding progress {len(T['funding_progress'])}, "
              f"contributions {len(T['employer_contributions'])}; checks passed {sum(chk.n_pass.values())}, accepted {len(chk.accepted)}, FAILED {len(chk.problems)}")
        for a in chk.accepted:
            print(f"  accepted [{a['key']}] {a['desc']} (diff {a['diff']})")
        for p in chk.problems:
            print("  !", p)
        for k in sorted(set(chk.allow) - chk.used):
            print("  ! unused allow override:", k)
    return T, chk


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--build" in sys.argv:
        import audit_pension_gaps_build as B
        B.main()
        return
    fy = int(args[0].replace("FY", ""))
    T, chk = run_year(fy)
    sys.exit(1 if chk.problems or set(chk.allow) - chk.used else 0)


if __name__ == "__main__":
    main()
