"""The park district's site datasets (govbudget.export_park): shapes, identities and the numbers the pages show."""
import json
import os

import pytest

from govbudget import export_site

ENT = "belvidere_park_district"
PARK = ["fund_revenue", "fund_spending", "fund_spending_by_department", "fund_revenue_by_department", "levies", "fund_audit_vs_budget", "fund_balances", "fund_debt"]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("park_site")
    export_site.export([ENT, "belvidere_il"], str(out))

    def load(*parts):
        with open(os.path.join(out, *parts)) as f:
            return json.load(f)
    return load


def test_park_gets_its_datasets_and_the_city_keeps_its_own(site):
    park, city = site(ENT, "meta.json")["datasets"], site("belvidere_il", "meta.json")["datasets"]
    assert set(park) == set(PARK) | {"reference", "reference_sources"}
    assert not set(PARK) & set(city) and "expenses_by_dept_object" in city and "tax_lines" not in park


@pytest.mark.parametrize("name", PARK)
def test_envelope_and_rows(site, name):
    d = site(ENT, name + ".json")
    assert d["dataset"] == name and d["entity_id"] == ENT and d["title"] and d["description"] and d["sources"]
    assert d["rows"] and all(list(r) == list(d["rows"][0]) or name == "levies" for r in d["rows"])
    assert set(d["columns"]) == set(d["rows"][0]) | ({"percent_increase", "eav", "rate_per_100", "extension", "extension_rate_per_100"} if name == "levies" else set())
    ids = {e["exception_id"] for e in site(ENT, "exceptions.json")["exceptions"]}
    assert all(i in ids for r in d["rows"] for i in r["exception_ids"])


def test_budget_totals_equal_the_adopted_lines(site):
    rev, exp = site(ENT, "fund_revenue.json")["rows"], site(ENT, "fund_spending.json")["rows"]
    assert sum(r["amount"] for r in rev if r["fiscal_year"] == 2026) == 9322993
    assert sum(r["amount"] for r in exp if r["fiscal_year"] == 2026) == 9299597
    assert {r["fiscal_year"] for r in rev if r["basis"] == "requested"} == {2027}  # only FY2027 is still a draft
    assert 2022 not in {r["fiscal_year"] for r in rev}  # the FY2022 budget document has no revenue report


def test_levies_debt_and_balances(site):
    levies = site(ENT, "levies.json")["rows"]
    agg = {r["fiscal_year"]: r for r in levies if r["fund_code"] == ""}
    assert [agg[y]["levy"] for y in range(2022, 2028)] == [3962074, 4218185, 4329577, 4533060, 4737628, 4943251]
    assert agg[2025]["rate_per_100"] == round(4533060 / 758732730 * 100, 4) or agg[2025]["eav"]  # tax year 2024 valuation
    assert [agg[y]["extension"] for y in range(2022, 2026)] == [3851324, 4128720, 4372109, 4557070] and agg[2026]["extension"] is None
    assert [agg[y]["extension_rate_per_100"] for y in range(2022, 2026)] == [0.53223, 0.54416, 0.52927, 0.49246]
    assert agg[2027]["status"] == "draft" and agg[2024]["status"] == "adopted" and agg[2023]["status"] == "adopted"
    debt = site(ENT, "fund_debt.json")["rows"]
    assert sum(r["ending_balance"] for r in debt if r["fiscal_year"] == 2025) == 4887628
    fb = {r["fiscal_year"]: r["fund_balance"] for r in site(ENT, "fund_balances.json")["rows"] if r["fund"] == "Total Governmental Funds"}
    assert fb[2019] == 5342620 and fb[2025] == 6966239


def test_reference_rows_carry_the_population_exception(site):
    rows = site(ENT, "reference.json")["rows"]
    assert [r["fiscal_year"] for r in rows] == list(range(2019, 2028)) and all("EX-reference-population-scaled" in r["exception_ids"] for r in rows)


def test_department_datasets_add_to_the_fund_level_totals(site):
    for dept, whole in (("fund_spending_by_department", "fund_spending"), ("fund_revenue_by_department", "fund_revenue")):
        d, w = site(ENT, dept + ".json")["rows"], site(ENT, whole + ".json")["rows"]
        for fy in sorted({r["fiscal_year"] for r in w}):
            assert sum(r["amount"] for r in d if r["fiscal_year"] == fy) == sum(r["amount"] for r in w if r["fiscal_year"] == fy), (dept, fy)
        assert {r["fiscal_year"] for r in d} == {r["fiscal_year"] for r in w}
        assert {(r["fiscal_year"], r["basis"]) for r in d} == {(r["fiscal_year"], r["basis"]) for r in w}


def test_department_identity_is_fund_and_name(site):
    rows = site(ENT, "fund_spending_by_department.json")["rows"]
    assert all(r["dept_id"] == f"{r['fund']}|{r['department']}" and r["dept_codes"] for r in rows)
    # one function per department, and a program keeps one identity across the FY2025 renumbering (Third Base was 02-8000, then 02-050)
    assert len({(r["dept_id"], r["function"]) for r in rows}) == len({r["dept_id"] for r in rows})
    tb = {r["fiscal_year"]: set(r["dept_codes"]) for r in rows if r["dept_id"] == "Recreation|Third Base"}
    assert tb[2024] == {"02-8000"} and tb[2026] == {"02-050"}
    assert len({r["dept_id"] for r in rows}) > 50
