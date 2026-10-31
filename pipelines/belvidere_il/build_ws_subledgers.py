"""Canonical Water / Sewer cash sub-ledger tables and their ties to the rest of the data.

Usage: python3 pipelines/belvidere_il/build_ws_subledgers.py   (run build_facts.py first)

Reads data/belvidere_il/extracted/FY20XX_budget_ws_subledgers.csv (extract_budget_ws_subledgers.py), entities/belvidere_il/ws_subledger_lines.csv
(label pattern -> line_id and line_group) and the committed facts / audit_ws_vs_budget.csv; writes
  data/belvidere_il/ws_subledgers.csv      one row per sub-ledger line x fiscal year x basis (schema.OPTIONAL_TABLES): bond reserves, connection fees,
                                           depreciation funding, bond payments (beginning, sources, uses, totals, ending) and the fund summary
  data/belvidere_il/ws_subledger_ties.csv  the sub-ledgers against the department blocks and the audit (status in schema.WS_TIE_STATUSES)
Ties: the depreciation and bond set-asides in the department blocks (61:DDD-DEPSA / BONDSA pseudo accounts) against the sub-ledgers' set-aside
sources; the loans between the connection fee and depreciation ledgers; the connection fee revenue accounts against the connection fee lines; each
ledger's beginning cash against the previous column's ending cash; the capital outlay the sub-ledgers budget against the audit's original Sewer
budget. Exceptions attach by entity-scoped exceptions.csv rows whose scope_id is `ws-subledgers:<ledger>:<kind>[:<doc_id>]` (rows) or
`ws-subledgers:tie:<tie_id>[:<doc_id>]` (ties), within their fiscal-year range and bases.
"""
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

from govbudget.schema import OPTIONAL_TABLES  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402
from govbudget.validate import validate_tables  # noqa: E402
import build_facts as BF  # noqa: E402

E = BF.E
LEDGERS = ["bond_reserves", "connection_fees", "depreciation_funding", "bond_payments"]


def attachments(prefix):
    """exceptions.csv entity rows scoped `ws-subledgers:<prefix...>` -> {(parts tuple): [(fy_from, fy_to, bases, id)]}."""
    out = defaultdict(list)
    for x in E.exceptions():
        if x["scope"] == "entity" and x["scope_id"].startswith("ws-subledgers:"):
            parts = tuple(x["scope_id"].split(":")[1:])
            out[parts].append((int(x["fy_from"] or 0), int(x["fy_to"] or 9999), set(filter(None, x["bases"].split("|"))), x["exception_id"]))
    return out


def ids_for(att, parts, doc_id, fy, basis):
    got = []
    for key in (parts, parts + (doc_id,)):
        for lo, hi, bases, i in att.get(key, []):
            if lo <= fy <= hi and (not bases or basis in bases):
                got.append(i)
    return "|".join(got)


def line_map():
    return [(r["ledger"], r["kind"], re.compile(r["label_pattern"], re.I), r["nth"], r) for r in E.table("ws_subledger_lines.csv")]


def classify(lines, r):
    """(line_id, line_group) of an extract row: beginning/ending/totals/summary lines are their own kind; sources and uses by label pattern."""
    if r["kind"] not in ("source", "use"):
        return r["kind"], r["kind"] if r["ledger"] == "fund_summary" else "balance"
    nth = r["account"].rsplit("#", 1)[1]
    for led, kind, rx, n, m in lines:
        if led == r["ledger"] and kind == r["kind"] and rx.search(r["line_item"]) and (not n or n == nth):
            return m["line_id"], m["line_group"]
    sys.exit(f"{r['doc']}: no ws_subledger_lines.csv entry for {r['ledger']} {r['kind']} {r['line_item']!r} (#{nth})")


def main():
    att = attachments("")
    lines = line_map()
    page_map = {(r["doc_id"], r["page_label"]): r["pdf_page"] for r in read_csv(E.data_path("page_map.csv"))}
    lo, hi = E.config["documents"]["budget_range"]
    rows = []
    for fy in range(lo, hi + 1):
        path = E.data_path("extracted", f"FY{fy}_budget_ws_subledgers.csv")
        if not os.path.exists(path):
            continue
        for r in csv.DictReader(open(path)):
            period, basis = BF.budget_basis(fy, r["column"])
            line_id, group = classify(lines, r)
            label = r["page_label"]
            m = re.search(r"61-[45]-(\d{3})-", r["account_printed"])
            rows.append(dict(entity_id=E.id, doc_id=r["doc"], period_fy=period, basis=basis, ledger=r["ledger"], kind=r["kind"], line_key=r["account"], line_id=line_id,
                             line_group=group, line_item=r["line_item"], account_printed=r["account_printed"], dept_code=m.group(1) if m else "",
                             amount=int(r["amount"]), source_column=r["column"], page_label=label,
                             pdf_page=label[3:] if label.startswith("pdf") else page_map[(r["doc"], label)], flag=r["flag"].strip(),
                             exception_ids=ids_for(att, (r["ledger"], r["kind"]), r["doc"], period, basis)))
    ties = build_ties(rows, att)
    write_csv(E.data_path("ws_subledgers.csv"), list(OPTIONAL_TABLES["ws_subledgers"]), rows)
    write_csv(E.data_path("ws_subledger_ties.csv"), list(OPTIONAL_TABLES["ws_subledger_ties"]), ties)
    errs = validate_tables(E)
    if errs:
        sys.exit(f"{len(errs)} integrity problems:\n  " + "\n  ".join(errs[:30]))
    by = defaultdict(int)
    for t in ties:
        by[t["status"]] += 1
    print(f"{len(rows)} sub-ledger cells, {len(ties)} ties: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))


def build_ties(rows, att):
    """Cells by (doc, period, basis) and the tie rows."""
    cell = defaultdict(lambda: defaultdict(int))  # (doc, period, basis) -> {line_id or ledger|kind: amount}
    for r in rows:
        k = (r["doc_id"], int(r["period_fy"]), r["basis"])
        cell[k][r["line_id"] if r["kind"] in ("source", "use") else f"{r['ledger']}|{r['kind']}"] += r["amount"]
        if r["kind"] in ("source", "use"):
            cell[k][f"{r['ledger']}|group|{r['line_group']}|{r['kind']}"] += r["amount"]
    facts = defaultdict(lambda: defaultdict(int))
    for f in read_csv(E.data_path("facts.csv")):
        if f["source_type"] == "budget" and f["fund_code"] == "61":
            k = (f["doc_id"], int(f["period_fy"]), f["basis"])
            key = f["account_key"]
            if key.endswith("-DEPSA"):
                facts[k]["depsa"] += int(f["amount"])
            elif key.endswith("-BONDSA"):
                facts[k]["bondsa"] += int(f["amount"])
            elif re.match(r"61:8[12]0-45[12]0$", key) and f["flow"] == "revenue":
                facts[k]["cf_revenue"] += int(f["amount"])
    ties = []

    def add(k, tie_id, desc, ll, left, rl, right, explain=None):
        doc, fy, basis = k
        d = left - right
        status, why = ("exact", "") if d == 0 else ("rounding", "") if abs(d) <= 3 else ("unexplained", "")
        if status == "unexplained" and explain:
            status, why = explain(d)
        ties.append(dict(entity_id=E.id, doc_id=doc, period_fy=fy, basis=basis, tie_id=tie_id, description=desc, left_label=ll, left_amount=left,
                         right_label=rl, right_amount=right, diff=d, status=status, explanation=why,
                         exception_ids="" if status in ("exact", "rounding") else ids_for(att, ("tie", tie_id), doc, fy, basis)))

    for k in sorted(cell):
        c, fx = cell[k], facts[k]
        if not fx:
            continue
        g = lambda led, kind, grp: c.get(f"{led}|group|{grp}|{kind}", 0)  # noqa: E731
        add(k, "depreciation_set_aside", "Depreciation set-asides: the department blocks against the Depreciation Funding sources",
            "Depreciation Funding set-aside sources", g("depreciation_funding", "source", "set_aside"),
            "Water + Sewer Depreciation Set Aside lines", fx["depsa"],
            lambda d: ("explained", f"the difference equals the Connection Fee Set-Aside / Loan Repayment of the same column ({d:,})")
            if d == c.get("cf_loan_repayment", 0) and d else ("unexplained", ""))
        add(k, "bond_set_aside", "Bond Pmt Set Aside of the department blocks (plus the transfer out of Bond Reserves) against the Bond Payments set-aside source",
            "Bond Payments set-aside source", g("bond_payments", "source", "set_aside"),
            "Water + Sewer Bond Pmt Set Aside lines + Bond Reserves transfer out", fx["bondsa"] + c.get("br_transfer_out_bond_payment", 0))
        if c.get("cf_loan_to_depreciation", 0) or c.get("df_loan_from_connection_fees", 0):
            add(k, "loan_to_depreciation", "Loan from the connection fee ledger to the depreciation ledger", "Connection Fees: Loan to Depreciation Fund",
                c.get("cf_loan_to_depreciation", 0), "Depreciation Funding: Loan From Connection Fees", c.get("df_loan_from_connection_fees", 0))
        if c.get("cf_loan_repayment", 0) or c.get("df_loan_payment", 0):
            add(k, "loan_repayment", "Repayment of that loan", "Connection Fees: Set-Aside / Loan Repayment (source)", c.get("cf_loan_repayment", 0),
                "Depreciation Funding: Transfer Out - Connection Fees Loan Payment", c.get("df_loan_payment", 0))
        if fx["cf_revenue"] or c.get("cf_fees_water", 0) or c.get("cf_fees_sewer", 0):
            add(k, "connection_fee_revenue", "Connection fees and deposits on agreement: the department revenue accounts (61-4-810/820-4510/4520) against the sub-ledger lines",
                "Connection Fees sources: fees + deposits", g("connection_fees", "source", "connection_fee"), "Department revenue accounts 4510 + 4520", fx["cf_revenue"])
    # carry-forward: a column's beginning cash is the previous column's ending cash
    by_doc = defaultdict(dict)
    for r in rows:
        if r["kind"] in ("beginning", "ending", "beginning_fund", "ending_fund"):
            by_doc[(r["doc_id"], r["ledger"], r["kind"])][r["source_column"]] = (int(r["period_fy"]), r["basis"], r["amount"])
    for (doc, led, kind), colvals in sorted(by_doc.items()):
        if kind not in ("beginning", "beginning_fund"):
            continue
        end = by_doc[(doc, led, "ending" if kind == "beginning" else "ending_fund")]
        cols = list(colvals)
        for i, col in enumerate(cols):
            if col.startswith("budget_revised") or i == 0:
                continue
            prev = cols[i - 1]
            if prev.startswith("budget_revised"):
                prev = cols[i - 2]
            fy, basis, beg = colvals[col]
            pe = end[prev]
            add((doc, fy, basis), "cash_carry_forward", f"{led.replace('_', ' ')}: beginning cash of the column against the ending cash of the column before",
                f"Beginning cash {col}", beg, f"Ending cash {prev}", pe[2])
    # the capital outlay the sub-ledgers budget against the audit's original budget for the Sewer department
    gap = {}
    for r in read_csv(E.data_path("audit_ws_vs_budget.csv")):
        if r["department"] == "Sewer" and r["diff"] != "":
            gap[int(r["fiscal_year"])] = int(r["diff"])
    for k in sorted(cell):
        doc, fy, basis = k
        if basis != "adopted" or fy not in gap:
            continue
        cap = sum(v for key, v in cell[k].items() if re.match(r"\w+\|group\|capital\|use$", key))
        a = gap[fy]
        status = None
        if a == cap:
            status, why = "exact", ""
        elif a == 0:
            status, why = "audit_excludes", "the audit's original Sewer budget is the operating lines only: the capital outlay budgeted in the sub-ledgers is not in it"
        else:
            status, why = "unexplained", ""
        ties.append(dict(entity_id=E.id, doc_id=doc, period_fy=fy, basis=basis, tie_id="audit_capital_outlay_sewer",
                         description="Sewer: audit original budget minus the budget's expense lines (audit_ws_vs_budget.csv) against the capital outlay the sub-ledgers budget",
                         left_label="Audit original - budget lines, Sewer", left_amount=a,
                         right_label="Capital uses in Connection Fees + Depreciation Funding", right_amount=cap, diff=a - cap, status=status, explanation=why,
                         exception_ids=ids_for(att, ("tie", "audit_capital_outlay_sewer"), doc, fy, basis) if status == "unexplained" else ""))
    return ties


if __name__ == "__main__":
    main()
