"""Reference series by fiscal year -> data/boone_county_il/reference_by_fy.csv.  Reads only committed snapshots; no network.

One row per fiscal year (December 1 to November 30, named for the year it ends) FY2012-FY2027:
  population          Census Bureau July 1 estimate of Boone County for the fiscal year's own calendar year N (July 1, N falls inside December N-1 .. November N); the
                      newest estimate is carried forward when it is not yet published (population_flag carried_forward); population_vintage says which vintage
  cpi_fy_avg          mean of the monthly CPI-U December(N-1) .. November(N) of the months published; cpi_flag provisional = the year is not over, missing_month:YYYY-MM
                      = the source has no value for a month (October 2025: the survey was not collected)
  cpi_factor          base-year mean / this year's mean: multiply a nominal amount by it to get constant base-year dollars (base FY2025, the latest complete year)
  eav_levy_year       tax (levy) year N-1, the levy collected in fiscal year N (the budgets' 'Actual 2024 EAV (Taxes Paid FY 2025)')
  eav_city            the county's assessed valuation of that levy year from the audits' Property Tax Levies and Collections (the column keeps the name the shared
                      export reads); for levy year 2025 the FY2026 budget's estimate, flagged EX-reference-eav-estimate
Run order: fetch_reference.py (network, occasionally) -> extract_audit_tax.py + build_facts.py -> build_reference.py.
"""
import csv, os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity, read_csv, write_csv  # noqa: E402

E = Entity.load("boone_county_il")
BASE_FY = 2025
COLUMNS = ["entity_id", "fiscal_year", "population", "population_year", "population_vintage", "population_flag", "cpi_fy_avg", "cpi_months",
           "cpi_flag", "cpi_base_fy", "cpi_factor", "eav_levy_year", "eav_city", "eav_flag", "eav_source_audits"]


def population():
    by_year = {}
    for r in sorted(read_csv(E.data_path("reference", "population_estimates.csv")), key=lambda r: (int(r["vintage"]), r["estimate_year"])):
        if r["estimate_year"].isdigit():
            by_year[int(r["estimate_year"])] = (int(r["population"]), int(r["vintage"]))  # the newest vintage that has the year
    return by_year


def cpi():
    months = {r["month"][:7]: float(r["cpi_u"]) for r in read_csv(E.data_path("reference", "cpi_u_monthly.csv"))}
    latest = max(months)
    out = {}
    for fy in range(2012, 2028):
        keys = [f"{fy - 1}-12"] + [f"{fy}-{m:02d}" for m in range(1, 12)]  # December of the year before .. November
        vals = [months[k] for k in keys if k in months]
        if not vals:  # the year has not begun: carry the previous year's mean, flagged
            out[fy] = (out[fy - 1][0], 0, "provisional_carried_forward")
            continue
        gaps = [k for k in keys if k not in months and k <= latest]
        future = [k for k in keys if k > latest]
        flag = "provisional" if future else (("missing_month:" + "+".join(gaps)) if gaps else "")
        out[fy] = (sum(vals) / len(vals), len(vals), flag)
    return out


def eav():
    seen = defaultdict(dict)  # tax year -> {audit fy: value}
    for r in read_csv(E.data_path("property_tax_levies.csv")):
        if r["section"] == "assessed_valuation":
            seen[int(r["tax_year"])][int(r["period_fy"])] = int(r["amount"])
    out = {}
    for y, by in sorted(seen.items()):
        if len(set(by.values())) > 1:
            sys.exit(f"tax year {y}: the audits disagree {by}")
        out[y] = (next(iter(by.values())), sorted(by), "")
    for r in read_csv(E.data_path("reference", "eav_budget_estimates.csv")):
        y = int(r["tax_year"])
        if y not in out:
            out[y] = (int(r["eav"]), [], "EX-reference-eav-estimate")
    return out


def main():
    pop, cp, ev = population(), cpi(), eav()
    base = cp[BASE_FY][0]
    assert "provisional" not in cp[BASE_FY][2], "the base year must be over"
    rows = []
    for fy in range(2012, 2028):
        py, flag = fy, ""
        if py not in pop:  # the July estimate has not been published: carry the newest forward
            py, flag = max(pop), "carried_forward"
        p, vintage = pop[py]
        avg, n, cflag = cp[fy]
        levy = fy - 1
        e = ev.get(levy)
        rows.append(dict(entity_id=E.id, fiscal_year=fy, population=p, population_year=py, population_vintage=vintage, population_flag=flag,
                         cpi_fy_avg=round(avg, 3), cpi_months=n, cpi_flag=cflag, cpi_base_fy=BASE_FY, cpi_factor=round(base / avg, 5), eav_levy_year=levy,
                         eav_city=e[0] if e else "", eav_flag=e[2] if e and e[2] else ("" if e else "not_available"),
                         eav_source_audits="|".join(f"FY{a}" for a in e[1]) if e else ""))
    write_csv(E.data_path("reference_by_fy.csv"), COLUMNS, rows)
    print(f"{len(rows)} fiscal years -> data/{E.id}/reference_by_fy.csv; CPI base FY{BASE_FY} mean {base:.3f}")


if __name__ == "__main__":
    main()
