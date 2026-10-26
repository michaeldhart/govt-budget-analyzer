"""Extract the audit's fund-level detail for the non-General governmental funds.

Usage: python3 pipelines/belvidere_il/extract_audit_other_funds.py FY2020 [--source auto|text|gray|thr150]

Two outputs per audit (data/belvidere_il/extracted/audits/):
  FY20XX_audit_fund_schedules.csv  "Schedule of Revenues, Expenditures and Changes in Fund Balance - Budget and Actual" of every
      governmental fund other than the General Fund (Motor Fuel Tax, Kishwaukee TIF, TIF #2, 1990/1993 TIF, Farmington SSA #2/#3,
      Foreign Fire Insurance, Capital Projects ...): one row per line with original budget, final budget, actual.
  FY20XX_audit_fund_combining.csv  the Combining Balance Sheet and the Combining Statement of Revenues, Expenditures and Changes
      in Fund Balances of the nonmajor governmental funds: one row per line x fund column (+ a total column).

Reading is positional (see audit_other_funds_reader.py): every number is assigned to a table column by its right edge, so
cells the audit leaves blank are blank, not shifted. Validation (each file/year must pass; the printed totals are the judge):
  schedules: revenue items = Total revenues; expenditure items = Total expenditures; excess = revenues - expenditures;
             other financing items = Total other financing; net change = excess + other financing; ending fund balance =
             beginning (restated) + net change (actual column).
  combining: every Total line = its items; balance sheet balances; the Total column = the sum of the fund columns on every
             line; excess, net change, ending balance identities (same validator as extract_audit_fund_statements.py).
Eyeballed corrections: data/belvidere_il/overrides/audit_other_funds_FY20XX.csv (kinds: cell, allow_diff, drop_page_line).
"""
import csv
import os
import re
import sys
from collections import Counter

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import DATA_E, ROOT, audit_pdf, page_texts_for_locating  # noqa: E402
import extract_audit_fund_statements as fs  # noqa: E402
from audit_other_funds_reader import page_lines, split_line  # noqa: E402

OUT_DIR = os.path.join(DATA_E, "extracted", "audits")
COLS = ["original_budget", "final_budget", "actual", "variance", "prior_year_actual"]  # FY2012 prints all five; later audits the first three
EXPECT_SCHED = {"Capital Projects", "Motor Fuel Tax", "Kishwaukee TIF"}  # funds that must have a schedule every year they exist (informational)


# ---- fund names ---------------------------------------------------------------------------------
KNOWN = {"Motor Fuel Tax", "Kishwaukee TIF", "Kishwaukee TIF #2", "1990 TIF", "1993 TIF", "Farmington SSA #2", "Farmington SSA #3",
         "Farmington SSA", "Foreign Fire Insurance", "Capital Projects", "Police Restricted", "Total"}


def canon_fund(text):
    t = re.sub(r"\s+", " ", text.upper())
    if re.fullmatch(r"(TOTALS?|TOTAL NONMAJOR.*)", t.strip()):
        return "Total"
    if "MOTOR" in t:
        return "Motor Fuel Tax"
    if "KISHWAUKEE" in t or re.search(r"T\.? ?[I1l]\.? ?F", t):
        if re.search(r"#\s*2|TIF 2|\bII\b|FUND 2|\b2$", t):
            return "Kishwaukee TIF #2"
        return "Kishwaukee TIF"
    if "1990" in t:
        return "1990 TIF"
    if "1993" in t or "INCENTIVE" in t:
        return "1993 TIF"
    if "FARMINGTON" in t or "SSA" in t:
        m = re.search(r"#\s*([23])", t)
        return f"Farmington SSA #{m.group(1)}" if m else "Farmington SSA #2"  # FY2012-15 print one unnumbered Farmington fund; its balances chain into SSA #2 (FY2015 end 28,086 = FY2016 SSA #2 begin)
    if "FOREIGN" in t or "INSURANCE" in t:
        return "Foreign Fire Insurance"
    if "POLICE" in t and "RESTRICT" in t:
        return "Police Restricted"
    if "CAPITAL" in t:
        return "Capital Projects"
    return text.strip().title()


# ---- locating -----------------------------------------------------------------------------------
def head(t, n=500):
    return re.sub(r"\s+", " ", t[:n])


def norm_head(t, n=600):
    h = head(t, n).upper()
    h = re.sub(r"[^A-Z0-9#,.\- ()&'/]", " ", h)
    return re.sub(r"\s+", " ", h)


EXCLUDE = re.compile(r"GENERAL FUND|PENSION|WATER|SEWER|LIBRARY|OPERATING EXPENSES|FIREFIGHTER")


def names_from_title(h):
    """Candidate fund names out of a normalized page head: after 'BUDGET AND ACTUAL' (FY2016+) and before 'SCHEDULE OF' (FY2012-15)."""
    out = []
    m = re.search(r"BUDGET AND \S{0,2}\.?\S{0,1}CTUAL (?:\(CONTINUED\) )?(.*?)(?: FOR THE| YEAR ENDED| WITH COMPARATIVE| ORIGINAL|$)", h)
    if m:
        out.append(m.group(1).strip())
    m = re.search(r"(?:ILLINOIS|ILLTNOTS|ILLINOTS|TLLTNOTS|BELVIDERE,?)\s+(.*?)\s*-?\s*(?:SPECIAL REVENUE|CAPITAL PROJECTS)? ?(?:FUND )?(?:SCHEDULE|STATEMENT) OF", h)
    if m:
        out.append(m.group(1).strip())
    m = re.search(r"^(.*?)(?:SCHEDULE|STATEMENT) OF REVENUES", h)
    if m:
        out.append(m.group(1).strip())
    return out


def locate(fy, source):
    """-> dict(found=[(page, fund, normalized head)], bs=first page of the combining balance sheet, soc=same for the statement of changes)."""
    texts = page_texts_for_locating(fy)
    if int(fy) == 2012:
        # FY2012 is a scan whose whole-document OCR sidecar is not page-aligned (and has the sideways pages as garbage):
        # OCR the supplementary pages one by one (cached) and use their text to locate the tables
        texts = [""] * len(texts)
        for pg in range(70, 100):
            texts[pg - 1] = "\n".join(" ".join(w.text for w in ln.words) for ln in page_lines(fy, pg, "gray"))
    found, bs, soc = [], None, None
    for i, t in enumerate(texts, 1):
        if re.search(r"\.{6}", t[:1500]):
            continue  # table of contents
        h = norm_head(t)
        if "TABLE OF CONTENTS" in h:
            continue
        if re.search(r"(SCHEDULE|STATEMENT) OF REVENUES,? EXPENDITURES", h) and re.search(r"BUDGET AND", h):
            cands = names_from_title(h)
            if any(EXCLUDE.search(c) for c in cands[:2]) or re.search(r"\bGENERAL FUND\b", h[:300]):
                continue
            fund = next((canon_fund(c) for c in cands if c and canon_fund(c) in KNOWN), None)
            if fund is None:
                fund = canon_fund(cands[0]) if cands else "?"
            found.append((i, fund, h))
        elif re.search(r"COMBINING BALANCE SHEET", h) and not re.search(r"PENSION|PROPRIETARY|FIDUCIARY", h[:300]):
            if "FOLLOWING PAGE" in h or len(re.findall(r"\d", t)) < 15:
                continue
            if bs is None:
                bs = i
        elif re.search(r"COMBINING STATEMENT OF REVENUES", h) and not re.search(r"PENSION|PLAN NET|PROPRIETARY", h[:300]):
            if "FOLLOWING PAGE" in h or len(re.findall(r"\d", t)) < 15:
                continue
            if soc is None:
                soc = i
    return dict(found=found, bs=bs, soc=soc, texts=texts)


def continuation_pages(texts, first, maxn=3):
    """Pages after `first` that continue the same table (they have no title of their own)."""
    out = [first]
    for j in range(first + 1, min(first + maxn, len(texts)) + 1):
        t = texts[j - 1]
        hh = head(t, 200).upper()
        if "CITY OF BELVIDERE" in hh or "SCHEDULE OF" in hh or "COMBINING" in hh or len(re.findall(r"\d", t)) < 40:
            break
        out.append(j)
    return out


# ---- overrides ----------------------------------------------------------------------------------
OV = {}


def load_overrides(fy):
    OV.clear()
    OV.update(cell={}, allow={}, drop=set(), fixlabel={}, columns={})
    path = os.path.join(DATA_E, "overrides", f"audit_other_funds_FY{fy}.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "cell":       # key "doc|page|line label|column" -> value
                OV["cell"][o["key"]] = int(o["value"])
            elif o["kind"] == "allow_diff":  # key "doc|check label" (doc = fund or statement) -> allowed difference
                OV["allow"][o["key"]] = int(o["value"])
            elif o["kind"] == "drop_line":   # key "page|label": a scan-noise line to ignore
                OV["drop"].add(o["key"])
            elif o["kind"] == "columns":     # key "statement|first pdf page" -> fund names left to right separated by ';'
                OV["columns"][o["key"]] = o["value"].split(";")
            elif o["kind"] == "label":       # key "page|raw label" -> corrected label
                OV["fixlabel"][o["key"]] = o["value"]


# ---- table reading ------------------------------------------------------------------------------
def cluster(xs, gap=20.0):
    xs = sorted(xs)
    groups = []
    for x in xs:
        if groups and x - groups[-1][-1] <= gap:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [sum(g) / len(g) for g in groups], groups


def read_page_rows(fy, page, source):
    """-> list of dict(label, x0, top, cells=[(x1, v)], raw) for the lines of a page, header junk NOT yet removed."""
    out = []
    for ln in page_lines(fy, page, source):
        if ln.top > 740:
            continue
        label, vals = split_line(ln, ocr=source != "text")
        if not label and len(vals) <= 3 and ln.top > 560 and sum(1 for _, v in vals if v) <= 1 and all(abs(v) < 1000 for _, v in vals):
            continue  # the page number ("- 83 -") at the foot of a scan
        if f"{page}|{label}" in OV["drop"]:
            continue
        if source != "text":
            label = re.sub(r"(?:\s+[*~“”\"'°•=|«»\\]+)+$", "", label).strip()  # OCR marks after a label
            label = re.sub(r"^[^A-Za-z0-9(]+", "", label).strip()
            label = re.sub(r"^(?:[a-z]{1,2}\s+)+(?=[A-Z])", "", label)  # leading scan-noise fragments ("nw Total assets")
        label = label.rstrip(":").strip() if source != "text" else label
        label = OV["fixlabel"].get(f"{page}|{label}", label)
        out.append(dict(label=label, x0=ln.words[0].x0, top=ln.top, cells=vals, page=page, words=ln.words))
    if source != "text":  # OCR: a label-only line and a nameless line of numbers within a few points of each other are one table row
        merged, i = [], 0
        while i < len(out):
            r = out[i]
            nxt = out[i + 1] if i + 1 < len(out) else None
            if nxt is not None and abs(nxt["top"] - r["top"]) <= 7.5:
                a, b = (r, nxt) if (r["label"] and not r["cells"] and not nxt["label"] and nxt["cells"]) else (nxt, r) if (nxt["label"] and not nxt["cells"] and not r["label"] and r["cells"]) else (None, None)
                if a is not None:
                    prev_ok = not merged or abs(merged[-1]["top"] - a["top"]) > 7.5 or merged[-1]["cells"] or not merged[-1]["label"] or True
                    merged.append(dict(a, cells=b["cells"]))
                    i += 2
                    continue
            merged.append(r)
            i += 1
        out = merged
    return out


FURN = re.compile(r"(?i)^(city of belvidere|schedule of|changes in fund|nonmajor governmental|combining|for the (fiscal )?year|year ended|april ?\d+,?$|\(see|see independent|\(this schedule|original\b|budget\b|"
                  r"governmental funds$|special revenue$|variance|positive|\d{4}$|2012 2011|with comparative|statement of revenues|and changes in|expenditures,? and|- ?\d+ ?-?$)")


COMPLETE = re.compile(r"(?i)^(net change in fund balances?|total .+|excess .*over( \(under\))? expenditures|fund balances?,? ?[-\u2013]? ?(beginning|ending|april 30)|.*restated|.*previously reported|prior period adjustment|transfers? .*|.*\(out\))$")
CONTINUATION = re.compile(r"(?i)^(of |over\b|previously|restated|and\b|under\b|expenditures)")


def fill_dash_rows(rows, x):
    """OCR sources: a complete label with no cells is a row of dashes (zeros) the OCR did not keep; give it one zero cell."""
    out = []
    for i, r in enumerate(rows):
        if not r["cells"] and r["label"] and COMPLETE.match(r["label"]) and not FURN.match(r["label"]):
            nxt = next((q for q in rows[i + 1:] if q["label"]), None)
            if not (nxt and (CONTINUATION.match(nxt["label"]) or re.match(r"[a-z(]", nxt["label"]))):
                r = dict(r, cells=[(x, 0)])
        out.append(r)
    return out


# ---- schedules ----------------------------------------------------------------------------------
def kind_of(label, section):
    l = label.lower()
    if re.match(r"total revenues?", l):
        return "total_revenues"
    if re.match(r"total expenditures?", l):
        return "total_expenditures"
    if l.startswith("excess") or re.match(r"\(?deficiency\)? ?of revenues", l):
        return "excess"
    if re.match(r"total (other )?financing", l):
        return "total_ofs"
    if l.startswith("net change"):
        return "net_change"
    if re.fullmatch(r"(beginning|ending)( of (the )?year)?", l):
        return "fb_begin" if l.startswith("beginning") else "fb_end"
    if l.startswith("fund balance") and "restated" in l:
        return "fb_restated"
    if l.startswith("fund balance") and re.search(r"april|ending|end of", l):
        return "fb_end"
    if l.startswith("fund balance") and re.search(r"may|beginning|previously", l):
        return "fb_begin"
    if re.match(r"(prior period adjustment|change in accounting|restatement)", l):
        return "fb_adjust"
    if l.startswith("total") or l == "(unlabeled subtotal)":
        return "subtotal"  # a printed subtotal inside a section (e.g. Total debt service); checked against the items directly above it
    if section == "REVENUES":
        return "revenue"
    if section == "EXPENDITURES":
        return "expenditure"
    if section:
        return "other_financing"
    return "other"


def parse_schedule(fy, fund_pages, source, doc):
    NC = 5 if int(fy) == 2012 else 3
    """Rows of one fund's schedule (possibly several pages). Returns (fund_rows, problems)."""
    rows = []
    for pg in fund_pages:
        rows += read_page_rows(fy, pg, source)
    # column positions from the value tokens of the schedule body
    start = next((i for i, r in enumerate(rows) if re.match(r"(?i)^revenues:?$", r["label"]) and not r["cells"]), 0)
    xs = [x for r in rows[start:] for x, _ in r["cells"] if not FURN.match(r["label"])]
    # use only the three right-most clusters; labels like "MAY 1" were already given back to the label
    centers, groups = cluster(xs, gap=22.0)
    keep = [(c, g) for c, g in zip(centers, groups)]
    if len(keep) > NC:  # stray tokens left of the table (scan noise): the table's columns are the right-most clusters
        keep = sorted(keep, key=lambda cg: cg[0])[-NC:]
    centers = [c for c, _ in keep]
    problems = []
    if len(centers) != NC:
        problems.append(f"{doc}: found {len(centers)} value columns (expected {NC})")
        return [], problems
    section, stack, pend = None, [], ""
    out = []
    rows = [r for r in rows if r["label"] or any(v for _, v in r["cells"])]  # drop scan-noise lines (lone dashes)
    unreliable = source != "text"  # OCR drops or displaces the dashes that stand for zero cells
    if unreliable:
        rows = fill_dash_rows(rows, centers[-1])
    for ri, r in enumerate(rows):
        label, cells = r["label"], r["cells"]
        if not cells and unreliable and section and label and not FURN.match(label) \
                and not re.match(r"(?i)^(revenues|expenditures|other financing|excess|fund balance|net change|total|\(?deficiency)", label) \
                and not (pend):
            nxt = next((q for q in rows[ri + 1:] if q["label"] and not FURN.match(q["label"])), None)
            is_heading = nxt is not None and nxt["x0"] > r["x0"] + 4 and not re.match(r"(?i)^total", nxt["label"])
            if not is_heading and not r["label"].isupper():
                r = dict(r, cells=[(centers[-1], 0)])  # an item whose three cells are dashes
                cells = r["cells"]
        if not cells:
            u = label.upper()
            if not label or FURN.match(label):
                continue
            if section is None and not re.match(r"(?i)^revenues", label):
                continue
            if re.match(r"(?i)^revenues$", label):
                section, stack, pend = "REVENUES", [], ""
                continue
            if re.match(r"(?i)^expenditures$", label):
                section, stack, pend = "EXPENDITURES", [], ""
                continue
            if re.match(r"(?i)^other financing", label):
                section, stack, pend = "OTHER FINANCING", [], ""
                continue
            if re.match(r"(?i)^(excess|fund balance|net change|total|\(?deficiency)", label):
                pend = (pend + " " + label).strip()
                continue
            while stack and stack[-1][1] >= r["x0"] - 4:
                stack.pop()
            stack.append((label, r["x0"]))
            continue
        if section is None:
            continue
        full = (pend + " " + label).strip() if pend else label
        pend = ""
        if not full:
            full = "(unlabeled subtotal)"  # the audit prints some subtotals without a label
        vals = [None] * NC
        for x, v in cells:
            ci = min(range(NC), key=lambda k: abs(centers[k] - x))
            if abs(centers[ci] - x) > 24:
                problems.append(f"{doc} p{r['page']}: {full!r}: value {v} at x={x:.0f} is not in a column {centers}")
                continue
            if vals[ci] is not None:
                problems.append(f"{doc} p{r['page']}: {full!r}: two values in column {ci}")
            vals[ci] = v
        for ci in range(NC):
            k = f"{doc}|{r['page']}|{full}|{COLS[ci]}"
            if k in OV["cell"]:
                vals[ci] = OV["cell"][k]
        kind = kind_of(full, section)
        while stack and stack[-1][1] >= r["x0"] - 4:  # an item indented no deeper than a heading closes that heading
            stack.pop()
        sec_out = {"total_revenues": "REVENUES", "total_expenditures": "EXPENDITURES", "excess": "", "total_ofs": "OTHER FINANCING",
                   "net_change": "FUND BALANCE", "fb_begin": "FUND BALANCE", "fb_adjust": "FUND BALANCE", "fb_restated": "FUND BALANCE", "fb_end": "FUND BALANCE"}.get(kind, section)
        out.append(dict(kind=kind, section=sec_out, sub=" > ".join(s for s, _ in stack) if kind in ("revenue", "expenditure", "other_financing") else "",
                        label=full, vals=vals, page=r["page"], flag=""))
    return out, problems


def allow(doc, label):
    return OV["allow"].get(f"{doc}|{label}", 0)


def validate_schedule(doc, rows):
    problems, notes = [], []
    NC = len(rows[0]["vals"])
    fbcols = [2] if NC == 3 else [2, 4]  # columns that carry fund balances (actual; FY2012 also the prior-year actual)
    z = lambda v: 0 if v is None else v

    def total(kinds):
        return [sum(z(r["vals"][c]) for r in rows if r["kind"] in kinds) for c in range(NC)]

    def one(kind):
        m = [r for r in rows if r["kind"] == kind]
        return m[0] if m else None

    def check(label, got, want):
        for c in range(NC):
            if want[c] is None:
                continue
            d = want[c] - got[c]
            if d and d != allow(doc, f"{label}|{COLS[c]}"):
                problems.append(f"{doc}: {label} {COLS[c]}: computed {got[c]:,} vs printed {want[c]:,} (diff {d:,})")

    for i, r in enumerate(rows):  # printed subtotals vs the items directly above them under the same heading
        if r["kind"] != "subtotal":
            continue
        items = []
        for q in reversed(rows[:i]):
            if q["kind"] not in ("revenue", "expenditure", "other_financing") or (items and q["sub"] != items[0]["sub"]):
                break
            items.append(q)
        if items:
            check(f"subtotal {r['label']!r} vs the {len(items)} items above it", [sum(z(q["vals"][c]) for q in items) for c in range(NC)], r["vals"])
    tr, te, ex, tof, nc = (one(k) for k in ("total_revenues", "total_expenditures", "excess", "total_ofs", "net_change"))
    fb, adj, rst, fe = (one(k) for k in ("fb_begin", "fb_adjust", "fb_restated", "fb_end"))
    if nc is None or fe is None:
        problems.append(f"{doc}: net change or ending fund balance line not found")
        return problems, notes
    rev, exp = total({"revenue"}), total({"expenditure"})
    if tr is not None:
        check("revenue items vs Total revenues", rev, tr["vals"])
    elif sum(r["kind"] == "revenue" for r in rows) > 1:
        notes.append("no Total revenues line (net change identity still checks the sum)")
    if te is not None:
        check("expenditure items vs Total expenditures", exp, te["vals"])
    elif sum(r["kind"] == "expenditure" for r in rows) > 1:
        notes.append("no Total expenditures line (net change identity still checks the sum)")
    R = [z(tr["vals"][c]) if tr else rev[c] for c in range(NC)]
    E = [z(te["vals"][c]) if te else exp[c] for c in range(NC)]
    sgn = [1 if (NC == 5 and c == 3) else -1 for c in range(NC)]  # the variance column is positive when favourable: expenditures enter with + sign
    if ex is not None:
        check("excess vs revenues - expenditures", [R[c] + sgn[c] * E[c] for c in range(NC)], ex["vals"])
    ofs_items = total({"other_financing"})
    if tof is not None:
        check("other financing items vs total", ofs_items, tof["vals"])
    O = [z(tof["vals"][c]) if tof else ofs_items[c] for c in range(NC)]
    check("net change vs excess + other financing", [R[c] + sgn[c] * E[c] + O[c] for c in range(NC)], nc["vals"])
    # fund balance roll-forward (actual column only)
    if fb is not None:
        for c in fbcols:
            b = z(fb["vals"][c])
            if rst is not None:
                if adj is not None:
                    check("restated beginning vs beginning + adjustment", [b + z(adj["vals"][c]) if k == c else 0 for k in range(NC)], [rst["vals"][c] if k == c else None for k in range(NC)])
                b = z(rst["vals"][c])
            elif adj is not None:
                b += z(adj["vals"][c])
            check("ending fund balance vs beginning + net change", [b + z(nc["vals"][c]) if k == c else 0 for k in range(NC)], [fe["vals"][c] if k == c else None for k in range(NC)])
    else:
        problems.append(f"{doc}: no beginning fund balance line")
    return problems, notes


# ---- combining statements ---------------------------------------------------------------------
HEAD_STOP = re.compile(r"(?i)^(assets|revenues)\b")


def read_combining(fy, pages, source, kind, doc):
    """Join the pages of one combining statement side by side. Returns (names, rows[dict(section,sub,label,vals,page)], problems)."""
    problems = []
    per_page = []
    for pg in pages:
        rows = read_page_rows(fy, pg, source)
        # header: lines before the first ASSETS/REVENUES heading and after the title
        stop = next((i for i, r in enumerate(rows) if HEAD_STOP.match(r["label"]) and not r["cells"]), None)
        if stop is None and len(per_page) > 0:  # a continuation page has no labels: the body starts at its first row of numbers
            stop = next((i for i, r in enumerate(rows) if r["cells"] and not FURN.match(r["label"])), None)
        if stop is None:
            problems.append(f"{doc} p{pg}: no ASSETS/REVENUES heading")
            continue
        hdr, body = rows[:stop], rows[stop:]
        xs = [x for r in body for x, _ in r["cells"] if not FURN.match(r["label"])]
        centers, groups = cluster(xs, gap=22.0)
        # drop noise clusters: at most a handful of tokens far left of the dense columns
        dense = max(len(g) for g in groups) if groups else 0
        sel = [(c, g) for c, g in zip(centers, groups) if len(g) >= max(3, dense // 6)]
        centers = [c for c, _ in sel]
        # header text per column
        colhead = [[] for _ in centers]
        for r in hdr:
            if FURN.match(r["label"]) and not re.match(r"(?i)^(farmington|foreign|capital|total|motor|kishwaukee|1993|1990)", r["label"]):
                continue
            if re.match(r"(?i)^(city of belvidere|combining|nonmajor|statement of|and changes|for the|april|year)", r["label"]):
                continue
            for w in r["words"]:
                if w.text.upper() in ("SPECIAL", "REVENUE") and r["label"].lower().startswith("special revenue"):
                    continue
                cx = (w.x0 + w.x1) / 2
                ci = min(range(len(centers)), key=lambda k: abs(centers[k] - 22 - cx))
                colhead[ci].append((r["top"], w.text))
        per_page.append((pg, body, centers, [" ".join(t for _, t in sorted(h)) for h in colhead]))
    return per_page, problems


def build_combining(fy, pages, source, kind, doc):
    per_page, problems = read_combining(fy, pages, source, kind, doc)
    if not per_page:
        return [], [], problems
    names, ncols_page = [], []
    for pg, body, centers, heads in per_page:
        names += [canon_fund(h) if h else f"col{k+1}" for k, h in enumerate(heads)]
        ncols_page.append(len(centers))
    ov_cols = OV["columns"].get(f"{doc}|{pages[0]}")
    if ov_cols:
        names = ov_cols
    if len(names) != sum(ncols_page) or len(set(names)) != len(names):
        problems.append(f"{doc}: column names {names} do not match {sum(ncols_page)} value columns (add a `columns` override)")
    unreliable = source != "text"
    tol = 6.5 if unreliable else 4.5

    def clean_rows(body):
        return [r for r in body if f"{r['page']}|{r['label']}" not in OV["drop"] and (r["label"] or any(v for _, v in r["cells"]))]

    pages_rows = [clean_rows(body) for _, body, _, _ in per_page]
    used = [set() for _ in per_page]
    # vertical offset of each continuation page against the first (scans are shifted by a few points between pages)
    lt = [q["top"] for q in pages_rows[0] if q["label"] and q["x0"] < 200 and not FURN.match(q["label"])]
    offs = [0.0]
    for k in range(1, len(per_page)):
        rt = [q["top"] for q in pages_rows[k] if q["cells"]]
        best = max(((sum(1 for a in lt for b in rt if abs(b - a - d / 2) < 3.0), -abs(d)), d / 2) for d in range(-60, 61))
        offs.append(best[1])
    rows, section, sub, pend = [], "", "", ""
    left = pages_rows[0]
    if unreliable:
        left = fill_dash_rows(left, per_page[0][2][0])
    for li, r in enumerate(left):
        label, cells = r["label"], r["cells"]
        if FURN.match(label) and not cells:
            continue
        has_right = False
        if not cells and unreliable and label:
            # a label with no cells: heading, or an item whose cells are all dashes (the OCR does not keep the dashes)
            has_right = any(abs(q["top"] - offs[1] - r["top"]) <= tol and q["cells"] for q in pages_rows[1]) if len(pages_rows) > 1 else False
        if not cells and not has_right:
            heading_like = True
            if unreliable and label and not fs.TOP.match(label) and not re.match(r"(?i)^(total|excess|net change|fund balance|\(?deficiency|of resources)", label):
                nxt = next((q for q in left[li + 1:] if q["label"] and not FURN.match(q["label"])), None)
                heading_like = nxt is not None and nxt["x0"] > r["x0"] + 4 and not re.match(r"(?i)^total", nxt["label"])
                if not heading_like and not pend and not label.isupper():
                    cells = [(per_page[0][2][0], 0)]  # item with all-dash cells
            if heading_like or not cells:
                if not label:
                    continue
                if re.match(r"(?i)^fund balance,", label):
                    pend = (pend + " " + label).strip()
                elif fs.TOP.match(label):
                    section, sub, pend = label.title(), "", ""
                elif fs.SUB.match(label):
                    sub, pend = label.title(), ""
                elif re.match(r"(?i)^(total|excess|net change|fund balance|\(?deficiency|of resources)", label):
                    pend = (pend + " " + label).strip()
                else:
                    pend = ""
                continue
        full = (pend + " " + label).strip() if pend else label
        pend = ""
        vals, flag = [], ""
        for k, (pg, body, centers, _) in enumerate(per_page):
            if k == 0:
                rr = dict(cells=cells, top=r["top"])
            else:
                cand = [(abs(q["top"] - offs[k] - r["top"]), qi) for qi, q in enumerate(pages_rows[k]) if qi not in used[k] and q["cells"] and abs(q["top"] - offs[k] - r["top"]) <= tol]
                if cand:
                    qi = min(cand)[1]
                    used[k].add(qi)
                    rr = pages_rows[k][qi]
                else:
                    rr = dict(cells=[], top=r["top"])
            cols = [None] * len(centers)
            for x, v in rr["cells"]:
                ci = min(range(len(centers)), key=lambda q: abs(centers[q] - x))
                if abs(centers[ci] - x) > 24:
                    problems.append(f"{doc} p{pg}: {full!r}: value {v} at x={x:.0f} outside columns")
                    continue
                if cols[ci] is not None and cols[ci] != v:
                    problems.append(f"{doc} p{pg}: {full!r}: two values in a column")
                cols[ci] = v
            for q, c in enumerate(cols):
                key = f"{doc}|{pg}|{full}|{names[sum(ncols_page[:k]) + q]}"
                if key in OV["cell"]:
                    cols[q] = OV["cell"][key]
            if any(c is None for c in cols) and not unreliable:
                flag = "blank_cell_as_zero"
            vals += [0 if c is None else c for c in cols]
        rows.append(dict(section=section, sub=sub, label=full.replace("MAY ONE", "MAY 1"), vals=vals, page=r["page"], flag=flag, top=r["top"]))
        if re.match(r"(?i)^(total|excess|net change)", full):
            sub = ""
    # right-page rows with values but no label row on the first page: the audit prints some subtotals without a label
    extras = []
    for k in range(1, len(per_page)):
        for qi, q in enumerate(pages_rows[k]):
            if qi in used[k] or not any(v for _, v in q["cells"]):
                continue
            top = q["top"] - offs[k]
            if any(abs(e["top"] - top) <= tol for e in extras):
                e = next(e for e in extras if abs(e["top"] - top) <= tol)
            else:
                e = dict(top=top, cols=[[None] * len(c) for _, _, c, _ in per_page])
                extras.append(e)
            for x, v in q["cells"]:
                ci = min(range(len(per_page[k][2])), key=lambda z: abs(per_page[k][2][z] - x))
                if abs(per_page[k][2][ci] - x) <= 24:
                    e["cols"][k][ci] = v
            used[k].add(qi)
    for e in extras:
        prev = [r for r in rows if r["top"] <= e["top"] + tol]
        sec = prev[-1]["section"] if prev else ""
        lab = {"Revenues": "Total revenues", "Expenditures": "Total expenditures", "Other Financing Sources (Uses)": "Total other financing sources (uses)",
               "Liabilities": "Total liabilities", "Assets": "Total assets"}.get(sec, "(unlabeled total)")
        if any(r["label"].lower() == lab.lower() for r in rows):
            lab = "(unlabeled total)"
        vals = []
        for cols in e["cols"]:
            vals += [0 if c is None else c for c in cols]
        row = dict(section=sec, sub="", label=lab, vals=vals, page=per_page[0][0], flag="label_added_unlabeled_in_source", top=e["top"])
        i = max([n for n, r in enumerate(rows) if r["top"] <= e["top"] + tol] + [-1]) + 1
        rows.insert(i, row)
    return rows, names, problems


def validate_combining(kind, rows, names):
    ncols = len(names)
    probs = [] if len(rows) else ["no rows"]
    if not rows:
        return rows, probs
    fs.ALLOW.clear()
    for k, v in OV["allow"].items():
        if k.startswith(kind + "|"):
            fs.ALLOW[k] = v
    synth = None
    if kind != "balance_sheet" and not any(re.match(r"(?i)excess", r["label"]) for r in rows):
        # FY2024-26 print no "excess" line: derive it (revenues - expenditures) so the remaining identities still run
        tr = next((r for r in rows if re.match(r"(?i)total revenues?", r["label"])), None)
        te = next((r for r in rows if re.match(r"(?i)total expenditures?", r["label"])), None)
        if tr and te:
            synth = dict(section="", sub="", label="EXCESS (derived)", vals=[a - b for a, b in zip(tr["vals"], te["vals"])], page=te["page"])
            rows = rows[:rows.index(te) + 1] + [synth] + rows[rows.index(te) + 1:]
    rows, pr = fs.validate(kind, rows, ncols)
    if synth is not None:
        rows = [r for r in rows if r is not synth]
    return rows, pr


# ---- driver -------------------------------------------------------------------------------------
def pick_sources(fy, source):
    if source != "auto":
        return [source]
    return ["text", "gray"] if int(fy) >= 2016 else ["gray", "text"]


def run(fy, source):
    load_overrides(fy)
    loc = locate(fy, source)
    texts = loc["texts"]
    problems, sched_out, comb_out, notes = [], [], [], []
    # schedules
    seen = {}
    for pg, fund, h in loc["found"]:
        seen.setdefault(fund, []).append(pg)
    for fund, pages in seen.items():
        doc = f"{fund}"
        rows, pr = parse_schedule(fy, pages, source, doc)
        problems += pr
        if rows:
            pr, nt = validate_schedule(doc, rows)
            problems += pr
            notes += [f"{doc}: {n}" for n in nt]
            sched_out.append((fund, rows))
    # combining
    names_all = {}
    for kind, first in (("balance_sheet", loc["bs"]), ("changes_in_fund_balance", loc["soc"])):
        if first is None:
            problems.append(f"{kind}: combining statement not found")
            continue
        pages = continuation_pages(texts, first, 3)
        doc = kind
        rows, names, pr = build_combining(fy, pages, source, kind, doc)
        problems += pr
        if rows:
            rows, pr = validate_combining(kind, rows, names)
            problems += [f"{kind}: {p}" for p in pr]
            comb_out.append((kind, rows, names, pages))
    return dict(source=source, loc=loc, sched=sched_out, comb=comb_out, problems=problems, notes=notes)


def write(fy, res):
    os.makedirs(OUT_DIR, exist_ok=True)
    p1 = os.path.join(OUT_DIR, f"FY{fy}_audit_fund_schedules.csv")
    with open(p1, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "fund", "section", "subsection", "line_item", "line_kind"] + COLS + ["flag"])
        for fund, rows in res["sched"]:
            for r in rows:
                vv = ["" if v is None else v for v in r["vals"]] + [""] * (5 - len(r["vals"]))
                w.writerow([fy, f"FY{fy}_audit", r["page"], fund, r["section"].title(), r["sub"], r["label"], r["kind"]] + vv + [r["flag"]])
    p2 = os.path.join(OUT_DIR, f"FY{fy}_audit_fund_combining.csv")
    with open(p2, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "statement", "section", "subsection", "line_item", "fund", "amount", "flag"])
        for kind, rows, names, pages in res["comb"]:
            for r in rows:
                for fund, v in zip(names, r["vals"]):
                    w.writerow([fy, f"FY{fy}_audit", r["page"], "combining_" + kind, r["section"], r["sub"], r["label"], fund, v, r.get("flag", "")])
    return p1, p2


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--source") + 1] if "--source" in sys.argv else "auto"
    results = []
    for s in pick_sources(fy, arg):
        try:
            r = run(fy, s)
        except SystemExit as e:
            print(f"source {s}: {e}")
            continue
        results.append(r)
        print(f"source {s}: {len(r['problems'])} problems; schedules {[f for f, _ in r['sched']]}; combining {[k for k, *_ in r['comb']]}")
    res = min(results, key=lambda r: (not r["sched"], len(r["problems"])))
    p1, p2 = write(fy, res)
    print(f"\nchosen source: {res['source']} -> {os.path.relpath(p1, ROOT)}, {os.path.relpath(p2, ROOT)}; {len(res['problems'])} problems")
    for n in res.get("notes", []):
        print("  note:", n)
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
