"""Cross-checks between the park district's audits, the debt notes and the fund statements -> data/belvidere_park_district/audit_debt_crosschecks.csv.

Usage: python3 pipelines/belvidere_park_district/build_debt_crosschecks.py

Each row is one comparison of two independently printed figures: the legal debt margin schedule's assets in debt service funds against the Bond & Interest and
Alternate Bond & Interest fund balances; this year's prior-year column against the previous audit's figure (legal debt margin, non-referendum margin);
a liability's beginning balance against the previous audit's ending balance; the statement of changes' debt service principal against the bonds retired
in the debt note; the tax year's collections against the property taxes of the statement; the assessed valuation schedule's overlapping tax years
between consecutive audits.
"""
import csv
import glob
import os
import re
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_budget_report as R  # noqa: E402

ENT = R.Entity.load(R.ENTITY)
D = ENT.data_path("extracted", "audits")
BONDS = ("park_bond_2015A", "agreement_deed", "park_bond_2019", "park_bond_2018", "alt_rev_2012A", "park_bond_2020", "park_bond_2021", "park_bond_2022", "park_bond_2023",
         "park_bond_2024", "park_bond_2025", "refunding_2020")


def rd(name):
    return list(csv.DictReader(open(os.path.join(D, name))))


def years():
    return sorted(int(re.search(r"FY(\d{4})_audit_debt_checks", p).group(1)) for p in glob.glob(os.path.join(D, "FY*_audit_debt_checks.csv")))


def main():
    out = []

    explained = {}
    opath = ENT.data_path("overrides", "audit_debt_crosschecks.csv")  # known, explained differences: kind explained, raw "<fiscal year>|<check>"
    if os.path.exists(opath):
        explained = {o["raw"]: o["reason"] for o in csv.DictReader(open(opath, newline="")) if o["kind"] == "explained"}
    used = set()

    def add(fy, name, a, b, tol=0):
        res = "ok" if a == b else ("ok: rounding" if a is not None and b is not None and abs(a - b) <= tol else "FAIL")
        if res == "FAIL" and f"{fy}|{name}" in explained:
            res = "explained: " + explained[f"{fy}|{name}"]
            used.add(f"{fy}|{name}")
        out.append((fy, name, a, b, res))

    ys = years()
    for fy in ys:
        doc = f"FY{fy}_audit"
        stm = rd(f"{doc}_fund_statements.csv")
        legal = rd(f"{doc}_legal_debt_margin.csv")
        act = rd(f"{doc}_debt_activity.csv")
        asd = rd(f"{doc}_assessed_valuations.csv")
        # 1. assets in debt service funds
        fb = sum(Decimal(r["amount"]) for r in stm if r["statement"] == "balance_sheet" and r["line_item"] == "Total fund balances"
                 and r["fund"] in ("Bond & Interest Fund", "Alternate Bond & Interest Fund"))
        less = next(Decimal(r["amount"]) for r in legal if r["schedule"] == "legal_debt_margin" and r["line_item"].startswith("Less") and r["column"] == "this_year")
        add(fy, "legal debt margin: assets in debt service funds = Bond & Interest + Alternate Bond & Interest fund balances", fb, less)
        # 4/7. debt principal in the statement vs bonds retired in the note
        pr = next(Decimal(r["amount"]) for r in stm if r["statement"] == "changes" and r["line_item"] == "Principal" and r["fund"] == "Total Governmental Funds")
        retired = sum(Decimal(r["amount"]) for r in act if r["column"] == "retired" and r["instrument_id"] in BONDS)
        if fy >= 2019:
            add(fy, "statement debt service principal = bonds retired in the debt note", pr, retired)
        # 6. collections vs property taxes
        ty = fy - 1
        coll = next(Decimal(r["amount"]) for r in asd if int(r["tax_year"]) == ty and r["section"] == "collections" and r["line_item"] == "Total")
        pt = next(Decimal(r["amount"]) for r in stm if r["statement"] == "changes" and r["line_item"] == "Property taxes" and r["fund"] == "Total Governmental Funds")
        add(fy, f"collections of tax year {ty} = property taxes of the statement of changes", pt, coll, tol=1)
        # previous audit
        if fy - 1 in ys:
            pdoc = f"FY{fy - 1}_audit"
            plegal = rd(f"{pdoc}_legal_debt_margin.csv")
            for key in ("legal_debt_margin", "non_referendum"):
                cur = {r["line_item"]: Decimal(r["amount"]) for r in legal if r["schedule"] == key and r["column"] == "prior_year"}
                prev = {r["line_item"]: Decimal(r["amount"]) for r in plegal if r["schedule"] == key and r["column"] == "this_year"}
                for k, v in cur.items():
                    nk = re.sub(r"[^a-z]", "", k.lower())
                    pk = next((x for x in prev if re.sub(r"[^a-z]", "", x.lower()) == nk), None)
                    if pk is not None and not k.startswith("Statutory"):
                        add(fy, f"{key}: {k}: prior-year column = previous audit", prev[pk], v)
            pact = {r["instrument_id"]: Decimal(r["amount"]) for r in rd(f"{pdoc}_debt_activity.csv") if r["column"] == "ending" and r["instrument_id"]}
            for r in act:
                if r["column"] == "beginning" and r["instrument_id"] in pact:
                    add(fy, f"{r['description']}: beginning = previous audit's ending", pact[r["instrument_id"]], Decimal(r["amount"]))
            pasd = rd(f"{pdoc}_assessed_valuations.csv")
            prev = {(r["tax_year"], r["section"], re.sub(r"[^a-z]", "", r["line_item"].lower())[:6]): Decimal(r["amount"]) for r in pasd}
            for r in asd:
                k = (r["tax_year"], r["section"], re.sub(r"[^a-z]", "", r["line_item"].lower())[:6])
                if k in prev and (r["line_item"] in ("Total", "Rate setting assessed valuation") or r["section"] != "percent"):
                    add(fy, f"assessed {r['section']} {r['line_item']} tax year {r['tax_year']} = previous audit", prev[k], Decimal(r["amount"]))
    # IMRF: the same calendar year printed in several audits' required supplementary information
    imrf = {}
    for path in sorted(glob.glob(os.path.join(D, "FY*_audit_imrf_changes.csv"))):
        for r in csv.DictReader(open(path)):
            imrf.setdefault((int(r["calendar_year"]), r["line_item"]), []).append((int(r["fiscal_year"]), Decimal(r["amount"])))
    for (cy, line), vals in sorted(imrf.items()):
        for fy, v in vals[1:]:
            add(fy, f"IMRF {line} calendar {cy}: audit FY{fy} = audit FY{vals[0][0]}", vals[0][1], v)
    imrf_c = {}
    for path in sorted(glob.glob(os.path.join(D, "FY*_audit_imrf_contributions.csv"))):
        for r in csv.DictReader(open(path)):
            imrf_c.setdefault(int(r["calendar_year"]), []).append((int(r["fiscal_year"]), tuple(Decimal(r[k]) for k in ("actuarially_determined", "actual", "deficiency", "covered_payroll"))))
    for cy, vals in sorted(imrf_c.items()):
        for fy, v in vals[1:]:
            add(fy, f"IMRF contributions calendar {cy}: audit FY{fy} = audit FY{vals[0][0]}", vals[0][1], v)
    for k in explained:
        if k not in used:
            out.append((k.split("|")[0], k.split("|", 1)[1], None, None, "FAIL"))  # an explanation that no longer matches a difference
    with open(ENT.data_path("audit_debt_crosschecks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "check", "a", "b", "result"])
        w.writerows(out)
    fails = [o for o in out if o[4] == "FAIL"]
    print(f"{len(out)} comparisons, {len(fails)} differences")
    for o in fails:
        print("  ", o[0], o[1], o[2], o[3])


if __name__ == "__main__":
    main()
