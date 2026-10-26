"""Pension disclosures: Required Supplementary Information schedules (multi-year) and the Notes (current-year roll-forward etc.).

read_rsi(fy)  -> dict(rows=[...], problems=[...])
  rows: plan_id, schedule ('npl' | 'contrib' | 'returns'), column_year (int, the header year), component ('tpl'|'fnp'|'npl'|'ratio'|'contrib'|'returns'),
        line_id, label, value (int, float for percents), kind ('amount'|'pct'), page, flag
"""
import re
import statistics

from audit_debt_pension_common import is_page_no, assign_columns, page_lines, all_plain_text

PLAN_PATTERNS = [
    (r"(?i)illinois municipal retirement fund\s*[-–]\s*(regular|city)\b|^imrf\s*[-–]\s*(regular|city)\b", "imrf_regular"),
    (r"(?i)illinois municipal retirement fund\s*[-–]\s*eco\b|^imrf\s*[-–]\s*eco\b", "imrf_eco"),
    (r"(?i)illinois municipal retirement fund\s*[-–]\s*slep\b|^imrf\s*[-–]\s*slep\b|^slep\b", "imrf_slep"),
    (r"(?i)police pension (fund|plan)", "police"),
    (r"(?i)firefighters.{0,3}\s*pension (fund|plan)", "fire"),
]


def plan_of(text):
    for pat, pid in PLAN_PATTERNS:
        if re.search(pat, text.strip()):
            return pid
    return None


RSI_TITLE = re.compile(r"(?i)schedule of (changes in the employer.s\s+(net pension liability|total opeb liability)|employer contributions|investment returns|funding progress|changes in the (total )?opeb)")


def rsi_pages(fy):
    texts = all_plain_text(fy)
    pages = []
    for i, t in enumerate(texts, 1):
        head = " ".join(t.strip().split())[:400]
        if RSI_TITLE.search(head) or re.search(r"(?i)^city of belvidere, illinois schedule of (changes in the employer|employer contributions|investment)", head):
            pages.append(i)
    # continuation pages (second page of the NPL schedule): the page after an NPL title page that starts with the MEASUREMENT DATE header
    extra = []
    for p in pages:
        if p + 1 not in pages and p + 1 <= len(texts):
            nxt = " ".join(texts[p].strip().split())[:200]
            if re.match(r"(?i)^measurement date|^fiscal year ended|^plan fiduciary", nxt):
                extra.append(p + 1)
    return sorted(set(pages + extra))


YEAR_TOK = re.compile(r"^((?:19|20)\d\d)\*?$")

NPL_LINES = [
    (r"^service cost", "tpl", "service_cost"),
    (r"^interest\b", "tpl", "interest"),
    (r"chang\w* (in|of) benefit terms", "tpl", "benefit_terms"),
    (r"differences? between expected", "tpl", "experience"),
    (r"chang\w* (of|to|in) (actuarial )?assumptions?", "tpl", "assumptions"),
    (r"^benefit payments", None, "benefit_payments"),
    (r"^net change in total pension liability", "tpl", "net_change"),
    (r"^total pension liability\s*[-–]\s*beginning", "tpl", "beginning"),
    (r"^total pension liability\s*[-–]\s*ending", "tpl", "ending"),
    (r"^contributions\s*[-–]\s*employer", "fnp", "employer_contributions"),
    (r"^contributions\s*[-–]\s*member", "fnp", "member_contributions"),
    (r"^contributions\s*[-–]\s*other", "fnp", "other_contributions"),
    (r"^net investment income", "fnp", "investment_income"),
    (r"^administrative expense", "fnp", "administrative_expense"),
    (r"^other\b", "fnp", "other"),
    (r"^net change in plan fiduciary net position", "fnp", "net_change"),
    (r"^plan fiduciary net position\s*[-–]\s*beginning", "fnp", "beginning"),
    (r"^plan fiduciary net position\s*[-–]\s*ending", "fnp", "ending"),
    (r"^employer.s net pension (liability|asset)(?!.*percent)", "npl", "ending"),
    (r"^plan fiduciary net position as a percentage", "ratio", "funded_ratio_pct"),
    (r"^covered(-employee)? payroll", "ratio", "covered_payroll"),
    (r"^employer.s net pension (liability|asset).*percentage", "ratio", "npl_pct_covered_payroll"),
]
CONTRIB_LINES = [
    (r"^actuarially determined contribution", "contrib", "adc"),
    (r"^contributions? in relation", "contrib", "contributions"),
    (r"^contribution deficiency", "contrib", "deficiency"),
    (r"^covered(-employee)? payroll", "contrib", "covered_payroll"),
    (r"^contributions as a percentage", "contrib", "contrib_pct_covered_payroll"),
]
RETURN_LINES = [(r"annual money-weighted rate of return", "returns", "money_weighted_return")]


def match_line(table, label):
    # a wrapped label ("Employer's net pension liability" / "as a percentage of covered payroll") is joined before matching
    for pat, comp, lid in table:
        if re.search(pat, label.strip(), re.I):
            return comp, lid
    return None, None


def read_rsi(fy, source="text", prep="gray"):
    pages = rsi_pages(fy)
    if not pages:
        return dict(rows=[], problems=["no RSI pages found"], pages=[])
    pl = page_lines(fy, pages, source, prep)
    rows, problems = [], []
    plan, sched = None, None
    for p in pages:
        lines = pl[p]
        texts = [ln.text for ln in lines]
        # title / plan of this page
        head = " ".join(texts[:8])
        if RSI_TITLE.search(head):
            m = RSI_TITLE.search(head).group(0).lower()
            sched = "opeb" if ("opeb" in m or "funding progress" in m or "other postemployment" in head.lower()) else "npl" if "changes" in m else "contrib" if "contributions" in m else "returns"
            pp = None
            for t in texts[:8]:
                pp = plan_of(t) or pp
            if "other postemployment" in head.lower():
                pp = "opeb_plan"
            plan = pp or plan
        if plan in (None, "opeb_plan") or sched in (None, "opeb"):
            continue
        table = NPL_LINES if sched == "npl" else CONTRIB_LINES if sched == "contrib" else RETURN_LINES
        # header row with the year columns
        hdr = next((ln for ln in lines if sum(1 for w in ln.words if YEAR_TOK.match(w.t)) >= 1 and re.match(r"(?i)^(measurement date|fiscal year ended|\d{4})", ln.text)
                    and not re.search(r"(?i)april 30, 20\d\d$", ln.text)), None)
        if hdr is None:
            # the single-year layout: a bare year above the figures
            hdr = next((ln for ln in lines if len(ln.words) <= 3 and all(YEAR_TOK.match(w.t) for w in ln.words)), None)
        if hdr is None:
            problems.append(f"p{p}: no year header ({plan}, {sched})")
            continue
        yws = [w for w in hdr.words if YEAR_TOK.match(w.t)]
        years = [int(YEAR_TOK.match(w.t).group(1)) for w in yws]
        if plan.startswith("imrf") and sched == "npl" and not re.match(r"(?i)^measurement date", hdr.text):
            years = [y - 1 for y in years]   # FY2016: the single IMRF column is headed by the fiscal year but holds the December 31 prior-year measurement
        ncol = len(years)
        start = lines.index(hdr) + 1
        body = []
        for ln in lines[start:]:
            if re.match(r"(?i)^(notes? to (the )?(required )?supplementary|notes to schedule|\*|\(see independent)", ln.text):
                break
            body.append(ln)
        # anchors from rows that carry a full set of numbers
        parsed = []
        pend = []
        comp = None
        for ln in body:
            if is_page_no(ln.text.strip()):
                continue   # page number
            label, nums = ln.split()
            if not nums:
                if re.match(r"^TOTAL PENSION LIABILITY$", label.strip()):
                    comp = "tpl"
                    pend = []
                elif re.match(r"^PLAN FIDUCIARY NET POSITION$", label.strip()):
                    comp = "fnp"
                    pend = []
                elif label:
                    pend.append(label)
                continue
            full_label = " ".join(pend + [label]).strip()
            pend = []
            parsed.append((full_label, nums, ln, comp))
        full = [r for r in parsed if len(r[1]) == ncol]
        if not full:
            problems.append(f"p{p}: no full-width row ({plan}, {sched}, {ncol} columns)")
            continue
        anchors = [statistics.median(r[1][c][0] for r in full) for c in range(ncol)]
        for full_label, nums, ln, comp0 in parsed:
            c2, lid = match_line(table, full_label)
            if lid is None:
                problems.append(f"p{p}: unrecognised row {full_label!r} ({plan}, {sched})")
                continue
            if sched == "npl":
                if c2 is None:
                    c2 = comp0 or "tpl"
                if lid == "benefit_payments":
                    c2 = comp0 or "tpl"
                if lid == "other" and comp0 == "tpl":
                    c2 = "tpl"
            cols = assign_columns(nums, anchors, tol=min(40, (anchors[1] - anchors[0]) * 0.45 if ncol > 1 else 40))
            for j in range(ncol):
                if j in cols:
                    v, k, raw = cols[j]
                    rows.append(dict(plan_id=plan, schedule=sched, column_year=years[j], component=c2 if sched == "npl" else sched, line_id=lid, label=full_label,
                                     value=v, kind="pct" if k == "pct" else "amount", page=p, flag=""))
                else:
                    rows.append(dict(plan_id=plan, schedule=sched, column_year=years[j], component=c2 if sched == "npl" else sched, line_id=lid, label=full_label,
                                     value=None, kind="blank", page=p, flag="blank_cell"))
    return dict(rows=rows, problems=problems, pages=pages)


def series(rows, plan, sched, comp, lid):
    return {r["column_year"]: r["value"] for r in rows if r["plan_id"] == plan and r["schedule"] == sched and r["component"] == comp and r["line_id"] == lid}


def validate_rsi(res, chk, tol_pct=0.06):
    """Identities within one audit's RSI schedules."""
    for p in res["problems"]:
        chk.fail("rsi|read", p)
    rows = res["rows"]
    for plan in sorted({r["plan_id"] for r in rows if r["schedule"] == "npl"}):
        years = sorted({r["column_year"] for r in rows if r["plan_id"] == plan and r["schedule"] == "npl"})
        for y in years:
            def g(comp, lid):
                return series(rows, plan, "npl", comp, lid).get(y)
            for comp in ("tpl", "fnp"):
                lines = [r for r in rows if r["plan_id"] == plan and r["schedule"] == "npl" and r["column_year"] == y and r["component"] == comp
                         and r["line_id"] not in ("net_change", "beginning", "ending") and r["value"] is not None]
                nc = g(comp, "net_change")
                if nc is None:
                    chk.fail(f"rsi|{plan}|{y}|{comp}|net_change", "no net change row")
                    continue
                chk.eq(f"rsi|{plan}|{y}|{comp}|net_change", nc, sum(r["value"] for r in lines), f"{plan} {y} {comp}: lines sum to net change")
                b, e = g(comp, "beginning"), g(comp, "ending")
                if None in (b, e):
                    chk.fail(f"rsi|{plan}|{y}|{comp}|roll", "beginning / ending missing")
                else:
                    chk.eq(f"rsi|{plan}|{y}|{comp}|roll", e, b + nc, f"{plan} {y} {comp}: beginning + net change = ending")
            tpl, fnp, npl = g("tpl", "ending"), g("fnp", "ending"), g("npl", "ending")
            if None not in (tpl, fnp, npl):
                chk.eq(f"rsi|{plan}|{y}|npl", npl, tpl - fnp, f"{plan} {y}: NPL = TPL - FNP")
            fr = g("ratio", "funded_ratio_pct")
            if fr is not None and tpl:
                chk.near(f"rsi|{plan}|{y}|funded_ratio", fr, fnp / tpl * 100, tol_pct + 0.001, f"{plan} {y}: funded ratio = FNP / TPL")
            cp, pct = g("ratio", "covered_payroll"), g("ratio", "npl_pct_covered_payroll")
            if cp and pct is not None and npl is not None:
                chk.near(f"rsi|{plan}|{y}|npl_pct", pct, npl / cp * 100, tol_pct + 0.001, f"{plan} {y}: NPL % of covered payroll")
        for y in years:
            if y - 1 in years:
                for comp in ("tpl", "fnp"):
                    e0 = series(rows, plan, "npl", comp, "ending").get(y - 1)
                    b1 = series(rows, plan, "npl", comp, "beginning").get(y)
                    if e0 is not None and b1 is not None:
                        chk.eq(f"rsi|{plan}|{y}|{comp}|chain", b1, e0, f"{plan} {comp}: {y - 1} ending = {y} beginning")
    for plan in sorted({r["plan_id"] for r in rows if r["schedule"] == "contrib"}):
        years = sorted({r["column_year"] for r in rows if r["plan_id"] == plan and r["schedule"] == "contrib"})
        for y in years:
            def g(lid):
                return series(rows, plan, "contrib", "contrib", lid).get(y)
            adc, act, defi, cp, pct = g("adc"), g("contributions"), g("deficiency"), g("covered_payroll"), g("contrib_pct_covered_payroll")
            if None not in (adc, act, defi):
                chk.eq(f"rsi|{plan}|{y}|contrib_deficiency", defi, adc - act, f"{plan} {y}: deficiency = ADC - contributions")
            if cp and act is not None and pct is not None:
                chk.near(f"rsi|{plan}|{y}|contrib_pct", pct, act / cp * 100, tol_pct + 0.001, f"{plan} {y}: contributions / covered payroll")


# ====================================================================================================================
# Notes to the financial statements: DEFINED BENEFIT PENSION PLANS
# ====================================================================================================================
PLAN_HEAD_EXACT = [
    (r"^(illinois municipal retirement fund|imrf)\s*[-\u2013]\s*(city|regular)(\s*\(continued\))?$", "imrf_regular"),
    (r"^(illinois municipal retirement fund|imrf)\s*[-\u2013]\s*eco(\s*\(continued\))?$", "imrf_eco"),
    (r"^(illinois municipal retirement fund|imrf)\s*[-\u2013]\s*slep(\s*\(continued\))?$|^slep(\s*\(continued\))?$", "imrf_slep"),
    (r"^(illinois municipal retirement fund|imrf)\s*[-\u2013]\s*(city|regular)?,?\s*(eco,?)?\s*(and\s+)?slep(\s*\(continued\))?$", "imrf_all"),
    (r"^police pension (plan|fund)(\s*\(continued\))?$", "police"),
    (r"^firefighters.{0,3}\s*pension (plan|fund)(\s*\(continued\))?$", "fire"),
    (r"^police pension plan and firefighters.{0,3}\s*pension plan(\s*\(continued\))?$", "police_fire"),
]
SECTION_KEYS = [
    (r"plan membership|membership", "membership"),
    (r"^contributions", "contributions"),
    (r"actuarial assumptions", "assumptions"),
    (r"^discount rate sensitivity|sensitivity", "sensitivity"),
    (r"^discount rate", "discount_rate"),
    (r"changes in (the )?net pension liability", "changes_npl"),
    (r"pension expense and deferred", "deferred"),
    (r"rate of return|investment rate of return", "return"),
    (r"^plan net position", "plan_net_position"),
    (r"^changes in plan net position", "changes_plan_net_position"),
]


def section_key(title):
    t = title.strip().lower()
    if re.match(r"changes in plan net position", t):
        return "changes_plan_net_position"
    for pat, key in SECTION_KEYS:
        if re.search(pat, t):
            return key
    return "other:" + t[:30]


def pension_note_pages(fy):
    texts = all_plain_text(fy)
    first = last = None
    for i, t in enumerate(texts, 1):
        if first is None:
            if re.search(r"(?m)^\s*\d+\.\s+DEFINED BENEFIT PENSION PLANS\s*$", t):
                first = i
        elif last is None:
            if re.search(r"(?m)^\s*\d+\.\s+[A-Z][A-Z ,\-']{8,}\s*$", t) and not re.search(r"(?m)^\s*\d+\.\s+DEFINED BENEFIT PENSION PLANS \(Continued\)", t):
                last = i
                break
    if first is None:
        return []
    return list(range(first, (last or first + 25) + 1))


NOTE_END = re.compile(r"^\d+\.\s+[A-Z][A-Z ,\-']{8,}$")


def sectioned_lines(fy, source="text", prep="gray"):
    """Lines of the pension note in order, each tagged with (plan, section_key, section_title)."""
    pages = pension_note_pages(fy)
    if not pages:
        return [], []
    pl = page_lines(fy, pages, source, prep)
    out, plan, sec, title = [], None, None, None
    started = False
    for p in pages:
        for ln in pl[p]:
            t = ln.text.strip()
            if is_page_no(t) or re.match(r"(?i)^(city of belvidere|notes to financial statements)", t):
                continue
            m = re.match(r"^(\d+)\.\s+(.*)$", t)
            if m and re.match(r"(?i)defined benefit pension plans", m.group(2)):
                started = True
                continue
            if not started:
                continue
            if m and NOTE_END.match(t) and not re.search(r"(?i)defined benefit", t):
                return out, pages
            # plan headings
            low = t.lower()
            hit = None
            for pat, pid in PLAN_HEAD_EXACT:
                if re.match(pat, low):
                    hit = pid
                    break
            if hit and re.match(r"(?i)^imrf\b", t):
                hit = None          # "IMRF - City   SLEP": a column header of a table, not a section heading
            if hit:
                plan = hit
                if hit in ("police", "fire", "imrf_all", "police_fire") and not re.search(r"(?i)\(continued\)", t):
                    sec, title = None, None
                continue
            lm = re.match(r"^([a-z])\.\s{1,6}(\S.*)$", t)
            if lm and not re.search(r"(?i)\(continued\)$", t) and len(t) < 110:
                title = lm.group(2)
                sec = section_key(title)
                continue
            if lm and re.search(r"(?i)\(continued\)$", t):
                title = re.sub(r"(?i)\s*\(continued\)$", "", lm.group(2))
                sec = section_key(title)
                continue
            out.append((plan, sec, title, ln))
    return out, pages


def to_iso(text):
    m = re.search(r"(?i)(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})", text)
    if not m:
        return ""
    mon = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"].index(m.group(1).lower()) + 1
    return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"


RF_LINES = [
    (r"^service cost", "service_cost"),
    (r"^interest\b", "interest"),
    (r"chang\w* (in|of) benefit terms", "benefit_terms"),
    (r"differences? between expected", "experience"),
    (r"chang\w* (in|of|to) (actuarial )?assumptions?", "assumptions"),
    (r"^employer contributions", "employer_contributions"),
    (r"^employee contributions|^member contributions", "member_contributions"),
    (r"^contributions\s*[-\u2013]\s*other", "other_contributions"),
    (r"^net investment income", "investment_income"),
    (r"^benefit payments", "benefit_payments"),
    (r"^administrative expense", "administrative_expense"),
    (r"^other\b", "other"),
    (r"^net changes?", "net_change"),
]
RF_LINES.insert(4, (r"^assumption changes|^changes in (the )?assumptions?", "assumptions"))


def parse_rollforward(lines, ncols=3):
    """lines: Lines of one roll-forward table ('BALANCES AT <date>' ... 'Net changes' ... 'BALANCES AT <date>').  Returns
    [(kind_or_line_id, [ncols values], date, page)] with kind 'beginning' / 'ending' for the two BALANCES rows."""
    rows, pend, seen = [], [], 0
    MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    for ln in lines:
        text = ln.text.strip()
        label, nums = ln.split()
        if re.match(r"(?i)^balances at\b", text):
            pend = ["BALANCES AT"]
            if len(nums) >= ncols and re.search(MONTH, text, re.I):
                rows.append(("beginning" if seen == 0 else "ending", [n[1] for n in nums[-ncols:]], to_iso(text), ln.page))
                seen += 1
                pend = []
                if seen == 2:
                    break
            continue
        if pend and pend[0] == "BALANCES AT" and re.match(r"(?i)^" + MONTH + r"\s+\d", text) and len(nums) >= ncols:
            rows.append(("beginning" if seen == 0 else "ending", [n[1] for n in nums[-ncols:]], to_iso(text), ln.page))
            seen += 1
            pend = []
            if seen == 2:
                break
            continue
        if seen == 1:
            if len(nums) >= ncols:
                lab = " ".join(pend + [label]).strip()
                pend = []
                lid = next((l for pat, l in RF_LINES if re.search(pat, lab, re.I)), None)
                rows.append((lid or ("?" + lab), [n[1] for n in nums[-ncols:]], "", ln.page))
            elif label and not re.match(r"(?i)^changes for the period", label):
                pend.append(label)
    return rows


def validate_rollforward(plan, rows, chk):
    beg = next((r for r in rows if r[0] == "beginning"), None)
    end = next((r for r in rows if r[0] == "ending"), None)
    chg = [r for r in rows if r[0] not in ("beginning", "ending", "net_change")]
    net = next((r for r in rows if r[0] == "net_change"), None)
    if beg is None or end is None or net is None:
        chk.fail(f"rf|{plan}|rows", "note roll-forward: missing beginning / ending / net changes row")
        return
    for r in chg:
        if r[0].startswith("?"):
            chk.fail(f"rf|{plan}|{r[0]}", f"unrecognised roll-forward line {r[0]!r}")
    for j, comp in enumerate(("tpl", "fnp", "npl")):
        chk.eq(f"rf|{plan}|{comp}|net_change", net[1][j], sum(r[1][j] for r in chg), f"{plan} note roll-forward {comp}: lines sum to net changes")
        chk.eq(f"rf|{plan}|{comp}|roll", end[1][j], beg[1][j] + net[1][j], f"{plan} note roll-forward {comp}: beginning + net = ending")
    for nm, r in [("beginning", beg), ("ending", end), ("net_change", net)] + [(r[0], r) for r in chg]:
        v = r[1]
        chk.eq(f"rf|{plan}|{nm}|npl_col", v[2], v[0] - v[1], f"{plan} note roll-forward {nm}: NPL column = TPL - FNP")


def note_rollforwards(items):
    """{plan: rows} from the 'changes in the net pension liability' sections."""
    byplan = {}
    for plan, sec, title, ln in items:
        if sec == "changes_npl" and plan:
            byplan.setdefault(plan, []).append(ln)
    return {plan: parse_rollforward(lines) for plan, lines in byplan.items()}


# ---- the other note tables ---------------------------------------------------------------------------
def sec_lines(items, plan, sec):
    return [ln for (pl, sc, _t, ln) in items if pl == plan and sc == sec]


def sec_text(items, plan, sec):
    return " ".join(re.sub(r"\s+", " ", ln.text) for ln in sec_lines(items, plan, sec))


def pct_list(text):
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]


def read_membership(items):
    """-> [(plan_id, class, count, page)] and problems (sum of classes = TOTAL)."""
    rows = []
    for plan_key in ("imrf_all", "police", "fire"):
        lines = sec_lines(items, plan_key, "membership")
        if not lines:
            continue
        hdr = next((ln for ln in lines if re.search(r"(?i)imrf\s*[-\u2013]\s*(city|regular)|\bSLEP\b", ln.text) and not ln.split()[1]), None)
        plans = [plan_key]
        if plan_key == "imrf_all":
            plans = []
            txt = hdr.text if hdr else ""
            for pat, pid in (("city|regular", "imrf_regular"), ("eco", "imrf_eco"), ("slep", "imrf_slep")):
                m = re.search(r"(?i)\b(" + pat + r")\b", txt)
                if m:
                    plans.append((m.start(), pid))
            plans = [pid for _x, pid in sorted(plans)]
        n = len(plans)
        pend = []
        for ln in lines:
            label, nums = ln.split()
            if len(nums) >= n and nums and not re.match(r"(?i)^at (december|april)", ln.text):
                lab = " ".join(pend + [label]).strip()
                pend = []
                cls = ("total" if re.search(r"(?i)^total", lab) else
                       "active" if re.search(r"(?i)(?<!in)active\b", lab) else
                       "terminated_vested" if re.search(r"(?i)entitled|not yet", lab) else
                       "retirees" if re.search(r"(?i)receiving", lab) else None)
                if cls is None:
                    continue
                vals = [v[1] for v in nums[-n:]]
                for pid, v in zip(plans, vals):
                    rows.append((pid, cls, v, ln.page))
            elif label and not re.match(r"(?i)^(at |imrf|slep|membership)", label):
                pend.append(label)
    return rows


def validate_membership(rows, chk):
    plans = {r[0] for r in rows}
    for pid in plans:
        d = {r[1]: r[2] for r in rows if r[0] == pid}
        if "total" not in d:
            chk.fail(f"mem|{pid}|total", "no TOTAL row")
            continue
        chk.eq(f"mem|{pid}|total", d["total"], sum(v for k, v in d.items() if k != "total"), f"{pid} membership: classes sum to TOTAL")


def read_sensitivity(items):
    """-> [(plan, rate_lo, rate_cur, rate_hi, npl_lo, npl_cur, npl_hi, page)]"""
    out = []
    for plan in sorted({pl for (pl, sc, _t, _l) in items if sc == "sensitivity" and pl}):
        lines = sec_lines(items, plan, "sensitivity")
        rates = []
        for ln in lines:
            ps = [t for t in ln.tokens() if t[1] == "pct"]
            if len(ps) == 3 and not ln.split()[1][-1:] == [] and re.match(r"^\(?[\d.]+%\)?", ln.words[0].t):
                rates = [abs(t[2]) for t in ps]
        for ln in lines:
            label, nums = ln.split()
            if re.match(r"(?i)^net pension (liability|asset)", label) and len(nums) >= 3:
                v = [n[1] for n in nums[-3:]]
                out.append((plan, *(rates or [None, None, None]), *v, ln.page))
    return out


def validate_sensitivity(rows, ratecur, chk):
    for r in rows:
        plan = r[0]
        if r[1] is not None:
            chk.near(f"sens|{plan}|rates", r[3] - r[1], 2.0, 0.011, f"{plan} sensitivity: high - low discount rate = 2 points")
        if not (r[4] >= r[5] >= r[6]):
            chk.fail(f"sens|{plan}|order", f"{plan} sensitivity NPL not decreasing with the discount rate: {r[4:7]}")


def read_deferred(items):
    """-> deferred rows [(plan, source, outflow, inflow, page)], amortisation [(plan, label, amount, page)], expense [(plan, amount)]"""
    d_rows, am_rows, expense = [], [], []
    for plan in sorted({pl for (pl, sc, _t, _l) in items if sc == "deferred" and pl and pl != "imrf_all"}):
        lines = sec_lines(items, plan, "deferred")
        phase, pend = 0, []
        for ln in lines:
            label, nums = ln.split()
            t = ln.text
            if re.search(r"(?i)recognized in pension expense as follows|^expense as follows|recognized in pension expense as\s*$|^(year ending)", t) and not nums:
                phase, pend = 1, []
                continue
            if phase == 0:
                if len(nums) >= 2 and not re.search(r"(?i)^(year|\d{4})", t) and not re.search(r"(?i)^at (december|april)", t):
                    lab = " ".join(pend + [label]).strip()
                    pend = []
                    if re.match(r"(?i)^total", lab):
                        d_rows.append((plan, "TOTAL", nums[-2][1], nums[-1][1], ln.page))
                    else:
                        src = ("experience" if re.search(r"(?i)expected and actual|experience", lab) else "assumptions" if re.search(r"(?i)assumption", lab) else
                               "investment_earnings" if re.search(r"(?i)earnings|investments", lab) else "contributions_after_measurement_date" if re.search(r"(?i)contributions made", lab) else "other:" + lab[:40])
                        d_rows.append((plan, src, nums[-2][1], nums[-1][1], ln.page))
                elif label and not re.search(r"(?i)^(deferred|outflows|inflows|resources|for the year|at (december|april)|the city reported|following sources|sources:|changes in the net pension|experience, or|and retired|measurement period)", label):
                    pend.append(label)
                else:
                    pend = []
            else:
                if nums and (re.match(r"^(20\d\d)\b", t) or re.match(r"(?i)^(thereafter|total)", label)) :
                    lab = label if label else t.split()[0]
                    m = re.match(r"^(20\d\d)\b", t)
                    if m:
                        lab = m.group(1)
                        amt = nums[-1][1]
                        if len(nums) >= 2 and nums[0][1] == int(lab):
                            amt = nums[-1][1]
                    else:
                        amt = nums[-1][1]
                    am_rows.append((plan, lab, amt, ln.page))
        text = " ".join(re.sub(r"\s+", " ", l.text) for l in lines)
        m = re.search(r"pension (expense|income)( \(income\))? of \$?\s*(\(?[\d,]+\)?)", text, re.I)
        if m:
            v = int(re.sub(r"[^\d]", "", m.group(3)))
            expense.append((plan, -v if "(" in m.group(3) or m.group(1).lower() == "income" else v, lines[0].page))
    return d_rows, am_rows, expense


def validate_deferred(d_rows, am_rows, chk):
    for plan in {r[0] for r in d_rows}:
        rows = [r for r in d_rows if r[0] == plan]
        tot = next((r for r in rows if r[1] == "TOTAL"), None)
        items = [r for r in rows if r[1] != "TOTAL"]
        if tot is None:
            chk.fail(f"def|{plan}|total", "no TOTAL row in the deferred outflows / inflows table")
            continue
        chk.eq(f"def|{plan}|outflows", tot[2], sum(r[2] for r in items), f"{plan} deferred outflows: items sum to TOTAL")
        chk.eq(f"def|{plan}|inflows", tot[3], sum(r[3] for r in items), f"{plan} deferred inflows: items sum to TOTAL")
        am = [r for r in am_rows if r[0] == plan]
        at = next((r for r in am if r[1].upper() == "TOTAL"), None)
        if at is not None:
            chk.eq(f"def|{plan}|amortization_sum", at[2], sum(r[2] for r in am if r[1].upper() != "TOTAL"), f"{plan} amortisation schedule: years sum to TOTAL")
            net = tot[2] - tot[3]
            contrib = sum(r[2] for r in items if r[1] == "contributions_after_measurement_date")
            if at[2] in (net, net - contrib):
                pass
            elif at[2] in (-net, -(net - contrib)):
                chk.accepted.append(dict(key=f"def|{plan}|amortization_sign", desc=f"{plan}: amortisation schedule is printed with the opposite sign to net deferred outflows (inflows)", printed=at[2], computed=net - contrib, diff=""))
            else:
                chk.eq(f"def|{plan}|amortization_tie", at[2], net, f"{plan} amortisation total vs net deferred outflows (inflows) {net:,} (excluding post-measurement contributions {net - contrib:,})")


def read_allocation(items):
    """IMRF totals reported in the basic financial statements as follows: [(plan, topic, row, amount, page)]"""
    out = []
    for plan in sorted({pl for (pl, sc, _t, _l) in items if pl}):
        lines = [ln for (pl, sc, _t, ln) in items if pl == plan and sc in ("changes_npl", "deferred")]
        topic, cur, pend = None, [], []
        for ln in lines:
            t = re.sub(r"\s+", " ", ln.text)
            if re.search(r"(?i)reported in the basic financial statements", t) or re.search(r"(?i)^financial statements as follows", t):
                topic = "net_pension_liability" if re.search(r"(?i)net pension (liability|asset)", " ".join(x.text for x in lines[max(0, lines.index(ln) - 3):lines.index(ln) + 1])) else "net_deferred"
                if re.search(r"(?i)deferred outflows", " ".join(x.text for x in lines[max(0, lines.index(ln) - 3):lines.index(ln) + 1])):
                    topic = "net_deferred"
                pend = []
                continue
            if topic:
                label, nums = ln.split()
                if nums:
                    lab = " ".join(pend + [label]).strip()
                    pend = []
                    key = ("governmental" if re.match(r"(?i)^governmental", lab) else "business_type" if re.match(r"(?i)^business", lab) else
                           "component_unit_library" if re.search(r"(?i)discretely|library|component", lab) else "total" if re.match(r"(?i)^total", lab) else None)
                    if key:
                        out.append((plan, topic, key, nums[-1][1], ln.page))
                        if key == "total":
                            topic = None
                elif label and re.match(r"(?i)^(discretely|public library)", label):
                    pend.append(label)
    return out


def validate_allocation(rows, chk):
    for plan, topic in {(r[0], r[1]) for r in rows}:
        rr = [r for r in rows if r[0] == plan and r[1] == topic]
        tot = next((r for r in rr if r[2] == "total"), None)
        if tot is None:
            chk.fail(f"alloc|{plan}|{topic}", "no TOTAL in allocation table")
            continue
        chk.eq(f"alloc|{plan}|{topic}", tot[3], sum(r[3] for r in rr if r[2] != "total"), f"IMRF {plan} {topic}: governmental + business-type + library = TOTAL")


KV_PATTERNS = [
    (r"^actuarial valuation date\s+(.*)$", "valuation_date"),
    (r"^actuarial cost method\s+(.*)$", "cost_method"),
    (r"^inflation\s+(.*)$", "inflation"),
    (r"^salary increases?\s+(.*)$", "salary_increases"),
    (r"^(interest rate|investment rate of return)\s+(.*)$", "interest_rate"),
    (r"^cost of living adjustments?\s+(.*)$", "cola"),
    (r"^asset valuation method\s+(.*)$", "asset_valuation"),
]


def read_text_facts(items, plans_seen):
    """Key facts from the note text per plan -> [(plan, measure, value, text, unit, page)]"""
    out = []
    for plan in plans_seen:
        # assumptions (IMRF: one block for the plans together)
        akey = "imrf_all" if plan.startswith("imrf") else plan
        for ln in sec_lines(items, akey, "assumptions"):
            t = re.sub(r"\s+", " ", ln.text).strip()
            for pat, meas in KV_PATTERNS:
                m = re.match(pat, t, re.I)
                if m:
                    val = m.group(m.lastindex).strip()
                    pcts = pct_list(val)
                    out.append((plan, "assump_" + meas, pcts[-1] if (pcts and meas in ("inflation", "interest_rate")) else (pcts[0] if pcts and meas == "cola" else None), val, "pct" if pcts else "", ln.page))
                    break
        # discount rate
        txt = sec_text(items, plan, "discount_rate")
        m = re.search(r"discount rate used to measure the total pension liability[^.]*?\bwas\s+(\d+(?:\.\d+)?)\s*%", txt, re.I)
        if m:
            out.append((plan, "discount_rate", float(m.group(1)), "", "pct", 0))
        m = re.search(r"long-term expected rate of return on pension plan investments of (\d+(?:\.\d+)?)%.*?index rate of (\d+(?:\.\d+)?)%", txt, re.I)
        if m:
            out.append((plan, "blended_expected_return", float(m.group(1)), "", "pct", 0))
            out.append((plan, "blended_index_rate", float(m.group(2)), "", "pct", 0))
        if re.search(r"not projected to be available|was not projected", txt, re.I):
            out.append((plan, "plan_assets_projected_sufficient", 0, "fiduciary net position was NOT projected to cover all future benefit payments (blended rate used)", "flag", 0))
        # contributions
        ckey = "imrf_all" if plan.startswith("imrf") else plan
        ctxt = sec_text(items, ckey, "contributions")
        if plan in ("police", "fire"):
            m = re.search(r"contribut\w* (?:was|were) (\d+(?:\.\d+)?)% of covered(?:-employee)? payroll", ctxt, re.I)
            if m:
                out.append((plan, "employer_contribution_pct_payroll", float(m.group(1)), "", "pct", 0))
            m = re.search(r"to contribute (\d+(?:\.\d+)?)% of (?:their )?(?:base )?salary", ctxt, re.I)
            if m:
                out.append((plan, "employee_contribution_rate", float(m.group(1)), "", "pct", 0))
        else:
            m = re.search(r"required to contribute (\d+(?:\.\d+)?)% and (\d+(?:\.\d+)?)%", ctxt, re.I)
            if m and plan in ("imrf_regular", "imrf_slep"):
                out.append((plan, "employee_contribution_rate", float(m.group(1 if plan == "imrf_regular" else 2)), "", "pct", 0))
            m = re.search(r"employer contribution for (?:the )?(IMRF\s*-\s*City|SLEP)[^.]*?(?:was|were) ([\d.]+%(?: and [\d.]+%)?)", ctxt, re.I)
            for mm in re.finditer(r"employer contribution for (?:the )?(IMRF\s*-\s*City|SLEP|IMRF\s*-\s*ECO)[^%]*?was ([\d.]+)%(?: and ([\d.]+)%)?", ctxt, re.I):
                pid = "imrf_regular" if "city" in mm.group(1).lower() else "imrf_slep" if "slep" in mm.group(1).lower() else "imrf_eco"
                if pid == plan:
                    val = float(mm.group(3) or mm.group(2))
                    out.append((plan, "employer_contribution_pct_payroll", val, mm.group(0)[:160], "pct", 0))
        # rate of return
        rtxt = sec_text(items, plan, "return")
        m = re.search(r"money-weighted rate of return[^%]*?(?:was|of)\s+\(?(-?\d+(?:\.\d+)?)\)?\s*%", rtxt, re.I)
        if m:
            out.append((plan, "money_weighted_return", float(m.group(1)), "", "pct", 0))
        # funded status sentence
        m = re.search(r"funded status of the plan[^%]*?is (\d+(?:\.\d+)?)%", sec_text(items, plan, "changes_npl"), re.I)
        if m:
            out.append((plan, "funded_status_pct_text", float(m.group(1)), "", "pct", 0))
    return out


if __name__ == "__main__":
    import sys
    fy = int(sys.argv[1])
    r = read_rsi(fy)
    print(r["pages"])
    for p in validate_rsi(r):
        print("  !", p)
    print(len(r["rows"]))
