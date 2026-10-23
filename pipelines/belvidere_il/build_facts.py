"""Turn Belvidere's per-year extracts into the canonical tables (govbudget.schema).

Usage: python3 pipelines/belvidere_il/build_facts.py

Inputs : data/belvidere_il/extracted/FY2015..FY2027_budget_gf_revenue.csv   (extract_budget.py)
         data/belvidere_il/extracted/audits/FY2012..FY2026_audit_gf_revenue.csv   (extract_audit.py)
         entities/belvidere_il/*  (account_map.csv, exceptions.csv, entity.json)
Outputs: data/belvidere_il/documents.csv, accounts.csv, facts.csv

Nothing here classifies or sums anything: facts.csv holds every extracted number once, with its document, period,
basis and provenance. Mapping decisions live in entities/belvidere_il/account_map.csv; anomalies in exceptions.csv
(matched to rows by account and fiscal-year range, listed in the exception_ids column).
"""
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402
from govbudget.schema import ACCOUNT_COLUMNS, DOCUMENT_COLUMNS, FACT_COLUMNS  # noqa: E402
from govbudget.validate import validate_config, validate_tables  # noqa: E402

E = Entity.load("belvidere_il")
COL = re.compile(r"^(budget_revised|budget|actual)_FY(\d{4})$")


def budget_basis(doc_fy, column):
    """Map a budget document's column name (budget_FY2019, budget_revised_FY2018, actual_FY2017) to (period_fy, basis)."""
    m = COL.match(column)
    if not m:
        sys.exit(f"FY{doc_fy} budget: unrecognised column {column!r}")
    kind, y = m.group(1), int(m.group(2))
    if kind == "actual":
        return y, "actual_prelim"
    if kind == "budget_revised":
        return y, "revised"
    if y == doc_fy:
        return y, "adopted"
    if y == doc_fy - 1:
        return y, "prior_doc_budget"
    sys.exit(f"FY{doc_fy} budget: plain budget column for FY{y} is neither the document's year nor the prior year")


def budget_pdf(fy):
    for p in (E.source_path("budgets", f"FY{fy}_budget.pdf"), E.source_path(f"FY{fy}_budget_753H.pdf")):
        if os.path.exists(p):
            return p
    return ""


def exception_index():
    """account exceptions as (set of keys, fy_from, fy_to, id); doc exceptions by doc_id."""
    out = []
    for x in E.exceptions():
        if x["scope"] in ("account", "doc"):
            out.append((x["scope"], set(x["scope_id"].split("|")), int(x["fy_from"] or 0), int(x["fy_to"] or 9999), x["exception_id"]))
    return out


def attach(ex, doc_id, key, period_fy):
    ids = [i for scope, ids_, a, b, i in ex
           if a <= period_fy <= b and ((scope == "account" and key in ids_) or (scope == "doc" and doc_id in ids_))]
    return "|".join(ids)


def main():
    cfg_errs = validate_config(E)
    if cfg_errs:
        sys.exit("config problems:\n  " + "\n  ".join(cfg_errs))
    ex = exception_index()
    facts, docs = [], []
    lo, hi = E.config["documents"]["budget_range"]
    for fy in range(lo, hi + 1):
        path = E.data_path("extracted", f"FY{fy}_budget_gf_revenue.csv")
        doc_id = f"FY{fy}_budget"
        pdf = budget_pdf(fy)
        docs.append(dict(entity_id=E.id, doc_id=doc_id, doc_type="budget", fiscal_year=fy,
                         pdf_path=os.path.relpath(pdf, E.sources_dir) if pdf else "", sha256=E.sha(pdf) if pdf else "",
                         source_url=E.config["source_index"]["budget"],
                         note="General Fund Exhibit A; FY2027 is ordinance 753H (revision 780H changes one salary only)" if fy == 2027 else ""))
        for seq, r in enumerate(csv.DictReader(open(path)), 1):
            period, basis = budget_basis(fy, r["column"])
            flow = "contra_expense" if "expense_block_offset" in r["flag"] else "revenue"
            key = E.account_key(r["account"])
            facts.append(dict(entity_id=E.id, doc_id=doc_id, source_type="budget", row_seq=seq, period_fy=period, basis=basis,
                              flow=flow, fund_code=r["account"].split("-")[0], dept_code=r["dept_code"], account_key=key,
                              account_printed=r["account"], audit_section="", label=r["line_item"], amount=int(r["amount"]),
                              source_column=r["column"], page_label=r["page_label"], pdf_page="", flag=r["flag"].strip(),
                              exception_ids=attach(ex, doc_id, key, period)))
    lo, hi = E.config["documents"]["audit_range"]
    for fy in range(lo, hi + 1):
        path = E.data_path("extracted", "audits", f"FY{fy}_audit_gf_revenue.csv")
        doc_id = f"FY{fy}_audit"
        pdf = E.source_path("audits", f"{doc_id}.pdf")
        docs.append(dict(entity_id=E.id, doc_id=doc_id, doc_type="audit", fiscal_year=fy,
                         pdf_path=os.path.relpath(pdf, E.sources_dir) if os.path.exists(pdf) else "",
                         sha256=E.sha(pdf) if os.path.exists(pdf) else "", source_url=E.config["source_index"]["audit"],
                         note="General Fund Schedule of Revenues - Budget and Actual (the detailed schedule)"))
        for seq, r in enumerate(csv.DictReader(open(path)), 1):
            base = dict(entity_id=E.id, doc_id=doc_id, source_type="audit", row_seq=seq, flow="revenue", fund_code="01",
                        dept_code="", account_key="", account_printed="", audit_section=r["section"], label=r["line_item"],
                        page_label="", pdf_page=r["pdf_page"], flag=r["flag"].strip(), exception_ids=attach(ex, doc_id, "", fy))
            for col, basis in (("original_budget", "audit_original"), ("final_budget", "audit_final"), ("actual", "actual_audited")):
                facts.append(dict(base, period_fy=fy, basis=basis, amount=int(r[col]), source_column=col))
            if r.get("prior_year_actual", "") != "":
                facts.append(dict(base, period_fy=fy - 1, basis="actual_audited", amount=int(r["prior_year_actual"]),
                                  source_column="prior_year_actual"))

    amap = E.account_map()
    first, last, label = {}, {}, {}
    for f in facts:
        if f["source_type"] == "budget" and f["basis"] == "adopted":
            k = (f["flow"], f["account_key"])
            first[k] = min(first.get(k, 9999), f["period_fy"])
            last[k] = max(last.get(k, 0), f["period_fy"])
            label[k] = f["label"]  # facts are in fiscal-year order, so the last label wins
    accounts = []
    for (flow, key), m in sorted(amap.items()):
        accounts.append(dict(entity_id=E.id, flow=flow, account_key=key, label_current=m["label"] or label.get((flow, key), ""),
                             dept_code=key.split("-")[0], department=m["department"], category_id=m["category_id"],
                             treatment=m["treatment"], first_fy=first.get((flow, key), ""), last_fy=last.get((flow, key), ""),
                             note=m["note"]))
    write_csv(E.data_path("documents.csv"), DOCUMENT_COLUMNS, docs)
    write_csv(E.data_path("accounts.csv"), ACCOUNT_COLUMNS, accounts)
    write_csv(E.data_path("facts.csv"), FACT_COLUMNS, facts)
    errs = validate_tables(E)
    if errs:
        sys.exit(f"{len(errs)} integrity problems:\n  " + "\n  ".join(errs[:30]))
    print(f"{len(docs)} documents, {len(accounts)} accounts, {len(facts)} facts -> data/{E.id}/ (integrity checks pass)")


if __name__ == "__main__":
    main()
