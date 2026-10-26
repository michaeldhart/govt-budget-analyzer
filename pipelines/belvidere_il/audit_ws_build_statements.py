"""Combine the per-year Water and Sewer audit statement extracts into canonical-style tables and run the cross-year checks.

Usage: python3 pipelines/belvidere_il/audit_ws_build_statements.py [--no-revalidate]

Reads  data/belvidere_il/extracted/audits/FY20XX_audit_ws_{netposition,income,cashflow}.csv (FY2012-FY2026) written by
       extract_audit_ws_statements.py (and, for the schedules, FY20XX_audit_ws_summary.csv / _operating_expense.csv).
Writes (new files only; nothing existing is touched):
  data/belvidere_il/ws_audit_statements.csv           long format, one row per statement line x year, columns as fund_statements.csv:
                                                      entity_id, doc_id, period_fy, statement, fund, section, subsection, line_item, line_id, amount, pdf_page, flag
                                                      statement = ws_stmt_net_position | ws_stmt_income | ws_stmt_cash_flows; the FY2012 audit's April 30, 2011
                                                      comparative column is kept as period_fy 2011 (doc FY2012_audit, flag prior_year_comparative)
  data/belvidere_il/ws_audit_statement_summary.csv    one row per fiscal year (FY2011 comparative-only, FY2012-FY2026) with the headline lines and the checks
  data/belvidere_il/ws_audit_statements_validation.csv  one row per year x statement: prep, lines, totals checked, problems, cross-reading result
Cross-year checks (in the summary's check columns and printed): beginning net position = prior year's ending (or the printed restatement);
beginning cash = prior year's ending cash (FY2013 restates FY2012's by adding investments: see the printed notes).
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_ws_common import AUDIT_OUT, DATA_E, line_id  # noqa: E402,F401
import extract_audit_ws_statements as S  # noqa: E402

OUT_LONG = os.path.join(DATA_E, "ws_audit_statements.csv")
OUT_SUM = os.path.join(DATA_E, "ws_audit_statement_summary.csv")
OUT_VAL = os.path.join(DATA_E, "ws_audit_statements_validation.csv")
STMT = {"netposition": "ws_stmt_net_position", "income": "ws_stmt_income", "cashflow": "ws_stmt_cash_flows"}
YEARS = range(2012, 2027)


def read_year(fy):
    out = {}
    for st in STMT:
        out[st] = list(csv.DictReader(open(os.path.join(AUDIT_OUT, f"FY{fy}_audit_ws_{st}.csv"))))
    return out


def write_long(data):
    with open(OUT_LONG, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "doc_id", "period_fy", "statement", "fund", "section", "subsection", "line_item", "line_id", "amount", "pdf_page", "flag"])
        for fy in YEARS:
            for st, rows in data[fy].items():
                for r in rows:
                    prior = "comparative" in r["column"]
                    w.writerow(["belvidere_il", f"FY{fy}_audit", fy - 1 if prior else fy, STMT[st], "Water and Sewer", r["section"], r["subsection"], r["line_item"], r["line_id"],
                                r["amount"], r["pdf_page"], "prior_year_comparative" if prior else r["flag"]])


def val(rows, lid, column="Water and Sewer", nth=0):
    got = [int(r["amount"]) for r in rows if r["line_id"] == lid and r["column"] == column]
    return got[nth] if len(got) > nth else None


def summary_rows(data):
    """One dict per fiscal year (and FY2011 from the FY2012 comparative column)."""
    out = {}
    cols = [(fy, "Water and Sewer", data[fy]) for fy in YEARS] + [(2011, "Water and Sewer (prior-year comparative)", data[2012])]
    for fy, col, d in cols:
        n, i, c = d["netposition"], d["income"], d["cashflow"]
        g = lambda rows, lid, nth=0: val(rows, lid, col, nth)
        cash_lines = [int(r["amount"]) for r in n if r["column"] == col and r["line_id"] in ("cash", "cash_pooled", "cash_nonpooled")]
        restricted = g(n, "restricted_investments")
        row = dict(fiscal_year=fy, source_doc=f"FY{2012 if fy == 2011 else fy}_audit")
        row["cash_and_investments"] = sum(cash_lines) if cash_lines else None
        inv = g(n, "cash_investments_fy2012")
        row["cash_and_investments_comparable"] = (sum(cash_lines) + (inv or 0)) if cash_lines else None  # FY2012 prints pooled / non-pooled cash and a separate Investments line
        row["restricted_investments"] = restricted
        row["total_current_assets"] = g(n, "total_current_assets")
        row["total_assets"] = g(n, "total_assets")
        row["total_deferred_outflows"] = g(n, "total_deferred_outflows")
        row["total_current_liabilities"] = g(n, "total_current_liabilities")
        row["total_liabilities"] = g(n, "total_liabilities")
        row["total_deferred_inflows"] = g(n, "total_deferred_inflows")
        row["net_position_net_investment_in_capital"] = g(n, "np_net_investment_in_capital")
        row["net_position_unrestricted"] = g(n, "np_unrestricted")
        row["net_position_total"] = g(n, "np_total")
        if None not in (row["net_position_total"], row["net_position_net_investment_in_capital"], row["net_position_unrestricted"]):
            row["net_position_restricted"] = row["net_position_total"] - row["net_position_net_investment_in_capital"] - row["net_position_unrestricted"]
        row["operating_revenues"] = g(i, "total_operating_revenues")
        row["operating_expenses_incl_depreciation"] = None
        toe, dep = g(i, "total_operating_expenses"), g(i, "depreciation")
        if toe is not None:
            row["operating_expenses_incl_depreciation"] = toe if fy <= 2015 else toe + (dep or 0)
        row["depreciation"] = dep
        row["operating_income"] = g(i, "operating_income")
        row["change_in_net_position"] = g(i, "change_in_net_position")
        row["net_position_beginning"] = g(i, "np_beginning")
        row["net_position_restated_beginning"] = g(i, "np_restated")
        row["net_position_ending"] = g(i, "np_ending")
        row["cash_flow_operating"] = g(c, "net_cash_operating")
        row["cash_flow_noncapital_financing"] = g(c, "net_cash_noncapital_financing")
        row["cash_flow_capital_financing"] = g(c, "net_cash_capital_financing")
        row["cash_flow_investing"] = g(c, "net_cash_investing")
        row["net_change_in_cash"] = g(c, "net_change_in_cash")
        row["cash_beginning"] = g(c, "cash_beginning")
        row["cash_ending"] = g(c, "cash_ending")
        row["purchases_of_capital_assets"] = g(c, "purchases_of_capital_assets")
        row["operating_cash_flow_less_capital_purchases"] = (row["cash_flow_operating"] + row["purchases_of_capital_assets"]) if None not in (row["cash_flow_operating"], row["purchases_of_capital_assets"]) else None
        out[fy] = row
    return out


def checks(rows):
    notes = []
    for fy in sorted(rows):
        r = rows[fy]
        prev = rows.get(fy - 1)
        r["check_assets_equal_liabilities_plus_net_position"] = ""
        if None not in (r["total_assets"], r["total_liabilities"], r["net_position_total"]):
            lhs = r["total_assets"] + (r["total_deferred_outflows"] or 0)
            rhs = r["total_liabilities"] + (r["total_deferred_inflows"] or 0) + r["net_position_total"]
            r["check_assets_equal_liabilities_plus_net_position"] = "ok" if lhs == rhs else f"diff {lhs - rhs:+,}"
        r["check_net_position_rollforward"] = ""
        if None not in (r["net_position_beginning"], r["change_in_net_position"], r["net_position_ending"]):
            b = r["net_position_restated_beginning"] if r["net_position_restated_beginning"] is not None else r["net_position_beginning"]
            r["check_net_position_rollforward"] = "ok" if b + r["change_in_net_position"] == r["net_position_ending"] else "diff"
        r["check_beginning_net_position_equals_prior_ending"] = ""
        if prev and r["net_position_beginning"] is not None and prev["net_position_ending"] is not None:
            ok = r["net_position_beginning"] == prev["net_position_ending"]
            r["check_beginning_net_position_equals_prior_ending"] = "ok" if ok else f"differs by {r['net_position_beginning'] - prev['net_position_ending']:+,}"
            if not ok:
                notes.append(f"FY{fy}: beginning net position {r['net_position_beginning']:,} vs FY{fy - 1} ending {prev['net_position_ending']:,}")
        r["check_cash_rollforward"] = ""
        if None not in (r["cash_beginning"], r["net_change_in_cash"], r["cash_ending"]):
            r["check_cash_rollforward"] = "ok" if r["cash_beginning"] + r["net_change_in_cash"] == r["cash_ending"] else "diff"
        r["check_beginning_cash_equals_prior_ending"] = ""
        if prev and r["cash_beginning"] is not None and prev["cash_ending"] is not None:
            ok = r["cash_beginning"] == prev["cash_ending"]
            r["check_beginning_cash_equals_prior_ending"] = "ok" if ok else f"differs by {r['cash_beginning'] - prev['cash_ending']:+,}"
            if not ok:
                notes.append(f"FY{fy}: beginning cash {r['cash_beginning']:,} vs FY{fy - 1} ending cash {prev['cash_ending']:,}")
        r["check_cash_ending_equals_net_position_cash"] = ""
        if r["cash_ending"] is not None and r["cash_and_investments"] is not None:
            opts = {r["cash_and_investments"], r["cash_and_investments"] + (r["restricted_investments"] or 0)}
            if r["fiscal_year"] > 2012:
                opts = {r["cash_and_investments"], r["cash_and_investments"] + (r["restricted_investments"] or 0)}
            r["check_cash_ending_equals_net_position_cash"] = "ok" if r["cash_ending"] in opts else f"differs by {r['cash_ending'] - r['cash_and_investments']:+,}"
        r["check_cash_flow_sections_sum_to_net_change"] = ""
        parts = [r["cash_flow_operating"], r["cash_flow_noncapital_financing"], r["cash_flow_capital_financing"], r["cash_flow_investing"]]
        if r["net_change_in_cash"] is not None and r["cash_flow_operating"] is not None:
            r["check_cash_flow_sections_sum_to_net_change"] = "ok" if sum(p or 0 for p in parts) == r["net_change_in_cash"] else "diff (a section without its own subtotal line?)"
    return notes


def validation_table(data):
    """Re-runs the extractors' validators (text layer for FY2016+, OCR for FY2012-FY2015, as chosen) and records the results."""
    rows = []
    for fy in YEARS:
        loc = S.locate(fy)
        ov = S.load_overrides("audit_ws_stmt", str(fy))
        results = {}
        for prep in (["gray"] if fy <= 2012 else ["text", "gray"]):
            try:
                results[prep] = S.run(str(fy), prep, loc, ov)
            except Exception as e:  # noqa: BLE001
                results[prep] = None
                print(f"FY{fy} {prep}: {type(e).__name__}: {e}")
        chosen = "gray" if fy <= 2015 else "text"
        res = results[chosen]
        xr = S.compare_readings(results["text"], results["gray"]) if "text" in results and results.get("text") and results.get("gray") else None
        for st in STMT:
            r = res[st]
            n_tot = sum(1 for x in r["rows"] if x["kind"] == "total")
            rows.append(dict(fiscal_year=fy, statement=STMT[st], pdf_pages=f"{r['pages'][0]}-{r['pages'][1]}", reading=chosen, lines=sum(1 for x in r["rows"] if x["kind"] != "heading"),
                             totals_checked=n_tot, problems=len(r["problems"]), notes=len(r["notes"]),
                             overrides=len([k for k in ov["fixline"]]) if st == "income" else "",
                             second_reading=("OCR" if chosen == "text" else "none (text layer unusable)") if xr or chosen == "gray" else "",
                             second_reading_amounts_agree=("" if xr is None else (xr["same"] if not (xr["only_a"] or xr["only_b"]) else f"{xr['only_a']} differ")),
                             second_reading_ocr_problems=(results["gray"]["n"] if chosen == "text" and results.get("gray") else "")))
        rows.append(dict(fiscal_year=fy, statement="across statements", pdf_pages="", reading=chosen, lines="", totals_checked="", problems=len(res["across"]["problems"]), notes=len(res["across"]["notes"])))
    return rows


def main():
    data = {fy: read_year(fy) for fy in YEARS}
    write_long(data)
    summ = summary_rows(data)
    notes = checks(summ)
    cols = list(summ[2012].keys())
    for r in summ.values():
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(OUT_SUM, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for fy in sorted(summ):
            w.writerow({k: ("" if summ[fy].get(k) is None else summ[fy].get(k)) for k in cols})
    print(f"wrote {os.path.relpath(OUT_LONG, DATA_E + '/..')}, {os.path.relpath(OUT_SUM, DATA_E + '/..')}")
    for n in notes:
        print("  note:", n)
    bad = [(fy, k, v) for fy, r in summ.items() for k, v in r.items() if k.startswith("check_") and v not in ("", "ok")]
    for b in bad:
        print("  check:", b)
    if "--no-revalidate" not in sys.argv:
        vt = validation_table(data)
        with open(OUT_VAL, "w", newline="") as f:
            fields = ["fiscal_year", "statement", "pdf_pages", "reading", "lines", "totals_checked", "problems", "notes", "overrides", "second_reading", "second_reading_amounts_agree", "second_reading_ocr_problems"]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in vt:
                w.writerow({k: r.get(k, "") for k in fields})
        print(f"wrote {os.path.relpath(OUT_VAL, DATA_E + '/..')}; total problems {sum(int(r['problems']) for r in vt)}")


if __name__ == "__main__":
    main()
