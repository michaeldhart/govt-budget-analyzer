"""Reference series by fiscal year -> data/belvidere_park_district/reference_by_fy.csv (T2, Phase 8). Reads only committed snapshots; no network.

One row per fiscal year FY2019-FY2027. The district's fiscal year is the calendar year, so the series are matched to the calendar year:
  population          the district's own population (its FY2025 Annual Financial Report to the Comptroller: 32,342, the only figure for the district's boundary,
                      which is larger than the city: Belvidere Township had 29,441 in 2020 and the city 25,337) scaled through the Census Bureau's July 1
                      estimates for the City of Belvidere: city estimate of year N x (32,342 / city estimate of 2025). Flag `scaled_from_district_figure`
                      (`+carried_forward` when the Bureau has no July 1 estimate for the year yet). The ratio, 1.2702, is assumed constant: it is a scaling of a
                      published series, not a count, and the city and the district grow at the same rate by construction.
  cpi_fy_avg          mean of the monthly CPI-U January..December of the year (published months only); cpi_flag provisional = year not over,
                      missing_month:YYYY-MM = the source has no value (October 2025);
                      provisional_carried_forward = no month published yet (FY2027), the previous year's mean
  cpi_factor          base-year mean / this year's mean: constant base-year dollars (base FY2025, the latest complete calendar year; the city's series uses FY2026)
  eav_levy_year       tax year N-1: the levy ordinance of fiscal year N levies tax year N-1 (extended and collected in year N)
  eav_city            the district's rate-setting assessed valuation of that tax year from the audits' Assessed Valuations schedule (column name shared with the
                      city's reference table); blank when no audit reports it yet (FY2026-27)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity, read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_park_district")
BASE_FY = 2025
YEARS = range(2019, 2028)
COLUMNS = ["entity_id", "fiscal_year", "population", "population_year", "population_vintage", "population_flag", "cpi_fy_avg", "cpi_months",
           "cpi_flag", "cpi_base_fy", "cpi_factor", "eav_levy_year", "eav_city", "eav_flag", "eav_source_audits"]


def city_population():
    est = {}
    for r in read_csv(E.data_path("reference", "population_estimates.csv")):
        if r["estimate_year"].isdigit():
            est[int(r["estimate_year"])] = (int(r["population"]), int(r["vintage"]))  # sorted by vintage in the file: the newest vintage that has the year wins
    return est


def cpi():
    months = {r["month"][:7]: float(r["cpi_u"]) for r in read_csv(E.data_path("reference", "cpi_u_monthly.csv"))}
    latest = max(months)
    out = {}
    for fy in YEARS:
        keys = [f"{fy}-{m:02d}" for m in range(1, 13)]
        vals = [months[k] for k in keys if k in months]
        gaps = [k for k in keys if k not in months and k <= latest]
        flag = "provisional" if any(k > latest for k in keys) else (("missing_month:" + "+".join(gaps)) if gaps else "")
        if not vals:  # no month of the year is published yet: carry the previous year's mean forward
            out[fy] = (out[fy - 1][0], 0, "provisional_carried_forward")
            continue
        out[fy] = (sum(vals) / len(vals), len(vals), flag)
    return out


def eav():
    out = {}
    for r in read_csv(E.data_path("assessed_valuations.csv")):
        if r["section"] == "valuation" and r["line_item"] == "Rate setting assessed valuation":
            y, v = int(r["tax_year"]), int(r["amount"])
            if out.get(y, (v,))[0] != v:
                sys.exit(f"tax year {y}: audits disagree {out[y][0]} vs {v}")
            out.setdefault(y, (v, []))[1].append(r["doc_id"].split("_")[0])
    return out


def main():
    pop, cp, ev = city_population(), cpi(), eav()
    afr = read_csv(E.data_path("reference", "district_population.csv"))[0]
    ratio = int(afr["population"]) / pop[int(afr["fiscal_year"])][0]
    base = cp[BASE_FY][0]
    assert "provisional" not in cp[BASE_FY][2], "the base year must be over"  # October 2025 has no published value; the mean uses 11 months
    rows = []
    for fy in YEARS:
        py, flag = (fy, "") if fy in pop else (max(pop), "+carried_forward")
        p, vintage = pop[py]
        avg, n, cflag = cp[fy]
        e = ev.get(fy - 1)
        rows.append(dict(entity_id=E.id, fiscal_year=fy, population=round(p * ratio), population_year=py, population_vintage=vintage,
                         population_flag="scaled_from_district_figure" + flag, cpi_fy_avg=round(avg, 3), cpi_months=n, cpi_flag=cflag, cpi_base_fy=BASE_FY,
                         cpi_factor=round(base / avg, 5), eav_levy_year=fy - 1, eav_city=e[0] if e else "", eav_flag="" if e else "not_yet_reported",
                         eav_source_audits="|".join(sorted(set(e[1]))) if e else ""))
    write_csv(E.data_path("reference_by_fy.csv"), COLUMNS, rows)
    print(f"{len(rows)} fiscal years -> data/{E.id}/reference_by_fy.csv; population ratio {ratio:.4f}; CPI base FY{BASE_FY} mean {base:.3f}")


if __name__ == "__main__":
    main()
