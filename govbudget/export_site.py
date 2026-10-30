"""Write the data files the website reads.

Usage: python3 -m govbudget.export_site [entity_id ...] [--out web/public/data]     (default: every entity under entities/)

Reads only the canonical and derived tables in data/<entity_id>/ plus the hand-edited config in entities/<entity_id>/, and writes
static JSON under <out>/:
  index.json                 the entities, with their dataset lists
  palette.json               design tokens (govbudget/palette.json)
  <entity_id>/meta.json      name, fiscal-year rule, document ranges, the vocabulary of bases
  <entity_id>/categories.json  category names, order and colors per scheme (entities/<id>/categories.csv)
  <entity_id>/exceptions.json  the exceptions registry (entities/<id>/exceptions.csv)
  <entity_id>/<dataset>.json   one file per dataset in DATASETS, in the envelope below
  <entity_id>/downloads/       gzipped facts.csv and plain copies of the tables behind each dataset
Dataset envelope: {dataset, entity_id, title, description, sources, columns: {name: meaning}, rows: [{...}]}.
Every row that depends on a line with an exception carries `exception_ids` (a list), so the site can show the caveat beside the number.
A dataset whose source tables an entity does not have is skipped. The numbers are not recomputed here beyond regrouping; the exporter
asserts the identities it relies on (category rows sum to the rollup).
"""
import gzip
import json
import re
import os
import shutil
import sys
from collections import defaultdict

from . import palette, schema
from .entity import ROOT, Entity, read_csv
from .export_common import ids, in_range, num
from .export_park import PARK_DATASETS

DEFAULT_OUT = os.path.join(ROOT, "web", "public", "data")


# --- datasets -------------------------------------------------------------------------------------------------------------
def taxes_by_category(E, read):
    """General Fund gross taxes / offsets / net per fiscal year x measure x category (rollup rows dropped)."""
    ex_of = defaultdict(set)
    for r in read("tax_series_long.csv"):
        for i in ids(r["exception_ids"]):
            ex_of[(int(r["fiscal_year"]), r["measure"], r["category"])].add(i)
    rows, roll, parts = [], {}, defaultdict(lambda: [0, 0, 0])
    for r in read("tax_series_by_category.csv"):
        k = (int(r["fiscal_year"]), r["measure"])
        v = (int(r["gross_tax"]), int(r["offsets"]), int(r["net"]))
        if r["is_rollup"] == "1":
            roll[k] = v
            continue
        for j in range(3):
            parts[k][j] += v[j]
        rows.append(dict(fiscal_year=k[0], measure=r["measure"], basis=r["basis"], category=r["category"], gross_tax=v[0],
                         offsets=v[1], net=v[2], exception_ids=sorted(ex_of[(k[0], r["measure"], r["category"])])))
    for k, v in roll.items():
        assert tuple(parts[k]) == v, f"category rows do not sum to ALL TAXES for {k}: {parts[k]} vs {v}"
    rows.sort(key=lambda r: (r["measure"], r["fiscal_year"], r["category"]))
    return dict(
        title="General Fund taxes by category and year",
        description="Gross taxes, offsets (rebates to developers, replacement tax passed to pensions) and net, per budget category. "
                    "measure says which document column the number comes from; budget_adopted is the primary series.",
        sources=["tax_series_by_category.csv", "tax_series_long.csv"],
        columns=dict(fiscal_year="fiscal year (May-April, named for the year it ends)", measure="series: budget_adopted, budget_next_doc, "
                     "budget_revised, actual_budget_doc", basis="schema basis of the measure", category="budget category (categories.json, "
                     "scheme budget_category)", gross_tax="dollars", offsets="dollars, negative", net="gross_tax + offsets",
                     exception_ids="exceptions (exceptions.json) touching any line in this cell"),
        rows=rows)


def taxes_audit_basis(E, read):
    """Budget vs audited actual on the audit's own groups."""
    ex = [e for e in E.exceptions() if e["scope"] == "group"]
    rows = []
    for r in read("tax_series_audit_basis.csv"):
        fy = int(r["fiscal_year"])
        rows.append(dict(
            fiscal_year=fy, group=r["group"], budget_audit_original=num(r["budget_audit_original"]),
            budget_audit_final=num(r["budget_audit_final"]), actual_audited=num(r["actual_audited"]),
            budget_docs_adopted=num(r["budget_docs_adopted"]), actual_budget_doc=num(r["actual_budget_doc"]), note=r["note"],
            exception_ids=sorted(e["exception_id"] for e in ex if e["scope_id"] == r["group"] and in_range(e, fy))))
    rows.sort(key=lambda r: (r["group"], r["fiscal_year"]))
    return dict(
        title="Taxes: budget vs audited actual, by the audit's groups",
        description="The audit's original and final budget beside the audited actual for each of the audit's own tax groups, so budget "
                    "and actual are strictly comparable. Empty cells mean the source has no figure.",
        sources=["tax_series_audit_basis.csv"],
        columns=dict(fiscal_year="fiscal year", group="report group (categories.json, scheme report_group)",
                     budget_audit_original="audit original budget, dollars", budget_audit_final="audit final budget, dollars",
                     actual_audited="audited actual, dollars", budget_docs_adopted="budget document's adopted figure, dollars",
                     actual_budget_doc="budget document's preliminary actual, dollars", note="how the group is defined this year",
                     exception_ids="group-scoped exceptions in force this year"),
        rows=rows)


def taxes_budget_vs_actual(E, read):
    """Budget vs audited actual per group, plus a total over all groups: what the budget-vs-actual charts draw."""
    budget_of = lambda r: r["budget_audit_original"] if r["budget_audit_original"] is not None else r["budget_docs_adopted"]
    src = taxes_audit_basis(E, read)["rows"]
    rows, by_year = [], defaultdict(list)
    for r in src:
        b, a = budget_of(r), r["actual_audited"]
        by_year[r["fiscal_year"]].append((b, a, r["exception_ids"]))
        rows.append(dict(fiscal_year=r["fiscal_year"], group=r["group"], is_total=False, budget=b, actual=a,
                         variance_pct=variance(b, a), exception_ids=r["exception_ids"]))
    for fy, parts in by_year.items():  # a total exists when at least one group has the figure; missing groups count as 0
        b = None if all(p[0] is None for p in parts) else sum(p[0] or 0 for p in parts)
        a = None if all(p[1] is None for p in parts) else sum(p[1] or 0 for p in parts)
        rows.append(dict(fiscal_year=fy, group="All taxes", is_total=True, budget=b, actual=a, variance_pct=variance(b, a),
                         exception_ids=sorted({i for p in parts for i in p[2]})))
    rows.sort(key=lambda r: (r["is_total"], r["group"], r["fiscal_year"]))
    return dict(
        title="Taxes: budget vs audited actual",
        description="Per audit group and in total (telecommunications included): the budget (the audit's original budget, else the "
                    "budget document's adopted figure) beside the audited actual and the percent difference.",
        sources=["tax_series_audit_basis.csv"],
        columns=dict(fiscal_year="fiscal year", group="report group, or 'All taxes' for the total of every group",
                     is_total="true on the 'All taxes' rows", budget="dollars; empty when the source has none",
                     actual="audited actual, dollars; empty before FY2012 and for FY2027 (not yet audited)",
                     variance_pct="(actual - budget) / budget, as a fraction; empty when either is missing or the budget is 0",
                     exception_ids="exceptions in force for any group in this row"),
        rows=rows)


def variance(budget, actual):
    return None if not budget or actual is None else round((actual - budget) / budget, 6)


def expenses_by_dept_object(E, read):
    """General Fund adopted expenses per fiscal year x department x object category (contra expense netted in, as the city's totals do)."""
    amap = E.account_map()
    cat = {c["category_id"]: c for c in E.categories("expense_object")}
    dept = E.departments("01")
    sums, ex_of = defaultdict(int), defaultdict(set)
    for f in read("facts.csv"):
        if f["fund_code"] != "01" or f["source_type"] != "budget" or f["basis"] != "adopted" or f["flow"] not in ("expense", "contra_expense"):
            continue
        # contra accounts carry no expense category in account_map; categories.csv (exp_personnel) says the health insurance
        # reimbursement is counted there, as the city's personnel subtotals do (exception EX-expense-categories)
        category = "exp_personnel" if f["flow"] == "contra_expense" else amap[(f["flow"], f["account_key"])]["category_id"]
        k = (int(f["period_fy"]), f["dept_code"], category)
        sums[k] += int(f["amount"])
        ex_of[k].update(ids(f["exception_ids"]))
    rows = []
    for (fy, d, c), amount in sums.items():
        rows.append(dict(fiscal_year=fy, dept_code=d, department=dept[d]["name"], function=dept[d]["function"], object_id=c,
                         object=cat[c]["name"], amount=amount, exception_ids=sorted(ex_of[(fy, d, c)])))
    rows.sort(key=lambda r: (r["fiscal_year"], r["dept_code"], r["object_id"]))
    return dict(
        title="General Fund expenses by department and object",
        description="The adopted budget's expense lines summed per department and object category. The health insurance reimbursement "
                    "(110-4540) is netted against personnel, as in the city's own subtotals. FY2027 includes a $10.9M operating transfer "
                    "out of reserves for capital projects.",
        sources=["facts.csv", "accounts map (entities/<id>/account_map.csv, departments.csv, categories.csv)"],
        columns=dict(fiscal_year="fiscal year", dept_code="department code", department="department name", function="function group",
                     object_id="object category id (categories.json, scheme expense_object)", object="object category name",
                     amount="dollars", exception_ids="exceptions touching the lines in this cell"),
        rows=rows)


def expenses_fund_totals(E, read):
    """Sum of the expense lines against the total the city prints, per adopted budget."""
    lines, line_ex = defaultdict(int), defaultdict(set)
    kinds = {e["exception_id"]: e["kind"] for e in E.exceptions()}
    for r in expenses_by_dept_object(E, read)["rows"]:
        lines[r["fiscal_year"]] += r["amount"]
        line_ex[r["fiscal_year"]].update(i for i in r["exception_ids"] if kinds.get(i) in ("omitted_from_total", "stale_column", "source_typo"))
    fund_ex = [e for e in E.exceptions() if e["scope_id"] == "fund-expense-total"]
    rows = []
    for t in read("control_totals.csv"):
        if t["kind"] != "fund_expenses" or t["basis"] != "adopted":
            continue
        fy = int(t["period_fy"])
        rows.append(dict(fiscal_year=fy, lines_total=lines[fy], printed_total=int(t["amount"]), diff=int(t["amount"]) - lines[fy],
                         page_label=t["page_label"], exception_ids=sorted({e["exception_id"] for e in fund_ex if in_range(e, fy)}
                                              | (line_ex[fy] if abs(int(t["amount"]) - lines[fy]) > 3 else set()))))
    rows.sort(key=lambda r: r["fiscal_year"])
    return dict(
        title="General Fund adopted expenses: lines vs the city's printed total",
        description="A difference of $1-3 is the city's rounding; larger ones are places the city's own arithmetic is off or leaves lines "
                    "out of its total (see the exceptions).",
        sources=["control_totals.csv", "facts.csv"],
        columns=dict(fiscal_year="fiscal year", lines_total="sum of the expense lines, dollars", printed_total="TOTAL GENERAL FUND EXPENSES as printed",
                     diff="printed - lines", page_label="printed page label of the total", exception_ids="exceptions that explain a difference"),
        rows=rows)


def expenses_audit_vs_budget(E, read):
    """Audited expenditures against the original budget per department group, plus a total over all groups."""
    def n(x):
        return None if x in ("", None) else int(x)

    def var(budget, actual):
        return None if not budget or actual is None else round((actual - budget) / budget, 6)

    codes, func = defaultdict(set), {}
    dept = E.departments("01")
    for m in E.table("audit_expenditure_map.csv"):
        if m["fund_code"] == "01" and m["department_group"] and m["budget_dept_codes"]:
            codes[m["department_group"]].update(m["budget_dept_codes"].split("|"))
            func.setdefault(m["department_group"], dept[sorted(codes[m["department_group"]])[0]]["function"])
    audit_ex = [e for e in E.exceptions() if e["exception_id"].startswith("EX-audit-")]

    def ex_for(group, fy):
        out = set()
        for e in audit_ex:
            if not in_range(e, fy):
                continue
            if e["scope"] == "group" and e["scope_id"] == group:
                out.add(e["exception_id"])
            elif e["scope"] == "account" and any(a.split("-")[0] in codes[group] for a in e["scope_id"].split("|")):
                out.add(e["exception_id"])
        return sorted(out)

    rows, by_year = [], defaultdict(lambda: [0, 0, 0, set()])
    for r in read("audit_expenses_vs_budget.csv"):
        g, fy = r["department_group"], int(r["fiscal_year"])
        orig, fin, act = n(r["audit_original"]), n(r["audit_final"]), n(r["audit_actual"])
        ex = ex_for(g, fy)
        rows.append(dict(
            fiscal_year=fy, department_group=g, label=g.replace("_", " ").capitalize(), function=func.get(g), is_total=False,
            audit_original=orig, audit_final=fin, audit_actual=act, variance_pct=var(orig, act),
            budget_adjusted=n(r["budget_adjusted"]) if r["diff"] != "" else None, diff=n(r["diff"]), note=r["note"], exception_ids=ex))
        t = by_year[fy]
        t[0] += orig or 0
        t[1] += fin or 0
        t[2] += act or 0
        t[3].update(ex)
    for fy, (orig, fin, act, ex) in by_year.items():
        rows.append(dict(fiscal_year=fy, department_group="all_departments", label="All departments", function=None, is_total=True,
                         audit_original=orig, audit_final=fin, audit_actual=act, variance_pct=var(orig, act), budget_adjusted=None,
                         diff=None, note="", exception_ids=sorted(ex)))
    rows.sort(key=lambda r: (r["is_total"], r["department_group"], r["fiscal_year"]))
    return dict(
        title="Audited expenditures vs the original budget, by department group",
        description="The audit's original budget equals the adopted budget (after three presentation rules) for 179 of 182 comparable "
                    "group-years; actual is what the audit reports spent. Transfers out are excluded on both sides. variance_pct is "
                    "(actual - original) / original, so a negative number means less was spent than budgeted.",
        sources=["audit_expenses_vs_budget.csv", "entities/<id>/audit_expenditure_map.csv", "exceptions.csv"],
        columns=dict(fiscal_year="fiscal year", department_group="department group", label="display name",
                     function="function group (categories.json, scheme function_group)", is_total="true on the all-departments total rows",
                     audit_original="audit original budget, dollars", audit_final="audit final budget, dollars",
                     audit_actual="audited actual expenditures, dollars", variance_pct="(actual - original) / original, as a fraction",
                     budget_adjusted="adopted budget put on the audit's footing; empty when there is no budget document",
                     diff="audit original - adjusted budget (0 = they agree)", note="free text",
                     exception_ids="audit-comparison exceptions (EX-audit-*) for this group and year"),
        rows=rows)


def fund_balance(E, read):
    """General Fund and total governmental funds: balances by category, revenues, expenditures and months of reserves."""
    cols = ["cash", "total_assets", "total_liabilities", "total_deferred_inflows", "nonspendable", "restricted", "committed", "assigned",
            "unassigned", "total_fund_balance", "revenues", "expenditures", "excess_revenues_over_expenditures", "other_financing",
            "net_change", "beginning_balance", "prior_period_adjustment", "ending_balance"]
    ex = [e for e in E.exceptions() if e["scope_id"] in ("fund-balance-rollforward", "fund-balance-categories", "fund-statements")]
    rows = []
    for r in read("fund_balance_summary.csv"):
        fy = int(r["fiscal_year"])
        d = dict(fiscal_year=fy, fund=r["fund"], **{c: num(r[c]) for c in cols},
                 unassigned_months=float(r["unassigned_months"]) if r["unassigned_months"] else None,
                 exception_ids=sorted(e["exception_id"] for e in ex if in_range(e, fy)))
        rows.append(d)
    rows.sort(key=lambda r: (r["fund"], r["fiscal_year"]))
    return dict(
        title="Fund balance, General Fund and all governmental funds",
        description="From the audits' balance sheet and statement of revenues, expenditures and changes in fund balances. Unassigned months = "
                    "unassigned balance divided by that year's General Fund expenditures, in months.",
        sources=["fund_balance_summary.csv"],
        columns=dict(fiscal_year="fiscal year (April 30 year end)", fund="General | Total governmental funds",
                     **{c: "dollars" for c in cols}, unassigned_months="months of expenditures covered by the unassigned balance (General Fund)",
                     exception_ids="exceptions about the statements for this year"),
        rows=rows)


# which exceptions concern which Water / Sewer dataset (the registry also holds ones about the budget extraction)
WS_STATEMENT_EX = ("EX-wsstmt-", "EX-wsold-", "EX-ws-audit-summary-basis")
WS_COMPARE_EX = ("EX-ws-audit-", "EX-ws-set-asides")
WS_GROUP = {"Water": "ws_water", "Sewer": "ws_sewer", "Collection system": "ws_collection"}
WS_DEPT = {"Water": "810", "Sewer": "820", "Collection system": "830"}


def ws_exceptions(E, fy, prefixes, department=None):
    """The Water / Sewer exceptions with these id prefixes in force for a fiscal year; with a department, the group- and account-scoped
    ones are kept only for that department."""
    out = []
    for e in E.exceptions():
        if not (e["exception_id"].startswith(prefixes) and in_range(e, fy)):
            continue
        if department and e["scope"] == "group" and e["scope_id"] != WS_GROUP[department]:
            continue
        if department and e["scope"] == "account" and not any(a.split(":")[-1].split("-")[0] == WS_DEPT[department] for a in e["scope_id"].split("|")):
            continue
        out.append(e["exception_id"])
    return sorted(out)


def ws_summary(E, read):
    """Water / Sewer Fund by fiscal year: the audit's statements (net position, income, cash) beside the budgetary-basis schedule."""
    sched = {int(r["fiscal_year"]): r for r in read("ws_summary.csv")}
    cols = ["cash_and_investments_comparable", "net_position_net_investment_in_capital", "net_position_restricted", "net_position_unrestricted",
            "net_position_total", "operating_revenues", "operating_expenses_incl_depreciation", "depreciation", "operating_income",
            "change_in_net_position", "cash_flow_operating", "cash_flow_noncapital_financing", "cash_flow_capital_financing", "purchases_of_capital_assets"]
    stmt = {int(r["fiscal_year"]): r for r in read("ws_audit_statement_summary.csv") if int(r["fiscal_year"]) >= E.config["documents"]["audit_range"][0]}
    rows = []
    for fy in sorted(set(stmt) | set(sched)):  # a year can have only a budget (the adopted FY2027 budget has no audit yet)
        r, b = stmt.get(fy, {}), sched.get(fy, {})
        d = dict(fiscal_year=fy, **{c: num(r.get(c)) for c in cols},
                 budgetary_revenues_original=num(b.get("operating_revenues_original")), budgetary_revenues_actual=num(b.get("operating_revenues_actual")),
                 budgetary_expenses_original=num(b.get("operating_expenses_original")), budgetary_expenses_actual=num(b.get("operating_expenses_actual")),
                 budget_revenues=num(b.get("budget_revenues")), budget_expenses=num(b.get("budget_expenses")), exception_ids=ws_exceptions(E, fy, WS_STATEMENT_EX))
        if d["purchases_of_capital_assets"] is not None:
            d["purchases_of_capital_assets"] = abs(d["purchases_of_capital_assets"])
        rows.append(d)
    rows.sort(key=lambda r: r["fiscal_year"])
    return dict(
        title="Water / Sewer Fund by year",
        description="Audited net position, operating income and cash flows (GAAP, with depreciation), beside the audit's budgetary-basis operating "
                    "schedule (which counts capital outlay as an expense and leaves out depreciation) and the budget document's revenue and expense totals.",
        sources=["ws_audit_statement_summary.csv", "ws_summary.csv"],
        columns=dict(fiscal_year="fiscal year (April 30 year end)", cash_and_investments_comparable="cash and investments, dollars (FY2012 on the same footing as later years)",
                     net_position_net_investment_in_capital="net position invested in capital assets", net_position_restricted="restricted net position",
                     net_position_unrestricted="unrestricted net position", net_position_total="total net position",
                     operating_revenues="audited operating revenues", operating_expenses_incl_depreciation="operating expenses including depreciation (statement of revenues, expenses and changes in net position)",
                     depreciation="depreciation", operating_income="operating revenues minus operating expenses including depreciation",
                     change_in_net_position="change in net position (includes capital grants and contributions)",
                     cash_flow_operating="net cash from operating activities", cash_flow_noncapital_financing="net cash from noncapital financing (e.g. an advance from the General Fund)",
                     cash_flow_capital_financing="net cash from capital and related financing", purchases_of_capital_assets="capital assets bought, dollars (positive)",
                     budgetary_revenues_original="original budget, operating revenues (audit schedule)", budgetary_revenues_actual="actual operating revenues (audit schedule)",
                     budgetary_expenses_original="original budget, operating expenses including capital outlay and excluding depreciation",
                     budgetary_expenses_actual="actual operating expenses on the same budgetary basis",
                     budget_revenues="budget document total revenues", budget_expenses="budget document total expenses",
                     exception_ids="Water / Sewer exceptions in force this year"),
        rows=rows)


def ws_audit_vs_budget(E, read):
    """Audited operating expenses against the original budget per Water / Sewer department."""
    def n(x):
        return None if x in ("", None) else int(x)
    rows = []
    for r in read("audit_ws_vs_budget.csv"):
        fy = int(r["fiscal_year"])
        o, a = n(r["audit_original"]), n(r["audit_actual"])
        rows.append(dict(fiscal_year=fy, department=r["department"], audit_original=o, audit_final=n(r["audit_final"]), audit_actual=a,
                         variance_pct=None if not o or a is None else round((a - o) / o, 6),
                         budget_adjusted=n(r["budget_adjusted"]), diff=n(r["diff"]), exception_ids=ws_exceptions(E, fy, WS_COMPARE_EX, r["department"])))
    rows.sort(key=lambda r: (r["department"], r["fiscal_year"]))
    return dict(
        title="Water / Sewer: audited operating expenses vs the original budget, by department",
        description="The audit's original budget equals the budget document's expense lines (without the depreciation and bond set-asides, transfers "
                    "out and the card processing fee) in 31 of 36 comparable department-years (FY2015-FY2026).",
        sources=["audit_ws_vs_budget.csv"],
        columns=dict(fiscal_year="fiscal year", department="Water, Sewer or Collection system", audit_original="audit original budget, dollars",
                     audit_final="audit final budget", audit_actual="audited actual operating expenses (budgetary basis)",
                     variance_pct="(actual - original) / original, as a fraction", budget_adjusted="budget document put on the audit's footing; empty before FY2015",
                     diff="audit original - adjusted budget (0 = they agree)", exception_ids="Water / Sewer exceptions in force this year"),
        rows=rows)


OTHER_FUND_EX = {  # exception id prefixes by fund code; EX-funds-audit-mapping and EX-negative-cash-balances concern every fund
    "10": ("EX-fundsched-", "EX-fund-duplicate"), "13": ("EX-fundsched-",), "15": ("EX-fundsched-",), "16": ("EX-fundsched-",), "17": ("EX-fundsched-",),
    "41": ("EX-pb-", "EX-fund-duplicate"), "18": ("EX-library-",), "IPL": ("EX-library-",),
    "PP": ("EX-pension-", "EX-fy24-pension"), "FP": ("EX-pension-", "EX-fy24-pension", "EX-fire-pension"),
}
OTHER_FUND_ALL = ("EX-funds-audit-mapping", "EX-negative-cash-balances")


def other_fund_exceptions(E, code, fy):
    prefixes = OTHER_FUND_ALL + OTHER_FUND_EX.get(code, ())
    return sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"].startswith(prefixes) and in_range(e, fy))


def other_funds(E, read):
    """Every fund other than the General and Water / Sewer funds: the audit's schedule (original budget, actual, balances) beside the budget."""
    kind = {f["fund_code"]: f for f in E.table("funds.csv")}
    cols = ["revenues_original", "revenues_actual", "expenditures_original", "expenditures_actual", "net_other_financing_actual", "net_change_actual",
            "beginning_balance", "ending_balance", "budget_revenues", "budget_expenses", "revenues_diff", "expenditures_diff"]
    rows = []
    for r in read("other_funds_summary.csv"):
        fy, code = int(r["fiscal_year"]), r["fund_code"]
        rows.append(dict(fiscal_year=fy, fund_code=code, fund=r["fund"], type=kind[code]["type"], match=r["match"], **{c: num(r[c]) for c in cols},
                         exception_ids=other_fund_exceptions(E, code, fy)))
    rows.sort(key=lambda r: (r["fund_code"], r["fiscal_year"]))
    return dict(
        title="Other funds: audited schedules beside the budget",
        description="Motor Fuel Tax, tax increment financing (TIF) districts, special service areas, Capital Projects, the library, escrow and the pension funds. "
                    "The audit's original budget is compared with the budget document only for the funds whose match is verified (every year agrees to the dollar); "
                    "other funds are matched by name, or exist only in the budget or only in the audit.",
        sources=["other_funds_summary.csv", "entities/<id>/funds.csv"],
        columns=dict(fiscal_year="fiscal year", fund_code="fund code (PP, FP and IPL are this project's codes)", fund="fund name", type="kind of fund",
                     match="verified | by_name | none: how the audit's fund was matched to the budget's", revenues_original="audit original budget, revenues",
                     revenues_actual="audited actual revenues", expenditures_original="audit original budget, expenditures", expenditures_actual="audited actual expenditures",
                     net_other_financing_actual="transfers and other financing, audited", net_change_actual="audited net change in fund balance",
                     beginning_balance="fund balance at the start of the year", ending_balance="fund balance at year end",
                     budget_revenues="budget document revenues", budget_expenses="budget document expenses",
                     revenues_diff="audit original - budget document, revenues (verified funds only)", expenditures_diff="audit original - budget document, expenditures (verified funds only)",
                     exception_ids="exceptions about this fund and year"),
        rows=rows)


def funds(E, read):
    """The funds list: kind, how each audit fund maps to a budget fund, and how sure that is."""
    rows = [dict(fund_code=f["fund_code"], name=f["name"], type=f["type"], audit_name=f["audit_name"], budget_match=f["budget_match"], note=f["note"])
            for f in E.table("funds.csv")]
    return dict(
        title="Funds",
        description="Every fund in the budget documents and audits, with how the audit's fund was matched to the budget's (verified by comparing dollars, by name, or not at all).",
        sources=["entities/<id>/funds.csv"],
        columns=dict(fund_code="fund code", name="fund name", type="kind of fund", audit_name="the name the audits use", budget_match="verified | by_name | empty",
                     note="how the match was made"),
        rows=rows)


DEBT_EX = ("EX-adp-iepa", "EX-adp-gasb68", "EX-adp-fy2025-iepa", "EX-adp-fy2012-schedule", "EX-adp-gasb75")  # debt-note exceptions (the pension ones go with pensions)
ACTIVITY = {"governmental": "Governmental activities", "business_type": "Water and sewer (business-type)", "component_unit": "Ida Public Library (component unit)"}


def debt_exceptions(E, fy):
    return sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"].startswith(DEBT_EX) and in_range(e, fy))


def debt_line(E, line_id, label):
    """The debt_lines.csv row (name, group, sort) for a printed line: by line_id, or by label pattern for the lines the extractor left without an id."""
    for r in E.table("debt_lines.csv"):
        if line_id and r["line_id"] == line_id and not r["label_pattern"]:
            return r
        if not line_id and r["label_pattern"] and re.search(r["label_pattern"], label):
            return r
    raise KeyError(f"no debt_lines.csv row for {line_id!r} / {label!r}")


def debt_balances(E, read):
    """Long-term liabilities from the audits' debt notes, one row per fiscal year x activity x kind of liability (the printed total excluded)."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    cells = defaultdict(lambda: dict(beginning=0, additions=0, reductions=0, ending=0, current_portion=0, pages=set()))
    meta = {}
    for r in read("debt_balances.csv"):
        if r["line_id"] == "total":
            continue
        line = debt_line(E, r["line_id"], r["line_item"])
        key = (int(r["period_fy"]), r["activity"], line["name"])
        meta[key] = (line, r["doc_id"])
        cells[key][r["measure"]] += int(r["amount"])
        cells[key]["pages"].add(int(r["pdf_page"]))
    rows = []
    for (fy, act, name), c in cells.items():
        line, doc = meta[(fy, act, name)]
        rows.append(dict(fiscal_year=fy, activity=act, activity_label=ACTIVITY[act], name=name, group=line["group"], beginning=c["beginning"], additions=c["additions"],
                         reductions=c["reductions"], ending=c["ending"], current_portion=c["current_portion"], pdf_page=min(c["pages"]), source_url=urls[doc],
                         exception_ids=debt_exceptions(E, fy), _sort=int(line["sort"])))
    rows.sort(key=lambda r: (r["fiscal_year"], r["activity"], r["_sort"], r["name"]))
    for r in rows:
        del r["_sort"]
    return dict(
        title="Long-term liabilities by year",
        description="From the notes to each audit: what the city owed at each April 30 by kind of liability, with the year's additions and reductions. group is borrowing "
                    "(debt certificates, loans, bonds, leases), pension (net pension liabilities), opeb (retiree health benefits) or other (accrued leave, landfill and "
                    "asset retirement). The three activities are never added together here: the library is a separate (component) unit.",
        sources=["debt_balances.csv", "entities/<id>/debt_lines.csv"],
        columns=dict(fiscal_year="fiscal year (April 30 year end)", activity="governmental | business_type | component_unit", activity_label="display name of the activity",
                     name="kind of liability", group="borrowing | pension | opeb | other", beginning="balance at the start of the year, dollars",
                     additions="borrowed or recognized during the year", reductions="repaid or reduced during the year", ending="balance at year end",
                     current_portion="due within one year", pdf_page="page of the audit PDF", source_url="the audit on belvidereil.gov",
                     exception_ids="debt-note exceptions in force this year"),
        rows=rows)


def debt_issues(E, read):
    """The borrowings the audits list one by one: rate, final maturity, balance and the part due within a year."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    rows = []
    for r in read("debt_issue_terms.csv"):
        fy = int(r["period_fy"])
        rows.append(dict(fiscal_year=fy, activity=r["activity"], activity_label=ACTIVITY.get(r["activity"], "Not split by activity (FY2012-15 audits)"), issue=r["issue"], interest_rate=r["interest_rate_text"],
                         maturity=r["maturity_text"], ending_balance=num(r["ending_balance"]), current_portion=num(r["current_portion"]),
                         pdf_page=num(r["pdf_page"]), source_url=urls[r["doc_id"]], exception_ids=debt_exceptions(E, fy)))
    rows.sort(key=lambda r: (r["fiscal_year"], r["activity"]))
    return dict(
        title="Borrowings listed in the audits",
        description="One row per borrowing in the audit's debt note (a TOTAL row closes each list). Labels, rates and maturities are as the audit prints them.",
        sources=["debt_issue_terms.csv"],
        columns=dict(fiscal_year="fiscal year", activity="governmental | business_type | component_unit", activity_label="display name", issue="the borrowing as printed",
                     interest_rate="rate(s) as printed", maturity="final maturity as printed", ending_balance="balance at year end, dollars",
                     current_portion="due within one year", pdf_page="page of the audit PDF", source_url="the audit on belvidereil.gov",
                     exception_ids="debt-note exceptions in force this year"),
        rows=rows)


def debt_payments(E, read):
    """The payments the audits schedule to maturity: principal and interest by year, as of each audit."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    rows = []
    for r in read("debt_service_schedule.csv"):
        if r["row_kind"] != "year":
            continue
        fy = int(r["period_fy"])
        rows.append(dict(fiscal_year=fy, activity=r["activity"], instrument=r["instrument"], year_ending=int(r["year_ending"]), principal=num(r["principal"]),
                         interest=num(r["interest"]), total=num(r["total"]), pdf_page=num(r["pdf_page"]), source_url=urls[r["doc_id"]],
                         exception_ids=debt_exceptions(E, fy)))
    rows.sort(key=lambda r: (r["fiscal_year"], r["activity"], r["instrument"], r["year_ending"]))
    return dict(
        title="Scheduled debt payments",
        description="Each audit's schedule of principal and interest due by fiscal year to maturity, as of that audit's year end. The FY2012 audit's schedule is not extracted.",
        sources=["debt_service_schedule.csv"],
        columns=dict(fiscal_year="the audit it comes from", activity="governmental | business_type | component_unit", instrument="the borrowing",
                     year_ending="the fiscal year the payment falls in", principal="principal due", interest="interest due", total="principal + interest",
                     pdf_page="page of the audit PDF", source_url="the audit on belvidereil.gov", exception_ids="debt-note exceptions in force for that audit"),
        rows=rows)


PLAN_NAME = {"police": "Police pension", "fire": "Fire pension", "imrf_regular": "IMRF (regular)", "imrf_slep": "IMRF (sheriff's law enforcement, SLEP)",
             "imrf_eco": "IMRF (elected county officials)"}
PENSION_MEASURES = ("funded_ratio_pct", "tpl", "fnp", "npl", "covered_payroll", "adc", "employer_contribution_actual", "contribution_deficiency",
                    "money_weighted_return_pct")
PENSION_EX_ALL = ("EX-adp-rsi-", "EX-adp-pension-", "EX-adp-gasb68", "EX-adp-fy2012-npo", "EX-adp-ocr-fixtok")
PENSION_EX = {"police": ("EX-adp-police",), "fire": ("EX-adp-fire",), "imrf_regular": ("EX-adp-imrf",), "imrf_slep": ("EX-adp-imrf", "EX-adp-slep"), "imrf_eco": ("EX-adp-imrf",)}


def pension_exceptions(E, plan, fy):
    prefixes = PENSION_EX_ALL + PENSION_EX.get(plan, ())
    return sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"].startswith(prefixes) and in_range(e, fy))


def pension_plans(E, read):
    """Each pension plan's funded position and contributions by year, from the audits' notes and required supplementary information."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    first = {}  # (plan, measure, measurement year) -> the earliest audit that reports it (later audits repeat it)
    for r in read("pension_summary.csv"):
        if r["plan_id"] not in PLAN_NAME or r["measure"] not in PENSION_MEASURES or r["value"] in ("", None):
            continue
        k = (r["plan_id"], r["measure"], r["period_label"] or r["period_fy"])
        if k not in first or int(r["period_fy"]) < int(first[k]["period_fy"]):
            first[k] = r
    cells = defaultdict(dict)
    for (plan, measure, label), r in first.items():
        fy = int(r["period_fy"])  # the audit year that reports it (IMRF is measured December 31 of the year before)
        cells[(plan, fy)][measure] = r
    rows = []
    for (plan, fy), m in cells.items():
        any_r = next(iter(m.values()))
        d = dict(fiscal_year=fy, plan=plan, plan_name=PLAN_NAME[plan], basis="GASB 68" if any(x["source"] == "rsi" for x in m.values()) else "GASB 27",
                 measured=("April 30" if plan in ("police", "fire") else "December 31") + (f" {fy - 1}" if plan not in ("police", "fire") else f" {fy}"))
        for meas in PENSION_MEASURES:
            r = m.get(meas)
            d[meas] = None if r is None else (float(r["value"]) if r["unit"] == "pct" else int(float(r["value"])))
        d.update(pdf_page=num(any_r["pdf_page"]), source_url=urls[any_r["doc_id"]], exception_ids=pension_exceptions(E, plan, fy))
        rows.append(d)
    rows.sort(key=lambda r: (r["plan"], r["fiscal_year"]))
    return dict(
        title="Pension plans by year",
        description="Funded ratio, total pension liability (TPL), plan assets (fiduciary net position, FNP), net pension liability (NPL = TPL - FNP), covered payroll and "
                    "contributions for the police and fire pensions and the three IMRF groups. FY2012-FY2015 audits (GASB 27) report only a funded status; from FY2016 "
                    "(GASB 68) they report the full liability. Percentages are 0-100.",
        sources=["pension_summary.csv"],
        columns=dict(fiscal_year="the audit year that reports the figure", plan="plan id", plan_name="display name", basis="GASB 27 (to FY2015) or GASB 68",
                     measured="the measurement date", funded_ratio_pct="plan assets as a percent of the actuarial liability",
                     tpl="total pension liability, dollars", fnp="plan assets (fiduciary net position), dollars", npl="net pension liability, dollars",
                     covered_payroll="payroll of the members the plan covers, dollars", adc="actuarially determined contribution, dollars",
                     employer_contribution_actual="what the city contributed, dollars", contribution_deficiency="ADC minus actual contribution, dollars",
                     money_weighted_return_pct="investment return, percent (police and fire)", pdf_page="page of the audit PDF",
                     source_url="the audit on belvidereil.gov", exception_ids="pension exceptions in force this year"),
        rows=rows)


def opeb_liability(E, read):
    """The city's retiree health (OPEB) liability by year: GASB 45 measures to FY2018, GASB 75 from FY2019."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    by = defaultdict(dict)
    for r in read("opeb_rollforward.csv"):
        if r["plan_id"] != "opeb_city" or r["source"] not in ("note_gasb45", "note"):
            continue
        by[int(r["period_fy"])].setdefault(r["line_id"], r)
    rows = []
    for fy, m in sorted(by.items()):
        v = lambda k: int(m[k]["amount"]) if k in m and m[k]["amount"] not in ("", None) else None
        gasb75 = fy >= 2019
        src = next(iter(m.values()))
        rows.append(dict(
            fiscal_year=fy, basis="GASB 75" if gasb75 else "GASB 45",
            total_opeb_liability=v("ending") if gasb75 else None, service_cost=v("service_cost") if gasb75 else None, interest=v("interest") if gasb75 else None,
            benefit_payments=v("benefit_payments") if gasb75 else None,
            actuarial_accrued_liability=None if gasb75 else v("aal"), net_opeb_obligation=None if gasb75 else v("noo_ending"),
            annual_opeb_cost=None if gasb75 else v("annual_opeb_cost"), contributions=None if gasb75 else v("contributions"),
            pdf_page=num(src["pdf_page"]), source_url=urls[src["doc_id"]],
            exception_ids=sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"].startswith(("EX-adp-opeb", "EX-adp-gasb75", "EX-adp-imrf-fy2016-col")) and in_range(e, fy))))
    return dict(
        title="Retiree health benefits (OPEB) by year",
        description="The city's promise of retiree health benefits. Until FY2018 (GASB 45) the audits report an actuarial accrued liability and the part booked as a net OPEB obligation; "
                    "from FY2019 (GASB 75) they report the full total OPEB liability. The two measures are not comparable.",
        sources=["opeb_rollforward.csv"],
        columns=dict(fiscal_year="audit year", basis="GASB 45 or GASB 75", total_opeb_liability="GASB 75 total OPEB liability, dollars",
                     service_cost="cost of benefits earned this year (GASB 75)", interest="interest on the liability (GASB 75)",
                     benefit_payments="benefits paid (GASB 75)", actuarial_accrued_liability="GASB 45 actuarial accrued liability, all unfunded",
                     net_opeb_obligation="GASB 45 liability recorded on the balance sheet", annual_opeb_cost="GASB 45 annual cost", contributions="GASB 45 contributions",
                     pdf_page="page of the audit PDF", source_url="the audit on belvidereil.gov", exception_ids="OPEB exceptions in force this year"),
        rows=rows)


def reference(E, read):
    """Population, price level and assessed valuation by fiscal year, for per-resident and inflation-adjusted views."""
    sources = {r["series"]: r for r in E.table("reference_sources.csv")}
    rows = []
    for r in read("reference_by_fy.csv"):
        rows.append(dict(
            fiscal_year=int(r["fiscal_year"]), population=int(r["population"]), population_year=int(r["population_year"]), population_flag=r["population_flag"],
            cpi_fy_avg=float(r["cpi_fy_avg"]), cpi_months=int(r["cpi_months"]), cpi_flag=r["cpi_flag"], cpi_base_fy=int(r["cpi_base_fy"]), cpi_factor=float(r["cpi_factor"]),
            eav_levy_year=int(r["eav_levy_year"]), eav_city=num(r["eav_city"]), eav_flag=r["eav_flag"],
            exception_ids=(ids(r["eav_flag"]) if r["eav_flag"].startswith("EX-") else []) + (["EX-reference-population-scaled"] if "scaled" in r["population_flag"] else [])))
    return dict(
        title="Population, price level and assessed valuation by fiscal year",
        description="Reference series for per-resident and inflation-adjusted views. Sources: " + "; ".join(f"{k}: {v['publisher']} ({v['identifier']})" for k, v in sources.items()) +
                    ". Full descriptions and methods are in the sources list.",
        sources=["reference_by_fy.csv", "entities/<id>/reference_sources.csv"],
        columns=dict(fiscal_year="fiscal year (May-April)", population="resident population estimate (July 1 of the year before the fiscal year ends)",
                     population_year="the July 1 the estimate is for", population_flag="carried_forward when the next estimate is not yet published",
                     cpi_fy_avg="mean CPI-U (1982-84=100) over the fiscal year's months", cpi_months="months averaged", cpi_flag="provisional (year not over) or missing_month:YYYY-MM",
                     cpi_base_fy="the fiscal year whose dollars the factor expresses", cpi_factor="multiply a nominal amount by this to get constant base-year dollars",
                     eav_levy_year="the tax levy year collected in this fiscal year (levy year + 2 = fiscal year)", eav_city="city equalized assessed valuation of that levy year, dollars",
                     eav_flag="exception id when the audits disagree", exception_ids="exceptions about this row"),
        rows=rows)


def reference_sources(E, read):
    """Where each reference series comes from and how it is used."""
    rows = [dict(series=r["series"], description=r["description"], publisher=r["publisher"], identifier=r["identifier"], url=r["url"],
                 retrieved_on=r["retrieved_on"], terms=r["terms"], method=r["method"]) for r in E.table("reference_sources.csv")]
    return dict(
        title="Reference series: sources",
        description="Population, price level and assessed valuation: publisher, identifier, link, when it was retrieved and how it is turned into a fiscal-year value.",
        sources=["entities/<id>/reference_sources.csv"],
        columns=dict(series="cpi | population | eav", description="what the series is", publisher="who publishes it", identifier="series or file id", url="where to find it",
                     retrieved_on="date the snapshot was taken", terms="terms of use", method="how the fiscal-year value is built"),
        rows=rows)


def tax_lines(E, read):
    """Every tax line behind the category series (account level), for the breakdown tables."""
    rows = []
    url = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    page = {(p["doc_id"], p["page_label"]): p["pdf_page"] for p in read("page_map.csv")}
    corr = defaultdict(list)  # what the OCR read before a value was corrected (corrections.csv), by document and account
    for c in read("corrections.csv"):
        if c["target"] == "line":
            corr[(c["doc_id"], c["account_printed"])].append(c)
    for r in read("tax_series_long.csv"):
        label, doc = r["source_page_label"], r["source_doc"]
        pdf_page = int(label[3:]) if label.startswith("pdf") else int(page[(doc, label)])  # budget footers: page_map.csv (build_page_map.py)
        rows.append(dict(
            fiscal_year=int(r["fiscal_year"]), measure=r["measure"], basis=r["basis"], account=r["account"],
            department=r["department"], line_item=r["line_item"], amount=int(r["amount"]), category=r["category"],
            treatment=r["treatment"], source_doc=doc, source_column=r["source_column"],
            page_label=label, pdf_page=pdf_page, source_url=url[doc], note=r["note"], exception_ids=ids(r["exception_ids"]),
            corrections=[dict(method=c["method"], column=c["source_column"], raw_value=c["raw_value"], final_value=c["final_value"], detail=c["detail"])
                         for c in corr[(doc, r["account"])] if c["source_column"] in (r["source_column"], "(all)", "")]))
    rows.sort(key=lambda r: (r["measure"], r["fiscal_year"], r["account"]))
    return dict(
        title="General Fund tax lines",
        description="One row per fiscal year x measure x account. treatment is tax (counted in gross taxes), offset (negative, netted) "
                    "or excluded_fee (looks like a tax, is a fee; never summed).",
        sources=["tax_series_long.csv"],
        columns=dict(fiscal_year="fiscal year", measure="series", basis="schema basis", account="printed account number",
                     department="department", line_item="label as printed", amount="dollars", category="budget category",
                     treatment="tax | offset | excluded_fee", source_doc="document the number was read from",
                     source_column="column of that document", page_label="printed page label (e.g. '1 of 15')", pdf_page="page of the source PDF (open it at source_url#page=N)",
                     source_url="the document on belvidereil.gov", note="free text",
                     exception_ids="exceptions touching this line",
                     corrections="values the OCR read differently, with the reading kept beside the final value (corrections.csv)"),
        rows=rows)


DATASETS = {  # name -> (builder, source tables, tables copied to downloads/)
    "taxes_by_category": (taxes_by_category, ["tax_series_by_category.csv", "tax_series_long.csv"]),
    "taxes_audit_basis": (taxes_audit_basis, ["tax_series_audit_basis.csv"]),
    "taxes_budget_vs_actual": (taxes_budget_vs_actual, ["tax_series_audit_basis.csv"]),
    "ws_summary": (ws_summary, ["ws_audit_statement_summary.csv", "ws_summary.csv"]),
    "ws_audit_vs_budget": (ws_audit_vs_budget, ["audit_ws_vs_budget.csv"]),
    "other_funds": (other_funds, ["other_funds_summary.csv"]),
    "funds": (funds, ["other_funds_summary.csv"]),
    "debt_balances": (debt_balances, ["debt_balances.csv", "documents.csv"]),
    "debt_issues": (debt_issues, ["debt_issue_terms.csv", "documents.csv"]),
    "debt_payments": (debt_payments, ["debt_service_schedule.csv", "documents.csv"]),
    "pension_plans": (pension_plans, ["pension_summary.csv", "documents.csv"]),
    "opeb_liability": (opeb_liability, ["opeb_rollforward.csv", "documents.csv"]),
    "reference": (reference, ["reference_by_fy.csv"]),
    "reference_sources": (reference_sources, ["reference_by_fy.csv"]),
    "tax_lines": (tax_lines, ["tax_series_long.csv", "documents.csv", "page_map.csv", "corrections.csv"]),
    "expenses_by_dept_object": (expenses_by_dept_object, ["facts.csv"]),
    "expenses_fund_totals": (expenses_fund_totals, ["facts.csv", "control_totals.csv"]),
    "expenses_audit_vs_budget": (expenses_audit_vs_budget, ["audit_expenses_vs_budget.csv"]),
    "fund_balance": (fund_balance, ["fund_balance_summary.csv"]),
}


# --- writing --------------------------------------------------------------------------------------------------------------
def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")


def export_entity(E, out):
    base = os.path.join(out, E.id)
    if os.path.isdir(base):
        shutil.rmtree(base)
    cache = {}

    def read(name):
        if name not in cache:
            cache[name] = read_csv(E.data_path(name))
        return cache[name]

    written = []
    copies = set()
    for name, (build, sources) in DATASETS.items():
        if name not in SHARED_DATASETS and (name in PARK_DATASETS) != bool(E.config.get("fund_based_budget")):
            continue  # entity.json "fund_based_budget": true picks the fund-organised datasets (export_park.py) instead of the General Fund ones
        if not all(os.path.exists(E.data_path(s)) for s in sources):
            continue
        d = build(E, read)
        dump(os.path.join(base, name + ".json"), dict(dataset=name, entity_id=E.id, **d))
        written.append(name)
        copies.update(sources)

    cfg = E.config
    dump(os.path.join(base, "meta.json"), dict(
        entity_id=E.id, name=cfg["name"], short_name=cfg.get("short_name", cfg["name"]), type=cfg.get("type"), state=cfg.get("state"),
        fy_start_month=cfg["fy_start_month"], fy_label_rule=cfg.get("fy_label_rule"), currency=cfg.get("currency"),
        funds=cfg.get("funds", []), documents=cfg.get("documents", {}), source_index=cfg.get("source_index", {}),
        bases=schema.BASES, datasets=written))
    cats = defaultdict(list)
    for c in sorted(E.categories(), key=lambda c: (c["scheme"], int(c["sort"]))):
        cats[c["scheme"]].append(dict(id=c["category_id"], name=c["name"], flow=c["flow"], color=c["color"] or None,
                                      sort=int(c["sort"]), note=c["note"]))
    dump(os.path.join(base, "categories.json"), dict(entity_id=E.id, schemes=cats))
    # where each exception shows up in the source: the documents and PDF pages of the facts it is attached to
    seen = defaultdict(set)
    if os.path.exists(E.data_path("facts.csv")):
        urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
        for f in read("facts.csv"):
            for i in ids(f["exception_ids"]):
                seen[i].add((f["doc_id"], int(f["pdf_page"] or 0)))

    def sources(i):
        return [dict(doc_id=d, fiscal_year=int(d[2:6]), pdf_page=p, source_url=urls[d]) for d, p in sorted(seen[i], key=lambda t: (int(t[0][2:6]), t[0], t[1])) if p]

    dump(os.path.join(base, "exceptions.json"), dict(
        entity_id=E.id, kinds=list(schema.EXCEPTION_KINDS), exceptions=[
            dict(e, fy_from=num(e["fy_from"]), fy_to=num(e["fy_to"]), bases=[b for b in e["bases"].split("|") if b], sources=sources(e["exception_id"]))
            for e in E.exceptions()]))

    dl = os.path.join(base, "downloads")
    os.makedirs(dl, exist_ok=True)
    for s in sorted(copies):
        shutil.copyfile(E.data_path(s), os.path.join(dl, s))
    if os.path.exists(E.data_path("facts.csv")):
        with open(E.data_path("facts.csv"), "rb") as src, gzip.GzipFile(os.path.join(dl, "facts.csv.gz"), "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
    return dict(entity_id=E.id, name=cfg["name"], short_name=cfg.get("short_name", cfg["name"]), datasets=written)


DATASETS.update(PARK_DATASETS)
SHARED_DATASETS = {"reference", "reference_sources"}  # built for every government that has the tables


def export(entity_ids=None, out=DEFAULT_OUT):
    if not entity_ids:
        entity_ids = sorted(d for d in os.listdir(os.path.join(ROOT, "entities"))
                            if os.path.exists(os.path.join(ROOT, "entities", d, "entity.json"))
                            and Entity.load(d).config.get("export_site", True))  # entity.json "export_site": false keeps a government off the site until its pages exist
    index = [export_entity(Entity.load(i), out) for i in entity_ids]
    dump(os.path.join(out, "index.json"), dict(entities=index))
    dump(os.path.join(out, "palette.json"), palette.tokens())
    return index


def main():
    args = sys.argv[1:]
    out = DEFAULT_OUT
    if "--out" in args:
        i = args.index("--out")
        out = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    for e in export(args, out):
        print(f"{e['entity_id']}: {', '.join(e['datasets'])} -> {os.path.relpath(out, ROOT)}/{e['entity_id']}/")


if __name__ == "__main__":
    main()
