"""Reference series by fiscal year -> data/belvidere_il/reference_by_fy.csv (T2). Reads only committed snapshots; no network.

One row per fiscal year (May 1 to April 30, named for the year it ends) FY2012-FY2027:
  population          Census Bureau July 1 estimate of year N-1 (inside the fiscal year); the newest estimate is carried forward when the next
                      is not yet published (population_flag carried_forward); population_vintage says which vintage it comes from
  cpi_fy_avg          mean of the monthly CPI-U May..April of the months published; cpi_flag provisional = the year is not over, missing_month:YYYY-MM = the
                      source has no value for a month (October 2025: the survey was not collected), the mean uses the months it has
  cpi_factor          base-year mean / this year's mean: multiply a nominal amount by it to get constant base-year dollars (base FY2026)
  eav_levy_year       tax levy year L = N-2: the levy collected in fiscal year N (the audits' own Property Tax Levies and Collections pairing)
  eav_city            city equalized assessed valuation of that levy year, from the audits (cross-checked: every audit that prints a levy year agrees)
Run order: fetch_reference.py (network, occasionally) -> extract_audit_eav.py -> build_reference.py.
"""
import csv
import glob
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity, read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
BASE_FY = 2026
COLUMNS = ["entity_id", "fiscal_year", "population", "population_year", "population_vintage", "population_flag", "cpi_fy_avg", "cpi_months",
           "cpi_flag", "cpi_base_fy", "cpi_factor", "eav_levy_year", "eav_city", "eav_flag", "eav_source_audits"]


def population():
    est = {}
    for r in read_csv(E.data_path("reference", "population_estimates.csv")):
        if r["estimate_year"].isdigit():
            est[(int(r["vintage"]), int(r["estimate_year"]))] = int(r["population"])
    newest = max(v for v, _ in est)
    # July 1 of year Y belongs to fiscal year Y+1; per calendar year take the newest vintage that has it
    by_year = {}
    for (v, y), p in sorted(est.items()):
        by_year[y] = (p, v)
    return by_year


def cpi():
    months = {r["month"][:7]: float(r["cpi_u"]) for r in read_csv(E.data_path("reference", "cpi_u_monthly.csv"))}
    out = {}
    for fy in range(2012, 2028):
        keys = [f"{fy - 1}-{m:02d}" for m in range(5, 13)] + [f"{fy}-{m:02d}" for m in range(1, 5)]  # May of the year before .. April
        vals = [months[k] for k in keys if k in months]
        latest = max(months)
        gaps = [k for k in keys if k not in months and k <= latest]  # a month the source never published (October 2025: no data was collected)
        future = [k for k in keys if k > latest]
        flag = "provisional" if future else (("missing_month:" + "+".join(gaps)) if gaps else "")
        out[fy] = (sum(vals) / len(vals), len(vals), flag)
    return out


def eav():
    seen = defaultdict(dict)  # levy year -> {audit fy: value}
    for path in sorted(glob.glob(E.data_path("extracted", "audits", "FY*_audit_eav.csv"))):
        for r in read_csv(path):
            if r["eav_city"]:
                seen[int(r["levy_year"])][int(r["fiscal_year_of_audit"])] = int(r["eav_city"])
    over = {int(r["key"]): r for r in read_csv(E.config_path("reference_overrides.csv")) if r["series"] == "eav_city"}
    out = {}
    for y, by in sorted(seen.items()):
        values = set(by.values())
        if len(values) > 1:
            o = over.get(y)
            if o is None or int(o["value"]) not in values:
                sys.exit(f"levy year {y}: the audits disagree {by} and no override decides it (reference_overrides.csv)")
            out[y] = (int(o["value"]), sorted(by), o["exception_id"])
        else:
            out[y] = (values.pop(), sorted(by), "")
    return out


def main():
    pop, cp, ev = population(), cpi(), eav()
    base = cp[BASE_FY][0]
    assert "provisional" not in cp[BASE_FY][2], "the base year must be over"
    rows = []
    for fy in range(2012, 2028):
        py = fy - 1
        flag = ""
        if py not in pop:  # the July estimate has not been published: carry the newest forward
            py, flag = max(pop), "carried_forward"
        p, vintage = pop[py]
        avg, n, cflag = cp[fy]
        levy = fy - 2
        e = ev.get(levy)
        rows.append(dict(entity_id=E.id, fiscal_year=fy, population=p, population_year=py, population_vintage=vintage, population_flag=flag,
                         cpi_fy_avg=round(avg, 3), cpi_months=n, cpi_flag=cflag, cpi_base_fy=BASE_FY,
                         cpi_factor=round(base / avg, 5), eav_levy_year=levy, eav_city=e[0] if e else "",
                         eav_flag=e[2] if e and e[2] else ("" if e else "not_extracted"), eav_source_audits="|".join(f"FY{a}" for a in e[1]) if e else ""))
    write_csv(E.data_path("reference_by_fy.csv"), COLUMNS, rows)
    print(f"{len(rows)} fiscal years -> data/{E.id}/reference_by_fy.csv; CPI base FY{BASE_FY} mean {base:.3f}")


if __name__ == "__main__":
    main()
