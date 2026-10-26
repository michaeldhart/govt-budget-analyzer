"""Extract the long-term debt, pension and OPEB disclosures from a Belvidere audit (Notes to the Financial Statements + RSI).

Usage:
  python3 pipelines/belvidere_il/extract_audit_debt_pension.py FY2020            # one audit -> data/belvidere_il/extracted/audits/FY2020_audit_{debt,pension,opeb}_*.csv
  python3 pipelines/belvidere_il/extract_audit_debt_pension.py --build           # consolidate all years -> data/belvidere_il/{debt_balances,...}.csv + cross-year checks

Per-year extracts (long format, one row per number):
  FY20XX_audit_debt_balances.csv      activity x line x measure (beginning, additions, reductions, ending, current_portion)
  FY20XX_audit_debt_issues.csv        issue terms (interest rate, maturity) and the balance / current portion of each issue
  FY20XX_audit_debt_service.csv       debt service to maturity: principal, interest, total by year and instrument
  FY20XX_audit_pension_rollforward.csv  changes in net pension liability: the note (current year, 3 columns) and the RSI (all years shown)
  FY20XX_audit_pension_summary.csv    headline measures per plan (TPL, FNP, NPL, ratios, payroll, contributions, assumptions, members, deferred items, ...)
  FY20XX_audit_opeb_rollforward.csv   OPEB: total OPEB liability roll-forward (GASB 75) or net OPEB obligation table (GASB 45), note and RSI
  FY20XX_audit_debt_pension_checks.csv  every validation check that was run (pass / accepted exception / FAIL)

Validation: see docs/handoff/debt_pension.md.  Overrides: data/belvidere_il/overrides/audit_debt_pension_FY20XX.csv
(kind `allow` = a difference the source itself contains, with the reason; kind `tag` = exception id attached to rows; kind `fixline` = OCR fix).
"""
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from audit_debt_pension_common import (Checker, DATA_E, ENTITY_ID, OUT_DIR, ROOT, load_allow, load_overrides, page_lines)  # noqa: E402
import audit_debt_pension_debt as D  # noqa: E402
import audit_debt_pension_pension as P  # noqa: E402
import audit_debt_pension_opeb as O  # noqa: E402
import audit_debt_pension_stmt as S  # noqa: E402

TEXT_YEARS = range(2016, 2027)


def meas_date(plan, y):
    if plan.startswith("imrf"):
        return f"{y}-12-31"
    return f"{y}-04-30"


def out_path(fy, name):
    return os.path.join(OUT_DIR, f"FY{fy}_audit_{name}.csv")


def write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------------------------------------------------
def read_library_debt(fy, source, prep):
    lp = O.library_note_pages(fy)
    if not lp:
        return dict(activity=[], issues=[], schedule=[], problems=[], pages=[])
    pl = page_lines(fy, lp, source, prep)
    lines = [ln for p in lp for ln in pl[p] if not D.is_furniture(ln.text)]
    blocks = D.split_blocks(lines)
    activity, schedule, problems = [], [], []
    for kind, act, bl in blocks:
        if kind == "act":
            rows = D.parse_activity("component_unit", bl, problems)
            for r in rows:
                r["line"] = re.sub(r"(?i)^discretely presented component unit\s*", "", r["line"]).strip()
            activity.extend(rows)
        elif kind == "sch":
            schedule.extend(D.parse_schedule("component_unit", bl, problems))
    return dict(activity=activity, issues=[], schedule=schedule, problems=problems, pages=lp)


def extract_text_year(fy, source="text", prep="gray"):
    """Everything for one FY2016+ audit.  Returns dict of tables + the Checker."""
    allow, other = load_allow(fy)
    chk = Checker(allow)
    T = dict(debt_balances=[], debt_issues=[], debt_service=[], pension_rollforward=[], pension_summary=[], opeb_rollforward=[])
    doc = f"FY{fy}_audit"

    # ---- debt ----
    debt = D.add_issue_gaps(D.read_debt(fy, source, prep))
    lib = read_library_debt(fy, source, prep)
    D.validate_debt(debt, chk)
    if lib["activity"]:
        libres = dict(activity=lib["activity"], issues=[], schedule=lib["schedule"], problems=lib["problems"])
        D.validate_debt(libres, chk)
    allact = debt["activity"] + lib["activity"]
    for r in allact:
        for m in ("beginning", "additions", "reductions", "ending", "current"):
            T["debt_balances"].append([fy, doc, r["page"], r["activity"], r["line"], r["line_id"], "current_portion" if m == "current" else m, r[m], r.get("flag", "")])
    for r in debt["issues"]:
        T["debt_issues"].append([fy, doc, r["page"], r["activity"], r["issue"], r["rate_text"], r["maturity_text"], r["ending"], r["current"], r.get("flag", ""), r["description"]])
    ends = {(r["activity"], r["line_id"]): r["ending"] for r in allact if r["line_id"] not in ("total",)}
    for r in debt["schedule"] + lib["schedule"]:
        # match the instrument to the balance line whose ending equals the schedule's principal total
        tot = next((x for x in (debt["schedule"] + lib["schedule"]) if x["activity"] == r["activity"] and x["group_idx"] == r["group_idx"] and x["kind"] == "total"), None)
        lid = ""
        if tot and tot["principal"] is not None:
            cands = [k[1] for k, v in ends.items() if k[0] == r["activity"] and v == tot["principal"] and k[1] in D.DEBT_TYPES]
            lid = cands[0] if len(cands) == 1 else ("|".join(cands) if cands else "")
        T["debt_service"].append([fy, doc, r["page"], r["activity"], r["group_idx"], r["group_header"], r["year_label"], r["principal"], r["interest"], r["total"], r["kind"], lid, r.get("flag", "")])

    # ---- pension: RSI ----
    rsi = P.read_rsi(fy, source, prep)
    P.validate_rsi(rsi, chk)
    for r in rsi["rows"]:
        if r["schedule"] == "npl" and r["component"] in ("tpl", "fnp", "npl") and r["kind"] != "blank":
            T["pension_rollforward"].append([fy, doc, r["page"], r["plan_id"], "rsi", meas_date(r["plan_id"], r["column_year"]), r["column_year"], r["component"], r["line_id"], r["value"], ""])
    summ = T["pension_summary"]

    def S_(plan, measure, label, value, text, unit, src, page, flag=""):
        summ.append([fy, doc, page, plan, measure, label, value, text, unit, src, flag])
    for r in rsi["rows"]:
        if r["kind"] == "blank":
            continue
        plan, y = r["plan_id"], r["column_year"]
        if r["schedule"] == "npl":
            if r["line_id"] == "ending" and r["component"] in ("tpl", "fnp"):
                S_(plan, r["component"], y, r["value"], "", "usd", "rsi", r["page"])
            elif r["component"] == "npl":
                S_(plan, "npl", y, r["value"], "", "usd", "rsi", r["page"])
            elif r["component"] == "ratio":
                S_(plan, {"funded_ratio_pct": "funded_ratio_pct", "covered_payroll": "covered_payroll", "npl_pct_covered_payroll": "npl_pct_covered_payroll"}[r["line_id"]], y, r["value"], "",
                   "pct" if r["kind"] == "pct" else "usd", "rsi", r["page"])
        elif r["schedule"] == "contrib":
            meas = {"adc": "adc", "contributions": "employer_contribution_actual", "deficiency": "contribution_deficiency", "covered_payroll": "covered_payroll_contrib",
                    "contrib_pct_covered_payroll": "contribution_pct_covered_payroll"}[r["line_id"]]
            S_(plan, meas, y, r["value"], "", "pct" if r["kind"] == "pct" else "usd", "rsi", r["page"])
        elif r["schedule"] == "returns":
            S_(plan, "money_weighted_return_pct", y, r["value"], "", "pct", "rsi", r["page"])

    # ---- pension: notes ----
    items, note_pages = P.sectioned_lines(fy, source, prep)
    rfs = P.note_rollforwards(items)
    for plan, rows in rfs.items():
        P.validate_rollforward(plan, rows, chk)
        for kind, vals, date, pg in rows:
            for comp, v in zip(("tpl", "fnp", "npl"), vals):
                T["pension_rollforward"].append([fy, doc, pg, plan, "note", date if kind in ("beginning", "ending") else "", "", comp, kind, v, ""])
    mem = P.read_membership(items)
    P.validate_membership(mem, chk)
    for plan, cls, v, pg in mem:
        S_(plan, "members_" + cls, "", v, "", "count", "note", pg)
    sens = P.read_sensitivity(items)
    P.validate_sensitivity(sens, None, chk)
    for (plan, lo, cur, hi, nlo, ncur, nhi, pg) in sens:
        for nm, r_, v in (("minus1", lo, nlo), ("current", cur, ncur), ("plus1", hi, nhi)):
            S_(plan, f"npl_at_discount_rate_{nm}", r_, v, "", "usd", "note", pg)
    d_rows, am_rows, expense = P.read_deferred(items)
    P.validate_deferred(d_rows, am_rows, chk)
    for plan, src, o, i, pg in d_rows:
        S_(plan, f"deferred_outflow:{src}", "", o, "", "usd", "note", pg)
        S_(plan, f"deferred_inflow:{src}", "", i, "", "usd", "note", pg)
    for plan, lab, amt, pg in am_rows:
        S_(plan, f"deferred_amortization:{lab}", "", amt, "", "usd", "note", pg)
    for plan, amt, pg in expense:
        S_(plan, "pension_expense", "", amt, "", "usd", "note", pg)
    alloc = P.read_allocation(items)
    P.validate_allocation(alloc, chk)
    for plan, topic, key, amt, pg in alloc:
        S_(plan if plan != "imrf_all" else "imrf_regular", f"imrf_allocation:{topic}:{key}", "", amt, "", "usd", "note", pg)
    plans_seen = sorted({pl for pl, _s, _t, _l in items if pl and pl not in ("imrf_all", "police_fire")})
    for plan, meas, val, text, unit, pg in P.read_text_facts(items, plans_seen):
        S_(plan, meas, "", val, text, unit, "note", pg)

    # ---- OPEB ----
    cp = O.city_note_pages(fy)
    city = None
    if cp:
        lines = O.note_lines(fy, cp, r"(?i)^\d+\.\s+OTHER POST-?EMPLOYMENT BENEFITS", r"^\d+\.\s+[A-Z][A-Z ,\-'()]{8,}$", source, prep)
        city = O.parse_opeb_flat(lines, "opeb_city")
    lp = O.library_note_pages(fy)
    libo = None
    if lp:
        ll = O.note_lines(fy, lp, r"(?i)^[a-z]\.\s+Other Post-?employment Benefits", r"^[g-z]\.\s+\S", source, prep)
        if ll:
            libo = O.parse_opeb_flat(ll, "opeb_library")
    opeb_rows = T["opeb_rollforward"]
    for plan, res in (("opeb_city", city), ("opeb_library", libo)):
        if not res:
            continue
        g75 = bool(res["rollforward"])
        O.validate_opeb(res, plan, chk, g75)
        for kind, vals, date, pg in res["rollforward"]:
            opeb_rows.append([fy, doc, pg, plan, "note", date if kind in ("beginning", "ending") else "", "", kind, vals[0], ""])
        for key, v, pg in res["noo"]:
            opeb_rows.append([fy, doc, pg, plan, "note_gasb45", "", "", key, v, ""])
        for key, v, unit, pg in res["funded"]:
            opeb_rows.append([fy, doc, pg, plan, "note_gasb45", "", "", "funded_" + key, v, ""])
        for (y, cost, contrib, pct, noo, pg) in res["trend"]:
            for k, v in (("annual_opeb_cost", cost), ("contributions", contrib), ("pct_contributed", pct), ("noo_ending", noo)):
                opeb_rows.append([fy, doc, pg, plan, "note_trend", f"{y}-04-30", y, k, v, ""])
        for cls, v, pg in res["membership"]:
            S_(plan, "members_" + cls, "", v, "", "count", "note", pg)
        for meas, val, text, pg in res["facts"]:
            S_(plan, meas, "", val, text, "pct" if val is not None and meas in ("discount_rate", "salary_increases", "inflation") else "", "note", pg)
        for cat, vals, rates, pg in res["sensitivity"]:
            for nm, v, r_ in zip(("minus1", "current", "plus1"), vals, (rates + [None, None, None])[:3]):
                S_(plan, f"tol_at_{cat}_{nm}", r_ or "", v, "", "usd", "note", pg)
        for src, o, i, pg in res["deferred"]:
            S_(plan, f"deferred_outflow:{src}", "", o, "", "usd", "note", pg)
            S_(plan, f"deferred_inflow:{src}", "", i, "", "usd", "note", pg)
        for lab, amt, pg in res["amort"]:
            S_(plan, f"deferred_amortization:{lab}", "", amt, "", "usd", "note", pg)
        if res["expense"] is not None:
            S_(plan, "opeb_expense", "", res["expense"], "", "usd", "note", cp[0] if plan == "opeb_city" else lp[0])
    orsi = O.read_opeb_rsi(fy, source, prep)
    O.validate_opeb_rsi(orsi, chk)
    for r in orsi["rows"]:
        opeb_rows.append([fy, doc, r["page"], r["plan_id"], "rsi", f"{r['column_year']}-04-30", r["column_year"], r["line_id"], r["value"], ""])

    crosscheck(fy, T, debt, lib, rfs, city, libo, d_rows, alloc, chk, source, prep)
    return T, chk


# ---------------------------------------------------------------------------------------------------------
def crosscheck(fy, T, debt, lib, rfs, city, libo, d_rows, alloc, chk, source, prep):
    """Ties between the notes and other parts of the same audit: debt table vs pension / OPEB notes, RSI vs notes, statement of net position,
    reconciliation of governmental funds."""
    act = {}
    for r in debt["activity"] + lib["activity"]:
        act[(r["activity"], r["line_id"])] = r

    def end(a, lid):
        r = act.get((a, lid))
        return None if r is None else r["ending"]

    def note_end(plan, j):
        rows = rfs.get(plan)
        if not rows:
            return None
        e = next((r for r in rows if r[0] == "ending"), None)
        return None if e is None else e[1][j]
    npp = S.read_net_position(fy, source, prep)
    has_npl_lines = any(k[1].startswith("npl_") for k in act)
    # 1. debt table pension lines vs note NPL (FY2016: the debt table has no pension lines; the Statement of Net Position lists them)
    for plan, lid in (("police", "npl_police"), ("fire", "npl_fire"), ("imrf_slep", "npl_slep")):
        npl = note_end(plan, 2)
        e = end("governmental", lid)
        if npl is None:
            continue
        if e is None and not has_npl_lines:
            row = next((r for r in npp["rows"] if r[0] == "liabilities" and re.search(r"net pension liability", r[1], re.I) and re.search({"police": "police", "fire": "firefighters", "imrf_slep": "slep"}[plan], r[1], re.I)), None)
            if row:
                chk.eq(f"xchk|netpos|npl|{plan}", row[2][0] or 0, max(npl, 0), f"{plan}: Statement of Net Position net pension liability (governmental column) = note")
            continue
        if e is None:
            if npl > 0:
                chk.fail(f"xchk|debt_vs_note|{plan}", f"{plan}: note NPL {npl:,} but no '{lid}' line in the governmental debt table")
            continue
        chk.eq(f"xchk|debt_vs_note|{plan}", e, max(npl, 0), f"{plan}: governmental debt table ending = note net pension liability (asset reported as 0)")
    npl_imrf = note_end("imrf_regular", 2)
    if npl_imrf is not None and not has_npl_lines:
        row = next((r for r in npp["rows"] if r[0] == "liabilities" and re.search(r"net pension liability\s*-\s*imrf", r[1], re.I)), None)
        if row:
            tot_all = sum(note_end(p_, 2) or 0 for p_ in ("imrf_regular", "imrf_eco", "imrf_slep"))
            chk.eq("xchk|netpos|npl|imrf", (row[2][2] or 0) + ((row[2][3] or 0) if len(row[2]) > 3 else 0), tot_all,
                   "IMRF (all plans): Statement of Net Position net pension liability (primary government total + library) = sum of the notes' IMRF plans")
    if npl_imrf is not None and any(k[1] == "npl_imrf" for k in act):
        tot = sum(act[k]["ending"] for k in act if k[1] == "npl_imrf")
        chk.eq("xchk|debt_vs_note|imrf_regular", tot, max(npl_imrf, 0), "IMRF regular: net pension liability in the debt tables (governmental + business-type + library) = note")
    # 2. OPEB
    if city and city["rollforward"]:
        e = next((r for r in city["rollforward"] if r[0] == "ending"), None)
        tot = sum(act[k]["ending"] for k in act if k[1] == "opeb" and k[0] in ("governmental", "business_type"))
        if e:
            chk.eq("xchk|debt_vs_note|opeb_city", tot, e[1][0], "city OPEB: governmental + business-type debt table = total OPEB liability in the note")
    if libo and libo["rollforward"]:
        e = next((r for r in libo["rollforward"] if r[0] == "ending"), None)
        if e and ("component_unit", "opeb") in act:
            chk.eq("xchk|debt_vs_note|opeb_library", act[("component_unit", "opeb")]["ending"], e[1][0], "library OPEB: component-unit debt table = total OPEB liability in the library note")
    # 3. RSI last column vs note roll-forward
    # (done in build: the RSI column for the current measurement year must equal the note's ending)
    # 4. Statement of net position
    if npp["rows"]:
        def np_find(pat, section=None):
            return next((r for r in npp["rows"] if re.search(pat, r[1], re.I) and (section is None or r[0] == section)), None)
        within = np_find(r"due within one year")
        after = np_find(r"due in more than one year")
        for a, col in (("governmental", 0), ("business_type", 1), ("component_unit", 3)):
            tot = act.get((a, "total"))
            if tot is None and a == "component_unit":
                continue
            if tot is None:
                continue
            if within and within[2][col] is not None:
                chk.eq(f"xchk|netpos|{a}|due_within_one_year", within[2][col], tot["current"], f"{a}: Statement of Net Position 'due within one year' = debt table current portion total")
            if after and within and after[2][col] is not None:
                # after the GASB 68 audits the net pension liability is inside 'due in more than one year'
                chk.eq(f"xchk|netpos|{a}|due_after_one_year", after[2][col], tot["ending"] - tot["current"], f"{a}: Statement of Net Position 'due in more than one year' = total ending - current portion")
        # deferred pension / OPEB items by plan
        dmap = {}
        for plan, src, o, i, pg in d_rows:
            if src == "TOTAL":
                dmap[plan] = (o, i)
        np_plan = [(r"pension items - imrf \(regular\)|pension items - imrf$|pension items - imrf\b(?!.*slep)", "imrf_regular"), (r"slep", "imrf_slep"), (r"police", "police"), (r"firefighters", "fire")]
        for sec, idx in (("deferred_outflows", 0), ("deferred_inflows", 1)):
            for pat, plan in np_plan:
                row = next((r for r in npp["rows"] if r[0] == sec and re.search(r"pension items", r[1], re.I) and re.search(pat, r[1], re.I)
                            and not (plan == "imrf_regular" and re.search("slep", r[1], re.I))), None)
                if row is None or plan not in dmap:
                    continue
                if plan in ("police", "fire") or (plan == "imrf_slep"):
                    chk.eq(f"xchk|netpos|{sec}|{plan}", row[2][2] or 0, dmap[plan][idx], f"{plan}: Statement of Net Position deferred {'outflows' if idx == 0 else 'inflows'} (total column) = pension note")
                elif not any(re.search("slep", r[1], re.I) and r[0] == sec for r in npp["rows"]):     # FY2016-17 style: one 'IMRF' line for all IMRF plans
                    lib_col = (row[2][3] or 0) if len(row[2]) > 3 else 0
                    allp = sum(dmap.get(p_, (0, 0))[idx] for p_ in ("imrf_regular", "imrf_eco", "imrf_slep"))
                    chk.eq(f"xchk|netpos|{sec}|imrf", (row[2][2] or 0) + lib_col, allp, "IMRF (all plans): Statement of Net Position deferred items (primary government total + library column) = sum of the IMRF notes")
                else:   # the note covers the library's share too: primary government total column + component-unit column
                    lib_col = (row[2][3] or 0) if len(row[2]) > 3 else 0
                    chk.eq(f"xchk|netpos|{sec}|{plan}", (row[2][2] or 0) + lib_col, dmap[plan][idx], "IMRF regular: Statement of Net Position deferred items (primary government total + library column) = pension note")
        if city:
            tot = next((r for r in city["deferred"] if r[0] == "TOTAL"), None)
            for sec, idx in (("deferred_outflows", 1), ("deferred_inflows", 2)):
                row = next((r for r in npp["rows"] if r[0] == sec and re.search(r"opeb", r[1], re.I)), None)
                if row and tot:
                    chk.eq(f"xchk|netpos|{sec}|opeb_city", row[2][2] or 0, tot[idx], f"city OPEB: Statement of Net Position deferred items (total column) = OPEB note")
        if libo:
            tot = next((r for r in libo["deferred"] if r[0] == "TOTAL"), None)
            for sec, idx in (("deferred_outflows", 1), ("deferred_inflows", 2)):
                row = next((r for r in npp["rows"] if r[0] == sec and re.search(r"opeb", r[1], re.I)), None)
                if row and tot and len(row[2]) > 3 and row[2][3] is not None:
                    chk.eq(f"xchk|netpos|{sec}|opeb_library", row[2][3], tot[idx], f"library OPEB: Statement of Net Position deferred items (library column) = library OPEB note")
    # 5. reconciliation of governmental funds
    rec = S.read_reconciliation(fy, source, prep)
    for lid, amt, pg, label in S.rec_line_ids(rec["rows"]):
        if lid == "lump_long_term_liabilities":
            continue
        r = act.get(("governmental", lid))
        if r is None:
            chk.fail(f"xchk|reconciliation|{lid}", f"reconciliation line {label!r} ({amt:,}) has no '{lid}' line in the governmental debt table")
            continue
        chk.eq(f"xchk|reconciliation|{lid}", amt, r["ending"], f"reconciliation of governmental funds: {label[-40:]!r} = governmental debt table ending")


def checks_rows(fy, chk):
    rows = []
    for t in sorted(set(chk.n_pass) | set(chk.n_fail)):
        rows.append([fy, t, chk.n_pass.get(t, 0), len(chk.accepted) and sum(1 for a in chk.accepted if a["key"].split("|")[0] == t), chk.n_fail.get(t, 0)])
    return rows


def write_tables(fy, T, chk):
    os.makedirs(OUT_DIR, exist_ok=True)
    write_csv(out_path(fy, "debt_balances"), ["fiscal_year", "doc", "pdf_page", "activity", "line_item", "line_id", "measure", "amount", "flag"], T["debt_balances"])
    write_csv(out_path(fy, "debt_issues"), ["fiscal_year", "doc", "pdf_page", "activity", "issue", "interest_rate_text", "maturity_text", "ending_balance", "current_portion", "flag", "description"], T["debt_issues"])
    write_csv(out_path(fy, "debt_service"), ["fiscal_year", "doc", "pdf_page", "activity", "group_idx", "instrument", "year_label", "principal", "interest", "total", "row_kind", "line_id", "flag"], T["debt_service"])
    write_csv(out_path(fy, "pension_rollforward"), ["fiscal_year", "doc", "pdf_page", "plan_id", "source", "measurement_date", "column_year", "component", "line_id", "amount", "flag"], T["pension_rollforward"])
    write_csv(out_path(fy, "pension_summary"), ["fiscal_year", "doc", "pdf_page", "plan_id", "measure", "period_label", "value", "value_text", "unit", "source", "flag"], T["pension_summary"])
    write_csv(out_path(fy, "opeb_rollforward"), ["fiscal_year", "doc", "pdf_page", "plan_id", "source", "measurement_date", "column_year", "line_id", "amount", "flag"], T["opeb_rollforward"])
    crow = []
    for t in sorted(set(chk.n_pass) | set(chk.n_fail)):
        acc = sum(1 for a in chk.accepted if a["key"].split("|")[0] == t)
        crow.append([fy, t, chk.n_pass.get(t, 0), acc, chk.n_fail.get(t, 0)])
    write_csv(out_path(fy, "debt_pension_checks"), ["fiscal_year", "check_group", "passed", "accepted_exceptions", "failed"], crow)


def report(fy, T, chk, quiet=False):
    if quiet:
        return
    print(f"FY{fy}: debt {len(T['debt_balances'])} cells, issues {len(T['debt_issues'])}, schedule {len(T['debt_service'])}, pension roll-forward {len(T['pension_rollforward'])}, "
          f"summary {len(T['pension_summary'])}, opeb {len(T['opeb_rollforward'])}; checks passed {sum(chk.n_pass.values())}, accepted {len(chk.accepted)}, FAILED {len(chk.problems)}")
    for a in chk.accepted:
        print(f"  accepted [{a['key']}] {a['desc']} (diff {a['diff']})")
    for p in chk.problems:
        print("  !", p)
    for k in sorted(set(chk.allow) - chk.used):
        print("  ! unused allow override:", k)


def run_year(fy, source="text", prep="gray", write=True, quiet=False):
    T, chk = extract_text_year(fy, source, prep)
    if write:
        write_tables(fy, T, chk)
    report(fy, T, chk, quiet)
    return T, chk


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--build" in sys.argv:
        import audit_debt_pension_build as B
        B.main()
        return
    fy = int(args[0].replace("FY", ""))
    source = "text" if fy >= 2016 else "ocr"
    if "--source" in sys.argv:
        source = sys.argv[sys.argv.index("--source") + 1]
    if fy < 2016:
        import audit_debt_pension_old as OLD
        T, chk = OLD.run_year(fy)
    else:
        T, chk = run_year(fy, source)
    sys.exit(1 if chk.problems else 0)


if __name__ == "__main__":
    main()
