"""Extract the General Fund "Schedule of Revenues - Budget and Actual" from a Belvidere audit PDF.

Usage: python3 pipelines/belvidere_il/extract_audit.py FY2020 [--prep auto|text|gray]

The audit's General Fund revenue schedule lists every revenue line (taxes, intergovernmental, licenses,
fines, charges, investment income, miscellaneous) with Original Budget, Final Budget and audited Actual.
Output: data/belvidere_il/extracted/audits/FY20XX_audit_gf_revenue.csv, one row per line item.

Validation: every section's items must sum to the printed section total (labeled "Total ..." or, in older
audits, an unlabeled line of numbers), and the sections to TOTAL REVENUES, in all three columns. Cells the
audit leaves blank (instead of printing a dash) lose their column position in text, so such rows are placed
by trying the possible columns until the section total ties out (flagged `placed_by_sum`).

--prep auto (default) tries the PDF's text layer and 300 dpi grayscale OCR and keeps whichever has fewer
validation problems. Eyeballed corrections live in data/belvidere_il/overrides/audit_FY20XX.csv (same kinds as budgets:
`row` replaces a line's three values keyed by section|label; `total` replaces one printed total column).
"""
import csv
import itertools
import os
import re
import sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_budget import ocr_page  # noqa: E402  (render + tesseract with the same preprocessing)

ROOT = os.path.join(HERE, "..", "..")
DATA_E = os.path.join(ROOT, "data", "belvidere_il")
SRC_E = os.path.join(ROOT, "sources", "belvidere_il")
COLS = ["original_budget", "final_budget", "actual"]
FIX = str.maketrans({"r": "1", "t": "1", "s": "5", "S": "5", "O": "0", "o": "0", "l": "1", "I": "1", "g": "9", "B": "8", "Q": "0"})
SKIP = re.compile(r"(?i)^((19|20)\d\d(\s+(19|20)\d\d)*$|variance|positive|city of belvidere|schedule of revenues|general fund$|for the year ended|year ended|original\b|budget\b|"
                  r"\(?this schedule is continued|\(?see independent|- ?\d+ ?-?$|\d+$|with comparative|for the fiscal year|for the year)")


def audit_pdf(fy):
    return os.path.join(SRC_E, "audits", f"FY{fy}_audit.pdf")


# ---- locating the schedule ---------------------------------------------------------------------
def page_texts_for_locating(fy):
    """Cheap per-page text used only to find the schedule: text layer, or the old full-document OCR for FY2012."""
    with pdfplumber.open(audit_pdf(fy)) as d:
        texts = [(p.extract_text() or "") for p in d.pages]
    if sum(len(t) for t in texts[:5]) < 100:  # scanned (FY2012): use the whole-document OCR sidecar
        side = os.path.join(DATA_E, "ocr", f"FY{fy}_audit.txt")
        if os.path.exists(side):
            texts = open(side).read().split("\f")
    return texts


def locate(fy):
    """(first, last) 1-based pdf pages of the detailed General Fund revenue schedule."""
    texts = page_texts_for_locating(fy)
    start = None
    for i, t in enumerate(texts, 1):
        u = t.upper()
        if "SCHEDULE OF REVENUES" in u and "BUDGET" in u and "GENERAL FUND" in u[:400] and "TAXES" in u \
                and "TOTAL REVENUES" not in u and "CONTINUED" not in u[:300]:
            start = i
            break
    if start is None:
        raise SystemExit(f"FY{fy}: schedule start page not found")
    for j in range(start, min(start + 4, len(texts)) + 1):
        if "TOTAL REVENUES" in texts[j - 1].upper():
            return start, j
    raise SystemExit(f"FY{fy}: TOTAL REVENUES page not found after page {start}")


def get_pages(fy, prep, first, last):
    if prep == "text":
        with pdfplumber.open(audit_pdf(fy)) as d:
            return [(p, d.pages[p - 1].extract_text() or "") for p in range(first, last + 1)]
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    return [(p, ocr_page(audit_pdf(fy), p, outdir, prep)) for p in range(first, last + 1)]


# ---- parsing -----------------------------------------------------------------------------------
def to_int(tok):
    t = tok.strip().replace("{", "(").replace("}", ")")
    neg = t.startswith("(")
    t = t.strip("()$")
    if t.startswith("-") and len(t) > 1:
        neg, t = True, t[1:]
    if not any(ch.isdigit() for ch in t):
        return None
    t = t.translate(FIX).replace(",", "").replace(".", "").replace(" ", "")
    if not t.isdigit():
        return None
    return -int(t) if neg else int(t)


def clean(line):
    line = line.replace("$", " ").replace("§", " ")  # OCR renders the $ sign as § 
    line = re.sub(r"(?<=\d)\s+,(?=\d)", ",", line)       # "3 ,440" -> "3,440"
    line = re.sub(r"(?<=\d),\s+(?=\d{3}\b)", ",", line)  # "1, 101, 104" -> "1,101,104"
    line = re.sub(r"\(cid:\d+\)", " ", line)
    line = re.sub(r"[“”\"`‘’_]", " ", line)                   # stray quote/underscore junk from ruled lines
    line = re.sub(r"(\d,\d{2})\s*\|", r"\g<1>1", line)          # "4,797,63 |" -> "4,797,631" (| is a misread 1)
    return line.strip(" ;:|,")


def split_numbers(line):
    """(label, [ints]) where trailing numeric tokens are values and a dash is 0."""
    toks = [t for t in clean(line).split() if t not in ("|", "!")]
    vals = []
    while toks:
        tk = toks[-1]
        if tk in ("-", "—", "–"):
            v = 0
        else:
            v = to_int(tk)
        if v is None:
            break
        vals.append(v)
        toks.pop()
    return " ".join(toks).strip(" :."), list(reversed(vals))


def norm_heading(label):
    return re.sub(r"(?i)\s*[-–]?\s*\(?continued\)?", "", label).strip(" -:").title()


PRIOR = {}  # (section, label) -> prior-year actual, only for audits whose schedule prints it (FY2012)


def parse(pages):
    """Returns sections: list of dict(name, items=[(label, vals, page)], total=(label, vals) or None), grand total."""
    sections, cur, grand = [], None, None
    for pg, text in pages:
        for raw in text.splitlines():
            line = clean(raw)
            if not line or SKIP.match(line) or sum(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ,.()/&'-:" for ch in line) > 3:
                continue  # blank, page furniture, or OCR junk from a ruled line
            label, vals = split_numbers(line)
            prior = None
            if len(vals) == 5:  # FY2012 layout: original, final, actual, variance, prior-year actual
                prior = vals[4]
                vals = vals[:3]
            if not vals:  # heading
                if label and 3 <= len(label) < 60 and sum(ch.isalpha() for ch in label) >= 3 \
                        and not re.search(r"(?i)^(for the|\(this|\(see)", label):
                    name = norm_heading(label)
                    if cur is None or cur["name"] != name:
                        cur = dict(name=name, items=[], total=None)
                        sections.append(cur)
                continue
            if re.match(r"(?i)^total revenues?$", label):
                grand = (label, vals)
                continue
            if cur is None:
                cur = dict(name="(start)", items=[], total=None)
                sections.append(cur)
            if re.match(r"(?i)^total\b", label) or (not label and len(vals) == 3):
                cur["total"] = (label or "(unlabeled total)", vals)
            else:
                if cur["total"] is not None:  # a labeled row after a closed section starts a new (single-line) one, e.g. "Interest"
                    cur = dict(name=norm_heading(label), items=[], total=None)
                    sections.append(cur)
                cur["items"].append((label, vals, pg))
                if prior is not None:
                    PRIOR[(cur["name"], label)] = prior
    return sections, grand


def place_partial(items, total):
    """Rows with fewer than 3 values: try column placements so the section total ties out in all columns."""
    partial = [i for i, (_, v, _) in enumerate(items) if len(v) < 3]
    full = [i for i in range(len(items)) if i not in partial]
    base = [sum(items[i][1][c] for i in full) for c in range(3)]
    options = []
    for i in partial:
        k = len(items[i][1])
        options.append([tuple(c) for c in itertools.combinations(range(3), k)])
    for combo in itertools.product(*options):
        sums = list(base)
        for i, cols in zip(partial, combo):
            for val, c in zip(items[i][1], cols):
                sums[c] += val
        if sums == list(total):
            return {i: cols for i, cols in zip(partial, combo)}
    return None


def validate(sections, grand, overrides):
    problems, notes, rows = [], [], []
    sec_tot = []
    for s in sections:
        items = [list(it) for it in s["items"]]
        flags = {}
        for k, (label, vals, pg) in enumerate(items):
            key = f"{s['name']}|{label}"
            if key in overrides["row"]:
                items[k][1] = overrides["row"][key]
                flags[k] = "values_overridden"
        total = s["total"][1] if s["total"] else None
        if total:
            for c, name in enumerate(COLS):
                ov = overrides["total"].get((s["name"], name))
                if ov is not None and len(total) == 3:
                    total[c] = ov
        partial = any(len(v) < 3 for _, v, _ in items)
        if partial:
            plc = place_partial([tuple(i) for i in items], total) if total and len(total) == 3 else None
            if plc is None:
                problems.append(f"{s['name']}: rows with blank cells could not be placed: " +
                                "; ".join(f"{l} {v}" for l, v, _ in items if len(v) < 3))
                continue
            for k, cols in plc.items():
                new = [0, 0, 0]
                for val, c in zip(items[k][1], cols):
                    new[c] = val
                items[k][1] = new
                flags[k] = "placed_by_sum"
        else:
            if total is not None and len(total) == 3:
                for c, name in enumerate(COLS):
                    got = sum(v[c] for _, v, _ in items)
                    if got != total[c] and overrides["allow"].get((s["name"], name)) == total[c] - got:
                        notes.append(f"{s['name']} {name}: printed total is {total[c]-got:+} vs its own items (source rounding; allowed by override)")
                    elif got != total[c]:
                        problems.append(f"{s['name']} {name}: items {got:,} vs printed total {total[c]:,} (diff {total[c]-got:,})")
            elif s["items"] and total is None:
                if len(s["items"]) > 1:
                    problems.append(f"{s['name']}: no section total found for {len(s['items'])} items")
        bad = [(l, v) for l, v, _ in items if len(v) != 3]
        if bad:
            problems.append(f"{s['name']}: rows without 3 values: {bad}")
            continue
        for k, (label, vals, pg) in enumerate(items):
            rows.append(dict(section=s["name"], line_item=label, original_budget=vals[0], final_budget=vals[1],
                             actual=vals[2], pdf_page=pg, flag=flags.get(k, ""),
                             prior_year_actual=PRIOR.get((s["name"], label), "")))
        # section value for the grand-total check: printed total when present, else the item sum
        sec_tot.append(total if total and len(total) == 3 else [sum(v[c] for _, v, _ in items) for c in range(3)])
    if grand is None or len(grand[1]) != 3:
        problems.append(f"TOTAL REVENUES line not read: {grand}")
    else:
        g = list(grand[1])
        for c, name in enumerate(COLS):
            ov = overrides["total"].get(("TOTAL REVENUES", name))
            if ov is not None:
                g[c] = ov
            got = sum(t[c] for t in sec_tot)
            if got != g[c] and overrides["allow"].get(("TOTAL REVENUES", name)) == g[c] - got:
                notes.append(f"TOTAL REVENUES {name}: printed total is {g[c]-got:+} vs its own sections (allowed by override)")
            elif got != g[c]:
                problems.append(f"TOTAL REVENUES {name}: sections {got:,} vs printed {g[c]:,} (diff {g[c]-got:,})")
    return rows, problems, notes


def load_overrides(fy):
    ov = dict(row={}, total={}, allow={})
    path = os.path.join(DATA_E, "overrides", f"audit_FY{fy}.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "row":
                ov["row"][o["key"]] = [int(x) for x in o["value"].split("|")]
            elif o["kind"] == "allow_diff":
                sec, col = o["key"].rsplit("|", 1)
                ov["allow"][(sec, col)] = int(o["value"])
            elif o["kind"] == "total":
                sec, col = o["key"].rsplit("|", 1)
                ov["total"][(sec, col)] = int(o["value"])
    return ov


def run(fy, prep):
    first, last = locate(fy)
    pages = get_pages(fy, prep, first, last)
    sections, grand = parse(pages)
    rows, problems, notes = validate(sections, grand, load_overrides(fy))
    return dict(prep=prep, first=first, last=last, rows=rows, problems=problems, notes=notes)


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    preps = ["text", "gray"] if arg == "auto" else [arg]
    results = []
    for p in preps:
        r = run(fy, p)
        results.append(r)
        print(f"prep {p}: {len(r['problems'])} problems, {len(r['rows'])} lines (pdf pages {r['first']}-{r['last']})")
    res = min(results, key=lambda r: (not r["rows"], len(r["problems"])))  # a result with no lines never wins
    os.makedirs(os.path.join(DATA_E, "extracted", "audits"), exist_ok=True)
    out = os.path.join(DATA_E, "extracted", "audits", f"FY{fy}_audit_gf_revenue.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "section", "line_item", "original_budget", "final_budget", "actual", "flag", "prior_year_actual"])
        for r in res["rows"]:
            w.writerow([fy, f"FY{fy}_audit", r["pdf_page"], r["section"], r["line_item"], r["original_budget"],
                        r["final_budget"], r["actual"], r["flag"], r["prior_year_actual"]])
    print(f"\nchosen prep: {res['prep']}; {len(res['rows'])} lines -> {os.path.relpath(out, ROOT)}; {len(res['problems'])} problems")
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
