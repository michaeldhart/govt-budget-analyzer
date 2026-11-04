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

NOISE = re.compile(r"^\s*(BOONE COUNTY, ILLINOIS|NOTES TO FINANCIAL STATEMENTS.*|\d+\.\s+(DEFINED BENEFIT PENSION PLANS|OTHER POSTEMPLOYMENT BENEFITS) \(Continued\)|-+\s*\d+\s*$|\d+\s*-+\s*$|-- \d+\s*$|\d+ --\s*$)", re.I)
PLAN_HEAD = [(r"^Illinois Municipal Retirement Fund\s*-\s*County\b", "imrf_regular"), (r"^Illinois Municipal Retirement Fund\s*-\s*Elected County Officials", "imrf_eco"),
             (r"^Sheriff.s Law Enforcement Personnel\b", "slep")]
SECTIONS = [(r"^Plan Membership|^c\.\s+Membership|^Membership$", "membership"), (r"^Contributions$", "contributions"), (r"^Actuarial Assumptions|^e\.\s+Actuarial Assumptions", "assumptions"),
            (r"^Discount Rate$", "discount_rate"), (r"^Long-Term Expected Rate of Return", "allocation"), (r"^Changes in the Net Pension Liability", "npl_changes"),
            (r"^Discount Rate Sensitivity|^g\.\s+Rate Sensitivity|^Rate Sensitivity", "sensitivity"),
            (r"^Pension Expense and Deferred|^h\.\s+OPEB Expense|^OPEB Expense and Deferred", "deferred"), (r"^Benefits Provided|^Plan Administration", "text"),
            (r"^Net Pension Liability$", "net_pension_liability_text"), (r"^Changes in the Total OPEB|^Total OPEB Liability$|^d\.\s+Total OPEB", "opeb_changes")]
NUM = r"\(?-?\$?\s*[\d,]+(?:\.\d+)?\)?%?"


def nums(s):
    """figures at the end of a line: -> (label, [values])"""
    s = s.replace("$", " ")
    toks = s.split()
    k = len(toks)
    while k and re.fullmatch(r"\(?-?[\d,]+(?:\.\d+)?%?\)?|-", toks[k - 1]):
        k -= 1
    vals = []
    for t in toks[k:]:
        vals.append(0.0 if t == "-" else (-1 if t.startswith("(") else 1) * float(t.strip("()").replace(",", "").rstrip("%")))
    return " ".join(toks[:k]), vals


def clean_pages(pages):
    """-> [(pdf_page, line)] of Notes 12-13 without running heads and page numbers."""
    out, on = [], False
    for pn, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())[:400]
        if not on and re.search(r"(DEFINED BENEFIT PENSION PLANS|OTHER POSTEMPLOYMENT BENEFITS)", U) and "NOTES TO FINANCIAL STATEMENTS" in U and "TABLE OF CONTENTS" not in U:
            on = True
        if on and re.search(r"BEGINNING BALANCE RESTATEMENTS|CHANGE WITHIN FINANCIAL REPORTING", U[:300]):
            break
        if on:
            for l in p.split("\n"):
                if l.strip() and not NOISE.match(l):
                    out.append((pn, re.sub(r"[ \t]+", lambda m: m.group() if len(m.group()) >= 2 else " ", l.strip().replace("’", "'"))))
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

    for pn, l in lines:
        s = l.strip()
        if re.match(r"(?i)^\d+\.\s+OTHER POSTEMPLOYMENT BENEFITS\s*$", s):
            plan, section = "opeb", "text"
            continue
        hit = next((p for rx, p in PLAN_HEAD if re.search(rx, s)), None)
        if hit and not re.search(r"\d{2,}", s):
            plan, section, pending = hit, "text", ""
            continue
        sec = next((nm for rx, nm in SECTIONS if re.search(rx, s) and not re.search(r"\d,\d{3}", s)), None)
        if sec and len(s) < 90:
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
            if v and len(v) == 1:
                add(pn, (pending + " " + label).strip(), "members", v[0], "", "count")
                pending = ""
            elif not v:
                pending = (pending + " " + s).strip()
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
        if section == "assumptions":
            m = re.match(r"^(Actuarial valuation date|Actuarial cost method|Inflation|Salary increases|Interest rate|Cost of living adjustments|Asset valuation method|Discount rate|"
                         r"Healthcare cost trend rates?|Healthcare cost trend|Amortization method|Remaining amortization period|Mortality|Retirement age|Investment rate of return|Salary increase)\s{2,}(.+)$", s, re.I)
            if m:
                tv = m.group(2).strip()
                pm = re.fullmatch(r"(\d+(?:\.\d+)?)%", tv)
                add(pn, m.group(1), "assumption", float(pm.group(1)) if pm else None, tv, "percent" if pm else "text")
            continue
        if section == "allocation":
            m = re.match(r"^(.+?)\s{2,}(\d+(?:\.\d+)?)%\s{2,}(.+)$", s)
            if m and not re.match(r"(?i)portfolio", s):
                add(pn, m.group(1), "target_pct", float(m.group(2)), "", "percent")
                rt = re.fullmatch(r"(\d+(?:\.\d+)?)%", m.group(3).strip())
                add(pn, m.group(1), "expected_real_return", float(rt.group(1)) if rt else None, m.group(3).strip(), "percent")
            elif re.match(r"(?i)^total\s{2,}(\d+(?:\.\d+)?)%", s):
                add(pn, "TOTAL", "target_pct", float(re.match(r"(?i)^total\s{2,}(\d+(?:\.\d+)?)%", s).group(1)), "", "percent")
            continue
        if section in ("npl_changes",):
            if len(v) == 3:
                lab = (pending + " " + label).strip() if pending else label
                pending = ""
                for c, x in zip(("total_pension_liability", "fiduciary_net_position", "net_pension_liability"), v):
                    add(pn, lab, c, x, "", "usd")
            elif not v:
                pending = (pending + " " + s).strip() if not s.isupper() or not pending else (pending + " " + s)
            continue
        if section == "sensitivity":
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
                add(pn, lab, "deferred_outflows", vals[0])
                add(pn, lab, "deferred_inflows", vals[1])
            elif len(vals) == 1 and re.fullmatch(r"20\d{2}", lab or ""):
                add(pn, "recognition " + lab, "net_amount", vals[0])
            elif len(vals) == 1 and re.fullmatch(r"(?i)total", lab or ""):
                add(pn, "recognition total", "net_amount", vals[0])
            elif not vals:
                pending = (pending + " " + s)[-140:]
            elif len(vals) == 1 and re.match(r"(?i)20\d{2}\s", s):
                add(pn, "recognition " + s.split()[0], "net_amount", vals[0])
            continue
    verify(doc, rows, checks)
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
    o = [float(r["value"]) for r in d if r["column"] == "deferred_outflows" and not r["line_item"].upper().endswith("TOTAL") and not r["line_item"].startswith(("recognition", "expense"))]
    t = next((float(r["value"]) for r in d if r["column"] == "deferred_outflows" and r["line_item"].upper().endswith("TOTAL")), None)
    if t is not None:
        checks.append(("opeb deferred outflows add to the total", abs(sum(o) - t) <= 1, f"{sum(o)} vs {t}"))
    i_ = [float(r["value"]) for r in d if r["column"] == "deferred_inflows" and not r["line_item"].upper().endswith("TOTAL") and not r["line_item"].startswith(("recognition", "expense"))]
    ti = next((float(r["value"]) for r in d if r["column"] == "deferred_inflows" and r["line_item"].upper().endswith("TOTAL")), None)
    if ti is not None:
        checks.append(("opeb deferred inflows add to the total", abs(sum(i_) - ti) <= 1, f"{sum(i_)} vs {ti}"))


def verify(doc, rows, checks):
    verify_opeb(doc, rows, checks)
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
            beg = next((by[l] for l in labs if re.search(r"JANUARY 1", l, re.I)), None)
            end = next((by[l] for l in labs if re.search(r"DECEMBER 31", l, re.I)), None)
            net = next((by[l] for l in labs if re.match(r"net changes", l, re.I)), None)
            if beg and end and net:
                for c in beg:
                    checks.append((f"{plan} changes {c}: beginning + net changes = ending", abs(beg[c] + net[c] - end[c]) <= 1, f"{beg[c]} + {net[c]} vs {end[c]}"))
                comps = [by[l] for l in labs if l not in (labs[0],) and not re.search(r"JANUARY 1|DECEMBER 31|net changes|changes for the period", l, re.I)]
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
                checks.append((f"{plan} deferred outflows add to the total", abs(sum(float(r["value"]) for r in out_ if not r["line_item"].strip().upper().endswith("TOTAL")) - tot_o) <= 1, ""))
                s2 = get(plan, "summary", r"deferred outflows")
                if s2:
                    checks.append((f"{plan} deferred outflows total = plan summary", tot_o == s2[0]["value"], f"{tot_o} vs {s2[0]['value']}"))
            tot_i = next((float(r["value"]) for r in inf_ if r["line_item"].strip().upper().endswith("TOTAL")), None)
            if tot_i is not None:
                checks.append((f"{plan} deferred inflows add to the total", abs(sum(float(r["value"]) for r in inf_ if not r["line_item"].strip().upper().endswith("TOTAL")) - tot_i) <= 1, ""))
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
