"""General Fund revenue by source: the comparable groups shared by the budget accounts and the audit's revenue lines.

Writes entities/boone_county_il/comparable_groups.csv (group -> report group + the General Fund revenue account keys it contains) and audit_map.csv (audit line -> group).
The audits regroup their revenue lines from year to year (FY2015-FY2023 put state-shared income, sales, use and replacement taxes under 'Taxes', FY2024-FY2025 under
'Intergovernmental'; the group headed 'Interest' holds rent, work release and miscellaneous), so the match is made line by line: audit_map.csv has (heading regex, label regex,
group) rows, first match wins; the budget side maps object codes (and, for the FY2020 book's lines printed without a code, labels).
Usage: python3 pipelines/boone_county_il/build_revenue_groups.py
"""
import csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402

E = Entity.load("boone_county_il")
GROUPS = [  # (group, report group name, note)
    ("property_tax", "Property tax", "County property tax levy (object 301000)"),
    ("sales_use_tax", "State sales and use tax", "1 cent and 1/4 cent sales taxes and the out-of-state / local use tax"),
    ("income_tax", "State income tax", "State-shared income tax"),
    ("replacement_tax", "Replacement tax", "Personal property replacement tax"),
    ("other_taxes", "Other taxes", "Video gaming and cannabis taxes, hotel/motel tax"),
    ("licenses_permits", "Licenses and permits", "Building, liquor and other permits"),
    ("fees_fines", "Fees, fines and charges", "Court, clerk, recorder, sheriff, coroner fees; cable franchise fees; penalties and tax-sale fees; stamps; towing"),
    ("investment_income", "Investment income", "Interest on investments and courts' interest"),
    ("reimbursements_grants", "Reimbursements and grants", "State salary reimbursements, City of Belvidere (public safety building), other reimbursements, grants"),
    ("miscellaneous", "Miscellaneous", "County farm, rent, royalties, work release, insurance and other"),
]
# audit lines: (heading regex, label regex, group)
AUDIT_RULES = [
    (r"", r"^property tax", "property_tax"),
    (r"", r"^state sales|^use tax|sales and use", "sales_use_tax"),
    (r"", r"^state income", "income_tax"),
    (r"", r"^replacement tax", "replacement_tax"),
    (r"", r"cannabis", "sales_use_tax"), (r"", r"video gaming", "other_taxes"),
    (r"", r"^licenses and permits$|^building permits|^liquor permits|^other permits", "licenses_permits"),
    (r"", r"^investments$|^investment income", "investment_income"),
    (r"(?i)intergovernmental", r"^(grants|other|other governmental revenue|other reimbursements|county sheriff reimbursements|public safety building|reimbursement from city|state of il)", "reimbursements_grants"),
    (r"(?i)interest|miscellaneous", r"^penalties", "fees_fines"),
    (r"(?i)interest|miscellaneous", r"^(county farm|miscellaneous|rent|work release|assessor|drug crime|dis?pursement|other$|plot|sheriff|treasurer)", "miscellaneous"),
    (r"(?i)interest", r"^revenue stamps", "fees_fines"),
    (r"(?i)fees|charges", r".", "fees_fines"),
]
# budget object codes -> group (an object not listed is not General Fund revenue by source: transfers 399xxx and proceeds 390000/395000 are other financing)
OBJECT_RULES = [(r"^301", "property_tax"), (r"^(304|305|3068|3060|30642)", "sales_use_tax"), (r"^(3061|3071)", "income_tax"), (r"^(3062|3072)", "replacement_tax"),
                (r"^(3060|30642|306420|307[0-9]|308)", "other_taxes"), (r"^329", "licenses_permits"), (r"^(3063|3064|3073|3074)", "fees_fines"), (r"^(340|365|360001|360045)", "fees_fines"),
                (r"^370", "investment_income"), (r"^375", "miscellaneous"), (r"^(376|377|379|380|381)", "reimbursements_grants"), (r"^(351|360)", "miscellaneous")]
# labels of the FY2020 book's lines printed without an object code
LABEL_RULES = [(r"transfer", None), (r"property tax", "property_tax"), (r"sales tax|use tax", "sales_use_tax"), (r"income tax", "income_tax"), (r"replacement", "replacement_tax"),
               (r"video gaming", "other_taxes"), (r"penalty|tax fee", "fees_fines"), (r"liquor|licen|building fee", "licenses_permits"), (r"zoning fee", "fees_fines"),
               (r"interest", "investment_income"), (r"salary|probation|reimburs|assessor|psb operations|township|workers|grant", "reimbursements_grants"),
               (r"fee|circuit court|fines|jail housing|counsel|gis mapping|website|coroner|clerk|first offender|finance court|cable|stamps", "fees_fines")]


def budget_group(account_key, label, category=None):
    if category == "transfers_in":
        return None  # a transfer in listed by fund name (FY2020 book) is other financing, whatever its label says
    obj = account_key.split("-", 1)[1]
    if obj.startswith("U"):
        for rx, g in LABEL_RULES:
            if re.search(rx, label, re.I):
                return g
        return "miscellaneous"
    for rx, g in OBJECT_RULES:
        if re.match(rx, obj):
            return g
    return None


def audit_group(heading, label):
    lab = re.sub(r"\s+", " ", label).strip().lower()
    for hrx, lrx, g in AUDIT_RULES:
        if re.search(hrx, heading or "") and re.search(lrx, lab):
            return g
    return None


def main():
    members = {g: [] for g, _, _ in GROUPS}
    for r in E.table("account_map.csv"):
        if r["flow"] == "revenue" and r["account_key"].startswith("001:"):
            g = budget_group(r["account_key"], r["label"], r["category_id"])
            if g:
                members[g].append(r["account_key"])
    write_csv(E.config_path("comparable_groups.csv"), ["comparable_group", "report_group", "budget_members", "note"],
              [dict(comparable_group=g, report_group=name, budget_members="|".join(sorted(members[g])), note=note) for g, name, note in GROUPS])
    write_csv(E.config_path("audit_map.csv"), ["audit_label", "comparable_group", "heading_regex", "label_regex"],
              [dict(audit_label=f"{h} | {l}", comparable_group=g, heading_regex=h, label_regex=l) for h, l, g in AUDIT_RULES])
    print({g: len(v) for g, v in members.items()})


if __name__ == "__main__":
    main()
