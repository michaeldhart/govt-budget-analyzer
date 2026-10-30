"""Extract a Belvidere Park District tax levy ordinance (FY2022-FY2027; scans, the FY2027 draft has a text layer).

Usage: python3 pipelines/belvidere_park_district/extract_ordinance_levy.py FY2024_levy

Layout: Section 1 levies an aggregate sum (in words and in figures, `($4,329,577.45)`) for fiscal year N (tax year N-1); then each fund (I. GENERAL CORPORATE FUND, II.
RECREATION FUND, III. JOINT HANDICAP REC FUND (= Special Recreation), IV. MUSEUM, V. LIABILITY INSURANCE, VI. AUDIT, VII. I.M.R.F., VIII. SOCIAL SECURITY, IX. BOND &
INTEREST) lists the purposes with their expenditures, `Total Expenditures` and `Total to be raised by Taxation` (the fund's levy), and a SUMMARY repeats the
levy per fund. The preamble gives the percent increase over the previous extension.
Output (data/belvidere_park_district/extracted/ordinances/<doc>_levy_lines.csv): fund, fund_code, general_fund_part, section (expenditure | levy), line_item, amount,
printed, pdf_page; <doc>_levy_summary.csv: aggregate_levy (the figure in Section 1), percent_increase, and the SUMMARY lines.
Validation (all must tie or the script fails, `allow_diff` overrides record the city's own arithmetic): each fund's purposes add to its Total Expenditures; the fund
levies add to the aggregate levy and equal the SUMMARY lines; the SUMMARY total equals the aggregate levy.
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
import extract_ordinance_appropriation as O  # noqa: E402

ENTITY = R.ENTITY
HEAD = re.compile(r"^[IVXl1|]{1,5}[.,]?\s+(.*FUND.*|.*)$")


NUMERAL = re.compile(r"^\S{1,5}[.,]\s+")  # the roman numeral of a heading, in any case the scan gives it ("Vill.", "tt.")


def clean_name(s):
    t = re.sub(r"(?<![A-Za-z])[1L|!]\.?M\.?R\.?F", "I.M.R.F", NUMERAL.sub("", s.strip()))  # "1.M.R.F.", "|.M.R.F.", "!.M.R.F." are the scan's I.M.R.F.
    return re.sub(r"[^A-Z0-9&.,' ]", "", t.upper()).strip(" .:'")


def fund_of(funds, name):
    return next(((n, c, g) for rx, n, c, g in funds if rx.search(clean_name(name))), None)


def lines_of(pdf, doc, prep, overrides, corrections, used):
    text = []
    old_doc = doc.startswith("FY2022_levy")  # the FY2022 ordinance is an older layout (amounts print without cents, trailing commas)
    for pdf_page, page in enumerate(R.pages_of(pdf, doc, prep=prep), start=1):
        for raw in page.splitlines():
            s = re.sub(r"^[^\w$(]+|^_+", "", raw.strip())
            if old_doc:
                s = s.rstrip(",.")  # scan junk before a line ("-- Total", "_~ for", "‘Total")
            for i, o in enumerate(overrides):
                if o["kind"] in ("line", "line_prefix") and s.startswith(o["raw"]):
                    if corrections is not None:
                        corrections.append((doc, pdf_page, o["kind"], s, o["fixed"], o["reason"]))
                    s = o["fixed"] if o["kind"] == "line" else o["fixed"] + s[len(o["raw"]):]
                    used.add(i)
            if s and not re.fullmatch(r"\W*\d{1,2}\W*", s):
                text.append((pdf_page, s))
    return text


def parse(pdf, fy, doc, ent, prep="gray", overrides=(), corrections=None, used=None):
    funds = O.fund_config(ent, fy)
    used = used if used is not None else set()
    text = lines_of(pdf, doc, prep, overrides, corrections, used)
    flat = " ".join(t for _, t in text)
    rows, summary, problems = [], [], []
    old = "Said amount" in flat
    pc = re.search(r"represents (?:a|an)\s+([\d.]+)\s*%\s+(?:increase|decrease)", flat)
    if pc:
        summary.append(("percent_increase", Decimal(pc.group(1)), 1))
    if not old:
        m = re.search(r"\(\$\s*([\d,]+\.\d{2})\)", flat)
        if m:
            summary.append(("aggregate_levy", Decimal(m.group(1).replace(",", "")), 1))
        else:
            problems.append("aggregate levy figure not found")
    fund, mode, pend, in_summary = None, None, [], False
    for pdf_page, s in text:
        if re.match(r"(?i)^summary( of levies)?$", s):
            in_summary, fund = True, None
            continue
        if in_summary:
            m = re.match(r"(?i)^(?:levied (?:as tax )?for|total (?:tax )?levy for)\s+(.*?)\s*\$?\s*([\d,]+(?:\.\d{2})?)\s*$", s)
            if m:
                summary.append((f"summary: {m.group(1).strip()}", O.to_decimal(m.group(2)), pdf_page))
                continue
            m = re.match(r"(?i)^total(?: tax)? levy\s*\$?\s*([\d,]+(?:\.\d{2})?)\s*$|^total\s+([\d,]{7,})\s*$|^total amount to be levied[^$\d]*\$?\s*([\d,]+(?:\.\d{2})?)\s*$", s)
            if m:
                summary.append(("summary_total", O.to_decimal(m.group(1) or m.group(2) or m.group(3)), pdf_page))
            continue
        u = clean_name(s)
        rest = NUMERAL.sub("", s)
        heading = (not old or re.match(r"^[IVXLivxl1|!tTHh]{1,4}[.,]\s", s)) and NUMERAL.match(s) and not re.search(r"\$|\d{3}", rest) and not re.match(r"(?i)^(section|total|summary)", rest) and \
            (old and re.fullmatch(r"[A-Za-z .&'/-]+", rest) or not old and ("FUND" in u or "PRIOR YEAR ADJUSTMENT" in u) and not re.search(r"[a-z]", re.sub(r"[‘’“”]", "", rest)))
        if heading:
            cfg = fund_of(funds, s)
            if cfg is not None:
                fund, mode, pend = cfg, None, []
                continue
        if fund is None:
            continue
        if old:
            m = re.match(r"(?i)^\*?\s*said amount to hereby levied as (.*?)\s+([\d,]+)\s*,?$", s)
            if m:
                rows.append((fund, "levy", m.group(1).strip(), O.to_decimal(m.group(2)), 1, pdf_page))
                continue
            label, v = O.split_line(s)
            if v is None:
                continue
            if re.match(r"(?i)^total\b", label):
                rows.append((fund, "expenditure", "Total Expenditures", v, 1, pdf_page))
            else:
                rows.append((fund, "expenditure", re.sub(r"^[A-Za-z0-9]{1,2}[.,]\s+", "", label), v, 0, pdf_page))
            continue
        if re.match(r"(?i)^total to be raised by taxation", s):
            label, v = O.split_line(s)
            if v is not None and not re.fullmatch(r"(?i)total to be raised by taxation", label):
                rows.append((fund, "levy", re.sub(r"(?i)^total to be raised by taxation\s*", "", label) or "levy", v, 1, pdf_page))
                mode = None
            else:
                mode = "levy"
            continue
        label, v = O.split_line(s)
        if mode == "levy":
            if v is not None:
                rows.append((fund, "levy", re.sub(r"(?i)^for\s+", "", label), v, 1, pdf_page))
                mode = None
            continue
        if v is None:
            pend.append(s)
            continue
        label = " ".join(pend + [label]).strip()
        pend = []
        if re.match(r"(?i)^total expenditures?", label):
            rows.append((fund, "expenditure", "Total Expenditures", v, 1, pdf_page))
        else:
            rows.append((fund, "expenditure", label, v, 0, pdf_page))
    return rows, summary, problems


def validate(rows, summary, problems, allow=(), ent=None, fy=None):
    used = set()

    def tie(key, a, b, msg):
        if a != b:
            if key in allow:
                used.add(key)
            else:
                problems.append(msg)
    funds = []
    for f, *_ in rows:
        if f not in funds:
            funds.append(f)
    levies = {}
    for f in funds:
        lines = sum(r[3] for r in rows if r[0] == f and r[1] == "expenditure" and not r[4])
        tot = [r[3] for r in rows if r[0] == f and r[1] == "expenditure" and r[4]]
        lev = [r[3] for r in rows if r[0] == f and r[1] == "levy"]
        if f[0].startswith("Prior Year Adjustment") and len(lev) == 1 and not tot:
            levies[f[0]] = lev[0]
            continue
        if not tot and len(lev) == 1 and f"{f[0]} total missing" in allow:  # FY2022: the IMRF total line prints no figure
            used.add(f"{f[0]} total missing")
            levies[f[0]] = lev[0]
            continue
        if len(tot) != 1 or len(lev) != 1:
            problems.append(f"{f[0]}: {len(tot)} Total Expenditures, {len(lev)} levies")
            continue
        levies[f[0]] = lev[0]
        tie(f"{f[0]} expenditure", lines, tot[0], f"{f[0]}: purposes {lines} != Total Expenditures {tot[0]}")
    s = {k: v for k, v, _ in summary}
    agg = s.get("aggregate_levy", s.get("summary_total"))
    if agg is not None:
        total = sum(levies.values())
        adj = sum(v for k, v in levies.items() if k.startswith("Prior Year Adjustment"))
        if total != agg and total - adj == agg:  # FY2024: the aggregate leaves out the Prior Year Adjustment (a memo inside Special Recreation)
            total = agg
        tie("aggregate levy", total, agg, f"fund levies add to {total}, the ordinance levies {agg}")
    cfg = O.fund_config(ent, fy)
    for k, v, _ in summary:
        if k.startswith("summary: "):
            hit = fund_of(cfg, k[9:])
            if hit is None:
                problems.append(f"{k}: unknown fund")
                continue
            tie(k, v, levies.get(hit[0]), f"{k}: {v} != the fund's levy {levies.get(hit[0])}")
    if "aggregate_levy" in s and "summary_total" in s:
        tie("summary total", s["summary_total"], s["aggregate_levy"], f"summary total {s['summary_total']} != aggregate levy {s['aggregate_levy']}")
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
    ov = A.load_overrides(ent, doc + "_levy") if False else A.load_overrides(ent, doc)
    corrections, used = [], set()
    pdf = ent.source_path(src[doc]["pdf_path"])
    rows, summary, problems = parse(pdf, fy, doc, ent, "gray", ov, corrections, used)
    allow = {o["raw"]: o for o in ov if o["kind"] == "allow_diff"}
    validate(rows, summary, problems, allow, ent, fy)
    for i, o in enumerate(ov):
        if o["kind"] in ("line", "line_prefix") and i not in used:
            problems.append(f"override not used: {o['raw']}")
    print(f"{doc}: {len({r[0][0] for r in rows})} funds, {sum(1 for r in rows if not r[4])} purposes, {len(summary)} summary items, {len(problems)} problems")
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
