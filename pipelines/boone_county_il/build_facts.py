"""Turn Boone County's extracts into the canonical tables (govbudget.schema).

Usage: python3 pipelines/boone_county_il/build_facts.py

Inputs : entities/boone_county_il/*  (source_urls.csv, account_map.csv, departments.csv, funds.csv, audit_fund_names.csv, fund_statement_lines.csv, exceptions.csv)
         data/boone_county_il/extracted/<doc>_{lines,totals}.csv                   (extract_budget_worksheet.py: FY2025, FY2026, FY2027 preliminary)
         data/boone_county_il/extracted/audits/<doc>_{gf_schedule,fund_schedules,fund_statements}.csv
Outputs: data/boone_county_il/{documents,accounts,facts,control_totals,corrections,fund_statements}.csv

Nothing here classifies or sums anything: facts.csv holds every extracted number once, with its document, period, basis and provenance.

Worksheet columns -> bases (a document's `measure`, the period is the fiscal year the column is for):
  final   the document's own "Final Budget" (the budget as adopted)                     -> adopted        (a preliminary document's "Prelim Budget" -> requested)
  budget  a prior year's "Total Budget" printed in a later document                      -> prior_doc_budget
  activity a closed year's "Total Activity"                                              -> actual_prelim  (the audit's actual supersedes it)
  ytd     year to date through a date in the document's year                             -> not a fact (EX-ytd-columns-not-facts)
Audits: "Original and Final Budget" -> audit_original and audit_final (flag combined_column); a lone "Final Budget" -> audit_final (flag final_budget_column);
"Actual" -> actual_audited. Other financing (transfers, proceeds) are revenue facts with flag other_financing (a transfer out is negative); totals, excess, net
change and balance rows are not facts (the funds' statements are in fund_statements.csv). Account keys carry the fund: `001:0051-401000`.
"""
import csv
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402
from govbudget.schema import (ACCOUNT_COLUMNS, CONTROL_TOTAL_COLUMNS, CORRECTION_COLUMNS, DOCUMENT_COLUMNS, FACT_COLUMNS,  # noqa: E402
                              FUND_STATEMENT_COLUMNS)
from build_audit_fund_names import norm  # noqa: E402

E = Entity.load("boone_county_il")
BUDGET_DOCS = ("FY2025_budget", "FY2026_budget", "FY2027_budget_prelim")
COMBINED_COLUMN_YEARS = {2015, 2016, 2017, 2024, 2025}  # audits that print "Original and Final Budget"


def whole_dollars(text):
    return int(Decimal(str(text)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def exception_index():
    out = []
    for x in E.exceptions():
        if x["scope"] in ("account", "doc"):
            out.append((x["scope"], set(x["scope_id"].split("|")), int(x["fy_from"] or 0), int(x["fy_to"] or 9999),
                        set(filter(None, x["bases"].split("|"))), x["exception_id"]))
    return out


def attach(ex, doc_id, key, period_fy, basis):
    return "|".join(i for scope, ids, a, b, bases, i in ex if a <= period_fy <= b and (not bases or basis in bases)
                    and ((scope == "account" and key in ids) or (scope == "doc" and doc_id in ids)))


def basis_of(measure, status):
    if measure == "final":
        return "requested" if status == "draft" else "adopted"
    if measure == "prelim":
        return "requested"
    return {"budget": "prior_doc_budget", "activity": "actual_prelim", "ytd": None}[measure]


def documents(sources):
    rows = []
    for s in sources:
        path = E.source_path(s["pdf_path"])
        status = {"adopted": "adopted", "preliminary": "draft", "final": "final"}[s["status"]]
        rows.append(dict(entity_id=E.id, doc_id=s["doc_id"], doc_type=s["doc_type"], fiscal_year=s["fiscal_year"], status=status,
                         pdf_path=s["pdf_path"], sha256=E.sha(path), source_url=s["url"], note=s["note"]))
    return rows


def budget_part(ex, docs_by_id):
    facts, totals = [], []
    amap = E.account_map()
    for doc_id in BUDGET_DOCS:
        status = docs_by_id[doc_id]["status"]
        for r in csv.DictReader(open(E.data_path("extracted", f"{doc_id}_lines.csv"))):
            basis = basis_of(r["measure"], status)
            if basis is None:
                continue
            fund, dept, obj = r["account_printed"].split("-")
            key = f"{fund}:{dept}-{obj}"
            if (r["flow"], key) not in amap:
                sys.exit(f"{doc_id}: {r['flow']}:{key} missing from account_map.csv (run build_account_map.py --write)")
            amount = whole_dollars(r["amount"])
            flag = r["flag"].strip()
            if Decimal(r["amount"]) != amount:
                flag = (flag + " rounded_from_cents").strip()
            fy = int(r["period_fy"])
            facts.append(dict(entity_id=E.id, doc_id=doc_id, source_type="budget", row_seq=int(r["row_seq"]), period_fy=fy, basis=basis, flow=r["flow"],
                              fund_code=fund, dept_code=dept, account_key=key, account_printed=r["account_printed"], audit_section="", label=r["label"],
                              amount=amount, source_column=r["column_label"], page_label="", pdf_page=r["pdf_page"], flag=flag,
                              exception_ids=attach(ex, doc_id, key, fy, basis)))
        for t in csv.DictReader(open(E.data_path("extracted", f"{doc_id}_totals.csv"))):
            basis = basis_of(t["measure"], status)
            if basis is None:
                continue
            level = "dept" if t["dept_code"] else "fund"
            kind = f"{level}_{'revenues' if t['kind'] == 'revenue' else 'expenses'}"
            name = f"{t['fund_code']}-{t['dept_code']}" if t["dept_code"] else t["fund_code"]
            totals.append(dict(entity_id=E.id, doc_id=doc_id, period_fy=int(t["period_fy"]), basis=basis, kind=kind, name=name,
                               amount=whole_dollars(t["printed"]), page_label=""))
    return facts, totals


def audit_bases(fy):
    if fy in COMBINED_COLUMN_YEARS:
        return [("audit_original", "combined_column"), ("audit_final", "combined_column")]
    return [("audit_final", "final_budget_column")]


def audit_part(ex, audits):
    facts, statements = [], []
    codes = {r["norm_name"]: r["fund_code"] for r in E.table("audit_fund_names.csv")}
    stmt_map = [(r["statement"], re.compile(r["label_pattern"]), r["line_id"]) for r in E.table("fund_statement_lines.csv")]
    adir = ("extracted", "audits")
    for d in audits:
        doc_id, fy = d["doc_id"], int(d["fiscal_year"])
        seq = 0

        def emit(base, budget, actual):
            if budget not in (None, ""):
                for basis, flag in audit_bases(fy):
                    facts.append(dict(base, period_fy=fy, basis=basis, amount=int(float(budget)), source_column="budget",
                                      flag=" ".join(x for x in (base["flag"], flag) if x), exception_ids=attach(ex, doc_id, "", fy, basis)))
            if actual not in (None, ""):
                facts.append(dict(base, period_fy=fy, basis="actual_audited", amount=int(float(actual)), source_column="actual",
                                  exception_ids=attach(ex, doc_id, "", fy, "actual_audited")))

        for r in csv.DictReader(open(E.data_path(*adir, f"{doc_id}_gf_schedule.csv"))):
            if r["line_type"] != "item":
                continue
            seq += 1
            base = dict(entity_id=E.id, doc_id=doc_id, source_type="audit", row_seq=seq, flow="revenue" if r["schedule"] == "revenue" else "expense",
                        fund_code="001", dept_code="", account_key="", account_printed="", audit_section=r["path"].replace("|", " | "), label=r["label"],
                        page_label="", pdf_page=r["pdf_page"], flag="")
            emit(base, r["budget"], r["actual"])
        for r in csv.DictReader(open(E.data_path(*adir, f"{doc_id}_fund_schedules.csv"))):
            if r["line_type"] != "item" or r["section"] not in ("revenue", "expenditure", "other_financing"):
                continue
            seq += 1
            financing = r["section"] == "other_financing"
            base = dict(entity_id=E.id, doc_id=doc_id, source_type="audit", row_seq=seq, flow="expense" if r["section"] == "expenditure" else "revenue",
                        fund_code=r["fund_code"] or codes.get(norm(r["fund_name"]), ""), dept_code="", account_key="", account_printed="",
                        audit_section=f"{r['fund_name']} | {'Other financing sources' if financing else r['section']} | ", label=r["label"],
                        page_label="", pdf_page=r["pdf_page"], flag="other_financing" if financing else "")
            emit(base, r["budget"], r["actual"])
        for r in csv.DictReader(open(E.data_path(*adir, f"{doc_id}_fund_statements.csv"))):
            statement = {"balance_sheet": "balance_sheet", "changes": "changes_in_fund_balance"}[r["statement"]]
            item = (r["prev_text"] + " " + r["label"]).strip()
            lid = next((i for st, rx, i in stmt_map if st == statement and rx.search(item)), "")
            statements.append(dict(entity_id=E.id, doc_id=doc_id, period_fy=fy, statement=statement, fund=r["col"], section="", subsection="",
                                   line_item=item, line_id=lid, amount=int(r["amount"]), pdf_page=r["pdf_page"], flag=""))
    return facts, statements


def accounts_table(facts):
    amap = E.account_map()
    years = {}
    for f in facts:
        if f["source_type"] == "budget" and f["basis"] == "adopted":
            years.setdefault((f["flow"], f["account_key"]), set()).add(f["period_fy"])
    seen = {(f["flow"], f["account_key"]) for f in facts if f["source_type"] == "budget"}
    out = []
    for (flow, key), m in sorted(amap.items()):
        if (flow, key) not in seen:
            continue
        ys = years.get((flow, key))
        out.append(dict(entity_id=E.id, flow=flow, account_key=key, label_current=m["label"], dept_code=key.split(":")[1].split("-")[0],
                        department=m["department"], category_id=m["category_id"], treatment=m["treatment"],
                        first_fy=min(ys) if ys else "", last_fy=max(ys) if ys else "", note=m["note"]))
    return out


def main():
    sources = [s for s in E.table("source_urls.csv") if s["doc_type"] in ("budget", "audit")]
    have = lambda s: (s["doc_id"] in BUDGET_DOCS) or (s["doc_type"] == "audit")  # noqa: E731
    docs = documents([s for s in sources if have(s)])
    by_id = {d["doc_id"]: d for d in docs}
    ex = exception_index()
    bf, totals = budget_part(ex, by_id)
    af, statements = audit_part(ex, sorted((d for d in docs if d["doc_type"] == "audit"), key=lambda d: int(d["fiscal_year"])))
    facts = bf + af
    write_csv(E.data_path("documents.csv"), DOCUMENT_COLUMNS, docs)
    write_csv(E.data_path("facts.csv"), FACT_COLUMNS, facts)
    write_csv(E.data_path("accounts.csv"), ACCOUNT_COLUMNS, accounts_table(facts))
    write_csv(E.data_path("control_totals.csv"), CONTROL_TOTAL_COLUMNS, totals)
    write_csv(E.data_path("corrections.csv"), CORRECTION_COLUMNS, [])
    write_csv(E.data_path("fund_statements.csv"), FUND_STATEMENT_COLUMNS, statements)
    n_b = sum(1 for f in facts if f["source_type"] == "budget")
    print(f"{len(docs)} documents, {len(facts)} facts ({n_b} budget, {len(facts) - n_b} audit), {len(totals)} control totals, {len(statements)} statement cells")


if __name__ == "__main__":
    main()
