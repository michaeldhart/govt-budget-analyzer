"""One row per fiscal year x fund from the audited fund statements, with cross-checks to the audit's other schedules.

Usage: python3 pipelines/belvidere_il/build_fund_summary.py   (run build_facts.py first)

Inputs : data/belvidere_il/fund_statements.csv (audited balance sheet and statement of changes in fund balances),
         data/belvidere_il/facts.csv (the audit's General Fund revenue and expenditure schedules)
Output : data/belvidere_il/fund_balance_summary.csv   funds: General, Total governmental funds

Checks (columns `*_vs_schedule`, `rollforward_diff`; all should be 0, exceptions are in exceptions.csv):
  * General Fund revenues / expenditures in the statement equal the totals of the audit's detailed General Fund schedules;
  * ending fund balance of year Y-1 equals the beginning (restated, if the audit restated it) balance of year Y.
`unassigned_months` = unassigned General Fund balance in months of that year's General Fund expenditures.
"""
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import read_csv, write_csv  # noqa: E402

E = Entity.load("belvidere_il")
COLUMNS = ["entity_id", "fiscal_year", "fund", "cash", "total_assets", "total_liabilities", "total_deferred_inflows", "nonspendable",
           "restricted", "committed", "assigned", "unassigned", "total_fund_balance", "revenues", "expenditures",
           "excess_revenues_over_expenditures", "other_financing", "net_change", "beginning_balance", "prior_period_adjustment",
           "ending_balance", "revenues_vs_schedule", "expenditures_vs_schedule", "rollforward_diff", "unassigned_months"]
FUNDS = ("General", "Total governmental funds")
CATEGORIES = ("Nonspendable", "Restricted", "Committed", "Assigned", "Unassigned")


def main():
    st = defaultdict(lambda: defaultdict(int))   # (fy, fund) -> line_id -> amount
    cats = defaultdict(lambda: defaultdict(int))  # (fy, fund) -> fund balance category -> amount
    for r in read_csv(E.data_path("fund_statements.csv")):
        if r["fund"] not in FUNDS:
            continue
        k = (int(r["period_fy"]), r["fund"])
        if r["line_id"]:
            st[k][r["line_id"]] += int(r["amount"])
        if r["statement"] == "balance_sheet" and r["section"].lower().startswith("fund balance"):
            # a line named after a category ("Unassigned", "Nonspendable") is that category; otherwise the heading it sits under
            cat = next((c for c in CATEGORIES if r["line_item"].title().startswith(c)), "") or \
                next((c for c in CATEGORIES if r["subsection"].title().startswith(c)), "")
            if cat and not r["line_id"].startswith("total"):
                cats[k][cat] += int(r["amount"])
    sched_rev, sched_exp = defaultdict(int), defaultdict(int)
    for f in read_csv(E.data_path("facts.csv")):
        if f["source_type"] == "audit" and f["basis"] == "actual_audited" and f["source_column"] == "actual":
            (sched_rev if f["flow"] == "revenue" else sched_exp)[int(f["period_fy"])] += int(f["amount"])
    out = []
    for (fy, fund), v in sorted(st.items()):
        c = cats[(fy, fund)]
        beg = v.get("fund_balance_restated", v.get("fund_balance_beginning", 0))
        prev_end = st.get((fy - 1, fund), {}).get("fund_balance_ending")
        other = v.get("total_other_financing", v.get("transfers_net", v.get("transfers_in", 0) + v.get("transfers_out", 0)))
        row = dict(entity_id=E.id, fiscal_year=fy, fund=fund, cash=v.get("cash", ""), total_assets=v.get("total_assets", ""),
                   total_liabilities=v.get("total_liabilities", ""), total_deferred_inflows=v.get("total_deferred_inflows", ""),
                   nonspendable=c.get("Nonspendable", ""), restricted=c.get("Restricted", ""), committed=c.get("Committed", ""),
                   assigned=c.get("Assigned", ""), unassigned=c.get("Unassigned", ""), total_fund_balance=v.get("total_fund_balance", ""),
                   revenues=v.get("total_revenues", ""), expenditures=v.get("total_expenditures", ""),
                   excess_revenues_over_expenditures=v.get("excess_revenues_over_expenditures", ""), other_financing=other,
                   net_change=v.get("net_change_in_fund_balance", ""), beginning_balance=beg,
                   prior_period_adjustment=v.get("prior_period_adjustment", ""), ending_balance=v.get("fund_balance_ending", ""),
                   revenues_vs_schedule=(v["total_revenues"] - sched_rev[fy]) if fund == "General" and "total_revenues" in v else "",
                   expenditures_vs_schedule=(v["total_expenditures"] - sched_exp[fy]) if fund == "General" and "total_expenditures" in v else "",
                   rollforward_diff=(beg - prev_end) if prev_end is not None else "",
                   unassigned_months=round(c["Unassigned"] / (v["total_expenditures"] / 12), 1) if fund == "General" and c.get("Unassigned") and v.get("total_expenditures") else "")
        out.append(row)
    write_csv(E.data_path("fund_balance_summary.csv"), COLUMNS, out)
    gen = [r for r in out if r["fund"] == "General"]
    print(f"{len(out)} rows -> data/{E.id}/fund_balance_summary.csv")
    bad = [r for r in gen if r["revenues_vs_schedule"] not in ("", 0) or r["expenditures_vs_schedule"] not in ("", 0)]
    print(f"General Fund: {len(gen) - len(bad)} of {len(gen)} years tie to the revenue and expenditure schedules")
    for r in gen:
        flag = "" if r["rollforward_diff"] in ("", 0) else f"  rollforward {r['rollforward_diff']:+,}"
        print(f"  FY{r['fiscal_year']}  fund balance {r['ending_balance']:>11,}  unassigned {r['unassigned'] if r['unassigned'] == '' else format(r['unassigned'], ','):>11}  "
              f"rev {r['revenues_vs_schedule']} exp {r['expenditures_vs_schedule']}{flag}")


if __name__ == "__main__":
    main()
