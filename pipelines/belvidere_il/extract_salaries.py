"""Extract Exhibit B (the non-union salary schedule) of a Belvidere budget ordinance.

Usage: python3 pipelines/belvidere_il/extract_salaries.py FY2020          # one budget ordinance
       python3 pipelines/belvidere_il/extract_salaries.py FY2027R         # ordinance 780H, which revises FY2027's Exhibit B
       python3 pipelines/belvidere_il/extract_salaries.py all

Exhibit B is one page of 18-23 appointed or non-union positions (FY2015-FY2023 name the incumbent) with the pay in the
previous year and the new year (FY2023-FY2024 print three years; FY2026-FY2027 add a percentage increase). Reads the
page from the cached OCR of data/belvidere_il/ocr/budget_pages/ (two preps, gray and thr150; the page is located by its
"Salary Schedule" heading), writes
  data/belvidere_il/extracted/FY20XX_budget_salaries.csv         one row per position x year column
  data/belvidere_il/extracted/FY20XX_budget_salaries_totals.csv  the printed column totals, "Total Increase" lines and the checks
Validation: where the schedule prints a total (FY2022 onward), each column's positions must add to it (a cent of rounding
is allowed); where it prints a percentage, it must equal the change between the two columns; the two OCR preps must agree
cell by cell. Positions are mapped to stable ids by entities/belvidere_il/salary_positions.csv. Eyeballed fixes from the
page image: data/belvidere_il/overrides/FY20XX_budget_salaries.csv (kind cell | allow_diff; a written reason is required).
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_budget import DATA_E, SRC_E, ocr_page, page_count  # noqa: E402

ROOT = os.path.join(HERE, "..", "..")
POSITIONS = os.path.join(ROOT, "entities", "belvidere_il", "salary_positions.csv")
PREPS = ["gray", "thr150"]
YEAR_COLUMNS = {2023: 3, 2024: 3}  # every other document prints two years
AMOUNT = re.compile(r"(?<![\d,.])\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?![\d,])")
PCT = re.compile(r"(\d{1,2})[.,](\d{2})\s?%")
CURRENCY = {"S", "$", "5", "§", "s", "S$", "$$", "5S", "i}", "i", "$S", "3"}
FOOT = "*“”‘’\"'=—–©™«»|"
OUT_COLS = ["doc_id", "document_fy", "version", "pdf_page", "row_seq", "label_printed", "position_id", "slot", "incumbent_printed",
            "status", "period_fy", "column_header", "amount", "pct_printed", "flag"]
TOTAL_COLS = ["doc_id", "document_fy", "period_fy", "kind", "printed", "computed", "diff", "n_positions", "status", "detail"]


def positions():
    return [(r["position_id"], re.compile("^" + r["label_pattern"], re.I)) for r in csv.DictReader(open(POSITIONS))]


def doc_info(arg):
    m = re.fullmatch(r"FY(\d{4})(R?)", arg)
    if not m:
        sys.exit(f"bad document {arg!r}")
    fy, rev = int(m.group(1)), bool(m.group(2))
    if rev:
        return fy, True, f"FY{fy}_budget_revision_780H", os.path.join(SRC_E, f"FY{fy}_budget_revision_780H.pdf"), f"FY{fy}R"
    pdf = os.path.join(SRC_E, "budgets", f"FY{fy}_budget.pdf")
    if not os.path.exists(pdf):
        pdf = os.path.join(SRC_E, f"FY{fy}_budget_753H.pdf")
    return fy, False, f"FY{fy}_budget", pdf, f"FY{fy}"


def page_text(arg, prep):
    """(pdf page number, text) of the Exhibit B page in one OCR prep (OCR run now if not cached)."""
    fy, rev, doc, pdf, tag = doc_info(arg)
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"{tag}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    n = page_count(pdf)
    cands = []
    for p in range(1, n + 1):
        t = ocr_page(pdf, p, outdir, prep)
        if re.search(r"salary\s+schedule", t, re.I) and not re.search(r"table of contents", t, re.I):
            cands.append((p, t))
    if len(cands) != 1:
        sys.exit(f"{arg} ({prep}): expected one Salary Schedule page, found {[p for p, _ in cands]}")
    return cands[0]


def money(tok):
    return round(float(tok.replace(",", "")), 2)


def parse(text, fy, ncols, pos):
    """Rows (label, position, slot, incumbent, [amounts or None per column], pct), totals and increases from one OCR reading."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    rows, totals, increases = [], None, []
    slots = {}
    for ln in lines:
        pct = PCT.search(ln)
        body = PCT.sub(" ", ln)
        amts = [m for m in AMOUNT.finditer(body)]
        na = re.search(r"\bN/A\b", body) is not None
        if not amts:
            continue
        label = body[:amts[0].start()].strip()
        toks = label.split()
        while toks and toks[-1] in CURRENCY:
            toks.pop()
        label = " ".join(toks).strip(FOOT + " ")
        vals = [money(m.group(0)) for m in amts]
        if not label:
            if len(vals) >= 2 and min(vals) > 100000 and totals is None:
                totals = vals
            continue
        if re.match(r"(?i)total\s?increase", label):
            increases = vals
            continue
        pid = next((p for p, rx in pos if rx.match(label.lstrip(FOOT + " "))), None)
        if pid is None:
            if len(vals) >= 2 and min(vals) > 15000 and totals is None:
                sys.exit(f"FY{fy}: unknown position label {label!r}: add it to salary_positions.csv")
            continue
        if totals is not None:
            continue
        cells = ([None] if na else []) + vals
        cells = [None] * (ncols - len(cells)) + cells if len(cells) < ncols else cells
        if len(cells) != ncols:
            sys.exit(f"FY{fy}: {label!r}: {len(cells)} amounts, expected {ncols}")
        slots[pid] = slots.get(pid, 0) + 1
        clean = label.lstrip(FOOT + " ")
        rx = dict(pos)[pid]
        inc = rx.sub("", clean, count=1).strip(FOOT + " ")
        inc = re.sub(r"^[*\s]+", "", inc)
        rows.append(dict(label=label, pid=pid, slot=slots[pid], incumbent=inc, cells=cells,
                         pct=float(f"{pct.group(1)}.{pct.group(2)}") if pct else None,
                         pt=bool(re.search(r"\bPT\b|part-time", label, re.I))))
    return rows, totals, increases


def read_overrides(tag):
    path = os.path.join(DATA_E, "overrides", f"{tag}_budget_salaries.csv")
    if not os.path.exists(path):
        return []
    out = list(csv.DictReader(open(path)))
    for r in out:
        if not r["reason"].strip():
            sys.exit(f"{path}: every override needs a reason")
    return out


def extract(arg):
    fy, rev, doc, pdf, tag = doc_info(arg)
    ncols = YEAR_COLUMNS.get(fy, 2)
    pos = positions()
    readings = {}
    for prep in PREPS:
        p, t = page_text(arg, prep)
        readings[prep] = (p,) + parse(t, fy, ncols, pos)
    pdf_page = readings["gray"][0]
    periods = [fy - ncols + 1 + i for i in range(ncols)]
    g, t150 = readings["gray"][1], readings["thr150"][1]
    problems, notes = [], []
    over = read_overrides(tag)
    ov = {(o["position_id"], int(o["slot"] or 1), int(o["period_fy"])): o for o in over if o["kind"] == "cell"}
    allow = {(o["kind"], o["period_fy"], o["position_id"]): o for o in over if o["kind"].startswith("allow")}

    # reading 1 (gray) is the base; the other prep must agree, or an override decides
    if [r["pid"] for r in g] != [r["pid"] for r in t150]:
        problems.append(f"preps disagree on the list of positions: {len(g)} vs {len(t150)}")
    for a, b in zip(g, t150):
        for i, (x, y) in enumerate(zip(a["cells"], b["cells"])):
            if x != y and (a["pid"], a["slot"], periods[i]) not in ov:
                problems.append(f"{a['pid']} FY{periods[i]}: gray {x} vs thr150 {y}")
    rows = []
    for seq, r in enumerate(g, 1):
        cells = list(r["cells"])
        flag = []
        for i, per in enumerate(periods):
            o = ov.get((r["pid"], r["slot"], per))
            if o:
                cells[i] = money(o["value"])
                flag.append("override")
        for i, per in enumerate(periods):
            if cells[i] is None:
                continue
            f = list(set(flag)) + (["na_printed"] if False else [])
            rows.append(dict(doc_id=doc, document_fy=fy, version="revised" if rev else "adopted", pdf_page=pdf_page, row_seq=seq,
                             label_printed=r["label"], position_id=r["pid"], slot=r["slot"], incumbent_printed=r["incumbent"],
                             status="part_time" if r["pt"] else "", period_fy=per, column_header=f"FY{per}",
                             amount=f"{cells[i]:.2f}", pct_printed=f"{r['pct']:.2f}" if r["pct"] is not None and per == fy else "",
                             flag="|".join(f)))
    # validation: column sums vs printed totals, increases, printed percentages
    totals = []
    tot_t, inc_t = readings["thr150"][2], readings["thr150"][3]
    tot_g, inc_g = readings["gray"][2], readings["gray"][3]
    if tot_g != tot_t:
        notes.append("printed totals differ between preps (the gray reading is the one that ties)")
    by_period = {per: round(sum(float(r["amount"]) for r in rows if r["period_fy"] == per), 2) for per in periods}
    n_by = {per: sum(1 for r in rows if r["period_fy"] == per) for per in periods}
    if tot_g:
        if len(tot_g) != ncols:
            problems.append(f"{len(tot_g)} printed totals for {ncols} columns")
        else:
            for per, pr in zip(periods, tot_g):
                d = round(by_period[per] - pr, 2)
                ok = abs(d) <= 0.02
                al = allow.get(("allow_diff", str(per), "(total)"))
                st = "ok" if ok else ("allowed" if al and abs(d - float(al["value"])) < 0.005 else "FAIL")
                totals.append(dict(doc_id=doc, document_fy=fy, period_fy=per, kind="total", printed=f"{pr:.2f}",
                                   computed=f"{by_period[per]:.2f}", diff=f"{d:.2f}", n_positions=n_by[per], status=st,
                                   detail=al["reason"] if st == "allowed" else ("cents rounding" if ok and d else "")))
                if st == "FAIL":
                    problems.append(f"FY{per} positions add to {by_period[per]:,.2f}, printed total {pr:,.2f} (diff {d:,.2f})")
        if inc_g and len(tot_g) == ncols:
            # "Total Increase" lines: one per year after the first column printed
            for k, v in enumerate(inc_g):
                per = periods[len(periods) - len(inc_g) + k]
                prev = tot_g[len(periods) - len(inc_g) + k - 1] if len(periods) - len(inc_g) + k - 1 >= 0 else None
                cur = tot_g[len(periods) - len(inc_g) + k]
                if prev is not None:
                    d = round(cur - prev - v, 2)
                    st = "ok" if abs(d) <= 0.02 else "FAIL"
                    totals.append(dict(doc_id=doc, document_fy=fy, period_fy=per, kind="total_increase", printed=f"{v:.2f}",
                                       computed=f"{cur - prev:.2f}", diff=f"{d:.2f}", n_positions="", status=st, detail=""))
                    if st == "FAIL":
                        problems.append(f"FY{per} Total Increase {v:,.2f} vs difference of printed totals {cur - prev:,.2f}")
    for r in g:
        if r["pct"] is not None and r["cells"][-1] is not None and r["cells"][-2] is not None:
            calc = (r["cells"][-1] / r["cells"][-2] - 1) * 100
            if abs(calc - r["pct"]) > 0.011:
                problems.append(f"{r['pid']}: printed {r['pct']:.2f}% but the two columns give {calc:.2f}%")
    if not tot_g:
        notes.append("no total printed")
    return rows, totals, problems, notes, doc


def write(arg):
    rows, totals, problems, notes, doc = extract(arg)
    if problems:
        print(f"{arg}: {len(problems)} problems")
        for p in problems:
            print("  ", p)
        return False
    d = os.path.join(DATA_E, "extracted")
    os.makedirs(d, exist_ok=True)
    tag = doc_info(arg)[4]
    for suffix, cols, data in (("budget_salaries", OUT_COLS, rows), ("budget_salaries_totals", TOTAL_COLS, totals)):
        with open(os.path.join(d, f"{tag}_{suffix}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, cols, lineterminator="\n")
            w.writeheader()
            w.writerows(data)
    print(f"{arg}: {len(rows)} cells, {len(totals)} total checks, 0 problems" + (f" ({'; '.join(notes)})" if notes else ""))
    return True


if __name__ == "__main__":
    args = sys.argv[1:] or sys.exit(__doc__)
    if args == ["all"]:
        args = [f"FY{y}" for y in range(2015, 2028)] + ["FY2027R"]
    ok = [write(a) for a in args]
    sys.exit(0 if all(ok) else 1)
