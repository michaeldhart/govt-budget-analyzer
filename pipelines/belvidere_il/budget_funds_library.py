"""Ida Public Library budget (FY2020-FY2027 ordinances): the library's own line-item budget, printed after the fund pages.

FY2015-FY2019 print the library as Library Operations Fund #18 on the fund pages (extract_budget_funds.py). From the FY2020 ordinance the
ordinance attaches the library's own budget instead: 5-digit library account codes (40100 Appropriation ...), dollars and cents, 1-3 budget
columns (the library names its years by the calendar year they start: `May 2020 - April 2021` = city FY2021). The pages are scans of
spreadsheet prints (ruled tables), so the ruled lines are erased before OCR and the words are placed by their x position (blank cells
exist in the 3-column layouts), see `words()` / `parse_page()`.
"""
import csv
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget as B  # noqa: E402
import extract_budget_funds as F  # noqa: E402

DATA_E = B.DATA_E
# ordinance PDF pages (1-based) holding the library budget
LIB_PAGES = {2020: [38, 39, 40], 2021: [39, 40], 2022: [63, 65, 67, 69], 2023: [19, 20, 21, 22], 2024: [17, 18, 19, 20],
             2025: [19, 20, 21, 22], 2026: [20, 21, 22, 23, 24], 2027: [21, 22, 23, 24]}


def tsv_words(fy, page):
    """OCR words with boxes for one page (300 dpi, ruled lines erased, tesseract psm 6): [(left, top, width, height, text, line_key)]."""
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"FY{fy}_nl_tsv")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"p{page:03d}.tsv")
    if not os.path.exists(path):
        pdf = F.pdf_path(fy)
        pgm = os.path.join(outdir, f"p{page:03d}.pgm")
        png = pgm.replace(".pgm", ".png")
        subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pgmraw", "-r300", f"-dFirstPage={page}", f"-dLastPage={page}", f"-sOutputFile={pgm}", pdf], check=True)
        from PIL import Image
        F.strip_lines(Image.open(pgm)).save(png)
        out = subprocess.run(["tesseract", png, "-", "--psm", "6", "-c", "preserve_interword_spaces=1", "tsv"], capture_output=True, text=True).stdout
        os.remove(pgm)
        os.remove(png)
        open(path, "w").write(out)
    words = []
    for r in csv.DictReader(open(path), delimiter="\t", quoting=csv.QUOTE_NONE):
        t = (r.get("text") or "").strip()
        if t and int(r["level"]) == 5:
            words.append((int(r["left"]), int(r["top"]), int(r["width"]), int(r["height"]), t, (r["block_num"], r["par_num"], r["line_num"])))
    return words



LIB_COLS = {2020: ["budget_FY2020", "budget_FY2019", "budget_FY2018"], 2021: ["budget_FY2021", "budget_FY2020"], 2022: ["budget_FY2022"],
            2023: ["budget_FY2023"], 2024: ["budget_FY2023", "budget_FY2024"], 2025: ["budget_FY2024", "budget_FY2025", "budget_revised_FY2025"],
            2026: ["budget_FY2026"], 2027: ["budget_FY2026", "budget_FY2027"]}
AMT = re.compile(r"^[\(\-\[]*[$S§5Ss]?\$?\(?[\d][\d,\.]*\.\d{2}[\)\]\|\.,;:!’‘'\"]*$")
CODE = re.compile(r"^[\[\"'‘’]?(\d{5})[\]\|\)\.:]*$")
NOISE = re.compile(r"^[\|\[\]\{\}\(\)_~\-—–=.,:;!'\"`<>\\/]+$")


NOCENTS = {2021}  # the FY2021 ordinance prints whole dollars without $ or cents
INT_AMT = re.compile(r"^[\(\-]?\d{1,3}(,\d{3})*[\)\]\|\.,]*$")


def is_amt(fy, t, right):
    if fy in NOCENTS:
        return bool(INT_AMT.match(t)) and right > 1400 and not re.match(r"^\d{5}$", t)
    return bool(AMT.match(t))


def to_cents(tok):
    """'$1,234.50)' -> 123450 (negative if parenthesised); None if not an amount."""
    t = tok.strip()
    neg = "(" in t or t.startswith("-")
    t = re.sub(r"^[^0-9]+", "", t) if not re.match(r"^[\(\-\[]*\$", t) else re.sub(r"^[^0-9]*\$?", "", t)
    t = re.sub(r"[^0-9,.]", "", t)
    t = re.sub(r"[^0-9]+$", "", t)
    m = re.match(r"^([\d,]*)\.(\d{2})$", t)
    if not m and re.match(r"^\d{1,3}(,\d{3})*$", t):
        return int(t.replace(",", "")) * 100
    if not m:
        return None
    whole = m.group(1).replace(",", "") or "0"
    v = int(whole) * 100 + int(m.group(2))
    return -v if neg else v


def page_lines(fy, page):
    lines = {}
    for w in tsv_words(fy, page):
        lines.setdefault(w[5], []).append(w)
    out = []
    for k, ws in lines.items():
        ws.sort()
        out.append((min(w[1] for w in ws), ws))
    out.sort(key=lambda x: x[0])
    return out


def column_centers(fy, ncols):
    """Right-edge x of the amount columns: 1-D clusters of every amount token's right edge in the document's pages."""
    xs = []
    for page in LIB_PAGES[fy]:
        for _, ws in page_lines(fy, page):
            for l, tp, wd, h, t, _ in ws:
                if is_amt(fy, t, l + wd):
                    xs.append(l + wd)
    xs.sort()
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > 150:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    centers = [sorted(c)[len(c) // 2] for c in clusters if len(c) >= 3]
    if len(centers) != ncols:
        raise SystemExit(f"FY{fy}: found {len(centers)} amount columns {centers}, expected {ncols}")
    return centers


def parse(fy):
    """-> (rows, events). rows: dict(page, flow, code, label, cells {col index: cents}); events: totals / subtotals with cells."""
    cols = LIB_COLS[fy]
    centers = column_centers(fy, len(cols))
    rows, events = [], []
    flow = "revenue"
    alltok = [t for pg in LIB_PAGES[fy] for _, ws in page_lines(fy, pg) for *_x, t, _k in ws if AMT.match(t)]
    dollar_doc = fy not in NOCENTS and bool(alltok) and sum("$" in t for t in alltok) / len(alltok) > 0.8
    for page in LIB_PAGES[fy]:
        for y, ws in page_lines(fy, page):
            toks = [(l, l + wd, t) for l, tp, wd, h, t, _ in ws]
            amts, rest, raws = [], [], {}
            for l, r, t in toks:
                t = re.sub(r"^[^\d$S§(\-]+(?=[$S§(\-]*\d)", "", t) if re.search(r"\d,?\d*\.\d{2}", t) else t  # a rule glued to the front: `—_$1,052,840.00`
                if dollar_doc and AMT.match(t) and "$" not in t and re.match(r"^[\(\-\[]*[5SS§s]\d", t):
                    t = "$" + re.sub(r"^([\(\-\[]*)[5SS§s]", r"\1", t)  # a printed `$` read as a 5 / S
                c = to_cents(t) if is_amt(fy, t, r) else None
                if c is not None:
                    ci = min(range(len(centers)), key=lambda i: abs(centers[i] - r))
                    if abs(centers[ci] - r) <= 120:
                        amts.append((ci, c, t))
                        raws[ci] = t
                        continue
                rest.append((l, r, t))
            rest = [(l, r, re.sub(r"^[_\W]+", "", t)) for l, r, t in rest if not NOISE.match(t)]
            rest = [x for x in rest if x[2]]
            code = None
            for j, (l, r, t) in enumerate(rest[:4]):  # the account code, possibly behind a rule fragment read as a letter
                t2 = t.translate(str.maketrans("SIOosl", "510051")) if re.match(r"^[0-9SIOosl]{5}(?=\D|$)|^[0-9SIOosl]{5}(?=[A-Za-z])", t) and len(re.findall(r"\d", t[:5])) >= 3 else t
                m5 = re.match(r"^(\d{5})(?=\D|$)", t2)
                if m5:
                    code = m5.group(1)
                    tail = t2[5:].lstrip("]|).:,\\/'")
                    rest = ([(l, r, tail)] if tail else []) + rest[j + 1:]
                    break
            if code is None and len(rest) > 1 and len(rest[0][2]) <= 2 and re.match(r"(?i)^total\b", rest[1][2]):
                rest = rest[1:]
            label = " ".join(t for _, _, t in rest).strip(" .:|")
            label = re.sub(r"\s+", " ", label)
            label = re.sub(r"(?i)^(total)(?=[a-z])", r"\1 ", label)
            if flow == "done":
                continue  # sub-budgets printed after the library's own total (rental property, Sullivan account)
            if not amts:
                if re.match(r"(?i)^expenses?\b.{0,4}$", label):
                    flow = "expense"
                continue
            cells = {}
            dup = False
            for ci, c, t in amts:
                if ci in cells:
                    dup = True
                cells[ci] = c
            if code is None and re.match(r"(?i)^(\w{1,2}\s+)?tax$", label):
                label = "TOTAL TAX"  # `TOTAL` read as a stray letter
            if code is None and re.search(r"(?i)collection$", label) and not re.match(r"(?i)^total", label):
                label = "Total " + label  # `Total Young Adult` lost its first words
            if code is None and re.match(r"(?i)^net income", label):
                continue  # the sheet's revenue - expense line (0.00); not a budget line
            low = label.lower()
            if re.match(r"(?i)^total\b", low) or (not label and not code):
                kind = "subtotal"
                if re.match(r"(?i)^total\s*(revenue|income)\b", low) or (not label and flow == "revenue"):
                    kind = "total_revenue"
                elif re.match(r"(?i)^total\s*expenses?\b", low) or (not label and flow == "expense"):
                    kind = "total_expense"
                events.append(dict(kind=kind, name=label or "(unlabelled total)", cells=cells, page=page, y=y, flow=flow, pos=len(rows)))
                if kind == "total_expense":
                    flow = "done"
                continue
            rows.append(dict(page=page, flow=flow, code=code, label=label, cells=cells, y=y, raw=" ".join(t for _, _, t in toks), dup=dup, raws=raws, flags={}))
    return rows, events



def groups(rows, events):
    """[(event, rows it must equal, preceding subtotals)]: a subtotal = the rows since the previous subtotal of its flow (when there are
    none, e.g. TOTAL COLLECTION, the sum of the subtotals just before it); grand totals = every row of the flow."""
    out, prev, subs = [], 0, []
    for e in events:
        fl = e["flow"]
        if e["kind"] == "subtotal":
            grp = [r for r in rows[prev:e["pos"]] if r["flow"] == fl]
            out.append((e, grp, list(subs)))
            if grp:
                prev = e["pos"]
            subs.append(e)
        else:
            out.append((e, [r for r in rows if r["flow"] == fl], []))
    return out


def sums_of(grp, ncols):
    return [sum(r["cells"].get(i, 0) for r in grp if not r.get("notin")) for i in range(ncols)]


def check_all(rows, events, ncols, cols, allow):
    problems, results = [], []
    for e, grp, subs in groups(rows, events):
        sums = sums_of(grp, ncols)
        alt = None
        if subs and not grp:  # a total of subtotals: the shortest suffix of preceding subtotals that reproduces it
            for k in range(2, len(subs) + 1):
                cand = [sum(x["cells"].get(i, 0) for x in subs[-k:]) for i in range(ncols)]
                if all(cand[i] == (e["cells"].get(i) or 0) for i in range(ncols)):
                    alt = cand
                    break
            if alt is None:
                alt = [sum(x["cells"].get(i, 0) for x in subs[-4:]) for i in range(ncols)]
        for i in range(ncols):
            got = e["cells"].get(i)
            if got is None:
                got = 0
            want = alt[i] if alt is not None else sums[i]
            d = got - want
            st = "ok" if d == 0 else ("allowed_override" if allow.get((e["name"], cols[i])) == d else "FAIL")
            if d and st == "FAIL":
                problems.append((e, i, d, grp))
            results.append(dict(check=e["kind"] + ":" + e["name"], column=cols[i], printed=got, computed=want, diff=d, status=st))
    return problems, results


def autofix(rows, events, ncols, cols, allow, notes):
    from extract_budget import digit_variants
    for _ in range(40):
        problems, _ = check_all(rows, events, ncols, cols, allow)
        done = False
        for e, i, d, grp in problems:
            cands = []
            for r in grp:
                v = r["cells"].get(i)
                if v is None or r["flags"].get(i):
                    continue
                raw = r["raws"].get(i, "")
                alts = set(digit_variants(abs(v), {"5": "9", "9": "5", "0": "6", "6": "0"}))
                m = re.match(r"^[\(\-\[]*\w\$?(\d[\d,]*\.\d{2})", raw) if "$" not in raw else None
                if m:  # the dollar sign read as a leading digit/letter
                    alts.add(to_cents(m.group(1)))
                for a in alts:
                    if a is not None and a - v == d:
                        cands.append((r, a))
            uniq = {(id(r), a) for r, a in cands}
            if len(uniq) == 1:
                r, a = cands[0]
                notes.append(f"auto-resolved {r['code'] or r['label']} {cols[i]}: {r['cells'][i] / 100:,.2f} -> {a / 100:,.2f} (makes {e['name']} tie)")
                r["cells"][i] = a
                r["flags"][i] = "auto_resolved_digit"
                done = True
                break
        if not done:
            break


def load_overrides(fy):
    """data/belvidere_il/overrides/FY20XX_budget_library.csv: columns kind, page_label, key, column, value, reason (reason required).
    cell (key = code or label, column = budget column, value = dollars) | allow_diff (key = printed total's name) | drop (key) | recode (key = code or label, value = corrected code)
    | not_in_total (key = code: a printed line the city leaves out of its own totals) | add_row (page_label = label, key = code, column = revenue|expense, value = dollars per budget column joined by |) | event_cell (key = total's name)."""
    path = os.path.join(DATA_E, "overrides", f"FY{fy}_budget_library.csv")
    ov = []
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            assert o.get("reason"), o
            ov.append(o)
    return ov


def apply_overrides(rows, events, cols, ov, allow, notes):
    for o in ov:
        k, c = o["key"], o["column"]
        if o["kind"] == "allow_diff":
            allow[(k, c)] = round(float(o["value"]) * 100)
        elif o["kind"] == "cell":
            ci = cols.index(c)
            hit = [r for r in rows if (r["code"] or r["label"]) == k and r.get("flow") == (o.get("page_label") or r.get("flow"))] or [r for r in rows if (r["code"] or r["label"]) == k]
            if not hit:
                notes.append(f"override never used: {k}")
                continue
            hit[0]["cells"][ci] = round(float(o["value"]) * 100)
            hit[0]["flags"][ci] = "cell_overridden"
        elif o["kind"] == "drop":
            for r in [r for r in rows if (r["code"] or r["label"]) == k]:
                rows.remove(r)
        elif o["kind"] == "not_in_total":
            for r in rows:
                if (r["code"] or r["label"]) == o["key"]:
                    r["notin"] = True
        elif o["kind"] == "recode":
            for r in rows:
                if (r["code"] or r["label"]) == o["key"]:
                    r["code"] = o["value"]
        elif o["kind"] == "add_row":
            vals = [round(float(v) * 100) for v in o["value"].split("|")]
            pos = len(rows)
            flow = o["column"]
            last = [i for i, r in enumerate(rows) if r["flow"] == flow]
            pos = last[-1] + 1 if last else len(rows)
            rows.insert(pos, dict(page=0, flow=flow, code=o["key"] or None, label=o["page_label"], cells={i: v for i, v in enumerate(vals)}, y=0, raw="(added by override)", dup=False, raws={}, flags={i: "added_by_override" for i in range(len(vals))}))
            for e in events:  # keep the event positions meaningful
                if e["pos"] >= pos:
                    e["pos"] += 1
        elif o["kind"] == "event_cell":
            ci = cols.index(c)
            for e in events:
                if e["name"] == k:
                    e["cells"][ci] = round(float(o["value"]) * 100)


def money(c):
    return c // 100 if c % 100 == 0 else round(c / 100, 2)


def slug(label):
    return (re.sub(r"[^A-Z]", "", label.upper())[:10] or "UNNAMED")


def run(fy, write=True):
    cols = LIB_COLS[fy]
    ncols = len(cols)
    rows, events = parse(fy)
    notes, allow = [], {}
    apply_overrides(rows, events, cols, load_overrides(fy), allow, notes)
    autofix(rows, events, ncols, cols, allow, notes)
    problems, results = check_all(rows, events, ncols, cols, allow)
    plist = [f"{e['name']} {cols[i]}: printed {e['cells'].get(i, 0) / 100:,.2f}, diff {d / 100:,.2f}" for e, i, d, grp in problems]
    if write:
        base = os.path.join(DATA_E, "extracted")
        hdr = ["fiscal_year", "doc", "page_label", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"]
        for flow in ("revenue", "expense"):
            with open(os.path.join(base, f"FY{fy}_budget_library_{flow}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(hdr)
                seen = {}
                for r in rows:
                    if r["flow"] != flow:
                        continue
                    obj = r["code"] or slug(r["label"])
                    acct = f"IPL-{'4' if flow == 'revenue' else '5'}-000-{obj}"
                    seen[acct] = seen.get(acct, 0) + 1
                    fl = []
                    if not r["code"]:
                        fl.append("no_account_number")
                    if seen[acct] > 1:
                        fl.append("duplicate_account")
                    if r.get("notin"):
                        fl.append("not_in_printed_total")
                    for i, c in enumerate(cols):
                        v = r["cells"].get(i)
                        ff = " ".join(fl + ([r["flags"][i]] if r["flags"].get(i) else []) + (["blank_cell"] if v is None else []))
                        if v is not None:
                            w.writerow([fy, f"FY{fy}_budget", f"pdf{r['page']}", "Ida Public Library", "000", acct, r["label"], c, money(v), ff])
        with open(os.path.join(base, f"FY{fy}_budget_library_totals.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fiscal_year", "doc", "page_label", "fund", "kind", "name", "column", "amount"])
            for e in events:
                for i, c in enumerate(cols):
                    if e["cells"].get(i) is not None:
                        w.writerow([fy, f"FY{fy}_budget", f"pdf{e['page']}", "Ida Public Library", {"total_revenue": "library_revenue_total", "total_expense": "library_expense_total"}.get(e["kind"], "library_subtotal"), e["name"], c, money(e["cells"][i])])
        with open(os.path.join(base, f"FY{fy}_budget_library_checks.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["fiscal_year", "doc", "fund", "check", "column", "printed", "computed", "diff", "status"])
            for r in results:
                w.writerow([fy, f"FY{fy}_budget", "Ida Public Library", r["check"], r["column"], money(r["printed"]), money(r["computed"]), money(r["diff"]), r["status"]])
    return dict(rows=rows, events=events, problems=plist, notes=notes, cols=cols)


if __name__ == "__main__":
    for a in [x for x in sys.argv[1:] if not x.startswith("--")]:
        fy = int(a.replace("FY", ""))
        res = run(fy, "--dry" not in sys.argv)
        print(f"FY{fy}: {len(res['rows'])} lines, {len(res['events'])} printed totals, {len(res['problems'])} problems")
        for n in res["notes"]:
            print("  note:", n)
        for p in res["problems"]:
            print("  !", p)
