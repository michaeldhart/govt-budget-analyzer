"""Datasets for Boone County (entity.json "export_profile": "county"): audited spending and revenue against the budget, fund balances of the ~60 funds, long-term
liabilities, the property tax levies and collections, and the retirement plans. The budget datasets (fund_revenue, fund_spending) are the fund-organised builders of
export_park.py; the three below replace the park district's versions of fund_audit_vs_budget, fund_balances and fund_debt for a county. Same envelope and conventions
as export_site (exception_ids on every row)."""
from collections import defaultdict

from .export_common import ids, in_range, num

NONMAJOR_NAMES = {"Nonmajor Governmental", "Other Governmental", "Nonmajor Governmental Funds"}
ALL_NONMAJOR = {"Total", "Total Nonmajor Governmental", "Total Nonmajor Governmental Funds"}
FUND_ALIASES = {"General": "General Fund", "County Motor Fuel Tax": "Motor Fuel Tax", "IMRF/ Retirement": "IMRF/Retirement", "Total Governmental": "All governmental funds"}
DEBT_GROUPS = {"bonds_payable": "Bonds and debt certificates", "debt_certificates": "Bonds and debt certificates", "bond_premium": "Bonds and debt certificates",
               "notes_payable": "Notes, leases and subscriptions", "leases": "Notes, leases and subscriptions", "sbita": "Notes, leases and subscriptions",
               "compensated_absences": "Compensated absences", "landfill_postclosure": "Landfill post-closure care", "opeb": "Retiree health benefits (OPEB)",
               "npl_imrf": "Net pension liabilities", "npl_slep": "Net pension liabilities", "npl_eco": "Net pension liabilities"}
PLAN_NAME = {"imrf_regular": "IMRF County Regular", "imrf_eco": "IMRF Elected County Officials", "slep": "Sheriff's Law Enforcement Personnel (SLEP)"}


def audit_exceptions(E, fy, prefixes=("EX-audit", "EX-health-insurance")):
    return sorted(e["exception_id"] for e in E.exceptions() if e["scope"] == "entity" and e["exception_id"].startswith(prefixes) and in_range(e, fy))


def audit_vs_budget(E, read):
    funds = {r["fund_code"]: r["name"] for r in E.table("funds.csv")}

    def n(x):
        return None if x in ("", None) else int(float(x))

    def var(b, a):
        return None if not b or a is None else round((a - b) / b, 6)
    cells = defaultdict(dict)
    for r in read("audit_vs_budget.csv"):
        if not r["fund_code"] or r["measure"] not in ("revenue", "expenditure"):
            continue  # audit schedules of funds that have no budget fund (the Maplecrest memorial funds, office automation ...) are in the audit's own tables
        cells[(int(r["period_fy"]), r["fund_code"])][r["measure"]] = r
    rows = []
    for (fy, code), m in sorted(cells.items(), key=lambda t: (t[0][1], t[0][0])):
        rev, exp = m.get("revenue"), m.get("expenditure")
        rb, ra = (n(rev["audit_budget"]), n(rev["audit_actual"])) if rev else (None, None)
        eb, ea = (n(exp["audit_budget"]), n(exp["audit_actual"])) if exp else (None, None)
        basis = next((x["budget_basis"] for x in (rev, exp) if x and x["budget_basis"]), "")
        rows.append(dict(fiscal_year=fy, fund=funds.get(code, code), fund_code=code, revenue_budget=rb, revenue_actual=ra, revenue_variance_pct=var(rb, ra),
                         expense_budget=eb, expense_actual=ea, expense_variance_pct=var(eb, ea), budget_basis=basis,
                         revenue_matches=bool(rev and rev["budget_status"] == "agree"), expense_matches=bool(exp and exp["budget_status"] == "agree"),
                         exception_ids=audit_exceptions(E, fy)))
    return dict(
        title="Audited revenue and spending against the budget, by fund",
        description="The audit's budget-and-actual schedule of each fund beside the county's budget. The audit prints the budget it was given (the original budget; the county does not "
                    "amend during the year); revenue_matches and expense_matches say whether that equals the budget document's fund total after the audit's presentation rules "
                    "(transfers and sale proceeds are other financing in the audit; the General Fund's employee health insurance is a transfer out in the budget books). variance_pct is "
                    "(actual - budget) / budget. Funds the audit shows but the budgets do not carry are not included.",
        sources=["audit_vs_budget.csv"],
        columns=dict(fiscal_year="fiscal year (December 1 to November 30)", fund="fund name", fund_code="county fund code", revenue_budget="audit budget revenues, dollars",
                     revenue_actual="audited revenues, dollars", revenue_variance_pct="(actual - budget) / budget", expense_budget="audit budget expenditures, dollars",
                     expense_actual="audited expenditures, dollars", expense_variance_pct="(actual - budget) / budget", budget_basis="basis of the budget document compared (adopted, or prior_doc_budget)",
                     revenue_matches="the audit's budget revenues equal the document's", expense_matches="the audit's budget expenditures equal the document's",
                     exception_ids="entity-wide exceptions about this comparison"),
        rows=rows)


def fund_balances(E, read):
    want = {"total_fund_balance": "fund_balance", "unassigned": "unassigned", "cash": "cash", "total_revenues": "revenues", "total_expenditures": "expenditures",
            "net_change_in_fund_balance": "net_change", "total_other_financing": "other_financing"}
    order = {"balance_sheet": 0, "changes_in_fund_balance": 0, "combining_balance_sheet": 1, "combining_changes_in_fund_balance": 1}  # the governmental funds statement first
    cells = defaultdict(dict)
    for r in read("fund_statements.csv"):
        if r["line_id"] not in want or "prior_year" in r["flag"] or r["statement"] not in order:
            continue
        name = r["fund"].strip()
        if r["statement"] in ("balance_sheet", "changes_in_fund_balance") and name in NONMAJOR_NAMES:
            continue  # replaced by the nonmajor funds one by one (combining statements)
        if r["statement"].startswith("combining") and name in ALL_NONMAJOR:
            name = "All nonmajor funds"
        name = FUND_ALIASES.get(name.replace("(Formerly Nonmajor) ", ""), name.replace("(Formerly Nonmajor) ", ""))
        k, f = (int(r["period_fy"]), name), want[r["line_id"]]
        prev = cells[k].get(f)
        if prev is None or order[r["statement"]] < prev[1]:
            cells[k][f] = (int(r["amount"]), order[r["statement"]])
    rows = []
    for (fy, fund), c in sorted(cells.items(), key=lambda t: (t[0][1], t[0][0])):
        row = dict(fiscal_year=fy, fund=fund, **{k: (c[k][0] if k in c else None) for k in want.values()})
        row["unassigned_pct_of_spending"] = round(row["unassigned"] / row["expenditures"], 4) if row["unassigned"] is not None and row["expenditures"] else None
        row["exception_ids"] = sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"] in ("EX-audit-funds-without-schedule", "EX-fund-statement-columns") and in_range(e, fy))
        rows.append(row)
    return dict(
        title="Fund balances by fund",
        description="From each audit's governmental funds statements (the General Fund, Motor Fuel Tax, County Bridge, IMRF/Retirement and other major funds of the year) and its "
                    "combining statements of the nonmajor funds (about 60 funds). 'All governmental funds' and 'All nonmajor funds' are the printed totals. Which funds are major "
                    "changes from audit to audit, so a fund can move between the two sources; the names are as the audit prints them (a few are aliased to one name).",
        sources=["fund_statements.csv"],
        columns=dict(fiscal_year="fiscal year (year ended November 30)", fund="fund as the audit names it", fund_balance="total fund balance, dollars", unassigned="unassigned balance",
                     cash="cash and investments", revenues="total revenues", expenditures="total expenditures", net_change="net change in fund balance",
                     other_financing="total other financing sources (uses)", unassigned_pct_of_spending="unassigned / expenditures", exception_ids="exceptions"),
        rows=rows)


def debt(E, read):
    by, total, ex = defaultdict(int), {}, defaultdict(set)
    for r in read("debt_balances.csv"):
        if r["measure"] != "ending":
            continue
        fy = int(r["period_fy"])
        if r["line_id"] == "total":
            total[fy] = int(r["amount"])
            continue
        g = DEBT_GROUPS.get(r["line_id"])
        if g is None:
            raise SystemExit(f"debt line {r['line_id']!r} has no group in export_county.DEBT_GROUPS")
        by[(fy, g)] += int(r["amount"])
        ex[(fy, g)].update(ids(r["exception_ids"]))
    for fy, t in total.items():
        got = sum(v for (y, _), v in by.items() if y == fy)
        assert got == t, f"debt groups do not add to the audit's total for {fy}: {got} vs {t}"
    rows = [dict(fiscal_year=fy, group=g, ending_balance=v, exception_ids=sorted(set(ex[(fy, g)]) | {"EX-liability-restatements"})) for (fy, g), v in sorted(by.items())]
    return dict(
        title="Long-term liabilities by kind",
        description="Governmental activities long-term liabilities at November 30 from each audit's long-term debt note, grouped into bonds and debt certificates (with premium), notes, "
                    "leases and subscription arrangements, compensated absences, landfill post-closure care, retiree health benefits and the net pension liabilities. The groups add to "
                    "the audit's printed total in every year; the composition of the total changes when the pension and OPEB liabilities enter the table (FY2016-FY2018).",
        sources=["debt_balances.csv"],
        columns=dict(fiscal_year="fiscal year (year ended November 30)", group="kind of liability", ending_balance="dollars", exception_ids="exceptions"),
        rows=rows)


def property_tax(E, read):
    """Assessed valuation, each levy's rate and extension, collections and the percentage collected, by tax year (the audits' Property Tax Levies and Collections table).
    Every tax year appears in several audits (identical figures); the earliest audit that prints it is used."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    first = {}
    for r in read("property_tax_levies.csv"):
        k = (int(r["tax_year"]), r["section"], r["line_item"].strip().lower())
        if k not in first or int(r["period_fy"]) < int(first[k]["period_fy"]):
            first[k] = r
    rows = []
    names = {}
    for (ty, section, key), r in first.items():
        if section in ("extension", "collection"):
            names.setdefault(key, " ".join(w if w.isupper() else w.capitalize() if i == 0 else w for i, w in enumerate(r["line_item"].strip().split())))
    tax_years = sorted({k[0] for k in first})
    for ty in tax_years:
        def g(section, key="assessed valuation"):
            return first.get((ty, section, key))
        av, tot_ext, tot_col, pct = g("assessed_valuation"), g("extension_total", "total"), g("collection_total", "total"), g("pct_collected", "percentage collected")
        doc = next(iter(v for k, v in first.items() if k[0] == ty))
        rows.append(dict(tax_year=ty, fiscal_year=ty + 1, levy="All levies", assessed_valuation=num(av["amount"]) if av else None,
                         rate_per_100=float(tot_ext["rate"]) if tot_ext and tot_ext["rate"] != "" else None, extension=num(tot_ext["amount"]) if tot_ext else None,
                         collected=num(tot_col["amount"]) if tot_col else None, pct_collected=float(pct["rate"]) if pct and pct["rate"] != "" else None,
                         pdf_page=num(doc["pdf_page"]), source_url=urls[doc["doc_id"]],
                         exception_ids=sorted({i for k, v in first.items() if k[0] == ty for i in ids(v["exception_ids"])})))
        for key in sorted(names):
            e, c = first.get((ty, "extension", key)), first.get((ty, "collection", key))
            if not e and not c:
                continue
            rows.append(dict(tax_year=ty, fiscal_year=ty + 1, levy=names[key], assessed_valuation=None,
                             rate_per_100=float(e["rate"]) if e and e["rate"] != "" else None, extension=num(e["amount"]) if e else None,
                             collected=num(c["amount"]) if c else None, pct_collected=None,
                             pdf_page=num((e or c)["pdf_page"]), source_url=urls[(e or c)["doc_id"]], exception_ids=ids((e or c)["exception_ids"])))
    return dict(
        title="Property tax levies, extensions and collections",
        description="The audit's Property Tax Levies and Collections table: for each tax year the assessed valuation, the rate and the extension of each levy (county general, "
                    "bonds and interest, IMRF, highway, bridge, matching, health, liability insurance, social security, cooperative extension, senior citizen, veterans) and what was collected. "
                    "A tax year's bills are collected in the next fiscal year (tax year 2023 in FY2024). The row 'All levies' carries the total rate, the total extension, the total collected "
                    "and the percentage collected.",
        sources=["property_tax_levies.csv", "documents.csv"],
        columns=dict(tax_year="the levy year", fiscal_year="the fiscal year in which most of it is collected (tax year + 1)", levy="levy name, or All levies",
                     assessed_valuation="equalized assessed valuation, dollars (All levies row)", rate_per_100="rate per $100 of assessed valuation", extension="extension, dollars",
                     collected="collections, dollars", pct_collected="percentage collected (All levies row)", pdf_page="page of the audit PDF", source_url="the audit on boonecountyil.gov",
                     exception_ids="exceptions"),
        rows=rows)


def retirement(E, read):
    """Net pension liability, plan assets and funded ratio of the three IMRF plans by measurement year (the latest audit that prints each figure, which carries IMRF's
    restatements of 2014 and 2015) and the total OPEB liability."""
    urls = {d["doc_id"]: d["source_url"] for d in read("documents.csv")}
    latest = {}
    for r in read("pension_rollforward.csv"):
        if r["plan_id"] in PLAN_NAME and r["source"] == "rsi_npl" and r["line_id"] in ("liability_ending", "fnp_ending", "npl", "contributions_employer"):
            k = (r["plan_id"], int(r["column_year"]), r["line_id"])
            if k not in latest or int(r["period_fy"]) > int(latest[k]["period_fy"]):
                latest[k] = r
    payroll, adc, paid = {}, {}, {}
    for r in read("pension_rollforward.csv"):
        if r["plan_id"] in PLAN_NAME and r["source"] == "rsi_contributions":
            if r["line_id"] == "actuarially_determined_contribution":
                adc[(r["plan_id"], int(r["column_year"]))] = int(r["amount"])
            elif r["line_id"] == "contributions_actual":
                paid[(r["plan_id"], int(r["column_year"]))] = int(r["amount"])
    for r in read("pension_summary.csv"):
        if r["plan_id"] in PLAN_NAME and r["measure"] == "covered_payroll" and r["source"] == "rsi_npl":
            k = (r["plan_id"], int(r["period_label"] or r["period_fy"]))
            payroll[k] = int(float(r["value"]))
    rows = []
    for plan in PLAN_NAME:
        for my in sorted({k[1] for k in latest if k[0] == plan}):
            g = lambda lid: (int(latest[(plan, my, lid)]["amount"]) if (plan, my, lid) in latest else None)  # noqa: E731
            tpl, fnp, npl = g("liability_ending"), g("fnp_ending"), g("npl")
            if fnp is None and tpl is not None and npl is not None:
                fnp = tpl - npl  # net pension liability = liability - assets
            src = next(latest[(plan, my, i)] for i in ("npl", "liability_ending", "fnp_ending") if (plan, my, i) in latest)
            rows.append(dict(plan=plan, plan_name=PLAN_NAME[plan], measurement_year=my, fiscal_year=my + 1, tpl=tpl, fnp=fnp, npl=npl,
                             funded_ratio_pct=round(fnp / tpl * 100, 2) if tpl and fnp is not None else None, covered_payroll=payroll.get((plan, my)),
                             pdf_page=num(src["pdf_page"]), source_url=urls[src["doc_id"]],
                             exception_ids=sorted(e["exception_id"] for e in E.exceptions() if e["exception_id"] == "EX-pension-restated-2014-2015" and my <= 2015)))
    opeb = {}
    for r in read("opeb_rollforward.csv"):
        if r["plan_id"] == "opeb" and r["line_id"] == "liability_ending":
            k = int(r["column_year"])
            if k not in opeb or int(r["period_fy"]) > int(opeb[k]["period_fy"]):
                opeb[k] = r
    for my, r in sorted(opeb.items()):
        rows.append(dict(plan="opeb", plan_name="Retiree health benefits (OPEB)", measurement_year=my, fiscal_year=my, tpl=int(r["amount"]), fnp=0, npl=int(r["amount"]),
                         funded_ratio_pct=0.0, covered_payroll=None, pdf_page=num(r["pdf_page"]), source_url=urls[r["doc_id"]], exception_ids=[]))
    return dict(
        title="Retirement plans: net pension liability and OPEB",
        description="The county's three IMRF-administered pension plans (County Regular, Elected County Officials, Sheriff's Law Enforcement Personnel) by measurement year (December 31; "
                    "the audit for the fiscal year ending the next November reports it): total pension liability, plan assets (fiduciary net position), net pension liability "
                    "(liability minus assets; negative is a net pension asset) and the funded ratio. The retiree health plan (OPEB) is unfunded: its total OPEB liability is measured "
                    "at November 30 from FY2018 (GASB 75). Figures are the latest audit that prints each year, which carries IMRF's restatement of 2014 and 2015.",
        sources=["pension_rollforward.csv", "pension_summary.csv", "opeb_rollforward.csv", "documents.csv"],
        columns=dict(plan="plan id", plan_name="display name", measurement_year="measurement date year (December 31; OPEB November 30)", fiscal_year="the audit year that reports it",
                     tpl="total pension liability (OPEB: total OPEB liability), dollars", fnp="plan fiduciary net position, dollars (0 for OPEB)",
                     npl="net pension liability (negative: net pension asset), dollars", funded_ratio_pct="plan assets as a percent of the total liability",
                     covered_payroll="covered valuation payroll, dollars", pdf_page="page of the audit PDF", source_url="the audit on boonecountyil.gov",
                     exception_ids="exceptions"),
        rows=rows)
