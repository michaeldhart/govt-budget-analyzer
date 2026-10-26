"""Extract the Water and Sewer Fund budget-and-actual schedules of the FY2012-FY2015 audits (different layouts from FY2016+).

Usage: python3 pipelines/belvidere_il/extract_audit_ws_old.py FY2013 [--prep gray]

Output (data/belvidere_il/extracted/audits/), same shapes as the FY2016+ files (extract_audit_ws_summary.py /
extract_audit_expenditures.py --schedule ws):
  FY20XX_audit_ws_summary.csv            fiscal_year, doc, pdf_page, section, line_item, original_budget, final_budget, actual [, variance,
                                         prior_year_actual: FY2012 only] , flag
  FY20XX_audit_ws_operating_expense.csv  fiscal_year, doc, pdf_page, function, department, category, line_item, original_budget, final_budget,
                                         actual, flag [, variance, prior_year_actual: FY2012 only], line_key, category_inferred
The old schedules are flat lists per department (WATER, SEWER, COLLECTION SYSTEM) with no Personnel / Contractual / ... category
subtotals, so `category` is blank (not in the source) and `category_inferred` gives the category the same line label has in the
FY2016+ schedules (a classification, not a printed number).  FY2012 prints five columns: original, final, actual, variance (positive =
favourable to the budget, measured against the FINAL budget) and the prior year's actual (FY2011); the first three go in the standard
columns and the other two are extra columns that double as validation.

Validation (all must pass; nothing is forced):
  summary   total operating revenues = its lines (incl. subtotals); total operating expenses = Water + Sewer + Collection system
            (+ depreciation, which is part of the printed total); operating income = revenues - expenses; non-operating total = its lines
            (unlabeled in FY2013-15); income before transfers = operating income + non-operating; change in net position = that +
            transfers / grants / contributions; ending net position = beginning + change (actual column; FY2012 also the 2011 column);
            FY2012: every row's variance = actual - final (final - actual for the operating-expense lines)
  expenses  each department's lines = its printed "Total <dept>" in every column; printed Total operating expenses = the departments
            (+ depreciation where the total includes it); FY2012: variance per row and the prior-year column sums
  across    the schedule's operating expense subtotals (Water / Sewer / Collection system) = the expense schedule's department totals; its
            actual column (revenues, operating income, change in net position, ending net position) = the audited income statement
            (FY20XX_audit_ws_income.csv)
Eyeballed corrections: data/belvidere_il/overrides/audit_ws_old_FY20XX.csv (kinds fixline, allow_diff, drop).
"""
import csv
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_ws_common import AUDIT_OUT, FURNITURE, ROOT, get_pages, load_overrides, parse_statement, reduce_rows  # noqa: E402
from extract_audit import page_texts_for_locating  # noqa: E402

OLD_FURNITURE = re.compile(FURNITURE.pattern.replace("water$|", "").replace("and sewer$|", ""), re.I)  # the department names are real headings here
OLD_FURNITURE = re.compile(FURNITURE.pattern.replace("water$|", "").replace("and sewer$|", ""), re.I)  # the department names are real headings here
TOT = re.compile(r"(?i)^(total\b|\(unlabeled\))")
DERIVED = re.compile(r"(?i)^((net )?operating income|(net )?income( \(loss\))? before|net income|change in net|net (position|assets)|beginning$|ending$)")
SUM_SECTIONS = [(r"^operating revenues?", "Operating Revenues"), (r"^operating expenses", "Operating Expenses"),
                (r"^non-?operating", "Non-Operating Revenues (Expenses)")]
OP_SECTIONS = [(r"^water\b", "WATER"), (r"^sewer\b", "SEWER"), (r"^collection system\b", "COLLECTION SYSTEM")]
RM_SUB = {"infrastructure", "equipment", "building", "vehicles", "contractual", "lift station"}
SUMMARY_PREFIXED = {"customer charges", "meter sales", "connection fees"}


# ---- locating ------------------------------------------------------------------------------------------
def locate(fy):
    texts = page_texts_for_locating(fy)
    head = lambda i: re.sub(r"\s+", " ", texts[i - 1][:420]).upper()
    s = None
    for i in range(60, len(texts) + 1):
        h = head(i)
        if s is None and "SCHEDULE OF REVENUES, EXPENSES" in h and "BUDGET AND ACTUAL" in h and re.search(r"WATER (AND|&) SEWER", h) and len(re.findall(r"\d{1,3},\d{3}", texts[i - 1])) > 15:
            s = i
            break
    if s is None:
        raise SystemExit(f"FY{fy}: Water and Sewer summary schedule not found")
    e = s
    while e + 1 <= len(texts) and "SCHEDULE OF OPERATING EXPENSES" in head(e + 1) and re.search(r"WATER (AND|&) SEWER", head(e + 1) + " " + re.sub(r"\s+", " ", texts[e][:900]).upper()):
        e += 1
    if e == s:
        raise SystemExit(f"FY{fy}: operating expense schedule not found after page {s}")
    return s, s + 1, e


# ---- reading ---------------------------------------------------------------------------------------------
def ncols_for(fy):
    return 5 if int(fy) == 2012 else 3


def read_rows(fy, pages, sections, ov):
    n = ncols_for(fy)
    rows, problems = parse_statement(pages, n, sections, fixline=ov["fixline"], drop=ov["drop"], tot_re=TOT, derived_re=DERIVED, ok_counts={n, 1, 2}, furniture=OLD_FURNITURE)
    return rows, problems


def reduce_cols(fy):
    return range(ncols_for(fy)) if int(fy) == 2012 else range(3)


def validate_summary(fy, rows, ov):
    problems, notes = [], []
    n = ncols_for(fy)
    # FY2012 prints the net-assets roll-forward as two values: actual and the prior year's actual (the variance cell is blank)
    for r in rows:
        if r["kind"] == "derived" and re.match(r"(?i)^(beginning|ending|net (position|assets).*(beginning|ending))", r["label"]):
            if n == 5 and len(r["vals"]) == 2:
                r["vals"] = [None, None, r["vals"][0], None, r["vals"][1]]
            elif len(r["vals"]) == 1:
                r["vals"] = [None, None, r["vals"][0]] + ([None, None] if n == 5 else [])
    for r in rows:
        if r["kind"] != "heading" and len(r["vals"]) != n:
            problems.append(f"p{r['page']} {r['label']!r}: {len(r['vals'])} values, expected {n}: {r['vals']}")
    if problems:
        return problems, notes
    safe = [dict(r, vals=[0 if x is None else x for x in r["vals"]]) for r in rows]
    for col in range(n):
        if col != 3:  # the variance column is checked row by row below
            reduce_rows(safe, col, "summary", ov["allow"], notes, problems)
    idx = lambda pat, a=0: next((i for i in range(a, len(rows)) if rows[i]["kind"] != "heading" and re.search(pat, rows[i]["label"], re.I)), None)
    tor, toe, oi = idx(r"^total operating revenues?"), idx(r"^total operating expenses"), idx(r"operating income")
    tno = idx(r"^total non-?operating")
    if tno is None:
        tno = next((i for i in range(oi or 0, len(rows)) if rows[i]["label"] == "(unlabeled)"), None)
    ib = idx(r"income( \(loss\))? before", oi or 0)
    chg = idx(r"^change in net", ib or 0)
    beg = idx(r"(^beginning$|net (position|assets).*beginning)", chg or 0)
    end = idx(r"(^ending$|net (position|assets).*ending)", chg or 0)
    if None in (tor, toe, oi, tno, ib, chg, beg, end):
        return problems + [f"summary: key line missing (rev {tor}, exp {toe}, op income {oi}, non-op total {tno}, income before {ib}, change {chg}, beginning {beg}, ending {end})"], notes
    v = lambda i, c: rows[i]["vals"][c]

    def eq(name, got, want, c):
        if got != want and ov["allow"].get(f"summary|{name}".lower()) != want - got:
            problems.append(f"summary: {name}: {got:,} vs printed {want:,} (col {c})")

    cols = [c for c in range(n) if c != 3]  # the variance column is not additive across the sign conventions; it is checked row by row
    for c in cols:
        eq("operating income = revenues - expenses", v(tor, c) - v(toe, c), v(oi, c), c)
        eq("income before transfers = operating income + non-operating", v(oi, c) + v(tno, c), v(ib, c), c)
        between = sum(rows[i]["vals"][c] for i in range(ib + 1, chg) if rows[i]["kind"] == "item")
        # items between "income before" and "change" (transfers, grants, contributions) may be listed under a total
        tot_between = [i for i in range(ib + 1, chg) if rows[i]["kind"] == "total"]
        if tot_between:
            between = sum(rows[i]["vals"][c] for i in tot_between)
        eq("change in net position = income before + transfers/grants/contributions", v(ib, c) + between, v(chg, c), c)
    for c in (2, 4) if n == 5 else (2,):
        if rows[beg]["vals"][c] is not None:
            eq("ending net position = beginning + change", v(beg, c) + v(chg, c), v(end, c), c)
    if n == 5:
        # variance = actual - final for revenues, final - actual for operating expense lines (positive = favourable); checked on every row
        in_exp = False
        for r in rows:
            if r["kind"] == "heading":
                if re.match(r"(?i)^operating expenses", r["label"]):
                    in_exp = True
                elif re.match(r"(?i)^nonoperating", r["label"]):
                    in_exp = False
                continue
            v5 = r["vals"]
            if None in v5[1:4] or r["kind"] == "derived" and v5[0] is None:
                continue
            exp_row = in_exp and not re.match(r"(?i)^(net operating income|operating income)", r["label"])
            want = (v5[1] - v5[2]) if exp_row else (v5[2] - v5[1])
            if want != v5[3] and ov["allow"].get(f"variance|{r['label']}".lower()) != v5[3] - want:
                problems.append(f"summary: variance of {r['label']!r}: printed {v5[3]:,} vs {'final - actual' if exp_row else 'actual - final'} {want:,}")
    return problems, notes


def build_opex(rows):
    """Prefixes the repair-and-maintenance sub-lines and drops the depreciation line (it is a summary item, not a department)."""
    rm = False
    for r in rows:
        if r["kind"] == "heading":
            rm = bool(re.match(r"(?i)^repairs? (and|&) maintenance", r["label"]))
            continue
        lab = r["label"]
        if rm and r["kind"] == "item":
            if lab.lower() in RM_SUB:
                r["label"] = f"Repair and maintenance - {lab.lower()}"
                r["flag"] = "rm_prefix_added"
            else:
                rm = False
    return rows


def validate_opex(fy, rows, ov):
    problems, notes = [], []
    n = ncols_for(fy)
    for r in rows:
        if r["kind"] != "heading" and len(r["vals"]) != n:
            problems.append(f"p{r['page']} {r['label']!r}: {len(r['vals'])} values, expected {n}: {r['vals']}")
    if problems:
        return problems, notes
    for col in range(n):
        reduce_rows(rows, col if col != 3 else 3, "opex", ov["allow"], notes, problems)
    for r in rows:
        if r["kind"] == "item" and n == 5:
            o, f, a, var, prior = r["vals"]
            if f - a != var and ov["allow"].get(f"variance|{r['label']}".lower()) != var - (f - a):
                problems.append(f"expenses: variance of {r['section']} / {r['label']!r}: printed {var:,} vs final - actual {f - a:,}")
    for r in rows:
        if r["kind"] == "total" and re.match(r"(?i)^total (water|sewer|collection)", r["label"]) and n == 5 and r["vals"][1] - r["vals"][2] != r["vals"][3]:
            problems.append(f"expenses: variance of {r['label']!r}: printed {r['vals'][3]:,} vs final - actual {r['vals'][1] - r['vals'][2]:,}")
    return problems, notes


def cross_old(fy, srows, orows, ov):
    problems, notes = [], []
    n = 3
    # operating expense schedule totals = the summary's Water / Sewer / Collection system lines
    for dept, pat in (("Water", r"^water$"), ("Sewer", r"^sewer$"), ("Collection System", r"^collection system$")):
        s = next((r for r in srows if r["kind"] == "item" and re.match(pat, r["label"], re.I)), None)
        t = next((r for r in orows if r["kind"] == "total" and re.match(rf"(?i)^total {dept}$", r["label"])), None)
        if s is None or t is None:
            problems.append(f"across: {dept} not found in both schedules")
        elif s["vals"][:n] != t["vals"][:n]:
            problems.append(f"across: {dept} summary {s['vals'][:n]} vs expense schedule total {t['vals'][:n]}")
    # actual column vs the audited income statement
    path = os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_income.csv")
    if os.path.exists(path):
        inc = {}
        for r in csv.DictReader(open(path)):
            if r["column"] == "Water and Sewer" and r["line_id"]:
                inc[r["line_id"]] = int(r["amount"])
        pairs = [("total_operating_revenues", r"^total operating revenues"), ("total_operating_expenses", r"^total operating expenses"),
                 ("operating_income", r"operating income"), ("change_in_net_position", r"^change in net"), ("np_ending", r"(^ending$|net (position|assets).*ending)")]
        for lid, pat in pairs:
            s = next((r for r in srows if r["kind"] != "heading" and re.search(pat, r["label"], re.I)), None)
            if s is not None and lid in inc and s["vals"][2] != inc[lid]:
                problems.append(f"across: summary {lid} {s['vals'][2]:,} vs audited income statement {inc[lid]:,}")
    else:
        notes.append("income statement extract not present; actual column not compared")
    return problems, notes


# ---- category inference (FY2016+ labels) -----------------------------------------------------------------
def line_key(label):
    k = re.sub(r"[^a-z0-9 ]", " ", label.lower().replace("&", " and "))
    k = re.sub(r"\brepairs\b", "repair", k)
    k = re.sub(r"\b([a-z]{4,})s\b", r"\1", k)  # vehicles/vehicle, rentals/rental: the FY2016+ schedules mix singular and plural
    return re.sub(r"\s+", " ", k).strip()


# Old labels with no FY2016+ counterpart: a classification decision (documented in docs/handoff/ws_audit_statements.md), by analogy
CATEGORY_ALIASES = {"supplies": "Commodities", "equipment rental": "Commodities", "other equipment rental": "Commodities", "office equipment rental": "Commodities",
                    "consulting": "Contractual services", "construction in progress": "Capital outlay", "consruction in progress": "Capital outlay",
                    "building": "Capital outlay", "health insurance": "Personnel services"}


def category_map():
    m = {}
    for y in range(2016, 2027):
        p = os.path.join(AUDIT_OUT, f"FY{y}_audit_ws_operating_expense.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p)):
                m.setdefault(line_key(r["line_item"]), Counter())[r["category"]] += 1
    return {k: c.most_common(1)[0][0] for k, c in m.items()}


def infer(catmap, label, key):
    """FY2016+ category of the same label; else the manual alias; a standalone 'Building' (not the repair line) is capital outlay."""
    if label.lower().startswith("repair and maintenance - "):
        return catmap.get(key, ""), "fy2016plus_label"
    if key in catmap:
        return catmap[key], "fy2016plus_label"
    aliases = {line_key(k): v for k, v in CATEGORY_ALIASES.items()}
    if key in aliases:
        return aliases[key], "manual_alias"
    return "", ""


def run(fy, prep, loc, ov):
    s, o1, o2 = loc
    srows, sp = read_rows(fy, get_pages(fy, prep, s, s), SUM_SECTIONS, ov)
    vs, sn = validate_summary(fy, srows, ov)
    sp += vs
    orows, op_ = read_rows(fy, get_pages(fy, prep, o1, o2), OP_SECTIONS, ov)
    orows = build_opex(orows)
    vo, on = validate_opex(fy, orows, ov)
    op_ += vo
    cp, cn = cross_old(fy, srows, orows, ov) if not sp and not op_ else ([], [])
    return dict(prep=prep, srows=srows, orows=orows, problems=sp + op_ + cp, notes=sn + on + cn, n=len(sp + op_ + cp))


def summary_postprocess(rows):
    """Section labels and the printed-name fixes: prefix the water/sewer revenue lines, name the unlabeled non-operating subtotal."""
    sub, section, after_ib = "", "", False
    out = []
    for r in rows:
        if r["kind"] == "heading":
            t = r["label"].strip(" :")
            if re.match(r"(?i)^(water|sewer)$", t):
                sub = t.title()
            elif re.match(r"(?i)^(operating revenues|operating expenses|nonoperating)", t):
                sub = ""
            continue
        lab = r["label"]
        if r["section"] == "Operating Revenues" and sub and lab.lower() in SUMMARY_PREFIXED:
            lab = f"{sub} - {lab.lower()}"
        if lab == "(unlabeled)" and r["kind"] == "total":
            lab, r["flag"] = "Total non-operating revenues (expenses)", "unlabeled_in_source"
        if re.search(r"(?i)income( \(loss\))? before", lab):
            after_ib = True
        sec = "Transfers" if after_ib and not re.search(r"(?i)income( \(loss\))? before", lab) else r["section"]
        out.append(dict(r, label=lab, section=sec if sec else r["section"]))
    return out


def write_summary(path, fy, rows):
    five = ncols_for(fy) == 5
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "section", "line_item", "original_budget", "final_budget", "actual"] + (["variance", "prior_year_actual"] if five else []) + ["flag"])
        for r in rows:
            v = list(r["vals"]) + [None] * 5
            cells = ["" if x is None else x for x in v[:3]]
            row = [fy, f"FY{fy}_audit", r["page"], r["section"], r["label"], *cells]
            if five:
                row += ["" if x is None else x for x in (v[3], v[4])]
            w.writerow(row + [r.get("flag", "")])


def write_opex(path, fy, rows, catmap):
    five = ncols_for(fy) == 5
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "function", "department", "category", "line_item", "original_budget", "final_budget", "actual", "flag"]
                   + (["variance", "prior_year_actual"] if five else []) + ["line_key", "category_inferred", "category_inferred_basis"])
        for r in rows:
            if r["kind"] != "item" or r["section"] not in ("WATER", "SEWER", "COLLECTION SYSTEM") or re.match(r"(?i)^depreciation", r["label"]):
                continue
            v = list(r["vals"]) + [""] * 5
            k = line_key(r["label"])
            w.writerow([fy, f"FY{fy}_audit", r["page"], r["section"], "", "", r["label"], *v[:3], r.get("flag", "")] + (list(v[3:5]) if five else []) + [k, *infer(catmap, r["label"], k)])


def report():
    """Re-runs all four years and writes data/belvidere_il/ws_audit_old_schedules_validation.csv."""
    out = os.path.join(AUDIT_OUT, "..", "..", "ws_audit_old_schedules_validation.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "schedule", "pdf_pages", "lines", "totals_checked", "row_variance_checks", "problems", "notes"])
        for fy in ("2012", "2013", "2014", "2015"):
            loc = locate(int(fy))
            ov = load_overrides("audit_ws_old", fy)
            res = run(fy, "gray", loc, ov)
            for name, rows, pages in (("summary (Schedule of Revenues, Expenses and Changes in Net Position - Budget and Actual)", res["srows"], f"{loc[0]}"),
                                      ("operating expenses (Schedule of Operating Expenses - Budget and Actual)", res["orows"], f"{loc[1]}-{loc[2]}")):
                items = [r for r in rows if r["kind"] != "heading"]
                nvar = len([r for r in items if r["kind"] != "derived"]) if fy == "2012" else 0
                w.writerow([fy, name, pages, len(items), len([r for r in items if r["kind"] == "total"]) + (5 if name.startswith("summary") else 0), nvar,
                            len(res["problems"]), len(res["notes"])])
    print("wrote", os.path.relpath(out, ROOT))


def main():
    if "--report" in sys.argv:
        return report()
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "gray"
    loc = locate(int(fy))
    ov = load_overrides("audit_ws_old", fy)
    res = run(fy, arg, loc, ov)
    print(f"prep {arg}: {res['n']} problems; summary page {loc[0]}, operating expenses pages {loc[1]}-{loc[2]}")
    write_summary(os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_summary.csv"), fy, summary_postprocess(res["srows"]))
    write_opex(os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_operating_expense.csv"), fy, res["orows"], category_map())
    for n in res["notes"]:
        print("  note:", n)
    if res["n"]:
        sys.exit(1) if False else None
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
