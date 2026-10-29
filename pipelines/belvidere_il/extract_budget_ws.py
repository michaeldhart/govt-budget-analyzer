"""Extract the Water / Sewer Fund (fund 61) revenue and expense lines from a Belvidere budget ordinance PDF.

Usage: python3 pipelines/belvidere_il/extract_budget_ws.py FY2019 [--prep auto|gray|thr150]

The fund's pages follow the General Fund pages in the same Exhibit A. Each department block (Water, Sewer, with Collection System
expenses inside Sewer) lists revenue lines (61-4-DDD-OOOO), expense lines (61-5-DDD-OOOO) and two unnumbered expense lines, the
depreciation and bond payment set-asides, then "Total X Department Revenues / Expenses" and "Net X Department", with the block's
beginning and ending cash. Output (same long format as the General Fund extracts, fund "Water/Sewer"):
  data/belvidere_il/extracted/FY20XX_budget_ws_revenue.csv, _ws_expense.csv, _ws_totals.csv
Validation, per department block and column: revenue lines = Total Revenues; expense lines + set-asides (+ the Collection System
lines inside Sewer) = Total Expenses; Net = Revenues - Expenses; Ending cash = Beginning cash + Net (+ the change in accounts
receivable line, when printed). The cash sub-ledgers after the department blocks (bond reserves, connection fees, depreciation
funding, bond payments) are not extracted. Eyeballed corrections: data/belvidere_il/overrides/FY20XX_budget_ws.csv, kinds
`row` (page|account -> all column values), `total` (page, printed line name, column, value), `allow_diff`, `account`.
"""
import csv
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget as B  # noqa: E402

DATA_E, SRC_E, ROOT = B.DATA_E, B.SRC_E, B.ROOT
ACCT = re.compile(r"(?<![\d-])(\d{2})-([45§])-(\d(?:\s?\d){1,3})-(\d{4})(?!\d)")
SETASIDE = re.compile(r"(?i)(depreci\w*\s+set\s*aside|b\w{2}d\s+p\w{1,3}\s+set\s*aside)")  # OCR reads "Bond Pmt" as "Bund Prnt" / "Bond Pmi"
BLOCK_HEAD = re.compile(r"(?i)^(water|sewer)\s+department$|^water\s*/\s*sewer\s+general\s+administration$|^collection\s+system\s+expenses$")
SUBLEDGER = re.compile(r"(?i)^(bond reserves|connection fees|depreciation funding|bond payments|sources$|uses$)")
WS_CONFUSIONS = {"6": "0", "0": "6", "9": "0"}  # more single-digit misreads seen in these pages (an unwanted 6 for 0, etc.)
KINDS = {"revtotal": "dept_revenues", "exptotal": "dept_expenses", "net": "dept_net", "beg": "beginning_cash", "end": "ending_cash"}


def pdf_path(fy):
    for p in (os.path.join(SRC_E, "budgets", f"FY{fy}_budget.pdf"), os.path.join(SRC_E, f"FY{fy}_budget_753H.pdf")):
        if os.path.exists(p):
            return p
    raise SystemExit(f"no PDF for FY{fy}")


def pages_of(fy, prep):
    pdf = pdf_path(fy)
    outdir = os.path.join(DATA_E, "ocr", "budget_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    n = B.page_count(pdf)
    texts = [B.ocr_page(pdf, p, outdir, prep) for p in range(1, n + 1)]
    start = next((i for i, t in enumerate(texts) if re.search(r"WATER\s*/\s*SEWER\s+FUND", t[:200], re.I) and re.search(r"61-4-", t)), None)
    if start is None:
        raise SystemExit(f"FY{fy}: Water / Sewer Fund pages not found")
    end = next((i for i in range(start, len(texts)) if re.search(r"NET CASH FLOW", texts[i], re.I)), None)
    if end is None:
        end = max(i for i in range(start, min(start + 8, len(texts))) if re.search(r"61-[45]-", texts[i]))
    out = []
    for n_, t in enumerate(texts[start:end + 1], start + 1):
        m = B.LABEL.search(t[-200:])
        out.append((f"{m.group(1)} of {m.group(2)}" if m else f"pdf{n_}", t))
    return out, (start + 1, end + 1)


def load_overrides(fy):
    ov = dict(row={}, total={}, allow={}, account={})
    path = os.path.join(DATA_E, "overrides", f"FY{fy}_budget_ws.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "row":
                ov["row"][(o["page_label"], o["key"])] = [int(v) for v in o["value"].split("|")]
            elif o["kind"] == "total":
                ov["total"][(o["page_label"], o["key"], o["column"])] = int(o["value"])
            elif o["kind"] == "allow_diff":
                ov["allow"][(o["page_label"], o["key"], o["column"])] = int(o["value"])
            elif o["kind"] == "account":
                ov["account"][(o["page_label"], o["key"])] = o["value"]
    return ov


def parse(pages, ncols):
    """Lines -> rows (revenue/expense, with the block they sit in) and events (totals, net, cash)."""
    rows, events, block, collection = [], [], None, False
    for label, text in pages:
        for raw in text.splitlines():
            line = re.sub(r"(\d) ,(\d{3})", r"\1,\2", raw.strip())
            line = re.sub(r"(?<=\s)[_~.:;](?=\s)", " ", line)  # a lone mark from a ruled line between two values
            if not line:
                continue
            m = ACCT.search(line)
            if m:
                dept = re.sub(r"\s", "", m.group(3))
                obj = m.group(4)
                tail = line[m.end():]
                vals, _ = B.numbers(tail, fill_to=ncols)
                guess = bool(B.numbers.guessed)
                if ncols and len(vals) != ncols:
                    for t2 in (tail, re.sub(r"(?<![\d,.])(\d{1,3},\d{3}) (\d{3})(?![\d,.])", r"\1,\2", tail)):  # "3,287 944" lost its comma
                        alt, _ = B.numbers(t2, merge=True, fill_to=ncols)
                        if len(alt) == ncols:
                            vals, guess = alt, bool(B.numbers.guessed)
                            break
                is_rev = m.group(2) == "4"
                if dept in ("810", "820", "830") and block is not None:  # the account's department says which block this is, even if OCR lost the heading
                    block = "WATER" if dept == "810" else "SEWER"
                flag = "ocr_zero_guess" if guess else ""
                if collection and not is_rev and dept == "820":  # the Collection System lines print under the sewer code in older years
                    dept, flag = "830", (flag + " dept_from_heading").strip()
                rows.append(dict(page_label=label, block=block, flow="revenue" if is_rev else "expense", dept=dept,
                                 account=f"61-{'4' if is_rev else '5'}-{dept}-{obj}", line_item=line[:m.start()].strip(" .:|"), vals=vals,
                                 raw=raw, flag=flag))
                continue
            vals, name = B.numbers(line)
            if ncols and vals and len(vals) != ncols:
                for l2 in (line, re.sub(r"(?<![\d,.])(\d{1,3},\d{3}) (\d{3})(?![\d,.])", r"\1,\2", line)):  # "2,999 435": a lost comma
                    alt, alt_name = B.numbers(l2, merge=True)
                    if len(alt) == ncols:
                        vals, name = alt, alt_name
                        break
            name = name.lstrip("| ").strip()
            if not vals:
                if BLOCK_HEAD.match(name):
                    key = name.lower()
                    if key.startswith("water department"):
                        block, collection = "WATER", False
                    elif key.startswith("sewer department"):
                        block, collection = "SEWER", False
                    elif key.startswith("collection"):
                        collection = True
                    else:
                        block, collection = "ADMIN", False
                elif SUBLEDGER.match(name):
                    block, collection = None, False
                continue
            if block is None:
                continue
            ev = None
            if re.match(r"(?i)^total\s+(water|sewer)\s+department\s+revenues?$", name):
                ev = "revtotal"
            elif re.match(r"(?i)^total\s+(water|sewer)\s+department\s+expenses?$", name):
                ev = "exptotal"
            elif re.match(r"(?i)^net\s+(water|sewer)\s+department$", name):
                ev = "net"
            elif re.match(r"(?i)^beginning\s+cash", name):
                ev = "beg"
            elif re.match(r"(?i)^ending\s+cash", name):
                ev = "end"
            elif re.match(r"(?i)^change in accounts receivable", name):
                ev, vals = "ar", vals + [0] * (ncols - len(vals)) if len(vals) < ncols else vals  # printed under the actual column only
            elif not name and len(vals) == ncols and block == "SEWER" and min(vals) > 100:
                ev = "subtotal"  # the unlabeled line after the Sewer expense lines (not the junk numbers OCR makes of a stray mark)
            elif SETASIDE.search(name) and len(vals) == ncols:
                code = "DEPSA" if re.search(r"(?i)depreci", name) else "BONDSA"
                dept = {"WATER": "810", "SEWER": "820"}.get(block, "")
                rows.append(dict(page_label=label, block=block, flow="expense", dept=dept, account=f"61-5-{dept}-{code}", line_item=name,
                                 vals=vals, raw=raw, flag="set_aside_no_account"))
                continue
            if ev:
                if ev in ("revtotal", "exptotal", "net") and re.search(r"(?i)water|sewer", name):  # these name their department
                    block = "WATER" if re.search(r"(?i)water", name) else "SEWER"
                events.append(dict(kind=ev, name=name, vals=vals, label=label, block=block, rev_pos=sum(1 for r in rows if r["flow"] == "revenue"),
                                   exp_pos=len(rows)))
    return rows, events


def build_checks(rows, events, ncols):
    """Every printed total as (event, rows summed with a sign, or None for a check between printed numbers)."""
    checks = []
    for b in ("WATER", "SEWER"):
        ev = {e["kind"]: e for e in events if e["block"] == b}
        good = [r for r in rows if r["block"] == b and len(r["vals"]) == ncols]
        revs = [(r, 1) for r in good if r["flow"] == "revenue"]
        exps = [(r, 1) for r in good if r["flow"] == "expense"]
        if "revtotal" in ev:
            checks.append(dict(e=ev["revtotal"], what="revenue lines", rows=revs))
        if "exptotal" in ev:
            checks.append(dict(e=ev["exptotal"], what="expense lines", rows=exps))
        if "subtotal" in ev:  # the unlabeled Sewer line before Collection System: the 820 expense lines
            checks.append(dict(e=ev["subtotal"], what="820 expense lines",
                               rows=[(r, 1) for r, _ in exps if r["dept"] == "820" and r["flag"] != "set_aside_no_account" and "dept_from_heading" not in r["flag"]]))
        if "net" in ev and "revtotal" in ev and "exptotal" in ev:
            checks.append(dict(e=ev["net"], what="revenues - expenses", rows=None, parts=(ev["revtotal"], ev["exptotal"]), kind="net"))
        if all(k in ev for k in ("beg", "end", "net")):
            checks.append(dict(e=ev["end"], what="beginning cash + net", rows=None, parts=(ev["beg"], ev["net"], ev.get("ar")), kind="cash"))
    return checks


def evaluate(checks, cols, ov, ncols, used):
    """(problems, notes, fails) where fails carry the rows of a failing sum check for the auto-resolver."""
    problems, notes, fails = [], [], []

    def printed(e):
        vals = list(e["vals"])
        for i, c in enumerate(cols):
            k = (e["label"], e["name"], c)
            if k in ov["total"]:
                vals[i] = ov["total"][k]
                used.add(("total",) + k)
        return vals

    for ck in checks:
        e, vals = ck["e"], printed(ck["e"])
        if ck["rows"] is not None:
            got = [sum(r["vals"][i] for r, _ in ck["rows"]) for i in range(ncols)]
            n = len(ck["rows"])
        elif ck.get("kind") == "net":
            a, b = printed(ck["parts"][0]), printed(ck["parts"][1])
            got, n = [a[i] - b[i] for i in range(ncols)], 2
        else:
            beg, net, ar = ck["parts"]
            nv = printed(net)
            got = [beg["vals"][i] + nv[i] + (ar["vals"][i] if ar and len(ar["vals"]) == ncols else 0) for i in range(ncols)]
            n = 4
        for i, c in enumerate(cols):
            d = vals[i] - got[i]
            if not d:
                continue
            if ck.get("kind") == "cash":  # the cash roll-forward is a consistency hint, not a tie: other fund transactions can sit between
                notes.append(f"{e['label']} {e['name']!r} {c}: cash roll-forward differs by {d:+,} from beginning cash + net")
            elif abs(d) <= 3 and ov["allow"].get((e["label"], e["name"], c)) is None and (c.startswith("budget") or abs(d) <= (n + 1) // 2):
                notes.append(f"{e['label']} {e['name']!r} {c}: off by {d:+} (the city's totals are often a dollar or two off their lines)")
            elif c.startswith("actual") and abs(d) <= (n + 1) // 2:
                notes.append(f"{e['label']} {e['name']!r} {c}: off by {d} (printed actuals are rounded from cents)")
            elif ov["allow"].get((e["label"], e["name"], c)) == d:
                used.add(("allow", e["label"], e["name"], c))
                notes.append(f"{e['label']} {e['name']!r} {c}: printed total is {d:+,} vs its lines (source arithmetic; allowed by override)")
            else:
                problems.append(f"{e['label']} {e['name']!r} {c}: printed {vals[i]:,} vs {ck['what']} {got[i]:,} (diff {d:,})")
                if ck["rows"] is not None:
                    fails.append((ck["rows"], i, d, (n + 1) // 2 if c.startswith("actual") else 0))
    return problems, notes, fails


def autofix(rows, events, cols, ov, ncols, alts, notes_out):
    """Same idea as the General Fund extractor: adopt another reading of a cell (other OCR prep, neighbouring year's document), or a
    single-digit OCR confusion, only when it makes a failing printed total tie exactly (one cell changes at a time; never twice)."""
    fixed = set()
    for _ in range(40):
        fails = evaluate(build_checks(rows, events, ncols), cols, ov, ncols, set())[2]
        changed = False
        for rws, i, d, tol in fails:
            if abs(d) <= 1:
                continue
            real, synth = [], []
            for r, sign in rws:
                if (id(r), i) in fixed:
                    continue
                for v, src in (alts.get(r.get("_key")) or {}).get(i, []):
                    if v != r["vals"][i]:
                        real.append((sign * (v - r["vals"][i]), r, v, src))
                if r["vals"][i] > 0 and abs(d) >= 10:
                    for v in B.digit_variants(r["vals"][i], WS_CONFUSIONS):
                        synth.append((sign * (v - r["vals"][i]), r, v, "a single-digit OCR confusion"))
            for tier in (real, synth):
                hit = [c for c in tier if abs(c[0] - d) <= tol]
                if len({(id(c[1]), c[2]) for c in hit}) == 1:
                    _, r, v, src = hit[0]
                    r.setdefault("corrections", []).append(dict(column=cols[i], raw_value=r["vals"][i], final_value=v,
                                                                 method="auto_resolved_digit" if tier is synth else "auto_resolved",
                                                                 detail=f"from {src}; makes the printed total tie"))
                    notes_out.append(f"auto-resolved {r['account']} {cols[i]}: {r['vals'][i]:,} -> {v:,} (from {src}; makes the printed total tie)")
                    r["vals"][i] = v
                    tag = "auto_resolved_digit" if tier is synth else "auto_resolved"
                    if tag not in r["flag"]:
                        r["flag"] = (r["flag"] + " " + tag).strip()
                    fixed.add((id(r), i))
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break


def build_alts(fy, prep, first):
    """{(account, nth): {column index: [(value, source)]}} from the other prep(s) and the neighbouring documents' prior-year columns."""
    me = first[prep]
    cols = me["cols"]
    alts = {}
    occ = {}
    for r in me["rows"]:
        n = occ[r["account"]] = occ.get(r["account"], 0) + 1
        alts[(r["account"], n)] = {}
    for p_, o in first.items():
        if p_ == prep:
            continue
        seen = {}
        for r in o["rows"]:
            n = seen[r["account"]] = seen.get(r["account"], 0) + 1
            if (r["account"], n) in alts and len(r["vals"]) == len(cols):
                for i, v in enumerate(r["vals"]):
                    alts[(r["account"], n)].setdefault(i, []).append((v, f"OCR prep {p_}"))
    for nb, mine in ((int(fy) - 1, f"budget_FY{int(fy) - 1}"), (int(fy) + 1, f"budget_FY{fy}")):  # the shared column: request in one document, prior-year in the next
        if mine not in cols:
            continue
        vals = {}
        for flow in ("revenue", "expense"):
            path = os.path.join(DATA_E, "extracted", f"FY{nb}_budget_ws_{flow}.csv")
            if os.path.exists(path):
                for r in csv.DictReader(open(path)):
                    if r["column"] == mine:
                        vals.setdefault(r["account"], []).append(int(r["amount"]))
        ci = cols.index(mine)
        occ = {}
        for r in me["rows"]:
            n = occ[r["account"]] = occ.get(r["account"], 0) + 1
            vs = vals.get(r["account"], [])
            if n <= len(vs):
                alts[(r["account"], n)].setdefault(ci, []).append((vs[n - 1], f"the FY{nb} document"))
    return alts


def run(fy, prep, alts=None):
    pages, span = pages_of(fy, prep)
    rows, _ = parse(pages, 0)
    counts = Counter(len(r["vals"]) for r in rows)
    ncols = max(set(counts), key=counts.get)
    cols = B.column_labels(fy, ncols)
    ov = load_overrides(fy)
    notes_by = B.read_notes(os.path.join(DATA_E, "overrides", f"FY{fy}_budget_ws.csv"))
    rows, events = parse(pages, ncols)
    used, notes = set(), []
    for r in rows:  # an account number the OCR garbled (61-4-3810-4900 for 61-4-810-4900), fixed by eye
        fix = ov["account"].get((r["page_label"], r["account"]))
        if fix:
            r.setdefault("corrections", []).append(dict(column="", raw_value=r["account"], final_value=fix, method="account_override",
                                                         detail=notes_by.get(("account", r["page_label"], r["account"], ""), "")))
            r["account"], r["dept"] = fix, fix.split("-")[2]
            r["flag"] = (r["flag"] + " account_overridden").strip()
    occ = {}
    for r in rows:
        n = occ[r["account"]] = occ.get(r["account"], 0) + 1
        r["_key"] = (r["account"], n)
    for r in rows:
        if 0 < len(r["vals"]) < ncols and not any(r["vals"]):  # an all-zero line the OCR cut short ("0 0" for "0 0 0")
            r["vals"], r["flag"] = [0] * ncols, (r["flag"] + " padded_zeros").strip()
        k = (r["page_label"], r["account"])
        if k in ov["row"]:
            raw_vals, new_vals, detail = list(r["vals"]), list(ov["row"][k]), notes_by.get(("row", k[0], k[1], ""), "")
            if len(raw_vals) == len(new_vals):
                r.setdefault("corrections", []).extend(dict(column=cols[i], raw_value=a, final_value=b, method="row_override", detail=detail)
                                                       for i, (a, b) in enumerate(zip(raw_vals, new_vals)) if a != b)
            else:
                r.setdefault("corrections", []).append(dict(column="(all)", raw_value="|".join(map(str, raw_vals)), final_value="|".join(map(str, new_vals)),
                                                             method="row_override", detail=detail))
            r["vals"], r["flag"] = ov["row"][k], (r["flag"] + " values_overridden").strip()
    if alts is not None:
        autofix(rows, events, cols, ov, ncols, alts, notes)
    problems = []
    for r in rows:
        if len(r["vals"]) != ncols:
            problems.append(f"{r['page_label']} {r['account']} {r['line_item']!r}: {len(r['vals'])} values, expected {ncols}: {r['raw']}")
    seen = {}
    for r in rows:
        if len(r["vals"]) == ncols:
            if r["account"] in seen:
                problems.append(f"duplicate account {r['account']}: {seen[r['account']]['line_item']!r} and {r['line_item']!r}")
            seen[r["account"]] = r
    checks = build_checks(rows, events, ncols)
    p2, n2, _ = evaluate(checks, cols, ov, ncols, used)
    problems += p2
    notes += n2
    for k in set(ov["total"]) | set(ov["allow"]):
        if ("total",) + k not in used and ("allow",) + k not in used:
            notes.append(f"override never used: {k}")
    totals = [(e["label"], KINDS[e["kind"]], f"{e['block'].title()}: {e['name']}",
               [ov["total"].get((e["label"], e["name"], c), v) for c, v in zip(cols, e["vals"])])
              for e in events if e["kind"] in KINDS and len(e["vals"]) == ncols]
    good = [r for r in rows if len(r["vals"]) == ncols]
    total_corr = [dict(page_label=e["label"], name=f"{e['block'].title()}: {e['name']}", column=c, raw_value=v, final_value=ov["total"][(e["label"], e["name"], c)],
                       method="total_override", detail=notes_by.get(("total", e["label"], e["name"], c), ""))
                  for e in events if e["kind"] in KINDS and len(e["vals"]) == ncols for c, v in zip(cols, e["vals"])
                  if (e["label"], e["name"], c) in ov["total"] and ov["total"][(e["label"], e["name"], c)] != v]
    return dict(prep=prep, span=span, cols=cols, rows=rows, good=good, problems=problems, notes=notes, totals=totals, total_corr=total_corr)


def write(fy, res):
    base = os.path.join(DATA_E, "extracted")
    hdr = ["fiscal_year", "doc", "page_label", "fund", "dept_code", "account", "line_item", "column", "amount", "flag"]
    for flow in ("revenue", "expense"):
        with open(os.path.join(base, f"FY{fy}_budget_ws_{flow}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(hdr)
            for r in res["good"]:
                if r["flow"] == flow:
                    for c, v in zip(res["cols"], r["vals"]):
                        w.writerow([fy, f"FY{fy}_budget", r["page_label"], "Water/Sewer", r["dept"], r["account"], r["line_item"], c, v, r["flag"]])
    with open(os.path.join(base, f"FY{fy}_budget_ws_totals.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "fund", "kind", "name", "column", "amount"])
        for label, kind, name, vals in res["totals"]:
            for c, v in zip(res["cols"], vals):
                w.writerow([fy, f"FY{fy}_budget", label, "Water/Sewer", kind, name, c, v])


def write_corrections(fy, res):
    return B.write_correction_csv(os.path.join(DATA_E, "extracted", f"FY{fy}_budget_ws_corrections.csv"), fy, "Water/Sewer", res["good"], res["total_corr"])


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    preps = B.PREPS if arg == "auto" else [arg]
    first = {p: run(fy, p) for p in preps}
    results = []
    for p in preps:
        r = run(fy, p, build_alts(fy, p, first))
        results.append(r)
        print(f"prep {p}: {len(r['problems'])} problems, {len(r['good'])}/{len(r['rows'])} lines usable (pdf pages {r['span'][0]}-{r['span'][1]})")
    res = min(results, key=lambda r: len(r["problems"]))
    write(fy, res)
    write_corrections(fy, res)
    print(f"\nchosen prep: {res['prep']}; columns {res['cols']}; {len(res['good'])} lines; {len(res['problems'])} problems")
    for n in res["notes"]:
        print("  note:", n)
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
