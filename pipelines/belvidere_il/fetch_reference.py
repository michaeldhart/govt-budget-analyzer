"""Download the public reference series the site's per-capita and inflation views need, and keep small snapshots in data/belvidere_il/reference/.

Usage: python3 pipelines/belvidere_il/fetch_reference.py        (network: FRED and www2.census.gov, no API key)
  cpi_u_monthly.csv         FRED series CPIAUCNS: CPI-U, all items, U.S. city average, not seasonally adjusted, 1982-84 = 100 (source: BLS)
  population_estimates.csv  Census Bureau Population Estimates Program, City of Belvidere (place 17-05092), July 1 estimates, two vintages:
                            2010-2019 from vintage 2019 and 2020-2025 from vintage 2025 (the vintages are not spliced: each is the Bureau's own series)
The snapshots are committed; build_reference.py reads only them. Re-run this to pick up new months and a new vintage, then update retrieved_on in
entities/belvidere_il/reference_sources.csv.
"""
import csv
import io
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from govbudget.entity import Entity, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
OUT = E.data_path("reference")
UA = {"User-Agent": "Mozilla/5.0 (govt-budget-analyzer reference fetch)"}
CENSUS = "https://www2.census.gov/programs-surveys/popest/datasets"
POP_FILES = [  # (vintage, url, first estimate year, columns)
    (2019, f"{CENSUS}/2010-2019/cities/totals/sub-est2019_17.csv", 2010),
    (2025, f"{CENSUS}/2020-2025/cities/totals/sub-est2025_17.csv", 2020),
]


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def cpi():
    rows = list(csv.reader(io.StringIO(get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS"))))
    assert rows[0] == ["observation_date", "CPIAUCNS"] or rows[0][0].lower().startswith(("date", "observation")), rows[0]
    out = [dict(month=d, cpi_u=v) for d, v in rows[1:] if v not in ("", ".") and d >= "2010-01-01"]  # the project needs FY2011 on
    write_csv(os.path.join(OUT, "cpi_u_monthly.csv"), ["month", "cpi_u"], out)
    print(f"CPI-U: {len(out)} months, {out[0]['month']} to {out[-1]['month']}")


def population():
    rows = []
    for vintage, url, first in POP_FILES:
        for r in csv.DictReader(io.StringIO(get(url))):
            if r["SUMLEV"] == "157" and r["STATE"] == "17" and r["PLACE"] == "05092":  # City of Belvidere, Boone County
                assert r["NAME"] == "Belvidere city", r["NAME"]
                for k, v in r.items():
                    if k.startswith("POPESTIMATE"):
                        rows.append(dict(vintage=vintage, estimate_year=int(k[len("POPESTIMATE"):]), population=int(v), source_file=url))
                    elif k.startswith(("ESTIMATESBASE", "CENSUS2010POP")):
                        rows.append(dict(vintage=vintage, estimate_year=k, population=int(v), source_file=url))
    write_csv(os.path.join(OUT, "population_estimates.csv"), ["vintage", "estimate_year", "population", "source_file"], rows)
    print(f"population: {len(rows)} values")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    cpi()
    population()
