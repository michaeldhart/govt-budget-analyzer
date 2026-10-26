"""Shared helpers for the Water and Sewer audit work: statement locating, line parsing, the total-reduction validator, line ids.

Used by extract_audit_ws_statements.py (Statement of Net Position, of Revenues, Expenses and Changes in Net Position, of Cash
Flows; FY2012-FY2026) and extract_audit_ws_old.py (the FY2012-FY2015 budget-and-actual schedules).  Reuses the techniques of
extract_audit_fund_statements.py (tidy: split-digit re-joining; lone marks for dashes), extract_audit.py (clean / split_numbers /
page_texts_for_locating) and extract_budget.py (ocr_page).

Core idea of the validator ("reduce"): rows are read in order.  Item rows push their value on a stack; a total row (label starts with
"Total", "Net cash ...", "Net increase ...") must equal the sum of a suffix of the stack that starts at a heading boundary (the
latest such boundary that ties); the suffix is then replaced by the printed total, so higher totals (Total assets = current +
noncurrent) are checked against totals, not lines.  Only if no heading boundary ties does it fall back to the smallest suffix
with a non-zero first entry (noted as `window_fallback`).  Nothing is forced: a total that ties nowhere is a problem.
"""
import csv
import os
import re
import sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, ROOT, audit_pdf, clean, page_texts_for_locating, split_numbers  # noqa: E402
from extract_audit_fund_statements import tidy  # noqa: E402
from extract_budget import digit_variants, ocr_page  # noqa: E402

AUDIT_OUT = os.path.join(DATA_E, "extracted", "audits")
OV_DIR = os.path.join(DATA_E, "overrides")

FURNITURE = re.compile(
    r"(?i)^(city of|statement of|proprietary fund|water (&|and) sewer( fund)?( - enterprise fund)?$|for the (fiscal )?year|year ended|april ?\d+,? ?\d{4}$|april ?30|business-type|activit|"
    r"\(this statement|see accompanying|the notes to|with comparative|- ?\d+ ?-?$|\d+$|(19|20)\d\d(\s+(19|20)\d\d)*$|and sewer$|water$|"
    r"original|final|actual|budget\b|variance|positive|\(negative|enterprise fund|schedule of|changes in (fund )?net|"
    r"proprietary fund type)")


ALWAYS_SKIP = re.compile(r"(?i)^(for the (fiscal )?year ended|year ended|april ?(\d+|thirty),? ?(19|20)\d\d$|with comparative|(19|20)\d\d$)")


def get_pages(fy, prep, first, last):
    if prep == "text":
        with pdfplumber.open(audit_pdf(fy)) as d:
            return [(p, d.pages[p - 1].extract_text() or "") for p in range(first, last + 1)]
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    return [(p, ocr_page(audit_pdf(fy), p, outdir, prep)) for p in range(first, last + 1)]


def load_overrides(prefix, fy):
    """Override file data/belvidere_il/overrides/<prefix>_FY20XX.csv: columns kind,page_label,key,column,value,note.
    kinds: fixline (key = the cleaned OCR/text line, value = corrected line), allow_diff (key = 'statement|label', value = the
    printed total minus its lines that the source itself shows), drop (key = a cleaned line to ignore)."""
    ov = dict(fixline={}, allow={}, drop=set())
    path = os.path.join(OV_DIR, f"{prefix}_FY{fy}.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "fixline":
                ov["fixline"][o["key"]] = o["value"]
            elif o["kind"] == "allow_diff":
                ov["allow"][o["key"].lower()] = int(o["value"])
            elif o["kind"] == "drop":
                ov["drop"].add(o["key"])
    return ov


# ---- parsing ---------------------------------------------------------------------------------------------
WRAP_END = re.compile(r"(?i)\b(and|of|from|to|in|for|excluding|noncapital|capital|related|the|net|by|or)$")


def is_continuation(label):
    return bool(label) and (label[0].islower() or re.match(r"(?i)^(and|or|of)\s", label) is not None)


def parse_statement(pages, ncols, sections, fixline=None, drop=(), tot_re=None, info_re=None, derived_re=None, ok_counts=None, furniture=None):
    furniture = furniture or FURNITURE
    """rows: list of dict(kind in heading|item|total|info|derived, label, vals, section, sub, page, flag).
    `sections` maps heading regexes to canonical section names.  Unnumbered lines are headings unless they continue the next
    numbered line's label ("Net cash from capital and related" + "financing activities (1,114,817)")."""
    rows, buf = [], []
    section, sub = "", ""
    problems = []

    def add_heading(text, pg):
        nonlocal section, sub
        for pat, name in sections:
            if re.search(pat, text, re.I):
                section, sub = name, ""
                break
        else:
            sub = text.title() if text.isupper() else text
        rows.append(dict(kind="heading", label=text, vals=[], section=section, sub=sub, page=pg))

    last_pg = None
    for pg, text in pages:
        for raw in text.splitlines():
            line = tidy(re.sub(r"(?<=\d)\s+[.|:;~)]+\s*$", "", raw.strip()))  # a stray mark after the last number is scan noise, not a blank cell
            if fixline and line in fixline:
                line = fixline[line]
            if line in drop:
                continue
            if not line or re.match(r"^[-–—]\s*\d+\s*[-–—]?$|^\d{1,3}$", line) or ALWAYS_SKIP.match(line):
                continue  # blank, a page number ("- 15 -", "11"), or the date / period line under the title
            label, vals = split_numbers(line)
            if vals and ncols > 1 and not any(ch.isdigit() for ch in label) and len(vals) == ncols and all(1990 <= x <= 2040 for x in vals):
                vals = []  # "Current Assets: 2012 2011": a heading with the column years
            if len(vals) > ncols and (ncols > 1 or all(1900 <= x <= 2100 for x in vals[:-1])):
                extra, vals = vals[:-ncols], vals[-ncols:]  # leading numbers belong to the label ("Series 2003", "bond issue -")
                label = (label + " " + " ".join("-" if x == 0 else str(x) for x in extra)).strip()
            if not vals and (furniture.match(line) or sum(ch.isalpha() for ch in line) < 3):
                continue
            if vals and not label and all(1990 <= v <= 2040 for v in vals):
                continue  # the "2012 2011" column header
            if vals and furniture.match(label) and not re.match(r"(?i)^(water|and sewer)\b", label) and re.match(r"(?i)^(city of|statement of|for the|see |\(this)", label):
                continue
            last_pg = pg
            if not vals:
                if buf and (is_continuation(label) or (WRAP_END.search(buf[-1][0]) and label.isupper() == buf[-1][0].isupper())):
                    buf[-1] = (buf[-1][0] + " " + label, buf[-1][1])
                else:
                    buf.append((label, pg))
                continue
            if buf and (is_continuation(label) or WRAP_END.search(buf[-1][0])):
                label = buf.pop()[0] + " " + label
            for h, hp in buf:
                add_heading(h, hp)
            buf = []
            if not label:
                label = "(unlabeled)"
            label = label.replace("MAY ONE", "MAY 1").replace("APRIL THIRTY", "APRIL 30").strip(" ,;:")
            kind = "item"
            if tot_re and tot_re.search(label):
                kind = "total"
            elif info_re and info_re.search(label):
                kind = "info"
            elif derived_re and derived_re.search(label):
                kind = "derived"
            rows.append(dict(kind=kind, label=label, vals=vals, section=section, sub=sub, page=pg))
            if kind == "total":
                sub = ""
    for h, hp in buf:
        add_heading(h, hp)
    for r in rows:
        if r["kind"] != "heading" and len(r["vals"]) not in (ok_counts or {ncols}):
            problems.append(f"p{r['page']} {r['label']!r}: {len(r['vals'])} values, expected {ncols}: {r['vals']}")
    return rows, problems


# ---- the total-reduction validator --------------------------------------------------------------------
def reduce_rows(rows, col, stmt, allow, notes, problems, stop_at=None):
    """Runs the stack validator over rows for value column `col`.  Returns the final stack."""
    stack, bounds = [], []
    for i, r in enumerate(rows):
        if stop_at is not None and i >= stop_at:
            break
        if r["kind"] == "heading":
            bounds.append(len(stack))
            continue
        if len(r["vals"]) <= col:
            continue
        v = r["vals"][col]
        if r["kind"] == "item":
            stack.append(v)
        elif r["kind"] == "total":
            found = None
            for b in sorted(set(bounds), reverse=True):
                if b < len(stack) and sum(stack[b:]) == v:
                    found = b
                    break
            if found is None:
                for b in range(len(stack) - 1, -1, -1):
                    if (b == 0 or stack[b] != 0) and sum(stack[b:]) == v:
                        found = b
                        notes.append(f"{stmt} p{r['page']} {r['label']!r} col{col}: ties only as a suffix not starting at a heading (window_fallback)")
                        r["flag"] = "window_fallback"
                        break
            if found is None:
                key = f"{stmt}|{r['label']}".lower()
                diff_candidates = []
                for b in sorted(set(bounds), reverse=True) + [0]:
                    if b < len(stack):
                        diff_candidates.append((b, v - sum(stack[b:])))
                ok = next((b for b, d in diff_candidates if allow.get(key) == d), None)
                if ok is not None:
                    notes.append(f"{stmt} {r['label']!r}: printed total differs from its lines by {allow[key]:+,} (source arithmetic; allowed by override)")
                    found = ok
                else:
                    best = min(diff_candidates, key=lambda bd: abs(bd[1]), default=None)
                    lat = best[0] if best else len(stack)
                    got = sum(stack[lat:]) if best else None
                    problems.append(f"{stmt} p{r['page']} {r['label']!r}: printed {v:,} vs lines {'n/a' if got is None else format(got, ',')} (closest window; diff {'n/a' if best is None else format(best[1], '+,')}; col{col}); stack tail {stack[-8:]}")
                    found = lat if lat < len(stack) else len(stack)
            stack[found:] = [v]
            bounds = [b for b in bounds if b <= found]
    return stack


def segment_sum(rows, a, b, col):
    """Sum of the top-level entries of rows[a+1:b] after reduction (items not covered by a total, plus the totals)."""
    sub = rows[a + 1:b]
    st = reduce_rows(sub, col, "seg", {}, [], [])
    return sum(st)


# ---- canonical section names ---------------------------------------------------------------------------
NP_SECTIONS = [(r"^current assets", "Current assets"), (r"^noncurrent assets", "Noncurrent assets"), (r"^deferred outflows", "Deferred outflows of resources"),
               (r"^current liabilities", "Current liabilities"), (r"^noncurrent liabilities", "Noncurrent liabilities"),
               (r"^deferred inflows", "Deferred inflows of resources"), (r"^net (position|assets)", "Net position")]
INC_SECTIONS = [(r"^operating revenues?", "Operating revenues"), (r"^operating expenses", "Operating expenses"), (r"^non-?operating", "Non-operating revenues (expenses)"),
                (r"^(capital grants|transfers|capital contributions)", "Capital grants, contributions and transfers"), (r"^net (position|assets)", "Net position")]
CF_SECTIONS = [(r"^cash flows? from operating", "Cash flows from operating activities"), (r"^cash flows? from noncapital", "Cash flows from noncapital financing activities"),
               (r"^cash flows? from capital", "Cash flows from capital and related financing activities"), (r"^cash flows? from investing", "Cash flows from investing activities"),
               (r"^reconciliation", "Reconciliation of operating income to net cash from operating activities"), (r"^noncash", "Noncash transactions"),
               (r"^cash and cash equivalents", "Cash and cash equivalents")]

# ---- line ids (stable ids for the lines the analysis uses; others are blank) --------------------------
LINE_IDS = {
    "netposition": [
        (r"^cash and (cash equivalents|investments)$", "cash"),
        (r"^pooled$", "cash_pooled"),
        (r"^non-pooled", "cash_nonpooled"),
        (r"^investments$", "cash_investments_fy2012"),
        (r"^restricted investments|^bond account", "restricted_investments"),
        (r"^total current assets", "total_current_assets"),
        (r"^total noncurrent assets", "total_noncurrent_assets"),
        (r"^total assets and deferred outflows", "total_assets_and_deferred_outflows"),
        (r"^total assets$", "total_assets"),
        (r"^total deferred outflows", "total_deferred_outflows"),
        (r"^total current liabilities", "total_current_liabilities"),
        (r"^total noncurrent liabilities", "total_noncurrent_liabilities"),
        (r"^total liabilities and deferred inflows", "total_liabilities_and_deferred_inflows"),
        (r"^total liabilities$", "total_liabilities"),
        (r"^total deferred inflows", "total_deferred_inflows"),
        (r"^(net investment in|invested in) capital assets", "np_net_investment_in_capital"),
        (r"^unrestricted", "np_unrestricted"),
        (r"^total net (position|assets)", "np_total"),
        (r"^accumulated depreciation", "accumulated_depreciation"),
        (r"^net pension liability", "net_pension_liability"),
        (r"^net capital assets$", "net_capital_assets"),
    ],
    "income": [
        (r"^total operating revenues", "total_operating_revenues"),
        (r"^total operating expenses", "total_operating_expenses"),
        (r"^operating income before depreciation", "operating_income_before_depreciation"),
        (r"^depreciation", "depreciation"),
        (r"^(net )?operating income( \(loss\))?$|^net operating income", "operating_income"),
        (r"^total non-?operating", "total_nonoperating"),
        (r"^(net )?income \(loss\) before|^net income \(loss\) before|^income before", "income_before_capital_grants_transfers"),
        (r"^change in net (position|assets)", "change_in_net_position"),
        (r"restated", "np_restated"),
        (r"^net (position|assets)[ ,-]*(may 1|beginning)$|^beginning$", "np_beginning"),
        (r"^net (position|assets)[ ,-]*(april 30|ending)$|^ending$", "np_ending"),
    ],
    "cashflow": [
        (r"^net cash (from|provided).*operating activities", "net_cash_operating"),
        (r"^net cash (from|provided).*noncapital", "net_cash_noncapital_financing"),
        (r"^net cash (from|provided).*(capital and related|financing activities)", "net_cash_capital_financing"),
        (r"^net cash (from|provided).*investing", "net_cash_investing"),
        (r"^net (increase|decrease|\(decrease\)|\(increase\))|^net change in cash", "net_change_in_cash"),
        (r"^cash and cash equivalents[ ,-]*(may 1|beginning)$|^beginning$", "cash_beginning"),
        (r"^cash and cash equivalents[ ,-]*(april 30|ending)$|^ending$", "cash_ending"),
        (r"^operating income", "recon_operating_income"),
        (r"^total cash and cash equivalents", "total_cash_and_equivalents"),
        (r"^receipts from customers", "receipts_from_customers"),
        (r"^payments to suppliers", "payments_to_suppliers"),
        (r"^payments to employees", "payments_to_employees"),
        (r"^purchases? of capital assets?", "purchases_of_capital_assets"),
        (r"^principal (retirement|paid)", "principal_paid"),
    ],
}


def line_id(stmt, label):
    for pat, lid in LINE_IDS[stmt]:
        if re.search(pat, label.strip(), re.I):
            return lid
    return ""


def write_stmt_csv(path, fy, stmt, rows, colnames, ids=True):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "statement", "section", "subsection", "line_item", "line_id", "column", "amount", "flag"])
        for r in rows:
            if r["kind"] == "heading":
                continue
            lid = r.get("lid") or (line_id(stmt, r["label"]) if ids else "")
            for cname, v in zip(colnames, r["vals"]):
                w.writerow([fy, f"FY{fy}_audit", r["page"], stmt, r["section"], r["sub"], r["label"], lid, cname, v, r.get("flag", "")])
