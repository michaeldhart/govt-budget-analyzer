"""Build Belvidere's canonical salary schedule (Exhibit B) and its checks from the per-year extracts.

Usage: python3 pipelines/belvidere_il/build_salaries.py

Inputs : data/belvidere_il/extracted/FY20XX_budget_salaries{,_totals}.csv   (extract_salaries.py; FY2027R = ordinance 780H)
         entities/belvidere_il/salary_positions.csv, salary_chain_exceptions.csv, exceptions.csv, facts.csv
Outputs: data/belvidere_il/salary_schedule.csv           canonical (govbudget.schema.SALARY_SCHEDULE_COLUMNS), no incumbent names
         data/belvidere_il/salary_schedule_checks.csv    printed totals, prior-year chain, revision vs adopted
         data/belvidere_il/salary_schedule_vs_budget.csv schedule total beside the budget's salary accounts

The chain check compares every prior-year cell with that year's own (adopted) figure in the previous ordinance; a difference of more
than a dollar fails the build unless entities/belvidere_il/salary_chain_exceptions.csv names the exception that explains it.
"""
import csv
import glob
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from govbudget import Entity  # noqa: E402
from govbudget.entity import write_csv  # noqa: E402
from govbudget.schema import SALARY_SCHEDULE_COLUMNS  # noqa: E402

E = Entity.load("belvidere_il")
EXT = E.data_path("extracted")
CHECK_COLUMNS = ["check", "doc_id", "period_fy", "position_id", "slot", "expected", "actual", "diff", "status", "exception_ids"]
VS_COLUMNS = ["entity_id", "period_fy", "schedule_total", "positions", "general_fund_salaries", "water_sewer_salaries", "schedule_share", "exception_ids"]
SALARY_OBJECTS = {"5000", "5010", "5020"}


def load(tag):
    return list(csv.DictReader(open(os.path.join(EXT, f"{tag}_budget_salaries.csv"))))


def main():
    pos = {r["position_id"]: r for r in E.table("salary_positions.csv")}
    chain_ex = {(int(r["document_fy"]), r["position_id"], int(r["slot"]), int(r["period_fy"])): r["exception_id"]
                for r in E.table("salary_chain_exceptions.csv")}
    known = {x["exception_id"] for x in E.exceptions()}
    for v in chain_ex.values():
        if v not in known:
            sys.exit(f"salary_chain_exceptions.csv: unknown exception {v}")
    tags = [f"FY{y}" for y in range(2015, 2028)] + ["FY2027R"]
    canon, checks = [], []
    own = {}   # (fy, position, slot) -> adopted pay of that document's own year
    for tag in tags:
        for r in load(tag):
            rev = r["version"] == "revised"
            fy, per = int(r["document_fy"]), int(r["period_fy"])
            basis = ("revised" if rev else "adopted") if per == fy else "prior_doc_budget"
            k = (fy, r["position_id"], int(r["slot"]), per)
            ex = chain_ex.get(k, "") if not rev else ""
            canon.append(dict(entity_id=E.id, doc_id=r["doc_id"], version=r["version"], period_fy=per, basis=basis,
                              position_id=r["position_id"], slot=r["slot"], position=pos[r["position_id"]]["position"],
                              status=r["status"], amount=r["amount"], pct_printed=r["pct_printed"], pdf_page=r["pdf_page"],
                              flag=r["flag"], exception_ids=ex))
            if basis == "adopted":
                own[(fy, r["position_id"], int(r["slot"]))] = float(r["amount"])
    # printed totals (from the extractor, already validated there)
    for tag in tags:
        for r in csv.DictReader(open(os.path.join(EXT, f"{tag}_budget_salaries_totals.csv"))):
            checks.append(dict(check=r["kind"], doc_id=r["doc_id"], period_fy=r["period_fy"], position_id="", slot="", expected=r["printed"],
                               actual=r["computed"], diff=r["diff"], status=r["status"], exception_ids=""))
    # prior-year chain
    fails = []
    for c in canon:
        if c["basis"] != "prior_doc_budget" or c["version"] == "revised":
            continue
        fy = int(c["doc_id"][2:6])
        per, k = int(c["period_fy"]), (int(c["period_fy"]), c["position_id"], int(c["slot"]))
        if per < 2015 or k not in own:
            continue  # FY2014 and new positions have no earlier ordinance to compare with
        d = round(float(c["amount"]) - own[k], 2)
        ex = chain_ex.get((fy, c["position_id"], int(c["slot"]), per), "")
        st = "ok" if abs(d) <= 1.0 else ("exception" if ex else "FAIL")
        if st == "FAIL":
            fails.append(f"FY{fy} {c['position_id']} {c['slot']} FY{per}: prior-year column {c['amount']} vs FY{per} ordinance {own[k]:.2f}")
        checks.append(dict(check="prior_year_chain", doc_id=c["doc_id"], period_fy=per, position_id=c["position_id"], slot=c["slot"],
                           expected=f"{own[k]:.2f}", actual=c["amount"], diff=f"{d:.2f}", status=st, exception_ids=ex))
    # 780H against 753H: only the Building Inspector changes
    a = {(r["position_id"], r["slot"], r["period_fy"]): r["amount"] for r in canon if r["doc_id"] == "FY2027_budget" and r["period_fy"] in (2026, 2027)}
    for r in canon:
        if r["version"] != "revised":
            continue
        k = (r["position_id"], r["slot"], r["period_fy"])
        d = round(float(r["amount"]) - float(a.get(k, 0)), 2)
        st = "ok" if d == 0 else ("revised" if r["position_id"] == "building_inspector" and r["period_fy"] == 2027 else "FAIL")
        if st == "FAIL":
            fails.append(f"780H {k} differs from 753H by {d}")
        checks.append(dict(check="revision_vs_adopted", doc_id=r["doc_id"], period_fy=r["period_fy"], position_id=r["position_id"], slot=r["slot"],
                           expected=a.get(k, ""), actual=r["amount"], diff=f"{d:.2f}", status=st, exception_ids=""))
    if fails or any(c["status"] == "FAIL" for c in checks):
        sys.exit("salary schedule checks failed:\n  " + "\n  ".join(fails or ["(see totals)"]))
    write_csv(E.data_path("salary_schedule.csv"), SALARY_SCHEDULE_COLUMNS, canon)
    write_csv(E.data_path("salary_schedule_checks.csv"), CHECK_COLUMNS, checks)

    # schedule total beside the adopted budget's salary accounts (General Fund and Water/Sewer)
    sal = defaultdict(lambda: defaultdict(float))
    for r in csv.DictReader(open(E.data_path("facts.csv"))):
        if r["source_type"] == "budget" and r["basis"] == "adopted" and r["flow"] == "expense" and r["fund_code"] in ("01", "61") \
                and r["account_key"].split("-")[-1] in SALARY_OBJECTS:
            sal[int(r["period_fy"])][r["fund_code"]] += float(r["amount"])
    vs = []
    for fy in range(2015, 2028):
        rows = [c for c in canon if c["version"] == "adopted" and c["basis"] == "adopted" and c["period_fy"] == fy]
        tot = sum(float(c["amount"]) for c in rows)
        gf, ws = sal[fy]["01"], sal[fy]["61"]
        vs.append(dict(entity_id=E.id, period_fy=fy, schedule_total=f"{tot:.2f}", positions=len(rows), general_fund_salaries=f"{gf:.0f}",
                       water_sewer_salaries=f"{ws:.0f}", schedule_share=f"{tot / (gf + ws):.4f}", exception_ids="EX-salary-not-comparable"))
    write_csv(E.data_path("salary_schedule_vs_budget.csv"), VS_COLUMNS, vs)
    n = defaultdict(int)
    for c in checks:
        n[(c["check"], c["status"])] += 1
    print(f"salary_schedule.csv: {len(canon)} rows; checks: " + ", ".join(f"{k[0]} {k[1]} {v}" for k, v in sorted(n.items())))


if __name__ == "__main__":
    main()
