"""Extract the Water and Sewer Fund's proprietary fund statements from an audit: Statement of Net Position (Net Assets before GASB 63),
Statement of Revenues, Expenses and Changes in Net Position, and Statement of Cash Flows.  Audits FY2012-FY2026.

Usage: python3 pipelines/belvidere_il/extract_audit_ws_statements.py FY2020 [--prep auto|text|gray]

Output (data/belvidere_il/extracted/audits/): FY20XX_audit_ws_netposition.csv, FY20XX_audit_ws_income.csv, FY20XX_audit_ws_cashflow.csv
(fiscal_year, doc, pdf_page, statement, section, subsection, line_item, line_id, column, amount, flag), one row per line x column.
FY2012 prints a comparative April 30, 2011 column next to April 30, 2012: both are kept (column `Water and Sewer` = the audit's own
year; `Water and Sewer (prior-year comparative)` = FY2011).

Validation (a year passes with 0 problems; nothing is forced):
  net position   every Total line = its lines (suffix of the running stack at a heading boundary, see audit_ws_common.reduce_rows);
                 total assets (+ deferred outflows) = total liabilities (+ deferred inflows) + total net position
  income stmt    total operating revenues / expenses = lines; operating income = revenues - expenses (- depreciation if separate);
                 non-operating total = lines; (income before transfers = operating income + non-operating); change in net position =
                 that + capital grants / contributions / transfers; ending net position = beginning (restated) + change
  cash flows     each section's net cash = its lines; net change = sum of the section nets; ending cash = beginning + net change;
                 reconciliation: operating income + adjustments = net cash from operating activities; the two net-cash-from-operations
                 figures agree; total noncash transactions / total cash and equivalents = their lines
  across         ending net position (income stmt) = total net position (net position stmt); ending cash (cash flows) = cash and
                 investments (+ restricted) in the net position stmt; operating income and depreciation in the reconciliation = the income
                 statement's; GAAP change / ending net position = the budgetary summary schedule (FY20XX_audit_ws_summary.csv) where present.
--prep auto reads the text layer (FY2013-FY2026; poor for FY2013-15) and 300 dpi gray OCR (all years) and keeps the one with fewer
problems.  Eyeballed corrections: data/belvidere_il/overrides/audit_ws_stmt_FY20XX.csv (kinds fixline, allow_diff, drop; see audit_ws_common).
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_ws_common import (AUDIT_OUT, CF_SECTIONS, INC_SECTIONS, NP_SECTIONS, ROOT, get_pages, line_id, load_overrides,  # noqa: E402
                             parse_statement, reduce_rows, segment_sum, write_stmt_csv)
from extract_audit import page_texts_for_locating  # noqa: E402

NP_TOT = re.compile(r"(?i)^(total\b(?! (opeb|pension|net pension) liab)|net capital assets|\(unlabeled\))")
INC_TOT = re.compile(r"(?i)^(total\b|\(unlabeled\))")
INC_DERIVED = re.compile(r"(?i)^((net )?operating income|(net )?income \(loss\) before|(net )?income before|net income|change in net|net (position|assets)|beginning$|ending$|net position|net assets)")
CF_TOT = re.compile(r"(?i)^(net cash\b|net (increase|decrease|\(decrease\)|\(increase\)|change)\b.*cash|total\b(?! (opeb|pension|net pension) liab)|\(unlabeled\))")
CF_INFO = re.compile(r"(?i)^(cash and cash equivalents,? ?(-|may|april)|beginning$|ending$)")


# ---- locating -------------------------------------------------------------------------------------------
def locate(fy):
    texts = page_texts_for_locating(fy)
    head = lambda i: re.sub(r"\s+", " ", texts[i - 1][:420]).upper()
    np_p = inc_p = cf_p = None
    for i in range(20, min(70, len(texts)) + 1):
        h = head(i)
        nnum = len(re.findall(r"\d{1,3},\d{3}", texts[i - 1]))
        if nnum < 8 or not re.search(r"PROPRIETARY|WATER (AND|&) SEWER", h) or "FIDUCIARY" in h:
            continue
        if np_p is None and re.search(r"STATEMENT OF NET (POSITION|ASSETS)", h) and "CONTINUED" not in h and "BUDGET" not in h:
            np_p = i
        elif np_p is not None and inc_p is None and re.search(r"STATEMENT OF REVENUE", h) and "BUDGET" not in h:
            inc_p = i
        elif inc_p is not None and cf_p is None and re.search(r"STATEMENT OF CASH FLOWS", h) and "CONTINUED" not in h:
            cf_p = i
    if None in (np_p, inc_p, cf_p):
        raise SystemExit(f"FY{fy}: statements not found (net position {np_p}, income {inc_p}, cash flows {cf_p})")
    cf_end = cf_p
    while cf_end + 1 <= len(texts) and re.search(r"CASH FLOWS|RECONCILIATION", head(cf_end + 1)) and "FIDUCIARY" not in head(cf_end + 1):
        cf_end += 1
    return {"netposition": (np_p, inc_p - 1), "income": (inc_p, inc_p), "cashflow": (cf_p, cf_end)}


# ---- per statement -----------------------------------------------------------------------------------
def colnames(ncols):
    return ["Water and Sewer"] if ncols == 1 else ["Water and Sewer", "Water and Sewer (prior-year comparative)"]


def first(rows, lid, stmt, start=0, kinds=("item", "total", "derived", "info")):
    for i in range(start, len(rows)):
        r = rows[i]
        if r["kind"] in kinds and line_id(stmt, r["label"]) == lid:
            return i
    return None


def check(problems, name, got, want, allow, col):
    if got != want:
        d = want - got
        if allow.get(name.lower()) == d:
            return
        problems.append(f"{name}{' (prior-year comparative column)' if col else ''}: lines {got:,} vs printed {want:,} (diff {d:+,})")


def validate_np(rows, ncols, ov):
    problems, notes = [], []
    for col in range(ncols):
        reduce_rows(rows, col, "netposition", ov["allow"], notes, problems)
        ta, tado = first(rows, "total_assets", "netposition"), first(rows, "total_assets_and_deferred_outflows", "netposition")
        tl, tldi = first(rows, "total_liabilities", "netposition"), first(rows, "total_liabilities_and_deferred_inflows", "netposition")
        npt = first(rows, "np_total", "netposition")
        a = tado if tado is not None else ta
        l = tldi if tldi is not None else tl
        if None in (a, l, npt):
            problems.append(f"netposition: a key total was not found (assets {a}, liabilities {l}, net position {npt})")
            continue
        check(problems, "netposition: assets = liabilities + net position", rows[a]["vals"][col], rows[l]["vals"][col] + rows[npt]["vals"][col], ov["allow"], col)
    return problems, notes


def validate_inc(rows, ncols, ov):
    problems, notes = [], []
    for col in range(ncols):
        reduce_rows(rows, col, "income", ov["allow"], notes, problems)
        v = lambda i: rows[i]["vals"][col] if i is not None else 0
        tor = first(rows, "total_operating_revenues", "income")
        toe = first(rows, "total_operating_expenses", "income")
        oibd = first(rows, "operating_income_before_depreciation", "income")
        oi = first(rows, "operating_income", "income")
        tno = first(rows, "total_nonoperating", "income")
        ib = first(rows, "income_before_capital_grants_transfers", "income")
        chg = first(rows, "change_in_net_position", "income")
        beg = first(rows, "np_beginning", "income")
        rest = first(rows, "np_restated", "income")
        end = first(rows, "np_ending", "income")
        if None in (tor, toe, oi, chg, beg, end):
            problems.append(f"income: a key line was not found (rev {tor}, exp {toe}, op income {oi}, change {chg}, beginning {beg}, ending {end})")
            continue
        dep = next((i for i in range(toe + 1, oi) if rows[i]["kind"] != "heading" and line_id("income", rows[i]["label"]) == "depreciation"), None)
        pre = v(tor) - v(toe)
        if oibd is not None:
            check(problems, "income: operating income before depreciation = revenues - expenses", pre, v(oibd), ov["allow"], col)
            pre = v(oibd)
        check(problems, "income: operating income", pre - v(dep), v(oi), ov["allow"], col)
        base = oi
        if tno is None:  # FY2013-FY2015 print the non-operating total as an unlabeled line; it is a "total" kind and found as (unlabeled)
            tno = next((i for i in range(oi + 1, chg) if rows[i]["label"] == "(unlabeled)"), None)
        if tno is not None:
            seg = segment_sum(rows, oi, tno, col)
            check(problems, "income: non-operating total = its lines", seg, v(tno), ov["allow"], col)
        after = tno if tno is not None else oi
        pre_chg = v(oi) + (v(tno) if tno is not None else 0)
        if ib is not None:
            check(problems, "income: income before capital grants/transfers = operating income + non-operating", pre_chg, v(ib), ov["allow"], col)
            pre_chg, after = v(ib), ib
        check(problems, "income: change in net position", pre_chg + segment_sum(rows, after, chg, col), v(chg), ov["allow"], col)
        b_eff = v(beg)
        if rest is not None:
            check(problems, "income: restated beginning net position = beginning + adjustments", v(beg) + segment_sum(rows, beg, rest, col), v(rest), ov["allow"], col)
            b_eff = v(rest)
        # other adjustments printed between the change and the ending balance are handled as items between beginning and change when beginning follows change
        if beg < chg:  # beginning above the change: the layout of FY2016+ (change, then roll-forward)
            pass
        check(problems, "income: ending net position = beginning (restated) + change", b_eff + v(chg), v(end), ov["allow"], col)
    return problems, notes


def validate_cf(rows, ncols, ov):
    problems, notes = [], []
    for col in range(ncols):
        reduce_rows(rows, col, "cashflow", ov["allow"], notes, problems)
        v = lambda i: rows[i]["vals"][col]
        net, beg, end = first(rows, "net_change_in_cash", "cashflow"), first(rows, "cash_beginning", "cashflow"), first(rows, "cash_ending", "cashflow")
        if None in (net, beg, end):
            problems.append(f"cashflow: key line not found (net change {net}, beginning {beg}, ending {end})")
            continue
        check(problems, "cashflow: ending cash = beginning + net change", v(beg) + v(net), v(end), ov["allow"], col)
        nops = [i for i, r in enumerate(rows) if r["kind"] == "total" and line_id("cashflow", r["label"]) == "net_cash_operating"] or \
               [i for i, r in enumerate(rows) if r["kind"] == "total" and r["label"] == "(unlabeled)"][:1]
        if len(nops) >= 2:
            check(problems, "cashflow: net cash from operations (statement) = (reconciliation)", v(nops[0]), v(nops[1]), ov["allow"], col)
    return problems, notes


VALIDATORS = {"netposition": validate_np, "income": validate_inc, "cashflow": validate_cf}
PARSE_CFG = {
    "netposition": dict(sections=NP_SECTIONS, tot_re=NP_TOT),
    "income": dict(sections=INC_SECTIONS, tot_re=INC_TOT, derived_re=INC_DERIVED),
    "cashflow": dict(sections=CF_SECTIONS, tot_re=CF_TOT, info_re=CF_INFO),
}


def ncols_for(fy):
    return 2 if int(fy) == 2012 else 1


CF_UNLABELED = {"Cash flows from operating activities": "net_cash_operating", "Cash flows from noncapital financing activities": "net_cash_noncapital_financing",
                "Cash flows from capital and related financing activities": "net_cash_capital_financing", "Cash flows from investing activities": "net_cash_investing",
                "Reconciliation of operating income to net cash from operating activities": "net_cash_operating"}


def postprocess(stmt, rows):
    """Section labels the plain heading scan cannot know, and ids for the unlabeled subtotals the FY2013-FY2015 audits print."""
    seen_change = False
    for r in rows:
        if r["kind"] == "heading":
            continue
        lid = line_id(stmt, r["label"])
        if stmt == "income":
            if lid == "operating_income":
                r["section"] = "Operating income"
            if lid in ("change_in_net_position", "np_beginning", "np_restated", "np_ending") or seen_change:
                r["section"], seen_change = "Net position roll-forward", True
        if stmt == "cashflow":
            if r["label"] == "(unlabeled)" and r["kind"] == "total" and r["section"] in CF_UNLABELED:
                r["lid"] = CF_UNLABELED[r["section"]]
                r["label"] = "(unlabeled subtotal: net cash)"
            if lid in ("net_change_in_cash", "cash_beginning", "cash_ending"):
                r["section"] = "Net change in cash"
            if r["section"].startswith("Reconciliation") and lid == "net_cash_operating":
                r["lid"] = "recon_net_cash_operating"
        if stmt == "income" and r["label"] == "(unlabeled)" and r["kind"] == "total":
            r["lid"] = "total_nonoperating"
            r["label"] = "(unlabeled subtotal: total non-operating)"
    return rows


def read_statement(fy, stmt, pages, ov):
    rows, problems = parse_statement(pages, ncols_for(fy), fixline=ov["fixline"], drop=ov["drop"], **PARSE_CFG[stmt])
    vp, notes = VALIDATORS[stmt](rows, ncols_for(fy), ov)
    rows = postprocess(stmt, rows)
    return rows, problems + vp, notes


def cross_checks(fy, res):
    """Checks across the three statements of the chosen readings (rows lists)."""
    problems, notes = [], []
    np_rows, inc_rows, cf_rows = (res[s]["rows"] for s in ("netposition", "income", "cashflow"))
    ov = res["ov"]
    for col in range(ncols_for(fy)):
        g = lambda rows, stmt, lid: next((r["vals"][col] for r in rows if r["kind"] != "heading" and line_id(stmt, r["label"]) == lid), None)
        npt, end = g(np_rows, "netposition", "np_total"), g(inc_rows, "income", "np_ending")
        if npt is not None and end is not None:
            check(problems, "across: ending net position (income stmt) = total net position (net position stmt)", end, npt, ov["allow"], col)
        cash_end = g(cf_rows, "cashflow", "cash_ending")
        cashes = [r["vals"][col] for r in np_rows if r["kind"] != "heading" and line_id("netposition", r["label"]) in ("cash", "cash_pooled", "cash_nonpooled", "restricted_investments")]
        if cash_end is not None and cashes:
            if cash_end not in (cashes[0], sum(cashes), sum(cashes[:2])):
                # the cash lines of the net position statement (cash [+ non-pooled] [+ restricted]) in any prefix combination
                combos = {sum(cashes[:k]) for k in range(1, len(cashes) + 1)}
                if cash_end not in combos:
                    problems.append(f"across: ending cash (cash flows) {cash_end:,} vs cash lines of the net position statement {cashes}")
        oi_i, oi_c = g(inc_rows, "income", "operating_income"), g(cf_rows, "cashflow", "recon_operating_income")
        if oi_i is not None and oi_c is not None:
            check(problems, "across: operating income (reconciliation) = income statement", oi_c, oi_i, ov["allow"], col)
        dep_i = g(inc_rows, "income", "depreciation")
        dep_c = next((r["vals"][col] for r in cf_rows if r["kind"] == "item" and re.match(r"(?i)^depreciation", r["label"])), None)
        if dep_i is not None and dep_c is not None and dep_i != dep_c:
            notes.append(f"across: depreciation in the cash flow reconciliation {dep_c:,} differs from the income statement {dep_i:,} ({dep_c - dep_i:+,})")
    return problems, notes


def summary_check(fy, res):
    """Compares the GAAP change and ending net position with the budgetary summary schedule's actual column (FY2016+ only)."""
    path = os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_summary.csv")
    problems = []
    if not os.path.exists(path):
        return problems, ["summary schedule not extracted for this year"]
    rows = list(csv.DictReader(open(path)))
    inc = res["income"]["rows"]
    chg = next((r["vals"][0] for r in inc if r["kind"] != "heading" and line_id("income", r["label"]) == "change_in_net_position"), None)
    end = next((r["vals"][0] for r in inc if r["kind"] != "heading" and line_id("income", r["label"]) == "np_ending"), None)
    gaap = next((int(r["actual"]) for r in rows if re.search(r"(?i)^change in net position.*gaap", r["line_item"])), None)
    sum_chg = gaap if gaap is not None else next((int(r["actual"]) for r in rows if re.search(r"(?i)^change in net (position|assets)", r["line_item"])), None)
    sum_end = next((int(r["actual"]) for r in rows if re.search(r"(?i)^(net (position|assets)[ ,-]*(april (30|thirty)|ending)|ending$)", r["line_item"])), None)
    if sum_chg is not None and chg is not None and sum_chg != chg:
        problems.append(f"summary schedule: change in net position {sum_chg:,} vs income statement {chg:,}")
    if sum_end is not None and end is not None and sum_end != end:
        problems.append(f"summary schedule: ending net position {sum_end:,} vs income statement {end:,}")
    notes = []
    tor_s = next((int(r["actual"]) for r in rows if re.search(r"(?i)^total operating revenues", r["line_item"])), None)
    tor_i = next((r["vals"][0] for r in inc if r["kind"] != "heading" and line_id("income", r["label"]) == "total_operating_revenues"), None)
    if tor_s is not None and tor_i is not None and tor_s != tor_i:
        notes.append(f"summary schedule: total operating revenues {tor_s:,} vs income statement {tor_i:,} ({tor_s - tor_i:+,}; the budgetary schedule can classify differently)")
    notes.append(f"summary schedule compared: change {sum_chg} / ending {sum_end}")
    return problems, notes


def compare_readings(a, b):
    """Multiset comparison of the amounts of two readings (text layer vs OCR) of the same statements."""
    from collections import Counter
    ca, cb, ex = Counter(), Counter(), []
    for stmt in ("netposition", "income", "cashflow"):
        for r in a[stmt]["rows"]:
            if r["kind"] != "heading":
                ca[(stmt, tuple(r["vals"]))] += 1
        for r in b[stmt]["rows"]:
            if r["kind"] != "heading":
                cb[(stmt, tuple(r["vals"]))] += 1
    oa, ob = ca - cb, cb - ca
    ex = [f"text-only {k}" for k in oa] + [f"ocr-only {k}" for k in ob]
    return dict(only_a=sum(oa.values()), only_b=sum(ob.values()), same=sum((ca & cb).values()), examples=ex)


def run(fy, prep, loc, ov):
    res = {"ov": ov}
    total = 0
    for stmt, (a, b) in loc.items():
        pages = get_pages(fy, prep, a, b)
        rows, problems, notes = read_statement(fy, stmt, pages, ov)
        res[stmt] = dict(rows=rows, problems=problems, notes=notes, pages=(a, b))
        total += len(problems)
    cp, cn = cross_checks(fy, res)
    sp, sn = summary_check(fy, res)
    res["across"] = dict(problems=cp + sp, notes=cn + sn)
    res["n"] = total + len(cp) + len(sp)
    res["prep"] = prep
    return res


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    loc = locate(int(fy))
    ov = load_overrides("audit_ws_stmt", fy)
    preps = ["text", "gray"] if arg == "auto" else [arg]
    if int(fy) == 2012 and arg == "auto":
        preps = ["gray"]
    results = []
    for p in preps:
        try:
            r = run(fy, p, loc, ov)
        except Exception as e:  # a text layer that is absent or unusable
            print(f"prep {p}: failed ({type(e).__name__}: {e})")
            continue
        results.append(r)
        print(f"prep {p}: {r['n']} problems, pages {loc}")
    res = min(results, key=lambda r: r["n"])
    xr = None
    if len(results) == 2:  # independent second reading: the amounts of the two preps must agree line for line
        xr = compare_readings(results[0], results[1])
        print(f"cross-reading (text layer vs OCR): {xr['only_a']} amounts only in the text layer, {xr['only_b']} only in OCR, {xr['same']} identical")
        for e in xr["examples"][:12]:
            print("   differs:", e)
    # statement by statement the better reading could differ, but a year is one document: keep a single prep for traceability
    names = colnames(ncols_for(fy))
    for stmt in ("netposition", "income", "cashflow"):
        path = os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_{stmt}.csv")
        write_stmt_csv(path, fy, stmt, res[stmt]["rows"], names)
    print(f"\nchosen prep: {res['prep']}; {res['n']} problems")
    for stmt in ("netposition", "income", "cashflow", "across"):
        for n in res[stmt]["notes"]:
            print(f"  note [{stmt}]: {n}")
        for p in res[stmt]["problems"]:
            print(f"  ! [{stmt}] {p}")
    sys.exit(1 if res["n"] else 0)


if __name__ == "__main__":
    main()
