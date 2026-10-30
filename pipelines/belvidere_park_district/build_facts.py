"""Turn the park district's extracts into the canonical tables (govbudget.schema).

Usage: python3 pipelines/belvidere_park_district/build_facts.py

Inputs : entities/belvidere_park_district/*  (source_urls.csv, account_map.csv, departments.csv, funds.csv, exceptions.csv, entity.json, ...)
         data/belvidere_park_district/extracted/<doc>_{revenue,expense,totals,corrections}.csv     (the budget extractors)
         data/belvidere_park_district/extracted/audits/<doc>_*.csv                                (the audit extractors)
Outputs: data/belvidere_park_district/documents.csv, accounts.csv, facts.csv, control_totals.csv, corrections.csv, fund_statements.csv, the debt, pension
         and tax tables.

Nothing here classifies or sums anything: facts.csv holds every extracted number once, with its document, period, basis and provenance. Mapping decisions
live in the entity's CSVs; anomalies in exceptions.csv (matched to rows by account, and by document for a document-scoped exception).

How the park's columns become bases (document status from source_urls.csv):
  draft      budget / approved / requested / budget_original  -> requested   (a draft prints Requested; Amended is empty)
  adopted    budget (operating budget), approved, budget_original -> adopted
  adopted    budget_current -> revised, only where it differs from budget_original (a budget printed before any activity: they never differ so far)
  final      amended (the FY2023 amended reports) -> revised; their `original` column equals the FY2023 adopted budget on every account (checked) and is not repeated
The fiscal year is the calendar year: period_fy is the document's fiscal_year. Accounts carry the fund (govbudget.entity.Entity.split_key): fund 01 unprefixed
(`0100-310`, `010-3010`), the others `FF:DDDD-OOO`; both account formats are in the account map and years compare through the category.
"""
import csv
import glob
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402
from govbudget.schema import (ACCOUNT_COLUMNS, CONTROL_TOTAL_COLUMNS, CORRECTION_COLUMNS, DEBT_PENSION_TABLES, DOCUMENT_COLUMNS,  # noqa: E402
                              FACT_COLUMNS, FUND_STATEMENT_COLUMNS, TAX_DEBT_TABLES)

E = Entity.load("belvidere_park_district")


def whole_dollars(text):
    """Integer dollars from a printed amount (the park prints cents from FY2026); half-cents round away from zero."""
    return int(Decimal(str(text)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def exception_index():
    """account/doc exceptions as (scope, ids, fy_from, fy_to, bases, exception_id)."""
    out = []
    for x in E.exceptions():
        if x["scope"] in ("account", "doc"):
            out.append((x["scope"], set(x["scope_id"].split("|")), int(x["fy_from"] or 0), int(x["fy_to"] or 9999),
                        set(filter(None, x["bases"].split("|"))), x["exception_id"]))
    return out


def attach(ex, doc_id, key, period_fy, basis):
    ids = [i for scope, ids_, a, b, bases, i in ex
           if a <= period_fy <= b and (not bases or basis in bases)
           and ((scope == "account" and key in ids_) or (scope == "doc" and doc_id in ids_))]
    return "|".join(ids)


def account_key(account):
    """`01-0100-310` -> `0100-310` (General Fund, unprefixed), `02-1200-300` -> `02:1200-300`; the FY2025+ format `01-010-3010` the same way."""
    fund, dept, obj = account.split("-", 2)
    return f"{dept}-{obj}" if fund == "01" else f"{fund}:{dept}-{obj}"


def to_basis(status, column):
    """(basis or None) for a column of a document of this status; None = the column is not repeated as a fact (see the module docstring)."""
    if column in ("budget", "approved", "requested", "budget_original"):
        return "requested" if status == "draft" else "adopted"
    if column in ("budget_current", "amended"):
        return "revised"
    if column == "original":
        return None  # the amended reports' Original column = the FY2023 adopted budget (verified account by account)
    sys.exit(f"unrecognised budget column {column!r}")


# Budget documents whose extracts become facts. FY2024_budget (the adopted FY2024 budget) is not extracted: the draft stands in (EX-fy2024-budget-placeholder);
# FY2023_budget_amended_oct is the scanned copy of FY2023_budget_amended (identical figures, EX-fy2023-amended-two-copies): one copy only.
SKIP_DOCS = {"FY2024_budget", "FY2023_budget_amended_oct"}


def budget_part(ex, docs):
    """Facts, control totals and corrections from the budget extracts (every layout), in document order."""
    facts, totals, corrections = [], [], []
    for d in sorted((d for d in docs if d["doc_type"] in ("budget", "budget_amendment") and d["doc_id"] not in SKIP_DOCS),
                    key=lambda d: (int(d["fiscal_year"]), d["doc_id"])):
        doc_id, fy, status = d["doc_id"], int(d["fiscal_year"]), d["status"]
        if not os.path.exists(E.data_path("extracted", f"{doc_id}_revenue.csv")) and not os.path.exists(E.data_path("extracted", f"{doc_id}_expense.csv")):
            continue
        seq = 0  # row_seq numbers the extract rows of the document: the revenue file first, then the expense file
        for flow in ("revenue", "expense"):
            path = E.data_path("extracted", f"{doc_id}_{flow}.csv")
            if not os.path.exists(path):
                continue  # FY2022 has no revenue report
            last_original = {}
            for r in csv.DictReader(open(path)):
                seq += 1
                basis = to_basis(status, r["column"])
                if r["column"] == "budget_current":  # a budget printed before any activity: Current = Original; a difference would be an amendment
                    if str(last_original.get(r["account"])) == r["amount"]:
                        continue
                    basis = "revised"
                elif r["column"] == "budget_original":
                    last_original[r["account"]] = r["amount"]
                if basis is None:
                    continue
                key = account_key(r["account"])
                amount, flag = whole_dollars(r["amount"]), r["flag"].strip()
                if Decimal(r["amount"]) != amount:
                    flag = (flag + " rounded_from_cents").strip()  # the park prints cents (FY2026 and some older funds); facts are whole dollars
                fund = r["account"].split("-")[0]
                facts.append(dict(entity_id=E.id, doc_id=doc_id, source_type="budget", row_seq=seq, period_fy=fy, basis=basis, flow=flow,
                                  fund_code=fund, dept_code=r["dept_code"], account_key=key, account_printed=r["account"], audit_section="",
                                  label=r["line_item"], amount=amount, source_column=r["column"], page_label=r["page_label"], pdf_page=r["pdf_page"],
                                  flag=flag, exception_ids=attach(ex, doc_id, key, fy, basis)))
        totals += control_totals(doc_id, fy, status)
        corrections += corrections_of(doc_id)
    return facts, totals, corrections


def control_totals(doc_id, fy, status):
    """Printed totals of one document as control totals (kinds in govbudget.schema.TOTAL_KINDS); computed lines (`block_unprinted`) are not printed, so not here."""
    path = E.data_path("extracted", f"{doc_id}_totals.csv")
    if not os.path.exists(path):
        return []
    out = []
    rows = list(csv.DictReader(open(path)))
    basis_of = lambda col: to_basis(status, col) if col else ("requested" if status == "draft" else "adopted")  # noqa: E731
    for r in rows:
        col = r.get("column", "")
        if col == "budget_current" or col == "original":
            continue  # same as budget_original / the adopted budget; see to_basis
        basis = basis_of(col)
        amount = whole_dollars(r["amount"])
        if "fund_group" in r:  # layout A (operating budget): blocks, grand totals and the SUMMARY page
            if r["kind"] == "block_unprinted":
                continue
            kind = {"block": "section_total", "grand": "rollup_revenues" if r["flow"] == "revenue" else "rollup_expenses",
                    "summary": "rollup_revenues" if r["flow"] == "revenue" else "rollup_expenses"}[r["kind"]]
            name = r["name"] if r["kind"] != "summary" else f"{r['fund_group']}: {r['name']}"
        else:
            fund = r["fund_code"]
            if r["kind"] == "report_net":
                kind, name = "rollup_net", r["name"]
            elif r["kind"] in ("fund_revenues", "fund_expenses", "fund_net"):  # layout D
                kind, name = r["kind"], f"{fund} {r['fund']}: {r['name']}"
            elif r["kind"] == "fund_subtotal":  # layouts B and the amended reports: one subtotal per fund and flow
                kind = "fund_revenues" if r["flow"] == "revenue" else "fund_expenses"
                name = f"{fund} {r['name']}: Subtotal"
            else:  # grand
                kind = "rollup_revenues" if r["flow"] == "revenue" else "rollup_expenses"
                name = r["name"]
        out.append(dict(entity_id=E.id, doc_id=doc_id, period_fy=fy, basis=basis, kind=kind, name=name, amount=amount, page_label=r["page_label"]))
    return out


ACCT_IN_TEXT = re.compile(r"(\d{2,3})-{1,2}(\d{3,4})-{1,2}(\d{3,4})")
METHODS = {"line": "row_override", "line_prefix": "row_override", "add_row": "add_row", "auto_resolved": "auto_resolved",
           "auto_resolved_digit": "auto_resolved_digit"}


def corrections_of(doc_id):
    """The extractors' corrections files (a line the OCR read wrongly and an override or an automatic cross-reading replaced) in the canonical layout.
    The park's extractors record the printed line text, so the account is read from it where there is one; a corrected printed total has none."""
    path = E.data_path("extracted", f"{doc_id}_corrections.csv")
    if not os.path.exists(path):
        return []
    out = []
    for r in csv.DictReader(open(path)):
        m = ACCT_IN_TEXT.search(r["final_text"]) or ACCT_IN_TEXT.search(r["raw_text"])
        printed = f"{m.group(1)[-2:]}-{m.group(2)}-{m.group(3)}" if m else ""
        out.append(dict(entity_id=E.id, doc_id=doc_id, source_type="budget", target="line" if m else "total", fund=printed.split("-")[0] if m else "",
                        account_printed=printed, account_key=account_key(printed) if m else "", label="", source_column="", page_label=r["page_label"],
                        pdf_page=r["pdf_page"], raw_value=r["raw_text"], final_value=r["final_text"], method=METHODS[r["method"]], detail=r["detail"],
                        ocr_line=r["raw_text"]))
    return out


def audit_docs(docs):
    return sorted((d for d in docs if d["doc_type"] == "audit" and os.path.exists(E.data_path("extracted", "audits", f"{d['doc_id']}_budget_actual.csv"))),
                  key=lambda d: int(d["fiscal_year"]))


def audit_fund_codes():
    """audit schedule name -> (statement column name, fund code up to FY2024, fund code from FY2025): Museum and Special Recreation swap codes."""
    return {r["schedule_name"]: r for r in E.table("audit_fund_codes.csv")}


def audit_part(ex, docs):
    """Facts from the audits' budget-and-actual schedules (revenue and expenditure lines, as the city's audit facts are: totals, other financing and fund
    balance rows are not facts, they are in fund_statements.csv) and the audited fund statements (balance sheet, statement of changes)."""
    facts, fund_rows = [], []
    codes = audit_fund_codes()
    stmt_map = [(r["statement"], re.compile(r["label_pattern"], re.I), r["line_id"]) for r in E.table("fund_statement_lines.csv")]
    for d in audit_docs(docs):
        doc_id, fy = d["doc_id"], int(d["fiscal_year"])
        seq = 0
        rows = list(csv.DictReader(open(E.data_path("extracted", "audits", f"{doc_id}_budget_actual.csv"))))
        by = {}  # (fund, row_seq) -> the row's cells by column
        for r in rows:
            by.setdefault((r["fund"], int(r["row_seq"])), []).append(r)
        for (fund, _), cells in by.items():
            first = cells[0]
            sec = first["section"].split("; ")
            if sec[0] not in ("Revenues", "Expenditures", "Other financing sources (uses)") or first["line_item"].startswith("Total"):
                continue  # totals, excess, net change and fund balance rows are not facts
            seq += 1
            c = codes[fund]
            fund_code = c["fund_code_from_fy2025"] if fy >= 2025 else c["fund_code_to_fy2024"]
            financing = sec[0].startswith("Other financing")  # proceeds of bonds, leases and subscriptions, sales of assets: the budget documents count them as
            # revenue, so they are revenue facts of their own subsection (flag other_financing; a payment to an escrow agent is a negative one)
            base = dict(entity_id=E.id, doc_id=doc_id, source_type="audit", row_seq=seq, flow="expense" if sec[0] == "Expenditures" else "revenue",
                        fund_code=fund_code, dept_code="", account_key="", account_printed="",
                        audit_section=f"{c['statement_column']} | {'Other financing sources' if financing else (sec[-1] if len(sec) > 1 else '')} | ",
                        label=first["line_item"], page_label="", pdf_page=first["pdf_page"], flag="other_financing" if financing else "")
            for cell in cells:
                col, amount = cell["column"], int(cell["amount"])
                if col == "budget":  # one Original & Final column: the budget was not amended, so it is both
                    bases = [("audit_original", fy, "combined_column"), ("audit_final", fy, "combined_column")]
                elif col == "budget_original":
                    bases = [("audit_original", fy, "")]
                elif col == "budget_final":
                    bases = [("audit_final", fy, "")]
                elif col == "actual":
                    bases = [("actual_audited", fy, "")]
                elif col == "prior_year_actual":
                    bases = [("actual_audited", fy - 1, "prior_year_comparative")]
                else:
                    continue  # variance: derived
                for basis, period, flag in bases:
                    facts.append(dict(base, period_fy=period, basis=basis, amount=amount, source_column=col,
                                      flag=" ".join(x for x in (base["flag"], flag) if x),
                                      exception_ids=attach(ex, doc_id, "", period, basis)))
        for r in csv.DictReader(open(E.data_path("extracted", "audits", f"{doc_id}_fund_statements.csv"))):
            key = f"{r['section']} | {r['line_item']}"
            statement = {"balance_sheet": "balance_sheet", "changes": "changes_in_fund_balance"}[r["statement"]]
            lid = next((i for st, rx, i in stmt_map if st == statement and rx.search(key)), "")
            if not lid:
                sys.exit(f"{doc_id} {r['statement']}: no line_id for {key!r} (entities/{E.id}/fund_statement_lines.csv)")
            parts = r["section"].split("; ", 1)
            fund_rows.append(dict(entity_id=E.id, doc_id=doc_id, period_fy=fy, statement=statement, fund=r["fund"], section=parts[0],
                                  subsection=parts[1] if len(parts) > 1 else "", line_item=r["line_item"], line_id=lid, amount=int(r["amount"]),
                                  pdf_page=r["pdf_page"], flag=""))
    return facts, fund_rows


PENSION_LINES = [  # (regex on the schedule's label, line_id, kind): dollar lines are the roll-forward, ratios and payroll the summary
    (r"^Service Cost", "service_cost", "roll"), (r"^Interest on the Total", "interest_on_tpl", "roll"), (r"^Benefit Changes", "benefit_changes", "roll"),
    (r"Expected and Actual", "experience_difference", "roll"), (r"^Assumption Changes", "assumption_changes", "roll"),
    (r"^Benefit Payments and Refunds$", "benefit_payments_tpl", "roll"), (r"^Net Change in Total Pension", "net_change_tpl", "roll"),
    (r"Total Pension Liability - Beginning", "tpl_beginning", "roll"), (r"Total Pension Liability - Ending", "tpl_ending", "roll"),
    (r"^Employer Contributions", "employer_contributions", "roll"), (r"^Employee Contributions", "employee_contributions", "roll"),
    (r"Net Investment Income", "net_investment_income", "roll"), (r"^Benefit Payments and Refunds \(2\)", "benefit_payments_fnp", "roll"),
    (r"^Other \(Net Transfer\)", "other_net_transfer", "roll"), (r"^Net Change in Plan Fiduciary", "net_change_fnp", "roll"),
    (r"Plan Fiduciary Net Position - Beginning", "fnp_beginning", "roll"), (r"Plan Fiduciary Net Position - Ending", "fnp_ending", "roll"),
    (r"^Net Pension Liability/\(Asset\)", "npl_ending", "roll"), (r"Plan Fiduciary Net Position as a Percentage", "funded_ratio", ("funded ratio", "percent")),
    (r"^Covered Valuation Payroll", "covered_payroll", ("covered valuation payroll", "usd")),
    (r"^Net Pension Liability as a Percentage", "npl_percent_of_payroll", ("net pension liability as a percentage of covered payroll", "percent"))]
DEBT_EXCEPTIONS = {("debt_service_schedule", "FY2020_audit", "2031-2032"): "EX-fy2020-debt-schedule-row",
                   ("debt_balances", "FY2024_audit", "compensated_absences", "beginning"): "EX-gasb101-compensated-absences",
                   ("debt_balances", "FY2020_audit", "alt_rev_2012A", "retired"): "EX-fy2020-advance-refunding"}


def audit_extract(doc_id, name):
    path = E.data_path("extracted", "audits", f"{doc_id}_{name}.csv")
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def debt_part(docs):
    """The debt, pension and tax tables of the audits (extract_audit_debt.py, extract_audit_imrf.py) in the canonical layouts."""
    out = {name: [] for name in list(DEBT_PENSION_TABLES) + list(TAX_DEBT_TABLES)}
    inst = {r["instrument_id"]: r for r in E.table("debt_instruments.csv")}
    ACT = "governmental activities"
    for d in audit_docs(docs):
        doc, fy = d["doc_id"], int(d["fiscal_year"])
        base = dict(entity_id=E.id, doc_id=doc, period_fy=fy, flag="")
        for name, measures in (("debt_outstanding", None), ("debt_activity", None)):
            for r in audit_extract(doc, name):
                lid = r["instrument_id"] or "total"
                ex_id = DEBT_EXCEPTIONS.get(("debt_balances", doc, lid, r["column"]), "")
                out["debt_balances"].append(dict(base, activity=ACT, line_id=lid, line_item=r["description"].split(";")[0].strip(), measure=r["column"],
                                                 amount=int(r["amount"]), pdf_page=r["pdf_page"], exception_ids=ex_id))
        for r in audit_extract(doc, "debt_outstanding"):  # bond terms, read from the description
            i = inst.get(r["instrument_id"])
            if r["column"] != "balance" or not i or i["kind"] not in ("go_bond", "alt_revenue_bond", "alt_revenue_refunding"):
                continue
            cur = next(x for x in audit_extract(doc, "debt_outstanding") if x["description"] == r["description"] and x["column"] == "current_portion")
            rate = re.search(r"interest rates?\s+(?:of\s+|vary from\s+)?([\d.]+%(?:\s+to\s+[\d.]+%)?)", r["description"])
            mat = re.search(r"(Principal[^;]*?(?:\d{4}|maturity[^;]*))", r["description"])
            out["debt_issue_terms"].append(dict(base, activity=ACT, issue=i["name"], interest_rate_text=rate.group(1) if rate else "",
                                                maturity_text=mat.group(1).strip() if mat else "", ending_balance=int(r["amount"]),
                                                current_portion=int(cur["amount"]), description=" ".join(r["description"].split()),
                                                pdf_page=r["pdf_page"], exception_ids=""))
        sched = {}
        for r in audit_extract(doc, "debt_schedule"):
            sched.setdefault((r["period"], r["instrument_id"]), {})[r["kind"]] = (int(r["amount"]), r["pdf_page"])
        for (period, i), v in sched.items():
            p, itr = v["principal"][0], v["interest"][0]
            if p == 0 and itr == 0:
                continue
            out["debt_service_schedule"].append(dict(base, activity=ACT, instrument=inst[i]["name"] if i in inst else "Total", line_id=i or "total",
                                                     year_ending=period, principal=p, interest=itr, total=p + itr,
                                                     row_kind="total" if period == "Total" else "range" if "-" in period else "year",
                                                     pdf_page=v["principal"][1], exception_ids=DEBT_EXCEPTIONS.get(("debt_service_schedule", doc, period), "")))
        for r in audit_extract(doc, "imrf_changes"):
            lid, kind = next((i, k) for rx, i, k in PENSION_LINES if re.search(rx, r["line_item"], re.I))
            if kind == "roll":
                out["pension_rollforward"].append(dict(base, plan_id="imrf", source="audit required supplementary information",
                                                       measurement_date=f"{r['calendar_year']}-12-31", column_year=r["calendar_year"], component=r["line_item"],
                                                       line_id=lid, amount=r["amount"], pdf_page=r["pdf_page"], exception_ids=""))
            else:
                out["pension_summary"].append(dict(base, plan_id="imrf", measure=kind[0], period_label=r["calendar_year"], value=r["amount"], value_text="",
                                                   unit=kind[1], source="audit required supplementary information", pdf_page=r["pdf_page"], exception_ids=""))
        for r in audit_extract(doc, "imrf_contributions"):
            for measure, col, unit in (("actuarially determined contribution", "actuarially_determined", "usd"), ("actual contribution", "actual", "usd"),
                                       ("contribution deficiency (excess)", "deficiency", "usd"), ("covered payroll (contributions schedule)", "covered_payroll", "usd"),
                                       ("actual contribution as a percentage of covered payroll", "percent_of_payroll", "percent")):
                out["pension_summary"].append(dict(base, plan_id="imrf", measure=measure, period_label=r["calendar_year"], value=r[col], value_text="",
                                                   unit=unit, source="audit schedule of contributions", pdf_page=r["pdf_page"], exception_ids=""))
        for r in audit_extract(doc, "legal_debt_margin"):
            out["legal_debt_margin"].append(dict(base, schedule=r["schedule"], line_item=r["line_item"], column=r["column"], amount=r["amount"],
                                                 pdf_page=r["pdf_page"], exception_ids=""))
        for r in audit_extract(doc, "assessed_valuations"):
            out["assessed_valuations"].append(dict(base, tax_year=r["tax_year"], section=r["section"], line_item=r["line_item"], amount=r["amount"],
                                                   pdf_page=r["pdf_page"], exception_ids=""))
    return out


def ordinance_part(ex, docs):
    """Appropriation and levy ordinances (extract_ordinance_appropriation.py, extract_ordinance_levy.py): fund-level lines and the ordinances' own summaries.
    Amounts keep the cents the ordinances print; `printed` is 1 on a line the ordinance prints as a total."""
    lines, summary = [], []
    for d in docs:
        if d["doc_type"] not in ("appropriation", "levy"):
            continue
        doc, fy, kind = d["doc_id"], int(d["fiscal_year"]), d["doc_type"]
        for r in read_csv(E.data_path("extracted", "ordinances", f"{doc}_lines.csv")):
            lines.append(dict(entity_id=E.id, doc_id=doc, period_fy=fy, ordinance=kind, fund_code=r["fund_code"], fund=r["fund"],
                              general_fund_part=r["general_fund_part"], section=r["section"], line_item=r["line_item"], amount=r["amount"],
                              printed=r["printed"], pdf_page=r["pdf_page"], flag="", exception_ids=attach(ex, doc, "", fy, "")))
        for r in read_csv(E.data_path("extracted", "ordinances", f"{doc}_summary.csv")):
            summary.append(dict(entity_id=E.id, doc_id=doc, period_fy=fy, ordinance=kind, item=r["item"], amount=r["amount"], pdf_page=r["pdf_page"],
                                flag="", exception_ids=attach(ex, doc, "", fy, "")))
    return lines, summary


def documents():
    """One row per source document (the 44 the district posts that this project holds), whether or not facts were extracted from it yet."""
    inv = {r["doc_id"]: r for r in read_csv(E.data_path("source_inventory.csv"))}
    out = []
    for r in E.table("source_urls.csv"):
        i = inv[r["doc_id"]]
        out.append(dict(entity_id=E.id, doc_id=r["doc_id"], doc_type=r["doc_type"], fiscal_year=r["fiscal_year"], status=r["status"],
                        pdf_path=r["pdf_path"], sha256=i["sha256"], source_url=r["url"],
                        note=("; ".join(x for x in (r["note"], f"{i['layer']} layer, {i['pages']} pages") if x))))
    return out


def accounts(facts):
    amap = E.account_map()
    first, last, label = {}, {}, {}
    for f in facts:
        if f["source_type"] == "budget" and f["basis"] == "adopted":
            k = (f["flow"], f["account_key"])
            first[k] = min(first.get(k, 9999), f["period_fy"])
            last[k] = max(last.get(k, 0), f["period_fy"])
            label[k] = f["label"]  # facts are in fiscal-year order, so the newest label wins
    rows = []
    for (flow, key), m in sorted(amap.items()):
        rows.append(dict(entity_id=E.id, flow=flow, account_key=key, label_current=m["label"] or label.get((flow, key), ""),
                         dept_code=E.split_key(key)[1].split("-")[0], department=m["department"], category_id=m["category_id"],
                         treatment=m["treatment"], first_fy=first.get((flow, key), ""), last_fy=last.get((flow, key), ""), note=m["note"]))
    return rows


def main():
    from govbudget.validate import validate_config
    errs = validate_config(E)
    if errs:
        sys.exit("config problems:\n  " + "\n  ".join(errs))
    docs = documents()
    ex = exception_index()
    facts, totals, corrections = budget_part(ex, docs)
    a_facts, fund_rows = audit_part(ex, docs)
    facts += a_facts
    write_csv(E.data_path("documents.csv"), DOCUMENT_COLUMNS, docs)
    write_csv(E.data_path("accounts.csv"), ACCOUNT_COLUMNS, accounts(facts))
    write_csv(E.data_path("facts.csv"), FACT_COLUMNS, facts)
    write_csv(E.data_path("control_totals.csv"), CONTROL_TOTAL_COLUMNS, totals)
    write_csv(E.data_path("corrections.csv"), CORRECTION_COLUMNS, corrections)
    write_csv(E.data_path("fund_statements.csv"), FUND_STATEMENT_COLUMNS, fund_rows)
    tables = debt_part(docs)
    tables["ordinance_lines"], tables["ordinance_summary"] = ordinance_part(ex, docs)
    for name, cols in {**DEBT_PENSION_TABLES, **TAX_DEBT_TABLES}.items():
        if tables[name]:
            write_csv(E.data_path(f"{name}.csv"), cols, tables[name])
    print(f"{len(docs)} documents, {len(accounts(facts))} accounts, {len(facts)} facts, {len(totals)} control totals, {len(corrections)} corrections "
          f"{len(fund_rows)} fund statement cells, " + ", ".join(f"{len(v)} {k}" for k, v in tables.items() if v) + f" -> data/{E.id}/")
    from govbudget.validate import validate_tables
    errs = validate_tables(E)
    if errs:
        sys.exit(f"{len(errs)} integrity problems:\n  " + "\n  ".join(errs[:30]))
    print("integrity checks pass")


if __name__ == "__main__":
    main()
