"""Extract a Belvidere Park District "Budget Version Report" (layout B: FY2022, FY2023, FY2023 draft, FY2024 draft).

Usage: python3 pipelines/belvidere_park_district/extract_budget_version.py FY2023        (or a document id: FY2024_budget_draft)

The document is a stack of two budget-software reports, REVENUE BUDGET VERSION REPORT and EXPENDITURE BUDGET VERSION REPORT (FY2022 has only the
expenditure report), each listing `FF-DDDD-OOO  label  amount` per fund under a heading `NN Fund Name`, closing every fund with `NN Fund Name
Subtotal: $X` (FY2022: `NN Fund Name X`) and the report with `GRAND TOTAL: $X` (FY2022: `TOTALS: X`). Adopted documents have one column (Approved), drafts
have Requested and Amended (only Requested is filled). Scanned; read through the shared OCR cache.
Output (data/belvidere_park_district/extracted/<doc_id>_{revenue,expense,totals,corrections}.csv): the long format of layout A; the amount column is
named after the header (`approved` or `requested`; a second filled column would be `amended`). Totals have kinds `fund_subtotal` and `grand`.
Validation (all must tie, or be corrected by an override with a reason): every fund's lines = its Subtotal; the fund Subtotals of a report = its
GRAND TOTAL.
"""
import csv
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_budget_operating as A  # noqa: E402  digit_fix, AMOUNT, amounts, load_overrides

ENTITY = R.ENTITY
AMOUNT = A.AMOUNT
ACCT = re.compile(r"^\W*([0-9OQSsl]{2,3})-(\d{4})-{1,2}(\d{3})[:;]?\s+(.*)$")
SUBTOTAL = re.compile(rf"^\W*(?:\d\s+)?(\d{{2}})\s*(.*?)\s*Subtotal\W*({AMOUNT})")
FUND_TOTAL_22 = re.compile(rf"^\W*(\d{{2}})\s+([A-Za-z][A-Za-z &]*?)\s+[\W_]*({AMOUNT})\s*$")
GRAND = re.compile(rf"^\W*(?:GRAND TOTAL|TOTALS?)\W*({AMOUNT})")
KNOWN_FUNDS = {"01", "02", "03", "09", "10", "12", "21", "25"}  # fund codes of FY2019-FY2024 (entities/belvidere_park_district/funds.csv)
FUND_HEAD = re.compile(r"^\W*(\d{2})\s*([A-Za-z][A-Za-z &]*?)\W*$")


def norm(s):
    s = re.sub(r"^(\W*\d{2})-\s*(\d)\s+(\d{3})-(\d{3})", r"\1-\2\3-\4", s)  # "09-0 100-686"
    s = re.sub(r"^(\W*\d{2})-\s+(\d{4})-(\d{3})", r"\1-\2-\3", s)  # "02- 1000-410"
    s = re.sub(r"^(\W*\d{2})-(\d{2}) (\d{2})-(\d{3})", r"\1-\2\3-\4", s)  # "09-01 00-349"
    s = re.sub(r"(\d) ,(\d)", r"\1,\2", s)  # "$6,867 ,889.22"
    return re.sub(r"(\d) \.(\d{2})\b", r"\1.\2", s)


def read_pages(pages, fy, doc, overrides=(), corrections=None, used=None):
    """Rows, printed fund subtotals and printed grand totals of one OCR reading (no validation)."""
    rows, subs, grands = [], [], []
    used = used if used is not None else set()
    flow, column = "revenue", "approved"
    fund = last_fund = None
    for pdf_page, page in enumerate(pages, start=1):
        head = "\n".join(page.splitlines()[:4])
        if re.search(r"(?i)ordinance no", page[:400]) and not re.search(r"(?i)BUDGET VERSION", head):
            break  # the appropriation ordinance appended to the draft (read in Phase 4)
        if re.search(r"(?i)EXPENDITURE", head):
            flow = "expense"
        elif re.search(r"(?i)REVENUE", head):
            flow = "revenue"
        if re.search(r"(?i)Approved", head):
            column = "approved"
        elif re.search(r"(?i)Requested", head):
            column = "requested"
        label = f"pdf{pdf_page}"
        for raw in page.splitlines():
            line = raw
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and line.strip().startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, o["kind"], line.strip(), o["fixed"], o["reason"]))
                    line = o["fixed"] if o["kind"] == "line" else o["fixed"] + line.strip()[len(o["raw"]):]
                    used.add(i)
            s = norm(line.strip())
            if not s:
                continue
            a = ACCT.match(s)
            if a:
                vals = A.amounts(a.group(4))
                first = re.search(AMOUNT, a.group(4))
                text = (a.group(4)[:first.start()] if first else a.group(4)).strip()
                if not vals:
                    continue
                flags = []
                if a.group(1) in KNOWN_FUNDS:  # a clean prefix wins: draft watermarks garble the fund headings, so the heading is only a fallback
                    fcode = a.group(1)
                else:
                    fcode = last_fund or fund or a.group(1)  # lines come in fund order: the previous line's fund, else the fund heading
                    flags.append("account_prefix_repaired")
                last_fund = fcode
                if len(vals) > 1:
                    flags.append("two_amount_columns")
                rows.append(dict(fiscal_year=fy, doc=doc, page_label=label, pdf_page=pdf_page, fund_code=fcode, fund="", dept_code=a.group(2),
                                 account=f"{fcode}-{a.group(2)}-{a.group(3)}", line_item=text, flow=flow, column=column, amount=R.clean(vals[0]),
                                 flag=";".join(flags), section="", tag=""))
                continue
            st = SUBTOTAL.match(s)
            m = st or FUND_TOTAL_22.match(s)
            if m:
                last_fund = None
                subs.append((label, pdf_page, m.group(1), flow, m.group(2).strip(), A.amounts(m.group(3))[0]))
                continue
            g = GRAND.match(s)
            if g:
                grands.append((label, pdf_page, "", flow, "GRAND TOTAL", A.amounts(g.group(1))[0]))
                continue
            h = FUND_HEAD.match(s)
            if h and not re.search(r"(?i)report|budget|subtotal", s):
                fund, last_fund = h.group(1), None
    return rows, subs, grands


def fund_sum(rows, code, flow):
    return sum(Decimal(str(r["amount"])) for r in rows if r["fund_code"] == code and r["flow"] == flow)


def crossread(rows, alt_rows, code, flow, printed):
    """Adopt another OCR reading's amounts for one or two lines (or add a line only that reading has) when that alone makes the fund's lines equal the
    printed subtotal; unique solutions only. Returns the list of (index or None, new row/amount) changes."""
    mine = {r["account"]: i for i, r in enumerate(rows) if r["fund_code"] == code and r["flow"] == flow}
    cands = []
    for r in alt_rows:
        if r["fund_code"] != code or r["flow"] != flow:
            continue
        i = mine.get(r["account"])
        if i is None:
            cands.append((None, r))
        elif Decimal(str(rows[i]["amount"])) != Decimal(str(r["amount"])):
            cands.append((i, r))
    base = fund_sum(rows, code, flow)

    def delta(c):
        i, r = c
        return Decimal(str(r["amount"])) - (Decimal(str(rows[i]["amount"])) if i is not None else 0)

    sols = [(c,) for c in cands if base + delta(c) == printed]
    if not sols:
        sols = [(c, d) for n, c in enumerate(cands) for d in cands[n + 1:] if base + delta(c) + delta(d) == printed]
    return sols[0] if len(sols) == 1 else None


NEIGHBORS = {"FY2023_budget_draft": "FY2023_budget", "FY2024_budget_draft": "FY2024_budget"}


def neighbor_rows(doc):
    """Lines of the draft's adopted document (or the reverse) when its extract exists: the budget printed twice, read twice, in the same layout."""
    other = NEIGHBORS.get(doc)
    path = os.path.join(R.ROOT, "data", ENTITY, "extracted")
    if not other or not os.path.exists(os.path.join(path, f"{other}_expense.csv")):
        return None, other
    rows = []
    for flow in ("revenue", "expense"):
        for r in csv.DictReader(open(os.path.join(path, f"{other}_{flow}.csv"))):
            r["flow"], r["amount"], r["pdf_page"] = flow, r["amount"], int(r["pdf_page"])
            rows.append(r)
    return rows, other


def parse(pdf, fy, doc, overrides=(), corrections=None):
    problems, used = [], set()
    rows, subs, grands = read_pages(R.pages_of(pdf, doc), fy, doc, overrides, corrections, used)
    alt = None
    neighbor, neighbor_doc = neighbor_rows(doc)
    for code, flow in sorted({(c, f) for _, _, c, f, _, _ in subs}):
        printed = next(v for _, _, c, f, _, v in subs if c == code and f == flow)
        got = fund_sum(rows, code, flow)
        if got == printed:
            continue
        idx = [i for i, r in enumerate(rows) if r["fund_code"] == code and r["flow"] == flow]
        fix = A.digit_fix(rows, idx, printed)
        if fix:
            i, old, new = fix
            rows[i]["amount"] = R.clean(new)
            rows[i]["flag"] = ";".join(filter(None, [rows[i]["flag"], "auto_resolved_digit"]))
            if corrections is not None:
                corrections.append((doc, rows[i]["page_label"], rows[i]["pdf_page"], "auto_resolved_digit", f"{rows[i]['account']} {rows[i]['line_item']} {R.clean(old)}",
                                    f"{R.clean(new)}", f"a single-digit OCR confusion that, alone, makes fund {code} {flow} lines equal the printed subtotal {R.clean(printed)}"))
            continue
        if alt is None:
            alt = read_pages(R.pages_of(pdf, doc, prep="thr150"), fy, doc, overrides, None, set())[0]
        sol = crossread(rows, alt, code, flow, printed)
        source = "the thr150 OCR reading"
        if not sol and neighbor:
            sol = crossread(rows, neighbor, code, flow, printed)
            source = "the same line of the neighbouring document " + neighbor_doc
        if sol:
            for i, r in sol:
                if i is None:
                    r = dict(r, flag=";".join(filter(None, [r["flag"], "auto_resolved"])))
                    rows.append(r)
                    raw = ""
                else:
                    raw = f"{rows[i]['account']} {rows[i]['line_item']} {rows[i]['amount']}"
                    rows[i]["amount"] = r["amount"]
                    rows[i]["flag"] = ";".join(filter(None, [rows[i]["flag"], "auto_resolved"]))
                if corrections is not None:
                    corrections.append((doc, r["page_label"], r["pdf_page"], "auto_resolved", raw, f"{r['account']} {r['line_item']} {r['amount']}",
                                        f"{source} of this line, adopted because alone it makes fund {code} {flow} lines equal the printed subtotal {R.clean(printed)}"))
            continue
        problems.append(f"fund {code} {flow} subtotal printed {printed} != lines {got}")
    totals = []
    for label, pg, code, flow, name, v in subs:
        totals.append((label, pg, code, flow, "fund_subtotal", name, v))
    for flow in ("revenue", "expense"):
        for label, pg, _, f, name, v in (g for g in grands if g[3] == flow):
            want = sum(v2 for _, _, _, f2, _, v2 in subs if f2 == flow)
            if v != want:
                problems.append(f"grand total {flow} printed {v} != fund subtotals {want}")
            totals.append((label, pg, "", flow, "grand", name, v))
    for i, o in enumerate(overrides):
        if i not in used:
            problems.append(f"override not used: {o['raw']}")
    return rows, totals, problems


def write(ent, doc, fy, rows, totals, corrections):
    out = ent.data_path("extracted")
    os.makedirs(out, exist_ok=True)
    cols = ["fiscal_year", "doc", "page_label", "pdf_page", "fund_code", "fund", "dept_code", "account", "line_item", "flow", "column", "amount", "flag",
            "section", "tag"]
    for flow in ("revenue", "expense"):
        with open(os.path.join(out, f"{doc}_{flow}.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(r for r in rows if r["flow"] == flow)
    with open(os.path.join(out, f"{doc}_totals.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "page_label", "pdf_page", "fund_code", "flow", "kind", "name", "amount"])
        for label, pg, code, flow, kind, name, v in totals:
            w.writerow([fy, doc, label, pg, code, flow, kind, name, R.clean(v)])
    with open(os.path.join(out, f"{doc}_corrections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "page_label", "pdf_page", "method", "raw_text", "final_text", "detail"])
        w.writerows(corrections)


def main(argv):
    doc = argv[1] if "_" in argv[1] else f"{argv[1].upper()}_budget"
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    rows = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    corrections = []
    data, totals, problems = parse(ent.source_path(rows[doc]["pdf_path"]), fy, doc, A.load_overrides(ent, doc), corrections)
    rev = sum(1 for r in data if r["flow"] == "revenue")
    print(f"{doc}: {rev} revenue lines, {len(data) - rev} expense lines, {len(totals)} totals, {len(problems)} problems")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    write(ent, doc, fy, data, totals, corrections)


if __name__ == "__main__":
    main(sys.argv)
