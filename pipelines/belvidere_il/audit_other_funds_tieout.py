"""Independent tie-outs of the audit fund-level extracts (extract_audit_other_funds.py) to data/belvidere_il/fund_statements.csv.

Usage: python3 pipelines/belvidere_il/audit_other_funds_tieout.py

Checks (every one is independent of the identities used while extracting):
  T1  combining statements, Total column  == fund_statements.csv "Nonmajor governmental funds" column, line by line (FY2012-FY2026).
  T2  each fund's budget-and-actual schedule == the same fund's column in the combining statements (revenues, expenditures,
      net change, beginning and ending fund balance), where the fund is a nonmajor fund that year.
  T3  Capital Projects and Motor Fuel Tax schedules == their own columns in fund_statements.csv in the years those are major funds.
  T4  chain: ending fund balance of year Y-1 == beginning (restated, before the prior-period adjustment) of year Y, per fund.
Writes data/belvidere_il/extracted/audits/audit_other_funds_tieout.csv (check, fiscal_year, fund, item, value_a, value_b, diff, status).
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
DATA = os.path.join(ROOT, "data", "belvidere_il")
AUD = os.path.join(DATA, "extracted", "audits")

ALIAS = {"Farmington SSA": "Farmington SSA #2"}


def load_patterns():
    pats = []
    for r in csv.DictReader(open(os.path.join(ROOT, "entities", "belvidere_il", "fund_statement_lines.csv"))):
        pats.append((r["statement"], re.compile(r["label_pattern"], re.I), r["line_id"]))
    return pats


def line_id(pats, statement, label):
    lab = label.strip()
    for st, rx, lid in pats:
        if st == statement and rx.search(lab):
            return lid
    return None


def load_fund_statements():
    d = defaultdict(float)
    for r in csv.DictReader(open(os.path.join(DATA, "fund_statements.csv"))):
        if r["statement"] in ("balance_sheet", "changes_in_fund_balance") and r["line_id"]:
            d[(int(r["period_fy"]), r["statement"], r["fund"], r["line_id"])] += float(r["amount"])
    return d


def fnum(x):
    return None if x in ("", None) else int(float(x))


def main():
    pats = load_patterns()
    fsd = load_fund_statements()
    out = []

    def rec(check, fy, fund, item, a, b, note=""):
        out.append(dict(check=check, fiscal_year=fy, fund=fund, item=item, value_a=a, value_b=b, diff=(None if a is None or b is None else a - b),
                        status="ok" if a == b else ("skip" if a is None or b is None else "DIFF"), note=note))

    comb, sched = {}, {}
    for fy in range(2012, 2027):
        p = os.path.join(AUD, f"FY{fy}_audit_fund_combining.csv")
        if os.path.exists(p):
            comb[fy] = list(csv.DictReader(open(p)))
        p = os.path.join(AUD, f"FY{fy}_audit_fund_schedules.csv")
        if os.path.exists(p):
            sched[fy] = list(csv.DictReader(open(p)))

    # ---- T1: combining total column vs Nonmajor column ------------------------------------------------
    cdata = {}  # (fy, statement, fund, line_id) -> sum
    for fy, rows in comb.items():
        stmt_of = {"combining_balance_sheet": "balance_sheet", "combining_changes_in_fund_balance": "changes_in_fund_balance"}
        per = defaultdict(float)
        for r in rows:
            st = stmt_of[r["statement"]]
            lid = line_id(pats, st, r["line_item"])
            if lid:
                fund = ALIAS.get(r["fund"], r["fund"])
                per[(st, fund, lid)] += float(r["amount"])
        for (st, fund, lid), v in per.items():
            cdata[(fy, st, fund, lid)] = v
        lids = sorted({(st, lid) for (st, f, lid) in per})
        for st, lid in lids:
            a = per.get((st, "Total", lid))
            b = fsd.get((fy, st, "Nonmajor governmental funds", lid))
            if a is None:
                continue
            rec("T1 combining Total vs fund_statements Nonmajor", fy, "Total nonmajor", f"{st}:{lid}", int(a), None if b is None else int(b))

    # ---- T2: schedules vs combining columns -----------------------------------------------------------
    kinds = {"total_revenues": "total_revenues", "total_expenditures": "total_expenditures", "net_change": "net_change_in_fund_balance",
             "fb_begin": "fund_balance_beginning", "fb_restated": "fund_balance_restated", "fb_end": "fund_balance_ending"}
    sdata = {}  # (fy, fund, kind) -> actual
    for fy, rows in sched.items():
        for r in rows:
            if r["line_kind"] in kinds:
                fund = ALIAS.get(r["fund"], r["fund"])
                sdata[(fy, fund, r["line_kind"])] = fnum(r["actual"])
    for (fy, fund, kind), v in sorted(sdata.items()):
        st = "balance_sheet" if False else "changes_in_fund_balance"
        lid = kinds[kind]
        cv = cdata.get((fy, st, fund, lid))
        if cv is not None:
            rec("T2 schedule vs combining column", fy, fund, kind, v, int(cv))
        # T3: fund_statements column (major-fund years)
        if fund in ("Capital Projects", "Motor Fuel Tax"):
            fv = fsd.get((fy, st, fund, lid))
            if fv is not None:
                rec("T3 schedule vs fund_statements major-fund column", fy, fund, kind, v, int(fv))
    # ---- T4: chain of fund balances --------------------------------------------------------------------
    funds = sorted({f for (fy, f, k) in sdata})
    for fund in funds:
        for fy in range(2013, 2027):
            e = sdata.get((fy - 1, fund, "fb_end"))
            b = sdata.get((fy, fund, "fb_begin"))
            if e is None and b is None:
                continue
            rec("T4 chain: ending(Y-1) vs beginning(Y), schedules", fy, fund, "fund balance", b, e)
    cfunds = sorted({f for (fy, st, f, lid) in cdata if f != "Total"})
    for fund in cfunds:
        for fy in range(2013, 2027):
            e = cdata.get((fy - 1, "changes_in_fund_balance", fund, "fund_balance_ending"))
            b = cdata.get((fy, "changes_in_fund_balance", fund, "fund_balance_beginning"))
            if e is None or b is None:
                continue
            rec("T4 chain: ending(Y-1) vs beginning(Y), combining", fy, fund, "fund balance", b, e)

    path = os.path.join(AUD, "audit_other_funds_tieout.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["check", "fiscal_year", "fund", "item", "value_a", "value_b", "diff", "status", "note"])
        w.writeheader()
        w.writerows(out)
    tot = defaultdict(lambda: defaultdict(int))
    for r in out:
        tot[r["check"]][r["status"]] += 1
    for c, d in tot.items():
        print(c, dict(d))
    bad = [r for r in out if r["status"] == "DIFF"]
    for r in bad:
        print("  DIFF", r["check"][:2], r["fiscal_year"], r["fund"], r["item"], r["value_a"], r["value_b"], r["diff"])
    print(f"-> {os.path.relpath(path, ROOT)}: {len(out)} comparisons, {len(bad)} differences")


if __name__ == "__main__":
    main()
