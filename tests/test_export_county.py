"""Boone County's site datasets (govbudget.export_county and the county entries of export_park): shapes, identities and the numbers the pages show."""
import json
import os

import pytest

from govbudget import export_site

ENT = "boone_county_il"
COUNTY = ["county_revenue", "county_spending", "county_audit_vs_budget", "county_fund_balances", "county_debt", "property_tax", "retirement"]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("county_site")
    export_site.export([ENT, "belvidere_park_district"], str(out))

    def load(*parts):
        with open(os.path.join(out, *parts)) as f:
            return json.load(f)
    return load


def test_county_gets_its_own_datasets(site):
    meta = site(ENT, "meta.json")
    assert set(meta["datasets"]) == set(COUNTY) | {"reference", "reference_sources"} and meta["noun"] == "county" and meta["profile"] == "county"
    park = set(site("belvidere_park_district", "meta.json")["datasets"])
    assert not park & set(COUNTY)  # the park district keeps the fund_* datasets


@pytest.mark.parametrize("name", COUNTY)
def test_envelope_and_rows(site, name):
    d = site(ENT, name + ".json")
    assert d["dataset"] == name and d["entity_id"] == ENT and d["title"] and d["description"] and d["sources"]
    assert d["rows"] and all(list(r) == list(d["rows"][0]) for r in d["rows"])
    assert set(d["columns"]) == set(d["rows"][0])
    ids = {e["exception_id"] for e in site(ENT, "exceptions.json")["exceptions"]}
    assert all(i in ids for r in d["rows"] for i in r["exception_ids"])


def test_budget_totals(site):
    rev, exp = site(ENT, "county_revenue.json")["rows"], site(ENT, "county_spending.json")["rows"]
    assert sum(r["amount"] for r in rev if r["fiscal_year"] == 2026) == 64678442 and sum(r["amount"] for r in exp if r["fiscal_year"] == 2026) == 73098666
    assert {r["fiscal_year"] for r in rev} == set(range(2020, 2028))  # the FY2017-FY2019 books print no line-level revenue
    assert {r["fiscal_year"] for r in exp} == set(range(2017, 2028))
    assert {r["fiscal_year"] for r in rev + exp if r["basis"] == "requested"} == {2027}


def test_audit_results_cover_funds_with_budgets(site):
    rows = site(ENT, "county_audit_vs_budget.json")["rows"]
    assert len(rows) == 474 and {r["fiscal_year"] for r in rows} == set(range(2015, 2026))
    assert not [r for r in rows if r["fund"].isdigit()]  # every fund has a name (funds.csv)
    gf = {r["fiscal_year"]: r for r in rows if r["fund"] == "General Fund"}
    assert gf[2024]["expense_budget"] == 18014299 and gf[2024]["expense_matches"] and gf[2024]["revenue_budget"] == 18809080


def test_balances_debt_tax_and_retirement(site):
    fb = site(ENT, "county_fund_balances.json")["rows"]
    tot = {r["fiscal_year"]: r["fund_balance"] for r in fb if r["fund"] == "All governmental funds"}
    assert tot[2015] == 24476707 and tot[2025] == 43666257 and set(tot) == set(range(2015, 2026))
    assert [r["unassigned"] for r in fb if r["fund"] == "General Fund" and r["fiscal_year"] == 2025] == [12322370]
    assert not [r for r in fb if "Formerly" in r["fund"]]
    debt = site(ENT, "county_debt.json")["rows"]  # the groups add to the audit's printed total (asserted by the builder)
    assert sum(r["ending_balance"] for r in debt if r["fiscal_year"] == 2025) == 10203886 and sum(r["ending_balance"] for r in debt if r["fiscal_year"] == 2015) == 8451102
    pt = {r["tax_year"]: r for r in site(ENT, "property_tax.json")["rows"] if r["levy"] == "All levies"}
    assert pt[2024]["assessed_valuation"] == 1547516447 and pt[2024]["extension"] == 13429472 and pt[2024]["rate_per_100"] == 0.8586 and pt[2011]["extension"] == 9394319
    levies = [r for r in site(ENT, "property_tax.json")["rows"] if r["tax_year"] == 2024 and r["levy"] != "All levies"]
    assert sum(r["extension"] for r in levies) == pt[2024]["extension"]  # the levies add to the total
    rt = {(r["plan"], r["measurement_year"]): r for r in site(ENT, "retirement.json")["rows"]}
    assert rt[("imrf_regular", 2024)]["npl"] == -980767 and rt[("slep", 2024)]["npl"] == 206719 and rt[("imrf_eco", 2024)]["npl"] == 1247169
    assert all(r["tpl"] - r["fnp"] == r["npl"] for r in rt.values())
