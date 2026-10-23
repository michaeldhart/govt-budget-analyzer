"""Extract the audit's governmental funds Balance Sheet and Statement of Revenues, Expenditures and Changes in Fund Balances.

Usage: python3 pipelines/belvidere_il/extract_audit_fund_statements.py FY2020 [--prep auto|text|gray]

Each statement is a table with one column per governmental fund (General, sometimes Motor Fuel Tax and/or Capital Projects,
Nonmajor funds) and a Total column. Output: data/belvidere_il/extracted/audits/FY20XX_audit_fund_statements.csv, one row per
statement line x fund column.

Validation (every year must pass; the printed totals are the judge, as for the budgets and the audit schedules):
  * every "Total ..." line equals the sum of the lines above it since the previous total (or of the previous totals, for
    "Total liabilities and deferred inflows" and "Total liabilities, deferred inflows and fund balances");
  * TOTAL ASSETS = total liabilities, deferred inflows and fund balances (the balance sheet balances);
  * the Total column equals the sum of the fund columns on every line;
  * statement of changes: excess = revenues - expenditures; net change = excess + other financing; ending fund balance =
    beginning + net change.
Cells the audit prints blank (instead of a dash) in the FY2012-FY2015 scans are placed by the same identities.
Eyeballed corrections: data/belvidere_il/overrides/audit_fund_FY20XX.csv (kind `fixline`: the OCR'd line -> the corrected line).
"""
import csv
import os
import re
import sys
from collections import Counter

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, ROOT, audit_pdf, clean, page_texts_for_locating, split_numbers  # noqa: E402
from extract_budget import ocr_page  # noqa: E402

TITLE_BS = re.compile(r"BALANCE SHEET", re.I)
TITLE_ST = re.compile(r"REVENUES,? EXPENDITURES", re.I)


def locate(fy):
    """{'balance_sheet': (first, last), 'changes_in_fund_balance': (first, last)} 1-based pdf pages."""
    texts = page_texts_for_locating(fy)
    bs = st = None
    for i, t in enumerate(texts[:70], 1):
        head = re.sub(r"\s+", " ", t[:450])
        if len(re.findall(r"\d{1,3}[,.]\d{3}", t)) < 12 or not re.search(r"(?i)governmental fund", head):
            continue
        if re.search(r"(?i)reconciliation|combining|nonmajor governmental funds\b.*combining", head):
            continue
        if bs is None and TITLE_BS.search(head) and not re.search(r"(?i)proprietary|fiduciary|statement of net", head):
            bs = i
        elif bs is not None and st is None and TITLE_ST.search(head) and "BUDGET" not in head.upper():
            st = i
    if bs is None or st is None:
        raise SystemExit(f"FY{fy}: fund statements not found (balance sheet page {bs}, changes statement page {st})")
    return {"balance_sheet": (bs, st - 1), "changes_in_fund_balance": (st, st)}


def get_pages(fy, prep, first, last):
    if prep == "text":
        with pdfplumber.open(audit_pdf(fy)) as d:
            return [(p, d.pages[p - 1].extract_text() or "") for p in range(first, last + 1)]
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    return [(p, ocr_page(audit_pdf(fy), p, outdir, prep)) for p in range(first, last + 1)]


def tidy(line):
    """clean() plus the text layer's habit of splitting a number after its first digit ("3 7,144,784", "6 ,015,628")."""
    line = re.sub(r"(?i)\bMAY 1\b", "MAY ONE", line)       # "MAY 1" / "APRIL 30" are labels, not numbers
    line = re.sub(r"(?i)\bAPRIL 30\b", "APRIL THIRTY", line)
    line = clean(line)
    line = re.sub(r"\(\s+(?=\d)", "(", line)
    line = re.sub(r"(?<=[\d-]) [:.·;~=](?= |$)", " -", line)  # a lone mark after a number or dash stands for a blank cell
    for _ in range(2):
        line = re.sub(r"(?<![\d,.])(\d) (?=0\d\b)", r"\1", line)  # "2 08" -> "208"
        line = re.sub(r"(?<![\d,.])(\d) (?=\d\b)(?! ?\d,)", r"\1", line)  # "7 4" -> "74"
        line = re.sub(r"(?<![\d,.])(\d) (?=,\d{3}\b)", r"\1", line)
        line = re.sub(r"(?<![\d,.])(\d) (?=\d{1,2},\d{3}\b)", r"\1", line)
    return re.sub(r"\s+", " ", line).strip()


FURNITURE = re.compile(r"(?i)^(city of belvidere|balance sheet|statement of|governmental funds|for the fiscal year|april ?\d+,? ?\d{4}|april ?30|for the year|year ended|see accompanying|the notes to|"
                       r"- ?\d+ ?-?$|\d+$|general$|nonmajor|totals?$|capital$|motor|fuel tax|projects|funds|governmental)")
TOP = re.compile(r"(?i)^(assets|liabilities|deferred inflows|fund balances?|revenues|expenditures|other financing)")
SUB = re.compile(r"(?i)^(nonspendable|restricted|committed|assigned|unassigned|current|debt service)")


def parse(pages, fixline=None):
    """rows: dict(section, sub, label, vals, page) in order. Parsing stops at the reconciliation that follows the balance sheet."""
    rows, section, sub, pend = [], "", "", ""
    for pg, text in pages:
        for raw in text.splitlines():
            line = tidy(raw)
            if fixline and line in fixline:
                line = fixline[line]
            if re.match(r"(?i)^reconciliation", line):
                return rows
            if not line or re.match(r"(?i)^april ?\d+,? ?\d{4}$", line) or (FURNITURE.match(line) and not re.search(r"\d{1,3},\d{3}", line)):
                continue
            label, vals = split_numbers(line)
            if re.match(r"(?i)^april (30|thirty),?$", label) and len(vals) == 1:
                continue  # the statement's date line ("April 30, 2026")
            if not vals:
                if TOP.match(label):
                    section, sub, pend = label.title(), "", ""
                elif SUB.match(label):
                    sub, pend = label.title(), ""
                elif re.match(r"(?i)^(total|excess|net change)", label):
                    pend = label  # a wrapped label whose numbers come on the next line
                else:
                    pend = ""
                continue
            if len(vals) <= 2 and not pend and not re.search(r"[A-Za-z]{4,}", label):
                continue  # scan noise from a ruled line ("EO OP NY 840"): no real word and too few numbers to be a table row
            if pend:
                label, pend = f"{pend} {label}".strip(), ""
            rows.append(dict(section=section, sub=sub, label=label.replace("MAY ONE", "MAY 1").replace("APRIL THIRTY", "APRIL 30"), vals=vals, page=pg))
            if re.match(r"(?i)^(total|excess|net change)", label):
                sub = ""
    return rows


def fund_names(pages, ncols):
    lines = pages[0][1].splitlines()
    stop = next((i for i, ln in enumerate(lines) if re.match(r"(?i)^(assets|revenues)\b", ln.strip()) and not re.search(r"(?i)expenditures", ln)), len(lines))
    head = " ".join(lines[:stop])
    names = ["General"]
    if re.search(r"(?i)motor", head) and ncols >= 5:
        names.append("Motor Fuel Tax")
    if re.search(r"(?i)capital", head) and len(names) + 2 < ncols + 1 and ncols >= 4:
        names.append("Capital Projects")
    names += ["Nonmajor governmental funds", "Total governmental funds"]
    return names


def repair(rows, ncols, problems):
    """Rows with a digit split off ("5 28") are re-joined where that makes the Total column equal the sum of the fund columns."""
    for r in rows:
        v = r["vals"]
        if len(v) == ncols + 1:
            for k in range(len(v) - 1):
                cand = v[:k] + [int(f"{abs(v[k])}{abs(v[k + 1])}") * (-1 if v[k] < 0 else 1)] + v[k + 2:]
                if cand[-1] == sum(cand[:-1]):
                    r["vals"], r["flag"] = cand, "digits_rejoined"
                    break
        if len(r["vals"]) != ncols:
            problems.append(f"{r['label']!r}: {len(r['vals'])} values, expected {ncols}: {r['vals']}")


def validate(kind, rows, ncols):
    problems = []
    repair(rows, ncols, problems)
    rows = [r for r in rows if len(r["vals"]) == ncols]
    for r in rows:  # the Total column is the sum of the fund columns on every line
        d = r["vals"][-1] - sum(r["vals"][:-1])
        if d and ALLOW.get(f"{kind}|{r['label']}|row") == d:
            continue
        if d:
            problems.append(f"{r['label']!r}: total column {r['vals'][-1]:,} vs funds {sum(r['vals'][:-1]):,}")
    def vec(f):
        return [sum(r["vals"][c] for r in f) for c in range(ncols)]
    def diff(label, got, want):
        if got != want:
            d = [w - g for g, w in zip(got, want)]
            if not any(d[:-1]) and ALLOW.get(f"{kind}|{label}") == d[-1]:
                return  # the source's own $1 rounding in the Total column
            problems.append(f"{label}: lines {got} vs printed {want}")
    if kind == "balance_sheet":
        items, totals = [], []
        for r in rows:
            if re.match(r"(?i)^total", r["label"]):
                ok = vec(items) == r["vals"] if items else False
                if not ok:  # a total of earlier totals, plus any lines printed since (old audits list the deferred inflow with no total of its own)
                    for k in (1, 2, 3):
                        base = vec(items) if items else [0] * ncols
                        if len(totals) >= k and [sum(t["vals"][c] for t in totals[-k:]) + base[c] for c in range(ncols)] == r["vals"]:
                            ok = True
                            break
                if not ok:
                    problems.append(f"{r['label']!r} (p{r['page']}): printed {r['vals']} vs items {vec(items) if items else None}")
                totals.append(r)
                items = []
            else:
                items.append(r)
        ta = next((r for r in rows if re.match(r"(?i)^total assets", r["label"])), None)
        last = next((r for r in reversed(rows) if re.match(r"(?i)^total liabilities.*fund balances", r["label"])), None)
        if ta is None or last is None:
            problems.append("TOTAL ASSETS or TOTAL LIABILITIES, DEFERRED INFLOWS AND FUND BALANCES not found")
        else:
            diff("balance sheet balances (total assets vs liabilities + deferred inflows + fund balances)", ta["vals"], last["vals"])
    else:
        def find(pat):
            return next((r for r in rows if re.match(pat, r["label"], re.I)), None)
        tr, te = find(r"total revenues?"), find(r"total expenditures?")
        ex, tof = find(r"excess"), find(r"total (other )?financing")
        if tof is None and ex is not None:  # FY2013-FY2015: the other financing total is an unlabeled line after the last transfer line
            after = rows[rows.index(ex) + 1:]
            tof = next((r for r in after if r["label"] == ""), None)
        nc = find(r"net change")
        beg = find(r"(fund balances?\b(?!.*restated).*(may 1|beginning))|beginning$")
        end = find(r"(fund balances?\b.*(april|ending))|ending$")
        if None in (tr, te, ex, nc, beg, end):
            problems.append("a key line of the statement was not found: " + str([n for n, x in (("total revenues", tr), ("total expenditures", te), ("excess", ex), ("net change", nc), ("beginning", beg), ("ending", end)) if x is None]))
            return rows, problems
        i_tr, i_te = rows.index(tr), rows.index(te)
        diff("total revenues", vec(rows[:i_tr]), tr["vals"])
        diff("total expenditures", vec([r for r in rows[i_tr + 1:i_te]]), te["vals"])
        diff("excess of revenues over expenditures", [tr["vals"][c] - te["vals"][c] for c in range(ncols)], ex["vals"])
        if tof is not None:
            diff("total other financing", vec(rows[rows.index(ex) + 1:rows.index(tof)]), tof["vals"])
        t2 = tof["vals"] if tof is not None else [0] * ncols
        diff("net change in fund balances", [ex["vals"][c] + t2[c] for c in range(ncols)], nc["vals"])
        adj, restated = find(r"prior period adjustment"), find(r".*restated")
        if restated is not None:  # a prior period adjustment restates the beginning balance
            diff("restated beginning balance", [beg["vals"][c] + (adj["vals"][c] if adj else 0) for c in range(ncols)], restated["vals"])
            beg = restated
        diff("ending fund balance", [beg["vals"][c] + nc["vals"][c] for c in range(ncols)], end["vals"])
    return rows, problems


def run(fy, prep):
    loc = locate(int(fy))
    ov = load_overrides(fy)
    out, problems = {}, []
    for kind, (a, b) in loc.items():
        pages = get_pages(fy, prep, a, b)
        rows = parse(pages, ov)
        counts = Counter(len(r["vals"]) for r in rows if len(r["vals"]) >= 3)
        if not counts:
            problems.append(f"{kind}: no table rows read")
            out[kind] = ([], [])
            continue
        ncols = counts.most_common(1)[0][0]
        names = fund_names(pages, ncols)
        if len(names) != ncols:
            problems.append(f"{kind}: {ncols} columns but fund names {names}")
        rows, pr = validate(kind, rows, ncols)
        problems += [f"{kind}: {p}" for p in pr]
        out[kind] = (rows, names)
    return dict(prep=prep, loc=loc, out=out, problems=problems)


ALLOW = {}  # (statement, check label) -> allowed difference (the source's own cents rounding), set per run from the overrides file


def load_overrides(fy):
    path = os.path.join(DATA_E, "overrides", f"audit_fund_FY{fy}.csv")
    ov = {}
    ALLOW.clear()
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "fixline":
                ov[o["key"]] = o["value"]
            elif o["kind"] == "allow_diff":  # key "statement|label": the printed total differs from its lines by this much
                ALLOW[o["key"]] = int(o["value"])
    return ov


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    results = []
    for p in (["text", "gray"] if arg == "auto" else [arg]):
        try:
            r = run(fy, p)
        except SystemExit as e:
            print(f"prep {p}: {e}")
            continue
        results.append(r)
        print(f"prep {p}: {len(r['problems'])} problems, " + ", ".join(f"{k} {len(v[0])} lines" for k, v in r["out"].items()))
    res = min(results, key=lambda r: (not any(v[0] for v in r["out"].values()), len(r["problems"])))
    path = os.path.join(DATA_E, "extracted", "audits", f"FY{fy}_audit_fund_statements.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "statement", "section", "subsection", "line_item", "fund", "amount", "flag"])
        for kind, (rows, names) in res["out"].items():
            for r in rows:
                for fund, v in zip(names, r["vals"]):
                    w.writerow([fy, f"FY{fy}_audit", r["page"], kind, r["section"], r["sub"], r["label"], fund, v, r.get("flag", "")])
    print(f"\nchosen prep: {res['prep']} -> {os.path.relpath(path, ROOT)}; {len(res['problems'])} problems")
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
