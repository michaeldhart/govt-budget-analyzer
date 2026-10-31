"""Read the governmental funds Balance Sheet and Statement of Revenues, Expenditures and Changes in Fund Balances
from each Boone County audit (text layer, `pdftotext -layout`).

Output data/boone_county_il/extracted/audits/<doc>_fund_statements.csv: one row per line x fund column
  doc_id, pdf_page, statement (balance_sheet | changes), row_seq, prev_text, label, col, amount
and <doc>_fund_checks.csv (check, ok, detail). Fund column names for each audit are in
entities/boone_county_il/audit_fund_columns.csv (doc_id, statement, columns joined by '|').
Checks: every Total column equals the sum of the fund columns; Balance sheet: totals of assets, liabilities,
deferred inflows and fund balances equal their lines and assets = liabilities + deferred inflows + fund balances;
Changes: total revenues, total expenditures and total other financing equal their lines, excess = revenues -
expenditures, net change = excess + other financing, ending = net change + the beginning lines.
Overrides: data/boone_county_il/overrides/audit_<doc>.csv (kind allow_diff|fixline, reason required).
Usage: python3 pipelines/boone_county_il/extract_audit_fund_statements.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"
COLS = ROOT / "entities/boone_county_il/audit_fund_columns.csv"
OVR = ROOT / "data/boone_county_il/overrides"

TOKEN = re.compile(r"\(\s*[\d,]+\s*\)|(?<![\w])[\d,]*\d(?=\s|$)|(?<![\w])[-–—](?=\s|$)")


def protect(line):
    """Keep dates in labels ('NOVEMBER 30', 'DECEMBER 1, 2014') from being read as amounts."""
    line = re.sub(r"((?:December|November|DECEMBER|NOVEMBER)\s+\d{1,2},?)\s+(\d{4})\b", r"\1 y\2", line)
    return re.sub(r"\b((?:December|November|DECEMBER|NOVEMBER)\s+)(\d{1,2})(?=\s|$)", r"\1d\2", line)


def parse_line(line):
    """-> (label, [(amount, right_edge)...]) reading numbers from the right; a lone dash is zero."""
    s = protect(line.rstrip().replace("$", " "))
    vals = []
    pos = len(s)
    while True:
        m = None
        for mm in TOKEN.finditer(s[:pos]):
            if s[mm.end():pos].strip() == "":
                m = mm
        if m is None:
            break
        tok = m.group().strip()
        if tok in "-–—":
            v = 0
        elif tok.startswith("("):
            v = -int(tok.strip("() ").replace(",", ""))
        else:
            v = int(tok.replace(",", ""))
        vals.append((v, m.end()))
        pos = m.start()
        if s[:pos].strip() == "":
            break
    vals.reverse()
    return re.sub(r"(?<=\s)[dy](?=\d)", "", s[:pos].strip()), vals


def find_pages(pages):
    found = {}
    for i, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())
        top = U[:170]
        if "GOVERNMENTAL FUNDS" in top and not re.search("RECONCIL|COMBINING|BUDGET|PROPRIETARY|FIDUCIARY", top):
            if "BALANCE SHEET" in top and "TOTAL ASSETS" in U and "balance_sheet" not in found:
                found["balance_sheet"] = i
            elif re.search("REVENUES, EXPENDITURES", top) and "NET CHANGE IN FUND BALANCE" in U and "changes" not in found:
                found["changes"] = i
    return found


def statement_pages(pages, first):
    """The page found plus its continuation pages (wide statements are printed on two pages)."""
    out = [first]
    while first + len(out) - 1 < len(pages):
        nxt = pages[first + len(out) - 1]
        U = re.sub(r"\s+", " ", nxt.upper())
        if "CONTINUED" in U[:300] or ("BOONE COUNTY" not in U[:200] and "NOTES TO" not in U[:300] and len(U) > 200
                                      and "RECONCIL" not in U[:300]):
            out.append(first + len(out))
        else:
            break
    return out


def read_page(page, statement, first):
    start_re = re.compile(r"^\s*(ASSETS|Assets)\b" if statement == "balance_sheet" else r"^\s*(REVENUES|Revenues)\b")
    started = False
    rows, prev = [], ""
    for line in page.split("\n"):
        if not started and start_re.match(line):
            started = True
            continue
        if "See accompanying" in line or "See Accompanying" in line or "Independent auditor" in line:
            break
        if not line.strip():
            continue
        label, vals = parse_line(line)
        if not started:
            if len(vals) >= 2 and not first:  # a continuation page without a title block: start at the first data line
                started = True
            else:
                continue
        if len(vals) >= 2:
            rows.append(dict(prev_text=prev, label=label, vals=vals))
            prev = ""
        else:
            prev = (prev + " " + line.strip()).strip() if line.strip().isupper() or prev.endswith(",") else line.strip()
    return rows


def place(rows):
    """Resolve each row's (value, edge) pairs to full-width value lists; a short row is placed by right edge."""
    n = max(set(len(r["vals"]) for r in rows), key=[len(r["vals"]) for r in rows].count)
    full_rows = [r for r in rows if len(r["vals"]) == n]
    edges = [sorted(r["vals"][c][1] for r in full_rows)[len(full_rows) // 2] for c in range(n)]
    for r in rows:
        if len(r["vals"]) == n:
            r["flag"] = ""
            r["vals"] = [v for v, _ in r["vals"]]
        elif len(r["vals"]) < n:
            cells = [0] * n
            for v, e in r["vals"]:
                cells[min(range(n), key=lambda c: abs(edges[c] - e))] = v
            r["vals"], r["flag"] = cells, "placed_by_position"
        else:
            r["flag"] = "too_many"
    return n


def merge_pages(page_rows):
    acc = page_rows[0]
    for rows in page_rows[1:]:
        labels_acc = {r["label"] for r in acc if r["label"]}
        labelled = [r for r in rows if r["label"]]
        if not labelled or sum(r["label"] in labels_acc for r in labelled) >= 0.6 * len(labelled):
            if len(rows) != len(acc):
                raise SystemExit(f"continuation page has {len(rows)} rows, first page {len(acc)}")
            for a, b in zip(acc, rows):
                a["vals"] = a["vals"] + b["vals"]
        else:
            acc = acc + rows
    return acc


def columns_for(doc, statement):
    if not COLS.exists():
        return None
    for r in csv.DictReader(open(COLS, newline="")):
        if r["doc_id"] == doc and r["statement"] == statement:
            return r["columns"].split("|")
    return None


def main(fy):
    doc = f"{fy}_audit"
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    found = find_pages(pages)
    out_rows, checks = [], []
    for statement in ("balance_sheet", "changes"):
        if statement not in found:
            checks.append((f"{statement}: page found", False, "not found"))
            continue
        pgs = statement_pages(pages, found[statement])
        per_page = []
        for k, pn in enumerate(pgs):
            rows = read_page(pages[pn - 1], statement, k == 0)
            place(rows)
            per_page.append(rows)
        rows = merge_pages(per_page)
        n = len(rows[0]["vals"])
        names = columns_for(doc, statement)
        if names is not None and len(names) != n:
            checks.append((f"{statement}: column count", False, f"{n} numbers per line, config has {len(names)}"))
        for r in rows:
            if len(r["vals"]) != n or r.get("flag") == "too_many":
                checks.append((f"{statement}: line has {len(r['vals'])} numbers", False, r["label"]))
        rows = [r for r in rows if len(r["vals"]) == n]
        for seq, r in enumerate(rows, 1):
            for c, v in enumerate(r["vals"]):
                out_rows.append(dict(doc_id=doc, pdf_page=found[statement], statement=statement, row_seq=seq,
                                     prev_text=r["prev_text"], label=r["label"], col=names[c] if names else c, amount=v))
        checks += check_statement(statement, rows, n)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_fund_statements.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "statement", "row_seq", "prev_text", "label", "col", "amount"])
        w.writeheader()
        w.writerows(out_rows)
    with open(OUT / f"{doc}_fund_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: pages {found}, {len(out_rows)} cells, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:30]:
        print("   FAIL", c[0], c[2])


def full(label, prev):
    return (prev + " " + label).strip().lower()


def check_statement(statement, rows, n):
    checks = []

    def add(name, ok, detail=""):
        checks.append((f"{statement}: {name}", ok, detail))

    # the Total column (last) equals the sum of the other columns on every line
    for r in rows:
        if n >= 3 and sum(r["vals"][:-1]) != r["vals"][-1]:
            add("total column = sum of funds", False, f"{r['label']}: {r['vals']}")
    if n >= 3:
        add(f"total column = sum of funds on {len(rows)} lines", all(sum(r["vals"][:-1]) == r["vals"][-1] for r in rows))
    idx = {}
    for i, r in enumerate(rows):
        idx.setdefault(full(r["label"], r["prev_text"]), i)

    def find(pattern, start=0):
        for i in range(start, len(rows)):
            if re.search(pattern, full(rows[i]["label"], rows[i]["prev_text"])):
                return i
        return None

    def vec_sum(a, b):
        return [sum(rows[i]["vals"][c] for i in range(a, b)) for c in range(n)]

    def equal(name, got, want):
        ok = got == want
        add(name, ok, "" if ok else f"{got} != {want}")

    def subtotal(name, heading_re, total_re, start=0):
        h = find(heading_re, start)
        t = find(total_re, (h + 1) if h is not None else start)
        if h is None or t is None:
            add(f"{name}: lines found", False, f"heading {h} total {t}")
            return None
        # a total's own lines are the numeric lines since the heading, excluding earlier subtotals
        lines = [i for i in range(h, t) if not re.match(r"^\s*(total|net)\b", rows[i]["label"].lower())]
        equal(name, [sum(rows[i]["vals"][c] for i in lines) for c in range(n)], rows[t]["vals"])
        return t

    if statement == "balance_sheet":
        ta = subtotal("total assets", r".", r"total assets")
        tl = find(r"^total liabilities$")
        tdi = find(r"total deferred inflows")
        tfb = find(r"total fund balances?")
        tla = len(rows) - 1 if re.search(r"and fund balances?$", full(rows[-1]["label"], rows[-1]["prev_text"])) else None
        for nm, v in (("total liabilities", tl), ("total deferred inflows", tdi), ("total fund balances", tfb), ("total liabilities and fund balances", tla)):
            add(f"{nm} line found", v is not None)
        if ta is not None and tla is not None:
            equal("total assets = total liabilities, deferred inflows and fund balances", rows[ta]["vals"], rows[tla]["vals"])
        if tl is not None and tdi is not None and tfb is not None and ta is not None:
            equal("assets = liabilities + deferred inflows + fund balances",
                  rows[ta]["vals"], [rows[tl]["vals"][c] + rows[tdi]["vals"][c] + rows[tfb]["vals"][c] for c in range(n)])
    else:
        tr = find(r"total revenues")
        te = find(r"total expenditures")
        ex = find(r"excess.*(revenues|expenditures)|deficiency")
        tf = find(r"total other financing")
        nc = find(r"net change in fund balances?")
        ed = len(rows) - 1 if re.search(r"end of year|november 30|, end", full(rows[-1]["label"], rows[-1]["prev_text"])) else None
        for nm, v in (("total revenues", tr), ("total expenditures", te), ("excess", ex), ("net change", nc), ("ending balance", ed)):
            add(f"{nm} line found", v is not None)
        if None not in (tr, te, ex):
            equal("excess = revenues - expenditures", rows[ex]["vals"], [rows[tr]["vals"][c] - rows[te]["vals"][c] for c in range(n)])
        if None not in (ex, tf, nc):
            equal("net change = excess + other financing", rows[nc]["vals"], [rows[ex]["vals"][c] + rows[tf]["vals"][c] for c in range(n)])
        elif None not in (ex, nc):
            equal("net change = excess (no other financing printed)", rows[nc]["vals"], rows[ex]["vals"])
        if None not in (nc, ed):
            between = list(range(nc + 1, ed))
            restated = [i for i in between if "restated" in full(rows[i]["label"], rows[i]["prev_text"])]
            if restated:  # 'as reported' + adjustments is printed again as 'as restated'
                between = restated[:1]
            equal("ending = net change + beginning (restated, if any)", rows[ed]["vals"],
                  [rows[nc]["vals"][c] + sum(rows[i]["vals"][c] for i in between) for c in range(n)])
        if tr is not None:
            h = 0
            equal("total revenues = lines", rows[tr]["vals"], [sum(rows[i]["vals"][c] for i in range(h, tr)) for c in range(n)])
        if te is not None and tr is not None:
            equal("total expenditures = lines", rows[te]["vals"], [sum(rows[i]["vals"][c] for i in range(tr + 1, te)) for c in range(n)])
        if tf is not None and ex is not None:
            equal("total other financing = lines", rows[tf]["vals"], [sum(rows[i]["vals"][c] for i in range(ex + 1, tf)) for c in range(n)])
    return checks


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
