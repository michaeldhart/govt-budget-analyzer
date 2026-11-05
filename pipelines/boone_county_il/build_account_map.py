"""Add the accounts, departments and funds of the budget worksheets to the entity's mapping CSVs (only rows that are missing; the CSVs are the authority).

Usage: python3 pipelines/boone_county_il/build_account_map.py [--write]
Rules: revenue accounts by object code (taxes, permits, fees, interest, reimbursements, grants ...), expense accounts by the leading digits of the object code
(40-41 personnel, 45-49 supplies, 50-55/57-58 contractual, 56 and 60 capital, 705 debt, 80/81/86 claims and grants, 89 transfers); a department's function group
comes from the department (General Fund) or the fund (other funds). Every rule is written to the `note` column of the row it creates ("rule: ...").
"""
import csv, os, re, subprocess, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402
from govbudget.schema import ACCOUNT_MAP_COLUMNS, DEPARTMENT_COLUMNS  # noqa: E402

E = Entity.load("boone_county_il")
DOCS = ("FY2025_budget", "FY2026_budget", "FY2027_budget_prelim")
BOOK_DOCS = ("FY2017_budget", "FY2018_budget", "FY2019_budget", "FY2020_budget", "FY2021_budget", "FY2022_budget", "FY2023_budget", "FY2024_budget")  # budget books (extract_budget_book.py), oldest first
EXTRA_FUND_NAMES = {"047": "Animal Services Bond Fund", "056": "Coroner's Fees Fund", "101": "Grant Fund", "123": "Opioid Grant Fund",
                    "143": "Regional Communication Study Fund", "154": "HAVA Election Grant Fund", "157": "Land/Cash Fees Fund"}
FUND_COLUMNS = ["fund_code", "name", "type", "numbering", "audit_name", "note"]

TAX = {"property_tax": ["301000", "301100", "301200", "302000", "313000"], "sales_tax": ["303000", "304000", "305000"], "income_tax": ["306100", "307100"],
       "replacement_tax": ["306200", "307200"],
       "other_tax": ["306000", "306420", "306800", "306950", "307000", "307001", "307420", "307800", "308000"]}
TAX_BY_OBJECT = {o: c for c, objs in TAX.items() for o in objs}
FEES_NOT_TAX = {"306300", "306400", "307300", "307400"}  # penalties on late taxes and tax sale fees: fees, not taxes
GRANT_EXCEPT = {"380141": "fees_fines", "381000": "fees_fines", "381100": "fees_fines", "381600": "fees_fines", "381500": "misc_revenue", "379300": "fees_fines"}


UNCODED_REVENUE = [  # label keywords for book lines printed without an object code (FY2020 General Fund revenue detail), first match wins
    (r"transfer", "transfers_in", "non_tax"), (r"property tax", "property_tax", "tax"), (r"sales tax", "sales_tax", "tax"), (r"income tax", "income_tax", "tax"),
    (r"replacement", "replacement_tax", "tax"), (r"use tax|video gaming|hotel|motor fuel|cannabis", "other_tax", "tax"), (r"penalty", "fees_fines", "non_tax"),
    (r"liquor|licen|building fee|permit", "licenses_permits", "non_tax"), (r"interest", "interest", "non_tax"),
    (r"salary|probation|reimburs|assessor|psb operations|township|workers", "reimbursements", "non_tax"), (r"grant", "grants", "non_tax"),
    (r"fee|circuit court|fines|jail housing|counsel|gis mapping|website|coroner|clerk|first offender|finance court|work release|zoning", "fees_fines", "non_tax")]


def uncoded_revenue_category(label):
    for rx, cat, treat in UNCODED_REVENUE:
        if re.search(rx, label, re.I):
            return cat, treat, "rule: label keyword (line printed without an object code)"
    return "misc_revenue", "non_tax", "rule: label keyword (line printed without an object code); default miscellaneous"


def revenue_category(obj):
    if obj in TAX_BY_OBJECT:
        return TAX_BY_OBJECT[obj], "tax", "rule: object code is a tax"
    if obj in FEES_NOT_TAX:
        return "fees_fines", "non_tax", "rule: penalty or fee charged on taxes, not a tax"
    if obj in GRANT_EXCEPT:
        return GRANT_EXCEPT[obj], "non_tax", "rule: object code"
    p = obj[:3]
    if p == "329":
        return "licenses_permits", "non_tax", "rule: 329xxx"
    if p in ("340", "365"):
        return "fees_fines", "non_tax", "rule: 340xxx/365xxx"
    if p in ("351", "360"):
        return "misc_revenue", "non_tax", "rule: 351xxx/360xxx"
    if p == "370":
        return "interest", "non_tax", "rule: 370xxx"
    if p in ("375", "376", "377"):
        return "reimbursements", "non_tax", "rule: 375xxx-377xxx"
    if p in ("379", "380"):
        return "grants", "non_tax", "rule: 379xxx/380xxx"
    if p in ("390", "395"):
        return "proceeds", "non_tax", "rule: 390xxx/395xxx"
    if p == "399":
        return "transfers_in", "non_tax", "rule: 399xxx"
    sys.exit(f"no revenue rule for object {obj}")


def expense_category(obj, label=""):
    p2, p3 = obj[:2], obj[:3]
    if p2 in ("40", "41"):
        return "exp_personnel", "rule: 40xxxx-41xxxx"
    if p2 in ("45", "46", "49"):
        return "exp_supplies", "rule: 45xxxx-49xxxx"
    if p2 == "56" or p2 == "60":
        return "exp_capital", "rule: 56xxxx/60xxxx"
    if p2 in ("50", "51", "52", "53", "54", "55", "57", "58"):
        return "exp_contractual", "rule: 50xxxx-58xxxx"
    if p3 == "705":
        return "exp_debt", "rule: 705xxx"
    if p2 == "70":
        return "exp_contractual", "rule: 700000"
    if p2 in ("80", "81", "86"):
        return "exp_claims_grants", "rule: 80xxxx/81xxxx/86xxxx"
    if p2 == "89":
        if not re.search(r"(?i)transfer|txfr|\bfund\b", label):  # the books print transfers out as 'Employee Health Care Fund -37'
            return "exp_contractual", "rule: 89xxxx whose label is not a transfer (Probation 'Placement' is a program cost)"
        return "exp_transfers", "rule: 89xxxx"
    return "exp_other", "rule: 59/79/90/99"


GF_FUNCTION = {"0000": "fn_general", "0051": "fn_general", "0052": "fn_general", "0053": "fn_general", "0054": "fn_general", "0057": "fn_general",
               "0058": "fn_general", "0059": "fn_general", "0060": "fn_general", "0062": "fn_general", "0076": "fn_general", "0077": "fn_general",
               "0078": "fn_general", "0080": "fn_general", "0081": "fn_general", "0082": "fn_general", "0084": "fn_general", "0085": "fn_general",
               "0086": "fn_general", "0087": "fn_general", "0088": "fn_general",
               "0056": "fn_safety", "0061": "fn_safety", "0063": "fn_safety", "0070": "fn_safety", "0073": "fn_safety",
               "0055": "fn_judiciary", "0064": "fn_judiciary", "0065": "fn_judiciary", "0066": "fn_judiciary", "0069": "fn_judiciary",
               "0072": "fn_judiciary", "0075": "fn_judiciary", "0071": "fn_judiciary"}
FUND_FUNCTION = {"002": "fn_highways", "003": "fn_highways", "004": "fn_highways", "005": "fn_highways", "144": "fn_highways", "145": "fn_highways",
                 "011": "fn_health", "012": "fn_health", "013": "fn_health", "100": "fn_health", "120": "fn_health", "127": "fn_health",
                 "019": "fn_judiciary", "020": "fn_judiciary", "021": "fn_judiciary", "039": "fn_judiciary", "040": "fn_judiciary", "041": "fn_judiciary",
                 "044": "fn_judiciary", "163": "fn_judiciary", "201": "fn_judiciary", "202": "fn_judiciary", "158": "fn_judiciary",
                 "036": "fn_safety", "038": "fn_safety", "045": "fn_safety", "151": "fn_safety", "152": "fn_safety", "153": "fn_safety",
                 "160": "fn_welfare", "166": "fn_welfare", "167": "fn_welfare",
                 "030": "fn_capital", "046": "fn_capital", "048": "fn_capital", "049": "fn_capital"}  # everything else: general
FUND_TYPE = {"001": "general", "030": "capital projects", "046": "debt service / bond", "048": "debt service / bond", "049": "debt service / bond",
             "160": "permanent / memorial", "166": "permanent / memorial", "167": "permanent / memorial"}


FN_NAME = {c["category_id"]: c["name"] for c in E.categories("function_group")}


def read_names():
    """fund names and department names printed in the worksheets (latest document wins)."""
    funds, depts = {}, {}
    for d in DOCS:
        t = subprocess.run(["pdftotext", "-layout", os.path.join(E.sources_dir, "budgets", f"{d}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
        for l in t.split("\n"):
            m = re.match(r"^\s*(?:Boone County, Illinois\s+)?Fund:?\s*(\d{3})\s*-\s*(.*?)\s*$", l)
            if m and "Total" not in l and "Surplus" not in l:
                funds[m.group(1)] = re.sub(r"\s+", " ", m.group(2))
            m = re.match(r"^\s*Department:\s*(\d{4})\s*-\s*(.*?)\s*$", l)
            if m and "Total" not in l and "Surplus" not in l:
                depts[m.group(1)] = re.sub(r"\s+", " ", m.group(2))
    return funds, depts


def book_obj(code, label):
    if not code:
        return "U" + re.sub(r"[^a-z0-9]", "", label.lower())[:24]
    a, _, b = code.partition(".")
    return a + (b + "000")[:3]


def book_flow(r):
    if r["code"]:
        return "revenue" if r["code"][0] == "3" else "expense"
    t = (r["section"] + " " + r["table"]).lower()
    return "revenue" if "revenue" in r["section"].lower() or ("revenue" in t and "expenditure" not in t) else "expense"


def book_dept(r, wdept):
    if r["dept_code"]:
        return r["dept_code"]
    fund = r["fund_code"]
    if fund == "001":
        return "0000"
    if fund in ("030", "145"):
        return "0" + fund
    c = wdept.get(fund)
    non0 = [d for d, _ in (c or Counter()).most_common() if d != "0000"]
    return non0[0] if non0 else (c.most_common(1)[0][0] if c else "0" + fund)


def book_key(r, wdept):
    """(flow, fund, dept, object) of a budget-book line; object codes are the ERP codes written with a dot."""
    if not r["fund_code"]:
        return None
    return (book_flow(r), r["fund_code"], book_dept(r, wdept), book_obj(r["code"], r["label"]))


book_transfer = set()


def main(write):
    global book_transfer
    lines = defaultdict(lambda: defaultdict(Counter))
    labels = {}
    for d in DOCS:  # later documents override earlier labels
        for r in csv.DictReader(open(E.data_path("extracted", f"{d}_lines.csv"))):
            key = (r["flow"], r["fund_code"], r["dept_code"], r["account_printed"].split("-")[2])
            labels[key] = r["label"]
    fund_names, dept_names = read_names()
    fund_names = {**EXTRA_FUND_NAMES, **fund_names}
    wdept = defaultdict(Counter)
    for (fl, fu, de, ob) in labels:
        wdept[fu][de] += 1
    book_label, book_transfer = {}, set()
    for d in BOOK_DOCS:
        for r in csv.DictReader(open(E.data_path("extracted", f"{d}_book_lines.csv"))):
            if "Summary" in r["table"]:
                continue
            k = book_key(r, wdept)
            if k:
                book_label[k] = r["label"]
                if "transfer" in r["section"].lower() and k[0] == "revenue":
                    book_transfer.add(k)
    for k, lab in book_label.items():
        labels.setdefault(k, lab)  # worksheet labels win; the books add the accounts that no worksheet has
    existing_funds = {r["fund_code"]: r for r in E.table("funds.csv")} if os.path.exists(E.config_path("funds.csv")) else {}
    funds = dict(existing_funds)
    for code in sorted({k[1] for k in labels}):
        if code not in funds:
            funds[code] = dict(fund_code=code, name=fund_names.get(code, code), type=FUND_TYPE.get(code, "special revenue"), numbering="all",
                               audit_name="", note="")
    existing_depts = {(r["fund_code"], r["dept_code"]): r for r in E.table("departments.csv")} if os.path.exists(E.config_path("departments.csv")) else {}
    depts = dict(existing_depts)
    for (flow, fund, dept, obj) in labels:
        if (fund, dept) not in depts:
            name = dept_names.get(dept) if fund == "001" else None
            name = name if name and not name.startswith(("Treasurer Total", "Urban Transportation Fund Total")) else fund_names.get(fund, f"{fund}-{dept}")
            fn = FN_NAME[GF_FUNCTION.get(dept, "fn_general") if fund == "001" else FUND_FUNCTION.get(fund, "fn_general")]
            depts[(fund, dept)] = dict(fund_code=fund, dept_code=dept, name=name, function=fn, note="rule: department of the General Fund by name (County Corrections is Judiciary and courts, as the audit presents it); other funds by fund")
    amap = {(r["flow"], r["account_key"]): r for r in E.table("account_map.csv")} if os.path.exists(E.config_path("account_map.csv")) else {}
    added = 0
    for (flow, fund, dept, obj), label in sorted(labels.items()):
        key = f"{fund}:{dept}-{obj}"
        if (flow, key) in amap:
            continue
        dname = depts[(fund, dept)]["name"]
        if (flow, fund, dept, obj) in book_transfer and not obj.startswith("399"):
            cat, treat, note = "transfers_in", "non_tax", "rule: line sits under the book's 'Transfers In' heading"
        elif flow == "revenue" and re.search(r"(?i)transfer", label) and not obj.startswith("399"):
            cat, treat, note = "transfers_in", "non_tax", "rule: label says transfer in (book code differs from the ERP's 399xxx)"
        elif flow == "revenue" and obj.startswith("U"):
            cat, treat, note = uncoded_revenue_category(label)
        elif flow == "revenue":
            cat, treat, note = revenue_category(obj)
        else:
            cat, note = expense_category(obj if not obj.startswith("U") else "99", label)
            treat = "expense"
        amap[(flow, key)] = dict(flow=flow, account_key=key, label=label, department=dname, category_id=cat, treatment=treat, note=note)
        added += 1
    print(f"{len(funds)} funds, {len(depts)} departments, {len(amap)} accounts ({added} new)")
    if write:
        write_csv(E.config_path("funds.csv"), FUND_COLUMNS, sorted(funds.values(), key=lambda r: r["fund_code"]))
        write_csv(E.config_path("departments.csv"), DEPARTMENT_COLUMNS, sorted(depts.values(), key=lambda r: (r["fund_code"], r["dept_code"])))
        write_csv(E.config_path("account_map.csv"), ACCOUNT_MAP_COLUMNS, sorted(amap.values(), key=lambda r: (r["flow"], r["account_key"])))


if __name__ == "__main__":
    main("--write" in sys.argv)
