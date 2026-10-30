"""Datasets for governments whose budgets are organised by fund (the Belvidere Park District): revenue and spending by fund, property tax levies,
audited spending against the budget, fund balances and long-term debt. Registered into export_site.DATASETS; each is skipped when its source tables are
missing. Same envelope and conventions as export_site (exception_ids on every row)."""
from collections import defaultdict

from .export_common import ids, in_range, num

DEBT_GROUPS = {"alt_rev_2012A": "Bonds", "refunding_2020": "Bonds", "agreement_deed": "Other borrowing", "sbita": "Other borrowing",
               "premium": "Bond premium", "net_pension": "Net pension liability (IMRF)", "compensated_absences": "Compensated absences"}


def fund_names(E):
    return {r["fund_code"]: r["name"] for r in E.table("funds.csv")}


def budget_facts(read, flow):
    """Budget facts for the figure the site draws per fiscal year: the adopted budget, else the draft request (flagged). Returns {fy: (basis, [facts])}."""
    by = defaultdict(lambda: defaultdict(list))
    for f in read("facts.csv"):
        if f["source_type"] == "budget" and f["flow"] == flow and f["basis"] in ("adopted", "requested"):
            by[int(f["period_fy"])][f["basis"]].append(f)
    return {fy: (("adopted", b["adopted"]) if b["adopted"] else ("requested", b["requested"])) for fy, b in sorted(by.items())}


def park_revenue(E, read):
    amap, cats, funds = E.account_map(), {c["category_id"]: c for c in E.categories("budget_category")}, fund_names(E)
    sums, ex = defaultdict(int), defaultdict(set)
    basis_of = {}
    for fy, (basis, facts) in budget_facts(read, "revenue").items():
        basis_of[fy] = basis
        for f in facts:
            k = (fy, funds[f["fund_code"]], amap[("revenue", f["account_key"])]["category_id"])
            sums[k] += int(f["amount"])
            ex[k].update(ids(f["exception_ids"]))
    rows = [dict(fiscal_year=fy, fund=fund, category_id=c, category=cats[c]["name"], basis=basis_of[fy], amount=a, exception_ids=sorted(ex[(fy, fund, c)]))
            for (fy, fund, c), a in sorted(sums.items())]
    return dict(
        title="Budgeted revenue by fund and category",
        description="Every revenue line of the district's budgets summed per fund and category. The adopted budget where the district's document is the adopted one; "
                    "FY2024 and FY2027 are the draft requests (basis requested). FY2022's budget has no revenue report. Bond proceeds are budgeted as revenue.",
        sources=["facts.csv", "entities/<id>/account_map.csv, funds.csv, categories.csv"],
        columns=dict(fiscal_year="fiscal year (calendar year)", fund="fund name", category_id="budget category (categories.json, scheme budget_category)",
                     category="category name", basis="adopted | requested (a draft request)", amount="dollars", exception_ids="exceptions touching the lines in this cell"),
        rows=rows)


def park_spending(E, read):
    amap, objs, funds = E.account_map(), {c["category_id"]: c for c in E.categories("expense_object")}, fund_names(E)
    fns = {c["name"]: c for c in E.categories("function_group")}
    depts = {}
    for r in E.table("departments.csv"):
        depts[(r["fund_code"], r["dept_code"])] = r
    sums, ex, basis_of = defaultdict(int), defaultdict(set), {}
    for fy, (basis, facts) in budget_facts(read, "expense").items():
        basis_of[fy] = basis
        for f in facts:
            d = depts[(f["fund_code"], f["dept_code"])]
            k = (fy, funds[f["fund_code"]], d["function"], amap[("expense", f["account_key"])]["category_id"])
            sums[k] += int(f["amount"])
            ex[k].update(ids(f["exception_ids"]))
    rows = [dict(fiscal_year=fy, fund=fund, function=fn, function_id=fns[fn]["category_id"], object_id=o, object=objs[o]["name"], basis=basis_of[fy], amount=a,
                 exception_ids=sorted(ex[(fy, fund, fn, o)])) for (fy, fund, fn, o), a in sorted(sums.items())]
    return dict(
        title="Budgeted spending by fund, function and object",
        description="Every expense line of the district's budgets summed per fund, function group and object category (adopted budget; FY2024 and FY2027 are draft requests). "
                    "Transfers between funds are not separated out by the district.",
        sources=["facts.csv", "entities/<id>/account_map.csv, departments.csv, funds.csv, categories.csv"],
        columns=dict(fiscal_year="fiscal year (calendar year)", fund="fund name", function="function group (categories.json, scheme function_group)", function_id="function id",
                     object_id="object category (scheme expense_object)", object="object category name", basis="adopted | requested", amount="dollars",
                     exception_ids="exceptions touching the lines in this cell"),
        rows=rows)


def levies(E, read):
    """Property tax levy by fund and year from the levy ordinances, with the aggregate, the percent increase the ordinance states and the assessed valuation."""
    docs = {d["doc_id"]: d for d in read("documents.csv") if d["doc_type"] == "levy"}
    pick = {}
    for d in sorted(docs.values(), key=lambda d: (d["fiscal_year"], d["status"] != "adopted")):  # the adopted ordinance wins over a draft of the same year
        pick.setdefault(int(d["fiscal_year"]), d["doc_id"])
    eav = {int(r["fiscal_year"]): num(r["eav_city"]) for r in read("reference_by_fy.csv")}
    rows = []
    for r in read("ordinance_lines.csv"):
        if r["ordinance"] == "levy" and r["section"] == "levy" and pick.get(int(r["period_fy"])) == r["doc_id"]:
            rows.append(dict(fiscal_year=int(r["period_fy"]), fund=r["fund"], fund_code=r["fund_code"], levy=round(float(r["amount"])), status=docs[r["doc_id"]]["status"],
                             pdf_page=int(r["pdf_page"]), exception_ids=ids(r["exception_ids"])))
    summ = defaultdict(dict)
    for r in read("ordinance_summary.csv"):
        if r["ordinance"] == "levy" and pick.get(int(r["period_fy"])) == r["doc_id"] and r["item"] in ("aggregate_levy", "summary_total", "percent_increase"):
            summ[int(r["period_fy"])][r["item"]] = float(r["amount"])
    for fy, s in summ.items():
        agg = s.get("aggregate_levy", s.get("summary_total"))
        pi = s.get("percent_increase")
        rows.append(dict(fiscal_year=fy, fund="All funds (aggregate levy)", fund_code="", levy=round(agg), status=docs[pick[fy]]["status"], pdf_page=None,
                         percent_increase=pi, eav=eav.get(fy), rate_per_100=round(agg / eav[fy] * 100, 4) if eav.get(fy) else None,
                         exception_ids=sorted({i for r in rows if r["fiscal_year"] == fy for i in r["exception_ids"]})))
    rows.sort(key=lambda r: (r["fund"] == "All funds (aggregate levy)", r["fund"], r["fiscal_year"]))
    return dict(
        title="Property tax levy by fund",
        description="The levy each year's ordinance asks the county to extend (tax year = fiscal year minus 1), by fund, with the aggregate. The aggregate row also gives the percent "
                    "increase over the previous extension that the ordinance states, the district's rate-setting assessed valuation and the aggregate levy as a rate per $100 of it. "
                    "FY2027 is a draft ordinance. The FY2024 aggregate leaves out a 50,000 Prior Year Adjustment (see the exceptions).",
        sources=["ordinance_lines.csv", "ordinance_summary.csv", "reference_by_fy.csv"],
        columns=dict(fiscal_year="fiscal year (the levy ordinance of the year)", fund="fund as the ordinance names it", fund_code="fund code (empty on the aggregate row)",
                     levy="dollars", status="adopted | draft", pdf_page="page of the ordinance PDF", percent_increase="stated increase over the previous extension (aggregate row)",
                     eav="rate-setting assessed valuation of the tax year (aggregate row)", rate_per_100="aggregate levy / eav x 100 (aggregate row)",
                     exception_ids="exceptions about this ordinance"),
        rows=rows)


def park_audit_vs_budget(E, read):
    def n(x):
        return None if x in ("", None) else int(float(x))

    def var(b, a):
        return None if not b or a is None else round((a - b) / b, 6)
    ex = [e for e in E.exceptions() if e["scope"] in ("entity", "group") and e["exception_id"].startswith("EX-audit")]
    rows = []
    for r in read("audit_vs_budget.csv"):
        fy = int(r["fiscal_year"])
        rb, ra, eb, ea = n(r["audit_budget_revenues"]), n(r["audit_actual_revenues"]), n(r["audit_budget_expenditures"]), n(r["audit_actual_expenditures"])
        rows.append(dict(fiscal_year=fy, fund=r["fund"], revenue_budget=rb, revenue_actual=ra, revenue_variance_pct=var(rb, ra),
                         expense_budget=eb, expense_actual=ea, expense_variance_pct=var(eb, ea), budget_basis=r["budget_basis"],
                         revenue_matches=r["revenue_matches"] == "1", expense_matches=r["expense_matches"] == "1",
                         exception_ids=sorted(e["exception_id"] for e in ex if in_range(e, fy))))
    rows.sort(key=lambda r: (r["fund"], r["fiscal_year"]))
    return dict(
        title="Audited revenue and spending against the budget, by fund",
        description="The audit's budget-and-actual schedule per fund: the original budget beside what the audit reports was collected and spent. variance_pct is (actual - budget) / budget. "
                    "revenue_matches and expense_matches say whether the audit's budget equals the budget document's fund total.",
        sources=["audit_vs_budget.csv"],
        columns=dict(fiscal_year="fiscal year", fund="fund", revenue_budget="audit budget revenues, dollars", revenue_actual="audited revenues, dollars",
                     revenue_variance_pct="(actual - budget) / budget", expense_budget="audit budget expenditures, dollars", expense_actual="audited expenditures, dollars",
                     expense_variance_pct="(actual - budget) / budget", budget_basis="basis of the budget document compared", revenue_matches="audit budget revenues equal the document's",
                     expense_matches="audit budget expenditures equal the document's", exception_ids="entity-wide exceptions in force"),
        rows=rows)


def park_fund_balance(E, read):
    want = {"total_fund_balance": "fund_balance", "unassigned": "unassigned", "cash": "cash", "total_revenues": "revenues", "total_expenditures": "expenditures",
            "net_change_in_fund_balance": "net_change", "total_other_financing": "other_financing"}
    cells = defaultdict(dict)
    for r in read("fund_statements.csv"):
        if r["line_id"] in want and "prior_year" not in r["flag"]:
            cells[(int(r["period_fy"]), r["fund"])][want[r["line_id"]]] = cells[(int(r["period_fy"]), r["fund"])].get(want[r["line_id"]], 0) + int(r["amount"])
    rows = []
    for (fy, fund), c in sorted(cells.items(), key=lambda t: (t[0][1], t[0][0])):
        row = dict(fiscal_year=fy, fund=fund, **{k: c.get(k) for k in want.values()})
        row["unassigned_pct_of_spending"] = round(c["unassigned"] / c["expenditures"], 4) if c.get("unassigned") is not None and c.get("expenditures") else None
        row["exception_ids"] = []
        rows.append(row)
    return dict(
        title="Fund balances by fund",
        description="From each audit's governmental funds balance sheet and statement of revenues, expenditures and changes in fund balances. Several funds hold only restricted money, "
                    "so unassigned balance is the General Fund's measure; unassigned_pct_of_spending is that balance over the year's expenditures.",
        sources=["fund_statements.csv"],
        columns=dict(fiscal_year="fiscal year (December 31 year end)", fund="fund as the audit names it", fund_balance="total fund balance, dollars", unassigned="unassigned balance",
                     cash="cash and equivalents", revenues="total revenues", expenditures="total expenditures", net_change="net change in fund balance",
                     other_financing="total other financing sources (uses)", unassigned_pct_of_spending="unassigned / expenditures", exception_ids="exceptions"),
        rows=rows)


def park_debt(E, read):
    docs = {d["doc_id"]: d for d in read("documents.csv")}
    inst = {r["instrument_id"]: r["name"] for r in E.table("debt_instruments.csv")}
    by, total, ex = defaultdict(int), {}, defaultdict(set)
    for r in read("debt_balances.csv"):
        if r["measure"] != "ending":
            continue
        fy = int(r["period_fy"])
        if r["line_id"] == "total":
            total[fy] = int(r["amount"])
            continue
        g = DEBT_GROUPS.get(r["line_id"], "Bonds" if r["line_id"].startswith("park_bond") else None)
        if g is None:
            raise SystemExit(f"debt line {r['line_id']!r} has no group in export_park.DEBT_GROUPS")
        by[(fy, g)] += int(r["amount"])
        ex[(fy, g)].update(ids(r["exception_ids"]))
    for fy, t in total.items():
        assert sum(v for (y, _), v in by.items() if y == fy) == t, f"debt groups do not add to the audit's total for {fy}"
    rows = [dict(fiscal_year=fy, group=g, ending_balance=v, exception_ids=sorted(ex[(fy, g)])) for (fy, g), v in sorted(by.items())]
    return dict(
        title="Long-term liabilities by kind",
        description="Governmental activities long-term liabilities at December 31 from each audit's long-term debt note, grouped into bonds, other borrowing (agreement for deed, "
                    "subscription liabilities), bond premium, the IMRF net pension liability and compensated absences. The groups add to the audit's printed total in every year.",
        sources=["debt_balances.csv"],
        columns=dict(fiscal_year="fiscal year (audit year end December 31)", group="kind of liability", ending_balance="dollars", exception_ids="exceptions"),
        rows=rows)


PARK_DATASETS = {
    "park_revenue": (park_revenue, ["facts.csv"]),
    "park_spending": (park_spending, ["facts.csv"]),
    "levies": (levies, ["ordinance_lines.csv", "ordinance_summary.csv", "reference_by_fy.csv", "documents.csv"]),
    "park_audit_vs_budget": (park_audit_vs_budget, ["audit_vs_budget.csv"]),
    "park_fund_balance": (park_fund_balance, ["fund_statements.csv"]),
    "park_debt": (park_debt, ["debt_balances.csv"]),
}
