"""Extract a Belvidere Park District Annual Appropriation Bill and Ordinance (FY2019-FY2027; scans, the FY2027 draft has a text layer).

Usage: python3 pipelines/belvidere_park_district/extract_ordinance_appropriation.py FY2023_appropriation

Layouts: FY2019-FY2021 give each fund its beginning fund balance, ESTIMATED REVENUES, APPROPRIATIONS (Salaries, Benefits, Contractual, Commodities, Equipment,
Building) and the estimated year-end balance, with the total cash on hand at the top. FY2022-FY2027 give each fund REVENUE lines with `Total Revenue <Fund>`, then
APPROPRIATIONS with `<Fund> Appropriation Total`, and a SUMMARY OF FUNDS (a. cash on hand at the beginning, b. cash expected, c. expenditures contemplated,
d. cash at the end, e. taxes to be received). The ordinance lists IMRF, Social Security, Liability and Audit as funds of their own in FY2022-FY2025 (the budget
carries them inside the General Fund; entities/belvidere_park_district/ordinance_funds.csv).
Output (data/belvidere_park_district/extracted/ordinances/<doc>_lines.csv): fund, fund_code, general_fund_part, section (revenue | appropriation | balance),
line_item, amount, printed (1 for a printed total), pdf_page; <doc>_summary.csv (the SUMMARY OF FUNDS items a-e, or the opening cash on hand).
Validation (all must tie or the script fails): each fund's revenue lines = its Total Revenue, appropriation lines = its appropriation total; beginning balance +
revenue - appropriation = estimated ending balance (FY2019-FY2021); the fund totals add to the summary (b and c) and a + b - c = d (FY2022+).
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

ENTITY = R.ENTITY
TOKEN = re.compile(r"\$?\s*(\d[\d,.]*\d|\d)(?![\d,])")


def to_decimal(tok):
    """Dollars from one printed number, tolerant of OCR: `1,882.068.00` (a comma read as a period), `1,776,793.23.00` (a stray .00), `18101.00`."""
    t = tok.strip()
    m = re.fullmatch(r"(\d{1,3}(?:,\d{3})*)\.(\d{3})\.(\d{2})", t)
    if m:
        t = f"{m.group(1)},{m.group(2)}.{m.group(3)}"
    m = re.fullmatch(r"(\d{1,3}(?:,\d{3})+\.\d{2})\.\d{2}", t)
    if m:
        t = m.group(1)
    t = t.replace(",", "")
    return Decimal(t) if re.fullmatch(r"\d+(\.\d+)?", t) else None


def split_line(s):
    """(label, amount) of a line that ends in an amount, else (s, None). The amount is the last number-like token; its label is what comes before."""
    s = s.strip().rstrip(".:;'\" ")
    s = re.sub(r"\s-\s?0\s?-$", " 0.00", s)  # "-0-" is a printed zero
    ms = list(TOKEN.finditer(s))
    if not ms:
        return s, None
    m = ms[-1]
    if m.end() < len(s) - 2:  # text after the number: not an amount line
        return s, None
    v = to_decimal(m.group(1))
    if v is None or (v < 100 and "," not in m.group(1) and "." not in m.group(1) and len(ms) == 1 and not s[:m.start()].strip()):
        return s, None
    return s[:m.start()].strip().rstrip("$ "), v


def fund_config(ent, fy):
    out = []
    for r in ent.table("ordinance_funds.csv"):
        out.append((re.compile(r["heading_pattern"], re.I), r["name"], r["fund_code_from_fy2025"] if fy >= 2025 else r["fund_code_to_fy2024"], r["general_fund_part"]))
    return out


def parse(pdf, fy, doc, ent, prep="gray", overrides=(), corrections=None, used=None):
    funds = fund_config(ent, fy)
    used = used if used is not None else set()
    rows, summary, problems = [], [], []
    fund, section, pending = None, None, []
    in_summary, summary_key = False, None
    start = False
    for pdf_page, page in enumerate(R.pages_of(pdf, doc, prep=prep), start=1):
        for raw in page.splitlines():
            s = raw.strip()
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and s.startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, pdf_page, o["kind"], s, o["fixed"], o["reason"]))
                    s = o["fixed"] if o["kind"] == "line" else o["fixed"] + s[len(o["raw"]):]
                    used.add(i)
            if not s or re.fullmatch(r"\d{1,2}", s) or re.match(r"(?i)^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.? \d{1,2},? ?20\d\d$", s):
                continue  # page numbers and the county clerk's date stamp ("MAR 26 2021")
            if re.search(r"(?i)^cash on hand january 1", s):
                label, v = split_line(s)
                summary.append(("cash_on_hand_beginning", v, pdf_page))
                start = True
                continue
            if re.match(r"(?i)^summary of funds|^summary$", s):
                in_summary, fund, section, start = True, None, None, True
                continue
            if in_summary:
                m = re.match(r"(?i)^\W*([a-e])\.\s*(.*)$", s)
                if m:
                    summary_key = m.group(1).lower()
                    s = m.group(2)
                elif not summary_key:
                    continue
                label, v = split_line(s)
                if v is not None and summary_key:
                    if re.search(r"(?i)\bunexpected|\bSECTION", s):
                        summary_key = None
                        continue
                    summary.append((summary_key, v, pdf_page))
                    summary_key = None
                continue
            u = re.sub(r"[^A-Z0-9&.,' ]", "", s.upper()).strip(" .:'")  # scan marks beside a heading ("RECREATION FUND ‘") are dropped
            is_caps = not re.search(r"[a-z]", re.sub(r"[‘’“”]", "", s))
            fm = next(((n, c, g) for rx, n, c, g in funds if is_caps and len(u) >= 6 and rx.search(u) and not re.match(r"(?i)^(total|estimated|beginning)", u)
                       and "TOTAL" not in u and "APPROPRIAT" not in u), None)
            if fm and (re.search(r"FUND|ALTERNATE BOND|SPECIAL RECREATION$|RIVERFRONT", u)):
                fund, section, pending, start = fm, None, [], True
                continue
            if not start or fund is None:
                continue
            s2 = re.sub(r"^[^A-Za-z0-9]+", "", s)  # a scan mark before a section heading (", ESTIMATED REVENUES")
            if re.match(r"(?i)^(estimated )?revenues?\b", s2) and split_line(s2)[1] is None:
                section, pending = "revenue", []
                continue
            if re.match(r"(?i)^appro\w*", s2) and split_line(s2)[1] is None:
                section, pending = "appropriation", []
                continue
            if re.match(r"(?i)^beginning fund balance", s):
                section = "balance_pending"
                continue
            label, v = split_line(s)
            if section == "balance_pending":
                if v is not None:
                    rows.append((fund, "balance", "Beginning fund balance", v, 1, pdf_page))
                    section = None
                continue
            if v is None:
                pending.append(s)
                continue
            label = " ".join(pending + [label]).strip()
            pending = []
            printed = 0
            if re.match(r"(?i)^estimated year ending", label):
                rows.append((fund, "balance", "Estimated year-end fund balance", v, 1, pdf_page))
                continue
            if re.search(r"(?i)\btotal\b", label) or re.search(r"(?i)appropriation total$", label):
                sec = "revenue" if re.search(r"(?i)revenue", label) else "appropriation"
                rows.append((fund, sec, label, v, 1, pdf_page))
                continue
            if section == "revenue" and re.fullmatch(r"(?i)alternate bond (&|and) interest", label):  # FY2027: the revenue total of this fund carries the fund's name
                rows.append((fund, "revenue", "Total revenue", v, 1, pdf_page))
                continue
            if section in ("revenue", "appropriation"):
                rows.append((fund, section, label, v, 0, pdf_page))
    return rows, summary, problems


def group_key(r):
    return (r[0][0], r[1])


def repair(rows, alt_rows, corrections, doc):
    """Where a fund's lines do not equal its printed total, adopt the same line(s) of a second OCR reading (thr150) if that alone makes them equal; unique
    solutions only, each recorded."""
    groups = []
    for r in rows:
        if group_key(r) not in groups and r[1] in ("revenue", "appropriation"):
            groups.append(group_key(r))
    for g in groups:
        idx = [i for i, r in enumerate(rows) if group_key(r) == g and not r[4]]
        tot = next((r[3] for r in rows if group_key(r) == g and r[4]), None)
        if tot is None:
            continue
        # no single-digit "repair" here: the city's own totals are off from their lines in some ordinances (FY2023), and a digit change that makes the
        # lines tie would then invent a misreading; only a second OCR reading that differs from the first can change a line
        if sum(rows[i][3] for i in idx) == tot:
            continue
        if alt_rows is None:
            continue
        alt = {}
        seen = {}
        for r in alt_rows:
            if r[4]:
                continue
            seen[(group_key(r), r[2])] = seen.get((group_key(r), r[2]), 0) + 1
            alt[(group_key(r), r[2], seen[(group_key(r), r[2])])] = r[3]
        seen = {}
        cands = []
        for i in idx:
            r = rows[i]
            seen[(group_key(r), r[2])] = seen.get((group_key(r), r[2]), 0) + 1
            a = alt.get((group_key(r), r[2], seen[(group_key(r), r[2])]))
            if a is not None and a != r[3]:
                cands.append((i, a))
        cur = sum(rows[i][3] for i in idx)
        sols = [(c,) for c in cands if cur - rows[c[0]][3] + c[1] == tot] or \
               [(c, d) for n, c in enumerate(cands) for d in cands[n + 1:] if cur - rows[c[0]][3] + c[1] - rows[d[0]][3] + d[1] == tot]
        if len(sols) == 1:
            for i, a in sols[0]:
                corrections.append((doc, rows[i][5], "auto_resolved", f"{rows[i][0][0]} {rows[i][2]} {R.clean(rows[i][3])}", str(R.clean(a)),
                                    f"the thr150 OCR reading of this line, adopted because alone it makes the lines equal the printed total {R.clean(tot)}"))
                rows[i] = rows[i][:3] + (a,) + rows[i][4:]


def validate(rows, summary, fy, problems, allow=()):
    funds = []
    for f, *_ in rows:
        if f not in funds:
            funds.append(f)
    totals = {}
    used = set()
    for f in funds:
        for sec in ("revenue", "appropriation"):
            lines = sum(r[3] for r in rows if r[0] == f and r[1] == sec and not r[4])
            tot = [r[3] for r in rows if r[0] == f and r[1] == sec and r[4]]
            if len(tot) != 1:
                problems.append(f"{f[0]} {sec}: {len(tot)} printed totals")
                continue
            totals[(f[0], sec)] = tot[0]
            if lines != tot[0]:
                key = f"{f[0]} {sec}"
                if key in allow:
                    used.add(key)
                else:
                    problems.append(f"{key}: lines {lines} != printed total {tot[0]}")
        beg = [r[3] for r in rows if r[0] == f and r[1] == "balance" and r[2].startswith("Beginning")]
        end = [r[3] for r in rows if r[0] == f and r[1] == "balance" and r[2].startswith("Estimated")]
        if beg and end and (f[0], "revenue") in totals and (f[0], "appropriation") in totals:
            if beg[0] + totals[(f[0], "revenue")] - totals[(f[0], "appropriation")] != end[0]:
                key = f"{f[0]} balance"
                if key in allow:
                    used.add(key)
                else:
                    problems.append(f"{f[0]}: beginning {beg[0]} + revenue - appropriation != estimated end {end[0]}")
    s = {k: v for k, v, _ in summary}
    if "b" in s:
        rev = sum(v for (n, sec), v in totals.items() if sec == "revenue")
        app = sum(v for (n, sec), v in totals.items() if sec == "appropriation")
        for k, want, name in (("b", rev, "revenue"), ("c", app, "appropriation")):
            if s[k] != want:
                if f"summary {k}" in allow:
                    used.add(f"summary {k}")
                else:
                    problems.append(f"summary {k}: printed {s[k]} != sum of the fund {name} totals {want}")
        if "a" in s and "d" in s and s["a"] + s["b"] - s["c"] != s["d"]:
            if "summary d" in allow:
                used.add("summary d")
            else:
                problems.append(f"summary: a + b - c = {s['a'] + s['b'] - s['c']} != d {s['d']}")
    for k in allow:
        if k not in used:
            problems.append(f"override not used: {k}")
    return problems


def write(ent, doc, fy, rows, summary):
    out = ent.data_path("extracted", "ordinances")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{doc}_lines.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "fund", "fund_code", "general_fund_part", "section", "line_item", "amount", "printed", "pdf_page"])
        for (name, code, gfp), sec, label, v, printed, pg in rows:
            w.writerow([fy, doc, name, code, gfp, sec, label, R.clean(v), printed, pg])
    with open(os.path.join(out, f"{doc}_summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "item", "amount", "pdf_page"])
        for k, v, pg in summary:
            w.writerow([fy, doc, k, R.clean(v), pg])


def main(argv):
    doc = argv[1]
    fy = int(doc[2:6])
    ent = R.Entity.load(ENTITY)
    src = {r["doc_id"]: r for r in csv.DictReader(open(ent.config_path("source_urls.csv")))}
    ov = A.load_overrides(ent, doc)
    corrections, used = [], set()
    pdf = ent.source_path(src[doc]["pdf_path"])
    rows, summary, problems = parse(pdf, fy, doc, ent, "gray", ov, corrections, used)
    alt = None
    if any(sum(x[3] for x in rows if group_key(x) == group_key(r) and not x[4]) != r[3] for r in rows if r[4] and r[1] in ("revenue", "appropriation")):
        alt = parse(pdf, fy, doc, ent, "thr150", ov, None, set())[0]
    repair(rows, alt, corrections, doc)
    allow = {o["raw"]: o for o in ov if o["kind"] == "allow_diff"}
    validate(rows, summary, fy, problems, allow)
    for i, o in enumerate(ov):
        if o["kind"] in ("line", "line_prefix") and i not in used:
            problems.append(f"override not used: {o['raw']}")
    nfund = len({r[0][0] for r in rows})
    print(f"{doc}: {nfund} funds, {sum(1 for r in rows if not r[4])} lines, {len(summary)} summary items, {len(problems)} problems")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    write(ent, doc, fy, rows, summary)
    with open(ent.data_path("extracted", "ordinances", f"{doc}_corrections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doc", "pdf_page", "method", "raw_text", "final_text", "detail"])
        w.writerows(corrections)


if __name__ == "__main__":
    main(sys.argv)
