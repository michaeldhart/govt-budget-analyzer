"""Read the per-fund "Schedule of Revenues, Expenditures and Changes in Fund Balance - Budget and Actual" of each Boone County audit
(every fund except the General Fund: Motor Fuel Tax, Bridge, Highway, Health, IMRF, Tort, ... about 36-47 funds a year).

Layouts: FY2015-17 and FY2024-25 one fund per page ("Original and Final Budget | Actual | Variance"); FY2018-20 one fund per page with
the fund number in the title ("... Fund - Fund 013"); FY2021-23 two funds side by side per page (6 number columns, names in the header).
Output data/boone_county_il/extracted/audits/<doc>_fund_schedules.csv:
  doc_id, pdf_page, fund_name, fund_code, section (revenue | expenditure | other_financing | summary), row_seq, label, line_type
  (item | total | excess | net_change | beginning | ending), budget, actual, variance   (balances: actual only)
and <doc>_fund_schedules_checks.csv. Checks per fund: revenue/expenditure/other financing items sum to their totals (budget and actual),
excess = revenues - expenditures, net change = excess + other financing, ending balance = beginning + net change, and the variance
column equals actual - budget (or budget - actual for expenditures, sign detected per section). Overrides:
data/boone_county_il/overrides/audit_fundsched_<doc>.csv (fund_name, check, value, reason).
Usage: python3 pipelines/boone_county_il/extract_audit_fund_schedules.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

from extract_audit_fund_statements import parse_line

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"
OVR = ROOT / "data/boone_county_il/overrides"

HEADER_LINE = re.compile(r"(?i)variance|^\s*(original and|final)\b|final budget|^\s*budget\s|^\s*actual\s|year ended|over \(under\)|with final|\(see |see independent|^\s*-\s*\d+\s*-\s*$")


OVERRIDES = {}


def load_overrides(doc):
    p = OVR / f"audit_fundsched_{doc}.csv"
    OVERRIDES[doc] = list(csv.DictReader(open(p, newline=""))) if p.exists() else []


def find_pages(pages):
    out = []
    for i, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())[:420]
        if "BUDGETARY COMPARISON SCHEDULE" in U[:200] and "REQUIRED SUPPLEMENTARY" in U[:260] and "GENERAL FUND" not in U[:300] \
                and re.search(r"(?i)net change in fund balance", p) and re.search(r"FUND \d{3}", U[:300]):
            out.append(i)
            continue
        if re.search(r"REVENUES,? EXPENDITURES,? AND CHANGES IN FUND BALANCES? - BUDGET AND ACTUAL", U) and "TABLE OF CONTENTS" not in U \
                and not re.search(r"GENERAL FUND (FOR|-|$)|^[^A-Z]*BOONE COUNTY, ILLINOIS SCHEDULE OF REVENUES, EXPENDITURES AND CHANGES IN FUND BALANCE - BUDGET AND ACTUAL GENERAL FUND", U) \
                and "COMBINING" not in U:
            if re.search(r"(?i)net change in fund balance", p):
                out.append(i)
    return out


def names_for(lines):
    """Fund names from the lines between the title and the column headings."""
    names, started = [], False
    if re.search(r"(?i)budgetary comparison schedule", " ".join(lines[:6])):
        for ln in lines[:8]:
            if re.search(r"(?i)\bfund \d{3}\s*$", ln.strip()):
                return [ln.strip()]
    for ln in lines[:14]:
        s = ln.strip()
        if not s:
            continue
        if re.search(r"(?i)budget and actual\s*$", s):
            started = True
            continue
        if not started:
            continue
        if HEADER_LINE.search(s) or re.match(r"(?i)(revenues|expenditures)", s):
            if names:
                break
            continue
        if re.search(r"(?i)^(nonmajor|major) governmental funds?$", s):
            continue
        names.append(s)
    return names


def split_names(names):
    joined = re.split(r"\s{3,}", " ".join(n if i == 0 else n for i, n in enumerate(names)).strip()) if len(names) == 1 else None
    if joined:
        return [j.strip() for j in joined if j.strip()]
    return [" ".join(names)]


def classify(label, prev, section):
    L = (prev + " " + label).lower().strip()
    if re.match(r"^\s*total (revenues?|expenditures?|other financing)", label.lower()):
        return "total"
    if re.search(r"excess|deficiency of revenues|over expenditures", L) and "net change" not in L:
        return "excess"
    if "net change" in L:
        return "net_change"
    if re.search(r"fund balances?,? (at )?(beginning|december 1)|beginning of (the )?year|december 1", L):
        return "beginning"
    if re.search(r"fund balances?,? (at )?(end|november 30)|end of (the )?year|november 30", L) and section in ("summary",):
        return "ending"
    if re.search(r"as restated|restated|prior period|adjustment", L) and section == "summary":
        return "adjustment"
    return "item"


def process_page(pages, pn, doc):
    lines = pages[pn - 1].split("\n")
    names = split_names(names_for(lines))
    k = max(1, len(names))
    head = " ".join(lines[:14])
    w = 4 if re.search(r"Original\s+Final", head) else 3
    # data rows start at the Revenues heading
    start = next((i for i, l in enumerate(lines) if re.match(r"\s*(REVENUES?|Revenues?)\b", l)), None)
    rows = []
    section, prev, new_section = None, "", None
    for line in lines[start:] if start is not None else []:
        if not line.strip() or re.search(r"(?i)see independent|see notes|\(see |this schedule", line) or re.match(r"^\s*-\s*\d+\s*-\s*$", line):
            continue
        label, vals = parse_line(line)
        if len(vals) == 1 and not label and not prev:
            continue  # a page number
        if not vals or (len(vals) == 1 and vals[0][0] == 0 and line.rstrip().endswith("-") and not re.search(r"\d", line)
                        and not re.search(r"(?i)balance", label)):
            h = label.rstrip(" -:")
            if re.fullmatch(r"(?i)revenues?", h):
                new_section = "revenue"
            elif re.fullmatch(r"(?i)expenditures?", h):
                new_section = "expenditure"
            elif re.match(r"(?i)other financing", h):
                new_section = "other_financing"
            prev = (prev + " " + h).strip() if h.isupper() and prev.isupper() else h
            continue
        rows.append(dict(section=new_section, label=label, prev=prev, vals=vals))
        new_section = None
        prev = ""
    # exits from tree sections
    cur = "revenue"
    for r in rows:
        pass
    return names, k, rows, w


def main(fy):
    doc = f"{fy}_audit"
    load_overrides(doc)
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    pnums = find_pages(pages)
    out, checks = [], []
    for pn in pnums:
        names, k, rows, w = process_page(pages, pn, doc)
        if not rows:
            checks.append((f"p{pn}: rows found", False, " / ".join(names)))
            continue
        width = w * k
        full = [r for r in rows if len(r["vals"]) == width]
        if not full:
            checks.append((f"p{pn} {names}: no full-width rows", False, f"widths {sorted(set(len(r['vals']) for r in rows))}"))
            continue
        edges = [sorted(r["vals"][c][1] for r in full)[len(full) // 2] for c in range(width)]
        for r in rows:
            cells = [None] * width
            if len(r["vals"]) == width:
                cells = [v for v, _ in r["vals"]]
            else:
                for v, e in r["vals"]:
                    cells[min(range(width), key=lambda c: abs(edges[c] - e))] = v
            r["cells"] = cells
        for f in range(k):
            name = names[f] if f < len(names) else names[-1]
            code = (re.search(r"Fund (\d{3})\b", name) or re.search(r"FUND (\d{3})\b", name))
            fund_rows = []
            section = "revenue"
            for r in rows:
                sec = r["section"] or section
                section = sec
                typ = classify(r["label"], r["prev"], "summary" if r.get("after_total") else sec)
                fund_rows.append((r, typ))
            check_fund(doc, pn, name, f, rows, out, checks, code.group(1) if code else "", w)
    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["doc_id", "pdf_page", "fund_name", "fund_code", "section", "row_seq", "label", "line_type", "budget", "actual", "variance"]
    with open(OUT / f"{doc}_fund_schedules.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    with open(OUT / f"{doc}_fund_schedules_checks.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: {len(pnums)} pages, {len({(o['pdf_page'], o['fund_name']) for o in out})} funds, {len(out)} lines, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:30]:
        print("   FAIL", c[0], c[2])


def check_fund(doc, pn, name, f, rows, out, checks, code, w=3):
    def cell(r, col):
        return r["cells"][w * f + col + (w - 3)]  # 4-column layouts print Original, Final, Actual, Variance: use Final

    sums = {"revenue": [0, 0], "expenditure": [0, 0], "other_financing": [0, 0]}
    totals, excess, net, beg, end, adj = {}, None, None, None, None, []
    seq = 0
    section = "revenue"
    state = "body"
    signs = {"revenue": [], "expenditure": [], "other_financing": []}
    body = []
    bal_rows = []
    for r in rows:
        b, a, v = cell(r, 0), cell(r, 1), cell(r, 2)
        lab = r["label"]
        if all(x is None for x in (b, a, v)):
            continue  # this fund has no numbers on the line (side-by-side layout with a shorter fund)
        L = (r["prev"] + " " + lab).lower()
        if r["section"]:
            section = r["section"]
        seq += 1
        row = dict(doc_id=doc, pdf_page=pn, fund_name=name, fund_code=code, section=section, row_seq=seq, label=lab,
                   budget=b, actual=a, variance=v)
        if re.match(r"^\s*total (revenues?|expenditures?|other financing)", (r["prev"] + " " + lab).lower().strip()):
            key = "revenue" if "revenue" in (r["prev"] + lab).lower() else "expenditure" if "expenditure" in (r["prev"] + lab).lower() else "other_financing"
            totals[key] = (b, a, v)
            row["line_type"] = "total"
            row["section"] = key
        elif "net change" in L:
            net = (b or 0, a or 0, v or 0); row["line_type"] = "net_change"; row["section"] = "summary"; state = "balances"
        elif re.search(r"excess|deficiency|over expenditures", L) and state == "body":
            excess = (b or 0, a or 0, v or 0); row["line_type"] = "excess"; row["section"] = "summary"
            section = "other_financing"
        elif state == "balances" or (a is not None and b is None and v is None):
            row["section"] = "summary"
            bal_rows.append((row, lab, a))
            row["budget"] = row["variance"] = None
        else:
            row["line_type"] = "item"
            if b is not None or a is not None:
                b, a = b or 0, a or 0
                sums[section][0] += b
                sums[section][1] += a
                if v is not None:
                    signs[section].append((a - b, b - a, v))
        out.append(row)

    # balance rows after the net change line: first = beginning, last = ending, any in between are adjustments
    if bal_rows:
        bal_rows[0][0]["line_type"] = "beginning"; beg = bal_rows[0][2]
        if len(bal_rows) > 1:
            bal_rows[-1][0]["line_type"] = "ending"; end = bal_rows[-1][2]
        for r_, lab_, a_ in bal_rows[1:-1]:
            r_["line_type"] = "adjustment"; adj.append((lab_, a_))

    def add(name_, ok, detail=""):
        checks.append((f"{doc} p{pn} {name}: {name_}", ok, detail))

    for sec in ("revenue", "expenditure", "other_financing"):
        if sec in totals:
            tb, ta, _ = totals[sec]
            add(f"{sec} items = total", [sums[sec][0], sums[sec][1]] == [tb, ta], f"{sums[sec]} vs {totals[sec][:2]}")
        elif any(sums[sec]):
            totals[sec] = (sums[sec][0], sums[sec][1], 0)  # a fund with a single line prints no total: the line is the total
            add(f"{sec} has no printed total (items used)", True, str(sums[sec]))
    tr = totals.get("revenue", (0, 0, 0)); te = totals.get("expenditure", (0, 0, 0)); to = totals.get("other_financing", (0, 0, 0))
    if excess is not None:
        add("excess = revenues - expenditures", (excess[0], excess[1]) == (tr[0] - te[0], tr[1] - te[1]), f"{excess[:2]} vs {(tr[0]-te[0], tr[1]-te[1])}")
    if net is not None:
        ex = (tr[0] - te[0], tr[1] - te[1])
        add("net change = revenues - expenditures + other financing", (net[0], net[1]) == (ex[0] + to[0], ex[1] + to[1]),
            f"{net[:2]} vs {(ex[0]+to[0], ex[1]+to[1])}")
    else:
        add("net change line found", False)
    if end is not None and beg is not None and net is not None:
        restated = [x for l, x in adj if "restated" in l.lower()]
        opening = restated[0] if restated else beg + sum(x for _, x in adj if x is not None)
        add("ending = beginning + net change", end == opening + net[1], f"{end} vs {opening} + {net[1]}")
    else:
        add("balance lines found", False, f"beg {beg} end {end}")
    # variance sign per section: actual - budget or budget - actual must hold on all lines of the section
    for sec, rows_ in signs.items():
        if rows_:
            ok = all(x[2] == x[0] or x[2] == x[1] for x in rows_) and (all(x[2] == x[0] for x in rows_) or all(x[2] == x[1] for x in rows_) or all(x[0] == x[1] for x in rows_))
            add(f"{sec} variance = actual - budget (consistent sign)", ok, "" if ok else f"{[x for x in rows_ if x[2] not in (x[0], x[1])][:3]}")
    accepted = OVERRIDES.get(doc, [])
    for i, (c, ok, d) in enumerate(checks):
        if not ok and c.startswith(f"{doc} p{pn} {name}:"):
            for o in accepted:
                if o["fund_name"] == name and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
