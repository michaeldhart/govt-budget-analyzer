"""Extract a Belvidere Park District "Operating Budget" (layout A: FY2019, FY2020, FY2021): one amount per account, grouped under headings.

Usage: python3 pipelines/belvidere_park_district/extract_budget_operating.py FY2020

Layout (FY2020): a heading per block ("Corporate Income", "Recreation Programs", "OTHER FUNDS"), account lines `[tag]  Label  FF-DDDD-OOO  amount`
(tags sal/ben/com/contract/equip/build, or "Fund 10"), a `Total ...` line closing each block, `Grand Total ...` / `Total Other Funds ...` lines that add
blocks, and a SUMMARY page (Income and Expenses per fund and in total). A heading with "Income" starts a revenue block; after a `Total ... Income`
every block until the next income heading is expense.
Output (data/belvidere_park_district/extracted/<doc_id>_{revenue,expense,totals,corrections}.csv): the same long format as layout D plus `section` (the
heading) and `tag`; the single amount is column `budget`. Totals have kinds `block` (a Total line), `grand` (a total that adds blocks) and `summary`.
Validation (all must tie, or be recorded with an `allow_diff` override and a reason): every block total = its lines; every grand total = the block totals
of its flow since the previous grand total or heading `OTHER FUNDS`; the SUMMARY lines = the sums of the blocks of each fund group; summary Total =
sum of the groups.
"""
import csv
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402  shared: Entity, ocr_pages, pages_of, clean

ENTITY = R.ENTITY
AMOUNT = r"-?\(?\$?[\d,]*\d\.\d{2}\)?"
ACCT = re.compile(r"(?<![\d-])([0-9OQ]{2})-(\d{4})-(\d{3})?(?!\d)")  # FY2019 prints six CIP accounts as "21-0910-" with no object code
TAGS = {"sal", "ben", "com", "contract", "equip", "build", "fund", "fune", "other", "debt"}
TOTAL_LINE = re.compile(rf"^\W*((?:Grand )?Tota[l!1I](?=\s).*?|[A-Z][\w &/.-]*? Tota[l!1I](?=\s).*?)\s+({AMOUNT})\s*$")
SUMMARY_LINE = re.compile(rf"^\W*(Corporate|Recreation|Other Funds|Total)\s+({AMOUNT})\s*$")


def amounts(text):
    out = []
    for m in re.finditer(AMOUNT, text):
        s = m.group(0)
        neg = s.startswith("-") or s.startswith("(")
        v = Decimal(re.sub(r"[^\d.]", "", s))
        out.append(-v if neg else v)
    return out


CONFUSIONS = {"1": "47", "4": "1", "6": "85", "8": "603", "5": "68", "0": "86", "3": "85", "7": "1", "9": "0"}


def digit_fix(rows, idx, printed):
    """One cell of a failing block whose single-digit change (OCR confusions such as 1/4, 6/8, 5/6) makes the lines equal the printed total; only if
    exactly one such change exists and the difference is at least $10 (smaller differences are the city's own rounding)."""
    total = sum(Decimal(str(rows[i]["amount"])) for i in idx)
    diff = printed - total
    if abs(diff) < 10:
        return None
    hits = []
    for i in idx:
        old = Decimal(str(rows[i]["amount"]))
        txt = f"{old:.2f}"
        for pos, ch in enumerate(txt):
            for alt in CONFUSIONS.get(ch, ""):
                new = Decimal(txt[:pos] + alt + txt[pos + 1:])
                if new - old == diff and not (pos == 0 and alt == "0"):
                    hits.append((i, old, new))
    return hits[0] if len(hits) == 1 else None


def parse(pdf, fy, doc, overrides=(), corrections=None):
    rows, totals, problems = [], [], []
    used = set()
    section, flow = "", "revenue"
    block_sum, block_n, block_rows = Decimal(0), 0, []
    pending = {"revenue": [], "expense": []}  # printed block totals since the last grand total
    group_sums = {}  # fund group (Corporate/Recreation/Other Funds) -> {flow: sum of block totals}
    group = "Corporate"
    summary = {}
    in_summary, summary_flow = False, None
    fund_ctx = None
    allow = {o["raw"]: o for o in overrides if o["kind"] == "allow_diff"}
    all_pages = R.pages_of(pdf, doc)
    by_group = any(re.search(r"(?m)^\s*SUMMARY\s*$", pg) for pg in all_pages)  # FY2019-20: grand totals per fund group; FY2021: whole document
    for pdf_page, page in enumerate(all_pages, start=1):
        lines = page.splitlines()
        nums = [l.strip() for l in lines if re.fullmatch(r"\s*\d{1,2}\s*", l)]
        label = nums[-1] if nums else f"pdf{pdf_page}"
        expanded = []
        for raw in lines:  # insert_before: a line no OCR reading kept, read from the page image, is added in front of an anchor line
            for i, o in enumerate(overrides):
                if o["kind"] == "insert_before" and raw.strip().startswith(o["raw"]):
                    expanded.append(o["fixed"])
                    used.add(i)
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, "add_row", "", o["fixed"], o["reason"]))
            expanded.append(raw)
        for raw in expanded:
            line = raw.rstrip()
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and line.strip().startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, label, pdf_page, o["kind"], line.strip(), o["fixed"], o["reason"]))
                    line = o["fixed"] if o["kind"] == "line" else o["fixed"] + line.strip()[len(o["raw"]):]
                    used.add(i)
            s = re.sub(r"(\d) \.(\d{2})\b", r"\1.\2", line.strip())  # "1,185,531 .00"
            if not s or re.fullmatch(r"\d{1,2}", s) or re.match(r"(?i)^description\s+amount", s):
                continue
            if s.upper() == "SUMMARY":
                in_summary = True
                continue
            if in_summary:
                if s in ("Income", "Expenses"):
                    summary_flow = "revenue" if s == "Income" else "expense"
                    continue
                m = SUMMARY_LINE.match(s)
                if m:
                    summary[(summary_flow, m.group(1))] = amounts(m.group(2))[0]
                continue
            a = ACCT.search(s)
            if a:
                head, tail = s[:a.start()], s[a.end():]
                vals = amounts(tail)
                words = head.split()
                tag = ""
                if words and (words[0].lower() in TAGS):
                    tag = words[0]
                    words = words[1:]
                    if tag.lower() in ("fund", "fune") and words and re.fullmatch(r"\d{2}", words[0]):
                        words = words[1:]
                if not words:  # FY2021 prints the account first, then the label
                    first = re.search(AMOUNT, tail)
                    words = (tail[:first.start()] if first else tail).split()
                if not vals:
                    problems.append(f"{label}: no amount on {s[:60]}")
                    continue
                fund_code = a.group(1).replace("O", "0").replace("Q", "0")
                if fund_ctx and fund_code != fund_ctx:  # FY2021 prints "Fund: NN" headings; OCR garbles the account's prefix ("42-0100-330" in fund 12)
                    flags_extra, fund_code = ["account_prefix_repaired"], fund_ctx
                else:
                    flags_extra = []
                flags = (["two_amount_columns"] if len(vals) > 1 else []) + flags_extra
                obj = a.group(3)
                if not obj:  # no object code printed: key on the label so the account stays unique and stable across rebuilds
                    obj = "NONE-" + re.sub(r"[^a-z0-9]+", "-", " ".join(words).lower()).strip("-")
                    flags.append("no_object_code")
                rows.append(dict(fiscal_year=fy, doc=doc, page_label=label, pdf_page=pdf_page, fund_code=fund_code, fund="",
                                 dept_code=a.group(2), account=f"{fund_code}-{a.group(2)}-{obj}", line_item=" ".join(words),
                                 flow=flow, column="budget", amount=R.clean(vals[0]),
                                 flag=";".join(flags), section=section, tag=tag))
                block_sum += vals[0]
                block_n += 1
                block_rows.append(len(rows) - 1)
                continue
            t = TOTAL_LINE.match(s)
            if t and re.search(r"(?i)\bTota[l!1I](?=\s)", t.group(1)):
                name, v = t.group(1).strip(), amounts(t.group(2))[0]
                block_flow = flow
                if block_n and v != block_sum:
                    fixed = digit_fix(rows, block_rows, v)
                    if fixed:
                        i, old, new = fixed
                        block_sum += new - old
                        rows[i]["flag"] = ";".join(filter(None, [rows[i]["flag"], "auto_resolved_digit"]))
                        if corrections is not None:
                            corrections.append((doc, label, pdf_page, "auto_resolved_digit", f"{rows[i]['account']} {rows[i]['line_item']} {R.clean(old)}",
                                                f"{R.clean(new)}", f"a single-digit OCR confusion that, alone, makes the lines of '{name}' equal the printed total {R.clean(v)}"))
                if block_n:
                    kind, want = "block", block_sum
                    pending[flow].append(v)
                    group_sums.setdefault((group, flow), []).append(v)
                else:  # a grand total adds the blocks of its flow; its own name says which flow (the last block may be the other one)
                    block_flow = "revenue" if re.search(r"(?i)income|revenue", name) else "expense" if re.search(r"(?i)expense", name) else flow
                    kind, want = "grand", sum(pending[block_flow])
                    pending[block_flow] = []
                if v != want:
                    ok = allow.get(name)
                    if ok:
                        used.add(overrides.index(ok))
                    else:
                        problems.append(f"{label}: {name} printed {v} != {'lines' if kind == 'block' else 'blocks'} {want}")
                totals.append((label, pdf_page, group, block_flow, kind, name, v, "" if v == want else "allow_diff"))
                if kind == "block" and re.search(r"(?i)income", name) and flow == "revenue":
                    flow = "expense"
                block_sum, block_n, block_rows = Decimal(0), 0, []
                continue
            fh = re.match(r"^Fund:\s*(\d{2})\b", s)
            if fh:
                fund_ctx, section = fh.group(1), s
                continue
            if not re.search(r"\d[\d,]*\.\d{2}", s):
                # a heading (a line with no amount); lines left open by the previous block have no printed total (FY2019 Rivers Edge Birthdays): the
                # city's grand total still adds them, so they close as an unprinted block
                if block_n:
                    pending[flow].append(block_sum)
                    group_sums.setdefault((group, flow), []).append(block_sum)
                    totals.append((label, pdf_page, group, flow, "block_unprinted", f"(no printed total) {section}", block_sum, "unprinted"))
                    block_sum, block_n, block_rows = Decimal(0), 0, []
                if re.search(r"(?i)\b(income|revenue)\b", s):
                    flow = "revenue"
                elif s.lower() == "expenses":
                    flow = "expense"
                if s.upper() == "OTHER FUNDS":
                    group, flow = "Other Funds", "revenue"
                    pending = {"revenue": [], "expense": []} if by_group else pending
                elif group == "Corporate" and s.lower().startswith("recreation"):
                    group, flow = "Recreation", "revenue" if re.search(r"(?i)income", s) else flow
                    pending = {"revenue": [], "expense": []} if by_group else pending
                section = s
                continue
    for i, o in enumerate(overrides):
        if i not in used:
            problems.append(f"override not used: {o['raw']}")
    return rows, totals, summary, group_sums, problems


def validate(totals, summary, group_sums, problems):
    # summary lines against the block totals of each fund group; the Total against the groups
    for (flow, name), v in summary.items():
        if name == "Total":
            want = sum(summary.get((flow, g), 0) for g in ("Corporate", "Recreation", "Other Funds"))
        else:
            want = sum(group_sums.get((name, flow), []))
        if v != want:
            problems.append(f"summary {flow} {name} printed {v} != sum of its block totals {want}")
    return problems


def load_overrides(ent, doc):
    path = ent.data_path("overrides", f"{doc}.csv")
    return list(csv.DictReader(open(path, newline=""))) if os.path.exists(path) else []


def write(ent, doc, fy, rows, totals, summary, corrections):
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
        w.writerow(["fiscal_year", "doc", "page_label", "pdf_page", "fund_group", "flow", "kind", "name", "amount", "flag"])
        for label, pg, group, flow, kind, name, v, flag in totals:
            w.writerow([fy, doc, label, pg, group, flow, kind, name, R.clean(v), flag])
        for (flow, name), v in summary.items():
            w.writerow([fy, doc, "", "", name, flow, "summary", f"SUMMARY {name}", R.clean(v), ""])
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
    data, totals, summary, group_sums, problems = parse(ent.source_path(rows[doc]["pdf_path"]), fy, doc, load_overrides(ent, doc), corrections)
    validate(totals, summary, group_sums, problems)
    rev = sum(1 for r in data if r["flow"] == "revenue")
    print(f"{doc}: {rev} revenue lines, {len(data) - rev} expense lines, {len(totals)} totals, {len(summary)} summary lines, {len(problems)} problems")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    write(ent, doc, fy, data, totals, summary, corrections)


if __name__ == "__main__":
    main(sys.argv)
