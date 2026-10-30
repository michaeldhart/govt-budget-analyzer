"""Belvidere Park District (second government): config validates, every extracted account is mapped, and each committed budget extract still ties
to the totals its own totals file holds (the extractors validate against the printed totals; this re-checks the committed CSVs without OCR)."""
import csv
import glob
import os
from collections import defaultdict
from decimal import Decimal

from conftest import ROOT, read as read_csv

ENT = "belvidere_park_district"
EXTRACTED = os.path.join(ROOT, "data", ENT, "extracted")
DOCS = ("FY2019_budget FY2020_budget FY2021_budget FY2022_budget FY2023_budget FY2023_budget_draft FY2024_budget_draft FY2025_budget "
        "FY2025_budget_draft FY2026_budget FY2026_budget_draft FY2027_budget_draft FY2023_budget_amended FY2023_budget_amended_oct").split()


def rows(doc, flow):
    return list(csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_{flow}.csv"))))


def test_config_validates():
    import sys
    sys.path.insert(0, ROOT)
    from govbudget.entity import Entity
    from govbudget.validate import validate_config
    assert validate_config(Entity.load(ENT)) == []


def test_every_extracted_account_is_mapped():
    import sys
    sys.path.insert(0, ROOT)
    from govbudget.entity import Entity
    E = Entity.load(ENT)
    mapped = {(r["flow"], r["account_key"]) for r in E.table("account_map.csv")}
    missing = set()
    for doc in DOCS:
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                fund, dept, obj = r["account"].split("-", 2)
                key = f"{dept}-{obj}" if fund == "01" else f"{fund}:{dept}-{obj}"
                if (flow, key) not in mapped:
                    missing.add((doc, flow, key))
    assert not missing, sorted(missing)[:10]


def test_fund_reports_tie_to_their_printed_totals():
    """Layout D (My Budget Report): fund revenue and expense lines equal the Revenue Total / Expense Total in both columns."""
    for doc in ("FY2025_budget", "FY2025_budget_draft", "FY2026_budget", "FY2026_budget_draft", "FY2027_budget_draft"):
        sums = defaultdict(Decimal)
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                sums[(r["fund_code"], "fund_revenues" if flow == "revenue" else "fund_expenses", r["column"])] += Decimal(r["amount"])
        printed = {(r["fund_code"], r["kind"], r["column"]): Decimal(r["amount"]) for r in csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv")))
                   if r["kind"] in ("fund_revenues", "fund_expenses")}
        assert printed and all(sums[k] == v for k, v in printed.items()), doc


def test_version_reports_tie_to_fund_subtotals():
    """Layout B and the amended reports: each fund's lines equal its Subtotal."""
    for doc in ("FY2022_budget", "FY2023_budget", "FY2023_budget_draft", "FY2024_budget_draft"):
        sums = defaultdict(Decimal)
        for flow in ("revenue", "expense"):
            for r in rows(doc, flow):
                sums[(r["fund_code"], flow)] += Decimal(r["amount"])
        subs = [r for r in csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv"))) if r["kind"] == "fund_subtotal"]
        assert subs and all(sums[(r["fund_code"], r["flow"])] == Decimal(r["amount"]) for r in subs), doc


def test_operating_budget_blocks_tie():
    """Layout A: block totals equal the lines of their section (FY2020's Meehan block is the city's own 1,000 difference, flagged allow_diff)."""
    for doc in ("FY2019_budget", "FY2020_budget", "FY2021_budget"):
        totals = list(csv.DictReader(open(os.path.join(EXTRACTED, f"{doc}_totals.csv"))))
        assert any(t["kind"] == "grand" for t in totals), doc
        assert all(t["flag"] in ("", "allow_diff", "unprinted") for t in totals if t["kind"] != "summary"), doc
    flagged = [t["name"] for t in csv.DictReader(open(os.path.join(EXTRACTED, "FY2020_budget_totals.csv"))) if t["flag"] == "allow_diff"]
    assert sorted(flagged) == ["Total Meehan Expenses", "Total Other Funds Expenses", "Total Other Funds Income"]


def test_fy2027_draft_headline():
    rev = sum(Decimal(r["amount"]) for r in rows("FY2027_budget_draft", "revenue") if r["column"] == "budget_current")
    exp = sum(Decimal(r["amount"]) for r in rows("FY2027_budget_draft", "expense") if r["column"] == "budget_current")
    assert (rev, exp) == (Decimal("8707625"), Decimal("8696267.00"))
    assert rev - exp == Decimal("11358")  # the draft's printed Report Surplus


AUDITED = ("FY2019_audit", "FY2020_audit", "FY2021_audit", "FY2022_audit", "FY2023_audit", "FY2024_audit", "FY2025_audit")


def test_audit_fund_statements_pass_every_check():
    """Every identity the audit extractor tests (sums, balance equation, net change, ending = beginning + change, schedule variances, ties between the
    schedules and the statement) holds; only the audit's own sign errors in the variance column are allowed, each with a written reason."""
    allowed = 0
    for doc in AUDITED:
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_checks.csv"))))
        assert checks and all(c["result"] == "ok" or c["result"].startswith("allowed: ") for c in checks), doc
        allowed += sum(1 for c in checks if c["result"] != "ok")
    assert allowed == 7  # FY2023 2, FY2024 2, FY2025 3 (EX-audit-variance-sign)


def test_audit_vs_budget_counts():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, "audit_vs_budget.csv"))))
    assert len(rows) == 49
    assert sum(1 for r in rows if r["expense_matches"] == "1") == 39 and sum(1 for r in rows if r["expense_matches"] != "") == 49
    assert sum(1 for r in rows if r["revenue_matches"] == "1") == 33 and sum(1 for r in rows if r["revenue_matches"] != "") == 42  # FY2022 has no revenue report


def test_fund_balance_chain_across_audits():
    """Each audit's beginning fund balance equals the previous audit's ending balance, for every fund column (FY2020-FY2025: 48 comparisons, 0 breaks)."""
    end, beg = {}, {}
    for y in range(2019, 2026):
        for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"FY{y}_audit_fund_statements.csv"))):
            if r["statement"] == "changes" and r["line_item"] in ("Beginning", "Ending"):
                (end if r["line_item"] == "Ending" else beg)[(y, r["fund"])] = int(r["amount"])
    pairs = [(k, v) for k, v in beg.items() if k[0] >= 2020 and (k[0] - 1, k[1]) in end]
    assert len(pairs) == 48
    assert all(end[(k[0] - 1, k[1])] == v for k, v in pairs)


def test_debt_notes_and_imrf_pass_every_check():
    """Debt note (outstanding, annual requirements, liability activity), legal debt margin, assessed valuation schedule: every identity holds in all seven audits;
    the IMRF required supplementary information in the four text audits."""
    tot = 0
    for doc in AUDITED:
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_debt_checks.csv"))))
        assert checks and all(c["result"].startswith("ok") for c in checks), doc
        tot += len(checks)
    assert tot == 485
    for doc in ("FY2019_audit", "FY2023_audit", "FY2024_audit", "FY2025_audit"):
        checks = list(csv.DictReader(open(os.path.join(EXTRACTED, "audits", f"{doc}_imrf_checks.csv"))))
        assert checks and all(c["result"].startswith("ok") for c in checks), doc


def test_debt_crosschecks():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, "audit_debt_crosschecks.csv"))))
    assert len(rows) == 1025
    assert not [r for r in rows if r["result"] == "FAIL"]
    assert sum(1 for r in rows if r["result"].startswith("explained")) == 2  # FY2020 refunding, FY2024 GASB 101


def test_fy2025_debt_and_pension_headlines():
    out = {r["line_item"]: r["amount"] for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", "FY2025_audit_legal_debt_margin.csv")))
           if r["schedule"] == "legal_debt_margin" and r["column"] == "this_year"}
    assert out["Legal debt margin"] == "22554102" and out["Total debt applicable to limit"] == "4410000"
    npl = [r["amount"] for r in csv.DictReader(open(os.path.join(EXTRACTED, "audits", "FY2025_audit_imrf_changes.csv")))
           if r["calendar_year"] == "2025" and r["line_item"].startswith("Net Pension Liability/(Asset)")]
    assert npl == ["-965991"]


def test_documents_table():
    from govbudget import schema
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, "documents.csv"))))
    assert list(rows[0]) == schema.DOCUMENT_COLUMNS and len(rows) == 44
    assert {r["status"] for r in rows} <= set(schema.DOC_STATUSES)
    assert all(len(r["sha256"]) == 64 for r in rows)


def canonical(name):
    return list(csv.DictReader(open(os.path.join(ROOT, "data", ENT, f"{name}.csv"))))


def test_budget_facts_structure():
    import sys
    sys.path.insert(0, ROOT)
    from govbudget import schema
    from govbudget.entity import Entity
    all_facts = canonical("facts")
    assert list(all_facts[0]) == schema.FACT_COLUMNS and len(all_facts) == 5308
    facts = [f for f in all_facts if f["source_type"] == "budget"]
    assert len(facts) == 4012
    from collections import Counter
    assert Counter(f["basis"] for f in facts) == {"adopted": 2156, "requested": 1543, "revised": 313}
    assert len({(f["doc_id"], f["row_seq"], f["basis"], f["period_fy"]) for f in all_facts}) == len(all_facts)
    mapped = {(r["flow"], r["account_key"]) for r in Entity.load(ENT).table("account_map.csv")}
    assert all((f["flow"], f["account_key"]) in mapped for f in facts)
    docs = {d["doc_id"] for d in canonical("documents")}
    assert {f["doc_id"] for f in facts} <= docs
    # the FY2024 draft stands in for the adopted budget: every one of its facts carries the placeholder exception
    assert all("EX-fy2024-budget-placeholder" in f["exception_ids"] for f in facts if f["doc_id"] == "FY2024_budget_draft")
    assert sum(1 for f in facts if f["exception_ids"]) == 336


def test_budget_facts_equal_the_extracts():
    """Per document, flow and fund the facts hold the extract's first (adopted/requested/amended) column: nothing dropped, nothing added, cents rounded."""
    from decimal import ROUND_HALF_UP
    facts = canonical("facts")
    first = {"budget", "approved", "requested", "budget_original", "amended"}
    for doc in DOCS:
        if doc.endswith("_amended_oct"):
            continue
        got = sorted((f["flow"], f["account_printed"], int(f["amount"])) for f in facts if f["doc_id"] == doc)
        want = sorted((flow, r["account"], int(Decimal(r["amount"]).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
                      for flow in ("revenue", "expense") if os.path.exists(os.path.join(EXTRACTED, f"{doc}_{flow}.csv"))
                      for r in rows(doc, flow) if r["column"] in first)
        assert got == want, doc


def test_fund_totals_reconcile_with_the_facts():
    """A printed fund total equals the sum of the fund's facts (whole dollars: a fund with cents may differ by the rounding of its lines)."""
    facts = canonical("facts")
    sums = defaultdict(int)
    cents = defaultdict(int)
    for f in facts:
        sums[(f["doc_id"], f["fund_code"], f["flow"], f["basis"])] += int(f["amount"])
        cents[(f["doc_id"], f["fund_code"], f["flow"], f["basis"])] += "rounded_from_cents" in f["flag"]
    n = 0
    for t in canonical("control_totals"):
        if t["kind"] in ("fund_revenues", "fund_expenses"):
            k = (t["doc_id"], t["name"].split()[0], "revenue" if t["kind"] == "fund_revenues" else "expense", t["basis"])
            assert abs(sums[k] - int(t["amount"])) <= max(1, (cents[k] + 1) // 2), (k, sums[k], t["amount"])
            n += 1
    assert n == 133


def test_report_totals_reconcile_with_the_facts():
    facts = canonical("facts")
    for t in canonical("control_totals"):
        if t["kind"] in ("rollup_revenues", "rollup_expenses") and t["doc_id"] in ("FY2022_budget", "FY2023_budget", "FY2023_budget_draft", "FY2024_budget_draft", "FY2023_budget_amended"):
            flow = "revenue" if t["kind"] == "rollup_revenues" else "expense"
            got = sum(int(f["amount"]) for f in facts if f["doc_id"] == t["doc_id"] and f["flow"] == flow and f["basis"] == t["basis"])
            assert abs(got - int(t["amount"])) <= 3, (t["doc_id"], flow, got, t["amount"])


def test_corrections_table():
    from govbudget import schema
    rows = canonical("corrections")
    assert rows and list(rows[0]) == schema.CORRECTION_COLUMNS and len(rows) == 23
    assert {r["method"] for r in rows} <= set(schema.CORRECTION_METHODS) and all(r["raw_value"] != r["final_value"] for r in rows)


def test_canonical_columns_match_schema():
    from govbudget import schema
    want = {"facts": schema.FACT_COLUMNS, "documents": schema.DOCUMENT_COLUMNS, "accounts": schema.ACCOUNT_COLUMNS, "control_totals": schema.CONTROL_TOTAL_COLUMNS,
            "corrections": schema.CORRECTION_COLUMNS, "fund_statements": schema.FUND_STATEMENT_COLUMNS,
            **{k: v for k, v in schema.DEBT_PENSION_TABLES.items() if k != "opeb_rollforward"}, **schema.TAX_DEBT_TABLES}
    for name, cols in want.items():
        with open(os.path.join(ROOT, "data", ENT, f"{name}.csv"), newline="") as f:
            assert next(csv.reader(f)) == cols, name
    assert not os.path.exists(os.path.join(ROOT, "data", ENT, "opeb_rollforward.csv"))  # the park has no OPEB (entity.json optional_tables)


def test_audit_facts_reconcile_with_the_fund_statements():
    """Per audit and fund, the revenue and expenditure facts (actual, without the other financing lines) add to the statement of changes' totals: 49 fund-years x 2."""
    sums = defaultdict(int)
    for f in canonical("facts"):
        if f["source_type"] == "audit" and f["basis"] == "actual_audited" and "prior_year" not in f["flag"] and "other_financing" not in f["flag"]:
            sums[(f["doc_id"], f["audit_section"].split(" | ")[0], f["flow"])] += int(f["amount"])
    n = 0
    for r in canonical("fund_statements"):
        if r["statement"] == "changes_in_fund_balance" and r["line_id"] in ("total_revenues", "total_expenditures") and r["fund"] != "Total Governmental Funds":
            assert sums[(r["doc_id"], r["fund"], "revenue" if r["line_id"] == "total_revenues" else "expense")] == int(r["amount"]), r
            n += 1
    assert n == 98


def test_audit_fact_bases():
    from collections import Counter
    c = Counter((f["source_type"], f["basis"]) for f in canonical("facts") if f["source_type"] == "audit")
    assert c == {("audit", "audit_original"): 350, ("audit", "audit_final"): 350, ("audit", "actual_audited"): 596}  # 92 of them are other financing lines
    assert all("combined_column" in f["flag"] for f in canonical("facts") if f["source_type"] == "audit" and f["source_column"] == "budget")


def test_fund_statements_have_line_ids_and_balance():
    rows = canonical("fund_statements")
    assert len(rows) == 2672 and all(r["line_id"] for r in rows)
    v = {(r["doc_id"], r["fund"], r["statement"], r["line_id"]): int(r["amount"]) for r in rows}
    for (doc, fund, st, lid), a in v.items():
        if lid == "total_assets":
            assert a == v[(doc, fund, st, "total_liabilities_deferred_fund_balances")]
        if lid == "fund_balance_ending":
            assert a == v[(doc, fund, st, "fund_balance_beginning")] + v[(doc, fund, st, "net_change_in_fund_balance")]


def test_debt_pension_and_tax_tables():
    counts = {"debt_balances": 276, "debt_issue_terms": 16, "debt_service_schedule": 128, "pension_rollforward": 648, "pension_summary": 288,
              "legal_debt_margin": 212, "assessed_valuations": 666}
    for name, n in counts.items():
        assert len(canonical(name)) == n, name
    bal = {(r["doc_id"], r["line_id"], r["measure"]): int(r["amount"]) for r in canonical("debt_balances")}
    assert bal[("FY2025_audit", "total", "ending")] == 4887628 and bal[("FY2025_audit", "total", "balance")] == 4887628
    roll = {(r["doc_id"], r["column_year"], r["line_id"]): int(r["amount"]) for r in canonical("pension_rollforward")}
    for (doc, y, lid), a in roll.items():
        if lid == "npl_ending":
            assert a == roll[(doc, y, "tpl_ending")] - roll[(doc, y, "fnp_ending")]
    assert roll[("FY2025_audit", "2025", "npl_ending")] == -965991
    ex = {(r["doc_id"], r["year_ending"]): r["exception_ids"] for r in canonical("debt_service_schedule") if r["line_id"] == "refunding_2020"}
    assert ex[("FY2020_audit", "2031-2032")] == "EX-fy2020-debt-schedule-row"


def test_park_is_on_the_site_with_fund_based_datasets():
    import json
    cfg = json.load(open(os.path.join(ROOT, "entities", ENT, "entity.json")))
    assert cfg["fund_based_budget"] is True and "export_site" not in cfg


def test_audit_vs_budget_is_built_from_the_facts():
    """The comparison table's audit and budget columns are sums of facts: recompute three of them independently."""
    facts = canonical("facts")
    rows = canonical("audit_vs_budget")
    assert len(rows) == 49
    code = {(r["fiscal_year"], r["fund"]): r["doc_fund_code"] for r in rows}
    for r in rows:
        fy, fc = int(r["fiscal_year"]), r["doc_fund_code"]
        mine = [f for f in facts if f["source_type"] == "audit" and f["fund_code"] == fc and int(f["period_fy"]) == fy and "prior_year" not in f["flag"]]
        assert sum(int(f["amount"]) for f in mine if f["basis"] == "audit_original" and f["flow"] == "expense") == int(r["audit_budget_expenditures"])
        assert sum(int(f["amount"]) for f in mine if f["basis"] == "actual_audited" and f["flow"] == "revenue" and "other_financing" not in f["flag"]) == int(r["audit_actual_revenues"])
        if r["doc_expenses"] != "":
            assert sum(int(f["amount"]) for f in facts if f["source_type"] == "budget" and f["fund_code"] == fc and int(f["period_fy"]) == fy and f["flow"] == "expense"
                       and f["basis"] == r["budget_basis"]) == int(r["doc_expenses"])
    assert {r["budget_basis"] for r in rows if r["fiscal_year"] == "2024"} == {"requested"}  # the placeholder
    assert {r["budget_basis"] for r in rows if r["fiscal_year"] != "2024"} == {"adopted"}


def test_audit_categories_vs_budget():
    rows = canonical("audit_categories_vs_budget")
    assert len(rows) == 107 and {r["group"] for r in rows} == {"personnel", "contractual", "supplies_capital", "debt_service"}
    comp = [r for r in rows if r["matches"] != ""]
    assert (sum(int(r["matches"]) for r in comp), sum(1 for r in comp if abs(float(r["diff_pct"] or 0)) <= 1), sum(1 for r in comp if abs(float(r["diff_pct"] or 0)) <= 5)) == (32, 60, 76)
    # the groups add up to the fund totals of audit_vs_budget.csv: a classification difference moves money between groups, it does not lose any
    totals = {(r["fiscal_year"], r["fund"]): r for r in canonical("audit_vs_budget")}
    sums = defaultdict(lambda: [0, 0])
    for r in rows:
        sums[(r["fiscal_year"], r["fund"])][0] += int(r["audit_original"])
        sums[(r["fiscal_year"], r["fund"])][1] += int(r["budget"] or 0)
    for k, (a, b) in sums.items():
        assert a == int(totals[k]["audit_budget_expenditures"]), k
        if totals[k]["doc_expenses"] != "":
            assert b == int(totals[k]["doc_expenses"]), k
    ex = {r["diff"] for r in rows if r["fund"] == "General Fund" and r["fiscal_year"] in ("2019", "2020")}
    assert max(abs(int(x)) for x in ex) <= 1000  # the General Fund of FY2019-FY2020 is within a thousand dollars in every group


def test_ordinances_cover_every_document_and_tie():
    """Appropriation (FY2019-FY2027) and levy (FY2022-FY2027) ordinances: every document is in the tables, each ordinance's SUMMARY (e) estimate of taxes
    equals the levy ordinance's aggregate levy in 5 of 6 years (FY2023: the appropriation prints 100 more, EX-ordinance-fy2023-arithmetic), and the
    fund levies add to the aggregate (FY2024's aggregate leaves out the 50,000 Prior Year Adjustment)."""
    from decimal import Decimal
    lines, summ = canonical("ordinance_lines"), canonical("ordinance_summary")
    docs = {r["doc_id"]: r for r in canonical("documents") if r["doc_type"] in ("appropriation", "levy")}
    assert len(docs) == 16 and {r["doc_id"] for r in lines} == set(docs) == {r["doc_id"] for r in summ}
    assert sum(r["doc_type"] == "appropriation" for r in docs.values()) == 9
    e = {r["period_fy"]: Decimal(r["amount"]) for r in summ if r["ordinance"] == "appropriation" and r["item"] == "e"}
    agg = {}
    for r in summ:
        if r["ordinance"] == "levy" and r["item"] in ("aggregate_levy", "summary_total") and r["doc_id"] != "FY2023_levy_draft":
            agg[r["period_fy"]] = Decimal(r["amount"])
    diff = {fy: e[fy] - agg[fy] for fy in agg}
    assert set(agg) == {str(y) for y in range(2022, 2028)} and sorted(fy for fy, d in diff.items() if d) == ["2023"] and diff["2023"] == 100
    for doc, d in docs.items():
        if d["doc_type"] != "levy":
            continue
        levies = [Decimal(r["amount"]) for r in lines if r["doc_id"] == doc and r["section"] == "levy"]
        a = next(Decimal(r["amount"]) for r in summ if r["doc_id"] == doc and r["item"] in ("aggregate_levy", "summary_total"))
        assert sum(levies) in (a, a + 50000, a - 4290 if doc == "FY2025_levy" else a), doc  # EX-ordinance-fy2025-social-security-levy
    assert all(r["exception_ids"] for r in lines if r["doc_id"] in ("FY2023_levy", "FY2024_levy", "FY2025_levy", "FY2022_levy"))


def test_reference_series_by_fiscal_year():
    """FY2019-FY2027: population scales the district's own AFR figure (32,342 in 2025) by the city's Census series; CPI is the calendar-year mean with base FY2025;
    EAV is the audits' rate-setting valuation of tax year N-1 (FY2026-27 not reported yet)."""
    rows = {int(r["fiscal_year"]): r for r in canonical("reference_by_fy")}
    assert sorted(rows) == list(range(2019, 2028))
    assert rows[2025]["population"] == "32342" and rows[2025]["cpi_factor"] == "1.0" and rows[2025]["cpi_flag"] == "missing_month:2025-10"
    assert all(r["population_flag"].startswith("scaled_from_district_figure") for r in rows.values())
    assert rows[2019]["eav_city"] == "612647631" and rows[2025]["eav_city"] == "925368616" and rows[2026]["eav_flag"] == "not_yet_reported"
    assert all(int(r["eav_levy_year"]) == int(y) - 1 for y, r in rows.items())
    assert [int(rows[y]["eav_city"]) for y in range(2019, 2026)] == sorted(int(rows[y]["eav_city"]) for y in range(2019, 2026))
    ratio = int(rows[2024]["population"]) / 25428  # the city's 2024 July 1 estimate (vintage 2025)
    assert abs(ratio - 32342 / 25462) < 1e-3
    src = {r["series"] for r in read_csv(os.path.join(ROOT, "entities", ENT, "reference_sources.csv"))}
    assert src == {"cpi", "population", "eav"}


def test_ordinance_copies_in_budget_documents():
    """The ordinance copies appended to the FY2023 draft (22-12-13A), the FY2024 draft (23-12-12A) and the October 2023 amended report (23-10-24A) are read
    with the standalone extractor (0 problems, own extract files) and compared with the standalone ordinances: the two draft copies are identical to the adopted
    ordinances in every amount (80 and 93 items); the October amendment differs in exactly 11 of 80 items, all in the Bond and Interest Fund or the
    repaired totals, and it is not in ordinance_lines (it is no separate document)."""
    base = os.path.join(ROOT, "data", "belvidere_park_district")
    cmp_rows = read_csv(os.path.join(base, "ordinance_copy_comparison.csv"))
    pairs = defaultdict(list)
    for r in cmp_rows:
        pairs[(r["copy_id"], r["compared_to"])].append(r)
    assert {k: len(v) for k, v in pairs.items()} == {
        ("FY2023_appropriation_in_draft", "FY2023_appropriation"): 80, ("FY2024_appropriation_in_draft", "FY2024_appropriation"): 93,
        ("FY2023_appropriation_amended_oct", "FY2023_appropriation"): 80, ("FY2023_appropriation_amended_oct", "FY2023_appropriation_in_draft"): 80}
    for k in (("FY2023_appropriation_in_draft", "FY2023_appropriation"), ("FY2024_appropriation_in_draft", "FY2024_appropriation")):
        assert all(r["status"] == "same" for r in pairs[k]), k
    assert all(float(r["label_similarity"]) >= 0.7 for v in pairs.values() for r in v if r["label_similarity"])
    diffs = {(r["fund"], r["section"], r["line_item_copy"][:12]): Decimal(r["diff"]) for r in pairs[("FY2023_appropriation_amended_oct", "FY2023_appropriation")] if r["status"] != "same"}
    assert len(diffs) == 11 and all(r["exception_ids"] == "EX-ordinance-fy2023-amended-oct" for r in pairs[("FY2023_appropriation_amended_oct", "FY2023_appropriation")] if r["status"] != "same")
    by = {(f, s, n): d for (f, s, n), d in diffs.items()}
    assert by[("Corporate Fund", "appropriation", "Corporate Fu")] == 500 and by[("Recreation Fund", "appropriation", "Recreation F")] == 3000
    assert by[("Bond & Interest Fund", "appropriation", "Capital Proj")] == 258239 and by[("Bond & Interest Fund", "revenue", "Total Revenu")] == Decimal("-18677.13")
    assert by[("(summary)", "summary", "b")] == Decimal("-42177.13") and by[("(summary)", "summary", "c")] == Decimal("257738.91") and by[("(summary)", "summary", "d")] == Decimal("-299916.04")
    # the amendment equals the Cip fund's change in the same day's amended revenue report: capital projects expense 1,139,034.87, revenue 863,122.87 vs 881,800 (original lines)
    tot = {(r["flow"], r["column"]): Decimal(r["amount"]) for r in read_csv(os.path.join(base, "extracted", "FY2023_budget_amended_oct_totals.csv")) if r["kind"] == "fund_subtotal" and r["fund_code"] == "21"}
    assert round(tot[("expense", "amended")]) == 1139035 and tot[("revenue", "amended")] - Decimal(881800) == Decimal("-18677.13")
    # the standalone ordinance is the only FY2023 appropriation in the canonical tables
    assert {r["doc_id"] for r in canonical("ordinance_lines")} & {"FY2023_appropriation_in_draft", "FY2024_appropriation_in_draft", "FY2023_appropriation_amended_oct"} == set()
    ids = {r["exception_id"] for r in read_csv(os.path.join(ROOT, "entities", "belvidere_park_district", "exceptions.csv"))}
    assert {"EX-ordinance-fy2023-amended-oct", "EX-ordinance-fy2023-amended-oct-arithmetic", "EX-ordinance-copies-appended"} <= ids
