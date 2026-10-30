"""Integrity checks on an entity's config and canonical tables. Returns a list of problems (empty = clean)."""
import os
import re

from .schema import (DEBT_PENSION_TABLES, DOC_STATUSES, TAX_DEBT_TABLES, STATEMENTS, BASES, EXCEPTION_EFFECTS, EXCEPTION_KINDS, EXCEPTION_SCOPES, FLOWS, SCHEMES, SOURCE_TYPES, TOTAL_KINDS,
                     TREATMENTS)
from .entity import read_csv


def optional_table(E, name):
    """A config table a government may not have yet (audit mappings come with the audits): no file is no rows."""
    import os
    return E.table(name) if os.path.exists(E.config_path(name)) else []


def validate_config(E):
    errs = []
    cats = {c["category_id"]: c for c in E.categories()}
    for c in cats.values():
        if c["scheme"] not in SCHEMES:
            errs.append(f"categories.csv: {c['category_id']}: unknown scheme {c['scheme']!r}")
    for (flow, key), r in E.account_map().items():
        if flow not in FLOWS:
            errs.append(f"account_map.csv: {key}: bad flow {flow!r}")
        if r["treatment"] not in TREATMENTS:
            errs.append(f"account_map.csv: {key}: bad treatment {r['treatment']!r}")
        c = cats.get(r["category_id"])
        scheme = "expense_object" if flow == "expense" else "budget_category"
        if c is None or c["scheme"] != scheme:
            errs.append(f"account_map.csv: {key}: category_id {r['category_id']!r} is not an {scheme} category (flow {flow})")
        if (flow == "expense") != (r["treatment"] == "expense"):
            errs.append(f"account_map.csv: {key}: treatment {r['treatment']!r} does not fit flow {flow!r}")
        fund, rest = E.split_key(key)
        if rest.split("-")[0] not in E.departments(fund):
            errs.append(f"account_map.csv: {key}: department code not in departments.csv for fund {fund}")
    rg = {c["name"] for c in E.categories("report_group")}
    groups = {g["comparable_group"]: g for g in optional_table(E, "comparable_groups.csv")}
    amap = E.account_map()
    for g in groups.values():
        if g["report_group"] and g["report_group"] not in rg:
            errs.append(f"comparable_groups.csv: {g['comparable_group']}: unknown report_group {g['report_group']!r}")
        for k in g["budget_members"].split("|"):
            if ("revenue", k) not in amap:
                errs.append(f"comparable_groups.csv: {g['comparable_group']}: member {k!r} not in account_map")
    for r in optional_table(E, "audit_map.csv"):
        if r["comparable_group"] not in groups:
            errs.append(f"audit_map.csv: {r['audit_label']!r}: unknown comparable_group {r['comparable_group']!r}")
    exp_groups = set()
    for r in optional_table(E, "audit_expenditure_map.csv"):
        exp_groups.add(r["department_group"])
        for d in filter(None, r["budget_dept_codes"].split("|")):
            if d not in E.departments(r.get("fund_code") or "01"):
                errs.append(f"audit_expenditure_map.csv: {r['department_group']}: dept code {d!r} not in departments.csv")
    ids = set()
    for x in E.exceptions():
        if x["exception_id"] in ids:
            errs.append(f"exceptions.csv: duplicate id {x['exception_id']}")
        ids.add(x["exception_id"])
        if x["kind"] not in EXCEPTION_KINDS:
            errs.append(f"exceptions.csv: {x['exception_id']}: bad kind {x['kind']!r}")
        if x["scope"] not in EXCEPTION_SCOPES:
            errs.append(f"exceptions.csv: {x['exception_id']}: bad scope {x['scope']!r}")
        if x["effect"] not in EXCEPTION_EFFECTS:
            errs.append(f"exceptions.csv: {x['exception_id']}: bad effect {x['effect']!r}")
        if x["scope"] == "account":
            for k in x["scope_id"].split("|"):
                if not any(key == k for (_, key) in amap) and not k.endswith("UNLABELED"):
                    errs.append(f"exceptions.csv: {x['exception_id']}: account {k!r} not in account_map")
        if x["scope"] == "group" and x["scope_id"] not in rg and x["scope_id"] not in groups and x["scope_id"] not in exp_groups:
            errs.append(f"exceptions.csv: {x['exception_id']}: group {x['scope_id']!r} unknown")
    return errs


def validate_tables(E):
    errs = []
    for r in read_csv(E.data_path("control_totals.csv")):
        if r["kind"] not in TOTAL_KINDS or r["basis"] not in BASES:
            errs.append(f"control_totals.csv: bad kind/basis {r['kind']!r}/{r['basis']!r}")
    docs = {r["doc_id"] for r in read_csv(E.data_path("documents.csv"))}
    for r in read_csv(E.data_path("documents.csv")):
        if r["status"] not in DOC_STATUSES:
            errs.append(f"documents.csv: {r['doc_id']}: status {r['status']!r} is not one of {DOC_STATUSES}")
    exc_ids = {x["exception_id"] for x in E.exceptions()}
    for name, cols in {**DEBT_PENSION_TABLES, **TAX_DEBT_TABLES}.items():
        if not os.path.exists(E.data_path(f"{name}.csv")):
            if name in TAX_DEBT_TABLES or E.config.get("optional_tables", []).count(name):
                continue  # a government may not have the table (no OPEB, no assessed valuation schedule); entity.json `optional_tables` says so
        rows = read_csv(E.data_path(f"{name}.csv")) if os.path.exists(E.data_path(f"{name}.csv")) else []
        if not rows or list(rows[0]) != cols:
            errs.append(f"{name}.csv: missing or its columns differ from schema.DEBT_PENSION_TABLES")
            continue
        for i, r in enumerate(rows, 2):
            if r["doc_id"] not in docs:
                errs.append(f"{name}.csv line {i}: unknown doc_id {r['doc_id']!r}")
            for e in filter(None, re.split(r"[;|]", r["exception_ids"])):
                if e not in exc_ids:
                    errs.append(f"{name}.csv line {i}: unknown exception {e!r}")
    for r in read_csv(E.data_path("fund_statements.csv")):
        if r["statement"] not in STATEMENTS:
            errs.append(f"fund_statements.csv: unknown statement {r['statement']!r}")
            break
    exc = {x["exception_id"] for x in E.exceptions()}
    amap = E.account_map()
    seen = set()
    for i, r in enumerate(read_csv(E.data_path("facts.csv")), 2):
        where = f"facts.csv line {i}"
        if r["entity_id"] != E.id:
            errs.append(f"{where}: entity_id {r['entity_id']!r}")
        if r["doc_id"] not in docs:
            errs.append(f"{where}: unknown doc_id {r['doc_id']!r}")
        if r["basis"] not in BASES or r["source_type"] not in SOURCE_TYPES or r["flow"] not in FLOWS:
            errs.append(f"{where}: bad basis/source_type/flow {r['basis']!r}/{r['source_type']!r}/{r['flow']!r}")
        try:
            int(r["amount"])
        except ValueError:
            errs.append(f"{where}: amount {r['amount']!r} is not an integer")
        if r["source_type"] == "budget" and (r["flow"], r["account_key"]) not in amap:
            errs.append(f"{where}: account {r['flow']}:{r['account_key']} missing from account_map.csv")
        for e in filter(None, r["exception_ids"].split("|")):
            if e not in exc:
                errs.append(f"{where}: unknown exception {e!r}")
        k = (r["doc_id"], r["row_seq"], r["basis"], r["period_fy"])
        if k in seen:
            errs.append(f"{where}: duplicate key {k}")
        seen.add(k)
    return errs
