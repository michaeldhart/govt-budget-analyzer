"""Consolidate the per-year audit debt / pension / OPEB extracts into canonical long tables and run the cross-year checks.

Writes (new files only; nothing shared is rewritten):
  data/belvidere_il/debt_balances.csv, debt_issue_terms.csv, debt_service_schedule.csv, pension_rollforward.csv, pension_summary.csv, opeb_rollforward.csv
  data/belvidere_il/audit_debt_pension_checks.csv       per-year validation counts (passed / accepted exceptions / failed)
  data/belvidere_il/audit_debt_pension_crosschecks.csv  cross-year and cross-statement checks (chain, RSI vs note, fund statements)
Run: python3 pipelines/belvidere_il/extract_audit_debt_pension.py --build   (after extracting every year FY2012-FY2026)
"""
import csv
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_debt_pension_common import DATA_E, ENTITY_ID, OUT_DIR, load_overrides  # noqa: E402

YEARS = range(2012, 2027)


def rd(fy, name):
    path = os.path.join(OUT_DIR, f"FY{fy}_audit_{name}.csv")
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def num(x):
    if x in ("", None):
        return None
    try:
        v = float(x)
        return int(v) if v == int(v) and "." not in str(x) else v
    except ValueError:
        return None


# exception id attachment: override `allow` rows carry an exception_id; map the check key to the rows it concerns
def exception_rules(fy):
    rules = []
    for o in load_overrides(fy):
        if o["kind"] != "allow" or not o.get("exception_id"):
            continue
        k = o["key"].split("|")
        rules.append((k, o["exception_id"]))
    return rules


def tag(table, row, rules):
    """row: dict; returns ';'-joined exception ids whose key matches the row"""
    ids = []
    for k, eid in rules:
        g = k[0]
        if table == "pension_rollforward" and g == "rf" and row["plan_id"] == k[1] and row["source"] == "note":
            ids.append(eid)
        elif table == "pension_summary" and g == "rsi" and row["plan_id"] == k[1] and row["measure"] in ("contribution_deficiency", "adc", "employer_contribution_actual") and str(row["period_label"]) == k[2]:
            ids.append(eid)
        elif table == "pension_summary" and g == "def" and row["plan_id"] == k[1] and row["measure"].startswith("deferred_"):
            ids.append(eid)
        elif table == "pension_summary" and g == "pen" and row["plan_id"] == k[1] and row["measure"].startswith("trend_"):
            ids.append(eid)
        elif table == "opeb_rollforward" and g == "opeb" and row["source"] in ("note", "note_trend") and row["plan_id"] == k[1]:
            ids.append(eid)
        elif table == "debt_service_schedule" and g == "debt" and len(k) > 2 and k[2] == "sched" and row["activity"] == k[1]:
            ids.append(eid)
    return ";".join(sorted(set(ids)))


def doc_year_tables():
    T = defaultdict(list)
    for fy in YEARS:
        rules = exception_rules(fy)
        for r in rd(fy, "debt_balances"):
            T["debt_balances"].append(dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, activity=r["activity"], line_id=r["line_id"], line_item=r["line_item"], measure=r["measure"],
                                           amount=r["amount"], pdf_page=r["pdf_page"], flag=r["flag"], exception_ids=""))
        for r in rd(fy, "debt_issues"):
            T["debt_issue_terms"].append(dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, activity=r["activity"], issue=r["issue"], interest_rate_text=r["interest_rate_text"],
                                              maturity_text=r["maturity_text"], ending_balance=r["ending_balance"], current_portion=r["current_portion"], description=r["description"],
                                              pdf_page=r["pdf_page"], flag=r["flag"], exception_ids=""))
        for r in rd(fy, "debt_service"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, activity=r["activity"], instrument=r["instrument"], line_id=r["line_id"], year_ending=r["year_label"],
                       principal=r["principal"], interest=r["interest"], total=r["total"], row_kind=r["row_kind"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("debt_service_schedule", row, rules)
            T["debt_service_schedule"].append(row)
        for r in rd(fy, "pension_rollforward"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], source=r["source"], measurement_date=r["measurement_date"], column_year=r["column_year"],
                       component=r["component"], line_id=r["line_id"], amount=r["amount"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("pension_rollforward", row, rules)
            T["pension_rollforward"].append(row)
        for r in rd(fy, "pension_summary"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], measure=r["measure"], period_label=r["period_label"], value=r["value"], value_text=r["value_text"],
                       unit=r["unit"], source=r["source"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("pension_summary", row, rules)
            T["pension_summary"].append(row)
        for r in rd(fy, "opeb_rollforward"):
            row = dict(entity_id=ENTITY_ID, doc_id=r["doc"], period_fy=fy, plan_id=r["plan_id"], source=r["source"], measurement_date=r["measurement_date"], column_year=r["column_year"],
                       line_id=r["line_id"], amount=r["amount"], pdf_page=r["pdf_page"], flag=r["flag"])
            row["exception_ids"] = tag("opeb_rollforward", row, rules)
            T["opeb_rollforward"].append(row)
    return T


COLS = dict(
    debt_balances=["entity_id", "doc_id", "period_fy", "activity", "line_id", "line_item", "measure", "amount", "pdf_page", "flag", "exception_ids"],
    debt_issue_terms=["entity_id", "doc_id", "period_fy", "activity", "issue", "interest_rate_text", "maturity_text", "ending_balance", "current_portion", "description", "pdf_page", "flag", "exception_ids"],
    debt_service_schedule=["entity_id", "doc_id", "period_fy", "activity", "instrument", "line_id", "year_ending", "principal", "interest", "total", "row_kind", "pdf_page", "flag", "exception_ids"],
    pension_rollforward=["entity_id", "doc_id", "period_fy", "plan_id", "source", "measurement_date", "column_year", "component", "line_id", "amount", "pdf_page", "flag", "exception_ids"],
    pension_summary=["entity_id", "doc_id", "period_fy", "plan_id", "measure", "period_label", "value", "value_text", "unit", "source", "pdf_page", "flag", "exception_ids"],
    opeb_rollforward=["entity_id", "doc_id", "period_fy", "plan_id", "source", "measurement_date", "column_year", "line_id", "amount", "pdf_page", "flag", "exception_ids"],
)


def write(name, rows):
    path = os.path.join(DATA_E, f"{name}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS[name])
        w.writeheader()
        w.writerows(rows)
    return path


# ---------------------------------------------------------------------------------------------------------
class X:
    def __init__(self):
        self.rows = []

    def add(self, group, key, a, b, note="", tol=0):
        if a is None or b is None:
            self.rows.append([group, key, a, b, "", "n/a", note])
            return
        d = a - b
        self.rows.append([group, key, a, b, d, "pass" if abs(d) <= tol else "DIFF", note])


KNOWN = [
    (r"debt_chain.*FY2012->FY2013", "FY2013 restated its opening balances (1993 TIF note forgiven, landfill, bonds refunded and reissued, net pension obligation); see FY2013 'beginning as restated'"),
    (r"debt_chain.*FY2015->FY2016", "FY2016's liability table no longer carries the net pension obligation / pension lines (GASB 68 liability is shown separately)"),
    (r"debt_chain.*FY2016->FY2017", "FY2017 restated the opening balances and added the net pension liability lines to the debt note (GASB 68 presentation)"),
    (r"debt_chain.*FY2017->FY2018", "FY2018 opening balance differs from FY2017 ending by $53,040 (SLEP net pension liability in FY2017 reported as an asset / reclassified); see exceptions"),
    (r"debt_chain.*FY2018->FY2019", "FY2019 adopted GASB 75: OPEB liability restated (governmental 3,061,591 -> 6,391,111; business-type; library)"),
    (r"debt_chain.*FY2024->FY2025", "FY2025 opening IEPA balance excludes $152,924 of loan #L17-4188 reported as outstanding at April 30, 2024 (not fully disbursed)"),
    (r"rsi_summary.*(deficiency).*2021 FY2025 vs FY2026", "FY2026 RSI prints a $1 deficiency for 2021 that FY2021-FY2025 print as zero (EX-adp-rsi-fy2026-deficiency)"),
    (r"rsi_summary.*FY201[67] vs FY201[78]$", "the first GASB 67/68 RSI years were re-stated / re-labelled in the next audit (covered payroll, contributions, ADC for the earliest columns); both audits tie to their own identities"),
    (r"rsi_summary.*covered_payroll|rsi_summary.*npl_pct_covered_payroll", "covered payroll for the 2016 column re-stated between the FY2024 and FY2025 audits (fire 2,202,000 -> 2,200,810 etc.); each audit ties to its own identities"),
    (r"note_vs_rsi.*FY2016 police", "city's own $1 rounding in the FY2016 police note roll-forward (EX-adp-police-fy2016-note-rounding)"),
    (r"opeb_noo_chain.*FY2015->FY2016", "FY2015 net OPEB obligation 2,196,448 = city 2,154,179 + library 42,269; FY2016 reports the city portion only"),
    (r"fund_statements_principal.*FY2012", "fund statement principal 86,408 = 40,000 debt certificates + 46,408 landfill post-closure costs paid (presented as principal)"),
]


def explain(rows):
    out = []
    for r in rows:
        if r[5] == "DIFF":
            for pat, why in KNOWN:
                if re.search(pat, r[0] + " " + r[1]):
                    r = r[:5] + ["DIFF-explained", why]
                    break
        out.append(r)
    return out


def crosschecks(T):
    x = X()
    # A. debt balances chain: ending(N) = beginning(N+1)
    bal = {}
    for r in T["debt_balances"]:
        bal[(int(r["period_fy"]), r["activity"], r["line_id"], r["measure"])] = bal.get((int(r["period_fy"]), r["activity"], r["line_id"], r["measure"]), 0) + (num(r["amount"]) or 0)
    keys = sorted({(a, l) for (_y, a, l, _m) in bal})
    for fy in range(2012, 2026):
        for (a, l) in keys:
            e, b = bal.get((fy, a, l, "ending")), bal.get((fy + 1, a, l, "beginning"))
            if e is None and b is None:
                continue
            if l in ("total",):
                x.add("debt_chain_total", f"FY{fy}->FY{fy + 1} {a} total", e, b, "ending(N) vs beginning(N+1)")
            elif e is not None and b is not None:
                x.add("debt_chain_line", f"FY{fy}->FY{fy + 1} {a} {l}", e, b, "ending(N) vs beginning(N+1)")
            else:
                x.add("debt_chain_line", f"FY{fy}->FY{fy + 1} {a} {l}", e, b, "line present in only one year (reclassification, new issue or retired)")
    # B. RSI: later audits restate / repeat earlier columns
    rf = defaultdict(dict)
    for r in T["pension_rollforward"]:
        if r["source"] == "rsi":
            rf[(r["plan_id"], r["column_year"], r["component"], r["line_id"])][int(r["period_fy"])] = num(r["amount"])
    for k, byd in sorted(rf.items()):
        ys = sorted(byd)
        for y0, y1 in zip(ys, ys[1:]):
            x.add("rsi_vs_earlier_audit", f"{k[0]} col {k[1]} {k[2]} {k[3]} FY{y0} vs FY{y1}", byd[y1], byd[y0], "same schedule column in consecutive audits")
    ps = defaultdict(dict)
    for r in T["pension_summary"]:
        if r["source"] == "rsi":
            ps[(r["plan_id"], r["measure"], r["period_label"])][int(r["period_fy"])] = num(r["value"])
    for k, byd in sorted(ps.items()):
        ys = sorted(byd)
        for y0, y1 in zip(ys, ys[1:]):
            x.add("rsi_summary_vs_earlier_audit", f"{k[0]} {k[1]} {k[2]} FY{y0} vs FY{y1}", byd[y1], byd[y0], "same RSI cell in consecutive audits", tol=0.0)
    # C. RSI last column vs note (same audit)
    note = {}
    for r in T["pension_rollforward"]:
        if r["source"] == "note" and r["line_id"] in ("beginning", "ending"):
            note[(int(r["period_fy"]), r["plan_id"], r["component"], r["line_id"])] = num(r["amount"])
    for (fy, plan, comp, kind), v in sorted(note.items()):
        if kind != "ending" or comp not in ("tpl", "fnp", "npl"):
            continue
        yr = fy if not plan.startswith("imrf") else fy - 1
        if plan.startswith("imrf") and fy >= 2016 or not plan.startswith("imrf") and fy >= 2016:
            r = rf.get((plan, str(yr), comp, "ending" if comp != "npl" else "ending"), {})
            x.add("note_vs_rsi", f"FY{fy} {plan} {comp} ending", v, r.get(fy), "note roll-forward ending vs RSI column for the same measurement date")
    # D. note chain: beginning(N+1) = ending(N)
    for (fy, plan, comp, kind), v in sorted(note.items()):
        if kind == "beginning":
            prev = note.get((fy - 1, plan, comp, "ending"))
            if prev is not None:
                x.add("pension_note_chain", f"FY{fy - 1}->FY{fy} {plan} {comp}", prev, v, "ending(N) vs beginning(N+1)")
    # E. OPEB chain
    ob = {}
    for r in T["opeb_rollforward"]:
        if r["source"] == "note" and r["line_id"] in ("beginning", "ending"):
            ob[(int(r["period_fy"]), r["plan_id"], r["line_id"])] = num(r["amount"])
    for (fy, plan, kind), v in sorted(ob.items()):
        if kind == "beginning" and (fy - 1, plan, "ending") in ob:
            x.add("opeb_note_chain", f"FY{fy - 1}->FY{fy} {plan}", ob[(fy - 1, plan, "ending")], v, "total OPEB liability ending(N) vs beginning(N+1)")
    # GASB 45 chain: NOO ending(N) = NOO beginning(N+1)
    noo = {}
    for r in T["opeb_rollforward"]:
        if r["source"] == "note_gasb45" and r["line_id"] in ("noo_beginning", "noo_ending"):
            noo[(int(r["period_fy"]), r["plan_id"], r["line_id"])] = num(r["amount"])
    for (fy, plan, kind), v in sorted(noo.items()):
        if kind == "noo_beginning" and (fy - 1, plan, "noo_ending") in noo:
            x.add("opeb_noo_chain", f"FY{fy - 1}->FY{fy} {plan}", noo[(fy - 1, plan, "noo_ending")], v, "net OPEB obligation ending(N) vs beginning(N+1)")
    orsi = defaultdict(dict)
    for r in T["opeb_rollforward"]:
        if r["source"] == "rsi":
            orsi[(r["plan_id"], r["column_year"], r["line_id"])][int(r["period_fy"])] = num(r["amount"])
    for k, byd in sorted(orsi.items()):
        ys = sorted(byd)
        for y0, y1 in zip(ys, ys[1:]):
            x.add("opeb_rsi_vs_earlier_audit", f"{k[0]} col {k[1]} {k[2]} FY{y0} vs FY{y1}", byd[y1], byd[y0], "same OPEB RSI column in consecutive audits")
    for (fy, plan, kind), v in sorted(ob.items()):
        if kind == "ending" and fy >= 2019:
            r = orsi.get((plan, str(fy), "ending"), {})
            x.add("opeb_note_vs_rsi", f"FY{fy} {plan}", v, r.get(fy), "note ending vs RSI column")
    # F. governmental funds statement: principal retirement vs reductions on debt lines
    fs = os.path.join(DATA_E, "fund_statements.csv")
    if os.path.exists(fs):
        prin = {}
        for r in csv.DictReader(open(fs)):
            if r["statement"] == "changes_in_fund_balance" and r["fund"].startswith("Total") and re.match(r"(?i)^principal", r["line_item"]):
                prin[int(r["period_fy"])] = prin.get(int(r["period_fy"]), 0) + (num(r["amount"]) or 0)
        debt_ids = {"debt_certificates", "capital_lease", "installment_contract", "revolving_loan", "mortgage_loan", "lease"}
        for fy, p in sorted(prin.items()):
            red = sum(bal.get((fy, "governmental", l, "reductions"), 0) for l in debt_ids)
            x.add("fund_statements_principal", f"FY{fy} governmental principal paid", p, red,
                  "Statement of revenues, expenditures and changes in fund balances (principal) vs reductions on debt certificates / leases / installment contract / revolving loan in the debt note (TIF forgiveness and landfill excluded)")
    return x.rows


def main():
    T = doc_year_tables()
    for name, rows in T.items():
        p = write(name, rows)
        print(f"{os.path.relpath(p, os.path.join(HERE, '..', '..'))}: {len(rows):,} rows")
    # per-year check log
    out = []
    for fy in YEARS:
        out += [[ENTITY_ID, f"FY{fy}_audit", fy] + [r[1], r[2], r[3], r[4]] for r in rd_checks(fy)]
    with open(os.path.join(DATA_E, "audit_debt_pension_checks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entity_id", "doc_id", "period_fy", "check_group", "passed", "accepted_exceptions", "failed"])
        w.writerows(out)
    xr = explain(crosschecks(T))
    with open(os.path.join(DATA_E, "audit_debt_pension_crosschecks.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check_group", "key", "value_a", "value_b", "diff", "status", "note"])
        w.writerows(xr)
    from collections import Counter
    c = Counter((r[0], r[5]) for r in xr)
    for k in sorted(c):
        print(f"  {k[0]:28s} {k[1]:5s} {c[k]}")


def rd_checks(fy):
    path = os.path.join(OUT_DIR, f"FY{fy}_audit_debt_pension_checks.csv")
    return [[r["fiscal_year"], r["check_group"], r["passed"], r["accepted_exceptions"], r["failed"]] for r in csv.DictReader(open(path))] if os.path.exists(path) else []


if __name__ == "__main__":
    main()
