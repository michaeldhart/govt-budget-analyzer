"""Other postemployment benefits (OPEB): the City plan (note 'OTHER POSTEMPLOYMENT BENEFITS') and the Ida Public Library plan (inside the
component-unit note), plus the multi-year RSI schedules.

GASB 45 layout (FY2012-FY2018): annual OPEB cost / net OPEB obligation (NOO) table, funded status, trend table.
GASB 75 layout (FY2019-FY2026): total OPEB liability roll-forward (one column), sensitivities, deferred outflows / inflows.
"""
import re
import statistics

from audit_debt_pension_common import is_page_no, assign_columns, page_lines, all_plain_text
from audit_debt_pension_pension import parse_rollforward, to_iso, YEAR_TOK, RF_LINES

NOTE_HEAD_CITY = re.compile(r"(?m)^\s*\d+\.\s+OTHER POST-?EMPLOYMENT BENEFITS")
NEXT_NOTE = re.compile(r"(?m)^\s*\d+\.\s+[A-Z][A-Z ,\-'()]{8,}\s*$")


def city_note_pages(fy):
    texts = all_plain_text(fy)
    first = last = None
    for i, t in enumerate(texts, 1):
        if first is None:
            if NOTE_HEAD_CITY.search(t) and re.search(r"(?i)notes to (the )?financial statements", t[:700]):
                first = i
        else:
            m = [x for x in NEXT_NOTE.findall(t) if not re.search(r"(?i)post-?employment|\(continued\)", x)]
            if m:
                last = i
                break
    return list(range(first, (last or first + 5) + 1)) if first else []


def note_lines(fy, pages, start_pat, stop_pat, source="text", prep="gray"):
    """Lines from the line matching start_pat until the line matching stop_pat (exclusive)."""
    pl = page_lines(fy, pages, source, prep)
    out, on = [], False
    for p in pages:
        for ln in pl[p]:
            t = ln.text.strip()
            if is_page_no(t) or re.match(r"(?i)^(city of belvidere|notes to (the )?financial statements)", t):
                continue
            if not on:
                if re.search(start_pat, t):
                    on = True
                continue
            if stop_pat and re.search(stop_pat, t) and not re.search(r"(?i)\(continued\)", t):
                return out
            out.append(ln)
    return out


def library_note_pages(fy):
    """Pages of the discretely presented component unit (Ida Public Library) note: from its numbered heading while the pages are still
    'Notes to Financial Statements' pages."""
    texts = all_plain_text(fy)
    first = None
    for i, t in enumerate(texts, 1):
        if re.search(r"(?m)^\s*\d+\.\s+DISCRETELY PRESENTED COMPONENT UNIT", t) and re.search(r"(?i)notes to (the )?financial statements", t[:700]):
            first = i
            break
    if first is None:
        return []
    pages = [first]
    for j in range(first + 1, len(texts) + 1):
        head = " ".join(texts[j - 1].split())[:300]
        if re.search(r"(?i)notes to (the )?financial statements", head) and not re.search(r"(?m)^\s*\d+\.\s+(?!DISCRETELY)[A-Z][A-Z ,\-'()]{8,}\s*$", texts[j - 1][:900]):
            pages.append(j)
        else:
            break
    return pages


def clean_amount_typo(nums):
    """'$686.594' (a period printed for a comma) -> 686594."""
    out = []
    for x1, v, k, raw in nums:
        if k == "num" and isinstance(v, float) and re.fullmatch(r"\(?\$?\d{1,3}\.\d{3}\)?", raw.strip()):
            v = int(round(v * 1000)) * (-1 if v < 0 else 1)
        out.append((x1, v, k, raw))
    return out


def parse_opeb_flat(lines, plan):
    """-> dict(rollforward, membership, facts, sensitivity, deferred, amort, expense, noo, funded, trend)"""
    res = dict(rollforward=[], membership=[], facts=[], sensitivity=[], deferred=[], amort=[], expense=None, noo=[], funded=[], trend=[])
    texts = [re.sub(r"\s+", " ", ln.text) for ln in lines]
    # membership
    mi = next((i for i, t in enumerate(texts) if re.search(r"(?i)membership consisted", t)), None)
    if mi is not None:
        pend = []
        for ln in lines[mi + 1:mi + 14]:
            label, nums = ln.split()
            nums = clean_amount_typo(nums)
            if nums and not re.match(r"(?i)^participating", label):
                lab = " ".join(pend + [label]).strip()
                pend = []
                cls = ("total" if re.match(r"(?i)^total", lab) else "active" if re.search(r"(?i)(?<!in)\bactive\b", lab) else
                       "terminated_vested" if re.search(r"(?i)entitled|not yet", lab) else "retirees" if re.search(r"(?i)receiving|retire", lab) else None)
                if cls:
                    res["membership"].append((cls, nums[-1][1], ln.page))
                    if cls == "total":
                        break
            elif label:
                pend.append(label)
    # key/value assumptions
    kv = [(r"^actuarial cost method\s+(.*)$", "cost_method"), (r"^actuarial value of assets\s+(.*)$", "asset_value"), (r"^salary increases?\s+(.*)$", "salary_increases"),
          (r"^discount rate\s+(\d.*)$", "discount_rate"), (r"^healthcare cost trend rates?\s+(.*)$", "healthcare_trend"), (r"^inflation\s+(.*)$", "inflation")]
    for ln, t in zip(lines, texts):
        for pat, meas in kv:
            m = re.match(pat, t, re.I)
            if m:
                val = m.group(1).strip()
                pcts = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", val)]
                res["facts"].append((meas, pcts[0] if pcts and meas in ("discount_rate", "salary_increases", "inflation") else None, val, ln.page))
    for t, ln in zip(texts, lines):
        m = re.search(r"OPEB (?:liability|obligation) of \$([\d,\.]+) was measured", t)
        if m:
            res["facts"].append(("total_opeb_liability_text", int(re.sub(r"[^\d]", "", m.group(1))), "", ln.page))
        m = re.search(r"actuarial valuation as of ([A-Z][a-z]+ \d{1,2}, \d{4})", t)
        if m:
            res["facts"].append(("valuation_date", None, m.group(1), ln.page))
    # roll-forward (one column)
    bi = next((i for i, t in enumerate(texts) if re.match(r"(?i)^balances at\b", t)), None)
    if bi is not None:
        res["rollforward"] = parse_rollforward(lines[bi:], 1)
    # sensitivities: rows 'Total OPEB liability  a b c'
    sens = []
    for i, (ln, t) in enumerate(zip(lines, texts)):
        label, nums = ln.split()
        if re.match(r"(?i)^total opeb liability", label) and len(nums) >= 3 and not re.search(r"(?i)ending|beginning|net change", label):
            nums = clean_amount_typo(nums)
            hdr = " ".join(texts[max(0, i - 5):i])
            rates = re.findall(r"\(([\d.]+%(?: to [\d.]+%)?)\)", hdr)
            sens.append(([n[1] for n in nums[-3:]], rates[-3:], ln.page))
    for k, (vals, rates, pg) in enumerate(sens):
        res["sensitivity"].append(("discount_rate" if k == 0 else "healthcare_trend", vals, rates, pg))
    # deferred table + amortisation + expense
    di = next((i for i, t in enumerate(texts) if re.search(r"(?i)deferred outflows of resources and deferred inflows of resources related to OPEB|following sources|from the following$", t)), None)
    m = None
    for t in texts:
        m = re.search(r"OPEB expense of \$?\s*(\(?[\d,\.]+\)?)", t) or m
    if m:
        v = m.group(1)
        res["expense"] = int(re.sub(r"[^\d]", "", v)) * (-1 if "(" in v else 1)
    if di is not None:
        phase, pend = 0, []
        for ln in lines[di:]:
            label, nums = ln.split()
            nums = clean_amount_typo(nums)
            t = re.sub(r"\s+", " ", ln.text)
            if re.search(r"(?i)recognized in (OPEB|pension) expense as follows|^expense as follows|^year ending", t) and not nums:
                phase, pend = 1, []
                continue
            if phase == 0:
                if len(nums) >= 2 and not re.match(r"(?i)^(deferred|outflows|inflows|resources|for the year)", label):
                    lab = " ".join(pend + [label]).strip()
                    pend = []
                    if re.match(r"(?i)^total", lab):
                        res["deferred"].append(("TOTAL", nums[-2][1], nums[-1][1], ln.page))
                    else:
                        src = "experience" if re.search(r"(?i)expected|experience", lab) else "assumptions" if re.search(r"(?i)assumption", lab) else "other:" + lab[:30]
                        res["deferred"].append((src, nums[-2][1], nums[-1][1], ln.page))
                elif label and not re.match(r"(?i)^(deferred|outflows|inflows|resources|for the year|at april|the city|the library|sources|from the following|differences? in|changes in the)", label):
                    pend.append(label)
                else:
                    pend = []
            else:
                if nums and (re.match(r"^20\d\d\b", t) or re.match(r"(?i)^(thereafter|total)", label)):
                    m2 = re.match(r"^(20\d\d)\b", t)
                    res["amort"].append((m2.group(1) if m2 else (label.split()[0].title() if label else "?"), nums[-1][1], ln.page))
                elif re.match(r"(?i)^(city of|notes to)", t):
                    continue
    # GASB 45: net OPEB obligation calculation
    noo_pat = [(r"^annual required contribution", "arc"), (r"^interest on net opeb", "interest_on_noo"), (r"^adjustment to annual required", "arc_adjustment"),
               (r"^annual opeb cost", "annual_opeb_cost"), (r"^contributions made", "contributions"), (r"^increase \(decrease\) in net opeb|^increase in net opeb", "increase"),
               (r"^net opeb obligation,? beginning", "noo_beginning"), (r"^net opeb obligation,? end", "noo_ending")]
    for ln in lines:
        label, nums = ln.split()
        nums = clean_amount_typo(nums)
        if nums:
            for pat, key in noo_pat:
                if re.search(pat, label, re.I):
                    res["noo"].append((key, nums[-1][1], ln.page))
    fund_pat = [(r"^actuarial accrued liability", "aal"), (r"^actuarial value of plan assets", "ava"), (r"^unfunded actuarial accrued liability", "uaal"),
                (r"^funded ratio", "funded_ratio_pct"), (r"^covered payroll", "covered_payroll"), (r"^uaal as a percentage", "uaal_pct_payroll")]
    for ln in lines:
        label, nums = ln.split()
        nums = clean_amount_typo(nums)
        if nums:
            for pat, key in fund_pat:
                if re.search(pat, label, re.I):
                    res["funded"].append((key, nums[-1][1], nums[-1][2], ln.page))
    # GASB 45 three-year trend: rows 'YYYY $ cost $ contrib pct% $ NOO'
    for ln in lines:
        t = ln.text
        m = re.match(r"^(20\d\d)\b", t)
        label, nums = ln.split()
        nums = [n for n in clean_amount_typo(nums)]
        if m and len(nums) == 5 and nums[3][2] == "pct":
            res["trend"].append((int(m.group(1)), nums[1][1], nums[2][1], nums[3][1], nums[4][1], ln.page))
    return res


def validate_opeb(res, plan, chk, gasb75):
    tag = f"opeb|{plan}"
    mem = {c: v for c, v, _p in res["membership"]}
    if mem.get("total") is not None:
        chk.eq(f"{tag}|members", mem["total"], sum(v for c, v in mem.items() if c != "total"), f"{plan} membership: classes sum to TOTAL")
    rf = res["rollforward"]
    if gasb75:
        beg = next((r for r in rf if r[0] == "beginning"), None)
        end = next((r for r in rf if r[0] == "ending"), None)
        net = next((r for r in rf if r[0] == "net_change"), None)
        chg = [r for r in rf if r[0] not in ("beginning", "ending", "net_change")]
        if None in (beg, end, net):
            chk.fail(f"{tag}|rf_rows", "OPEB roll-forward: missing beginning / ending / net changes")
        else:
            for r in chg:
                if r[0].startswith("?"):
                    chk.fail(f"{tag}|rf|{r[0]}", f"unrecognised roll-forward line {r[0]!r}")
            chk.eq(f"{tag}|rf|net_change", net[1][0], sum(r[1][0] for r in chg), f"{plan} OPEB roll-forward: lines sum to net changes")
            chk.eq(f"{tag}|rf|roll", end[1][0], beg[1][0] + net[1][0], f"{plan} OPEB roll-forward: beginning + net = ending")
        for fact in res["facts"]:
            if fact[0] == "total_opeb_liability_text" and end is not None:
                chk.eq(f"{tag}|text_total", fact[1], end[1][0], f"{plan}: the note's sentence total OPEB liability = roll-forward ending")
        for cat, vals, rates, pg in res["sensitivity"]:
            if end is not None:
                chk.eq(f"{tag}|sens|{cat}", vals[1], end[1][0], f"{plan} OPEB sensitivity ({cat}): current-rate column = total OPEB liability")
                if not (vals[0] >= vals[1] >= vals[2]) and cat == "discount_rate":
                    chk.fail(f"{tag}|sens_order|{cat}", f"{plan} OPEB sensitivity not decreasing with discount rate: {vals}")
        d = res["deferred"]
        tot = next((r for r in d if r[0] == "TOTAL"), None)
        if tot:
            items = [r for r in d if r[0] != "TOTAL"]
            chk.eq(f"{tag}|deferred|out", tot[1], sum(r[1] for r in items), f"{plan} OPEB deferred outflows: items sum to TOTAL")
            chk.eq(f"{tag}|deferred|in", tot[2], sum(r[2] for r in items), f"{plan} OPEB deferred inflows: items sum to TOTAL")
            am = res["amort"]
            at = next((r for r in am if r[0].upper() == "TOTAL"), None)
            if at:
                chk.eq(f"{tag}|amort_sum", at[1], sum(r[1] for r in am if r[0].upper() != "TOTAL"), f"{plan} OPEB amortisation: years sum to TOTAL")
                net_d = tot[1] - tot[2]
                if at[1] not in (net_d, -net_d):
                    chk.eq(f"{tag}|amort_tie", at[1], net_d, f"{plan} OPEB amortisation total vs net deferred outflows")
    else:
        n = {k: v for k, v, _p in res["noo"]}
        if n:
            try:
                chk.eq(f"{tag}|noo|cost", n["annual_opeb_cost"], n["arc"] + n["interest_on_noo"] + n["arc_adjustment"], f"{plan} annual OPEB cost = ARC + interest + adjustment")
                chk.eq(f"{tag}|noo|increase", n["increase"], n["annual_opeb_cost"] - abs(n["contributions"]), f"{plan} increase in NOO = cost - contributions")
                chk.eq(f"{tag}|noo|end", n["noo_ending"], n["noo_beginning"] + n["increase"], f"{plan} NOO ending = beginning + increase")
            except KeyError as e:
                chk.fail(f"{tag}|noo|rows", f"net OPEB obligation table incomplete: missing {e}")
        f = {k: v for k, v, _u, _p in res["funded"]}
        if {"aal", "ava", "uaal"} <= set(f):
            chk.eq(f"{tag}|funded|uaal", f["uaal"], f["aal"] - f["ava"], f"{plan} UAAL = AAL - AVA")
            if "funded_ratio_pct" in f and f["aal"]:
                chk.near(f"{tag}|funded|ratio", f["funded_ratio_pct"], f["ava"] / f["aal"] * 100, 0.011, f"{plan} funded ratio = AVA / AAL")
            if "covered_payroll" in f and "uaal_pct_payroll" in f and f["covered_payroll"]:
                chk.near(f"{tag}|funded|uaal_pct", f["uaal_pct_payroll"], f["uaal"] / f["covered_payroll"] * 100, 0.011, f"{plan} UAAL % of covered payroll")
        for (y, cost, contrib, pct, noo, pg) in res["trend"]:
            if cost:
                chk.near(f"{tag}|trend|{y}|pct", pct, contrib / cost * 100, 0.011, f"{plan} {y}: % of annual OPEB cost contributed")
        tr = sorted(res["trend"])
        for a, b in zip(tr, tr[1:]):
            if b[0] == a[0] + 1:
                chk.eq(f"{tag}|trend|{b[0]}|noo", b[4], a[4] + b[1] - b[2], f"{plan} {b[0]}: NOO = previous NOO + annual cost - contributions")


# ---- OPEB RSI (multi-year) -----------------------------------------------------------------------------
OPEB_RSI_LINES = [
    (r"^service cost", "service_cost"), (r"^interest\b", "interest"), (r"chang\w* of benefit terms", "benefit_terms"),
    (r"differences? between expected", "experience"), (r"chang\w* (of|in) assumptions?", "assumptions"), (r"^benefit payments", "benefit_payments"),
    (r"^net change in total opeb liability", "net_change"), (r"^total opeb liability\s*[-–]\s*beginning", "beginning"),
    (r"^total opeb liability\s*[-–]\s*ending", "ending"), (r"^covered(-employee)? payroll", "covered_payroll"),
    (r"^employer.s total opeb liability.*percentage", "tol_pct_covered_payroll"),
]


def read_opeb_rsi(fy, source="text", prep="gray"):
    """GASB 75 RSI schedule(s): [{plan_id: 'opeb_city'|'opeb_library', column_year, line_id, value, page}] and problems."""
    texts = all_plain_text(fy)
    pages = [i for i, t in enumerate(texts, 1) if re.search(r"(?i)schedule of changes in the employer.s\s+total opeb liability", " ".join(t.split())[:500])]
    rows, problems = [], []
    if not pages:
        return dict(rows=rows, problems=problems, pages=pages)
    pl = page_lines(fy, pages, source, prep)
    for p in pages:
        lines = pl[p]
        head = " ".join(ln.text for ln in lines[:8])
        plan = "opeb_library" if re.search(r"(?i)library", head) else "opeb_city"
        hdr = next((ln for ln in lines if re.match(r"(?i)^measurement date", ln.text)), None)
        if hdr is None:
            problems.append(f"p{p}: no header row")
            continue
        yws = [w for w in hdr.words if YEAR_TOK.match(w.t)]
        years = [int(YEAR_TOK.match(w.t).group(1)) for w in yws]
        ncol = len(years)
        body, pend = [], []
        for ln in lines[lines.index(hdr) + 1:]:
            if re.match(r"(?i)^(no assets|notes to|changes in|\(see)", ln.text):
                break
            body.append(ln)
        parsed = []
        for ln in body:
            label, nums = ln.split()
            if not nums:
                if re.match(r"^TOTAL OPEB LIABILITY$", label.strip()):
                    pend = []
                elif label:
                    pend.append(label)
                continue
            parsed.append((" ".join(pend + [label]).strip(), nums))
            pend = []
        full = [r for r in parsed if len(r[1]) == ncol]
        if not full:
            problems.append(f"p{p}: no full-width row")
            continue
        anchors = [statistics.median(r[1][c][0] for r in full) for c in range(ncol)]
        for label, nums in parsed:
            lid = next((l for pat, l in OPEB_RSI_LINES if re.search(pat, label, re.I)), None)
            if lid is None:
                problems.append(f"p{p}: unrecognised OPEB RSI row {label!r}")
                continue
            cols = assign_columns(nums, anchors, tol=min(40, (anchors[1] - anchors[0]) * 0.45 if ncol > 1 else 40))
            for j in range(ncol):
                if j in cols:
                    v, k, raw = cols[j]
                    rows.append(dict(plan_id=plan, column_year=years[j], line_id=lid, value=v, kind="pct" if k == "pct" else "amount", page=p))
    return dict(rows=rows, problems=problems, pages=pages)


def validate_opeb_rsi(res, chk):
    for p in res["problems"]:
        chk.fail("opeb_rsi|read", p)
    rows = res["rows"]
    for plan in sorted({r["plan_id"] for r in rows}):
        years = sorted({r["column_year"] for r in rows if r["plan_id"] == plan})

        def g(lid, y):
            return next((r["value"] for r in rows if r["plan_id"] == plan and r["column_year"] == y and r["line_id"] == lid), None)
        for y in years:
            lines = [r["value"] for r in rows if r["plan_id"] == plan and r["column_year"] == y and r["line_id"] not in ("net_change", "beginning", "ending", "covered_payroll", "tol_pct_covered_payroll") and r["value"] is not None]
            nc, b, e = g("net_change", y), g("beginning", y), g("ending", y)
            if None in (nc, b, e):
                chk.fail(f"opeb_rsi|{plan}|{y}|rows", "net change / beginning / ending missing")
                continue
            chk.eq(f"opeb_rsi|{plan}|{y}|net_change", nc, sum(lines), f"{plan} {y}: lines sum to net change in TOL")
            chk.eq(f"opeb_rsi|{plan}|{y}|roll", e, b + nc, f"{plan} {y}: beginning + net change = ending")
            cp, pc = g("covered_payroll", y), g("tol_pct_covered_payroll", y)
            if cp and pc is not None:
                chk.near(f"opeb_rsi|{plan}|{y}|pct", pc, e / cp * 100, 0.011, f"{plan} {y}: TOL % of covered payroll")
            if y - 1 in years and g("ending", y - 1) is not None:
                chk.eq(f"opeb_rsi|{plan}|{y}|chain", b, g("ending", y - 1), f"{plan}: {y - 1} ending = {y} beginning")
