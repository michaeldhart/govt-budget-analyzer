"""Map the audit's General Fund tax lines onto comparable groups and compare them with the budget documents.

Usage: python scripts/build_audit_compare.py

Inputs : data/extracted/audits/FY2012..FY2026_audit_gf_revenue.csv   (scripts/extract_audit.py)
         data/tax_series_long.csv                                    (scripts/build_tax_series.py)
Outputs: data/audit_tax_lines_by_group.csv   audited original budget / final budget / actual per tax group per year
         data/audit_vs_budget_docs.csv       audit vs budget documents, per group and year

Comparable groups follow how the audit presents taxes, not the FY2027 categories:
  * Replacement tax in the audit is NET of the replacement tax distributed to pensions.
  * Sales tax is municipal + home rule sales tax; FY2012-FY2015 audits report sales and use tax combined.
  * "Telecommunications tax" is the budget's Municipal Infrastructure Maintenance line (a fee in the FY2027
    methodology, a tax in the audit).
Budget-document figures come from tax_series_long.csv: `budget_adopted` for the audit's original budget and
`actual_budget_doc` for the city's own reported actual.

Second output, data/tax_series_audit_basis.csv, is the table the report uses for budget vs actual. It rolls the
audit lines into seven report groups (Property; Sales and use; State income; Utility; Replacement net of the
pension distribution; Video gaming; Other = hotel/motel + auto rental + cannabis + motor fuel) plus the
telecommunications line shown separately. Budget = the audit's own ORIGINAL budget (FY2012-FY2026), which equals the
documents' adopted budget on 134 of 135 line-years; FY2027 has no audit yet. Local motor fuel tax sits inside the
audit's "Grants" line in FY2020-FY2025 (the audit's Grants budget equals the documents' grants + motor fuel to the
dollar) and is its own line only in FY2026, so it is left out of every year, on both sides, to keep the series continuous.
"""
import csv
import glob
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

AUDIT_GROUP = {
    "property": "Property tax", "property taxes": "Property tax",
    "hotel/motel": "Hotel/motel tax", "hotel/motel tax": "Hotel/motel tax",
    "auto rental": "Auto rental tax", "auto rental tax": "Auto rental tax",
    "telecommunications": "Telecommunications tax", "telecommunication tax": "Telecommunications tax",
    "telecommunications tax": "Telecommunications tax",
    "utility tax": "Utility taxes",
    "replacement tax": "Replacement tax (net)", "personal property replacement tax": "Replacement tax (net)",
    "income tax": "State income tax", "state income": "State income tax", "state income tax": "State income tax",
    "sales tax": "Sales tax", "sales & use": "Sales + use tax", "sales and use tax": "Sales + use tax",
    "state use tax": "Local use tax",
    "video gambling tax": "Video gaming tax", "video gambling": "Video gaming tax",
    "cannabis tax": "Cannabis tax", "motor fuel tax": "Local motor fuel tax",
}
# budget-document accounts (department-object key) per comparable group
BUDGET_KEYS = {
    "Hotel/motel tax": ["110-4011"], "Auto rental tax": ["110-4012"], "Telecommunications tax": ["110-4013"],
    "State income tax": ["110-4100"], "Sales tax": ["110-4109", "110-4110"], "Local use tax": ["110-4112"],
    "Local motor fuel tax": ["110-4113"], "Cannabis tax": ["110-4115"], "Video gaming tax": ["110-4440"],
    "Utility taxes": ["751-4131", "751-4132", "751-4133"],
    "Replacement tax (net)": ["110-4120", "140-4120", "110-4121"],
}
DEVELOPER_OFFSETS = ["110-4111", "110-4114"]


def key_of(account):
    p = account.replace("1104114", "110-4114").split("-")
    return f"{p[2]}-{p[3]}"


def load_audits():
    """fy -> group -> [original, final, actual], plus the list of unmapped tax-section labels."""
    out, unmapped = defaultdict(lambda: defaultdict(lambda: [0, 0, 0])), []
    for f in sorted(glob.glob(os.path.join(DATA, "extracted", "audits", "FY*_audit_gf_revenue.csv"))):
        for r in csv.DictReader(open(f)):
            lab = re.sub(r"\s+", " ", r["line_item"]).strip().lower()
            if lab in AUDIT_GROUP:
                g = out[int(r["fiscal_year"])][AUDIT_GROUP[lab]]
                for i, c in enumerate(("original_budget", "final_budget", "actual")):
                    g[i] += int(r[c])
            elif r["section"].lower() in ("taxes", "intergovernmental"):
                unmapped.append((int(r["fiscal_year"]), r["section"], r["line_item"], r["original_budget"], r["actual"]))
    return out, unmapped


def load_budget_docs():
    """(fy, measure, key) -> amount, all 31 mapped accounts (including the fee-treated ones)."""
    d = {}
    for r in csv.DictReader(open(os.path.join(DATA, "tax_series_long.csv"))):
        d[(int(r["fiscal_year"]), r["measure"], key_of(r["account"]))] = int(r["amount"])
    prop_keys = sorted({k[2] for k in d if k[2].endswith("-4010")})
    return d, prop_keys


def budget_group_value(d, prop_keys, fy, measure, group):
    if group == "Property tax":
        keys = prop_keys
    elif group == "Sales + use tax":
        keys = BUDGET_KEYS["Sales tax"] + BUDGET_KEYS["Local use tax"]
    else:
        keys = BUDGET_KEYS.get(group)
        if keys is None:
            return None
    vals = [d.get((fy, measure, k)) for k in keys]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)


REPORT_GROUP = {
    "Property tax": "Property tax", "Sales tax": "Sales and use taxes", "Sales + use tax": "Sales and use taxes",
    "Local use tax": "Sales and use taxes", "State income tax": "State income tax", "Utility taxes": "Utility taxes",
    "Replacement tax (net)": "Replacement tax (net)", "Video gaming tax": "Video gaming tax",
    "Hotel/motel tax": "Other taxes", "Auto rental tax": "Other taxes", "Cannabis tax": "Other taxes",
    "Local motor fuel tax": None, "Telecommunications tax": "Telecommunications tax",  # motor fuel: inside Grants FY2020-25, so left out of every year
}
# budget-document accounts per report group (local motor fuel tax 110-4113 is deliberately in none: the audits report it
# inside Grants in FY2020-FY2025 and separately only in FY2026, so it is left out of every year to keep the series continuous)
REPORT_KEYS = {
    "Sales and use taxes": ["110-4109", "110-4110", "110-4112"], "State income tax": ["110-4100"],
    "Utility taxes": ["751-4131", "751-4132", "751-4133"], "Replacement tax (net)": ["110-4120", "140-4120", "110-4121"],
    "Video gaming tax": ["110-4440"], "Other taxes": ["110-4011", "110-4012", "110-4115"],
    "Telecommunications tax": ["110-4013"],
}


def report_group_docs(d, prop_keys, fy, measure, group):
    keys = prop_keys if group == "Property tax" else list(REPORT_KEYS[group])
    vals = [d.get((fy, measure, k)) for k in keys]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)


def build_audit_basis(audits, d, prop_keys):
    rows = []
    for fy in sorted(audits):
        agg = defaultdict(lambda: [0, 0, 0])
        for g, vals in audits[fy].items():
            rg = REPORT_GROUP[g]
            if rg is None:
                continue
            for i in range(3):
                agg[rg][i] += vals[i]
        for rg, (o, fnl, a) in sorted(agg.items()):
            note = ""
            if rg == "Other taxes":
                note = "hotel/motel + auto rental + cannabis; local motor fuel tax excluded in every year (audit reports it inside Grants FY2020-25)"
            if rg == "Sales and use taxes" and fy <= 2015:
                note = "audit reports sales and use combined; later audits report them separately (summed here)"
            if rg == "Telecommunications tax":
                note = "budget documents call this Municipal Infrastructure Maintenance; counted as a tax (decided 2026-10-22), as the audit presents it"
            rows.append(dict(fiscal_year=fy, group=rg, budget_audit_original=o, budget_audit_final=fnl,
                             actual_audited=a,
                             budget_docs_adopted=report_group_docs(d, prop_keys, fy, "budget_adopted", rg) if (fy, "budget_adopted", "110-4010") in d else "",
                             actual_budget_doc=report_group_docs(d, prop_keys, fy, "actual_budget_doc", rg) if (fy, "actual_budget_doc", "110-4010") in d else "",
                             note=note))
    # FY2027: the ordinance's adopted budget only (no audit yet)
    for rg in ["Property tax"] + list(REPORT_KEYS):
        v = report_group_docs(d, prop_keys, 2027, "budget_adopted", rg)
        if v is not None:
            rows.append(dict(fiscal_year=2027, group=rg, budget_audit_original="", budget_audit_final="", actual_audited="",
                             budget_docs_adopted=v, actual_budget_doc="", note="FY2027 ordinance; no audit yet"))
    return rows


def main():
    audits, unmapped = load_audits()
    d, prop_keys = load_budget_docs()
    lines, comp = [], []
    for fy in sorted(audits):
        for g, (o, fnl, a) in sorted(audits[fy].items()):
            lines.append(dict(fiscal_year=fy, group=g, original_budget=o, final_budget=fnl, actual=a))
            doc_orig = budget_group_value(d, prop_keys, fy, "budget_adopted", g)
            act = budget_group_value(d, prop_keys, fy, "actual_budget_doc", g)
            comp.append(dict(fiscal_year=fy, group=g, audit_original=o, doc_budget=doc_orig,
                             budget_diff="" if doc_orig is None else o - doc_orig,
                             audit_actual=a, doc_actual=act if act is not None else "",
                             actual_diff="" if act is None else a - act))
    with open(os.path.join(DATA, "audit_tax_lines_by_group.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["fiscal_year", "group", "original_budget", "final_budget", "actual"])
        w.writeheader()
        w.writerows(lines)
    with open(os.path.join(DATA, "audit_vs_budget_docs.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["fiscal_year", "group", "audit_original", "doc_budget", "budget_diff", "audit_actual", "doc_actual", "actual_diff"])
        w.writeheader()
        w.writerows(comp)
    basis = build_audit_basis(audits, d, prop_keys)
    with open(os.path.join(DATA, "tax_series_audit_basis.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["fiscal_year", "group", "budget_audit_original", "budget_audit_final", "actual_audited",
                               "budget_docs_adopted", "actual_budget_doc", "note"])
        w.writeheader()
        w.writerows(basis)
    print(f"{len(basis)} rows -> data/tax_series_audit_basis.csv")
    print(f"{len(lines)} audit group rows -> data/audit_tax_lines_by_group.csv; {len(comp)} comparison rows -> data/audit_vs_budget_docs.csv")
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
