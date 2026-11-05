"""Read the pension and OPEB required supplementary schedules of each Boone County audit (text layer, ten fiscal years a table).

Plans: IMRF County Regular, IMRF Elected County Officials (ECO), Sheriff's Law Enforcement Personnel (SLEP), and the county's other postemployment benefit plan.
Schedules: changes in the employer's net pension liability and related ratios (measurement date December 31, up to ten columns, the ratios on a second page), the
schedule of employer contributions (fiscal years ending November 30), and the changes in total OPEB liability (measurement date November 30).
Output data/boone_county_il/extracted/audits/<doc>_pension_rsi.csv: doc_id, pdf_page, plan_id, schedule, section, line_id, label, column_year, value, unit
and <doc>_pension_checks.csv.  Checks: total pension liability beginning + net change = ending and the components add to the net change; the same for the plan
fiduciary net position; net pension liability = total pension liability - fiduciary net position; ratios recompute; contributions: ADC - contributions = deficiency and
contributions / payroll = the printed percentage; OPEB: components add, beginning + change = ending, liability / payroll = the ratio.
Overrides: data/boone_county_il/overrides/audit_pension_<doc>.csv (check, reason).
Usage: python3 pipelines/boone_county_il/extract_audit_pension.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"

PLANS = [(r"COUNTY REGULAR|REGULAR PLAN", "imrf_regular"), (r"ELECTED COUNTY|\bECO\b", "imrf_eco"), (r"SHERIFF|SLEP|LAW ENFORCEMENT", "slep"),
         (r"POSTEMPLOYMENT|OPEB", "opeb")]
LINES = [  # (label regex, line_id) per schedule; first match wins
    (r"^service cost", "service_cost"), (r"^actuarial experience", "actuarial_experience"), (r"^interest\b", "interest"), (r"changes? of benefit (terms|changes)|changes in benefit terms|^benefit changes", "benefit_changes"),
    (r"differences? between expected", "experience_difference"), (r"changes (of|in) assumptions?|^assumption changes", "assumption_changes"),
    (r"^benefit payments", "benefit_payments"), (r"^other changes|^other( \(net transfers\))?$", "other_changes"),
    (r"^net change in total (pension|opeb) liability", "net_change_liability"), (r"^total (pension|opeb) liability - beginning", "liability_beginning"),
    (r"^total (pension|opeb) liability - ending", "liability_ending"), (r"^contributions - employer|^employer contributions", "contributions_employer"),
    (r"^contributions - (member|employees?)|^employee contributions", "contributions_member"), (r"^((pension|opeb) plan )?net investment income", "net_investment_income"),
    (r"^administrative expense", "administrative_expense"), (r"^net change in plan fiduciary", "net_change_fnp"),
    (r"^plan (fiduciary )?net position,? - beginning|^plan (fiduciary )?net position, beginning", "fnp_beginning"), (r"^plan (fiducia\w+ )?net position,? - ending|^plan (fiducia\w+ )?net position, ending", "fnp_ending"),
    (r"^employer'?s net pension liability$|^employer'?s net pension liability \(|^net pension liability ?\(asset\) - ending", "npl"), (r"plan fiduciary net position as a percentage|^plan fiduciary net position$", "fnp_pct_tpl"),
    (r"^covered(-employee)? (valuation )?payroll", "covered_payroll"), (r"net pension liability as a percentage|total opeb liability as a percentage|liability as a percentage", "liability_pct_payroll"),
    (r"^actuarially determined contribution", "actuarially_determined_contribution"), (r"^contributions in relation", "contributions_actual"),
    (r"^contribution deficiency", "contribution_deficiency"), (r"^contributions as a percentage", "contribution_pct_payroll")]


def number_tokens(line):
    """(label, [value]): the label is everything before the trailing run of figures; figures may have decimals, a % sign, parentheses or be a lone dash
    (zero).  A dash inside a label ('Contributions - employer') is not a figure because it is not followed only by figures."""
    s = re.sub(r"\$", " ", line)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"(\d|\))\s+%", r"\1%", s)  # '107.15 %' and '(19.08) %' (FY2021-FY2023 print a space before the sign)
    toks = s.split()
    k = len(toks)
    while k > 0 and re.fullmatch(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?|-|–", toks[k - 1]):
        k -= 1
    run = toks[k:]
    if not run:
        return s.strip(), []
    vals = []
    for t in run:
        if t in ("-", "–"):
            vals.append(0.0)
        else:
            vals.append((-1 if t.startswith("(") else 1) * float(re.sub(r"[()%,]", "", t)))
    return " ".join(toks[:k]), vals


def number_ends(line, n):
    """Right edges (character offsets) of the last n figures of a line."""
    s = line.replace("$", " ")
    toks = [(m_.group(), m_.end()) for m_ in re.finditer(r"\S+", s)]
    toks = [t for t in toks if re.fullmatch(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?|-|–", t[0])]
    return [e for _, e in toks[-n:]] if len(toks) >= n else []


def plan_of(text):
    U = text.upper()
    for rx, pid in PLANS:
        if re.search(rx, U):
            return pid
    return ""


VROW = re.compile(r"^\s*(20\d{2})\s+(.*)$")
VPLANS = [(r"NON-?\s?SLEP", "imrf_regular"), (r"\bSLEP\b", "slep"), (r"\bECO\b", "imrf_eco")]


def vertical_contributions(doc, pn, page, rows):
    """FY2018-FY2023: schedules of employer contributions with the fiscal years down the page (year, actuarially determined contribution, actual, deficiency,
    covered payroll, percentage); several plans on a page, named by the heading above each table. -> number of rows read."""
    plan = "opeb" if re.search(r"(?i)contributions\s*[-–]\s*opeb", " ".join(page.split())[:400]) else ""
    n = 0
    for l in page.split("\n"):
        if not plan or not VROW.match(l):
            for rx, pid in VPLANS:
                if re.search(rx, l.upper()) and re.search(r"(?i)imrf|slep|eco", l) and not VROW.match(l):
                    plan = pid
                    break
        m = VROW.match(l)
        if not m or not plan:
            continue
        rest = re.sub(r"(\d) %", r"\1%", m.group(2).replace("$", " ")).replace("DIV/0 %", "n/a").replace("DIV/0%", "n/a").replace("#DIV/0!", "n/a").replace("(", "").replace(")", "")
        toks = rest.split()
        if len(toks) != 5 or not (toks[4].endswith("%") or toks[4] == "n/a"):
            continue
        try:
            nums = [0.0 if t in ("-", "–") else float(t.replace(",", "")) for t in toks[:4]]
        except ValueError:
            continue
        try:
            pct = None if toks[4] == "n/a" else float(toks[4].rstrip("%"))
        except ValueError:
            pct = None  # a dash where the percentage would be
        for lid, v, unit in zip(("actuarially_determined_contribution", "contributions_actual", "contribution_deficiency", "covered_payroll", "contribution_pct_payroll"),
                                nums + [pct], ("usd",) * 4 + ("percent",)):
            if v is None:
                continue
            rows.append(dict(doc_id=doc, pdf_page=pn, plan_id=plan, schedule="contributions", section="", line_id=lid, label=lid.replace("_", " "),
                             column_year=int(m.group(1)), value=v, unit=unit))
        n += 1
    return n


def read_doc(doc, pages):
    rows, checks = [], []
    current = {"plan": "", "schedule": ""}
    for pn, page in enumerate(pages, 1):
        head = re.sub(r"\s+", " ", page).strip()[:520]
        U = head.upper()
        if "TABLE OF CONTENTS" not in U[:250] and re.search(r"SCHEDULE OF (IMRF|EMPLOYER) CONTRIBUTIONS|SCHEDULE OF CONTRIBUTIONS", U[:300]):
            if vertical_contributions(doc, pn, page, rows):
                continue
        title = re.search(r"(NET PENSION LIABILITY|EMPLOYER CONTRIBUTIONS|OPEB LIABILITY|OPEB)", U) and re.search(r"SCHEDULE", U[:200])
        cont = bool(re.match(r"MEASU[A-Z]*NT DATE|FISCAL YEAR ENDING", U)) and current["plan"]
        if "TABLE OF CONTENTS" in U[:250] or "GAAP REQUIRES" in U[:200] or not (title or cont):
            continue
        if title:
            sched = "contributions" if "EMPLOYER CONTRIBUTIONS" in U[:300] else ("opeb" if "OPEB LIABILITY" in U[:300] else "npl")
            if sched != "contributions" and "RELATED RATIO" not in U[:400]:
                continue
            if sched == "contributions" and "POSTEMPLOYMENT" in U[:300]:
                continue  # the GASB 45 schedule of employer contributions for OPEB (FY2015-FY2017): not a pension schedule
            current = {"plan": plan_of(head[:400]), "schedule": sched}
        if not current["plan"]:
            checks.append((f"p{pn}: plan identified", False, head[:80]))
            continue
        lines = [l for l in page.split("\n") if l.strip()]
        years, hdr, year_edges = [], None, []
        for i, l in enumerate(lines):
            if re.search(r"(?i)(measu\w*nt date|fiscal year ending)", l):
                ys = re.findall(r"\b(20\d{2})\b", l)
                if len(ys) >= 2:
                    years, hdr = [int(y) for y in ys], i
                    break
        if not years:  # FY2018 on: a line of years (latest first, the older columns empty) above 'Calendar year ending'
            for i, l in enumerate(lines[:14]):
                ys = re.findall(r"\b(20\d{2})\b", l)
                if len(ys) >= 6 and not re.search(r"[A-Za-z]{4}", re.sub(r"(?i)imrf|regular|slep|eco|plan", "", l)):
                    years, hdr = [int(y) for y in ys], i
                    year_edges = [m_.end() for m_ in re.finditer(r"\b20\d{2}\b", l)]
                    break
        if not years and doc.startswith("FY2015"):  # GASB 68's first year: one column; the pension liability is measured December 31 of the prior year, contributions are for the fiscal year
            if current["schedule"] == "npl":
                m_ = re.search(r"measurement date is December 31,\s*(20\d{2})", " ".join(page.split()), re.I)
                years = [int(m_.group(1))] if m_ else []
            else:
                m_ = re.search(r"November 30,\s*(20\d{2})", " ".join(page.split())[:300])
                years = [int(m_.group(1))] if m_ else []
            hdr = next((i for i, l in enumerate(lines) if re.search(r"(?i)november 30", l)), 0) if years else None
            if years and current["schedule"] == "npl":
                hdr = next((i for i, l in enumerate(lines) if re.search(r"(?i)november 30", l)), 0)
        if not years:
            checks.append((f"p{pn} {current['plan']}: column years found", False, ""))
            continue
        section, pending = "", ""
        hdr_blank = not re.search(r"(?i)(measu\w*nt date|fiscal year ending)", lines[hdr])
        for l in lines[hdr + 1:]:
            if re.search(r"(?i)see independent|notes to required|^\s*-\s*\d+\s*-\s*$|^\s*\d{4}\s*[-–]\s*(changes|$)|ultimately, this|no assets are|the following are the|the county implemented|discount rate", l):
                break
            label, vals = number_tokens(l)
            if not vals:
                t = l.strip()
                if (t.isupper() or t.endswith(":")) and len(t) < 50:
                    section, pending = t, ""
                else:
                    pending = (pending + " " + t).strip()
                continue
            if re.match(r"(?i)\s*(fiscal year ending|see multiyear|see notes)", label):
                continue
            label = (pending + " " + label).strip() if pending else label
            pending = ""
            if len(vals) < len(years) and hdr_blank:
                row_years = years[:len(vals)]  # the older columns are blank (trailing)
                ends = number_ends(l, len(vals))
                if ends and year_edges:  # a blank cell can also sit between figures: place each figure under the year whose heading ends nearest
                    pick = [min(range(len(years)), key=lambda k, e=e: abs(year_edges[k] - e)) for e in ends]
                    if len(set(pick)) == len(pick):
                        row_years = [years[k] for k in pick]
            else:
                row_years = years
            if len(vals) != len(row_years):
                checks.append((f"p{pn} {current['plan']}: row '{label[:40]}' has {len(vals)} numbers for {len(years)} years", False, ""))
                continue
            clean = re.sub(r"\s+", " ", label).strip().lower()
            lid = next((i for rx, i in LINES if re.search(rx, clean)), "")
            if lid == "benefit_payments":
                lid = "benefit_payments_fnp" if "FIDUCIARY" in section.upper() else "benefit_payments_liability"
            if lid == "other_changes" and "FIDUCIARY" in section.upper():
                lid = "other_fnp"
            unit = "percent" if "%" in l else "usd"
            for y, v in zip(row_years, vals):
                rows.append(dict(doc_id=doc, pdf_page=pn, plan_id=current["plan"], schedule=current["schedule"], section=section, line_id=lid,
                                 label=re.sub(r"\s+", " ", label).strip(), column_year=y, value=v, unit=unit))
    return rows, checks


def verify(rows, checks):
    by = {}
    for r in rows:
        by.setdefault((r["plan_id"], r["schedule"], r["line_id"], r["column_year"]), r["value"])
    keys = sorted({(r["plan_id"], r["schedule"]) for r in rows})

    def v(plan, sched, lid, y):
        return by.get((plan, sched, lid, y))

    def tol(a, b, t=1.0):
        return abs(a - b) <= t

    for plan, sched in keys:
        ys = sorted({r["column_year"] for r in rows if r["plan_id"] == plan and r["schedule"] == sched})
        for y in ys:
            g = lambda lid: v(plan, sched, lid, y)  # noqa: E731
            name = f"{plan} {sched} {y}"
            if sched in ("npl", "opeb"):
                parts = [g(i) for i in ("service_cost", "interest", "benefit_changes", "actuarial_experience", "experience_difference", "assumption_changes", "benefit_payments_liability", "other_changes") if g(i) is not None]
                if g("net_change_liability") is not None and parts:
                    checks.append((f"{name}: liability components add to the net change", tol(sum(parts), g("net_change_liability"), 3), f"{sum(parts)} vs {g('net_change_liability')}"))
                if None not in (g("liability_beginning"), g("net_change_liability"), g("liability_ending")):
                    checks.append((f"{name}: beginning + change = ending liability", tol(g("liability_beginning") + g("net_change_liability"), g("liability_ending"), 3), ""))
                if sched == "npl":
                    fp = [g(i) for i in ("contributions_employer", "contributions_member", "net_investment_income", "benefit_payments_fnp", "administrative_expense", "other_fnp") if g(i) is not None]
                    if g("net_change_fnp") is not None and fp:
                        checks.append((f"{name}: fiduciary net position components add to the net change", tol(sum(fp), g("net_change_fnp"), 3), f"{sum(fp)} vs {g('net_change_fnp')}"))
                    if None not in (g("fnp_beginning"), g("net_change_fnp"), g("fnp_ending")):
                        checks.append((f"{name}: beginning + change = ending fiduciary net position", tol(g("fnp_beginning") + g("net_change_fnp"), g("fnp_ending"), 3), ""))
                    if None not in (g("liability_ending"), g("fnp_ending"), g("npl")):
                        checks.append((f"{name}: net pension liability = liability - fiduciary net position", tol(g("liability_ending") - g("fnp_ending"), g("npl"), 3),
                                       f"{g('liability_ending') - g('fnp_ending')} vs {g('npl')}"))
                    if None not in (g("fnp_ending"), g("liability_ending"), g("fnp_pct_tpl")) and g("liability_ending"):
                        checks.append((f"{name}: fiduciary net position percentage", tol(g("fnp_ending") / g("liability_ending") * 100, g("fnp_pct_tpl"), 0.011),
                                       f"{g('fnp_ending') / g('liability_ending') * 100:.3f} vs {g('fnp_pct_tpl')}"))
                    if None not in (g("npl"), g("covered_payroll"), g("liability_pct_payroll")) and g("covered_payroll"):
                        checks.append((f"{name}: net pension liability as a percentage of payroll", tol(g("npl") / g("covered_payroll") * 100, g("liability_pct_payroll"), 0.011),
                                       f"{g('npl') / g('covered_payroll') * 100:.3f} vs {g('liability_pct_payroll')}"))
                else:
                    if None not in (g("liability_ending"), g("covered_payroll"), g("liability_pct_payroll")) and g("covered_payroll"):
                        checks.append((f"{name}: OPEB liability as a percentage of payroll", tol(g("liability_ending") / g("covered_payroll") * 100, g("liability_pct_payroll"), 0.011), ""))
            else:
                a, c, d = g("actuarially_determined_contribution"), g("contributions_actual"), g("contribution_deficiency")
                if None not in (a, c, d):
                    checks.append((f"{name}: ADC - contributions = deficiency", tol(a - c, d, 1), f"{a - c} vs {d}"))
                if None not in (c, g("covered_payroll"), g("contribution_pct_payroll")) and g("covered_payroll"):
                    checks.append((f"{name}: contributions / payroll", tol(c / g("covered_payroll") * 100, g("contribution_pct_payroll"), 0.011), ""))
    # a year's ending liability is the next year's beginning
    for plan, sched in keys:
        if sched == "contributions":
            continue
        ys = sorted({r["column_year"] for r in rows if r["plan_id"] == plan and r["schedule"] == sched})
        for y, y2 in zip(ys, ys[1:]):
            for e, b in (("liability_ending", "liability_beginning"), ("fnp_ending", "fnp_beginning")):
                if v(plan, sched, e, y) is not None and v(plan, sched, b, y2) is not None:
                    checks.append((f"{plan} {sched} {y}->{y2}: {e} = next {b}", tol(v(plan, sched, e, y), v(plan, sched, b, y2), 1), ""))


READ_LAYOUTS = {"FY2015", "FY2016", "FY2017", "FY2018", "FY2019", "FY2020", "FY2021", "FY2022", "FY2023", "FY2024", "FY2025"}  # the ten-column 'Schedule of Changes in the Employer's Net Pension Liability' family; FY2015 and FY2018-FY2023 print
# other layouts (one column, years across with the latest first, 'IMRF Non-SLEP' / 'SLEP' contribution tables); their figures are the earlier columns of the FY2024 and FY2025 tables


def main(fy):
    doc = f"{fy}_audit"
    if fy not in READ_LAYOUTS:
        print(f"{doc}: layout not read (its tables are the earlier columns of FY2024/FY2025)")
        return
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    rows, checks = read_doc(doc, text.split("\f"))
    verify(rows, checks)
    ovr = ROOT / "data/boone_county_il/overrides" / f"audit_pension_{doc}.csv"
    if ovr.exists():
        for o in csv.DictReader(open(ovr, newline="")):
            for i, (c, ok, d) in enumerate(checks):
                if not ok and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_pension_rsi.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "plan_id", "schedule", "section", "line_id", "label", "column_year", "value", "unit"])
        w.writeheader()
        w.writerows(rows)
    with open(OUT / f"{doc}_pension_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    plans = sorted({(r["plan_id"], r["schedule"]) for r in rows})
    print(f"{doc}: {len(rows)} cells, plans {plans}, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:25]:
        print("   FAIL", c[0], c[2])


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
