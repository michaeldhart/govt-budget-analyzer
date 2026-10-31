"""Water / Sewer budget cash sub-ledgers (data/belvidere_il/ws_subledgers.csv, ws_subledger_ties.csv): identities re-computed from the
committed table, independent of the extractor, and the ties pinned. A deliberate change means updating the numbers here and in CLAUDE.md."""
from collections import Counter, defaultdict

LEDGERS = ["bond_reserves", "connection_fees", "depreciation_funding", "bond_payments"]
# the city's own arithmetic, (doc, ledger, check) -> column differences allowed (overrides FY2019 / FY2027 *_budget_ws_subledgers.csv)
FUND_ENDING_ALLOWED = {("FY2019_budget", 2017, "actual_prelim"), ("FY2027_budget", 2025, "actual_prelim"), ("FY2027_budget", 2026, "prior_doc_budget"),
                       ("FY2027_budget", 2027, "adopted")}


def cells(table):
    out = defaultdict(lambda: defaultdict(list))  # (doc, period, basis, ledger) -> kind -> [amounts]
    for r in table("ws_subledgers"):
        out[(r["doc_id"], int(r["period_fy"]), r["basis"], r["ledger"])][r["kind"]].append(int(r["amount"]))
    return out


def test_shape(table):
    rows = table("ws_subledgers")
    assert len(rows) == 2513
    assert {r["doc_id"] for r in rows} == {f"FY{y}_budget" for y in range(2015, 2028)}
    assert {r["ledger"] for r in rows} == set(LEDGERS) | {"fund_summary"}
    assert not [r for r in rows if r["kind"] in ("source", "use") and not r["line_id"]]
    assert all(r["pdf_page"] for r in rows)


def test_each_ledger_identity(table):
    """sources = TOTAL Sources, uses = TOTAL Uses, ending = beginning + sources - uses, in every document column (the city's totals are up to $3 off)."""
    n = 0
    for (doc, fy, basis, led), c in cells(table).items():
        if led not in LEDGERS:
            continue
        (beg,), (end,) = c["beginning"], c["ending"]
        if led != "bond_reserves":
            assert abs(sum(c["source"]) - c["total_sources"][0]) <= 3, (doc, fy, basis, led)
            assert abs(sum(c["use"]) - c["total_uses"][0]) <= 3, (doc, fy, basis, led)
            moved = c["total_sources"][0] - c["total_uses"][0]
        else:
            moved = sum(c["source"]) - sum(c["use"])
        assert abs(beg + moved - end) <= 3, (doc, fy, basis, led, beg, moved, end)
        n += 1
    assert n == 188  # four ledgers in every (document, period, basis) column


def test_fund_summary(table):
    """Net cash flow = gross revenues - gross expenditures; ending = beginning + net + change in receivables, except the three documented columns."""
    bad = set()
    for (doc, fy, basis, led), c in cells(table).items():
        if led != "fund_summary":
            continue
        (rev,), (exp,), (net,) = c["gross_revenues"], c["gross_expenditures"], c["net_cash_flow"]
        assert abs(rev - exp - net) <= 3, (doc, fy, basis)
        chg = sum(c["change_receivable"])
        if abs(c["beginning_fund"][0] + net + chg - c["ending_fund"][0]) > 3:
            bad.add((doc, fy, basis))
    assert bad == FUND_ENDING_ALLOWED


def test_ties(table):
    ties = table("ws_subledger_ties")
    assert len(ties) == 301
    assert Counter(t["status"] for t in ties) == {"exact": 243, "rounding": 18, "explained": 5, "audit_excludes": 10, "unexplained": 25}
    by = Counter((t["tie_id"], t["status"]) for t in ties)
    # the connection fee revenue accounts in the department blocks are the sub-ledger's connection fee and deposit lines, to the dollar
    assert by[("connection_fee_revenue", "exact")] == 47 and sum(v for (i, s), v in by.items() if i == "connection_fee_revenue") == 47
    # every unexplained tie carries an exception (the allowed overrides and the structural notes are in exceptions.csv)
    assert not [t for t in ties if t["status"] == "unexplained" and not t["exception_ids"]]


def test_fy2020_sewer_capital_is_in_the_depreciation_sub_ledger(table):
    """The FY2020 audit's $5,204,000 Sewer capital outlay (EX-ws-audit-fy2020-sewer-capital) is the sub-ledger's capital uses: 4,699,000 + 505,000."""
    (t,) = [t for t in table("ws_subledger_ties") if t["tie_id"] == "audit_capital_outlay_sewer" and t["period_fy"] == "2020"]
    assert (t["left_amount"], t["right_amount"], t["status"]) == ("5204000", "5204000", "exact")
    uses = {r["line_id"]: int(r["amount"]) for r in table("ws_subledgers")
            if r["doc_id"] == "FY2020_budget" and r["basis"] == "adopted" and r["line_group"] == "capital" and int(r["amount"])}
    assert uses == {"df_cip_sewer": 4699000, "df_equipment": 505000}
    (t15,) = [t for t in table("ws_subledger_ties") if t["tie_id"] == "audit_capital_outlay_sewer" and t["period_fy"] == "2015"]
    assert t15["status"] == "exact" and t15["left_amount"] == "5286000"  # FY2015: Connection Fees Building 3.8M + Depreciation Funding 1.436M + 0.05M


def test_set_asides_of_the_department_blocks_reach_the_sub_ledgers(table):
    ties = {(t["doc_id"], t["period_fy"], t["basis"], t["tie_id"]): t for t in table("ws_subledger_ties")}
    for doc in ("FY2022_budget", "FY2024_budget", "FY2026_budget"):
        for tid in ("depreciation_set_aside", "bond_set_aside"):
            assert ties[(doc, doc[2:6], "adopted", tid)]["status"] == "exact"
