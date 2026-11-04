"""Read the combining balance sheet and the combining statement of revenues, expenditures and changes in fund balances of the nonmajor governmental funds.

Layouts: the FY2015-FY2020 audits print one labelled page per four or five funds; FY2021-FY2025 alternate a labelled page (labels and the first four to six funds) with
unlabelled pages (ten fund columns whose rows are those of the labelled page above) and end with a page that carries the Total Nonmajor Governmental Funds column.  Fund
names are stacked words in the header ('Public' / 'Safety' / 'Sales Tax'); every word is assigned to the column whose figures it sits over.
Output data/boone_county_il/extracted/audits/<doc>_combining.csv: doc_id, pdf_page, statement, fund, row_seq, label, amount; and <doc>_combining_checks.csv.
Checks: the balance sheet and statement of changes identities of extract_audit_fund_statements.py for every fund column, the Total column equals the sum of the fund
columns on every row, and the Total column equals the Nonmajor column of the governmental funds statements (data/.../<doc>_fund_statements.csv).
Overrides: data/boone_county_il/overrides/audit_combining_<doc>.csv (check, reason).
Usage: python3 pipelines/boone_county_il/extract_audit_combining.py FY2025 [...]
"""
import csv, re, subprocess, sys
from pathlib import Path

from extract_audit_fund_statements import check_statement, parse_line

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sources/boone_county_il/audits"
OUT = ROOT / "data/boone_county_il/extracted/audits"
PAGE_NO = re.compile(r"^\s*-+\s*\d+\s*-+\s*$")
GROUPS = re.compile(r"(?i)^(special revenue|capital projects|debt service|ASSETS)$")


def statement_pages(pages):
    """-> {'balance_sheet': [pages], 'changes': [pages]}: the contiguous pages of each combining statement of the nonmajor funds."""
    out = {"balance_sheet": [], "changes": []}
    current = None
    for i, p in enumerate(pages, 1):
        U = re.sub(r"\s+", " ", p.upper())[:360]
        if "TABLE OF CONTENTS" in U[:200]:
            continue
        if "BOONE COUNTY" in U[:60] or re.match(r"[A-Z ]*COMBINING", U[:60]):
            if "COMBINING BALANCE SHEET" in U and "NONMAJOR" in U and "SUBFUND" not in U:
                current = "balance_sheet"
            elif "COMBINING STATEMENT OF REVENUES" in U and "NONMAJOR" in U and "SUBFUND" not in U:
                current = "changes"
            else:
                current = None
        elif current and (re.match(r"\s*(SPECIAL REVENUE|CAPITAL PROJECTS|DEBT SERVICE|[A-Z/' ]+\s{2,})", p.lstrip()[:200].upper()) and len(re.findall(r"\d,\d{3}|\$", p)) >= 20
                          and "BOONE COUNTY" not in U[:200]):
            pass  # an unlabelled continuation page keeps the current statement
        else:
            current = None
        if current:
            out[current].append(i)
    return out


def sheet_of(page, labelled):
    """-> dict(rows=[(label, prev, vals)], edges=[...], names=[...]) for one page."""
    lines = page.split("\n")
    parsed = []
    for line in lines:
        label, vals = parse_line(line)
        parsed.append((line, label, vals))
    rows = [(l, lab, vals) for l, lab, vals in parsed if len(vals) >= 2 and not PAGE_NO.match(l)]
    if not rows:
        return None
    if not rows:
        return None
    first_idx = next(i for i, (l, lab, vals) in enumerate(parsed) if len(vals) >= 2 and not PAGE_NO.match(l))
    top = max(rows, key=lambda r: len(r[2]))
    edges = [e for _, e in top[2]]
    n = len(edges)
    # header words: chunks (split on 3+ spaces) of the lines above the first data row, centred over a column
    names = [[] for _ in range(n)]
    hdr_lines = [l for l in lines[:first_idx] if l.strip()]
    for h in hdr_lines:
        if re.search(r"(?i)boone county|combining|nonmajor governmental funds$|november 30|^\s*assets\s*$|liabilities, deferred|revenues|inflows|continued", h):
            continue
        for m in re.finditer(r"\S+(?: \S+)*", h):
            chunk = m.group()
            if GROUPS.match(chunk) or re.match(r"(?i)(special revenue|capital projects|debt service)\b", chunk) and len(chunk.split()) <= 2:
                continue
            c = (m.start() + m.end()) / 2
            # a column's header is centred over the column, not over the right edge of its figures
            centres = [edges[k] - ((edges[k] - edges[k - 1]) if k else (edges[1] - edges[0]) if n > 1 else 10) / 2 for k in range(n)]
            j = min(range(n), key=lambda k: abs(centres[k] - c))
            names[j].append(chunk)
    sheet_rows = []
    prev = ""
    for line, label, vals in parsed[first_idx - 3 if first_idx >= 3 else 0:]:
        if n >= 6 and len(vals) <= 2 and not label.strip():
            continue  # a stray fragment line of dashes (the text layer of the FY2025 page 123 splits one row in two)
        if len(vals) >= 2 and not PAGE_NO.match(line):
            if len(vals) != n:
                cells = [0] * n
                for v, e in vals:
                    cells[min(range(n), key=lambda k: abs(edges[k] - e))] = v
                v = cells
            else:
                v = [x for x, _ in vals]
            sheet_rows.append((re.sub(r"\s+", " ", label).strip() if labelled else "", prev if labelled else "", v))
            prev = ""
        elif labelled and line.strip() and not re.search(r"(?i)see accompanying|boone county|combining|^\s*-?\s*\d+\s*-?\s*$|^\s*november|nonmajor", line):
            prev = (prev + " " + label.strip()).strip() if line.strip().isupper() else line.strip()
    return dict(rows=sheet_rows, names=[" ".join(x) for x in names], n=n)


# header words of neighbouring columns that run into each other (a word centred between two columns)
NAME_FIXES = {"Probation Replacement Services": "Probation Services", "Public Defender": "Public Defender Automation",
              "Law Enforcement Automation Operations": "Law Enforcement Operations"}
READ_LAYOUTS = {"FY2016", "FY2024", "FY2025"}  # FY2015 and FY2017 print rows that differ page to page; FY2018-FY2023 put the Total column first, name funds under 'Special Revenue Funds'
# and have no total-deferred-inflows line: their funds' ending balances are in the per-fund schedules (extract_audit_fund_schedules.py)


def main(fy):
    doc = f"{fy}_audit"
    if fy not in READ_LAYOUTS:
        print(f"{doc}: layout not read")
        return
    text = subprocess.run(["pdftotext", "-layout", str(SRC / f"{doc}.pdf"), "-"], capture_output=True, text=True, check=True).stdout
    pages = text.split("\f")
    sp = statement_pages(pages)
    out, checks = [], []
    stm_map = {"balance_sheet": "combining_balance_sheet", "changes": "combining_changes_in_fund_balance"}
    for kind, pns in sp.items():
        if not pns:
            checks.append((f"{kind}: pages found", False, ""))
            continue
        master, cols = None, []  # master = labelled rows of the current labelled page
        sheets = []
        for pn in pns:
            labelled = "BOONE COUNTY" in re.sub(r"\s+", " ", pages[pn - 1].upper())[:80]
            sh = sheet_of(pages[pn - 1], labelled)
            if sh is None:
                checks.append((f"{kind} p{pn}: figures found", False, ""))
                continue
            sh["pn"], sh["labelled"] = pn, labelled
            sheets.append(sh)
        # labels of every sheet: its own if labelled, else those of the last labelled sheet with the same number of rows
        last = None
        for sh in sheets:
            if sh["labelled"]:
                last = sh
            elif last is None or len(last["rows"]) != len(sh["rows"]):
                checks.append((f"{kind} p{sh['pn']}: rows match the labelled page above", False, f"{len(sh['rows'])} vs {len(last['rows']) if last else 0}"))
                continue
            else:
                sh["rows"] = [(lab, prev, v) for (lab, prev, _), (_, _, v) in zip(last["rows"], sh["rows"])]
            # master rows: the first labelled sheet defines the row list; later sheets must have the same labels
        base = next((s for s in sheets if s["labelled"]), None)
        if not base:
            checks.append((f"{kind}: labelled page", False, ""))
            continue
        # master rows: the labelled pages' rows in order; a page whose fund has no 'Restricted for ...' line, say, is aligned by label (and occurrence)
        def keyed(rows):
            seen, out_ = {}, []
            for lab, prev, v in rows:
                k = re.sub(r"[^a-z]", "", re.sub(r"\(.*?\)", "", lab).lower()) or re.sub(r"[^a-z]", "", prev.lower())
                seen[k] = seen.get(k, 0) + 1
                out_.append(((k, seen[k]), lab, prev, v))
            return out_
        order, meta = [], {}
        for sh in sheets:
            if sh["labelled"]:
                for k, lab, prev, _ in keyed(sh["rows"]):
                    if k not in meta:
                        meta[k] = (lab, prev)
                        order.append(k)
        full = [dict(label=meta[k][0], prev_text=meta[k][1], vals=[], key=k) for k in order]
        nrows = len(order)
        names = []
        last_lab = None
        for sh in sheets:
            if sh["labelled"]:
                last_lab = {k: v for k, _, _, v in keyed(sh["rows"])}
                cells = last_lab
            else:
                if last_lab is None or len(sh["rows"]) != len(last_lab):
                    checks.append((f"{kind} p{sh['pn']}: row count {len(sh['rows'])} vs {len(last_lab) if last_lab else 0}", False, ""))
                    continue
                cells = dict(zip(last_lab.keys(), (r[2] for r in sh["rows"])))
            names += [(sh["pn"], NAME_FIXES.get(nm, nm)) for nm in sh["names"]]
            for f_ in full:
                f_["vals"] += cells.get(f_["key"], [0] * sh["n"])
        width = len(names)
        total_name = names[-1][1]
        for r in full:
            r["vals"] = r["vals"][:width]
        # identities per fund column (reuse the governmental funds statement checks on one column at a time) and the Total column
        nfail = 0
        for j, (pn, nm) in enumerate(names):
            col_rows = [dict(label=r["label"], prev_text=r["prev_text"], vals=[r["vals"][j]]) for r in full]
            sub = check_statement("balance_sheet" if kind == "balance_sheet" else "changes", col_rows, 1)
            for c, ok, d in sub:
                if not ok:
                    nfail += 1
                    checks.append((f"{kind} p{pn} {nm or j}: {c}", ok, d))
        checks.append((f"{kind}: statement identities hold in every one of the {width} columns", nfail == 0, ""))
        for i, r in enumerate(full):
            if sum(r["vals"][:-1]) != r["vals"][-1]:
                checks.append((f"{kind} row '{r['label'][:40]}': Total column = sum of the funds", False, f"{sum(r['vals'][:-1])} vs {r['vals'][-1]}"))
        checks.append((f"{kind}: {width} columns x {nrows} rows read; last column '{total_name}'", True, ""))
        for j, (pn, nm) in enumerate(names):
            for seq, r in enumerate(full, 1):
                out.append(dict(doc_id=doc, pdf_page=pn, statement=stm_map[kind], fund=nm, row_seq=seq, label=(r["prev_text"] + " " + r["label"]).strip(), amount=r["vals"][j]))
        # the Total column equals the Nonmajor column of the governmental funds statement
        stm = OUT / f"{doc}_fund_statements.csv"
        if stm.exists():
            gov = {}
            for r in csv.DictReader(open(stm, newline="")):
                if r["statement"] == kind and re.search(r"(?i)nonmajor|other governmental", r["col"]):
                    gov[(r["prev_text"] + " " + r["label"]).strip().lower()] = int(r["amount"])
            tied = tot_checked = 0
            for r in full:
                key = (r["prev_text"] + " " + r["label"]).strip().lower()
                if key in gov and re.match(r"(?i)total|net change|excess", key):
                    tot_checked += 1
                    if gov[key] == r["vals"][-1]:
                        tied += 1
                    else:
                        checks.append((f"{kind} '{key[:40]}': Total Nonmajor = governmental funds statement", False, f"{r['vals'][-1]} vs {gov[key]}"))
            checks.append((f"{kind}: {tied} of {tot_checked} total lines equal the governmental funds statement", tied == tot_checked and tot_checked > 0, ""))
    ovr = ROOT / "data/boone_county_il/overrides" / f"audit_combining_{doc}.csv"
    if ovr.exists():
        for o in csv.DictReader(open(ovr, newline="")):
            for i, (c, ok, d) in enumerate(checks):
                if not ok and o["check"] in c:
                    checks[i] = (c, True, f"ACCEPTED ({o['reason']}) {d}")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"{doc}_combining.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "pdf_page", "statement", "fund", "row_seq", "label", "amount"])
        w.writeheader()
        w.writerows(out)
    with open(OUT / f"{doc}_combining_checks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["check", "ok", "detail"])
        w.writerows(checks)
    bad = [c for c in checks if not c[1]]
    print(f"{doc}: pages {sp['balance_sheet'][:1]}..{sp['balance_sheet'][-1:]} / {sp['changes'][:1]}..{sp['changes'][-1:]}, {len(out)} cells, {len(checks)} checks, {len(bad)} failed")
    for c in bad[:20]:
        print("   FAIL", c[0], c[2])


if __name__ == "__main__":
    for a in sys.argv[1:]:
        main(a)
