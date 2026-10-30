"""Draft account_map.csv and departments.csv for the Belvidere Park District from every account in the extracts.

Usage: python3 pipelines/belvidere_park_district/build_account_map.py [--write]

Run it to ADD accounts the extracts contain that the committed files do not yet have; it never changes or removes an existing row (the CSVs are the
hand-edited authority, mappings are data, not code). Without --write it prints what it would add.

Keys: General Fund (01) accounts are `DDDD-OOO` / `DDD-OOOO`, other funds `FF:DDDD-OOO` (govbudget.entity.Entity.split_key). The budget changed
account formats between FY2024 and FY2025 (`01-0100-310` -> `01-010-3010`, exception EX-account-format-fy2025) and the funds Museum and Special
Recreation swapped codes (EX-fund-renumbering-fy2025), so both formats are mapped and years compare through the category, not the number.
Revenue categories (budget_category): property tax and replacement tax are taxes, everything else is `non_tax`. Expense categories (expense_object):
FY2025+ accounts by the first digit of the object code (4 personnel, 5 contractual, 6 supplies and maintenance, 7 debt service, 8 capital); older
accounts take the category of the same label in the FY2025+ budgets where there is one, else a keyword rule; funds 10/12 are debt service and 21
capital projects whatever the line. Every account a rule classified is listed by `--write` in `note` as `rule: ...` so it can be reviewed.
"""
import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from govbudget.entity import Entity  # noqa: E402
from govbudget.schema import ACCOUNT_MAP_COLUMNS, DEPARTMENT_COLUMNS  # noqa: E402

ENTITY = "belvidere_park_district"

REVENUE_RULES = [  # (category, regex on the label), first match wins
    ("property_tax", r"real\s*estate\s*tax|re tax|r\.e\. tax|\bproperty tax"),
    ("replacement_tax", r"replacement|pprtax|\bpprt\b"),
    ("interest", r"\binterest\b"),
    ("bond_proceeds", r"bond proceeds"),
    ("impact_fees", r"impact fee"),
    ("grants", r"grant|intergovernmental"),
    ("donations_sponsorships", r"donation|memorial|sponsor|fundrais"),
    ("rentals", r"rental|\brents?\b|farm lease|lease|shelter"),
]
PERSONNEL = r"salar|wage|longevity|health insur|life insur|imrf|social sec|soc\.? ?sec|medicare|uniform|mileage|training|employee assist|tuition"
CONTRACTUAL = (r"contract|service|electric|natural gas|water/sewer|telephone|phone|internet|disposal|advertis|attorney|legal|professional|police|"
               r"insurance|premium|claims|dues|subscription|meeting|conference|seminar|postage|alarm|elevator|bankcard|fee|marketing|brochure|"
               r"engineering|audit|rent|lease|cell|software|chamber|bond admin|meehan|sponsorship|special event|misc")
SUPPLIES = (r"suppl|snack|chemical|gasoline|gas & oil|gas/oil|fuel|m&r|maint|repair|purchase|equipment|tool|landscape|turf|mower|material|signage|"
            r"memorial|vandalism|amenit|playground|vehicle|building|grounds|herbicide|plant|tree|horticult|pavement|first aid|1st aid|custodial")
FUNCTIONS = {"administration": "Administration", "parks": "Parks and facilities", "recreation": "Recreation programs and facilities",
             "museum": "Museum", "special": "Special recreation", "debt": "Debt service", "capital": "Capital projects"}


def norm_label(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def revenue_category(label, fund, obj):
    l = label.lower()
    for cat, rx in REVENUE_RULES:
        if re.search(rx, l):
            return cat, f"rule: {cat}"
    if fund in ("02", "09", "05") and (obj.startswith("30") or obj.startswith("31")):
        return "program_fees", "rule: program revenue object 300"
    return "other_revenue", "rule: other"


def expense_category(label, fund, obj, new_era, by_label):
    if fund == "21":
        return "exp_capital", "rule: fund 21"
    if fund in ("10", "12"):
        return "exp_debt_service", f"rule: fund {fund}"
    if new_era:
        return {"4": "exp_personnel", "5": "exp_contractual", "6": "exp_supplies", "7": "exp_debt_service", "8": "exp_capital"}.get(obj[0], "exp_other"), "rule: object code"
    n = norm_label(label)
    if n in by_label:
        return by_label[n], "rule: same label in FY2025+ budgets"
    l = label.lower()
    if re.search(r"\bcip\b", l):
        return "exp_capital", "rule: keyword CIP"
    if re.search(r"\bcapital\b", l):  # object 700 "<Program> Capital": equipment purchases, like the FY2025+ 6xxx equipment lines
        return "exp_supplies", "rule: keyword capital (equipment purchase)"
    if re.search(PERSONNEL, l):
        return "exp_personnel", "rule: keyword personnel"
    if re.search(CONTRACTUAL, l):
        return "exp_contractual", "rule: keyword contractual"
    if re.search(SUPPLIES, l):
        return "exp_supplies", "rule: keyword supplies"
    return "exp_other", "rule: other"


def function_of(fund, dept):
    admin = {"0100", "0140", "0150", "0160", "0170", "010", "011", "012", "013", "014"}
    if fund == "01":
        return FUNCTIONS["administration"] if dept in admin else FUNCTIONS["parks"]
    if fund == "02":
        return FUNCTIONS["administration"] if dept in ("0100", "010") else FUNCTIONS["recreation"]
    return {"03": FUNCTIONS["museum"], "08": FUNCTIONS["museum"], "09": FUNCTIONS["special"], "05": FUNCTIONS["special"], "10": FUNCTIONS["debt"],
            "12": FUNCTIONS["debt"], "21": FUNCTIONS["capital"], "25": FUNCTIONS["capital"]}.get(fund, FUNCTIONS["parks"])


def dept_name(labels):
    words = Counter()
    for l in labels:
        w = l.split()
        for n in (1, 2):
            if len(w) >= n:
                words[" ".join(w[:n])] += 1
    if not words:
        return ""
    best, cnt = max(words.items(), key=lambda kv: (kv[1] / len(labels) >= 0.5, len(kv[0].split()) if kv[1] / len(labels) >= 0.5 else 0, kv[1]))
    return best if cnt / len(labels) >= 0.5 else words.most_common(1)[0][0]


def main(write):
    ent = Entity.load(ENTITY)
    lines = []  # (fiscal_year, flow, fund, dept, obj, label)
    for f in sorted(glob.glob(ent.data_path("extracted", "*_revenue.csv")) + glob.glob(ent.data_path("extracted", "*_expense.csv"))):
        flow = "revenue" if f.endswith("_revenue.csv") else "expense"
        for r in csv.DictReader(open(f)):
            fund, dept, obj = r["account"].split("-", 2)
            lines.append((int(r["fiscal_year"]), flow, fund, dept, obj, r["line_item"].strip()))
    latest = {}
    for fy, flow, fund, dept, obj, label in sorted(lines):
        if label:
            latest[(flow, fund, dept, obj)] = label  # the newest document's label wins
    by_label = defaultdict(Counter)
    for fy, flow, fund, dept, obj, label in lines:
        if flow == "expense" and len(dept) == 3 and fund not in ("10", "12", "21") and label:
            c, _ = expense_category(label, fund, obj, True, {})
            by_label[norm_label(label)][c] += 1
    by_label = {k: v.most_common(1)[0][0] for k, v in by_label.items()}
    have = {(r["flow"], r["account_key"]) for r in ent.table("account_map.csv")} if os.path.exists(ent.config_path("account_map.csv")) else set()
    new_rows = []
    for (flow, fund, dept, obj), label in sorted(latest.items()):
        key = f"{dept}-{obj}" if fund == "01" else f"{fund}:{dept}-{obj}"
        if (flow, key) in have:
            continue
        new_era = len(dept) == 3
        if flow == "revenue":
            cat, note = revenue_category(label, fund, obj)
            treat = "tax" if cat in ("property_tax", "replacement_tax") else "non_tax"
        else:
            cat, note = expense_category(label, fund, obj, new_era, by_label)
            treat = "expense"
        new_rows.append({"flow": flow, "account_key": key, "label": label, "department": "", "category_id": cat, "treatment": treat, "note": note})
    # departments
    labs = defaultdict(list)
    for fy, flow, fund, dept, obj, label in lines:
        if label:
            labs[(fund, dept)].append(label)
    have_d = {(r["fund_code"], r["dept_code"]) for r in ent.table("departments.csv")} if os.path.exists(ent.config_path("departments.csv")) else set()
    new_depts = [{"fund_code": f, "dept_code": d, "name": dept_name(l), "function": function_of(f, d), "note": "name derived from line labels; to be curated"}
                 for (f, d), l in sorted(labs.items()) if (f, d) not in have_d]
    dname = {(r["fund_code"], r["dept_code"]): r["name"] for r in ent.table("departments.csv")} if have_d else {}
    dname.update({(r["fund_code"], r["dept_code"]): r["name"] for r in new_depts})
    for r in new_rows:
        fund, rest = Entity.split_key(r["account_key"])
        r["department"] = dname.get((fund, rest.split("-")[0]), "")
    print(f"{len(new_rows)} new accounts ({Counter(r['category_id'] for r in new_rows)}), {len(new_depts)} new departments")
    if write:
        for name, cols, rows in (("account_map.csv", ACCOUNT_MAP_COLUMNS, new_rows), ("departments.csv", DEPARTMENT_COLUMNS, new_depts)):
            path = ent.config_path(name)
            exists = os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                if not exists:
                    w.writeheader()
                w.writerows(rows)


if __name__ == "__main__":
    main("--write" in sys.argv)
