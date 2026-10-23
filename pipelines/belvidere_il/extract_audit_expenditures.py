"""Extract the General Fund "Schedule of Detailed Expenditures - Budget and Actual" from a Belvidere audit PDF.

Usage: python3 pipelines/belvidere_il/extract_audit_expenditures.py FY2020 [--prep auto|text|gray]

The schedule lists every General Fund expenditure line with Original Budget, Final Budget and audited Actual, grouped
function -> department -> category (Personnel services, Contractual services, Commodities, Capital outlay), with a printed
"Total ..." at every level and TOTAL EXPENDITURES at the end. (FY2012-FY2015 audits call it "Schedule of Expenditures" and
are scanned.) Output: data/belvidere_il/extracted/audits/FY20XX_audit_gf_expenditure.csv, one row per line item, with its
function/department/category.

Validation: every group's items plus its sub-group totals must equal the group's printed total, in all three columns, and the
functions must sum to TOTAL EXPENDITURES. Rows with blank cells (the audit leaves some cells empty rather than printing a
dash) are placed by trying column positions until the group total ties (flag `placed_by_sum`). Eyeballed corrections are in
data/belvidere_il/overrides/audit_expenditure_FY20XX.csv (kinds `row` keyed `path|label`, `total` keyed `path|column`,
`allow_diff` keyed `path|column`, `fixline` keyed by the OCR'd line, value the corrected line).

--prep auto tries the PDF text layer and 300 dpi grayscale OCR and keeps the one with fewer problems.
"""
import csv
import difflib
import itertools
import os
import re
import sys

import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from extract_audit import (COLS, DATA_E, ROOT, audit_pdf, clean, page_texts_for_locating,  # noqa: E402
                           split_numbers)
from extract_budget import digit_variants, ocr_page  # noqa: E402

TITLE = re.compile(r"SCHEDULE OF (DETAILED )?EXPENDITURES", re.I)
CATEGORY = re.compile(r"(?i)^(personnel services|contractual services|commodities|capital outlay|debt service)$")
# Group names the schedule uses (functions, departments, categories). A line with no numbers that is NOT one of these is a line item
# whose three cells are all blank, e.g. "Unemployment Compensation", and counts as zeros; a new, unseen group name would then fail
# the printed-total check and show up as a problem.
KNOWN_GROUPS = {
    "general government", "public safety", "highways and streets", "health and social services", "community and economic development",
    "general", "audit", "social security", "police department", "public safety building", "fire department", "police and fire commissions",
    "police and fire commission", "building department", "civil defense", "street department", "street lighting", "garbage", "forestry",
    "city engineering", "health regulations and inspections", "economic development", "public works", "capital projects",
}
FURNITURE = re.compile(r"(?i)^((19|20)\d\d(\s+(19|20)\d\d)*$|variance|positive|city of belvidere|schedule of|general fund$|for the year ended|year ended|"
                       r"original\b|budget\b|\(?this schedule is continued|\(?see independent|- ?\d+ ?-?$|\d+$|with comparative|for the fiscal year|for the year|"
                       r"final\b|actual\b|\(negative\))")


FIXED_PAGES = {2012: (96, 100)}  # scanned audit whose heading OCR is unreliable: pages found by eye (heading page is 96 = "GENERAL GOVERNMENT")


def locate(fy):
    """(first, last) 1-based pdf pages of the detailed General Fund expenditure schedule."""
    if int(fy) in FIXED_PAGES:
        return FIXED_PAGES[int(fy)]
    texts = page_texts_for_locating(fy)
    start = None
    for i, t in enumerate(texts, 1):
        head = t[:700]
        if TITLE.search(head) and "GENERAL FUND" in head.upper() and "CONTINUED" not in head.upper() \
                and "TABLE OF CONTENTS" not in t.upper() and "PAGE NO" not in t.upper() and len(re.findall(r"\d{1,3},\d{3}", t)) > 15:
            start = i
            break
    if start is None:
        raise SystemExit(f"FY{fy}: expenditure schedule start page not found")
    for j in range(start, min(start + 9, len(texts)) + 1):
        if re.search(r"TOTAL\s+EXPENDITURES", texts[j - 1], re.I):
            return start, j
    raise SystemExit(f"FY{fy}: TOTAL EXPENDITURES page not found after page {start}")


def get_pages(fy, prep, first, last):
    if prep == "text":
        with pdfplumber.open(audit_pdf(fy)) as d:
            return [(p, d.pages[p - 1].extract_text() or "") for p in range(first, last + 1)]
    outdir = os.path.join(DATA_E, "ocr", "audit_pages", f"FY{fy}_{prep}")
    os.makedirs(outdir, exist_ok=True)
    return [(p, ocr_page(audit_pdf(fy), p, outdir, prep)) for p in range(first, last + 1)]


def norm(name):
    """Group names compare case-insensitively without 'continued', punctuation, plurals' differences."""
    n = re.sub(r"(?i)\(?continued\)?", "", name).replace("&", " and ")
    n = re.sub(r"[^a-z ]", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip().rstrip("s")


def tidy(line):
    """Module-level cleanups on top of extract_audit.clean: a digit split off by OCR ("704,63 1"), a lone punctuation mark
    standing for a blank/zero cell (". :"), and runs of spaces (so overrides keyed on a line match)."""
    line = re.sub(r"(\d,\d{2}) (\d)(?= |$)", r"\1\2", line)
    line = re.sub(r"(?<=[\d-]) [:.·;~](?= |$)", " -", line)  # a lone mark after a number or dash stands for a blank cell
    line = re.sub(r"(?i)\s*\bOriginal Final\b|\s*\bBudget Budget Actual( \(Negative\))?( Actual)?\s*$|\s*\bPositive\s*$", "", line)  # FY2012 column headers glued to a heading
    return re.sub(r"\s+", " ", line).strip()


def prefix_of(label):
    """The words of a line before its first number: the key two readings of the same line share."""
    words = []
    for w in label.split():
        if any(ch.isdigit() for ch in w):
            break
        words.append(w.lower())
    return " ".join(words)


def rescue_lines(pages, other):
    """OCR garbles a number now and then, which turns a line into a bogus heading ("Leaf Cleanup 14,880 551 55]").
    When the other reading (text layer vs OCR) of the schedule has exactly one parseable line with the same words, use it.
    Returns {cleaned line: replacement line}; the validation against the printed totals still decides whether it helps."""
    good = {}
    for _pg, text in other:
        for raw in text.splitlines():
            line = tidy(clean(raw))
            label, vals = split_numbers(line)
            if len(vals) == 3 and label:
                good.setdefault(label.lower(), []).append(line)
    out = {}
    for _pg, text in pages:
        for raw in text.splitlines():
            line = tidy(clean(raw))
            label, vals = split_numbers(line)
            if len(vals) < 3 and sum(any(ch.isdigit() for ch in w) for w in label.split()) >= 2:
                pre = prefix_of(label)
                cands = [ln for k, lns in good.items() if pre and k.startswith(pre) and not any(ch.isdigit() for ch in k[:len(pre)]) for ln in lns]
                if len(cands) == 1:
                    out[line] = cands[0]
    return out


def is_group_name(name):
    return CATEGORY.match(name) is not None or norm(name) in {norm(k) for k in KNOWN_GROUPS}


def parse(pages, fixline=None):
    """Returns (rows, groups, grand, problems) where groups carry their printed totals for validation."""
    stack, closed, items, grand, problems = [], [], [], None, []
    pending = None  # a "Total ..." label wrapped onto the next line, whose numbers follow with the rest of the name
    for pg, text in pages:
        lines = text.splitlines()
        # Everything above the column header ("Original Final Actual") is page furniture; OCR turns its ruled lines into junk text.
        hdr = next((i for i, ln in enumerate(lines) if re.search(r"(?i)\bactual\b", ln) and not re.search(r"\d", ln) and len(ln) < 45), None)
        for raw in lines[hdr + 1:] if hdr is not None else lines:
            line = clean(raw)
            line = tidy(line)
            if fixline and line in fixline:  # an OCR misread of a whole line, corrected from the page image / the other reading
                line = fixline[line]
            if not line or FURNITURE.match(line) or sum(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ,.()/&'-:" for ch in line) > 3:
                continue
            label, vals = split_numbers(line)
            if len(vals) >= 5:  # FY2012 layout: original, final, actual, variance, prior-year actual (a split number makes it 6)
                vals = vals[:3]
            if pending and vals and not re.match(r"(?i)^total\b", label):
                label, pending = f"{pending} {label}".strip(), None
            elif pending:
                pending = None
            if not vals and re.match(r"(?i)^tota[l!1|]\s+\w+", label) and not re.match(r"(?i)^total\s+expenditures?$", label):
                pending = label  # wrapped total label: the numbers are on the next line
                continue
            if grand is not None:
                continue  # nothing after TOTAL EXPENDITURES but page furniture
            if not vals:  # heading: opens a group unless it re-opens the current one (page continuation)
                if not label or len(label) < 3 or sum(ch.isalpha() for ch in label) < 3:
                    continue
                name = re.sub(r"(?i)\s*\(?continued\)?", "", label).strip(" -:")
                if not re.search(r"(?i)continued", label) and not is_group_name(name) and stack:
                    row = dict(path=[g["name"] for g in stack], label=label, vals=[0, 0, 0], page=pg, flag="blank_cells")
                    stack[-1]["items"].append(row)
                    items.append(row)
                    continue
                # page headers repeat the open path ("PUBLIC SAFETY (Continued)"): same name at the same depth is a continuation
                if re.search(r"(?i)continued", label):
                    k = next((i for i, g in enumerate(stack) if norm(g["name"]) == norm(name)), None)
                    if k is not None:
                        continue
                # old audits (FY2013-FY2015) print some departments with no total: a new heading closes a department that
                # has items and no total, and its lines roll up into the function total, which still checks them
                while len(stack) >= 2 and stack[-1]["items"] and stack[-1]["total"] is None and not stack[-1]["children"]:
                    dead = stack.pop()
                    stack[-1]["items"].extend(dead["items"])
                stack.append(dict(name=name, items=[], children=[], depth=len(stack), total=None))
                continue
            if re.match(r"(?i)^total\s+expenditures?$", label):
                grand = vals
                continue
            m = re.match(r"(?i)^tota[l!1|]\s+(.*)$", label)
            if m or (not label and len(vals) == 3):
                want = norm(m.group(1)) if m else None
                if want is None:  # FY2013-FY2015 audits print a department's total as an unlabeled line of numbers
                    k = len(stack) - 1 if stack else None
                else:
                    k = next((i for i in range(len(stack) - 1, -1, -1) if norm(stack[i]["name"]) == want), None)
                if k is None and want:  # a label cut short at the page edge, or a heading cut short ("COMMUNITY & ECONOMIC" / "DEVELOPMENT")
                    k = next((i for i in range(len(stack) - 1, -1, -1)
                              if len(want) >= 8 and (norm(stack[i]["name"]).startswith(want) or want.startswith(norm(stack[i]["name"])))), None)
                if k is None and want:  # the audit misspells some labels ("inspecitons"), and OCR garbles others
                    k = next((i for i in range(len(stack) - 1, -1, -1)
                              if difflib.SequenceMatcher(None, norm(stack[i]["name"]), want).ratio() >= 0.85), None)
                if k is None:
                    problems.append(f"p{pg}: total line {label!r} {vals} matches no open group ({[g['name'] for g in stack]})")
                    continue
                g = stack[k]
                g["total"] = vals
                g["page"] = pg
                # anything still open above it never printed a total: it is folded into this group's children unchecked
                for dead in stack[k + 1:]:  # a department without a printed total rolls up into this group
                    g["items"].extend(dead["items"])
                    g["children"].extend(dead["children"])
                del stack[k:]
                g["path"] = [x["name"] for x in stack] + [g["name"]]
                closed.append(g)
                if stack:
                    stack[-1]["children"].append(g)
                continue
            if not stack:
                problems.append(f"p{pg}: line {label!r} outside any group")
                continue
            row = dict(path=[g["name"] for g in stack], label=label, vals=vals, page=pg)
            stack[-1]["items"].append(row)
            items.append(row)
    # A function whose only department prints the one total (FY2012 "COMMUNITY & ECONOMIC DEVELOPMENT") has no total of its own:
    # it is closed here and taken as the sum of what it holds, with nothing to check it against.
    while stack:
        g = stack.pop()
        g["total"], g["path"] = None, [x["name"] for x in stack] + [g["name"]]
        if not (g["items"] or g["children"]):
            problems.append(f"group {g['name']!r} is empty and was never closed by a total")
            continue
        closed.append(g)
        if stack:
            stack[-1]["children"].append(g)
    return items, closed, grand, problems


def place_partial(rows, total, child_sums):
    partial = [i for i, r in enumerate(rows) if len(r["vals"]) < 3]
    full = [i for i in range(len(rows)) if i not in partial]
    base = [sum(rows[i]["vals"][c] for i in full) + child_sums[c] for c in range(3)]
    options = [[tuple(c) for c in itertools.combinations(range(3), len(rows[i]["vals"]))] for i in partial]
    for combo in itertools.product(*options):
        sums = list(base)
        for i, cols in zip(partial, combo):
            for val, c in zip(rows[i]["vals"], cols):
                sums[c] += val
        if sums == list(total):
            return {i: cols for i, cols in zip(partial, combo)}
    return None


def validate(items, closed, grand, overrides):
    problems, notes = [], []
    for r in items:  # eyeballed row overrides
        key = "|".join(r["path"]) + "|" + r["label"]
        if key in overrides["row"]:
            r["vals"] = overrides["row"][key]
            r["flag"] = "values_overridden"
    for g in closed:
        pkey = "|".join(g["path"])
        if g["total"] is None:  # no printed total (see parse): the sum of its lines and sub-groups stands in
            g["total_used"] = [sum(r["vals"][c] for r in g["items"] if len(r["vals"]) == 3) + sum(ch.get("total_used", [0, 0, 0])[c] for ch in g["children"]) for c in range(3)]
            notes.append(f"{pkey}: no printed total; taken as the sum of its lines")
            continue
        total = list(g["total"])
        for c, name in enumerate(COLS):
            ov = overrides["total"].get(f"{pkey}|{name}")
            if ov is not None and len(total) == 3:
                total[c] = ov
        if len(total) != 3:
            problems.append(f"{pkey}: total has {len(total)} values: {total}")
            continue
        child = [sum(ch["total_used"][c] for ch in g["children"] if "total_used" in ch) for c in range(3)]
        rows = g["items"]
        if any(len(r["vals"]) < 3 for r in rows):
            plc = place_partial(rows, total, child)
            if plc is None:
                problems.append(f"{pkey}: rows with blank cells could not be placed: " + "; ".join(f"{r['label']} {r['vals']}" for r in rows if len(r["vals"]) < 3))
                continue
            for i, cols in plc.items():
                new = [0, 0, 0]
                for val, c in zip(rows[i]["vals"], cols):
                    new[c] = val
                rows[i]["vals"], rows[i]["flag"] = new, "placed_by_sum"
        if any(len(r["vals"]) != 3 for r in rows):
            problems.append(f"{pkey}: rows without 3 values: " + "; ".join(f"{r['label']} {r['vals']}" for r in rows if len(r["vals"]) != 3))
            continue
        g["total_used"] = total
        for c, name in enumerate(COLS):
            got = sum(r["vals"][c] for r in rows) + child[c]
            diff = total[c] - got
            if abs(diff) >= 10 and not overrides["allow"].get(f"{pkey}|{name}") == diff:
                # a single-digit OCR confusion in exactly one of the group's own lines explains the difference
                cands = [(r, v) for r in rows if r["vals"][c] > 0 for v in digit_variants(r["vals"][c]) if v - r["vals"][c] == diff]
                if len(cands) == 1:
                    r, v = cands[0]
                    notes.append(f"auto-resolved {pkey} | {r['label']} {name}: {r['vals'][c]:,} -> {v:,} (a single-digit OCR confusion; makes the printed total tie)")
                    r["vals"] = [v if i == c else x for i, x in enumerate(r["vals"])]
                    r["flag"] = "auto_resolved_digit"
                    diff = 0
            if diff and overrides["allow"].get(f"{pkey}|{name}") == diff:
                notes.append(f"{pkey} {name}: printed total is {diff:+,} vs its own lines (source arithmetic; allowed by override)")
            elif diff:
                problems.append(f"{pkey} {name}: lines {got:,} vs printed {total[c]:,} (diff {diff:,})")
    tops = [g for g in closed if len(g["path"]) == 1]
    if grand is None or len(grand) != 3:
        problems.append(f"TOTAL EXPENDITURES not read: {grand}")
    else:
        g3 = list(grand)
        for c, name in enumerate(COLS):
            ov = overrides["total"].get(f"TOTAL EXPENDITURES|{name}")
            if ov is not None:
                g3[c] = ov
            got = sum(t["total_used"][c] for t in tops if "total_used" in t)
            diff = g3[c] - got
            if diff and overrides["allow"].get(f"TOTAL EXPENDITURES|{name}") == diff:
                notes.append(f"TOTAL EXPENDITURES {name}: printed total is {diff:+,} vs its functions (allowed by override)")
            elif diff:
                problems.append(f"TOTAL EXPENDITURES {name}: functions {got:,} vs printed {g3[c]:,} (diff {diff:,})")
    return problems, notes


def load_overrides(fy):
    ov = dict(row={}, total={}, allow={}, fixline={})
    path = os.path.join(DATA_E, "overrides", f"audit_expenditure_FY{fy}.csv")
    if os.path.exists(path):
        for o in csv.DictReader(open(path)):
            if o["kind"] == "row":
                ov["row"][o["key"]] = [int(x) for x in o["value"].split("|")]
            elif o["kind"] == "allow_diff":
                ov["allow"][o["key"]] = int(o["value"])
            elif o["kind"] == "total":
                ov["total"][o["key"]] = int(o["value"])
            elif o["kind"] == "fixline":  # key = the line as the OCR read it (after cleaning), value = the corrected line
                ov["fixline"][o["key"]] = o["value"]
    return ov


def run(fy, prep):
    first, last = locate(fy)
    ov = load_overrides(fy)
    pages = get_pages(fy, prep, first, last)
    other = get_pages(fy, "gray" if prep == "text" else "text", first, last)
    auto = rescue_lines(pages, other)
    items, closed, grand, problems = parse(pages, {**auto, **ov["fixline"]})
    vp, notes = validate(items, closed, grand, ov)
    notes += [f"line {k!r} read as {v!r} from the other reading" for k, v in auto.items()]
    return dict(prep=prep, first=first, last=last, items=items, closed=closed, grand=grand, problems=problems + vp, notes=notes)


def main():
    fy = sys.argv[1].replace("FY", "")
    arg = sys.argv[sys.argv.index("--prep") + 1] if "--prep" in sys.argv else "auto"
    results = []
    for p in (["text", "gray"] if arg == "auto" else [arg]):
        r = run(fy, p)
        results.append(r)
        print(f"prep {p}: {len(r['problems'])} problems, {len(r['items'])} lines (pdf pages {r['first']}-{r['last']})")
    res = min(results, key=lambda r: (not r["items"], len(r["problems"])))
    out = os.path.join(DATA_E, "extracted", "audits", f"FY{fy}_audit_gf_expenditure.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fiscal_year", "doc", "pdf_page", "function", "department", "category", "line_item", "original_budget", "final_budget", "actual", "flag"])
        for r in res["items"]:
            path = r["path"]
            func = path[0]
            cat = next((x for x in path[1:] if CATEGORY.match(x)), "")
            dept = " / ".join(x for x in path[1:] if not CATEGORY.match(x))
            w.writerow([fy, f"FY{fy}_audit", r["page"], func, dept, cat, r["label"], *r["vals"], r.get("flag", "")])
    print(f"\nchosen prep: {res['prep']}; {len(res['items'])} lines -> {os.path.relpath(out, ROOT)}; {len(res['problems'])} problems")
    for n in res["notes"]:
        print("  note:", n)
    for p in res["problems"]:
        print("  !", p)
    sys.exit(1 if res["problems"] else 0)


if __name__ == "__main__":
    main()
