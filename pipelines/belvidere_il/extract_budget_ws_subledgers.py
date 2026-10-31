"""Extract the Water / Sewer Fund cash sub-ledgers from a Belvidere budget ordinance PDF.

Usage: python3 pipelines/belvidere_il/extract_budget_ws_subledgers.py FY2019 [--prep auto|gray|thr150]

After the Water and Sewer department blocks (extract_budget_ws.py) the fund's pages print four cash sub-ledgers, each a
Beginning Cash & Investments, Sources, Uses, Ending Cash & Investments table, and a fund summary:
  Bond Reserves (necessary per bond ordinances)       beginning, interest income, transfer out: bond payment, ending (no Sources/Uses totals)
  Connection Fees / Deposits on Agreement Accounting  sources (interest, connection fees and deposits per department, set-aside/loan repayment),
                                                      uses (construction, building, equipment, land, loan to depreciation fund)
  Depreciation Funding                                sources (interest, loans and grants, the depreciation set-asides of the department blocks,
                                                      loan from connection fees), uses (construction, equipment, buildings, transfers out)
  Bond Payments Accounting                            sources (interest, bond proceeds, transfer in, the Bond Pmt Set Aside), uses (debt service)
  Fund summary                                        gross revenues and expenditures (excluding set-asides), net cash flow, change in accounts
                                                      receivable, ending cash and investments of the whole fund
Output (long format, fund "Water/Sewer"): data/belvidere_il/extracted/FY20XX_budget_ws_subledgers.csv with columns
  fiscal_year, doc, page_label, fund, ledger, kind, account (a unique key: ledger|kind|label#n), account_printed, line_item, column, amount, flag
`kind` is beginning | source | use | total_sources | total_uses | ending, and for the fund summary gross_revenues | gross_expenditures |
net_cash_flow | change_receivable | ending_fund.
Validation, per column (every printed number is a row of a zero-sum check, so any failing cell can be auto-resolved like the department
blocks: other OCR prep, neighbouring year's document, single-digit confusion; always only when the check then ties exactly):
  sources = TOTAL Sources, uses = TOTAL Uses, ending = beginning + sources - uses (per ledger); gross revenues - gross expenditures = net cash
  flow; fund ending cash = fund beginning cash + net cash flow + change in receivables; beginning cash of a later column = ending cash of the earlier one (the city's carry-forward, reported as a note when it differs).
Short lines (the printed cell is blank, e.g. Transfer Out in the actual column) are placed in the column that makes the checks tie.
Eyeballed corrections: data/belvidere_il/overrides/FY20XX_budget_ws_subledgers.csv, kinds `row` (page|key -> all column values) and
`allow_diff` (page, check name, column, difference), with a reason in `note`.
"""
import csv
import itertools
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget as B  # noqa: E402
import extract_budget_ws as W  # noqa: E402

DATA_E = B.DATA_E
FLOWS = ("subledgers",)
LEDGER_HEAD = [
    (re.compile(r"(?i)^bond\s+reserves"), "bond_reserves"),
    (re.compile(r"(?i)^connection\s+fees\s*/"), "connection_fees"),
    (re.compile(r"(?i)^depreciation\s+funding$"), "depreciation_funding"),
    (re.compile(r"(?i)^bond\s+payments"), "bond_payments"),
]
SUMMARY = [
    (re.compile(r"(?i)^beginning\s+cash.*fund"), "beginning_fund"),
    (re.compile(r"(?i)^gross\s+revenues"), "gross_revenues"),
    (re.compile(r"(?i)^gross\s+expenditures"), "gross_expenditures"),
    (re.compile(r"(?i)^net\s+cash\s+flow"), "net_cash_flow"),
    (re.compile(r"(?i)^change\s+in\s+accounts\s+receivable"), "change_receivable"),
    (re.compile(r"(?i)^ending\s+cash.*fund"), "ending_fund"),
]
LEDGERS = ["bond_reserves", "connection_fees", "depreciation_funding", "bond_payments"]
CHECK_COLS = ["fiscal_year", "doc", "page_label", "fund", "ledger", "kind", "account", "account_printed", "line_item", "column", "amount", "flag"]


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def clean(raw):
    line = re.sub(r"(\d) ,(\d{3})", r"\1,\2", raw.strip())
    line = re.sub(r"(?<=\s)[_~.:;|](?=\s)", " ", line)  # a lone mark from a ruled line between two values
    return line.lstrip("| ")


def parse(pages, ncols):
    """Lines after the Bond Reserves heading -> items (dicts with ledger, kind, label, vals, ...)."""
    items, ledger, section, started = [], None, None, False
    for label, text in pages:
        for raw in text.splitlines():
            line = clean(raw)
            if not line:
                continue
            vals, name = B.numbers(line, fill_to=ncols)
            guess = bool(B.numbers.guessed)
            if ncols and vals and len(vals) != ncols:
                for l2 in (line, re.sub(r"(?<![\d,.])(\d{1,3},\d{3}) (\d{3})(?![\d,.])", r"\1,\2", line)):  # "4,824 932": a lost comma
                    alt, alt_name = B.numbers(l2, merge=True, fill_to=ncols)
                    if len(alt) == ncols:
                        vals, name, guess = alt, alt_name, bool(B.numbers.guessed)
                        break
            name = re.sub(r"^[|\s]+", "", name).strip(" .:|")
            if not vals:
                for rx, led in LEDGER_HEAD:
                    if rx.match(name):
                        ledger, section, started = led, None, True
                        break
                else:
                    if started and re.match(r"(?i)^s\w{0,2}[uc]rces?$", name):  # OCR: "Scurces"
                        section = "source"
                    elif started and re.match(r"(?i)^uses$", name):
                        section = "use"
                continue
            if re.match(r"(?i)^(revised\b|line\s+item|actual\b|fy\b)", name):  # column headings
                continue
            if not started and not re.match(r"(?i)^beginning\s+cash.*fund", name):
                continue
            printed = ""
            m = W.ACCT.search(name)
            if m:
                printed = m.group(0)
                name = (name[:m.start()] + name[m.end():]).strip(" .:|")
            kind = led = None
            for rx, k in SUMMARY:
                if rx.match(name):
                    led, kind = "fund_summary", k
                    break
            if kind is None:
                if not ledger:
                    continue
                led = ledger
                if re.match(r"(?i)^.eginning\s+cash", name):  # OCR: "Seginning"
                    kind = "beginning"
                    section = None if ledger == "bond_reserves" else "source"
                elif re.match(r"(?i)^ending\s+cash", name):
                    kind = "ending"
                elif re.match(r"(?i)^total\s+sources", name):
                    kind, section = "total_sources", "use"
                elif re.match(r"(?i)^total\s+uses", name):
                    kind = "total_uses"
                elif ledger == "bond_reserves":
                    kind = "use" if re.match(r"(?i)^transfer", name) else "source"
                else:
                    kind = section or "unclassified"
            if not name or len(vals) > ncols:
                continue
            items.append(dict(page_label=label, ledger=led, kind=kind, label=name, account_printed=printed, vals=vals, raw=raw,
                              flag="ocr_zero_guess" if guess else ""))
    occ = Counter()
    for it in items:
        it["locked"] = it["kind"] in ("beginning_fund", "ending_fund")  # the fund-level carry-forward is the city's own arithmetic: never auto-adjusted
        base = f"{it['ledger']}|{it['kind']}|{slug(it['label'])}"
        occ[base] += 1
        it["account"] = f"{base}#{occ[base]}"
        it["_key"] = (it["account"], 1)
        it["line_item"], it["dept"] = it["label"], ""
        it["block"], it["flow"] = it["ledger"], it["kind"]
    return items


def zero_event(ledger, what, label, ncols):
    return dict(kind="id", name=f"{ledger}: {what}", vals=[0] * ncols, label=label, block=ledger)


def build_checks(items, events, ncols, locked=()):
    """Zero-sum identities. `events` is unused (kept for the shared autofix signature); locked = rows from the department blocks."""
    checks = []
    good = [r for r in items if len(r["vals"]) == ncols]

    def of(led, kind):
        return [r for r in good if r["ledger"] == led and r["kind"] == kind]

    def add(led, what, rows, anchor):
        checks.append(dict(e=zero_event(led, what, anchor["page_label"], ncols), what=what, rows=rows))

    for led in LEDGERS:
        beg, end, src, use = of(led, "beginning"), of(led, "ending"), of(led, "source"), of(led, "use")
        ts, tu = of(led, "total_sources"), of(led, "total_uses")
        if not (beg and end):
            continue
        if ts:
            add(led, "sources = TOTAL Sources", [(ts[0], 1)] + [(r, -1) for r in src], ts[0])
        if tu:
            add(led, "uses = TOTAL Uses", [(tu[0], 1)] + [(r, -1) for r in use], tu[0])
        inflow = [(ts[0], -1)] if ts else [(r, -1) for r in src]
        outflow = [(tu[0], 1)] if tu else [(r, 1) for r in use]
        add(led, "ending = beginning + sources - uses", [(end[0], 1), (beg[0], -1)] + inflow + outflow, end[0])
    fs = {r["kind"]: r for r in good if r["ledger"] == "fund_summary"}
    if {"gross_revenues", "gross_expenditures", "net_cash_flow"} <= set(fs):
        add("fund_summary", "net cash flow = gross revenues - gross expenditures",
            [(fs["net_cash_flow"], 1), (fs["gross_revenues"], -1), (fs["gross_expenditures"], 1)], fs["net_cash_flow"])
    if {"beginning_fund", "ending_fund"} <= set(fs):
        rows = [(fs["ending_fund"], 1), (fs["beginning_fund"], -1)]
        for k in ("net_cash_flow", "change_receivable"):
            if k in fs:
                rows.append((fs[k], -1))
        add("fund_summary", "fund ending cash = beginning + net cash flow + change in receivables", rows, fs["ending_fund"])
    return checks


def chain_notes(items, cols, ncols):
    """Beginning cash of a column that follows another should equal that column's ending cash (the carry-forward); notes for differences."""
    out = []
    good = [r for r in items if len(r["vals"]) == ncols]
    for led in LEDGERS:
        beg = next((r for r in good if r["ledger"] == led and r["kind"] == "beginning"), None)
        end = next((r for r in good if r["ledger"] == led and r["kind"] == "ending"), None)
        if not (beg and end):
            continue
        for i in range(1, ncols):
            # the revised column repeats the budget column's figures; the request column follows the last budget column
            prev = i - 1
            if cols[i].startswith("budget_revised"):
                continue
            if cols[prev].startswith("budget_revised") and i == ncols - 1:
                prev = i - 2
            d = beg["vals"][i] - end["vals"][prev]
            if d:
                out.append(f"{beg['page_label']} {led}: beginning cash {cols[i]} {beg['vals'][i]:,} differs by {d:+,} from ending cash {cols[prev]} {end['vals'][prev]:,}")
    return out


def load_overrides(fy):
    ov = dict(row={}, total={}, allow={}, account={})
    path = os.path.join(DATA_E, "overrides", f"FY{fy}_budget_ws_subledgers.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "row":
                ov["row"][(o["page_label"], o["key"])] = [int(v) for v in o["value"].split("|")]
            elif o["kind"] == "allow_diff":
                ov["allow"][(o["page_label"], o["key"], o["column"])] = int(o["value"])
    return ov, path


def locked_rows(fy, cols):
    """The Water and Sewer department blocks' ending cash (from extract_budget_ws.py) as rows the fund-ending check can use."""
    path = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_ws_totals.csv")
    rows = {}
    for r in csv.DictReader(open(path)):
        if r["kind"] == "ending_cash":
            rows.setdefault(r["name"], {})[r["column"]] = int(r["amount"])
    return [dict(page_label="", account=f"dept|{n}", vals=[v.get(c, 0) for c in cols], flag="", locked=True, ledger="dept", kind="ending",
                 line_item=n) for n, v in rows.items()]


def place_short(items, ncols, cols, ov, locked):
    """A line with fewer values than columns (a blank printed cell): all zero -> zeros; else the column placement that makes the checks tie."""
    notes = []
    for r in items:
        if 0 < len(r["vals"]) < ncols and not any(r["vals"]):
            r["vals"], r["flag"] = [0] * ncols, (r["flag"] + " padded_zeros").strip()
    short = [r for r in items if 0 < len(r["vals"]) < ncols]
    for r in short:
        base = list(r["vals"])
        best = []
        for pos in itertools.combinations(range(ncols), len(base)):
            v = [0] * ncols
            for p, x in zip(pos, base):
                v[p] = x
            r["vals"] = v
            n = len(W.evaluate(build_checks(items, [], ncols, locked), cols, ov, ncols, set())[0])
            best.append((n, pos, v))
        low = min(b[0] for b in best)
        cand = [b for b in best if b[0] == low]
        if len({tuple(b[2]) for b in cand}) == 1:
            r["vals"] = cand[0][2]
            r["flag"] = (r["flag"] + " placed_by_sum").strip()
            notes.append(f"{r['page_label']} {r['account']}: {len(base)} of {ncols} cells printed, placed in columns {[cols[p] for p in cand[0][1]]}")
        else:
            r["vals"] = base  # stays short: reported as a problem
    return notes


def pages_of(fy, prep):
    pages, span = W.pages_of(fy, prep)
    return pages, span


def run(fy, prep, alts=None):
    pages, span = pages_of(fy, prep)
    cols_path = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_ws_totals.csv")
    cols = list(dict.fromkeys(r["column"] for r in csv.DictReader(open(cols_path))))
    ncols = len(cols)
    items = parse(pages, ncols)
    ov, ov_path = load_overrides(fy)
    notes_by = B.read_notes(ov_path)
    locked = locked_rows(fy, cols)
    notes, used = [], set()
    for r in items:
        k = (r["page_label"], r["account"])
        if k in ov["row"]:
            raw_vals, new_vals = list(r["vals"]), list(ov["row"][k])
            detail = notes_by.get(("row", k[0], k[1], ""), "")
            if len(raw_vals) == len(new_vals):
                r.setdefault("corrections", []).extend(dict(column=cols[i], raw_value=a, final_value=b, method="row_override", detail=detail)
                                                       for i, (a, b) in enumerate(zip(raw_vals, new_vals)) if a != b)
            else:
                r.setdefault("corrections", []).append(dict(column="(all)", raw_value="|".join(map(str, raw_vals)), final_value="|".join(map(str, new_vals)),
                                                             method="row_override", detail=detail))
            r["vals"], r["flag"] = new_vals, (r["flag"] + " values_overridden").strip()
            used.add(("row",) + k)
    notes += place_short(items, ncols, cols, ov, locked)

    def checks_fn(rows, events, nc):
        return build_checks(rows, events, nc, locked)

    if alts is not None:
        W.autofix(items, [], cols, ov, ncols, alts, notes, checks_fn)
    problems = [f"{r['page_label']} {r['account']}: {len(r['vals'])} values, expected {ncols}: {r['raw']}" for r in items if len(r["vals"]) != ncols]
    problems += [f"{r['page_label']} line outside any Sources/Uses section: {r['raw']}" for r in items if r["kind"] == "unclassified"]
    need = {led: ["beginning", "ending"] + ([] if led == "bond_reserves" else ["total_sources", "total_uses"]) for led in LEDGERS}
    need["fund_summary"] = ["beginning_fund", "gross_revenues", "gross_expenditures", "net_cash_flow", "change_receivable", "ending_fund"]
    for led, kinds in need.items():
        for k in kinds:
            n = sum(1 for r in items if r["ledger"] == led and r["kind"] == k)
            if n != 1 and not (k == "change_receivable" and n == 0):  # FY2026-27 print no receivable line in the sub-ledgers' summary? checked below
                problems.append(f"{led}: expected one {k} line, found {n}")
    p2, n2, _ = W.evaluate(checks_fn(items, [], ncols), cols, ov, ncols, used)
    problems += p2
    notes += n2
    notes += chain_notes(items, cols, ncols)
    for k in set(ov["row"]):
        if ("row",) + k not in used:
            notes.append(f"override never used: {k}")
    for k in ov["allow"]:
        if ("allow",) + k not in used:
            notes.append(f"override never used: {k}")
    good = [r for r in items if len(r["vals"]) == ncols]
    return dict(prep=prep, span=span, cols=cols, rows=items, good=good, problems=problems, notes=notes, total_corr=[])


def write(fy, res):
    out = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_ws_subledgers.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CHECK_COLS)
        for r in res["good"]:
            for c, v in zip(res["cols"], r["vals"]):
                w.writerow([fy, f"FY{fy}_budget", r["page_label"], "Water/Sewer", r["ledger"], r["kind"], r["account"], r["account_printed"],
                            r["label"], c, v, r["flag"]])


def write_corrections(fy, res):
    out = os.path.join(DATA_E, "extracted", f"FY{fy}_budget_ws_subledgers_corrections.csv")
    rows = [dict(r, dept_code="") for r in res["good"]]
    return B.write_correction_csv(out, fy, "Water/Sewer", rows, res["total_corr"])


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    preps = B.PREPS if arg == "auto" else [arg]
    first = {p: run(fy, p) for p in preps}
    results = []
    for p in preps:
        r = run(fy, p, W.build_alts(fy, p, first, FLOWS))
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
