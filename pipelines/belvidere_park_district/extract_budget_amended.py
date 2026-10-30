"""Extract the FY2023 amended budget reports of the Belvidere Park District (FY2023_budget_amended: text layer, marked DRAFT on the page; FY2023_budget_amended_oct:
scan, posted October 2023; both confirmed final by the owner).

Usage: python3 pipelines/belvidere_park_district/extract_budget_amended.py FY2023_budget_amended

Layout: a revenue report ("FY 2023 AMENDED REVENUE") and an expenditure report ("FISCAL YEAR 2023 AMENDED BUDGET EXPENDITURES") (the October scan has both,
the first file only revenue), each per fund: `Fund: NN Name  Ameded Budget  Original Budget`, lines `FF-DDDD-OOO label $ amended $ original` (`$ -` is zero),
`<Fund> Subtotal $ a $ o`, and at the end `Report Total Revenue / Expenditure`. The ordinance printed after the October scan (No. 23-10-24A, adopted
October 24, 2023: the amended appropriation) is not read here (Phase 4).
Output (data/belvidere_park_district/extracted/<doc_id>_{revenue,expense,totals,corrections}.csv): the long format of layout A, columns `amended` and
`original`, one row each per account. Validation per fund, flow and column: lines = Subtotal; Subtotals = Report Total. Repairs as in layout B
(single-digit confusion that alone ties a subtotal, flagged; overrides with a reason).
"""
import csv
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402
import extract_budget_operating as A  # noqa: E402
import extract_budget_version as V  # noqa: E402

ENTITY = R.ENTITY
ACCT = re.compile(r"^\W*([0-9OQSsl]{2,3})-{1,2}(\d{4})-{1,2}(\d{3})[:;]?\s+(.*)$")
FUND = re.compile(r"^\W*Fund:\s*(\d{2})\b")
SUBTOTAL = re.compile(r"^\W*(.*?)\s*Subtotal\b\W*(.*)$")
REPORT = re.compile(r"^\W*Report\s+Total\s+(Revenue|Expenditure)\W*(.*)$")
CELL = re.compile(r"(?<![\w.])(?:[$§S]\s*)?(\d[\d,]*\.\d{2}|-)(?![\w.])")
COLUMNS = ("amended", "original")


def cells(text):
    out = []
    for m in CELL.finditer(re.sub(r"(\d) ,(\d)", r"\1,\2", text)):
        out.append(Decimal(0) if m.group(1) == "-" else Decimal(m.group(1).replace(",", "")))
    return out


def parse(pdf, fy, doc, overrides=(), corrections=None):
    rows, subs, reports, problems = [], [], [], []
    used = set()
    flow, fund = "revenue", None
    for pdf_page, page in enumerate(R.pages_of(pdf, doc), start=1):
        if re.search(r"(?i)ordinance no|adopted october", page[:300]):
            break
        label = f"pdf{pdf_page}"
        for raw in page.splitlines():
            line = raw
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and re.sub(r"^\W+", "", line).startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, o["kind"], line.strip(), o["fixed"], o["reason"]))
                    line = o["fixed"] if o["kind"] == "line" else o["fixed"] + re.sub(r"^\W+", "", line)[len(o["raw"]):]
                    used.add(i)
            s = line.strip()
            if not s:
                continue
            if re.search(r"(?i)AMENDED BUDGET EXPENDITURES", s):  # a section heading, anywhere on the page
                flow = "expense"
                continue
            if re.search(r"(?i)AMENDED REVENUE", s):
                flow = "revenue"
                continue
            fh = FUND.match(s)
            if fh:
                fund = fh.group(1)
                continue
            rp = REPORT.match(s)
            if rp:
                v = cells(rp.group(2))
                reports.append((label, pdf_page, "revenue" if rp.group(1) == "Revenue" else "expense", v))
                if re.search(r"(?i)expenditure", rp.group(1)):
                    flow = "expense"
                continue
            st = SUBTOTAL.match(s)
            if st:
                subs.append((label, pdf_page, fund, flow, st.group(1).strip(), cells(st.group(2))))
                continue
            a = ACCT.match(s)
            if a and fund:
                v = cells(a.group(4))
                first = re.search(r"[$§]|\s-\s|\d[\d,]*\.\d{2}", a.group(4))
                text = (a.group(4)[:first.start()] if first else a.group(4)).strip()
                if len(v) < 2:
                    problems.append(f"{label}: {a.group(1)}-{a.group(2)}-{a.group(3)} has {len(v)} amount(s)")
                    continue
                fcode = a.group(1) if a.group(1) in V.KNOWN_FUNDS else fund
                flags = ["account_prefix_repaired"] if fcode != a.group(1) else []
                for c, val in zip(COLUMNS, v[:2]):
                    rows.append(dict(fiscal_year=fy, doc=doc, page_label=label, pdf_page=pdf_page, fund_code=fcode, fund="", dept_code=a.group(2),
                                     account=f"{fcode}-{a.group(2)}-{a.group(3)}", line_item=text, flow=flow, column=c, amount=R.clean(val),
                                     flag=";".join(flags), section="", tag=""))
    return rows, subs, reports, problems, used


def neighbor_rows(ent, doc):
    """The text-layer report (the same figures, read without OCR) as a second reading for the scanned one; None for the text-layer report itself."""
    other = "FY2023_budget_amended"
    if doc == other:
        return None
    rows = []
    for flow in ("revenue", "expense"):
        path = ent.data_path("extracted", f"{other}_{flow}.csv")
        if not os.path.exists(path):
            return None
        for r in csv.DictReader(open(path)):
            r["flow"], r["pdf_page"] = flow, int(r["pdf_page"])
            rows.append(r)
    return rows


def validate(rows, subs, reports, problems, doc, corrections, overrides=(), neighbor=None):
    allowed = {o["raw"]: o for o in overrides if o["kind"] == "allow_diff"}
    used_allow = set()
    effective = {}  # (fund, flow, column) -> value the report total adds (the lines, where the printed subtotal is stale)
    for label, pg, fund, flow, name, v in subs:
        if len(v) < 2:
            problems.append(f"{label}: subtotal {name} unreadable")
            continue
        for c, printed in zip(COLUMNS, v[:2]):
            effective[(fund, flow, c)] = printed
            idx = [i for i, r in enumerate(rows) if r["fund_code"] == fund and r["flow"] == flow and r["column"] == c]
            got = sum(Decimal(str(rows[i]["amount"])) for i in idx)
            if got != printed:
                fix = A.digit_fix(rows, idx, printed)
                if fix:
                    i, old, new = fix
                    rows[i]["amount"] = R.clean(new)
                    rows[i]["flag"] = ";".join(filter(None, [rows[i]["flag"], "auto_resolved_digit"]))
                    corrections.append((doc, rows[i]["page_label"], rows[i]["pdf_page"], "auto_resolved_digit",
                                        f"{rows[i]['account']} {rows[i]['line_item']} {R.clean(old)}", f"{R.clean(new)}",
                                        f"a single-digit OCR confusion that, alone, makes fund {fund} {flow} {c} lines equal the printed subtotal {R.clean(printed)}"))
                    continue
                if neighbor:
                    nb = [dict(r) for r in neighbor if r["column"] == c and r["fund_code"] == fund and r["flow"] == flow]
                    mine = [dict(rows[i]) for i in idx]
                    sol = V.crossread(mine, nb, fund, flow, printed)
                    if sol:
                        for ni, r in sol:
                            if ni is None:
                                rows.append(dict(r, column=c, flag=";".join(filter(None, [r["flag"], "auto_resolved"]))))
                                raw = ""
                            else:
                                gi = idx[ni]
                                raw = f"{rows[gi]['account']} {rows[gi]['line_item']} {rows[gi]['amount']}"
                                rows[gi]["amount"] = r["amount"]
                                rows[gi]["flag"] = ";".join(filter(None, [rows[gi]["flag"], "auto_resolved"]))
                            corrections.append((doc, r["page_label"], r["pdf_page"], "auto_resolved", raw, f"{r['account']} {r['line_item']} {r['amount']}",
                                                f"the same line of FY2023_budget_amended (text layer), adopted because alone it makes fund {fund} {flow} {c} lines equal the printed subtotal {R.clean(printed)}"))
                        continue
                key = f"fund {fund} {flow} {c} subtotal"
                if key in allowed:
                    used_allow.add(key)
                    effective[(fund, flow, c)] = got
                else:
                    problems.append(f"{label}: fund {fund} {flow} {c} subtotal printed {printed} != lines {got}")
    for label, pg, flow, v in reports:
        for c, printed in zip(COLUMNS, v[:2]):
            want = sum(v2 for (fu, fl, cc), v2 in effective.items() if fl == flow and cc == c)
            if printed != want:
                problems.append(f"{label}: report total {flow} {c} printed {printed} != fund subtotals {want}")
    for key in allowed:
        if key not in used_allow:
            problems.append(f"override not used: {key}")
    return problems


def write(ent, doc, fy, rows, subs, reports, corrections):
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
        w.writerow(["fiscal_year", "doc", "page_label", "pdf_page", "fund_code", "flow", "kind", "name", "column", "amount"])
        for label, pg, fund, flow, name, v in subs:
            for c, x in zip(COLUMNS, v[:2]):
                w.writerow([fy, doc, label, pg, fund, flow, "fund_subtotal", name, c, R.clean(x)])
        for label, pg, flow, v in reports:
            for c, x in zip(COLUMNS, v[:2]):
                w.writerow([fy, doc, label, pg, "", flow, "grand", f"Report Total {flow}", c, R.clean(x)])
    with open(os.path.join(out, f"{doc}_corrections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "page_label", "pdf_page", "method", "raw_text", "final_text", "detail"])
        w.writerows(corrections)


def main(argv):
    doc = argv[1]
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    corrections = []
    rows, subs, reports, problems, used = parse(ent.source_path(src[doc]["pdf_path"]), fy, doc, A.load_overrides(ent, doc), corrections)
    ov = A.load_overrides(ent, doc)
    validate(rows, subs, reports, problems, doc, corrections, ov, neighbor_rows(ent, doc))
    n_rev = sum(1 for r in rows if r["flow"] == "revenue") // 2
    print(f"{doc}: {n_rev} revenue accounts, {(len(rows) - 2 * n_rev) // 2} expense accounts, {len(subs)} subtotals, {len(reports)} report totals, "
          f"{len(problems)} problems")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    write(ent, doc, fy, rows, subs, reports, corrections)


if __name__ == "__main__":
    main(sys.argv)
