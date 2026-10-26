"""The site's data files (govbudget.export_site): shapes, identities and references."""
import gzip
import json
import os
import re

import pytest

from conftest import ENTITY, ROOT
from govbudget import export_site, schema

DATASET_COLUMNS = {
    "taxes_by_category": ["fiscal_year", "measure", "basis", "category", "gross_tax", "offsets", "net", "exception_ids"],
    "taxes_audit_basis": ["fiscal_year", "group", "budget_audit_original", "budget_audit_final", "actual_audited",
                          "budget_docs_adopted", "actual_budget_doc", "note", "exception_ids"],
    "tax_lines": ["fiscal_year", "measure", "basis", "account", "department", "line_item", "amount", "category", "treatment",
                  "source_doc", "source_column", "page_label", "note", "exception_ids"],
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    export_site.export([ENTITY], str(out))

    def load(*parts):
        with open(os.path.join(out, *parts)) as f:
            return json.load(f)
    load.out = str(out)
    return load


@pytest.mark.parametrize("name", sorted(DATASET_COLUMNS))
def test_dataset_envelope_and_columns(site, name):
    d = site(ENTITY, name + ".json")
    assert d["dataset"] == name and d["entity_id"] == ENTITY
    assert d["title"] and d["description"] and d["sources"]
    assert list(d["columns"]) == DATASET_COLUMNS[name]  # the documented columns are exactly the row keys, in order
    assert d["rows"] and all(list(r) == DATASET_COLUMNS[name] for r in d["rows"])


def test_index_meta_and_palette(site):
    idx = site("index.json")["entities"]
    assert [e["entity_id"] for e in idx] == [ENTITY] and set(idx[0]["datasets"]) == set(DATASET_COLUMNS)
    meta = site(ENTITY, "meta.json")
    assert meta["fy_start_month"] == 5 and set(meta["bases"]) == set(schema.BASES) and meta["datasets"] == idx[0]["datasets"]
    pal = site("palette.json")
    assert pal and all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in pal.values())


def test_categories_json_matches_config(site, entity):
    c = site(ENTITY, "categories.json")["schemes"]
    assert set(c) == {r["scheme"] for r in entity.categories()}
    for scheme, rows in c.items():
        assert [r["sort"] for r in rows] == sorted(r["sort"] for r in rows)
        assert all(r["color"] is None or re.fullmatch(r"#[0-9a-f]{6}", r["color"]) for r in rows)
    assert [r["color"] for r in c["budget_category"][:2]] == ["#2a78d6", "#eb6834"]


def test_fy2027_adopted_taxes(site):
    rows = [r for r in site(ENTITY, "taxes_by_category.json")["rows"] if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted"]
    assert not any(r["category"] == "ALL TAXES" for r in rows)  # rollup rows are not exported
    assert (sum(r["gross_tax"] for r in rows), sum(r["offsets"] for r in rows), sum(r["net"] for r in rows)) == (
        20998468, -532141, 20466327)
    lines = [r for r in site(ENTITY, "tax_lines.json")["rows"] if r["fiscal_year"] == 2027 and r["measure"] == "budget_adopted"]
    assert sum(r["amount"] for r in lines if r["treatment"] == "tax") == 20998468
    assert sum(r["amount"] for r in lines if r["treatment"] == "offset") == -532141


def test_category_names_exist_in_categories_json(site):
    names = {r["name"] for r in site(ENTITY, "categories.json")["schemes"]["budget_category"]}
    assert {r["category"] for r in site(ENTITY, "taxes_by_category.json")["rows"]} <= names
    groups = {r["name"] for r in site(ENTITY, "categories.json")["schemes"]["report_group"]}
    assert {r["group"] for r in site(ENTITY, "taxes_audit_basis.json")["rows"]} <= groups


def test_exception_ids_resolve_and_are_attached(site):
    known = {e["exception_id"] for e in site(ENTITY, "exceptions.json")["exceptions"]}
    used = set()
    for name in DATASET_COLUMNS:
        for r in site(ENTITY, name + ".json")["rows"]:
            assert isinstance(r["exception_ids"], list)
            used.update(r["exception_ids"])
    assert used and used <= known
    # group-scoped exceptions reach the audit-basis rows (telecommunications: 'EX-telecom-group')
    assert any("EX-telecom-group" in r["exception_ids"] for r in site(ENTITY, "taxes_audit_basis.json")["rows"])
    assert any("EX-stale-utility-fy24" in r["exception_ids"] for r in site(ENTITY, "taxes_by_category.json")["rows"])


def test_downloads_and_size_budget(site):
    dl = os.path.join(site.out, ENTITY, "downloads")
    with gzip.open(os.path.join(dl, "facts.csv.gz"), "rt") as f, open(os.path.join(ROOT, "data", ENTITY, "facts.csv")) as g:
        assert f.read() == g.read()
    for name in ("tax_series_by_category.csv", "tax_series_long.csv", "tax_series_audit_basis.csv"):
        assert os.path.exists(os.path.join(dl, name))
    for name in DATASET_COLUMNS:  # a page loads its datasets whole: keep each small
        assert os.path.getsize(os.path.join(site.out, ENTITY, name + ".json")) < 1_000_000


def test_export_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    export_site.export([ENTITY], str(a))
    export_site.export([ENTITY], str(b))
    for root, _, files in os.walk(a):
        for f in files:
            pa = os.path.join(root, f)
            with open(pa, "rb") as x, open(os.path.join(b, os.path.relpath(pa, a)), "rb") as y:
                assert x.read() == y.read(), pa
