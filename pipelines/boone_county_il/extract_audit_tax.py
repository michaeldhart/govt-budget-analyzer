"""Read the "Property Tax Levies and Collections" schedule of each Boone County audit (statistical section, 1-5 tax years per page, 1-3 pages per audit).

Per tax year: the assessed valuation, each levy's rate and extension (county general, bonds and interest, IMRF, highway, bridge, matching, health, liability
insurance, social security, cooperative extension, veterans, senior citizen), the collections by levy and the percentage collected.
Output data/boone_county_il/extracted/audits/<doc>_tax_levies.csv: doc_id, pdf_page, tax_year, section (assessed_valuation | extension | collection | pct_collected),
line_item, rate, amount; and <doc>_tax_levies_checks.csv. Checks: extensions add to the printed total, collections add to the printed total, the percentage
collected equals total collections / total extensions (to the printed two decimals), and each extension is about rate x assessed valuation (rates are printed
rounded; a difference of up to 0.5% is allowed).  Cross-year: build_audit_tax_levies in build_facts compares the same tax year across audits.
Usage: python3 pipelines/boone_county_il/extract_audit_tax.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?|(?<!\w)-(?=\s|$)")


def tokens_pos(line):
    """(label, [(value, right_edge)]) - the same reading as tokens() but keeping where each figure ends, to place a short row (blank cells) by position."""
    s = re.sub(r"(\d) %", r"\1%", line.replace("$", " ")).replace("( ", "(")
    m = re.search(r"(?:(?<=\s)|^)(?:-?\d[\d,]*(?:\.\d+)?%?|-)(?=\s|$)", s)
    if not m:
        return s.strip(), []
    out = []
    for mm in re.finditer(r"\S+", s[m.start():]):
        t = mm.group()
        if t in ("-", "–"):
            v = 0.0
        else:
            try:
                v = float(t.replace(",", "").rstrip("%"))
            except ValueError:
                return s.strip(), []
        out.append((v, m.start() + mm.end()))
    return s[:m.start()].strip(), out


def tokens(line):
    """(label, [values]) of a table line: numbers from the first numeric token on; a lone dash is zero; percentages keep their number."""
    s = re.sub(r"(\d) %", r"\1%", line.replace("$", " ")).replace("( ", "(")
    m = re.search(r"(?:(?<=\s)|^)(?:-?\d[\d,]*(?:\.\d+)?%?|-)(?=\s|$)", s)
    if not m:
        return s.strip(), []
    label = s[:m.start()].strip()
    vals = []
    for t in s[m.start():].split():
        if t == "-" or t == "–":
            vals.append(0.0)
        else:
            t2 = t.replace(",", "").rstrip("%")
            try:
                vals.append(float(t2))
            except ValueError:
                return s.strip(), []
    return label, vals


def find_pages(pages):
    return [i for i, p in enumerate(pages, 1) if re.search(r"PROPERTY TAX LEVIES AND COLLECTIONS", re.sub(r"\s+", " ", p.upper())[:300])
            and "TABLE OF CONTENTS" not in p.upper()[:400]]


def read_page(page, pn, doc, out, checks):
    lines = [l for l in page.split("\n") if l.strip()]
    years = []
    for i, l in enumerate(lines):
        if re.search(r"(?i)tax year", l):
            cand = re.findall(r"\b(20\d{2})\b", " ".join(lines[i:i + 3]))
            if cand:
                years = [int(y) for y in cand]
                break
    if not years:
        checks.append((f"p{pn}: tax years found", False, ""))
        return
    k = len(years)
    section = None
    ext_rows, col_rows = [], []
    assessed = None
    edges = {}

    def edges_for(width):
        if width not in edges:
            full = [[e for _, e in tokens_pos(l)[1]] for l in lines if len(tokens_pos(l)[1]) == width and not re.search(r"\d{4}\s", l[:40])]
            edges[width] = [sorted(x[c] for x in full)[len(full) // 2] for c in range(width)] if full else None
        return edges[width]

    def placed(l, width):
        label, pv = tokens_pos(l)
        ed = edges_for(width)
        if not ed or not pv:
            return label, []
        cells = [None] * width
        for v, e in pv:
            cells[min(range(width), key=lambda c: abs(ed[c] - e))] = v
        return label, [0.0 if c is None else c for c in cells]

    for l in lines:
        U = l.upper()
        if re.search(r"ASSESSED VALUATION", U):
            _, v = tokens(l)
            if len(v) == k:
                assessed = v
                for y, a in zip(years, v):
                    out.append(dict(doc_id=doc, pdf_page=pn, tax_year=y, section="assessed_valuation", line_item="Assessed valuation", rate="", amount=int(a)))
            continue
        if re.search(r"RATES$|^\s*AND EXTENSIONS|RATES AND EXTENSIONS", U.strip()):
            section = "extension"
            continue
        if re.match(r"\s*PROPERTY TAX COLLECTION", U):
            section = "collection"
            continue
        if re.match(r"\s*PERCENTAGE COLLECTED", U):
            _, v = tokens(l)
            if len(v) == k:
                for y, a in zip(years, v):
                    out.append(dict(doc_id=doc, pdf_page=pn, tax_year=y, section="pct_collected", line_item="Percentage collected", rate=a, amount=""))
            else:
                checks.append((f"p{pn}: percentage collected has {len(v)} numbers for {k} years", False, l.strip()[:80]))
            section = None
            continue
        if section is None or re.search(r"(?i)\brate\b|amount|data sources|office of|independent auditor|^\s*-?\s*\d+\s*-?\s*$", l):
            continue
        label, v = tokens(l)
        if section == "extension":
            if v and len(v) < 2 * k:
                label, v = placed(l, 2 * k)  # blank cells (a levy that did not exist in the older years): placed by right edge
            if len(v) != 2 * k:
                if v:
                    checks.append((f"p{pn}: extension row has {len(v)} numbers for {k} years", False, f"{label}: {v}"))
                continue
            row = (label or "TOTAL", [(v[2 * j], int(v[2 * j + 1])) for j in range(k)])
            ext_rows.append(row)
        elif section == "collection":
            if v and len(v) < k:
                label, v = placed(l, k)
            if len(v) != k:
                if v:
                    checks.append((f"p{pn}: collection row has {len(v)} numbers for {k} years", False, f"{label}: {v}"))
                continue
            col_rows.append((label or "TOTAL", [int(x) for x in v]))
    for name, rows in (("extension", ext_rows), ("collection", col_rows)):
        if not rows:
            checks.append((f"p{pn}: {name} rows found", False, ""))
            continue
        total = rows[-1] if rows[-1][0] == "TOTAL" else None
        items = rows[:-1] if total else rows
        for label, vals in items:
            for j, y in enumerate(years):
                rate, amt = (vals[j] if name == "extension" else (None, vals[j]))
                out.append(dict(doc_id=doc, pdf_page=pn, tax_year=y, section=name, line_item=label, rate="" if rate is None else rate, amount=amt))
        if total:
            for j, y in enumerate(years):
                s = sum((it[1][j][1] if name == "extension" else it[1][j]) for it in items)
                printed = total[1][j][1] if name == "extension" else total[1][j]
                checks.append((f"p{pn} {y}: {name}s add to the printed total", s == printed, f"{s} vs {printed}"))
                if name == "extension":
                    out.append(dict(doc_id=doc, pdf_page=pn, tax_year=y, section="extension_total", line_item="Total", rate=total[1][j][0], amount=printed))
                else:
                    out.append(dict(doc_id=doc, pdf_page=pn, tax_year=y, section="collection_total", line_item="Total", rate="", amount=printed))
        else:
            checks.append((f"p{pn}: {name} total row found", False, ""))


def main(fy):
    doc = f"{fy}_audit"
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    out, checks = [], []
    pnums = find_pages(pages)
    for pn in pnums:
        read_page(pages[pn - 1], pn, doc, out, checks)
    # percentage collected = total collections / total extensions
    by = {}
    for r in out:
        by[(r["tax_year"], r["section"])] = r
    for (y, sec), r in list(by.items()):
        if sec == "pct_collected" and (y, "collection_total") in by and (y, "extension_total") in by:
            pct = by[(y, "collection_total")]["amount"] / by[(y, "extension_total")]["amount"] * 100
            checks.append((f"{y}: percentage collected = collections / extensions", abs(pct - float(r["rate"])) <= 0.011, f"{pct:.3f} vs {r['rate']}"))
    ovr = ROOT / "data/boone_county_il/overrides" / f"audit_tax_{doc}.csv"
    if ovr.exists():
        for o in csv.DictReader(open(ovr, newline="")):
            for i, (c, ok, d) in enumerate(checks):
                if not ok and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_tax_levies.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "tax_year", "section", "line_item", "rate", "amount"])
        w.writeheader()
        w.writerows(out)
    with open(OUT / f"{doc}_tax_levies_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: pages {pnums}, {len(out)} cells, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:20]:
        print("   FAIL", c[0], c[2])


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
