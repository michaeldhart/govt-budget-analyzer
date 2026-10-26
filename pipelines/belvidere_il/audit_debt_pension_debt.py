"""Long-term debt note: activity tables, issue terms, debt service to maturity (FY2016-FY2026 layout; FY2012-FY2015 has its own readers).

read_debt(fy, source) -> dict(activity=[...], issues=[...], schedule=[...], problems=[...], pages=[...])

activity rows : activity, line (as printed, footnote marks removed), line_id, beginning, additions, reductions, ending, current, page
issue rows    : activity, issue, description, rate_text, maturity_text, ending, current, page
schedule rows : activity, group_idx, group_header, year_label, principal, interest, total, page, kind (year|total)
"""
import re
import statistics

from audit_debt_pension_common import is_page_no, assign_columns, page_lines, all_plain_text

LINE_IDS = [
    (r"mortgage", "mortgage_loan"),
    (r"revolving loan", "revolving_loan"),
    (r"debt certificates?", "debt_certificates"),
    (r"tax increment", "tif_note"),
    (r"capital lease", "capital_lease"),
    (r"installment contract", "installment_contract"),
    (r"landfill", "landfill_postclosure"),
    (r"net pension.*imrf", "npl_imrf"),
    (r"net pension.*slep", "npl_slep"),
    (r"net pension.*police", "npl_police"),
    (r"net pension.*fire", "npl_fire"),
    (r"net pension", "net_pension_obligation"),
    (r"compensated absences", "compensated_absences"),
    (r"post-?employment benefit|opeb", "opeb"),
    (r"waterworks|revenue (refunding )?bonds|alternate revenue", "revenue_bonds"),
    (r"iepa", "iepa_loans"),
    (r"asset retirement", "asset_retirement"),
    (r"premium", "bond_premium"),
    (r"general obligation", "go_bonds"),
    (r"lease", "lease"),
]


def line_id(label):
    for pat, lid in LINE_IDS:
        if re.search(pat, label, re.I):
            return lid
    return ""


def strip_marks(label):
    label = re.sub(r"\s*\(\d\)", "", label)
    label = re.sub(r"[*+†‡]+", "", label)
    label = re.sub(r"\s+", " ", label).strip(" -:")
    return label


def is_furniture(text):
    t = text.strip()
    if is_page_no(t):
        return True
    return bool(re.match(r"(?i)^(city of belvidere|notes to (the )?financial statements)", t))


def region_pages(fy):
    """Pdf pages of the long-term debt note: from its '4. LONG-TERM DEBT' heading to the 'Legal Debt Margin' end."""
    texts = all_plain_text(fy)
    first = last = None
    for i, t in enumerate(texts, 1):
        if first is None:
            if re.search(r"(?m)^\s*(\d+\.)\s+LONG-TERM (DEBT|LIABILITIES)\b", t) and re.search(r"(?i)notes to (the )?financial statements", t[:600]):
                first = i
        elif last is None and re.search(r"(?mi)^\s*\d+\.\s+RISK MANAGEMENT|^\s*\d+\.\s+(CONTINGENT|DEFINED BENEFIT|JOINT|COMMITMENT)", t):
            last = i
    if first is None:
        return []
    last = last or first + 12
    return list(range(first, last + 1))


def read_debt(fy, source="text", prep="gray"):
    pages = region_pages(fy)
    if not pages:
        return dict(activity=[], issues=[], schedule=[], problems=["debt note pages not found"], pages=[])
    pl = page_lines(fy, pages, source, prep)
    lines = [ln for p in pages for ln in pl[p] if not is_furniture(ln.text)]
    return parse_debt(lines, pages)


TRIG_ACT = re.compile(r"(?i)long-term (liability|debt|liabilities) activity|changes in long-term liabilit")
TRIG_ISS = re.compile(r"(?i)comprise the following|comprised of the following|long-term liabilities payable from")
TRIG_SCH = re.compile(r"(?i)annual (debt service )?requirements to (amortize|maturity)|debt service requirements to maturity")
END_SECT = re.compile(r"(?i)^(legal debt margin|c\.\s+legal|landfill (post)?closure|the city (issued|entered)|asset retirement|iepa loans? #l\d\d-\d+ \(|the (brownfield|tax increment))")


def split_blocks(lines):
    """[(kind, act, [Line])] in order.  The activity type (governmental / business_type) comes from the nearest heading."""
    blocks, act, cur = [], None, None
    for ln in lines:
        t = ln.text
        m_act = None
        if re.search(r"(?i)governmental activities", t) and re.search(r"(?i)long-term|annual|payable from|schedule|^b?\.? ?long", t):
            m_act = "governmental"
        if re.search(r"(?i)business-?type activities", t) and re.search(r"(?i)long-term|annual|payable from|schedule", t):
            m_act = "business_type"
        kind = "act" if TRIG_ACT.search(t) else "iss" if TRIG_ISS.search(t) else "sch" if (TRIG_SCH.search(t) and not re.search(r"(?i)\(continued\)", t)) else None
        if kind:
            if m_act:
                act = m_act
            if cur is not None and cur[0] == kind and not cur[2]:
                cur[1] = act      # a heading line followed by its own introductory sentence
            else:
                cur = [kind, act, []]
                blocks.append(cur)
            continue
        if m_act and re.match(r"(?i)^[a-c]\.\s", t):
            act = m_act
            if cur is not None and cur[0] in ("act", "iss", "sch"):
                pass
            continue
        if cur is not None:
            cur[2].append(ln)
    return blocks


def parse_debt(lines, pages):
    blocks = split_blocks(lines)
    activity, issues, schedule, problems = [], [], [], []
    iss_text = {}
    for kind, act, bl in blocks:
        if kind == "act":
            activity.extend(parse_activity(act, bl, problems))
        elif kind == "iss":
            issues.extend(parse_issues(act, bl))
            iss_text[act] = iss_text.get(act, "") + " " + " ".join(ln.text for ln in bl)
        else:
            schedule.extend(parse_schedule(act, bl, problems))
    return dict(activity=activity, issues=issues, schedule=schedule, problems=problems, pages=pages, iss_text=iss_text)


HEADER_ONLY = re.compile(r"(?i)^(governmental|business-?type|activities|governmental activities|business-?type activities)$")
COLHDR = re.compile(r"(?i)^(may 1|april 30|additions|current|portion|reductions|beginning|ending|balances?|refundings|deductions|retirements|issuances|amounts|due within|one year)\b")


def parse_activity(act, bl, problems):
    rows, pend = [], []
    for ln in bl:
        label, nums = ln.split()
        if len(nums) >= 4 and re.match(r"(?i)^(.*)", label):
            # drop footnote marks glued as numbers "(1) (4)" left of the 5 value columns
            lab = " ".join(pend + [label]).strip()
            rows.append(dict(label=lab, nums=nums, page=ln.page))
            pend = []
            if re.match(r"(?i)^total\b", strip_marks(label)) and (re.search(r"(?i)total", lab)):
                break
        else:
            if not label:
                continue
            if HEADER_ONLY.match(label.strip()) or COLHDR.match(label) or re.search(r"(?i)was as follows|for the year ended|^\d{4},? ?(was|were)", label):
                continue
            if label.startswith("*") or re.match(r"^\(\d\)", label) or re.match(r"(?i)^these (liabilities|amounts)", label):
                break
            pend.append(label)
    full = [r for r in rows if len(r["nums"]) >= 5]
    anchors = [statistics.median(r["nums"][-5:][c][0] for r in full) for c in range(5)] if full else []
    out = []
    for r in rows:
        nums = r["nums"]
        if len(nums) >= 5:
            vals = [nv[1] for nv in nums[-5:]]
            flag = ""
        elif anchors:
            cols = assign_columns(nums, anchors)
            vals = [cols[c][0] if c in cols else 0 for c in range(5)]
            flag = "blank_cells_zero"
        else:
            vals, flag = [nv[1] for nv in nums] + [None] * (5 - len(nums)), "short_row"
        label = strip_marks(r["label"])
        label = re.sub(r"(?i)^(governmental activities|business-type activities|activities)\s+", "", label)
        tot = bool(re.match(r"(?i)^total\b", label))
        out.append(dict(activity=act, line=label, line_id=("total" if tot else line_id(label)), beginning=vals[0], additions=vals[1], reductions=vals[2],
                        ending=vals[3], current=vals[4], page=r["page"], flag=flag))
    return out


def parse_issues(act, bl):
    """The 'comprise the following' table: a wrapped description whose last line carries two right-aligned numbers (balance, current
    portion), ending in a TOTAL row.  Numbers are only taken at the TOTAL row's column positions (dates and years in the text are not)."""
    tot = next((ln for ln in bl if re.match(r"(?i)^total\b", ln.text) and len(ln.split()[1]) >= 1), None)
    if tot is None:
        return []          # narrative only (no table)
    anchors = [x1 for (x1, _v, _k, _r) in tot.split()[1]]
    out, desc = [], []
    for ln in bl:
        label, nums = ln.split()
        if re.match(r"(?i)^total\b", ln.text):
            vals = [v for (_x, v, _k, _r) in nums]
            out.append(dict(activity=act, issue="TOTAL", description="", rate_text="", maturity_text="", ending=vals[0] if vals else None,
                            current=vals[1] if len(vals) > 1 else None, page=ln.page))
            break
        if re.match(r"(?i)^(issue|balances|current|total|portion|april 30)\b", ln.text) and not any(abs(x1 - anchors[-1]) < 12 for (x1, *_r) in nums):
            continue
        cols = assign_columns([n for n in nums], anchors, tol=14)
        text_words = [w.t for w in ln.words if not any(abs(w.x1 - anchors[j]) < 14 and classify_num(w.t) for j in range(len(anchors)))]
        label = " ".join(text_words).replace("$", "").strip()
        if cols:
            vals = [cols[j][0] if j in cols else None for j in range(len(anchors))]
            if label:
                desc.append(label)
            text = " ".join(desc)
            rate = re.findall(r"\d+(?:\.\d+)?\s?%(?:\s*(?:to|-)\s*\d+(?:\.\d+)?\s?%)?", text)
            if re.search(r"(?i)non-interest", text):
                rate = rate or ["0% (non-interest-bearing)"]
            mat = re.findall(r"through\s+((?:[A-Z][a-z]+\.? \d{1,2},? )?\d{4})", text)
            first = re.split(r"\s(?=due |dated |IEPA loan payable|to provide|payable;|;)", text)[0]
            out.append(dict(activity=act, issue=strip_marks(first)[:140], description=strip_marks(text), rate_text="; ".join(rate), maturity_text=mat[0] if mat else "",
                            ending=vals[0], current=vals[1] if len(vals) > 1 else None, page=ln.page))
            desc = []
        elif label:
            desc.append(label)
    return out


def classify_num(t):
    from audit_debt_pension_common import classify
    return classify(t)[0] in ("num", "dash", "cur")


HDR_SKIP = re.compile(r"(?i)^(year|ending|fiscal|april|30,?|principal|interest|total|governmental|business-?type|activities|payable|annual|requirements|are|as|follows:?|the|to|maturity|debt|service|including|of|:|note|\(continued\)|continued)$")


def parse_schedule(act, bl, problems):
    hdr, rows = [], []
    for ln in bl:
        label, nums = ln.split()
        m = re.match(r"^((?:19|20)\d\d)(?:\s*[-–]\s*((?:19|20)\d\d))?\b", ln.text)
        if m or re.match(r"(?i)^total\b", ln.text):
            toks = nums[:]
            if m and toks and toks[0][2] == "num" and 1900 < toks[0][1] < 2100 and not label:
                toks = toks[1:]
                if m.group(2) and toks and toks[0][2] == "num" and 1900 < toks[0][1] < 2100:
                    toks = toks[1:]
            rows.append((ln, (m.group(0).replace(" ", "") if m else "TOTAL"), toks))
            if not m:
                break
        elif not rows:
            hdr.append(ln)
        else:
            if re.match(r"(?i)^(the |c\.|b\.|legal|landfill)", ln.text):
                break
    if not rows:
        return []
    width = max(len(r[2]) for r in rows)
    wide = [r for r in rows if len(r[2]) == width]
    anchors = [statistics.median(r[2][c][0] for r in wide) for c in range(width)]
    page_anchors = {}
    for pg in {r[0].page for r in rows}:   # the table is printed again on the next page, slightly shifted
        w2 = [r for r in wide if r[0].page == pg]
        page_anchors[pg] = [statistics.median(r[2][c][0] for r in w2) for c in range(width)] if w2 else anchors
    per = 3
    if width % 3 and width % 2 == 0:
        per = 2     # the library's schedule prints principal and interest only
    elif width % 3:
        problems.append(f"schedule {act}: {width} columns, not a multiple of 3 or 2")
    ng = max(1, round(width / per))
    names = []
    for g in range(ng):
        cols = anchors[g * per:(g + 1) * per]
        lo, hi = min(cols) - 60, max(cols) + 8
        words = []
        for hl in hdr:
            for w in hl.words:
                if lo <= (w.x0 + w.x1) / 2 <= hi and not HDR_SKIP.match(w.t.strip(",")) and not re.match(r"^\d", w.t):
                    words.append(w.t)
        names.append(" ".join(words))
    out = []
    for ln, ylabel, nums in rows:
        cols = assign_columns(nums, page_anchors[ln.page], tol=16)
        for g in range(ng):
            vals = [cols[g * per + c][0] if (g * per + c) in cols else None for c in range(per)]
            if all(v is None for v in vals):
                continue
            flag = ""
            if per == 2:
                vals = vals + [(vals[0] or 0) + (vals[1] or 0)]
                flag = "total_derived"
            out.append(dict(activity=act, group_idx=g, group_header=names[g], year_label=ylabel, principal=vals[0], interest=vals[1], total=vals[2],
                            page=ln.page, kind="total" if ylabel == "TOTAL" else "year", flag=flag))
    return out


DEBT_TYPES = {"mortgage_loan", "revolving_loan", "debt_certificates", "tif_note", "capital_lease", "installment_contract", "revenue_bonds", "iepa_loans", "go_bonds",
              "bond_premium", "lease"}


def add_issue_gaps(res):
    """The 'comprise the following' table lists only loans with a payment schedule: IEPA draws on loans not yet fully disbursed are in the
    activity table but not in the issue table.  The difference is added as a derived issue row (flag derived_gap) so the table ties."""
    acts = {}
    for r in res["activity"]:
        acts.setdefault(r["activity"], []).append(r)
    for act in {i["activity"] for i in res["issues"]}:
        its = [i for i in res["issues"] if i["activity"] == act]
        tot = next((i for i in its if i["issue"] == "TOTAL"), None)
        if tot is None:
            continue
        lines = [r for r in acts.get(act, []) if r["line_id"] != "total"]
        dsum = sum(r["ending"] or 0 for r in lines if r["line_id"] in DEBT_TYPES)
        dcur = sum(r["current"] or 0 for r in lines if r["line_id"] in DEBT_TYPES)
        if tot["ending"] is not None and 0 < dsum - tot["ending"] and any(r["line_id"] in ("revolving_loan", "iepa_loans") for r in lines) \
                and not any(tot["ending"] == x for x in (dsum, dsum + sum(r["ending"] or 0 for r in lines if r["line_id"] == "landfill_postclosure"))):
            narr = re.findall(r"\(\$([\d,]+)\)", re.sub(r"\s+", " ", res.get("iss_text", {}).get(act, "")).split("not fully")[0][-400:]) if "not fully" in res.get("iss_text", {}).get(act, "") else []
            narr_amt = sum(int(x.replace(",", "")) for x in narr)
            note = f"; the note's own amounts for the undisbursed loans: {narr_amt:,}" + (" (equals the gap)" if narr_amt == dsum - tot["ending"] else " (differs from the gap)") if narr else ""
            res["issues"].append(dict(activity=act, issue="(derived) IEPA loan draws without a payment schedule", description="activity-table balance less the issue table's TOTAL; "
                                      "the note says the IEPA loans #L17-3623 / #L17-4188 were not fully disbursed so no payment schedule is printed" + note,
                                      rate_text="", maturity_text="", ending=dsum - tot["ending"], current=dcur - (tot["current"] or 0), page=tot["page"], flag="derived_gap"))
    return res


def validate_debt(res, chk):
    """Identities that must hold in one audit's long-term debt note (problems go to the Checker)."""
    for p in res["problems"]:
        chk.fail("debt|read", p)
    acts = {}
    for r in res["activity"]:
        acts.setdefault(r["activity"], []).append(r)
    for act, rows in acts.items():
        lines = [r for r in rows if r["line_id"] != "total"]
        tots = [r for r in rows if r["line_id"] == "total"]
        for r in lines:
            if None in (r["beginning"], r["additions"], r["reductions"], r["ending"]):
                chk.fail(f"debt|{act}|{r['line']}|cells", "missing cell")
                continue
            chk.eq(f"debt|{act}|{r['line']}|rollforward", r["ending"], r["beginning"] + r["additions"] - r["reductions"], f"{act} {r['line']!r}: beginning + additions - reductions = ending")
        if not tots:
            chk.fail(f"debt|{act}|TOTAL", "no TOTAL row")
            continue
        t = tots[-1]
        for col in ("beginning", "additions", "reductions", "ending", "current"):
            if t[col] is not None:
                chk.eq(f"debt|{act}|TOTAL|{col}", t[col], sum(r[col] or 0 for r in lines), f"{act} TOTAL {col}: sum of lines")
        r2 = [(t["beginning"] or 0) + (t["additions"] or 0) - (t["reductions"] or 0)]
        chk.eq(f"debt|{act}|TOTAL|rollforward", t["ending"], r2[0], f"{act} TOTAL beginning + additions - reductions")
    for act in {i["activity"] for i in res["issues"]}:
        its = [i for i in res["issues"] if i["activity"] == act]
        items = [i for i in its if i["issue"] != "TOTAL" and i.get("flag") != "derived_gap"]
        tot = next((i for i in its if i["issue"] == "TOTAL"), None)
        if tot is None:
            continue
        for col in ("ending", "current"):
            chk.eq(f"debt|{act}|issues|{col}", tot[col], sum(i[col] or 0 for i in items), f"{act} issue table {col}: items sum to TOTAL")
        lines = [r for r in acts.get(act, []) if r["line_id"] != "total"]
        t = next((r for r in acts.get(act, []) if r["line_id"] == "total"), None)
        dsum = sum(r["ending"] or 0 for r in lines if r["line_id"] in DEBT_TYPES)
        options = {dsum, dsum + sum(r["ending"] or 0 for r in lines if r["line_id"] == "landfill_postclosure"), dsum + sum(r["ending"] or 0 for r in lines if r["line_id"] in ("landfill_postclosure", "asset_retirement"))}
        if t is not None:
            options.add(t["ending"])
        gap = sum(i["ending"] for i in its if i.get("flag") == "derived_gap")
        real = tot["ending"] - 0
        if not any(abs((tot["ending"] + gap) - o) == 0 for o in options):
            chk.fail(f"debt|{act}|issues|tie", f"{act} issue table TOTAL {tot['ending']:,} (+ undisbursed-loan gap {gap:,}) ties to neither the debt lines ({dsum:,}) nor the activity total")
    groups = {}
    for r in res["schedule"]:
        groups.setdefault((r["activity"], r["group_idx"]), []).append(r)
    for (act, g), rows in groups.items():
        yrs = [r for r in rows if r["kind"] == "year"]
        tot = next((r for r in rows if r["kind"] == "total"), None)
        hdr = rows[0]["group_header"]
        for r in yrs:
            if None not in (r["principal"], r["interest"], r["total"]):
                chk.eq(f"debt|{act}|sched|{hdr}|{r['year_label']}|pi", r["total"], r["principal"] + r["interest"], f"{act} schedule {hdr!r} {r['year_label']}: principal + interest = total")
        if tot is None:
            chk.fail(f"debt|{act}|sched|{hdr}|total", "no TOTAL row")
            continue
        for col in ("principal", "interest", "total"):
            chk.eq(f"debt|{act}|sched|{hdr}|total|{col}", tot[col] or 0, sum(r[col] or 0 for r in yrs), f"{act} schedule {hdr!r} {col}: years sum to TOTAL")
    # schedule principal totals tie to outstanding balances
    for act in {a for (a, _g) in groups}:
        lines = [r for r in acts.get(act, []) if r["line_id"] != "total"]
        ends = [r["ending"] for r in lines if r["line_id"] in DEBT_TYPES]
        gp = [next((r for r in rows if r["kind"] == "total"), None) for (a2, _g), rows in groups.items() if a2 == act]
        sched_total = sum((x["principal"] or 0) for x in gp if x)
        gap = sum(i["ending"] for i in res["issues"] if i["activity"] == act and i.get("flag") == "derived_gap")
        sched_total += gap
        single_ok = all(x is None or x["principal"] in ends for x in gp)
        if not (single_ok or sched_total == sum(ends) or sched_total in {sum(ends) + r["ending"] for r in lines if r["line_id"] == "landfill_postclosure"}):
            chk.fail(f"debt|{act}|sched|principal_tie", f"{act}: schedule principal totals {[x['principal'] for x in gp if x]} do not match the outstanding debt balances {ends}")


if __name__ == "__main__":
    import sys
    fy = int(sys.argv[1])
    r = read_debt(fy)
    for k in ("activity", "issues", "schedule"):
        print("==", k, len(r[k]))
        for x in r[k]:
            if k == "schedule" and x["kind"] == "year":
                continue
            print({a: b for a, b in x.items() if a != "description"})
    print(r["problems"])
