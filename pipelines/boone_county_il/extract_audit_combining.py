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
        h = re.sub(r"^(\s*)(?:Year Ended )?(?:November|December) \d{1,2},? \d{4}", lambda m_: " " * len(m_.group()), h)  # FY2021-23: the date shares a line with the last row of column names
        if re.search(r"(?i)boone county|combining|nonmajor governmental funds$|november 30|^\s*assets\s*$|liabilities, deferred|revenues|inflows|continued", h) or not h.strip():
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
    for line, label, vals in parsed[first_idx:]:
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
              "Law Enforcement Automation Operations": "Law Enforcement Operations",
              # FY2021-23: wrapped names of neighbouring columns read in the wrong order
              "IMRF/Retirement Immunity": "IMRF/Retirement", "Tort": "Tort Immunity", "Automation": "Vital Records Automation",
              "Regional Vital Records Communication Study": "Regional Communication Study", "Marriage and Prairie Shield Grant": "Prairie Shield Grant",
              "Civil Union": "Marriage and Civil Union",
              "Regional Communicatio n Study": "Regional Communication Study", "Electronic Monitoring Fund": "Electronic Monitoring", "tronic Monitoring": "Electronic Monitoring",
              "iff K9 Donation": "Sheriff K9 Donation", "ecrest Memorial Funds": "Maplecrest Memorial", "Maplecrest Memorial Funds": "Maplecrest Memorial", "Circuit Clerk E- Citation": "Circuit Clerk E-Citation"}
LEGACY = {2018: True, 2019: True, 2020: True, 2021: True, 2022: True, 2023: True}  # no 'Total deferred inflows' line; fund balances by classification only


TITLE_WORDS = re.compile(r"^(?:(?:Nonmajor Government(?:al)? Funds|Special Revenue Funds|Debt Service|Capital Projects(?= \S)|\(Continued\))\s+)+", re.I)


def clean_name(nm):
    """Drop title/group words that the header reading picked up above the column names (FY2018-FY2020)."""
    return TITLE_WORDS.sub("", nm).strip()


def check_legacy_bs(rows):
    """Balance sheet identities for the FY2018-FY2023 layout: assets add up; liabilities add up; total liabilities and deferred inflows = liabilities + the deferred line(s);
    the fund balance lines add to the total; assets = liabilities and deferred inflows + fund balances."""
    out = []
    v = lambda i: rows[i]["vals"][0]  # noqa: E731
    lab = [re.sub(r"[^a-z ]", "", (r["prev_text"] + " " + r["label"]).lower()).strip() for r in rows]

    def idx(rx, start=0):
        return next((i for i in range(start, len(lab)) if re.search(rx, lab[i])), None)
    ta = idx(r"^total assets")
    tl = idx(r"^total liabilities$")
    tld = idx(r"^total liabilities and")
    tfb = idx(r"^total fund balance")
    tt = idx(r"^total liabilities, deferred inflows .* and fund balances|^total liabilities deferred inflows .*fund balances", (tld or 0) + 1) if tld is not None else None
    if None in (ta, tl, tld, tfb):
        return [("legacy balance sheet: total lines found", False, f"{ta} {tl} {tld} {tfb}")]
    out.append(("assets add to the total", sum(v(i) for i in range(0, ta)) == v(ta), ""))
    out.append(("liabilities add to the total", sum(v(i) for i in range(ta + 1, tl)) == v(tl), ""))
    dfd = sum(v(i) for i in range(tl + 1, tld))
    tld_v = v(tld) if (v(tld) or dfd) else v(tl)  # a fund with no deferred inflows prints no 'Total liabilities and deferred inflows' figure
    note = ""
    if v(tl) and dfd and tld_v == dfd:  # FY2021-22: the line holds the deferred-inflows subtotal (the label is the source's); the true total is liabilities + deferred
        tld_v, note = v(tl) + dfd, f" (source prints the deferred-inflows subtotal {dfd} on this line)"
    out.append(("total liabilities and deferred inflows = liabilities + deferred inflows", v(tl) + dfd == tld_v, f"{v(tl)} + {dfd} vs {tld_v}{note}"))
    out.append(("fund balance lines add to the total", sum(v(i) for i in range(tld + 1, tfb)) == v(tfb), ""))
    out.append(("assets = liabilities and deferred inflows + fund balances", v(ta) == tld_v + v(tfb), f"{v(ta)} vs {tld_v + v(tfb)}"))
    return out


READ_LAYOUTS = {"FY2016", "FY2021", "FY2022", "FY2023", "FY2024", "FY2025"}  # FY2015 and FY2017 print rows that differ page to page; FY2018-FY2020 (Total column first, funds named under group headings, statements of changes whose rows differ page to page) are not read yet
# and have no total-deferred-inflows line: their funds' ending balances are in the per-fund schedules (extract_audit_fund_schedules.py)


def main(fy):
    doc = f"{fy}_audit"
    fy_int = int(fy[2:])
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
        # master rows: the first labelled page's rows in order; a later labelled page is aligned by label (a wrapped label may be split differently from page to page, so a
        # row matches on its full text, its own text or its first line, with the occurrence number of that text); a fund with no 'Restricted for ...' line, say, gets zero
        def norm_(x):
            k = re.sub(r"[^a-z]", "", re.sub(r"\(.*?\)", "", x).lower())
            return "cashdepositsandinvestments" if k.startswith("cash") else k  # 'Cash and cash equivalents' / 'Cash, deposits and investments'


        def cand(rows):
            seen, out_ = {}, []
            for lab, prev, v in rows:
                ks = []
                for t in ((prev + " " + lab), lab, prev):
                    t = norm_(t)
                    if t and t not in ks:
                        ks.append(t)
                occ = tuple((k, seen.setdefault(k, 0) + 1) for k in ks)
                for k in ks:
                    seen[k] += 1
                out_.append((occ, lab, prev, v))
            return out_
        full, index = [], {}
        names = []
        last_rows = None
        for sh in sheets:
            if sh["labelled"]:
                last_rows = cand(sh["rows"])
                assigned, prev_obj = [], None
                for occ, lab, prev, v in last_rows:
                    obj = next((index[k] for k in occ if k in index), None)
                    if obj is None:
                        obj = dict(label=lab, prev_text=prev, vals=[0] * len(names))
                        full.insert((full.index(prev_obj) + 1) if prev_obj is not None else 0, obj)  # a row only this page prints goes after the row above it
                        for k in occ:
                            index[k] = obj
                    assigned.append((obj, v))
                    prev_obj = obj
                last_assign = [o for o, _ in assigned]
                col_vals = {id(o): v for o, v in assigned}
            else:
                if last_rows is None or len(sh["rows"]) != len(last_assign):
                    checks.append((f"{kind} p{sh['pn']}: row count {len(sh['rows'])} vs {len(last_assign) if last_rows else 0}", False, ""))
                    continue
                col_vals = {id(o): r[2] for o, r in zip(last_assign, sh["rows"])}
            names += [(sh["pn"], NAME_FIXES.get(nm, nm)) for nm in (clean_name(x) for x in sh["names"])]
            for f_ in full:
                f_["vals"] += col_vals.get(id(f_), [0] * sh["n"])
        if names and re.search(r"(?i)^total", names[0][1]) and not re.search(r"(?i)^total", names[-1][1]):  # FY2018-FY2020 print the Total column first
            names = names[1:] + names[:1]
            for f_ in full:
                f_["vals"] = f_["vals"][1:] + f_["vals"][:1]
        nrows = len(full)
        width = len(names)
        total_name = names[-1][1]
        for r in full:
            r["vals"] = r["vals"][:width]
        # identities per fund column (reuse the governmental funds statement checks on one column at a time) and the Total column
        nfail = 0
        for j, (pn, nm) in enumerate(names):
            col_rows = [dict(label=r["label"], prev_text=r["prev_text"], vals=[r["vals"][j]]) for r in full]
            sub = check_legacy_bs(col_rows) if (kind == "balance_sheet" and LEGACY.get(int(fy[2:]))) else check_statement("balance_sheet" if kind == "balance_sheet" else "changes", col_rows, 1)
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
