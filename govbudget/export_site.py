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
import os
import shutil
import sys
from collections import defaultdict

from . import palette, schema
from .entity import ROOT, Entity, read_csv

DEFAULT_OUT = os.path.join(ROOT, "web", "public", "data")


def ids(s):
    return sorted(i for i in (s or "").split("|") if i)


def num(s):
    return int(s) if s not in ("", None) else None


def in_range(ex, fy):
    lo = int(ex["fy_from"]) if ex["fy_from"] else -10**9
    hi = int(ex["fy_to"]) if ex["fy_to"] else 10**9
    return lo <= fy <= hi


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
    dump(os.path.join(base, "exceptions.json"), dict(
        entity_id=E.id, kinds=list(schema.EXCEPTION_KINDS), exceptions=[
            dict(e, fy_from=num(e["fy_from"]), fy_to=num(e["fy_to"]), bases=[b for b in e["bases"].split("|") if b])
            for e in E.exceptions()]))

    dl = os.path.join(base, "downloads")
    os.makedirs(dl, exist_ok=True)
    for s in sorted(copies):
        shutil.copyfile(E.data_path(s), os.path.join(dl, s))
    if os.path.exists(E.data_path("facts.csv")):
        with open(E.data_path("facts.csv"), "rb") as src, gzip.GzipFile(os.path.join(dl, "facts.csv.gz"), "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
    return dict(entity_id=E.id, name=cfg["name"], short_name=cfg.get("short_name", cfg["name"]), datasets=written)


def export(entity_ids=None, out=DEFAULT_OUT):
    if not entity_ids:
        entity_ids = sorted(d for d in os.listdir(os.path.join(ROOT, "entities"))
                            if os.path.exists(os.path.join(ROOT, "entities", d, "entity.json")))
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
