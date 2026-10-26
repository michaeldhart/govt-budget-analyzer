"""FY2012-FY2015 audits (pre GASB 68 / 75): scanned or poor text layer, so everything is read from tesseract word boxes (OCR, cached).

Debt: long-term liability activity, bond / note issue tables, debt service to maturity.
Pensions (GASB 27): contribution rates, annual pension cost and net pension obligation (ARC, interest, adjustment, APC, contributions, NPO), trend
information, funded status (AAL, AVA, UAAL, covered payroll), membership.   OPEB (GASB 45): net OPEB obligation, funded status, trend.

Each table is validated by its printed identities; eyeballed fixes are `fixtok` overrides (page, wrong token -> right token) in
data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv.
"""
import os
import re
import statistics

from audit_debt_pension_common import (Checker, Line, Word, assign_columns, classify, cluster_lines, load_allow, load_overrides, ocr_words, OUT_DIR)
import audit_debt_pension_debt as D

RANGES = {2012: (50, 84), 2013: (58, 92), 2014: (60, 90), 2015: (56, 88)}
DOC = lambda fy: f"FY{fy}_audit"


def clean_word(t):
    t = t.strip()
    t = t.replace("§", "$").replace("—", "-").replace("–", "-")
    if re.fullmatch(r"[.:,|_~=!*]+", t):
        return ""
    if re.fullmatch(r"\$?_*", t):
        return "$" if "$" in t else ""
    t = t.strip("|_~:;")
    if re.fullmatch(r"[=~]\d[\d,]*", t):
        t = t[1:]
    # thousands separators read as periods: 2.038.315 / 680.098
    if re.fullmatch(r"\(?\$?\d{1,3}([.,]\d{3})+\)?", t) and "." in t:
        t = t.replace(".", ",")
    return t


def old_lines(fy, page, overrides, ytol=None):
    words = []
    for w in ocr_words(fy, page, "gray"):
        t = clean_word(w.t)
        for o in overrides:
            if o["kind"] == "fixtok" and o["key"] == f"{page}|{t}":
                t = o["value"]
        if t:
            w.t = t
            words.append(w)
    # merge "866 224" split by OCR: a 1-3 digit token followed closely by a 3-digit token
    words.sort(key=lambda w: ((w.y0 + w.y1) / 2 // 4, w.x0))
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if i + 1 < len(words):
            n = words[i + 1]
            same = abs((w.y0 + w.y1) / 2 - (n.y0 + n.y1) / 2) < 3
            if same and re.fullmatch(r"\$?\d{1,3}", w.t) and re.fullmatch(r"\d{3}", n.t) and 0 <= n.x0 - w.x1 < 4:
                out.append(Word(w.x0, n.x1, w.y0, n.y1, w.t + "," + n.t))
                i += 2
                continue
        out.append(w)
        i += 1
    return cluster_lines(out, page, ytol)


def all_pages(fy, overrides):
    """OCR'd pages of the notes (pages whose header says 'Notes to the Financial Statements'); RSI schedules are excluded."""
    a, b = RANGES[fy]
    out = {}
    for p in range(a, b + 1):
        ls = old_lines(fy, p, overrides)
        if any(re.search(r"(?i)notes to (the )?financial statements", l.text) for l in ls[:8]):
            out[p] = ls
    return out


def tail_numbers(ln, n=None):
    label, nums = ln.split()
    return label, nums


# ------------------------------------------------------------------------------------------------------
def find_page_lines(pages, start_pat, stop_pat=None, first_only=True):
    """Concatenated lines (all pages) from the first line matching start_pat to the line matching stop_pat."""
    out, on = [], False
    for p in sorted(pages):
        for ln in pages[p]:
            if not on and re.search(start_pat, ln.text, re.I):
                on = True
            if on:
                if stop_pat and out and re.search(stop_pat, ln.text, re.I):
                    return out
                out.append(ln)
    return out


def read_activity(pages):
    """Long-term liability activity: [(activity, label, [vals], page)].  Subtotal rows are unlabeled in FY2013-15 and 'TOTAL' in FY2012."""
    if 2012 in {int(pages[p][0].page and 0) or 0 for p in pages} or False:
        pass
    pages = {p: v for p, v in pages.items() if any(re.search(r"(?i)long-term debt", l.text) for l in v[:8])}
    lines = find_page_lines(pages, r"changes in long-term liabilities during|GOVERNMENTAL ACTIVITIES\s+Beginning", r"liquidated by|outstanding debt as of")
    rows, act, pend, ncol = [], None, [], 4
    for ln in lines:
        t = ln.text
        if re.search(r"(?i)one year|due within", t):
            ncol = 5
        if re.match(r"(?i)^governmental activities", t):
            act = "governmental"
            t2 = re.sub(r"(?i)^governmental activities\s*", "", t)
            if not ln.split()[1]:
                pend = []
                continue
        if re.match(r"(?i)^business-?type activities", t):
            act = "business_type"
            if not ln.split()[1]:
                pend = []
                continue
        label, nums = ln.split()
        if len(nums) >= 3 and act and not re.search(r"(?i)balances|additions|deductions|reductions|beginning", t):
            rows.append([act, " ".join(pend + [label]).strip(), nums, ln.page])
            pend = []
        elif label and not re.search(r"(?i)^(city of belvidere|notes to|april 30|note \d|long-term debt|changes in|type of debt|amounts|beginning|balance|governmental|business|\d+\.)", label):
            pend.append(label)
    # anchors from rows that carry all columns
    full = [r for r in rows if len(r[2]) == ncol]
    out = []
    if full:
        anchors = [statistics.median(r[2][c][0] for r in full) for c in range(ncol)]
        for r in rows:
            if len(r[2]) == ncol:
                vals = [x[1] for x in r[2]]
            else:
                cols = assign_columns(r[2], anchors, tol=25)
                vals = [cols[c][0] if c in cols else 0 for c in range(ncol)]
            out.append((r[0], r[1], vals, r[3]))
    return out, ncol


def validate_activity(rows, ncol, chk):
    for act in ("governmental", "business_type"):
        rr = [r for r in rows if r[0] == act]
        lines = [r for r in rr if r[1] and not re.match(r"(?i)^total", r[1])]
        tot = next((r for r in rr if not r[1] or re.match(r"(?i)^total", r[1])), None)
        for r in lines:
            v = r[2]
            chk.eq(f"debt|{act}|{r[1]}|rollforward", v[3], v[0] + v[1] - v[2], f"{act} {r[1]!r}: beginning + additions - reductions = ending")
        if tot is None:
            chk.fail(f"debt|{act}|TOTAL", "no subtotal row")
            continue
        for c, nm in enumerate(("beginning", "additions", "reductions", "ending", "current")[:ncol]):
            chk.eq(f"debt|{act}|TOTAL|{nm}", tot[2][c], sum(r[2][c] for r in lines), f"{act} subtotal {nm}: sum of lines")


def old_line_id(label):
    if re.search(r"(?i)net pension obligation|pension underfunding", label):
        return "net_pension_obligation"
    if re.search(r"(?i)post-?employment|benefit obligation", label):
        return "opeb"
    if re.search(r"(?i)capital lease|lease purchase", label):
        return "capital_lease"
    if re.search(r"(?i)alternate|source bonds|bonds payable", label):
        return "revenue_bonds"
    if re.search(r"(?i)unamortized", label):
        return "bond_premium"
    if re.search(r"(?i)iepa", label):
        return "revolving_loan"
    return D.line_id(label)


# ---- schedule ---------------------------------------------------------------------------------------------
def numeric_words(words):
    out = []
    for w in words:
        k, v = classify(w.t)
        if k in ("num", "dash"):
            out.append((w, v))
    return out


def read_schedules(fy, pages, ends_by_line):
    """FY2013-FY2015 debt service tables: rows are the consecutive years starting at the first label; numbers are placed by the column
    positions of the TOTAL row, merging the pieces OCR put on neighbouring text lines.  -> list of dict(page, rows=[(year, [vals])], total=[vals])"""
    out = []
    for p, lines in sorted(pages.items()):
        words = [w for ln in lines for w in ln.words]
        hdr = [ln for ln in lines if re.search(r"(?i)\bprincipal\b", ln.text) and re.search(r"(?i)interest", ln.text)]
        tot = next((ln for ln in lines if re.match(r"(?i)^total\b", ln.text) and len(ln.split()[1]) >= 4 and ln.split()[1][0][0] > 150), None)
        if not hdr or tot is None:
            continue
        hy = hdr[0].y
        ty = tot.y
        anchors = [x[0] for x in tot.split()[1]]
        # drop the leading label tokens (none) and make sure an even number of columns
        if len(anchors) % 2:
            continue
        left = [w for w in words if w.x1 < anchors[0] - 55 and hy < (w.y0 + w.y1) / 2 < ty - 4 and re.fullmatch(r"20\d\d|20\d|203|202|\d{4}", w.t)]
        left.sort(key=lambda w: w.y0)
        if not left:
            continue
        # merge fragments ("203" + "1") on the same row
        rows_y = []
        for w in left:
            yc = (w.y0 + w.y1) / 2
            if rows_y and abs(rows_y[-1] - yc) < 4:
                continue
            rows_y.append(yc)
        first_year = None
        for w in left:
            if re.fullmatch(r"20\d\d", w.t):
                first_year = int(w.t)
                break
        if first_year is None:
            continue
        # the first row's year is the smallest plausible (the label may be misread: use the minimum of the 4-digit labels minus its row offset)
        labels = [(int(w.t), (w.y0 + w.y1) / 2) for w in left if re.fullmatch(r"20\d\d", w.t)]
        y0 = min(l[0] - rows_y.index(min(rows_y, key=lambda yy: abs(yy - l[1]))) for l in labels)
        nums = numeric_words([w for w in words if w.x1 >= anchors[0] - 25 and hy < (w.y0 + w.y1) / 2 < ty + 5])
        rows = []
        for i, yy in enumerate(rows_y):
            band = [(w, v) for (w, v) in nums if abs((w.y0 + w.y1) / 2 - yy) <= 5]
            cols = assign_columns([(w.x1, 0 if (isinstance(v, int) and 0 < v < 10 and len(w.t) == 1) else v, "num", w.t) for (w, v) in band], anchors, tol=30)
            rows.append((y0 + i, [cols[c][0] if c in cols else 0 for c in range(len(anchors))]))
        tcols = [x[1] for x in tot.split()[1]]
        out.append(dict(page=p, rows=rows, total=tcols, ncols=len(anchors)))
    return out


def validate_schedules(scheds, ends, chk):
    for sc in scheds:
        n = sc["ncols"]
        for c in range(n):
            chk.eq(f"debt|sched|p{sc['page']}|col{c}", sc["total"][c], sum(r[1][c] for r in sc["rows"]), f"schedule on page {sc['page']} column {c}: years sum to TOTAL")
        for g in range(n // 2):
            pt = sc["total"][2 * g]
            if pt and pt not in ends:
                chk.fail(f"debt|sched|p{sc['page']}|tie{g}", f"schedule on page {sc['page']} group {g}: principal total {pt:,} equals no balance in the liability table {sorted(ends)}")


ISSUE_HDR = re.compile(r"(?i)issuances")


def read_old_issues(fy, pages):
    """FY2013-15 bond / TIF note / loan tables (beginning, issuances, retirements, ending), one table per header line.
    -> [dict(table, desc, vals, page, is_total)]"""
    res, tno = [], 0
    for p, lines in sorted(pages.items()):
        hdrs = [i for i, ln in enumerate(lines) if ISSUE_HDR.search(ln.text) and re.search(r"(?i)retirements", ln.text)]
        for hi in hdrs:
            hdr = lines[hi]
            wc = {}
            for w in hdr.words:
                t = w.t.lower().strip("_")
                if t.startswith("issuances"):
                    wc["i"] = w.x1
                elif t.startswith("retirements"):
                    wc["r"] = w.x1
            bal = [w for w in hdr.words if w.t.lower().startswith("balances")]
            if "i" not in wc or "r" not in wc or len(bal) < 2:
                continue
            tno += 1
            anchors = [bal[0].x1, wc["i"], wc["r"], bal[-1].x1]
            pend = []
            nxt = next((j for j in hdrs if j > hi), len(lines))
            for ln in lines[hi + 1:nxt]:
                label, nums = ln.split()
                right = [x for x in nums if x[0] > anchors[0] - 40]
                if re.match(r"(?i)^(tax increment revenue notes|the city issued|the notes will be|financing district|landfill|\*\s|the city entered)", ln.text) and not right:
                    break
                cols = assign_columns(list(right), anchors, tol=34) if right else {}
                keep = {id(x) for x in right}
                words_left = " ".join(w.t for w in ln.words if not (w.t.replace(",", "").replace("$", "").isdigit() and w.x1 > anchors[0] - 40) and w.t not in ("-", "$", "*"))
                if cols:
                    vals = [cols[c][0] if c in cols else 0 for c in range(4)]
                    desc = strip_desc(" ".join(pend + [words_left]))
                    if sum(1 for v in vals if v) == 1 and max(vals) < 1000:
                        pend = []
                        continue
                    res.append(dict(table=tno, desc=desc, vals=vals, page=p, is_total=not desc))
                    pend = []
                elif words_left:
                    pend.append(words_left)
    return res


def validate_old_issues(rows, ends, chk):
    for t in sorted({r["table"] for r in rows}):
        tr = [r for r in rows if r["table"] == t]
        tot = next((r for r in tr if r["is_total"]), None)
        items = [r for r in tr if not r["is_total"]]
        for c, nm in enumerate(("beginning", "issuances", "retirements", "ending")):
            if tot:
                chk.eq(f"debt|issues|t{t}|{nm}", tot["vals"][c], sum(r["vals"][c] for r in items), f"issue table {t}: {nm} sum to printed total")
        b, i, r_, e = (sum(r["vals"][c] for r in items) for c in range(4))
        chk.eq(f"debt|issues|t{t}|roll", e, b + i - r_, f"issue table {t}: beginning + issuances - retirements = ending")
        if not any(v for v in ends) or not e:
            continue
        # tie the tables's ending balance to the liability table
        if e not in ends and not any(abs(e - x) == 0 for x in ends):
            chk.fail(f"debt|issues|t{t}|tie", f"issue table {t}: ending {e:,} matches no balance of the liability table")


def strip_desc(t):
    return re.sub(r"\s+", " ", t.replace("$", "")).strip(" -")


# ---- generic labelled rows (pension / OPEB) ---------------------------------------------------------------
PLAN4 = ["imrf_regular", "imrf_slep", "police", "fire"]


def rows_of(lines, pat, n, stop=None):
    """[(numbers list, line)] for lines whose label matches pat and that carry >= n numbers (last n returned)."""
    out = []
    for ln in lines:
        label, nums = ln.split()
        if re.search(pat, label, re.I) and len(nums) >= n:
            out.append(([x[1] for x in nums[-n:]], [x[2] for x in nums[-n:]], ln))
    return out


def read_pension(fy, pages):
    if fy == 2012:
        return read_pension_2012(pages)
    """-> list of (plan, measure, period, value, page) and problems"""
    lines = [ln for p in sorted(pages) for ln in pages[p]]
    res = []

    def add(plan, meas, period, v, ln):
        res.append((plan, meas, period, v, ln.page))
    # contribution rates / assumptions (4 plan columns)
    for pat, meas in ((r"^employer\b", "employer_rate_pct"), (r"^employee\b", "employee_rate_pct"), (r"investment rate of return", "investment_return_assumption_pct"),
                      (r"inflation rate", "inflation_pct"), (r"cost-of-living", "cola_pct")):
        for vals, kinds, ln in rows_of(lines, pat, 4):
            if all(k == "pct" for k in kinds):
                for plan, v in zip(PLAN4, vals):
                    add(plan, meas, "", v, ln)
                break
    # funded status (4 plan columns)
    fs = [(r"^percent funded", "funded_ratio_pct"), (r"for benefits$", "aal"), (r"^actuarial value of assets", "ava"), (r"\(uaal\)$", "uaal_signed"),
          (r"by the plan\)$", "covered_payroll"), (r"^payroll$", "uaal_pct_payroll")]
    for pat, meas in fs:
        for vals, kinds, ln in rows_of(lines, pat, 4):
            for plan, v in zip(PLAN4, vals):
                add(plan, meas, "", v, ln)
            break
    # annual pension cost / NPO (police, fire [, total])
    npo = [(r"^annual required contribution", "arc"), (r"^interest on the npo|^interest on net pension", "interest_on_npo"), (r"^adjustment to (the )?arc", "arc_adjustment"),
           (r"^annual pension cost$", "annual_pension_cost"), (r"^(actual contribution|contributions made)", "employer_contribution"), (r"^(increase|change)( in)?( the)? npo|^change in npo", "change_in_npo"),
           (r"^(npo\s*-?\s*beginning|beginning npo)", "npo_beginning"), (r"^(npo\s*-?\s*(ending|end of year)|ending npo)", "npo_ending")]
    for pat, meas in npo:
        got = None
        for n in (3, 2):
            for vals, kinds, ln in rows_of(lines, pat, n):
                if not any(k == "pct" for k in kinds) and not (2000 < vals[0] < 2100 and len(vals) >= 3 and n == 3 and False) and not re.search(r"\b20\d\d\b", ln.text.split("$")[0]):
                    got = (vals, ln, n)
                    break
            if got:
                break
        if got:
            vals, ln, n = got
            for plan, v in zip(("police", "fire"), vals[:2]):
                add(plan, meas, "", v, ln)
    # trend information: rows 'YYYY v v v v' under the labels APC / Actual Contributions / Percentage / NPO (4 plan columns), blocks of years
    blk, cur = [], None
    cut = next((i for i, l in enumerate(lines) if re.search(r"(?i)other post.?employment benefits", l.text) and not l.split()[1]), len(lines))
    for ln in lines[:cut]:
        t = ln.text
        m = re.search(r"(?i)(annual pension cost|actual contributions|percentage of apc|net pension obligation|\(apc\)|contributed$)", t)
        label, nums = ln.split()
        ym = re.search(r"\b(20\d\d)\b", t)
        if re.search(r"(?i)^annual pension cost\s*(\(apc\))?\s*20\d\d|^annual pension cost.*20\d\d", t):
            cur = "annual_pension_cost"
        elif re.search(r"(?i)^actual contributions?.*20\d\d", t):
            cur = "employer_contribution"
        elif re.search(r"(?i)^percentage of apc.*20\d\d", t):
            cur = "pct_apc_contributed"
        elif re.search(r"(?i)^net pension obligation.*20\d\d", t):
            cur = "npo"
        if cur == "npo" and ym and len(nums) == 4 and nums[0][1] == int(ym.group(1)):
            nums = [nums[0], (0, 0, "dash", "-"), (0, 0, "dash", "-")] + [(x[0], x[1], x[2], x[3]) for x in nums[2:]]
            nums = [nums[0], (0, 0, "dash", "-"), (0, 0, "dash", "-"), nums[3], nums[4]]
        if cur and ym and len(nums) >= 5 and nums[0][1] == int(ym.group(1)) and 2000 < nums[0][1] < 2100:
            vals = [x[1] for x in nums[1:5]]
            for plan, v in zip(PLAN4, vals):
                res.append((plan, "trend_" + cur, int(ym.group(1)), v, ln.page))
        elif cur and ym and len(nums) == 3 and nums[0][1] == int(ym.group(1)):
            pass
    # membership
    for pat, cls in ((r"^(retirees.*|.*terminated employees.*)\.?$", "retirees_and_terminated_vested"), (r"^vested$", "active_vested"), (r"^nonvested$", "active_nonvested")):
        pass
    return res


def read_pension_2012(pages):
    """FY2012: trend tables for IMRF (regular, SLEP) and police / firefighters printed as 2 columns each, three rows per block labelled
    2011/2010/2009 (= fiscal years 2012/2011/2010; OCR misreads some labels, so the year is taken from the row's position in its block)."""
    lines = [ln for p in sorted(pages) for ln in pages[p]]
    res, pair, cur, idx = [], None, None, 0
    for ln in lines:
        t = ln.text
        label, nums = ln.split()
        if re.search(r"(?i)\bRegular\b.*\bSLEP\b", t) and not nums:
            pair, cur = ("imrf_regular", "imrf_slep"), None
        elif re.search(r"(?i)firefighters", t) and not nums and re.search(r"(?i)police|for", t) and cur is None or re.search(r"(?i)^for police firefighters", t):
            pair, cur = ("police", "fire"), None
        for pat, name in ((r"^annual pension cost", "annual_pension_cost"), (r"^actual contribution", "employer_contribution"), (r"^percentage of apc", "pct_apc_contributed"), (r"^npo \(asset\)", "npo")):
            if re.search(pat, t, re.I) and pair:
                cur, idx = name, 0
        if pair and cur and len(nums) in (3, 4) and 2000 < nums[0][1] < 2100 or (pair and cur and len(nums) == 3 and nums[0][1] > 2000):
            vals = [x[1] for x in nums[-2:]]
            if cur == "pct_apc_contributed":
                vals = [v if x[2] == "pct" else float(v) for v, x in zip(vals, nums[-2:])]
            if idx < 3 and not re.search(r"(?i)^(annual|actual|percentage|npo)", t) or idx < 3:
                for plan, v in zip(pair, vals):
                    res.append((plan, "trend_" + cur, (2011 if cur == "npo" else 2012) - idx, v, ln.page))
                idx += 1
    # annual pension cost table (police, fire)
    npo = [(r"^annual required contribution", "arc"), (r"^interest on net pension", "interest_on_npo"), (r"^adjustment to arc", "arc_adjustment"),
           (r"^annual pension cost$", "annual_pension_cost"), (r"^contributions made", "employer_contribution"), (r"^change in npo", "change_in_npo"),
           (r"^beginning npo", "npo_beginning"), (r"^ending npo", "npo_ending")]
    for pat, meas in npo:
        for vals, kinds, ln in rows_of(lines, pat, 2):
            if not re.search(r"20\d\d", ln.text):
                for plan, v in zip(("police", "fire"), vals):
                    res.append((plan, meas, "", v, ln.page))
                break
    return res


def validate_pension(res, chk):
    def g(plan, meas, period=""):
        return next((v for (p, m, per, v, pg) in res if p == plan and m == meas and per == period), None)
    for plan in PLAN4:
        aal, ava, uaal, cp = g(plan, "aal"), g(plan, "ava"), g(plan, "uaal_signed"), g(plan, "covered_payroll")
        fr, ratio = g(plan, "funded_ratio_pct"), g(plan, "uaal_pct_payroll")
        if None not in (aal, ava, uaal):
            chk.eq(f"pen|{plan}|uaal", abs(uaal) if uaal < 0 else uaal, aal - ava, f"{plan}: UAAL = AAL - AVA")
        if None not in (aal, ava, fr) and aal:
            chk.near(f"pen|{plan}|funded", fr, ava / aal * 100, 0.011, f"{plan}: funded ratio = AVA / AAL")
        if None not in (uaal, cp, ratio) and cp:
            chk.near(f"pen|{plan}|uaal_pct", ratio, abs(uaal) / cp * 100, 0.011, f"{plan}: UAAL % of covered payroll")
    for plan in ("police", "fire"):
        arc, i, adj, apc = (g(plan, k) for k in ("arc", "interest_on_npo", "arc_adjustment", "annual_pension_cost"))
        con, inc, b, e = (g(plan, k) for k in ("employer_contribution", "change_in_npo", "npo_beginning", "npo_ending"))
        if None not in (arc, i, adj, apc):
            chk.eq(f"pen|{plan}|apc", apc, arc + i + adj if adj < 0 else arc + i - adj, f"{plan}: APC = ARC + interest + adjustment")
        if None not in (apc, con, inc):
            chk.eq(f"pen|{plan}|change", inc, apc - con, f"{plan}: change in NPO = APC - contribution")
        if None not in (b, inc, e):
            chk.eq(f"pen|{plan}|npo", e, b + inc, f"{plan}: NPO ending = beginning + change")
    years = sorted({per for (_p, m, per, _v, _g) in res if m == "trend_npo" and per != ""})
    for plan in PLAN4:
        for y in years:
            a, c, pc = (g(plan, "trend_" + k, y) for k in ("annual_pension_cost", "employer_contribution", "pct_apc_contributed"))
            if None not in (a, c, pc) and a:
                chk.near(f"pen|{plan}|trend|{y}|pct", pc, c / a * 100, 0.011, f"{plan} {y}: % of APC contributed")
            n0, n1 = g(plan, "trend_npo", y - 1), g(plan, "trend_npo", y)
            if None not in (n0, n1, a, c):
                chk.eq(f"pen|{plan}|trend|{y}|npo", n1, n0 + a - c, f"{plan} {y}: NPO = previous NPO + APC - contributions")


OPEB_OLD = [(r"^annual required contribution", "arc"), (r"^interest on (the )?net opeb", "interest_on_noo"), (r"^adjustment to (the )?(arc|annual required)", "arc_adjustment"),
            (r"^annual opeb cost", "annual_opeb_cost"), (r"^(actual contribution|contributions made)", "contributions"), (r"^(increase|change)[^0-9]*net opeb", "increase"),
            (r"^net opeb obligation.*beginning|^net opeb obligation beginning", "noo_beginning"), (r"^net opeb obligation.*(ending|end of year)|^net opeb obligation \(excess\)", "noo_ending"),
            (r"^city portion", "noo_city_portion"), (r"^library portion", "noo_library_portion"),
            (r"^actuarial accrued liability", "aal"), (r"^unfunded actuarial accrued", "uaal"), (r"^funded ratio", "funded_ratio_pct"), (r"^covered payroll", "covered_payroll"),
            (r"^uaal as a percentage", "uaal_pct_payroll")]


def read_opeb(fy, pages):
    lines = [ln for p in sorted(pages) for ln in pages[p]]
    # start at the OPEB section
    start = next((i for i, ln in enumerate(lines) if re.search(r"(?i)other post.?employment benefits|postemployment benefits other than pension|annual opeb cost and net opeb", ln.text) and not ln.split()[1]), None)
    res = []
    if start is None:
        return res
    cur_col = 1 if fy == 2012 else 1
    for ln in lines[start:]:
        label, nums = ln.split()
        if not nums:
            continue
        for pat, key in OPEB_OLD:
            if re.search(pat, label, re.I):
                if not any(r[0] == key for r in res):
                    vals = [x[1] for x in nums]
                    res.append((key, vals[0] if fy != 2012 else vals[0], ln.page, vals))
                break
    # trend rows 'YYYY cost contrib pct% NOO'
    for ln in lines[start:]:
        label, nums = ln.split()
        m = re.match(r"^(20\d\d)\b", ln.text)
        if m and len(nums) >= 5 and any(x[2] == "pct" for x in nums):
            v = [x[1] for x in nums]
            res.append(("trend", int(m.group(1)), ln.page, v[1:5]))
    return res


def validate_opeb_old(res, chk):
    d = {r[0]: r[1] for r in res if r[0] != "trend"}
    try:
        chk.eq("opeb|old|cost", d["annual_opeb_cost"], d["arc"] + d["interest_on_noo"] + d["arc_adjustment"] if d["arc_adjustment"] < 0 else d["arc"] + d["interest_on_noo"] - d["arc_adjustment"], "annual OPEB cost = ARC + interest + adjustment")
        chk.eq("opeb|old|increase", d["increase"], d["annual_opeb_cost"] - d["contributions"], "increase in NOO = cost - contributions")
        chk.eq("opeb|old|end", d["noo_ending"], d["noo_beginning"] + d["increase"], "NOO ending = beginning + increase")
    except KeyError as e:
        chk.fail("opeb|old|rows", f"net OPEB obligation table incomplete: {e}")
    if "noo_city_portion" in d and "noo_library_portion" in d:
        chk.eq("opeb|old|portions", d["noo_ending"], d["noo_city_portion"] + d["noo_library_portion"], "NOO = city portion + library portion")
    if {"aal", "uaal"} <= set(d) and "funded_ratio_pct" in d:
        chk.eq("opeb|old|uaal", d["uaal"], d["aal"], "UAAL = AAL (no assets)")
    for r in res:
        if r[0] == "trend":
            y, (cost, con, pct, noo) = r[1], r[3]
            if not cost:
                continue
            chk.near(f"opeb|old|trend|{y}|pct", pct, con / cost * 100, 0.011, f"{y}: % of OPEB cost contributed")
    tr = sorted((r[1], r[3]) for r in res if r[0] == "trend")
    for (y0, a), (y1, b) in zip(tr, tr[1:]):
        if y1 == y0 + 1:
            chk.eq(f"opeb|old|trend|{y1}|noo", b[3], a[3] + b[0] - b[1], f"{y1}: NOO = previous + cost - contributions")


def run_year(fy):
    import extract_audit_debt_pension as X
    allow, other = load_allow(fy)
    chk = Checker(allow)
    ov = other
    pages = all_pages(fy, ov)
    T = dict(debt_balances=[], debt_issues=[], debt_service=[], pension_rollforward=[], pension_summary=[], opeb_rollforward=[])
    doc = DOC(fy)
    rows, ncol = read_activity(pages)
    validate_activity(rows, ncol, chk)
    for act, label, vals, pg in rows:
        lab = label or "(unlabeled subtotal)"
        lid = "total" if not label or re.match(r"(?i)^total", label) else old_line_id(label)
        for m, v in zip(("beginning", "additions", "reductions", "ending", "current_portion")[:ncol], vals):
            T["debt_balances"].append([fy, doc, pg, act, lab, lid, m, v, ""])
    ends = {r[2][3] for r in rows}
    if fy == 2012:
        lines = [ln for p_ in (52, 53) for ln in pages.get(p_, [])]
        gi = next((i for i, l in enumerate(lines) if re.search(r"(?i)outstanding debt as of", l.text)), None)
        if gi is not None:
            rest = lines[gi + 1:]
            bi = next((i for i, l in enumerate(rest) if re.match(r"(?i)^business-?type activities", l.text)), len(rest))
            for act, bl in (("governmental", rest[:bi]), ("business_type", rest[bi:])):
                its = D.parse_issues(act, bl)
                its = [i for i in its]
                D_ok = [i for i in its if i["issue"] != "TOTAL"]
                tot = next((i for i in its if i["issue"] == "TOTAL"), None)
                if tot:
                    chk.eq(f"debt|issues|{act}|ending", tot["ending"], sum(i["ending"] or 0 for i in D_ok), f"{act} outstanding-debt table: items sum to total")
                    chk.eq(f"debt|issues|{act}|current", tot["current"], sum(i["current"] or 0 for i in D_ok), f"{act} outstanding-debt table: current portions sum to total")
                    at = next((r for r in rows if r[0] == act and (not r[1] or re.match(r"(?i)^total", r[1]))), None)
                    if at:
                        chk.eq(f"debt|issues|{act}|tie", tot["ending"], at[2][3], f"{act} outstanding-debt table total = liability table ending")
                for i in its:
                    T["debt_issues"].append([fy, doc, i["page"], act, i["issue"], i["rate_text"], i["maturity_text"], i["ending"], i["current"], "", i["description"]])
    if fy >= 2013:
        iss = read_old_issues(fy, pages)
        validate_old_issues(iss, ends, chk)
        for r in iss:
            if not r["is_total"]:
                T["debt_issues"].append([fy, doc, r["page"], "", r["desc"][:140], "; ".join(re.findall(r"\d+(?:\.\d+)?%(?: to \d+(?:\.\d+)?%)?", r["desc"])),
                                         (re.findall(r"through\s+((?:[A-Z][a-z]+\.? \d{1,2},? )?\d{4})", r["desc"]) or [""])[0], r["vals"][3], "", "", r["desc"]])
        scheds = read_schedules(fy, pages, ends)
        validate_schedules(scheds, ends, chk)
        for sc in scheds:
            for g in range(sc["ncols"] // 2):
                m_ = next((r for r in rows if r[2][3] == sc["total"][2 * g] and r[1]), None)
                lid = old_line_id(m_[1]) if m_ else ""
                a_, i_ = (m_[0], m_[1]) if m_ else ("", "")
                for y, vals in sc["rows"]:
                    T["debt_service"].append([fy, doc, sc["page"], a_, g, i_, str(y), vals[2 * g], vals[2 * g + 1], vals[2 * g] + vals[2 * g + 1], "year", lid, ""])
                T["debt_service"].append([fy, doc, sc["page"], a_, g, i_, "TOTAL", sc["total"][2 * g], sc["total"][2 * g + 1], sc["total"][2 * g] + sc["total"][2 * g + 1], "total", lid, ""])
    pen = read_pension(fy, pages)
    validate_pension(pen, chk)
    for plan, meas, per, v, pg in pen:
        if meas in ("arc", "interest_on_npo", "arc_adjustment", "annual_pension_cost", "employer_contribution", "change_in_npo", "npo_beginning", "npo_ending"):
            T["pension_rollforward"].append([fy, doc, pg, plan, "note_gasb27", "", "", "npo", meas, v, ""])
        else:
            T["pension_summary"].append([fy, doc, pg, plan, meas, per, v, "", "pct" if "pct" in meas or "ratio" in meas else "usd", "note", ""])
    op = read_opeb(fy, pages)
    validate_opeb_old(op, chk)
    for r in op:
        if r[0] == "trend":
            for k, v in zip(("annual_opeb_cost", "contributions", "pct_contributed", "noo_ending"), r[3]):
                T["opeb_rollforward"].append([fy, doc, r[2], "opeb_city", "note_trend", f"{r[1]}-04-30", r[1], k, v, ""])
        else:
            T["opeb_rollforward"].append([fy, doc, r[2], "opeb_city", "note_gasb45", "", "", r[0], r[1], ""])
    X.write_tables(fy, T, chk)
    X.report(fy, T, chk)
    return T, chk
