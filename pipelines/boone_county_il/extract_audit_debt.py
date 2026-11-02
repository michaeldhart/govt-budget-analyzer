"""Read the long-term debt note of each Boone County audit: the summary of changes in long-term liabilities and the debt service to maturity.

Output data/boone_county_il/extracted/audits/<doc>_debt_changes.csv   (doc_id, pdf_page, line_item, beginning, additions, reductions, ending, current_portion)
                                           <doc>_debt_maturity.csv  (doc_id, pdf_page, instrument, fiscal_year_ending, row_kind year | band | total, principal, interest)
and <doc>_debt_checks.csv.  Checks: beginning + additions - reductions = ending on every liability (the county prints reductions in parentheses from FY2021);
the liabilities add to the printed total; each maturity column adds to its printed total; the principal total of an instrument equals that liability's ending
balance.  Overrides: data/boone_county_il/overrides/audit_debt_<doc>.csv (check, reason) accept a source slip.
Usage: python3 pipelines/boone_county_il/extract_audit_debt.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

from extract_audit_fund_statements import parse_line

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"

# maturity column names -> the liability line they should equal
INSTRUMENTS = [(r"general obligation|bonds? payable|^bonds$|alternate revenue", "Bonds payable"), (r"debt certificate", "Debt certificates"),
               (r"notes? payable", "Notes payable"), (r"capital lease|lease", "Capital lease"), (r"sbita", "SBITAs payable")]


def instrument(name):
    for rx, std in INSTRUMENTS:
        if re.search(rx, name, re.I):
            return std
    return name


def liabilities(pages, doc, checks):
    rows = []
    for pn, page in enumerate(pages, 1):
        if not re.search(r"summary of changes in long.?term", page, re.I) or "TABLE OF CONTENTS" in page.upper()[:300]:
            continue
        lines = page.split("\n")
        start = next(i for i, l in enumerate(lines) if re.search(r"summary of changes in long.?term", l, re.I))
        prev = ""
        for line in lines[start + 1:]:
            if not line.strip():
                continue
            label, vals = parse_line(line)
            if len(vals) >= 4:
                lab = re.sub(r"\s+", " ", (prev + " " + label).strip() if (prev and prev.isupper()) else label)
                prev = ""
                v = [x for x, _ in vals]
                if len(v) == 4:
                    v.append(None)  # FY2015-17 style without current portion? keep the four
                rows.append(dict(doc_id=doc, pdf_page=pn, line_item=lab, beginning=v[0], additions=v[1], reductions=abs(v[2]), ending=v[3],
                                 current_portion=v[4] if v[4] is not None else ""))
                if re.match(r"(?i)total", lab):
                    return rows
            elif not vals and (re.match(r"\s*[A-Z ]+$", line.strip()) and len(line.strip()) < 40):
                prev = line.strip()
            elif not vals and re.search(r"(?i)beginning|balance|additions|ending", line):
                continue
            elif not vals and re.match(r"(?i)\s*(governmental|business)", line):
                continue
        break
    return rows


def maturity(pages, doc, checks):
    out = []
    for pn, page in enumerate(pages, 1):
        U = page.upper()
        if "TABLE OF CONTENTS" in U[:300] or not re.search(r"ANNUAL REQUIREMENTS|DEBT SERVICE TO MATURITY", U):
            continue
        lines = page.split("\n")
        names = None
        started = False
        for line in lines:
            if not line.strip():
                continue
            if re.search(r"(?i)annual requirements|debt service to maturity|annual debt service", line):
                started = True
            if not started:
                continue
            if re.search(r"(?i)principal\s+interest", line) and not re.search(r"\d,\d{3}", line):
                if names is None or not names:
                    names = ["Bonds payable"] if re.search(r"(?i)interest\s+total", line) else names or ["Bonds payable"]
                continue
            if not re.search(r"\d", line) and len(line.strip()) < 140:
                cand = [re.sub(r"(?i)^\s*(fiscal year\s+)?ending\s+", "", c.strip()) for c in re.split(r"\s{3,}", line.strip()) if c.strip()]
                cand = [c for c in cand if any(re.search(rx, c, re.I) for rx, _ in INSTRUMENTS)]
                if cand and "following" not in line.lower() and "outstanding" not in line.lower():
                    names = [instrument(c) for c in cand]
                continue
            m = re.match(r"\s*(20\d{2}(?:\s*[-‐–]\s*20\d{2})?|totals?)\b", line, re.I)
            if m and names:
                label, vals = parse_line(line[m.end():])
                v = [x for x, _ in vals]
                kind = "total" if m.group(1).lower().startswith("total") else ("band" if re.search(r"[-‐–]", m.group(1)) else "year")
                if len(v) == 3 and names == ["Bonds payable"]:
                    pairs = [(v[0], v[1])]
                elif len(v) == 2 * len(names):
                    pairs = [(v[2 * j], v[2 * j + 1]) for j in range(len(names))]
                else:
                    checks.append((f"p{pn}: maturity row {m.group(1)} has {len(v)} numbers for {len(names)} instruments", False, line.strip()[:90]))
                    if kind == "total":
                        names = None
                    continue
                for nm, (pr, it) in zip(names, pairs):
                    out.append(dict(doc_id=doc, pdf_page=pn, instrument=nm, fiscal_year_ending="TOTAL" if kind == "total" else re.sub(r"\s", "", m.group(1)).replace("‐", "-").replace("–", "-"),
                                    row_kind=kind, principal=int(pr), interest=int(it)))
                if kind == "total":
                    names = None  # the table ends; later 'Total' lines on the page belong to other tables
    return out


def main(fy):
    doc = f"{fy}_audit"
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    checks = []
    liab = liabilities(pages, doc, checks)
    if not liab:
        checks.append(("liabilities table found", False, ""))
    items = [r for r in liab if not re.match(r"(?i)total", r["line_item"])]
    tot = next((r for r in liab if re.match(r"(?i)total", r["line_item"])), None)
    for r in items:
        ok = r["beginning"] + r["additions"] - r["reductions"] == r["ending"]
        checks.append((f"{r['line_item']}: beginning + additions - reductions = ending", ok, f"{r['beginning']} + {r['additions']} - {r['reductions']} vs {r['ending']}"))
    if tot:
        for col in ("beginning", "additions", "reductions", "ending", "current_portion"):
            if tot[col] == "":
                continue
            s = sum(r[col] for r in items if r[col] != "")
            checks.append((f"liabilities {col} add to the printed total", s == tot[col], f"{s} vs {tot[col]}"))
    mat = maturity(pages, doc, checks)
    by = {}
    for r in mat:
        by.setdefault((r["pdf_page"], r["instrument"]), []).append(r)
    ends = {instrument(r["line_item"]): r["ending"] for r in items}
    for (pn, inst), rs in by.items():
        t = [r for r in rs if r["row_kind"] == "total"]
        rest = [r for r in rs if r["row_kind"] != "total"]
        if not t:
            checks.append((f"p{pn} {inst}: maturity total row found", False, ""))
            continue
        checks.append((f"p{pn} {inst}: principal adds to the printed total", sum(r["principal"] for r in rest) == t[0]["principal"], f"{sum(r['principal'] for r in rest)} vs {t[0]['principal']}"))
        checks.append((f"p{pn} {inst}: interest adds to the printed total", sum(r["interest"] for r in rest) == t[0]["interest"], f"{sum(r['interest'] for r in rest)} vs {t[0]['interest']}"))
        if inst in ends:
            checks.append((f"p{pn} {inst}: principal total = ending balance", t[0]["principal"] == ends[inst], f"{t[0]['principal']} vs {ends[inst]}"))
    ovr = ROOT / "data/boone_county_il/overrides" / f"audit_debt_{doc}.csv"
    if ovr.exists():
        for o in csv.DictReader(open(ovr, newline="")):
            for i, (c, ok, d) in enumerate(checks):
                if not ok and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_debt_changes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "line_item", "beginning", "additions", "reductions", "ending", "current_portion"])
        w.writeheader()
        w.writerows(liab)
    with open(OUT / f"{doc}_debt_maturity.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "instrument", "fiscal_year_ending", "row_kind", "principal", "interest"])
        w.writeheader()
        w.writerows(mat)
    with open(OUT / f"{doc}_debt_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: {len(liab)} liability lines, {len(mat)} maturity cells, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:20]:
        print("   FAIL", c[0], c[2])


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
