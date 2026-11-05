"""Read the narrative pension and OPEB notes of the FY2024 and FY2025 Boone County audits (Notes 12 and 13): the summary of the IMRF plans, and for IMRF County Regular, IMRF
Elected County Officials (ECO), the Sheriff's Law Enforcement Personnel plan (SLEP) and the OPEB plan: membership, contribution rates, actuarial assumptions, discount rate,
target asset allocation and expected real returns, the changes in the net pension liability (total pension liability / fiduciary net position / net), the discount rate
sensitivity, pension expense, deferred outflows and inflows by source and their recognition schedule.

Output data/boone_county_il/extracted/audits/<doc>_pension_note.csv: doc_id, pdf_page, plan_id, section, line_item, column, value, value_text, unit; and <doc>_pension_note_checks.csv.
Checks: the plan summary equals the schedules in extract_audit_pension.py (net pension liability per plan); the changes table adds (beginning + net changes = ending, components =
net changes, (a) - (b) = net) and ends at the schedule's net pension liability; the sensitivity's middle value equals the net liability and the lower rate gives the larger liability;
deferred outflow and inflow sources add to the printed totals and to the summary; the recognition schedule adds to its printed total; membership adds; target allocations add to 100%.
Usage: python3 pipelines/boone_county_il/extract_audit_pension_note.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"

NOISE = re.compile(r"^\s*(BOONE COUNTY, ILLINOIS|NOTES? TO BASIC FINANCIAL STATEMENTS|NOTE \d+:?\s+(RETIREMENT PLANS|OTHER POSTEMPLOYMENT BENEFITS)( \(CONTINUED\))?\s*$|\d{1,3}\s*$|NOTES TO FINANCIAL STATEMENTS.*|\d+\.\s+(DEFINED BENEFIT PENSION PLANS|OTHER POSTEMPLOYMENT BENEFITS) \(Continued\)|-+\s*\d+\s*$|\d+\s*-+\s*$|-- \d+\s*$|\d+ --\s*$)", re.I)
PLAN_HEAD = [(r"^Illinois Municipal Retirement Fund\s*-\s*County\b", "imrf_regular"), (r"^Illinois Municipal Retirement Fund\s*-\s*Elected County Officials", "imrf_eco"),
             (r"^Sheriff.s Law Enforcement Personnel\b", "slep"), (r"^Regular Personnel \(Non.SLEP\)", "imrf_regular"),
             (r"^Sheriff.s Law Enforcements? \(SLEP\)", "slep"), (r"^Elected County Officials \(ECO\)", "imrf_eco"),
             (r"^SLEP Plan:?$", "slep"), (r"^ECO Plan:?$", "imrf_eco")]
SECTIONS = [(r"^d\.\s+Funding Policy", "text"), (r"^e\.\s+Annual OPEB Costs", "opeb_cost"), (r"^Funded Status and Funding Progress", "funded_status"),
            (r"^Employees Covered by the Benefit Terms", "membership"), (r"^Single Discount Rate", "discount_rate"), (r"^Actuarial assumptions", "assumptions"),
            (r"^Asset Class\b", "allocation"), (r"^Sensitivity of the .{0,70}pension liability", "sensitivity"), (r"^Pension Expense,? (and )?Deferred", "deferred"),
            (r"^Plan Membership|^c\.\s+Membership|^Membership$", "membership"), (r"^Contributions$", "contributions"), (r"^Actuarial Assumptions|^e\.\s+Actuarial Assumptions", "assumptions"),
            (r"^Discount Rate$", "discount_rate"), (r"^Long-Term Expected Rate of Return", "allocation"), (r"^Changes (in|to) (the )?Net Pension Liability", "npl_changes"),
            (r"^Sensitivity of the (Net|Total) OPEB Liability", "sensitivity"), (r"^Discount Rate Sensitivity|^g\.\s+Rate Sensitivity|^Rate Sensitivity", "sensitivity"),
            (r"^Pension Expense and Deferred|^h\.\s+OPEB Expense|^OPEB Expense and Deferred", "deferred"), (r"^Benefits Provided|^Plan Administration", "text"),
            (r"^Net Pension Liability$", "net_pension_liability_text"), (r"^Changes in the (Net|Total) OPEB|^Total OPEB Liability$|^d\.\s+Total OPEB", "opeb_changes")]
NUM = r"\(?-?\$?\s*[\d,]+(?:\.\d+)?\)?%?"


def nums(s):
    """figures at the end of a line: -> (label, [values])"""
    s = s.replace("($", "(").replace("$", " ")
    toks = s.split()
    k = len(toks)
    while k and re.fullmatch(r"\(?-?[\d,]+(?:\.\d+)?%?\)?|-", toks[k - 1]):
        k -= 1
    mon = r"(?i)jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*"
    while k < len(toks) - 1 and k > 0 and ((re.fullmatch(r"\d{1,2},?", toks[k]) and re.fullmatch(mon, toks[k - 1])) or (re.fullmatch(r"(19|20)\d{2}", toks[k]) and toks[k - 1].endswith(","))):
        k += 1  # a date ('Balances at January 1, 2020') is part of the label, not a figure
    vals = []
    for t in toks[k:]:
        vals.append(0.0 if t == "-" else (-1 if t.startswith("(") else 1) * float(t.strip("()").replace(",", "").rstrip("%")))
    return " ".join(toks[:k]), vals


NOTE_WORDS = re.compile(r"PENSION|POSTEMPLOYMENT|RETIREMENT PLANS|OPEB")


def clean_pages(pages):
    """-> [(pdf_page, line)] of the pension and OPEB notes without running heads and page numbers."""
    out, on, seen = [], False, False
    for pn, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())[:600]
        if not on and re.search(r"(DEFINED BENEFIT PENSION PLANS|OTHER POSTEMPLOYMENT BENEFITS|RETIREMENT PLANS)", U) and "NOTES TO" in U and "TABLE OF CONTENTS" not in U and "SUMMARY OF SIGNIFICANT" not in U:
            on = True
        if on and (re.search(r"BEGINNING BALANCE RESTATEMENTS|CHANGE WITHIN FINANCIAL REPORTING", U[:300]) or not NOTE_WORDS.search(U[:600])):
            break
        if on:
            stop = False
            for l in p.split("\n"):
                l = l.strip()
                if re.match(r"(?i)^(note\s+\d+:?|\d+\.)\s+(defined benefit|retirement plans|other postemployment)", l):
                    seen = True
                elif seen and re.match(r"(?i)^note\s+\d+:?\s+[a-z]", l):
                    stop = True  # the next note starts on this page
                    break
                if re.match(r"(?i)^(note\s+\d+:?\s+|\d+\.\s+)other postemployment benefits( \(opeb\))?\s*$", l):
                    out.append((pn, "12. OTHER POSTEMPLOYMENT BENEFITS"))  # the note's first page: switches the plan to OPEB
                    continue
                if l and not NOISE.match(l):
                    l = re.sub(r"(\d)\s+%", r"\1%", l.replace("’", "'"))
                    l = re.sub(r"(\d)\.(\d{3},\d{3})\b", r"\1,\2", l)  # FY2017 SLEP prints 17.103,711 for 17,103,711
                    out.append((pn, re.sub(r"[ \t]+", lambda m: m.group() if len(m.group()) >= 2 else " ", l)))
            if stop:
                break
    return out


def main(fy):
    doc = f"{fy}_audit"
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    lines = clean_pages(text.split("\f"))
    rows, checks = [], []
    plan, section = "all", "summary"
    pending = ""
    sens_cols = []

    def add(pn, item, col, val=None, vtext="", unit="usd"):
        rows.append(dict(doc_id=doc, pdf_page=pn, plan_id=plan, section=section, line_item=item, column=col, value="" if val is None else val, value_text=vtext, unit=unit))

    done_c = set()

    def contrib_facts(pn, text):
        """member / employer contribution rate and the employer's contribution for the fiscal year, from the 'Contributions - As set by statute ...' paragraph."""
        for item, rx, unit, scale in (("member contribution rate", r"required to contribute\s+(\d+(?:\.\d+)?)%", "percent", 1),
                                      ("employer contribution rate", r"(?:annual |employer )contribution rates? for calendar years? .*?(?:was|were)\s+(\d+(?:\.\d+)?)%(?:\s+and\s+(\d+(?:\.\d+)?)%)?", "percent", 1),
                                      ("employer contributions for the fiscal year", r"(?:employer )?contributed\s+\$([\d,]+)", "usd", 1)):
            m = re.search(rx, text)
            if m and (plan, item) not in done_c:
                g = [x for x in m.groups() if x]
                val = float(g[-1].replace(",", ""))  # two rates (calendar years 2019 and 2020): the later one
                done_c.add((plan, item))
                rows.append(dict(doc_id=doc, pdf_page=pn, plan_id=plan, section="contributions", line_item=item, column="amount" if unit == "usd" else "rate", value=val, value_text="", unit=unit))

    for pn, l in lines:
        s = l.strip()
        if re.match(r"(?i)^\d+\.\s+OTHER POSTEMPLOYMENT BENEFITS\s*$", s):
            plan, section = "opeb", "text"
            continue
        hit = next((p for rx, p in PLAN_HEAD if re.search(rx, s, re.I)), None)
        if hit and not re.search(r"\d{2,}", s):
            if not (hit == plan and re.search(r"(?i)\(continued\)\s*$", s)):  # a running head repeated on a continuation page
                plan, section, pending = hit, "text", ""
            continue
        sec = next((nm for rx, nm in SECTIONS if re.search(rx, s, re.I) and not re.search(r"\d,\d{3}", s)), None)
        if sec == "discount_rate" and re.search(r"(?i)discount rate of (\d+(?:\.\d+)?)%", s):
            section, pending, sens_cols = "discount_rate", "", []
            add(pn, "discount rate", "rate", float(re.search(r"(?i)discount rate of (\d+(?:\.\d+)?)%", s).group(1)), "", "percent")
            continue
        if sec and (len(re.sub(r"\s{2,}", " ", s)) < 90 or re.match(r"(?i)employees covered by the benefit terms|actuarial assumptions|sensitivity of the |pension expense,|opeb expense and deferred|changes in the (net|total) opeb", s)):
            section, pending = sec, ""
            sens_cols = []
            continue
        label, v = nums(re.sub(r"\s{2,}", "  ", s))
        if section == "summary":
            if len(v) == 4:
                add(pn, label, "county|slep|eco|total", None, "|".join(f"{x:.0f}" for x in v))
                for c, x in zip(("imrf_regular", "slep", "imrf_eco", "total"), v):
                    rows.append(dict(doc_id=doc, pdf_page=pn, plan_id=c, section="summary", line_item=label, column="value", value=x, value_text="", unit="usd"))
            continue
        if section == "membership":
            if v and len(v) == 1 and re.search(r"\s{3,}([\d,]+|-)\s*$", s):
                add(pn, (pending + " " + label).strip(), "members", v[0], "", "count")
                pending = ""
            elif not v:
                pending = (pending + " " + s).strip()
            if plan != "opeb" and re.search(r"(?i)contribut", pending + " " + s):
                contrib_facts(pn, pending + " " + s)
            continue
        if section in ("contributions", "discount_rate", "text", "net_pension_liability_text", "opeb_changes") and not (section == "opeb_changes" and len(v) >= 2):
            m = re.search(r"required to contribute\s+(\d+(?:\.\d+)?)%", s) or re.search(r"contribute\s+(\d+(?:\.\d+)?)%", s)
            if m:
                add(pn, "member contribution rate", "rate", float(m.group(1)), "", "percent")
            m = re.search(r"employer contribution rate.*?was\s+(\d+(?:\.\d+)?)%", pending + " " + s)
            if m:
                add(pn, "employer contribution rate", "rate", float(m.group(1)), "", "percent")
            m = re.search(r"employer contributions.*?were\s+\$([\d,]+)", pending + " " + s)
            if m:
                add(pn, "employer contributions for the fiscal year", "amount", float(m.group(1).replace(",", "")), "", "usd")
            m = re.search(r"discount rate.*?(?:was|of)\s+(\d+(?:\.\d+)?)%", s, re.I)
            if m and section == "discount_rate":
                add(pn, "discount rate", "rate", float(m.group(1)), "", "percent")
            pending = (pending + " " + s)[-160:] if section == "contributions" else ""
            m = re.search(r"recognized pension expense of \$([\d,]+)|recognized OPEB expense of \$([\d,]+)|OPEB expense of \$([\d,]+)", s + " " + pending)
            continue
        if section in ("opeb_cost", "funded_status"):  # GASB 45 (FY2015-FY2017): annual OPEB cost, net OPEB obligation and funded status
            lab, vals = nums(re.sub(r"\s{2,}", "  ", s))
            ym = re.match(r"^(20\d{2})\b", s.replace("$", " ").strip())
            if section == "opeb_cost" and ym and len(vals) == 5 and vals[0] == float(ym.group(1)):
                for item, x, unit in (("annual OPEB cost", vals[1], "usd"), ("employer contributions", vals[2], "usd"), ("percentage contributed", vals[3], "percent"),
                                      ("net OPEB obligation", vals[4], "usd")):
                    add(pn, item, "fy" + ym.group(1), x, "", unit)
            elif len(vals) == 1 and lab and (section == "opeb_cost" or re.match(r"(?i)actuarial|unfunded|funded ratio|covered payroll|uaal", lab)):
                add(pn, lab, "value", vals[0], "", "percent" if "%" in s else "usd")
            continue
        if section == "assumptions":
            for item, rx, unit in (("Actuarial cost method", r"Actuarial Cost Method used was (.+?)\.?$", "text"), ("Asset valuation method", r"Asset Valuation Method used was (.+?)\.?$", "text"),
                                   ("Inflation", r"Inflation Rate was assumed to be (\d+(?:\.\d+)?)%", "percent"),
                                   ("Salary increases", r"Salary Increases were expected to be (.+?)(?:, including inflation)?\.?$", "text"),
                                   ("Investment rate of return", r"Investment Rate of Return was assumed to be (\d+(?:\.\d+)?)%", "percent")):
                mm = re.search(rx, s)
                if mm:
                    tv = mm.group(1).strip()
                    add(pn, item, "assumption", float(tv) if unit == "percent" else None, tv + ("%" if unit == "percent" else ""), unit)
            m = re.match(r"^(Actuarial valuation date|Actuarial cost method|Inflation|Salary increases|Interest rate|Cost of living adjustments|Asset valuation method|Discount rate|"
                         r"Healthcare cost trend rates?|Healthcare cost trend|Amortization method|Remaining amortization period|Mortality|Retirement age|Investment rate of return|Salary increase)\s{2,}(.+)$", s, re.I)
            if m:
                tv = m.group(2).strip()
                pm = re.fullmatch(r"(\d+(?:\.\d+)?)%", tv)
                add(pn, m.group(1), "assumption", float(pm.group(1)) if pm else None, tv, "percent" if pm else "text")
            continue
        if section == "allocation":
            m = re.match(r"^(.+?)\s{2,}(\d+(?:\.\d+)?)%\s{2,}(.+)$", s) or re.match(r"^([A-Za-z][A-Za-z ,&-]+?)\s{2,}(\d+(?:\.\d+)?)\s{2,}(\d+(?:\.\d+)?)$", s)  # FY2018 prints one row without percent signs
            if m and not re.match(r"(?i)portfolio", s):
                add(pn, m.group(1), "target_pct", float(m.group(2)), "", "percent")
                rt = re.fullmatch(r"(\d+(?:\.\d+)?)%", m.group(3).strip())
                add(pn, m.group(1), "expected_real_return", float(rt.group(1)) if rt else None, m.group(3).strip(), "percent")
            elif re.match(r"(?i)^total\s{2,}(\d+(?:\.\d+)?)%", s):
                add(pn, "TOTAL", "target_pct", float(re.match(r"(?i)^total\s{2,}(\d+(?:\.\d+)?)%", s).group(1)), "", "percent")
            continue
        if section in ("npl_changes",):
            if len(v) == 1 and v[0] == 0 and label and not pending.endswith("|dash"):  # a wrapped label whose first cell (a dash) is printed on its first line (FY2017: 'Difference between expected / and actual experience')
                pending = (pending + " " + label).strip() + "|dash"
                continue
            if len(v) == 2 and pending.endswith("|dash"):
                v = [v[0], 0.0, v[1]]
                pending = pending[:-5]
            if len(v) == 3:
                lab = (pending + " " + label).strip() if pending else label
                pending = ""
                for c, x in zip(("total_pension_liability", "fiduciary_net_position", "net_pension_liability"), v):
                    add(pn, lab, c, x, "", "usd")
            elif not v:
                pending = (pending + " " + s).strip() if not s.isupper() or not pending else (pending + " " + s)
            continue
        if section == "sensitivity":
            if re.search(r"(?i)1% (increase|decrease|lower|higher).*(valuation|current)", s) and not re.search(r"\d,\d{3}", s):
                sens_cols = []
                continue
            if re.search(r"(?i)^valuation\s+1% (increase|decrease)|^1% increase\s+rate", s):
                continue
            pcts = re.findall(r"\((\d+(?:\.\d+)?)%\)", s)
            if len(pcts) >= 2 and not label.strip(" ()"):
                sens_cols = pcts
                continue
            if len(v) == 3:
                cols = sens_cols if len(sens_cols) == 3 else ["low", "current", "high"]
                for c, x in zip(cols, v):
                    add(pn, label, f"rate_{c}", x, "", "usd")
            elif len(v) == 1 and sens_cols and not label:
                pass
            continue
        if section == "deferred":
            m = re.search(r"recognized (?:pension|OPEB) expense of \$([\d,]+)", s + " " + pending)
            if m:
                add(pn, "expense recognized", "amount", float(m.group(1).replace(",", "")), "", "usd")
                pending = ""
                continue
            if re.match(r"^(Deferred\s+Deferred|Outflows of\s+Inflows of|Resources\s+Resources|Year Ending|November 30,)", s):
                pending = ""  # the column headings of the table
                continue
            lab, vals = nums(re.sub(r"\s{2,}", "  ", s))
            ym = re.match(r"^(20\d{2})\s+(.+)$", s.replace("$", " "))
            if ym and len(vals) == 2 and vals[0] == float(ym.group(1)):
                add(pn, "recognition " + ym.group(1), "net_amount", vals[1])
                continue
            if ym and len(vals) == 1 and float(ym.group(1)) == vals[0]:
                continue
            if len(vals) == 2:
                lab = (pending + " " + lab).strip() if pending else lab
                pending = ""
                lab = re.sub(r"(?i)^deferred amounts to be recognized in pension expense in future periods:?\s*", "", lab)
                lab = re.sub(r"(?i)^deferred amounts to be recognized in OPEB expense in future periods:?\s*", "", lab)
                if re.match(r"(?i)total deferred amounts related to", lab):
                    lab = "TOTAL"  # FY2021-FY2023 wording of the grand total
                elif re.match(r"(?i)total deferred amounts to be recognized", lab):
                    lab = "subtotal future periods"
                add(pn, lab, "deferred_outflows", vals[0])
                add(pn, lab, "deferred_inflows", vals[1])
            elif len(vals) == 1 and re.fullmatch(r"20\d{2}", lab or ""):
                add(pn, "recognition " + lab, "net_amount", vals[0])
            elif len(vals) == 1 and re.fullmatch(r"(?i)thereafter", lab or ""):
                add(pn, "recognition thereafter", "net_amount", vals[0])
            elif len(vals) == 1 and re.fullmatch(r"(?i)total", lab or ""):
                add(pn, "recognition total", "net_amount", vals[0])
            elif not vals:
                pending = (pending + " " + s)[-140:]
            elif len(vals) == 1 and re.match(r"(?i)20\d{2}\s", s):
                add(pn, "recognition " + s.split()[0], "net_amount", vals[0])
            continue
    verify(doc, rows, checks)
    ovr = ROOT / "data/boone_county_il/overrides" / f"audit_pension_note_{doc}.csv"
    if ovr.exists():
        for o in csv.DictReader(open(ovr, newline="")):
            for i, (c, ok, d) in enumerate(checks):
                if not ok and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_pension_note.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "plan_id", "section", "line_item", "column", "value", "value_text", "unit"])
        w.writeheader()
        w.writerows(rows)
    with open(OUT / f"{doc}_pension_note_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: {len(rows)} cells, {len(checks)} checks, {len(bad)} failed; sections {sorted({(r['plan_id'], r['section']) for r in rows})}")
    for c in bad[:25]:
        print("   FAIL", c[0], c[2])


def verify_opeb(doc, rows, checks):
    rsi = OUT / f"{doc}_pension_rsi.csv"
    if not rsi.exists():
        return
    end = {int(r["column_year"]): float(r["value"]) for r in csv.DictReader(open(rsi, newline="")) if r["plan_id"] == "opeb" and r["line_id"] == "liability_ending"}
    if not end:
        return
    latest = end[max(end)]
    sens = [r for r in rows if r["plan_id"] == "opeb" and r["section"] == "sensitivity" and r["line_item"].lower().startswith("total opeb liability")]
    cur = [float(r["value"]) for r in sens if r["column"].startswith("rate_") and r["column"].endswith(("4.4", "current", "4.40"))]
    mids = [float(r["value"]) for i, r in enumerate(sens) if i % 3 == 1]
    checks.append(("opeb sensitivity: the middle column of both tables = the total OPEB liability", bool(mids) and all(m == latest for m in mids), f"{mids} vs {latest}"))
    d = [r for r in rows if r["plan_id"] == "opeb" and r["section"] == "deferred"]
    o = [float(r["value"]) for r in d if r["column"] == "deferred_outflows" and not r["line_item"].upper().endswith("TOTAL") and not r["line_item"].startswith(("recognition", "expense", "subtotal"))]
    t = next((float(r["value"]) for r in d if r["column"] == "deferred_outflows" and r["line_item"].upper().endswith("TOTAL")), None)
    if t is not None:
        checks.append(("opeb deferred outflows add to the total", abs(sum(o) - t) <= 1, f"{sum(o)} vs {t}"))
    i_ = [float(r["value"]) for r in d if r["column"] == "deferred_inflows" and not r["line_item"].upper().endswith("TOTAL") and not r["line_item"].startswith(("recognition", "expense", "subtotal"))]
    ti = next((float(r["value"]) for r in d if r["column"] == "deferred_inflows" and r["line_item"].upper().endswith("TOTAL")), None)
    if ti is not None:
        checks.append(("opeb deferred inflows add to the total", abs(sum(i_) - ti) <= 1, f"{sum(i_)} vs {ti}"))


def verify_gasb45(doc, rows, checks):
    g = lambda sec, rx: next((float(r["value"]) for r in rows if r["plan_id"] == "opeb" and r["section"] == sec and r["column"] == "value" and re.search(rx, r["line_item"], re.I)), None)  # noqa: E731
    arc, intr, adj, cost = g("opeb_cost", r"^annual required contribution"), g("opeb_cost", r"^interest on net opeb"), g("opeb_cost", r"^adjustment to annual"), g("opeb_cost", r"^annual opeb cost$")
    paid, inc, beg, end = g("opeb_cost", r"^contributions made"), g("opeb_cost", r"^increase in net opeb"), g("opeb_cost", r"beginning of year"), g("opeb_cost", r"end of year")
    if None not in (arc, intr, adj, cost):
        checks.append(("opeb: required contribution + interest + adjustment = annual OPEB cost", abs(arc + intr + adj - cost) <= 1, f"{arc + intr + adj} vs {cost}"))
    if None not in (cost, paid, inc):
        checks.append(("opeb: annual cost - contributions = increase in the obligation", abs(cost + paid - inc) <= 1, f"{cost + paid} vs {inc}"))
    if None not in (beg, inc, end):
        checks.append(("opeb: beginning + increase = ending net OPEB obligation", abs(beg + inc - end) <= 1, f"{beg + inc} vs {end}"))
    fy = int(doc[2:6])
    last = g("opeb_cost", r"^net opeb obligation") if False else None
    tbl = {r["column"]: float(r["value"]) for r in rows if r["plan_id"] == "opeb" and r["section"] == "opeb_cost" and r["line_item"] == "net OPEB obligation"}
    if end is not None and f"fy{fy}" in tbl:
        checks.append(("opeb: obligation in the three-year table = the roll-forward", tbl[f"fy{fy}"] == end, f"{tbl[f'fy{fy}']} vs {end}"))
    for y in (fy - 1, fy):
        c, p_, pct = (next((float(r["value"]) for r in rows if r["plan_id"] == "opeb" and r["section"] == "opeb_cost" and r["line_item"] == it and r["column"] == f"fy{y}"), None) for it in ("annual OPEB cost", "employer contributions", "percentage contributed"))
        if None not in (c, p_, pct):
            checks.append((f"opeb {y}: contributions / annual cost = the printed percentage", abs(p_ / c * 100 - pct) <= 0.011, f"{p_ / c * 100:.3f} vs {pct}"))
    aal, assets, uaal, payroll, pct = (g("funded_status", rx) for rx in (r"^actuarial accrued liability", r"^actuarial value of plan assets", r"^unfunded actuarial", r"^covered payroll", r"^uaal as a percentage"))
    if None not in (aal, uaal):
        checks.append(("opeb: AAL - assets = UAAL", abs(aal - (assets or 0) - uaal) <= 1, f"{aal} - {assets} vs {uaal}"))
    if None not in (uaal, payroll, pct):
        checks.append(("opeb: UAAL / covered payroll = the printed percentage", abs(uaal / payroll * 100 - pct) <= 0.011, f"{uaal / payroll * 100:.3f} vs {pct}"))


def verify(doc, rows, checks):
    verify_opeb(doc, rows, checks)
    if any(r["plan_id"] == "opeb" and r["section"] == "opeb_cost" for r in rows):
        verify_gasb45(doc, rows, checks)
    def get(plan, section, item=None, col=None):
        return [r for r in rows if r["plan_id"] == plan and r["section"] == section and (item is None or re.search(item, r["line_item"], re.I)) and (col is None or r["column"] == col)]
    rsi = OUT / f"{doc}_pension_rsi.csv"
    sched = {}
    if rsi.exists():
        for r in csv.DictReader(open(rsi, newline="")):
            if r["line_id"] == "npl" and r["schedule"] == "npl":
                sched[(r["plan_id"], int(r["column_year"]))] = float(r["value"])
    mdate = max((y for (_, y) in sched), default=None)
    for plan in ("imrf_regular", "slep", "imrf_eco"):
        s = get(plan, "summary", r"net pension liability")
        if s and (plan, mdate) in sched:
            checks.append((f"{plan}: summary net pension liability = schedule", s[0]["value"] == sched[(plan, mdate)], f"{s[0]['value']} vs {sched[(plan, mdate)]}"))
        ch = get(plan, "npl_changes")
        if ch:
            by = {}
            for r in ch:
                by.setdefault(r["line_item"], {})[r["column"]] = float(r["value"])
            labs = list(by)
            for lab, d in by.items():
                if len(d) == 3:
                    checks.append((f"{plan} changes '{lab[:30]}': (a) - (b) = net", abs(d["total_pension_liability"] - d["fiduciary_net_position"] - d["net_pension_liability"]) <= 1, ""))
            net_i = next((i for i, l in enumerate(labs) if re.match(r"net changes?", l, re.I)), None)
            beg = by[labs[0]] if len(labs) > 2 and net_i else None  # the first row is the opening balance (its label may carry the table heading), the last the closing balance
            end = by[labs[-1]] if len(labs) > 2 and net_i else None
            net = by[labs[net_i]] if net_i else None
            if beg and end and net and len(beg) == 3 and len(end) == 3:
                for c in beg:
                    checks.append((f"{plan} changes {c}: beginning + net changes = ending", abs(beg[c] + net[c] - end[c]) <= 1, f"{beg[c]} + {net[c]} vs {end[c]}"))
                comps = [by[l] for l in labs[1:net_i]]
                for c in net:
                    ssum = sum(x.get(c, 0) for x in comps)
                    checks.append((f"{plan} changes {c}: components add to the net changes", abs(ssum - net[c]) <= 1, f"{ssum} vs {net[c]}"))
                if (plan, mdate) in sched:
                    checks.append((f"{plan} changes: ending net pension liability = schedule", end["net_pension_liability"] == sched[(plan, mdate)], f"{end['net_pension_liability']} vs {sched[(plan, mdate)]}"))
        sen = get(plan, "sensitivity")
        if sen:
            vals = {r["column"]: float(r["value"]) for r in sen}
            ks = sorted(vals, key=lambda k: float(k.split("_")[1]) if k.split("_")[1].replace(".", "").isdigit() else 0)
            if len(ks) == 3:
                lo, cur, hi = (vals[k] for k in ks)
                checks.append((f"{plan} sensitivity: lower rate gives the larger liability", lo > cur > hi, f"{lo} > {cur} > {hi}"))
                if (plan, mdate) in sched:
                    checks.append((f"{plan} sensitivity: current rate = net pension liability", cur == sched[(plan, mdate)], f"{cur} vs {sched[(plan, mdate)]}"))
        dfr = get(plan, "deferred")
        out_ = [r for r in dfr if r["column"] == "deferred_outflows"]
        inf_ = [r for r in dfr if r["column"] == "deferred_inflows"]
        if out_:
            tot_o = next((float(r["value"]) for r in out_ if r["line_item"].strip().upper().endswith("TOTAL")), None)
            if tot_o is not None:
                checks.append((f"{plan} deferred outflows add to the total", abs(sum(float(r["value"]) for r in out_ if not r["line_item"].strip().upper().endswith("TOTAL") and not r["line_item"].startswith("subtotal")) - tot_o) <= 1, ""))
                s2 = get(plan, "summary", r"deferred outflows")
                if s2:
                    checks.append((f"{plan} deferred outflows total = plan summary", tot_o == s2[0]["value"], f"{tot_o} vs {s2[0]['value']}"))
            tot_i = next((float(r["value"]) for r in inf_ if r["line_item"].strip().upper().endswith("TOTAL")), None)
            if tot_i is not None:
                checks.append((f"{plan} deferred inflows add to the total", abs(sum(float(r["value"]) for r in inf_ if not r["line_item"].strip().upper().endswith("TOTAL") and not r["line_item"].startswith("subtotal")) - tot_i) <= 1, ""))
                s3 = get(plan, "summary", r"deferred inflows")
                if s3:
                    checks.append((f"{plan} deferred inflows total = plan summary", tot_i == s3[0]["value"], f"{tot_i} vs {s3[0]['value']}"))
        rec = [r for r in dfr if r["line_item"].startswith("recognition ") and r["line_item"] != "recognition total"]
        rt = next((float(r["value"]) for r in dfr if r["line_item"] == "recognition total"), None)
        if rec and rt is not None:
            checks.append((f"{plan} recognition schedule adds to the total", abs(sum(float(r["value"]) for r in rec) - rt) <= 1, f"{sum(float(r['value']) for r in rec)} vs {rt}"))
        mem = get(plan, "membership")
        if mem:
            tot = next((float(r["value"]) for r in mem if re.search(r"total", r["line_item"], re.I)), None)
            parts = [float(r["value"]) for r in mem if not re.search(r"total", r["line_item"], re.I)]
            if tot is not None:
                checks.append((f"{plan} membership adds to the total", sum(parts) == tot, f"{sum(parts)} vs {tot}"))
        al = get(plan, "allocation", None, "target_pct")
        if al:
            tot = next((float(r["value"]) for r in al if r["line_item"] == "TOTAL"), None)
            parts = [float(r["value"]) for r in al if r["line_item"] != "TOTAL"]
            if tot is not None:
                checks.append((f"{plan} target allocation adds to the total", abs(sum(parts) - tot) < 0.011, f"{sum(parts)} vs {tot}"))


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
