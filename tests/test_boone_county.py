"""Boone County: the committed budget-worksheet extracts tie to the totals the county prints (no PDFs needed)."""
import csv
from pathlib import Path

EX = Path(__file__).resolve().parents[1] / "data/boone_county_il/extracted"
DOCS = {"FY2025_budget": 821, "FY2026_budget": 828, "FY2027_budget_prelim": 811}


def rows(name):
    return list(csv.DictReader(open(EX / name, newline="")))


def test_worksheet_extracts_have_no_open_problems():
    for doc, n_lines in DOCS.items():
        assert rows(f"{doc}_checks.csv") == [], doc
        assert len({r["row_seq"] for r in rows(f"{doc}_lines.csv")}) == n_lines, doc


def test_every_printed_total_ties_or_is_an_accepted_difference():
    for doc in DOCS:
        for t in rows(f"{doc}_totals.csv"):
            if abs(float(t["diff"])) > max(1.0, 0.5 * int(t["n_lines"])):
                assert t["flag"] == "allow_diff", (doc, t)


def test_adjacent_documents_agree_on_the_shared_budget_column():
    def load(doc):
        return {(r["account_printed"], r["period_fy"], r["measure"]): float(r["amount"]) for r in rows(f"{doc}_lines.csv")}
    a, b = load("FY2025_budget"), load("FY2026_budget")
    shared = [(k, v) for k, v in a.items() if k[1:] == ("2025", "final") and (k[0], "2025", "budget") in b]
    bad = [k for k, v in shared if abs(v - b[(k[0], "2025", "budget")]) > 1]
    assert len(shared) == 793 and len(bad) == 16  # restated prior-year budgets, to be listed as exceptions


AUD = EX / "audits"


def audit_rows(fy):
    return list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_statements.csv", newline="")))


def test_audit_fund_statements_pass_every_check_in_all_eleven_audits():
    for fy in range(2015, 2026):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_checks.csv", newline="")))
        assert len(checks) >= 18 and all(c["ok"] == "True" for c in checks), fy


def test_general_fund_balance_chain_between_audits():
    def lines(fy, col):
        return [(r["label"], int(r["amount"])) for r in audit_rows(fy) if r["statement"] == "changes" and r["col"] == col]

    def beginning(fy, col):
        return next(v for l, v in lines(fy, col) if "beginning" in l.lower() or "DECEMBER 1" in l.upper())
    for fy in range(2015, 2025):
        want = lines(fy, "General")[-1][1]
        got = beginning(fy + 1, "General")
        # FY2021's audit restates the General Fund opening balance by 100,024 (Note 15: prior year restatement)
        assert got == want or (fy, got - want) == (2021, 100024), (fy, want, got)
    assert lines(2025, "General")[-1][1] == 13812064
    assert lines(2025, "Total Governmental")[-1][1] == 43666257


def gf_rows(fy):
    return list(csv.DictReader(open(AUD / f"FY{fy}_audit_gf_schedule.csv", newline="")))


def test_general_fund_schedules_pass_every_check_in_all_eleven_audits():
    for fy in range(2015, 2026):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_gf_schedule_checks.csv", newline="")))
        assert len(checks) >= 35 and all(c["ok"] == "True" for c in checks), fy


def test_general_fund_schedule_totals():
    def total(fy, name):
        return next(r for r in gf_rows(fy) if r["line_type"] == "total" and r["path"] == "" and r["label"].lower() == name)
    assert (int(total(2025, "total revenues")["budget"]), int(total(2025, "total revenues")["actual"])) == (18898960, 20439677)
    assert int(total(2025, "total expenditures")["actual"]) == 21030404
    # FY2015-FY2019 the schedules equal the governmental funds statement's General column to the dollar
    for fy in range(2015, 2020):
        st = {r["label"].lower(): int(r["amount"]) for r in audit_rows(fy) if r["statement"] == "changes" and r["col"] == "General"}
        assert int(total(fy, "total revenues")["actual"]) == st["total revenues"], fy
        assert int(total(fy, "total expenditures")["actual"]) == st["total expenditures"], fy


def fund_schedule_rows(fy):
    return list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_schedules.csv", newline="")))


def test_per_fund_schedules_pass_every_check_in_all_eleven_audits():
    counts = {}
    for fy in range(2015, 2026):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_fund_schedules_checks.csv", newline="")))
        assert len(checks) >= 260 and all(c["ok"] == "True" for c in checks), fy
        counts[fy] = len({(r["pdf_page"], r["fund_name"]) for r in fund_schedule_rows(fy)})
    assert counts[2025] == 39 and counts[2022] == 76 and counts[2020] == 50


def test_per_fund_ending_balances_tie_to_the_fund_statements():
    def statement_end(fy, col):
        last = None
        for r in audit_rows(fy):
            if r["statement"] == "changes" and r["col"] == col:
                last = int(r["amount"])
        return last
    for fy in (2021, 2022):  # every non-General governmental fund has a schedule these years
        ends = sum(int(float(r["actual"])) for r in fund_schedule_rows(fy) if r["line_type"] == "ending" and r["actual"] != "")
        assert ends == statement_end(fy, "Total Governmental") - statement_end(fy, "General"), fy
    mft = [r for r in fund_schedule_rows(2025) if r["line_type"] == "ending" and "MOTOR FUEL" in r["fund_name"]]
    assert int(float(mft[0]["actual"])) == statement_end(2025, "County Motor Fuel Tax") == 5202333


DATA = EX.parent


def _facts():
    return list(csv.DictReader(open(DATA / "facts.csv", newline="")))


def test_canonical_tables_validate():
    from govbudget import Entity
    from govbudget.validate import validate_config, validate_tables
    e = Entity.load("boone_county_il")
    assert validate_config(e) == []
    assert validate_tables(e) == []


def test_fact_counts_and_headline_numbers():
    facts = _facts()
    assert len(facts) == 41634 and sum(f["source_type"] == "budget" for f in facts) == 25885
    gf = lambda **k: sum(int(f["amount"]) for f in facts if all(f[a] == str(b) for a, b in k.items()))  # noqa: E731
    assert gf(doc_id="FY2026_budget", fund_code="001", flow="revenue", period_fy=2026, basis="adopted") == 25188465
    # the budget extracts reconcile with the totals the county prints (993 fund/department-flow-period groups; 4 are the recorded source slips)
    assert gf(doc_id="FY2025_budget", fund_code="163", flow="revenue", period_fy=2025, basis="adopted") == 5000


def test_budget_facts_reconcile_with_control_totals():
    import collections
    fs, n, ts = collections.defaultdict(int), collections.defaultdict(int), collections.defaultdict(int)
    for r in _facts():
        if r["source_type"] == "budget" and r["doc_id"] in ("FY2025_budget", "FY2026_budget", "FY2027_budget_prelim"):
            k = (r["doc_id"], r["fund_code"], r["flow"], r["period_fy"], r["basis"])
            fs[k] += int(r["amount"]); n[k] += 1
    for r in csv.DictReader(open(DATA / "control_totals.csv", newline="")):
        ts[(r["doc_id"], r["name"].split("-")[0], "revenue" if r["kind"].endswith("revenues") else "expense", r["period_fy"], r["basis"])] += int(r["amount"])
    bad = sorted(k for k, v in ts.items() if abs(fs.get(k, 0) - v) > max(1, 0.5 * n.get(k, 0)))
    assert len(ts) == 993 and len(bad) == 4, bad


def test_audit_vs_budget_comparison():
    rows = list(csv.DictReader(open(DATA / "audit_vs_budget.csv", newline="")))
    by_year = {}
    for fy in range(2016, 2026):
        b = [r for r in rows if r["period_fy"] == str(fy) and r["budget_status"]]
        by_year[fy] = (len(b), sum(r["budget_status"] == "agree" for r in b))
    assert by_year == {2016: (80, 64), 2017: (91, 78), 2018: (93, 76), 2019: (102, 95), 2020: (105, 99), 2021: (97, 92), 2022: (93, 87),
                       2023: (94, 89), 2024: (89, 82), 2025: (89, 85)}
    # the budget books and worksheets agree with the audits' original budgets to the dollar in 847 of 933 fund-year-measure comparisons
    assert sum(r["budget_status"] == "agree" for r in rows) == 847 and sum(r["budget_status"] == "differs" for r in rows) == 86
    diff25 = sorted((r["fund_code"], r["measure"], int(r["budget_diff"])) for r in rows if r["period_fy"] == "2025" and r["budget_status"] == "differs")
    assert diff25 == [("001", "expenditure", -2400), ("001", "revenue", -2400), ("045", "revenue", 2000), ("120", "revenue", 200)]
    # the county's employee health insurance (a transfer out in the books, 2.3M-2.9M a year) is most of the General Fund expenditure gap in the years
    # whose audit budget includes it as an expenditure (FY2016-FY2019, FY2023); FY2020-FY2022 and FY2024 audits treat it as other financing
    gf = {r["period_fy"]: int(r["budget_diff"]) for r in rows if r["fund_code"] == "001" and r["measure"] == "expenditure" and r["budget_status"]}
    assert gf["2016"] == 2289500 and gf["2017"] == 2755000 and gf["2019"] == 2836000 and gf["2023"] == 2483999 and gf["2024"] == 0 and gf["2020"] == -171999
    funcs = {(r["period_fy"], r["function"]): int(r["budget_diff"]) for r in csv.DictReader(open(DATA / "audit_vs_budget_gf_functions.csv", newline=""))}
    assert funcs[("2024", "Judiciary and courts")] == 0 and funcs[("2024", "Public safety")] == 120
    assert funcs[("2025", "General control and administration")] + funcs[("2025", "Judiciary and courts")] + funcs[("2025", "Public safety")] == 2567600


def test_budget_books_tie_to_their_printed_totals():
    expected = {"FY2017_budget": 876, "FY2019_budget": 880, "FY2020_budget": 813, "FY2021_budget": 837, "FY2022_budget": 878, "FY2023_budget": 862, "FY2024_budget": 875}
    for doc, n_lines in expected.items():
        checks = list(csv.DictReader(open(EX / f"{doc}_book_checks.csv", newline="")))
        assert all(c["ok"] == "True" for c in checks), (doc, [c for c in checks if c["ok"] != "True"][:3])
        assert len({r["row_seq"] for r in csv.DictReader(open(EX / f"{doc}_book_lines.csv", newline=""))}) == n_lines, doc
        totals = list(csv.DictReader(open(EX / f"{doc}_book_totals.csv", newline="")))
        assert len(totals) > 400
    # the FY2024 book's budget column equals the FY2025 worksheet's FY2024 column for most accounts, with the ERP renumbering the rest
    book = {}
    for r in csv.DictReader(open(EX / "FY2024_budget_book_lines.csv", newline="")):
        if r["period_fy"] == "2024" and r["measure"] == "budget" and r["code"]:
            book[(r["fund_code"], r["code"].replace(".", ""))] = book.get((r["fund_code"], r["code"].replace(".", "")), 0) + int(r["amount"])
    assert book[("001", "301000")] == 7400000


def test_scanned_fy2023_book_agrees_with_its_neighbours():
    """The FY2023 book is a scan read by OCR; its budget columns equal the FY2022 and FY2024 books' for 99% of the lines both print."""
    import re

    def load(doc, fy):
        d = {}
        for r in csv.DictReader(open(EX / f"{doc}_book_lines.csv", newline="")):
            if "Summary" in r["table"] or not r["fund_code"] or r["period_fy"] != str(fy) or r["measure"] != "budget":
                continue
            k = (r["fund_code"], r["dept_code"] if r["code"][:1] != "3" else "", r["code"] or "U", re.sub(r"[^a-z]", "", r["label"].lower())[:8])
            d[k] = d.get(k, 0) + int(r["amount"])
        return d
    for other, fy in (("FY2024_budget", 2023), ("FY2022_budget", 2022)):
        a, b = load("FY2023_budget", fy), load(other, fy)
        shared = set(a) & set(b)
        assert len(shared) > 700 and sum(a[k] != b[k] for k in shared) <= 8, other


def test_audit_tax_and_debt_tables_pass_every_check():
    for fy in range(2015, 2026):
        for kind in ("tax_levies", "debt"):
            checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_{kind}_checks.csv", newline="")))
            assert checks and all(c["ok"] == "True" for c in checks), (fy, kind)


def test_property_tax_levies_agree_across_audits():
    import collections
    seen = collections.defaultdict(set)
    for r in csv.DictReader(open(DATA / "property_tax_levies.csv", newline="")):
        if r["section"] in ("assessed_valuation", "extension", "extension_total"):
            seen[(r["tax_year"], r["section"], r["line_item"].lower())].add(r["amount"])
    assert len(seen) > 150 and all(len(v) == 1 for v in seen.values())  # the same levy year, valuation and extension in every audit that prints it
    totals = {r["tax_year"]: int(r["amount"]) for r in csv.DictReader(open(DATA / "property_tax_levies.csv", newline=""))
              if r["doc_id"] == "FY2025_audit" and r["section"] == "extension_total"}
    assert totals["2024"] == 13429472 and totals["2021"] == 12311714


def test_liabilities_chain_between_audits():
    import collections
    d = collections.defaultdict(dict)
    for r in csv.DictReader(open(DATA / "debt_balances.csv", newline="")):
        d[(r["line_id"], int(r["period_fy"]))][r["measure"]] = int(r["amount"])
    ok = bad = 0
    for (lid, fy), v in d.items():
        n = d.get((lid, fy + 1))
        if n and "ending" in v and "beginning" in n:
            ok += v["ending"] == n["beginning"]
            bad += v["ending"] != n["beginning"]
    assert (ok, bad) == (62, 14)
    ends = {(r["line_id"], r["period_fy"]): int(r["amount"]) for r in csv.DictReader(open(DATA / "debt_balances.csv", newline="")) if r["measure"] == "ending"}
    assert ends[("bonds_payable", "2025")] == 1165000 and ends[("total", "2025")] == 10203886


def test_pension_schedules_agree_across_audits_and_tie_to_the_debt_note():
    import collections
    seen = collections.defaultdict(dict)
    for fy in (2016, 2017, 2024, 2025):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_pension_checks.csv", newline="")))
        assert len(checks) > 40 and all(c["ok"] == "True" for c in checks), fy
        for r in csv.DictReader(open(AUD / f"FY{fy}_audit_pension_rsi.csv", newline="")):
            if r["line_id"]:
                seen[(r["plan_id"], r["schedule"], r["line_id"], r["column_year"])][fy] = float(r["value"])
    differ = [k for k, v in seen.items() if len(set(v.values())) > 1]
    assert len(seen) == 943 and len(differ) == 62 and all(int(k[3]) <= 2015 for k in differ)  # IMRF restated its 2014 and 2015 columns (EX-pension-restated-2014-2015)
    roll = list(csv.DictReader(open(DATA / "pension_rollforward.csv", newline="")))
    npl = {(r["doc_id"], r["plan_id"], r["column_year"]): int(r["amount"]) for r in roll if r["line_id"] == "npl"}
    debt = {(r["period_fy"], r["line_id"]): int(r["amount"]) for r in csv.DictReader(open(DATA / "debt_balances.csv", newline="")) if r["measure"] == "ending"}
    assert npl[("FY2025_audit", "slep", "2024")] == debt[("2025", "npl_slep")] == 206719
    assert npl[("FY2025_audit", "imrf_eco", "2024")] == debt[("2025", "npl_eco")] == 1247169
    opeb = {(r["doc_id"], r["column_year"]): int(r["amount"]) for r in csv.DictReader(open(DATA / "opeb_rollforward.csv", newline="")) if r["line_id"] == "liability_ending"}
    assert opeb[("FY2025_audit", "2025")] == debt[("2025", "opeb")] == 5436603


def test_combining_statements_of_the_nonmajor_funds():
    import re
    for fy in (2016, 2024, 2025):
        checks = list(csv.DictReader(open(AUD / f"FY{fy}_audit_combining_checks.csv", newline="")))
        assert checks and all(c["ok"] == "True" for c in checks), fy
    rows = list(csv.DictReader(open(DATA / "fund_statements.csv", newline="")))
    bal = {}
    for r in rows:
        if r["doc_id"] == "FY2025_audit" and r["statement"] == "combining_balance_sheet" and r["line_id"] == "total_fund_balance":
            bal[r["fund"]] = int(r["amount"])
    total = bal.pop("Total Nonmajor Governmental Funds")
    assert len(bal) == 60 and sum(bal.values()) == total == 20302376
    # every fund that has a per-fund schedule shows the same ending balance there; 23 funds (5.6M) have none
    sched = {}
    for r in csv.DictReader(open(AUD / "FY2025_audit_fund_schedules.csv", newline="")):
        if r["line_type"] == "ending" and r["actual"] != "":
            sched[re.sub(r"[^a-z]", "", r["fund_name"].lower().replace("fund", ""))] = int(float(r["actual"]))
    key = lambda s: re.sub(r"[^a-z]", "", s.lower().replace("fund", ""))  # noqa: E731
    both = {k: v for k, v in bal.items() if key(k) in sched}
    assert len(both) == 37 and all(v == sched[key(k)] for k, v in both.items())
    assert sum(v for k, v in bal.items() if key(k) not in sched) == 5620309
    # each fund's FY2025 opening balance is its FY2024 closing balance (61 of 61 funds in both audits)
    end24 = {r["fund"]: int(r["amount"]) for r in rows if r["doc_id"] == "FY2024_audit" and r["statement"] == "combining_changes_in_fund_balance" and r["line_id"] == "fund_balance_ending"}
    beg25 = {r["fund"]: int(r["amount"]) for r in rows if r["doc_id"] == "FY2025_audit" and r["statement"] == "combining_changes_in_fund_balance" and r["line_id"] == "fund_balance_beginning"}
    common = set(end24) & set(beg25)
    assert len(common) == 61 and all(end24[f] == beg25[f] for f in common)  # 60 funds and the Total column


def test_reference_series():
    rows = {int(r["fiscal_year"]): r for r in csv.DictReader(open(DATA / "reference_by_fy.csv", newline=""))}
    assert sorted(rows) == list(range(2012, 2028))
    assert rows[2025]["cpi_factor"] == "1.0" and rows[2025]["cpi_base_fy"] == "2025" and rows[2025]["cpi_flag"] == "missing_month:2025-10"
    assert rows[2025]["population"] == "53568" and rows[2012]["population"] == "53735" and rows[2026]["population_flag"] == "carried_forward"
    # the assessed valuation of tax year N-1 is the one the FY2025 budget quotes for taxes paid FY2025
    assert rows[2025]["eav_city"] == "1547516447" and rows[2025]["eav_levy_year"] == "2024"
    assert rows[2026]["eav_flag"] == "EX-reference-eav-estimate" and rows[2026]["eav_city"] == "1722225472"
    assert all(rows[y]["eav_city"] for y in range(2012, 2027))
    assert float(rows[2012]["cpi_factor"]) > float(rows[2020]["cpi_factor"]) > 1.0


def test_general_fund_revenue_by_source():
    rows = list(csv.DictReader(open(DATA / "audit_revenue_by_source.csv", newline="")))
    b = [r for r in rows if r["budget_status"]]
    assert len(b) == 70 and sum(r["budget_status"] == "agree" for r in b) == 53
    assert {r["period_fy"] for r in b} == {str(y) for y in range(2019, 2026)}  # FY2015-FY2018 have no line-level budget
    total = lambda fy, col: sum(int(r[col]) for r in rows if r["period_fy"] == str(fy) and r[col] != "")  # noqa: E731
    assert [total(fy, "audit_budget") - total(fy, "budget") for fy in range(2019, 2026)] == [19580, 106311, 75000, -86000, -86000, 0, -2400]
    fy24 = [r for r in b if r["period_fy"] == "2024"]
    assert len(fy24) == 10 and all(r["budget_status"] == "agree" for r in fy24)
    from govbudget import Entity
    from govbudget.validate import validate_config
    assert validate_config(Entity.load("boone_county_il")) == []
