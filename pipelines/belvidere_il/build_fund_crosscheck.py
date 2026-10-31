"""Cross-check three budgeted funds against the audit by a route other than the fund totals: the 1990 / 1993 TIF funds (budget #12 / #14) and Capital Projects (#41).

Usage: python3 pipelines/belvidere_il/build_fund_crosscheck.py   (needs the extracts; no other builder first)

Inputs : data/belvidere_il/extracted/FY20XX_budget_funds_{revenue,expense,totals}.csv   the budget lines and cash lines of each fund page
         data/belvidere_il/extracted/audits/FY20XX_audit_fund_schedules.csv             the audit's budget-and-actual schedule per fund
Output : data/belvidere_il/fund_crosscheck.csv   one row per fund x fiscal year x measure x basis, audit value beside budget value

Budget side: revenue lines without the transfer-in account (object 9998), the transfer-in lines, expense lines without the transfer-out account
(9999) and the cash lines (the two Capital Projects ledgers are added together). A budget document prints these columns: its own year (adopted),
the prior year's budget (prior_doc_budget, used only for years before the first document) and the actual of two years earlier (actual); the
revised-budget column is not compared. Audit side: original budget (vs adopted / prior_doc_budget) and actual. `revenues_and_transfers_in` adds
the two because the city's actual columns book some grants as transfers in. `status` is tie (|diff| <= 1, the audits round), differs, or no_audit /
no_budget when one side does not have the measure. Cash lines are compared with the audit's fund balance (`beginning_balance`, `ending_balance`):
cash and fund balance are different things (receivables, payables), so a difference there is expected and not an error.
Line-level TIF measures (`prop_tax`, `interest`, `misc_revenue`, `tax_distributions`, `contractual`, `misc_expense`, `bond_interest`) use the
audit line label and the budget account's object code.
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402

E = Entity.load("belvidere_il")
X = E.data_path("extracted")
COLUMNS = ["entity_id", "fund_code", "audit_fund", "fiscal_year", "measure", "basis", "audit_value", "budget_value", "diff", "status", "budget_document"]

# fund code -> (budget heading marker, audit fund name)
FUNDS = {"12": ("#12", "1990 TIF"), "14": ("#14", "1993 TIF"), "41": ("#41", "Capital Projects")}
# line-level measures: object code -> (measure, audit label)
TIF_LINES = {
    "12": {"4010": ("prop_tax", "property"), "4600": ("interest", "interest"), "4900": ("misc_revenue", "miscellaneous"),
           "7930": ("tax_distributions", "tax distributions"), "6890": ("contractual", "contractual services"), "7900": ("misc_expense", "miscellaneous")},
    "14": {"4010": ("prop_tax", "property"), "8920": ("bond_interest", "interest and fiscal charges")},
}
AUDIT_COL = {"adopted": "original_budget", "prior_doc_budget": "original_budget", "actual": "actual"}


def n(v):
    return int(v) if v not in ("", None) else None


def column_basis(col, fy):
    """Budget extract column name (in document `fy`) -> (period_fy, basis) or None."""
    m = re.fullmatch(r"(budget|budget_revised|actual)_FY(\d{4})", col)
    if not m:
        return None
    kind, y = m.group(1), int(m.group(2))
    if kind == "actual":
        return y, "actual"
    if kind == "budget_revised":
        return None  # the revised column is a mid-year estimate printed in the next document, not the audit's final budget: not compared
    return (y, "adopted") if y == fy else (y, "prior_doc_budget")


def budget_side(code):
    marker = FUNDS[code][0]
    out = defaultdict(lambda: defaultdict(int))  # (fy, basis) -> measure -> amount
    docs = {}
    for fy in range(2015, 2028):
        for kind in ("revenue", "expense", "totals"):
            path = os.path.join(X, f"FY{fy}_budget_funds_{kind}.csv")
            if not os.path.exists(path):
                continue
            for r in csv.DictReader(open(path)):
                if marker not in r["fund"]:
                    continue
                cb = column_basis(r["column"], fy)
                if cb is None:
                    continue
                k = cb
                # a basis other than actual/revised appears in several documents (adopted once, prior_doc_budget once): keep each document apart
                key = (cb[0], cb[1], fy)
                a = int(r["amount"])
                m = out[key]
                docs[key] = f"FY{fy}_budget"
                if kind == "totals":
                    if r["kind"] == "beginning_cash":
                        m["beginning_balance"] += a
                    elif r["kind"] == "ending_cash":
                        m["ending_balance"] += a
                    continue
                obj = r["account"].split("-")[-1]
                if kind == "revenue":
                    if obj == "9998":
                        m["transfers_in"] += a
                    else:
                        m["revenues"] += a
                else:
                    if obj == "9999":
                        m["transfers_out"] += a
                    else:
                        m["expenditures"] += a
                if kind == "revenue":
                    m["revenues_and_transfers_in"] += a
                if code in TIF_LINES and obj in TIF_LINES[code]:
                    m[TIF_LINES[code][obj][0]] += a
                if code == "41" and obj == "4010":
                    m["prop_tax"] += a
    return out, docs


def audit_side(code):
    name = FUNDS[code][1]
    out = defaultdict(dict)  # fy -> measure -> {col: value}
    for p in sorted(glob.glob(os.path.join(X, "audits", "FY*_audit_fund_schedules.csv"))):
        for r in csv.DictReader(open(p)):
            if r["fund"] != name:
                continue
            fy, k, lab = int(r["fiscal_year"]), r["line_kind"], r["line_item"].strip().lower()
            vals = {"original_budget": n(r["original_budget"]), "final_budget": n(r["final_budget"]), "actual": n(r["actual"])}
            d = out[fy]
            if k == "total_revenues":
                d["revenues"] = vals
            elif k == "total_expenditures":
                d["expenditures"] = vals
            elif k == "other_financing" and lab.startswith("transfer") and "out" not in lab:
                cur = d.setdefault("transfers_in", {"original_budget": 0, "final_budget": 0, "actual": 0})
                for c, v in vals.items():
                    cur[c] = (cur[c] or 0) + (v or 0)
            elif k == "other_financing" and lab.startswith("transfer") and "out" in lab:
                cur = d.setdefault("transfers_out", {"original_budget": 0, "final_budget": 0, "actual": 0})
                for c, v in vals.items():
                    cur[c] = (cur[c] or 0) - (v or 0)
            elif k == "fb_begin":
                d["beginning_balance"] = {"actual": vals["actual"]}
            elif k == "fb_end":
                d["ending_balance"] = {"actual": vals["actual"]}
            elif k in ("revenue", "expenditure"):
                for obj, (measure, label) in TIF_LINES.get(code, {}).items():
                    if lab == label and (measure in ("prop_tax", "interest", "misc_revenue")) == (k == "revenue"):
                        d[measure] = vals
                if code == "41" and k == "revenue" and lab.startswith("property"):
                    d["prop_tax"] = vals
    for d in out.values():
        if "revenues" in d:
            d["revenues_and_transfers_in"] = {c: (d["revenues"][c] or 0) + (d.get("transfers_in", {}).get(c) or 0) for c in d["revenues"]}
    return out


def main():
    rows = []
    for code, (marker, aname) in FUNDS.items():
        bud, docs = budget_side(code)
        aud = audit_side(code)
        seen = set()
        for (fy, basis, docfy), measures in sorted(bud.items()):
            # the same (year, basis) can come from two documents (an actual from the next-but-one document only; revised only once);
            # prior_doc_budget only where no adopted document exists for the year
            if basis == "prior_doc_budget" and fy >= 2015:
                continue
            if (fy, basis) in seen:
                continue
            seen.add((fy, basis))
            for measure in sorted(set(measures) | set(aud.get(fy, {}))):
                if measure == "transfers_out" and basis != "actual" and measure not in measures:
                    continue
                a = aud.get(fy, {}).get(measure)
                av = a.get(AUDIT_COL[basis]) if a else None
                if measure in ("beginning_balance", "ending_balance") and basis != "actual":
                    continue
                bv = measures.get(measure) if measure in measures or measure in ("revenues", "expenditures") else None
                if av is None and bv is None:
                    continue
                if av is None:
                    status = "no_audit"
                elif bv is None:
                    status = "no_budget"
                else:
                    status = "tie" if abs(av - bv) <= 1 else "differs"
                rows.append(dict(entity_id=E.id, fund_code=code, audit_fund=aname, fiscal_year=fy, measure=measure, basis=basis,
                                 audit_value="" if av is None else av, budget_value="" if bv is None else bv,
                                 diff="" if av is None or bv is None else av - bv, status=status, budget_document=docs[(fy, basis, docfy)]))
    rows.sort(key=lambda r: (r["fund_code"], int(r["fiscal_year"]), r["measure"], r["basis"]))
    write_csv(E.data_path("fund_crosscheck.csv"), COLUMNS, rows)
    for code in FUNDS:
        sub = [r for r in rows if r["fund_code"] == code and r["status"] in ("tie", "differs")]
        print(f"fund {code} ({FUNDS[code][1]}): {len(sub)} comparisons, {sum(r['status'] == 'tie' for r in sub)} tie")
        for r in sub:
            if r["status"] == "differs":
                print(f"   FY{r['fiscal_year']} {r['measure']:18} {r['basis']:16} audit {r['audit_value']!s:>9} budget {r['budget_value']!s:>9} diff {r['diff']}")


if __name__ == "__main__":
    main()
