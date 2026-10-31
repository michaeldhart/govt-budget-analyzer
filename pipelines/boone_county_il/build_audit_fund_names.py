"""Map the fund names printed in the audits' per-fund schedules to fund codes -> entities/boone_county_il/audit_fund_names.csv.

The FY2015-FY2020 schedules print "Fund 002" in the title (match `verified`); later audits print only the name. A name is matched to a code through the
codes printed with the same name in other years, then through ALIASES (names the county changed, checked against the budget worksheets' fund names:
`by_name`); a name with neither stays unmatched (`none`: funds the county does not put in its budget worksheets: grants, Circuit Clerk funds, trusts ...).
Usage: python3 pipelines/boone_county_il/build_audit_fund_names.py
"""
import csv, glob, os, re, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
OUT = os.path.join(ROOT, "data/boone_county_il/extracted/audits")

ALIASES = {  # normalized audit name -> fund code, with the reason
    "healthdepartment": ("013", "County Health Department Fund - Fund 013 before FY2021"),
    "healthgrant": ("014", "County Health Grant Fund - Fund 014 before FY2021"),
    "highwaymatching": ("004", "County Matching Fund - Fund 004 before FY2021"),
    "tortimmunity": ("018", "Liability Insurance Fund - Fund 018 before FY2021"),
    "courthousebond": ("046", "Courthouse Construction Fund - Fund 046 before FY2021"),
    "landfillclosure": ("025", "Landfill Closing Fund - Fund 025 before FY2021"),
    "maintenanceandchildsupport": ("158", "Child Support Maintenance Fund - Fund 158 before FY2021"),
    "animalservices": ("012", "Animal Control Fund - Fund 012 before FY2021"),
    "animalservicesother": ("012", "Animal Services Fund - Other (FY2021-23)"),
    "deathcertificatesurcharge": ("127", "Coroner's Grant Fund - Fund 127 before FY2024; worksheet fund 127 is Death Certificate Surcharge"),
    "statesattorneycollection": ("201", "Financial Compliance Unit - Fund 201 before FY2024; worksheet fund 201 is State's Attorney Collection"),
    "urbantransportation": ("144", "worksheet fund 144"),
    "saleinerror": ("163", "worksheet fund 163"),
    "motorfueltax": ("005", "County Motor Fuel Tax - Fund 005"),
    "federalforfeiteds": ("152", "Federal Forfeited Funds - Fund 152 before FY2019"),
}


def norm(name):
    s = re.sub(r"(?i)\s*-?\s*funds? \d{3}.*$|\(.*\)", "", name).lower().replace("fund", "")
    return re.sub(r"[^a-z]", "", s)


def main():
    seen = defaultdict(lambda: {"codes": set(), "names": set(), "years": set()})
    for path in sorted(glob.glob(os.path.join(OUT, "FY*_audit_fund_schedules.csv"))):
        fy = int(os.path.basename(path)[2:6])
        for r in csv.DictReader(open(path)):
            n = norm(r["fund_name"])
            seen[n]["names"].add(r["fund_name"])
            seen[n]["years"].add(fy)
            if r["fund_code"]:
                seen[n]["codes"].add(r["fund_code"])
    rows = []
    for n, v in sorted(seen.items()):
        if len(v["codes"]) == 1:
            code, match, why = next(iter(v["codes"])), "verified", "fund number printed in the schedule title"
        elif n in ALIASES:
            code, why = ALIASES[n]
            match = "by_name"
        elif len(v["codes"]) > 1:
            sys.exit(f"{n}: several printed codes {v['codes']}")
        else:
            code, match, why = "", "none", "no fund number printed and no budget worksheet fund of this name"
        rows.append(dict(norm_name=n, fund_code=code, match=match, years=f"{min(v['years'])}-{max(v['years'])}", example=sorted(v["names"])[0], note=why))
    with open(os.path.join(ROOT, "entities/boone_county_il/audit_fund_names.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, ["norm_name", "fund_code", "match", "years", "example", "note"])
        w.writeheader()
        w.writerows(rows)
    c = defaultdict(int)
    for r in rows:
        c[r["match"]] += 1
    print(len(rows), "names", dict(c))


if __name__ == "__main__":
    main()
