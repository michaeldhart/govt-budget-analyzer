"""Map the audit's General Fund tax lines onto comparable groups and compare them with the budget documents.

Usage: python3 pipelines/belvidere_il/build_audit_compare.py   (run build_facts.py and build_tax_series.py first)

Inputs : data/belvidere_il/facts.csv                     audit lines (basis audit_original / audit_final / actual_audited)
         data/belvidere_il/tax_series_long.csv           budget-document figures (build_tax_series.py)
         entities/belvidere_il/audit_map.csv             audit line label -> comparable group
         entities/belvidere_il/comparable_groups.csv     comparable group -> report group and the budget accounts it contains
         entities/belvidere_il/exceptions.csv            notes shown on the report groups
Outputs: data/belvidere_il/audit_tax_lines_by_group.csv  audited original budget / final budget / actual per tax group per year
         data/belvidere_il/audit_vs_budget_docs.csv      audit vs budget documents, per group and year
         data/belvidere_il/tax_series_audit_basis.csv    the report's budget-vs-actual table, on the audit's own report groups

Comparable groups follow how the audit presents taxes, not the FY2027 categories (exceptions EX-replacement-net,
EX-grp-sales-combined, EX-telecom-group, EX-motor-fuel-grants describe where they differ). The mapping itself is data:
the audit's labels and the budget accounts behind each group live in the entity's CSVs, not in this script.
Budget = the audit's own ORIGINAL budget (FY2012-FY2026), which equals the documents' adopted budget on 134 of 135
line-years; FY2027 has no audit yet, so its row carries the ordinance's adopted budget only.
"""
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
AUDIT_BASES = (("audit_original", 0), ("audit_final", 1), ("actual_audited", 2))


def norm(label):
    return re.sub(r"\s+", " ", label).strip().lower()


def load_audits(audit_map):
    """fy -> comparable group -> [original, final, actual], plus the tax-section labels that map to no group."""
    out, unmapped = defaultdict(lambda: defaultdict(lambda: [0, 0, 0])), []
    lines = defaultdict(dict)  # (doc, row_seq) -> {basis: amount} for rows of the audit's own year
    meta = {}
    for r in read_csv(E.data_path("facts.csv")):
        if r["source_type"] != "audit" or r["source_column"] == "prior_year_actual":
            continue
        lines[(r["doc_id"], int(r["row_seq"]))][r["basis"]] = int(r["amount"])
        meta[(r["doc_id"], int(r["row_seq"]))] = (int(r["period_fy"]), r["audit_section"], r["label"])
    for key, vals in lines.items():
        fy, section, label = meta[key]
        lab = norm(label)
        if lab in audit_map:
            g = out[fy][audit_map[lab]]
            for basis, i in AUDIT_BASES:
                g[i] += vals[basis]
        elif section.lower() in ("taxes", "intergovernmental"):
            unmapped.append((fy, section, label, vals["audit_original"], vals["actual_audited"]))
    return out, unmapped


def load_budget_docs():
    """(fy, measure, account_key) -> amount, all 31 mapped accounts (including the fee-treated ones)."""
    return {(int(r["fiscal_year"]), r["measure"], E.account_key(r["account"])): int(r["amount"])
            for r in read_csv(E.data_path("tax_series_long.csv"))}


def docs_value(d, fy, measure, members):
    vals = [d.get((fy, measure, k)) for k in members]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)


def group_notes(report_group, fy):
    """Notes for a report group: the summaries of the group-scoped exceptions in force that year."""
    return "; ".join(x["summary"] for x in E.exceptions()
                     if x["scope"] == "group" and x["scope_id"] == report_group
                     and int(x["fy_from"] or 0) <= fy <= int(x["fy_to"] or 9999))


def build_audit_basis(audits, d, groups):
    report_members = defaultdict(list)  # report group -> budget accounts (a set: Sales+use overlaps Sales and Use)
    for g in groups.values():
        if g["report_group"]:
            for k in g["budget_members"].split("|"):
                if k not in report_members[g["report_group"]]:
                    report_members[g["report_group"]].append(k)
    rows = []
    for fy in sorted(audits):
        agg = defaultdict(lambda: [0, 0, 0])
        for g, vals in audits[fy].items():
            rg = groups[g]["report_group"]
            if not rg:
                continue
            for i in range(3):
                agg[rg][i] += vals[i]
        has_docs = (fy, "budget_adopted", "110-4010") in d
        for rg, (o, fnl, a) in sorted(agg.items()):
            rows.append(dict(entity_id=E.id, fiscal_year=fy, group=rg, budget_audit_original=o, budget_audit_final=fnl,
                             actual_audited=a,
                             budget_docs_adopted=docs_value(d, fy, "budget_adopted", report_members[rg]) if has_docs else "",
                             actual_budget_doc=docs_value(d, fy, "actual_budget_doc", report_members[rg]) if (fy, "actual_budget_doc", "110-4010") in d else "",
                             note=group_notes(rg, fy)))
    # FY2027: the ordinance's adopted budget only (no audit yet)
    for rg, members in report_members.items():
        v = docs_value(d, 2027, "budget_adopted", members)
        if v is not None:
            rows.append(dict(entity_id=E.id, fiscal_year=2027, group=rg, budget_audit_original="", budget_audit_final="",
                             actual_audited="", budget_docs_adopted=v, actual_budget_doc="", note="FY2027 ordinance; no audit yet"))
    return rows


def main():
    audit_map = {norm(r["audit_label"]): r["comparable_group"] for r in E.table("audit_map.csv")}
    groups = {g["comparable_group"]: g for g in E.table("comparable_groups.csv")}
    audits, unmapped = load_audits(audit_map)
    d = load_budget_docs()
    lines, comp = [], []
    for fy in sorted(audits):
        for g, (o, fnl, a) in sorted(audits[fy].items()):
            lines.append(dict(entity_id=E.id, fiscal_year=fy, group=g, original_budget=o, final_budget=fnl, actual=a))
            members = groups[g]["budget_members"].split("|")
            doc_orig = docs_value(d, fy, "budget_adopted", members)
            act = docs_value(d, fy, "actual_budget_doc", members)
            comp.append(dict(entity_id=E.id, fiscal_year=fy, group=g, audit_original=o, doc_budget=doc_orig,
                             budget_diff="" if doc_orig is None else o - doc_orig,
                             audit_actual=a, doc_actual=act if act is not None else "",
                             actual_diff="" if act is None else a - act))
    write_csv(E.data_path("audit_tax_lines_by_group.csv"),
              ["entity_id", "fiscal_year", "group", "original_budget", "final_budget", "actual"], lines)
    write_csv(E.data_path("audit_vs_budget_docs.csv"),
              ["entity_id", "fiscal_year", "group", "audit_original", "doc_budget", "budget_diff", "audit_actual", "doc_actual", "actual_diff"], comp)
    basis = build_audit_basis(audits, d, groups)
    write_csv(E.data_path("tax_series_audit_basis.csv"),
              ["entity_id", "fiscal_year", "group", "budget_audit_original", "budget_audit_final", "actual_audited",
               "budget_docs_adopted", "actual_budget_doc", "note"], basis)
    print(f"{len(basis)} rows -> data/{E.id}/tax_series_audit_basis.csv")
    print(f"{len(lines)} audit group rows -> audit_tax_lines_by_group.csv; {len(comp)} comparison rows -> audit_vs_budget_docs.csv")
    if unmapped:
        print("\nTaxes/Intergovernmental audit lines NOT mapped to a tax group (review):")
        seen = defaultdict(list)
        for fy, sec, lab, o, a in unmapped:
            seen[(sec, lab)].append(fy)
        for (sec, lab), ys in sorted(seen.items()):
            print(f"  {sec:18} {lab:36} FY{min(ys)}-FY{max(ys)} ({len(ys)} yrs)")

    bd = [c for c in comp if c["budget_diff"] not in ("", 0)]
    print(f"\nOriginal budget: {sum(1 for c in comp if c['budget_diff'] == 0)} group-years match the budget documents exactly, "
          f"{len(bd)} differ, {sum(1 for c in comp if c['budget_diff'] == '')} have no budget-document figure")


if __name__ == "__main__":
    main()
