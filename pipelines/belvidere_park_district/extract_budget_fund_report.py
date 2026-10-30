"""Extract a Belvidere Park District "Budget Report by Fund - All" (layout C: the adopted FY2024 budget, 8 pages, printed 12/14/2023).

Usage: python3 pipelines/belvidere_park_district/extract_budget_fund_report.py FY2024        (document id FY2024_budget)

Layout: per `Fund: NN Name`, `Expenditure` lines then `Revenue` lines (`FF-DDDD-OOO  label  Budgeted  Current  Year To Date  Encumbrance  Remaining Balance
PCT`), then `<Fund> Total Revenue`, `<Fund> Total Expenditure`, `<Fund> Net`; the report ends with `Report Total Revenue`, `Report Total Expenditure`, `Report
Totals Net`. Nothing was spent when it was printed, so Current / Year To Date / Encumbrance are 0.00 and Remaining Balance equals Budgeted: every line prints its
amount twice, which gives two independent readings of it.

The scan defeats whole-page OCR (dotted rules run through the numbers; only half the lines read cleanly). What works: render each page at 300 dpi gray, erase the
long horizontal runs (the rules), find the text rows from the ink profile, and read per row (tesseract, one line at a time, digits only) the Budgeted column and
the Remaining Balance column, each at 1x and 2x, plus the words of a whole-page reading placed by x. Those are up to six readings of one number.
Accounts: the row's account number is read from the label and matched to the chart of accounts the neighbouring draft (FY2024_budget_draft, the same budget before
adoption) prints; a row whose label OCR cannot read takes the one account of that chart that no other row has and that lies between its neighbours (flag
`account_inferred`). The line item text is the draft's.
Amounts (ladder, every step after the first is flagged and recorded in `<doc_id>_corrections.csv`): (1) a number that at least two readings give, no other number gives twice and the draft's amount for the line equals; (2) the same line of the draft when it is one of the readings, flagged `auto_resolved`; (3) an override from the page image (`data/belvidere_park_district/overrides/FY2024_budget.csv`, kind `amount`, with the reason).
Output (data/belvidere_park_district/extracted/<doc_id>_{revenue,expense,totals,corrections}.csv): the long format of layout A, amount column `budgeted`;
totals have kinds `fund_subtotal` (per fund and flow), `grand` (Report Total Revenue / Expenditure) and `report_net`.
Validation (a failure exits non-zero and writes nothing): every fund's revenue lines = its Total Revenue and expense lines = its Total Expenditure; Net = revenue -
expenditure; the funds add up to the Report totals; Report Totals Net = revenue - expenditure. The printed totals are read the same way (two agreeing readings,
else an override kind `total`, name `NN|revenue`, `NN|expense`, `report|revenue`, `report|expense`, `report|net`).
"""
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from difflib import SequenceMatcher

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_budget_operating as A  # noqa: E402  load_overrides

ENTITY = R.ENTITY
ACCT = re.compile(r"(\d\d)\s?-\s?(\d{4})\s?-\s?(\d{3})")
MONEY_TOKEN = re.compile(r"\d[\d,]*\.\d\d")
BUD_X, REM_X = (860, 1265), (2020, 2310)  # right-aligned amount columns at 300 dpi
LABEL_X = (240, 900)
NEIGHBOR = {"FY2024_budget": "FY2024_budget_draft"}


def money(s):
    s = re.sub(r"[^\d,.]", "", s or "").strip(",.")
    if re.fullmatch(r"\d{1,3}(,\d{3})*\.\d\d", s) or re.fullmatch(r"\d+\.\d\d", s):
        return Decimal(s.replace(",", ""))
    return None


def render(pdf, doc):
    """300 dpi gray page images, cached next to the OCR text (gitignored)."""
    cache = os.path.join(R.ROOT, "data", ENTITY, "ocr", f"{doc}_cols300")
    os.makedirs(cache, exist_ok=True)
    pages = int(next(l.split()[1] for l in subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, check=True).stdout.splitlines() if l.startswith("Pages")))
    out = []
    for p in range(1, pages + 1):
        path = os.path.join(cache, f"page-{p}.png")
        if not os.path.exists(path):
            subprocess.run(["pdftoppm", "-r", "300", "-f", str(p), "-l", str(p), "-gray", "-png", "-singlefile", pdf, path[:-4]], check=True)
            erase(Image.open(path), 30).save(path)  # the cache holds the rule-erased page
        out.append(path)
    return out


def erase(img, minrun):
    """White out every horizontal run of dark pixels at least `minrun` long: the table rules, dotted rules included (a digit stroke is far shorter)."""
    a = np.array(img.convert("L"))
    dark = a < 170
    for y in np.where(dark.sum(1) >= minrun)[0]:
        d = np.diff(np.concatenate(([0], dark[y].astype(np.int8), [0])))
        for x0, x1 in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if x1 - x0 >= minrun:
                a[y, x0:x1] = 255
    return Image.fromarray(a)


def text_bands(img):
    ink = (np.array(img) < 150)[:, LABEL_X[0]:LABEL_X[1]].sum(1)
    out, start = [], None
    for y, v in enumerate(ink):
        if v >= 6 and start is None:
            start = y
        if v < 6 and start is not None:
            if y - start > 12:
                out.append((start, y))
            start = None
    return out


def words(path):
    t = subprocess.run(["tesseract", path, "-", "--psm", "6", "tsv"], capture_output=True, text=True).stdout
    out = []
    for line in t.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 12 and f[11].strip():
            out.append((int(f[6]), int(f[7]), int(f[8]), int(f[9]), f[11]))
    return out


def crop_read(args):
    path, y0, y1, x0, x1, scale, tmp = args
    im = Image.open(path).crop((x0, max(0, y0 - 8), x1, y1 + 8))
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    bg = Image.new("L", (im.width + 40, im.height + 40), 255)
    bg.paste(im, (20, 20))
    bg.save(tmp)
    return subprocess.run(["tesseract", tmp, "-", "--psm", "7", "-c", "tessedit_char_whitelist=0123456789,.-"], capture_output=True, text=True).stdout.strip()


def read_rows(pages, tmpdir):
    """One dict per text row of every page: page, label (whole-page reading) and the amounts the readings of the row give."""
    rows = []
    for pdf_page, path in enumerate(pages, start=1):
        img = Image.open(path)
        bands = text_bands(img)
        ws = words(path)
        jobs = [(path, y0, y1, x0, x1, sc, os.path.join(tmpdir, f"c_{pdf_page}_{i}_{k}_{sc}.png"))
                for i, (y0, y1) in enumerate(bands) for k, (x0, x1) in (("b", BUD_X), ("r", REM_X)) for sc in (1, 2)]
        with ThreadPoolExecutor(8) as ex:
            got = list(ex.map(crop_read, jobs))
        crops = defaultdict(list)
        for j, g in zip(jobs, got):
            crops[os.path.basename(j[6]).split("_")[2]].append(g)
        for i, (y0, y1) in enumerate(bands):
            yc = (y0 + y1) / 2
            inrow = sorted(w for w in ws if abs(w[1] + w[3] / 2 - yc) < 22)
            label = " ".join(w[4] for w in inrow if w[0] < 1000 and w[0] + w[2] < 1300)
            wb = [w[4] for w in inrow if BUD_X[0] < w[0] + w[2] < BUD_X[1] and MONEY_TOKEN.search(w[4])]
            wr = [w[4] for w in inrow if REM_X[0] < w[0] + w[2] < REM_X[1] and MONEY_TOKEN.search(w[4])]
            readings = [v for v in (money(s) for s in wb + wr + crops[str(i)]) if v is not None]
            rows.append(dict(pdf_page=pdf_page, y=y0, label=label, votes=Counter(readings)))
    return rows


def vote(votes):
    """The one number at least two readings give (None when no number has two or two numbers do)."""
    top = [v for v, n in votes.items() if n >= 2]
    return top[0] if len(top) == 1 else None


def chart_of_accounts(doc):
    """Ordered (account, flow, line_item) of the neighbouring draft: per fund, its expense lines then its revenue lines (the printed order)."""
    other = NEIGHBOR[doc]
    base = os.path.join(R.ROOT, "data", ENTITY, "extracted")
    rec = {flow: [r for r in csv.DictReader(open(os.path.join(base, f"{other}_{flow}.csv")))] for flow in ("expense", "revenue")}
    funds = sorted({r["fund_code"] for fl in rec.values() for r in fl})
    return [(r["account"], flow, r["line_item"]) for f in funds for flow in ("expense", "revenue") for r in rec[flow] if r["fund_code"] == f]


def draft_amounts(doc):
    base = os.path.join(R.ROOT, "data", ENTITY, "extracted")
    return {r["account"]: Decimal(r["amount"]) for flow in ("expense", "revenue") for r in csv.DictReader(open(os.path.join(base, f"{NEIGHBOR[doc]}_{flow}.csv")))}


def fund_names(doc):
    """Fund code -> name as the neighbouring draft's fund subtotals print it."""
    path = os.path.join(R.ROOT, "data", ENTITY, "extracted", f"{NEIGHBOR[doc]}_totals.csv")
    return {r["fund_code"]: r["name"] for r in csv.DictReader(open(path)) if r["kind"] == "fund_subtotal"}


def similar(ocr_text, item):
    """How much the OCR'd label text resembles the chart's line item (an account number alone can be misread into another line's number)."""
    norm = lambda t: re.sub(r"[^a-z]", "", t.lower())  # noqa: E731
    a, b = norm(ocr_text), norm(item)
    return SequenceMatcher(None, a[:len(b) + 6], b).ratio() if b else 0


HEADING = re.compile(r"(?i)(fund:|expend|revenue|melissam|budget report|budget-report|fiscal year|current period|belvidere|^\W*account|budgeted|balance|budget-report|page \d|total|\bnet\b|date)")


def assign_accounts(rows, chart):
    """Give every line row its account and find the total rows. Anchors are rows whose label shows an account of the chart, in chart order; between two
    anchors the rows that are neither a heading nor a total take, in order, the chart accounts that lie between them (flag `account_inferred`).
    Returns the lines [(row, account, flow, item, flags)], the total rows and the chart accounts no row got."""
    known = {a: (fl, it) for a, fl, it in chart}
    order = [a for a, _, _ in chart]
    last = -1
    for r in rows:
        r["acct"] = None
        best = max(((similar(r["label"][m.end():], known["-".join(m.groups())][1]), "-".join(m.groups())) for m in ACCT.finditer(r["label"])
                    if "-".join(m.groups()) in known and order.index("-".join(m.groups())) > last), default=(0, None))  # a label can print two account numbers: the text after the right one describes it
        if best[0] >= 0.5:
            r["acct"], last = best[1], order.index(best[1])
    totals = [r for r in rows if r["acct"] is None and re.search(r"(?i)total|net\b", r["label"])]
    anchors = [n for n, r in enumerate(rows) if r["acct"]]
    bounds = [-1] + anchors + [len(rows)]
    problems = []
    for lo, hi in zip(bounds, bounds[1:]):
        olo = order.index(rows[lo]["acct"]) if lo >= 0 else -1
        ohi = order.index(rows[hi]["acct"]) if hi < len(rows) else len(order)
        gap = order[olo + 1:ohi]
        cand = [r for r in rows[lo + 1:hi] if r not in totals and not (HEADING.search(r["label"]) and not r["votes"]) and not re.search(r"(?i)melissam|budget-report|date|fund:|page \d", r["label"])]
        if len(cand) > len(gap):
            cand = [r for r in cand if r["votes"] or len(r["label"]) > 8]
        if len(cand) == len(gap):
            for r, a in zip(cand, gap):
                r["acct"], r["inferred"] = a, True
        elif gap or cand:
            problems.append(f"{len(cand)} unplaced rows (pdf{(cand or rows[lo + 1:hi] or [rows[lo]])[0]['pdf_page']}) for chart accounts {gap}")
    lines = [(r, r["acct"], known[r["acct"]][0], known[r["acct"]][1], ["account_inferred"] if r.get("inferred") else []) for r in rows if r["acct"]]
    seen = {a for _, a, _, _, _ in lines}
    return lines, totals, [a for a in order if a not in seen], problems


def total_key(r, last_fund):
    """(`NN|revenue`...) of a printed total row from its label."""
    lab = r["label"]
    flow = "expense" if re.search(r"(?i)expend|[#E]xpend", lab) else "revenue" if re.search(r"(?i)revenue", lab) else "net" if re.search(r"Net\b", lab) else None
    if re.search(r"(?i)^\W*Rep", lab):
        return f"report|{flow or 'net'}"
    return f"{last_fund}|{flow}" if flow else None


def parse(pdf, fy, doc, overrides, corrections, tmpdir):
    problems = []
    cache = os.path.join(R.ROOT, "data", ENTITY, "ocr", f"{doc}_cols300", "rows.json")  # the readings of every row (delete to read again)
    if os.path.exists(cache):
        rows = [dict(r, votes=Counter({Decimal(k): n for k, n in r["votes"].items()})) for r in json.load(open(cache))]
    else:
        rows = read_rows(render(pdf, doc), tmpdir)
        json.dump([dict(r, votes={str(k): n for k, n in r["votes"].items()}) for r in rows], open(cache, "w"))
    chart = chart_of_accounts(doc)
    draft = draft_amounts(doc)
    lines, total_rows, missing, gap_problems = assign_accounts(rows, chart)
    problems += gap_problems
    for a in missing:
        problems.append(f"account of the chart not found on any row: {a}")
    ov_amount = {o["raw"]: o for o in overrides if o["kind"] == "amount"}
    ov_total = {o["raw"]: o for o in overrides if o["kind"] == "total"}
    used = set()
    out = []
    for r, acct, flow, item, flags in lines:
        v, flags = vote(r["votes"]), list(flags)
        raw = ", ".join(f"{k}:{n}" for k, n in r["votes"].most_common(4)) or "no reading"
        o = ov_amount.get(acct)
        dv = draft[acct]
        if o:
            used.add(("amount", acct))
            fixed = Decimal(o["fixed"])
            if v != fixed:  # an override that only confirms the OCR's number is no correction
                corrections.append((doc, f"pdf{r['pdf_page']}", r["pdf_page"], "line_prefix", f"{acct} {item} {R.clean(v) if v is not None else raw}",
                                    f"{acct} {item} {R.clean(fixed)}", o["reason"]))
            v = fixed
            flags.append("overridden")
        elif v == dv:
            pass  # two readings and the draft agree
        elif v is None and dv in r["votes"]:
            v = dv  # no number was read twice, but the same line of the draft is one of the readings
            corrections.append((doc, f"pdf{r['pdf_page']}", r["pdf_page"], "auto_resolved", f"{acct} {item} {raw}", f"{acct} {item} {R.clean(v)}",
                                f"no number was read twice; the same line of {NEIGHBOR[doc]} ({R.clean(v)}) is one of the readings"))
            flags.append("auto_resolved")
        else:  # a number read twice can still be a leading 1 read as 4 both times, so a reading that differs from the draft is decided from the page image
            problems.append(f"{acct} {item} (pdf{r['pdf_page']}): readings {raw} do not settle on the draft's {R.clean(dv)}; read the page image")
            continue
        out.append(dict(fiscal_year=fy, doc=doc, page_label=f"pdf{r['pdf_page']}", pdf_page=r["pdf_page"], fund_code=acct[:2], fund="", dept_code=acct[3:7],
                        account=acct, line_item=item, flow=flow, column="budgeted", amount=R.clean(v), flag=";".join(flags), section="", tag=""))
    # printed totals, in page order, each belonging to the fund of the line before it
    totals, last_fund, by_key = [], None, {}
    pos = {id(r): n for n, r in enumerate(rows)}
    line_pos = [(pos[id(r)], acct) for r, acct, _, _, _ in lines]
    for r in total_rows:
        prev = [a for p, a in line_pos if p < pos[id(r)]]
        last_fund = prev[-1][:2] if prev else None
        key = total_key(r, last_fund)
        if key is None:
            continue
        v = vote(r["votes"])
        o = ov_total.get(key)
        if o:
            used.add(("total", key))
            fixed = Decimal(o["fixed"])
            if v != fixed:
                shown = R.clean(v) if v is not None else (", ".join(f"{R.clean(k)}x{n}" for k, n in r["votes"].most_common(4)) or "no reading")
                corrections.append((doc, f"pdf{r['pdf_page']}", r["pdf_page"], "total", f"{key} printed total {shown}", f"{key} printed total {R.clean(fixed)}", o["reason"]))
            v = fixed
        if v is None:
            problems.append(f"printed total {key} on pdf{r['pdf_page']}: no number given by two readings ({dict(r['votes'])})")
            continue
        by_key[key] = (r["pdf_page"], v)
    for key in ov_total:
        if ("total", key) not in used:
            problems.append(f"override not used: total {key}")
    for acct in ov_amount:
        if ("amount", acct) not in used:
            problems.append(f"override not used: {acct}")
    funds = sorted({r["fund_code"] for r in out})
    names = fund_names(doc)
    for f in funds:
        for flow in ("revenue", "expense"):
            printed = by_key.get(f"{f}|{flow}")
            got = sum(Decimal(str(r["amount"])) for r in out if r["fund_code"] == f and r["flow"] == flow)
            if printed is None:
                problems.append(f"fund {f} {flow}: no printed total")
            elif got != printed[1]:
                problems.append(f"fund {f} {flow} total printed {printed[1]} != lines {got}")
            if printed:
                totals.append((f"pdf{printed[0]}", printed[0], f, flow, "fund_subtotal", names.get(f, ""), printed[1]))
        rv, ex, net = by_key.get(f"{f}|revenue"), by_key.get(f"{f}|expense"), by_key.get(f"{f}|net")
        if rv and ex and net and rv[1] - ex[1] != net[1]:
            problems.append(f"fund {f} net printed {net[1]} != revenue - expenditure {rv[1] - ex[1]}")
        elif not net:
            problems.append(f"fund {f}: no printed net")
    for flow in ("revenue", "expense"):
        g = by_key.get(f"report|{flow}")
        if not g:
            problems.append(f"report total {flow} missing")
            continue
        want = sum(by_key[f"{f}|{flow}"][1] for f in funds if f"{f}|{flow}" in by_key)
        if g[1] != want:
            problems.append(f"report total {flow} printed {g[1]} != fund totals {want}")
        totals.append((f"pdf{g[0]}", g[0], "", flow, "grand", "GRAND TOTAL", g[1]))
    rn = by_key.get("report|net")
    if rn and "report|revenue" in by_key and "report|expense" in by_key and by_key["report|revenue"][1] - by_key["report|expense"][1] != rn[1]:
        problems.append(f"report net printed {rn[1]} != revenue - expenditure")
    if rn:
        totals.append((f"pdf{rn[0]}", rn[0], "", "", "report_net", "Report Totals Net", rn[1]))
    return out, totals, problems


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_budget"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    corrections = []
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        data, totals, problems = parse(ent.source_path(src[doc]["pdf_path"]), fy, doc, A.load_overrides(ent, doc), corrections, tmp)
    rev = sum(1 for r in data if r["flow"] == "revenue")
    print(f"{doc}: {rev} revenue lines, {len(data) - rev} expense lines, {len(totals)} totals, {len(problems)} problems")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    import extract_budget_version as V
    V.write(ent, doc, fy, data, totals, corrections)


if __name__ == "__main__":
    main(sys.argv)
